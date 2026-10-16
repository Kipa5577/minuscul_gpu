import py4hw

from ..memory.memory import MemoryInterface
from ..output_stage.export import ExportInterface, drive_export
from ..params import GROUP_WIDTH, GROUP_SLOTS, IMEM_WORDS, WORD_WIDTH
from .isa import NUM_SGPRS, VMEM, EXP, decode
from .venus_ref import (GroupState, VenusError, execute, export_packet, format_of,
                        vmem_complete, vmem_requests)

'''
VENUS v2 shader core (work module M5).

  launch port  -> GROUP_SLOTS group slots (registers, PC, SCC, side data)
  scheduler    -> each cycle, the next *ready* slot after the last one that
                  issued (round-robin) issues one instruction
  scalar + vector execution: one cycle, through the same per-instruction
                  functions as the reference model (venus_ref.execute)
  VMEM unit    -> one memory port (32-bit words); a vector load/store
                  performs one access per active lane, in lane order, each
                  a 3-cycle issue/wait/release round trip on a plain
                  Memory. The slot is not ready until all accesses finish.
                  Coalescing into 512-bit lines arrives with M7.
  export unit  -> V_EXPORT_COLOR sends (seq, tile, mask = exec, colours, z)
                  on the export port; the slot waits until it is accepted.
                  A *pixel* group that ends without exporting sends an
                  empty packet (mask 0), so the output stage's in-order
                  sequence never has a gap.

Only one VMEM and one export can be in progress at a time; a slot whose
next instruction needs a busy unit is skipped by the scheduler that cycle.

The instruction memory is loaded by the host before launch
(`load_program`), as README_ISA.md §1 describes.
'''

LAUNCH_VGPRS = 5     # v0-v4 can be preloaded (README_ISA.md §6.3)


class LaunchInterface(py4hw.Interface):
    '''Dispatcher -> core: one thread group to start.'''

    def __init__(self, parent, name):
        super().__init__(parent, name)
        self.valid = self.addSourceToSink('valid', 1)
        self.ready = self.addSinkToSource('ready', 1)
        self.pixel = self.addSourceToSink('pixel', 1)       # 1 = pixel group (must export)
        self.pc = self.addSourceToSink('pc', 16)
        self.seq = self.addSourceToSink('seq', 32)
        self.sgpr = [self.addSourceToSink(f's{i}', WORD_WIDTH) for i in range(NUM_SGPRS)]
        self.vgpr = [[self.addSourceToSink(f'v{r}_{lane}', WORD_WIDTH) for lane in range(GROUP_WIDTH)]
                     for r in range(LAUNCH_VGPRS)]
        # side data carried with the group and returned with its export
        self.tile_x = self.addSourceToSink('tile_x', WORD_WIDTH)
        self.tile_y = self.addSourceToSink('tile_y', WORD_WIDTH)
        self.z = [self.addSourceToSink(f'z{lane}', WORD_WIDTH) for lane in range(GROUP_WIDTH)]


def read_launch(launch: LaunchInterface):
    return {
        'pixel': launch.pixel.get(),
        'pc': launch.pc.get(),
        'seq': launch.seq.get(),
        'sgprs': {i: w.get() for i, w in enumerate(launch.sgpr)},
        'vgprs': {r: [w.get() for w in lanes] for r, lanes in enumerate(launch.vgpr)},
        'tile_x': py4hw.IntegerHelper.c2_to_signed(launch.tile_x.get(), WORD_WIDTH),
        'tile_y': py4hw.IntegerHelper.c2_to_signed(launch.tile_y.get(), WORD_WIDTH),
        'z': [py4hw.IntegerHelper.c2_to_signed(w.get(), WORD_WIDTH) for w in launch.z],
    }


def drive_launch(launch: LaunchInterface, group):
    '''prepare() a group (dict like read_launch's, missing keys = 0); the
    caller drives `valid`.'''
    m = (1 << WORD_WIDTH) - 1
    launch.pixel.prepare(group.get('pixel', 0))
    launch.pc.prepare(group.get('pc', 0))
    launch.seq.prepare(group.get('seq', 0))
    sgprs = group.get('sgprs', {})
    for i, w in enumerate(launch.sgpr):
        w.prepare(sgprs.get(i, 0) & m)
    vgprs = group.get('vgprs', {})
    for r, lanes in enumerate(launch.vgpr):
        values = vgprs.get(r, [0] * GROUP_WIDTH)
        for w, value in zip(lanes, values):
            w.prepare(value & m)
    launch.tile_x.prepare(group.get('tile_x', 0) & m)
    launch.tile_y.prepare(group.get('tile_y', 0) & m)
    for w, value in zip(launch.z, group.get('z', [0] * GROUP_WIDTH)):
        w.prepare(value & m)


class Slot:
    FREE, READY, WAIT_MEM, WAIT_EXP = range(4)

    def __init__(self):
        self.status = Slot.FREE
        self.state = None
        self.side = None
        self.ending = False       # waiting for the implicit empty export of S_ENDPGM


class ShaderCore(py4hw.Logic):

    def __init__(self, parent, name, launch: LaunchInterface, mem: MemoryInterface,
                 exp: ExportInterface, idle, slots: int = GROUP_SLOTS, imem_words: int = IMEM_WORDS):
        super().__init__(parent, name)
        self.launch = self.addInterfaceSink('launch', launch)
        self.mem = self.addInterfaceSource('mem', mem)
        self.exp = self.addInterfaceSource('exp', exp)
        self.idle = self.addOut('idle', idle)

        self.imem = [0] * imem_words
        self.slots = [Slot() for _ in range(slots)]
        self.last_issued = slots - 1

        # VMEM unit
        self.mem_slot = None
        self.mem_queue = []
        self.mem_loaded = {}
        self.mem_issue_phase = True
        self.mem_op = None
        # export unit
        self.exp_slot = None

        # statistics
        self.cycles = 0
        self.instructions = 0
        self.issue_trace = []      # (cycle, slot) of every issued instruction
        self.groups_finished = 0

        launch.ready.put(1)
        exp.valid.put(0)
        idle.put(1)

    def load_program(self, words):
        assert len(words) <= len(self.imem), 'program larger than instruction memory'
        self.imem[:len(words)] = list(words)

    # --- per-cycle behaviour -----------------------------------------------------

    def clock(self):
        self.cycles += 1
        self._exp_valid_next = None   # drive exp.valid at most once per cycle
        self._accept_launch()
        self._step_memory()
        self._step_export()
        self._issue()
        if self._exp_valid_next is not None:
            self.exp.valid.prepare(self._exp_valid_next)

        free = sum(1 for s in self.slots if s.status == Slot.FREE)
        self.launch.ready.prepare(1 if free > 0 else 0)
        busy = free < len(self.slots) or self.launch.valid.get() == 1
        self.idle.prepare(0 if busy else 1)

    def _accept_launch(self):
        ln = self.launch
        if ln.valid.get() == 1 and ln.ready.get() == 1:
            group = read_launch(ln)
            slot = next(s for s in self.slots if s.status == Slot.FREE)
            slot.state = GroupState(group['pc'], group['sgprs'], group['vgprs'])
            slot.side = group
            slot.status = Slot.READY
            slot.ending = False

    def _step_memory(self):
        if self.mem_slot is None:
            return
        m = self.mem
        if self.mem_issue_phase:
            lane, addr, value = self.mem_queue[0]
            m.address.prepare(addr)
            if value is None:
                m.read.prepare(1)
                m.write.prepare(0)
            else:
                m.write_data.prepare(value)
                m.read.prepare(0)
                m.write.prepare(1)
            self.mem_issue_phase = False
        elif m.done.get() == 1:
            lane, addr, value = self.mem_queue.pop(0)
            if value is None:
                self.mem_loaded[lane] = m.read_data.get()
            m.read.prepare(0)
            m.write.prepare(0)
            self.mem_issue_phase = True
            if not self.mem_queue:
                slot = self.mem_slot
                mnemonic, f = self.mem_op
                vmem_complete(slot.state, mnemonic, f, self.mem_loaded)
                slot.status = Slot.READY
                self.mem_slot = None

    def _step_export(self):
        if self.exp_slot is None:
            return
        e = self.exp
        if e.valid.get() == 1 and e.ready.get() == 1:
            self._exp_valid_next = 0
            slot = self.exp_slot
            self.exp_slot = None
            if slot.ending:
                self._finish(slot)
            else:
                slot.state.pc += 1
                slot.status = Slot.READY

    def _send_export(self, slot, mask, colours):
        side = slot.side
        drive_export(self.exp, {'seq': side['seq'], 'tile_x': side['tile_x'], 'tile_y': side['tile_y'],
                                'mask': mask, 'colour': colours, 'z': side['z']})
        self._exp_valid_next = 1
        self.exp_slot = slot
        slot.status = Slot.WAIT_EXP

    def _finish(self, slot):
        slot.status = Slot.FREE
        slot.state = None
        self.groups_finished += 1

    def _issue(self):
        n = len(self.slots)
        for k in range(1, n + 1):
            index = (self.last_issued + k) % n
            slot = self.slots[index]
            if slot.status != Slot.READY:
                continue
            st = slot.state
            if not 0 <= st.pc < len(self.imem):
                raise VenusError(f'slot {index}: PC {st.pc} outside instruction memory')
            try:
                mnemonic, f = decode(self.imem[st.pc])
            except Exception as e:
                raise VenusError(f'slot {index}, PC {st.pc}: {e}') from None
            fmt = format_of(mnemonic)
            if fmt == VMEM and self.mem_slot is not None:
                continue            # memory unit busy: try another slot
            needs_export = fmt == EXP or (mnemonic == 'S_ENDPGM' and slot.side['pixel']
                                          and not st.exported)
            if needs_export and self.exp_slot is not None:
                continue            # export unit busy
            self._execute(slot, mnemonic, f, fmt)
            self.last_issued = index
            self.instructions += 1
            self.issue_trace.append((self.cycles, index))
            return

    def _execute(self, slot, mnemonic, f, fmt):
        st = slot.state
        if fmt == VMEM:
            requests = vmem_requests(st, mnemonic, f)
            if not requests:
                vmem_complete(st, mnemonic, f, {})
                return
            self.mem_slot = slot
            self.mem_queue = requests
            self.mem_loaded = {}
            self.mem_issue_phase = True
            self.mem_op = (mnemonic, f)
            slot.status = Slot.WAIT_MEM
        elif fmt == EXP:
            if not slot.side['pixel']:
                raise VenusError('V_EXPORT_COLOR in a non-pixel group')
            mask, colours = export_packet(st, f)
            self._send_export(slot, mask, colours)
        elif execute(st, mnemonic, f, len(self.imem)):          # S_ENDPGM
            if slot.side['pixel'] and not st.exported:
                slot.ending = True
                self._send_export(slot, 0, [0] * GROUP_WIDTH)
            else:
                self._finish(slot)

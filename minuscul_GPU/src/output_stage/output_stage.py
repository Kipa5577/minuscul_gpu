import py4hw

from ..params import GROUP_WIDTH, GROUP_SLOTS, WORD_WIDTH, lane_xy
from .export import ExportInterface, read_export

'''
Output stage (work module M2): the last block of a slice, the equivalent of
NVIDIA's ROPs / AMD's render backends.

  - accepts export packets into a small reorder buffer (`reorder_depth`
    entries, default GROUP_SLOTS), whatever order they arrive in
  - processes them strictly in `seq` order (D8: exports in launch order),
    so draw order is preserved even when groups finish out of order
  - per packet: depth test of the 16 lanes in parallel ("z < stored z",
    smaller is closer, first writer wins ties), then writes the passing
    lanes' colours into this slice's framebuffer strip

The framebuffer write path is the one measured in
RASTERISER_ARCHITECTURE.md #7-#8, moved here from RenderController:
pipelined back-to-back issue on a single port, or one write per bank per
cycle when the strip is split into `write_bank_count` interleaved banks.

The depth buffer is private state, as it was in RenderController: nothing
outside the output stage reads it.
'''


def framebuffer_bank_size(pixel_count: int, write_bank_count: int) -> int:
    '''Size of each bank's Memory when a `pixel_count`-pixel strip is split
    across `write_bank_count` interleaved banks (bank = addr % banks,
    bank address = addr // banks).'''
    return -(-pixel_count // write_bank_count)  # ceil division


def resolve_banked_framebuffer(bank_memories, write_bank_count: int, pixel_count: int):
    '''Inverse of the bank interleaving: stitches the bank Memory objects
    back into one flat, pixel_count-long list of framebuffer values.'''
    combined = [0] * pixel_count
    for addr in range(pixel_count):
        combined[addr] = bank_memories[addr % write_bank_count].memory[addr // write_bank_count]
    return combined


class OutputStage(py4hw.Logic):
    '''
    States of the processing FSM (packet acceptance runs every cycle,
    independently):
      TEST          - if the next packet in seq order is buffered: depth-test
                      its 16 lanes and queue the passing writes
      WRITE_ISSUE   - issue queued writes, one per bank per cycle, back to back
      WRITE_WAIT    - wait for the last batch's `done`, then next packet

    `idle` (output) is 1 when no packet is buffered or being processed.
    '''

    S_TEST, S_WRITE_ISSUE, S_WRITE_WAIT = range(3)

    def __init__(self, parent, name, exp: ExportInterface, framebuffer_mem, idle,
                 screen_width: int, strip_y0: int, strip_rows: int,
                 write_bank_count: int = 1, reorder_depth: int = GROUP_SLOTS):
        super().__init__(parent, name)

        self.exp = self.addInterfaceSink('exp', exp)
        self.idle = self.addOut('idle', idle)

        self.write_bank_count = write_bank_count
        fb_ifs = [framebuffer_mem] if write_bank_count == 1 else list(framebuffer_mem)
        assert len(fb_ifs) == write_bank_count, \
            'framebuffer_mem must supply exactly write_bank_count interfaces'
        self.fb = [self.addInterfaceSource(f'fb{i}', fb_ifs[i]) for i in range(write_bank_count)]

        self.screen_width = screen_width
        self.strip_y0 = strip_y0
        self.strip_rows = strip_rows
        self.reorder_depth = reorder_depth

        self._zbuffer = [None] * (screen_width * strip_rows)
        self.buffer = {}            # seq -> packet
        self.next_seq = 0
        self.state = self.S_TEST
        self.pending_writes = []    # (local pixel address, colour)
        self._writes_in_flight = []

        # statistics
        self.packets_done = 0
        self.pixels_written = 0
        self.pixels_depth_failed = 0

        self.exp.ready.put(1)
        self.idle.put(1)

    def clock(self):
        e = self.exp

        # --- acceptance: one packet per cycle into the reorder buffer ------
        if e.valid.get() == 1 and e.ready.get() == 1:
            packet = read_export(e)
            assert packet['seq'] not in self.buffer and packet['seq'] >= self.next_seq, \
                f'duplicate or stale export seq {packet["seq"]}'
            self.buffer[packet['seq']] = packet

        # --- processing -----------------------------------------------------
        if self.state == self.S_TEST:
            packet = self.buffer.pop(self.next_seq, None)
            if packet is not None:
                for lane in range(GROUP_WIDTH):
                    if not (packet['mask'] >> lane & 1):
                        continue
                    dx, dy = lane_xy(lane)
                    y = packet['tile_y'] + dy - self.strip_y0
                    x = packet['tile_x'] + dx
                    assert 0 <= y < self.strip_rows and 0 <= x < self.screen_width, \
                        f'pixel ({x}, {y + self.strip_y0}) outside this strip'
                    addr = y * self.screen_width + x
                    z = packet['z'][lane]
                    if self._zbuffer[addr] is None or z < self._zbuffer[addr]:
                        self._zbuffer[addr] = z
                        self.pending_writes.append((addr, packet['colour'][lane]))
                    else:
                        self.pixels_depth_failed += 1
                if self.pending_writes:
                    self.state = self.S_WRITE_ISSUE
                else:
                    self._packet_finished()

        elif self.state == self.S_WRITE_ISSUE:
            # at most one pixel per bank per cycle; later pixels of a
            # claimed bank wait for the next cycle
            batch, claimed, remaining = [], set(), []
            for addr, colour in self.pending_writes:
                bank = addr % self.write_bank_count
                if bank in claimed:
                    remaining.append((addr, colour))
                else:
                    claimed.add(bank)
                    batch.append((bank, addr // self.write_bank_count, colour))
            self.pending_writes = remaining

            for bank, bank_addr, colour in batch:
                fb = self.fb[bank]
                fb.address.prepare(bank_addr)
                fb.read.prepare(0)
                fb.write.prepare(1)
                fb.write_data.prepare(colour)
            self._writes_in_flight = [bank for bank, _, _ in batch]
            self.pixels_written += len(batch)
            # pipelined: next batch next cycle; drain only after the last
            self.state = self.S_WRITE_ISSUE if self.pending_writes else self.S_WRITE_WAIT

        elif self.state == self.S_WRITE_WAIT:
            if all(self.fb[bank].done.get() == 1 for bank in self._writes_in_flight):
                for bank in self._writes_in_flight:
                    self.fb[bank].write.prepare(0)
                self._packet_finished()

        # room is checked after this cycle's acceptance, so a full buffer
        # deasserts ready before another packet can arrive
        e.ready.prepare(1 if len(self.buffer) < self.reorder_depth else 0)
        busy = self.state != self.S_TEST or bool(self.buffer) or e.valid.get() == 1
        self.idle.prepare(0 if busy else 1)

    def _packet_finished(self):
        self.next_seq += 1
        self.packets_done += 1
        self.state = self.S_TEST

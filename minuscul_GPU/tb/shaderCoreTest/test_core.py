'''
M5 testbench, part 3: the py4hw ShaderCore against the reference model.

Random programs (ALU, compares into masks, IF/ELSE/ENDIF, loops, vector
loads/stores) run as several groups at once, with 1 and 4 slots. The
core's final memory and exports must equal running each group on the
reference interpreter. Directed tests check scheduling, latency hiding,
export ordering/side data and backpressure.
'''
import random

import py4hw
import py4hw.simulation
import pytest

from ...src.memory.memory import MemoryInterface, Memory
from ...src.output_stage.export import ExportInterface, read_export
from ...src.params import GROUP_WIDTH, LANE_MASK
from ...src.shader_core.core import LaunchInterface, ShaderCore, drive_launch
from ...src.shader_core.isa import INSTRUCTIONS, SOP, SOPK, VOP, VOPS
from ...src.shader_core.venus_ref import GroupState, VenusError, run as ref_run
from ...tools.venus_asm import assemble

MEM_WORDS = 8192


# --- test-side components ------------------------------------------------------------

class LaunchSource(py4hw.Logic):
    '''Drives `groups` (dicts for drive_launch) with the valid/ready handshake.'''

    def __init__(self, parent, name, launch, groups):
        super().__init__(parent, name)
        self.ln = self.addInterfaceSource('launch', launch)
        self.groups = list(groups)
        self.index = 0
        self.stall_cycles = 0
        launch.valid.put(0)

    @property
    def finished(self):
        return self.index >= len(self.groups) and self.ln.valid.get() == 0

    def clock(self):
        ln = self.ln
        if ln.valid.get() == 1:
            if ln.ready.get() == 1:
                self.index += 1
            else:
                self.stall_cycles += 1
                return
        if self.index < len(self.groups):
            drive_launch(ln, self.groups[self.index])
            ln.valid.prepare(1)
        else:
            ln.valid.prepare(0)


class ExportSink(py4hw.Logic):
    def __init__(self, parent, name, exp, ready_pattern=None):
        super().__init__(parent, name)
        self.exp = self.addInterfaceSink('exp', exp)
        self.ready_pattern = ready_pattern or (lambda cycle: 1)
        self.packets = []
        self.cycle = 0
        exp.ready.put(self.ready_pattern(0))

    def clock(self):
        e = self.exp
        if e.valid.get() == 1 and e.ready.get() == 1:
            self.packets.append(read_export(e))
        self.cycle += 1
        e.ready.prepare(self.ready_pattern(self.cycle))


def run_core(program_words, groups, memory_init, slots=4, export_ready=None, max_cycles=400_000):
    hw = py4hw.HWSystem()
    launch = LaunchInterface(hw, 'launch')
    exp = ExportInterface(hw, 'exp')
    mem_if = MemoryInterface(hw, 'Mem', memory_size=MEM_WORDS)
    mem = Memory(hw, 'MemImpl', mem_if, memory_size=MEM_WORDS)
    mem.memory[:] = list(memory_init)
    idle = hw.wire('idle', 1)
    core = ShaderCore(hw, 'core', launch, mem_if, exp, idle, slots=slots)
    core.load_program(program_words)
    source = LaunchSource(hw, 'src', launch, groups)
    sink = ExportSink(hw, 'sink', exp, export_ready)
    sim = py4hw.simulation.Simulator(hw)
    for cycle in range(1, max_cycles + 1):
        sim._clk_cycle()
        if source.finished and idle.get() == 1 and core.groups_finished == len(groups):
            return mem.memory, sink.packets, cycle, core, source
    raise AssertionError('core did not finish')


def run_reference(program_words, groups, memory_init):
    memory = list(memory_init)
    exports = {}
    for g in groups:
        st = GroupState(g.get('pc', 0), g.get('sgprs'), g.get('vgprs'))
        out, _ = ref_run(st, program_words, memory)
        exports[g.get('seq', 0)] = out
    return memory, exports


# --- random program generator -------------------------------------------------------
# registers: v0-v15 and s0-s11 are free for random code; s12/s13 IF condition/save,
# s14 loop counter, s28 scratch base, s29 dump base (preloaded per group)

ALU_V = [m for m, (f, _, _) in INSTRUCTIONS.items()
         if f == VOP and m not in ('V_CNDMASK', 'V_LSHL_OR', 'V_LANEID')]
ALU_S = [m for m, (f, _, _) in INSTRUCTIONS.items() if f == SOP and m not in ('S_SETPC',)]
ALU_K = ['S_MOVK', 'S_MOVHI', 'S_ORK', 'S_ADDK', 'S_CMPK_EQ', 'S_CMPK_LT', 'S_CMPK_GE']
CMPS = [m for m, (f, _, _) in INSTRUCTIONS.items() if f == VOPS and m != 'V_READFIRSTLANE']


def _v(rng):
    return f'v{rng.randrange(16)}'


def _s(rng):
    return f's{rng.randrange(12)}'


def _src0(rng):
    return _v(rng) if rng.random() < 0.7 else _s(rng)


def random_instruction(rng):
    r = rng.random()
    if r < 0.45:
        m = rng.choice(ALU_V)
        ops = INSTRUCTIONS[m][2]
        args = [_v(rng), _src0(rng)] + ([_v(rng)] if len(ops) == 3 else [])
        return f'{m} {", ".join(args)}'
    if r < 0.55:
        return rng.choice([f'V_CNDMASK {_v(rng)}, {_src0(rng)}, {_v(rng)}, {_s(rng)}',
                           f'V_LSHL_OR {_v(rng)}, {_src0(rng)}, {_v(rng)}, {rng.randrange(32)}',
                           f'V_LANEID {_v(rng)}', f'V_READFIRSTLANE {_s(rng)}, {_v(rng)}'])
    if r < 0.75:
        m = rng.choice(ALU_S)
        n = len(INSTRUCTIONS[m][2])
        if m.startswith('S_CMP_'):
            return f'{m} {_s(rng)}, {_s(rng)}'
        return f'{m} {", ".join(_s(rng) for _ in range(n))}'
    if r < 0.85:
        m = rng.choice(ALU_K)
        imm = rng.randint(0, 0xFFFF) if m in ('S_MOVHI', 'S_ORK') else rng.randint(-0x8000, 0x7FFF)
        return f'{m} {_s(rng)}, {imm}'
    if r < 0.93:
        return f'{rng.choice(CMPS)} {_s(rng)}, {_src0(rng)}, {_v(rng)}'
    # memory: group-private scratch region at s28 (64 words)
    m = rng.choice(['V_LOAD_LANE', 'V_STORE_LANE', 'V_LOAD_BCAST'])
    return f'{m} {_v(rng)}, s28, {rng.randrange(48)}'


def random_block(rng, depth, length):
    lines = []
    for _ in range(length):
        r = rng.random()
        if r < 0.08 and depth < 2:
            # divergent IF on a random lane condition (nesting uses separate save regs)
            cond, save = f's{12}', f's{20 + depth}'
            lines.append(f'{rng.choice(CMPS[:6] + CMPS[6:])} {cond}, {_src0(rng)}, {_v(rng)}')
            lines.append(f'IF {cond}, {save}')
            lines += random_block(rng, depth + 1, rng.randint(1, 5))
            if rng.random() < 0.5:
                lines.append('ELSE')
                lines += random_block(rng, depth + 1, rng.randint(1, 5))
            lines.append('ENDIF')
        elif r < 0.12 and depth == 0:
            label = f'loop{len(lines)}_{rng.randrange(10**6)}'
            lines.append(f'S_MOVK s14, {rng.randint(1, 4)}')
            lines.append(f'{label}:')
            lines += random_block(rng, depth + 1, rng.randint(1, 4))
            lines += ['S_ADDK s14, -1', 'S_CMPK_EQ s14, 0', f'S_CBRANCH_SCC0 {label}']
        else:
            lines.append(random_instruction(rng))
    return lines


def random_program(seed, length=40):
    rng = random.Random(seed)
    lines = random_block(rng, 0, length)
    # dump: all lanes on, v0-v15 then s0-s11 (broadcast) to the dump region at s29
    lines.append('S_MOVK exec, -1')
    for r in range(16):
        if r % 8 == 0 and r:
            lines.append('S_ADDK s29, 128')
        lines.append(f'V_STORE_LANE v{r}, s29, {(r % 8) * 16}')
    lines.append('S_ADDK s29, 128')
    for i in range(12):
        if i == 8:
            lines.append('S_ADDK s29, 128')
        lines.append(f'V_MOV v31, s{i}')
        lines.append(f'V_STORE_LANE v31, s29, {(i % 8) * 16}')
    lines.append('S_ENDPGM')
    return '\n'.join(lines)


def random_groups(rng, count, entries):
    groups = []
    for g in range(count):
        sgprs = {i: rng.getrandbits(32) for i in range(12)}
        sgprs.update({28: 64 * g, 29: 1024 + 640 * g, 31: rng.getrandbits(16) | 1})
        vgprs = {r: [rng.getrandbits(32) for _ in range(GROUP_WIDTH)] for r in range(5)}
        groups.append({'pc': rng.choice(entries), 'seq': g, 'sgprs': sgprs, 'vgprs': vgprs})
    return groups


@pytest.mark.parametrize('seed', range(8))
@pytest.mark.parametrize('slots', [1, 4])
def test_random_programs_match_reference(seed, slots):
    sources = [random_program(seed * 10 + k) for k in range(3)]
    # three programs in one instruction memory, at three entry points
    text = '\n'.join(f'.entry p{k}\n{src}' for k, src in enumerate(sources))
    program = assemble(text)
    rng = random.Random(seed)
    groups = random_groups(rng, 6, list(program.entries.values()))
    memory_init = [rng.getrandbits(32) for _ in range(MEM_WORDS)]
    expected, _ = run_reference(program.words, groups, memory_init)
    actual, packets, cycles, core, _ = run_core(program.words, groups, memory_init, slots=slots)
    assert actual == expected
    assert packets == []                    # compute groups never export
    assert core.instructions >= len(groups)


def test_slots_interleave_round_robin():
    program = assemble('\n'.join(['S_ADDK s1, 1'] * 6 + ['S_ENDPGM']))
    groups = [{'pc': 0, 'seq': i, 'sgprs': {31: LANE_MASK}} for i in range(3)]
    _, _, _, core, _ = run_core(program.words, groups, [0] * MEM_WORDS, slots=4)
    slots_in_order = [slot for _, slot in core.issue_trace]
    # once all three groups are resident, issue rotates 0,1,2,0,1,2...
    tail = slots_in_order[3:12]
    assert tail == [0, 1, 2] * 3, slots_in_order


def test_more_slots_hide_memory_latency():
    # each load (16 lanes x 3 cycles on the uncoalesced port) is followed by
    # ALU work; with 4 slots one group's ALU work overlaps another's load
    body = ['V_LOAD_LANE v1, s28, 0'] + ['V_ADD_I32 v2, v1, v2'] * 24
    program = assemble('\n'.join(body * 2 + ['S_ENDPGM']))
    groups = [{'pc': 0, 'seq': i, 'sgprs': {28: 16 * i, 31: LANE_MASK}} for i in range(4)]
    _, _, one_slot, _, _ = run_core(program.words, groups, [0] * MEM_WORDS, slots=1)
    _, _, four_slots, _, _ = run_core(program.words, groups, [0] * MEM_WORDS, slots=4)
    assert four_slots < 0.75 * one_slot
    print(f'4 groups, load + 24 ALU ops x2: 1 slot {one_slot} cycles, 4 slots {four_slots} cycles')


def test_launch_backpressure_with_more_groups_than_slots():
    program = assemble('S_MOVK s1, 3\nloop: S_ADDK s1, -1\nS_CMPK_EQ s1, 0\nS_CBRANCH_SCC0 loop\n'
                       'V_MOV v0, s2\nV_STORE_LANE v0, s3, 0\nS_ENDPGM')
    groups = [{'pc': 0, 'seq': i, 'sgprs': {2: 100 + i, 3: 16 * i, 31: LANE_MASK}} for i in range(10)]
    memory, _, _, core, source = run_core(program.words, groups, [0] * MEM_WORDS, slots=2)
    assert source.stall_cycles > 0 and core.groups_finished == 10
    for i in range(10):
        assert memory[16 * i:16 * i + 16] == [100 + i] * 16


PIXEL_PROGRAM = '''
.entry export_lane_ids
    V_LANEID v1
    V_ADD_I32 v1, v1, v0            ; v0 preloaded: per-group base value
    V_EXPORT_COLOR v1
    S_ENDPGM
.entry no_export
    S_NOP
    S_ENDPGM
.entry discard_all
    S_MOVK exec, 0
    V_EXPORT_COLOR v0
    S_ENDPGM
'''


def pixel_groups(program, n):
    names = ['export_lane_ids', 'no_export', 'discard_all']
    groups = []
    for i in range(n):
        groups.append({'pixel': 1, 'pc': program.entries[names[i % 3]], 'seq': i,
                       'sgprs': {31: 0xFFFF >> (i % 5)}, 'vgprs': {0: [1000 * i] * GROUP_WIDTH},
                       'tile_x': 4 * i, 'tile_y': 8 * i, 'z': [i * 16 + lane for lane in range(GROUP_WIDTH)]})
    return groups


@pytest.mark.parametrize('slots', [1, 4])
def test_pixel_groups_export_once_with_side_data(slots):
    program = assemble(PIXEL_PROGRAM)
    groups = pixel_groups(program, 9)
    _, packets, _, _, _ = run_core(program.words, groups, [0] * MEM_WORDS, slots=slots)
    by_seq = {p['seq']: p for p in packets}
    assert sorted(by_seq) == list(range(9)) and len(packets) == 9   # every group exactly once
    for i, g in enumerate(groups):
        p = by_seq[i]
        assert (p['tile_x'], p['tile_y'], p['z']) == (g['tile_x'], g['tile_y'], g['z'])
        if i % 3 == 0:
            assert p['mask'] == g['sgprs'][31]
            active = [lane for lane in range(GROUP_WIDTH) if p['mask'] >> lane & 1]
            assert all(p['colour'][lane] == 1000 * i + lane for lane in active)
        else:
            assert p['mask'] == 0                 # no export, or everything discarded


def test_export_backpressure_does_not_lose_or_duplicate_packets():
    program = assemble(PIXEL_PROGRAM)
    groups = pixel_groups(program, 12)
    rng = random.Random(9)
    _, packets, _, _, _ = run_core(program.words, groups, [0] * MEM_WORDS, slots=4,
                                   export_ready=lambda c: 1 if rng.random() < 0.3 else 0)
    assert sorted(p['seq'] for p in packets) == list(range(12))


def test_errors_stop_the_simulation():
    program = assemble('S_NOP')            # runs off the end of the program...
    program.words.append(0x70000000)       # ...into an illegal word
    with pytest.raises(VenusError, match='illegal'):
        run_core(program.words, [{'pc': 0, 'sgprs': {31: 1}}], [0] * MEM_WORDS)

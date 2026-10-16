'''
VENUS v2 reference model: a plain-Python interpreter of one thread group,
written straight from README_ISA.md. It is the golden model for the
hardware core (core.py); both use the same per-instruction functions
below, so the hardware tests check the core's control path (slots,
scheduling, memory and export ports) while tb/shaderCoreTest/test_alu.py
checks the arithmetic itself.
'''
from ..params import GROUP_WIDTH, LANE_MASK
from .alu import M32, SALU, SCMP, VALU, VCMP, V_MAC
from .isa import EXEC_SGPR, NUM_SGPRS, NUM_VGPRS, SOP, SOPK, SOPP, VOP, VOPS, VMEM, EXP, decode, signed16


class VenusError(Exception):
    '''A program did something the ISA leaves undefined (illegal
    instruction, PC outside instruction memory, double export, ...).'''


class GroupState:
    '''Registers of one thread group: s0-s31 (s31 = exec, 16 bits used),
    v0-v31 x 16 lanes, SCC, PC.'''

    def __init__(self, pc=0, sgprs=None, vgprs=None):
        self.pc = pc
        self.scc = 0
        self.s = [0] * NUM_SGPRS
        self.v = [[0] * GROUP_WIDTH for _ in range(NUM_VGPRS)]
        for i, value in (sgprs or {}).items():
            self.write_s(i, value)
        for i, lanes in (vgprs or {}).items():
            self.v[i] = [x & M32 for x in lanes]
        self.exported = False

    @property
    def exec(self):
        return self.s[EXEC_SGPR]

    def write_s(self, index, value):
        self.s[index] = value & (LANE_MASK if index == EXEC_SGPR else M32)

    def active(self, lane):
        return self.exec >> lane & 1

    def snapshot(self):
        return {'pc': self.pc, 'scc': self.scc, 's': list(self.s), 'v': [list(r) for r in self.v]}


# --- one instruction (everything except memory and export) -----------------------

def execute(st: GroupState, mnemonic, f, imem_size):
    '''Executes a SOP/SOPK/SOPP/VOP/VOPS instruction on `st`, including the
    PC update. Returns True if the instruction was S_ENDPGM.'''
    fmt = _FORMAT[mnemonic]
    next_pc = st.pc + 1

    if fmt == SOP:
        a, b = st.s[f['ssrc0']], st.s[f['ssrc1']]
        if mnemonic in SALU:
            st.write_s(f['sdst'], SALU[mnemonic](a, b))
        elif mnemonic in SCMP:
            st.scc = int(SCMP[mnemonic](a, b))
        elif mnemonic == 'S_SAVEEXEC_AND':
            old = st.exec
            st.write_s(f['sdst'], old)
            st.write_s(EXEC_SGPR, old & a)
            st.scc = int(st.exec != 0)
        elif mnemonic == 'S_SETPC':
            next_pc = a & 0xFFFF
        else:
            raise AssertionError(mnemonic)

    elif fmt == SOPK:
        d, imm = f['sdst'], f['imm16']
        simm = signed16(imm)
        if mnemonic == 'S_MOVK':
            st.write_s(d, simm)
        elif mnemonic == 'S_MOVHI':
            st.write_s(d, imm << 16)
        elif mnemonic == 'S_ORK':
            st.write_s(d, st.s[d] | imm)
        elif mnemonic == 'S_ADDK':
            st.write_s(d, st.s[d] + simm)
        elif mnemonic.startswith('S_CMPK_'):
            st.scc = int(SCMP['S_CMP_' + mnemonic[7:]](st.s[d], simm & M32))
        elif mnemonic == 'S_CALL':
            st.write_s(d, next_pc)
            next_pc = next_pc + simm
        else:
            raise AssertionError(mnemonic)

    elif fmt == SOPP:
        if mnemonic == 'S_ENDPGM':
            return True
        taken = {
            'S_NOP': False,
            'S_BRANCH': True,
            'S_CBRANCH_SCC0': st.scc == 0,
            'S_CBRANCH_SCC1': st.scc == 1,
            'S_CBRANCH_EXECZ': st.exec == 0,
            'S_CBRANCH_EXECNZ': st.exec != 0,
        }[mnemonic]
        if taken:
            next_pc += signed16(f['simm16'])

    elif fmt == VOP:
        _vop(st, mnemonic, f)

    elif fmt == VOPS:
        if mnemonic == 'V_READFIRSTLANE':
            if f['s0sel']:
                raise VenusError('V_READFIRSTLANE needs a VGPR source')
            lane = next((i for i in range(GROUP_WIDTH) if st.active(i)), 0)
            st.write_s(f['sdst'], st.v[f['src0']][lane])
        else:
            cmp = VCMP[mnemonic]
            mask = 0
            for lane in range(GROUP_WIDTH):
                if st.active(lane) and cmp(_src0(st, f, lane), st.v[f['vsrc1']][lane]):
                    mask |= 1 << lane
            st.write_s(f['sdst'], mask)

    else:
        raise AssertionError(f'{mnemonic} is not executed by execute()')

    if not 0 <= next_pc < imem_size:
        raise VenusError(f'PC {next_pc} outside instruction memory (size {imem_size})')
    st.pc = next_pc
    return False


def _src0(st, f, lane):
    return st.s[f['src0']] if f['s0sel'] else st.v[f['src0']][lane]


def _vop(st, mnemonic, f):
    d = f['vdst']
    result = list(st.v[d])
    for lane in range(GROUP_WIDTH):
        if not st.active(lane):
            continue
        a = _src0(st, f, lane)
        b = st.v[f['vsrc1']][lane]
        if mnemonic in VALU:
            result[lane] = VALU[mnemonic](a, b)
        elif mnemonic == 'V_MAC_F32':
            result[lane] = V_MAC(a, b, st.v[d][lane])
        elif mnemonic == 'V_LSHL_OR':
            result[lane] = ((a << f['aux5']) | b) & M32
        elif mnemonic == 'V_CNDMASK':
            result[lane] = b if st.s[f['aux5']] >> lane & 1 else a
        elif mnemonic == 'V_LANEID':
            result[lane] = lane
        else:
            raise AssertionError(mnemonic)
    st.v[d] = result


# --- memory and export -----------------------------------------------------------------

def vmem_requests(st: GroupState, mnemonic, f):
    '''Active lanes' accesses, in lane order: list of (lane, word address,
    store value or None for loads).'''
    base = st.s[f['sbase']] + f['offset']
    out = []
    for lane in range(GROUP_WIDTH):
        if not st.active(lane):
            continue
        if mnemonic in ('V_LOAD', 'V_STORE'):
            index = st.v[f['vaddr']][lane]
        elif mnemonic in ('V_LOAD_LANE', 'V_STORE_LANE'):
            index = lane
        else:
            index = 0
        addr = (base + index) & M32
        value = st.v[f['vdata']][lane] if mnemonic.startswith('V_STORE') else None
        out.append((lane, addr, value))
    return out


def vmem_complete(st: GroupState, mnemonic, f, loaded):
    '''Writes loaded values ({lane: value}) and advances the PC.'''
    if mnemonic.startswith('V_LOAD'):
        for lane, value in loaded.items():
            st.v[f['vdata']][lane] = value & M32
    st.pc += 1


def export_packet(st: GroupState, f):
    '''(mask, colours) of V_EXPORT_COLOR; marks the group as exported.'''
    if st.exported:
        raise VenusError('a group may export only once')
    st.exported = True
    return st.exec, list(st.v[f['vsrc']])


_FORMAT = {}


def _init_formats():
    from .isa import INSTRUCTIONS
    for name, (fmt, _, _) in INSTRUCTIONS.items():
        _FORMAT[name] = fmt


_init_formats()


def format_of(mnemonic):
    return _FORMAT[mnemonic]


# --- whole-group interpreter --------------------------------------------------------

def run(st: GroupState, program, memory, max_steps=1_000_000):
    '''Runs one group to S_ENDPGM. `program`: list of instruction words;
    `memory`: mutable list of words (global memory). Returns the list of
    exports [(mask, colours)] (empty or one entry) and the number of
    instructions executed.'''
    exports = []
    for steps in range(1, max_steps + 1):
        if not 0 <= st.pc < len(program):
            raise VenusError(f'PC {st.pc} outside instruction memory')
        try:
            mnemonic, f = decode(program[st.pc])
        except Exception as e:
            raise VenusError(str(e)) from None
        fmt = _FORMAT[mnemonic]
        if fmt == VMEM:
            loaded = {}
            for lane, addr, value in vmem_requests(st, mnemonic, f):
                if not 0 <= addr < len(memory):
                    raise VenusError(f'address {addr} outside memory')
                if value is None:
                    loaded[lane] = memory[addr]
                else:
                    memory[addr] = value
            vmem_complete(st, mnemonic, f, loaded)
        elif fmt == EXP:
            exports.append(export_packet(st, f))
            st.pc += 1
        elif execute(st, mnemonic, f, len(program)):
            return exports, steps
    raise VenusError(f'no S_ENDPGM within {max_steps} instructions')

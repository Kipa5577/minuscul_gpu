'''
venus_asm: VENUS v2 assembler and disassembler (work module M4).

Specification: src/README_ISA.md §7 (syntax, directives, macros, errors)
and §4-5 (encodings, via src/shader_core/isa.py).

    from minuscul_GPU.tools.venus_asm import assemble, disassemble
    program = assemble(source_text)
    program.words          # list of 32-bit instruction words
    program.entries        # {'entry_name': pc}
    program.write_hex(path)

Command line:
    python -m minuscul_GPU.tools.venus_asm input.vas [-o output.hex] [-d]
'''
import re
import struct
import sys

from ..src.shader_core.isa import (INSTRUCTIONS, FIELDS, EXEC_SGPR, NUM_SGPRS, NUM_VGPRS,
                                   SOPK, encode, decode, signed16)


class AsmError(Exception):
    def __init__(self, line_no, message):
        super().__init__(f'line {line_no}: {message}')
        self.line_no = line_no


class Program:
    def __init__(self, words, entries, lines):
        self.words = words
        self.entries = entries
        self.lines = lines      # source line number of each word

    def write_hex(self, path):
        with open(path, 'w') as f:
            for word in self.words:
                f.write(f'{word:08X}\n')

    def __len__(self):
        return len(self.words)


def read_hex(path):
    with open(path) as f:
        return [int(line, 16) for line in f if line.strip()]


# --- parsing helpers ---------------------------------------------------------------

_REG = re.compile(r'^([sv])(\d+)$')
_LABEL = re.compile(r'^[A-Za-z_.][A-Za-z0-9_.]*$')


def _register(token, kind, line_no):
    t = token.lower()
    if t == 'exec':
        t = f's{EXEC_SGPR}'
    m = _REG.match(t)
    if not m:
        raise AsmError(line_no, f'expected a register, got "{token}"')
    bank, index = m.group(1), int(m.group(2))
    limit = NUM_SGPRS if bank == 's' else NUM_VGPRS
    if index >= limit:
        raise AsmError(line_no, f'register {token} does not exist')
    if kind == 'S' and bank != 's':
        raise AsmError(line_no, f'expected a scalar register, got {token}')
    if kind == 'V' and bank != 'v':
        raise AsmError(line_no, f'expected a vector register, got {token}')
    return bank, index


def _integer(token, equs, line_no):
    t = token.strip()
    if t in equs:
        return equs[t]
    try:
        return int(t, 0)
    except ValueError:
        raise AsmError(line_no, f'expected an integer, got "{token}"') from None


def _imm32(token, equs, line_no):
    '''Integer (any 32-bit value) or float literal -> unsigned 32-bit pattern.'''
    t = token.strip()
    if t in equs:
        value = equs[t]
    elif re.match(r'^[+-]?(\d+\.\d*|\.\d+|\d+(\.\d*)?[eE][+-]?\d+|inf|nan)$', t, re.IGNORECASE):
        return struct.unpack('<I', struct.pack('<f', float(t)))[0]
    else:
        value = _integer(t, equs, line_no)
    if not -(1 << 31) <= value < (1 << 32):
        raise AsmError(line_no, f'{token} does not fit in 32 bits')
    return value & 0xFFFFFFFF


def _split_operands(text):
    return [t.strip() for t in text.split(',')] if text.strip() else []


# --- macros (README_ISA.md §7) ------------------------------------------------------

def _expand(mnemonic, ops, line_no, equs, if_stack, counter):
    '''Returns a list of (label_or_None, mnemonic, operand tokens) for a
    macro, or None if `mnemonic` is not a macro.'''
    def need(n):
        if len(ops) != n:
            raise AsmError(line_no, f'{mnemonic} takes {n} operands, got {len(ops)}')

    if mnemonic == 'S_MOV32':
        need(2)
        value = _imm32(ops[1], equs, line_no)
        return [(None, 'S_MOVHI', [ops[0], str(value >> 16)]),
                (None, 'S_ORK', [ops[0], str(value & 0xFFFF)])]
    if mnemonic == 'IF':
        need(2)
        k = counter[0]
        counter[0] += 1
        if_stack.append({'k': k, 'save': ops[1], 'else': False, 'line': line_no})
        return [(None, 'S_SAVEEXEC_AND', [ops[1], ops[0]]),
                (None, 'S_CBRANCH_EXECZ', [f'.else{k}'])]
    if mnemonic == 'ELSE':
        need(0)
        if not if_stack or if_stack[-1]['else']:
            raise AsmError(line_no, 'ELSE without a matching IF')
        top = if_stack[-1]
        top['else'] = True
        k, save = top['k'], top['save']
        return [(f'.else{k}', 'S_XOR', ['exec', save, 'exec']),
                (None, 'S_CBRANCH_EXECZ', [f'.endif{k}'])]
    if mnemonic == 'ENDIF':
        need(0)
        if not if_stack:
            raise AsmError(line_no, 'ENDIF without a matching IF')
        top = if_stack.pop()
        k, save = top['k'], top['save']
        labels = [f'.endif{k}'] if top['else'] else [f'.else{k}', f'.endif{k}']
        return [(labels, 'S_MOV', ['exec', save])]
    if mnemonic == 'INTERP':
        need(3)
        vd, k, vt = ops
        return [(None, 'V_LOAD_BCAST', [vt, 's0', k]), (None, 'V_MUL_F32', [vd, vt, 'v2']),
                (None, 'V_LOAD_BCAST', [vt, 's1', k]), (None, 'V_MAC_F32', [vd, vt, 'v3']),
                (None, 'V_LOAD_BCAST', [vt, 's2', k]), (None, 'V_MAC_F32', [vd, vt, 'v4'])]
    if mnemonic == 'PACK_RGBA':
        need(6)
        vd, vr, vg, vb, va, vt = ops
        if vd.lower() in (vr.lower(), vg.lower(), vb.lower()):
            raise AsmError(line_no, 'PACK_RGBA: vD must not be vR, vG or vB')
        out = [(None, 'V_CVT_UNORM8_F32', [vd, va])]
        for vx in (vr, vg, vb):
            out += [(None, 'V_CVT_UNORM8_F32', [vt, vx]), (None, 'V_LSHL_OR', [vd, vd, vt, '8'])]
        return out
    if mnemonic == 'S_LOADC':
        need(4)
        sd, sbase, off, vt = ops
        return [(None, 'V_LOAD_BCAST', [vt, sbase, off]), (None, 'V_READFIRSTLANE', [sd, vt])]
    return None


# --- assembler -------------------------------------------------------------------

def assemble(source):
    equs = {}
    labels = {}
    entries = {}
    pending_entries = []
    items = []          # (pc, line_no, mnemonic, operand tokens)
    if_stack = []
    counter = [0]

    def place_label(name, line_no):
        if name in labels:
            raise AsmError(line_no, f'label "{name}" defined twice')
        labels[name] = len(items)

    for line_no, raw in enumerate(source.splitlines(), start=1):
        text = raw.split(';', 1)[0].strip()
        # leading labels ("name:"), possibly several, possibly with an instruction after
        while True:
            m = re.match(r'^([A-Za-z_][A-Za-z0-9_]*)\s*:(.*)$', text)
            if not m:
                break
            place_label(m.group(1), line_no)
            text = m.group(2).strip()
        if not text:
            continue

        head, _, rest = text.partition(' ')
        mnemonic = head.upper()
        ops = _split_operands(rest)

        if mnemonic == '.ENTRY':
            if len(ops) != 1 or not _LABEL.match(ops[0]):
                raise AsmError(line_no, '.entry takes one name')
            pending_entries.append((ops[0], line_no))
            continue
        if mnemonic == '.EQU':
            if len(ops) != 2:
                raise AsmError(line_no, '.equ takes a name and a value')
            equs[ops[0]] = _integer(ops[1], equs, line_no)
            continue

        expansion = _expand(mnemonic, ops, line_no, equs, if_stack, counter)
        if expansion is None:
            if mnemonic not in INSTRUCTIONS:
                raise AsmError(line_no, f'unknown instruction "{head}"')
            expansion = [(None, mnemonic, ops)]

        for name, line_no_entry in pending_entries:
            if name in entries:
                raise AsmError(line_no_entry, f'entry "{name}" defined twice')
            entries[name] = len(items)
        pending_entries = []

        for label, mn, mops in expansion:
            for lab in ([label] if isinstance(label, str) else (label or [])):
                place_label(lab, line_no)
            items.append((len(items), line_no, mn, mops))

    if if_stack:
        raise AsmError(if_stack[-1]['line'], 'IF without ENDIF')
    if pending_entries:
        raise AsmError(pending_entries[0][1], '.entry at the end of the program has no instruction')

    words, lines = [], []
    for pc, line_no, mnemonic, ops in items:
        words.append(_encode_instruction(pc, line_no, mnemonic, ops, labels, equs))
        lines.append(line_no)
    return Program(words, entries, lines)


def _encode_instruction(pc, line_no, mnemonic, ops, labels, equs):
    fmt, _, operands = INSTRUCTIONS[mnemonic]
    if len(ops) != len(operands):
        raise AsmError(line_no, f'{mnemonic} takes {len(operands)} operands, got {len(ops)}')
    fields = {}
    for token, (kind, field) in zip(ops, operands):
        if kind in ('S', 'V'):
            fields[field] = _register(token, kind, line_no)[1]
        elif kind == 'SV':
            bank, index = _register(token, 'SV', line_no)
            fields[field] = index
            fields['s0sel'] = 1 if bank == 's' else 0
        elif kind == 'I16':
            value = _integer(token, equs, line_no)
            if not -0x8000 <= value <= 0x7FFF:
                raise AsmError(line_no, f'{token} does not fit a signed 16-bit immediate')
            fields[field] = value & 0xFFFF
        elif kind == 'U16':
            value = _integer(token, equs, line_no)
            if not 0 <= value <= 0xFFFF:
                raise AsmError(line_no, f'{token} does not fit an unsigned 16-bit immediate')
            fields[field] = value
        elif kind == 'L16':
            if token in labels:
                offset = labels[token] - (pc + 1)
            elif _LABEL.match(token) and token not in equs:
                raise AsmError(line_no, f'unknown label "{token}"')
            else:
                offset = _integer(token, equs, line_no)
            if not -0x8000 <= offset <= 0x7FFF:
                raise AsmError(line_no, f'branch to {token} is too far')
            fields[field] = offset & 0xFFFF
        elif kind == 'U5':
            value = _integer(token, equs, line_no)
            if not 0 <= value <= 31:
                raise AsmError(line_no, f'{token} does not fit 0-31')
            fields[field] = value
        elif kind == 'U7':
            value = _integer(token, equs, line_no)
            if not 0 <= value <= 127:
                raise AsmError(line_no, f'offset {token} does not fit 0-127')
            fields[field] = value
        else:
            raise AssertionError(kind)
    return encode(mnemonic, fields)


# --- disassembler ------------------------------------------------------------------

def disassemble_word(word):
    mnemonic, fields = decode(word)
    fmt, _, operands = INSTRUCTIONS[mnemonic]
    out = []
    for kind, field in operands:
        value = fields[field]
        if kind == 'S':
            out.append('exec' if value == EXEC_SGPR else f's{value}')
        elif kind == 'V':
            out.append(f'v{value}')
        elif kind == 'SV':
            if fields['s0sel']:
                out.append('exec' if value == EXEC_SGPR else f's{value}')
            else:
                out.append(f'v{value}')
        elif kind in ('I16', 'L16'):
            out.append(str(signed16(value)))
        elif kind == 'U16':
            out.append(f'0x{value:04X}')
        else:
            out.append(str(value))
    return mnemonic + (' ' + ', '.join(out) if out else '')


def disassemble(words):
    return [disassemble_word(w) for w in words]


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description='VENUS v2 assembler')
    parser.add_argument('input')
    parser.add_argument('-o', '--output', help='write a .hex file')
    parser.add_argument('-d', '--disassemble', action='store_true',
                        help='input is a .hex file; print its disassembly')
    args = parser.parse_args(argv)
    if args.disassemble:
        for pc, line in enumerate(disassemble(read_hex(args.input))):
            print(f'{pc:4d}: {line}')
        return 0
    with open(args.input) as f:
        try:
            program = assemble(f.read())
        except AsmError as e:
            print(f'{args.input}: {e}', file=sys.stderr)
            return 1
    if args.output:
        program.write_hex(args.output)
    for name, pc in program.entries.items():
        print(f'.entry {name} = {pc}')
    print(f'{len(program)} instructions')
    return 0


if __name__ == '__main__':
    sys.exit(main())

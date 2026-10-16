'''
M4 testbench: the venus_asm assembler/disassembler and the shared ISA
tables (src/shader_core/isa.py).
'''
import random

import pytest

from ...src.shader_core.isa import (INSTRUCTIONS, SOP, SOPK, SOPP, VOP, VOPS, VMEM, EXP,
                                    IllegalInstruction, decode, encode)
from ...tools.venus_asm import AsmError, assemble, disassemble, read_hex, main


# --- round trip over the whole instruction table ----------------------------------

def random_operand(kind, rng):
    if kind == 'S':
        return rng.choice([f's{rng.randrange(31)}', 'exec'])
    if kind == 'V':
        return f'v{rng.randrange(32)}'
    if kind == 'SV':
        return rng.choice([f'v{rng.randrange(32)}', f's{rng.randrange(32)}'])
    if kind in ('I16', 'L16'):
        return str(rng.randint(-0x8000, 0x7FFF))
    if kind == 'U16':
        return hex(rng.randint(0, 0xFFFF))
    if kind == 'U5':
        return str(rng.randint(0, 31))
    if kind == 'U7':
        return str(rng.randint(0, 127))
    raise AssertionError(kind)


@pytest.mark.parametrize('mnemonic', sorted(INSTRUCTIONS))
def test_round_trip_every_instruction(mnemonic):
    rng = random.Random(mnemonic)
    operands = INSTRUCTIONS[mnemonic][2]
    lines = [mnemonic + (' ' + ', '.join(random_operand(k, rng) for k, _ in operands) if operands else '')
             for _ in range(20)]
    words = assemble('\n'.join(lines)).words
    text = disassemble(words)
    assert assemble('\n'.join(text)).words == words
    for word in words:
        assert decode(word)[0] == mnemonic


def test_every_opcode_is_unique_and_fits():
    seen = set()
    for name, (fmt, op, _) in INSTRUCTIONS.items():
        assert 0 <= fmt <= 6 and 0 <= op < 64
        assert (fmt, op) not in seen, name
        seen.add((fmt, op))
    # instruction counts per format, from the README_ISA §5 tables
    per_format = {fmt: sum(1 for f, _, _ in INSTRUCTIONS.values() if f == fmt)
                  for fmt in (SOP, SOPK, SOPP, VOP, VOPS, VMEM, EXP)}
    assert per_format == {SOP: 25, SOPK: 8, SOPP: 7, VOP: 30, VOPS: 13, VMEM: 5, EXP: 1}


# --- encodings checked by hand against README_ISA §4 -------------------------------

@pytest.mark.parametrize('text,word', [
    ('S_ADD s1, s2, s3', (SOP << 28) | (0x02 << 22) | (1 << 17) | (2 << 12) | (3 << 7)),
    ('S_MOVK s5, -2', (SOPK << 28) | (0x00 << 22) | (5 << 17) | 0xFFFE),
    ('S_ENDPGM', (SOPP << 28) | (0x01 << 22)),
    ('V_ADD_F32 v3, s4, v5', (VOP << 28) | (0x10 << 22) | (3 << 17) | (1 << 16) | (4 << 11) | (5 << 6)),
    ('V_LSHL_OR v1, v2, v3, 8', (VOP << 28) | (0x0D << 22) | (1 << 17) | (2 << 11) | (3 << 6) | (8 << 1)),
    ('V_CNDMASK v1, v2, v3, s7', (VOP << 28) | (0x0E << 22) | (1 << 17) | (2 << 11) | (3 << 6) | (7 << 1)),
    ('V_CMP_LT_I32 exec, s1, v2', (VOPS << 28) | (0x0A << 22) | (31 << 17) | (1 << 16) | (1 << 11) | (2 << 6)),
    ('V_LOAD v1, v2, s3, 127', (VMEM << 28) | (0x00 << 22) | (1 << 17) | (2 << 12) | (3 << 7) | 127),
    ('V_EXPORT_COLOR v9', (EXP << 28) | (9 << 17)),
])
def test_known_encodings(text, word):
    assert assemble(text).words == [word]


def test_branch_offsets_are_relative_to_next_instruction():
    program = assemble('''
    top:  S_NOP
          S_BRANCH top        ; pc 1 -> 0: offset -2
          S_CBRANCH_SCC1 end  ; pc 2 -> 4: offset +1
          S_NOP
    end:  S_ENDPGM
    ''')
    assert [decode(w)[1]['simm16'] for w in program.words[1:3]] == [0xFFFE, 0x0001]


def test_labels_entries_and_equ():
    program = assemble('''
    .equ COLOUR_WORD, 4
    .entry first
        V_LOAD_BCAST v1, s0, COLOUR_WORD
        S_ENDPGM
    .entry second
    loop: S_ADDK s1, -1
        S_CBRANCH_SCC0 loop
    ''')
    assert program.entries == {'first': 0, 'second': 2}
    assert decode(program.words[0])[1]['offset'] == 4
    assert program.lines == [4, 5, 7, 8]


# --- macros expand exactly as README_ISA §7 specifies --------------------------------

@pytest.mark.parametrize('macro,expansion', [
    ('S_MOV32 s4, 0x12345678', 'S_MOVHI s4, 0x1234\nS_ORK s4, 0x5678'),
    ('S_MOV32 s4, 1.0', 'S_MOVHI s4, 0x3F80\nS_ORK s4, 0'),
    ('S_MOV32 s4, -1', 'S_MOVHI s4, 0xFFFF\nS_ORK s4, 0xFFFF'),
    ('INTERP v5, 4, v12', 'V_LOAD_BCAST v12, s0, 4\nV_MUL_F32 v5, v12, v2\nV_LOAD_BCAST v12, s1, 4\n'
                          'V_MAC_F32 v5, v12, v3\nV_LOAD_BCAST v12, s2, 4\nV_MAC_F32 v5, v12, v4'),
    ('PACK_RGBA v9, v5, v6, v7, v8, v12',
     'V_CVT_UNORM8_F32 v9, v8\n' + '\n'.join(f'V_CVT_UNORM8_F32 v12, v{x}\nV_LSHL_OR v9, v9, v12, 8'
                                              for x in (5, 6, 7))),
    ('S_LOADC s12, s4, 3, v1', 'V_LOAD_BCAST v1, s4, 3\nV_READFIRSTLANE s12, v1'),
])
def test_macro_expansion(macro, expansion):
    assert assemble(macro).words == assemble(expansion).words


def test_if_else_endif_expansion():
    assert assemble('''
        IF s10, s20
        V_MOV v1, v2
        ELSE
        V_MOV v1, v3
        ENDIF
    ''').words == assemble('''
        S_SAVEEXEC_AND s20, s10
        S_CBRANCH_EXECZ else0
        V_MOV v1, v2
    else0: S_XOR exec, s20, exec
        S_CBRANCH_EXECZ endif0
        V_MOV v1, v3
    endif0: S_MOV exec, s20
    ''').words


def test_if_without_else_and_nesting():
    assert assemble('''
        IF s10, s20
          IF s11, s21
            V_MOV v1, v2
          ENDIF
        ENDIF
    ''').words == assemble('''
        S_SAVEEXEC_AND s20, s10
        S_CBRANCH_EXECZ outer
        S_SAVEEXEC_AND s21, s11
        S_CBRANCH_EXECZ inner
        V_MOV v1, v2
    inner: S_MOV exec, s21
    outer: S_MOV exec, s20
    ''').words


def test_readme_examples_assemble():
    gouraud = assemble('''
    .entry ps_gouraud
        INTERP    v5, 4, v12
        INTERP    v6, 5, v12
        INTERP    v7, 6, v12
        S_MOV32   s12, 1.0
        V_MOV     v8, s12
        PACK_RGBA v9, v5, v6, v7, v8, v12
        V_EXPORT_COLOR v9
        S_ENDPGM
    ''')
    assert len(gouraud) == 3 * 6 + 2 + 1 + 7 + 1 + 1 and gouraud.entries == {'ps_gouraud': 0}
    discard = assemble('''
        S_MOV32   s13, 0.5
        V_CMP_GT_F32 s14, s13, v5
        S_ANDN2   exec, exec, s14
    ''')
    assert len(discard) == 4
    vadd = assemble('''
    .entry vadd
        V_LOAD    v1, v0, s4, 0
        V_LOAD    v2, v0, s5, 0
        V_ADD_F32 v3, v1, v2
        V_STORE   v3, v0, s6, 0
        S_ENDPGM
    ''')
    assert len(vadd) == 5


# --- errors carry the right line number ---------------------------------------------

@pytest.mark.parametrize('source,line,message', [
    ('S_NOP\nS_FOO s1', 2, 'unknown instruction'),
    ('V_ADD_F32 v1, v2, s3', 1, 'expected a vector register'),
    ('S_ADD s1, v2, s3', 1, 'expected a scalar register'),
    ('S_MOV s32, s1', 1, 'does not exist'),
    ('S_MOVK s1, 40000', 1, 'signed 16-bit'),
    ('S_MOVHI s1, -1', 1, 'unsigned 16-bit'),
    ('V_LOAD v1, v2, s3, 128', 1, '0-127'),
    ('V_LSHL_OR v1, v2, v3, 32', 1, '0-31'),
    ('S_BRANCH nowhere', 1, 'unknown label'),
    ('S_ADD s1, s2', 1, 'takes 3 operands'),
    ('S_NOP\n\nELSE', 3, 'ELSE without'),
    ('ENDIF', 1, 'ENDIF without'),
    ('S_NOP\nIF s1, s2\nS_NOP', 2, 'IF without ENDIF'),
    ('PACK_RGBA v5, v5, v6, v7, v8, v9', 1, 'must not be'),
    ('a: S_NOP\na: S_NOP', 2, 'defined twice'),
    ('S_NOP\n.entry tail', 2, 'no instruction'),
])
def test_errors(source, line, message):
    with pytest.raises(AsmError) as info:
        assemble(source)
    assert info.value.line_no == line and message in str(info.value)


def test_far_branch_is_rejected():
    source = 'S_BRANCH far\n' + 'S_NOP\n' * 0x8000 + 'far: S_ENDPGM'
    with pytest.raises(AsmError, match='too far'):
        assemble(source)


# --- decoder ---------------------------------------------------------------------------

@pytest.mark.parametrize('word', [
    0x70000000,                                  # format 7: reserved
    (SOPP << 28) | (0x07 << 22),                 # S_BARRIER: reserved opcode
    (SOP << 28) | (0x17 << 22),                  # gap in the SOP opcodes
    (SOP << 28) | (0x02 << 22) | 1,              # S_ADD with reserved low bits set
    (VOP << 28) | 1,                             # VOP bit 0 set
])
def test_decoder_rejects_illegal_words(word):
    with pytest.raises(IllegalInstruction):
        decode(word)


def test_encode_checks_field_width():
    with pytest.raises(AssertionError):
        encode('S_MOV', {'sdst': 32, 'ssrc0': 0})


# --- files and command line -------------------------------------------------------------

def test_hex_file_and_command_line(tmp_path, capsys):
    src = tmp_path / 'prog.vas'
    src.write_text('.entry main\nS_MOVK s1, 5\nS_ENDPGM\n')
    out = tmp_path / 'prog.hex'
    assert main([str(src), '-o', str(out)]) == 0
    assert read_hex(out) == assemble(src.read_text()).words
    assert main([str(out), '-d']) == 0
    printed = capsys.readouterr().out
    assert '.entry main = 0' in printed and 'S_MOVK s1, 5' in printed

    bad = tmp_path / 'bad.vas'
    bad.write_text('S_FOO\n')
    assert main([str(bad)]) == 1

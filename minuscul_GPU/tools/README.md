# tools

Host-side software tools. These are not hardware.

## M4: `venus_asm`, the VENUS v2 assembler and disassembler - done

Implemented in `venus_asm.py`; the instruction table and bit encodings
are in `src/shader_core/isa.py` (89 instructions). Tests:
`tb/assemblerTest/`.

```
python -m minuscul_GPU.tools.venus_asm prog.vas -o prog.hex   # assemble
python -m minuscul_GPU.tools.venus_asm prog.hex -d             # disassemble
```

Specification: [README_ISA.md §7](../src/README_ISA.md) (syntax,
directives, output, errors, macros) and §4-5 (encodings).

Tasks:
- [x] encoder: one table of formats and opcodes, generated from the spec,
      shared with the disassembler and with the shader core's decoder
      tests
- [x] parser: labels, `.entry`, `.equ`, comments, `exec` alias, decimal,
      hex and float immediates
- [x] macros: `S_MOV32`, `IF`/`ELSE`/`ENDIF` (nesting), `INTERP`,
      `PACK_RGBA`, `S_LOADC`
- [x] errors with line numbers: unknown mnemonic, wrong register kind,
      out-of-range immediate or offset, branch too far, unbalanced macro
- [x] output: word list + entry-point table (Python) and a `.hex` file
- [x] disassembler: words → text

**Done when:** the three README_ISA §8 examples assemble, and for every
instruction, assemble → disassemble → assemble gives identical words. Each
macro expands to the exact sequence in the spec. Tests live in
`minuscul_GPU/tb/assemblerTest/`.

**Depends on:** nothing; it can be done in parallel with M1-M3. M5 uses
it for its test programs.

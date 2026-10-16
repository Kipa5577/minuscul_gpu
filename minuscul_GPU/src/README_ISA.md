# VENUS v2 ISA Specification

The instruction set of the minuscul_GPU shader cores. It follows the
architecture decisions D4-D11 and D13-D15 in [decisions.md](decisions.md):
16-lane thread groups, a software-managed `EXEC` mask, an integer scalar
unit, an FP32 vector unit, and one shared global memory.

Instruction *behaviour* follows AMD's public RDNA ISA guide wherever it
fits. The encodings are our own.

The older v1 draft is kept in [README_ISA_v1.md](README_ISA_v1.md).

---

## 1. Machine state (per thread group)

| State | Size | Notes |
|---|---|---|
| `v0`-`v31` | 32 vector registers × 16 lanes × 32 bit | per-lane values; FP32 or integer, by instruction |
| `s0`-`s30` | 31 scalar registers × 32 bit | values shared by the whole group |
| `s31` = `exec` | 32 bit, low 16 bits used | **EXEC mask**: bit *i* = lane *i* active. Bits 31-16 always read as 0 |
| `SCC` | 1 bit | scalar condition code, set by scalar compares and `S_SAVEEXEC_AND` |
| `PC` | 16 bit | instruction index into the core's private instruction memory |

Storage per group: 2 KB of VGPRs + 128 B of SGPRs. With the default 4
group slots (D8), that's 8.5 KB per core.

**Lanes and pixels.** For pixel work, lane *i* covers pixel
`(tile_x + (i & 3), tile_y + (i >> 2))` of a 4×4 tile. The 2×2 quads are
lanes {0,1,4,5}, {2,3,6,7}, {8,9,12,13} and {10,11,14,15}.

**Instruction memory.** Each core has a private instruction memory
(`IMEM_WORDS`, default 1024 × 32 bit), loaded by the host with the program
image before launch. Every core holds the same image. `PC` counts
instructions, not bytes.

**Data memory.** Global memory is **word-addressed** (one address = one
32-bit word). It is organised in 512-bit lines of 16 words. There are no
byte or half-word accesses.

## 2. Number formats

- **Integer:** 32-bit two's complement, wrap-around on overflow. Used by
  the scalar unit and the `_I32` and bitwise vector instructions.
- **FP32:** IEEE-754 single precision, simplified as on GPUs (D10):
  - round to nearest even only;
  - denormal inputs and outputs are flushed to zero (sign kept);
  - no exception traps;
  - Inf and NaN behave as in IEEE.

  `MIN`/`MAX` return the non-NaN operand if only one is NaN. A compare
  involving NaN is false, except `NE`, which is true. `RCP` and `SQRT` are
  correctly rounded in the reference model; `RSQ` is rounded from a
  double-precision result, which can differ from exact rounding by
  one ulp in rare cases.

## 3. EXEC rules

- A vector instruction writes its destination only in **active** lanes
  (`exec` bit = 1). Inactive lanes keep their old values.
- A vector compare writes 1 or 0 for active lanes and **0 for inactive
  lanes** into its 16-bit result mask.
- Vector loads and stores access memory only for active lanes.
- `V_EXPORT_COLOR` writes only active lanes. Clearing a lane's `exec` bit
  before export **discards** that pixel.
- Scalar instructions are never affected by `exec`.

## 4. Encoding

Every instruction is 32 bits. All formats share one header:

```
 31   28 27     22 21                                   0
┌───────┬─────────┬──────────────────────────────────────┐
│  FMT  │   OP    │            format payload            │
└───────┴─────────┴──────────────────────────────────────┘
```

| FMT | Name | Payload [21:0] |
|---|---|---|
| 0 | **SOP** scalar ALU | `[21:17] SDST · [16:12] SSRC0 · [11:7] SSRC1 · [6:0] 0` |
| 1 | **SOPK** scalar + 16-bit immediate | `[21:17] SDST · [16] 0 · [15:0] IMM16` |
| 2 | **SOPP** program control | `[21:16] 0 · [15:0] SIMM16` |
| 3 | **VOP** vector ALU | `[21:17] VDST · [16] S0SEL · [15:11] SRC0 · [10:6] VSRC1 · [5:1] AUX5 · [0] 0` |
| 4 | **VOPS** vector → scalar result | `[21:17] SDST · [16] S0SEL · [15:11] SRC0 · [10:6] VSRC1 · [5:0] 0` |
| 5 | **VMEM** vector memory | `[21:17] VDATA · [16:12] VADDR · [11:7] SBASE · [6:0] OFFSET` |
| 6 | **EXP** export | `[21:17] VSRC · [16:0] 0` |
| 7-15 | reserved | |

Field rules:
- In VOP and VOPS, `SRC0` is a VGPR when `S0SEL = 0` and an SGPR
  (broadcast to all lanes) when `S0SEL = 1`. `VSRC1` is always a VGPR.
  Constants reach vector instructions through an SGPR. There are no
  literal constants in the instruction stream.
- `SIMM16` and branch offsets are signed. A branch target is
  `PC + 1 + SIMM16`.
- `OFFSET` in VMEM is unsigned, 0-127 words.
- A reserved format or opcode is **illegal**. The reference model stops
  with an error, so mistakes are caught during verification.

## 5. Instructions

Notation: `D` = destination, `A` = SRC0/SSRC0, `B` = VSRC1/SSRC1,
`lane` = the lane index 0-15.

### 5.1 SOP: scalar ALU (FMT 0)

| OP | Mnemonic | Operation |
|---|---|---|
| 0x00 | `S_MOV D, A` | D = A |
| 0x01 | `S_NOT D, A` | D = ~A |
| 0x02 | `S_ADD D, A, B` | D = A + B |
| 0x03 | `S_SUB D, A, B` | D = A − B |
| 0x04 | `S_MUL D, A, B` | D = low 32 bits of A × B |
| 0x05 | `S_AND D, A, B` | D = A & B |
| 0x06 | `S_OR D, A, B` | D = A \| B |
| 0x07 | `S_XOR D, A, B` | D = A ^ B |
| 0x08 | `S_ANDN2 D, A, B` | D = A & ~B |
| 0x09 | `S_SHL D, A, B` | D = A << (B & 31) |
| 0x0A | `S_SHR D, A, B` | D = A >> (B & 31), logical |
| 0x0B | `S_ASHR D, A, B` | D = A >> (B & 31), arithmetic |
| 0x0C | `S_MIN_I32 D, A, B` | D = min(A, B), signed |
| 0x0D | `S_MAX_I32 D, A, B` | D = max(A, B), signed |
| 0x0E | `S_BITCNT D, A` | D = number of 1 bits in A |
| 0x0F | `S_FF1 D, A` | D = index of the lowest 1 bit in A, or −1 if A = 0 |
| 0x10 | `S_CMP_EQ A, B` | SCC = (A == B) |
| 0x11 | `S_CMP_NE A, B` | SCC = (A != B) |
| 0x12 | `S_CMP_LT A, B` | SCC = (A < B), signed |
| 0x13 | `S_CMP_LE A, B` | SCC = (A ≤ B), signed |
| 0x14 | `S_CMP_GT A, B` | SCC = (A > B), signed |
| 0x15 | `S_CMP_GE A, B` | SCC = (A ≥ B), signed |
| 0x16 | `S_CMP_LT_U32 A, B` | SCC = (A < B), unsigned |
| 0x18 | `S_SAVEEXEC_AND D, A` | D = exec; exec = exec & A; SCC = (exec != 0) |
| 0x19 | `S_SETPC A` | PC = A (used to return from `S_CALL`) |

### 5.2 SOPK: scalar with immediate (FMT 1)

| OP | Mnemonic | Operation |
|---|---|---|
| 0x00 | `S_MOVK D, simm16` | D = sign-extended simm16 |
| 0x01 | `S_MOVHI D, imm16` | D = imm16 << 16 (low half cleared) |
| 0x02 | `S_ORK D, imm16` | D = D \| zero-extended imm16 |
| 0x03 | `S_ADDK D, simm16` | D = D + sign-extended simm16 |
| 0x04 | `S_CMPK_EQ D, simm16` | SCC = (D == simm16) |
| 0x05 | `S_CMPK_LT D, simm16` | SCC = (D < simm16), signed |
| 0x06 | `S_CMPK_GE D, simm16` | SCC = (D ≥ simm16), signed |
| 0x07 | `S_CALL D, simm16` | D = PC + 1; PC = PC + 1 + simm16 |

### 5.3 SOPP: program control (FMT 2)

| OP | Mnemonic | Operation |
|---|---|---|
| 0x00 | `S_NOP` | nothing |
| 0x01 | `S_ENDPGM` | group finishes; its slot is freed |
| 0x02 | `S_BRANCH simm16` | always jump |
| 0x03 | `S_CBRANCH_SCC0 simm16` | jump if SCC = 0 |
| 0x04 | `S_CBRANCH_SCC1 simm16` | jump if SCC = 1 |
| 0x05 | `S_CBRANCH_EXECZ simm16` | jump if exec = 0 |
| 0x06 | `S_CBRANCH_EXECNZ simm16` | jump if exec ≠ 0 |
| 0x07 | `S_BARRIER` | *reserved* (needs workgroups; not in v2) |

### 5.4 VOP: vector ALU (FMT 3)

Unary instructions ignore `VSRC1`. Shifts take the shift amount from `A`,
so it can be an SGPR, and the value from `B`.

| OP | Mnemonic | Operation (per active lane) |
|---|---|---|
| 0x00 | `V_MOV D, A` | D = A |
| 0x01 | `V_NOT D, A` | D = ~A |
| 0x02 | `V_ADD_I32 D, A, B` | D = A + B |
| 0x03 | `V_SUB_I32 D, A, B` | D = A − B |
| 0x04 | `V_MUL_I32 D, A, B` | D = low 32 bits of A × B |
| 0x05 | `V_AND D, A, B` | D = A & B |
| 0x06 | `V_OR D, A, B` | D = A \| B |
| 0x07 | `V_XOR D, A, B` | D = A ^ B |
| 0x08 | `V_SHL D, A, B` | D = B << (A & 31) |
| 0x09 | `V_SHR D, A, B` | D = B >> (A & 31), logical |
| 0x0A | `V_ASHR D, A, B` | D = B >> (A & 31), arithmetic |
| 0x0B | `V_MIN_I32 D, A, B` | D = min(A, B), signed |
| 0x0C | `V_MAX_I32 D, A, B` | D = max(A, B), signed |
| 0x0D | `V_LSHL_OR D, A, B, n` | D = (A << n) \| B, with n = AUX5 (0-31) |
| 0x0E | `V_CNDMASK D, A, B, sM` | D = (bit *lane* of SGPR `sM`) ? B : A, with M = AUX5 |
| 0x0F | `V_LANEID D` | D = lane index (0-15) |
| 0x10 | `V_ADD_F32 D, A, B` | D = A + B |
| 0x11 | `V_SUB_F32 D, A, B` | D = A − B |
| 0x12 | `V_MUL_F32 D, A, B` | D = A × B |
| 0x13 | `V_MAC_F32 D, A, B` | D = A × B + D, fused (one rounding) |
| 0x14 | `V_MIN_F32 D, A, B` | D = min(A, B) |
| 0x15 | `V_MAX_F32 D, A, B` | D = max(A, B) |
| 0x18 | `V_CVT_F32_I32 D, A` | D = float(A) |
| 0x19 | `V_CVT_I32_F32 D, A` | D = int(A), truncated toward zero, saturated; NaN → 0 |
| 0x1A | `V_CVT_UNORM8_F32 D, A` | D = round(clamp(A, 0, 1) × 255) as an integer 0-255; NaN → 0 |
| 0x1B | `V_RCP_F32 D, A` | D = 1 / A |
| 0x1C | `V_RSQ_F32 D, A` | D = 1 / √A |
| 0x1D | `V_SQRT_F32 D, A` | D = √A |
| 0x1E | `V_FLOOR_F32 D, A` | D = floor(A) |
| 0x1F | `V_FRACT_F32 D, A` | D = A − floor(A) |

### 5.5 VOPS: vector → scalar (FMT 4)

| OP | Mnemonic | Operation |
|---|---|---|
| 0x00-0x05 | `V_CMP_{EQ,NE,LT,LE,GT,GE}_F32 sD, A, B` | bit *lane* of sD = (A op B) for active lanes, 0 for inactive lanes; bits 31-16 = 0 |
| 0x08-0x0D | `V_CMP_{EQ,NE,LT,LE,GT,GE}_I32 sD, A, B` | same, signed integer compare |
| 0x10 | `V_READFIRSTLANE sD, vA` | sD = vA of the lowest active lane (lane 0 if exec = 0) |

### 5.6 VMEM: vector memory (FMT 5)

Word address per lane = `s[SBASE] + index + OFFSET`, where `index` is:

| OP | Mnemonic | `index` | Use |
|---|---|---|---|
| 0x00 | `V_LOAD vD, vA, sB, off` | `vA[lane]` | general gather |
| 0x01 | `V_LOAD_LANE vD, sB, off` | `lane` | "thread *i* reads word *i*" |
| 0x02 | `V_LOAD_BCAST vD, sB, off` | 0 | same word to every lane (constants, vertex attributes) |
| 0x08 | `V_STORE vS, vA, sB, off` | `vA[lane]` | general scatter |
| 0x09 | `V_STORE_LANE vS, sB, off` | `lane` | "thread *i* writes word *i*" |

**Coalescing (D9):** the core issues one memory transaction per distinct
512-bit line touched by the active lanes. `V_LOAD_BCAST` is always one
transaction, and so are `_LANE` forms whose base is 16-word aligned. The
group waits (not ready) until all its transactions complete. If two
active lanes store to the same word, the higher lane wins.

### 5.7 EXP: export (FMT 6)

| OP | Mnemonic | Operation |
|---|---|---|
| 0x00 | `V_EXPORT_COLOR vS` | send each active lane's 32-bit colour to the slice's output stage |

Exports are handed over **in launch order** (D8). The output stage then
does the depth test and the framebuffer write. Colour format: bits
`[31:24]` A, `[23:16]` R, `[15:8]` G, `[7:0]` B. A pixel shader exports at
most once; a group that ends without exporting writes nothing.

## 6. Launching work

A fixed-function **front end** reads a descriptor from global memory and
starts work. The host writes the descriptor, loads the program image into
every core's instruction memory, and triggers the front end.

### 6.1 Draw descriptor (32 words, 16-word aligned)

| Word | Field |
|---|---|
| 0 | `VS_PC`: vertex shader entry; `0xFFFFFFFF` = no vertex pass (host already wrote screen-space triangle lines to `TRI_BUF`) |
| 1 | `PS_PC`: pixel shader entry |
| 2 | `VERTEX_COUNT` |
| 3 | `TRIANGLE_COUNT` |
| 4 | `INDEX_BUF`: 3 × 32-bit vertex indices per triangle (triangle list) |
| 5 | `VOUT_BASE`: vertex output records (see 6.4) |
| 6 | `VOUT_STRIDE`: words per vertex record, 4-128 |
| 7 | `FLAGS`: bits 1-0 cull mode (0 none, 1 back, 2 front); bit 2 front face (0 = counter-clockwise, 1 = clockwise) |
| 8-15 | `USER[0..7]`: copied into `s4`-`s11` of every vertex and pixel group |
| 16 | `TRI_BUF`: triangle lines (one 512-bit line each; the primitive unit's output, the Binner's input) |
| 17-31 | reserved, 0 |

Sequence of a draw:

1. **Vertex pass:** groups run on all cores until every one finishes.
2. **Primitive unit:** reads `INDEX_BUF` and the positions in the vertex
   records, and writes surviving triangles to `TRI_BUF` (rules in 6.5).
3. **Binner:** sends each triangle line to the slice(s) whose strip it
   overlaps.
4. **Pixel pass:** in each slice, the rasteriser launches pixel groups.

### 6.2 Compute descriptor (16 words)

| Word | Field |
|---|---|
| 0 | `PC`: entry point |
| 1 | `THREAD_COUNT` |
| 2-7 | reserved, 0 |
| 8-15 | `USER[0..7]` → `s4`-`s11` |

Groups (`ceil(THREAD_COUNT / 16)` of them) are spread round-robin across
the slices' cores.

### 6.3 Registers preloaded at launch

Every register not listed starts at 0.

| | Vertex group | Pixel group | Compute group |
|---|---|---|---|
| `exec` | lanes whose vertex index < `VERTEX_COUNT` | **coverage mask** of the 4×4 tile | lanes whose thread id < `THREAD_COUNT` |
| `v0` | vertex index | pixel x (i32) | global thread id |
| `v1` | - | pixel y (i32) | - |
| `v2`, `v3`, `v4` | - | barycentrics b0, b1, b2 (FP32, perspective-correct, sum = 1) | - |
| `s0` | group id | word address of vertex 0's record | group id |
| `s1` | `VERTEX_COUNT` | word address of vertex 1's record | `THREAD_COUNT` |
| `s2` | `VOUT_BASE` | word address of vertex 2's record | - |
| `s3` | `VOUT_STRIDE` | triangle flat colour (line word 9) | - |
| `s4`-`s11` | `USER[0..7]` | `USER[0..7]` | `USER[0..7]` |

Pixel group details (D15):
- `s0`-`s2` = `VOUT_BASE + index_k × VOUT_STRIDE`, computed by the
  dispatcher. For host-written triangle lines without indices (index
  words = `0xFFFFFFFF`), `s0`-`s2` = 0.
- Barycentrics: the rasteriser gives screen-space weights
  `λk = Wk / Area`. The dispatcher corrects them for perspective using the
  line's `1/w` words: `bk = λk·qk / Σj λj·qj`, where `qk = 1/wk`. A `1/w`
  word that is all-zero bits means "no perspective" and is treated as 1.0,
  so host-written screen-space lines get plain screen-space barycentrics.

### 6.4 Vertex output records (D15)

The vertex shader writes one record per vertex at
`VOUT_BASE + vertex_index × VOUT_STRIDE` (array of structures):

| Record word | Content |
|---|---|
| 0-3 | clip-space position x, y, z, w (FP32), **required** - read by the primitive unit |
| 4 … `VOUT_STRIDE`−1 | attributes (FP32) for the pixel shader: colours, normals, texture coordinates… |

A pixel shader reads attribute word *k* of its three vertices with
`V_LOAD_BCAST v, s0/s1/s2, k`. Since `OFFSET` ≤ 127, `VOUT_STRIDE` ≤ 128.

### 6.5 Primitive unit and triangle lines (D14, D15)

For each triangle, the primitive unit:
1. reads its 3 indices and the 3 clip-space positions;
2. **trivially rejects** it if all three vertices are outside the same
   frustum plane (x > w, x < −w, y > w, y < −w, or z > w);
3. **near plane, first version:** drops it if any vertex has `w ≤ 0` or
   `z < −w`. No clipping yet (D14);
4. divides by w and applies the viewport (integer screen coordinates):
   `X = round((x/w + 1)/2 × WIDTH)`, `Y = round((1 − y/w)/2 × HEIGHT)`,
   `Z = round((z/w + 1)/2 × (2²⁴ − 1))`;
5. **guard band:** drops it if any `|X|` or `|Y|` > 16383. This keeps the
   rasteriser's edge-function products within 32 bits;
6. computes the signed area: drops zero-area triangles, and culls by
   `FLAGS`;
7. writes a 512-bit triangle line to `TRI_BUF`:

| Line word | Content |
|---|---|
| 0-8 | V1X V1Y V1Z V2X V2Y V2Z V3X V3Y V3Z (i32 screen space, as today) |
| 9 | flat colour (0 for shaded draws; host-written lines may set it) |
| 10-12 | vertex indices i0, i1, i2 (`0xFFFFFFFF` = none) |
| 13-15 | 1/w0, 1/w1, 1/w2 (FP32; all-zero bits = 1.0) |

Words 0-9 are unchanged from the current rasteriser format, so existing
host-written lines (words 10-15 = 0) remain valid. The dispatcher treats
index words of 0 (old-style lines) the same as `0xFFFFFFFF` when `VS_PC` is
`0xFFFFFFFF`.

## 7. Assembler (`venus_asm`)

To be written in Python as `minuscul_GPU/tools/venus_asm.py`.

**Syntax**
- One instruction per line: `MNEMONIC operands`, comma-separated,
  case-insensitive. Comments start with `;`.
- Registers `v0`-`v31` and `s0`-`s31`; `exec` is an alias for `s31`.
- Immediates in decimal, `0x` hex, or float (`1.5`, accepted only where a
  32-bit value is loaded with `S_MOV32`).
- Labels: `name:`. Branch operands take labels; the assembler computes
  the offsets.

**Directives**
- `.entry name`: exports the next instruction's PC as an entry point.
- `.equ NAME, value`: defines a constant.

**Output**
- A list of 32-bit words plus a symbol table of entry points, as a Python
  object and as a `.hex` file to load into instruction memory.
- A disassembler (words → text) ships in the same module, for debugging
  and for round-trip tests.

**Errors**
- Unknown mnemonics, out-of-range immediates and offsets, an SGPR used
  where a VGPR is required, and unbalanced macros are all reported with
  their line numbers.

**Macros** (they expand into ordinary instructions; each needs the scratch
registers it is given):

| Macro | Expansion |
|---|---|
| `S_MOV32 sD, imm32` (also a float literal) | `S_MOVHI sD, hi16` ; `S_ORK sD, lo16` |
| `IF sCond, sSave` | `S_SAVEEXEC_AND sSave, sCond` ; `S_CBRANCH_EXECZ <else>` |
| `ELSE` | `<else>: S_XOR exec, sSave, exec` ; `S_CBRANCH_EXECZ <endif>` |
| `ENDIF` | `<endif>: S_MOV exec, sSave` (`<else>` is placed here if there was no `ELSE`) |
| `INTERP vD, k, vTmp` | `V_LOAD_BCAST vTmp, s0, k` ; `V_MUL_F32 vD, vTmp, v2` ; `V_LOAD_BCAST vTmp, s1, k` ; `V_MAC_F32 vD, vTmp, v3` ; `V_LOAD_BCAST vTmp, s2, k` ; `V_MAC_F32 vD, vTmp, v4` |
| `PACK_RGBA vD, vR, vG, vB, vA, vTmp` | `V_CVT_UNORM8_F32 vD, vA`, then for each of R, G, B: `V_CVT_UNORM8_F32 vTmp, vX` ; `V_LSHL_OR vD, vD, vTmp, 8` |
| `S_LOADC sD, sBase, off, vTmp` | `V_LOAD_BCAST vTmp, sBase, off` ; `V_READFIRSTLANE sD, vTmp` |

`IF` blocks nest; each level needs its own `sSave` register. `INTERP`
relies on the pixel-group convention (`s0`-`s2`, `v2`-`v4`), so those
registers must not be overwritten before interpolation is done. In
`PACK_RGBA`, `vD` must not be `vR`, `vG` or `vB`.

## 8. Examples

**Gouraud pixel shader** (vertex record = position, then r, g, b in words
4-6):

```asm
.entry ps_gouraud
    INTERP    v5, 4, v12          ; red
    INTERP    v6, 5, v12          ; green
    INTERP    v7, 6, v12          ; blue
    S_MOV32   s12, 1.0
    V_MOV     v8, s12             ; alpha = 1.0 (SGPR broadcast)
    PACK_RGBA v9, v5, v6, v7, v8, v12
    V_EXPORT_COLOR v9
    S_ENDPGM
```

**Discard with an `IF`** (drop pixels whose red is below 0.5):

An SGPR can only be operand A, so the compare is written as "0.5 > red":

```asm
    S_MOV32   s13, 0.5
    V_CMP_GT_F32 s14, s13, v5     ; s14 = lanes where 0.5 > red
    S_ANDN2   exec, exec, s14     ; turn those lanes off = discard
```

**Compute: vector add** (`USER0` = A, `USER1` = B, `USER2` = C, so they
arrive in `s4`, `s5`, `s6`):

```asm
.entry vadd
    V_LOAD    v1, v0, s4, 0       ; a[i]   (v0 = global thread id)
    V_LOAD    v2, v0, s5, 0       ; b[i]
    V_ADD_F32 v3, v1, v2
    V_STORE   v3, v0, s6, 0       ; c[i] = a[i] + b[i]
    S_ENDPGM
```

Because thread ids are consecutive, each load and store above touches at
most two 512-bit lines.

## 9. Not in v2 (possible later additions)

- Scalar memory loads (`S_LOAD`, D6 option C). Loading a 4×4 matrix
  currently costs 32 instructions with `S_LOADC`.
- Texture sampling instructions and texture units.
- Atomics, workgroups, `S_BARRIER` and a scratchpad (D9 option D).
- Hardware attribute interpolation (today it's the `INTERP` macro).
- Packed FP16.
- Proper near-plane clipping (D14).
- Sub-pixel precision in the primitive unit (vertices are rounded to whole
  pixels).

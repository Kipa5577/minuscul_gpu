# shader_core

The programmable VENUS v2 core: holds `GROUP_SLOTS` thread groups of 16
threads, each with its own registers, and issues one instruction per cycle
from a ready group.

**Implements:** D2 (unified programmable cores), D4 (16 lanes), D5
(`EXEC` mask), D6 (scalar unit), D7/D13 (VENUS v2, spec in
[../README_ISA.md](../README_ISA.md)), D8 (round-robin group slots), D10
(FP32).

```
            ┌──────────── shader core ─────────────┐
 launch ───►│ slot table (GROUP_SLOTS)             │
 (dispatch) │  per slot: PC, SCC, s0-s31, v0-v31   │
            │ scheduler: round-robin, ready groups │
            │ IMEM ─► decode ─┬─► scalar ALU        │
            │                 ├─► vector ALU ×16    │──► VMEM port (memory)
            │                 └─► branch / control  │──► export port (output stage)
            └──────────────────────────────────────┘
```

## M5: shader core - done

| File | Content |
|---|---|
| `isa.py` | instruction table and bit-level encode/decode (shared with the assembler) |
| `alu.py` | semantics of every operation on 32-bit patterns (FP32 rules, integer ops) |
| `venus_ref.py` | reference interpreter of one group (`GroupState`, `execute`, `run`) |
| `core.py` | py4hw `ShaderCore`, `LaunchInterface` |

Tests: `tb/shaderCoreTest/` (ALU semantics, reference model, and the
hardware core against the reference on random programs with 1 and 4
slots).

Implementation notes:
- **Shared semantics.** The core executes each instruction through the
  same functions as the reference model (`venus_ref.execute`, `alu.py`).
  The core-vs-reference tests therefore check the control path (slots,
  scheduling, memory and export ports), and `test_alu.py` checks the
  arithmetic on its own.
- **Launch interface.** It carries `pixel` (pixel groups must export),
  `seq`, all 32 SGPRs, v0-v4, and side data (tile position, per-lane z)
  that is returned with the export. The export port connects straight to
  the output stage.
- **Groups that never export.** A pixel group that ends without
  exporting sends an empty packet (mask 0), so the output stage's in-order
  sequence never has a gap.
- **Memory port.** For now each active lane costs one 3-cycle access on
  a plain 32-bit `Memory`; coalescing into 512-bit lines comes with M7.
  Measured: a load followed by 24 ALU instructions, ×2, over 4 groups
  takes 581 cycles with 1 slot and 416 with 4.
- **`V_RSQ_F32`** is rounded from a double-precision result, not exactly
  (README_ISA.md §2 updated). The other operations are exactly rounded.


Tasks, in order:
- [x] **Reference model** (`venus_ref.py`): a plain-Python interpreter of
      one group, written straight from README_ISA.md. It is the golden
      model for every hardware test, including FP32 rules (round to nearest
      even, flush denormals to zero, `MIN`/`MAX` with NaN) via numpy
      `float32`.
- [x] **Instruction memory** (`IMEM_WORDS`, loaded from the assembler's
      output) and the **decoder** (7 formats, illegal-opcode error)
- [x] **Register files:** per slot, `s0`-`s30` + `exec` (`s31`, 16 bits
      used), `v0`-`v31` × 16 lanes, `SCC`, `PC`
- [x] **Scalar unit:** SOP, SOPK, SOPP
- [x] **Vector unit:** VOP, VOPS (16 lanes, `exec`-masked writes)
- [x] **VMEM:** address generation and a single memory port. With one
      client it talks to a plain `Memory`; the coalescer and arbiter
      arrive in M7
- [x] **Export port:** `V_EXPORT_COLOR` → packet (colours + `exec`) to the
      output stage
- [x] **Scheduler:** `GROUP_SLOTS` slots, round-robin over ready slots; a
      slot waiting for memory or export is not ready; `S_ENDPGM` frees it
- [x] **Launch port:** accepts a group with initial register values
      (driven by the dispatcher in M6, and by tests before that)

**Done when:** for every instruction, and for the README_ISA §8 example
programs, the hardware core's registers and memory match the reference
model after every instruction, with 1 and with 4 slots.

*As tested:* the comparison is made at the end of each program, not after
every instruction. Every random program ends by dumping v0-v15 and s0-s11
to memory, so final registers and memory are compared, over 8 seeds × 3
programs × 6 groups, with 1 and 4 slots.

**Depends on:** M4 (assembler) to write the test programs.

**Later:** scoreboard and multi-cycle ALU (D8 option D), `S_LOAD` (D6
option C), texture instructions.

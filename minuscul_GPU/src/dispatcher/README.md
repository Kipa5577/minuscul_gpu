# dispatcher

Turns work items into thread groups inside a slice's shader core. It
allocates a free slot and preloads the group's registers following the
launch convention in [README_ISA.md §6.3](../README_ISA.md). For pixel
work it also keeps each group's side data (tile position, per-lane depth,
sequence number) for the output stage.

**Implements:** D6 (constants preloaded into SGPRs), D8 (a group launches
only when a slot is free), D13/D15 (launch convention, vertex record
addresses, perspective-correct barycentrics).

## M6: pixel dispatch (end of build step 3) - done

Implemented in `dispatcher.py` (`PixelDispatcher`, `build_pixel_launch`,
`DrawState`). A slice runs it with `Slice(..., shading='shader',
program=..., draw=..., data_mem=...)`. Tests: `tb/dispatcherTest/`.

Notes:
- **Draw state.** Until the front end exists (M8), the draw state is a
  `DrawState` object given at construction, not wires.
- **Barycentrics** are computed in double precision and rounded once to
  FP32.
- **No slot bookkeeping.** The core's launch `ready` already means "a slot
  is free", so the dispatcher doesn't track slots, and the core carries
  the side data (tile, z, seq) to the output stage.
- **Interim memory.** `SliceArray` in shader mode gives every slice a
  private copy of the data memory until M7.
- **Measured:** the canonical scene with a flat pixel shader on the core
  takes 21,757 cycles (flat-colour unit: 21,944). Gouraud shading is
  memory-bound: every `V_LOAD_BCAST` in `INTERP` still makes 16 single-word
  accesses until M7 coalesces them.


Tasks:
- [x] take fragment groups from the rasteriser (M1 handshake); stall when
      no slot is free
- [x] preload: `exec` = coverage; `v0`/`v1` = pixel x/y; `s3` = flat colour;
      `s4`-`s11` = draw user values; PC = `PS_PC`
- [x] **barycentrics:** λk = Wk / Area as FP32, perspective-corrected with
      the line's 1/w words (all-zero bits = 1.0), into `v2`-`v4`
- [x] **vertex record addresses:** `s0`-`s2` = `VOUT_BASE + index × VOUT_STRIDE`
      (0 when the line has no indices)
- [x] give each group a sequence number; hand `seq`, tile position and
      `z[16]` to the output stage with that group's export
- [x] slice mode switch: flat-colour unit (M3) or dispatcher + shader core

**Done when:** a slice running the README_ISA §8 Gouraud pixel shader
renders a test triangle that matches a Python reference (colour
interpolated per pixel), and the flat-colour mode still matches today's
image.

**Depends on:** M3, M5.

## M8: compute dispatch

- [ ] accept compute groups from the front end: `exec` = lanes with
      thread id < `THREAD_COUNT`; `v0` = global thread id;
      `s0`/`s1` = group id / thread count; `s4`-`s11` = user values

## M9: vertex dispatch

- [ ] like compute, with `v0` = vertex index, `s1` = `VERTEX_COUNT`,
      `s2`/`s3` = `VOUT_BASE`/`VOUT_STRIDE`

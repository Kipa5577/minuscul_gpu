# slice

Assembles a **slice** (D3): rasteriser core → shading → output stage, with
its private triangle memory and framebuffer strip. Also builds the
**slice array**: `NUM_SLICES` slices fed by the binner, which replaces
`MultiCoreRenderModule`.

**Implements:** D3 (slices), D1 build-order step 2.

```
triangle lines ─► Binner ─► ┌ slice 0: rasteriser ─► shading ─► output stage ─► strip 0
                            ├ slice 1: …
                            └ slice N-1: …
```

## M3: slice integration (end of build step 2) - done

Implemented in `slice.py` (`Slice`, `SliceArray`) and `flat_colour.py`
(`FlatColourUnit`). Tests: `tb/sliceTest/`. Result: canonical scene 21,944
cycles on one slice (legacy 44,085), 3,864 on 8 slices (legacy 7,478); see
RASTERISER_ARCHITECTURE.md #12.

Deviations from the plan below:
- `MultiCoreRenderModule` and `RenderModule` were **kept**, as the golden
  reference for differential tests, and because both animations draw
  `RenderController`'s internal state. Porting the animations to
  `Slice`/`SliceArray` is a follow-up task.
- Cycle counts went **down**, not up as expected: see
  RASTERISER_ARCHITECTURE.md #12.

Shading in M3 is a **flat-colour unit**: a stand-in that turns each
fragment group straight into an export packet (colour = triangle flat
colour, `seq` = arrival order). It is replaced by dispatcher + shader core
in M6, but kept as a selectable mode for tests.

Tasks:
- [x] `FlatColourUnit`: fragment group → export packet
- [x] `Slice`: rasteriser + flat-colour unit + output stage + triangle
      memory + framebuffer strip
- [x] `SliceArray(num_slices, …)`: binner + slices + all-done, with
      `resolve_framebuffer()`, the same interface as
      `MultiCoreRenderModule`
- [x] move `Binner`/`AllDone` to `binner/` (see its README)
- [x] new tests on `Slice`/`SliceArray`; legacy modules kept (see deviations)
- [x] re-measure the canonical scene and update the "Current state" table
      in `rasteriser/RASTERISER_ARCHITECTURE.md`

**Done when:** all existing tests pass, and the 1-slice and 8-slice
renders of the canonical scene are pixel-identical to today's
`RenderModule` / `MultiCoreRenderModule` output. The cycle-count change is
recorded.

**Follow-up:**
- [ ] port `animation_of_RenderModule.py` and
      `animation_of_MultiCoreRenderModule.py` to `Slice`/`SliceArray`, then
      retire the legacy modules from the design (keep them only as a
      test reference)

**Depends on:** M1, M2.

**M6 (done):** `shading='shader'` puts dispatcher + shader core in place of
the flat-colour unit; `'flat'` is still the default.

**Later:** M7
connects every slice's shader core to the shared memory arbiter. M10 adds
double-buffered strips.

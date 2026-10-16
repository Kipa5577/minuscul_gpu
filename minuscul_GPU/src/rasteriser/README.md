# rasteriser

Fixed-function front end of each slice: turns screen-space triangle lines
into covered pixels. This is the part of the GPU that already works.

**Implements:** D1 (rasteriser as the fixed-function front end), D3 (one
rasteriser per slice), D4 (one 4×4 tile = one thread group), D5
(coverage mask = initial `EXEC`).

## Existing code: `renderer.py`

| Name | Role | Future home |
|---|---|---|
| `BoundingBoxCalculator`, `EdgeFunction`, `InsideAndArea`, `PointInTriangle`, `BarycentricDepth` | combinational leaves | stays here |
| `pack_triangle_line`, `unpack_triangle_line` | 512-bit triangle line format | moved to `triangle_line.py` (re-exported); gains words 10-15 in M9 |
| `framebuffer_bank_size`, `resolve_banked_framebuffer` | framebuffer helpers | moved to `output_stage/` (re-exported here) |
| `Binner`, `AllDone` | binning pre-pass | moved to `binner/` (re-exported) |
| `RenderController`, `RenderModule`, `MultiCoreRenderModule` (legacy) | the original single-FSM renderer | kept as the **golden reference** for differential tests and for the two animations; replaced in the design by `Rasteriser` + `slice/` |
| `MatrixMultiplier4x4`, `CrossProduct`, `Vector3Norm` | standalone math blocks, never wired in | move to `legacy/` (vertex work runs on the shader cores, D11) |

Design history and measurements:
[RASTERISER_ARCHITECTURE.md](RASTERISER_ARCHITECTURE.md),
[PIPELINE_CYCLE_PROFILE.md](PIPELINE_CYCLE_PROFILE.md).

## M1: fragment output - done

Implemented in `rasteriser.py` (`Rasteriser`, `RasteriserController`) and
`fragment.py` (`FragmentGroupInterface`). Tests: `tb/rasteriserTest/`.
Result: RASTERISER_ARCHITECTURE.md #12.

**Goal:** the rasteriser stops writing colour and depth itself. Instead it
emits **fragment groups**, one per covered 4×4 tile per triangle, for the
rest of the slice to shade and write.

Fragment group (rasteriser → shading), with a valid/ready handshake:

| Field | Size | Notes |
|---|---|---|
| `tile_x`, `tile_y` | 2 × 32 bit | multiples of 4 |
| `coverage` | 16 bit | bit *i* = lane *i*'s pixel is inside the triangle (lane mapping: README_ISA §1) |
| `z[16]` | 16 × 32 bit | interpolated depth per lane, as `BarycentricDepth` computes today |
| `w0[16]`, `w1[16]`, `w2[16]`, `area` | integers | edge-function weights; the dispatcher turns them into barycentrics (M6) |
| `tri_words` | words 9-15 of the triangle line | flat colour, vertex indices, 1/w |

Tasks:
- [x] emit fragments per **aligned 4×4 tile**. The fine-tile path already
      works on 4×4 tiles. The "fill" path, which today scans rows up to 16
      wide, must walk its coarse tile as 4×4 sub-tiles instead.
- [x] remove the z-buffer, depth test and framebuffer writes from
      `RenderController` (they move to the output stage, M2)
- [x] stall when the consumer isn't ready (handshake)
- [x] keep the hierarchical tile test, adaptive tile size and 16 lanes

**Known effect:** without early depth testing, hidden pixels are shaded
too. Fine for now; hierarchical or early Z can come back later as an
optimisation.

**Done when:** a test collects all fragment groups for the existing test
scenes, and depth-tests and writes them in Python. The resulting image
matches today's `RenderModule` output pixel for pixel.

**Depends on:** nothing.

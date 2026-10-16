# primitive_unit

Fixed-function stage between the vertex pass and the binner. It turns
clip-space vertices, written by vertex shaders, into the screen-space
triangle lines the rasteriser takes.

**Implements:** D11 (vertex shaders on the cores + fixed-function
primitive unit), D14 (near-plane policy), D15 (vertex records, triangle
line words 10-15). Exact rules: [README_ISA.md §6.4-6.5](../README_ISA.md).

## M9: primitive unit (build step 5)

For each of `TRIANGLE_COUNT` triangles:
- [ ] read 3 indices from `INDEX_BUF` and the 3 positions (record words
      0-3) from `VOUT_BASE + index × VOUT_STRIDE`
- [ ] trivial reject: all 3 vertices outside the same frustum plane
- [ ] near plane (D14): drop if any vertex has `w ≤ 0` or `z < −w`
- [ ] perspective divide and viewport → integer X, Y; 24-bit Z
- [ ] guard band: drop if any |X| or |Y| > 16383
- [ ] signed area: drop zero area; cull per `FLAGS`
- [ ] write the 512-bit line (words 0-8 screen space, 9 = 0, 10-12
      indices, 13-15 1/w) to `TRI_BUF`; count the triangles written
- [ ] extend `pack_triangle_line`/`unpack_triangle_line` (in
      `rasteriser/renderer.py`) with words 10-15

**Done when:** a 3D scene from `algorithms/rasterisation.py` (camera,
perspective, rotating cube or pyramid) renders fully on-chip:
- a vertex shader transforms the vertices;
- the primitive unit sets the triangles up;
- the pixel shader shades them.

The image matches the numpy reference within rounding, and triangles
behind the camera are dropped as specified.

**Depends on:** M8 (front end, compute-style launches) and M7 (global
memory).

**Later:** proper near-plane clipping, sub-pixel precision (both listed in
README_ISA.md §9).

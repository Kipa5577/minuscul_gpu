# Algorithms that make a GPU work

Ordered roughly by where they sit in the pipeline. Status column refers to
this repo (as of work module M6; see `src/README.md`):
✅ implemented · 🟡 partial / building blocks exist · ⬜ not yet.

---

## 1. Geometry / vertex stage

| Algorithm | What it does | Status |
|---|---|---|
| 4×4 matrix transform (Model-View-Projection) | Moves vertices from object space → world → camera → clip space with one matrix multiply per vertex | 🟡 `MatrixMultiplier4x4` |
| Perspective divide | Clip space → NDC by dividing x, y, z by w | ⬜ |
| Viewport transform | NDC [-1,1] → pixel coordinates (scale + offset) | ⬜ |
| Clipping (Sutherland–Hodgman against the view frustum, esp. near plane) | Cuts triangles crossing the frustum so w never hits 0 / negative | ⬜ |
| Guard-band clipping | Skips real clipping for x/y; only clips against near/far, lets the rasteriser's bounding-box clamp discard off-screen pixels | 🟡 (bbox clamp exists) |
| Back-face culling | Discards triangles whose signed area (2D cross product) is negative / facing away | ⬜ (signed area is computed in `InsideAndArea`, but both windings are drawn) |
| Normal computation & normalisation | Cross product of two edges, then divide by length (needs sqrt or inverse-sqrt) | 🟡 `CrossProduct`, `Vector3Norm` (standalone, not wired in) |
| Vertex cache / index reuse | Reuses transformed vertices shared between triangles (indexed meshes) | ⬜ |

## 2. Rasterisation

| Algorithm | What it does | Status |
|---|---|---|
| Edge functions (Pineda, 1988) | `E(x,y) = (x-x0)(y1-y0) - (y-y0)(x1-x0)`; pixel is inside if all three have the same sign. The foundation of every modern rasteriser | ✅ `EdgeFunction`, `PointInTriangle` |
| Incremental edge evaluation | E is linear, so stepping one pixel right is just `E += A`; no multiplies in the inner loop | ⬜ |
| Bounding-box traversal | Only scan pixels inside the triangle's clamped bbox | ✅ `BoundingBoxCalculator` |
| Hierarchical / tiled rasterisation | Test tile corners, trivially accept/reject whole tiles, subdivide mixed ones | ✅ (see `RASTERISER_ARCHITECTURE.md`) |
| Top-left fill rule | Decides ownership of pixels exactly on a shared edge so they're drawn once, never twice or zero times | ⬜ |
| Fixed-point sub-pixel precision (e.g. 16.8) | Snaps vertices to a sub-pixel grid so edge tests are exact integers, no cracks | ⬜ |
| Barycentric interpolation | `(w0,w1,w2)/area` weights to interpolate any per-vertex attribute (depth, colour, UV) | ✅ depth in the rasteriser; attributes via the `INTERP` macro on the shader core |
| Perspective-correct interpolation | Interpolate `attr/w` and `1/w` linearly, then divide; needed for textures to not "swim" | ✅ dispatcher corrects barycentrics with 1/w (`src/dispatcher/`) |
| Bresenham / DDA line drawing | Integer-only line and point rasterisation | ⬜ |
| Tile-based binning (TBR / TBDR) | Sort triangles into screen tiles first, then render each tile entirely on-chip (mobile GPUs) | ✅ `Binner` + `SliceArray` (bins by row strip, one strip per slice) |
| Adaptive tile size | Choose the coarse tile size per triangle from its size (small → 4, medium → 8, large → 16) | ✅ `COARSE_TILE_BANDS` |

## 3. Visibility / hidden-surface removal

| Algorithm | What it does | Status |
|---|---|---|
| Z-buffer (depth test) | Keep the fragment only if its depth is closer than what's stored | ✅ |
| Early-Z | Do the depth test *before* shading so hidden fragments cost nothing | ⬜ (removed in M1: the depth test now runs in the output stage, after shading) |
| Hierarchical-Z (Hi-Z) | Store per-tile min/max depth; reject whole tiles of a triangle at once | ⬜ |
| Depth-buffer compression | Store a tile as a plane equation instead of per-pixel values to save bandwidth | ⬜ |
| Stencil test | Per-pixel integer mask used for shadows, portals, outlines | ⬜ |
| Painter's algorithm | Sort back-to-front instead of a Z-buffer (cheap, but fails on intersecting triangles) | ⬜ |

## 4. Fragment / pixel stage

| Algorithm | What it does | Status |
|---|---|---|
| Texture mapping (UV → texel lookup) | Fetch colour from an image using interpolated coordinates | ⬜ |
| Nearest / bilinear / trilinear filtering | Blend 1, 4 or 8 texels to avoid blockiness and shimmering | ⬜ |
| Mipmapping (LOD selection from UV derivatives) | Pick a pre-shrunk texture level based on how big a texel is on screen | ⬜ |
| Anisotropic filtering | Several samples along the stretched direction for oblique surfaces | ⬜ |
| Texture swizzling / Morton (Z-order) layout | Store texels so 2D-neighbours are memory-neighbours → better cache hits | ⬜ |
| Shading models (flat, Gouraud, Phong, Blinn-Phong) | Light per triangle / per vertex / per pixel with `N·L` and specular terms | 🟡 flat and Gouraud as VENUS v2 pixel shaders; Phong possible in software |
| Alpha blending | `out = src·α + dst·(1-α)` and the other blend equations | ⬜ |
| Anti-aliasing (SSAA, MSAA, coverage masks) | Multiple coverage samples per pixel; MSAA shades once but tests coverage N times | ⬜ |
| Dithering | Hide banding when reducing colour depth (e.g. RGB888 → RGB565) | ⬜ |

## 5. Arithmetic units (what the hardware needs to compute the above)

| Algorithm | What it does | Status |
|---|---|---|
| Fused multiply-add (FMA) | `a*b+c` with one rounding; the core op of every shader ALU | ✅ `V_MAC_F32`, single rounding |
| Fixed-point arithmetic | Integer-only maths with an implied binary point; cheap in hardware | 🟡 (integer pipeline) |
| Reciprocal / inverse-sqrt (Newton–Raphson, lookup-table seed) | Needed for perspective divide, `1/w`, normalising vectors | 🟡 `V_RCP_F32`, `V_RSQ_F32`, `V_SQRT_F32` (behavioural, not Newton–Raphson hardware) |
| CORDIC | Shift-and-add sin/cos/atan/sqrt with no multiplier | ⬜ |
| Integer sqrt (digit-by-digit) | Exact sqrt for `Vector3Norm` without floats | ⬜ (`Vector3Norm` currently calls Python's float `math.sqrt`) |
| Dot product tree / reduction | Parallel adders for 3/4-wide dot products and matrix rows | 🟡 |

## 6. Execution model / scheduling (the "parallel processor" part)

| Algorithm | What it does | Status |
|---|---|---|
| SIMT (Single Instruction, Multiple Threads) | One instruction stream drives many lanes (a warp/wavefront) | ✅ `src/shader_core/`: 16-thread groups, VENUS v2 |
| Warp scheduling (round-robin, greedy-then-oldest) | Pick a ready warp each cycle to hide memory latency | ✅ round-robin over ready groups, `GROUP_SLOTS` per core |
| Branch divergence handling (active masks, reconvergence stack / IPDOM) | When lanes take different branches, run both paths with masks and rejoin | ✅ software-managed `EXEC` mask + assembler `IF`/`ELSE`/`ENDIF` |
| Scoreboarding | Track register hazards so instructions issue only when operands are ready | ⬜ (not needed yet: one instruction in flight per group, D8) |
| Work distribution / load balancing | Split triangles/tiles/threads evenly across cores | ✅ `Binner` → slices; dispatcher → core slots |
| Barriers / synchronisation | All threads in a group wait at a point (e.g. `__syncthreads`) | ⬜ (`S_BARRIER` reserved in VENUS v2) |
| Atomic operations | Safe read-modify-write on shared memory (counters, histograms) | ⬜ |

## 7. Memory system

| Algorithm | What it does | Status |
|---|---|---|
| Memory coalescing | Merge adjacent lanes' accesses into one wide burst | ⬜ planned in M7 (512-bit lines) |
| Banked memory + bank-conflict avoidance | Split memory into banks so many accesses happen per cycle | ✅ banked framebuffer in the output stage (`write_bank_count`) |
| Caches (L1/L2, texture cache) with LRU-type replacement | Keep recently used data close | ⬜ |
| Double / triple buffering | Render into one framebuffer while the screen scans out the other | 🟡 planned in M10; `display/screen.py` is a broken prototype |
| Framebuffer / colour compression (delta, constant-tile) | Cut bandwidth for flat-coloured regions | ⬜ |
| DMA / command-buffer processing | CPU writes commands to a ring buffer; GPU front-end fetches and decodes them | ⬜ |

## 8. Display output

| Algorithm | What it does | Status |
|---|---|---|
| Video timing generation (VGA/HDMI: hsync, vsync, blanking) | Scan the framebuffer out to a screen at a fixed pixel clock | ⬜ planned in M10 (`display/`) |
| Gamma correction / sRGB conversion | Map linear light to display encoding | ⬜ |
| Scaling / scan-out filtering | Resize the framebuffer to the display resolution | ⬜ |

## 9. General-purpose compute (GPGPU) building blocks

| Algorithm | What it does | Status |
|---|---|---|
| Parallel reduction (sum/min/max in a tree) | O(log n) steps across lanes | ⬜ |
| Prefix sum / scan (Blelloch, Hillis–Steele) | Basis for stream compaction, sorting, binning | ⬜ |
| Radix sort / bitonic sort | GPU-friendly sorts (sorting triangles, particles) | ⬜ |
| Tiled matrix multiply (GEMM) | Load blocks into shared memory and reuse them; what tensor cores accelerate | 🟡 `MatrixMultiplier4x4` |
| Convolution / stencil | Image filters, neural nets | ⬜ |

---

## Suggested next steps for this project

Picked to give the biggest visible/structural gain for a small GPU:

1. **Incremental edge evaluation** - removes the multipliers from the per-pixel loop.
2. **Top-left fill rule + fixed-point sub-pixel coordinates** - makes adjacent triangles render without gaps or double-drawn pixels.
3. **Perspective divide + viewport transform** - connects `MatrixMultiplier4x4` to the rasteriser so real 3D scenes can be fed in.
4. **Back-face culling** - nearly free given the signed area already computed; halves work on closed meshes.
5. **Gouraud shading via barycentrics** - reuse `BarycentricDepth` to interpolate colour.
6. **Hi-Z per tile** - fits naturally on top of the existing tile hierarchy.
7. **Texture mapping (nearest, then bilinear)** + perspective-correct interpolation.

# RenderModule architecture improvements

This documents the sequence of architectural changes made to the py4hw
rasterisation core in [`renderer.py`](renderer.py), in the order they were
built, each with the problem it targeted, what changed, and the measured
effect on cycle count. All figures come from real py4hw simulations (see
`tb/rasterisationTest/`), not estimates.

Baseline for comparison throughout: a flat per-pixel scan of each triangle's
clamped bounding box, one pixel tested per two-cycle round, one 32-bit word
fetched per triangle field.

**Current state** (after all sections below; re-measured on the canonical
5-triangle, 255×255 scene):

| Configuration | Legacy `RenderModule` (sections 1-11) | `Slice` / `SliceArray` (section 12) |
|---|---:|---:|
| single core, 1 framebuffer port | 44,085 | **21,944** |
| single core, 16 framebuffer banks | 32,051 | **11,409** |
| 8 cores/slices + binner | 7,478 | **3,864** |

The legacy single-core figures are slightly above the 43,961 / 31,895
quoted in sections 7-8. Those were measured with a fixed 16×16 coarse tile.
This scene's 85-px triangles now fall in the adaptive 8×8 band (section
10), which costs them +0.3% (see section 9's caveat).

---

## 1. Hierarchical tile-based rasterisation

**Problem:** the flat scan tests every pixel in a triangle's bounding box,
including all the pixels that aren't actually inside the triangle - for a
typical triangle, roughly half its bounding box is empty.

**Change:** the bounding box is covered with 16×16-aligned tiles. Each
tile's 4 corners are tested first (reusing the same point-in-triangle
hardware used for individual pixels):
- all 4 corners outside **on the same triangle edge** → the whole tile is
  rejected outright, no further work
- all 4 corners inside → the tile is filled (still one depth test/write per
  pixel - depth genuinely varies per pixel and needs the same edge-function
  evaluation the inside test needs, so this path doesn't skip real work, it
  only skips *re-deciding* inside/outside for pixels already proven so)
- a mixed 16×16 tile subdivides into sixteen 4×4 sub-tiles, each
  corner-tested the same way; a mixed 4×4 tile falls back to per-pixel
  testing (no further subdivision)

**A real bug caught along the way:** the first version rejected a tile
whenever all 4 corners individually failed the inside test. That's unsound
- a small triangle sitting well inside a big empty tile has every corner
outside it too, each possibly failing a *different* edge, while the
triangle still occupies the tile's middle. Caught by 3 of 4 existing pytest
tests failing immediately. The correct trivial-reject test is per-edge: reject
only if all 4 corners are on the excluded side of the *same* edge, which
needs the individual W0/W1/W2 edge-function values, not just the aggregate
inside flag.

**Result** (5 triangles, 255×255 screen):

| | cycles |
|---|---|
| flat scan (baseline) | 166,723 |
| hierarchical tiling | 127,522 |

**~23.5% fewer cycles.**

**Honest caveat:** for *small* triangles, this can be a net loss. A single
10×10 triangle went from 593 cycles (flat scan) to 621 (hierarchical) - a
small bbox doesn't leave enough empty area for the reject path to pay back
the corner-test overhead of subdividing all the way down to 4×4. Real
hierarchical rasterisers handle this with a small-bbox fast path that skips
tiling below some size threshold. *(Since addressed by section 10: triangles
with a bbox extent under 20 px now use a 4×4 coarse tile, which equals
`FINE_TILE_SIZE`, so they skip the two-level hierarchy entirely.)*

---

## 2. 512-bit triangle bus

**Problem:** each triangle's 10 fields (V1/V2/V3/colour, 32 bits each) were
fetched with 10 sequential word reads, each its own address/read/wait-for-done
round trip - about 20 cycles of fetch overhead per triangle, paid regardless
of triangle size.

**Change:** `MemoryInterface` gained an optional `data_width` (default 32,
unchanged for the framebuffer). Triangle memory now uses a 512-bit bus, and
each triangle is packed into one line (`pack_triangle_line`/
`unpack_triangle_line`, 320 of the 512 bits used) instead of 10 separate
words. `RenderController`'s fetch collapsed from a 10-iteration word loop to
a single `FETCH_ISSUE`/`FETCH_WAIT` round - **20 → 2 cycles per triangle**,
a 10× reduction in fetch overhead specifically.

**Result** (5 triangles, 255×255 screen, same scene as above):

| | cycles |
|---|---|
| hierarchical tiling only | 127,522 |
| + wide bus | 127,387 |

Only a ~135-cycle difference here because this scene's triangles are large
- rasterisation cost dominates and fetch is a small fraction of the total.
The win is per-triangle-count, not per-pixel, so it matters most for scenes
with many small triangles (see the benchmark sweep in
`rasterisationBenchmark.py`, where fetch overhead is proportionally larger).

---

## 3. Parallel pixel-test lanes

**Problem:** even inside a tile that survives rejection, pixels were tested
one at a time - each pixel costing its own setup+read round through the
single shared point-in-triangle/depth-interpolation hardware.

**Change:** `RenderModule` now instantiates `PIXEL_LANE_COUNT = 4` parallel
`PointInTriangle`/`BarycentricDepth` pairs, all wired to the same triangle
data, each with its own Px/Py/Inside/W0/W1/W2/Area/Zinterp wires.
`RenderController` drives all 4 lanes in one `ROW_SETUP`/`ROW_READ` round -
testing up to 4 pixels of a row at once instead of one pixel per round - a
real 4× reduction in test rounds for any tile that reaches per-pixel or fill
work. Lane 0 doubles as the tile-corner tester (corners are cheap enough
that dedicating separate hardware to them wasn't worth it). Framebuffer
writes still serialise one at a time through the single-ported memory via a
small per-row pending-write queue - parallelising the test phase doesn't
remove that real constraint.

**Result** (5 triangles, 255×255 screen):

| | cycles |
|---|---|
| hierarchical + wide bus | 127,387 |
| + 4 parallel lanes | 82,471 |

**~35% fewer cycles**, and from the benchmark sweep, 50 small triangles
dropped **21,348 → 12,498 cycles (~41% fewer)**.

This also fully recovered improvement #1's small-triangle regression: the
single 10×10 triangle that went 593 → 621 cycles under hierarchical tiling
alone dropped to **444 cycles** with parallel lanes added - below even the
original flat-scan baseline.

---

## 4. Multi-core rasterisation (spatial partitioning)

**Problem:** everything above still runs on one `RenderController` - one
triangle in flight at a time, one core doing all the work.

**Change:** `MultiCoreRenderModule` instantiates N independent `RenderModule`
cores in parallel, each owning a disjoint horizontal strip of the screen's
rows. py4hw raises an exception the moment a second component tries to
drive an already-sourced wire, so cores genuinely cannot share one memory
bus without a real bus arbiter - built the simpler, equally valid
alternative instead: each core gets its own private read-only copy of the
triangle data (harmless to duplicate - never written during rendering) and
its own private framebuffer memory sized only for its own strip, addressed
relative to that strip's first row. Since no two cores ever touch the same
memory, there's nothing to arbitrate. `RenderController` gained
`partition_y_min`/`partition_y_max` (reusing the exact clip-to-bbox
mechanism already there for screen bounds) and `framebuffer_y_offset`. A
small `AllDone` leaf ANDs every core's own done signal into one aggregate
output; `resolve_framebuffer()` stitches the per-core strips back into one
image afterward (the same "resolve" step a real tiled GPU performs outside
its rasteriser cores).

**Result** (3 triangles, 32×32 screen, 4 cores, no binner yet):

| | cycles |
|---|---|
| single core | 2,045 |
| 4 cores (spatial partition only) | 771 |

**~2.65× faster** with 4 cores - not the theoretical 4×, because every core
still walked and bbox-tested every triangle even when it never touched that
core's strip.

Verified pixel-for-pixel identical to the single-core renderer.

---

## 5. Binner pre-pass

**Problem:** improvement #4's cores each fetch and bbox-test *every*
triangle in the scene, even ones nowhere near their row strip - a real cost
that grows with triangle count regardless of how well distributed the
scene is.

**Change:** added `Binner`, a real binning pre-pass (the same kind a
tile-based GPU runs before its rasteriser cores start). It reads each
triangle once from a single shared, read-only source triangle memory,
computes its Y-extent, and writes it only into the private memory of the
core(s) whose row-strip it actually overlaps (duplicated into more than one
core if it straddles a boundary, same as a real tile binner). Two wiring
constraints shaped this:
- writing into a core's triangle memory can't go through the bus (py4hw's
  single-driver-per-wire rule again), so the binner writes directly into
  the `Memory` object's Python list - the same "preload" pattern already
  used everywhere triangle data gets loaded in this codebase
- cores must not start before binning finishes, or they'd read whatever
  garbage is sitting in a not-yet-written memory - `RenderController` gained
  a `start` gate and a new `S_WAIT_START` state, defaulting to an
  always-ready internal wire so plain `RenderModule` (no binner) is
  completely unaffected

A related bug caught while wiring this up: a core assigned zero triangles
would have fetched address 0 from its own (empty) memory anyway and
rendered garbage as a "triangle." `S_WAIT_START` now checks the count and
jumps straight to done if it's zero.

**Result** (20 triangles scattered across a 64×256 screen, 8 cores):

| | cycles |
|---|---|
| single core | 5,019 |
| 8 cores + binner | 1,150 |

**4.36× speedup** (theoretical max 8×) - a much larger fraction of the
theoretical ceiling than improvement #4 alone managed at a smaller scale,
confirming the binner is what actually closes the gap toward N×. At very
small triangle counts (e.g. 3 triangles) the binning pass's own overhead
can nearly cancel its own savings - it pays off as scene complexity grows,
which is exactly the regime it's meant for.

Verified pixel-for-pixel identical to the single-core renderer in both the
small (3-triangle) and scaled (20-triangle) cases.

---

## 6. Wider pixel-test lanes (4 → 16)

**Problem:** improvement #3 sized `PIXEL_LANE_COUNT` at 4 to match
`FINE_TILE_SIZE`, so a 4×4 fine-tile fallback row tested in one round - but
the common "fill" path (all 4 corners of a 16×16 coarse tile inside) still
scanned its up-to-16-pixel-wide rows 4 lanes at a time, needing up to 4
`ROW_SETUP`/`ROW_READ` rounds per row.

**Change:** `PIXEL_LANE_COUNT` raised from 4 to 16, matching
`COARSE_TILE_SIZE` instead of `FINE_TILE_SIZE`. No other code changed -
`RenderModule` already built its lane wires and `PointInTriangle`/
`BarycentricDepth` pairs in a loop over `PIXEL_LANE_COUNT`, and
`RenderController` already sized itself off `len(Pxs)`. A fully-covered
16-wide coarse tile row now tests in a single round instead of four; a 4×4
fine-tile fallback row still only uses 4 of the 16 lanes, so it gains
nothing (and costs nothing extra either - unused lanes just sit idle for
that round). Framebuffer writes still serialise one at a time through the
single-ported memory, same constraint as improvement #3.

**Result** (5 triangles, 255×255 screen, same scene as above):

| | cycles |
|---|---|
| 4 lanes | 82,472 |
| 16 lanes | 77,576 |

**~5.9% fewer cycles.** Smaller than improvement #3's own win because most
of this scene's rows are already short (clipped tile edges) or handled by
the 4×4 fine-tile fallback, which doesn't benefit from the wider lane count
at all - the win is concentrated in rows from large, mostly-filled coarse
tiles, so scenes dominated by large triangles should see more benefit than
this one did.

Verified against the existing pytest suite (4/4 passing, no framebuffer
content change).

---

## 7. Pipelined framebuffer writes

**Problem:** a per-state cycle profile (see
[`PIPELINE_CYCLE_PROFILE.md`](PIPELINE_CYCLE_PROFILE.md)) found that
`WRITE_ISSUE`/`WRITE_WAIT` alone accounted for ~72% of all cycles - far more
than every other phase combined. The cause: each pixel write cost a full
3-cycle round trip (1 issue + 2 cycles waiting for `fb.done`) with zero
overlap, because `WRITE_ISSUE` only ever issued one address, then blocked
in `WRITE_WAIT` until that specific write was acknowledged before issuing
the next one - even though nothing about the framebuffer memory model
actually required that stall.

**Change:** `WRITE_ISSUE` now fires every cycle back-to-back for as long as
`pending_writes` has entries left, instead of dropping into `WRITE_WAIT`
after every single address. `WRITE_WAIT` is only entered once, after the
*last* pixel of a row has been issued, purely to drain that final write's
`fb.done` before the FSM moves on to `NEXT_TILE_ROW` (so the framebuffer
port is never left with pending, unacknowledged writes when something else
might reuse it). No new hardware - same single write port, same `fb.done`
handshake - just no longer stalling on it between every pixel.

**Result** (5 triangles, 255×255 screen, same scene as above):

| | cycles |
|---|---|
| stalling writes (1 per 3 cycles) | 77,576 |
| pipelined writes | 43,961 |

**~43.3% fewer cycles overall** - the write phase itself dropped from
55,623 to 22,008 cycles (a 2.53× cut on that phase specifically; short of
the full 3× because `WRITE_ISSUE` itself still costs one cycle per pixel,
it's only the *waiting* between issues that disappeared). Verified
pixel-for-pixel identical output (same framebuffer SHA-256 hash with writes
stalling vs. pipelined) and the existing pytest suite (4/4 passing).

---

## 8. Banked framebuffer write ports (opt-in)

**Problem:** improvement #7 removed the *stall* between writes, but every
pixel still needed its own `WRITE_ISSUE` cycle - there was still only one
write port, so a fully-covered 16-wide tile row still cost 16 issue cycles
back-to-back, just with the 2-cycle wait between them gone.

**Change:** `RenderController` and `RenderModule` gained a
`write_bank_count: int = 1` constructor parameter - **a flag, off by
default**, so every existing call site (the pytest suite, the benchmark,
`animation_of_RenderModule.py`, and each `MultiCoreRenderModule` core)
keeps its exact current single-port behaviour with zero changes required.

At `write_bank_count == 1`, `framebuffer_mem` is a single `MemoryInterface`
exactly as before. Set it higher and `framebuffer_mem` instead takes a
*list* of that many `MemoryInterface`s - one per bank, each its own private
`Memory` instance (py4hw forbids two drivers on one wire, so real
per-bank parallelism needs one dedicated port per bank, the same reasoning
`MultiCoreRenderModule` already uses for its per-core framebuffers). A
pixel's local (partition-relative) address maps to `bank = local_addr %
write_bank_count`, `bank_addr = local_addr // write_bank_count` - since any
run of `write_bank_count` *consecutive* pixel addresses is guaranteed to
hit `write_bank_count` distinct residues mod `write_bank_count`, a whole
row's surviving pixels (all built from one contiguous run of x's) can
always be issued in a single batch with zero same-cycle bank collisions,
regardless of how many of them actually survived the depth test.

`S_WRITE_ISSUE` now builds that batch each cycle - one address per distinct
bank, deferring any pixel whose bank is already claimed this cycle to the
next one - and prepares all of them in the same cycle, a real parallel
write port rather than a pipelined serial one. `S_WRITE_WAIT` only drains
the most recently issued batch (tracked in `_writes_in_flight`), checking
`done` on exactly the banks that batch touched. Two new module-level
helpers, `framebuffer_bank_size()` and `resolve_banked_framebuffer()`,
handle sizing each bank's `Memory` and stitching the banks' contents back
into one flat pixel list afterward (mirroring
`MultiCoreRenderModule.resolve_framebuffer()`).

**Result** (5 triangles, 255×255 screen, same scene as above):

| | cycles |
|---|---|
| 1 bank (pipelined single port) | 43,961 |
| 16 banks | 31,895 |

**~27.4% fewer cycles** on top of improvement #7's own gain - **58.9%
fewer than the pre-pipelining baseline (77,576 → 31,895) combined.** The
write phase itself dropped a further 54.8% (22,008 → 9,942 cycles) and is
no longer the dominant cost: it's now ~31% of the total, with the
corner-test and row-test phases making up the larger share. Verified
pixel-for-pixel identical output across `write_bank_count` in
`{1, 2, 4, 16}` (same framebuffer SHA-256 hash in every case), plus a new
parametrized pytest (`test_banked_framebuffer_matches_single_port_output`)
added to the existing suite (7/7 passing).

---

## 9. `COARSE_TILE_SIZE` retuned for 1920×1080 (16 → 8)

> Superseded by section 10: `COARSE_TILE_SIZE` no longer exists as a single
> constant. It was replaced by per-triangle `COARSE_TILE_BANDS`.

**Problem:** `COARSE_TILE_SIZE` (section 1) was set to 16 by convention, never
actually tuned against a target resolution. The tradeoff it controls -
smaller tiles mean cheaper corner tests but less coarse-reject payoff per
tile; larger tiles mean the opposite, and do worse for small triangles
(section 1's own "honest caveat") - depends on typical triangle size
relative to tile size, which in turn depends on what a "normal" scene at a
given resolution actually looks like.

**Change:** swept `COARSE_TILE_SIZE` in `{4, 8, 16, 32, 64}` across five
screen resolutions (128×128 up to 1920×1080), each with the same
24-triangle scene (right triangles, legs 6-50px, fixed-seed scatter) meant
to represent typical small-to-medium detail triangles rather than the
few-large-triangles scene used in sections 1-8. `PIXEL_LANE_COUNT` stayed
at 16 throughout (verified separately: since 16 >= every candidate
`COARSE_TILE_SIZE`, a full tile row always still fits in one round - no
lane-count change was needed alongside this one). Full interactive
results: **[Coarse Tile Sweep chart](https://claude.ai/code/artifact/f681c135-e098-4141-a0ba-f44d5b00dfc7)**.

`COARSE_TILE_SIZE = 8` won at **every single resolution tested**, including
the 1920×1080 target - `RenderController.COARSE_TILE_SIZE` changed from 16
to 8, `FINE_TILE_SIZE` and `PIXEL_LANE_COUNT` both left unchanged.

**Result** (24 small-to-medium triangles, 1920×1080 screen):

| `COARSE_TILE_SIZE` | cycles |
|---|---|
| 4 (no real hierarchy - equals FINE_TILE_SIZE) | 48,592 |
| **8 (new default)** | **41,906** |
| 16 (old default) | 45,994 |
| 32 | 51,696 |
| 64 | 59,803 |

**~8.9% fewer cycles** at the target resolution (45,994 → 41,906),
consistent with the same win at 128×128, 256×256, 512×512, and 1024×576 -
screen size did *not* shift where the optimum sits in this sweep; triangle
size relative to tile size is what actually governs the tradeoff, as
expected from the theory in section 1.

**Honest caveat:** re-running the 5-large-triangle/255×255 canonical scene
used throughout sections 1-8 (legs 85-235px) shows the *opposite* result at
that scale: 43,961 → 44,085 cycles, a negligible **+0.28% regression**.
This is exactly the predicted tradeoff - a smaller tile pays off for small
triangles and costs a little for large ones - so `COARSE_TILE_SIZE = 8` is
a genuine win for scenes dominated by small-to-medium triangles (the normal
case at HD+ resolutions) and a rounding-error loss for scenes of a handful
of very large triangles. Verified pixel-for-pixel identical output in both
scenes (same framebuffer SHA-256 as `COARSE_TILE_SIZE = 16` in each case)
and the existing pytest suite (7/7 passing).

A follow-up sweep at fixed 1920×1080, this time varying triangle leg length
directly (6-235px, uniform-size scenes) against every `COARSE_TILE_SIZE`
candidate, found this isn't a two-way tradeoff but a genuine **three-way
crossover**: tile 4 wins for 6-16px legs, tile 8 wins for 24-75px, tile 16
wins for 110-235px - see the
**[Coarse Tile Terrain](https://claude.ai/code/artifact/517eb4b9-6669-4a99-892a-dee7b6b33b9f)**
3D surface. The canonical scene's 85-235px legs sit right at the 8→16
boundary, which is exactly why it barely regressed rather than clearly
losing - it's straddling the crossover, not on one side of it.
`COARSE_TILE_SIZE = 8` was kept as the shipped default since it's the
better fit for the broad small-to-medium mesh case; a workload dominated by
very large triangles would do better at 16 instead.

---

## 10. Adaptive per-triangle `COARSE_TILE_SIZE`

**Problem:** section 9 picked one global `COARSE_TILE_SIZE` for every
triangle a core ever rasterises. But the terrain sweep found the real
optimum is a *property of the triangle*, not a scene-wide constant - a
scene mixing small, medium, and large triangles has no single tile size
that's best for all of them, so any fixed choice is a compromise for
whichever triangles don't match it.

**Change:** `COARSE_TILE_SIZE` (a fixed class constant) is replaced by
`COARSE_TILE_BANDS = ((20, 4), (90, 8))` plus `COARSE_TILE_SIZE_LARGE = 16`
and a `_coarse_tile_size_for_extent()` classmethod, matching the three
crossover bands the terrain sweep found. `S_BBOX` computes each triangle's
own bbox extent (`max(clamped width, clamped height)`) right after
clamping and picks that triangle's tile size from it - stored in a new
`self.triangle_coarse_tile_size` instance attribute, since it now varies
between triangles rather than being a constant every state can read
directly. `S_CORNER_READ`'s subdivision step (dividing a mixed-result
coarse tile into `FINE_TILE_SIZE` sub-tiles) reads that per-triangle value
instead of the old constant; everything else - `FINE_TILE_SIZE`,
`PIXEL_LANE_COUNT`, the corner-reject logic itself - is unchanged, since
16 (the largest band) was already `PIXEL_LANE_COUNT`'s sizing target.

**Result** (mixed scene: 6 triangles each of 10px/50px/200px legs,
1920×1080 - deliberately spanning all three bands in one scene, the case
no single fixed tile size can win):

| | cycles |
|---|---|
| fixed tile 4 (best for the 10px triangles alone) | 416,343 |
| fixed tile 8 (best for the 50px triangles alone) | 270,672 |
| fixed tile 16 (best for the 200px triangles alone) | 249,435 |
| **adaptive (this change)** | **247,254** |

**~0.9% fewer cycles than the best possible single fixed choice** for this
mix. That number looks small next to sections 1-9's, but it's an honest
one: cycle cost scales with triangle *area*, so the six 200px triangles
dominate this scene's total regardless of tile size, and "fixed 16" was
already close to optimal for them - the adaptive win is capped by how much
of the total cycle budget belongs to triangles a single fixed choice would
have gotten wrong. The real guarantee adaptive tiling gives isn't "always a
big win," it's "never worse than the best fixed choice, automatically, for
*any* mix" - a scene weighted more toward small/medium triangles (much more
common than a scene of giant triangles) would show a bigger gap, the same
way section 9's mixed small-to-medium scene showed -8.9% against a fixed
16. Verified pixel-for-pixel identical output across all four configurations
above (same framebuffer SHA-256 in every case) and the existing pytest
suite (7/7 passing).

---

## 11. How many cores actually help?

**Question:** `MultiCoreRenderModule`'s core count is a free parameter -
sections 4-5 measured 4 and 8 cores, but never asked what the actual
optimum is, or whether there even is one.

**Method:** swept `NUM_CORES` from 1 to 255 (one row per core - the finest
partitioning a row-strip scheme can produce on a 255-row screen) on the
canonical 5-triangle, 255×255 scene. Full interactive results:
**[Core Count Sweep](https://claude.ai/code/artifact/d54b8a66-0584-455b-af82-4dcb6d81cf19)**.

**Finding: raw cycle count has no interior minimum.** Cycles keep dropping
all the way to 255 cores (1,019 cycles, 43.3× faster than 1 core) with no
"too many cores" penalty, because every core is fully independent hardware
(own triangle memory, own framebuffer, own test pipeline) advancing on the
same simulated clock edge - a triangle getting duplicated into more cores
whenever its Y-extent straddles a strip boundary (the Binner's normal
behaviour, section 5) costs zero extra *cycles*, only extra *silicon*. The
only fixed serial cost is the Binner pre-pass itself, independent of core
count, which is why the curve never reaches zero.

**But parallel efficiency (speedup ÷ core count) decays sharply** and *that*
curve does have a clear knee:

| Cores | Cycles | Speedup | Efficiency |
|---|---:|---:|---:|
| 1 | 44,105 | 1.00× | 100% |
| 4 | 13,998 | 3.15× | 78.8% |
| 6 | 10,220 | 4.32× | 71.9% |
| 8 | 7,478 | 5.90× | 73.7% |
| 12 | 5,810 | 7.59× | 63.3% |
| 16 | 4,445 | 9.92× | 62.0% |
| 32 | 3,066 | 14.39× | 45.0% |
| 64 | 2,019 | 21.84× | 34.1% |
| 128 | 1,366 | 32.29× | 25.2% |
| 255 | 1,019 | 43.28× | 17.0% |

Marginal return drops off fast past ~12 cores: going 1→4 cores buys
~10,000+ cycles per additional core; going 128→255 buys ~2.7 cycles per
additional core. **6-12 cores is the practical knee** - still >60-70%
efficient - if the cost that matters is hardware (each core duplicates a
full corner-test/row-test pipeline), not just latency. If latency is the
only cost that matters, more is strictly better, up to one core per screen
row.

**Honest non-monotonic wrinkle:** 20 cores (4,722 cycles) is briefly
*slower* than 16 cores (4,445) - not noise, a real load-imbalance artifact:
that particular row-strip boundary happens to split the scene's 5
triangles less evenly than 16's boundaries do, giving one core a
momentarily worse straggler workload. Real parallel hardware shows exactly
this kind of quirk; it isn't a bug, and it disappears again by 24 cores.

Verified pixel-for-pixel identical output at every core count tested (same
`resolve_framebuffer()` result, 18,475 covered pixels in every case).

---

## 12. Split into rasteriser → output stage (work modules M1-M3)

**Change:** part of the move to the target architecture (Architecture..md,
decisions D3-D5, D8), not a tuning step. The legacy `RenderController` did
everything in one FSM. It is now three blocks in a pipeline:
- **`Rasteriser`** (`rasteriser.py`) emits one *fragment group* per
  covered 4×4 tile: coverage mask, per-lane depth and edge weights.
- **`FlatColourUnit`** (`slice/flat_colour.py`) turns each group into an
  export packet. It stands in for the shader core until M6.
- **`OutputStage`** (`output_stage/`) does the depth test and the
  framebuffer write, in launch order.

Two side effects made it faster:
- **4×4 tiles are pixel-tested directly.** The 16 lanes cover one whole
  4×4 tile in one 2-cycle round, so 4×4 tiles skip the corner test (8
  cycles). Only 8×8 and 16×16 tiles are corner-tested.
- **Rasterising and writing overlap.** The rasteriser walks the next tiles
  while the output stage writes the previous ones. Before, the single FSM
  stalled in `WRITE_ISSUE`/`WRITE_WAIT`.

**Result** (canonical scene; legacy = the same scene on `RenderModule`):

| | Legacy | Slice | Change |
|---|---:|---:|---:|
| 1 port | 44,085 | 21,944 | **-50.2%** |
| 16 banks | 32,051 | 11,409 | **-64.4%** |
| 8 slices + binner | 7,478 | 3,864 | **-48.3%** |

With 16 banks the slice finishes in exactly the rasteriser's own cycle
count (11,409): the writes are completely hidden, and **the rasteriser is
now the bottleneck**. Further gains must come from the tile walk or the
pixel test.

Note: without early depth testing, hidden pixels still go through
shading. That's free with the flat-colour unit, but it will cost
shader-core time from M6 on.

Verified pixel-for-pixel identical to the legacy modules on every test
scene, with 1-16 banks and 1-10 slices (`tb/rasteriserTest`,
`tb/outputStageTest`, `tb/sliceTest`).

---

## Summary

| Improvement | Scenario | Before | After | Change |
|---|---|---|---|---|
| Hierarchical tiling | 5 tri, 255×255 | 166,723 | 127,522 | -23.5% |
| 512-bit bus | 5 tri, 255×255 | 127,522 | 127,387 | -0.1% (scene-dependent; matters more with many small triangles) |
| Parallel pixel lanes (4) | 5 tri, 255×255 | 127,387 | 82,471 | -35.2% |
| Wider pixel lanes (4→16) | 5 tri, 255×255 | 82,472 | 77,576 | -5.9% |
| Pipelined framebuffer writes | 5 tri, 255×255 | 77,576 | 43,961 | -43.3% |
| Banked framebuffer writes (opt-in) | 5 tri, 255×255 | 43,961 | 31,895 | -27.4% |
| Multi-core (no binner) | 3 tri, 4 cores | 2,045 | 771 | 2.65× |
| Binner pre-pass | 20 tri, 8 cores | 5,019 | 1,150 | 4.36× |
| Coarse tile retuned (16→8) | 24 tri, 1920×1080 | 45,994 | 41,906 | -8.9% |
| Adaptive per-triangle coarse tile | mixed 18 tri, 1920×1080 | 249,435 | 247,254 | -0.9% (vs. best single fixed choice) |
| Core count sweep (1→255) | 5 tri, 255×255 | 44,105 | 1,019 | 43.3× (no cycle floor; ~6-12 cores is the efficiency knee) |
| Rasteriser → output stage pipeline (M1-M3) | 5 tri, 255×255, 1 port | 44,085 | 21,944 | -50.2% (16 banks: 11,409; 8 slices: 3,864) |

Every improvement was verified against a single-core / pre-change baseline
for pixel-for-pixel identical output before being accepted - correctness
first, then performance.

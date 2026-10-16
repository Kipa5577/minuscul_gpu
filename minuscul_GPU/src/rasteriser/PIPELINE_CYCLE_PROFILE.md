# RenderController pipeline cycle profile

This breaks down where `RenderController`'s cycles actually go, state by
state, to find the real bottleneck rather than guessing. Measured by
instrumenting the same 5-triangle, 255×255 scene used throughout
[`RASTERISER_ARCHITECTURE.md`](RASTERISER_ARCHITECTURE.md) (current code:
hierarchical tiling, 512-bit triangle bus, 16 parallel pixel-test lanes) —
sampling `ctrl.state` once per clock cycle, before it advances, and tallying
how many cycles were spent in each state.

**Total: 77,576 cycles.**

> This profile was taken *before* pipelined writes, banking and adaptive
> tiling. The two updates at the end show how the numbers moved. Current
> totals for this scene are 44,085 cycles (1 port) and 32,051 (16 banks).
> See the "Current state" table in `RASTERISER_ARCHITECTURE.md`.

---

## Cycles per FSM state

| State | Cycles | % of total |
|---|---:|---:|
| `WAIT_START` | 1 | 0.00% |
| `FETCH_ISSUE` | 5 | 0.01% |
| `FETCH_WAIT` | 10 | 0.01% |
| `BBOX` | 5 | 0.01% |
| `TILE_POP` | 1,545 | 1.99% |
| `CORNER_SETUP` | 4,968 | 6.40% |
| `CORNER_READ` | 4,968 | 6.40% |
| `ROW_SETUP` | 3,482 | 4.49% |
| `ROW_READ` | 3,482 | 4.49% |
| **`WRITE_ISSUE`** | **18,541** | **23.90%** |
| **`WRITE_WAIT`** | **37,082** | **47.80%** |
| `NEXT_TILE_ROW` | 3,482 | 4.49% |
| `NEXT_TRIANGLE` | 5 | 0.01% |

Grouped by pipeline phase:

| Phase | States | Cycles | % of total |
|---|---|---:|---:|
| Fetch + bbox | `FETCH_ISSUE/WAIT`, `BBOX` | 20 | 0.03% |
| Tile traversal | `TILE_POP` | 1,545 | 1.99% |
| Corner (coarse) test | `CORNER_SETUP/READ` | 9,936 | 12.81% |
| Per-pixel inside/depth test | `ROW_SETUP/READ`, `NEXT_TILE_ROW` | 10,446 | 13.47% |
| **Framebuffer write** | `WRITE_ISSUE/WAIT` | **55,623** | **71.70%** |
| Triangle bookkeeping | `NEXT_TRIANGLE` | 5 | 0.01% |

## The bottleneck: framebuffer writes, ~72% of all cycles

**Framebuffer writes cost 3 cycles per pixel and don't overlap at all.**
`WRITE_ISSUE` fires exactly once per surviving pixel (18,541 cycles = 18,541
pixels actually written across the 5 triangles), and `WRITE_WAIT` runs for
exactly 2 cycles after each issue (37,082 / 18,541 = 2.00) before
`fb.done` goes high — that's the framebuffer memory's own read/write
latency. The next pixel's `WRITE_ISSUE` never starts until the current
one's `WRITE_WAIT` finishes, so there is zero pipelining: 18,541 pixels ×
3 cycles = 55,623 cycles, exactly matching the measured total. This single
phase is larger than every other phase in the pipeline *combined* (21,953
cycles) by more than 2×.

This is also why the section-6 lane-count bump (4 → 16 lanes) only bought
~5.9%: it sped up the *test* phase (corner + row testing), which was never
the bottleneck. Every improvement made so far — hierarchical tiling, the
wide bus, wider lanes — targets the ~28% of cycles that isn't the write
phase. None of them touch the ~72% that is.

## Per-triangle totals

| Triangle | Cycles | Corner test | Row test | Writes |
|---|---:|---:|---:|---:|
| 0 (top-left, red) | 15,990 | 2,112 | 2,292 | 11,223 |
| 1 (top-right, blue) | 14,185 | 1,280 | 1,464 | 11,223 |
| 2 (bottom-left, green) | 15,989 | 2,112 | 2,292 | 11,223 |
| 3 (bottom-right, amber) | 15,079 | 1,952 | 1,878 | 10,983 |
| 4 (centre, closest, purple) | 16,333 | 2,480 | 2,520 | 10,971 |

Writes dominate every single triangle individually, not just in aggregate —
each triangle spends 69–73% of its own cycles in `WRITE_ISSUE`/`WRITE_WAIT`.
Triangle 1 (smallest bbox: 85×85 vs. the corner triangles' 85×85 plus more
tile overhead from screen-edge clipping) has the fewest corner/row-test
cycles, but its write cost is identical — writes scale with *pixels
actually covered*, not bbox or tile-traversal overhead.

## Recommendation

The framebuffer write path is the bottleneck by a wide margin, and no prior
optimization has touched it. Two changes attack it directly, in order of
expected leverage:

1. **Pipeline the write port**: let `WRITE_ISSUE` for pixel N+1 fire the
   same cycle `WRITE_WAIT` for pixel N is still resolving, instead of
   waiting for `fb.done`. If the memory genuinely has fixed 2-cycle
   latency, a simple issue queue could get this down to ~1 cycle/pixel
   once primed, roughly a 3× cut on 72% of the total runtime.
2. **Bank the framebuffer across multiple lanes** (mirroring the
   `PIXEL_LANE_COUNT` pixel-test lanes with `PIXEL_LANE_COUNT` independent
   write ports/banks, addressed so lanes never collide on the same bank in
   one round): would let a full row's surviving pixels write in parallel
   instead of one at a time. Bigger win than (1) alone, but real hardware
   cost — one arbiter/decoder per bank, and py4hw's single-driver-per-wire
   rule means each bank needs its own dedicated `Memory` instance, similar
   to how `MultiCoreRenderModule` already gives each core its own private
   framebuffer.

Either change would dwarf the ~6–35% gains from tiling, the wide bus, or
wider test lanes, because it's the only one aimed at the 72% instead of the
remaining 28%.

---

## Update: recommendation (1) implemented

`WRITE_ISSUE` now fires every cycle back-to-back while `pending_writes` has
entries left, instead of blocking in `WRITE_WAIT` after every single pixel;
`WRITE_WAIT` only drains the row's last write before moving on. See
[`RASTERISER_ARCHITECTURE.md`](RASTERISER_ARCHITECTURE.md#7-pipelined-framebuffer-writes)
for the change itself. Re-profiling the same scene:

| State | Before (cycles) | After (cycles) | Change |
|---|---:|---:|---:|
| `WRITE_ISSUE` | 18,541 | 18,541 | unchanged (still 1 cycle/pixel to issue) |
| `WRITE_WAIT` | 37,082 | 3,467 | **-90.6%** (drains once per row, not once per pixel) |
| Write phase total | 55,623 | 22,008 | **-60.4%** |
| **Grand total** | **77,576** | **43,961** | **-43.3%** |

The write phase is no longer the outsized bottleneck it was — it's now
50.1% of total cycles (down from 71.7%), roughly in line with its actual
share of the work, and the corner/row test phases (previously ~28% of a
much bigger total) now make up a correspondingly larger fraction of a much
smaller total. `WRITE_ISSUE` didn't shrink at all, since every pixel still
needs its own issue cycle - only the *stalling* between issues was
eliminated, which is exactly what recommendation (1) targeted.

---

## Update: recommendation (2) implemented (opt-in via `write_bank_count`)

`RenderController`/`RenderModule` now take a `write_bank_count: int = 1`
parameter. At the default (1) nothing changes - single write port,
pipelined issue, exactly the behaviour profiled above. Set it higher (e.g.
`write_bank_count=PIXEL_LANE_COUNT`) and the framebuffer is split into that
many independently addressed banks (interleaved by pixel address: `bank =
local_addr % write_bank_count`), so an entire row's surviving pixels issue
into distinct banks in the *same* cycle instead of one address at a time.
See [`RASTERISER_ARCHITECTURE.md`](RASTERISER_ARCHITECTURE.md#8-banked-framebuffer-write-ports-opt-in)
for the implementation. Re-profiling the same scene at `write_bank_count=16`:

| State | 1 bank (cycles) | 16 banks (cycles) | Change |
|---|---:|---:|---:|
| `WRITE_ISSUE` | 18,541 | 3,314 | -82.1% |
| `WRITE_WAIT` | 3,467 | 6,628 | +91.2% (one drain per row-batch now, and there are more batches than before) |
| Write phase total | 22,008 | 9,942 | **-54.8%** |
| **Grand total** | **43,961** | **31,895** | **-27.4%** |

Combined with recommendation (1), that's **31,895 cycles vs. the original
77,576 — a 58.9% reduction overall.** The write phase has shrunk enough
(50.1% → 31.2% of total) that it's no longer the dominant cost: the
corner-test and row-test phases (`CORNER_SETUP/READ`, `ROW_SETUP/READ`,
`NEXT_TILE_ROW`) now make up the larger share (~53.7% combined). Any
further large win would have to target *that* phase - e.g. more pixel-test
lanes, or skipping redundant corner tests - not the write port, which has
now been addressed from both directions (pipelining and banking).

Verified pixel-for-pixel identical framebuffer output across
`write_bank_count` in `{1, 2, 4, 16}` (same SHA-256 hash) and covered by a
new parametrized pytest
(`test_banked_framebuffer_matches_single_port_output`), alongside the
existing 4 tests (7/7 passing in total).

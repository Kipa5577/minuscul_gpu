# binner

Sends each triangle only to the slice(s) whose screen strip its Y-extent
overlaps, so a slice never fetches triangles that can't land in it. A
triangle straddling a strip boundary is copied into each slice it
touches.

**Implements:** D3 (slices own horizontal screen strips).

## Existing code

`binner.py`: `Binner` and the `AllDone` helper (moved from
`rasteriser/renderer.py` in M3, which still re-exports them). It reads triangle lines from one source memory
and writes them straight into each slice's private triangle memory (a
direct Python-list write, the same preload pattern used elsewhere,
because py4hw allows only one bus master per memory).

## Work

**M3 (slice integration):**
- [x] move `Binner` and `AllDone` into this folder; `renderer.py` keeps
      re-exports until the tests and animations import from here
- [x] no behaviour change

**M9 (vertex pass):**
- [ ] read triangle lines from `TRI_BUF` in **global memory** through the
      arbiter (M7), instead of a private source memory
- [ ] take the triangle count from the primitive unit
- [ ] keep words 10-15 of each line (vertex indices, 1/w) intact

**Done when:** M3: existing multi-core tests and animations pass,
unchanged. M9: a draw whose triangles come from the primitive unit is
binned and rendered correctly across several slices.

**Depends on:** M3 for the move; M7 and M9 for global memory.

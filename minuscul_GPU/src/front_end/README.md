# front_end

The chip's command processor. The host writes a draw or compute
descriptor into global memory and triggers the front end, which reads it
and runs the passes in order: vertex pass → primitive unit → binner →
pixel pass, or a compute launch.

**Implements:** D1 build-order steps 4-5, D11 (two-pass frame), D13
(descriptor formats in [README_ISA.md §6.1-6.2](../README_ISA.md)).

## M8: compute launches (build step 4)

Tasks:
- [ ] host interface: descriptor address + start, `done` back to the host
- [ ] read the compute descriptor (16 words) through the memory arbiter
- [ ] split `THREAD_COUNT` into groups of 16, and hand them round-robin to
      the slices' dispatchers
- [ ] signal `done` when every group has executed `S_ENDPGM`
- [ ] read the draw descriptor and start a raster-only draw
      (`VS_PC = 0xFFFFFFFF`): binner → pixel pass, with `TRI_BUF` written by
      the host

**Done when:** the README_ISA §8 vector-add kernel gives correct results
for thread counts that aren't multiples of 16, on 1 and on several
slices.

**Depends on:** M6, M7.

## M9: draws with a vertex pass (build step 5)

- [ ] launch `VERTEX_COUNT` vertex threads (dispatcher vertex mode); wait
      for all of them
- [ ] run the primitive unit, then the binner, then the pixel pass
- [ ] host-side helper to write a draw (vertex data, indices,
      descriptor, program image)

**Done when:** see `primitive_unit/README.md`.

# memory

Memory interfaces and memories used by every other block. In **M7** it
also gets the chip's shared global memory system.

**Implements:** D9 (memory hierarchy), the VMEM coalescing rule in
README_ISA.md §5.6.

## Existing code: `memory.py`

| Name | What it is |
|---|---|
| `MemoryInterface` | bus bundle: `read`, `write`, `address`, `read_data`, `write_data`, `done`; `data_width` defaults to 32, and the triangle memory uses 512 |
| `Memory` | simple memory: answers a read or write with `done` on the next clock |
| `TrinagleInfo` | the 9 vertex fields of the current triangle (used inside the rasteriser) |
| `MatrixInfo4X4` | 16 fields of a 4×4 matrix (used by the legacy math blocks) |
| `registerFile` | broken stub (local `reg` list); unused, to be deleted |

## M7: memory system

**Goal:** one global data memory shared by all slices (and later the front
end and primitive unit), without breaking py4hw's one-driver-per-wire
rule.

Tasks:
- [ ] `GlobalMemory`: word-addressed, organised in 512-bit lines of 16
      words; one line per transaction
- [ ] `Arbiter`: N client `MemoryInterface`s → 1 memory, round-robin, one
      transaction per cycle; each client has its own interface
- [ ] `Coalescer` (used by the shader core's VMEM path): takes 16 lane
      addresses + `exec`, issues one transaction per distinct line, and
      reassembles per-lane results
- [ ] delete `registerFile`

**Interfaces:** each client owns a `MemoryInterface` with
`data_width = 512` and line addresses, plus a 16-bit word-enable mask for
partial-line stores (to be added to `MemoryInterface`).

**Done when:** several shader cores and other clients run the same program
against one `GlobalMemory` through the arbiter and get the same results as
with private memories. Coalescing counts match the README_ISA §5.6 rule
(e.g. `V_LOAD_BCAST` = 1 transaction).

**Depends on:** M5 (shader core, as the main client).

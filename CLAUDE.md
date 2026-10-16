# minuscul_gpu: handoff notes for agents

A small GPU designed and simulated cycle by cycle in Python with **py4hw**.
Owner: Patrick Kosa-Ivasca. This file is the entry point for an agent
picking the project up. It says where things are, what state they are in,
how to work on them, and what comes next. Details live in the linked
documents; keep this file short and current.

Last updated: 2026-10-16, after work module **M6** (build step 3 done).

---

## 1. Read these first

| Document | What it gives you |
|---|---|
| [README.md](README.md) | project overview, chip block diagram and build plan (Mermaid), component status, how to run |
| [minuscul_GPU/src/Architecture..md](minuscul_GPU/src/Architecture..md) | NVIDIA/AMD background, **the chosen architecture** (summary + ASCII diagram), the table of final decisions D1-D15 |
| [minuscul_GPU/src/decisions.md](minuscul_GPU/src/decisions.md) | how each decision was reached (options, trade-offs, reasons) |
| [minuscul_GPU/src/README_ISA.md](minuscul_GPU/src/README_ISA.md) | **VENUS v2 ISA**: registers, encodings, all 89 instructions, launch conventions, descriptors, vertex records, triangle-line format, assembler, examples |
| [minuscul_GPU/src/README.md](minuscul_GPU/src/README.md) | folder map, **work modules M1-M10** with dependencies and "done when" tests, coding conventions |
| `minuscul_GPU/src/<block>/README.md` | per-block purpose, interfaces, task checklist, status, measurements |
| [minuscul_GPU/tb/README.md](minuscul_GPU/tb/README.md) | what every testbench checks; shared test helpers |
| [minuscul_GPU/src/rasteriser/RASTERISER_ARCHITECTURE.md](minuscul_GPU/src/rasteriser/RASTERISER_ARCHITECTURE.md) | rasteriser design history with measured cycle counts (#1-#12) |

The file name `Architecture..md` (two dots) is the owner's own; keep it,
since many links point to it.

## 2. Current state

**Done (M1-M6), all tested:** a frame of screen-space triangles goes
binner → per-slice rasteriser → pixel dispatcher → VENUS v2 shader core →
output stage → framebuffer strip, on 1 to N slices.

| Module | Status | Code | Tests |
|---|---|---|---|
| M1 Fragment output | done | `src/rasteriser/rasteriser.py`, `fragment.py` | `tb/rasteriserTest/` |
| M2 Output stage | done | `src/output_stage/` | `tb/outputStageTest/` |
| M3 Slice integration | done | `src/slice/slice.py`, `flat_colour.py`; `src/binner/` | `tb/sliceTest/` |
| M4 Assembler | done | `tools/venus_asm.py`; table in `src/shader_core/isa.py` | `tb/assemblerTest/` |
| M5 Shader core | done | `src/shader_core/` (`alu.py`, `venus_ref.py`, `core.py`) | `tb/shaderCoreTest/` |
| M6 Pixel dispatcher | done | `src/dispatcher/dispatcher.py` | `tb/dispatcherTest/` |
| **M7 Memory system** | **next** | `src/memory/` | - |
| M8 Front end + compute | planned | `src/front_end/` | - |
| M9 Vertex pass + primitive unit | planned | `src/primitive_unit/` | - |
| M10 Display | planned (independent, any time) | `src/display/` | - |

**Tests:** 269 pass in about 60 s.

**Git:** nothing has been committed since `495de71` ("Restructuring the
repository"). All M1-M6 work, the docs and the folder moves are
uncommitted. Ask the owner before committing.

**Reference numbers** (canonical 5-triangle 255×255 scene,
`tb/common.py:CANONICAL_SCENE`):

| Configuration | Cycles |
|---|---:|
| legacy `RenderModule`, 1 port / 16 banks / 8 cores | 44,085 / 32,051 / 7,478 |
| `Slice` (flat colour), 1 port / 16 banks / `SliceArray` 8 slices | 21,944 / 11,409 / 3,864 |
| `Slice` with flat pixel shader on the core (1 port) | 21,757 |
| Gouraud shader, 32×32 scene: 1 slot / 4 slots / 4 slices × 4 slots | 13,141 / 12,227 / 7,193 (memory-bound, see M7) |

## 3. Running things

```
cd /mnt/f/Hardware/Puces/minuscul_gpu
gpu_env/bin/python -m pytest                                  # all testbenches (pytest.ini)
gpu_env/bin/python -m pytest minuscul_GPU/tb/sliceTest -v -s  # one module, prints cycle counts
gpu_env/bin/python -m minuscul_GPU.tools.venus_asm prog.vas -o prog.hex   # assembler
gpu_env/bin/python -m minuscul_GPU.tools.venus_asm prog.hex -d            # disassembler
gpu_env/bin/python minuscul_GPU/tb/rasterisationTest/animation_of_RenderModule.py   # needs a display
```

- Virtualenv `gpu_env/`: Python 3.13, py4hw 2026.2, numpy, Pillow,
  matplotlib, pytest. `requirements.txt` lacks matplotlib and pytest.
- Code is a package (`minuscul_GPU.src...`), with relative imports inside
  `src/` and `tb/`. Run from the repository root.

## 4. How to work on this project (owner's preferences)

- **Architecture decisions belong to the owner.**
  - Present the options with their trade-offs, consequences and a
    recommendation, then let the owner choose.
  - Log the process in `decisions.md` (status open → decided, with a
    discussion log). Record the final choice in the "Final decisions" table
    of `Architecture..md`.
  - When the owner says "decide for me" (as for D13-D15), decide, and log
    it as "decided by Claude at the owner's request".
- **Sources must be freely available** (vendor whitepapers, public ISA
  guides, open-source drivers, papers).
- **Every module gets a testbench** in `minuscul_GPU/tb/<module>Test/`.
  Prefer differential tests against a golden reference over hand-picked
  expectations.
- **After finishing a module:**
  - tick its tasks in the block README and add a "done" note with
    measurements and any deviations from the plan;
  - update the status column in `src/README.md`, the table in `tb/README.md`,
    and the component-status table and diagram classes in the root
    `README.md`;
  - update this file.
- **Report honestly.** Say when a prediction was wrong (e.g. M3's cycle
  counts went down, not up), and when a test checks less than its
  "done when" says (e.g. M5 compares at program end, not after every
  instruction).
- **Parameters** (`GROUP_WIDTH`, `GROUP_SLOTS`, `IMEM_WORDS`, line width …)
  live in `src/params.py`. Don't repeat them as literals.

## 5. Gotchas (things that cost time)

**py4hw**
- **Clock semantics.** In `clock()`, `wire.get()` returns the value from
  before the edge, and `wire.prepare(v)` sets the value after the edge.
  Combinational leaves (`propagate()`) settle after every edge. A
  setup → read round through a combinational leaf therefore takes 2 cycles.
- **Prepare each wire at most once per `clock()`.** A second prepare prints
  "WARNING! wire … already prepared" (the last value wins). See
  `ShaderCore._exp_valid_next`, which drives `exp.valid` once.
- **One driver per wire.** A memory can have only one bus master, which is
  why the M7 arbiter is needed. `Binner` writes `Memory.memory` directly as
  a preload, by design.
- **32-bit wires hold two's-complement patterns.** Mask with `& 0xFFFFFFFF`
  when preparing, and read back with `py4hw.IntegerHelper.c2_to_signed`.

**Handshakes and timing**
- **valid/ready rule** (fragment, export and launch interfaces): the
  source holds `valid` and its data until an edge where both are 1, as both
  sides see them with `get()`. Source and sink both decide from pre-edge
  values. Examples: `FlatColourUnit`, `PixelDispatcher`, `tb/common.py:FragmentSink`.
- **`memory.Memory` protocol.** It answers `done` one cycle after `read`
  or `write`. For reads, drop `read` for one cycle before the next access,
  or a stale `done` with the old address's data is accepted. The core's
  VMEM unit uses issue → wait → release, 3 cycles per access. The output
  stage issues writes back to back, which is safe only because a repeated
  write is idempotent.
- **Slice `done` is an AND** of the rasteriser's `done` and every
  downstream `idle`. It is correct only because the rasteriser raises
  `done` at least 2 cycles after its last fragment transfer, by which time
  the downstream units report busy. If you change FSM timing, keep
  `tb/sliceTest::test_done_rises_only_after_the_last_write` passing.
- **Export order.** The output stage accepts exports only in `seq` order.
  Every pixel group must produce exactly one export. The core sends an
  empty one (mask 0) when a pixel shader ends without `V_EXPORT_COLOR`.
  A missing `seq` deadlocks the slice.

**Code organisation**
- **The legacy renderer is the golden reference.** `RenderController` /
  `RenderModule` / `MultiCoreRenderModule` in `src/rasteriser/renderer.py`
  are used by `tb/common.py:legacy_render`, the differential tests and both
  animations. Don't delete them; they are not part of the new design.
- **The animation scripts run at import time.** Importing them starts a
  matplotlib animation that never ends. Never import them from tests.
- **The core shares instruction semantics with the reference model.**
  `ShaderCore` calls `venus_ref.execute` and the `alu.py` functions.
  Core-vs-reference tests check the control path, and `test_alu.py`
  checks the arithmetic. To add or change an instruction, update together:
  `isa.py` (table), `alu.py`/`venus_ref.py` (semantics), `README_ISA.md`
  (spec), and the per-format count in
  `tb/assemblerTest::test_every_opcode_is_unique_and_fits`.
- `pytest.ini` sets `python_files = test_*.py *Test.py` so the legacy
  `rasterisationTest.py` is collected.
- **Moved folders.** `rasterisaion/` was renamed `rasteriser/` (typo fix),
  `Threads/` became `legacy/`, and `screen/` became `display/`. Old
  imports of these paths no longer work.

## 6. Next step: M7 memory system (detailed)

Goal: one global data memory shared by every slice (and later by the
front end, primitive unit and binner), with **coalescing**. Full task
list: `src/memory/README.md`; rule: README_ISA.md §5.6; decision D9.

**What exists today**
- `ShaderCore` (`src/shader_core/core.py`, `_step_memory` / `_execute`)
  makes **one 32-bit access per active lane**: 3 cycles each, 16
  accesses even for `V_LOAD_BCAST`.
- In shader mode, `SliceArray` gives each slice a **private copy** of the
  data memory (`data_memories`).

**Suggested design**
1. `GlobalMemory` (`src/memory/`): word-addressed, 512-bit lines
   (`params.LINE_WORDS` = 16). One transaction reads or writes a line,
   with a 16-bit word-enable mask for stores. Add the line data width and
   word-enable to the interface; keep the plain 32-bit `Memory` working
   for the existing users.
2. Coalescer (in or beside the core's VMEM unit):
   - group the active lanes' addresses by line (`addr // 16`);
   - issue one transaction per distinct line, in a fixed order;
   - scatter or gather the words per lane.
   For stores to the same word, the higher lane wins (README_ISA §5.6).
3. `Arbiter`: N client interfaces → 1 memory, round-robin, one transaction
   per cycle. Each client owns its interface (py4hw's one-driver rule).
4. Connect every slice's core to one `GlobalMemory` through the arbiter,
   and remove the private copies from `SliceArray`.
5. Delete the broken `registerFile` stub in `src/memory/memory.py`.

**Tests to write** (`tb/memoryTest/`)
- Transaction counts: `V_LOAD_BCAST` = 1; aligned `_LANE` = 1; a 2-line
  stride = 2.
- The existing core-vs-reference random programs, with the new memory
  path (`tb/shaderCoreTest/test_core.py:run_core`).
- Several cores sharing memory give the same results as private memories.
- Arbiter fairness under contention.
- Re-measure Gouraud 32×32 against the 13,141 / 12,227 / 7,193 baseline.

**Watch out**
- A store and a load in flight from different slices: there are no caches,
  so define the order in which the arbiter makes writes visible, and test
  it.

## 7. Later modules (summary; details in the block READMEs)

- **M8 Front end + compute** (`src/front_end/`, `src/dispatcher/`):
  - read the draw and compute descriptors (README_ISA §6.1-6.2) from global
    memory, replacing the Python `DrawState`;
  - add compute launches with `v0` = thread id, spread round-robin across
    slices (`exec` masks the threads past `THREAD_COUNT`);
  - support raster-only draws (`VS_PC = 0xFFFFFFFF`).
- **M9 Vertex pass + primitive unit**:
  - vertex launches (`v0` = vertex index);
  - primitive unit rules in README_ISA §6.5: trivial reject, drop
    triangles crossing the near plane (D14), ±16383 guard band, cull,
    24-bit Z;
  - triangle-line words 10-15 (indices, 1/w): extend
    `rasteriser/triangle_line.py`;
  - the Binner reads `TRI_BUF` from global memory;
  - move `MatrixMultiplier4x4`/`CrossProduct`/`Vector3Norm` to `legacy/`.
  - Done when a scene from `algorithms/rasterisation.py` renders fully
    on-chip.
- **M10 Display** (`src/display/`): VGA 640×480 at 60 Hz timing
  generator; front/back dual-port framebuffer strips with the swap during
  vertical blanking; simulated screen replacing the broken `screen.py`.

## 8. Known follow-ups and open items

- Port both animations (`tb/rasterisationTest/animation_of_*.py`) to
  `Slice`/`SliceArray`. They still draw the legacy `RenderController`'s
  internal states.
- `rasterisationBenchmark.py` still measures the legacy `RenderModule`.
- No early depth test: hidden pixels are shaded too, and depth runs in the
  output stage. That costs shader time from M6 on. Hierarchical or early Z
  is a later optimisation.
- `V_RSQ_F32` is rounded from double precision (README_ISA §2); every
  other operation is exactly rounded.
- `requirements.txt` lacks matplotlib and pytest. `setup.py` lists unused
  `pyelftools` and `itanium-demangler`.
- `minuscul_GPU/docs/` is an empty folder from before this work, left
  untouched.
- Deferred by the owner: choosing an FPGA board (only needed for a
  physical display output, D12 option C).
- Later ISA additions listed in README_ISA §9: scalar loads, textures,
  atomics/workgroups, hardware interpolation, FP16, near-plane clipping,
  sub-pixel precision.

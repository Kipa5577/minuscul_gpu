# minuscul_GPU source

One folder per hardware block of the chosen architecture
([Architecture..md](Architecture..md)). The design is built in ten work
modules, **M1-M10**, done one at a time. Each module has a clear
deliverable and a "done when" test, and leaves a working chip behind it.

## Design documents

| File | Content |
|---|---|
| [Architecture..md](Architecture..md) | NVIDIA/AMD overview, chosen architecture, final decisions |
| [decisions.md](decisions.md) | how each decision was made (D1-D15) |
| [README_ISA.md](README_ISA.md) | VENUS v2 ISA, launch interfaces, assembler |
| [README_ISA_v1.md](README_ISA_v1.md) | old v1 draft (superseded) |

## Folders

| Folder | Hardware block | Status |
|---|---|---|
| [memory/](memory/README.md) | memory interfaces, memories; later the global memory, arbiter and coalescer | interfaces exist; global memory system in M7 |
| [rasteriser/](rasteriser/README.md) | triangle → 4×4 fragment groups (fixed function) | **M1 done** |
| [binner/](binner/README.md) | triangle → slice(s) | working, moved in M3; reads global memory in M9 |
| [output_stage/](output_stage/README.md) | in-order export, depth test, framebuffer write | **M2 done** |
| [slice/](slice/README.md) | one slice = rasteriser + shading + output stage; the slice array | **M3 done** (flat-colour shading) |
| [shader_core/](shader_core/README.md) | VENUS v2 group core: scalar + 16-lane FP32 vector unit, `GROUP_SLOTS` scheduler | **M5 done** |
| [dispatcher/](dispatcher/README.md) | launches groups into the core and preloads their registers | **M6 done** (pixel); M8 (compute), M9 (vertex) |
| [front_end/](front_end/README.md) | reads draw/compute descriptors, sequences the passes | M8, M9 |
| [primitive_unit/](primitive_unit/README.md) | clip-space vertices → triangle lines | M9 |
| [display/](display/README.md) | display controller, double buffering, simulated screen | M10 |
| [legacy/](legacy/README.md) | superseded code kept for reference | - |
| [../tools/](../tools/README.md) | `venus_asm` assembler and disassembler | **M4 done** |

## Work modules

| # | Module | Folder(s) | Build step | Depends on | Done when |
|---|---|---|---|---|---|
| **M1** | Fragment output | rasteriser | 2 | - | the rasteriser emits 4×4 fragment groups instead of writing the framebuffer |
| **M2** | Output stage | output_stage | 2 | M1 | depth test + framebuffer write from export packets, in launch order |
| **M3** | Slice integration | slice, binner | 2 | M1, M2 | rasteriser → flat-colour unit → output stage; existing tests and multi-core output pixel-identical to today |
| **M4** | Assembler | tools | (any time) | - | VENUS v2 text ↔ words round-trips; macros expand as specified |
| **M5** | Shader core | shader_core | 3 | M4 | every VENUS v2 instruction matches a Python reference model |
| **M6** | Pixel dispatcher | dispatcher | 3 | M3, M5 | a Gouraud pixel shader renders a triangle correctly |
| **M7** | Memory system | memory | 3-4 | M5 | global memory + round-robin arbiter + coalescing, shared by all slices |
| **M8** | Front end + compute | front_end, dispatcher | 4 | M6, M7 | the vector-add compute kernel runs across several slices |
| **M9** | Vertex pass + primitive unit | primitive_unit, front_end, dispatcher, binner | 5 | M8 | a 3D scene from `algorithms/rasterisation.py` renders fully on-chip |
| **M10** | Display | display | (after M3) | M3 | frames appear on the simulated screen with no tearing |

```
M1 ─► M2 ─► M3 ─────────────► M6 ─► M8 ─► M9
              │          ▲          ▲
              │   M4 ─► M5 ─► M7 ───┘
              │
              └─► M10   (independent once M3 is done)
```

M4 (assembler) needs nothing else and can be done any time, in parallel
with M1-M3. M10 can be done any time after M3.

## Conventions

- **Language and simulator:** py4hw, the same style as
  `rasteriser/renderer.py`. Combinational leaves use `propagate()`,
  sequential FSMs use `clock()`, and structural modules only wire children.
- **Shared parameters** (`GROUP_WIDTH` = 16, `GROUP_SLOTS`, `NUM_SLICES`,
  `IMEM_WORDS`, line width 512) are defined once in `params.py` and
  imported, never repeated as literals.
- **Tests** for a module go in `minuscul_GPU/tb/<module>Test/`. Running
  `pytest` from the repository root runs every testbench (`pytest.ini`).
- **A module is done** when its "done when" test passes, the existing
  tests still pass, and its folder README status is updated.
- **Design changes** made while building a module are logged in
  [decisions.md](decisions.md) and, if final, in
  [Architecture..md](Architecture..md).

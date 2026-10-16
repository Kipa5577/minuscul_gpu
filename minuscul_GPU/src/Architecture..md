# minuscul_GPU architecture

This document is where we decide the architecture of the chip. Part 1
summarises how NVIDIA and AMD build their GPUs, using **only freely
available sources**: vendor whitepapers, public ISA manuals, official
programming guides, open-source drivers and published research. Part 2
lists the decisions we have to make for minuscul_GPU, informed by Part 1.

---

# Part 1 - How NVIDIA and AMD GPUs are built

## 1.1 The common shape of every modern GPU

NVIDIA and AMD use different names, but the structure is the same:

```
 Host (CPU) ──PCIe──► Command processor / front-end
                            │  reads command buffers, launches work
                            ▼
              Work distributor (blocks / workgroups → cores)
                            │
     ┌──────────────────────┼──────────────────────┐
     ▼                      ▼                      ▼
 ┌────────────┐       ┌────────────┐        ┌────────────┐
 │ Graphics   │       │ Graphics   │  ...   │ Graphics   │   one cluster each:
 │ cluster    │       │ cluster    │        │ cluster    │   - raster engine
 │ ┌──┐ ┌──┐  │       │            │        │            │   - geometry / primitive unit
 │ │SM│ │SM│…│       │  cores…    │        │  cores…    │   - N shader cores
 │ └──┘ └──┘  │       │            │        │            │   - ROPs (pixel output)
 └─────┬──────┘       └─────┬──────┘        └─────┬──────┘
       └──────────── shared L2 cache ─────────────┘
                            │
               Memory controllers → GDDR / HBM
```

The ideas both vendors share:

1. **Many simple cores instead of a few complex ones.** No out-of-order
   execution and no branch prediction. The transistors go into ALUs and
   registers.
2. **SIMT execution.** Threads are grouped (32 on NVIDIA, 32 or 64 on AMD)
   and each group runs one instruction at a time across all its lanes.
3. **Latency hiding by switching groups, not by caches.** Each core holds
   many groups in flight. When one waits for memory, the scheduler issues
   from another on the next cycle. A huge register file makes this possible.
4. **Fixed-function units around the programmable cores.** Rasterisers,
   texture units, ROPs (blend/depth/write), ray-tracing units and matrix
   units each do one job much faster than a shader could.
5. **A memory hierarchy built for bandwidth.** Registers → small per-core
   L1 / shared memory → large shared L2 → wide external DRAM.

## 1.2 NVIDIA

### Hierarchy

| Level | NVIDIA name | Contents |
|---|---|---|
| Chip | GPU | GigaThread engine (work distributor), several GPCs, L2, memory controllers |
| Cluster | **GPC** (Graphics Processing Cluster) | 1 **raster engine**, several TPCs, ROPs (inside the GPC since Ampere) |
| Pair | **TPC** (Texture Processing Cluster) | 2 SMs + 1 **PolyMorph engine** (vertex fetch, tessellation, viewport transform) |
| Core | **SM** (Streaming Multiprocessor) | 4 processing blocks, L1 / shared memory, texture units, RT core |
| Sub-core | **SM sub-partition** (processing block) | 1 warp scheduler, 1 dispatch unit, register file, ALUs, 1 tensor core |

### Inside an SM (Ampere GA102 / Ada AD102, from the whitepapers)

- **4 sub-partitions.** Each issues **one warp instruction per clock**.
- **Per sub-partition:** a 16K × 32-bit register file (64 KB, so 256 KB per
  SM), 16 FP32 lanes plus 16 lanes that do FP32 *or* INT32, a load/store
  unit, an SFU (sin, cos, reciprocal, sqrt) and 1 tensor core.
- **128 KB of L1 data cache / shared memory per SM.** The split is
  configurable. Hopper raised this to 256 KB (up to 228 KB as shared).
- **4 texture units and 1 RT core** (ray–triangle and ray–box intersection)
  per SM.
- **Blackwell** makes all 128 lanes per SM able to run FP32 *or* INT32. It
  adds 5th-gen tensor cores and a dedicated Tensor Memory (TMEM) per SM for
  matrix operands.

### Execution model

- **Warp = 32 threads.** The scheduler picks a ready warp each cycle; a
  scoreboard tracks which registers are still being written.
- **Divergence:** when threads of a warp take different branches, the
  hardware runs each path with the inactive lanes masked off. Since Volta,
  every thread has its own program counter ("independent thread
  scheduling"), so threads in a warp can interleave and even synchronise
  with each other, but they still run one path at a time.
- **Thread block → SM:** a whole block always runs on one SM, so its
  threads can share fast on-chip shared memory and use `__syncthreads()`.
  Hopper added **thread block clusters**, letting blocks on neighbouring
  SMs read each other's shared memory.

### Graphics pipeline (fixed function)

PolyMorph engine (vertex fetch, tessellation, viewport transform,
attribute setup) → **raster engine** (triangle setup, rasterisation, Z
cull) → shader cores (pixel shading) → **ROPs** (depth test, blending,
colour compression, framebuffer write).

### What is and isn't public

- **Public:** architecture whitepapers, the CUDA programming guide, the
  **PTX** virtual ISA (fully documented), the open-source kernel driver
  (`open-gpu-kernel-modules`), and the reverse-engineered Mesa NVK /
  nouveau drivers.
- **Not public:** the real machine ISA (**SASS**). Only instruction mnemonic
  lists exist officially; encodings come from reverse engineering and
  research papers.

## 1.3 AMD

AMD has two separate families: **RDNA** for graphics (Radeon) and **CDNA**
for compute (Instinct). Both descend from **GCN** (2012-2019), the
architecture whose open RTL clone (MIAOW) exists.

### RDNA (graphics) hierarchy

| Level | AMD name | Contents |
|---|---|---|
| Chip | GPU | Command processor, ACEs (async compute engines), shader engines, L2, Infinity Cache (RDNA 2+), memory controllers |
| Cluster | **Shader Engine** → **Shader Array** | each Shader Array: **rasteriser**, **primitive unit**, **render backends (ROPs)**, a shared graphics L1, several WGPs |
| Core | **WGP** (Work Group Processor) = 2 CUs | shared instruction cache, scalar data cache, **128 KB LDS** (shared memory) |
| Half-core | **CU** (Compute Unit) | **2 × SIMD32**, 16 KB vector L0 cache, texture units, ray accelerator (RDNA 2+) |
| Lane group | **SIMD32** | 32-lane vector ALU, its own vector register file (128 KB on RDNA 1), a **scalar ALU** with its own scalar registers, and an instruction buffer for up to 16-20 waves |

### What makes AMD different from NVIDIA

- **A scalar unit next to every vector unit.** Values that are the same for
  every thread in a wave (loop counters, base addresses, constants) live in
  **SGPRs** and are computed once by the scalar ALU instead of 32 times. The
  compiler decides what is scalar. This is a cheap, effective idea for a
  small GPU.
- **Wave32 *and* wave64.** RDNA runs 32-thread waves natively, and 64-thread
  waves over two passes. GCN and CDNA use wave64 on **SIMD16** units, so
  one instruction takes 4 cycles.
- **Execution masks are explicit.** Divergence is handled by the compiler
  writing the `EXEC` mask register with ordinary scalar instructions. There
  is no hidden reconvergence hardware, which makes it much simpler to
  implement.
- **RDNA 3** adds dual-issue vector ALUs, WMMA matrix instructions and a
  chiplet design: a graphics die plus memory dies holding the Infinity
  Cache.
- **RDNA 4** improves ray tracing and matrix (WMMA) throughput. Its ISA guide
  covers both RDNA 3.5 and RDNA 4.

### CDNA (compute) in one paragraph

CDNA drops the graphics pipeline (no rasteriser, no ROPs) and keeps GCN's
wave64 on 4 × SIMD16 per CU, plus **matrix cores**. CDNA 4 (MI350 series)
is built from chiplets: 8 compute dies (XCDs) of 32 active CUs each (256 CUs
total) on top of I/O dies holding the Infinity Cache and HBM controllers.

### What is public

Almost everything: **full ISA reference guides** with binary encodings for
every generation (GCN, RDNA 1-4, CDNA 1-4), machine-readable XML ISA specs,
architecture whitepapers, the ROCm documentation, and the open-source
kernel driver (`amdgpu`) and user-space drivers (Mesa RADV with the ACO
compiler). **AMD is by far the better reference when we need exact
details.**

## 1.4 Side-by-side

| Concept | NVIDIA | AMD RDNA | AMD CDNA / GCN |
|---|---|---|---|
| Thread group | warp, 32 | wave32 (or wave64 over 2 passes) | wave64 |
| Core | SM | WGP (2 CUs) | CU |
| Vector unit | 4 × 32-wide (as 16+16 lanes) per SM | 2 × SIMD32 per CU | 4 × SIMD16 per CU |
| Scalar unit | none (uniform datapath, partly) | yes, per SIMD | yes, per CU |
| Shared memory | shared mem / L1, 128-256 KB per SM | LDS, 128 KB per WGP | LDS, 64-160 KB per CU |
| Divergence | hardware, per-thread PC | compiler-managed `EXEC` mask | compiler-managed `EXEC` mask |
| Graphics cluster | GPC (raster engine + ROPs) | Shader Array (rasteriser + RBs) | none |
| Matrix units | tensor cores | WMMA on the vector ALUs (RDNA 3+) | matrix cores |
| Machine ISA public? | no (PTX only) | yes | yes |

## 1.5 Open GPU designs worth studying (small scale, like ours)

| Project | What it is | Why it is useful to us |
|---|---|---|
| **tiny-gpu** (adam-maj) | A minimal GPU in Verilog: a few cores, an 11-instruction ISA, warp-style execution | The smallest complete example: a scheduler, threads, and memory controllers |
| **Vortex** (Georgia Tech) | An open RISC-V GPGPU with warps, a SIMT stack, caches and texture units; runs OpenCL | A real SIMT design that runs real software; shows how far a RISC-V base can go |
| **MIAOW** (Wisconsin) | An open RTL implementation of an AMD Southern Islands (GCN) compute unit | Shows what a real AMD CU looks like in RTL |
| **Nyuzi** | An open GPGPU with a software-rendering approach | Shows the trade-off of doing graphics in shaders instead of fixed function |

---

# Part 2 - Decisions for minuscul_GPU

What we already have: a **fixed-function rasteriser** (the equivalent of
NVIDIA's raster engine + ROPs, or AMD's rasteriser + render backends) that
scales to N cores with a binner. We also have a **draft ISA** (VENUS) and
the start of a **single thread** (`threadV1`). No shader core exists yet.
The decisions below have to be made to go from that to a chip.

| # | Decision | Main options | Notes from Part 1 |
|---|---|---|---|
| 1 | **Purpose of the chip** | graphics only / graphics + compute / compute only | Decides whether the rasteriser stays at the centre (RDNA style) or is dropped (CDNA style) |
| 2 | **Where shading happens** | fixed-function only (flat colour, as now) / programmable pixel shader cores fed by the rasteriser | Every modern GPU uses programmable shading; fixed function is far simpler |
| 3 | **Core organisation** | 1 core with N lanes / several small cores (like SMs or CUs) | Our multi-core rasteriser already uses the "several cores, each owning screen strips" pattern |
| 4 | **Group width (warp size)** | 4 / 8 / 16 / 32 lanes | Real GPUs use 32 or 64; a small chip might pick 4-8 so a single core stays cheap |
| 5 | **Divergence handling** | none (all lanes always run the same path) / `EXEC` mask written by software (AMD) / hardware reconvergence stack (NVIDIA pre-Volta) | The software mask is the simplest real solution |
| 6 | **Scalar unit** | yes / no | AMD-style: cheap, saves energy and registers |
| 7 | **ISA** | keep VENUS (fix it first) / adopt RISC-V plus vector extensions (Vortex style) / subset of an AMD ISA (fully documented) | VENUS v1 and `threadV1` disagree (see `README_ISA_v1.md`) |
| 8 | **Latency hiding** | one group per core / several groups per core with a scheduler | Several groups per core is how GPUs stay busy, but each extra group needs its own registers |
| 9 | **Memory hierarchy** | flat memory per core (as now) / per-core shared memory + shared L2 / add caches | py4hw's one-driver-per-wire rule means any shared memory needs an arbiter |
| 10 | **Number format** | integer / fixed point / FP32 / FP16 | The rasteriser is fully integer today; the vertex stage needs either fixed point or floats |
| 11 | **Vertex stage** | done on the CPU / fixed function (our `MatrixMultiplier4x4` etc.) / run on the shader cores | Real GPUs run vertex work on the shader cores |
| 12 | **Display output** | software viewer only / framebuffer scan-out with video timing | Only needed if the chip drives a screen directly |

We go through these one at a time. The discussion for each one (options,
trade-offs, consequences) is logged in [decisions.md](decisions.md). Only
the final choice is recorded below.

## Final decisions

| # | Decision | Choice | Date | Details |
|---|---|---|---|---|
| D1 | Purpose of the chip | **B - Graphics with programmable shader cores** (rasteriser as fixed-function front end, SIMT cores for shading and compute) | 2026-10-15 | [decisions.md#d1](decisions.md#d1---purpose-of-the-chip) |
| D2 | Where shading happens | **Programmable, unified shader cores fed by the rasteriser** (follows from D1) | 2026-10-15 | [decisions.md#d2](decisions.md#d2---where-shading-happens) |
| D3 | Core organisation | **C - Slices**: each slice = rasteriser core + shader core + output stage + private framebuffer strip; slice count is a parameter, starting at 1 | 2026-10-15 | [decisions.md#d3](decisions.md#d3---core-organisation) |
| D4 | Thread group width | **C - 16 threads per group on 16 physical lanes** (`GROUP_WIDTH` parameter; one 4×4 tile = 1 group) | 2026-10-15 | [decisions.md#d4](decisions.md#d4---thread-group-width-lanes-per-core) |
| D5 | Divergence handling | **C - Software-managed 16-bit `EXEC` mask** (AMD style; initial mask = rasteriser coverage; assembler `IF`/`ELSE`/`ENDIF` macros) | 2026-10-15 | [decisions.md#d5](decisions.md#d5---divergence-handling) |
| D6 | Scalar unit | **B - Scalar ALU + SGPRs, no scalar loads** (`EXEC`/masks in SGPRs, scalar branches, SGPR broadcast to vector ops, constants preloaded by the dispatcher) | 2026-10-15 | [decisions.md#d6](decisions.md#d6---scalar-unit) |
| D7 | Instruction set | **A - VENUS v2**, our own ISA: fixed 32-bit format, `S_*`/`V_*` classes, implicit 16-bit `EXEC`, graphics instructions; AMD RDNA as the reference for behaviour; own Python assembler | 2026-10-15 | [decisions.md#d7](decisions.md#d7---instruction-set-isa) |
| D8 | Latency hiding | **B - `GROUP_SLOTS` per core (start 4), round-robin over ready groups**, one instruction in flight per group, exports in launch order | 2026-10-15 | [decisions.md#d8](decisions.md#d8---latency-hiding-groups-in-flight-per-core) |
| D9 | Memory hierarchy | **B - Shared global data memory + round-robin arbiter, no caches**; private instruction memory per core; private framebuffer strips; 512-bit lines with coalescing | 2026-10-15 | [decisions.md#d9](decisions.md#d9---memory-hierarchy) |
| D10 | Number format | **C - FP32** (IEEE-754, GPU-simplified: round-to-nearest-even, denormals flushed, no traps) on the vector unit; integers for addresses, masks and the scalar unit | 2026-10-15 | [decisions.md#d10](decisions.md#d10---number-format) |
| D11 | Vertex stage | **C - Vertex shaders on the cores + fixed-function primitive unit** (assembly, near-plane clip, divide, viewport, cull, setup → Binner); host does it until build step 5 | 2026-10-15 | [decisions.md#d11](decisions.md#d11---vertex-stage) |
| D12 | Display output | **B - On-chip display controller + simulated screen**: configurable timing (default 640×480 @ 60 Hz), dual-port front/back framebuffer strips, swap at vertical blanking | 2026-10-15 | [decisions.md#d12](decisions.md#d12---display-output) |
| D13 | VENUS v2 specification | **[README_ISA.md](README_ISA.md)**: 32 VGPRs, 31 SGPRs + `exec` (`s31`), 7 fixed 32-bit formats, word-addressed memory, descriptor-based launches with preloaded registers, Python assembler with macros | 2026-10-15 | [decisions.md#d13](decisions.md#d13---venus-v2-specification) |
| D14 | Near-plane handling | **Drop** triangles crossing the near plane, plus trivial frustum reject and a ±16383 guard band; proper clipping later | 2026-10-15 | [decisions.md#d14](decisions.md#d14---near-plane-handling-in-the-first-primitive-unit) |
| D15 | Vertex-output layout | **Array-of-structures records** (position in words 0-3, attributes after); indices and 1/w in triangle-line words 10-15; record addresses preloaded into `s0`-`s2` | 2026-10-15 | [decisions.md#d15](decisions.md#d15---vertex-output-layout-and-how-the-pixel-shader-finds-it) |

## Chosen architecture (summary of D1-D12)

```
 Host ── loads programs, vertex data, constants into global memory, starts a frame
   │
   ▼
 ┌─────────────────────── Pass 1: vertex ───────────────────────┐
 │ Shader cores run the vertex shader (one thread per vertex)   │
 │ → vertex outputs written to global memory                    │
 └──────────────────────────────┬───────────────────────────────┘
                                ▼
       Primitive unit (fixed): assembly, near-plane clip, divide,
       viewport, back-face cull, setup → triangle lines
                                ▼
                     Binner: triangle → slice(s)
                                ▼
 ┌──────────── Pass 2: pixel, one SLICE per screen strip ───────────┐
 │  Rasteriser core ──fragments (4×4 tile = 16-thread group,        │
 │        │            coverage mask = initial EXEC)                │
 │        ▼                                                         │
 │  Shader core: GROUP_SLOTS × 16-lane FP32 vector unit             │
 │               + integer scalar unit (SGPRs, EXEC, branches)      │
 │               + private instruction memory                       │
 │        │  exports in launch order                                │
 │        ▼                                                         │
 │  Output stage: depth test, colour write → back framebuffer strip │
 └──────────────────────────────────────────────────────────────────┘
        │ loads/stores (512-bit coalesced)          │ front strip (2nd port)
        ▼                                           ▼
 Round-robin arbiter → shared global memory    Display controller → screen
 (no caches)                                   (640×480 @ 60 Hz default,
                                                swap at vertical blanking)
```

**Parameters** (chosen values are starting points, not fixed):
`NUM_SLICES` (start 1) · `GROUP_WIDTH` = 16 · `GROUP_SLOTS` (start 4) ·
32 VGPRs + 32 SGPRs per group · `IMEM_WORDS` = 1024 per core ·
data-memory line = 512 bits · display timing (default 640×480 @ 60 Hz).

**Build order** (D1): ① fixed-function rasteriser *(done)* → ② rasteriser
emits fragments to a flat-colour unit + separate output stage → ③
programmable shader core (VENUS v2) → ④ compute launches → ⑤ vertex
shaders + primitive unit. The display controller (D12) and the shared
memory + arbiter (D9) are built when the steps first need them.
The implementation is split into work modules M1-M10 in
[README.md](README.md) (one folder per block).

**Open items:**
- **Physical target** (FPGA board): only needed for D12 option C; deferred
  by the project owner
- Later additions already identified (README_ISA.md §9): scalar loads,
  texture sampling, atomics/workgroups, hardware interpolation, packed
  FP16, proper near-plane clipping, sub-pixel precision

---

## Sources (all freely available)

**NVIDIA**
- NVIDIA Ampere GA102 GPU Architecture whitepaper - <https://www.nvidia.com/content/PDF/nvidia-ampere-ga-102-gpu-architecture-whitepaper-v2.pdf>
- NVIDIA Ada GPU Architecture whitepaper - <https://images.nvidia.com/aem-dam/Solutions/geforce/ada/nvidia-ada-gpu-architecture.pdf>
- NVIDIA RTX Blackwell PRO GPU Architecture (v1.1) - <https://www.nvidia.com/content/dam/en-zz/Solutions/design-visualization/quadro-product-literature/pdf/NVIDIA-RTX-Blackwell-PRO-GPU-Architecture-v1_1.pdf>
- CUDA C++ Programming Guide - <https://docs.nvidia.com/cuda/cuda-c-programming-guide/>
- PTX ISA - <https://docs.nvidia.com/cuda/parallel-thread-execution/>
- NVIDIA open GPU kernel modules - <https://github.com/NVIDIA/open-gpu-kernel-modules>
- Cornell Virtual Workshop, B200 SM - <https://cvw.cac.cornell.edu/gpu-architecture/horizon-gpus-blackwell-b200/b200_sm>
- "Microbenchmarking NVIDIA's Blackwell Architecture" (arXiv 2512.02189) - <https://arxiv.org/pdf/2512.02189>

**AMD**
- RDNA architecture whitepaper - <https://www.amd.com/system/files/documents/rdna-whitepaper.pdf>
- RDNA 4 ISA reference guide - <https://docs.amd.com/v/u/en-US/rdna4-instruction-set-architecture>
- Machine-readable ISA specs (all generations) - <https://gpuopen.com/machine-readable-isa/>
- GPUOpen RDNA resources - <https://gpuopen.com/rdna/>
- CDNA 4 architecture whitepaper - <https://www.amd.com/content/dam/amd/en/documents/instinct-tech-docs/white-papers/amd-cdna-4-architecture-whitepaper.pdf>
- ROCm docs, MI350 microarchitecture - <https://rocm.docs.amd.com/en/latest/reference/gpu-arch/mi350.html>
- Mesa (RADV / ACO / NVK drivers) - <https://docs.mesa3d.org/>

**Open designs**
- tiny-gpu - <https://github.com/adam-maj/tiny-gpu>
- Vortex RISC-V GPGPU - <https://github.com/vortexgpgpu/vortex>
- MIAOW - <https://github.com/VerticalResearchGroup/miaow>
- Nyuzi - <https://github.com/jbush001/NyuziProcessor>

Whitepaper PDF links sometimes move; search for the document title if a
link breaks.

# minuscul_GPU decision log

How each architecture decision was made: the question, the options, the
trade-offs, the consequences and the discussion. Once a decision is final,
it is recorded in the "Final decisions" section of
[Architecture..md](Architecture..md). This file keeps the reasoning.

Decision numbers match the table in Architecture..md, Part 2.

Status values: **open** (being discussed) · **decided** · **revisited**
(changed later; the old reasoning is kept, not deleted).

---

## D1 - Purpose of the chip

| | |
|---|---|
| Status | **decided** |
| Opened | 2026-10-15 |
| Decided | 2026-10-15 |

### Question

What workloads is minuscul_GPU for: drawing images (graphics), running
general parallel programs (compute), or both?

This comes first because almost every other decision depends on it. It
decides whether the rasteriser we already built is the centre of the chip,
a side unit, or unused. It also decides whether we need programmable cores
and an ISA at all.

### Starting point

- **Built and tested:** a fixed-function rasteriser (`renderer.py`). It
  takes screen-space triangles and writes one flat colour per triangle,
  with a depth test, across 1 to N cores.
- **Drafted, not running:** the VENUS ISA (now `README_ISA_v1.md`) and a
  single-thread interpreter (`legacy/Theadv1.py`).
- **Not started:** a vertex stage connected to the rasteriser, texturing,
  per-pixel shading, an instruction scheduler, any compiler or assembler.

### Options

**A. Fixed-function graphics only.**
The chip is a pipeline of dedicated hardware blocks: vertex transform →
rasteriser → depth test → pixel write, with optional fixed extras such as
Gouraud shading or texturing. There are no programmable cores.
*Real-world equivalent:* 3dfx Voodoo, the original GeForce 256 (1990s).

**B. Graphics with programmable shader cores** (modern GPU).
The rasteriser stays, but it hands each covered pixel to small programmable
cores that compute its colour with a program (a pixel shader). The same
cores later run vertex programs, and plain compute kernels too, since a
compute kernel is just a shader with no rasteriser in front of it.
*Real-world equivalent:* every GPU since the GeForce 8 / Radeon HD 2000
(2006-2007), today NVIDIA GeForce and AMD RDNA.

**C. Compute only (GPGPU).**
The chip is an array of programmable SIMT cores and nothing else. The
rasteriser is dropped or kept only as a reference model.
*Real-world equivalent:* AMD CDNA (Instinct), NVIDIA's datacenter parts
(mostly), tiny-gpu, Vortex.

**D. Compute cores that also do graphics in software.**
Like C in hardware, but graphics is still a goal: rasterisation, depth
test and shading are all programs running on the cores, with no
fixed-function graphics hardware.
*Real-world equivalent:* Intel Larrabee (cancelled as a graphics product),
Nyuzi.

### Trade-offs

| | A. Fixed-function graphics | B. Graphics + shader cores | C. Compute only | D. Software graphics on compute cores |
|---|---|---|---|---|
| Hardware complexity | low | high | medium | medium |
| Reuses the rasteriser | fully, it *is* the chip | fully, as the front end | no | no (only as a reference for the software version) |
| Reuses the ISA / `threadV1` | no | yes | yes | yes |
| Graphics speed per area | best for what it does | very good | none | poor: one fixed-function rasteriser does the work of many cores |
| Flexibility | fixed set of effects; every new effect needs new hardware | any effect, plus compute | any compute, no graphics | anything, but slow for graphics |
| Software needed | none (drive it with data) | assembler (later a compiler) plus shader programs | assembler plus kernels | assembler plus a full software renderer |
| Verification effort | low: compare the image with a reference | high: hardware *and* programs | medium | medium hardware, high software |
| Time to a first visible result | short (already partly there) | medium: works in fixed-function form first, then grows | medium: needs a working core before anything runs | long: needs cores *and* a renderer in software |
| How close to a real modern GPU | not close (1990s design) | very close | close to datacenter GPUs | an experimental dead end in industry |

### Consequences for the other decisions

- **A:** decisions 4-8 (thread group width, divergence, scalar unit, ISA,
  latency hiding) disappear. VENUS and `threadV1` are shelved. Effort goes
  into fixed hardware: vertex transform (11), texture units, and a
  shading block. Each new visual feature is a new hardware block.
- **B:** every decision in the table applies. Some new work appears:
  - The rasteriser must stop writing colour itself. It produces
    "fragments" (pixel position, depth, barycentrics) into a queue that the
    shader cores read.
  - The depth test and framebuffer write move into a separate output stage
    (NVIDIA and AMD call it ROPs / render backends).
  - The ISA needs graphics instructions: read interpolated attributes,
    sample a texture, emit a colour.
  - Compute support costs little extra: a way to launch threads without
    the rasteriser.
- **C:** the rasteriser is retired (kept as a model only). Decisions 2, 11
  and 12 disappear. The ISA and the core become the whole project, and
  "graphics" can at most be a demo kernel.
- **D:** same hardware decisions as C, plus a large software project (a
  renderer in VENUS assembly). The tile/binning ideas from the rasteriser
  carry over as algorithms, not hardware.

### Path that keeps options open

Option B doesn't have to be built all at once. It can be reached in steps,
and each step produces a working chip:

1. **(done)** Fixed-function rasteriser, flat colour: this is option A today.
2. Make the colour step replaceable: the rasteriser produces fragments,
   and a fixed "shade = flat colour" unit consumes them.
3. Replace that unit with a small programmable core running a pixel shader.
4. Let the same cores accept compute launches that don't go through the
   rasteriser.
5. Move vertex transform onto the cores.

Stopping after step 2 gives a good option A. Stopping after step 4 gives
option B.

### Recommendation (Claude)

**B, built in the steps above.** Reasons:
- It is the only option that uses both things already built: the
  rasteriser and the ISA work.
- It matches how NVIDIA and AMD graphics GPUs are actually organised
  (Architecture..md, Part 1).
- Compute support then costs almost nothing extra.
- Every step leaves a working, testable chip, so the risk stays low.

The real cost is that B is the most complex option: it pulls in all the
other decisions, and needs at least an assembler. If the goal is a chip
that can be finished quickly and kept simple, **A** is the honest
alternative.

### Discussion

- 2026-10-15: options and trade-offs written up; waiting for the project
  owner's choice.
- 2026-10-15: project owner chose **B**.
- 2026-10-15: project owner confirmed the step-by-step build order.

### Outcome

**B - Graphics with programmable shader cores.**

The rasteriser stays as the fixed-function front end. Pixel colour (and
later vertex work and compute kernels) is computed by programmable SIMT
cores. Consequences that now apply:
- every remaining decision (D2-D12) is relevant
- the rasteriser must be changed to hand out fragments through a queue
  instead of writing colour itself
- the depth test and framebuffer write become a separate output stage
- VENUS and `threadV1` stay in the project and must be fixed and
  reconciled (see `README_ISA_v1.md`)
- an assembler is needed before the first shader can run

Build order (confirmed 2026-10-15): the step path above - fragments →
flat-colour unit → programmable core → compute launch → vertex on cores.

---

## D2 - Where shading happens

| | |
|---|---|
| Status | **decided** (follows from D1) |
| Opened | 2026-10-15 |
| Decided | 2026-10-15 |

### Question

Does pixel colour come from fixed-function hardware or from programmable
shader cores fed by the rasteriser?

### Outcome

**Programmable shader cores fed by the rasteriser.** Choosing D1 = B already
answered this, so there was no separate discussion. Also implied by D1's
build order: the cores are **unified**. The same cores run pixel shaders,
compute kernels and (later) vertex shaders, as on every GPU since 2006,
rather than separate vertex and pixel units.

---

## D3 - Core organisation

| | |
|---|---|
| Status | **decided** |
| Opened | 2026-10-15 |
| Decided | 2026-10-15 |

### Question

How are the programmable cores arranged on the chip, and how do they
connect to the rasteriser and to the framebuffer?

This is about the *structure*, not the *number* of cores. Every option
below can be built with the core count as a parameter, starting at 1. What
changes between options is who talks to whom, which decides what
interconnect we need and how work is split.

### Starting point

- `MultiCoreRenderModule` already splits the screen into horizontal strips,
  one per rasteriser core. A `Binner` sends each triangle only to the cores
  whose strip it touches. Each core has a private framebuffer strip.
- This works without any bus arbiter because no two cores ever share a
  memory. py4hw forbids two drivers on one wire, so any *shared* memory
  needs an explicit arbiter module.
- Measured (RASTERISER_ARCHITECTURE.md #11): splitting by strips scales
  well up to ~6-12 cores, then efficiency drops. Uneven strips cause some
  load imbalance (20 cores was slower than 16 on the test scene).

### Options

**A. One shader core with N lanes.**
A single programmable core (one scheduler, one register file, N SIMD
lanes). All rasteriser cores send their fragments to it.
*Real-world equivalent:* tiny-gpu, very small embedded GPUs.

**B. Pool of identical shader cores, shared by all rasterisers.**
Several shader cores (each like a small NVIDIA SM / AMD CU). A distributor
sends each batch of fragments, or each compute workgroup, to whichever core
is free. Cores reach the framebuffer and memory through a shared
interconnect.
*Real-world equivalent:* NVIDIA's GigaThread engine dispatching to SMs for
compute; the general shape of most GPUs at the core level.

**C. Slices: each rasteriser core paired with its own shader core and
framebuffer strip.**
Extend today's multi-core design: one *slice* = rasteriser core + shader
core + output stage (depth test, write) + private framebuffer strip. The
Binner keeps feeding slices. Fragments never leave their slice.
*Real-world equivalent:* AMD's Shader Array and NVIDIA's GPC (each cluster
has its own raster engine, its own cores and its own ROPs), and tile-based
mobile GPUs, where each core owns screen tiles.

**D. Clusters: each slice holds several shader cores.**
Same as C, but inside each slice, one rasteriser feeds a small pool of
shader cores (like B inside each slice). This two-level structure is how
large GPUs are really built (a GPC holds many SMs).

### Trade-offs

| | A. One core | B. Shared pool | C. Slices | D. Clusters |
|---|---|---|---|---|
| Hardware complexity | lowest | high | medium | highest |
| Interconnect needed for graphics | many-to-one fragment queue | crossbar between rasterisers, cores and framebuffer | none (everything stays inside a slice) | small, local to each cluster |
| Reuses `MultiCoreRenderModule` + `Binner` | partly (rasterisers only) | partly | fully (it becomes the slice array) | fully |
| Shading throughput scaling | doesn't scale: one core is the bottleneck | best: any core can take any work | scales with slice count | best of both |
| Load balance | n/a | very good (dynamic) | depends on screen split (strips are uneven) | good inside a slice, strip-dependent between slices |
| Compute kernels | simple: one core | natural fit | needs a launch path to every slice's core | needs a launch path to every cluster |
| Shared memory (textures, compute data) | one port, simple | needs an arbiter | needs an arbiter | needs an arbiter |
| py4hw simulation speed | fastest | slower (more modules and wires) | medium | slowest |
| Closeness to real GPUs | low | medium | high (GPC / Shader Array) | highest |

Two things are true whatever we pick:
- **Shared memory needs an arbiter eventually.** Textures and compute
  buffers are read by every core, so a shared memory and an arbiter come
  with D9 in every option. Option C only postpones it for the framebuffer.
- **Lane count per core is a separate decision** (D4, thread group width).

### Consequences

- **A:** simple to build and verify, but the single core limits
  performance. Moving to B or C later means redesigning how fragments are
  distributed.
- **B:** needs a fragment/work distributor and a crossbar from day one. The
  framebuffer becomes shared, so the output stage needs ordering rules:
  two cores may write the same pixel, and draw order must be respected.
- **C:** extends what exists. Each slice's output stage writes only its
  own framebuffer strip, so pixel ordering is automatic (one slice owns
  each pixel). Main risks: load imbalance between strips, which can later be
  fixed by interleaving tiles instead of strips; and the compute launch
  path must reach every slice.
- **D:** C plus a small distributor inside each slice. Possible as a later
  step: start as C with one shader core per slice, then raise it.

### Recommendation (Claude)

**C (slices), designed so a slice can later hold several shader cores (D).**
Start with 1 slice and 1 shader core per slice. Reasons:
- It is the direct continuation of the working `MultiCoreRenderModule` /
  `Binner` design, and keeps its biggest advantage: no shared framebuffer,
  hence no arbiter needed for graphics, which matters with py4hw's
  single-driver rule.
- One slice owns each pixel, so the output stage never has to resolve two
  cores writing the same pixel out of order.
- It mirrors the GPC / Shader Array structure from Architecture..md Part 1.
- With 1 slice it is effectively option A, so the first shader core can
  be developed without any distribution logic, and the core count grows
  later without redesign.

The cost: compute launches and shared texture/data memory will still need
an arbiter later (D9). Strip-based splitting may also need to become
interleaved tiles for better balance.

### Discussion

- 2026-10-15: options and trade-offs written up; waiting for the project
  owner's choice.
- 2026-10-15: project owner chose **C**.

### Outcome

**C - Slices.** One slice = rasteriser core + shader core + output stage
(depth test, framebuffer write) + private framebuffer strip, fed by the
`Binner`. The slice count is a parameter; development starts with 1 slice
and 1 shader core.

Consequences that now apply:
- the existing `MultiCoreRenderModule` becomes the slice array; each
  `RenderModule` core gains a fragment output, a shader core and an output
  stage
- no arbiter is needed for the framebuffer; each pixel is owned by exactly
  one slice, so draw order is preserved automatically
- shared memory (textures, compute buffers) and the compute launch path to
  every slice remain open, to be settled in D9
- several shader cores per slice (option D) is possible later but was
  not decided here

---

## D4 - Thread group width (lanes per core)

| | |
|---|---|
| Status | **decided** |
| Opened | 2026-10-15 |
| Decided | 2026-10-15 |

### Question

How many threads run one instruction together in a shader core? NVIDIA
calls this group a *warp* (32 threads), AMD a *wave* (32 or 64). Inside a
group, every thread executes the same instruction at the same time on its
own data.

A second, related question: is the group as wide as the number of physical
ALU lanes (one instruction per cycle), or wider (one instruction spread
over several cycles, as on AMD GCN)?

### Starting point

- The rasteriser tests **16 pixels at a time** (`PIXEL_LANE_COUNT = 16`).
  Its smallest tile is **4×4 = 16 pixels**, and the framebuffer can be split
  into **16 banks**.
- Pixel shaders are normally run on **2×2 quads**. The differences between
  the 4 pixels of a quad give the screen-space derivatives that texture
  mipmapping needs. So the group width should be a multiple of 4.
- VENUS has 16 registers per thread (32-bit).

### Options

**A. 4 threads** (one 2×2 quad per group).
**B. 8 threads** (two quads).
**C. 16 threads** (four quads = one 4×4 tile).
**D. 32 threads** (the industry standard: NVIDIA warps, AMD RDNA wave32).
**E. 16-thread groups on 4 physical lanes, one instruction over 4 cycles**
(AMD GCN style: wave64 on SIMD16). Same programming model as C, a quarter
of the ALUs.

### Trade-offs

| | A. 4 | B. 8 | C. 16 | D. 32 | E. 16 on 4 lanes |
|---|---|---|---|---|---|
| ALUs per core | 4 | 8 | 16 | 32 | 4 |
| Instruction fetch/decode cost per useful operation | high (one decode per 4 ops) | medium | low | lowest | low |
| Lanes wasted on divergent branches | least | low | medium | most | medium |
| Lanes wasted on small triangles (partial groups) | least | low | medium | high | medium |
| Registers per group (16 regs × 4 B × width) | 256 B | 512 B | 1 KB | 2 KB | 1 KB |
| Match with the rasteriser (16 lanes, 4×4 tiles, 16 banks) | needs 4 groups per tile | needs 2 per tile | **exact** | needs 2 tiles per group | exact |
| Throughput per core | low | medium | high | highest | low (= A) |
| Hides ALU pipeline latency by itself | no | no | no | no | yes (4 cycles per instruction) |

About wasted lanes on small triangles: a group is usually filled with
quads from one triangle. A triangle that covers only one quad leaves the
rest of the group idle. Real GPUs reduce this by packing quads from
several triangles into one group. That's an extra feature, but it works
with any width.

### Consequences

- The width sets the size of the **execution mask** (D5, divergence), the
  **register file** (width × registers × groups in flight, see D8), and
  how many memory accesses one load instruction makes (D9, coalescing).
- Fragment batching between the rasteriser and the shader core is
  simplest when one batch = one group.
- Changing the width later touches the core, the register file and the
  fragment interface, so it is best treated as a parameter of the design
  from the start, even if only one value is used.

### Recommendation (Claude)

**C, 16 threads, with 16 physical lanes**, written as a design parameter.
Reasons:
- It matches what the rasteriser already produces: one 4×4 tile = 4 quads
  = 1 group, using the same 16 lanes and 16 framebuffer banks. No
  re-packing is needed between rasteriser and shader core.
- It is a multiple of 4, so quad derivatives work.
- 16 is half of the industry's 32, a reasonable size for a small chip:
  wide enough to amortise instruction decode, narrow enough that
  divergence and small triangles waste less.

If area or simulation speed becomes a problem, **E** keeps the exact same
programming model with 4 physical lanes, so switching to it later doesn't
change any software.

### Discussion

- 2026-10-15: options and trade-offs written up; waiting for the project
  owner's choice.
- 2026-10-15: project owner chose **C**.

### Outcome

**C - 16 threads per group, on 16 physical lanes** (one instruction per
cycle for the whole group). The width is a design parameter (`GROUP_WIDTH`),
not hard-coded.

Consequences that now apply:
- one 4×4 rasteriser tile = 4 quads = 1 thread group
- execution masks are 16 bits (D5)
- register storage is 1 KB per group (16 regs × 4 B × 16 threads) before
  counting groups in flight (D8)
- switching to option E (16 threads on 4 lanes) later stays possible
  without software changes

---

## D5 - Divergence handling

| | |
|---|---|
| Status | **decided** |
| Opened | 2026-10-15 |
| Decided | 2026-10-15 |

### Question

The 16 threads of a group share one instruction stream. What happens when
an `if` is true for some threads and false for others (a *divergent*
branch)?

### Starting point

- **Partial groups exist even without branches.** When a 4×4 tile is only
  partly covered by a triangle, the uncovered pixels' lanes still run
  (they are needed as quad neighbours for texture derivatives, so-called
  *helper* lanes) but must not write to the framebuffer. So every option
  needs at least a 16-bit **active/coverage mask** that the rasteriser fills
  in.
- VENUS today branches on **flags** (zero / lt / ge), and `threadV1` keeps
  flags per thread. With 16 threads, "jump if zero" has no single answer
  when the flags differ between threads, so the ISA needs to change
  whatever we pick (D7).
- There is no compiler yet. Shaders will be written in assembly, so
  whoever writes shaders will see the divergence mechanism.

### Options

**A. Uniform branches only.**
Branch conditions must be the same for all 16 threads (for example a loop
counter). Per-thread choices are made with a `SELECT` instruction
(`r = cond ? a : b`) that computes both values. The hardware only keeps
the coverage mask for writes.
*Real-world equivalent:* early shader hardware (DirectX 9 era pixel
shaders).

**B. Predication.**
Every instruction can be guarded by a per-thread predicate. Both sides of
an `if` are always executed, each with the opposite predicate. There are no
real jumps for divergent code.
*Real-world equivalent:* predicate registers on NVIDIA (`@p` in PTX/SASS),
and most DSPs.

**C. Execution mask managed by software (AMD style).**
A 16-bit `EXEC` register says which lanes are active. A compare
instruction writes a 16-bit result mask; ordinary instructions save,
AND and restore `EXEC` around each side of the `if`. A branch skips a side
completely when its mask is all-zero (`branch if EXEC == 0`).
*Real-world equivalent:* AMD GCN, RDNA and CDNA (`s_and_saveexec`,
`s_cbranch_execz`), fully documented in AMD's public ISA guides.

**D. Hardware reconvergence stack.**
A divergent branch pushes (mask, reconvergence address) on a small
per-group hardware stack. The hardware runs one side, then the other, then
pops the stack and reunites the threads at the reconvergence point. The
reconvergence point is marked by the assembler (e.g. `SPLIT` / `JOIN`
instructions).
*Real-world equivalent:* NVIDIA before Volta (2017); Vortex (`split`/`join`).

**E. Independent thread scheduling.**
Every thread has its own program counter. The scheduler regroups threads
that are at the same PC.
*Real-world equivalent:* NVIDIA Volta and later.

### Trade-offs

| | A. Uniform only | B. Predication | C. EXEC mask (software) | D. Hardware stack | E. Per-thread PC |
|---|---|---|---|---|---|
| Hardware cost | lowest (coverage mask only) | low (predicate regs + masked write) | low (16-bit EXEC + mask ops + masked write) | medium (stack per group + control) | high (16 PCs, regrouping logic) |
| Divergent `if` in a shader | not possible, must rewrite as `SELECT` | always runs both sides | runs both sides, skips a side if no lane needs it | runs both sides, skips a side if no lane needs it | runs both sides, can interleave |
| Divergent loops (different trip counts) | no | awkward | yes | yes | yes |
| Nested `if`s | n/a | manageable | yes (save masks in registers) | yes (up to stack depth) | yes |
| What the assembly programmer sees | `SELECT` only | predicates on every instruction | explicit mask save/restore (can be hidden by assembler macros `IF`/`ELSE`/`ENDIF`) | `SPLIT`/`JOIN` markers | ordinary branches |
| Public, detailed reference | - | NVIDIA PTX docs | AMD ISA guides (exact encodings) | papers, Vortex source | patents and papers only |
| Verification effort | lowest | low | low-medium | medium | high |

### Consequences

- **A:** shaders are restricted (no per-pixel loops, no early-out paths).
  Moving to C or D later means adding mask logic and new instructions.
- **B:** every instruction encoding needs predicate bits, taken from
  VENUS's 32-bit format (D7).
- **C:** VENUS gets: compare → mask instructions, mask logic
  (AND/OR/NOT/save) and `branch if EXEC == 0`. The mask naturally lives in a
  scalar register, which ties this to D6 (scalar unit). Without a scalar
  unit it needs its own small mask register file.
- **D:** needs a stack sized for the deepest nesting allowed. Overflow
  must be handled (an error or a spill to memory).
- **E:** the scheduler (D8) becomes much more complex.
- **All options:** the rasteriser hands the initial coverage mask to the
  shader core with each group, and the output stage writes only lanes
  that are covered *and* active.

### Recommendation (Claude)

**C, the software-managed EXEC mask**, with `IF`/`ELSE`/`ENDIF` macros in
the assembler so shader code stays readable. Reasons:
- The hardware is about as cheap as it gets: one 16-bit register, a few
  mask instructions and masked register writes. The coverage mask we need
  anyway is just the initial `EXEC` value.
- It handles everything real shaders do (nested `if`s, per-pixel loops,
  early-out) with no hard nesting limit.
- AMD documents it completely and publicly, so we can copy proven
  instruction semantics instead of inventing them.
- It fits a scalar unit naturally (D6), but works without one.

D is the main alternative if you prefer the hardware to manage
reconvergence and assembly to look like ordinary branches. It costs a
hardware stack and a nesting limit.

### Discussion

- 2026-10-15: options and trade-offs written up; waiting for the project
  owner's choice.
- 2026-10-15: project owner chose **C**.

### Outcome

**C - Software-managed execution mask (AMD style).** Each group has a
16-bit `EXEC` register; lanes with a 0 bit don't write results. Compare
instructions produce 16-bit masks; mask instructions save, combine and
restore `EXEC`; a branch skips code when `EXEC == 0`. The assembler provides
`IF`/`ELSE`/`ENDIF` macros that expand into these instructions.

Consequences that now apply:
- the rasteriser hands each group its 16-bit coverage mask, which becomes
  the initial `EXEC`
- the output stage writes only lanes that are covered *and* active
- VENUS's flag-based branches are replaced: per-thread conditions become
  masks, and real branches only test values shared by the whole group
  (D7)
- where `EXEC` and saved masks live depends on D6 (scalar unit)

---

## D6 - Scalar unit

| | |
|---|---|
| Status | **decided** |
| Opened | 2026-10-15 |
| Decided | 2026-10-15 |

### Question

Should each shader core have a **scalar unit**: a small register file plus
a 1-lane ALU, next to the 16-lane vector unit, for values that are the same
for every thread in a group?

### Why it matters

Many values in a shader are identical for all 16 threads: loop counters,
the base address of a buffer, a light direction, the `EXEC` mask itself.
Without a scalar unit, each one is stored 16 times (64 bytes instead of 4)
and computed 16 times. AMD has used a scalar unit since GCN (2012). NVIDIA
added a smaller version, the *uniform datapath*, in Turing (2018).

### Starting point

- D5 = C: the `EXEC` mask, the masks saved around nested `if`s, and the
  "skip if `EXEC == 0`" branch are all per-group values. They need to live
  somewhere.
- Only per-group values can steer a real branch now (D5), so the branch
  logic works on group-wide values in any case.
- Shaders are written in assembly, so the programmer chooses scalar or
  vector registers explicitly (AMD assembly does this: `s0` vs `v0`).

### Options

**A. No scalar unit.**
Everything runs on the 16-lane vector unit. `EXEC` and saved masks live
in a small dedicated mask register file (e.g. 4-8 × 16-bit). Uniform values
are stored in vector registers, duplicated in every lane. Branches test
masks or lane 0 of a vector register.

**B. Scalar unit: scalar registers + scalar ALU, no scalar memory access.**
Each group gets scalar registers (SGPRs, e.g. 16 × 32-bit). `EXEC` and
saved masks are just SGPRs. A 1-lane ALU does integer add/sub, logic,
shifts, compares and mask operations; the branch unit reads SGPRs and a
scalar condition bit. Vector instructions can read an SGPR as an operand
(broadcast to all lanes). Constants and pointers are **preloaded into
SGPRs by the dispatcher** when a group starts, as AMD does with its "user
SGPRs".
*Real-world equivalent:* AMD SALU without scalar loads; close to NVIDIA's
uniform datapath.

**C. Full scalar unit: B plus scalar memory loads.**
Adds `S_LOAD` instructions that read constants from memory into SGPRs
once per group, through a small scalar data cache.
*Real-world equivalent:* AMD GCN/RDNA/CDNA (`s_load_dword`, scalar cache).

### Trade-offs

| | A. None | B. Scalar ALU + SGPRs | C. B + scalar loads |
|---|---|---|---|
| Extra hardware | small mask register file | SGPR file (16 × 32 bit per group in flight) + 1-lane ALU | B + scalar memory port and cache |
| Storage for a uniform value | 64 B (16 lanes × 4 B) | 4 B | 4 B |
| Energy/work for a uniform op | 16 ALU ops | 1 ALU op | 1 ALU op |
| Where `EXEC` / saved masks live | special mask registers | ordinary SGPRs | ordinary SGPRs |
| Loop counters, branch conditions | in vector regs, branch reads lane 0 (awkward) | natural | natural |
| Getting constants into a shader | vector load, every lane reads the same address | preloaded by dispatcher (limited by SGPR count) | preloaded or loaded on demand |
| ISA impact | vector-only instruction set | two instruction classes (scalar / vector) | same as B + scalar load instructions |
| Memory system impact (D9) | none | none | a second memory client to arbitrate |
| Verification effort | lowest | medium | medium-high |

Issue rate: the simplest design issues **one instruction per cycle, scalar
or vector**. AMD issues one of each per cycle from different groups; that's
an optimisation that can come later and doesn't change the ISA.

### Consequences

- **A:** VENUS stays a single register class plus mask registers. Uniform
  data costs 16× the storage, which reduces how many groups fit in the
  register file (D8).
- **B / C:** VENUS splits into scalar (`S_...`) and vector (`V_...`)
  instructions with an `s`/`v` register namespace, and all branches become
  scalar instructions. This lines up with D5 (only group-wide values steer
  branches). The dispatcher (the link from rasteriser/launcher to the core)
  must be able to write the initial SGPRs: coverage mask into `EXEC`, tile
  position, constant pointers.
- **C** only: one more memory client for D9.

### Recommendation (Claude)

**B: scalar ALU + SGPRs, constants preloaded by the dispatcher**, leaving
scalar loads (C) for later if preloading proves too limiting. Reasons:
- After D5 = C, the core already needs per-group registers and logic for
  masks and branches. B is that same hardware, generalised into a proper
  unit, so it costs little beyond what D5 requires.
- 16× less storage and work for uniform values, which matters with a
  small register file.
- It follows AMD's public ISA closely (SALU, `EXEC` in SGPRs, user SGPRs),
  so semantics can be copied, not invented.
- No new memory client, so D9 stays simpler. Adding C later only adds
  instructions; it doesn't change any existing ones.

### Discussion

- 2026-10-15: options and trade-offs written up; waiting for the project
  owner's choice.
- 2026-10-15: project owner chose **B**.

### Outcome

**B - Scalar ALU + scalar registers (SGPRs), no scalar memory loads.**
Each group has its own SGPRs; `EXEC` and saved masks are SGPRs; the scalar
ALU does integer, logic, compare and mask operations; all branches are
scalar instructions; vector instructions can read an SGPR as a broadcast
operand. Constants and pointers are written into SGPRs by the dispatcher
when a group starts.

Consequences that now apply:
- the ISA has two instruction classes, scalar and vector, with separate
  `s` and `v` register names (D7)
- the dispatcher writes the initial SGPRs: coverage mask → `EXEC`, tile
  position, constant pointers
- one instruction (scalar or vector) issues per cycle at first
- scalar memory loads (option C) can be added later without changing
  existing instructions

---

## D7 - Instruction set (ISA)

| | |
|---|---|
| Status | **decided** |
| Opened | 2026-10-15 |
| Decided | 2026-10-15 |

### Question

Which instruction set do the shader cores run, and with which tools are
programs written?

### Requirements set by earlier decisions

Whatever we pick must provide:
- **two instruction classes:** scalar (1 lane, SGPRs) and vector (16 lanes,
  VGPRs), where vector instructions can take an SGPR operand (D6)
- **an implicit 16-bit `EXEC` mask** on every vector instruction, compares
  that produce masks, mask save/restore operations, and a "branch if
  `EXEC == 0`" (D5)
- **branches only on scalar values** (D5, D6)
- **graphics instructions** for the shading path: read interpolated
  attributes, sample a texture (later), export a colour to the output
  stage (D1)
- a decoder simple enough to build and verify in py4hw

The current VENUS draft meets none of the first three. It is one register
class with per-thread flags, and `threadV1` disagrees with the spec on 8
opcodes (now README_ISA_v1.md). Some rework is needed in every option.

### Options

**A. Redesigned VENUS ("VENUS v2"), our own ISA.**
Keep the name and the fixed 32-bit instruction format. Rebuild the
instruction list around D5/D6: `S_*` scalar and `V_*` vector instructions,
`EXEC`, masks, graphics exports. Copy *semantics* from AMD's public RDNA ISA
guide where they fit, so we don't invent behaviour. We write our own
assembler (Python).

**B. RISC-V base + vector extension (RVV) subset.**
The scalar unit runs RV32I (the standard 32-bit integer RISC-V). The vector
unit runs a subset of RVV with 16 × 32-bit elements, mask register `v0`, plus
custom instructions for graphics.
*Real-world equivalent:* many RISC-V AI/vector chips. Vortex uses RISC-V
too, but with per-thread SIMT and `split`/`join` (D5 option D), not RVV.

**C. Subset of AMD's RDNA ISA, with AMD's exact encodings.**
Implement a chosen subset of real RDNA instructions (SOP*, VOP*, branches,
`EXEC`), bit-for-bit compatible.
*Real-world equivalent:* MIAOW did this for GCN (Southern Islands).

### Trade-offs

| | A. VENUS v2 | B. RISC-V + RVV subset | C. RDNA subset |
|---|---|---|---|
| Fits D4-D6 (16 lanes, `EXEC`, SGPR/VGPR) | exactly, designed for it | partly: RVV masks are explicit per instruction (`v0.t`), not an implicit `EXEC`; vector length is configurable at runtime | mostly: RDNA has `EXEC`/SGPR/VGPR, but is built for 32 lanes (wave32), so our 16 lanes don't match its mask width |
| Encoding complexity | low (fixed 32-bit, we choose fields) | medium (RV32I simple; RVV large: `vtype`, `vl`, LMUL, SEW) | high (many formats, 32/64-bit instructions, inline literals) |
| Decoder in py4hw | small | medium | large |
| Assembler | must write our own (a few hundred lines of Python) | free: GNU and LLVM assemblers | free: LLVM `llvm-mc` for AMDGPU targets |
| Compiler (C or shader language) | none, unless we build one | GCC/LLVM for the scalar part; RVV intrinsics for vector | in principle LLVM, but compiled code assumes AMD's full runtime, memory model and wave size, so not usable as-is |
| Graphics instructions | add freely | custom extension space exists | must fit AMD's encodings, or break compatibility |
| Documentation source | ours (semantics borrowed from AMD's public guide) | public RISC-V specs | AMD's public ISA guides |
| Uses existing VENUS / `threadV1` work | yes (rework) | no | no |
| Learning value / originality | highest: we design it | medium | medium: we copy a real design |

### Consequences

- **A:** README_ISA.md is rewritten as the VENUS v2 spec, `threadV1` is
  replaced by a group-wide core, and a Python assembler (with the D5
  `IF`/`ELSE`/`ENDIF` macros) becomes a project deliverable.
- **B:** we get a standard toolchain, but D5's implicit `EXEC` has to be
  emulated (every vector instruction masked by `v0`, plus custom mask
  instructions). The vector unit must implement enough of RVV's
  configuration model to stay compatible.
- **C:** to stay bit-compatible we would likely run AMD's 32-wide waves on
  our 16 lanes over 2 cycles, which reopens D4. The decoder is the largest
  of the three.

### Recommendation (Claude)

**A, VENUS v2**, with AMD RDNA as the semantic reference. Reasons:
- It is the only option that fits D4-D6 exactly (16 lanes, implicit
  16-bit `EXEC`, SGPR/VGPR split) without emulation or reopening D4.
- A fixed 32-bit format keeps the py4hw decoder small and easy to verify.
- A Python assembler is a small, well-understood task. Without a compiler,
  every option needs hand-written assembly anyway, so B's and C's free
  assemblers save less than they first seem to.
- Graphics instructions can be added where they fit best.

**C** is the alternative if a real, external toolchain matters more than
fit and simplicity. **B** makes most sense if the scalar unit should one
day run ordinary C code.

### Discussion

- 2026-10-15: options and trade-offs written up; waiting for the project
  owner's choice.
- 2026-10-15: project owner chose **A**.

### Outcome

**A - VENUS v2, our own ISA.** Fixed 32-bit instruction format; `S_*`
scalar and `V_*` vector instruction classes with `s`/`v` registers;
implicit 16-bit `EXEC` on vector instructions; mask and scalar-branch
instructions; graphics instructions (attribute read, colour export, later
texture sample). AMD's public RDNA ISA guide is the reference for
instruction *behaviour* wherever it fits; encodings are our own.

Consequences that now apply:
- README_ISA.md is rewritten as the VENUS v2 specification (register
  counts, encoding formats and the instruction list are decided there)
- `threadV1` is replaced by a 16-lane group core with a scalar unit
- a Python assembler, including the `IF`/`ELSE`/`ENDIF` macros from D5,
  becomes a project deliverable

---

## D8 - Latency hiding (groups in flight per core)

| | |
|---|---|
| Status | **decided** |
| Opened | 2026-10-15 |
| Decided | 2026-10-15 |

### Question

How many thread groups does a shader core hold at once, and how does it
choose which one to run each cycle?

GPUs don't use big caches or out-of-order execution to hide slow memory.
They keep many groups loaded, and when one is waiting, they issue an
instruction from another one on the next cycle. The cost is registers:
every group in flight needs its own full set.

### Starting point

- Per group (from D4, D6, and VENUS v2 with an assumed 16 VGPRs + 16 SGPRs;
  the exact counts are fixed in the VENUS v2 spec):
  16 VGPRs × 16 lanes × 4 B = **1 KB**, plus 16 SGPRs × 4 B = **64 B**.
- In the current py4hw model, memory answers in about **2 cycles**, much
  faster than real DRAM (hundreds of cycles). Latency will grow once memory
  is shared through an arbiter (D9) and textures are added. So in our
  simulation, more groups in flight help less than on a real chip, until
  memory gets more realistic.
- A second, very practical reason for several slots: they act as a
  **buffer between the rasteriser and the core**. The rasteriser can hand
  over the next tile while the previous one is still being shaded, instead
  of waiting.

### A correctness issue every option above A must solve

Two triangles can cover the same tile, and the later triangle's group can
finish first. Blending, and depth tests on equal depths, depend on draw
order. So with more than one group in flight, the output stage must
**write results in launch order**, or the dispatcher must not launch a
group for a tile that already has one in flight (*tile interlock*). Real
GPUs do both kinds of thing.

### Options

**A. One group per core.**
The core runs one group to completion, stalling whenever it waits on
memory.
*Real-world equivalent:* tiny-gpu; the simplest designs.

**B. Several group slots, switch every cycle (fine-grained).**
N slots (e.g. 4). Each cycle, the scheduler picks the next *ready* group in
round-robin order. A group is not ready while it waits for memory. Each
group has at most one instruction in flight, so there are no register
hazards to track.
*Real-world equivalent:* barrel processors, the basic GPU warp scheduler.

**C. Several group slots, switch only on a stall (coarse-grained).**
Keep running the same group until it has to wait, then switch.
*Real-world equivalent:* some multithreaded CPUs and simple GPU designs.

**D. Several group slots + scoreboard.**
Like B, but a group may issue its next instruction before the previous one
finishes, as long as it doesn't need that result. A *scoreboard* tracks
which registers are still being written. Usually paired with a "greedy
then oldest" policy.
*Real-world equivalent:* NVIDIA and AMD shader cores.

### Trade-offs

| | A. 1 group | B. N slots, round-robin | C. N slots, switch on stall | D. N slots + scoreboard |
|---|---|---|---|---|
| Register storage (4 slots shown) | 1.06 KB | 4.25 KB | 4.25 KB | 4.25 KB |
| Scheduler logic | none | small (ready bits + round-robin pointer) | small | larger (scoreboard per group) |
| Hides memory latency | no | yes | yes | yes |
| Hides ALU pipeline latency (if the ALU becomes multi-cycle) | no | yes, if N ≥ ALU depth | no | yes, even with few groups |
| Buffers the rasteriser → core handoff | no | yes | yes | yes |
| Output ordering logic needed | no | yes | yes | yes |
| Verification effort | lowest | low-medium | low-medium | medium-high |
| Benefit in today's simulation (2-cycle memory) | baseline | mostly from buffering | mostly from buffering | little beyond B |

### Consequences

- **A:** simplest core. But the rasteriser stalls whenever the core is
  busy, and every memory access stalls the core. Upgrading to B later
  means adding slots, ready bits and output ordering.
- **B, C, D:** the register file is sized as N × per-group size. The
  dispatcher needs a free-slot check, and the output stage needs in-order
  export or a tile interlock.
- **D:** only pays off once instructions take more than one cycle (a
  pipelined ALU, or several loads in flight per group).

### Recommendation (Claude)

**B, with the slot count N as a parameter (start at 4), round-robin over
ready groups, one instruction in flight per group, and exports written in
launch order.** Reasons:
- With N = 1 it *is* option A, so the first core can be brought up
  without the scheduler mattering, then N raised.
- The slots buffer the rasteriser → core handoff, a real gain even with
  today's fast simulated memory.
- It hides the memory and arbiter latency that D9 will introduce.
- One instruction in flight per group means no scoreboard, keeping
  verification small. A scoreboard (D) can be added later if the ALU gets
  pipelined, with no ISA change.
- Writing exports in launch order is simple to build and guarantees
  correct draw order.

### Discussion

- 2026-10-15: options and trade-offs written up; waiting for the project
  owner's choice.
- 2026-10-15: project owner chose **B**.

### Outcome

**B - N group slots per core, round-robin over ready groups.** The slot
count is a parameter (`GROUP_SLOTS`, starting value 4; 1 behaves like option
A). Each group has at most one instruction in flight, so no scoreboard is
needed. A group is not ready while it waits on memory. Exports reach the
output stage in launch order, which keeps draw order correct.

Consequences that now apply:
- register storage per core = `GROUP_SLOTS` × (VGPRs + SGPRs of one group),
  about 4.25 KB at 4 slots with 16 + 16 registers
- the dispatcher only launches a group when a slot is free; otherwise the
  rasteriser waits
- the output stage needs in-order export (e.g. a small reorder queue)
- a scoreboard (option D) can be added later if the ALU becomes
  multi-cycle, with no ISA change

---

## D9 - Memory hierarchy

| | |
|---|---|
| Status | **decided** |
| Opened | 2026-10-15 |
| Decided | 2026-10-15 |

### Question

Where do programs and data live, how do the cores reach them, and what
sits between the cores and memory (arbiters, caches, scratchpads)?

### Starting point

- Today every memory has exactly **one** user, so nothing needs to
  arbitrate. Each slice has a private triangle memory and a private
  framebuffer strip (D3).
- py4hw forbids two modules driving the same wire, so a memory shared by
  several users needs an explicit **arbiter** module: each user gets its own
  interface to the arbiter, and the arbiter alone drives the memory.
- New memory users created by D1-D8, per core:
  - **instruction fetch**: up to one instruction per cycle
  - **vector loads/stores**: one instruction = up to 16 addresses (D4)
  - **texture reads** (later)
  - the **dispatcher**, reading constants to preload into SGPRs (D6)
- Shared data is unavoidable: every slice reads the same textures,
  constants and shader program, and compute kernels write results that
  other slices may read.
- The triangle memory already uses **512-bit lines = 16 × 32-bit words**,
  exactly one word per lane.

### Parts that are the same in every option

To keep the options comparable, these are proposed for all of them:
- **Private instruction memory per core**, loaded with the shader program
  before launch. Fetching an instruction almost every cycle from a shared
  memory would saturate it, which is why real GPUs give each core its own
  instruction cache.
- **Framebuffer strips stay private to each slice** (D3), so draw order and
  pixel ownership need no arbitration.
- **Coalescing:** data memory uses 512-bit lines (16 words). A vector
  load/store whose 16 addresses fall in one line takes one memory
  transaction; otherwise it is split into one transaction per distinct
  line touched. The usual pattern, "thread i reads word base + i", becomes
  one access.

### Options

**A. Private copies, no shared memory.**
Each slice gets its own copy of the textures, constants and data, preloaded
like the Binner fills triangle memories today. No arbiter at all.
Compute results stay in each slice's memory and are combined afterwards,
as `resolve_framebuffer()` does.

**B. One shared global memory + arbiter, no caches.**
A single global data memory for textures, constants and compute buffers.
A round-robin arbiter serves one transaction per cycle among all users of
all slices. No caches, so a value written is immediately visible to
everyone.
*Real-world equivalent:* simple GPUs and microcontroller-class
accelerators; tiny-gpu.

**C. B + caches.**
Each core gets a small read cache (data/texture L1) in front of the
arbiter, optionally with a shared L2 behind it.
*Real-world equivalent:* every NVIDIA and AMD GPU (L1/texture cache per
SM/CU, shared L2).

**D. B + per-core shared memory (scratchpad / LDS).**
Each core gets a small software-managed memory that only its own groups
can use, and that is fast and private. Programs copy data into it
explicitly. It is used by compute kernels (e.g. tiled matrix multiply) and
for sharing data between groups on the same core.
*Real-world equivalent:* NVIDIA "shared memory", AMD LDS.

### Trade-offs

| | A. Private copies | B. Shared + arbiter | C. B + caches | D. B + scratchpad |
|---|---|---|---|---|
| Hardware complexity | lowest | medium (arbiter + coalescer) | high (cache tags, miss handling) | medium-high |
| Memory size | × number of slices (all data duplicated) | 1 copy | 1 copy + cache | 1 copy + scratchpad per core |
| Compute writes visible to other slices | no (combine afterwards) | yes, immediately | yes, but needs a coherence rule (caches can go stale) | yes, via global memory |
| Bandwidth bottleneck | none (each slice has its own) | the single shared port, worse as slices are added | reduced by cache hits | reduced for compute kernels that use it |
| Latency seen by a core | ~2 cycles | 2 + waiting for the arbiter | short on hits, long on misses | short for scratchpad, B for the rest |
| ISA impact | none | none | cache control instructions (flush, invalidate) eventually | scratchpad load/store instructions + workgroup barrier |
| Scales with more slices | memory cost grows | bandwidth becomes the limit | yes | partly |
| Fits D8 (slots hide waiting) | little to hide | yes, this is what slots are for | yes | yes |

### Consequences

- **A:** no shared compute results, and memory grows with the slice count.
  Effectively graphics-only, which weakens D1 = B (compute on the same
  cores).
- **B:** an arbiter and a coalescer have to be built. The single port's
  bandwidth becomes the limit as slices grow, which is visible and
  measurable in simulation, like the rasteriser profiles.
- **C:** adds a coherence problem: a core can read stale data from its
  cache after another core writes. It needs flush/invalidate rules and
  instructions. Large verification effort.
- **D:** adds instructions and a barrier (VENUS v2). Only useful once
  compute kernels exist.
- **B, C and D** can be built in that order: B is the base for both C and
  D, and adding either later doesn't change anything in B.

### Recommendation (Claude)

**B: one shared global data memory with a round-robin arbiter and 512-bit
coalescing, plus a private instruction memory per core and private
framebuffer strips.** Leave caches (C) and the scratchpad (D) for later. Reasons:
- It is the smallest design where compute results are really shared, as
  D1 = B requires.
- No caches means no stale data and no coherence rules to get wrong.
- The waiting it introduces is exactly what D8's group slots hide.
- Coalescing on 512-bit lines matches the 16-lane groups. The common
  access pattern costs one transaction instead of 16, and the line width
  is already used by the triangle memory.
- The shared port's bandwidth will become the bottleneck as slices grow.
  When that is measured, C or D can be added on top without changing B.

### Discussion

- 2026-10-15: options and trade-offs written up; waiting for the project
  owner's choice.
- 2026-10-15: project owner chose **B**.

### Outcome

**B - One shared global data memory + round-robin arbiter, no caches.**
Together with the parts common to all options:
- private instruction memory per core, loaded before launch
- private framebuffer strip per slice (D3)
- data memory organised in 512-bit lines (16 × 32-bit words); a vector
  load/store costs one transaction per distinct line its active lanes touch

Consequences that now apply:
- new hardware blocks: memory arbiter (one interface per user) and
  coalescer in each core's load/store path
- no caches, so writes are visible to everyone immediately and there are
  no coherence rules
- the shared port's bandwidth is the expected bottleneck as slices grow;
  caches (C) or a scratchpad (D) can be added on top later without
  changing B

---

## D10 - Number format

| | |
|---|---|
| Status | **decided** |
| Opened | 2026-10-15 |
| Decided | 2026-10-15 |

### Question

What kind of numbers does the vector unit compute with, beyond the
integers it needs anyway for addresses, masks and loop counters?

### Starting point

- **The rasteriser is fully integer:** pixel coordinates, depth,
  edge-function weights `W0/W1/W2`, and `Area`. Depth is interpolated with an
  integer division. As fixed-function hardware it can stay that way
  whatever we pick here (real rasterisers use fixed point internally).
- **Shaders need fractions everywhere:** interpolation weights
  (`W_i / Area`), colours between 0 and 1, normalised vectors for lighting,
  texture coordinates. The vertex stage (D11) needs matrices with fractional
  entries and a perspective divide, with a wide range of values.
- **The framebuffer stores 32-bit colours** (e.g. 8 bits per channel), so a
  conversion "number → 8-bit channel" is needed in any case.
- The scalar unit is integer-only (D6). The reference renderer
  (`algorithms/rasterisation.py`) uses floating point (numpy).
- The py4hw models are behavioural Python, so any format is easy to
  *simulate*. Hardware cost matters if the design is later turned into
  gates (RTL).

### Options

**A. Integers only.**
Programmers scale values by hand (e.g. colours 0-255, weights × 1024).
*Real-world equivalent:* early fixed-function and DSP-style designs.

**B. Fixed point (e.g. Q16.16).**
32-bit numbers with an implied binary point: 16 integer bits, 16
fraction bits. Range ±32,768, step 1/65,536. Added instructions:
fixed-point multiply (`(a × b) >> 16`), reciprocal/divide, conversions.
*Real-world equivalent:* PlayStation 1 GTE, Nintendo DS, many
microcontroller graphics libraries.

**C. FP32 (IEEE-754 single precision), simplified GPU-style.**
Standard 32-bit floating point. Simplifications common on GPUs: one
rounding mode (round to nearest even), tiny "denormal" numbers flushed to
zero, no exception traps. Infinity and NaN still behave as in IEEE.
*Real-world equivalent:* every programmable GPU since ~2004 (NVIDIA, AMD).

**D. FP16 (half precision).**
16-bit floating point: about 3 decimal digits, range ±65,504. Can be
packed two per register.
*Real-world equivalent:* used by real GPUs *alongside* FP32 for colours
and machine learning. Not enough on its own for positions or depth.

### Trade-offs

| | A. Integer | B. Fixed point Q16.16 | C. FP32 | D. FP16 |
|---|---|---|---|---|
| Hardware size (if built in gates) | smallest (already needed) | small: reuses the integer multiplier + shifter | large: FP adder, multiplier, normaliser | medium |
| Range / precision | depends on manual scaling | fixed: ±32,768, step 1.5·10⁻⁵ | huge range, ~7 significant digits everywhere | ±65,504, ~3 digits |
| Vertex transform & perspective divide | very hard | works for small scenes; overflow and precision loss on large coordinates or near the camera | works | not enough precision |
| Lighting (normalised vectors, dot products) | awkward | works | works | works |
| Colours | works | works | works | works, ideal size |
| Ease of writing shaders by hand | hard (manual scaling everywhere) | medium (watch for overflow) | easy | easy for colours, risky otherwise |
| Comparing with the reference renderer | hard | approximate | direct (numpy `float32`) | approximate |
| Matches public GPU literature/algorithms | no | partly | yes | partly |
| Verification | trivial | easy (integer maths) | medium (edge cases: rounding, NaN, Inf, denormals) | medium |

### Consequences

- **A:** no new hardware, but every shader carries scaling code, and the
  vertex stage can't realistically run on the cores (weakens D11).
- **B:** cheap hardware and integer-only verification. Choosing B means
  accepting limits on scene size and depth precision, or later adding
  FP32 anyway, which would leave two fractional formats in the ISA.
- **C:** VENUS v2 gets FP32 vector instructions (add, mul, fused
  multiply-add, compare-to-mask, min/max, reciprocal, reciprocal square
  root), int↔float conversion, and "float → 8-bit channel" pack. The
  rasteriser's integer `W_i` and `Area` are converted to float at the
  core's input. Area is per-triangle (uniform), but the scalar unit is
  integer-only (D6), so `1 / Area` is computed once in a vector
  instruction, or the dispatcher precomputes it.
- **D alone:** blocks the vertex stage on the cores. As an *addition* to
  C (packed FP16 for colours), it can come later.

### Recommendation (Claude)

**C, FP32 with the usual GPU simplifications**, on the vector unit; integers
stay for addresses, masks and the scalar unit. Reasons:
- It is the only option that handles everything D1 asks the cores to do,
  including vertex transform and perspective (D11), without range or
  precision traps.
- Shaders become straightforward to write by hand, which matters
  because there is no compiler.
- Results can be compared directly with the numpy reference renderer and
  with published algorithms.
- In py4hw simulation, FP32 is cheap to model. The hardware cost only
  matters if the design goes to gates, and then FP16 or a reduced
  FP32 unit can be considered.

**B** is the honest alternative if keeping hardware small and
integer-only matters more than flexibility. Expect limits on scene size and
depth precision.

### Discussion

- 2026-10-15: options and trade-offs written up; waiting for the project
  owner's choice.
- 2026-10-15: project owner chose **C**.

### Outcome

**C - FP32 (IEEE-754 single precision), simplified GPU-style** on the
vector unit: round to nearest even only, denormals flushed to zero, no
exception traps, Inf/NaN as in IEEE. Integers remain for addresses, masks,
counters and the whole scalar unit.

Consequences that now apply:
- VENUS v2 vector instructions include FP32 add, mul, fused multiply-add,
  compare → mask, min/max, reciprocal, reciprocal square root, int↔float
  conversion, and float → 8-bit channel pack
- the rasteriser stays integer; its `W0/W1/W2` and `Area` are converted
  to float at the shader core's input
- `1 / Area` (uniform per triangle) is computed by a vector instruction or
  precomputed by the dispatcher, since the scalar unit is integer-only
- reference results can be compared against numpy `float32`
- packed FP16 for colours remains a possible later addition

---

## D11 - Vertex stage

| | |
|---|---|
| Status | **decided** |
| Opened | 2026-10-15 |
| Decided | 2026-10-15 |

### Question

Who turns 3D models into the screen-space triangles the rasteriser takes,
and which parts of that job are fixed-function hardware?

### What the vertex stage has to do

For each vertex: transform it by the model-view-projection matrix
(4×4 matrix × 4-vector), and compute any per-vertex values the pixel shader
needs (colour, normal, texture coordinates, lighting). Then, per triangle:

1. **Primitive assembly:** group 3 vertices into a triangle (via an index
   list, so shared vertices are transformed once).
2. **Clipping:** handle triangles crossing the camera's near plane. The
   rasteriser already clamps to the screen edges, so only the near plane
   really needs clipping (*guard-band clipping*).
3. **Perspective divide:** x/w, y/w, z/w.
4. **Viewport transform:** scale to pixel coordinates.
5. **Back-face culling:** drop triangles facing away (sign of the area).
6. **Setup:** convert to the rasteriser's integer format and write the
   triangle line for the Binner.

### Starting point

- Today the host (Python) does all of this; the rasteriser takes
  ready-made screen-space triangles. `algorithms/rasterisation.py` contains
  a full software version (perspective, look-at, rotation, flat lighting).
- `MatrixMultiplier4x4`, `CrossProduct` and `Vector3Norm` exist as
  standalone blocks, not wired in.
- **D1's build order already places "vertex transform on the cores" as
  step 5.** The cores are unified (D2), FP32 (D10), and share global memory
  (D9). So this decision is mainly about what stays fixed-function around
  them, and what happens before step 5.
- With slices (D3), every triangle must be in screen space *before*
  binning. So vertex work is a separate pass that runs first (on all
  cores), writes results to global memory, and then the Binner runs.
  Tile-based mobile GPUs work exactly this way.
- With programmable shading, a triangle carries more than one flat colour:
  per-vertex values must reach the pixel shader. The 512-bit triangle line
  has 6 unused 32-bit words, but in general the pixel shader will read
  vertex outputs from global memory using a triangle ID.

### Options

**A. Host does it (as now).**
The CPU-side program transforms, clips and sets up triangles; the chip
starts at the Binner.
*Real-world equivalent:* 1990s accelerators such as 3dfx Voodoo.

**B. Fixed-function vertex unit.**
Dedicated hardware does the matrix transform, a fixed lighting model,
clipping, divide, viewport, culling and setup. Built from blocks like
`MatrixMultiplier4x4`.
*Real-world equivalent:* "hardware T&L", GeForce 256 (1999).

**C. Vertex shaders on the cores + fixed-function primitive unit.**
The per-vertex work (transform, lighting, outputs) is a VENUS v2 program
running on the shader cores. A small fixed-function **primitive unit**
does steps 1-6 (assembly, near-plane clip, divide, viewport, cull, setup)
and feeds the Binner.
*Real-world equivalent:* every GPU since 2006 (NVIDIA PolyMorph / viewport
units, AMD primitive units).

**D. Everything on the cores.**
Vertex shaders *and* steps 1-6 are programs (a compute pass). Fixed
hardware starts at the Binner.
*Real-world equivalent:* compute-based culling in modern engines, mesh
shaders, Larrabee-style designs.

### Trade-offs

| | A. Host | B. Fixed-function unit | C. Vertex shaders + primitive unit | D. All on cores |
|---|---|---|---|---|
| New hardware | none | large (matrix unit, lighting, clipper, setup) | medium (primitive unit) | none beyond the cores |
| Flexibility (skinning, custom lighting, animation) | full, but on the host | none, one fixed model | full | full |
| Chip is a complete graphics pipeline | no | yes | yes | yes |
| Uses decisions D2, D7, D10 (unified FP32 cores) | no | no | yes | yes |
| Performance of steps 1-6 | host-bound | fast | fast | slower (clip and setup are branchy code on 16-wide groups) |
| Near-plane clipping complexity | host code | hardware clipper (hard: one triangle can become two) | hardware (same) | program (easier to write) |
| Verification | none on chip | high | medium | software, against the reference renderer |
| Fits D1 build order | until step 5 | conflicts (builds hardware later made redundant) | yes, is step 5 | yes, is step 5 |

About clipping: a triangle crossing the near plane may be cut into two
triangles, which is awkward in hardware. A common simplification for a
first version is to **drop** triangles that cross the near plane and only
clip properly later. The visible artefact is triangles disappearing when
very close to the camera.

### Consequences

- **A:** nothing changes on the chip. Every scene needs host preprocessing,
  and the cores' FP32 capability is unused for geometry.
- **B:** a big block of hardware that does one job. It duplicates what the
  cores can do, against D2's unified-core direction.
- **C:** a two-pass frame (vertex pass on all cores → primitive unit →
  Binner → raster/pixel pass). VENUS v2 needs a way to launch a vertex
  pass (index-based launch: one thread per vertex). Vertex outputs go to
  global memory (D9); triangle lines or IDs point to them.
- **D:** same two-pass flow as C, with no primitive unit. Its work becomes a
  library of VENUS v2 programs, written and debugged in assembly.

### Recommendation (Claude)

**C as the target, with A kept as the interim until build-order step 5.**
- The host keeps preparing screen-space triangles while the shader core
  and pixel path are built (steps 2-4); nothing is wasted.
- At step 5, the vertex transform moves into a VENUS v2 vertex shader.
  A fixed-function primitive unit then handles assembly, divide, viewport,
  back-face culling and setup.
- Near-plane clipping starts as "drop crossing triangles", with proper
  clipping later.

Reasons: C uses the unified FP32 cores as decided (D2, D7, D10). It keeps
the small, regular, performance-critical steps 1-6 in hardware, where they
are cheap, and matches how NVIDIA and AMD split this work.

**D** is the alternative if you'd rather have less hardware and accept
writing clipping and setup in assembly.

### Discussion

- 2026-10-15: options and trade-offs written up; waiting for the project
  owner's choice.
- 2026-10-15: project owner chose **C**.
- 2026-10-15: the open near-plane question was settled in D14.

### Outcome

**C - Vertex shaders on the shader cores + fixed-function primitive unit.**
Per-vertex work (transform, lighting, outputs for the pixel shader) is a
VENUS v2 program. The primitive unit does assembly, near-plane clipping,
perspective divide, viewport transform, back-face culling and setup, then
feeds the Binner.

Consequences that now apply:
- a frame is two passes: vertex pass on all cores → primitive unit →
  Binner → raster/pixel pass
- VENUS v2 needs an index-based launch for the vertex pass (one thread per
  vertex)
- vertex outputs live in global memory (D9); the pixel shader finds them
  through a triangle ID
- until D1 build-order step 5, the host keeps producing screen-space
  triangles, as today (follows from the confirmed build order)
- not decided yet: whether the first primitive unit drops triangles
  crossing the near plane instead of clipping them (proposed as a first
  simplification)

---

## D12 - Display output

| | |
|---|---|
| Status | **decided** |
| Opened | 2026-10-15 |
| Decided | 2026-10-15 |

### Question

How does a finished frame get from the framebuffer to a screen? Does the
chip include a display controller, or does the host read the image back?

### Starting point

- Today the host reads the framebuffer after rendering
  (`resolve_framebuffer()`, `resolve_banked_framebuffer()`). Then the
  animations show it with matplotlib.
- `display/screen.py` was started as a simulated screen with **two buffers
  and a `Switch_Buffer` input**, which is double buffering. It doesn't work
  yet (README.md).
- The framebuffer is split into **private strips, one per slice** (D3).
  Today each strip has exactly one user, the slice's output stage. A
  display controller reading the strips would be a second user of each.
- **Bandwidth check:** scan-out needs one pixel per pixel-clock tick.
  640×480 at 60 Hz needs a 25.175 MHz pixel clock. 1920×1080 at 60 Hz needs
  148.5 MHz. The benchmark assumes a 50 MHz chip clock, so at one pixel per
  cycle, 640×480 fits comfortably and 1080p60 doesn't.
- **Memory check:** one 32-bit frame is 1.2 MB at 640×480 and 8.3 MB at
  1920×1080. Double buffering doubles it. That matters if the design ever
  goes onto an FPGA, whose on-chip memory is usually a few MB at most.
- There is no physical target (FPGA board or chip) chosen yet.

### Options

**A. Host reads the image (no display hardware).**
After `done`, the host stitches the strips into an image and displays or
saves it, as today.
*Real-world equivalent:* compute accelerators with no display output
(AMD Instinct, NVIDIA datacenter GPUs).

**B. On-chip display controller, simulated screen.**
A display controller generates video timing (line and frame counters,
horizontal/vertical blanking, sync), reads the framebuffer in scan order
across all strips, and streams pixels to a simulated screen module (a
working version of `screen.py`). **Double buffering:** the cores render
into the *back* buffer while the controller shows the *front* buffer; they
swap during vertical blanking, so no half-drawn frame is ever shown. Each
strip memory gets a second, read-only port for scan-out (*dual-port
memory*, standard in FPGAs), so no arbiter is needed.
*Real-world equivalent:* the display engine of every consumer GPU.

**C. B + a physical video output.**
Same as B, plus a block that drives a real VGA or HDMI/DVI connector on an
FPGA board.
*Real-world equivalent:* FPGA graphics projects; real GPUs' display PHYs.

### Trade-offs

| | A. Host reads image | B. Display controller (simulated) | C. B + physical output |
|---|---|---|---|
| New hardware | none | timing generator, scan-out reader, buffer swap, 2nd read port per strip | B + VGA/HDMI output block |
| Memory | 1 framebuffer | 2 framebuffers (double buffering) | 2 framebuffers, must fit the board |
| Animation without tearing | host's job | yes (swap at vertical blanking) | yes |
| Chip is a complete GPU (render → screen) | no | yes, in simulation | yes, on real hardware |
| Requires a hardware target | no | no | yes (an FPGA board must be chosen) |
| Resolution limit | none | none in simulation; timing parameters configurable | pixel clock and memory of the board (640×480 is a realistic start) |
| Simulation speed | fastest | slower (the scan-out runs every cycle) | n/a (real hardware) |
| Fixes / replaces `screen.py` | no | yes | yes |

### Consequences

- **A:** nothing new to build; the existing animations and tests keep
  working. Frame pacing and tearing are left to the host.
- **B:** each slice's framebuffer becomes two dual-port memories (front and
  back). The output stage writes the back buffer through one port; the
  display controller reads the front buffer through the other. A frame
  sequence (render → wait for vertical blanking → swap) is needed. Timing
  parameters (resolution, blanking lengths) are configurable, defaulting to
  standard 640×480 at 60 Hz VGA timing. The host-readback path (A) stays
  available for tests.
- **C:** needs a choice of FPGA board, and a framebuffer that fits its
  memory (external DRAM at higher resolutions, which brings back an
  arbiter). It can be added on top of B later without changing B.

### Recommendation (Claude)

**B, with configurable timing defaulting to 640×480 at 60 Hz, dual-port
front/back framebuffers per strip, swap at vertical blanking, and the host
readback path kept for tests.** Reasons:
- It completes the GPU in simulation (render → screen) and is what
  `screen.py` was meant to be.
- Dual-port strip memories avoid any new arbiter, keeping D3's "each slice
  owns its pixels" property.
- Standard VGA timing means C later only needs the physical output block.
  The FPGA board choice can wait until there is something worth putting
  on it.

### Discussion

- 2026-10-15: options and trade-offs written up; waiting for the project
  owner's choice.
- 2026-10-15: project owner chose **B**.

### Outcome

**B - On-chip display controller with a simulated screen.** Configurable
video timing, defaulting to 640×480 at 60 Hz (VGA). Double buffering: each
slice's framebuffer strip becomes a front and a back dual-port memory; the
output stage writes the back buffer, the display controller reads the
front buffer, and they swap during vertical blanking.

Consequences that now apply:
- framebuffer memory per slice doubles (front + back)
- no new arbiter: the scan-out uses each strip's second (read) port
- `screen.py` is replaced by a working simulated screen fed by the
  display controller
- the host readback path (`resolve_framebuffer()`) stays for tests
- a physical VGA/HDMI output (option C) can be added later; it needs an
  FPGA board choice

---

## D13 - VENUS v2 specification

| | |
|---|---|
| Status | **decided** |
| Opened | 2026-10-15 |
| Decided | 2026-10-15, by Claude at the project owner's request |

### Question

The details D7 left to the spec: register counts, instruction formats, the
instruction list, how pixel, compute and vertex work is launched, and the
assembler. The full result is [README_ISA.md](README_ISA.md). This entry
records the choices that shaped it and the alternatives that were turned down.

### Choices and reasons

| Topic | Chosen | Alternatives considered | Why |
|---|---|---|---|
| Register counts | **32 VGPRs + 31 SGPRs + `exec` as `s31`** | 16 + 16 (v1); 64 + 64 | 5-bit register fields fit three operands in 32 bits. Vec4 lighting and interpolation code needs more than 16 registers, and with hand-allocated registers (no compiler) headroom matters. 64 would cost 6-bit fields and double the register file. Cost: 2.125 KB per group, 8.5 KB per core at 4 slots (D8's 4.25 KB estimate assumed 16 + 16) |
| Where `exec` lives | **`s31`**, an ordinary SGPR | separate special register | Every scalar instruction can save, AND, XOR or restore it directly. The `IF`/`ELSE`/`ENDIF` macros need no special instructions beyond `S_SAVEEXEC_AND` |
| Encoding | **one 4-bit format + 6-bit opcode header** for all formats, fixed 32 bits | per-format header sizes; 64-bit instructions with literals (AMD) | One decoder path for every instruction, fields at fixed positions: the smallest py4hw decoder. 64 opcodes per format leaves room to grow |
| Constants | **through SGPRs** (`S_MOVK`, `S_MOVHI` + `S_ORK`, macro `S_MOV32`) | literal word after the instruction | Keeps every instruction 32 bits. Constants are loop-invariant, so loading them once is cheap |
| Vector operands | **only operand A can be an SGPR** | any operand an SGPR | One extra bit instead of three. Shifts and `CNDMASK` are ordered so that the common constant operand is A (the same trick AMD uses) |
| Addressing | **word-addressed**, 16-word (512-bit) lines | byte-addressed with byte/half loads | Matches every memory in the design today. Colours are packed in registers, so byte stores aren't needed |
| Vector memory forms | `V_LOAD/STORE` (per-lane index), `_LANE` (index = lane), `V_LOAD_BCAST` | one general form only | `_LANE` and `_BCAST` cover the two most common patterns without spending a register on an index, and coalesce to one transaction |
| Waiting on memory | **implicit**: the group stalls until its access completes | explicit wait instructions (AMD `s_waitcnt`) | D8 allows one instruction in flight per group, so there's nothing to overlap within a group. Can be added later with a scoreboard |
| Attribute interpolation | **software** (`INTERP` macro: 3 loads + MUL + 2 MAC) | hardware `V_INTERP` with an attribute cache | No new hardware, and no attribute cache to keep coherent. Hardware interpolation stays a later optimisation |
| Perspective correction | **in the dispatcher**, using `1/w` from the triangle line | in every pixel shader | Done once per pixel in fixed function, as on real GPUs; shaders always get correct barycentrics. Lines without `1/w` behave as before |
| Launch interface | **descriptors in global memory + preloaded registers** (table in README_ISA §6.3) | a command stream with many register writes | Three launch kinds share one convention: `v0` = the item's index, `s4`-`s11` = 8 user values. A vertex launch is just a compute launch followed by the primitive unit |
| Raster-only draws | `VS_PC = 0xFFFFFFFF`: the host writes triangle lines itself | none | Keeps today's screen-space flow working through build steps 2-4, until vertex shaders exist (step 5) |
| Assembler | **Python, `minuscul_GPU/tools/venus_asm.py`**, labels, `.entry`/`.equ`, macros, built-in disassembler | external tool; no macros | Same language as the rest of the project. Macros hide the mask bookkeeping D5 calls for. A disassembler makes round-trip tests possible |

### Consequences

- The v1 draft moved to README_ISA_v1.md. `legacy/Theadv1.py` implements v1
  and will be replaced by a v2 group core, not updated.
- New hardware blocks follow from the launch interface: the front end
  (reads descriptors), the dispatcher (preloads registers, computes vertex
  record addresses and perspective-correct barycentrics), the primitive
  unit, and the output stage receiving exports.
- Loading a 4×4 matrix costs 32 instructions (`S_LOADC` × 16) without
  scalar loads. If vertex shaders measure as slow, that's the case for
  adding `S_LOAD` (D6 option C).

### Outcome

The VENUS v2 specification as written in README_ISA.md (2026-10-15).

---

## D14 - Near-plane handling in the first primitive unit

| | |
|---|---|
| Status | **decided** |
| Opened | 2026-10-15 (left open in D11) |
| Decided | 2026-10-15, by Claude at the project owner's request |

### Question

What does the first primitive unit do with a triangle that crosses the
camera's near plane?

### Options considered

1. **Drop** any triangle with a vertex behind the near plane.
2. **Clip properly**, creating new vertices: up to two triangles out.
   Every new vertex needs *all* its attributes interpolated, which means
   reading and writing whole vertex records of any stride and allocating
   space for new ones. This is the hard part in hardware.
3. **Clip, but describe new vertices as barycentric combinations of the
   original triangle**, so attributes are still read from the original 3
   records. Needs extra words per triangle line, which aren't available
   (words 10-15 are used, D15).
4. **Homogeneous rasterisation** (Olano & Greer, 1997): rasterise without
   dividing by w, so no clipping is needed. Rewrites the existing,
   tested rasteriser.

### Choice

**Option 1, with two cheap companions:**
- **trivial reject:** drop triangles entirely outside one frustum plane;
- **guard band:** drop triangles with any screen coordinate beyond
  ±16383, so the rasteriser's 32-bit edge-function products can't overflow.

Rules in README_ISA.md §6.5.

### Reasons

- It's a few comparisons per vertex. Every clipping option needs either
  variable-size vertex copying (2) or a rasteriser rewrite (4).
- It only matters from build step 5. Until then the host supplies
  screen-space triangles.
- The failure mode is visible and well understood. Triangles reaching
  behind the camera vanish whole, which mainly affects large ground or
  wall triangles in walk-through scenes. Spinning objects in front of the
  camera, like `algorithms/rasterisation.py` renders, are unaffected.

### Consequences and upgrade path

- Scenes should keep geometry in front of the near plane, or subdivide
  large triangles near the camera.
- Proper clipping is listed as a later addition in README_ISA.md §9. When
  needed, option 2 is the expected route.

### Outcome

Drop triangles crossing the near plane, plus trivial frustum reject and a
±16383 guard band (2026-10-15).

---

## D15 - Vertex-output layout and how the pixel shader finds it

| | |
|---|---|
| Status | **decided** |
| Opened | 2026-10-15 |
| Decided | 2026-10-15, by Claude at the project owner's request |

### Question

How are vertex shader results stored in global memory, and how does a
pixel group find the three vertices of its triangle?

### Options considered

| | Option | Turned down because |
|---|---|---|
| Layout | **array of structures**: one record of `VOUT_STRIDE` words per vertex | - |
| | structure of arrays: one array per attribute | The primitive unit would need one base address per attribute. A pixel shader reading all of a vertex's attributes would touch many lines instead of one or two |
| Finding vertices | **indices in the triangle line → the dispatcher computes record addresses into `s0`-`s2`** | - |
| | triangle ID only; the shader fetches indices itself | 3 extra loads and a multiply in every pixel shader, for every group |
| | an attribute cache filled by the primitive unit (AMD's parameter cache) | New hardware and a new memory, for a speed-up we can't yet measure the need for |

### Choice

- **Vertex record:** `VOUT_BASE + index × VOUT_STRIDE`. Words 0-3 hold the
  clip-space position (required, read by the primitive unit); words 4 and
  up hold attributes (FP32). Stride is 4-128 words, bounded by the 7-bit
  VMEM offset.
- **Triangle line:** words 10-12 hold the vertex indices, words 13-15 hold
  1/w per vertex (the 6 previously unused words of the 512-bit line).
  Words 0-9 are unchanged, so existing lines stay valid.
- **Pixel launch:** the dispatcher preloads `s0`-`s2` with the three record
  addresses. `V_LOAD_BCAST v, s0, k` reads attribute *k* of vertex 0 in one
  transaction.
- Full details: README_ISA.md §6.3-6.5.

### Consequences

- An interpolated attribute costs 6 instructions (`INTERP` macro) and 3
  single-line memory transactions per group.
- The dispatcher needs a multiplier (index × stride) and an FP32 unit for
  perspective-correct barycentrics.
- `pack_triangle_line` / `unpack_triangle_line` in renderer.py will gain the
  extra fields when the dispatcher is built.

### Outcome

Array-of-structures vertex records with position first, indices and 1/w
in triangle-line words 10-15, record addresses preloaded into `s0`-`s2`
(2026-10-15).

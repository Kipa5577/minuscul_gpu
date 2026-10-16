# Testbenches

Run every testbench with the project's virtualenv (`gpu_env`), from the
repository root (`pytest.ini` points pytest at this folder):

```
gpu_env/bin/python -m pytest            # all testbenches
gpu_env/bin/python -m pytest minuscul_GPU/tb/sliceTest -v -s   # one module, with cycle counts
```

`common.py` holds the shared helpers: scenes (`CANONICAL_SCENE`,
`SMALL_SCENES`, `random_scene`), memory setup, the simulation loop, the
**legacy `RenderModule` as golden reference** (`legacy_render`), a
software output stage (`compose_fragments`), and test-side components
(`FragmentSink` with configurable backpressure).

| Folder | Module | What it checks |
|---|---|---|
| `rasteriserTest/` | M1 | fragment groups, composed in software, match the legacy renderer; aligned tiles; backpressure; strips; line words 10-15 pass through |
| `outputStageTest/` | M2 | in-order, out-of-order and stalled packet streams; banks; depth rules; strip addressing |
| `sliceTest/` | M3 | `Slice` and `SliceArray` pixel-identical to the legacy modules (1-10 slices, 1-16 banks); `done` only after the last write |
| `assemblerTest/` | M4 | every instruction round-trips (assemble → disassemble → assemble); hand-checked encodings; exact macro expansions; error messages and line numbers; illegal words; `.hex` and command line |
| `shaderCoreTest/` | M5 | FP32/integer semantics (flush-to-zero, rounding, NaN, fused MAC); reference interpreter on directed programs (loops, IF/ELSE, per-lane loops, calls, memory, Gouraud shader, errors); the py4hw core against the reference on random programs (1 and 4 slots); round-robin scheduling; latency hiding; export side data and backpressure |
| `dispatcherTest/` | M6 | launch register convention (incl. perspective-correct barycentrics, vertex record addresses); dispatcher under backpressure; programmable slices: flat shader identical to the legacy renderer, Gouraud shading against an independent software rasteriser, discard without depth writes, 1 vs 4 slots and 1 vs 4 slices give the same image |
| `rasterisationTest/` | legacy | the original suite, on the legacy `RenderModule` (the reference model) |

## rasterisationTest (legacy reference)

Exercises `RenderModule` (`minuscul_GPU/src/rasteriser/renderer.py`) end to
end: preloads a fake triangle memory and an empty framebuffer memory, runs
the py4hw simulation until the renderer asserts `done`, and checks the
resulting framebuffer contents. Covers a single filled triangle, the
depth-test ordering two overlapping triangles, a triangle whose bounding box
is clamped to the screen, and a degenerate (zero-area) triangle.

`test_banked_framebuffer_matches_single_port_output` (parametrized over 2, 4
and 8 banks) renders the depth-test scene through `write_bank_count` banks
and checks the result is pixel-for-pixel identical to the single-port
output. 7 tests in total.

`MultiCoreRenderModule` and `Binner` are now covered indirectly by
`sliceTest/`. Still not covered: the legacy vertex-math blocks
(`MatrixMultiplier4x4`, `CrossProduct`, `Vector3Norm`).

## rasterisationBenchmark

Measures `RenderModule`'s performance across a matrix of scenes (varying
triangle count, triangle size, and screen resolution): instantiation time,
py4hw `Simulator` build time, wall-clock simulation time, clock cycles
consumed, average per-cycle simulation cost, and the estimated hardware
render time for that scene if the design were clocked at 50 MHz.

```
gpu_env/bin/python minuscul_GPU/tb/rasterisationTest/rasterisationBenchmark.py
```

## Animations (need `matplotlib`)

- `animation_of_RenderModule.py`: steps a single `RenderModule` through
  the 5-triangle, 255×255 reference scene used in `RASTERISER_ARCHITECTURE.md`,
  drawing the tiles and corners being tested and the framebuffer filling
  in, live.
- `animation_of_MultiCoreRenderModule.py`: the same idea for
  `MultiCoreRenderModule` at 1920×1080 with 16 cores, using a scene mixing
  small, medium and large triangles so every adaptive tile-size band is
  exercised.

```
gpu_env/bin/python minuscul_GPU/tb/rasterisationTest/animation_of_RenderModule.py
gpu_env/bin/python minuscul_GPU/tb/rasterisationTest/animation_of_MultiCoreRenderModule.py
```

# legacy

Superseded code, kept for reference. Nothing imports it, and it isn't
maintained.

| File | What it is | Superseded by |
|---|---|---|
| `Theadv1.py` | `threadV1`: single-thread interpreter for the VENUS v1 draft ([../README_ISA_v1.md](../README_ISA_v1.md)). It doesn't build: wrong `py4hw.Logic` arguments, `self.mem` never set, no instruction fetch | the VENUS v2 shader core (`shader_core/`, M5) |

To be moved here in M9: `MatrixMultiplier4x4`, `CrossProduct` and
`Vector3Norm` from `rasteriser/renderer.py`. Vertex maths runs as vertex
shaders on the cores (D11), so these standalone blocks won't be wired in.

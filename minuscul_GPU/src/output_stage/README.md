# output_stage

Last block of each slice. It receives finished pixels (export packets),
**in launch order**, does the depth test, and writes the colour into the
slice's framebuffer strip. It is the equivalent of NVIDIA's ROPs and AMD's
render backends.

**Implements:** D3 (each slice owns its framebuffer strip), D8 (exports in
launch order), D12 (writes the back buffer; display reads the front).

## M2: output stage - done

Implemented in `output_stage.py` (`OutputStage`) and `export.py`
(`ExportInterface`). Tests: `tb/outputStageTest/`.

Input, an export packet (valid/ready handshake):

| Field | Size | Source |
|---|---|---|
| `seq` | launch sequence number | dispatcher (or flat-colour unit in M3) |
| `tile_x`, `tile_y` | 2 × 32 bit | from the fragment group |
| `mask` | 16 bit | coverage AND the shader's `exec` at export |
| `colour[16]` | 16 × 32 bit | `V_EXPORT_COLOR` (ARGB, README_ISA §5.7) |
| `z[16]` | 16 × 32 bit | from the fragment group (kept by the dispatcher) |

Tasks:
- [x] **in-order acceptance:** accept packet `seq` = *n* only after *n* − 1;
      later packets wait in a small reorder queue (size = `GROUP_SLOTS`)
- [x] **depth buffer** for the strip, and the test "z < stored z" (smaller
      is closer, as today), applied per lane under `mask`
- [x] **framebuffer write** of the passing lanes into the strip,
      addressed relative to the strip's first row. Move the existing
      pipelined and banked write logic (`write_bank_count`,
      `framebuffer_bank_size`, `resolve_banked_framebuffer`) here from
      `rasteriser/renderer.py`
- [x] a `resolve` helper that returns the strip's pixels, for tests

**Done when:** feeding the export packets that M1's fragments produce
(colour = flat colour) gives the same image as today's renderer, including
when packets arrive out of order.

**Depends on:** M1.

**Later:** the back/front double buffer with a second read port for the
display (M10); blending; early/hierarchical Z.

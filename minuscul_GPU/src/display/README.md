# display

On-chip display controller and the simulated screen it drives.

**Implements:** D12 (option B: display controller + simulated screen,
configurable timing defaulting to 640×480 at 60 Hz, double buffering with
a swap during vertical blanking).

## Existing code: `screen.py`

An early tkinter screen with two buffers and a `Switch_Buffer` input.
It doesn't work:
- `from memory import *` fails inside the package;
- it uses a `resp` signal that `MemoryInterface` doesn't have;
- `mainloop()` blocks inside `__init__`.

It shows the intended idea and is replaced in M10.

## M10: display

Tasks:
- [ ] **double-buffered strips:** each slice's framebuffer strip becomes
      a front and a back memory, each with a second read-only port. The
      output stage writes the back buffer; the controller reads the front
- [ ] **timing generator:** pixel/line counters, active area,
      horizontal/vertical blanking and sync, from parameters (default VGA
      640×480 at 60 Hz: 800 × 525 total, 25.175 MHz pixel clock)
- [ ] **scan-out:** read pixels in raster order across all strips (row y
      → the slice owning y), one pixel per pixel-clock tick
- [ ] **swap:** at the start of vertical blanking, if a new frame is
      finished, exchange front and back
- [ ] **simulated screen:** consumes the pixel stream and shows it
      (matplotlib or tkinter, run outside the simulation loop so it
      doesn't block); also saves frames as images for tests
- [ ] keep the host readback (`resolve_framebuffer()`) for tests

**Done when:** a rotating-triangle animation is shown on the simulated
screen. No frame mixes two renders (checked by comparing displayed frames
with complete renders), and the timing counters match the VGA
parameters.

**Depends on:** M3 (framebuffer strips). Independent of M4-M9.

**Later:** physical VGA/HDMI output on an FPGA (D12 option C; board not
chosen).

import itertools
import os
import sys
import time

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from matplotlib.patches import Rectangle, Polygon
import py4hw

# allows running this file directly (e.g. `python animation_of_RenderModule.py`
# from within its own directory), where the repo root isn't on sys.path yet
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))

from minuscul_GPU.src.memory.memory import MemoryInterface, Memory
from minuscul_GPU.src.rasteriser.renderer import (
    RenderModule, RenderController, TRIANGLE_LINE_WIDTH, pack_triangle_line,
)


# --- Simulation speed -----------------------------------------------------
# How many simulated clock edges to advance per real second. This is decoupled
# from the render/redraw rate below: each frame fast-forwards as many cycles
# as `elapsed wall time * CLOCKS_PER_SECOND` calls for, so the screen doesn't
# have to redraw once per clock cycle to keep up. There is still a hard
# ceiling: a bare `sim.clk(1)` costs ~19us in pure Python on this machine, so
# CLOCKS_PER_SECOND can't meaningfully exceed roughly 50,000 no matter how the
# rendering is batched - a higher value will just run flat-out and fall
# behind its target rate.
CLOCKS_PER_SECOND = 360

# How often the figure is actually redrawn. Kept independent of
# CLOCKS_PER_SECOND on purpose: a full (blit=False) redraw of this figure
# costs ~130ms, so anything above ~10fps here won't actually happen - it
# would just make FuncAnimation queue frames it can't keep up with.
RENDER_FPS = 10

SCREEN_WIDTH = 255
SCREEN_HEIGHT = 255

# (v1, v2, v3, colour) - each vertex an (x, y, z) screen-space + depth triple.
# sized for a 255x255 screen: one large triangle in each corner, plus a
# closer one in the middle straddling all four to show the depth test. Kept
# identical to the scene used throughout RASTERISER_ARCHITECTURE.md so the
# cycle counts quoted there stay reproducible from this script.
TRIANGLES = [
    ((10, 10, 20),   (95, 10, 20),   (10, 95, 20),   0xE86A5C),  # top-left, red
    ((160, 10, 16),  (245, 10, 16),  (160, 95, 16),  0x5CC2E8),  # top-right, blue
    ((10, 160, 24),  (95, 245, 24),  (10, 245, 24),  0x8DE85C),  # bottom-left, green
    ((160, 160, 12), (245, 160, 12), (200, 245, 12), 0xE8C25C),  # bottom-right, amber
    ((85, 85, 6),    (170, 85, 6),   (127, 170, 6),  0xB07CE8),  # centre, closest, purple
]

STAGE_NAMES = [
    "Fetch",
    "BoundingBox",
    "Rasterize",
    "NextTriangle",
]
NUM_STAGES = len(STAGE_NAMES)

# RenderController's own FSM has finer-grained states than the four stages
# above (e.g. separate FETCH_ISSUE/FETCH_WAIT for the bus handshake); this
# groups them the same way the pipeline board in the renderer's own trace
# viewer does.
_STAGE_OF_STATE = {
    RenderController.S_FETCH_ISSUE: 0, RenderController.S_FETCH_WAIT: 0,
    RenderController.S_BBOX: 1,
    RenderController.S_TILE_POP: 2,
    RenderController.S_CORNER_SETUP: 2, RenderController.S_CORNER_READ: 2,
    RenderController.S_ROW_SETUP: 2, RenderController.S_ROW_READ: 2,
    RenderController.S_WRITE_ISSUE: 2, RenderController.S_WRITE_WAIT: 2,
    RenderController.S_NEXT_TILE_ROW: 2,
    RenderController.S_NEXT_TRIANGLE: 3,
    RenderController.S_DONE: None,
}

# Human-readable label for every FSM state, shown live in the HUD.
_STATE_LABELS = {
    getattr(RenderController, name): name[2:] for name in dir(RenderController) if name.startswith('S_')
}

# States during which a tile (RenderController's tile_x0/tile_y0/tile_size)
# is meaningfully "active" and worth drawing on the screen overlay.
_TILE_ACTIVE_STATES = {
    RenderController.S_CORNER_SETUP, RenderController.S_CORNER_READ,
    RenderController.S_ROW_SETUP, RenderController.S_ROW_READ,
    RenderController.S_WRITE_ISSUE, RenderController.S_WRITE_WAIT,
    RenderController.S_NEXT_TILE_ROW,
}
_ROW_ACTIVE_STATES = {RenderController.S_ROW_SETUP, RenderController.S_ROW_READ}

# Same three adaptive-band colours used in the "Rasteriser Replay" web
# artifact, for visual continuity: small/medium/large coarse tile size.
_BAND_COLORS = {4: '#1baf7a', 8: '#2a78d6', 16: '#eb6834'}


def _band_color(size):
    return _BAND_COLORS.get(size, '#888888')


def _band_name(size):
    if size <= 4:
        return 'small (4x4)'
    if size <= 8:
        return 'medium (8x8)'
    return 'large (16x16)'


_TOKEN_COLORS = itertools.cycle([f'#{color:06X}' for _, _, _, color in TRIANGLES])


class Token:
    """One triangle's rasterisation as it rides through the pipeline."""

    def __init__(self, index):
        self.index = index
        self.color = next(_TOKEN_COLORS)

    @property
    def label(self):
        return f"T{self.index}"


# --- Build the real RenderModule simulation --------------------------------
sys_ = py4hw.HWSystem()

triangle_mem_if = MemoryInterface(sys_, 'TriMem', memory_size=len(TRIANGLES), data_width=TRIANGLE_LINE_WIDTH)
triangle_mem = Memory(sys_, 'TriMemImpl', triangle_mem_if, memory_size=len(TRIANGLES))
for i, (v1, v2, v3, color) in enumerate(TRIANGLES):
    triangle_mem.memory[i] = pack_triangle_line(v1, v2, v3, color)

fb_mem_if = MemoryInterface(sys_, 'FbMem', memory_size=SCREEN_WIDTH * SCREEN_HEIGHT)
fb_mem = Memory(sys_, 'FbMemImpl', fb_mem_if, memory_size=SCREEN_WIDTH * SCREEN_HEIGHT)

done = sys_.wire('done', 1)
first_addr = sys_.wire('first_addr', 32)
num_tri = sys_.wire('num_tri', 32)
first_addr.put(0)
num_tri.put(len(TRIANGLES))

renderer = RenderModule(sys_, 'renderer', triangle_mem_if, fb_mem_if,
                         done, first_addr, num_tri, SCREEN_WIDTH, SCREEN_HEIGHT)

sim = sys_.getSimulator()

ctrl = renderer.children['controller']

# One Token (or None) per pipeline stage. Unlike an overlapped hardware
# pipeline, RenderController is a single sequential FSM: exactly one
# triangle is ever "in flight", so at most one slot is ever occupied.
slots = [None] * NUM_STAGES
_prev_stage = None

# --- Detailed per-cycle tracking for the tile-hierarchy overlay ------------
# Unlike the pipeline board above (which only needs the state AFTER a
# transition), the corner-test dots need to know exactly which corner index
# a just-read Inside result belongs to - and by the time clock() returns,
# RenderController has already advanced its own corner_idx past it. So this
# reads each cycle's relevant fields BEFORE clocking, then attributes the
# post-clock Inside/state results back to that pre-clock snapshot - the same
# technique the "Rasteriser Replay" trace capture uses.
overlay = {
    'state': None, 'tri': None, 'tile': None, 'band': None,
    'row_y': None, 'row_xs': [],
}
_current_tile_key = None
corner_results = [None, None, None, None]
pixel_writes_issued = 0


def advance_one_clock():
    """Steps the real simulation by one clock, slides the pipeline Token
    forward on the rising edge of each stage, and updates the tile/corner/row
    overlay state from RenderController's own attributes - all every cycle,
    not just once per redraw, so nothing at high CLOCKS_PER_SECOND gets
    silently skipped the way a once-per-frame snapshot would."""
    global _prev_stage, _current_tile_key

    state_before = ctrl.state
    tri_before = ctrl.triangle_index
    tile_before = (ctrl.tile_x0, ctrl.tile_y0, ctrl.tile_size)
    band_before = ctrl.triangle_coarse_tile_size
    corner_idx_before = ctrl.corner_idx
    corner_list_before = list(ctrl.corner_list)
    py_before = ctrl.py
    row_xs_before = list(ctrl.row_xs)

    sim.clk(1)

    stage_now = _STAGE_OF_STATE[ctrl.state]
    if stage_now is not None and stage_now != _prev_stage:
        if stage_now == 0:
            # entering Fetch for a brand-new triangle
            slots[0] = Token(ctrl.triangle_index)
        else:
            slots[stage_now] = slots[stage_now - 1]
            slots[stage_now - 1] = None
    _prev_stage = stage_now

    overlay['state'] = state_before
    overlay['tri'] = tri_before

    if state_before in _TILE_ACTIVE_STATES:
        overlay['tile'] = tile_before
        overlay['band'] = band_before
        if tile_before != _current_tile_key:
            _current_tile_key = tile_before
            corner_results[:] = [None, None, None, None]
    else:
        overlay['tile'] = None

    if state_before == RenderController.S_CORNER_READ and corner_idx_before < len(corner_list_before):
        corner_results[corner_idx_before] = (ctrl.Inside[0].get() == 1)

    if state_before in _ROW_ACTIVE_STATES:
        overlay['row_y'] = py_before
        overlay['row_xs'] = row_xs_before
    else:
        overlay['row_xs'] = []


def pipeline_finished():
    return done.get() == 1


# --- Drawing ----------------------------------------------------------------
fig, (ax, ax_screen) = plt.subplots(
    1, 2, figsize=(2.1 * NUM_STAGES + 3.4, 4.6),
    gridspec_kw={'width_ratios': [2.1 * NUM_STAGES, 3.4]})
ax.set_xlim(0, NUM_STAGES)
ax.set_ylim(0, 3.1)
ax.axis('off')
fig.suptitle("RenderModule pipeline", fontsize=14, fontweight='bold')

# --- virtual screen: the real framebuffer memory, read straight off the
# hardware model and redrawn every frame as the triangles are rasterised.
BACKGROUND_RGB = (24, 28, 34)
screen_image = np.full((SCREEN_HEIGHT, SCREEN_WIDTH, 3), BACKGROUND_RGB, dtype=np.uint8)
screen_im = ax_screen.imshow(screen_image, interpolation='nearest', zorder=1)
ax_screen.set_title(f"Framebuffer ({SCREEN_WIDTH}x{SCREEN_HEIGHT})", fontsize=10)
ax_screen.set_xticks([])
ax_screen.set_yticks([])
for spine in ax_screen.spines.values():
    spine.set_edgecolor('black')
    spine.set_linewidth(2)

# Every triangle's outline, always visible for scene context; the one
# currently being rasterised is restyled to stand out (see update()).
_tri_outline_patches = []
for v1, v2, v3, color in TRIANGLES:
    poly = Polygon([v1[:2], v2[:2], v3[:2]], closed=True, fill=False,
                    edgecolor='#666666', linewidth=1.0, zorder=2)
    ax_screen.add_patch(poly)
    _tri_outline_patches.append(poly)
_active_tri_index = None

# Current tile boundary - colour is the triangle's own adaptive band (see
# RASTERISER_ARCHITECTURE.md #10), not the momentary tile size, so a
# fine-subdivision fallback (always 4x4, regardless of band - FINE_TILE_SIZE
# never changes) never looks like the wrong band got picked. Subdivision is
# shown as a thinner, tighter dash instead of a colour change.
tile_patch = Rectangle((0, 0), 0, 0, fill=False, edgecolor='#888888',
                        linewidth=2, linestyle=(0, (4, 3)), zorder=4, visible=False)
ax_screen.add_patch(tile_patch)

# Row-scan highlight: the pixel row RenderController is currently testing.
row_patch = Rectangle((0, 0), 0, 0, facecolor='white', edgecolor='none',
                       alpha=0.18, zorder=3, visible=False)
ax_screen.add_patch(row_patch)

# The 4 tile-corner test points: grey until tested, then green/red for
# inside/outside - reused every tile rather than recreated.
corner_scatter = ax_screen.scatter([0, 0, 0, 0], [0, 0, 0, 0], s=45, c='#888888',
                                    edgecolors='white', linewidths=1, zorder=5)
corner_scatter.set_visible(False)

_UNTESTED_COLOR = '#888888'
_INSIDE_COLOR = '#1baf7a'
_OUTSIDE_COLOR = '#e34948'

# Writes are pipelined/optionally banked (RenderController issues a new
# address every cycle rather than holding one until its own `done` fires -
# see RASTERISER_ARCHITECTURE.md #7/#8), so `write==1 and done==1` on the
# same cycle no longer means "the address on the bus right now is the one
# that just finished" - by the time an earlier write's `done` appears,
# `address` has already moved on to a later pixel. Track writes by their own
# (address, colour) the cycle they're issued instead, and paint them one
# external cycle later - the cycle by which the underlying Memory has
# always actually performed that exact mutation, regardless of whether
# writes are pipelined or banked.
_pending_paints = []


def refresh_screen_image():
    # Only touch pixels that just landed, rather than rescanning the whole
    # W*H buffer every clock - that rescan is what made this unusable once
    # the screen grew past a few dozen pixels on a side.
    global _pending_paints, pixel_writes_issued
    to_paint, _pending_paints = _pending_paints, []
    for addr, value in to_paint:
        y, x = divmod(addr, SCREEN_WIDTH)
        screen_image[y, x] = BACKGROUND_RGB if value == 0 else \
            ((value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF)
        pixel_writes_issued += 1

    if fb_mem_if.write.get() == 1:
        _pending_paints.append((fb_mem_if.address.get(), fb_mem_if.write_data.get()))

    screen_im.set_data(screen_image)


def refresh_overlay():
    """Repositions the tile box, corner dots, row highlight, and active-
    triangle outline from the `overlay`/`corner_results` state that
    advance_one_clock() has been keeping up to date every cycle."""
    global _active_tri_index

    tri = overlay['tri']
    if tri != _active_tri_index and tri is not None and tri < len(_tri_outline_patches):
        if _active_tri_index is not None and _active_tri_index < len(_tri_outline_patches):
            prev = _tri_outline_patches[_active_tri_index]
            prev.set_edgecolor('#666666')
            prev.set_linewidth(1.0)
        active = _tri_outline_patches[tri]
        active.set_edgecolor(f'#{TRIANGLES[tri][3]:06X}')
        active.set_linewidth(2.2)
        _active_tri_index = tri

    tile = overlay['tile']
    if tile is None:
        tile_patch.set_visible(False)
        corner_scatter.set_visible(False)
    else:
        tx, ty, tsize = tile
        band = overlay['band'] or tsize
        is_fine_fallback = tsize < band
        tile_patch.set_visible(True)
        tile_patch.set_bounds(tx - 0.5, ty - 0.5, tsize, tsize)
        tile_patch.set_edgecolor(_band_color(band))
        tile_patch.set_linewidth(1.3 if is_fine_fallback else 2.0)
        tile_patch.set_linestyle((0, (2, 2)) if is_fine_fallback else (0, (4, 3)))

        corners = [
            (tx, ty), (tx + tsize - 1, ty),
            (tx, ty + tsize - 1), (tx + tsize - 1, ty + tsize - 1),
        ]
        corner_scatter.set_visible(True)
        corner_scatter.set_offsets(corners)
        colors = []
        for result in corner_results:
            if result is True:
                colors.append(_INSIDE_COLOR)
            elif result is False:
                colors.append(_OUTSIDE_COLOR)
            else:
                colors.append(_UNTESTED_COLOR)
        corner_scatter.set_facecolor(colors)

    row_xs = overlay['row_xs']
    if row_xs:
        x0, x1 = min(row_xs), max(row_xs)
        row_patch.set_visible(True)
        row_patch.set_bounds(x0 - 0.5, overlay['row_y'] - 0.5, x1 - x0 + 1, 1)
    else:
        row_patch.set_visible(False)


STAGE_BOX_Y = 1.35
STAGE_BOX_H = 1.05
TOKEN_BOX_Y = 0.15
TOKEN_BOX_H = 1.05
BOX_MARGIN = 0.12

stage_boxes = []
token_boxes = []
token_labels = []

for i, name in enumerate(STAGE_NAMES):
    x = i + BOX_MARGIN

    ax.text(i + 0.5, STAGE_BOX_Y + STAGE_BOX_H + 0.12, name,
             ha='center', va='bottom', fontsize=10)

    stage_box = Rectangle((x, STAGE_BOX_Y), 1 - 2 * BOX_MARGIN, STAGE_BOX_H,
                           facecolor='white', edgecolor='black', linewidth=2, zorder=2)
    ax.add_patch(stage_box)
    stage_boxes.append(stage_box)

    token_box = Rectangle((x, TOKEN_BOX_Y), 1 - 2 * BOX_MARGIN, TOKEN_BOX_H,
                           facecolor='none', edgecolor='none', linewidth=2, zorder=2)
    ax.add_patch(token_box)
    token_boxes.append(token_box)

    label = ax.text(i + 0.5, TOKEN_BOX_Y + TOKEN_BOX_H / 2, '', ha='center', va='center',
                     fontsize=9, fontweight='bold', zorder=3)
    token_labels.append(label)

    # Dotted line delimiting this component (its left edge; the loop below adds the final right edge).
    ax.plot([i, i], [0, STAGE_BOX_Y + STAGE_BOX_H + 0.3], linestyle=':', color='black', linewidth=1.2, zorder=1)

# Right-hand delimiter for the last stage.
ax.plot([NUM_STAGES, NUM_STAGES], [0, STAGE_BOX_Y + STAGE_BOX_H + 0.3],
         linestyle=':', color='black', linewidth=1.2, zorder=1)

# A single line tracing the *order* of operations through the stages
order_y = STAGE_BOX_Y + STAGE_BOX_H + 0.3
ax.annotate('', xy=(NUM_STAGES - 0.05, order_y + 0.15), xytext=(0.05, order_y + 0.15),
            arrowprops=dict(arrowstyle='-|>', color='#555555', linewidth=1.5))

cycle_text = ax.text(0.0, 3.05, '', fontsize=10, ha='left', va='top', family='monospace')
done_text = ax.text(NUM_STAGES, 3.05, '', fontsize=10, ha='right', va='top', family='monospace')

# Live FSM/tile-hierarchy HUD - the detail the pipeline board is too coarse
# to show (it only tracks 4 macro-stages; RenderController actually has 14
# states and a full tile hierarchy underneath "Rasterize").
state_text = ax.text(0.0, 2.85, '', fontsize=10, ha='left', va='top',
                      family='monospace', fontweight='bold', color='#2a5faf')
tile_text = ax.text(0.0, 2.65, '', fontsize=9, ha='left', va='top', family='monospace')
written_text = ax.text(NUM_STAGES, 2.65, '', fontsize=9, ha='right', va='top', family='monospace')

cycle_count = 0
_last_frame_time = None


def update(_frame):
    global cycle_count, _last_frame_time

    if pipeline_finished():
        return (stage_boxes + token_boxes + token_labels + _tri_outline_patches
                + [cycle_text, done_text, state_text, tile_text, written_text,
                   screen_im, tile_patch, row_patch, corner_scatter])

    now = time.perf_counter()
    # first frame has nothing to measure elapsed time against yet
    elapsed = 0.0 if _last_frame_time is None else (now - _last_frame_time)
    _last_frame_time = now

    cycles_this_frame = max(1, round(elapsed * CLOCKS_PER_SECOND))
    for _ in range(cycles_this_frame):
        advance_one_clock()
        cycle_count += 1
        # every batched cycle can carry its own pixel write, so this has to
        # run per-cycle - calling it once after the whole batch would only
        # ever catch the last write and silently drop the rest
        refresh_screen_image()
        if pipeline_finished():
            break

    refresh_overlay()

    for i in range(NUM_STAGES):
        token = slots[i]
        box = token_boxes[i]
        label = token_labels[i]
        if token is None:
            box.set_facecolor('none')
            box.set_edgecolor('none')
            label.set_text('')
        else:
            box.set_facecolor(token.color)
            box.set_edgecolor('black')
            label.set_text(token.label)

    cycle_text.set_text(f"clock cycle: {cycle_count}")
    done_text.set_text(f"triangles done: {ctrl.triangle_index}/{len(TRIANGLES)}")
    state_text.set_text(f"state: {_STATE_LABELS.get(overlay['state'], '?')}")
    if overlay['tile'] is not None:
        tsize = overlay['tile'][2]
        band = overlay['band'] or tsize
        fallback = ' (fine subdivision)' if tsize < band else ''
        tile_text.set_text(f"tile: {tsize}x{tsize}px{fallback}  |  band: {_band_name(band)}")
    else:
        tile_text.set_text("tile: -")
    written_text.set_text(f"pixel writes: {pixel_writes_issued}")

    if pipeline_finished():
        done_text.set_text(f"triangles done: {len(TRIANGLES)}/{len(TRIANGLES)}  (finished)")

    return (stage_boxes + token_boxes + token_labels + _tri_outline_patches
            + [cycle_text, done_text, state_text, tile_text, written_text,
               screen_im, tile_patch, row_patch, corner_scatter])


ani = FuncAnimation(fig, update, interval=1000 / RENDER_FPS, blit=False,
                     cache_frame_data=False)

plt.tight_layout()
plt.show()

import os
import sys
import time

import numpy as np
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation
from matplotlib.patches import Rectangle, Polygon
import py4hw

# allows running this file directly (e.g. `python animation_of_MultiCoreRenderModule.py`
# from within its own directory), where the repo root isn't on sys.path yet
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))

from minuscul_GPU.src.rasteriser.renderer import (
    MultiCoreRenderModule, RenderController, pack_triangle_line,
)

'''
This is the multi-cursor sibling of animation_of_RenderModule.py: instead of
one RenderController working alone, MultiCoreRenderModule instantiates
NUM_CORES independent RenderControllers - real, separate FSM instances, each
with its own tile/corner/row state - each owning a disjoint horizontal strip
of the screen's rows. A Binner pre-pass (not separately animated here; see
its own state in RASTERISER_ARCHITECTURE.md #5) assigns each triangle only
to the core(s) whose strip it overlaps before any core starts, so every
core visible below only ever touches the triangles that can actually land
in its own strip.

What's genuinely parallel here - and the thing this animation is built to
show - is that every core's clock() call happens on the SAME simulated
clock edge. There is exactly one sim.clk(1) per cycle below, same as the
single-core animation; the difference is that call now advances all
NUM_CORES cursors at once, each independently walking its own tile
hierarchy over its own row-strip.

Screen is the real 1920x1080 target RASTERISER_ARCHITECTURE.md #9/#10 tuned
COARSE_TILE_SIZE for, and the scene is deliberately built from three
triangle-size bands (small/medium/large) so the adaptive per-triangle
coarse tile size (#10) picks a different band for each group live, on the
actual resolution it was tuned for - not just the 255x255 canonical scene
used elsewhere in this repo.
'''

CLOCKS_PER_SECOND = 60
RENDER_FPS = 60

SCREEN_WIDTH = 1920
SCREEN_HEIGHT = 1080
NUM_CORES = 16


def _tri(x0, y0, size, z, color):
    return ((x0, y0, z), (x0 + size, y0, z), (x0, y0 + size, z), color)


# 16 triangles in 3 size bands (small/medium/large legs) arranged in a 4x4
# grid, with sizes rotated Latin-square-style across rows/columns so no two
# triangles in the same row (same core-row-strip neighbourhood) are the same
# size. This matters: an earlier version grouped all 4 large triangles into
# the same y-band, so their tall bounding boxes all landed on the SAME 4-5
# cores (10-13) while 8 of the other 16 cores got zero triangles and sat
# idle the whole run - real load imbalance, not a simulator quirk. Spreading
# each size band across a different row of cores instead means every one of
# the 16 cores gets at least one triangle, which is the whole point of this
# animation (see MultiCoreRenderModule and RASTERISER_ARCHITECTURE.md #11's
# core-count sweep, which found exactly this kind of imbalance can make more
# cores barely help - or, as it did here, make most of them useless).
_SIZES = [220, 60, 15]  # large, medium, small
_X_COLS = [80, 570, 1060, 1550]
_Y_ROWS = [20, 300, 580, 860]
_COLORS = [0xE86A5C, 0x5CC2E8, 0x8DE85C, 0xE8C25C, 0xB07CE8, 0xE85CA0,
           0x5CE8D0, 0xE8A05C, 0x6A8FE8, 0xE86A8F, 0x8FE86A, 0xC2E85C,
           0xF0B26A, 0x6AE8C2, 0xC26AE8, 0xE8E85C]

TRIANGLES = [
    _tri(x0, y0, _SIZES[(row + col) % 3], 10 + (row * 4 + col), _COLORS[row * 4 + col])
    for row, y0 in enumerate(_Y_ROWS)
    for col, x0 in enumerate(_X_COLS)
]

_STATE_LABELS = {
    getattr(RenderController, name): name[2:] for name in dir(RenderController) if name.startswith('S_')
}
_TILE_ACTIVE_STATES = {
    RenderController.S_CORNER_SETUP, RenderController.S_CORNER_READ,
    RenderController.S_ROW_SETUP, RenderController.S_ROW_READ,
    RenderController.S_WRITE_ISSUE, RenderController.S_WRITE_WAIT,
    RenderController.S_NEXT_TILE_ROW,
}
_ROW_ACTIVE_STATES = {RenderController.S_ROW_SETUP, RenderController.S_ROW_READ}

_BAND_COLORS = {4: '#2fd18f', 8: '#4a9bf0', 16: '#ff9852'}


def _band_color(size):
    return _BAND_COLORS.get(size, '#888888')


def _band_name(size):
    if size <= 4:
        return 'small'
    if size <= 8:
        return 'medium'
    return 'large'


# One fixed categorical colour per core (identity, not magnitude - see the
# dataviz skill's colour-formula rule), cycled if NUM_CORES is raised past 8.
_CORE_COLOR_CYCLE = ['#4a9bf0', '#ff9852', '#2fd18f', '#f28bb5', '#ffc247', '#8f7ef0', '#f0645f', '#4fd6c4']
CORE_COLORS = [_CORE_COLOR_CYCLE[i % len(_CORE_COLOR_CYCLE)] for i in range(NUM_CORES)]

_UNTESTED_COLOR = '#6b6f76'
_INSIDE_COLOR = '#2fd18f'
_OUTSIDE_COLOR = '#f0645f'

# --- Cohesive dark theme (matches the "Rasteriser Replay" / "Core Count
# Sweep" web artifacts built alongside this script) instead of a plain
# white matplotlib figure around one dark axis.
BG_PAGE = '#111318'
BG_PANEL = '#181b21'
BORDER = '#2b2f38'
TEXT_PRIMARY = '#eef0f3'
TEXT_MUTED = '#8b8f99'
ACCENT = '#4a9bf0'


# --- Build the real MultiCoreRenderModule simulation ------------------------
hw = py4hw.HWSystem()

triangle_lines = [pack_triangle_line(v1, v2, v3, color) for v1, v2, v3, color in TRIANGLES]

done = hw.wire('done', 1)
first_addr = hw.wire('first_addr', 32)
num_tri = hw.wire('num_tri', 32)
first_addr.put(0)
num_tri.put(len(TRIANGLES))

renderer = MultiCoreRenderModule(hw, 'renderer', NUM_CORES, triangle_lines,
                                  done, first_addr, num_tri, SCREEN_WIDTH, SCREEN_HEIGHT)

sim = hw.getSimulator()

# Per-core state: the real RenderController instance, its row-strip bounds,
# its own framebuffer bus (framebuffer_mem[0] - index 0 since MultiCore's
# cores are never banked, write_bank_count defaults to 1), and all the
# tracking animation_of_RenderModule.py keeps for one core, duplicated here
# once per cursor.
cores = []
for i in range(NUM_CORES):
    core_module = renderer.children[f'core{i}']
    core_ctrl = core_module.children['controller']
    y_start, y_end = renderer.core_partitions[i]
    cores.append({
        'ctrl': core_ctrl,
        'y_start': y_start,
        'y_end': y_end,
        'fb_if': core_ctrl.framebuffer_mem[0],
        'color': CORE_COLORS[i],
        'pending_paints': [],
        'overlay': {'state': None, 'tri': None, 'tile': None, 'band': None, 'row_y': None, 'row_xs': []},
        'corner_results': [None, None, None, None],
        'current_tile_key': None,
        'active_tri': None,
    })

pixel_writes_issued = 0
cycle_count = 0


def still_binning():
    """Every core sits in S_WAIT_START until the Binner pre-pass finishes
    writing into its private triangle memory - true for all of them at once
    or none of them, so checking one core is enough."""
    return cores[0]['ctrl'].state == RenderController.S_WAIT_START


def advance_one_clock():
    """One real simulated clock edge - advances every core's own
    RenderController in the same cycle, which is the actual parallelism
    MultiCoreRenderModule provides. Pre-clock snapshots are taken per core
    for the same reason animation_of_RenderModule.py takes one: a corner
    test's Inside result must be attributed to the corner index that was
    active BEFORE this clock, since clock() advances corner_idx past it."""
    pre = []
    for c in cores:
        ctrl = c['ctrl']
        pre.append({
            'state': ctrl.state,
            'tri': ctrl.triangle_index,
            'tile': (ctrl.tile_x0, ctrl.tile_y0, ctrl.tile_size),
            'band': ctrl.triangle_coarse_tile_size,
            'corner_idx': ctrl.corner_idx,
            'corner_list': list(ctrl.corner_list),
            'py': ctrl.py,
            'row_xs': list(ctrl.row_xs),
        })

    sim.clk(1)

    for c, p in zip(cores, pre):
        ctrl = c['ctrl']
        overlay = c['overlay']
        overlay['state'] = p['state']
        overlay['tri'] = p['tri']

        if p['state'] in _TILE_ACTIVE_STATES:
            overlay['tile'] = p['tile']
            overlay['band'] = p['band']
            if p['tile'] != c['current_tile_key']:
                c['current_tile_key'] = p['tile']
                c['corner_results'][:] = [None, None, None, None]
        else:
            overlay['tile'] = None

        if p['state'] == RenderController.S_CORNER_READ and p['corner_idx'] < len(p['corner_list']):
            c['corner_results'][p['corner_idx']] = (ctrl.Inside[0].get() == 1)

        if p['state'] in _ROW_ACTIVE_STATES:
            overlay['row_y'] = p['py']
            overlay['row_xs'] = p['row_xs']
        else:
            overlay['row_xs'] = []


def pipeline_finished():
    return done.get() == 1


# --- Drawing ----------------------------------------------------------------
# The HUD panel needs more width (one line per core: "c00 r0-67 tri2/3
# CORNER_READ tile8x8(med)") and a bit more height as NUM_CORES grows, so
# both scale with it rather than staying fixed at a 4-core layout's size.
fig, (ax_hud, ax_screen) = plt.subplots(
    1, 2, figsize=(15.5, max(6.4, 0.30 * NUM_CORES + 3.0)),
    gridspec_kw={'width_ratios': [1.55, 3.0]})

fig.patch.set_facecolor(BG_PAGE)
ax_hud.set_facecolor(BG_PANEL)
ax_hud.set_xlim(0, 1)
ax_hud.set_ylim(0, 1)
ax_hud.axis('off')

fig.suptitle(f"MultiCoreRenderModule — {NUM_CORES} cursors, {SCREEN_WIDTH}×{SCREEN_HEIGHT}",
             fontsize=16, fontweight='bold', color=TEXT_PRIMARY, y=0.975)
fig.text(0.5, 0.935, f"{len(TRIANGLES)} triangles across 3 adaptive tile bands"
                      " (RASTERISER_ARCHITECTURE.md #10)  ·  real py4hw simulation",
         ha='center', fontsize=10, color=TEXT_MUTED)

BACKGROUND_RGB = (16, 18, 22)
screen_image = np.full((SCREEN_HEIGHT, SCREEN_WIDTH, 3), BACKGROUND_RGB, dtype=np.uint8)
screen_im = ax_screen.imshow(screen_image, interpolation='nearest', zorder=1)
ax_screen.set_title(f"Framebuffer, stitched live", fontsize=11, color=TEXT_PRIMARY, pad=8)
ax_screen.set_xticks([])
ax_screen.set_yticks([])
ax_screen.set_facecolor(BG_PAGE)
for spine in ax_screen.spines.values():
    spine.set_edgecolor(BORDER)
    spine.set_linewidth(1.6)

# Row-strip boundaries: static, drawn once - shows each core's own disjoint
# slice of the screen it owns for the whole run. The label shrinks (and
# eventually just becomes the swatch/number) as strips get thinner with
# more cores, so it never overlaps a neighbouring strip's own label.
_strip_label_fontsize = max(6.0, min(9.0, (SCREEN_HEIGHT / NUM_CORES) * 0.11))
for i, c in enumerate(cores):
    if i > 0:
        ax_screen.axhline(c['y_start'] - 0.5, color='#3a3f4a', linewidth=0.7, linestyle='--', zorder=2)
    label = f"core {i}" if NUM_CORES <= 8 else str(i)
    ax_screen.text(SCREEN_WIDTH - 14, (c['y_start'] + c['y_end']) / 2, label,
                   color=c['color'], fontsize=_strip_label_fontsize, fontweight='bold',
                   ha='right', va='center', zorder=6,
                   bbox=dict(boxstyle='round,pad=0.2', facecolor=BG_PANEL, edgecolor=c['color'],
                             linewidth=0.8, alpha=0.85))

# Every triangle's outline, always visible for scene context - with a very
# faint fill in its own colour so the *shape* reads even before any pixel
# inside it has actually been rasterised yet.
_tri_outline_patches = []
_tri_fill_patches = []
for v1, v2, v3, color in TRIANGLES:
    hexcolor = f'#{color:06X}'
    fill = Polygon([v1[:2], v2[:2], v3[:2]], closed=True, fill=True,
                   facecolor=hexcolor, edgecolor='none', alpha=0.07, zorder=1.5)
    ax_screen.add_patch(fill)
    _tri_fill_patches.append(fill)

    poly = Polygon([v1[:2], v2[:2], v3[:2]], closed=True, fill=False,
                    edgecolor='#4a4f5a', linewidth=1.0, zorder=2)
    ax_screen.add_patch(poly)
    _tri_outline_patches.append(poly)

# One tile box (with a soft "glow" twin behind it) + corner-dot set (with a
# dark ring for contrast against bright framebuffer pixels) + row highlight
# per core, colour-keyed to that core's own identity - size is shown via
# dash weight (thin/tight dash = fine-subdivision fallback), same convention
# as the single-core animation.
for c in cores:
    c['tile_glow'] = Rectangle((0, 0), 0, 0, fill=False, edgecolor=c['color'],
                                linewidth=6, alpha=0.18, zorder=3, visible=False)
    ax_screen.add_patch(c['tile_glow'])

    c['tile_patch'] = Rectangle((0, 0), 0, 0, fill=False, edgecolor=c['color'],
                                 linewidth=2, linestyle=(0, (4, 3)), zorder=4, visible=False)
    ax_screen.add_patch(c['tile_patch'])

    c['row_patch'] = Rectangle((0, 0), 0, 0, facecolor=c['color'], edgecolor='none',
                                alpha=0.16, zorder=3, visible=False)
    ax_screen.add_patch(c['row_patch'])

    c['corner_scatter'] = ax_screen.scatter([0, 0, 0, 0], [0, 0, 0, 0], s=70, c=_UNTESTED_COLOR,
                                             edgecolors='#0b0d10', linewidths=1.6, zorder=5)
    c['corner_scatter'].set_visible(False)

# Writes are pipelined/optionally banked (RASTERISER_ARCHITECTURE.md #7/#8):
# a write's address moves on before its own `done` appears, so - same as
# the single-core animation - track each core's (address, colour) the cycle
# it's issued and paint it one external cycle later, per core.
def refresh_screen_image():
    global pixel_writes_issued
    for c in cores:
        to_paint, c['pending_paints'] = c['pending_paints'], []
        for local_addr, value in to_paint:
            y_local, x = divmod(local_addr, SCREEN_WIDTH)
            y = c['y_start'] + y_local
            screen_image[y, x] = BACKGROUND_RGB if value == 0 else \
                ((value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF)
            pixel_writes_issued += 1

        fb = c['fb_if']
        if fb.write.get() == 1:
            c['pending_paints'].append((fb.address.get(), fb.write_data.get()))

    screen_im.set_data(screen_image)


def refresh_overlay():
    for c in cores:
        overlay = c['overlay']
        tri = overlay['tri']
        if tri != c['active_tri'] and tri is not None and tri < len(_tri_outline_patches):
            if c['active_tri'] is not None and c['active_tri'] < len(_tri_outline_patches):
                # only reset the previous triangle's outline if no OTHER
                # core is also currently active on it
                if not any(oc is not c and oc['active_tri'] == c['active_tri'] for oc in cores):
                    _tri_outline_patches[c['active_tri']].set_edgecolor('#4a4f5a')
                    _tri_outline_patches[c['active_tri']].set_linewidth(1.0)
                    _tri_fill_patches[c['active_tri']].set_alpha(0.07)
            _tri_outline_patches[tri].set_edgecolor(c['color'])
            _tri_outline_patches[tri].set_linewidth(2.4)
            _tri_fill_patches[tri].set_alpha(0.22)
            c['active_tri'] = tri

        tile = overlay['tile']
        if tile is None:
            c['tile_patch'].set_visible(False)
            c['tile_glow'].set_visible(False)
            c['corner_scatter'].set_visible(False)
        else:
            tx, ty, tsize = tile
            ty_global = ty + c['y_start']
            band = overlay['band'] or tsize
            is_fine_fallback = tsize < band

            c['tile_glow'].set_visible(True)
            c['tile_glow'].set_bounds(tx - 0.5, ty_global - 0.5, tsize, tsize)

            c['tile_patch'].set_visible(True)
            c['tile_patch'].set_bounds(tx - 0.5, ty_global - 0.5, tsize, tsize)
            c['tile_patch'].set_linewidth(1.3 if is_fine_fallback else 2.2)
            c['tile_patch'].set_linestyle((0, (2, 2)) if is_fine_fallback else (0, (4, 3)))

            corners = [
                (tx, ty_global), (tx + tsize - 1, ty_global),
                (tx, ty_global + tsize - 1), (tx + tsize - 1, ty_global + tsize - 1),
            ]
            c['corner_scatter'].set_visible(True)
            c['corner_scatter'].set_offsets(corners)
            colors = []
            for result in c['corner_results']:
                if result is True:
                    colors.append(_INSIDE_COLOR)
                elif result is False:
                    colors.append(_OUTSIDE_COLOR)
                else:
                    colors.append(_UNTESTED_COLOR)
            c['corner_scatter'].set_facecolor(colors)

        row_xs = overlay['row_xs']
        if row_xs:
            x0, x1 = min(row_xs), max(row_xs)
            y_global = overlay['row_y'] + c['y_start']
            c['row_patch'].set_visible(True)
            c['row_patch'].set_bounds(x0 - 0.5, y_global - 0.5, x1 - x0 + 1, 1)
        else:
            c['row_patch'].set_visible(False)


# --- HUD: one compact line per core, since with NUM_CORES cursors the
# interesting live information is "what is each cursor doing right now",
# not a single-core 4-stage macro pipeline board. A small legend up top
# explains the tile/corner colour language once, rather than repeating it
# per row.
header_text = ax_hud.text(0.03, 0.975, '', fontsize=11, ha='left', va='top',
                           family='monospace', fontweight='bold', color=TEXT_PRIMARY)

legend_y = 0.86
ax_hud.text(0.03, legend_y + 0.032, 'adaptive tile band', fontsize=8.5, ha='left', va='bottom',
            color=TEXT_MUTED, fontweight='bold')
for j, (size, name) in enumerate([(4, 'small'), (8, 'medium'), (16, 'large')]):
    lx = 0.03 + j * 0.30
    ax_hud.add_patch(Rectangle((lx, legend_y - 0.012), 0.022, 0.022, facecolor='none',
                                edgecolor=_band_color(size), linewidth=2))
    ax_hud.text(lx + 0.03, legend_y, f'{name} ({size}px)', fontsize=8, ha='left', va='center', color=TEXT_PRIMARY)

corner_legend_y = legend_y - 0.075
ax_hud.text(0.03, corner_legend_y + 0.032, 'corner test', fontsize=8.5, ha='left', va='bottom',
            color=TEXT_MUTED, fontweight='bold')
for j, (label, color) in enumerate([('untested', _UNTESTED_COLOR), ('inside', _INSIDE_COLOR), ('outside', _OUTSIDE_COLOR)]):
    lx = 0.03 + j * 0.30
    ax_hud.scatter([lx + 0.011], [corner_legend_y], s=55, c=color, edgecolors='#0b0d10', linewidths=1.4, zorder=3)
    ax_hud.text(lx + 0.03, corner_legend_y, label, fontsize=8, ha='left', va='center', color=TEXT_PRIMARY)

ax_hud.axhline(corner_legend_y - 0.04, xmin=0.02, xmax=0.98, color=BORDER, linewidth=1)

core_texts = []
list_top = corner_legend_y - 0.08
row_h = list_top / NUM_CORES
swatch_w = 0.022
row_fontsize = max(6.0, min(9.5, 155 / NUM_CORES))
for i, c in enumerate(cores):
    y_top = list_top - i * row_h
    y_mid = y_top - row_h * 0.5
    ax_hud.add_patch(Rectangle((0.03, y_mid - row_h * 0.28), swatch_w, row_h * 0.56,
                                facecolor=c['color'], edgecolor='none'))
    line_t = ax_hud.text(0.03 + swatch_w + 0.015, y_mid, '', fontsize=row_fontsize,
                          ha='left', va='center', family='monospace', color=TEXT_PRIMARY)
    core_texts.append({'line': line_t})

_last_frame_time = None


def update(_frame):
    global cycle_count, _last_frame_time

    if pipeline_finished():
        return _static_artists()

    now = time.perf_counter()
    elapsed = 0.0 if _last_frame_time is None else (now - _last_frame_time)
    _last_frame_time = now

    cycles_this_frame = max(1, round(elapsed * CLOCKS_PER_SECOND))
    for _ in range(cycles_this_frame):
        advance_one_clock()
        cycle_count += 1
        refresh_screen_image()
        if pipeline_finished():
            break

    refresh_overlay()

    binning = still_binning()
    header_text.set_text(
        f"cycle {cycle_count:,}\n{pixel_writes_issued:,} pixel writes\n"
        + ("binning...\n" if binning else "")
        + ("ALL DONE" if pipeline_finished() else "")
    )
    header_text.set_color('#2fd18f' if pipeline_finished() else TEXT_PRIMARY)

    for i, (c, texts) in enumerate(zip(cores, core_texts)):
        ov = c['overlay']
        state_name = _STATE_LABELS.get(ov['state'], '?')
        tri_total = c['ctrl'].number_of_triangles.get() if not binning else '?'

        if ov['tile'] is not None:
            tsize = ov['tile'][2]
            band = ov['band'] or tsize
            fallback = '*' if tsize < band else ''
            tile_part = f"tile {tsize}x{tsize}{fallback}({_band_name(band)[:3]})"
        else:
            tile_part = "tile -"

        texts['line'].set_text(
            f"c{i:02d} r{c['y_start']:>4d}-{c['y_end']:<4d} tri{ov['tri']}/{tri_total} "
            f"{state_name:<12s} {tile_part}"
        )

    return _static_artists()


def _static_artists():
    artists = [screen_im, header_text] + _tri_outline_patches + _tri_fill_patches
    for c in cores:
        artists += [c['tile_glow'], c['tile_patch'], c['row_patch'], c['corner_scatter']]
    for texts in core_texts:
        artists.append(texts['line'])
    return artists


ani = FuncAnimation(fig, update, interval=1000 / RENDER_FPS, blit=False,
                     cache_frame_data=False)

plt.tight_layout(rect=[0, 0, 1, 0.90])
plt.show()

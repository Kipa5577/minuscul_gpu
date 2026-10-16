'''
M6 testbench: PixelDispatcher and programmable slices (end of build
step 3). Pixel shaders written in VENUS v2 assembly run on the shader core
inside a slice; images are checked against the legacy renderer (flat
shader) or an independent software rasteriser (Gouraud, discard).
'''
import struct

import numpy as np
import py4hw
import py4hw.simulation
import pytest

from ...src.dispatcher.dispatcher import DrawState, PixelDispatcher, build_pixel_launch, NO_INDEX
from ...src.memory.memory import MemoryInterface, Memory
from ...src.params import GROUP_WIDTH, LANE_MASK, lane_xy
from ...src.rasteriser.fragment import FragmentGroupInterface
from ...src.rasteriser.rasteriser import Rasteriser
from ...src.rasteriser.triangle_line import pack_triangle_line
from ...src.shader_core.alu import f32_bits, to_f32
from ...src.shader_core.core import LaunchInterface, read_launch
from ...src.slice.slice import Slice, SliceArray
from ...tools.venus_asm import assemble
from ..common import CANONICAL_SCENE, random_scene, triangle_memory, constant, run_until, legacy_render
from ..rasteriserTest.test_fragment_output import rasterise

ONE = f32_bits(1.0)

FLAT_SHADER = '''
.entry ps_flat
    V_MOV v1, s3          ; the triangle's flat colour
    V_EXPORT_COLOR v1
    S_ENDPGM
'''

GOURAUD_SHADER = '''
.entry ps_gouraud
    INTERP    v5, 4, v12          ; vertex record words 4-6 = r, g, b
    INTERP    v6, 5, v12
    INTERP    v7, 6, v12
    S_MOV32   s12, 1.0
    V_MOV     v8, s12
    PACK_RGBA v9, v5, v6, v7, v8, v12
    V_EXPORT_COLOR v9
    S_ENDPGM
'''

DISCARD_ODD_COLUMNS = '''
.entry ps_discard
    S_MOVK    s12, 1
    V_AND     v5, s12, v0          ; x & 1
    V_CMP_EQ_I32 s13, s12, v5      ; odd columns
    S_ANDN2   exec, exec, s13      ; discard them
    V_MOV     v1, s3
    V_EXPORT_COLOR v1
    S_ENDPGM
'''


# --- rendering helpers ------------------------------------------------------------------

def render(triangles, width, height, source, draw=None, data=None, extra_words=None,
           num_slices=None, slots=4, max_cycles=3_000_000):
    '''Renders with programmable slices. num_slices None = a single Slice.'''
    program = assemble(source)
    draw = draw or DrawState()
    draw.ps_pc = next(iter(program.entries.values()))
    data = list(data or [0] * 16)
    hw = py4hw.HWSystem()
    done = hw.wire('done', 1)
    if num_slices is None:
        tri_if, _ = triangle_memory(hw, 'TriMem', triangles, extra_words)
        fb_if = MemoryInterface(hw, 'Fb', memory_size=width * height)
        fb = Memory(hw, 'FbImpl', fb_if, memory_size=width * height)
        data_if = MemoryInterface(hw, 'Data', memory_size=len(data))
        Memory(hw, 'DataImpl', data_if, memory_size=len(data)).memory[:] = data
        s = Slice(hw, 'slice', tri_if, fb_if, done, constant(hw, 'first', 0),
                  constant(hw, 'count', len(triangles)), width, height, shading='shader',
                  program=program.words, draw=draw, data_mem=data_if, slots=slots)
        cycles = run_until(py4hw.simulation.Simulator(hw), done, max_cycles)
        return list(fb.memory), cycles, s
    lines = []
    for i, t in enumerate(triangles):
        line = pack_triangle_line(*t)
        for k, word in enumerate((extra_words or [[0] * 6] * len(triangles))[i]):
            line |= (word & 0xFFFFFFFF) << ((10 + k) * 32)
        lines.append(line)
    array = SliceArray(hw, 'array', num_slices, lines, done, constant(hw, 'first', 0),
                       constant(hw, 'count', len(triangles)), width, height, shading='shader',
                       program=program.words, draw=draw, data_memory=data, slots=slots)
    cycles = run_until(py4hw.simulation.Simulator(hw), done, max_cycles)
    return array.resolve_framebuffer(), cycles, array


def software_render(triangles, width, height, shade):
    '''Independent reference rasteriser, using the same inside test, depth
    interpolation and tie rule as the hardware. shade(tri_index, b0, b1, b2,
    x, y) returns the pixel colour, or None to discard.'''
    image = [0] * (width * height)
    zbuf = [None] * (width * height)
    for t, ((x1, y1, z1), (x2, y2, z2), (x3, y3, z3), _) in enumerate(triangles):
        for y in range(max(0, min(y1, y2, y3)), min(height - 1, max(y1, y2, y3)) + 1):
            for x in range(max(0, min(x1, x2, x3)), min(width - 1, max(x1, x2, x3)) + 1):
                w0 = (x - x3) * (y2 - y3) - (x2 - x3) * (y - y3)
                w1 = (x - x1) * (y3 - y1) - (x3 - x1) * (y - y1)
                w2 = (x - x2) * (y1 - y2) - (x1 - x2) * (y - y2)
                area = w0 + w1 + w2
                ws = (w0, w1, w2)
                if area == 0 or (min(ws) < 0 < max(ws)):
                    continue
                z = int((w0 * z1 + w1 * z2 + w2 * z3) / area)
                colour = shade(t, w0 / area, w1 / area, w2 / area, x, y)
                if colour is None:
                    continue
                addr = y * width + x
                if zbuf[addr] is None or z < zbuf[addr]:
                    zbuf[addr] = z
                    image[addr] = colour
    return image


def unorm8(c):
    return int(np.round(min(max(c, 0.0), 1.0) * 255))


def channels(argb):
    return [(argb >> s) & 0xFF for s in (24, 16, 8, 0)]


# --- launch contents --------------------------------------------------------------------

def test_launch_follows_the_register_convention():
    triangle = ((2, 1, 7), (13, 3, 9), (4, 14, 11), 0x00ABCDEF)
    inv_w = [f32_bits(0.5), f32_bits(0.25), f32_bits(1.0)]
    extra = [3, 5, 9] + inv_w
    groups, _, _ = rasterise([triangle], 16, 16, extra_words=[extra])
    draw = DrawState(ps_pc=7, user=list(range(100, 108)), vout_base=1000, vout_stride=8,
                     has_vertex_records=True)
    for seq, g in enumerate(groups):
        ln = build_pixel_launch(g, seq, draw)
        s, v = ln['sgprs'], ln['vgprs']
        assert ln['pixel'] == 1 and ln['pc'] == 7 and ln['seq'] == seq
        assert s[31] == g['coverage'] and s[3] == 0x00ABCDEF
        assert [s[0], s[1], s[2]] == [1000 + 3 * 8, 1000 + 5 * 8, 1000 + 9 * 8]
        assert [s[4 + i] for i in range(8)] == list(range(100, 108))
        assert (ln['tile_x'], ln['tile_y'], ln['z']) == (g['tile_x'], g['tile_y'], g['z'])
        for lane in range(GROUP_WIDTH):
            dx, dy = lane_xy(lane)
            assert (v[0][lane], v[1][lane]) == (g['tile_x'] + dx, g['tile_y'] + dy)
            lam = np.array([g['w0'][lane], g['w1'][lane], g['w2'][lane]]) / g['area']
            q = np.array([0.5, 0.25, 1.0])
            expected = lam * q / (lam * q).sum()
            got = [float(to_f32(v[2 + k][lane])) for k in range(3)]
            assert np.allclose(got, expected, rtol=1e-6, atol=1e-7)


def test_no_vertex_records_and_no_perspective():
    groups, _, _ = rasterise([((0, 0, 1), (7, 0, 1), (0, 7, 1), 5)], 8, 8)
    ln = build_pixel_launch(groups[0], 0, DrawState(vout_base=50, vout_stride=4))
    assert all(ln['sgprs'].get(k, 0) == 0 for k in range(3))
    g = groups[0]
    for lane in range(GROUP_WIDTH):
        lam = [g['w0'][lane] / g['area'], g['w1'][lane] / g['area'], g['w2'][lane] / g['area']]
        assert [ln['vgprs'][2 + k][lane] for k in range(3)] == [f32_bits(x) for x in lam]


class LaunchSink(py4hw.Logic):
    def __init__(self, parent, name, launch, ready_pattern):
        super().__init__(parent, name)
        self.ln = self.addInterfaceSink('launch', launch)
        self.ready_pattern = ready_pattern
        self.launches = []
        self.cycle = 0
        launch.ready.put(ready_pattern(0))

    def clock(self):
        if self.ln.valid.get() == 1 and self.ln.ready.get() == 1:
            self.launches.append(read_launch(self.ln))
        self.cycle += 1
        self.ln.ready.prepare(self.ready_pattern(self.cycle))


def test_dispatcher_hardware_under_backpressure():
    triangles = random_scene(41, 6, 24, 24)
    groups, _, _ = rasterise(triangles, 24, 24)
    draw = DrawState(ps_pc=3, user=[1, 2, 3, 4, 5, 6, 7, 8])
    hw = py4hw.HWSystem()
    tri_if, _ = triangle_memory(hw, 'TriMem', triangles)
    frag = FragmentGroupInterface(hw, 'frag')
    launch = LaunchInterface(hw, 'launch')
    rast_done, idle = hw.wire('rast_done', 1), hw.wire('idle', 1)
    Rasteriser(hw, 'rast', tri_if, frag, rast_done, constant(hw, 'first', 0),
               constant(hw, 'count', len(triangles)), 24, 24)
    PixelDispatcher(hw, 'disp', frag, launch, idle, draw)
    sink = LaunchSink(hw, 'sink', launch, lambda c: (c // 3) % 2)   # ready 3 cycles on, 3 off
    sim = py4hw.simulation.Simulator(hw)
    for _ in range(100_000):
        sim._clk_cycle()
        if rast_done.get() == 1 and idle.get() == 1:
            break
    expected = [build_pixel_launch(g, seq, draw) for seq, g in enumerate(groups)]
    assert len(sink.launches) == len(expected)
    for got, exp in zip(sink.launches, expected):
        assert got['seq'] == exp['seq'] and got['tile_x'] == exp['tile_x']
        assert got['sgprs'][31] == exp['sgprs'][31] and got['sgprs'][4] == 1
        assert got['vgprs'][2] == exp['vgprs'][2]


# --- programmable slices ----------------------------------------------------------------

@pytest.mark.parametrize('name,triangles,width,height', [
    ('random_48', random_scene(42, 20, 48, 48), 48, 48),
    ('canonical', CANONICAL_SCENE, 255, 255),
], ids=['random_48', 'canonical'])
def test_flat_pixel_shader_matches_legacy_renderer(name, triangles, width, height):
    image, cycles, s = render(triangles, width, height, FLAT_SHADER)
    assert image == legacy_render(triangles, width, height)[0]
    print(f'{name}: shader-core slice {cycles} cycles, {s.core.instructions} instructions')


def gouraud_scene(width, height):
    tris = random_scene(43, 10, width, height, margin=4)
    rng = np.random.default_rng(43)
    vertex_colours = rng.random((3 * len(tris), 3))
    stride, base = 8, 16
    data = [0] * (base + stride * 3 * len(tris))
    for v, (r, g, b) in enumerate(vertex_colours):
        data[base + v * stride + 4:base + v * stride + 7] = [f32_bits(r), f32_bits(g), f32_bits(b)]
    extra = [[3 * t, 3 * t + 1, 3 * t + 2, ONE, ONE, ONE] for t in range(len(tris))]
    draw = DrawState(vout_base=base, vout_stride=stride, has_vertex_records=True)

    def shade(t, b0, b1, b2, x, y):
        c = vertex_colours[3 * t] * b0 + vertex_colours[3 * t + 1] * b1 + vertex_colours[3 * t + 2] * b2
        r, g, b = (unorm8(x) for x in c)
        return (255 << 24) | (r << 16) | (g << 8) | b
    return tris, data, extra, draw, shade


def assert_close_images(actual, expected, tolerance=1):
    assert len(actual) == len(expected)
    for addr, (a, e) in enumerate(zip(actual, expected)):
        if a == e:
            continue
        assert a and e, f'pixel {addr}: coverage differs ({a:08X} vs {e:08X})'
        assert max(abs(x - y) for x, y in zip(channels(a), channels(e))) <= tolerance, \
            f'pixel {addr}: {a:08X} vs {e:08X}'


def test_gouraud_pixel_shader():
    width, height = 40, 40
    tris, data, extra, draw, shade = gouraud_scene(width, height)
    image, cycles, s = render(tris, width, height, GOURAUD_SHADER, draw, data, extra)
    assert_close_images(image, software_render(tris, width, height, shade))
    assert any(p for p in image)
    print(f'gouraud 40x40: {cycles} cycles, {s.core.instructions} instructions')


def test_discarded_pixels_do_not_write_depth():
    far = ((0, 0, 50), (15, 0, 50), (0, 15, 50), 0x00FF0000)    # red, behind, drawn first
    near = ((0, 0, 5), (15, 0, 5), (0, 15, 5), 0x0000FF00)      # green, in front, odd x discarded
    # first draw: the far triangle alone, flat; second: both with the discard shader on top
    width = height = 16
    image, _, _ = render([far, near], width, height, DISCARD_ODD_COLUMNS)

    def shade(t, b0, b1, b2, x, y):
        return None if x % 2 else (far, near)[t][3]
    assert image == software_render([far, near], width, height, shade)
    # odd columns: green discarded -> red stays visible behind it; but the
    # red triangle is discarded there too (same shader), so they are empty
    assert image[1] == 0 and image[0] == 0x0000FF00


def test_slots_and_slices_do_not_change_the_image():
    width, height = 32, 32
    tris, data, extra, draw, _ = gouraud_scene(width, height)
    reference, one_slot, _ = render(tris, width, height, GOURAUD_SHADER, draw, data, extra, slots=1)
    four_slots_image, four_slots, _ = render(tris, width, height, GOURAUD_SHADER, draw, data, extra, slots=4)
    sliced, sliced_cycles, _ = render(tris, width, height, GOURAUD_SHADER, draw, data, extra, num_slices=4)
    assert four_slots_image == reference and sliced == reference
    assert four_slots < one_slot
    print(f'gouraud 32x32: 1 slot {one_slot}, 4 slots {four_slots}, 4 slices x 4 slots {sliced_cycles} cycles')

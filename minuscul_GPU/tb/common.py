'''
Shared testbench helpers: scenes, memory setup, simulation loop, the
legacy RenderModule as golden reference, and test-side py4hw components.
'''
import random

import py4hw
import py4hw.simulation

from ..src.memory.memory import MemoryInterface, Memory
from ..src.params import GROUP_WIDTH, LANE_MASK, lane_xy
from ..src.rasteriser.fragment import FragmentGroupInterface, read_fragment_group
from ..src.rasteriser.renderer import RenderModule, TRIANGLE_LINE_WIDTH, pack_triangle_line


# --- scenes: lists of (v1, v2, v3, colour), vertices (x, y, z) -------------

# the 5-triangle 255x255 scene used throughout RASTERISER_ARCHITECTURE.md
CANONICAL_SCENE = [
    ((10, 10, 20), (95, 10, 20), (10, 95, 20), 0xE86A5C),
    ((160, 10, 16), (245, 10, 16), (160, 95, 16), 0x5CC2E8),
    ((10, 160, 24), (95, 245, 24), (10, 245, 24), 0x8DE85C),
    ((160, 160, 12), (245, 160, 12), (200, 245, 12), 0xE8C25C),
    ((85, 85, 6), (170, 85, 6), (127, 170, 6), 0xB07CE8),
]

# the scenes of the original rasterisationTest suite (8x8 screen)
SMALL_SCENES = {
    'single': [((1, 1, 10), (6, 1, 10), (1, 6, 10), 0xAABBCC)],
    'depth_order': [((0, 0, 100), (7, 0, 100), (0, 7, 100), 0x0000FF),
                    ((1, 1, 5), (4, 1, 5), (1, 4, 5), 0x00FF00)],
    'off_screen': [((5, 5, 1), (12, 5, 1), (5, 12, 1), 0x123456)],
    'degenerate': [((0, 0, 1), (4, 0, 1), (8, 0, 1), 0xFF00FF)],
}


def random_scene(seed, count, width, height, margin=10, max_z=50):
    '''Triangles of every size and winding, some partly off-screen, some
    degenerate, with overlapping depths - for differential tests.'''
    rng = random.Random(seed)
    tris = []
    for i in range(count):
        cx, cy = rng.randint(-margin, width + margin), rng.randint(-margin, height + margin)
        size = rng.choice([2, 5, 12, 30, max(width, height)])
        verts = [(cx + rng.randint(-size, size), cy + rng.randint(-size, size), rng.randint(1, max_z))
                 for _ in range(3)]
        if i % 7 == 6:  # degenerate: collinear
            (x0, y0, z0) = verts[0]
            verts = [(x0, y0, z0), (x0 + 3, y0 + 3, z0), (x0 + 6, y0 + 6, z0)]
        tris.append((verts[0], verts[1], verts[2], rng.randint(1, 0xFFFFFF)))
    return tris


# --- simulation helpers -----------------------------------------------------

def triangle_memory(hw, name, triangles, extra_words=None):
    '''512-bit triangle memory preloaded with `triangles`. `extra_words`
    (optional, per triangle) fills line words 10-15.'''
    num_lines = max(len(triangles), 1)
    mem_if = MemoryInterface(hw, name, memory_size=num_lines, data_width=TRIANGLE_LINE_WIDTH)
    mem = Memory(hw, name + 'Impl', mem_if, memory_size=num_lines)
    for i, (v1, v2, v3, color) in enumerate(triangles):
        line = pack_triangle_line(v1, v2, v3, color)
        if extra_words is not None:
            for k, word in enumerate(extra_words[i]):
                line |= (word & 0xFFFFFFFF) << ((10 + k) * 32)
        mem.memory[i] = line
    return mem_if, mem


def constant(hw, name, value, width=32):
    w = hw.wire(name, width)
    w.put(value)
    return w


def run_until(sim, done_wire, max_cycles):
    '''Clocks until `done_wire` is 1; returns the number of cycles.'''
    for cycle in range(1, max_cycles + 1):
        sim._clk_cycle()
        if done_wire.get() == 1:
            return cycle
    raise AssertionError(f'simulation did not finish within {max_cycles} cycles')


def legacy_render(triangles, width, height, max_cycles=2_000_000):
    '''Golden reference: the original RenderModule's framebuffer.'''
    hw = py4hw.HWSystem()
    tri_if, _ = triangle_memory(hw, 'TriMem', triangles)
    fb_if = MemoryInterface(hw, 'FbMem', memory_size=width * height)
    fb = Memory(hw, 'FbMemImpl', fb_if, memory_size=width * height)
    done = hw.wire('done', 1)
    RenderModule(hw, 'renderer', tri_if, fb_if, done, constant(hw, 'first', 0),
                 constant(hw, 'count', len(triangles)), width, height)
    cycles = run_until(py4hw.simulation.Simulator(hw), done, max_cycles)
    return list(fb.memory), cycles


def compose_fragments(groups, width, height):
    '''Software output stage: depth-tests and writes fragment groups in
    order (smaller z wins, first writer wins ties), exactly as the legacy
    RenderController did.'''
    zbuf = [None] * (width * height)
    image = [0] * (width * height)
    for g in groups:
        for lane in range(GROUP_WIDTH):
            if g['coverage'] >> lane & 1:
                dx, dy = lane_xy(lane)
                addr = (g['tile_y'] + dy) * width + g['tile_x'] + dx
                if zbuf[addr] is None or g['z'][lane] < zbuf[addr]:
                    zbuf[addr] = g['z'][lane]
                    image[addr] = g['tri_words'][0]
    return image


# --- test-side components ---------------------------------------------------

class FragmentSink(py4hw.Logic):
    '''Consumes fragment groups and records them. `ready_pattern(cycle)`
    returns the ready value to drive for that cycle (default: always 1),
    so backpressure can be tested.'''

    def __init__(self, parent, name, frag: FragmentGroupInterface, ready_pattern=None):
        super().__init__(parent, name)
        self.frag = self.addInterfaceSink('frag', frag)
        self.ready_pattern = ready_pattern or (lambda cycle: 1)
        self.groups = []
        self.cycle = 0
        frag.ready.put(self.ready_pattern(0))

    def clock(self):
        f = self.frag
        if f.valid.get() == 1 and f.ready.get() == 1:
            self.groups.append(read_fragment_group(f))
        self.cycle += 1
        f.ready.prepare(self.ready_pattern(self.cycle))


def random_ready(seed, probability=0.5):
    rng = random.Random(seed)
    return lambda cycle: 1 if rng.random() < probability else 0


def coverage_pixels(group):
    '''Set of (x, y) pixels a group covers.'''
    pixels = set()
    for lane in range(GROUP_WIDTH):
        if group['coverage'] >> lane & 1:
            dx, dy = lane_xy(lane)
            pixels.add((group['tile_x'] + dx, group['tile_y'] + dy))
    return pixels


__all__ = ['CANONICAL_SCENE', 'SMALL_SCENES', 'random_scene', 'triangle_memory', 'constant',
           'run_until', 'legacy_render', 'compose_fragments', 'FragmentSink', 'random_ready',
           'coverage_pixels', 'LANE_MASK']

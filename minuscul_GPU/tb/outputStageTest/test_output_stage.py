'''
M2 testbench: the OutputStage.

Packets are built from M1's fragment groups (colour = triangle flat colour)
and driven by a test-side ExportSource, in order or shuffled within the
reorder window. The framebuffer must match the legacy renderer exactly.
'''
import random

import py4hw
import py4hw.simulation
import pytest

from ...src.memory.memory import MemoryInterface, Memory
from ...src.output_stage.export import ExportInterface, drive_export
from ...src.output_stage.output_stage import (OutputStage, framebuffer_bank_size,
                                               resolve_banked_framebuffer)
from ...src.params import GROUP_WIDTH, GROUP_SLOTS, LANE_MASK
from ..common import CANONICAL_SCENE, random_scene, legacy_render
from ..rasteriserTest.test_fragment_output import rasterise


class ExportSource(py4hw.Logic):
    '''Drives `packets` (in list order) with the valid/ready handshake.
    `gap(cycle)` true = keep valid low this cycle (bubbles).'''

    def __init__(self, parent, name, exp, packets, gap=None):
        super().__init__(parent, name)
        self.exp = self.addInterfaceSource('exp', exp)
        self.packets = list(packets)
        self.gap = gap or (lambda cycle: False)
        self.index = 0
        self.cycle = 0
        self.ready_low_cycles = 0
        exp.valid.put(0)

    @property
    def finished(self):
        return self.index >= len(self.packets) and self.exp.valid.get() == 0

    def clock(self):
        e = self.exp
        if e.valid.get() == 1:
            if e.ready.get() == 1:
                self.index += 1             # transferred at this edge
            else:
                self.ready_low_cycles += 1
                return                      # hold valid and data
        self.cycle += 1
        if self.index < len(self.packets) and not self.gap(self.cycle):
            drive_export(e, self.packets[self.index])
            e.valid.prepare(1)
        else:
            e.valid.prepare(0)


def packets_from_fragments(groups):
    '''One export packet per fragment group, seq = emission order,
    colour = the triangle's flat colour (what M3's flat-colour unit does).'''
    return [{'seq': seq, 'tile_x': g['tile_x'], 'tile_y': g['tile_y'], 'mask': g['coverage'],
             'colour': [g['tri_words'][0]] * GROUP_WIDTH, 'z': g['z']}
            for seq, g in enumerate(groups)]


def shuffle_in_windows(packets, window, seed):
    '''Out-of-order arrival that never needs more than `window` buffered
    packets: shuffle within consecutive blocks of `window`.'''
    rng = random.Random(seed)
    out = []
    for i in range(0, len(packets), window):
        block = packets[i:i + window]
        rng.shuffle(block)
        out.extend(block)
    return out


def run_output_stage(packets, width, strip_y0, strip_rows, write_bank_count=1,
                     reorder_depth=GROUP_SLOTS, gap=None, max_cycles=500_000):
    hw = py4hw.HWSystem()
    exp = ExportInterface(hw, 'exp')
    idle = hw.wire('idle', 1)
    pixels = width * strip_rows
    if write_bank_count == 1:
        fb_if = MemoryInterface(hw, 'Fb', memory_size=pixels)
        fb = [Memory(hw, 'FbImpl', fb_if, memory_size=pixels)]
        fb_arg = fb_if
    else:
        size = framebuffer_bank_size(pixels, write_bank_count)
        fb_arg = [MemoryInterface(hw, f'Fb{i}', memory_size=size) for i in range(write_bank_count)]
        fb = [Memory(hw, f'FbImpl{i}', fb_arg[i], memory_size=size) for i in range(write_bank_count)]
    stage = OutputStage(hw, 'out', exp, fb_arg, idle, width, strip_y0, strip_rows,
                        write_bank_count=write_bank_count, reorder_depth=reorder_depth)
    source = ExportSource(hw, 'src', exp, packets, gap)
    sim = py4hw.simulation.Simulator(hw)
    for cycle in range(1, max_cycles + 1):
        sim._clk_cycle()
        if source.finished and idle.get() == 1 and stage.packets_done == len(packets):
            break
    else:
        raise AssertionError('output stage did not drain')
    image = resolve_banked_framebuffer(fb, write_bank_count, pixels)
    return image, cycle, stage, source


def scene_packets(triangles, width, height):
    groups, _, _ = rasterise(triangles, width, height)
    return packets_from_fragments(groups)


@pytest.mark.parametrize('write_bank_count', [1, 2, 4, 16])
def test_in_order_matches_legacy_renderer(write_bank_count):
    width, height = 64, 64
    triangles = random_scene(21, 25, width, height)
    image, cycles, _, _ = run_output_stage(scene_packets(triangles, width, height),
                                           width, 0, height, write_bank_count)
    assert image == legacy_render(triangles, width, height)[0]
    print(f'banks={write_bank_count}: {cycles} cycles')


def test_canonical_scene_matches_legacy_renderer():
    image, cycles, stage, _ = run_output_stage(scene_packets(CANONICAL_SCENE, 255, 255), 255, 0, 255)
    assert image == legacy_render(CANONICAL_SCENE, 255, 255)[0]
    print(f'canonical: {cycles} cycles, {stage.pixels_written} pixels written, '
          f'{stage.pixels_depth_failed} failed depth')


@pytest.mark.parametrize('seed', [1, 2, 3])
def test_out_of_order_arrival_gives_same_image(seed):
    width, height = 48, 48
    triangles = random_scene(22, 20, width, height)
    packets = scene_packets(triangles, width, height)
    in_order, _, _, _ = run_output_stage(packets, width, 0, height)
    shuffled = shuffle_in_windows(packets, GROUP_SLOTS, seed)
    assert shuffled != packets
    out_of_order, _, _, _ = run_output_stage(shuffled, width, 0, height)
    assert out_of_order == in_order


def test_full_reorder_buffer_applies_backpressure():
    width, height = 32, 32
    packets = scene_packets(random_scene(23, 10, width, height), width, height)
    # the packet the stage needs next always arrives last in its window, so
    # the buffer fills up and ready must drop
    reordered = []
    for i in range(0, len(packets), 2):
        reordered.extend(reversed(packets[i:i + 2]))
    image, _, _, source = run_output_stage(reordered, width, 0, height, reorder_depth=2)
    assert image == run_output_stage(packets, width, 0, height)[0]
    assert source.ready_low_cycles > 0


def test_bubbles_between_packets():
    width, height = 32, 32
    triangles = random_scene(24, 10, width, height)
    packets = scene_packets(triangles, width, height)
    rng = random.Random(5)
    image, _, _, _ = run_output_stage(packets, width, 0, height, gap=lambda c: rng.random() < 0.4)
    assert image == legacy_render(triangles, width, height)[0]


def test_depth_rules_mask_and_ties():
    full = LANE_MASK
    near, far = [5] * GROUP_WIDTH, [9] * GROUP_WIDTH
    packets = [
        {'seq': 0, 'tile_x': 0, 'tile_y': 0, 'mask': full, 'colour': [0xA] * GROUP_WIDTH, 'z': far},
        # nearer: wins everywhere it is masked in (lanes 0-7 only)
        {'seq': 1, 'tile_x': 0, 'tile_y': 0, 'mask': 0x00FF, 'colour': [0xB] * GROUP_WIDTH, 'z': near},
        # lanes 0-7: equal depth, first writer keeps the pixel;
        # lanes 8-15: nearer than seq 0, so they are written
        {'seq': 2, 'tile_x': 0, 'tile_y': 0, 'mask': full, 'colour': [0xC] * GROUP_WIDTH, 'z': near},
    ]
    image, _, stage, _ = run_output_stage(packets, 4, 0, 4)
    assert image == [0xB] * 8 + [0xC] * 8
    assert stage.pixels_depth_failed == 8


def test_strip_addresses_are_relative_to_strip_start():
    # a strip owning screen rows 8-15 of a 4-wide screen
    packets = [{'seq': 0, 'tile_x': 0, 'tile_y': 8, 'mask': LANE_MASK,
                'colour': list(range(1, GROUP_WIDTH + 1)), 'z': [1] * GROUP_WIDTH}]
    image, _, _, _ = run_output_stage(packets, 4, 8, 8)
    assert image[:16] == list(range(1, GROUP_WIDTH + 1))
    assert image[16:] == [0] * 16

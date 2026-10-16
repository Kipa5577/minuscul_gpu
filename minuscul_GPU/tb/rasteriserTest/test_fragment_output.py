'''
M1 testbench: the fragment-producing Rasteriser.

Golden reference: composing the emitted fragment groups with a software
depth test must give exactly the image the legacy RenderModule draws.
'''
import py4hw
import py4hw.simulation
import pytest

from ...src.params import TILE_SIZE
from ...src.rasteriser.fragment import FragmentGroupInterface
from ...src.rasteriser.rasteriser import Rasteriser
from ..common import (CANONICAL_SCENE, SMALL_SCENES, random_scene, triangle_memory, constant,
                      run_until, legacy_render, compose_fragments, FragmentSink, random_ready,
                      coverage_pixels)


def rasterise(triangles, width, height, ready_pattern=None, partition=(None, None),
              extra_words=None, max_cycles=2_000_000):
    '''Runs a Rasteriser on `triangles`; returns (groups, cycles, controller).'''
    hw = py4hw.HWSystem()
    tri_if, _ = triangle_memory(hw, 'TriMem', triangles, extra_words)
    frag = FragmentGroupInterface(hw, 'frag')
    done = hw.wire('done', 1)
    r = Rasteriser(hw, 'rast', tri_if, frag, done, constant(hw, 'first', 0),
                   constant(hw, 'count', len(triangles)), width, height,
                   partition_y_min=partition[0], partition_y_max=partition[1])
    sink = FragmentSink(hw, 'sink', frag, ready_pattern)
    cycles = run_until(py4hw.simulation.Simulator(hw), done, max_cycles)
    return sink.groups, cycles, r.controller


SCENES = [(name, tris, 8, 8) for name, tris in SMALL_SCENES.items()] + [
    ('random_64', random_scene(1, 25, 64, 64), 64, 64),
    ('random_wide', random_scene(2, 15, 96, 40), 96, 40),
    ('canonical', CANONICAL_SCENE, 255, 255),
]


@pytest.mark.parametrize('name,triangles,width,height', SCENES, ids=[s[0] for s in SCENES])
def test_composed_fragments_match_legacy_renderer(name, triangles, width, height):
    groups, cycles, _ = rasterise(triangles, width, height)
    expected, legacy_cycles = legacy_render(triangles, width, height)
    assert compose_fragments(groups, width, height) == expected
    print(f'{name}: {len(groups)} groups, {cycles} cycles (legacy RenderModule: {legacy_cycles})')


def test_fragment_groups_are_well_formed():
    width, height = 64, 64
    triangles = random_scene(3, 30, width, height)
    groups, _, _ = rasterise(triangles, width, height)
    for g in groups:
        assert g['tile_x'] % TILE_SIZE == 0 and g['tile_y'] % TILE_SIZE == 0
        assert g['coverage'] != 0
        for x, y in coverage_pixels(g):
            assert 0 <= x < width and 0 <= y < height
    # a triangle never emits the same tile twice: groups of one triangle
    # (consecutive runs with equal triangle colour + area) never share a tile
    seen = set()
    previous = None
    for g in groups:
        key = (g['tri_words'][0], g['area'])
        if key != previous:
            seen = set()
            previous = key
        tile = (g['tile_x'], g['tile_y'])
        assert tile not in seen
        seen.add(tile)


@pytest.mark.parametrize('seed', [10, 11])
def test_backpressure_changes_timing_not_output(seed):
    width, height = 48, 48
    triangles = random_scene(4, 12, width, height)
    free, free_cycles, _ = rasterise(triangles, width, height)
    stalled, stalled_cycles, ctrl = rasterise(triangles, width, height,
                                              ready_pattern=random_ready(seed, 0.3))
    assert stalled == free
    assert ctrl.emit_stall_cycles > 0
    assert stalled_cycles > free_cycles


def test_partitions_cover_disjoint_rows_and_union_is_full_image():
    width, height = 40, 40
    triangles = random_scene(5, 15, width, height)
    full = compose_fragments(rasterise(triangles, width, height)[0], width, height)

    strips = [(0, 12), (13, 25), (26, 39)]
    union = [0] * (width * height)
    for y0, y1 in strips:
        groups, _, _ = rasterise(triangles, width, height, partition=(y0, y1))
        for g in groups:
            assert all(y0 <= y <= y1 for _, y in coverage_pixels(g))
        part = compose_fragments(groups, width, height)
        for addr, value in enumerate(part):
            if value:
                union[addr] = value
    assert union == full


def test_triangle_line_words_pass_through():
    triangle = ((1, 1, 3), (14, 2, 3), (2, 13, 3), 0x00ABCDEF)
    extra = [0x00000007, 0x00000008, 0x00000009, 0x3F800000, 0x3F000000, 0x40000000]
    groups, _, _ = rasterise([triangle], 16, 16, extra_words=[extra])
    assert groups
    for g in groups:
        assert g['tri_words'] == [0x00ABCDEF] + extra


def test_no_triangles_finishes_without_output():
    groups, cycles, _ = rasterise([], 8, 8)
    assert groups == [] and cycles <= 3

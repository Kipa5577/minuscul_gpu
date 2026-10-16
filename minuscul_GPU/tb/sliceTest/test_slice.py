'''
M3 testbench: Slice (rasteriser -> flat colour -> output stage) and
SliceArray (binner + N slices). End of build step 2: images must be
pixel-identical to the legacy RenderModule / MultiCoreRenderModule.
'''
import py4hw
import py4hw.simulation
import pytest

from ...src.memory.memory import MemoryInterface, Memory
from ...src.output_stage.output_stage import framebuffer_bank_size, resolve_banked_framebuffer
from ...src.rasteriser.renderer import MultiCoreRenderModule, pack_triangle_line
from ...src.slice.slice import Slice, SliceArray
from ..common import (CANONICAL_SCENE, SMALL_SCENES, random_scene, triangle_memory, constant,
                      run_until, legacy_render)


def render_slice(triangles, width, height, write_bank_count=1, linger=0, max_cycles=2_000_000):
    '''One full-screen Slice. Returns (image, cycles, image_after_linger).'''
    hw = py4hw.HWSystem()
    tri_if, _ = triangle_memory(hw, 'TriMem', triangles)
    pixels = width * height
    if write_bank_count == 1:
        fb_arg = MemoryInterface(hw, 'Fb', memory_size=pixels)
        banks = [Memory(hw, 'FbImpl', fb_arg, memory_size=pixels)]
    else:
        size = framebuffer_bank_size(pixels, write_bank_count)
        fb_arg = [MemoryInterface(hw, f'Fb{i}', memory_size=size) for i in range(write_bank_count)]
        banks = [Memory(hw, f'FbImpl{i}', fb_arg[i], memory_size=size) for i in range(write_bank_count)]
    done = hw.wire('done', 1)
    Slice(hw, 'slice', tri_if, fb_arg, done, constant(hw, 'first', 0),
          constant(hw, 'count', len(triangles)), width, height, write_bank_count=write_bank_count)
    sim = py4hw.simulation.Simulator(hw)
    cycles = run_until(sim, done, max_cycles)
    image = resolve_banked_framebuffer(banks, write_bank_count, pixels)
    for _ in range(linger):
        sim._clk_cycle()
        assert done.get() == 1
    return image, cycles, resolve_banked_framebuffer(banks, write_bank_count, pixels)


def render_array(triangles, width, height, num_slices, write_bank_count=1, legacy=False,
                 max_cycles=2_000_000):
    hw = py4hw.HWSystem()
    done = hw.wire('done', 1)
    lines = [pack_triangle_line(*t) for t in triangles]
    args = (hw, 'array', num_slices, lines, done, constant(hw, 'first', 0),
            constant(hw, 'count', len(triangles)), width, height)
    array = MultiCoreRenderModule(*args) if legacy else SliceArray(*args, write_bank_count=write_bank_count)
    cycles = run_until(py4hw.simulation.Simulator(hw), done, max_cycles)
    return array.resolve_framebuffer(), cycles


SLICE_SCENES = [(name, tris, 8, 8) for name, tris in SMALL_SCENES.items()] + [
    ('random_64', random_scene(31, 25, 64, 64), 64, 64),
    ('canonical', CANONICAL_SCENE, 255, 255),
]


@pytest.mark.parametrize('name,triangles,width,height', SLICE_SCENES, ids=[s[0] for s in SLICE_SCENES])
def test_slice_matches_legacy_render_module(name, triangles, width, height):
    image, cycles, _ = render_slice(triangles, width, height)
    expected, legacy_cycles = legacy_render(triangles, width, height)
    assert image == expected
    print(f'{name}: slice {cycles} cycles, legacy RenderModule {legacy_cycles}')


@pytest.mark.parametrize('write_bank_count', [2, 4, 16])
def test_banked_slice_matches_single_port(write_bank_count):
    triangles = random_scene(32, 20, 48, 48)
    assert render_slice(triangles, 48, 48, write_bank_count)[0] == render_slice(triangles, 48, 48)[0]


def test_done_rises_only_after_the_last_write():
    triangles = random_scene(33, 15, 40, 40)
    image, _, after = render_slice(triangles, 40, 40, linger=50)
    assert image == after == legacy_render(triangles, 40, 40)[0]


def test_empty_scene():
    image, cycles, _ = render_slice([], 8, 8)
    assert image == [0] * 64 and cycles <= 5


@pytest.mark.parametrize('num_slices', [1, 2, 3, 8])
def test_slice_array_matches_single_core(num_slices):
    width, height = 48, 40
    triangles = random_scene(34, 20, width, height)
    image, _ = render_array(triangles, width, height, num_slices)
    assert image == legacy_render(triangles, width, height)[0]


def test_slice_array_matches_legacy_multicore_on_canonical_scene():
    image, cycles = render_array(CANONICAL_SCENE, 255, 255, 8)
    legacy_image, legacy_cycles = render_array(CANONICAL_SCENE, 255, 255, 8, legacy=True)
    assert image == legacy_image
    print(f'canonical, 8 slices: {cycles} cycles (legacy MultiCoreRenderModule: {legacy_cycles})')


def test_banked_slice_array():
    triangles = random_scene(35, 15, 32, 32)
    image, _ = render_array(triangles, 32, 32, 4, write_bank_count=4)
    assert image == legacy_render(triangles, 32, 32)[0]


def test_more_slices_than_rows():
    triangles = random_scene(36, 6, 16, 6)
    image, _ = render_array(triangles, 16, 6, 10)
    assert image == legacy_render(triangles, 16, 6)[0]

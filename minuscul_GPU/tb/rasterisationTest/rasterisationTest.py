import py4hw
import py4hw.simulation
import pytest

if __package__:
    from ...src.memory.memory import MemoryInterface, Memory
    from ...src.rasteriser.renderer import (
        RenderModule, TRIANGLE_LINE_WIDTH, pack_triangle_line,
        framebuffer_bank_size, resolve_banked_framebuffer,
    )
else:
    # allows running this file directly (e.g. `python rasterisationTest.py`),
    # where there is no parent package for a relative import to resolve against
    import os
    import sys
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))
    from minuscul_GPU.src.memory.memory import MemoryInterface, Memory
    from minuscul_GPU.src.rasteriser.renderer import (
        RenderModule, TRIANGLE_LINE_WIDTH, pack_triangle_line,
        framebuffer_bank_size, resolve_banked_framebuffer,
    )

SCREEN_WIDTH = 8
SCREEN_HEIGHT = 8
MAX_CYCLES = 20000


def build_renderer(triangles, screen_width=SCREEN_WIDTH, screen_height=SCREEN_HEIGHT,
                    write_bank_count=1):
    '''
    Wires a RenderModule to a triangle memory preloaded with `triangles`
    (each a (v1, v2, v3, color) tuple of (x, y, z) vertices) and an empty
    framebuffer memory, and returns (done_wire, framebuffer_memory, run).

    write_bank_count > 1 splits the framebuffer into that many interleaved
    write-port banks instead of a single one; `framebuffer_memory` is then a
    list of bank Memory objects (read it back with resolve_banked_framebuffer)
    rather than a single Memory.
    '''
    hw = py4hw.HWSystem()

    num_lines = max(len(triangles), 1)
    triangle_mem_if = MemoryInterface(hw, 'TriMem', memory_size=num_lines, data_width=TRIANGLE_LINE_WIDTH)
    triangle_mem = Memory(hw, 'TriMemImpl', triangle_mem_if, memory_size=num_lines)
    for i, (v1, v2, v3, color) in enumerate(triangles):
        triangle_mem.memory[i] = pack_triangle_line(v1, v2, v3, color)

    pixel_count = screen_width * screen_height
    if write_bank_count == 1:
        fb_mem_if = MemoryInterface(hw, 'FbMem', memory_size=pixel_count)
        fb_mem = Memory(hw, 'FbMemImpl', fb_mem_if, memory_size=pixel_count)
        fb_arg = fb_mem_if
        framebuffer_memory = fb_mem
    else:
        bank_size = framebuffer_bank_size(pixel_count, write_bank_count)
        fb_mem_ifs = [MemoryInterface(hw, f'FbMem{i}', memory_size=bank_size)
                      for i in range(write_bank_count)]
        fb_arg = fb_mem_ifs
        framebuffer_memory = [Memory(hw, f'FbMemImpl{i}', fb_mem_ifs[i], memory_size=bank_size)
                               for i in range(write_bank_count)]

    done = hw.wire('done', 1)
    first_addr = hw.wire('first_addr', 32)
    num_tri = hw.wire('num_tri', 32)
    first_addr.put(0)
    num_tri.put(len(triangles))

    RenderModule(hw, 'renderer', triangle_mem_if, fb_arg,
                 done, first_addr, num_tri, screen_width, screen_height,
                 write_bank_count=write_bank_count)

    sim = py4hw.simulation.Simulator(hw)

    def run():
        for _ in range(MAX_CYCLES):
            sim._clk_cycle()
            if done.get() == 1:
                return
        raise AssertionError(f'simulation did not finish within {MAX_CYCLES} cycles')

    return done, framebuffer_memory, run


def pixel(fb_mem, x, y, width=SCREEN_WIDTH):
    return fb_mem.memory[y * width + x]


def test_single_triangle_is_filled_with_its_colour():
    triangle = ((1, 1, 10), (6, 1, 10), (1, 6, 10), 0xAABBCC)
    done, fb_mem, run = build_renderer([triangle])

    run()
    assert done.get() == 1

    covered = sum(1 for v in fb_mem.memory if v != 0)
    # right triangle with legs of length 5: 6+5+4+3+2+1 = 21 pixels
    assert covered == 21
    assert pixel(fb_mem, 2, 2) == 0xAABBCC
    assert pixel(fb_mem, 0, 0) == 0  # outside the triangle stays untouched


def test_nearer_triangle_wins_the_depth_test_regardless_of_draw_order():
    far = ((0, 0, 100), (7, 0, 100), (0, 7, 100), 0x0000FF)   # drawn first, far away
    near = ((1, 1, 5), (4, 1, 5), (1, 4, 5), 0x00FF00)         # drawn second, closer
    done, fb_mem, run = build_renderer([far, near])

    run()
    assert done.get() == 1

    assert pixel(fb_mem, 2, 2) == 0x00FF00  # inside both: nearer green wins
    assert pixel(fb_mem, 1, 5) == 0x0000FF  # inside far triangle only: blue survives


def test_triangle_partially_off_screen_is_clamped_not_crashed():
    # this triangle's bounding box extends past the right/bottom edges
    triangle = ((5, 5, 1), (12, 5, 1), (5, 12, 1), 0x123456)
    done, fb_mem, run = build_renderer([triangle], screen_width=8, screen_height=8)

    run()
    assert done.get() == 1
    assert pixel(fb_mem, 6, 6) == 0x123456
    assert pixel(fb_mem, 0, 0) == 0


def test_degenerate_triangle_is_skipped():
    # all three vertices collinear -> zero area, must not hang or write anything
    triangle = ((0, 0, 1), (4, 0, 1), (8, 0, 1), 0xFF00FF)
    done, fb_mem, run = build_renderer([triangle])

    run()
    assert done.get() == 1
    assert all(v == 0 for v in fb_mem.memory)


@pytest.mark.parametrize('write_bank_count', [2, 4, 8])
def test_banked_framebuffer_matches_single_port_output(write_bank_count):
    # same two-triangle depth-test scene as
    # test_nearer_triangle_wins_the_depth_test_regardless_of_draw_order,
    # rendered once through the default single write port and once through
    # `write_bank_count` interleaved banks - the banked version must produce
    # pixel-for-pixel identical output, just with more of its writes issued
    # in parallel.
    far = ((0, 0, 100), (7, 0, 100), (0, 7, 100), 0x0000FF)
    near = ((1, 1, 5), (4, 1, 5), (1, 4, 5), 0x00FF00)
    triangles = [far, near]

    done1, fb_mem, run1 = build_renderer(triangles, write_bank_count=1)
    run1()
    assert done1.get() == 1
    expected = list(fb_mem.memory)

    done2, banks, run2 = build_renderer(triangles, write_bank_count=write_bank_count)
    run2()
    assert done2.get() == 1
    pixel_count = SCREEN_WIDTH * SCREEN_HEIGHT
    actual = resolve_banked_framebuffer(banks, write_bank_count, pixel_count)

    assert actual == expected


if __name__ == '__main__':
    import sys
    sys.exit(pytest.main([__file__, '-v']))

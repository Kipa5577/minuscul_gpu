'''
Benchmark for RenderModule.

For a set of parameterised scenes (varying triangle count, triangle size and
screen resolution) this measures, per scenario:
  - instantiation time   (building HWSystem + memories + RenderModule)
  - simulator build time (py4hw.simulation.Simulator's topological sort + first propagate)
  - simulation run time  (wall-clock time to run clock cycles until `done`)
  - clock cycles used    (how many hardware cycles the rasteriser actually took)
  - time per clock cycle (simulation run time / cycles - a Python-simulation cost,
                           NOT a hardware cycle time)
  - estimated hardware render time if the design were clocked at 50 MHz
                           (= cycles / 50e6, independent of how long the Python
                           simulation itself took to compute those cycles)

Run directly:
    gpu_env/bin/python rasterisationBenchmark.py
'''

import time

import py4hw
import py4hw.simulation

if __package__:
    from ...src.memory.memory import MemoryInterface, Memory
    from ...src.rasteriser.renderer import RenderModule, TRIANGLE_LINE_WIDTH, pack_triangle_line
else:
    import os
    import sys
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..')))
    from minuscul_GPU.src.memory.memory import MemoryInterface, Memory
    from minuscul_GPU.src.rasteriser.renderer import RenderModule, TRIANGLE_LINE_WIDTH, pack_triangle_line

CLOCK_HZ = 50_000_000
MAX_CYCLES = 2_000_000


def make_triangle(x0, y0, size, z, color):
    '''Right triangle with both legs `size` pixels long, top-left corner at (x0,y0).'''
    return ((x0, y0, z), (x0 + size, y0, z), (x0, y0 + size, z), color)


def build(triangles, screen_width, screen_height):
    '''Builds the system and returns (done_wire, run_one_cycle_fn), separated
    from the two construction phases so each can be timed on its own.'''
    hw = py4hw.HWSystem()

    num_lines = max(len(triangles), 1)
    triangle_mem_if = MemoryInterface(hw, 'TriMem', memory_size=num_lines, data_width=TRIANGLE_LINE_WIDTH)
    triangle_mem = Memory(hw, 'TriMemImpl', triangle_mem_if, memory_size=num_lines)
    for i, (v1, v2, v3, color) in enumerate(triangles):
        triangle_mem.memory[i] = pack_triangle_line(v1, v2, v3, color)

    fb_mem_if = MemoryInterface(hw, 'FbMem', memory_size=screen_width * screen_height)
    Memory(hw, 'FbMemImpl', fb_mem_if, memory_size=screen_width * screen_height)

    done = hw.wire('done', 1)
    first_addr = hw.wire('first_addr', 32)
    num_tri = hw.wire('num_tri', 32)
    first_addr.put(0)
    num_tri.put(len(triangles))

    RenderModule(hw, 'renderer', triangle_mem_if, fb_mem_if,
                 done, first_addr, num_tri, screen_width, screen_height)

    return hw, done


def run_scenario(label, triangles, screen_width, screen_height):
    t0 = time.perf_counter()
    hw, done = build(triangles, screen_width, screen_height)
    t1 = time.perf_counter()

    sim = py4hw.simulation.Simulator(hw)
    t2 = time.perf_counter()

    cycles = 0
    while done.get() != 1:
        sim._clk_cycle()
        cycles += 1
        if cycles >= MAX_CYCLES:
            raise RuntimeError(f'{label}: did not finish within {MAX_CYCLES} cycles')
    t3 = time.perf_counter()

    instantiation_s = t1 - t0
    simulator_build_s = t2 - t1
    simulation_run_s = t3 - t2
    per_cycle_s = simulation_run_s / cycles if cycles else 0.0
    hw_time_s = cycles / CLOCK_HZ

    return {
        'label': label,
        'triangles': len(triangles),
        'screen': f'{screen_width}x{screen_height}',
        'cycles': cycles,
        'instantiation_s': instantiation_s,
        'simulator_build_s': simulator_build_s,
        'simulation_run_s': simulation_run_s,
        'per_cycle_s': per_cycle_s,
        'hw_time_s': hw_time_s,
    }


def fmt_time(seconds):
    if seconds < 1e-3:
        return f'{seconds * 1e6:.2f} us'
    if seconds < 1:
        return f'{seconds * 1e3:.3f} ms'
    return f'{seconds:.3f} s'


def print_report(results):
    headers = ['scenario', 'tris', 'screen', 'cycles', 'instantiate', 'sim build',
               'sim run', 'per cycle', 'est. @50MHz']
    rows = []
    for r in results:
        rows.append([
            r['label'],
            str(r['triangles']),
            r['screen'],
            f"{r['cycles']:,}",
            fmt_time(r['instantiation_s']),
            fmt_time(r['simulator_build_s']),
            fmt_time(r['simulation_run_s']),
            fmt_time(r['per_cycle_s']),
            fmt_time(r['hw_time_s']),
        ])

    widths = [max(len(h), *(len(row[i]) for row in rows)) for i, h in enumerate(headers)]
    line = ' | '.join(h.ljust(w) for h, w in zip(headers, widths))
    print(line)
    print('-+-'.join('-' * w for w in widths))
    for row in rows:
        print(' | '.join(c.ljust(w) for c, w in zip(row, widths)))


def main():
    results = []

    # --- vary number of triangles (fixed 10x10 triangle each, 64x64 screen) ---
    for count in (1, 5, 20, 50):
        triangles = [make_triangle(2, 2, 10, 10, 0x00FF00 + i) for i in range(count)]
        results.append(run_scenario(f'{count} triangles (10x10 each)', triangles, 64, 64))

    # --- vary triangle size (single triangle, 128x128 screen) ---
    for size in (5, 10, 20, 40):
        triangles = [make_triangle(2, 2, size, 10, 0x00FF00)]
        results.append(run_scenario(f'1 triangle ({size}x{size})', triangles, 128, 128))

    # --- vary screen resolution (fixed small triangle, resolution only affects
    #     addressing/clamping, not the pixels actually tested) ---
    for w, h in ((32, 32), (64, 64), (128, 128), (256, 256)):
        triangles = [make_triangle(2, 2, 10, 10, 0x00FF00)]
        results.append(run_scenario(f'1 triangle (10x10), {w}x{h} screen', triangles, w, h))

    print_report(results)

    print()
    print(f'Clock cycle time at {CLOCK_HZ/1e6:.0f} MHz = {1/CLOCK_HZ*1e9:.1f} ns')
    print('"est. @50MHz" is cycles/50e6, i.e. how long the modelled hardware would')
    print('take to render that exact scene if the design ran at 50 MHz with single-')
    print('cycle memory access - it does not depend on how long this Python')
    print('simulation itself took to compute those cycles ("sim run" above).')


if __name__ == '__main__':
    main()

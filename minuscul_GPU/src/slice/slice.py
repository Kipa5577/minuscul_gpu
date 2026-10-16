import py4hw

from ..binner.binner import AllDone, Binner
from ..memory.memory import MemoryInterface, Memory
from ..output_stage.export import ExportInterface
from ..output_stage.output_stage import OutputStage, framebuffer_bank_size, resolve_banked_framebuffer
from ..rasteriser.fragment import FragmentGroupInterface
from ..rasteriser.rasteriser import Rasteriser
from ..rasteriser.triangle_line import TRIANGLE_LINE_WIDTH
from ..dispatcher.dispatcher import DrawState, PixelDispatcher
from ..params import GROUP_SLOTS
from ..shader_core.core import LaunchInterface, ShaderCore
from .flat_colour import FlatColourUnit

'''
Slice and slice array (work module M3, decision D3).

  Slice      = Rasteriser -> shading -> OutputStage, owning one strip of
               screen rows and its framebuffer strip
  SliceArray = Binner + NUM_SLICES slices + all-done, the replacement for
               MultiCoreRenderModule

Shading modes:
  'flat'   - FlatColourUnit (M3): every covered pixel gets the triangle's
             flat colour
  'shader' - PixelDispatcher -> ShaderCore (M6): a VENUS v2 pixel shader
             computes each pixel. The core's data port goes to `data_mem`.
             Until the shared memory system exists (M7), SliceArray gives
             every slice a private copy of the data memory.
'''


class Slice(py4hw.Logic):
    '''
    One slice. `framebuffer_mem` is a MemoryInterface sized for this
    slice's strip (rows strip_y0..strip_y1), or a list of
    `write_bank_count` interfaces for a banked strip.

    Same constructor arguments as the legacy RenderModule, with the strip
    given as partition_y_min/partition_y_max (the strip is the whole screen
    when omitted), so it can replace RenderModule directly.

    `done` = rasteriser done AND flat-colour unit idle AND output stage
    idle. The rasteriser only reaches done 2+ cycles after its last
    transfer, by which time the downstream units already report busy, so
    `done` can't rise before the last pixel is written.
    '''

    def __init__(self, parent, name, triangle_mem: MemoryInterface, framebuffer_mem,
                 done, first_triangle_address, number_of_triangles,
                 screen_width: int, screen_height: int,
                 partition_y_min: int = None, partition_y_max: int = None,
                 start=None, write_bank_count: int = 1,
                 shading: str = 'flat', program=None, draw: DrawState = None,
                 data_mem: MemoryInterface = None, slots: int = GROUP_SLOTS):
        super().__init__(parent, name)

        strip_y0 = 0 if partition_y_min is None else partition_y_min
        strip_y1 = screen_height - 1 if partition_y_max is None else partition_y_max

        frag = FragmentGroupInterface(self, 'frag')
        exp = ExportInterface(self, 'exp')
        rast_done = self.wire('rast_done', 1)
        shade_idle = self.wire('shade_idle', 1)
        out_idle = self.wire('out_idle', 1)

        self.rasteriser = Rasteriser(self, 'rasteriser', triangle_mem, frag, rast_done,
                                     first_triangle_address, number_of_triangles,
                                     screen_width, screen_height, strip_y0, strip_y1, start)
        if shading == 'flat':
            self.shading = FlatColourUnit(self, 'flat_colour', frag, exp, shade_idle)
            idles = [rast_done, shade_idle]
        elif shading == 'shader':
            assert program is not None and data_mem is not None, 'shader mode needs a program and data_mem'
            launch = LaunchInterface(self, 'launch')
            core_idle = self.wire('core_idle', 1)
            self.dispatcher = PixelDispatcher(self, 'dispatcher', frag, launch, shade_idle,
                                              draw or DrawState())
            self.core = ShaderCore(self, 'core', launch, data_mem, exp, core_idle, slots=slots)
            self.core.load_program(program)
            idles = [rast_done, shade_idle, core_idle]
        else:
            raise ValueError(f'unknown shading mode {shading!r}')
        self.output = OutputStage(self, 'output', exp, framebuffer_mem, out_idle,
                                  screen_width, strip_y0, strip_y1 - strip_y0 + 1,
                                  write_bank_count=write_bank_count)
        AllDone(self, 'done', idles + [out_idle], done)


class SliceArray(py4hw.Logic):
    '''
    NUM_SLICES slices, each owning a horizontal strip of rows, fed by the
    Binner from one shared source triangle memory. Same interface as the
    legacy MultiCoreRenderModule; call resolve_framebuffer() after `done`.
    '''

    def __init__(self, parent, name, num_slices: int, triangle_lines,
                 done, first_triangle_address, number_of_triangles,
                 screen_width: int, screen_height: int, write_bank_count: int = 1,
                 shading: str = 'flat', program=None, draw: DrawState = None,
                 data_memory=None, slots: int = GROUP_SLOTS):
        super().__init__(parent, name)

        self.screen_width = screen_width
        self.screen_height = screen_height
        self.write_bank_count = write_bank_count
        self.slice_framebuffers = []   # per slice: list of bank Memory objects
        self.slice_partitions = []
        self.slices = []
        self.data_memories = []        # shader mode: per-slice private copies (until M7)

        rows_per_slice = -(-screen_height // num_slices)
        num_lines = max(len(triangle_lines), 1)

        source_if = MemoryInterface(self, 'SourceTriMem', memory_size=num_lines,
                                    data_width=TRIANGLE_LINE_WIDTH)
        source = Memory(self, 'SourceTriMemImpl', source_if, memory_size=num_lines)
        source.memory[:len(triangle_lines)] = list(triangle_lines)

        zero = self.wire('zero_addr', 32)
        zero.put(0)
        binning_done = self.wire('binning_done', 1)

        tri_memories, counts, dones = [], [], []
        for i in range(num_slices):
            y0 = i * rows_per_slice
            y1 = min(screen_height, y0 + rows_per_slice) - 1
            if y1 < y0:
                continue  # more slices than rows

            tri_if = MemoryInterface(self, f'TriMem{i}', memory_size=num_lines,
                                     data_width=TRIANGLE_LINE_WIDTH)
            tri_memories.append(Memory(self, f'TriMemImpl{i}', tri_if, memory_size=num_lines))

            pixels = screen_width * (y1 - y0 + 1)
            if write_bank_count == 1:
                fb_ifs = [MemoryInterface(self, f'Fb{i}', memory_size=pixels)]
                sizes = [pixels]
            else:
                size = framebuffer_bank_size(pixels, write_bank_count)
                fb_ifs = [MemoryInterface(self, f'Fb{i}_{b}', memory_size=size)
                          for b in range(write_bank_count)]
                sizes = [size] * write_bank_count
            banks = [Memory(self, f'{fb_if.name}Impl', fb_if, memory_size=size)
                     for fb_if, size in zip(fb_ifs, sizes)]
            self.slice_framebuffers.append(banks)
            self.slice_partitions.append((y0, y1))

            count = self.wire(f'slice_triangle_count{i}', 32)
            slice_done = self.wire(f'slice_done{i}', 1)
            counts.append(count)
            dones.append(slice_done)
            data_if = None
            if shading == 'shader':
                words = list(data_memory or [0])
                data_if = MemoryInterface(self, f'Data{i}', memory_size=len(words))
                data = Memory(self, f'DataImpl{i}', data_if, memory_size=len(words))
                data.memory[:] = words
                self.data_memories.append(data)

            self.slices.append(Slice(self, f'slice{i}', tri_if,
                                     fb_ifs[0] if write_bank_count == 1 else fb_ifs,
                                     slice_done, zero, count, screen_width, screen_height,
                                     y0, y1, start=binning_done,
                                     write_bank_count=write_bank_count, shading=shading,
                                     program=program, draw=draw, data_mem=data_if, slots=slots))

        Binner(self, 'binner', source_if, first_triangle_address, number_of_triangles,
               tri_memories, self.slice_partitions, counts, binning_done)
        AllDone(self, 'all_done', dones, done)

    def resolve_framebuffer(self):
        '''Stitches every slice's strip into one flat
        screen_width * screen_height list of pixels.'''
        combined = [0] * (self.screen_width * self.screen_height)
        for banks, (y0, y1) in zip(self.slice_framebuffers, self.slice_partitions):
            pixels = self.screen_width * (y1 - y0 + 1)
            strip = resolve_banked_framebuffer(banks, self.write_bank_count, pixels)
            combined[y0 * self.screen_width:(y1 + 1) * self.screen_width] = strip
        return combined

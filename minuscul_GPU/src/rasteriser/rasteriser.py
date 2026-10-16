import py4hw

from ..memory.memory import MemoryInterface, TrinagleInfo
from ..params import GROUP_WIDTH, TILE_SIZE, WORD_MASK, lane_xy
from .fragment import FragmentGroupInterface, TRI_WORDS_FIRST, TRI_WORDS_COUNT
from .renderer import BoundingBoxCalculator, PointInTriangle, BarycentricDepth, RenderController
from .triangle_line import unpack_triangle_line, TRIANGLE_FIELD_WIDTH

'''
Fragment-producing rasteriser (work module M1).

Same front half as RenderController - fetch a 512-bit triangle line,
clamp its bounding box, walk it with the hierarchical tile test and the
per-triangle adaptive coarse tile size - but instead of depth-testing and
writing pixels itself, it emits one fragment group per covered 4x4 tile
(see fragment.py). Depth test and framebuffer writes live in the output
stage (M2).

Tiles are always 4x4-aligned, so the GROUP_WIDTH (16) pixel-test lanes
cover exactly one tile per round: lane i tests pixel
(tile_x + i % 4, tile_y + i // 4). A 4x4 tile therefore skips the corner
test (one 2-cycle lane round tests all 16 pixels, cheaper than 4 corner
rounds); only coarse tiles (8x8, 16x16) are corner-tested first, then
split into 4x4 tiles if not rejected.
'''


class RasteriserController(py4hw.Logic):
    '''
    States:
      WAIT_START                - hold until `start` (binner finished)
      FETCH_ISSUE/FETCH_WAIT    - read the triangle line, unpack it
      BBOX                      - clamp the bbox to screen and partition,
                                  pick the coarse tile size, seed the
                                  tile worklist
      TILE_POP                  - next tile; skip it if it misses the bbox
      CORNER_SETUP/CORNER_READ  - coarse tiles only: test the 4 corners on
                                  lane 0; reject if all are outside the
                                  same edge, else push its 4x4 sub-tiles
      PIX_SETUP/PIX_READ        - 4x4 tiles: test all 16 pixels at once,
                                  build the coverage mask
      EMIT                      - hold the fragment group until `ready`
      NEXT_TRIANGLE / DONE
    '''

    (S_WAIT_START, S_FETCH_ISSUE, S_FETCH_WAIT, S_BBOX, S_TILE_POP,
     S_CORNER_SETUP, S_CORNER_READ, S_PIX_SETUP, S_PIX_READ, S_EMIT,
     S_NEXT_TRIANGLE, S_DONE) = range(12)

    # same per-triangle adaptive coarse tile bands as RenderController
    # (RASTERISER_ARCHITECTURE.md #10)
    COARSE_TILE_BANDS = RenderController.COARSE_TILE_BANDS
    COARSE_TILE_SIZE_LARGE = RenderController.COARSE_TILE_SIZE_LARGE

    @classmethod
    def _coarse_tile_size_for_extent(cls, extent):
        for threshold, size in cls.COARSE_TILE_BANDS:
            if extent < threshold:
                return size
        return cls.COARSE_TILE_SIZE_LARGE

    def __init__(self, parent, name, triangle_mem: MemoryInterface, frag: FragmentGroupInterface,
                 done, first_triangle_address, number_of_triangles,
                 triangle_info: TrinagleInfo, Pxs, Pys,
                 Xmin, Xmax, Ymin, Ymax,
                 Insides, W0s, W1s, W2s, Areas, Zinterps,
                 screen_width: int, screen_height: int,
                 partition_y_min: int = None, partition_y_max: int = None,
                 start=None):
        super().__init__(parent, name)

        assert len(Pxs) == GROUP_WIDTH, 'one pixel-test lane per thread of a group'

        self.triangle_mem = self.addInterfaceSource('triangle_mem', triangle_mem)
        self.frag = self.addInterfaceSource('frag', frag)

        self.done = self.addOut('done', done)
        self.first_triangle_address = self.addIn('first_triangle_address', first_triangle_address)
        self.number_of_triangles = self.addIn('number_of_triangles', number_of_triangles)

        if start is None:
            start = self.wire('always_start', 1)
            start.put(1)
        self.start = self.addIn('start', start)

        self.V = [self.addOut(n, getattr(triangle_info, n)) for n in
                  ('V1X', 'V1Y', 'V1Z', 'V2X', 'V2Y', 'V2Z', 'V3X', 'V3Y', 'V3Z')]

        self.Px = [self.addOut(f'Px{i}', w) for i, w in enumerate(Pxs)]
        self.Py = [self.addOut(f'Py{i}', w) for i, w in enumerate(Pys)]

        self.Xmin = self.addIn('Xmin', Xmin)
        self.Xmax = self.addIn('Xmax', Xmax)
        self.Ymin = self.addIn('Ymin', Ymin)
        self.Ymax = self.addIn('Ymax', Ymax)

        self.Inside = [self.addIn(f'Inside{i}', w) for i, w in enumerate(Insides)]
        self.W0 = [self.addIn(f'W0_{i}', w) for i, w in enumerate(W0s)]
        self.W1 = [self.addIn(f'W1_{i}', w) for i, w in enumerate(W1s)]
        self.W2 = [self.addIn(f'W2_{i}', w) for i, w in enumerate(W2s)]
        self.Area = [self.addIn(f'Area{i}', w) for i, w in enumerate(Areas)]
        self.Zinterp = [self.addIn(f'Zinterp{i}', w) for i, w in enumerate(Zinterps)]

        self.screen_width = screen_width
        self.screen_height = screen_height
        self.partition_y_min = 0 if partition_y_min is None else partition_y_min
        self.partition_y_max = screen_height - 1 if partition_y_max is None else partition_y_max

        self.state = self.S_WAIT_START
        self.triangle_index = 0
        self.tri_words = [0] * TRI_WORDS_COUNT
        self.xmin = self.xmax = self.ymin = self.ymax = 0
        # worklist of (x0, y0, size): size == TILE_SIZE -> pixel test,
        # larger -> corner test first
        self.tile_stack = []
        self.tile_x0 = self.tile_y0 = self.tile_size = 0
        self.corner_list = []
        self.corner_idx = 0
        self.edge_all_outside = [True, True, True]

        # statistics, read by testbenches and benchmarks
        self.groups_emitted = 0
        self.emit_stall_cycles = 0

    def _signed(self, wire):
        return py4hw.IntegerHelper.c2_to_signed(wire.get(), TRIANGLE_FIELD_WIDTH)

    def clock(self):
        m = self.triangle_mem
        f = self.frag

        if self.state == self.S_WAIT_START:
            if self.start.get() == 1:
                self.state = self.S_DONE if self.number_of_triangles.get() == 0 else self.S_FETCH_ISSUE

        elif self.state == self.S_FETCH_ISSUE:
            m.address.prepare(self.first_triangle_address.get() + self.triangle_index)
            m.read.prepare(1)
            m.write.prepare(0)
            self.state = self.S_FETCH_WAIT

        elif self.state == self.S_FETCH_WAIT:
            if m.done.get() == 1:
                m.read.prepare(0)
                line = m.read_data.get()
                for port, value in zip(self.V, unpack_triangle_line(line)[:9]):
                    port.prepare(value & WORD_MASK)
                self.tri_words = [(line >> ((TRI_WORDS_FIRST + i) * TRIANGLE_FIELD_WIDTH)) & WORD_MASK
                                  for i in range(TRI_WORDS_COUNT)]
                self.state = self.S_BBOX

        elif self.state == self.S_BBOX:
            self.xmin = max(0, self._signed(self.Xmin))
            self.xmax = min(self.screen_width - 1, self._signed(self.Xmax))
            self.ymin = max(0, self.partition_y_min, self._signed(self.Ymin))
            self.ymax = min(self.screen_height - 1, self.partition_y_max, self._signed(self.Ymax))

            if self.xmin > self.xmax or self.ymin > self.ymax:
                self.state = self.S_NEXT_TRIANGLE  # fully off-screen / outside the partition
            else:
                extent = max(self.xmax - self.xmin + 1, self.ymax - self.ymin + 1)
                size = self._coarse_tile_size_for_extent(extent)
                x0 = (self.xmin // size) * size
                y0 = (self.ymin // size) * size
                self.tile_stack = [(tx, ty, size)
                                   for ty in range(y0, self.ymax + 1, size)
                                   for tx in range(x0, self.xmax + 1, size)]
                self.state = self.S_TILE_POP

        elif self.state == self.S_TILE_POP:
            if not self.tile_stack:
                self.state = self.S_NEXT_TRIANGLE
            else:
                tx, ty, size = self.tile_stack.pop()
                overlaps = (tx <= self.xmax and tx + size - 1 >= self.xmin and
                            ty <= self.ymax and ty + size - 1 >= self.ymin)
                if overlaps:
                    self.tile_x0, self.tile_y0, self.tile_size = tx, ty, size
                    if size == TILE_SIZE:
                        self.state = self.S_PIX_SETUP
                    else:
                        self.corner_list = [(tx, ty), (tx + size - 1, ty),
                                            (tx, ty + size - 1), (tx + size - 1, ty + size - 1)]
                        self.corner_idx = 0
                        self.edge_all_outside = [True, True, True]
                        self.state = self.S_CORNER_SETUP

        elif self.state == self.S_CORNER_SETUP:
            cx, cy = self.corner_list[self.corner_idx]
            self.Px[0].prepare(cx & WORD_MASK)
            self.Py[0].prepare(cy & WORD_MASK)
            self.state = self.S_CORNER_READ

        elif self.state == self.S_CORNER_READ:
            # sound trivial reject: every corner strictly on the excluded
            # side of the *same* edge (see RASTERISER_ARCHITECTURE.md #1)
            area = self._signed(self.Area[0])
            for i, wire in enumerate((self.W0[0], self.W1[0], self.W2[0])):
                value = self._signed(wire)
                if not ((value > 0 and area < 0) or (value < 0 and area > 0)):
                    self.edge_all_outside[i] = False

            self.corner_idx += 1
            if self.corner_idx < 4:
                self.state = self.S_CORNER_SETUP
            else:
                if not any(self.edge_all_outside):
                    n = self.tile_size // TILE_SIZE
                    self.tile_stack.extend((self.tile_x0 + dx * TILE_SIZE, self.tile_y0 + dy * TILE_SIZE, TILE_SIZE)
                                           for dy in range(n) for dx in range(n))
                self.state = self.S_TILE_POP

        elif self.state == self.S_PIX_SETUP:
            for lane in range(GROUP_WIDTH):
                dx, dy = lane_xy(lane)
                self.Px[lane].prepare((self.tile_x0 + dx) & WORD_MASK)
                self.Py[lane].prepare((self.tile_y0 + dy) & WORD_MASK)
            self.state = self.S_PIX_READ

        elif self.state == self.S_PIX_READ:
            coverage = 0
            for lane in range(GROUP_WIDTH):
                dx, dy = lane_xy(lane)
                x, y = self.tile_x0 + dx, self.tile_y0 + dy
                if (self.xmin <= x <= self.xmax and self.ymin <= y <= self.ymax and
                        self.Inside[lane].get() == 1 and self._signed(self.Area[lane]) != 0):
                    coverage |= 1 << lane

            if coverage == 0:
                self.state = self.S_TILE_POP
            else:
                f.tile_x.prepare(self.tile_x0 & WORD_MASK)
                f.tile_y.prepare(self.tile_y0 & WORD_MASK)
                f.coverage.prepare(coverage)
                f.area.prepare(self.Area[0].get())
                for i in range(GROUP_WIDTH):
                    f.z[i].prepare(self.Zinterp[i].get())
                    f.w0[i].prepare(self.W0[i].get())
                    f.w1[i].prepare(self.W1[i].get())
                    f.w2[i].prepare(self.W2[i].get())
                for wire, value in zip(f.tri_words, self.tri_words):
                    wire.prepare(value)
                f.valid.prepare(1)
                self.state = self.S_EMIT

        elif self.state == self.S_EMIT:
            if f.ready.get() == 1:
                # this edge is the transfer
                f.valid.prepare(0)
                self.groups_emitted += 1
                self.state = self.S_TILE_POP
            else:
                self.emit_stall_cycles += 1

        elif self.state == self.S_NEXT_TRIANGLE:
            self.triangle_index += 1
            self.state = self.S_DONE if self.triangle_index >= self.number_of_triangles.get() else self.S_FETCH_ISSUE

        self.done.prepare(1 if self.state == self.S_DONE else 0)


class Rasteriser(py4hw.Logic):
    '''Structural: the current-triangle registers, bbox calculator and
    GROUP_WIDTH pixel-test/depth lanes, wired to RasteriserController.
    Output: fragment groups on `frag`; `done` once every triangle has been
    walked and its last group transferred.'''

    def __init__(self, parent, name, triangle_mem: MemoryInterface, frag: FragmentGroupInterface,
                 done, first_triangle_address, number_of_triangles,
                 screen_width: int, screen_height: int,
                 partition_y_min: int = None, partition_y_max: int = None, start=None):
        super().__init__(parent, name)

        triangle_info = TrinagleInfo(self, 'current_triangle')

        Xmin, Xmax, Ymin, Ymax, Zmin, Zmax = (self.wire(n, 32) for n in
                                              ('Xmin', 'Xmax', 'Ymin', 'Ymax', 'Zmin', 'Zmax'))
        BoundingBoxCalculator(self, 'bbox', triangle_info, Xmin, Xmax, Ymin, Ymax, Zmin, Zmax)

        lanes = {k: [] for k in ('Px', 'Py', 'Inside', 'W0', 'W1', 'W2', 'Area', 'Zinterp')}
        for lane in range(GROUP_WIDTH):
            w = {k: self.wire(f'{k}{lane}', 1 if k == 'Inside' else 32) for k in lanes}
            PointInTriangle(self, f'point_test{lane}', w['Px'], w['Py'], triangle_info,
                            w['Inside'], w['W0'], w['W1'], w['W2'], w['Area'])
            BarycentricDepth(self, f'depth_interp{lane}', w['W0'], w['W1'], w['W2'], w['Area'],
                             triangle_info.V1Z, triangle_info.V2Z, triangle_info.V3Z, w['Zinterp'])
            for k in lanes:
                lanes[k].append(w[k])

        self.controller = RasteriserController(
            self, 'controller', triangle_mem, frag, done, first_triangle_address, number_of_triangles,
            triangle_info, lanes['Px'], lanes['Py'], Xmin, Xmax, Ymin, Ymax,
            lanes['Inside'], lanes['W0'], lanes['W1'], lanes['W2'], lanes['Area'], lanes['Zinterp'],
            screen_width, screen_height, partition_y_min, partition_y_max, start)

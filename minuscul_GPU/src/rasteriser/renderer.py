import py4hw
from ..memory.memory import MemoryInterface, Memory, TrinagleInfo, MatrixInfo4X4

'''
Rasterisation core.

+----------------+        +---------------+
| Triangle memory|------->|               |
+----------------+        |               |
                          |  RenderModule |
+----------------+        |               |
| Framebuffer mem|<-------|               |
+----------------+        +---------------+

RenderModule assumes triangles are already in screen space (X,Y in pixels,Z a depth value where "smaller is closer").
Triangle memory is a wide (512-bit) bus: each triangle is one line holding its
10 fields - V1X V1Y V1Z  V2X V2Y V2Z  V3X V3Y V3Z  COLOR - packed as 32-bit
two's-complement words at bit offsets 0,32,64,...,288 (320 of the 512 bits
used). This lets RenderController fetch an entire triangle in a single bus
transaction instead of 10 sequential word reads.
'''

from .triangle_line import (  # noqa: E402,F401
    TRIANGLE_LINE_WIDTH, TRIANGLE_FIELD_WIDTH, TRIANGLE_NUM_FIELDS,
    pack_triangle_line, unpack_triangle_line,
)

# Number of PointInTriangle/BarycentricDepth pairs run in parallel, one pixel
# each, when testing a row of pixels. Sized to be >= the largest coarse tile
# RenderController.COARSE_TILE_SIZE_LARGE ever picks (16 - see
# RASTERISER_ARCHITECTURE.md #10, where the coarse tile size became
# per-triangle adaptive rather than one fixed constant), so a fully-covered
# tile (the common "fill" path) always tests its widest row in one round
# instead of several, whatever tile size that particular triangle picked; a
# 4x4 fine-tile fallback row just leaves some lanes unused. Framebuffer
# writes go through a single pipelined port (one new write issued per
# cycle, back-to-back - see WRITE_ISSUE/WRITE_WAIT in RenderController), so
# this constant only speeds up the test/depth phase's
# own round count, not the write throughput itself.
PIXEL_LANE_COUNT = 16


# the banked-framebuffer helpers moved to the output stage (M2); re-exported
# here so existing callers keep working
from ..output_stage.output_stage import framebuffer_bank_size, resolve_banked_framebuffer  # noqa: E402,F401


class BoundingBoxCalculator(py4hw.Logic):
    def __init__(self, parent, name, triangle: TrinagleInfo, Xmin, Xmax, Ymin, Ymax, Zmin, Zmax):
        super().__init__(parent, name)

        self.Triangle = self.addInterfaceSink('triangle', triangle)
        self.Xmin = self.addOut('Xmin', Xmin)
        self.Xmax = self.addOut('Xmax', Xmax)
        self.Ymin = self.addOut('Ymin', Ymin)
        self.Ymax = self.addOut('Ymax', Ymax)
        self.Zmin = self.addOut('Zmin', Zmin)
        self.Zmax = self.addOut('Zmax', Zmax)

    def propagate(self):
        w = 32
        v1x = py4hw.IntegerHelper.c2_to_signed(self.Triangle.V1X.get(), w)
        v1y = py4hw.IntegerHelper.c2_to_signed(self.Triangle.V1Y.get(), w)
        v1z = py4hw.IntegerHelper.c2_to_signed(self.Triangle.V1Z.get(), w)
        v2x = py4hw.IntegerHelper.c2_to_signed(self.Triangle.V2X.get(), w)
        v2y = py4hw.IntegerHelper.c2_to_signed(self.Triangle.V2Y.get(), w)
        v2z = py4hw.IntegerHelper.c2_to_signed(self.Triangle.V2Z.get(), w)
        v3x = py4hw.IntegerHelper.c2_to_signed(self.Triangle.V3X.get(), w)
        v3y = py4hw.IntegerHelper.c2_to_signed(self.Triangle.V3Y.get(), w)
        v3z = py4hw.IntegerHelper.c2_to_signed(self.Triangle.V3Z.get(), w)

        self.Xmin.put(min(v1x, v2x, v3x))
        self.Xmax.put(max(v1x, v2x, v3x))
        self.Ymin.put(min(v1y, v2y, v3y))
        self.Ymax.put(max(v1y, v2y, v3y))
        self.Zmin.put(min(v1z, v2z, v3z))
        self.Zmax.put(max(v1z, v2z, v3z))


class EdgeFunction(py4hw.Logic):
    '''
    2D edge function: (X1-X3)*(Y2-Y3) - (X2-X3)*(Y1-Y3).
    '''
    def __init__(self, parent, name, X1, Y1, X2, Y2, X3, Y3, out):
        super().__init__(parent, name)

        self.X1 = self.addIn('X1', X1)
        self.Y1 = self.addIn('Y1', Y1)
        self.X2 = self.addIn('X2', X2)
        self.Y2 = self.addIn('Y2', Y2)
        self.X3 = self.addIn('X3', X3)
        self.Y3 = self.addIn('Y3', Y3)
        self.out = self.addOut('out', out)

    def propagate(self):
        w = 32
        x1 = py4hw.IntegerHelper.c2_to_signed(self.X1.get(), w)
        y1 = py4hw.IntegerHelper.c2_to_signed(self.Y1.get(), w)
        x2 = py4hw.IntegerHelper.c2_to_signed(self.X2.get(), w)
        y2 = py4hw.IntegerHelper.c2_to_signed(self.Y2.get(), w)
        x3 = py4hw.IntegerHelper.c2_to_signed(self.X3.get(), w)
        y3 = py4hw.IntegerHelper.c2_to_signed(self.Y3.get(), w)

        value = (x1 - x3) * (y2 - y3) - (x2 - x3) * (y1 - y3)
        self.out.put(value)


class InsideAndArea(py4hw.Logic):
    '''
    Serches if a point is outside of inside a tirangle 
    Combines the three edge-function weights of a point against a triangle's
    edges into an inside/outside flag and the (doubled, signed) triangle
    area. w0+w1+w2 == area for any point, inside or not, since w_i/area are
    exactly the point's barycentric coordinates.
    '''
    def __init__(self, parent, name, W0, W1, W2, Inside, Area):
        super().__init__(parent, name)

        self.W0 = self.addIn('W0', W0)
        self.W1 = self.addIn('W1', W1)
        self.W2 = self.addIn('W2', W2)
        self.Inside = self.addOut('Inside', Inside)
        self.Area = self.addOut('Area', Area)

    def propagate(self):
        w = 32
        w0 = py4hw.IntegerHelper.c2_to_signed(self.W0.get(), w)
        w1 = py4hw.IntegerHelper.c2_to_signed(self.W1.get(), w)
        w2 = py4hw.IntegerHelper.c2_to_signed(self.W2.get(), w)

        has_neg = (w0 < 0) or (w1 < 0) or (w2 < 0)
        has_pos = (w0 > 0) or (w1 > 0) or (w2 > 0)

        self.Inside.put(0 if (has_neg and has_pos) else 1)
        self.Area.put(w0 + w1 + w2)


class PointInTriangle(py4hw.Logic):
    '''Structural: wires a pixel (Px,Py) against a TrinagleInfo bundle.'''
    def __init__(self, parent, name, Px, Py, triangle: TrinagleInfo, Inside, W0, W1, W2, Area):
        super().__init__(parent, name)

        self.Triangle = self.addInterfaceSink('triangle', triangle)
        self.Px = self.addIn('Px', Px)
        self.Py = self.addIn('Py', Py)

        EdgeFunction(self, 'e0', Px, Py, self.Triangle.V2X, self.Triangle.V2Y,
                     self.Triangle.V3X, self.Triangle.V3Y, W0)
        EdgeFunction(self, 'e1', Px, Py, self.Triangle.V3X, self.Triangle.V3Y,
                     self.Triangle.V1X, self.Triangle.V1Y, W1)
        EdgeFunction(self, 'e2', Px, Py, self.Triangle.V1X, self.Triangle.V1Y,
                     self.Triangle.V2X, self.Triangle.V2Y, W2)

        InsideAndArea(self, 'combine', W0, W1, W2, Inside, Area)


class BarycentricDepth(py4hw.Logic):
    '''Zinterp = (W0*Z1 + W1*Z2 + W2*Z3) / Area, truncated toward zero.'''
    def __init__(self, parent, name, W0, W1, W2, Area, Z1, Z2, Z3, Zinterp):
        super().__init__(parent, name)

        self.W0 = self.addIn('W0', W0)
        self.W1 = self.addIn('W1', W1)
        self.W2 = self.addIn('W2', W2)
        self.Area = self.addIn('Area', Area)
        self.Z1 = self.addIn('Z1', Z1)
        self.Z2 = self.addIn('Z2', Z2)
        self.Z3 = self.addIn('Z3', Z3)
        self.Zinterp = self.addOut('Zinterp', Zinterp)

    def propagate(self):
        w = 32
        w0 = py4hw.IntegerHelper.c2_to_signed(self.W0.get(), w)
        w1 = py4hw.IntegerHelper.c2_to_signed(self.W1.get(), w)
        w2 = py4hw.IntegerHelper.c2_to_signed(self.W2.get(), w)
        area = py4hw.IntegerHelper.c2_to_signed(self.Area.get(), w)
        z1 = py4hw.IntegerHelper.c2_to_signed(self.Z1.get(), w)
        z2 = py4hw.IntegerHelper.c2_to_signed(self.Z2.get(), w)
        z3 = py4hw.IntegerHelper.c2_to_signed(self.Z3.get(), w)

        if area == 0:
            self.Zinterp.put(0)
        else:
            numerator = w0 * z1 + w1 * z2 + w2 * z3
            self.Zinterp.put(int(numerator / area))


class RenderController(py4hw.Logic):
    '''
    Leaf FSM: fetches triangles from Triangle memory and rasterises each one
    with a two-level hierarchical tile test (matching a real coarse-rasteriser
    design) instead of a flat per-pixel scan of its bounding box:

      - the bounding box is covered with tiles aligned to a coarse tile size
        chosen per-triangle from its own bbox extent (see COARSE_TILE_BANDS -
        RASTERISER_ARCHITECTURE.md #10 found the fastest coarse tile size
        isn't one global constant, it's a three-way crossover keyed on the
        triangle's own size)
      - each tile's 4 corners are tested against the triangle (reusing the
        same PointInTriangle/BarycentricDepth pipeline used for individual
        pixels, just run 4 times first)
          - all 4 corners outside the *same* edge -> the whole tile is
            rejected outright (corners outside *different* edges prove
            nothing - see S_CORNER_READ), no further work at all - this is
            where the scheme actually saves
            cycles versus a flat scan, since most of a triangle's bounding
            box is typically NOT covered by the triangle itself
          - all 4 corners inside   -> every pixel in the tile is filled
            (each still gets its own depth test/write - depth genuinely
            varies per pixel, and computing it needs the same edge-function
            evaluation as the inside test, so this path doesn't skip work,
            it just skips re-deciding "inside" for pixels already proven so)
          - a coarse tile with a mixed result subdivides directly into 4x4
            sub-tiles, each independently corner-tested the same way
          - a 4x4 tile with a mixed result falls back to testing its 16
            pixels individually (no further subdivision)

    States:
      FETCH_ISSUE/FETCH_WAIT     - read the current triangle's entire 512-bit
                                    line in one bus transaction and unpack it
      BBOX                       - clamp the bounding box to the screen,
                                    pick this triangle's own coarse tile size
                                    from its bbox extent, and seed the tile
                                    worklist at that size
      TILE_POP                   - pop the next tile; skip it immediately if
                                    it doesn't intersect the bounding box
      CORNER_SETUP/CORNER_READ   - test the tile's 4 corners one at a time,
                                    using lane 0 of the pixel-test hardware
      ROW_SETUP/ROW_READ         - drive Px,Py for up to PIXEL_LANE_COUNT
                                    pixels of one row at once (one per lane),
                                    then read all their Inside/Area/Zinterp
                                    results in the same round
      WRITE_ISSUE/WRITE_WAIT     - write one row's surviving pixels to the
                                    framebuffer. With write_bank_count == 1
                                    (default), WRITE_ISSUE fires every cycle
                                    back-to-back (pipelined - it never waits
                                    for the previous pixel's done before
                                    issuing the next address). With
                                    write_bank_count > 1, the framebuffer is
                                    split into that many independently
                                    addressed banks (interleaved by pixel
                                    address, so any run of write_bank_count
                                    consecutive pixels always lands in
                                    write_bank_count distinct banks) and
                                    WRITE_ISSUE fires every surviving pixel
                                    of a row into its own bank in the SAME
                                    cycle - a real parallel write port, not
                                    just a pipelined serial one. Either way,
                                    WRITE_WAIT only drains the last-issued
                                    batch's done signal(s) before moving on
      NEXT_TILE_ROW               - advance to the tile's next row of pixels,
                                    or return to TILE_POP once it's done
      NEXT_TRIANGLE              - advance the triangle index, or finish (DONE)

    The depth buffer is kept as private Python state rather than a bus-attached
    Memory: nothing outside this rasteriser core ever needs to see it.
    '''

    (S_WAIT_START, S_FETCH_ISSUE, S_FETCH_WAIT, S_BBOX, S_TILE_POP, S_CORNER_SETUP, S_CORNER_READ,
     S_ROW_SETUP, S_ROW_READ, S_WRITE_ISSUE, S_WRITE_WAIT, S_NEXT_TILE_ROW,
     S_NEXT_TRIANGLE, S_DONE) = range(14)

    # Per-triangle adaptive coarse tile size (RASTERISER_ARCHITECTURE.md #10):
    # a 1920x1080 sweep across triangle sizes found the fastest coarse tile
    # isn't one global constant - it's a three-way crossover keyed on the
    # triangle's own bbox extent (max of its clamped width/height): small
    # triangles do best with 4x4 tiles, medium with 8x8, large with 16x16.
    # Each (threshold, size) entry covers "bbox extent < threshold"; anything
    # at or past the last threshold falls through to COARSE_TILE_SIZE_LARGE.
    COARSE_TILE_BANDS = (
        (20, 4),
        (90, 8),
    )
    COARSE_TILE_SIZE_LARGE = 16
    FINE_TILE_SIZE = 4

    @classmethod
    def _coarse_tile_size_for_extent(cls, extent):
        for threshold, size in cls.COARSE_TILE_BANDS:
            if extent < threshold:
                return size
        return cls.COARSE_TILE_SIZE_LARGE

    def __init__(self, parent, name, triangle_mem: MemoryInterface, framebuffer_mem,
                 done, first_triangle_address, number_of_triangles,
                 triangle_info: TrinagleInfo, Pxs, Pys,
                 Xmin, Xmax, Ymin, Ymax,
                 Insides, W0s, W1s, W2s, Areas, Zinterps,
                 screen_width: int, screen_height: int,
                 partition_y_min: int = None, partition_y_max: int = None,
                 framebuffer_y_offset: int = 0, start=None,
                 write_bank_count: int = 1):
        super().__init__(parent, name)

        self.triangle_mem = self.addInterfaceSource('triangle_mem', triangle_mem)

        # write_bank_count == 1 (default): framebuffer_mem is a single
        # MemoryInterface, unchanged from before - one write port, pipelined
        # issue (see S_WRITE_ISSUE/S_WRITE_WAIT below).
        # write_bank_count > 1: framebuffer_mem must be a list of that many
        # MemoryInterfaces (independent Memory instances - py4hw forbids two
        # drivers on one wire, so real bank parallelism needs one dedicated
        # port per bank, same reasoning as MultiCoreRenderModule's per-core
        # framebuffers). A pixel's local (partition-relative) address maps to
        # bank = local_addr % write_bank_count, bank_addr = local_addr //
        # write_bank_count - so any run of write_bank_count consecutive
        # pixels always spreads across write_bank_count distinct banks,
        # guaranteeing a same-cycle batch never collides on one bank.
        self.write_bank_count = write_bank_count
        fb_ifs = [framebuffer_mem] if write_bank_count == 1 else list(framebuffer_mem)
        assert len(fb_ifs) == write_bank_count, \
            'framebuffer_mem must supply exactly write_bank_count interfaces'
        self.framebuffer_mem = [
            self.addInterfaceSource(f'framebuffer_mem{i}', fb_ifs[i])
            for i in range(write_bank_count)
        ]

        self.done = self.addOut('done', done)
        self.first_triangle_address = self.addIn('first_triangle_address', first_triangle_address)
        self.number_of_triangles = self.addIn('number_of_triangles', number_of_triangles)

        # a binner (see Binner/MultiCoreRenderModule) needs every core to
        # hold off touching its own triangle memory until binning has
        # finished writing into it; a plain RenderModule has no binner, so
        # it gets an always-ready wire it drives itself
        if start is None:
            start = self.wire('always_start', 1)
            start.put(1)
        self.start = self.addIn('start', start)

        self.V1X = self.addOut('V1X', triangle_info.V1X)
        self.V1Y = self.addOut('V1Y', triangle_info.V1Y)
        self.V1Z = self.addOut('V1Z', triangle_info.V1Z)
        self.V2X = self.addOut('V2X', triangle_info.V2X)
        self.V2Y = self.addOut('V2Y', triangle_info.V2Y)
        self.V2Z = self.addOut('V2Z', triangle_info.V2Z)
        self.V3X = self.addOut('V3X', triangle_info.V3X)
        self.V3Y = self.addOut('V3Y', triangle_info.V3Y)
        self.V3Z = self.addOut('V3Z', triangle_info.V3Z)

        self.lane_count = len(Pxs)
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
        self._zbuffer = [None] * (screen_width * screen_height)

        # a core with no partition given owns the whole screen (single-core
        # behaviour, unchanged); a core given a partition only ever rasterises
        # and writes within its own disjoint strip of rows, into its own
        # private framebuffer_mem addressed relative to that strip's start
        self.partition_y_min = 0 if partition_y_min is None else partition_y_min
        self.partition_y_max = screen_height - 1 if partition_y_max is None else partition_y_max
        self.framebuffer_y_offset = framebuffer_y_offset

        self.state = self.S_WAIT_START
        self.triangle_index = 0
        self.color = 0

        self.xmin = self.xmax = self.ymin = self.ymax = 0
        # this triangle's own coarse tile size, chosen in S_BBOX from its
        # bbox extent (see COARSE_TILE_BANDS) - subdivision in S_CORNER_READ
        # needs this rather than a fixed constant, since it now varies
        # per triangle
        self.triangle_coarse_tile_size = self.COARSE_TILE_SIZE_LARGE

        # tile worklist: (x0, y0, size) tuples still to be corner-tested
        self.tile_stack = []
        # the tile currently being processed, and its scan range clipped to
        # the triangle's own bounding box
        self.tile_x0 = self.tile_y0 = self.tile_size = 0
        self.tile_cx0 = self.tile_cx1 = self.tile_cy0 = self.tile_cy1 = 0
        self.corner_list = []
        self.corner_idx = 0
        self.corner_inside_count = 0
        # row cursor within the current tile's clipped scan range: (px, py)
        # is the row's leftmost pixel, row_xs the actual x's tested this round
        # (fewer than lane_count only at the tile's right edge)
        self.px = self.py = 0
        self.row_xs = []
        self.pending_writes = []
        # bank indices written by the most recently issued batch, so
        # S_WRITE_WAIT knows exactly which banks' done signals to check
        self._writes_in_flight = []

    def clock(self):
        m = self.triangle_mem
        w = 32

        if self.state == self.S_WAIT_START:
            if self.start.get() == 1:
                # a binner may have assigned this core zero triangles - go
                # straight to done rather than fetching garbage at index 0
                self.state = self.S_DONE if self.number_of_triangles.get() == 0 else self.S_FETCH_ISSUE
            # else: stay in S_WAIT_START

        elif self.state == self.S_FETCH_ISSUE:
            addr = self.first_triangle_address.get() + self.triangle_index
            m.address.prepare(addr)
            m.read.prepare(1)
            m.write.prepare(0)
            self.state = self.S_FETCH_WAIT

        elif self.state == self.S_FETCH_WAIT:
            if m.done.get() == 1:
                m.read.prepare(0)
                (v1x, v1y, v1z, v2x, v2y, v2z, v3x, v3y, v3z, color) = \
                    unpack_triangle_line(m.read_data.get())
                self.V1X.prepare(v1x)
                self.V1Y.prepare(v1y)
                self.V1Z.prepare(v1z)
                self.V2X.prepare(v2x)
                self.V2Y.prepare(v2y)
                self.V2Z.prepare(v2z)
                self.V3X.prepare(v3x)
                self.V3Y.prepare(v3y)
                self.V3Z.prepare(v3z)
                self.color = color
                self.state = self.S_BBOX
            # else: memory not ready yet, stay in S_FETCH_WAIT

        elif self.state == self.S_BBOX:
            xmin = py4hw.IntegerHelper.c2_to_signed(self.Xmin.get(), w)
            xmax = py4hw.IntegerHelper.c2_to_signed(self.Xmax.get(), w)
            ymin = py4hw.IntegerHelper.c2_to_signed(self.Ymin.get(), w)
            ymax = py4hw.IntegerHelper.c2_to_signed(self.Ymax.get(), w)

            self.xmin = max(0, xmin)
            self.xmax = min(self.screen_width - 1, xmax)
            self.ymin = max(0, self.partition_y_min, ymin)
            self.ymax = min(self.screen_height - 1, self.partition_y_max, ymax)

            if self.xmin > self.xmax or self.ymin > self.ymax:
                self.state = self.S_NEXT_TRIANGLE  # degenerate or fully off-screen
            else:
                extent = max(self.xmax - self.xmin + 1, self.ymax - self.ymin + 1)
                size = self._coarse_tile_size_for_extent(extent)
                self.triangle_coarse_tile_size = size
                tile_x_start = (self.xmin // size) * size
                tile_y_start = (self.ymin // size) * size
                self.tile_stack = [
                    (tx, ty, size)
                    for ty in range(tile_y_start, self.ymax + 1, size)
                    for tx in range(tile_x_start, self.xmax + 1, size)
                ]
                self.state = self.S_TILE_POP

        elif self.state == self.S_TILE_POP:
            if not self.tile_stack:
                self.state = self.S_NEXT_TRIANGLE
            else:
                tx, ty, size = self.tile_stack.pop()
                cx0 = max(tx, self.xmin)
                cx1 = min(tx + size - 1, self.xmax)
                cy0 = max(ty, self.ymin)
                cy1 = min(ty + size - 1, self.ymax)

                if cx0 > cx1 or cy0 > cy1:
                    pass  # tile doesn't overlap the bbox at all: skip it, stay in S_TILE_POP
                else:
                    self.tile_x0, self.tile_y0, self.tile_size = tx, ty, size
                    self.tile_cx0, self.tile_cx1 = cx0, cx1
                    self.tile_cy0, self.tile_cy1 = cy0, cy1
                    self.corner_list = [
                        (tx, ty), (tx + size - 1, ty),
                        (tx, ty + size - 1), (tx + size - 1, ty + size - 1),
                    ]
                    self.corner_idx = 0
                    self.corner_inside_count = 0
                    # tracked per triangle edge: true only if every corner
                    # tested so far is strictly outside *that* edge - if any
                    # one of the three ends up true after all 4 corners, the
                    # whole tile is provably outside the triangle, since it
                    # then lies entirely in that edge's excluded half-plane
                    self.edge_all_outside = [True, True, True]
                    self.state = self.S_CORNER_SETUP

        elif self.state == self.S_CORNER_SETUP:
            cx, cy = self.corner_list[self.corner_idx]
            self.Px[0].prepare(cx)
            self.Py[0].prepare(cy)
            self.state = self.S_CORNER_READ

        elif self.state == self.S_CORNER_READ:
            if self.Inside[0].get() == 1:
                self.corner_inside_count += 1

            # "all 4 corners fail the inside test" does NOT mean the tile is
            # outside the triangle - a small triangle sitting well inside a
            # big empty tile has every corner outside it too, each possibly
            # failing a *different* edge. The only sound trivial-reject test
            # is per edge: if every corner is strictly on the excluded side
            # of the *same* edge, the whole tile lies in that edge's
            # excluded half-plane and is therefore guaranteed outside.
            area_signed = py4hw.IntegerHelper.c2_to_signed(self.Area[0].get(), w)
            for i, wire in enumerate((self.W0[0], self.W1[0], self.W2[0])):
                value = py4hw.IntegerHelper.c2_to_signed(wire.get(), w)
                on_excluded_side = (value > 0 and area_signed < 0) or (value < 0 and area_signed > 0)
                if not on_excluded_side:
                    self.edge_all_outside[i] = False

            self.corner_idx += 1

            if self.corner_idx < 4:
                self.state = self.S_CORNER_SETUP
            elif any(self.edge_all_outside):
                self.state = self.S_TILE_POP  # provably outside: reject the whole tile
            elif self.corner_inside_count == 4 or self.tile_size == self.FINE_TILE_SIZE:
                # all 4 corners inside (fill), or a 4x4 tile with a mixed
                # result (fall back to per-pixel testing) - either way, scan
                # this tile's own pixels
                self.px, self.py = self.tile_cx0, self.tile_cy0
                self.state = self.S_ROW_SETUP
            else:
                # coarse tile (this triangle's own size, from S_BBOX), mixed
                # result: subdivide into FINE_TILE_SIZE sub-tiles
                tx, ty = self.tile_x0, self.tile_y0
                fine = self.FINE_TILE_SIZE
                self.tile_stack.extend(
                    (tx + dx * fine, ty + dy * fine, fine)
                    for dy in range(self.triangle_coarse_tile_size // fine)
                    for dx in range(self.triangle_coarse_tile_size // fine)
                )
                self.state = self.S_TILE_POP

        elif self.state == self.S_ROW_SETUP:
            self.row_xs = list(range(self.px, min(self.px + self.lane_count, self.tile_cx1 + 1)))
            for lane, x in enumerate(self.row_xs):
                self.Px[lane].prepare(x)
                self.Py[lane].prepare(self.py)
            self.state = self.S_ROW_READ

        elif self.state == self.S_ROW_READ:
            self.pending_writes = []
            for lane, x in enumerate(self.row_xs):
                if self.Inside[lane].get() == 1 and \
                        py4hw.IntegerHelper.c2_to_signed(self.Area[lane].get(), w) != 0:
                    z = py4hw.IntegerHelper.c2_to_signed(self.Zinterp[lane].get(), w)
                    addr = self.py * self.screen_width + x
                    current_z = self._zbuffer[addr]

                    if current_z is None or z < current_z:
                        self._zbuffer[addr] = z
                        self.pending_writes.append(addr)

            self.state = self.S_WRITE_ISSUE if self.pending_writes else self.S_NEXT_TILE_ROW

        elif self.state == self.S_WRITE_ISSUE:
            # pending_writes holds global (whole-screen) addresses, shared
            # with the z-buffer's own indexing; framebuffer_mem is only ever
            # sized for this core's own partition, so the actual bus address
            # has to be relative to where that partition starts before it's
            # split into a (bank, bank_addr) pair.
            #
            # Group as many pending writes as possible into this cycle's
            # batch, at most one pixel per bank (skip - defer to a later
            # cycle - any pixel whose bank is already claimed this cycle).
            # With write_bank_count == 1 every pixel maps to bank 0, so the
            # batch is always a single pixel: identical to the old
            # one-write-per-cycle pipelined behaviour. With write_bank_count
            # >= the number of surviving pixels in a row, the whole row
            # becomes one single-cycle batch - true parallel writes.
            batch = []
            claimed_banks = set()
            remaining = []
            for addr in self.pending_writes:
                local_addr = addr - self.framebuffer_y_offset * self.screen_width
                bank = local_addr % self.write_bank_count
                if bank in claimed_banks:
                    remaining.append(addr)
                else:
                    claimed_banks.add(bank)
                    batch.append((bank, local_addr // self.write_bank_count))
            self.pending_writes = remaining

            for bank, bank_addr in batch:
                fb_bank = self.framebuffer_mem[bank]
                fb_bank.address.prepare(bank_addr)
                fb_bank.read.prepare(0)
                fb_bank.write.prepare(1)
                fb_bank.write_data.prepare(self.color)
            self._writes_in_flight = [bank for bank, _ in batch]

            # pipelined: the next batch's addresses go out next cycle
            # regardless of whether this cycle's writes have been acked yet
            # - nothing downstream needs a bank's done before the next batch
            # can reuse it, so there's no reason to stall waiting for it.
            # Only once the queue itself is empty do we need to actually
            # drain (S_WRITE_WAIT) and confirm the last batch landed.
            self.state = self.S_WRITE_ISSUE if self.pending_writes else self.S_WRITE_WAIT

        elif self.state == self.S_WRITE_WAIT:
            if all(self.framebuffer_mem[bank].done.get() == 1 for bank in self._writes_in_flight):
                for bank in self._writes_in_flight:
                    self.framebuffer_mem[bank].write.prepare(0)
                self.state = self.S_NEXT_TILE_ROW
            # else: stay in S_WRITE_WAIT

        elif self.state == self.S_NEXT_TILE_ROW:
            self.px += self.lane_count
            if self.px > self.tile_cx1:
                self.px = self.tile_cx0
                self.py += 1

            self.state = self.S_TILE_POP if self.py > self.tile_cy1 else self.S_ROW_SETUP

        elif self.state == self.S_NEXT_TRIANGLE:
            self.triangle_index += 1
            if self.triangle_index >= self.number_of_triangles.get():
                self.state = self.S_DONE
            else:
                self.state = self.S_FETCH_ISSUE

        elif self.state == self.S_DONE:
            pass

        self.done.prepare(1 if self.state == self.S_DONE else 0)


class RenderModule(py4hw.Logic):
    '''Structural top level: owns the "current triangle" registers and wires
    the bounding-box/point-test/depth-interpolation leaves to RenderController.'''

    def __init__(self, parent, name, triangle_mem: MemoryInterface, framebuffer_mem,
                 done, first_triangle_address, number_of_triangles,
                 screen_width: int, screen_height: int,
                 partition_y_min: int = None, partition_y_max: int = None,
                 framebuffer_y_offset: int = 0, start=None,
                 write_bank_count: int = 1):
        super().__init__(parent, name)

        triangle_info = TrinagleInfo(self, 'current_triangle')

        Xmin = self.wire('Xmin', 32)
        Xmax = self.wire('Xmax', 32)
        Ymin = self.wire('Ymin', 32)
        Ymax = self.wire('Ymax', 32)
        Zmin = self.wire('Zmin', 32)
        Zmax = self.wire('Zmax', 32)
        BoundingBoxCalculator(self, 'bbox', triangle_info, Xmin, Xmax, Ymin, Ymax, Zmin, Zmax)

        # PIXEL_LANE_COUNT independent PointInTriangle/BarycentricDepth pairs,
        # all wired to the same triangle_info, so RenderController can test a
        # whole row of pixels in one setup+read round. Lane 0 also serves the
        # tile-corner test, which only ever needs one point at a time.
        lane_Px, lane_Py = [], []
        lane_Inside, lane_W0, lane_W1, lane_W2, lane_Area, lane_Zinterp = [], [], [], [], [], []
        for lane in range(PIXEL_LANE_COUNT):
            Px = self.wire(f'Px{lane}', 32)
            Py = self.wire(f'Py{lane}', 32)
            Inside = self.wire(f'Inside{lane}', 1)
            W0 = self.wire(f'W0_{lane}', 32)
            W1 = self.wire(f'W1_{lane}', 32)
            W2 = self.wire(f'W2_{lane}', 32)
            Area = self.wire(f'Area{lane}', 32)
            Zinterp = self.wire(f'Zinterp{lane}', 32)

            PointInTriangle(self, f'point_test{lane}', Px, Py, triangle_info, Inside, W0, W1, W2, Area)
            BarycentricDepth(self, f'depth_interp{lane}', W0, W1, W2, Area,
                              triangle_info.V1Z, triangle_info.V2Z, triangle_info.V3Z, Zinterp)

            lane_Px.append(Px)
            lane_Py.append(Py)
            lane_Inside.append(Inside)
            lane_W0.append(W0)
            lane_W1.append(W1)
            lane_W2.append(W2)
            lane_Area.append(Area)
            lane_Zinterp.append(Zinterp)

        RenderController(self, 'controller', triangle_mem, framebuffer_mem,
                          done, first_triangle_address, number_of_triangles,
                          triangle_info, lane_Px, lane_Py, Xmin, Xmax, Ymin, Ymax,
                          lane_Inside, lane_W0, lane_W1, lane_W2, lane_Area, lane_Zinterp,
                          screen_width, screen_height,
                          partition_y_min, partition_y_max, framebuffer_y_offset, start,
                          write_bank_count)


# AllDone and Binner moved to binner/ (M3); re-exported for existing callers
from ..binner.binner import AllDone, Binner  # noqa: E402,F401


class MultiCoreRenderModule(py4hw.Logic):
    '''
    Structural: NUM_CORES independent RenderModule cores rasterising the same
    triangle list in parallel, each owning a disjoint horizontal strip of
    the screen's rows, fed by a real Binner pre-pass so each core only ever
    sees the triangles that can actually land in its own strip.

    py4hw doesn't allow two components to drive the same wire (a second
    addOut/addInterfaceSource on an already-sourced wire raises an exception),
    so cores genuinely can't share one memory bus without a real bus
    arbiter - that's the piece a shared-framebuffer design would need and
    this one doesn't. Instead each core gets:
      - its own private triangle memory, holding only the Binner's subset
        for its strip (sized for the worst case - every triangle landing in
        every core - since the actual count isn't known until binning runs)
      - its own private framebuffer memory sized only for its own strip of
        rows, addressed relative to that strip's first row

    Since no two cores ever touch the same memory, there is nothing to
    arbitrate. Cores hold in S_WAIT_START until the Binner's binning_done
    fires, since starting earlier would mean fetching whatever garbage
    happens to be sitting in a not-yet-written triangle memory. What's
    genuinely parallel is everything after that: fetch, tile test, and
    per-pixel work all happen on every core's own clock() in the same
    simulated cycle, so the whole system's completion latency is the
    binning pass plus the slowest core's own cycle count, not the sum of
    all cores' work - call resolve_framebuffer() after `done` to stitch the
    per-core strips back into one image, the same "resolve" step a real
    tiled GPU performs outside its rasteriser cores.
    '''

    def __init__(self, parent, name, num_cores: int, triangle_lines,
                 done, first_triangle_address, number_of_triangles,
                 screen_width: int, screen_height: int):
        super().__init__(parent, name)

        self.screen_width = screen_width
        self.screen_height = screen_height
        self.core_framebuffers = []
        self.core_partitions = []

        rows_per_core = -(-screen_height // num_cores)  # ceil division
        num_lines = max(len(triangle_lines), 1)

        source_mem_if = MemoryInterface(self, 'SourceTriMem', memory_size=num_lines,
                                         data_width=TRIANGLE_LINE_WIDTH)
        source_mem = Memory(self, 'SourceTriMemImpl', source_mem_if, memory_size=num_lines)
        for line_idx, line in enumerate(triangle_lines):
            source_mem.memory[line_idx] = line

        zero_addr = self.wire('zero_addr', 32)
        zero_addr.put(0)
        binning_done = self.wire('binning_done', 1)

        binner_core_partitions = []
        binner_core_memories = []
        binner_core_counts = []
        core_done_wires = []

        for i in range(num_cores):
            y_start = i * rows_per_core
            y_end = min(screen_height, y_start + rows_per_core) - 1
            if y_end < y_start:
                continue  # more cores than rows of screen: nothing left for this one

            partition_rows = y_end - y_start + 1

            # sized for the worst case - the Binner might put every triangle
            # in this one core - since the real per-core count isn't known
            # until the Binner has actually run
            triangle_mem_if = MemoryInterface(self, f'TriMem{i}', memory_size=num_lines,
                                               data_width=TRIANGLE_LINE_WIDTH)
            triangle_mem = Memory(self, f'TriMemImpl{i}', triangle_mem_if, memory_size=num_lines)

            fb_size = screen_width * partition_rows
            fb_mem_if = MemoryInterface(self, f'FbMem{i}', memory_size=fb_size)
            fb_mem = Memory(self, f'FbMemImpl{i}', fb_mem_if, memory_size=fb_size)
            self.core_framebuffers.append(fb_mem)
            self.core_partitions.append((y_start, y_end))

            core_count = self.wire(f'core_triangle_count{i}', 32)
            core_done = self.wire(f'core_done{i}', 1)
            core_done_wires.append(core_done)

            RenderModule(self, f'core{i}', triangle_mem_if, fb_mem_if,
                         core_done, zero_addr, core_count,
                         screen_width, screen_height,
                         partition_y_min=y_start, partition_y_max=y_end,
                         framebuffer_y_offset=y_start, start=binning_done)

            binner_core_partitions.append((y_start, y_end))
            binner_core_memories.append(triangle_mem)
            binner_core_counts.append(core_count)

        Binner(self, 'binner', source_mem_if, first_triangle_address, number_of_triangles,
               binner_core_memories, binner_core_partitions, binner_core_counts, binning_done)

        AllDone(self, 'all_done', core_done_wires, done)

    def resolve_framebuffer(self):
        '''Stitches every core's private row-strip memory into one flat list
        of screen_width*screen_height pixel values - the same "resolve" a
        real tiled GPU performs outside its rasteriser cores, done here in
        Python since nothing inside the simulated hardware needs the merged
        image.'''
        combined = [0] * (self.screen_width * self.screen_height)
        for fb_mem, (y_start, y_end) in zip(self.core_framebuffers, self.core_partitions):
            rows = y_end - y_start + 1
            for local_addr in range(self.screen_width * rows):
                y_local, x = divmod(local_addr, self.screen_width)
                combined[(y_start + y_local) * self.screen_width + x] = fb_mem.memory[local_addr]
        return combined


###############################################################################
# Vertex-transform building blocks.
#
# These are fixed and independently usable, but RenderController above does
# not yet sequence them: it consumes triangles that are already in screen
# space. Wiring a full per-vertex MVP-transform stage (matrix-vector multiply,
# clip-space -> screen-space conversion, flat-shading dot product) into the
# controller is follow-up work, not done in this pass.
###############################################################################

class MatrixMultiplier4x4(py4hw.Logic):
    def __init__(self, parent, name, matrixA: MatrixInfo4X4, matrixB: MatrixInfo4X4, matrixR: MatrixInfo4X4):
        super().__init__(parent, name)
        self.A = self.addInterfaceSink('A', matrixA)
        self.B = self.addInterfaceSink('B', matrixB)
        self.R = self.addInterfaceSource('R', matrixR)

    def propagate(self):
        w = 32
        A = self.A
        B = self.B

        def g(port):
            return py4hw.IntegerHelper.c2_to_signed(port.get(), w)

        a = [[g(getattr(A, f'V{i}{j}')) for j in range(4)] for i in range(4)]
        b = [[g(getattr(B, f'V{i}{j}')) for j in range(4)] for i in range(4)]

        for i in range(4):
            for j in range(4):
                value = sum(a[i][k] * b[k][j] for k in range(4))
                getattr(self.R, f'V{i}{j}').put(value)


class CrossProduct(py4hw.Logic):
    def __init__(self, parent, name, x1, y1, z1, x2, y2, z2, rx, ry, rz):
        super().__init__(parent, name)
        self.x1 = self.addIn('X1', x1)
        self.y1 = self.addIn('Y1', y1)
        self.z1 = self.addIn('Z1', z1)
        self.x2 = self.addIn('X2', x2)
        self.y2 = self.addIn('Y2', y2)
        self.z2 = self.addIn('Z2', z2)

        self.rx = self.addOut('RX', rx)
        self.ry = self.addOut('RY', ry)
        self.rz = self.addOut('RZ', rz)

    def propagate(self):
        w = 32

        def g(port):
            return py4hw.IntegerHelper.c2_to_signed(port.get(), w)

        x1, y1, z1 = g(self.x1), g(self.y1), g(self.z1)
        x2, y2, z2 = g(self.x2), g(self.y2), g(self.z2)

        self.rx.put(y1 * z2 - z1 * y2)
        self.ry.put(z1 * x2 - x1 * z2)
        self.rz.put(x1 * y2 - y1 * x2)


class Vector3Norm(py4hw.Logic):
    '''norm = sqrt(x^2+y^2+z^2), scaled by `scale` to keep the result an
    integer (e.g. scale=1000 gives 3 decimal digits of fixed-point precision).'''
    def __init__(self, parent, name, x, y, z, norm, scale=1000):
        super().__init__(parent, name)

        self.x = self.addIn('X', x)
        self.y = self.addIn('Y', y)
        self.z = self.addIn('Z', z)
        self.norm = self.addOut('norm', norm)
        self.scale = scale

    def propagate(self):
        import math
        w = 32

        def g(port):
            return py4hw.IntegerHelper.c2_to_signed(port.get(), w)

        x, y, z = g(self.x), g(self.y), g(self.z)
        self.norm.put(int(math.sqrt(x * x + y * y + z * z) * self.scale))

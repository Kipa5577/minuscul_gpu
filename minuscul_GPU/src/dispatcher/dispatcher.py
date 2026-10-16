from dataclasses import dataclass, field

import numpy as np
import py4hw

from ..params import GROUP_WIDTH, lane_xy
from ..rasteriser.fragment import FragmentGroupInterface, read_fragment_group
from ..shader_core.alu import f32_bits, to_f32
from ..shader_core.core import LaunchInterface, drive_launch

'''
Pixel dispatcher (work module M6): fragment group -> pixel thread group,
with the launch register convention of README_ISA.md §6.3.

  exec    = coverage mask
  v0, v1  = pixel x, y (integers)
  v2-v4   = barycentrics b0, b1, b2 (FP32), perspective-corrected with
            the triangle line's 1/w words (all-zero bits = 1.0)
  s0-s2   = word addresses of the triangle's three vertex records
            (VOUT_BASE + index * VOUT_STRIDE), or 0 when the draw has no
            vertex records
  s3      = the triangle's flat colour (line word 9)
  s4-s11  = the draw's 8 user values
  side    = tile position and per-lane depth, returned with the export
  seq     = launch order, for the output stage

The draw state (pixel shader entry, user values, vertex record layout)
is a DrawState given at construction; the front end (M8) will drive it.

Barycentrics: lambda_k = W_k / Area from the rasteriser's integer edge
weights (W0 belongs to vertex 1, as in BarycentricDepth). They are
computed in double precision and rounded once to FP32.
'''

NO_INDEX = 0xFFFFFFFF


@dataclass
class DrawState:
    ps_pc: int = 0
    user: list = field(default_factory=lambda: [0] * 8)
    vout_base: int = 0
    vout_stride: int = 0
    has_vertex_records: bool = False   # False: s0-s2 = 0 (host-written screen-space lines)


def barycentrics(w0, w1, w2, area, inv_w_bits):
    '''-> [b0, b1, b2] as FP32 bit patterns for one lane.'''
    lam = np.array([w0, w1, w2], dtype=np.float64) / float(area)
    q = np.array([1.0 if b == 0 else float(to_f32(b)) for b in inv_w_bits])
    weighted = lam * q
    total = weighted.sum()
    b = weighted / total if total != 0 else lam
    return [f32_bits(x) for x in b]


def build_pixel_launch(g, seq, draw: DrawState):
    '''The launch (dict for drive_launch) for fragment group `g`
    (as returned by read_fragment_group).'''
    tri = g['tri_words']                       # words 9-15
    colour, indices, inv_w = tri[0], tri[1:4], tri[4:7]

    xs, ys, b = [], [], [[], [], []]
    for lane in range(GROUP_WIDTH):
        dx, dy = lane_xy(lane)
        xs.append(g['tile_x'] + dx)
        ys.append(g['tile_y'] + dy)
        for k, value in enumerate(barycentrics(g['w0'][lane], g['w1'][lane], g['w2'][lane],
                                               g['area'], inv_w)):
            b[k].append(value)

    sgprs = {31: g['coverage'], 3: colour}
    if draw.has_vertex_records and NO_INDEX not in indices:
        for k in range(3):
            sgprs[k] = draw.vout_base + indices[k] * draw.vout_stride
    for i, value in enumerate(draw.user):
        sgprs[4 + i] = value

    return {'pixel': 1, 'pc': draw.ps_pc, 'seq': seq, 'sgprs': sgprs,
            'vgprs': {0: xs, 1: ys, 2: b[0], 3: b[1], 4: b[2]},
            'tile_x': g['tile_x'], 'tile_y': g['tile_y'], 'z': g['z']}


class PixelDispatcher(py4hw.Logic):
    '''Fragment groups in, launches out, one-entry buffer (the same
    handshake structure as the flat-colour unit). The core's `ready`
    already means "a slot is free", so the dispatcher needs no slot
    bookkeeping of its own.'''

    def __init__(self, parent, name, frag: FragmentGroupInterface, launch: LaunchInterface, idle,
                 draw: DrawState):
        super().__init__(parent, name)
        self.frag = self.addInterfaceSink('frag', frag)
        self.launch = self.addInterfaceSource('launch', launch)
        self.idle = self.addOut('idle', idle)
        self.draw = draw

        self.holding = False
        self.seq = 0

        frag.ready.put(1)
        launch.valid.put(0)
        idle.put(1)

    def clock(self):
        f, ln = self.frag, self.launch

        if self.holding and ln.valid.get() == 1 and ln.ready.get() == 1:
            self.holding = False
            ln.valid.prepare(0)

        if f.valid.get() == 1 and f.ready.get() == 1:
            drive_launch(ln, build_pixel_launch(read_fragment_group(f), self.seq, self.draw))
            ln.valid.prepare(1)
            self.seq += 1
            self.holding = True

        f.ready.prepare(0 if self.holding else 1)
        self.idle.prepare(0 if self.holding or f.valid.get() == 1 else 1)

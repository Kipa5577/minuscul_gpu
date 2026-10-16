import py4hw

from ..params import GROUP_WIDTH
from ..rasteriser.fragment import FragmentGroupInterface, read_fragment_group
from ..output_stage.export import ExportInterface, drive_export

'''
Flat-colour unit: the stand-in for dispatcher + shader core during build
step 2 (M3), kept afterwards as a selectable slice mode for tests.

Turns each fragment group straight into an export packet: every covered
lane gets the triangle's flat colour (triangle-line word 9), `seq` counts
groups in arrival order.

One-entry buffer: it takes a fragment only while not holding an export,
so it sustains one group every 2 cycles - faster than the rasteriser can
emit (at least 3 cycles per group).
'''


class FlatColourUnit(py4hw.Logic):

    def __init__(self, parent, name, frag: FragmentGroupInterface, exp: ExportInterface, idle):
        super().__init__(parent, name)
        self.frag = self.addInterfaceSink('frag', frag)
        self.exp = self.addInterfaceSource('exp', exp)
        self.idle = self.addOut('idle', idle)

        self.holding = False
        self.seq = 0

        frag.ready.put(1)
        exp.valid.put(0)
        idle.put(1)

    def clock(self):
        f, e = self.frag, self.exp

        if self.holding and e.valid.get() == 1 and e.ready.get() == 1:
            self.holding = False            # export transferred at this edge
            e.valid.prepare(0)

        if f.valid.get() == 1 and f.ready.get() == 1:
            g = read_fragment_group(f)
            drive_export(e, {'seq': self.seq, 'tile_x': g['tile_x'], 'tile_y': g['tile_y'],
                             'mask': g['coverage'], 'colour': [g['tri_words'][0]] * GROUP_WIDTH,
                             'z': g['z']})
            e.valid.prepare(1)
            self.seq += 1
            self.holding = True

        f.ready.prepare(0 if self.holding else 1)
        self.idle.prepare(0 if self.holding or f.valid.get() == 1 else 1)

import py4hw

from ..params import GROUP_WIDTH, WORD_WIDTH

'''
Export packet interface: shading -> output stage. One packet = the
finished pixels of one thread group (one 4x4 tile).

Same valid/ready handshake as the fragment group interface: the source
holds `valid` and the data until an edge where `ready` is also 1.

`seq` is the group's launch sequence number. The output stage accepts
packets strictly in `seq` order (D8: exports in launch order) and buffers
early arrivals.
'''

SEQ_WIDTH = 32


class ExportInterface(py4hw.Interface):
    def __init__(self, parent, name):
        super().__init__(parent, name)

        self.valid = self.addSourceToSink('valid', 1)
        self.ready = self.addSinkToSource('ready', 1)

        self.seq = self.addSourceToSink('seq', SEQ_WIDTH)
        self.tile_x = self.addSourceToSink('tile_x', WORD_WIDTH)
        self.tile_y = self.addSourceToSink('tile_y', WORD_WIDTH)
        self.mask = self.addSourceToSink('mask', GROUP_WIDTH)
        self.colour = [self.addSourceToSink(f'colour{i}', WORD_WIDTH) for i in range(GROUP_WIDTH)]
        self.z = [self.addSourceToSink(f'z{i}', WORD_WIDTH) for i in range(GROUP_WIDTH)]


def read_export(exp: ExportInterface):
    '''Snapshot of the interface's current data as plain Python values.'''
    s = lambda wire: py4hw.IntegerHelper.c2_to_signed(wire.get(), WORD_WIDTH)
    return {
        'seq': exp.seq.get(),
        'tile_x': s(exp.tile_x),
        'tile_y': s(exp.tile_y),
        'mask': exp.mask.get(),
        'colour': [w.get() for w in exp.colour],
        'z': [s(w) for w in exp.z],
    }


def drive_export(exp: ExportInterface, packet):
    '''prepare() a packet (dict as returned by read_export) on the
    interface's data wires; the caller drives `valid`.'''
    mask32 = (1 << WORD_WIDTH) - 1
    exp.seq.prepare(packet['seq'])
    exp.tile_x.prepare(packet['tile_x'] & mask32)
    exp.tile_y.prepare(packet['tile_y'] & mask32)
    exp.mask.prepare(packet['mask'])
    for wire, value in zip(exp.colour, packet['colour']):
        wire.prepare(value & mask32)
    for wire, value in zip(exp.z, packet['z']):
        wire.prepare(value & mask32)

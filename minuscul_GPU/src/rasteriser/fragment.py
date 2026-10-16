import py4hw

from ..params import GROUP_WIDTH, WORD_WIDTH

'''
Fragment group interface: rasteriser -> shading (flat-colour unit in M3,
dispatcher in M6). One transfer = one covered 4x4 tile of one triangle,
lane i covering pixel (tile_x + i % 4, tile_y + i // 4).

Handshake: the source holds `valid` = 1 and the data steady until a clock
edge where `ready` (driven by the sink) is also 1 - that edge is the
transfer. Both sides decide from the values they `get()` before the edge.

All 32-bit fields carry two's-complement values; read them back with
py4hw.IntegerHelper.c2_to_signed.
'''

# triangle-line words passed through untouched: 9 = flat colour,
# 10-12 = vertex indices, 13-15 = 1/w (README_ISA.md §6.5)
TRI_WORDS_FIRST = 9
TRI_WORDS_COUNT = 7


class FragmentGroupInterface(py4hw.Interface):
    def __init__(self, parent, name):
        super().__init__(parent, name)

        self.valid = self.addSourceToSink('valid', 1)
        self.ready = self.addSinkToSource('ready', 1)

        self.tile_x = self.addSourceToSink('tile_x', WORD_WIDTH)
        self.tile_y = self.addSourceToSink('tile_y', WORD_WIDTH)
        self.coverage = self.addSourceToSink('coverage', GROUP_WIDTH)
        self.area = self.addSourceToSink('area', WORD_WIDTH)
        self.tri_words = [self.addSourceToSink(f'tri_word{TRI_WORDS_FIRST + i}', WORD_WIDTH)
                          for i in range(TRI_WORDS_COUNT)]

        self.z = [self.addSourceToSink(f'z{i}', WORD_WIDTH) for i in range(GROUP_WIDTH)]
        self.w0 = [self.addSourceToSink(f'w0_{i}', WORD_WIDTH) for i in range(GROUP_WIDTH)]
        self.w1 = [self.addSourceToSink(f'w1_{i}', WORD_WIDTH) for i in range(GROUP_WIDTH)]
        self.w2 = [self.addSourceToSink(f'w2_{i}', WORD_WIDTH) for i in range(GROUP_WIDTH)]


def read_fragment_group(frag: FragmentGroupInterface):
    '''Snapshot of the interface's current data as plain Python values
    (signed ints), for sinks and testbenches.'''
    s = lambda wire: py4hw.IntegerHelper.c2_to_signed(wire.get(), WORD_WIDTH)
    return {
        'tile_x': s(frag.tile_x),
        'tile_y': s(frag.tile_y),
        'coverage': frag.coverage.get(),
        'area': s(frag.area),
        'tri_words': [w.get() for w in frag.tri_words],
        'z': [s(w) for w in frag.z],
        'w0': [s(w) for w in frag.w0],
        'w1': [s(w) for w in frag.w1],
        'w2': [s(w) for w in frag.w2],
    }

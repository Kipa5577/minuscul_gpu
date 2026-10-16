'''
Chip-wide parameters, defined once (see src/README.md "Conventions").
Values follow the architecture decisions in Architecture..md.
'''

GROUP_WIDTH = 16          # threads per group = lanes per shader core (D4)
TILE_SIZE = 4             # one fragment group = one TILE_SIZE x TILE_SIZE tile
GROUP_SLOTS = 4           # groups in flight per shader core (D8), default
IMEM_WORDS = 1024         # instructions per core instruction memory (D13)

WORD_WIDTH = 32
LINE_WORDS = 16           # one memory line = 16 words = 512 bits (D9)
LINE_WIDTH = LINE_WORDS * WORD_WIDTH

WORD_MASK = (1 << WORD_WIDTH) - 1
LANE_MASK = (1 << GROUP_WIDTH) - 1


def lane_xy(lane):
    '''Pixel offset (dx, dy) of `lane` inside its 4x4 tile (README_ISA.md §1).'''
    return lane % TILE_SIZE, lane // TILE_SIZE

import py4hw

'''
512-bit triangle line format, shared by the rasteriser, the binner and
(from M9) the primitive unit: words 0-8 = V1X V1Y V1Z V2X V2Y V2Z V3X V3Y
V3Z, word 9 = flat colour, as 32-bit two's-complement fields. Words 10-15
are defined in README_ISA.md §6.5.
'''

TRIANGLE_LINE_WIDTH = 512
TRIANGLE_FIELD_WIDTH = 32
TRIANGLE_NUM_FIELDS = 10  # V1X,V1Y,V1Z, V2X,V2Y,V2Z, V3X,V3Y,V3Z, COLOR


def pack_triangle_line(v1, v2, v3, color):
    '''Packs (v1, v2, v3, color) - each vertex an (x, y, z) triple - into one
    512-bit triangle-memory line, matching RenderController's unpacking.'''
    fields = [*v1, *v2, *v3, color]
    mask = (1 << TRIANGLE_FIELD_WIDTH) - 1
    line = 0
    for i, value in enumerate(fields):
        line |= (value & mask) << (i * TRIANGLE_FIELD_WIDTH)
    return line


def unpack_triangle_line(line):
    '''Inverse of pack_triangle_line: returns the 10 fields as signed ints.'''
    mask = (1 << TRIANGLE_FIELD_WIDTH) - 1
    fields = []
    for i in range(TRIANGLE_NUM_FIELDS):
        raw = (line >> (i * TRIANGLE_FIELD_WIDTH)) & mask
        fields.append(py4hw.IntegerHelper.c2_to_signed(raw, TRIANGLE_FIELD_WIDTH))
    return fields

'''
VENUS v2 instruction table and bit-level encode/decode (README_ISA.md §4-5).

The single source of truth for encodings: the assembler/disassembler
(tools/venus_asm.py), the reference model and the hardware decoder (M5)
all use these tables.
'''

# --- formats and field layout (README_ISA.md §4) ------------------------------

SOP, SOPK, SOPP, VOP, VOPS, VMEM, EXP = range(7)
FORMAT_NAMES = {SOP: 'SOP', SOPK: 'SOPK', SOPP: 'SOPP', VOP: 'VOP', VOPS: 'VOPS', VMEM: 'VMEM', EXP: 'EXP'}

# field name -> (low bit, width), per format; FMT [31:28] and OP [27:22]
# are common to all
FIELDS = {
    SOP:  {'sdst': (17, 5), 'ssrc0': (12, 5), 'ssrc1': (7, 5)},
    SOPK: {'sdst': (17, 5), 'imm16': (0, 16)},
    SOPP: {'simm16': (0, 16)},
    VOP:  {'vdst': (17, 5), 's0sel': (16, 1), 'src0': (11, 5), 'vsrc1': (6, 5), 'aux5': (1, 5)},
    VOPS: {'sdst': (17, 5), 's0sel': (16, 1), 'src0': (11, 5), 'vsrc1': (6, 5)},
    VMEM: {'vdata': (17, 5), 'vaddr': (12, 5), 'sbase': (7, 5), 'offset': (0, 7)},
    EXP:  {'vsrc': (17, 5)},
}

NUM_VGPRS = 32
NUM_SGPRS = 32
EXEC_SGPR = 31

# --- operand kinds --------------------------------------------------------------
#   S    scalar register s0-s31            -> field
#   V    vector register v0-v31            -> field
#   SV   VGPR or SGPR (operand A)          -> src0 + s0sel
#   I16  signed 16-bit immediate           -> imm16 (two's complement)
#   U16  unsigned 16-bit immediate         -> imm16
#   L16  branch target (label or offset)   -> imm16/simm16 = target - (PC + 1)
#   U5   unsigned 5-bit immediate          -> aux5
#   U7   unsigned 7-bit word offset        -> offset

_S3 = [('S', 'sdst'), ('S', 'ssrc0'), ('S', 'ssrc1')]
_S2 = [('S', 'sdst'), ('S', 'ssrc0')]
_SC = [('S', 'ssrc0'), ('S', 'ssrc1')]
_V3 = [('V', 'vdst'), ('SV', 'src0'), ('V', 'vsrc1')]
_V2 = [('V', 'vdst'), ('SV', 'src0')]
_CMP = [('S', 'sdst'), ('SV', 'src0'), ('V', 'vsrc1')]
_BR = [('L16', 'simm16')]

# mnemonic -> (format, opcode, operands)
INSTRUCTIONS = {
    # SOP (§5.1)
    'S_MOV': (SOP, 0x00, _S2), 'S_NOT': (SOP, 0x01, _S2),
    'S_ADD': (SOP, 0x02, _S3), 'S_SUB': (SOP, 0x03, _S3), 'S_MUL': (SOP, 0x04, _S3),
    'S_AND': (SOP, 0x05, _S3), 'S_OR': (SOP, 0x06, _S3), 'S_XOR': (SOP, 0x07, _S3),
    'S_ANDN2': (SOP, 0x08, _S3), 'S_SHL': (SOP, 0x09, _S3), 'S_SHR': (SOP, 0x0A, _S3),
    'S_ASHR': (SOP, 0x0B, _S3), 'S_MIN_I32': (SOP, 0x0C, _S3), 'S_MAX_I32': (SOP, 0x0D, _S3),
    'S_BITCNT': (SOP, 0x0E, _S2), 'S_FF1': (SOP, 0x0F, _S2),
    'S_CMP_EQ': (SOP, 0x10, _SC), 'S_CMP_NE': (SOP, 0x11, _SC), 'S_CMP_LT': (SOP, 0x12, _SC),
    'S_CMP_LE': (SOP, 0x13, _SC), 'S_CMP_GT': (SOP, 0x14, _SC), 'S_CMP_GE': (SOP, 0x15, _SC),
    'S_CMP_LT_U32': (SOP, 0x16, _SC),
    'S_SAVEEXEC_AND': (SOP, 0x18, _S2),
    'S_SETPC': (SOP, 0x19, [('S', 'ssrc0')]),
    # SOPK (§5.2)
    'S_MOVK': (SOPK, 0x00, [('S', 'sdst'), ('I16', 'imm16')]),
    'S_MOVHI': (SOPK, 0x01, [('S', 'sdst'), ('U16', 'imm16')]),
    'S_ORK': (SOPK, 0x02, [('S', 'sdst'), ('U16', 'imm16')]),
    'S_ADDK': (SOPK, 0x03, [('S', 'sdst'), ('I16', 'imm16')]),
    'S_CMPK_EQ': (SOPK, 0x04, [('S', 'sdst'), ('I16', 'imm16')]),
    'S_CMPK_LT': (SOPK, 0x05, [('S', 'sdst'), ('I16', 'imm16')]),
    'S_CMPK_GE': (SOPK, 0x06, [('S', 'sdst'), ('I16', 'imm16')]),
    'S_CALL': (SOPK, 0x07, [('S', 'sdst'), ('L16', 'imm16')]),
    # SOPP (§5.3)
    'S_NOP': (SOPP, 0x00, []), 'S_ENDPGM': (SOPP, 0x01, []),
    'S_BRANCH': (SOPP, 0x02, _BR), 'S_CBRANCH_SCC0': (SOPP, 0x03, _BR),
    'S_CBRANCH_SCC1': (SOPP, 0x04, _BR), 'S_CBRANCH_EXECZ': (SOPP, 0x05, _BR),
    'S_CBRANCH_EXECNZ': (SOPP, 0x06, _BR),
    # VOP (§5.4)
    'V_MOV': (VOP, 0x00, _V2), 'V_NOT': (VOP, 0x01, _V2),
    'V_ADD_I32': (VOP, 0x02, _V3), 'V_SUB_I32': (VOP, 0x03, _V3), 'V_MUL_I32': (VOP, 0x04, _V3),
    'V_AND': (VOP, 0x05, _V3), 'V_OR': (VOP, 0x06, _V3), 'V_XOR': (VOP, 0x07, _V3),
    'V_SHL': (VOP, 0x08, _V3), 'V_SHR': (VOP, 0x09, _V3), 'V_ASHR': (VOP, 0x0A, _V3),
    'V_MIN_I32': (VOP, 0x0B, _V3), 'V_MAX_I32': (VOP, 0x0C, _V3),
    'V_LSHL_OR': (VOP, 0x0D, _V3 + [('U5', 'aux5')]),
    'V_CNDMASK': (VOP, 0x0E, _V3 + [('S', 'aux5')]),
    'V_LANEID': (VOP, 0x0F, [('V', 'vdst')]),
    'V_ADD_F32': (VOP, 0x10, _V3), 'V_SUB_F32': (VOP, 0x11, _V3), 'V_MUL_F32': (VOP, 0x12, _V3),
    'V_MAC_F32': (VOP, 0x13, _V3), 'V_MIN_F32': (VOP, 0x14, _V3), 'V_MAX_F32': (VOP, 0x15, _V3),
    'V_CVT_F32_I32': (VOP, 0x18, _V2), 'V_CVT_I32_F32': (VOP, 0x19, _V2),
    'V_CVT_UNORM8_F32': (VOP, 0x1A, _V2), 'V_RCP_F32': (VOP, 0x1B, _V2),
    'V_RSQ_F32': (VOP, 0x1C, _V2), 'V_SQRT_F32': (VOP, 0x1D, _V2),
    'V_FLOOR_F32': (VOP, 0x1E, _V2), 'V_FRACT_F32': (VOP, 0x1F, _V2),
    # VOPS (§5.5)
    'V_CMP_EQ_F32': (VOPS, 0x00, _CMP), 'V_CMP_NE_F32': (VOPS, 0x01, _CMP),
    'V_CMP_LT_F32': (VOPS, 0x02, _CMP), 'V_CMP_LE_F32': (VOPS, 0x03, _CMP),
    'V_CMP_GT_F32': (VOPS, 0x04, _CMP), 'V_CMP_GE_F32': (VOPS, 0x05, _CMP),
    'V_CMP_EQ_I32': (VOPS, 0x08, _CMP), 'V_CMP_NE_I32': (VOPS, 0x09, _CMP),
    'V_CMP_LT_I32': (VOPS, 0x0A, _CMP), 'V_CMP_LE_I32': (VOPS, 0x0B, _CMP),
    'V_CMP_GT_I32': (VOPS, 0x0C, _CMP), 'V_CMP_GE_I32': (VOPS, 0x0D, _CMP),
    'V_READFIRSTLANE': (VOPS, 0x10, [('S', 'sdst'), ('V', 'src0')]),
    # VMEM (§5.6)
    'V_LOAD': (VMEM, 0x00, [('V', 'vdata'), ('V', 'vaddr'), ('S', 'sbase'), ('U7', 'offset')]),
    'V_LOAD_LANE': (VMEM, 0x01, [('V', 'vdata'), ('S', 'sbase'), ('U7', 'offset')]),
    'V_LOAD_BCAST': (VMEM, 0x02, [('V', 'vdata'), ('S', 'sbase'), ('U7', 'offset')]),
    'V_STORE': (VMEM, 0x08, [('V', 'vdata'), ('V', 'vaddr'), ('S', 'sbase'), ('U7', 'offset')]),
    'V_STORE_LANE': (VMEM, 0x09, [('V', 'vdata'), ('S', 'sbase'), ('U7', 'offset')]),
    # EXP (§5.7)
    'V_EXPORT_COLOR': (EXP, 0x00, [('V', 'vsrc')]),
}

BY_ENCODING = {(fmt, op): name for name, (fmt, op, _) in INSTRUCTIONS.items()}


class IllegalInstruction(Exception):
    pass


def encode(mnemonic, fields):
    '''fields: field name -> unsigned value (already range-checked).'''
    fmt, op, _ = INSTRUCTIONS[mnemonic]
    word = (fmt << 28) | (op << 22)
    for name, value in fields.items():
        low, width = FIELDS[fmt][name]
        assert 0 <= value < (1 << width), f'{mnemonic}: {name}={value} does not fit {width} bits'
        word |= value << low
    return word


def decode(word):
    '''-> (mnemonic, {field: value}). Raises IllegalInstruction for a
    reserved format/opcode, or non-zero bits in unused fields.'''
    fmt, op = (word >> 28) & 0xF, (word >> 22) & 0x3F
    name = BY_ENCODING.get((fmt, op))
    if name is None:
        raise IllegalInstruction(f'illegal instruction 0x{word:08X} (format {fmt}, opcode 0x{op:02X})')
    fields = {}
    used = (0xF << 28) | (0x3F << 22)
    for field, (low, width) in FIELDS[fmt].items():
        fields[field] = (word >> low) & ((1 << width) - 1)
        used |= ((1 << width) - 1) << low
    if word & ~used & 0xFFFFFFFF:
        raise IllegalInstruction(f'illegal instruction 0x{word:08X}: reserved bits set')
    return name, fields


def signed16(value):
    return value - 0x10000 if value & 0x8000 else value

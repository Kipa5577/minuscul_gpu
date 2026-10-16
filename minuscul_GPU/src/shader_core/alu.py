'''
VENUS v2 operation semantics (README_ISA.md §2, §5): pure functions on
32-bit patterns, shared by the reference model (venus_ref.py) and the
hardware core (core.py).

FP32 rules (D10): round to nearest even; denormal inputs and outputs are
flushed to zero (sign kept); no traps; any NaN result is the canonical
quiet NaN 0x7FC00000. Add/sub/mul/div/sqrt use numpy float32, which is
IEEE correctly rounded. The fused multiply-add is computed exactly with
fractions and rounded once.
'''
import math
import struct
from fractions import Fraction

import numpy as np

from ..params import GROUP_WIDTH

M32 = 0xFFFFFFFF
CANONICAL_NAN = 0x7FC00000


def s32(x):
    '''unsigned 32-bit pattern -> signed int'''
    x &= M32
    return x - (1 << 32) if x & 0x80000000 else x


# --- FP32 conversion with flush-to-zero ------------------------------------------

def _is_denormal(bits):
    return (bits & 0x7F800000) == 0 and (bits & 0x007FFFFF) != 0


def to_f32(bits):
    '''32-bit pattern -> np.float32, denormal inputs flushed to signed zero.'''
    bits &= M32
    if _is_denormal(bits):
        bits &= 0x80000000
    return np.frombuffer(struct.pack('<I', bits), dtype=np.float32)[0]


def from_f32(x):
    '''np.float32 (or float) -> 32-bit pattern, denormal outputs flushed,
    NaN canonicalised.'''
    x = np.float32(x)
    if np.isnan(x):
        return CANONICAL_NAN
    bits = struct.unpack('<I', x.tobytes())[0]
    if _is_denormal(bits):
        bits &= 0x80000000
    return bits


def f32_bits(value):
    '''Python float -> pattern (convenience for tests and programs).'''
    return from_f32(np.float32(value))


def round_fraction_to_f32(q, negative_zero=False):
    '''Exact rational -> nearest FP32 pattern (round half to even), with
    overflow to infinity and flush-to-zero of denormal results.'''
    if q == 0:
        return 0x80000000 if negative_zero else 0
    sign = 0x80000000 if q < 0 else 0
    a = abs(q)
    e = a.numerator.bit_length() - a.denominator.bit_length()
    if Fraction(2) ** e > a:
        e -= 1
    # now 2^e <= a < 2^(e+1)
    e_eff = max(e, -126)
    scaled = a * Fraction(2) ** (23 - e_eff)
    m = math.floor(scaled)
    rest = scaled - m
    if rest > Fraction(1, 2) or (rest == Fraction(1, 2) and m % 2 == 1):
        m += 1
    if m >= 1 << 24:
        m >>= 1
        e_eff += 1
    if m < 1 << 23:
        return sign            # denormal result: flushed to zero
    if e_eff > 127:
        return sign | 0x7F800000
    return sign | ((e_eff + 127) << 23) | (m - (1 << 23))


def _fp(fn):
    '''Wrap a float32 function of float32 args into a pattern function.'''
    def wrapped(*bits):
        with np.errstate(all='ignore'):
            return from_f32(fn(*(to_f32(b) for b in bits)))
    return wrapped


def _fma(a_bits, b_bits, d_bits):
    a, b, d = to_f32(a_bits), to_f32(b_bits), to_f32(d_bits)
    with np.errstate(all='ignore'):
        if not (np.isfinite(a) and np.isfinite(b) and np.isfinite(d)):
            return from_f32(np.float32(np.float64(a) * np.float64(b) + np.float64(d)))
        product = Fraction(float(a)) * Fraction(float(b))
        exact = product + Fraction(float(d))
        product_neg_zero = product == 0 and (np.signbit(a) != np.signbit(b))
        return round_fraction_to_f32(exact, negative_zero=product_neg_zero and np.signbit(d))


def _min(a, b):
    if np.isnan(a):
        return b
    if np.isnan(b):
        return a
    return min(a, b)


def _max(a, b):
    if np.isnan(a):
        return b
    if np.isnan(b):
        return a
    return max(a, b)


def _cvt_i32_f32(a_bits):
    a = to_f32(a_bits)
    if np.isnan(a):
        return 0
    if a >= 2.0 ** 31:
        return 0x7FFFFFFF
    if a <= -2.0 ** 31:
        return 0x80000000
    return int(a) & M32       # int() truncates toward zero


def _cvt_unorm8(a_bits):
    a = to_f32(a_bits)
    if np.isnan(a):
        return 0
    a = min(max(float(a), 0.0), 1.0)
    return int(np.round(np.float64(a) * 255.0))   # numpy rounds half to even


def _rsq(a_bits):
    a = to_f32(a_bits)
    with np.errstate(all='ignore'):
        return from_f32(np.float32(1.0 / np.sqrt(np.float64(a))))


# --- scalar ALU (SOP / SOPK) ------------------------------------------------------

def _ff1(a):
    a &= M32
    return (a & -a).bit_length() - 1 & M32 if a else M32


SALU = {
    'S_MOV': lambda a, b: a,
    'S_NOT': lambda a, b: ~a & M32,
    'S_ADD': lambda a, b: (a + b) & M32,
    'S_SUB': lambda a, b: (a - b) & M32,
    'S_MUL': lambda a, b: (a * b) & M32,
    'S_AND': lambda a, b: a & b,
    'S_OR': lambda a, b: a | b,
    'S_XOR': lambda a, b: a ^ b,
    'S_ANDN2': lambda a, b: a & ~b & M32,
    'S_SHL': lambda a, b: (a << (b & 31)) & M32,
    'S_SHR': lambda a, b: a >> (b & 31),
    'S_ASHR': lambda a, b: (s32(a) >> (b & 31)) & M32,
    'S_MIN_I32': lambda a, b: a if s32(a) <= s32(b) else b,
    'S_MAX_I32': lambda a, b: a if s32(a) >= s32(b) else b,
    'S_BITCNT': lambda a, b: bin(a & M32).count('1'),
    'S_FF1': lambda a, b: _ff1(a),
}

SCMP = {
    'S_CMP_EQ': lambda a, b: a == b,
    'S_CMP_NE': lambda a, b: a != b,
    'S_CMP_LT': lambda a, b: s32(a) < s32(b),
    'S_CMP_LE': lambda a, b: s32(a) <= s32(b),
    'S_CMP_GT': lambda a, b: s32(a) > s32(b),
    'S_CMP_GE': lambda a, b: s32(a) >= s32(b),
    'S_CMP_LT_U32': lambda a, b: (a & M32) < (b & M32),
}

# --- vector ALU (VOP), per lane ------------------------------------------------------

VALU = {
    'V_MOV': lambda a, b: a,
    'V_NOT': lambda a, b: ~a & M32,
    'V_ADD_I32': lambda a, b: (a + b) & M32,
    'V_SUB_I32': lambda a, b: (a - b) & M32,
    'V_MUL_I32': lambda a, b: (a * b) & M32,
    'V_AND': lambda a, b: a & b,
    'V_OR': lambda a, b: a | b,
    'V_XOR': lambda a, b: a ^ b,
    'V_SHL': lambda a, b: (b << (a & 31)) & M32,
    'V_SHR': lambda a, b: (b & M32) >> (a & 31),
    'V_ASHR': lambda a, b: (s32(b) >> (a & 31)) & M32,
    'V_MIN_I32': lambda a, b: a if s32(a) <= s32(b) else b,
    'V_MAX_I32': lambda a, b: a if s32(a) >= s32(b) else b,
    'V_ADD_F32': _fp(lambda a, b: a + b),
    'V_SUB_F32': _fp(lambda a, b: a - b),
    'V_MUL_F32': _fp(lambda a, b: a * b),
    'V_MIN_F32': _fp(_min),
    'V_MAX_F32': _fp(_max),
    'V_CVT_F32_I32': lambda a, b: from_f32(np.float32(s32(a))),
    'V_CVT_I32_F32': lambda a, b: _cvt_i32_f32(a),
    'V_CVT_UNORM8_F32': lambda a, b: _cvt_unorm8(a),
    'V_RCP_F32': lambda a, b: _fp(lambda x: np.float32(1.0) / x)(a),
    'V_RSQ_F32': lambda a, b: _rsq(a),
    'V_SQRT_F32': lambda a, b: _fp(np.sqrt)(a),
    'V_FLOOR_F32': lambda a, b: _fp(np.floor)(a),
    'V_FRACT_F32': lambda a, b: _fp(lambda x: x - np.floor(x))(a),
}

# V_MAC_F32 (d = a*b + d), V_LSHL_OR, V_CNDMASK and V_LANEID need more
# than (a, b) and are handled by the interpreter directly
V_MAC = _fma


def _fcmp(op):
    def cmp(a_bits, b_bits):
        a, b = to_f32(a_bits), to_f32(b_bits)
        if np.isnan(a) or np.isnan(b):
            return op == 'NE'
        return {'EQ': a == b, 'NE': a != b, 'LT': a < b, 'LE': a <= b, 'GT': a > b, 'GE': a >= b}[op]
    return cmp


def _icmp(op):
    def cmp(a_bits, b_bits):
        a, b = s32(a_bits), s32(b_bits)
        return {'EQ': a == b, 'NE': a != b, 'LT': a < b, 'LE': a <= b, 'GT': a > b, 'GE': a >= b}[op]
    return cmp


VCMP = {}
for _op in ('EQ', 'NE', 'LT', 'LE', 'GT', 'GE'):
    VCMP[f'V_CMP_{_op}_F32'] = _fcmp(_op)
    VCMP[f'V_CMP_{_op}_I32'] = _icmp(_op)

assert GROUP_WIDTH == 16

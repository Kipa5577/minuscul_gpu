'''
M5 testbench, part 1: operation semantics (src/shader_core/alu.py)
against README_ISA.md §2 and §5.
'''
import random
import struct
from fractions import Fraction

import numpy as np
import pytest

from ...src.shader_core.alu import (CANONICAL_NAN, SALU, SCMP, VALU, VCMP, V_MAC, f32_bits,
                                    round_fraction_to_f32, s32)

F = f32_bits
NAN, INF = CANONICAL_NAN, 0x7F800000
DENORM = 0x00000001


def value(bits):
    return struct.unpack('<f', struct.pack('<I', bits))[0]


@pytest.mark.parametrize('op,a,b,expected', [
    ('V_ADD_F32', 1.5, 2.25, 3.75),
    ('V_SUB_F32', 1.0, 3.0, -2.0),
    ('V_MUL_F32', -1.5, 4.0, -6.0),
    ('V_MIN_F32', 2.0, -3.0, -3.0),
    ('V_MAX_F32', 2.0, -3.0, 2.0),
])
def test_basic_float_ops(op, a, b, expected):
    assert VALU[op](F(a), F(b)) == F(expected)


def test_float_add_rounds_to_nearest_even():
    # 1 + 2^-24 is exactly half an ulp above 1.0: ties to even -> 1.0
    assert VALU['V_ADD_F32'](F(1.0), F(2.0 ** -24)) == F(1.0)
    # 1 + 3 * 2^-24 is half an ulp above 1 + 2^-23 (odd): ties to even -> 1 + 2^-22
    assert VALU['V_ADD_F32'](F(1.0 + 2.0 ** -23), F(2.0 ** -24)) == F(1.0 + 2.0 ** -22)


def test_denormals_are_flushed_to_zero():
    assert VALU['V_ADD_F32'](DENORM, 0) == 0                        # denormal input
    assert VALU['V_MUL_F32'](F(2.0 ** -100), F(2.0 ** -30)) == 0     # denormal result
    assert VALU['V_MUL_F32'](F(-2.0 ** -100), F(2.0 ** -30)) == 0x80000000  # sign kept
    assert VALU['V_MOV'](DENORM, 0) == DENORM                        # moves are bit copies


def test_nan_and_infinity():
    assert VALU['V_MUL_F32'](F(0.0), INF) == NAN
    assert VALU['V_ADD_F32'](INF, F(1.0)) == INF
    assert VALU['V_ADD_F32'](0x7FC12345, F(1.0)) == NAN               # NaN results are canonical
    assert VALU['V_MIN_F32'](NAN, F(1.0)) == F(1.0)
    assert VALU['V_MAX_F32'](F(1.0), NAN) == F(1.0)
    assert VALU['V_MIN_F32'](NAN, NAN) == NAN
    assert VALU['V_MUL_F32'](F(3e38), F(10.0)) == INF                # overflow


def test_compares():
    assert VCMP['V_CMP_LT_F32'](F(-1.0), F(2.0))
    assert not VCMP['V_CMP_EQ_F32'](NAN, NAN)
    assert VCMP['V_CMP_NE_F32'](NAN, F(1.0))
    assert not VCMP['V_CMP_LT_F32'](NAN, F(1.0))
    assert VCMP['V_CMP_EQ_F32'](F(0.0), F(-0.0))
    assert VCMP['V_CMP_LT_I32'](0xFFFFFFFF, 0)                       # -1 < 0, signed
    assert VCMP['V_CMP_GE_I32'](5, 5)


@pytest.mark.parametrize('a,expected', [
    (2.9, 2), (-2.9, -2), (3e9, 0x7FFFFFFF), (-3e9, -0x80000000), (float('nan'), 0),
])
def test_cvt_i32_f32_truncates_and_saturates(a, expected):
    assert VALU['V_CVT_I32_F32'](F(a), 0) == expected & 0xFFFFFFFF


def test_cvt_f32_i32():
    assert VALU['V_CVT_F32_I32'](-5 & 0xFFFFFFFF, 0) == F(-5.0)
    assert VALU['V_CVT_F32_I32'](16777217, 0) == F(16777216.0)       # rounds to nearest even


@pytest.mark.parametrize('a,expected', [
    (0.0, 0), (1.0, 255), (0.5, 128), (-1.0, 0), (2.0, 255), (float('nan'), 0), (0.25, 64),
])
def test_cvt_unorm8(a, expected):
    # 0.5 * 255 = 127.5 -> ties to even -> 128; 0.25 * 255 = 63.75 -> 64
    assert VALU['V_CVT_UNORM8_F32'](F(a), 0) == expected


def test_transcendental_and_rounding_ops():
    assert VALU['V_RCP_F32'](F(3.0), 0) == F(np.float32(1) / np.float32(3))
    assert VALU['V_RCP_F32'](F(0.0), 0) == INF
    assert VALU['V_RSQ_F32'](F(4.0), 0) == F(0.5)
    assert VALU['V_SQRT_F32'](F(2.0), 0) == F(np.sqrt(np.float32(2)))
    assert VALU['V_SQRT_F32'](F(-1.0), 0) == NAN
    assert VALU['V_FLOOR_F32'](F(-1.5), 0) == F(-2.0)
    assert VALU['V_FRACT_F32'](F(-1.25), 0) == F(0.75)


def test_mac_is_fused_with_a_single_rounding():
    a = b = F(1.0 + 2.0 ** -12)
    # exact a*b - 1 = 2^-11 + 2^-24; rounding a*b first would lose the 2^-24
    assert V_MAC(a, b, F(-1.0)) == F(2.0 ** -11 + 2.0 ** -24)
    separate = VALU['V_ADD_F32'](VALU['V_MUL_F32'](a, b), F(-1.0))
    assert separate == F(2.0 ** -11)
    assert V_MAC(F(2.0), F(3.0), F(1.0)) == F(7.0)
    assert V_MAC(INF, F(0.0), F(1.0)) == NAN


def test_exact_rounding_matches_numpy_on_random_doubles():
    rng = random.Random(7)
    for _ in range(2000):
        x = rng.uniform(-1, 1) * 2.0 ** rng.randint(-120, 120)
        expected = F(np.float32(x))
        assert round_fraction_to_f32(Fraction(x)) == expected, x


@pytest.mark.parametrize('op,a,b,expected', [
    ('S_ADD', 0xFFFFFFFF, 2, 1),
    ('S_SUB', 0, 1, 0xFFFFFFFF),
    ('S_MUL', 0x10000, 0x10000, 0),
    ('S_ANDN2', 0b1111, 0b0101, 0b1010),
    ('S_SHL', 1, 33, 2),                       # shift amount masked to 5 bits
    ('S_SHR', 0x80000000, 31, 1),
    ('S_ASHR', 0x80000000, 31, 0xFFFFFFFF),
    ('S_MIN_I32', 0xFFFFFFFF, 1, 0xFFFFFFFF),  # -1 < 1
    ('S_MAX_I32', 0xFFFFFFFF, 1, 1),
    ('S_BITCNT', 0xF0F0, 0, 8),
    ('S_FF1', 0b101000, 0, 3),
    ('S_FF1', 0, 0, 0xFFFFFFFF),
])
def test_scalar_integer_ops(op, a, b, expected):
    assert SALU[op](a, b) == expected


def test_scalar_compares():
    assert SCMP['S_CMP_LT'](0xFFFFFFFF, 0)            # signed: -1 < 0
    assert not SCMP['S_CMP_LT_U32'](0xFFFFFFFF, 0)    # unsigned
    assert s32(0x80000000) == -2 ** 31


def test_vector_shifts_take_the_amount_from_operand_a():
    assert VALU['V_SHL'](4, 1) == 16
    assert VALU['V_SHR'](4, 0x100) == 0x10
    assert VALU['V_ASHR'](1, 0x80000000) == 0xC0000000

'''
M5 testbench, part 2: the reference interpreter (venus_ref.py) on
directed programs - control flow, EXEC handling, memory, export.
'''
import numpy as np
import pytest

from ...src.params import LANE_MASK
from ...src.shader_core.alu import f32_bits
from ...src.shader_core.venus_ref import GroupState, VenusError, run
from ...tools.venus_asm import assemble

ALL = LANE_MASK


def run_src(source, sgprs=None, vgprs=None, memory=None, entry=None):
    program = assemble(source)
    pc = program.entries[entry] if entry else 0
    sgprs = {31: ALL, **(sgprs or {})}
    st = GroupState(pc, sgprs, vgprs)
    memory = memory if memory is not None else [0] * 512
    exports, steps = run(st, program.words, memory)
    return st, memory, exports, steps


def test_scalar_loop_with_scc():
    st, _, _, steps = run_src('''
        S_MOVK s1, 10
        S_MOVK s2, 0
    loop:
        S_ADD s2, s2, s1
        S_ADDK s1, -1
        S_CMPK_EQ s1, 0
        S_CBRANCH_SCC0 loop
        S_ENDPGM
    ''')
    assert st.s[2] == 55 and steps == 2 + 4 * 10 + 1


def test_vector_memory_forms():
    memory = list(range(1000, 1512))
    st, memory, _, _ = run_src('''
        V_LANEID v0
        V_LOAD_LANE v1, s4, 0          ; v1[i] = mem[16 + i]
        V_LOAD_BCAST v2, s4, 5         ; v2[i] = mem[21]
        V_ADD_I32 v3, v0, v0           ; v3[i] = 2i
        V_LOAD v4, v3, s4, 1           ; v4[i] = mem[16 + 2i + 1]
        V_ADD_I32 v5, v1, v2
        V_STORE_LANE v5, s5, 0         ; mem[200 + i] = v5[i]
        V_STORE v0, v3, s5, 40         ; mem[240 + 2i] = i
        S_ENDPGM
    ''', sgprs={4: 16, 5: 200}, memory=memory)
    for i in range(16):
        assert st.v[1][i] == 1016 + i and st.v[2][i] == 1021 and st.v[4][i] == 1016 + 2 * i + 1
        assert memory[200 + i] == 1016 + i + 1021
        assert memory[240 + 2 * i] == i


def test_if_else_endif_divergence_and_exec_restore():
    st, _, _, _ = run_src('''
        V_LANEID v0
        S_MOVK s1, 8
        V_CMP_GT_I32 s10, s1, v0       ; lanes 0-7: 8 > lane
        IF s10, s20
            S_MOVK s2, 1               ; scalar: runs once
            V_MOVK_PLACEHOLDER
        ELSE
            V_MOV v1, s3
        ENDIF
        S_ENDPGM
    '''.replace('V_MOVK_PLACEHOLDER', 'V_MOV v1, s4'), sgprs={3: 333, 4: 444})
    assert st.v[1] == [444] * 8 + [333] * 8
    assert st.exec == ALL and st.s[2] == 1


def test_execz_branch_skips_an_empty_block():
    st, _, _, _ = run_src('''
        S_MOVK s10, 0                  ; no lane takes the IF
        IF s10, s20
            S_MOVK s2, 7               ; must be skipped
        ENDIF
        S_ENDPGM
    ''')
    assert st.s[2] == 0 and st.exec == ALL


def test_nested_ifs():
    st, _, _, _ = run_src('''
        V_LANEID v0
        S_MOVK s1, 8
        S_MOVK s2, 4
        V_CMP_GT_I32 s10, s1, v0       ; lanes 0-7
        IF s10, s20
            V_CMP_GT_I32 s11, s2, v0   ; lanes 0-3 (of 0-7)
            IF s11, s21
                V_MOV v1, s3
            ELSE
                V_MOV v1, s4
            ENDIF
        ELSE
            V_MOV v1, s5
        ENDIF
        S_ENDPGM
    ''', sgprs={3: 1, 4: 2, 5: 3})
    assert st.v[1] == [1] * 4 + [2] * 4 + [3] * 8 and st.exec == ALL


def test_per_lane_loop_with_exec_mask():
    # each lane counts down from its lane id; lanes drop out of EXEC when done
    st, _, _, _ = run_src('''
        V_LANEID v0
        V_MOV v1, s0                   ; v1 = 0 (iterations done per lane)
        S_MOVK s1, 1
        S_MOV s20, exec
    loop:
        V_CMP_GT_I32 s10, v0, v2       ; still lanes with v0 > 0 ? (v2 = 0)
        S_AND exec, exec, s10
        S_CBRANCH_EXECZ done
        V_SUB_I32 v0, v0, v3           ; v3 = 1
        V_ADD_I32 v1, v1, v3
        S_BRANCH loop
    done:
        S_MOV exec, s20
        S_ENDPGM
    ''', vgprs={2: [0] * 16, 3: [1] * 16})
    assert st.v[1] == list(range(16))


def test_call_and_return():
    st, _, _, _ = run_src('''
        S_MOVK s1, 5
        S_CALL s30, double
        S_CALL s30, double
        S_ENDPGM
    double:
        S_ADD s1, s1, s1
        S_SETPC s30
    ''')
    assert st.s[1] == 20


def test_lane_operations():
    st, _, _, _ = run_src('''
        V_LANEID v0
        S_MOVK s1, 0x00F0
        V_CNDMASK v1, v0, v2, s1       ; lanes 4-7: v2 (=100), else lane id
        V_LSHL_OR v3, v0, v0, 4        ; (i << 4) | i
        S_MOVK exec, 0x0F00            ; lanes 8-11 active
        V_READFIRSTLANE s2, v0         ; lowest active lane: 8
        S_MOVK exec, -1                ; exec keeps only 16 bits
        S_ENDPGM
    ''', vgprs={2: [100] * 16})
    assert st.v[1] == [0, 1, 2, 3, 100, 100, 100, 100] + list(range(8, 16))
    assert st.v[3] == [(i << 4) | i for i in range(16)]
    assert st.s[2] == 8 and st.exec == ALL


def test_compare_inactive_lanes_give_zero():
    st, _, _, _ = run_src('''
        V_LANEID v0
        S_MOVK exec, 0x00FF
        V_CMP_GE_I32 s3, v0, v1        ; all active lanes true (v1 = 0)
        S_ENDPGM
    ''', vgprs={1: [0] * 16})
    assert st.s[3] == 0x00FF


def test_gouraud_pixel_shader_from_the_readme():
    # vertex records at 100, 110, 120: words 4-6 = r, g, b
    memory = [0] * 256
    colours = [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)]
    for base, (r, g, b) in zip((100, 110, 120), colours):
        memory[base + 4:base + 7] = [f32_bits(r), f32_bits(g), f32_bits(b)]
    rng = np.random.default_rng(3)
    bary = rng.dirichlet([1, 1, 1], size=16).astype(np.float32)
    vgprs = {2 + k: [f32_bits(bary[lane][k]) for lane in range(16)] for k in range(3)}
    _, _, exports, _ = run_src('''
    .entry ps_gouraud
        INTERP    v5, 4, v12
        INTERP    v6, 5, v12
        INTERP    v7, 6, v12
        S_MOV32   s12, 1.0
        V_MOV     v8, s12
        PACK_RGBA v9, v5, v6, v7, v8, v12
        V_EXPORT_COLOR v9
        S_ENDPGM
    ''', sgprs={0: 100, 1: 110, 2: 120, 31: 0x7FFF}, vgprs=vgprs, memory=memory)
    (mask, packed), = exports
    assert mask == 0x7FFF
    assert packed[15] == 0              # lane 15 inactive: register never written
    for lane in range(15):
        b0, b1, b2 = (float(x) for x in bary[lane])
        expected = [round(min(max(c, 0), 1) * 255) for c in (b0, b1, b2)]   # r, g, b
        argb = packed[lane]
        got = [(argb >> 16) & 0xFF, (argb >> 8) & 0xFF, argb & 0xFF]
        assert argb >> 24 == 255
        assert all(abs(g - e) <= 1 for g, e in zip(got, expected)), (lane, got, expected)


@pytest.mark.parametrize('source,message', [
    ('V_EXPORT_COLOR v0\nV_EXPORT_COLOR v0\nS_ENDPGM', 'only once'),
    ('S_NOP', 'outside instruction memory'),
    ('V_LOAD_BCAST v0, s1, 0\nS_ENDPGM', 'outside memory'),
    ('S_BRANCH -5\nS_ENDPGM', 'outside instruction memory'),
])
def test_undefined_behaviour_is_reported(source, message):
    with pytest.raises(VenusError, match=message):
        run_src(source, sgprs={1: 10_000})


def test_illegal_word_is_reported():
    st = GroupState(0, {31: ALL})
    with pytest.raises(VenusError, match='illegal'):
        run(st, [0x70000000], [0] * 4)

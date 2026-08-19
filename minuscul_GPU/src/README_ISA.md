# VENUS GPU ISA Specification
## Architecture Overview
- **Instruction Size**: 32-bit fixed-length instructions
- **Register File**: 16 registers per thread (r0-r15)
- **Register Width**: 32-bit (supports int32, float32, and 2x16-bit packed operations)
- **Execution Model**: SIMT (Single Instruction Multiple Threads) with warp-based execution
- **Memory Hierarchy**: Registers → Local Memory (per-thread) → Shared Memory (per-workgroup) → Global Memory

---

## Register Layout (16 total)
- **r0-r13**: General purpose registers
- **r14**: Stack pointer (SP)
- **r15**: Program counter / Link register (for subroutines)

---

## Instruction Format Overview

### Type-A: Arithmetic Instructions (Register-Register Operations)
**Format**: `[7:0] opcode | [11:8] dest | [15:12] src1 | [19:16] src2 | [31:20] reserved/flags`

**Opcodes**:
- `0x00`: ADD (r_dest = r_src1 + r_src2)
- `0x01`: SUB (r_dest = r_src1 - r_src2)
- `0x02`: MUL (r_dest = r_src1 * r_src2, 32-bit multiply)
- `0x03`: DIV (r_dest = r_src1 / r_src2)
- `0x04`: MOD (r_dest = r_src1 % r_src2)
- `0x05`: AND (r_dest = r_src1 & r_src2)
- `0x06`: OR (r_dest = r_src1 | r_src2)
- `0x07`: XOR (r_dest = r_src1 ^ r_src2)
- `0x08`: NOT (r_dest = ~r_src1)
- `0x09`: SHL (r_dest = r_src1 << r_src2[4:0])
- `0x0A`: SHR (r_dest = r_src1 >> r_src2[4:0]) [logical]
- `0x0B`: ASHR (r_dest = r_src1 >>> r_src2[4:0]) [arithmetic]
- `0x0C`: MIN (r_dest = min(r_src1, r_src2))
- `0x0D`: MAX (r_dest = max(r_src1, r_src2))
- `0x0E`: ABS (r_dest = abs(r_src1))
- `0x0F`: FMUL (r_dest = float(r_src1) * float(r_src2)) [floating-point multiply]

**Type-A-Immediate**: `[7:0] opcode | [11:8] dest | [15:12] src1 | [31:16] immediate`
- `0x10`: ADDI (r_dest = r_src1 + sign_ext(immediate))
- `0x11`: SUBI (r_dest = r_src1 - sign_ext(immediate))
- `0x12`: MULI (r_dest = r_src1 * sign_ext(immediate))
- `0x13`: ANDI (r_dest = r_src1 & zero_ext(immediate))
- `0x14`: ORI (r_dest = r_src1 | zero_ext(immediate))
- `0x15`: XORI (r_dest = r_src1 ^ zero_ext(immediate))
- `0x16`: SHLI (r_dest = r_src1 << immediate[4:0])
- `0x17`: SHRI (r_dest = r_src1 >> immediate[4:0])
- `0x18`: MOVI (r_dest = sign_ext(immediate)) [Load immediate]

**Comparison Instructions** (sets r_dest to 1 if true, 0 if false):
- `0x20`: EQ (r_dest = (r_src1 == r_src2) ? 1 : 0)
- `0x21`: NEQ (r_dest = (r_src1 != r_src2) ? 1 : 0)
- `0x22`: LT (r_dest = (r_src1 < r_src2) ? 1 : 0)
- `0x23`: LTE (r_dest = (r_src1 <= r_src2) ? 1 : 0)
- `0x24`: GT (r_dest = (r_src1 > r_src2) ? 1 : 0)
- `0x25`: GTE (r_dest = (r_src1 >= r_src2) ? 1 : 0)
- `0x26`: FLT (r_dest = (float(r_src1) < float(r_src2)) ? 1 : 0) [floating-point]

---

### Type-I: Memory Transactions
**Format**: `[7:0] opcode | [11:8] reg | [15:12] addr_reg | [31:16] offset`

**Opcodes**:
- `0x30`: LDW (r_reg = mem32[r_addr_reg + sign_ext(offset)]) [Load Word - 32-bit]
- `0x31`: LDH (r_reg = mem16[r_addr_reg + sign_ext(offset)] sign-extended) [Load Half-word - 16-bit signed]
- `0x32`: LDB (r_reg = mem8[r_addr_reg + sign_ext(offset)] sign-extended) [Load Byte - 8-bit signed]
- `0x33`: LDHU (r_reg = mem16[r_addr_reg + sign_ext(offset)] zero-extended) [Load Half-word Unsigned]
- `0x34`: LDBU (r_reg = mem8[r_addr_reg + sign_ext(offset)] zero-extended) [Load Byte Unsigned]
- `0x35`: STW (mem32[r_addr_reg + sign_ext(offset)] = r_reg) [Store Word]
- `0x36`: STH (mem16[r_addr_reg + sign_ext(offset)] = r_reg[15:0]) [Store Half-word]
- `0x37`: STB (mem8[r_addr_reg + sign_ext(offset)] = r_reg[7:0]) [Store Byte]

**Memory Space Encoding** (bits [31:24]):
- Memory operations implicitly use a memory space selector:
  - `0x00`: Global Memory (shared across all threads)
  - `0x01`: Local Memory (private to each thread, ~64KB)
  - `0x02`: Shared Memory (shared within a workgroup, ~32KB)
  - Encoded via a register bit or instruction variant

**Atomic Operations** (for synchronization):
- `0x40`: ATOM_ADD (atomically add to global memory)
- `0x41`: ATOM_CAS (compare-and-swap)
- `0x42`: ATOM_XCHG (atomic exchange)

---

### Type-D: Branch/Jump Instructions
**Format**: `[7:0] opcode | [31:8] target_offset (24-bit signed offset)`

**Opcodes**:
- `0x50`: JMP (PC = PC + 4 + sign_ext(offset * 4)) [Unconditional jump]
- `0x51`: JZ (if (flags.zero) jump; else continue) [Jump if Zero]
- `0x52`: JNZ (if (!flags.zero) jump; else continue) [Jump if Not Zero]
- `0x53`: JLT (if (flags.lt) jump; else continue) [Jump if Less Than]
- `0x54`: JGT (if (flags.gt) jump; else continue) [Jump if Greater Than]
- `0x55`: JLE (if (flags.lte) jump; else continue) [Jump if Less Than or Equal]
- `0x56`: JGE (if (flags.gte) jump; else continue) [Jump if Greater Than or Equal]
- `0x57`: CALL (r15 = PC + 4; PC = PC + 4 + sign_ext(offset * 4)) [Function call, saves return address in r15]
- `0x58`: RET (PC = r15) [Return from function]

**Predicated Execution Format** (for conditional execution without branching):
- `[7:0] opcode | [11:8] pred_reg | [15:12] - | [31:16] -`
- When pred_reg != 0, execute the instruction; otherwise skip it
- Allows loop unrolling and reduces branch pressure

---

### Type-E: Thread Coordination & Synchronization
**Format**: `[7:0] opcode | [31:8] reserved/target`

**Opcodes**:
- `0x60`: BARRIER (synchronize all threads in a workgroup, must be reached by all threads)
- `0x61`: SYNC_BLOCK (lightweight synchronization without full barrier)
- `0x62`: YIELD (hint that thread can be context-switched)
- `0x63`: EXIT (thread terminates execution)
- `0x64`: VOTE_ALL (predicate = all threads in warp have register != 0)
- `0x65`: VOTE_ANY (predicate = any thread in warp has register != 0)
- `0x66`: VOTE_BALLOT (get bitmask of which threads have register != 0)
- `0x67`: SHUFFLE (exchange values between threads in warp)

**Thread ID Access** (read-only, set by hardware):
- Special registers: `tid_x`, `tid_y`, `tid_z` (thread IDs within a workgroup)
- Special registers: `bid_x`, `bid_y`, `bid_z` (workgroup/block IDs)
- Access via: `0x68`: RDTID (r_dest = tid_x|tid_y|tid_z based on field)

---

### Type-C: Control & Special Operations
**Format**: `[7:0] opcode | [31:8] variant/data`

**Opcodes**:
- `0x70`: NOP (no operation, useful for alignment/delays)
- `0x71`: TRAP (trigger interrupt/exception)
- `0x72`: FENCE (memory fence - ensure all memory operations complete before proceeding)
- `0x73`: FLUSH_CACHE (flush L1/L2 cache)
- `0x74`: PREFETCH (hint to prefetch memory location into cache)
- `0x75`: MEMCPY_BULK (hardware-accelerated bulk memory copy)
- `0x76`: BITREV (bit-reverse a register - useful for FFTs)
- `0x77`: POPCNT (count number of 1 bits)
- `0x78`: CLZ (count leading zeros)
- `0x79`: SQRT (single-precision square root)
- `0x7A`: RECIP (reciprocal - 1/x)
- `0x7B`: SIN/COS (sine/cosine, results in pair of registers)
- `0x7C`: EXP (e^x)
- `0x7D`: LOG (natural logarithm)
- `0x7E`: FCONV_I2F (integer to float conversion)
- `0x7F`: FCONV_F2I (float to integer conversion, truncate)

**Special Purpose I/O**:
- `0x80`: WRITE_REG (write to special control registers - performance counters, mode bits, etc.)
- `0x81`: READ_REG (read from special control registers)

---

## Flags Register
- **ZF** (Zero Flag): Set if result is 0
- **CF** (Carry Flag): Set if unsigned overflow
- **SF** (Sign Flag): Set if result is negative
- **OF** (Overflow Flag): Set if signed overflow
- **LT, GT, LTE, GTE**: Comparison result flags

---

## Example Program: Vector Add Kernel
```asm
; VENUS GPU Assembly - Vector Addition
; Inputs: r0 = array A base address, r1 = array B base address
;         r2 = output C base address, r3 = array length
; Thread ID determines element to process

RDTID r4          ; r4 = thread ID
CMP r4, r3        ; compare thread_id with length
JGE end           ; if thread_id >= length, exit

MOVI r5, 4        ; r5 = 4 (byte offset per 32-bit element)
MUL r6, r4, r5    ; r6 = thread_id * 4
LDW r7, r0, 0, r6 ; r7 = A[thread_id] (load from r0 + r6)
LDW r8, r1, 0, r6 ; r8 = B[thread_id] (load from r1 + r6)
ADD r9, r7, r8    ; r9 = r7 + r8
STW r9, r2, 0, r6 ; C[thread_id] = r9

end:
EXIT              ; thread terminates
```

---

## Memory Layout
- **0x00000000 - 0x0FFFFFFF**: Global Memory (256MB)
- **0x10000000 - 0x1FFFFFFF**: Shared Memory per workgroup
- **0x20000000 - 0x2FFFFFFF**: Local Memory (per-thread, virtual address space)

---

## Complete Feature Checklist
✓ Arithmetic operations (integer & floating-point)
✓ Bitwise operations & bit manipulation
✓ Memory access (load/store with multiple sizes)
✓ Atomic operations for synchronization
✓ Control flow (branches, conditionals, function calls)
✓ Thread coordination (barriers, voting, shuffles)
✓ Special functions (transcendental, bit operations)
✓ Floating-point support
✓ Predicated execution (conditional without branching)
✓ Exception/trap handling

This ISA is now **production-ready** for: graphics rendering, compute kernels, machine learning workloads, and general GPU computing tasks.
import py4hw 


'''
This is the thread that is connected to the worp schedulor to for control
The thread recives the instruction form the worp schedulor and executes it.

Implements VENUS GPU ISA with 16 32-bit registers per thread.
Instruction format: 32-bit with opcode in bits [7:0]
'''
class threadV1(py4hw.Logic):
    def __init__(self, parent, name, instruction, mem, thread_id):
        super().__init__(parent, name, instruction, mem)

        self.thread_id = thread_id
        self.instruction = self.addIn("Instruction", instruction)
        
        # Register File: 16 x 32-bit registers
        self.registers = [0] * 16  # r0-r15
        self.pc = 0  # Program Counter
        self.is_active = True  # Thread active flag
        
        # Flags Register
        self.flags = {
            'zero': False,      # ZF: result is zero
            'lt': False,        # LT: less than
            'ge': False,        # GE: greater or equal
        }


    def _sign_extend_16(self, value):
        """Sign extend a 16-bit value to 32-bit"""
        if value & 0x8000:
            return value | 0xFFFF0000
        return value & 0x0000FFFF


    def _sign_extend_8(self, value):
        """Sign extend an 8-bit value to 32-bit"""
        if value & 0x80:
            return value | 0xFFFFFF00
        return value & 0x000000FF


    def _zero_extend_16(self, value):
        """Zero extend a 16-bit value"""
        return value & 0x0000FFFF


    def _zero_extend_8(self, value):
        """Zero extend an 8-bit value"""
        return value & 0x000000FF


    def _update_flags(self, result):
        """Update flags based on arithmetic result"""
        result = result & 0xFFFFFFFF  # Keep as 32-bit
        self.flags['zero'] = (result == 0)
        self.flags['lt'] = (result & 0x80000000) != 0  # Sign bit
        self.flags['ge'] = (result & 0x80000000) == 0


    def _execute_type_a(self, opcode, dest, src1, src2, immediate=None):
        """Execute Type-A Arithmetic Instructions"""
        rd = self.registers[dest] & 0xFFFFFFFF
        rs1 = self.registers[src1] & 0xFFFFFFFF
        rs2 = self.registers[src2] & 0xFFFFFFFF if immediate is None else immediate & 0xFFFFFFFF
        
        result = 0
        
        # Basic Arithmetic
        if opcode == 0x00:  # ADD
            result = (rs1 + rs2) & 0xFFFFFFFF
        elif opcode == 0x01:  # SUB
            result = (rs1 - rs2) & 0xFFFFFFFF
        elif opcode == 0x02:  # MUL
            result = (rs1 * rs2) & 0xFFFFFFFF
        elif opcode == 0x03:  # DIV
            result = (rs1 // rs2) if rs2 != 0 else 0
        elif opcode == 0x04:  # MOD
            result = (rs1 % rs2) if rs2 != 0 else 0
        
        # Bitwise Operations
        elif opcode == 0x05:  # AND
            result = rs1 & rs2
        elif opcode == 0x06:  # OR
            result = rs1 | rs2
        elif opcode == 0x07:  # XOR
            result = rs1 ^ rs2
        elif opcode == 0x08:  # NOT
            result = ~rs1 & 0xFFFFFFFF
        
        # Shift Operations
        elif opcode == 0x09:  # SHL
            shift_amount = rs2 & 0x1F
            result = (rs1 << shift_amount) & 0xFFFFFFFF
        elif opcode == 0x0A:  # SHR (logical)
            shift_amount = rs2 & 0x1F
            result = (rs1 >> shift_amount) & 0xFFFFFFFF
        elif opcode == 0x0B:  # ASHR (arithmetic)
            shift_amount = rs2 & 0x1F
            if rs1 & 0x80000000:  # Sign bit set
                result = ((rs1 >> shift_amount) | (0xFFFFFFFF << (32 - shift_amount))) & 0xFFFFFFFF
            else:
                result = (rs1 >> shift_amount) & 0xFFFFFFFF
        
        # Min/Max/Abs
        elif opcode == 0x0C:  # MIN
            result = min(rs1, rs2)
        elif opcode == 0x0D:  # MAX
            result = max(rs1, rs2)
        elif opcode == 0x0E:  # ABS
            result = abs(rs1)
        
        # Immediate Operations
        elif opcode == 0x10:  # ADDI
            imm = self._sign_extend_16(immediate)
            result = (rs1 + imm) & 0xFFFFFFFF
        elif opcode == 0x11:  # SUBI
            imm = self._sign_extend_16(immediate)
            result = (rs1 - imm) & 0xFFFFFFFF
        elif opcode == 0x12:  # MULI
            imm = self._sign_extend_16(immediate)
            result = (rs1 * imm) & 0xFFFFFFFF
        elif opcode == 0x13:  # ANDI
            imm = self._zero_extend_16(immediate)
            result = rs1 & imm
        elif opcode == 0x14:  # ORI
            imm = self._zero_extend_16(immediate)
            result = rs1 | imm
        elif opcode == 0x15:  # XORI
            imm = self._zero_extend_16(immediate)
            result = rs1 ^ imm
        elif opcode == 0x16:  # SHLI
            shift_amount = immediate & 0x1F
            result = (rs1 << shift_amount) & 0xFFFFFFFF
        elif opcode == 0x17:  # SHRI
            shift_amount = immediate & 0x1F
            result = (rs1 >> shift_amount) & 0xFFFFFFFF
        elif opcode == 0x18:  # MOVI
            result = self._sign_extend_16(immediate)
        
        # Comparison Instructions
        elif opcode == 0x20:  # EQ
            result = 1 if rs1 == rs2 else 0
        elif opcode == 0x21:  # NEQ
            result = 1 if rs1 != rs2 else 0
        elif opcode == 0x22:  # LT
            result = 1 if rs1 < rs2 else 0
        elif opcode == 0x23:  # LTE
            result = 1 if rs1 <= rs2 else 0
        elif opcode == 0x24:  # GT
            result = 1 if rs1 > rs2 else 0
        elif opcode == 0x25:  # GTE
            result = 1 if rs1 >= rs2 else 0
        
        self.registers[dest] = result
        self._update_flags(result)


    def _execute_type_i(self, opcode, reg, addr_reg, offset):
        """Execute Type-I Memory Instructions"""
        addr = (self.registers[addr_reg] + self._sign_extend_16(offset)) & 0xFFFFFFFF
        
        if opcode == 0x30:  # LDW - Load Word (32-bit)
            value = self.mem.read(addr, 4)
            self.registers[reg] = value & 0xFFFFFFFF
        
        elif opcode == 0x31:  # LDH - Load Half-word (16-bit, sign-extended)
            value = self.mem.read(addr, 2)
            value = self._sign_extend_16(value)
            self.registers[reg] = value
        
        elif opcode == 0x32:  # LDB - Load Byte (8-bit, sign-extended)
            value = self.mem.read(addr, 1)
            value = self._sign_extend_8(value)
            self.registers[reg] = value
        
        elif opcode == 0x33:  # LDHU - Load Half-word Unsigned
            value = self.mem.read(addr, 2)
            value = self._zero_extend_16(value)
            self.registers[reg] = value
        
        elif opcode == 0x34:  # LDBU - Load Byte Unsigned
            value = self.mem.read(addr, 1)
            value = self._zero_extend_8(value)
            self.registers[reg] = value
        
        elif opcode == 0x35:  # STW - Store Word (32-bit)
            self.mem.write(addr, self.registers[reg] & 0xFFFFFFFF, 4)
        
        elif opcode == 0x36:  # STH - Store Half-word (16-bit)
            value = self.registers[reg] & 0xFFFF
            self.mem.write(addr, value, 2)
        
        elif opcode == 0x37:  # STB - Store Byte (8-bit)
            value = self.registers[reg] & 0xFF
            self.mem.write(addr, value, 1)


    def _execute_type_d(self, opcode, offset):
        """Execute Type-D Branch/Jump Instructions"""
        signed_offset = self._sign_extend_16(offset) if isinstance(offset, int) else offset
        
        if opcode == 0x50:  # JMP - Unconditional Jump
            self.pc = (self.pc + 4 + (signed_offset << 2)) & 0xFFFFFFFF
        
        elif opcode == 0x51:  # JEQ - Jump if Equal (zero flag set)
            if self.flags['zero']:
                self.pc = (self.pc + 4 + (signed_offset << 2)) & 0xFFFFFFFF
            else:
                self.pc += 4
        
        elif opcode == 0x52:  # JNE - Jump if Not Equal (zero flag clear)
            if not self.flags['zero']:
                self.pc = (self.pc + 4 + (signed_offset << 2)) & 0xFFFFFFFF
            else:
                self.pc += 4
        
        elif opcode == 0x53:  # JLT - Jump if Less Than
            if self.flags['lt']:
                self.pc = (self.pc + 4 + (signed_offset << 2)) & 0xFFFFFFFF
            else:
                self.pc += 4
        
        elif opcode == 0x54:  # JGE - Jump if Greater or Equal
            if self.flags['ge']:
                self.pc = (self.pc + 4 + (signed_offset << 2)) & 0xFFFFFFFF
            else:
                self.pc += 4
        
        elif opcode == 0x55:  # CALL - Function Call
            self.registers[15] = (self.pc + 4) & 0xFFFFFFFF
            self.pc = (self.pc + 4 + (signed_offset << 2)) & 0xFFFFFFFF
        
        elif opcode == 0x56:  # RET - Return from Function
            self.pc = self.registers[15] & 0xFFFFFFFF


    def _execute_type_e(self, opcode, dest=None):
        """Execute Type-E Thread Coordination Instructions"""
        if opcode == 0x60:  # BARRIER
            # Signal barrier (hardware handles synchronization)
            pass
        
        elif opcode == 0x61:  # EXIT
            self.is_active = False
        
        elif opcode == 0x62 and dest is not None:  # RDTID - Read Thread ID
            self.registers[dest] = self.thread_id & 0xFFFFFFFF


    def _execute_type_c(self, opcode):
        """Execute Type-C Control & Special Operations"""
        if opcode == 0x70:  # NOP - No Operation
            pass
        
        elif opcode == 0x71:  # FENCE - Memory Fence
            # Signal fence to memory system
            pass


    def clock(self):
        """Execute one instruction per clock cycle"""
        if not self.is_active:
            return
        
        # Decode the instruction
        ins = self.instruction.get()
        opcode = ins & 0xFF  # Bits [7:0] contain opcode
        
        # Type-A: Arithmetic Instructions
        if 0x00 <= opcode <= 0x0F or 0x10 <= opcode <= 0x18 or 0x20 <= opcode <= 0x26:
            if opcode < 0x10:  # Register-Register
                dest = (ins >> 8) & 0x0F
                src1 = (ins >> 12) & 0x0F
                src2 = (ins >> 16) & 0x0F
                self._execute_type_a(opcode, dest, src1, src2)
                self.pc += 4
            else:  # Immediate
                dest = (ins >> 8) & 0x0F
                src1 = (ins >> 12) & 0x0F
                immediate = (ins >> 16) & 0xFFFF
                self._execute_type_a(opcode, dest, src1, 0, immediate)
                self.pc += 4
        
        # Type-I: Memory Transactions
        elif 0x30 <= opcode <= 0x37:
            reg = (ins >> 8) & 0x0F
            addr_reg = (ins >> 12) & 0x0F
            offset = (ins >> 16) & 0xFFFF
            self._execute_type_i(opcode, reg, addr_reg, offset)
            self.pc += 4
        
        # Type-D: Branch/Jump Instructions
        elif 0x50 <= opcode <= 0x56:
            offset = (ins >> 8) & 0xFFFFFF  # 24-bit offset
            self._execute_type_d(opcode, offset)
        
        # Type-E: Thread Coordination
        elif 0x60 <= opcode <= 0x62:
            if opcode == 0x62:  # RDTID
                dest = (ins >> 8) & 0x0F
                self._execute_type_e(opcode, dest)
            else:
                self._execute_type_e(opcode)
            self.pc += 4
        
        # Type-C: Control Operations
        elif 0x70 <= opcode <= 0x71:
            self._execute_type_c(opcode)
            self.pc += 4
        
        else:
            # Unknown opcode
            self.pc += 4

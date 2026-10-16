import py4hw

"""
Triangles are stored one per 512-bit memory line: V1X V1Y V1Z V2X V2Y V2Z
V3X V3Y V3Z COLOR, each a 32-bit two's-complement field (see
pack_triangle_line/unpack_triangle_line in rasteriser/renderer.py).
"""

class MemoryInterface(py4hw.Interface):
    def __init__(self,parent,name,memory_size: int,data_width: int = 32):
        super().__init__(parent, name)

        self.read = self.addSourceToSink('read',1)
        self.write = self.addSourceToSink('write',1)
        self.address = self.addSourceToSink('address',32)
        self.read_data = self.addSinkToSource('read_data',data_width)
        self.write_data = self.addSourceToSink('write_data',data_width)
        self.done = self.addSourceToSink('done',1)


class TrinagleInfo(py4hw.Interface):
    def __init__(self, parent, name):
        super().__init__(parent, name)

        self.V1X = self.addSourceToSink('V1X',32)
        self.V1Y = self.addSourceToSink('V1Y',32)
        self.V1Z = self.addSourceToSink('V1Z',32)

        self.V2X = self.addSourceToSink('V2X',32)
        self.V2Y = self.addSourceToSink('V2Y',32)
        self.V2Z = self.addSourceToSink('V2Z',32)

        self.V3X = self.addSourceToSink('V3X',32)
        self.V3Y = self.addSourceToSink('V3Y',32)
        self.V3Z = self.addSourceToSink('V3Z',32)



class MatrixInfo4X4(py4hw.Interface):
    def __init__(self,parent,name):
        super().__init__(parent,name)

        self.V00 = self.addSourceToSink('V00',32)
        self.V01 = self.addSourceToSink('V01',32)
        self.V02 = self.addSourceToSink('V02',32)
        self.V03 = self.addSourceToSink('V03',32)
        self.V10 = self.addSourceToSink('V10',32)
        self.V11 = self.addSourceToSink('V11',32)
        self.V12 = self.addSourceToSink('V12',32)
        self.V13 = self.addSourceToSink('V13',32)
        self.V20 = self.addSourceToSink('V20',32)
        self.V21 = self.addSourceToSink('V21',32)
        self.V22 = self.addSourceToSink('V22',32)
        self.V23 = self.addSourceToSink('V23',32)
        self.V30 = self.addSourceToSink('V30',32)
        self.V31 = self.addSourceToSink('V31',32)
        self.V32 = self.addSourceToSink('V32',32)
        self.V33 = self.addSourceToSink('V33',32)
        

class Memory(py4hw.Logic):
    def __init__(self,parent,name,mem:MemoryInterface,memory_size:int):
        super().__init__(parent, name)
        self.mem =  self.addInterfaceSink('mem',mem)
        self.memory = [0]*memory_size

    def clock(self):
        address =  self.mem.address.get()
        #read
        if (self.mem.read.get() == 1) and (self.mem.write.get() == 0):
            self.mem.read_data.prepare(self.memory[address])
            self.mem.done.prepare(1)
        elif (self.mem.read.get() == 0) and (self.mem.write.get() == 1 ):
            val  = self.mem.write_data.get()
            self.memory[address] = val
            self.mem.done.prepare(1)
        else:
            self.mem.done.prepare(0)



class registerFile(py4hw.Logic):
    def __init__(self, parent, name,rr,rd,inp,out):
        super().__init__(parent, name)

        self.addIn('Rr',rr)
        self.addIn('Rd',rd)
        self.addIn('input',inp)
        self.addOut('Output',out)

        reg = [0] * 32


    def clock(self):
        reg[0] = reg[0]
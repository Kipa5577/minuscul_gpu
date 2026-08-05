import py4hw 
from ..memory.memory import TrinagleInfo

'''
This component takes a position on the screen and a triangle and tells if the pixel is in the triangle or not


STATES:

0 = FETCH_TRIANGLE_VALUES
1 = COMPUTE_OUTPUT
2 = WRITE_OUTPUT_TO_BUFFER

'''

class RenderModule(py4hw.Logic):
    def __init__(self,parent,name,memory_BUFFER,TRIANGLE_ADDRESS,IMAGE_POSITION_X,IMAGE_POSITION_Y,DONE):
        super().__init__(self,parent)

        self.triangle = self.addIn("TRIANGLE_ADDRESS",TRIANGLE_ADDRESS)
        self.window_origin_x = self.addIn("IMAGE_POSITION_X",IMAGE_POSITION_X)
        self.window_origin_y = self.addIn("IMAGE_POSITON_Y",IMAGE_POSITION_Y)

        self.state = 0

        #Triangle vertecies 
        self.v1X = 0
        self.v1Y = 0 
        self.v1Z = 0

        self.v2X = 0
        self.v2Y = 0
        self.v2Z = 0

        self.v3X = 0
        self.v3Y = 0
        self.v3Z = 0 

    def clock(self):
        tri_add = self.triangle.get()
        origin_x = self.window_origin_x.get()
        origin_y = self.window_origin_y.get()

        match self.state:
            case 0:
                #self.v1X = yield
                #self.v1Y = yield
                #self.v1Z = yield
                break

            case 1:
                break                

            case 2:
                break


class BoundingBoxCalculator(py4hw.Logic):
    def __init__(self, parent, name,triangle:TrinagleInfo,Xmin,Xmax,Ymin,Ymax,Zmin,Zmax):
        super().__init__(parent, name)

        self.Triangle = self.addInterfaceSink('triangle',triangle)
        self.Xmin = self.addOut('Xmin',Xmin)
        self.Xmax = self.addOut('Xmax',Xmax)
        self.Ymin = self.addOut('Ymin',Ymin)
        self.Ymax = self.addOut('Ymax',Ymax)
        self.Zmin = self.addOut('Zmin',Zmin)
        self.Zmax = self.addOut('Zmax',Zmax)


    def propagate(self):

        self.Xmin.put(min(self.Triangle.V1X.get(),self.Triangle.V2X.get(),self.Triangle.V3X.get()))
        self.Xmax.put(max(self.Triangle.V1X.get(),self.Triangle.V2X.get(),self.Triangle.V3X.get()))

        self.Ymin.put(min(self.Triangle.V1Y.get(),self.Triangle.V2Y.get(),self.Triangle.V3Y.get()))
        self.Ymax.put(max(self.Triangle.V1Y.get(),self.Triangle.V2Y.get(),self.Triangle.V3Y.get()))

        self.Zmin.put(min(self.Triangle.V1Z.get(),self.Triangle.V2Z.get(),self.Triangle.V3Z.get()))
        self.Zmax.put(max(self.Triangle.V1Z.get(),self.Triangle.V2Z.get(),self.Triangle.V3Z.get()))


class Sign(py4hw.Logic):
    def __init__(self, parent, name,X1,Y1,Z1,X2,Y2,Z2,X3,Y3,Z3,output):
        super().__init__(parent, name)

        self.X1 = self.addIn('X1',X1)
        self.Y1 = self.addIn('Y1',Y1)
        self.Z1 = self.addIn('Z1',Z1)

        self.X2 = self.addIn('X2',X2)
        self.Y2 = self.addIn('Y2',Y2)
        self.Z2 = self.addIn('Z2',Z2)

        self.X3 = self.addIn('X3',X3)
        self.Y3 = self.addIn('Y3',Y3)
        self.Z3 = self.addIn('Z3',Z3)

        self.out = self.addOut('out',output)

    def clock(self):

        d1X = self.X1.get() - self.X3.get() 
        d1Y = self.Y2.get() - self.Y3.get()
        d2X = self.X2.get() - self.X3.get()
        d2Y = self.Y1.get() - self.Y3.get()

        val = d1X*d1Y - d2X*d2Y
        self.out.prepare(val)


class pixelInTriangle(py4hw.Logic):
    def __init__(self,parent,name,x,y,z,trig:TrinagleInfo,res):
        super().__init__(self,parent,name)

        self.x = self.addIn('X',x)
        self.y = self.addIn('Y',y)
        self.z = self.addIn('Z',z)

        self.trig = self.addInterfaceSink('trig',trig)
        self.d1 = self.wire("d1",32)
        self.d2 = self.wire("d2",32)
        self.d3 = self.wire("d3",32)

        Sign(self.x,self.y,self.z,self.trig.V1X.get(),self.trig.V1Y.get(),self.trig.V1Z.get(),self.trig.V2X.get(),self.trig.V2Y.get(),self.trig.V2Z.get(),self.d1)
        Sign(self.x,self.y,self.z,self.trig.V2X.get(),self.trig.V2Y.get(),self.trig.V2Z.get(),self.trig.V3X.get(),self.trig.V3Y.get(),self.trig.V3Z.get(),self.d2)
        Sign(self.x,self.y,self.z,self.trig.V3X.get(),self.trig.V3Y.get(),self.trig.V3Z.get(),self.trig.V2X.get(),self.trig.V2Y.get(),self.trig.V2Z.get(),self.d3)

        self.const = self.wire("const",1)
        self.const.put(0)

        self.comp1 = self.wire("comp1",1)
        self.comp2 = self.wire("comp2",1)
        self.comp3 = self.wire("comp3",1)
        self.comp4 = self.wire("comp4",1)
        self.comp5 = self.wire("comp5",1)
        self.comp6 = self.wire("comp6",1)

        py4hw.Comparator(self,"Comp1",self.d1,self.const,self.comp4,None,self.comp1)
        py4hw.Comparator(self,"Comp2",self.d2,self.const,self.comp5,None,self.comp2)
        py4hw.Comparator(self,"Comp3",self.d3,self.const,self.comp6,None,self.comp3)

        self.has_neg = self.wire("has_neg",1)
        self.has_pos = self.wire("has_pos",1)

        py4hw.Or(self,"or1",[self.comp1,self.comp2,self.comp3],self.has_neg)
        py4hw.Or(self,"or2",[self.comp4,self.comp5,self.comp6],self.has_pos)

        res_wire = self.wire("res_wire",1)
        py4hw.And2(self,"and1",self.has_neg,self.has_pos,res_wire)

        py4hw.Not2(self,"not1",res_wire,res)


class MatixMultiplier4x4(py4hw.Logic):
    def __init__(self,parent,name,matrixA:MatrixInfo4X4,matrixB:MatrixInfo4X4,matrixR:MatrixInfo4x4):
        self.A = self.addInterfaceSink('A',matrixA)
        self.B = self.addInterfaceSink('B',matrixB)

        self.R = self.addInterfaceSource('R',matrixR)


    def clock(self):
        #Matrix Multiplication between A and B to get R
        V00 = self.A.V00.get()*self.B.V00.get() + self.A.V01.get()*self.B.V10.get() + self.A.V02.get()*self.B.V20.get() + self.A.V03.get()*self.B.V30.get()
        V01 = self.A.V00.get()*self.B.V01.get() + self.A.V01.get()*self.B.V11.get() + self.A.V02.get()*self.B.V21.get() + self.A.V03.get()*self.B.V31.get()
        V02 = self.A.V00.get()*self.B.V02.get() + self.A.V01.get()*self.B.V12.get() + self.A.V02.get()*self.B.V22.get() + self.A.V03.get()*self.B.V32.get()
        V03 = self.A.V00.get()*self.B.V03.get() + self.A.V01.get()*self.B.V13.get() + self.A.V02.get()*self.B.V23.get() + self.A.V03.get()*self.B.V33.get()
        V10 = self.A.V10.get()*self.B.V00.get() + self.A.V11.get()*self.B.V10.get() + self.A.V12.get()*self.B.V20.get() + self.A.V13.get()*self.B.V30.get()
        V11 = self.A.V10.get()*self.B.V01.get() + self.A.V11.get()*self.B.V11.get() + self.A.V12.get()*self.B.V21.get() + self.A.V13.get()*self.B.V31.get()
        V12 = self.A.V10.get()*self.B.V02.get() + self.A.V11.get()*self.B.V12.get() + self.A.V12.get()*self.B.V22.get() + self.A.V13.get()*self.B.V32.get()
        V13 = self.A.V10.get()*self.B.V03.get() + self.A.V11.get()*self.B.V13.get() + self.A.V12.get()*self.B.V23.get() + self.A.V13.get()*self.B.V33.get()
        V20 = self.A.V20.get()*self.B.V00.get() + self.A.V21.get()*self.B.V10.get() + self.A.V22.get()*self.B.V20.get() + self.A.V23.get()*self.B.V30.get()
        V21 = self.A.V20.get()*self.B.V01.get() + self.A.V21.get()*self.B.V11.get() + self.A.V22.get()*self.B.V21.get() + self.A.V23.get()*self.B.V31.get()
        V22 = self.A.V20.get()*self.B.V02.get() + self.A.V21.get()*self.B.V12.get() + self.A.V22.get()*self.B.V22.get() + self.A.V23.get()*self.B.V32.get()
        V23 = self.A.V20.get()*self.B.V03.get() + self.A.V21.get()*self.B.V13.get() + self.A.V22.get()*self.B.V23.get() + self.A.V23.get()*self.B.V33.get()
        V30 = self.A.V30.get()*self.B.V00.get() + self.A.V31.get()*self.B.V10.get() + self.A.V32.get()*self.B.V20.get() + self.A.V33.get()*self.B.V30.get()
        V31 = self.A.V30.get()*self.B.V01.get() + self.A.V31.get()*self.B.V11.get() + self.A.V32.get()*self.B.V21.get() + self.A.V33.get()*self.B.V31.get()
        V32 = self.A.V30.get()*self.B.V02.get() + self.A.V31.get()*self.B.V12.get() + self.A.V32.get()*self.B.V22.get() + self.A.V33.get()*self.B.V32.get()
        V33 = self.A.V30.get()*self.B.V03.get() + self.A.V31.get()*self.B.V13.get() + self.A.V32.get()*self.B.V23.get() + self.A.V33.get()*self.B.V33.get()
    

        #Assigning values to the output matrix R
        self.R.V00.prepare(V00) 
        self.R.V01.prepare(V01)
        self.R.V02.prepare(V02)
        self.R.V03.prepare(V03)
        self.R.V10.prepare(V10)
        self.R.V11.prepare(V11)
        self.R.V12.prepare(V12)
        self.R.V13.prepare(V13)
        self.R.V20.prepare(V20)
        self.R.V21.prepare(V21)
        self.R.V22.prepare(V22)
        self.R.V23.prepare(V23)
        self.R.V30.prepare(V30)
        self.R.V31.prepare(V31)
        self.R.V32.prepare(V32)
        self.R.V33.prepare(V33)


        
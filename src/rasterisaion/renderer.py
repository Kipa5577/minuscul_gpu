import py4hw 
from ..memory.memory import TrinagleInfo
import math

'''
                
+--------------+      +-----------+
| RenderModule |------|Memory     |
|              |      +-----------+
|              |
|              |      +-----------+
|              |------|Screen     |
|              |      +-----------+
+--------------+

Parameters of the renderer:
Reset: To Reinitialise this component
First_Triangle_Address: To read the triangle points and color in memory 
Number_of_Triangles: To know when to stop
Camera_position: This is to know were to draw the image form 
Camera_Target: This is were the camera is looking 

Rotation_Matrix_X: This is to be reviewed because I would want the renderer to create a frame in functions of the parameters past in the entrence
Rotation_Matrix_Y: This is to be reviewed because I would want the renderer to create a frame in function of the  parameters passed to i
Screen: This is th inteface to communicate directly to the screen
Done: To signal to the screen that the image is done and to change the buffer that is displayed 

USED TO Know the size and the dimention of the screen to know how many rendering elements to instanciate
SCREEN_WIDTH:
SCREEN_HEIGHT: 


inside the renderer:


      +----------+
      |Controller|
      +----------+

These could be implemented autside the component maybe 
                   +---------------------+
Rotation_Matrix_X->|Matrix_Multiplication|
                   |                     |---->Model_Matrix--|
Rotation_Matrix_Y->|                     |                   |   
                   +---------------------+                   |                                      +---------------------+
                                                             |------------------------------------->|Matrix_Multiplication|
                   +---------------------+                                                          |                     |
Camera_pos-------->|Create_look_at_matrix|                                                          |                     |---->MVP_matrix
                   |                     |                                                    |---->|                     |
Camera_target----->|                     |---->view_matrix------|                             |     +---------------------+
                   |                     |                      |                             |
up_dir ----------->|                     |                      |   +---------------------+   |
                   +---------------------+                      |-->|Matrix_Multiplication|   |
                                                                    |                     |---|
                   +-------------------------+                      |                     |
fov_deg ---------->|Create_perspective_matrix|                  |-->|                     |
                   |                         |                  |   +---------------------+
aspect_ratio------>|                         |                  |  
                   |                         |---->porj_matrix--|
near-------------->|                         |
                   |                         |
far -------------->|                         |
                   +-------------------------+
In the for each triangle loop
               +---------------------+
Model_Matix--->|Matrix_Multiplication|
               |                     |-----> V1_World
TriangleV1---->|                     |
               +---------------------+
            
               +---------------------+
Model_Matrix-->|Matrix_Multiplication|
               |                     |-----> V1_World 
TriangleV2---->|                     |
               +---------------------+
               
               +---------------------+
Model _Matrix->|Matrix_Multiplication|
               |                     |-----> V1_Wold
TriangleV3---->|                     |
               +---------------------+

               +---------------------+
MVP_matrix---->|Matrix_Multiplication|
               |                     |---->p1
TriangleV1---->|                     |
               +---------------------+
               
               +---------------------+
MVP_matrix---->|Matrix_Multiplication|
               |                     |---->p2
TriangleV2---->|                     |
               +---------------------+
               
               +---------------------+
MVP_matrix---->|Matrix_Multiplication|
               |                     |---->p3
TriangleV3---->|                     |
               +---------------------+ 
'''     




'''
This component takes a position on the screen and a triangle and tells if the pixel is in the triangle or not


STATES:

0 = FETCH_TRIANGLE_VALUES
1 = COMPUTE_OUTPUT
2 = WRITE_OUTPUT_TO_BUFFER

'''

class RenderModule(py4hw.Logic):
    def __init__(self,parent,name,Triangle:MemoryInterface,Screen:MemoryInterface,DONE,FIRST_TRIANGLE_ADDRESS,NUMBER_OF_TRIANGLES,DONE):
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


class CrossProduct(py4hw.Logic):
    def __init__(self,parent,name,x1,y1,z1,x2,y2,z2,rx,ry,rz):
        super().__init__(self,parent,name)
        self.x1 = self.addIn('X1',x1)
        self.y1 = self.addIn('Y1',y1)
        self.z1 = self.addIn('Z1',z1)
        self.x2 = self.addIn('X2',x2)
        self.y2 = self.addIn('Y2',y2)
        self.z2 = self.addIn('Z2',z2)

        self.rx = self.addOut('RX',rx)
        self.ry = self.addOut('RY',ry)
        self.rz = self.addOut('RZ',rz)

    def propagate(self):
        #cross product formula
        self.rx.put(self.y1.get()*self.z2.get() - self.z1.get()*self.y2.get())
        self.ry.put(self.z1.get()*self.x2.get() - self.x1.get()*self.z2.get())
        self.rz.put(self.x1.get()*self.y2.get() - self.y1.get()*self.x2.get())



class Vector3Norm(py4hw.Logic):
    def __init__(self, parent, name,x,y,z,norm):
        super().__init__(parent, name,)

        self.x = self.addIn('X',x) 
        self.y = self.addIn('Y',y)
        self.z = self.addIn('Z',z)

        self.norm = self.addOut('norm',norm)

    def propagate(self):

        self.norm.put(math.sqrt((self.x.get()**2)+(self.y.get()**2)+(self.z.get()**2)))



class ClipToScreenConvertor(py4hw.Logic): # Converts clip space to screen space
    def __init__(self, parent, name,x,y,z,x_scr,y_scr,depth,SCREEN_WIDTH:int,SCREEN_HEIGHT:int):
        super().__init__(parent, name)

        self.x = self.addIn('x',x)
        self.y = self.addIn('y',y)
        self.z = self.addIn('z',z)

        self.x_scr = self.addIn('rx',x_scr)
        self.y_scr = self.addIn('ry',y_scr)
        self.depth = self.addIn('rz',depth)

    def propagate(self):
        self.x_src.put((((self.x.get()/self.z.get())*0.5)+0.5)*SCREEN_WIDTH)
        self.y_src.put((1.0 - ((self.x.get() / self.z.get())*0.5 +0.5)) * SCREEN_HEIGHT)
        self.depth.put(self.y.get()/self.z.get())




class PixelDrawerController(py4hw.Logic):
    def __init__(self,parent,name,triangle:TrinagleInfo,Triangle_color,SCREEN_WIDTH:int,SCREEN_HEIGHT:int):
        super().__init__(parent,name)

        self.triangle = self.addInterfaceSink('triangle',triangle)
        self.Triangle_color = self.addIn('Triangle_color',Triangle_color)

        #Bounding Box
        self.Xmin = self.wire('Xmin',32)
        self.Xmax = self.wire('Xmax',32)
        self.Ymin = self.wire('Ymin',32)
        self.Ymax = self.wire('Ymax',32)
        self.Zmin = self.wire('Zmin',32)
        self.Zmax = self.wire('Zmax',32)

        BoundingBoxCalculator(self,'BoundingBoxCalculator',self.triangle,self.Xmin,self.Xmax,self.Ymin,self.Ymax,self.Zmin,self.Zmax)



class PixelDrawer16x16(py4hw.Logic): 
    def __init__(self,parent,name,x_offset,y_offset,triangle:TriangleInfo,Triangle_color):
        super().__init__(parent,name):
        '''
        This component tests the corners of the drawing window to save on computation 
        '''
    
        PixelDraw4x4('Draw1',x_offset=x_offset,y_offset=y_offset,triangle=triangle,Triangle_color=Triangle_color)
        PixelDraw4x4('Draw2',x_offset=x_offset+4,y_offset=y_offset,triangle=triangle,Triangle_color=Triangle_color)
        PixelDraw4x4('Draw3',x_offset=x_offset,y_offset=y_offset+4,triangle=triangle,Triangle_color=Triangle_color)
        PixelDraw4x4('Draw4',x_offset=x_offset+4,y_offset=y_offset+4,triangle=triangle,Triangle_color=Trinagle_color)


        

class PixelDraw4x4(py4hw.Logic):
    def __init__(self, parent, name,x_offset,y_offset,triangle:TrinagleInfo,Triangle_color):
        super().__init__(parent, name)

        '''
        array of pixels chekers
        '''

        self.res = []
        for i in range(4*4):
            self.res[i] =  self.wire(f'Res{i}',1)
        
        pixelInTriangle(self,'Test1',x_offset,y_offset,0,triangle,self.res[0])
        pixelInTriangle(self,'Test2',x_offset+1,y_offset,0,triangle,self.res[1])
        pixelInTriangle(self,'Test3',x_offset,y_offset+1,0,triangle,self.res[2])
        pixelInTriangle(self,'Test4',x_offset+1,y_offset+1,0,triangle,self.res[3])
        pixelInTriangle(self,'Test5',x_offset+2,y_offset,0,triangle,self.res[4])
        pixelInTriangle(self,'Test6',x_offset+3,y_offset,0,triangle,self.res[5])
        pixelInTriangle(self,'Test7',x_offset+2,y_offset+1,0,triangle,self.res[6])
        pixelInTriangle(self,'Test8',x_offset+3,y_offset+1,0,triangle,self.res[7])
        pixelInTriangle(self,'Test9',x_offset,y_offset+2,0,triangle,self.res[8])
        pixelInTriangle(self,'Test10',x_offset+1,y_offset+2,0,triangle,self.res[9])
        pixelInTriangle(self,'Test11',x_offset,y_offset+3,0,triangle,self.res[10])
        pixelInTriangle(self,'Test12',x_offset+1,y_offset+3,0,triangle,self.res[11])
        pixelInTriangle(self,'Test13',x_offset+2,y_offset+2,0,triangle,self.res[12])
        pixelInTriangle(self,'Test14',x_offset+3,y_offset+2,0,triangle,self.res[13])
        pixelInTriangle(self,'Test15',x_offset+2,y_offset+3,0,triangle,self.res[14])
        pixelInTriangle(self,'Test16',x_offset+3,y_offset+3,0,triangle,self.res[15])
        
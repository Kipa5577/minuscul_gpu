import math
import random
from PIL import Image
import numpy as np

HEIGHT =1080
WIDTH = 1980




##################
# Camera functions
##################

def create_perspective_matrix(fov_deg, aspect_ratio,near,far):

     fov_rad = math.radians(fov_deg)
     f = 1.0 /math.tan(fov_rad/2.0)

     proj = np.zeros((4,4))
     proj[0,0] = f / aspect_ratio
     proj[1,1] = f 
     proj[2,2] = (far + near) / (near - far)
     proj[2,3] = (2.0 * far * near)/ (near - far)
     proj[3,2] = -1.0
     return proj

def create_look_at_matrix(eye,target,up):
    forward = target - eye
    forward = forward / np.linalg.norm(forward)

    right = np.cross(forward,up)
    right = right / np.linalg.norm(right)

    true_up = np.cross(right, forward)

    view = np.identity(4)
    view[0, :3] = right
    view[1, :3] = true_up 
    view[2, :3] = -forward
    view[0, 3] = -np.dot(right,eye)
    view[1, 3] = -np.dot(true_up,eye)
    view[2, 3] = np.dot(forward, eye)

    return view 

########
# 3D Objects
########


def build_piramid(center=(0,0,0),base_size=2.0,height=2.0,color=(255,140,0)):

    cx,cy,cz = center
    half =  base_size / 2.0

    # Define the 5 key verticies in 3d 
    v0 = np.array([cx - half, cy, cz - half,1.0])
    v1 = np.array([cx + half, cy, cz - half,1.0])
    v2 = np.array([cx + half, cy, cz + half,1.0])
    v3 = np.array([cx - half, cy, cz + half,1.0])

    apex = np.array([cx,height,cz,1.0])

    #triangles 
    triangles = []
    triangles.append((v0, v1, apex, color))
    triangles.append((v1, v2, apex, color))
    triangles.append((v2, v3, apex, color))
    triangles.append((v3, v0, apex, color))
    triangles.append((v0, v2, v1, color))
    triangles.append((v0, v3, v2, color))

    return triangles


def build_cube(center=(0,0,0), size=1.0, color=(0, 200, 255)):

    cx, cy, cz = center 
    s = size / 2.0

    #vertecies 

    v = [ 
        np.array([cx - s, cy - s, cz - s, 1.0]),
        np.array([cx + s, cy - s, cz - s, 1.0]),
        np.array([cx + s, cy + s, cz - s, 1.0]),
        np.array([cx - s, cy + s, cz - s, 1.0]),
        np.array([cx - s, cy - s, cz + s, 1.0]),
        np.array([cx + s, cy - s, cz + s, 1.0]),
        np.array([cx + s, cy + s, cz + s, 1.0]),
        np.array([cx - s, cy + s, cz + s, 1.0]),
    ]

    quads = [ 
        (0,1,2,3),
        (5,4,7,6),
        (4,0,3,7),
        (1,5,6,2),
        (4,5,1,0),
        (3,2,6,7),
    ]

    triangles = []

    for q in quads:
        triangles.append((v[q[0]], v[q[1]], v[q[2]], color))
        triangles.append((v[q[0]], v[q[2]], v[q[3]], color))

    return triangles
    

def create_rotation_y_matrix(angle_degrees):
    rad = math.radians(angle_degrees)
    cos_a,sin_a = math.cos(rad),math.sin(rad)
    return np.array([
        [cos_a, 0.0, sin_a, 0.0],
        [0.0, 1.0, 0.0, 0.0],
        [-sin_a, 0.0, cos_a, 0.0],
        [0.0, 0.0, 0.0, 1.0]
    ])

def create_rotation_x_matrix(angle_degrees):
    rad = math.radians(angle_degrees)
    cos_a, sin_a = math.cos(rad), math.sin(rad)
    return np.array([
        [1.0, 0.0, 0.0, 0.0],
        [0.0, cos_a, -sin_a, 0.0],
        [0.0, sin_a, cos_a, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ])


def random_Triangle_Generator(numberOfTriangles):
    trianglelist =[]
    for n in range(numberOfTriangles):
        triangle = []
        for p in range(3):
            point = []
            x = int(random.random()*WIDTH)
            y = int(random.random()*HEIGHT)
            z = int(random.random()*255)
            point.append(x)
            point.append(y)
            point.append(z)
            triangle.append(point)
        color = int(random.random()*255)
        triangle.append(color) # add a color 
        trianglelist.append(triangle)
    return trianglelist



def sign (point1,point2,point3):
    return ((point1[0]-point3[0])*(point2[1]-point3[1])) - ((point2[0]-point3[0])*(point1[1]-point3[1]))

def pixelContainedIn2DTriangle(pt,v1,v2,v3):
    d1 = sign(pt,v1,v2)
    d2 = sign(pt,v2,v3)
    d3 = sign(pt,v3,v1)
    has_neg = (d1<0)or(d2<0)or(d3<0)
    has_pos = (d1>0)or(d2>0)or(d3>0)
    return not(has_neg and has_pos)



def render_rotating_animation():

    camera_pos = np.array([0.0,2.0,5.0])
    camera_target = np.array([0.0,0.0,0.0])
    up_dir = np.array([0.0,1.0,0.0])

    light_dir = np.array([0.5,1.0,0.7])
    light_dir = light_dir / np.linalg.norm(light_dir)

    tri = build_piramid(base_size=2.0,height=3)

    frames = []
    num_frames = 36
    
    print('Rendering animation frames...')
    for frame_idx in range(num_frames):
        angle = (360/ num_frames) * frame_idx
        img = render_frame(angle, camera_pos, camera_target, up_dir,light_dir,tri)
        frames.append(img)
        print(f"Frame {frame_idx + 1}/{num_frames} done (angle:{int(angle)})")

    # Save as animated GIF
    frames[0].save(
        'rotation.gif',
        save_all=True,
        append_images=frames[1:],
        duration=50,
        loop=0
    )
    print("Animation saved successfully to rotation.gif!")

def render_frame(rotation_angle, camera_pos, camera_target, up_dir,light_dir,triangles):
    pixels = np.zeros((HEIGHT,WIDTH,3),dtype=np.uint8)
    z_buffer = np.full((HEIGHT,WIDTH),np.inf)
    model_matrix = create_rotation_y_matrix(rotation_angle)
    #model_matrix = create_rotation_y_matrix(rotation_angle) @ create_rotation_x_matrix(rotation_angle*0.5)
    view_matrix = create_look_at_matrix(camera_pos,camera_target,up_dir)
    proj_matrix = create_perspective_matrix(60.0,WIDTH/HEIGHT,0.1,100.0)
    mvp = proj_matrix @ view_matrix @ model_matrix

    for triangle in triangles:

        v0_world = model_matrix @ triangle[0]
        v1_world = model_matrix @ triangle[1]
        v2_world = model_matrix @ triangle[2]

        edge1 = v1_world[:3] - v0_world[:3]
        edge2 = v2_world[:3] - v0_world[:3]
        normal = np.cross(edge1,edge2)
        norm_len = np.linalg.norm(normal)
        if norm_len == 0:
            continue
        normal = normal / norm_len

        intensity = max(0.2, np.dot(normal,light_dir))
        shaded_color = (np.array(triangle[3])*intensity).astype(np.uint8)

        p1 = mvp @ triangle[0]
        p2 = mvp @ triangle[1]
        p3 = mvp @ triangle[2]

        pts,depths = [], []
        for p in [p1, p2, p3]:
            if p[3] <= 0:
                continue 
            x_scr = (((p[0]/p[3])*0.5)+0.5)*WIDTH
            y_scr = (1.0 - ((p[1]/p[3])*0.5 +0.5))*HEIGHT
            pts.append([x_scr, y_scr])
            depths.append(p[2]/p[3])

        if len(pts) < 3:
            continue

        p1_scr, p2_scr, p3_scr = pts[0], pts[1], pts[2]

        # Restricting the rendering zone
        xmin = math.floor(min(p1_scr[0],p2_scr[0],p3_scr[0]))
        xmax = math.floor(max(p1_scr[0],p2_scr[0],p3_scr[0]))
        ymin = math.floor(min(p1_scr[1],p2_scr[1],p3_scr[1]))
        ymax = math.floor(max(p1_scr[1],p2_scr[1],p3_scr[1])) 

        print(f"xmin:{xmin} |xmax{xmax} |ymin{ymin} |ymax{ymax}")
        # Clamping bounding boxes 
        xmax = int(min(WIDTH,xmax))
        xmin = int(max(0,xmin))
        ymax = int(min(HEIGHT,ymax))
        ymin = int(max(0,ymin))

        area = sign(p1_scr,p2_scr,p3_scr)
        if area == 0: ##skiping degenerate triangles
            continue 
        for x in range(xmin,xmax): # could optimise with bounding boxes 
            for y in range(ymin,ymax):
                if pixelContainedIn2DTriangle([x,y],p1_scr,p2_scr,p3_scr):
                    pt = [x+0.5,y+0.5]
                    w0 = sign(pt,p2_scr,p3_scr)
                    w1 = sign(pt,p3_scr,p1_scr)
                    w2 = sign(pt,p1_scr,p2_scr)

                    w0_norm = abs(w0/area)
                    w1_norm = abs(w1/area)
                    w2_norm = abs(w2/area)

                    z_interpo =  (w0_norm*depths[0])+(w1_norm*depths[1])+(w2_norm*depths[2])                    

                    if z_interpo < z_buffer[y,x]:
                        z_buffer[y,x] =  z_interpo
                        pixels[y,x] = shaded_color
    array = np.array(pixels, dtype=np.uint8)
    new_image = Image.fromarray(array, 'RGB')

    return new_image


def render_example():
    # Bengining of the rasterisation algorithm
    pixels = np.zeros((HEIGHT,WIDTH,3),dtype=np.uint8)
    z_buffer = np.full((HEIGHT,WIDTH),np.inf) 

    # creating and seting up camera

    camera_position = np.array([3.0,2.0,4.0])
    camera_target = np.array([0,0,0])
    up_dir = np.array([0.0, 1.0, 0.0])

    view_matrix = create_look_at_matrix(camera_position,camera_target,up_dir)
    proj_matrix = create_perspective_matrix(60.0, WIDTH/HEIGHT, 0.1, 100.0)
    mvp = proj_matrix @ view_matrix

    # Directional Light (Normalized)
    light_dir = np.array([0.5,1.0,0.7])
    light_dir = light_dir / np.linalg.norm(light_dir)

    # Generate the shapes 

    triangles = build_cube(center=(0,0,0), size=2.0, color=(0,180,240))

    for triangle in triangles:

        #Calculate Face Normal 
        edge1 = np.array(triangle[1][:3]) - np.array(triangle[0][:3])
        edge2 = np.array(triangle[2][:3]) - np.array(triangle[0][:3])
        normal = np.cross(edge1,edge2)
        norm_len = np.linalg.norm(normal)
        if norm_len == 0:
            continue
        normal = normal / norm_len 
        
        #Flat Shading (Diffuse Lighting)
        intensity = max(0.2,np.dot(normal, light_dir))
        shaded_color = (np.array(triangle[3])*intensity).astype(np.uint8)

        p1 = mvp @ triangle[0]
        p2 = mvp @ triangle[1]
        p3 = mvp @ triangle[2]

        # Converting Clip Space to Screen 
        pts = []
        depths = []
        for p in [p1, p2, p3]:
            #Guard against points
            if p[3] <= 0:
                continue
            x_scr = (((p[0]/p[3])*0.5)+0.5)* WIDTH
            y_scr = (1.0 - ((p[1] / p[3])*0.5 +0.5)) * HEIGHT
            pts.append([x_scr,y_scr])
            depths.append(p[2]/p[3])

        if len(pts) < 3:
            continue

        p1_scr,p2_scr,p3_scr = pts[0],pts[1],pts[2]


        # Project the vertices of the triangle using perspective projection
        # for now the triangles are exactly infront of the camera
        

        # Restricting the rendering zone
        xmin = math.floor(min(p1_scr[0],p2_scr[0],p3_scr[0]))
        xmax = math.floor(max(p1_scr[0],p2_scr[0],p3_scr[0]))
        ymin = math.floor(min(p1_scr[1],p2_scr[1],p3_scr[1]))
        ymax = math.floor(max(p1_scr[1],p2_scr[1],p3_scr[1])) 

        print(f"xmin:{xmin} |xmax{xmax} |ymin{ymin} |ymax{ymax}")
        # Clamping bounding boxes 
        xmax = int(min(WIDTH,xmax))
        xmin = int(max(0,xmin))
        ymax = int(min(HEIGHT,ymax))
        ymin = int(max(0,ymin))

        area = sign(p1_scr,p2_scr,p3_scr)
        if area == 0: ##skiping degenerate triangles
            continue 
        
        for x in range(xmin,xmax): # could optimise with bounding boxes 
            for y in range(ymin,ymax):
                if pixelContainedIn2DTriangle([x,y],p1_scr,p2_scr,p3_scr):
                    pt = [x+0.5,y+0.5]
                    w0 = sign(pt,p2_scr,p3_scr)
                    w1 = sign(pt,p3_scr,p1_scr)
                    w2 = sign(pt,p1_scr,p2_scr)

                    w0_norm = abs(w0/area)
                    w1_norm = abs(w1/area)
                    w2_norm = abs(w2/area)

                    z_interpo =  (w0_norm*depths[0])+(w1_norm*depths[1])+(w2_norm*depths[2])                    

                    if z_interpo < z_buffer[y,x]:
                        z_buffer[y,x] =  z_interpo
                        pixels[y,x] = shaded_color
        print("triangle done")
    print("Rendering done")


    #Convert the pixels into an array using numpy
    array = np.array(pixels,dtype=np.uint8)

    # Use PIL to create an image from the new array of pixels
    new_image =  Image.fromarray(array,'RGB')
    new_image.save('renderOutput.png')

if __name__ == '__main__':
    #render_example()
    render_rotating_animation()
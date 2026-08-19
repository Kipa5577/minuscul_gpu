import py4hw 
import tkinter as tk 
import numpy as np
from PIL import Image, ImageTk
from memory import *



class screen(py4hw.Logic):
    def __init__(self, parent, Name,mem:MemoryInterface,switch_buffer,SCREEN_WIDTH,SCREEN_HEIGHT,side_by_side=False):
        super().__init__(parent, Name)

        self.Switch_Buffer = self.addIn('Switch_Buffer ',switch_buffer)
        self.mem = self.addInterfaceSink('Memory',mem)

        nb_addresses = SCREEN_HEIGHT * SCREEN_WIDTH
        self.Buffer0 = np.zeros(nb_addresses,dtype=np.uint32) 
        self.Buffer1 = np.zeros(nb_addresses,dtype=np.uint32)
        self.ActiveBuffer = 0 # 0 or 1

        #tkinter initialisation 
        self.root = tk.Tk()
        self.root.title("Rasterisation Output")
        self.root.geometry(f"{SCREEN_WIDTH}x{SCREEN_HEIGHT}")
        self.label = tk.Label(self.root)
        if side_by_side:
            self.label.pack(side=tk.LEFT)
        else:
            self.label.pack()

        self.root.mainloop()


    def update_display(self,buffer):
        # convert Numpy array to Tkinter-compatible Image
        if buffer:
            display_Image = Image.fromarray(self.Buffer0.reshape((SCREEN_HEIGHT, SCREEN_WIDTH)).astype(np.uint8)) 
            display_Image2 = Image.fromarray(self.Buffer0.reshape(()))
        else:
            display_Image = Image.fromarray(self.Buffer1.reshape((SCREEN_HEIGHT, SCREEN_WIDTH)).astype(np.uint8), mode='L')


        tk_image = ImageTk.PhotoImage(image=display_Image)
        self.label.config(image=tk_image)

        self.root.after(16,self.update_display)


    def clock(self):
        address = self.mem.address.get()
        r = self.mem.read.get()
        w = self.mem.write.get()

        if r == 1 and w == 0:
            if self.ActiveBuffer:
                self.Buffer0[address] = self.mem.read_data.get()                
            else:
                self.Buffer1[address] = self.mem.read_data.get()
            self.mem.resp.prepare(1)
        elif r == 0 and w == 1:
            if self.ActiveBuffer:
                self.Buffer0[address] = self.mem.write_data.get()
            else:
                self.Buffer1[address] = self.mem.write_data.get()
            self.mem.resp.prepare(1) 
        else:
            self.mem.resp.prepare(0)

        self.update_display()



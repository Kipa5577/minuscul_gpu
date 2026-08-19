import py4hw
from ..src.rasterisation.renderer import *
from ..src.screen.screen import *
from ..src.memory.memory import *

sys = py4hw.HWSystem()

screen_width = 640
screen_height = 480

memory_Interface = MemoryInterface(sys, 'MemoryInterface', memory_size=screen_width*screen_height)

mem = Memory(sys, 'Memory', memory_Interface)

screen = Screen(sys, 'screen', screen_width, screen_height,mem=memory_Interface, side_by_side=False)

renderer = Renderer(sys, 'renderer', screen_width, screen_height, mem=memory_Interface)
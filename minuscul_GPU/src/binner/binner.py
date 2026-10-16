import py4hw

from ..memory.memory import MemoryInterface
from ..rasteriser.triangle_line import unpack_triangle_line


class AllDone(py4hw.Logic):
    '''Combinational leaf: done = AND of every core's own done signal.'''
    def __init__(self, parent, name, core_dones, done):
        super().__init__(parent, name)
        self.core_dones = [self.addIn(f'core_done{i}', w) for i, w in enumerate(core_dones)]
        self.done = self.addOut('done', done)

    def propagate(self):
        self.done.put(1 if all(w.get() == 1 for w in self.core_dones) else 0)


class Binner(py4hw.Logic):
    '''
    Leaf FSM: the real binning pre-pass a tile-based GPU runs before its
    rasteriser cores start. Reads every triangle once from a single shared,
    read-only source triangle memory, and assigns it to every core whose
    row-strip its Y-extent overlaps - so a core only ever fetches and
    bbox-tests the triangles that can actually land in its own strip,
    instead of every triangle in the scene.

    A triangle straddling more than one core's strip is duplicated into
    each core it overlaps, same as a real tile binner would do.

    Writing directly into core_memories[i].memory rather than through a bus
    is deliberate, not a shortcut: py4hw raises the moment a second
    component tries to drive an already-sourced wire, so this memory can
    only ever have one bus master, and that has to be the owning
    RenderController (it's the one reading through the normal protocol).
    A direct list write is the same "preload" pattern this codebase already
    uses everywhere triangle data gets loaded.
    '''

    (S_FETCH_ISSUE, S_FETCH_WAIT, S_NEXT_TRIANGLE, S_DONE) = range(4)

    def __init__(self, parent, name, source_mem: MemoryInterface,
                 first_triangle_address, number_of_triangles,
                 core_memories, core_partitions,
                 core_triangle_counts, binning_done):
        super().__init__(parent, name)

        self.source_mem = self.addInterfaceSource('source_mem', source_mem)
        self.first_triangle_address = self.addIn('first_triangle_address', first_triangle_address)
        self.number_of_triangles = self.addIn('number_of_triangles', number_of_triangles)
        self.binning_done = self.addOut('binning_done', binning_done)
        self.core_triangle_count = [
            self.addOut(f'core_triangle_count{i}', w) for i, w in enumerate(core_triangle_counts)
        ]

        self.core_memories = core_memories      # plain Memory objects: direct .memory writes
        self.core_partitions = core_partitions  # [(y_start, y_end), ...], one per core
        self._core_write_index = [0] * len(core_memories)

        self.state = self.S_FETCH_ISSUE
        self.triangle_index = 0

    def clock(self):
        m = self.source_mem

        if self.state == self.S_FETCH_ISSUE:
            addr = self.first_triangle_address.get() + self.triangle_index
            m.address.prepare(addr)
            m.read.prepare(1)
            m.write.prepare(0)
            self.state = self.S_FETCH_WAIT

        elif self.state == self.S_FETCH_WAIT:
            if m.done.get() == 1:
                m.read.prepare(0)
                line = m.read_data.get()
                _, tri_y1, _, _, tri_y2, _, _, tri_y3, _, _ = unpack_triangle_line(line)
                tri_ymin = min(tri_y1, tri_y2, tri_y3)
                tri_ymax = max(tri_y1, tri_y2, tri_y3)

                for i, (y_start, y_end) in enumerate(self.core_partitions):
                    if tri_ymax >= y_start and tri_ymin <= y_end:
                        idx = self._core_write_index[i]
                        self.core_memories[i].memory[idx] = line
                        self._core_write_index[i] = idx + 1

                self.state = self.S_NEXT_TRIANGLE
            # else: memory not ready yet, stay in S_FETCH_WAIT

        elif self.state == self.S_NEXT_TRIANGLE:
            self.triangle_index += 1
            if self.triangle_index >= self.number_of_triangles.get():
                for count_wire, count in zip(self.core_triangle_count, self._core_write_index):
                    count_wire.prepare(count)
                self.state = self.S_DONE
            else:
                self.state = self.S_FETCH_ISSUE

        elif self.state == self.S_DONE:
            pass

        self.binning_done.prepare(1 if self.state == self.S_DONE else 0)

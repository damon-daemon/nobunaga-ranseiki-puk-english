"""English data segment: code and data the patches need, loaded at the end of the BSS.

The ELF has two PT_LOAD program headers; the second (at _end = 0x19F0800, after the BSS)
is empty. Filling it makes the loader place our bytes right after the BSS. The heap
starts at _end, so it is moved past the segment (survey part A):

  0x10006C/0x100074  lui/addiu a0 = _end   SetupHeap syscall (0x3D): heap start
  0x619394           word = _end           libkernel sbrk's heap pointer (sbrk 0x11F2E0)

0x10000C/0x100014 (BSS clear end) stays: the BSS still ends at 0x19F0800. The clear loop
(sq, then compare, then step) stores once more at the end address: the segment's
first 16 bytes are zeroed at boot, so they are kept as padding (PAD).
The heap (and so the segment) must stay below the stack (0x1F60000-0x1FB0000).
Port of Soutenroku tools/elfseg.py (same engine generation, same crt0).
"""
import struct, sys, os

sys.path.insert(0, os.path.dirname(__file__))
import asm

BASE = 0x19F0800
FOFF, VBASE = 0x80, 0x100000
HEAP_SITES = [(0x10006C, 0x100074, 'a0')]
HEAP_WORDS = [0x619394]
LOAD = 1                                  # index of the empty PT_LOAD
PAD = 16                                  # zeroed by crt0's BSS clear (see above)

class Segment:
    def __init__(self, base=BASE):
        self.base, self.data = base, bytearray(PAD)

    @property
    def end(self):
        return self.base + len(self.data)

    def add(self, b, align=4):
        self.data.extend(bytes(-len(self.data) % align))
        addr = self.end
        self.data.extend(b)
        return addr

    def reserve(self, n, align=4):
        """Address of n zero bytes (filled later with put)."""
        return self.add(bytes(n), align)

    def put(self, addr, b):
        o = addr - self.base
        assert 0 <= o and o + len(b) <= len(self.data)
        self.data[o:o + len(b)] = b

    def code(self, lines):
        """Assemble lines (asm.assemble format, labels allowed) into the segment; -> address."""
        n = 4 * len([l for l in lines if not isinstance(l, str)])
        at = self.reserve(n)
        self.put(at, asm.assemble(at, lines))
        return at

def word(elf, vaddr):
    return struct.unpack_from('<I', elf, vaddr - VBASE + FOFF)[0]

def patch(elf, vaddr, b):
    o = vaddr - VBASE + FOFF
    elf[o:o + len(b)] = b

def apply(elf, seg):
    """Append the segment to the ELF file, point the empty PT_LOAD at it, move the heap.
    Returns the new heap start."""
    phoff = struct.unpack_from('<I', elf, 0x1C)[0]
    phnum = struct.unpack_from('<H', elf, 0x2C)[0]
    assert phnum == LOAD + 1, phnum
    at = phoff + 32 * LOAD
    p = struct.unpack_from('<8I', elf, at)
    assert p[0] == 1 and p[2] == BASE and p[4] == 0 and p[5] == 0, 'PT_LOAD %d is not the empty one at _end' % LOAD
    if len(seg.data) <= PAD:
        return BASE
    elf.extend(bytes(-len(elf) % 0x80))
    off = len(elf)
    elf.extend(seg.data)
    size = len(seg.data)
    struct.pack_into('<8I', elf, at, 1, off, BASE, BASE, size, size, 7, 0x10)
    end = (BASE + size + 0xFF) & ~0xFF
    for lui, addiu, reg in HEAP_SITES:
        assert (elf[lui - VBASE + FOFF:lui - VBASE + FOFF + 4] == asm.assemble(lui, [('lui', reg, asm.hi(BASE))]) and
                elf[addiu - VBASE + FOFF:addiu - VBASE + FOFF + 4] == asm.assemble(addiu, [('addiu', reg, reg, asm.lo(BASE))])), \
            'SetupHeap site %x changed' % lui
        patch(elf, lui, asm.assemble(lui, [('lui', reg, asm.hi(end))]))
        patch(elf, addiu, asm.assemble(addiu, [('addiu', reg, reg, asm.lo(end))]))
    for va in HEAP_WORDS:
        o = va - VBASE + FOFF
        assert elf[o:o + 4] == struct.pack('<I', BASE), 'sbrk word %x changed' % va
        patch(elf, va, struct.pack('<I', end))
    return end

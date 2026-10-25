"""Debug build hook (build.py env LABEL_LOG=1): log every label widget 0x3E5D00(obj, x, y, rect,
text) call as (obj, text) pairs into a ring of 64 entries in the English segment, to read the
label objects back over PINE (+0x10 plate sprite, +0x14 text object; glyph sprites hang off the
text object +0x1C, linked by +0x6C). Buffer: u32 count, then 64 x (obj, text)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import elfseg
from asm import assemble, hi, lo

LABEL = 0x3E5D00
OLD = (0x27BDFF60, 0xFFBF0080)          # addiu sp,sp,-0xa0; sd ra,0x80(sp)

def apply(elf, seg):
    assert (elfseg.word(elf, LABEL), elfseg.word(elf, LABEL + 4)) == OLD, 'label entry changed'
    buf = seg.add(bytes(4 + 64 * 8), 16)
    assert -0x8000 <= lo(buf) and lo(buf) + 8 + 63 * 8 < 0x8000
    stub = seg.code([
        ('lui', 'at', hi(buf)),
        ('lw', 't9', lo(buf), 'at'),
        ('andi', 't9', 't9', 63),
        ('sll', 't9', 't9', 3),
        ('addu', 't9', 't9', 'at'),
        ('sw', 'a0', lo(buf) + 4, 't9'),
        ('sw', 't0', lo(buf) + 8, 't9'),
        ('lw', 't9', lo(buf), 'at'),
        ('addiu', 't9', 't9', 1),
        ('sw', 't9', lo(buf), 'at'),
        ('sd', 'ra', 0x80, 'sp'),
        ('j', LABEL + 8),
        ('nop',)])
    elfseg.patch(elf, LABEL, assemble(LABEL, [('j', stub), ('addiu', 'sp', 'sp', -0xA0)]))
    print('label log at %#x' % buf)
    return buf

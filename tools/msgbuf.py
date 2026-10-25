"""Bigger message output buffer (ELF code patch).

Every message expansion goes to one buffer: malloc(0x4B0) at 0x40CAC0 (manager +0x14 -> +4,
0x40CA80), cleared per message (memset 0x4B0 at 0x40DFC8 in the main loop 0x40DFA0); the
byte writer stops at the limit mgr+0x3C = 0x4AE (0x40DFD4) and the rest of the message is
cut. English help pages will exceed 1,198 bytes, so the three immediates are raised
(survey part A section 5; same layout as Soutenroku's msgbuf.py).
"""
import struct

FOFF = 0x100000 - 0x80
SIZE = 0x1000
LIMIT = SIZE - 2            # bytes of text the writer accepts
SITES = {0x40CAC0: (0x24040000, 0x4B0),     # addiu a0, zero, size   (malloc)
         0x40DFC8: (0x24060000, 0x4B0),     # addiu a2, zero, size   (memset)
         0x40DFD4: (0x24030000, 0x4AE)}     # addiu v1, zero, limit

def apply(elf):
    for va, (op, old) in SITES.items():
        o = va - FOFF
        assert struct.unpack_from('<I', elf, o)[0] == op | old, hex(va)
        struct.pack_into('<I', elf, o, op | (LIMIT if old == 0x4AE else SIZE))

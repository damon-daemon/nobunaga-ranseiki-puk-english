"""Message blocks: MEDIA/DATA/MESSAGE.NB9 ('message') and MEDIA/DATA2/KRETSU.NB9 ('kretsu').

Each file is one block: u16 count, u32 offsets[count] (from the block start), then the
strings back to back with NO terminator: a message runs to the next offset, the last one to
the end of the block minus one pad byte (00) that follows it. Messages normally end 05 05 05
(END). NUL bytes do occur inside messages (01 C lo hi with hi = 0), so the strings must be
split by offset, never at NULs. Plain Shift-JIS, no obfuscation. Strings are never shared.
"""
import struct

NAMES = {'message': 'MEDIA/DATA/MESSAGE.NB9', 'kretsu': 'MEDIA/DATA2/KRETSU.NB9'}
BLOCKS = tuple(NAMES)
END = b'\5\5\5'
PAD = b'\0'

def strings(data):
    n = struct.unpack_from('<H', data)[0]
    offs = list(struct.unpack_from('<%dI' % n, data, 2))
    assert offs[0] == 2 + 4 * n and all(a <= b for a, b in zip(offs, offs[1:])), 'not a message block'
    end = len(data)
    if data.endswith(END + PAD):
        end -= 1                        # the pad byte after the last message
    return [data[a:b] for a, b in zip(offs, offs[1:] + [end])]

def pack(strs):
    n = len(strs)
    assert strs[-1].endswith(END), 'last message must end with END so the pad byte can be told apart'
    out, offs = bytearray(), []
    for s in strs:
        offs.append(2 + 4 * n + len(out))
        out += s
    return struct.pack('<H%dI' % n, n, *offs) + bytes(out) + PAD

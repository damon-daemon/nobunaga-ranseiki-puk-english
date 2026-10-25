"""NB9 packs: (u32 offset, u32 size) pairs from the file start, then a sentinel pair
(file size, file size); the header is (n+1)*8 bytes rounded up to the pack's alignment
(2048 for most packs, 16 for COMMON/FONTTEX/EDEVENT/MISSION, 128 for MAINCG) and the
entries follow in order, each padded to that alignment. The reader (0x144520) counts
entries up to the first pair with offset == size, so the sentinel is required and no real
entry may have offset == size. Entry reads (0x171120) round offset and size up to 2048
and do not use the next offset, so a 2048-aligned pack may have slack.

Three packs have a copy of their table in the ELF that must stay in sync (ELF_TOC), and
COMBG/WMAPDATA have no header at all: their table lives in the ELF only.
"""
import struct

ELF_FOFF = 0x100000 - 0x80
ALIGNS = (2048, 128, 16)
# iso path -> (ELF vaddr of the TOC copy, entries, sentinel present)
ELF_TOC = {'MEDIA/DATA/EVENT.NB9': (0x6227A0, 81, True),
           'MEDIA/WAR/WARMAPCG.NB9': (0x622C50, 57, True),
           'MEDIA/DATA/SHIROCG.NB9': (0x622E20, 960, True),
           'MEDIA/DATA/COMBG.NB9': (0x622BF0, 10, True),          # ELF only (no header in the file)
           'MEDIA/WAR/WMAPDATA.NB9': (0x62F430, 57, False)}      # ELF only, no sentinel


def toc(data):
    """[(offset, size)] of the entries (without the sentinel)."""
    out = []
    for i in range(0, len(data) - 7, 8):
        o, s = struct.unpack_from('<II', data, i)
        if o == s:
            assert o == len(data), 'sentinel %#x != file size %#x' % (o, len(data))
            return out
        out.append((o, s))
    raise ValueError('no sentinel')


def alignment(data, table=None):
    """The pack's alignment, from the header size and the entry offsets."""
    table = table or toc(data)
    n = len(table)
    for a in ALIGNS:
        if all(o % a == 0 for o, _ in table) and len(data) % a == 0 and -(n + 1) * 8 % a + (n + 1) * 8 == table[0][0]:
            return a
    raise ValueError('cannot determine alignment (header %#x, %d entries)' % (table[0][0], n))


def entries(data):
    return [data[o:o + s] for o, s in toc(data)]


def entry(data, i):
    o, s = toc(data)[i]
    return data[o:o + s]


def pack(items, align):
    """Pack bytes and table [(offset, size)] for a list of entry bytes."""
    n = len(items)
    out = bytearray(-(n + 1) * 8 % align + (n + 1) * 8)
    table = []
    for data in items:
        table.append((len(out), len(data)))
        out += data
        out += bytes(-len(out) % align)
    for i, (o, s) in enumerate(table):
        assert o != s, 'entry %d: offset == size (%#x) would end the table early' % (i, o)
        struct.pack_into('<II', out, 8 * i, o, s)
    struct.pack_into('<II', out, 8 * n, len(out), len(out))
    return bytes(out), table


def apply(data, changes, name=None, elf=None):
    """Rebuild a pack with {entry index: bytes}; if name has an ELF copy of the table and
    elf (bytearray) is given, the copy is updated too. Returns the new pack bytes."""
    items = entries(data)
    align = alignment(data)
    for i, d in changes.items():
        items[i] = bytes(d)
    out, table = pack(items, align)
    if name in ELF_TOC and elf is not None:
        set_elf_toc(elf, name, table, len(out))
    return out


def elf_toc(elf, name):
    """[(offset, size)] of the ELF copy of a pack's table."""
    va, n, _ = ELF_TOC[name]
    return [struct.unpack_from('<II', elf, va - ELF_FOFF + 8 * i) for i in range(n)]


def set_elf_toc(elf, name, table, size):
    va, n, sentinel = ELF_TOC[name]
    assert len(table) == n, '%s: %d entries, ELF table has %d' % (name, len(table), n)
    for i, (o, s) in enumerate(table):
        struct.pack_into('<II', elf, va - ELF_FOFF + 8 * i, o, s)
    if sentinel:
        struct.pack_into('<II', elf, va - ELF_FOFF + 8 * n, size, size)

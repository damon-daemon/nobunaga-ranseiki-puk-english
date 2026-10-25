"""PS2 CD image (single track, raw 2352-byte MODE2 Form 1 sectors): read files, write a patched copy.

The game does not search the file system for its data: the ELF holds a table of
(name, LBA, end LBA, sector count) for 92 files (LBA_TABLE; whole-file reads load
`sectors * 2048` bytes, so the count must cover the new size). Only the ELF and the IOP
modules are found through the ISO 9660 directory. There is no UDF.

A rebuilt file that still fits its sectors is rewritten in place; a bigger one moves to
the end of the data area. The ELF is the last file on the disc, so it simply grows in
place and moved files follow it; the 150-sector empty tail and the PVD volume size shift
by the same amount (the image may grow). The ISO directory record and the ELF table are
patched for every file that changes place or size; old copies of moved files are left as
they are (smaller patch). Every rewritten sector gets a new EDC/ECC; untouched sectors are
copied byte for byte.

  list <bin>              -> path, LBA, size, sector count
  table <bin>             -> the ELF LBA table against the ISO directory
"""
import os, struct, sys

RAW = 2352
SECTOR = 2048
PREGAP = 150                                # LBA 0 = MSF 00:02:00
TAIL = 150                                  # empty Mode 2 sectors after the last file
ELF_NAME = 'SLPM_622.25'
ELF_FOFF = 0x100000 - 0x80                  # vaddr - file offset of the code/data segment
LBA_TABLE, LBA_COUNT = 0x61CEC0, 92         # 32-byte records: name ptr, +8 LBA, +0x10 end, +0x18 sectors
SUB_DATA, SUB_EOF = b'\0\0\x08\0', b'\0\0\x89\0'
SYNC = b'\0' + b'\xff' * 10 + b'\0'

# ---- EDC / ECC (CD-ROM Mode 2 Form 1) ----
EDC_LUT = []
for _i in range(256):
    _e = _i
    for _ in range(8):
        _e = (_e >> 1) ^ (0xD8018001 if _e & 1 else 0)
    EDC_LUT.append(_e)
ECC_F = [((i << 1) ^ (0x11D if i & 0x80 else 0)) & 0xFF for i in range(256)]
ECC_B = [0] * 256
for _i in range(256):
    ECC_B[_i ^ ECC_F[_i]] = _i


def edc(data):
    e = 0
    for b in data:
        e = (e >> 8) ^ EDC_LUT[(e ^ b) & 0xFF]
    return e


def _ecc_block(src, major_count, minor_count, major_mult, minor_inc):
    size = major_count * minor_count
    out = bytearray(2 * major_count)
    f, bl = ECC_F, ECC_B
    for major in range(major_count):
        index = (major >> 1) * major_mult + (major & 1)
        a = b = 0
        for _ in range(minor_count):
            t = src[index]
            index += minor_inc
            if index >= size:
                index -= size
            a = f[a ^ t]
            b ^= t
        a = bl[f[a] ^ b]
        out[major] = a
        out[major + major_count] = a ^ b
    return out


def msf(lba):
    m, s = divmod(lba + PREGAP, 75 * 60)
    s, f = divmod(s, 75)
    bcd = lambda v: (v // 10) << 4 | v % 10
    return bytes((bcd(m), bcd(s), bcd(f)))


def form1_sector(lba, sub, data):
    """Full raw Mode 2 Form 1 sector: header, subheader x2, 2048 bytes, EDC, ECC."""
    assert len(data) == SECTOR and len(sub) == 4
    s = bytearray(SYNC + msf(lba) + b'\x02' + sub + sub + data + bytes(4 + 276))
    struct.pack_into('<I', s, 2072, edc(s[16:2072]))
    hdr = bytes(s[12:16])
    s[12:16] = bytes(4)                     # Mode 2: the header is not part of the ECC
    s[2076:2248] = _ecc_block(s[12:], 86, 24, 2, 86)
    s[2248:2352] = _ecc_block(s[12:], 52, 43, 86, 88)
    s[12:16] = hdr
    return bytes(s)


def empty_sector(lba):
    """Tail sector as on the clean disc: header only, everything else zero."""
    return SYNC + msf(lba) + b'\x02' + bytes(RAW - 16)


# ---- reading ----
class Record:
    def __init__(self, path, dir_lba, pos, lba, size, is_dir):
        self.path, self.dir_lba, self.pos, self.lba, self.size, self.is_dir = path, dir_lba, pos, lba, size, is_dir

    @property
    def sectors(self):
        return (self.size + SECTOR - 1) // SECTOR


class Disc:
    def __init__(self, path):
        self.path = path
        self.f = open(path, 'rb')
        self.nsec = os.path.getsize(path) // RAW
        self._recs = None

    def raw(self, lba):
        self.f.seek(lba * RAW)
        return self.f.read(RAW)

    def sub(self, lba):
        """XA subheader (file, channel, submode, coding)."""
        return self.raw(lba)[16:20]

    def user(self, lba):
        return self.raw(lba)[24:24 + SECTOR]

    def read(self, lba, size):
        self.f.seek(lba * RAW)
        n = (size + SECTOR - 1) // SECTOR
        buf = self.f.read(n * RAW)
        return b''.join(buf[i * RAW + 24:i * RAW + 24 + SECTOR] for i in range(n))[:size]

    def records(self):
        """{'MEDIA/DATA/MESSAGE.NB9': Record, 'SLPM_622.25': Record, ...} (directories included)."""
        if self._recs is None:
            pvd = self.user(16)
            assert pvd[1:6] == b'CD001', 'no PVD'
            lba, size = struct.unpack_from('<I4xI', pvd, 156 + 2)
            self._recs = {}
            self._dir(lba, size, '', self._recs)
        return self._recs

    def _dir(self, lba, size, prefix, out):
        data = self.read(lba, size)
        pos = 0
        while pos < len(data):
            ln = data[pos]
            if ln == 0:
                pos = (pos // SECTOR + 1) * SECTOR
                continue
            elba, esize = struct.unpack_from('<I4xI', data, pos + 2)
            flags, nl = data[pos + 25], data[pos + 32]
            name = data[pos + 33:pos + 33 + nl]
            if name not in (b'\0', b'\1'):
                name = name.decode('ascii').split(';')[0]
                rec = Record(prefix + name, lba + pos // SECTOR, pos % SECTOR, elba, esize, bool(flags & 2))
                out[rec.path] = rec
                if rec.is_dir:
                    self._dir(elba, esize, rec.path + '/', out)
            pos += ln

    def walk(self):
        for r in self.records().values():
            if not r.is_dir:
                yield r.path, r.lba, r.size

    def file(self, name):
        r = self.records()[name]
        return self.read(r.lba, r.size)

    def data_end(self):
        """First sector after the last file (the ELF); the empty tail follows."""
        return max(r.lba + r.sectors for r in self.records().values())


def read_file(path, name):
    return Disc(path).file(name)


# ---- ELF LBA table ----
def lba_table(elf):
    """{iso path: record file offset} for the ELF's hard-coded file table (disc order)."""
    out = {}
    for i in range(LBA_COUNT):
        off = LBA_TABLE - ELF_FOFF + 32 * i
        p = struct.unpack_from('<I', elf, off)[0] - ELF_FOFF
        name = elf[p:elf.index(b'\0', p)].decode('ascii').lstrip('\\').split(';')[0]
        out[name.replace('\\', '/').upper()] = off
    return out


def table_entry(elf, off):
    """(lba, end lba, sectors) of one table record."""
    return struct.unpack_from('<I4xI4xI', elf, off + 8)


def _set_table(elf, off, lba, size):
    n = (size + SECTOR - 1) // SECTOR
    struct.pack_into('<I', elf, off + 8, lba)
    struct.pack_into('<I', elf, off + 0x10, lba + n - 1)
    struct.pack_into('<I', elf, off + 0x18, n)


# ---- writing ----
def _file_sectors(lba, data, n):
    """n raw sectors holding data (zero padded); subheader EOF on the last data sector,
    like the clean disc (unused tail sectors of a shrunk file keep the data submode)."""
    last = max(1, (len(data) + SECTOR - 1) // SECTOR) - 1
    out = []
    for i in range(n):
        chunk = data[i * SECTOR:(i + 1) * SECTOR].ljust(SECTOR, b'\0')
        out.append(form1_sector(lba + i, SUB_EOF if i == last else SUB_DATA, chunk))
    return out


def plan(disc, files):
    """Place the changed files: ({name: (lba, sectors to write)} for the given files,
    [moved names], first sector of the tail)."""
    recs = disc.records()
    place, moved = {}, []
    tail = disc.data_end()
    elf = recs[ELF_NAME]
    assert elf.lba + elf.sectors == tail, 'the ELF is not the last file'
    if ELF_NAME in files:                   # grows in place: it is last on the disc
        n = (len(files[ELF_NAME]) + SECTOR - 1) // SECTOR
        place[ELF_NAME] = (elf.lba, max(n, elf.sectors))
        tail = elf.lba + place[ELF_NAME][1]
    for name in sorted(files, key=lambda n: recs[n].lba):
        if name == ELF_NAME:
            continue
        r, n = recs[name], (len(files[name]) + SECTOR - 1) // SECTOR
        if n <= r.sectors:
            place[name] = (r.lba, r.sectors)
        else:
            place[name] = (tail, n)
            tail += n
            moved.append(name)
    return place, moved, tail


def write_image(clean, out, files):
    """Copy the clean disc to out with files ({iso path: bytes}) replaced. The ELF LBA
    table is patched for every file that moves or changes size (the ELF is added to the
    written files if needed). Returns the list of moved files."""
    disc = Disc(clean)
    recs = disc.records()
    for name in files:
        assert name in recs and not recs[name].is_dir, name
    files = {n: d for n, d in files.items() if disc.file(n) != d}
    place, moved, tail = plan(disc, files)
    elf = bytearray(files.get(ELF_NAME) or disc.file(ELF_NAME))
    for name, off in lba_table(elf).items():
        lba = place[name][0] if name in place else recs[name].lba
        size = len(files[name]) if name in files else recs[name].size
        _set_table(elf, off, lba, size)
    if bytes(elf) != disc.file(ELF_NAME):
        files[ELF_NAME] = bytes(elf)
        place, moved2, tail2 = plan(disc, files)
        assert (moved2, tail2) == (moved, tail), 'the patched table changed the layout'
    data_end = disc.data_end()
    shift = tail - data_end
    patch = {}                              # directory / PVD sectors to rewrite: lba -> bytearray(user data)

    def user(lba):
        if lba not in patch:
            patch[lba] = bytearray(disc.user(lba))
        return patch[lba]

    def set_rec(r, lba, size):
        u = user(r.dir_lba)
        struct.pack_into('<I', u, r.pos + 2, lba)
        struct.pack_into('>I', u, r.pos + 6, lba)
        struct.pack_into('<I', u, r.pos + 10, size)
        struct.pack_into('>I', u, r.pos + 14, size)

    for name, (lba, _) in place.items():
        if (lba, len(files[name])) != (recs[name].lba, recs[name].size):
            set_rec(recs[name], lba, len(files[name]))
    if shift:
        pvd = user(16)
        vol = struct.unpack_from('<I', pvd, 80)[0] + shift
        struct.pack_into('<I', pvd, 80, vol)
        struct.pack_into('>I', pvd, 84, vol)
    new = {}                                # lba -> raw sector
    for name, (lba, n) in place.items():
        for i, s in enumerate(_file_sectors(lba, files[name], n)):
            new[lba + i] = s
    for lba, u in patch.items():
        if bytes(u) != disc.user(lba):
            new[lba] = form1_sector(lba, disc.sub(lba), bytes(u))
    tmp = out + '.tmp'
    with open(tmp, 'wb') as f:
        for lba in range(data_end):
            f.write(new.get(lba) or disc.raw(lba))
        for lba in range(data_end, tail):
            f.write(new[lba])
        for i in range(TAIL):
            f.write(empty_sector(tail + i))
    os.replace(tmp, out)
    return moved


def verify(out, files):
    """Every given file reads back from out through the ISO directory with valid EDC/ECC,
    the ELF table agrees with the directory for all 92 files, and the volume size covers
    the image; raise on any mismatch."""
    disc = Disc(out)
    recs = disc.records()
    for name, data in files.items():
        r = recs[name]
        assert r.size == len(data) and disc.read(r.lba, r.size) == data, '%s: read back differs' % name
        for i in range(r.sectors):
            raw = disc.raw(r.lba + i)
            assert raw == form1_sector(r.lba + i, raw[16:20], raw[24:24 + SECTOR]), '%s: bad sector %d' % (name, i)
    elf = disc.file(ELF_NAME)
    for name, off in lba_table(elf).items():
        lba, end, n = table_entry(elf, off)
        r = recs[name]
        assert (lba, end, n) == (r.lba, r.lba + r.sectors - 1, r.sectors), \
            '%s: ELF table (%d, %d, %d) != ISO (%d, %d)' % (name, lba, end, n, r.lba, r.sectors)
    vol = struct.unpack_from('<I', disc.user(16), 80)[0]
    assert vol == disc.nsec == disc.data_end() + TAIL, 'volume size %d, image %d sectors' % (vol, disc.nsec)


def write_cue(cue, track):
    with open(cue, 'w', newline='\r\n') as f:
        f.write('FILE "%s" BINARY\n  TRACK 01 MODE2/2352\n    INDEX 01 00:00:00\n' % os.path.basename(track))


def main():
    cmd, path = sys.argv[1], sys.argv[2]
    d = Disc(path)
    if cmd == 'list':
        for name, lba, size in sorted(d.walk(), key=lambda e: e[1]):
            print('%-32s %6d %10d %6d' % (name, lba, size, (size + SECTOR - 1) // SECTOR))
    elif cmd == 'table':
        elf = d.file(ELF_NAME)
        recs = d.records()
        for i, (name, off) in enumerate(lba_table(elf).items()):
            lba, end, n = table_entry(elf, off)
            r = recs.get(name)
            ok = r and (lba, end, n) == (r.lba, r.lba + r.sectors - 1, r.sectors)
            print('%2d %-32s %6d %6d %5d %s' % (i, name, lba, end, n, 'OK' if ok else 'MISMATCH'))


if __name__ == '__main__':
    main()

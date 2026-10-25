"""Dump the run-time field-descriptor tables of the message variables from the running
headless PCSX2 (PINE), see notes/survey/msg_vars.md: the field getters dispatch through
BSS tables filled at start-up, so they cannot be read from the ELF. Each record points
at the field's Japanese name in the ELF, which is resolved here.

  base fields   0x17D5E30 + 16*i  (i = f - 0x32 < 0x22; all object classes)
  person fields 0x17D4CF0 + 16*i  (i >= 0x22; person class only)
  record: +0 name ptr, +4 string getter, +8 numeric getter, +0xC u16, +0xE type byte
          (0 flag, 1 number, 2 string)

usage: .venv/bin/python tools/dev/fieldtab.py [out.txt]   (game must be past start-up;
       result of 2026-10-23 in notes/survey/fieldtab.txt)
"""
import os, struct, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, '..'))
sys.path.insert(0, os.path.join(HERE, '..', 'emu'))
import pine, setup

TABLES = [('base', 0x17D5E30, 0, 0x22), ('person', 0x17D4CF0, 0x22, 0xB3)]


class Elf:
    def __init__(self, data):
        self.data = data
        phoff = struct.unpack_from('<I', data, 0x1C)[0]
        phentsize, phnum = struct.unpack_from('<HH', data, 0x2A)
        self.segs = []
        for i in range(phnum):
            _, off, va, _, filesz, _ = struct.unpack_from('<6I', data, phoff + i * phentsize)
            self.segs.append((va, off, filesz))

    def offset(self, va):
        for v, o, s in self.segs:
            if v <= va < v + s:
                return o + va - v

    def cstr(self, va):
        o = self.offset(va)
        if o is None:
            return '?'
        return self.data[o:self.data.index(b'\0', o)].decode('cp932', 'replace')


def main():
    elf = Elf(setup.clean_elf())
    out = []
    for name, base, lo, hi in TABLES:
        data = pine.read(base + 16 * lo, 16 * (hi - lo))
        out.append('# %s table %#x, i = %#x..%#x (f = i + 0x32)' % (name, base, lo, hi - 1))
        out.append('#    i  f   name_ptr  str_fn    num_fn   u16  type  name')
        for i in range(lo, hi):
            v, s, n, u, t = struct.unpack_from('<IIIHB', data, 16 * (i - lo))
            if not (v or s or n or u or t):
                continue
            f = i + 0x32
            fs = chr(f) if 0x20 < f < 0x7F else '%02X' % f
            out.append('%#5x  %-3s %08x  %08x  %08x  %04x  %d  %s' % (i, fs, v, s, n, u, t, elf.cstr(v)))
    text = '\n'.join(out) + '\n'
    if len(sys.argv) > 1:
        open(sys.argv[1], 'w', encoding='utf-8').write(text)
    print(text)


if __name__ == '__main__':
    main()

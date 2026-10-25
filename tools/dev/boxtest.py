"""Box-measuring build: every translatable unit (except the inserts 0-239 and entries used
as inserts) becomes a ruler so a screenshot shows the box width and line count of
whatever message appears:

    e612:123456789A123456789B123456789C123456789D1234  line 1: entry number + 44 cells
    L2:123456789A123456789B123456789C123456789D1234    line 2: another ruler
    L3:
    L4:
    L5:
    L6:                                                (units whose Japanese is one line
                                                         get line 1 only)

Without the VWF every ASCII cell is 12 px: the last visible ruler character * 12 px is
the box width (letters mark the tens: A = 10 cells = 120 px). A missing "L5:" / "L6:" line
shows the line limit of the panel. Nothing is written to tl/.

usage: .venv/bin/python tools/dev/boxtest.py [out.bin] [--tall]   -> build/boxtest.bin (+ .cue)
  --tall: multi-line units get 12 lines (ruler + L2..L12) to find page heights / line
          limits of the taller windows (help, missions, editors).
"""
import os, re, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import build, iso, msgbuf, msgfile, msgscript, setup, tl

RULER = '123456789A123456789B123456789C123456789D1234'       # 44 cells = 528 px (11 px each)
TALL = '--tall' in sys.argv
INSERTS = range(0, 240)


def insert_targets(n):
    """Entries referenced by {01}C inserts anywhere in block n (kept Japanese)."""
    out = set()
    for e in range(len(tl.rows_of(n))):
        for k, t in msgscript.tokens(tl.rows_of(n)[e]):
            if k == 'inline' and t[0] == 0x01 and len(t) == 4 and t[1] == 0x43:
                out.add(t[2] | t[3] << 8)
    return out


def rulers(n):
    """{key: text} for block n."""
    keep = insert_targets(n)
    if n == 'message':
        keep |= set(INSERTS)
    out = {}
    for e in range(len(tl.rows_of(n))):
        if e in keep:
            continue
        toks, spans = tl.units_of(n, e)
        for u, (a, z) in enumerate(spans):
            jl = len(tl.lines_of(n, toks[a:z]))
            key = '%d' % e if u == 0 else '%d.%d' % (e, u)
            # codes the validator insists on (printf fields, colours, icons), kept at the end
            codes = ''.join(tl.code_str(t) for k, t in toks[a:z]
                            if k == 'inline' and t[0] not in (0x02, 0x01) and not tl.KANA_TOGGLE.match(t))
            if jl <= 1:
                out[key] = 'e%d:%s %s' % (e, RULER, codes)
            elif TALL and n == 'kretsu':       # description limit 147 bytes: 2 wide + 6 short lines
                out[key] = '\\n'.join(['e%d:%s' % (e, RULER), 'L2:' + RULER] + ['L%d:' % i for i in range(3, 8)]
                                      + ['L8: ' + codes])
            elif TALL:
                out[key] = '\\n'.join(['e%d:%s' % (e, RULER)] + ['L%d:' % i for i in range(2, 12)] + ['L12: ' + codes])
            else:
                out[key] = '\\n'.join(['e%d:%s' % (e, RULER), 'L2:' + RULER] + ['L%d:' % i for i in range(3, 6)]
                                      + ['L6: ' + codes])
    return out


def build_english(tls):
    """tl.build_all, dropping the entries that fail (unusual structures) until it is clean."""
    while True:
        english, bad = tl.build_all(tls, quiet=True)
        if not bad:
            return english
        dropped = 0
        for msg in bad:
            m = re.match(r'(\w+)@(\d+)', msg)
            if not m:
                sys.exit('cannot drop: ' + msg)
            n, e = m.group(1), int(m.group(2))
            for k in [k for k in tls[n] if k.split('.')[0] == str(e)]:
                del tls[n][k]; dropped += 1
        print('dropped %d units (%d errors, e.g. %s)' % (dropped, len(bad), bad[0]))


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    out = args[0] if args else os.path.join(build.ROOT, 'build', 'boxtest.bin')
    disc = iso.Disc(setup.CLEAN)
    tls = {n: rulers(n) for n in msgfile.BLOCKS}
    english = build_english(tls)
    elf = bytearray(disc.file(iso.ELF_NAME))
    msgbuf.apply(elf)
    files = {iso.ELF_NAME: bytes(elf)}
    for n, name in msgfile.NAMES.items():
        files[name] = tl.block_bytes(n, english[n])
        print('%-28s %8d -> %8d bytes (%d units)' % (name, len(disc.file(name)), len(files[name]), len(tls[n])))
    build.check_buffers(disc, files)
    build.write(setup.CLEAN, out, files)


if __name__ == '__main__':
    main()

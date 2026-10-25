"""Pipeline self-test: pack repacking and the identity build are byte-identical to the
clean disc, and a stress build moves and grows files (for a boot test in the emulator).

usage: .venv/bin/python tools/dev/isotest.py packs | identity | stress [out.bin]
  packs     parse + repack every NB9 pack from the disc: must equal the original; the ELF
            copies of the EVENT/WARMAPCG/SHIROCG tables must equal the in-file tables
  identity  build with no changes; the image must have the clean SHA-1
  stress    pad MESSAGE.NB9 by 64 KB (moves after the ELF), pad the ELF by 1 MB (grows in
            place), and repack STARTUP.NB9 with its last entry padded to a new sector (TOC
            rewritten, file grows -> moves too)
"""
import os, sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..'))
import build, iso, nb9, setup

MESSAGE, STARTUP = 'MEDIA/DATA/MESSAGE.NB9', 'MEDIA/DATA/STARTUP.NB9'


def packs(disc):
    elf = disc.file(iso.ELF_NAME)
    bad = 0
    for name, _, _ in sorted(disc.walk(), key=lambda e: e[1]):
        if not name.endswith('.NB9'):
            continue
        data = disc.file(name)
        try:
            table = nb9.toc(data)
        except (ValueError, AssertionError):
            continue                        # message blocks, scenarios, header-less packs
        align = nb9.alignment(data, table)
        again = nb9.apply(data, {})
        ok = again == data
        if name in nb9.ELF_TOC:
            ok = ok and nb9.elf_toc(elf, name) == table
        print('%-28s %5d entries align %4d %s' % (name, len(table), align, 'OK' if ok else 'DIFFERS'))
        bad += not ok
    for name in ('MEDIA/DATA/COMBG.NB9', 'MEDIA/WAR/WMAPDATA.NB9'):
        t = nb9.elf_toc(elf, name)
        size = disc.records()[name].size
        ok = all(o + s <= size for o, s in t) and all(o % 2048 == 0 for o, _ in t)
        print('%-28s %5d entries (ELF only) %s' % (name, len(t), 'OK' if ok else 'BAD'))
        bad += not ok
    print('packs: %s' % ('OK' if not bad else '%d FAILED' % bad))


def main():
    mode = sys.argv[1]
    clean = setup.CLEAN
    disc = iso.Disc(clean)
    if mode == 'packs':
        packs(disc)
        return
    files = build.build_files(disc)
    if mode == 'identity':
        out = os.path.join(build.ROOT, 'build', 'identity.bin')
        build.write(clean, out, files)
        digest = setup.sha1(out)
        print('identity: %s' % ('OK' if digest == setup.CLEAN_SHA1 else 'FAILED ' + digest))
        for ext in ('.bin', '.cue', '.xdelta'):
            os.remove(os.path.splitext(out)[0] + ext)
    elif mode == 'stress':
        out = sys.argv[2] if len(sys.argv) > 2 else os.path.join(build.ROOT, 'build', 'stress.bin')
        elf = bytearray(files[iso.ELF_NAME])
        files[MESSAGE] = disc.file(MESSAGE) + bytes(64 << 10)
        startup = disc.file(STARTUP)
        last = len(nb9.toc(startup)) - 1
        files[STARTUP] = nb9.apply(startup, {last: nb9.entry(startup, last) + bytes(2048)})
        files[iso.ELF_NAME] = bytes(elf) + bytes(1 << 20)
        moved = build.write(clean, out, files)
        print('stress: moved %s' % moved)


if __name__ == '__main__':
    main()

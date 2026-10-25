"""Rebuild the disc image with the translation and produce a patch.

Output: build/nobunaga_ranseiki_puk_en.bin, a .cue next to it and the xdelta patch.

usage: .venv/bin/python tools/build.py [clean.bin] [out.bin]
"""
import os, subprocess, sys

sys.path.insert(0, os.path.dirname(__file__))
import elfseg, elfstr, gfx, iso, msgbuf, msgfile, nameentry, namehook, names, nb9, overflow, scehead, setup, tl, uifix, vwf

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, 'build', 'nobunaga_ranseiki_puk_en.bin')


def elfstr_keys():
    """Debug build (env ELFSTR_KEYS=1): every translated ELF string shows its own key
    (e6e2f10 ...) to find which entry a label on screen comes from. None = normal build."""
    if not os.environ.get('ELFSTR_KEYS'):
        return None
    rows = elfstr.rows()
    en = elfstr.resolved(elfstr.load_tl(), rows)
    room = {r['key']: (r['room'], r['movable']) for r in rows}
    # format strings keep their English (conversions must match); keys that don't fit a
    # slot that can't move keep their English too
    return {k: (v if '%' in v or (not room[k][1] and room[k][0] < len(k)) else k) for k, v in en.items() if k in room}
# sector-rounded size limits of the fixed buffers the game loads these packs into (survey part A)
BUFFERS = {'MEDIA/DATA/COMMON.NB9': 0x55000, 'MEDIA/DATA/FONTTEX.NB9': 0x127000,
           'MEDIA/DATA/MAINCG.NB9': 0x400000, 'MEDIA/WAR/WARCG.NB9': 0x1E000}
SLOT2 = 0x200000                        # UI / editor / mission packs share this buffer


def build_files(disc):
    """{iso path: bytes} of every rebuilt file."""
    elf = bytearray(disc.file(iso.ELF_NAME))
    clean_elf = bytes(elf)
    files = {}
    seg = elfseg.Segment()
    msgbuf.apply(elf)
    width_table = vwf.apply(elf, seg)               # VWF advances (nameentry's cursor x)
    print("uifix: render node drop counter at %#x" % uifix.apply(elf, seg))
    if os.environ.get("SEG_PAD"):
        seg.reserve(int(os.environ["SEG_PAD"], 0))         # layout experiments
    nstr = moved = 0
    if not os.environ.get("NO_ELFSTR"):                    # experiments: skip the ELF strings
        try:
            nstr, moved = elfstr.apply(elf, clean_elf, seg, elfstr_keys())
        except ValueError as e:
            sys.exit('elfstr:\n%s' % e)
    if not os.environ.get("NO_NAMEENTRY"):                 # Latin name-entry grid (PUK editors)
        nameentry.apply(elf, seg, width_table)
    nnames = 0
    if not os.environ.get("NO_NAMEHOOK"):
        scens = [disc.file(names.SCEN_FILE % k) for k in names.SCENARIOS]
        nnames = len(namehook.apply(elf, clean_elf, seg, scens)[0])
    if os.environ.get("LABEL_LOG"):                        # debug: log label widgets (tools/dev/labellog.py)
        from dev import labellog
        labellog.apply(elf, seg)
    fonttex = disc.file(vwf.FONTTEX)
    files[vwf.FONTTEX] = nb9.apply(fonttex, {vwf.SHEET: vwf.sheet(nb9.entry(fonttex, vwf.SHEET))})
    assert len(files[vwf.FONTTEX]) == len(fonttex), 'FONTTEX must keep its size (exact-fit buffer)'
    if not os.environ.get("NO_GFX"):
        try:
            tex = gfx.apply(disc.file)
        except ValueError as e:
            sys.exit(str(e))
        for name, data in tex.items():
            assert len(data) == len(disc.file(name)), name
        files.update(tex)
        print('gfx: %d textures in %d packs' % (len(gfx.load_tl()), len(tex)))
    sce_tl = scehead.load_tl(disc)
    files[scehead.FILE] = scehead.apply(disc.file(scehead.FILE), sce_tl)      # scenario titles (settings page)
    for k in names.SCENARIOS:
        files[names.SCEN_FILE % k] = scehead.apply(disc.file(names.SCEN_FILE % k), sce_tl, 1)
        files[scehead.VAR_FILE % k] = scehead.apply(disc.file(scehead.VAR_FILE % k), sce_tl, 1)
    english, bad = tl.build_all(quiet=True)
    if bad:
        sys.exit('tl/ has %d errors (run tools/tl.py check)' % len(bad))
    growth = 0
    for n, name in msgfile.NAMES.items():
        if english[n]:
            files[name] = tl.block_bytes(n, english[n])
            growth += len(files[name]) - len(disc.file(name))
    heap = elfseg.apply(elf, seg)
    growth += heap - elfseg.BASE
    if growth > overflow.HEAP_GROWTH:
        sys.exit('message blocks + segment grew the heap use by %d bytes > %d (budget, overflow.py)' % (growth, overflow.HEAP_GROWTH))
    print('elfstr %d strings (%d moved); namehook %d names; segment %d bytes, heap starts at %#x'
          % (nstr, moved, nnames, len(seg.data), heap))
    files[iso.ELF_NAME] = bytes(elf)
    return files


def check_buffers(disc, files):
    """Sector-rounded sizes within the game's fixed buffers; ELF table copies in sync."""
    elf = files.get(iso.ELF_NAME) or disc.file(iso.ELF_NAME)
    for name, data in files.items():
        n = -(-len(data) // iso.SECTOR) * iso.SECTOR
        limit = BUFFERS.get(name, SLOT2 if name.endswith('.NB9') and name.split('/')[-1] not in
                            ('MESSAGE.NB9', 'KRETSU.NB9', 'EVENT.NB9', 'COMBG.NB9', 'SHIROCG.NB9', 'KAOCG.NB9',
                             'ITEMCG.NB9', 'WARMAPCG.NB9', 'WMAPDATA.NB9') and '/SCENA' not in name else None)
        if limit and n > limit:
            sys.exit('%s: %d bytes exceed its %#x buffer' % (name, len(data), limit))
        if name in nb9.ELF_TOC and nb9.ELF_TOC[name][2] and name != 'MEDIA/DATA/COMBG.NB9':
            if nb9.elf_toc(elf, name) != nb9.toc(data):
                sys.exit('%s: the ELF copy of its table is out of date' % name)


def write(clean, out, files):
    """Write the image, check it, write the .cue and make the xdelta patch next to it."""
    os.makedirs(os.path.dirname(out), exist_ok=True)
    moved = iso.write_image(clean, out, files)
    print('rebuilt %s%s' % (out, ' (moved: %s)' % ', '.join(moved) if moved else ''))
    files = dict(files)
    files[iso.ELF_NAME] = iso.read_file(out, iso.ELF_NAME)     # with the patched LBA table
    iso.verify(out, files)
    iso.write_cue(os.path.splitext(out)[0] + '.cue', out)
    patch = os.path.splitext(out)[0] + '.xdelta'
    try:
        subprocess.run(['xdelta3', '-e', '-f', '-9', '-S', 'djw', '-B', str(1 << 30), '-s', clean, out, patch], check=True)
        print('patch %s (%d bytes)' % (patch, os.path.getsize(patch)))
    except FileNotFoundError:
        print('xdelta3 not installed; skipped patch')
    return moved


def main():
    clean = sys.argv[1] if len(sys.argv) > 1 else setup.CLEAN
    out = sys.argv[2] if len(sys.argv) > 2 else OUT
    disc = iso.Disc(clean)
    files = build_files(disc)
    check_buffers(disc, files)
    write(clean, out, files)


if __name__ == '__main__':
    main()

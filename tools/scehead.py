"""Scenario headers: the first 0x1000 bytes of every SCEFIXnn.NB9 (and the 14-block cache
MEDIA/SCENA1/SCEFHEAD.NB9, chronological, SCEFIX13 missing) are read by the serializer at 0x1F622C
(intro +0x54, 0x143 bytes; title +0x197, 0x80 bytes). The title ("1534年  5月 信長誕生") is the
scenario line of the settings page (read from the chosen SCEFIX, not from SCEFHEAD); the
scenario-select names are ELF strings (e640cc0.., tl/elfstr/w2.txt) and the two must agree.

The intro is the scenario-select description (7 lines x 24 full-width cells, explicit newlines).

tl/names/scenarios.json: [{"scenario": n, "en": title, "intro": text}] (n = SCEFIXnn); the Japanese is read
from the disc. The intro is wrapped here to INTRO_PX with the VWF widths and must fit INTRO_LEN - 1 bytes. `python tools/scehead.py` prints the titles, `check` validates. build.py applies it to SCEFHEAD (all blocks), each SCEFIX (header +
a trailer copy at ~+0x37404) and each SCEVAR (header). The settings page shows the title with the file's
date padded to the Japanese 11 bytes + a space (the scenario-select map plate copies those 11 bytes) (half-width spaces are 5 px in the VWF).
"""
import json, os, sys

FILE = 'MEDIA/SCENA1/SCEFHEAD.NB9'
VAR_FILE = 'MEDIA/SCENA2/SCEVAR%02d.NB9'      # scenario variables, same header block
BLOCK, TITLE, TITLE_LEN = 0x1000, 0x197, 0x80
INTRO, INTRO_LEN, INTRO_PX, INTRO_LINES = 0x54, 0x143, 520, 7   # box 24 x 22 px (s27)
TL = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'tl', 'names', 'scenarios.json')

def titles(data, blocks=None):
    """Japanese titles of the (first `blocks`) header blocks."""
    out = []
    for b in range(0, len(data) if blocks is None else blocks * BLOCK, BLOCK):
        t = data[b + TITLE:b + TITLE + TITLE_LEN].split(b'\0')[0]
        out.append(t.decode('shift_jis'))
    return out

def intro(data):
    """Japanese intro of the first header block."""
    return data[INTRO:INTRO + INTRO_LEN].split(b'\0')[0].decode('shift_jis')

def wrap(text):
    """English intro with newlines at INTRO_PX (tl.py VWF widths); checks lines and bytes."""
    import tl
    w = tl.widths()
    px = lambda s: sum(w.get(c, tl.HALF_PX) for c in s)
    lines, cur = [], ''
    for word in text.split(' '):
        c = cur + ' ' + word if cur else word
        if cur and px(c) > INTRO_PX:
            lines.append(cur)
            c = word
        cur = c
    out = '\n'.join(lines + [cur])
    if len(lines) + 1 > INTRO_LINES or len(out.encode('shift_jis')) >= INTRO_LEN:
        raise ValueError('scenarios.json: intro too long (%d lines, %d bytes): %r'
                         % (len(lines) + 1, len(out.encode('shift_jis')), text[:40]))
    return out

def load_tl(disc):
    """Japanese title and intro (from the scenario's SCEFIX header) -> English."""
    import names
    out = {}
    for r in json.load(open(TL, encoding='utf-8')):
        data = disc.file(names.SCEN_FILE % r['scenario'])
        if r.get('en'):
            out[titles(data, 1)[0]] = r['en']
        if r.get('intro'):
            out[intro(data)] = wrap(r['intro'])
    return out

def apply(data, tl, blocks=None):
    """File bytes with the English titles: every NUL-terminated copy of a known Japanese title with
    room for the English (the +0x197 header field is 128 bytes; SCEFIX also keeps a copy in a trailer
    record near its end, SCEVAR a header of its own). tl: load_tl(); blocks: header blocks the file must have."""
    out = bytearray(data)
    for jp in titles(data, blocks):
        if jp not in tl:
            raise ValueError('scenarios.json: no English for %r' % jp)
    n = 0
    for jp, en in tl.items():
        j, e = jp.encode('shift_jis') + b'\0', en.encode('shift_jis') + b'\0'
        i = -1
        while True:
            i = data.find(j, i + 1)
            if i < 0:
                break
            if data[i + len(j):i + len(e)] != bytes(max(0, len(e) - len(j))):
                raise ValueError('%r at %#x: no room for %r' % (jp, i, en))
            out[i:i + max(len(j), len(e))] = e.ljust(len(j), b'\0')
            n += 1
    return bytes(out)

if __name__ == '__main__':
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import iso, names, setup
    disc = iso.Disc(setup.CLEAN)
    tl = load_tl(disc) if os.path.exists(TL) else {}
    for k in names.SCENARIOS:
        jp = titles(disc.file(names.SCEN_FILE % k), 1)[0]
        print('s%d' % k, jp, '=>', tl.get(jp, ''))
    if 'check' in sys.argv:
        apply(disc.file(FILE), tl)
        for k in names.SCENARIOS:
            apply(disc.file(names.SCEN_FILE % k), tl, 1)
            apply(disc.file(VAR_FILE % k), tl, 1)
        print('scenarios ok')

"""Proportional Latin text (VWF): new half-width glyphs and the advances that use them.

The game has one text size (24 px rows) and one renderer (0x59C610, survey part B).
Half-width characters (0x20-0x7F, 0xA2-0xDF) come from one 192x384 4bpp sheet, FONTTEX
object 2859: cell = code, 12x24 (x0 = (code & 15) * 12, y0 = (code >> 4) * 24); the
draw loop always advances 12. The glyph palette (objects 2860-2875, one per text colour)
is a brightness ramp: 1 transparent, 2 dim edge .. 14 body (0 and 15 unused), no shadow.

Changes:
- sheet cells 0x20-0x7E are redrawn with tools/latinfont.py ('msg', ink from column 1:
  a sprite also samples one texel of the next cell); kana cells stay; size unchanged
  (in-place patch, FONTTEX keeps its exact buffer size);
- W = 256-byte width table in the English segment: Latin = latinfont advance, kana and
  SJIS lead bytes 12, control bytes 0;
- draw loop 0x59C610: the half-width advance 0x59C81C (`addiu s1,s1,12; b 0x59CA54`,
  code in s2) jumps to a stub adding W[code];
- line measure 0x59D350 (centre/right alignment): +12 at 0x59D3A8 (v0, code in a1) and
  text bbox 0x59D460 (13 callers): +12 at 0x59D4E0 (t0, code in a3) likewise.
- half-width glyph clip 0x59CC14: the game draws min(12, right - x) px, but the sprite takes its
  texture offset as col * width (0x157B94: u = col * w, v = row * h), so a clipped glyph samples
  the wrong cell ("Goal" -> "Goak" at a label's right edge). CLIP: a glyph that starts inside
  the clip rect is drawn whole (12 px); one past the edge is still skipped.
- SQUEEZE (emulator pass): English wider than its text object is squeezed to fit instead of
  clipped (the Japanese always fits). At each line start (entry 0x59C700, after a newline
  0x59C78C) a stub measures the line (0x59D350) and sets SCALE = s5 * 256 / width when the
  width exceeds the text object's width s5 (gp-0x5634, set by the 0x59C610 wrappers), else
  256; the game itself already left-aligns a line wider than s5. The half-width advance adds
  W[code] * SCALE >> 8 (glyphs overlap; sprites stay 12 px). SCALE never drops below
  SQUEEZE_MIN / 256 (beyond that the tail clips as before).
"""
import os, struct, sys

sys.path.insert(0, os.path.dirname(__file__))
import elfseg, latinfont
from asm import assemble, hi, lo

FONTTEX = 'MEDIA/DATA/FONTTEX.NB9'
SHEET = 2859
CELL_W, CELL_H, SHEET_W, SHEET_H = 12, 24, 192, 384
TEXOBJ_ROUND = 0x59BE54                  # text object ctor 0x59BDA0: width rounded to even
TEXOBJ_ROUND_OLD = (0x32020001, 0x06010004, 0, 0x10400002, 0, 0x2442FFFE, 0x10400002, 0, 0x2610FFFF)
DRAW_ADV, DRAW_DONE = 0x59C81C, 0x59CA54
MEAS_ADV, MEAS_DONE = 0x59D3A8, 0x59D44C
BBOX_ADV, BBOX_DONE = 0x59D4E0, 0x59D584
CLIP = 0x59CC14                          # slti v1,s0,12; bnez v1,+2; nop; addiu s0,zero,12
CLIP_OLD = (0x2A03000C, 0x14600002, 0, 0x2410000C)
LINE_START = 0x59C700                    # move s0,zero; b DRAW_DONE (every line-1 path joins here)
LINE_START_OLD = (0x70008628, 0x100000D3)     # por s0,zero,zero (move)
NEWLINE = 0x59C78C                       # addiu s0,s0,24 (0x59C790 slti v1,s2,0x20 is a branch target)
NEWLINE_OLD = (0x26100018, 0x2A430020)
MEASURE = 0x59D350                       # line width of a0 (stops at \n / NUL; leaf)
SQUEEZE_MIN = int(os.environ.get("SQUEEZE_MIN", 128))   # env 256 = off (the goal-window crash was uifix SLOTS, session 15)
CLEAR, EDGE, BODY = 1, 2, 14             # palette ramp indices
TRIM_LEFT = {'j': 2}

def glyph_cells():
    """{code: [[index]]} 12x24 palette indices of the new Latin glyphs, and {code: advance}."""
    font = latinfont.render('msg')
    top = latinfont.SIZES['msg'][5]
    cells, adv = {}, {}
    for c, (rows, a) in font.items():
        if c in TRIM_LEFT:
            # the descender hook of j sits left of the stem: a gap after the previous letter
            n = TRIM_LEFT[c]
            rows, a = [r[n:] + [0] * n for r in rows], a - n
        # column 0 stays blank (sprites sample one texel of the next cell); shifting every
        # glyph keeps the spacing
        out = []
        for y in range(CELL_H):
            row = []
            for x in range(CELL_W):
                v = (rows[y][x - 1] / top) if x else 0
                row.append(CLEAR if v < 0.1 else max(EDGE, min(BODY, CLEAR + round(v * (BODY - CLEAR)))))
            out.append(row)
        cells[ord(c)] = out
        adv[ord(c)] = a
    return cells, adv

def widths():
    """256-byte advance table."""
    _, adv = glyph_cells()
    t = bytearray(256)
    for c, a in adv.items():
        t[c] = a
    for c in list(range(0xA1, 0xE0)) + list(range(0x81, 0xA0)) + list(range(0xE0, 0xFD)):
        t[c] = CELL_W
    return bytes(t)

def sheet(obj):
    """FONTTEX object 2859 bytes with the Latin cells of the half-width sheet redrawn."""
    e = bytearray(obj)
    assert e[:4] == b'N9GD' and e[0x30:0x34] == b'TIM2', 'not the font sheet object'
    assert struct.unpack_from('<HH', e, 0x30 + 16 + 0x14) == (SHEET_W, SHEET_H), 'sheet size changed'
    img = 0x70
    cells, _ = glyph_cells()
    for code, rows in cells.items():
        x0, y0 = (code & 15) * CELL_W, (code >> 4) * CELL_H
        for y, row in enumerate(rows):
            for x, v in enumerate(row):
                p = (y0 + y) * SHEET_W + x0 + x
                k = img + p // 2
                e[k] = (e[k] & 0x0F) | (v << 4) if p & 1 else (e[k] & 0xF0) | v
    assert len(e) == len(obj)
    return bytes(e)

def apply(elf, seg):
    """Width table + stubs into the segment, hooks into the ELF. Returns the width table address."""
    table = seg.add(widths(), 4)
    scale = seg.add(struct.pack('<I', 256), 4)
    w = lambda va: elfseg.word(elf, va)
    # SQUEEZE_LOG=1 (debug builds): every squeezed line appends (string, s5, width, mode) to a 64-entry
    # ring in the segment; the word before the ring = next byte offset (read it over PINE)
    log = []
    if os.environ.get('SQUEEZE_LOG'):
        ring = seg.add(bytes(4 + 64 * 16), 16) + 4
        print('vwf: squeeze log ring at %#x (offset word %#x)' % (ring, ring - 4))
        log = [
            ('lui', 'at', hi(ring - 4)),
            ('lw', 't8', lo(ring - 4), 'at'),
            ('andi', 't8', 't8', 63 * 16),
            ('lui', 'v1', hi(ring)),
            ('addiu', 'v1', 'v1', lo(ring)),
            ('addu', 'v1', 'v1', 't8'),
            ('sw', 's3', 0, 'v1'),
            ('sw', 's5', 4, 'v1'),
            ('sw', 'v0', 8, 'v1'),
            ('sw', 's6', 12, 'v1'),
            ('addiu', 't8', 't8', 16),
            ('sw', 't8', lo(ring - 4), 'at')]
    def line_scale(tail):
        return seg.code([
            ('jal', MEASURE),
            ('addu', 'a0', 's3', 'zero'),
            ('ori', 't9', 'zero', 256),
            ('slt', 'at', 's5', 'v0'),          # line wider than the text object?
            ('beqz', 'at', 'store'),
            ('slt', 'at', 'zero', 's5'),
            ('beqz', 'at', 'store'),
            ('sll', 't8', 's5', 8),
            ('div', 't8', 'v0'),
            ('mflo', 't9'),
            ('sltiu', 'at', 't9', SQUEEZE_MIN),
            ('beqz', 'at', 'log'),
            ('nop',),
            ('ori', 't9', 'zero', SQUEEZE_MIN),
            'log:'] + log + [
            'store:',
            ('lui', 'at', hi(scale)),
            ('sw', 't9', lo(scale), 'at')] + tail)
    assert (w(LINE_START), w(LINE_START + 4)) == LINE_START_OLD, 'line start site changed'
    assert (w(NEWLINE), w(NEWLINE + 4)) == NEWLINE_OLD, 'newline site changed'
    stub = line_scale([('addu', 's0', 'zero', 'zero'), ('j', DRAW_DONE), ('nop',)])
    elfseg.patch(elf, LINE_START, assemble(LINE_START, [('j', stub), ('nop',)]))
    stub = line_scale([('addiu', 's0', 's0', 24), ('sltiu', 'v1', 's2', 0x20), ('j', NEWLINE + 8), ('nop',)])
    elfseg.patch(elf, NEWLINE, assemble(NEWLINE, [('j', stub)]))     # delay slot = the original slti
    hooks = (
        # (site, expected words, code register, advance register, continue address)
        (DRAW_ADV, (0x2631000C, 0x1000008C), 's2', 's1', DRAW_DONE),
        (MEAS_ADV, (0x2442000C, 0x10000027), 'a1', 'v0', MEAS_DONE),
        (BBOX_ADV, (0x2508000C, 0x10000027), 'a3', 't0', BBOX_DONE))
    for site, old, code, acc, done in hooks:
        assert (w(site), w(site + 4)) == old, 'hook site %x changed' % site
        squeeze = [] if site != DRAW_ADV else [
            ('lui', 'at', hi(scale)),
            ('lw', 't8', lo(scale), 'at'),
            ('mult', 't9', 't9', 't8'),
            ('srl', 't9', 't9', 8)]
        stub = seg.code([
            ('andi', 't9', code, 0xFF),
            ('lui', 'at', hi(table)),
            ('addu', 'at', 'at', 't9'),
            ('lbu', 't9', lo(table), 'at')] + squeeze + [
            ('j', done),
            ('addu', acc, acc, 't9')])
        elfseg.patch(elf, site, assemble(site, [('j', stub), ('nop',)]))
    assert tuple(w(CLIP + 4 * i) for i in range(4)) == CLIP_OLD, 'clip site changed'
    elfseg.patch(elf, CLIP, assemble(CLIP, [('bltz', 's0', CLIP + 16), ('nop',), ('nop',), ('addiu', 's0', 'zero', 12)]))
    return table

if __name__ == '__main__':
    cells, adv = glyph_cells()
    for ch in sys.argv[1:] or 'Ag':
        print(ch, adv[ord(ch)])
        for r in cells[ord(ch)]:
            print(''.join('.123456789ABCDEF'[v] for v in r))

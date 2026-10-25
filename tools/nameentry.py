"""Name entry screen (PUK officer / treasure / event editors): Latin letters instead of kana/kanji.

Class: ctor 0x3F32D0(this, ?, text*, chars per line, lines, title, kana_only), vtable 0x68AF00,
init 0x3F3120 (notes/survey/nameentry_re.md). The text is a fixed byte buffer this+0x18C
(max bytes +0x220 = 2 * chars * lines, cursor byte index +0x188, insert flag +0x155) edited in
2-byte steps; the grid cells are 2-byte slices of row strings (row table 0x622A50: tab * 10 + row,
blank cell = "  "). Changes:
  - the 45 grid rows point to ASCII rows in the segment (cells "A\\0"; a real space " \\0",
    shown as "Sp"); the blank test (0x3F1C74) compares the whole cell with "  "
  - tab 0 (Kanji) no longer opens the kanji pages (0x3F0DA4) and has 10 columns like the rest
  - every tab is Latin; the screen opens on tab 3 (ABC) and the kana-only flag (+0x234, reading
    fields) no longer restricts the tab cycle (0x3F0FF4)
  - type (0x3F0ED8), delete (0x3F0B38) and L1/R1 (0x3F10B8, 0x3F10C8, 0x3F115C) work on bytes;
    a typed character never makes the text longer than max; delete at the end of the text
    removes the last character
  - the text is drawn as one string (VWF) and the cursor x is the VWF width up to the cursor
    (0x3F1410); the cursor box is 12 px wide (0x3F1B10 / 0x3F1B20)
  - grid cells are centred in their 24 px box (0x3F15A4, 0x3F1720)
  - the text starts empty when the current name is not ASCII or longer than max (Japanese
    record names), else with the cursor at its end (0x3F3394)
'%', '{', '}', '~' and '\\' are left out: they mean something to the message expander.
"""
import struct, sys, os

sys.path.insert(0, os.path.dirname(__file__))
import asm, elfseg
from asm import hi, lo

ROWS = 0x622A50                      # 45 row pointers (tab * 10 + row; tab 4 has 5 rows), then 0
ROWS_FIRST, NROWS = 0x64F4C0, 45
DRAW_TEXT = 0x59C1B0                 # (text object, x, y, string, t0 = 0)
UPPER = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
LOWER = UPPER.lower()
DIGITS = '1234567890'
PUNCT = "&!?(),:/+="
SYMBOLS = "-'.,&!?()[]<>:;/+=#*@$^_\"`|"
SP = ' '

def rows(*lines, n=10):
    """n grid rows of 10 cells; a line is padded with blank cells, '\\0' = blank."""
    out = []
    for ln in lines + ('',) * (n - len(lines)):
        assert len(ln) <= 10, ln
        out.append([None if c == '\0' else c for c in ln] + [None] * (10 - len(ln)))
    assert len(out) == n
    return out

# 10 rows x 10 cells per tab (tab 4: 5 rows); columns 0-4 and 5-9 are two blocks
U = (UPPER[:10], UPPER[10:20], UPPER[20:] + SP + "-'.")
L = (LOWER[:10], LOWER[10:20], LOWER[20:] + SP + "-'.")
TABS = [
    rows(*U, '', DIGITS, PUNCT),                                    # 0 label Kanji
    rows(*L, '', DIGITS, PUNCT),                                    # 1 label Hira
    rows(DIGITS, '', SP + "-'.", PUNCT, '', SYMBOLS[:10], SYMBOLS[10:20], SYMBOLS[20:]),   # 2 label Kata
    rows(*U, *L, DIGITS, PUNCT),                                    # 3 label ABC (opens here)
    rows(SP + SYMBOLS[:9], SYMBOLS[9:19], SYMBOLS[19:], n=5),       # 4 label Symbols
]
START_TAB = 3

# (address, original word, new instruction)
SIMPLE = [
    (0x3F0DA4, 0x1440004C, ('b', 0x3F0ED8)),               # bnez v0 (tab != 0): tab 0 = kanji pages
    (0x3F1520, 0x24140005, ('addiu', 's4', 'zero', 10)),   # draw: tab 0 columns
    (0x3F1C20, 0x24060005, ('addiu', 'a2', 'zero', 10)),   # selectable mask: tab 0 columns
    (0x3F1C74, 0x90E80000, ('lhu', 't0', 0, 'a3')),        # blank cell = "  " (whole cell)
    (0x3F1C78, 0x24070020, ('addiu', 'a3', 'zero', 0x2020)),
    (0x3F0FF4, 0x1040000B, ('b', 0x3F1024)),               # tab cycle ignores the kana-only flag
    (0x3F316C, 0x92020234, ('addiu', 'v0', 'zero', START_TAB)),   # init tab = kana_only ? 1 : 0
    (0x3F3170, 0x10400004, ('b', 0x3F3188)),
    (0x3F3174, 0, ('sw', 'v0', 0x160, 's0')),
    (0x3F10B8, 0x2442FFFE, ('addiu', 'v0', 'v0', -1)),     # R1: pos < max - 2
    (0x3F10C8, 0x24620002, ('addiu', 'v0', 'v1', 1)),      # R1: pos += 2
    (0x3F115C, 0x2442FFFE, ('addiu', 'v0', 'v0', -1)),     # L1: pos -= 2
]
TYPE, TYPE_DONE = 0x3F0ED8, 0x3F0F68        # cell confirmed (s0 = this); continue at the redraw
DELETE, DELETE_DONE = 0x3F0B38, 0x3F0B70    # delete at the cursor (a0 = this)
DRAW, DRAW_DONE = 0x3F1410, 0x3F14D8        # text loop (s5 = this)
CURSOR_RECTS = [(0x3F1B10, 0x24452A40), (0x3F1B20, 0x24452A30)]   # lui v0,0x62 + addiu a1 (insert, overwrite)
CELL_DRAWS = [0x3F15A4, 0x3F1720]           # jal DRAW_TEXT of a grid cell / the cell under the cursor
INIT_CALL, INIT = 0x3F3394, 0x3F3120        # ctor: jal init
BUF, POS, MAX, INSERT, CELL, SRC = 0x18C, 0x188, 0x220, 0x155, 0x88, 0x22C
BUF_CLEAR = 0x92                            # bytes init zeroes

def word(elf, va):
    return elfseg.word(elf, va)

def apply(elf, seg, width_table):
    for va, old, _ in SIMPLE:
        assert word(elf, va) == old, 'name entry site %x changed' % va
    for i in range(NROWS):
        want = ROWS_FIRST + (0x10 * i if i < 10 else 0xA0 + 0x20 * (i - 10))
        if i in (38, 39):                       # two blank ABC rows share one string
            want = 0x64F8E0
        if i > 39:
            want = 0x64F900 + 0x20 * (i - 40)
        assert word(elf, ROWS + 4 * i) == want, 'grid row table changed at %d' % i
    assert word(elf, ROWS + 4 * NROWS) == 0
    assert (word(elf, TYPE), word(elf, DELETE), word(elf, DRAW), word(elf, DRAW_DONE)) == \
        (0x92020155, 0x8C850188, 0x1000002D, 0x8EA4036C), 'name entry code changed'
    assert word(elf, INIT_CALL) == 0x0C000000 | INIT >> 2 and word(elf, INIT_CALL + 4) == 0
    for va in CELL_DRAWS:
        assert word(elf, va) == 0x0C000000 | DRAW_TEXT >> 2
    for va, addiu in CURSOR_RECTS:
        assert (word(elf, va), word(elf, va + 4)) == (0x3C020062, addiu)

    # grid rows: 10 two-byte cells + NUL; blank = "  "
    for t, tab in enumerate(TABS):
        for r, row in enumerate(tab):
            b = b''.join(b'  ' if c is None else c.encode('ascii') + b'\0' for c in row) + b'\0\0'
            at = seg.add(b, 4)
            elfseg.patch(elf, ROWS + 4 * (t * 10 + r), struct.pack('<I', at))
    for va, _, ins in SIMPLE:
        elfseg.patch(elf, va, asm.assemble(va, [ins]))

    # type a character: insert / overwrite one byte at the cursor, never past max
    stub = seg.code([
        ('lbu', 't0', CELL, 's0'),
        ('beqz', 't0', 'out'),
        ('lw', 't1', POS, 's0'),
        ('lw', 't2', MAX, 's0'),
        ('addiu', 't3', 's0', BUF),
        ('or', 't4', 'zero', 'zero'),
        'len:',
        ('addu', 't5', 't3', 't4'),
        ('lbu', 't6', 0, 't5'),
        ('beqz', 't6', 'lend'),
        ('nop',),
        ('b', 'len'),
        ('addiu', 't4', 't4', 1),
        'lend:',
        ('lbu', 't6', INSERT, 's0'),
        ('beqz', 't6', 'put'),                 # overwrite (pos <= len < max or pos < len)
        ('slt', 'at', 't4', 't2'),
        ('beqz', 'at', 'out'),                 # insert into a full text: ignored
        ('or', 't5', 't4', 'zero'),
        'shift:',
        ('slt', 'at', 't1', 't5'),
        ('beqz', 'at', 'put'),
        ('addu', 't6', 't3', 't5'),
        ('lbu', 't7', -1, 't6'),
        ('sb', 't7', 0, 't6'),
        ('b', 'shift'),
        ('addiu', 't5', 't5', -1),
        'put:',
        ('addu', 't6', 't3', 't1'),
        ('sb', 't0', 0, 't6'),
        ('addiu', 't1', 't1', 1),
        ('slt', 'at', 't1', 't2'),
        ('beqz', 'at', 'last'),
        ('nop',),
        ('b', 'store'),
        ('nop',),
        'last:',
        ('addiu', 't1', 't2', -1),
        'store:',
        ('sw', 't1', POS, 's0'),
        'out:',
        ('j', TYPE_DONE),
        ('nop',)])
    elfseg.patch(elf, TYPE, asm.assemble(TYPE, [('j', stub), ('nop',)]))

    # delete the character at the cursor; at the end of the text, the one before it
    stub = seg.code([
        ('lw', 't1', POS, 'a0'),
        ('addiu', 't3', 'a0', BUF),
        ('addu', 't5', 't3', 't1'),
        ('lbu', 't6', 0, 't5'),
        ('bnez', 't6', 'del'),
        ('nop',),
        ('beqz', 't1', 'done'),
        ('addiu', 't1', 't1', -1),
        ('sw', 't1', POS, 'a0'),
        ('addiu', 't5', 't5', -1),
        'del:',
        ('lbu', 't6', 1, 't5'),
        ('sb', 't6', 0, 't5'),
        ('bnez', 't6', 'del'),
        ('addiu', 't5', 't5', 1),
        'done:',
        ('j', DELETE_DONE),
        ('nop',)])
    elfseg.patch(elf, DELETE, asm.assemble(DELETE, [('j', stub), ('nop',)]))

    # text: one VWF string at (0, 0) of text object +0x368; cursor sprite +0x370 at
    # (0x48 + width up to the cursor - 1, 0x40 - 1) as the game placed it
    stub = seg.code([
        ('lw', 'a0', 0x368, 's5'),
        ('or', 'a1', 'zero', 'zero'),
        ('or', 'a2', 'zero', 'zero'),
        ('addiu', 'a3', 's5', BUF),
        ('jal', DRAW_TEXT),
        ('or', 't0', 'zero', 'zero'),
        ('lw', 't1', POS, 's5'),
        ('addiu', 't3', 's5', BUF),
        ('lui', 't4', hi(width_table)),
        ('addiu', 't4', 't4', lo(width_table)),
        ('or', 't2', 'zero', 'zero'),
        'cx:',
        ('beqz', 't1', 'cdone'),
        ('lbu', 't5', 0, 't3'),
        ('addu', 't5', 't4', 't5'),
        ('lbu', 't5', 0, 't5'),
        ('addu', 't2', 't2', 't5'),
        ('addiu', 't3', 't3', 1),
        ('b', 'cx'),
        ('addiu', 't1', 't1', -1),
        'cdone:',
        ('lw', 'a0', 0x370, 's5'),
        ('addiu', 't2', 't2', 0x47),
        ('sw', 't2', 0x20, 'a0'),
        ('addiu', 't2', 'zero', 0x3F),
        ('sw', 't2', 0x24, 'a0'),
        ('j', DRAW_DONE),
        ('nop',)])
    elfseg.patch(elf, DRAW, asm.assemble(DRAW, [('j', stub), ('nop',)]))

    # cursor box: the game's (0,0,24,24) / (1,23,24,24) rects with 12 instead of 24 wide
    for (va, _), rect in zip(CURSOR_RECTS, [(0, 0, 12, 24), (1, 23, 12, 24)]):
        at = seg.add(struct.pack('<4I', *rect), 4)
        elfseg.patch(elf, va, asm.assemble(va, [('lui', 'v0', hi(at)), ('addiu', 'a1', 'v0', lo(at))]))

    # grid cell: centred in its 24 px box; the space cell shows "Sp"
    sp = seg.add(b'Sp\0', 4)
    stub = seg.code([
        ('lbu', 't1', 0, 'a3'),
        ('addiu', 't2', 'zero', 0x20),
        ('bne', 't1', 't2', 'meas'),
        ('lbu', 't3', 1, 'a3'),
        ('bnez', 't3', 'meas'),
        ('nop',),
        ('lui', 'a3', hi(sp)),
        ('addiu', 'a3', 'a3', lo(sp)),
        'meas:',
        ('or', 't2', 'zero', 'zero'),
        ('or', 't4', 'a3', 'zero'),
        ('lui', 't5', hi(width_table)),
        ('addiu', 't5', 't5', lo(width_table)),
        'ml:',
        ('lbu', 't1', 0, 't4'),
        ('beqz', 't1', 'md'),
        ('addu', 't3', 't5', 't1'),
        ('lbu', 't3', 0, 't3'),
        ('addu', 't2', 't2', 't3'),
        ('b', 'ml'),
        ('addiu', 't4', 't4', 1),
        'md:',
        ('addiu', 't3', 'zero', 24),
        ('slt', 'at', 't3', 't2'),
        ('movn', 't2', 't3', 'at'),
        ('subu', 't3', 't3', 't2'),
        ('srl', 't3', 't3', 1),
        ('j', DRAW_TEXT),
        ('addu', 'a1', 'a1', 't3')])
    for va in CELL_DRAWS:
        elfseg.patch(elf, va, asm.assemble(va, [('jal', stub)]))

    # initial text: empty unless ASCII and not longer than max; cursor at its end
    stub = seg.code([
        ('addiu', 'sp', 'sp', -16),
        ('sd', 'ra', 0, 'sp'),
        ('sd', 's0', 8, 'sp'),
        ('jal', INIT),
        ('or', 's0', 'a0', 'zero'),
        ('lw', 't0', SRC, 's0'),
        ('lw', 't2', MAX, 's0'),
        ('or', 't1', 'zero', 'zero'),
        'scan:',
        ('lbu', 't3', 0, 't0'),
        ('beqz', 't3', 'ok'),
        ('andi', 't4', 't3', 0x80),
        ('bnez', 't4', 'clear'),
        ('addiu', 't0', 't0', 1),
        ('addiu', 't4', 'zero', 0x0A),         # init skips newlines
        ('beq', 't3', 't4', 'scan'),
        ('nop',),
        ('addiu', 't1', 't1', 1),
        ('slt', 'at', 't2', 't1'),
        ('beqz', 'at', 'scan'),
        ('nop',),
        'clear:',
        ('addiu', 't0', 's0', BUF),
        ('addiu', 't1', 'zero', BUF_CLEAR),
        'cl:',
        ('sb', 'zero', 0, 't0'),
        ('addiu', 't1', 't1', -1),
        ('bnez', 't1', 'cl'),
        ('addiu', 't0', 't0', 1),
        ('b', 'ret'),
        ('nop',),
        'ok:',
        ('slt', 'at', 't1', 't2'),             # cursor = min(len, max - 1)
        ('bnez', 'at', 'pos'),
        ('nop',),
        ('addiu', 't1', 't2', -1),
        'pos:',
        ('sw', 't1', POS, 's0'),
        'ret:',
        ('ld', 'ra', 0, 'sp'),
        ('ld', 's0', 8, 'sp'),
        ('jr', 'ra'),
        ('addiu', 'sp', 'sp', 16)])
    elfseg.patch(elf, INIT_CALL, asm.assemble(INIT_CALL, [('jal', stub)]))
    return stub

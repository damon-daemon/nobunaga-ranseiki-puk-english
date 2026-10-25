"""Ranseiki PK message grammar (MESSAGE.NB9, KRETSU.NB9): split a message into text and codes.

Ported from Soutenroku's msgscript.py and checked against this ELF (SLPM_622.25): main loop
0x40DFA0 (bytes < 0x20 -> control dispatch 0x40D9D0; half-width kana 0xA6-0xDF are drawn as
hiragana via 0x40E650 unless {1B}k is active, a following DE/DF dakuten is merged; SJIS lead
0x81-0x9F/0xE0-0xFC = 2 bytes; ASCII passes).

  %  or 02         expression printed (expr 0x40E3B0); '%' alone = number term ('%' + <= 10
                   digits, 0x40E540), so a literal '%' in English must be full-width ％
  01 C lo hi       insert entry lo|hi<<8 of the same block    01 J lo hi  jump to it
  01 S expr        pick variant                   01 R expr   return value + end
  01 M 02 x expr   register (as Soutenroku)       01 F x [expr if x == 'd']
  01 other         that byte only                                 (commands 0x40D260)
  05 05 04 cond    IF (false -> skip to the matching END)         (keywords 0x40D760)
  05 05 05         END                            05 05 06-09  other (unused here)
  05 other         end of message
  1B/1A x          P S B F C take one more byte (S is new vs Soutenroku); k H K kana
                   modes; others 2 bytes
  1C               no handler in this game (1 byte, ignored); never used
  08 09 0A         to the renderer (0A newline)   other < 0x20: 1 byte, ignored

  expr = term (03 op term)*   cmp = expr (04 op expr)?   cond = cmp (06 &| cmp)*
  term (0x416280) = 02 x  (classifier 0x415DF0, handlers via table 0x64FCF0):
      x in the 9-wide bands 01-09, 0B-13, 15-1D ... B5-BD (types 0-18; persons are 0B-13):
         object, + a field byte if the next byte is >= 0x20 (0x64FB88)
      BF-C7 number parameter, C9-D1 string parameter: no field.
      D3-EE: function, arguments from FUNC (o = 02 x, e = expr); EF-FF: none.
    | '%' or a digit + <= 10 digits | any other byte (error flag, consumed)

A message is a block: (IF cond block)* text END. Text runs between structural codes
(IF/cond, END, S/M/R/F, end of message) are the translatable units; inline codes
(02 prints, %-fields, 01 C/J, 1B/1A, 08, 09, stray control bytes) stay inside them.
"""

FUNC = {0xd3: 'e', 0xd4: 'e', 0xd5: 'e', 0xd6: 'e', 0xd7: 'oo', 0xd8: 'oo', 0xd9: 'oo', 0xda: 'oo',
        0xdb: 'oo', 0xdc: 'o', 0xdd: '', 0xde: '', 0xdf: '', 0xe0: 'oo', 0xe1: '', 0xe2: '', 0xe3: '',
        0xe4: '', 0xe5: 'oo', 0xe6: 'oo', 0xe7: 'oo', 0xe8: 'o', 0xe9: 'o', 0xea: 'o', 0xeb: 'o',
        0xec: 'o', 0xed: 'o', 0xee: 'e'}
ESC1B = b'PSBFC'                 # 1B/1A codes with one argument byte

# Messages fetched with arguments are run through sprintf before the interpreter sees
# them: %s / %d / %5d are printf fields (kept as one code, in their order).
PRINTF = b'sd'

def is_field(t):
    return t[:1] == b'%' and t[-1:] in (b's', b'd')

def vcat(x):
    if 1 <= x <= 0xbd and x % 10 != 0:
        return 'field'
    if 0xbf <= x <= 0xc7 or 0xc9 <= x <= 0xd1:
        return 'plain'
    if x >= 0xd3:
        return 'func'
    return 'bad'

def vtype(x):
    """classifier type number (0-21) or None"""
    if x >= 0xd3:
        return 21
    if x == 0 or x % 10 == 0:
        return None
    return (x - 1) // 10

class ParseError(ValueError):
    pass

class _Reader:
    def __init__(self, b):
        self.b, self.i = b, 0

    def get(self):
        if self.i >= len(self.b):
            raise ParseError('unexpected end at %d' % self.i)
        self.i += 1
        return self.b[self.i - 1]

    def peek(self):
        return self.b[self.i] if self.i < len(self.b) else None

    def term(self):
        c = self.get()
        if c == 0x02:
            x = self.get()
            k = vcat(x)
            if k == 'field':
                if (d := self.peek()) is not None and d >= 0x20:
                    self.i += 1
            elif k == 'func':
                for a in FUNC.get(x, ''):
                    if a == 'o':
                        self.get(); self.get()
                    else:
                        self.expr()
        elif c == 0x25 or 0x30 <= c <= 0x39:
            n = 0
            while n < 10 and (d := self.peek()) is not None and 0x30 <= d <= 0x39:
                self.i += 1
                n += 1

    def expr(self):
        self.term()
        while self.peek() == 0x03:
            self.i += 1
            self.get()
            self.term()

    def cmp(self):
        self.expr()
        if self.peek() == 0x04:
            self.i += 1
            self.get()
            self.expr()

    def cond(self):
        self.cmp()
        while self.peek() == 0x06:
            self.i += 1
            self.get()
            self.cmp()

def sjis_lead(c):
    return 0x81 <= c <= 0x9f or 0xe0 <= c <= 0xfc

def tokens(b):
    """-> [(kind, bytes)], kind 'text' | 'nl' | 'inline' | 'if' | 'end' | 'cmd' | 'eom'.
    Concatenating the bytes gives b back."""
    r = _Reader(b)
    out = []
    def add(kind, st):
        if kind == 'text' and out and out[-1][0] == 'text':
            out[-1] = ('text', out[-1][1] + b[st:r.i])
        else:
            out.append((kind, b[st:r.i]))
    while r.i < len(b):
        st = r.i
        c = r.get()
        if c in (0x25, 0x02):
            r.i -= 1
            r.expr()
            if c == 0x25 and r.i - st <= 3 and r.peek() in PRINTF:
                r.i += 1                    # %s / %d / %5d: printf field
            add('inline', st)
        elif c >= 0x20:
            if sjis_lead(c) and r.peek() is not None:
                r.i += 1
            add('text', st)
        elif c == 0x0a:
            add('nl', st)
        elif c in (0x1b, 0x1a):
            x = r.get()
            if x in ESC1B:
                r.get()
            add('inline', st)
        elif c == 0x01:
            x = r.get()
            if x in (0x43, 0x4a):           # C, J
                if r.i + 2 > len(b):
                    r.i = len(b)
                    add('cmd', st)
                    continue
                r.i += 2
                add('inline', st)
                continue
            if x in (0x53, 0x52):           # S, R
                r.expr()
            elif x == 0x4d:                 # M
                if r.get() != 0x02:
                    raise ParseError('M without 02 at %d' % st)
                r.get()
                r.expr()
            elif x == 0x46:                 # F
                if r.get() == 0x64:
                    r.expr()
            add('cmd', st)
        elif c == 0x05:
            if r.peek() == 0x05 and r.i + 1 < len(b) and 4 <= b[r.i + 1] <= 9:
                k = b[r.i + 1]
                r.i += 2
                if k == 4:
                    r.cond()
                    add('if', st)
                elif k == 5:
                    add('end', st)
                else:
                    add('cmd', st)
            else:
                add('eom', st)
        else:
            add('inline', st)
    return out

INLINE = ('text', 'nl', 'inline')

def _visible(t):
    return t.replace(b'\x81\x40', b'').strip(b' ')

def units(b):
    """-> (toks, spans): spans = [(start, end)] token index ranges of text runs that
    hold visible text (anything but spaces)."""
    toks = tokens(b)
    spans, i = [], 0
    while i < len(toks):
        if toks[i][0] in INLINE:
            j = i
            while j < len(toks) and toks[j][0] in INLINE:
                j += 1
            if any(k == 'text' and _visible(t) for k, t in toks[i:j]):
                spans.append((i, j))
            i = j
        else:
            i += 1
    return toks, spans

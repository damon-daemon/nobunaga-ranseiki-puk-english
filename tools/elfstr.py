"""Strings of the ELF (menu labels, stat names, printf formats, system messages ...).

tl/elfstr/*.txt: "key<TAB>English" lines, key = e<vaddr> (hex); \\n = newline; \\e = ESC (colour codes
\\eCY..\\eCD); ASCII text;
English "-" = keep the Japanese (debug logs never shown); '#' starts a comment line. A
string without English takes the English of another string with the same Japanese
(translation memory). English that fits the string's slot (its bytes plus the compiler's
zero padding) is written in place. Longer English goes into the English data segment
(elfseg.py) and every reference is repointed:
  - 32-bit data words equal to the address (pointer tables),
  - lui/addiu (or ori / load / store) pairs in the code that build the address, when that
    lui has no other user (else the pair cannot be changed alone: shorten the English).
    Ranseiki builds most string addresses this way (Soutenroku was 90% pointer words).
A string with no reference found, or with a reference into its interior (lui/addiu or
pointer to byte k > 0 of the string), must fit its slot. printf conversions must match.

Not covered here (later tools): the name pools and the given-name/reading dictionary
(NAME_RANGES, names/namehook), the name-entry kana grid (GRID), the SJIS glyph table and
the pack TOC copies (TABLES; never text, excluded in case a word looks like SJIS).

  elfstr.py export            -> script/elfstr.json (every candidate, with flags)
  elfstr.py show [KEY KEY] [--todo]   strings to translate between two keys
  elfstr.py stats | check
Port of Soutenroku tools/elfstr.py (same key/file format; one image, no overlays).
"""
import collections, json, os, re, struct, sys

sys.path.insert(0, os.path.dirname(__file__))
import elfseg

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TL = os.path.join(ROOT, 'tl', 'elfstr')
SCRIPT = os.path.join(ROOT, 'script', 'elfstr.json')
ELF_NAME = 'SLPM_622.25'
FMT = re.compile(rb'%[-+ #0]*\d*(?:\.\d+)?[hl]?[diouxXcsfeEgGp%]')
BASE, FOFF, END = 0x100000, 0x80, 0x100000 + 0x5E3B00
CODE = [(0x100000, 0x60E610), (0x658C80, 0x678940)]       # main code, C++ static constructors
DATA = [(0x60E610, 0x658C80), (0x678940, END)]
SAVED = {16, 17, 18, 19, 20, 21, 22, 23, 28, 29, 30}       # s0-s7, gp, sp, fp: kept across calls

class Image:
    """One loaded file: data[foff + (va - base)] for va in [base, end)."""

    def __init__(self, tag, name, data, base, foff, end, code, words):
        self.tag, self.name, self.data = tag, name, data
        self.base, self.foff, self.end = base, foff, end
        self.code, self.words = code, words          # [(lo, hi)] ranges

    def f(self, va):
        assert self.base <= va < self.end, '%s %x' % (self.tag, va)
        return va - self.base + self.foff

    def w(self, va):
        return struct.unpack_from('<I', self.data, self.f(va))[0]

    def put(self, va, b):
        o = self.f(va)
        self.data[o:o + len(b)] = b

def images(elf):
    """[ELF] image over the given (mutable) file bytes (a list, like Soutenroku's)."""
    return [Image('e', ELF_NAME, elf, BASE, FOFF, END, CODE, DATA)]

def _s16(x):
    return x - 0x10000 if x & 0x8000 else x

def _writes(w):
    """Register written by instruction w (None if none / unknown)."""
    op = w >> 26
    if op in (0, 0x1C):
        return (w >> 11) & 31
    if op in (0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F, 0x18, 0x19, 0x1A, 0x1B, 0x1E, 0x37) or 0x20 <= op <= 0x27:
        return (w >> 16) & 31
    if op == 0x03:
        return 31
    return None

def _jump(w):
    """'call' (jal/jalr: temporaries are clobbered, s-registers survive), 'jump' (j/jr: the
    next block is reached from elsewhere) or None."""
    op = w >> 26
    if op == 3 or (op == 0 and (w & 0x3F) == 9):
        return 'call'
    if op == 2 or (op == 0 and (w & 0x3F) == 8):
        return 'jump'
    return None

def code_pairs(img):
    """({target: [(lui va, user va)]}, {lui va: number of users}) for the image's code.
    A lui register is followed until it is overwritten or a jump's delay slot ends (a call
    keeps the callee-saved registers); any other read of it counts as a user too (so such
    a lui is never changed)."""
    pairs, users = collections.defaultdict(list), collections.Counter()
    lui, end_after = {}, None
    for va in (a for lo, hi in img.code for a in range(lo, hi, 4)):
        w = img.w(va)
        op, rs, rt, imm = w >> 26, (w >> 21) & 31, (w >> 16) & 31, w & 0xFFFF
        if op == 0x0F:
            lui[rt] = (imm << 16, va)
        else:
            if rs in lui and (op in (0x09, 0x0D) or 0x20 <= op <= 0x3F):
                t = (lui[rs][0] + (imm if op == 0x0D else _s16(imm))) & 0xFFFFFFFF
                pairs[t].append((lui[rs][1], va))
                users[lui[rs][1]] += 1
                if 0x28 <= op <= 0x3F and rt in lui and rt != rs:     # stores the lui value
                    users[lui[rt][1]] += 2
            else:
                for r in {rs, rt}:
                    if r in lui:
                        users[lui[r][1]] += 2
            wr = _writes(w)
            if wr in lui:
                del lui[wr]
        if end_after is not None and va >= end_after[0]:
            if end_after[1] == 'jump':
                lui.clear()
            else:
                lui = {r: v for r, v in lui.items() if r in SAVED}
            end_after = None
        j = _jump(w)
        if j:
            end_after = (va + 4, j)
    return pairs, users

def data_words(img, lo, hi):
    """{value: [vaddr]} of the aligned words of the image's data pointing into [lo, hi)."""
    out = collections.defaultdict(list)
    for va in (a for lo_, hi_ in img.words for a in range(lo_, hi_ - 3, 4)):
        v = img.w(va)
        if lo <= v < hi:
            out[v].append(va)
    return out

class Refs:
    """Every reference to a string of each image, by image tag."""

    def __init__(self, imgs):
        self.imgs = {i.tag: i for i in imgs}
        self.code = {i.tag: code_pairs(i) for i in imgs}
        self.words = {i.tag: data_words(i, BASE, END) for i in imgs}

    def of(self, tag, va, n=0):
        """(code pairs [(img tag, lui, user)], data words [(img tag, at)], blockers [text])
        of the string of n bytes at va."""
        code, data, block = [], [], []
        for l, u in self.code[tag][0].get(va, []):
            code.append((tag, l, u))
            if self.code[tag][1][l] != 1:
                block.append('lui %x has other users' % l)
        for at in self.words[tag].get(va, []):
            data.append((tag, at))
        inner = [a for a in range(va + 1, va + n + 1) if self.code[tag][0].get(a) or self.words[tag].get(a)]
        if inner:
            block.append('referenced inside the string (+%s)' % ','.join('%d' % (a - va) for a in inner[:4]))
        return code, data, block

def slot(img, va):
    """(Japanese bytes, room) of the string at va: room = bytes up to the next 8-byte
    boundary after its NUL (the compiler's padding; zeros beyond it may be constants),
    and never past the next non-zero byte, minus one for the NUL."""
    b = img.data
    o = img.f(va)
    assert b[o - 1] == 0 or va % 4 == 0, '%s%x is not the start of a string' % (img.tag, va)
    e = b.index(b'\0', o)
    k = e
    while k < len(b) and b[k] == 0:
        k += 1
    end = min(k, (va + (e - o) + 1 + 7) // 8 * 8 - va + o)
    return bytes(b[o:e]), end - o - 1

def parse_key(k):
    return k[0], int(k[1:], 16)

def load_tl(d=TL):
    """{key: English} of every tl/elfstr file ('-' entries included)."""
    out = {}
    for fn in sorted(os.listdir(d)) if os.path.isdir(d) else []:
        if not fn.endswith('.txt'):
            continue
        path = os.path.join(d, fn)
        for n, line in enumerate(open(path, encoding='utf-8').read().splitlines(), 1):
            if line.strip() and not line.startswith('#'):
                k, tab, v = line.partition('\t')
                if not (tab and re.fullmatch('e[0-9a-f]+', k)):
                    raise ValueError('%s line %d: expected key<TAB>English: %r' % (fn, n, line))
                if k in out:
                    raise ValueError('%s line %d: %s given twice' % (fn, n, k))
                out[k] = v.replace('\\n', '\n').replace('\\e', '\x1b')
    return out

def resolved(tl, rows):
    """{key: English} to write: explicit English, else translation memory by Japanese;
    '-' entries dropped. rows = export rows (key, jp, ui)."""
    jp = {r['key']: r['jp'] for r in rows}
    mem = {}
    for k, v in tl.items():
        if v != '-' and k in jp:
            mem.setdefault(jp[k], v)
    out = {k: v for k, v in tl.items() if v != '-'}
    for r in rows:
        if r['ui'] and r['key'] not in tl and r['jp'] in mem:
            out[r['key']] = mem[r['jp']]
    return out

def encode(en):
    return en.encode('ascii')

def _hilo(new, uw):
    """(hi, lo) halves of address new for the pair instruction uw."""
    if uw >> 26 == 0x0D:                                  # ori: unsigned low half
        return new >> 16, new & 0xFFFF
    hi = (new + 0x8000) >> 16
    return hi, (new - (hi << 16)) & 0xFFFF

def apply(elf, clean, seg, tl=None):
    """Write tl/elfstr into elf (bytearray); clean = the clean ELF bytes; seg = elfseg.Segment.
    Returns (strings, moved). Raises ValueError listing every key that cannot be placed."""
    cimg = images(bytearray(clean))
    tl = resolved(load_tl() if tl is None else tl, survey(cimg))
    if not tl:
        return 0, 0
    img = images(elf)[0]
    refs = Refs(cimg)
    cimg = cimg[0]
    errors, moved, patched = [], 0, {}
    for key, en in sorted(tl.items()):
        tag, va = parse_key(key)
        jp, room = slot(cimg, va)
        try:
            b = encode(en)
        except UnicodeEncodeError:
            errors.append('elfstr %s: not ASCII: %r' % (key, en))
            continue
        if FMT.findall(b) != FMT.findall(jp):
            errors.append('elfstr %s: conversions %s, Japanese has %s' % (key, FMT.findall(b), FMT.findall(jp)))
            continue
        if len(b) <= room:
            img.put(va, b + bytes(room + 1 - len(b)))
            continue
        code, data, block = refs.of(tag, va, len(jp))
        if not code and not data:
            errors.append('elfstr %s: %d bytes, room %d and no reference to repoint' % (key, len(b), room))
            continue
        if block:
            errors.append('elfstr %s: %d bytes, room %d; %s' % (key, len(b), room, '; '.join(block)))
            continue
        new = seg.add(b + b'\0', 1)
        for _, at in data:
            img.put(at, struct.pack('<I', new))
        for _, l, u in code:
            lw, uw = cimg.w(l), cimg.w(u)
            assert (img.w(l), img.w(u)) == (lw, uw), 'elfstr %s: code at %x/%x already patched' % (key, l, u)
            hi, lo = _hilo(new, uw)
            img.put(l, struct.pack('<I', (lw & 0xFFFF0000) | hi))
            img.put(u, struct.pack('<I', (uw & 0xFFFF0000) | lo))
            patched[(l, u)] = new
        moved += 1
    if errors:
        raise ValueError('\n'.join(errors))
    if patched:
        _verify(img, refs, patched)
    return len(tl), moved

def _verify(img, refs, patched):
    """Re-scan the patched code: every rewritten pair must build its new address and no other
    pair may have changed (a lui shared with an unseen user would show up here)."""
    pairs, _ = code_pairs(img)
    after = {(l, u): t for t, lst in pairs.items() for l, u in lst}
    before = {(l, u): t for t, lst in refs.code['e'][0].items() for l, u in lst}
    for k, new in patched.items():
        assert after.get(k) == new, 'elfstr: pair %x/%x builds %s, expected %x' % (k + (after.get(k) and '%x' % after[k], new))
    for k, t in before.items():
        if k not in patched:
            assert after.get(k) == t, 'elfstr: untouched pair %x/%x changed from %x to %s' % (k + (t, after.get(k)))

# ---------------------------------------------------------------- survey

JP = re.compile('[぀-ヿ一-鿿！-～　-〿]')

def _is_text(b):
    i, jp = 0, 0
    while i < len(b):
        c = b[i]
        if 0x81 <= c <= 0x9F or 0xE0 <= c <= 0xFC:
            if i + 1 >= len(b) or not (0x40 <= b[i + 1] <= 0xFC and b[i + 1] != 0x7F):
                return False
            jp += 1
            i += 2
            continue
        if 0xA1 <= c <= 0xDF:
            jp += 1
        elif not (0x20 <= c < 0x7F or c in (0x0A, 0x1B, 0x09)):
            return False
        i += 1
    return jp > 0

def candidates(img):
    """[(vaddr, bytes)] of the Shift-JIS strings in the image's data (mostly Japanese)."""
    out = []
    for lo, hi in img.words:
        out += _scan(img, img.f(lo), img.f(hi - 1) + 1)
    return out

def _scan(img, o, hi):
    out, b = [], img.data
    while o < hi:
        if b[o] == 0:
            o += 1
            continue
        e = b.index(b'\0', o)
        # a string starts after a NUL or, right after a pointer word, at a 4-aligned address
        starts = [o] if b[o - 1] == 0 else []
        starts += [p for p in range(o + 1, e) if (p - img.foff + img.base) % 4 == 0]
        for p in starts:
            s = bytes(b[p:e])
            if _is_text(s):
                t = s.decode('cp932', 'replace')
                fw = len(JP.findall(t))
                if '�' not in t and fw >= 1 and (fw * 2 >= len(t) * 0.5 or FMT.search(s)):
                    out.append((p - img.foff + img.base, s))
                break
        o = e + 1
    return out

# name pools / dictionaries (kanji + kana reading pairs): handled with the names, not here.
# Pools are pointer tables (lo, hi, stride) whose targets are excluded wherever they sit
# (the same table appears three times, pointing at three copies of the strings);
# the given-name dictionary is a run of (kanji, reading) registration calls, each entry
# with its own lui/addiu, so it is excluded by address (notes/elfstr_port_report.md).
NAME_TABLES = {'e': [(0x634BC0, 0x634F04, 4),      # family crests (家紋) 209
                     (0x63FBE0, 0x63FF00, 4),      # women's given names 100 (kanji, reading) ...
                     (0x651AF0, 0x651E10, 4), (0x6584C0, 0x6587E0, 4),            # ... copies 2, 3
                     (0x64A4D0, 0x64A6A8, 8),      # ninja nicknames 59 (name, " ") ...
                     (0x64CEC0, 0x64D098, 8), (0x656E10, 0x656FE8, 8),            # ... copies 2, 3
                     (0x64AA60, 0x64ADC8, 8),      # ninja given names 109 ...
                     (0x64D450, 0x64D7B8, 8), (0x6573A0, 0x657708, 8),            # ... copies 2, 3
                     (0x64AEF0, 0x64AFE0, 8),      # kunoichi nicknames 30 ...
                     (0x657830, 0x657920, 8),                                     # ... copy 2
                     (0x64B140, 0x64B290, 8),      # kunoichi given names 42 ...
                     (0x657A80, 0x657BD0, 8)]}                                    # ... copy 2
NAME_RANGES = {'e': [(0x6411E0, 0x642990), (0x642AB0, 0x642B90), (0x642C30, 0x649D00)]}     # name-entry dictionary: family/given/castle names + readings, baptism names
# (UI islands left out: castle list headers + castle types 0x642990-0x642AA8, Ming trade 0x642B90, trade/court
#  headers 0x642BE0-0x642C20; emulator pass 2026-10-23)
GRID = ('e', 0x64F4C0, 0x64F980)        # name-entry kana/letter/symbol grid rows (table 0x622A50 + 4 symbol rows)
# never text: SJIS glyph table (2,859 u16 codes), LBA table, pack TOC copies
TABLES = {'e': [(0x6300A0, 0x6300A0 + 2 * 2859), (0x61CEC0, 0x61DA40),
                (0x6227A0, 0x624C28), (0x62F430, 0x62F430 + 8 * 57)]}
_FMT_S = re.compile(r'%[-+ #0]*\d*[hl]?[dsxuc]')
# likely debug output (never shown): workers still see these, flagged
DEBUG = re.compile(r'Algo|－.*処理|処理$|\*\*\*|===|<<|>>|\$\$|->|Phase|Buffer|Error|[:：]%d|（[ア-ンａ-ｚＡ-Ｚ]）|^\s')

def _junk(t):
    """One kanji + ASCII / half-width bytes: an instruction, float or pointer word that
    looks like text."""
    rest = _FMT_S.sub('', t)
    return len(JP.findall(t)) == 1 and len(rest) > 1 and all(c.isascii() or '｡' <= c <= 'ﾟ' for c in rest if not JP.match(c))

def _in(ranges, va):
    return any(lo <= va < hi for lo, hi in ranges)

def pool_targets(img):
    """{string va} pointed to by the name-pool tables of the image."""
    out = set()
    for lo, hi, stride in NAME_TABLES[img.tag]:
        for at in range(lo, hi, stride):
            out.add(img.w(at))
    return out

def survey(imgs):
    """[row] of every candidate string: key, tag, va, room, code, data (reference counts),
    block, movable, excluded, jp (text), ui (to translate), debug (flag), kind."""
    refs = Refs(imgs)
    rows = []
    for img in imgs:
        pools = pool_targets(img)
        for va, s in candidates(img):
            jp, room = slot(img, va)
            code, data, block = refs.of(img.tag, va, len(jp))
            t = jp.decode('cp932')
            if _in(TABLES[img.tag], va):
                kind = 'table'
            elif va in pools or _in(NAME_RANGES[img.tag], va):
                kind = 'name'
            elif img.tag == GRID[0] and GRID[1] <= va < GRID[2]:
                kind = 'grid'
            elif _junk(t):
                kind = 'junk'
            elif not code and not data:
                kind = 'noref'
            else:
                kind = 'ui'
            rows.append({'key': '%s%x' % (img.tag, va), 'tag': img.tag, 'va': va, 'room': room,
                         'code': len(code), 'data': len(data), 'block': block,
                         'movable': bool(code or data) and not block, 'excluded': kind in ('table', 'name', 'grid'),
                         'jp': t, 'ui': kind == 'ui', 'debug': kind == 'ui' and bool(DEBUG.search(t) or t.endswith('\n')),
                         'kind': kind})
    return rows

def clean_files():
    import setup
    return [setup.clean_elf()]

def export():
    rows = survey(images(bytearray(clean_files()[0])))
    os.makedirs(os.path.dirname(SCRIPT), exist_ok=True)
    json.dump(rows, open(SCRIPT, 'w', encoding='utf-8'), ensure_ascii=False, indent=0)
    return rows

def rows():
    if not os.path.exists(SCRIPT):
        export()
    return json.load(open(SCRIPT, encoding='utf-8'))

def _order(key):
    return int(key[1:], 16)

def show(lo=None, hi=None, todo=False):
    """Print the strings to translate in [lo, hi] (keys), address order:
    key  room  M (movable if the English is longer) / F (fixed: must fit room)  D (debug?)  Japanese  => English"""
    tl = load_tl()
    rs = [r for r in rows() if r['ui']]
    res = resolved(tl, rs)
    for r in rs:
        if lo and _order(r['key']) < _order(lo) or hi and _order(r['key']) > _order(hi):
            continue
        en = tl.get(r['key'])
        if todo and en is not None:
            continue
        mark = '' if en is not None else '[tm] ' + res[r['key']] if r['key'] in res else ''
        print('%s\t%d\t%s%s\t%s%s' % (r['key'], r['room'], 'M' if r['movable'] else 'F', ' D' if r['debug'] else '',
                                    r['jp'].replace('\n', '\\n'), '\t=> ' + (en.replace('\n', '\\n') if en is not None else mark) if en is not None or mark else ''))

def stats():
    tl = load_tl()
    rs = [r for r in rows() if r['ui']]
    res = resolved(tl, rs)
    done = sum(r['key'] in tl or r['key'] in res for r in rs)
    print('ui strings %d: done %d (%d explicit, %d kept Japanese "-"), todo %d; debug-flagged %d'
          % (len(rs), done, sum(r['key'] in tl and tl[r['key']] != '-' for r in rs), sum(tl.get(r['key']) == '-' for r in rs),
             len(rs) - done, sum(r['debug'] for r in rs)))
    keys = {r['key'] for r in rs}
    stray = [k for k in tl if k not in keys]
    if stray:
        print('keys not in the ui list:', ' '.join(stray[:20]))

_W = None
HALF, FULL = 12, 24                     # Ranseiki font cells (Soutenroku: 11 / 22)

def px(t):
    """Pixel width of a line: English with the proportional font (latinfont 'msg', the
    VWF), Japanese 24 per full-width and 12 per half-width character."""
    global _W
    if _W is None:
        import latinfont
        _W = {c: g[1] for c, g in latinfont.render('msg').items()}
    return max(sum(_W.get(c, HALF) if c.isascii() else HALF if '｡' <= c <= 'ﾟ' else FULL for c in line)
               for line in t.split('\n'))

def wide(tl=None):
    """[(key, en px, jp px, en, jp)] of English wider than its Japanese (labels may be clipped)."""
    tl = load_tl() if tl is None else tl
    out = []
    for r in rows():
        en = tl.get(r['key'])
        if r['ui'] and en and en != '-' and px(en) > px(r['jp']):
            out.append((r['key'], px(en), px(r['jp']), en, r['jp']))
    return out

def check():
    """Dry-run apply on the clean file; prints errors. True if clean."""
    clean = clean_files()[0]
    try:
        n, moved = apply(bytearray(clean), clean, elfseg.Segment())
    except ValueError as e:
        print(e)
        return False
    print('elfstr: %d strings OK (%d moved to the segment)' % (n, moved))
    w = wide()
    if w:
        print('%d strings wider than the Japanese (may be clipped; shorten labels where possible):' % len(w))
        for k, a, b, en, jp in w:
            print('  %s %d > %d px  %s  (%s)' % (k, a, b, en.replace('\n', '\\n'), jp.replace('\n', '\\n')))
    return True

if __name__ == '__main__':
    a = sys.argv[1:]
    if a == ['export']:
        print(len(export()), 'strings ->', SCRIPT)
    elif a and a[0] == 'show':
        todo = '--todo' in a
        a = [x for x in a[1:] if x != '--todo']
        show(a[0] if a else None, a[1] if len(a) > 1 else None, todo)
    elif a == ['stats']:
        stats()
    elif a == ['check']:
        sys.exit(0 if check() else 1)
    else:
        print(__doc__)

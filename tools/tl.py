"""Translation helper for the message blocks ('message', 'kretsu'): show, validate + wrap, apply.

A message is split into text units by tools/msgscript.py: the runs of text between
structural codes (IF conditions, END, S/M/R/F commands). Only units are translated;
the structure around them is kept byte for byte.

tl/<block>/EEEE.txt holds the English (no Japanese) of entries EEEE..EEEE+499, one
block per unit:

    # comment (a line starting with #; English text never does)
    @<entry>          unit 0 of the entry (same as @<entry>.0)
    @<entry>.<unit>
    @<entry>.*        every unit of the entry     @<a>-<b>.*  every unit of a range
    English text, possibly over several physical lines (joined with spaces).

  \\n or ||  forced line break (\\n\\n = empty line)   \\_  a space stripping would eat
  =          (whole block) keep the Japanese of this unit
  Inline codes are copied from `show` as printed: {02}{0B}3 = a variable, %s / %d =
  printf fields (keep their order), {01}C{16}{00} = insert entry 0x16 of the same
  block. Codes may move; {01}C inserts and {02} prints may be dropped (word endings,
  pronouns), not added. Kana toggles ({1B}K {1B}H {1B}k) are dropped.
  A '%' that is not a field becomes full-width ％.

Translation memory: a unit without English takes the English of a unit with the
same Japanese bytes (any entry, either block; explicit English always wins).

Units are word-wrapped (proportional widths of tools/latinfont.py, size 'msg') to the
box of their entry and checked: pixels per line, lines, bytes per line, and the whole
message re-parsed must keep the Japanese structure. overflow.py checks every buffer
the expanded messages reach.

usage:
    tl.py show  BLOCK [from] [to] [--todo]   # Japanese units to translate (codes visible)
    tl.py apply BLOCK batch.txt              # validate a batch, merge it into tl/BLOCK/
                                             # (nothing is written if there are errors)
    tl.py check                              # validate everything in tl/
    tl.py stats                              # progress
BLOCK = message | kretsu
"""
import collections, contextlib, fcntl, json, os, re, sys

sys.path.insert(0, os.path.dirname(__file__))
import latinfont, msgfile, msgscript, setup, text

TL = setup.TL
FILE_ENTRIES = 500                 # entries per tl file

# ---------------------------------------------------------------- boxes
# (width px, lines per page, mode). mode 'window': the unit is shown in a paging
# window, so it may use as many pages as the Japanese; 'panel': a fixed panel, at most
# `lines` lines. Entries not covered use FIT: each unit is wrapped to its widest
# Japanese line (at least the block's usual width) with no more lines than the Japanese.
FULL_PX, HALF_PX = 22, 11          # on-screen cells: the 24x24 / 12x24 glyphs are drawn at 22/24 (measured)
SHEET_PX = 12                      # half-width cell on the FONTTEX sheet (latinfont 'msg' widths)
VAR_PX, VAR_BYTES = 110, 20        # an inserted name or number ({02} print, %s)
NUM_PX, NUM_BYTES = 44, 6          # %d
LINE_BYTES = 255                   # longest line the layout code is assumed to take (unverified here)
FLOW_PX = 1000                     # wider Japanese lines would be flowing paragraphs (none seen here)
# Measured on screen 2026-10-23 (notes/boxes.md): event/advisor dialogue with portrait = 38
# half-width cells (19 full-width) x 4 lines; officer bio panel 36 cells x 4; the treasure
# info effect box (4614-4665) 14 cells x 2; help pages open a window 44 cells wide that
# grows with the text (12 lines seen, Japanese max 13), units = pages, but some entries in
# the help range are advisor dialogue (mode 'help' decides per unit). Confirm dialogs,
# narration boxes and the rest size themselves to the text or match the Japanese: FIT.
# KRETSU descriptions were not reached yet: 36 x 4 provisional (+ 147-byte limit).
# The widths assume the proportional Latin font (VWF hook, to do): without it every
# ASCII character is 11 px.
DIALOGUE, BIO = (418, 4, 'panel'), (396, 4, 'panel')
EFFECT, HELP = (154, 2, 'panel'), (484, 13, 'help')
EVDESC = (250, 4, 'panel')
GOAL, STEPS = (330, 2, 'panel'), (370, 8, 'panel')   # council goal window: proposal summary / 手順 steps box (boxes.md;
                                                      # screen-measured s15b: proposal wraps between 309 and 345 px, steps between 371 and 420)
BOXES = {'message': [(range(4614, 4666), EFFECT), (range(600, 4800), DIALOGUE), (range(5300, 6100), HELP),
                     (range(4843, 4845), DIALOGUE), ((4865, 4869, 4912), DIALOGUE),   # council advisors with portrait (boxes.md)
                     (range(4854, 4865), GOAL), (range(4882, 4892), STEPS),
                     (range(6129, 9699), BIO),
                     (range(10767, 10793), EVDESC),   # event editor description panel (s22: ~255 px, under a title line)
                     (range(10325, 11306), DIALOGUE)],
         'kretsu': [(range(0, 10 ** 6), BIO)]}        # [(entry range, (width, lines, mode))], first match wins

KANA_TOGGLE = re.compile(rb'\x1b[KHk]')
FULL_LATIN = re.compile('[０-９Ａ-Ｚａ-ｚ]')

_W = None

def widths():
    global _W
    if _W is None:
        _W = {c: g[1] * HALF_PX / SHEET_PX for c, g in latinfont.render('msg').items()}
    return _W

# ---------------------------------------------------------------- Japanese rows

_ROWS = {}

def rows_of(n):
    """[jp bytes] of block n (from script/msg, made by setup.py)."""
    if n not in _ROWS:
        p = setup.msg_json(n)
        if not os.path.exists(p):
            sys.exit('%s missing: run tools/setup.py first' % p)
        _ROWS[n] = [text.encode(r['jp']) for r in json.load(open(p, encoding='utf-8'))]
    return _ROWS[n]

_UNITS = {}

def units_of(n, e):
    k = (n, e)
    if k not in _UNITS:
        _UNITS[k] = msgscript.units(rows_of(n)[e])
    return _UNITS[k]

def unit_bytes(toks, a, z):
    return b''.join(t for k, t in toks[a:z])

# ---------------------------------------------------------------- measuring

FINAL = {}                         # (block, entry) -> English bytes, while checking

def message(n, e):
    return FINAL.get((n, e)) or rows_of(n)[e]

def insert_size(n, code, depth=0):
    """(px, bytes) of an inline code when shown (first line of an insert)."""
    k = code[0]
    if k == 0x02:
        return VAR_PX, VAR_BYTES
    if k == 0x25:
        return (NUM_PX, NUM_BYTES) if code[-1:] != b's' else (VAR_PX, VAR_BYTES)
    if k == 0x1B and code[1:2] == b'P':
        return FULL_PX, 3                  # icon
    if k == 0x01 and len(code) == 4 and code[1] == 0x43 and depth < 3:
        e = code[2] | code[3] << 8
        if e >= len(rows_of(n)):
            return 0, 0
        toks, spans = msgscript.units(message(n, e))
        best = (0, 0)
        for a, z in spans:
            best = max(best, measure(n, lines_of(n, toks[a:z])[0], depth + 1))
        return best
    return 0, 0

def measure(n, toks, depth=0):
    """(px, bytes) of one line given as tokens."""
    px = nb = 0
    W = widths()
    for kind, t in toks:
        if kind == 'text':
            i = 0
            while i < len(t):
                c = t[i]
                if msgscript.sjis_lead(c) and i + 1 < len(t):
                    px += FULL_PX; nb += 2; i += 2
                    continue
                px += W.get(chr(c), HALF_PX) if c < 0x80 else HALF_PX
                nb += 1; i += 1
        elif kind == 'inline':
            p, b = insert_size(n, t, depth)
            px += p; nb += b
    return px, nb

def ends_line(n, code):
    """True if a {01}C insert's text ends with a newline (the line ends there)."""
    if not (code[0] == 0x01 and len(code) == 4 and code[1] == 0x43):
        return False
    e = code[2] | code[3] << 8
    if e >= len(rows_of(n)):
        return False
    toks, spans = msgscript.units(message(n, e))
    return any(toks[z - 1][0] == 'nl' for a, z in spans)

def lines_of(n, toks):
    """Split tokens into display lines; a line ended by an insert that itself ends
    with a newline gets a ('soft', b'') marker (no newline is written after it)."""
    out, cur = [], []
    for kind, t in toks:
        if kind == 'nl':
            out.append(cur); cur = []
        else:
            cur.append((kind, t))
            if kind == 'inline' and ends_line(n, t):
                out.append(cur + [('soft', b'')]); cur = []
    return out + [cur]

def join_lines(lines):
    out = b''
    for i, l in enumerate(lines):
        out += b''.join(t for k, t in l)
        if i < len(lines) - 1 and not (l and l[-1][0] == 'soft'):
            out += b'\n'
    return out

# ---------------------------------------------------------------- wrapping

def words(toks):
    """Split a token line into words (lists of tokens) at ASCII spaces."""
    out, cur = [], []
    for kind, t in toks:
        if kind != 'text':
            cur.append((kind, t)); continue
        parts = t.split(b' ')
        for j, p in enumerate(parts):
            if j:
                out.append(cur); cur = []
            if p:
                cur.append(('text', p))
    out.append(cur)
    return [w for w in out if w]

def join(ws):
    toks = []
    for j, w in enumerate(ws):
        if j:
            toks.append(('text', b' '))
        toks += w
    return toks

def wrap(n, toks, width):
    """Word-wrap tokens (with 'nl' forced breaks) -> list of lines (token lists)."""
    out = []
    for para in lines_of(n, toks):
        line = []
        for w in words(para):
            if line and measure(n, join(line + [w]))[0] > width:
                out.append(join(line)); line = [w]
            else:
                line.append(w)
        out.append(join(line))
    return out

class Box:
    def __init__(self, n):
        self.n = n
        ws = []
        for e in range(len(rows_of(n))):
            toks, spans = units_of(n, e)
            for a, z in spans:
                ls = lines_of(n, toks[a:z])
                if len(ls) > 1:
                    ws += [x for x in (measure(n, l)[0] for l in ls) if x <= FLOW_PX]
        ws.sort()
        self.floor = ws[len(ws) * 3 // 4] if ws else 0

    def limits(self, jp_toks, entry):
        """(width px, max lines, lines per page or 0) for a unit."""
        jl = [measure(self.n, l)[0] for l in lines_of(self.n, jp_toks)]
        fixed = next((b for r, b in BOXES.get(self.n, ()) if entry in r), None)
        if fixed:
            w, page, mode = fixed
            if mode == 'help':         # help window, unless the unit is dialogue-sized (advisor lines in the range)
                if len(jl) <= DIALOGUE[1] and max(jl) <= DIALOGUE[0]:
                    w, page = DIALOGUE[:2]
                return w, page, page
            if mode == 'panel':
                return w, page, page
            if mode == 'parts':        # pieces joined by {01}C into one panel: keep the Japanese line count
                return w, min(page, len(jl)), page
            shown = sum(max(1, -(-x // w)) for x in jl)
            return w, -(-shown // page) * page, page
        manual = [x for x in jl if x <= FLOW_PX]
        flowing = len(manual) < len(jl)
        return max([self.floor] + manual), 10 ** 6 if flowing else len(jl), 0

# ---------------------------------------------------------------- tl files

KEY = re.compile(r'@(\d+)(?:-(\d+))?(?:\.(\d+|\*))?\s*')

def _key(a, b, u):
    return '%s%s%s' % (a, '-' + b if b else '', '.' + u if u and u != '0' else '')

def parse_tl(path):
    """-> {key: text}, key as written without '@': '12', '12.3', '12.*' or '63-99.*'."""
    out, key, buf = {}, None, []
    def flush():
        if key is not None:
            out[key] = ' '.join(buf)
    for raw in open(path, encoding='utf-8'):
        line = raw.rstrip('\n')
        m = KEY.fullmatch(line)
        if m:
            flush()
            a, b, u = m.groups()
            if b and u != '*':
                sys.exit('%s: %s: a range needs .*' % (path, line))
            key, buf = _key(a, b, u), []
        elif line.lstrip().startswith('#'):
            continue
        elif key is not None and line.strip():
            buf.append(line.strip())
    flush()
    return out

def key_order(k):
    a, b, u = re.fullmatch(r'(\d+)(?:-(\d+))?(?:\.(\d+|\*))?', k).groups()
    return int(a), int(b or a), -1 if u == '*' else int(u or 0)

def tl_dir(n):
    return os.path.join(TL, n)

def tl_path(n, entry):
    return os.path.join(tl_dir(n), '%04d.txt' % (entry // FILE_ENTRIES * FILE_ENTRIES))

def load_tl(n):
    """{key: text} of all tl files of block n."""
    out = {}
    d = tl_dir(n)
    if os.path.isdir(d):
        for f in sorted(os.listdir(d)):
            if f.endswith('.txt'):
                out.update(parse_tl(os.path.join(d, f)))
    return out

def expand(n, keys):
    """{key: text} -> {(entry, unit): text}; explicit units beat N.*, which beats ranges."""
    out = {}
    rows = rows_of(n)
    for k in sorted(keys, key=lambda k: (('-' not in k) + ('*' not in k), key_order(k))):
        a, b, u = key_order(k)
        for e in range(a, min(b, len(rows) - 1) + 1 if a != b else a + 1):
            if u >= 0 or e >= len(rows):
                out[(e, max(u, 0))] = keys[k]
            else:
                for i in range(len(units_of(n, e)[1])):
                    out[(e, i)] = keys[k]
    return out

def write_tl(path, new):
    """Merge {key: text} into a tl file: existing blocks are replaced in place (comments
    and order kept), new ones appended in key order."""
    lines = open(path, encoding='utf-8').read().split('\n') if os.path.exists(path) else []
    out, i, done = [], 0, set()
    while i < len(lines):
        m = KEY.fullmatch(lines[i])
        key = _key(*m.groups()) if m else None
        if key in new:
            out += [lines[i], new[key]]
            done.add(key)
            i += 1
            while i < len(lines) and lines[i].strip() and not KEY.fullmatch(lines[i]) \
                    and not lines[i].lstrip().startswith('#'):
                i += 1
            continue
        out.append(lines[i])
        i += 1
    while out and out[-1] == '':
        out.pop()
    for k in sorted(set(new) - done, key=key_order):
        out += ['@' + k, new[k]]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(out) + '\n')

# ---------------------------------------------------------------- English bytes

def english_bytes(src, jp_unit):
    """Batch text -> bytes: codes decoded, kana toggles dropped (a leading one is kept
    when the Japanese starts with one, and the Japanese unit's last one is kept at the
    end so the mode after the unit is unchanged), '%' that is not a field -> ％."""
    s = src.replace(' || ', '\n').replace('||', '\n').replace('\\n ', '\n').replace('\\n', '\n')
    s = s.replace('\\_', ' ').replace(' \n', '\n')
    b = KANA_TOGGLE.sub(b'', text.encode(s))
    toggles = KANA_TOGGLE.findall(jp_unit)
    if toggles:
        lead = KANA_TOGGLE.match(jp_unit)
        b = (lead.group() if lead else b'') + b + (toggles[-1] if not lead or len(toggles) > 1 else b'')
    out = []
    for k, t in msgscript.tokens(b):
        if k == 'inline' and t[:1] == b'%' and not msgscript.is_field(t):
            t = '％'.encode('cp932') + t[1:]
        out.append(t)
    return b''.join(out)

# ---------------------------------------------------------------- validation

def code_str(t):
    """A code as written in tl/ files: printable ASCII as is, other bytes {XX}."""
    return ''.join(chr(c) if 0x20 <= c <= 0x7E and c not in (0x7B, 0x7D) else '{%02X}' % c for c in t)

HW = 'ｦｧｨｩｪｫｬｭｮｯｰｱｲｳｴｵｶｷｸｹｺｻｼｽｾｿﾀﾁﾂﾃﾄﾅﾆﾇﾈﾉﾊﾋﾌﾍﾎﾏﾐﾑﾒﾓﾔﾕﾖﾗﾘﾙﾚﾛﾜﾝ'
HI = 'をぁぃぅぇぉゃゅょっーあいうえおかきくけこさしすせそたちつてとなにぬねのはひふへほまみむめもやゆよらりるれろわん'
KATA = 'ヲァィゥェォャュョッーアイウエオカキクケコサシスセソタチツテトナニヌネノハヒフヘホマミムメモヤユヨラリルレロワン'

def readable(s, kata=False):
    """Half-width kana -> hiragana (katakana after {1B}k), with voicing marks combined."""
    import unicodedata
    s = s.translate(str.maketrans(HW, KATA if kata else HI))
    s = s.replace('ﾞ', '゙').replace('ﾟ', '゚').replace('゛', '゙').replace('゜', '゚')
    return unicodedata.normalize('NFC', s)

def unit_str(toks):
    """Japanese unit for display: text readable, codes via code_str, kana toggles dropped."""
    out, kata = [], False
    for k, t in toks:
        if k in ('text', 'nl'):
            out.append(readable(text.decode(t), kata))
        elif KANA_TOGGLE.fullmatch(t):
            kata = t == b'\x1bk'
        else:
            out.append(code_str(t))
    return ''.join(out)

def inline_codes(toks):
    return collections.Counter(t for k, t in toks if k == 'inline' and not KANA_TOGGLE.fullmatch(t))

def check_unit(jp_toks, en):
    errs, warns = [], []
    try:
        et = msgscript.tokens(en)
    except msgscript.ParseError as e:
        return ['parse: %s' % e], warns, None
    bad = [t for k, t in et if k not in msgscript.INLINE]
    if bad:
        errs.append('structural code in English: %s' % ' '.join(code_str(t) for t in bad))
    jc, ec = inline_codes(jp_toks), inline_codes(et)
    for t in (ec - jc).elements():
        errs.append('code not in the Japanese: %s' % code_str(t))
    for t, c in (jc - ec).items():
        if t[0] == 0x01 and t[1] == 0x43:
            continue
        if t[0] == 0x02:
            warns.append('dropped variable %s' % code_str(t))
        else:
            errs.append('missing code %s' % code_str(t))
    jf = [t for k, t in jp_toks if k == 'inline' and msgscript.is_field(t)]
    ef = [t for k, t in et if k == 'inline' and msgscript.is_field(t)]
    if jf != ef:
        errs.append('printf fields must stay in order: %s' % ' '.join(code_str(t) for t in jf))
    if FULL_LATIN.search(en.decode('cp932', 'replace')):
        errs.append('full-width Latin/digits in English')
    return errs, warns, et

def build_entry(n, e, units_en, box, log):
    """Substitute English units into one message -> bytes (None if untouched)."""
    toks, spans = units_of(n, e)
    out = [t for k, t in toks]
    used = False
    tag = '%s@%d' % (n, e)
    for u, (a, z) in enumerate(spans):
        src = units_en.pop(u, None)
        if src is None:
            continue
        if src.strip() == '=':
            used = True
            continue
        jp_unit = unit_bytes(toks, a, z)
        try:
            en = english_bytes(src, jp_unit)
        except ValueError as x:
            log('%s.%d ERROR %s' % (tag, u, x), True); continue
        errs, warns, et = check_unit(toks[a:z], en)
        if et is None or errs:
            for x in errs:
                log('%s.%d ERROR %s' % (tag, u, x), True)
            continue
        width, max_lines, page = box.limits(toks[a:z], e)
        lines = wrap(n, et, width)
        for i, l in enumerate(lines):
            px, nb = measure(n, l)
            if px > width:
                warns.append('line %d is %d px > %d' % (i + 1, px, width))
            if nb > LINE_BYTES:
                errs.append('line %d is %d bytes > %d' % (i + 1, nb, LINE_BYTES))
        if len(lines) > max_lines:
            warns.append('%d lines > %d%s' % (len(lines), max_lines, ' (%d a page)' % page if page else ''))
        for x in errs:
            log('%s.%d ERROR %s' % (tag, u, x), True)
        for x in warns:
            log('%s.%d warn  %s' % (tag, u, x), False)
        for i in range(a, z):
            out[i] = b''
        out[a] = join_lines(lines)
        used = True
    for u in units_en:
        log('%s.%d ERROR no such unit (entry has %d)' % (tag, u, len(spans)), True)
    if not used:
        return None
    nb = b''.join(out)
    try:
        new_struct = [t for k, t in msgscript.tokens(nb) if k not in msgscript.INLINE]
    except msgscript.ParseError as x:
        log('%s ERROR rebuilt message does not parse: %s' % (tag, x), True)
        return None
    if new_struct != [t for k, t in toks if k not in msgscript.INLINE]:
        log('%s ERROR English changes the message structure (text after a code eaten as '
            'its argument? a digit right after a number needs {1B}H before it)' % tag, True)
        return None
    return nb

def memory(tls):
    """Translation memory: {jp unit bytes: English} from {block: {(entry, unit): text}}."""
    tm = {}
    for n in msgfile.BLOCKS:
        for (e, u), src in sorted(tls[n].items()):
            if src.strip() == '=' or e >= len(rows_of(n)):
                continue
            toks, spans = units_of(n, e)
            if u < len(spans):
                tm.setdefault(unit_bytes(toks, *spans[u]), src)
    return tm

def with_memory(n, explicit, tm):
    """{(entry, unit): text} of block n: explicit English, then translation memory."""
    out = dict(explicit)
    for e in range(len(rows_of(n))):
        toks, spans = units_of(n, e)
        for u, (a, z) in enumerate(spans):
            if (e, u) not in out:
                src = tm.get(unit_bytes(toks, a, z))
                if src is not None:
                    out[(e, u)] = src
    return out

def build_block(n, units, log):
    """{entry: English bytes} of block n from {(entry, unit): text}."""
    box = Box(n)
    per = collections.defaultdict(dict)
    for (e, u), src in units.items():
        per[e][u] = src
    out = {}
    for e, us in sorted(per.items()):
        if e >= len(rows_of(n)):
            log('%s@%d ERROR no such entry' % (n, e), True); continue
        b = build_entry(n, e, dict(us), box, log)
        if b is not None:
            out[e] = b
    return out

def build_all(tls=None, quiet=False):
    """-> ({block: {entry: bytes}}, errors). tls: {block: {key: text}} (default: tl/)."""
    if tls is None:
        tls = {n: load_tl(n) for n in msgfile.BLOCKS}
    bad = []
    def log(msg, err):
        if err:
            bad.append(msg)
        if err or not quiet:
            print(msg)
    expl = {n: expand(n, tls[n]) for n in msgfile.BLOCKS}
    tm = memory(expl)
    FINAL.clear()
    out = {}
    for n in msgfile.BLOCKS:
        out[n] = build_block(n, with_memory(n, expl[n], tm), log)
        FINAL.update({(n, e): b for e, b in out[n].items()})
    if not bad:
        import overflow
        for x in overflow.check(lambda n, e: message(n, e) if e < len(rows_of(n)) else None,
                                {n: len(rows_of(n)) for n in msgfile.BLOCKS}):
            log('ERROR ' + x, True)
    return out, bad

def block_bytes(n, english):
    """File bytes of block n with {entry: English bytes}."""
    return msgfile.pack([english.get(e, b) for e, b in enumerate(rows_of(n))])

@contextlib.contextmanager
def locked():
    os.makedirs(TL, exist_ok=True)
    with open(os.path.join(TL, '.lock'), 'w') as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield

def apply(n, batch):
    """Validate a batch file with everything else in tl/ and merge it in."""
    with locked():
        new = parse_tl(batch)
        tls = {b: load_tl(b) for b in msgfile.BLOCKS}
        tls[n].update(new)
        print('%s: %d blocks from %s' % (n, len(new), batch))
        _, bad = build_all(tls, quiet=False)
        if bad:
            print('%s: %d errors: nothing written' % (n, len(bad)))
            return False
        files = collections.defaultdict(dict)
        for k, v in new.items():
            files[tl_path(n, key_order(k)[0])][k] = v
        for path, keys in files.items():
            write_tl(path, keys)
    stats(n)
    return True

def apply_all(verbose=False):
    """Validate tl/ (setup.py, build.py); verbose = print the width/line warnings too."""
    _, bad = build_all(quiet=not verbose)
    return not bad

# ---------------------------------------------------------------- show / stats

def context(toks, a):
    """What leads to a unit: the structural code just before it."""
    for k, t in reversed(toks[:a]):
        if k == 'if':
            return 'if ' + code_str(t[3:])
        if k == 'end':
            return 'else'
        if k == 'cmd':
            return code_str(t)
        if k not in msgscript.INLINE:
            break
    return ''

def show(n, a=0, b=None, todo=False):
    tls = {x: expand(x, load_tl(x)) for x in msgfile.BLOCKS}
    tm = memory(tls)
    seen = {}
    rows = rows_of(n)
    for e in range(a, min(b if b is not None else len(rows), len(rows))):
        toks, spans = units_of(n, e)
        for u, (x, z) in enumerate(spans):
            jb = unit_bytes(toks, x, z)
            if (e, u) in tls[n]:
                mark = ' [done]'
            elif jb in tm:
                mark = ' [tm]'
            elif jb in seen:
                mark = ' [same as @%d.%d]' % seen[jb]
            else:
                mark = ''
                seen[jb] = (e, u)
            if todo and mark:
                continue
            ctx = context(toks, x)
            print('@%d%s%s%s  %s' % (e, '.%d' % u if u else '', mark, '  # ' + ctx if ctx else '',
                                     unit_str(toks[x:z]).replace('\n', '⏎')))

def stats(only=None):
    tls = {n: expand(n, load_tl(n)) for n in msgfile.BLOCKS}
    tm = memory(tls)
    for n in msgfile.BLOCKS:
        if only is not None and n != only:
            continue
        total = done = mem = 0
        per = collections.Counter()
        for e in range(len(rows_of(n))):
            toks, spans = units_of(n, e)
            for u, (a, z) in enumerate(spans):
                total += 1
                if (e, u) in tls[n]:
                    done += 1; per[e // FILE_ENTRIES] += 1
                elif unit_bytes(toks, a, z) in tm:
                    mem += 1; per[e // FILE_ENTRIES] += 1
        print('%s: %d/%d units translated (+%d from memory) = %.1f%%' %
              (n, done, total, mem, 100 * (done + mem) / total))

def _block(name):
    if name not in msgfile.BLOCKS:
        sys.exit('block must be one of %s' % ', '.join(msgfile.BLOCKS))
    return name

if __name__ == '__main__':
    args = [x for x in sys.argv[1:] if not x.startswith('--')]
    cmd = args[0] if args else ''
    if cmd == 'show' and len(args) >= 2:
        show(_block(args[1]), *(int(x) for x in args[2:4]), todo='--todo' in sys.argv)
    elif cmd == 'apply' and len(args) == 3:
        sys.exit(0 if apply(_block(args[1]), args[2]) else 1)
    elif cmd == 'check':
        sys.exit(0 if apply_all('-v' in sys.argv) else 1)
    elif cmd == 'stats':
        stats()
    else:
        sys.exit(__doc__)

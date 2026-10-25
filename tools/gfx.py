"""Redraw Japanese text baked into NB9 textures (tools/tim2.py edits them in place; port of Soutenroku gfx.py).

Lettering comes from the stroke glyphs of tools/latinfont.py (original drawings) rendered at
any size (ported from Taiko 5 gfx.py); colours are measured from the original texture:

  plate(): light text with a dark outline on a textured plate (tags, buttons, banners on
    a panel): text pixels (brighter than the plate) plus their outline are erased by
    resampling unmasked pixels of the same row, then the English is drawn with the
    original's fill and outline colours.
  banner(): big outlined lettering on transparency (勝利 ...).

tl/gfx.txt: "PACK.object<TAB>English" (PACK = NB9 file stem, tim2.FILES) ('|' = line break inside one spot, ';' separates spots
of ITEMS with several boxes). ITEMS gives the kind and boxes; objects not in ITEMS are plates
over the whole texture.
"""
import collections, os, random, sys
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(__file__))
import latinfont, tim2

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TL = os.path.join(ROOT, 'tl', 'gfx.txt')
SS = 4

def luma(c):
    return (c[0] * 299 + c[1] * 587 + c[2] * 114) // 1000

class Canvas:
    """Palette helpers around a tim2.Texture."""
    def __init__(self, tex):
        self.t = tex
        self.w, self.h, self.pal = tex.w, tex.h, tex.pal
        self.cache = {}

    def get(self, x, y):
        return self.t.get(x, y)

    def idx(self, x, y):
        return self.t.idx(x, y)

    def put(self, x, y, i):
        self.t.put(x, y, i)

    def index(self, rgb, alpha=0x80):
        """Nearest palette entry to an RGB colour and alpha (0-0x80)."""
        key = tuple(int(round(v)) for v in rgb[:3]) + (int(round(alpha)),)
        if key not in self.cache:
            self.cache[key] = min(range(len(self.pal)), key=lambda i: sum((a - b) ** 2 for a, b in zip(self.pal[i][:3], key[:3]))
                                  + 6 * (self.pal[i][3] - key[3]) ** 2)
        return self.cache[key]

class Stack(Canvas):
    """Textures joined as one canvas, stacked vertically (a 400x80 banner split into two
    400x40 textures with their own palettes) or side by side (across=True: a 512x448
    screen + its 128x448 right strip): indexes are into the joined palette list; a put maps
    the colour to the nearest entry of the texture that holds the pixel."""
    def __init__(self, texs, across=False):
        self.ts, self.across = texs, across
        if across:
            self.w, self.h = sum(t.w for t in texs), texs[0].h
        else:
            self.w, self.h = texs[0].w, sum(t.h for t in texs)
        self.pal, self.base, self.o0 = [], [], []
        o = 0
        for t in texs:
            self.base.append(len(self.pal)); self.o0.append(o)
            self.pal += t.pal; o += t.w if across else t.h
        self.cache = {}

    def _at(self, x, y):
        v = x if self.across else y
        k = max(i for i in range(len(self.ts)) if self.o0[i] <= v)
        return (k, x - self.o0[k], y) if self.across else (k, x, y - self.o0[k])

    def get(self, x, y):
        k, xx, yy = self._at(x, y)
        return self.ts[k].get(xx, yy)

    def idx(self, x, y):
        k, xx, yy = self._at(x, y)
        return self.base[k] + self.ts[k].idx(xx, yy)

    def put(self, x, y, i):
        k, xx, yy = self._at(x, y)
        t = self.ts[k]
        lo = self.base[k]
        if not lo <= i < lo + len(t.pal):
            c = self.pal[i]
            i = lo + min(range(len(t.pal)), key=lambda j: sum((a - b) ** 2 for a, b in zip(t.pal[j][:3], c[:3])) + 6 * (t.pal[j][3] - c[3]) ** 2)
        t.put(xx, yy, i - lo)

# ---------------------------------------------------------------- lettering

def text_masks(text, cap, widths, condense=1.0, spacing=None, descender=None):
    """Coverage images (PIL 'L', SS times the final size) of text drawn with each
    stroke width in widths (px, final scale): wider strokes give the outline layers.
    Returns (images, (w, h), (left, top) of the cap box) at final scale."""
    stroke = widths[0]
    s = (cap - stroke) / latinfont.C
    spacing = stroke * 0.45 if spacing is None else spacing
    pad = max(widths) / 2 + 1
    xs, x = [], pad
    for c in text:
        w, _ = latinfont.GLYPHS[c]
        xs.append(x)
        x += (w * s * condense + stroke + spacing) if c != ' ' else cap * 0.32
    width = x - spacing + pad
    if descender is None:
        descender = any(c in 'gjpqy,;' for c in text)
    height = cap + stroke + 2 * pad + (3 * s if descender else 0)
    base = pad + cap - stroke / 2
    out = []
    for sw in widths:
        im = Image.new('L', (int(width * SS) + 1, int(height * SS) + 1), 0)
        dr = ImageDraw.Draw(im)
        r = sw * SS / 2
        for c, x0 in zip(text, xs):
            for st in latinfont.GLYPHS[c][1]:
                pts = [((x0 + stroke / 2 + px * s * condense) * SS, (base - py * s) * SS) for px, py in st]
                for a, b in zip(pts, pts[1:]):
                    dr.line([a, b], fill=255, width=max(1, round(sw * SS)))
                for px, py in pts:
                    dr.ellipse((px - r, py - r, px + r, py + r), fill=255)
        out.append(im)
    return out, (width, height), (pad, pad)

def fit(lines, cap, box_w, box_h, widths_of, min_cap=5.0, min_condense=0.7):
    """Shrink (condense first, then cap) until every line fits box_w and all lines box_h.
    -> (cap, condense, [(masks, (w, h), (px, py))])"""
    condense = 1.0
    while True:
        r = [text_masks(l, cap, widths_of(cap), condense, descender=False) for l in lines]
        lh = cap + widths_of(cap)[-1]
        tw = max(m[1][0] for m in r) - 2 * (max(widths_of(cap)) / 2 + 1)
        if (tw <= box_w and lh * len(lines) + (len(lines) - 1) * 1 <= box_h + 1) or cap <= min_cap:
            if tw > box_w + 0.5 or lh * len(lines) > box_h + 3:
                raise ValueError('%r does not fit %dx%d' % ('|'.join(lines), box_w, box_h))
            return cap, condense, r
        if condense > min_condense:
            condense *= 0.96
        else:
            cap *= 0.95

# ---------------------------------------------------------------- plates

def _box(cv, box):
    if box == 'auto':
        # the opaque plate of a texture wider than its plate (command banners): columns
        # opaque on the middle row, inset 4 px
        xs = [x for x in range(cv.w) if cv.get(x, cv.h // 2)[3] >= 0x40]
        return (xs[0] + 4, 4, xs[-1] - 4, cv.h - 5)
    if box:
        # negative x1/y1 count from the right/bottom edge (one box for plates of several widths)
        x0, y0, x1, y1 = box
        return (x0, y0, x1 + cv.w if x1 < 0 else x1, y1 + cv.h if y1 < 0 else y1)
    return (1, 1, cv.w - 2, cv.h - 2)

def plate_style(cv, box, mode='light', grow=2, dilate=4, sens=(24, 0.4), amin=0x40):
    """-> (text mask set, fill rgb, outline rgb, text alpha) for light text with a dark
    outline (mode 'light') or dark text on a light plate ('dark'). amin = alpha of the pixels
    measured (lower for faint, half-transparent art)."""
    x0, y0, x1, y1 = box
    px = [(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1)]
    L = {p: luma(cv.get(*p)) for p in px}
    A = {p: cv.get(*p)[3] for p in px}
    vals = sorted(L[p] for p in px if A[p] > amin)
    # the plate is the bulk of the pixels; text the bright tail
    med = vals[len(vals) // 2]
    spread = max(6, vals[len(vals) * 2 // 3] - vals[len(vals) // 3])
    hi = vals[int(len(vals) * 0.97)]
    if mode in ('light', 'glow'):
        th = med + max(sens[0], (hi - med) * sens[1])
        core = {p for p in px if L[p] >= th and A[p] > amin}
    else:
        lo = vals[int(len(vals) * 0.03)]
        th = med - max(sens[0], (med - lo) * sens[1])
        core = {p for p in px if L[p] <= th and A[p] > amin}
    # the outline: core neighbours on the opposite side of the plate
    ring = collections.Counter()
    for (x, y) in core:
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                q = (x + dx, y + dy)
                if q in L and q not in core and ((L[q] < med) if mode == 'light' else (L[q] > med)):
                    ring[q] += 1
    if mode == 'glow':
        # dark lettering inside a light glow: the glow is the bright core; the letters'
        # insides are dark like the background, so the mask is the glow grown by dilate px
        dark = {p for p in px if L[p] < med - 8}
        core = {(x, y) for (x, y) in core if any((x + dx, y + dy) in dark for dx in range(-3, 4) for dy in range(-3, 4))}
        mask = set(core)
        for _ in range(dilate):
            mask |= {(x + dx, y + dy) for (x, y) in mask for dx in (-1, 0, 1) for dy in (-1, 0, 1) if (x + dx, y + dy) in L}
        inner = sorted((cv.get(*p) for p in mask - core), key=luma)
        glow = sorted((cv.get(*p) for p in core), key=luma)
        fill, outline = inner[len(inner) // 10], glow[len(glow) * 3 // 4]
        return mask, fill[:3], outline[:3], max(fill[3], outline[3])
    if not core:
        raise ValueError('no lettering found in %r' % (box,))
    fillc = sorted((cv.get(*p) for p in core), key=luma)
    fill = fillc[int(len(fillc) * 0.8)] if mode == 'light' else fillc[len(fillc) // 5]
    outc = sorted((cv.get(*p) for p in ring), key=luma)
    outline = (outc[len(outc) // 4] if mode == 'light' else outc[len(outc) * 3 // 4]) if outc else None
    # erase a bit more than the outline: anti-aliased edges otherwise stay as specks
    mask = set(core) | set(ring)
    for _ in range(grow):
        mask |= {(x + dx, y + dy) for (x, y) in mask for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                 if (x + dx, y + dy) in L and abs(L[x + dx, y + dy] - med) > spread}
    # plus one pixel all round: the inpainting copies from the mask's edge, and faint
    # anti-aliased text edges there grew back into ghosts of the old lettering
    mask |= {(x + dx, y + dy) for (x, y) in mask for dx in (-1, 0, 1) for dy in (-1, 0, 1) if (x + dx, y + dy) in L}
    return mask, fill[:3], outline[:3] if outline else None, fill[3]

def erase(cv, box, mask, seed=0):
    """Inpaint masked pixels from the outside in (onion peel): each pass fills the pixels
    with the most known neighbours by copying a random known pixel within 2 px, so the
    background's local colours and grain continue under the text."""
    x0, y0, x1, y1 = box
    rnd = random.Random(seed)
    todo = {p for p in mask if x0 <= p[0] <= x1 and y0 <= p[1] <= y1}
    near = [(dx, dy) for dx in range(-2, 3) for dy in range(-2, 3) if dx or dy]
    def known(x, y):
        return [(x + dx, y + dy) for dx, dy in near
                if x0 <= x + dx <= x1 and y0 <= y + dy <= y1 and (x + dx, y + dy) not in todo]
    while todo:
        cand = {p: known(*p) for p in todo}
        best = max(len(v) for v in cand.values())
        if best == 0:
            break
        need = max(1, min(best, 8))
        layer = [p for p, v in cand.items() if len(v) >= need]
        for p in layer:
            cv.put(p[0], p[1], cv.idx(*rnd.choice(cand[p])))
        todo -= set(layer)

def draw(cv, box, text, fill, outline, alpha=0x80, cap=None, align='center', bold=0.14, lw=1.0, vshift=0, hshift=0):
    """English (lines split on '|') centred in box, fill + outline colours."""
    x0, y0, x1, y1 = box
    lines = text.split('|')
    bw, bh = x1 - x0 + 1, y1 - y0 + 1
    widths_of = lambda c: [max(1.2, c * bold)] + ([max(1.2, c * bold) + 2 * lw] if outline else [])
    cap = cap or min(bh / len(lines) * 0.62, 22)
    cap = min(cap, bh / len(lines) * 0.75)
    cap, condense, rs = fit(lines, cap, bw - 2, bh, widths_of)
    lh = cap + widths_of(cap)[-1]
    total = lh * len(lines)
    top = y0 + (bh - total) / 2 + vshift
    for k, (masks, (tw, th), (px, py)) in enumerate(rs):
        size = (int(tw) + 1, int(th) + 1)
        layers = [m.resize(size, Image.BOX) for m in masks]
        inkw = tw - 2 * px
        ox = {'center': x0 + (bw - inkw) / 2, 'left': x0 + 1, 'right': x1 - inkw}[align] - px + hshift
        oy = top + k * lh - py + (widths_of(cap)[-1] - widths_of(cap)[0]) / 2
        ox, oy = int(round(ox)), int(round(oy))
        for y in range(size[1]):
            for x in range(size[0]):
                tx, ty = ox + x, oy + y
                if not (x0 <= tx <= x1 and y0 <= ty <= y1):
                    continue
                c = layers[0].getpixel((x, y)) / 255
                e = layers[1].getpixel((x, y)) / 255 if outline else 0
                if c <= 0.04 and e <= 0.08:
                    continue
                base = cv.get(tx, ty)
                col = base[:3]
                if e > 0:
                    col = tuple(p + (q - p) * min(1, e * 1.3) for p, q in zip(col, outline))
                if c > 0:
                    col = tuple(p + (q - p) * min(1, c * 1.15) for p, q in zip(col, fill))
                a = base[3] + (max(alpha, base[3]) - base[3]) * max(c, e)
                cv.put(tx, ty, cv.index(col, a))

def clear(cv, box, text, dark=False, area=None, amin=0x60, outline=True, **kw):
    """Text on transparency: fill/outline = bright/dark opaque colours of the box, which is
    cleared to the most transparent palette entry and redrawn. area(x, y) -> bool limits the
    cleared pixels (a shape inside the box); amin = alpha of the pixels measured."""
    x0, y0, x1, y1 = box
    inside = [(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1) if area is None or area(x, y)]
    opq = sorted((cv.get(x, y) for x, y in inside if cv.get(x, y)[3] >= amin), key=luma)
    fill, line = opq[len(opq) * 19 // 20], opq[len(opq) // 10]
    if dark:
        fill, line = opq[len(opq) // 20], opq[len(opq) * 9 // 10]
    t = min(range(len(cv.pal)), key=lambda i: cv.pal[i][3])
    for x, y in inside:
        cv.put(x, y, t)
    if text:
        draw(cv, box, text, fill[:3], line[:3] if outline else None,
             max(fill[3], line[3]) if outline else fill[3], **kw)

def plate(cv, text, box=None, mode='light', erase_box=None, style=None, copy=None, **kw):
    """copy = ((sx, y0, y1), (x0, x1)): after erasing, rows y0-y1 of columns x0-x1 become
    copies of column sx (rebuilds a straight rule the old lettering was joined to)."""
    box = _box(cv, box)
    if mode == 'clear':
        return clear(cv, box, text, **kw)
    if mode == 'holes':
        # lettering cut out of a shape (faint menu art): transparent pixels with shape on both
        # sides of their row and column are filled from the shape around them
        x0, y0, x1, y1 = box
        amin = (style or {}).get('amin', 0x18)
        solid = lambda x, y: cv.get(x, y)[3] >= amin
        mask = {(x, y) for y in range(y0, y1 + 1) for x in range(x0, x1 + 1) if not solid(x, y)
                and any(solid(i, y) for i in range(x0, x)) and any(solid(i, y) for i in range(x + 1, x1 + 1))
                and any(solid(x, j) for j in range(y0, y)) and any(solid(x, j) for j in range(y + 1, y1 + 1))}
        assert not text, 'holes: erase only'
        return erase(cv, box, mask)
    boxes = erase_box if isinstance(erase_box, list) else [erase_box or box]
    mask, fill, outline, alpha = plate_style(cv, boxes[0], mode, **(style or {}))
    for b in boxes[1:]:
        mask |= plate_style(cv, b, mode, **(style or {}))[0]
    if mode == 'glow':
        kw.setdefault('lw', 1.4)
    for b in boxes:
        erase(cv, b, mask)
    if copy:
        (sx, cy0, cy1), (cx0, cx1) = copy
        for y in range(cy0, cy1 + 1):
            for x in range(cx0, cx1 + 1):
                cv.put(x, y, cv.idx(sx, y))
    if text:
        draw(cv, box, text, fill, outline, alpha, **kw)

# ---------------------------------------------------------------- banners (Taiko 5)

def _runs(row):
    out, x = [], 0
    while x < len(row):
        if row[x]:
            x0 = x
            while x < len(row) and row[x]:
                x += 1
            out.append((x0, x))
        x += 1
    return out

def banner(cv, text, cap=None, fill_pct=0.5):
    """Replace the lettering of an outlined banner on transparency with text (centred):
    fill rows, outline and outer glow measured from the original."""
    w, h = cv.w, cv.h
    A = [[cv.get(x, y)[3] for x in range(w)] for y in range(h)]
    Lm = [[luma(cv.get(x, y)) for x in range(w)] for y in range(h)]
    solid = [[A[y][x] >= 0x70 for x in range(w)] for y in range(h)]
    ys = [y for y in range(h) if any(solid[y])]
    # outline = solid pixels touching non-solid; fill = solid pixels 2+ px inside
    edge, fillpx = collections.Counter(), collections.defaultdict(list)
    for y in ys:
        for x in range(w):
            if not solid[y][x]:
                continue
            near = [solid[y + dy][x + dx] if 0 <= y + dy < h and 0 <= x + dx < w else False
                    for dx in range(-2, 3) for dy in range(-2, 3)]
            if not all(near[6:9] + near[11:14] + near[16:19]):
                edge[cv.idx(x, y)] += 1
            elif all(near):
                fillpx[y].append(cv.idx(x, y))
    outline = edge.most_common(1)[0][0]
    fill = {y: sorted(v, key=lambda i: luma(cv.pal[i]))[int(len(v) * fill_pct)] for y, v in fillpx.items() if v}
    glow = collections.Counter(cv.idx(x, y) for y in range(h) for x in range(w) if 0 < A[y][x] < 0x70)
    glow_rgb = cv.pal[glow.most_common(1)[0][0]][:3] if glow else None
    transparent = min(range(len(cv.pal)), key=lambda i: cv.pal[i][3])
    y0, y1 = ys[0], ys[-1]
    cap = cap or (y1 - y0 + 1) * 0.7
    condense = 1.0
    while True:
        stroke = max(2.0, cap * 0.16)
        widths = [stroke, stroke + 2 * max(1, cap * 0.06), stroke + 2 * max(2, cap * 0.12)]
        masks, (tw, th), _ = text_masks(text, cap, widths, condense)
        if tw <= w - 4 or cap < 10:
            break
        if condense > 0.8:
            condense *= 0.96
        else:
            cap *= 0.95
    if tw > w - 4:
        raise ValueError('banner %r: %d px wide, texture %d' % (text, tw, w))
    size = (int(tw) + 1, int(th) + 1)
    core, mid, out = (m.resize(size, Image.BOX) for m in masks)
    ox = (w - size[0]) // 2
    oy = (y0 + y1) // 2 - size[1] // 2
    fy = sorted(fill)
    for y in range(h):
        for x in range(w):
            cv.put(x, y, transparent)
    for y in range(size[1]):
        ty = oy + y
        if not 0 <= ty < h:
            continue
        row = fill[min(fy, key=lambda r: abs(r - (y0 + (y1 - y0) * y / size[1])))]
        for x in range(size[0]):
            tx = ox + x
            c, m, o = core.getpixel((x, y)) / 255, mid.getpixel((x, y)) / 255, out.getpixel((x, y)) / 255
            if c > 0.5:
                i = row
            elif m > 0.5:
                i = outline
            elif o > 0.15 and glow_rgb:
                i = cv.index(glow_rgb, o * 0x60)
            else:
                continue
            cv.put(tx, ty, i)

# ---------------------------------------------------------------- items

# (pack, object): (kind, options) ; kind 'plate' (default), 'banner', 'plates' (several
# boxes: options = list of dicts for plate()), 'stack' (textures joined vertically)
GLOW = {'mode': 'glow', 'bold': 0.2}
def frames(n, h, inner, shifts):
    """plates for an animation strip of n frames h px tall: inner = (x0, y0, x1, y1) of
    frame 0; shifts = {frame: px} for frames where the text slides in/out (None = empty)."""
    out = []
    for k in range(n):
        x0, y0, x1, y1 = inner
        o = {'box': (x0, y0 + k * h, x1, y1 + k * h), 'style': {'grow': 3}}
        if k in shifts:
            if shifts[k] is None:
                o['empty'] = True
            else:
                o['hshift'] = shifts[k]
        out.append(o)
    return out

CMD = ('plate', {'box': (6, 5, 57, 26), 'erase_box': (5, 3, 58, 28)})            # MAINCG 64x32 command buttons: inside the frame + bevel
GOLD = ('plate', {'mode': 'dark', 'box': (4, 4, -5, -5)})      # gold title plates, embossed dark kanji
def shadow(x0, y0, x1, y1, pad=5):
    """light lettering with a soft dark drop shadow on busy art: the erase box reaches past the
    shadow so the plate colour measured is the art behind it"""
    return {'box': (x0, y0, x1, y1), 'erase_box': (x0 - pad, y0 - pad, x1 + pad, y1 + pad), 'style': {'grow': 6}}
TWO = ('plates', [{'box': (1, 1, 46, 22), 'mode': 'dark'}, {'box': (1, 25, 46, 46), 'mode': 'dark'}])     # 48x48 = two 48x24 states
ITEMS = {
    **{('MAINCG', k): CMD for k in range(237, 396)},
    **{('MAINCG', k): TWO for k in (595, 598, 602, 603, 610, 611)},
    ('MAINCG', 634): ('plates', [{'box': (48 * c + 2, 24 * r + 2, 48 * c + 45, 24 * r + 21), 'mode': 'dark', 'ti': c + (5 if r >= 2 else 0)}
                                 for r in range(4) for c in range(5 if r < 2 else 4)]),
    ('MAINCG', 159): ('plate', {'box': (36, 5, 104, 26)}),          # keep the triangle icon
    ('MAINCG', 551): ('plate', {'box': (3, 3, 50, 28)}),            # label at the left end of the bar
    ('MAINCG', 552): ('plate', {'box': (133, 3, 180, 28)}),
    **{('MAINCG', k): ('plate', {'mode': 'dark'}) for k in (594, 596, 597, 599, 600, 601, 604, 605, 606, 609, 612, 613, 638, 639, 640)},
    **{('MAINCG', k): ('plate', {'mode': 'clear', 'cap': 30}) for k in (644, 645)},   # red brush stamps on transparency
    **{('MAINCG', k): ('plates', [{'box': (75, 4 + y, 196, 19 + y), 'style': {'grow': 3}} for y in (0, 64)]) for k in range(494, 510) if k not in (506, 507)},
    **{('MAINCG', k): ('plates', [{'box': (75, 4 + y, 196, 19 + y), 'style': {'grow': 3}, 'ti': 0} for y in (0, 64)]
                       + [{'box': (x, 34 + y, x + 16, 50 + y), 'cap': 10, 'style': {'sens': (12, 0.3)}, 'ti': t} for y in (0, 64) for x, t in ((12, 1), (241, 2))]) for k in (506, 507)},
    **{('MAINCG', k): ('plates', [{'box': (3, 2 + y, 68, 21 + y), 'ti': 0} for y in (0, 24)]) for k in range(510, 532)},
    **{('MAINCG', k): ('plates', [{'box': (40, 3 + y, 108, 21 + y), 'mode': 'dark', 'ti': 0} for y in (0, 24)]) for k in (487, 488, 489)},
    **{('COMMON', k): TWO for k in list(range(42, 48)) + list(range(108, 138))},     # dark lettering (TWO)
    ('COMMON', 24): ('plate', {'box': (36, 5, 121, 26)}),             # keep the cross icon
    **{('COMMON', k): ('plate', {'mode': 'dark'}) for k in list(range(48, 58)) + list(range(138, 146))},
    ('COMMON', 191): ('plates', [{'box': (122, 4, 145, 27), 'cap': 9}, {'box': (82, 34, 105, 59), 'cap': 9}]),   # 攻/守 at the end of the gauge bars
    ('COMMON', 184): ('plates', [{'box': (24, 4 + y, 76, 27 + y), 'mode': 'dark', 'ti': 0} for y in (0, 32)]),   # arrow on the left
    ('COMMON', 187): ('plates', [{'box': (4, 4 + y, 54, 27 + y), 'mode': 'dark', 'ti': 0} for y in (0, 32)]),    # arrow on the right
    # title logo: パワーアップキット (white, joined to a zigzag underline) -> English; the Japanese title stays
    ('STARTUP', 0): ('plate', {'box': (182, 292, 472, 350), 'erase_box': [(174, 287, 480, 362), (178, 281, 232, 290), (366, 279, 424, 292)], 'cap': 44, 'bold': 0.18,
                               'style': {'sens': (40, 0.5), 'grow': 2}}),
    **{('STARTUP', k): ('plate', {'box': (8, 5, 239, 26)}) for k in range(9, 37)},      # main-menu buttons, 4 states each
    **{('EDITORPK', k): ('plate', {'mode': 'dark', 'box': (8, 5, 279, 34)}) for k in range(2, 37)},
    **{('EDKAHOU', k): ('plate', {'mode': 'dark', 'box': (2, 2, 69, 21)}) for k in range(5, 57)},
    **{(p, 2): ('plate', {'box': (4, 4, -5, -5)}) for p in ('EDBUSHOU', 'EDDAI', 'EDHIME', 'EDNINJA', 'EDSEI', 'EDSHIRO')},
    ('EDKAHOU', 4): ('plate', {'box': (4, 4, -5, -5)}),
    # battle label sheets (cells = the N9GD tile rects); one-kanji cells 命 隊 気 (48-120, row 2) = unit panel order / squads / morale
    ('WARCG', 4): ('plates', [{'box': (48 * c + 1, 1, 48 * c + 46, 22)} for c in range(10)]
                   + [{'box': (x0 + 1, 25, x1 - 2, 46)} for x0, x1 in ((0, 48), (120, 168), (168, 216))]
                   + [{'box': (x + 1, 25, x + 22, 46), 'cap': 9} for x in (48, 72, 96)]),
    ('WARCG', 16): ('plates', [{'box': (72 * c + 1, y + 1, 72 * c + 70, y + 22), 'ti': c} for y in (0, 24) for c in range(3)]
                    + [{'box': (x + 1, 1, x + 22, 22), 'ti': 3 + k, 'cap': 9} for k, x in enumerate((384, 408))]),
    # save/load screen (512x448 + right strip 1): 選択大名 / 保存日 / 難易度 (straddles the seam)
    ('SAVELOAD', 0): ('stack', {'with': [1], 'across': True, 'kind': 'plates', 'opts': [
        {'box': (192, 318, 278, 342), 'erase_box': (190, 314, 280, 346), 'align': 'right'},
        {'box': (440, 318, 513, 342), 'erase_box': (452, 314, 515, 346), 'align': 'right'},
        {'box': (470, 290, 563, 314), 'erase_box': (490, 286, 566, 316), 'align': 'right'}]}),
    ('SAVELOAD', 5): ('plate', {'box': (2, 2, 88, 21)}),               # ミッション label left of an empty field
    ('SOUNDMD', 0): ('plate', {'box': (62, 24, 232, 48), 'align': 'left', 'cap': 15}),
    ('KAISEN', 4): ('plate', {'box': (6, 42, 66, 66)}),                  # 船の数 tab under the panel
    ('KAISEN', 5): ('plate', {'box': (138, 42, 200, 66)}),
    # mission select screen: 使用シナリオ 期限年月 選択可能大名 達成条件 失敗条件 (panel headings)
    ('MISSION', 0): ('plates', [{'box': b, 'align': 'left', 'cap': 13} for b in
                                ((24, 106, 235, 127), (24, 166, 235, 194), (20, 242, 260, 264), (288, 73, 500, 94), (288, 217, 500, 239))]),
    ('MISSION', 2): ('plate', {'box': (300, 62, 495, 88), 'cap': 13}),     # クリアミッション数
    ('EDEVENT', 2): ('plate', {'box': (24, 17, 210, 47), 'align': 'left', 'cap': 15}),    # イベント登録 on the painting
    ('EDEVENT', 4): ('plates', [{'box': b, 'align': 'left', 'cap': 13} for b in ((30, 73, 260, 97), (30, 261, 260, 285))]),
    # mission result: ミッション / 選択大名 / 達成年月 labels left of their fields; 2/3 = failed/achieved banners
    ('MRESULT', 0): ('plates', [{'box': b, 'align': 'right', 'cap': 13} for b in ((50, 276, 184, 302), (50, 316, 184, 342), (330, 316, 450, 342))]),
    **{('MRESULT', k): ('plate', {'box': (20, 5, 323, 43), 'cap': 22, 'style': {'sens': (14, 0.2), 'grow': 6}}) for k in (2, 3)},
    **{('MAINCG', k): GOLD for k in list(range(396, 439)) + list(range(580, 590))},
    # tutorial diagrams (TGAIYOU): 1 map legend plates, 2 callout bubbles, 3 arrow labels, 6 org chart,
    # 7 diplomacy chart, 8 strategy chart; vertical Japanese labels become short horizontal English
    ('TGAIYOU', 0): ('plate', {'box': (95, 147, 290, 186), 'erase_box': [(90, 147, 295, 199), (90, 139, 170, 147), (214, 139, 295, 147)], 'cap': 29, 'bold': 0.18,
                               'style': {'sens': (40, 0.5), 'grow': 2}}),     # logo copy, same treatment as STARTUP 0
    ('TGAIYOU', 1): ('plates', [{'box': (4, y + 3, 83, y + 20), 'mode': 'dark'} for y in (40, 80, 120)]),
    ('TGAIYOU', 2): ('plates', [{'box': b, 'mode': 'dark', 'align': 'left'} for b in
                                ((10, 39, 122, 88), (6, 152, 131, 187), (10, 234, 247, 252), (110, 268, 345, 286))]),
    ('TGAIYOU', 3): ('plates', [shadow(8, 24, 112, 44, 3), shadow(20, 88, 104, 107, 3), {'box': (4, 111, 121, 157), 'style': {'grow': 4}}]),
    ('TGAIYOU', 6): ('plates', [{'box': (30, 6, 96, 29), 'mode': 'dark'},
                                {'box': (158, 48, 262, 92), 'mode': 'dark'}, {'box': (305, 53, 407, 92), 'erase_box': [(305, 53, 407, 92), (305, 44, 350, 53), (358, 44, 407, 53)], 'mode': 'dark'},
                                {'box': (18, 106, 104, 177), 'mode': 'dark', 'ti': 3},
                                {'box': (163, 106, 248, 177), 'mode': 'dark', 'ti': 3}, {'box': (310, 106, 401, 177), 'mode': 'dark', 'ti': 3},
                                {'box': (20, 186, 104, 205), 'ti': 4}, {'box': (168, 186, 248, 205), 'ti': 5}, {'box': (313, 186, 400, 205), 'ti': 6}]),
    ('TGAIYOU', 7): ('plates', [shadow(45, 13, 167, 29), shadow(78, 33, 126, 51), shadow(216, 33, 304, 51),
                                {'box': (62, 80, 147, 104)}, {'box': (214, 80, 296, 104)},
                                {'box': (14, 48, 52, 136), 'mode': 'dark'}, {'box': (161, 48, 199, 136), 'mode': 'dark'},
                                {'box': (309, 48, 348, 136), 'mode': 'dark', 'ti': 5}]),
    ('TGAIYOU', 8): ('plates', [{'box': (40, 11, 182, 34), 'mode': 'dark'}, {'box': (270, 11, 360, 34), 'mode': 'dark'},
                                {'box': (27, 40, 110, 78), 'mode': 'dark'}, {'box': (131, 40, 213, 78), 'mode': 'dark'},
                                {'box': (236, 36, 259, 75)}, {'box': (236, 79, 259, 114)},
                                {'box': (282, 40, 363, 78), 'mode': 'dark'}, shadow(298, 84, 374, 101),
                                {'box': (282, 109, 363, 147), 'mode': 'dark'},
                                shadow(63, 94, 102, 111), shadow(131, 94, 176, 111),
                                {'box': (79, 129, 162, 166), 'mode': 'dark', 'ti': 6},
                                {'box': (40, 175, 182, 196), 'mode': 'dark', 'ti': 11}, {'box': (252, 166, 383, 202), 'mode': 'dark', 'ti': 12}]),
}

def load_tl():
    """{(pack, object): text} from tl/gfx.txt ('MAINCG.237<TAB>Sell')."""
    out = {}
    if os.path.exists(TL):
        for n, line in enumerate(open(TL, encoding='ascii').read().splitlines(), 1):
            if line.strip() and not line.startswith('#'):
                k, _, v = line.partition('\t')
                p, _, t = k.partition('.')
                assert p in tim2.FILES, 'tl/gfx.txt:%d: unknown pack %r' % (n, p)
                out[p, int(t)] = v
    return out

def redraw(pack, key, text):
    """-> (edited textures, clean textures) for one tl/gfx.txt item; pack = clean pack bytes."""
    kind, opts = ITEMS.get(key, ('plate', {}))
    ids = [key[1]]
    across = False
    if kind == 'stack':
        ids += opts['with']
        across = opts.get('across', False)
        kind, opts = opts['kind'], opts.get('opts', {})
    texs = [tim2.Texture(pack, i) for i in ids]
    cv = Stack(texs, across) if len(texs) > 1 else Canvas(texs[0])
    if kind == 'plate':
        plate(cv, text, **opts)
    elif kind == 'banner':
        banner(cv, text, **opts)
    elif kind == 'plates':
        texts = text.split(';')
        if len(texts) == 1:
            texts *= max([len(opts)] + [o.get('ti', 0) + 1 for o in opts])
        for k, o in enumerate(opts):
            o = dict(o)
            t = texts[o.pop('ti', k)]       # 'ti' = which ';' spot of the tl text (state copies share one)
            if o.pop('empty', False):
                t = ''
            plate(cv, t, **o)
    else:
        raise ValueError(kind)
    return texs, [tim2.Texture(pack, i) for i in ids]

def apply(file_fn):
    """-> {iso path: new pack bytes}; file_fn(iso path) = clean file bytes."""
    by_pack = collections.defaultdict(dict)
    for (p, t), text in load_tl().items():
        by_pack[p][t] = text
    out = {}
    for p, items in sorted(by_pack.items()):
        name = tim2.FILES[p]
        clean = file_fn(name)
        texs = []
        for t, text in sorted(items.items()):
            try:
                texs += redraw(clean, (p, t), text)[0]
            except (ValueError, AssertionError) as err:
                raise ValueError('gfx %s.%d: %s' % (p, t, err))
        assert len({x.i for x in texs}) == len(texs), 'gfx %s: a texture is edited twice' % p
        out[name] = tim2.apply(clean, texs)
    return out

# ---------------------------------------------------------------- dev previews (Koei data: keep out of git)

def extracted(name):
    import iso, setup
    return iso.Disc(setup.CLEAN).file(name)

def checker(w, h):
    im = Image.new('RGBA', (w, h), (90, 110, 130, 255))
    dr = ImageDraw.Draw(im)
    for y in range(0, h, 8):
        for x in range((y // 8 % 2) * 8, w, 16):
            dr.rectangle((x, y, x + 7, y + 7), fill=(110, 130, 150, 255))
    return im

def _stacked(texs):
    ims = [t.image() for t in texs]
    across = len({i.width for i in ims}) > 1          # a screen + its right strip
    if across:
        out = Image.new('RGBA', (sum(i.width for i in ims), max(i.height for i in ims)))
    else:
        out = Image.new('RGBA', (max(i.width for i in ims), sum(i.height for i in ims)))
    o = 0
    for i in ims:
        out.paste(i, (o, 0) if across else (0, o)); o += i.width if across else i.height
    return out

def preview(out, keys=None, scale=3):
    """Before/after sheet of tl/gfx.txt items whose 'PACK.n' starts with one of keys."""
    tl = load_tl()
    cells, packs = [], {}
    for (p, t), text in sorted(tl.items()):
        if keys and not any(('%s.%d' % (p, t)).startswith(k) for k in keys):
            continue
        d = packs.setdefault(p, extracted(tim2.FILES[p]))
        try:
            b, a = redraw(d, (p, t), text)
        except (ValueError, AssertionError) as err:
            print('%s.%d: %s' % (p, t, err))
            continue
        pair = []
        for x in (a, b):
            im = _stacked(x)
            im = im.resize((im.width * scale, im.height * scale), Image.NEAREST)
            bg = checker(im.width, im.height); bg.alpha_composite(im)
            pair.append(bg)
        cells.append(('%s.%d' % (p, t), pair))
    W = max([1200] + [a.width * 2 + 4 for _, (a, b) in cells])
    x = y = rowh = 0
    pos = []
    for lab, (a, b) in cells:
        cw = a.width + b.width + 4
        if x + cw > W:
            x, y, rowh = 0, y + rowh + 16, 0
        pos.append((x, y))
        x += max(cw, 60) + 10
        rowh = max(rowh, a.height)
    sheet = Image.new('RGBA', (W, y + rowh + 16), (30, 30, 30, 255))
    dr = ImageDraw.Draw(sheet)
    for (lab, (a, b)), (px, py) in zip(cells, pos):
        dr.text((px, py), lab, fill=(255, 255, 0, 255))
        sheet.alpha_composite(a, (px, py + 12))
        sheet.alpha_composite(b, (px + a.width + 4, py + 12))
    sheet.save(out)
    print(out, len(cells))

if __name__ == '__main__':
    # gfx.py preview out.png [key prefixes...] [--scale N]
    a = sys.argv[3:]
    sc = 3
    if '--scale' in a:
        k = a.index('--scale'); sc = int(a[k + 1]); del a[k:k + 2]
    preview(sys.argv[2], a, sc)

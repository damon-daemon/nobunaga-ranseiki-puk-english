"""Koei N9GD textures (standard TIM2 inside a wrapper) in NB9 packs (port of Soutenroku tools/tim2.py).

In Ranseiki every entry of a texture pack (tools/nb9.py table) is one N9GD object, so a
texture is addressed as (pack, entry index); the packs without a table (KAOCG, ITEMCG,
COMBG) have no text and are not handled.

N9GD header 0x20: 'N9GD', u32 n (1 + count of 16-byte tile rects after the header), 0x20,
u32 TIM2 offset (0x30 + (n-1)*0x10), i32 palette ref (-1 = own CLUT; >= 0 objects still carry
a copy here), 0, display w, h; TIM2 at that offset: one picture, 4bpp or 8bpp indexed,
32-bit CLUT (8bpp CLUTs CSM1-swizzled: bits 3/4 of the index swapped). Palette-only
objects (w = h = 0) and the CLUT-less font glyphs exist, so textures are only ever edited
in place: pixels change, every header, size and palette stays. apply() asserts that.
"""
import struct
import nb9

FILES = {n.split('/')[-1].split('.')[0]: n for n in (
    'MEDIA/DATA/STARTUP.NB9', 'MEDIA/DATA/MAINCG.NB9', 'MEDIA/DATA/COMMON.NB9', 'MEDIA/DATA/SAVELOAD.NB9',
    'MEDIA/DATA/SOUNDMD.NB9', 'MEDIA/DATA/KAISEN.NB9', 'MEDIA/DATA/TGAIYOU.NB9', 'MEDIA/DATA/TGAIYOUB.NB9',
    'MEDIA/DATA/THYOUJOU.NB9', 'MEDIA/DATA/EVENT.NB9', 'MEDIA/DATA/SHIROCG.NB9',
    'MEDIA/DATA2/EDITORPK.NB9', 'MEDIA/DATA2/EDBUSHOU.NB9', 'MEDIA/DATA2/EDDAI.NB9', 'MEDIA/DATA2/EDHIME.NB9',
    'MEDIA/DATA2/EDNINJA.NB9', 'MEDIA/DATA2/EDSEI.NB9', 'MEDIA/DATA2/EDSHIRO.NB9', 'MEDIA/DATA2/EDKAHOU.NB9',
    'MEDIA/DATA2/EDEVENT.NB9', 'MEDIA/DATA2/MISSION.NB9', 'MEDIA/DATA2/MRESULT.NB9', 'MEDIA/DATA2/NEWB.NB9',
    'MEDIA/WAR/WARCG.NB9', 'MEDIA/WAR/WARMAPCG.NB9')}

def _swz(i):
    return (i & ~0x18) | ((i & 8) << 1) | ((i & 0x10) >> 1)

class Texture:
    """N9GD object i of a pack: pix = palette indexes (row-major), pal = [(r, g, b, a)] in
    index order (a = PS2 alpha 0-0x80). Offsets (img) are into the pack."""
    def __init__(self, pack, i):
        self.pack, self.i = pack, i
        o, self.size = nb9.toc(pack)[i]
        self.off = o
        tim = o + struct.unpack_from('<I', pack, o + 0xC)[0]
        assert pack[o:o + 4] == b'N9GD' and pack[tim:tim + 4] == b'TIM2', (i, hex(o))
        self.ref = struct.unpack_from('<i', pack, o + 0x10)[0]
        p = tim + 0x10
        tot, clsz, imsz, hsz, clc, pfmt, mip, clt, imt, w, h = struct.unpack_from('<IIIHHBBBBHH', pack, p)
        assert imt in (4, 5) and mip == 1, (i, imt, mip)
        self.w, self.h, self.bpp = w, h, 4 if imt == 4 else 8
        self.img, self.img_size = p + hsz, imsz
        raw = pack[self.img:self.img + imsz]
        if self.bpp == 8:
            self.pix = list(raw[:w * h])
        else:
            self.pix = [v for b in raw for v in (b & 15, b >> 4)][:w * h]
        assert clc, 'object %d has no CLUT (font glyph?)' % i
        n = 16 if imt == 4 else 256
        c = self.img + imsz
        pal = [tuple(pack[c + 4 * k:c + 4 * k + 4]) for k in range(n)]
        if imt == 5 and not clt & 0x80:
            pal = [pal[_swz(k)] for k in range(n)]
        self.pal = pal

    def get(self, x, y):
        return self.pal[self.pix[y * self.w + x]]

    def idx(self, x, y):
        return self.pix[y * self.w + x]

    def put(self, x, y, i):
        assert 0 <= i < len(self.pal)
        self.pix[y * self.w + x] = i

    def pixels(self):
        """Image bytes (same size as the original)."""
        if self.bpp == 8:
            out = bytes(self.pix)
        else:
            p = self.pix + [0] * (-len(self.pix) % 2)
            out = bytes(p[k] | p[k + 1] << 4 for k in range(0, len(p), 2))
        out += self.pack[self.img + len(out):self.img + self.img_size]
        assert len(out) == self.img_size
        return out

    def image(self, palette=None):
        from PIL import Image
        pal = palette or self.pal
        im = Image.new('RGBA', (self.w, self.h))
        im.putdata([pal[i][:3] + (min(255, pal[i][3] * 2),) for i in self.pix])
        return im

def apply(pack, textures):
    """Pack bytes with the pixels of the edited textures written back in place."""
    e = bytearray(pack)
    for t in textures:
        e[t.img:t.img + t.img_size] = t.pixels()
    assert len(e) == len(pack)
    # headers, tables and palettes unchanged: only image bytes may differ
    spans = sorted((t.img, t.img + t.img_size) for t in textures)
    k = 0
    for a, b in spans:
        assert e[k:a] == pack[k:a]
        k = b
    assert e[k:] == pack[k:]
    return bytes(e)

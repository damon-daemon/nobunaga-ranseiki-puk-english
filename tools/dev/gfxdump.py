"""Contact sheet of NB9 textures (dev; output is Koei data: keep it out of git).

usage: gfxdump.py PACK[.a-b] ... out.png [--scale N] [--grid]
--grid draws a 10 px ruler grid (labels every 50 px) over each texture.
"""
import os, sys
from PIL import Image, ImageDraw
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import gfx, nb9, tim2

def textures(spec):
    p, _, r = spec.partition('.')
    d = gfx.extracted(tim2.FILES[p])
    if r:
        a, _, b = r.partition('-')
        ids = range(int(a), int(b or a) + 1)
    else:
        ids = range(len(nb9.toc(d)))
    for i in ids:
        try:
            t = tim2.Texture(d, i)
        except Exception:
            continue
        if t.w and t.h:
            yield '%s.%d' % (p, i), t

def main():
    args = sys.argv[1:]
    scale = 2
    grid = '--grid' in args
    if grid:
        args.remove('--grid')
    if '--scale' in args:
        k = args.index('--scale'); scale = int(args[k + 1]); del args[k:k + 2]
    out = args.pop()
    cells = []
    for spec in args:
        for name, t in textures(spec):
            im = t.image().resize((t.w * scale, t.h * scale), Image.NEAREST)
            bg = gfx.checker(im.width, im.height); bg.alpha_composite(im)
            if grid:
                dr = ImageDraw.Draw(bg)
                for x in range(0, t.w, 10):
                    dr.line((x * scale, 0, x * scale, bg.height), fill=(255, 0, 0, 90 if x % 50 else 200))
                for y in range(0, t.h, 10):
                    dr.line((0, y * scale, bg.width, y * scale), fill=(255, 0, 0, 90 if y % 50 else 200))
            cells.append(('%s %dx%d %dbpp' % (name, t.w, t.h, t.bpp), bg))
    W = max([1000] + [c.width for _, c in cells])
    x = y = rowh = 0
    pos = []
    for lab, c in cells:
        if x + c.width > W:
            x, y, rowh = 0, y + rowh + 14, 0
        pos.append((x, y))
        x += max(c.width, 110) + 8
        rowh = max(rowh, c.height)
    sheet = Image.new('RGBA', (W, y + rowh + 14), (30, 30, 30, 255))
    dr = ImageDraw.Draw(sheet)
    for (lab, c), (px, py) in zip(cells, pos):
        dr.text((px, py), lab, fill=(255, 255, 0, 255))
        sheet.alpha_composite(c, (px, py + 12))
    sheet.save(out)
    print(out, len(cells))

if __name__ == '__main__':
    main()

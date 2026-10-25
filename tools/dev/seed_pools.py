"""One-off (historical: pools.json is now English lists in ELF table order; do not re-run): English for the ELF name pools (notes/survey/namehook_re.md §6),
keyed by the Japanese. Ninja lists take Soutenroku's English where its pools have the same Japanese
(~/Projects/nobunaga_soutenroku_puk_translation tl/names/pools.json + its ELF tables), the rest is
romanised from the readings. Edit the output by hand afterwards; re-running overwrites it.
"""
import json, os, re, struct, subprocess, sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import elfseg, iso, names, setup

SOU = os.path.join(os.path.dirname(setup.ROOT), 'nobunaga_soutenroku_puk_translation')
# (table, count) of {name, reading} pointer pairs
POOLS = {'women': [(0x63FBE0, 100)],
         'ninja_epithets': [(0x64A4D0, 60)], 'ninja_given': [(0x64AA60, 110)],
         'kunoichi_epithets': [(0x64AEF0, 31)], 'kunoichi_given': [(0x64B140, 43)]}
SOU_FOFF = 0x800
SOU_NINJA = {'ninja_epithets': (0x3BDA10, 62), 'ninja_given': (0x3BDFB0, 110),
             'kunoichi_epithets': (0x3BE440, 62), 'kunoichi_given': (0x3BE7F0, 110)}

def s(elf, va, foff=elfseg.FOFF):
    o = va - elfseg.VBASE + foff
    return elf[o:elf.index(b'\0', o)].decode('cp932')

def w(elf, va, foff=elfseg.FOFF):
    return struct.unpack_from('<I', elf, va - elfseg.VBASE + foff)[0]

def pairs(elf, tab, n, foff=elfseg.FOFF):
    return [(s(elf, w(elf, tab + 8 * k, foff), foff), s(elf, w(elf, tab + 8 * k + 4, foff), foff)) for k in range(n)]

def main():
    elf = setup.clean_elf()
    sou_en = json.load(open(os.path.join(SOU, 'tl', 'names', 'pools.json'), encoding='utf-8'))
    # Soutenroku's iso module reads its disc (own process: the module names clash)
    sou_elf = subprocess.run([sys.executable, '-c', 'import sys; sys.path.insert(0, "tools"); import setup; '
                              'sys.stdout.buffer.write(setup.clean_elf())'], cwd=SOU, capture_output=True,
                             check=True).stdout
    known = {}
    for lst, (tab, n) in SOU_NINJA.items():
        for (jp, _), en in zip(pairs(sou_elf, tab, n, SOU_FOFF), sou_en[lst]):
            known.setdefault(jp, en)
    out, stats = {'_doc': 'ELF name pools (namehook.py): Japanese -> English. women: random princess given '
                  'names; ninja/kunoichi: epithet (shown as the family name) + given name.'}, {}
    for lst, tabs in POOLS.items():
        d = {}
        for tab, n in tabs:
            for jp, rd in pairs(elf, tab, n):
                if jp in known:
                    d[jp] = known[jp]
                else:
                    r = re.sub('の$', '', rd)
                    d[jp] = names.romaji(r, 'given' in lst or lst == 'women') + ('' if r == rd else '-no')
        out[lst] = d
        stats[lst] = (len(d), sum(jp in known for jp in d))
    path = os.path.join(names.TL_DIR, 'pools.json')
    with open(path, 'w', encoding='utf-8') as f:
        f.write('{\n' + ',\n'.join('%s: %s' % (json.dumps(k), json.dumps(v, ensure_ascii=False, indent=None))
                                   for k, v in out.items()) + '\n}\n')
    print(path, stats)

if __name__ == '__main__':
    main()

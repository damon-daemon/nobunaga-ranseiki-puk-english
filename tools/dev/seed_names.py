"""One-off: seed tl/names/ from Soutenroku's name lists + romanised readings.

Persons: a (family, given) pair takes Soutenroku's spelling when the Japanese pair matches
(any scenario) and either the readings agree or Soutenroku's spelling is not a plain
romanisation of its own reading (a hand-set spelling); otherwise each part takes
Soutenroku's part spelling (same kanji + reading), else the romanised reading of this
game. 通称 (3rd part): the given name's English when equal to it, else its guessed
reading romanised (names.tsusho_readings), else the given name's English (listed in the
report as TODO). Princesses: Soutenroku women.json by given name.
Other tables: Soutenroku's English where the Japanese name matches, else patterns
(kunishu "X Gentry", navies "X Navy", ninja "X School") or the romanised reading.
Existing tl rows are kept unless --force. --report writes notes/names_seed_report.md.

usage: seed_names.py [table ...] [--force] [--report]
"""
import collections, json, os, sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
import names

SOU = os.path.expanduser('~/Projects/nobunaga_soutenroku_puk_translation/script/names')
REPORT = os.path.join(names.ROOT, 'notes', 'names_seed_report.md')
report = []

def sou(table):
    return json.load(open(os.path.join(SOU, table + '.json'), encoding='utf-8'))

def sou_simple(table):
    """{Japanese name: English} of a one-part Soutenroku table (all scenario forms)."""
    out = {}
    for r in sou(table):
        tl = r.get('tl')
        if not tl:
            continue
        out.setdefault(r['jp'][0], tl['en'])
        for k, v in r.get('jp_scen', {}).items():
            out.setdefault(v[0], tl.get(k, tl['en']))
    return out

def sou_persons():
    """pairs {(family, given): [(reading, [en family, en given])]}, parts
    {(kanji, k): {reading: en}} from Soutenroku persons (readings only for the base form)."""
    pairs, parts = collections.defaultdict(list), collections.defaultdict(dict)
    for r in sou('persons'):
        tl = r.get('tl')
        if not tl:
            continue
        pairs[tuple(r['jp'])].append((tuple(r['reading']), list(tl['en'])))
        for k in (0, 1):
            if r['jp'][k] and tl['en'][k]:
                parts[r['jp'][k], k].setdefault(r['reading'][k], tl['en'][k])
        for s, v in r.get('jp_scen', {}).items():
            pairs[tuple(v)].append((None, list(tl.get(s, tl['en']))))
    return pairs, parts

def person_en(jp, reading, refs, tsusho):
    pairs, parts = refs
    fam, giv = jp[0], jp[1]
    src = 'parts'
    en = None
    for kana, e in pairs.get((fam, giv), []):
        if kana is None or kana == tuple(reading[:2]):
            en, src = list(e), 'sou'
            break
        if e != [names.romaji(kana[0]), names.romaji(kana[1], True)]:
            en, src = list(e), 'sou-hand'      # hand-set spelling, readings differ
            break
        src = 'reading-differs'
    if en is None:
        en = []
        for k in (0, 1):
            if not jp[k]:
                en.append('')
                continue
            e = parts.get((jp[k], k), {}).get(reading[k])
            en.append(e or names.romaji(reading[k], k == 1))
    ts = jp[2]
    if not ts:
        en.append('')
    elif ts == giv:
        en.append(en[1])
    elif tsusho.get(ts):
        en.append(names.romaji(tsusho[ts], True))
    else:
        en.append(en[1])
        src += '+tsusho-todo'
    return en, src

def seed_persons(table, jp, refs, tsusho):
    rows, stats, todo = [], collections.Counter(), []
    for i, forms in enumerate(jp[table]):
        if all(names.is_empty(v[0]) for v in forms.values()):
            continue
        base = names.common(forms)
        en, src = person_en(base[0], base[1], refs, tsusho)
        row = {'id': i, 'en': en}
        stats[src.split('+')[0]] += 1
        if 'todo' in src:
            todo.append('%s %d %s %s -> %s' % (table, i, ' '.join(base[0]), ' '.join(base[1]), ' '.join(en)))
        for k, v in forms.items():
            if v[0] != base[0] and not names.is_empty(v[0]):
                row[k] = person_en(v[0], v[1], refs, tsusho)[0]
        rows.append(row)
    report.append('## %s\n\nsources: %s\n' % (table, dict(stats)))
    report.append('通称 without a reading (English = given name, TODO):\n' + '\n'.join('- ' + x for x in todo) + '\n')
    return rows

def seed_simple(table, jp, refs, pattern=None):
    rows, stats, todo = [], collections.Counter(), []
    def en(v):
        n, r = v[0][0], v[1][0]
        if n in refs:
            stats['sou'] += 1
            return refs[n]
        if pattern:
            e = pattern(n, r)
            if e:
                stats['pattern'] += 1
                return e
        stats['romaji'] += 1
        todo.append('%s %s -> %s' % (n, r, names.romaji(r)))
        return names.romaji(r)
    for i, forms in enumerate(jp[table]):
        if all(names.is_empty(v[0]) for v in forms.values()):
            continue
        base = names.common(forms)
        row = {'id': i, 'en': en(base)}
        for k, v in forms.items():
            if v[0] != base[0] and not names.is_empty(v[0]):
                row[k] = en(v)
        rows.append(row)
    report.append('## %s\n\nsources: %s\n' % (table, dict(stats)))
    if todo:
        report.append('romanised only (review / translate):\n' + '\n'.join('- ' + x for x in todo) + '\n')
    return rows

def place_pattern(jp, suffix, kana_suffix, fmt):
    """'X<suffix>' -> fmt % English of place X (provinces/regions tl, else romanised)."""
    places = {}
    for t in ('provinces', 'regions', 'castles'):
        tl = names.load_tl(t)
        for i, forms in enumerate(jp[t]):
            if i in tl:
                for k, v in forms.items():
                    places.setdefault(v[0][0], names.english(tl[i], k))
    def f(n, r):
        if not n.endswith(suffix):
            return None
        place = n[:-len(suffix)]
        kana = r[:-len(kana_suffix)] if r.endswith(kana_suffix) else r
        return fmt % places.get(place, names.romaji(kana))
    return f

SEA_SUFFIX = [('海峡', 'かいきょう', '%s Strait'), ('水道', 'すいどう', '%s Channel'), ('沖', 'おき', '%s Coast'),
              ('湾', 'わん', '%s Bay'), ('灘', 'なだ', '%s Sea'), ('海', 'かい', '%s Sea')]

def province_pattern(jp, refs):
    """Merged provinces 'AB' -> 'A-B' (A, B single provinces of this table or Soutenroku),
    sea areas 'X沖' etc. -> 'X Coast' (X a province/region, else romanised)."""
    singles = dict(refs) | {'河内': 'Kawachi', '但馬': 'Tajima', '和泉': 'Izumi', '飛騨': 'Hida', '志摩': 'Shima',
                            '伯耆': 'Hoki', '淡路': 'Awaji', '若狭': 'Wakasa', '石見': 'Iwami', '備中': 'Bitchu',
                            '備後': 'Bingo', '長門': 'Nagato', '大隅': 'Osumi', '遠江': 'Totomi', '能登': 'Noto',
                            '丹後': 'Tango', '伊豆': 'Izu', '安房': 'Awa'}
    for forms in jp['provinces']:
        n, r = names.common(forms)
        if n[0] in refs:
            singles[n[0]] = refs[n[0]]
    regions = sou_simple('regions')
    def f(n, r):
        for suf, kana, fmt in SEA_SUFFIX:
            if n.endswith(suf):
                place = n[:-len(suf)]
                kana_ = r[:-len(kana)] if r.endswith(kana) else r
                return fmt % (singles.get(place) or regions.get(place) or names.romaji(kana_))
        for i in range(2, len(n) - 1):
            if n[:i] in singles and n[i:] in singles:
                return '%s-%s' % (singles[n[:i]], singles[n[i:]])
        return None
    return f

TEMPLE_FIXED = {'比叡山延暦寺': 'Enryakuji', '吉崎御坊': 'Yoshizaki Gobo', '鷺森御坊': 'Saginomori Gobo',
                '英賀寺内町': 'Aga Temple Town', '江戸崎不動院': 'Edosaki Fudoin', '箕浦誓願寺': 'Minoura Seiganji'}
TITLE_FIXED = {'出羽介': 'Vice-Governor of Dewa'}

def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    force, do_report = '--force' in sys.argv, '--report' in sys.argv
    jp = names.japanese(names.scenarios())
    tsusho = names.tsusho_readings(jp)
    refs = sou_persons()
    women = sou_simple('women')
    # princesses: given name from Soutenroku women where the kanji matches
    def princess_refs():
        pairs, parts = refs
        p2 = collections.defaultdict(dict, parts)
        for k, v in women.items():
            p2[k, 1].setdefault(None, v)
        return pairs, p2
    def person_en_w(jp_, reading, refs_, ts):
        pairs, parts = refs_
        en, src = person_en(jp_, reading, refs_, ts)
        w = women.get(jp_[1])
        if w and (jp_[1], 1) not in refs[1]:
            en[1] = w
            if jp_[2] == jp_[1]:
                en[2] = w
            src = 'women'
        return en, src
    seeders = {
        'persons': lambda: seed_persons('persons', jp, refs, tsusho),
        'princesses': lambda: _seed_princesses(jp, refs, tsusho, person_en_w),
        'shogunate': lambda: seed_simple('shogunate', jp, {'室町幕府': 'Muromachi Shogunate', '江戸幕府': 'Edo Shogunate'}),
        'regions': lambda: seed_simple('regions', jp, sou_simple('regions')),
        'provinces': lambda: seed_simple('provinces', jp, sou_simple('provinces'),
                                         province_pattern(jp, sou_simple('provinces'))),
        'castles': lambda: seed_simple('castles', jp, sou_simple('castles')),
        'treasures': lambda: seed_simple('treasures', jp, sou_simple('treasures')),
        'courttitles': lambda: seed_simple('courttitles', jp, sou_simple('courttitles') | TITLE_FIXED),
        'offices': lambda: seed_simple('offices', jp, sou_simple('offices')),
        'towns': lambda: seed_simple('towns', jp, {}),
        'temples': lambda: seed_simple('temples', jp, TEMPLE_FIXED),
        'kunishu': lambda: seed_simple('kunishu', jp, sou_simple('kunishu'),
                                       place_pattern(jp, '国衆', 'くにしゅう', '%s Gentry')),
        'navies': lambda: seed_simple('navies', jp, {}, place_pattern(jp, '水軍', 'すいぐん', '%s Navy')),
        'ninja': lambda: seed_simple('ninja', jp, {}, place_pattern(jp, '流', 'りゅう', '%s School')),
    }
    # provinces/regions/castles first: the patterns look their English up
    order = ['regions', 'provinces', 'castles'] + [t for t in seeders if t not in ('regions', 'provinces', 'castles')]
    for t in order:
        if args and t not in args:
            continue
        if os.path.exists(names.tl_path(t)) and not force:
            print('%s: exists (use --force)' % t)
            continue
        rows = seeders[t]()
        names.write_tl(t, rows)
        print('%s: %d rows' % (t, len(rows)))
    if do_report:
        with open(REPORT, 'w', encoding='utf-8') as f:
            f.write('# tl/names seed report (tools/dev/seed_names.py)\n\n' + '\n'.join(report))
        print('report:', REPORT)

def _seed_princesses(jp, refs, tsusho, person_en_w):
    rows, stats = [], collections.Counter()
    for i, forms in enumerate(jp['princesses']):
        base = names.common(forms)
        en, src = person_en_w(base[0], base[1], refs, tsusho)
        stats[src] += 1
        row = {'id': i, 'en': en}
        for k, v in forms.items():
            if v[0] != base[0] and not names.is_empty(v[0]):
                row[k] = person_en_w(v[0], v[1], refs, tsusho)[0]
        rows.append(row)
    report.append('## princesses\n\nsources: %s\n' % dict(stats))
    return rows

if __name__ == '__main__':
    main()

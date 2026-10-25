"""Name-only message units from tl/names (phase 5, port of Soutenroku's names40.py).

Officer bios: `message` 6129 + 2*i = name line of persons record i ("family given", 36 of them
"family given {1B}k reading{1B}H 1479～1545"), 6130 + 2*i = the bio. The officer-info panel does
NOT read the name line (ruler build: the panel showed the record reading and the SCEVAR years
while entry 7305 was a ruler), so the format is free: "Family Given 1479-1545" (reading dropped).
Template bios 9157-9698: name-only units (court families "X家の人々", merchants, monks, women
of the princesses table) paired with template bios, looked up by their Japanese.
`kretsu` 2*i = treasure name of treasures record i (full English, not the short form).

Writes the English of every such unit no tl file has yet into a batch for `tl.py apply`;
`--all` regenerates the units written before (keys in tl/<block>/.namelines.json) so they follow
tl/names after a change: `names.py export`, then `namelines.py --all out`, `tl.py apply`.

usage: .venv/bin/python tools/dev/namelines.py [--all] OUT_DIR   (writes OUT_DIR/message.txt,
       OUT_DIR/kretsu.txt; prints the names it could not match)
"""
import json, os, re, sys
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
import names, tl

BIO_A, BIO_Z, TPL_Z = 6129, 9157, 9699
# Bio name lines headed by a popular name or a form no record has: Japanese -> English.
EXTRA = {
    '斯波詮貞': 'Shiba Akisada', '斯波詮義': 'Shiba Akiyoshi', '山野辺義忠': 'Yamanobe Yoshitada',
    '三浦為春': 'Miura Tameharu', '長尾景長': 'Nagao Kagenaga', '由良泰詮': 'Yura Shigeaki',
    '姉小路頼綱': 'Anegakoji Yoritsuna', '陶晴賢': 'Sue Harukata', '高橋紹運': 'Takahashi Joun',
    '甲斐宗運': 'Kai Soun',
    # template-bio name lines
    '高来喜三郎': 'Takagi Kisaburo', '江戸崎豪盛': 'Edosaki Gosei', '飫肥日良': 'Obi Nichiryo',
    'ろざりおあんりけ': 'Rosario Henrique', 'さんとすぽるろ': 'Santos Porro',
}

def load(t):
    return json.load(open(os.path.join(names.SCRIPT_DIR, t + '.json'), encoding='utf-8'))

PERSONS, PRINCESSES, TREASURES = load('persons'), load('princesses'), load('treasures')

def fold(s):
    return names.hiragana(s)

def person_forms(r):
    """{folded Japanese: English} of a 3-part record: family+given, family+given+通称,
    family+通称+given (PUK officers 1320-1449 are written that way), base and scenario forms."""
    out = {}
    def add(jp, en):
        f, g, t = jp
        if not f and not g:
            return
        e = r['tl'].get(en) if isinstance(en, str) else en
        if e is None:
            e = r['tl']['en']
        fg = '%s %s' % (e[0], e[1]) if e[1] else e[0]
        out.setdefault(fold(f + g), fg)
        if t:
            out.setdefault(fold(f + g + t), fg)
            out.setdefault(fold(f + t + g), fg)
            out.setdefault(fold(f + t), '%s %s' % (e[0], e[2]))
    add(r['jp'], 'en')
    for s, jp in sorted(r.get('jp_scen', {}).items()):
        add(jp, s)
    return out

def by_jp():
    out = {}
    for r in PERSONS + PRINCESSES:
        if 'tl' in r:
            for jp, en in person_forms(r).items():
                out.setdefault(jp, en)
    return out

ALL = by_jp()
FAMILY = {}
for r in PERSONS:
    if r['jp'][0] and 'tl' in r:
        FAMILY.setdefault(r['jp'][0], r['tl']['en'][0])

def split_line(jp):
    """'name [reading] [1479～1545]' -> (name, years or None)."""
    parts = jp.split()
    m = re.fullmatch(r'([0-9?]+)～([0-9?]+)', parts[-1]) if len(parts) > 1 else None
    return parts[0], ('%s-%s' % (m.group(1), m.group(2)) if m else None)

def officer(i, jp, notes):
    head, years = split_line(jp)
    r = PERSONS[i]
    forms = person_forms(r)
    if fold(head) in forms:
        en = forms[fold(head)]
    elif head in EXTRA:
        en = EXTRA[head]
    else:
        # a variant/typo of the record's name: the record's English (base form, else the
        # first scenario form - PUK heroes have a blank base record)
        e = r['tl']['en']
        if not any(e):
            e = next((r['tl'][s] for s in sorted(r['tl']) if s.startswith('s') and any(r['tl'][s])), e)
        en = '%s %s' % (e[0], e[1]) if e[1] else e[0]
        notes.append('persons %d: %s -> %s (record %s)' % (i, head, en, ''.join(r['jp']) or ''.join(next(iter(r.get('jp_scen', {}).values()), ''))))
    return en + (' ' + years if years else '')

def template(jp, notes):
    m = re.fullmatch(r'(.+)家の人々', jp)
    if m:
        fam = FAMILY.get(m.group(1))
        if fam:
            return 'The %s Family' % fam
    if jp in EXTRA:
        return EXTRA[jp]
    return ALL.get(fold(jp))

def name_only(s):
    return s and '。' not in s and '{' not in s and len(s) <= 24

def main():
    args = sys.argv[1:]
    regen = '--all' in args
    out_dir = [a for a in args if not a.startswith('--')][0]
    os.makedirs(out_dir, exist_ok=True)
    for n in ('message', 'kretsu'):
        gen_path = os.path.join(tl.tl_dir(n), '.namelines.json')
        gen = set(json.load(open(gen_path))) if os.path.exists(gen_path) else set()
        tls = tl.expand(n, tl.load_tl(n))
        batch, notes = {}, []
        if n == 'message':
            targets = [(e, 'officer') for e in range(BIO_A, BIO_Z, 2)] + [(e, 'template') for e in range(BIO_Z, TPL_Z)]
        else:
            targets = [(e, 'treasure') for e in range(0, 2 * len(TREASURES), 2)]
        for e, kind in targets:
            toks, spans = tl.units_of(n, e)
            for u, (x, y) in enumerate(spans):
                key = '%d%s' % (e, '.%d' % u if u else '')
                if (e, u) in tls and not (regen and key in gen):
                    continue
                jp = tl.unit_str(toks[x:y]).strip()
                if not jp:
                    continue
                if kind == 'officer':
                    en = officer((e - BIO_A) // 2, jp, notes)
                elif kind == 'treasure':
                    en = TREASURES[e // 2]['tl']['en']
                elif name_only(jp):
                    en = template(jp, notes)
                else:
                    continue
                if en:
                    batch[key] = en
                else:
                    notes.append('MISSING @%s %s' % (key, jp))
        path = os.path.join(out_dir, n + '.txt')
        with open(path, 'w', encoding='utf-8') as f:
            for k, v in batch.items():
                f.write('@%s\n%s\n' % (k, v))
        if os.path.exists(gen_path) or batch:
            json.dump(sorted(gen | set(batch), key=tl.key_order), open(gen_path, 'w'))
        print('%s: %d units written to %s' % (n, len(batch), path))
        for x in notes:
            print('  ' + x)

if __name__ == '__main__':
    main()

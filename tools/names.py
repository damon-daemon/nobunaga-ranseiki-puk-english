"""Names: Japanese from the scenario files on the disc, English from tl/names/.

The fifteen scenario files MEDIA/SCENA1/SCEFIX01-15.NB9 (= scenarios s1-s15) all have the
same layout; each table is a run of fixed-size records with a name field and a full-width
hiragana reading field (TABLES below). The same record can be named differently per
scenario (稲葉山 / 岐阜), so the tl files are keyed by record index with optional
per-scenario overrides:

  tl/names/persons.json  {"id": 13, "en": ["Nanbu", "Nobunao", "Hikokuro"], "s1": [...], ...}
  tl/names/castles.json  {"id": 78, "en": "Gifu", "s1": "Inabayama", ...}

Persons (and princesses) have three parts: family, given and 通称 (common name, e.g.
新三郎; it has no reading field: `export` guesses one from the given names of all
records, the ELF word dictionary and TSUSHO_PARTS). "en" is the form used in most
scenarios, "sN" only where another Japanese name is used. An optional "short" is a cut
form for fixed-size fields when "en" is longer than the Japanese field (bytes - 1, see
TABLES), e.g. a treasure name copied into a record.

  names.py export   -> script/names/<table>.json (per id: Japanese per scenario,
                       readings, romanised reading, current English)
  names.py check    -> validate tl/names/ against the disc
  names.py find X   -> rows whose Japanese, reading or English contain X (all tables;
                       uses script/names/, run export first)
"""
import collections, json, os, re, sys, unicodedata

sys.path.insert(0, os.path.dirname(__file__))
import iso, overflow, setup

ROOT = setup.ROOT
TL_DIR = os.path.join(setup.TL, 'names')
SCRIPT_DIR = os.path.join(setup.SCRIPT, 'names')
SCENARIOS = range(1, 16)
SCEN_FILE = 'MEDIA/SCENA1/SCEFIX%02d.NB9'
SCEN_KEYS = ['s%d' % k for k in SCENARIOS]

# table: (record offset, record size, count, [(name offset, size, reading offset, size)])
# Persons: `02` + id u16, family [10], 通称 [14], given [10], readings [18] + [18], data;
# blank slots have id 0xFFFF (`02 FF FF` markers follow the table). Every other table: `01` + id + tag, name, reading, data; the
# reading sizes are upper bounds (NUL-padded up to the next used field; survey part C 4).
TABLES = {
    'persons':     (0x2A4,   83, 1772, [(3, 10, 0x25, 18), (0x1B, 10, 0x37, 18), (0xD, 14, None, 0)]),
    'princesses':  (0x24E0F, 83, 20,   [(3, 10, 0x25, 18), (0x1B, 10, 0x37, 18), (0xD, 14, None, 0)]),
    'shogunate':   (0x25BAA, 72, 1,    [(3, 18, 21, 50)]),     # 室町幕府
    'regions':     (0x25BF2, 63, 11,   [(3, 8, 11, 34)]),
    'treasures':   (0x26349, 67, 380,  [(3, 14, 17, 50)]),
    'towns':       (0x2C754, 71, 12,   [(3, 18, 21, 50)]),     # merchant towns 商人町
    'temples':     (0x2CAA9, 71, 21,   [(3, 18, 21, 50)]),
    'courttitles': (0x2D152, 56, 350,  [(3, 14, 17, 38)]),
    'kunishu':     (0x31DE3, 71, 54,   [(3, 18, 21, 50)]),     # 国衆 local powers
    'provinces':   (0x32D25, 75, 77,   [(3, 10, 13, 34)]),     # 64 provinces + 13 sea areas
    'ninja':       (0x34400, 71, 12,   [(3, 18, 21, 50)]),     # ninja schools 〜流
    'castles':     (0x34755, 49, 135,  [(3, 10, 13, 34)]),
    'navies':      (0x3618F, 71, 16,   [(3, 18, 21, 50)]),     # 水軍
    'offices':     (0x36600, 56, 64,   [(3, 14, 17, 38)]),     # 役職
}
PERSON_TABLES = ('persons', 'princesses')
PERSON_PARTS = 3                       # family, given, 通称

def field(b):
    return b.split(b'\0')[0].decode('cp932', 'replace')

def table_rows(scen, table):
    """[[(name, reading) per part]] of one scenario file."""
    off, size, count, parts = TABLES[table]
    out = []
    for i in range(count):
        r = scen[off + i * size:off + (i + 1) * size]
        out.append([(field(r[a:a + n]), field(r[b:b + m]) if b is not None else '')
                    for a, n, b, m in parts])
    return out

def scenarios(path=setup.CLEAN):
    disc = iso.Disc(path)
    return [disc.file(SCEN_FILE % k) for k in SCENARIOS]

def japanese(scens):
    """{table: [{scenario key: (names tuple, readings tuple)}]}."""
    out = {}
    for t in TABLES:
        per = [table_rows(s, t) for s in scens]
        out[t] = [{k: (tuple(p[0] for p in per[j][i]), tuple(p[1] for p in per[j][i]))
                   for j, k in enumerate(SCEN_KEYS)} for i in range(TABLES[t][2])]
    return out

def common(forms):
    """Most frequent value of {scenario: value} (earliest scenario on a tie)."""
    c = collections.Counter(forms.values())
    best = max(c.values())
    return next(v for v in forms.values() if c[v] == best)

# ---------------------------------------------------------------- 通称 readings

# ELF word dictionary (kanji word, reading) pairs, va 0x641300-0x649D00 (survey part C).
DICT_VA = (0x641300, 0x649D00)

def elf_dictionary(elf=None):
    """{kanji word: hiragana reading} from the ELF name/word dictionary."""
    if elf is None:
        elf = setup.clean_elf()
    a, b = (v - iso.ELF_FOFF for v in DICT_VA)
    toks = [t.decode('cp932', 'replace') for t in re.split(rb'\0+', elf[a:b]) if t]
    out = {}
    for w, r in zip(toks, toks[1:]):
        if re.fullmatch(r'[ぁ-ゖー]+', r) and not re.fullmatch(r'[ぁ-ゖー]+', w) and w not in out:
            out[w] = r
    return out

# Components of the formulaic common names (又左衛門, 彦太郎, 権兵衛...), longest first.
TSUSHO_PARTS = [
    ('左衛門', 'ざえもん'), ('右衛門', 'えもん'), ('衛門', 'えもん'), ('兵衛', 'べえ'), ('之助', 'のすけ'),
    ('之介', 'のすけ'), ('之丞', 'のじょう'), ('之進', 'のしん'), ('太郎', 'たろう'), ('次郎', 'じろう'),
    ('二郎', 'じろう'), ('三郎', 'さぶろう'), ('四郎', 'しろう'), ('五郎', 'ごろう'), ('六郎', 'ろくろう'),
    ('七郎', 'しちろう'), ('八郎', 'はちろう'), ('九郎', 'くろう'), ('十郎', 'じゅうろう'), ('冠者', 'かじゃ'),
    ('太夫', 'だゆう'), ('大夫', 'だゆう'), ('法師', 'ほうし'), ('大膳', 'だいぜん'), ('主水', 'もんど'),
    ('主膳', 'しゅぜん'), ('主税', 'ちから'), ('内蔵', 'くら'), ('掃部', 'かもん'), ('修理', 'しゅり'),
    ('采女', 'うねめ'), ('織部', 'おりべ'), ('兵庫', 'ひょうご'), ('図書', 'ずしょ'), ('民部', 'みんぶ'),
    ('刑部', 'ぎょうぶ'), ('式部', 'しきぶ'), ('治部', 'じぶ'), ('兵部', 'ひょうぶ'), ('中務', 'なかつかさ'),
    ('弾正', 'だんじょう'), ('左京', 'さきょう'), ('右京', 'うきょう'), ('大炊', 'おおい'), ('主殿', 'とのも'),
    ('内記', 'ないき'), ('外記', 'げき'), ('典膳', 'てんぜん'), ('靱負', 'ゆきえ'), ('隼人', 'はやと'),
    ('勘解由', 'かげゆ'), ('右近', 'うこん'), ('左近', 'さこん'), ('監物', 'けんもつ'), ('玄蕃', 'げんば'),
    ('頼母', 'たのも'), ('将監', 'しょうげん'), ('帯刀', 'たてわき'), ('図書', 'ずしょ'), ('舎人', 'とねり'),
    ('郎', 'ろう'), ('助', 'すけ'), ('介', 'すけ'), ('丞', 'じょう'), ('進', 'しん'), ('蔵', 'ぞう'),
    ('吉', 'きち'), ('新', 'しん'), ('又', 'また'), ('孫', 'まご'), ('彦', 'ひこ'), ('小', 'こ'), ('与', 'よ'),
    ('源', 'げん'), ('平', 'へい'), ('藤', 'とう'), ('弥', 'や'), ('勘', 'かん'), ('久', 'きゅう'), ('伝', 'でん'),
    ('善', 'ぜん'), ('喜', 'き'), ('内', 'ない'), ('甚', 'じん'), ('治', 'じ'), ('市', 'いち'), ('清', 'せい'),
    ('庄', 'しょう'), ('半', 'はん'), ('権', 'ごん'), ('千', 'せん'), ('万', 'まん'), ('惣', 'そう'), ('総', 'そう'),
    ('勝', 'かつ'), ('元', 'もと'), ('正', 'まさ'), ('丸', 'まる'), ('坊', 'ぼう'), ('金', 'きん'), ('竹', 'たけ'),
    ('松', 'まつ'), ('梅', 'うめ'), ('若', 'わか'), ('虎', 'とら'), ('熊', 'くま'), ('鶴', 'つる'), ('亀', 'かめ'),
    ('力', 'りき'), ('長', 'ちょう'), ('重', 'じゅう'), ('寿', 'じゅ'), ('福', 'ふく'), ('徳', 'とく'), ('作', 'さく'),
    ('太', 'た'), ('次', 'じ'), ('一', 'いち'), ('二', 'に'), ('三', 'さん'), ('四', 'し'), ('五', 'ご'),
    ('六', 'ろく'), ('七', 'しち'), ('八', 'はち'), ('九', 'く'), ('十', 'じゅう'), ('百', 'ひゃく'),
    ('右', 'う'), ('左', 'さ'), ('大', 'だい'), ('中', 'ちゅう'), ('門', 'もん'), ('衛', 'え'), ('兵', 'へい'),
    ('之', 'の'), ('助', 'すけ'), ('男', 'お'), ('馬', 'ま'), ('雲', 'うん'), ('斎', 'さい'), ('入', 'にゅう'),
    ('道', 'どう'), ('宗', 'そう'), ('玄', 'げん'), ('全', 'ぜん'), ('休', 'きゅう'), ('斎', 'さい'), ('庵', 'あん'),
    ('軒', 'けん'), ('斉', 'さい'), ('院', 'いん'), ('阿弥', 'あみ'), ('和', 'わ'), ('尚', 'しょう'),
]

# Common names no source resolves (hand readings).
TSUSHO_FIXED = {
    '忠次郎': 'ちゅうじろう', '文七郎': 'ぶんしちろう', '梅江斎': 'ばいこうさい', '好雪斎': 'こうせっさい',
    '如哲': 'じょてつ', '融成': 'ゆうせい', '崇孚': 'すうふ', '弁之介': 'べんのすけ', '晴幸': 'はるゆき',
    '於義丸': 'おぎまる', '竹千代': 'たけちよ', '辰千代': 'たつちよ', '宗淳': 'そうじゅん', '仁左衛門': 'にざえもん',
    '半羽介': 'はんばのすけ', '伊介': 'いすけ', '辰之助': 'たつのすけ', '成利': 'なりとし', '将右衛門': 'しょうえもん',
    '団右衛門': 'だんえもん', '茂助': 'もすけ', '伊右衛門': 'いえもん', '良通': 'よしみち', '直元': 'なおもと',
    '半夢斎': 'はんむさい', '幸昌': 'ゆきまさ', '仙三郎': 'せんざぶろう', '越ノ十郎': 'こしのじゅうろう', '貞興': 'さだおき',
    '了': 'りょう', '善祥坊': 'ぜんしょうぼう', '佐吉': 'さきち', '紀之介': 'きのすけ', '忠三郎': 'ちゅうざぶろう',
    '悪右衛門': 'あくえもん', '光佐': 'こうさ', '光寿': 'こうじゅ', '兼誉': 'けんよ', '神太郎': 'じんたろう',
    '神五郎': 'じんごろう', '瀬兵衛': 'せべえ', '栄舜坊': 'えいしゅんぼう', '陽舜坊': 'ようしゅんぼう', '才助': 'さいすけ',
    '重意': 'しげおき', '幸盛': 'ゆきもり', '孝高': 'よしたか', '基次': 'もとつぐ', '友信': 'とものぶ', '貫阿弥': 'かんあみ',
    '少輔次郎': 'しょうじろう', '少輔太郎': 'しょうたろう', '少輔四郎': 'しょうしろう', '弥坂兵衛': 'やさかべえ',
    '宮松': 'みやまつ', '加兵衛': 'かへえ', '寂心': 'じゃくしん', '宗運': 'そううん', '軍七': 'ぐんしち', '菊三郎': 'きくさぶろう',
    '所右衛門': 'しょえもん', '時貞': 'ときさだ', '保和': 'やすかず', '土用之助': 'どようのすけ', '宗享': 'そうきょう',
    '宇右衛門': 'うえもん', '道犬斎': 'どうけんさい', '仙蔵': 'せんぞう', '小才次': 'こさいじ', '国松': 'くにまつ',
    '幸松': 'こうまつ', '千代松': 'ちよまつ', '少大夫': 'しょうだゆう', '利益': 'とします', '猿千代': 'さるちよ',
    '政之助': 'まさのすけ', '自庵': 'じあん', '宗易': 'そうえき', '蓮宗': 'れんそう', '長門守': 'ながとのかみ',
    '資長': 'すけなが', '良雄': 'よしお', '義央': 'よしなか',
    # corrections of the component readings
    '重治': 'しげはる', '吉長': 'よしなが', '惣三': 'そうざ', '半三': 'はんざ',
}

def tsusho_reading(name, given_readings, dictionary):
    """Reading of a 通称: TSUSHO_FIXED, a given name of some record, the ELF dictionary,
    else assembled from TSUSHO_PARTS ('' when a kanji is unknown)."""
    if not name:
        return ''
    r = TSUSHO_FIXED.get(name) or given_readings.get(name) or dictionary.get(name)
    if r:
        return r
    out, i = '', 0
    while i < len(name):
        for k, v in TSUSHO_PARTS:
            if name.startswith(k, i):
                out += v
                i += len(k)
                break
        else:
            return ''
    return out

def tsusho_readings(jp, elf=None):
    """Guessed readings {通称: kana} for every person/princess record."""
    given = {}
    for t in PERSON_TABLES:
        for forms in jp[t]:
            for names_, readings in forms.values():
                for k in (1, 0):
                    if names_[k] and readings[k]:
                        given.setdefault(names_[k], readings[k])
    d = elf_dictionary(elf)
    out = {}
    for t in PERSON_TABLES:
        for forms in jp[t]:
            for names_, _ in forms.values():
                if names_[2] and names_[2] not in out:
                    out[names_[2]] = tsusho_reading(names_[2], given, d)
    return out

# ---------------------------------------------------------------- romanisation

KANA = {
    'きゃ': 'kya', 'きゅ': 'kyu', 'きょ': 'kyo', 'しゃ': 'sha', 'しゅ': 'shu', 'しょ': 'sho',
    'ちゃ': 'cha', 'ちゅ': 'chu', 'ちょ': 'cho', 'にゃ': 'nya', 'にゅ': 'nyu', 'にょ': 'nyo',
    'ひゃ': 'hya', 'ひゅ': 'hyu', 'ひょ': 'hyo', 'みゃ': 'mya', 'みゅ': 'myu', 'みょ': 'myo',
    'りゃ': 'rya', 'りゅ': 'ryu', 'りょ': 'ryo', 'ぎゃ': 'gya', 'ぎゅ': 'gyu', 'ぎょ': 'gyo',
    'じゃ': 'ja', 'じゅ': 'ju', 'じょ': 'jo', 'ぢゃ': 'ja', 'ぢゅ': 'ju', 'ぢょ': 'jo',
    'びゃ': 'bya', 'びゅ': 'byu', 'びょ': 'byo', 'ぴゃ': 'pya', 'ぴゅ': 'pyu', 'ぴょ': 'pyo',
    'てぃ': 'ti', 'でぃ': 'di', 'ふぁ': 'fa', 'ふぃ': 'fi', 'ふぇ': 'fe', 'ふぉ': 'fo',
    'うぃ': 'wi', 'うぇ': 'we', 'うぉ': 'wo', 'しぇ': 'she', 'じぇ': 'je', 'ちぇ': 'che',
    'あ': 'a', 'い': 'i', 'う': 'u', 'え': 'e', 'お': 'o',
    'か': 'ka', 'き': 'ki', 'く': 'ku', 'け': 'ke', 'こ': 'ko',
    'さ': 'sa', 'し': 'shi', 'す': 'su', 'せ': 'se', 'そ': 'so',
    'た': 'ta', 'ち': 'chi', 'つ': 'tsu', 'て': 'te', 'と': 'to',
    'な': 'na', 'に': 'ni', 'ぬ': 'nu', 'ね': 'ne', 'の': 'no',
    'は': 'ha', 'ひ': 'hi', 'ふ': 'fu', 'へ': 'he', 'ほ': 'ho',
    'ま': 'ma', 'み': 'mi', 'む': 'mu', 'め': 'me', 'も': 'mo',
    'や': 'ya', 'ゆ': 'yu', 'よ': 'yo', 'ら': 'ra', 'り': 'ri', 'る': 'ru', 'れ': 're', 'ろ': 'ro',
    'わ': 'wa', 'ゐ': 'i', 'ゑ': 'e', 'を': 'o', 'ん': 'n', 'ゔ': 'vu',
    'ゔぁ': 'va', 'ゔぃ': 'vi', 'ゔぇ': 've', 'ゔぉ': 'vo',
    'が': 'ga', 'ぎ': 'gi', 'ぐ': 'gu', 'げ': 'ge', 'ご': 'go',
    'ざ': 'za', 'じ': 'ji', 'ず': 'zu', 'ぜ': 'ze', 'ぞ': 'zo',
    'だ': 'da', 'ぢ': 'ji', 'づ': 'zu', 'で': 'de', 'ど': 'do',
    'ば': 'ba', 'び': 'bi', 'ぶ': 'bu', 'べ': 'be', 'ぼ': 'bo',
    'ぱ': 'pa', 'ぴ': 'pi', 'ぷ': 'pu', 'ぺ': 'pe', 'ぽ': 'po',
    'ぁ': 'a', 'ぃ': 'i', 'ぅ': 'u', 'ぇ': 'e', 'ぉ': 'o',
}

def hiragana(s):
    """Half-width / full-width katakana -> hiragana."""
    s = unicodedata.normalize('NFKC', s.replace('\u309b', '\u3099'))     # ウ゛ -> ヴ
    return ''.join(chr(ord(c) - 0x60) if 'ァ' <= c <= 'ヶ' else c for c in s)

def syllables(kana):
    out, i = [], 0
    while i < len(kana):
        if kana[i] == 'っ' and i + 1 < len(kana):
            nxt = KANA.get(kana[i + 1:i + 3]) or KANA.get(kana[i + 1], '')
            out.append('t' if nxt.startswith('ch') else nxt[:1])
            i += 1
        elif kana[i:i + 2] in KANA:
            out.append(KANA[kana[i:i + 2]])
            i += 2
        elif kana[i] == 'ー':
            out.append('')
            i += 1
        else:
            out.append(KANA.get(kana[i], kana[i]))
            i += 1
    return out

def romaji(kana, given=False):
    """Hepburn romanisation of a reading with long vowels written short, as in the
    earlier releases (Hojo Ujiyasu, Yuki, Choin): ou/oo -> o and uu -> u when the second
    vowel is its own kana, except that a vowel after an already long vowel starts a new
    word (おおうち Ouchi), ou before e stays (いのうえ Inoue, しょうえ Shoue, but とうえもん
    Toemon) and a given name ending in u + うじ (氏) keeps it (かずうじ Kazuuji; not
    ぜんつうじ Zentsuji). A given name ending in べえ (兵衛) is written -bei (Gonbei)."""
    syl = syllables(hiragana(kana))
    out, long_ = '', False
    for j, s in enumerate(syl):
        nxt = syl[j + 1] if j + 1 < len(syl) else ''
        prev = out[-1:] if out else ''
        rest = ''.join(syl[j + 1:])
        if not long_ and s == 'u' and prev in ('o', 'u'):
            keep = (prev == 'o' and nxt[:1] == 'e' and not rest.startswith('emon')
                    or given and prev == 'u' and rest == 'ji' and 'y' not in out[-3:]
                    and out[-3:] not in ('shu', 'chu') and out[-2:] != 'ju')
            if not keep:
                long_ = True
                continue
        if not long_ and s == 'o' and prev == 'o':
            long_ = True
            continue
        long_ = False
        out += s
    if given and out.endswith('bee'):
        out = out[:-3] + 'bei'
    return out.capitalize()

# ---------------------------------------------------------------- tl

def tl_path(table):
    return os.path.join(TL_DIR, table + '.json')

def load_tl(table):
    p = tl_path(table)
    if not os.path.exists(p):
        return {}
    return {r['id']: r for r in json.load(open(p, encoding='utf-8'))}

def write_tl(table, rows):
    """One row per line, keys in a fixed order (stable diffs)."""
    order = ['id', 'en', 'short'] + SCEN_KEYS
    lines = []
    for r in sorted(rows, key=lambda r: r['id']):
        r = {k: r[k] for k in order if k in r} | {k: v for k, v in r.items() if k not in order}
        lines.append(json.dumps(r, ensure_ascii=False))
    os.makedirs(TL_DIR, exist_ok=True)
    with open(tl_path(table), 'w', encoding='utf-8') as f:
        f.write('[\n' + ',\n'.join(lines) + '\n]\n')

def english(row, scen_key):
    """English for one scenario (list for persons, str otherwise)."""
    return row.get(scen_key, row['en'])

def is_empty(names):
    return not any(names)

def check(jp=None, quiet=False):
    """Validate tl/names/: every non-empty record has English, overrides only where
    the Japanese differs from the common form, no Japanese in tl. Returns errors."""
    if jp is None:
        jp = japanese(scenarios())
    errs = []
    for t, rows in jp.items():
        tl = load_tl(t)
        if not tl and not os.path.exists(tl_path(t)):
            errs.append('%s: tl/names/%s.json missing' % (t, t))
            continue
        nparts = len(TABLES[t][3])
        for i, forms in enumerate(rows):
            names = {k: v[0] for k, v in forms.items()}
            if all(is_empty(v) for v in names.values()):
                continue
            r = tl.get(i)
            if r is None:
                errs.append('%s %d: no English' % (t, i))
                continue
            base = common(names)
            for k, v in r.items():
                if k in ('id', 'short'):
                    continue
                if k != 'en' and k not in SCEN_KEYS:
                    errs.append('%s %d: unknown key %r' % (t, i, k))
                    continue
                vals = v if isinstance(v, list) else [v]
                if len(vals) != nparts or not all(isinstance(x, str) for x in vals):
                    errs.append('%s %d %s: expected %d string(s)' % (t, i, k, nparts))
                    continue
                if any(re.search(r'[^\x20-\x7e]', x) for x in vals):
                    errs.append('%s %d %s: non-ASCII %r' % (t, i, k, v))
                if any(x != x.strip() or not x for x in vals if base != ('',) * nparts):
                    if not (t in PERSON_TABLES and any(not p for p in base)):
                        errs.append('%s %d %s: empty or padded %r' % (t, i, k, v))
            field_max = TABLES[t][3][0][1] - 1
            for k, v in r.items():
                if k == 'id' or not isinstance(v, (str, list)):
                    continue
                n = len(' '.join(v)) if isinstance(v, list) else len(v)
                if k != 'short' and n > overflow.VAR:
                    errs.append('%s %d %s: %d bytes > overflow.VAR %d ({02} variable buffer model)'
                                % (t, i, k, n, overflow.VAR))
            if t not in PERSON_TABLES:
                if 'short' in r and len(r['short']) > field_max:
                    errs.append('%s %d: short %r is %d bytes > field %d' % (t, i, r['short'], len(r['short']), field_max))
            for k in SCEN_KEYS:
                if names[k] != base and not is_empty(names[k]) and k not in r:
                    errs.append('%s %d: %s is named differently (%s) but has no %s'
                                % (t, i, k, ''.join(names[k]), k))
    if not quiet:
        for e in errs[:50]:
            print(e)
        if len(errs) > 50:
            print('... %d errors' % len(errs))
    return errs

def export(jp=None, elf=None):
    """script/names/<table>.json: Japanese per scenario + readings + English.
    Person readings[2] (通称) is a guess (tsusho_readings); '' when unknown."""
    if jp is None:
        jp = japanese(scenarios())
    tsusho = tsusho_readings(jp, elf)
    os.makedirs(SCRIPT_DIR, exist_ok=True)
    for t, rows in jp.items():
        tl = load_tl(t)
        out = []
        for i, forms in enumerate(rows):
            names = {k: v for k, v in forms.items()}
            base = common(names)
            reading = list(base[1])
            if t in PERSON_TABLES:
                reading[2] = tsusho.get(base[0][2], '')
            row = {'id': i, 'jp': list(base[0]), 'reading': reading,
                   'romaji': [romaji(x, k >= 1) for k, x in enumerate(reading)]}
            other = {k: list(v[0]) for k, v in names.items() if v != base}
            if other:
                row['jp_scen'] = other
                if t in PERSON_TABLES:
                    row['reading_scen'] = {k: list(v[1][:2]) + [tsusho.get(v[0][2], '')]
                                           for k, v in names.items() if v != base}
            if i in tl:
                row['tl'] = tl[i]
            out.append(row)
        with open(os.path.join(SCRIPT_DIR, t + '.json'), 'w', encoding='utf-8') as f:
            json.dump(out, f, ensure_ascii=False, indent=0)

def find(q):
    ql = q.lower()
    for t in TABLES:
        p = os.path.join(SCRIPT_DIR, t + '.json')
        tl = load_tl(t)
        for r in json.load(open(p, encoding='utf-8')):
            t_row = tl.get(r['id'], {})
            jp = [''.join(r['jp'])] + [''.join(v) for v in r.get('jp_scen', {}).values()]
            en = json.dumps({k: v for k, v in t_row.items() if k != 'id'}, ensure_ascii=False)
            if any(q in x for x in jp) or q in ''.join(r['reading']) or ql in en.lower():
                scen = ' ' + ' '.join('%s:%s' % (k, ''.join(v)) for k, v in r['jp_scen'].items()) if 'jp_scen' in r else ''
                print('%-11s %4d %s (%s)%s -> %s' % (t, r['id'], ''.join(r['jp']), ''.join(r['reading']), scen, en))

def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'check'
    if cmd == 'find':
        return find(sys.argv[2])
    jp = japanese(scenarios())
    if cmd == 'export':
        export(jp)
    errs = check(jp)
    if errs:
        sys.exit(1)
    print('names ok')

if __name__ == '__main__':
    main()

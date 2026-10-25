"""Name hook: the records keep their Japanese, the getters show English.

Port of Soutenroku's design (notes/survey/namehook_re.md has the RE). Ranseiki objects are
C++ with a virtual GetName (vtbl+0x24, 364 call sites) returning char*, and the message
variables go through per-field getters (out String*, record). At build time a table is
generated:

  FNV-1a(kind byte, field, 0xFF, field, ...) -> English string      (sorted by hash)

  kind  key                                   English
  n     full name String +8, full reading +0xC  "Family Given"
  F/G   family + reading / given + reading      one part (records, ELF pools: women, ninja)
  U     通称                                    common name (message `4`)
  c     castle name + reading (+8, +0x12)      bare castle name (two castles are 松前)
  r t T o p q  region, treasure, court title, office, province, group (+8 name)
  0x80+i castle name + reading, suffix type i   whole name where the suffix is part of the
                                                English ("Kosuiji" with 寺)

Hooks (everything else, saves and history renames included, sees the Japanese records):
  person/woman GetName 0x1DEEF0 -> n, else "F G" from the parts (women and ninja are
    generated from the pools; children take the family by chain), else the Japanese
  message fields `2` 0x1DDB40, `3` 0x1DDAF0, `4` 0x1DDAA0, `5` 0x1DD9A0 (new Strings)
  family char* 0x1DB780 at the display-only call sites FAMILY_SITES (namehook_re.md §7;
    the other callers build records, the PUK editor and the name entry: they stay Japanese)
  simple GetName bodies (a0+8) SIMPLE
  castle GetName 0x210AA0: its strcat of the suffix word -> the English castle name
  castle type word 0x20FF70, house GetName 0x1A17C0 (suffix by +0x73), shogunate 0x181E40
The castle/house suffixes are chosen by index, not by the suffix string: elfstr translates
some of those shared ELF strings (家 "Clan", 幕府 "Shogunate").

  namehook.py stats          table size, conflicts, longest names
  namehook.py find TEXT      keys whose English contains TEXT
"""
import collections, json, os, re, struct, sys

sys.path.insert(0, os.path.dirname(__file__))
import asm, elfseg, names, setup
from asm import hi, lo

POOLS = os.path.join(names.TL_DIR, 'pools.json')
P32, M32 = 0x01000193, 0xFFFFFFFF

# game functions
FAM, FAMR = 0x1DB780, 0x1DB5C0          # family-by-chain char*, its reading (person)
STRLEN, STRCPY, STRCAT = 0x137D70, 0x137C58, 0x137850
COPY = 0x1EF210                         # char* -> one of 20 rotating 0x4B0 buffers
STRING = 0x1130C0                       # String(out, char*, len)

PERSON_GETNAME = 0x1DEEF0               # lw v0,8(a0); lw v0,0xC(v0); jr ra
MSG_FIELDS = {0x1DDB40: 'm2', 0x1DDAF0: 'm3', 0x1DDAA0: 'm4', 0x1DD9A0: 'm5'}
MSG_FIRST = {0x1DDB40: 0x27BDFFF0, 0x1DDAF0: 0x27BDFFD0, 0x1DDAA0: 0x27BDFFD0, 0x1DD9A0: 0x27BDFFC0}
# jal 0x1DB780 whose result is only shown (shogunate, `j`, house names, %s家重臣 formats)
FAMILY_SITES = (0x181EA4, 0x18D440, 0x19D804, 0x1A1878, 0x455EF8, 0x458DC0, 0x458E28, 0x606EFC,
                0x393838)                  # tutorial event castle String (family + 城), rawcastle.md
SIMPLE = {0x1948A0: 'r', 0x1D77F0: 't', 0x1E1360: 'T', 0x212400: 'o', 0x1ECF50: 'p', 0x218990: 'q',
          0x210A90: 'c'}               # addiu v0,a0,8; jr ra; nop
CASTLE_READING = 0x12                   # castle record: +8 name, +0x12 reading
ADDIU_V0_A0_8 = 0x24820008
CASTLE_CAT = 0x210AE0                   # jal strcat(name copy, suffix table[+0x54]) in GetName 0x210AA0
CASTLE_TYPE = 0x20FFA0                  # jal strlen(suffix word) in the type-word getter 0x20FF70
HOUSE_CAT = 0x1A18AC                    # jal strcat(family copy, house suffix[+0x73]) in 0x1A17C0
SHOGUN_CAT = 0x181EC8                   # jal strcat(family copy, "幕府") in 0x181E40

# castle suffix table 0x18BF470 (13: 城 館 御坊 御所 寺 砦 町 城 城 寺 要塞 宮殿 迷宮)
SUFFIX_EN = [' Castle', ' Hall', ' Gobo', ' Palace', 'ji', ' Fort', ' Town', ' Castle', ' Castle', 'dera',
             ' Fortress', ' Palace', ' Labyrinth']
TYPE_EN = ['Castle', 'Hall', 'Gobo', 'Palace', 'Temple', 'Fort', 'Town', 'Castle', 'Castle', 'Temple',
           'Fortress', 'Palace', 'Labyrinth']
TEMPLE_SUFFIX = (4, 9)
HOUSE_EN = [' clan', ' clan', ' band', ' sect', '']      # house suffix table 0x17D5B10: 家 族 衆 教 ""
SHOGUN_EN = ' Shogunate'

POOL_TABLES = {'women': 'G', 'ninja_epithets': 'F', 'kunoichi_epithets': 'F', 'ninja_given': 'G',
               'kunoichi_given': 'G'}
POOL_READINGS = {'women': [(0x63FBE0, 100)], 'ninja_epithets': [(0x64A4D0, 60)],
                 'ninja_given': [(0x64AA60, 110)], 'kunoichi_epithets': [(0x64AEF0, 31)],
                 'kunoichi_given': [(0x64B140, 43)]}
RENAMES = (0x6411E0, 0x641450)          # history rename (name, reading) pairs, written into records
GROUP_TABLES = ('towns', 'temples', 'kunishu', 'ninja', 'navies')
SIMPLE_TABLES = {'regions': 'r', 'treasures': 't', 'courttitles': 'T', 'offices': 'o', 'provinces': 'p',
                 'castles': 'c'} | {t: 'q' for t in GROUP_TABLES}
EXTRA_Q = {'朝廷': 'Imperial Court'}
# MEDIA/SCENA2/TUTODATA.NB9 (the tutorial's own records, same layouts as the scenario files): persons
# (family/given + reading), castle 西 (+ 城 by suffix), province, gentry group, house family.
TUTORIAL = {'F': {('矢島', 'やじま'): 'Yajima', ('牧野', 'まきの'): 'Makino', ('新田', 'にった'): 'Nitta',
                  ('山野', 'やまの'): 'Yamano', ('ユーザー', 'ゆーざー'): 'Player', ('大名姓', 'だいみょうせい'): 'Daimyo'},
            'G': {('隼人', 'はやと'): 'Hayato', ('主馬', 'しゅめ'): 'Shume', ('兵庫', 'ひょうご'): 'Hyogo',
                  ('源哉', 'げんや'): 'Genya', ('佐平', 'さへい'): 'Sahei', ('弥助', 'やすけ'): 'Yasuke',
                  ('大名', 'だいみょう'): 'Daimyo'},
            'c': {('西', 'にし'): 'West'}, 'p': {('プレイ国',): 'Tutorial'}, 'q': {('蝦夷国衆',): 'Ezo Gentry'}}

def fnv(kind, parts):
    h = ((0x811C9DC5 ^ kind) * P32) & M32
    for i, p in enumerate(parts):
        if i:
            h = ((h ^ 0xFF) * P32) & M32
        for b in p:
            h = ((h ^ b) * P32) & M32
    return h

def sj(s):
    return s.encode('cp932')

def kind_byte(kind):
    return kind if isinstance(kind, int) else ord(kind)

# ---------------------------------------------------------------- table

class Table:
    """kind + Japanese parts -> English; votes for records, first writer wins for fallbacks."""
    def __init__(self):
        self.votes = collections.defaultdict(collections.Counter)

    def vote(self, kind, parts, en, n=1):
        if en:
            self.votes[(kind_byte(kind), tuple(sj(p) for p in parts))][en] += n

    def fallback(self, kind, parts, en):
        k = (kind_byte(kind), tuple(sj(p) for p in parts))
        if en and k not in self.votes:
            self.votes[k][en] += 1

    def resolved(self):
        out, conflicts = {}, []
        for k, c in self.votes.items():
            out[k] = c.most_common(1)[0][0]
            if len(c) > 1:
                conflicts.append((k, c))
        return out, conflicts

def elf_str(elf, va):
    o = va - elfseg.VBASE + elfseg.FOFF
    return elf[o:elf.index(b'\0', o)].decode('cp932')

def elf_word(elf, va):
    return struct.unpack_from('<I', elf, va - elfseg.VBASE + elfseg.FOFF)[0]

def build_table(elf, scens):
    t = Table()
    jp = names.japanese(scens)
    for table in names.PERSON_TABLES:
        tl = names.load_tl(table)
        for i, forms in enumerate(jp[table]):
            row = tl.get(i)
            if row is None:
                continue
            for sk, ((fam, giv, tsu), (famr, givr, _)) in forms.items():
                if not (fam or giv):
                    continue
                en = names.english(row, sk)
                t.vote('n', (fam + giv, famr + givr), ' '.join(p for p in en[:2] if p))
                if fam:
                    t.vote('F', (fam, famr), en[0])
                if giv:
                    t.vote('G', (giv, givr), en[1])
                if tsu:
                    t.vote('U', (tsu,), en[2])
    # the tutorial renames a castle after the player's family: name = family kanji, reading = the
    # TUTODATA placeholder だいみょうせい (seen in RAM, session 17) -> castle key (family, placeholder)
    for (kind, parts), cnt in list(t.votes.items()):
        if kind == ord('F'):
            for en, n in cnt.items():
                t.vote('c', (parts[0].decode('cp932'), 'だいみょうせい'), en, n)
    for table, kind in SIMPLE_TABLES.items():
        tl = names.load_tl(table)
        for i, forms in enumerate(jp[table]):
            row = tl.get(i)
            for sk, (nm, _) in forms.items():
                if nm[0] and row is not None:
                    en = names.english(row, sk)
                    key = (nm[0], forms[sk][1][0]) if kind == 'c' else (nm[0],)
                    t.vote(kind, key, en)
                    if kind == 'c' and re.search('(ji|dera)$', en):
                        for k in TEMPLE_SUFFIX:
                            t.vote(0x80 + k, key, en)
    # SCEVAR01-15 carry extra person records (scenario women: 龍造寺千世, 有馬智子, 伊達杏...; ids past the
    # SCEFIX person table, seen raw in the Princesses list, session 17): same 83-byte layout, scattered in
    # FF padding -> scan for `02` + id + family/given + hiragana readings; English by romaji (family by vote)
    resolved, _ = t.resolved()
    for rec in scevar_persons():
        fam, giv, tsu, famr, givr = rec
        fam_en = resolved.get((ord('F'), (sj(fam), sj(famr)))) or names.romaji(famr)
        giv_en = names.romaji(givr, True)
        t.fallback('F', (fam, famr), fam_en)
        t.fallback('G', (giv, givr), giv_en)
        t.fallback('n', (fam + giv, famr + givr), fam_en + ' ' + giv_en)
        if tsu:
            t.fallback('U', (tsu,), giv_en)
    for nm, en in EXTRA_Q.items():
        t.fallback('q', (nm,), en)
    for kind, d in TUTORIAL.items():
        for key, en in d.items():
            t.fallback(kind, key, en)
    pools = json.load(open(POOLS, encoding='utf-8'))
    for lst, kind in POOL_TABLES.items():
        for tab, n in POOL_READINGS[lst]:
            for k in range(n):
                nm = elf_str(elf, elf_word(elf, tab + 8 * k))
                rd = elf_str(elf, elf_word(elf, tab + 8 * k + 4))
                t.fallback(kind, (nm, rd), pools[lst][k])      # English in table order
    for va in range(RENAMES[0], RENAMES[1] - 4, 4):     # (name, reading) pairs, not all 8-aligned
        try:
            nm, rd = elf_str(elf, elf_word(elf, va)), elf_str(elf, elf_word(elf, va + 4))
        except (ValueError, UnicodeDecodeError, IndexError):
            continue
        if re.fullmatch('[ぁ-ん]+', rd) and not re.search('[ぁ-ん]', nm):
            t.fallback('F', (nm, rd), names.romaji(rd))
            t.fallback('G', (nm, rd), names.romaji(rd, True))
    return t

SCEVAR_FILE = 'MEDIA/SCENA2/SCEVAR%02d.NB9'
HIRA = re.compile('[ぁ-んー]+')

def scevar_persons(path=setup.CLEAN):
    """Sorted [(family, given, 通称, family reading, given reading)] of the person records embedded in
    the SCEVAR files (marker 02, id u16, then the SCEFIX person layout; validated by hiragana readings).
    Sorted: fallbacks are first-wins, and a set's order changes with the hash seed."""
    import iso
    disc = iso.Disc(path)
    out = set()
    for k in names.SCENARIOS:
        d = disc.file(SCEVAR_FILE % k)
        i = 0
        while True:
            i = d.find(b'\x02', i)
            if i < 0 or i + 83 > len(d):
                break
            r = d[i:i + 83]
            try:
                fam, tsu, giv = names.field(r[3:13]), names.field(r[0xD:0x1B]), names.field(r[0x1B:0x25])
                famr, givr = names.field(r[0x25:0x37]), names.field(r[0x37:0x49])
            except Exception:
                fam = ''
            if fam and giv and famr and givr and HIRA.fullmatch(famr) and HIRA.fullmatch(givr) \
                    and '�' not in fam + giv + tsu and not re.search('[ぁ-ん]', fam + giv):
                out.add((fam, giv, tsu, famr, givr))
                i += 83
            else:
                i += 1
    return sorted(out)

def entries(table):
    """sorted [(hash, kind, parts, English)]; fails on a hash collision of two keys."""
    res, conflicts = table.resolved()
    by_hash = {}
    for (kind, parts), en in res.items():
        h = fnv(kind, parts)
        if h in by_hash and by_hash[h][:2] != (kind, parts):
            raise ValueError('namehook: hash collision %r / %r' % (by_hash[h][:2], (kind, parts)))
        by_hash[h] = (kind, parts, en)
    return sorted((h,) + v for h, v in by_hash.items()), conflicts

# ---------------------------------------------------------------- code

def tr_code(tab, tab_end):
    """tr: t0 kind, t1..t4 strings (0 ends) -> v0 English or 0. Leaf; keeps a0-a3, s*.
    tr1 / tr2: one string in t1, two in t1/t2."""
    return [
        'tr1:',
        ('or', 't2', 'zero', 'zero'),
        'tr2:',
        ('or', 't3', 'zero', 'zero'), ('or', 't4', 'zero', 'zero'),
        ('lui', 'v0', 0x811C), ('ori', 'v0', 'v0', 0x9DC5),
        ('lui', 't9', P32 >> 16), ('ori', 't9', 't9', P32 & 0xFFFF),
        ('xor', 'v0', 'v0', 't0'), ('mult', 'v0', 'v0', 't9'),
        'tr_str:',
        ('lbu', 't6', 0, 't1'),
        'tr_chr:',
        ('beqz', 't6', 'tr_end'), ('addiu', 't1', 't1', 1),
        ('xor', 'v0', 'v0', 't6'), ('mult', 'v0', 'v0', 't9'),
        ('b', 'tr_chr'), ('lbu', 't6', 0, 't1'),
        'tr_end:',
        ('or', 't1', 't2', 'zero'), ('or', 't2', 't3', 'zero'), ('or', 't3', 't4', 'zero'),
        ('beqz', 't1', 'tr_find'), ('or', 't4', 'zero', 'zero'),
        ('ori', 't6', 'zero', 0xFF), ('xor', 'v0', 'v0', 't6'),
        ('b', 'tr_str'), ('mult', 'v0', 'v0', 't9'),
        'tr_find:',
        ('lui', 't5', hi(tab)), ('addiu', 't5', 't5', lo(tab)),
        ('lui', 't6', hi(tab_end)), ('addiu', 't6', 't6', lo(tab_end)),
        'tr_bs:',
        ('sltu', 'at', 't5', 't6'), ('beqz', 'at', 'tr_miss'), ('subu', 't7', 't6', 't5'),
        ('srl', 't7', 't7', 4), ('sll', 't7', 't7', 3), ('addu', 't7', 't5', 't7'),
        ('lw', 't8', 0, 't7'), ('beq', 't8', 'v0', 'tr_hit'), ('sltu', 'at', 't8', 'v0'),
        ('bnez', 'at', 'tr_right'), ('nop',),
        ('b', 'tr_bs'), ('or', 't6', 't7', 'zero'),
        'tr_right:',
        ('b', 'tr_bs'), ('addiu', 't5', 't7', 8),
        'tr_hit:',
        ('jr', 'ra'), ('lw', 'v0', 4, 't7'),
        'tr_miss:',
        ('jr', 'ra'), ('or', 'v0', 'zero', 'zero'),
    ]

PUSH = [('addiu', 'sp', 'sp', -32), ('sd', 'ra', 0, 'sp')]
POP = [('ld', 'ra', 0, 'sp'), ('addiu', 'sp', 'sp', 32)]
RET = [('jr', 'ra'), ('nop',)]

def table_word(reg, table, idx_reg):
    """reg = table[idx_reg] (idx_reg is scaled to words, clobbered)."""
    return [('sll', idx_reg, idx_reg, 2), ('lui', reg, hi(table)), ('addu', reg, reg, idx_reg),
            ('lw', reg, lo(table), reg)]

def stubs(space, suf, types, house, shogun):
    """Everything but tr. space/suf/types/house/shogun: addresses of " " and the English tables."""
    c = [
        # fam(a0 person) -> English family or the Japanese char*
        'fam:'] + PUSH + [
        ('sw', 'a0', 8, 'sp'), ('jal', FAM), ('nop',), ('sw', 'v0', 16, 'sp'),
        ('jal', FAMR), ('lw', 'a0', 8, 'sp'),
        ('lw', 't1', 16, 'sp'), ('or', 't2', 'v0', 'zero'), ('jal', 'tr2'), ('ori', 't0', 'zero', ord('F')),
        ('lw', 't1', 16, 'sp'), ('movz', 'v0', 't1', 'v0')] + POP + RET + [
        # given(a0 person)
        'given:'] + PUSH + [
        ('addiu', 't1', 'a0', 0x28), ('addiu', 't2', 'a0', 0x44), ('jal', 'tr2'), ('ori', 't0', 'zero', ord('G')),
        ('addiu', 't1', 'a0', 0x28), ('movz', 'v0', 't1', 'v0')] + POP + RET + [
        # pname(a0 person) = GetName: n, else "F G" if a part is English, else the Japanese
        'pname:'] + PUSH + [
        ('sw', 'a0', 8, 'sp'),
        ('lw', 't1', 8, 'a0'), ('lw', 't1', 0xC, 't1'), ('lw', 't2', 0xC, 'a0'), ('lw', 't2', 0xC, 't2'),
        ('jal', 'tr2'), ('ori', 't0', 'zero', ord('n')),
        ('bnez', 'v0', 'pn_out'), ('nop',),
        ('jal', 'fam'), ('lw', 'a0', 8, 'sp'), ('sw', 'v0', 16, 'sp'),
        ('jal', 'given'), ('lw', 'a0', 8, 'sp'), ('sw', 'v0', 24, 'sp'),
        ('lw', 't1', 16, 'sp'), ('lbu', 't1', 0, 't1'), ('lw', 't2', 24, 'sp'), ('lbu', 't2', 0, 't2'),
        ('beqz', 't1', 'pn_out'), ('lw', 'v0', 24, 'sp'),          # no family: the given name
        ('beqz', 't2', 'pn_out'), ('lw', 'v0', 16, 'sp'),          # no given: the family
        ('sltiu', 't3', 't1', 0x80), ('sltiu', 't4', 't2', 0x80), ('or', 't3', 't3', 't4'),
        ('beqz', 't3', 'pn_jp'), ('nop',),
        ('jal', COPY), ('lw', 'a0', 16, 'sp'), ('sw', 'v0', 16, 'sp'),
        ('or', 'a0', 'v0', 'zero'), ('lui', 'a1', hi(space)), ('jal', STRCAT), ('addiu', 'a1', 'a1', lo(space)),
        ('lw', 'a0', 16, 'sp'), ('jal', STRCAT), ('lw', 'a1', 24, 'sp'),
        ('b', 'pn_out'), ('lw', 'v0', 16, 'sp'),
        'pn_jp:',
        ('lw', 'a0', 8, 'sp'), ('lw', 'v0', 8, 'a0'), ('lw', 'v0', 0xC, 'v0'),
        'pn_out:'] + POP + RET + [
        # mkstr(a0 out String*, a1 char*): new String
        'mkstr:'] + PUSH + [
        ('sw', 'a0', 8, 'sp'), ('sw', 'a1', 16, 'sp'), ('jal', STRLEN), ('or', 'a0', 'a1', 'zero'),
        ('lw', 'a0', 8, 'sp'), ('lw', 'a1', 16, 'sp'), ('jal', STRING), ('or', 'a2', 'v0', 'zero')] + POP + RET
    # message field getters (a0 out String*, a1 person)
    for label, fn in (('m2', 'pname'), ('m3', 'given'), ('m5', 'fam')):
        c += [label + ':'] + PUSH + [
            ('sw', 'a0', 8, 'sp'), ('jal', fn), ('or', 'a0', 'a1', 'zero'),
            ('or', 'a1', 'v0', 'zero'), ('lw', 'a0', 8, 'sp')] + POP + [('j', 'mkstr'), ('nop',)]
    c += ['m4:'] + PUSH + [
        ('addiu', 't1', 'a1', 0x1A), ('jal', 'tr1'), ('ori', 't0', 'zero', ord('U')),
        ('addiu', 't1', 'a1', 0x1A), ('movz', 'v0', 't1', 'v0'), ('or', 'a1', 'v0', 'zero')] + POP + [
        ('j', 'mkstr'), ('nop',)]
    # simple GetName bodies: English for the +8 name, else a0+8
    for va, kind in SIMPLE.items():
        c += ['s%x:' % va] + PUSH + [
            ('addiu', 't1', 'a0', 8), ('addiu', 't2', 'a0', CASTLE_READING),
            ('jal', 'tr2' if kind == 'c' else 'tr1'), ('ori', 't0', 'zero', ord(kind)),
            ('addiu', 't1', 'a0', 8), ('movz', 'v0', 't1', 'v0')] + POP + RET
    # castle GetName's strcat(a0 name copy, a1 suffix), s1 = castle
    c += ['ccat:'] + PUSH + [
        ('sw', 'a0', 8, 'sp'), ('sw', 'a1', 16, 'sp'),
        ('lbu', 't0', 0x54, 's1'), ('sltiu', 'at', 't0', len(SUFFIX_EN)), ('beqz', 'at', 'cc_orig'),
        ('sw', 't0', 24, 'sp'),
        ('addiu', 't2', 's1', CASTLE_READING), ('addiu', 't0', 't0', 0x80),
        ('jal', 'tr2'), ('or', 't1', 'a0', 'zero'),
        ('addiu', 't2', 's1', CASTLE_READING), ('bnez', 'v0', 'cc_cpy'), ('ori', 't0', 'zero', ord('c')),
        ('jal', 'tr2'), ('lw', 't1', 8, 'sp'),
        ('beqz', 'v0', 'cc_orig'), ('nop',),
        ('lw', 'a0', 8, 'sp'), ('jal', STRCPY), ('or', 'a1', 'v0', 'zero'),
        ('lw', 't0', 24, 'sp')] + table_word('a1', suf, 't0') + [
        ('b', 'cc_cat'), ('lw', 'a0', 8, 'sp'),
        'cc_cpy:',
        ('lw', 'a0', 8, 'sp'), ('jal', STRCPY), ('or', 'a1', 'v0', 'zero'),
        ('b', 'cc_out'), ('nop',),
        'cc_orig:',
        ('lw', 'a0', 8, 'sp'), ('lw', 'a1', 16, 'sp'),
        'cc_cat:',
        ('jal', STRCAT), ('nop',),
        'cc_out:'] + POP + RET
    # castle type word: jal strlen(a0 = s0 = suffix word), a1 = castle -> s0 = English word
    c += ['ctype:',
          ('lbu', 't1', 0x54, 'a1'), ('sltiu', 'at', 't1', len(TYPE_EN)), ('beqz', 'at', 'ct_go'), ('nop',)] + \
        table_word('s0', types, 't1') + [('or', 'a0', 's0', 'zero'),
        'ct_go:', ('j', STRLEN), ('nop',)]
    # house GetName's strcat(a0 family copy, a1 suffix), s2 = house: English suffix after an English family
    c += ['hcat:',
          ('lbu', 't0', 0, 'a0'), ('beqz', 't0', 'hc_go'), ('sltiu', 't0', 't0', 0x80),
          ('beqz', 't0', 'hc_go'), ('lbu', 't1', 0x73, 's2'),
          ('sltiu', 'at', 't1', len(HOUSE_EN)), ('beqz', 'at', 'hc_go'), ('nop',)] + \
        table_word('a1', house, 't1') + [
        'hc_go:', ('j', STRCAT), ('nop',)]
    c += ['scat:',
          ('lbu', 't0', 0, 'a0'), ('beqz', 't0', 'sc_go'), ('sltiu', 't0', 't0', 0x80),
          ('beqz', 't0', 'sc_go'), ('lui', 't1', hi(shogun)),
          ('addiu', 'a1', 't1', lo(shogun)),
          'sc_go:', ('j', STRCAT), ('nop',)]
    return c

def jal_target(w, site):
    assert w >> 26 == 3, 'namehook: %#x is not a jal' % site
    return (w & 0x3FFFFFF) << 2 | site & 0xF0000000

def jal(target):
    return struct.pack('<I', 0x0C000000 | (target >> 2) & 0x3FFFFFF)

# ---------------------------------------------------------------- apply

def apply(elf, clean, seg, scens):
    """Table + stubs into the segment, hooks into the ELF. clean = the unpatched ELF.
    Returns (entries, conflicts)."""
    ents, conflicts = entries(build_table(clean, scens))
    strings, pos = {}, bytearray()
    def string(s):
        if s not in strings:
            strings[s] = len(pos)
            pos.extend(s.encode('ascii') + b'\0')
        return strings[s]
    for e in ents:
        string(e[3])
    extra = [' '] + SUFFIX_EN + TYPE_EN + HOUSE_EN + [SHOGUN_EN]
    for s in extra:
        string(s)
    base = seg.add(bytes(pos), 4)
    addr = lambda s: base + strings[s]
    ptrs = lambda lst: seg.add(b''.join(struct.pack('<I', addr(s)) for s in lst), 4)
    suf, types, house = ptrs(SUFFIX_EN), ptrs(TYPE_EN), ptrs(HOUSE_EN)
    tab = seg.add(b''.join(struct.pack('<II', h, addr(en)) for h, _, _, en in ents), 8)
    tab_end = tab + 8 * len(ents)
    code = tr_code(tab, tab_end) + stubs(addr(' '), suf, types, house, addr(SHOGUN_EN))
    at = seg.code(code)
    lab = asm.labels(at, code)
    word = lambda va: elf_word(elf, va)
    j2 = lambda site, label: elfseg.patch(elf, site, asm.assemble(site, [('j', lab[label]), ('nop',)]))
    # person GetName, message fields, simple GetNames: replace the body
    assert word(PERSON_GETNAME) == 0x8C820008 and word(PERSON_GETNAME + 4) == 0x8C42000C, 'person GetName changed'
    j2(PERSON_GETNAME, 'pname')
    for va, label in MSG_FIELDS.items():
        assert word(va) == MSG_FIRST[va], 'message field getter %x changed' % va
        j2(va, label)
    for va in SIMPLE:
        assert word(va) == ADDIU_V0_A0_8 and word(va + 4) == 0x03E00008, 'GetName %x changed' % va
        j2(va, 's%x' % va)
    # call sites
    sites = [(s, FAM, 'fam') for s in FAMILY_SITES] + [
        (CASTLE_CAT, STRCAT, 'ccat'), (CASTLE_TYPE, STRLEN, 'ctype'), (HOUSE_CAT, STRCAT, 'hcat'),
        (SHOGUN_CAT, STRCAT, 'scat')]
    for site, orig, label in sites:
        assert jal_target(word(site), site) == orig, 'hook site %x changed' % site
        elfseg.patch(elf, site, jal(lab[label]))
    return ents, conflicts

# ---------------------------------------------------------------- cli

def kind_name(k):
    return chr(k) if k < 0x80 else 'K%d' % (k - 0x80)

def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'stats'
    scens = names.scenarios()
    ents, conflicts = entries(build_table(setup.clean_elf(), scens))
    if cmd == 'stats':
        c = collections.Counter(kind_name(e[1]) for e in ents)
        size = 8 * len(ents) + sum(len(s) + 1 for s in {e[3] for e in ents})
        print('%d entries (~%d bytes): %s' % (len(ents), size, ' '.join('%s %d' % kv for kv in sorted(c.items()))))
        print('%d keys with several English (most common used):' % len(conflicts))
        for (kind, parts), cnt in conflicts[:40]:
            print('  %s %s %s' % (kind_name(kind), '|'.join(p.decode('cp932') for p in parts), dict(cnt)))
        longest = sorted((e for e in ents if e[1] == ord('n')), key=lambda e: -len(e[3]))[:5]
        print('longest full names:', [e[3] for e in longest])
    elif cmd == 'find':
        q = sys.argv[2].lower()
        for h, kind, parts, en in ents:
            if q in en.lower():
                print('%08x %s %s -> %s' % (h, kind_name(kind), '|'.join(p.decode('cp932') for p in parts), en))

if __name__ == '__main__':
    main()

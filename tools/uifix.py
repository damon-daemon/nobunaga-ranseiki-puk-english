"""Engine fixes found in the emulator pass (phase 6e).

Render node pool (notes/emu_pass.md "Goal-window crash"): the per-frame render context keeps 4
list heads (+0) and a pool of 0x73A 8-byte nodes (+0x10: object, next) with the count at +0x39E0
(cleared by 0x16F030). The four add-to-list functions (0x16E8F8, 0x16EA50, 0x16EC04, 0x16EDFC:
`lw v1,0x39E0(s3); sll v1,v1,3; addu v1,v1,s3` ... `sw v1,0x39E0(s3)`) never check the count.
A frame with more than 0x73A drawn objects writes nodes past the pool into the data after it;
the list walk 0x16F110 then follows GS packet words as pointers and jumps to 0 (EE log "TLB Miss,
pc=0x0"), and the game stalls. English text squeezed to fit (vwf.SQUEEZE) draws glyphs the
clipped text never drew, which pushed the council goal window over the limit. NODES: a full pool
now skips the add (the object is not drawn this frame) and counts it in the segment word
`node_drops` instead of corrupting memory. (NODES alone did not stop the crash: node_drops stayed 0.)

Slot arrays (the real goal-window crash, session 15): the texture manager T = 0xAECE40 keeps
two per-frame arrays of 0xC0-byte slots (two 0x60-byte records, one per packet buffer gp-0x612C),
both filled by 0x16AAF0, which the list walk 0x16F110 calls once per node; counters reset by
0x16A900, never checked:
- upload (T+0x2AFA0, 150 slots, idx T+0x2AF90: GIF IMAGE uploads BITBLTBUF/TRXPOS/TRXREG/TRXDIR,
  up to two per node): slots 150+ are the texture slots 0.. → early-drawn objects lose their
  texture setup in busy frames (218 in the goal window);
- texture (T+0x471A0, 1,020 slots, idx T+0x2AF94: VIF DIRECT TEXFLUSH/TEXA/ALPHA_1/TEX0_1): slots
  1,020+ overwrite T's free-lists (T+0x76EA0, pointers later free()d) and the render context
  0xB64FD0 (every 24th node = a TEX0 word → the walk jumps to 0). The squeezed goal window drew
  1,099-1,102.
SLOTS moves the slots past the in-place count to the segment (template records as the init
writes them), clamps an index past all slots to the last one, and keeps a high-water word each.

Sprite records (the goal-window header bug, session 16; RE notes/survey/header_bug.md): the render
context C = 0xB64FD0 holds 1,000 per-frame sprite records (0x140 B at C+0x39F0, in-use flags at
C+0x51BF0, cleared once per frame by 0x16EEA0). Every textured sprite (glyph, plate, button face)
takes one (0x16E7F0 scans the flags and returns 0 when none is free) and 0x16ED40 fills it and
links a render node to it. Past 1,000 sprites in a frame every sprite rewrites record 0, so all
those nodes draw whatever was written last ("only the last glyph of the label survives"). English
text needs about twice the sprites of kanji text. SPRITES: EXT records in the segment (ids 1000+),
allocator / record address / flag write / flag clear patched to use them, high-water and overflow
words (read them over PINE: `sprite_high`, `sprite_over` printed by build.py).
"""
import os, struct, sys

sys.path.insert(0, os.path.dirname(__file__))
import elfseg
from asm import assemble, hi, lo

NODE_CAP = 0x73A
NODE_OLD = (0x8E6339E0, 0x000318C0)       # lw v1,0x39E0(s3); sll v1,v1,3
NODES = [(0x16E8F8, 0x16E970), (0x16EA50, 0x16EAC8), (0x16EC04, 0x16EC7C), (0x16EDFC, 0x16EE74)]
STORE = 0xAE6339E0                        # sw v1,0x39E0(s3) (the add's last instruction)

TEX_T = 0xAECE40                          # the texture manager (only instance; the walk passes it)

def _records(q):
    return struct.pack('<12Q', *q)

# Per-frame slot arrays of T (0xC0 = one 0x60-byte record per packet buffer), as the init 0x16BBF4 /
# 0x16BC80 fills them: name, T offset, slots in place, slots in all, counter (T+0x30000+cnt), record,
# sites (address of `addu v0,v0,at` after the lui/ori of the offset, expected words, next word).
UPLOAD = ('upload', 0x2AFA0, 150, 512, -0x5070,        # goal window: 218 used (session 15)
          _records([0x1000000000008004, 0xE, 0, 0x50, 0, 0x51, 0, 0x52, 0, 0x53, 0x0800000000008000, 0]),
          [(0x16AB8C, (0x3C010002, 0x3421AFA0, 0x00411021), 0x96040000),     # lhu a0,0(s0)
           (0x16ADD4, (0x3C010002, 0x3421AFA0, 0x00411021), 0x96230004)])    # lhu v1,4(s1)
TEXTURE = ('texture', 0x471A0, 1020, NODE_CAP, -0x506C,  # one per node at most; goal window 1,102
           _records([0, 0x5100000513000000, 0x1000000000008004, 0xE, 1, 0x3F, 0x80 << 32, 0x3B, 0x44, 0x42, 0, 6]),
           [(0x16ACDC, (0x3C010004, 0x342171A0, 0x00411021), 0xAFA2009C)])   # sw v0,0x9C(sp)

def slots(elf, seg, arr):
    """Slots >= in-place count -> segment extension; an index past all slots reuses the last one
    (wrong picture, no corruption). Returns (extension address, high-water word address)."""
    name, base, n_in, n_all, cnt, rec, sites = arr
    w = lambda va: elfseg.word(elf, va)
    ext = seg.add(rec * (2 * (n_all - n_in)), 16)
    high = seg.add(bytes(4), 4)
    delta = ext - n_in * 0xC0             # v0 = T + idx*0xC0 + buf*0x60 (+ base in place)
    for site, old, nxt in sites:
        assert tuple(w(site - 8 + 4 * i) for i in range(3)) == old and w(site + 4) == nxt, \
            '%s slot site %x changed' % (name, site)
        stub = seg.code([                 # at = base (lui/ori before the site); v1 is free here
            ('lui', 'v1', 3),
            ('addu', 'v1', 'v1', 's3'),
            ('lw', 'v1', cnt, 'v1'),            # idx
            ('sltiu', 'at', 'v1', n_in),
            ('beqz', 'at', 'out'),
            ('lui', 'at', base >> 16),
            ('ori', 'at', 'at', base & 0xFFFF),
            ('addu', 'v0', 'v0', 'at'),
            ('j', site + 8),
            ('word', nxt),
            'out:',
            ('lui', 'at', hi(high)),            # high-water mark (debug: read it over PINE)
            ('lw', 'at', lo(high), 'at'),
            ('slt', 'at', 'at', 'v1'),
            ('beqz', 'at', 'cap'),
            ('lui', 'at', hi(high)),
            ('sw', 'v1', lo(high), 'at'),
            'cap:',
            ('sltiu', 'at', 'v1', n_all),
            ('bnez', 'at', 'ext'),
            ('sll', 'at', 'v1', 7),             # past all slots: v0 -= idx*0xC0, += (n_all-1)*0xC0
            ('subu', 'v0', 'v0', 'at'),
            ('sll', 'at', 'v1', 6),
            ('subu', 'v0', 'v0', 'at'),
            ('lui', 'at', hi((n_all - 1) * 0xC0)),
            ('addiu', 'at', 'at', lo((n_all - 1) * 0xC0)),
            ('addu', 'v0', 'v0', 'at'),
            'ext:',                             # ext + (idx - n_in)*0xC0 + buf*0x60
            ('subu', 'v0', 'v0', 's3'),
            ('lui', 'at', hi(delta)),
            ('addiu', 'at', 'at', lo(delta)),
            ('addu', 'v0', 'v0', 'at'),
            ('j', site + 8),
            ('word', nxt)])
        elfseg.patch(elf, site, assemble(site, [('j', stub), ('nop',)]))
    print('uifix: %s slots %d-%d at %#x, high-water word %#x' % (name, n_in, n_all - 1, ext, high))
    return ext, high

SPRITE_N, SPRITE_EXT = 1000, 600                 # in place / in the segment (total <= NODE_CAP)
SPRITE_REC, SPRITE_FLAGS = 0x39F0, 0x51BF0
SPRITE_VTBL = 0x67A070

def _sprite_records(base):
    """SPRITE_EXT records at base, formatted like the constructor 0x16FC30 leaves them."""
    out = bytearray(SPRITE_EXT * 0x140)
    for i in range(SPRITE_EXT):
        r = i * 0x140
        struct.pack_into('<II', out, r, SPRITE_VTBL, base + r + 0x2C)
        struct.pack_into('<II', out, r + 0x14, 0x3F800000, 0x3F800000)       # 1.0f
        struct.pack_into('<II', out, r + 0x20, 0x45000000, 0x45000000)       # 2048.0f
        for pk in (0x60, 0xD0):                                              # VIF DIRECTHL 6 + GIF sprite tag
            struct.pack_into('<4I2Q', out, r + pk, 0, 0, 0, 0x51000006, 0x50AB400000008001, 0x41343)
    return bytes(out)

def sprites(elf, seg):
    """Sprite records 1000.. in the segment. Returns (high-water, overflow) word addresses."""
    w = lambda va: elfseg.word(elf, va)
    ext = seg.add(bytes(SPRITE_EXT * 0x140), 16)
    seg.data[ext - seg.base:ext - seg.base + SPRITE_EXT * 0x140] = _sprite_records(ext)
    flags = seg.add(bytes(SPRITE_EXT * 4), 16)
    high = seg.add(bytes(4), 4)
    over = seg.add(bytes(4), 4)
    # allocator 0x16E7F0(a0 = C) -> id (0 when none free), scratch v0 v1 a1 at
    assert w(0x16E7F0) == 0x70001628 and w(0x16E838) == 0x03E00008, 'sprite allocator changed'
    alloc = seg.code([
        ('addu', 'a1', 'zero', 'zero'),
        'scan:',
        ('sll', 'v1', 'a1', 2),
        ('addu', 'v1', 'v1', 'a0'),
        ('lui', 'at', 5),
        ('addu', 'at', 'v1', 'at'),
        ('lw', 'v1', SPRITE_FLAGS - 0x50000, 'at'),
        ('beqz', 'v1', 'found'),
        ('addu', 'v0', 'a1', 'zero'),
        ('addiu', 'a1', 'a1', 1),
        ('sltiu', 'at', 'a1', SPRITE_N),
        ('bnez', 'at', 'scan'),
        ('nop',),
        ('addu', 'a1', 'zero', 'zero'),
        'scan2:',
        ('sll', 'v1', 'a1', 2),
        ('lui', 'at', hi(flags)),
        ('addu', 'at', 'at', 'v1'),
        ('lw', 'v1', lo(flags), 'at'),
        ('beqz', 'v1', 'found'),
        ('addiu', 'v0', 'a1', SPRITE_N),
        ('addiu', 'a1', 'a1', 1),
        ('sltiu', 'at', 'a1', SPRITE_EXT),
        ('bnez', 'at', 'scan2'),
        ('nop',),
        ('lui', 'at', hi(over)),                    # all full: count it, return 0 (as before)
        ('lw', 'v1', lo(over), 'at'),
        ('addiu', 'v1', 'v1', 1),
        ('sw', 'v1', lo(over), 'at'),
        ('jr', 'ra'),
        ('addu', 'v0', 'zero', 'zero'),
        'found:',
        ('lui', 'at', hi(high)),
        ('lw', 'v1', lo(high), 'at'),
        ('slt', 'v1', 'v1', 'v0'),
        ('beqz', 'v1', 'ret'),
        ('lui', 'at', hi(high)),
        ('sw', 'v0', lo(high), 'at'),
        'ret:',
        ('jr', 'ra'),
        ('nop',)])
    elfseg.patch(elf, 0x16E7F0, assemble(0x16E7F0, [('j', alloc), ('nop',)]))
    # 0x16ED40: record address s0 = C + 0x39F0 + id*0x140 (0x16EDA4-0x16EDB4, s2 = id, s3 = C)
    assert (w(0x16EDA4), w(0x16EDB4), w(0x16EDB8)) == (0x00121080, 0x245039F0, 0x3128FFFF), 'sprite record site changed'
    rec = seg.code([
        ('sltiu', 'at', 's2', SPRITE_N),
        ('beqz', 'at', 'ext'),
        ('sll', 'v0', 's2', 2),
        ('addu', 'v0', 'v0', 's2'),
        ('sll', 'v0', 'v0', 6),
        ('addu', 'v0', 's3', 'v0'),
        ('j', 0x16EDB8),
        ('addiu', 's0', 'v0', SPRITE_REC),
        'ext:',
        ('addiu', 'v0', 's2', -SPRITE_N),
        ('sll', 'v1', 'v0', 2),
        ('addu', 'v0', 'v0', 'v1'),
        ('sll', 'v0', 'v0', 6),
        ('lui', 'at', hi(ext)),
        ('addiu', 'at', 'at', lo(ext)),
        ('j', 0x16EDB8),
        ('addu', 's0', 'v0', 'at')])
    elfseg.patch(elf, 0x16EDA4, assemble(0x16EDA4, [('j', rec), ('nop',)]))
    # flag write (0x16EDE8-0x16EDF8: a0 = 1, s2 = id) -> falls into the node add at 0x16EDFC
    assert (w(0x16EDE8), w(0x16EDF8)) == (0x00121880, 0xAC241BF0), 'sprite flag site changed'
    flag = seg.code([
        ('sltiu', 'at', 's2', SPRITE_N),
        ('beqz', 'at', 'ext'),
        ('sll', 'v1', 's2', 2),
        ('addu', 'v1', 'v1', 's3'),
        ('lui', 'at', 5),
        ('addu', 'at', 'v1', 'at'),
        ('j', 0x16EDFC),
        ('sw', 'a0', SPRITE_FLAGS - 0x50000, 'at'),
        'ext:',
        ('addiu', 'v1', 's2', -SPRITE_N),
        ('sll', 'v1', 'v1', 2),
        ('lui', 'at', hi(flags)),
        ('addu', 'at', 'at', 'v1'),
        ('j', 0x16EDFC),
        ('sw', 'a0', lo(flags), 'at')])
    elfseg.patch(elf, 0x16EDE8, assemble(0x16EDE8, [('j', flag), ('nop',)]))
    # per-frame clear 0x16EEA0 (leaf; v1 a1 at scratch): also zero the EXT flags
    assert (w(0x16EEA0), w(0x16EEA4), w(0x16EEC4)) == (0x70001E28, 0x10000007, 0x286503E8), 'sprite clear changed'
    clear = seg.code([
        ('addu', 'v1', 'zero', 'zero'),
        ('lui', 'at', hi(flags)),
        ('addiu', 'a1', 'at', lo(flags)),
        'loop:',
        ('sw', 'zero', 0, 'a1'),
        ('addiu', 'v1', 'v1', 1),
        ('sltiu', 'at', 'v1', SPRITE_EXT),
        ('bnez', 'at', 'loop'),
        ('addiu', 'a1', 'a1', 4),
        ('j', 0x16EEC4),
        ('addu', 'v1', 'zero', 'zero')])
    elfseg.patch(elf, 0x16EEA0, assemble(0x16EEA0, [('j', clear), ('nop',)]))
    print('uifix: sprite records 1000-%d at %#x, sprite_high %#x, sprite_over %#x' % (SPRITE_N + SPRITE_EXT - 1, ext, high, over))
    return high, over

def apply(elf, seg):
    """Bounded render node adds + texture slot extension + sprite records. Returns the node drop counter address."""
    for arr in (UPLOAD, TEXTURE):
        slots(elf, seg, arr)
    sprites(elf, seg)
    w = lambda va: elfseg.word(elf, va)
    drops = seg.add(bytes(4), 4)
    for site, store in NODES:
        assert (w(site), w(site + 4)) == NODE_OLD and w(store) == STORE, 'node add %x changed' % site
        stub = seg.code([
            ('lw', 'v1', 0x39E0, 's3'),
            ('sltiu', 'at', 'v1', NODE_CAP),
            ('bnez', 'at', 'ok'),
            ('nop',),
            ('lui', 'at', hi(drops)),           # pool full: count the drop, skip the add
            ('lw', 'v1', lo(drops), 'at'),
            ('addiu', 'v1', 'v1', 1),
            ('sw', 'v1', lo(drops), 'at'),
            ('j', store + 4),
            ('nop',),
            'ok:',
            ('j', site + 8),
            ('sll', 'v1', 'v1', 3)])
        elfseg.patch(elf, site, assemble(site, [('j', stub), ('nop',)]))
    return drops

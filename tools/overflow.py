"""Buffer-overflow guards: the longest text that can reach each fixed-size buffer.

English is longer than the Japanese and the game copies text into fixed buffers with
no length checks. This module measures the longest possible expansion of every
message that reaches one and fails tl.py apply / the build when it would not fit.
Audit so far: survey part A section 5 (notes/overflow_audit.md once the ELF pass is done).

Expansion model (interpreter 0x40DFA0): text bytes count as is, {02} prints and %s
count VAR bytes (the longest English name or table entry), %d / numbers NUMBER,
{01}C inserts the longest expansion of the inserted entry, {01}J the target instead
of the rest. A taken IF body ends in 05 05 05 (return), so IFs are alternatives: the
longest path counts.

usage: .venv/bin/python tools/overflow.py      # longest expansions vs limits
"""
import os, sys

sys.path.insert(0, os.path.dirname(__file__))
import msgbuf, msgscript

VAR = 48                 # a {02} print / %s: longest English name or table entry
                         # (names.py check must fail on any tl/names entry longer than this)
NUMBER = 11
MSG_BUF = msgbuf.LIMIT   # message output buffer (writer cuts the text there; msgbuf.py)
# whole-block limits: every message of the block must expand to at most this
# KRETSU: the expanded text goes through sprintf into a 0x94-byte field of the treasure
# object (+0x90; part A, GUESS - verify in the emulator pass / ELF phase). The longest
# Japanese entry is 139 bytes.
BLOCK_LIMITS = {'kretsu': (0x94 - 1, 'KRETSU sprintf into the 0x94-byte object field (+0x90, guess)')}
# (block, entry) -> (limit, where): message expansion incl. the %s string
SITES = {}
# The two blocks stay loaded on the heap (malloc(size + 0x1000)) in a ~5.4 MB heap that
# the English segment also eats into. Free heap is not measured yet: until it is, English
# may grow the two blocks by at most this much in total.
HEAP_GROWTH = 1 << 20
RUN = 255                # longest line (bytes) the layout code is assumed to take (unverified here)

def _tree(toks):
    """tokens -> nodes: ('t', bytes, kind) | ('if', [body nodes]) | ('ret',)."""
    root = []
    stack = [root]
    for kind, b in toks:
        if kind == 'if':
            node = ('if', [])
            stack[-1].append(node)
            stack.append(node[1])
        elif kind in ('end', 'eom'):
            stack[-1].append(('ret',))
            if len(stack) > 1:
                stack.pop()
        else:
            stack[-1].append(('t', b, kind))
    return root

class Measure:
    """Longest expansion of messages; get(block, entry) -> bytes or None."""

    def __init__(self, get):
        self.get = get
        self.memo = {}
        self.active = set()

    def msg(self, n, i):
        k = (n, i)
        if k in self.memo:
            return self.memo[k]
        if k in self.active:                # recursion: count as one variable
            return VAR
        b = self.get(n, i)
        if b is None:
            return 0
        self.active.add(k)
        try:
            v = self.seq(n, _tree(msgscript.tokens(b)))[0]
        except msgscript.ParseError:
            v = len(b) + 8 * VAR
        self.active.discard(k)
        self.memo[k] = v
        return v

    def item(self, n, kind, b):
        """-> (bytes, stop) of one token."""
        if kind == 'text':
            return len(b), False
        if kind == 'nl':
            return 1, False
        if kind == 'cmd':
            if b[:2] == b'\x01J' and len(b) >= 4:
                return self.msg(n, b[2] | b[3] << 8), True
            return 0, False
        c = b[0]
        if c == 0x01 and b[1:2] == b'C' and len(b) >= 4:
            return self.msg(n, b[2] | b[3] << 8), False
        if c == 0x25:
            return (VAR if b[-1:] == b's' else NUMBER), False
        if c == 0x02:
            return VAR, False
        return 0, False                     # 1B/1A codes and stray bytes print nothing

    def seq(self, n, nodes):
        """-> (longest bytes, always returns)."""
        total = best = 0
        for node in nodes:
            if node[0] == 'if':
                v, _ = self.seq(n, node[1])
                best = max(best, total + v)
                continue
            if node[0] == 'ret':
                return max(best, total), True
            v, stop = self.item(n, node[2], node[1])
            total += v
            if stop:
                return max(best, total), True
        return max(best, total), False

def check(get, sizes):
    """-> list of error strings. get(block, entry) -> final bytes or None;
    sizes = {block: entry count}."""
    m = Measure(get)
    errs = []
    for n, count in sizes.items():
        limit, where = BLOCK_LIMITS.get(n, (None, None))
        for i in range(count):
            v = m.msg(n, i)
            if MSG_BUF and v > MSG_BUF:
                errs.append('%s #%d: expands to %d bytes > %d (message buffer)' % (n, i, v, MSG_BUF))
            if limit and v > limit:
                errs.append('%s #%d: expands to %d bytes > %d: %s' % (n, i, v, limit, where))
    for (n, i), (limit, where) in sorted(SITES.items()):
        v = m.msg(n, i)
        if v > limit:
            errs.append('%s #%d: expands to %d bytes > %d: %s' % (n, i, v, limit, where))
    return errs

def main():
    import tl, msgfile
    english, bad = tl.build_all(quiet=True)
    get = lambda n, e: tl.message(n, e) if e < len(tl.rows_of(n)) else None
    m = Measure(get)
    for n in msgfile.BLOCKS:
        worst = sorted(((m.msg(n, i), i) for i in range(len(tl.rows_of(n)))), reverse=True)
        print('%s longest expansions:' % n, ', '.join('#%d %d' % (i, v) for v, i in worst[:10]))
    errs = check(get, {n: len(tl.rows_of(n)) for n in msgfile.BLOCKS})
    print('\n'.join(errs) or 'no message overflows')

if __name__ == '__main__':
    main()

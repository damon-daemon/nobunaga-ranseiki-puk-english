# elfgrep.py PATTERN : ui elfstr entries whose Japanese or English matches (regex); run from the project root
import json, re, sys, glob
en = {}
for f in sorted(glob.glob('tl/elfstr/*.txt')):
    for l in open(f, encoding='utf-8'):
        if l.startswith('#') or '\t' not in l: continue
        k, e = l.rstrip('\n').split('\t', 1); en[k] = e
r = re.compile(sys.argv[1])
for x in json.load(open('script/elfstr.json')):
    if not x['ui']: continue
    e = en.get(x['key'], '')
    if r.search(x['jp']) or r.search(e):
        print(x['key'], x['room'], 'mov' if x['movable'] else 'fix', repr(x['jp']), '->', repr(e))

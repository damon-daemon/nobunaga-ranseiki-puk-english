"""Create the working folder script/ from your own disc image and the English in tl/.

script/ holds the Japanese text extracted from the disc (messages, script/msg/) next to the
English, so it is generated on each machine and never distributed. Run this once after
cloning (and again after editing tl/), then tools/build.py.

usage: .venv/bin/python tools/setup.py [clean.bin]
"""
import hashlib, json, os, sys

sys.path.insert(0, os.path.dirname(__file__))
import iso, msgfile, text

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, 'script')
TL = os.path.join(ROOT, 'tl')
CLEAN = os.path.join(ROOT, 'nobunaga_ranseiki_puk.bin')
CLEAN_SHA1 = '29896216e810e511b466343441a238c89b3059ac'


def sha1(path):
    h = hashlib.sha1()
    with open(path, 'rb') as f:
        while chunk := f.read(1 << 24):
            h.update(chunk)
    return h.hexdigest()


def check_clean(path):
    digest = sha1(path)
    if digest != CLEAN_SHA1:
        sys.exit('%s: SHA-1 %s does not match the clean dump (%s)' % (path, digest, CLEAN_SHA1))


def msg_json(n):
    return os.path.join(SCRIPT, 'msg', '%s.json' % n)


def clean_elf(path=CLEAN):
    return iso.read_file(path, iso.ELF_NAME)


def export_messages(path):
    """script/msg/<block>.json = [{'id', 'jp'}] for the two message blocks."""
    os.makedirs(os.path.join(SCRIPT, 'msg'), exist_ok=True)
    disc = iso.Disc(path)
    for n, name in msgfile.NAMES.items():
        strings = msgfile.strings(disc.file(name))
        rows = [{'id': i, 'jp': text.decode(s)} for i, s in enumerate(strings)]
        with open(msg_json(n), 'w', encoding='utf-8') as f:
            json.dump(rows, f, ensure_ascii=False, indent=0)


def export_elfstr(path):
    """script/elfstr.json = every candidate ELF string with its references (tools/elfstr.py)."""
    import elfstr
    rows = elfstr.survey(elfstr.images(bytearray(clean_elf(path))))
    with open(elfstr.SCRIPT, 'w', encoding='utf-8') as f:
        json.dump(rows, f, ensure_ascii=False, indent=0)
    if not elfstr.check():
        sys.exit('tl/elfstr/ has errors (see above)')


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else CLEAN
    check_clean(path)
    os.makedirs(SCRIPT, exist_ok=True)
    export_messages(path)
    import names, tl
    jp = names.japanese(names.scenarios(path))
    names.export(jp)
    if names.check(jp):
        sys.exit('tl/names/ has errors (see above)')
    export_elfstr(path)
    if not tl.apply_all():
        sys.exit('tl/ has errors (see above)')
    print('script/ ready; now run tools/build.py')


if __name__ == '__main__':
    main()

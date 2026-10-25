"""Dump the vwf SQUEEZE_LOG ring over PINE, oldest first: python sqlog.py [ring]"""
import sys, struct
sys.path.insert(0, '/home/leto2/Projects/nobunaga_ranseiki_puk_translation/tools/emu')
import pine
ring = int(sys.argv[1], 0) if len(sys.argv) > 1 else 0x19f0924
off = pine.read32(ring - 4)
data = pine.read(ring, 64 * 16)
n = off // 16
for k in range(max(0, n - 64), n):
    i = (k % 64) * 16
    s3, s5, v0, s6 = struct.unpack_from('<4i', data, i)
    txt = pine.read(s3, 48).split(b'\0')[0].split(b'\n')[0]
    print('%4d str %#x s5 %4d w %4d mode %d  %r' % (k, s3, s5, v0, s6, txt.decode('cp932', 'replace')))
print('total squeezed lines:', n)

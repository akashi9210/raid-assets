"""Raid WW2 Bundle-Zugriff: bundle_db.blb + *_h.bundle + *.bundle."""
import os, struct, zlib, glob, collections
from hash64 import hash64

ASSETS = "/home/akashi/.local/share/Steam/steamapps/common/RAID World War II/assets/"
PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read_blocks(path):
    d = open(path, 'rb').read()
    out = []
    o = 8
    tot, = struct.unpack_from('<Q', d, 0)
    got = 0
    while o < len(d) and got < tot:
        c, = struct.unpack_from('<I', d, o)
        o += 4
        blk = d[o:o + c]
        try:
            out.append(zlib.decompressobj().decompress(blk))
        except zlib.error:
            if c != 65536:
                raise
            out.append(blk)  # unkomprimierter Block
        got += len(out[-1])
        o += c
    return b''.join(out)[:tot]


class Db:
    def __init__(self):
        hl = [l.strip() for l in open(os.path.join(PROJ, 'hashlist.txt')) if l.strip()]
        self.names = {hash64(s): s for s in hl}
        self.exts = {hash64(s): s for s in hl if '/' not in s}
        d = open(ASSETS + 'bundle_db.blb', 'rb').read()
        n, = struct.unpack_from('<I', d, 0x38)
        self.entries = {}  # id -> (ext, path, hash-path)
        for i in range(n):
            ext, path, lang, u1, id_, u2 = struct.unpack_from('<QQIIII', d, 280 + i * 32)
            e = self.exts.get(ext, '%016x' % ext)
            p = self.names.get(path)
            self.entries[id_] = (e, p if p else '%016x' % path, lang)
        self.loc = {}  # id -> (bundle, addr, len)
        self._index()
        self.cache = collections.OrderedDict()

    def _index(self):
        for hp in glob.glob(ASSETS + '*_h.bundle'):
            name = os.path.basename(hp)[:-9]
            if os.path.getsize(hp) < 100:
                continue
            try:
                h = read_blocks(hp)
                cnt, = struct.unpack_from('<I', h, 4)
                if cnt == 0:  # Multi-/Stream-Bundle: Eintraege (id, addr, len), einzeln zlib-komprimiert
                    cnt, = struct.unpack_from('<I', h, 12)
                    for i in range(cnt):
                        id_, a, l = struct.unpack_from('<III', h, 52 + i * 12)
                        if l:
                            self.loc.setdefault(id_, (name, a, l, True))
                    continue
                tot = os.path.getsize(ASSETS + name + '.bundle')
                if tot < 20:
                    continue
                tot, = struct.unpack_from('<Q', open(ASSETS + name + '.bundle', 'rb').read(8))
                items = {}
                for i in range(cnt):
                    id_, addr = struct.unpack_from('<II', h, 0x24 + i * 8)
                    items.setdefault(id_, addr)
                addrs = sorted(set(items.values()) | {tot})
                for id_, a in items.items():
                    nxt = addrs[addrs.index(a) + 1]
                    self.loc[id_] = (name, a, nxt - a, False)
            except Exception:
                continue

    def bundle(self, name):
        if name in self.cache:
            self.cache.move_to_end(name)
            return self.cache[name]
        b = read_blocks(ASSETS + name + '.bundle')
        self.cache[name] = b
        if len(self.cache) > 6:
            self.cache.popitem(last=False)
        return b

    def get(self, id_):
        name, a, l, single = self.loc[id_]
        if single:
            with open(ASSETS + name + '.bundle', 'rb') as f:
                f.seek(a)
                raw = f.read(l)
            try:
                return zlib.decompress(raw)
            except zlib.error:
                return raw  # kleine Dateien liegen unkomprimiert vor
        return self.bundle(name)[a:a + l]

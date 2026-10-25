#!/usr/bin/env python3
"""Raid WW2 Asset-Viewer (Texturen + Modelle). Start: python3 server.py [port]"""
import io, json, os, re, sys, threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from PIL import Image
from raidlib import Db
from modelparse import parse_model, to_json
from hash64 import hash64
from weapons import Weapons

HERE = os.path.dirname(os.path.abspath(__file__))
print("Lade Index ...", flush=True)
db = Db()
lock = threading.RLock()  # Bundle-Cache ist nicht threadsicher
print("Bereit:", len(db.loc), "Dateien", flush=True)


def listing(ext):
    out = [[i, e[1]] for i, e in db.entries.items() if e[0] == ext and i in db.loc]
    out.sort(key=lambda x: x[1])
    return out


LISTS = {}
BYP = {}
for _i, _e in db.entries.items():
    BYP.setdefault(_e[1], {})[_e[0]] = _i
_mc = {}


def mc_tex(unit):
    """material_config einer Unit -> {material-hash: {'df': textur-id, 'nm': ...}}"""
    if unit in _mc:
        return _mc[unit]
    out = {}
    u = BYP.get(unit, {})
    if 'material_config' in u and u['material_config'] in db.loc:
        with lock:
            xml = db.get(u['material_config']).decode('utf-8', 'replace')
        for m in re.finditer(r'<material\s+([^>]*?)>(.*?)</material>', xml, re.S):
            nm = re.search(r'\bname="([^"]+)"', m[1])
            if not nm:
                continue
            m = (None, nm[1], m[2])
            t = {'name': m[1]}
            for key, tag in (('df', 'diffuse_texture'), ('nm', 'bump_normal_texture')):
                f = re.search(r'<%s\s+file="([^"]+)"' % tag, m[2])
                if f:
                    tid = BYP.get(f[1], {}).get('texture')
                    if tid in db.loc:
                        t[key] = tid
            out['%016x' % hash64(m[1])] = t
    _mc[unit] = out
    return out


WEAPONS = Weapons(db, BYP, mc_tex)


def unit_info(unit):
    u = BYP.get(unit, {})
    out = {'unit': unit, 'files': [], 'materials': [], 'unit_xml': '', 'material_xml': ''}
    for ext, i in sorted(u.items()):
        out['files'].append({'ext': ext, 'id': i, 'size': db.loc[i][2] if i in db.loc else None})
    with lock:
        if 'unit' in u and u['unit'] in db.loc:
            out['unit_xml'] = db.get(u['unit']).decode('utf-8', 'replace')
        if 'material_config' in u and u['material_config'] in db.loc:
            xml = db.get(u['material_config']).decode('utf-8', 'replace')
            out['material_xml'] = xml
            for m in re.finditer(r'<material\s+([^>]*?)>(.*?)</material>', xml, re.S):
                nm = re.search(r'\bname="([^"]+)"', m[1])
                rt = re.search(r'render_template="([^"]+)"', m[1])
                texs = []
                for t in re.finditer(r'<(\w+_texture)\s+[^>]*?file="([^"]+)"', m[2]):
                    texs.append({'slot': t[1], 'path': t[2], 'id': BYP.get(t[2], {}).get('texture')})
                if nm:
                    out['materials'].append({'name': nm[1], 'hash': '%016x' % hash64(nm[1]), 'template': rt[1] if rt else '', 'textures': texs})
    return out


def texture_png(i, chan, maxs=2048):
    with lock:
        raw = db.get(i)
    im = Image.open(io.BytesIO(raw))
    im.load()
    w, h = im.size
    info = {'w': w, 'h': h, 'mode': im.mode}
    if max(w, h) > maxs:
        im = im.resize((max(1, w * maxs // max(w, h)), max(1, h * maxs // max(w, h))), Image.LANCZOS)
    im = im.convert('RGBA')
    if chan == 'rgb':
        im = Image.merge('RGBA', (*im.split()[:3], Image.new('L', im.size, 255)))
    elif chan == 'a':
        a = im.split()[3]
        im = Image.merge('RGBA', (a, a, a, Image.new('L', im.size, 255)))
    elif chan in 'rgb':
        c = im.split()['rgb'.index(chan)]
        im = Image.merge('RGBA', (c, c, c, Image.new('L', im.size, 255)))
    buf = io.BytesIO()
    im.save(buf, 'PNG')
    return buf.getvalue(), info


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def send(self, code, body, ctype, extra=None):
        self.send_response(code)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        p, _, q = self.path.partition('?')
        try:
            if p in ('/', '/index.html'):
                return self.send(200, open(os.path.join(HERE, 'index.html'), 'rb').read(), 'text/html; charset=utf-8')
            m = re.fullmatch(r'/static/([\w.]+)', p)
            if m:
                return self.send(200, open(os.path.join(HERE, 'static', m[1]), 'rb').read(), 'text/javascript')
            m = re.fullmatch(r'/api/list/(texture|model)', p)
            if m:
                if m[1] not in LISTS:
                    LISTS[m[1]] = json.dumps(listing(m[1])).encode()
                return self.send(200, LISTS[m[1]], 'application/json')
            m = re.fullmatch(r'/api/texture/(\d+)', p)
            if m:
                chan = re.search(r'c=(\w+)', q)
                mx = re.search(r's=(\d+)', q)
                png, info = texture_png(int(m[1]), chan[1] if chan else 'rgba', int(mx[1]) if mx else 99999)
                return self.send(200, png, 'image/png', {'X-Info': json.dumps(info), 'Cache-Control': 'max-age=3600'})
            m = re.fullmatch(r'/api/model/(\d+)', p)
            if m:
                mid = int(m[1])
                with lock:
                    raw = db.get(mid)
                ms = to_json(parse_model(raw)['meshes'], None, db.names)
                unit = db.entries[mid][1]
                for j in ms:
                    j['unit'] = unit
                return self.send(200, json.dumps({'meshes': ms, 'tex': mc_tex(unit)}).encode(), 'application/json')
            if p == '/api/weapons':
                return self.send(200, json.dumps(WEAPONS.names()).encode(), 'application/json')
            m = re.fullmatch(r'/api/weaponinfo/(\w+)', p)
            if m:
                return self.send(200, json.dumps(WEAPONS.info(m[1])).encode(), 'application/json')
            if p == '/api/unitinfo':
                u = re.search(r'unit=([^&]+)', q)
                from urllib.parse import unquote
                return self.send(200, json.dumps(unit_info(unquote(u[1]))).encode(), 'application/json')
            m = re.fullmatch(r'/api/weapon/(\w+)', p)
            if m:
                bp = re.search(r'bp=([^&]*)', q)
                with lock:
                    res = WEAPONS.assemble(m[1], [x for x in bp[1].split(',') if x] if bp else None)
                return self.send(200, json.dumps(res).encode(), 'application/json')
            m = re.fullmatch(r'/api/raw/(\d+)', p)
            if m:
                with lock:
                    raw = db.get(int(m[1]))
                return self.send(200, raw, 'application/octet-stream',
                                 {'Content-Disposition': 'attachment; filename="%s"' % m[1]})
            self.send(404, b'nicht gefunden', 'text/plain')
        except Exception as e:
            self.send(500, ('Fehler: %r' % e).encode(), 'text/plain')


if __name__ == '__main__':
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    print('http://localhost:%d' % port, flush=True)
    ThreadingHTTPServer(('127.0.0.1', port), H).serve_forever()

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
    if ext == 'tool':   # Granaten, Nahkampf, Equipment: fps-Einzelmodelle
        out = [[i, e[1]] for i, e in db.entries.items()
               if e[0] == 'model' and i in db.loc and TOOL_RE.match(e[1]) and not TOOL_SKIP.search(e[1])]
        out.sort(key=lambda x: x[1])
        return out
    out = [[i, e[1]] for i, e in db.entries.items() if e[0] == ext and i in db.loc]
    out.sort(key=lambda x: x[1])
    return out


LISTS = {}
BYP = {}
TOOL_RE = re.compile(r'^units/[\w]+/(?:weapons|equipment)/.*/(?:wpn_\w*?_?(?:gre|mel)_\w+|equip_\w+)$')
TOOL_SKIP = re.compile(r'_(?:hand|husk|tps|third|npc)\b|wpn_(?:tps|third)_|_pts/')
for _i, _e in db.entries.items():
    BYP.setdefault(_e[1], {})[_e[0]] = _i
_mc = {}
_dirmc = {}


def mc_id(unit):
    """ID der material_config einer Unit; ohne eigene die gemeinsame im selben Ordner (z.B. georg_parts_fps)."""
    u = BYP.get(unit, {})
    if 'material_config' in u and u['material_config'] in db.loc:
        return u['material_config']
    d = unit.rsplit('/', 1)[0]
    if d not in _dirmc:
        _dirmc[d] = [(p, v['material_config']) for p, v in BYP.items()
                     if p.startswith(d + '/') and '/' not in p[len(d) + 1:] and 'material_config' in v and v['material_config'] in db.loc]
    c = _dirmc[d]
    if not c:
        return None
    tag = 'fps' if unit.endswith('fps') else 'tps' if unit.endswith('tps') else ''
    m = [x for x in c if tag and x[0].endswith(tag)] or c
    return m[0][1] if len(m) == 1 or tag else None


def mc_tex(unit):
    """material_config einer Unit -> {material-hash: {'df': textur-id, 'nm': ...}}"""
    if unit in _mc:
        return _mc[unit]
    out = {}
    mid = mc_id(unit)
    if mid is not None:
        with lock:
            xml = db.get(mid).decode('utf-8', 'replace')
        for m in re.finditer(r'<material\s+([^>]*?)(?:/>|>(.*?)</material>)', xml, re.S):
            nm = re.search(r'\bname="([^"]+)"', m[1])
            if not nm:
                continue
            m = (None, nm[1], m[2] or '')
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

# Mod-Skins: modskins.json -> eigene DDS-Dateien als Texturen mit ids ab 9000001
MODTEX = {}
_mf = os.path.join(HERE, 'modskins.json')
if os.path.exists(_mf):
    for _s in json.load(open(_mf))['skins']:
        _map = {}
        for _orig, _file in _s['replace'].items():
            _id = 9000001 + len(MODTEX)
            MODTEX[_id] = os.path.join(os.path.expanduser(_s['dir']), _file)
            _map[int(_orig)] = _id
        WEAPONS.modskins.setdefault(_s['weapon'], []).append({'id': _s['id'], 'label': _s['label'], 'map': _map})


def unit_info(unit):
    u = BYP.get(unit, {})
    out = {'unit': unit, 'files': [], 'materials': [], 'unit_xml': '', 'material_xml': ''}
    for ext, i in sorted(u.items()):
        out['files'].append({'ext': ext, 'id': i, 'size': db.loc[i][2] if i in db.loc else None})
    with lock:
        if 'unit' in u and u['unit'] in db.loc:
            out['unit_xml'] = db.get(u['unit']).decode('utf-8', 'replace')
        mid = mc_id(unit)
        if mid is not None:
            xml = db.get(mid).decode('utf-8', 'replace')
            out['material_xml'] = xml
            for m in re.finditer(r'<material\s+([^>]*?)(?:/>|>(.*?)</material>)', xml, re.S):
                nm = re.search(r'\bname="([^"]+)"', m[1])
                rt = re.search(r'render_template="([^"]+)"', m[1])
                texs = []
                for t in re.finditer(r'<(\w+_texture)\s+[^>]*?file="([^"]+)"', m[2] or ''):
                    texs.append({'slot': t[1], 'path': t[2], 'id': BYP.get(t[2], {}).get('texture')})
                if nm:
                    out['materials'].append({'name': nm[1], 'hash': '%016x' % hash64(nm[1]), 'template': rt[1] if rt else '', 'textures': texs})
    return out


def texture_png(i, chan, maxs=2048):
    if i in MODTEX:
        raw = open(MODTEX[i], 'rb').read()
    else:
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


def export_diffuse(name):
    """Alle Diffuse-Texturen einer Waffe (Basis, alle Aufsaetze, fps + third, Skin-Varianten) als PNG nach ../export/<waffe>/."""
    info = WEAPONS.info(name)
    units = [info['unit']]
    for opts in info['slots'].values():
        for o in opts:
            units.append(o['unit'])
            tu = WEAPONS.parts.get(o['part'], {}).get('third_unit')
            if tu:
                units.append(tu)
    units += [u.replace('wpn_fps_', 'wpn_third_') for u in list(units)]
    ids = {}
    for u in dict.fromkeys(units):
        if u in BYP:
            for t in mc_tex(u).values():
                if t.get('df') is not None:
                    ids[t['df']] = None
    for p in WEAPONS.parts.values():   # Skin-Varianten
        for mt in (p.get('materials_fps') or {}).values():
            tid = BYP.get(mt.get('diffuse_texture'), {}).get('texture') if isinstance(mt, dict) else None
            if tid in db.loc and p.get('clone_of', '').startswith(name):
                ids[tid] = None
    out = os.path.join(HERE, '..', 'export', name)
    os.makedirs(out, exist_ok=True)
    files = []
    for i in ids:
        path = db.entries[i][1]
        fn = os.path.basename(path) + '.png'
        png, _ = texture_png(i, 'rgba', 99999)
        with open(os.path.join(out, fn), 'wb') as f:
            f.write(png)
        files.append(fn)
    return {'dir': os.path.normpath(out), 'count': len(files), 'files': sorted(files)}


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
            m = re.fullmatch(r'/api/list/(texture|model|tool)', p)
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
                tex = mc_tex(unit)
                if 'clean=1' in q:   # Tools: Platzhalterboxen und Schattenmeshes ausblenden
                    clean = [j for j in ms if any((tex.get(h) or {}).get('name', 'lambert1') not in ('lambert1', 'shadow_caster') for h in j['mats'])]
                    ms = clean or ms
                return self.send(200, json.dumps({'meshes': ms, 'tex': tex}).encode(), 'application/json')
            m = re.fullmatch(r'/api/export/(\w+)', p)
            if m:
                with lock:
                    res = export_diffuse(m[1])
                return self.send(200, json.dumps(res).encode(), 'application/json')
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
                sk = re.search(r'skin=(\w*)', q)
                with lock:
                    res = WEAPONS.assemble(m[1], [x for x in bp[1].split(',') if x] if bp else None, sk[1] if sk and sk[1] else None)
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

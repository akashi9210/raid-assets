"""Waffen aus weaponfactorytweakdata.lua zusammenbauen."""
import re, os, json
import numpy as np
from hash64 import hash64
from modelparse import parse_model, to_json

HERE = os.path.dirname(os.path.abspath(__file__))


def parse_lua(path):
    parts, weapons = {}, {}
    cur = None  # (kind, name, depth)
    lst = None
    for line in open(path, encoding='utf-8', errors='replace'):
        s = line.strip()
        if cur is None:
            m = re.match(r'self\.parts\.(\w+) = \{$', s)
            if m:
                cur = ('p', m[1], 1); parts[m[1]] = {}; continue
            m = re.match(r'self\.(wpn_\w+) = \{$', s)
            if m:
                cur = ('w', m[1], 1); weapons[m[1]] = {}; continue
            continue
        kind, name, depth = cur
        d = parts[name] if kind == 'p' else weapons[name]
        if depth == 1:
            m = re.match(r'(\w+) = "([^"]*)",?$', s)
            if m:
                d[m[1]] = m[2]
            m = re.match(r'(\w+) = \{$', s)
            if m:
                lst = m[1]
                if lst in ('default_blueprint', 'uses_parts'):
                    d[lst] = []
        elif lst in ('default_blueprint', 'uses_parts') and depth == 2:
            m = re.match(r'"([^"]+)"', s)
            if m:
                d[lst].append(m[1])
        depth += s.count('{') - s.count('}')
        if depth <= 0:
            cur = None; lst = None
        else:
            cur = (kind, name, depth)
    weapons = {k: v for k, v in weapons.items() if 'default_blueprint' in v and 'unit' in v}
    return parts, weapons


class Weapons:
    def __init__(self, db, byp, texmap):
        self.db, self.byp, self.tex = db, byp, texmap
        self.parts, self.weapons = parse_lua(os.path.join(HERE, 'data', 'weaponfactory.lua'))

    def names(self):
        out = []
        for k, v in self.weapons.items():
            if k.endswith('_npc') or '_third_' in k:
                continue
            u = self.byp.get(v['unit'], {})
            if 'model' in u:
                out.append(k)
        return sorted(out)

    def _load(self, unit):
        u = self.byp.get(unit)
        if not u or 'model' not in u:
            return None
        r = parse_model(self.db.get(u['model']))
        r['unit'] = unit
        return r

    def info(self, name):
        w = self.weapons[name]
        slots = {}
        for pn in w.get('uses_parts', w['default_blueprint']):
            p = self.parts.get(pn)
            if not p or 'unit' not in p or p['unit'] not in self.byp:
                continue
            slots.setdefault('%s|%s' % (p.get('type', '?'), p.get('a_obj', '')), []).append(
                {'part': pn, 'name': p.get('name_id', pn), 'parent': p.get('parent'), 'a_obj': p.get('a_obj'),
                 'unit': p['unit'], 'forbids': self.parts[pn].get('forbids', [])})
        return {'slots': slots, 'default': w['default_blueprint'], 'unit': w['unit']}

    def assemble(self, name, blueprint=None):
        w = self.weapons[name]
        base = self._load(w['unit'])
        placed = []  # (type, model, world-xf)
        eye = np.eye(4, dtype='f4')
        if base:
            placed.append(('base', base, eye, w['unit']))
        anchors = {}  # hash -> world matrix (Basis zuerst)
        if base:
            anchors.update(base['objects'])
        by_type = {}
        missing = []
        todo = list(blueprint if blueprint is not None else w['default_blueprint'])
        # forbids anwenden, Teile ohne gewaehltes Elternteil entfernen
        forbidden = set()
        for pn in todo:
            forbidden |= set(self.parts.get(pn, {}).get('forbids', []))
        todo = [pn for pn in todo if pn not in forbidden]
        types = {self.parts.get(pn, {}).get('type') for pn in todo}
        todo = [pn for pn in todo if not self.parts.get(pn, {}).get('parent') or self.parts[pn]['parent'] in types]
        # Teile mit parent erst nach ihrem Elternteil
        todo.sort(key=lambda n: 1 if self.parts.get(n, {}).get('parent') else 0)
        for pn in todo:
            part = self.parts.get(pn)
            if not part or 'unit' not in part:
                missing.append(pn); continue
            mdl = self._load(part['unit'])
            if not mdl:
                missing.append(pn); continue
            h = hash64(part.get('a_obj', 'a_body'))
            src = by_type.get(part.get('parent')) if part.get('parent') else None
            xf = None
            if src and h in src['objects']:
                xf = src['objects'][h] @ src['xf']
            elif h in anchors:
                xf = anchors[h]
            else:
                for q in placed:
                    if h in q[1]['objects']:
                        xf = q[1]['objects'][h] @ q[2]; break
            if xf is None:
                xf = eye
                missing.append(pn + ' (kein Anker ' + part.get('a_obj', '?') + ')')
            mdl['xf'] = xf
            by_type[part.get('type')] = mdl
            placed.append((pn, mdl, xf, part['unit']))
        meshes = []
        for pn, mdl, xf, unit in placed:
            for j in to_json(mdl['meshes'], xf, self.db.names):
                j['part'] = pn
                j['unit'] = unit
                j['ptype'] = self.parts.get(pn, {}).get('type')
                j['a_obj'] = self.parts.get(pn, {}).get('a_obj')
                j['mc'] = self.tex(unit)
                meshes.append(j)
        return {'meshes': meshes, 'missing': missing}

"""Waffen aus weaponfactorytweakdata.lua zusammenbauen."""
import re, os, json, copy
import numpy as np
from hash64 import hash64
from modelparse import parse_model, to_json

HERE = os.path.dirname(os.path.abspath(__file__))


def parse_lua(path):
    """Liest Teile und Waffen aus dem (dekompilierten) weaponfactorytweakdata.lua (beide Schreibweisen)."""
    parts, weapons = {}, {}
    vars_ = {}    # lokale Lua-Tabellen (z.B. materials_fps, unique_materials)
    cur = None    # ('p'|'w', name, depth) bei Block "= {"
    lst = None    # (dict, key) bei Listen
    lst_depth = 0
    var = None    # (name, stack, depth) beim Einlesen einer lokalen Tabelle
    for line in open(path, encoding='utf-8', errors='replace'):
        s = line.strip()
        if var is not None:                       # lokale Tabelle (verschachtelt: Material -> Kanal -> Pfad)
            name, stack, depth = var
            m = re.match(r'(\w+) = \{$', s)
            if m:
                stack.append({})
                stack[-2][m[1]] = stack[-1]
            m = re.match(r'(\w+) = "([^"]*)",?$', s)
            if m:
                stack[-1][m[1]] = m[2]
            m = re.match(r'"([^"]*)",?$', s)
            if m:
                stack[0].setdefault('_list', []).append(m[1])
            depth += s.count('{') - s.count('}')
            if s.startswith('}') and len(stack) > 1:
                stack.pop()
            if depth <= 0:
                vars_[name] = stack[0].get('_list', stack[0]) if '_list' in stack[0] else stack[0]
                var = None
            else:
                var = (name, stack, depth)
            continue
        if lst is not None:                       # Liste "x = {" ... "}"
            m = re.match(r'"([^"]+)"', s)
            if m:
                lst[0][lst[1]].append(m[1])
            lst_depth += s.count('{') - s.count('}')
            if lst_depth <= 0:
                lst = None
            continue
        if cur is None:
            m = re.match(r'table\.insert\(self\.(wpn_\w+)\.uses_parts, "([^"]+)"\)$', s)
            if m and m[1] in weapons:
                weapons[m[1]].setdefault('uses_parts', []).append(m[2])
                continue
            m = re.match(r'self\.parts\.(\w+) = deep_clone\(self\.parts\.(\w+)\)$', s)
            if m and m[2] in parts:
                parts[m[1]] = copy.deepcopy(parts[m[2]])
                parts[m[1]]['clone_of'] = m[2]
                continue
            m = re.match(r'self\.parts\.(\w+)\.(materials_fps|materials_tps) = (\w+)$', s)
            if m and m[1] in parts and m[3] in vars_:
                parts[m[1]][m[2]] = vars_[m[3]]
                continue
            m = re.match(r'(?:local\s+)?(\w+) = (\w+)$', s)
            if m and m[2] in vars_ and not s.startswith('self'):
                vars_[m[1]] = vars_[m[2]]
                continue
            m = re.match(r'(?:local\s+)?(\w+) = \{(\})?$', s)
            if m and not s.startswith('self'):
                if m[2]:
                    vars_[m[1]] = {}
                else:
                    var = (m[1], [{}], 1)
                continue
            m = re.match(r'self\.parts\.(\w+) = \{(\})?$', s)
            if m:
                parts[m[1]] = {}
                if not m[2]:
                    cur = ('p', m[1], 1)
                continue
            m = re.match(r'self\.(wpn_\w+) = \{(\})?$', s)
            if m:
                weapons[m[1]] = {}
                if not m[2]:
                    cur = ('w', m[1], 1)
                continue
            m = re.match(r'self\.(parts|wpn_\w+?)(?:\.(\w+))?\.(\w+) = (?:"([^"]*)"|(\{)(\})?)$', s)
            if m and m[3] in ('unit', 'a_obj', 'type', 'parent', 'default_blueprint', 'uses_parts', 'third_unit', 'name_id'):
                pass
            m = re.match(r'self\.parts\.(\w+)\.(\w+) = "([^"]*)"$', s)
            if m and m[1] in parts:
                parts[m[1]][m[2]] = m[3]
                continue
            m = re.match(r'self\.(wpn_\w+)\.(\w+) = "([^"]*)"$', s)
            if m and m[1] in weapons:
                weapons[m[1]][m[2]] = m[3]
                continue
            m = re.match(r'self\.(wpn_\w+)\.(default_blueprint|uses_parts) = \{(\})?$', s)
            if m and m[1] in weapons:
                weapons[m[1]][m[2]] = []
                if not m[3]:
                    lst, lst_depth = (weapons[m[1]], m[2]), 1
                continue
            continue
        kind, name, depth = cur
        d = parts[name] if kind == 'p' else weapons[name]
        if depth == 1:
            m = re.match(r'(\w+) = "([^"]*)",?$', s)
            if m:
                d[m[1]] = m[2]
            m = re.match(r'(\w+) = (\w+),?$', s)
            if m and m[2] in vars_ and isinstance(vars_[m[2]], list):
                d[m[1]] = vars_[m[2]]
            m = re.match(r'(\w+) = \{$', s)
            if m and m[1] in ('default_blueprint', 'uses_parts', 'forbids'):
                d[m[1]] = []
                lst, lst_depth = (d, m[1]), 1
                continue
        depth += s.count('{') - s.count('}')
        cur = None if depth <= 0 else (kind, name, depth)
    weapons = {k: v for k, v in weapons.items() if v.get('default_blueprint') and 'unit' in v}
    return parts, weapons


def _verts(mdl, xf=None):
    out = []
    for me in mdl['meshes']:
        p = me['geo']['pos']
        m = me['m'] if xf is None else me['m'] @ xf
        out.append((np.hstack([p, np.ones((len(p), 1), 'f4')]) @ m)[:, :3])
    return np.concatenate(out) if out else np.zeros((0, 3), 'f4')


def _inside(v, box, margin=3.0):
    if not len(v) or box is None:
        return 1.0
    lo, hi = box[0] - margin, box[1] + margin
    return float(np.mean(np.all((v >= lo) & (v <= hi), axis=1)))


class Weapons:
    def __init__(self, db, byp, texmap):
        self.db, self.byp, self.tex = db, byp, texmap
        self.parts, self.weapons = parse_lua(os.path.join(HERE, 'data', 'weaponfactory.lua'))
        self._discover()

    ANCHORS = {'barrel': ['a_barrel', 'a_b'], 'b': ['a_b', 'a_barrel'], 'body': ['a_body'], 'mag': ['a_mag', 'a_m'],
               'm': ['a_m', 'a_mag'], 'slide': ['a_slide', 'a_sl'], 'sl': ['a_sl', 'a_slide'], 'stock': ['a_stock', 'a_s'],
               's': ['a_s', 'a_stock'], 'bolt': ['a_bolt'], 'trigger': ['a_trigger'], 'catch': ['a_catch'],
               'safety': ['a_safety'], 'dh': ['a_dh'], 'sight': ['a_sight'], 'o': ['a_o', 'a_sight']}

    def _discover(self):
        """Waffen, die nicht in der (aelteren) Lua-Konfiguration stehen: Teile aus dem Dateibaum ableiten."""
        gun = re.compile(r'^(units/[\w]+/weapons/(wpn_fps_(?:pis|smg|ass|lmg|snp|sho)_\w+))/\2$')
        for unit, files in list(self.byp.items()):
            m = gun.match(unit)
            if not m or 'model' not in files or m[2] in self.weapons or m[2].endswith('_npc'):
                continue
            d, name = m[1], m[2]
            cands = {}
            for k, v in self.byp.items():
                if 'model' not in v or re.search(r'third|tps|npc', k):
                    continue
                if k.startswith(d + '/parts/') and k.endswith('_fps'):
                    stem = k[len(d) + 7:-4]
                elif k.startswith(d + '_pts/'):
                    stem = k[len(d) + 5:].replace(name + '_', '', 1)
                else:
                    continue
                cands[stem] = k
            if not cands:
                continue
            slots = {}
            for stem, k in sorted(cands.items()):
                tok = stem.split('_')[0]
                slots.setdefault(tok, []).append((stem, k))
            default, uses = [], []
            for tok, lst in slots.items():
                for stem, k in lst:
                    pn = name + '__' + stem
                    anc = self.ANCHORS.get(tok, ['a_' + tok])
                    self.parts[pn] = {'unit': k, 'type': tok, 'a_obj': anc[0], 'a_cands': anc, 'name_id': stem}
                    uses.append(pn)
                pick = [pn for pn in (name + '__' + s for s, _ in lst) if re.search(r'default|standard|_long', pn)] or [name + '__' + lst[0][0]]
                if tok != 'stock':
                    default.append(pick[0])
            self.weapons[name] = {'unit': unit, 'default_blueprint': default, 'uses_parts': uses, 'synthetic': '1'}

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

    def _token_variants(self, w):
        """Alternative Teile, deren Name ein zusaetzliches Token enthaelt (garand_golden_body_standard <-> garand_body_standard),
        gleicher Typ und Anker: Token -> {Basisteil: Variante}."""
        names = [n for n in w.get('uses_parts', []) if n in self.parts and 'unit' in self.parts[n] and not self.parts[n].get('materials_fps')]
        by_toks = {}
        for n in names:
            by_toks.setdefault(tuple(n.split('_')), []).append(n)
        out = {}
        for p in names:
            toks = p.split('_')
            for i in range(len(toks) - 1):   # Token am Ende (m_standard_double, b_long_finned) sind echte Teile-Alternativen
                q = by_toks.get(tuple(toks[:i] + toks[i + 1:]))
                if not q:
                    continue
                pp, qq = self.parts[p], self.parts[q[0]]
                if pp.get('type') == qq.get('type') and pp.get('a_obj') == qq.get('a_obj'):
                    out.setdefault(toks[i], {})[q[0]] = p
        return out

    modskins = {}   # Waffe -> [{'id','label','map': {orig_tex_id: mod_tex_id}}], von server.py gesetzt

    def info(self, name):
        w = self.weapons[name]
        slots = {}
        skins = {}
        tokvar = self._token_variants(w)
        tokvar_parts = {v for d in tokvar.values() for v in d.values()}
        for tok in tokvar:
            skins['tok_' + tok] = tok
        for pn in w.get('uses_parts', w['default_blueprint']):
            p = self.parts.get(pn)
            if p and p.get('clone_of') and p.get('materials_fps'):   # Skin-Variante eines Teils
                suffix = pn[len(p['clone_of']):] if pn.startswith(p['clone_of']) else pn
                tex = next((t for mt in p['materials_fps'].values() for t in mt.values() if isinstance(t, str)), '')
                lab = re.search(r'/skins/([^/]+)/', tex)
                skins.setdefault(suffix, lab[1] if lab else suffix.lstrip('_'))
                continue
            if pn in tokvar_parts:
                continue
            if not p or 'unit' not in p or p['unit'] not in self.byp:
                continue
            slots.setdefault('%s|%s' % (p.get('type', '?'), p.get('a_obj', '')), []).append(
                {'part': pn, 'name': p.get('name_id', pn), 'parent': p.get('parent'), 'a_obj': p.get('a_obj'),
                 'unit': p['unit'], 'forbids': self.parts[pn].get('forbids', [])})
        sk = [{'id': k, 'label': v} for k, v in sorted(skins.items())]
        sk += [{'id': m['id'], 'label': m['label']} for m in self.modskins.get(name, [])]
        return {'slots': slots, 'default': w['default_blueprint'], 'unit': w['unit'], 'skins': sk}

    def _skinned(self, mc, part, override):
        """Textur-Override eines Skins (wie NewRaycastWeaponBase:apply_modified_textures): nur Materialien aus unique_materials."""
        uniq = part.get('unique_materials')
        out = {}
        for h, t in mc.items():
            t2 = dict(t)
            ov = override.get(t.get('name'))
            if ov and (uniq is None or t.get('name') in uniq):
                for ch, key in (('diffuse_texture', 'df'), ('bump_normal_texture', 'nm')):
                    tid = self.byp.get(ov.get(ch), {}).get('texture') if ov.get(ch) else None
                    if tid is not None:
                        t2[key] = tid
            out[h] = t2
        return out

    def assemble(self, name, blueprint=None, skin=None):
        w = self.weapons[name]
        base = self._load(w['unit'])
        placed = []  # (type, model, world-xf)
        eye = np.eye(4, dtype='f4')
        if base:
            placed.append(('base', base, eye, w['unit']))
        anchors = {}  # hash -> world matrix (Basis zuerst)
        anchor_amp = {}
        if base:
            anchors.update(base['objects'])
            anchor_amp = base['amp']
        by_type = {}
        missing = []
        todo = list(blueprint if blueprint is not None else w['default_blueprint'])
        if skin and skin.startswith('tok_'):
            tv = self._token_variants(w).get(skin[4:], {})
            todo = [tv.get(n, n) for n in todo]
            alt = w['unit'].replace(name, name + '_' + skin[4:])   # z.B. wpn_fps_ass_garand_golden
            if alt in self.byp and 'model' in self.byp[alt]:
                base = self._load(alt) or base
        # forbids anwenden, Teile ohne gewaehltes Elternteil entfernen
        forbidden = set()
        for pn in todo:
            forbidden |= set(self.parts.get(pn, {}).get('forbids', []))
        todo = [pn for pn in todo if pn not in forbidden]
        types = {self.parts.get(pn, {}).get('type') for pn in todo}
        todo = [pn for pn in todo if not self.parts.get(pn, {}).get('parent') or self.parts[pn]['parent'] in types]
        # Teile mit parent erst nach ihrem Elternteil
        by_t = {self.parts.get(n, {}).get('type'): n for n in todo}

        def depth(n, d=0):
            par = self.parts.get(n, {}).get('parent')
            return d if not par or par not in by_t or d > 8 else depth(by_t[par], d + 1)
        todo.sort(key=lambda n: depth(n) + (0 if self.parts.get(n, {}).get('type') == 'body' else 1 if w.get('synthetic') else 0))
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
            if w.get('synthetic'):  # Anker zuerst in schon platzierten Teilen suchen, dann in der Basis
                for cand in part.get('a_cands', [part.get('a_obj', 'a_body')]):
                    hh = hash64(cand)
                    for q in reversed([q for q in placed if q[0] != 'base']):
                        if hh in q[1]['objects']:
                            xf, h = q[1]['objects'][hh] @ q[2], hh
                            break
                    if xf is None and hh in anchors:
                        xf, h = anchors[hh], hh
                    if xf is not None:
                        break
            elif src and h in src['objects']:
                xf = src['objects'][h] @ src['xf']
            elif h in anchors:
                xf = anchors[h]
            elif xf is None:
                for q in placed:
                    if h in q[1]['objects']:
                        xf = q[1]['objects'][h] @ q[2]; break
            # Teile haben teils eine Wurzel mit Versatz (z.B. Lauf bei y=10): die Wurzel kommt auf den Anker
            if xf is not None and np.abs(mdl['root'][3, :3]).max() > 0.01:
                xf = (np.linalg.inv(mdl['root']) @ xf).astype('f4')
            if xf is None:
                xf = eye
                missing.append(pn + ' (kein Anker ' + part.get('a_obj', '?') + ')')
            mdl['xf'] = xf
            by_type[part.get('type')] = mdl
            placed.append((pn, mdl, xf, part['unit']))
        modmap = next((m['map'] for m in self.modskins.get(name, []) if m['id'] == skin), None) if skin else None
        meshes = []
        for pn, mdl, xf, unit in placed:
            for j in to_json(mdl['meshes'], xf, self.db.names):
                j['part'] = pn
                j['unit'] = unit
                j['ptype'] = self.parts.get(pn, {}).get('type')
                j['a_obj'] = self.parts.get(pn, {}).get('a_obj')
                j['mc'] = self.tex(unit)
                sk = self.parts.get(pn + skin, {}).get('materials_fps') if skin else None
                if sk:
                    j['mc'] = self._skinned(j['mc'], self.parts.get(pn, {}), sk)
                if modmap:
                    j['mc'] = {h: dict(t, df=modmap.get(t.get('df'), t.get('df'))) for h, t in j['mc'].items()}
                meshes.append(j)
        return {'meshes': meshes, 'missing': missing}

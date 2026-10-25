"""Diesel-Modelle (PD2/Raid-Format) -> Meshes (numpy). Nur Geometrie."""
import struct
import numpy as np

T_OBJ3D, T_MODEL, T_GEOM, T_TOPO, T_PGP, T_TIP = 0x0FFCD100, 0x62212D88, 0x7AB072D3, 0x4C507A13, 0xE3A3B1CA, 0x03B634BD
T_MAT, T_MATGRP, T_VEC3CTRL = 0x3C54609C, 0x29276B1D, 0x26A5128C
SIZES = [0, 4, 8, 12, 16, 4, 4, 8, 4, 4]


def sections(b):
    first, = struct.unpack_from('<i', b, 0)
    if first == -1:
        n, = struct.unpack_from('<I', b, 8)
        o = 12
    else:
        n = first
        o = 4
    out = {}
    for _ in range(n):
        t, sid, sz = struct.unpack_from('<III', b, o)
        out[sid] = (t, o + 12, sz)
        o += 12 + sz
    return out


def parse_geom(b, o, sz):
    vc, hc = struct.unpack_from('<II', b, o)
    o += 8
    hdr = [struct.unpack_from('<II', b, o + i * 8) for i in range(hc)]
    o += hc * 8
    ch = {}
    for isz, typ in hdr:
        n = SIZES[isz] * vc
        ch[typ] = (isz, b[o:o + n])
        o += n
    res = {'count': vc}
    if 1 in ch and ch[1][0] == 3:
        res['pos'] = np.frombuffer(ch[1][1], '<f4').reshape(-1, 3)
    if 2 in ch:
        isz, raw = ch[2]
        if isz == 3:
            res['nrm'] = np.frombuffer(raw, '<f4').reshape(-1, 3)
        elif SIZES[isz] == 4:
            res['nrm'] = np.frombuffer(raw, 'u1').reshape(-1, 4)[:, :3].astype('f4') / 127.5 - 1
    if 7 in ch:
        isz, raw = ch[7]
        if isz == 2:
            uv = np.frombuffer(raw, '<f4').reshape(-1, 2)
        elif SIZES[isz] == 4:
            uv = np.frombuffer(raw, '<f2').reshape(-1, 2).astype('f4')
        else:
            uv = None
        if uv is not None:
            res['uv'] = uv
    return res


def parse_topo(b, o, sz):
    _, n = struct.unpack_from('<II', b, o)
    return np.frombuffer(b[o + 8:o + 8 + n * 2], '<u2').astype('u4')


def parse_obj3d(b, o):
    o += 8
    cc, = struct.unpack_from('<I', b, o)
    o += 4 + cc * 12
    m = np.frombuffer(b[o:o + 64], '<f4').reshape(4, 4).copy()
    o += 64
    m[3, :3] = struct.unpack_from('<3f', b, o)
    o += 12
    parent, = struct.unpack_from('<I', b, o)
    return m, parent, o + 4


def parse_model(b):
    sec = sections(b)
    world = {}
    local = {}
    amp = {}
    for sid, (t, o, sz) in sec.items():
        if t in (T_OBJ3D, T_MODEL):
            m, p, _ = parse_obj3d(b, o)
            # Ruhepose: Position-Controller ersetzen die gespeicherte Position durch ihren ersten Keyframe
            cc, = struct.unpack_from('<I', b, o + 8)
            for k in range(cc):
                cid, = struct.unpack_from('<I', b, o + 12 + k * 12)
                if cid in sec and sec[cid][0] == T_VEC3CTRL:
                    co = sec[cid][1]
                    n, = struct.unpack_from('<I', b, co + 20)
                    if n:
                        m[3, :3] = struct.unpack_from('<3f', b, co + 24 + 4)
                    break
            local[sid] = (m, p)
            a = np.zeros(3)
            for k in range(cc):
                cid, = struct.unpack_from('<I', b, o + 12 + k * 12)
                if cid in sec and sec[cid][0] == T_VEC3CTRL:
                    co = sec[cid][1]
                    n, = struct.unpack_from('<I', b, co + 20)
                    if n:
                        kf = np.frombuffer(b[co + 24:co + 24 + n * 16], '<f4').reshape(n, 4)
                        a = np.maximum(a, np.abs(kf[:, 1:]).max(0))
            amp[sid] = a

    def wm(sid, depth=0):
        if sid in world:
            return world[sid]
        m, p = local[sid]
        r = m if (p == 0 or p not in local or depth > 64) else m @ wm(p, depth + 1)
        world[sid] = r
        return r

    objects = {}
    obj_amp = {}

    def chain_amp(sid, depth=0):
        m, p = local[sid]
        return amp.get(sid, 0) + (chain_amp(p, depth + 1) if p in local and depth < 64 else 0)

    for sid, (t, o, sz) in sec.items():
        if t in (T_OBJ3D, T_MODEL):
            hh = struct.unpack_from('<Q', b, o)[0]
            objects[hh] = wm(sid)
            obj_amp[hh] = chain_amp(sid)
    matname = {sid: struct.unpack_from('<Q', b, o)[0] for sid, (t, o, sz) in sec.items() if t == T_MAT}
    meshes = []
    for sid, (t, o, sz) in sec.items():
        if t != T_MODEL:
            continue
        _, _, o2 = parse_obj3d(b, o)
        ver, = struct.unpack_from('<I', b, o2)
        if ver == 6:
            continue
        pgp, tip, na = struct.unpack_from('<III', b, o2 + 4)
        atoms = [struct.unpack_from('<5I', b, o2 + 16 + i * 20) for i in range(na)]
        if pgp not in sec or tip not in sec:
            continue
        g = struct.unpack_from('<I', b, sec[pgp][1])[0]
        tp = struct.unpack_from('<I', b, sec[tip][1])[0]
        if g not in sec or tp not in sec:
            continue
        geo = parse_geom(b, sec[g][1], sec[g][2])
        if 'pos' not in geo:
            continue
        idx = parse_topo(b, sec[tp][1], sec[tp][2])
        mg = struct.unpack_from('<I', b, o2 + 16 + na * 20)[0]
        mats = []
        if mg in sec and sec[mg][0] == T_MATGRP:
            n, = struct.unpack_from('<I', b, sec[mg][1])
            mats = [matname.get(x, 0) for x in struct.unpack_from('<%dI' % n, b, sec[mg][1] + 4)]
        meshes.append({'geo': geo, 'idx': idx, 'atoms': atoms, 'm': wm(sid), 'mats': mats, 'hash': struct.unpack_from('<Q', b, o)[0], 'sid': sid})
    roots = [sid for sid, (m, p) in local.items() if p == 0 or p not in local]
    root = wm(min(roots)) if roots else np.eye(4, dtype='f4')
    return {'meshes': meshes, 'objects': objects, 'amp': obj_amp, 'root': root}


def to_json(meshes, xf=None, names=None):
    out = []
    for me in meshes:
        g = me['geo']
        pos = g['pos']
        nrm = g.get('nrm')
        m = me['m'] if xf is None else me['m'] @ xf
        p = np.hstack([pos, np.ones((len(pos), 1), 'f4')]) @ m
        p = p[:, :3]
        if nrm is not None:
            nrm = nrm @ m[:3, :3]
        d = {'p': np.round(p, 3).ravel().tolist(), 'i': me['idx'].tolist()}
        if nrm is not None:
            d['n'] = np.round(nrm, 3).ravel().tolist()
        d['atoms'] = [[a[0], a[1], a[2], a[3], a[4]] for a in me['atoms']]
        d['mats'] = ['%016x' % x for x in me['mats']]
        d['hash'] = '%016x' % me['hash']
        d['oname'] = (names or {}).get(me['hash']) or d['hash']
        d['sid'] = me['sid']
        d['has_n'] = nrm is not None
        d['has_uv'] = 'uv' in g
        if 'uv' in g:
            uv = g['uv'].copy()
            uv[:, 1] = -uv[:, 1]
            d['uv'] = np.round(uv, 4).ravel().tolist()
        out.append(d)
    return out

"""Minimal Source-engine BSP (v17-21) top-down preview renderer.

Draws brush-face outlines projected onto the XY plane (Source: Z is up).
The 2003-2013 archive predates the int32 edge format, so edges are read as
uint16 pairs; the renderer auto-detects the working edges/surfedges lump
assignment per map and skips maps whose face chain doesn't validate.
"""
from __future__ import annotations

import struct
import numpy as np
from PIL import Image, ImageDraw, ImageFilter


def render_bsp(bsp_path: str, png_path: str, size: int = 640) -> dict:
    d = open(bsp_path, 'rb').read()
    if d[:4] != b'VBSP':
        raise ValueError("not a Source BSP")
    ver, = struct.unpack_from('<i', d, 4)
    if not (17 <= ver <= 23):
        raise ValueError(f"unsupported BSP version {ver}")
    esz = 16 if ver >= 20 else 8
    L = []
    for i in range(64):
        if esz == 16:
            off, ln, _v, _f = struct.unpack_from('<iiii', d, 8 + i * 16)
        else:
            off, ln = struct.unpack_from('<ii', d, 8 + i * 8)
        L.append(d[off:off + ln] if ln > 0 else b'')

    if len(L[3]) == 0 or len(L[3]) % 12:
        raise ValueError("bad vertexes lump")
    verts = np.frombuffer(L[3], dtype='<f4').reshape(-1, 3)[:, :2]
    nv = len(verts)
    fd = L[7]
    nf = len(fd) // 56
    if nf == 0:
        raise ValueError("no faces")

    # auto-detect edges/surfedges: these early maps use uint16 edge pairs
    cands = []
    for ei, si in ((12, 13), (13, 12)):
        a, b = L[ei], L[si]
        for fmt, dt, w in (('u2', '<u2', 2), ('i4', '<i4', 4)):
            if len(a) % w or len(a) // w % 2 or len(b) % 4:
                continue
            edges = np.frombuffer(a, dtype=dt).reshape(-1, 2)
            if len(edges) == 0 or int(np.abs(edges).max()) >= nv:
                continue
            surf = np.abs(np.frombuffer(b, dtype='<i4'))
            if len(surf) == 0 or surf.max() >= len(edges):
                continue
            cands.append((ei, fmt, edges, surf))
            break
        if cands:
            break
    if not cands:
        raise ValueError("edges/surfedges not identified")
    _ei, _fmt, edges, surf = cands[0]

    img = Image.new('L', (size, size), 0)
    dr = ImageDraw.Draw(img)
    lo = verts.min(0)
    hi = verts.max(0)
    span = float((hi - lo).max())
    if span <= 0:
        raise ValueError("degenerate bounds")
    scale = (size - 16) / span

    def proj(p):
        return (float((p[0] - lo[0]) * scale + 8), size - float((p[1] - lo[1]) * scale + 8))

    parsed = 0
    for f in range(nf):
        fe, ne = struct.unpack_from('<ih', fd, f * 56 + 4)
        if ne <= 0 or fe < 0 or fe + ne > len(surf):
            continue
        pts = []
        ok = True
        for k in range(ne):
            e = int(surf[fe + k])
            if e >= len(edges):
                ok = False
                break
            a = int(edges[e][0])
            if a >= nv:
                ok = False
                break
            pts.append(proj(verts[a]))
        if ok and len(pts) >= 2:
            dr.polygon(pts, outline=255)
            parsed += 1
    if parsed < nf * 0.5:
        raise ValueError(f"face chain failed ({parsed}/{nf} parsed)")

    img = img.filter(ImageFilter.GaussianBlur(0.6))
    out = Image.new('RGB', (size, size), (13, 16, 21))
    tinted = Image.merge('RGB', (
        img.point(lambda v: v * 64 // 255),
        img.point(lambda v: v * 190 // 255),
        img.point(lambda v: v * 175 // 255)))
    out.paste(tinted, (0, 0))
    out.save(png_path)
    return {"faces": nf, "parsed": parsed, "verts": nv, "version": ver}

"""Build preview assets (models / map previews / audio) from locally cached
depot payloads and publish them into site/dist/data/previews/.

Currently sources previews from depot 441 (Team Fortress 2) versions whose
dats are fully local (v0-v17, the Oct 2007 launch-era beta). Every emitted
asset cites depot + version so provenance is always verifiable.

Run after the extract step has populated dats/ for depot 441.
"""
from __future__ import annotations

import json
import os
import struct
import subprocess
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

OUT = os.path.join(BASE, "site", "dist", "data", "previews")
TMP = "/tmp/s2preview_extract"
DEPOT = 441
DEPOT_VERSION = 17  # highest fully-local version with model/map content
AUDIO_VERSION = 21  # local for wav content

DEPOT_DESC = {
    "depot": DEPOT,
    "model_version": DEPOT_VERSION,
    "audio_version": AUDIO_VERSION,
    "map_version": DEPOT_VERSION,
    "note": "Team Fortress 2, October 2007 launch-era build extracted from the "
            "archive's own delta chain. Previews render from the original files.",
}


def _extract(path_filter, tag: str) -> str:
    from s2_extract import extract
    out = os.path.join(TMP, tag)
    os.makedirs(out, exist_ok=True)
    res = extract(DEPOT, DEPOT_VERSION, os.path.join(BASE, "blobs"),
                  os.path.join(BASE, "dats"), out, path_filter=path_filter)
    print(f"extract {tag}: {res['files']} files")
    return out


def build_models(extract_root: str) -> list:
    sys.path.insert(0, "/Users/studio/Documents/valveleak/thirdparty")
    from mdl2glb import mdl_to_glb  # local converter (vendored SourceIO loaders)
    models = []
    mroot = os.path.join(OUT, "models", str(DEPOT))
    os.makedirs(mroot, exist_ok=True)
    for dirpath, _dirs, files in os.walk(extract_root):
        for fn in files:
            if not fn.endswith(".mdl"):
                continue
            src = os.path.join(dirpath, fn)
            base = src[:-4]
            if not (os.path.exists(base + ".vvd") and os.path.exists(base + ".dx90.vtx")):
                continue
            rel = os.path.relpath(src, extract_root).replace(os.sep, "/")
            name = os.path.splitext(os.path.basename(fn))[0]
            slug = rel[:-4].replace("/", "_")
            out_glb = os.path.join(mroot, slug + ".glb")
            if os.path.exists(out_glb):
                models.append({"file": f"{DEPOT}/{slug}.glb", "label": rel, "src": f"depot {DEPOT} v{DEPOT_VERSION} · {rel}"})
                continue
            try:
                info = mdl_to_glb(src, out_glb, name)
            except Exception as e:
                print("skip", rel, e)
                continue
            if info["tris"] < 8 or os.path.getsize(out_glb) > 8 * 1024 * 1024:
                os.remove(out_glb)
                continue
            models.append({"file": f"{DEPOT}/{slug}.glb", "label": rel, "src": f"depot {DEPOT} v{DEPOT_VERSION} · {rel}"})
    return models


def build_map_previews(extract_root: str) -> list:
    from bsppreview import render_bsp
    maps = []
    proot = os.path.join(OUT, "maps", str(DEPOT))
    os.makedirs(proot, exist_ok=True)
    mdir = os.path.join(extract_root, "tf", "maps")
    if not os.path.isdir(mdir):
        return maps
    for fn in sorted(os.listdir(mdir)):
        if not fn.endswith(".bsp"):
            continue
        rel = f"tf/maps/{fn}"
        slug = fn[:-4]
        out_png = os.path.join(proot, slug + ".png")
        if os.path.exists(out_png):
            maps.append({"file": f"{DEPOT}/{slug}.png", "label": slug,
                         "src": f"depot {DEPOT} v{DEPOT_VERSION} · {rel}"})
            continue
        try:
            info = render_bsp(os.path.join(mdir, fn), out_png)
        except Exception as e:
            print("skip map", fn, e)
            continue
        maps.append({"file": f"{DEPOT}/{slug}.png", "label": slug,
                     "src": f"depot {DEPOT} v{DEPOT_VERSION} · {rel} (v{info['version']})"})
    return maps


def build_audio(extract_root: str, wav_version: int) -> list:
    """Audio comes from AUDIO_VERSION chain (v21 has more wav content local)."""
    from s2_extract import extract
    aroot = os.path.join(TMP, "audio")
    os.makedirs(aroot, exist_ok=True)
    extract(DEPOT, wav_version, os.path.join(BASE, "blobs"),
            os.path.join(BASE, "dats"), aroot,
            path_filter=lambda p: p.lower().endswith(".wav") and "/sound/" in p.lower())
    audio = []
    aout = os.path.join(OUT, "audio", str(DEPOT))
    os.makedirs(aout, exist_ok=True)
    n = 0
    for dirpath, _dirs, files in os.walk(aroot):
        for fn in sorted(files):
            if not fn.endswith(".wav"):
                continue
            src = os.path.join(dirpath, fn)
            rel = os.path.relpath(src, aroot).replace(os.sep, "/")
            # downsample-free copy; cap at 400 KB per clip for the site
            if os.path.getsize(src) > 400 * 1024:
                continue
            slug = rel[:-4].replace("/", "_")
            dst = os.path.join(aout, slug + ".wav")
            if not os.path.exists(dst):
                with open(src, "rb") as f, open(dst, "wb") as g:
                    g.write(f.read())
            audio.append({"file": f"{DEPOT}/{slug}.wav", "label": rel,
                          "src": f"depot {DEPOT} v{wav_version} · {rel}"})
            n += 1
            if n >= 400:
                return audio
    return audio


def main() -> None:
    os.makedirs(OUT, exist_ok=True)
    mdl_root = _extract(lambda p: p.lower().endswith((".mdl", ".vvd", ".dx90.vtx")), "models")
    map_root = _extract(lambda p: p.lower().endswith(".bsp"), "maps")

    models = build_models(mdl_root)
    maps = build_map_previews(map_root)
    audio = build_audio(os.path.join(TMP, "models"), AUDIO_VERSION)

    index = {
        **DEPOT_DESC,
        "models": sorted(models, key=lambda m: m["label"]),
        "maps": sorted(maps, key=lambda m: m["label"]),
        "audio": sorted(audio, key=lambda a: a["label"]),
    }
    with open(os.path.join(OUT, f"{DEPOT}.json"), "w") as f:
        json.dump(index, f, indent=1)
    print(f"previews: {len(models)} models, {len(maps)} maps, {len(audio)} audio clips")


if __name__ == "__main__":
    main()

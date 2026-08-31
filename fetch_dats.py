"""Fetch specific depot dats (chain-aware) from the mirrors with verify + resume.

Usage:
    python3 fetch_dats.py --depot 441 --to-version 12     # all chain dats v0..12
    python3 fetch_dats.py --depot 451 --to-version 9
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)
from s2_extract import resolve_chain, index_dir  # noqa: E402

MIRRORS = ["https://de.steam2.download", "http://ro.steam2.download", "http://us.steam2.download"]
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(filename: str, dest_dir: str, want_sha: str) -> bool:
    dest = os.path.join(dest_dir, filename)
    if os.path.exists(dest) and sha256_file(dest) == want_sha:
        print(f"  have {filename}")
        return True
    part = dest + ".part"
    url_path = f"dats/{filename}"
    for attempt in range(4):
        resume = os.path.getsize(part) if os.path.exists(part) else 0
        for mirror in MIRRORS:
            try:
                req = urllib.request.Request(f"{mirror}/{url_path}", headers={"User-Agent": UA})
                if resume:
                    req.add_header("Range", f"bytes={resume}-")
                with urllib.request.urlopen(req, timeout=90) as r:
                    mode = "ab" if resume else "wb"
                    with open(part, mode) as f:
                        while True:
                            chunk = r.read(1 << 20)
                            if not chunk:
                                break
                            f.write(chunk)
                if sha256_file(part) == want_sha:
                    os.replace(part, dest)
                    return True
                os.remove(part)
                resume = 0
            except urllib.error.HTTPError as e:
                if e.code in (403, 429, 503):
                    time.sleep(2 + attempt * 2)
                resume = os.path.getsize(part) if os.path.exists(part) else 0
            except Exception:
                resume = os.path.getsize(part) if os.path.exists(part) else 0
        time.sleep(1 + attempt)
    return False


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--depot", type=int, required=True)
    ap.add_argument("--to-version", type=int, required=True)
    ap.add_argument("--blobcrc", default=None)
    ap.add_argument("--out", default=os.path.join(BASE, "dats"))
    args = ap.parse_args()

    os.makedirs(args.out, exist_ok=True)
    blob_dir = os.path.join(BASE, "blobs")

    # Resolve which dats the chain actually needs.
    try:
        chain = resolve_chain(blob_dir, args.out, args.depot, args.to_version, args.blobcrc)
        needed = sorted(chain.dats.keys())
        print(f"chain needs {len(needed)} dats: {needed}")
        missing = [v for v in needed if not os.path.exists(chain.dats[v])]
        # for missing versions we must pick the right dat: use blob's recorded dat size
    except (FileNotFoundError, ValueError) as e:
        # chain incomplete: fall back to "every version up to target, resolve forks later"
        print(f"chain pre-resolve unavailable ({e}); fetching all versions <= target")
        blobs = index_dir(blob_dir, args.depot, "blob")
        missing = None

    if missing is not None:
        # need blob dat_size per version to pick the right file name; do it via blobs
        import s2_parse
        to_fetch = []
        for v in needed:
            lst = index_dir(blob_dir, args.depot, "blob").get(v, [])
            # find the blob whose crc matches the chain's chosen blob for v
            chosen = os.path.basename(chain.blobs[v])
            crc = chosen.split("_")[2]
            blob_data = open(os.path.join(blob_dir, chosen), "rb").read()
            info = s2_parse.parse_blob(blob_data)
            dat_size = info.dat_size
            # dat name from catalog DB
            import sqlite3
            con = sqlite3.connect(os.path.join(BASE, "index", "steam2.db"))
            row = con.execute(
                "SELECT crc, sha256, size FROM files WHERE depot=? AND version=? AND kind='dat'",
                (args.depot, v)).fetchall()
            match = next((r for r in row if r[2] == dat_size), None)
            if match is None and len(row) == 1:
                match = row[0]
            if match is None:
                print(f"  v{v}: cannot pick dat (sizes {[(r[2]) for r in row]})")
                continue
            name = f"{args.depot}_{v}_{match[0]}_{match[1]}.dat"
            to_fetch.append((name, match[1]))
    else:
        import sqlite3
        con = sqlite3.connect(os.path.join(BASE, "index", "steam2.db"))
        to_fetch = []
        for (v, crc, sha) in con.execute(
                "SELECT version, crc, sha256 FROM files WHERE depot=? AND kind='dat' AND version<=?",
                (args.depot, args.to_version)):
            to_fetch.append((f"{args.depot}_{v}_{crc}_{sha}.dat", sha))

    ok = fail = 0
    total = 0
    t0 = time.time()
    for name, sha in to_fetch:
        if download(name, args.out, sha):
            ok += 1
            total += os.path.getsize(os.path.join(args.out, name))
        else:
            fail += 1
            print(f"  FAIL {name}")
        done = ok + fail
        if done % 5 == 0 or done == len(to_fetch):
            rate = done / max(time.time() - t0, 1e-9)
            print(f"  [{done}/{len(to_fetch)}] ok={ok} fail={fail} {total/1048576:.0f} MiB ({rate:.1f}/s)")
    print(f"DONE ok={ok} fail={fail}")
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()

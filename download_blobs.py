"""Download all Steam2 blobs (metadata layer) with resume + sha256 verify + failover.

Blobs are the ~15.8 GB metadata layer (manifests) and carry no encrypted
payload; every blob's sha256 is embedded as the 4th component of its filename.

Usage:
    python3 download_blobs.py              # all blobs from index/blobs_dates.txt
    python3 download_blobs.py --max 500    # first 500 for a smoke run
"""
from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

BASE = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE, "blobs")
INDEX = os.path.join(BASE, "index", "blobs_dates.txt")

MIRRORS = [
    "https://de.steam2.download",
    "http://ro.steam2.download",
    "http://us.steam2.download",
]

WORKERS = 8
RETRIES = 3
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_name(filename: str):
    stem = filename.rsplit(".", 1)[0]
    parts = stem.split("_")
    if len(parts) != 4:
        return None
    depot, version, crc, sha = parts
    return int(depot), int(version), crc, sha


def fetch(url: str, dest: str, resume_from: int = 0) -> int:
    """Download `url` to `dest` (appending if resume_from>0). Returns bytes pulled."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    if resume_from > 0:
        req.add_header("Range", f"bytes={resume_from}-")
    with urllib.request.urlopen(req, timeout=60) as resp:
        mode = "ab" if resume_from > 0 else "wb"
        pulled = 0
        with open(dest, mode) as f:
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                f.write(chunk)
                pulled += len(chunk)
        return pulled


def download_one(filename: str) -> str:
    dest = os.path.join(OUT_DIR, filename)
    info = parse_name(filename)
    if info is None:
        return f"BADNAME {filename}"

    want_sha = info[3]

    # Already good?
    if os.path.exists(dest):
        try:
            if sha256_file(dest) == want_sha:
                return f"have {filename}"
        except OSError:
            pass

    part = dest + ".part"
    last_err = None
    for attempt in range(RETRIES + 1):
        resume_from = os.path.getsize(part) if os.path.exists(part) else 0
        for mirror in MIRRORS:
            url = f"{mirror}/blobs/{filename}"
            try:
                pulled = fetch(url, part, resume_from)
                got = resume_from + pulled
                # Verify before trusting; on mismatch, discard and retry from scratch.
                if sha256_file(part) == want_sha:
                    os.replace(part, dest)
                    return f"ok {filename} ({got} bytes)"
                # sha mismatch: restart clean
                if os.path.exists(part):
                    os.remove(part)
                resume_from = 0
                last_err = f"sha mismatch from {mirror}"
            except urllib.error.HTTPError as e:
                last_err = f"HTTP {e.code} {mirror}"
                if e.code == 416 and resume_from > 0:
                    # Range not satisfiable (file already complete?) -> restart
                    if os.path.exists(part):
                        os.remove(part)
                    resume_from = 0
                elif e.code in (403, 429, 500, 502, 503):
                    time.sleep(1.5 + 2.0 * attempt)  # back off on throttling/transient errors
            except Exception as e:  # noqa: BLE001
                last_err = f"{type(e).__name__} {mirror}: {e}"
        time.sleep(0.2 * (attempt + 1))

    # cleanup any partial
    if os.path.exists(part):
        os.remove(part)
    return f"FAIL {filename}: {last_err}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=0, help="limit to first N blobs")
    ap.add_argument("--workers", type=int, default=WORKERS)
    ap.add_argument("--only-depot", type=int, default=0, help="only this depot id")
    args = ap.parse_args()

    os.makedirs(OUT_DIR, exist_ok=True)

    names = []
    with open(INDEX, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            filename = line.split("\t")[0]
            if args.only_depot and parse_name(filename) is not None:
                if parse_name(filename)[0] != args.only_depot:
                    continue
            names.append(filename)

    if args.max:
        names = names[: args.max]

    total = len(names)
    done = 0
    ok = 0
    fail = 0
    have = 0
    t0 = time.time()

    # Pre-count already-verified to report accurate progress.
    print(f"targeting {total} blobs -> {OUT_DIR}", flush=True)

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(download_one, n): n for n in names}
        for fut in as_completed(futs):
            done += 1
            res = fut.result()
            if res.startswith("ok"):
                ok += 1
            elif res.startswith("have"):
                have += 1
            elif res.startswith("FAIL"):
                fail += 1
                print(res, flush=True)
            if done % 500 == 0 or done == total:
                rate = done / max(time.time() - t0, 1e-9)
                print(f"  [{done}/{total}] ok={ok} have={have} fail={fail} "
                      f"({rate:.1f} files/s)", flush=True)

    print(f"DONE ok={ok} have={have} fail={fail} total={total}", flush=True)
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()

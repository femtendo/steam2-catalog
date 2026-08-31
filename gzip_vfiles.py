"""Compress the vfiles JSONs to .gz (fetch() decompresses transparently) and
drop the whole-file variant for giants (they use paged diffs instead).

Run after build_vfiles.py. Result should be < 1 GiB total.
"""
from __future__ import annotations

import gzip
import json
import os

BASE = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(BASE, "site", "dist", "data", "vfiles")

WHOLE_LIMIT = 40 * 1024 * 1024  # depots whose gz JSON exceeds this stay paged-only


def main() -> None:
    total_after = 0
    n_keep = 0
    n_drop = 0
    dropped = []
    for fn in sorted(os.listdir(DIST)):
        path = os.path.join(DIST, fn)
        if not fn.endswith(".json"):
            continue
        raw = open(path, "rb").read()
        gz = gzip.compress(raw, 6)
        gz_path = path + ".gz"
        with open(gz_path, "wb") as f:
            f.write(gz)
        os.remove(path)

        total_after += len(gz)

        # giants whose whole-file JSON is too big: remove the .gz, keep only meta/pages
        if fn.endswith(".meta.json") or "." in fn[:-5]:
            n_keep += 1
            continue
        if len(gz) > WHOLE_LIMIT:
            os.remove(gz_path)
            total_after -= len(gz)
            n_drop += 1
            dropped.append((fn, len(gz) // 1048576))
        else:
            n_keep += 1

    print(f"gzipped: {n_keep} kept, {n_drop} giants dropped to paged-only")
    for fn, mb in dropped:
        print(f"  paged-only: {fn} ({mb} MiB gz)")
    print(f"total vfiles size: {total_after / 1024 ** 3:.2f} GiB")


if __name__ == "__main__":
    main()

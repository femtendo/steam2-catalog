"""Compute the leak verification percentage for the site header.

Metric = manifest coverage: versions with parsed manifests / total (depot,version)
pairs in the archive. This is the honest number — it counts versions whose full
file listing is actually indexed, not just downloaded blobs.

Writes site/dist/data/verify.json
"""
from __future__ import annotations

import json
import os
import sqlite3

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "index", "steam2.db")
DIST = os.path.join(BASE, "site", "dist", "data")


def main() -> None:
    con = sqlite3.connect(DB)
    total_versions = con.execute(
        "SELECT COUNT(*) FROM (SELECT DISTINCT depot, version FROM files)").fetchone()[0]
    indexed = con.execute("SELECT COUNT(*) FROM versions").fetchone()[0]
    total_depots = con.execute("SELECT COUNT(DISTINCT depot) FROM files").fetchone()[0]
    labeled = con.execute("SELECT COUNT(*) FROM labels").fetchone()[0]
    blobs = con.execute("SELECT COUNT(*) FROM files WHERE kind='blob'").fetchone()[0]
    con.close()

    pct = 100.0 * indexed / total_versions if total_versions else 0.0
    out = {
        "indexed_versions": indexed,
        "total_versions": total_versions,
        "total_depots": total_depots,
        "labeled_depots": labeled,
        "total_blobs": blobs,
        "pct": round(pct, 1),
    }
    os.makedirs(DIST, exist_ok=True)
    with open(os.path.join(DIST, "verify.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"))
    print(f"verified: {pct:.1f}% ({indexed}/{total_versions} versions)")


if __name__ == "__main__":
    main()

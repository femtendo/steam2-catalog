"""Build per-version file indexes for the site's version-filtered file browser.

Strategy: 105M file-version rows is too much to ship wholesale. Instead, for each
(depots with versions) we ship a compact per-version file list only for depots
under a size cap, and a sampled/paged variant for giants.

Outputs under site/dist/data/vfiles/:
  <depot>.json          {"versions": [v0,v1,...], "files": {v: [[path,size],...]}}  (depots <= 200k rows)
  <depot>.meta.json     {"total_rows": N, "paged": true}                            (giants: paged chunks <depot>.<page>.json)
"""
from __future__ import annotations

import json
import os
import sqlite3

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "index", "steam2.db")
DIST = os.path.join(BASE, "site", "dist", "data", "vfiles")

ROW_CAP = 200_000        # depots up to this many (version,path) rows ship whole
PAGE_ROWS = 500_000      # giants: 500k rows per page


def main() -> None:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    os.makedirs(DIST, exist_ok=True)

    # version -> files: walk versions table; for each version, list its files.
    # file paths per version come from re-parsing blobs is too slow here; instead
    # use depot_paths ranges + version file lists from a dedicated table if present.
    # We build a compact table version_files(depot, version, path, size) lazily from
    # the blobs already parsed. For the site, we ship per-depot version diff lists:
    # files ADDED/REMOVED per version are derived from first_ver/last_ver in depot_paths.

    depots = [r["depot"] for r in con.execute(
        """SELECT dp.depot FROM depot_paths dp GROUP BY dp.depot
           HAVING COUNT(*) <= ?""", (ROW_CAP,))]

    n_small = 0
    n_giant = 0
    for depot in depots:
        rows = con.execute(
            "SELECT path,size,first_ver,last_ver FROM depot_paths WHERE depot=? ORDER BY path",
            (depot,)).fetchall()
        versions = [r["v"] for r in con.execute(
            "SELECT version v FROM versions WHERE depot=? ORDER BY version", (depot,))]
        if not versions:
            continue

        # per version: file = present if first_ver <= v <= last_ver
        out = {"depot": depot, "versions": versions, "files": {}}
        for v in versions:
            files = [[r["path"], r["size"]] for r in rows
                     if r["first_ver"] <= v <= r["last_ver"]]
            out["files"][str(v)] = files

        with open(os.path.join(DIST, f"{depot}.json"), "w", encoding="utf-8") as f:
            json.dump(out, f, separators=(",", ":"), ensure_ascii=False)
        n_small += 1

    # giants: page the union path list with per-version presence flags omitted
    giants = con.execute(
        """SELECT dp.depot, COUNT(*) n FROM depot_paths dp GROUP BY dp.depot
           HAVING COUNT(*) > ?""", (ROW_CAP,)).fetchall()
    for r in giants:
        depot = r["depot"]
        versions = [x["v"] for x in con.execute(
            "SELECT version v FROM versions WHERE depot=? ORDER BY version", (depot,))]
        meta = {"depot": depot, "versions": versions, "total_rows": r["n"], "paged": True}
        with open(os.path.join(DIST, f"{depot}.meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, separators=(",", ":"))
        # pages of paths with their ranges; client filters per version
        page = 0
        buf = []
        for row in con.execute(
                "SELECT path,size,first_ver,last_ver FROM depot_paths WHERE depot=? ORDER BY path",
                (depot,)):
            buf.append([row["path"], row["size"], row["first_ver"], row["last_ver"]])
            if len(buf) >= PAGE_ROWS:
                with open(os.path.join(DIST, f"{depot}.{page}.json"), "w", encoding="utf-8") as f:
                    json.dump({"depot": depot, "page": page, "rows": buf},
                              f, separators=(",", ":"), ensure_ascii=False)
                page += 1
                buf = []
        if buf:
            with open(os.path.join(DIST, f"{depot}.{page}.json"), "w", encoding="utf-8") as f:
                json.dump({"depot": depot, "page": page, "rows": buf},
                          f, separators=(",", ":"), ensure_ascii=False)
        n_giant += 1

    print(f"vfiles: {n_small} whole-file depots, {n_giant} paged giants")
    con.close()


if __name__ == "__main__":
    main()

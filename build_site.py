"""Generate a static catalog website from index/steam2.db.

Produces under site/dist/:
  data/catalog.json      per-depot summary (all depots)
  data/findings.json     discovery report (copied from index/)
  data/depots/<id>.json  per-depot file paths + version history
  index.html             single-page app (copied from site/)

Nothing is fetched at serve time; the site is pure static files. Download
links point at the public mirrors (de/ro/us), never at hosted payloads.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "index", "steam2.db")
SITE_SRC = os.path.join(BASE, "site")
DIST = os.path.join(BASE, "site", "dist")

MIRRORS = ["https://de.steam2.download", "http://ro.steam2.download", "http://us.steam2.download"]


def main() -> None:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row

    data_dir = os.path.join(DIST, "data")
    depot_dir = os.path.join(data_dir, "depots")
    os.makedirs(depot_dir, exist_ok=True)

    labels = {r["depot"]: r["label"] for r in con.execute("SELECT depot,label FROM labels")}

    # per-depot aggregate from files table
    stats = {}
    for r in con.execute(
        """SELECT depot,
                  MAX(CASE WHEN kind='blob' THEN version END) AS max_version,
                  COUNT(DISTINCT version) AS distinct_versions,
                  MIN(date) AS first_date, MAX(date) AS last_date,
                  SUM(CASE WHEN kind='blob' THEN size END) AS blob_bytes,
                  SUM(CASE WHEN kind='dat'  THEN size END) AS dat_bytes,
                  SUM(CASE WHEN kind='blob' THEN 1 ELSE 0 END) AS n_blobs,
                  SUM(CASE WHEN kind='dat'  THEN 1 ELSE 0 END) AS n_dats
           FROM files GROUP BY depot"""
    ):
        stats[r["depot"]] = dict(r)

    # latest-version roots per depot (for a manifest-derived name fallback)
    roots = {}
    for r in con.execute(
        """SELECT v.depot, v.roots FROM versions v
           JOIN (SELECT depot, MAX(version) mv FROM versions GROUP BY depot) m
             ON v.depot=m.depot AND v.version=m.mv"""
    ):
        roots[r["depot"]] = r["roots"].split("\x1f") if r["roots"] else []

    # path counts per depot
    path_counts = {r["depot"]: r["n"] for r in con.execute(
        "SELECT depot, COUNT(*) n FROM depot_paths GROUP BY depot")}

    # ---- catalog.json ----
    catalog = []
    for depot, st in stats.items():
        catalog.append({
            "depot": depot,
            "label": labels.get(depot, ""),
            "manifest_roots": roots.get(depot, []),
            "first_date": st["first_date"],
            "last_date": st["last_date"],
            "max_version": st["max_version"],
            "distinct_versions": st["distinct_versions"],
            "n_blobs": st["n_blobs"],
            "n_dats": st["n_dats"],
            "blob_bytes": st["blob_bytes"],
            "dat_bytes": st["dat_bytes"],
            "path_count": path_counts.get(depot, 0),
        })
    catalog.sort(key=lambda d: d["depot"])

    with open(os.path.join(data_dir, "catalog.json"), "w", encoding="utf-8") as f:
        json.dump(catalog, f, separators=(",", ":"), ensure_ascii=False)

    # ---- per-depot detail files ----
    cur_depot = None
    cur_paths = None
    for r in con.execute("SELECT depot,path,size,first_ver,last_ver FROM depot_paths ORDER BY depot,path"):
        if r["depot"] != cur_depot:
            if cur_depot is not None:
                _write_depot(depot_dir, cur_depot, cur_paths, versions_of(con, cur_depot), files_of(con, cur_depot))
            cur_depot = r["depot"]
            cur_paths = []
        cur_paths.append({"p": r["path"], "s": r["size"],
                          "f": r["first_ver"], "l": r["last_ver"]})
    if cur_depot is not None:
        _write_depot(depot_dir, cur_depot, cur_paths, versions_of(con, cur_depot), files_of(con, cur_depot))

    # ---- findings ----
    src = os.path.join(BASE, "index", "findings.json")
    if os.path.exists(src):
        shutil.copy(src, os.path.join(data_dir, "findings.json"))

    # ---- static assets ----
    for name in ("index.html", "style.css", "app.js"):
        s = os.path.join(SITE_SRC, name)
        if os.path.exists(s):
            shutil.copy(s, os.path.join(DIST, name))

    print(f"catalog: {len(catalog)} depots")
    print(f"wrote site -> {DIST}")
    con.close()


def versions_of(con, depot):
    rows = con.execute(
        "SELECT version, app_id, ver_id, node_count, file_count, roots "
        "FROM versions WHERE depot=? ORDER BY version", (depot,)).fetchall()
    return [{"v": r["version"], "app": r["app_id"], "ver": r["ver_id"],
             "nodes": r["node_count"], "files": r["file_count"],
             "roots": r["roots"].split("\x1f") if r["roots"] else []}
            for r in rows]


def files_of(con, depot):
    """Blob/dat archive files for this depot, to build mirror download links."""
    rows = con.execute(
        "SELECT depot,version,crc,sha256,kind,size,date FROM files "
        "WHERE depot=? ORDER BY kind, version", (depot,)).fetchall()
    out = []
    for r in rows:
        name = f"{r['depot']}_{r['version']}_{r['crc']}_{r['sha256']}.{r['kind']}"
        out.append({"name": name, "v": r["version"], "crc": r["crc"],
                    "kind": r["kind"], "size": r["size"], "date": r["date"]})
    return out


def _write_depot(depot_dir, depot, paths, versions, files):
    with open(os.path.join(depot_dir, f"{depot}.json"), "w", encoding="utf-8") as f:
        json.dump({"depot": depot, "versions": versions, "paths": paths, "files": files},
                  f, separators=(",", ":"), ensure_ascii=False)


if __name__ == "__main__":
    main()

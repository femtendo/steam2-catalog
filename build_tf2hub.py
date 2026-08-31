"""Build the TF2 hub page (site/data/tf2.json + tf2 section data).

Gathers: depot inventory for the TF2 family, version timeline, cut content,
bundle manifest. Rendered by the site frontend.
"""
from __future__ import annotations

import json
import os
import sqlite3

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "index", "steam2.db")
DIST = os.path.join(BASE, "site", "dist")

TF2_DEPOTS = {
    441: "TF2 content (main)",
    442: "TF2 engine / binaries",
    443: "TF2 engine (alt branch)",
    444: "TF2 (language)",
    445: "TF2 (language)",
    446: "TF2 (client binary)",
    448: "TF2 (client binary)",
    449: "TF2 (dedicated server)",
    451: "TF2 client (mac)",
    452: "TF2 (language)",
    522: "TF2 Beta content",
    523: "TF2 Beta engine",
    524: "TF2 Beta (alt)",
    525: "TF2 Beta (alt)",
    526: "TF2 Beta (alt)",
    527: "TF2 Beta dedicated server",
    821: "HL2 / TF2 shared",
    826: "TF2 shared",
}


def main() -> None:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row

    labels = {r["depot"]: r["label"] for r in con.execute("SELECT depot,label FROM labels")}

    depots = []
    for depot, role in TF2_DEPOTS.items():
        st = con.execute(
            """SELECT COUNT(DISTINCT version) dv,
                      MAX(CASE WHEN kind='blob' THEN version END) mv,
                      MIN(date) first_date, MAX(date) last_date,
                      SUM(CASE WHEN kind='dat' THEN size END) dat_bytes
               FROM files WHERE depot=?""", (depot,)).fetchone()
        n_paths = con.execute("SELECT COUNT(*) FROM depot_paths WHERE depot=?",
                              (depot,)).fetchone()[0]
        n_maps = con.execute(
            "SELECT COUNT(*) FROM depot_paths WHERE depot=? AND (path LIKE 'tf/maps/%' OR path LIKE 'maps/%')",
            (depot,)).fetchone()[0]
        if st["dv"] == 0:
            continue
        depots.append({
            "depot": depot, "role": role, "label": labels.get(depot, ""),
            "versions": st["dv"], "max_version": st["mv"],
            "first_date": st["first_date"], "last_date": st["last_date"],
            "dat_bytes": st["dat_bytes"], "path_count": n_paths, "map_count": n_maps,
        })

    # version timeline for the main content depot: files + map count per version
    timeline = []
    for r in con.execute(
            """SELECT v.version, v.file_count, v.roots
               FROM versions v WHERE v.depot=441 ORDER BY v.version"""):
        timeline.append({"v": r["version"], "files": r["file_count"]})

    # cut content across the TF2 family (files removed by the final version)
    cut = []
    for depot in (441, 442, 443):
        mx = con.execute(
            "SELECT MAX(version) FROM versions WHERE depot=?", (depot,)).fetchone()[0]
        if not mx:
            continue
        for r in con.execute(
                """SELECT path, first_ver, last_ver FROM depot_paths
                   WHERE depot=? AND last_ver < ?
                   AND (path LIKE '%.bsp' OR path LIKE '%.mdl' OR path LIKE '%.wav'
                        OR path LIKE '%.vmt' OR path LIKE '%weapon%' OR path LIKE '%protO%')
                   ORDER BY path LIMIT 400""", (depot, mx)):
            cut.append({"depot": depot, "path": r["path"],
                        "f": r["first_ver"], "l": r["last_ver"]})

    out = {"depots": depots, "timeline": timeline, "cut": cut,
           "generated": __import__("datetime").datetime.utcnow().isoformat()}
    os.makedirs(os.path.join(DIST, "data"), exist_ok=True)
    with open(os.path.join(DIST, "data", "tf2.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, separators=(",", ":"), ensure_ascii=False)
    print(f"tf2 hub: {len(depots)} depots, timeline {len(timeline)} versions, "
          f"cut {len(cut)} files")
    con.close()


if __name__ == "__main__":
    main()

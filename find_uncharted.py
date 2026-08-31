"""Discovery engine: flag "uncharted" / interesting depots for validation.

Signals (weighted):
  unlabeled          no curated name, or a placeholder name          +10
  valve_test_app     label is a ValveTestApp* internal test app      +15
  pre_release        label marks beta/demo/press/review/prototype    +5
  valve_marker       Valve-specific unreleased marker (fstop,        +25 each
                     weaponizer, icegun, paintgun)
  ep3_marker         Half-Life Ep3 marker (episode3/ep3/...)         +25 if Valve depot, else +5
  dev_marker         generic dev/cut marker (wip/unused/proto/...)   +10 each
  cut_content        file present early, gone by final version       +8  (+20 if its name is a marker)
  content_mismatch   non-game label but game payload inside          +8

Outputs: index/findings.json (machine) and a ranked human summary on stdout.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "index", "steam2.db")

PLACEHOLDER = {"unknown / no depot", "unknown", "no depot", "n/a", "none", "-", "?", "--", ""}

VALVE_ONLY = re.compile(
    r"f-?stop|f_?stop|weaponizer|ice\s*gun|ice_?gun|paint\s*gun|paint_?gun",
    re.IGNORECASE,
)
EP3 = re.compile(r"episode\s*3|episode_?3|/ep3/|ep3_|_ep3\b|\bep3\b", re.IGNORECASE)
DEV = re.compile(
    r"\bwip\b|_wip|wip_|unused|unreleased|scrapped|cut_|_cut\b|internal|"
    r"devtest|placeholder|_old|old_|backup|draft|deleted|pre-?alpha|pre_?alpha|prototype|proto_",
    re.IGNORECASE,
)

GAME_EXT = re.compile(r"\.(bsp|vpk|map|vmf|vcd|exe|dll|nut)$", re.IGNORECASE)
NON_GAME_LABEL = re.compile(r"trailer|video|movie|teaser|short|tv spot|commercial|intro|localiz",
                            re.IGNORECASE)
TEST_APP = re.compile(r"test\s*app|testapp|valvetest", re.IGNORECASE)
PRE_RELEASE = re.compile(r"beta|demo|press|review|preview|prototype|alpha|release candidate",
                         re.IGNORECASE)
VALVE_LABEL = re.compile(
    r"half-?life|portal|left 4 dead|team fortress|counter-?strike|day of defeat|"
    r"dota|alien swarm|ricochet|deathmatch|source|valvetest|valve test",
    re.IGNORECASE,
)


def main() -> None:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row

    labels = {r["depot"]: r["label"] for r in con.execute("SELECT depot,label FROM labels")}

    stats = {}
    for r in con.execute(
        """SELECT depot,
                  MAX(CASE WHEN kind='blob' THEN version END) AS max_version,
                  MIN(date) AS first_date, MAX(date) AS last_date,
                  SUM(CASE WHEN kind='blob' THEN size END) AS blob_bytes,
                  SUM(CASE WHEN kind='dat'  THEN size END) AS dat_bytes,
                  SUM(CASE WHEN kind='blob' THEN 1 ELSE 0 END) AS n_blobs,
                  SUM(CASE WHEN kind='dat'  THEN 1 ELSE 0 END) AS n_dats
           FROM files GROUP BY depot"""
    ):
        stats[r["depot"]] = dict(r)

    # Streaming scan of every path -> per-depot keyword hits (by category) + ext histogram.
    hits = {}  # depot -> {"v": {kw:[paths]}, "e": {kw:[paths]}, "d": {kw:[paths]}}
    game_ext = {}  # depot -> count
    cut = {}  # depot -> {"total": n, "interesting": [paths]}

    def rec(depot, cat, kw, path):
        d = hits.setdefault(depot, {"v": {}, "e": {}, "d": {}})
        lst = d[cat].setdefault(kw, [])
        if len(lst) < 12:
            lst.append(path)

    for r in con.execute("SELECT depot,path,size,first_ver,last_ver FROM depot_paths"):
        depot = r["depot"]
        path = r["path"]
        p = path.lower()
        mx = stats.get(depot, {}).get("max_version") or 0

        v = {m.group(0).lower() for m in VALVE_ONLY.finditer(p)}
        e = {m.group(0).lower() for m in EP3.finditer(p)}
        d = {m.group(0).lower() for m in DEV.finditer(p)}

        for kw in v:
            rec(depot, "v", kw, path)
        for kw in e:
            rec(depot, "e", kw, path)
        for kw in d:
            rec(depot, "d", kw, path)

        if GAME_EXT.search(p):
            game_ext[depot] = game_ext.get(depot, 0) + 1

        if mx and r["last_ver"] < mx:
            c = cut.setdefault(depot, {"total": 0, "interesting": []})
            c["total"] += 1
            if v or e or d:
                c["interesting"].append(path)

    findings = []
    for depot, st in sorted(stats.items()):
        label = labels.get(depot, "")
        lab_low = label.lower()
        valve_ctx = bool(VALVE_LABEL.search(lab_low))

        flags = []
        score = 0
        evidence = {"keywords": {}}

        if label.strip().lower() in PLACEHOLDER or depot not in labels:
            flags.append("unlabeled")
            score += 10

        if TEST_APP.search(lab_low):
            flags.append("valve_test_app")
            score += 15

        if PRE_RELEASE.search(lab_low):
            flags.append("pre_release")
            score += 5

        if NON_GAME_LABEL.search(lab_low) and game_ext.get(depot, 0) > 0:
            flags.append("content_mismatch")
            score += 8

        d = hits.get(depot, {})
        for kw, paths in d.get("v", {}).items():
            score += 25
            evidence["keywords"][kw] = paths[:8]
        for kw, paths in d.get("e", {}).items():
            score += 25 if valve_ctx else 5
            evidence["keywords"][kw] = paths[:8]
        for kw, paths in d.get("d", {}).items():
            score += 10
            evidence["keywords"][kw] = paths[:8]

        if cut.get(depot, {}).get("interesting"):
            flags.append("cut_content")
            score += 20
            evidence["cut_content"] = cut[depot]["interesting"][:8]
        elif cut.get(depot, {}).get("total", 0) > 50:
            flags.append("cut_content")
            score += 8

        if not flags:
            continue

        findings.append({
            "depot": depot,
            "label": label,
            "flags": flags,
            "score": score,
            "max_version": st.get("max_version"),
            "first_date": st.get("first_date"),
            "last_date": st.get("last_date"),
            "blob_bytes": st.get("blob_bytes"),
            "dat_bytes": st.get("dat_bytes"),
            "n_blobs": st.get("n_blobs"),
            "n_dats": st.get("n_dats"),
            "evidence": evidence,
        })

    findings.sort(key=lambda f: -f["score"])

    out = os.path.join(BASE, "index", "findings.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump(findings, f, indent=2, ensure_ascii=False)
    print(f"wrote {out}  ({len(findings)} flagged depots)")

    print("\n=== TOP 60 FLAGGED DEPOTS ===")
    for f in findings[:60]:
        ev = ", ".join(sorted(f["evidence"].get("keywords", {}).keys()))[:64]
        print(f"{f['depot']:>7}  score={f['score']:>3}  [{','.join(f['flags'])}]  "
              f"v{f['max_version']}  {f['label'][:42]}"
              + (f"  KW:{ev}" if ev else ""))

    con.close()


if __name__ == "__main__":
    main()

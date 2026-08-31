"""Discovery engine: flag "uncharted" / interesting depots for validation.

Signals (weighted):
  unlabeled          no curated name, or a placeholder name          +10
  valve_test_app     label is a ValveTestApp* internal test app      +15
  pre_release        label marks beta/demo/press/review/prototype    +5
  tier1 keyword      unreleased-project filename hit (ep3/fstop/…)   +25 each (capped)
  tier2 keyword      dev/cut marker filename hit (wip/unused/…)      +10 each (capped)
  cut_content        file present early, gone by final version       +8  (+20 if its name is tier1/tier2)
  content_mismatch   non-game label but game payload inside          +8

Outputs: index/findings.json (machine) and a ranked human summary on stdout.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "index", "steam2.db")

PLACEHOLDER = {"unknown / no depot", "unknown", "no depot", "n/a", "none", "-", "?", "--", ""}

TIER1 = re.compile(
    r"episode\s*3|episode_?3|/ep3/|ep3_|_ep3\b|ep3\b|"
    r"f-?stop|f_?stop|"
    r"weaponizer|"
    r"ice\s*gun|ice_?gun|"
    r"paint\s*gun|paint_?gun|"
    r"pre-?alpha|pre_?alpha|"
    r"prototype|proto_",
    re.IGNORECASE,
)

TIER2 = re.compile(
    r"\bwip\b|_wip|wip_|unused|unreleased|scrapped|cut_|_cut\b|"
    r"internal|devtest|placeholder|_old|old_|backup|draft|deleted",
    re.IGNORECASE,
)

GAME_EXT = re.compile(r"\.(bsp|vpk|map|vmf|vcd|exe|dll|nut|bsp2)$", re.IGNORECASE)
NON_GAME_LABEL = re.compile(
    r"trailer|video|movie|teaser|short|tv spot|commercial|intro|localiz", re.IGNORECASE
)
TEST_APP = re.compile(r"test\s*app|testapp|valvetest", re.IGNORECASE)
PRE_RELEASE = re.compile(
    r"beta|demo|press|review|preview|prototype|alpha|release candidate|internal build",
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

    # Streaming scan of every path -> per-depot keyword hits + ext histogram + game-ext count.
    hits = {}  # depot -> {kw -> [paths]}
    game_ext = {}  # depot -> count
    cut = {}  # depot -> {"total": n, "interesting": [paths]}

    for r in con.execute("SELECT depot,path,size,first_ver,last_ver FROM depot_paths"):
        depot = r["depot"]
        path = r["path"]
        p = path.lower()
        mx = stats.get(depot, {}).get("max_version") or 0

        d = hits.setdefault(depot, {})
        t1 = {m.group(0).lower() for m in TIER1.finditer(p)}
        t2 = {m.group(0).lower() for m in TIER2.finditer(p)}
        for kw in t1:
            d.setdefault(kw, []).append(path)
        for kw in t2:
            d.setdefault(kw, []).append(path)

        if GAME_EXT.search(p):
            game_ext[depot] = game_ext.get(depot, 0) + 1

        if mx and r["last_ver"] < mx:
            c = cut.setdefault(depot, {"total": 0, "interesting": []})
            c["total"] += 1
            if t1 or t2:
                c["interesting"].append(path)

    findings = []
    for depot, st in sorted(stats.items()):
        label = labels.get(depot, "")
        lab_low = label.lower()
        flags = []
        score = 0
        evidence = {}

        is_placeholder = label.strip().lower() in PLACEHOLDER or depot not in labels

        if is_placeholder:
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
        for kw, paths in d.items():
            is_t1 = TIER1.search(kw) is not None
            score += 25 if is_t1 else 10
            evidence.setdefault("keywords", {})[kw] = paths[:8]

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

    # Human summary
    print("\n=== TOP 60 FLAGGED DEPOTS ===")
    for f in findings[:60]:
        ev = ", ".join(sorted(f["evidence"].get("keywords", {}).keys()))[:60]
        print(f"{f['depot']:>7}  score={f['score']:>3}  [{','.join(f['flags'])}]  "
              f"v{f['max_version']}  {f['label'][:45]}"
              + (f"  KW:{ev}" if ev else ""))

    con.close()


if __name__ == "__main__":
    main()

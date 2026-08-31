"""Build the Games catalog: group depots by game, aggregate maps per game.

Outputs (site/dist/data/):
  games.json          — per-game summary (depots, versions, payload, map count)
  maps/<slug>.json    — per-game map list (path, size, depot(s), version range, cut status)
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import unicodedata

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "index", "steam2.db")
DIST = os.path.join(BASE, "site", "dist", "data")

# depots whose label lists multiple products (shared depot) get split across games
def primary_game(label: str) -> str | None:
    """Pick the most likely game name from a depot label."""
    if not label:
        return None
    # split multi-product labels and take the first game-ish title
    parts = [p.strip() for p in label.split(" / ")]
    for p in parts:
        if re.search(r"trailer|video|movie|teaser|dedicated|server|localiz|shared|authoring|add-on|sdk|dedicated", p, re.I):
            continue
        return p
    return parts[0] if parts else None


def slugify(name: str) -> str:
    name = unicodedata.normalize("NFKD", name)
    name = re.sub(r"[^a-zA-Z0-9]+", "-", name).strip("-").lower()
    return name or "unknown"


# well-known games -> canonical short slug + Steam appid for icon art
WELL_KNOWN = {
    "team fortress 2": ("tf", 440),
    "counter-strike source": ("css", 240),
    "counter-strike": ("cs16", 10),
    "counter-strike: condition zero": ("cz", 80),
    "counter-strike: condition zero deleted scenes": ("czds", 100),
    "half-life 2": ("hl2", 220),
    "portal 2": ("p2", 620),
    "portal": ("p1", 400),
    "left 4 dead 2": ("l4d2", 550),
    "left 4 dead": ("l4d1", 500),
    "half-life": ("hl1", 70),
    "day of defeat: source": ("dods", 300),
    "half-life 2: deathmatch": ("hl2dm", 320),
    "half-life 2: lost coast": ("hl2lc", 340),
    "alien swarm": ("as", 630),
    "counter-strike: global offensive": ("csgo", 730),
    "dota 2": ("dota", 570),
    "portal 2 authoring tools": ("p2at", 629),
    "source filmmaker": ("sfm", 1840),
    "deathmatch classic": ("dmc", 40),
    "day of defeat": ("dod", 30),
    "ricochet": ("ricochet", 60),
    "half-life: opposing force": ("opfor", 50),
    "half-life: blue shift": ("bshift", 130),
    "team fortress classic": ("tfc", 20),
}


def canonical(name: str):
    """Returns (canonical_slug, steam_appid) or (None, None)."""
    key = re.sub(r"\s+", " ", name.strip().lower())
    if key in WELL_KNOWN:
        return WELL_KNOWN[key]
    return (None, None)


MAP_PATH = re.compile(r"(?:^|/)maps/[^/]+\.bsp$", re.IGNORECASE)
NAV_PATH = re.compile(r"(?:^|/)maps/[^/]+\.(?:nav|res|lst)$", re.IGNORECASE)
# broad net for anything that looks like a map dir: <anything>/maps/<name>.bsp
MAP_PATH_BROAD = re.compile(r"maps/[^/]+\.bsp$", re.IGNORECASE)


def main() -> None:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row

    labels = {r["depot"]: r["label"] for r in con.execute("SELECT depot,label FROM labels")}

    # ---- game groups ----
    games: dict[str, dict] = {}
    depot_game: dict[int, str] = {}
    for r in con.execute(
        """SELECT depot,
                  MAX(CASE WHEN kind='blob' THEN version END) max_version,
                  COUNT(DISTINCT version) distinct_versions,
                  SUM(CASE WHEN kind='dat' THEN size END) dat_bytes,
                  MIN(date) first_date, MAX(date) last_date
           FROM files GROUP BY depot"""):
        depot = r["depot"]
        game = primary_game(labels.get(depot, ""))
        if not game:
            game = "(unidentified)"
        if game not in games:
            games[game] = {"depots": [], "dat_bytes": 0, "versions": 0,
                           "first_date": r["first_date"], "last_date": r["last_date"]}
        g = games[game]
        g["depots"].append(depot)
        g["dat_bytes"] += r["dat_bytes"] or 0
        g["versions"] += r["distinct_versions"]
        if r["first_date"] and r["first_date"] < g["first_date"]:
            g["first_date"] = r["first_date"]
        if r["last_date"] and r["last_date"] > g["last_date"]:
            g["last_date"] = r["last_date"]
        depot_game[depot] = game

    # ---- map census per game ----
    maps: dict[str, dict] = {}  # game -> {(path): map entry}
    for r in con.execute("SELECT depot, path, size, first_ver, last_ver FROM depot_paths"):
        p = r["path"]
        if not (MAP_PATH_BROAD.search(p) or NAV_PATH.search(p)):
            continue
        game = depot_game.get(r["depot"], "(unidentified)")
        m = maps.setdefault(game, {})
        e = m.setdefault(p, {"path": p, "size": 0, "depots": [],
                             "first_ver": r["first_ver"], "last_ver": r["last_ver"]})
        e["size"] = max(e["size"], r["size"])
        if r["depot"] not in e["depots"]:
            e["depots"].append(r["depot"])
        e["first_ver"] = min(e["first_ver"], r["first_ver"])
        e["last_ver"] = max(e["last_ver"], r["last_ver"])

    # ---- emit games.json ----
    os.makedirs(os.path.join(DIST, "maps"), exist_ok=True)

    # version->date per depot (for the game page timeline)
    vdates = {}
    for r in con.execute("SELECT depot, version, date FROM files WHERE kind='blob' ORDER BY depot, version"):
        vdates.setdefault(r["depot"], []).append([r["version"], r["date"][:10]])

    games_out = []
    for name, g in games.items():
        slug = slugify(name)
        game_maps = maps.get(name, {})
        cslug, appid = canonical(name)
        games_out.append({
            "game": name,
            "slug": slug,
            "icon": cslug or slug,
            "appid": appid,
            "depots": sorted(g["depots"]),
            "versions": g["versions"],
            "dat_bytes": g["dat_bytes"],
            "map_count": len(game_maps),
            "first_date": g["first_date"],
            "last_date": g["last_date"],
            "vdates": {str(d): vdates.get(d, []) for d in g["depots"]},
        })
        # per-game map file
        if game_maps:
            cut_count = 0
            entries = []
            for e in sorted(game_maps.values(), key=lambda x: x["path"]):
                is_bsp = e["path"].lower().endswith(".bsp")
                cut = not is_bsp  # nav/res sidecars counted separately
                entries.append({
                    "path": e["path"], "size": e["size"],
                    "depots": sorted(e["depots"]),
                    "first_ver": e["first_ver"], "last_ver": e["last_ver"],
                    "type": "bsp" if is_bsp else "sidecar",
                })
            with open(os.path.join(DIST, "maps", f"{slug}.json"), "w", encoding="utf-8") as f:
                json.dump({"game": name, "slug": slug, "maps": entries,
                           "bsp_count": sum(1 for e in entries if e["type"] == "bsp"),
                           "sidecar_count": sum(1 for e in entries if e["type"] != "sidecar")},
                          f, separators=(",", ":"), ensure_ascii=False)

    games_out.sort(key=lambda g: -g["map_count"])
    # vdates bloat: ship only for games with maps (the interactive pages)
    slim = []
    for g in games_out:
        if g["map_count"]:
            slim.append(g)
        else:
            slim.append({k: v for k, v in g.items() if k != "vdates"})
    with open(os.path.join(DIST, "games.json"), "w", encoding="utf-8") as f:
        json.dump(slim, f, separators=(",", ":"), ensure_ascii=False)

    print(f"games: {len(games_out)}  with maps: {sum(1 for g in games_out if g['map_count'])}")
    for g in games_out[:8]:
        print(f"  {g['game'][:40]:42} maps={g['map_count']:4} depots={len(g['depots'])}")
    con.close()


if __name__ == "__main__":
    main()

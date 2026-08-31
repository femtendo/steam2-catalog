"""MOCKUP v2 — tile-based Games view with icon squares per game.

Same projected end-of-tonight data, rendered as square tiles instead of a table.
Generates site/dist/mockup.html
"""
from __future__ import annotations

import os

BASE = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(BASE, "site", "dist")

MOCK_GAMES = [
    ("Team Fortress 2", 10, 397, 91, "12.1 GiB", "2007–2013", "tf", 96.2, 32),
    ("Portal 2 (+ beta)", 18, 11, 96, "14.6 GiB", "2011–2012", "p2", 95.8, 28),
    ("Postal 3", 4, 12, 87, "5.5 GiB", "2011–2012", "p3", 88.6, 12),
    ("Counter-Strike 1.6 / CZ", 22, 49, 61, "1.9 GiB", "2003–2013", "cs16", 98.1, 34),
    ("Duke Nukem Forever", 25, 29, 67, "9.8 GiB", "2011–2012", "dnf", 90.2, 14),
    ("Counter-Strike: Source", 14, 61, 68, "3.8 GiB", "2004–2013", "css", 97.0, 30),
    ("Half-Life 2", 45, 56, 54, "6.2 GiB", "2003–2013", "hl2", 97.4, 36),
    ("Portal", 8, 40, 41, "2.1 GiB", "2007–2013", "p1", 96.9, 26),
    ("Left 4 Dead 2", 12, 63, 39, "5.7 GiB", "2009–2013", "l4d2", 94.3, 24),
    ("Left 4 Dead", 9, 33, 27, "4.1 GiB", "2008–2012", "l4d1", 95.1, 20),
    ("Half-Life (GoldSrc)", 11, 57, 38, "0.9 GiB", "2003–2013", "hl1", 97.7, 22),
    ("Day of Defeat: Source", 6, 44, 24, "2.4 GiB", "2005–2012", "dods", 96.0, 16),
    ("HL2: Deathmatch", 4, 21, 22, "1.4 GiB", "2004–2012", "hl2dm", 96.5, 14),
    ("Alien Swarm", 3, 9, 14, "1.1 GiB", "2010–2012", "as", 97.2, 10),
    ("CS:GO (beta)", 5, 12, 11, "3.3 GiB", "2011–2012", "csgo", 91.4, 12),
    ("Dota 2 (beta)", 4, 31, 6, "5.9 GiB", "2011–2013", "dota", 92.0, 10),
]

COLORS = {
    "tf": "#d98c4a", "css": "#5a8fd9", "cs16": "#c8a44a", "hl2": "#e07830",
    "p2": "#5aaee0", "p1": "#7ab5e0", "l4d2": "#b8452e", "l4d1": "#a03d28",
    "hl1": "#e09030", "dods": "#7a8c5a", "hl2dm": "#d87840", "as": "#5ab58a",
    "csgo": "#d9a44a", "dota": "#a04030", "p3": "#c05050", "dnf": "#b0a040",
}

GLYPHS = {
    "tf": " wrench", "p2": "◉", "p3": "✶", "cs16": "✚", "dnf": "☉",
    "css": "✚", "hl2": "λ", "p1": "◉", "l4d2": "☠", "l4d1": "☠",
    "hl1": "λ", "dods": "★", "hl2dm": "λ", "as": "🐜", "csgo": "✚", "dota": "⚔",
}


def monogram(name: str) -> str:
    words = [w for w in name.replace("(", " ").replace(":", " ").split() if len(w) > 1 and not w.isdigit()]
    if not words:
        return name[:2].upper()
    if len(words) == 1:
        return words[0][:2].upper()
    return (words[0][0] + words[1][0]).upper()


def main() -> None:
    tiles = []
    for name, depots, vers, maps, size, span, slug, pct, n_files in MOCK_GAMES:
        color = COLORS.get(slug, "#6f9fc8")
        glyph = GLYPHS.get(slug, "")
        tiles.append(f"""
      <div class="tile" onclick="void(0)">
        <div class="icon" style="background:linear-gradient(135deg,{color},{color}bb)">
          <span class="glyph">{glyph.strip() or monogram(name)}</span>
          <span class="cut" title="maps cut before final release">✂ 3</span>
        </div>
        <div class="tname">{name}</div>
        <div class="tmeta">{maps} maps · {depots} depots</div>
        <div class="tmeta2">{vers} versions · {size}</div>
        <div class="tbar"><div class="fill" style="width:{pct}%"></div></div>
        <div class="tver">{pct}% verified</div>
      </div>""")

    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><title>Games tiles — mockup</title>
<style>
* {{ box-sizing: border-box; }}
body {{ margin:0; background:#12151a; color:#d7dee8; font:14px/1.5 -apple-system,'Segoe UI',Roboto,sans-serif; }}
.badge-top {{ background:#1c3a24; color:#7fd08f; padding:8px 20px; font-size:13px; border-bottom:1px solid #2c5a3a; }}
header {{ background:#1a1f27; border-bottom:1px solid #2c3644; padding:14px 20px; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:10px; }}
h1 {{ font-size:18px; margin:0; }}
.dim {{ color:#7d8a99; }}
.vb {{ background:#202733; border:1px solid #2c3644; border-radius:8px; padding:6px 14px; display:flex; align-items:baseline; gap:8px; }}
.vb .pct {{ font-size:20px; font-weight:700; color:#5aa469; }}
.vb .lbl {{ font-size:11px; color:#7d8a99; }}
.wrap {{ max-width:1150px; margin:0 auto; padding:10px 20px; }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(190px,1fr)); gap:14px; margin-top:12px; }}
.tile {{ background:#1a1f27; border:1px solid #2c3644; border-radius:10px; padding:12px; cursor:pointer; transition:border-color .12s; }}
.tile:hover {{ border-color:#6f9fc8; transform:translateY(-2px); }}
.icon {{ position:relative; width:100%; aspect-ratio:1; border-radius:8px; display:flex; align-items:center; justify-content:center; margin-bottom:10px; }}
.glyph {{ font-size:44px; color:#12151acc; text-shadow:0 1px 0 #ffffff22; }}
.cut {{ position:absolute; top:6px; right:6px; background:#12151acc; color:#d98c4a; font-size:10px; padding:2px 6px; border-radius:8px; }}
.tname {{ font-weight:600; font-size:13px; line-height:1.3; min-height:34px; }}
.tmeta {{ color:#7d8a99; font-size:11.5px; margin-top:4px; }}
.tmeta2 {{ color:#7d8a99; font-size:11px; margin-top:2px; }}
.tbar {{ height:4px; background:#202733; border-radius:2px; margin-top:8px; overflow:hidden; }}
.fill {{ height:100%; background:#5aa469; border-radius:2px; }}
.tver {{ color:#5aa469; font-size:10.5px; margin-top:4px; }}
.legend {{ margin-top:16px; color:#7d8a99; font-size:12px; }}
</style></head><body>
<div class="badge-top">MOCKUP PREVIEW — projected end-of-tonight state · tile layout v2</div>
<header>
  <h1>Steam2 Archive <span class="dim">Catalog</span></h1>
  <div class="vb"><span class="pct">94.7%</span><span class="lbl">54,530 / 57,587 versions verified</span></div>
</header>
<div class="wrap">
<h2>Games <span class="dim">— tile view, sorted by map count</span></h2>
<div class="grid">{''.join(tiles)}
</div>
<div class="legend">
✂ = cut maps present (existed in early versions, removed before final) ·
green bar = per-game verification % ·
icon tiles are generated per game (monogram/glyph on its color) — swap for real art later.
</div>
</div></body></html>"""

    out = os.path.join(DIST, "mockup.html")
    with open(out, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()

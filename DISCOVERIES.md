# Discoveries

Working notes on unreleased / cut / otherwise notable content found in the Steam2
archive. Every entry cites the exact depot, version, and file path so it can be
reproduced independently.

Method: the archive's `.blob` files are parsed (no decryption key required) and
their file manifests — the only place that carries human-readable names — are
indexed and searched. No `.dat` payloads are extracted unless noted. Status per
entry: `manifest-verified` (paths confirmed in the archive) → `payload-verified`
(files extracted and confirmed).

The full index covers 57,581 of 57,587 depot versions (99.99%) and 11.8M distinct
file paths across 10,876 depots. 2,040 depots are auto-flagged for review; the
entries below are the highlights.

---

## 1. The Portal 2 beta complex — F-Stop era (depots 841, 843, 645, 852)

**Status:** manifest-verified. The single largest unreleased-content finding in
the archive.

Four depots preserve the F-Stop era of Portal 2 development (2009–2011), before
the game was rebuilt around portals:

| Depot | Label | Versions | File paths | Maps (.bsp) | Cut files |
|---|---|---|---|---|---|
| 841 | Portal 2 | 41 (v0–v40) | 120,701 | 501 | 117,237 |
| 852 | ValveTestApp852 | 11 (v0–v10) | 72,309 | 502 | 67,954 |
| 645 | Portal 2 | 11 (v0–v10) | 10,116 | 105 | 2,987 |
| 843 | Portal 2 | 2 (v0–v1) | 4,376 | 165 | 196 |

**F-Stop / Paint Gun evidence** (127 paths across 841 + 852):
- `portal2/scripts/game_sounds_weapons_paintgun.txt` — the Paint Gun weapon script
- `portal2/scripts/weapon_paintgun.txt` — weapon definition
- `portal2/cfg/paintgun_bindings.cfg` — key bindings
- `portal2/materials/models/props_fstop/…` — dollhouse01–04, tombstone001,
  instruction_manual_scrap01–05 materials
- `portal2_tempcontent/models/props_fstop/instruction_manual_scrap01.*` —
  compiled Source models (.mdl/.vvd/.phy/.vtx)

The Paint Gun was F-Stop's core mechanic: photographing objects to move them
between miniature dioramas (the dollhouse props). Valve publicly confirmed the
F-Stop prototype existed; these depots contain its actual scripts, configs, and
compiled props.

**Depot 852** also carries `bin/ep3.fgd` (Half-Life 2: Episode Three Hammer
entity definitions) and `bin/unusedcontent.exe` / `portal/unusedcontent.bat` —
Valve's own unused-content tooling.

**Cut maps:** 501 maps in depot 841 and 502 in 852 existed at some version and
were gone by the final one — the early Portal 2 test maps.

---

## 2. CS:GO beta (depots 711, 712)

**Status:** manifest-verified.

- Depot 712 (cstrike15 content): 141,724 paths, **64 maps**, 15 Episode-3-adjacent
  hits — the 2011-era CS:GO prototypes shipped through Steam2.
- Depot 711 (engine): 12,811 paths.

The CS:GO that launched in 2012 was a very different game from the 2011 prototype
builds these depots preserve.

---

## 3. Source Filmmaker cut content (depot 1841)

**Status:** manifest-verified.

61 cut maps and extensive dev-marker files across 12 versions of the internal
SFM builds — the tool Valve used for "Meet the Team" was itself iterating on
unreleased material.

---

## 4. TF2 full update history (depots 441–452, 522–527)

**Status:** manifest-verified.

- Depot 441: 397 versions (v0 = October 2007 beta window → v396), 65 distinct
  maps, 2 confirmed cut (`background01.bsp`, removed after v4; `item_test.bsp`,
  a dev-test map present v197–v225).
- The 2008 TF2 Beta branch (depots 522–527) preserves the beta-content pipeline.

---

## 5. ValveTestApp700 (depot 701)

**Status:** manifest-verified.

87 cut maps across 13 versions — an unidentified Valve internal test app with
heavy prototype content. Label candidates welcome via the issue tracker.

---

_More entries are added as flagged depots are manually verified. 2,040 depots
are currently auto-flagged; every confirmed entry needs exact paths and, ideally,
payload verification._

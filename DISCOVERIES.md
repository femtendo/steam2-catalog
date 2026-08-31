# Discoveries

Working notes on unreleased / cut / otherwise notable content found in the Steam2
archive. Every entry cites the exact depot, version, and file path so it can be
reproduced independently.

Method: the archive's `.blob` files are parsed (no decryption key required) and
their file manifests — the only place that carries human-readable names — are
indexed and searched. No `.dat` payloads are extracted.

---

## 1. F-Stop asset leftovers in a Portal 2 test build (depot 852)

**Depot:** 852 — labeled `ValveTestApp852`, a Valve internal test app.
**Version:** 0 (18,173 files), dated 2011-07-30.
**Build roots:** `bin/`, `hl2/`, `platform/`, `portal/`, `portal2/`,
`portal2_tempcontent/`, `hl2.exe`.

This is a Portal 2 test build that still ships a `portal2_tempcontent/` tree
containing **F-Stop development assets** — the canceled prequel concept Valve
abandoned in favor of Portal 2's final mechanics.

Notable files (all under depot 852, version 0):

F-Stop prop models (compiled Source models — `.mdl`/`.vvd`/`.phy`/`.vtx`):
- `portal2_tempcontent/models/props_fstop/instruction_manual_scrap01.{mdl,phy,vvd,sw.vtx,dx80.vtx,dx90.vtx}`
- `portal2/materials/models/props_fstop/dollhouse01.{vmt,vtf}` … `dollhouse04.{vmt,vtf}` (+ `dollhouse04_detail`)
- `portal2/materials/models/props_fstop/instruction_manual_scrap01..05.{vmt,vtf}`
- `portal2/materials/models/props_fstop/tombstone001.{vmt,vtf}`
- `portal2/materials/models/props_fstop/grey.vtf`

The "dollhouse" props are direct evidence of F-Stop's diorama/capture mechanic —
miniature scenes photographed by the player camera — which Valve removed when the
project pivoted to portals.

Other development leftovers in the same build:
- `bin/ep3.fgd` — an FGD (Hammer editor entity definition) named `ep3`, i.e.
  Half-Life 2: Episode 3 entity definitions.
- `bin/unusedcontent.exe`, `portal/unusedcontent.bat` — tooling explicitly named
  "unused content".
- `portal/sound/vo/aperture_ai/file_deleted.wav` — a voice line literally named
  `file_deleted`.

**Status:** verified by manifest parse; asset extraction for visual confirmation
not yet performed.

---

_More entries are added as the full manifest index completes._

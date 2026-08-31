# Steam2 Archive Catalog

**A searchable catalog of every game and build in the 2016-era Steam2 content-server archive — focused on Valve's Source engine games — with cut-content discovery and downloadable map bundles.**

In August 2026 a ~12 TB archive of Valve's old Steam2 content servers surfaced
online. It spans 2003–2013 and contains **116,339 files across 10,876 depots** —
every game Valve and its partners shipped through Steam's first delivery system,
including builds that were never released.

This project makes that archive *navigable*. Instead of a 12 TB wall of
`0_0_1b04cb6e_….dat` files, you get a searchable index: every depot, every
version, every file path — plus a discovery engine that flags unreleased and cut
content, and per-game bundles of extracted maps you can actually play.

---

## What you can do here

| I want to… | Do this |
|---|---|
| Find out what's in the archive | **Browse** — search all 10,876 depots by name or id, no install needed |
| See what a specific game shipped | Open its **depot page** — full version history and complete file list |
| Hunt for unreleased / cut content | Check **Discoveries** — automatically flagged depots with cited evidence |
| Play recovered maps | Grab a **bundle zip** for a game — verified map files, ready to run |
| Do your own research | Clone this repo and run the pipeline yourself (below) |

## Scope: Source-engine Valve games

The archive covers every Steam2 publisher, but this project focuses on
**Valve's Source engine catalog** — Half-Life 2 and its Episodes, Portal and
Portal 2, Counter-Strike: Source and CS:GO, Team Fortress 2, Left 4 Dead 1 & 2,
Day of Defeat: Source, Alien Swarm, and the internal builds around them
(ValveTestApps, betas, press demos). These are the depots where unreleased
content is concentrated and where the community's interest is highest.
Third-party games stay in the index — they're searchable like everything else —
but bundles and deep-dive discoveries prioritize Valve titles.

## The 60-second version

Steam2 stored every game as a chain of **`.dat` payload files** (the actual game
data) with a small **`.blob` metadata file** beside each one. The blob holds the
file manifest — every path, size, and hash in that version — and it needs **no
decryption key** to read.

That means the entire archive's *contents listing* is only ~16 GB, not 12 TB.
This project downloads just the blobs, parses every manifest, and builds:

1. a **SQLite index** — 116k files, version history, per-depot file paths,
2. a **discovery report** — depots flagged for beta content, cut files,
   unreleased-project markers (F-Stop, Episode 3, …),
3. a **static website** anyone can search,
4. **map bundles** — extracted, verified `.bsp` maps per game, zipped and
   ready to play.

No game files are hosted here. Download links point at the community mirrors.

## Quick start

Run the whole pipeline yourself. Nothing but Python 3 is required.

```sh
# 1. fetch the archive indexes (small)
curl -o index/dats_dates.txt  https://de.steam2.download/dats_dates.txt
curl -o index/blobs_dates.txt https://de.steam2.download/blobs_dates.txt
curl -o index/dats_listing.html  https://de.steam2.download/dats/
curl -o index/blobs_listing.html https://de.steam2.download/blobs/
curl -o depot_labels.tsv \
  https://raw.githubusercontent.com/dr3murr/steam2-winfsp/refs/heads/main/data/depot_labels.tsv

# 2. build the catalog database
python3 build_catalog.py

# 3. download all blob metadata (~16 GB, resumable)
python3 download_blobs.py

# 4. index every manifest
python3 index_blobs.py

# 5. discovery pass — flags depots worth a closer look
python3 find_uncharted.py

# 6. build map bundles for Source games
python3 build_bundles.py

# 7. generate the static site
python3 build_site.py
```

Every step resumes cleanly. Step 3 is the only long one (~16 GB); everything
else runs in minutes.

## How a depot gets flagged

`find_uncharted.py` reads every indexed file path and scores each depot on:

- **Unlabeled or placeholder names** — depots the community hasn't identified yet
- **Internal Valve test apps** — `ValveTestApp*` depots, which often hold prototypes
- **Pre-release labels** — betas, demos, press builds
- **Unreleased-project markers in file paths** — `fstop`, `weaponizer`,
  `episode3`, `ep3.fgd`, `prototype`, `unused` …
- **Cut content** — files that existed in early versions and were deleted before
  the final one
- **Label-vs-content mismatches** — a "trailer" depot containing game data

Every flag cites exact depot ids, versions, and file paths, so any claim can be
verified against the mirrors in minutes. Confirmed finds are written up in
[DISCOVERIES.md](DISCOVERIES.md).

## The site

`site/dist/` is fully static — open it with any file server, no backend:

- **Browse** — every depot, searchable by id or name
- **Depot pages** — version history, complete file manifest, mirror download links
- **Discoveries** — ranked flag list with evidence paths
- **Bundles** — per-game map zips with file counts, sizes, and build dates

> ⚠️ **Safety first:** bundles contain unreleased or cut game content from
> 2003–2013. These files were never quality-controlled by anyone — they may be
> unstable, incomplete, or unsafe. **Extract and run them only inside an
> isolated virtual machine.** This site hosts metadata; game files themselves
> come from community-run mirrors.

## Project layout

```
s2_parse.py         blob container + manifest reader (no decryption key needed)
build_catalog.py    SQLite catalog from the archive indexes
download_blobs.py   resumable, sha256-verified blob downloader with mirror failover
index_blobs.py      parses every manifest into per-depot paths + version ranges
find_uncharted.py   discovery engine — flags depots worth a closer look
build_bundles.py    extracts and verifies maps, zips them per game
build_site.py       generates the static site from the index
site/               the site frontend (single page, no backend)
DISCOVERIES.md      write-ups of confirmed finds, with reproducible citations
```

## Attribution

- **Archive**: the TeraRelease Steam2 content-server dump and its public mirrors.
- **Depot names**: the curated `depot_labels.tsv` from
  [dr3murr/steam2-winfsp](https://github.com/dr3murr/steam2-winfsp) — dedicated work this project builds on.
- **Format knowledge**: the blob/manifest reader here is an independent
  implementation; [extremebleem/steam2_downloader](https://github.com/extremebleem/steam2_downloader)
  was used as a format reference.

## Contributing

Issues and PRs welcome, especially:

- **Label fixes** — know what an unnamed depot is? Open a PR against
  `depot_labels.tsv` (or upstream's).
- **New discoveries** — flag something interesting? Add it to `DISCOVERIES.md`
  with depot id, version, and exact file paths.
- **Bundle improvements** — better map filtering, more sidecar files (`.nav`,
  `.res`), per-build zips.

## Legal

This project publishes **metadata only** — depot names, file paths, sizes,
dates, and sha256 hashes. It does not host, redistribute, or extract `.dat`
payloads, and no copyrighted game files are stored in this repository. Download
links point to third-party mirrors; bundle downloads are generated locally by
running the pipeline yourself. Game content remains the property of its
respective owners — use the archive in accordance with your local law.

See the safety notice above before running anything extracted from the archive.

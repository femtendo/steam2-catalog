# Steam2 Archive Catalog

An index and discovery tool for the **Steam2 content-server archive** — Valve's
pre-SteamPipe content delivery system, spanning roughly 2003–2013. The archive
contains **116,339 files across 10,876 depots**: ~12 TB of `.dat` payloads plus
~16 GB of `.blob` metadata (file manifests).

This project catalogs the **metadata only**. It downloads the `.blob` manifests,
parses the file tree inside every depot version, and builds a searchable index —
without touching a single `.dat` payload. The manifest layer carries every
human-readable file path and needs no decryption key.

## Why

Working out *what* is in a Steam2 depot by hand is impractical: depots are stored
as delta chains of `.dat` payloads with `.blob` metadata beside them, and a single
depot can span hundreds of versions. The blob manifest is the only place that
carries human-readable names, and it is readable without any key. That makes a
full-archive content index achievable at ~16 GB instead of ~12 TB.

## Components

| File | Purpose |
|------|---------|
| `s2_parse.py`       | Minimal reader for the `.blob` container and embedded file manifest |
| `build_catalog.py`  | Builds the SQLite catalog (files, sizes, dates, depot names) from the archive indexes |
| `download_blobs.py` | Downloads all `.blob` metadata (resumable, sha256-verified, mirror failover) |
| `index_blobs.py`    | Parses every blob manifest into per-depot file paths + version ranges |
| `find_uncharted.py` | Discovery pass: flags unlabeled / test-app / pre-release depots, unreleased-project keyword hits, cut content, and label-vs-content mismatches |
| `build_site.py`     | Generates a static catalog website from the SQLite index |

No third-party Python packages are required — everything runs on the standard
library.

## Pipeline

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

# 3. download all blob metadata (~16 GB)
python3 download_blobs.py

# 4. index every manifest
python3 index_blobs.py

# 5. discovery pass
python3 find_uncharted.py

# 6. generate the static site
python3 build_site.py
```

## Site

The generated site (`site/dist/`) is fully static and hosts no game files:

- **Browse** every depot by id or name.
- **Depot detail** shows the version history, the complete file manifest, and
  download links that point at the public community mirrors.
- **Discoveries** lists auto-flagged depots worth a closer look, each with
  reproducible depot ids and file paths.

## Attribution

- Archive: the **TeraRelease** Steam2 content-server dump and its public mirrors.
- Depot names: the curated `depot_labels.tsv` published in the
  [steam2-winfsp](https://github.com/dr3murr/steam2-winfsp) project.
- The blob/manifest layout is a from-scratch implementation of the documented
  Steam2 container format; the
  [steam2_downloader](https://github.com/extremebleem/steam2_downloader) source was
  used as a reference.

## Legal

This project indexes and publishes **metadata** — depot names, file paths, sizes,
dates, and sha256 hashes — not copyrighted game content. It does not host,
redistribute, or extract `.dat` payloads. Download links point to third-party
mirrors. Use the archive in accordance with your local law.

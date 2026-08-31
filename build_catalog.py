"""Build the Steam2 catalog database (files + labels + per-depot stats) into SQLite.

Inputs (already fetched into index/):
    dats_dates.txt, blobs_dates.txt   -> filename<TAB>date
    dats_listing.html, blobs_listing.html -> nginx autoindex (sizes)
    depot_labels.tsv                  -> curated depot names

Output: index/steam2.db
"""
from __future__ import annotations

import html as htmllib
import os
import re
import sqlite3

BASE = os.path.dirname(os.path.abspath(__file__))
IDX = os.path.join(BASE, "index")
DB = os.path.join(IDX, "steam2.db")


def human_size(s: str) -> int:
    s = s.strip()
    if s in ("-", ""):
        return -1
    m = re.match(r"([\d.]+)\s*(B|KiB|MiB|GiB|TiB)?", s)
    if not m:
        return -1
    v = float(m.group(1))
    unit = m.group(2) or "B"
    mul = {"B": 1, "KiB": 1024, "MiB": 1024 ** 2, "GiB": 1024 ** 3, "TiB": 1024 ** 4}[unit]
    return int(v * mul)


def parse_listing(path: str):
    data = open(path, encoding="utf-8", errors="replace").read()
    rows = re.findall(r'<td class="link"><a href="([^"]+)".*?</a></td><td class="size">([^<]*)</td>', data)
    out = {}
    for name, size in rows:
        if name.endswith("/") or name in ("../",):
            continue
        name = htmllib.unescape(name)
        out[name] = human_size(size)
    return out


def parse_name(filename: str):
    stem = filename.rsplit(".", 1)[0]
    parts = stem.split("_")
    if len(parts) != 4:
        return None
    depot, version, crc, sha = parts
    if not depot.isdigit() or not version.isdigit():
        return None
    if len(crc) != 8 or len(sha) != 64:
        return None
    return int(depot), int(version), crc, sha


def load_dates(path: str, kind: str):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            filename, date = line.split("\t", 1)
            info = parse_name(filename)
            if info is None:
                continue
            depot, version, crc, sha = info
            rows.append((depot, version, crc, sha, kind, date.strip()))
    return rows


def main() -> None:
    if os.path.exists(DB):
        os.remove(DB)
    con = sqlite3.connect(DB)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("""CREATE TABLE files(
        depot INTEGER, version INTEGER, crc TEXT, sha256 TEXT,
        kind TEXT, date TEXT, size INTEGER,
        PRIMARY KEY(kind, depot, version, crc))""")
    con.execute("""CREATE TABLE labels(depot INTEGER PRIMARY KEY, label TEXT)""")

    # Sizes
    blob_sizes = parse_listing(os.path.join(IDX, "blobs_listing.html"))
    dat_sizes = parse_listing(os.path.join(IDX, "dats_listing.html"))

    all_rows = []
    all_rows += load_dates(os.path.join(IDX, "blobs_dates.txt"), "blob")
    all_rows += load_dates(os.path.join(IDX, "dats_dates.txt"), "dat")

    sized = []
    for depot, version, crc, sha, kind, date in all_rows:
        fname = f"{depot}_{version}_{crc}_{sha}.{kind}"
        size = blob_sizes.get(fname, -1) if kind == "blob" else dat_sizes.get(fname, -1)
        sized.append((depot, version, crc, sha, kind, date, size))

    con.executemany("INSERT INTO files VALUES(?,?,?,?,?,?,?)", sized)

    # Labels
    labels = []
    with open(os.path.join(BASE, "depot_labels.tsv"), encoding="utf-8") as f:
        for line in f:
            line = line.rstrip("\n")
            if not line.strip():
                continue
            if "\t" not in line:
                continue
            depot_s, label = line.split("\t", 1)
            if not depot_s.strip().isdigit():
                continue
            labels.append((int(depot_s.strip()), label.strip()))
    con.executemany("INSERT INTO labels VALUES(?,?)", labels)

    con.commit()

    # Stats
    n_files = con.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    n_blobs = con.execute("SELECT COUNT(*) FROM files WHERE kind='blob'").fetchone()[0]
    n_dats = con.execute("SELECT COUNT(*) FROM files WHERE kind='dat'").fetchone()[0]
    n_depots = con.execute("SELECT COUNT(DISTINCT depot) FROM files").fetchone()[0]
    n_labeled = con.execute("SELECT COUNT(*) FROM labels").fetchone()[0]
    blob_bytes = con.execute("SELECT COALESCE(SUM(size),0) FROM files WHERE kind='blob'").fetchone()[0]
    dat_bytes = con.execute("SELECT COALESCE(SUM(size),0) FROM files WHERE kind='dat'").fetchone()[0]

    print(f"depots={n_depots} files={n_files} (blobs={n_blobs} dats={n_dats})")
    print(f"labels={n_labeled}")
    print(f"blob metadata total={blob_bytes/1024**3:.2f} GiB")
    print(f"dat payload total  ={dat_bytes/1024**4:.2f} TiB")
    print(f"wrote {DB}")
    con.close()


if __name__ == "__main__":
    main()

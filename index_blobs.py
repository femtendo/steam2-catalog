"""Index every downloaded blob: parse manifests, store per-version summaries and
per-depot file paths (with first/last-version range) into index/steam2.db.

depot_paths holds the union of every file path each depot ever shipped, plus the
version range in which it existed. That makes two discovery queries trivial:
  * cut content   -> paths whose last_ver < the depot's max version
  * new content   -> paths whose first_ver > 0
and a keyword search over paths needs no .dat download at all.
"""
from __future__ import annotations

import os
import re
import sqlite3
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import s2_parse  # noqa: E402

BASE = os.path.dirname(os.path.abspath(__file__))
BLOBS = os.path.join(BASE, "blobs")
DB = os.path.join(BASE, "index", "steam2.db")

NAME_RE = re.compile(r"^(\d+)_(\d+)_[0-9a-f]{8}_[0-9a-f]{64}\.blob$")

BATCH = 2000


def iter_blobs():
    for fn in sorted(os.listdir(BLOBS)):
        if fn.endswith(".blob"):
            m = NAME_RE.match(fn)
            if m:
                yield int(m.group(1)), int(m.group(2)), fn


def main() -> None:
    con = sqlite3.connect(DB)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("""CREATE TABLE IF NOT EXISTS versions(
        depot INTEGER, version INTEGER, app_id INTEGER, ver_id INTEGER,
        node_count INTEGER, file_count INTEGER, roots TEXT,
        PRIMARY KEY(depot, version))""")
    con.execute("""CREATE TABLE IF NOT EXISTS depot_paths(
        depot INTEGER, path TEXT, size INTEGER,
        first_ver INTEGER, last_ver INTEGER,
        PRIMARY KEY(depot, path)) WITHOUT ROWID""")

    indexed = {r[0] for r in con.execute("SELECT depot||':'||version FROM versions")}

    t0 = time.time()
    n = 0
    n_parsed = 0
    n_paths = 0
    vbuf = []
    pbuf = []

    def flush():
        nonlocal vbuf, pbuf
        if vbuf:
            con.executemany(
                "INSERT OR IGNORE INTO versions VALUES(?,?,?,?,?,?,?)", vbuf)
            vbuf = []
        if pbuf:
            con.executemany(
                """INSERT INTO depot_paths(depot,path,size,first_ver,last_ver)
                   VALUES(?,?,?,?,?)
                   ON CONFLICT(depot,path) DO UPDATE SET
                     size=max(size, excluded.size),
                     first_ver=min(first_ver, excluded.first_ver),
                     last_ver=max(last_ver, excluded.last_ver)""", pbuf)
            pbuf = []

    for depot, version, fn in iter_blobs():
        n += 1
        key = f"{depot}:{version}"
        if key in indexed:
            continue

        data = open(os.path.join(BLOBS, fn), "rb").read()
        try:
            m = s2_parse.manifest_from_blob(data)
        except Exception:
            m = None
        if m is None:
            vbuf.append((depot, version, None, None, 0, 0, ""))
        else:
            n_parsed += 1
            vbuf.append((depot, version, m.app_id, m.ver_id,
                         m.node_count, m.file_count, "\x1f".join(m.roots)))
            for node in m.file_nodes():
                pbuf.append((depot, node.path, node.size, version, version))
                n_paths += 1
        del data

        if len(vbuf) >= BATCH or len(pbuf) >= BATCH:
            flush()
        if n % 2000 == 0:
            flush()
            con.commit()
            rate = n / max(time.time() - t0, 1e-9)
            print(f"  [{n}] parsed={n_parsed} path-rows={n_paths} ({rate:.0f}/s)", flush=True)

    flush()
    con.commit()

    tot = con.execute("SELECT COUNT(*) FROM depot_paths").fetchone()[0]
    nver = con.execute("SELECT COUNT(*) FROM versions").fetchone()[0]
    print(f"DONE blobs={n} versions={nver} distinct_paths={tot} "
          f"({time.time()-t0:.0f}s)", flush=True)
    con.close()


if __name__ == "__main__":
    main()

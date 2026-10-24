"""Build a disposable, local-only catalog using generated metadata (no game files).

Usage: python tests/integration_demo.py --output NEW_DIRECTORY [--interrupt-check] [--serve]
All database, input and output paths are redirected into a new marked sandbox.
"""
from __future__ import annotations

import argparse
import hashlib
import http.server
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from functools import partial
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import build_site
import build_vfiles
import index_blobs
from test_phase9_e2e import _manifest, _source_name

MARKER = "SYNTHETIC_STEAM2_FIXTURE_ONLY"


def verified_sidecar(source: Path, target: Path) -> dict:
    """Rebuild a separate validated union; never infer from legacy depot_paths."""
    if target.exists():
        raise ValueError("verified index target must be new (keep prior snapshots for rollback)")
    coverage = {}
    with closing(sqlite3.connect(source)) as src, closing(sqlite3.connect(target)) as dst:
        src.row_factory = sqlite3.Row
        dst.executescript("""
            CREATE TABLE sources(depot,version,source_blob_name,sha256,parser_version,
                PRIMARY KEY(depot,version,source_blob_name));
            CREATE TABLE observations(depot,version,source_blob_name,ordinal,path,size,file_id,flags,
                PRIMARY KEY(depot,version,source_blob_name,ordinal));
            CREATE TABLE coverage(depot PRIMARY KEY,known_versions,exact_versions,state);
            CREATE VIEW paths AS SELECT depot,path,MAX(size) AS size,
                MIN(version) AS first_ver,MAX(version) AS last_ver,COUNT(*) AS observations
                FROM observations GROUP BY depot,path;
            CREATE VIEW complete_paths AS SELECT p.* FROM paths p JOIN coverage c USING(depot)
                WHERE c.state='complete_for_known_inventory';
        """)
        with dst:
            for depot in build_vfiles._depot_ids(src):
                versions, metadata, sources, _ = build_vfiles._version_context(src, depot)
                state = ("complete_for_known_inventory" if versions and len(versions) == len(sources)
                         else "partial")
                coverage[depot] = {"known": len(versions), "exact": len(sources), "state": state}
                dst.execute("INSERT INTO coverage VALUES(?,?,?,?)",
                            (depot, len(versions), len(sources), state))
                for version, name in sources.items():
                    meta = metadata[str(version)]
                    dst.execute("INSERT INTO sources VALUES(?,?,?,?,?)",
                                (depot, version, name, meta["source_blob_sha256"], meta["parser_version"]))
                    dst.executemany("INSERT INTO observations VALUES(?,?,?,?,?,?,?,?)", src.execute(
                        "SELECT depot,version,source_blob_name,record_ordinal,path,size,file_id,flags "
                        "FROM version_file_records WHERE depot=? AND version=? AND source_blob_name=?",
                        (depot, version, name)))
    return coverage


def _interrupt(database: Path, blobs: Path, after_commit: bool) -> dict:
    # Terminate a child inside the actual indexer transaction, then reopen SQLite.
    code = """
import os,sys
sys.path.insert(0,sys.argv[1])
import index_blobs
index_blobs.DB=sys.argv[2]; index_blobs.BLOBS=sys.argv[3]; index_blobs.BATCH=1
original=index_blobs._store_prepared
def interrupted(con,prepared):
    original(con,prepared)
    if sys.argv[4]=='commit': con.commit()
    os._exit(91)
index_blobs._store_prepared=interrupted
index_blobs.main()
"""
    result = subprocess.run([sys.executable, "-c", code, str(ROOT), str(database),
                             str(blobs), "commit" if after_commit else "rollback"],
                            capture_output=True, text=True)
    if result.returncode != 91:
        raise AssertionError(result.stdout + result.stderr)
    with closing(sqlite3.connect(database)) as con:
        return {"sources": con.execute("SELECT COUNT(*) FROM version_manifests").fetchone()[0],
                "records": con.execute("SELECT COUNT(*) FROM version_file_records").fetchone()[0]}


def build_fixture(output: Path, *, interrupt_check: bool = False) -> dict:
    output = output.resolve()
    if any(output.is_relative_to((ROOT / name).resolve()) for name in ("index", "blobs", "site")):
        raise ValueError("fixture output must not overlap historical inputs or site output")
    output.mkdir(parents=True, exist_ok=False)
    (output / MARKER).write_text("Generated test data only; never publish as archive metadata.\n")
    blobs, index, dist = output / "blobs", output / "index", output / "site"
    blobs.mkdir(); index.mkdir()
    database = index / "steam2.db"
    with closing(sqlite3.connect(database)) as con:
        con.executescript("""CREATE TABLE files(depot,version,crc,sha256,kind,date,size,
                PRIMARY KEY(kind,depot,version,crc));
            CREATE TABLE labels(depot PRIMARY KEY,label);""")
        index_blobs.ensure_schema(con)

        def add(depot, version, records):
            data = _manifest(records, version, depot=depot)
            name = _source_name(version, data, depot=depot)
            (blobs / name).write_bytes(data)
            con.execute("INSERT INTO files VALUES(?,?, '00000000',?,'blob','2009-05-19',?)",
                        (depot, version, hashlib.sha256(data).hexdigest(), len(data)))

        def record(path, size, file_id):
            return {"path": path, "size": size, "file_id": file_id, "flags": 1}

        for version in (6, 7, 8):
            rows = [record("assets/change.bin", version * 10, 1),
                    record("assets/stable.bin", 5, 2), record("large.bin", 5 * 1024 ** 3, 3),
                    record("max.bin", 0x7FFFFFFFFFFFFFFF, 4), record("zero.bin", 0, 5)]
            if version != 7:
                rows.insert(0, record("assets/return.bin", version, 6))
            add(900001, version, rows)
        add(900002, 1, [record("small.bin", 12, 1), record("zero.bin", 0, 2)])
        add(900004, 1, [])
        add(900006, 1, [record("delivery.bin", 1, 1)])
        for version in (6, 7):
            add(900007, version, [record(f"v{version}-file{n}.bin", n, n) for n in (1, 2, 3)])
        con.execute("INSERT INTO files VALUES(900003,1,'00000000',?,'blob','2009-05-19',1)",
                    ("f" * 64,))
        con.execute("INSERT INTO files VALUES(900005,4,'00000000',?,'blob','2009-05-19',1)",
                    ("e" * 64,))
        con.execute("INSERT INTO versions VALUES(900005,4,900005,4,2,1,'legacy.bin')")
        con.execute("INSERT INTO depot_paths VALUES(900005,'legacy.bin',9,3,5)")
        for depot, label in {900001: "SYNTHETIC exact paged", 900002: "SYNTHETIC exact small",
                            900003: "SYNTHETIC UNKNOWN", 900004: "SYNTHETIC empty valid",
                            900005: "SYNTHETIC LEGACY", 900006: "SYNTHETIC corrupt gzip",
                            900007: "SYNTHETIC delayed pages"}.items():
            con.execute("INSERT INTO labels VALUES(?,?)", (depot, label))
        con.commit()
    (index / "findings.json").write_text("[]", encoding="utf-8")
    result = {"synthetic_only": True, "output": str(output)}
    if interrupt_check:
        result["before_commit_exit"] = _interrupt(database, blobs, False)
        result["after_commit_exit"] = _interrupt(database, blobs, True)
        assert result["before_commit_exit"]["records"] == 0
        assert result["after_commit_exit"]["sources"] == 1

    with mock.patch.multiple(index_blobs, DB=str(database), BLOBS=str(blobs)), \
         mock.patch.multiple(build_vfiles, DB=str(database), DIST=str(dist / "data" / "vfiles"),
                             ROW_CAP=5, PAGE_ROWS=2), \
         mock.patch.multiple(build_site, BASE=str(output), DB=str(database), DIST=str(dist),
                             SITE_SRC=str(ROOT / "site")):
        index_blobs.main()
        with closing(sqlite3.connect(database)) as con:
            first_count = con.execute("SELECT COUNT(*) FROM version_file_records").fetchone()[0]
        index_blobs.main()  # source bytes are rehashed; unchanged valid records are reused
        with closing(sqlite3.connect(database)) as con:
            assert first_count == con.execute("SELECT COUNT(*) FROM version_file_records").fetchone()[0]
        build_vfiles.main()
        build_site.main()

    with closing(sqlite3.connect(database)) as con:
        summaries = con.execute("SELECT COUNT(*) FROM versions").fetchone()[0]
        total = con.execute("SELECT COUNT(*) FROM (SELECT DISTINCT depot,version FROM files)").fetchone()[0]
    (dist / "data/verify.json").write_text(json.dumps({
        "pct": 100 * summaries / total, "indexed_versions": summaries, "total_versions": total,
    }), encoding="utf-8")

    # Fixture-only delivery failures and old-schema data. Production assets stay unchanged.
    (dist / "data" / "vfiles" / "900006.json.gz").write_bytes(b"\x1f\x8bnot-a-gzip-stream")
    old = {"depot": 900005, "versions": [4], "files": {"4": [["legacy.bin", 9]]}}
    build_vfiles._write_json(str(dist / "data" / "vfiles" / "900005.json"), old)
    html = (dist / "index.html").read_text(encoding="utf-8")
    html = re.sub(r'<script type="module" src="https://[^\"]+"></script>', "", html)
    html = html.replace('<script src="counter.js"></script>', "")
    html = html.replace("<title>Steam2 Archive Catalog</title>", "<title>SYNTHETIC Steam2 Catalog test</title>")
    (dist / "index.html").write_text(html, encoding="utf-8")
    result["records"] = first_count
    result["coverage"] = verified_sidecar(database, index / "verified_union.sqlite")
    (output / "run_results.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


class FixtureHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        if "/vfiles/900007.6." in self.path:
            time.sleep(0.25)
        return super().do_GET()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--interrupt-check", action="store_true")
    parser.add_argument("--serve", action="store_true")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    result = build_fixture(args.output, interrupt_check=args.interrupt_check)
    print(json.dumps(result, indent=2))
    if args.serve:
        handler = partial(FixtureHandler, directory=str(args.output.resolve() / "site"))
        with http.server.ThreadingHTTPServer(("127.0.0.1", args.port), handler) as server:
            print(f"Synthetic local fixture: http://127.0.0.1:{args.port}", flush=True)
            server.serve_forever()


if __name__ == "__main__":
    main()

"""Index Steam2 manifests into both a search summary and exact source records.

``depot_paths`` remains a compact union for keyword search. Its ``size`` is the
maximum observed size and its first/last versions are bounds, not a history.
``version_manifests`` and ``version_file_records`` retain the records parsed
from each individual BLOB so consumers can build revision-specific views.
"""
from __future__ import annotations

import hashlib
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

NAME_RE = re.compile(r"^(\d+)_(\d+)_([0-9a-f]{8})_([0-9a-f]{64})\.blob$")
BATCH = 2000
BATCH_RECORDS = 50_000
BATCH_PATH_CHARS = 4 * 1024 * 1024
MAX_BLOB_BYTES = s2_parse.MAX_METADATA_SIZE
PARSER_NAME = "steam2-catalog/s2_parse.py"
def _source_fingerprint(name: str) -> str:
    with open(os.path.join(BASE, name), "rb") as source:
        return name + " sha256:" + hashlib.sha256(source.read()).hexdigest()


PARSER_VERSION = "; ".join(_source_fingerprint(name)
                           for name in ("s2_parse.py", "index_blobs.py"))


def iter_blobs():
    if not os.path.isdir(BLOBS):
        return
    for entry in sorted(os.scandir(BLOBS), key=lambda item: item.name):
        match = NAME_RE.fullmatch(entry.name)
        if not match or not entry.is_file(follow_symlinks=False):
            continue
        depot, version = int(match.group(1)), int(match.group(2))
        if depot > 0xFFFFFFFF or version > 0xFFFFFFFF:
            continue
        yield depot, version, entry.name


def ensure_schema(con: sqlite3.Connection) -> None:
    """Create additive tables; existing catalog tables are left untouched."""
    con.execute("""CREATE TABLE IF NOT EXISTS versions(
        depot INTEGER, version INTEGER, app_id INTEGER, ver_id INTEGER,
        node_count INTEGER, file_count INTEGER, roots TEXT,
        PRIMARY KEY(depot, version))""")
    con.execute("""CREATE TABLE IF NOT EXISTS depot_paths(
        depot INTEGER, path TEXT, size INTEGER,
        first_ver INTEGER, last_ver INTEGER,
        PRIMARY KEY(depot, path)) WITHOUT ROWID""")
    con.execute("""CREATE TABLE IF NOT EXISTS version_manifests(
        depot INTEGER NOT NULL,
        version INTEGER NOT NULL,
        source_blob_name TEXT NOT NULL,
        manifest_ver_id INTEGER,
        source_blob_sha256 TEXT,
        expected_blob_sha256 TEXT,
        source_hash_matches INTEGER,
        parser_name TEXT NOT NULL,
        parser_version TEXT NOT NULL,
        validation_state TEXT NOT NULL,
        header_node_count INTEGER,
        header_file_count INTEGER,
        parsed_node_count INTEGER,
        parsed_file_count INTEGER,
        validation_detail TEXT,
        PRIMARY KEY(depot, version, source_blob_name)) WITHOUT ROWID""")
    con.execute("""CREATE TABLE IF NOT EXISTS version_file_records(
        depot INTEGER NOT NULL,
        version INTEGER NOT NULL,
        source_blob_name TEXT NOT NULL,
        record_ordinal INTEGER NOT NULL,
        path TEXT NOT NULL,
        file_id INTEGER NOT NULL,
        flags INTEGER NOT NULL,
        size INTEGER NOT NULL,
        PRIMARY KEY(depot, version, source_blob_name, record_ordinal),
        FOREIGN KEY(depot, version, source_blob_name)
            REFERENCES version_manifests(depot, version, source_blob_name)
            ON DELETE CASCADE) WITHOUT ROWID""")
    con.execute("""CREATE INDEX IF NOT EXISTS version_file_records_by_path
        ON version_file_records(depot, version, path)""")


def prepare_blob(depot: int, version: int, fn: str, data: bytes,
                 parser=s2_parse.manifest_from_blob, *, actual_sha256: str | None = None,
                 parse_error_override: str | None = None, resource_limited: bool = False) -> dict:
    """Parse one local BLOB and prepare durable evidence rows.

    The expected digest is taken from the content-addressed filename and checked
    against the bytes read from disk. This validates the local copy against that
    identifier; it does not independently authenticate who published the file.
    """
    match = NAME_RE.match(fn)
    expected_sha = match.group(4) if match else None
    actual_sha = actual_sha256 or hashlib.sha256(data).hexdigest()
    source_matches = (actual_sha == expected_sha) if expected_sha else None
    parse_error = parse_error_override
    manifest = None
    if parse_error is None:
        try:
            manifest = parser(data)
            if manifest is None:
                parse_error = "parser returned no manifest"
        except Exception as exc:  # keep the failure with the evidence row
            resource_limited = isinstance(exc, s2_parse.ResourceLimitError)
            parse_error = f"{type(exc).__name__}: {exc}"[:500]
        if manifest is not None and (manifest.app_id != depot or manifest.ver_id != version):
            parse_error = (f"manifest identity mismatch: app_id={manifest.app_id}, "
                           f"version={manifest.ver_id}; expected depot={depot}, version={version}")
            manifest = None

    if manifest is None:
        state = ("hash_mismatch" if source_matches is False else
                 "resource_limit" if resource_limited else "invalid")
        header_nodes = header_files = parsed_nodes = parsed_files = manifest_id = None
        roots = ""
        version_row = (depot, version, None, None, None, None, roots)
        file_rows = []
        path_rows = []
        detail = parse_error
    else:
        file_nodes = [(node.ordinal if node.ordinal is not None else ordinal, node)
                      for ordinal, node in enumerate(manifest.nodes)
                      if node.flags != 0 and node.path]
        header_nodes = manifest.node_count
        header_files = manifest.file_count
        parsed_nodes = len(manifest.nodes)
        parsed_files = len(file_nodes)
        manifest_id = manifest.ver_id
        roots = "\x1f".join(manifest.roots)
        version_row = (depot, version, manifest.app_id, manifest.ver_id,
                       manifest.node_count, manifest.file_count, roots)
        file_rows = [(depot, version, fn, ordinal, node.path,
                      node.file_id, node.flags, node.size)
                     for ordinal, node in file_nodes]
        path_rows = [(depot, node.path, node.size, version, version)
                     for _, node in file_nodes]
        if source_matches is False:
            state = "hash_mismatch"
            detail = "SHA-256 differs from the digest in the BLOB filename"
        elif source_matches is None:
            state = "unverified"
            detail = "source BLOB has no expected SHA-256 in its filename"
        elif header_files != parsed_files:
            state = "partial"
            detail = "header file_count differs from s2_parse.file_nodes() count"
        else:
            state = "valid"
            detail = None
        if state != "valid":
            path_rows = []

    manifest_row = (depot, version, fn, manifest_id, actual_sha, expected_sha,
                    None if source_matches is None else int(source_matches),
                    PARSER_NAME, PARSER_VERSION, state, header_nodes, header_files,
                    parsed_nodes, parsed_files, detail)
    return {"version": version_row, "manifest": manifest_row,
            "records": file_rows, "paths": path_rows}


def _store_prepared(con: sqlite3.Connection, prepared: list[dict]) -> None:
    """Commit a batch while retaining one row per source manifest record."""
    for item in prepared:
        manifest = item["manifest"]
        if manifest[9] == "valid" and manifest[6] == 1:
            con.execute("""INSERT INTO versions VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(depot,version) DO UPDATE SET
                  app_id=excluded.app_id, ver_id=excluded.ver_id,
                  node_count=excluded.node_count, file_count=excluded.file_count,
                  roots=excluded.roots""", item["version"])
        depot, version, source_name = item["manifest"][:3]
        con.execute("DELETE FROM version_file_records "
                    "WHERE depot=? AND version=? AND source_blob_name=?",
                    (depot, version, source_name))
        con.execute("""INSERT OR REPLACE INTO version_manifests(
            depot,version,source_blob_name,manifest_ver_id,source_blob_sha256,
            expected_blob_sha256,source_hash_matches,parser_name,parser_version,
            validation_state,header_node_count,header_file_count,parsed_node_count,
            parsed_file_count,validation_detail)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", item["manifest"])
        con.executemany("""INSERT INTO version_file_records(
            depot,version,source_blob_name,record_ordinal,path,file_id,flags,size)
            VALUES(?,?,?,?,?,?,?,?)""", item["records"])
        con.executemany("""INSERT INTO depot_paths(depot,path,size,first_ver,last_ver)
            VALUES(?,?,?,?,?)
            ON CONFLICT(depot,path) DO UPDATE SET
              size=max(size, excluded.size),
              first_ver=min(first_ver, excluded.first_ver),
              last_ver=max(last_ver, excluded.last_ver)""", item["paths"])


def record_unavailable_sources(con: sqlite3.Connection) -> int:
    """Record known BLOB inventory entries that are not present in blobs/."""
    has_files = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='files'"
    ).fetchone()
    if not has_files:
        return 0
    rows = con.execute("""SELECT DISTINCT depot,version,crc,sha256
        FROM files WHERE kind='blob'""").fetchall()
    missing = []
    for depot, version, crc, sha in rows:
        if not crc or not sha:
            continue
        fn = f"{depot}_{version}_{crc}_{sha}.blob"
        source_path = os.path.join(BLOBS, fn)
        if not os.path.islink(source_path) and os.path.isfile(source_path):
            continue
        missing.append((depot, version, fn, sha, PARSER_NAME, PARSER_VERSION,
                        "unavailable", "source BLOB is not present in local blobs/"))
    con.executemany("""INSERT OR IGNORE INTO version_manifests(
        depot,version,source_blob_name,expected_blob_sha256,parser_name,
        parser_version,validation_state,validation_detail)
        VALUES(?,?,?,?,?,?,?,?)""", missing)
    return len(missing)


def main() -> None:
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    con = sqlite3.connect(DB)
    try:
        _index(con)
    finally:
        con.close()


def _read_source(path: str) -> tuple[bytes, str, str | None]:
    with open(path, "rb") as source:
        data = source.read(MAX_BLOB_BYTES + 1)
        digest = hashlib.sha256(data)
        if len(data) <= MAX_BLOB_BYTES:
            return data, digest.hexdigest(), None
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return b"", digest.hexdigest(), f"source BLOB exceeds {MAX_BLOB_BYTES} byte limit"


def _index(con: sqlite3.Connection) -> None:
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA foreign_keys=ON")
    ensure_schema(con)

    indexed = {(r[0], r[1], r[2], r[3]): r[4] for r in con.execute(
        "SELECT depot,version,source_blob_name,parser_version,source_blob_sha256 "
        "FROM version_manifests WHERE validation_state = 'valid'")}
    t0 = time.time()
    n = n_parsed = n_records = 0
    batch = []
    batch_records = 0
    batch_path_chars = 0

    def flush() -> None:
        nonlocal batch, batch_records, batch_path_chars
        if batch:
            _store_prepared(con, batch)
            con.commit()
            batch = []
            batch_records = 0
            batch_path_chars = 0

    for depot, version, fn in iter_blobs():
        n += 1
        key = (depot, version, fn, PARSER_VERSION)
        source_path = os.path.join(BLOBS, fn)
        if os.path.islink(source_path) or not os.path.isfile(source_path):
            continue
        data, actual_sha, parse_error = _read_source(source_path)
        if parse_error is None and indexed.get(key) == actual_sha:
            del data
            continue
        prepared = prepare_blob(depot, version, fn, data, actual_sha256=actual_sha,
                                parse_error_override=parse_error,
                                resource_limited=parse_error is not None)
        batch.append(prepared)
        batch_records += len(prepared["records"])
        batch_path_chars += sum(len(row[4]) for row in prepared["records"])
        if prepared["manifest"][3] is not None:
            n_parsed += 1
        n_records += len(prepared["records"])
        del data
        if (len(batch) >= BATCH or batch_records >= BATCH_RECORDS or
                batch_path_chars >= BATCH_PATH_CHARS):
            flush()
        if n % BATCH == 0:
            flush()
            rate = n / max(time.time() - t0, 1e-9)
            print(f"  [{n}] parsed={n_parsed} exact-records={n_records} ({rate:.0f}/s)",
                  flush=True)

    flush()
    n_missing = record_unavailable_sources(con)
    con.commit()
    total_paths = con.execute("SELECT COUNT(*) FROM depot_paths").fetchone()[0]
    total_manifests = con.execute("SELECT COUNT(*) FROM version_manifests").fetchone()[0]
    print(f"DONE blobs={n} parsed={n_parsed} records={n_records} "
          f"unavailable={n_missing} manifests={total_manifests} "
          f"union_paths={total_paths} ({time.time()-t0:.0f}s)", flush=True)


if __name__ == "__main__":
    main()

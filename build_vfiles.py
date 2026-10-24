"""Build revision-specific static file lists from indexed source manifests.

Only ``validation_state=valid`` records are emitted as exact file lists. Missing,
ambiguous, invalid, or partial source manifests remain explicitly unknown. The
legacy ``files`` mapping stays ``revision -> [[path, size], ...]`` for compatible
consumers; schema version 2 adds per-revision validation metadata.
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import sqlite3
import index_blobs

BASE = os.path.dirname(os.path.abspath(__file__))
DB = os.path.join(BASE, "index", "steam2.db")
DIST = os.path.join(BASE, "site", "dist", "data", "vfiles")

SCHEMA_VERSION = 2
ROW_CAP = 200_000
PAGE_ROWS = 500_000
JS_MAX_SAFE_INTEGER = 9_007_199_254_740_991
with open(__file__, "rb") as _source:
    GENERATOR_VERSION = hashlib.sha256(_source.read()).hexdigest()


def revision_id(versions: list[int], metadata: dict) -> str:
    """Bind metadata, pages and the depot view to the same generated snapshot."""
    value = {"schema_version": SCHEMA_VERSION, "generator": GENERATOR_VERSION,
             "row_cap": ROW_CAP, "page_rows": PAGE_ROWS,
             "versions": versions, "version_status": metadata}
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode("utf-8")).hexdigest()


def _json_size(size: int) -> int | str:
    """Keep every Steam2 size exact after JavaScript parses the JSON."""
    return size if abs(size) <= JS_MAX_SAFE_INTEGER else str(size)


def _has_table(con: sqlite3.Connection, name: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone() is not None


def _source_name(depot: int, version: int, crc: str, sha256: str) -> str:
    return f"{depot}_{version}_{crc}_{sha256}.blob"


def _versions(con: sqlite3.Connection, depot: int) -> list[int]:
    values = set()
    if _has_table(con, "files"):
        values.update(r[0] for r in con.execute(
            "SELECT DISTINCT version FROM files WHERE depot=? AND kind='blob'",
            (depot,)))
    if _has_table(con, "version_manifests"):
        values.update(r[0] for r in con.execute(
            "SELECT DISTINCT version FROM version_manifests WHERE depot=?", (depot,)))
    if _has_table(con, "versions"):
        values.update(r[0] for r in con.execute(
            "SELECT DISTINCT version FROM versions WHERE depot=?", (depot,)))
    return sorted(values)


def _sources_for_version(con: sqlite3.Connection, depot: int, version: int) -> list[str]:
    sources = set()
    if _has_table(con, "files"):
        for crc, sha in con.execute(
                "SELECT DISTINCT crc,sha256 FROM files "
                "WHERE depot=? AND version=? AND kind='blob'", (depot, version)):
            if crc and sha:
                sources.add(_source_name(depot, version, crc, sha))
    if _has_table(con, "version_manifests"):
        sources.update(r[0] for r in con.execute(
            "SELECT source_blob_name FROM version_manifests "
            "WHERE depot=? AND version=?", (depot, version)))
    return sorted(sources)


def _manifest_metadata(con: sqlite3.Connection, depot: int, version: int,
                       source_name: str | None, source_count: int) -> dict:
    if source_count > 1:
        return {"state": "ambiguous", "source_blob_names": _sources_for_version(
            con, depot, version), "validation_detail":
                "multiple source BLOB identities exist for this revision"}
    if not source_name or not _has_table(con, "version_manifests"):
        return {"state": "unavailable", "source_blob_name": source_name,
                "validation_detail": "source BLOB has not been indexed"}
    row = con.execute("""SELECT source_blob_name,manifest_ver_id,
            source_blob_sha256,expected_blob_sha256,source_hash_matches,
            parser_name,parser_version,validation_state,header_node_count,
            header_file_count,parsed_node_count,parsed_file_count,validation_detail
        FROM version_manifests
        WHERE depot=? AND version=? AND source_blob_name=?""",
        (depot, version, source_name)).fetchone()
    if row is None:
        return {"state": "unavailable", "source_blob_name": source_name,
                "validation_detail": "source BLOB is not present in the local index"}
    meta = dict(row)
    state = meta.pop("validation_state")
    if state == "valid" and meta["parser_version"] != index_blobs.PARSER_VERSION:
        state = "stale_parser"
        meta["validation_detail"] = "stored records require reindexing with the current parser and validator"
    if state == "valid" and (meta["source_hash_matches"] != 1 or
                              not meta["source_blob_sha256"]):
        state = "invalid"
        meta["validation_detail"] = "source hash has not been validated"
    if state == "valid" and not _has_table(con, "version_file_records"):
        state = "partial"
        meta["validation_detail"] = "stored file record table is absent"
    if state == "valid":
        count = con.execute("""SELECT COUNT(*) FROM version_file_records
            WHERE depot=? AND version=? AND source_blob_name=?""",
            (depot, version, source_name)).fetchone()[0]
        if (meta["parsed_file_count"] is None or
                meta["header_file_count"] is None or
                count != meta["parsed_file_count"] or
                count != meta["header_file_count"]):
            state = "partial"
            meta["validation_detail"] = "stored record count does not match manifest counts"
    meta["state"] = state
    return meta


def _depot_ids(con: sqlite3.Connection) -> list[int]:
    values = set()
    for table in ("files", "version_manifests", "versions"):
        if not _has_table(con, table):
            continue
        if table == "files":
            values.update(r[0] for r in con.execute(
                "SELECT DISTINCT depot FROM files WHERE kind='blob'"))
        else:
            values.update(r[0] for r in con.execute(
                f"SELECT DISTINCT depot FROM {table}"))
    return sorted(values)


def _version_context(con: sqlite3.Connection, depot: int):
    versions = _versions(con, depot)
    metadata = {}
    exact_sources = {}
    total_rows = 0
    for version in versions:
        sources = _sources_for_version(con, depot, version)
        chosen = sources[0] if len(sources) == 1 else None
        meta = _manifest_metadata(con, depot, version, chosen, len(sources))
        metadata[str(version)] = meta
        if meta["state"] == "valid" and chosen:
            exact_sources[version] = chosen
            total_rows += meta["parsed_file_count"]
    return versions, metadata, exact_sources, total_rows


def build_depot_payload(con: sqlite3.Connection, depot: int, context=None) -> dict:
    """Return a small-depot schema-v2 JSON object (use pages for large depots)."""
    versions, metadata, exact_sources, total_rows = context or _version_context(con, depot)
    if total_rows > ROW_CAP:
        raise ValueError("depot exceeds ROW_CAP; write paged output instead")
    files = {}
    for version, source_name in exact_sources.items():
        rows = con.execute("""SELECT path,size FROM version_file_records
            WHERE depot=? AND version=? AND source_blob_name=?
            ORDER BY path COLLATE BINARY,record_ordinal""",
            (depot, version, source_name)).fetchall()
        files[str(version)] = [[row[0], _json_size(row[1])] for row in rows]
    return {"schema_version": SCHEMA_VERSION, "depot": depot,
            "data_revision": revision_id(versions, metadata),
            "versions": versions, "version_status": metadata, "files": files}


def _remove_old_outputs(depot: int) -> None:
    if not os.path.isdir(DIST):
        return
    prefix = f"{depot}."
    exact = {f"{depot}.json", f"{depot}.json.gz",
             f"{depot}.meta.json", f"{depot}.meta.json.gz"}
    for name in os.listdir(DIST):
        if name in exact or (name.startswith(prefix) and
                             (name.endswith(".json") or name.endswith(".json.gz"))):
            os.remove(os.path.join(DIST, name))


def _write_json(path: str, value: dict) -> None:
    text = json.dumps(value, separators=(",", ":"), ensure_ascii=False)
    with open(path, "w", encoding="utf-8", newline="\n") as output:
        output.write(text)
    # The static browser fetches pre-compressed JSON. Keep the plain artifact
    # for local inspection and write a deterministic gzip sidecar for publish.
    with open(path + ".gz", "wb") as output:
        with gzip.GzipFile(filename="", fileobj=output, mode="wb", mtime=0) as compressed:
            compressed.write(text.encode("utf-8"))


def _write_paged(con: sqlite3.Connection, depot: int, versions: list[int],
                 metadata: dict, exact_sources: dict, total_rows: int) -> None:
    version_pages = {}
    snapshot = revision_id(versions, metadata)
    for version, source_name in exact_sources.items():
        version_pages[str(version)] = 0
        cursor = con.execute("""SELECT path,size FROM version_file_records
            WHERE depot=? AND version=? AND source_blob_name=?
            ORDER BY path COLLATE BINARY,record_ordinal""",
            (depot, version, source_name))
        page = 0
        while True:
            rows = cursor.fetchmany(PAGE_ROWS)
            if not rows:
                break
            version_pages[str(version)] += 1
            _write_json(os.path.join(DIST, f"{depot}.{version}.{page}.json"), {
                "schema_version": SCHEMA_VERSION, "depot": depot,
                "version": version, "page": page, "data_revision": snapshot,
                "rows": [[row[0], _json_size(row[1])] for row in rows],
            })
            page += 1
    _write_json(os.path.join(DIST, f"{depot}.meta.json"), {
        "schema_version": SCHEMA_VERSION, "depot": depot,
        "data_revision": snapshot, "page_rows": PAGE_ROWS,
        "versions": versions, "version_status": metadata,
        "version_pages": version_pages, "total_rows": total_rows, "paged": True,
    })


def main() -> None:
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    os.makedirs(DIST, exist_ok=True)
    small = paged = 0
    for depot in _depot_ids(con):
        _remove_old_outputs(depot)
        context = _version_context(con, depot)
        versions, metadata, exact_sources, total_rows = context
        if total_rows <= ROW_CAP:
            payload = build_depot_payload(con, depot, context)
            _write_json(os.path.join(DIST, f"{depot}.json"), payload)
            small += 1
        else:
            _write_paged(con, depot, versions, metadata, exact_sources, total_rows)
            paged += 1
    print(f"vfiles schema={SCHEMA_VERSION}: {small} whole depots, {paged} paged depots")
    con.close()


if __name__ == "__main__":
    main()

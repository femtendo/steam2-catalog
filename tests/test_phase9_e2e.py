"""Synthetic end-to-end regression for the versioned manifest pipeline."""
from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
import struct
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import build_vfiles
import index_blobs


def _container(records):
    body = b"".join(struct.pack("<HI", 4, len(value)) +
                     struct.pack("<I", key) + value for key, value in records)
    return struct.pack("<HII", 0x5001, 10 + len(body), 0) + body


def _manifest(records, version, *, declared_files=None, depot=3591):
    """Encode a tree-shaped manifest independently from the production parser."""
    root = {"name": "", "path": "", "directory": True, "children": {}}
    for record in records:
        path = record["path"]
        parts = path.split("/")
        parent = root
        for position, part in enumerate(parts[:-1]):
            parent = parent["children"].setdefault(part, {
                "name": part, "path": "/".join(parts[:position + 1]),
                "directory": True, "children": {}})
        leaf = parts[-1]
        if leaf in parent["children"]:
            raise ValueError("fixture contains a duplicate path")
        parent["children"][leaf] = {
            **record, "name": leaf, "path": path,
            "directory": False, "children": {}}

    strings = bytearray(b"\0")
    offsets = {"": 0}
    def name_offset(name):
        if name not in offsets:
            offsets[name] = len(strings)
            strings.extend(name.encode("utf-8") + b"\0")
        return offsets[name]

    ordered = []
    def visit(node, parent_path):
        node["parent_path"] = parent_path
        node["index"] = len(ordered)
        ordered.append(node)
        children = [node["children"][key] for key in sorted(node["children"])]
        node["ordered_children"] = children
        for child in children:
            visit(child, node["path"])
            if not child["directory"] and child["size"] > 0x7FFFFFFF:
                child["aux_index"] = len(ordered)
                ordered.append({"auxiliary": True, "upper": child["size"] >> 31})

    visit(root, None)
    raw_nodes = []
    file_count = sum(not entry.get("auxiliary") and not entry["directory"]
                     for entry in ordered)
    for item in ordered:
        if item.get("auxiliary"):
            raw_nodes.append((0, item["upper"], 0, 0, 0, 0, 0))
            continue
        children = item["ordered_children"]
        first_child = children[0]["index"] if children else 0
        next_sibling = 0
        if item["parent_path"] is not None:
            parent = root
            for part in item["parent_path"].split("/") if item["parent_path"] else []:
                parent = parent["children"][part]
            siblings = parent["ordered_children"]
            position = siblings.index(item)
            if position + 1 < len(siblings):
                next_sibling = siblings[position + 1]["index"]
        if item["directory"]:
            count_or_size, file_id, flags = len(children), 0, 0
        else:
            size = item["size"]
            count_or_size = ((size & 0x7FFFFFFF) | 0x80000000
                             if size > 0x7FFFFFFF else size)
            file_id, flags = item["file_id"], item["flags"]
        parent_index = (0xFFFFFFFF if item["parent_path"] is None
                        else next(node["index"] for node in ordered
                                  if not node.get("auxiliary") and
                                  node["path"] == item["parent_path"]))
        raw_nodes.append((name_offset(item["name"]), count_or_size, file_id,
                          flags, parent_index, next_sibling, first_child))

    node_bytes = b"".join(struct.pack("<7I", *node) for node in raw_nodes)
    file_count = file_count if declared_files is None else declared_files
    total = 56 + len(node_bytes) + len(strings)
    header = struct.pack("<14I", 3, depot, version, len(raw_nodes), file_count,
                         0x8000, total, len(strings), 0, 0, 0, 0, 0, 0)
    data = bytearray(header + node_bytes + strings)
    a = b = 0
    for offset, value in enumerate(data):
        if 48 <= offset < 56:
            value = 0
        a = (a + value) % 65521
        b = (b + a) % 65521
    struct.pack_into("<I", data, 52, (b << 16) | a)
    inner = _container([(0, bytes(data))])
    return _container([(3, inner)])


def _source_name(version, data, crc="00000000", depot=3591):
    return f"{depot}_{version}_{crc}_{hashlib.sha256(data).hexdigest()}.blob"


class EndToEndPipelineTests(unittest.TestCase):
    def test_real_parser_sqlite_generator_and_gzip_chain(self):
        records = {
            6: [
                {"path": "PlantsVsZombies/big.dat", "size": (3 << 31) | 7,
                 "file_id": 5, "flags": 1},
                {"path": "PlantsVsZombies/change.bin", "size": 10,
                 "file_id": 6, "flags": 1},
                {"path": "PlantsVsZombies/return.bin", "size": 20,
                 "file_id": 7, "flags": 1},
                {"path": "PlantsVsZombies/stable.bin", "size": 5,
                 "file_id": 8, "flags": 1},
                {"path": "PlantsVsZombies/flags.bin", "size": 3,
                 "file_id": 9, "flags": 1},
            ],
            7: [
                {"path": "PlantsVsZombies/change.bin", "size": 11,
                 "file_id": 16, "flags": 1},
                {"path": "PlantsVsZombies/stable.bin", "size": 5,
                 "file_id": 17, "flags": 1},
                {"path": "PlantsVsZombies/flags.bin", "size": 3,
                 "file_id": 18, "flags": 2},
                {"path": "PlantsVsZombies/empty.bin", "size": 0,
                 "file_id": 19, "flags": 1},
            ],
            8: [
                {"path": "PlantsVsZombies/change.bin", "size": 12,
                 "file_id": 26, "flags": 1},
                {"path": "PlantsVsZombies/return.bin", "size": 20,
                 "file_id": 27, "flags": 1},
                {"path": "PlantsVsZombies/stable.bin", "size": 5,
                 "file_id": 28, "flags": 1},
                {"path": "PlantsVsZombies/flags.bin", "size": 3,
                 "file_id": 29, "flags": 3},
            ],
        }
        expected = {
            version: sorted([[record["path"], record["size"]] for record in rows])
            for version, rows in records.items()
        }
        expected[11] = []

        with tempfile.TemporaryDirectory(prefix=".phase9-e2e-", dir=ROOT) as temp:
            base = Path(temp)
            blobs = base / "blobs"
            database_dir = base / "index"
            dist = base / "dist"
            blobs.mkdir()
            database_dir.mkdir()
            db = database_dir / "steam2.db"
            con = sqlite3.connect(db)
            con.execute("CREATE TABLE files(depot INTEGER,version INTEGER,crc TEXT,"
                         "sha256 TEXT,kind TEXT,size INTEGER,date TEXT)")
            source_rows = {}
            for version, rows in records.items():
                data = _manifest(rows, version)
                name = _source_name(version, data)
                (blobs / name).write_bytes(data)
                source_rows[version] = (name, data)
                con.execute("INSERT INTO files VALUES(3591,?,?,?,'blob',?,?)",
                            (version, name.split("_")[2],
                             hashlib.sha256(data).hexdigest(), len(data), "2009-05-01"))
            malformed = b"this is not a Steam2 blob"
            malformed_name = _source_name(10, malformed)
            (blobs / malformed_name).write_bytes(malformed)
            con.execute("INSERT INTO files VALUES(3591,10,?,?, 'blob', ?,?)",
                        (malformed_name.split("_")[2],
                         hashlib.sha256(malformed).hexdigest(), len(malformed), "2009-05-01"))
            empty = _manifest([], 11, declared_files=0)
            empty_name = _source_name(11, empty)
            (blobs / empty_name).write_bytes(empty)
            con.execute("INSERT INTO files VALUES(3591,11,?,?, 'blob', ?,?)",
                        (empty_name.split("_")[2], hashlib.sha256(empty).hexdigest(),
                         len(empty), "2009-05-01"))
            inconsistent = _manifest([
                {"path": "PlantsVsZombies/hidden.bin", "size": 1,
                 "file_id": 90, "flags": 1}], 12, declared_files=0)
            inconsistent_name = _source_name(12, inconsistent)
            (blobs / inconsistent_name).write_bytes(inconsistent)
            con.execute("INSERT INTO files VALUES(3591,12,?,?, 'blob', ?,?)",
                        (inconsistent_name.split("_")[2],
                         hashlib.sha256(inconsistent).hexdigest(), len(inconsistent),
                         "2009-05-01"))
            missing_digest = "f" * 64
            con.execute("INSERT INTO files VALUES(3591,9,'00000000',?,'blob',1,?)",
                        (missing_digest, "2009-05-01"))
            con.commit()
            con.close()

            old = (index_blobs.BLOBS, index_blobs.DB, build_vfiles.DB, build_vfiles.DIST)
            index_blobs.BLOBS = str(blobs)
            index_blobs.DB = str(db)
            build_vfiles.DB = str(db)
            build_vfiles.DIST = str(dist)
            try:
                index_blobs.main()
                build_vfiles.main()
            finally:
                index_blobs.BLOBS, index_blobs.DB, build_vfiles.DB, build_vfiles.DIST = old

            with closing(sqlite3.connect(db)) as con:
                stored = con.execute("SELECT version,path,size,file_id,flags "
                                 "FROM version_file_records WHERE depot=3591 "
                                 "ORDER BY version,path").fetchall()
                stored_valid = [(version, path, size) for version, path, size, _, _ in stored
                                if version in records]
                expected_sql = [(version, path, size)
                                for version in (6, 7, 8)
                                for path, size in expected[version]]
                self.assertEqual(expected_sql, stored_valid)
                self.assertEqual((18, 2), con.execute(
                    "SELECT file_id,flags FROM version_file_records "
                    "WHERE version=7 AND path LIKE '%/flags.bin'").fetchone())
                states = dict(con.execute("SELECT version,validation_state "
                                          "FROM version_manifests WHERE depot=3591"))
                self.assertEqual("unavailable", states[9])
                self.assertEqual("invalid", states[10])
                self.assertEqual("valid", states[11])
                self.assertEqual("invalid", states[12])

            plain = json.loads((dist / "3591.json").read_text(encoding="utf-8"))
            decoded_gzip = json.loads(gzip.decompress((dist / "3591.json.gz").read_bytes()))
            self.assertEqual(plain, decoded_gzip)
            for version in (6, 7, 8):
                self.assertEqual(expected[version], plain["files"][str(version)])
            self.assertNotIn("9", plain["files"])
            self.assertNotIn("10", plain["files"])
            self.assertEqual([], plain["files"]["11"])
            self.assertNotIn("12", plain["files"])
            self.assertEqual("unavailable", plain["version_status"]["9"]["state"])
            self.assertEqual("invalid", plain["version_status"]["10"]["state"])
            self.assertEqual("valid", plain["version_status"]["11"]["state"])
            self.assertEqual("invalid", plain["version_status"]["12"]["state"])
            self.assertEqual((3 << 31) | 7, plain["files"]["6"][0][1])


if __name__ == "__main__":
    unittest.main()

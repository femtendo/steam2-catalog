"""Regressions for source reuse and parser resource limits found in final review."""
from __future__ import annotations

import hashlib
import gzip
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from contextlib import closing
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import index_blobs
import s2_parse
import build_site
import build_vfiles
from integration_demo import build_fixture
from test_phase9_e2e import _manifest, _source_name
from test_versioned_index import raw_manifest


class FinalReviewRegressions(unittest.TestCase):
    def test_oversized_first_blob_is_recorded_without_crashing(self):
        with tempfile.TemporaryDirectory(prefix=".final-review-", dir=ROOT) as temp:
            base = Path(temp)
            blobs = base / "blobs"
            blobs.mkdir()
            data = b"oversized synthetic metadata" * 4
            (blobs / _source_name(7, data)).write_bytes(data)
            database = base / "index" / "steam2.db"
            code = ("import sys; sys.path.insert(0,sys.argv[1]); import index_blobs; "
                    "index_blobs.BLOBS=sys.argv[2]; index_blobs.DB=sys.argv[3]; "
                    "index_blobs.MAX_BLOB_BYTES=32; index_blobs.main()")
            result = subprocess.run([sys.executable, "-c", code, str(ROOT), str(blobs),
                                     str(database)], capture_output=True, text=True)
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            with closing(sqlite3.connect(database)) as con:
                state, detail = con.execute(
                    "SELECT validation_state,validation_detail FROM version_manifests").fetchone()
                self.assertEqual("resource_limit", state)
                self.assertIn("limit", detail)

    def test_known_filename_is_rehashed_before_reusing_parsed_records(self):
        with tempfile.TemporaryDirectory(prefix=".final-review-", dir=ROOT) as temp:
            base = Path(temp)
            blobs = base / "blobs"
            blobs.mkdir()
            data = _manifest([{"path": "asset.bin", "size": 7,
                               "file_id": 1, "flags": 1}], 7)
            source = blobs / _source_name(7, data)
            source.write_bytes(data)
            database = base / "index" / "steam2.db"
            with mock.patch.multiple(index_blobs, BLOBS=str(blobs), DB=str(database)):
                index_blobs.main()
                with closing(sqlite3.connect(database)) as con:
                    con.row_factory = sqlite3.Row
                    before = build_vfiles.build_depot_payload(con, 3591)["data_revision"]
                source.write_bytes(b"changed local bytes with the same source filename")
                index_blobs.main()
            with closing(sqlite3.connect(database)) as con:
                con.row_factory = sqlite3.Row
                self.assertEqual("hash_mismatch", con.execute(
                    "SELECT validation_state FROM version_manifests").fetchone()[0])
                payload = build_vfiles.build_depot_payload(con, 3591)
                self.assertNotEqual(before, payload["data_revision"])
                self.assertEqual({}, payload["files"])
            indexer_digest = hashlib.sha256((ROOT / "index_blobs.py").read_bytes()).hexdigest()
            self.assertIn(indexer_digest, index_blobs.PARSER_VERSION)

    def test_component_is_bounded_before_decoding_or_retaining_it(self):
        data = b"x" * 40_000 + b"\0"
        with self.assertRaisesRegex(ValueError, "component.*length"):
            s2_parse._read_string(data, 0, len(data), 0)

    def test_parent_first_order_cannot_bypass_path_depth_limit(self):
        count = 1027
        nodes = [(0, 1, 0, 0, 0xFFFFFFFF, 0, 1)]
        for index in range(1, count - 1):
            nodes.append((1, 1, 0, 0, index - 1, 0, index + 1))
        nodes.append((3, 1, 1, 1, count - 2, 0, 0))
        with self.assertRaisesRegex(ValueError, "depth"):
            s2_parse.parse_manifest(raw_manifest(nodes, b"\0d\0f\0"))

    def test_component_budget_is_checked_before_retaining_duplicate_names(self):
        strings = b"\0" + b"a" * 16 + b"\0" + b"b" * 16 + b"\0"
        nodes = [(0, 2, 0, 0, 0xFFFFFFFF, 0, 1),
                 (1, 1, 1, 1, 0, 2, 0), (18, 1, 2, 1, 0, 0, 0)]
        data = raw_manifest(nodes, strings, file_count=2)
        with mock.patch.object(s2_parse, "MAX_EXPANDED_PATH_CHARS", 32):
            self.assertEqual(2, len(s2_parse.parse_manifest(data).file_nodes()))
        with mock.patch.object(s2_parse, "MAX_EXPANDED_PATH_CHARS", 31):
            with self.assertRaisesRegex(ValueError, "components exceed character limit 31"):
                s2_parse.parse_manifest(data)

    def test_metadata_limit_has_an_exact_boundary_and_bounded_decompression(self):
        import struct
        import zlib
        from test_phase9_e2e import _container
        data = _container([(0, b"small fixture")])
        with mock.patch.object(s2_parse, "MAX_METADATA_SIZE", len(data)):
            self.assertEqual(b"small fixture", s2_parse._read_keys(data)[0])
        with mock.patch.object(s2_parse, "MAX_METADATA_SIZE", len(data) - 1):
            with self.assertRaisesRegex(ValueError, "limit"):
                s2_parse._read_keys(data)
            packed = zlib.compress(data)
            wrapper = struct.pack("<HQQH", 0x4301, len(packed), len(data), 6) + packed
            with self.assertRaisesRegex(ValueError, "unpacked size too large"):
                s2_parse._decompress(wrapper)

    def test_batches_are_bounded_by_records_not_only_blob_count(self):
        with tempfile.TemporaryDirectory(prefix=".final-review-", dir=ROOT) as temp:
            base = Path(temp); blobs = base / "blobs"; blobs.mkdir()
            for version in (6, 7):
                data = _manifest([{"path": f"{n}.bin", "size": 1,
                                   "file_id": n, "flags": 1} for n in (1, 2, 3)], version)
                (blobs / _source_name(version, data)).write_bytes(data)
            original = index_blobs._store_prepared
            lengths = []
            def store(con, batch):
                lengths.append(len(batch)); original(con, batch)
            for number, limits in enumerate(({"BATCH_RECORDS": 2}, {"BATCH_PATH_CHARS": 1})):
                lengths.clear()
                with mock.patch.multiple(index_blobs, DB=str(base / f"index{number}" / "steam2.db"),
                                         BLOBS=str(blobs), **limits), \
                     mock.patch.object(index_blobs, "_store_prepared", side_effect=store):
                    index_blobs.main()
                self.assertEqual([1, 1], lengths)

    def test_old_validator_metadata_requires_reindexing_before_exact_output(self):
        with closing(sqlite3.connect(":memory:")) as con:
            con.row_factory = sqlite3.Row
            index_blobs.ensure_schema(con)
            data = _manifest([{"path": "x.bin", "size": 1, "file_id": 1, "flags": 1}], 7)
            prepared = index_blobs.prepare_blob(3591, 7, _source_name(7, data), data)
            index_blobs._store_prepared(con, [prepared])
            self.assertIn("7", build_vfiles.build_depot_payload(con, 3591)["files"])
            con.execute("UPDATE version_manifests SET parser_version='old validator'")
            value = build_vfiles.build_depot_payload(con, 3591)
            self.assertEqual("stale_parser", value["version_status"]["7"]["state"])
            self.assertEqual({}, value["files"])

    def test_ambiguous_inventory_is_not_labeled_exact_by_site_builder(self):
        with closing(sqlite3.connect(":memory:")) as con:
            con.row_factory = sqlite3.Row
            index_blobs.ensure_schema(con)
            con.execute("CREATE TABLE files(depot,version,crc,sha256,kind)")
            data = _manifest([{"path": "x.bin", "size": 1, "file_id": 1, "flags": 1}], 7)
            name = _source_name(7, data)
            index_blobs._store_prepared(con, [index_blobs.prepare_blob(3591, 7, name, data)])
            con.execute("INSERT INTO files VALUES(3591,7,'12345678',?,'blob')", ("e" * 64,))
            rows = build_site.versions_of(con, 3591)
            self.assertEqual("ambiguous", rows[0]["source_state"])
            self.assertNotEqual("exact", rows[0]["manifest_state"])

    def test_full_isolated_site_interrupt_resume_and_verified_sidecar(self):
        with tempfile.TemporaryDirectory(prefix=".final-review-", dir=ROOT) as temp:
            output = Path(temp) / "isolated"
            result = build_fixture(output, interrupt_check=True)
            self.assertEqual(0, result["before_commit_exit"]["records"])
            self.assertEqual(1, result["after_commit_exit"]["sources"])
            self.assertEqual("partial", result["coverage"][900003]["state"])
            dist = output / "site"
            detail = json.loads((dist / "data/depots/900001.json").read_text())
            meta = json.loads(gzip.decompress((dist / "data/vfiles/900001.meta.json.gz").read_bytes()))
            self.assertEqual(detail["vfiles_revision"], meta["data_revision"])
            self.assertEqual(3, meta["version_pages"]["6"])
            for version in (6, 7, 8):
                paths = []
                for page in range(meta["version_pages"][str(version)]):
                    value = json.loads((dist / f"data/vfiles/900001.{version}.{page}.json").read_text())
                    self.assertEqual(meta["data_revision"], value["data_revision"])
                    paths.extend(row[0] for row in value["rows"])
                self.assertEqual(version != 7, "assets/return.bin" in paths)
            empty = json.loads((dist / "data/vfiles/900004.json").read_text())
            self.assertEqual([], empty["files"]["1"])
            self.assertTrue((dist / "data/depots/900003.json").is_file())
            self.assertTrue((dist / "data/depots/900004.json").is_file())
            self.assertEqual((ROOT / "site/app.js").read_bytes(), (dist / "app.js").read_bytes())
            with closing(sqlite3.connect(output / "index/verified_union.sqlite")) as con:
                self.assertEqual(0, con.execute(
                    "SELECT COUNT(*) FROM observations WHERE path='legacy.bin'").fetchone()[0])
                self.assertEqual(0, con.execute(
                    "SELECT COUNT(*) FROM complete_paths WHERE depot IN(900003,900005)").fetchone()[0])
            with self.assertRaises(FileExistsError):
                build_fixture(output)


if __name__ == "__main__":
    unittest.main()

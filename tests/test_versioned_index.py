from __future__ import annotations

import hashlib
import gzip
import json
import sqlite3
import struct
import sys
import unittest
import zlib
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import build_site
import build_vfiles
import index_blobs
import s2_parse


def manifest(nodes=(), *, file_count=None, ver_id=100):
    nodes = list(nodes)
    files = sum(1 for node in nodes if node.flags != 0 and node.path)
    return SimpleNamespace(
        app_id=3591,
        ver_id=ver_id,
        node_count=len(nodes),
        file_count=files if file_count is None else file_count,
        roots=["PlantsVsZombies"],
        nodes=nodes,
    )


def raw_manifest(nodes, strings, *, version=7, file_count=1):
    """Build a full synthetic manifest with an independent checksum."""
    body_nodes = b"".join(struct.pack("<7I", *node) for node in nodes)
    total_size = 56 + len(body_nodes) + len(strings)
    header = struct.pack("<14I", 3, 3591, version, len(nodes), file_count,
                         0x8000, total_size, len(strings), 0, 0, 0, 0, 0, 0)
    data = bytearray(header + body_nodes + strings)
    a = b = 0
    for offset, value in enumerate(data):
        if 48 <= offset < 56:
            value = 0
        a = (a + value) % 65521
        b = (b + a) % 65521
    struct.pack_into("<I", data, 52, (b << 16) | a)
    return bytes(data)


def wrap_blob(manifest_bytes):
    def record(key, value):
        return struct.pack("<HI", 4, len(value)) + struct.pack("<I", key) + value

    inner_record = record(0, manifest_bytes)
    inner = struct.pack("<HII", 0x5001, 10 + len(inner_record), 0) + inner_record
    outer_record = record(3, inner)
    return struct.pack("<HII", 0x5001, 10 + len(outer_record), 0) + outer_record


class VersionedIndexTests(unittest.TestCase):
    def setUp(self):
        self.con = sqlite3.connect(":memory:")
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA foreign_keys=ON")
        index_blobs.ensure_schema(self.con)
        self.con.execute("""CREATE TABLE files(
            depot INTEGER,version INTEGER,crc TEXT,sha256 TEXT,kind TEXT)""")

    def tearDown(self):
        self.con.close()

    def ingest(self, version, nodes=(), *, file_count=None, fn=None, data=None,
               parsed=None):
        data = data if data is not None else f"blob-{version}".encode()
        digest = hashlib.sha256(data).hexdigest()
        fn = fn or f"3591_{version}_00000000_{digest}.blob"
        parsed = parsed if parsed is not None else manifest(
            nodes, file_count=file_count, ver_id=version)
        prepared = index_blobs.prepare_blob(
            3591, version, fn, data, parser=lambda _raw: parsed)
        index_blobs._store_prepared(self.con, [prepared])
        self.con.commit()
        return fn, digest

    def payload(self, depot=3591):
        return build_vfiles.build_depot_payload(self.con, depot)

    def test_legacy_projection_reproduces_gap_and_max_size_loss(self):
        observations = {6: ("return.bin", 100), 8: ("return.bin", 250)}
        first, last = min(observations), max(observations)
        max_size = max(size for _, size in observations.values())
        old = {v: [["return.bin", max_size]] if first <= v <= last else []
               for v in (6, 7, 8)}
        self.assertEqual([["return.bin", 250]], old[7])

    def test_size_changes_are_preserved_per_revision(self):
        for version, size in ((6, 100), (7, 250), (8, 120)):
            self.ingest(version, [s2_parse.ManifestNode(4, 1, "asset.bin", size)])
        payload = self.payload()
        self.assertEqual([100, 250, 120], [payload["files"][str(v)][0][1]
                                           for v in (6, 7, 8)])

    def test_removed_then_restored_path_is_not_interpolated(self):
        self.ingest(6, [s2_parse.ManifestNode(4, 1, "return.bin", 40)])
        self.ingest(7, [], file_count=0)
        self.ingest(8, [s2_parse.ManifestNode(9, 1, "return.bin", 40)])
        payload = self.payload()
        self.assertEqual([], payload["files"]["7"])
        self.assertEqual([["return.bin", 40]], payload["files"]["8"])

    def test_nonconsecutive_revision_missing_manifest_stays_unknown(self):
        self.ingest(6, [s2_parse.ManifestNode(4, 1, "return.bin", 40)])
        self.ingest(8, [s2_parse.ManifestNode(9, 1, "return.bin", 40)])
        self.con.execute("""INSERT INTO files(depot,version,crc,sha256,kind)
            VALUES(3591,7,'00000000',?,'blob')""", ("f" * 64,))
        self.con.commit()
        payload = self.payload()
        self.assertEqual("unavailable", payload["version_status"]["7"]["state"])
        self.assertNotIn("7", payload["files"])

    def test_identical_records_are_kept_in_every_revision(self):
        for version in (6, 7, 8):
            self.ingest(version, [s2_parse.ManifestNode(3, 1, "stable.bin", 15)])
        payload = self.payload()
        self.assertEqual([[["stable.bin", 15]]] * 3,
                         [payload["files"][str(v)] for v in (6, 7, 8)])

    def test_manifest_and_record_provenance_fields_are_persisted(self):
        data = b"synthetic source blob"
        fn, digest = self.ingest(
            7, [s2_parse.ManifestNode(44, 5, "PlantsVsZombies.exe", 1234)],
            data=data)
        meta = self.con.execute("SELECT * FROM version_manifests").fetchone()
        row = self.con.execute("SELECT * FROM version_file_records").fetchone()
        self.assertEqual(fn, meta["source_blob_name"])
        self.assertEqual(digest, meta["source_blob_sha256"])
        self.assertEqual(digest, meta["expected_blob_sha256"])
        self.assertEqual(7, meta["manifest_ver_id"])
        self.assertEqual("valid", meta["validation_state"])
        self.assertEqual(0, row["record_ordinal"])
        self.assertEqual(44, row["file_id"])
        self.assertEqual(5, row["flags"])
        self.assertEqual(1234, row["size"])

    def test_hash_mismatch_is_retained_but_not_emitted_as_exact(self):
        wrong_sha = "0" * 64
        fn = f"3591_7_00000000_{wrong_sha}.blob"
        self.ingest(7, [s2_parse.ManifestNode(1, 1, "x.bin", 4)], fn=fn,
                    data=b"different bytes")
        payload = self.payload()
        self.assertEqual("hash_mismatch", payload["version_status"]["7"]["state"])
        self.assertNotIn("7", payload["files"])

    def test_parse_failure_is_invalid_and_not_an_empty_manifest(self):
        data = b"broken"
        fn = f"3591_7_00000000_{hashlib.sha256(data).hexdigest()}.blob"
        prep = index_blobs.prepare_blob(
            3591, 7, fn, data,
            parser=lambda _raw: (_ for _ in ()).throw(ValueError("bad manifest")))
        index_blobs._store_prepared(self.con, [prep])
        self.con.commit()
        payload = self.payload()
        self.assertEqual("invalid", payload["version_status"]["7"]["state"])
        self.assertNotIn("7", payload["files"])

    def test_count_mismatch_downgrades_to_partial(self):
        self.ingest(7, [s2_parse.ManifestNode(1, 1, "x.bin", 4)], file_count=2)
        payload = self.payload()
        self.assertEqual("partial", payload["version_status"]["7"]["state"])
        self.assertEqual(2, payload["version_status"]["7"]["header_file_count"])
        self.assertEqual(1, payload["version_status"]["7"]["parsed_file_count"])
        self.assertNotIn("7", payload["files"])

    def test_empty_valid_manifest_is_distinct_from_unavailable(self):
        self.ingest(6, [], file_count=0)
        self.con.execute("""INSERT INTO files(depot,version,crc,sha256,kind)
            VALUES(3591,7,'00000000',?,'blob')""", ("e" * 64,))
        self.con.commit()
        payload = self.payload()
        self.assertEqual("valid", payload["version_status"]["6"]["state"])
        self.assertEqual([], payload["files"]["6"])
        self.assertEqual("unavailable", payload["version_status"]["7"]["state"])
        self.assertNotIn("7", payload["files"])

    def test_duplicate_manifest_sources_are_ambiguous(self):
        self.ingest(7, [s2_parse.ManifestNode(1, 1, "x.bin", 4)])
        self.con.execute("""INSERT INTO files(depot,version,crc,sha256,kind)
            VALUES(3591,7,'11111111',?,'blob')""", ("b" * 64,))
        self.con.commit()
        payload = self.payload()
        self.assertEqual("ambiguous", payload["version_status"]["7"]["state"])
        self.assertNotIn("7", payload["files"])

    def test_duplicate_paths_retain_ordered_records(self):
        self.ingest(7, [s2_parse.ManifestNode(1, 1, "dup.bin", 3),
                        s2_parse.ManifestNode(2, 1, "dup.bin", 4)])
        rows = self.con.execute("""SELECT record_ordinal,file_id,size
            FROM version_file_records ORDER BY record_ordinal""").fetchall()
        self.assertEqual([(0, 1, 3), (1, 2, 4)], [tuple(row) for row in rows])

    def test_case_unicode_and_potentially_dangerous_paths_are_preserved_as_data(self):
        paths = ("Foo.dat", "foo.dat", "sprites/🌻.png", "../outside.txt")
        self.ingest(7, [s2_parse.ManifestNode(i, 1, path, i)
                        for i, path in enumerate(paths)])
        records = self.con.execute("SELECT path FROM version_file_records "
                                   "ORDER BY record_ordinal").fetchall()
        self.assertEqual(list(paths), [row[0] for row in records])

    def test_flags_file_ids_and_zero_sizes_are_not_collapsed(self):
        self.ingest(6, [s2_parse.ManifestNode(10, 1, "same-size.bin", 8)])
        self.ingest(7, [s2_parse.ManifestNode(11, 2, "same-size.bin", 8)])
        self.ingest(8, [s2_parse.ManifestNode(12, 1, "empty.bin", 0)])
        payload = self.payload()
        self.assertEqual([["same-size.bin", 8]], payload["files"]["6"])
        rows = self.con.execute("""SELECT file_id,flags,size FROM version_file_records
            WHERE path='same-size.bin' ORDER BY version""").fetchall()
        self.assertEqual([(10, 1, 8), (11, 2, 8)], [tuple(row) for row in rows])
        self.assertEqual([["empty.bin", 0]], payload["files"]["8"])

    def test_version_order_is_numeric_and_not_interpolated(self):
        for version in (2, 11, 40):
            self.ingest(version, [s2_parse.ManifestNode(version, 1, "x", version)])
        payload = self.payload()
        self.assertEqual([2, 11, 40], payload["versions"])
        self.assertNotIn("3", payload["files"])

    def test_public_json_shape_is_backward_compatible_and_versioned(self):
        self.ingest(7, [s2_parse.ManifestNode(44, 5, "PlantsVsZombies.exe", 1234)])
        payload = self.payload()
        serialized = json.dumps(payload, ensure_ascii=False)
        decoded = json.loads(serialized)
        self.assertEqual(2, decoded["schema_version"])
        self.assertEqual([["PlantsVsZombies.exe", 1234]], decoded["files"]["7"])
        self.assertEqual("valid", decoded["version_status"]["7"]["state"])

    def test_sizes_above_javascript_safe_integer_remain_exact_in_json(self):
        maximum = 0x7FFFFFFFFFFFFFFF
        self.ingest(7, [s2_parse.ManifestNode(77, 1, "huge.bin", maximum)])
        payload = json.loads(json.dumps(self.payload()))
        self.assertEqual(str(maximum), payload["files"]["7"][0][1])

    def test_paged_output_keeps_revision_and_validated_rows(self):
        self.ingest(7, [s2_parse.ManifestNode(1, 1, "a.bin", 1),
                        s2_parse.ManifestNode(2, 1, "b.bin", 2)])
        versions, metadata, sources, total = build_vfiles._version_context(self.con, 3591)
        folder = ROOT / "tests" / ".vfiles_test_output"
        folder.mkdir(exist_ok=True)
        old_dist, old_page_rows = build_vfiles.DIST, build_vfiles.PAGE_ROWS
        build_vfiles.DIST = str(folder)
        build_vfiles.PAGE_ROWS = 1
        try:
            build_vfiles._write_paged(self.con, 3591, versions, metadata, sources, total)
            meta = json.loads((folder / "3591.meta.json").read_text(encoding="utf-8"))
            first = json.loads((folder / "3591.7.0.json").read_text(encoding="utf-8"))
            second = json.loads((folder / "3591.7.1.json").read_text(encoding="utf-8"))
            self.assertEqual({"7": 2}, meta["version_pages"])
            self.assertEqual([["a.bin", 1]], first["rows"])
            self.assertEqual([["b.bin", 2]], second["rows"])
        finally:
            build_vfiles.DIST, build_vfiles.PAGE_ROWS = old_dist, old_page_rows
            for output in folder.iterdir():
                output.unlink()
            folder.rmdir()

    def test_previously_generated_legacy_cache_is_not_reported_as_exact(self):
        old = {"depot": 3591, "versions": [7], "files": {"7": [["x", 9]]}}
        self.assertNotEqual(2, old.get("schema_version"))
        # The frontend contract deliberately requires schema_version == 2;
        # absence of the new field therefore cannot be treated as a valid list.

    def test_unavailable_inventory_rows_are_recorded_without_fabricated_files(self):
        old_blobs = index_blobs.BLOBS
        index_blobs.BLOBS = str(ROOT / "tests" / "empty-blob-fixture")
        try:
            self.con.execute("INSERT INTO files VALUES(3591,9,'12345678',?,'blob')",
                             ("9" * 64,))
            self.con.commit()
            count = index_blobs.record_unavailable_sources(self.con)
        finally:
            index_blobs.BLOBS = old_blobs
        self.assertEqual(1, count)
        meta = self.con.execute("SELECT * FROM version_manifests").fetchone()
        self.assertEqual("unavailable", meta["validation_state"])
        self.assertEqual({}, self.payload()["files"])

    def test_build_site_shows_unparsed_known_revisions_with_unknown_counts(self):
        self.con.execute("INSERT INTO files VALUES(3591,7,'12345678',?,'blob')",
                         ("7" * 64,))
        self.con.commit()
        rows = build_site.versions_of(self.con, 3591)
        self.assertEqual([{"v": 7, "app": None, "ver": None, "nodes": None,
                           "files": None, "roots": [], "manifest_state": "unknown",
                           "source_state": "unavailable"}], rows)

    def test_depot_version_summaries_are_labeled_exact_legacy_or_unknown(self):
        self.ingest(6, [s2_parse.ManifestNode(1, 1, "exact.bin", 4)])
        self.con.execute("INSERT INTO versions VALUES(3591,5,3591,5,2,1,'legacy')")
        self.con.commit()
        rows = {row["v"]: row for row in build_site.versions_of(self.con, 3591)}
        self.assertEqual("exact", rows[6]["manifest_state"])
        self.assertEqual("valid", rows[6]["source_state"])
        self.assertEqual("legacy", rows[5]["manifest_state"])
        self.assertEqual("unavailable", rows[5]["source_state"])

    def test_path_union_keeps_legacy_search_index_but_is_not_vfiles_source(self):
        for version, size in ((6, 100), (8, 250)):
            self.ingest(version, [s2_parse.ManifestNode(4, 1, "asset.bin", size)])
        union = self.con.execute("SELECT size,first_ver,last_ver FROM depot_paths "
                                 "WHERE depot=3591 AND path='asset.bin'").fetchone()
        self.assertEqual((250, 6, 8), tuple(union))
        self.assertEqual([100, 250], [self.payload()["files"][str(v)][0][1]
                                      for v in (6, 8)])

    def test_invalid_source_name_digest_does_not_override_computed_digest(self):
        fn = f"3591_7_00000000_{'f' * 64}.blob"
        data = b"raw"
        result = index_blobs.prepare_blob(
            3591, 7, fn, data,
            parser=lambda _raw: manifest([s2_parse.ManifestNode(1, 1, "x", 1)]))
        self.assertNotEqual(result["manifest"][4], result["manifest"][5])
        self.assertEqual("hash_mismatch", result["manifest"][9])

    def test_unverified_or_hash_mismatched_source_does_not_pollute_union(self):
        self.con.execute("INSERT INTO versions VALUES(3591,7,3591,7,2,1,'legacy')")
        data = b"manifest bytes"
        wrong_fn = f"3591_7_00000000_{'0' * 64}.blob"
        wrong = index_blobs.prepare_blob(
            3591, 7, wrong_fn, data,
            parser=lambda _raw: manifest([s2_parse.ManifestNode(1, 1, "bad.bin", 6)]))
        index_blobs._store_prepared(self.con, [wrong])
        row = self.con.execute("SELECT app_id,ver_id,file_count FROM versions "
                               "WHERE depot=3591 AND version=7").fetchone()
        self.assertEqual((3591, 7, 1), tuple(row))
        self.assertIsNone(self.con.execute(
            "SELECT 1 FROM depot_paths WHERE path='bad.bin'").fetchone())

        no_digest_fn = "3591_8_00000000.blob"
        unverified = index_blobs.prepare_blob(
            3591, 8, no_digest_fn, data,
            parser=lambda _raw: manifest([s2_parse.ManifestNode(2, 1, "unknown.bin", 7)],
                                         ver_id=8))
        self.assertEqual("unverified", unverified["manifest"][9])
        index_blobs._store_prepared(self.con, [unverified])
        self.assertIsNone(self.con.execute(
            "SELECT 1 FROM depot_paths WHERE path='unknown.bin'").fetchone())

    def test_name_offsets_cannot_escape_declared_string_table(self):
        strings = b"\0x\0f\0"
        nodes = [(0, 1, 0, 0, 0xFFFFFFFF, 0, 1),
                 (1, 1, 0, 0, 0, 0, 2),
                 (3, 1, 1, 1, 1, 0, 0)]
        data = bytearray(raw_manifest(nodes, strings))
        trailer = b"outside\0"
        struct.pack_into("<I", data, 24, len(data) + len(trailer))
        struct.pack_into("<I", data, 56 + 2 * 28, len(strings))
        data.extend(trailer)
        a = b = 0
        for offset, value in enumerate(data):
            if 48 <= offset < 56:
                value = 0
            a = (a + value) % 65521
            b = (b + a) % 65521
        struct.pack_into("<I", data, 52, (b << 16) | a)
        with self.assertRaisesRegex(ValueError, "name offset"):
            s2_parse.parse_manifest(bytes(data))

    def test_manifest_identity_must_match_depot_and_revision(self):
        data = b"manifest with wrong identity"
        fn = f"3591_7_00000000_{hashlib.sha256(data).hexdigest()}.blob"
        wrong = manifest([s2_parse.ManifestNode(1, 1, "x", 1)], ver_id=8)
        prepared = index_blobs.prepare_blob(3591, 7, fn, data,
                                            parser=lambda _raw: wrong)
        self.assertEqual("invalid", prepared["manifest"][9])
        self.assertIn("identity mismatch", prepared["manifest"][14])

    def test_extended_file_size_is_decoded_and_auxiliary_node_is_not_a_file(self):
        # Root, directory, file and auxiliary high-size node.
        strings = b"\0PlantsVsZombies\0large.dat\0"
        game = strings.index(b"PlantsVsZombies")
        large = strings.index(b"large.dat")
        nodes = [
            (0, 1, 0, 0, 0xFFFFFFFF, 0, 1),
            (game, 1, 0, 0, 0, 0, 2),
            (large, 0x80000005, 77, 1, 1, 0, 0),
            (0, 3, 0, 0, 0, 0, 0),
        ]
        parsed = s2_parse.parse_manifest(raw_manifest(nodes, strings))
        self.assertIsNotNone(parsed)
        files = parsed.file_nodes()
        self.assertEqual(1, len(files))
        self.assertEqual("PlantsVsZombies/large.dat", files[0].path)
        self.assertEqual((3 << 31) | 5, files[0].size)
        self.assertEqual(2, files[0].ordinal)
        self.assertEqual([], [node for node in files if node.ordinal == 3])

    def test_extended_size_maximum_signed_64_bit_boundary(self):
        strings = b"\0x\0f\0"
        nodes = [(0, 1, 0, 0, 0xFFFFFFFF, 0, 1),
                 (1, 1, 0, 0, 0, 0, 2),
                 (3, 0xFFFFFFFF, 1, 1, 1, 0, 0),
                 (0, 0xFFFFFFFF, 0, 0, 0, 0, 0)]
        parsed = s2_parse.parse_manifest(raw_manifest(nodes, strings))
        self.assertEqual(0x7FFFFFFFFFFFFFFF, parsed.file_nodes()[0].size)

    def test_size_encoding_at_two_and_four_gib_boundaries(self):
        strings = b"\0file.bin\0"
        for size in (0, 0x7FFFFFFF, 0x80000000, 0xFFFFFFFF,
                     0x100000000, 0x100000001):
            with self.subTest(size=size):
                nodes = [(0, 1, 0, 0, 0xFFFFFFFF, 0, 1),
                         (1, (size & 0x7FFFFFFF) | (0x80000000 if size > 0x7FFFFFFF else 0),
                          1, 1, 0, 0, 0)]
                if size > 0x7FFFFFFF:
                    nodes.append((0, size >> 31, 0, 0, 0, 0, 0))
                parsed = s2_parse.parse_manifest(raw_manifest(nodes, strings))
                self.assertEqual(size, parsed.file_nodes()[0].size)

    def test_missing_large_size_auxiliary_node_is_rejected(self):
        strings = b"\0x\0f\0"
        nodes = [(0, 1, 0, 0, 0xFFFFFFFF, 0, 1),
                 (1, 1, 0, 0, 0, 0, 2),
                 (3, 0x80000001, 1, 1, 1, 0, 0)]
        with self.assertRaisesRegex(ValueError, "auxiliary node is absent"):
            s2_parse.parse_manifest(raw_manifest(nodes, strings))

    def test_adjacent_extended_files_do_not_chain_and_unknown_flags_survive(self):
        strings = b"\0x\0a\0b\0"
        nodes = [
            (0, 1, 0, 0, 0xFFFFFFFF, 0, 1),
            (1, 2, 0, 0, 0, 0, 2),
            (3, 0x80000001, 10, 1, 1, 4, 0),
            (0, 2, 0, 0, 0, 0, 0),
            (5, 0x80000002, 11, 0xA500, 1, 0, 0),
            (0, 3, 0, 0, 0, 0, 0),
        ]
        parsed = s2_parse.parse_manifest(raw_manifest(nodes, strings, file_count=2))
        files = parsed.file_nodes()
        self.assertEqual(["x/a", "x/b"], [node.path for node in files])
        self.assertEqual([(2 << 31) | 1, (3 << 31) | 2], [node.size for node in files])
        self.assertEqual(0xA500, files[1].flags)

    def test_bad_name_offset_truncation_and_real_header_count_mismatch_fail(self):
        strings = b"\0x\0f\0"
        valid = [(0, 1, 0, 0, 0xFFFFFFFF, 0, 1),
                 (1, 1, 0, 0, 0, 0, 2),
                 (3, 1, 1, 1, 1, 0, 0)]
        bad_offset = list(valid)
        bad_offset[2] = (99, 1, 1, 1, 1, 0, 0)
        with self.assertRaisesRegex(ValueError, "name offset"):
            s2_parse.parse_manifest(raw_manifest(bad_offset, strings))
        good_bytes = raw_manifest(valid, strings)
        with self.assertRaisesRegex(ValueError, "binary size"):
            s2_parse.parse_manifest(good_bytes[:-1])
        with self.assertRaisesRegex(ValueError, "file count"):
            s2_parse.parse_manifest(raw_manifest(valid, strings, file_count=0))

    def test_manifest_node_limit_rejects_before_allocating_node_arrays(self):
        header = bytearray(struct.pack("<14I", 3, 3591, 7,
            s2_parse.MAX_MANIFEST_NODES + 1, 0, 0x8000, 56, 0,
            0, 0, 0, 0, 0, 0))
        a = b = 0
        for offset, value in enumerate(header):
            if 48 <= offset < 56:
                value = 0
            a = (a + value) % 65521
            b = (b + a) % 65521
        struct.pack_into("<I", header, 52, (b << 16) | a)
        with self.assertRaisesRegex(ValueError, "node or file count"):
            s2_parse.parse_manifest(bytes(header))

    def test_bad_tree_parent_count_and_checksum_are_rejected(self):
        strings = b"\0x\0f\0"
        good = [(0, 1, 0, 0, 0xFFFFFFFF, 0, 1),
                (1, 1, 0, 0, 0, 0, 2),
                (3, 1, 1, 1, 1, 0, 0)]
        invalid_parent = list(good)
        invalid_parent[2] = (3, 1, 1, 1, 0xFFFFFFFE, 0, 0)
        with self.assertRaisesRegex(ValueError, "parent"):
            s2_parse.parse_manifest(raw_manifest(invalid_parent, strings))
        invalid_children = list(good)
        invalid_children[0] = (0, 0, 0, 0, 0xFFFFFFFF, 0, 1)
        with self.assertRaisesRegex(ValueError, "child count"):
            s2_parse.parse_manifest(raw_manifest(invalid_children, strings))
        corrupted = bytearray(raw_manifest(good, strings))
        corrupted[-1] ^= 0x01
        with self.assertRaisesRegex(ValueError, "checksum"):
            s2_parse.parse_manifest(bytes(corrupted))

    def test_malformed_compressed_container_cannot_exceed_declared_output(self):
        packed = zlib.compress(b"A" * 65536)
        raw = struct.pack("<HQQH", 0x4301, len(packed), 1, 6) + packed
        with self.assertRaisesRegex(ValueError, "does not match its declaration"):
            s2_parse._decompress(raw)

    def test_versioned_index_keeps_original_node_ordinal_after_large_size_node(self):
        strings = b"\0PlantsVsZombies\0large.dat\0"
        game = strings.index(b"PlantsVsZombies")
        large = strings.index(b"large.dat")
        nodes = [
            (0, 1, 0, 0, 0xFFFFFFFF, 0, 1),
            (game, 1, 0, 0, 0, 0, 2),
            (large, 0x80000002, 77, 1, 1, 0, 0),
            (0, 4, 0, 0, 0, 0, 0),
        ]
        data = wrap_blob(raw_manifest(nodes, strings))
        fn = f"3591_7_00000000_{hashlib.sha256(data).hexdigest()}.blob"
        prepared = index_blobs.prepare_blob(3591, 7, fn, data)
        index_blobs._store_prepared(self.con, [prepared])
        row = self.con.execute("SELECT record_ordinal,size FROM version_file_records").fetchone()
        self.assertEqual((2, (4 << 31) | 2), tuple(row))

    def test_invalid_utf8_manifest_name_is_not_replaced_silently(self):
        strings = b"\0game\0bad\xffname\0"
        nodes = [
            (0, 0, 0, 0, 0xFFFFFFFF, 0xFFFFFFFF, 1),
            (1, 1, 0, 0, 0, 0xFFFFFFFF, 2),
            (6, 1, 77, 1, 1, 0xFFFFFFFF, 0xFFFFFFFF),
        ]
        node_bytes = b"".join(struct.pack("<7I", *node) for node in nodes)
        total_size = 56 + len(node_bytes) + len(strings)
        header = struct.pack("<14I", 3, 3591, 7, len(nodes), 1, 0x8000,
                             total_size, len(strings), 0, 0, 0, 0, 0, 0)
        data = wrap_blob(header + node_bytes + strings)
        fn = f"3591_7_00000000_{hashlib.sha256(data).hexdigest()}.blob"
        prepared = index_blobs.prepare_blob(3591, 7, fn, data)
        self.assertEqual("invalid", prepared["manifest"][9])
        self.assertEqual([], prepared["records"])

    def test_vfiles_writer_emits_plain_and_gzip_static_artifact(self):
        self.ingest(7, [s2_parse.ManifestNode(1, 1, "asset.bin", 9)])
        folder = ROOT / "tests" / ".gzip_test_output"
        folder.mkdir(exist_ok=True)
        try:
            path = folder / "3591.json"
            build_vfiles._write_json(str(path), self.payload())
            plain = json.loads(path.read_text(encoding="utf-8"))
            compressed_bytes = path.with_suffix(".json.gz").read_bytes()
            compressed = json.loads(gzip.decompress(compressed_bytes))
            self.assertEqual(plain, compressed)
            build_vfiles._write_json(str(path), self.payload())
            self.assertEqual(compressed_bytes, path.with_suffix(".json.gz").read_bytes())
        finally:
            for output in folder.iterdir():
                output.unlink()
            folder.rmdir()


if __name__ == "__main__":
    unittest.main()

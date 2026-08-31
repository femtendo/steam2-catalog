"""Steam2 blob + manifest parser.

Pure-Python port of the Steam2 .blob container and embedded file manifest,
mirroring blobng.hpp / steam2ng.hpp / the C# reference in
extremebleem/steam2_downloader (BlobFormat.cs, ManifestFormat.cs).

A .blob is the *metadata* for one depot version. It holds a file manifest
(human-readable paths + sizes + file ids) and needs NO decryption key --
unlike the .dat payloads. This is the layer that makes full-archive content
search possible without downloading 12 TB of game data.
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field
from typing import Optional


MAGIC_PLAIN = 0x5001
MAGIC_COMPRESSED = 0x4301


def _read_keys(raw: bytes) -> dict[int, bytes]:
    """Read the key/value record list of a blob, unwrapping compression if present.

    Plain layout: u16 magic 0x5001, u32 totalSize, u32 slackSize, then records
    from offset 10: u16 keySize, u32 valueSize, key bytes, value bytes.
    Compressed wrapper: u16 magic 0x4301, u64 packed, u64 unpacked, u16 level,
    then a zlib stream from offset 20.
    """
    if len(raw) < 2:
        return {}

    magic = struct.unpack_from("<H", raw, 0)[0]
    if magic == MAGIC_COMPRESSED:
        raw = _decompress(raw)
        if len(raw) < 2:
            return {}

    magic = struct.unpack_from("<H", raw, 0)[0]
    if magic != MAGIC_PLAIN or len(raw) < 10:
        return {}

    total_size = struct.unpack_from("<I", raw, 2)[0]
    end = min(total_size, len(raw))

    result: dict[int, bytes] = {}
    pos = 10
    while pos + 6 <= end:
        key_size = struct.unpack_from("<H", raw, pos)[0]
        value_size = struct.unpack_from("<I", raw, pos + 2)[0]
        pos += 6
        if pos + key_size + value_size > end:
            break
        if key_size == 4:
            key = struct.unpack_from("<I", raw, pos)[0]
            result[key] = raw[pos + key_size : pos + key_size + value_size]
        pos += key_size + value_size
    return result


def _decompress(raw: bytes) -> bytes:
    if len(raw) < 20:
        raise ValueError("compressed blob header truncated")
    unpacked = struct.unpack_from("<Q", raw, 10)[0]
    if unpacked > 0x7FFFFFFF:
        raise ValueError("unpacked size too large")
    return zlib.decompress(raw[20:])


@dataclass
class BlobInfo:
    format_code: int
    parent_crc: Optional[int]
    dat_size: Optional[int]
    keys: dict[int, bytes] = field(default_factory=dict)


def parse_blob(raw: bytes) -> BlobInfo:
    keys = _read_keys(raw)
    format_code = 0
    if 0 in keys and len(keys[0]) == 4:
        format_code = struct.unpack("<I", keys[0])[0]
    parent_crc = None
    if 12 in keys and len(keys[12]) == 4:
        parent_crc = struct.unpack("<I", keys[12])[0]
    dat_size = None
    if 13 in keys:
        ds = keys[13]
        if len(ds) == 4:
            dat_size = struct.unpack("<I", ds)[0]
        elif len(ds) == 8:
            dat_size = struct.unpack("<Q", ds)[0]
    return BlobInfo(format_code, parent_crc, dat_size, keys)


@dataclass
class ManifestNode:
    file_id: int
    flags: int
    path: str
    size: int


@dataclass
class Manifest:
    app_id: int
    ver_id: int
    node_count: int
    file_count: int
    roots: list[str]
    nodes: list[ManifestNode]

    def file_nodes(self) -> list[ManifestNode]:
        return [n for n in self.nodes if n.flags != 0 and n.path]


HEADER_SIZE = 56  # 14 x u32
NODE_SIZE = 28    # 7 x u32


def _read_string(m: bytes, table_start: int, offset: int) -> str:
    start = table_start + offset
    if start < 0 or start >= len(m):
        return ""
    end = start
    while end < len(m) and m[end] != 0:
        end += 1
    return m[start:end].decode("utf-8", errors="replace")


def manifest_from_blob(blob_bytes: bytes) -> Optional[Manifest]:
    outer = _read_keys(blob_bytes)
    if 3 not in outer or not outer[3]:
        return None
    inner = _read_keys(outer[3])
    if 0 not in inner or len(inner[0]) < HEADER_SIZE:
        return None
    return parse_manifest(inner[0])


def parse_manifest(m: bytes) -> Optional[Manifest]:
    if len(m) < HEADER_SIZE:
        return None

    version = struct.unpack_from("<I", m, 0)[0]
    if version not in (3, 4):
        return None

    app_id = struct.unpack_from("<I", m, 4)[0]
    ver_id = struct.unpack_from("<I", m, 8)[0]
    node_count = struct.unpack_from("<I", m, 12)[0]
    file_count = struct.unpack_from("<I", m, 16)[0]
    binary_size = struct.unpack_from("<I", m, 24)[0]

    if binary_size != len(m):
        return None

    nodes_end = HEADER_SIZE + node_count * NODE_SIZE
    if node_count == 0 or nodes_end > len(m):
        return None

    table_start = nodes_end

    roots: list[str] = []
    nodes: list[ManifestNode] = []

    name_offsets = [0] * node_count
    sizes = [0] * node_count
    file_ids = [0] * node_count
    flags = [0] * node_count
    parents = [0] * node_count

    for i in range(node_count):
        at = HEADER_SIZE + i * NODE_SIZE
        name_offsets[i] = struct.unpack_from("<I", m, at)[0]
        sizes[i] = struct.unpack_from("<I", m, at + 4)[0]
        file_ids[i] = struct.unpack_from("<I", m, at + 8)[0]
        flags[i] = struct.unpack_from("<I", m, at + 12)[0]
        parents[i] = struct.unpack_from("<I", m, at + 16)[0]

    for i in range(node_count):
        if parents[i] == 0:
            name = _read_string(m, table_start, name_offsets[i])
            if name.strip():
                roots.append(name)
                if len(roots) >= 32:
                    break

    # Resolve full paths.
    for i in range(node_count):
        parts: list[str] = []
        cur = i
        guard = 0
        while parents[cur] != 0xFFFFFFFF:
            parts.append(_read_string(m, table_start, name_offsets[cur]))
            cur = parents[cur]
            if cur >= node_count:
                break
            guard += 1
            if guard > 256:
                break
        parts.reverse()
        nodes.append(ManifestNode(file_ids[i], flags[i], "/".join(parts), sizes[i]))

    return Manifest(app_id, ver_id, node_count, file_count, roots, nodes)


def label_from_roots(roots: list[str]) -> str:
    if not roots:
        return ""
    if len(roots) == 1:
        return roots[0]
    folders = [r for r in roots if "." not in r]
    if len(folders) == 1:
        return folders[0]
    head = ", ".join(roots[:3])
    return head + (f" (+{len(roots) - 3})" if len(roots) > 3 else "")


if __name__ == "__main__":
    import sys
    for path in sys.argv[1:]:
        data = open(path, "rb").read()
        try:
            m = manifest_from_blob(data)
            if m is None:
                print(f"{path}: no manifest")
                continue
            print(f"{path}: app={m.app_id} ver={m.ver_id} "
                  f"nodes={m.node_count} files={m.file_count}")
            print(f"  roots: {m.roots}")
            files = m.file_nodes()
            print(f"  {len(files)} file paths; sample:")
            for n in files[:10]:
                print(f"    {n.size:>12}  {n.path}")
        except Exception as e:
            print(f"{path}: ERROR {e!r}")

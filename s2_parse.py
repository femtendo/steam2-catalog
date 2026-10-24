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
from array import array
from dataclasses import dataclass, field
from typing import Optional


MAGIC_PLAIN = 0x5001
MAGIC_COMPRESSED = 0x4301
MAX_METADATA_SIZE = 128 * 1024 * 1024
MAX_MANIFEST_NODES = 500_000
MAX_PATH_DEPTH = 1024
MAX_PATH_CHARS = 32_767
MAX_EXPANDED_PATH_CHARS = 64 * 1024 * 1024
NO_NODE = 0xFFFFFFFF
LARGE_FILE_FLAG = 0x80000000
ADLER_MODULUS = 65521


class ResourceLimitError(ValueError):
    """Metadata may be legitimate but exceeds this parser's defensive budget."""


def _read_keys(raw: bytes) -> dict[int, bytes]:
    """Read the key/value record list of a blob, unwrapping compression if present.

    Plain layout: u16 magic 0x5001, u32 totalSize, u32 slackSize, then records
    from offset 10: u16 keySize, u32 valueSize, key bytes, value bytes.
    Compressed wrapper: u16 magic 0x4301, u64 packed, u64 unpacked, u16 level,
    then a zlib stream from offset 20.
    """
    if len(raw) > MAX_METADATA_SIZE:
        raise ResourceLimitError(f"blob metadata exceeds byte limit {MAX_METADATA_SIZE}")
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

    total_size, slack_size = struct.unpack_from("<II", raw, 2)
    if total_size != len(raw) or slack_size > total_size - 10:
        raise ValueError("plain blob size is invalid")
    end = total_size - slack_size

    result: dict[int, bytes] = {}
    pos = 10
    while pos + 6 <= end:
        key_size = struct.unpack_from("<H", raw, pos)[0]
        value_size = struct.unpack_from("<I", raw, pos + 2)[0]
        pos += 6
        if pos + key_size + value_size > end:
            raise ValueError("blob entry exceeds the container boundary")
        if key_size == 4:
            key = struct.unpack_from("<I", raw, pos)[0]
            if key in result:
                raise ValueError(f"duplicate blob key {key}")
            result[key] = raw[pos + key_size : pos + key_size + value_size]
        pos += key_size + value_size
    if pos != end:
        raise ValueError("blob entries do not end at the slack area")
    return result


def _decompress(raw: bytes) -> bytes:
    if len(raw) < 20:
        raise ValueError("compressed blob header truncated")
    packed = struct.unpack_from("<Q", raw, 2)[0]
    unpacked = struct.unpack_from("<Q", raw, 10)[0]
    if packed not in (len(raw) - 20, len(raw)):
        raise ValueError("compressed blob size is invalid")
    if unpacked > MAX_METADATA_SIZE:
        raise ResourceLimitError(f"unpacked size too large: byte limit {MAX_METADATA_SIZE}")
    decoder = zlib.decompressobj()
    decoded = decoder.decompress(raw[20:], int(unpacked) + 1)
    if (len(decoded) != unpacked or decoder.unconsumed_tail or
            decoder.unused_data or not decoder.eof):
        raise ValueError("compressed blob size does not match its declaration")
    return decoded


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
    # Original node-array position, including gaps for auxiliary size nodes.
    ordinal: Optional[int] = None


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


def _read_string(m: bytes, table_start: int, table_size: int, offset: int) -> str:
    start = table_start + offset
    table_end = table_start + table_size
    if start < table_start or start >= table_end:
        raise ValueError("manifest name offset is invalid")
    # Bound work and allocation even if many nodes reference one oversized name.
    scan_end = min(table_end, start + MAX_PATH_CHARS * 4 + 1)
    end = m.find(b"\0", start, scan_end)
    if end < 0 and scan_end < table_end:
        raise ResourceLimitError(f"manifest component exceeds length limit {MAX_PATH_CHARS}")
    if end < 0:
        raise ValueError("manifest name has no terminator")
    component = m[start:end].decode("utf-8", errors="strict")
    if len(component) > MAX_PATH_CHARS:
        raise ResourceLimitError(f"manifest component exceeds length limit {MAX_PATH_CHARS}")
    return component


def _manifest_adler(data: bytes) -> int:
    """Compute the Steam2 manifest Adler checksum (checksum header zeroed)."""
    a = b = 0
    for start in range(0, len(data), 5552):
        for offset, value in enumerate(data[start : start + 5552], start):
            if 48 <= offset < 56:
                value = 0
            a += value
            b += a
        a %= ADLER_MODULUS
        b %= ADLER_MODULUS
    return (b << 16) | a


def _read_node(m: bytes, index: int) -> tuple[int, int, int, int, int, int, int]:
    return struct.unpack_from("<7I", m, HEADER_SIZE + index * NODE_SIZE)


def _validate_topology(m: bytes, table_start: int, table_size: int, node_count: int,
                       auxiliary: bytearray) -> tuple[list[str], list[str]]:
    root_index = -1
    components = [""] * node_count
    component_chars = 0
    for index in range(node_count):
        node = _read_node(m, index)
        if auxiliary[index]:
            continue
        name_offset, _, _, flags, parent, _, _ = node
        component = _read_string(m, table_start, table_size, name_offset)
        component_chars += len(component)
        if component_chars > MAX_EXPANDED_PATH_CHARS:
            raise ResourceLimitError(f"manifest components exceed character limit {MAX_EXPANDED_PATH_CHARS}")
        components[index] = component
        if parent == NO_NODE:
            if root_index >= 0:
                raise ValueError("manifest contains more than one root node")
            root_index = index
    if root_index != 0:
        raise ValueError("manifest root topology is invalid")
    root = _read_node(m, root_index)
    if root[3] != 0 or root[5] != 0 or components[root_index] != "":
        raise ValueError("manifest root node is invalid")

    for index in range(node_count):
        node = _read_node(m, index)
        if auxiliary[index] or index == root_index:
            continue
        component = components[index]
        if not component or component in (".", ".."):
            raise ValueError("manifest path component is empty or relative")
        if "/" in component or "\\" in component or any(ord(ch) < 32 for ch in component):
            raise ValueError("manifest path component contains a separator or control character")
        parent = node[4]
        if parent >= node_count or auxiliary[parent] or _read_node(m, parent)[3] != 0:
            raise ValueError("manifest parent is not a directory")

    linked = bytearray(node_count)
    linked[root_index] = 1
    for parent_index in range(node_count):
        parent = _read_node(m, parent_index)
        if auxiliary[parent_index]:
            continue
        flags = parent[3]
        first_child = parent[6]
        if flags != 0:
            if first_child != 0:
                raise ValueError("manifest file node has a child")
            continue
        child = first_child
        child_count = 0
        while child != 0:
            if child >= node_count:
                raise ValueError("manifest child index is invalid")
            if auxiliary[child] or linked[child]:
                raise ValueError("manifest sibling topology contains a cycle or duplicate")
            linked[child] = 1
            child_node = _read_node(m, child)
            if child_node[4] != parent_index:
                raise ValueError("manifest child has an inconsistent parent")
            child_count += 1
            if child_count > node_count:
                raise ValueError("manifest sibling topology contains a cycle")
            child = child_node[5]
        if parent[1] != child_count:
            raise ValueError("manifest directory child count is inconsistent")
    if any(not auxiliary[i] and not linked[i] for i in range(node_count)):
        raise ValueError("manifest node is absent from child topology")

    # Resolve paths iteratively so malformed deep trees cannot exhaust Python's
    # call stack. The depth and aggregate limits mirror the reference parser.
    paths: list[Optional[str]] = [None] * node_count
    depths = array("I", [0]) * node_count
    paths[root_index] = ""
    total_path_chars = 0
    for index in range(node_count):
        node = _read_node(m, index)
        if auxiliary[index] or paths[index] is not None:
            continue
        chain = []
        seen = set()
        current = index
        while paths[current] is None:
            if current in seen:
                raise ValueError("manifest parent graph contains a cycle")
            seen.add(current)
            chain.append(current)
            if len(chain) > MAX_PATH_DEPTH:
                raise ResourceLimitError(f"manifest path exceeds depth limit {MAX_PATH_DEPTH}")
            current_node = _read_node(m, current)
            parent = current_node[4]
            if parent == NO_NODE:
                if current != root_index:
                    raise ValueError("manifest node has no root parent")
                paths[current] = ""
                break
            if parent >= node_count or auxiliary[parent] or _read_node(m, parent)[3] != 0:
                raise ValueError("manifest parent index is invalid")
            current = parent
        for child in reversed(chain):
            if child == root_index:
                continue
            parent = _read_node(m, child)[4]
            parent_path = paths[parent]
            if parent_path is None:
                raise ValueError("manifest parent path could not be resolved")
            depth = depths[parent] + 1
            if depth > MAX_PATH_DEPTH:
                raise ResourceLimitError(f"manifest path exceeds depth limit {MAX_PATH_DEPTH}")
            depths[child] = depth
            path_length = len(parent_path) + len(components[child]) + bool(parent_path)
            if path_length > MAX_PATH_CHARS:
                raise ResourceLimitError(f"manifest path exceeds length limit {MAX_PATH_CHARS}")
            total_path_chars += path_length
            if total_path_chars > MAX_EXPANDED_PATH_CHARS:
                raise ResourceLimitError(f"expanded manifest paths exceed character limit {MAX_EXPANDED_PATH_CHARS}")
            paths[child] = (components[child] if not parent_path else
                            parent_path + "/" + components[child])

    roots = []
    child = root[6]
    while child != 0:
        roots.append(components[child])
        child = _read_node(m, child)[5]
    return [path or "" for path in paths], roots


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
    if len(m) > MAX_METADATA_SIZE:
        raise ResourceLimitError(f"manifest exceeds metadata byte limit {MAX_METADATA_SIZE}")

    version = struct.unpack_from("<I", m, 0)[0]
    if version not in (3, 4):
        return None

    app_id = struct.unpack_from("<I", m, 4)[0]
    ver_id = struct.unpack_from("<I", m, 8)[0]
    node_count = struct.unpack_from("<I", m, 12)[0]
    file_count = struct.unpack_from("<I", m, 16)[0]
    block_size = struct.unpack_from("<I", m, 20)[0]
    binary_size = struct.unpack_from("<I", m, 24)[0]
    string_table_size = struct.unpack_from("<I", m, 28)[0]
    stored_checksum = struct.unpack_from("<I", m, 52)[0]

    if block_size != 0x8000:
        raise ValueError("manifest compression block size is invalid")
    if binary_size != len(m):
        raise ValueError("manifest binary size is invalid")
    if stored_checksum != _manifest_adler(m):
        raise ValueError("manifest Adler checksum is invalid")
    if node_count > MAX_MANIFEST_NODES:
        raise ResourceLimitError(f"manifest node or file count exceeds node limit {MAX_MANIFEST_NODES}")
    if node_count == 0 or file_count > node_count:
        raise ValueError("manifest node or file count is invalid")

    nodes_end = HEADER_SIZE + node_count * NODE_SIZE
    if nodes_end > len(m) or string_table_size > len(m) - nodes_end:
        raise ValueError("manifest nodes or string table are truncated")

    table_start = nodes_end
    auxiliary = bytearray(node_count)
    sizes = array("Q", [0]) * node_count
    for i in range(node_count):
        sizes[i] = _read_node(m, i)[1]

    # A physical file size with bit 31 set stores its upper size bits in the
    # following auxiliary node. This layout is also used by Steam2.Extractor's
    # ManifestParser; the node remains present in the raw node count but does
    # not become a path record.
    for i in range(node_count):
        if auxiliary[i] or _read_node(m, i)[3] == 0:
            continue
        raw_size = sizes[i]
        if raw_size & LARGE_FILE_FLAG:
            extra_index = i + 1
            if extra_index >= node_count or auxiliary[extra_index]:
                raise ValueError("large file size auxiliary node is absent")
            auxiliary[extra_index] = 1
            sizes[i] = (raw_size & ~LARGE_FILE_FLAG) | (sizes[extra_index] << 31)

    physical_file_count = sum(
        1 for index in range(node_count)
        if not auxiliary[index] and _read_node(m, index)[3] != 0)
    if physical_file_count != file_count:
        raise ValueError("manifest file count is inconsistent")

    paths, roots = _validate_topology(m, table_start, string_table_size,
                                      node_count, auxiliary)
    nodes: list[ManifestNode] = []
    for i in range(node_count):
        node = _read_node(m, i)
        if auxiliary[i]:
            nodes.append(ManifestNode(node[2], 0, "", 0, i))
            continue
        nodes.append(ManifestNode(node[2], node[3], paths[i], sizes[i], i))

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

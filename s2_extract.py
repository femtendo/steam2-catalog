"""Steam2 chain resolver + extractor (Python port).

Resolves the delta chain for a depot version from local blob/dat files, builds
the file id table, and rebuilds files from the dats. Ported from the reference
C++ extractor and the C# in extremebleem/steam2_downloader.

Files are stored as deltas: extracting version N needs every blob up to N, plus
the dats that the target version's file ids actually reference.
"""
from __future__ import annotations

import hashlib
import os
import re
import struct
import sys
import zlib
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import s2_parse  # noqa: E402

BLOCK_SIZE = 0x8000
MAX_CHUNK = 0x10000
TABLE_MAGIC = 0x34457234


# ---------------------------------------------------------------- keys

def load_depot_keys(path: str) -> dict[int, bytes]:
    keys = {}
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or " " not in line:
                continue
            depot_s, hexkey = line.split(" ", 1)
            if depot_s.isdigit() and len(hexkey) == 32:
                try:
                    keys[int(depot_s)] = bytes.fromhex(hexkey)
                except ValueError:
                    pass
    return keys


# ---------------------------------------------------------------- file id table

@dataclass
class Block:
    compressed_size: int
    checksum: int


@dataclass
class FileLocation:
    file_mode: int
    offset: int
    file_size: int
    part: int
    blocks: list[Block]


def parse_file_table(t: bytes, part: int) -> dict[int, FileLocation]:
    if len(t) < 0x20:
        raise ValueError("file id table truncated")
    u32 = lambda at: struct.unpack_from("<I", t, at)[0]
    u64 = lambda at: struct.unpack_from("<Q", t, at)[0]

    magic = u32(0)
    version = u32(4)
    num_file_blocks = u32(8)
    num_items = u32(12)
    offset1 = u32(16)
    offset2 = u32(20)
    block_size = u32(24)
    largest_num_blocks = u32(28)

    if magic != TABLE_MAGIC:
        raise ValueError("file id table: bad magic")
    if block_size != BLOCK_SIZE:
        raise ValueError("file id table: bad block size")
    if version not in (0, 1):
        raise ValueError(f"file id table: version {version}")
    if offset1 != 0x20 or offset2 != 0x20 + 0x10 * num_file_blocks:
        raise ValueError("file id table: bad offsets")

    pos = 0x20
    blocks = []
    for _ in range(num_file_blocks):
        if pos + 16 > len(t):
            raise ValueError("file id table: block list truncated")
        blocks.append((u32(pos), u32(pos + 4), u32(pos + 8)))
        pos += 16

    result: dict[int, FileLocation] = {}
    files_seen = 0
    max_blocks = 0

    for start, count, off in blocks:
        if pos != off:
            raise ValueError("file id table: reader drifted")
        files_seen += count
        for f in range(count):
            loc = FileLocation(0, 0, 0, part, [])
            if version == 0:
                if pos + 12 > len(t):
                    raise ValueError("truncated")
                loc.file_size = u32(pos)
                loc.offset = u32(pos + 4)
                pos += 8
            else:
                if pos + 20 > len(t):
                    raise ValueError("truncated")
                loc.file_size = u64(pos)
                loc.offset = u64(pos + 8)
                pos += 16

            packed = u32(pos)
            pos += 4
            loc.file_mode = packed >> 24
            block_count = packed & 0x00FFFFFF
            if loc.file_mode not in (1, 2, 3):
                raise ValueError(f"filemode {loc.file_mode}")
            max_blocks = max(max_blocks, block_count)

            if pos + block_count * 8 > len(t):
                raise ValueError("truncated block list")
            loc.blocks = [Block(u32(pos + j * 8), u32(pos + j * 8 + 4))
                          for j in range(block_count)]
            pos += block_count * 8

            result[start + f] = loc

    if pos + 4 > len(t) or u32(pos) != TABLE_MAGIC:
        raise ValueError("file id table: bad footer")
    if max_blocks != largest_num_blocks:
        raise ValueError("file id table: block count mismatch")
    if files_seen != num_items:
        raise ValueError("file id table: item count mismatch")
    return result


# ---------------------------------------------------------------- chain resolution

@dataclass
class Chain:
    dats: dict[int, str]
    blobs: dict[int, str]


NAME_RE = re.compile(r"^(\d+)_(\d+)_([0-9a-f]{8})_([0-9a-f]{64})\.(blob|dat)$")


def index_dir(directory: str, depot: int, ext: str) -> dict[int, list[tuple[int, str, str]]]:
    """version -> [(crc, sha, path), ...]"""
    result: dict[int, list] = {}
    if not os.path.isdir(directory):
        return result
    for fn in os.listdir(directory):
        m = NAME_RE.match(fn)
        if not m or m.group(5) != ext:
            continue
        if int(m.group(1)) != depot:
            continue
        result.setdefault(int(m.group(2)), []).append(
            (m.group(3), m.group(4), os.path.join(directory, fn)))
    for v in result:
        result[v].sort()
    return result


def resolve_chain(blob_dir: str, dat_dir: str, depot: int, version: int,
                  blob_crc: str | None = None) -> Chain:
    blobs = index_dir(blob_dir, depot, "blob")
    dats = index_dir(dat_dir, depot, "dat")
    if not blobs:
        raise FileNotFoundError(f"no blobs for depot {depot}")
    if not dats:
        raise FileNotFoundError(f"no dats for depot {depot}")

    chain_dats: dict[int, str] = {}
    chain_blobs: dict[int, str] = {}

    if not blob_crc:
        for v, lst in blobs.items():
            if v > version:
                continue
            if len(lst) > 1:
                raise ValueError(f"version {v} has {len(lst)} blobs — reset; pick a blob crc")
            chain_blobs[v] = lst[0][2]
        for v, lst in dats.items():
            if v > version:
                continue
            if len(lst) > 1:
                raise ValueError(f"version {v} has {len(lst)} dats — reset; pick a blob crc")
            chain_dats[v] = lst[0][2]
        for v in range(version, -1, -1):
            if v not in chain_blobs:
                raise FileNotFoundError(f"missing blob for version {v}")
        return Chain(chain_dats, chain_blobs)

    heads = blobs.get(version)
    if not heads:
        raise FileNotFoundError(f"no blob for version {version}")
    head = next((b for b in heads if b[0].lower() == blob_crc.lower()), None)
    if head is None:
        raise FileNotFoundError(f"no blob with crc {blob_crc} at version {version}")

    current = head
    at = version
    while True:
        chain_blobs[at] = current[2]
        info = s2_parse.parse_blob(open(current[2], "rb").read())

        candidates = dats.get(at, [])
        if len(candidates) == 1:
            chain_dats[at] = candidates[0][2]
        elif len(candidates) > 1:
            if info.dat_size is None:
                raise ValueError(f"version {at}: blob records no dat size")
            match = next((c for c in candidates
                          if os.path.getsize(c[2]) == info.dat_size), None)
            if match is None:
                raise FileNotFoundError(f"version {at}: no dat of size {info.dat_size}")
            chain_dats[at] = match[2]

        if at == 0:
            break
        if info.parent_crc is None:
            raise ValueError(f"version {at}: no parent crc")
        parents = blobs.get(at - 1)
        if not parents:
            raise FileNotFoundError(f"missing blob for version {at - 1}")
        current = next((b for b in parents if int(b[0], 16) == info.parent_crc), None)
        if current is None:
            raise FileNotFoundError(f"no blob with parent crc {info.parent_crc:08x} at v{at - 1}")
        at -= 1

    return Chain(chain_dats, chain_blobs)


# ---------------------------------------------------------------- chunk decode

def decrypt_cfb(buf: bytearray, offset: int, length: int, key: bytes) -> None:
    """AES-128-CFB, 128-bit segments, zero IV, in place."""
    try:
        from Crypto.Cipher import AES  # pycryptodome
        enc = AES.new(key, AES.MODE_ECB)
        feedback = bytearray(16)
        for at in range(0, length, 16):
            keystream = enc.encrypt(bytes(feedback))
            n = min(16, length - at)
            saved = bytes(buf[offset + at: offset + at + n])
            for i in range(n):
                buf[offset + at + i] ^= keystream[i]
            feedback[:n] = saved
    except ImportError:
        _decrypt_cfb_pure(buf, offset, length, key)


def _aes_ecb_encrypt_block(key: bytes, block: bytes) -> bytes:
    """Minimal AES-128 ECB single-block encrypt (pure Python)."""
    _SBOX = None  # lazily built
    global _AES_TABLES
    try:
        tables = _AES_TABLES
    except NameError:
        tables = _build_aes_tables()
        globals()["_AES_TABLES"] = tables
    sbox, rcon = tables
    w = _key_expansion(key, sbox, rcon)
    s = [block[i] for i in range(16)]
    _add_round_key(s, w[0])
    for rnd in range(1, 11):
        s = [sbox[b] for b in s]
        _shift_rows(s)
        if rnd != 10:
            _mix_columns(s)
        _add_round_key(s, w[rnd])
    return bytes(s)


def _build_aes_tables():
    # AES S-box
    p = 1
    q = 1
    sbox = [0] * 256
    sbox[0] = 0x63
    log = [0] * 256
    alog = [0] * 256
    x = 1
    for i in range(255):
        log[x] = i
        alog[i] = x
        x ^= (x << 1) ^ (0x11B if x & 0x80 else 0)
        x &= 0xFF
    def gmul(a, b):
        if a == 0 or b == 0:
            return 0
        return alog[(log[a] + log[b]) % 255]
    for i in range(1, 256):
        inv = alog[(255 - log[i]) % 255]
        s = inv
        s = rotr = ((s << 1) | (s >> 7)) & 0xFF
        s ^= inv
        r = s
        for _ in range(4):
            s = ((s << 1) | (s >> 7)) & 0xFF
            r ^= s
        sbox[i] = r ^ 0x63
    rcon = [0x01]
    for _ in range(9):
        rcon.append((rcon[-1] << 1) ^ (0x11B if rcon[-1] & 0x80 else 0) & 0xFF)
    return sbox, rcon


def _key_expansion(key: bytes, sbox, rcon):
    w = [list(key[i * 4:i * 4 + 4]) for i in range(4)]
    for i in range(4, 44):
        t = list(w[i - 1])
        if i % 4 == 0:
            t = t[1:] + t[:1]
            t = [sbox[b] for b in t]
            t[0] ^= rcon[i // 4 - 1]
        w.append([w[i - 4][j] ^ t[j] for j in range(4)])
    return [bytes(w[i * 4:i * 4 + 4]) for i in range(11)]


def _add_round_key(s, rk):
    for i in range(16):
        s[i] ^= rk[i]


def _shift_rows(s):
    # state is column-major: s[c*4+r]
    t = list(s)
    for r in range(1, 4):
        for c in range(4):
            s[c * 4 + r] = t[((c + r) % 4) * 4 + r]


def _mix_columns(s):
    def xt(a):
        a <<= 1
        if a & 0x100:
            a ^= 0x11B
        return a & 0xFF
    def gmul(a, b):
        p = 0
        for _ in range(8):
            if b & 1:
                p ^= a
            hi = a & 0x80
            a = (a << 1) & 0xFF
            if hi:
                a ^= 0x1B
            b >>= 1
        return p
    for c in range(4):
        a = s[c * 4:c * 4 + 4]
        t = a[0] ^ a[1] ^ a[2] ^ a[3]
        s[c * 4 + 0] = a[0] ^ t ^ xt(a[0] ^ a[1])
        s[c * 4 + 1] = a[1] ^ t ^ xt(a[1] ^ a[2])
        s[c * 4 + 2] = a[2] ^ t ^ xt(a[2] ^ a[3])
        s[c * 4 + 3] = a[3] ^ t ^ xt(a[3] ^ a[0])


def _decrypt_cfb_pure(buf: bytearray, offset: int, length: int, key: bytes) -> None:
    feedback = bytearray(16)
    for at in range(0, length, 16):
        keystream = _aes_ecb_encrypt_block(key, bytes(feedback))
        n = min(16, length - at)
        saved = bytes(buf[offset + at: offset + at + n])
        for i in range(n):
            buf[offset + at + i] ^= keystream[i]
        feedback[:n] = saved


def handle_chunk(chunk: bytes, file_mode: int, key: bytes | None) -> bytes:
    if file_mode == 0:
        return chunk
    if file_mode == 1:
        return zlib.decompress(chunk)
    if file_mode == 2:
        if len(chunk) < 8:
            raise ValueError("chunk too short for mode 2")
        buf = bytearray(chunk)
        decrypt_cfb(buf, 8, len(buf) - 8, key)  # type: ignore[arg-type]
        return zlib.decompress(bytes(buf[8:]))
    if file_mode == 3:
        buf = bytearray(chunk)
        decrypt_cfb(buf, 0, len(buf), key)  # type: ignore[arg-type]
        return bytes(buf)
    raise ValueError(f"unknown filemode {file_mode}")


# ---------------------------------------------------------------- extraction

def extract(depot: int, version: int, blob_dir: str, dat_dir: str, out_dir: str,
            keys: dict[int, bytes] | None = None, blob_crc: str | None = None,
            path_filter=None, verify_sha: dict[str, str] | None = None) -> dict:
    keys = keys or {}
    key = keys.get(depot)
    chain = resolve_chain(blob_dir, dat_dir, depot, version, blob_crc)

    file_ids: dict[int, FileLocation] = {}
    for v in sorted(chain.blobs):
        data = open(chain.blobs[v], "rb").read()
        for fid, loc in parse_file_table(
                s2_parse._read_keys(data)[4], v).items():
            loc.part = v
            file_ids[fid] = loc

    newest = open(chain.blobs[max(chain.blobs)], "rb").read()
    tree = s2_parse.parse_manifest(
        s2_parse._read_keys(s2_parse._read_keys(newest)[3])[0])
    if tree is None:
        raise ValueError("newest blob carries no manifest")

    wanted = [n for n in tree.nodes if n.flags != 0 and n.path]
    if path_filter:
        wanted = [n for n in wanted if path_filter(n.path)]

    missing_dats = {loc.part for n in wanted
                    if (loc := file_ids.get(n.file_id)) and loc.part not in chain.dats}
    if missing_dats:
        raise FileNotFoundError(f"missing dats for versions {sorted(missing_dats)}")

    encrypted = sum(1 for n in wanted
                    if (loc := file_ids.get(n.file_id)) and loc.file_mode in (2, 3))
    if encrypted and key is None:
        raise PermissionError(f"{encrypted} encrypted files and no key for depot {depot}")

    os.makedirs(out_dir, exist_ok=True)
    done = failed = 0
    bytes_written = 0
    dat_handles = {v: open(p, "rb") for v, p in chain.dats.items()}

    for n in wanted:
        loc = file_ids.get(n.file_id)
        if loc is None:
            failed += 1
            continue
        try:
            out_path = _safe_path(out_dir, n.path)
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            h = hashlib.sha256()
            with open(out_path, "wb") as out:
                offset = loc.offset
                for block in loc.blocks:
                    if block.compressed_size == 0:
                        continue
                    if block.compressed_size > MAX_CHUNK:
                        raise ValueError("chunk too large")
                    dat_handles[loc.part].seek(offset)
                    raw = dat_handles[loc.part].read(block.compressed_size)
                    plain = handle_chunk(raw, loc.file_mode, key)
                    out.write(plain)
                    h.update(plain)
                    offset += block.compressed_size
            if verify_sha and n.path in verify_sha:
                if h.hexdigest() != verify_sha[n.path]:
                    raise ValueError("sha256 mismatch vs manifest")
            done += 1
            bytes_written += loc.file_size
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"  FAILED {n.path}: {e}", file=sys.stderr)

    for f in dat_handles.values():
        f.close()
    return {"done": done, "failed": failed, "bytes": bytes_written,
            "chain_dats": sorted(chain.dats), "files": len(wanted)}


def _safe_path(out_dir: str, relative: str) -> str:
    parts = [p for p in re.split(r"[/\\]", relative) if p and p not in (".", "..")]
    clean = []
    for seg in parts:
        seg = re.sub(r'[<>:"|?*\x00-\x1f]', "_", seg).rstrip(" .")
        if seg:
            clean.append(seg)
    return os.path.join(out_dir, *clean)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("depot", type=int)
    ap.add_argument("version", type=int)
    ap.add_argument("--blobs", default="blobs")
    ap.add_argument("--dats", default="dats")
    ap.add_argument("--out", required=True)
    ap.add_argument("--keys", default="ref/steam2_downloader-main/Steam2Browser/depotkeys.txt")
    ap.add_argument("--blobcrc", default=None)
    ap.add_argument("--filter", default=None, help="regex on paths")
    ap.add_argument("--only", default=None, help="extract a single path (exact match)")
    args = ap.parse_args()

    keys = load_depot_keys(args.keys) if os.path.exists(args.keys) else {}
    only = {args.only} if args.only else None
    filt = (lambda p: p in only) if only else (lambda p: True)
    stats = extract(args.depot, args.version, args.blobs, args.dats, args.out,
                    keys=keys, blob_crc=args.blobcrc,
                    path_filter=(lambda p: __import__("re").search(args.filter, p)) if args.filter else filt)
    print(stats)

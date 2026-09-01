"""MDL+VVD+DX90.VTX -> GLB converter (vendored SourceIO loaders).

Usage: python3 build_preview_assets.py mdl <in.mdl> <out.glb> [name]
"""
import sys, struct, json
import numpy as np

sys.path.insert(0, '/Users/studio/Documents/valveleak/thirdparty')
from SourceIO.models.vtx.v7.vtx import Vtx
from SourceIO.models.vvd import Vvd
from SourceIO.library.utils import MemoryBuffer


def _chunk(b: bytes, typ: bytes) -> bytes:
    c = struct.pack('<I', len(b)) + typ + b
    pad = (4 - len(c) % 4) % 4
    return c + b'\0' * pad


def mdl_to_glb(mdl_path: str, out_path: str, name: str = "model") -> dict:
    vvd = Vvd.from_buffer(MemoryBuffer(open(mdl_path[:-4] + '.vvd', 'rb').read()))
    lod = vvd.lod_data[0]
    verts = lod['vertex'].astype('f4')
    norms = lod['normal'].astype('f4')
    n = len(verts)
    if n == 0:
        raise ValueError("no lod0 vertices")

    vtx = Vtx.from_buffer(MemoryBuffer(open(mdl_path[:-4] + '.dx90.vtx', 'rb').read()))
    indices = []
    for bp in vtx.body_parts:
        for model in bp.models:
            for lodv in model.model_lods[:1]:
                for mesh in lodv.meshes:
                    for sg in mesh.strip_groups:
                        for idx in sg.indices:
                            sv = sg.vertexes[int(idx)]
                            indices.append(int(sv['original_mesh_vertex_index'][0]))
    indices = np.array(indices, dtype='u4')
    if not len(indices):
        raise ValueError("no indices")
    # sanity: clamp
    if indices.max() >= n:
        indices = indices[indices < n]

    pos_bin = verts.tobytes()
    nrm_bin = norms.tobytes()
    idx_bin = indices.tobytes()
    j = {
        "asset": {"version": "2.0", "generator": "steam2-catalog"},
        "buffers": [{"byteLength": len(pos_bin) + len(nrm_bin) + len(idx_bin)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(pos_bin), "target": 34962},
            {"buffer": 0, "byteOffset": len(pos_bin), "byteLength": len(nrm_bin), "target": 34962},
            {"buffer": 0, "byteOffset": len(pos_bin) + len(nrm_bin), "byteLength": len(idx_bin), "target": 34963}],
        "accessors": [
            {"bufferView": 0, "componentType": 5126, "count": n, "type": "VEC3",
             "min": verts.min(0).tolist(), "max": verts.max(0).tolist()},
            {"bufferView": 1, "componentType": 5126, "count": n, "type": "VEC3"},
            {"bufferView": 2, "componentType": 5125, "count": len(indices), "type": "SCALAR"}],
        "meshes": [{"primitives": [{"attributes": {"POSITION": 0, "NORMAL": 1}, "indices": 2, "mode": 4}]}],
        "nodes": [{"mesh": 0, "name": name}],
        "scenes": [{"nodes": [0]}],
        "scene": 0}
    jb = json.dumps(j).encode()
    json_chunk = _chunk(jb, b'JSON')
    bin_chunk = _chunk(pos_bin + nrm_bin + idx_bin, b'BIN\0')
    glb = b'glTF' + struct.pack('<II', 2, 12 + len(json_chunk) + len(bin_chunk)) + json_chunk + bin_chunk
    open(out_path, 'wb').write(glb)
    return {"verts": int(n), "tris": int(len(indices) // 3), "bytes": len(glb)}


if __name__ == "__main__":
    print(mdl_to_glb(sys.argv[1], sys.argv[2], sys.argv[3] if len(sys.argv) > 3 else "model"))

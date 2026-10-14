"""Triangulate meshes, assign barycentric UVs, and export masked border buffers.

Run in Blender's Text Editor. Triangulation, UVs and masks use temporary copies;
source meshes and editing mode remain unchanged. Includes live Edit Mode edits.
Exports base mesh geometry with world-space
object transforms, without applying modifiers or changing coordinate axes.
VB: little-endian float32 XYZ + UV (20-byte stride). IB: uint32 indices.
Edges: one uint32 per triangle; bit i enables the edge opposite corner i.
Shared coplanar edges are hidden; boundaries, creases and non-manifold edges remain.
"""

# User settings: relative paths resolve beside this script (or the saved blend).
OUTPUT_DIRECTORY = './Exports'
COPLANAR_ANGLE_DEGREES = 0.1

import json
import math
import struct
from pathlib import Path

import bmesh
import bpy

BARY_UVS = ((0., 0.), (1., 0.), (0., 1.))


def output_directory():
    hint = Path(OUTPUT_DIRECTORY).expanduser()
    if hint.is_absolute():
        return hint.resolve()
    text = getattr(bpy.context.space_data, 'text', None)
    candidates = []
    if text is not None and not text.is_in_memory and text.filepath:
        candidates.append(Path(bpy.path.abspath(text.filepath)))
    if globals().get('__file__'):
        candidates.append(Path(__file__))
    for path in candidates:
        if path.suffix.lower() == '.py' and path.is_file():
            return (path.resolve().parent / hint).resolve()
    if bpy.data.filepath:
        return (Path(bpy.data.filepath).resolve().parent / hint).resolve()
    raise FileNotFoundError('Save the blend, open this script from disk, or set an absolute OUTPUT_DIRECTORY')


def validate_name(name):
    if any(c in name for c in '<>:"/\\|?*') or any(ord(c) < 32 for c in name) or name.endswith((' ', '.')):
        raise ValueError('Object name is not a valid buffer filename: ' + name)


def edge_masks(mesh, obj):
    """Classify adjacency in world space, in the same polygon order as the IB."""
    points = [obj.matrix_world @ vertex.co for vertex in mesh.vertices]
    adjacency, normals = {}, []
    for polygon in mesh.polygons:
        vertices = tuple(polygon.vertices)
        a, b, c = [points[i] for i in vertices]
        normal = (b - a).cross(c - a)
        normals.append(normal.normalized() if normal.length_squared > 0 and
                       all(math.isfinite(v) for v in normal) else None)
        for a, b in zip(vertices, vertices[1:] + vertices[:1]):
            adjacency.setdefault(tuple(sorted((a, b))), []).append(polygon.index)
    cosine = math.cos(math.radians(COPLANAR_ANGLE_DEGREES))
    hidden = set()
    for edge, faces in adjacency.items():
        if len(faces) != 2:
            continue
        a, b = [normals[i] for i in faces]
        # Opposite winding does not turn a flat shared edge into a crease.
        if a is not None and b is not None and abs(a.dot(b)) >= cosine:
            hidden.add(edge)
    masks = []
    for polygon in mesh.polygons:
        vertices = tuple(polygon.vertices)
        mask = 0
        for corner in range(3):
            edge = tuple(sorted((vertices[(corner + 1) % 3], vertices[(corner + 2) % 3])))
            if edge not in hidden:
                mask |= 1 << corner
        masks.append(mask)
    return masks


def prepare(obj):
    """Prepare a private mesh and validate all buffer values before publishing."""
    editing = obj.mode == 'EDIT'
    mesh = bpy.data.meshes.new('_EvictZoneExport') if editing else obj.data.copy()
    bm = None
    try:
        if editing:
            # Copy the live BMesh without flushing edits into the source datablock.
            bm = bmesh.from_edit_mesh(obj.data).copy()
        else:
            bm = bmesh.new()
            bm.from_mesh(mesh)
        bm.normal_update()
        bmesh.ops.triangulate(bm, faces=list(bm.faces), quad_method='BEAUTY', ngon_method='BEAUTY')
        bm.to_mesh(mesh)
        mesh.update()
        uv = mesh.uv_layers.active or mesh.uv_layers.new(name='UVMap')
        vb = bytearray()
        for polygon in mesh.polygons:
            if polygon.loop_total != 3:
                raise ValueError('Triangulation failed for ' + obj.name)
            for loop_index, value in zip(polygon.loop_indices, BARY_UVS):
                uv.data[loop_index].uv = value
                point = obj.matrix_world @ mesh.vertices[mesh.loops[loop_index].vertex_index].co
                if not all(math.isfinite(v) for v in point):
                    raise ValueError('Nonfinite world-space vertex in ' + obj.name)
                try:
                    vb.extend(struct.pack('<5f', *point, *value))
                except (OverflowError, struct.error) as exc:
                    raise ValueError('Vertex exceeds float32 capacity in ' + obj.name) from exc
        count = len(mesh.polygons) * 3
        if count > 0xffffffff:
            raise ValueError('Index count exceeds uint32 capacity in ' + obj.name)
        ib = b''.join(struct.pack('<I', i) for i in range(count))
        masks = edge_masks(mesh, obj)
        attribute = mesh.attributes.get('border_edge_mask')
        if attribute is not None and (attribute.domain != 'FACE' or attribute.data_type != 'INT'):
            raise ValueError('border_edge_mask must be a face integer attribute in ' + obj.name)
        attribute = attribute or mesh.attributes.new(name='border_edge_mask', type='INT', domain='FACE')
        attribute.data.foreach_set('value', masks)
        edges = b''.join(struct.pack('<I', mask) for mask in masks)
        return obj, mesh, bytes(vb), ib, count, edges
    except Exception:
        bpy.data.meshes.remove(mesh)
        raise
    finally:
        if bm is not None:
            bm.free()


def replace_file(path, payload):
    temporary = path.with_name(path.name + '.tmp')
    try:
        temporary.write_bytes(payload)
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def export():
    if not math.isfinite(COPLANAR_ANGLE_DEGREES) or not 0 <= COPLANAR_ANGLE_DEGREES < 90:
        raise ValueError('COPLANAR_ANGLE_DEGREES must be finite and in [0, 90)')
    output = output_directory()
    objects = sorted((obj for obj in bpy.context.scene.objects
                      if obj.type == 'MESH'), key=lambda obj: obj.name)
    if not objects:
        raise RuntimeError('No mesh objects in the current scene')
    seen = set()
    for obj in objects:
        validate_name(obj.name)
        if obj.name.casefold() in seen:
            raise ValueError('Object names collide on disk: ' + obj.name)
        seen.add(obj.name.casefold())
    jobs = []
    try:
        for obj in objects:
            jobs.append(prepare(obj))
        # Validate every temporary mesh before replacing any output.
        output.mkdir(parents=True, exist_ok=True)
        for obj, mesh, vb, ib, count, edges in jobs:
            replace_file(output / (obj.name + '_VB.buf'), vb)
            replace_file(output / (obj.name + '_IB.buf'), ib)
            replace_file(output / (obj.name + '_Edges.buf'), edges)
        counts = {obj.name: count for obj, _, _, _, count, _ in jobs}
        replace_file(output / 'EvictZones_DrawFunc.json', (json.dumps(counts, indent=2) + '\n').encode('utf-8'))
        for obj, mesh, _, _, count, _ in jobs:
            print('Exported', obj.name, count // 3, 'triangles;', count, 'indices to', output)
        return counts
    finally:
        for _, mesh, _, _, _, _ in jobs:
            bpy.data.meshes.remove(mesh)


EXPORTED_COUNTS = export()

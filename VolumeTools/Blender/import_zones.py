"""Import one zone mesh per selected map and death/TP kind."""

# User settings
PROJECT_PATH = '..'  # VolumeTools folder containing config.json and Source/.
MAPS = ['map01', 'map02']  # Explicit maps to import; no automatic discovery.
KINDS = ['death', 'tp']  # Zone kinds to import.
MERGE_DISTANCE = 0.0001  # Vertex welding grid, matching the wall importer.

import json
import math
import sys
from pathlib import Path
import bpy

UVS = ((0.,0.),(1.,0.),(0.,1.))

# Relative hints use the external script folder, or the saved blend folder for embedded text.
if not isinstance(PROJECT_PATH, str) or not PROJECT_PATH.strip():
    raise ValueError('PROJECT_PATH must be a nonempty path to VolumeTools')
hint = Path(PROJECT_PATH).expanduser()
if hint.is_absolute():
    ROOT = hint.resolve()
else:
    text = getattr(bpy.context.space_data, 'text', None)
    external = None
    if text is not None and not text.is_in_memory and text.filepath:
        candidate = Path(bpy.path.abspath(text.filepath))
        if candidate.is_file():
            external = candidate.resolve()
    try:
        script_file = __file__
    except NameError:
        script_file = None
    if external is None and script_file:
        candidate = Path(script_file)
        if candidate.suffix.lower() == '.py' and candidate.is_file():
            external = candidate.resolve()
    if external is not None:
        anchor = external.parent
    elif bpy.data.filepath:
        anchor = Path(bpy.data.filepath).resolve().parent
    else:
        raise FileNotFoundError('Embedded script in an unsaved blend: save the blend or set PROJECT_PATH to an absolute VolumeTools path')
    ROOT = (anchor / hint).resolve()
required = ('config.json', 'process_game_data.py', 'Source/__init__.py', 'Source/workflow.py', 'Source/blender_support.py')
missing = [name for name in required if not (ROOT / name).is_file()]
if missing:
    raise FileNotFoundError('PROJECT_PATH resolved to ' + str(ROOT) + '; missing: ' + ', '.join(missing))
# Load the selected support file directly, before using any cached Source imports.
import importlib.util
spec = importlib.util.spec_from_file_location('_volume_tools_blender_support', ROOT / 'Source/blender_support.py')
support = importlib.util.module_from_spec(spec)
spec.loader.exec_module(support)
support.activate_project(ROOT)
ROOT = support.workflow_root(ROOT)
from Source.blender_support import read_config, configured_path, identity, attach, activate
CONFIG = read_config(ROOT)


def load_dataset(region, kind):
    identity(kind, region)
    stored_kind = 'teleport' if kind == 'tp' else kind
    dataset = configured_path(ROOT, CONFIG, 'output') / 'Zones' / (region + '-' + stored_kind + '-zones.json')
    data = json.loads(dataset.read_text(encoding='utf-8-sig'))
    if data.get('format') != 'endfield-offline-zone-meshes' or data.get('version') != 1 or data.get('coordinateSpace') != 'unity-world':
        raise ValueError('Unsupported zone dataset')
    if data['region'] != region or data['kind'] != stored_kind:
        raise ValueError('Dataset identity disagrees with map/type selection')
    if data.get('status') != 'decoded' or data.get('errors') or data.get('unresolvedPrefixes'):
        raise ValueError('Zone dataset is incomplete; run a successful fresh game-data dump before importing')
    if not isinstance(data.get("zones"), list) or "boundary" not in data:
        raise ValueError("Zone dataset is missing zones or boundary: " + str(dataset))
    return region, kind, dataset, data


def import_dataset(region, kind, dataset, data):
    vertices, triangles, provenance = [], [], []
    vertex_grid, face_keys = {}, set()
    merged_vertices = duplicate_faces = collapsed_faces = 0
    for index, zone in enumerate(data['zones']):
        remap = []
        for x,y,z in zone['vertices']:
            point = (x,z,y)
            key = tuple(round(value / MERGE_DISTANCE) for value in point)
            if key not in vertex_grid:
                vertex_grid[key] = len(vertices)
                vertices.append(point)
            else:
                merged_vertices += 1
            remap.append(vertex_grid[key])
        for triangle in zone['triangles']:
            mapped = tuple(remap[i] for i in reversed(triangle))
            if len(set(mapped)) < 3:
                collapsed_faces += 1
                continue
            key = tuple(sorted(mapped))
            if key in face_keys:
                duplicate_faces += 1
                continue
            face_keys.add(key)
            triangles.append(mapped)
            provenance.append(index)
    mesh = bpy.data.meshes.new('Zones')
    mesh.from_pydata(vertices, [], triangles)
    if mesh.validate():
        bpy.data.meshes.remove(mesh)
        raise ValueError('Blender repaired invalid zone geometry; import refused')
    uv = mesh.uv_layers.new(name='UVMap')
    for polygon in mesh.polygons:
        for loop, value in zip(polygon.loop_indices, UVS):
            uv.data[loop].uv = value
    attribute = mesh.attributes.new(name='offline_zone_index', type='INT', domain='FACE')
    attribute.data.foreach_set('value', provenance)
    mesh.update()
    obj = attach(mesh, kind, region, dataset, data['boundary'])
    obj['volume_count'] = len(data['zones'])
    obj['merged_vertex_count'] = merged_vertices
    obj['skipped_duplicate_triangles'] = duplicate_faces
    obj['skipped_collapsed_triangles'] = collapsed_faces
    activate(obj)
    print('Imported', region, kind, len(data['zones']), 'zones;', merged_vertices, 'vertices merged;',
          duplicate_faces, 'duplicate and', collapsed_faces, 'collapsed triangles skipped')
    return obj


def main():
    if isinstance(MERGE_DISTANCE, bool) or not isinstance(MERGE_DISTANCE, (int, float)) or not math.isfinite(MERGE_DISTANCE) or MERGE_DISTANCE <= 0:
        raise ValueError('MERGE_DISTANCE must be finite and positive')
    if not isinstance(MAPS, list) or not MAPS:
        raise ValueError('MAPS must be a nonempty list of mapNN identifiers')
    if not isinstance(KINDS, list) or not KINDS or any(kind not in ('death', 'tp') for kind in KINDS):
        raise ValueError("KINDS must be a nonempty list selecting 'death' and/or 'tp'")
    for region in MAPS:
        identity('death', region)
    maps = list(dict.fromkeys(MAPS))
    kinds = list(dict.fromkeys(KINDS))
    prepared = [load_dataset(region, kind) for region in maps for kind in kinds]
    return [import_dataset(*entry) for entry in prepared]


IMPORTED_OBJECTS = main()

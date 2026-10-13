"""Import one invisible-wall mesh per selected map."""

# User settings
PROJECT_PATH = '..'  # VolumeTools folder containing config.json and Source/.
MAPS = ['map01', 'map02']  # Explicit maps to import; no automatic discovery.
MERGE_DISTANCE = 0.0001  # Vertex welding grid.

import json
import sys
from pathlib import Path
import bpy
import bmesh
from mathutils import Vector

QUADS = ((0,1,3,2),(4,6,7,5),(0,4,5,1),(2,3,7,6),(0,2,6,4),(1,5,7,3))
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


def load_dataset(region):
    identity('walls', region)
    dataset = configured_path(ROOT, CONFIG, 'output') / 'InvisWalls' / (region + '-walls.json')
    data = json.loads(dataset.read_text(encoding='utf-8-sig'))
    if data.get('region') != region or data.get('errors'):
        raise ValueError('Wall dataset is mismatched or has decoding errors')
    if not isinstance(data.get("boxes"), list) or "boundary" not in data:
        raise ValueError("Wall dataset is missing boxes or boundary: " + str(dataset))
    return region, dataset, data


def import_dataset(region, dataset, data):
    bm = bmesh.new()
    try:
        uv = bm.loops.layers.uv.new('UVMap')
        provenance = bm.faces.layers.int.new('offline_box_index')
        vertices = {}
        for index, box in enumerate(data['boxes']):
            points = [Vector((x,z,y)) for x,y,z in box['vertices']]
            x,y,z = box['center']
            center = Vector((x,z,y))
            for q in QUADS:
                for triangle in ((q[0],q[1],q[2]),(q[0],q[2],q[3])):
                    vv = []
                    for i in triangle:
                        key = tuple(round(v / MERGE_DISTANCE) for v in points[i])
                        if key not in vertices:
                            vertices[key] = bm.verts.new(points[i])
                        vv.append(vertices[key])
                    try:
                        face = bm.faces.new(vv)
                    except ValueError:
                        continue
                    face.normal_update()
                    if face.normal.dot(face.calc_center_median() - center) < 0:
                        face.normal_flip()
                    face[provenance] = index
                    for loop, value in zip(face.loops, UVS):
                        loop[uv].uv = value
        bm.normal_update()
        mesh = bpy.data.meshes.new('Walls')
        bm.to_mesh(mesh)
        if mesh.validate():
            bpy.data.meshes.remove(mesh)
            raise ValueError('Blender repaired invalid wall geometry; import refused')
        mesh.update()
    finally:
        bm.free()
    obj = attach(mesh, 'walls', region, dataset, data['boundary'])
    obj['volume_count'] = len(data['boxes'])
    activate(obj)
    print('Imported', region, len(data['boxes']), 'invisible walls')
    return obj


def main():
    if not isinstance(MAPS, list) or not MAPS:
        raise ValueError('MAPS must be a nonempty list of mapNN identifiers')
    for region in MAPS:
        identity('walls', region)
    maps = list(dict.fromkeys(MAPS))
    prepared = [load_dataset(region) for region in maps]
    return [import_dataset(*entry) for entry in prepared]


IMPORTED_OBJECTS = main()

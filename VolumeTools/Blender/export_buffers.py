"""Export every imported VolumeTools mesh into per-type map buffers and draw counts."""

# User settings
PROJECT_PATH = '..'  # VolumeTools folder containing config.json and Source/.

import json
import math
import struct
import sys
from pathlib import Path
import bpy

BARY_UVS = ((0.,0.),(1.,0.),(0.,1.))
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
from Source.blender_support import read_config, configured_path, identity
from Source.workflow import write_json
CONFIG = read_config(ROOT)


def export():
    output = configured_path(ROOT, CONFIG, 'blender_export_dir')
    backup_root = configured_path(ROOT, CONFIG, 'output')
    jobs, seen, manifests = [], {}, {}
    for obj in bpy.context.scene.objects:
        if 'volume_tools_type' not in obj and 'volume_tools_map' not in obj:
            continue
        if obj.type != 'MESH':
            raise ValueError('Tagged volume is not a mesh: ' + obj.name)
        category, map_name = identity(obj.get('volume_tools_type'), obj.get('volume_tools_map'))
        key = (category, map_name)
        if key in seen:
            raise ValueError('Duplicate ' + category + '/' + map_name + ': ' + seen[key] + ', ' + obj.name + '; remove or untag the old import')
        seen[key] = obj.name
        if obj.mode == 'EDIT':
            obj.update_from_editmode()
        mesh = obj.data
        mesh.calc_loop_triangles()
        count = len(mesh.loop_triangles) * 3
        if count > 0xffffffff:
            raise ValueError('Index count exceeds uint32 capacity')
        # Validate every job before replacing any output, including transformed float32 values.
        vb = bytearray()
        for triangle in mesh.loop_triangles:
            for loop_index, uv in zip(triangle.loops, BARY_UVS):
                point = obj.matrix_world @ mesh.vertices[mesh.loops[loop_index].vertex_index].co
                if not all(math.isfinite(v) for v in point):
                    raise ValueError('Nonfinite exported vertex in ' + obj.name)
                vb.extend(struct.pack('<5f', *point, *uv))
        ib = b''.join(struct.pack('<I', index) for index in range(count))
        jobs.append((category, map_name, bytes(vb), ib, count))
    if not jobs:
        raise RuntimeError('No imported VolumeTools meshes in the current scene')
    for category, _, _, _, _ in jobs:
        if category in manifests:
            continue
        path = output / category / 'draw_counts.json'
        value = json.loads(path.read_text(encoding='utf-8-sig')) if path.is_file() else {}
        if not isinstance(value, dict) or any(not isinstance(k, str) or type(v) is not int or v < 0 for k, v in value.items()):
            raise ValueError('Invalid draw-count manifest: ' + str(path))
        manifests[category] = value
    for category, map_name, vb, ib, count in jobs:
        directory = output / category
        directory.mkdir(parents=True, exist_ok=True)
        name = category + '_' + map_name
        for suffix, data in (('_VB.buf', vb), ('_IB.buf', ib)):
            path = directory / (name + suffix)
            temporary = path.with_suffix('.buf.tmp')
            try:
                temporary.write_bytes(data)
                temporary.replace(path)
            finally:
                if temporary.exists():
                    temporary.unlink()
        manifests[category][map_name] = count
        print('Exported', name, count // 3, 'triangles to', directory)
    for category, counts in manifests.items():
        write_json(output / category / 'draw_counts.json', dict(sorted(counts.items())), backup_root)
    return jobs


EXPORTED_JOBS = export()

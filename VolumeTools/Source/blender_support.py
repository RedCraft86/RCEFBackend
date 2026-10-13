"""Selected-project validation, imports, and mesh metadata for Blender scripts."""
import json
import re
import sys
from pathlib import Path
import bpy

CATEGORIES = {'walls': 'InvisWalls', 'death': 'KillZones', 'tp': 'TPZones'}


def workflow_root(project_root):
    """Validate the explicit root resolved by the external/embedded script bootstrap."""
    root = Path(project_root).resolve()
    required = ('config.json', 'process_game_data.py', 'Source/__init__.py',
                'Source/workflow.py', 'Source/blender_support.py')
    missing = [name for name in required if not (root / name).is_file()]
    if missing:
        raise FileNotFoundError('PROJECT_PATH resolved to ' + str(root) + '; missing: ' + ', '.join(missing))
    return root


def activate_project(project_root):
    """Select this project's Source package and discard helpers cached at another root."""
    root = workflow_root(project_root)
    package = sys.modules.get('Source')
    origin = getattr(package, '__file__', None)
    if package is not None and (not origin or Path(origin).resolve() != root / 'Source/__init__.py'):
        for name in list(sys.modules):
            if name == 'Source' or name.startswith('Source.'):
                del sys.modules[name]
    else:
        # Reopen/rerun picks up helper edits even within the same Blender session.
        sys.modules.pop('Source.blender_support', None)
        sys.modules.pop('Source.workflow', None)
    location = str(root)
    if location in sys.path:
        sys.path.remove(location)
    sys.path.insert(0, location)


def read_config(root):
    return json.loads((root / 'config.json').read_text(encoding='utf-8-sig'))


def configured_path(root, config, name):
    path = Path(config[name])
    return path.resolve() if path.is_absolute() else (root / path).resolve()


def identity(kind, region):
    if kind not in CATEGORIES or not isinstance(region, str) or not re.fullmatch(r'map\d+', region):
        raise ValueError('Expected walls/death/tp and a mapNN identifier')
    return CATEGORIES[kind], 'Map' + region[3:]


def attach(mesh, kind, region, dataset, boundary):
    category, map_name = identity(kind, region)
    name = category + '_' + map_name
    mesh.name = name
    obj = bpy.data.objects.new(name, mesh)
    collection = bpy.data.collections.get(category)
    if collection is None:
        collection = bpy.data.collections.new(category)
        bpy.context.scene.collection.children.link(collection)
    if collection.name not in bpy.context.scene.collection.children:
        bpy.context.scene.collection.children.link(collection)
    collection.objects.link(obj)
    obj['volume_tools_type'] = kind
    obj['volume_tools_map'] = region
    obj['dataset'] = str(dataset.resolve())
    obj['boundary'] = boundary
    return obj


def activate(obj):
    for selected in bpy.context.selected_objects:
        selected.select_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj

"""Background entrypoint; imports only the successful datasets from the current run."""
import importlib.util
import json
import sys
from pathlib import Path

import bpy

ROOT = Path(__file__).resolve().parents[2]


def load_script(name, config):
    spec = importlib.util.spec_from_file_location('_volume_tools_' + name, ROOT / 'Source/blender' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.CONFIG = config
    return module


def main():
    request = json.loads(Path(sys.argv[sys.argv.index('--') + 1]).read_text(encoding='utf-8'))
    config, datasets = request['config'], request['datasets']
    if not datasets or any(row.get('status') != 'decoded' for row in datasets):
        raise ValueError('A successful nonempty dataset selection is required')
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.context.scene['volume_tools_config'] = json.dumps(config)
    modules, prepared = {}, []
    for row in datasets:
        kind = row['type']
        name = 'import_walls' if kind == 'walls' else 'import_zones'
        if name not in modules:
            modules[name] = load_script(name, config)
        module = modules[name]
        args = (row['region'],) if kind == 'walls' else (row['region'], kind)
        prepared.append((module, module.load_dataset(*args)))
    for module, entry in prepared:
        module.import_dataset(*entry)
    load_script('export_buffers', config).export()


if __name__ == '__main__':
    main()

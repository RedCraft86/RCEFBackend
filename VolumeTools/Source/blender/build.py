"""Locate Blender and build a dated export from this run's decoded datasets."""
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

from Source.workflow import ROOT, resolve_path


def blender_executable(config):
    configured = config.get('blender_executable')
    if configured:
        executable = shutil.which(configured) or str(resolve_path(configured))
        if not Path(executable).is_file():
            raise FileNotFoundError('Configured Blender executable does not exist: ' + executable)
        return executable
    executable = shutil.which('blender')
    if executable:
        return executable
    installed = Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Blender Foundation'
    candidates = sorted(installed.glob('Blender */blender.exe'),
                        key=lambda p: tuple(int(n) for n in re.findall(r'\d+', p.parent.name)), reverse=True)
    if candidates:
        return str(candidates[0])
    raise FileNotFoundError('Blender not found; set blender_executable in the VolumeTools configuration')


def build(config, datasets):
    executable = blender_executable(config)
    with tempfile.TemporaryDirectory(prefix='volume-tools-build-') as temporary:
        request = Path(temporary) / 'request.json'
        request.write_text(json.dumps({'config': config, 'datasets': datasets}), encoding='utf-8')
        subprocess.run([executable, '--background', '--factory-startup', '--disable-autoexec',
                        '--python-exit-code', '1', '--python', str(ROOT / 'Source/blender/scene.py'),
                        '--', str(request)], check=True)

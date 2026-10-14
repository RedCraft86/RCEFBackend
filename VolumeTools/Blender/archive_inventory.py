"""Read an archive without saving it: blender -b --disable-autoexec archive.blend -P this.py -- output.json."""
import hashlib
import json
import sys
from pathlib import Path
import bpy


def inventory():
    source = Path(bpy.data.filepath).resolve()
    objects = []
    for obj in sorted(bpy.data.objects, key=lambda item: item.name):
        if obj.type != 'MESH' or 'volume_tools_type' not in obj:
            continue
        category = obj['volume_tools_type']
        attribute = obj.data.attributes.get('offline_box_index' if category == 'walls' else 'offline_zone_index')
        if obj.data.polygons and (attribute is None or attribute.domain != 'FACE'):
            raise ValueError('Missing face provenance: ' + obj.name)
        groups = {}
        for face in obj.data.polygons:
            index = attribute.data[face.index].value
            group = groups.setdefault(index, {'indices': set(), 'triangles': []})
            group['indices'].update(face.vertices)
            group['triangles'].append(list(face.vertices))
        volumes = []
        for index, group in sorted(groups.items()):
            indices = sorted(group['indices'])
            points = []
            for vertex in indices:
                point = obj.matrix_world @ obj.data.vertices[vertex].co
                points.append([point.x, point.z, point.y])
            remap = {vertex: ordinal for ordinal, vertex in enumerate(indices)}
            volumes.append({'archiveIndex': index, 'vertices': points,
                            'triangles': [[remap[v] for v in face] for face in group['triangles']],
                            'bounds': [[min(p[i] for p in points) for i in range(3)],
                                       [max(p[i] for p in points) for i in range(3)]]})
        objects.append({'name': obj.name, 'type': category, 'map': obj['volume_tools_map'],
                        'declaredCount': obj.get('volume_count'), 'volumes': volumes,
                        'datasetHint': obj.get('dataset')})
    return {'format': 'endfield-volume-archive', 'version': 1, 'coordinateSpace': 'unity-world',
            'source': str(source), 'sourceSha256': hashlib.sha256(source.read_bytes()).hexdigest(),
            'boundary': 'Historical mesh reference; not current-build native evidence.', 'objects': objects}


if __name__ == '__main__':
    args = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    if len(args) != 1:
        raise SystemExit('Provide the output JSON path after --')
    destination = Path(args[0]).resolve()
    if destination == Path(bpy.data.filepath).resolve():
        raise ValueError('Output must be separate from the archive')
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(inventory(), indent=2), encoding='utf-8')
    print('Archive inventory:', destination)

"""Compare current geometry with a historical archive without promoting its evidence."""
import itertools
import math
import json
from pathlib import Path


def read_datasets(root,archive):
    """Read the latest dataset or withheld-publication diagnostic for each archived category."""
    root=Path(root);datasets={}
    for obj in archive['objects']:
        region,category=obj['map'],obj['type']
        kind='teleport' if category=='tp' else category
        folder=root/('InvisWalls' if category=='walls' else 'Zones')
        stem=region if category=='walls' else region+'-'+kind
        candidates=[folder/(stem+'-scan-report.json'),
                    folder/(stem+('-walls.json' if category=='walls' else '-zones.json'))]
        path=max((p for p in candidates if p.is_file()),key=lambda p:p.stat().st_mtime_ns,default=None)
        if path:datasets[(region,category)]=json.loads(path.read_text(encoding='utf-8-sig'))
    return datasets


def bounds(vertices):
    return [[min(p[i] for p in vertices) for i in range(3)],
            [max(p[i] for p in vertices) for i in range(3)]]


def same_vertices(left, right, tolerance):
    def covered(points, targets):
        return all(any(max(abs(a-b) for a,b in zip(point,target)) <= tolerance
                       for target in targets) for point in points)
    return covered(left,right) and covered(right,left)


def compare(archive, datasets, *, tolerance=0.001):
    if tolerance <= 0 or not math.isfinite(tolerance):
        raise ValueError('Comparison tolerance must be finite and positive')
    summaries=[]
    for obj in archive['objects']:
        region,category=obj['map'],obj['type']
        dataset=datasets.get((region,category))
        rows=[] if dataset is None else dataset.get('boxes',dataset.get('zones',[]))
        bins={}
        for ordinal,row in enumerate(rows):
            vertices=row.get('vertices',[])
            if not vertices: continue
            extent=bounds(vertices)
            key=tuple(math.floor(v/tolerance) for v in extent[0])
            bins.setdefault(key,[]).append((ordinal,row,extent))
        matched=[]; missing=[]; used=set()
        for volume in obj['volumes']:
            extent=volume['bounds'];key=tuple(math.floor(v/tolerance) for v in extent[0]);found=None
            for delta in itertools.product((-1,0,1),repeat=3):
                for ordinal,row,candidate in bins.get(tuple(a+b for a,b in zip(key,delta)),[]):
                    if ordinal in used:continue
                    if (all(abs(a-b)<=tolerance for lo,hi in zip(extent,candidate) for a,b in zip(lo,hi))
                            and same_vertices(volume['vertices'],row['vertices'],tolerance)):
                        found=(ordinal,row);break
                if found:break
            if found:
                ordinal,row=found;used.add(ordinal)
                matched.append({'archiveIndex':volume['archiveIndex'],'currentIndex':ordinal,
                                'origins':row.get('origins',[]),
                                'triangleCountDelta':len(row.get('triangles',[]))-len(volume['triangles'])
                                if category!='walls' else None})
            else:missing.append(volume['archiveIndex'])
        summaries.append({'region':region,'type':category,'archiveCount':len(obj['volumes']),
                          'currentCount':len(rows),'currentStatus':dataset.get('status','unverified') if dataset else 'missing',
                          'matched':matched,'missingArchiveIndices':missing,
                          'currentAdditions':[{'index':i,'origins':r.get('origins',[])} for i,r in enumerate(rows) if i not in used]})
    return {'format':'endfield-volume-archive-comparison','version':1,'tolerance':tolerance,
            'archiveSha256':archive['sourceSha256'],'datasets':summaries,
            'boundary':'Vertex geometry comparison against historical meshes; triangle differences recorded separately. Missing matches do not prove removal from the current game.'}

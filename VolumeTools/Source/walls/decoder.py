"""Extract bounded offline box payloads, retaining empirical wall candidates.

Selection uses a raw-word signature shared by five verified wall boxes.
It is empirical, not a native-authenticated collision-layer definition.
"""
import argparse
import hashlib
import json
import math
import re
import struct
import sys
from pathlib import Path


def decode(source, expected):
    """Decode the kit's inverted LZ4 format with overlapping slice copies."""
    output = bytearray()
    cursor = 0
    while cursor < len(source):
        token = source[cursor]; cursor += 1
        lit = token & 0x33; lit = (lit & 3) | (lit >> 2)
        match = (token & 0xcc) >> 2; match = (match & 3) | (match >> 2)
        if lit == 15:
            while True:
                value = source[cursor]; cursor += 1; lit += value
                if value != 255: break
        if cursor+lit > len(source) or len(output)+lit > expected: raise ValueError('literal bounds')
        output.extend(source[cursor:cursor+lit]); cursor += lit
        if cursor == len(source): break
        offset = (source[cursor]<<8) | source[cursor+1]; cursor += 2
        if not 0 < offset <= len(output): raise ValueError('match offset')
        if match == 15:
            while True:
                value = source[cursor]; cursor += 1; match += value
                if value != 255: break
        match += 4
        if len(output)+match > expected: raise ValueError('match bounds')
        while match:
            size = min(match, offset)
            start = len(output)-offset
            output.extend(output[start:start+size]); match -= size
    if cursor != len(source) or len(output) != expected: raise ValueError('size mismatch')
    return bytes(output)


def rotate(v, q):
    x,y,z,w=q
    tx=2*(y*v[2]-z*v[1]); ty=2*(z*v[0]-x*v[2]); tz=2*(x*v[1]-y*v[0])
    return (v[0]+w*tx+y*tz-z*ty,v[1]+w*ty+z*tx-x*tz,v[2]+w*tz+x*ty-y*tx)


def multiply(a,b):
    x,y,z,w=a; X,Y,Z,W=b
    return (w*X+x*W+y*Z-z*Y,w*Y-x*Z+y*W+z*X,w*Z+x*Y-y*X+z*W,w*W-x*X-y*Y-z*Z)


def _table_layout(data, table):
    if not 0<=table<=len(data)-4: raise ValueError('table bounds')
    back=struct.unpack_from('<i',data,table)[0]
    vt=table-back
    if not 0<=vt<=len(data)-4 or back==0: raise ValueError('vtable bounds')
    size,obj=struct.unpack_from('<HH',data,vt)
    if size<4 or size%2 or vt+size>len(data) or obj<4 or table+obj>len(data): raise ValueError('table size')
    fields=list(struct.unpack_from('<'+'H'*((size-4)//2),data,vt+4))
    if any(v and not 4<=v<obj for v in fields): raise ValueError('field bounds')
    return {'fields':fields}

_CONFIG = None
_SCENE = None
_PATTERN = None


def initialize_worker(config, scene):
    global _CONFIG, _SCENE, _PATTERN
    from Source.workflow import configure_research_kit
    configure_research_kit(config)
    _CONFIG, _SCENE = config, Path(scene).resolve()
    lengths={64,68}
    lengths.update(64+stride*n for stride in (52,56)
                   for n in range(config['maximum_compound_boxes']+1))
    _PATTERN = re.compile(b'|'.join(re.escape(struct.pack('<I',length)) for length in sorted(lengths)))


def scan_file(path):
    """Return bounded geometry and provenance; never write datasets from a worker."""
    from scripts.game_data.streaming.framing import _table_layout
    path = Path(path).resolve()
    result = {'source': str(path.relative_to(_SCENE)), 'boxes': [], 'occurrences': 0,
              'decoded': False, 'errors': []}
    if 'ChunkInfo' in path.name:
        return result
    try:
        raw = path.read_bytes()
        expected = struct.unpack_from('<I', raw)[0]
        if not 0 < expected < _CONFIG['maximum_decoded_bytes']:
            raise ValueError('invalid decoded size')
        data = decode(raw[4:], expected)
        digest = hashlib.sha256(raw).hexdigest()
        all_records = {}
        total_boxes = 0
        for match in _PATTERN.finditer(data):
            prefix=match.start(); start=prefix+4; wrapper=prefix-8
            length=struct.unpack_from('<I',data,prefix)[0]
            if start+length>len(data) or wrapper<0: continue
            try: layout=_table_layout(data,wrapper)
            except ValueError: continue
            if layout['fields'] != [4] or wrapper+4+struct.unpack_from('<I',data,wrapper+4)[0] != prefix: continue
            actor=struct.unpack_from('<3f',data,start); actor_q=struct.unpack_from('<4f',data,start+12)
            kind,group,usage=struct.unpack_from('<3I',data,start+28)
            if length in (64,68) and kind==1:
                parts=[(actor,actor_q,struct.unpack_from('<3f',data,start+40),struct.unpack_from('<3I',data,start+52),0)]
                layout_name='compact-'+str(length)
                extra_words=[struct.unpack_from('<I',data,start+64)[0]] if length==68 else []
            elif kind==5:
                part_count=struct.unpack_from('<I',data,start+44)[0]
                strides=[stride for stride in (52,56) if part_count and length==64+stride*part_count]
                if len(strides)!=1 or part_count>_CONFIG['maximum_compound_boxes']: continue
                stride=strides[0];layout_name='compound-'+str(stride);extra_words=[]
                parts=[]
                # Fix: current compound records add an opaque word after each 52-byte shape body.
                for j in range(part_count):
                    sub=start+64+stride*j
                    local=struct.unpack_from('<3f',data,sub); local_q=struct.unpack_from('<4f',data,sub+12)
                    shifted=rotate(local,actor_q)
                    parts.append((tuple(actor[i]+shifted[i] for i in range(3)),multiply(actor_q,local_q),struct.unpack_from('<3f',data,sub+40),struct.unpack_from('<3I',data,sub+28),j))
                    extra_words.append(struct.unpack_from('<I',data,sub+52)[0] if stride==56 else None)
            else: continue
            for center,quat,half,flags,part_index in parts:
                if not all(math.isfinite(v) for v in (*center,*quat,*half)) or abs(sum(v*v for v in quat)-1)>.001 or not all(0<v<10000 for v in half): continue
                total_boxes+=1
                # Classification is performed by the parent using configured signatures.
                vertices=[]
                for sx in (-1,1):
                    for sy in (-1,1):
                        for sz in (-1,1):
                            v=rotate((sx*half[0],sy*half[1],sz*half[2]),quat)
                            vertices.append([v[i]+center[i] for i in range(3)])
                identity=hashlib.sha256(data[start:start+length]).hexdigest()+':'+str(part_index)
                origin={'source':str(path.relative_to(_SCENE)),'decodedOffset':start,'subShapeIndex':part_index,'payloadLength':length,'sourceSha256':digest}
                if identity in all_records: all_records[identity]['origins'].append(origin); continue
                words=[kind,group,usage,*flags]
                row={'center':center,'quaternionXYZW':quat,'halfExtents':half,'vertices':vertices,'anonymousWords28to100':words,'selectionSignature':[group,usage,*flags],'origins':[origin],
                     'payloadLayout':layout_name,'shapeTrailingWord':extra_words[part_index] if extra_words else None}
                row['_identity']=identity
                all_records[identity]=row
        result.update(boxes=list(all_records.values()), occurrences=total_boxes, decoded=True)
    except Exception as exc:
        result['errors'].append({'source': result['source'], 'error': str(exc)})
    return result


def extract(scene, output, region, config):
    from Source.parallel import map_files, default_jobs
    from Source.workflow import write_json
    output = Path(output)
    paths = sorted((Path(scene)/'Data/Streaming/PC'/region).rglob('*.bytes'))
    all_records, errors = {}, []
    occurrences = decoded = count = 0
    jobs = config.get('jobs', default_jobs())
    for result in map_files(scan_file, paths, jobs=jobs, initializer=initialize_worker,
                            initargs=(config, str(scene))):
        count += 1
        decoded += result['decoded']
        occurrences += result['occurrences']
        errors.extend(result['errors'])
        for row in result['boxes']:
            key = row.pop('_identity')
            if key in all_records:
                all_records[key]['origins'].extend(row['origins'])
            else:
                all_records[key] = row
        if count % 500 == 0:
            print(f'Scanned {count}/{len(paths)} files with {jobs} workers; {len(all_records)} bounded candidates', flush=True)
    report = {'region': region, 'filesScanned': count, 'decodedFiles': decoded,
              'boundedBoxOccurrences': occurrences, 'errors': errors,
              'codecCheckedAgainstKit': False, 'boxes': list(all_records.values()),
              'selection': 'All bounded compact/compound box candidates',
              'boundary': 'Empirical static box selection; no runtime activation or complete coverage claim.'}
    # Fix: no recognized shapes is an unsupported format, not proof of no walls.
    report['status'] = 'decoded' if decoded and occurrences and not errors else 'partial'
    if decoded and not occurrences:
        report['errors'].append({'error': 'No supported box payloads recognized; wall layout recovery required'})
    report['wallCandidateCount'] = len(all_records)
    write_json(output/(region+'-all-box-candidates.json'), report, output)
    exclusions = config.get('excluded_source_suffixes', {}).get(region, [])
    kept, excluded = [], []
    for row in all_records.values():
        if list(row['selectionSignature']) not in config['accepted_signatures']:
            continue
        origins = [o for o in row['origins'] if not any(o['source'].endswith('_'+suffix+'.bytes') for suffix in exclusions)]
        if not origins:
            excluded.append(row)
            continue
        kept.append({**row, 'origins': origins})
    report = {**report, 'boxes': kept, 'wallCandidateCount': len(kept),
              'excludedBoxCount': len(excluded), 'configuration': config,
              'selection': 'Configured empirical signatures and source exclusions'}
    # Fix: a lost selection signature must not replace an earlier usable mesh with zero walls.
    if not kept:
        report['status'] = 'partial'
        report['errors'].append({'error': 'No validated wall selection; previous wall dataset retained'})
    if report['status'] == 'decoded':
        write_json(output/(region+'-walls.json'), report, output)
        write_json(output/(region+'-excluded.json'), {'boxes': excluded}, output)
    else:
        write_json(output/(region+'-scan-report.json'), report, output)
    print(region, report['status'], len(kept), 'walls;', len(errors), 'diagnostics', flush=True)
    return report

"""Extract supported offline Endfield zone meshes; --decode-only reuses saved inputs."""
import argparse
import collections
import hashlib
import json
import re
from pathlib import Path
from .decoder import ZoneCodec
from .geometry import reconstruct
from .native import load_evidence, prepare
from Source.workflow import resolve_path, write_json

def build(input_dir,output,regions,kinds,config,evidence):
    if not kinds or any(kind not in ("death", "teleport") for kind in kinds):
        raise ValueError("Only death and teleport datasets are supported")
    codec=ZoneCodec(config,evidence); datasets={(r,k):[] for r in regions for k in kinds}; errors=[]; unresolved=[]; prefixes=[]; source_hashes={}; files=collections.Counter()
    paths=sorted((input_dir/'Data/Json/LevelData').rglob('*.json'))
    if not paths: raise FileNotFoundError('No LevelData inputs; run with --extract or supply --input')
    for path in paths:
        region=next((r for r in regions if path.name.startswith(r+'_')),None)
        if region is None: continue
        files[region]+=1; raw=path.read_bytes(); source=path.relative_to(input_dir).as_posix(); digest=hashlib.sha256(raw).hexdigest(); source_hashes[source]=digest
        try:
            framed=codec.frame(raw); area=framed['fields'].get('functionArea'); stop=framed.get('openField')
            if stop and (stop['name'].startswith('functionArea') or stop['name'].split('.')[0] in codec.leveldata.LEVELDATA_FIELDS[:17]):
                prefixes.append({'region':region,'source':source,'stop':stop}); continue
            if not area or area.get('value','present') is None: continue
            members=area.get('members',{}); bases=members.get('baseData',{}).get('rows',[]); ranges=members.get('ranges',{}).get('rows',[]); specifics=members.get('specificDatas',{}).get('rows',[])
            conditions=members.get('functionAreaConditions',{}).get('rows',[])
            if len(ranges)!=len(bases): raise ValueError('Function-area bounds are not parallel to base rows')
            for row_index,base in enumerate(bases):
                if base.get('value','present') is None: continue
                function_type=base['functionType']
                if function_type not in (1,15): continue
                if function_type==15:
                    unresolved.append({'region':region,'source':source,'baseData':base,'reason':'TempRepatriateZone is not established as a timed eviction volume'}); continue
                setting_index=base['index']
                if not 0<=setting_index<len(specifics): raise ValueError('Repatriation settings index outside specificDatas')
                settings=specifics[setting_index]
                if not settings or settings['storedType']!='Beyond.Gameplay.LevelFunctionAreaData+RepatriateZoneData': raise ValueError('Function type does not join to RepatriateZoneData')
                kind='teleport' if settings['useRepatriateZoneType'] else 'death'
                if kind not in kinds: continue
                try:
                    geometry=reconstruct(base,ranges[row_index])
                    selected_conditions=[c for c in conditions if str(c.get('uniqueId'))==base['uniqueId']] if base['hasCondition'] else []
                    if base['hasCondition'] and not selected_conditions: raise ValueError('Conditional zone has no matching condition identity')
                    identity=region+':'+source+':'+str(row_index)
                    row={'zoneId':identity,'kind':kind,'baseData':base,'settings':settings,'conditions':selected_conditions,'range':ranges[row_index],'origins':[{'source':source,'sourceSha256':digest,'rowIndex':row_index,'decodedOffset':base['startOffset'],'inputRoot':str(input_dir.resolve())}],**geometry}
                    datasets[(region,kind)].append(row)
                except ValueError as exc:
                    errors.append({'region':region,'kind':kind,'source':source,'rowIndex':row_index,'error':str(exc)})
                    unresolved.append({'region':region,'source':source,'baseData':base,'settings':settings,'reason':str(exc)})
        except Exception as exc: errors.append({'region':region,'source':source,'error':str(exc)})
    summaries=[]
    for region in regions:
        if not files[region]: raise ValueError('No LevelData files for '+region)
        for kind in kinds:
            selected_errors=[e for e in errors if e['region']==region and e.get('kind',kind)==kind]
            selected_prefixes=[p for p in prefixes if p['region']==region]
            status='decoded' if not selected_errors and not selected_prefixes else 'partial'
            boundary='Authored supported function-area volumes. Conditions/defaultOff are retained; no runtime activation or whole-game coverage claim.'
            report={'format':'endfield-offline-zone-meshes','version':1,'region':region,'kind':kind,'status':status,'coordinateSpace':'unity-world','boundary':boundary,'nativeInputs':codec.hashes,'filesScanned':files[region],'sourceHashes':{s:h for s,h in source_hashes.items() if region+'_' in s},'errors':selected_errors,'unresolvedPrefixes':selected_prefixes,'zones':datasets[(region,kind)]}
            write_json(output/(region+'-'+kind+'-zones.json'),report,output)
            summaries.append({'region':region,'kind':kind,'status':status,'zoneCount':len(report['zones']),'triangleCount':sum(len(z['triangles']) for z in report['zones']),'errorCount':len(selected_errors),'prefixGaps':len(selected_prefixes)})
    write_json(output/'unresolved-candidates.json',{'records':unresolved,'prefixes':prefixes,'errors':errors},output)
    write_json(output/'extraction-report.json',{'datasets':summaries,'inputRoot':str(input_dir.resolve()),'nativeInputs':codec.hashes,'boundary':'Eviction classification withheld; no safe-zone or chunk-boundary substitutes are published.'},output)
    for row in summaries: print(row,flush=True)
    return 0 if all(s['status']=='decoded' for s in summaries) else 2


def discover_regions(input_dir):
    return sorted({match.group(1) for p in (input_dir/'Data/Json/LevelData').rglob('*.json')
                   if (match:=re.match(r'(map\d+)_',p.name))})

def validate_input(input_dir,evidence):
    manifest=input_dir/'zone-inputs.json'
    if not manifest.is_file():
        raise FileNotFoundError('Input provenance missing: '+str(manifest)+'; use an extraction produced by VolumeTools')
    value=json.loads(manifest.read_text(encoding='utf-8-sig'))
    if value.get('format')!='endfield-zone-inputs' or value.get('version')!=1:
        raise ValueError('Unsupported zone input manifest')
    if value.get('nativeInputs')!=evidence['nativeInputs']:
        raise ValueError('Saved LevelData and native evidence belong to different builds; select matching saved evidence or extract fresh inputs')

def saved_input(config,output):
    if config.get('zones_input'):
        return resolve_path(config['zones_input'])
    manifest=output/'latest-extraction.json'
    if not manifest.is_file():
        raise FileNotFoundError('No saved extraction; run process_game_data.py or configure zones_input')
    saved=json.loads(manifest.read_text(encoding='utf-8-sig'))
    if saved.get('format') != 'endfield-volume-inputs' or saved.get('version') != 1:
        raise ValueError('Unsupported unified extraction manifest')
    value=saved['inputs']['zones']
    path=Path(value)
    return path.resolve() if path.is_absolute() else (output/path).resolve()

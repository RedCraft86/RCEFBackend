"""Extract invisible walls, death zones, and TP zones from installed Endfield data."""
import argparse
import json
import re
import subprocess
from pathlib import Path

from Source.workflow import ROOT, TYPES, load_config, resolve_path, timestamp, write_json
from Source.walls import decoder as walls
from Source.zones import zones
from Source.zones.native import load_evidence, prepare


def extract_inputs(config, output, regions, selected):
    engine = resolve_path(config['research_kit']) / 'tools/AnimeStudio/AnimeStudio.CLI/bin/Release/net9.0-windows/AnimeStudio.CLI.exe'
    if not engine.is_file():
        raise FileNotFoundError('Build ResearchKit AnimeStudio CLI first: ' + str(engine))
    game = resolve_path(config['game_data'])
    primary = game / 'Persistent' if (game / 'Persistent/VFS').is_dir() else game / 'StreamingAssets'
    fallback = ['--fallback-assets', str(game / 'StreamingAssets')] if primary.name == 'Persistent' else []
    folder = output / 'Inputs' / timestamp()
    inputs = {}
    for name, block in (('walls', 'streaming'), ('zones', 'json-data')):
        if name == 'walls' and 'walls' not in selected:
            continue
        if name == 'zones' and not any(t in selected for t in ('death', 'tp')):
            continue
        scene = folder / name
        command = [str(engine), 'dump', '-s', str(primary), '-o', str(scene), '-b', block, *fallback]
        maps = '|'.join(re.escape(r) for r in regions) if regions else r'map\d+'
        pattern = r'(?i)(' + maps + r')/' if name == 'walls' else r'(?i)LevelData/(' + maps + r')_'
        command += ['--file-regex', pattern]
        subprocess.run(command, check=True)
        if not discover(scene, name):
            raise RuntimeError('Extraction returned no mapNN ' + name + ' inputs')
        inputs[name] = scene
    return inputs


def discover(scene, name):
    if name == 'zones':
        return zones.discover_regions(scene)
    return sorted(p.name for p in (scene / 'Data/Streaming/PC').glob('map*')
                  if p.is_dir() and re.fullmatch(r'map\d+', p.name))


def saved_inputs(config, output, overrides):
    manifest = output / 'latest-extraction.json'
    value = json.loads(manifest.read_text(encoding='utf-8-sig')) if manifest.is_file() else {}
    if value and (value.get('format') != 'endfield-volume-inputs' or value.get('version') != 1):
        raise ValueError('Unsupported unified extraction manifest')
    result = {}
    for name in ('walls', 'zones'):
        if overrides.get(name):
            result[name] = overrides[name].resolve()
        elif config.get(name + '_input'):
            result[name] = resolve_path(config[name + '_input'])
        elif name in value.get('inputs', {}):
            path = Path(value['inputs'][name])
            result[name] = path.resolve() if path.is_absolute() else (output / path).resolve()
    return result


def process(config, *, decode_only=False, overrides=None):
    output = resolve_path(config['output'])
    selected = config['types']
    regions = config['regions']
    needs_zones = any(t in selected for t in ('death', 'tp'))
    evidence = None
    if needs_zones:
        # A first run prepares missing evidence; existing build mismatches always fail closed.
        if not decode_only and not resolve_path(config['native_evidence']).is_file():
            prepare(config)
        evidence = load_evidence(config, check_installed=not decode_only)
    if decode_only:
        inputs = saved_inputs(config, output, overrides or {})
    else:
        inputs = extract_inputs(config, output, regions, selected)
        if needs_zones:
            load_evidence(config, check_installed=True)
            write_json(inputs['zones'] / 'zone-inputs.json', {
                'format': 'endfield-zone-inputs', 'version': 1, 'nativeInputs': evidence['nativeInputs']}, output)
    required = (['walls'] if 'walls' in selected else []) + (['zones'] if needs_zones else [])
    for name in required:
        if name not in inputs:
            raise FileNotFoundError('No saved ' + name + ' inputs; extract first or supply --' + name + '-input')
        available = discover(inputs[name], name)
        missing = set(regions) - set(available)
        if missing or not available:
            raise ValueError('Missing map inputs for ' + name + ': ' + ', '.join(sorted(missing or {'all maps'})))
    if needs_zones:
        zones.validate_input(inputs['zones'], evidence)
    if not decode_only:
        # Preserve saved inputs for types omitted from a narrower fresh run.
        previous = saved_inputs(config, output, {})
        previous.update(inputs)
        write_json(output / 'latest-extraction.json', {
            'format': 'endfield-volume-inputs', 'version': 1,
            'inputs': {name: str(path.relative_to(output)) if path.is_relative_to(output) else str(path)
                       for name, path in previous.items()},
            'nativeInputs': evidence['nativeInputs'] if evidence else None}, output)
    summaries = []
    if 'walls' in selected:
        wall_config = {**config, **config['walls'], 'research_kit': str(resolve_path(config['research_kit']))}
        for region in regions or discover(inputs['walls'], 'walls'):
            walls.extract(inputs['walls'], output / 'InvisWalls', region, wall_config)
            data = json.loads((output / 'InvisWalls' / (region + '-walls.json')).read_text())
            summaries.append({'region': region, 'type': 'walls', 'count': len(data['boxes'])})
    status = 0
    if needs_zones:
        zone_regions = regions or discover(inputs['zones'], 'zones')
        kinds = ['teleport' if t == 'tp' else t for t in selected if t != 'walls']
        status = zones.build(inputs['zones'], output / 'Zones', zone_regions, kinds, config, evidence)
        for row in json.loads((output / 'Zones/extraction-report.json').read_text())['datasets']:
            summaries.append({**row, 'type': 'tp' if row['kind'] == 'teleport' else row['kind']})
    write_json(output / 'processing-report.json', {'datasets': summaries, 'status': 'decoded' if not status else 'partial'}, output)
    return status


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=ROOT / 'config.json')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--decode-only', action='store_true')
    mode.add_argument('--prepare-native', action='store_true')
    parser.add_argument('--regions', nargs='+')
    parser.add_argument('--types', nargs='+', choices=TYPES)
    parser.add_argument('--walls-input', type=Path)
    parser.add_argument('--zones-input', type=Path)
    args = parser.parse_args()
    if (args.walls_input or args.zones_input) and not args.decode_only:
        parser.error('Input overrides require --decode-only')
    if args.prepare_native and any((args.regions, args.types, args.walls_input, args.zones_input)):
        parser.error('--prepare-native accepts --config only')
    config = load_config(args.config)
    if args.regions is not None:
        if any(not re.fullmatch(r'map\d+', r) for r in args.regions):
            parser.error('Regions must be mapNN identifiers')
        config['regions'] = list(dict.fromkeys(args.regions))
    if args.types is not None:
        config['types'] = list(dict.fromkeys(args.types))
    if args.prepare_native:
        prepare(config)
        return 0
    return process(config, decode_only=args.decode_only, overrides={'walls': args.walls_input, 'zones': args.zones_input})


if __name__ == '__main__':
    raise SystemExit(main())

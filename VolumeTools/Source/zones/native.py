"""Prepare and validate VolumeTools evidence against local native copies."""
import hashlib
import re
import shutil
import struct
from pathlib import Path
from Source.workflow import resolve_path, timestamp, write_json

VERSION = 3
FORMAT = 'endfield-zone-native-evidence'
NATIVE_NAMES = ('GameAssembly.dll', 'global-metadata.dat')

BASE_TYPE = 'Beyond.Gameplay.LevelFunctionAreaData+FunctionAreaBaseData'
HIDE_TYPE = 'Beyond.Gameplay.LevelFunctionAreaData+FunctionAreaFactoryHideDesc'
BASE_LAYOUT = [
    ('bufferDistance', 'float'), ('defaultOff', 'bool'), ('enableFactoryHide', 'bool'),
    ('euler', 'UnityEngine.Vector3'), ('factoryHideDesc', HIDE_TYPE),
    ('functionType', 'Beyond.Gameplay.EFunctionAreaType'), ('hasCondition', 'bool'),
    ('index', 'int'), ('offset', 'UnityEngine.Vector3'),
    ('posList', 'System.Collections.Generic.List`1<UnityEngine.Vector2>'),
    ('postion', 'UnityEngine.Vector3'), ('radius', 'float'),
    ('shapeType', 'Beyond.Gameplay.Core.TriggerObserverComponent+Shape'),
    ('size', 'UnityEngine.Vector3'), ('uniqueId', 'ulong'),
]
BASE_LAYOUT_WITH_CHANNEL_MASK = BASE_LAYOUT[:9] + [
    ('playerCenterChannelMask', 'Beyond.Gameplay.FunctionAreaManager+EPlayerCenterChannelMask'),
] + BASE_LAYOUT[9:]
HIDE_LAYOUT = [(name, 'bool') for name in (
    'hideAllBuildingUI', 'hideFacBuilding', 'hideFacBuildingAll', 'hideFacFence', 'hideSocialFac'
)] + [('hideSocialFacOwner', 'Beyond.Gameplay.Factory.SocialFragment+EOwner'),
     ('showFacBuildingTypes', 'System.Collections.Generic.List`1<Beyond.GEnums.FCNodeType>')]


def validate_layout(schemas, type_name, layout):
    schema = schemas.get(type_name, {})
    actual = [(m.get('name'), m.get('declaredType')) for m in schema.get('members', [])]
    expected_order = [name for name, _ in layout]
    if (actual != layout or schema.get('memberOrder') != expected_order
            or schema.get('serializedMemberCount') != len(layout)
            or schema.get('framesAsRawStruct') is not False):
        raise ValueError(f'Unsupported current-build schema {type_name}: expected {layout!r} '
                         f'({len(layout)} members); actual {actual!r}, '
                         f'order={schema.get("memberOrder")!r}, '
                         f'count={schema.get("serializedMemberCount")!r}, '
                         f'raw={schema.get("framesAsRawStruct")!r}')
    return schema

def hash_file(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()

def installed_paths(config):
    game = resolve_path(config['game_data'])
    return {'GameAssembly.dll': game.parent / 'GameAssembly.dll',
            'global-metadata.dat': game / 'il2cpp_data/Metadata/global-metadata.dat'}

def validate_prefix_schema(schemas):
    """Require native declarations that let the decoder reach functionArea safely."""
    def declared_members(type_name):
        schema = schemas.get(type_name, {})
        members = schema.get('members', [])
        names = [member.get('name') for member in members]
        if (not members or any(not isinstance(name, str) or not name for name in names)
                or len(set(names)) != len(names)
                or schema.get('memberOrder') != names
                or schema.get('serializedMemberCount') != len(members)
                or schema.get('framesAsRawStruct') is not False
                or any(not isinstance(member.get('declaredType'), str)
                       or not member['declaredType'] for member in members)):
            raise ValueError('Required native schema missing or incomplete: ' + type_name
                             + '; run process_game_data.py --prepare-native')
        return {member['name']: member['declaredType'] for member in members}

    fields = declared_members('Beyond.Gameplay.LevelData')
    area_type = 'Beyond.Gameplay.LevelFunctionAreaData'
    if fields.get('functionArea') != area_type:
        raise ValueError('Required native LevelData.functionArea declaration missing or unsupported; '
                         'run process_game_data.py --prepare-native')
    area_fields = declared_members(area_type)
    required = {
        'baseData': BASE_TYPE,
        'functionAreaConditions': area_type + '+ConditionData',
        'ranges': 'Beyond.Gameplay.ThreeDimRange',
        'specificDatas': area_type + '+FunctionAreaSpecificData',
    }
    if any(area_fields.get(name) != 'System.Collections.Generic.List`1<' + item_type + '>'
           for name, item_type in required.items()):
        raise ValueError('Required native functionArea collections missing or unsupported; '
                         'run process_game_data.py --prepare-native')


def validate_schema(evidence):
    if evidence.get('format') != FORMAT or evidence.get('version') != VERSION:
        raise ValueError(f'Unsupported native evidence format/version: expected {FORMAT} v{VERSION}, '
                         f'actual {evidence.get("format")!r} v{evidence.get("version")!r}; '
                         'run process_game_data.py --prepare-native, then extract fresh inputs')
    schemas = evidence['wrappers']
    if evidence.get('stringWireFormat') != 'int32-reference':
        raise ValueError('Current string formatter evidence missing; run process_game_data.py --prepare-native')
    validate_prefix_schema(schemas)
    base_layout = (BASE_LAYOUT_WITH_CHANNEL_MASK
                   if 'playerCenterChannelMask' in schemas.get(BASE_TYPE, {}).get('memberOrder', [])
                   else BASE_LAYOUT)
    base = validate_layout(schemas, BASE_TYPE, base_layout)
    hide = validate_layout(schemas, HIDE_TYPE, HIDE_LAYOUT)
    widths = evidence.get('enumWidths', {})
    if not isinstance(widths, dict) or any(type(w) is not int or w not in (1, 2, 4, 8) for w in widths.values()):
        raise ValueError('Unsupported native enumWidths: expected widths of 1, 2, 4, or 8 bytes')
    for enum in ('Beyond.Gameplay.EFunctionAreaType', 'Beyond.Gameplay.Core.TriggerObserverComponent+Shape',
                 'Beyond.Gameplay.Factory.SocialFragment+EOwner', 'Beyond.GEnums.FCNodeType'):
        if enum not in widths:
            raise ValueError('Missing native enum width: ' + enum)
    for member in base['members'] + hide['members']:
        if (member['kind'] == 'enum' and member['name'] != 'playerCenterChannelMask'
                and member.get('width') != 4):
            raise ValueError(f'Unsupported function-area enum width for {member["declaredType"]}: '
                             f'expected 4, actual {member.get("width")!r}')
        if member['kind'] == 'enum' and (member['declaredType'] not in widths
                                        or widths[member['declaredType']] != member.get('width')):
            raise ValueError('Native enum width disagrees with member: ' + member['declaredType'])
    for enum, values in {
        'Beyond.Gameplay.EFunctionAreaType': {'RepatriateZone': 1, 'TempRepatriateZone': 15},
        'Beyond.Gameplay.Core.TriggerObserverComponent+Shape': {'Box': 1, 'PolyLine': 3},
    }.items():
        if any(evidence['enumConstants'][enum].get(name) != value for name, value in values.items()):
            raise ValueError('Unsupported current-build zone enum identities: ' + enum)
    settings = schemas['Beyond.Gameplay.LevelFunctionAreaData+RepatriateZoneData']
    if settings['memberOrder'] != ['lifeRatio', 'useRepatriateZoneType'] or [m['declaredType'] for m in settings['members']] != ['float', 'bool']:
        raise ValueError('Unsupported current-build repatriation settings')
    by_name = {s['wrapperName']: s for s in schemas.values()}
    for family in evidence['families']:
        seen = set()
        for entry in evidence['families'][family]:
            if entry['tag'] in seen:
                raise ValueError('Duplicate native union tag')
            seen.add(entry['tag'])
            schema = by_name[entry['wrapperName']]
            if schema['serializedMemberCount'] != entry['memberCount'] or schema['memberOrder'] != entry['members']:
                raise ValueError('Native union and generated schema disagree')

def verify_hashes(expected, paths, label):
    for name in NATIVE_NAMES:
        path = paths[name]
        if not path.is_file():
            raise FileNotFoundError(label + ' native input missing: ' + str(path))
        if hash_file(path) != expected.get(name):
            raise ValueError(label + ' build differs from native evidence: ' + name + '; run process_game_data.py --prepare-native for the selected installed build')

def load_evidence(config, check_installed=False):
    import json
    path = resolve_path(config['native_evidence'])
    if not path.is_file():
        raise FileNotFoundError('Native evidence missing; run python -B process_game_data.py --prepare-native')
    evidence = json.loads(path.read_text(encoding='utf-8-sig'))
    validate_schema(evidence)
    directory = resolve_path(config['native_inputs'])
    verify_hashes(evidence['nativeInputs'], {n: directory / n for n in NATIVE_NAMES}, 'Copied')
    if check_installed:
        verify_hashes(evidence['nativeInputs'], installed_paths(config), 'Installed')
    return evidence

def string_formatter_evidence(image, start):
    def relative_target(address, code, offset):
        return address + offset + 5 + struct.unpack_from('<i', code, offset + 1)[0]

    body = start
    code = image.pe.bytes_at_va(start, 320)
    # New builds outline the reader and leave a call followed by the result store.
    if code.startswith(bytes.fromhex('40534883ec20488bca498bd8e8')):
        body = relative_target(start, code, 12)
        code = image.pe.bytes_at_va(body, 0xa00)
        read = re.search(
            rb'\x83\x7d\x30\x04\x0f\x8d(.{4})', code, re.DOTALL)
        if read:
            offset = read.end() + struct.unpack('<i', read[1])[0]
            advance = re.match(
                rb'\x48\x8b\x45\x50\x44\x8b\x30\x8b\x75\x30\x83\xc6\xfc'
                rb'\x79.\xba\x04\x00\x00\x00\x48\x8b\xcd\xe8.{4}\x84\xc0\x75.'
                rb'\x48\x83\x45\x50\x04\x83\x45\x40\x04\x83\x45\x44\x04\x89\x75\x30'
                rb'\x41\x83\xfe\xfe\x0f\x84(.{4})\x41\x83\xfe\xff\x0f\x84(.{4})',
                code[offset:], re.DOTALL)
            resolver = re.search(
                rb'\x45\x33\xc0\x41\x8b\xd6\x48\x8b\xcf\x48\x83\xc4\x50\x41\x5e\x5f\x5e\x5d\x5b\xe9',
                code[offset:]) if advance else None
            if resolver:
                null_at = offset + advance.end(1) + struct.unpack('<i', advance[1])[0]
                empty_at = offset + advance.end(2) + struct.unpack('<i', advance[2])[0]
                target = relative_target(body, code, offset + resolver.end() - 1)
                if code.startswith(b'\x33\xc0', null_at) and code.startswith(b'\x48\x8b\x05', empty_at):
                    return {'dispatcherVa': hex(start), 'readerVa': hex(body), 'resolverVa': hex(target),
                            'codeWindowHex': code[:null_at + 2].hex()}
    else:
        resolver = code.find(bytes.fromhex('4533c08bd6488bcbe8'))
        if resolver >= 0 and all(bytes.fromhex(pattern) in code for pattern in
                                 ('837b3004', '8b30', '83fefe', '83feff')):
            return {'dispatcherVa': hex(start), 'resolverVa': hex(relative_target(start, code, resolver + 8)),
                    'codeWindowHex': code.hex()}
    raise ValueError(f'Unsupported native pooled-string formatter shape at {start:#x} (reader {body:#x})')


def prepare(config):
    output = resolve_path(config['output'])
    directory = resolve_path(config['native_inputs'])
    sources = installed_paths(config)
    for name in NATIVE_NAMES:
        if (directory / name).resolve() == sources[name].resolve():
            raise ValueError('native_inputs must be separate from installed game inputs')
        if not sources[name].is_file():
            raise FileNotFoundError(sources[name])
    hashes = {name: hash_file(path) for name, path in sources.items()}
    directory.mkdir(parents=True, exist_ok=True)
    for name in NATIVE_NAMES:
        destination = directory / name
        if destination.is_file() and hash_file(destination) == hashes[name]:
            continue
        if destination.exists():
            backup = output / 'backups' / timestamp()
            backup.mkdir(parents=True)
            shutil.copy2(destination, backup / name)
        temporary = destination.with_name(name + '.' + timestamp() + '.tmp')
        try:
            shutil.copy2(sources[name], temporary)
            if hash_file(temporary) != hashes[name]:
                raise ValueError('Installed input changed while being copied: ' + name)
            temporary.replace(destination)
        finally:
            if temporary.exists(): temporary.unlink()
    verify_hashes(hashes, sources, 'Installed')
    from Source.gamedata.il2cpp.native_image import NativeImage
    from Source.gamedata.memorypack.wrapper_members import derive_from_image, enum_widths_from_image, unmanaged_value_sizes_from_image
    from Source.gamedata.memorypack.union_dispatch import read_union_switch
    from Source.gamedata.unions import FAMILY_BASES, short_name
    from Source.gamedata.il2cpp.protocol import field_defaults, native_enum_members
    image = NativeImage(directory / 'GameAssembly.dll', directory / 'global-metadata.dat', label='VolumeTools')
    wrappers = derive_from_image(image)
    by_name = {w.name: w for w in wrappers.values()}
    families = {}
    for family in FAMILY_BASES:
        switch = read_union_switch(image, FAMILY_BASES[family], wrappers=wrappers)
        families[family] = [{**entry, 'name': short_name(entry['wrapperName']),
                            'memberCount': len(by_name[entry['wrapperName']].members),
                            'members': [m.name for m in by_name[entry['wrapperName']].members]}
                           for entry in switch['entries']]
        print(family, len(families[family]), 'verified routes', flush=True)
    def method(owner, name):
        matches = [m for m in image.metadata.methods
                   if image.type_name(m.declaring_type) == owner and image.metadata.string(m.name_index) == name]
        if len(matches) != 1:
            raise ValueError('Ambiguous native formatter method: ' + owner + '.' + name)
        return image.method_pointer_va(matches[0])
    start = method('MemoryPack.BeyondStringFormatter', 'Deserialize')
    string_formatter = string_formatter_evidence(image, start)
    defaults = field_defaults(image.metadata)
    enum_names = ('Beyond.Gameplay.EFunctionAreaType', 'Beyond.Gameplay.Core.TriggerObserverComponent+Shape', 'Beyond.Gameplay.RepatriateRealSource', 'Proto.SCENE_CROSS_BORDER_OP', 'Proto.SCENE_CROSS_BORDER_REASON')
    enums = {name: {row['name']: row['id'] for row in native_enum_members(image.metadata, defaults, image.pe, image.registration, name)} for name in enum_names}
    evidence = {'format': FORMAT, 'version': VERSION, 'nativeInputs': hashes, 'families': families,
                'stringWireFormat': 'int32-reference',
                'stringFormatter': string_formatter,
                'enumWidths': enum_widths_from_image(image),
                'unmanagedSizes': unmanaged_value_sizes_from_image(image),
                'enumConstants': enums, 'wrappers': {w.wrapped_type: w.row() for w in wrappers.values() if w.wrapped_type},
                'boundary': 'Current copied-build formatter switches, generated member declarations, and typed enum constants; not a runtime activation proof.'}
    validate_schema(evidence)
    verify_hashes(hashes, {n: directory / n for n in NATIVE_NAMES}, 'Copied')
    verify_hashes(hashes, sources, 'Installed')
    write_json(resolve_path(config['native_evidence']), evidence, output)
    print('Native evidence ready:', resolve_path(config['native_evidence']), flush=True)
    return evidence

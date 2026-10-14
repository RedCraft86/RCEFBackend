"""Strict VolumeTools MemoryPack decoding with current copied-build evidence."""
import math
import struct
import types
from Source.workflow import configure_research_kit
from .native import BASE_TYPE, load_evidence, validate_schema

class ZoneCodec:
    def __init__(self, config, evidence=None):
        configure_research_kit(config)
        from scripts.game_data import leveldata_binary
        from scripts.game_data.codecs.leveldata import memorypack
        self.leveldata = leveldata_binary
        self.memorypack = memorypack
        evidence = load_evidence(config) if evidence is None else evidence
        validate_schema(evidence)
        self.schemas = evidence['wrappers']
        self.enum_widths = evidence['enumWidths']
        self.unmanaged_sizes = evidence.get('unmanagedSizes',{})
        self.hashes = evidence['nativeInputs']
        self.unions = {}
        by_name = {s['wrapperName']: s for s in self.schemas.values()}
        for family, base in [('ConditionRuntimeBase', 'Beyond.Gameplay.ConditionRuntimeBase'), ('FunctionAreaSpecificData', 'Beyond.Gameplay.LevelFunctionAreaData+FunctionAreaSpecificData')]:
            self.unions[base] = {entry['tag']: by_name[entry['wrapperName']]['wrappedType'] for entry in evidence['families'][family]}
        from scripts.game_data.levelscript_union_tags import FAMILY_BASES
        for family, wrapper in FAMILY_BASES.items():
            base = by_name.get('Beyond.MemoryPack.' + wrapper)
            if base and family in evidence['families']:
                self.unions[base['wrappedType']] = {
                    entry['tag']: by_name[entry['wrapperName']]['wrappedType']
                    for entry in evidence['families'][family]}

    def string_reference(self,data,offset):
        # Fix: BeyondStringFormatter reads an int32 reference, not inline UTF-8 bytes.
        result=self.memorypack.read_i32(data,offset)
        if result is None: raise ValueError('Truncated string reference at '+str(offset))
        value,end=result
        if value==-1: return None,end
        if value==-2: return '',end
        if value<0: raise ValueError('Unsupported negative string reference at '+str(offset))
        return {'stringReference':value},end

    def read(self,data,offset,type_name,member=None,depth=0):
        if depth>32: raise ValueError('Nested zone data exceeds depth limit')
        def checked(value):
            if value is None: raise ValueError(f'Truncated/invalid {type_name} at {offset}')
            return value
        primitive={'bool':self.memorypack.read_bool,'int':self.memorypack.read_i32,'uint':self.memorypack.read_u32,'ulong':self.memorypack.read_u64,'long':self.memorypack.read_i64,'float':self.memorypack.read_f32,'double':self.memorypack.read_f64,'string':self.string_reference}
        if type_name in primitive: return checked(primitive[type_name](data,offset))
        if type_name=='System.Nullable`1<float>':
            from scripts.game_data.codecs.levelscript.enemies import _nullable_float, LevelEnemyCodecError
            try: return _nullable_float(data,offset,'extraDelayToRecycleTime')
            except LevelEnemyCodecError as exc: raise ValueError(str(exc)) from exc
        if type_name.startswith('System.Nullable`1<'):
            child=type_name.split('<',1)[1][:-1]
            widths={'bool':1,'int':4,'uint':4,'float':4,'long':8,'ulong':8,'double':8}
            width=widths.get(child)
            size=self.unmanaged_sizes.get(type_name)
            if width is None or size!=2*width:
                raise ValueError('Unsupported native nullable layout: '+type_name)
            if offset+size>len(data) or data[offset] not in (0,1):
                raise ValueError('Truncated/invalid nullable at '+str(offset))
            value=None
            if data[offset]: value,_=self.read(data,offset+width,child,depth=depth+1)
            return value,offset+size
        # Fix: enum collection elements have no member descriptor of their own.
        if type_name in self.enum_widths or (member and member.get('kind')=='enum'):
            width=self.enum_widths.get(type_name)
            if member and member.get('kind')=='enum' and width!=member.get('width'):
                raise ValueError('Native enum width disagrees with member: '+type_name)
            if width not in (1,2,4,8) or offset+width>len(data): raise ValueError('Enum bounds/width')
            return int.from_bytes(data[offset:offset+width],'little',signed=False),offset+width
        # Improve: typed integer grid coordinates close dynamic-occlusion prefixes.
        if type_name in ('UnityEngine.Vector2Int','UnityEngine.Vector3Int'):
            n=2 if type_name.endswith('Vector2Int') else 3
            if offset+4*n>len(data): raise ValueError('Integer vector bounds')
            return list(struct.unpack_from('<'+'i'*n,data,offset)),offset+4*n
        if type_name in ('UnityEngine.Vector2','UnityEngine.Vector3','UnityEngine.Vector4','UnityEngine.Quaternion'):
            n={'UnityEngine.Vector2':2,'UnityEngine.Vector3':3}.get(type_name,4)
            if offset+4*n>len(data): raise ValueError('Vector bounds')
            values=struct.unpack_from('<'+'f'*n,data,offset)
            if not all(math.isfinite(v) for v in values): raise ValueError('Nonfinite vector')
            return list(values),offset+4*n
        if type_name.startswith('System.Collections.Generic.List`1<') or type_name.endswith('[]'):
            child=type_name.split('<',1)[1][:-1] if '<' in type_name else type_name[:-2]
            count,cursor=checked(self.memorypack.read_count(data,offset,max_count=100000))
            if count==-1: return None,cursor
            rows=[]
            for _ in range(count):
                value,cursor=self.read(data,cursor,child,depth=depth+1); rows.append(value)
            return rows,cursor
        if type_name.startswith('System.Collections.Generic.Dictionary`2<'):
            # Improve: retain key/value pairs without turning reference keys into guessed strings.
            arguments=type_name.split('<',1)[1][:-1]
            nesting=0; split=None
            for index,character in enumerate(arguments):
                if character=='<': nesting+=1
                elif character=='>': nesting-=1
                elif character==',' and nesting==0: split=index; break
            if split is None: raise ValueError('Invalid dictionary type '+type_name)
            key_type,value_type=arguments[:split],arguments[split+1:]
            count,cursor=checked(self.memorypack.read_count(data,offset,max_count=100000))
            if count==-1: return None,cursor
            pairs=[]
            for _ in range(count):
                key,cursor=self.read(data,cursor,key_type,depth=depth+1)
                value,cursor=self.read(data,cursor,value_type,depth=depth+1)
                pairs.append({'key':key,'value':value})
            return pairs,cursor
        if offset>=len(data): raise ValueError('Truncated object')
        schema=self.schemas.get(type_name)
        # Fix: fixed serialized fields alone do not prove an unmanaged runtime struct (EntityPtr is framed).
        raw_size=self.unmanaged_sizes.get(type_name) if schema is None or schema.get('framesAsRawStruct') else None
        if raw_size is not None:
            if offset+raw_size>len(data): raise ValueError('Truncated native raw struct '+type_name)
            return {'storedType':type_name,'startOffset':offset,'endOffset':offset+raw_size,
                    'rawHex':data[offset:offset+raw_size].hex()},offset+raw_size
        # Fix: raw unmanaged structs have no null marker, even when their first byte is 0xff.
        if data[offset]==255: return None,offset+1
        if type_name in self.unions:
            tag=data[offset]; concrete=self.unions[type_name].get(tag)
            if concrete is None: raise ValueError(f'Unsupported union {type_name} tag {tag} at {offset}')
            row,end=self.read(data,offset+1,concrete,depth=depth+1)
            if row is None: raise ValueError('Null union body')
            row['unionTag']=tag
            return row,end
        if schema is None: raise ValueError(f'Unsupported nested type {type_name} at {offset}')
        cursor=offset
        if data[cursor]!=schema['serializedMemberCount']: raise ValueError(f'Changed member count for {type_name} at {cursor}')
        cursor+=1
        row={'storedType':type_name,'startOffset':offset}
        for field in schema['members']:
            try: row[field['name']],cursor=self.read(data,cursor,field['declaredType'],field,depth+1)
            except ValueError as exc:
                raise ValueError(f'{type_name}.{field["name"]} at {cursor}: {exc}') from exc
        row['endOffset']=cursor
        if type_name=='Beyond.Gameplay.ThreeDimRange':
            # Fix: keep the geometry API's names while retaining native m_center/m_size members.
            row['center']=row['m_center']; row['size']=row['m_size']
        return row,cursor

    def collection(self,data,offset,item_type):
        rows,end=self.read(data,offset,'System.Collections.Generic.List`1<'+item_type+'>')
        return {'startOffset':offset,'endOffset':end,'count':-1 if rows is None else len(rows),'rows':rows or []}

    def base_collection(self,data,offset):
        # Fix: retain the framing reader's row contract while decoding the current native layout.
        result=self.collection(data,offset,BASE_TYPE)
        cursor=offset+4
        for index,row in enumerate(result['rows']):
            if row is None:
                row={'startOffset':cursor,'endOffset':cursor+1,'value':None}
                result['rows'][index]=row
            else:
                row['memberCount']=self.schemas[BASE_TYPE]['serializedMemberCount']
                row['uniqueId']=str(row['uniqueId'])
            row['indexInCollection']=index
            cursor=row['endOffset']
        if result['count']==-1:
            result['value']=None
        return result

    def frame(self,data):
        if 'Beyond.Gameplay.LevelData' in self.schemas:
            return self.frame_current(data)
        def conditions(b,o):
            try: return self.collection(b,o,'Beyond.Gameplay.LevelFunctionAreaData+ConditionData')
            except ValueError as exc: raise self.leveldata.LevelFunctionAreaCodecError(str(exc)) from exc
        def specifics(b,o):
            try: return self.collection(b,o,'Beyond.Gameplay.LevelFunctionAreaData+FunctionAreaSpecificData')
            except ValueError as exc: raise self.leveldata.LevelFunctionAreaCodecError(str(exc)) from exc
        def condition(b,o,field,**kwargs):
            try: return self.read(b,o,'Beyond.Gameplay.ConditionRuntimeBase')
            except ValueError as exc: raise self.leveldata.LevelFunctionAreaCodecError(str(exc)) from exc
        def bases(b,o):
            try: return self.base_collection(b,o)
            except ValueError as exc: raise self.leveldata.LevelFunctionAreaCodecError(str(exc)) from exc
        # Improve: bind structural framing to independently derived current-build codecs.
        # No ResearchKit module or existing contract gate is modified.
        scope=dict(self.leveldata.frame_leveldata_named_prefix.__globals__)
        # Improve: close current dynamic-occlusion rows before the function-area field.
        def dynamic(b,o):
            try: return self.collection(b,o,'Beyond.Gameplay.LevelDynamicOccludeAreaData')
            except ValueError: return None
        scope.update(_decode_function_area_base_data_list=bases,decode_function_area_condition_list=conditions,decode_function_area_specific_data_list=specifics,decode_condition_runtime=condition,decode_dynamic_occlude_area_list=dynamic)
        reader=types.FunctionType(self.leveldata.frame_leveldata_named_prefix.__code__,scope)
        return reader(data)

    def frame_current(self,data):
        """Decode the native-declared prefix through functionArea; later fields remain outside scope."""
        schema=self.schemas['Beyond.Gameplay.LevelData']
        if not data or data[0]!=schema['serializedMemberCount']:
            raise ValueError('Changed LevelData member count')
        cursor=1; fields={}; closed=[]; stop=None
        for field in schema['members']:
            name=field['name']; start=cursor
            try:
                if name=='functionArea':
                    area_schema=self.schemas[field['declaredType']]
                    if cursor>=len(data): raise ValueError('Truncated functionArea')
                    if data[cursor]==255:
                        fields[name]={'value':None,'startOffset':cursor,'endOffset':cursor+1}
                        cursor+=1
                    else:
                        if data[cursor]!=area_schema['serializedMemberCount']:
                            raise ValueError('Changed functionArea member count')
                        cursor+=1; members={}
                        for member in area_schema['members']:
                            child=member['declaredType'].split('<',1)[1][:-1]
                            collection=self.base_collection(data,cursor) if member['name']=='baseData' else self.collection(data,cursor,child)
                            cursor=collection['endOffset']; members[member['name']]=collection
                        fields[name]={'startOffset':start,'endOffset':cursor,'members':members}
                    closed.append(name)
                    break
                value,cursor=self.read(data,cursor,field['declaredType'],field)
                fields[name]={'startOffset':start,'endOffset':cursor,'value':value}
                closed.append(name)
            except ValueError as exc:
                stop={'name':name,'startOffset':start,'status':'unsupported_current_native_prefix','diagnostic':str(exc)}
                break
        return {'fields':fields,'closedFields':closed,'openField':stop,'bytesConsumed':cursor,
                'opaqueRemainder':{'startOffset':cursor,'endOffset':len(data)},
                'evidenceBoundary':'Current native declarations through functionArea; string references retained without resolved text.'}


"""PE/IL2CPP reader primitives ported from ResearchKit; see ../../../../README.md."""

from __future__ import annotations


import struct


import sys


from collections import Counter, defaultdict


from dataclasses import dataclass


from pathlib import Path


from typing import Any, Iterable


@dataclass(frozen=True)
class Section:
    name: str
    offset: int
    size: int

    def count(self, record_size: int) -> int:
        if record_size <= 0:
            return 0
        return self.size // record_size


@dataclass
class TypeDef:
    index: int
    name_index: int
    namespace_index: int
    byval_type_index: int
    byref_type_index: int | None
    declaring_type_index: int
    parent_index: int
    element_type_index: int | None
    generic_container_index: int
    flags: int
    field_start: int
    method_start: int
    event_start: int
    property_start: int
    nested_types_start: int
    interfaces_start: int
    vtable_start: int
    interface_offsets_start: int
    method_count: int
    property_count: int
    field_count: int
    event_count: int
    nested_type_count: int
    vtable_count: int
    interfaces_count: int
    interface_offsets_count: int
    bitfield: int
    token: int


@dataclass
class MethodDef:
    index: int
    name_index: int
    declaring_type: int
    return_type: int
    return_parameter_token: int | None
    parameter_start: int
    generic_container_index: int
    token: int
    flags: int
    iflags: int
    slot: int
    parameter_count: int


@dataclass
class FieldDef:
    index: int
    name_index: int
    type_index: int
    token: int


@dataclass
class ParameterDef:
    index: int
    name_index: int
    token: int
    type_index: int


@dataclass
class ImageDef:
    index: int
    name_index: int
    assembly_index: int
    type_start: int
    type_count: int
    exported_type_start: int
    exported_type_count: int
    entry_point_index: int
    token: int
    custom_attribute_start: int
    custom_attribute_count: int


class MetadataParseError(RuntimeError):
    pass


def parse_type_reference_prefix(
    buf: bytes, pos: int, record_size: int
) -> tuple[int, int, int, int | None, int, int, int | None, int, int]:
    """Parse the selected v29+ TypeDef reference prefix and return the next offset."""

    name_index, namespace_index, byval_type_index = struct.unpack_from(
        "<iii", buf, pos
    )
    pos += 12
    declaring_type_index = struct.unpack_from("<i", buf, pos)[0]
    parent_index = struct.unpack_from("<i", buf, pos + 4)[0]
    pos += 8
    element_type_index: int | None = None
    if record_size in (88, 92):
        element_type_index = struct.unpack_from("<i", buf, pos)[0]
        pos += 4
    byref_type_index: int | None = None
    generic_container_index = struct.unpack_from("<i", buf, pos)[0]
    pos += 4
    return (
        name_index,
        namespace_index,
        byval_type_index,
        byref_type_index,
        declaring_type_index,
        parent_index,
        element_type_index,
        generic_container_index,
        pos,
    )


def parse_type_layout_tail(
    buf: bytes, pos: int, record_size: int
) -> tuple[
    int,
    tuple[int, ...],
    int | None,
    tuple[int, ...],
    int,
    int,
    int,
]:
    """Parse flags, ranges, counts, bitfield, and token at the measured boundary."""

    flags = struct.unpack_from("<I", buf, pos)[0]
    pos += 4
    starts = struct.unpack_from("<iiiiiiii", buf, pos)
    pos += 32
    extra_index: int | None = None
    if record_size == 92:
        # The selected Endfield v29 dialect inserts one int32 between the
        # eight range starts and the ushort count block. Its semantics are
        # intentionally left unnamed until established.
        extra_index = struct.unpack_from("<i", buf, pos)[0]
        pos += 4
    counts = struct.unpack_from("<HHHHHHHH", buf, pos)
    pos += 16
    bitfield = struct.unpack_from("<I", buf, pos)[0]
    token = struct.unpack_from("<I", buf, pos + 4)[0]
    pos += 8
    return flags, starts, extra_index, counts, bitfield, token, pos


class Metadata:
    def __init__(self, path: Path):
        self.path = path
        self.buf = path.read_bytes()
        self.magic, self.version = struct.unpack_from("<II", self.buf, 0)
        if self.magic != 0xFAB11BAF:
            raise MetadataParseError(
                f"unexpected metadata magic 0x{self.magic:08x} "
                "(expected 0xFAB11BAF)"
            )
        if self.version >= 38:
            raise MetadataParseError(
                "metadata versions >= 38 use newer dynamic index/header "
                "layouts; this lightweight parser currently supports v29-v37"
            )
        if self.version < 29:
            raise MetadataParseError(
                "this parser is focused on Endfield's v29+ metadata layout"
            )

        self.sections = self._parse_sections()
        self._string_cache: dict[int, str] = {}
        self._full_name_cache: dict[int, str] = {}
        self.type_record_size = self._type_record_size()
        self.method_record_size = self._method_record_size()
        self.field_record_size = 12
        self.parameter_record_size = 12
        self.image_record_size = 40

        self.types = self._parse_types()
        self.methods = self._parse_methods()
        self.fields = self._parse_fields()
        self.parameters = self._parse_parameters()
        self.images = self._parse_images()
        self.nested_parent_by_type_index = self._parse_nested_parents()
        self.image_name_by_type_index = self._map_images_to_types()
        self.type_name_by_metadata_type_index = self._map_metadata_type_names()

    def _parse_sections(self) -> dict[str, Section]:
        names = [
            "stringLiteral",
            "stringLiteralData",
            "string",
            "events",
            "properties",
            "methods",
            "parameterDefaultValues",
            "fieldDefaultValues",
            "fieldAndParameterDefaultValueData",
            "fieldMarshaledSizes",
            "parameters",
            "fields",
            "genericParameters",
            "genericParameterConstraints",
            "genericContainers",
            "nestedTypes",
            "interfaces",
            "vtableMethods",
            "interfaceOffsets",
            "typeDefinitions",
            "images",
            "assemblies",
            "fieldRefs",
            "referencedAssemblies",
            "attributeData",
            "attributeDataRange",
            "unresolvedVirtualCallParameterTypes",
            "unresolvedVirtualCallParameterRanges",
            "windowsRuntimeTypeNames",
            "windowsRuntimeStrings",
            "exportedTypeDefinitions",
        ]
        offset = 8
        sections: dict[str, Section] = {}
        for name in names:
            raw_offset, raw_size = struct.unpack_from("<Ii", self.buf, offset)
            sections[name] = Section(name, raw_offset, raw_size)
            offset += 8
        return sections

    def _type_record_size(self) -> int:
        section_size = self.sections["typeDefinitions"].size
        # Endfield's selected v29 metadata adds one int32 after the range-start
        # block, so the type record is 92 bytes. Infer the local stride instead
        # of assuming one dialect.
        for size in (92, 88, 84):
            if section_size % size == 0:
                return size
        # Fall back to the common post-v35 shape to produce a useful alignment
        # warning before parsing fails loudly.
        return 84 if self.version >= 35 else 92

    def _method_record_size(self) -> int:
        # v31 added returnParameterToken.
        return 36 if self.version >= 31 else 32

    def string(self, index: int) -> str:
        if index < 0:
            return ""
        cached = self._string_cache.get(index)
        if cached is not None:
            return cached
        section = self.sections["string"]
        start = section.offset + index
        end_limit = section.offset + section.size
        if start < section.offset or start >= end_limit:
            return f"<bad-string-index:{index}>"
        end = self.buf.find(b"\x00", start, end_limit)
        if end < 0:
            end = end_limit
        value = self.buf[start:end].decode("utf-8", errors="replace")
        self._string_cache[index] = value
        return value

    def _parse_types(self) -> list[TypeDef]:
        section = self.sections["typeDefinitions"]
        self._warn_if_misaligned(section, self.type_record_size)
        out: list[TypeDef] = []
        for index in range(section.count(self.type_record_size)):
            pos = section.offset + index * self.type_record_size
            (
                name_index,
                namespace_index,
                byval_type_index,
                byref_type_index,
                declaring_type_index,
                parent_index,
                element_type_index,
                generic_container_index,
                pos,
            ) = parse_type_reference_prefix(self.buf, pos, self.type_record_size)
            (
                flags,
                starts,
                _extra_index,
                counts,
                bitfield,
                token,
                pos,
            ) = parse_type_layout_tail(self.buf, pos, self.type_record_size)
            out.append(
                TypeDef(
                    index=index,
                    name_index=name_index,
                    namespace_index=namespace_index,
                    byval_type_index=byval_type_index,
                    byref_type_index=byref_type_index,
                    declaring_type_index=declaring_type_index,
                    parent_index=parent_index,
                    element_type_index=element_type_index,
                    generic_container_index=generic_container_index,
                    flags=flags,
                    field_start=starts[0],
                    method_start=starts[1],
                    event_start=starts[2],
                    property_start=starts[3],
                    nested_types_start=starts[4],
                    interfaces_start=starts[5],
                    vtable_start=starts[6],
                    interface_offsets_start=starts[7],
                    method_count=counts[0],
                    property_count=counts[1],
                    field_count=counts[2],
                    event_count=counts[3],
                    nested_type_count=counts[4],
                    vtable_count=counts[5],
                    interfaces_count=counts[6],
                    interface_offsets_count=counts[7],
                    bitfield=bitfield,
                    token=token,
                )
            )
        return out

    def _parse_methods(self) -> list[MethodDef]:
        section = self.sections["methods"]
        self._warn_if_misaligned(section, self.method_record_size)
        out: list[MethodDef] = []
        for index in range(section.count(self.method_record_size)):
            pos = section.offset + index * self.method_record_size
            name_index = self._i32(pos)
            declaring_type = self._i32(pos + 4)
            return_type = self._i32(pos + 8)
            pos += 12
            return_parameter_token = None
            if self.version >= 31:
                return_parameter_token = self._u32(pos)
                pos += 4
            parameter_start = self._i32(pos)
            generic_container_index = self._i32(pos + 4)
            token = self._u32(pos + 8)
            flags, iflags, slot, parameter_count = struct.unpack_from("<HHHH", self.buf, pos + 12)
            out.append(
                MethodDef(
                    index=index,
                    name_index=name_index,
                    declaring_type=declaring_type,
                    return_type=return_type,
                    return_parameter_token=return_parameter_token,
                    parameter_start=parameter_start,
                    generic_container_index=generic_container_index,
                    token=token,
                    flags=flags,
                    iflags=iflags,
                    slot=slot,
                    parameter_count=parameter_count,
                )
            )
        return out

    def _parse_fields(self) -> list[FieldDef]:
        section = self.sections["fields"]
        self._warn_if_misaligned(section, self.field_record_size)
        out: list[FieldDef] = []
        for index in range(section.count(self.field_record_size)):
            pos = section.offset + index * self.field_record_size
            out.append(
                FieldDef(
                    index=index,
                    name_index=self._i32(pos),
                    type_index=self._i32(pos + 4),
                    token=self._u32(pos + 8),
                )
            )
        return out

    def _parse_parameters(self) -> list[ParameterDef]:
        section = self.sections["parameters"]
        self._warn_if_misaligned(section, self.parameter_record_size)
        out: list[ParameterDef] = []
        for index in range(section.count(self.parameter_record_size)):
            pos = section.offset + index * self.parameter_record_size
            out.append(
                ParameterDef(
                    index=index,
                    name_index=self._i32(pos),
                    token=self._u32(pos + 4),
                    type_index=self._i32(pos + 8),
                )
            )
        return out

    def _parse_images(self) -> list[ImageDef]:
        section = self.sections["images"]
        self._warn_if_misaligned(section, self.image_record_size)
        out: list[ImageDef] = []
        for index in range(section.count(self.image_record_size)):
            pos = section.offset + index * self.image_record_size
            values = struct.unpack_from("<iiiiIiiIiI", self.buf, pos)
            out.append(
                ImageDef(
                    index=index,
                    name_index=values[0],
                    assembly_index=values[1],
                    type_start=values[2],
                    type_count=values[3],
                    exported_type_start=values[4],
                    exported_type_count=values[5],
                    entry_point_index=values[6],
                    token=values[7],
                    custom_attribute_start=values[8],
                    custom_attribute_count=values[9],
                )
            )
        return out

    def _parse_nested_parents(self) -> dict[int, int]:
        section = self.sections["nestedTypes"]
        parents: dict[int, int] = {}
        for parent in self.types:
            if parent.nested_type_count <= 0 or parent.nested_types_start < 0:
                continue
            for rel in range(parent.nested_type_count):
                pos = section.offset + (parent.nested_types_start + rel) * 4
                if pos < section.offset or pos + 4 > section.offset + section.size:
                    continue
                child_idx = self._i32(pos)
                if 0 <= child_idx < len(self.types):
                    parents[child_idx] = parent.index
        return parents

    def _map_images_to_types(self) -> dict[int, str]:
        mapping: dict[int, str] = {}
        for image in self.images:
            image_name = self.string(image.name_index)
            for idx in range(image.type_start, image.type_start + image.type_count):
                if 0 <= idx < len(self.types):
                    mapping[idx] = image_name
        return mapping

    def _map_metadata_type_names(self) -> dict[int, str]:
        mapping: dict[int, str] = {}
        for type_def in self.types:
            full_name = self.type_full_name(type_def)
            if type_def.byval_type_index >= 0:
                mapping.setdefault(type_def.byval_type_index, full_name)
            if type_def.byref_type_index is not None and type_def.byref_type_index >= 0:
                mapping.setdefault(type_def.byref_type_index, f"{full_name}&")
        return mapping

    def metadata_type_name(self, type_index: int) -> str:
        if type_index < 0:
            return ""
        return self.type_name_by_metadata_type_index.get(type_index, f"<type-index:{type_index}>")

    def type_name(self, type_def: TypeDef) -> str:
        return self.string(type_def.name_index)

    def type_namespace(self, type_def: TypeDef) -> str:
        return self.string(type_def.namespace_index)

    def type_full_name(self, type_def: TypeDef, seen: set[int] | None = None) -> str:
        if seen is None:
            cached = self._full_name_cache.get(type_def.index)
            if cached is not None:
                return cached
        if seen is None:
            seen = set()
        if type_def.index in seen:
            return self.type_name(type_def)
        seen.add(type_def.index)

        name = self.type_name(type_def)
        parent_idx = self.nested_parent_by_type_index.get(type_def.index)
        if parent_idx is not None and 0 <= parent_idx < len(self.types):
            full_name = f"{self.type_full_name(self.types[parent_idx], seen)}+{name}"
            self._full_name_cache[type_def.index] = full_name
            return full_name
        namespace = self.type_namespace(type_def)
        full_name = f"{namespace}.{name}" if namespace else name
        self._full_name_cache[type_def.index] = full_name
        return full_name

    def fields_for(self, type_def: TypeDef) -> list[FieldDef]:
        if type_def.field_count <= 0 or type_def.field_start < 0:
            return []
        return self.fields[type_def.field_start:type_def.field_start + type_def.field_count]

    def methods_for(self, type_def: TypeDef) -> list[MethodDef]:
        if type_def.method_count <= 0 or type_def.method_start < 0:
            return []
        return self.methods[type_def.method_start:type_def.method_start + type_def.method_count]

    def parameters_for(self, method: MethodDef) -> list[ParameterDef]:
        if method.parameter_count <= 0 or method.parameter_start < 0:
            return []
        return self.parameters[method.parameter_start:method.parameter_start + method.parameter_count]

    def _warn_if_misaligned(self, section: Section, record_size: int) -> None:
        if section.size % record_size:
            print(
                f"warning: section {section.name} size {section.size} is not "
                f"aligned to record size {record_size}",
                file=sys.stderr,
            )

    def _i32(self, pos: int) -> int:
        return struct.unpack_from("<i", self.buf, pos)[0]

    def _u32(self, pos: int) -> int:
        return struct.unpack_from("<I", self.buf, pos)[0]

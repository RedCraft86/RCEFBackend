"""Bounds-checked metadata ownership, method pointers, and union usage indexes."""

from __future__ import annotations
import struct

class ContextError(ValueError):
    def __init__(self, source: str, offset: int, expected: object, actual: object):
        self.diagnostics = {"source": source, "offset": offset,
                            "expected": expected, "actual": actual}
        super().__init__(f"{source}: offset=0x{offset:X} expected={expected!r} actual={actual!r}")


def method_token_pointer(token: int, pointers: bytes, *, source: str, offset: int) -> dict:
    """Select a module slot by MethodDef RID, never by global declaration order.

    The caller must establish the declaring type's exact image/module identity.
    Null slots are preserved; this is not a runtime MethodInfo or invocation.
    """
    if len(pointers)%8 or len(pointers)//8>1_000_000:
        raise ContextError(source,offset,'bounded exact qword method-pointer array',len(pointers))
    if type(offset) is not int or not 0 <= offset < 1<<64 or len(pointers)>(1<<64)-offset:
        raise ContextError(source,0,'bounded 64-bit pointer-array extent',offset)
    if type(token) is not int or not 0x06000001 <= token <= 0x06FFFFFF:
        raise ContextError(source,offset,'nonzero MethodDef RID token',token)
    slot=(token&0xFFFFFF)-1
    if slot>=len(pointers)//8:
        raise ContextError(source,offset,'MethodDef RID within module pointer count',slot)
    raw=pointers[slot*8:(slot+1)*8]
    return {'token':token,'slot':slot,'slotVa':offset+slot*8,
            'pointerVa':struct.unpack('<Q',raw)[0],'rawHex':raw.hex().upper()}


def type_image_owners(metadata: bytes, type_count: int, *, source: str) -> list[int]:
    """Exact partition of type indices by gated 40-byte image definitions.

    Native initialization copies each image's type start/count to its directory
    carrier. Overlaps are ambiguous, even if native lookup would choose one.
    Other image fields remain uninterpreted here.
    """
    if type(type_count) is not int or not 0 <= type_count <= 1_000_000:
        raise ContextError(source, 0xA8, 'bounded type count', type_count)
    if len(metadata) < 0xB0:
        raise ContextError(source, 0xA8, 'complete image section pair', len(metadata))
    offset, size = struct.unpack_from('<II', metadata, 0xA8)
    if offset < 0xB0 or offset > len(metadata) or size > len(metadata)-offset or size % 40:
        raise ContextError(source, 0xA8, 'bounded 40-byte image section', (offset, size))
    owners = [-1] * type_count
    for image_index in range(size // 40):
        row = offset + image_index * 40
        start, count = struct.unpack_from('<iI', metadata, row+8)
        if count == 0 and start == -1:
            continue
        if start < 0 or start > type_count or count > type_count-start:
            raise ContextError(source, row+8, f'type interval within [0,{type_count})', (start, count))
        for index in range(start, start+count):
            if owners[index] != -1:
                raise ContextError(source, row+8, 'unambiguous image owner',
                                   {'typeIndex': index, 'candidateImages': [owners[index], image_index]})
            owners[index] = image_index
    if -1 in owners:
        raise ContextError(source, offset, 'complete type/image partition', {'uncoveredTypeIndex':owners.index(-1)})
    return owners


def unresolved_usage_index(raw: bytes, count: int, *, tag: int, source: str, offset: int) -> int:
    """Exact on-disk encoding only; tag identity does not prove live resolution.

    The caller supplies the independently gated target table and expected tag.
    Resolved pointers, other tags, truncated cells and implicit tails fail closed.
    """
    if type(tag) is not int or tag not in (1, 2, 3, 5, 6):
        raise ContextError(source, offset, 'supported usage tag in (1,2,3,5,6)', tag)
    if len(raw) != 8:
        raise ContextError(source, offset, 'exact eight-byte usage cell', len(raw))
    if type(count) is not int or not 0 <= count <= 1_000_000:
        raise ContextError(source, offset, 'bounded usage target count', count)
    word = struct.unpack('<Q', raw)[0]
    if word > 0xFFFFFFFF or not word & 1 or word >> 29 != tag:
        raise ContextError(source, offset, f'unresolved 32-bit tag-{tag} usage encoding', hex(word))
    index = (word >> 1) & 0x0FFFFFFF
    if index >= count:
        raise ContextError(source, offset, f'usage target index in [0,{count})', index)
    return index


def match_image_modules(image_names: list[str], modules: list[tuple[str, int]], *, source: str) -> dict[str, int]:
    """Fail closed on duplicate names; do not reproduce native last-match wins."""
    if len(set(image_names)) != len(image_names):
        raise ContextError(source, 0, 'unique metadata image names', image_names)
    found = {}
    for index, (name, pointer) in enumerate(modules):
        if not isinstance(name, str) or not name or type(pointer) is not int or not 0 < pointer < 1 << 64:
            raise ContextError(source, index*8, 'named non-null module pointer', (name, pointer))
        if name in found:
            raise ContextError(source, index*8, 'unambiguous module name',
                               {'name':name, 'candidatePointers':[found[name],pointer]})
        found[name] = pointer
    if set(found) != set(image_names):
        raise ContextError(source, 0, 'complete image/module name correspondence',
                           {'missing':sorted(set(image_names)-set(found)), 'unexpected':sorted(set(found)-set(image_names))})
    return found

"""Bounded MemoryPack primitives used by the schema-driven volume decoder."""
from __future__ import annotations

import math
import struct


def read_i32(data: bytes, offset: int) -> tuple[int, int] | None:
    if offset < 0 or offset + 4 > len(data):
        return None
    return int.from_bytes(data[offset : offset + 4], "little", signed=True), offset + 4


def read_u32(data: bytes, offset: int) -> tuple[int, int] | None:
    if offset < 0 or offset + 4 > len(data):
        return None
    return int.from_bytes(data[offset : offset + 4], "little", signed=False), offset + 4


def read_u64(data: bytes, offset: int) -> tuple[int, int] | None:
    if offset < 0 or offset + 8 > len(data):
        return None
    return int.from_bytes(data[offset : offset + 8], "little", signed=False), offset + 8


def read_i64(data: bytes, offset: int) -> tuple[int, int] | None:
    if offset < 0 or offset + 8 > len(data):
        return None
    return int.from_bytes(data[offset : offset + 8], "little", signed=True), offset + 8


def read_count(
    data: bytes,
    offset: int,
    *,
    max_count: int = 100_000,
) -> tuple[int, int] | None:
    decoded = read_i32(data, offset)
    if decoded is None:
        return None
    count, offset = decoded
    if count < -1 or count > max_count:
        return None
    return count, offset


def read_bool(data: bytes, offset: int) -> tuple[bool, int] | None:
    if offset < 0 or offset >= len(data) or data[offset] not in (0, 1):
        return None
    return bool(data[offset]), offset + 1


def read_f32(data: bytes, offset: int) -> tuple[float, int] | None:
    if offset < 0 or offset + 4 > len(data):
        return None
    value = struct.unpack_from("<f", data, offset)[0]
    if not math.isfinite(value):
        return None
    return value, offset + 4


def read_f64(data: bytes, offset: int) -> tuple[float, int] | None:
    if offset < 0 or offset + 8 > len(data):
        return None
    value = struct.unpack_from("<d", data, offset)[0]
    if not math.isfinite(value):
        return None
    return value, offset + 8


def read_nullable_f32(data: bytes, offset: int) -> tuple[float | None, int]:
    """Read the native eight-byte Nullable<float> layout used in LevelData.

    The presence byte is followed by three zero padding bytes and a float.
    Reject truncated layouts, invalid presence/padding, and nonfinite values
    when present. The stored float is ignored when the value is absent.
    """
    if offset < 0 or offset + 8 > len(data):
        raise ValueError(f'Truncated nullable float at {offset}: expected 8 bytes')
    present = data[offset]
    if present not in (0, 1) or data[offset + 1:offset + 4] != b'\0\0\0':
        raise ValueError(f'Invalid nullable float presence/padding at {offset}')
    value = struct.unpack_from('<f', data, offset + 4)[0]
    if present and not math.isfinite(value):
        raise ValueError(f'Nonfinite nullable float at {offset}')
    return value if present else None, offset + 8

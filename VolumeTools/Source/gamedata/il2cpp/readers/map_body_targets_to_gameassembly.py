"""PE/IL2CPP reader primitives ported from ResearchKit; see ../../../../README.md."""

from __future__ import annotations


import re


import struct


from collections import Counter


from bisect import bisect_right


from pathlib import Path


from typing import Any


class PeImage:
    def __init__(self, path: Path):
        self.path = path
        self.buf = path.read_bytes()
        pe_offset = self.u32_at_file(0x3C)
        if self.u32_at_file(pe_offset) != 0x00004550:
            raise ValueError(f"not a PE image: {path}")
        coff = pe_offset + 4
        self.section_count = self.u16_at_file(coff + 2)
        optional_size = self.u16_at_file(coff + 16)
        optional = coff + 20
        self._check_file_range(optional, optional_size)
        if optional_size < 32:
            raise ValueError(f"{path}: file offset={optional} expected=at least 32 optional-header bytes actual={optional_size}")
        magic = self.u16_at_file(optional)
        if magic != 0x20B:
            raise ValueError("only PE32+ x64 images are supported")
        self.image_base = self.u64_at_file(optional + 24)
        section_offset = optional + optional_size
        self._check_file_range(section_offset, self.section_count * 40)
        self.sections: list[dict[str, Any]] = []
        for index in range(self.section_count):
            off = section_offset + index * 40
            name = self.buf[off:off + 8].split(b"\0", 1)[0].decode("ascii", errors="replace")
            virtual_size, virtual_address, raw_size, raw_pointer = struct.unpack_from(
                "<IIII", self.buf, off + 8
            )
            if raw_size:
                self._check_file_range(raw_pointer, raw_size)
            self.sections.append(
                {
                    "name": name,
                    "virtualAddress": virtual_address,
                    "virtualSize": virtual_size,
                    "rawPointer": raw_pointer,
                    "rawSize": raw_size,
                }
            )

    def u16_at_file(self, offset: int) -> int:
        self._check_file_range(offset, 2)
        return struct.unpack_from("<H", self.buf, offset)[0]

    def u32_at_file(self, offset: int) -> int:
        self._check_file_range(offset, 4)
        return struct.unpack_from("<I", self.buf, offset)[0]

    def u64_at_file(self, offset: int) -> int:
        self._check_file_range(offset, 8)
        return struct.unpack_from("<Q", self.buf, offset)[0]

    def _check_file_range(self, offset: int, size: int) -> None:
        if offset < 0 or size < 0 or offset > len(self.buf) or size > len(self.buf) - offset:
            raise ValueError(
                f"{self.path}: file offset={offset} expected={size} raw bytes "
                f"actual available={max(0, len(self.buf) - max(0, offset))} "
                f"fileLength={len(self.buf)}"
            )

    def file_offset_for_rva(self, rva: int) -> tuple[int | None, str]:
        matches = []
        for section in self.sections:
            start = section["virtualAddress"]
            size = max(section["virtualSize"], section["rawSize"])
            if start <= rva < start + size:
                matches.append(section)
        if len(matches) > 1:
            raise ValueError(f"{self.path}: RVA=0x{rva:x} expected=one section actual={len(matches)}")
        if not matches:
            return None, ""
        section = matches[0]
        relative = rva - section["virtualAddress"]
        offset = section["rawPointer"] + relative
        # A virtual-only tail (BSS) has no on-disk byte. Do not manufacture a
        # file offset that reads the next section's unrelated raw data.
        if relative >= section["rawSize"] or offset >= len(self.buf):
            return None, section["name"]
        return offset, section["name"]

    def file_offset_for_va(self, va: int) -> tuple[int | None, str, int]:
        rva = va - self.image_base
        file_offset, section = self.file_offset_for_rva(rva)
        return file_offset, section, rva

    def u32_at_va(self, va: int) -> int:
        return struct.unpack("<I", self.bytes_at_va(va, 4))[0]

    def u64_at_va(self, va: int) -> int:
        return struct.unpack("<Q", self.bytes_at_va(va, 8))[0]

    def bytes_at_va(self, va: int, size: int) -> bytes:
        offset, name, rva = self.file_offset_for_va(va)
        if offset is None:
            raise ValueError(
                f"{self.path}: VA=0x{va:x} RVA=0x{rva:x} section={name!r} "
                f"expected={size} raw-backed bytes actual=unbacked"
            )
        section = next(s for s in self.sections if s["name"] == name
                       and s["virtualAddress"] <= rva < s["virtualAddress"] + max(s["virtualSize"], s["rawSize"]))
        remaining = min(section["rawSize"] - (rva - section["virtualAddress"]), len(self.buf) - offset)
        if size < 0 or size > remaining:
            raise ValueError(
                f"{self.path}: VA=0x{va:x} file offset={offset} section={name!r} "
                f"expected={size} raw-backed bytes actual available={remaining}"
            )
        return self.buf[offset:offset + size]

    def c_string_at_va(self, va: int, *, limit: int = 512) -> str:
        offset, name, rva = self.file_offset_for_va(va)
        if offset is None:
            return f"<bad-va:0x{va:x}>"
        if limit < 0:
            raise ValueError(f"{self.path}: VA=0x{va:x} expected=nonnegative string limit actual={limit}")
        section = next(s for s in self.sections if s["name"] == name
                       and s["virtualAddress"] <= rva < s["virtualAddress"] + max(s["virtualSize"], s["rawSize"]))
        raw_end = min(len(self.buf), offset + limit, section["rawPointer"] + section["rawSize"])
        end = self.buf.find(b"\0", offset, raw_end)
        if end < 0:
            raise ValueError(f"{self.path}: VA=0x{va:x} expected=NUL within {raw_end - offset} raw bytes actual=unterminated")
        return self.buf[offset:end].decode("utf-8", errors="replace")


def _file_offset_to_va(pe: PeImage, file_offset: int) -> int | None:
    for section in pe.sections:
        start = section["rawPointer"]
        end = start + section["rawSize"]
        if start <= file_offset < end:
            return pe.image_base + section["virtualAddress"] + (file_offset - start)
    return None


def find_code_registration_candidates(
    pe: PeImage,
    expected_module_names: set[str] | None = None,
) -> list[int]:
    """Find Unity 2021 x64 CodeRegistration candidates by module-table shape.

    The final CodeRegistration count/pointer pair names the
    Il2CppCodeGenModule table. Every table entry points to a structure whose
    first pointer is an assembly image name. Matching the complete metadata
    image-name set makes this scan fail closed instead of trusting a nearby
    integer/pointer pair that only happens to look plausible.
    """
    expected_folded = (
        {name.casefold() for name in expected_module_names}
        if expected_module_names
        else None
    )
    expected_count = len(expected_folded) if expected_folded is not None else None
    candidates: list[int] = []
    buf = pe.buf
    for section in pe.sections:
        if section["name"] not in {".rdata", ".data"} or section["rawSize"] < 0x78:
            continue
        start = section["rawPointer"]
        end = start + section["rawSize"] - 0x78
        for file_offset in range(start, end + 1, 8):
            module_count = struct.unpack_from("<I", buf, file_offset + 0x68)[0]
            if expected_count is not None:
                if module_count != expected_count:
                    continue
            elif not 16 <= module_count <= 4096:
                continue

            module_table_va = struct.unpack_from("<Q", buf, file_offset + 0x70)[0]
            table_offset, _, _ = pe.file_offset_for_va(module_table_va)
            if table_offset is None or table_offset + module_count * 8 > len(buf):
                continue

            names: list[str] = []
            valid = True
            for index in range(module_count):
                module_va = struct.unpack_from("<Q", buf, table_offset + index * 8)[0]
                module_offset, _, _ = pe.file_offset_for_va(module_va)
                if module_offset is None or module_offset + 8 > len(buf):
                    valid = False
                    break
                name_va = struct.unpack_from("<Q", buf, module_offset)[0]
                name = pe.c_string_at_va(name_va, limit=260)
                if (
                    not name
                    or name.startswith("<bad-va:")
                    or any(ord(char) < 0x20 or ord(char) > 0x7E for char in name)
                ):
                    valid = False
                    break
                names.append(name)
            if not valid or len(set(name.casefold() for name in names)) != module_count:
                continue

            folded = {name.casefold() for name in names}
            if expected_folded is not None:
                if folded != expected_folded:
                    continue
            elif sum(
                name.casefold().endswith((".dll", ".exe")) or name == "__Generated"
                for name in names
            ) < max(1, module_count - 1):
                continue

            candidate = _file_offset_to_va(pe, file_offset)
            if candidate is not None:
                candidates.append(candidate)
    return sorted(set(candidates))


METADATA_REGISTRATION_FIELDS = (
    "genericClasses",
    "genericInsts",
    "genericMethodTable",
    "types",
    "methodSpecs",
    "fieldOffsets",
    "typeDefinitionsSizes",
    "metadataUsages",
)


def metadata_registration_summary(pe: PeImage, metadata_registration_va: int) -> dict[str, Any]:
    """Read the count/pointer pairs of Il2CppMetadataRegistration."""
    summary: dict[str, Any] = {"va": f"0x{metadata_registration_va:x}"}
    for index, name in enumerate(METADATA_REGISTRATION_FIELDS):
        base = metadata_registration_va + index * 0x10
        summary[f"{name}Count"] = pe.u32_at_va(base)
        summary[name] = f"0x{pe.u64_at_va(base + 8):x}"
    return summary


def metadata_registration_is_plausible(pe: PeImage, candidate_va: int) -> bool:
    """Reject candidates whose table pointers fall outside the image."""
    try:
        summary = metadata_registration_summary(pe, candidate_va)
    except (ValueError, struct.error):
        return False
    for name in METADATA_REGISTRATION_FIELDS:
        pointer = int(summary[name], 16)
        if not pointer:
            continue
        file_offset, _, _ = pe.file_offset_for_va(pointer)
        if file_offset is None:
            return False
    return True


_RIP_RELATIVE_LEA_RE = re.compile(
    rb"[\x48-\x4f]\x8d[\x05\x0d\x15\x1d\x25\x2d\x35\x3d]", re.DOTALL
)


_METADATA_REGISTRATION_CACHE: dict[tuple[str, int, int], int | None] = {}


def find_metadata_registration(pe: PeImage, code_registration_va: int) -> int | None:
    """Locate MetadataRegistration from the codegen registration call site.

    s_Il2CppCodegenRegistration() loads CodeRegistration and MetadataRegistration
    with adjacent rip-relative LEAs. CodeRegistration is already known, so scan
    executable sections for the LEA that resolves to it and validate the nearby
    LEA targets as MetadataRegistration candidates.

    The scan reads every executable byte of a ~280 MB image, and several audits
    ask for the same registration in one process, so the answer is memoized per
    loaded image and registration address.
    """
    # Synthetic images used by tests carry no path and are not memoized.
    image_path = getattr(pe, "path", None)
    cache_key = (
        (str(image_path), len(pe.buf), code_registration_va)
        if image_path is not None
        else None
    )
    if cache_key is not None and cache_key in _METADATA_REGISTRATION_CACHE:
        return _METADATA_REGISTRATION_CACHE[cache_key]

    candidates: list[int] = []
    for section in pe.sections:
        if not section["rawSize"]:
            continue
        data = pe.buf[section["rawPointer"]: section["rawPointer"] + section["rawSize"]]
        va_base = pe.image_base + section["virtualAddress"]
        limit = len(data) - 7
        targets: list[tuple[int, int]] = []
        for match in _RIP_RELATIVE_LEA_RE.finditer(data):
            offset = match.start()
            if offset >= limit:
                break
            disp = struct.unpack_from("<i", data, offset + 3)[0]
            va = va_base + offset
            targets.append((va, va + 7 + disp))
        anchors = [va for va, target in targets if target == code_registration_va]
        if not anchors:
            continue
        for anchor in anchors:
            for va, target in targets:
                if va == anchor or abs(va - anchor) > 0x40:
                    continue
                if target != code_registration_va:
                    candidates.append(target)
    registration = None
    for candidate in candidates:
        if metadata_registration_is_plausible(pe, candidate):
            registration = candidate
            break
    if cache_key is not None:
        _METADATA_REGISTRATION_CACHE[cache_key] = registration
    return registration

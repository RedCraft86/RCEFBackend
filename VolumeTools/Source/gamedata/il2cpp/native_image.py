"""Open the explicit copied build and resolve native method pointers for VolumeTools."""

from __future__ import annotations
import struct
from functools import cached_property
from pathlib import Path
from typing import Any
from Source.gamedata.il2cpp.context import match_image_modules, method_token_pointer, type_image_owners
from Source.gamedata.il2cpp.protocol import load_metadata_helper, load_native_mapper

NATIVE_MAPPER_PATH = Path(__file__).resolve().parent / "readers/map_body_targets_to_gameassembly.py"


METADATA_HELPER_PATH = Path(__file__).resolve().parent / "readers/catalog_option_flow_metadata.py"


class NativeImage:
    """The selected build's PE image and metadata plus the tables checks need."""

    def __init__(self, gameassembly: Path, metadata_path: Path, *, label: str = "native") -> None:
        self.label = label
        self.gameassembly = Path(gameassembly)
        self.metadata_path = Path(metadata_path)
        mapper = load_native_mapper(NATIVE_MAPPER_PATH)
        catalog = load_metadata_helper(METADATA_HELPER_PATH)
        self.mapper = mapper
        self.pe = mapper.PeImage(self.gameassembly)
        self.metadata = catalog.Metadata(self.metadata_path)
        image_names = {self.metadata.string(row.name_index) for row in self.metadata.images}
        registrations = mapper.find_code_registration_candidates(self.pe, image_names)
        if len(registrations) != 1:
            raise ValueError(f"{label}.native:code-registration={registrations!r}")
        self.code_registration = registrations[0]
        self.metadata_registration = mapper.find_metadata_registration(self.pe, self.code_registration)
        self.registration = mapper.metadata_registration_summary(self.pe, self.metadata_registration)

    @cached_property
    def owners(self) -> Any:
        return type_image_owners(self.metadata.buf, len(self.metadata.types), source=str(self.metadata_path))

    @cached_property
    def modules(self) -> dict[str, int]:
        pe, metadata = self.pe, self.metadata
        module_pointers = pe.bytes_at_va(
            pe.u64_at_va(self.code_registration + 0x70), len(metadata.images) * 8
        )
        module_rows = [
            (pe.c_string_at_va(pe.u64_at_va(pointer)), pointer)
            for (pointer,) in struct.iter_unpack("<Q", module_pointers)
        ]
        return match_image_modules(
            [metadata.string(row.name_index) for row in metadata.images],
            module_rows,
            source=str(self.gameassembly),
        )

    def type_name(self, definition: int) -> str:
        return self.metadata.type_full_name(self.metadata.types[definition])

    def method_pointer_va(self, method: Any) -> int:
        """The registered native pointer for a metadata method."""
        image = self.metadata.string(self.metadata.images[self.owners[method.declaring_type]].name_index)
        module = self.modules[image]
        count = self.pe.u32_at_va(module + 8)
        pointers = self.pe.bytes_at_va(self.pe.u64_at_va(module + 16), count * 8)
        row = method_token_pointer(method.token, pointers, source=str(self.gameassembly), offset=module + 16)
        return row["pointerVa"]

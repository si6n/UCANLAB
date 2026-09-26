# -*- coding: utf-8 -*-
"""CiA 306 Electronic Data Sheet (EDS) & Device Configuration File (DCF) Parser.

Complies with:
- CiA 306 (CANopen Electronic Data Sheet and Device Configuration File)
- CiA 301 v4.2.0 (CANopen Application Layer and Communication Profile)
- Konsolide Keşif Raporu §A.3.1 (Madde M) & §H.3 (Aksiyon 34):
  "CiA 306 EDS = düz INI, CiA login'iyle ücretsiz; ~200 satırlık parser, lisans engeli yok."
"""

from __future__ import annotations

import configparser
import re
from dataclasses import dataclass, field
from pathlib import Path

from src.core.errors import ProtocolError

_INDEX_RE = re.compile(r"^([0-9a-fA-F]{1,4})(?:sub([0-9a-fA-F]{1,2}))?$")


@dataclass(slots=True, frozen=True)
class CanOpenSubObject:
    """Sub-index parameter entry in a CANopen Object Dictionary."""

    sub_index: int
    parameter_name: str
    object_type: int = 7  # 7 = VAR
    data_type: int = 0x0005  # Standard CANopen data type code (e.g. 5 = UNSIGNED8, 7 = UNSIGNED32)
    access_type: str = "ro"  # "ro" | "wo" | "rw" | "rwr" | "rww"
    default_value: str | None = None
    pdo_mapping: bool = False
    low_limit: str | None = None
    high_limit: str | None = None


@dataclass(slots=True)
class CanOpenObject:
    """Primary index entry in a CANopen Object Dictionary (0x0001..0xFFFF)."""

    index: int
    parameter_name: str
    object_type: int = 7  # 7 = VAR, 8 = RECORD, 9 = ARRAY
    data_type: int = 0x0005
    access_type: str = "ro"
    default_value: str | None = None
    pdo_mapping: bool = False
    sub_objects: dict[int, CanOpenSubObject] = field(default_factory=dict)

    @property
    def is_complex(self) -> bool:
        """True if the object is an ARRAY or RECORD containing sub-indices."""
        return self.object_type in (8, 9) or bool(self.sub_objects)


@dataclass(slots=True, frozen=True)
class CanOpenDeviceInfo:
    """CANopen device metadata extracted from [DeviceInfo] section."""

    vendor_name: str = ""
    vendor_number: int = 0
    product_name: str = ""
    product_number: int = 0
    revision_number: int = 0
    baud_rates: tuple[int, ...] = field(default_factory=tuple)
    granularity: int = 8


@dataclass(slots=True)
class CanOpenObjectDictionary:
    """Complete CANopen Object Dictionary representation parsed from an EDS/DCF file."""

    file_version: str = "1.0"
    created_by: str = ""
    device_info: CanOpenDeviceInfo = field(default_factory=CanOpenDeviceInfo)
    objects: dict[int, CanOpenObject] = field(default_factory=dict)

    def get_object(self, index: int) -> CanOpenObject | None:
        return self.objects.get(index)

    def get_sub_object(self, index: int, sub_index: int) -> CanOpenSubObject | None:
        obj = self.get_object(index)
        if obj is None:
            return None
        return obj.sub_objects.get(sub_index)


def _parse_int_safe(val: str, default: int = 0) -> int:
    val = val.strip()
    try:
        if val.lower().startswith("0x") or any(c in val.lower() for c in "abcdef"):
            return int(val, 16)
        return int(val)
    except ValueError:
        return default


def parse_eds_text(text: str) -> CanOpenObjectDictionary:
    """Parse CiA 306 INI-formatted EDS or DCF string into CanOpenObjectDictionary."""
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    try:
        parser.read_string(text)
    except configparser.Error as exc:
        raise ProtocolError(f"Malformed CiA 306 EDS INI syntax: {exc}", code="EDS_SYNTAX_ERROR") from exc

    od = CanOpenObjectDictionary()

    # 1. FileInfo section
    if parser.has_section("FileInfo"):
        od.file_version = parser.get("FileInfo", "FileVersion", fallback="1.0")
        od.created_by = parser.get("FileInfo", "CreatedBy", fallback="")

    # 2. DeviceInfo section
    if parser.has_section("DeviceInfo"):
        sec = parser["DeviceInfo"]
        od.device_info = CanOpenDeviceInfo(
            vendor_name=sec.get("VendorName", ""),
            vendor_number=_parse_int_safe(sec.get("VendorNumber", "0")),
            product_name=sec.get("ProductName", ""),
            product_number=_parse_int_safe(sec.get("ProductNumber", "0")),
            revision_number=_parse_int_safe(sec.get("RevisionNumber", "0")),
            granularity=_parse_int_safe(sec.get("Granularity", "8")),
        )

    # 3. Object sections (e.g. [1000], [1018], [1018sub1])
    for section_name in parser.sections():
        m = _INDEX_RE.match(section_name.strip())
        if not m:
            continue

        raw_index, raw_sub = m.group(1), m.group(2)
        idx = int(raw_index, 16)
        sec = parser[section_name]

        if raw_sub is None:
            # Main object entry
            param_name = sec.get("ParameterName", f"Object_{idx:04X}")
            obj_type = _parse_int_safe(sec.get("ObjectType", "7"))
            data_type = _parse_int_safe(sec.get("DataType", "5"))
            access_type = sec.get("AccessType", "ro").lower()
            default_val = sec.get("DefaultValue")
            pdo_map = sec.get("PDOMapping", "0").strip() in ("1", "true", "True")

            if idx not in od.objects:
                od.objects[idx] = CanOpenObject(
                    index=idx,
                    parameter_name=param_name,
                    object_type=obj_type,
                    data_type=data_type,
                    access_type=access_type,
                    default_value=default_val,
                    pdo_mapping=pdo_map,
                )
            else:
                obj = od.objects[idx]
                obj.parameter_name = param_name
                obj.object_type = obj_type
                obj.data_type = data_type
                obj.access_type = access_type
                obj.default_value = default_val
                obj.pdo_mapping = pdo_map
        else:
            # Sub-object entry (e.g. 1018sub1)
            sub_idx = int(raw_sub, 16) if any(c in raw_sub.lower() for c in "abcdef") else int(raw_sub)
            param_name = sec.get("ParameterName", f"SubIndex_{sub_idx}")
            obj_type = _parse_int_safe(sec.get("ObjectType", "7"))
            data_type = _parse_int_safe(sec.get("DataType", "5"))
            access_type = sec.get("AccessType", "ro").lower()
            default_val = sec.get("DefaultValue")
            pdo_map = sec.get("PDOMapping", "0").strip() in ("1", "true", "True")

            sub_obj = CanOpenSubObject(
                sub_index=sub_idx,
                parameter_name=param_name,
                object_type=obj_type,
                data_type=data_type,
                access_type=access_type,
                default_value=default_val,
                pdo_mapping=pdo_map,
                low_limit=sec.get("LowLimit"),
                high_limit=sec.get("HighLimit"),
            )

            if idx not in od.objects:
                od.objects[idx] = CanOpenObject(
                    index=idx,
                    parameter_name=f"Record_{idx:04X}",
                    object_type=8,  # RECORD
                )
            od.objects[idx].sub_objects[sub_idx] = sub_obj

    return od


def parse_eds_file(path: Path | str) -> CanOpenObjectDictionary:
    """Read and parse CiA 306 EDS or DCF file from disk."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"CANopen EDS file not found: {p}")
    content = p.read_text(encoding="utf-8", errors="replace")
    return parse_eds_text(content)

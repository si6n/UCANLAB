# -*- coding: utf-8 -*-
"""Test suite for Phase 6 actions (Aksiyon 34, 35, 36) — CanOpen, Telemetry Schema, ISOBUS DDI."""

import json

from src.engine.storage.telemetry_schema import (
    TelemetryRecord,
    format_mosquitto_payload,
    format_questdb_ilp,
    get_duckdb_ddl,
    get_questdb_ddl,
)
from src.protocols.canopen.eds_parser import parse_eds_text
from src.protocols.isobus.ddi_registry import IsobusDdiRegistry


# 1. CANopen EDS (CiA 306) Tests
def test_canopen_eds_parser() -> None:
    sample_eds = """
[FileInfo]
FileName=test_device.eds
FileVersion=1.2
CreatedBy=CANopen Automation Team

[DeviceInfo]
VendorName=Universal Sensors GmbH
VendorNumber=0x00000042
ProductName=CAN-Angle-Sensor
ProductNumber=1001
RevisionNumber=2
Granularity=8

[1000]
ParameterName=DeviceType
ObjectType=7
DataType=7
AccessType=ro
DefaultValue=0x00020191
PDOMapping=0

[1018]
ParameterName=IdentityObject
ObjectType=8
SubNumber=5

[1018sub1]
ParameterName=VendorId
ObjectType=7
DataType=7
AccessType=ro
DefaultValue=0x00000042
PDOMapping=0
"""
    od = parse_eds_text(sample_eds)
    assert od.file_version == "1.2"
    assert od.created_by == "CANopen Automation Team"
    assert od.device_info.vendor_name == "Universal Sensors GmbH"
    assert od.device_info.vendor_number == 0x42
    assert od.device_info.product_name == "CAN-Angle-Sensor"

    # Index 0x1000
    obj1000 = od.get_object(0x1000)
    assert obj1000 is not None
    assert obj1000.parameter_name == "DeviceType"
    assert obj1000.access_type == "ro"
    assert obj1000.default_value == "0x00020191"

    # Index 0x1018 sub 1
    sub1 = od.get_sub_object(0x1018, 1)
    assert sub1 is not None
    assert sub1.parameter_name == "VendorId"
    assert sub1.default_value == "0x00000042"


# 2. Telemetry Schema & Storage Tests (QuestDB, DuckDB, Mosquitto 2.1.x)
def test_telemetry_schema_invariants() -> None:
    # Strict validation of synthetic boolean and source_channel
    rec = TelemetryRecord(
        timestamp_ns=1700000000000000000,
        arbitration_id=0x18FEEF00,
        signal_name="EngineSpeed",
        physical_value=1850.5,
        unit="rpm",
        source_channel="can0",
        synthetic=False,
    )
    assert rec.synthetic is False
    assert rec.source_channel == "can0"

    # Synthetic invariant test
    rec_synth = TelemetryRecord(
        timestamp_ns=1700000000000000000,
        arbitration_id=0x18FEEF00,
        signal_name="EstimatedTorque",
        physical_value=420.0,
        unit="Nm",
        source_channel="virtual",
        synthetic=True,
    )
    assert rec_synth.synthetic is True

    # DDL generation checks
    quest_ddl = get_questdb_ddl()
    assert "synthetic BOOLEAN" in quest_ddl
    assert "source_channel SYMBOL" in quest_ddl
    assert "PARTITION BY DAY WAL" in quest_ddl

    duck_ddl = get_duckdb_ddl()
    assert "synthetic BOOLEAN NOT NULL" in duck_ddl
    assert "source_channel VARCHAR NOT NULL" in duck_ddl

    # QuestDB ILP row format
    ilp = format_questdb_ilp(rec)
    assert "synthetic=false" in ilp
    assert "signal_name=EngineSpeed" in ilp
    assert "source_channel=can0" in ilp

    # Mosquitto 2.1.x payload
    mqtt_bytes = format_mosquitto_payload(rec)
    mqtt_json = json.loads(mqtt_bytes.decode("utf-8"))
    assert mqtt_json["synthetic"] is False
    assert mqtt_json["ch"] == "can0"
    assert mqtt_json["sig"] == "EngineSpeed"


# 3. ISOBUS DDI Pinned Registry Tests
def test_isobus_ddi_registry() -> None:
    reg = IsobusDdiRegistry()
    assert len(reg.PINNED_RELEASES) == 3
    assert "2025092601" in reg.PINNED_RELEASES
    assert "2026050501" in reg.PINNED_RELEASES
    assert "2026090801" in reg.PINNED_RELEASES

    # DDI lookup (0x0001 = Setpoint Volume Application Rate)
    ddi1 = reg.lookup_ddi(1)
    assert ddi1 is not None
    assert ddi1.hex_id == "0x0001"
    assert ddi1.unit == "mm3/m2"
    assert "Setpoint" in ddi1.name

    # Diff between releases
    diff = reg.diff_releases("2025092601", "2026050501")
    assert diff["from_release"] == "2025092601"
    assert diff["to_release"] == "2026050501"
    assert diff["ddi_count_delta"] == 16  # 748 - 732
    assert "citation" in diff
    assert "licensing_notice" in diff

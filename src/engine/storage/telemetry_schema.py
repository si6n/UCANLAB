# -*- coding: utf-8 -*-
"""Telemetry Storage Schema (QuestDB / DuckDB / Mosquitto 2.1.x).

Complies with:
- AGENTS.md §2.3: "No Fabricated Telemetry: Diagnostic decoders must never invent,
  smooth, or extrapolate raw sensor measurements without explicitly flagging them
  as synthetic/virtual channels."
- Konsolide Keşif Raporu §E.4, §A.3.1 (Madde N & O), §H.3 (Aksiyon 35):
  "Mosquitto 2.1.x + QuestDB 10.0.1 (Apache-2.0, 8M+ row/s) + DuckDB.
   DB şemasına 'synthetic: bool' + 'source_channel: str' sütunu."
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from src.core.errors import PlatformError


@dataclass(slots=True, frozen=True)
class TelemetryRecord:
    """Canonical telemetry measurement record."""

    timestamp_ns: int
    arbitration_id: int
    signal_name: str
    physical_value: float
    unit: str
    source_channel: str  # e.g. "can0", "can1", "j1939_pri"
    synthetic: bool  # AGENTS.md §2.3 mandatory flag: True if calculated/virtual/smoothed
    sequence: int = 0
    device_id: str = "local"

    def __post_init__(self) -> None:
        if not self.source_channel:
            raise PlatformError("TelemetryRecord.source_channel must be non-empty", code="SCHEMA_ERROR")
        if not self.signal_name:
            raise PlatformError("TelemetryRecord.signal_name must be non-empty", code="SCHEMA_ERROR")
        if not isinstance(self.synthetic, bool):
            raise PlatformError("TelemetryRecord.synthetic must be a strict boolean", code="SCHEMA_ERROR")


def get_questdb_ddl(table_name: str = "telemetry_timeseries") -> str:
    """Return QuestDB SQL DDL with daily partitioning and WAL mode."""
    return f"""CREATE TABLE IF NOT EXISTS {table_name} (
    timestamp TIMESTAMP,
    arbitration_id INT,
    signal_name SYMBOL,
    physical_value DOUBLE,
    unit SYMBOL,
    source_channel SYMBOL,
    synthetic BOOLEAN,
    sequence LONG,
    device_id SYMBOL
) TIMESTAMP(timestamp) PARTITION BY DAY WAL;"""


def get_duckdb_ddl(table_name: str = "telemetry_timeseries") -> str:
    """Return DuckDB SQL DDL with strict types and mandatory synthetic boolean."""
    return f"""CREATE TABLE IF NOT EXISTS {table_name} (
    timestamp TIMESTAMP_NS,
    arbitration_id UINTEGER,
    signal_name VARCHAR NOT NULL,
    physical_value DOUBLE NOT NULL,
    unit VARCHAR,
    source_channel VARCHAR NOT NULL,
    synthetic BOOLEAN NOT NULL,
    sequence UBIGINT NOT NULL,
    device_id VARCHAR NOT NULL
);"""


def get_sqlite_ddl(table_name: str = "telemetry_timeseries") -> str:
    """Return SQLite SQL DDL with check constraints."""
    return f"""CREATE TABLE IF NOT EXISTS {table_name} (
    timestamp_ns INTEGER NOT NULL,
    arbitration_id INTEGER NOT NULL,
    signal_name TEXT NOT NULL,
    physical_value REAL NOT NULL,
    unit TEXT,
    source_channel TEXT NOT NULL,
    synthetic INTEGER NOT NULL CHECK (synthetic IN (0, 1)),
    sequence INTEGER NOT NULL,
    device_id TEXT NOT NULL
);"""


def format_questdb_ilp(record: TelemetryRecord, table_name: str = "telemetry_timeseries") -> str:
    """Format single record as QuestDB Influx Line Protocol (ILP) row."""
    # Tags: signal_name, source_channel, device_id, synthetic
    # Fields: physical_value, arbitration_id, sequence, unit
    # Timestamp: in microseconds or nanoseconds
    syn_tag = "true" if record.synthetic else "false"
    unit_esc = record.unit.replace(" ", "_") if record.unit else "none"
    line = (
        f"{table_name},signal_name={record.signal_name},source_channel={record.source_channel},"
        f"device_id={record.device_id},synthetic={syn_tag} "
        f"physical_value={record.physical_value},arbitration_id={record.arbitration_id}i,"
        f"sequence={record.sequence}i,unit=\"{unit_esc}\" "
        f"{record.timestamp_ns}"
    )
    return line


def format_mosquitto_payload(record: TelemetryRecord) -> bytes:
    """Format record for Mosquitto 2.1.x edge publishing (max 2 MB packet budget)."""
    payload_dict = {
        "ts": record.timestamp_ns,
        "id": f"0x{record.arbitration_id:08X}",
        "sig": record.signal_name,
        "val": record.physical_value,
        "unit": record.unit,
        "ch": record.source_channel,
        "synthetic": record.synthetic,
        "seq": record.sequence,
        "dev": record.device_id,
    }
    raw = json.dumps(payload_dict, separators=(",", ":")).encode("utf-8")
    if len(raw) > 2 * 1024 * 1024:
        raise PlatformError("Mosquitto edge packet exceeds 2 MB budget", code="PACKET_TOO_LARGE")
    return raw

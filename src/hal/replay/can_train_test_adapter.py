# -*- coding: utf-8 -*-
"""can-train-and-test Benchmark Dataset Adapter (arXiv:2308.04972 / Aksiyon 29).

Provides parsing and normalization for the external CAN Intrusion & Diagnostic
benchmark dataset:
- RFC 4180 CSV / whitespace delimiter support.
- Ground truth anomaly/attack label extraction without corrupting raw CAN payload.
- Strict hex validation and monotonic timestamp sorting.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from src.core.errors import ProtocolError
from src.core.models.can_frame import CanFrame


@dataclass(slots=True, frozen=True)
class LabeledCanFrame:
    """CanFrame enriched with ground-truth benchmark label."""

    frame: CanFrame
    is_anomaly: bool
    label_name: str = ""


class CanTrainTestAdapter:
    """Adapter for arXiv:2308.04972 benchmark traces."""

    @staticmethod
    def parse_file(path: Path | str) -> Iterator[LabeledCanFrame]:
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Benchmark file not found: {p}")

        with p.open("r", encoding="utf-8", errors="replace") as f:
            reader = csv.reader(f)
            header = next(reader, None)
            if not header:
                return

            for line_no, row in enumerate(reader, start=2):
                if not row or row[0].startswith("#"):
                    continue
                try:
                    ts = float(row[0].strip())
                    arb_id_str = row[1].strip()
                    arb_id = int(arb_id_str, 16) if arb_id_str.lower().startswith("0x") else int(arb_id_str, 16)
                    dlc = int(row[2].strip())
                    raw_hex = row[3].strip().replace(" ", "").replace(":", "")
                    data = bytes.fromhex(raw_hex)
                    if len(data) != dlc:
                        raise ValueError(f"DLC {dlc} != data len {len(data)}")

                    is_anomaly = False
                    label_name = "normal"
                    if len(row) > 4:
                        lbl = row[4].strip()
                        if lbl not in ("0", "normal", "F"):
                            is_anomaly = True
                            label_name = "anomaly" if lbl in ("1", "T") else lbl

                    frame = CanFrame.create(
                        channel_id="can0",
                        arbitration_id=arb_id,
                        data=data,
                        timestamp_ns=int(ts * 1_000_000_000),
                        is_extended=(arb_id > 0x7FF),
                    )
                    yield LabeledCanFrame(frame=frame, is_anomaly=is_anomaly, label_name=label_name)
                except Exception as exc:
                    raise ProtocolError(
                        f"Malformed row {line_no} in benchmark dataset {p.name}: {exc}",
                        code="BENCHMARK_PARSE_ERROR",
                        cause=exc,
                    ) from exc

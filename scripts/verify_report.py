"""Verify an HTML service-report session seal (R2-EN1 companion tool).

Recomputes the canonical seal over the report's metadata fields and compares
it with the embedded value. For keyed (HMAC) reports the REPORT_SIGNING_KEY
must be supplied via --signing-key-hex; keyless reports verify as integrity
checksums only (not tamper-evident).

Usage:
    python scripts/verify_report.py --report exports/report.html --date "2026-09-18 12:00:00" \\
        --vin <VIN> --tech <NAME> --shop <SHOP> [--notes ...] [--dtcs a,b] [--stats k=v,...] \\
        [--signing-key-hex <64 hex chars>]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.engine.exporters.pdf_report import _sign_canonical  # noqa: E402


def _canonical(vin: str, tech: str, shop: str, notes: str, date: str, dtcs: str, stats: str) -> str:
    return (
        f"VIN={vin}|TECH={tech}|SHOP={shop}|NOTES={notes}|"
        f"DATE={date}|DTCS={dtcs}|STATS={stats}"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify a service-report session seal.")
    parser.add_argument("--report", required=True)
    parser.add_argument("--date", required=True)
    parser.add_argument("--vin", default="")
    parser.add_argument("--tech", default="")
    parser.add_argument("--shop", default="")
    parser.add_argument("--notes", default="")
    parser.add_argument("--dtcs", default="")
    parser.add_argument("--stats", default="")
    parser.add_argument("--signing-key-hex", default=None)
    args = parser.parse_args(argv)

    key = bytes.fromhex(args.signing_key_hex) if args.signing_key_hex else None
    digest, label = _sign_canonical(
        _canonical(args.vin, args.tech, args.shop, args.notes, args.date, args.dtcs, args.stats),
        key,
    )
    content = Path(args.report).read_text(encoding="utf-8")
    match = re.search(r"Session seal \[(.*?)\]:</strong> ([0-9A-F]+)", content)
    if not match:
        print("No session seal found in report.")
        return 1
    embedded_label, embedded = match.group(1), match.group(2)
    print(f"Embedded: {embedded_label} = {embedded}")
    print(f"Computed: {label} = {digest}")
    if embedded == digest:
        print("OK: seal matches" + (" (tamper-evident)" if key else " (integrity only)"))
        return 0
    print("MISMATCH: report content differs from sealed canonical form.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

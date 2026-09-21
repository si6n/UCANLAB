# -*- coding: utf-8 -*-
"""T2-4: `extended_pid_database.csv` ikizini JSON'dan yeniden uretir.

Neden bu dosya ayri:
    `scripts/rebuild_csv_exports.py` YALNIZ 3 CSV ikizini uretir
    (dtc_database, uds_did_database, j1939_spn_fmi_database). `extended_pid_database.csv`
    kapsam disidir ve ureticisi depoda YOK — bu yuzden T2-4'te PID katmani buyudugunde
    `scripts/data_integrity_audit.py` FAIL verdi:

        extended_pid_database.csv: 112 dolu satir ama extended_pid_database.json 226 kayit bekliyor

    Bu script bilgi tabanini URETMEZ; yalniz turev CSV ikizini JSON'dan yeniden uretir
    (`rebuild_csv_exports.py` ile ayni disiplin: uydurma yok, her hucre JSON'dan birebir).

Kolon semasi: dosyanin KENDI mevcut basligi korunur (15 kolon, git kaniti):
    pid,pid_hex,name,description,formula,unit,min_value,max_value,ecu_header,
    service,manufacturer,vehicle_model,category,data_source,confidence

Idempotent: ayni JSON ile tekrar kosmak ayni dosyayi uretir (byte-identical).
Yazim atomik: tmp + os.replace. Patch oncesi .bak_t24 (bir kez).

Kullanim:
    python tools/data_ingest/rebuild_extended_pid_csv.py --check
    python tools/data_ingest/rebuild_extended_pid_csv.py
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import shutil
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DIAG = ROOT / "data" / "diagnostics"
JSON_PATH = DIAG / "extended_pid_database.json"
CSV_PATH = DIAG / "extended_pid_database.csv"


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _atomic_write(path: Path, payload: bytes) -> None:
    tmp = path.with_suffix(path.suffix + f".tmp-{os.getpid()}-{time.monotonic_ns()}")
    tmp.write_bytes(payload)
    os.replace(tmp, path)


def _read_existing_header(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return next(csv.reader(handle))


def build_rows(header: list[str]) -> list[list[str]]:
    """CSV satirlarini JSON `pids` listesinden URETIR. Bilinmeyen kolon -> bos."""
    pids = json.loads(JSON_PATH.read_text(encoding="utf-8"))["pids"]
    rows: list[list[str]] = []
    for record in pids:
        if not isinstance(record, dict):
            continue
        rows.append([_text(record.get(column)) for column in header])
    return rows


def render(header: list[str], rows: list[list[str]], *, bom: bool, crlf: bool) -> bytes:
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, lineterminator="\r\n" if crlf else "\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8-sig" if bom else "utf-8")


def profile(path: Path) -> dict[str, Any]:
    filled = blank = ragged = 0
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        header = next(reader, None) or []
        for row in reader:
            if any(cell.strip() for cell in row):
                filled += 1
            else:
                blank += 1
            if header and len(row) != len(header):
                ragged += 1
    return {"filled": filled, "blank": blank, "ragged": ragged, "header_len": len(header)}


def run(check_only: bool) -> int:
    if not CSV_PATH.exists():
        print(f"DUR: {CSV_PATH} yok — sema (baslik) kaniti olmadan uretilmez.")
        return 1

    header = _read_existing_header(CSV_PATH)
    raw = CSV_PATH.read_bytes()
    bom = raw[:3] == b"\xef\xbb\xbf"
    crlf = b"\r\n" in raw

    rows = build_rows(header)
    before = profile(CSV_PATH)
    expected = len(json.loads(JSON_PATH.read_text(encoding="utf-8"))["pids"])

    print("[extended_pid_database.csv]")
    print(f"  baslik    : {len(header)} kolon  {header}")
    print(f"  mevcut    : dolu={before['filled']}  bos={before['blank']}  ragged={before['ragged']}")
    print(f"  kaynak    : extended_pid_database.json pids={expected}")
    print(f"  hedef     : {len(rows)} satir  (bom={bom} crlf={crlf})")
    print(f"  md5       : {hashlib.md5(raw).hexdigest()}")

    if check_only:
        print("\nSONUC: yalniz rapor (--check), dosya yazilmadi")
        return 0 if before["filled"] == expected else 1

    backup = CSV_PATH.with_suffix(".csv.bak_t24")
    if not backup.exists():
        shutil.copy2(CSV_PATH, backup)
        print(f"  yedek     : {backup.name}")

    _atomic_write(CSV_PATH, render(header, rows, bom=bom, crlf=crlf))

    after = profile(CSV_PATH)
    if after["filled"] != len(rows) or after["blank"] != 0 or after["ragged"] != 0:
        print(f"DUR: yazim dogrulamasi basarisiz {after}")
        return 1
    print(
        f"  yazildi   : dolu={after['filled']} bos={after['blank']} ragged={after['ragged']} "
        f"md5={hashlib.md5(CSV_PATH.read_bytes()).hexdigest()}"
    )
    print("\nSONUC: extended_pid_database.csv ikizi JSON'dan yeniden uretildi")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild extended_pid_database.csv from its JSON source")
    parser.add_argument("--check", action="store_true", help="Report only; write nothing")
    args = parser.parse_args()
    return run(args.check)


if __name__ == "__main__":
    sys.exit(main())

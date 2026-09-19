# -*- coding: utf-8 -*-
"""data/diagnostics CSV ikizlerini JSON bilgi tabanlarından yeniden üretir.

Kanıt (2026-09-19 denetimi):
    `f8bb892` commit'i (feat(diagnostics): J1939/SAE J1939DA enrichment ...)
    üç CSV ikizinin VERİ satırlarını boşalttı; yalnız başlık kaldı:

        dtc_database.csv        14.166 boş satır (başlık hariç, 0 dolu)
        j1939_spn_fmi_database   3.710 boş satır (başlık hariç, 0 dolu)
        uds_did_database.csv        68 boş satır (başlık hariç, 0 dolu)

    Boş satırlar doğru kolon sayısına sahiptir, bu yüzden hiçbir CSV okuyucu
    hata vermez — Excel'de "boş sayfa" olarak görünür. Sessiz veri kaybı.

Bu script, CSV'leri JSON bilgi tabanlarından yeniden üretir. Uydurma yok:
her hücre JSON'daki bir alandan birebir gelir.

Kolon semantiği (kanıt sütunları):
    dtc_database.csv       : dosyanın KENDİ mevcut başlığı korunur
                             (code,title,subsystem,severity,causes_count,steps_count)
    uds_did_database.csv   : dosyanın KENDİ mevcut başlığı korunur
                             (did_hex,did_int,name,name_tr,oem,subsystem,unit,scaling,offset,byte_length)
    j1939_spn_fmi_database : `scripts/expand_j1939_spn_database.py`'nin ürettiği
                             FMI-başına düzen (git f80eb36 kanıtıyla birebir):
                             SPN,FMI,Başlık (TR),Alt Sistem,PGN,FMI Tanımı,Öncelik,Saha Adımı
                             PGN bilinmiyorsa "0 (TBD)" (eski dosya kuralı).

Idempotent: aynı JSON ile tekrar koşmak aynı dosyayı üretir.
Yazım atomik: tmp dosya + os.replace (kısmi yazım bilgi tabanını bozamaz).

Kullanım:
    python scripts/rebuild_csv_exports.py --check   # yalnız rapor, yazmaz
    python scripts/rebuild_csv_exports.py           # yeniden üret
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import time
from collections.abc import Iterable
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIAG_DIR = ROOT / "data" / "diagnostics"

DTC_JSON = DIAG_DIR / "dtc_database.json"
DTC_CSV = DIAG_DIR / "dtc_database.csv"
UDS_JSON = DIAG_DIR / "uds_did_database.json"
UDS_CSV = DIAG_DIR / "uds_did_database.csv"
J1939_JSON = DIAG_DIR / "j1939_spn_fmi_database.json"
J1939_CSV = DIAG_DIR / "j1939_spn_fmi_database.csv"

# Kanıt: git f80eb36:data/diagnostics/j1939_spn_fmi_database.csv başlığı, aynı
# düzeni scripts/expand_j1939_spn_database.py (apply(), satır 575-579) üretir.
J1939_HEADER = [
    "SPN", "FMI", "Başlık (TR)", "Alt Sistem", "PGN", "FMI Tanımı", "Öncelik", "Saha Adımı",
]


def _md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def _has_bom(path: Path) -> bool:
    return path.read_bytes()[:3] == b"\xef\xbb\xbf"


def _read_existing_header(path: Path) -> list[str]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return next(csv.reader(handle))


def _atomic_write(path: Path, payload: bytes) -> None:
    """tmp + os.replace — yarıda kesilen bir yazım CSV'yi boş bırakamaz."""
    tmp = path.with_suffix(path.suffix + f".tmp-{os.getpid()}-{time.monotonic_ns()}")
    tmp.write_bytes(payload)
    os.replace(tmp, path)


def _render(header: list[str], rows: Iterable[list[object]], bom: bool) -> bytes:
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buf.getvalue().encode("utf-8-sig" if bom else "utf-8")


def _text(value: object) -> str:
    return "" if value is None else str(value)


def _count(value: object) -> int:
    if value is None:
        return 0
    if isinstance(value, (list, dict, str)):
        return len(value)
    return 1


def _hollow_stats(path: Path) -> tuple[int, int]:
    """(dolu_satır, boş_satır) — başlık hariç."""
    filled = blank = 0
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.reader(handle)
        next(reader, None)
        for row in reader:
            if any(cell.strip() for cell in row):
                filled += 1
            else:
                blank += 1
    return filled, blank


def _build_dtc_rows() -> list[list[object]]:
    db = json.loads(DTC_JSON.read_text(encoding="utf-8"))
    return [
        [code, _text(rec.get("title")), _text(rec.get("subsystem")), _text(rec.get("severity")),
         _count(rec.get("causes")), _count(rec.get("steps"))]
        for code, rec in db.items()
    ]


def _build_uds_rows() -> list[list[object]]:
    payload = json.loads(UDS_JSON.read_text(encoding="utf-8"))
    return [
        [did.get("did_hex"), did.get("did_int"), _text(did.get("name")), _text(did.get("name_tr")),
         _text(did.get("oem")), _text(did.get("subsystem")), _text(did.get("unit")),
         _text(did.get("scaling")), _text(did.get("offset")), _text(did.get("byte_length"))]
        for did in payload["dids"].values()
    ]


def _build_j1939_rows() -> list[list[object]]:
    spns = json.loads(J1939_JSON.read_text(encoding="utf-8"))["spns"]
    rows: list[list[object]] = []
    for key in sorted(spns, key=lambda k: int(k.split("_")[1])):
        entry = spns[key]
        pgn = entry.get("associated_pgn")
        acronym = entry.get("pgn_acronym")
        pgn_text = f"{pgn} ({acronym})" if pgn else "0 (TBD)"
        fault_matrix = entry.get("fault_matrix") or {}
        for fmi in sorted(fault_matrix, key=lambda k: int(k)):
            fm = fault_matrix[fmi]
            rows.append([
                entry.get("spn"), int(fmi), _text(fm.get("fault_title")), _text(entry.get("subsystem")),
                pgn_text, _text(fm.get("fmi_name")), _text(fm.get("severity")),
                _text(fm.get("diagnostic_action")),
            ])
    return rows


def _report(name: str, path: Path, header: list[str], rows: list[list[object]]) -> None:
    filled, blank = _hollow_stats(path)
    print(f"[{name}]")
    print(f"  mevcut : dolu={filled}  boş={blank}")
    print(f"  hedef  : {len(rows)} satır / {len(header)} kolon")
    print(f"  md5    : {_md5(path)}")


def run(check_only: bool) -> int:
    jobs: list[tuple[str, Path, list[str], list[list[object]]]] = [
        ("dtc_database.csv", DTC_CSV, _read_existing_header(DTC_CSV), _build_dtc_rows()),
        ("uds_did_database.csv", UDS_CSV, _read_existing_header(UDS_CSV), _build_uds_rows()),
        ("j1939_spn_fmi_database.csv", J1939_CSV, J1939_HEADER, _build_j1939_rows()),
    ]

    for name, path, header, rows in jobs:
        _report(name, path, header, rows)
        if check_only:
            continue
        _atomic_write(path, _render(header, rows, _has_bom(path)))
        filled, blank = _hollow_stats(path)
        if filled != len(rows) or blank != 0:
            raise SystemExit(f"DUR: {name} yazım doğrulaması başarısız (dolu={filled} boş={blank})")
        print(f"  yazıldı: dolu={filled} boş={blank} md5={_md5(path)}")

    tail = "yalnız rapor (--check), dosya yazılmadı" if check_only else "CSV ikizleri yeniden üretildi"
    print(f"\nSONUC: {tail}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Rebuild data/diagnostics CSV twins from their JSON sources")
    parser.add_argument("--check", action="store_true", help="Report only; write nothing")
    args = parser.parse_args()
    return run(args.check)


if __name__ == "__main__":
    raise SystemExit(main())

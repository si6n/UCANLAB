# -*- coding: utf-8 -*-
"""T2-4: vendored staging verisini `data/` altina KATMANLI olarak merge eder.

Prensipler (AGENTS.md §2.3, tasks/README "PAYLASILAN AGAC GUVENLIK KURALLARI"):
    * YALNIZ EKLEME (additive). Hicbir mevcut deger EZILMEZ.
    * Idempotent: iki kez kosmak ayni sonucu verir (ikinci kosuda 0 yeni alan).
    * Her yeni kayit/alan `_source_license` + `_source_ref` tasir.
    * Uydurma YOK: kaynakta olmayan alan bos/None kalir, doldurulmaz.
    * Patch oncesi .bak; yazim atomik (tmp + os.replace).

Bu script `src/engine/ai/` altina HICBIR SEY yazmaz (AI-TX izolasyonu bozulmaz).
Ayrica hicbir ag cagrisi yapmaz — veri `tools/data_ingest/staging/` altindan
(commit-pinli build-time vendor) okunur.

Kullanim:
    python tools/data_ingest/merge_staging_into_data.py --dry-run   # yazmaz
    python tools/data_ingest/merge_staging_into_data.py --apply
    python tools/data_ingest/merge_staging_into_data.py --counts    # yalniz olcum
    python tools/data_ingest/merge_staging_into_data.py --verify-idempotent
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
STAGING = ROOT / "tools" / "data_ingest" / "staging"
DIAG = ROOT / "data" / "diagnostics"

DTC_JSON = DIAG / "dtc_database.json"
EPID_JSON = DIAG / "extended_pid_database.json"
J1939_JSON = DIAG / "j1939_spn_fmi_database.json"

OBDEX_LICENSE = "CC0-1.0"
CANBOAT_LICENSE = "Apache-2.0"
SITRAK_LICENSE = "CC-BY-4.0"
DTCDB_LICENSE = "MIT"

# _source_ref sabitleri: commit-pinli URL (tools/data_ingest/provenance.json ile ayni).
OBDEX_COMMIT = "bc58b0eb7273226a1aabae98e956b70b8362bda1"
CANBOAT_COMMIT = "f7f088b49d58f5b4a0feb9b29c288b0ae18a7880"
DTCDB_COMMIT = "04c43d72e7db7197658b6f72fe582c5076d9eee8"
SITRAK_COMMIT = "fdb0c0d9daf0643975b0ff62e0ff69ef9c07f742"

OBDEX_REF = f"https://github.com/foerbsnavi/OBDex/blob/{OBDEX_COMMIT}"
CANBOAT_REF = f"https://github.com/canboat/canboat/blob/{CANBOAT_COMMIT}"
DTCDB_REF = f"https://github.com/Wal33D/dtc-database/blob/{DTCDB_COMMIT}/data/dtc_codes.db"
SITRAK_REF = f"https://github.com/STAS63-bit/sitrak-error-codes/blob/{SITRAK_COMMIT}/error-codes.json"

# ---------------------------------------------------------------------------
# Sayisal kanit biriktiricisi (rapor + idempotency iddiasi icin)
# ---------------------------------------------------------------------------
STATS: dict[str, Any] = {}


def _note(key: str, value: Any) -> None:
    STATS[key] = value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_json(path: Path, payload: Any) -> None:
    """tmp + os.replace — yarida kesilen yazim bilgi tabanini bozamaz."""
    tmp = path.with_suffix(path.suffix + f".tmp-{os.getpid()}-{time.monotonic_ns()}")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def backup(path: Path) -> Path | None:
    """Patch oncesi .bak. Zaten varsa dokunmaz (idempotent)."""
    if not path.exists():
        return None
    dest = path.with_suffix(path.suffix + ".bak_t24")
    if dest.exists():
        return dest
    shutil.copy2(path, dest)
    return dest


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 1 — OBDex (CC0-1.0): 9.533 generic DTC + 132 Mode 01/09 PID
# ---------------------------------------------------------------------------
def _obdex_generic_records() -> list[dict[str, Any]]:
    import yaml  # noqa: PLC0415 — build-time tool, runtime bagimliligi degil

    records: list[dict[str, Any]] = []
    for path in sorted((STAGING / "obdex" / "data" / "generic").glob("*_enriched.yaml")):
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        for rec in payload:
            records.append({**rec, "_obdex_file": path.name})
    return records


def _obdex_pid_records() -> list[dict[str, Any]]:
    import yaml  # noqa: PLC0415

    records: list[dict[str, Any]] = []
    for mode in ("mode01", "mode09"):
        path = STAGING / "obdex" / "data" / "pids" / f"{mode}.yaml"
        payload = yaml.safe_load(path.read_text(encoding="utf-8")) or []
        for rec in payload:
            records.append({**rec, "_obdex_file": path.name})
    return records


def _pid_identity(record: dict[str, Any]) -> tuple[str, str] | None:
    """(service, PID) kimligi — MEVCUT DB'nin tutarsiz `pid_hex` bicimini tolere eder.

    Kanit (T2-4 olcumu, iki ayri idempotency ihlali):
      1. `extended_pid_database.json` icinde `service="01"` icin `pid_hex` bazen
         `"0C"` bazen `"010C"` yazilmis (14 cift) — ham karsilastirma 14 kaydi
         "yeni" sanip DUPLICATE ekliyordu.
      2. Kaynak `mode01.yaml` PID'leri de iki bicimde geliyor (`"0B"` ve `"010B"`),
         yani duzeltilmis ama yine ham anahtar kullanan bir kosum IKINCI kez
         duplicate uretiyordu.

    Kimlik: servis oneki AYRISTIRILIR ve `pid` (int) alani varsa o kullanilir;
    hicbir deger uydurulmaz, mevcut alanlardan normalise edilir.
    """
    service = str(record.get("service") or "").strip()
    if not service:
        return None
    pid_value = record.get("pid")
    if isinstance(pid_value, int):
        return service, f"{pid_value:02X}"
    raw = str(record.get("pid_hex") or pid_value or "").strip().upper()
    if not raw:
        return None
    if raw.startswith(service) and len(raw) > len(service):
        raw = raw[len(service) :]
    if not raw:
        return None
    try:
        return service, f"{int(raw, 16):02X}"
    except ValueError:
        return service, raw


def merge_obdex(generic: bool, pids: bool, apply: bool) -> None:
    db = load_json(DTC_JSON)
    before = len(db)
    before_ext = len(load_json(EPID_JSON)["pids"])

    # --- 1a. generic DTC -> data/diagnostics/dtc_database.json (YENI KAYIT) ---
    if generic:
        records = _obdex_generic_records()
        src_counts: Counter[str] = Counter()
        added = 0
        for rec in records:
            code = str(rec.get("code") or "").strip().upper()
            if not code:
                continue
            src_counts[rec["_obdex_file"]] += 1
            if code in db:  # KATMANLI: mevcut kayit EZILMEZ
                continue
            title = rec.get("title") or {}
            description = rec.get("description") or {}
            causes = [
                ((c.get("label") or {}).get("en") or "").strip()
                for c in (rec.get("common_causes") or [])
                if isinstance(c, dict) and ((c.get("label") or {}).get("en") or "").strip()
            ]
            symptoms = [
                (s.get("en") or "").strip()
                for s in (rec.get("symptoms") or [])
                if isinstance(s, dict) and (s.get("en") or "").strip()
            ]
            db[code] = {
                "title": (title.get("en") or "").strip(),
                "subsystem": (rec.get("category") or "").strip(),
                "severity": "MEDIUM",
                "causes": causes,
                "steps": [],  # OBDex'te adim/prosedur alani YOK — uydurulmaz
                "symptoms": symptoms,
                "description": (description.get("en") or "").strip(),
                "obdex": {
                    "category": (rec.get("category") or "").strip(),
                    "affected_components": list(rec.get("affected_components") or []),
                    "common_causes": rec.get("common_causes") or [],
                    "symptoms": rec.get("symptoms") or [],
                    "repair": rec.get("repair") or {},
                    "title_de": (title.get("de") or "").strip(),
                    "description_de": (description.get("de") or "").strip(),
                    "flags": rec.get("flags") or {},
                },
                "_source_license": OBDEX_LICENSE,
                "_source_ref": OBDEX_REF,
            }
            added += 1
        _note(
            "obdex_generic",
            {
                "source_records": len(records),
                "source_unique_codes": sum(src_counts.values()),
                "per_file": dict(sorted(src_counts.items())),
                "before": before,
                "after": len(db),
                "added": added,
                "skipped_existing": len(records) - added,
            },
        )
        if apply:
            backup(DTC_JSON)
            atomic_write_json(DTC_JSON, db)

    # --- 1b. Mode 01/09 PID -> extended_pid_database.json (YENI KAYIT) ---
    if pids:
        existing_pids = load_json(EPID_JSON)
        pid_list = existing_pids["pids"]
        before_pids = len(pid_list)
        present = {ident for ident in (_pid_identity(p) for p in pid_list if isinstance(p, dict)) if ident}
        records = _obdex_pid_records()
        added_pids = 0
        for rec in records:
            mode = str(rec.get("mode") or "").strip()
            pid_hex = str(rec.get("pid") or "").strip().upper()
            if not mode or not pid_hex:
                continue
            # Kanonik: kaynak `mode01.yaml` PID'leri hem "0B" hem "010B" biciminde
            # geliyor; kayit icindeki `pid_hex` daima AYNI bicime yazilir ki
            # tekrar kosum dosyayi degistirmesin (idempotency kaniti).
            try:
                pid_hex = f"{int(pid_hex, 16):02X}"
            except ValueError:
                pass
            if (mode, pid_hex) in present:
                continue
            present.add((mode, pid_hex))
            name = rec.get("name") or {}
            pid_list.append(
                {
                    "pid": int(pid_hex, 16),
                    "pid_hex": pid_hex,
                    "name": (name.get("en") or "").strip(),
                    "description": "",
                    "formula": (rec.get("formula") or "").strip(),
                    "unit": (rec.get("unit") or "").strip(),
                    "ecu_header": "",
                    "service": mode,
                    "manufacturer": "",
                    "vehicle_model": "",
                    "ecu_module": "",
                    "category": "standard",
                    "data_source": "obdex",
                    "source_repo": OBDEX_REF,
                    "confidence": "standard",
                    "min_value": None,
                    "max_value": None,
                    "bytes": rec.get("bytes"),
                    "range": rec.get("range"),
                    "name_de": (name.get("de") or "").strip(),
                    "_source_license": OBDEX_LICENSE,
                    "_source_ref": OBDEX_REF,
                }
            )
            added_pids += 1
        existing_pids["metadata"]["total_pids"] = len(pid_list)
        existing_pids["metadata"].setdefault("sources", [])
        if "obdex" not in existing_pids["metadata"]["sources"]:
            existing_pids["metadata"]["sources"].append("obdex")
        # IDEMPOTENCY: `added_pids` bu kosumun sayisi DEGIL, katmanin toplam
        # buyuklugu yazilir. "Bu kosumda eklenen" degeri yazmak ikinci kosuda
        # 0'a dusup dosyayi degistiriyordu (idempotency ihlali, olculdu).
        obdex_present = sum(1 for p in pid_list if isinstance(p, dict) and p.get("data_source") == "obdex")
        existing_pids["metadata"]["obdex_layer"] = {
            "license": OBDEX_LICENSE,
            "source_ref": OBDEX_REF,
            "pids": obdex_present,
            "modes": ["01", "09"],
            "note": "Mode 01/09 standart PID katalogu; _source_license/_source_ref her yeni kayitta.",
        }
        _note(
            "obdex_pids",
            {
                "source_records": len(records),
                "before": before_pids,
                "after": len(pid_list),
                "added": added_pids,
                "skipped_existing": len(records) - added_pids,
            },
        )
        if apply:
            backup(EPID_JSON)
            atomic_write_json(EPID_JSON, existing_pids)

    STATS["extended_pid_before"] = before_ext


# ---------------------------------------------------------------------------
# 2 — Wal33D dtc-database (MIT): OEM katmani, YENI dosya (komşu dosyaya yazar)
# ---------------------------------------------------------------------------
def merge_dtcdb(apply: bool) -> None:
    src = STAGING / "dtcdb" / "dtc_codes.db"
    conn = sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT code, manufacturer, description, type, locale, is_generic, source_file "
        "FROM dtc_definitions ORDER BY manufacturer, code, locale"
    ).fetchall()
    conn.close()

    codes: dict[str, dict[str, Any]] = {}
    oem_index: dict[str, list[str]] = {}
    for row in rows:
        code = str(row["code"]).strip().upper()
        oem = (row["manufacturer"] or "").strip()
        codes.setdefault(code, {"type": row["type"], "_source_license": DTCDB_LICENSE, "_source_ref": DTCDB_REF})
        for locale, column in (("en", "description"), ("tr", "description")):
            if (row["locale"] or "") == locale:
                value = (row[column] or "").strip()
                if value:
                    codes[code].setdefault("description", {})[locale] = value
        if not row["is_generic"] and oem:
            oem_index.setdefault(code, []).append(oem)

    payload = {
        "metadata": {
            "title": "Wal33D/dtc-database OEM katmani (MIT)",
            "version": "1.0.0",
            "license": DTCDB_LICENSE,
            "source_ref": DTCDB_REF,
            "source_rows": len(rows),
            "total_codes": len(codes),
            "total_oem_entries": sum(len(v) for v in oem_index.values()),
            "oem_codes": len(oem_index),
            "manufacturers": sorted({m for v in oem_index.values() for m in v}),
            "note": (
                "T2-4 KATMANLI merge. Genel kod sozlugu mevcut dtc_database.json'un "
                "anahtar semasini (P/B/C/U) bozmamak icin oraya KARISTIRILMADI; "
                "komsu dosya olarak tutulur (T25/HANDOFF_TUR24 §4.4 kuralinin aynisi). "
                "Hicbir mevcut kayit ezilmedi."
            ),
        },
        "codes": codes,
        "oem_index": {k: sorted(set(v)) for k, v in sorted(oem_index.items())},
    }
    out = DIAG / "dtc_database_oem_layer.json"
    _note(
        "dtcdb",
        {
            "source_rows": len(rows),
            "codes": len(codes),
            "oem_codes": len(oem_index),
            "oem_entries": sum(len(v) for v in oem_index.values()),
            "manufacturers": len(payload["metadata"]["manufacturers"]),
            "target": "data/diagnostics/dtc_database_oem_layer.json",
            "existed_before": out.exists(),
            "unchanged": out.exists() and load_json(out) == payload if out.exists() else False,
        },
    )
    if apply:
        backup(out)
        atomic_write_json(out, payload)


# ---------------------------------------------------------------------------
# 3 — canboat (Apache-2.0): 628 NMEA-2000 PGN + J1939 DM1
# ---------------------------------------------------------------------------
def merge_canboat(apply: bool) -> None:
    canboat = load_json(STAGING / "canboat" / "canboat.json")
    pgns = canboat["PGNs"]
    # JSON tarafi PascalCase: PGN / Fields / Id / Name / BitLength
    export = {
        "metadata": {
            "title": "canboat PGN katalogu (NMEA-2000) + J1939 DM1",
            "version": canboat.get("Version"),
            "schema_version": canboat.get("SchemaVersion"),
            "license": CANBOAT_LICENSE,
            "notice": "NOTICE korunur: data/licenses/NOTICE.canboat (Kees Verruijt, CANboat)",
            "source_ref": CANBOAT_REF,
            "copyright": canboat.get("Copyright"),
            "pgn_count": len(pgns),
            "pgn_number_range": [min(p["PGN"] for p in pgns), max(p["PGN"] for p in pgns)],
            "j1939_pgn_count": 0,
            "j1939_note": (
                "canboat.json J1939 ICERMEZ (T1-3 dogrulamasi: PGN 65226 yok, SPN/FMI "
                "alani olan PGN 0, J1939 tipli PGN 0). J1939 DM1 ayri artefakttir."
            ),
            "schema_casing": "PascalCase (PGN/Fields/Id) — J1939 YAML tarafi kucuk harf (pgn/fields)",
            "note": (
                "T2-4 KATMANLI export. Yeni PGN dosyasi; mevcut DBC/katalog dosyalarina "
                "YAZILMADI. Her PGN kaydinda _source_license + _source_ref."
            ),
        },
        "pgns": {},
        "pgns_by_number": {},
        "j1939": {},
    }
    # DIKKAT: canboat.json'da PGN numarasi TEKIL DEGIL (ayni PGN icin birden fazla
    # varyant kaydi var; ornek kayit PGN 59392 "Fallback": true). PGN'i sozluk
    # anahtari yapmak 628 kaydi sessizce 348'e dusururdu. Bu yuzden hem liste
    # (kayipsiz) hem PGN->indeks haritasi yazilir; `pgns_by_number` bir PGN icin
    # TUM indeksleri tutar, hicbir varyant atilmaz.
    pgn_list: list[dict[str, Any]] = []
    pgns_by_number: dict[str, list[int]] = {}
    for pgn in pgns:
        index = len(pgn_list)
        pgn_list.append(
            {
                "pgn": pgn["PGN"],
                "id": pgn.get("Id"),
                "description": pgn.get("Description"),
                "priority": pgn.get("Priority"),
                "type": pgn.get("Type"),
                "length": pgn.get("Length"),
                "min_length": pgn.get("MinLength"),
                "complete": pgn.get("Complete"),
                "fallback": pgn.get("Fallback"),
                "field_count": pgn.get("FieldCount"),
                "fields": pgn.get("Fields") or [],
                "missing": pgn.get("Missing") or [],
                "url": pgn.get("URL"),
                "_source_license": CANBOAT_LICENSE,
                "_source_ref": CANBOAT_REF,
            }
        )
        pgns_by_number.setdefault(str(pgn["PGN"]), []).append(index)
    export["pgns"] = pgn_list
    export["pgns_by_number"] = pgns_by_number
    export["metadata"]["distinct_pgn_numbers"] = len(pgns_by_number)
    export["metadata"]["variant_records"] = len(pgn_list) - len(pgns_by_number)

    dm1 = load_json_yaml(STAGING / "canboat" / "j1939_065226-activeTroubleCodes.yaml")
    export["j1939"][str(dm1["pgn"])] = {
        "pgn": dm1["pgn"],
        "id": dm1.get("id"),
        "description": dm1.get("description"),
        "type": dm1.get("type"),
        "fields": dm1.get("fields") or [],
        "notes": dm1.get("notes"),
        "_source_license": CANBOAT_LICENSE,
        "_source_ref": (f"{CANBOAT_REF}/database/j1939/pgns/065226-activeTroubleCodes.yaml"),
    }
    export["metadata"]["j1939_pgn_count"] = len(export["j1939"])

    out = DIAG / "canboat_pgn_reference.json"
    _note(
        "canboat",
        {
            "n2k_pgn_records": len(export["pgns"]),
            "distinct_pgn_numbers": export["metadata"]["distinct_pgn_numbers"],
            "j1939_pgns": len(export["j1939"]),
            "pgn_range": export["metadata"]["pgn_number_range"],
            "target": "data/diagnostics/canboat_pgn_reference.json",
            "existed_before": out.exists(),
            "unchanged": out.exists() and load_json(out) == export if out.exists() else False,
        },
    )
    if apply:
        backup(out)
        atomic_write_json(out, export)


def load_json_yaml(path: Path) -> Any:
    import yaml  # noqa: PLC0415

    return yaml.safe_load(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# 4 — SITRAK (CC-BY-4.0): SPN/FMI ailesi, j1939 DB'ye KATMANLI yeni alt-sozluk
# ---------------------------------------------------------------------------
# T2-4 SHELL-ROW DEFECT (AGENTS.md §2.3 uydurma yasagi).
# Eski davranis (defect):
#     key = f"SPN_{spn}"                      # ham kaynak stringi: "0x01" -> "SPN_0x01"
#     if key not in spns:                     # gercek kayit YOKSA ...
#         spns[key] = {"spn": ...}            # ... yalniz `spn` tasiyan SHELL uretiyordu
# Sonuc: 10 adet "SPN_0x*" (hex bicimli ham anahtar) + 3 adet isimsiz sayisal shell
# (SPN_139/140/141) => DB 4253'ten 4266'ya cikti; 13 kayit name/title_tr'siz.
#
# DuZELTME IKI KATMANLI:
#   (1) `canonical_spn_key` TEK yerde, TEK amacla: kaynak `spn` degerini kanonik
#       ONDALIK anahtara cevirir. Hex bicimini ("0x01") DESTEKLEMEZ — boylece
#       `SPN_0x*` anahtari artik URETILEMEZ (fail-closed: ValueError).
#   (2) `merge_sitrak` MEVCUT olmayan bir SPN icin KAYIT ACMAZ. Kaynak yalnizca
#       mevcut bir gercek kayda katman ekleyebilir; aksi halde UYARI verip atlar
#       (atlananlar sayilir ve `_note` ile raporlanir — sessiz kayip yok).

_SITRAK_SKIPPED: list[dict[str, str]] = []


def canonical_spn_key(raw: Any) -> str:
    """Ham kaynak `spn` degerini kanonik ``SPN_<ondalik>`` sozluk anahtarina cevirir.

    Fail-closed: yalniz ONDALIK tam sayi (veya bare ondalik string) kabul edilir.
    Hex onekli degerler ("0x01") KABUL EDILMEZ; cagiran taraf bunlari atlar.
    Uydurma anahtar (`SPN_0x01`) bu fonksiyonun ciktisi OLARAK URETILEMEZ.
    """
    if isinstance(raw, bool):  # bool int'in alt sinifi — acikca reddet
        raise ValueError(f"gecersiz SPN (bool): {raw!r}")
    if isinstance(raw, int):
        if raw < 0:
            raise ValueError(f"gecersiz SPN (negatif): {raw!r}")
        return f"SPN_{raw}"
    text = str(raw or "").strip()
    if not text:
        raise ValueError("gecersiz SPN (bos)")
    if text.lower().startswith("0x"):
        raise ValueError(f"gecersiz SPN (hex bicimi kabul edilmez): {raw!r}")
    if not text.isdigit():
        raise ValueError(f"gecersiz SPN (ondalik degil): {raw!r}")
    return f"SPN_{int(text, 10)}"


def assert_no_shell_rows(spns: dict[str, Any]) -> list[str]:
    """Uydurma/shell satir tarayicisi (fail-closed pre-condition).

    Iki imza: (a) hex bicimli ham anahtar (`SPN_0x..`), (b) `name` ya da
    `title_tr` bos olan kayit. Ikisi de AGENTS.md §2.3 ihlalidir. Liste bos
    degilse cagiran taraf MErge'u DURDURMALIDIR (veriyi daha da kirletmesin).
    """
    problems: list[str] = []
    for key, entry in spns.items():
        if key.startswith("SPN_0x"):
            problems.append(f"hex bicimli uydurma anahtar: {key}")
        if not isinstance(entry, dict) or not entry.get("name") or not entry.get("title_tr"):
            problems.append(f"name/title_tr eksik shell kayit: {key}")
    return problems


def merge_sitrak(apply: bool) -> None:
    records = load_json(STAGING / "sitrak" / "sitrak_error-codes.json")
    db = load_json(J1939_JSON)
    spns = db["spns"]

    # FAIL-CLOSED pre-condition: DB'de shell satir varsa hicbir sey yazmadan dur.
    shell_problems = assert_no_shell_rows(spns)
    if shell_problems:
        raise RuntimeError(
            f"T2-4: DB'de uydurma shell satir(lar) var; merge DURDURULDU (once temizleyin): {shell_problems[:20]}"
        )

    entries: dict[str, list[dict[str, Any]]] = {}
    for rec in records:
        raw_spn = rec.get("spn")
        try:
            key = canonical_spn_key(raw_spn)  # fail-closed: hex/uydurma anahtar yok
        except ValueError as exc:
            _SITRAK_SKIPPED.append({"spn": str(raw_spn), "reason": str(exc)})
            continue
        entries.setdefault(key, []).append(
            {
                "spn": key[len("SPN_") :],  # kanonik ONDALIK string (ham "0x.." degil)
                "fmi": str(rec.get("fmi") or "").strip(),
                "dtc": str(rec.get("dtc") or "").strip(),
                "system": str(rec.get("system") or "").strip(),
                "description_ru": str(rec.get("description_ru") or "").strip(),
                "description_en": str(rec.get("description_en") or "").strip(),
                "_source_license": SITRAK_LICENSE,
                "_source_ref": SITRAK_REF,
            }
        )

    for key, rows in entries.items():
        rows.sort(key=lambda r: (int(r["fmi"]) if r["fmi"].isdigit() else 999, r["fmi"]))
        entry = spns.get(key)
        if not isinstance(entry, dict):
            # FAIL-CLOSED (T2-4): gercek kayit yoksa KAYIT ACILMAZ. Shell uretmek
            # name/title_tr'siz uydurma kayit demektir (AGENTS.md §2.3). UYARI ver
            # ve atla; kaynak satirlar staging'de kalir, hicbir veri silinmez.
            _SITRAK_SKIPPED.append(
                {
                    "spn": key[len("SPN_") :],
                    "reason": f"{key}: DB'de gercek kayit yok — uydurma shell OLUSTURULMADI",
                }
            )
            continue
        # Mevcut anahtarlari ASLA ezme; yalniz yoksa yaz.
        entry.setdefault("sitrak_fmi_family", rows)
        entry.setdefault("sitrak_systems", sorted({r["system"] for r in rows if r["system"]}))
        entry.setdefault("_source_license_sitrak", SITRAK_LICENSE)
        entry.setdefault("_source_ref_sitrak", SITRAK_REF)
        entry.setdefault(
            "sitrak_attribution",
            "Северные данные: МегаДата / megadata.pro — CC BY 4.0 (https://creativecommons.org/licenses/by/4.0/)",
        )

    db["metadata"]["total_spns"] = len(spns)
    db["metadata"]["sitrak_layer"] = {
        "license": SITRAK_LICENSE,
        "attribution": "© МегаДата / megadata.pro (https://megadata.pro) — CC BY 4.0",
        "source_ref": SITRAK_REF,
        "source_records": len(records),
        "spn_keys": len(entries),
        "systems": len({r["system"] for rows in entries.values() for r in rows if r["system"]}),
        "note": (
            "T2-4 KATMANLI merge: yalniz YENI alter anahtar (sitrak_fmi_family / "
            "sitrak_systems / _source_license_sitrak / _source_ref_sitrak / "
            "sitrak_attribution) eklendi. Mevcut alanlar (fault_matrix, causes, steps, "
            "procedures_full, ...) DOKUNULMADI."
        ),
    }
    # Katmanlilik/fail-closed iddiasi: SPN anahtar sayisi merge boyunca DEGISMEZ.
    # ONEMLI: karsilastirma `.bak_t24` (GERCEK merge-oncesi durum) ile yapilir.
    # Daha once `merge_sitrak._before_keys` (script basinda okunan CANLI dosya)
    # kullaniliyordu; ikinci kosumda canli dosya zaten merged oldugu icin bu kume
    # yanlis cikiyor ve iddia sahte sekilde `false` donuyordu (olculdu).
    _bak = J1939_JSON.with_suffix(".json.bak_t24")
    pre_merge_keys = set(load_json(_bak)["spns"]) if _bak.exists() else set(spns)
    _note(
        "sitrak",
        {
            "source_records": len(records),
            "spn_keys_tagged": len(entries),
            "systems": db["metadata"]["sitrak_layer"]["systems"],
            "spn_before": len(pre_merge_keys),
            "spn_after": len(spns),
            # T2-4: fail-closed. `new_spn_records_created` bu tasarimda HER ZAMAN 0'dir;
            # sifirdan farkli olmasi uydurma shell uretildigini gosterir (regresyon olsun
            # diye ham olcum, sabit degil).
            "new_spn_records_created": len(set(spns) - pre_merge_keys),
            "existing_spns_enriched": sum(1 for k in entries if k in pre_merge_keys),
            "skipped_no_real_record": len(_SITRAK_SKIPPED),
            "skipped_detail": _SITRAK_SKIPPED,
            # Katmanlilik/fail-closed iddiasi: SPN anahtar sayisi merge boyunca DEGISMEZ
            # (yalniz mevcut kayitlara alter anahtar eklenir; yeni kayit acilmaz).
            "db_keys_unchanged": set(spns) == pre_merge_keys,
            "target": "data/diagnostics/j1939_spn_fmi_database.json",
        },
    )
    if apply:
        backup(J1939_JSON)
        atomic_write_json(J1939_JSON, db)


# ---------------------------------------------------------------------------
# Olcum / dogrulama
# ---------------------------------------------------------------------------
def measure_counts() -> dict[str, Any]:
    dtc = load_json(DTC_JSON)
    epid = load_json(EPID_JSON)
    j1939 = load_json(J1939_JSON)
    oem_path = DIAG / "dtc_database_oem_layer.json"
    pgn_path = DIAG / "canboat_pgn_reference.json"

    tagged_dtc = sum(1 for v in dtc.values() if isinstance(v, dict) and v.get("_source_license"))
    sitrak_spns = sum(1 for v in j1939["spns"].values() if isinstance(v, dict) and v.get("sitrak_fmi_family"))
    sitrak_rows = sum(len(v.get("sitrak_fmi_family") or []) for v in j1939["spns"].values() if isinstance(v, dict))

    return {
        "dtc_database.json": {
            "records": len(dtc),
            "level2_source_tagged": tagged_dtc,
            "obdex_keys": sum(1 for v in dtc.values() if isinstance(v, dict) and "obdex" in v),
        },
        "extended_pid_database.json": {
            "pids": len(epid["pids"]),
            "source_tagged": sum(1 for p in epid["pids"] if p.get("_source_license")),
            "metadata_total_pids": epid["metadata"]["total_pids"],
        },
        "j1939_spn_fmi_database.json": {
            "spns": len(j1939["spns"]),
            "fault_matrix_rows": sum(len(v.get("fault_matrix") or {}) for v in j1939["spns"].values()),
            "sitrak_tagged_spns": sitrak_spns,
            "sitrak_fmi_rows": sitrak_rows,
            "metadata_total_spns": j1939["metadata"]["total_spns"],
        },
        "dtc_database_oem_layer.json": {
            "exists": oem_path.exists(),
            "codes": len(load_json(oem_path)["codes"]) if oem_path.exists() else 0,
            "oem_index_codes": len(load_json(oem_path)["oem_index"]) if oem_path.exists() else 0,
        },
        "canboat_pgn_reference.json": {
            "exists": pgn_path.exists(),
            "n2k_pgn_records": len(load_json(pgn_path)["pgns"]) if pgn_path.exists() else 0,
            "distinct_pgn_numbers": (len(load_json(pgn_path)["pgns_by_number"]) if pgn_path.exists() else 0),
            "j1939_pgns": len(load_json(pgn_path)["j1939"]) if pgn_path.exists() else 0,
        },
    }


# Bilinçli, GEREKÇELİ istisnalar: silinen/eklenen anahtar değil, türev üst veri
# sayacı. `extended_pid_database.json.metadata.total_pids` gerçek kayıt sayısına
# eşitlenmek ZORUNDADIR, aksi halde `scripts/data_integrity_audit.py:92`
# (`metadata_totals`, kriter `len(d["pids"])`) FAIL verir. Veri değeri değildir.
_ALLOWED_METADATA_RECOUNTS: dict[str, set[str]] = {
    "extended_pid_database.json": {"total_pids"},
    "j1939_spn_fmi_database.json": {"total_spns"},
}


def verify_no_overwrite() -> list[str]:
    """Katmanlilik denetimi: eski degerler korunmus mu (remove-only karsilastirma).

    Rapor `problems` (gercek ihlaller) ve `recounts` (izinli sayaç guncellemeleri)
    olarak ayrilir; ikincisi FAIL degildir ama GORUNUR kalir (gizlenmez).
    """
    problems: list[str] = []
    recounts: list[str] = []
    allowed = _ALLOWED_METADATA_RECOUNTS

    for path, bak in (
        (DTC_JSON, DTC_JSON.with_suffix(".json.bak_t24")),
        (EPID_JSON, EPID_JSON.with_suffix(".json.bak_t24")),
        (J1939_JSON, J1939_JSON.with_suffix(".json.bak_t24")),
    ):
        if not bak.exists():
            problems.append(f"{path.name}: .bak_t24 yok, katmanlilik denetimi yapilamaz")
            continue
        old = load_json(bak)
        new = load_json(path)
        permit = allowed.get(path.name, set())

        # B023: `walk` dongu degiskenlerini kapatmasin — degerler varsayilan
        # argumanla baglanir; her yinelemede o yinelemenin degeri kullanilir.
        name = path.name

        def walk(old_node: Any, new_node: Any, trail: str, _name: str = name, _permit: set[str] = permit) -> None:
            if isinstance(old_node, dict):
                if not isinstance(new_node, dict):
                    problems.append(f"{_name}:{trail} sozlukten baska tipe donusmus")
                    return
                for k, v in old_node.items():
                    if k not in new_node:
                        problems.append(f"{_name}:{trail}.{k} SILINMIS")
                    else:
                        walk(v, new_node[k], f"{trail}.{k}")
            elif isinstance(old_node, list):
                if not isinstance(new_node, list) or len(new_node) < len(old_node):
                    problems.append(f"{_name}:{trail} liste kuculmus")
                    return
                for i, v in enumerate(old_node):
                    walk(v, new_node[i], f"{trail}[{i}]")
            elif old_node != new_node:
                leaf = trail.rsplit(".", 1)[-1]
                if trail.startswith("metadata.") and leaf in _permit:
                    recounts.append(
                        f"{_name}:{trail} sayaci {old_node!r} -> {new_node!r} (izinli: gercek sayima esitlendi)"
                    )
                else:
                    problems.append(f"{_name}:{trail} DEGISMIS ({old_node!r} -> {new_node!r})")

        if isinstance(old, dict) and "spns" in old:  # j1939 kok nesnesi
            for key, entry in old["spns"].items():
                if key not in new["spns"]:
                    problems.append(f"{path.name}:spns.{key} SILINMIS")
                else:
                    walk(entry, new["spns"][key], f"spns.{key}")
            for k, v in old.get("metadata", {}).items():
                if new.get("metadata", {}).get(k) != v:
                    if k in permit:
                        recounts.append(f"{path.name}:metadata.{k} sayaci (izinli: gercek sayima esitlendi)")
                    else:
                        problems.append(f"{path.name}:metadata.{k} DEGISMIS")
        else:
            for key, entry in old.items():
                if key not in new:
                    problems.append(f"{path.name}:{key} SILINMIS")
                else:
                    walk(entry, new[key], key)

    for note in recounts:
        print(f"[IZINLI SAYAC] {note}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description="T2-4 layered merge of staging data into data/")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="Hesapla, yazma")
    group.add_argument("--apply", action="store_true", help="Yaz (.bak + atomik)")
    group.add_argument("--counts", action="store_true", help="Yalniz olcum")
    group.add_argument("--verify-idempotent", action="store_true", help="Katmanlilik denetimi")
    parser.add_argument("--only", default="obdex-generic,obdex-pids,dtcdb,canboat,sitrak")
    args = parser.parse_args()

    if args.counts:
        print(json.dumps(measure_counts(), ensure_ascii=False, indent=2))
        return 0

    if args.verify_idempotent:
        problems = verify_no_overwrite()
        if problems:
            print("KATMANLILIK IHLALI:")
            for p in problems[:40]:
                print(f"  - {p}")
            return 1
        print("KATMANLILIK OK: hicbir mevcut deger silinmedi/degismedi.")
        return 0

    before = measure_counts()
    merge_sitrak._before_keys = set(load_json(J1939_JSON)["spns"])  # noqa: SLF001

    wanted = {s.strip() for s in args.only.split(",") if s.strip()}
    apply = args.apply
    if "obdex-generic" in wanted or "obdex-pids" in wanted:
        merge_obdex("obdex-generic" in wanted, "obdex-pids" in wanted, apply)
    if "dtcdb" in wanted:
        merge_dtcdb(apply)
    if "canboat" in wanted:
        merge_canboat(apply)
    if "sitrak" in wanted:
        merge_sitrak(apply)

    after = measure_counts() if apply else None
    print(
        json.dumps(
            {"mode": "apply" if apply else "dry-run", "before": before, "after": after, "stats": STATS},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

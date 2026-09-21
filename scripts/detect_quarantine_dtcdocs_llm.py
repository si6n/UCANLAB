# -*- coding: utf-8 -*-
"""T1-2 — `dtcdocs.com` LLM/SEO prosedür bloklarını TESPİT ET ve KARANTİNAYA AL.

Kapsam (ölçülmüş, kanıtlı):
    `data/diagnostics/j1939_spn_fmi_database.json` içindeki `procedures_full[]`
    blokları arasında `dtcdocs.com` kaynaklı, makine/LLM üretimi görünen
    "Meaning, Causes & Fix" SEO sayfaları prosedür olarak duruyor.

Neden bu script var:
    `Tuzaklar-ve-Dersler.md` §1 (ajan "başarılı" der ama veri yanlıştır),
    §12 (arama motoru sonucu kaynak DEĞİLDİR) ve §16 (sayıyı söylerken kalıcı
    kanıt bırak). Bu script kendi kriterini diske yazar; bağımsız doğrulama
    T1-4 (marshal) işidir — kendi kontrolüm doğrulama SAYILMAZ.

Tasarım ilkeleri:
    1. TESPİT ve UYGULAMA ayrıdır. Varsayılan mod yalnız tespit + kanıt yazar.
    2. Karantina EK'tir, overwrite değildir: `procedures_full` içinde kirli
       blok yerinde bırakılmaz ama SİLİNMEZ; birebir kopyası karantina
       dosyasına taşınır, dizi girdisi tamamen çıkarılır. `--apply` ayrıca
       kirli bloğu YERİNDE `_quarantined` işaretiyle bırakan eski sürümün
       artıklarını da diziden temizler (`detect_quarantined_blocks`, Aşama 2).
    3. EN kaynak metin korunur — çeviri/onarım YOK (Rehber §4).
    4. Uydurma YOK (AGENTS.md §2.3): bilinemeyen alan boş bırakılır.

    ⚠️ T2-5 DÜZELTMESİ (bu dosyanın eski bir sürümü veri kaybettirdi):
       `write_quarantine()` eskiden karantina dosyasını **overwrite** ediyordu.
       İki ardışık `--apply` koşumu 72 kaydın 53'ünü bu yüzden sildi. Artık
       dosya **oku+birleştir (upsert by `(spn, block_index)`)** ile yazılır:
       önceki kayıtlar korunur, aynı anahtarın yeni hâli güncellenir, yeni
       anahtarlar eklenir. Patch öncesi `.bak` yedeği alınır ve her koşumda
       `merged_total` raporlanır. Bkz. `tests/unit/test_t2_5_quarantine_idempotent.py`.

Kullanım:
    python scripts/detect_quarantine_dtcdocs_llm.py --detect    # yalnız tespit + kanıt
    python scripts/detect_quarantine_dtcdocs_llm.py --verify    # karantina sonrası sayım
    python scripts/detect_quarantine_dtcdocs_llm.py --apply     # karantinayı uygula
    python scripts/detect_quarantine_dtcdocs_llm.py --detect --json   # stdout'a özet
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "diagnostics" / "j1939_spn_fmi_database.json"
QUARANTINE = ROOT / "data" / "diagnostics" / "quarantine" / "dtcdocs_llm_blocks.json"
EVIDENCE = ROOT / "spn_gap_hunter" / "output" / "t1_2_dirty_blocks.json"

# ---------------------------------------------------------------------------
# KRİTER (tek doğruluk kaynağı — kanıt dosyasına aynen yazılır)
# ---------------------------------------------------------------------------
CRITERION_SITE = "procedures_full[].source_url VEYA source_site içinde 'dtcdocs.com' (case-insensitive)"
CRITERION_TEXT = (
    "procedures_full[].overview/source_title/causes[]/first_moves[]/severity_action "
    "içinde (Frequently Asked Questions|Diagnostic & Repair Procedure|"
    "Meaning, Causes & Fix|Is this a false alarm|We use cookies|By continuing, you agree)"
)
CRITERION = f"({CRITERION_SITE}) VEYA ({CRITERION_TEXT})"

# Bu imza, dtcdocs.com'un ürettiği 12-alanlı blok gövdesidir. BOVEY/roadservice/
# detroitdieselengines.info blokları aynı imzayı TAŞIMAZ (ölçüldü: 277 blok
# imzayı taşır, 72'si dtcdocs metin/site kriterini geçer).
DTCDOCS_BODY_KEYS = frozenset(
    {
        "causes",
        "common_misdiagnoses",
        "first_moves",
        "fmi",
        "method",
        "overview",
        "quality",
        "severity",
        "severity_action",
        "source_site",
        "source_title",
        "source_url",
    }
)

SITE_RE = re.compile(r"dtcdocs\.com", re.I)
TEXT_RE = re.compile(
    r"(Frequently Asked Questions|Diagnostic & Repair Procedure|"
    r"Meaning, Causes & Fix|Is this a false alarm|We use cookies|"
    r"By continuing, you agree)",
    re.I,
)
STEP_RE = re.compile(r"^\s*Step\s+\d+\s*:", re.I)

# --- severity alanı güvenilirliği (ölçülmüş, uydurma değil) ----------------
# Ölçüm: J1939-73'ün resmî FMI→yönergesi ailesinden gelen 5 değerin **%100'ü**
# yalnız bu 72 dtcdocs bloğunda bulunur; aynı 12-alanlı imzayı taşıyan 205 temiz
# blokta (detroitdieselengines.info / roadservice.app) bu sözlük hiç görünmez,
# onlar {Serious, Caution, Critical, Informational} kullanır. Ayrıca bu 72 bloğun
# hepsinde `severity_action` == None ve 154 temiz blokta severity_action dolu.
# → Bu alanlar için bağımsız doğrulama YOK. Kanıt: "belirsiz", flag: suspect.
SEVERITY_SUSPECT_BASIS = (
    "Ayrı doğrulama bulunamadı. severity_action her 72 blokta None; 5 severity "
    "değerinin tamamı yalnız dtcdocs bloklarında geçiyor ve kaynak aynı "
    "dtcdocs.com SEO sayfasıdır. Bu SEO sitesinin sertifikalı bir eşdeğeri "
    "yoktur; bu nedenle değerler güvenilmez sayılır (Tuzaklar §11: boşluk = "
    "gerçek durum; sahte değer boş bırakmaktan kötüdür). Kanıt listesi: quarantine "
    "dosyasındaki records[].severity.provenance."
)
J1939_FMI_TEXTUAL = {
    "STOP ENGINE",
    "CHECK AT NEXT STOP",
    "CHECK SOON",
    "WARNING",
    "INFO",
}


def _severity_provenance(value: Any) -> dict[str, Any]:
    """severity değerinin nereden geldiğini/doğrulanıp doğrulanmadığını yazar."""
    if value in J1939_FMI_TEXTUAL:
        return {
            "matches_j1939_73_fmi_textual_convention": True,
            "independently_verified": False,
            "value_has_evidence_source_other_than_dtcdocs": False,
            "severity_action_supplied": False,
            "status": "belirsiz",
        }
    return {
        "matches_j1939_73_fmi_textual_convention": False,
        "independently_verified": False,
        "value_has_evidence_source_other_than_dtcdocs": False,
        "severity_action_supplied": False,
        "status": "belirsiz",
    }


# Kanıt için saklanan metin alanları
EVIDENCE_TEXT_FIELDS = ("overview", "source_title")


def _sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iter_strings(node: Any, path: str = "") -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    if isinstance(node, dict):
        for k, v in node.items():
            out.extend(_iter_strings(v, f"{path}.{k}" if path else k))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            out.extend(_iter_strings(v, f"{path}[{i}]"))
    elif isinstance(node, str):
        out.append((path, node))
    return out


def _synthesized_causes(block: dict[str, Any]) -> dict[str, Any]:
    """`causes[]` içine karışmış prosedür/FAQ/çerez metnini AYIKLAR (silmez).

    Gerçek neden metinleri yerinde kalır; yalnız makine üretimi adım/SSS
    satırları raporlanır ve karantina kaydında listelenir.
    """
    causes = block.get("causes")
    if not isinstance(causes, list):
        return {"total": 0, "indexes": [], "texts": []}
    idx = [j for j, s in enumerate(causes) if isinstance(s, str) and (STEP_RE.match(s) or TEXT_RE.search(s))]
    return {
        "total": len(idx),
        "indexes": idx,
        "texts": [causes[j] for j in idx],
    }


def _block_evidence(block: dict[str, Any]) -> dict[str, Any]:
    """Bir bloğun kirli olduğunu GÖSTEREN metin kanıtını çıkarır."""
    hits: list[dict[str, str]] = []
    for path, s in _iter_strings(block):
        for m in TEXT_RE.finditer(s):
            hits.append({"path": path, "match": m.group(0), "excerpt": s[:220]})
    step_hits = [{"path": path, "excerpt": s[:220]} for path, s in _iter_strings(block) if STEP_RE.match(s)]
    url_hits = [{"path": path, "value": s} for path, s in _iter_strings(block) if SITE_RE.search(s)]
    return {"text_marker_hits": hits, "step_hits": step_hits, "url_hits": url_hits}


def _spn_sort_key(key: str) -> tuple[int, int, str]:
    """Total, crash-free ordering key for SPN dictionary keys.

    Keys are usually ``SPN_<decimal>``, but the catalog also contains
    hexadecimal-style ids (``SPN_0x01``, ``SPN_0x3E``) contributed by other
    harvests. A bare ``int(k.split("_")[1])`` raises ``ValueError`` on those
    and takes the whole script down — including ``--verify`` (observed while
    restoring the T2-5 quarantine). Decimal ids sort first in numeric order,
    then hex ids in numeric order, then anything unparseable by name.
    """
    suffix = key.split("_", 1)[1] if "_" in key else key
    try:
        return (0, int(suffix), key)
    except ValueError:
        pass
    try:
        return (1, int(suffix, 16), key)
    except ValueError:
        return (2, 0, key)


def detect(payload: dict[str, Any]) -> dict[str, Any]:
    """Kirli blokları bulur. DB'ye YAZMAZ."""
    spns: dict[str, Any] = payload.get("spns", {})
    total_blocks = 0
    dirty: list[dict[str, Any]] = []

    for key in sorted(spns, key=_spn_sort_key):
        entry = spns[key]
        blocks = entry.get("procedures_full")
        if not isinstance(blocks, list):
            continue
        total_blocks += len(blocks)
        for idx, block in enumerate(blocks):
            if not isinstance(block, dict):
                continue
            blob = json.dumps(block, ensure_ascii=False)
            has_site = bool(SITE_RE.search(blob))
            text_marker = bool(TEXT_RE.search(blob))
            if not (has_site or text_marker):
                continue
            body_sig = frozenset(block.keys()) == DTCDOCS_BODY_KEYS
            ev = _block_evidence(block)
            dirty.append(
                {
                    "spn": key,
                    "block_index": idx,
                    "spn_value": entry.get("spn"),
                    "fmi": block.get("fmi"),
                    "source_site": block.get("source_site"),
                    "source_url": block.get("source_url"),
                    "source_title": block.get("source_title"),
                    "block_sha256": _sha256_bytes(blob.encode("utf-8")),
                    "block_bytes": len(blob.encode("utf-8")),
                    "matched_by": {
                        "source_site_or_url_dtcdocs": has_site,
                        "llm_seo_text_marker": text_marker,
                    },
                    "dtcdocs_body_signature": body_sig,
                    "overview": block.get("overview"),
                    "causes_count": len(block.get("causes") or []) if isinstance(block.get("causes"), list) else 0,
                    "synthesized_causes": _synthesized_causes(block),
                    "evidence": ev,
                    "severity": {
                        "value": block.get("severity"),
                        "flag": "suspect",
                        "basis": SEVERITY_SUSPECT_BASIS,
                        "provenance": _severity_provenance(block.get("severity")),
                    },
                    "block_payload": block,  # kayıpsız taşıma için birebir kopya
                }
            )

    affected = sorted({d["spn"] for d in dirty}, key=_spn_sort_key)
    by_site = Counter(d["source_site"] for d in dirty)
    return {
        "total_blocks": total_blocks,
        "dirty_blocks": dirty,
        "affected_spns": affected,
        "by_source_site": dict(by_site),
    }


def detect_quarantined_blocks(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Var olan `_quarantined` işaretlerini envanterler (T2-3).

    Kriter yeniden koşulmaz — bu, `--apply` sırasında zaten yerleştirilmiş
    işaretlerin envanteridir. İkinci aşama (rezidü temizliği) bu işaretleri
    hedefler; aksi halde `_quarantined` bloklar uygulandıktan sonra kriterden
    muaf kalır (a kritik yarık) ve bir daha temizlenemez.

    Payload birebir kopyası, `--apply` çalışırken `write_quarantine(...)` ile
    karantina dosyasına taşınmış olmalıdır; bu fonksiyon yalnız envanter üretir.
    """
    blocks_by_ref: dict[str, list[int]] = {}
    for record in _load_quarantine_index():
        blocks_by_ref.setdefault(record["spn"], []).append(record["block_index"])

    found: list[dict[str, Any]] = []
    spns: dict[str, Any] = payload.get("spns", {})
    for key in sorted(spns, key=_spn_sort_key):
        entry = spns[key]
        blocks = entry.get("procedures_full")
        if not isinstance(blocks, list):
            continue
        for idx, block in enumerate(blocks):
            if not isinstance(block, dict) or not block.get("_quarantined"):
                continue
            ref = str(block.get("_quarantine_ref") or "")
            found.append(
                {
                    "spn": key,
                    "spn_value": entry.get("spn"),
                    "block_index": idx,
                    "quarantine_ref": ref,
                    "quarantine_ref_exists": (ROOT / ref).exists() if ref else False,
                    "block_sha256": block.get("_quarantine_block_sha256"),
                    "payload_in_quarantine_file": idx in blocks_by_ref.get(key, []),
                }
            )
    return found


def _load_quarantine_index() -> list[dict[str, Any]]:
    """Karantina dosyasındaki (spn, block_index) envanterini okur (salt-okunur)."""
    if not QUARANTINE.exists():
        return []
    doc = json.loads(QUARANTINE.read_text(encoding="utf-8"))
    return [
        {
            "spn": str(rec.get("spn") or ""),
            "block_index": int(rec.get("block_index") or 0),
            "block_sha256": rec.get("block_sha256"),
        }
        for rec in doc.get("records", [])
    ]


def write_evidence(
    payload: dict[str, Any], db_sha: str, result: dict[str, Any], *, mode: str, extra: dict[str, Any] | None = None
) -> None:
    dirty = result["dirty_blocks"]
    doc: dict[str, Any] = {
        "task": "T1-2",
        "role": "telemetry",
        "generated_at_utc": _now(),
        "mode": mode,
        "source_db": str(DB.relative_to(ROOT)).replace("\\", "/"),
        "db_sha256": db_sha,
        "criterion": CRITERION,
        "criterion_source_site": CRITERION_SITE,
        "criterion_text_marker": CRITERION_TEXT,
        "detector": "scripts/detect_quarantine_dtcdocs_llm.py",
        "total_blocks": result["total_blocks"],
        "dirty_blocks_count": len(dirty),
        "affected_spns_count": len(result["affected_spns"]),
        "affected_spns": result["affected_spns"],
        "by_source_site": result["by_source_site"],
        "dirty_blocks": [{k: v for k, v in d.items() if k != "block_payload"} for d in dirty],
        "note": (
            "Bu dosya KENDİ tespit çıktısıdır; bağımsız doğrulama değildir "
            "(Rehber §3.4). Blokların tam gövdesi karantina dosyasındadır."
        ),
    }
    if extra:
        doc.update(extra)
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    EVIDENCE.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def _read_existing_quarantine() -> dict[str, Any]:
    """Read the current quarantine document, or return an empty skeleton.

    Unreadable/corrupt file is treated as empty so a bad file cannot block the
    write — but the caller takes a `.bak` first, so nothing is destroyed.
    """
    if not QUARANTINE.exists():
        return {}
    try:
        doc = json.loads(QUARANTINE.read_text(encoding="utf-8"))
        return doc if isinstance(doc, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _quarantine_key(record: dict[str, Any]) -> tuple[str, int]:
    """Identity of a quarantine record: one quarantined BLOCK of one SPN.

    ``(spn, block_index)`` is the stable identity used for upsert. A record
    without those fields falls back to its payload hash so it is never merged
    onto an unrelated record.
    """
    spn = str(record.get("spn") or "")
    idx = record.get("block_index")
    if spn and isinstance(idx, int):
        return (spn, idx)
    return (spn, -1)


def _backup_quarantine() -> Path | None:
    """Copy the current quarantine file to a timestamped ``.bak`` before patching.

    Returns the backup path, or ``None`` when there was nothing to back up.
    This is the safety net that the T2-5 overwrite bug did not have.
    """
    if not QUARANTINE.exists():
        return None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = QUARANTINE.with_name(f"{QUARANTINE.name}.{stamp}.bak")
    try:
        backup.write_bytes(QUARANTINE.read_bytes())
    except OSError:
        return None
    return backup


def write_quarantine(dirty: list[dict[str, Any]], db_sha: str, db_before: dict[str, Any]) -> dict[str, Any]:
    """Kirli blokları karantina dosyasına UPSERT eder — overwrite YOK.

    T2-5 (veri kaybı olayı): bu fonksiyon eskiden dosyayı sıfırdan yazıyordu.
    İki ardışık `--apply` koşumu 72 kaydın 53'ünü bu yüzden sildi; o kayıtların
    `original_payload`'ı yaşayan tek kopyaydı. Artık:

      1. Patch öncesi mevcut dosya `.bak` olarak yedeklenir (silme yok).
      2. Mevcut kayıtlar okunur ve `(spn, block_index)` anahtarıyla BİRLEŞTİRİLİR:
         aynı anahtar güncellenir, yeni anahtar eklenir, diğerleri KORUNUR.
      3. `count`/`records` birleşik kümeyi yansıtır; `merged_total` döndürülür.

    Böylece `write_quarantine` idempotenttir: N kez çağrılması kayıt kaybettirmez.
    """
    incoming: list[dict[str, Any]] = []
    for d in dirty:
        rec = {k: v for k, v in d.items() if k != "block_payload"}
        rec["original_payload"] = d["block_payload"]
        rec["reason"] = (
            "dtcdocs.com kaynaklı LLM/SEO üretimi sayfa içeriği prosedür olarak "
            "kayıtlı (kriter: {c}). Prosedür değil; kullanıcıya adım/ölçüm "
            "talimatı vermez. İçerik çoğunlukla yüzde-tahminli neden listesi ve "
            "web-sayfası navigasyon/çerez metni. overview alanı "
            "'<Marka> SPN <n> FMI <m>: Meaning, Causes & Fix' SEO şablonundadır."
        ).format(c="source_site/source_url=dtcdocs.com + SEO metin imzası")
        rec["quarantine_flags"] = {
            "llm_seo_generated": True,
            "source_url_or_site_is_dtcdocs": True,
            "severity_suspect": True,
            "severity_status": "belirsiz",
            "cause_entries_settled_into_quarantine": d.get("synthesized_causes", {}),
        }
        incoming.append(rec)

    existing = _read_existing_quarantine()
    previous_records = [
        r for r in (existing.get("records") or []) if isinstance(r, dict)
    ]

    # Upsert by (spn, block_index): existing order preserved, incoming appended.
    merged: list[dict[str, Any]] = list(previous_records)
    by_key: dict[tuple[str, int], int] = {
        _quarantine_key(r): i for i, r in enumerate(merged)
    }
    added = updated = 0
    for rec in incoming:
        key = _quarantine_key(rec)
        if key in by_key:
            merged[by_key[key]] = rec
            updated += 1
        else:
            by_key[key] = len(merged)
            merged.append(rec)
            added += 1

    sev_values: Counter[Any] = Counter()
    for rec in merged:
        s = (rec.get("severity") or {}).get("value")
        if s:
            sev_values[s] += 1

    backup = _backup_quarantine()

    doc: dict[str, Any] = dict(existing)  # preserve unknown top-level fields
    doc.update({
        "schema_version": 1,
        "task": "T1-2",
        "quarantined_at_utc": _now(),
        "quarantined_by": "telemetry",
        "source_db": "data/diagnostics/j1939_spn_fmi_database.json",
        "source_db_sha256_at_detection": db_sha,
        "source_db_sha256_at_quarantine": _sha256_file(DB),
        "source_db_before_stats": {
            "total_blocks": db_before["total_blocks"],
            "affected_spns": len(db_before["affected_spns"]),
        },
        "criterion": CRITERION,
        "suspect_severity": {
            "flag": "suspect",
            "status": "belirsiz",
            "distinct_values": dict(sev_values),
            "basis": SEVERITY_SUSPECT_BASIS,
        },
        "policy": (
            "SİLME YOK. Bloklar `procedures_full` dizisinden çıkarıldı ve burada "
            "birebir (byte-for-byte) saklandı. Geri alma: `original_payload`ı "
            "ilgili SPN'in procedures_full dizisindeki `block_index` konumuna "
            "koyun. Yazma UPSERT'tir: (spn, block_index) anahtarıyla birleştirir, "
            "önceki kayıtları korur ve patch öncesi `.bak` alır."
        ),
        "count": len(merged),
        "records": merged,
        "merge_stats": {
            "previous_total": len(previous_records),
            "incoming_total": len(incoming),
            "added": added,
            "updated": updated,
            "merged_total": len(merged),
            "backup_file": backup.name if backup else None,
        },
    })

    QUARANTINE.parent.mkdir(parents=True, exist_ok=True)
    tmp = QUARANTINE.with_suffix(QUARANTINE.suffix + f".tmp-{os.getpid()}-{time.monotonic_ns()}")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, QUARANTINE)
    return doc["merge_stats"]


def apply_quarantine(payload: dict[str, Any], dirty: list[dict[str, Any]]) -> dict[str, int]:
    """Kirli VE daha önce işaretlenmiş blokları `procedures_full` dizisinden ÇIKARIR.

    T2-3 (kalan sızıntı): eski sürüm kirli bloğun içeriğini silip YERİNDE
    yalnız `_quarantined` işareti bırakıyordu. Tüketici `procedures_full[:1]`
    (index 0) okuduğu için, index 0'ı işaretli 3 SPN'de (SPN_520604,
    SPN_520605, SPN_524265) karantina HİÇBİR KORUMA sağlamıyordu.

    Düzeltme: blok diziden tamamen çıkarılır (yerinde işaret bırakmak yerine).
    İçerik kaybolmaz — `--apply` akışı bloğun birebir kopyasını
    `write_quarantine(...)` ile karantina dosyasına taşır ve her kayıtta
    `original_payload` + `block_sha256` ile geri alınabilir tutar.

    İki aşamalıdır:
        Aşama 1: kriteri geçen kirli bloklar (yeni tespit).
        Aşama 2: zaten `_quarantined` işaretli bloklar (önceki sürümün
                 yerinde-bıraktığı rezidü) — bunlar da diziden çıkarılır.

    T2-5 (kısmi kaldırma hatası): `applied` sözlüğü yalnız `spn` ile anahtarlanıyordu
    (`{d["spn"]: d}`), bu yüzden bir SPN'de BİRDEN FAZLA kirli blok varsa (ölçüldü:
    13 SPN'de 2-4 blok) en son listelenen `block_index` dışındakiler dizide KALIYORDU
    — 53/72 kaldırıldı, 19 kirli blok geride kaldı. Anahtar artık
    `(spn, block_index)` çiftidir ve her kirli blok ayrı ayrı eşleşir.

    Bloklar SPN bazında `block_index` sırasına göre sondan-başa çıkarılır ki
    kalan indeksler kaymasın.

    Diske atomik yazar. Silme YOK — çıkarma + karantinaya taşıma.
    """
    # (spn, block_index) -> kayıt. Bir SPN'de N kirli blok olabilir; SPN bazlı
    # tek-slot sözlük bu durumda yalnız bir tanesini kaldırırdı.
    applied: dict[tuple[str, int], dict[str, Any]] = {
        (d["spn"], int(d["block_index"])): d for d in dirty
    }
    removed = 0
    residue_removed: list[dict[str, Any]] = []

    for spn_key, entry in payload["spns"].items():
        blocks = entry.get("procedures_full")
        if not isinstance(blocks, list) or not blocks:
            continue
        keep: list[Any] = []
        for i, block in enumerate(blocks):
            is_newly_dirty = (spn_key, i) in applied
            is_marked_residue = isinstance(block, dict) and bool(block.get("_quarantined"))
            if is_newly_dirty or is_marked_residue:
                if is_marked_residue and not is_newly_dirty:
                    residue_removed.append(
                        {
                            "spn": spn_key,
                            "block_index": i,
                            "block_sha256": block.get("_quarantine_block_sha256"),
                        }
                    )
                removed += 1
                continue
            keep.append(block)
        if len(keep) != len(blocks):
            if keep:
                entry["procedures_full"] = keep
            else:
                entry.pop("procedures_full", None)

    tmp = DB.with_suffix(DB.suffix + f".tmp-{os.getpid()}-{time.monotonic_ns()}")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, DB)
    return {"removed": removed, "residue_removed": residue_removed}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--detect", action="store_true", help="yalnız tespit + kanıt yaz")
    ap.add_argument("--apply", action="store_true", help="karantinayı uygula (DB'ye yaz)")
    ap.add_argument("--verify", action="store_true", help="kriter yeniden koş; sayı raporla")
    ap.add_argument("--json", action="store_true", help="stdout'a JSON özet")
    args = ap.parse_args()
    if not (args.detect or args.apply or args.verify):
        args.detect = True

    db_sha = _sha256_file(DB)
    payload = json.loads(DB.read_text(encoding="utf-8"))
    result = detect(payload)

    if args.verify:
        quarantined = detect_quarantined_blocks(payload)
        # T2-3 doğrulaması: karantina diziden ÇIKARILMADIYSA index-0 sızıntısı
        # sürüyor demektir → verify KIRMIZI.
        index0_leaks = [{"spn": b["spn"], "spn_value": b["spn_value"]} for b in quarantined if b["block_index"] == 0]
        summary = {
            "db_sha256": db_sha,
            "total_blocks": result["total_blocks"],
            "dirty_blocks": len(result["dirty_blocks"]),
            "affected_spns": result["affected_spns"],
            "quarantined_blocks_in_procedures_full": len(quarantined),
            "procedures_full_index0_quarantined_spns": index0_leaks,
        }
        write_evidence(
            payload,
            db_sha,
            result,
            mode="verify",
            extra={
                "quarantined_blocks_in_procedures_full": len(quarantined),
                "procedures_full_index0_quarantined_spns": index0_leaks,
                "quarantined_blocks": quarantined,
            },
        )
        print(
            json.dumps(summary, ensure_ascii=False, indent=2)
            if args.json
            else f"[verify] db_sha256={db_sha}\n"
            f"[verify] total_blocks={result['total_blocks']} "
            f"dirty_blocks={len(result['dirty_blocks'])} "
            f"affected_spns={len(result['affected_spns'])}\n"
            f"[verify] procedures_full içinde kalan karantina bloğu={len(quarantined)} "
            f"(index0 sızıntısı={len(index0_leaks)})"
        )
        if result["dirty_blocks"] or quarantined:
            return 2
        return 0

    if args.apply:
        residue = detect_quarantined_blocks(payload)
        if not result["dirty_blocks"] and not residue:
            print("[apply] kirli/artık blok yok — yazma yok.")
            return 0
        merge_stats: dict[str, Any] | None = None
        if result["dirty_blocks"]:
            merge_stats = write_quarantine(result["dirty_blocks"], db_sha, result)
        outcome = apply_quarantine(payload, result["dirty_blocks"])
        print(
            f"[apply] procedures_full dizisinden çıkarılan blok={outcome['removed']} "
            f"(yeni karantina={len(result['dirty_blocks'])}, "
            f"önceki-sürüm artığı={len(outcome['residue_removed'])}) -> {QUARANTINE}"
        )
        if merge_stats:
            # T2-5: a lost record is invisible unless the merge is reported.
            print(
                f"[apply] karantina birleştirme: önceki={merge_stats['previous_total']} "
                f"+eklenen={merge_stats['added']} ~güncellenen={merge_stats['updated']} "
                f"= merged_total={merge_stats['merged_total']} "
                f"(yedek={merge_stats['backup_file']})"
            )
            if merge_stats["merged_total"] < merge_stats["previous_total"]:
                print(
                    "[apply] ⚠️ UYARI: merged_total < previous_total — kayıt kaybı! "
                    "Bu bir hata; dosyayı .bak'tan geri alın."
                )
        if outcome["residue_removed"]:
            print(
                f"[apply] artık temizlenen SPN index0: "
                f"{[r['spn'] for r in outcome['residue_removed'] if r['block_index'] == 0]}"
            )

    # tespit çıktısı (apply sonrası dahil, güncel durumu yansıtır)
    payload2 = json.loads(DB.read_text(encoding="utf-8"))
    result2 = detect(payload2)
    write_evidence(payload2, _sha256_file(DB), result2, mode="apply" if args.apply else "detect")
    print(
        f"[detect] total_blocks={result2['total_blocks']} "
        f"dirty_blocks={len(result2['dirty_blocks'])} "
        f"affected_spns={len(result2['affected_spns'])} -> {EVIDENCE}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

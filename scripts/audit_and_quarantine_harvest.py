"""Audit and Quarantine Scraped Diagnostic Data.
Runs strict schema, sanitization, and safety audits on raw harvested files.
Separates verified records from quarantined ones without modifying production DB.
"""
import json
import sys
from pathlib import Path
from datetime import datetime

MAIN_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(MAIN_DIR))

from src.engine.ai.harvest_validator import QuarantineGatekeeper

RAW_FILE = MAIN_DIR / "spn_gap_hunter" / "output" / "scout_harvest_2026-09-17.json"
VERIFIED_DIR = MAIN_DIR / "spn_gap_hunter" / "output" / "verified"
QUARANTINE_DIR = MAIN_DIR / "spn_gap_hunter" / "output" / "quarantine"
VAULT_REPORT = MAIN_DIR / "obsidian-vault" / "04-Ajan-Notlari" / "Marshal-Karantina-Denetim-Raporu.md"

def run_audit():
    if not RAW_FILE.exists():
        print(f"Hata: {RAW_FILE} bulunamadi.")
        return

    with open(RAW_FILE, "r", encoding="utf-8") as f:
        raw_data = json.load(f)

    dtc_records = raw_data.get("dtc_records", [])
    spn_records = []
    # Flatten SPN items if nested
    for s_entry in raw_data.get("spn_records", []):
        for item in s_entry.get("items", []):
            spn_records.append(item)

    print(f"Denetlenen girdi: {len(dtc_records)} DTC kaydı, {len(spn_records)} SPN/FMI kaydı.")
    audit_results = QuarantineGatekeeper.audit_batch(dtc_records, spn_records)
    summary = audit_results["summary"]

    now = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    VERIFIED_DIR.mkdir(parents=True, exist_ok=True)
    QUARANTINE_DIR.mkdir(parents=True, exist_ok=True)

    verified_file = VERIFIED_DIR / f"verified_harvest_{now}.json"
    quarantine_file = QUARANTINE_DIR / f"quarantined_harvest_{now}.json"

    with open(verified_file, "w", encoding="utf-8") as f:
        json.dump({
            "audit_timestamp": now,
            "audited_by": "marshal_gatekeeper",
            "approved_dtcs": audit_results["approved_dtcs"],
            "approved_spns": audit_results["approved_spns"]
        }, f, ensure_ascii=False, indent=2)

    with open(quarantine_file, "w", encoding="utf-8") as f:
        json.dump({
            "audit_timestamp": now,
            "audited_by": "marshal_gatekeeper",
            "quarantined_dtcs": audit_results["quarantined_dtcs"],
            "quarantined_spns": audit_results["quarantined_spns"]
        }, f, ensure_ascii=False, indent=2)

    # Write Obsidian Report
    md = f"""---
title: Marshal Karantina ve Guvenlik Denetim Raporu
category: guvenlik
tags: [marshal, asil-b, karantina, denetim, dtc, j1939]
created: {datetime.now().strftime('%Y-%m-%d')}
status: tamamlandi
---

# 🛡️ Marshal Teşhis Veri Karantina ve Güvenlik Denetim Raporu

**Tarih:** {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}  
**Denetçi:** `marshal` (ASIL-B/D Güvenlik Kapısı)  
**Kural:** Dış kaynak verilerine asla doğrudan güvenilmez. Sıfır uydurma veri, katı şema ve sanitizasyon denetimi.

## 1. Denetim İstatistikleri
| Veri Türü | Girdi Adedi | Onaylanan (Verified) | Karantinaya Alınan (Red) | Kabul Oranı |
|---|---|---|---|---|
| **DTC (OBD-II)** | {summary['dtc_in']} | {summary['dtc_ok']} | {summary['dtc_rej']} | %{(summary['dtc_ok']/summary['dtc_in']*100) if summary['dtc_in'] else 0:.1f} |
| **SPN/FMI (J1939)** | {summary['spn_in']} | {summary['spn_ok']} | {summary['spn_rej']} | %{(summary['spn_ok']/summary['spn_in']*100) if summary['spn_in'] else 0:.1f} |

## 2. Depolanan İzolasyon Dosyaları
- **Onaylanan Temiz Veri:** `{verified_file}`
- **Karantinaya Alınan (Hatalı/Çöp) Veri:** `{quarantine_file}`

## 3. Karantinaya Alınma Gerekçeleri
- Boş veya gürültülü (404, bot engeli, çok kısa metin) içerikler ayıklandı.
- SAE J2012 formatına uymayan geçersiz kodlar ve sınır dışı SPN/FMI değerleri doğrudan reddedildi.
- Üretim veritabanlarına (`dtc_database.json` / `j1939_spn_fmi_database.json`) doğrudan yazım engellendi.
"""
    VAULT_REPORT.parent.mkdir(parents=True, exist_ok=True)
    VAULT_REPORT.write_text(md, encoding="utf-8")
    print(f"[OK] Karantina denetimi tamamlandi.")
    print(f"  -> Onaylanan: {summary['dtc_ok']} DTC, {summary['spn_ok']} SPN")
    print(f"  -> Karantina: {summary['dtc_rej']} DTC, {summary['spn_rej']} SPN")
    print(f"  -> Rapor: {VAULT_REPORT}")

if __name__ == "__main__":
    run_audit()

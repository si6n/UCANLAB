#!/usr/bin/env python3
"""UCANLAB surekli tarama hatti — pipeline tick.

Cron ile her 30 dakikada bir calisir. LLM KULLANMAZ (--no-agent).
Kanban gorevlerini olusturur; gateway onlari ajanlara dagitir.

Akis:
  KESIF ajani (scout)  -> yeni kaynak bulur, scan_queue.json'a ekler
  TARAMA ajanlari      -> kuyruktan pending kaynak alir, ham cikti uretir
  MERGE gorevi         -> ham ciktiyi DB'ye uygular (TEK yazar)

Guvenlik:
  - Ayni turde ikinci gorev acmaz (aktif gorev kontrolu)
  - KESIF icin zaman esigi (varsayilan 3 saat)
  - Hicbir sey yapmazsa stdout BOS -> cron sessiz kalir
"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

MAIN = Path(r"C:\Users\canak\Desktop\Universal-CAN-BUS-Tool")
HERMES = r"C:\Users\canak\AppData\Local\hermes\hermes-agent\venv\Scripts\hermes.exe"
BOARD = "ucanlab"

QUEUE = MAIN / "scripts" / "scan_queue.json"
STATE = MAIN / "scripts" / ".pipeline_state.json"
OUTDIR = MAIN / "spn_gap_hunter" / "output"

DISCOVERY_INTERVAL_H = 0.5    # KESIF en sik bu kadar arayla (30dk — cron tick'i)
SCAN_BATCH = 5                # bir TARAMA gorevi kac kaynak alsin (kesife yetissin)
BACKLOG_LIMIT = 40            # kuyrukta bundan fazla pending varsa kesif DURUR
ACTIVE = ("todo", "ready", "running", "scheduled", "blocked")

ORTAK = """
=== ORTAK KURALLAR ===
- DEPO: C:/Users/canak/Desktop/Universal-CAN-BUS-Tool
- TEST: py -3.13 -m pytest (hermes venv'inde pytest YOK), QT_QPA_PLATFORM=offscreen
- KUYRUK: scripts/scan_queue.json  (once OKU, sonra guncelle)
- DEFTER: scripts/scrape_registry.json (ayni URL'yi ikinci kez taramayi onler)
- KARA LISTE: scan_queue.json -> blacklist (DOKUNMA)
- UYDURMA YASAK: bulamazsan "VERI YOK" / "BELIRSIZ" yaz
- "Bulundu" != "Yeni": kaynakta olan ile DB'ye eklenen AYRI raporlanir
- COMMIT ET (zorunlu) — commit edilmeyen is kaybolur
- PROTOKOL: obsidian-vault/03-Kaynaklar/Tarama-Hatti.md
"""

KESIF_BODY = ORTAK + """
GOREV (KESIF): Yeni TARAMA KAYNAGI bul. Tarama YAPMA, DB'ye YAZMA.

SEN SADECE KESIF YAPARSIN. Amacin: taranacak YENI kaynak bulup kuyruga eklemek.

1) scripts/scan_queue.json OKU:
   - mevcut domain'ler (TEKRAR EKLEME)
   - blacklist (DOKUNMA)
2) YENI kaynak ara (0 BrightData kredisi):
   - Web: arama motoru + sitemap.xml + robots.txt
   - GitHub: api.github.com/search/repositories?q=j1939+OR+dtc+database
   - HuggingFace datasets, acik veri portallari
   - OEM servis portallari (Cummins, PACCAR, Volvo/Mack, Detroit, Navistar)
3) HER ADAY ICIN degerlendir:
   - URL + sitemap URL
   - KAC SPN/DTC iceriyor (sitemap'ten SAY — tahmin etme!)
   - Erisim: free / login duvari / JS-render
   - Lisans (CC BY / MIT / public domain / belirsiz)
   - 2 ornek sayfa cek -> icerik GERCEK mi (yer tutucu degil)
4) KALITE FILTRESI:
   - "SPN xxx nedir" genel metin -> DUSUK (kalite=low, yine de kaydet)
   - KODA OZEL semptom+neden+adim -> YUKSEK (kalite=high)
5) scan_queue.json'a EKLE:
   {id, url, domain, type, method, status:"pending", quality, est_items, license, notes}
   Ayni domain zaten varsa EKLEME.
6) vault/03-Kaynaklar/Kaynak-Kuyrugu.md guncelle (insan-okunur liste)
7) spn_gap_hunter/output/kesif_<tarih>.json yaz (bulunan kaynaklar + kanit)
8) Commit: chore(scan): KESIF <tarih> <N> yeni kaynak

CIKTI: bulunan kaynak sayisi, her biri icin 1 satir ozet.
"""

TARAMA_BODY = ORTAK + """
GOREV (TARAMA): Kuyruktan PENDING kaynak tara. DB'ye YAZMA.

1) scripts/scan_queue.json OKU -> status="pending" ilk %d kaynagi al
2) Her kaynak icin status="scanning" yap ve kaydet (KILIT — baska ajan almasin)
3) Tara:
   - method=free -> urllib/requests (UA: Mozilla/5.0)
   - method=brightdata -> BrightData browser (krediyi say!)
   - sitemap'ten TUM sayfalari listele, her sayfayi cek
   - SPN/FMI/DTC + semptom/neden/adim cikar
   - YER TUTUCU metinleri isaretle (placeholder=true) — bunlar merge'de ATLANIR
4) Ham cikti yaz: spn_gap_hunter/output/scan_<domain>_<tarih>.json
   Her kayit: {url, status, method, credits, spn, fmi, code, field, text,
              placeholder, evidence_url}
5) scan_queue.json guncelle: status="done", credits_used, artifact yolu
6) scripts/scrape_registry.json'a kaydet (ayni URL tekrar taranmasin)
7) vault/03-Kaynaklar/Tarama-Kayit-Defteri.md guncelle
8) Commit: feat(scan): TARAMA <domain> <N> kayit (<M> gercek)

=== BD KURALI (pitboss olctu — UYGULA) ===
- HER DOMAIN ICIN AYRI connect_over_cdp() — ayni oturumda 2. domain 'domain limit
  reached' verir. Kanit: ayri oturumla DDG 3.4s, bigrigfaults 8.8s.
- Istekler arasi 2-3s bekle; 502 (no_peer) -> 1 retry.
- Google yerine html.duckduckgo.com/html/?q= kullan (0 kredi, captcha yok).
- Google zorunluysa URL'e &gl=us&hl=en ekle (zone cikisi VN).
- 502 hatalari kredi SAYMAZ; yalniz basarili icerik cekimi kredi.
- Teshis: spn_gap_hunter/output/brightdata_durum.json

CAP: 40 BrightData kredisi (asarsan DUR ve raporla)
ONEMLI: Kac sayfa tarandi, kaci gercek, kaci yer tutucu — KESIN sayilarla.
""" % SCAN_BATCH

MERGE_BODY = ORTAK + """
GOREV (MERGE): Tarama ciktilarini DB'ye uygula. TEK YAZAR.

1) spn_gap_hunter/output/scan_*.json + t55b/t55c kalintilarini tara
2) scan_queue.json'da applied=false olan ciktilara bak
3) Her kayit icin:
   - Yeni SPN/DTC -> EKLE. ZORUNLU: name + title_tr (TAM Turkce ceviri)
   - Bos alan -> DOLDUR (placeholder=true olanlari ATLA)
   - Cakisma -> mevcut veriyi KORU, yenisini at
4) DOSYA FORMATI: json.dumps(db, ensure_ascii=False, indent=1) + "\n"
   (indent=2 KULLANMA -> 500K satirlik SAHTE diff olur; T54 dersi)
5) Test sabitlerini guncelle:
   tests/unit/test_t48_t44_recovery.py, test_j1939_v190_load.py,
   test_benchmark_ai_copilot.py, tests/safety/test_e2e_safety_audit.py
6) applied=true isaretle (scan_queue.json)
7) KABUL: py -3.13 -m pytest -q TAM YESIL + ruff temiz + title_tr eksik 0
8) Commit: fix(diagnostics): MERGE <kaynak> +<N> SPN +<M> alan

KANIT: kac SPN eklendi, kac alan doldu, test sayisi (once/sonra).
"""


def hermes(*args: str) -> str:
    r = subprocess.run([HERMES, "kanban", "--board", BOARD, *args],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=120)
    return (r.stdout or "") + (r.stderr or "")


def tasks() -> list[dict]:
    try:
        return json.loads(hermes("ls", "--json"))
    except Exception:
        return []


def active_with(prefix: str) -> bool:
    for t in tasks():
        if t.get("status") in ACTIVE and str(t.get("title", "")).startswith(prefix):
            return True
    return False


def create(title: str, body: str, assignee: str) -> str:
    out = hermes("create", title, "--body", body, "--assignee", assignee,
                 "--workspace", f"dir:{MAIN}", "--json")
    for tok in out.split('"'):
        if tok.startswith("t_") and len(tok) == 10:
            return tok
    return "?"


def load_json(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def main() -> int:
    if "--force" in sys.argv:
        pass
    q = load_json(QUEUE, None)
    if q is None:
        print("KUYRUK YOK: scripts/scan_queue.json")
        return 1

    st = load_json(STATE, {})
    now = datetime.now()
    made = []

    # --- 1) KESIF ---
    pending_now = [s for s in q.get("sources", []) if s.get("status") == "pending"]
    last = st.get("last_discovery")
    due = True
    if last:
        try:
            due = now - datetime.fromisoformat(last) > timedelta(hours=DISCOVERY_INTERVAL_H)
        except Exception:
            due = True
    # Backlog korumasi: kuyruk zaten doluysa kesif DURUR (tarama yetissin)
    if len(pending_now) >= BACKLOG_LIMIT:
        due = False
    if due and not active_with("KESIF"):
        tid = create(f"KESIF: yeni tarama kaynagi ara ({now:%Y-%m-%d %H:%M})",
                     KESIF_BODY, "scout")
        st["last_discovery"] = now.isoformat()
        made.append(f"KESIF={tid}")

    # --- 2) TARAMA ---
    pending = [s for s in q.get("sources", []) if s.get("status") == "pending"]
    if pending and not active_with("TARAMA"):
        names = ", ".join(s["domain"] for s in pending[:SCAN_BATCH])
        tid = create(f"TARAMA: {names[:60]}",
                     TARAMA_BODY + "\n\nSECILEN KAYNAKLAR:\n" +
                     "\n".join(f"- {s['domain']} ({s.get('url')}) [{s.get('method')}]"
                               for s in pending[:SCAN_BATCH]),
                     "chassis")
        made.append(f"TARAMA={tid}")

    # --- 3) MERGE ---
    unapplied = []
    for s in q.get("sources", []):
        if s.get("status") == "done" and s.get("artifact") and not s.get("applied"):
            unapplied.append(s)
    if unapplied and not active_with("MERGE"):
        tid = create(f"MERGE: {len(unapplied)} cikti DB'ye uygulanacak",
                     MERGE_BODY + "\n\nUYGULANACAK CIKTILAR:\n" +
                     "\n".join(f"- {s['domain']}: {s.get('artifact')}" for s in unapplied[:6]),
                     "telemetry")
        made.append(f"MERGE={tid}")

    STATE.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")

    if made:
        print(f"[{now:%Y-%m-%d %H:%M}] pipeline: " + " ".join(made) +
              f" | pending={len(pending)} unapplied={len(unapplied)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

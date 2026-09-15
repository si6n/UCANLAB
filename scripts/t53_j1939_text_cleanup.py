#!/usr/bin/env python3
"""T53: T30 J1939 kalan metin temizligi (kirpik causes tamamlama + SEO silme).

Kaynaklar (mevcut yerel arsiv artifact'leri, YENI tarama/kredi YOK):
  - raw_truckfaultcode_pages.json  (kirpik causes tam geri kazanilir; 28/30)
  - SPN_4012[3] j1939hub arsivinden tamamlanir
  - SPN_3064[5] / SPN_1807[0] = saf genel/SEO metin, arsivde YOK -> SILINIR
  - 7 SEO satiri (Long-term strategies / Workshops frequently) + onlara bitisik
    sarkan SEO parcaciklari SILINIR

Idempotent: 2. calistirmada DB degismez (bkz. asagidaki assert'ler).

Kullanim: python scripts/t53_j1939_text_cleanup.py
"""
from __future__ import annotations
import json, re, hashlib, shutil, sys, io
from pathlib import Path

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "diagnostics" / "j1939_spn_fmi_database.json"
BAK = DB.with_suffix(".json.bak_t53")
RAW_TFC = Path("C:/Users/canak/Desktop/UCANLAB-ARSIV/collector-final/output-final/raw_truckfaultcode_pages.json")

CLIP = "\u2026"
CLIPCHK = re.compile(r"(\u2026|\.\.\.)\s*$")
# T30'un tanimi (audit_tur30 / T30 raporu ile birebir)
SEOCHK = ("long-term strategies", "workshops frequently encounter")


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def nw(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def make_norm(s: str):
    """dash/mojibake-duyarsiz normalize + orijinal offset haritasi."""
    out, mp, prev = [], [], False
    for i, ch in enumerate(s):
        if ch in "\ufffd\u2013\u2014\u2212-":
            ch = " "
        elif ch == "?" and i > 0 and s[i - 1] == "\ufffd":
            ch = " "
        if ch.isspace():
            if prev:
                continue
            out.append(" "); mp.append(i); prev = True
        else:
            out.append(ch.lower()); mp.append(i); prev = False
    while out and out[0] == " ":
        out.pop(0); mp.pop(0)
    return "".join(out), mp


def clean_text(s: str) -> str:
    s = s.replace("&#39;", "'").replace("&amp;", "&").replace("&quot;", '"')
    s = s.replace("&lt;", "<").replace("&gt;", ">")
    s = re.sub(r"\ufffd\??", " - ", s)
    s = nw(s)
    s = re.sub(r"(\d),(\d{3})-(?=\d)", r"\1,\2 - ", s)
    s = re.sub(r"\s-([a-z])", r" - \1", s)
    return nw(s)


def longest_contig(c: str, t: str):
    for L in range(len(c), 0, -1):
        if c[:L] in t:
            return L, "head"
    for L in range(len(c), 0, -1):
        if c[-L:] in t:
            return L, "tail"
    return 0, None


def recover_tfc(by_tfc, spn_num: int, clipped: str):
    c, _ = make_norm(clipped)
    best = None
    for p in by_tfc.get(spn_num, []):
        t_orig = nw(p.get("text", ""))
        t, mp = make_norm(t_orig)
        L, kind = longest_contig(c, t)
        if L < 40:
            continue
        key = c[:L] if kind == "head" else c[-L:]
        pos = t.find(key)
        if pos < 0:
            continue
        tail = t_orig[mp[pos]:]
        ci = tail.find("First Checks")
        block = nw(tail[:ci]) if 0 < ci else nw(tail[:len(clipped) + 500])
        block = clean_text(block)
        score = (L, len(block))
        if best is None or score > best[0]:
            best = (score, block, p["url"])
    return (best[1], best[2]) if best else (None, None)


# ------------------------------------------------------------------ 0) yedek
raw_before = DB.read_text(encoding="utf-8")
db = json.loads(raw_before)
spns = db["spns"]
n_before = len(spns)
empties_before = {k for k, v in spns.items() if not (v.get("causes") or [])}

if not BAK.exists():
    shutil.copy2(DB, BAK)
    print(f"[0] yedek olusturuldu: {BAK.name}  sha256={sha(BAK)[:16]}...")
else:
    print(f"[0] yedek zaten var (pristine): {BAK.name}  sha256={sha(BAK)[:16]}...")

# Pristine icerikten basla (idempotent: her run ayni girdiden uretir)
base = json.loads(BAK.read_text(encoding="utf-8")) if BAK.exists() else db
spns = base["spns"]
# marker'lari temizle (onceki run'dan kalma)
for v in spns.values():
    v.pop("_t53_causes_recovered", None)
    v.pop("_t53_causes_removed", None)

try:
    tfc = json.loads(RAW_TFC.read_text(encoding="utf-8"))["pages"]
except FileNotFoundError:
    tfc = []
by_tfc = {}
for p in tfc:
    by_tfc.setdefault(p.get("spn"), []).append(p)

# ------------------------------------------------- 1) truckfaultcode kirpik tamamlama
recovered = []
for k, v in spns.items():
    spn_num = v.get("spn")
    if not isinstance(spn_num, int):
        continue
    for i, c in enumerate(v.get("causes", []) or []):
        if not (isinstance(c, str) and c.rstrip().endswith(CLIP)):
            continue
        if len(c) >= 295:      # 300-char j1939hub kirpikleri -> adim 2
            continue
        full, url = recover_tfc(by_tfc, spn_num, c)
        if full and len(full) > len(c):
            v["causes"][i] = full
            v.setdefault("_t53_causes_recovered", []).append(
                {"idx": i, "old_len": len(c), "new_len": len(full), "source": url})
            recovered.append((k, i, len(c), len(full), url))

print(f"[1] truckfaultcode ile geri yazilan kirpik: {len(recovered)}")
for k, i, a, b, u in recovered:
    print(f"    {k}[{i}]  {a}->{b}  {u.split('/')[-2]}")

# ------------------------------------------------- 2) kalan kirpik (j1939hub 300-char)
#   SPN_4012[3] -> j1939hub arsivinden tamamla
#   SPN_3064[5] / SPN_1807[0] = arsivde olmayan genel/SEO metin -> SIL
hub_removed = []
hub_recovered = []
RAW_HUB = Path("C:/Users/canak/Desktop/UCANLAB-ARSIV/collector-final/output-final/raw_j1939hub_gap_pages.json")
try:
    hub_pages = json.loads(RAW_HUB.read_text(encoding="utf-8"))["pages"]
except FileNotFoundError:
    hub_pages = []

def recover_hub_4012(clipped: str):
    for p in hub_pages:
        t = nw(p.get("text", ""))
        idx = t.find("Alternator Overvoltage: Regulator failure")
        if idx < 0:
            continue
        seg = t[idx:]
        end = seg.find("Step-by-Step Troubleshooting")
        if end < 0:
            end = seg.find("Common real-world")
        if end > 0:
            seg = seg[:end]
        full = clean_text(seg)
        if full and len(full) > len(clipped):
            return full, p["url"]
    return None, None

for k, v in spns.items():
    for i, c in enumerate(list(v.get("causes", []) or [])):
        if not (isinstance(c, str) and c.rstrip().endswith(("\u2026", "..."))):
            continue
        if len(c) < 295:
            continue  # bunlar zaten [1]'de halledildi
        if k == "SPN_4012":
            full, url = recover_hub_4012(c)
            if full:
                v["causes"][i] = full
                v.setdefault("_t53_causes_recovered", []).append(
                    {"idx": i, "old_len": len(c), "new_len": len(full), "source": url})
                hub_recovered.append((k, i, len(c), len(full), url))
                continue
        # arsivde olmayan genel/SEO 300-char kirpik -> sil
        hub_removed.append((k, i, c))

for k, i, c in sorted(hub_removed, key=lambda x: -x[1]):
    v = spns[k]
    assert v["causes"][i] == c
    v["causes"].pop(i)
    v.setdefault("_t53_causes_removed", []).append(
        {"idx": i, "text": c, "reason": "seo_generic_clipped"})

print(f"[2] j1939hub 300-char kirpik: {len(hub_recovered)} tamamlandi, {len(hub_removed)} SILINDI")
for k, i, a, b, u in hub_recovered:
    print(f"    {k}[{i}] {a}->{b}  {u}")
for k, i, c in hub_removed:
    print(f"    {k}[{i}] SILINDI: {c[:70]}...")

# ------------------------------------------------- 3) SEO satirlari (7) + sarkan SEO parcaciklari
SEO_EXACT = {
    "SPN_98":   ["Long-term strategies involve routine checks and maintenance of the oil level sensor and related wiring"],
    "SPN_6780": ["Long-term strategies for addressing SPN 6780 FMI 21 involve routine sensor checks and calibration",
                 "Workshops frequently encounter this fault after ECM updates or sensor replacements"],
    "SPN_4813": ["Long-term strategies involve regular maintenance checks and updating ECM software to prevent recurring issues"],
    "SPN_230":  ["Long-term strategies involve regular updates and recalibration of network configurations"],
    "SPN_1059": ["Long-term strategies involve routine sensor recalibrations and periodic inspections of wiring integrity"],
    "SPN_1787": ["Workshops frequently encounter this fault during post-service checks, especially after ECM reprogramming sessions"],
}
# sarkan SEO parcaciklari (T30 SEO cumlelerinin yarim kalan kuyrugu) -> ait olduklari SPN
DANGLING = {"SPN_230": ["Workshops often employ", "For long-term prevention, regular"],
            "SPN_6780": ["Technicians should incorporate regular sensor"],
            "SPN_1787": ["Real-world examples show that consistent"]}

removed_seo, already_gone = [], []
for k, texts in SEO_EXACT.items():
    v = spns[k]
    for tx in texts:
        if tx in v["causes"]:
            v["causes"].remove(tx)
            v.setdefault("_t53_causes_removed", []).append({"text": tx, "reason": "seo"})
            removed_seo.append((k, tx))
        else:
            already_gone.append((k, tx))
# sarkan SEO parcaciklari (T30 SEO cumlelerinin yarim kalan kuyrugu)
for k, texts in DANGLING.items():
    v = spns[k]
    for tx in texts:
        if tx in v["causes"]:
            v["causes"].remove(tx)
            v.setdefault("_t53_causes_removed", []).append({"text": tx, "reason": "seo_dangling"})
            removed_seo.append((k, tx))
print(f"[3] silinen SEO satiri/parcasi: {len(removed_seo)} (zaten yok: {len(already_gone)})")
for k, tx in removed_seo:
    print(f"    {k}: {tx[:75]}")

# ------------------------------------------------------------------ 4) assert
bad_clip, bad_seo = [], []
for k, v in spns.items():
    for c in (v.get("causes", []) or []):
        if not isinstance(c, str):
            continue
        if CLIPCHK.search(c.strip()):
            bad_clip.append((k, c[:60]))
        if any(h in c.lower() for h in SEOCHK):
            bad_seo.append((k, c[:60]))

# T53'un dokundugu SPN'lerde causes bos kalmamali
touched = {k for k, *_ in recovered} | {k for k, *_ in hub_removed} | set(SEO_EXACT) | set(DANGLING)
emptied = [k for k in touched if not (spns[k].get("causes") or [])]

n_after = len(spns)
print("\n[4] ASSERT")
print(f"    kirpik causes kalan : {len(bad_clip)} {bad_clip}")
print(f"    SEO causes kalan    : {len(bad_seo)} {bad_seo}")
print(f"    causes bosalan SPN  : {len(emptied)} {emptied}")
print(f"    kayit sayisi        : {n_before} -> {n_after}")

assert not bad_clip, f"kirpik kaldi: {bad_clip}"
assert not bad_seo, f"SEO kaldi: {bad_seo}"
assert not emptied, f"causes bosalan: {emptied}"
assert n_before == n_after == 3910

# ------------------------------------------------------------------ 5) yaz
base["metadata"]["t53_cleanup"] = {
    "date": "2026-09-16",
    "clipped_recovered_truckfaultcode": len(recovered),
    "clipped_removed_seo_generic": len(hub_removed),
    "seo_removed": len(removed_seo),
    "note": "T30 kalan kirpik causes tamamlandi (kaynak: yerel arsiv); genel/SEO satirlari silindi",
    "sha256_before": sha(BAK),
}
DB.write_text(json.dumps(base, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
print(f"\n[5] yazildi. DB sha256={sha(DB)[:16]}...")
print("TAMAM")

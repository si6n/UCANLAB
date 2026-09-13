---
tags: [ajan-raporu, ai, analiz, tur27]
ajan: tuner
tur: Tur-27
---

# Çevrimdışı AI Analizi — Tur-27

Tam plan: `docs/AI_GELISTIRME_PLANI.md` (182 satır, kanıtlı)

## Ana Cevap: SPN Kontrolü = EVET, ama 3 DELİKLE

| Yol | Durum |
|---|---|
| Sorgu ("SPN 100 FMI 4") | ✅ Zengin — fault_matrix + DM1 butonları |
| Oturum (canlı DM1) | ❌ J1939 DB BYPASS — genel fallback |

## 3 Kritik Delik
1. **P0** Oturum yolu SPN DB'sine düşmüyor (~30 satır) — canlı arıza raporu fakir
2. **P0** `oem_variants`+`gm_monitor`+`nhtsa_evidence` (5.400 kayıt) ÖLÜ — 0 kod okur
3. **P1** FMI severity taşınmıyor, FMI 20-31 tanımsız, DTC korelasyonu yok

## 10 Boşluk / 5 Faz
- Faz1: Oturum SPN fallback (P0)
- Faz2: Ölü OEM katmanları rapora bağla (P0)
- Faz3: FMI severity + korelasyon (P1)
- Faz4: Veri katkıları (P2)
- Faz5: Kullanıcı kartı köprüsü (P3)

# MANIFEST — `data/intake/` kaynak kaydı

`data/intake/` altındaki **her** artefaktın kaynağı, lisansı, bayt sayısı ve
`sha256` özeti burada zorunludur. Doğrulayıcı
(`python scripts/validate_intake.py`) tabloyu diskle karşılaştırır: eksik
satır, yanlış hash, yanlış lisans veya `intake_id` tekrarı **FAIL**'dir.

Kurallar ve terfi adımları: `data/intake/README.md`.

- `kind` değerleri: `dtc`, `spn_fmi`, `case`, `oem_note`, `trace`,
  `trace_frames` (bir trace'in kare dosyası).
- `licence` kaydın kendi `source.licence` değeriyle **birebir** aynı olmalıdır
  (kapalı liste: `ALLOWED_LICENCES`; belirsiz lisans reddedilir).
- `_templates/` altındaki şablonlar kayıt değildir; buraya yazılmaz.
- Satır biçimi örnekleri README § "MANIFEST satır biçimi" bölümündedir.

## Git'e giren kayıtlar

| intake_id | path | kind | bytes | sha256 | licence | source |
|---|---|---|---|---|---|---|

## Git'e girmeyen trace'ler (yalnız hash + konum)

Büyük yakalamalar (>`1 MiB`) depoya girmez; yalnız `sha256` + konum burada
tutulur. `sha256` terfi öncesi mutlaka doğrulanır.

| intake_id | format | bytes | sha256 | location |
|---|---|---|---|---|

## Değişiklik günlüğü

Kayda alınan her satır için: tarih, ekleyen, terfi kararı (ör. “P0301 üretici
metnine göre zaten mevcut — terfi yok”). Bu günlük bilgi tabanı değildir;
`data/PROVENANCE.md` ve `data/diagnostics/PROVENANCE.md` değiştirilmeden
burada bırakılır.

# Review Kaynak Paketleri

Bu dosyalar güçlü modele aşamalı review yaptırmak için hazırlanmıştır.
Satır numaralı kaynak içeriği gömülüdür — ayrıca dosya okumaya gerek yoktur.

| Aşama | Paket | Token | Kapsam |
|---|---|---|---|
| 1 | ASAMA-1-PAKET.txt | ~31K | TX güvenlik: gateway, estop, state_machine |
| 2 | ASAMA-2-PAKET.txt | ~12K | Lisans, HWID, anti-tamper, auth |
| 3a | ASAMA-3a-PAKET.txt | ~27K | AI motoru (1-1700) |
| 3b | ASAMA-3b-PAKET.txt | ~26K | AI motoru (1701-3394) |
| 4 | ASAMA-4-PAKET.txt | ~62K | J1939 TP, ISO-TP, flasher, UDS |
| 5 | ASAMA-5-PAKET.txt | ~23K | rolling_disk, ring_buffer, DBC, router |
| 6 | ASAMA-6-PAKET.txt | ~52K | UI, launcher, main |

## Kullanım
Her aşama AYRI OTURUMDA çalıştırılır (token tasarrufu %55+).
İlgili paketi modele ver, çıktıyı `obsidian-vault/02-Diagnostics/review/` altına kaydet.

Paketler `obsidian-vault/02-Diagnostics/review-input/` klasöründen üretilmiştir.

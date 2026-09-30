# Risk → Test İzlenebilirlik Matrisi

Her remediation risk maddesinin (F-xx) hangi test dosyası ve test adıyla
karşılığı olduğunu gösterir. P0 satırlarının karşılığı manuel olarak korunur; matrisi
`pytest --collect-only` çıktısıyla karşılaştıran otomatik bir CI kontrolü
şu an **yoktur** (yapılması gereken iş).

Bakım kuralı: yeni bir F maddesi eklendiğinde veya bir test yeniden
adlandırıldığında bu tablo aynı PR'da güncellenir.

## FAZ 0 — Güvenlik Kapama

| Risk ID | Öncelik | Risk | Test Dosyası | Test(ler) |
|---|---|---|---|---|
| F-01 | P0 | Pickle RCE + chunk tamper | `tests/unit/test_rolling_disk.py` | `test_body_tamper_is_rejected`, `test_authenticated_header_tamper_is_rejected`, `test_bad_magic_is_rejected`, `test_truncated_and_trailing_data_are_rejected`, `test_reserved_frame_flags_are_rejected_even_with_valid_hmac`, `test_legacy_chunk_is_moved_out_of_active_store` |
| F-02 | P0 | HTML/KML enjeksiyon | `tests/unit/test_exporters.py` | `test_*report*`, `test_*kml*` (escape davranışı) |
| F-03 | P0 | API key URL'de | `tests/unit/test_ai_copilot.py` | `test_gemini_request_carries_key_in_header_not_url` (x-goog-api-key header, URL'de key yok), `test_gemini_endpoint_constant_matches_readme_model` |
| F-04 | P0 | HWM hardcoded key / plaintext / drift | `tests/unit/test_license_validator.py` | `test_persistent_high_water_mark_corrupt_file`, `test_persistent_high_water_mark_tampered_file`, `test_license_clock_rollback_attack_vectors` (adversarial) |
| F-05 | P0 | Wildcard fingerprint bypass | `tests/unit/test_license_validator.py` | `test_license_wildcard_hwid`; `tests/unit/test_adversarial_final_gate.py::test_license_wildcard_hwid_behavior` |
| F-06 | P0 | CI supply chain | `.github/workflows/ci.yml` | "Verify Action Pins (E-7)" adımı (her koşuda çalışır) |
| F-07 | P0 | Demo script fiziksel hatta | `scripts/demo_traffic_generator.py` | `ALLOWED_INTERFACES` kilidi — `RuntimeError` (manuel doğrulama: fiziksel interface reddi) |
| F-10 | P1 | Anti-tamper fail-open | `tests/unit/test_anti_tamper.py` | `test_anti_tamper_checks` |
| F-12 | P1 | build_exe shell=True | — | Statik: `ruff`/review (liste argüman + `shutil.which`) |

## FAZ 1 — Safety & Protokol

| Risk ID | Öncelik | Risk | Test Dosyası | Test(ler) |
|---|---|---|---|---|
| F-13/F-26 | P0 | UDS sync motor + gateway offload | `tests/unit/test_uds_client.py`, `tests/unit/test_isotp.py` | `IsoTpSender/Receiver` async roundtrip; `test_safety_gateway.py` bütçe testleri |
| F-14 | P0 | NRC 0x78 pending | `tests/unit/test_uds_client.py` | response-pending pencere uzatma (P2*) |
| F-15/F-18 | P1 | Rate limiter tip + token bucket | `tests/unit/test_safety_gateway.py` | `test_protocol_burst_budget_allows_full_bam_transfer` (BAM 255 → E-Stop YOK), `test_protocol_burst_budget_exhaustion_at_capacity_plus_one`, `test_diagnostic_budget_capacity_is_ten`, `test_calibration_budget_capacity_is_five`, `test_unknown_budget_category_is_rejected`, `test_budgets_are_independent_per_category` |
| F-16 | P0 | Watchdog UI'dan bağımsız | `tests/unit/test_tx_watchdog.py` | `test_tx_watchdog_lease_and_heartbeat`, `test_ui_freeze_expires_watchdog_and_triggers_estop` (900ms donma → expire), `test_live_ui_pulse_never_expires_watchdog` (250ms pulse → lease canlı) |
| F-17 | P1 | E-Stop token'sız FAULT çıkışı | `tests/unit/test_estop.py`, `tests/unit/test_safety_estop.py` | reset challenge/anti-replay testleri |
| F-19/F-20 | P1 | Session overwrite + channel keying | `tests/unit/test_j1939_transport.py` | `test_j1939_session_collision_handling`, `test_j1939_session_keying_strict_node_isolation` |
| F-22 | P1 | scan_bitrate sızıntısı | `tests/unit/test_hal.py` | HAL lifecycle testleri (finally disconnect) |
| F-23 | P2 | recv(None) sonsuz blok | `tests/unit/test_hal.py` / multiplexer | bounded timeout davranışı |
| F-24/F-25 | P2 | Session dict yarışı / N2K restart | `tests/unit/test_n2k_fast_packet.py` | restart regression |
| F-27 | P2 | Bare except | CI kuralı | `ruff check --select E722,S110` (CI'da kalıcı) |

## FAZ 2 — Entegrasyon

| Risk ID | Öncelik | Risk | Test Dosyası | Test(ler) |
|---|---|---|---|---|
| F-28 | P0 | UI'a gerçek veri akışı | `tests/unit/test_desktop_app.py` | live ingestion + `_decode_j1939_signal` |
| F-30 | P1 | Çift bus | `tests/unit/test_main.py` | tek composition root ctor |
| F-31 | P1 | Claim timer yok | `tests/unit/test_j1939_address_claim.py` | `test_claim_confirms_automatically_after_250ms_window` (daemon timer), `test_contention_inside_window_cancels_auto_confirmation`, `test_contention_late_does_not_break_confirmed_claim` |
| F-32 | P2 | Copilot UI freeze | `tests/unit/test_ai_copilot.py` | executor + timeout davranışı |
| F-33 | P2 | Ring buffer kopya | `tests/unit/test_ring_buffer.py` | `test_get_latest_view_is_true_zero_copy` (`np.shares_memory` assert — E-13), `test_get_latest_view_wraparound_ordering`, `test_get_latest_view_empty_request`, `test_ring_buffer_wrapping`, `test_ring_buffer_two_phase_read_wraparound_exact` |
| F-34 | P2 | Flush bloklaması | `tests/unit/test_rolling_disk.py` | append/flush/read roundtrip |
| F-35 | P2 | TS batch | `src/ui/frontend` (derleme) | frontend-build CI job'ı |

## FAZ 3 — Hijyen & CI

| Risk ID | Öncelik | Risk | Karşılık |
|---|---|---|---|
| F-36 | P1 | CI hardening | `ci.yml`: coverage ≥ %80, bandit, pip-audit, vcan job, CodeQL |
| F-37 | P1 | .gitignore sızıntısı | `machine_seed.bin`, `*.lic`, `*.dpapi`, `data/` yüklü |
| F-42 | P1 | Doküman-kod senkron | Bu matris + README tek kaynak ilkeleri |

## N Serisi — İnceleme Kapanışları

| ID | Konu | Karşılık |
|---|---|---|
| N-01 | Watchdog testi | `tests/unit/test_tx_watchdog.py` mevcut — KAPALI |
| N-02 | Ring buffer wrap testi | `test_ring_buffer_two_phase_read_wraparound_exact` — KAPALI |
| N-03 | `time.sleep` flaky riski | KAPALI — tarama yapıldı; watchdog/stress/router testleri 3x tekrar kararlı (0.43–7.6s); kalan sleep'ler eşik bekleme amaçlı (ör. 100ms WD timeout'a karşı 200ms), toleranslı |
| N-04 | Anti-tamper davranış testleri | `test_anti_tamper.py::test_anti_tamper_checks` — KAPALI |
| N-05 | Derlenmiş artefaktta safety modülleri | KAPALI — `build_exe.py`'ye `--paths=<root>` + 7 adet `--hidden-import=src.safety.*/src.security.*` eklendi; artifact içi modül sorgusu build sonrası doğrulanır |

---
title: "AI Context: Testing & Quality Verification"
tags:
  - ai-context
  - testing
  - pytest
  - hypothesis
  - coverage
  - verification
updated: 2026-09-01
---

# Testing & Quality Verification Context Card

Bu kart, projede yeni kod yazılırken veya refactor yapılırken testlerin nasıl yazılması ve koşturulması gerektiğini belirler.

## 1. Test Piramidi & Standartları

- **Toplam Test Durumu:** 1160+ Test (%100 Pass Oranı).
- **Test Çerçeveleri:** `pytest`, `pytest-cov`, `hypothesis` (Property-based testing).
- **Kod Kalitesi & Linting:** `ruff` (0 hata toleransı).

## 2. Test Kategorileri

| Dizin | Kapsam | Örnek Dosyalar |
|---|---|---|
| `tests/unit/` | Düz dizin: core, protokoller, HAL, AI copilot, veri bütünlüğü, anti-tamper, bulut istemcisi, adversarial ve benchmark testleri (`test_*.py`) | `test_can_frame.py`, `test_dbc_decoder.py`, `test_ai_copilot.py`, `test_data_integrity.py`, `test_benchmark_ai_copilot.py` |
| `tests/safety/` | TX choke-point mimarisi, AI-TX izolasyonu, link-fault E-Stop, E2E safety audit | `test_tx_chokepoint_architecture.py`, `test_ai_tx_isolation.py` |
| `tests/integration/` | Benchmark vektörleri | `test_benchmark_vectors.py` |
| `tests/e2e/` | Uçtan uca güvenlik/taşıma/teşhis senaryoları | `test_phase1_e2e.py`, `test_safety_wiring.py` |
| `tests/fixtures/` | Ortak test verisi | — |

> Benchmark testleri `benchmark` marker'ı ile işaretlidir (`pytest -m "not benchmark"` ile atlanır).

## 3. Test Koşturma Komutları

```powershell
# Hızlı birim testleri koşturma
pytest -v -m "not slow"

# Tam test süitini koşturma
pytest -v

# Ruff linter & format kontrolü
ruff check .
ruff format --check .

# Yalnız safety testleri
pytest tests/safety/

# Benchmark'ları atla (hızlı yineleme)
pytest -m "not benchmark"
```

## 4. AI İçin Test Kuralları
- Eklenen her yeni özellik için **en az bir pozitif**, **en az iki sınır/hata (boundary & negative)** test senaryosu yazılmalıdır.
- Zaman aşımı içeren testlerde `sleep` kullanılmamalı; `VirtualClock` veya enjekte edilmiş `ClockProvider` mock'lanmalıdır.

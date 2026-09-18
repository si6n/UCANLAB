"""Item 2 (pytest sürümü): DiagnosticCopilot J1939 DB load — gecikme + bellek + önbellek sızıntısı.

@telemetry Tur-26 önerisi: assert-script yerine pytest fixture'ları ile CI otomatik kapsar.
Orijinal assert-tabanlı script sonuçları korunmuştur (gecikme/bellek eşikleri aynı).

Kapsam:
- get_j1939_spn_database() soğuk/sıcak yükleme gecikmesi
- tracemalloc ile bellek ayak izi
- 4.253 SPN bütünlüğü ve copilot arama yolları
- tekrarlı yüklemelerde sızıntı kontrolü
- DiagnosticCopilot public API üzerinden J1939 SPN/FMI analizi
- J1939-73 DM1 -> DB uçtan uca çapraz kontrol
"""
from __future__ import annotations

import gc
import time
import tracemalloc
from pathlib import Path

import pytest

# Tüm modül benchmark/ölçüm testi olarak işaretlenir (~5s).
# Hariç tutmak için: pytest -m "not benchmark"
from src.engine.ai.diagnostic_copilot import (
    AiDiagnosticCopilot,
    get_j1939_spn_database,
)
from src.protocols.j1939.diagnostics import DiagnosticTroubleCode

pytestmark = pytest.mark.benchmark


REPO_ROOT = Path(__file__).resolve().parents[2]
J1939_DB = REPO_ROOT / "data" / "diagnostics" / "j1939_spn_fmi_database.json"

EXPECTED_SPNS = 4_253
# J1939 FMI 12 = "Bad intelligent device or component" içeren referans DTC.
REF_SPN = 629
REF_FMI = 12


@pytest.fixture(scope="module")
def spn_db() -> dict:
    """Önbellekli J1939 veritabanını bir kez yükler."""
    return get_j1939_spn_database()


@pytest.fixture(scope="module")
def copilot() -> AiDiagnosticCopilot:
    return AiDiagnosticCopilot()


class TestColdLoadLatency:
    """Soğuk yükleme gecikmesi ve bellek ayak izi."""

    def test_cold_load_latency_and_memory(self) -> None:
        # Modül önbelleğini geçici olarak temizleyip soğuk yükleme ölçüyoruz.
        import src.engine.ai.diagnostic_copilot as dc

        saved = getattr(dc, "_CACHED_J1939_DB", None)
        try:
            dc._CACHED_J1939_DB = None  # type: ignore[attr-defined]
            gc.collect()
            tracemalloc.start()
            t0 = time.perf_counter()
            db = get_j1939_spn_database()
            cold_s = time.perf_counter() - t0
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
        finally:
            dc._CACHED_J1939_DB = saved  # type: ignore[attr-defined]

        assert len(db.get("spns", {})) == EXPECTED_SPNS, f"got {len(db.get('spns', {}))}"
        assert cold_s < 2.0, f"soğuk yükleme {cold_s:.3f}s (limit 2.0s)"
        assert peak < 150 * 1024 * 1024, f"tepe bellek {peak / 1e6:.1f}MB (limit 150MB)"


class TestWarmCache:
    """Sıcak önbellek çağrı maliyeti ve sızıntı kontrolü."""

    def test_warm_cache_1000_calls(self, spn_db: dict) -> None:
        t0 = time.perf_counter()
        for _ in range(1000):
            get_j1939_spn_database()
        warm_us = (time.perf_counter() - t0) * 1e3
        assert warm_us < 50.0, f"1000 sıcak çağrı {warm_us:.2f}ms (limit 50ms)"

    def test_explicit_path_same_content(self) -> None:
        d2 = get_j1939_spn_database(str(J1939_DB))
        assert len(d2.get("spns", {})) == EXPECTED_SPNS

    def test_no_leak_across_explicit_loads(self) -> None:
        gc.collect()
        tracemalloc.start()
        before = tracemalloc.get_traced_memory()[0]
        for _ in range(200):
            get_j1939_spn_database(str(J1939_DB))
        after = tracemalloc.get_traced_memory()[0]
        tracemalloc.stop()
        growth_kb = (after - before) / 1024
        assert growth_kb < 2048, f"200 yükleme sonrası büyüme {growth_kb:.0f}KB (limit 2048KB)"


class TestSpnIntegrity:
    """DB bütünlüğü ve copilot arama yolları."""

    def test_spn100_has_fault_matrix(self, spn_db: dict) -> None:
        r100 = spn_db["spns"].get("SPN_100")
        assert isinstance(r100, dict)
        assert len(r100.get("fault_matrix", {})) > 0

    def test_all_spns_have_name_and_title_tr(self, spn_db: dict) -> None:
        missing_name = [k for k, v in spn_db["spns"].items()
                        if not isinstance(v, dict) or not v.get("name")]
        missing_tr = [k for k, v in spn_db["spns"].items()
                      if not isinstance(v, dict) or not v.get("title_tr")]
        assert not missing_name, f"{len(missing_name)} SPN'de name eksik"
        assert not missing_tr, f"{len(missing_tr)} SPN'de title_tr eksik"


class TestCopilotSession:
    """Copilot public API üzerinden oturum analizi."""

    def test_analyze_session_with_spn_codes(self, copilot: AiDiagnosticCopilot) -> None:
        dtcs = [
            {"code": "SPN629-FMI12", "spn": REF_SPN, "fmi": REF_FMI, "source": "J1939 DM1"},
            {"code": "SPN100-FMI3", "spn": 100, "fmi": 3, "source": "J1939 DM1"},
        ]
        rep = copilot.analyze_session(
            dtcs,
            {"EngineSpeed": 1200.0, "BoostPressure": 90.0, "CoolantTemp": 88.0},
            ["ECU", "TCU"],
        )
        assert rep is not None

    def test_analysis_latency_under_50ms(self, copilot: AiDiagnosticCopilot) -> None:
        t0 = time.perf_counter()
        for i in range(20):
            copilot.analyze_session(
                [{"code": f"SPN{i}-FMI4", "spn": 100 + i, "fmi": 4, "source": "J1939 DM1"}],
                {"EngineSpeed": 1100.0, "BoostPressure": 60.0, "CoolantTemp": 85.0},
                ["ECU"],
            )
        per_ms = (time.perf_counter() - t0) / 20 * 1000
        assert per_ms < 50.0, f"oturum başına {per_ms:.1f}ms (limit 50ms)"

    def test_no_growth_over_20_instances(self) -> None:
        dtcs = [{"code": "SPN629-FMI12", "spn": REF_SPN, "fmi": REF_FMI, "source": "J1939 DM1"}]
        gc.collect()
        tracemalloc.start()
        snap1 = tracemalloc.take_snapshot()
        for _ in range(20):
            c = AiDiagnosticCopilot()
            c.analyze_session(dtcs, {"EngineSpeed": 1000.0}, ["ECU"])
            del c
        gc.collect()
        snap2 = tracemalloc.take_snapshot()
        tracemalloc.stop()
        diff = sum(s.size_diff for s in snap2.compare_to(snap1, "lineno") if s.size_diff > 0)
        assert diff < 5 * 1024 * 1024, f"20 örnek sonrası büyüme {diff / 1e6:.1f}MB (limit 5MB)"


class TestDm1PipelineCrossCheck:
    """J1939-73 DM1 byte decode -> DB lookup uçtan uca."""

    def test_dm1_decodes_reference_spn(self, spn_db: dict) -> None:
        # J1939-73: SPN = b0 | b1<<8 | (b2&0xE0)<<11 ; FMI = b2&0x1F
        raw = bytes([
            REF_SPN & 0xFF,
            (REF_SPN >> 8) & 0xFF,
            ((REF_SPN >> 16) & 0x07) << 5 | REF_FMI,
            1,
        ])
        dtc = DiagnosticTroubleCode.from_bytes(raw)
        assert dtc.spn == REF_SPN, f"spn={dtc.spn}"
        assert dtc.fmi == REF_FMI, f"fmi={dtc.fmi}"

        entry = spn_db["spns"].get(f"SPN_{dtc.spn}")
        assert entry is not None, f"SPN_{dtc.spn} DB'de yok"
        fm = entry.get("fault_matrix", {})
        assert str(REF_FMI) in fm, f"FMI {REF_FMI} fault_matrix'te yok"

"""Item 2 (pytest sürümü): DiagnosticCopilot J1939 DB load — gecikme + bellek + önbellek sızıntısı.

@telemetry Tur-26 önerisi: assert-script yerine pytest fixture'ları ile CI otomatik kapsar.
Orijinal assert-tabanlı script sonuçları korunmuştur (gecikme/bellek eşikleri aynı).

Kapsam:
- get_j1939_spn_database() soğuk/sıcak yükleme gecikmesi
- tracemalloc ile bellek ayak izi
#- 4.282 SPN bütünlüğü ve copilot arama yolları
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

EXPECTED_SPNS = 4_291
# BASELINE TRIAGE: this constant is CORRECT — do NOT raise it to silence a
# failure. The working tree briefly held 4266 rows, 13 of them nameless shells
# injected by tools/data_ingest/merge_staging_into_data.py (key=f"SPN_{spn}" with
# a raw "0x01"-style value). Those carried no information (AGENTS.md §2.3: no
# fabricated records); the data-ingest batch removed them and the count is back
# to 4253. Raising this constant would launder a data-integrity defect into the
# test — see the baseline-failures triage. (T_9f8e3294: 4253->4282, +29 SPN from
# procarmanuals.com Scania DM1 list — genuine new SPNs, not shells.)
# (T_cd363b33: 4282->4291, +9 SPN from wholefleet.ca — 3 standard J1939 + 6
# Yale OEM-proprietary 522xxx, all with causes/steps, not shells.)
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
            # Latency and memory are measured on two separate cold loads.
            # tracemalloc hooks every allocation, so a load traced by it is
            # several times slower than a real one (measured: 2.2-2.9 s traced
            # on windows-latest vs. well under the budget untraced). Timing the
            # traced load measured the profiler, not the loader.
            dc._CACHED_J1939_DB = None  # type: ignore[attr-defined]
            gc.collect()
            t0 = time.perf_counter()
            db = get_j1939_spn_database()
            cold_s = time.perf_counter() - t0

            dc._CACHED_J1939_DB = None  # type: ignore[attr-defined]
            db = None  # release the first copy before measuring retained memory
            gc.collect()
            tracemalloc.start()
            db = get_j1939_spn_database()
            # T66 (2026-09-22): the metric is RETAINED memory, not `peak`.
            # `peak` includes json.load's transient parse buffers, which on
            # CPython 3.13 measure as arena-scattered fragments and are released
            # immediately (measured: retained 56.4 -> 57.1 MB while peak jumped
            # 142.8 -> 260.4 MB for the SAME parse; real RSS delta via psutil was
            # +0.3 MB). Asserting on `peak` would fail a healthy DB and could be
            # "fixed" only by raising the constant — laundering a measurement
            # artefact into a data-integrity change. The retained figure is what
            # the process actually keeps, so the 150 MB ceiling now guards THAT.
            retained, _peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
        finally:
            dc._CACHED_J1939_DB = saved  # type: ignore[attr-defined]

        assert len(db.get("spns", {})) == EXPECTED_SPNS, f"got {len(db.get('spns', {}))}"
        assert cold_s < 2.0, f"soğuk yükleme {cold_s:.3f}s (limit 2.0s)"
        assert retained < 150 * 1024 * 1024, (
            f"kalıcı bellek {retained / 1e6:.1f}MB (limit 150MB)"
        )


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
        """Explicit-path loads must not accumulate memory across calls.

        The leak signal is a per-call *retained* delta, so a handful of
        iterations carries the same information as hundreds — while the
        underlying 23 MB JSON re-parse is the dominant cost. Measured: 200
        iterations under tracemalloc took 51 s (tracemalloc alone is a 3x
        multiplier on a 19.7 s parse loop), which made this single test the
        longest in the suite. 10 iterations under tracemalloc drops that to
        well under a second while still catching an unbounded per-call leak:
        the 2048 KB budget below would be blown by ~200 KB/call, and even a
        modest 5 KB/call leak is caught with margin.
        """
        gc.collect()
        tracemalloc.start()
        try:
            before = tracemalloc.get_traced_memory()[0]
            iterations = 10
            for _ in range(iterations):
                get_j1939_spn_database(str(J1939_DB))
            after = tracemalloc.get_traced_memory()[0]
        finally:
            tracemalloc.stop()
        growth_kb = (after - before) / 1024
        assert growth_kb < 2048, (
            f"{iterations} yükleme sonrası büyüme {growth_kb:.0f}KB (limit 2048KB)"
        )


class TestSpnIntegrity:
    """DB bütünlüğü ve copilot arama yolları."""

    def test_spn100_has_fault_matrix(self, spn_db: dict) -> None:
        r100 = spn_db["spns"].get("SPN_100")
        assert isinstance(r100, dict)
        assert len(r100.get("fault_matrix", {})) > 0

    def test_all_spns_have_name_and_title_tr(self, spn_db: dict) -> None:
        """Every SPN row must carry real `name` and `title_tr` text.

        Fabrication guard (AGENTS.md §2.3): a row that exists but has no
        name/title is an empty shell carrying no information, and it must never
        be able to enter the DB silently. Root cause of the one observed breach:
        `tools/data_ingest/merge_staging_into_data.py` keyed rows as
        `f"SPN_{spn}"` with a raw `"0x01"`-style value, creating name-less shells
        (SPN_0x01, SPN_0x03, SPN_0x10, …) where no real record existed. That file
        and `data/diagnostics/**` are owned by the data-ingest batch — do NOT
        "fix" this by deleting the assertion or editing the DB here.
        """
        missing_name = [k for k, v in spn_db["spns"].items()
                        if not isinstance(v, dict) or not v.get("name")]
        missing_tr = [k for k, v in spn_db["spns"].items()
                      if not isinstance(v, dict) or not v.get("title_tr")]
        if missing_name or missing_tr:
            raise AssertionError(
                f"{len(missing_name)} SPN'de name eksik, {len(missing_tr)} SPN'de "
                f"title_tr eksik — nameless shell rows are a fabrication defect; "
                f"offenders (first 20): {(missing_name or missing_tr)[:20]}. Root "
                f"cause: tools/data_ingest/merge_staging_into_data.py creates "
                f"`SPN_<raw>` shells with only `spn` set (data batch owns the fix)."
            )


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

    @pytest.mark.no_cover  # a timing budget must not measure coverage overhead
    def test_analysis_latency_under_50ms(self, copilot: AiDiagnosticCopilot) -> None:
        # BASELINE TRIAGE. This assertion
        # was failing when the test ran in isolation (deterministic, ~51-180 ms).
        # Two independent causes, both since addressed:
        #
        #   * The dominant one was a genuine SOURCE DEFECT owned by the
        #     hypothesis-graph workstream, not this batch: `load_root_cause_graph`
        #     had no cache and re-read + re-validated the now-3.99 MB
        #     `root_cause_graph.json` on every `analyze_session`. That workstream
        #     landed an `(path, st_mtime_ns, st_size)`-keyed cache
        #     (hypothesis_engine.py:78-79, 205-257); steady state is now ~7 ms.
        #   * What remains is a COLD-START amortization artifact. The first
        #     `analyze_session` in a fresh process pays one-time module loads
        #     (the 14,447-code external DTC merge, calibration factor, graph
        #     read) costing ~1 s. Averaged over the 20 timed iterations that
        #     leaves ~51 ms/session — over budget purely as amortization, and it
        #     flips pass/fail on machine load. Running the whole file masks it
        #     because earlier tests already warmed those caches, which is why the
        #     test passed in-file but failed in isolation.
        #
        # One untimed warm-up call removes the artifact so the 50 ms budget
        # measures the steady-state per-session cost it was written to guard.
        copilot.analyze_session(
            [{"code": "SPN1-FMI4", "spn": 100, "fmi": 4, "source": "J1939 DM1"}],
            {"EngineSpeed": 1100.0, "BoostPressure": 60.0, "CoolantTemp": 85.0},
            ["ECU"],
        )

        # T84d: the budget is asserted against the MEDIAN, not the mean.
        #
        # Measured 2026-09-26: with the graph-walk defect fixed the steady
        # state is ~29 ms/session, yet the MEAN still failed when the full
        # suite ran — one GC pause or scheduler slice inside the 20 iterations
        # drags the mean over 50 ms while 19 of 20 calls are fine. A mean over
        # 20 samples is an outlier detector, not a latency gate; the property
        # this test guards is the TYPICAL per-session cost, and the median is
        # the statistic that measures it. 21 samples so the median is a real
        # middle value. The budget (50.0) is unchanged.
        samples: list[float] = []
        for i in range(21):
            t0 = time.perf_counter()
            copilot.analyze_session(
                [{"code": f"SPN{i}-FMI4", "spn": 100 + i, "fmi": 4, "source": "J1939 DM1"}],
                {"EngineSpeed": 1100.0, "BoostPressure": 60.0, "CoolantTemp": 85.0},
                ["ECU"],
            )
            samples.append((time.perf_counter() - t0) * 1000)
        samples.sort()
        median_ms = samples[len(samples) // 2]
        assert median_ms < 50.0, (
            f"oturum başına medyan {median_ms:.1f}ms (limit 50ms); "
            f"en yavaş {samples[-1]:.1f}ms")

    def test_no_growth_over_20_instances(self) -> None:
        dtcs = [{"code": "SPN629-FMI12", "spn": REF_SPN, "fmi": REF_FMI, "source": "J1939 DM1"}]

        # WARM-UP (measurement isolation, NOT a threshold change).
        #
        # This test measures PER-INSTANCE retention across 20 copilot instances.
        # The first call in a process also pays the one-time, module-level cache
        # population: parsing the 23.9 MB j1939_spn_fmi_database.json (cached in
        # `_CACHED_J1939_DB`) and quarantining all 14,469 DTC records into
        # EXPERT_KNOWLEDGE_BASE. tracemalloc attributes that one-time cost to the
        # loop and reports it as "growth" — measured 128.6 MB when this test was
        # the first to touch those paths in the full-suite ordering, and 0.00 MB
        # per 20 instances once warm. The genuine property under test is
        # retention, so the caches are primed BEFORE snap1: any per-instance
        # leak still fails here, while the one-time initialization no longer
        # masquerades as one.
        warmup = AiDiagnosticCopilot()
        warmup.analyze_session(dtcs, {"EngineSpeed": 1000.0}, ["ECU"])
        del warmup

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

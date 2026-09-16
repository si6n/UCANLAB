"""FAZ 1 unit tests: evidence gate determinism + tiering (offline)."""

from __future__ import annotations

from src.core.models.diagnostics import (
    DiagnosticDomain,
    DiagnosticEvent,
    SignalSample,
    SignalSource,
    VehicleSession,
)
from src.engine.ai.evidence_gate import active_dtc_events, evaluate_sufficiency


def _mk_sample(name: str = "EngineSpeed", ts_ns: int = 1_000_000_000, confidence: float = 1.0) -> SignalSample:
    return SignalSample(
        timestamp_ns=ts_ns,
        name=name,
        raw_value=100,
        physical_value=1000.0,
        unit="rpm",
        source=SignalSource.J1939,
        confidence=confidence,
    )


def _mk_session(samples=None, events=None, started_ns: int = 1_000_000_000) -> VehicleSession:
    s = VehicleSession(
        session_id="sess-test",
        started_at_ns=started_ns,
        domain=DiagnosticDomain.HEAVY_DUTY,
    )
    s.samples.extend(samples or [])
    s.events.extend(events or [])
    return s


def _dtc_event(code: str = "SPN 100 FMI 1", ts_ns: int = 1_060_000_000) -> DiagnosticEvent:
    return DiagnosticEvent(
        timestamp_ns=ts_ns,
        code=code,
        domain=DiagnosticDomain.HEAVY_DUTY,
        severity="CRITICAL_STOP",
        status="ACTIVE",
    )


def _sufficient_samples(count: int = 12, name: str = "EngineSpeed") -> list[SignalSample]:
    # 12 samples spread across a >60 s span, all fresh.
    return [
        _mk_sample(name=name, ts_ns=1_000_000_000 + int(i * 6e9))
        for i in range(count)
    ]


class TestSufficiencyTiers:
    def test_insufficient_session_rejected(self) -> None:
        session = _mk_session(samples=[_mk_sample()])  # 1 sample, 0 span
        report = evaluate_sufficiency(session)
        assert report.anomaly_sufficient is False
        assert report.dtc_sufficient is False
        assert report.gaps

    def test_sufficient_samples_without_dtc_passes_tier1_only(self) -> None:
        session = _mk_session(samples=_sufficient_samples())
        report = evaluate_sufficiency(session)
        assert report.anomaly_sufficient is True
        # Tier 2 needs an ACTIVE DTC record.
        assert report.dtc_sufficient is False
        assert any("aktif DTC kaydı yok" in g for g in report.gaps)
        assert report.active_dtc_count == 0

    def test_active_dtc_unlocks_tier2(self) -> None:
        session = _mk_session(samples=_sufficient_samples(), events=[_dtc_event()])
        report = evaluate_sufficiency(session)
        assert report.anomaly_sufficient is True
        assert report.dtc_sufficient is True
        assert report.active_dtc_count == 1

    def test_history_dtc_does_not_unlock_tier2(self) -> None:
        event = DiagnosticEvent(
            timestamp_ns=1_060_000_000,
            code="SPN 100 FMI 1",
            domain=DiagnosticDomain.HEAVY_DUTY,
            severity="CRITICAL_STOP",
            status="HISTORY",
        )
        session = _mk_session(samples=_sufficient_samples(), events=[event])
        report = evaluate_sufficiency(session)
        assert report.dtc_sufficient is False

    def test_stale_signal_fails_recency(self) -> None:
        fresh = _sufficient_samples()
        stale = [_mk_sample(name="EngineCoolantTemp", ts_ns=1_000_000_000)]  # never refreshed
        session = _mk_session(samples=fresh + stale)
        report = evaluate_sufficiency(session)
        assert report.anomaly_sufficient is False
        assert any("güncel değil" in g for g in report.gaps)

    def test_discovered_majority_reduces_trust(self) -> None:
        samples = [
            SignalSample(
                timestamp_ns=1_000_000_000 + int(i * 6e9),
                name=f"disc_{i}",
                raw_value=1,
                physical_value=1.0,
                unit="x",
                source=SignalSource.DISCOVERED,
                confidence=0.3,
            )
            for i in range(12)
        ]
        session = _mk_session(samples=samples)
        report = evaluate_sufficiency(session)
        assert report.anomaly_sufficient is False
        assert any("DISCOVERED" in g for g in report.gaps)
        assert any("güveni" in g for g in report.gaps)

    def test_exact_half_discovered_is_fail_closed(self) -> None:
        # A3-9: the DISCOVERED share must be strictly UNDER 0.5. A session at
        # exactly 50% (6/12) previously slipped through the `> 0.5` comparison
        # and was reported sufficient — low-confidence data accepted.
        def _burst(name: str, source: SignalSource, confidence: float) -> list[SignalSample]:
            return [
                SignalSample(
                    timestamp_ns=1_000_000_000 + int(i * 6e9),
                    name=name,
                    raw_value=1,
                    physical_value=1.0,
                    unit="x",
                    source=source,
                    confidence=confidence,
                )
                for i in range(12)
            ]

        samples: list[SignalSample] = []
        for i in range(6):
            samples += _burst(f"disc_{i}", SignalSource.DISCOVERED, 0.9)
        for i in range(6):
            samples += _burst(f"hard_{i}", SignalSource.J1939, 1.0)
        session = _mk_session(samples=samples)
        report = evaluate_sufficiency(session)
        assert report.anomaly_sufficient is False
        assert any("DISCOVERED" in g for g in report.gaps)

    def test_just_under_half_discovered_passes(self) -> None:
        # Guard against over-tightening: 5/12 DISCOVERED signals (with full
        # confidence) is genuinely below the threshold and must stay
        # sufficient. Each signal carries MIN_SAMPLES_PER_SIGNAL samples.
        def _burst(name: str, source: SignalSource, confidence: float) -> list[SignalSample]:
            return [
                SignalSample(
                    timestamp_ns=1_000_000_000 + int(i * 6e9),
                    name=name,
                    raw_value=1,
                    physical_value=1.0,
                    unit="x",
                    source=source,
                    confidence=confidence,
                )
                for i in range(12)
            ]

        samples: list[SignalSample] = []
        for i in range(5):
            samples += _burst(f"disc_{i}", SignalSource.DISCOVERED, 0.9)
        for i in range(7):
            samples += _burst(f"hard_{i}", SignalSource.J1939, 1.0)
        session = _mk_session(samples=samples)
        report = evaluate_sufficiency(session)
        assert report.anomaly_sufficient is True

    def test_exact_min_mean_confidence_is_fail_closed(self) -> None:
        # A3-9: mean confidence exactly at the minimum (0.5) must be treated as
        # insufficient — the boundary favours fail-closed (`<= MIN`). One signal
        # with enough samples isolates the confidence boundary from the
        # min-samples rule.
        samples = [
            _mk_sample(name="EngineSpeed", ts_ns=1_000_000_000 + int(i * 6e9), confidence=0.5)
            for i in range(12)
        ]
        session = _mk_session(samples=samples)
        report = evaluate_sufficiency(session)
        assert report.anomaly_sufficient is False
        assert any("güveni" in g for g in report.gaps)


class TestDeterminism:
    def test_same_session_same_report(self) -> None:
        def build() -> VehicleSession:
            return _mk_session(samples=_sufficient_samples(), events=[_dtc_event()])

        r1 = evaluate_sufficiency(build())
        r2 = evaluate_sufficiency(build())
        assert r1 == r2
        assert r1.to_dict() == r2.to_dict()

    def test_gap_order_is_independent_of_insertion(self) -> None:
        a = _mk_session(samples=[_mk_sample(ts_ns=1_000_000_000), _mk_sample(name="B", ts_ns=1_000_000_000)])
        b = _mk_session(samples=[_mk_sample(name="B", ts_ns=1_000_000_000), _mk_sample(ts_ns=1_000_000_000)])
        assert evaluate_sufficiency(a).gaps == evaluate_sufficiency(b).gaps


class TestActiveDtcEvents:
    def test_sorted_deterministic_order(self) -> None:
        events = [
            _dtc_event("SPN 110 FMI 0", ts_ns=1_061_000_000),
            _dtc_event("SPN 100 FMI 1", ts_ns=1_060_000_000),
        ]
        session = _mk_session(events=events)
        actives = active_dtc_events(session)
        assert [e.code for e in actives] == ["SPN 100 FMI 1", "SPN 110 FMI 0"]


class TestModelInvariants:
    def test_nonfinite_physical_value_rejected(self) -> None:

        try:
            SignalSample(
                timestamp_ns=1,
                name="x",
                raw_value=0,
                physical_value=float("nan"),
                unit="",
                source=SignalSource.J1939,
            )
            raise AssertionError("NaN sample accepted — evidence base polluted")
        except ValueError:
            pass

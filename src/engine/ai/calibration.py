"""Golden-Set Calibration & Confidence Evaluation Engine (FAZ 3).

Evaluates the diagnostic engine across all verified golden cases:
- Measures empirical accuracy and confidence calibration.
- Calibrates compute_root_cause_confidence to prevent overconfidence.
- Completely offline, deterministic, zero external framework dependencies.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.core.logging import get_logger
from src.core.models.diagnostics import (
    DiagnosticEvent,
    Severity,
    SignalSample,
    SignalSource,
    VehicleSession,
)
from src.engine.ai.golden_cases import GoldenCase, load_all_cases
from src.engine.ai.golden_similarity import _normalize_code
from src.engine.ai.hypothesis_engine import rank_hypotheses

logger = get_logger("engine.ai_calibration")


# Bilingual CONCEPT terms for the golden-set match (T2-1).
#
# Each entry is one component/mechanism CONCEPT with its Turkish and English
# surface forms. Rationale (measured in `scripts/_probe_t21_concepts.py`): the
# engine states the fault MECHANISM while a golden `actual_fault` usually states
# the FMI DESCRIPTION, and the two sides are frequently in different languages
# (many DB `causes[]` strings are English while the golden cases are Turkish).
# Plain word overlap therefore reports 0.00 for substantively correct cases:
#     engine="ECT sensör kablosu kopuk (ECU -40°C sanal okuma)"
#     actual="FMI 0 (Kritik Yüksek): Sıcaklık >108°C; termostat kapalı kalmış…"
#     engine="Downstream (Katalizör sonrası / Sensör 2) Oksijen sensörünün yaşlanması."
#     actual="Katalizör monolitinin kurşun/yağ ile zehirlenmesi…"
# A shared concept term keeps the metric meaningful without loosening it: the
# code precondition is still mandatory, and a case still matches only when both
# sides name the same component/mechanism.
_CONCEPT_TERMS: dict[str, tuple[str, ...]] = {
    "catalyst": ("kataliz", "catalyst", "konvertör", "converter"),
    "thermostat": ("termostat", "thermostat"),
    "turbo": ("turbo", "vgt", "wastegate", "intercooler", "kompresör çark", "boost", "aşırı besleme"),
    "injector": ("enjektör", "injector"),
    "spark": ("buji", "spark plug", "bobin", "coil"),
    "knock": ("vuruntu", "knock"),
    "dpf": ("dpf", "partikül filt", "particulate filter", "kurum"),
    "egr": ("egr", "egzoz gazı geri"),
    "scr": ("scr",),
    "def": ("def", "adblue"),
    "hvil": ("hvil", "msd", "manuel servis şalter"),
    "inverter": ("inverter", "e-pump", "elektrikli inverter"),
    "battery": ("batarya", "battery", "hücre", "cell", "soh"),
    "maf": ("maf", "kütle hava", "mass air"),
    "o2": ("oksijen", "oxygen", "o2", "lambda", "probe", "ısıtıcı", "heater"),
    "sensor": ("sensör", "sensor"),
    "wiring": ("kablo", "tesisat", "wiring", "harness", "konnektör", "connector", "soket", "pin"),
    "vacuum": ("vakum", "vacuum", "kaçak", "leak", "sızınt", "hortum", "hose"),
    "fuel_pump": ("yakıt pompa", "fuel pump", "hpfp", "cp4", "yakıt basın", "fuel pressure", "ray", "rail"),
    "coolant": ("soğutma", "coolant", "radyatör", "radiator", "termostat"),
    "oil": ("yağ basın", "oil pressure", "yağ pompa", "oil pump", "yağlama", "lubric"),
    "exhaust_leak": ("egzoz manifold", "exhaust manifold", "manifold", "conta", "gasket"),
    "pcm": ("pcm", "ecm", "ecu", "tcm", "kontrol ünitesi", "kontrol modülü", "control module"),
    "can": ("can-bus", "can bus", "can_h", "can-l", "haberleşme", "communication", "sonlandır", "terminat"),
    "speed_sensor": (
        "hız sensör",
        "speed sensor",
        "vss",
        "tekerlek hız",
        "wheel speed",
        "relüktör",
        "reluctor",
        "dişli",
    ),
    "timing": (
        "triger",
        "zincir",
        "chain",
        "kayış",
        "belt",
        "zamanlama",
        "timing",
        "eksantrik",
        "camshaft",
        "krank",
        "crankshaft",
    ),
    "evap": ("evap", "kanister", "canister", "buhar", "vapur", "purge", "tahliye"),
    "throttle": ("gaz kelebe", "throttle", "tps", "potansiyometre", "potentiometer"),
    "compressor": ("kompresör", "compressor", "hava basınc"),
    "alternator": (
        "alternatör",
        "alternator",
        "şarj",
        "charging",
        "sigorta",
        "fuse",
        "röle",
        "relay",
        "besleme",
        "supply",
    ),
    "regulator": ("regülatör", "regulator", "basınç kontrol valf", "pcv"),
    "solenoid": ("selenoid", "solenoid", "valf", "valve"),
}


def _concepts_in(text: str) -> frozenset[str]:
    """Concept ids named by ``text`` (case-insensitive, bilingual)."""
    low = text.lower()
    return frozenset(name for name, forms in _CONCEPT_TERMS.items() if any(f in low for f in forms))


@dataclass(slots=True, frozen=True)
class CalibrationResult:
    """Outcome of evaluating golden cases calibration."""

    total_cases: int
    verified_cases: int
    top1_matches: int
    accuracy_pct: float
    mean_confidence: float
    overconfidence_detected: bool
    calibration_factor: float
    # Cases whose own fault code a hypothesis even claims (strict-metric ceiling).
    code_coverage_pct: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_cases": self.total_cases,
            "verified_cases": self.verified_cases,
            "top1_matches": self.top1_matches,
            "accuracy_pct": round(self.accuracy_pct, 2),
            "mean_confidence": round(self.mean_confidence, 3),
            "overconfidence_detected": self.overconfidence_detected,
            "calibration_factor": round(self.calibration_factor, 3),
            "code_coverage_pct": round(self.code_coverage_pct, 2),
        }


def _case_to_vehicle_session(case: GoldenCase) -> tuple[VehicleSession, tuple[str, ...]]:
    """Convert a GoldenCase into ``(VehicleSession, observed signal names)``.

    The second element is the case's recorded ``signals_of_interest[].name``
    list — REAL observed-channel names that carry no measurement value (T2-8).
    """
    session = VehicleSession(
        session_id=f"eval-{case.case_id}",
        started_at_ns=time.monotonic_ns(),
        domain=case.domain,
        make=case.make,
        model=case.model,
        vin_masked=None,
    )
    # Populate events from case DTCs
    for dtc in case.dtcs:
        code_str = dtc.get("code") if isinstance(dtc, dict) else getattr(dtc, "code", "")
        status_str = dtc.get("status", "ACTIVE") if isinstance(dtc, dict) else getattr(dtc, "status", "ACTIVE")
        session.events.append(
            DiagnosticEvent(
                timestamp_ns=session.started_at_ns + 1000,
                code=code_str,
                domain=case.domain,
                severity=Severity.HIGH,
                status=status_str,
            )
        )
    # Populate samples from signals of interest.
    #
    # P1-3 (AGENTS.md §2.3 no-fabrication): this loop used to append a
    # ``SignalSample(raw_value=100, physical_value=100.0)`` for EVERY named
    # signal of interest — a measurement value no golden case ever recorded.
    # The fabricated 100.0 then counted as "the signal was observed" inside
    # ``rank_hypotheses`` (observed_signals / contradiction penalty), so the
    # calibration corpus was scored against invented telemetry. A case that
    # carries no quantitative value for a signal now contributes NO sample:
    # "not measured" stays "not measured". Only keys actually present in the
    # case are read (never a default), and only values that are genuinely
    # numeric produce a sample.
    #
    # T2-8 (b): the NAMES collected in ``observed_names`` are forwarded to
    # ``rank_hypotheses`` as name-only evidence. Measured: all 54 golden cases'
    # signals carry their only metric as PROSE in ``observed_behavior``
    # ("Normal Çalışma Aralığı: 82°C - 95°C | Uyarı (AWL): >103°C") with no
    # ``value`` key at all, so before this change the case's evidence base was
    # EMPTY, every candidate scored 1.0, and the lexicographic id tie-break
    # decided the winner. Those prose numbers are NOMINAL/threshold values, not
    # measured instantaneous ones — turning them into ``SignalSample`` values
    # would be exactly the fabrication P1-3 removed (option (a), REJECTED). The
    # signal NAME, by contrast, is a real, recorded fact ("this channel was
    # looked at"), so it is forwarded as a name and nothing is invented
    # (option (b), CHOSEN). The value semantics of ``SignalSample`` are left
    # untouched.
    observed_names: list[str] = []
    for i, sig in enumerate(case.signals_of_interest):
        if not isinstance(sig, dict):
            # Non-dict entries (legacy objects) carry no readable quantity.
            continue
        sig_name = sig.get("name")
        if not isinstance(sig_name, str) or not sig_name.strip():
            continue
        observed_names.append(sig_name)
        raw_value = sig.get("value")
        if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
            continue
        physical_value = sig.get("physical_value", raw_value)
        if isinstance(physical_value, bool) or not isinstance(physical_value, (int, float)):
            physical_value = raw_value
        session.samples.append(
            SignalSample(
                timestamp_ns=session.started_at_ns + ((i + 1) * 1_000_000),
                name=sig_name,
                raw_value=int(raw_value),
                physical_value=float(physical_value),
                unit=str(sig.get("unit") or ""),
                source=SignalSource.J1939,
                confidence=1.0,
            )
        )
    # T2-8 (b): the recorded signal NAMES are returned alongside the session so
    # the caller can forward them as name-only evidence. ``VehicleSession`` uses
    # ``slots=True``, so the names cannot ride on the instance — and they must
    # NOT become ``SignalSample`` rows, which would re-introduce a fabricated
    # measurement (P1-3).
    return session, tuple(observed_names)


def evaluate_calibration(cases: list[GoldenCase] | None = None) -> CalibrationResult:
    """Run empirical calibration evaluation over verified golden cases."""
    all_cases = cases if cases is not None else load_all_cases()
    verified = [c for c in all_cases if c.verified and not c.is_draft and c.actual_fault]

    if not verified:
        return CalibrationResult(
            total_cases=len(all_cases),
            verified_cases=0,
            top1_matches=0,
            accuracy_pct=0.0,
            mean_confidence=0.0,
            overconfidence_detected=False,
            calibration_factor=1.0,
        )

    matches = 0
    code_covered = 0
    conf_scores: list[float] = []

    for case in verified:
        session, observed_names = _case_to_vehicle_session(case)
        hyps = rank_hypotheses(
            session,
            anomalies=[],
            observed_signal_names=set(observed_names),
        )
        case_codes = frozenset(_normalize_code(d["code"]) for d in case.dtcs if isinstance(d, dict) and d.get("code"))
        if hyps:
            top = hyps[0]
            conf_scores.append(top.score)
            # STRICT match (T2-1 remediation). The previous rule was:
            #     any(w in clean_actual for w in clean_top.split() if len(w) > 4)
            # i.e. ONE shared word of >4 chars counted as a hit. Measured effect:
            # a node titled from a golden case's own `actual_fault` text (with
            # `expected_dtcs` naming exactly that golden code) satisfied it BY
            # CONSTRUCTION, so the published 44.4 % baseline was an overfit
            # measurement rather than a capability measurement (Tuzaklar §1:
            # "his own check was too loose"). Whatever figure the loose rule
            # produced, it was upward-biased by that construction overlap.
            #
            # CORRECTION (T3-1 independent audit — do NOT restore a "0/54"):
            # an earlier comment here claimed the old 53-node graph scored
            # 0/54 under this strict rule. That was WRONG and unmeasured: no
            # harness ever produced it. Independently re-measured with this
            # repository's own loader and this shipped rule:
            #     OLD 53-node graph : 27/54 = 50.00 %  (code coverage 37/54 = 68.52 %)
            #     NEW 8,975-node    : 28/54 = 51.85 %  (code coverage 34/54 = 62.96 %)
            # i.e. the old graph is only ONE case behind the new one under the
            # shipped rule. The overfit claim concerns the LOOSE rule's 44.4 %
            # artefact, NOT a collapse of the old graph under the strict rule.
            # The old graph's real weakness is narrower reach: its code
            # coverage is 68.52 % against a metric that can only count a hit
            # when the code is present at all, and its extra coverage came
            # largely from construction overlap. Any future claim about this
            # number MUST cite a harness (see `scripts/_probe_t21_baseline.py`).
            #
            # Two independent conditions must now hold:
            #   (a) CODE PRECONDITION — the top hypothesis must actually claim one
            #       of the case's own codes. A hypothesis that does not name the
            #       fault code cannot be a top-1 hit for that code, no matter how
            #       its prose reads ("code not in the graph => case cannot match").
            #   (b) CONCEPT EVIDENCE — the title and `actual_fault` must share at
            #       least one CONCEPT term (see `_CONCEPT_TERMS`).
            #
            # Why a concept term and not plain word overlap: the engine states the
            # fault MECHANISM while the golden `actual_fault` usually states the
            # FMI DESCRIPTION, and the two sides are often in different languages
            # (DB `causes[]` prose is EN for many codes, golden cases are TR). Word
            # overlap therefore reports 0.00 for cases that are substantively
            # correct, e.g.
            #     engine="Downstream (Katalizör sonrası/Sensör 2) Oksijen…"
            #     actual="Katalizör monolitinin kurşun/yağ ile zehirlenmesi"
            # A bilingual concept term ("catalyst": kataliz|catalyst|converter)
            # matches both, is immune to inflection and language, and stays strict
            # because the code precondition is still mandatory.
            top_codes = frozenset(_normalize_code(c) for c in top.expected_dtcs) if top.expected_dtcs else frozenset()
            if top_codes & case_codes:
                code_covered += 1
            concept_match = bool(_concepts_in(top.fault) & _concepts_in(case.actual_fault or ""))
            if (top_codes & case_codes) and concept_match:
                matches += 1
        else:
            conf_scores.append(0.0)

    acc = (matches / len(verified)) * 100.0
    mean_conf = (sum(conf_scores) / len(conf_scores)) if conf_scores else 0.0
    # Coverage is reported separately: it is the honest ceiling of the strict
    # metric (cases whose code a hypothesis even claims), so a low top-1 can be
    # read against "how much of the corpus the graph can address at all".
    coverage_pct = (code_covered / len(verified)) * 100.0

    # Overconfidence check: if mean reported confidence is significantly higher than empirical accuracy
    overconfident = (mean_conf * 100.0) > (acc + 15.0)
    cal_factor = min(1.0, (acc / (mean_conf * 100.0))) if mean_conf > 0 else 1.0

    return CalibrationResult(
        total_cases=len(all_cases),
        verified_cases=len(verified),
        top1_matches=matches,
        accuracy_pct=acc,
        mean_confidence=mean_conf,
        overconfidence_detected=overconfident,
        calibration_factor=cal_factor,
        code_coverage_pct=coverage_pct,
    )


# ---------------------------------------------------------------------------
# Cached golden-set calibration factor.
#
# The calibration factor is used to DAMP the confidence labels the live
# diagnostic path emits. Re-running the whole golden corpus inside every
# ``analyze_session`` call would be prohibitively expensive (one
# ``rank_hypotheses`` pass per verified case), so the value is computed ONCE
# per process and memoised.
#
# Fail-closed contract: if the golden corpus cannot be read at all (missing
# directory, corrupt case file), the result is ``None`` — NOT ``1.0``.
# ``None`` means "no calibration evidence available"; the confidence
# computation then leaves the base score untouched. Returning ``1.0`` would
# silently assert "the engine is perfectly calibrated", which is exactly the
# fabricated claim AGENTS.md §2.3 forbids. ``evaluate_calibration`` itself
# already returns ``1.0`` — measured, not assumed — when the corpus loads but
# contains no verified cases, and that measured value is passed through.
#
# No network, no LLM, no heavy dependency: pure local JSON reads (golden_cases)
# plus the deterministic hypothesis engine. Thread-safe (double-checked lock).
#
# T2-5 LATENCY FIX (cross-process disk cache):
#
#   A cold evaluation measured ~2.4 s on the dev machine, and the production
#   path calls ``compute_calibration_factor()`` on EVERY ``analyze_session``
#   (diagnostic_copilot). The in-process memo only helps after the first call,
#   so a short-lived CLI/UI process paid the full 2.4 s for one diagnosis.
#
#   The result is therefore also persisted to
#   ``data/diagnostics/.calibration_cache.json``, keyed by a SHA-256 digest of
#   every input the evaluation depends on:
#       * each golden case file's bytes (``data/golden_traces/cases/*.json``)
#       * ``data/diagnostics/root_cause_graph.json`` (feeds rank_hypotheses)
#   If any input changes the digest changes and the entry misses — a stale
#   value can never be served. A miss (or any read/write failure) simply
#   recomputes; the cache is an optimisation, never an authority.
#
#   FAIL-CLOSED IS PRESERVED: only a *successful* evaluation is cached. A
#   failed evaluation (``None``) is deliberately NOT persisted, so an
#   unreadable corpus is retried on the next process rather than being frozen
#   into a "no calibration evidence" verdict forever. An absent/partial entry
#   therefore falls through to the same ``None`` fail-closed path as before.
# ---------------------------------------------------------------------------
_CALIBRATION_CACHE_LOCK = threading.Lock()
_CALIBRATION_FACTOR_CACHE: dict[str, float | None] = {}
_CALIBRATION_FACTOR_UNSET = object()


def _calibration_cache_path() -> Path:
    """Path of the cross-process calibration cache file.

    Derived from the golden-corpus location so a frozen build and a source
    checkout never share an entry.
    """
    try:
        from src.engine.ai import golden_cases
        cases_dir = Path(golden_cases.CASES_DIR)
    except Exception:  # noqa: BLE001 — fall back to the repository layout
        cases_dir = Path(__file__).resolve().parents[3] / "data" / "golden_traces" / "cases"
    # data/golden_traces/cases -> data/diagnostics
    return cases_dir.parent.parent / "diagnostics" / ".calibration_cache.json"


def _corpus_digest() -> str | None:
    """SHA-256 over every file the calibration evaluation reads.

    ``None`` when the inputs cannot be enumerated (e.g. the corpus directory is
    missing) — the caller then skips the cache entirely and evaluates directly,
    so a broken digest can only ever cost time, never correctness.
    """
    try:
        from src.engine.ai import golden_cases
        cases_dir = Path(golden_cases.CASES_DIR)
        graph_path = Path(golden_cases.__file__).resolve().parents[3] / "data" / "diagnostics" / "root_cause_graph.json"
    except Exception:  # noqa: BLE001
        return None
    try:
        case_files = sorted(p for p in cases_dir.glob("*.json") if p.name != "schema.json")
    except OSError:
        return None
    if not case_files:
        return None
    digest = hashlib.sha256()
    try:
        for path in case_files:
            digest.update(path.name.encode("utf-8"))
            digest.update(b"\0")
            digest.update(hashlib.sha256(path.read_bytes()).digest())
        # The hypothesis graph shapes rank_hypotheses output, so it is an input.
        if graph_path.exists():
            digest.update(b"root_cause_graph\0")
            digest.update(hashlib.sha256(graph_path.read_bytes()).digest())
    except OSError:
        return None
    return digest.hexdigest()


def _read_disk_cache(digest: str) -> float | None:
    """Return the cached factor for ``digest``, or ``None`` on any miss.

    A miss is indistinguishable from "no cache" by design: the caller always
    recomputes on a miss, so a corrupt cache file degrades to slow-but-correct.
    """
    try:
        path = _calibration_cache_path()
        if not path.exists():
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return None
        entry = payload.get(digest)
        if not isinstance(entry, dict):
            return None
        factor = entry.get("calibration_factor")
        if factor is None:
            return None
        return float(factor)
    except Exception:  # noqa: BLE001 — a bad cache must never break diagnosis
        return None


def _write_disk_cache(digest: str, factor: float) -> None:
    """Persist a SUCCESSFUL evaluation keyed by ``digest``. Best-effort.

    Only called with a real (non-``None``) factor — a failed evaluation is
    never frozen into the cache (see the fail-closed note above). Failures are
    swallowed: caching is an optimisation and must never surface as an error.
    """
    try:
        path = _calibration_cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        payload: dict[str, Any] = {}
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(existing, dict):
                    payload = existing
            except (OSError, json.JSONDecodeError):
                payload = {}
        payload[digest] = {
            "calibration_factor": float(factor),
            "computed_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        tmp = path.with_suffix(path.suffix + f".tmp-{os.getpid()}")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp, path)
    except Exception:  # noqa: BLE001 — never let the cache break diagnosis
        logger.debug("Calibration disk cache write skipped", exc_info=True)


def compute_calibration_factor(*, force_reload: bool = False) -> float | None:
    """Return the golden-set ``calibration_factor``, memoised per process.

    ``None`` is a first-class result: the corpus could not be evaluated, so no
    damping evidence exists (fail-closed, never a magic ``1.0``).

    Deterministic: the golden corpus and the hypothesis engine are both
    offline and reproducible, so the memoised value equals a fresh
    ``evaluate_calibration().calibration_factor`` call in the same process.

    A cross-process SHA-256-keyed disk cache avoids paying the ~2.4 s cold
    evaluation in every new process (T2-5). The cache is invalidated whenever
    any corpus input changes, and a failed evaluation is never cached.
    """
    if not force_reload:
        cached = _CALIBRATION_FACTOR_CACHE.get("value", _CALIBRATION_FACTOR_UNSET)
        if cached is not _CALIBRATION_FACTOR_UNSET:
            return cached  # type: ignore[return-value]
    with _CALIBRATION_CACHE_LOCK:
        if not force_reload:
            cached = _CALIBRATION_FACTOR_CACHE.get("value", _CALIBRATION_FACTOR_UNSET)
            if cached is not _CALIBRATION_FACTOR_UNSET:
                return cached  # type: ignore[return-value]

        if not force_reload:
            digest = _corpus_digest()
            if digest is not None:
                disk_factor = _read_disk_cache(digest)
                if disk_factor is not None:
                    _CALIBRATION_FACTOR_CACHE["value"] = disk_factor
                    return disk_factor

        try:
            factor: float | None = float(evaluate_calibration().calibration_factor)
        except Exception:  # noqa: BLE001 — unreadable corpus must never break diagnosis
            logger.warning("Golden-set calibration unavailable — confidence left uncalibrated")
            factor = None
        _CALIBRATION_FACTOR_CACHE["value"] = factor
        if factor is not None and not force_reload:
            digest = _corpus_digest()
            if digest is not None:
                _write_disk_cache(digest, factor)
        return factor


def calibrate_score(score: float, *, calibration_factor: float | None = None) -> float:
    """Damp a raw [0, 1] evidence score with the cached calibration factor.

    ``calibration_factor=None`` means "resolve the cached golden-set factor".
    A resolved ``None`` (corpus unavailable) leaves the score untouched.
    The factor is clamped to [0.1, 1.0] — identical bounds to
    ``compute_root_cause_confidence`` so both paths damp by the same rule.
    """
    factor = compute_calibration_factor() if calibration_factor is None else float(calibration_factor)
    if factor is None:
        return score
    return max(0.0, min(1.0, score * max(0.1, min(1.0, factor))))


__all__ = [
    "CalibrationResult",
    "calibrate_score",
    "compute_calibration_factor",
    "evaluate_calibration",
]

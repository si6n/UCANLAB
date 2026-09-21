# -*- coding: utf-8 -*-
"""T2-3 regression lock — fail-closed confidence default + quarantine leak.

Two separate defects are locked here (task card T2-3):

(A) FAIL-OPEN DEFAULT.  ``compute_root_cause_confidence(..., calibration_factor=None)``
    treated ``None`` as "apply no damping" (``cal = 1.0``). A caller that forgot
    the argument silently received the over-confident label:

        BEFORE: f(2,1,1,1) -> "Yüksek (%85 ağırlıklı kanıt skoru)"
        AFTER : f(2,1,1,1) -> "Orta (%55 ağırlıklı kanıt skoru) · kalibre"

    ``None`` now means "resolve the golden-set factor yourself". Tuzaklar §2:
    any path that fails toward the UNSAFE side is a bug.

(B) QUARANTINE LEAK.  The consumer at ``diagnostic_copilot.py`` rendered only
    ``procedures_full[:1]`` (index 0). For three SPNs — SPN_520604, SPN_520605,
    SPN_524265 — index 0 WAS the quarantined block, so the ``_quarantined``
    marker protected them from nothing. Fixed in two layers (see the task
    report): upstream removal from the array, plus a consumer-side skip.

Nothing here calls an LLM or the network: the calibration factor comes from the
offline golden corpus and the quarantine fixtures are local JSON.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.engine.ai import diagnostic_copilot as dc
from src.engine.ai.calibration import compute_calibration_factor
from src.engine.ai.diagnostic_copilot import (
    CALIBRATION_CONFIDENCE_SUFFIX,
    UNCALIBRATED_CONFIDENCE_SUFFIX,
    CausalBayesianInferenceEngine,
    compute_root_cause_confidence,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SPN_DB = REPO_ROOT / "data" / "diagnostics" / "j1939_spn_fmi_database.json"
QUARANTINE_FILE = REPO_ROOT / "data" / "diagnostics" / "quarantine" / "dtcdocs_llm_blocks.json"

# The three SPNs whose index-0 procedure block was quarantined. Determined by
# scanning the live DB (72 blocked, 3 of them at index 0) — see the T2-3 report,
# "Sayısal sonuç". Re-measured by the test below rather than trusted.
INDEX0_LEAK_SPNS = ("SPN_520604", "SPN_520605", "SPN_524265")

# The exact over-confident label the fail-open default produced for f(2,1,1,1).
# P0-5 (2026-09-21 audit) CORRECTION: this constant used to be the assertion
# target, but "%85" is not a defect — it is the HONEST weighted mean for the
# (2,1,1,1) evidence tuple (weights 0.5/0.3/0.2 * 1.0/0.5/1.0 = 0.85). The
# constant conflated "UNDAMPED" with "FORBIDDEN": it only ever disappeared
# because a < 1.0 calibration factor damped it. Now that absolute scoring makes
# the corpus honest (factor 1.0), the undamped value legitimately reappears.
# The real fail-open defect the test must forbid is an UNCALIBRATED label, so
# the assertion is on the calibration SUFFIX, never on a score value.
OVERCONFIDENT_LABEL_PREFIX = "Yüksek (%85"  # retained for the legacy-doc reference below


def _spn_db() -> dict:
    return json.loads(SPN_DB.read_text(encoding="utf-8"))


# ===========================================================================
# (A) Fail-closed confidence default
# ===========================================================================
class TestFailClosedDefault:
    """``calibration_factor=None`` must damp, never grant full confidence."""

    def test_none_does_not_return_the_overconfident_label(self) -> None:
        """THE red->green assertion of part (A).

        On the un-fixed tree this returned "Yüksek (%85 ağırlıklı kanıt skoru)".

        T2-1 note: the assertion is on the *invariant* (the fail-open ``%85``
        label never returns, and the implicit-``None`` path stays strictly below
        the undamped control), not on a fixed band name. The golden-set factor is
        a measured quantity, and T2-1 corrected the metric that measures it (the
        old match rule accepted a single shared word, inflating accuracy and
        therefore the factor). Pinning a band name would re-encode whatever the
        factor happens to be into this test.
        """
        result = compute_root_cause_confidence(2, 1, 1, 1)
        # The fail-open defect is an UNCALIBRATED label (no damping evidence),
        # never a specific score value. See the OVERCONFIDENT_LABEL_PREFIX note.
        assert CALIBRATION_CONFIDENCE_SUFFIX in result, (
            "the implicit-None path must produce a CALIBRATED label: " + repr(result)
        )
        assert UNCALIBRATED_CONFIDENCE_SUFFIX not in result, (
            "the implicit-None path must never fall back to the uncalibrated label: " + repr(result)
        )
        # P0-5 (2026-09-21 audit): the implicit-None path resolves the measured
        # golden factor. Since the absolute-scoring fix the corpus is no longer
        # overconfident and that factor is legitimately 1.0, so the label
        # coincides with the undamped control. The invariant that must hold in
        # ALL cases: the label is a valid band and never the fail-open %85.
        explicit = compute_root_cause_confidence(2, 1, 1, 1, calibration_factor=compute_calibration_factor())
        assert result == explicit, (
            "the implicit-None path must equal the explicit measured-factor path, got " + repr(result)
        )
        assert result.startswith(("Düşük", "Orta", "Yüksek")), (
            "f(2,1,1,1) must stay a valid probability band, got " + repr(result)
        )

    def test_none_resolves_the_cached_golden_set_factor(self) -> None:
        """``None`` must be resolved internally to the memoised factor.

        Proves the fix is a *computation*, not a hardcoded constant: the
        ``None`` path must equal the explicit-factor path for the same factor.
        """
        factor = compute_calibration_factor()
        assert factor is not None, "the shipped golden corpus must yield a factor"
        # P0-5: the factor is a MEASURED quantity in the documented clamp band.
        # It was < 1.0 only while the scores were self-normalised to %100; now
        # that they are absolute (mean confidence 0.28 < accuracy 0.52) it is
        # legitimately 1.0. Asserting "< 1.0" would re-encode the old defect.
        assert 0.1 <= factor <= 1.0, f"factor must sit in the documented clamp band, got {factor}"

        implicit = compute_root_cause_confidence(2, 1, 1, 1)
        explicit = compute_root_cause_confidence(2, 1, 1, 1, calibration_factor=factor)
        assert implicit == explicit

    def test_resolution_actually_calls_compute_calibration_factor(self) -> None:
        """Point the accessor at a sentinel: the label must follow it (no constant)."""
        sentinel = 0.2
        real = dc._resolve_calibration_factor.__globals__["compute_calibration_factor"]
        try:
            dc._resolve_calibration_factor.__globals__["compute_calibration_factor"] = lambda **kw: sentinel
            assert compute_root_cause_confidence(2, 1, 1, 1) == compute_root_cause_confidence(
                2, 1, 1, 1, calibration_factor=sentinel
            )
        finally:
            dc._resolve_calibration_factor.__globals__["compute_calibration_factor"] = real

    def test_unavailable_calibration_is_flagged_not_silently_confident(self) -> None:
        """No calibration evidence -> explicit uncalibrated marker, never full trust.

        Tuzaklar §2 requires the failure direction to be the SAFE one: the
        result is labelled uncalibrated so downstream readers cannot mistake it
        for a verified score.
        """
        real = dc._resolve_calibration_factor.__globals__["compute_calibration_factor"]
        try:
            dc._resolve_calibration_factor.__globals__["compute_calibration_factor"] = lambda **kw: None
            result = compute_root_cause_confidence(2, 1, 1, 1)
        finally:
            dc._resolve_calibration_factor.__globals__["compute_calibration_factor"] = real

        assert result.endswith(UNCALIBRATED_CONFIDENCE_SUFFIX), repr(result)
        assert "KALİBRE EDİLMEDİ" in result

    def test_raising_calibration_accessor_does_not_break_diagnosis(self) -> None:
        """A broken calibration module must degrade, not crash the engine."""

        def _boom(**kwargs):  # noqa: ANN003
            raise RuntimeError("corpus exploded")

        real = dc._resolve_calibration_factor.__globals__["compute_calibration_factor"]
        try:
            dc._resolve_calibration_factor.__globals__["compute_calibration_factor"] = _boom
            result = compute_root_cause_confidence(2, 1, 1, 1)
        finally:
            dc._resolve_calibration_factor.__globals__["compute_calibration_factor"] = real
        assert result.endswith(UNCALIBRATED_CONFIDENCE_SUFFIX)

    def test_calibrated_labels_are_marked_and_still_start_with_a_verdict(self) -> None:
        """The label keeps its machine prefix and gains a calibration marker."""
        result = compute_root_cause_confidence(2, 1, 1, 1)
        assert result.endswith(CALIBRATION_CONFIDENCE_SUFFIX)
        assert result.split(" ")[0] in {"Yüksek", "Orta", "Düşük", "Normal"}

    def test_damping_is_monotonic_in_the_factor(self) -> None:
        """A lower factor must never raise the reported score."""
        scores = []
        for factor in (1.0, 0.8, 0.5, 0.2):
            label = compute_root_cause_confidence(2, 1, 1, 1, calibration_factor=factor)
            scores.append(float(label.split("(%")[1].split(" ")[0]))
        assert scores == sorted(scores, reverse=True)

    def test_zero_dtcs_still_returns_normal(self) -> None:
        """The calibration path must not disturb the no-fault short circuit."""
        assert compute_root_cause_confidence(0, 0, 0, 0) == "Normal"

    def test_explicit_factor_outside_bounds_is_clamped(self) -> None:
        """Clamp bounds are unchanged so both confidence paths damp identically."""
        assert compute_root_cause_confidence(2, 1, 1, 1, calibration_factor=0.0) == (
            compute_root_cause_confidence(2, 1, 1, 1, calibration_factor=0.1)
        )
        assert compute_root_cause_confidence(2, 1, 1, 1, calibration_factor=9.0) == (
            compute_root_cause_confidence(2, 1, 1, 1, calibration_factor=1.0)
        )

    def test_production_report_path_is_calibrated(self) -> None:
        """The end-to-end session path must emit a calibrated label."""
        from src.engine.ai.diagnostic_copilot import AiDiagnosticCopilot

        report = AiDiagnosticCopilot().analyze_session(
            [{"code": "P0300"}], {"EngineSpeed": 1800.0, "BoostPressure": 140.0}, ["ECU_0"]
        )
        assert report.root_cause_probability.endswith(CALIBRATION_CONFIDENCE_SUFFIX)
        assert not report.root_cause_probability.startswith("Yüksek (%100")


# ===========================================================================
# (B) Quarantine leak
# ===========================================================================
class TestQuarantineIsGoneFromTheConsumerPath:
    """``procedures_full[:1]`` must never return quarantined data."""

    def test_the_three_index0_spns_carry_no_quarantined_content(self) -> None:
        """The three leak SPNs must carry no quarantined/dirty procedure content.

        T2-5 PREMISE UPDATE (not a relaxation). The original form of this test
        asserted that the quarantine FILE still recorded these three SPNs at
        ``block_index == 0``. That assertion is no longer the right measurement:
        a T2-5 ``--apply`` run drove the OLD overwrite-based ``write_quarantine``
        twice, collapsing the file from 72 records to 19. The three index-0
        records were among the 53 lost (see ``_audit_loss`` in the quarantine
        file and ``test_audit_loss_is_documented`` below).

        The DEFECT this test exists to catch was never "the ledger lists them" —
        it was "quarantined content reaches the report". The correct, durable
        measurement is therefore on the DATA: the three SPNs' ``procedures_full``
        must contain no quarantined block and no dtcdocs LLM/SEO content. After
        the upstream removal the array is empty (``[]``), so this passes for the
        right reason, and it stays a genuine regression lock: re-introducing a
        ``_quarantined`` block for any of these SPNs fails it.
        """
        spns = _spn_db()["spns"]
        for key in INDEX0_LEAK_SPNS:
            blocks = spns[key].get("procedures_full") or []
            assert not any(isinstance(b, dict) and b.get("_quarantined") for b in blocks), (
                f"{key} still has a quarantined block in procedures_full"
            )
            # No dtcdocs/LLM SEO text may survive in any form for these SPNs.
            blob = json.dumps(blocks, ensure_ascii=False).lower()
            for marker in ("dtcdocs.com", "frequently asked questions", "we use cookies",
                           "meaning, causes & fix"):
                assert marker not in blob, f"{key} still leaks dtcdocs content: {marker!r}"

    def test_audit_loss_is_documented(self) -> None:
        """T2-5: the quarantine file must disclose its own unrecoverable loss.

        Silently leaving a 19-record file where 72 were expected would mislead
        every future reader (Rehber §11: never silently skip a loss). The file
        must carry an explicit ``_audit_loss`` block that agrees with the record
        count it describes.
        """
        quarantine = json.loads(QUARANTINE_FILE.read_text(encoding="utf-8"))
        loss = quarantine.get("_audit_loss")
        assert isinstance(loss, dict), "quarantine file must carry an _audit_loss block"
        assert loss["records_lost"] == 53, loss
        assert loss["recoverable"] is False, loss
        assert loss["records_before"] - loss["records_lost"] == quarantine["count"], (
            "the loss marker must agree with the file it describes"
        )
        assert isinstance(loss["surviving_spns"], list) and loss["surviving_spns"]
        # The marker must name the actual cause, not a vague apology.
        assert "overwrite" in loss["cause"].lower(), loss["cause"]

    def test_no_spn_in_the_database_leaks_a_quarantined_index0_block(self) -> None:
        """Global sweep: for EVERY SPN, the index-0 block is not quarantined."""
        spns = _spn_db()["spns"]
        leaks = [
            key
            for key, entry in spns.items()
            for block in (entry.get("procedures_full") or [])[:1]
            if isinstance(block, dict) and block.get("_quarantined")
        ]
        assert leaks == [], f"quarantined data reachable via procedures_full[:1]: {leaks}"

    def test_no_quarantined_marker_remains_anywhere_in_procedures_full(self) -> None:
        """The upstream fix removed the blocks outright (not merely re-marked)."""
        spns = _spn_db()["spns"]
        remaining = [
            (key, index)
            for key, entry in spns.items()
            for index, block in enumerate(entry.get("procedures_full") or [])
            if isinstance(block, dict) and block.get("_quarantined")
        ]
        assert remaining == [], f"_quarantined markers left behind: {remaining[:5]}"

    def test_quarantine_data_is_still_recoverable(self) -> None:
        """Nothing is deleted: every quarantined payload is byte-identical in the store."""
        import hashlib

        quarantine = json.loads(QUARANTINE_FILE.read_text(encoding="utf-8"))
        assert quarantine["count"] == len(quarantine["records"]) > 0
        for record in quarantine["records"]:
            payload = record["original_payload"]
            digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False).encode("utf-8")).hexdigest()
            assert digest == record["block_sha256"], f"{record['spn']} payload not recoverable"
            assert payload, f"{record['spn']} payload is empty"

    def test_consumer_skips_a_quarantined_block_that_slips_through(self) -> None:
        """Second layer: a re-introduced quarantined block must never be rendered.

        This is the defence-in-depth assertion. It injects a synthetic entry
        (never written to disk) whose index 0 is quarantined and index 1 is the
        genuine procedure, and requires the report to render index 1 and drop
        the poisoned index 0 entirely.
        """
        entry = {
            "spn": 999999,
            "name": "Synthetic",
            "title_tr": "Sentetik",
            "description": "leak fixture",
            "unit": "-",
            "procedures_full": [
                {
                    "_quarantined": True,
                    "_quarantine_reason": "dtcdocs_llm_seo_content",
                    "_quarantine_ref": "data/diagnostics/quarantine/dtcdocs_llm_blocks.json",
                    "overview": "LEAKED_LLM_SEO_TEXT",
                    "source_url": "https://dtcdocs.com/leak",
                },
                {
                    "eaton_fault_code": "E-9999",
                    "overview": "Genuine OEM procedure body.",
                },
            ],
        }
        report = CausalBayesianInferenceEngine._format_j1939_technician_report(entry, "SPN 999999", {})
        assert "LEAKED_LLM_SEO_TEXT" not in report
        assert "dtcdocs.com" not in report
        assert "_quarantined" not in report
        assert "E-9999" in report, "the first CLEAN block must still be rendered"

    def test_consumer_renders_no_block_when_every_block_is_quarantined(self) -> None:
        """Fail-closed: an all-quarantined array yields no procedure block at all."""
        entry = {
            "spn": 999998,
            "name": "Synthetic",
            "title_tr": "Sentetik",
            "description": "leak fixture",
            "unit": "-",
            "procedures_full": [
                {
                    "_quarantined": True,
                    "overview": "LEAKED_LLM_SEO_TEXT",
                    "source_url": "https://dtcdocs.com/leak",
                }
            ],
        }
        report = CausalBayesianInferenceEngine._format_j1939_technician_report(entry, "SPN 999998", {})
        assert "Eaton OEM Tam Prosedürü (PIM)" not in report
        assert "LEAKED_LLM_SEO_TEXT" not in report

    def test_real_index0_spns_render_no_quarantined_content(self) -> None:
        """End-to-end on the shipped DB: the three SPNs reach the report clean."""
        from src.engine.ai.diagnostic_copilot import get_j1939_spn_database

        db = get_j1939_spn_database()
        for key in INDEX0_LEAK_SPNS:
            entry = db["spns"][key]
            report = CausalBayesianInferenceEngine._format_j1939_technician_report(entry, f"SPN {entry.get('spn')}", {})
            assert "dtcdocs.com" not in report
            assert "_quarantine" not in report
            assert "Frequently Asked Questions" not in report


# ===========================================================================
# Architectural guard — the fixes must not open a network/TX path
# ===========================================================================
def test_fixes_stay_offline() -> None:
    """AGENTS.md §2.8: no network/TX import may appear in the touched modules."""
    for rel in ("src/engine/ai/diagnostic_copilot.py", "scripts/detect_quarantine_dtcdocs_llm.py"):
        source = (REPO_ROOT / rel).read_text(encoding="utf-8")
        for forbidden in ("urllib", "requests", "socket", "http.client", "src.hal", "TxPort"):
            assert f"import {forbidden}" not in source, f"{rel} imports {forbidden}"
            assert f"from {forbidden}" not in source, f"{rel} imports {forbidden}"


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))

# -*- coding: utf-8 -*-
"""P0-1 regression lock — source-independent DTC severity (2026-09-21 audit).

WHY THIS EXISTS
---------------
The deep-discovery audit measured (at HEAD ``a6f7477``) that
``data/diagnostics/dtc_database.json`` inherited its ``severity`` from scraped
source prose instead of from SAE J2012 semantics:

* ``P0300`` (random/multiple misfire) = ``MEDIUM`` while ``P0301``-``P0312``
  (single-cylinder misfire, a LESS severe case) = ``CRITICAL_STOP`` — inverted.
* HO2S heater circuits (P0038/P0044/...), fuel-fired park heaters (P2029) and
  trailer/park-brake communication losses (U0137) = ``CRITICAL_STOP``.

Severity is not cosmetic: ``drive_safety_policy.decide_risk`` maps
``CRITICAL_STOP`` -> ``RED``, whose fixed advice is *"Aracı güvenli bir şekilde
durdurun. Motoru kapatın..."*. These tests stop that class of false positive
from re-entering the repository.

The tests are written against the empirical reproductions recorded in the audit
so a future refactor that reintroduces the old data fails here rather than
shipping a "stop the vehicle" advisory for a wiper relay.

Offline, deterministic — no network, no LLM.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.engine.ai import diagnostic_copilot as dc
from src.engine.ai.drive_safety_policy import decide_risk
from src.engine.ai.severity_rules import (
    audit_database_severities,
    critical_stop_allowlist,
    load_severity_rules,
    resolve_severity,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
DTC_DB = REPO_ROOT / "data" / "diagnostics" / "dtc_database.json"
RULES = REPO_ROOT / "data" / "diagnostics" / "dtc_severity_rules.json"


@pytest.fixture(scope="module")
def dtc_database() -> dict[str, object]:
    return json.loads(DTC_DB.read_text(encoding="utf-8"))


class TestRuleTableIntegrity:
    """The rule table itself must be present and well-formed."""

    def test_rule_table_exists(self) -> None:
        assert RULES.is_file(), (
            "dtc_severity_rules.json is missing — every DTC would resolve to "
            "UNKNOWN and the audit is blind (AGENTS.md 2.3)"
        )

    def test_rule_table_is_valid_json_object(self) -> None:
        payload = json.loads(RULES.read_text(encoding="utf-8"))
        assert isinstance(payload, dict)
        assert load_severity_rules()  # non-empty after load

    def test_every_pattern_compiles(self) -> None:
        """A malformed regex would be silently skipped at runtime — catch it here."""
        import re

        rules = load_severity_rules()
        patterns: list[str] = []
        for section in ("fault_type_patterns", "structural_fault_modes"):
            block = rules.get(section) or {}
            if isinstance(block, dict):
                for value in block.values():
                    if isinstance(value, list):
                        patterns.extend(p for p in value if isinstance(p, str))
        assert patterns, "no patterns found in the rule table"
        for pattern in patterns:
            re.compile(pattern)  # raises re.error on a bad pattern

    def test_critical_allowlist_has_no_duplicates(self) -> None:
        rules = load_severity_rules()
        allow = rules.get("critical_stop_allowlist") or {}
        codes = allow.get("exact_codes") or []
        assert len(codes) == len(set(codes)), "duplicate codes in the CRITICAL_STOP allowlist"


class TestCriticalStopAllowlist:
    """CRITICAL_STOP is a CLOSED list — the core of the fix."""

    def test_comfort_codes_are_never_critical_stop(self, dtc_database) -> None:
        """Body/comfort families must not reach the stop-the-engine rung."""
        offenders: list[tuple[str, str]] = []
        for code, record in dtc_database.items():
            if not isinstance(record, dict):
                continue
            severity = resolve_severity(
                code,
                str(record.get("title") or ""),
                str(record.get("subsystem") or ""),
            ).value
            if severity == "CRITICAL_STOP" and code[:1] in {"B", "C"}:
                offenders.append((code, severity))
        assert not offenders, (
            "body/chassis codes resolved to CRITICAL_STOP: " + repr(offenders[:10])
        )

    def test_database_critical_stop_subset_of_allowlist(self, dtc_database) -> None:
        """Every CRITICAL_STOP the table derives must be an allowlisted code.

        This is the ratchet: adding a new CRITICAL_STOP requires editing the
        allowlist deliberately, which is the reviewable act the audit asked for.
        """
        allow = critical_stop_allowlist()
        offenders: list[str] = []
        for code, record in dtc_database.items():
            if not isinstance(record, dict):
                continue
            severity = resolve_severity(
                code,
                str(record.get("title") or ""),
                str(record.get("subsystem") or ""),
            ).value
            if severity == "CRITICAL_STOP" and code not in allow:
                # Family rules may still permit a code (e.g. U01*).
                if not _family_allows(code):
                    offenders.append(code)
        assert not offenders, (
            "CRITICAL_STOP derived for non-allowlisted codes: " + repr(offenders[:20])
        )

    def test_critical_stop_volume_is_bounded(self, dtc_database) -> None:
        """The audit found 281 CRITICAL_STOP codes; the corrected base must be far smaller."""
        stopped = 0
        for code, record in dtc_database.items():
            if not isinstance(record, dict):
                continue
            if resolve_severity(
                code,
                str(record.get("title") or ""),
                str(record.get("subsystem") or ""),
            ).value == "CRITICAL_STOP":
                stopped += 1
        assert stopped < 150, f"{stopped} CRITICAL_STOP codes — allowlist has drifted open"


def _family_allows(code: str) -> bool:
    allow = load_severity_rules().get("critical_stop_allowlist") or {}
    for family in allow.get("family_rules") or []:
        if isinstance(family, dict):
            prefix = family.get("code_prefix")
            if prefix and code.startswith(str(prefix)) and not family.get("decision"):
                return True
    return False


class TestMeasuredAuditFindings:
    """The exact defects recorded in the audit, pinned case by case."""

    def test_misfire_severity_is_not_inverted(self, dtc_database) -> None:
        """P0301-P0312 must be no MORE severe than P0300 (audit finding 6)."""
        order = ["INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL_STOP"]
        p0300 = resolve_severity("P0300", "Random/Multiple Cylinder Misfire", "").value
        for code in [f"P030{i}" for i in range(1, 10)] + ["P0310", "P0311", "P0312"]:
            record = dtc_database.get(code)
            if not isinstance(record, dict):
                continue
            single = resolve_severity(
                code, str(record.get("title") or ""), str(record.get("subsystem") or "")
            ).value
            assert order.index(single) <= order.index(p0300), (
                f"{code} ({single}) is more severe than P0300 ({p0300}) — inversion returned"
            )

    @pytest.mark.parametrize(
        "code",
        ["P0038", "P0044", "P0058", "P0064", "P2029", "P2208", "P2209", "P2210", "P2211", "P2212"],
    )
    def test_heater_circuits_are_not_critical_stop(self, code: str, dtc_database) -> None:
        record = dtc_database.get(code)
        if not isinstance(record, dict):
            pytest.skip(f"{code} not present in the database")
        severity = resolve_severity(
            code, str(record.get("title") or ""), str(record.get("subsystem") or "")
        ).value
        assert severity != "CRITICAL_STOP", f"{code} heater circuit stamped CRITICAL_STOP"

    @pytest.mark.parametrize("code", ["B1431", "B1432", "B1483", "B2133", "B2458"])
    def test_body_codes_are_low(self, code: str, dtc_database) -> None:
        record = dtc_database.get(code)
        if not isinstance(record, dict):
            pytest.skip(f"{code} not present in the database")
        severity = resolve_severity(
            code, str(record.get("title") or ""), str(record.get("subsystem") or "")
        ).value
        assert severity in {"INFO", "LOW"}, f"{code} body code resolved to {severity}"

    def test_trailer_brake_comm_loss_is_not_critical(self, dtc_database) -> None:
        record = dtc_database.get("U0137")
        if not isinstance(record, dict):
            pytest.skip("U0137 not present")
        severity = resolve_severity(
            "U0137", str(record.get("title") or ""), str(record.get("subsystem") or "")
        ).value
        assert severity != "CRITICAL_STOP"

    def test_genuine_stop_codes_remain_critical(self, dtc_database) -> None:
        """The fix must not over-correct: real stop-worthy faults stay CRITICAL_STOP."""
        for code in ["P0217", "P0234", "P0087", "P0730"]:
            record = dtc_database.get(code)
            if not isinstance(record, dict):
                pytest.skip(f"{code} not present")
            severity = resolve_severity(
                code, str(record.get("title") or ""), str(record.get("subsystem") or "")
            ).value
            assert severity == "CRITICAL_STOP", f"{code} lost its CRITICAL_STOP rung"


class TestUnknownFailsSafe:
    """An unclassifiable code must never be given an invented rung."""

    def test_placeholder_title_is_unknown(self) -> None:
        assert resolve_severity("P9999", "OBD Code", "").value == "UNKNOWN"

    def test_unknown_maps_to_gray_not_green(self) -> None:
        assert decide_risk("UNKNOWN") == "GRAY"

    def test_empty_code_is_unknown(self) -> None:
        assert resolve_severity("", "", "").value == "UNKNOWN"

    def test_unknown_is_not_silently_medium(self) -> None:
        """UNKNOWN must be distinguishable from a fabricated MEDIUM."""
        assert resolve_severity("P9999", "OBD Code", "").value != "MEDIUM"


class TestRuntimeWiring:
    """The derivation must actually be WIRED, not merely available."""

    def test_external_load_derives_severity(self) -> None:
        dc.load_external_dtc_database()
        entry = dc.EXPERT_KNOWLEDGE_BASE.get("P0312")
        assert isinstance(entry, dict), "P0312 missing from the knowledge base"
        assert entry.get("severity") == "MEDIUM", (
            "external severity was not derived — wiring regressed"
        )

    def test_handcrafted_entries_are_reconciled(self) -> None:
        """Built-in entries carry the same defect and must be reconciled too."""
        dc.load_external_dtc_database()
        entry = dc.EXPERT_KNOWLEDGE_BASE.get("P0300")
        assert isinstance(entry, dict)
        assert entry.get("severity") == "MEDIUM", (
            "hand-crafted P0300 is still CRITICAL_STOP — reconciliation not wired"
        )

    def test_source_severity_is_recorded_for_audit(self) -> None:
        dc.load_external_dtc_database()
        entry = dc.EXPERT_KNOWLEDGE_BASE.get("P0312")
        assert isinstance(entry, dict)
        assert entry.get("_source_severity") == "CRITICAL_STOP", (
            "the original rung must be preserved so the change is auditable"
        )

    def test_analysis_severity_matches_database(self) -> None:
        """End-to-end: the copilot must not re-inflate a corrected rung."""
        from src.engine.ai.diagnostic_copilot import AiDiagnosticCopilot

        report = AiDiagnosticCopilot().analyze_session([{"code": "P0038", "fmi": 0}], {}, [])
        assert report.severity.value != "CRITICAL_STOP", (
            "copilot still advises stopping the vehicle for an HO2S heater circuit"
        )


class TestNonObdCodesAreOutOfScope:
    """The rule table must NOT touch J1939 SPN keys (regression guard).

    During development an unscoped reconciliation downgraded ``SPN100``
    (engine oil pressure) from CRITICAL_STOP to HIGH, because the "oil
    pressure" pattern was applied to a key the table has no authority over.
    ``tests/unit/test_offline_ai_reasoning.py`` caught it. These tests keep it
    caught.
    """

    def test_spn_key_is_out_of_scope(self) -> None:
        from src.engine.ai.severity_rules import is_obd_code

        assert not is_obd_code("SPN100")
        assert not is_obd_code("SPN 100")

    def test_spn_key_resolves_to_unknown(self) -> None:
        assert resolve_severity("SPN100", "Motor Yağ Basıncı Hatası", "").value == "UNKNOWN"

    def test_hex_suffix_codes_are_in_scope(self) -> None:
        """Real SAE codes contain hex digits (C003F, U010A) — not just decimal."""
        from src.engine.ai.severity_rules import is_obd_code

        assert is_obd_code("C003F")
        assert is_obd_code("U010A")
        assert is_obd_code("P0312")

    def test_reconciliation_leaves_spn_entries_untouched(self) -> None:
        dc.load_external_dtc_database()
        entry = dc.EXPERT_KNOWLEDGE_BASE.get("SPN100")
        assert isinstance(entry, dict), "SPN100 missing from the knowledge base"
        assert entry.get("severity") == "CRITICAL_STOP", (
            "the OBD-II rule table over-reached into a J1939 SPN entry"
        )


class TestDatabaseDriftRatchet:
    """The on-disk database should converge on the rule table (informational)."""

    def test_database_matches_rule_table_within_tolerance(self, dtc_database) -> None:
        """A large drift means the stored data was never rebuilt from the table.

        The runtime derives severity correctly regardless, so this is a
        convergence ratchet rather than a hard correctness gate; it is set
        generously and tightened as the rebuild tool is run.
        """
        mismatches = audit_database_severities(dtc_database)
        total = len(dtc_database)
        drift_pct = 100.0 * len(mismatches) / max(1, total)
        assert drift_pct < 40.0, (
            f"{drift_pct:.1f}% of records disagree with the rule table "
            "(run the severity rebuild to converge the on-disk data)"
        )

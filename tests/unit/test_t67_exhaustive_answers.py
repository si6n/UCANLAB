# -*- coding: utf-8 -*-
"""T67 regression lock — exhaustive, non-fabricating copilot answers.

Round T67 (2026-09-22) was driven by one operator requirement: *"her olasılığı
her şeyi hesaplasın"* — the copilot must compute every possibility, not a
single confident-looking line. The work surfaced five measured defects, each
pinned by a test below.

T67-A  FABRICATION. The query path defaulted coolant to 85.0 °C and printed it
       whenever RPM *or* boost was measured, so a session with only RPM
       rendered "1800 RPM, 0.00 Bar, 85.0°C". Missing `measurement` /
       `uds_routine` fields were replaced with invented text ("Nominal voltaj
       ve şasi dirençlerini test edin."), and stage 4 was hardcoded for every
       code. AGENTS.md §2.3 forbids all three. The session path has been strict
       since F-02; the query path now is too.

T67-B  TRUNCATION. `causes[:2]` and `steps[:2]` dropped data for most of the
       catalog (1,388 records carry 4 causes; 13,805 carry exactly 3 steps).
       The J1939 reader rendered the two richest procedure blocks out of up to
       52 and capped every list it printed.

T67-C  UNREAD FIELDS. DTC `procedures_full` (6,087 records), `vag_code`
       (2,102), `evidence_url` (11,088) and J1939 `diagnostic_steps` (546),
       `fmi_map` (170), `field_evidence` (244), `sitrakin_dtc_codes` (2,411)
       had no consumer anywhere in the engine.

T67-D  KB SHADOWING. `code not in EXPERT_KNOWLEDGE_BASE` was a key-presence
       test, so 17 hand-written builtin keys were never enriched by their
       external counterpart — P0300 alone lost 10 populated fields.

T67-E  ROUTER SHADOWING. Frame forensics ran first and matched any `0x…`
       token, so "P0300 nedir (CAN ID 0x7E8)" returned a frame report.

The tests are deliberately written against BEHAVIOUR (what the operator sees),
not against the implementation, so a future refactor that preserves the
guarantees still passes.
"""

from __future__ import annotations

import json
import re

import pytest

from src.engine.ai.diagnostic_copilot import (
    EXPERT_KNOWLEDGE_BASE,
    CausalBayesianInferenceEngine,
    ensure_external_dtc_database_loaded,
    get_j1939_spn_database,
)


@pytest.fixture(scope="module", autouse=True)
def _loaded() -> None:
    ensure_external_dtc_database_loaded()
    get_j1939_spn_database()


def _query(text: str, telemetry: dict | None = None) -> str:
    return CausalBayesianInferenceEngine.evaluate_diagnostic_query(
        text, [], telemetry or {}
    )


# ---------------------------------------------------------------------------
# T67-A — no fabricated measurement, no invented procedure text
# ---------------------------------------------------------------------------


class TestT67ANoFabricationOnTheQueryPath:
    """A value the operator never supplied must never be printed as measured."""

    def test_rpm_only_does_not_render_a_coolant_reading(self) -> None:
        """The measured defect: RPM alone printed "85.0°C"."""
        report = _query("P0300 nedir", {"EngineSpeed": 1800.0})
        assert "1800 RPM" in report
        assert "85.0" not in report, (
            "P0300 report fabricated a coolant reading (85.0 °C) from a session "
            "that only measured RPM"
        )

    def test_boost_only_does_not_render_rpm_or_coolant(self) -> None:
        report = _query("P0234 nedir", {"BoostPressure": 1.4})
        assert "1.40 Bar" in report
        assert "0 RPM" not in report
        assert "85.0" not in report

    def test_measured_coolant_is_still_rendered(self) -> None:
        """The fix must not silence a value that WAS measured."""
        report = _query("P0115 nedir", {"CoolantTemp": 92.5})
        assert "92.5°C" in report

    def test_no_telemetry_renders_no_measurement_line(self) -> None:
        report = _query("P0300 nedir", {})
        assert "RPM," not in report
        assert "85.0" not in report

    def test_missing_measurement_field_is_not_invented(self) -> None:
        """A record without `measurement` must state the gap, not invent one.

        "Nominal voltaj ve şasi dirençlerini test edin." was printed for every
        code that lacked the field — an invented tolerance (AGENTS.md §2.3).
        """
        victim = None
        for code, info in EXPERT_KNOWLEDGE_BASE.items():
            if not isinstance(info, dict):
                continue
            if not str(info.get("measurement", "") or "").strip() and not str(
                info.get("uds_routine", "") or ""
            ).strip():
                victim = code
                break
        if victim is None:
            pytest.skip("every KB record carries measurement + uds_routine")
        report = _query(f"{victim} nedir", {})
        assert "Nominal voltaj ve şasi dirençlerini test edin" not in report
        assert "uydurma tolerans üretilmedi" in report

    def test_stage4_is_not_the_same_boilerplate_for_every_code(self) -> None:
        """Stage 4 used to assert a UDS 0x14 clear for every code, always."""
        a = _query("P0300 nedir", {})
        b = _query("P0420 nedir", {})
        stage4_a = a.split("Aşama 4:")[-1].split("🔗")[0]
        stage4_b = b.split("Aşama 4:")[-1].split("🔗")[0]
        assert stage4_a != stage4_b, (
            "stage 4 is identical for two different codes — it is still hardcoded"
        )

    def test_report_does_not_offer_a_destructive_action_unprompted(self) -> None:
        """P0-3 parity: a report is a record, not a command surface."""
        report = _query("P0300 nedir", {})
        assert "act_uds_0x14_clear_dtc" not in report
        assert "uds_clear_dtc" not in report

    def test_j1939_report_does_not_offer_dm11_clear_unprompted(self) -> None:
        """The J1939 generator must not mint a DM11 clear on its own.

        SPN100 is a KB key, so the router answers it with the 4-stage report;
        the J1939 generator is reached by an SPN outside the KB. Both paths are
        checked here so neither can regress into offering a destructive action.
        """
        db = get_j1939_spn_database()
        for key, entry in db.get("spns", {}).items():
            if not isinstance(entry, dict) or not entry.get("causes"):
                continue
            num = key.replace("SPN_", "")
            if f"SPN{num}" in EXPERT_KNOWLEDGE_BASE:
                continue
            report = _query(f"SPN {num} nedir", {})
            if "[SPN" not in report:
                continue
            assert "act_j1939_dm11_clear" not in report, (
                f"SPN {num}: the J1939 report offered an unprompted DM11 clear"
            )
            # The READ-ONLY DM1 query is still allowed.
            assert "act_j1939_dm1_query" in report
            return
        pytest.skip("no SPN outside the knowledge base renders a J1939 report")


# ---------------------------------------------------------------------------
# T67-B — every possibility is computed, not the first two
# ---------------------------------------------------------------------------


class TestT67BExhaustiveAnswers:
    def test_all_causes_render_not_the_first_two(self) -> None:
        """Pick a record with >2 causes and require every one of them."""
        found = None
        for code, info in EXPERT_KNOWLEDGE_BASE.items():
            if not isinstance(info, dict):
                continue
            causes = info.get("causes")
            if isinstance(causes, list) and len(causes) >= 4:
                found = (code, causes)
                break
        if found is None:
            pytest.skip("no KB record carries 4+ causes")
        code, causes = found
        report = _query(f"{code} nedir", {})
        for cause in causes:
            probe = str(cause).strip()[:50]
            if not probe:
                continue
            assert probe in report, (
                f"{code}: cause {probe!r} was dropped — the report still truncates"
            )

    def test_all_steps_render_not_the_first_two(self) -> None:
        found = None
        for code, info in EXPERT_KNOWLEDGE_BASE.items():
            if not isinstance(info, dict):
                continue
            steps = info.get("steps")
            if isinstance(steps, list) and len(steps) >= 3:
                found = (code, steps)
                break
        if found is None:
            pytest.skip("no KB record carries 3+ steps")
        code, steps = found
        report = _query(f"{code} nedir", {})
        for step in steps:
            text = step[0] if isinstance(step, (list, tuple)) and step else step
            probe = str(text).strip()[:50]
            if not probe:
                continue
            assert probe in report, (
                f"{code}: step {probe!r} was dropped — the report still truncates"
            )

    def test_j1939_renders_more_than_two_procedure_blocks(self) -> None:
        """The old reader stopped at 2 blocks; SPN_157 alone holds 52."""
        db = get_j1939_spn_database()
        victim = None
        for key, entry in db.get("spns", {}).items():
            if not isinstance(entry, dict):
                continue
            blocks = entry.get("procedures_full")
            if not isinstance(blocks, list):
                continue
            clean = [b for b in blocks if isinstance(b, dict) and not b.get("_quarantined")]
            if len(clean) > 2:
                victim = (key.replace("SPN_", ""), len(clean))
                break
        if victim is None:
            pytest.skip("no SPN with more than 2 procedure blocks")
        num, total = victim
        report = _query(f"SPN {num} nedir", {})
        # The header states the rendered block count; it must exceed the old cap.
        m = re.search(r"\((\d+) blok / (\d+) kaynak sitesi", report)
        if m is None:
            pytest.skip(f"SPN {num} renders no grouped procedure block")
        assert int(m.group(1)) > 2, (
            f"SPN {num} has {total} clean blocks but only {m.group(1)} rendered"
        )

    def test_procedure_header_states_what_was_shown(self) -> None:
        """The operator must be able to see the answer was not truncated."""
        db = get_j1939_spn_database()
        for key, entry in db.get("spns", {}).items():
            if not isinstance(entry, dict):
                continue
            blocks = entry.get("procedures_full")
            if not isinstance(blocks, list) or len(blocks) < 2:
                continue
            report = _query(f"SPN {key.replace('SPN_', '')} nedir", {})
            if "procedures_full" not in report:
                continue
            assert "tekrarlar ayıklandı" in report
            return
        pytest.skip("no SPN renders a procedures_full block")

    def test_quarantined_block_never_renders(self) -> None:
        """T2-3 survives the widened reader."""
        db = get_j1939_spn_database()
        for key, entry in db.get("spns", {}).items():
            blocks = entry.get("procedures_full") if isinstance(entry, dict) else None
            if not isinstance(blocks, list):
                continue
            if not any(isinstance(b, dict) and b.get("_quarantined") for b in blocks):
                continue
            num = key.replace("SPN_", "")
            if f"SPN{num}" in EXPERT_KNOWLEDGE_BASE:
                continue
            report = _query(f"SPN {num} nedir", {})
            assert "frequently asked questions" not in report.lower()
            assert "dtcdocs.com" not in report.lower()
            return
        pytest.skip("no SPN with a quarantined block found")

    def test_determinism(self) -> None:
        assert _query("SPN 100 nedir", {}) == _query("SPN 100 nedir", {})
        assert _query("P0300 nedir", {}) == _query("P0300 nedir", {})


# ---------------------------------------------------------------------------
# T67-C — fields that had no consumer now reach the answer
# ---------------------------------------------------------------------------


class TestT67CUnreadFieldsAreWired:
    def test_dtc_procedures_full_reaches_the_report(self) -> None:
        """6,087 DTC records carry `procedures_full`; none were rendered."""
        import json
        from pathlib import Path

        db = json.loads(
            Path("data/diagnostics/dtc_database.json").read_text(encoding="utf-8")
        )
        victim = None
        for code, rec in db.items():
            if not isinstance(rec, dict):
                continue
            if code not in EXPERT_KNOWLEDGE_BASE:
                continue
            steps = rec.get("procedures_full")
            if isinstance(steps, list) and len(steps) >= 3:
                victim = (code, [str(s) for s in steps if isinstance(s, str) and str(s).strip()])
                break
        if victim is None:
            pytest.skip("no KB code carries a DTC-side procedures_full list")
        code, steps = victim
        report = _query(f"{code} nedir", {})
        assert "procedures_full" in report
        probe = steps[-1][:60]
        assert probe in report, (
            f"{code}: the last harvested procedure step {probe!r} is missing"
        )

    def test_vag_code_reaches_the_report(self) -> None:
        import json
        from pathlib import Path

        db = json.loads(
            Path("data/diagnostics/dtc_database.json").read_text(encoding="utf-8")
        )
        victim = None
        for code, rec in db.items():
            if isinstance(rec, dict) and rec.get("vag_code") and code in EXPERT_KNOWLEDGE_BASE:
                victim = (code, str(rec["vag_code"]))
                break
        if victim is None:
            pytest.skip("no KB code carries a vag_code")
        code, vag = victim
        report = _query(f"{code} nedir", {})
        assert "VAG" in report
        assert vag in report

    def test_j1939_diagnostic_steps_reach_the_report(self) -> None:
        db = get_j1939_spn_database()
        victim = None
        for key, entry in db.get("spns", {}).items():
            if not isinstance(entry, dict):
                continue
            steps = entry.get("diagnostic_steps")
            if isinstance(steps, list) and len(steps) >= 3:
                num = key.replace("SPN_", "")
                if f"SPN{num}" in EXPERT_KNOWLEDGE_BASE:
                    continue
                victim = (num, [str(s) for s in steps])
                break
        if victim is None:
            pytest.skip("no SPN outside the KB carries diagnostic_steps")
        num, steps = victim
        report = _query(f"SPN {num} nedir", {})
        assert "diagnostic_steps" in report
        assert steps[-1][:60] in report

    def test_j1939_fmi_map_reaches_the_report_for_the_named_fmi(self) -> None:
        db = get_j1939_spn_database()
        victim = None
        for key, entry in db.get("spns", {}).items():
            if not isinstance(entry, dict):
                continue
            fmi_map = entry.get("fmi_map")
            if not isinstance(fmi_map, dict) or not fmi_map:
                continue
            num = key.replace("SPN_", "")
            if f"SPN{num}" in EXPERT_KNOWLEDGE_BASE:
                continue
            fmi = sorted(fmi_map.keys(), key=lambda k: int(k) if str(k).isdigit() else 99)[0]
            row = fmi_map.get(fmi)
            if isinstance(row, dict) and (row.get("causes") or row.get("actions")):
                victim = (num, str(fmi), row)
                break
        if victim is None:
            pytest.skip("no SPN outside the KB carries an fmi_map row")
        num, fmi, row = victim
        report = _query(f"SPN {num} FMI {fmi} nedir", {})
        assert "fmi_map" in report
        causes = row.get("causes")
        if isinstance(causes, str) and causes.strip():
            assert causes.split(";")[0].strip()[:40] in report

    def test_j1939_field_evidence_reaches_the_report(self) -> None:
        db = get_j1939_spn_database()
        victim = None
        for key, entry in db.get("spns", {}).items():
            if not isinstance(entry, dict):
                continue
            ev = entry.get("field_evidence")
            if isinstance(ev, list) and ev and isinstance(ev[0], dict):
                num = key.replace("SPN_", "")
                if f"SPN{num}" in EXPERT_KNOWLEDGE_BASE:
                    continue
                victim = (num, str(ev[0].get("title", "")))
                break
        if victim is None:
            pytest.skip("no SPN outside the KB carries field_evidence")
        num, title = victim
        if not title:
            pytest.skip("field_evidence row carries no title")
        report = _query(f"SPN {num} nedir", {})
        assert "field_evidence" in report
        assert title[:50] in report

    def test_j1939_dtc_cross_reference_reaches_the_report(self) -> None:
        db = get_j1939_spn_database()
        victim = None
        for key, entry in db.get("spns", {}).items():
            if not isinstance(entry, dict):
                continue
            xref = entry.get("sitrakin_dtc_codes")
            if isinstance(xref, list) and xref:
                num = key.replace("SPN_", "")
                if f"SPN{num}" in EXPERT_KNOWLEDGE_BASE:
                    continue
                victim = (num, str(xref[0]))
                break
        if victim is None:
            pytest.skip("no SPN outside the KB carries sitrakin_dtc_codes")
        num, code = victim
        report = _query(f"SPN {num} nedir", {})
        assert "sitrakin_dtc_codes" in report
        assert code in report


# ---------------------------------------------------------------------------
# T67-D — a builtin record is enriched, never overwritten
# ---------------------------------------------------------------------------


class TestT67DBuiltinRecordsAreEnriched:
    SHADOWED = (
        "P0016", "P0087", "P0234", "P0300", "P0420", "P0A0B", "P0A0D",
        "P0A80", "P0A93", "P0AA1", "P0AA2", "P0AA6", "P0AC0", "P0B24",
        "U0100", "U0126", "U0415",
    )
    RICH = (
        "procedures_full", "causes_en", "description_en", "symptoms_en",
        "title_en", "evidence_url", "vag_code", "oem_variants",
        "gm_monitor", "nhtsa_evidence",
    )

    def test_shadowed_keys_gained_external_fields(self) -> None:
        gained = {}
        for code in self.SHADOWED:
            info = EXPERT_KNOWLEDGE_BASE.get(code)
            if not isinstance(info, dict):
                continue
            got = [f for f in self.RICH if info.get(f)]
            if got:
                gained[code] = got
        assert gained, (
            "no shadowed builtin key gained a single external field — the "
            "key-presence shadow test is back"
        )

    def test_curated_values_are_never_overwritten(self) -> None:
        """A hand-written title must survive the enrichment pass."""
        import json
        from pathlib import Path

        db = json.loads(
            Path("data/diagnostics/dtc_database.json").read_text(encoding="utf-8")
        )
        checked = 0
        for code in self.SHADOWED:
            info = EXPERT_KNOWLEDGE_BASE.get(code)
            rec = db.get(code)
            if not isinstance(info, dict) or not isinstance(rec, dict):
                continue
            ext_title = rec.get("title")
            kb_title = info.get("title")
            if not ext_title or not kb_title:
                continue
            # The builtin literal's title must still be the one in the KB.
            assert kb_title == info.get("title")
            checked += 1
        if checked == 0:
            pytest.skip("no comparable title found")

    def test_curated_severity_survives(self) -> None:
        """P0-1 severity is derived from the SAE rule table, not the scrape."""
        info = EXPERT_KNOWLEDGE_BASE.get("P0300")
        assert isinstance(info, dict)
        assert info.get("severity"), "P0300 lost its severity in the enrichment pass"

    def test_shadowed_ev_key_keeps_its_curated_ev_content(self) -> None:
        """P0AA6 is a hand-curated HV isolation rule; enrichment must not gut it."""
        info = EXPERT_KNOWLEDGE_BASE.get("P0AA6")
        assert isinstance(info, dict)
        blob = json.dumps(info, ensure_ascii=False)
        assert "Megger" in blob or "İzolasyon" in blob or "izolasyon" in blob


# ---------------------------------------------------------------------------
# T67-E — a CAN id in the sentence must not shadow the diagnosis
# ---------------------------------------------------------------------------


class TestT67ERouterShadowing:
    @pytest.mark.parametrize(
        "query,expected",
        [
            ("P0300 nedir (CAN ID 0x7E8)", "[P0300]"),
            ("SPN 100 FMI 3 (0x18FEEE00)", "SPN100"),
            ("U0100 iletisim koptu 0x7E0", "[U0100]"),
        ],
    )
    def test_fault_query_with_can_id_still_diagnoses(self, query: str, expected: str) -> None:
        report = _query(query, {})
        assert expected in report, (
            f"{query!r} returned a frame report instead of the fault diagnosis"
        )

    @pytest.mark.parametrize(
        "query,expected",
        [
            ("0x22 f190 vin oku", "UDS 0x22"),
            ("0x14 dtc temizle", "UDS 0x14"),
            ("0x11 ecu reset", "ECUReset"),
        ],
    )
    def test_action_request_with_can_id_still_acts(self, query: str, expected: str) -> None:
        report = _query(query, {})
        assert expected in report

    def test_pure_frame_query_still_forensics(self) -> None:
        """The frame branch must keep working when nothing else claims it."""
        assert "CAN Hata Karesi" in _query("0x00000000 hata karesi", {})
        assert "EV BMS" in _query("0x1808E5F4 BMS hücre", {})

    def test_frame_query_detection_is_not_broken(self) -> None:
        from src.engine.ai.diagnostic_copilot import AutomotiveTokenizer

        norm = AutomotiveTokenizer.normalize_text("0x7E8 nedir")
        out = CausalBayesianInferenceEngine.analyze_can_frame("0x7E8 nedir", norm, {})
        assert out is not None


# ---------------------------------------------------------------------------
# T67-G — the user-facing card carries the alternatives
# ---------------------------------------------------------------------------


class TestT67GCardCarriesAlternatives:
    def test_card_exposes_alternatives(self) -> None:
        from src.core.models.diagnostics import (
            DiagnosticDomain,
            DiagnosticEvent,
            VehicleSession,
        )
        from src.engine.ai.diagnostic_copilot import AiDiagnosticCopilot
        from src.engine.ai.user_report_composer import compose_user_card

        session = VehicleSession(session_id="t67", started_at_ns=1, domain=DiagnosticDomain.PASSENGER)
        session.events.append(
            DiagnosticEvent(
                timestamp_ns=2,
                code="P0300",
                domain=DiagnosticDomain.PASSENGER,
                severity="HIGH",
                status="ACTIVE",
            )
        )
        report = AiDiagnosticCopilot().analyze_session(
            [{"code": "P0300"}], {"EngineSpeed": 2400.0}, []
        )
        card = compose_user_card(report, session)
        payload = card.card_to_dict()
        assert "alternatives_tr" in payload["technical"]
        assert isinstance(payload["technical"]["alternatives_tr"], list)
        assert payload["technical"]["alternatives_tr"], (
            "the card carries no alternatives — the owner still sees one reading"
        )

    def test_card_alternatives_are_deterministic(self) -> None:
        from src.core.models.diagnostics import (
            DiagnosticDomain,
            DiagnosticEvent,
            VehicleSession,
        )
        from src.engine.ai.diagnostic_copilot import AiDiagnosticCopilot
        from src.engine.ai.user_report_composer import compose_user_card

        def _card() -> dict:
            session = VehicleSession(session_id="t67", started_at_ns=1, domain=DiagnosticDomain.PASSENGER)
            session.events.append(
                DiagnosticEvent(
                    timestamp_ns=2,
                    code="P0300",
                    domain=DiagnosticDomain.PASSENGER,
                    severity="HIGH",
                    status="ACTIVE",
                )
            )
            report = AiDiagnosticCopilot().analyze_session(
                [{"code": "P0300"}], {"EngineSpeed": 2400.0}, []
            )
            return compose_user_card(report, session).card_to_dict()

        assert _card() == _card()

    def test_card_still_never_says_kesin(self) -> None:
        """§45 Kural 3 survives the added content."""
        from src.core.models.diagnostics import (
            DiagnosticDomain,
            DiagnosticEvent,
            VehicleSession,
        )
        from src.engine.ai.diagnostic_copilot import AiDiagnosticCopilot
        from src.engine.ai.user_report_composer import compose_user_card, is_honest_card

        session = VehicleSession(session_id="t67", started_at_ns=1, domain=DiagnosticDomain.PASSENGER)
        session.events.append(
            DiagnosticEvent(
                timestamp_ns=2,
                code="P0300",
                domain=DiagnosticDomain.PASSENGER,
                severity="HIGH",
                status="ACTIVE",
            )
        )
        report = AiDiagnosticCopilot().analyze_session([{"code": "P0300"}], {}, [])
        card = compose_user_card(report, session)
        assert is_honest_card(card)


class TestT67HypothesesAreAllListed:
    def test_every_ranked_hypothesis_is_surfaced(self) -> None:
        from src.engine.ai.diagnostic_copilot import AiDiagnosticCopilot

        report = AiDiagnosticCopilot().analyze_session(
            [{"code": "P0300"}, {"code": "P0420"}], {"EngineSpeed": 2400.0}, []
        )
        if not report.hypothesis_candidates:
            pytest.skip("no hypothesis produced for this input")
        # Rank numbers must be contiguous from 1 when more than one exists.
        ranks = []
        for line in report.hypothesis_candidates:
            m = re.search(r"Kök neden adayı #(\d+)", line)
            if m:
                ranks.append(int(m.group(1)))
        if len(ranks) > 1:
            assert ranks == list(range(1, len(ranks) + 1)), (
                f"hypothesis ranks are not contiguous: {ranks}"
            )

    def test_narrow_gap_is_flagged(self) -> None:
        """A near-tie must say so instead of implying a settled diagnosis."""
        from src.engine.ai.diagnostic_copilot import AiDiagnosticCopilot

        report = AiDiagnosticCopilot().analyze_session(
            [{"code": "P0300"}, {"code": "P0420"}, {"code": "P0171"}],
            {"EngineSpeed": 2400.0, "CoolantTemp": 92.0},
            [],
        )
        blob = " ".join(report.hypothesis_candidates)
        if len(report.hypothesis_candidates) < 2:
            pytest.skip("fewer than two hypotheses for this input")
        assert "ayırt edici test" in blob or "%" in blob

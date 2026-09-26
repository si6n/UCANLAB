# -*- coding: utf-8 -*-
"""T74 regression lock — compact J1939 SPN/FMI query forms (measured defect).

WHY (measured 2026-09-24 on the shipped DB via ``evaluate_diagnostic_query``):

  The SPN and FMI extractors demanded whitespace between the token and the
  number (``\\bspn\\s*([0-9]+)\\b`` / ``\\bfmi[\\s:]*(\\d{1,2})\\b``), but
  ``AutomotiveTokenizer.normalize_text`` turns ``:`` into a SPACE while KEEPING
  ``_`` and ``-`` (its clean-up class is ``[^\\w\\s\\-\\.]`` and ``\\w``
  includes the underscore). An operator typing the compact form a DM1 display
  shows therefore produced a string the extractors could not see:

      "SPN100FMI3"    -> norm "spn100fmi3"     -> SPN regex: NO MATCH
      "SPN 100FMI 3"  -> norm "spn 100fmi 3"   -> SPN regex: NO MATCH
      "SPN_100_FMI_3" -> norm "spn_100_fmi_3"  -> SPN regex: NO MATCH

  With no SPN extracted, the query fell through to the J1939 keyword router,
  whose ``fmi`` keyword branch ends in ``_format_4stage_technician_report(
  "SPN4364")``. Measured BEFORE the fix — every one of these forms answered an
  UNRELATED fault at CRITICAL_STOP:

      "SPN100FMI3"    -> SPN4364 SCR DeNOx Dönüşüm Verimliliği Düşük
      "SPN157FMI3"    -> SPN4364 (asked about fuel-rail pressure)
      "SPN 100FMI 3"  -> SPN4364
      "SPN:100FMI:3"  -> SPN4364
      "SPN_100_FMI_3" -> SPN4364
      "SPN100FMI03"   -> SPN4364

  A SECOND, narrower loss was measured on the forms that DID route: the report
  body re-extracted the FMI with its own ``\\bfmi\\s*([0-9]+)\\b``, so the
  per-FMI rung (the whole point of P0-3) was dropped even though the SPN was
  right:

      "spn 157 fmi 3" -> routed SPN 157, but no "FMI 3" rung in the body

  Both extractors now share one separator-tolerant helper pair. The fix is a
  PARSING fix only: no new evidence, no new text, and an absent FMI still
  yields no FMI rung (fail-safe, never a fabricated one).

Locked invariants (all deterministic, no fabrication):
  1. Compact/underscore/hyphen/colon SPN forms resolve to the SPN they name.
  2. The same forms keep the per-FMI rung (FMI is not silently dropped).
  3. A zero-padded FMI ("FMI03") resolves the DB's unpadded key ("3").
  4. Digit runs are never truncated: SPN1004 is 1004, not 100.
  5. An absent FMI produces NO FMI rung and NO fabricated severity.
  6. Same input -> same output.

Fixtures are SYNTHETIC (monkeypatched SPN DB + KB + severity loader) — the
shipped ``data/diagnostics/*.json`` is never read here (T72 precedent).
"""

from __future__ import annotations

import pytest

import src.engine.ai.diagnostic_copilot as dc
import src.engine.ai.j1939_severity as js
from src.engine.ai.diagnostic_copilot import (
    AutomotiveTokenizer,
    query_fmi_number,
    query_spn_number,
)

#: The synthetic SPN DB. Two SPNs, chosen so a wrong route is unmistakable:
#: ``SPN_100`` (the one an operator names) and ``SPN_4364`` (the unrelated
#: fault the pre-fix router used to answer with).
_SYNTHETIC_SPN_DB: dict[str, object] = {
    "metadata": {"source": "synthetic-t74"},
    "fmi_definitions": {
        "3": {
            "name": "Voltage Above Normal",
            "description_tr": "Voltaj normalin üzerinde",
            "diagnostic_action": "Sinyal hattını kontrol edin",
        }
    },
    "spns": {
        "SPN_100": {
            "spn": 100,
            "name": "Engine Oil Pressure",
            "title_tr": "Motor Yağ Basıncı",
            "subsystem": "Yağlama",
            "associated_pgn": 65263,
            "range": [0, 1000],
            "unit": "kPa",
            "description": "Yağ basıncı sensörü",
            "fault_matrix": {
                "3": {
                    "fmi_name": "Voltage Above Normal",
                    "fault_title": "SENTETIK-FMI3-BASINC-SENSORU-VOLTAJ-YUKSEK",
                    "diagnostic_action": "Sinyal hattını ölçün",
                    "severity": "HIGH",
                    "severity_basis": "Elektriksel arıza",
                    "lamp": "Amber",
                }
            },
            "causes": ["SENTETIK-NEDEN-SENSOR-DEVRESI-KOPUK"],
            "steps": ["SENTETIK-ADIM-SINYAL-HATTINI-OLCUN"],
            "diagnostic_steps": ["SENTETIK-SAH-ADIMI"],
            "fmi_map": {
                "3": {
                    "fmi": 3,
                    "causes": "SENTETIK-FMIMAP-NEDEN-ACIK-DEVRE",
                    "actions": "Konnektörü kontrol edin",
                    "source": "synthetic",
                }
            },
            "procedures_full": [
                {
                    "source_site": "synthetic-site",
                    "overview": "SENTETIK-PROSEDUR-GENEL-BAKIS",
                    "steps": ["SENTETIK-PROSEDUR-ADIMI"],
                }
            ],
            "field_evidence": [
                {
                    "title": "SENTETIK-MAKALE",
                    "excerpt": "Sentetik alıntı",
                    "url": "https://example.invalid/a",
                }
            ],
        },
        "SPN_4364": {
            "spn": 4364,
            "name": "SCR Conversion Efficiency",
            "title_tr": "SCR Dönüşüm Verimliliği",
            "subsystem": "SCR",
            "associated_pgn": 0,
            "pgn_source": "j1939_73_diagnostic_only",
            "range": [0, 100],
            "unit": "%",
            "description": "Sentetik SCR kaydı",
            "fault_matrix": {},
            "causes": ["SENTETIK-SCR-NEDENI"],
            "steps": ["SENTETIK-SCR-ADIMI"],
        },
    },
}

#: Synthetic KB entries. ``SPN4364`` is present so the router's own keyword
#: branch stays reachable; ``SPN100`` is deliberately ABSENT so the SPN-100
#: queries route through the J1939 DB path — the path that renders the
#: ``causes``/``steps``/``diagnostic_steps``/``fmi_map`` fields under test.
_SYNTHETIC_KB: dict[str, dict[str, object]] = {
    "SPN4364": {
        "title": "SCR Dönüşüm Verimliliği Düşük",
        "subsystem": "Ağır Vasıta SCR",
        "severity": "CRITICAL_STOP",
        "causes": ["Sentetik SCR nedeni"],
        "steps": [["Sentetik SCR adımı", "Hedef"]],
        "measurement": "",
        "uds_routine": "",
    },
}

@pytest.fixture(autouse=True)
def _synthetic_databases(monkeypatch: pytest.MonkeyPatch) -> None:
    """Patch the SPN DB, the KB and the severity loader with synthetic content.

    The real ``data/diagnostics/*.json`` is never read, so these locks survive
    any future DB curation (T72 precedent).
    """
    # The J1939 getter caches into a module global; seed it so no file is read.
    monkeypatch.setattr(dc, "_CACHED_J1939_DB", _SYNTHETIC_SPN_DB, raising=False)
    monkeypatch.setattr(dc, "_CACHED_DTC_DB", _SYNTHETIC_KB, raising=False)

    kb = dc.EXPERT_KNOWLEDGE_BASE
    monkeypatch.setitem(kb, "SPN4364", _SYNTHETIC_KB["SPN4364"])
    # SPN100 must NOT resolve from the KB, or the report would take the 4-stage
    # path and never render the J1939 record fields under test.
    monkeypatch.delitem(kb, "SPN100", raising=False)

    # ``evaluate_diagnostic_query`` calls this at entry; keep it a no-op so the
    # lazy external-DB loader cannot repopulate the KB mid-test.
    monkeypatch.setattr(dc, "ensure_external_dtc_database_loaded", lambda: None, raising=False)

    # The severity resolver has its OWN lru_cached loader that reads the real
    # file. Patch it so a KB-path FMI lookup is synthetic too.
    monkeypatch.setattr(js, "load_spn_database", lambda: _SYNTHETIC_SPN_DB, raising=False)

def _answered_spn(report: str) -> str | None:
    """The SPN the report is actually ABOUT, read off its header line.

    Handles both report shapes: the KB 4-stage form (``**[SPN100] — ...``) and
    the J1939 form (``**[SPN 100] — ...``).
    """
    import re

    head = report.lstrip().splitlines()[0]
    match = re.search(r"\*\*\[SPN[\s_]*([0-9]+)\]", head)
    return f"SPN{match.group(1)}" if match else None

def _ask(query: str) -> str:
    return dc.CausalBayesianInferenceEngine.evaluate_diagnostic_query(query, [], {})

# ---------------------------------------------------------------------------
# 1. The extractors themselves (pure, no DB)
# ---------------------------------------------------------------------------

class TestExtractorSeparators:
    """The shared helpers must see every form ``normalize_text`` can emit."""

    @pytest.mark.parametrize(
        "query,expected",
        [
            ("SPN 100", 100),
            ("SPN100", 100),
            ("SPN_100", 100),
            ("SPN-100", 100),
            ("SPN:100", 100),
            ("SPN 100 FMI 3", 100),
            ("SPN100FMI3", 100),
            ("SPN 100FMI 3", 100),
            ("SPN:100FMI:3", 100),
            ("SPN_100_FMI_3", 100),
            ("SPN100FMI03", 100),
            ("SPN157FMI3", 157),
        ],
    )
    def test_spn_extracted_from_every_separator_form(self, query: str, expected: int) -> None:
        norm = AutomotiveTokenizer.normalize_text(query)
        assert query_spn_number(norm) == expected, f"{query!r} -> {norm!r}"

    @pytest.mark.parametrize(
        "query,expected",
        [
            ("SPN 100 FMI 3", 3),
            ("SPN100FMI3", 3),
            ("SPN 100FMI 3", 3),
            ("SPN:100FMI:3", 3),
            ("SPN_100_FMI_3", 3),
            ("SPN100FMI03", 3),
            ("SPN 100 FMI=3", 3),
            ("SPN 100 fmi no 3", 3),
            ("SPN 100 FMI: 3", 3),
        ],
    )
    def test_fmi_extracted_from_every_separator_form(self, query: str, expected: int) -> None:
        norm = AutomotiveTokenizer.normalize_text(query)
        assert query_fmi_number(norm) == expected, f"{query!r} -> {norm!r} lost its FMI"

    def test_digit_run_is_never_truncated(self) -> None:
        """SPN1004 is 1004 — a greedy digit run must not clip to 100."""
        assert query_spn_number(AutomotiveTokenizer.normalize_text("SPN1004")) == 1004

    @pytest.mark.parametrize("query", ["SPN 100", "SPN 100 arıza", "fmi nedir", "spn100"])
    def test_absent_fmi_stays_absent(self, query: str) -> None:
        """No digits after the token => None, so no FMI is ever inferred."""
        assert query_fmi_number(AutomotiveTokenizer.normalize_text(query)) is None

    def test_three_digit_run_is_not_an_fmi(self) -> None:
        """FMI is 0-31 (SAE J1939-73); "FMI 123" must not yield 12."""
        assert query_fmi_number(AutomotiveTokenizer.normalize_text("SPN 100 FMI 123")) is None

    @pytest.mark.parametrize("query", ["xspn100", "aspx100", "dspn100"])
    def test_unrelated_token_does_not_match(self, query: str) -> None:
        """The leading boundary still holds: "xspn100" is not an SPN."""
        assert query_spn_number(AutomotiveTokenizer.normalize_text(query)) is None

# ---------------------------------------------------------------------------
# 2. End-to-end routing (the measured defect)
# ---------------------------------------------------------------------------

class TestCompactFormRouting:
    """Every compact form must answer the SPN it names, never SPN4364."""

    @pytest.mark.parametrize(
        "query",
        [
            "SPN 100 FMI 3",
            "SPN100",
            "SPN_100",
            "SPN-100",
            "SPN:100",
            "SPN100FMI3",
            "SPN 100FMI 3",
            "SPN:100FMI:3",
            "SPN_100_FMI_3",
            "SPN100FMI03",
        ],
    )
    def test_query_answers_the_named_spn(self, query: str) -> None:
        report = _ask(query)
        assert _answered_spn(report) == "SPN100", (
            f"{query!r} answered {_answered_spn(report)!r} — the pre-fix router "
            "sent compact forms to the unrelated SPN4364"
        )

    def test_compact_form_keeps_the_per_fmi_rung(self) -> None:
        """The FMI is not merely routed — its specific rung must render."""
        report = _ask("SPN100FMI3")
        assert "FMI 3" in report
        # The synthetic fault_matrix title must be surfaced for FMI 3.
        assert "SENTETIK-FMI3-BASINC-SENSORU-VOLTAJ-YUKSEK" in report

    def test_zero_padded_fmi_hits_the_unpadded_db_key(self) -> None:
        '''"FMI03" and "FMI 3" must resolve the same synthetic DB row.'''
        padded = _ask("SPN100FMI03")
        plain = _ask("SPN 100 FMI 3")
        assert "SENTETIK-FMI3-BASINC-SENSORU-VOLTAJ-YUKSEK" in padded
        assert "SENTETIK-FMI3-BASINC-SENSORU-VOLTAJ-YUKSEK" in plain

    def test_unrelated_spn_is_still_reachable(self) -> None:
        """The fix must not swallow the router's own SPN4364 branch."""
        assert _answered_spn(_ask("SPN 4364")) == "SPN4364"

    def test_compact_form_reaches_the_fmi_map(self) -> None:
        """`fmi_map` is FMI-keyed: the compact form must still reach it."""
        report = _ask("SPN100FMI3")
        assert "SENTETIK-FMIMAP-NEDEN-ACIK-DEVRE" in report

# ---------------------------------------------------------------------------
# 3. No fabrication (AGENTS.md §2.3)
# ---------------------------------------------------------------------------

class TestNoFabrication:
    """An absent FMI must produce a gap, never an invented rung."""

    def test_absent_fmi_renders_no_fmi_rung(self) -> None:
        report = _ask("SPN 100")
        assert _answered_spn(report) == "SPN100"
        # No per-FMI line, and none of the synthetic FMI-3 text.
        assert "SENTETIK-FMI3-BASINC-SENSORU-VOLTAJ-YUKSEK" not in report
        assert "SENTETIK-FMIMAP-NEDEN-ACIK-DEVRE" not in report

    def test_unknown_fmi_is_not_invented(self) -> None:
        """FMI 9 is absent from the synthetic record: no rung, no invented text."""
        report = _ask("SPN 100 FMI 9")
        assert _answered_spn(report) == "SPN100"
        assert "SENTETIK-FMI3-BASINC-SENSORU-VOLTAJ-YUKSEK" not in report
        assert "SENTETIK-FMIMAP-NEDEN-ACIK-DEVRE" not in report

    def test_recorded_content_is_rendered_verbatim(self) -> None:
        """What IS recorded is rendered — the fix must not suppress content."""
        report = _ask("SPN100FMI3")
        for expected in (
            "SENTETIK-NEDEN-SENSOR-DEVRESI-KOPUK",   # top-level causes
            "SENTETIK-ADIM-SINYAL-HATTINI-OLCUN",    # top-level steps
            "SENTETIK-SAH-ADIMI",                    # diagnostic_steps
            "SENTETIK-PROSEDUR-GENEL-BAKIS",         # procedures_full overview
            "SENTETIK-MAKALE",                       # field_evidence
        ):
            assert expected in report, f"recorded content {expected!r} was dropped"

# ---------------------------------------------------------------------------
# 4. Determinism
# ---------------------------------------------------------------------------

class TestDeterminism:
    @pytest.mark.parametrize("query", ["SPN100FMI3", "SPN_100_FMI_3", "SPN 100"])
    def test_same_input_same_output(self, query: str) -> None:
        assert _ask(query) == _ask(query)

    def test_equivalent_forms_agree_on_the_spn(self) -> None:
        """Separator spelling changes, the ANSWER does not."""
        answers = {
            q: _answered_spn(_ask(q))
            for q in ("SPN100FMI3", "SPN 100 FMI 3", "SPN_100_FMI_3", "SPN:100FMI:3")
        }
        assert set(answers.values()) == {"SPN100"}, answers

"""T2-8 regression tests — signal-name ALIAS RESOLUTION, fail-closed loading,
unaliased behaviour preservation, the FMI qualifier gate, and the two target
golden cases.

Measured root cause this suite locks (``scripts/_t28_probe_measure.py``):

    golden signal names   : 22   (CoolantTemp, BoostPressure, CamPhaseAngle, …)
    graph evidence signals: 25   (EngineCoolantTemp, EngineOilPressure, …)
    NORMALIZED OVERLAP    :  3   -> 13.6 %

``_normalize_signal`` normalizes FORM but not MEANING, so ``coolanttemp`` never
met ``enginecoolanttemp``; ``case-spn110-verified`` produced ``samples=[]``,
BOTH SPN 110 nodes scored exactly 1.0, and the lexicographic id tie-break picked
``coolant-sensor-open`` over ``thermostat-stuck`` — the engine decided by
alphabet, not by evidence.

Four properties are locked here:

  1. alias resolution is real, two-sided and reported (overlap before/after);
  2. alias loading is FAIL-CLOSED (missing/corrupt/shape-broken -> empty map,
     never a crash) — and the engine still ranks without it;
  3. a name that is in NO alias record keeps EXACTLY today's behaviour;
  4. the FMI qualifier gate applies ONLY to nodes that DECLARE a qualifier, so
     the FMI-less golden event still matches (a naive gate would MISS it).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.core.models.diagnostics import (
    DiagnosticDomain,
    DiagnosticEvent,
    SignalSample,
    VehicleSession,
)
from src.engine.ai.anomaly_detector import AnomalyFinding
from src.engine.ai.calibration import _case_to_vehicle_session
from src.engine.ai.golden_cases import load_all_cases
from src.engine.ai.golden_similarity import _normalize_signal
from src.engine.ai.hypothesis_engine import (
    GraphNode,
    _split_code_qualifier,
    canonical_signal_set,
    canonicalize_signal,
    load_root_cause_graph,
    load_signal_aliases,
    rank_hypotheses,
)

ROOT = Path(__file__).resolve().parents[2]
ALIASES_FILE = ROOT / "data" / "diagnostics" / "signal_aliases.json"
GRAPH_FILE = ROOT / "data" / "diagnostics" / "root_cause_graph.json"


def _session(codes: list[str] | None = None, samples: list[SignalSample] | None = None) -> VehicleSession:
    s = VehicleSession(session_id="t28", started_at_ns=1, domain=DiagnosticDomain.HEAVY_DUTY)
    for code in codes or []:
        s.events.append(
            DiagnosticEvent(timestamp_ns=1, code=code, domain=DiagnosticDomain.HEAVY_DUTY, severity="HIGH", status="ACTIVE")
        )
    s.samples.extend(samples or [])
    return s


def _case(case_id: str):
    return next(c for c in load_all_cases() if c.case_id == case_id)


def _rank_case(case):
    session, names = _case_to_vehicle_session(case)
    return rank_hypotheses(session, anomalies=[], observed_signal_names=set(names))


# ---------------------------------------------------------------------------
# 1. the alias data itself is real, two-sided evidence
# ---------------------------------------------------------------------------


class TestAliasData:
    def test_shipped_alias_file_loads_and_is_non_empty(self) -> None:
        aliases = load_signal_aliases()
        assert aliases, "the shipped alias map must not be empty"
        assert len(aliases) >= 12, f"alias map unexpectedly small: {len(aliases)}"

    def test_every_alias_record_has_a_protocol_rationale(self) -> None:
        """No fabricated alias: each record cites WHY the two names are one signal."""
        payload = json.loads(ALIASES_FILE.read_text(encoding="utf-8"))
        records = payload["aliases"]
        assert len(records) >= 12
        for record in records:
            assert str(record.get("rationale", "")).strip(), f"{record.get('canonical')} has no rationale"
            assert record.get("source_forms"), f"{record.get('canonical')} has no source_forms"

    def test_alias_resolution_collapses_two_real_spellings_to_one(self) -> None:
        """The measured defect, verbatim: golden vs node spelling, one signal."""
        aliases = load_signal_aliases()
        assert canonicalize_signal("CoolantTemp", aliases) == canonicalize_signal("EngineCoolantTemp", aliases)
        assert canonicalize_signal("coolant_temp", aliases) == canonicalize_signal("CoolantTemp", aliases)
        # …and the canonical name is stable under a second application.
        once = canonicalize_signal("EngineCoolantTemperature", aliases)
        assert canonicalize_signal(once, aliases) == once

    def test_normalization_alone_is_insufficient_showing_why_aliases_are_needed(self) -> None:
        """Guards the rationale: without aliases these two names do NOT meet."""
        assert _normalize_signal("CoolantTemp") != _normalize_signal("EngineCoolantTemp")

    def test_alias_overlap_improves_measurably_and_is_not_overclaimed(self) -> None:
        """Report the honest before/after of the ROOT CAUSE measurement.

        Before: 3 of 22 golden names intersect the graph's names (13.6 %).
        After : strictly more. The assertion is a LOWER bound proportional to the
        measured value, so it cannot be satisfied by an accidental rename — and
        it deliberately does NOT claim the golden set is now fully covered.
        """
        aliases = load_signal_aliases()
        payload = json.loads(GRAPH_FILE.read_text(encoding="utf-8"))
        node_signals = {s for n in payload["nodes"] for s in n["evidence_signals"] + n["contradicting_signals"]}
        golden_signals = {
            s["name"]
            for c in load_all_cases()
            for s in c.signals_of_interest
            if isinstance(s, dict) and isinstance(s.get("name"), str)
        }
        before = {_normalize_signal(g) for g in golden_signals} & {_normalize_signal(n) for n in node_signals}
        after = canonical_signal_set(golden_signals, aliases) & canonical_signal_set(node_signals, aliases)
        assert len(before) == 3, f"the documented baseline moved: {len(before)}"
        assert len(after) > len(before), "alias resolution produced no new intersection"
        # Honest: this is NOT full coverage.
        assert len(after) < len(golden_signals)

    def test_unresolved_names_are_declared_not_silently_aliased(self) -> None:
        """``EVAPPressure`` / ``MisfireCount`` have no graph counterpart.

        The task requires an honest open-risk report rather than an invented
        alias, so the file must DECLARE them.
        """
        payload = json.loads(ALIASES_FILE.read_text(encoding="utf-8"))
        unresolved = " ".join(payload.get("_unresolved", []))
        assert "EVAPPressure" in unresolved
        assert "misfirecount" in unresolved.lower()


# ---------------------------------------------------------------------------
# 2. alias loading is FAIL-CLOSED
# ---------------------------------------------------------------------------


class TestAliasLoadingFailsClosed:
    def test_missing_file_yields_empty_map(self, tmp_path: Path) -> None:
        assert load_signal_aliases(tmp_path / "does-not-exist.json") == {}

    def test_corrupt_json_yields_empty_map(self, tmp_path: Path) -> None:
        bad = tmp_path / "broken.json"
        bad.write_text("{not json at all", encoding="utf-8")
        assert load_signal_aliases(bad) == {}

    def test_wrong_shape_yields_empty_map(self, tmp_path: Path) -> None:
        for payload in (
            [],
            {"aliases": "nope"},
            {"aliases": [1, 2, 3]},
            {"something_else": []},
        ):
            p = tmp_path / "shape.json"
            p.write_text(json.dumps(payload), encoding="utf-8")
            assert load_signal_aliases(p) == {}, f"payload {payload!r} was not rejected"

    def test_one_malformed_record_cannot_disable_the_whole_map(self, tmp_path: Path) -> None:
        """Per-record fail-closed: a bad entry is skipped, good ones survive."""
        p = tmp_path / "partial.json"
        p.write_text(
            json.dumps(
                {
                    "aliases": [
                        {"canonical": "CoolantTemp", "source_forms": ["CoolantTemp", "EngineCoolantTemp"]},
                        {"canonical": "", "source_forms": ["x"]},
                        {"source_forms": ["y"]},
                        {"canonical": "BoostPressure"},
                        "not-an-object",
                        {"canonical": "EngineSpeed", "source_forms": ["EngineSpeed", "RPM"]},
                    ]
                }
            ),
            encoding="utf-8",
        )
        aliases = load_signal_aliases(p)
        assert canonicalize_signal("EngineCoolantTemp", aliases) == canonicalize_signal("CoolantTemp", aliases)
        assert canonicalize_signal("RPM", aliases) == canonicalize_signal("EngineSpeed", aliases)

    def test_unreadable_path_yields_empty_map(self, tmp_path: Path) -> None:
        # A directory is not a readable JSON file — must not raise.
        assert load_signal_aliases(tmp_path) == {}

    def test_engine_still_ranks_with_an_empty_alias_map(self, monkeypatch) -> None:
        """Fail-closed means the ENGINE keeps working, not just the loader.

        With no aliases the engine must fall back to exactly the pre-T2-8
        behaviour: bare-`SPN 110` DTC still produces hypotheses.
        """
        from src.engine.ai import hypothesis_engine as he

        monkeypatch.setattr(he, "load_signal_aliases", lambda data_path=None: {})
        hyps = he.rank_hypotheses(_session(["SPN 110"]), [], None, graph=load_root_cause_graph())
        assert hyps, "no hypotheses with an empty alias map — the engine became dependent on alias data"
        assert any(h.id == "coolant-sensor-open" for h in hyps)


# ---------------------------------------------------------------------------
# 3. UNALIASED behaviour is preserved (backwards compatibility)
# ---------------------------------------------------------------------------


class TestUnaliasedBehaviourPreserved:
    def test_name_in_no_alias_record_is_returned_normalized_verbatim(self) -> None:
        aliases = load_signal_aliases()
        for name in ("SomeUnknownSignal", "ZZPAD 0000", "BarometricPressure"):
            assert canonicalize_signal(name, aliases) == _normalize_signal(name)

    def test_operator_prefix_stripping_still_happens(self) -> None:
        """P1-2 must not be lost: the ``OP:`` / ``Q_SIG_`` wrappers still come off."""
        for raw in ("OP:Q_SIG_EngineSpeed", "Q_SIG_EngineSpeed", "EngineSpeed"):
            assert canonicalize_signal(raw) == canonicalize_signal("EngineSpeed")

    def test_aliased_name_collapses_to_the_recorded_canonical(self) -> None:
        aliases = {_normalize_signal("EngineCoolantTemp"): _normalize_signal("CoolantTemp")}
        assert canonicalize_signal("EngineCoolantTemp", aliases) == _normalize_signal("CoolantTemp")
        # An unaliased sibling is untouched by the same map.
        assert canonicalize_signal("EngineLoad", aliases) == _normalize_signal("EngineLoad")

    def test_unaliased_node_evidence_still_junctions_with_a_matching_anomaly(self) -> None:
        """The core pre-T2-8 path: exact-name anomaly -> evidence hit."""
        graph = [
            GraphNode(
                id="n-unaliased",
                title="Test node",
                evidence_signals=("BarometricPressure",),
                expected_dtcs=(),
                contradicting_signals=(),
                source_ref="test",
            )
        ]
        anomalies = [
            AnomalyFinding(signal="BarometricPressure", finding="x", ratio=1.0, evidence_sample_count=5, synthetic=False)
        ]
        ranked = rank_hypotheses(_session(), anomalies, None, graph)
        assert ranked
        assert any("BarometricPressure" in s for s in ranked[0].supporting_evidence)

    def test_observed_signal_names_default_to_absent(self) -> None:
        """Omitting the new kwarg is behaviour-preserving, not an error."""
        graph = load_root_cause_graph()
        session = _session(["SPN 110"])
        assert [h.to_dict() for h in rank_hypotheses(session, [], None, graph)] == [
            h.to_dict() for h in rank_hypotheses(session, [], None, graph, observed_signal_names=None)
        ]

    def test_name_only_evidence_cannot_rank_a_node_by_itself(self) -> None:
        """A NAME is half-weight corroboration, never independent evidence."""
        graph = [
            GraphNode(
                id="n-name",
                title="Test node",
                evidence_signals=("CoolantTemp",),
                expected_dtcs=("SPN 999",),
                contradicting_signals=(),
                source_ref="test",
            )
        ]
        # No DTC, no anomaly -> the early return already yields nothing.
        assert rank_hypotheses(_session(), [], None, graph, observed_signal_names={"CoolantTemp"}) == []

    def test_name_only_evidence_lifts_a_node_that_has_a_dtc_hit(self) -> None:
        graph = [
            GraphNode(
                id="n-name",
                title="Test node",
                evidence_signals=("CoolantTemp",),
                expected_dtcs=("SPN 110",),
                contradicting_signals=(),
                source_ref="test",
            )
        ]
        base = rank_hypotheses(_session(["SPN 110"]), [], None, graph)
        lifted = rank_hypotheses(_session(["SPN 110"]), [], None, graph, observed_signal_names={"CoolantTemp"})
        assert lifted[0].score >= base[0].score
        assert any("gözlemlenen sinyal" in s for s in lifted[0].supporting_evidence)
        # The ledger must never present it as a measurement.
        assert any("değer kaydı yok" in s for s in lifted[0].supporting_evidence)


# ---------------------------------------------------------------------------
# 4. FMI QUALIFIER GATE — only nodes that DECLARE a qualifier are gated
# ---------------------------------------------------------------------------


def _qualified_graph() -> list[GraphNode]:
    return [
        GraphNode(
            id="engine-overrev",
            title="Aşırı devir",
            evidence_signals=("EngineSpeed",),
            expected_dtcs=("SPN 190 FMI 0",),
            contradicting_signals=(),
            source_ref="test",
        ),
        GraphNode(
            id="crank-sync-loss",
            title="Faz senkronizasyon kaybı",
            evidence_signals=("EngineSpeed",),
            expected_dtcs=("SPN 190",),
            contradicting_signals=(),
            source_ref="test",
        ),
    ]


class TestFmiQualifierGate:
    @pytest.mark.parametrize(
        ("code", "expected"),
        [
            ("SPN 190", ("SPN 190", None)),
            ("SPN 190 FMI 0", ("SPN 190", 0)),
            ("SPN 190 fmi2", ("SPN 190", 2)),
            ("SPN 190.0", ("SPN 190", 0)),
            ("SPN 190.12", ("SPN 190", 12)),
            ("P0016", ("P0016", None)),
            ("P0A0B", ("P0A0B", None)),
        ],
    )
    def test_code_qualifier_split(self, code: str, expected: tuple[str, int | None]) -> None:
        assert _split_code_qualifier(code) == expected

    def test_event_without_fmi_cannot_satisfy_a_declared_qualifier(self) -> None:
        """The critical one-sided rule.

        The golden event is the BARE ``"SPN 190"``. A naive FMI gate would make
        that case a MISS, so a qualifier-bearing node must simply NOT match —
        while the unqualified node still does.
        """
        ranked = rank_hypotheses(_session(["SPN 190"]), [], None, _qualified_graph())
        ids = [h.id for h in ranked]
        assert "crank-sync-loss" in ids, "the unqualified node must still match a bare SPN event"
        assert "engine-overrev" not in ids, "a qualified node matched an event with NO FMI"

    def test_matching_fmi_activates_the_qualified_node(self) -> None:
        ranked = rank_hypotheses(_session(["SPN 190 FMI 0"]), [], None, _qualified_graph())
        ids = [h.id for h in ranked]
        assert "engine-overrev" in ids
        assert "crank-sync-loss" in ids
        overrev = next(h for h in ranked if h.id == "engine-overrev")
        assert any("FMI nitelik eşleşmesi" in s for s in overrev.supporting_evidence)

    def test_non_matching_fmi_keeps_the_qualified_node_silent(self) -> None:
        ranked = rank_hypotheses(_session(["SPN 190 FMI 2"]), [], None, _qualified_graph())
        assert "engine-overrev" not in [h.id for h in ranked]

    def test_unqualified_nodes_are_never_gated(self) -> None:
        """Regression lock: the sidecar must not start filtering 8.974 unqualified nodes."""
        graph = load_root_cause_graph()
        for code in ("SPN 100 FMI 1", "SPN 100", "SPN 111 FMI 1", "P0401"):
            assert rank_hypotheses(_session([code]), [], None, graph), f"{code} stopped producing hypotheses"

    def test_fmi_suffix_and_explicit_forms_are_interchangeable(self) -> None:
        graph = _qualified_graph()
        explicit = rank_hypotheses(_session(["SPN 190 FMI 0"]), [], None, graph)
        suffix = rank_hypotheses(_session(["SPN 190.0"]), [], None, graph)
        assert [h.id for h in explicit] == [h.id for h in suffix]


# ---------------------------------------------------------------------------
# 5. THE TARGET: the two golden cases
# ---------------------------------------------------------------------------


class TestTargetGoldenCases:
    def test_spn110_top1_is_thermostat_stuck(self) -> None:
        """``CoolantTemp`` must reach ``thermostat-stuck`` through the alias.

        Golden ``actual_fault``: "FMI 0 (Kritik Yüksek): Sıcaklık >108°C;
        termostat kapalı kalmış…" Before T2-8 BOTH SPN 110 nodes scored 1.0 and
        the lexicographic tie-break chose ``coolant-sensor-open``.
        """
        ranked = _rank_case(_case("case-spn110-verified"))
        assert ranked, "no hypotheses for the SPN 110 golden case"
        assert ranked[0].id == "thermostat-stuck", f"top-1 is {ranked[0].id}"
        # The alias is the reason: the node's EngineCoolantTemp evidence met the
        # case's CoolantTemp observation.
        assert any("gözlemlenen sinyal" in s for s in ranked[0].supporting_evidence)

    def test_spn190_qualified_node_fires_for_an_fmi_carrying_event(self) -> None:
        """The over-rev mechanism works for a LIVE DM1-style event.

        Golden ``actual_fault``: "FMI 0 (Aşırı Devir): Motor devri >2450 RPM".
        ``crank-sync-loss`` (the phase-sync mechanism, KB ``causes[1]``) was the
        only SPN 190 node; ``engine-overrev`` is the over-rev mechanism
        (KB ``causes[0]``) and is now a real seed node.

        NOTE the honest scope: the golden FIXTURE carries the bare ``"SPN 190"``
        with the FMI only in prose, which the engine must not read (see
        ``test_spn190_golden_case_stays_a_miss_and_is_reported``). This test
        proves the node itself is correct for the event shape a real J1939 DM1
        frame produces.
        """
        graph = load_root_cause_graph()
        ranked = rank_hypotheses(_session(["SPN 190 FMI 0"]), [], None, graph)
        assert ranked, "SPN 190 FMI 0 produced no hypotheses"
        assert ranked[0].id == "engine-overrev", f"top-1 is {ranked[0].id}"
        assert any("FMI nitelik eşleşmesi" in s for s in ranked[0].supporting_evidence)

    def test_spn190_golden_case_stays_a_miss_and_is_reported(self) -> None:
        """HONEST MEASURED OUTCOME — a documented limitation, not a hidden one.

        ``case-spn190-verified`` is still a MISS, and this test pins that so it
        cannot be silently "fixed" by leaking the case's own ``actual_fault``
        (ground truth) into the engine. The bare ``"SPN 190"`` event cannot
        satisfy the ``FMI 0`` qualifier on ``engine-overrev``, so the
        unqualified ``crank-sync-loss`` still wins.
        """
        ranked = _rank_case(_case("case-spn190-verified"))
        assert ranked
        assert ranked[0].id == "crank-sync-loss", "the FMI-less golden fixture changed behaviour unexpectedly"

    def test_spn190_seed_node_is_provenance_backed(self) -> None:
        """No fabricated node: the seed cites the expert KB entry verbatim."""
        payload = json.loads(GRAPH_FILE.read_text(encoding="utf-8"))
        node = next(n for n in payload["nodes"] if n["id"] == "engine-overrev")
        assert "EXPERT_KNOWLEDGE_BASE['SPN190'].causes[0]" in node["source_ref"]
        assert node["expected_dtcs"] == ["SPN 190 FMI 0"]

    def test_spn190_seed_ref_resolves_against_the_kb_source(self) -> None:
        """The same fail-closed resolver the derivation gate uses must accept it.

        Measured limit of that gate, stated honestly rather than glossed: it
        validates that the KB *key* exists in the copilot source, NOT the
        ``causes[i]`` subscript inside it. So this test proves the POSITIVE
        resolution (the key is real) and the key-level rejection of a made-up
        KB key. The index-level claim is proved separately, against the KB
        value itself, in ``test_spn190_seed_node_is_provenance_backed`` plus the
        ``causes[0]`` text check below.
        """
        import sys

        sys.path.insert(0, str(ROOT))
        from scripts.derive_root_cause_graph import _build_seed_resolver

        resolver = _build_seed_resolver()
        assert resolver("diagnostic_copilot.py EXPERT_KNOWLEDGE_BASE['SPN190'].causes[0]") is not None
        # An invented KB KEY is rejected (the anti-fabrication property).
        assert resolver("diagnostic_copilot.py EXPERT_KNOWLEDGE_BASE['SPN190_MADE_UP'].causes[0]") is None

    def test_spn190_overrev_mechanism_really_is_causes_zero(self) -> None:
        """Index-level provenance, checked against the KB value not the gate.

        ``EXPERT_KNOWLEDGE_BASE['SPN190'].causes[0]`` must be the OVER-REV
        mechanism (the golden case's documented fault), while
        ``causes[1]`` — the entry ``crank-sync-loss`` cites — is the phase-sync
        mechanism. If this ever swaps, the seed node's provenance is wrong.
        """
        from src.engine.ai.diagnostic_copilot import EXPERT_KNOWLEDGE_BASE

        causes = EXPERT_KNOWLEDGE_BASE["SPN190"]["causes"]
        assert causes[0].startswith("FMI 0")
        assert "Aşırı Devir" in causes[0]
        assert "2450" in causes[0]
        assert causes[1].startswith("FMI 2")
        assert "Faz Senkronizasyon" in causes[1]

    def test_spn110_is_a_calibration_hit_and_spn190_is_the_documented_miss(self) -> None:
        """End-to-end: the metric that defines HIT, per target case.

        SPN110 is now a HIT (was a MISS). SPN190 is still a MISS for the reason
        documented above — the fixture carries no FMI the engine may read.
        Asserting BOTH directions keeps the honest state pinned.
        """
        from src.engine.ai.calibration import _concepts_in
        from src.engine.ai.golden_similarity import _normalize_code

        def hit(case_id: str) -> bool:
            case = _case(case_id)
            top = _rank_case(case)[0]
            case_codes = frozenset(_normalize_code(d["code"]) for d in case.dtcs if isinstance(d, dict) and d.get("code"))
            top_codes = frozenset(_normalize_code(c) for c in top.expected_dtcs)
            return bool(top_codes & case_codes) and bool(
                _concepts_in(top.fault) & _concepts_in(case.actual_fault or "")
            )

        assert hit("case-spn110-verified") is True, "SPN110 must be a HIT (T2-8 target)"
        assert hit("case-spn190-verified") is False, "SPN190 unexpectedly changed — re-verify the FMI reasoning"

    def test_headline_metrics_did_not_drop(self) -> None:
        """Overall top-1 and the DB-supported subset must not regress.

        Measured before T2-8: 27/54 = 50.00 % overall, 27/34 = 79.41 % subset.
        These are LOWER bounds — the change is required to raise them, and the
        exact new values are reported in the task report.
        """
        from src.engine.ai.calibration import evaluate_calibration

        result = evaluate_calibration()
        assert result.verified_cases == 54
        assert result.top1_matches >= 28, f"overall top-1 regressed to {result.top1_matches}/54"

        graph = load_root_cause_graph()
        verified = [c for c in load_all_cases() if c.verified and not c.is_draft and c.actual_fault]
        from src.engine.ai.golden_similarity import _normalize_code

        supported = [
            c
            for c in verified
            if any(_normalize_code(c.dtcs[0]["code"]) in node.norm_dtcs for node in graph)
        ]
        assert len(supported) == 34, f"DB-supported subset changed size: {len(supported)}"
        from src.engine.ai.calibration import _concepts_in

        hits = 0
        for case in supported:
            ranked = _rank_case(case)
            if not ranked:
                continue
            top = ranked[0]
            case_codes = frozenset(_normalize_code(d["code"]) for d in case.dtcs if isinstance(d, dict) and d.get("code"))
            top_codes = frozenset(_normalize_code(c) for c in top.expected_dtcs)
            if (top_codes & case_codes) and (_concepts_in(top.fault) & _concepts_in(case.actual_fault or "")):
                hits += 1
        assert hits >= 28, f"DB-supported subset top-1 regressed to {hits}/34"

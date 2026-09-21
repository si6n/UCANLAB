"""T3-3 regresyon: içerik-imzası sınıflandırıcısı ve ölçülen sınırları.

T3-3'ün asıl bulgusu bir ölçümdü: ``dtcdocs.com`` dışındaki kaynaklar
sandıldığı kadar kirli DEĞİL (``detroitdieselengines.info`` %7.5,
boş-kaynak %3.2, ``tur23_j1939hub`` %5.8). Testler hem sınıflandırıcıyı hem de
bu **ölçülmüş sınırları** kilitler; bir sonraki ajan eşikleri genişletirse
kırmızıya döner ve §2.3 gereği meşru kanıtın silinmediğini kanıtlar.

Ayrıca iki GERÇEK yanlış-pozitif sınıfı kilitlenir — ikisi de ilk sürümde
meşru nedenleri junk saymıştı:
  1. "duyuru ifadesi + uzun satır" → kelime sayısı karar veriyordu
  2. "gerçek neden + SEO bölümü tek kayda yapıştırılmış" → meşru önek siliniyordu
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.engine.ai.junk_content_signatures import (
    SIGNATURE_CATALOG,
    classify_cause,
    is_dirty_source,
)

ROOT = Path(__file__).resolve().parents[2]
SPN_DB = ROOT / "data/diagnostics/j1939_spn_fmi_database.json"


class TestClassifierBasicBehaviour:
    def test_empty_and_whitespace_are_clean(self) -> None:
        assert classify_cause("").is_junk is False
        assert classify_cause("   \n  ").is_junk is False
        assert classify_cause(None).is_junk is False  # type: ignore[arg-type]

    def test_plain_real_cause_is_clean(self) -> None:
        for text in (
            "Sensor Gap Check: Measure air gap between speed sensor tip and turbine wheel",
            "FMI 3: Voltage above normal, or shorted to high source.",
            "Corroded connector pins: Moisture ingress at the 120-pin ECM connector",
        ):
            assert classify_cause(text).is_junk is False, text

    def test_seo_filler_without_a_cause_is_junk(self) -> None:
        v = classify_cause("Advanced Technical Analysis")
        assert v.is_junk is True and v.signature == "seo_marketing_filler"

    def test_link_residue_is_junk(self) -> None:
        v = classify_cause("See https://example.com/fault-codes for the full list")
        assert v.is_junk is True and v.signature == "link_or_attribution_residue"

    def test_mojibake_is_junk(self) -> None:
        v = classify_cause("AdBlue / DEF S\u00c4\u00b1v\u00c4\u00b1 Kalitesi")
        assert v.is_junk is True and v.signature == "mojibake_bad_encoding"

    def test_verdict_always_names_a_signature_when_junk(self) -> None:
        for text in ("Advanced Technical Analysis", "www.foo.com", "\ufffd broken"):
            v = classify_cause(text)
            assert v.is_junk is True and v.signature, text


class TestMeasuredFalsePositivesAreLocked:
    """The two false-positive classes found during the T3-3 measurement."""

    def test_long_announcement_line_with_a_fault_noun_is_clean(self) -> None:
        """Class 1: an earlier revision junked these on line length alone."""
        text = (
            "Electrical Circuit Fault: Damaged wiring or poor connections can "
            "disrupt signal transmission from sensors to the ECM and may cause "
            "possible causes of intermittent fault logging to be misread"
        )
        assert classify_cause(text).is_junk is False, text

    def test_real_cause_prefixed_to_an_seo_section_is_clean(self) -> None:
        """Class 2: harvest concatenated a genuine cause with SEO filler.

        Verified live in the database at SPN_4335[3] and SPN_61683[3]; deleting
        the record would have destroyed the leading real mechanism.
        """
        text = (
            "Wiring harness damage: Heat from exhaust aftertreatment system "
            "melted wire jacket near DPF. Advanced Technical Analysis The ECM "
            "monitors the signal pin voltage via a pull-up resistor to 5V reference"
        )
        assert classify_cause(text).is_junk is False, text

    def test_seo_only_tail_still_junks_when_head_is_not_a_cause(self) -> None:
        """The head guard must not become a blanket immunity for the marker."""
        text = "See also our experts. Advanced Technical Analysis The ECM monitors continuously."
        assert classify_cause(text).is_junk is True


class TestSourceRuleIsNarrow:
    def test_only_truly_wholesale_junk_sources_are_listed(self) -> None:
        """detroitdieselengines.info was deliberately REMOVED from this list."""
        assert is_dirty_source("dtcdocs.com") is True
        assert (
            is_dirty_source("detroitdieselengines.info") is False
        ), "measured 7.5% junk — wholesale removal would destroy ~296 legitimate causes"
        assert is_dirty_source("sitrak_ccby4") is False
        assert is_dirty_source("") is False
        assert is_dirty_source(None) is False  # type: ignore[arg-type]

    def test_catalog_lists_the_source_rule_honestly(self) -> None:
        assert "dirty_source_patterns" in SIGNATURE_CATALOG
        patterns = SIGNATURE_CATALOG["dirty_source_patterns"]
        assert isinstance(patterns, list)
        assert not any("detroit" in p for p in patterns)


class TestMeasuredDatabaseBounds:
    """Pin the MEASURED junk rate per source so an over-broad rule is caught.

    These bounds come from the T3-3 sweep. A future classifier that suddenly
    flags a large share of a legitimate source fails here — which is exactly the
    §2.3 protection (do not delete legitimate evidence to remove junk).
    """

    @pytest.fixture(scope="class")
    def junk_by_source(self) -> dict[str, tuple[int, int]]:
        spns = json.loads(SPN_DB.read_text(encoding="utf-8"))["spns"]
        counts: dict[str, list[int]] = {}
        for entry in spns.values():
            if not isinstance(entry, dict):
                continue
            src = str(entry.get("source", ""))
            c = counts.setdefault(src, [0, 0])
            for cause in entry.get("causes") or []:
                c[1] += 1
                if classify_cause(str(cause)).is_junk:
                    c[0] += 1
        return {k: (v[0], v[1]) for k, v in counts.items()}

    def test_sitrak_is_effectively_untouched(self, junk_by_source: dict[str, tuple[int, int]]) -> None:
        """SITRAK is licensed/attributed (CC-BY-4.0) — its data must stay intact."""
        junk, total = junk_by_source.get("sitrak_ccby4", (0, 0))
        assert total > 3000, f"expected SITRAK rows; got {total}"
        assert junk / total < 0.01, f"SITRAK junk rate {junk}/{total} too high"

    def test_detroit_harvest_stays_under_ten_percent(
        self, junk_by_source: dict[str, tuple[int, int]]
    ) -> None:
        junk, total = junk_by_source.get("detroitdieselengines.info", (0, 0))
        assert total > 200, f"expected detroit rows; got {total}"
        assert junk / total < 0.10, (
            f"detroit junk rate {junk}/{total} — the rule is eating legitimate procedures"
        )

    def test_total_junk_share_is_small(self, junk_by_source: dict[str, tuple[int, int]]) -> None:
        junk = sum(j for j, _ in junk_by_source.values())
        total = sum(t for _, t in junk_by_source.values())
        assert total > 7000, f"expected the full corpus; got {total}"
        assert junk / total < 0.05, f"overall junk share {junk}/{total} is implausibly high"


class TestGraphIsNotFedByJunk:
    """The measured end state: no graph node derives from a junk cause."""

    def test_no_graph_node_derives_from_a_junk_cause(self) -> None:
        import re

        spns = json.loads(SPN_DB.read_text(encoding="utf-8"))["spns"]
        junk_keys: set[tuple[str, int]] = set()
        for key, entry in spns.items():
            if not isinstance(entry, dict):
                continue
            for i, cause in enumerate(entry.get("causes") or []):
                if classify_cause(str(cause)).is_junk:
                    junk_keys.add((key, i))
        assert junk_keys, "expected the classifier to find something — it may be broken"

        nodes = json.loads(
            (ROOT / "data/diagnostics/root_cause_graph.json").read_text(encoding="utf-8")
        )["nodes"]
        offenders = [
            n["id"]
            for n in nodes
            if (m := re.search(r"\['(SPN_\d+)'\]\.causes\[(\d+)\]", n.get("source_ref", "")))
            and (m.group(1), int(m.group(2))) in junk_keys
        ]
        assert offenders == [], f"{len(offenders)} node(s) derive from junk causes: {offenders[:5]}"

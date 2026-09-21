"""T3-2 regresyon: dtcdocs.com kaynaklı kirli düğümler grafta OLMAMALI.

T3-1 bağımsız denetiminin Ç-2 bulgusu:
  ``scripts/derive_root_cause_graph.py --verify`` içindeki kontrol
  ``re.search(r"dtcdocs", ref)`` idi. Bir ``source_ref`` biçimi
  ``j1939_spn_fmi_database.json['spns']['SPN_102'].causes[0]`` olduğu için
  bu grep **yapısal olarak her zaman 0** dönüyordu — yani "0 dtcdocs düğümü"
  güvencesi SAHTEYDİ. Gerçekte canlı DB'de ``source`` alanı ``dtcdocs.com``
  olan 39 SPN, grafta 36 düğüm üretiyordu (SEO/LLM üretimi cümle-slug'lar,
  ör. ``spn-105-advanced-technical-analysis-the-ecm-monitors-the-int``).

Bu test hem düzeltilmiş kontrolü hem de gerçek veri durumunu kilitler (§2.3:
kaynağından şüpheli içerik üretim grafiğine giremez).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GRAPH = ROOT / "data/diagnostics/root_cause_graph.json"
SPN_DB = ROOT / "data/diagnostics/j1939_spn_fmi_database.json"
DERIVE = ROOT / "scripts/derive_root_cause_graph.py"


@pytest.fixture(scope="module")
def graph_nodes() -> list[dict]:
    return json.loads(GRAPH.read_text(encoding="utf-8"))["nodes"]


@pytest.fixture(scope="module")
def dtcdocs_spns() -> set[str]:
    spns = json.loads(SPN_DB.read_text(encoding="utf-8"))["spns"]
    return {
        key
        for key, entry in spns.items()
        if isinstance(entry, dict) and "dtcdocs" in str(entry.get("source", "")).lower()
    }


def test_dtcdocs_source_set_is_non_empty(dtcdocs_spns: set[str]) -> None:
    """The quarantined-source set must be discoverable (guards a silent rename)."""
    assert len(dtcdocs_spns) >= 30, (
        f"expected the live DB to still tag dtcdocs.com SPNs; found {len(dtcdocs_spns)}"
    )


def test_no_graph_node_derives_from_a_dtcdocs_source(
    graph_nodes: list[dict], dtcdocs_spns: set[str]
) -> None:
    """No node may trace back to a quarantined dtcdocs.com SPN."""
    offenders = [
        n["id"]
        for n in graph_nodes
        if (m := re.search(r"\['(SPN_\d+)'\]", n.get("source_ref", "")))
        and m.group(1) in dtcdocs_spns
    ]
    assert offenders == [], (
        f"{len(offenders)} graph node(s) derive from quarantined dtcdocs.com SPNs: "
        f"{sorted(offenders)[:6]}"
    )


def test_no_graph_node_has_seo_harvest_signature(graph_nodes: list[dict]) -> None:
    """The dtcdocs.com LLM/SEO harvest left specific filler prose in node ids.

    Note this checks only the *dtcdocs* signature. A broader junk-source sweep
    (``detroitdieselengines.info`` and similar low-quality aggregators) is a
    separate, larger remediation tracked as T3-3. This test must not be loosened
    to hide T3-3's findings, and T3-3 must not be "fixed" by deleting this test.
    """
    dtcdocs_markers = (
        "advanced-technical-analysis",
        "this-fault-is-typically-triggered",
        "common-causes-include",
        "at-intercooler-connections",
        "fmi-0-hava-giris-basinci-veri-gecerli-fakat-normal",
    )
    suspects = [
        n["id"] for n in graph_nodes if any(m in n["id"] for m in dtcdocs_markers)
    ]
    assert suspects == [], (
        f"{len(suspects)} node id(s) carry the dtcdocs SEO-harvest signature: "
        f"{sorted(suspects)[:5]}"
    )


def test_verify_reports_quarantined_source_honestly() -> None:
    """`--verify` must report 0 dtcdocs nodes *by checking the DATA*, and pass.

    This is the anti-regression for the structural false assurance: the old
    check grepped the source_ref string and could never match.
    """
    out = subprocess.run(
        [sys.executable, str(DERIVE), "--verify"],
        cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=300,
    )
    combined = (out.stdout or "") + (out.stderr or "")
    assert "dtcdocs_sourced_nodes=0" in combined, combined[-1500:]
    assert "unresolved_source_refs=0" in combined, combined[-1500:]
    assert "[OK]" in combined, combined[-1500:]
    assert out.returncode == 0, combined[-1500:]


def test_verify_uses_data_not_string_grep() -> None:
    """The verify source must derive the quarantined set from the DB `source` field."""
    src = DERIVE.read_text(encoding="utf-8")
    assert "dtcdocs_spns" in src, "verify must build a data-driven quarantined set"
    assert 're.search(r"dtcdocs", ref' not in src, (
        "the old string-grep check must not come back — it can never match a source_ref"
    )

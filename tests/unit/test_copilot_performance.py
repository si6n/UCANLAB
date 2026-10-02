"""Performance budgets for the structured copilot (query time, memory, startup).

Budgets are generous on purpose (CI runs under coverage on Windows runners);
they catch regressions of an order of magnitude, not micro-noise. Cold loads
of the 50 MB DTC database are an existing, separately budgeted cost
(test_benchmark_ai_copilot.py) and are excluded by a warm-up.
"""

from __future__ import annotations

import time
import tracemalloc

import pytest

from src.engine.ai.copilot_answer import CopilotOptions, answer_query
from src.engine.ai.knowledge_base import KnowledgeBase, get_knowledge_base
from src.engine.ai.local_search import LocalSearchIndex

pytestmark = pytest.mark.benchmark

WARM_QUERIES = [
    {"text": "P0101"},
    {"text": "motor ısınıyor, DPF lambası yandı"},
    {"dtcs": ["SPN 110 FMI 0"], "telemetry": {"CoolantTemp": 112}},
    {"text": "P0217 P0300 P0171"},
    {"dm1": ["44 FF 6E 00 00 05 FF FF"]},
    {"text": "engine overheting and black smoke"},
    {"dtcs": ["P0AA6"], "telemetry": {"IsolationResistance": 20, "HVPackVoltage": 400}},
]


@pytest.fixture(scope="module", autouse=True)
def warm() -> None:
    for q in WARM_QUERIES:
        answer_query(**q)


def test_knowledge_base_construction_is_instant() -> None:
    t0 = time.perf_counter()
    kb = KnowledgeBase()
    elapsed = time.perf_counter() - t0
    assert elapsed < 0.01
    assert kb.loaded_sources() == []


def test_warm_query_latency() -> None:
    rounds = 5
    t0 = time.perf_counter()
    for _ in range(rounds):
        for q in WARM_QUERIES:
            answer_query(**q)
    per_query_ms = (time.perf_counter() - t0) * 1000 / (rounds * len(WARM_QUERIES))
    assert per_query_ms < 150, f"{per_query_ms:.1f} ms per query"


def test_worst_warm_query_latency() -> None:
    worst = 0.0
    for q in WARM_QUERIES:
        t0 = time.perf_counter()
        answer_query(**q)
        worst = max(worst, time.perf_counter() - t0)
    assert worst < 0.5, f"worst query {worst * 1000:.0f} ms"


def test_warm_query_memory() -> None:
    tracemalloc.start()
    try:
        for q in WARM_QUERIES:
            answer_query(**q)
        _cur, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < 8 * 1024 * 1024, f"peak {peak / 1e6:.1f} MB for warm queries"


def test_local_search_index_build_and_query() -> None:
    idx = LocalSearchIndex(get_knowledge_base())
    t0 = time.perf_counter()
    hits = idx.search("dashboard clock wrong")
    build = time.perf_counter() - t0
    assert build < 10.0, f"index build {build:.1f} s"
    t0 = time.perf_counter()
    idx.search("brake pedal soft")
    assert time.perf_counter() - t0 < 0.5
    assert all(h.ref for h in hits)


def test_local_search_can_be_switched_off(monkeypatch: pytest.MonkeyPatch) -> None:
    off = answer_query("the dashboard clock is wrong", options=CopilotOptions(local_search=False))
    assert off.technical["similar_records"] == []
    monkeypatch.setenv("UCANLAB_COPILOT_LOCAL_SEARCH", "0")
    env_off = answer_query("the dashboard clock is wrong")
    assert env_off.technical["similar_records"] == []
    core = answer_query("SPN 110 FMI 0")
    assert core.causes, "the core must work with the search layer off"

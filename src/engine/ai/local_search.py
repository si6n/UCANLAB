"""Optional local BM25 search over DTC / SPN titles (offline, switchable).

Used only as a *hint* layer: when a free-text complaint matched no canonical
symptom and no code was given, the copilot can show "records whose wording is
similar" — clearly labelled as text similarity, never as a diagnosis, never as
an active code. The deterministic core does not depend on it.

Switches (any one disables it):
* ``CopilotOptions(local_search=False)``
* environment ``UCANLAB_COPILOT_LOCAL_SEARCH=0``

Pure Python BM25 (k1=1.5, b=0.75); the index is built lazily on first use from
the already-loaded knowledge base, so it adds nothing to startup time.
"""

from __future__ import annotations

import math
import os
import threading
from collections import Counter
from dataclasses import dataclass

from src.engine.ai.knowledge_base import KnowledgeBase, fold_text

__all__ = ["LocalSearchIndex", "SearchHit", "local_search_enabled"]

_STOP = frozenset({
    "the", "and", "or", "of", "a", "an", "in", "on", "to", "for", "is", "with", "ve", "veya", "ile", "bir",
    "bu", "da", "de", "mi", "icin", "circuit", "devre", "devresi", "sensor", "sensoru",
})


def local_search_enabled(flag: bool | None = None) -> bool:
    if flag is False:
        return False
    return os.environ.get("UCANLAB_COPILOT_LOCAL_SEARCH", "1").strip() not in ("0", "false", "off", "no")


def _tokens(text: str) -> list[str]:
    return [t for t in fold_text(text).split() if len(t) > 2 and t not in _STOP and not t.isdigit()]


@dataclass(frozen=True, slots=True)
class SearchHit:
    ref: str
    title: str
    score: float


class LocalSearchIndex:
    """BM25 over DTC titles (TR/EN) and SPN names (TR/EN)."""

    def __init__(self, kb: KnowledgeBase) -> None:
        self._kb = kb
        self._lock = threading.Lock()
        self._built = False
        self._docs: list[tuple[str, str, Counter[str], int]] = []
        self._df: Counter[str] = Counter()
        self._avg = 1.0

    def _build(self) -> None:
        with self._lock:
            if self._built:
                return
            table = self._kb._dtc_map() or {}  # noqa: SLF001 — same package
            for code in sorted(table):
                rec = table[code]
                title = str(rec.get("title_tr") or rec.get("title") or "")
                text = " ".join(str(rec.get(k) or "") for k in ("title", "title_en", "title_tr"))
                self._add(f"dtc_database#{code}", f"{code} — {title}", text)
            j = self._kb._j1939() or {}  # noqa: SLF001
            for key in sorted(j.get("spns") or {}):
                rec = j["spns"][key]
                if not isinstance(rec, dict):
                    continue
                text = f"{rec.get('name') or ''} {rec.get('title_tr') or ''}"
                self._add(f"j1939_spn_fmi#{key}", f"SPN {rec.get('spn')} — {rec.get('title_tr') or rec.get('name')}", text)
            self._avg = (sum(n for *_, n in self._docs) / len(self._docs)) if self._docs else 1.0
            self._built = True

    def _add(self, ref: str, title: str, text: str) -> None:
        toks = _tokens(text)
        if not toks:
            return
        tf = Counter(toks)
        self._docs.append((ref, title, tf, len(toks)))
        self._df.update(tf.keys())

    def search(self, query: str, k: int = 3, min_score: float = 4.0) -> list[SearchHit]:
        q = list(dict.fromkeys(_tokens(query)))
        if not q:
            return []
        self._build()
        n = len(self._docs)
        idf = {t: math.log(1 + (n - self._df[t] + 0.5) / (self._df[t] + 0.5)) for t in q if self._df[t]}
        if not idf:
            return []
        scored: list[SearchHit] = []
        for ref, title, tf, length in self._docs:
            s = 0.0
            for t, w in idf.items():
                f = tf.get(t)
                if f:
                    s += w * f * 2.5 / (f + 1.5 * (0.25 + 0.75 * length / self._avg))
            if s >= min_score:
                scored.append(SearchHit(ref, title, round(s, 2)))
        scored.sort(key=lambda h: (-h.score, h.ref))
        return scored[:k]

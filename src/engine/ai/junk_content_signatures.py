# -*- coding: utf-8 -*-
"""T3-3 — Content-signature classifier for junk diagnostic ``causes[]`` text.

WHY THIS MODULE EXISTS
----------------------
The T1-2/T2-3 quarantine sweep targeted only ``dtcdocs.com`` and hand-excluded
two SPNs. The T3-1 audit proved the ``--verify`` guarantee
``dtcdocs_sourced_nodes=0`` was a **structural false assurance** (it grepped a
``source_ref`` for a substring that a ``source_ref`` can never contain). The
real state was 39 dtcdocs SPNs producing 36 graph nodes. T3-2 fixed that and
exposed the same contamination class in other harvests — SEO/marketing filler
and machine-generated prose that no *source name* reveals.

This module is the measurement instrument for that sweep. It classifies a
single cause string by **content signature**, never by source name alone,
because quality is MIXED inside a single SPN. Live example, ``SPN_103``
(``source=detroitdieselengines.info``)::

    [0] "Sensor Gap Check: Measure air gap between speed sensor tip and ..."  <- legitimate
    [3] "Experienced technicians verify turbocharger shaft endplay using ..." <- SEO/LLM FILLER
    [4] "Common field repairs involve connector cleaning with dielectric ..." <- SEO/LLM FILLER
    [5] "Preventive maintenance includes regular inspection of oil feed ..."   <- SEO/LLM FILLER

DESIGN RULES
------------
1. **Deterministic and offline.** Pure string logic; no LLM, no network, no
   I/O. Importable from ``scripts/`` and from tests (and, deliberately, from
   ``src/engine/ai/**`` — it must satisfy the AI TX-isolation scan).
2. **Conservative.** A signature fires only on an explicit marker or on a
   structural property that a genuine cause cannot have (e.g. an encoding
   replacement character). Anything arguable is scored ``clean`` so legitimate
   evidence is never destroyed to remove junk (see the T3-3 brief: "destroying
   legitimate evidence to remove junk is the wrong trade").
3. **Auditable.** Every verdict names the signature that fired, so a human can
   re-derive the decision from the quarantine record alone.
"""

from __future__ import annotations

import re
from typing import NamedTuple

__all__ = [
    "SIGNATURE_CATALOG",
    "CauseVerdict",
    "classify_cause",
    "is_dirty_source",
    "normalize_for_match",
]
# ---------------------------------------------------------------------------
# Signature catalog — the single source of truth, written verbatim into the
# quarantine file so a later agent can reproduce the sweep from the ledger.
# ---------------------------------------------------------------------------

#: Explicit SEO / marketing / machine-filler phrases. Matched case-insensitively
#: on a whitespace-normalized copy of the cause. Every entry was observed in the
#: live database during the T3-3 measurement (see the sweep report for counts).
SEO_FILLER_PHRASES: tuple[str, ...] = (
    "advanced technical analysis",
    "experienced technicians",
    "common field repairs involve",
    "preventive maintenance includes",
    "this fault is typically triggered",
    "common causes include",
    "in this article",
    "read more",
    "click here",
    "our experts",
)

#: Link / attribution residue that is never part of a fault mechanism.
RESIDUE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"https?://", re.I),
    re.compile(r"www\.", re.I),
    re.compile(r"\.com\b", re.I),
    re.compile(r"©"),
    re.compile(r"all rights reserved", re.I),
)

#: Mojibake signature. A mis-decoded scrape surfaces U+FFFD (the encoding
#: replacement character) or the doubled Latin-1/UTF-8 artefact ``SÄ±vÄ±``
#: observed in ``SPN_3364``. A genuine published cause never carries either.
#   Written with explicit \u escapes: the artefact only exists as mojibake, and
#   a raw literal in this file would itself be at the mercy of the editor's
#   encoding (observed while authoring the module).
MOJIBAKE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile("\ufffd"),  # U+FFFD encoding replacement character
    re.compile("S\u00c4\u00b1v\u00c4\u00b1"),  # "SÄ±vÄ±" — T2/SPN_3364 artefact
    re.compile("\u00c3[\u0080-\u00bf]"),  # UTF-8 read as Latin-1 (Ã + continuation)
    re.compile("\u00c2[\u0080-\u00bf]"),  # UTF-8 read as Latin-1 (Â + continuation)
)

#: Phrases that merely ANNOUNCE causes instead of naming one.
#:
#: MEASURED FALSE-POSITIVE, T3-3 (do not re-broaden): an earlier revision fired
#: this rule on "announcement phrase + >= 12 words" and marked two legitimate
#: causes as junk —
#:   SPN_4335[3] "Electrical Circuit Fault: Damaged wiring or poor connections can disrupt signal ..."
#:   SPN_61683[3] "Wiring harness damage: Heat from exhaust aftertreatment system melted wire jacke ..."
#: Both are real fault mechanisms; the word threshold, not the content, decided
#: them. A phrase on this list is now junk ONLY when the line carries no fault
#: noun at all, which is what ``_FAULT_NOUN_RE`` below encodes.
ANNOUNCEMENT_PHRASES: tuple[str, ...] = (
    "common problems",
    "possible causes",
    "some common causes",
)

#: A line that names a concrete fault subject (component, sensor, circuit,
#: connector, wiring, module, valve, pump, injector, ...) is a CAUSE, whatever
#: framing prose surrounds it. Presence of one of these blocks the announcement
#: rule. This is the guard the T3-3 false positive proved was missing.
_FAULT_NOUN_RE = re.compile(
    r"\b("
    r"sensor|switch|circuit|connector|connector?s|wiring|harness|wire|"
    r"module|ecm|pcm|valve|pump|injector|actuator|solenoid|relay|fuse|"
    r"bearing|seal|gasket|filter|radiator|thermostat|turbocharger|"
    r"battery|alternator|motor|coil|plug|nozzle|linkage|hose|line|pipe|"
    r"ground|terminal|pin|resistor|capacitor|diode|transistor|driver|"
    r"voltage|pressure|temperature|signal|data\s+link|can\s+bus|"
    r"piston|cylinder|gear|shaft|clutch|brake|exhaust|egr|dpf|scr|def"
    r")\b",
    re.I,
)

_WHITESPACE_RE = re.compile(r"\s+")


class CauseVerdict(NamedTuple):
    """Result of classifying one cause string."""

    is_junk: bool
    #: The signature that fired (``""`` when ``is_junk`` is False).
    signature: str
    #: The matched marker text, for the quarantine ledger (``""`` if structural).
    marker: str


def normalize_for_match(text: str) -> str:
    """Lowercase + collapse whitespace (and NBSP) so markers match reliably."""
    return _WHITESPACE_RE.sub(" ", text.replace("\u00a0", " ")).strip().lower()


def _looks_like_a_cause(text: str) -> bool:
    """True when ``text`` alone names a concrete fault subject.

    Used to protect the leading clause of a concatenated record (a real cause
    followed by an SEO section). Requires a fault noun AND at least three words,
    so a dangling fragment like ``"see also"`` is not mistaken for a cause.
    """
    head = text.strip().rstrip(".:;,-")
    return len(head.split()) >= 3 and bool(_FAULT_NOUN_RE.search(head))


def classify_cause(text: str) -> CauseVerdict:
    """Classify a single cause string as ``junk`` or ``clean`` by content.

    Returns a :class:`CauseVerdict`. Order of evaluation is explicit and
    deterministic: residue and mojibake are structural (highest confidence),
    then SEO filler, then the LLM-prose length signature.
    """
    if not isinstance(text, str) or not text.strip():
        # An empty cause carries no information; it is not "junk content" and
        # removing it would be an unmeasured data change. Left as clean.
        return CauseVerdict(False, "", "")

    for pat in RESIDUE_PATTERNS:
        m = pat.search(text)
        if m:
            return CauseVerdict(True, "link_or_attribution_residue", m.group(0))

    for pat in MOJIBAKE_PATTERNS:
        m = pat.search(text)
        if m:
            return CauseVerdict(True, "mojibake_bad_encoding", m.group(0))

    low = normalize_for_match(text)
    for phrase in SEO_FILLER_PHRASES:
        if phrase in low:
            # MEASURED: a harvest sometimes CONCATENATES a genuine cause with a
            # trailing SEO section into one record. Live examples from the T3-3
            # sweep, both flagged on the marker "advanced technical analysis":
            #   SPN_4335[3]:  "Electrical Circuit Fault: Damaged wiring or poor
            #                  connections can disrupt signal transmission from
            #                  sensors to the ECM. Advanced Technical Analysis
            #                  The ECM microcontroller employs logic to ..."
            #   SPN_61683[3]: "Wiring harness damage: Heat from exhaust
            #                  aftertreatment system melted wire jacket near
            #                  DPF. Advanced Technical Analysis The ECM monitors..."
            # The leading clause is a real fault mechanism; discarding the whole
            # record would destroy it. Rule: if the text BEFORE the marker is
            # itself a usable cause, the record is CLEAN (the trailing filler is
            # the harvesting tool's problem, not a reason to delete evidence).
            #
            # The head is sliced from ``low`` — the SAME normalized copy the
            # phrase matched — never from the raw text. ``find()`` on the raw
            # lowercased text returns -1 when the marker's internal whitespace
            # differs from the collapsed form ("Advanced  Technical"), and
            # ``text[:-1]`` then degenerated to the WHOLE text, so
            # ``_looks_like_a_cause`` saw the leading fault nouns of the filler
            # itself and the SEO rule was defeated for whitespace variants.
            head = low[: low.find(phrase)]
            if _looks_like_a_cause(head):
                return CauseVerdict(False, "", "")
            return CauseVerdict(True, "seo_marketing_filler", phrase)

    # An announcement phrase with no fault content beyond it is scaffold prose,
    # not a cause. The ``_FAULT_NOUN_RE`` guard is REQUIRED: without it the rule
    # marked legitimate mechanisms as junk (SPN_4335[3], SPN_61683[3]) purely on
    # line length — see the note on ANNOUNCEMENT_PHRASES.
    for phrase in ANNOUNCEMENT_PHRASES:
        if phrase in low and len(low.split()) >= 12 and not _FAULT_NOUN_RE.search(text):
            return CauseVerdict(True, "seo_marketing_filler", phrase)

    return CauseVerdict(False, "", "")


# ---------------------------------------------------------------------------
# Source-level rule (layer (a) of the sweep)
# ---------------------------------------------------------------------------

#: Source tags whose harvest is machine-generated END TO END — i.e. measured to
#: be junk in (near) full, so no legitimate row is destroyed by removing it all.
#:
#: ``dtcdocs.com`` qualifies and is listed even though T3-2 already removed its
#: nodes: keeping it here makes this rule self-sufficient rather than silently
#: depending on an earlier task's edit.
#:
#: ``detroitdieselengines.info`` was in an earlier draft of this list and was
#: REMOVED on measurement. The T3-3 card called it SUSPECT, but the sweep
#: measured only **24 of its 320 causes (7.5 %) as junk** — the other 296 are
#: genuine Detroit Diesel service procedures ("Corroded or loose power supply
#: to ..."). Quarantining that source wholesale would have destroyed ~296
#: legitimate causes to remove 24. It is therefore handled by the cause-level
#: rule (layer b), which is exactly the SPN_103 mixed-quality case.
DIRTY_SOURCE_PATTERNS: tuple[re.Pattern[str], ...] = (re.compile(r"dtcdocs", re.I),)


def is_dirty_source(source: str) -> bool:
    """True when every SPN from ``source`` is quarantined wholesale.

    Deliberately narrow: only harvests measured to be machine-generated in
    full. Sources that merely *contain* some filler are handled by the
    cause-level rule so their legitimate rows survive (``SPN_103`` case).
    """
    if not isinstance(source, str) or not source.strip():
        return False
    return any(p.search(source) for p in DIRTY_SOURCE_PATTERNS)


#: Human-readable catalog written into the quarantine ledger. Kept as a plain
#: data structure so the ledger and the code cannot drift.
SIGNATURE_CATALOG: dict[str, object] = {
    "seo_filler_phrases": list(SEO_FILLER_PHRASES),
    "announcement_phrases": list(ANNOUNCEMENT_PHRASES),
    "residue_patterns": [p.pattern for p in RESIDUE_PATTERNS],
    "mojibake_patterns": [p.pattern for p in MOJIBAKE_PATTERNS],
    "dirty_source_patterns": [p.pattern for p in DIRTY_SOURCE_PATTERNS],
    "layer_a_source_rule": (
        "source field matches a dirty_source_pattern -> every cause of every SPN "
        "from that source is quarantined"
    ),
    "layer_b_cause_rule": (
        "classify_cause(text).is_junk -> only that cause index is quarantined "
        "(a legitimate source can carry mixed rows, e.g. SPN_103)"
    ),
}

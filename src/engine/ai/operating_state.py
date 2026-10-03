"""Operating state of the vehicle when the evidence was taken (copilot scenario layer).

A reading only means something in its state: 12.3 V is a healthy resting
battery but a dead alternator with the engine running; 8 kPa DPF differential
pressure is a clogged filter at idle but normal at full load. This module
infers that state deterministically from what the request actually carries:

* engine: off | cranking | idle | running | load | unknown
* thermal: cold | warm | unknown
* system_voltage: 12 | 24 | None

Measured values win over words; words win over nothing. Every decision keeps
its source ("EngineSpeed=750", "text:rolantide") so the answer can show why the
copilot judged a reading the way it did. Nothing is guessed: no evidence keeps
the field "unknown".
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from src.engine.ai.knowledge_base import fold_text
from src.engine.ai.query_understanding import ParsedQuery, Reading

__all__ = ["OperatingState", "infer_state", "infer_state_from"]

# Folded word cues (Turkish + English). Longer phrases first where they overlap.
_ENGINE_CUES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("cranking", ("marsta", "mars basarken", "mars sirasinda", "marsa basilirken", "while cranking", "cranking")),
    ("off", ("kontak kapali", "motor kapali", "motor dururken", "motor calismiyorken", "motor durmusken",
             "engine off", "ignition off", "engine stopped")),
    ("load", ("yukte", "yuk altinda", "tam gaz", "gaza basinca", "gaza bastigimda", "hizlanirken", "yokusta",
              "under load", "full throttle", "accelerating", "uphill")),
    ("idle", ("rolantide", "rolanti", "at idle", "idling")),
    ("running", ("seyir halinde", "seyirde", "giderken", "surerken", "yolda", "while driving", "on the road",
                 "motor calisirken", "engine running")),
)
_THERMAL_CUES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("cold", ("sogukken", "soguk motor", "sabah ilk", "ilk calistirmada", "cold start", "cold engine", "when cold")),
    ("warm", ("sicakken", "isininca", "isindiktan sonra", "isinmis", "motor sicak", "uzun yoldan sonra",
              "when warm", "hot engine", "warmed up", "after driving")),
)
_VOLT_RE = re.compile(r"(?<![0-9])(12|24)\s*(?:v|volt)\b")


@dataclass(slots=True)
class OperatingState:
    engine: str = "unknown"
    thermal: str = "unknown"
    system_voltage: int | None = None
    sources: list[str] = field(default_factory=list)  # "engine:EngineSpeed=750", "thermal:text:sicakken"

    @property
    def known(self) -> bool:
        return self.engine != "unknown" or self.thermal != "unknown"

    def source_of(self, part: str) -> str:
        return next((s.split(":", 1)[1] for s in self.sources if s.startswith(f"{part}:")), "")


def _cue(folded: str, table: tuple[tuple[str, tuple[str, ...]], ...]) -> tuple[str, str] | None:
    padded = f" {folded} "
    for state, words in table:
        for w in words:
            if f" {w}" in padded:
                return state, w
    return None


def infer_state(parsed: ParsedQuery) -> OperatingState:
    return infer_state_from(parsed.readings, parsed.text)


def infer_state_from(readings: Iterable[Reading], text: str = "") -> OperatingState:
    """Same inference over any set of readings (live, or a freeze frame: the fault moment)."""
    st = OperatingState()
    values = {r.canonical: r.value for r in readings}
    folded = fold_text(text)

    rpm = values.get("EngineSpeed")
    load = values.get("EngineLoad")
    if rpm is not None:
        if rpm < 50:
            st.engine = "off"
        elif rpm < 400:
            st.engine = "cranking"
        elif load is not None and load >= 70:
            st.engine = "load"
        elif rpm <= 1100:
            st.engine = "idle"
        else:
            st.engine = "running"
        st.sources.append(f"engine:EngineSpeed={rpm:g}")
    else:
        hit = _cue(folded, _ENGINE_CUES)
        if hit:
            st.engine = hit[0]
            st.sources.append(f"engine:text:{hit[1]}")
        elif load is not None and load >= 70:
            st.engine = "load"
            st.sources.append(f"engine:EngineLoad={load:g}")

    coolant = values.get("CoolantTemp")
    hit = _cue(folded, _THERMAL_CUES)
    if hit:
        st.thermal = hit[0]
        st.sources.append(f"thermal:text:{hit[1]}")
    elif coolant is not None:
        if coolant >= 70:
            st.thermal = "warm"
        elif coolant < 50:
            st.thermal = "cold"
        if st.thermal != "unknown":
            st.sources.append(f"thermal:CoolantTemp={coolant:g}")

    m = _VOLT_RE.search(text.lower())
    if m and ("sistem" in folded or "system" in folded or "arac" in folded or "truck" in folded or "kamyon" in folded):
        st.system_voltage = int(m.group(1))
        st.sources.append(f"voltage:text:{m.group(0)}")
    elif "BatteryVoltage" in values:
        st.system_voltage = 24 if values["BatteryVoltage"] > 18 else 12
        st.sources.append(f"voltage:BatteryVoltage={values['BatteryVoltage']:g}")
    return st

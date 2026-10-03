"""Single read-only knowledge access layer for the offline diagnostic copilot.

Why this module exists
----------------------
Before it, every consumer reached into ``data/`` on its own: the copilot read
the DTC/J1939/PID/Mode 06/NHTSA files, the hypothesis engine read the
root-cause graph and the signal aliases, the anomaly detector read the
telemetry thresholds, and several shipped data sets (``canonical_symptoms``,
``system_taxonomy``, ``hv_safety_thresholds``, ``canboat_pgn_reference``,
``dtc_database_oem_layer``) had **no consumer at all**. ``KnowledgeBase`` puts
all of them behind one façade so the copilot can answer from every source.

Design rules
------------
1. **Lazy.** Constructing a ``KnowledgeBase`` reads nothing. A source is
   loaded the first time a lookup needs it, then cached for the life of the
   instance (thread-safe, one lock per source). Startup cost stays zero.
2. **Reuse the validated loaders.** Sources that already had a fail-closed
   loader (DTC merge + quarantine gate, J1939 DB, root-cause graph schema,
   threshold schema, procedure schema, golden cases) are read *through* that
   loader, never re-parsed here, so the copilot sees exactly the data the rest
   of the product sees.
3. **Honest misses.** Every lookup returns a :class:`Lookup` whose ``found``
   flag is explicit; a miss carries a reason (``not_found`` /
   ``source_unavailable`` / ``invalid_key``) and never a guessed record.
4. **Traceable.** Every hit carries a ``ref`` (``"<file>#<key>"``) that the
   answer composer prints next to the claim it supports, and
   :meth:`KnowledgeBase.resolve_ref` can re-resolve that ref later (used by the
   no-fabrication tests).
5. **Read-only and offline.** No writes, no network, no TX path. The module
   imports only the AI package, the core models and the standard library
   (enforced by ``tests/safety/test_ai_tx_isolation.py``).
"""

from __future__ import annotations

import json
import logging
import re
import sys
import threading
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("universal_can.engine.ai.knowledge_base")

__all__ = [
    "KnowledgeBase",
    "Lookup",
    "SOURCE_FILES",
    "fold_text",
    "get_knowledge_base",
    "normalize_dtc_code",
]

# ---------------------------------------------------------------------------
# Source registry. Key = short source id used in refs and the inventory.
# Value = path relative to ``data/``. Order is the documentation order.
# ---------------------------------------------------------------------------
SOURCE_FILES: dict[str, str] = {
    "dtc_database": "diagnostics/dtc_database.json",
    "dtc_oem_layer": "diagnostics/dtc_database_oem_layer.json",
    "j1939_spn_fmi": "diagnostics/j1939_spn_fmi_database.json",
    "extended_pid": "diagnostics/extended_pid_database.json",
    "obd_mode06": "diagnostics/obd_mode06_database.json",
    "uds_did": "diagnostics/uds_did_database.json",
    "canonical_symptoms": "diagnostics/canonical_symptoms.json",
    "symptom_lexicon": "diagnostics/symptom_lexicon.json",
    "symptom_checks": "diagnostics/symptom_checks.json",
    "subsystem_labels_en": "diagnostics/subsystem_labels_en.json",
    "graph_title_i18n": "diagnostics/graph_title_i18n.json",
    "reasoning_rules": "diagnostics/reasoning_rules.json",
    "operating_scenarios": "diagnostics/operating_scenarios.json",
    "root_cause_graph": "diagnostics/root_cause_graph.json",
    "signal_aliases": "diagnostics/signal_aliases.json",
    "signal_measurement_map": "diagnostics/signal_measurement_map.json",
    "system_taxonomy": "diagnostics/system_taxonomy.json",
    "telemetry_thresholds": "diagnostics/telemetry_thresholds.json",
    "hv_safety_thresholds": "diagnostics/hv_safety_thresholds.json",
    "dtc_severity_rules": "diagnostics/dtc_severity_rules.json",
    "nhtsa_recalls": "diagnostics/nhtsa_can_recalls_database.json",
    "nhtsa_complaints": "diagnostics/nhtsa_can_complaints_database.json",
    "canboat_pgn": "diagnostics/canboat_pgn_reference.json",
    "user_kb": "diagnostics/user_kb.json",
    "copilot_glossary": "diagnostics/copilot_glossary.json",
    "dtc_procedures": "knowledge/dtc_procedures",
    "dbc_catalog": "dbc/catalog.json",
    "golden_cases": "golden_traces/cases",
}

_TR_FOLD = str.maketrans({
    "ı": "i", "İ": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g",
    "ü": "u", "Ü": "u", "ö": "o", "Ö": "o", "ç": "c", "Ç": "c",
})
_NON_WORD_RE = re.compile(r"[^0-9a-z]+")
# A signal named by its protocol identifier instead of a name, as bus tools and
# exported logs often do: "SPN 110", "SPN_190", "J1939 SPN 102", "PID 0C",
# "PID 0x05", "01 PID 0C". Matched on fold_text() output.
_SPN_KEY_RE = re.compile(r"^(?:j1939 )?spn ?(\d{1,6})$")
_PID_KEY_RE = re.compile(r"^(?:obd )?(?:(?:mode |service )?0?1 )?pid ?(?:0x)?([0-9a-f]{1,2})$")


def fold_text(text: str) -> str:
    """Lower-case, Turkish-fold and de-accent ``text``; collapse punctuation.

    ``"Motor ISINIYOR, DPF lambası!"`` -> ``"motor isiniyor dpf lambasi"``.
    The same folding is applied to the data side and the query side, so a
    keyword written with or without Turkish characters matches either way.
    """
    folded = str(text).translate(_TR_FOLD).lower()
    folded = unicodedata.normalize("NFKD", folded)
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return " ".join(_NON_WORD_RE.sub(" ", folded).split())


# SAE J2012: the second character is 0-3 (0/2 generic, 1/3 manufacturer). The
# shipped DB holds two malformed "C6xxx" rows (garbled PDF titles, see
# docs/audit/copilot_data_quality_2026-10-02.md); they are not valid codes.
_DTC_RE = re.compile(r"^([PBCU])([0-3][0-9A-F]{3})$")


def normalize_dtc_code(raw: str) -> str | None:
    """Canonical ``P0101`` form of an OBD/UDS-style DTC, or ``None``.

    Tolerates lower case, inner spaces/dashes and the common letter-O-for-zero
    typo (``PO101``). Anything that does not end up as one letter + four hex
    digits is rejected rather than guessed.
    """
    cleaned = re.sub(r"[\s\-_]", "", str(raw)).upper()
    if len(cleaned) == 5 and cleaned[0] in "PBCU":
        cleaned = cleaned[0] + cleaned[1:].replace("O", "0")
    return cleaned if _DTC_RE.match(cleaned) else None


@dataclass(frozen=True, slots=True)
class Lookup:
    """Result of one knowledge lookup. ``found`` is never implied."""

    found: bool
    source: str
    key: str
    record: Any = None
    reason: str = ""

    @property
    def ref(self) -> str:
        """Stable citation ``"<source>#<key>"`` printed next to a claim."""
        return f"{self.source}#{self.key}"

    @staticmethod
    def miss(source: str, key: str, reason: str = "not_found") -> Lookup:
        return Lookup(False, source, key, None, reason)


def _resolve_data_dir() -> Path:
    """``data/`` root for a source checkout or a PyInstaller bundle."""
    if getattr(sys, "frozen", False):
        frozen = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)).resolve() / "data"
        if frozen.is_dir():
            return frozen
    return Path(__file__).resolve().parents[3] / "data"


def _read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


_SPN_KEY_RE = re.compile(r"^SPN[\s_]?(\d{1,6})$", re.IGNORECASE)


@dataclass(slots=True)
class _SourceState:
    lock: threading.Lock = field(default_factory=threading.Lock)
    loaded: bool = False
    value: Any = None
    error: str = ""


class KnowledgeBase:
    """Lazy, cached, indexed façade over every shipped knowledge source.

    Use :func:`get_knowledge_base` for the process-wide instance. Tests may
    build their own instance with ``data_dir`` pointing at a fixture tree.
    """

    def __init__(self, data_dir: Path | None = None) -> None:
        self._data_dir = Path(data_dir) if data_dir is not None else _resolve_data_dir()
        self._states: dict[str, _SourceState] = {}
        self._states_lock = threading.Lock()

    # ------------------------------------------------------------------ infra
    @property
    def data_dir(self) -> Path:
        return self._data_dir

    def _state(self, name: str) -> _SourceState:
        with self._states_lock:
            state = self._states.get(name)
            if state is None:
                state = _SourceState()
                self._states[name] = state
            return state

    def _load(self, name: str, loader: Callable[[], Any]) -> Any:
        """Run ``loader`` once per source; cache the value or the failure."""
        state = self._state(name)
        if state.loaded:
            return state.value
        with state.lock:
            if not state.loaded:
                try:
                    state.value = loader()
                except Exception as exc:  # noqa: BLE001 — a broken source must not kill the copilot
                    logger.warning("knowledge source unavailable", extra={"source": name, "error": str(exc)})
                    state.value = None
                    state.error = f"{type(exc).__name__}: {exc}"
                state.loaded = True
        return state.value

    def _path(self, source: str) -> Path:
        return self._data_dir / SOURCE_FILES[source]

    def _json_source(self, source: str) -> Any:
        return self._load(source, lambda: _read_json(self._path(source)))

    def loaded_sources(self) -> list[str]:
        """Sources read so far (successfully or not) — startup-cost probe."""
        with self._states_lock:
            return sorted(name for name, st in self._states.items() if st.loaded)

    def source_inventory(self) -> list[dict[str, Any]]:
        """Which sources exist on disk and whether they loaded (no forced load)."""
        rows: list[dict[str, Any]] = []
        for name, rel in SOURCE_FILES.items():
            path = self._data_dir / rel
            state = self._states.get(name)
            rows.append({
                "source": name,
                "path": f"data/{rel}",
                "present": path.exists(),
                "loaded": bool(state and state.loaded and state.value is not None),
                "error": state.error if state else "",
            })
        return rows

    # -------------------------------------------------------------- DTC layer
    def _dtc_map(self) -> dict[str, dict[str, Any]] | None:
        def load() -> dict[str, dict[str, Any]]:
            # Read THROUGH the copilot's fail-closed merge (shape gate +
            # quarantine + severity reconciliation) — the same view the rest of
            # the product uses. Only the shipped data dir takes this path; a
            # fixture tree is read directly.
            if self._data_dir == _resolve_data_dir():
                from src.engine.ai import diagnostic_copilot as dc

                dc.ensure_external_dtc_database_loaded()
                return {k: v for k, v in dc.EXPERT_KNOWLEDGE_BASE.items() if isinstance(v, dict)}
            raw = _read_json(self._path("dtc_database"))
            return {k: v for k, v in raw.items() if isinstance(v, dict)}

        value = self._load("dtc_database", load)
        return value if isinstance(value, dict) else None

    def dtc(self, raw_code: str) -> Lookup:
        """DTC record for an OBD/UDS code (``P0101``) — merged, validated view."""
        code = normalize_dtc_code(raw_code)
        if code is None:
            return Lookup.miss("dtc_database", str(raw_code), "invalid_key")
        table = self._dtc_map()
        if table is None:
            return Lookup.miss("dtc_database", code, "source_unavailable")
        record = table.get(code)
        if record is None:
            return Lookup.miss("dtc_database", code)
        return Lookup(True, "dtc_database", code, record)

    def dtc_oem(self, raw_code: str) -> Lookup:
        """OEM-layer entry (Wal33D MIT) for a code: generic text + OEM makes."""
        code = normalize_dtc_code(raw_code)
        if code is None:
            return Lookup.miss("dtc_oem_layer", str(raw_code), "invalid_key")
        layer = self._json_source("dtc_oem_layer")
        if not isinstance(layer, dict):
            return Lookup.miss("dtc_oem_layer", code, "source_unavailable")
        entry = (layer.get("codes") or {}).get(code)
        makes = (layer.get("oem_index") or {}).get(code) or []
        if entry is None and not makes:
            return Lookup.miss("dtc_oem_layer", code)
        return Lookup(True, "dtc_oem_layer", code, {"entry": entry or {}, "makes": list(makes)})

    def dtc_count(self) -> int:
        table = self._dtc_map()
        return len(table) if table else 0

    # ------------------------------------------------------------ J1939 layer
    def _j1939(self) -> dict[str, Any] | None:
        def load() -> dict[str, Any]:
            if self._data_dir == _resolve_data_dir():
                from src.engine.ai import diagnostic_copilot as dc

                db = dc.get_j1939_spn_database()
                if db:
                    return db
            raw = _read_json(self._path("j1939_spn_fmi"))
            if not isinstance(raw, dict):
                raise ValueError("j1939 database root must be an object")
            return raw

        value = self._load("j1939_spn_fmi", load)
        return value if isinstance(value, dict) else None

    def spn(self, spn: int, fmi: int | None = None) -> Lookup:
        """SPN record; with ``fmi`` the record is ``{"spn": ..., "fmi": ...}``.

        The FMI part comes from the SPN's own ``fault_matrix`` when present,
        otherwise from the generic SAE J1939-73 FMI definition table. Its
        ``fmi_source`` says which, so the answer never presents a generic FMI
        meaning as SPN-specific.
        """
        key = f"SPN_{int(spn)}"
        if not 0 <= int(spn) <= 524287:
            return Lookup.miss("j1939_spn_fmi", key, "invalid_key")
        db = self._j1939()
        if db is None:
            return Lookup.miss("j1939_spn_fmi", key, "source_unavailable")
        record = (db.get("spns") or {}).get(key)
        if fmi is None:
            if record is None:
                return Lookup.miss("j1939_spn_fmi", key)
            return Lookup(True, "j1939_spn_fmi", key, record)
        if not 0 <= int(fmi) <= 31:
            return Lookup.miss("j1939_spn_fmi", f"{key}.FMI_{fmi}", "invalid_key")
        fmi_entry: dict[str, Any] | None = None
        fmi_source = ""
        if record is not None:
            matrix = record.get("fault_matrix") or {}
            if isinstance(matrix, dict) and isinstance(matrix.get(str(fmi)), dict):
                fmi_entry = matrix[str(fmi)]
                fmi_source = "fault_matrix"
        if fmi_entry is None:
            generic = (db.get("fmi_definitions") or {}).get(str(fmi))
            if isinstance(generic, dict):
                fmi_entry = generic
                fmi_source = "fmi_definitions"
        if record is None and fmi_entry is None:
            return Lookup.miss("j1939_spn_fmi", f"{key}.FMI_{fmi}")
        return Lookup(
            record is not None,
            "j1939_spn_fmi",
            f"{key}.FMI_{fmi}",
            {"spn": record, "fmi": fmi_entry, "fmi_source": fmi_source, "fmi_number": int(fmi)},
            "" if record is not None else "spn_not_found",
        )

    def fmi_definition(self, fmi: int) -> Lookup:
        db = self._j1939()
        if db is None:
            return Lookup.miss("j1939_spn_fmi", f"fmi_definitions.{fmi}", "source_unavailable")
        entry = (db.get("fmi_definitions") or {}).get(str(int(fmi)))
        if not isinstance(entry, dict):
            return Lookup.miss("j1939_spn_fmi", f"fmi_definitions.{fmi}")
        return Lookup(True, "j1939_spn_fmi", f"fmi_definitions.{fmi}", entry)

    def spns_for_pgn(self, pgn: int) -> list[int]:
        """SPN numbers the J1939 DB associates with ``pgn`` (sorted)."""
        index = self._load("_idx_pgn_spn", self._build_pgn_spn_index)
        return list(index.get(int(pgn), ())) if isinstance(index, dict) else []

    def _build_pgn_spn_index(self) -> dict[int, tuple[int, ...]]:
        db = self._j1939() or {}
        acc: dict[int, set[int]] = {}
        for rec in (db.get("spns") or {}).values():
            if not isinstance(rec, dict) or not isinstance(rec.get("spn"), int):
                continue
            pgns: list[Any] = []
            if rec.get("associated_pgn") is not None:
                pgns.append(rec.get("associated_pgn"))
            extra = rec.get("associated_pgn_all")
            if isinstance(extra, list):
                pgns.extend(extra)
            for pgn in pgns:
                try:
                    acc.setdefault(int(pgn), set()).add(int(rec["spn"]))
                except (TypeError, ValueError):
                    continue
        return {k: tuple(sorted(v)) for k, v in acc.items()}

    # ---------------------------------------------------------------- PGN layer
    def pgn(self, pgn: int) -> Lookup:
        """PGN description: canboat (NMEA 2000 / DM1) and J1939-DB acronyms."""
        key = f"PGN_{int(pgn)}"
        canboat = self._json_source("canboat_pgn")
        records: list[dict[str, Any]] = []
        if isinstance(canboat, dict):
            by_number = canboat.get("pgns_by_number") or {}
            idxs = by_number.get(str(int(pgn)))
            pgns = canboat.get("pgns") or []
            if isinstance(idxs, list):
                for i in idxs:
                    if isinstance(i, int) and 0 <= i < len(pgns):
                        records.append(pgns[i])
            elif isinstance(idxs, dict):
                records.append(idxs)
            j1939_part = (canboat.get("j1939") or {}).get(str(int(pgn)))
            if isinstance(j1939_part, dict):
                records.append(j1939_part)
        spns = self.spns_for_pgn(pgn)
        acronym = ""
        label = ""
        if spns:
            db = self._j1939() or {}
            for s in spns:
                rec = (db.get("spns") or {}).get(f"SPN_{s}") or {}
                acronym = acronym or str(rec.get("pgn_acronym") or "")
                label = label or str(rec.get("pgn_label") or "")
        if not records and not spns:
            return Lookup.miss("canboat_pgn", key)
        source = "canboat_pgn" if records else "j1939_spn_fmi"
        return Lookup(True, source, key, {
            "pgn": int(pgn), "canboat": records, "spns": spns, "acronym": acronym, "label": label,
        })

    # ---------------------------------------------------------------- PID layer
    def _pid_index(self) -> dict[tuple[str, str], list[dict[str, Any]]]:
        def build() -> dict[tuple[str, str], list[dict[str, Any]]]:
            db = self._json_source("extended_pid")
            idx: dict[tuple[str, str], list[dict[str, Any]]] = {}
            for rec in (db or {}).get("pids", []) if isinstance(db, dict) else []:
                if not isinstance(rec, dict):
                    continue
                service = str(rec.get("service") or "").upper().zfill(2)
                pid = rec.get("pid")
                if pid is None or pid == "":
                    continue
                pid_hex = f"{pid:02X}" if isinstance(pid, int) else str(pid).upper().zfill(2)
                idx.setdefault((service, pid_hex), []).append(rec)
            return idx

        value = self._load("_idx_pid", build)
        return value if isinstance(value, dict) else {}

    def pid(self, pid_hex: str, service: str = "01") -> Lookup:
        """Mode/service PID records (standard first, then manufacturer rows)."""
        try:
            pid_norm = f"{int(str(pid_hex), 16):02X}"
            service_norm = f"{int(str(service), 16):02X}"
        except ValueError:
            return Lookup.miss("extended_pid", f"{service}:{pid_hex}", "invalid_key")
        key = f"{service_norm}:{pid_norm}"
        rows = self._pid_index().get((service_norm, pid_norm))
        if not rows:
            return Lookup.miss("extended_pid", key)
        ordered = sorted(rows, key=lambda r: (r.get("confidence") != "standard", str(r.get("manufacturer") or "")))
        return Lookup(True, "extended_pid", key, ordered)

    def mode06(self, mid: int) -> Lookup:
        db = self._json_source("obd_mode06")
        key = f"0x{int(mid):02X}"
        if not isinstance(db, dict):
            return Lookup.miss("obd_mode06", key, "source_unavailable")
        rec = (db.get("monitors") or {}).get(key)
        return Lookup(True, "obd_mode06", key, rec) if isinstance(rec, dict) else Lookup.miss("obd_mode06", key)

    def uds_did(self, did: int) -> Lookup:
        db = self._json_source("uds_did")
        key = f"0x{int(did):04X}"
        if not isinstance(db, dict):
            return Lookup.miss("uds_did", key, "source_unavailable")
        # The shipped file mixes "0x1153" and "0XF180" key spellings: match on
        # the numeric value, never on the raw string.
        dids = {str(k).upper(): v for k, v in (db.get("dids") or {}).items()}
        rec = dids.get(key.upper())
        return Lookup(True, "uds_did", key, rec) if isinstance(rec, dict) else Lookup.miss("uds_did", key)

    # ------------------------------------------------------------ symptom layer
    def symptoms(self) -> dict[str, dict[str, Any]]:
        db = self._json_source("canonical_symptoms")
        if not isinstance(db, dict):
            return {}
        table = db.get("symptoms") or {}
        return table if isinstance(table, dict) else {}

    def symptom(self, symptom_id: str) -> Lookup:
        rec = self.symptoms().get(symptom_id)
        if not isinstance(rec, dict):
            return Lookup.miss("canonical_symptoms", symptom_id)
        return Lookup(True, "canonical_symptoms", symptom_id, rec)

    def operating_scenarios(self) -> dict[str, Any]:
        """State-aware judgement data: battery bands per state + scenario rules (``{}`` when absent)."""
        data = self._json_source("operating_scenarios")
        return data if isinstance(data, dict) else {}

    def operating_scenario(self, scenario_id: str) -> dict[str, Any] | None:
        return next((s for s in self.operating_scenarios().get("scenarios") or []
                     if isinstance(s, dict) and s.get("id") == scenario_id), None)

    def reasoning_rules(self) -> list[dict[str, Any]]:
        """Curated common-cause rules over several active codes (``[]`` when absent)."""
        data = self._json_source("reasoning_rules")
        rules = data.get("rules") if isinstance(data, dict) else None
        return [r for r in rules if isinstance(r, dict) and r.get("id")] if isinstance(rules, list) else []

    def reasoning_rule(self, rule_id: str) -> dict[str, Any] | None:
        return next((r for r in self.reasoning_rules() if r.get("id") == rule_id), None)

    def graph_title(self, node_id: str, lang: str) -> str | None:
        """Display title of a graph node in ``lang`` when a translation exists (else ``None``)."""
        data = self._json_source("graph_title_i18n")
        titles = data.get("titles") if isinstance(data, dict) else None
        entry = titles.get(node_id) if isinstance(titles, dict) else None
        found = entry.get(lang) if isinstance(entry, dict) else None
        return str(found) if found else None

    def subsystem_label_en(self, label: str) -> str | None:
        """English name of a canonical symptom subsystem label (``None`` when not translated)."""
        data = self._json_source("subsystem_labels_en")
        labels = data.get("labels") if isinstance(data, dict) else None
        found = labels.get(label) if isinstance(labels, dict) else None
        return str(found) if found else None

    def symptoms_for_code(self, code_key: str) -> list[str]:
        """Symptom ids whose ``candidate_dtcs`` name this code ("P0301", "SPN 100"), file order."""
        def build() -> dict[str, list[str]]:
            out: dict[str, list[str]] = {}
            for sid, rec in self.symptoms().items():
                for code in (rec.get("candidate_dtcs") or []) if isinstance(rec, dict) else []:
                    key = " ".join(str(code).upper().split())
                    if sid not in out.setdefault(key, []):
                        out[key].append(sid)
            return out

        index = self._load("_idx_symptoms_by_code", build)
        return list(index.get(" ".join(code_key.upper().split()), [])) if isinstance(index, dict) else []

    def symptom_checks(self, symptom_id: str) -> list[dict[str, Any]]:
        """Curated answer effects for one symptom's questions (``[]`` when none)."""
        data = self._json_source("symptom_checks")
        rec = ((data or {}).get("symptoms") or {}).get(symptom_id) if isinstance(data, dict) else None
        checks = rec.get("checks") if isinstance(rec, dict) else None
        return [c for c in checks if isinstance(c, dict)] if isinstance(checks, list) else []

    def symptom_phrases(self) -> list[tuple[str, str, str]]:
        """``(folded phrase, symptom_id, origin)`` for every keyword we ship.

        Origins: ``canonical_symptoms`` (keywords_tr/en + names) and the
        curated ``symptom_lexicon`` (extra TR/EN phrasings). Longest phrases
        first so a multi-word match wins over its sub-words.
        """
        def build() -> list[tuple[str, str, str]]:
            seen: set[tuple[str, str]] = set()
            out: list[tuple[str, str, str]] = []

            def add(phrase: Any, sid: str, origin: str) -> None:
                folded = fold_text(str(phrase or ""))
                if len(folded) < 3 or (folded, sid) in seen:
                    return
                seen.add((folded, sid))
                out.append((folded, sid, origin))

            for sid, rec in self.symptoms().items():
                if not isinstance(rec, dict):
                    continue
                for kw in list(rec.get("keywords_tr") or []) + list(rec.get("keywords_en") or []):
                    add(kw, sid, "canonical_symptoms")
                add(rec.get("name_en"), sid, "canonical_symptoms")
                add(rec.get("name_tr"), sid, "canonical_symptoms")
            lex = self._json_source("symptom_lexicon")
            if isinstance(lex, dict):
                for entry in lex.get("entries") or []:
                    if not isinstance(entry, dict):
                        continue
                    sid = str(entry.get("symptom_id") or "")
                    if sid not in self.symptoms():
                        continue  # lexicon may only point at real symptoms
                    for phrase in list(entry.get("phrases_tr") or []) + list(entry.get("phrases_en") or []):
                        add(phrase, sid, "symptom_lexicon")
            out.sort(key=lambda t: (-len(t[0]), t[0], t[1]))
            return out

        value = self._load("_idx_symptom_phrases", build)
        return value if isinstance(value, list) else []

    # -------------------------------------------------------- root-cause graph
    def _graph_index(self) -> dict[str, list[Any]]:
        def build() -> dict[str, list[Any]]:
            from src.engine.ai.hypothesis_engine import _split_code_qualifier, load_root_cause_graph

            path = self._path("root_cause_graph")
            nodes = load_root_cause_graph(path if self._data_dir != _resolve_data_dir() else None)
            idx: dict[str, list[Any]] = {}
            for node in nodes:
                for code in node.expected_dtcs:
                    norm, _fmi = _split_code_qualifier(code)
                    idx.setdefault(norm, []).append(node)
            return idx

        value = self._load("root_cause_graph", build)
        return value if isinstance(value, dict) else {}

    def graph_nodes_for_code(self, code_key: str) -> list[Any]:
        """Root-cause graph nodes declaring ``code_key`` (``P0101`` / ``SPN 110``)."""
        from src.engine.ai.hypothesis_engine import _split_code_qualifier

        norm, _ = _split_code_qualifier(code_key)
        return list(self._graph_index().get(norm, ()))

    def graph_node_count(self) -> int:
        return sum(1 for _ in {id(n) for nodes in self._graph_index().values() for n in nodes})

    # --------------------------------------------------------- systems/aliases
    def system(self, system_id: str) -> Lookup:
        tax = self._json_source("system_taxonomy")
        if not isinstance(tax, dict):
            return Lookup.miss("system_taxonomy", system_id, "source_unavailable")
        for rec in tax.get("canonical_systems") or []:
            if isinstance(rec, dict) and rec.get("id") == system_id:
                return Lookup(True, "system_taxonomy", system_id, rec)
        return Lookup.miss("system_taxonomy", system_id)

    def system_for_label(self, label: str) -> str | None:
        """Canonical system id for a free subsystem label (exact label match)."""
        def build() -> dict[str, str]:
            tax = self._json_source("system_taxonomy")
            out: dict[str, str] = {}
            for rec in (tax or {}).get("canonical_systems", []) if isinstance(tax, dict) else []:
                if not isinstance(rec, dict):
                    continue
                for lab in rec.get("source_labels") or []:
                    out[fold_text(str(lab))] = str(rec.get("id"))
            return out

        index = self._load("_idx_system_labels", build)
        found = index.get(fold_text(label)) if isinstance(index, dict) else None
        return str(found) if found else None

    def canonical_signal(self, name: str) -> str:
        """Alias-resolved signal name (``coolant_temp`` -> ``CoolantTemp``)."""
        index = self._load("_idx_signal_alias", self._build_alias_index)
        if not isinstance(index, dict):
            return name
        protocol = self.protocol_signal(name)
        if protocol:
            return protocol[0]
        return str(index.get(fold_text(name).replace(" ", ""), name))

    def protocol_signal(self, name: str) -> tuple[str, str] | None:
        """(canonical signal, native unit) for an SPN/PID-named key, else None.

        Canonical parameter layer.

        One physical quantity has a different identifier per protocol (engine
        speed = SPN 190 = Mode 01 PID 0x0C). ``signal_measurement_map`` already
        carries both identifiers per canonical signal, copied from the shipped
        J1939 and OBD databases, so a reading keyed by either identifier lands on
        the same canonical signal, thresholds and plausibility range. An
        identifier claimed by two canonical signals is ambiguous and resolves to
        nothing. The native unit is the protocol's own (SPN 100 is kPa, while the
        canonical oil pressure is bar): a unit-less value keyed "SPN 100" is in
        kPa and must be converted, never read as bar.
        """
        folded = fold_text(name)
        m = _SPN_KEY_RE.match(folded)
        key = f"spn:{int(m.group(1))}" if m else ""
        if not key:
            m = _PID_KEY_RE.match(folded)
            key = f"pid:{int(m.group(1), 16):02X}" if m else ""
        if not key:
            return None
        index = self._load("_idx_signal_protocol", self._build_protocol_index)
        hit = index.get(key) if isinstance(index, dict) else None
        return hit if isinstance(hit, tuple) else None

    def _build_protocol_index(self) -> dict[str, tuple[str, str]]:
        mmap = self._json_source("signal_measurement_map")
        claims: dict[str, set[tuple[str, str]]] = {}
        for rec in (mmap or {}).get("signals", []) if isinstance(mmap, dict) else []:
            if not isinstance(rec, dict) or not rec.get("canonical"):
                continue
            canonical = str(rec["canonical"])
            j1939 = rec.get("j1939")
            spn = j1939.get("spn") if isinstance(j1939, dict) else None
            if isinstance(spn, int) and not isinstance(spn, bool):
                claims.setdefault(f"spn:{spn}", set()).add((canonical, str(j1939.get("unit") or "")))
            obd = rec.get("obd")
            if isinstance(obd, dict) and str(obd.get("service") or "") == "01":
                try:
                    pid = int(str(obd.get("pid") or ""), 16)
                except ValueError:
                    continue
                claims.setdefault(f"pid:{pid:02X}", set()).add((canonical, str(obd.get("unit") or "")))
        return {key: next(iter(hits)) for key, hits in claims.items() if len(hits) == 1}

    def _build_alias_index(self) -> dict[str, str]:
        data = self._json_source("signal_aliases")
        out: dict[str, str] = {}
        for rec in (data or {}).get("aliases", []) if isinstance(data, dict) else []:
            if not isinstance(rec, dict) or not rec.get("canonical"):
                continue
            canonical = str(rec["canonical"])
            for form in [canonical] + list(rec.get("source_forms") or []) + list(rec.get("node_side") or []):
                out[fold_text(str(form)).replace(" ", "")] = canonical
        mmap = self._json_source("signal_measurement_map")
        for rec in (mmap or {}).get("signals", []) if isinstance(mmap, dict) else []:
            if not isinstance(rec, dict) or not rec.get("canonical"):
                continue
            canonical = str(rec["canonical"])
            for form in [canonical] + list(rec.get("aliases") or []):
                out.setdefault(fold_text(str(form)).replace(" ", ""), canonical)
        return out

    def signal_measurement(self, canonical: str) -> Lookup:
        """How a signal is measured (SPN/PGN, OBD PID, tool hint) — curated map."""
        data = self._json_source("signal_measurement_map")
        if not isinstance(data, dict):
            return Lookup.miss("signal_measurement_map", canonical, "source_unavailable")
        for rec in data.get("signals") or []:
            if isinstance(rec, dict) and rec.get("canonical") == canonical:
                return Lookup(True, "signal_measurement_map", canonical, rec)
        return Lookup.miss("signal_measurement_map", canonical)

    def signal_phrases(self) -> list[tuple[str, str, str]]:
        """``(folded phrase, canonical signal, unit)`` used to read values from text."""
        def build() -> list[tuple[str, str, str]]:
            data = self._json_source("signal_measurement_map")
            out: list[tuple[str, str, str]] = []
            for rec in (data or {}).get("signals", []) if isinstance(data, dict) else []:
                if not isinstance(rec, dict):
                    continue
                for phrase in list(rec.get("phrases_tr") or []) + list(rec.get("phrases_en") or []):
                    folded = fold_text(str(phrase))
                    if folded:
                        out.append((folded, str(rec["canonical"]), str(rec.get("unit") or "")))
            out.sort(key=lambda t: (-len(t[0]), t[0]))
            return out

        value = self._load("_idx_signal_phrases", build)
        return value if isinstance(value, list) else []

    # -------------------------------------------------------------- thresholds
    def telemetry_threshold(self, canonical: str) -> Lookup:
        """Threshold entry for a canonical signal (alias-tolerant)."""
        def load() -> dict[str, dict[str, Any]]:
            from src.engine.ai.anomaly_detector import load_thresholds

            if self._data_dir == _resolve_data_dir():
                return load_thresholds()
            return load_thresholds(self._path("telemetry_thresholds"))

        table = self._load("telemetry_thresholds", load)
        if not isinstance(table, dict):
            return Lookup.miss("telemetry_thresholds", canonical, "source_unavailable")
        if canonical in table:
            return Lookup(True, "telemetry_thresholds", canonical, table[canonical])
        want = self.canonical_signal(canonical)
        for key, rec in table.items():
            if self.canonical_signal(key) == want:
                return Lookup(True, "telemetry_thresholds", key, rec)
        return Lookup.miss("telemetry_thresholds", canonical)

    def hv_thresholds(self) -> dict[str, dict[str, Any]]:
        data = self._json_source("hv_safety_thresholds")
        table = (data or {}).get("thresholds") if isinstance(data, dict) else None
        return table if isinstance(table, dict) else {}

    def hv_threshold(self, threshold_key: str) -> Lookup:
        rec = self.hv_thresholds().get(threshold_key)
        if not isinstance(rec, dict):
            return Lookup.miss("hv_safety_thresholds", threshold_key)
        return Lookup(True, "hv_safety_thresholds", threshold_key, rec)

    # ---------------------------------------------------------------- severity
    def dtc_severity(self, code: str, title: str = "", subsystem: str = "") -> Lookup:
        """Rule-table severity (SAE J2012 subsystem x fault type)."""
        try:
            from src.engine.ai.severity_rules import resolve_severity

            sev = resolve_severity(code, title, subsystem)
        except Exception as exc:  # noqa: BLE001
            return Lookup.miss("dtc_severity_rules", code, f"source_unavailable: {exc}")
        return Lookup(True, "dtc_severity_rules", code, getattr(sev, "value", str(sev)))

    # -------------------------------------------------------------- procedures
    def procedure(self, code: str) -> Lookup:
        try:
            from src.engine.ai.procedure_validator import get_procedure

            dir_path = None if self._data_dir == _resolve_data_dir() else self._path("dtc_procedures")
            proc = get_procedure(code, dir_path)
        except Exception as exc:  # noqa: BLE001
            return Lookup.miss("dtc_procedures", code, f"source_unavailable: {exc}")
        return Lookup(True, "dtc_procedures", code, proc) if proc is not None else Lookup.miss("dtc_procedures", code)

    # ------------------------------------------------------------ NHTSA layer
    def recalls(self, make: str, model: str | None = None, terms: Iterable[str] = (), limit: int = 3) -> list[Lookup]:
        """NHTSA recalls for a make (and optional model) mentioning any term."""
        data = self._json_source("nhtsa_recalls")
        if not isinstance(data, dict) or not make:
            return []
        make_f, model_f = fold_text(make), fold_text(model or "")
        term_f = [fold_text(t) for t in terms if fold_text(t)]
        hits: list[Lookup] = []
        for cid in sorted(data):
            rec = data[cid]
            if not isinstance(rec, dict):
                continue
            vehicles = rec.get("affected_vehicles") or []
            if not any(
                make_f in fold_text(str(v.get("make", ""))) and (not model_f or model_f in fold_text(str(v.get("model", ""))))
                for v in vehicles if isinstance(v, dict)
            ) and make_f not in fold_text(str(rec.get("manufacturer", ""))):
                continue
            if term_f:
                text = fold_text(" ".join(str(rec.get(k, "")) for k in ("component", "summary", "consequence")))
                if not any(f" {t} " in f" {text} " for t in term_f):
                    continue
            hits.append(Lookup(True, "nhtsa_recalls", str(cid), rec))
            if len(hits) >= limit:
                break
        return hits

    def complaint_count(self, make: str, model: str | None = None) -> Lookup:
        data = self._json_source("nhtsa_complaints")
        key = f"{make}/{model or '*'}"
        if not isinstance(data, dict) or not make:
            return Lookup.miss("nhtsa_complaints", key, "source_unavailable" if not isinstance(data, dict) else "invalid_key")
        make_f, model_f = fold_text(make), fold_text(model or "")
        total = 0
        rows = 0
        for v in data.get("vehicles") or []:
            if not isinstance(v, dict) or fold_text(str(v.get("make", ""))) != make_f:
                continue
            if model_f and fold_text(str(v.get("model", ""))) != model_f:
                continue
            rows += 1
            total += len(v.get("can_related") or [])
        if not rows:
            return Lookup.miss("nhtsa_complaints", key)
        return Lookup(True, "nhtsa_complaints", key, {"vehicle_rows": rows, "can_related_complaints": total})

    # --------------------------------------------------------------- glossary
    def glossary(self) -> dict[str, dict[str, Any]]:
        data = self._json_source("copilot_glossary")
        terms = (data or {}).get("terms") if isinstance(data, dict) else None
        return terms if isinstance(terms, dict) else {}

    def user_kb(self, code: str) -> Lookup:
        data = self._json_source("user_kb")
        rec = ((data or {}).get("entries") or {}).get(code) if isinstance(data, dict) else None
        return Lookup(True, "user_kb", code, rec) if isinstance(rec, dict) else Lookup.miss("user_kb", code)

    # ---------------------------------------------------------- ref resolution
    def resolve_ref(self, ref: str) -> bool:
        """True when a printed ``source#key`` citation resolves to real data."""
        source, _, key = ref.partition("#")
        if not key or source not in SOURCE_FILES:
            return False
        if source == "dtc_database":
            return self.dtc(key.split(".")[0]).found
        if source == "j1939_spn_fmi":
            if key.startswith("PGN_"):
                return self.pgn(int(key[4:])).found
            if key.startswith("fmi_definitions."):
                return self.fmi_definition(int(key.split(".")[1])).found
            m = re.match(r"^SPN_(\d+)(?:\.FMI_(\d+))?", key)
            if not m:
                return False
            return self.spn(int(m.group(1)), int(m.group(2)) if m.group(2) else None).record is not None
        if source == "canonical_symptoms":
            return self.symptom(key).found
        if source == "symptom_lexicon":
            lex = self._json_source("symptom_lexicon")
            return (key == "safety_terms" and isinstance(lex, dict) and "safety_terms" in lex) or self.symptom(key).found
        if source == "obd_mode06":
            db = self._json_source("obd_mode06")
            return isinstance(db, dict) and key.upper().replace("0X", "0x") in (db.get("monitors") or {})
        if source == "operating_scenarios":
            return key == "battery_voltage" and bool(self.operating_scenarios().get("battery_voltage")) \
                or self.operating_scenario(key) is not None
        if source == "reasoning_rules":
            return self.reasoning_rule(key) is not None
        if source == "graph_title_i18n":
            return self.graph_title(key, "tr") is not None or self.graph_title(key, "en") is not None
        if source == "subsystem_labels_en":
            return self.subsystem_label_en(key) is not None
        if source == "symptom_checks":
            sid, _, cid = key.partition(".")
            return any(c.get("id") == cid for c in self.symptom_checks(sid))
        if source == "root_cause_graph":
            return any(n.id == key for nodes in self._graph_index().values() for n in nodes)
        if source == "telemetry_thresholds":
            return self.telemetry_threshold(key).found
        if source == "hv_safety_thresholds":
            return self.hv_threshold(key).found
        if source == "signal_measurement_map":
            return self.signal_measurement(key).found
        if source == "nhtsa_recalls":
            data = self._json_source("nhtsa_recalls")
            return isinstance(data, dict) and key in data
        if source == "canboat_pgn":
            return key.startswith("PGN_") and self.pgn(int(key[4:])).found
        if source == "dtc_severity_rules":
            return self.dtc_severity(key).found
        if source == "dtc_procedures":
            return self.procedure(key).found
        if source == "extended_pid":
            service, _, pid = key.partition(":")
            return self.pid(pid, service).found
        if source == "dtc_oem_layer":
            return self.dtc_oem(key).found
        if source == "copilot_glossary":
            return key in self.glossary()
        if source == "system_taxonomy":
            return self.system(key).found
        if source == "nhtsa_complaints":
            make, _, model = key.partition("/")
            return self.complaint_count(make, None if model in ("", "*") else model).found
        if source == "user_kb":
            return self.user_kb(key).found
        return (self._data_dir / SOURCE_FILES[source]).exists()


_DEFAULT_KB: KnowledgeBase | None = None
_DEFAULT_KB_LOCK = threading.Lock()


def get_knowledge_base() -> KnowledgeBase:
    """Process-wide lazy instance over the shipped ``data/`` tree."""
    global _DEFAULT_KB
    if _DEFAULT_KB is None:
        with _DEFAULT_KB_LOCK:
            if _DEFAULT_KB is None:
                _DEFAULT_KB = KnowledgeBase()
    return _DEFAULT_KB

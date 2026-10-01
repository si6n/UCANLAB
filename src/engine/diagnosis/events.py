"""Observed fault codes → ``DiagnosticEvent`` records (Aşama 6).

Shared by the live telemetry path (J1939 DM1 broadcasts) and the scan paths
(OBD-II read-only readout, simulator). Severity comes from the knowledge base
only; a code the knowledge base does not classify is kept as
``Severity.UNKNOWN`` — it never disappears and is never invented.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from src.core.logging import get_logger
from src.core.models.diagnostics import DiagnosticDomain, DiagnosticEvent, Severity

logger = get_logger("engine.diagnosis.events")

OBD_STATUS = {"stored": "ACTIVE", "permanent": "ACTIVE", "pending": "PENDING"}


def _kb_severity(raw: Any, **ctx: Any) -> Severity:
    if not raw:
        return Severity.UNKNOWN
    try:
        return Severity(str(raw))
    except ValueError:
        logger.warning("KB severity outside the allowlist — recording as UNKNOWN",
                       extra={**ctx, "kb_severity": str(raw)})
        return Severity.UNKNOWN


def dm1_to_events(dtcs: Iterable[Any], timestamp_ns: int) -> list[DiagnosticEvent]:
    """J1939 DM1 SPN/FMI records → ACTIVE events (SPN 0 / 0xFF placeholders skipped)."""
    from src.engine.ai.diagnostic_copilot import get_j1939_spn_database

    spn_db = get_j1939_spn_database().get("spns", {})
    events: list[DiagnosticEvent] = []
    for dtc in dtcs:
        spn, fmi = getattr(dtc, "spn", 0), getattr(dtc, "fmi", 0)
        if spn in (0, 0xFF):
            continue
        severity = Severity.UNKNOWN
        rec = spn_db.get(f"SPN_{spn}")
        if rec:
            fm = rec.get("fault_matrix", {})
            fmi_rec = fm.get(str(fmi)) if isinstance(fm, dict) else None
            if isinstance(fmi_rec, dict):
                severity = _kb_severity(fmi_rec.get("severity"), spn=spn, fmi=fmi)
        try:
            events.append(DiagnosticEvent(timestamp_ns=timestamp_ns, code=f"SPN {spn} FMI {fmi}",
                                          domain=DiagnosticDomain.HEAVY_DUTY, severity=severity, status="ACTIVE"))
        except ValueError as exc:
            logger.debug("DiagnosticEvent rejected", extra={"error": str(exc), "spn": spn, "fmi": fmi})
    return events


def obd_codes_to_events(codes: Iterable[Any], timestamp_ns: int) -> list[DiagnosticEvent]:
    """OBD-II readout (``ObdCode``) → events; pending codes stay PENDING."""
    from src.engine.ai.diagnostic_copilot import EXPERT_KNOWLEDGE_BASE, ensure_external_dtc_database_loaded

    ensure_external_dtc_database_loaded()
    events: list[DiagnosticEvent] = []
    seen: set[tuple[str, str]] = set()
    for code in codes:
        status = OBD_STATUS.get(code.kind, "ACTIVE")
        if (code.code, status) in seen:
            continue
        seen.add((code.code, status))
        entry = EXPERT_KNOWLEDGE_BASE.get(code.code)
        severity = _kb_severity(entry.get("severity") if isinstance(entry, dict) else None, code=code.code)
        events.append(DiagnosticEvent(timestamp_ns=timestamp_ns, code=code.code, domain=DiagnosticDomain.PASSENGER,
                                      severity=severity, status=status))
    return events

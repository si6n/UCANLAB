# -*- coding: utf-8 -*-
"""T2-6 regression lock — the `Severity` allowlist and its `UNKNOWN` member.

Context (task card ``tasks/T2-6-tuner-severity-enum-migration.md``): P2-9 made
``DiagnosticEvent.severity`` an allowlisted ``Severity`` enum instead of a free
string, which is the CORRECT hardening. But four call sites kept passing the
bare literal ``"UNKNOWN"`` for a DTC whose severity the knowledge base does not
state, so construction raised and — because ``_record_dm1_events`` swallows the
``ValueError`` behind a debug log — the events silently vanished from the
evidence base.

The fix chosen was NOT to relax P2-9 and NOT to fold the state into ``INFO``
(claiming "informational" for a fault of unknown urgency is a fabricated
downgrade, and a severity-keyed consumer would read it as GREEN). ``UNKNOWN`` is
a first-class, documented member meaning "the knowledge base states no
severity", which is exactly what ``drive_safety_policy.decide_risk`` already
treats conservatively ("Unknown/empty severity -> GRAY (never GREEN)").

These tests lock BOTH halves: the member exists and is reachable, and the
allowlist still rejects everything outside it (including near-miss typos).

Offline, deterministic — no LLM, no network, no I/O beyond the local model.
"""

from __future__ import annotations

import pytest

from src.core.models.diagnostics import (
    DiagnosticDomain,
    DiagnosticEvent,
    Severity,
)

# The rungs the knowledge base may state, plus the modelled absence state.
EXPECTED_MEMBERS = {"INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL_STOP", "UNKNOWN"}


def _event(severity: object) -> DiagnosticEvent:
    return DiagnosticEvent(
        timestamp_ns=42,
        code="SPN 110 FMI 0",
        domain=DiagnosticDomain.HEAVY_DUTY,
        severity=severity,  # type: ignore[arg-type]
        status="ACTIVE",
    )


class TestSeverityAllowlistContract:
    """The enum is a closed allowlist — that is the hardening, and it stays."""

    def test_member_set_is_exactly_the_documented_rungs(self) -> None:
        assert {s.value for s in Severity} == EXPECTED_MEMBERS

    def test_unknown_is_a_first_class_member(self) -> None:
        """"No KB severity stated" must be representable without a fake value."""
        assert Severity.UNKNOWN.value == "UNKNOWN"
        assert _event(Severity.UNKNOWN).severity is Severity.UNKNOWN

    def test_unknown_is_distinct_from_info(self) -> None:
        """Folding UNKNOWN into INFO would fabricate a benign classification.

        `decide_risk` maps unknown/empty severity to GRAY but INFO to GREEN, so
        conflating them would DOWNGRADE the risk of an unclassifiable fault.
        """
        assert Severity.UNKNOWN is not Severity.INFO
        assert Severity.UNKNOWN != Severity.INFO
        from src.engine.ai.drive_safety_policy import decide_risk

        assert decide_risk(Severity.UNKNOWN.value) == "GRAY"
        assert decide_risk(Severity.INFO.value) == "GREEN"

    @pytest.mark.parametrize("member", sorted(EXPECTED_MEMBERS))
    def test_every_documented_member_constructs(self, member: str) -> None:
        assert _event(member).severity is Severity(member)

    @pytest.mark.parametrize("member", sorted(EXPECTED_MEMBERS))
    def test_every_member_is_accepted_as_the_enum_too(self, member: str) -> None:
        assert _event(Severity(member)).severity is Severity(member)


class TestUnlistedValuesStillFailClosed:
    """P2-9's strictness is preserved: only allowlist members may be built."""

    @pytest.mark.parametrize(
        "bad",
        ["HIHG", "unknown", "Unknown", "CRITICAL", "", "  ", "HIGH ", "INFOO", None, 3, 1.5, object()],
    )
    def test_unlisted_values_are_rejected(self, bad: object) -> None:
        with pytest.raises(ValueError, match="severity"):
            _event(bad)

    def test_near_miss_typos_are_not_normalised_into_members(self) -> None:
        """Case and whitespace are NOT folded — a near miss must not pass.

        This is the property that makes the allowlist meaningful: if
        `"high"`/`" HIGH "` silently became `HIGH`, a malformed KB row would be
        indistinguishable from a correct one.
        """
        for near_miss in ("high", " HIGH ", "Medium", "critical_stop", "info"):
            with pytest.raises(ValueError, match="severity"):
                _event(near_miss)

    def test_the_rejection_message_lists_the_allowlist(self) -> None:
        """The error must tell the caller the valid rungs (including UNKNOWN)."""
        with pytest.raises(ValueError) as exc:
            _event("HIHG")
        message = str(exc.value)
        for member in EXPECTED_MEMBERS:
            assert member in message, f"{member} missing from the rejection message"


class TestUnclassifiedDtcIsRecordedNotDropped:
    """The behavioural point of the fix: an unknown-severity DTC keeps its event.

    Before T2-6 the literal raised, and the swallowing ``except`` in the DM1
    ingestion path turned "we cannot classify this" into "this DTC does not
    exist" — the worse, silent outcome.
    """

    def test_unknown_severity_event_survives_construction(self) -> None:
        from src.core.models.diagnostics import VehicleSession

        session = VehicleSession(session_id="s-1", started_at_ns=0, domain=DiagnosticDomain.HEAVY_DUTY)
        session.events.append(_event(Severity.UNKNOWN))
        assert len(session.events) == 1
        assert session.events[0].severity is Severity.UNKNOWN

    def test_unknown_severity_serialises_as_a_plain_string(self) -> None:
        """`str` mixin: the value must survive a JSON round-trip as "UNKNOWN"."""
        import json

        payload = json.loads(json.dumps({"severity": _event(Severity.UNKNOWN).severity}))
        assert payload == {"severity": "UNKNOWN"}


class TestNoFabricatedSeverityInProducers:
    """A producer must never invent a rung the KB did not state (§2.3)."""

    def test_producers_that_reference_unknown_use_the_enum(self) -> None:
        """The bare `"UNKNOWN"` literal must not appear at a severity call site.

        Scans the two production sources that carried the literal so a future
        edit cannot silently reintroduce the free string.
        """
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        for rel in ("src/ui/desktop_app.py", "src/engine/ai/diagnostic_copilot.py"):
            source = (root / rel).read_text(encoding="utf-8")
            for lineno, line in enumerate(source.splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if 'severity = "UNKNOWN"' in line or 'severity="UNKNOWN"' in line:
                    pytest.fail(f"{rel}:{lineno} passes the bare 'UNKNOWN' literal — use Severity.UNKNOWN")


class TestExtendedPidZeroIsNotDropped:
    """T2-6 companion fix: PID 0x00 must survive the catalog load filter.

    The load filter used a TRUTHINESS test (`if p.get("pid")`), and OBD-II PID
    ``0x00`` — "PIDs supported [01-20]", the mandated first Mode 01 query — is
    stored as the integer ``0``, which is falsy. The record was deleted at load
    time, so the shipped 226-record catalog was served as 223 and
    ``get_extended_pid_info("00")`` returned ``None`` for a PID the file
    contains. `csv_twins` counts the JSON, so it could not see the loss.

    These tests lock the filter to "has any pid identifier" rather than
    "pid is truthy".
    """

    def test_pid_zero_record_is_present_in_the_loaded_catalog(self) -> None:
        from src.engine.ai.diagnostic_copilot import get_extended_pid_database

        pids = get_extended_pid_database().get("pids", [])
        zero = [p for p in pids if str(p.get("pid_hex", "")).upper() == "00"]
        assert zero, "PID 0x00 was dropped from the loaded catalog"
        assert any(str(p.get("pid")) == "0" for p in zero), "the int-typed PID 0 record is missing"

    def test_lookup_of_pid_zero_returns_its_record(self) -> None:
        from src.engine.ai.diagnostic_copilot import get_extended_pid_info

        info = get_extended_pid_info("00")
        assert info is not None, 'get_extended_pid_info("00") returned None'
        assert info.get("name") == "PIDs supported [01-20]"

    def test_lookup_never_raises_on_the_mixed_int_str_pid_shapes(self) -> None:
        """114 catalog rows carry an int `pid`, 112 a str — neither may crash.

        `p.get("pid", "").upper()` raised `AttributeError` on the int rows,
        which is uncaught, so the lookup aborted instead of returning a record.
        """
        from src.engine.ai.diagnostic_copilot import get_extended_pid_database, get_extended_pid_info

        hexes = [str(p.get("pid_hex", "")) for p in get_extended_pid_database().get("pids", [])]
        assert any(h.upper() == "00" for h in hexes), "precondition: 00 must be in the catalog"
        for h in ("00", "0B", "2F", "5C", "02", "08", "01", "20"):
            get_extended_pid_info(h)  # must not raise

    def test_filter_still_rejects_a_genuine_comment_row(self) -> None:
        """The filter must remain useful: a row with NO pid identifier is dropped.

        The shipped catalog contains one such row
        (`~Modifications by Jason Rapp for ABRP data contribution`). Accepting
        `0` must not have opened the door to comment rows.
        """
        import json
        from pathlib import Path

        from src.engine.ai.diagnostic_copilot import get_extended_pid_database

        root = Path(__file__).resolve().parents[2]
        raw = json.loads((root / "data" / "diagnostics" / "extended_pid_database.json").read_text(encoding="utf-8"))
        loaded = get_extended_pid_database().get("pids", [])
        assert len(loaded) < len(raw["pids"]), "the comment row should still be filtered"
        assert len(raw["pids"]) - len(loaded) == 1, "exactly the comment row is dropped"
        assert all(str(p.get("pid")).strip() or str(p.get("pid_hex")).strip() for p in loaded)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))

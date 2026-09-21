"""Regression tests for the verified ``src/safety/**`` remediation round 2.

One test class per review finding (S-01 .. S-17 as implemented). Evidence for
every finding is in the safety audit. All timing is driven by a
``VirtualClock`` — no test sleeps against the real clock.

Coverage (finding -> class):
  S-01 test_s01_*  unbounded consumed-token store, expiry prune
  S-02 test_s02_*  no token-free lease re-anchor on ARMED_TX/ACTIVE
  S-03 test_s03_*  DM4/DM5 are READ PGNs, not clears
  S-04 test_s04_*  single 0xCC padding helper
  S-05 test_s05_*  fail-closed arm when auth_secret is None
  S-05 test_s05_production_*  authenticated-deployment declaration is satisfied
  S-07 test_s07_*  RequestUpload demoted; 0x29/0x2C/0x3B/0x84/0x86 added
  S-08 test_s08_*  is_lease_valid requires the monitor to be running
  S-09 test_s09_*  whitelist hit no longer clears the miss streak
  S-10 test_s10_*  stable MachineGuid machine identity
  S-11 test_s11_*  isinstance-based VirtualClock refusal
  S-12 test_s12_*  rate-limit message from the real caps
  S-13 test_s13_*  is_connected resolves the bus provider once
  S-14 test_s14_*  evicted stream yields WRONG_SEQUENCE, not INITIAL
  S-15 test_s15_*  arm refused while protection is downgraded
  S-17 test_s17_*  atexit fail-closed E-Stop on teardown
"""

from __future__ import annotations

from typing import Any

import pytest

from src.core.contracts.ports import VirtualClock
from src.core.errors import SafetyError
from src.hal.virtual import VirtualBus
from src.safety import criticality
from src.safety.e2e.packager import E2E_PAD_BYTE, E2ESafetyPackager, ensure_min_len
from src.safety.e2e.profiles import E2EProfileConfig, E2EProfileType, E2EStatus
from src.safety.e2e.validator import E2ESafetyValidator
from src.safety.estop import EmergencyStopSystem, EStopTriggerSource
from src.safety.exceptions import RateLimitExceededError
from src.safety.gateway import TxSafetyGateway
from src.safety.state_machine import SafetyState, SafetySupervisor
from src.safety.watchdog import TxWatchdogSupervisor

_ARM_SECRET = b"round2-arm-secret-32-bytes-long!!"
_TOKEN = "round2-heartbeat-token"


def _passive(**kwargs: Any) -> SafetySupervisor:
    return SafetySupervisor(initial_state=SafetyState.PASSIVE, **kwargs)


class _StubEstop:
    """Minimal E-Stop stand-in (is_engaged + the S-15 gate surface)."""

    def __init__(self, downgraded: bool = False) -> None:
        self.is_engaged = False
        self.protection_downgraded = downgraded
        self.arm_calls = 0

    def assert_arm_permitted(self, operation: str = "arm_tx") -> None:
        self.arm_calls += 1
        if self.protection_downgraded:
            raise SafetyError(
                f"{operation} refused: E-Stop protection is downgraded",
                code="ESTOP_PROTECTION_DOWNGRADED",
            )


# ---------------------------------------------------------------------------
# S-01 — consumed arm tokens are pruned by EXPIRY, never by capacity
# ---------------------------------------------------------------------------


class TestS01ArmTokenReplayStore:
    def test_s01_store_is_not_capacity_bounded(self) -> None:
        sup = _passive(auth_secret=_ARM_SECRET)
        assert sup._consumed_arm_tokens.maxlen is None, "S-01: deque is still maxlen-bounded"

    def test_s01_token_survives_a_mint_flood_within_one_ttl(self) -> None:
        """A >1024-token flood must not evict a still-unexpired consumed token."""
        sup = _passive(auth_secret=_ARM_SECRET)
        victim = sup.issue_arm_token(ttl_s=30.0)
        sup.arm_tx("arm", auth_token=victim)
        sup.enter_passive_mode("passive again")

        # Flood the store (maxlen=1024 would have evicted `victim`'s payload).
        for _ in range(5000):
            flood = sup.issue_arm_token(ttl_s=30.0)
            sup._verify_arm_token(flood, consume=True)

        with pytest.raises(SafetyError) as exc:
            sup.arm_tx("replay", auth_token=victim)
        assert exc.value.code == "ARM_AUTH_REPLAYED", "S-01: consumed token was evicted and replayed"

    def test_s01_expired_tokens_are_pruned(self) -> None:
        sup = _passive(auth_secret=_ARM_SECRET)
        token = sup.issue_arm_token(ttl_s=0.0)  # coerced to 1s TTL
        sup.arm_tx("arm", auth_token=token)
        assert len(sup._consumed_arm_tokens) == 1

        # Force the recorded entry into the past and prune directly (no sleep).
        payload = sup._consumed_arm_tokens[0]
        sup._consumed_arm_tokens.clear()
        sup._consumed_arm_tokens_heap.clear()
        sup._record_consumed_arm_token((0).to_bytes(8, "big") + payload[8:])
        sup._prune_consumed_arm_tokens()
        assert len(sup._consumed_arm_tokens) == 0, "S-01: expired token not pruned"
        assert sup._consumed_arm_tokens_heap == []


# ---------------------------------------------------------------------------
# S-02 — no token-free lease re-anchor on ARMED_TX/ACTIVE
# ---------------------------------------------------------------------------


class TestS02NoTokenFreeReanchor:
    def test_s02_armed_transition_does_not_refresh_the_lease(self) -> None:
        supervisor = _passive(auth_secret=_ARM_SECRET, allow_unauthenticated_arm=True)
        clock = VirtualClock(start_monotonic_sec=1000.0)
        watchdog = TxWatchdogSupervisor(supervisor=supervisor, timeout_ms=800.0, clock=clock)
        watchdog.arm_heartbeat_token(_TOKEN)
        watchdog.start()
        try:
            clock.advance(0.5)  # lease is now half-consumed
            before = watchdog._last_heartbeat_time
            supervisor.arm_tx("arm", auth_token=supervisor.issue_arm_token())
            assert supervisor.current_state == SafetyState.ARMED_TX
            assert watchdog._last_heartbeat_time == before, (
                "S-02: ARMED_TX transition re-anchored the lease WITHOUT a token"
            )
        finally:
            watchdog.stop()

    def test_s02_fault_entry_invalidates_the_lease(self) -> None:
        supervisor = _passive(auth_secret=_ARM_SECRET, allow_unauthenticated_arm=True)
        clock = VirtualClock(start_monotonic_sec=1000.0)
        watchdog = TxWatchdogSupervisor(supervisor=supervisor, timeout_ms=800.0, clock=clock)
        watchdog.arm_heartbeat_token(_TOKEN)
        watchdog.start()
        try:
            assert watchdog.is_lease_valid is True
            supervisor.trigger_fault("fault")
            assert watchdog.is_lease_valid is False, "S-02: FAULT must invalidate the lease"
        finally:
            watchdog.stop()


# ---------------------------------------------------------------------------
# S-03 — DM4/DM5 are READ PGNs (SAE J1939-73), not clears
# ---------------------------------------------------------------------------


class TestS03Dm4Dm5AreReadOnly:
    def test_s03_dm4_dm5_removed_from_critical(self) -> None:
        assert 65229 not in criticality.CRITICAL_J1939_PGNS, "S-03: DM4 still critical"
        assert 65230 not in criticality.CRITICAL_J1939_PGNS, "S-03: DM5 still critical"

    def test_s03_dm4_dm5_kept_in_readonly_diagnostic_set(self) -> None:
        assert 65229 in criticality.READONLY_DIAGNOSTIC_J1939_PGNS
        assert 65230 in criticality.READONLY_DIAGNOSTIC_J1939_PGNS

    def test_s03_real_clear_pgns_still_critical(self) -> None:
        assert 65235 in criticality.CRITICAL_J1939_PGNS  # DM11 Clear Active DTCs
        assert 65228 in criticality.CRITICAL_J1939_PGNS  # DM3  Clear Previously Active

    def test_s03_gateway_does_not_escalate_a_dm4_poll(self) -> None:
        """A DM4 Request poll must not inherit DM4 criticality via the gateway."""
        bus = VirtualBus(channel_id="s03")
        bus.connect()
        gw = TxSafetyGateway(bus=bus, whitelist_ids={0x18EA00F9})
        # J1939 Request (PGN 59904) asking for DM4 (65229 = 0xFECD)
        # -> must NOT be critical. J1939-21 §5.4.2 LE: CD FE 00.
        request = b"\xCD\xFE\x00\x00"
        frame = type(
            "F", (), {"data": request, "is_extended": True, "arbitration_id": 0x18EA00F9}
        )()
        assert gw._frame_is_critical(frame) is False, "S-03: DM4 request still critical"
        # DM11 (65235) request stays critical -> the policy was not broadened.
        dm11_request = b"\xD3\xFE\x00\x00"  # 65235 (0xFED3) little-endian
        dm11_frame = type(
            "F", (), {"data": dm11_request, "is_extended": True, "arbitration_id": 0x18EA00F9}
        )()
        assert gw._frame_is_critical(dm11_frame) is True
        bus.disconnect()


# ---------------------------------------------------------------------------
# S-04 — one padding helper, canonical 0xCC
# ---------------------------------------------------------------------------


class TestS04CanonicalPadding:
    def test_s04_pad_byte_is_0xcc(self) -> None:
        assert E2E_PAD_BYTE == 0xCC

    def test_s04_helper_pads_with_0xcc(self) -> None:
        buf = bytearray(b"\x01")
        ensure_min_len(buf, 4)
        assert bytes(buf) == b"\x01\xCC\xCC\xCC"

    def test_s04_both_packager_paths_agree(self) -> None:
        """package() and package_payload() must pad identically (S-04).

        The profile below places the counter at offset 3, so a 1-byte payload
        forces THREE pad bytes — enough to distinguish 0xCC from 0x00.
        """
        profile = E2EProfileConfig(
            profile_type=E2EProfileType.AUTOSAR_PROFILE_1A,
            data_id=0x11,
            crc_byte_offset=0,
            counter_byte_offset=3,
            counter_bit_mask=0x0F,
            counter_bit_shift=0,
            counter_modulo=16,
            max_delta_counter=3,
        )
        packager = E2ESafetyPackager()
        payload, _counter, _crc = packager.package_payload(b"\x01", profile, arbitration_id=0x123)
        # Byte 0 is the CRC; bytes 1..3 are the injected padding. 0xCC (0b11001100)
        # must dominate the padding region — a 0x00 pad would show up as 0b0000
        # in the low nibble. The counter is masked to the low nibble of byte 3,
        # so its HIGH nibble must still be 0b1100 (0xC) from the 0xCC pad.
        assert payload[1:3] == b"\xCC\xCC", f"S-04: unexpected pad bytes {payload!r}"
        assert payload[3] & 0xF0 == 0xC0, f"S-04: byte 3 high nibble not 0xCC-derived: {payload!r}"


# ---------------------------------------------------------------------------
# S-05 — fail-closed arm when no auth_secret is configured
# ---------------------------------------------------------------------------


class TestS05FailClosedUnauthenticatedArm:
    def test_s05_arm_without_secret_fails_closed(self) -> None:
        """S-05 (corrected): the fail-closed arm gate is per-DEPLOYMENT.

        The first remediation read "auth_secret is None => always fail closed",
        which contradicted the deliberate T41/Y-2 contract (an UNCONFIGURED
        supervisor must keep working). S-05's real defect needs the production
        composition root to omit the operator credential, so the two halves are
        asserted separately:

          (i)  a DECLARED authenticated deployment (``require_arm_auth=True``,
               the flag the real composition root passes) can never be left
               without an operator credential, and
          (ii) an UNDECLARED, unconfigured supervisor keeps the legacy,
               backward-compatible arm locked by `test_y2_*`.
        """
        # (i) declared-authenticated + no secret -> refuse to build at all.
        with pytest.raises(RuntimeError) as build_exc:
            _passive(require_arm_auth=True)
        assert "require_arm_auth" in str(build_exc.value)
        # ... and if the credential is lost AFTER construction, arm still fails
        # closed (no test-only opt-in can launder an authenticated deployment).
        sup = _passive(auth_secret=_ARM_SECRET, require_arm_auth=True)
        sup._auth_secret = None
        with pytest.raises(SafetyError) as exc:
            sup.arm_tx("arm")
        assert exc.value.code == "ARM_AUTH_NOT_CONFIGURED"
        assert sup.current_state == SafetyState.PASSIVE
        assert sup.is_tx_permitted is False
        # (ii) undeclared + no secret -> legacy unauthenticated arm still works.
        legacy = _passive()
        assert legacy.requires_arm_auth is False
        legacy.arm_tx("arm")
        assert legacy.current_state == SafetyState.ARMED_TX

    def test_s05_optin_flag_allows_unauthenticated_arm_in_test_mode(self) -> None:
        sup = _passive(allow_unauthenticated_arm=True)  # conftest sets UCANLAB_TEST_MODE=1
        sup.arm_tx("arm")
        assert sup.current_state == SafetyState.ARMED_TX

    def test_s05_optin_requires_test_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("UCANLAB_TEST_MODE", raising=False)
        with pytest.raises(RuntimeError):
            _passive(allow_unauthenticated_arm=True)

    def test_s05_explicit_secret_still_requires_token(self) -> None:
        sup = _passive(auth_secret=_ARM_SECRET)
        with pytest.raises(SafetyError) as exc:
            sup.arm_tx("arm")
        assert exc.value.code == "ARM_AUTH_REQUIRED"
        sup.arm_tx("arm", auth_token=sup.issue_arm_token())
        assert sup.current_state == SafetyState.ARMED_TX


# ---------------------------------------------------------------------------
# S-07 — RequestUpload demoted to replay-only; new mutating SIDs added
# ---------------------------------------------------------------------------


class TestS07UdsSidPolicy:
    def test_s07_requestupload_is_replay_only(self) -> None:
        assert 0x35 not in criticality.CRITICAL_UDS_SIDS, "S-07: 0x35 still critical"
        assert 0x35 in criticality.REPLAY_ONLY_PROHIBITED_UDS_SIDS
        assert 0x35 in criticality.PROHIBITED_UDS_SIDS, "S-07: 0x35 dropped from the replay set"

    @pytest.mark.parametrize("sid", [0x29, 0x2C, 0x3B, 0x84, 0x86])
    def test_s07_new_mutating_sids_are_critical(self, sid: int) -> None:
        assert sid in criticality.CRITICAL_UDS_SIDS

    def test_s07_gateway_uses_the_shared_catalogue(self) -> None:
        assert TxSafetyGateway.CRITICAL_UDS_SIDS == criticality.CRITICAL_UDS_SIDS


# ---------------------------------------------------------------------------
# S-08 — lease validity requires a running monitor
# ---------------------------------------------------------------------------


class TestS08LeaseRequiresRunningMonitor:
    def test_s08_unstarted_watchdog_lease_is_invalid(self) -> None:
        supervisor = _passive(auth_secret=_ARM_SECRET, allow_unauthenticated_arm=True)
        clock = VirtualClock(start_monotonic_sec=1000.0)
        supervisor.arm_tx("arm", auth_token=supervisor.issue_arm_token())
        watchdog = TxWatchdogSupervisor(supervisor=supervisor, timeout_ms=800.0, clock=clock)
        watchdog.arm_heartbeat_token(_TOKEN)
        assert watchdog.is_lease_valid is False, "S-08: unstarted monitor reports a valid lease"

    def test_s08_running_watchdog_lease_is_valid(self) -> None:
        supervisor = _passive(auth_secret=_ARM_SECRET, allow_unauthenticated_arm=True)
        clock = VirtualClock(start_monotonic_sec=1000.0)
        supervisor.arm_tx("arm", auth_token=supervisor.issue_arm_token())
        watchdog = TxWatchdogSupervisor(supervisor=supervisor, timeout_ms=800.0, clock=clock)
        watchdog.arm_heartbeat_token(_TOKEN)
        watchdog.start()
        try:
            assert watchdog.is_lease_valid is True
            clock.advance(1.0)
            assert watchdog.is_lease_valid is False, "S-08: expired lease reported valid"
        finally:
            watchdog.stop()


# ---------------------------------------------------------------------------
# S-09 — a whitelisted HIT must not clear the miss streak
# ---------------------------------------------------------------------------


class TestS09WhitelistStreakNotClearedByHit:
    def _gateway(self) -> tuple[TxSafetyGateway, EmergencyStopSystem, VirtualBus]:
        bus = VirtualBus(channel_id="s09")
        bus.connect()
        estop = EmergencyStopSystem(allow_self_reset=True)
        gw = TxSafetyGateway(bus=bus, estop=estop, whitelist_ids={0x7E0})
        return gw, estop, bus

    def test_s09_n_miss_then_hit_still_latches(self) -> None:
        from src.core.models.can_frame import CanFrame
        from src.safety.exceptions import WhitelistViolationError

        gw, estop, bus = self._gateway()
        good = CanFrame.create(channel_id="s09", arbitration_id=0x7E0, data=b"\x01")
        bad = CanFrame.create(channel_id="s09", arbitration_id=0x123, data=b"\x01")

        for _ in range(TxSafetyGateway.WHITELIST_ESTOP_AFTER - 1):
            with pytest.raises(WhitelistViolationError):
                gw.validate_and_transmit(bad)
            # A legal frame in between must NOT disarm the streak (S-09).
            assert gw.validate_and_transmit(good) is True

        with pytest.raises(WhitelistViolationError):
            gw.validate_and_transmit(bad)
        assert estop.is_engaged is True, "S-09: N-miss-then-hit pattern never latched"
        bus.disconnect()

    def test_s09_streak_decays_by_time_window(self) -> None:
        gw, _estop, bus = self._gateway()
        gw._whitelist_miss_streak = 3
        gw._whitelist_last_miss_ns = 0  # ancient -> window expired
        gw._decay_whitelist_miss_streak(10**18)
        assert gw._whitelist_miss_streak == 0
        bus.disconnect()


# ---------------------------------------------------------------------------
# S-10 — stable machine identity for DPAPI entropy
# ---------------------------------------------------------------------------


class TestS10MachineGuidIdentity:
    def test_s10_machine_guid_used_when_available(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from src.safety import secret_provider as sp

        monkeypatch.setattr(sp, "read_windows_machine_guid", lambda: "GUID-STABLE-1234")
        bound = sp.derive_machine_dpapi_entropy()

        # Renaming the machine (node/COMPUTERNAME) must NOT change the binding.
        monkeypatch.setenv("COMPUTERNAME", "RENAMED-HOST")
        monkeypatch.setattr("platform.node", lambda: "renamed-host")
        assert sp.derive_machine_dpapi_entropy() == bound, "S-10: identity still node-based"

    def test_s10_falls_back_when_machine_guid_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from src.safety import secret_provider as sp

        monkeypatch.setattr(sp, "read_windows_machine_guid", lambda: None)
        monkeypatch.setenv("COMPUTERNAME", "FALLBACK-HOST")
        assert isinstance(sp.derive_machine_dpapi_entropy(), bytes)

    def test_s10_read_helper_never_raises(self) -> None:
        from src.safety.secret_provider import read_windows_machine_guid

        result = read_windows_machine_guid()
        assert result is None or (isinstance(result, str) and result)


# ---------------------------------------------------------------------------
# S-11 — isinstance-based VirtualClock refusal
# ---------------------------------------------------------------------------


class TestS11VirtualClockRefusal:
    def test_s11_virtual_clock_refused_without_test_mode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("UCANLAB_TEST_MODE", raising=False)
        supervisor = _passive()
        with pytest.raises(RuntimeError, match="VirtualClock"):
            TxWatchdogSupervisor(
                supervisor=supervisor, timeout_ms=800.0,
                clock=VirtualClock(start_monotonic_sec=1.0),
            )

    def test_s11_virtual_clock_accepted_in_test_mode(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("UCANLAB_TEST_MODE", "1")
        supervisor = _passive()
        watchdog = TxWatchdogSupervisor(
            supervisor=supervisor, timeout_ms=800.0, clock=VirtualClock(start_monotonic_sec=1.0)
        )
        assert isinstance(watchdog.clock, VirtualClock)

    def test_s11_detection_is_isinstance_based(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A SUBCLASS of VirtualClock must still be caught (the old __name__ test missed it)."""
        monkeypatch.delenv("UCANLAB_TEST_MODE", raising=False)

        class _SubclassedClock(VirtualClock):
            pass

        supervisor = _passive()
        with pytest.raises(RuntimeError):
            TxWatchdogSupervisor(
                supervisor=supervisor, timeout_ms=800.0, clock=_SubclassedClock(start_monotonic_sec=1.0)
            )


# ---------------------------------------------------------------------------
# S-12 — rate-limit message built from the real caps
# ---------------------------------------------------------------------------


class TestS12RateLimitMessageFromConstants:
    def test_s12_message_matches_gateway_constants(self) -> None:
        msg = RateLimitExceededError.default_message()
        assert str(TxSafetyGateway.MAX_TX_RATE_PER_SEC) in msg
        assert str(TxSafetyGateway.MAX_TOTAL_TX_PER_SEC) in msg
        assert RateLimitExceededError.RATE_LIMIT_LANE_MSG_PER_SEC == TxSafetyGateway.MAX_TX_RATE_PER_SEC
        assert RateLimitExceededError.RATE_LIMIT_TOTAL_MSG_PER_SEC == TxSafetyGateway.MAX_TOTAL_TX_PER_SEC

    def test_s12_default_ctor_uses_the_derived_message(self) -> None:
        err = RateLimitExceededError()
        assert "400" in str(err), "S-12: aggregate cap missing from the message"
        assert "(100 msg/s)" not in str(err), "S-12: hardcoded single-limit text remains"

    def test_s12_explicit_message_still_honoured(self) -> None:
        assert str(RateLimitExceededError("custom text")) == "custom text"


# ---------------------------------------------------------------------------
# S-13 — is_connected resolves the bus provider exactly once
# ---------------------------------------------------------------------------


class TestS13SingleProviderResolution:
    def test_s13_provider_called_once_per_read(self) -> None:
        from src.safety.multiplexer import SafeMultiplexedBus

        calls = {"n": 0}

        class _FakeBus:
            is_connected = True

        class _Mux(SafeMultiplexedBus):
            def __init__(self) -> None:  # bypass heavy construction
                pass

            def __getattribute__(self, name: str) -> Any:
                if name == "_bus_provider":
                    calls["n"] += 1
                    return lambda: _FakeBus()
                return object.__getattribute__(self, name)

        mux = _Mux()
        assert mux.is_connected is True
        assert calls["n"] == 1, f"S-13: _bus_provider() called {calls['n']} times"

    def test_s13_none_provider_is_not_connected(self) -> None:
        from src.safety.multiplexer import SafeMultiplexedBus

        class _Mux(SafeMultiplexedBus):
            def __init__(self) -> None:
                pass

            def _bus_provider(self) -> Any:
                return None

        assert _Mux().is_connected is False


# ---------------------------------------------------------------------------
# S-14 — evicted stream yields WRONG_SEQUENCE, not INITIAL
# ---------------------------------------------------------------------------


class TestS14EvictionTombstone:
    def test_s14_evicted_stream_next_frame_is_not_valid(self) -> None:
        validator = E2ESafetyValidator()
        profile = E2EProfileConfig.create_autosar_profile_1(data_id=0x12)
        payload = b"\x00" * 8

        for i in range(E2ESafetyValidator.MAX_TRACKED_STREAMS):
            validator.validate_raw(
                channel_id=f"ch_{i}", arbitration_id=0x100 + i, data=payload, profile=profile
            )

        # Push ch_0 out of the table.
        validator.validate_raw(channel_id="ch_new", arbitration_id=0x999, data=payload, profile=profile)
        assert ("ch_0", 0x100) not in validator._streams

        result = validator.validate_raw(
            channel_id="ch_0", arbitration_id=0x100, data=payload, profile=profile
        )
        assert result.verdict != E2EStatus.INITIAL, "S-14: evicted stream re-initialised as valid"
        assert result.verdict == E2EStatus.WRONG_SEQUENCE
        assert result.is_valid is False

    def test_s14_explicit_reset_clears_the_tombstone(self) -> None:
        validator = E2ESafetyValidator()
        profile = E2EProfileConfig.create_autosar_profile_1(data_id=0x12)
        # Build a CRC-VALID payload via the packager so the verdict reaches the
        # counter stage (an all-zero payload is CRC_ERROR, not INITIAL).
        packager = E2ESafetyPackager()
        valid, _c, _crc = packager.package_payload(b"\x01\x02", profile, arbitration_id=0x100)

        for i in range(E2ESafetyValidator.MAX_TRACKED_STREAMS):
            validator.validate_raw(
                channel_id=f"ch_{i}", arbitration_id=0x100 + i, data=valid, profile=profile
            )
        validator.validate_raw(channel_id="ch_new", arbitration_id=0x999, data=valid, profile=profile)

        # Without a reset the returning stream is tombstones -> WRONG_SEQUENCE.
        evicted = validator.validate_raw(
            channel_id="ch_0", arbitration_id=0x100, data=valid, profile=profile
        )
        assert evicted.verdict == E2EStatus.WRONG_SEQUENCE

        # Explicit reset is the sanctioned resync: the next frame is INITIAL.
        validator.reset(channel_id="ch_0", arbitration_id=0x100)
        result = validator.validate_raw(
            channel_id="ch_0", arbitration_id=0x100, data=valid, profile=profile
        )
        assert result.verdict == E2EStatus.INITIAL, "S-14: reset must be a sanctioned resync"


# ---------------------------------------------------------------------------
# S-15 — arm refused while E-Stop protection is downgraded
# ---------------------------------------------------------------------------


class TestS15DowngradedProtectionBlocksArm:
    def test_s15_arm_refused_when_downgraded(self) -> None:
        sup = SafetySupervisor(
            initial_state=SafetyState.PASSIVE,
            estop=_StubEstop(downgraded=True),
            auth_secret=_ARM_SECRET,
        )
        with pytest.raises(SafetyError) as exc:
            sup.arm_tx("arm", auth_token=sup.issue_arm_token())
        assert exc.value.code == "ESTOP_PROTECTION_DOWNGRADED"
        assert sup.current_state == SafetyState.PASSIVE
        assert sup.is_tx_permitted is False

    def test_s15_arm_permitted_when_protection_intact(self) -> None:
        estop = _StubEstop(downgraded=False)
        sup = SafetySupervisor(
            initial_state=SafetyState.PASSIVE, estop=estop, auth_secret=_ARM_SECRET
        )
        sup.arm_tx("arm", auth_token=sup.issue_arm_token())
        assert sup.current_state == SafetyState.ARMED_TX
        assert estop.arm_calls == 1

    def test_s15_estop_gate_helper_raises(self) -> None:
        estop = EmergencyStopSystem()
        estop._protection_downgraded = True
        with pytest.raises(SafetyError):
            estop.assert_arm_permitted("arm_tx")
        estop._protection_downgraded = False
        estop.assert_arm_permitted("arm_tx")  # no raise


# ---------------------------------------------------------------------------
# S-17 — atexit fail-closed E-Stop on process teardown
# ---------------------------------------------------------------------------


class TestS17ProcessTeardownEstop:
    def test_s17_teardown_hook_registered_on_start(self) -> None:
        supervisor = _passive(auth_secret=_ARM_SECRET, allow_unauthenticated_arm=True)
        clock = VirtualClock(start_monotonic_sec=1000.0)
        watchdog = TxWatchdogSupervisor(supervisor=supervisor, timeout_ms=800.0, clock=clock)
        watchdog.start()
        try:
            assert watchdog._teardown_registered is True, "S-17: no atexit hook registered"
        finally:
            watchdog.stop()

    def test_s17_teardown_triggers_process_termination_estop(self) -> None:
        supervisor = _passive(auth_secret=_ARM_SECRET, allow_unauthenticated_arm=True)
        supervisor.arm_tx("arm", auth_token=supervisor.issue_arm_token())
        estop = EmergencyStopSystem()
        clock = VirtualClock(start_monotonic_sec=1000.0)
        watchdog = TxWatchdogSupervisor(
            supervisor=supervisor, estop=estop, timeout_ms=800.0, clock=clock
        )
        watchdog.start()
        try:
            watchdog._on_process_teardown()
            assert estop.is_engaged is True, "S-17: teardown did not E-Stop"
            assert estop.last_event is not None
            assert estop.last_event.trigger == EStopTriggerSource.PROCESS_TERMINATION
        finally:
            watchdog.stop()

    def test_s17_teardown_never_reaches_the_wire(self) -> None:
        """The teardown path must not bypass TxSafetyGateway (AGENTS.md §2.1)."""
        import src.safety.watchdog as wd

        source = open(wd.__file__, encoding="utf-8").read()
        assert "privileged_send" not in source
        assert "validate_and_transmit" not in source


# ---------------------------------------------------------------------------
# Cross-cutting: fail-closed / choke-point invariants (AGENTS.md §2.1, §2.2)
# ---------------------------------------------------------------------------


class TestRound2FailClosedInvariants:
    def test_gateway_still_blocks_when_lease_is_invalid(self) -> None:
        """S-02/S-08 must keep TX gated by the watchdog lease through the gateway."""
        bus = VirtualBus(channel_id="round2_gw")
        bus.connect()
        supervisor = _passive(auth_secret=_ARM_SECRET, allow_unauthenticated_arm=True)
        clock = VirtualClock(start_monotonic_sec=1000.0)
        supervisor.transition_to(SafetyState.SAFE)
        supervisor.transition_to(SafetyState.PASSIVE)
        supervisor.arm_tx("arm", auth_token=supervisor.issue_arm_token())
        watchdog = TxWatchdogSupervisor(
            supervisor=supervisor, timeout_ms=800.0, clock=clock
        )
        watchdog.arm_heartbeat_token(_TOKEN)
        gw = TxSafetyGateway(
            bus=bus, supervisor=supervisor, watchdog=watchdog, whitelist_ids={0x7E0}
        )
        from src.core.models.can_frame import CanFrame

        frame = CanFrame.create(channel_id="round2_gw", arbitration_id=0x7E0, data=b"\x01")
        with pytest.raises(SafetyError) as exc:
            gw.validate_and_transmit(frame)
        assert exc.value.code == "WATCHDOG_LEASE_EXPIRED"
        bus.disconnect()

    def test_no_new_tx_path_bypasses_the_gateway(self) -> None:
        """No safety module outside the gateway may write to a HAL bus."""
        import pathlib

        offenders: list[str] = []
        for path in pathlib.Path("src/safety").rglob("*.py"):
            text = path.read_text(encoding="utf-8")
            if path.name == "gateway.py":
                continue
            if ".privileged_send(" in text or ".send_frame(" in text:
                offenders.append(str(path))
        assert offenders == [], f"unsanctioned TX paths: {offenders}"


# ---------------------------------------------------------------------------
# S-05 — the PRODUCTION composition root cannot arm TX unauthenticated
# ---------------------------------------------------------------------------


class TestS05ProductionDeploymentCannotArmUnauthenticated:
    """Acceptance: the production composition root fails closed.

    Safety audit row S-05 recorded that the real defect
    "needs composition-root omission" to be exploitable — i.e. the defect was
    never inside the state machine, it was the production root failing to
    configure an operator credential. ``SafetySupervisor(require_arm_auth=True)``
    is the declaration the production root (``src/ui/desktop_app.py``) now
    makes, and it has two independently testable consequences:

      1. a supervisor carrying that declaration with NO ``auth_secret`` cannot
         even be constructed — the app refuses to START instead of booting a
         supervisor that could later arm TX unauthenticated;
      2. no construction-time override can launder an unauthenticated arm
         through it, so it can never be downgraded to the legacy path.
    """

    def test_s05_production_supervisor_without_secret_refuses_to_construct(self) -> None:
        with pytest.raises(RuntimeError) as exc:
            _passive(require_arm_auth=True)  # production assertion, no auth_secret
        assert "require_arm_auth" in str(exc.value)

    def test_s05_production_supervisor_cannot_be_built_with_the_test_only_optin(self) -> None:
        """`allow_unauthenticated_arm` is TEST-ONLY and cannot launder prod."""
        with pytest.raises(ValueError):
            _passive(require_arm_auth=True, allow_unauthenticated_arm=True)

    def test_s05_production_supervisor_always_requires_a_token(self) -> None:
        """With the declaration AND a real secret, a token is mandatory."""
        sup = _passive(auth_secret=_ARM_SECRET, require_arm_auth=True)
        assert sup.requires_arm_auth is True
        with pytest.raises(SafetyError) as exc:
            sup.arm_tx("arm")  # no token
        assert exc.value.code == "ARM_AUTH_REQUIRED"
        assert sup.current_state == SafetyState.PASSIVE
        sup.arm_tx("arm", auth_token=sup.issue_arm_token())
        assert sup.current_state == SafetyState.ARMED_TX

    def test_s05_production_supervisor_is_fail_closed_even_if_the_secret_is_lost(self) -> None:
        """Direct evidence that the DECLARATION (not just the secret) gates TX.

        Simulates an out-of-band credential loss on a production supervisor:
        with the operator credential gone there is no token to verify, no
        config to trust and no opt-in to fall back on, so ``arm_tx`` MUST raise
        ``ARM_AUTH_NOT_CONFIGURED`` — the S-05 fail-open path is gone.
        """
        sup = _passive(auth_secret=_ARM_SECRET, require_arm_auth=True)
        sup._auth_secret = None  # out-of-band loss of the proxy-verified secret
        with pytest.raises(SafetyError) as exc:
            sup.arm_tx("arm")
        assert exc.value.code == "ARM_AUTH_NOT_CONFIGURED"
        assert sup.current_state == SafetyState.PASSIVE
        assert sup.is_tx_permitted is False

    def test_s05_legacy_unconfigured_supervisor_keeps_its_contract(self) -> None:
        """T41/Y-2: an UNCONFIGURED, undeclared supervisor stays compatible.

        The remediation must not re-break `test_y2_unconfigured_supervisor_
        stays_backward_compatible`: the fail-closed third state is entered only
        when the deployment DECLARED itself authenticated.
        """
        sup = _passive()  # no secret, no declaration
        assert sup.requires_arm_auth is False
        sup.arm_tx("Operator explicit authorization")
        assert sup.current_state == SafetyState.ARMED_TX
        sup.activate_tx("Active transmission stream")
        assert sup.current_state == SafetyState.ACTIVE

    def test_s05_desktop_composition_root_declares_authenticated_arm(self) -> None:
        """The PRODUCTION root must pass the declaration, not just a secret.

        Source-level assertion on the composition root: a secret wired into
        ``SafetySupervisor`` without ``require_arm_auth=True`` is exactly the
        S-05 composition-root omission this finding is about.
        """
        import inspect

        import src.ui.desktop_app as desktop

        source = inspect.getsource(desktop.UniversalCanDesktopApp.__init__)
        assert "SafetySupervisor(" in source
        assert "require_arm_auth=True" in source, (
            "S-05: the desktop composition root built its SafetySupervisor "
            "without declaring itself an authenticated deployment"
        )

    def test_s05_desktop_composition_root_supervisor_is_authenticated(self) -> None:
        """End-to-end: the app the root builds really does require arm auth."""
        from src.ui.desktop_app import UniversalCanDesktopApp

        app = UniversalCanDesktopApp(channel="vcan0", bitrate=250000)
        assert app.supervisor.requires_arm_auth is True
        with pytest.raises(SafetyError) as exc:
            app.supervisor.arm_tx(reason="renderer attempted unauthenticated arm")
        assert exc.value.code == "ARM_AUTH_REQUIRED"
        assert app.supervisor.current_state == SafetyState.PASSIVE
        app.supervisor.arm_tx(reason="operator", auth_token=app._mint_arm_token())
        assert app.supervisor.is_tx_permitted is True


# ---------------------------------------------------------------------------
# A1-F2 — a truncated 29-bit ISO-TP SF/FF header stays FAIL-CLOSED critical
# ---------------------------------------------------------------------------


class TestA1F2TruncatedIsotpFailClosed:
    """REVIEW Aşama 1 — Faz 1B (A1-F2), pinned.

    A 29-bit ISO-TP frame (``0x18DAxxxx`` physical / ``0x18DBxxxx`` functional)
    whose PCI claims a SingleFrame (0x0) or FirstFrame (0x1) but whose service
    byte CANNOT be parsed (the declared length exceeds the wire payload) is
    FAIL-CLOSED critical. Otherwise an attacker could truncate the header so
    the SID never materialises and slip a mutating command past Stage 4/5.

    These tests fix the *derived criticality* (``_frame_is_critical``), so they
    hold regardless of whether a speed source is wired: a regression that
    narrows A1-F2 back to "benign" turns the first two assertions red.
    """

    _SWEEP = (
        0x18DAF110,  # 29-bit physical ISO-TP (PF=0xDA)
        0x18DB33F1,  # 29-bit global functional ISO-TP (PF=0xDB)
    )
    _TRUNCATED = (
        b"\x00",  # SF claiming 0 data bytes -> no SID
        b"\x02",  # SF claiming 2 data bytes, but only the PCI byte is present
        b"\x10",  # FF header byte alone (no 2nd length byte, no SID)
        b"\x10\x0D",  # FF claiming 13 bytes, payload truncated before the SID
    )

    @staticmethod
    def _frame(arb_id: int, data: bytes) -> Any:
        from src.core.models.can_frame import CanFrame

        return CanFrame.create(
            channel_id="round2_a1f2",
            arbitration_id=arb_id,
            data=data,
            is_extended=True,
        )

    @staticmethod
    def _gateway() -> TxSafetyGateway:
        return TxSafetyGateway(bus=VirtualBus(channel_id="round2_a1f2"), whitelist_ids=None)

    @pytest.mark.parametrize("arb_id", _SWEEP)
    @pytest.mark.parametrize("payload", _TRUNCATED)
    def test_a1f2_truncated_sf_ff_is_critical_fail_closed(self, arb_id: int, payload: bytes) -> None:
        gw = self._gateway()
        gw.rebind_whitelist({arb_id})
        assert gw._frame_is_critical(self._frame(arb_id, payload)) is True, (
            f"A1-F2 regression: {payload.hex()} on 0x{arb_id:X} was judged NOT "
            "critical — a truncated SF/FF header now dodges Stage 4/5"
        )

    def test_a1f2_benign_wellformed_read_is_not_escalated(self) -> None:
        """Control: a WELL-FORMED, non-critical read SingleFrame must NOT escalate.

        This is what the e2e whitelist tests now transmit on ``0x18DAF110``
        (SID 0x22 ReadDataByIdentifier). It proves the fix is not a blanket
        exemption and that the truncated cases above are the ONLY fail-closed
        triggers in this branch.
        """
        gw = self._gateway()
        gw.rebind_whitelist({0x18DAF110})
        benign = self._frame(0x18DAF110, b"\x03\x22\xF1\x90")
        assert gw._frame_is_critical(benign) is False

    def test_a1f2_consecutive_frame_still_does_not_self_escalate(self) -> None:
        """Control: the CF/FC carve-out (pre-A1 contract) is preserved.

        A ConsecutiveFrame (0x2) carries no SID by design and must not
        self-escalate — collapsing it into critical would re-pin every benign
        multi-frame read behind the interlock.
        """
        gw = self._gateway()
        gw.rebind_whitelist({0x18DAF110})
        assert gw._frame_is_critical(self._frame(0x18DAF110, b"\x21\xEE\xFF\x11")) is False

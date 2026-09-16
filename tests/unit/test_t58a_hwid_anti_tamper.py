"""T58-A regression tests: HWID hardening (B-1/B-2/B-3), anti-tamper
launch gate (G-1t) and structured launcher auth logging (A-1).

Each test is written to FAIL against the pre-fix source and PASS after the
hardening, per the repo's TDD acceptance criterion.
"""

from __future__ import annotations

import re
from unittest.mock import patch

from src.security.hwid import collector
from src.security.hwid.collector import (
    ALLOWED_WMI_CLASSES,
    UNKNOWN_MAC,
    _compute_hardware_fingerprint,
    _wmi_query,
    collect_primary_mac,
)

# ---------------------------------------------------------------------------
# B-1 — uuid.getnode() random fallback flag
# ---------------------------------------------------------------------------


def test_b1_random_node_bit_detected_returns_unknown_mac() -> None:
    """uuid.getnode() sets bit 0x02 of the first octet when it cannot read a
    real MAC. That sentinel must be rejected: otherwise the fingerprint
    changes between processes (random node) and the license lock breaks."""
    random_node = (0x02 << 40) | (0x11 << 32) | (0x22 << 24) | (0x33 << 16) | (0x44 << 8) | 0x55
    with patch("src.security.hwid.collector._run_powershell", return_value=""):
        with patch("uuid.getnode", return_value=random_node):
            assert collect_primary_mac() == UNKNOWN_MAC


def test_b1_real_mac_detected_via_getnode() -> None:
    """A genuine (non-random) node still yields the formatted MAC."""
    real_node = (0xAC << 40) | (0xDE << 32) | (0x48 << 24) | (0x11 << 16) | (0x22 << 8) | 0x33
    with patch("src.security.hwid.collector._run_powershell", return_value=""):
        with patch("uuid.getnode", return_value=real_node):
            assert collect_primary_mac() == "AC:DE:48:11:22:33"


# ---------------------------------------------------------------------------
# B-2 — require at least two independent physical components
# ---------------------------------------------------------------------------


def test_b2_all_sentinels_mark_fingerprint_indeterminate() -> None:
    """When every component is a sentinel the fallback ``FALLBACK-<node>-<mac>``
    converges on cloned VMs. Such a fingerprint must be flagged indeterminate."""
    with (
        patch("src.security.hwid.collector.sys.platform", "win32"),
        patch.object(collector, "collect_motherboard_uuid", return_value="FALLBACK-host-A0:11:22:33:44:55"),
        patch.object(collector, "collect_cpu_processor_id", return_value="UNKNOWN_CPU"),
        patch.object(collector, "collect_disk_serial", return_value="UNKNOWN_DISK"),
        patch.object(collector, "collect_bios_serial", return_value="UNKNOWN_BIOS"),
        patch.object(collector, "collect_primary_mac", return_value=collector.UNKNOWN_MAC),
    ):
        fp = _compute_hardware_fingerprint()
    assert fp == collector.INDETERMINATE_FINGERPRINT


def test_b2_two_physical_components_is_determinate() -> None:
    """Two real components are enough for a stable, machine-bound identity."""
    with (
        patch("src.security.hwid.collector.sys.platform", "win32"),
        patch.object(collector, "collect_motherboard_uuid", return_value="FALLBACK-host-A0:11:22:33:44:55"),
        patch.object(collector, "collect_cpu_processor_id", return_value="BFEBFBFF000306A9"),
        patch.object(collector, "collect_disk_serial", return_value="WD-XYZ123"),
        patch.object(collector, "collect_bios_serial", return_value="UNKNOWN_BIOS"),
    ):
        fp = _compute_hardware_fingerprint()
    assert fp != collector.INDETERMINATE_FINGERPRINT
    assert re.fullmatch(r"[0-9a-f]{64}", fp)


# ---------------------------------------------------------------------------
# B-3 — WMI class/field allow-list
# ---------------------------------------------------------------------------


def test_b3_known_wmi_class_passes_allowlist() -> None:
    with patch.object(collector, "_run_powershell", return_value="XYZ") as mock_run:
        assert _wmi_query("Win32_BIOS", "SerialNumber") == "XYZ"
        mock_run.assert_called_once_with("(Get-CimInstance -ClassName Win32_BIOS).SerialNumber")


def test_b3_unknown_wmi_class_is_rejected() -> None:
    """B-3: the character allow-list permitted ``.`` and ``|`` so a class
    name could smuggle pipeline/metadata syntax. The class must be checked
    against a module-level allow-list (fail closed → empty)."""
    with patch.object(collector, "_run_powershell", return_value="PWNED") as mock_run:
        assert _wmi_query("Win32_BIOS).Defender | Get-Content C:/x", "SerialNumber") == ""
        mock_run.assert_not_called()


def test_b3_unknown_field_is_rejected() -> None:
    with patch.object(collector, "_run_powershell", return_value="PWNED") as mock_run:
        assert _wmi_query("Win32_BIOS", "SerialNumber; calc") == ""
        mock_run.assert_not_called()


def test_b3_allowlist_contains_expected_classes() -> None:
    for cls in (
        "Win32_ComputerSystemProduct",
        "Win32_Processor",
        "Win32_BIOS",
        "Win32_NetworkAdapterConfiguration",
    ):
        assert cls in ALLOWED_WMI_CLASSES


# ---------------------------------------------------------------------------
# G-1t — AntiTamperGuard.enforce() is wired into the production launch gate
# ---------------------------------------------------------------------------


def test_g1t_enforce_returns_none_on_clean_host_contract() -> None:
    """enforce() must be callable as a launch gate without side effects on a
    clean host (no debugger, fast timing)."""
    from src.security.anti_tamper.guard import AntiTamperGuard

    with (
        patch.object(AntiTamperGuard, "is_debugger_present", return_value=False),
        patch.object(AntiTamperGuard, "check_remote_debugger", return_value=False),
        patch.object(AntiTamperGuard, "detect_timing_anomaly", return_value=False),
    ):
        assert AntiTamperGuard.enforce() is None


def test_g1t_enforce_result_bound_to_launch_gate_and_blocks() -> None:
    """main() must consult AntiTamperGuard.enforce(): a violation has to make
    the entry point refuse to run the app (return non-zero)."""
    import src.main as main_mod
    from src.core.errors import SecurityError

    called: dict[str, bool] = {}

    def _raise(*_a, **_k):
        called["enforce"] = True
        raise SecurityError("Anti-tamper: debugger", code="ANTI_TAMPER_VIOLATION")

    with patch("src.security.anti_tamper.guard.AntiTamperGuard.enforce", side_effect=_raise):
        with patch.object(main_mod, "build_bus") as mock_bus:
            rc = main_mod.main(["--cli", "--interface", "virtual"])
    assert called.get("enforce") is True
    assert rc != 0
    mock_bus.assert_not_called()


def test_g1t_enforce_invoked_on_gui_path() -> None:
    """The desktop-app GUI path also runs the anti-tamper gate before startup."""
    import src.main as main_mod

    invoked: dict[str, bool] = {}

    def _ok(*_a, **_k):
        invoked["enforce"] = True

    with patch("src.security.anti_tamper.guard.AntiTamperGuard.enforce", side_effect=_ok):
        with patch.object(main_mod, "UniversalCanDesktopApp") as mock_app:
            mock_app.return_value.run.return_value = None
            rc = main_mod.main(["--interface", "virtual"])
    assert invoked.get("enforce") is True
    assert rc == 0


# ---------------------------------------------------------------------------
# A-1 (src/launcher/auth.py) is DEFERRED to T57-B to avoid a file collision:
# the operator blocked T58-A on auth.py because T57-B also edits the same
# get_current_status() error path. No A-1 test lives here.
# ---------------------------------------------------------------------------

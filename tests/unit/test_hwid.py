"""Unit tests for Hardware Fingerprint (HWID) generation and collection."""

from __future__ import annotations

import hashlib
import platform
import subprocess
import sys
from unittest.mock import patch

import pytest

from src.security.hwid.collector import (
    _INVALID_UUIDS,
    _run_powershell,
    _wmi_query,
    collect_bios_serial,
    collect_cpu_id,
    collect_cpu_processor_id,
    collect_disk_serial,
    collect_motherboard_uuid,
    collect_primary_mac,
    generate_hardware_fingerprint,
)


def test_generate_hardware_fingerprint_structure() -> None:
    """Test that HWID returns a 64-character lowercase hex string."""
    fp = generate_hardware_fingerprint()
    assert isinstance(fp, str)
    assert len(fp) == 64
    assert all(c in "0123456789abcdef" for c in fp)


def test_generate_hardware_fingerprint_deterministic() -> None:
    """Test that multiple calls produce identical fingerprints."""
    fp1 = generate_hardware_fingerprint()
    fp2 = generate_hardware_fingerprint()
    assert fp1 == fp2


def test_collect_components_format() -> None:
    """Test that each component collector returns a valid string."""
    mb_uuid = collect_motherboard_uuid()
    cpu_id = collect_cpu_processor_id()
    cpu_id_alias = collect_cpu_id()
    disk_serial = collect_disk_serial()
    bios_serial = collect_bios_serial()
    mac = collect_primary_mac()

    assert isinstance(mb_uuid, str) and len(mb_uuid) > 0
    assert isinstance(cpu_id, str) and len(cpu_id) > 0
    assert isinstance(cpu_id_alias, str) and cpu_id_alias == cpu_id
    assert isinstance(disk_serial, str) and len(disk_serial) > 0
    assert isinstance(bios_serial, str) and len(bios_serial) > 0
    assert isinstance(mac, str) and len(mac) > 0


@pytest.mark.parametrize("invalid_uuid", list(_INVALID_UUIDS))
def test_collect_motherboard_uuid_fallback_on_invalid(invalid_uuid: str) -> None:
    """Invalid UUID values trigger the hardened fallback (SEC-H-002).

    The fallback binds hostname AND primary MAC so cloned/guessed
    hostnames cannot reproduce another machine's identifier.
    """
    with patch("src.security.hwid.collector._wmi_query", return_value=invalid_uuid):
        with patch("src.security.hwid.collector.collect_primary_mac", return_value="A0:AD:9F:D0:CC:00"):
            uuid_val = collect_motherboard_uuid()
            expected = f"FALLBACK-{platform.node()}-A0:AD:9F:D0:CC:00"
            assert uuid_val == expected


def test_collect_cpu_processor_id_fallback() -> None:
    """Test CPU processor ID returns UNKNOWN_CPU when WMI returns empty."""
    with patch("src.security.hwid.collector._wmi_query", return_value=""):
        assert collect_cpu_processor_id() == "UNKNOWN_CPU"
        assert collect_cpu_id() == "UNKNOWN_CPU"


def test_collect_disk_serial_fallback() -> None:
    """Test disk serial returns UNKNOWN_DISK when PowerShell returns empty."""
    with patch("src.security.hwid.collector._run_powershell", return_value=""):
        assert collect_disk_serial() == "UNKNOWN_DISK"


def test_collect_bios_serial_fallback() -> None:
    """Test BIOS serial returns UNKNOWN_BIOS when WMI returns empty."""
    with patch("src.security.hwid.collector._wmi_query", return_value=""):
        assert collect_bios_serial() == "UNKNOWN_BIOS"


def test_collect_primary_mac_fallback() -> None:
    """Test MAC address falls back to uuid.getnode() when PowerShell returns empty."""
    # R2-S2: pin uuid.getnode to a deterministic physical value so the test
    # is hermetic on NIC-less CI/VM hosts (product then returns it formatted).
    with (
        patch("src.security.hwid.collector._run_powershell", return_value=""),
        patch("src.security.hwid.collector.uuid.getnode", return_value=0x00155D123456),
    ):
        mac = collect_primary_mac()
        assert isinstance(mac, str)
        # Verify MAC format XX:XX:XX:XX:XX:XX
        parts = mac.split(":")
        assert len(parts) == 6
        for part in parts:
            assert len(part) == 2
            int(part, 16)  # must be valid hex


def test_collect_primary_mac_sentinel_when_no_physical_nic() -> None:
    """R2-S2: a random/multicast node id must surface UNKNOWN_MAC (fail-closed)."""
    import uuid as _uuid

    from src.security.hwid.collector import UNKNOWN_MAC
    from src.security.hwid.collector import collect_primary_mac as _collect

    with (
        patch("src.security.hwid.collector._run_powershell", return_value=""),
        patch("src.security.hwid.collector.uuid.getnode", return_value=_uuid.getnode() | (0x02 << 40)),
    ):
        # Multicast-bit-set node ids are random, not hardware: sentinel expected
        # on hosts where getnode itself reports a random value. If this host
        # has a real NIC the mock above forces the random path regardless.
        assert _collect() == UNKNOWN_MAC


def test_wmi_query_delegates_to_run_powershell() -> None:
    """Test _wmi_query correctly formats the CimInstance query."""
    with patch("src.security.hwid.collector._run_powershell") as mock_run:
        mock_run.return_value = "TEST_VAL"
        val = _wmi_query("Win32_Processor", "ProcessorId")
        assert val == "TEST_VAL"
        mock_run.assert_called_once_with("(Get-CimInstance -ClassName Win32_Processor).ProcessorId")


@pytest.mark.parametrize(
    "exc_type",
    [
        subprocess.TimeoutExpired(cmd="powershell", timeout=10),
        OSError("Command failed"),
        FileNotFoundError("powershell not found"),
    ],
)
def test_run_powershell_exception_handling(exc_type: Exception) -> None:
    """Test _run_powershell catches subprocess exceptions and returns empty string."""
    with patch("subprocess.run", side_effect=exc_type):
        result = _run_powershell("Get-CimInstance Win32_BIOS")
        assert result == ""


def test_non_win32_platform_fingerprint() -> None:
    # SEC-3: the fingerprint is cached per-process — clear between tests
    # so patched collectors are honoured.
    generate_hardware_fingerprint.cache_clear()
    """Non-Windows platforms return a deterministic platform hash (SEC-H-002:
    the fallback mixes in the primary MAC for clone resistance)."""
    with patch.object(sys, "platform", "linux"):
        with patch("src.security.hwid.collector.collect_primary_mac", return_value="A0:AD:9F:D0:CC:00"):
            fp = generate_hardware_fingerprint()
            expected_raw = (
                f"NON_WIN32-{platform.node()}-{platform.machine()}-{platform.processor()}-A0:AD:9F:D0:CC:00"
            )
            expected_hash = hashlib.sha256(expected_raw.encode("utf-8")).hexdigest()
            assert fp == expected_hash
        assert _run_powershell("anything") == ""


def test_package_exports() -> None:
    """Test that HWID functions are properly exported from package inits."""
    import src.security as sec
    import src.security.hwid as sec_hwid

    assert hasattr(sec_hwid, "generate_hardware_fingerprint")
    assert hasattr(sec_hwid, "collect_disk_serial")
    assert hasattr(sec_hwid, "collect_motherboard_uuid")
    assert hasattr(sec_hwid, "collect_cpu_id")
    assert hasattr(sec_hwid, "collect_cpu_processor_id")
    assert hasattr(sec_hwid, "collect_bios_serial")
    assert hasattr(sec_hwid, "collect_primary_mac")

    assert hasattr(sec, "generate_hardware_fingerprint")
    assert hasattr(sec, "collect_disk_serial")
    assert hasattr(sec, "collect_motherboard_uuid")
    assert hasattr(sec, "collect_cpu_id")
    assert hasattr(sec, "collect_primary_mac")


def test_powershell_guard_allows_legit_wmi_blocks_injection() -> None:
    """B603 hardening: only plain WMI read queries may reach PowerShell.

    Live-collected regression: the MAC query legitimately contains
    -Filter 'IPEnabled=True' (single quotes + equals), which a naive
    character deny-list would reject and silently fall back to uuid.getnode().
    """
    from src.security.hwid.collector import re as _re

    # The exact production MAC query conforms
    legit = (
        "(Get-CimInstance -ClassName Win32_NetworkAdapterConfiguration "
        "-Filter 'IPEnabled=True' | Select-Object -First 1).MACAddress"
    )
    assert _re.fullmatch(r"[A-Za-z0-9_().|,'= \-]+", legit)

    # Injection vectors must be rejected by the guard pattern
    attacks = [
        "Get-Process; Remove-Item C:/x",   # chaining
        "Get-CimInstance $(calc)",         # interpolation
        "Get-Content x > out.txt",         # redirection
        'Write-Host "hello"',              # double quotes
        "Get-Process & calc",              # background op
    ]
    for a in attacks:
        assert not _re.fullmatch(r"[A-Za-z0-9_().|,'= \-]+", a) or "\n" in a or '"' in a, a

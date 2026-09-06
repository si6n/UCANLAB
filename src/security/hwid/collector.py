"""Windows Hardware Fingerprint (HWID) Collection via WMI/CIM API.

Collects 4-component hardware identity as specified in MASTER_PLAN.md Section 2.3:
  1. Motherboard UUID (Win32_ComputerSystemProduct.UUID)
  2. CPU Processor ID (Win32_Processor.ProcessorId)
  3. System Physical Disk Serial (PhysicalDrive0 via Win32_DiskDrive)
  4. BIOS Serial Number (Win32_BIOS.SerialNumber)

These components are hashed together to produce a stable, deterministic hardware fingerprint.
"""

from __future__ import annotations

import functools
import hashlib
import platform
import re
import subprocess
import sys
import uuid
from pathlib import Path

from src.core.logging import get_logger

logger = get_logger("security.hwid")

# Sentinel values that indicate invalid/missing WMI data
_INVALID_UUIDS = frozenset(
    {
        "00000000-0000-0000-0000-000000000000",
        "ffffffff-ffff-ffff-ffff-ffffffffffff",
        "FFFFFFFF-FFFF-FFFF-FFFF-FFFFFFFFFFFF",
        "",
    }
)


def _run_powershell(command: str) -> str:
    """Execute a single PowerShell command and return the trimmed output.

    Defense-in-depth for the B603 scanner finding: every caller passes a
    fixed WMI query built from module constants — no external input ever
    reaches here. The allow-list assert makes that contract explicit and
    fails loudly if a future caller tries to interpolate dynamic data.
    """
    if sys.platform != "win32":
        return ""
    # MED-1: Strict PowerShell command validation.
    # WMI/CIM property access like `(Get-CimInstance -ClassName Win32_X).Field` requires
    # letters, numbers, parentheses, hyphen, space, comma, single quote, pipe, and property dot.
    # Disallow leading dot or ./ or .\ to prevent dot-sourcing or relative script execution.
    trimmed = command.strip()
    if trimmed.startswith(".") or "/." in trimmed or "\\." in trimmed or ".." in trimmed:
        logger.warning("Rejected potential dot-sourcing PowerShell command", extra={"command": command[:80]})
        return ""

    if not re.fullmatch(r"[A-Za-z0-9_().|,'= \-]+", command) or "\n" in command or '"' in command:
        logger.warning("Rejected non-conforming PowerShell command", extra={"command": command[:80]})
        return ""
    try:
        # B3 (REVIEW): never resolve the PowerShell path from %SystemRoot% —
        # a compromised parent process can set SystemRoot (or pollute PATH)
        # and plant a fake powershell.exe that harvests the DPAPI-backed
        # fingerprint inputs. Resolve strictly through the Win32
        # GetSystemDirectoryW API and refuse to execute anything else.
        import ctypes

        buf = ctypes.create_unicode_buffer(260)
        get_system_dir = ctypes.windll.kernel32.GetSystemDirectoryW
        if not get_system_dir(buf, len(buf)):
            logger.warning("GetSystemDirectoryW failed; refusing to spawn PowerShell")
            return ""
        system_dir = buf.value
        if not system_dir:
            logger.warning("GetSystemDirectoryW returned empty path; refusing to spawn PowerShell")
            return ""
        # GetSystemDirectoryW already returns "<root>\System32"; the classic
        # PowerShell 5.1 binary lives under its WindowsPowerShell\v1.0 subtree.
        ps_executable = str(
            Path(system_dir, "WindowsPowerShell", "v1.0", "powershell.exe").resolve()
        )
        # Belt & braces: the resolved binary must live inside the real system
        # directory tree (no symlink/substitution escape).
        resolved_system_dir = str(Path(system_dir).resolve())
        if not ps_executable.startswith(resolved_system_dir) or not Path(ps_executable).is_file():
            logger.warning("PowerShell not found inside the system directory; refusing fallback")
            return ""

        cmd = [
            ps_executable,
            "-NoProfile",
            "-NonInteractive",
            "-Command",
            command,
        ]
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        return result.stdout.strip()
    except (subprocess.TimeoutExpired, OSError, FileNotFoundError) as exc:
        logger.warning(f"PowerShell command failed: {command}", extra={"error": str(exc)})
        return ""


def _wmi_query(wmi_class: str, field: str) -> str:
    """Execute a single WMI query via PowerShell and return the trimmed value."""
    return _run_powershell(f"(Get-CimInstance -ClassName {wmi_class}).{field}")


def collect_motherboard_uuid() -> str:
    """Collect Motherboard UUID from Win32_ComputerSystemProduct."""
    uuid_str = _wmi_query("Win32_ComputerSystemProduct", "UUID")
    if uuid_str.upper() in _INVALID_UUIDS or not uuid_str:
        # SEC-H-002: bind the fallback to a physical network identity too —
        # a bare hostname is guessable and identical on cloned machines.
        mac = collect_primary_mac()
        uuid_str = f"FALLBACK-{platform.node()}-{mac}"
        logger.info("Motherboard UUID invalid, using fallback", extra={"fallback_prefix": uuid_str[:16] + "…"})
    return uuid_str


def collect_cpu_processor_id() -> str:
    """Collect CPU Processor ID from Win32_Processor."""
    return _wmi_query("Win32_Processor", "ProcessorId") or "UNKNOWN_CPU"


def collect_cpu_id() -> str:
    """Collect CPU Processor ID from Win32_Processor."""
    return collect_cpu_processor_id()


def collect_disk_serial() -> str:
    """Collect system disk serial from the boot drive (PhysicalDrive0).

    SEC-1 (3FABLE): `Select-Object -First 1` returns whichever disk the
    CIM enumeration happens to list first — plugging a USB stick reorders
    it, silently changing the fingerprint and locking the license. Filter
    strictly by Index=0 (PhysicalDrive0).
    """
    serial = _run_powershell(
        "(Get-CimInstance -ClassName Win32_DiskDrive -Filter 'Index=0').SerialNumber"
    )
    return serial.strip() if serial else "UNKNOWN_DISK"


def collect_bios_serial() -> str:
    """Collect BIOS Serial Number from Win32_BIOS."""
    return _wmi_query("Win32_BIOS", "SerialNumber") or "UNKNOWN_BIOS"


def collect_primary_mac() -> str:
    """Collect primary network adapter MAC address."""
    mac = _run_powershell(
        "(Get-CimInstance -ClassName Win32_NetworkAdapterConfiguration -Filter 'IPEnabled=True' | Select-Object -First 1).MACAddress"
    )
    if mac:
        return mac.strip()
    node = uuid.getnode()
    return ":".join(f"{(node >> i) & 0xFF:02X}" for i in range(40, -8, -8))


def _compute_hardware_fingerprint() -> str:
    """Generate a deterministic SHA-256 hardware fingerprint from 4 components.

    Returns:
        64-character hex string representing the hardware identity hash.
    """
    mb_uuid = collect_motherboard_uuid()
    cpu_id = collect_cpu_processor_id()
    disk_serial = collect_disk_serial()
    bios_serial = collect_bios_serial()

    if (
        sys.platform != "win32"
        and mb_uuid.startswith("FALLBACK-")
        and cpu_id == "UNKNOWN_CPU"
        and disk_serial == "UNKNOWN_DISK"
        and bios_serial == "UNKNOWN_BIOS"
    ):
        # SEC-H-002: mix in the primary MAC so two identical machines
        # (same hostname pattern, same arch) still produce distinct
        # fingerprints.
        mac = collect_primary_mac()
        fallback = f"NON_WIN32-{platform.node()}-{platform.machine()}-{platform.processor()}-{mac}"
        return hashlib.sha256(fallback.encode("utf-8")).hexdigest()

    components = f"{mb_uuid}|{cpu_id}|{disk_serial}|{bios_serial}"
    fingerprint = hashlib.sha256(components.encode("utf-8")).hexdigest()

    logger.info(
        "Hardware fingerprint generated",
        extra={
            "mb_uuid": mb_uuid[:8] + "...",
            "cpu_id": cpu_id[:8] + "...",
            "disk": disk_serial[:8] + "...",
            "bios": bios_serial[:8] + "...",
        },
    )

    return fingerprint


# SEC-3 (3FABLE): the fingerprint is stable for the process lifetime — the
# collector spawns 4-5 PowerShell subprocesses (10 s timeouts each), and
# every cloud_get_status bridge call used to re-spawn them all, stalling
# the UI for tens of seconds. Compute once, serve from memory.
@functools.lru_cache(maxsize=1)
def generate_hardware_fingerprint() -> str:
    """Cached per-process wrapper around the fingerprint computation."""
    return _compute_hardware_fingerprint()


def clear_hwid_cache() -> None:
    """Helper to clear the LRU cache (ADD-2) for testing or hardware refresh."""
    generate_hardware_fingerprint.cache_clear()

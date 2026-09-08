"""TMC RP1210 (A/B/C) 64-Bit Isolated Ctypes Client Wrapper.

Matches MASTER_PLAN.md Section 8.1 / 19.2 (Task 0.3).
"""

from __future__ import annotations

import ctypes
import os
import sys
import threading
from types import TracebackType
from typing import Self

from src.core.errors import HardwareError, TransportError
from src.core.logging import get_logger
from src.hal.rp1210.types import RP1210ErrorCode

logger = get_logger("hal.rp1210")


class RP1210Client:
    """Standard-compliant TMC RP1210 client wrapper supporting NEXIQ, DPA5, Noregon adapters."""

    def __init__(self, dll_name: str, device_id: int = 1, protocol: str = "J1939") -> None:
        self.dll_name = dll_name
        self.device_id = device_id
        self.protocol = protocol
        self.client_id: int | None = None
        self._dll: ctypes.CDLL | None = None
        # H-H-002: send/disconnect/connect race guard — client_id is read
        # and mutated from multiple threads (bus RX loop vs teardown).
        self._lifecycle_lock = threading.Lock()
        # K-06: one pre-allocated RX scratch buffer shared by read_message
        # calls — at 5000 msg/s a per-call create_string_buffer would hammer
        # the allocator. Single-consumer under _lifecycle_lock.
        self._rx_scratch = ctypes.create_string_buffer(4096)
        self._rx_scratch_size = 4096

        self._load_dll()

    def _load_dll(self) -> None:
        """Dynamically load RP1210 64-bit or 32-bit DLL with safe error wrapping.

        D7/L-20 (P2-27): system directories are resolved via the Win32 API
        (GetSystemDirectoryW / GetWindowsDirectoryW), NOT the WINDIR
        environment variable — an attacker-controlled environment block
        must not be able to redirect DLL loading to a planted driver.
        """
        candidates: list[str] = []
        if sys.platform == "win32":
            try:
                buf = ctypes.create_unicode_buffer(260)
                res = ctypes.windll.kernel32.GetSystemDirectoryW(buf, 260)
                if res and 0 < res < 260:
                    candidates.append(os.path.join(buf.value, self.dll_name))
                buf2 = ctypes.create_unicode_buffer(260)
                res2 = ctypes.windll.kernel32.GetWindowsDirectoryW(buf2, 260)
                if res2 and 0 < res2 < 260:
                    candidates.append(os.path.join(buf2.value, "SysWOW64", self.dll_name))
            except Exception:  # noqa: BLE001 — API unavailable: fall back to hard defaults
                candidates = [
                    os.path.join("C:\\Windows", "System32", self.dll_name),
                    os.path.join("C:\\Windows", "SysWOW64", self.dll_name),
                ]
        else:
            candidates = [
                os.path.join("C:\\Windows", "System32", self.dll_name),
                os.path.join("C:\\Windows", "SysWOW64", self.dll_name),
            ]

        loaded = False
        last_err: Exception | None = None

        for path in candidates:
            try:
                # Use WinDLL on Windows for stdcall convention
                if sys.platform == "win32":
                    self._dll = ctypes.WinDLL(path)
                else:
                    self._dll = ctypes.CDLL(path)
                loaded = True
                logger.info("Loaded RP1210 DLL successfully", extra={"path": path})
                break
            except (OSError, FileNotFoundError) as exc:
                last_err = exc

        if not loaded or self._dll is None:
            raise HardwareError(
                f"RP1210 DLL '{self.dll_name}' could not be loaded. Please ensure the vendor driver is installed.",
                code="HARDWARE_DLL_NOT_FOUND",
                details={"dll_name": self.dll_name, "candidates": candidates},
                cause=last_err,
            )

        # L-20 (P2-27): verify the entry points the client actually calls —
        # loading a wrong-architecture or non-RP1210 DLL fails HERE with a
        # structured error instead of AttributeError mid-connect.
        required_exports = (
            "RP1210_ClientConnect",
            "RP1210_ClientDisconnect",
            "RP1210_SendMessage",
            "RP1210_ReadMessage",
        )
        missing = [name for name in required_exports if not hasattr(self._dll, name)]
        if missing:
            raise HardwareError(
                "Loaded DLL does not export the required RP1210 entry points",
                code="HARDWARE_DLL_INVALID",
                details={"dll_name": self.dll_name, "missing": missing},
            )

        # Setup ctypes function signatures
        self._setup_signatures()

    def _setup_signatures(self) -> None:
        """Define strict C argument and return types for RP1210 API functions."""
        if not self._dll:
            return

        # RP1210_ClientConnect(hwnd, nDeviceID, fpchProtocol, lTxBuf, lRxBuf, nBlockOnSend) -> short
        # HWND is pointer-sized (c_void_p) for correct 32-bit and 64-bit calling conventions
        if hasattr(self._dll, "RP1210_ClientConnect"):
            self._dll.RP1210_ClientConnect.argtypes = [
                ctypes.c_void_p,
                ctypes.c_short,
                ctypes.c_char_p,
                ctypes.c_long,
                ctypes.c_long,
                ctypes.c_short,
            ]
            self._dll.RP1210_ClientConnect.restype = ctypes.c_short

        # RP1210_ClientDisconnect(nClientID) -> short
        if hasattr(self._dll, "RP1210_ClientDisconnect"):
            self._dll.RP1210_ClientDisconnect.argtypes = [ctypes.c_short]
            self._dll.RP1210_ClientDisconnect.restype = ctypes.c_short

        # RP1210_SendMessage(nClientID, fpchMsg, nMsgSize, nNotify, nBlock) -> short
        if hasattr(self._dll, "RP1210_SendMessage"):
            self._dll.RP1210_SendMessage.argtypes = [
                ctypes.c_short,
                ctypes.c_char_p,
                ctypes.c_short,
                ctypes.c_short,
                ctypes.c_short,
            ]
            self._dll.RP1210_SendMessage.restype = ctypes.c_short

        # RP1210_ReadMessage(nClientID, fpchRxBuf, nBufSize, nBlock) -> short
        if hasattr(self._dll, "RP1210_ReadMessage"):
            self._dll.RP1210_ReadMessage.argtypes = [
                ctypes.c_short,
                ctypes.c_char_p,
                ctypes.c_short,
                ctypes.c_short,
            ]
            self._dll.RP1210_ReadMessage.restype = ctypes.c_short

        # RP1210_SendCommand(nCommandNumber, nClientID, fpchClientInfo, nInfoSize) -> short
        if hasattr(self._dll, "RP1210_SendCommand"):
            self._dll.RP1210_SendCommand.argtypes = [
                ctypes.c_short,
                ctypes.c_short,
                ctypes.c_char_p,
                ctypes.c_short,
            ]
            self._dll.RP1210_SendCommand.restype = ctypes.c_short

        # RP1210_GetErrorMsg(nErrorCode, fpchDescription) -> short
        if hasattr(self._dll, "RP1210_GetErrorMsg"):
            self._dll.RP1210_GetErrorMsg.argtypes = [ctypes.c_short, ctypes.c_char_p]
            self._dll.RP1210_GetErrorMsg.restype = ctypes.c_short

    def connect(self, tx_buffer_size: int = 8000, rx_buffer_size: int = 8000) -> int:
        """Establish client connection to the RP1210 adapter.

        B11 (REVIEW): `ctypes.create_string_buffer` already guarantees a
        NUL-terminated C buffer sized len(input)+1; the manual `+ b"\\x00"`
        appended a second trailing NUL byte. Healthy vendor DLLs stop at
        the first NUL, but strict RP1210 implementations can reject strings
        with embedded/trailing NULs — pass the protocol bytes exactly once.
        """
        if not self._dll:
            raise HardwareError("DLL not loaded")

        proto_buf = ctypes.create_string_buffer(self.protocol.encode("ascii"))
        with self._lifecycle_lock:
            client_id = self._dll.RP1210_ClientConnect(
                None,
                ctypes.c_short(self.device_id),
                proto_buf,
                ctypes.c_long(tx_buffer_size),
                ctypes.c_long(rx_buffer_size),
                0,
            )

            if client_id < 0 or client_id > 127:
                err_desc = self.get_error_message(client_id)
                raise HardwareError(
                    f"RP1210_ClientConnect failed with error code {client_id}: {err_desc}",
                    code="HARDWARE_CONNECT_FAILED",
                    details={"error_code": client_id, "description": err_desc},
                )

            self.client_id = int(client_id)
        logger.info("Connected to RP1210 adapter", extra={"client_id": client_id, "device_id": self.device_id})
        return int(client_id)

    def disconnect(self) -> None:
        """Gracefully disconnect from the RP1210 adapter."""
        with self._lifecycle_lock:
            if self.client_id is not None and self._dll:
                ret = self._dll.RP1210_ClientDisconnect(ctypes.c_short(self.client_id))
                if ret != RP1210ErrorCode.NO_ERRORS:
                    logger.warning("RP1210_ClientDisconnect returned error", extra={"error_code": ret})
                self.client_id = None

    def send_message(self, message_bytes: bytes, block: bool = False) -> None:
        """Transmit raw frame through RP1210 bus.

        H-H-005: the message must be genuine bytes of sane length — a stray
        None or oversized buffer would crash the vendor DLL.
        """
        if not isinstance(message_bytes, (bytes, bytearray)):
            raise HardwareError(
                f"RP1210_SendMessage requires bytes, got {type(message_bytes).__name__}",
                code="HARDWARE_FRAME_REJECTED",
            )
        message_bytes = bytes(message_bytes)
        if not (1 <= len(message_bytes) <= 2048):
            raise HardwareError(
                f"RP1210_SendMessage message length out of range (1..2048): {len(message_bytes)}",
                code="HARDWARE_FRAME_REJECTED",
            )

        with self._lifecycle_lock:
            # H-H-002: snapshot the handle under the lock so a concurrent
            # disconnect cannot swap it mid-call (TOCTOU).
            if self.client_id is None or not self._dll:
                raise HardwareError("RP1210 client is not connected")
            client_id = self.client_id
            ret = self._dll.RP1210_SendMessage(
                ctypes.c_short(client_id),
                message_bytes,
                ctypes.c_short(len(message_bytes)),
                0,
                1 if block else 0,
            )

        if ret != RP1210ErrorCode.NO_ERRORS:
            err_desc = self.get_error_message(ret)
            raise TransportError(
                f"RP1210_SendMessage failed: {err_desc}",
                code="TRANSPORT_TX_FAILED",
                details={"error_code": ret, "description": err_desc},
            )

    def read_message(self, buffer_size: int = 2048, block: bool = False) -> bytes | None:
        """Read received packet from RP1210 queue. Returns None if queue is empty.

        H-C-002/H-H-003: the buffer size is validated against the c_short
        ABI (1..32767) so a misconfigured size can never corrupt memory.
        K-06: uses the pre-allocated scratch buffer (sized 4096) instead of
        a per-call heap allocation; requested sizes above the scratch
        capacity allocate a temporary buffer as before.
        """
        if not (1 <= buffer_size <= 32767):
            raise HardwareError(
                f"RP1210_ReadMessage buffer_size out of range (1..32767): {buffer_size}",
                code="HARDWARE_CONFIG_INVALID",
            )

        with self._lifecycle_lock:
            if self.client_id is None or not self._dll:
                raise HardwareError("RP1210 client is not connected")
            client_id = self.client_id
            if buffer_size <= self._rx_scratch_size:
                rx_buffer = self._rx_scratch
            else:
                rx_buffer = ctypes.create_string_buffer(buffer_size)
            ret = self._dll.RP1210_ReadMessage(
                ctypes.c_short(client_id),
                rx_buffer,
                ctypes.c_short(buffer_size),
                1 if block else 0,
            )

            # REVIEW.md 2.1: per TMC RP1210C, a POSITIVE return value is the
            # number of bytes read (up to the buffer size); errors are
            # NEGATIVE RP1210 error codes. The old `0 < ret < 128` guard
            # misread any packet >= 128 bytes (J1939 TP / ISO-TP responses)
            # as an "error code" and crashed the diagnostic session.
            if ret > 0:
                return bytes(rx_buffer.raw[:ret])
            if ret == 0:
                return None  # Queue empty

            # Error code returned (negative)
            err_code = abs(ret)
            if err_code == RP1210ErrorCode.ERR_RX_QUEUE_FULL:
                # L-20 (P2-27): RP1210Client owns no metrics object (the
                # counters live on RP1210Bus) — the old hasattr guard was
                # dead code that never counted anything.
                logger.warning("RP1210 RX Queue is full; frame drops may occur")
                return None

            err_desc = self.get_error_message(err_code)
            raise HardwareError(
                f"RP1210_ReadMessage failed: {err_desc}",
                code="HARDWARE_READ_FAILED",
                details={"error_code": err_code, "description": err_desc},
            )

    def send_command(self, command_number: int, client_info: bytes = b"") -> int:
        """Execute RP1210_SendCommand for device/filter/protocol configuration.

        L-20 (P2-27): a DLL without the SendCommand export now fails LOUDLY
        — the old `return 0` reported success for configuration that never
        happened (silent misconfiguration of filters/baud).
        """
        with self._lifecycle_lock:
            if not self._dll:
                raise HardwareError("RP1210 DLL is not loaded", code="HARDWARE_DLL_NOT_FOUND")
            if not hasattr(self._dll, "RP1210_SendCommand"):
                raise HardwareError(
                    "Vendor DLL does not export RP1210_SendCommand — configuration command cannot be executed",
                    code="HARDWARE_DLL_INVALID",
                )
            if self.client_id is None:
                raise HardwareError("RP1210 client is not connected")
            client_id_snap = self.client_id
            info_buf = ctypes.create_string_buffer(client_info)
            ret = self._dll.RP1210_SendCommand(
                ctypes.c_short(command_number),
                ctypes.c_short(client_id_snap),
                info_buf,
                ctypes.c_short(len(client_info)),
            )
            if ret != RP1210ErrorCode.NO_ERRORS:
                err_desc = self.get_error_message(ret)
                logger.warning(
                    "RP1210_SendCommand returned non-zero",
                    extra={"cmd": command_number, "error_code": ret, "desc": err_desc},
                )
            return int(ret)

    def get_error_message(self, error_code: int) -> str:
        """Fetch descriptive error string from RP1210 DLL or fallback dictionary."""
        if self._dll and hasattr(self._dll, "RP1210_GetErrorMsg"):
            desc_buf = ctypes.create_string_buffer(256)
            ret = self._dll.RP1210_GetErrorMsg(ctypes.c_short(error_code), desc_buf)
            if ret == 0:
                raw_str = desc_buf.value.decode("ascii", errors="ignore").strip()
                if raw_str:
                    return raw_str

        return RP1210ErrorCode.get_description(error_code)

    def __enter__(self) -> Self:
        self.connect()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.disconnect()

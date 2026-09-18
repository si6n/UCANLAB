"""High-Reliability ISO 14229 UDS ECU Flashing & Bootloader Reprogramming Engine.

Enforces CORE_SAFETY_FLOOR, dual-confirmation, and full UDS download sequence
(0x10 Extended/Programming -> 0x27 SecurityAccess -> 0x34 RequestDownload ->
 0x36 TransferData in blocks -> 0x37 RequestTransferExit -> 0x31 RoutineControl Checksum -> 0x11 ECUReset).
"""

from __future__ import annotations

import hashlib
import math
import threading
import time
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, ClassVar

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric import ed25519

from src.core.errors import ProtocolError, SafetyError
from src.core.logging import get_logger
from src.protocols.uds.firmware import FirmwareContainer
from src.protocols.uds.services import DiagnosticSessionType, RoutineControlType

if TYPE_CHECKING:
    from src.protocols.uds.client import UdsClient
    from src.safety.gateway import TxSafetyGateway

logger = get_logger("protocols.uds.flasher")


class FlashingStep(StrEnum):
    """Sequential stages of the ECU reprogramming lifecycle."""

    IDLE = "Boşta (Hazır)"
    SAFETY_VALIDATION = "1. Güvenlik & Hız Kilidi Doğrulaması"
    EXTENDED_SESSION = "2. Genişletilmiş Diyagnostik Oturumu (0x10 0x03)"
    SECURITY_ACCESS = "3. Güvenlik Erişimi & Tohum-Anahtar (0x27)"
    PROGRAMMING_SESSION = "4. Programlama / Bootloader Oturumu (0x10 0x02)"
    REQUEST_DOWNLOAD = "5. Bellek İndirme Talebi (0x34)"
    TRANSFER_DATA = "6. Blok Veri Aktarımı (0x36)"
    TRANSFER_EXIT = "7. Aktarım Çıkışı ve Tamamlama (0x37)"
    CHECKSUM_VERIFICATION = "8. Sağlama Toplamı / CRC32 Doğrulaması (0x31)"
    ECU_RESET = "9. ECU Yeniden Başlatma / Hard Reset (0x11)"
    COMPLETED = "10. Flashing Başarıyla Tamamlandı"
    FAILED = "Hata / İptal Edildi"


@dataclass(slots=True)
class FlashingConfig:
    """Reprogramming configuration payload."""

    # REVIEW (Protocol hardening): ECU-controlled block-size cap. The ECU's
    # maxNumberOfBlockLength is additionally clamped to MAX_BLOCK_LENGTH_CAP
    # in _parse_max_block_length. The security control is the UPPER bound
    # (oversized chunks -> OOM/bus flood/ECU overflow); small blocks are
    # safe and kept legal for unit-test wraparound coverage.
    ALLOWED_BLOCK_SIZES: ClassVar[frozenset[int]] = frozenset({64, 128, 256, 512, 1024, 2048, 4096})
    MAX_BLOCK_LENGTH_CAP: ClassVar[int] = 4096
    MIN_BLOCK_SIZE: ClassVar[int] = 1

    memory_address: int = 0
    data: bytes = b""
    container: FirmwareContainer | None = None
    block_size: int = 256  # 64, 128, 256, 512, 1024, 2048, 4096 bytes
    security_level: int = 1
    security_key: bytes | None = None
    # P-1 (3FABLE): ISO 14229 seed-key is a CHALLENGE-RESPONSE — the key is
    # derived from the ECU's fresh seed, never a static value. Callers pass
    # a derivation callable (e.g. an OEM algorithm or a tool-side KDF);
    # `security_key` remains only for genuinely fixed-key ECUs.
    key_derivation: Callable[[bytes, int], bytes] | None = None
    # P1-5: ISO 14229 re-locks security on session transitions — the
    # programming session typically requires its own (usually higher)
    # security level. Defaults to security_level when not set.
    programming_security_level: int | None = None
    verify_checksum: bool = True
    checksum_routine_id: int = 0x0202
    erase_routine_id: int | None = None
    erase_routine_options: bytes = b""
    expected_vin: str | None = None
    expected_serial: str | None = None
    # REVIEW 3-CRITICAL: optional ECU memory-profile bounds enforced BEFORE
    # 0x34 RequestDownload leaves the tool. Address/size beyond the ECU's
    # flash window would otherwise be rejected by the ECU AFTER the erase
    # routine already wiped the target region (unrecoverable brick window).
    memory_min_address: int = 0
    memory_max_address: int = 0x1FFFFFFFFFFFFFFF  # sane 64-bit default
    memory_alignment: int = 1  # 1 = no alignment requirement
    max_image_bytes: int = 0xFFFFFFFFFFFFFFFF  # sane 64-bit default
    reset_after_flash: bool = True
    reset_type: int = 0x01  # Hard Reset
    user_confirmed: bool = False
    # T57-D / F-1 (YÜKSEK): tool-side firmware AUTHENTICITY + anti-rollback.
    # The old review-era block only checked `len(firmware_signature)` and
    # logged `min_version` — a tampered/unsigned image was flashed and the
    # anti-rollback floor was never enforced (fail-open). The signature is
    # now verified cryptographically (Ed25519) over `bytes(data)` against a
    # trusted public key; both the signature and the trust anchor are
    # MANDATORY by default (`require_signature=True`).
    firmware_signature: bytes | None = None
    min_version: int = 0
    trusted_pubkey: ed25519.Ed25519PublicKey | None = None
    require_signature: bool = True
    # DID that exposes the ECU's current application software version, read
    # for the anti-rollback comparison (ISO 14229 F189 = VM ECU software
    # version). Overridable for OEMs that expose the version elsewhere.
    version_did: int = 0xF189
    # T57-D / F-4 (ORTA): target-identity verification (VIN/serial) was
    # optional and OFF by default, so a multi-ECU bus could be flashed on the
    # WRONG ECU. Mandatory by default: at least one of expected_vin /
    # expected_serial must be supplied unless the operator explicitly opts
    # out with require_target_identity=False.
    require_target_identity: bool = True
    # T57-D / F-2: gateway-issued single-use HMAC confirmation token factory.
    # The flasher mints a fresh token for EVERY critical step (bound to the
    # client's arbitration id) so a production gateway with a
    # `confirmation_secret` accepts the flash instead of failing closed at
    # step 2. When the gateway has no secret (legacy wiring) the factory
    # returns None and the parameter is omitted.
    confirmation_token_factory: Callable[[int], bytes | str | None] | None = None

    def __post_init__(self) -> None:
        if self.container is not None:
            if not self.data:
                base_addr, bin_data = self.container.get_continuous_binary()
                if self.memory_address == 0 and base_addr != 0:
                    object.__setattr__(self, "memory_address", base_addr)
                object.__setattr__(self, "data", bin_data)

        if not (self.MIN_BLOCK_SIZE <= self.block_size <= self.MAX_BLOCK_LENGTH_CAP):
            raise ValueError(
                f"Invalid block_size {self.block_size}. "
                f"Must be {self.MIN_BLOCK_SIZE}..{self.MAX_BLOCK_LENGTH_CAP} bytes (fail-closed cap)."
            )
        if self.block_size not in self.ALLOWED_BLOCK_SIZES:
            import logging as _logging

            _logging.getLogger("universal_can.protocols.uds.flasher").warning(
                "Non-standard block_size %d (standard: %s)",
                self.block_size,
                sorted(self.ALLOWED_BLOCK_SIZES),
            )
        if not self.data:
            raise ValueError("Flashing payload data cannot be empty.")

        # REVIEW 3-CRITICAL preflight (fail BEFORE any frame leaves the tool):
        # image size, address window, and alignment validated against the
        # ECU profile bounds so a doomed download is rejected here, not after
        # the erase routine already wiped the target region.
        if not (1 <= len(self.data) <= self.max_image_bytes):
            raise ValueError(
                f"Image size {len(self.data)} exceeds ECU profile max ({self.max_image_bytes})"
            )
        if not (self.memory_min_address <= self.memory_address):
            raise ValueError(
                f"Memory address 0x{self.memory_address:X} below ECU profile minimum 0x{self.memory_min_address:X}"
            )
        end_address = self.memory_address + len(self.data)
        if end_address > self.memory_max_address + 1:
            raise ValueError(
                f"Image end address 0x{end_address:X} exceeds ECU profile maximum 0x{self.memory_max_address:X}"
            )
        if self.memory_alignment > 1 and (self.memory_address % self.memory_alignment) != 0:
            raise ValueError(
                f"Memory address 0x{self.memory_address:X} not {self.memory_alignment}-byte aligned"
            )

    @property
    def effective_programming_security_level(self) -> int:
        """Security level applied inside the programming session."""
        return self.programming_security_level if self.programming_security_level is not None else self.security_level


@dataclass(slots=True)
class FlashingProgress:
    """Progress update telemetry emitted to UI/callbacks."""

    current_step: FlashingStep
    step_index: int
    total_steps: int
    bytes_transferred: int
    total_bytes: int
    percent: float
    transfer_speed_kbps: float
    elapsed_time_s: float
    crc32_checksum: str


class EcuFlashingEngine:
    """Deterministic, step-by-step UDS ECU reprogramming orchestrator."""

    TOTAL_STEPS: int = 10

    def __init__(
        self,
        uds_client: UdsClient,
        gateway: TxSafetyGateway,
        on_progress: Callable[[FlashingProgress], None] | None = None,
        on_log: Callable[[str, str], None] | None = None,
        confirmation_token_factory: Callable[[int], bytes | str | None] | None = None,
    ) -> None:
        self.uds_client = uds_client
        self.gateway = gateway
        self.on_progress = on_progress
        self.on_log = on_log
        # R2-P2: composition-root-owned mint capability. When supplied, step
        # tokens are minted through this factory — the engine never touches
        # the gateway's public mint directly.
        self._issuer_factory = confirmation_token_factory

        self.current_step: FlashingStep = FlashingStep.IDLE
        self._is_cancelled = False
        # T57-D / F-2: active config stashed so per-step critical calls can
        # mint their gateway confirmation token without threading the config
        # through every helper.
        self._active_config: FlashingConfig | None = None

    def _confirmation_token(self) -> bytes | str | None:
        """Mint a fresh single-use gateway confirmation token for a critical step.

        T57-D / F-2 (YÜKSEK): the flasher used to present NO token on any
        critical step, so a production gateway with a configured
        `confirmation_secret` rejected the flash at step 2
        (DualConfirmationRequiredError) — flash was unusable, and the only
        "fix" would have been to enable the legacy boolean bypass, re-opening
        the T41/G-3 confirmation gap. The token is bound to the client's
        arbitration id (the frame that will carry it) and is minted ONCE per
        critical step (single-use, short TTL).

        Resolution order:
          1. Engine-level `confirmation_token_factory` (R2-P2: passed by the
             composition root at construction — preferred).
          2. `config.confirmation_token_factory` (composition-root supplied).
          3. The gateway's own `issue_confirmation_token` when it has a
             confirmation secret (legacy composition roots).
        Returns None when no secret is wired (legacy boolean path), so the
        parameter is simply omitted.
        """
        config = self._active_config
        arb_id = int(getattr(self.uds_client, "tx_id", 0x7E0))
        if self._issuer_factory is not None:
            return self._issuer_factory(arb_id)
        if config is not None and config.confirmation_token_factory is not None:
            return config.confirmation_token_factory(arb_id)
        gateway = self.gateway
        if getattr(gateway, "_confirmation_secret", None) is None:
            return None
        issuer = getattr(gateway, "issue_confirmation_token", None)
        if issuer is None:
            return None
        return issuer(arb_id, ttl_s=30.0)

    @staticmethod
    def _parse_ecu_version(response: Any) -> int | None:
        """Parse the ECU application software version from a 0x22 read.

        Accepts an ASCII decimal version (e.g. `b'\\xf1\\x89' + b'7'` -> 7).
        Anything else is unverifiable (returns None) — the caller fails
        closed rather than treating an unparsable value as "new enough".
        """
        data = getattr(response, "data", None)
        if not data:
            return None
        raw = bytes(data)
        # Strip the 2-byte DID echo (`62 F1 89 <value>` -> `data` is
        # `F1 89 <value>`) when present.
        if len(raw) >= 2 and raw[:2] == bytes([0xF1, 0x89]):
            raw = raw[2:]
        text = raw.decode("ascii", errors="replace").strip()
        if text.isdigit():
            return int(text)
        return None

    def _call_transfer_data(self, block_sequence: int, data: bytes, user_confirmed: bool = False) -> Any:
        """Invoke transfer_data under the fixed critical-command contract.

        P1-1 (REVIEW M-8/H-4): the operator's dual confirmation is granted
        ONCE per flashing session via FlashingConfig.user_confirmed (asserted
        at step 1 and bound to 0x34/0x31/0x11) and forwarded to every 0x36
        block — the old hardcoded user_confirmed=True lied to the gateway's
        Stage-5 check on every block, making the dual-confirmation gate a
        no-op for the one service that writes flash.

        REVIEW 2 CRITICAL-1: the old inspect.signature fallback silently
        dropped is_critical_command=True when the client signature did not
        offer it — an unflagged 0x36 flash write. Now the contract is FIXED:
        every call carries is_critical_command=True; a client that cannot
        accept it fails closed with SafetyError (never an unflagged send).
        """
        try:
            return self.uds_client.transfer_data(
                block_sequence=block_sequence,
                data=data,
                is_critical_command=True,
                user_confirmed=user_confirmed,
                confirmation_token=self._confirmation_token(),
            )
        except TypeError as exc:
            raise SafetyError(
                "UDS client violates the transfer_data safety contract: "
                "is_critical_command/user_confirmed parameters are mandatory "
                "(fail-closed, refusing unflagged flash write)",
                code="TRANSFER_DATA_CONTRACT_VIOLATION",
            ) from exc

    def cancel(self) -> None:
        """Signal engine to abort flashing safely at next boundary."""
        self._is_cancelled = True
        self._log("İptal talebi alındı! Flashing durduruluyor...", "warning")

    def _check_cancelled(self) -> None:
        """P1-8: cancellation is honoured at EVERY step boundary, not only
        inside the 0x36 loop — steps 2-9 can each take seconds on a slow bus."""
        if self._is_cancelled:
            raise ProtocolError("Flashing kullanıcı tarafından iptal edildi.")

    @staticmethod
    def _parse_max_block_length(download_response: Any) -> int:
        """Parse maxNumberOfBlockLength from a positive 0x34 response (P1-6).

        Response layout per ISO 14229-1 §14.2:
            lengthFormatIdentifier (1B) | maxNumberOfBlockLength (n bytes)

        The high nibble encodes the width (1..8, 0xF = variable) and the
        low nibble the address unit size. An unparsable/absent value fails
        closed — the caller must never flash with an unbounded block size.
        """
        data = getattr(download_response, "data", None)
        if not data or len(data) < 1:
            raise ProtocolError(
                "0x34 yanıtı maxNumberOfBlockLength içermiyor — sınırsız blok boyutu ile flash reddedildi (fail-closed)"
            )
        length_format = data[0]
        num_bytes = (length_format >> 4) & 0x0F
        if not (1 <= num_bytes <= 8):
            raise ProtocolError(
                f"0x34 yanıtı geçersiz lengthFormatIdentifier (0x{length_format:02X}) — flash reddedildi (fail-closed)"
            )
        raw = bytes(data[1 : 1 + num_bytes])
        if len(raw) < num_bytes:
            raise ProtocolError("0x34 yanıtı maxNumberOfBlockLength alanı kesik — flash reddedildi (fail-closed)")
        value = int.from_bytes(raw, byteorder="big")
        if value <= 0:
            raise ProtocolError("0x34 yanıtı maxNumberOfBlockLength=0 — flash reddedildi (fail-closed)")
        # REVIEW hardening: ECU-controlled bound — clamp to the tool-side
        # ceiling so a malicious/broken ECU cannot force OOM-size blocks.
        if value > FlashingConfig.MAX_BLOCK_LENGTH_CAP:
            logger.warning(
                "0x34 maxNumberOfBlockLength tavana çekildi (ECU değeri sınırlı)",
                extra={
                    "ecu_value": value,
                    "cap": FlashingConfig.MAX_BLOCK_LENGTH_CAP,
                },
            )
            return FlashingConfig.MAX_BLOCK_LENGTH_CAP
        return value

    def _assert_gateway_preconditions(self) -> None:
        """P1-8: verify every TX precondition before touching the ECU.

        Once the first session-control frame leaves, a mid-sequence gateway
        rejection leaves the ECU in a programming session with the recovery
        reset itself blocked (ESTOP_ACTIVE). Checking supervisor state,
        watchdog lease, and speed interlock up front turns those failures
        into step-1 SafetyErrors — before the ECU is disturbed.
        """
        estop = self.gateway.estop
        if estop.is_engaged:
            raise SafetyError("E-Stop aktifken flashing yapılamaz!")

        supervisor = getattr(self.gateway, "supervisor", None)
        if supervisor is not None and not getattr(supervisor, "is_tx_permitted", True):
            raise SafetyError(
                "Güvenlik süpervizörü TX izni vermiyor (FAULT/ARMED olmayan durum) — flashing reddedildi."
            )

        watchdog = getattr(self.gateway, "watchdog", None)
        if watchdog is not None and not getattr(watchdog, "is_lease_valid", True):
            raise SafetyError(
                "TX watchdog kira süresi geçersiz — flashing reddedildi (lease yenileyin)."
            )

        # Speed interlock: check through public gateway API when available
        if hasattr(self.gateway, "is_speed_fresh_and_safe"):
            if not self.gateway.is_speed_fresh_and_safe():
                raise SafetyError("Hız telemetrisi yok/taze değil veya araç hareket halinde — flashing reddedildi.")
        else:
            # Fallback for mock gateways in unit tests
            last_update_ns = getattr(self.gateway, "_last_speed_update_ns", None)
            if not isinstance(last_update_ns, int) or last_update_ns == 0:
                raise SafetyError("Hız telemetrisi yok/taze değil — hareketli araçta flashing reddedildi.")
            speed = getattr(self.gateway, "_physical_speed_kmh", 0.0)
            threshold = getattr(self.gateway, "SPEED_NOISE_THRESHOLD_KMH", 0.5)
            if not isinstance(speed, (int, float)) or isinstance(speed, bool):
                raise SafetyError("Hız telemetrisi geçersiz (non-numeric) — flashing reddedildi.")
            if math.isnan(float(speed)) or math.isinf(float(speed)):
                raise SafetyError("Hız telemetrisi geçersiz (NaN/Inf) — flashing reddedildi.")
            if not isinstance(threshold, (int, float)) or isinstance(threshold, bool):
                threshold = 0.5
            if float(speed) > float(threshold):
                raise SafetyError(
                    f"Araç hareket halinde ({float(speed):.1f} km/s > {float(threshold)} km/s) — flashing reddedildi."
                )

    def _log(self, message: str, level: str = "info") -> None:
        if self.on_log:
            self.on_log(message, level)
        if level == "error":
            logger.error(message)
        elif level == "warning":
            logger.warning(message)
        else:
            logger.info(message)

    def _emit_progress(
        self,
        step: FlashingStep,
        step_idx: int,
        bytes_sent: int,
        total_bytes: int,
        start_time: float,
        crc_str: str,
    ) -> None:
        self.current_step = step
        now = time.monotonic()
        elapsed = max(0.001, now - start_time)
        pct = (bytes_sent / total_bytes * 100.0) if total_bytes > 0 else 0.0
        speed_kbps = (bytes_sent / 1024.0) / elapsed

        progress = FlashingProgress(
            current_step=step,
            step_index=step_idx,
            total_steps=self.TOTAL_STEPS,
            bytes_transferred=bytes_sent,
            total_bytes=total_bytes,
            percent=round(pct, 1),
            transfer_speed_kbps=round(speed_kbps, 2),
            elapsed_time_s=round(elapsed, 2),
            crc32_checksum=crc_str,
        )

        if self.on_progress:
            self.on_progress(progress)

    # REVIEW.md 3.3: S3 tester-present cadence. ISO 14229 S3 is 5 s; send at
    # 2 s so one lost/deferred TesterPresent never drops the session during
    # long erase/key-computation gaps.
    TESTER_PRESENT_INTERVAL_S: ClassVar[float] = 2.0

    def _tester_present_loop(self, stop_event: threading.Event, config: FlashingConfig) -> None:
        """Background keep-alive: TesterPresent 0x3E (suppress) while flashing.

        REVIEW.md 3.3: in extended/programming sessions the ECU runs the S3
        timer (2-5 s). A 0x36 block whose erase cycle stalls the bus for a
        few seconds otherwise returns the ECU to the default session, and
        the NEXT TransferData gets rejected with NRC 0x7E — a half-written
        flash. This loop keeps the session alive for the whole sequence.
        """
        while not stop_event.wait(self.TESTER_PRESENT_INTERVAL_S):
            try:
                # suppressPosResponse bit (0x80): fire-and-forget, no reply
                # is expected and none is waited for.
                self.uds_client.tester_present(suppress_response=True)
            except Exception as exc:  # noqa: BLE001 — keep-alive must never kill the flash
                logger.debug("TesterPresent keep-alive failed (retrying)", extra={"error": str(exc)})

    def execute_flash(self, config: FlashingConfig) -> bool:
        """Execute full end-to-end ECU flashing cycle synchronously."""
        self._is_cancelled = False
        self._active_config = config
        start_time = time.monotonic()
        total_bytes = len(config.data)
        if total_bytes == 0:
            raise ValueError("Flashing payload data is empty")

        crc32_val = zlib.crc32(config.data) & 0xFFFFFFFF
        crc_hex = f"0x{crc32_val:08X}"
        # REVIEW hardening: SHA-256 alongside CRC32 (transport integrity vs
        # cryptographic identity). CRC32 alone is forgeable; the SHA-256 is
        # audit-logged for every flash.
        sha256_hex = hashlib.sha256(bytes(config.data)).hexdigest()
        self._log(
            f"İmaj bütünlüğü: CRC32={crc_hex} SHA-256={sha256_hex} Boyut={total_bytes}",
            "info",
        )

        # T57-D / F-1 (YÜKSEK): cryptographic firmware authenticity — fail-closed.
        # The old block only checked that `firmware_signature` was non-empty and
        # logged `min_version`; a tampered/unsigned image passed and the
        # anti-rollback floor was never compared. The Ed25519 signature is now
        # verified over the exact image bytes against the embedded trust anchor
        # BEFORE any frame leaves the tool.
        if config.require_signature:
            if config.firmware_signature is None or len(config.firmware_signature) == 0:
                raise SafetyError(
                    "firmware_signature zorunlu — imzasız imaj flash edilemez (fail-closed).",
                    code="FLASH_SIGNATURE_MISSING",
                )
            if config.trusted_pubkey is None:
                raise SafetyError(
                    "Doğrulama açık anahtarı (trusted_pubkey) yapılandırılmamış — "
                    "imza doğrulanamaz (fail-closed).",
                    code="FLASH_TRUST_ANCHOR_MISSING",
                )
            try:
                config.trusted_pubkey.verify(config.firmware_signature, bytes(config.data))
            except InvalidSignature as exc:
                raise SafetyError(
                    "Firmware imzası geçersiz — kurcalanmış/yetkisiz imaj reddedildi (fail-closed).",
                    code="FLASH_SIGNATURE_INVALID",
                ) from exc
            except Exception as exc:  # noqa: BLE001 — malformed key/sig also fails closed
                raise SafetyError(
                    "Firmware imzası doğrulanamadı (bozuk imza/anahtar) — flash reddedildi (fail-closed).",
                    code="FLASH_SIGNATURE_INVALID",
                ) from exc
            self._log(
                f"✅ Firmware imzası doğrulandı (Ed25519, boyut={len(config.firmware_signature)} B).",
                "info",
            )
        elif config.firmware_signature is not None and len(config.firmware_signature) == 0:
            # A present-but-empty signature is always malformed, even when
            # verification is explicitly opted out.
            raise SafetyError(
                "Boş firmware_signature ile flash reddedildi (fail-closed).",
                code="FLASH_SIGNATURE_INVALID",
            )

        # T57-D / F-4 (ORTA): target-identity verification is mandatory by
        # default. Flashing a multi-ECU bus without pinning the target risks
        # writing the image to the wrong ECU. Require at least one identity
        # anchor up front (before any TX); the actual DID comparison happens
        # once the extended session is open.
        if config.require_target_identity and config.expected_vin is None and config.expected_serial is None:
            raise SafetyError(
                "Hedef kimlik doğrulaması zorunlu (expected_vin veya expected_serial) — "
                "yanlış ECU'ya flash riski nedeniyle reddedildi (fail-closed).",
                code="FLASH_TARGET_IDENTITY_REQUIRED",
            )
        if not config.require_signature:
            self._log(
                "UYARI: require_signature=False — firmware imza doğrulaması atlandı "
                "(operatör açıkça devre dışı bıraktı).",
                "warning",
            )

        # REVIEW.md 3.3: start the S3 keep-alive before the first session
        # control frame; stop it on completion, failure, or cancellation.
        keepalive_stop = threading.Event()
        keepalive_thread = threading.Thread(
            target=self._tester_present_loop,
            args=(keepalive_stop, config),
            daemon=True,
            name="uds_tester_present",
        )
        keepalive_thread.start()

        try:
            return self._execute_flash_inner(config, start_time, crc32_val, crc_hex, sha256_hex)
        finally:
            keepalive_stop.set()
            keepalive_thread.join(timeout=self.TESTER_PRESENT_INTERVAL_S * 2)

    def _execute_flash_inner(
        self,
        config: FlashingConfig,
        start_time: float,
        crc32_val: int,
        crc_hex: str,
        sha256_hex: str,
    ) -> bool:
        total_bytes = len(config.data)
        self._log(
            f"🚀 Flashing Başlatılıyor: Boyut={total_bytes} bayt, Hedef Adres=0x{config.memory_address:08X}, CRC32={crc_hex}",
            "info",
        )

        recovery_needed = False
        try:
            # 1. Safety Validation
            self._emit_progress(FlashingStep.SAFETY_VALIDATION, 1, 0, total_bytes, start_time, crc_hex)
            self._log("Adım 1/10: Hız kilidi ve güvenlik kontrolleri yapılıyor...", "info")
            if not config.user_confirmed:
                raise SafetyError("Flashing işlemi operatörün açık çift onayını (Dual-Confirmation) gerektirir.")

            if self.gateway.estop.is_engaged:
                raise SafetyError("Acil Durdurma (E-Stop) aktifken flashing yapılamaz!")

            # P1-8: verify ALL gateway preconditions BEFORE a single byte
            # reaches the ECU. Once the ECU leaves its default session a
            # failed TX must recover it — cheaper to fail here, at step 1.
            self._assert_gateway_preconditions()

            # 2. Extended Diagnostic Session
            self._emit_progress(FlashingStep.EXTENDED_SESSION, 2, 0, total_bytes, start_time, crc_hex)
            self._log("Adım 2/10: Genişletilmiş Diyagnostik Oturumu (0x10 0x03) açılıyor...", "info")
            self._check_cancelled()
            # REVIEW 3-CRITICAL: no TypeError fallback — a client signature
            # without user_confirmed is a contract violation and must fail
            # hard, never silently re-issue the request with the dual
            # confirmation flag dropped (gateway Stage-5 bypass).
            resp = self.uds_client.change_session(
                DiagnosticSessionType.EXTENDED_DIAGNOSTIC_SESSION,
                user_confirmed=True,
                confirmation_token=self._confirmation_token(),
            )
            if not resp.is_positive:
                raise ProtocolError(f"Genişletilmiş oturum açılamadı: {resp.nrc_description_tr} (NRC 0x{resp.nrc:02X})")
            # From here on the ECU is out of its default session; a failure
            # must attempt best-effort recovery before surfacing the error.
            recovery_needed = True

            # Step 2b: Target Identity Verification (VIN / Serial DID match)
            if config.expected_vin is not None:
                self._log(f"Hedef VIN doğrulaması yapılıyor (Beklenen: {config.expected_vin})...", "info")
                vin_resp = self.uds_client.read_did(0xF190)
                if not vin_resp.is_positive:
                    raise ProtocolError(f"ECU VIN (0xF190) okunamadı: {vin_resp.nrc_description_tr}")
                raw_vin = vin_resp.data[2:] if len(vin_resp.data) >= 2 and vin_resp.data[:2] == b"\xf1\x90" else vin_resp.data
                actual_vin = raw_vin.decode("ascii", errors="replace").strip()
                if actual_vin != config.expected_vin:
                    raise SafetyError(
                        f"Araç VIN uyuşmazlığı! Beklenen: {config.expected_vin}, Okunan: {actual_vin} (Flashing fail-closed reddedildi)"
                    )
                self._log(f"✅ VIN başarıyla doğrulandı: {actual_vin}", "info")

            if config.expected_serial is not None:
                self._log(f"Hedef Seri Numarası doğrulaması yapılıyor (Beklenen: {config.expected_serial})...", "info")
                ser_resp = self.uds_client.read_did(0xF18C)
                if not ser_resp.is_positive:
                    raise ProtocolError(f"ECU Seri No (0xF18C) okunamadı: {ser_resp.nrc_description_tr}")
                raw_ser = ser_resp.data[2:] if len(ser_resp.data) >= 2 and ser_resp.data[:2] == b"\xf1\x8c" else ser_resp.data
                actual_serial = raw_ser.decode("ascii", errors="replace").strip()
                if actual_serial != config.expected_serial:
                    raise SafetyError(
                        f"ECU Seri No uyuşmazlığı! Beklenen: {config.expected_serial}, Okunan: {actual_serial} (Flashing fail-closed reddedildi)"
                    )
                self._log(f"✅ ECU Seri Numarası başarıyla doğrulandı: {actual_serial}", "info")

            # Step 2c: T57-D / F-1 anti-rollback. `min_version` used to be
            # logged only — never compared. Read the ECU's current application
            # software version and refuse the flash if it is below the floor.
            # An unreadable/unparsable version fails closed (cannot prove the
            # image is not a downgrade).
            if config.min_version and config.min_version > 0:
                self._log(
                    f"Anti-rollback kontrolü: ECU sürümü okunuyor (DID 0x{config.version_did:04X}, "
                    f"taban={config.min_version})...",
                    "info",
                )
                ver_resp = self.uds_client.read_did(config.version_did)
                if not ver_resp.is_positive:
                    raise SafetyError(
                        f"Anti-rollback: ECU sürümü (0x{config.version_did:04X}) okunamadı "
                        f"({ver_resp.nrc_description_tr}) — downgrade doğrulanamaz (fail-closed).",
                        code="FLASH_ROLLBACK_UNVERIFIABLE",
                    )
                current_version = self._parse_ecu_version(ver_resp)
                if current_version is None:
                    raise SafetyError(
                        f"Anti-rollback: ECU sürümü ayrıştırılamadı "
                        f"(0x{config.version_did:04X}) — downgrade doğrulanamaz (fail-closed).",
                        code="FLASH_ROLLBACK_UNVERIFIABLE",
                    )
                if current_version < config.min_version:
                    raise SafetyError(
                        f"Anti-rollback: ECU sürümü ({current_version}) tabanın "
                        f"({config.min_version}) altında — downgrade reddedildi (fail-closed).",
                        code="FLASH_ROLLBACK_DENIED",
                    )
                self._log(
                    f"✅ Anti-rollback OK: ECU sürümü {current_version} >= {config.min_version}.",
                    "info",
                )

            # 3. Programming Session (P1-5: 0x10 0x02 BEFORE 0x27 — the ECU
            # re-locks security access on session transition; the normative
            # reprogramming order is 0x10 0x02 -> 0x27 -> 0x34, with the
            # bootloader applying its own seed/key ladder).
            self._emit_progress(FlashingStep.PROGRAMMING_SESSION, 3, 0, total_bytes, start_time, crc_hex)
            self._log("Adım 3/10: Bootloader Programlama Oturumu (0x10 0x02) açılıyor...", "info")
            self._check_cancelled()
            # REVIEW 3-CRITICAL: hard-fail contract (same as step 2).
            resp = self.uds_client.change_session(
                DiagnosticSessionType.PROGRAMMING_SESSION,
                user_confirmed=True,
                confirmation_token=self._confirmation_token(),
            )
            if not resp.is_positive:
                raise ProtocolError(f"Programlama oturumuna geçilemedi: {resp.nrc_description_tr}")

            # 4. Security Access inside the programming session (optional/if configured)
            self._emit_progress(FlashingStep.SECURITY_ACCESS, 4, 0, total_bytes, start_time, crc_hex)
            if config.key_derivation is not None or config.security_key is not None:
                sec_level = config.effective_programming_security_level
                self._log(f"Adım 4/10: Güvenlik Erişimi (0x27 Level {sec_level}) doğrulanıyor...", "info")
                self._check_cancelled()
                seed_resp = self.uds_client.security_access_request_seed(level=sec_level, user_confirmed=config.user_confirmed)
                if not seed_resp.is_positive:
                    raise ProtocolError(f"Güvenlik tohumu alınamadı: {seed_resp.nrc_description_tr}")

                # REVIEW (SecurityAccess echo): `67 01 12 34` is SID +
                # securityAccessType ECHO + seed. The old code consumed the
                # whole `data` (echo byte included) as the seed, so the
                # key derivation computed the key from a wrong seed and the
                # ECU rejected it. data[0] must echo our level; the seed is
                # data[1:].
                if not seed_resp.data:
                    raise ProtocolError(
                        "ECU 0x27 yanıtı subfunction echa/seed içermiyor (bozuk yanıt)",
                    )
                if seed_resp.data[0] != (sec_level & 0xFF):
                    raise ProtocolError(
                        f"ECU 0x27 yanıtı subfunction uyuşmazlığı "
                        f"(ECU: {seed_resp.data[0]:02X}, istek: {sec_level:02X})"
                    )
                seed = bytes(seed_resp.data[1:])
                if config.key_derivation is not None:
                    # P-1: derive the key from the FRESH seed (ISO 14229
                    # challenge-response); repeated wrong-key attempts put
                    # the ECU into NRC 0x36/0x37 lockout (up to 10 min).
                    if not seed:
                        raise ProtocolError(
                            "ECU boş seed döndü — anahtar türetmesi yapılamaz (0x27 yanıtı bozuk)",
                        )
                    try:
                        derived_key = config.key_derivation(seed, sec_level)
                    except Exception as derive_exc:  # noqa: BLE001
                        raise ProtocolError(
                            f"Seed'den anahtar türetilemedi (key_derivation hatası): {derive_exc}",
                        ) from derive_exc
                    key_bytes = derived_key
                else:
                    # Fixed-key ECU path (documented limitation): the static
                    # config key is only correct for ECUs that do not bind
                    # the key to the seed.
                    key_bytes = config.security_key or b""

                key_resp = self.uds_client.security_access_send_key(
                    level=sec_level, key=key_bytes, user_confirmed=config.user_confirmed
                )
                if not key_resp.is_positive:
                    raise ProtocolError(f"Güvenlik anahtarı reddedildi: {key_resp.nrc_description_tr}")
                self._log("Güvenlik kilidi başarıyla açıldı.", "info")
            else:
                self._log("Adım 4/10: Güvenlik Erişimi adımı atlandı (Anahtarsız mod).", "info")

            # 4b. Optional Memory Erase Routine (0x31 Start Routine)
            if config.erase_routine_id is not None:
                self._log(
                    f"Bellek silme rutini (0x31 RID: 0x{config.erase_routine_id:04X}) çalıştırılıyor...",
                    "info",
                )
                self._check_cancelled()
                erase_resp = self.uds_client.start_routine(
                    routine_id=config.erase_routine_id,
                    options=config.erase_routine_options,
                    user_confirmed=config.user_confirmed,
                    confirmation_token=self._confirmation_token(),
                )
                if not erase_resp.is_positive:
                    raise ProtocolError(f"Bellek silme rutini reddedildi: {erase_resp.nrc_description_tr}")
                self._log("✅ Bellek silme rutini başarıyla tamamlandı.", "info")

            # 5. Request Download
            self._emit_progress(FlashingStep.REQUEST_DOWNLOAD, 5, 0, total_bytes, start_time, crc_hex)
            self._log(
                f"Adım 5/10: İndirme Talebi (0x34) gönderiliyor (Adres: 0x{config.memory_address:08X}, Boyut: {total_bytes})...",
                "info",
            )
            self._check_cancelled()
            resp = self.uds_client.request_download(
                memory_address=config.memory_address,
                memory_size=total_bytes,
                user_confirmed=config.user_confirmed,
                confirmation_token=self._confirmation_token(),
            )
            if not resp.is_positive:
                raise ProtocolError(f"RequestDownload ECU tarafından reddedildi: {resp.nrc_description_tr}")

            # P1-6: the ECU's maxNumberOfBlockLength bounds every 0x36
            # message (SID + BSC + payload). Effective block size is
            # min(config, max_len - 2); an unparsable/absent value fails
            # closed — flashing with an unbounded block size is how a
            # half-erased ECU happens.
            max_block_len = self._parse_max_block_length(resp)
            effective_block_size = min(config.block_size, max(max_block_len - 2, 1))
            if effective_block_size < config.block_size:
                self._log(
                    f"Blok boyutu ECU sınırına göre {config.block_size} → {effective_block_size} bayta düşürüldü "
                    f"(maxNumberOfBlockLength={max_block_len}).",
                    "info",
                )

            # 6. Transfer Data in Blocks
            self._log(f"Adım 6/10: Blok aktarımı başlatılıyor (Blok Boyutu: {effective_block_size} B)...", "info")
            bytes_sent = 0
            block_seq = 1
            blocks_since_precondition = 0
            # REVIEW hardening (TOCTOU): re-verify gateway preconditions
            # every N blocks — the vehicle may start moving mid-transfer.
            PRECONDITION_RECHECK_EVERY_N_BLOCKS = 16

            while bytes_sent < total_bytes:
                self._check_cancelled()
                # TOCTOU fix: periodic speed/supervisor/watchdog re-check.
                if blocks_since_precondition >= PRECONDITION_RECHECK_EVERY_N_BLOCKS:
                    self._assert_gateway_preconditions()
                    blocks_since_precondition = 0

                chunk = config.data[bytes_sent : bytes_sent + effective_block_size]
                resp = self._call_transfer_data(block_seq, chunk, user_confirmed=config.user_confirmed)
                if not resp.is_positive:
                    raise ProtocolError(f"Blok #{block_seq} aktarımı reddedildi: {resp.nrc_description_tr}")

                # P1-6: verify the ECU echoed our block sequence counter.
                # An empty response or missing BSC is a strict protocol violation.
                if not resp.data or len(resp.data) < 1:
                    raise ProtocolError(f"Blok #{block_seq} yanıtı boş veya BSC içermiyor")
                if resp.data[0] != (block_seq & 0xFF):
                    raise ProtocolError(
                        f"Blok #{block_seq} yanıtı BSC uyuşmazlığı (ECU: {resp.data[0]:02X}, Beklenen: {(block_seq & 0xFF):02X})"
                    )

                bytes_sent += len(chunk)
                block_seq = (block_seq + 1) & 0xFF  # Wraps naturally from 0xFF to 0x00 per ISO 14229-1
                blocks_since_precondition += 1

                self._emit_progress(
                    FlashingStep.TRANSFER_DATA,
                    6,
                    bytes_sent,
                    total_bytes,
                    start_time,
                    crc_hex,
                )

            self._log(f"Tüm {total_bytes} bayt başarıyla aktarıldı.", "info")

            # 7. Request Transfer Exit
            self._emit_progress(FlashingStep.TRANSFER_EXIT, 7, total_bytes, total_bytes, start_time, crc_hex)
            self._log("Adım 7/10: Aktarım Çıkışı (0x37) gönderiliyor...", "info")
            self._check_cancelled()
            # REVIEW 3-CRITICAL: no TypeError fallback — dropping
            # user_confirmed here would bypass the gateway Stage-5 gate.
            resp = self.uds_client.request_transfer_exit(
                is_critical_command=True,
                user_confirmed=config.user_confirmed,
                confirmation_token=self._confirmation_token(),
            )
            if not resp.is_positive:
                raise ProtocolError(f"RequestTransferExit reddedildi: {resp.nrc_description_tr}")

            # 8. Checksum / Routine Verification
            self._emit_progress(FlashingStep.CHECKSUM_VERIFICATION, 8, total_bytes, total_bytes, start_time, crc_hex)
            # REVIEW hardening (fail-closed): verify_checksum=False no longer
            # silently skips integrity verification. The bypass path is kept
            # for API compatibility but now raises SafetyError with an audit
            # log — there is no operator double-confirm bypass for this.
            if not config.verify_checksum:
                self._log(
                    f"GÜVENLİK REDDİ: verify_checksum=False ile flash reddedildi "
                    f"(CRC32={crc_hex} SHA-256={sha256_hex}, boyut={total_bytes}) — "
                    "sağlama toplamı doğrulaması atlanamaz (fail-closed).",
                    "error",
                )
                raise SafetyError(
                    "Sağlama toplamı doğrulaması atlanamaz (verify_checksum=False reddedildi, fail-closed).",
                    code="FLASH_CHECKSUM_BYPASS_DENIED",
                )
            self._check_cancelled()
            self._log(f"Adım 8/10: Sağlama toplamı doğrulanıyor (CRC32: {crc_hex} SHA-256: {sha256_hex})...", "info")
            crc_bytes = crc32_val.to_bytes(4, byteorder="big")
            resp = self.uds_client.start_routine(
                routine_id=config.checksum_routine_id,
                options=crc_bytes,
                user_confirmed=config.user_confirmed,
                confirmation_token=self._confirmation_token(),
            )
            if not resp.is_positive:
                raise ProtocolError(f"Sağlama toplamı doğrulama başlatılamadı: {resp.nrc_description_tr}")

            # P2 & B-17: Request Routine Results (0x31 0x03) to ensure ECU validates CRC
            # RoutineStatus 0x01 means "routineExecutionInProgress"; wait for completion (0x00, 0x02).
            # REVIEW (RoutineControl header): `71 03 <rid hi> <rid lo> ...` —
            # data[0:3] is the controlType+RID echo, NOT status. The old
            # code read data[0] (the 0x03 echo) as the routine status and
            # misdiagnosed every valid reply as a CRC failure.
            routine_prefix = bytes(
                [
                    RoutineControlType.REQUEST_ROUTINE_RESULTS & 0xFF,
                    (config.checksum_routine_id >> 8) & 0xFF,
                    config.checksum_routine_id & 0xFF,
                ]
            )
            deadline = time.monotonic() + 10.0
            while True:
                result_resp = self.uds_client.request_routine_results(routine_id=config.checksum_routine_id)
                if not result_resp.is_positive:
                    raise ProtocolError(f"Sağlama toplamı sonuç sorgusu reddedildi: {result_resp.nrc_description_tr}")

                if not result_resp.data or len(result_resp.data) < 3:
                    raise ProtocolError(
                        "ECU sağlama toplamı sonucu boş döndü (routineStatusRecord yok) — "
                        "doğrulanmamış imaj üzerinde reset atılamaz (fail-closed)"
                    )
                if bytes(result_resp.data[:3]) != routine_prefix:
                    raise ProtocolError(
                        f"ECU 0x31 yanıtı routine echo uyuşmazlığı "
                        f"(ECU: {bytes(result_resp.data[:3]).hex()}, beklenen: {routine_prefix.hex()})"
                    )

                status_record = bytes(result_resp.data[3:])
                if not status_record:
                    raise ProtocolError(
                        "ECU sağlama toplamı sonucu routineStatusRecord içermiyor — "
                        "doğrulanmamış imaj üzerinde reset atılamaz (fail-closed)"
                    )

                status_code = status_record[0]
                if status_code in (0x00, 0x02):
                    # Correctly completed
                    break
                elif status_code == 0x01:
                    # In progress
                    if time.monotonic() > deadline:
                        raise ProtocolError("ECU CRC doğrulama zaman aşımına uğradı (hâlâ çalışıyor)")
                    time.sleep(0.1)
                    continue
                else:
                    raise ProtocolError(f"ECU sağlama toplamı (CRC32) uyumsuzluğu tespit etti (Durum: 0x{status_code:02X})")

            self._log(
                f"✅ Sağlama toplamı (CRC32={crc_hex} SHA-256={sha256_hex}) ECU tarafından başarıyla doğrulandı.",
                "info",
            )

            # 9. ECU Reset
            self._emit_progress(FlashingStep.ECU_RESET, 9, total_bytes, total_bytes, start_time, crc_hex)
            if config.reset_after_flash:
                self._check_cancelled()
                self._log("Adım 9/10: ECU yeniden başlatılıyor (Hard Reset 0x11)...", "info")
                with_reset_resp = self.uds_client.ecu_reset(
                    reset_type=config.reset_type,
                    user_confirmed=config.user_confirmed,
                    confirmation_token=self._confirmation_token(),
                )
                if not with_reset_resp.is_positive:
                    # REVIEW 3-HIGH: the flash image is verified on the ECU but
                    # the ECU refused to boot into it. That is NOT "COMPLETED"
                    # — the old code logged a warning and reported success,
                    # leaving the operator unaware the ECU is still running
                    # the old image. Fail loudly so recovery runs.
                    raise ProtocolError(
                        f"ECU Reset (0x11) reddedildi: {with_reset_resp.nrc_description_tr} "
                        f"(NRC 0x{with_reset_resp.nrc:02X}) — imaj yazıldı ama ECU yeni yazılıma "
                        "başlatılamadı (FAILED olarak raporlandı)"
                    )
                self._log("ECU Reset komutu kabul edildi.", "info")

            # 10. Completed
            self._emit_progress(FlashingStep.COMPLETED, 10, total_bytes, total_bytes, start_time, crc_hex)
            elapsed_final = time.monotonic() - start_time
            self._log(f"🎉 ECU Flashing işlemi {elapsed_final:.2f} saniyede BAŞARIYLA TAMAMLANDI!", "info")
            return True

        except Exception as exc:
            self.current_step = FlashingStep.FAILED
            self._log(f"❌ Flashing Hatası: {exc}", "error")
            if recovery_needed:
                self._best_effort_recovery(config)
            raise

    def _best_effort_recovery(self, config: FlashingConfig) -> None:
        """Attempt to return the ECU to a safe state after a failed flash.

        REVIEW.md 3.2 (bricking): a Hard Reset (0x11 0x01) after a partial
        0x36 transfer can leave the microcontroller booting into an
        application image with a truncated/absent signature or vector
        table — a PERMANENTLY bricked ECU with no bootloader access. The
        recovery ladder therefore NEVER hard-resets as a first move:

        1. Try RequestTransferExit (0x37) so the ECU's own flash manager
           marks the transfer uncommitted and exits the programming
           sequence cleanly.
        2. Fall back to Default Session (0x10 0x01) — the safest non-reset
           retreat: the ECU stays powered and communicable for a re-flash.
        3. Hard Reset is NOT attempted automatically. The operator is
           explicitly warned to keep ignition ON and re-flash.

        Recovery failures are logged and swallowed so they never mask the
        original error.

        K-08: the operator confirmation flows from the flashing config —
        recovery never grants dual-confirmation on its own.
        """
        self._log(
            "Kurtarma: ECU güvenli duruma döndürülüyor (Hard Reset ATILMAZ — yarım yazılım brick riski)...",
            "warning",
        )

        # 1. Cleanest exit: let the ECU finalize/close the transfer itself.
        try:
            resp = self.uds_client.request_transfer_exit()
            if resp.is_positive:
                self._log("Kurtarma: RequestTransferExit (0x37) kabul edildi — transfer ECU tarafından kapatıldı.", "info")
                self._log("⚠️ Yazılım YARIM KALDI! Kontağı KAPATMAYIN, yazılımı yeniden yükleyin.", "warning")
                return
            self._log(f"Kurtarma: 0x37 reddedildi: {resp.nrc_description_tr}", "warning")
        except Exception as exit_exc:  # noqa: BLE001
            self._log(f"Kurtarma: 0x37 denemesi başarısız: {exit_exc}", "warning")

        # 2. Retreat to the default session — no reset, ECU stays reachable.
        try:
            resp = self.uds_client.change_session(DiagnosticSessionType.DEFAULT_SESSION)
            if resp.is_positive:
                self._log("Kurtarma: ECU Default Session'a (0x10 0x01) döndürüldü — oturum güvenli.", "info")
            else:
                self._log(f"Kurtarma: Default Session reddedildi: {resp.nrc_description_tr}", "warning")
        except Exception as session_exc:  # noqa: BLE001
            self._log(f"Kurtarma: Default Session denemesi başarısız: {session_exc}", "warning")

        # 3. NEVER auto hard-reset a partially flashed ECU.
        self._log(
            "⚠️ OTOMATİK HARD RESET YAPILMADI: yarım yazılımla reset brick riski taşır. "
            "Kontağı AÇIK tutun ve yazılımı yeniden yükleyin.",
            "warning",
        )

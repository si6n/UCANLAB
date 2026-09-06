"""High-Reliability ISO 14229 UDS ECU Flashing & Bootloader Reprogramming Engine.

Enforces CORE_SAFETY_FLOOR, dual-confirmation, and full UDS download sequence
(0x10 Extended/Programming -> 0x27 SecurityAccess -> 0x34 RequestDownload ->
 0x36 TransferData in blocks -> 0x37 RequestTransferExit -> 0x31 RoutineControl Checksum -> 0x11 ECUReset).
"""

from __future__ import annotations

import inspect
import math
import threading
import time
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, ClassVar

from src.core.errors import ProtocolError, SafetyError
from src.core.logging import get_logger
from src.protocols.uds.services import DiagnosticSessionType

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

    memory_address: int
    data: bytes
    block_size: int = 256  # 64, 128, 256, 512, 1024, 4096 bytes
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
    reset_after_flash: bool = True
    reset_type: int = 0x01  # Hard Reset
    user_confirmed: bool = False

    def __post_init__(self) -> None:
        if self.block_size <= 0:
            raise ValueError(f"Invalid block_size {self.block_size}. Must be greater than 0.")
        if not self.data:
            raise ValueError("Flashing payload data cannot be empty.")

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
    ) -> None:
        self.uds_client = uds_client
        self.gateway = gateway
        self.on_progress = on_progress
        self.on_log = on_log

        self.current_step: FlashingStep = FlashingStep.IDLE
        self._is_cancelled = False

    def _call_transfer_data(self, block_sequence: int, data: bytes) -> Any:
        """Invoke transfer_data with safety flags if supported by the client signature."""
        target = getattr(self.uds_client.transfer_data, "side_effect", None) or self.uds_client.transfer_data
        try:
            sig = inspect.signature(target)
            if "is_critical_command" in sig.parameters or any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
            ):
                return self.uds_client.transfer_data(
                    block_sequence=block_sequence, data=data, is_critical_command=True, user_confirmed=True
                )
        except (ValueError, TypeError):
            pass
        return self.uds_client.transfer_data(block_sequence=block_sequence, data=data)

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
            speed = getattr(self.gateway, "_current_vehicle_speed_kmh", 0.0)
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
        start_time = time.monotonic()
        total_bytes = len(config.data)
        if total_bytes == 0:
            raise ValueError("Flashing payload data is empty")

        crc32_val = zlib.crc32(config.data) & 0xFFFFFFFF
        crc_hex = f"0x{crc32_val:08X}"

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
            return self._execute_flash_inner(config, start_time, crc32_val, crc_hex)
        finally:
            keepalive_stop.set()
            keepalive_thread.join(timeout=self.TESTER_PRESENT_INTERVAL_S * 2)

    def _execute_flash_inner(
        self,
        config: FlashingConfig,
        start_time: float,
        crc32_val: int,
        crc_hex: str,
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
            try:
                resp = self.uds_client.change_session(
                    DiagnosticSessionType.EXTENDED_DIAGNOSTIC_SESSION, user_confirmed=True
                )
            except TypeError:
                resp = self.uds_client.change_session(DiagnosticSessionType.EXTENDED_DIAGNOSTIC_SESSION)
            if not resp.is_positive:
                raise ProtocolError(f"Genişletilmiş oturum açılamadı: {resp.nrc_description_tr} (NRC 0x{resp.nrc:02X})")
            # From here on the ECU is out of its default session; a failure
            # must attempt best-effort recovery before surfacing the error.
            recovery_needed = True

            # 3. Programming Session (P1-5: 0x10 0x02 BEFORE 0x27 — the ECU
            # re-locks security access on session transition; the normative
            # reprogramming order is 0x10 0x02 -> 0x27 -> 0x34, with the
            # bootloader applying its own seed/key ladder).
            self._emit_progress(FlashingStep.PROGRAMMING_SESSION, 3, 0, total_bytes, start_time, crc_hex)
            self._log("Adım 3/10: Bootloader Programlama Oturumu (0x10 0x02) açılıyor...", "info")
            self._check_cancelled()
            try:
                resp = self.uds_client.change_session(
                    DiagnosticSessionType.PROGRAMMING_SESSION, user_confirmed=True
                )
            except TypeError:
                resp = self.uds_client.change_session(DiagnosticSessionType.PROGRAMMING_SESSION)
            if not resp.is_positive:
                raise ProtocolError(f"Programlama oturumuna geçilemedi: {resp.nrc_description_tr}")

            # 4. Security Access inside the programming session (optional/if configured)
            self._emit_progress(FlashingStep.SECURITY_ACCESS, 4, 0, total_bytes, start_time, crc_hex)
            if config.key_derivation is not None or config.security_key is not None:
                sec_level = config.effective_programming_security_level
                self._log(f"Adım 4/10: Güvenlik Erişimi (0x27 Level {sec_level}) doğrulanıyor...", "info")
                self._check_cancelled()
                seed_resp = self.uds_client.security_access_request_seed(level=sec_level)
                if not seed_resp.is_positive:
                    raise ProtocolError(f"Güvenlik tohumu alınamadı: {seed_resp.nrc_description_tr}")

                seed = bytes(getattr(seed_resp, "data", b"") or b"")
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

                key_resp = self.uds_client.security_access_send_key(level=sec_level, key=key_bytes)
                if not key_resp.is_positive:
                    raise ProtocolError(f"Güvenlik anahtarı reddedildi: {key_resp.nrc_description_tr}")
                self._log("Güvenlik kilidi başarıyla açıldı.", "info")
            else:
                self._log("Adım 4/10: Güvenlik Erişimi adımı atlandı (Anahtarsız mod).", "info")

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

            while bytes_sent < total_bytes:
                self._check_cancelled()

                chunk = config.data[bytes_sent : bytes_sent + effective_block_size]
                resp = self._call_transfer_data(block_seq, chunk)
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
            try:
                resp = self.uds_client.request_transfer_exit(is_critical_command=True, user_confirmed=True)
            except TypeError:
                resp = self.uds_client.request_transfer_exit()
            if not resp.is_positive:
                raise ProtocolError(f"RequestTransferExit reddedildi: {resp.nrc_description_tr}")

            # 8. Checksum / Routine Verification
            self._emit_progress(FlashingStep.CHECKSUM_VERIFICATION, 8, total_bytes, total_bytes, start_time, crc_hex)
            if config.verify_checksum:
                self._check_cancelled()
                self._log(f"Adım 8/10: Sağlama toplamı doğrulanıyor (CRC32: {crc_hex})...", "info")
                crc_bytes = crc32_val.to_bytes(4, byteorder="big")
                resp = self.uds_client.start_routine(
                    routine_id=config.checksum_routine_id,
                    options=crc_bytes,
                    user_confirmed=config.user_confirmed,
                )
                if not resp.is_positive:
                    raise ProtocolError(f"Sağlama toplamı doğrulama başlatılamadı: {resp.nrc_description_tr}")

                # P2 & B-17: Request Routine Results (0x31 0x03) to ensure ECU validates CRC
                # RoutineStatus 0x01 means "routineExecutionInProgress"; wait for completion (0x00, 0x02).
                deadline = time.monotonic() + 10.0
                while True:
                    result_resp = self.uds_client.request_routine_results(routine_id=config.checksum_routine_id)
                    if not result_resp.is_positive:
                        raise ProtocolError(f"Sağlama toplamı sonuç sorgusu reddedildi: {result_resp.nrc_description_tr}")

                    if not result_resp.data or len(result_resp.data) < 1:
                        raise ProtocolError(
                            "ECU sağlama toplamı sonucu boş döndü (routineStatusRecord yok) — "
                            "doğrulanmamış imaj üzerinde reset atılamaz (fail-closed)"
                        )

                    status_code = result_resp.data[0]
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

                self._log("✅ Sağlama toplamı (CRC32) ECU tarafından başarıyla doğrulandı.", "info")
            else:
                self._log("Adım 8/10: Sağlama toplamı doğrulama adımı atlandı.", "info")

            # 9. ECU Reset
            self._emit_progress(FlashingStep.ECU_RESET, 9, total_bytes, total_bytes, start_time, crc_hex)
            if config.reset_after_flash:
                self._check_cancelled()
                self._log("Adım 9/10: ECU yeniden başlatılıyor (Hard Reset 0x11)...", "info")
                with_reset_resp = self.uds_client.ecu_reset(
                    reset_type=config.reset_type, user_confirmed=config.user_confirmed
                )
                if not with_reset_resp.is_positive:
                    self._log(f"ECU Reset uyarısı: {with_reset_resp.nrc_description_tr}", "warning")
                else:
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
            resp = self.uds_client.change_session(0x01)  # defaultSession
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

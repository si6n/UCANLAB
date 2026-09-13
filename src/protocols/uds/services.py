"""ISO 14229-1 Unified Diagnostic Services (UDS) Message Builders and Parsers."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from src.protocols.uds.nrc import NRC_DESCRIPTIONS, UdsNrc


class UdsServiceId(IntEnum):
    """ISO 14229-1 Service Identifiers (SID)."""

    DIAGNOSTIC_SESSION_CONTROL = 0x10
    ECU_RESET = 0x11
    SECURITY_ACCESS = 0x27
    COMMUNICATION_CONTROL = 0x28
    TESTER_PRESENT = 0x3E
    READ_DATA_BY_IDENTIFIER = 0x22
    WRITE_DATA_BY_IDENTIFIER = 0x2E
    CLEAR_DIAGNOSTIC_INFORMATION = 0x14
    READ_DTC_INFORMATION = 0x19
    READ_MEMORY_BY_ADDRESS = 0x23
    ROUTINE_CONTROL = 0x31
    REQUEST_DOWNLOAD = 0x34
    REQUEST_UPLOAD = 0x35
    TRANSFER_DATA = 0x36
    REQUEST_TRANSFER_EXIT = 0x37
    NEGATIVE_RESPONSE = 0x7F


class DiagnosticSessionType(IntEnum):
    """0x10 Diagnostic Session Types."""

    DEFAULT_SESSION = 0x01
    PROGRAMMING_SESSION = 0x02
    EXTENDED_DIAGNOSTIC_SESSION = 0x03
    SAFETY_SYSTEM_DIAGNOSTIC_SESSION = 0x04


class RoutineControlType(IntEnum):
    """0x31 Routine Control Sub-functions."""

    START_ROUTINE = 0x01
    STOP_ROUTINE = 0x02
    REQUEST_ROUTINE_RESULTS = 0x03


class ReadDtcInformationType(IntEnum):
    """0x19 ReadDTCInformation Sub-functions (ISO 14229-1 §11)."""

    REPORT_NUMBER_OF_DTC_BY_STATUS_MASK = 0x01
    REPORT_DTC_BY_STATUS_MASK = 0x02
    REPORT_DTC_SNAPSHOT_IDENTIFICATION = 0x03
    REPORT_DTC_SNAPSHOT_RECORD = 0x04
    REPORT_SUPPORTED_DTC = 0x0A
    REPORT_FIRST_TEST_FAILED_DTC = 0x0B


@dataclass(slots=True)
class UdsResponse:
    """Parsed ISO 14229 UDS response message."""

    service_id: int
    is_positive: bool
    data: bytes
    nrc: UdsNrc = UdsNrc.POSITIVE_RESPONSE
    # Raw NRC byte as received on the wire. Diverges from `nrc` only for
    # vendor-specific codes absent from UdsNrc, which would otherwise be
    # silently remapped to GENERAL_REJECT.
    raw_nrc: int | None = None

    @property
    def nrc_description_en(self) -> str:
        return NRC_DESCRIPTIONS.get(self.nrc, (f"NRC 0x{self.nrc:02X}", f"NRC 0x{self.nrc:02X}"))[0]

    @property
    def nrc_description_tr(self) -> str:
        return NRC_DESCRIPTIONS.get(self.nrc, (f"NRC 0x{self.nrc:02X}", f"NRC 0x{self.nrc:02X}"))[1]


class UdsServiceBuilder:
    """Builds raw payload bytes for UDS diagnostic requests."""

    @classmethod
    def build_diagnostic_session_control(cls, session_type: DiagnosticSessionType) -> bytes:
        # REVIEW 2-M6: reject int/enum out of range instead of truncating —
        # a raw int like 0x103 would emit a corrupted sub-function byte.
        try:
            session_type = DiagnosticSessionType(session_type)
        except ValueError:
            valid = ", ".join(f"0x{s.value:02X}" for s in DiagnosticSessionType)
            raise ValueError(f"Invalid diagnostic session type 0x{session_type:02X} (valid: {valid})") from None
        return bytes([UdsServiceId.DIAGNOSTIC_SESSION_CONTROL, session_type])

    @classmethod
    def build_tester_present(cls, suppress_positive_response: bool = False) -> bytes:
        sub_fn = 0x80 if suppress_positive_response else 0x00
        return bytes([UdsServiceId.TESTER_PRESENT, sub_fn])

    @classmethod
    def build_read_data_by_identifier(cls, did: int) -> bytes:
        if not 0 <= did <= 0xFFFF:
            raise ValueError(f"DID 0x{did:X} out of 16-bit range (0..0xFFFF)")
        return bytes([UdsServiceId.READ_DATA_BY_IDENTIFIER, (did >> 8) & 0xFF, did & 0xFF])

    @classmethod
    def build_clear_diagnostic_information(cls, dtc_group: int = 0xFFFFFF) -> bytes:
        return bytes(
            [
                UdsServiceId.CLEAR_DIAGNOSTIC_INFORMATION,
                (dtc_group >> 16) & 0xFF,
                (dtc_group >> 8) & 0xFF,
                dtc_group & 0xFF,
            ]
        )

    @classmethod
    def build_write_data_by_identifier(cls, did: int, data: bytes) -> bytes:
        if not 0 <= did <= 0xFFFF:
            raise ValueError(f"DID 0x{did:X} out of 16-bit range (0..0xFFFF)")
        return bytes([UdsServiceId.WRITE_DATA_BY_IDENTIFIER, (did >> 8) & 0xFF, did & 0xFF]) + data

    @classmethod
    def build_security_access_request_seed(cls, level: int = 1) -> bytes:
        # ISO 14229-1 §11.5: 0x27 sub-functions are 0x01..0x7E. Out-of-range
        # or non-positive levels are rejected instead of truncating to a
        # corrupt sub-function byte. (Parity is NOT enforced: OEM profiles
        # historically pass the seed level; the tool never invents it.)
        if not 1 <= level <= 0x7E:
            raise ValueError(f"Invalid security access seed level 0x{level:02X} (0x01..0x7E)")
        return bytes([UdsServiceId.SECURITY_ACCESS, level])

    @classmethod
    def build_security_access_send_key(cls, level: int, key: bytes) -> bytes:
        # Convention: callers pass the SEED level; the emitted byte is
        # level+1 (the key sub-function, max 0x7E). Reject out-of-range so
        # the wire byte can never be corrupt.
        if not 1 <= level <= 0x7D:
            raise ValueError(f"Invalid security access level 0x{level:02X} (seed level 0x01..0x7D)")
        return bytes([UdsServiceId.SECURITY_ACCESS, level + 1]) + key

    @classmethod
    def build_routine_control(
        cls,
        control_type: RoutineControlType,
        routine_id: int,
        option_bytes: bytes = b"",
    ) -> bytes:
        if not isinstance(control_type, RoutineControlType):
            try:
                control_type = RoutineControlType(control_type)
            except ValueError:
                valid = ", ".join(f"0x{t.value:02X}" for t in RoutineControlType)
                raise ValueError(f"Invalid routine control type 0x{int(control_type):02X} (valid: {valid})") from None
        if not 0 <= routine_id <= 0xFFFF:
            raise ValueError(f"Routine ID 0x{routine_id:X} out of 16-bit range (0..0xFFFF)")
        header = bytes(
            [
                UdsServiceId.ROUTINE_CONTROL,
                control_type,
                (routine_id >> 8) & 0xFF,
                routine_id & 0xFF,
            ]
        )
        return header + option_bytes

    @classmethod
    def build_read_memory_by_address(
        cls,
        memory_address: int,
        memory_size: int,
        address_and_length_format_identifier: int = 0x44,
    ) -> bytes:
        """Build 0x23 ReadMemoryByAddress request."""
        size_width = (address_and_length_format_identifier >> 4) & 0x0F
        addr_width = address_and_length_format_identifier & 0x0F
        if not (1 <= addr_width <= 8 and 1 <= size_width <= 8):
            raise ValueError(
                f"Invalid ALFI 0x{address_and_length_format_identifier:02X}: "
                f"address width {addr_width}, size width {size_width} (each must be 1..8)"
            )
        try:
            addr_bytes = memory_address.to_bytes(addr_width, byteorder="big")
            size_bytes = memory_size.to_bytes(size_width, byteorder="big")
        except OverflowError as exc:
            raise ValueError(
                f"Value does not fit ALFI 0x{address_and_length_format_identifier:02X} widths "
                f"(address {addr_width}B, size {size_width}B)"
            ) from exc
        return (
            bytes(
                [
                    UdsServiceId.READ_MEMORY_BY_ADDRESS,
                    address_and_length_format_identifier,
                ]
            )
            + addr_bytes
            + size_bytes
        )

    @classmethod
    def build_read_dtc_information(
        cls,
        sub_function: ReadDtcInformationType | int,
        status_mask: int = 0xFF,
        dtc_mask: int | None = None,
        snapshot_record_num: int = 0xFF,
    ) -> bytes:
        """Build 0x19 ReadDTCInformation request."""
        sub_fn_val = int(sub_function) & 0xFF
        header = bytes([UdsServiceId.READ_DTC_INFORMATION, sub_fn_val])
        if sub_fn_val in (
            ReadDtcInformationType.REPORT_NUMBER_OF_DTC_BY_STATUS_MASK,
            ReadDtcInformationType.REPORT_DTC_BY_STATUS_MASK,
        ):
            return header + bytes([status_mask & 0xFF])
        elif sub_fn_val == ReadDtcInformationType.REPORT_DTC_SNAPSHOT_RECORD:
            mask = dtc_mask or 0x000000
            return header + bytes([(mask >> 16) & 0xFF, (mask >> 8) & 0xFF, mask & 0xFF, snapshot_record_num & 0xFF])
        elif sub_fn_val == ReadDtcInformationType.REPORT_DTC_SNAPSHOT_IDENTIFICATION:
            return header
        elif sub_fn_val in (
            ReadDtcInformationType.REPORT_SUPPORTED_DTC,
            ReadDtcInformationType.REPORT_FIRST_TEST_FAILED_DTC,
        ):
            return header
        return header + bytes([status_mask & 0xFF])

    @classmethod
    def build_request_upload(
        cls,
        memory_address: int,
        memory_size: int,
        data_format_identifier: int = 0x00,
        address_and_length_format_identifier: int = 0x44,
    ) -> bytes:
        """Build 0x35 RequestUpload request."""
        size_width = (address_and_length_format_identifier >> 4) & 0x0F
        addr_width = address_and_length_format_identifier & 0x0F
        if not (1 <= addr_width <= 8 and 1 <= size_width <= 8):
            raise ValueError(
                f"Invalid ALFI 0x{address_and_length_format_identifier:02X}: "
                f"address width {addr_width}, size width {size_width} (each must be 1..8)"
            )
        try:
            addr_bytes = memory_address.to_bytes(addr_width, byteorder="big")
            size_bytes = memory_size.to_bytes(size_width, byteorder="big")
        except OverflowError as exc:
            raise ValueError(
                f"Value does not fit ALFI 0x{address_and_length_format_identifier:02X} widths "
                f"(address {addr_width}B, size {size_width}B)"
            ) from exc
        return (
            bytes(
                [
                    UdsServiceId.REQUEST_UPLOAD,
                    data_format_identifier,
                    address_and_length_format_identifier,
                ]
            )
            + addr_bytes
            + size_bytes
        )

    @classmethod
    def build_request_download(
        cls,
        memory_address: int,
        memory_size: int,
        data_format_identifier: int = 0x00,
        address_and_length_format_identifier: int = 0x44,
    ) -> bytes:
        # ISO 14229-1: ALFI high nibble = memory size (length) width in bytes,
        # low nibble = memory address width in bytes. Widths other than 1..8
        # (or values that overflow their width) are rejected up front instead
        # of emitting an inconsistent request. REVIEW 2-M5: the previous 1..4
        # (32-bit) cap rejected 64-bit addressing used by large-flash ADAS /
        # EV BMS / infotainment ECUs; ISO 14229-1 permits nibble widths 1..8.
        size_width = (address_and_length_format_identifier >> 4) & 0x0F
        addr_width = address_and_length_format_identifier & 0x0F
        if not (1 <= addr_width <= 8 and 1 <= size_width <= 8):
            raise ValueError(
                f"Invalid ALFI 0x{address_and_length_format_identifier:02X}: "
                f"address width {addr_width}, size width {size_width} (each must be 1..8)"
            )
        try:
            addr_bytes = memory_address.to_bytes(addr_width, byteorder="big")
            size_bytes = memory_size.to_bytes(size_width, byteorder="big")
        except OverflowError as exc:
            raise ValueError(
                f"Value does not fit ALFI 0x{address_and_length_format_identifier:02X} widths "
                f"(address {addr_width}B, size {size_width}B)"
            ) from exc
        return (
            bytes(
                [
                    UdsServiceId.REQUEST_DOWNLOAD,
                    data_format_identifier,
                    address_and_length_format_identifier,
                ]
            )
            + addr_bytes
            + size_bytes
        )

    @classmethod
    def build_transfer_data(cls, block_sequence: int, data: bytes) -> bytes:
        if not 0 <= block_sequence <= 0xFF:
            raise ValueError(f"Block sequence counter {block_sequence} out of range (0..255)")
        return bytes([UdsServiceId.TRANSFER_DATA, block_sequence & 0xFF]) + data

    @classmethod
    def build_request_transfer_exit(cls) -> bytes:
        return bytes([UdsServiceId.REQUEST_TRANSFER_EXIT])

    @classmethod
    def build_ecu_reset(cls, reset_type: int = 0x01) -> bytes:
        # REVIEW 2-M6: 0x11 sub-function must be a valid reset type (0x01
        # hard, 0x02 key off/on, 0x03 soft) — reject other/negative values
        # instead of truncating to a corrupt sub-function byte.
        if reset_type not in (0x01, 0x02, 0x03):
            raise ValueError(f"Invalid ECU reset type 0x{reset_type:02X} (valid: 0x01 hard, 0x02 keyOffOn, 0x03 soft)")
        return bytes([UdsServiceId.ECU_RESET, reset_type & 0xFF])

    @classmethod
    def parse_response(cls, payload: bytes) -> UdsResponse:
        """Parse raw diagnostic response payload."""
        if not payload:
            raise ValueError("Empty UDS payload")

        sid = payload[0]

        # Negative Response (0x7F, Rejected SID, NRC)
        if sid == UdsServiceId.NEGATIVE_RESPONSE:
            if len(payload) < 3:
                raise ValueError("Negative response requires at least 3 bytes")
            rejected_sid = payload[1]
            nrc_val = payload[2]
            known_nrc = nrc_val in UdsNrc._value2member_map_
            return UdsResponse(
                service_id=rejected_sid,
                is_positive=False,
                data=payload[3:],
                nrc=UdsNrc(nrc_val) if known_nrc else UdsNrc.GENERAL_REJECT,
                raw_nrc=nrc_val,
            )

        # Positive Response (SID + 0x40)
        # Y-08: ISO 14229 requires the response echo to carry SID + 0x40 —
        # a bare SID below 0x40 is a malformed/hostile payload and must
        # fail closed instead of being silently labelled "positive".
        if sid < 0x40:
            raise ValueError(
                f"Invalid UDS positive response SID 0x{sid:02X} "
                "(expected SID + 0x40 >= 0x40 for a positive response)"
            )
        orig_sid = sid - 0x40
        return UdsResponse(
            service_id=orig_sid,
            is_positive=True,
            data=payload[1:],
            nrc=UdsNrc.POSITIVE_RESPONSE,
        )

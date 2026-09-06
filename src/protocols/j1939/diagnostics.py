"""SAE J1939-73 Diagnostic Services (DM1..DM6, DM11, DM12) and FMI 0-31 Parser.

Complies with SAE J1939-73 and MASTER_PLAN.md Section 4.4.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from src.core.logging import get_logger
from src.core.models.can_frame import CanFrame

logger = get_logger("protocols.j1939.diagnostics")

PGN_DM1: int = 65226  # 0xFECA (Active DTCs)
PGN_DM2: int = 65227  # 0xFECB (Previously Active DTCs)
PGN_DM3: int = 65228  # 0xFECC (Clear Previously Active DTCs)
PGN_DM4: int = 65229  # 0xFECD (Freeze Frame Parameters)
PGN_DM5: int = 65230  # 0xFECE (Diagnostic Readiness 1)
PGN_DM6: int = 65231  # 0xFECF (Pending Emission-Related DTCs)
PGN_DM11: int = 65235  # 0xFED3 (Clear Active DTCs)
PGN_DM12: int = 65236  # 0xFED4 (Emissions-Related Active DTCs)


class LampStatus(IntEnum):
    """J1939 MIL and Diagnostic Lamp States (2-bit discrete)."""

    OFF = 0b00
    ON = 0b01
    ERROR = 0b10
    NOT_AVAILABLE = 0b11


FMI_DESCRIPTIONS: dict[int, tuple[str, str]] = {
    0: ("Data Valid but Above Normal Range - Most Severe", "Veri Geçerli ancak Normalin Çok Üstünde (Kritik)"),
    1: ("Data Valid but Below Normal Range - Most Severe", "Veri Geçerli ancak Normalin Çok Altında (Kritik)"),
    2: ("Data Erratic, Intermittent or Incorrect", "Düzensiz, Kesintili veya Hatalı Veri"),
    3: ("Voltage Above Normal, or Shorted to High Source", "Voltaj Yüksek veya Artıya Kısa Devre"),
    4: ("Voltage Below Normal, or Shorted to Low Source", "Voltaj Düşük veya Şaseye Kısa Devre"),
    5: ("Current Below Normal or Open Circuit", "Akım Düşük veya Açık Devre"),
    6: ("Current Above Normal or Grounded Circuit", "Akım Yüksek veya Şaseye Kaçak"),
    7: ("Mechanical System Not Responding or Out of Adjustment", "Mekanik Sistem Yanıt Vermiyor veya Ayar Dışı"),
    8: ("Abnormal Frequency or Pulse Width or Period", "Anormal Frekans, Pals Genişliği veya Periyot"),
    9: ("Abnormal Update Rate (Timeout)", "Anormal Güncelleme Hızı (İletişim Zaman Aşımı)"),
    10: ("Abnormal Rate of Change", "Anormal Değişim Hızı"),
    11: ("Root Cause Not Known", "Kök Neden Bilinmiyor"),
    12: ("Bad Intelligent Device or Component", "Arızalı Akıllı Cihaz veya Dahili Bileşen"),
    13: ("Out of Calibration", "Kalibrasyon Dışı"),
    14: ("Special Instructions", "Özel Talimatlar"),
    15: ("Data Valid but Above Normal Range - Least Severe", "Veri Normalin Üstünde (Düşük Seviye)"),
    16: ("Data Valid but Above Normal Range - Moderately Severe", "Veri Normalin Üstünde (Orta Seviye)"),
    17: ("Data Valid but Below Normal Range - Least Severe", "Veri Normalin Altında (Düşük Seviye)"),
    18: ("Data Valid but Below Normal Range - Moderately Severe", "Veri Normalin Altında (Orta Seviye)"),
    19: ("Received Network Data In Error", "Ağ Üzerinden Hatalı Veri Alındı"),
    20: ("Data Drifted Out of Range (Most Severe)", "Veri Kapsama Alanı Dışına Kaydı (Kritik)"),
    21: ("Data Drifted Out of Range (Least Severe)", "Veri Kapsama Alanı Dışına Kaydı (Düşük Seviye)"),
    22: ("Reserved for SAE Future Use", "SAE Gelecek Kullanım İçin Rezerve"),
    23: ("Reserved for SAE Future Use", "SAE Gelecek Kullanım İçin Rezerve"),
    24: ("Reserved for SAE Future Use", "SAE Gelecek Kullanım İçin Rezerve"),
    25: ("Reserved for SAE Future Use", "SAE Gelecek Kullanım İçin Rezerve"),
    26: ("Reserved for SAE Future Use", "SAE Gelecek Kullanım İçin Rezerve"),
    27: ("Reserved for SAE Future Use", "SAE Gelecek Kullanım İçin Rezerve"),
    28: ("Reserved for SAE Future Use", "SAE Gelecek Kullanım İçin Rezerve"),
    29: ("Reserved for SAE Future Use", "SAE Gelecek Kullanım İçin Rezerve"),
    30: ("Reserved for SAE Future Use", "SAE Gelecek Kullanım İçin Rezerve"),
    31: ("Condition Exists", "Durum/Koşul Mevcut"),
}


@dataclass(slots=True, frozen=True)
class DiagnosticTroubleCode:
    """Standard SAE J1939 4-byte DTC representation."""

    spn: int
    fmi: int
    occurrence_count: int
    conversion_method: int = 0
    source_address: int = 0

    @property
    def fmi_description_en(self) -> str:
        return FMI_DESCRIPTIONS.get(self.fmi, (f"FMI {self.fmi}", f"FMI {self.fmi}"))[0]

    @property
    def fmi_description_tr(self) -> str:
        return FMI_DESCRIPTIONS.get(self.fmi, (f"FMI {self.fmi}", f"FMI {self.fmi}"))[1]

    @property
    def is_critical(self) -> bool:
        return self.fmi in (0, 1, 6, 12)

    @classmethod
    def from_bytes(cls, dtc_bytes: bytes, source_address: int = 0) -> DiagnosticTroubleCode:
        """Decode 4-byte binary DTC according to SAE J1939-73 formula."""
        if len(dtc_bytes) < 4:
            raise ValueError(f"DTC requires 4 bytes, got {len(dtc_bytes)}")

        b0, b1, b2, b3 = dtc_bytes[:4]

        # SPN = Byte0 | (Byte1 << 8) | ((Byte2 & 0xE0) << 11)
        spn = b0 | (b1 << 8) | ((b2 & 0xE0) << 11)
        fmi = b2 & 0x1F
        occurrence_count = b3 & 0x7F
        conversion_method = (b3 >> 7) & 0x01

        return cls(
            spn=spn,
            fmi=fmi,
            occurrence_count=occurrence_count,
            conversion_method=conversion_method,
            source_address=source_address,
        )


@dataclass(slots=True)
class DMMessage:
    """Decoded DM1 or DM2 message with Lamp states and DTC list."""

    pgn: int
    source_address: int
    malfunction_indicator_lamp: LampStatus
    red_stop_lamp: LampStatus
    amber_warning_lamp: LampStatus
    protect_lamp: LampStatus
    dtcs: list[DiagnosticTroubleCode]
    timestamp_ns: int


@dataclass(slots=True, frozen=True)
class DMReadiness:
    """Decoded DM5 (PGN 65230) Diagnostic Readiness 1 payload."""

    active_rank: int
    previously_active_rank: int
    supported_systems: int
    completed_systems: int
    source_address: int
    timestamp_ns: int


class J1939DiagnosticService:
    """SAE J1939-73 Diagnostic Service Parser and Command Generator."""

    @classmethod
    def parse_dm1_or_dm2(cls, data: bytes, pgn: int, source_address: int = 0, timestamp_ns: int = 0) -> DMMessage:
        """Parse raw payload from DM1 (PGN 65226) or DM2 (PGN 65227)."""
        if len(data) < 2:
            return DMMessage(
                pgn=pgn,
                source_address=source_address,
                malfunction_indicator_lamp=LampStatus.NOT_AVAILABLE,
                red_stop_lamp=LampStatus.OFF,
                amber_warning_lamp=LampStatus.OFF,
                protect_lamp=LampStatus.OFF,
                dtcs=[],
                timestamp_ns=timestamp_ns,
            )

        # Byte 0: Lamp States (2 bits each)
        b0 = data[0]
        mil = LampStatus((b0 >> 6) & 0x03)
        red_stop = LampStatus((b0 >> 4) & 0x03)
        amber_warning = LampStatus((b0 >> 2) & 0x03)
        protect = LampStatus(b0 & 0x03)

        dtcs: list[DiagnosticTroubleCode] = []
        # DTC records start at Byte 2 (4 bytes each)
        dtc_payload = data[2:]
        num_dtcs = len(dtc_payload) // 4

        for i in range(num_dtcs):
            chunk = dtc_payload[i * 4 : (i + 1) * 4]
            # Check for empty DTC (SPN=0, FMI=0)
            if chunk == b"\x00\x00\x00\x00" or chunk == b"\xff\xff\xff\xff":
                continue
            dtc = DiagnosticTroubleCode.from_bytes(chunk, source_address=source_address)
            if dtc.spn != 0:
                dtcs.append(dtc)

        return DMMessage(
            pgn=pgn,
            source_address=source_address,
            malfunction_indicator_lamp=mil,
            red_stop_lamp=red_stop,
            amber_warning_lamp=amber_warning,
            protect_lamp=protect,
            dtcs=dtcs,
            timestamp_ns=timestamp_ns,
        )

    @classmethod
    def dm11_request_payload(cls) -> bytes:
        """Construct PGN 59904 (Request PGN) payload targeting DM11 (PGN 65235 / 0xFED3)."""
        return b"\xd3\xfe\x00"

    @classmethod
    def dm3_request_payload(cls) -> bytes:
        """Construct PGN 59904 (Request PGN) payload targeting DM3 (PGN 65228 / 0xFECC)."""
        return b"\xcc\xfe\x00"

    @classmethod
    def create_dm11_clear_active_request(cls, target_address: int = 0, source_address: int = 0xF9) -> bytes:
        """Construct PGN 59904 (Request PGN) targeting DM11 (PGN 65235 / 0xFED3)."""
        return cls.dm11_request_payload()

    @classmethod
    def create_dm11_frame(cls, target_address: int = 0, source_address: int = 0xF9) -> CanFrame:
        """Construct CanFrame targeting DM11 clear active DTCs."""
        can_id = 0x18EA0000 | ((target_address & 0xFF) << 8) | (source_address & 0xFF)
        return CanFrame.create(
            channel_id="j1939",
            arbitration_id=can_id,
            data=cls.dm11_request_payload(),
            is_extended=True,
            direction="tx",
        )

    @classmethod
    def create_dm3_clear_previously_active_request(cls, target_address: int = 0, source_address: int = 0xF9) -> bytes:
        """Construct PGN 59904 (Request PGN) targeting DM3 (PGN 65228 / 0xFECC)."""
        return cls.dm3_request_payload()

    @classmethod
    def create_dm3_frame(cls, target_address: int = 0, source_address: int = 0xF9) -> CanFrame:
        """Construct CanFrame targeting DM3 clear previously active DTCs."""
        can_id = 0x18EA0000 | ((target_address & 0xFF) << 8) | (source_address & 0xFF)
        return CanFrame.create(
            channel_id="j1939",
            arbitration_id=can_id,
            data=cls.dm3_request_payload(),
            is_extended=True,
            direction="tx",
        )

    @classmethod
    def create_request_frame(cls, pgn: int, target_address: int = 0, source_address: int = 0xF9) -> CanFrame:
        """Construct a PGN 59904 (Request PGN) frame for any diagnostic PGN.

        Shared builder for DM3/DM4/DM5/DM6/DM11 read and clear requests so
        every diagnostic request frame follows one canonical ID layout:
        priority 6, PDU format 0xEA, destination = target, source = tool.
        """
        if not (0 <= pgn <= 0x1FFFF):
            raise ValueError(f"PGN out of range: {pgn}")
        can_id = 0x18EA0000 | ((target_address & 0xFF) << 8) | (source_address & 0xFF)
        payload = bytes((pgn & 0xFF, (pgn >> 8) & 0xFF, (pgn >> 16) & 0xFF))
        return CanFrame.create(
            channel_id="j1939",
            arbitration_id=can_id,
            data=payload,
            is_extended=True,
            direction="tx",
        )

    @classmethod
    def create_dm4_request_frame(cls, target_address: int = 0, source_address: int = 0xF9) -> CanFrame:
        """Construct a request frame for DM4 (PGN 65229, Freeze Frame Parameters)."""
        return cls.create_request_frame(PGN_DM4, target_address, source_address)

    @classmethod
    def create_dm5_request_frame(cls, target_address: int = 0, source_address: int = 0xF9) -> CanFrame:
        """Construct a request frame for DM5 (PGN 65230, Diagnostic Readiness 1)."""
        return cls.create_request_frame(PGN_DM5, target_address, source_address)

    @classmethod
    def create_dm6_request_frame(cls, target_address: int = 0, source_address: int = 0xF9) -> CanFrame:
        """Construct a request frame for DM6 (PGN 65231, Pending Emission-Related DTCs)."""
        return cls.create_request_frame(PGN_DM6, target_address, source_address)

    @staticmethod
    def parse_dm5_readiness(data: bytes, source_address: int = 0, timestamp_ns: int = 0) -> DMReadiness:
        """Parse DM5 (PGN 65230) Diagnostic Readiness 1 payload per SAE J1939-73.

        Byte 0: active rank/support bits; Byte 1: previously-active rank;
        Byte 2: supported systems; Byte 3: completed systems
        (bit-per-system, bit set = supported/complete).
        """
        if len(data) < 4:
            raise ValueError(f"DM5 requires >= 4 bytes, got {len(data)}")
        return DMReadiness(
            active_rank=data[0],
            previously_active_rank=data[1],
            supported_systems=data[2],
            completed_systems=data[3],
            source_address=source_address,
            timestamp_ns=timestamp_ns,
        )

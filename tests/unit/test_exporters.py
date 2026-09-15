"""Unit tests for MDF4, MATLAB, KML and PDF/HTML report exporters."""

import tempfile
from pathlib import Path

import pytest

from src.engine.exporters.kml_exporter import GpsPoint, KmlExporter
from src.engine.exporters.mat_exporter import MatExporter
from src.engine.exporters.mdf4_exporter import Mdf4Exporter
from src.engine.exporters.pdf_report import (
    DiagnosticReportGenerator,
    ServiceReportMetadata,
)
from src.protocols.j1939.diagnostics import (
    DiagnosticTroubleCode,
    DMMessage,
    LampStatus,
)


def _require_asammdf() -> None:
    try:
        from asammdf import MDF, Signal  # noqa: F401
        _ = MDF()
    except Exception as exc:
        pytest.skip(f"asammdf not functional in this environment: {exc}")


def test_mdf4_exporter() -> None:
    _require_asammdf()
    with tempfile.TemporaryDirectory() as tmpdir:
        out_file = Path(tmpdir) / "test_session.mf4"
        signals = {
            "EngineSpeed": ([0.0, 0.1, 0.2, 0.3], [800.0, 850.0, 1200.0, 1500.0], "rpm"),
            "CoolantTemp": ([0.0, 0.1, 0.2, 0.3], [75.0, 75.2, 75.5, 76.0], "degC"),
        }
        res_path = Mdf4Exporter.export_signals(out_file, signals)
        assert res_path.exists()
        assert res_path.stat().st_size > 0


def test_mat_exporter() -> None:
    pytest.importorskip("scipy")
    with tempfile.TemporaryDirectory() as tmpdir:
        out_file = Path(tmpdir) / "test_session.mat"
        signals = {
            "EngineSpeed": ([0.0, 0.1], [1000.0, 1100.0], "rpm"),
        }
        res_path = MatExporter.export_signals(out_file, signals)
        assert res_path.exists()
        assert res_path.stat().st_size > 0


def test_kml_exporter() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        out_file = Path(tmpdir) / "track.kml"
        pts = [
            GpsPoint(latitude=41.0082, longitude=28.9784, altitude_m=10.0, speed_knots=15.0),
            GpsPoint(latitude=41.0100, longitude=28.9800, altitude_m=10.0, speed_knots=18.0),
        ]
        res_path = KmlExporter.export_track(out_file, "Sea Trial 1", pts)
        assert res_path.exists()
        content = res_path.read_text(encoding="utf-8")
        assert "Sea Trial 1" in content
        assert "28.9784,41.0082,10.0" in content


def test_report_generator_html_and_hash() -> None:
    with tempfile.TemporaryDirectory() as tmpdir:
        out_file = Path(tmpdir) / "service_report.html"
        meta = ServiceReportMetadata(
            vin_or_hin="TR-MAR-12345-K324",
            technician_name="Ali Usta",
            workshop_name="Marintek Yetkili Servis",
            notes="Deniz testi tamamlandı.",
        )
        dtc1 = DiagnosticTroubleCode(spn=100, fmi=1, occurrence_count=2, source_address=0)
        dm = DMMessage(
            pgn=65226,
            source_address=0,
            malfunction_indicator_lamp=LampStatus.ON,
            red_stop_lamp=LampStatus.OFF,
            amber_warning_lamp=LampStatus.OFF,
            protect_lamp=LampStatus.OFF,
            dtcs=[dtc1],
            timestamp_ns=1000,
        )

        res_path = DiagnosticReportGenerator.generate_html_report(
            output_file=out_file,
            metadata=meta,
            dm_messages=[dm],
            summary_stats={"Total Frames": 10000},
        )

        assert res_path.exists()
        content = res_path.read_text(encoding="utf-8")
        assert "TR-MAR-12345-K324" in content
        assert "SPN 100" in content
        assert "Ali Usta" in content
        assert "Cryptographic Session SHA-256" in content


def test_report_generator_tamper_detection() -> None:
    meta = ServiceReportMetadata(
        vin_or_hin="WVWZZZ3CZWE123456",
        technician_name="Master Tech",
        workshop_name="Bosch Car Service",
    )
    dtc1 = DiagnosticTroubleCode(spn=100, fmi=1, occurrence_count=2, source_address=0)
    dm1 = DMMessage(
        pgn=65226,
        source_address=0,
        malfunction_indicator_lamp=LampStatus.ON,
        red_stop_lamp=LampStatus.OFF,
        amber_warning_lamp=LampStatus.OFF,
        protect_lamp=LampStatus.OFF,
        dtcs=[dtc1],
        timestamp_ns=1000,
    )
    date_str = "2026-09-01 12:00:00 UTC"
    stats = {"Total Frames": 1000}

    hash1 = DiagnosticReportGenerator.calculate_canonical_hash(meta, [dm1], stats, date_str)

    # Tamper with DTC SPN
    dtc_tampered = DiagnosticTroubleCode(spn=102, fmi=1, occurrence_count=2, source_address=0)
    dm_tampered = DMMessage(
        pgn=65226,
        source_address=0,
        malfunction_indicator_lamp=LampStatus.ON,
        red_stop_lamp=LampStatus.OFF,
        amber_warning_lamp=LampStatus.OFF,
        protect_lamp=LampStatus.OFF,
        dtcs=[dtc_tampered],
        timestamp_ns=1000,
    )
    hash_tampered = DiagnosticReportGenerator.calculate_canonical_hash(meta, [dm_tampered], stats, date_str)

    assert hash1 != hash_tampered, "SHA-256 must change when DTC content is modified!"

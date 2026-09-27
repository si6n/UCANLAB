from src.core.models.can_frame import CanFrame
from src.ui.desktop_app import UniversalCanDesktopApp


def test_desktop_app_review_wiring_components():
    app = UniversalCanDesktopApp()

    # 1. E2E Packager on TxSafetyGateway
    assert app.gateway.e2e_packager is not None

    # 2. E2E Validator & profile registration
    assert app.e2e_validator is not None
    reg_res = app.register_e2e_profile_by_name(arbitration_id=0x123, profile_name="AUTOSAR_P01")
    assert reg_res["success"] is True
    assert 0x123 in app._rx_e2e_profiles

    # 3. Reassembly Pipeline wired to router
    assert app.reassembly_pipeline is not None
    stats = app.reassembly_get_stats()
    assert "total_frames_processed" in stats

    # 4. J1939 Address Claim Engine wired
    assert app.address_claim_engine is not None
    status = app.j1939_get_address_claim_status()
    assert "claimed" in status
    assert "address" in status

    # 5. OBD Poller start & stop
    assert hasattr(app, "start_obd_polling")
    assert hasattr(app, "stop_obd_polling")

    # Test frame ingestion through E2E validator
    # Raw payload without CRC/counter fails P01
    bad_frame = CanFrame.create(channel_id="can0", arbitration_id=0x123, data=b"\x00" * 8, is_extended=False)
    app._ingest_live_frame(bad_frame)
    # Shouldn't raise, logged cleanly

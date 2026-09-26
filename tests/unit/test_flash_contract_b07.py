"""B-07 flash contract: frontend-shaped payload vs backend pre-arm gates.

The synchronous pre-arm gate is ``UniversalCanDesktopApp._validate_flash_prerequisites``
(R2-P1). ``DesktopApiBridge.flash_start`` is a thin passthrough to
``app.flash_start``, which invokes the gate BEFORE arming the bus — so the
contract lives on the app class, not the bridge.

Block-size, address-window and image-size bounds are ENGINE-side gates
(``FlashingConfig`` in ``src/protocols/uds/flasher.py``, covered by
``test_flasher.py``); duplicating them here with UI-level guesses would
create a second, drifting source of truth.
"""
from __future__ import annotations


def _valid_payload(**over):
    base = {
        "action_type": "ecu_flash",
        "ecu": "ECM",
        "fileName": "fw.bin",
        "sizeBytes": 4096,
        "data": "00" * 4096,
        "memoryAddress": 0x80000,
        "blockSize": 256,
        "firmwareSignature": "ab" * 32,
        "trustedPubkey": "cd" * 32,
        "expectedVin": "WVWZZZ1KZDP123456",
    }
    base.update(over)
    for k in [k for k in list(over) if over[k] is None]:
        base.pop(k, None)
    return base


class TestFlashContractB07:
    def test_valid_payload_passes(self):
        from src.ui.desktop_app import UniversalCanDesktopApp

        assert UniversalCanDesktopApp._validate_flash_prerequisites(_valid_payload()) is None

    def test_missing_signature_rejected(self):
        from src.ui.desktop_app import UniversalCanDesktopApp

        assert (
            UniversalCanDesktopApp._validate_flash_prerequisites(_valid_payload(firmwareSignature=None))
            is not None
        )

    def test_missing_anchor_rejected(self):
        from src.ui.desktop_app import UniversalCanDesktopApp

        assert (
            UniversalCanDesktopApp._validate_flash_prerequisites(_valid_payload(trustedPubkey=None))
            is not None
        )

    def test_missing_identity_rejected(self):
        from src.ui.desktop_app import UniversalCanDesktopApp

        assert (
            UniversalCanDesktopApp._validate_flash_prerequisites(_valid_payload(expectedVin=None))
            is not None
        )

    def test_renderer_identity_waiver_ignored(self, monkeypatch):
        """S1-P1-6: ``skipTargetIdentity`` from the renderer NEVER waives the
        target-identity requirement without the out-of-band lab environment
        gate (``UCANLAB_FLASH_SKIP_IDENTITY=1`` + ``UCANLAB_TEST_MODE=1``).
        A correctly-signed image must never be written to the wrong ECU."""
        from src.ui.desktop_app import UniversalCanDesktopApp

        monkeypatch.delenv("UCANLAB_FLASH_SKIP_IDENTITY", raising=False)
        monkeypatch.delenv("UCANLAB_TEST_MODE", raising=False)
        payload = _valid_payload(expectedVin=None, skipTargetIdentity=True)
        assert UniversalCanDesktopApp._validate_flash_prerequisites(payload) is not None

    def test_expected_serial_alternative_passes(self):
        from src.ui.desktop_app import UniversalCanDesktopApp

        payload = _valid_payload(expectedVin=None, expectedSerial="SN123456")
        assert UniversalCanDesktopApp._validate_flash_prerequisites(payload) is None

    def test_snake_case_aliases_accepted(self):
        from src.ui.desktop_app import UniversalCanDesktopApp

        payload = _valid_payload(
            firmwareSignature=None,
            firmware_signature="ab" * 32,
            trustedPubkey=None,
            trusted_pubkey="cd" * 32,
        )
        assert UniversalCanDesktopApp._validate_flash_prerequisites(payload) is None

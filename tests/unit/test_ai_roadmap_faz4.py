"""Unit tests for AI Engine Roadmap FAZ 4 (AI-Initiative Safe Actions & Policy Enforcement)."""


from src.engine.ai.drive_safety_policy import (
    validate_ai_dialogue_action,
)


def test_ai_proposed_read_action_policy() -> None:
    # Read-only action should be permitted by safety policy
    allowed, msg = validate_ai_dialogue_action("PROPOSE_READ", confirmed_by_operator=False, vehicle_speed_kmh=40.0)
    assert allowed is True
    assert "izin verildi" in msg


def test_ai_proposed_mutating_action_blocks_on_moving_vehicle() -> None:
    # Clear DTC proposed by AI with vehicle moving (30 km/h)
    allowed, msg = validate_ai_dialogue_action("PROPOSE_DTC_CLEAR", confirmed_by_operator=True, vehicle_speed_kmh=30.0)
    assert allowed is False
    assert "hareketsiz değil" in msg


def test_ai_proposed_mutating_action_blocks_without_confirmation() -> None:
    # Stationary but unconfirmed
    allowed, msg = validate_ai_dialogue_action("PROPOSE_DTC_CLEAR", confirmed_by_operator=False, vehicle_speed_kmh=0.0)
    assert allowed is False
    assert "çift onayı olmadan" in msg


def test_ai_proposed_mutating_action_permitted_stationary_confirmed() -> None:
    # Stationary and confirmed
    allowed, msg = validate_ai_dialogue_action("PROPOSE_DTC_CLEAR", confirmed_by_operator=True, vehicle_speed_kmh=0.0)
    assert allowed is True
    assert "izin verildi" in msg


def test_desktop_bridge_provenance_tagging() -> None:
    from unittest.mock import MagicMock

    from src.ui.desktop_app import DesktopApiBridge

    mock_app = MagicMock()
    mock_app.execute_diagnostic_action.return_value = {"success": True, "message": "OK"}
    bridge = DesktopApiBridge(mock_app)

    # 1. Operator initiated
    res_op = bridge.execute_diagnostic_action({"action_type": "uds_read_did", "is_ai_suggested": False})
    assert res_op.get("provenance") == "Operatör başlattı"

    # 2. AI suggested
    res_ai = bridge.execute_diagnostic_action({"action_type": "uds_read_did", "is_ai_suggested": True})
    assert res_ai.get("provenance") == "AI önerdi / operatör onayladı"


def test_dialogue_watchdog_staleness_protection() -> None:
    import time

    from src.engine.ai.dialogue_engine import DialogueSession, DialogueState

    dialogue = DialogueSession(activity_timeout_s=0.1)
    dialogue.proposed_actions = [{"type": "PROPOSE_READ", "title": "Test"}]
    dialogue.state = DialogueState.INTERROGATE

    # Before timeout
    assert dialogue.check_watchdog_staleness() is False
    assert len(dialogue.proposed_actions) == 1

    # Sleep past timeout
    time.sleep(0.15)

    # After timeout
    assert dialogue.check_watchdog_staleness() is True
    assert len(dialogue.proposed_actions) == 0
    assert dialogue.state == DialogueState.IDLE

"""AŞAMA 2 — Faz 2A: E-Stop / Watchdog / StateMachine derin incelemesi.

PLAN (her bulgu için: önce başarısız-önce testi, sonra düzeltme):
  A2-F1 (E-Stop): reset() backoff kapısı — DOĞRULANDI, bulgu YOK. Kötü
      şekilli girdiler backoff sırasında ucuz reddediliyor; GEÇERLİ
      operatör kredansiyeli HMAC'e ulaşıyor ve kabul ediliyor (saldırgan
      operatörü kilitleyemiyor). Test: 10 ardışık kötü reset sonrası
      GEÇERLİ token kabul edilmeli.
  A2-F2 (StateMachine): arm_tx token sırası — DOĞRULANDI, bulgu YOK.
      Token SADECE geçiş BAŞARILI olursa yanıyor (consume=False + geçiş
      sonrası burn); E-Stop engaged iken arm reddediliyor VE token
      korunuyor (aynı token reset sonrası tekrar kullanılabilir).
  A2-F3 (Watchdog): start() kirası — DOĞRULANDI, bulgu YOK. İlk start
      kirası bağlıyor; restart uzatmıyor (anti-extension); S-08 koşulu
      (monitor çalışmalı) yerinde.

Sonuç: Aşama 2 dosyalarında düzeltme gerektiren gerçek bulgu YOK.
Bu dosya, üç invarianti kilitleyen REGRESYON testleri olarak kalır.
"""

from __future__ import annotations

import time

from src.core.errors import SafetyError
from src.safety.estop import EmergencyStopSystem, EStopTriggerSource
from src.safety.state_machine import SafetyState, SafetySupervisor
from src.safety.watchdog import TxWatchdogSupervisor

_HEARTBEAT_TOKEN = "asama2-probe-token"


def _supervisor(**kwargs: object) -> SafetySupervisor:
    kwargs.setdefault("allow_unauthenticated_arm", True)
    return SafetySupervisor(**kwargs)  # type: ignore[arg-type]


def test_a2f1_valid_credential_bypasses_guess_throttle() -> None:
    """A2-F1: 10 kötü reset sonrası GEÇERLİ token kabul edilmeli.

    Saldırgan kötü token'larla backoff'u şişirse bile operatörün gerçek
    kredansiyeli HMAC'e ulaşıp kabul ediliyor — DoS yok.
    """
    estop = EmergencyStopSystem(allow_self_reset=True)
    estop.trigger(EStopTriggerSource.USER_UI_BUTTON, reason="probe")
    for _ in range(10):
        try:
            estop.reset("0:00:0:ESTOP_RESET:00")
        except Exception:
            pass
    token = estop.create_reset_token()
    assert token is not None
    estop.reset(token)  # must NOT raise ESTOP_RESET_BACKOFF
    assert not estop.is_engaged


def test_a2f2_failed_arm_preserves_single_use_token() -> None:
    """A2-F2: E-Stop engaged iken reddedilen arm token'ı yakmamalı."""
    estop = EmergencyStopSystem(allow_self_reset=True)
    secret = b"probe-arm-secret-32bytes-1234567!"
    sup = SafetySupervisor(
        initial_state=SafetyState.PASSIVE,
        estop=estop,
        auth_secret=secret,
        allow_unauthenticated_arm=False,
    )
    estop.trigger(EStopTriggerSource.USER_UI_BUTTON, reason="probe")
    token = sup.issue_arm_token()
    import pytest

    with pytest.raises(SafetyError):
        sup.arm_tx("probe", auth_token=token)
    # Token korunmalı: aynı token ile verify (consume) BAŞARILI olmalı.
    sup._verify_arm_token(token, consume=True)
    # Ve ikinci tüketim replay olarak reddedilmeli (single-use intact).
    with pytest.raises(SafetyError):
        sup._verify_arm_token(token, consume=True)


def test_a2f3_fresh_start_reports_valid_lease() -> None:
    """A2-F3: taze start() sonrası kira geçerli olmalı."""
    sup = _supervisor(initial_state=SafetyState.SAFE)
    sup.transition_to(SafetyState.PASSIVE, "t")
    watchdog = TxWatchdogSupervisor(supervisor=sup, timeout_ms=800.0)
    time.sleep(0.05)
    watchdog.arm_heartbeat_token(_HEARTBEAT_TOKEN)
    watchdog.start()
    try:
        assert watchdog.is_lease_valid is True
    finally:
        watchdog.stop()

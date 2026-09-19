"""Unit tests for AI Engine Roadmap FAZ 5 (Evidence Chain, Dialogue Transcript & R2-EN1 Seal)."""

import time

from src.core.models.diagnostics import DiagnosticDomain, DiagnosticEvent, SignalSample, SignalSource, VehicleSession
from src.engine.ai.evidence_gate import evaluate_sufficiency
from src.engine.ai.hypothesis_engine import Hypothesis
from src.engine.ai.session_report import _sign_report, build_technician_report


def test_technician_report_with_dialogue_transcript_and_eliminations() -> None:
    session = VehicleSession(
        session_id="test-faz5-sess",
        started_at_ns=time.monotonic_ns(),
        domain=DiagnosticDomain.HEAVY_DUTY,
    )
    session.events.append(
        DiagnosticEvent(
            timestamp_ns=time.monotonic_ns(),
            code="SPN 100",
            status="ACTIVE",
            severity="HIGH",
            domain=DiagnosticDomain.HEAVY_DUTY,
        )
    )
    session.samples.append(
        SignalSample(
            timestamp_ns=time.monotonic_ns(),
            name="EngineOilPressure",
            raw_value=50,
            physical_value=50.0,
            unit="kPa",
            source=SignalSource.J1939,
            confidence=1.0,
        )
    )
    sufficiency = evaluate_sufficiency(session)
    hyp = Hypothesis(id="oil-pump-wear", fault="Yağ pompası aşınması", score=0.85)

    transcript = [
        {
            "question": "Yağ çubuğunu çektiniz mi?",
            "answer": "Evet (Min-Max arasında)",
            "kind": "yes_no",
            "evidence_link": "kb:SPN100",
        },
        {
            "question": "Mekanik manometre ölçümü yapıldı mı?",
            "answer": "120 kPa",
            "kind": "measurement",
            "evidence_link": "test:1",
        },
    ]

    eliminated = [
        {
            "fault": "Sensör tesisatı kopuk",
            "reason": "Manometre de düşük okudu; sensör sağlam",
            "eliminated_by_question_id": "Q_SPN100_MANOMETER",
        }
    ]

    correction = "Karter süzgeci tıkalıydı, temizlendiğinde basınç 280 kPa'ya yükseldi."

    report = build_technician_report(
        session=session,
        sufficiency=sufficiency,
        anomalies=[],
        hypotheses=[hyp],
        similar_cases=[],
        dialogue_transcript=transcript,
        eliminated_hypotheses=eliminated,
        technician_correction=correction,
    )

    assert "## İnteraktif Diyalog Transkripti & Kanıt Zinciri" in report
    assert "Yağ çubuğunu çektiniz mi?" in report
    assert "## Elenen Olasılıklar ve Nedenleri" in report
    assert "Sensör tesisatı kopuk" in report
    assert "## Teknisyen Düzeltme Notu (\"Yanıldım\" Geri Bildirimi)" in report
    assert "Karter süzgeci tıkalıydı" in report
    assert "Rapor Kriptografik Mührü (R2-EN1):" in report


def test_r2_en1_cryptographic_seal_varieties() -> None:
    body = "Test diagnostic report content"

    # Keyless seal: SHA-256 integrity checksum
    seal_keyless, algo_keyless = _sign_report(body, signing_key=None)
    assert len(seal_keyless) == 64
    assert "SHA-256" in algo_keyless

    # Keyed seal: HMAC-SHA256 tamper-evident seal
    key = b"secret-report-signing-key-12345"
    seal_keyed, algo_keyed = _sign_report(body, signing_key=key)
    assert len(seal_keyed) == 64
    assert "HMAC-SHA256" in algo_keyed
    assert seal_keyed != seal_keyless

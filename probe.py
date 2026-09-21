import time
from src.core.models.diagnostics import VehicleSession, SignalSample, SignalSource, DiagnosticDomain
from src.engine.ai.anomaly_detector import detect_anomalies, load_thresholds
from src.engine.ai.hypothesis_engine import rank_hypotheses
th = load_thresholds()
now = time.monotonic_ns()
def mk(name):
    s = VehicleSession(session_id="t", started_at_ns=now, domain=DiagnosticDomain.HEAVY_DUTY)
    for i in range(20):
        s.samples.append(SignalSample(timestamp_ns=now+i, name=name, raw_value=150, physical_value=150.0, unit="C", source=SignalSource.J1939, confidence=1.0))
    return s
for name in ("OP:EngineCoolantTemp", "EngineCoolantTemp"):
    s = mk(name)
    f = detect_anomalies(s, th)
    print(repr(name), "findings=", [(x.signal, x.synthetic) for x in f])
    print("   hyps(anomalies):", [(x.id, round(x.score,3)) for x in rank_hypotheses(s, f)][:4])
    print("   hyps(anomalies=[]):", [(x.id, round(x.score,3)) for x in rank_hypotheses(s, [])][:4])

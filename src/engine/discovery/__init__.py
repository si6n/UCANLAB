"""Evidence-Based Signal Discovery & Reverse Engineering Engine.

Complies with MASTER_PLAN.md Section 7 and docs/specs/signal_discovery_spec.md.
"""

from src.engine.discovery.bitstats import BitStats
from src.engine.discovery.dbc_builder import DbcBuilder
from src.engine.discovery.detectors.checksum import ChecksumDetector, Crc8Model
from src.engine.discovery.detectors.counter import CounterDetector
from src.engine.discovery.engine import SignalDiscoveryEngine
from src.engine.discovery.evidence import ConfidenceReport, evidence_report, score_hypothesis
from src.engine.discovery.hypotheses import Evidence, Hypothesis, IdReport
from src.engine.discovery.segmenter import SignalSegmenter

__all__ = [
    "BitStats",
    "ChecksumDetector",
    "ConfidenceReport",
    "CounterDetector",
    "Crc8Model",
    "DbcBuilder",
    "Evidence",
    "Hypothesis",
    "IdReport",
    "SignalDiscoveryEngine",
    "SignalSegmenter",
    "evidence_report",
    "score_hypothesis",
]

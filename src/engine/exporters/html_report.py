"""Canonical HTML service-report module (R2-EN4).

`pdf_report.py` is kept as a backward-compatible alias; new code imports
from here.
"""

from src.engine.exporters.pdf_report import (
    DiagnosticReportGenerator,
    ServiceReportMetadata,
)

__all__ = ["DiagnosticReportGenerator", "ServiceReportMetadata"]

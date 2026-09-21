"""Canonical, correctly-named import surface for the service-report generator.

The generator writes an HTML file — never a PDF — so this module (not
`pdf_report`) carries the honest name for the public API. The implementation
currently lives in `pdf_report.py`, which is retained only because callers
still import that legacy name; `pdf_report` is NOT the canonical module,
despite what this docstring previously claimed (corrected under P3-7).

New code should import from here:

    from src.engine.exporters.html_report import DiagnosticReportGenerator
"""

from src.engine.exporters.pdf_report import (
    DiagnosticReportGenerator,
    ServiceReportMetadata,
)

__all__ = ["DiagnosticReportGenerator", "ServiceReportMetadata"]

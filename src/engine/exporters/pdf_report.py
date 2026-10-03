"""Service-report implementation (produces HTML, NOT PDF; legacy module name).

P3-7 — HONEST NAMING: this module is called `pdf_report` but no PDF is ever
produced. `DiagnosticReportGenerator.generate_html_report` writes an HTML file,
and the docstring used to describe the module as an "alias" of a canonical
`html_report` module even though THIS file holds the implementation and
`html_report.py` is the thin re-export. Both statements were backwards.

The current, truthful arrangement is:

* `html_report.py` — the CANONICAL, correctly-named import surface
  (re-exports the two public names below);
* `pdf_report.py` (this file) — the implementation, kept under its historical
  name only so existing imports keep resolving. The name is a leftover from an
  earlier design and new code must import from `html_report`.

Nothing here should be renamed without updating every caller; the misleading
part fixed by P3-7 is the claim, not the module path.
"""

from __future__ import annotations

import hashlib
import hmac as _hmac
import html as html_mod
import time
from dataclasses import dataclass
from pathlib import Path

from src.core.logging import get_logger
from src.engine.exporters.path_guard import atomic_write_text, resolve_export_path
from src.protocols.j1939.diagnostics import DMMessage
from src.version import __version__

logger = get_logger("engine.exporters.report")


def _sign_canonical(raw: str, signing_key: bytes | None) -> tuple[str, str]:
    """R2-EN1: keyed HMAC when a REPORT_SIGNING_KEY is supplied, else plain hash.

    Returns (hex_digest, label). A keyless SHA-256 is an integrity checksum
    only — anyone can recompute it — so the label must never claim
    tamper-evidence for it.
    """
    if signing_key:
        digest = _hmac.new(signing_key, raw.encode("utf-8"), hashlib.sha256).hexdigest().upper()
        return digest, "HMAC-SHA256 (keyed, tamper-evident)"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest().upper(), "SHA-256 (integrity checksum — not tamper-evident)"


def _resolve_export_path(output_file: str | Path, exports_root: str | Path | None) -> Path:
    # F7: shared, fail-closed confinement (see path_guard). The former local
    # copy exempted the system temp dir when `exports_root` was omitted.
    return resolve_export_path(output_file, exports_root, allow_cwd_fallback=True)


@dataclass(slots=True)
class ServiceReportMetadata:
    """Metadata for vehicle service report."""

    vin_or_hin: str
    technician_name: str
    workshop_name: str
    notes: str = ""


class DiagnosticReportGenerator:
    """Generates structured HTML & printable diagnostic service reports with SHA-256 session hash."""

    @classmethod
    def generate_html_report(
        cls,
        output_file: str | Path,
        metadata: ServiceReportMetadata,
        dm_messages: list[DMMessage],
        summary_stats: dict[str, str | int | float],
        exports_root: str | Path | None = None,
        signing_key: bytes | None = None,
    ) -> Path:
        """Generate structured HTML diagnostic report with session seal.

        R2-EN1: pass the `REPORT_SIGNING_KEY` bytes for a keyed HMAC seal;
        without it the report carries an integrity checksum only.
        """
        path = _resolve_export_path(output_file, exports_root)
        path.parent.mkdir(parents=True, exist_ok=True)

        now_str = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())

        # Collect DTC rows (all user/signal-derived strings HTML-escaped — F-02, CWE-79)
        dtc_rows = []
        for dm in dm_messages:
            for dtc in dm.dtcs:
                criticality_badge = (
                    '<span style="color:red; font-weight:bold;">KRİTİK</span>'
                    if dtc.is_critical
                    else '<span style="color:orange;">UYARI</span>'
                )
                fmi_tr = html_mod.escape(dtc.fmi_description_tr)
                fmi_en = html_mod.escape(dtc.fmi_description_en)
                dtc_rows.append(
                    f"<tr>"
                    f"<td>0x{dm.source_address:02X}</td>"
                    f"<td>SPN {dtc.spn}</td>"
                    f"<td>FMI {dtc.fmi}</td>"
                    f"<td>{dtc.occurrence_count}</td>"
                    f"<td>{fmi_tr} ({fmi_en})</td>"
                    f"<td>{criticality_badge}</td>"
                    f"</tr>"
                )

        if not dtc_rows:
            dtc_rows_html = "<tr><td colspan='6' style='text-align:center; color:green;'>✅ Aktif veya Kayıtlı Arıza Kodu Bulunmamaktadır (No Faults Detected)</td></tr>"
        else:
            dtc_rows_html = "\n".join(dtc_rows)

        # Render summary stats table if provided (E14)
        stats_html = ""
        if summary_stats:
            stats_rows = []
            for k, v in sorted(summary_stats.items()):
                stats_rows.append(
                    f"<tr><td><strong>{html_mod.escape(str(k))}</strong></td><td>{html_mod.escape(str(v))}</td></tr>"
                )
            stats_html = f"""
  <h2>📊 Seans Telemetri Özeti (Summary Statistics)</h2>
  <table>
    <thead><tr><th>Metrik / Parametre</th><th>Değer</th></tr></thead>
    <tbody>{''.join(stats_rows)}</tbody>
  </table>
"""

        # Compute the tamper-evident session seal (E1). SCOPE: the digest covers
        # the canonical METADATA + DTC list + stats + date record below — NOT
        # the rendered HTML bytes. Editing a rendered cell (e.g. a DTC
        # description) is not detectable by this seal; its guarantee is that
        # the canonical diagnostic record itself is unmodified.
        canonical_dtcs = []
        for dm in dm_messages:
            for dtc in dm.dtcs:
                canonical_dtcs.append(
                    f"{dm.source_address}:{dtc.spn}:{dtc.fmi}:{dtc.occurrence_count}:{dtc.is_critical}"
                )
        canonical_dtcs.sort()

        canonical_stats = []
        if summary_stats:
            for k, v in sorted(summary_stats.items()):
                canonical_stats.append(f"{k}={v}")

        raw_to_hash = (
            f"VIN={metadata.vin_or_hin}|"
            f"TECH={metadata.technician_name}|"
            f"SHOP={metadata.workshop_name}|"
            f"NOTES={metadata.notes}|"
            f"DATE={now_str}|"
            f"DTCS={','.join(canonical_dtcs)}|"
            f"STATS={','.join(canonical_stats)}"
        )
        report_sha256, seal_label = _sign_canonical(raw_to_hash, signing_key)

        html_content = f"""<!DOCTYPE html>
<html lang="tr">
<head>
  <meta charset="UTF-8">
  <title>Universal CAN-Bus Telemetry & Diagnostic Report</title>
  <style>
    body {{ font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; margin: 40px; color: #333; }}
    h1 {{ color: #1E3A8A; border-bottom: 2px solid #1E3A8A; padding-bottom: 10px; }}
    table {{ width: 100%; border-collapse: collapse; margin-top: 20px; }}
    th, td {{ border: 1px solid #CBD5E1; padding: 10px; text-align: left; }}
    th {{ background-color: #F1F5F9; color: #1E293B; }}
    .meta-box {{ background-color: #F8FAFC; border: 1px solid #E2E8F0; padding: 15px; border-radius: 6px; margin-bottom: 20px; }}
    .signature {{ font-family: monospace; color: #64748B; font-size: 11px; margin-top: 30px; }}
  </style>
</head>
<body>
  <h1>🛠 Universal CAN-Bus Servis Teşhis & Telemetri Raporu</h1>

  <div class="meta-box">
    <p><strong>Araç / Tekne Kimliği (VIN / HIN):</strong> {html_mod.escape(metadata.vin_or_hin)}</p>
    <p><strong>Servis / Atölye:</strong> {html_mod.escape(metadata.workshop_name)} | <strong>Teknisyen:</strong> {html_mod.escape(metadata.technician_name)}</p>
    <p><strong>Rapor Tarihi:</strong> {now_str}</p>
    <p><strong>Notlar:</strong> {html_mod.escape(metadata.notes or "Rutin periyodik kontrol ve telemetri doğrulaması.")}</p>
  </div>

  <h2>📋 Diyagnostik Arıza Kodları (DTCs)</h2>
  <table>
    <thead>
      <tr>
        <th>Kaynak (SA)</th>
        <th>SPN</th>
        <th>FMI</th>
        <th>Tekrar (OC)</th>
        <th>Arıza Tanımı</th>
        <th>Durum</th>
      </tr>
    </thead>
    <tbody>
      {dtc_rows_html}
    </tbody>
  </table>
  {stats_html}
  <div class="signature">
    <p>🔒 <strong>Session seal [{seal_label}]:</strong> {report_sha256}</p>
    <p>Platform: Universal CAN-Bus Diagnostic & Telemetry System v{__version__} (SAE J1939 / NMEA 2000 / ISO 14229)</p>
  </div>
</body>
</html>
"""
        # Residual item #2: atomic write — a report carrying a session seal must
        # never be observed truncated (a half-written file would fail to load
        # or render, not merely mismatch the seal, which does not cover the
        # HTML bytes — see the scope note above).
        atomic_write_text(path, html_content)

        logger.info("Generated Diagnostic Service HTML Report", extra={"file": str(path), "hash": report_sha256})
        return path

    @classmethod
    def calculate_canonical_hash(
        cls,
        metadata: ServiceReportMetadata,
        dm_messages: list[DMMessage],
        summary_stats: dict[str, str | int | float],
        date_str: str,
        signing_key: bytes | None = None,
    ) -> str:
        """Calculate canonical session seal for verification (R2-EN1: HMAC when keyed)."""
        canonical_dtcs = []
        for dm in dm_messages:
            for dtc in dm.dtcs:
                canonical_dtcs.append(
                    f"{dm.source_address}:{dtc.spn}:{dtc.fmi}:{dtc.occurrence_count}:{dtc.is_critical}"
                )
        canonical_dtcs.sort()

        canonical_stats = []
        if summary_stats:
            for k, v in sorted(summary_stats.items()):
                canonical_stats.append(f"{k}={v}")

        raw_to_hash = (
            f"VIN={metadata.vin_or_hin}|"
            f"TECH={metadata.technician_name}|"
            f"SHOP={metadata.workshop_name}|"
            f"NOTES={metadata.notes}|"
            f"DATE={date_str}|"
            f"DTCS={','.join(canonical_dtcs)}|"
            f"STATS={','.join(canonical_stats)}"
        )
        digest, _label = _sign_canonical(raw_to_hash, signing_key)
        return digest

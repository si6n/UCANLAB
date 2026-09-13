"""Google Earth KML GPS Track Exporter."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from src.core.logging import get_logger

logger = get_logger("engine.exporters.kml")

_TRACK_NAME_RE = re.compile(r"^[A-Za-z0-9 _.\-]{1,128}$")


def _resolve_export_path(output_file: str | Path, exports_root: str | Path | None) -> Path:
    import tempfile as _tempfile

    explicit = exports_root is not None
    root = Path(exports_root) if explicit else (Path.cwd() / "exports")
    resolved = Path(output_file).resolve()
    root_resolved = root.resolve()
    try:
        is_inside = resolved.is_relative_to(root_resolved)
    except Exception as exc:
        raise ValueError(f"Export path validation failed: {exc}") from exc
    if not is_inside:
        # Backward-compat for tests/tooling: an omitted exports_root still
        # permits system-temp staging. Production callers pass an explicit
        # exports_root and get a strict fail-closed check.
        if not explicit:
            try:
                if resolved.is_relative_to(Path(_tempfile.gettempdir()).resolve()):
                    return resolved
            except Exception:
                pass
        raise ValueError(f"Export path escapes exports root: {resolved}")
    return resolved


@dataclass(slots=True)
class GpsPoint:
    """GPS coordinate waypoint."""

    latitude: float
    longitude: float
    altitude_m: float = 0.0
    speed_knots: float = 0.0
    timestamp_iso: str = ""


class KmlExporter:
    """Exports GPS coordinates into standard Google Earth .kml paths."""

    @classmethod
    def export_track(
        cls,
        output_file: str | Path,
        track_name: str,
        points: list[GpsPoint],
        exports_root: str | Path | None = None,
    ) -> Path:
        """Export GPS waypoints list into KML file."""
        path = _resolve_export_path(output_file, exports_root)
        path.parent.mkdir(parents=True, exist_ok=True)

        if not isinstance(track_name, str) or not _TRACK_NAME_RE.fullmatch(track_name):
            raise ValueError(f"Invalid track name: {track_name!r}")
        # XML-escape user-supplied track name (F-02, CWE-91)
        safe_track_name = xml_escape(track_name)

        validated: list[str] = []
        for p in points:
            try:
                lat = float(p.latitude)
                lon = float(p.longitude)
                alt = float(p.altitude_m)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Non-numeric GPS coordinate: {exc}") from exc
            if not (math.isfinite(lat) and math.isfinite(lon) and math.isfinite(alt)):
                raise ValueError("Non-finite GPS coordinate rejected")
            if not (-90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0):
                raise ValueError(f"GPS coordinate out of range: lat={lat} lon={lon}")
            validated.append(f"{lon},{lat},{alt}")
        coords_str = " ".join(validated)

        kml_content = f"""<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>{safe_track_name}</name>
    <Style id="trackLine">
      <LineStyle>
        <color>ff0000ff</color>
        <width>4</width>
      </LineStyle>
    </Style>
    <Placemark>
      <name>{safe_track_name} Path</name>
      <styleUrl>#trackLine</styleUrl>
      <LineString>
        <extrude>1</extrude>
        <tessellate>1</tessellate>
        <altitudeMode>relativeToGround</altitudeMode>
        <coordinates>
          {coords_str}
        </coordinates>
      </LineString>
    </Placemark>
  </Document>
</kml>
"""
        with open(path, "w", encoding="utf-8") as f:
            f.write(kml_content)

        logger.info("Saved Google Earth KML track", extra={"file": str(path), "points": len(points)})
        return path

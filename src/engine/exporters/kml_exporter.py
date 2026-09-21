"""Google Earth KML GPS Track Exporter."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape as xml_escape

from src.core.logging import get_logger
from src.engine.exporters.path_guard import atomic_write_text, resolve_export_path

logger = get_logger("engine.exporters.kml")

_TRACK_NAME_RE = re.compile(r"^[A-Za-z0-9 _.\-]{1,128}$")


def _resolve_export_path(output_file: str | Path, exports_root: str | Path | None) -> Path:
    # F7: shared, fail-closed confinement. The previous local copy waived the
    # root check for anything under the system temp dir when `exports_root`
    # was omitted — a write-outside-the-tree primitive.
    return resolve_export_path(output_file, exports_root, allow_cwd_fallback=True)


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
        # Residual item #2: atomic write — a KML file must never be observed
        # half-written (see path_guard.atomic_write_text).
        atomic_write_text(path, kml_content)

        logger.info("Saved Google Earth KML track", extra={"file": str(path), "points": len(points)})
        return path

"""Persisted mechanic-flow preferences (Aşama 4).

Stores the usage mode ("mechanic" / "engineer") chosen on first run and the
last selected vehicle profile, in the per-user app data root. The WebView's
localStorage is not used for this: it is not guaranteed to survive a
pywebview/EdgeChromium profile reset, and the mode also gates Python-side
behaviour. Writes are atomic (temp file + replace) so a crash cannot leave a
half-written file; a corrupt file reads as "no preference" (ask again).
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from src.core.logging import get_logger

logger = get_logger("ui.mechanic_prefs")

MODES = ("mechanic", "engineer")
_MAX_ID_CHARS = 64


@dataclass(frozen=True)
class MechanicPrefs:
    mode: str | None = None
    vehicle_profile_id: str | None = None


class MechanicPrefsStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()

    def load(self) -> MechanicPrefs:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return MechanicPrefs()
        except (OSError, ValueError) as exc:
            logger.warning("Mechanic preferences unreadable; asking again", extra={"error": type(exc).__name__})
            return MechanicPrefs()
        if not isinstance(raw, dict):
            return MechanicPrefs()
        mode = raw.get("mode") if raw.get("mode") in MODES else None
        vid = raw.get("vehicle_profile_id")
        vid = vid if isinstance(vid, str) and 0 < len(vid) <= _MAX_ID_CHARS else None
        return MechanicPrefs(mode=mode, vehicle_profile_id=vid)

    def save(self, prefs: MechanicPrefs) -> None:
        if prefs.mode is not None and prefs.mode not in MODES:
            raise ValueError(f"unknown mode {prefs.mode!r}")
        payload = json.dumps({"mode": prefs.mode, "vehicle_profile_id": prefs.vehicle_profile_id})
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".mechanic_prefs.", dir=str(self.path.parent))
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    fh.write(payload)
                os.replace(tmp, self.path)
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise

    def update(self, **changes: str | None) -> MechanicPrefs:
        current = self.load()
        merged = MechanicPrefs(
            mode=changes.get("mode", current.mode),
            vehicle_profile_id=changes.get("vehicle_profile_id", current.vehicle_profile_id),
        )
        self.save(merged)
        return merged

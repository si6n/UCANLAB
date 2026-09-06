"""Resumable chunked MDF4 telemetry upload (Task 5.4 client side).

 Protocol (MASTER_PLAN §16):
   POST /api/v1/telematics/sessions            — announce size + SHA-256
   PUT  /api/v1/telematics/sessions/{id}/chunks/{idx}  — 5 MB binary parts
   POST /api/v1/telematics/sessions/{id}/complete      — finalize on worker

 The uploader is resumable: on reconnect it queries the session state and
 skips chunks the server already holds (acked via received_chunks counter).
"""

from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from src.core.errors import LicenseError
from src.core.logging import get_logger
from src.security.cloud.client import CloudClient

logger = get_logger("security.cloud.telemetry_uploader")

DEFAULT_CHUNK_SIZE = 5 * 1024 * 1024  # 5 MB — matches backend minimum window


@dataclass(slots=True)
class UploadProgress:
    """Live progress reported to the UI layer."""

    session_id: str | None = None
    total_chunks: int = 0
    uploaded_chunks: int = 0
    bytes_sent: int = 0
    total_bytes: int = 0
    status: str = "idle"  # idle|uploading|processing|ready|failed
    error: str | None = None

    @property
    def percent(self) -> float:
        if self.total_bytes == 0:
            return 0.0
        return min(100.0, self.bytes_sent / self.total_bytes * 100.0)


@dataclass(slots=True)
class UploadResult:
    session_id: str
    status: str  # ready|processing|failed
    archive_s3_key: str | None = None


class TelemetryUploader:
    """Uploads an MDF4 session file to the cloud telemetry store."""

    def __init__(
        self,
        client: CloudClient,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        progress_callback: Callable[[UploadProgress], None] | None = None,
    ) -> None:
        self.client = client
        self.chunk_size = chunk_size
        self._progress_cb = progress_callback

    # ------------------------------------------------------------------
    def upload_file(
        self,
        file_path: str | Path,
        vehicle_vin: str | None = None,
    ) -> UploadResult:
        path = Path(file_path)
        if not path.is_file():
            raise LicenseError(f"Telemetry file not found: {path}", code="FILE_NOT_FOUND")

        # MED-5: Compute file size and streaming SHA-256 without loading entire file into memory
        file_size = path.stat().st_size
        if file_size == 0:
            raise LicenseError("Cannot upload empty telemetry file", code="EMPTY_TELEMETRY_FILE")

        hasher = hashlib.sha256()
        with open(path, "rb") as f:
            while chunk := f.read(64 * 1024):
                hasher.update(chunk)
        sha256 = hasher.hexdigest()
        total_chunks = math.ceil(file_size / self.chunk_size)

        progress = UploadProgress(total_bytes=file_size, total_chunks=total_chunks, status="uploading")
        self._emit(progress)

        # 1. Announce the session (size + digest + chunk size).
        resp = self.client.request(
            "POST",
            "/telematics/sessions",
            json_body={
                "vehicle_vin": vehicle_vin,
                "declared_size_bytes": file_size,
                "declared_sha256": sha256,
                "chunk_size_bytes": self.chunk_size,
            },
        )
        if resp.status != 201:
            raise LicenseError(
                f"Session announce failed (HTTP {resp.status})",
                code="SESSION_ANNOUNCE_FAILED",
            )

        session = resp.json()
        session_id = session["id"]
        progress.session_id = session_id
        progress.uploaded_chunks = session.get("received_chunks", 0)
        self._emit(progress)

        # 2. Upload chunks with seek/read to keep memory constant.
        first_unsent = progress.uploaded_chunks
        progress.bytes_sent = min(file_size, first_unsent * self.chunk_size)
        self._emit(progress)
        with open(path, "rb") as f:
            for index in range(total_chunks):
                if index < first_unsent:
                    continue

                f.seek(index * self.chunk_size)
                chunk = f.read(self.chunk_size)

                put = self.client.request(
                    "PUT",
                    f"/telematics/sessions/{session_id}/chunks/{index}",
                    raw_body=chunk,
                    content_type="application/octet-stream",
                )
                if put.status != 200:
                    progress.status = "failed"
                    progress.error = f"chunk {index} rejected (HTTP {put.status})"
                    self._emit(progress)
                    raise LicenseError(progress.error, code="CHUNK_UPLOAD_FAILED")

                progress.uploaded_chunks += 1
                progress.bytes_sent += len(chunk)
                self._emit(progress)

        # 3. Complete — the cloud worker verifies SHA-256, archives to S3 and
        #    ingests signals into TimescaleDB.
        done = self.client.request(
            "POST",
            f"/telematics/sessions/{session_id}/complete",
            json_body={"client_sha256": sha256},
        )
        if done.status not in (200, 202):
            progress.status = "failed"
            progress.error = f"complete rejected (HTTP {done.status})"
            self._emit(progress)
            raise LicenseError(progress.error, code="COMPLETE_FAILED")

        result_data = done.json()
        progress.status = result_data.get("status", "processing")
        self._emit(progress)

        logger.info(
            "Telemetry session uploaded",
            extra={"session_id": session_id, "bytes": file_size, "chunks": total_chunks},
        )
        return UploadResult(
            session_id=session_id,
            status=progress.status,
            archive_s3_key=None,
        )

    # ------------------------------------------------------------------
    def resume(self, session_id: str) -> UploadProgress:
        """Query a session's current state (status report, not a resume driver).

        NOTE: this reports server-side counters for display/monitoring. To
        continue an interrupted session, re-run upload_file — chunk PUTs are
        idempotent, so re-sent chunks are safe; a per-chunk state query would be
        needed for true gap-aware resume (not part of the current contract).
        """
        resp = self.client.request("GET", f"/telematics/sessions/{session_id}")
        if resp.status != 200:
            raise LicenseError(f"Session not found: {session_id}", code="SESSION_NOT_FOUND")
        data = resp.json()
        return UploadProgress(
            session_id=session_id,
            total_chunks=data["total_chunks"],
            uploaded_chunks=data["received_chunks"],
            bytes_sent=data["uploaded_size_bytes"],
            total_bytes=data["declared_size_bytes"],
            status=data["status"],
        )

    def _emit(self, progress: UploadProgress) -> None:
        if self._progress_cb:
            try:
                self._progress_cb(progress)
            except Exception:  # UI callback must never break the transfer
                logger.debug("progress callback raised", exc_info=True)

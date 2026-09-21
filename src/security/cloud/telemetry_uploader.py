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
import re as _re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable
from urllib.parse import quote as _quote

from src.core.errors import LicenseError
from src.core.logging import get_logger
from src.security.cloud.client import CloudClient

logger = get_logger("security.cloud.telemetry_uploader")

DEFAULT_CHUNK_SIZE = 5 * 1024 * 1024  # 5 MB — matches backend minimum window

# Server-controlled session id must never shape a URL path unchecked.
# Allowlist is [A-Za-z0-9-_]; underscore is kept because backend session
# ids use it (e.g. "ses_...") and it is path-safe once quoted with safe="".
_SESSION_ID_RE = _re.compile(r"^[A-Za-z0-9\-_]{8,128}$")


def _sanitize_session_id(raw: object) -> str:
    sid = str(raw or "").strip()
    if not sid or not _SESSION_ID_RE.fullmatch(sid):
        raise LicenseError("Invalid telemetry session id from server", code="INVALID_SESSION_ID")
    return _quote(sid, safe="")


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
    @staticmethod
    def _parse_received_chunks(session_data: dict[str, object], total_chunks: int) -> set[int]:
        """Extract set of already received chunk indices from server session data.

        Supports:
        - 'received_chunk_indices' or 'acknowledged_chunks': list/set/tuple of int indices
        - 'received_chunks': list/set/tuple of int indices OR int count (legacy fallback)
        - 'received_bitmap' or 'chunks_bitmap': bitstring where '1' = received
        """
        received: set[int] = set()

        for key in ("received_chunk_indices", "acknowledged_chunks"):
            indices_val = session_data.get(key)
            if isinstance(indices_val, (list, tuple, set)):
                for idx in indices_val:
                    if isinstance(idx, int) and 0 <= idx < total_chunks:
                        received.add(idx)
                return received

        chunks_val = session_data.get("received_chunks")
        if isinstance(chunks_val, (list, tuple, set)):
            for idx in chunks_val:
                if isinstance(idx, int) and 0 <= idx < total_chunks:
                    received.add(idx)
            return received

        for key in ("received_bitmap", "chunks_bitmap"):
            bm_val = session_data.get(key)
            if isinstance(bm_val, str):
                for idx, bit in enumerate(bm_val[:total_chunks]):
                    if bit == "1":
                        received.add(idx)
                return received

        if isinstance(chunks_val, int) and not isinstance(chunks_val, bool) and chunks_val > 0:
            for idx in range(min(chunks_val, total_chunks)):
                received.add(idx)

        return received

    def upload_file(
        self,
        file_path: str | Path,
        vehicle_vin: str | None = None,
        session_id: str | None = None,
        user_consented: bool = False,
    ) -> UploadResult:
        """Upload a telemetry file in resumable chunks.

        R2-S3: `vehicle_vin` is vehicle-identifying data — it is sent only
        with explicit operator consent (`user_consented=True`); otherwise a
        VIN-bearing upload fails closed. VINs never appear in logs.
        """
        if vehicle_vin is not None and not user_consented:
            raise LicenseError(
                "Telemetry upload with vehicle VIN requires explicit operator consent",
                code="TELEMETRY_CONSENT_REQUIRED",
            )
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

        # 1. Announce or resume session.
        if session_id:
            clean_session_id = _sanitize_session_id(session_id)
            resp = self.client.request("GET", f"/telematics/sessions/{clean_session_id}")
            if resp.status != 200:
                raise LicenseError(
                    f"Session not found for resume (HTTP {resp.status})",
                    code="SESSION_NOT_FOUND",
                )
            session = resp.json_object()
            session_id = clean_session_id
        else:
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

            session = resp.json_object()
            if not isinstance(session, dict) or not session.get("id"):
                raise LicenseError("Session announce returned no id", code="SESSION_ANNOUNCE_FAILED")
            session_id = _sanitize_session_id(session.get("id"))
        progress.session_id = session_id

        # 2. Gap-aware chunk tracking: inspect bitmap/list of chunks from server
        received_chunk_indices = self._parse_received_chunks(session, total_chunks)
        progress.uploaded_chunks = len(received_chunk_indices)
        progress.bytes_sent = sum(
            min(self.chunk_size, max(0, file_size - idx * self.chunk_size))
            for idx in received_chunk_indices
        )
        self._emit(progress)

        # 3. Upload chunks with seek/read to keep memory constant.
        with open(path, "rb") as f:
            for index in range(total_chunks):
                if index in received_chunk_indices:
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

                received_chunk_indices.add(index)
                progress.uploaded_chunks = len(received_chunk_indices)
                progress.bytes_sent += len(chunk)
                self._emit(progress)

        # 4. Complete — the cloud worker verifies SHA-256, archives to S3 and
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

        result_data = done.json_object()
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
        """Query a session's current state and received chunks."""
        session_id = _sanitize_session_id(session_id)
        resp = self.client.request("GET", f"/telematics/sessions/{session_id}")
        if resp.status != 200:
            raise LicenseError(f"Session not found: {session_id}", code="SESSION_NOT_FOUND")
        data = resp.json_object()
        total_chunks = data.get("total_chunks", 0)
        received_chunks = self._parse_received_chunks(data, total_chunks)
        uploaded_count = len(received_chunks) if received_chunks else (
            data.get("received_chunks", 0) if isinstance(data.get("received_chunks"), int) else 0
        )
        return UploadProgress(
            session_id=session_id,
            total_chunks=total_chunks,
            uploaded_chunks=uploaded_count,
            bytes_sent=data.get("uploaded_size_bytes", 0),
            total_bytes=data.get("declared_size_bytes", 0),
            status=data.get("status", "uploading"),
        )

    def _emit(self, progress: UploadProgress) -> None:
        if self._progress_cb:
            try:
                self._progress_cb(progress)
            except Exception:  # UI callback must never break the transfer
                logger.debug("progress callback raised", exc_info=True)

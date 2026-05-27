"""
AttendanceIngestionClient — edge → attendance orchestration bridge (D.2B).

Posts successful recognition decisions to POST /attendance/recognition/events.
Separate from CloudVerificationClient (/verify/image). Never raises; failures
are logged and returned in AttendanceIngestionResult.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger("attendance_client")


class AttendanceIngestOutcome(Enum):
    SENT = "sent"
    DISABLED = "disabled"
    SKIPPED = "skipped"
    TIMEOUT = "timeout"
    CONNECTION_ERROR = "connection_error"
    SERVER_ERROR = "server_error"
    CLIENT_ERROR = "client_error"


@dataclass
class AttendanceIngestionResult:
    outcome: AttendanceIngestOutcome
    sent: bool = False
    accepted: Optional[bool] = None
    disposition: Optional[str] = None
    detail: Optional[str] = None
    rtt_ms: float = 0.0
    http_status: Optional[int] = None
    from_state: Optional[str] = None
    to_state: Optional[str] = None
    lecture_id: Optional[str] = None

    def to_diag_dict(self) -> dict[str, Any]:
        return {
            "attendance_sent": int(self.sent),
            "attendance_disposition": self.disposition or self.outcome.value,
            "attendance_rtt_ms": round(self.rtt_ms, 2) if self.rtt_ms else None,
            "attendance_accepted": int(self.accepted) if self.accepted is not None else None,
        }


def resolve_attendance_api_url() -> str:
    """Build ingestion URL from ATTENDANCE_API_URL or CLOUD_SERVER_URL."""
    explicit = os.environ.get("ATTENDANCE_API_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    cloud_base = os.environ.get("CLOUD_SERVER_URL", "http://localhost:8000").rstrip("/")
    return f"{cloud_base}/attendance/recognition/events"


class AttendanceIngestionClient:
    """Lightweight HTTP client for attendance recognition-event ingestion."""

    def __init__(
        self,
        *,
        enabled: bool,
        api_url: str,
        camera_id: str,
        timeout_s: float = 1.0,
    ) -> None:
        self.enabled = enabled and bool(api_url)
        self.api_url = api_url.rstrip("/") if api_url else ""
        self.camera_id = camera_id.strip()
        self.timeout_s = timeout_s

        self._session = requests.Session()
        retry = Retry(total=0, connect=0, read=0, redirect=0, backoff_factor=0)
        adapter = HTTPAdapter(max_retries=retry)
        self._session.mount("http://", adapter)
        self._session.mount("https://", adapter)

        if self.enabled:
            log.info(
                "AttendanceIngestionClient → %s camera_id=%r timeout=%.1fs",
                self.api_url,
                self.camera_id or "(none)",
                self.timeout_s,
            )
        else:
            log.info("AttendanceIngestionClient disabled")

    def emit(
        self,
        *,
        gallery_identity: str,
        confidence: float,
        source: str = "edge_runtime",
        timestamp_ms: Optional[int] = None,
    ) -> AttendanceIngestionResult:
        """POST a recognition event. Never raises."""
        if not self.enabled:
            return AttendanceIngestionResult(
                outcome=AttendanceIngestOutcome.DISABLED,
                disposition="disabled",
            )

        identity = (gallery_identity or "").strip()
        if not identity or identity in ("NA", "UNKNOWN", "unknown"):
            return AttendanceIngestionResult(
                outcome=AttendanceIngestOutcome.SKIPPED,
                disposition="skipped_invalid_identity",
            )

        payload: dict[str, Any] = {
            "gallery_identity": identity,
            "confidence": float(confidence),
            "timestamp_ms": timestamp_ms if timestamp_ms is not None else int(time.time() * 1000),
            "source": source,
        }
        if self.camera_id:
            payload["camera_id"] = self.camera_id

        t0 = time.perf_counter()
        try:
            resp = self._session.post(
                self.api_url,
                json=payload,
                timeout=self.timeout_s,
                headers={"Content-Type": "application/json"},
            )
            rtt_ms = (time.perf_counter() - t0) * 1000.0

            if resp.status_code >= 500:
                log.warning(
                    "Attendance ingest server error %s: %s",
                    resp.status_code,
                    resp.text[:200],
                )
                return AttendanceIngestionResult(
                    outcome=AttendanceIngestOutcome.SERVER_ERROR,
                    sent=False,
                    rtt_ms=rtt_ms,
                    http_status=resp.status_code,
                    detail=resp.text[:200],
                    disposition="server_error",
                )

            if resp.status_code >= 400:
                log.warning(
                    "Attendance ingest client error %s: %s",
                    resp.status_code,
                    resp.text[:200],
                )
                return AttendanceIngestionResult(
                    outcome=AttendanceIngestOutcome.CLIENT_ERROR,
                    sent=False,
                    rtt_ms=rtt_ms,
                    http_status=resp.status_code,
                    detail=resp.text[:200],
                    disposition="client_error",
                )

            data = resp.json()
            accepted = bool(data.get("accepted"))
            disposition = data.get("disposition")
            detail = data.get("detail")
            from_state = data.get("from_state")
            to_state = data.get("to_state")
            lecture_id = data.get("lecture_id")

            log.info(
                "Attendance event sent identity=%s accepted=%s disposition=%s rtt=%.0fms",
                identity,
                accepted,
                disposition,
                rtt_ms,
            )

            return AttendanceIngestionResult(
                outcome=AttendanceIngestOutcome.SENT,
                sent=True,
                accepted=accepted,
                disposition=disposition,
                detail=detail,
                rtt_ms=rtt_ms,
                http_status=resp.status_code,
                from_state=from_state,
                to_state=to_state,
                lecture_id=str(lecture_id) if lecture_id else None,
            )

        except requests.exceptions.Timeout:
            rtt_ms = (time.perf_counter() - t0) * 1000.0
            log.warning("Attendance ingest timeout (%.1fs)", self.timeout_s)
            return AttendanceIngestionResult(
                outcome=AttendanceIngestOutcome.TIMEOUT,
                sent=False,
                rtt_ms=rtt_ms,
                disposition="timeout",
                detail=f"timeout after {self.timeout_s}s",
            )
        except requests.exceptions.ConnectionError as exc:
            rtt_ms = (time.perf_counter() - t0) * 1000.0
            log.warning("Attendance ingest connection error: %s", exc)
            return AttendanceIngestionResult(
                outcome=AttendanceIngestOutcome.CONNECTION_ERROR,
                sent=False,
                rtt_ms=rtt_ms,
                disposition="connection_error",
                detail=str(exc)[:200],
            )
        except Exception as exc:  # noqa: BLE001
            rtt_ms = (time.perf_counter() - t0) * 1000.0
            log.warning("Attendance ingest unexpected error: %s", exc)
            return AttendanceIngestionResult(
                outcome=AttendanceIngestOutcome.SERVER_ERROR,
                sent=False,
                rtt_ms=rtt_ms,
                disposition="error",
                detail=str(exc)[:200],
            )

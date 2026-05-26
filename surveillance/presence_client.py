"""SurveillancePresenceClient — laptop → cloud presence events (D3 Track 4)."""

from __future__ import annotations

import logging
import os
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Optional

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger("surveillance.presence_client")


class PresenceEmitOutcome(Enum):
    SENT = "sent"
    DISABLED = "disabled"
    SKIPPED = "skipped"
    TIMEOUT = "timeout"
    CONNECTION_ERROR = "connection_error"
    SERVER_ERROR = "server_error"
    CLIENT_ERROR = "client_error"


@dataclass
class PresenceEmitResult:
    outcome: PresenceEmitOutcome
    sent: bool = False
    accepted: Optional[bool] = None
    detail: Optional[str] = None
    rtt_ms: float = 0.0
    http_status: Optional[int] = None


def resolve_presence_api_url() -> str:
    explicit = os.environ.get("SURVEILLANCE_PRESENCE_API_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    cloud_base = os.environ.get("CLOUD_SERVER_URL", "http://localhost:8000").rstrip("/")
    return f"{cloud_base}/presence/events"


class SurveillancePresenceClient:
    """HTTP client for surveillance presence events. Never raises."""

    def __init__(
        self,
        *,
        enabled: bool,
        api_url: str,
        camera_id: str,
        timeout_s: float = 1.0,
        batch_size: int = 0,
    ) -> None:
        self.enabled = enabled and bool(api_url)
        self.api_url = api_url.rstrip("/") if api_url else ""
        self.camera_id = camera_id.strip() or "surveillance-laptop-01"
        self.timeout_s = timeout_s
        self.batch_size = max(0, int(batch_size))

        self._lock = threading.Lock()
        self._pending: list[dict[str, Any]] = []

        self._session = requests.Session()
        retry = Retry(total=0, connect=0, read=0, redirect=0, backoff_factor=0)
        adapter = HTTPAdapter(max_retries=retry)
        self._session.mount("http://", adapter)
        self._session.mount("https://", adapter)

        if self.enabled:
            log.info(
                "SurveillancePresenceClient → %s camera_id=%r timeout=%.1fs batch=%d",
                self.api_url,
                self.camera_id,
                self.timeout_s,
                self.batch_size,
            )
        else:
            log.info("SurveillancePresenceClient disabled")

    def emit(
        self,
        *,
        track_id: int,
        event: str,
        occupancy: int,
        timestamp_ms: Optional[int] = None,
    ) -> PresenceEmitResult:
        """Queue or send one presence event. Never raises."""
        if not self.enabled:
            return PresenceEmitResult(outcome=PresenceEmitOutcome.DISABLED)

        payload: dict[str, Any] = {
            "camera_id": self.camera_id,
            "track_id": int(track_id),
            "event": event,
            "timestamp_ms": timestamp_ms if timestamp_ms is not None else int(time.time() * 1000),
            "occupancy": max(0, int(occupancy)),
        }

        if self.batch_size <= 1:
            return self._post_one(payload)

        with self._lock:
            self._pending.append(payload)
            if len(self._pending) < self.batch_size:
                return PresenceEmitResult(outcome=PresenceEmitOutcome.SKIPPED, detail="batched")
            batch = self._pending
            self._pending = []

        return self._post_batch(batch)

    def flush(self) -> None:
        """Send any batched events. Never raises."""
        if not self.enabled or self.batch_size <= 1:
            return
        with self._lock:
            if not self._pending:
                return
            batch = self._pending
            self._pending = []
        self._post_batch(batch)

    def _post_batch(self, batch: list[dict[str, Any]]) -> PresenceEmitResult:
        last = PresenceEmitResult(outcome=PresenceEmitOutcome.SKIPPED)
        for payload in batch:
            last = self._post_one(payload)
        return last

    def _post_one(self, payload: dict[str, Any]) -> PresenceEmitResult:
        t0 = time.perf_counter()
        try:
            with self._lock:
                resp = self._session.post(
                    self.api_url,
                    json=payload,
                    timeout=self.timeout_s,
                    headers={"Content-Type": "application/json"},
                )
            rtt_ms = (time.perf_counter() - t0) * 1000.0

            if resp.status_code >= 500:
                log.warning("Presence POST server error %s: %s", resp.status_code, resp.text[:200])
                return PresenceEmitResult(
                    outcome=PresenceEmitOutcome.SERVER_ERROR,
                    sent=False,
                    rtt_ms=rtt_ms,
                    http_status=resp.status_code,
                    detail=resp.text[:200],
                )

            if resp.status_code >= 400:
                log.warning("Presence POST client error %s: %s", resp.status_code, resp.text[:200])
                return PresenceEmitResult(
                    outcome=PresenceEmitOutcome.CLIENT_ERROR,
                    sent=False,
                    rtt_ms=rtt_ms,
                    http_status=resp.status_code,
                    detail=resp.text[:200],
                )

            data = resp.json()
            accepted = bool(data.get("accepted", True))
            log.debug(
                "Presence event sent track_id=%s event=%s accepted=%s rtt=%.0fms",
                payload.get("track_id"),
                payload.get("event"),
                accepted,
                rtt_ms,
            )
            return PresenceEmitResult(
                outcome=PresenceEmitOutcome.SENT,
                sent=True,
                accepted=accepted,
                rtt_ms=rtt_ms,
                http_status=resp.status_code,
            )

        except requests.exceptions.Timeout:
            rtt_ms = (time.perf_counter() - t0) * 1000.0
            log.warning("Presence POST timeout (%.1fs)", self.timeout_s)
            return PresenceEmitResult(
                outcome=PresenceEmitOutcome.TIMEOUT,
                sent=False,
                rtt_ms=rtt_ms,
                detail=f"timeout after {self.timeout_s}s",
            )
        except requests.exceptions.ConnectionError as exc:
            rtt_ms = (time.perf_counter() - t0) * 1000.0
            log.warning("Presence POST connection error: %s", exc)
            return PresenceEmitResult(
                outcome=PresenceEmitOutcome.CONNECTION_ERROR,
                sent=False,
                rtt_ms=rtt_ms,
                detail=str(exc)[:200],
            )
        except Exception as exc:  # noqa: BLE001
            rtt_ms = (time.perf_counter() - t0) * 1000.0
            log.warning("Presence POST unexpected error: %s", exc)
            return PresenceEmitResult(
                outcome=PresenceEmitOutcome.SERVER_ERROR,
                sent=False,
                rtt_ms=rtt_ms,
                detail=str(exc)[:200],
            )

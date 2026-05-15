"""
CloudVerificationClient — Edge-side transport layer (Phase C2, corrected)

Responsibilities:
  - JPEG-encode the aligned face crop (measured separately for telemetry)
  - HTTP multipart upload to ArcFace verification server
  - Timeout handling (Pi must NEVER block indefinitely)
  - Graceful fallback when cloud is unavailable
  - Per-request latency telemetry (encode_ms, rtt_ms)
  - Retry with exponential backoff (configurable)
  - Connection health / circuit breaker

CRITICAL:
  This client sends a JPEG face crop image, NOT a MobileFaceNet embedding.
  ArcFace extraction happens server-side only.
  No embedding is serialised or transmitted here.

This runs ON the Raspberry Pi.
Must remain lightweight and non-blocking in the pipeline critical path.
"""

import io
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

import cv2
import numpy as np
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

log = logging.getLogger("cloud_client")


# ── Result types ──────────────────────────────────────────────────────────────

class OffloadOutcome(Enum):
    SUCCESS          = "success"
    TIMEOUT          = "timeout"
    CONNECTION_ERROR = "connection_error"
    SERVER_ERROR     = "server_error"
    ENCODE_FAILED    = "encode_failed"
    FALLBACK         = "fallback"


@dataclass
class CloudVerificationResult:
    outcome: OffloadOutcome

    # Populated on SUCCESS
    identity: Optional[str]          = None
    arcface_confidence: float        = 0.0
    verified: bool                   = False
    edge_cloud_agree: Optional[bool] = None

    # Per-stage server latency (ms) — from VerificationResponse
    server_image_decode_ms:    float = 0.0
    server_arcface_extract_ms: float = 0.0
    server_gallery_search_ms:  float = 0.0
    server_total_ms:           float = 0.0

    # Edge-side timing
    jpeg_encode_ms:   float = 0.0    # time to JPEG-encode the face crop
    image_size_bytes: int   = 0      # compressed payload size (bandwidth telemetry)
    rtt_ms:           float = 0.0    # full round-trip time measured on edge

    attempt_count: int               = 1
    error_detail: Optional[str]      = None

    @property
    def succeeded(self) -> bool:
        return self.outcome == OffloadOutcome.SUCCESS

    def to_telemetry_dict(self) -> dict:
        return {
            "offload_outcome":                self.outcome.value,
            "offload_identity":               self.identity,
            "offload_arcface_confidence":     self.arcface_confidence,
            "offload_verified":               self.verified,
            "offload_edge_cloud_agree":       self.edge_cloud_agree,
            "offload_jpeg_encode_ms":         self.jpeg_encode_ms,
            "offload_image_size_bytes":       self.image_size_bytes,
            "offload_rtt_ms":                 self.rtt_ms,
            "offload_server_decode_ms":       self.server_image_decode_ms,
            "offload_server_arcface_ms":      self.server_arcface_extract_ms,
            "offload_server_gallery_ms":      self.server_gallery_search_ms,
            "offload_server_total_ms":        self.server_total_ms,
            "offload_attempt_count":          self.attempt_count,
            "offload_error":                  self.error_detail,
        }


# ── Client ────────────────────────────────────────────────────────────────────

class CloudVerificationClient:
    """
    Lightweight HTTP client for edge → cloud image-based offload.

    Usage (in pipeline):
        client = CloudVerificationClient(server_url="http://192.168.1.100:8000")
        result = client.verify(
            face_crop_bgr=aligned_face_bgr,     # numpy uint8 BGR, ~112x112
            edge_confidence=0.58,
            edge_candidate="student_042",
            session_id=session_id,
            frame_id=frame_counter,
        )
        if result.succeeded:
            identity = result.identity
        else:
            identity = local_candidate          # fallback to edge decision
    """

    def __init__(
        self,
        server_url: str,
        timeout_s: float = 2.0,             # hard timeout — keep pipeline moving
        jpeg_quality: int = 85,             # JPEG quality: balance size vs accuracy
        max_retries: int = 1,
        retry_backoff: float = 0.5,
        health_check_interval_s: float = 30.0,
    ):
        self.server_url             = server_url.rstrip("/")
        self.timeout_s              = timeout_s
        self.jpeg_quality           = jpeg_quality
        self.max_retries            = max_retries
        self._health_check_interval_s = health_check_interval_s

        # Connection health tracking
        self._consecutive_failures  = 0
        self._last_health_check_ts: float = 0.0
        self._server_reachable: bool = True
        self._total_requests        = 0
        self._total_failures        = 0

        self._session = self._build_session(max_retries, retry_backoff)

        log.info(
            f"CloudVerificationClient → {self.server_url} "
            f"(timeout={timeout_s}s jpeg_quality={jpeg_quality})"
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def verify(
        self,
        face_crop_bgr: np.ndarray,
        edge_confidence: float,
        session_id: str,
        frame_id: int,
        edge_candidate: Optional[str] = None,
        track_id: Optional[int] = None,
        routing_strategy: str = "unknown",
    ) -> CloudVerificationResult:
        """
        Offload verification to cloud ArcFace server.

        Sends a JPEG-encoded aligned face crop via multipart/form-data.
        ArcFace embedding extraction happens server-side only.
        Never serialises or transmits MobileFaceNet embeddings.

        Args:
            face_crop_bgr:    BGR uint8 numpy array — aligned face crop from edge detector.
                              Should be the same crop that MobileFaceNet ran on.
            edge_confidence:  MobileFaceNet similarity score that triggered this offload.
            session_id:       Experiment session identifier.
            frame_id:         Frame counter from edge pipeline.
            edge_candidate:   Edge's best-guess identity label (or None).
            track_id:         Tracker ID if available.
            routing_strategy: Name of the routing strategy that made this decision.

        Returns:
            CloudVerificationResult — always returns, never raises.
        """
        # ── Circuit breaker ────────────────────────────────────────────────────
        if self._should_skip_due_to_failures():
            log.debug("Circuit breaker: skipping cloud offload (consecutive failures)")
            return CloudVerificationResult(
                outcome=OffloadOutcome.FALLBACK,
                error_detail="circuit_breaker_open",
            )

        # ── Stage 1: JPEG encode (measured on edge) ────────────────────────────
        t_encode_start = time.perf_counter()
        jpeg_bytes, encode_ok = self._jpeg_encode(face_crop_bgr)
        jpeg_encode_ms = (time.perf_counter() - t_encode_start) * 1000

        if not encode_ok or jpeg_bytes is None:
            log.error("JPEG encode failed — cannot offload this frame")
            return CloudVerificationResult(
                outcome=OffloadOutcome.ENCODE_FAILED,
                jpeg_encode_ms=round(jpeg_encode_ms, 2),
                error_detail="cv2.imencode failed",
            )

        image_size_bytes = len(jpeg_bytes)

        # ── Stage 2: Metadata payload ──────────────────────────────────────────
        metadata = json.dumps({
            "session_id":       session_id,
            "frame_id":         frame_id,
            "track_id":         track_id,
            "edge_confidence":  edge_confidence,
            "edge_candidate":   edge_candidate,
            "routing_strategy": routing_strategy,
            "timestamp_edge_ms": int(time.time() * 1000),
        })

        # ── Stage 3: HTTP upload (RTT measured on edge) ────────────────────────
        t_rtt_start = time.perf_counter()
        result = self._post_with_retry(jpeg_bytes, metadata)
        result.rtt_ms           = round((time.perf_counter() - t_rtt_start) * 1000, 2)
        result.jpeg_encode_ms   = round(jpeg_encode_ms, 2)
        result.image_size_bytes = image_size_bytes

        self._update_health_tracking(result)
        self._total_requests += 1

        return result

    def is_healthy(self) -> bool:
        return self._server_reachable and self._consecutive_failures < 5

    def health_check(self) -> bool:
        try:
            resp = self._session.get(f"{self.server_url}/health", timeout=1.0)
            self._server_reachable = (resp.status_code == 200)
            self._last_health_check_ts = time.time()
            return self._server_reachable
        except Exception:
            self._server_reachable = False
            return False

    def stats(self) -> dict:
        return {
            "server_url":            self.server_url,
            "jpeg_quality":          self.jpeg_quality,
            "total_requests":        self._total_requests,
            "total_failures":        self._total_failures,
            "consecutive_failures":  self._consecutive_failures,
            "server_reachable":      self._server_reachable,
            "failure_rate":          self._total_failures / max(self._total_requests, 1),
        }

    # ── Internal ──────────────────────────────────────────────────────────────

    def _jpeg_encode(self, face_crop_bgr: np.ndarray):
        """
        JPEG-encode a BGR face crop.
        Returns (bytes, success_bool).
        Measured separately so encode latency is visible in telemetry.
        """
        try:
            encode_params = [cv2.IMWRITE_JPEG_QUALITY, self.jpeg_quality]
            ok, buf = cv2.imencode(".jpg", face_crop_bgr, encode_params)
            if not ok:
                return None, False
            return buf.tobytes(), True
        except Exception as e:
            log.error(f"JPEG encode exception: {e}")
            return None, False

    def _post_with_retry(self, jpeg_bytes: bytes, metadata: str) -> CloudVerificationResult:
        last_error = None

        for attempt in range(1, self.max_retries + 2):
            try:
                files   = {"image": ("face_crop.jpg", jpeg_bytes, "image/jpeg")}
                data    = {"metadata": metadata}

                resp = self._session.post(
                    f"{self.server_url}/verify/image",
                    files=files,
                    data=data,
                    timeout=self.timeout_s,
                )

                if resp.status_code == 200:
                    d = resp.json()
                    return CloudVerificationResult(
                        outcome=OffloadOutcome.SUCCESS,
                        identity=d.get("identity"),
                        arcface_confidence=d.get("arcface_confidence", 0.0),
                        verified=d.get("verified", False),
                        edge_cloud_agree=d.get("edge_cloud_agree"),
                        server_image_decode_ms=d.get("image_decode_ms", 0.0),
                        server_arcface_extract_ms=d.get("arcface_extract_ms", 0.0),
                        server_gallery_search_ms=d.get("gallery_search_ms", 0.0),
                        server_total_ms=d.get("server_total_ms", 0.0),
                        attempt_count=attempt,
                    )
                else:
                    last_error = f"HTTP {resp.status_code}"
                    log.warning(f"Cloud returned {resp.status_code} (attempt {attempt})")

            except requests.exceptions.Timeout:
                last_error = "timeout"
                log.warning(f"Cloud request timed out after {self.timeout_s}s (attempt {attempt})")
                return CloudVerificationResult(
                    outcome=OffloadOutcome.TIMEOUT,
                    error_detail=f"timeout>{self.timeout_s}s",
                    attempt_count=attempt,
                )

            except requests.exceptions.ConnectionError as e:
                last_error = f"connection_error: {e}"
                log.warning(f"Cloud connection error (attempt {attempt}): {e}")

            except Exception as e:
                last_error = f"unexpected: {e}"
                log.error(f"Unexpected cloud client error (attempt {attempt}): {e}")

        outcome = (
            OffloadOutcome.CONNECTION_ERROR
            if "connection" in (last_error or "").lower()
            else OffloadOutcome.SERVER_ERROR
        )
        return CloudVerificationResult(
            outcome=outcome,
            error_detail=last_error,
            attempt_count=self.max_retries + 1,
        )

    def _should_skip_due_to_failures(self) -> bool:
        OPEN_THRESHOLD = 5
        RESET_AFTER_S  = 30.0
        if self._consecutive_failures < OPEN_THRESHOLD:
            return False
        if (time.time() - self._last_health_check_ts) > RESET_AFTER_S:
            log.info("Circuit breaker: attempting reset after backoff window")
            self._consecutive_failures = 0
            return False
        return True

    def _update_health_tracking(self, result: CloudVerificationResult):
        if result.succeeded:
            self._consecutive_failures = 0
            self._server_reachable = True
        else:
            self._consecutive_failures += 1
            self._total_failures += 1
            if self._consecutive_failures >= 3:
                self._server_reachable = False

    @staticmethod
    def _build_session(max_retries: int, backoff: float) -> requests.Session:
        session = requests.Session()
        retry = Retry(
            total=max_retries,
            backoff_factor=backoff,
            status_forcelist=[500, 502, 503, 504],
            allowed_methods=["POST"],
        )
        adapter = HTTPAdapter(max_retries=retry)
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        return session

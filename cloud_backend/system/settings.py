"""Centralized environment profiles and tunables (D5 Track 5)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
_ENV_DIR = _REPO_ROOT / "deployment" / "env"
_VALID_PROFILES = frozenset({"development", "demo", "production"})


def _parse_bool(raw: str | None, default: bool) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() not in ("0", "false", "no", "off")


def _parse_float(raw: str | None, default: float) -> float:
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _parse_int(raw: str | None, default: int) -> int:
    if raw is None:
        return default
    try:
        return int(float(raw))
    except ValueError:
        return default


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def load_settings(profile: str | None = None) -> None:
    """Load deployment env profile; explicit env vars always win."""
    selected = (profile or os.environ.get("HYBRID_PROFILE", "development")).strip().lower()
    if selected not in _VALID_PROFILES:
        selected = "development"
    os.environ.setdefault("HYBRID_PROFILE", selected)
    _load_env_file(_ENV_DIR / f"{selected}.env")
    _load_env_file(_ENV_DIR / "local.env")
    get_settings.cache_clear()


@dataclass(frozen=True)
class AttendanceTunables:
    eligibility_threshold: float = 0.80
    presence_session_timeout_s: int = 45
    evidence_presence_camera_fallback: bool = True
    evidence_surveillance_camera_ids: str = ""
    evidence_temporal_window_sec: int = 300
    surveillance_presence_enabled: bool = True
    surveillance_presence_timeout_s: float = 1.0
    surveillance_heartbeat_s: int = 30
    cloud_server_url: str = "http://localhost:8000"


@dataclass(frozen=True)
class SystemSettings:
    profile: str = "development"
    log_level: str = "INFO"
    verbose_http: bool = False
    attendance: AttendanceTunables = field(default_factory=AttendanceTunables)

    def safe_summary(self) -> dict:
        """Non-secret values for GET /system/config."""
        att = self.attendance
        return {
            "profile": self.profile,
            "log_level": self.log_level,
            "verbose_http": self.verbose_http,
            "attendance": {
                "eligibility_threshold": att.eligibility_threshold,
                "presence_session_timeout_s": att.presence_session_timeout_s,
                "evidence_presence_camera_fallback": att.evidence_presence_camera_fallback,
                "evidence_surveillance_camera_ids": att.evidence_surveillance_camera_ids or None,
                "evidence_temporal_window_sec": att.evidence_temporal_window_sec,
                "surveillance_presence_enabled": att.surveillance_presence_enabled,
                "surveillance_presence_timeout_s": att.surveillance_presence_timeout_s,
                "surveillance_heartbeat_s": att.surveillance_heartbeat_s,
                "cloud_server_url": att.cloud_server_url,
            },
            "features": {
                "recognition_ingest": True,
                "presence_ingest": True,
                "evidence_pipeline": True,
                "eligibility_pipeline": True,
                "decision_pipeline": True,
                "finalization_pipeline": True,
                "report_pipeline": True,
            },
        }


@lru_cache
def get_settings() -> SystemSettings:
    profile = os.environ.get("HYBRID_PROFILE", "development").strip().lower()
    if profile not in _VALID_PROFILES:
        profile = "development"

    attendance = AttendanceTunables(
        eligibility_threshold=_parse_float(
            os.environ.get("ATTENDANCE_ELIGIBILITY_THRESHOLD"), 0.80
        ),
        presence_session_timeout_s=_parse_int(
            os.environ.get("PRESENCE_SESSION_TIMEOUT_S"), 45
        ),
        evidence_presence_camera_fallback=_parse_bool(
            os.environ.get("EVIDENCE_PRESENCE_CAMERA_FALLBACK"), True
        ),
        evidence_surveillance_camera_ids=os.environ.get(
            "EVIDENCE_SURVEILLANCE_CAMERA_IDS", ""
        ).strip(),
        evidence_temporal_window_sec=_parse_int(
            os.environ.get("EVIDENCE_TEMPORAL_WINDOW_SEC"), 300
        ),
        surveillance_presence_enabled=_parse_bool(
            os.environ.get("SURVEILLANCE_PRESENCE_ENABLED"), True
        ),
        surveillance_presence_timeout_s=_parse_float(
            os.environ.get("SURVEILLANCE_PRESENCE_TIMEOUT_S"), 45.0
        ),
        surveillance_heartbeat_s=_parse_int(os.environ.get("SURVEILLANCE_HEARTBEAT_S"), 5),
        cloud_server_url=os.environ.get("CLOUD_SERVER_URL", "http://localhost:8000").rstrip(
            "/"
        ),
    )
    return SystemSettings(
        profile=profile,
        log_level=os.environ.get("HYBRID_LOG_LEVEL", "INFO").upper(),
        verbose_http=_parse_bool(os.environ.get("HYBRID_VERBOSE_HTTP"), False),
        attendance=attendance,
    )

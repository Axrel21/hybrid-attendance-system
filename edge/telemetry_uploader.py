# edge/telemetry_uploader.py
"""Post-session telemetry uploader: edge experiment session → cloud backend.

Design rules
------------
* **Runs as a separate process.** ``edge/main.py`` does not import this
  module. The Pi continues to operate offline regardless of whether the
  uploader is ever started; the per-run CSVs under
  ``experiments/exp_<id>/`` are the durable buffer.
* **One session at a time.** A session is a directory matching the
  layout created by :func:`config.experiment_session.init_experiment_session`.
* **Idempotent.** Re-uploading a session overwrites its cloud-side
  metadata + summary atomically; events are append-only on disk, so
  re-running ``--ingest`` appends duplicates — use ``--no-ingest`` if you
  only want to refresh metadata.

CLI examples
------------
::

    # Upload a single completed session
    python -m edge.telemetry_uploader \\
        --session experiments/exp_20260516_120000/ \\
        --cloud http://cloud:8000

    # Replay every session under experiments/ once
    python -m edge.telemetry_uploader \\
        --replay experiments/ \\
        --cloud http://cloud:8000

    # Dry run (no HTTP calls)
    python -m edge.telemetry_uploader \\
        --session experiments/exp_20260516_120000/ \\
        --cloud http://cloud:8000 --dry-run
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import socket
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional

# requests is already pinned in edge/requirements-edge.txt
import requests

# shared/ ships with the Pi bundle; falling back to hardcoded paths if the
# package somehow isn't on sys.path keeps the uploader robust on stripped
# deployments. Both branches keep the wire format identical.
try:
    from shared.contracts import (
        DEFAULT_TELEMETRY_BATCH_SIZE,
        DEFAULT_TELEMETRY_MAX_RETRIES,
        DEFAULT_TELEMETRY_RETRY_BACKOFF_S,
        DEFAULT_TIMEOUT_S,
        TELEMETRY_INGEST_PATH,
        TELEMETRY_SESSION_END_PATH,
        TELEMETRY_SESSION_START_PATH,
    )
except Exception:  # noqa: BLE001
    DEFAULT_TELEMETRY_BATCH_SIZE = 64
    DEFAULT_TELEMETRY_MAX_RETRIES = 3
    DEFAULT_TELEMETRY_RETRY_BACKOFF_S = 5.0
    DEFAULT_TIMEOUT_S = 2.0
    TELEMETRY_INGEST_PATH = "/telemetry/ingest"
    TELEMETRY_SESSION_END_PATH = "/telemetry/sessions/end"
    TELEMETRY_SESSION_START_PATH = "/telemetry/sessions/start"


log = logging.getLogger("edge.telemetry_uploader")


# ── Session resolution ────────────────────────────────────────────────────────

@dataclass
class SessionPaths:
    """Minimal projection of an experiments/exp_<id>/ directory."""

    root: Path
    session_id: str
    settings_snapshot: Path
    telemetry_csv: Path
    diagnostic_csv: Path
    attendance_csv: Path
    summary_dir: Path

    @classmethod
    def from_dir(cls, root: Path) -> "SessionPaths":
        root = Path(root).resolve()
        session_id = root.name  # "exp_YYYYMMDD_HHMMSS"
        return cls(
            root=root,
            session_id=session_id,
            settings_snapshot=root / "config" / "settings_snapshot.json",
            telemetry_csv=root / "telemetry" / "telemetry_log.csv",
            diagnostic_csv=root / "diagnostics" / "diagnostic_log.csv",
            attendance_csv=root / "diagnostics" / "attendance_log.csv",
            summary_dir=root / "summaries",
        )


# ── Cloud client (separate from edge.cloud_client; that one is verify-only) ───

class _CloudTelemetryClient:
    """Thin HTTP client for the telemetry endpoints."""

    def __init__(
        self,
        base_url: str,
        timeout_s: float = DEFAULT_TIMEOUT_S * 5,  # ingest can be heavier than verify
        max_retries: int = DEFAULT_TELEMETRY_MAX_RETRIES,
        backoff_s: float = DEFAULT_TELEMETRY_RETRY_BACKOFF_S,
        dry_run: bool = False,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self.max_retries = max_retries
        self.backoff_s = backoff_s
        self.dry_run = dry_run
        self._session = requests.Session()

    def session_start(self, payload: Dict[str, Any]) -> bool:
        return self._post(TELEMETRY_SESSION_START_PATH, payload)

    def session_end(self, payload: Dict[str, Any]) -> bool:
        return self._post(TELEMETRY_SESSION_END_PATH, payload)

    def ingest_batch(self, payload: Dict[str, Any]) -> bool:
        return self._post(TELEMETRY_INGEST_PATH, payload)

    def _post(self, path: str, payload: Dict[str, Any]) -> bool:
        url = f"{self.base_url}{path}"
        if self.dry_run:
            log.info("[dry-run] POST %s (payload_keys=%s)", url, list(payload.keys()))
            return True

        for attempt in range(1, self.max_retries + 1):
            try:
                resp = self._session.post(url, json=payload, timeout=self.timeout_s)
                if resp.status_code < 400:
                    return True
                log.warning(
                    "POST %s -> %d (attempt %d/%d): %s",
                    url, resp.status_code, attempt, self.max_retries,
                    resp.text[:200],
                )
                # Don't retry on client errors (4xx).
                if 400 <= resp.status_code < 500:
                    return False
            except requests.RequestException as exc:
                log.warning(
                    "POST %s exception (attempt %d/%d): %s",
                    url, attempt, self.max_retries, exc,
                )
            time.sleep(self.backoff_s * attempt)
        return False


# ── Event projection from CSV → telemetry event dicts ─────────────────────────

def _csv_rows(path: Path) -> Iterator[Dict[str, str]]:
    if not path.exists() or path.stat().st_size == 0:
        return
    with open(path, "r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            yield row


def _to_event(
    event_type: str,
    session_id: str,
    experiment_label: str,
    row: Dict[str, str],
) -> Dict[str, Any]:
    # Try to pull the producer-side timestamp; fall back to time.time().
    ts_str = row.get("timestamp") or row.get("timestamp_edge_ms")
    ts_ms: int
    if ts_str:
        try:
            ts_f = float(ts_str)
            ts_ms = int(ts_f * 1000) if ts_f < 1e12 else int(ts_f)
        except ValueError:
            ts_ms = int(time.time() * 1000)
    else:
        ts_ms = int(time.time() * 1000)

    frame_id_raw = row.get("frame_idx") or row.get("frame_id")
    track_id_raw = row.get("track_id")
    return {
        "event_type": event_type,
        "timestamp_ms": ts_ms,
        "session_id": session_id,
        "experiment_label": experiment_label,
        "frame_id": _to_int(frame_id_raw),
        "track_id": _to_int(track_id_raw),
        "fields": {k: v for k, v in row.items() if v != ""},
    }


def _to_int(v: Optional[str]) -> Optional[int]:
    if v in (None, ""):
        return None
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


# ── Metadata + summary builders ───────────────────────────────────────────────

def _read_settings_snapshot(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Failed to read %s: %s", path, exc)
        return {}


def build_session_start(paths: SessionPaths, experiment_label: str) -> Dict[str, Any]:
    snapshot = _read_settings_snapshot(paths.settings_snapshot)
    settings_mod = snapshot.get("settings_module", {})
    thresholds = {
        k: v for k, v in settings_mod.items()
        if k.startswith("MATCH_") or k.startswith("ORIENTATION_") or k in (
            "K_FOCAL", "MIN_DISTANCE", "MAX_DISTANCE",
            "CLOUD_THRESHOLD", "TARGET_LATENCY_MS",
        )
    }
    environment = snapshot.get("runtime_env_overrides", {})
    return {
        "session_id": paths.session_id,
        "started_at": _started_at_from_session_id(paths.session_id),
        "experiment_label": experiment_label,
        "device_id": os.environ.get("DEVICE_ID") or socket.gethostname(),
        "hostname": socket.gethostname(),
        "camera_backend": settings_mod.get("CAMERA_BACKEND"),
        "headless": _truthy(settings_mod.get("HEADLESS")),
        "simulate_pi": _truthy(settings_mod.get("SIMULATE_PI")),
        "thresholds": thresholds,
        "hardware": {
            "platform": sys.platform,
            "python": sys.version.split()[0],
        },
        "environment": environment,
        "notes": os.environ.get("UPLOADER_NOTES"),
    }


def build_session_summary(paths: SessionPaths, experiment_label: str) -> Dict[str, Any]:
    """Cheap summary computed from the CSVs without pandas.

    The cloud is free to recompute richer summaries from the raw events
    after ingest.
    """
    summary: Dict[str, Any] = {
        "session_id": paths.session_id,
        "experiment_label": experiment_label,
        "frames_total": 0,
        "matched_total": 0,
        "spoof_total": 0,
        "offload_total": 0,
        "offload_success_total": 0,
    }
    for row in _csv_rows(paths.diagnostic_csv):
        summary["frames_total"] += 1
        if row.get("decision") == "MATCHED":
            summary["matched_total"] += 1
        if row.get("lbl") == "SPOOF":
            summary["spoof_total"] += 1
        if row.get("decision") == "OFFLOAD_TO_CLOUD":
            summary["offload_total"] += 1
        if row.get("cloud_outcome") == "success":
            summary["offload_success_total"] += 1
    return summary


# ── Main upload pipeline ──────────────────────────────────────────────────────

class SessionUploader:
    """Upload one experiment session's metadata + telemetry to the cloud."""

    def __init__(
        self,
        paths: SessionPaths,
        client: _CloudTelemetryClient,
        experiment_label: str = "",
        batch_size: int = DEFAULT_TELEMETRY_BATCH_SIZE,
        include_diagnostic: bool = True,
        include_telemetry: bool = True,
        include_attendance: bool = True,
    ) -> None:
        self.paths = paths
        self.client = client
        self.experiment_label = experiment_label
        self.batch_size = max(1, batch_size)
        self.include_diagnostic = include_diagnostic
        self.include_telemetry = include_telemetry
        self.include_attendance = include_attendance

    def run(self, do_ingest: bool = True, do_finalize: bool = True) -> Dict[str, int]:
        stats = {"started": 0, "events": 0, "batches": 0, "ended": 0, "failures": 0}

        if not self.paths.root.exists():
            raise FileNotFoundError(f"Session directory not found: {self.paths.root}")

        # 1. session_start
        start_payload = build_session_start(self.paths, self.experiment_label)
        if not self.client.session_start(start_payload):
            stats["failures"] += 1
            log.warning("session_start failed for %s — continuing", self.paths.session_id)
        else:
            stats["started"] = 1

        # 2. ingest events
        if do_ingest:
            for batch in self._iter_batches():
                ok = self.client.ingest_batch({
                    "session_id": self.paths.session_id,
                    "events": batch,
                })
                if ok:
                    stats["batches"] += 1
                    stats["events"] += len(batch)
                else:
                    stats["failures"] += 1

        # 3. session_end with summary
        if do_finalize:
            summary = build_session_summary(self.paths, self.experiment_label)
            end_payload = {
                "session_id": self.paths.session_id,
                "ended_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "summary": summary,
            }
            if self.client.session_end(end_payload):
                stats["ended"] = 1
            else:
                stats["failures"] += 1

        return stats

    def _iter_batches(self) -> Iterator[List[Dict[str, Any]]]:
        feeds: List[Iterable[Dict[str, Any]]] = []
        if self.include_diagnostic:
            feeds.append(
                _to_event("diagnostic", self.paths.session_id, self.experiment_label, row)
                for row in _csv_rows(self.paths.diagnostic_csv)
            )
        if self.include_telemetry:
            feeds.append(
                _to_event("frame_telemetry", self.paths.session_id, self.experiment_label, row)
                for row in _csv_rows(self.paths.telemetry_csv)
            )
        if self.include_attendance:
            feeds.append(
                _to_event("attendance", self.paths.session_id, self.experiment_label, row)
                for row in _csv_rows(self.paths.attendance_csv)
            )
        buf: List[Dict[str, Any]] = []
        for feed in feeds:
            for ev in feed:
                buf.append(ev)
                if len(buf) >= self.batch_size:
                    yield buf
                    buf = []
        if buf:
            yield buf


# ── Helpers ───────────────────────────────────────────────────────────────────

def _started_at_from_session_id(session_id: str) -> str:
    """Parse ``exp_YYYYMMDD_HHMMSS`` → ISO timestamp; best effort."""
    if not session_id.startswith("exp_"):
        return time.strftime("%Y-%m-%dT%H:%M:%S")
    raw = session_id[len("exp_"):]
    try:
        t = time.strptime(raw, "%Y%m%d_%H%M%S")
        return time.strftime("%Y-%m-%dT%H:%M:%S", t)
    except ValueError:
        return time.strftime("%Y-%m-%dT%H:%M:%S")


def _truthy(v: Any) -> Optional[bool]:
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    s = str(v).strip().lower()
    if s in ("1", "true", "yes", "y", "t"):
        return True
    if s in ("0", "false", "no", "n", "f", ""):
        return False
    return None


# ── CLI ───────────────────────────────────────────────────────────────────────

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--session", help="path to a single experiments/exp_<id>/ directory",
    )
    group.add_argument(
        "--replay", help="replay every experiments/exp_*/ directory under PATH",
    )

    parser.add_argument(
        "--cloud", required=True,
        help="cloud backend base URL (e.g. http://cloud:8000)",
    )
    parser.add_argument(
        "--label", default="", help="experiment_label tag (overrides snapshot)",
    )
    parser.add_argument(
        "--batch-size", type=int, default=DEFAULT_TELEMETRY_BATCH_SIZE,
        help="events per /telemetry/ingest batch (default: %(default)s)",
    )
    parser.add_argument(
        "--timeout", type=float, default=DEFAULT_TIMEOUT_S * 5,
        help="per-request HTTP timeout in seconds (default: %(default)s)",
    )
    parser.add_argument(
        "--no-diagnostic", action="store_true",
        help="skip diagnostic_log.csv events",
    )
    parser.add_argument(
        "--no-telemetry", action="store_true",
        help="skip telemetry_log.csv events",
    )
    parser.add_argument(
        "--no-attendance", action="store_true",
        help="skip attendance_log.csv events",
    )
    parser.add_argument(
        "--no-ingest", action="store_true",
        help="upload metadata+summary only (no /telemetry/ingest calls)",
    )
    parser.add_argument(
        "--no-finalize", action="store_true",
        help="skip the /telemetry/sessions/end call (e.g. when session is still live)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="print intended requests but do not POST",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true",
        help="enable DEBUG logging",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    client = _CloudTelemetryClient(
        base_url=args.cloud,
        timeout_s=args.timeout,
        dry_run=args.dry_run,
    )

    targets: List[Path]
    if args.session:
        targets = [Path(args.session)]
    else:
        root = Path(args.replay)
        if not root.exists():
            log.error("replay root not found: %s", root)
            return 2
        targets = sorted(p for p in root.iterdir() if p.is_dir() and p.name.startswith("exp_"))
        if not targets:
            log.warning("no exp_*/ directories under %s", root)
            return 0

    failures = 0
    for tgt in targets:
        paths = SessionPaths.from_dir(tgt)
        uploader = SessionUploader(
            paths=paths,
            client=client,
            experiment_label=args.label,
            batch_size=args.batch_size,
            include_diagnostic=not args.no_diagnostic,
            include_telemetry=not args.no_telemetry,
            include_attendance=not args.no_attendance,
        )
        try:
            stats = uploader.run(
                do_ingest=not args.no_ingest,
                do_finalize=not args.no_finalize,
            )
        except Exception:  # noqa: BLE001
            log.exception("Uploader failed for %s", tgt)
            failures += 1
            continue
        log.info("uploaded session=%s stats=%s", paths.session_id, stats)
        if stats["failures"]:
            failures += 1

    return 0 if failures == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())

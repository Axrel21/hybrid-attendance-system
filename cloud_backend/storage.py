# cloud_backend/storage.py
"""Filesystem-backed telemetry storage.

Layout under ``$CLOUD_STORAGE_DIR`` (default ``<repo>/cloud_storage/``):

    cloud_storage/
    ├── index.jsonl                       # one record per known session
    └── sessions/
        └── <session_id>/
            ├── metadata.json             # SessionStartRequest payload
            ├── summary.json              # SessionEndRequest payload (optional)
            └── events.jsonl              # TelemetryEvent stream (append-only)

Design rules:

* No database. JSON / JSONL on the local filesystem.
* Metadata is written via temp-file + atomic rename so partial writes do
  not corrupt the JSON. Event log is line-buffered append (durable under
  unclean shutdown — at most one truncated line is lost).
* Reads stream events lazily so large sessions don't eat memory.
* All locking is per-process via a single ``threading.Lock`` — the cloud
  backend is single-worker by design (mirrors ``cloud/main.py`` workers=1
  guidance because of the ArcFace ONNX session).

A future migration to SQLite / Postgres would reuse the same wire schema —
nothing in :mod:`shared.schemas` or :mod:`cloud_backend.schemas` depends on
the on-disk format.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

log = logging.getLogger("cloud_backend.storage")


def _repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _default_storage_dir() -> Path:
    override = os.environ.get("CLOUD_STORAGE_DIR")
    if override:
        return Path(override).expanduser().resolve()
    return _repo_root() / "cloud_storage"


@dataclass
class SessionRecord:
    """In-memory projection of a stored session for quick listing."""

    session_id: str
    experiment_label: str
    started_at: Optional[str]
    ended_at: Optional[str]
    event_count: int
    has_summary: bool
    storage_path: str


class TelemetryStorage:
    """Filesystem-backed session + event store.

    Thread-safe via a single re-entrant lock; the cloud backend is
    single-worker so contention is negligible. Falls back to creating
    parent dirs on demand.
    """

    INDEX_NAME = "index.jsonl"

    def __init__(self, root: Optional[Path] = None) -> None:
        self.root: Path = Path(root) if root else _default_storage_dir()
        self.sessions_dir: Path = self.root / "sessions"
        self.index_path: Path = self.root / self.INDEX_NAME
        self._lock = threading.RLock()
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        log.info("TelemetryStorage rooted at %s", self.root)

    # ── Path helpers ──────────────────────────────────────────────────────────

    def _session_dir(self, session_id: str) -> Path:
        # Reject empty / path-traversal session ids.
        if not session_id or "/" in session_id or session_id in (".", ".."):
            raise ValueError(f"Invalid session_id: {session_id!r}")
        return self.sessions_dir / session_id

    def _metadata_path(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "metadata.json"

    def _summary_path(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "summary.json"

    def _events_path(self, session_id: str) -> Path:
        return self._session_dir(session_id) / "events.jsonl"

    # ── Write paths ───────────────────────────────────────────────────────────

    def record_session_start(self, metadata: Dict[str, Any]) -> Path:
        session_id = metadata.get("session_id")
        if not session_id:
            raise ValueError("metadata['session_id'] required")
        with self._lock:
            sdir = self._session_dir(session_id)
            sdir.mkdir(parents=True, exist_ok=True)
            self._atomic_write_json(self._metadata_path(session_id), metadata)
            self._append_index({
                "session_id": session_id,
                "experiment_label": metadata.get("experiment_label", ""),
                "started_at": metadata.get("started_at"),
                "recorded_at": _now_iso(),
                "event": "session_start",
            })
            return sdir

    def record_session_end(self, payload: Dict[str, Any]) -> Path:
        session_id = payload.get("session_id")
        if not session_id:
            raise ValueError("payload['session_id'] required")
        with self._lock:
            sdir = self._session_dir(session_id)
            sdir.mkdir(parents=True, exist_ok=True)
            self._atomic_write_json(self._summary_path(session_id), payload)
            self._append_index({
                "session_id": session_id,
                "ended_at": payload.get("ended_at"),
                "recorded_at": _now_iso(),
                "event": "session_end",
            })
            return self._summary_path(session_id)

    def append_events(self, session_id: str, events: List[Dict[str, Any]]) -> int:
        if not events:
            return 0
        with self._lock:
            sdir = self._session_dir(session_id)
            sdir.mkdir(parents=True, exist_ok=True)
            path = self._events_path(session_id)
            with open(path, "a", encoding="utf-8") as fh:
                for ev in events:
                    fh.write(json.dumps(ev, separators=(",", ":"), default=str))
                    fh.write("\n")
            return len(events)

    # ── Read paths ────────────────────────────────────────────────────────────

    def list_sessions(self) -> List[SessionRecord]:
        if not self.sessions_dir.exists():
            return []
        out: List[SessionRecord] = []
        for sdir in sorted(self.sessions_dir.iterdir()):
            if not sdir.is_dir():
                continue
            try:
                out.append(self._read_session_record(sdir))
            except Exception as exc:  # noqa: BLE001
                log.warning("Skipping malformed session dir %s: %s", sdir, exc)
        return out

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        sdir = self._session_dir(session_id)
        if not sdir.exists():
            return None
        record = self._read_session_record(sdir)
        return {
            "session_id": record.session_id,
            "metadata": _safe_load_json(self._metadata_path(session_id)) or {},
            "summary": _safe_load_json(self._summary_path(session_id)),
            "event_count": record.event_count,
            "storage_path": str(sdir),
        }

    def iter_session_events(
        self,
        session_id: str,
        limit: Optional[int] = None,
        offset: int = 0,
    ) -> Iterator[Dict[str, Any]]:
        path = self._events_path(session_id)
        if not path.exists():
            return
        with open(path, "r", encoding="utf-8") as fh:
            for idx, line in enumerate(fh):
                if idx < offset:
                    continue
                if limit is not None and idx - offset >= limit:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    log.warning("Skipping malformed event line %d in %s", idx, path)

    def session_event_count(self, session_id: str) -> int:
        path = self._events_path(session_id)
        if not path.exists():
            return 0
        count = 0
        with open(path, "r", encoding="utf-8") as fh:
            for _ in fh:
                count += 1
        return count

    # ── Internals ─────────────────────────────────────────────────────────────

    def _read_session_record(self, sdir: Path) -> SessionRecord:
        session_id = sdir.name
        metadata = _safe_load_json(sdir / "metadata.json") or {}
        summary = _safe_load_json(sdir / "summary.json")
        event_count = 0
        events_path = sdir / "events.jsonl"
        if events_path.exists():
            with open(events_path, "r", encoding="utf-8") as fh:
                for _ in fh:
                    event_count += 1
        return SessionRecord(
            session_id=session_id,
            experiment_label=str(metadata.get("experiment_label", "")),
            started_at=metadata.get("started_at"),
            ended_at=(summary or {}).get("ended_at"),
            event_count=event_count,
            has_summary=summary is not None,
            storage_path=str(sdir),
        )

    def _atomic_write_json(self, path: Path, payload: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write to a temp file in the same directory then rename — POSIX guarantees
        # the rename is atomic within a single filesystem.
        fd, tmp_name = tempfile.mkstemp(
            prefix=path.name + ".",
            suffix=".tmp",
            dir=str(path.parent),
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, sort_keys=True, default=str)
            os.replace(tmp_name, path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def _append_index(self, record: Dict[str, Any]) -> None:
        self.index_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.index_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, separators=(",", ":"), default=str))
            fh.write("\n")


# ── Module-level singleton (the cloud server is single-worker by design) ──────

_default_storage: Optional[TelemetryStorage] = None
_default_lock = threading.Lock()


def get_default_storage() -> TelemetryStorage:
    global _default_storage
    if _default_storage is None:
        with _default_lock:
            if _default_storage is None:
                _default_storage = TelemetryStorage()
    return _default_storage


def reset_default_storage_for_tests() -> None:
    """Hook for tests / dev tooling that want a fresh singleton."""
    global _default_storage
    with _default_lock:
        _default_storage = None


# ── Helpers ───────────────────────────────────────────────────────────────────

def _safe_load_json(path: Path) -> Optional[Dict[str, Any]]:
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Failed to load %s: %s", path, exc)
        return None


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")

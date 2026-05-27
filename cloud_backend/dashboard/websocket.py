# cloud_backend/dashboard/websocket.py
"""Live telemetry broadcast hub.

Subscribers connect to ``GET /ws/telemetry`` and receive JSON frames as
new events are ingested via ``POST /telemetry/ingest``. The hub is
intentionally minimal:

* In-process only (single-worker uvicorn — matches the verification
  server's worker model).
* Bounded queue per subscriber; backpressure drops the oldest frame on
  the affected connection rather than blocking ingest.
* Optional ``?session_id=`` query filter so a dashboard can subscribe to
  a single session.

``register(app)`` is called once by :mod:`cloud_backend.server` to attach
the route. ``broadcast()`` is called by
:mod:`cloud_backend.telemetry.api` after each successful event batch.
"""
from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Set

from fastapi import FastAPI, WebSocket, WebSocketDisconnect

log = logging.getLogger("cloud_backend.ws")

_MAX_QUEUE_PER_SUBSCRIBER = 256


@dataclass
class _Subscriber:
    ws: WebSocket
    session_filter: Optional[str] = None
    queue: Deque[Dict[str, Any]] = field(default_factory=lambda: deque(maxlen=_MAX_QUEUE_PER_SUBSCRIBER))
    notify: asyncio.Event = field(default_factory=asyncio.Event)
    dropped: int = 0


_subscribers: Set[_Subscriber] = set()
_lock = asyncio.Lock()


async def _add_subscriber(ws: WebSocket, session_filter: Optional[str]) -> _Subscriber:
    sub = _Subscriber(ws=ws, session_filter=session_filter)
    async with _lock:
        _subscribers.add(sub)
    log.info("ws subscriber connected; filter=%s (total=%d)", session_filter, len(_subscribers))
    return sub


async def _remove_subscriber(sub: _Subscriber) -> None:
    async with _lock:
        _subscribers.discard(sub)
    log.info("ws subscriber disconnected (remaining=%d, dropped=%d)", len(_subscribers), sub.dropped)


async def broadcast(session_id: str, events: List[Dict[str, Any]]) -> None:
    """Fan an event batch out to every interested subscriber.

    Safe to call from a sync context via ``asyncio.run`` only if the loop
    is not already running; the telemetry router calls this from within
    its own async handler, which is correct.
    """
    if not events:
        return
    async with _lock:
        targets = [s for s in _subscribers if s.session_filter in (None, session_id)]

    if not targets:
        return

    frame = {
        "type": "telemetry_batch",
        "session_id": session_id,
        "events": events,
    }
    for sub in targets:
        # Bounded deque -> automatic drop of the oldest frame on overflow.
        if len(sub.queue) >= _MAX_QUEUE_PER_SUBSCRIBER:
            sub.dropped += 1
        sub.queue.append(frame)
        sub.notify.set()


async def _writer(sub: _Subscriber) -> None:
    """Drain the subscriber's queue, send frames over the socket."""
    try:
        while True:
            await sub.notify.wait()
            sub.notify.clear()
            while sub.queue:
                frame = sub.queue.popleft()
                await sub.ws.send_text(json.dumps(frame, separators=(",", ":"), default=str))
    except WebSocketDisconnect:
        pass
    except Exception:  # noqa: BLE001
        log.exception("ws writer exited unexpectedly")


def register(app: FastAPI) -> None:
    """Attach the WebSocket route to the given FastAPI app."""

    @app.websocket("/ws/telemetry")
    async def telemetry_ws(ws: WebSocket, session_id: Optional[str] = None) -> None:
        await ws.accept()
        sub = await _add_subscriber(ws, session_id)

        # Send a hello frame so clients can validate the contract early.
        try:
            await ws.send_text(json.dumps({
                "type": "hello",
                "session_filter": session_id,
                "max_queue": _MAX_QUEUE_PER_SUBSCRIBER,
            }))
        except Exception:  # noqa: BLE001
            await _remove_subscriber(sub)
            return

        writer_task = asyncio.create_task(_writer(sub))
        try:
            # We don't expect client messages, but draining keeps the socket healthy.
            while True:
                msg = await ws.receive_text()
                if msg == "ping":
                    await ws.send_text(json.dumps({"type": "pong"}))
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001
            log.exception("ws receive loop exited unexpectedly")
        finally:
            writer_task.cancel()
            await _remove_subscriber(sub)


def subscriber_count() -> int:
    """Diagnostic: how many WS subscribers are currently connected."""
    return len(_subscribers)

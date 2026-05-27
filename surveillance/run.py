"""Local surveillance runtime — webcam feed with live occupancy overlay."""

from __future__ import annotations

import os

import cv2

from surveillance.camera import WebcamCapture
from surveillance.occupancy import estimate_occupancy, get_active_track_ids, get_track_centroids
from surveillance.entry_zone import centroid_in_entry_zone_pixels
from surveillance.presence_client import SurveillancePresenceClient, resolve_presence_api_url
from surveillance.presence_sync import build_presence_sync

_WINDOW_TITLE = "Surveillance (Track 1)"
_QUIT_KEYS = {ord("q"), ord("Q"), 27}


def _presence_enabled() -> bool:
    raw = os.environ.get("SURVEILLANCE_PRESENCE_ENABLED", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def _presence_client() -> SurveillancePresenceClient:
    raw_batch = os.environ.get("SURVEILLANCE_PRESENCE_BATCH_SIZE", "0")
    try:
        batch_size = int(raw_batch)
    except ValueError:
        batch_size = 0

    raw_timeout = os.environ.get("SURVEILLANCE_PRESENCE_TIMEOUT_S", "1.0")
    try:
        timeout_s = float(raw_timeout)
    except ValueError:
        timeout_s = 1.0

    return SurveillancePresenceClient(
        enabled=_presence_enabled(),
        api_url=resolve_presence_api_url(),
        camera_id=os.environ.get("SURVEILLANCE_CAMERA_ID", "surveillance-laptop-01"),
        timeout_s=timeout_s,
        batch_size=batch_size,
    )


def main() -> int:
    camera = WebcamCapture(device_index=0)
    presence = build_presence_sync(_presence_client())

    try:
        for frame in camera.frames():
            count = estimate_occupancy(frame)
            height, width = frame.shape[:2]
            track_entry_zone = {
                track_id: centroid_in_entry_zone_pixels(cx, cy, frame_width=width, frame_height=height)
                for track_id, (cx, cy) in get_track_centroids().items()
            }
            presence.observe(get_active_track_ids(), count, track_entry_zone=track_entry_zone)

            cv2.putText(
                frame,
                f"Occupancy: {count}",
                (12, 32),
                cv2.FONT_HERSHEY_SIMPLEX,
                1.0,
                (0, 255, 0),
                2,
                cv2.LINE_AA,
            )

            cv2.imshow(_WINDOW_TITLE, frame)
            key = cv2.waitKey(1) & 0xFF
            if key in _QUIT_KEYS:
                break
    finally:
        presence.flush()
        camera.release()
        cv2.destroyAllWindows()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

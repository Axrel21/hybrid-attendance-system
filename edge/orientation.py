# edge/orientation.py
import numpy as np
from collections import deque
from config import settings


class PoseEstimator:
    """
    Lightweight geometric pose classifier.

    Classifies each face as FRONTAL / TILTED / OVERHEAD from the 5
    YuNet landmarks via a single ratio:

        ratio = ||eye_center - mouth_center|| / ||left_eye - right_eye||

    A perfectly frontal face has ratio ~ 1.0 (vertical eye-mouth span
    roughly equals inter-ocular). As the face is tilted forward (chin
    down, camera elevated overhead) the projected vertical span
    collapses and the ratio drops; severe overhead poses sit below 0.6.

    Thresholds are pulled from config.settings so they can be calibrated
    experimentally from the diagnostic CSV without touching this file.

    The estimator preserves its previous public contract — `estimate_mode`
    still returns the temporally-smoothed mode string — and additionally
    exposes the raw geometric measurements for the most recent call on
    `self.last_metrics[track_id]`. The runtime pipeline reads those for
    diagnostic logging (orient_ratio, eye_dist_px, vertical_dist_px,
    mode_raw) without ever altering the classification path itself.
    """

    def __init__(self):
        # smoothing-window history of the per-frame raw classifications
        self.mode_history = {}
        # most-recent raw measurements per track. Read by main._write_diag.
        # Schema:
        #   {
        #       'ratio': float,
        #       'eye_dist': float,
        #       'vertical_dist': float,
        #       'mode_raw': str,        # this-frame classification
        #       'mode_smoothed': str,   # majority vote over the window
        #   }
        self.last_metrics = {}

    @staticmethod
    def _classify(ratio: float) -> str:
        """Pure threshold mapping. Centralised for offline calibration:
        callers can recompute mode_raw from the logged ratio + new
        thresholds without rerunning the camera pipeline."""
        if ratio < settings.ORIENTATION_OVERHEAD_TH:
            return "OVERHEAD"
        if ratio < settings.ORIENTATION_TILTED_TH:
            return "TILTED"
        return "FRONTAL"

    def estimate_mode(self, track_id, landmarks):
        """Classify a face as FRONTAL / TILTED / OVERHEAD.

        Returns the temporally smoothed mode (majority vote over the
        last ORIENTATION_SMOOTHING_WINDOW frames). Side-effect: stashes
        raw metrics on `self.last_metrics[track_id]` for diagnostics.
        """
        window = settings.ORIENTATION_SMOOTHING_WINDOW
        if track_id not in self.mode_history or self.mode_history[track_id].maxlen != window:
            # Re-create the deque if the window length was tuned mid-run
            # (e.g. by editing settings.py while iterating on calibration).
            self.mode_history[track_id] = deque(maxlen=window)

        left_eye, right_eye, nose, left_mouth, right_mouth = landmarks

        eye_dist = float(np.linalg.norm(np.array(left_eye) - np.array(right_eye)))
        mouth_center = (
            (left_mouth[0] + right_mouth[0]) / 2.0,
            (left_mouth[1] + right_mouth[1]) / 2.0,
        )
        eye_center = (
            (left_eye[0] + right_eye[0]) / 2.0,
            (left_eye[1] + right_eye[1]) / 2.0,
        )
        vertical_dist = float(np.linalg.norm(np.array(eye_center) - np.array(mouth_center)))

        ratio = vertical_dist / (eye_dist + 1e-6)
        mode_raw = self._classify(ratio)

        self.mode_history[track_id].append(mode_raw)
        hist = self.mode_history[track_id]
        mode_smoothed = max(set(hist), key=hist.count)

        self.last_metrics[track_id] = {
            "ratio": float(ratio),
            "eye_dist": float(eye_dist),
            "vertical_dist": float(vertical_dist),
            "mode_raw": mode_raw,
            "mode_smoothed": mode_smoothed,
        }

        return mode_smoothed

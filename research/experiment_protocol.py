# research/experiment_protocol.py
"""Structured experiment-protocol metadata for reproducible sessions.

Writes ``experiments/exp_<id>/config/experiment_protocol.json`` next to
the existing ``settings_snapshot.json`` produced by
:func:`config.experiment_session.init_experiment_session`. The sidecar
is **purely additive** — the edge runtime does not read it, and older
sessions without one continue to work unchanged.

The fields cover the reproducibility dimensions called out in the
stabilization brief (attack-type, distance, lighting, orientation,
mounting, movement, dataset label, operator, target identities,
environment, notes). Free-text values are accepted at the wire level;
controlled vocabularies for UI consistency live in :mod:`shared.contracts`
(``ATTACK_TYPES`` / ``LIGHTING_LABELS`` / ``ORIENTATION_LABELS`` /
``MOUNTING_LABELS`` / ``MOVEMENT_LABELS``).

CLI examples
------------
::

    # Annotate the most recent session manually
    python -m research.experiment_protocol \\
        --session experiments/exp_20260516_120000 \\
        --attack-type print \\
        --distance 2.0 \\
        --lighting normal \\
        --orientation frontal \\
        --mounting tripod_eye_level \\
        --movement static \\
        --dataset-label classroom_pilot_03 \\
        --operator nikhil \\
        --target-identities student_001,student_002,student_003 \\
        --notes "phone photo replay, 1080p screen, 50cm from camera"

    # Validate an existing protocol JSON
    python -m research.experiment_protocol \\
        --validate experiments/exp_20260516_120000/config/experiment_protocol.json
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    from shared.contracts import (
        ATTACK_TYPES,
        EXPERIMENT_PROTOCOL_VERSION,
        LIGHTING_LABELS,
        MOUNTING_LABELS,
        MOVEMENT_LABELS,
        ORIENTATION_LABELS,
    )
except Exception:  # noqa: BLE001
    ATTACK_TYPES = ("none", "print", "screen_replay", "video_replay",
                    "mask_paper", "mask_silicone", "mask_resin", "deepfake",
                    "occlusion", "other")
    LIGHTING_LABELS = ("bright", "normal", "dim", "backlit", "side_lit",
                       "uneven", "outdoor_sunny", "outdoor_cloudy")
    ORIENTATION_LABELS = ("frontal", "tilted", "overhead", "side", "mixed")
    MOUNTING_LABELS = ("tripod_eye_level", "tripod_overhead", "wall_mount",
                       "ceiling_mount", "desk_clip", "handheld", "other")
    MOVEMENT_LABELS = ("static", "slow_walk", "fast_walk", "approach",
                       "retreat", "lateral", "rotation", "mixed")
    EXPERIMENT_PROTOCOL_VERSION = "1.0"


log = logging.getLogger("research.experiment_protocol")


@dataclass
class ExperimentProtocol:
    """Structured per-session protocol metadata.

    All fields are optional; ``protocol_version`` and ``recorded_at``
    are always set. Use :meth:`to_dict` to serialise; the round-tripped
    JSON is the on-disk form.
    """

    protocol_version: str = EXPERIMENT_PROTOCOL_VERSION
    session_id: str = ""
    experiment_label: str = ""
    attack_type: Optional[str] = None
    distance_m: Optional[float] = None
    lighting: Optional[str] = None
    orientation: Optional[str] = None
    mounting: Optional[str] = None
    movement: Optional[str] = None
    dataset_label: Optional[str] = None
    operator: Optional[str] = None
    target_identities: List[str] = field(default_factory=list)
    environment: Optional[str] = None
    notes: Optional[str] = None
    recorded_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%S"))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "ExperimentProtocol":
        # Only consume known fields; unknown keys are dropped (forward-compat).
        known = set(cls.__dataclass_fields__.keys())
        filtered = {k: v for k, v in payload.items() if k in known}
        return cls(**filtered)

    def validate(self) -> List[str]:
        """Soft validation. Returns a list of warning strings (empty == clean)."""
        warnings: List[str] = []
        if self.attack_type is not None and self.attack_type not in ATTACK_TYPES:
            warnings.append(f"attack_type={self.attack_type!r} not in {ATTACK_TYPES}")
        if self.lighting is not None and self.lighting not in LIGHTING_LABELS:
            warnings.append(f"lighting={self.lighting!r} not in {LIGHTING_LABELS}")
        if self.orientation is not None and self.orientation not in ORIENTATION_LABELS:
            warnings.append(f"orientation={self.orientation!r} not in {ORIENTATION_LABELS}")
        if self.mounting is not None and self.mounting not in MOUNTING_LABELS:
            warnings.append(f"mounting={self.mounting!r} not in {MOUNTING_LABELS}")
        if self.movement is not None and self.movement not in MOVEMENT_LABELS:
            warnings.append(f"movement={self.movement!r} not in {MOVEMENT_LABELS}")
        if self.distance_m is not None:
            try:
                d = float(self.distance_m)
                if d <= 0 or d > 20:
                    warnings.append(f"distance_m={self.distance_m!r} outside (0, 20) m")
            except (TypeError, ValueError):
                warnings.append(f"distance_m={self.distance_m!r} not numeric")
        return warnings


# ── I/O helpers ───────────────────────────────────────────────────────────────

def _session_config_dir(session_dir: Path) -> Path:
    return Path(session_dir).resolve() / "config"


def protocol_path(session_dir: Path) -> Path:
    return _session_config_dir(session_dir) / "experiment_protocol.json"


def load_protocol(session_dir: Path) -> Optional[ExperimentProtocol]:
    path = protocol_path(session_dir)
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("Failed to load %s: %s", path, exc)
        return None
    return ExperimentProtocol.from_dict(payload)


def write_protocol(session_dir: Path, protocol: ExperimentProtocol) -> Path:
    """Write the protocol JSON atomically. Creates config/ if needed."""
    session_dir = Path(session_dir).resolve()
    if not session_dir.exists():
        raise FileNotFoundError(f"session dir does not exist: {session_dir}")
    cfg_dir = _session_config_dir(session_dir)
    cfg_dir.mkdir(parents=True, exist_ok=True)
    target = protocol_path(session_dir)
    if not protocol.session_id:
        protocol.session_id = session_dir.name
    payload = protocol.to_dict()
    tmp = target.with_suffix(target.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
    os.replace(tmp, target)
    log.info("wrote protocol %s (warnings=%s)", target, protocol.validate())
    return target


# ── CLI ───────────────────────────────────────────────────────────────────────

def _parse_target_identities(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    return [x.strip() for x in raw.split(",") if x.strip()]


def _build_argparser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--session", help="path to experiments/exp_<id>/")
    g.add_argument("--validate", help="validate an existing experiment_protocol.json")

    p.add_argument("--experiment-label", default=os.environ.get("EXPERIMENT_LABEL", ""))
    p.add_argument("--attack-type", choices=tuple(list(ATTACK_TYPES) + [None]), default=None)
    p.add_argument("--distance", type=float, default=None,
                   help="standing distance in meters")
    p.add_argument("--lighting", choices=tuple(list(LIGHTING_LABELS) + [None]), default=None)
    p.add_argument("--orientation", choices=tuple(list(ORIENTATION_LABELS) + [None]), default=None)
    p.add_argument("--mounting", choices=tuple(list(MOUNTING_LABELS) + [None]), default=None)
    p.add_argument("--movement", choices=tuple(list(MOVEMENT_LABELS) + [None]), default=None)
    p.add_argument("--dataset-label", default=None)
    p.add_argument("--operator", default=os.environ.get("USER"))
    p.add_argument("--target-identities", default=None,
                   help="comma-separated list of expected identity labels")
    p.add_argument("--environment", default=None)
    p.add_argument("--notes", default=None)
    p.add_argument("--allow-unknown", action="store_true",
                   help="bypass vocabulary checks and accept arbitrary strings")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )
    args = _build_argparser().parse_args(argv)

    if args.validate:
        path = Path(args.validate)
        if not path.exists():
            log.error("file not found: %s", path)
            return 2
        try:
            with open(path, "r", encoding="utf-8") as fh:
                payload = json.load(fh)
        except (OSError, json.JSONDecodeError) as exc:
            log.error("invalid JSON in %s: %s", path, exc)
            return 2
        protocol = ExperimentProtocol.from_dict(payload)
        warnings = protocol.validate()
        if warnings:
            for w in warnings:
                log.warning("  - %s", w)
        else:
            log.info("OK; protocol_version=%s session_id=%s",
                     protocol.protocol_version, protocol.session_id)
        return 0 if not warnings else 1

    session_dir = Path(args.session)
    if not session_dir.exists():
        log.error("session dir not found: %s", session_dir)
        return 2

    protocol = ExperimentProtocol(
        experiment_label=args.experiment_label,
        attack_type=args.attack_type,
        distance_m=args.distance,
        lighting=args.lighting,
        orientation=args.orientation,
        mounting=args.mounting,
        movement=args.movement,
        dataset_label=args.dataset_label,
        operator=args.operator,
        target_identities=_parse_target_identities(args.target_identities),
        environment=args.environment,
        notes=args.notes,
    )

    warnings = protocol.validate()
    if warnings and not args.allow_unknown:
        for w in warnings:
            log.error("  - %s", w)
        log.error("Use --allow-unknown to bypass vocabulary checks.")
        return 1
    elif warnings:
        for w in warnings:
            log.warning("  - %s", w)

    target = write_protocol(session_dir, protocol)
    log.info("wrote %s", target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

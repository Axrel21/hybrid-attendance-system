"""
Shim — implementation: ``research.dataset_preprocess``.

Canonical enrollment preprocessing lives under ``research/`` so Pi-oriented
deploy bundles can omit it. CLI and behavior are unchanged.
"""
from __future__ import annotations

from research.dataset_preprocess import _parse_args, run

if __name__ == "__main__":
    args = _parse_args()
    run(
        raw_dir=args.raw,
        out_dir=args.out,
        yunet_path=args.yunet,
        augment_flip=args.augment_flip,
        quality=args.quality,
    )

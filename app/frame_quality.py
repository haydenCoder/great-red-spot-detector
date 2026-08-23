#!/usr/bin/env python3
"""
frame_quality.py — per-frame quality assessment + lucky-imaging rejection.

WHY THIS EXISTS
===============
Real planetary video has wildly variable seeing: a few sharp frames amid many
blurred ones. Stacking every frame equally lets the blurry majority drag the
result down. AutoStakkert / RegiStakk / Siril all do the same thing — score
each frame for sharpness, then stack only the best fraction ("lucky imaging").
Until now the planetary stacker weighted frames by sharpness but kept ALL of
them; this module adds explicit frame rejection.

WHAT IT MEASURES
================
  - sharpness : Laplacian variance on the on-disk region (a standard, cheap,
    seeing-correlated sharpness proxy — higher is sharper).
  - relative  : sharpness / max(sharpness) so scores are comparable across runs.

It does NOT claim to estimate a physical FWHM; it ranks frames, which is all a
rejection gate needs. The rank order is robust even if the absolute scale isn't.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import List, Sequence, Tuple

import numpy as np


@dataclass
class FrameQuality:
    index: int
    sharpness: float
    relative: float

    def to_dict(self) -> dict:
        return {"index": self.index, "sharpness": self.sharpness, "relative": self.relative}


def _on_disk_mask(img: np.ndarray, fill_frac: float = 0.30) -> np.ndarray:
    """Cheap on-disk mask (top fill_frac of pixels) — only for sharpness scoring,
    so it does not need the precision of a full limb fit."""
    a = np.asarray(img, dtype=np.float64)
    if a.ndim == 3:
        # Collapse to a single-plane luma regardless of HWC vs CHW layout.
        # The previous expression `a.mean(axis=tuple(range(a.ndim - 1)))`
        # averaged over (H, W) for an HWC image, returning one value per
        # channel (shape (3,)); the subsequent percentile/threshold compare
        # then broadcast to a (3,) mask, which made _laplacian_var return 0
        # for every RGB frame and silently disabled lucky-imaging rejection
        # on all colour video. Use the same NTSC weights as to_mono().
        if a.shape[-1] in (3, 4):
            # HWC (the usual SER/AVI/PNG layout)
            a = 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]
        else:
            # CHW
            a = 0.299 * a[0] + 0.587 * a[1] + 0.114 * a[2]
    thr = float(np.percentile(a, int((1.0 - fill_frac) * 100)))
    return a >= thr


def _laplacian_var(img: np.ndarray, mask: np.ndarray) -> float:
    a = np.asarray(img, dtype=np.float64)
    lap = (a[2:, 1:-1] + a[:-2, 1:-1] + a[1:-1, 2:] + a[1:-1, :-2] - 4.0 * a[1:-1, 1:-1])
    m = mask[1:-1, 1:-1]
    if m.sum() < 16:
        return 0.0
    return float(np.var(lap[m]))


def assess_frames(frames: Sequence[np.ndarray]) -> List[FrameQuality]:
    """Score every frame for sharpness. Returns one FrameQuality per frame."""
    sharp = []
    for f in frames:
        a = np.asarray(f, dtype=np.float64)
        try:
            # Collapse colour frames to a single luma plane once, up front. The
            # Laplacian operator indexes a 2-D array; a CHW/HWC colour frame
            # would otherwise produce a 3-D lap and a mismatched 2-D mask.
            if a.ndim == 3:
                if a.shape[-1] in (3, 4):
                    a = 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]
                else:
                    a = 0.299 * a[0] + 0.587 * a[1] + 0.114 * a[2]
            mask = _on_disk_mask(a)
            sharp.append(_laplacian_var(a, mask))
        except Exception:
            sharp.append(0.0)
    mx = max(sharp) if sharp else 1.0
    if mx <= 0:
        mx = 1.0
    return [FrameQuality(index=i, sharpness=float(s), relative=float(s) / mx)
            for i, s in enumerate(sharp)]


def select_best_frames(
    frames: Sequence[np.ndarray],
    keep_frac: float = 0.75,
    min_keep: int = 3,
) -> Tuple[List[int], List[int], List[FrameQuality]]:
    """Lucky-imaging selection: keep the sharpest `keep_frac` of frames.

    Returns (kept_indices, dropped_indices, qualities). Always keeps at least
    `min_keep` frames. keep_frac is clamped to (0, 1]; 1.0 keeps everything.
    """
    n = len(frames)
    keep_frac = float(min(1.0, max(0.0, keep_frac)))
    qualities = assess_frames(frames)
    order = sorted(range(n), key=lambda i: qualities[i].sharpness, reverse=True)
    n_keep = max(min_keep, int(round(keep_frac * n)))
    n_keep = min(n_keep, n)
    kept = sorted(order[:n_keep])
    dropped = sorted(order[n_keep:])
    return kept, dropped, qualities


def lucky_report(qualities: Sequence[FrameQuality]) -> dict:
    """A compact, auditable summary of the frame-quality distribution."""
    s = np.array([q.sharpness for q in qualities], dtype=np.float64)
    if s.size == 0:
        return {"n": 0}
    return {
        "n": int(s.size),
        "sharpness_min": float(s.min()),
        "sharpness_p10": float(np.percentile(s, 10)),
        "sharpness_median": float(np.median(s)),
        "sharpness_p90": float(np.percentile(s, 90)),
        "sharpness_max": float(s.max()),
        "dynamic_range": float(s.max() / max(s.min(), 1e-12)),
    }


__all__ = [
    "FrameQuality", "assess_frames", "select_best_frames", "lucky_report",
]

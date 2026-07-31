#!/usr/bin/env python3
"""
flow_warp.py — dense 2D optical-flow warp from per-AP drifts.

WHY THIS EXISTS
===============
`planetary_stacker`'s per-latitude warp aligns each image ROW by a single
latitude-dependent x-shift. That captures pure *zonal* (east–west) shear
exactly — but it cannot represent any 2D motion: a local eddy, a meridional
drift, limb foreshortening differences, or a feature that moved diagonally.
On frames with genuine local distortion it therefore leaves residual smear.

This module fits a DENSE 2D (dy, dx) displacement field from the per-AP
measurements (the same RBF fit `jpa_10k._fit_velocity_field` already uses for
its velocity field) and applies it as a sub-pixel backward warp. It is the
"2D per-pixel warp" the v6.6.3 changelog explicitly called the right next step.

HONEST SCOPE
============
On purely zonal motion (the existing benchmark) this is equivalent to the
per-latitude warp — there is nothing 2D to capture, so the extra freedom just
adds noise. It earns its keep only when the motion has a real 2D component
(local eddies, meridional drift), which is why the stacker keeps BOTH warp
modes and the benchmark suite adds a 2D-distortion case.

NOISE SENSITIVITY (measured, do not ignore)
------------------------------------------
A dense warp has more degrees of freedom than a per-row warp, so it is more
sensitive to noisy per-AP measurements. On clean, well-resolved, 2D-distorted
frames it beats per-latitude (on-disk RMS 0.134 vs 0.161). Under heavy seeing
+ read noise it can do WORSE than per-latitude (and even than naive mean),
because noisy tracker drifts get interpolated into a spurious flow that
mis-aligns the stack. The fit therefore uses a smoothing ridge + residual-space
outlier rejection (so it no longer interpolates noise exactly), and the
stacker's DEFAULT warp mode is per_latitude — flow is for clean / large-motion
data where local 2D structure matters. Pick the mode for your data; the
benchmark tool (tools/flow_warp_benchmark.py) reports which wins on yours.

The warp is a backward map (sample the frame at grid + apply-field), order-1
(bilinear) via scipy.ndimage.map_coordinates. Higher order would be sharper at
the cost of ringing at the disk edge; order-1 is the safe choice for stacking.
"""
from __future__ import annotations

from typing import Tuple

import numpy as np


def _rbf_dense_measured(
    aps_xy: np.ndarray,
    drifts: np.ndarray,
    shape: Tuple[int, int],
    smoothness: float = 2.0,
    coarse: int = 16,
    ridge: float = 0.15,
    reject_k: float = 3.0,
) -> np.ndarray:
    """Dense (h, w, 2) MEASURED-drift field from per-AP drifts via Gaussian RBF.

    Solves a SMOOTHING RBF system (K + λI) W = drifts on the APs, rejects APs
    whose residual is a robust outlier, refits, evaluates on a coarse grid,
    then bilinearly upsamples to the full frame.

    Why smoothing + rejection: a plain RBF solve interpolates EXACTLY through
    the per-AP drifts, so under seeing/noise it overfits the noisy measurements
    and the resulting warp is worse than doing nothing (measured: flow 0.138 vs
    per-lat 0.103 on-disk RMS on noisy frames). The ridge term stops exact
    interpolation and the residual-space rejection drops bad locks before the
    refit, making the dense warp robust the way per-lat's median binning is.

    `aps_xy` is (N, 2) in (x, y); `drifts` is (N, 2) in (dy, dx).
    """
    from scipy.spatial.distance import cdist
    from scipy.ndimage import zoom
    h, w = shape
    aps_xy = np.asarray(aps_xy, dtype=np.float64)
    drifts = np.asarray(drifts, dtype=np.float64)
    n = aps_xy.shape[0]
    if n == 0:
        return np.zeros((h, w, 2), dtype=np.float64)
    d = cdist(aps_xy, aps_xy)
    d_sorted = np.sort(d, axis=1)
    nn = d_sorted[:, 1] if n > 1 else d_sorted[:, 0]
    sigma = max(float(np.median(nn)) * float(smoothness), 4.0)
    K = np.exp(-(d / sigma) ** 2)
    eye = np.eye(n)
    lam = float(ridge) * float(np.trace(K) / max(n, 1))   # scale λ to the kernel

    def _fit(idx):
        Ki = K[np.ix_(idx, idx)]
        A = Ki + lam * np.eye(len(idx))
        try:
            W = np.linalg.solve(A, drifts[idx])
        except np.linalg.LinAlgError:
            W = np.linalg.lstsq(A, drifts[idx], rcond=None)[0]
        return W

    idx = np.arange(n)
    W = _fit(idx)
    ap_pred = K[:, idx] @ W
    resid = drifts - ap_pred
    keep = np.ones(n, dtype=bool)
    for c in (0, 1):
        med = float(np.median(resid[:, c]))
        s = 1.4826 * float(np.median(np.abs(resid[:, c] - med))) + 1e-9
        keep &= np.abs(resid[:, c] - med) < reject_k * s
    if int(keep.sum()) >= 3 and int(keep.sum()) < n:
        idx = np.where(keep)[0]
        W = _fit(idx)
    # evaluate on a coarse query grid (explicit linspace — mgrid's third index
    # is a STEP, not a count, the bug in the old _fit_velocity_field).
    ys = np.arange(0, h, coarse, dtype=np.float64)
    xs = np.arange(0, w, coarse, dtype=np.float64)
    qy, qx = np.meshgrid(ys, xs, indexing="ij")
    qpts = np.stack([qx.ravel(), qy.ravel()], axis=1)        # (M, 2) in (x, y)
    dq = cdist(qpts, aps_xy[idx])
    pred = (np.exp(-(dq / sigma) ** 2) @ W).reshape(len(ys), len(xs), 2)
    full = np.empty((h, w, 2), dtype=np.float64)
    fy, fx = h / pred.shape[0], w / pred.shape[1]
    for c in range(2):
        full[..., c] = zoom(pred[..., c], (fy, fx), order=1, mode="nearest")
    return full


def fit_dense_apply_field(
    aps: np.ndarray,
    drifts: np.ndarray,
    snrs: np.ndarray,
    shape: Tuple[int, int],
    smoothness: float = 2.0,
) -> np.ndarray:
    """Fit a dense (h, w, 2) *apply* displacement field from per-AP drifts.

    `drifts` is (N, 2): measured (dy, dx) frame-vs-reference at each AP.
    The returned field is the displacement to ADD when sampling (i.e. the
    negated measured drift), so `apply_flow_warp(frame, field)` aligns the
    frame to the reference. APs with non-finite drift or ~zero SNR are dropped
    so a few bad locks cannot poison the RBF.
    """
    aps = np.asarray(aps, dtype=np.float64)
    drifts = np.asarray(drifts, dtype=np.float64)
    snrs = np.asarray(snrs, dtype=np.float64)
    good = np.isfinite(drifts[:, 0]) & np.isfinite(drifts[:, 1]) & (snrs > 0.05)
    if int(good.sum()) < 1:
        return np.zeros((shape[0], shape[1], 2), dtype=np.float64)
    if int(good.sum()) < 3:
        const = drifts[good].mean(axis=0)
        field = np.zeros((shape[0], shape[1], 2), dtype=np.float64)
        field[..., 0] = -const[0]
        field[..., 1] = -const[1]
        return field
    # _rbf_dense_measured wants (x, y); aps is already (x, y).
    measured = _rbf_dense_measured(aps[good], drifts[good], shape, smoothness=smoothness)
    return -measured   # apply = -measured


def apply_flow_warp(frame: np.ndarray, apply_field: np.ndarray) -> np.ndarray:
    """Backward-warp `frame` by the (h,w,2) apply field: out(y,x) = frame(y+dy, x+dx).

    Uses bilinear (order=1) sampling. Off-edge samples clamp to the nearest edge
    value (mode='nearest'); sky pixels are ~0 so the disk edge stays clean.
    """
    from scipy.ndimage import map_coordinates
    frame = np.asarray(frame, dtype=np.float64)
    h, w = frame.shape
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    coords = np.stack([yy + apply_field[..., 0], xx + apply_field[..., 1]])
    return map_coordinates(frame, coords, order=1, mode="nearest")


__all__ = ["fit_dense_apply_field", "apply_flow_warp"]

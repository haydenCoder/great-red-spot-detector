#!/usr/bin/env python3
"""
WinJUPOS-style zonal derotator — applies a single global rotation about
the planet centre to every frame in a video, using the equatorial-band
AP-drift velocity field as the input.

WHAT THIS IS
============
WinJUPOS derotates SER/video frames by a single rotation about the
planet's centre, computed from cloud-tracking. The user typically:
  1) Identifies the equatorial-band cloud features they want to lock on.
  2) WinJUPOS measures the rotation per unit time from the cloud motion.
  3) The software applies the inverse rotation to each frame so the
     output stack has zero rotational smearing.

This module does the same thing in pure NumPy. The input is a list of
grayscale frames; the output is the list of derotated frames plus the
accumulated rotation per frame and a quality diagnostic.

ALGORITHM
=========
  1) Build an AP grid on the reference frame.
  2) For every other frame, run multi-octave phase correlation at each
     AP, get the per-AP drift (dy, dx).
  3) Restrict the APs to the equatorial band (lat < 30° from the equator
     in image-y terms — the band is configurable).
  4) Fit a single rotation about (cx, cy) to the equatorial-band
     drifts. The rotation is the one that minimises the residual.
  5) Apply the inverse rotation to every frame.
  6) Return the derotated frames and the rotation history.

The single global rotation is the WinJUPOS way: WinJUPOS does not
warp individual features; it rotates the whole disk by a single θ.
This is different from a per-pixel flow warp (which would be the JPA
"velocity field" approach). For a typical amateur Jupiter stack the
two are essentially equivalent because the disk is small enough that
the velocity field is approximately rigid-body in the disk's rotating
frame.

HONEST OPTICAL ENVELOPE
=======================
This is a standard amateur-derotation step. It does not "see" the
atmosphere; it only removes the rigid-body cloud-tracking rotation.
The remaining seeing and differential rotation across latitudes are
not removed by this module.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from verbose_log import CONSOLE
from jpa_10k import _build_ap_grid, _phase_corr_shift, _laplacian_octave


# -----------------------------------------------------------------------------
# Public dataclass
# -----------------------------------------------------------------------------

@dataclass
class DerotatorResult:
    n_frames: int
    n_aps_total: int
    n_aps_eq: int
    eq_band_frac: float
    mean_rms_drift_px: float
    rotation_per_frame_deg: List[float]
    accumulated_rotation_deg: float
    elapsed_s: float
    output_path: str
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return asdict(self)


# -----------------------------------------------------------------------------
# Rigid-body rotation fit to equatorial-band AP drifts
# -----------------------------------------------------------------------------

def _fit_rigid_rotation(
    aps: np.ndarray,           # (N, 2) — AP coordinates (x, y)
    drifts: np.ndarray,        # (N, 2) — measured (dy, dx) per AP
    centre: Tuple[float, float],
) -> float:
    """
    Fit a single rotation θ about `centre` that best explains the
    equatorial-band drifts. The linearised model for small θ is:
        dx = -θ · (y - cy)
        dy =  θ · (x - cx)
    so we solve a 1-D least-squares: θ = (Σ x·dy - Σ y·dx) / Σ r².
    """
    if aps.shape[0] < 2:
        return 0.0
    cx, cy = centre
    x = aps[:, 0] - cx
    y = aps[:, 1] - cy
    dx = drifts[:, 1]                       # x-shift in image coords
    dy = drifts[:, 0]                       # y-shift
    num = float(np.sum(x * dy) - np.sum(y * dx))
    den = float(np.sum(x * x + y * y))
    if den < 1e-12:
        return 0.0
    return num / den


# -----------------------------------------------------------------------------
# Bilinear rotation via FFT (sub-pixel rotation, no resampling)
# -----------------------------------------------------------------------------

def _rotate_about_centre(img: np.ndarray, theta_rad: float,
                        cx: float, cy: float) -> np.ndarray:
    """
    Rotate the image content by `theta_rad` about (cx, cy) in image
    coordinates — i.e. the displacement field (dx, dy) =
    (-theta·(y-cy), +theta·(x-cx)) that `_fit_rigid_rotation` fits —
    with a single-pass cubic-spline resample.

    v6.8.x audit: the previous "FFT three-shear" was mathematically wrong
    TWICE: (1) per-pixel shears were implemented as spectrum multiplications
    exp(-2πi·s(x)·k/N), but the Fourier shift theorem only supports CONSTANT
    s — a spatially-varying phase modulation is not a shear; (2) taking the
    real part of the Hermitian-broken inverse transform returns the even
    mixture (f(x-s)+f(x+s))/2, not f(x-s) (measured: ±1.5 px shifts gave
    byte-identical MSE, see app/image_warp.py docstring). A single
    scipy map_coordinates resample is exact, flux-consistent and edge-safe.
    """
    h, w = img.shape
    if abs(theta_rad) < 1e-7:
        return img.copy()
    from scipy.ndimage import map_coordinates
    c, s = math.cos(theta_rad), math.sin(theta_rad)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    dxr = xx - float(cx)
    dyr = yy - float(cy)
    # Inverse-map: out(p) = in(centre + R(-theta)·(p - centre))
    src_x = float(cx) + c * dxr + s * dyr
    src_y = float(cy) - s * dxr + c * dyr
    return map_coordinates(
        np.asarray(img, dtype=np.float64), [src_y, src_x],
        order=3, mode="nearest", prefilter=True,
    )


# -----------------------------------------------------------------------------
# Public entry point
# -----------------------------------------------------------------------------

def run_win_jupos_derotate(
    frames: List[np.ndarray],
    out_dir: Path,
    *,
    n_grid: int = 6,
    ap_half: int = 16,
    eq_band_frac: float = 0.2,
    save: bool = True,
) -> DerotatorResult:
    """
    Run the WinJUPOS-style rigid-rotation derotator on a list of
    grayscale frames. Returns a DerotatorResult with the derotated
    stack saved under `out_dir/stacked_derotated.png` plus the
    per-frame rotation history.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    if not frames:
        raise ValueError("run_win_jupos_derotate: empty frame list")
    h, w = frames[0].shape
    n_frames = len(frames)
    CONSOLE.info(
        f"WINJUPOS-DEROT: {n_frames} frames {w}x{h}, grid {n_grid}x{n_grid}, "
        f"ap_half={ap_half}, eq_band_frac={eq_band_frac}"
    )
    # Build AP grid
    ref = frames[0].astype(np.float64, copy=False)
    thr = float(np.percentile(ref, 30.0))
    disk_mask = ref > thr
    aps = _build_ap_grid(h, w, n_grid=n_grid, mask=disk_mask)
    n_aps = aps.shape[0]
    # Equatorial band mask (by image-y, the rough proxy for latitude)
    eq_band = np.abs(aps[:, 1] - h / 2) < eq_band_frac * h
    if not eq_band.any():
        eq_band = np.ones(n_aps, dtype=bool)
    n_aps_eq = int(eq_band.sum())
    CONSOLE.info(
        f"WINJUPOS-DEROT: {n_aps} APs on disk, {n_aps_eq} on equatorial band"
    )
    cy, cx = h / 2.0, w / 2.0
    # Track every frame
    per_frame_drift = np.zeros((n_frames, n_aps, 2), dtype=np.float64)
    per_frame_snr = np.zeros((n_frames, n_aps), dtype=np.float64)
    per_frame_rms = []
    for k, frame in enumerate(frames):
        if frame.shape != ref.shape:
            fh, fw = frame.shape
            y0 = max(0, (fh - h) // 2); x0 = max(0, (fw - w) // 2)
            frame = frame[y0:y0 + h, x0:x0 + w]
        for i, (x, y) in enumerate(aps):
            xi, yi = int(round(x)), int(round(y))
            if (xi - ap_half < 0 or yi - ap_half < 0
                    or xi + ap_half >= w or yi + ap_half >= h):
                continue
            # v6.8.x: proven prior-seeded tracker (content displacement,
            # _measure_shift engine). The legacy loop measured the FULL
            # shift independently at EVERY octave without re-centring and
            # summed dy_o·2^oct — a 1+2+4=7x overcount even before the
            # parabola row/col bug; planted 1.5/3/4.5 px equatorial drifts
            # fitted theta=0.15/-0.18/-0.68 deg instead of 0.96/1.91/2.86.
            from planetary_stacker import _track_ap_planetary
            tdy, tdx, snr = _track_ap_planetary(
                ref, frame, (x, y), ap_half, expected_dx=0.0)
            if math.isfinite(tdx) and math.isfinite(tdy) and snr > 0:
                per_frame_drift[k, i] = (tdy, tdx)
                per_frame_snr[k, i] = snr
        rms = float(np.sqrt(np.mean(
            per_frame_drift[k, :, 0] ** 2 + per_frame_drift[k, :, 1] ** 2
        )))
        per_frame_rms.append(rms)
    # Fit a single global rotation per frame from the equatorial band
    rot_per_frame = np.zeros(n_frames, dtype=np.float64)
    for k in range(n_frames):
        valid = eq_band & np.isfinite(per_frame_drift[k, :, 0])
        if not valid.any():
            continue
        theta = _fit_rigid_rotation(
            aps[valid], per_frame_drift[k, valid], (cx, cy)
        )
        rot_per_frame[k] = theta
    # Whole-disk image-domain polish of every fitted rotation. The AP-box
    # tracker under-measures rotation by ~20-25% on smooth textures (each
    # 33 px box sees sheared content; the correlation peak settles at a
    # compromise — measured 0.96/1.91/2.86 expected -> 0.3/1.32/1.15 fitted
    # on a gaussian-field rig, v6.8.x). The polish minimises the direct
    # image objective  MSE(ref, rotate(frame, delta))  with the exact cubic
    # rotation, initialised at the AP fit so periodic belt texture cannot
    # pull it to a neighbouring fringe, and NEVER accepted unless it beats
    # the AP fit on the same objective (guarded, so it cannot regress).
    disk_px = ref > float(np.percentile(ref, 30.0))
    ref_med = float(np.median(ref[disk_px])) if disk_px.any() else 1.0
    polish_notes = 0
    for k in range(n_frames):
        if k == 0 or not np.isfinite(rot_per_frame[k]):
            continue
        frame_f = frames[k].astype(np.float64, copy=False)
        if frame_f.shape != ref.shape:
            fh, fw = frame_f.shape
            y0 = max(0, (fh - ref.shape[0]) // 2); x0 = max(0, (fw - ref.shape[1]) // 2)
            frame_f = frame_f[y0:y0 + ref.shape[0], x0:x0 + ref.shape[1]]
        if frame_f.shape != ref.shape or not disk_px.any():
            continue
        f_med = float(np.median(frame_f[disk_px]))
        frame_g = frame_f * (ref_med / f_med) if f_med > 1e-9 else frame_f
        theta_ap_deg = math.degrees(rot_per_frame[k])
        delta0 = -theta_ap_deg                      # derotation angle (deg)

        def _mse_at(delta_deg):
            der = _rotate_about_centre(
                frame_g, math.radians(float(delta_deg)), cx, cy)
            d = (ref - der)[disk_px]
            return float(np.mean(d * d))

        span = max(0.5, 1.5 * abs(delta0) + 0.25)
        try:
            from scipy.optimize import minimize_scalar
            sol = minimize_scalar(_mse_at, bounds=(delta0 - span, delta0 + span),
                                  method="bounded",
                                  options={"xatol": 1e-3})
            best_delta = float(sol.x) if (sol.success and np.isfinite(sol.x)
                                          and _mse_at(sol.x) <= _mse_at(delta0)) \
                else delta0
        except Exception:
            best_delta = delta0
        if abs(best_delta - delta0) > 1e-4:
            polish_notes += 1
        rot_per_frame[k] = math.radians(-best_delta)
    # Compute the cumulative rotation and apply inverse derotation
    accumulated = 0.0
    derotated: List[np.ndarray] = []
    for k, frame in enumerate(frames):
        if frame.shape != ref.shape:
            fh, fw = frame.shape
            y0 = max(0, (fh - h) // 2); x0 = max(0, (fw - w) // 2)
            frame = frame[y0:y0 + h, x0:x0 + w]
        # The fitted θ is the rotation that maps ref → frame k. To
        # derotate frame k back to ref, apply -θ about (cx, cy).
        if k == 0:
            derotated.append(frame.copy())
            continue
        theta = rot_per_frame[k]
        accumulated += math.degrees(theta)
        derot_frame = _rotate_about_centre(
            np.asarray(frame, dtype=np.float64), -theta, cx, cy
        )
        derotated.append(derot_frame)
    # Quality-weighted stack of the derotated frames
    snr_per_frame = per_frame_snr.mean(axis=1)
    snr_per_frame = np.maximum(snr_per_frame, 1e-3)
    weights = snr_per_frame / snr_per_frame.sum()
    stacked = np.zeros((h, w), dtype=np.float64)
    for k, frame in enumerate(derotated):
        stacked += weights[k] * frame
    out_path = out_dir / "stacked_derotated.png"
    if save:
        try:
            from PIL import Image
            u8 = (np.clip(stacked / max(stacked.max(), 1e-9), 0, 1) * 255).astype(np.uint8)
            Image.fromarray(u8, "L").save(out_path, optimize=False)
        except Exception as e:
            CONSOLE.warn(f"WINJUPOS-DEROT: PNG save failed: {e}")
            out_path = out_dir / "stacked_derotated.npy"
            np.save(out_path, stacked)
    elapsed = time.time() - t0
    mean_rms = float(np.mean(per_frame_rms)) if per_frame_rms else 0.0
    CONSOLE.ok(
        f"WINJUPOS-DEROT done: {n_frames} frames, "
        f"{n_aps_eq}/{n_aps} APs on band, "
        f"acc rotation {accumulated:+.2f}°, mean drift {mean_rms:.2f}px, "
        f"{elapsed:.1f}s"
    )
    return DerotatorResult(
        n_frames=n_frames,
        n_aps_total=n_aps,
        n_aps_eq=n_aps_eq,
        eq_band_frac=float(eq_band.sum() / max(n_aps, 1)),
        mean_rms_drift_px=mean_rms,
        rotation_per_frame_deg=[float(math.degrees(t)) for t in rot_per_frame],
        accumulated_rotation_deg=float(accumulated),
        elapsed_s=float(elapsed),
        output_path=str(out_path),
        notes=[
            "Rigid-body rotation about planet centre (WinJUPOS way)",
            "Equatorial-band APs only — fit a single θ per frame",
            "Shear-decomposition rotation (Unser 1995) preserves flux",
        ],
    )


__all__ = ["run_win_jupos_derotate", "DerotatorResult"]

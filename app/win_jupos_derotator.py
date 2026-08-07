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
    Rotate the image by `theta_rad` about (cx, cy) via the three-shear
    decomposition (Unser et al. 1995), resampled in the SPATIAL domain.

    v6.8.x: the passes used to be FFT phase ramps, which are exact only for
    INTEGER shifts — at fractional shifts Re(ifft(F * e^{iks})) breaks
    Hermitian symmetry and collapses to the even mixture
    (f(x-s)+f(x+s))/2, so every fractional shear smeared half the shift
    back in (measured 2026-08-07). Spline resampling is exact; signs and
    pass order are unchanged, so integer-grid results are bit-near-identical
    and sub-pixel rotations are finally real.
    """
    h, w = img.shape
    if abs(theta_rad) < 1e-7:
        return img.copy()
    from scipy.ndimage import map_coordinates, shift as _nd_shift
    tan_half = math.tan(theta_rad / 2.0)
    sin_th = math.sin(theta_rad)
    ys = np.arange(h, dtype=np.float64)
    xs = np.arange(w, dtype=np.float64)

    def _shift_translate(im: np.ndarray, dy: float, dx: float) -> np.ndarray:
        return _nd_shift(im, shift=(dy, dx), order=3, mode="nearest")

    def _shear_y(im: np.ndarray, a: float) -> np.ndarray:
        # content of column x moves along y by a*(x - cx)
        out = np.empty_like(im)
        for x in range(w):
            d = a * (x - cx)
            out[:, x] = map_coordinates(im[:, x], [ys - d], order=3,
                                        mode="nearest")
        return out

    def _shear_x(im: np.ndarray, b: float) -> np.ndarray:
        # content of row y moves along x by b*(y - cy)
        out = np.empty_like(im)
        for y in range(h):
            d = b * (y - cy)
            out[y] = map_coordinates(im[y], [xs - d], order=3,
                                     mode="nearest")
        return out

    # Three-shear decomposition (Unser et al. 1995):
    #   R(θ) = T(cx, cy) * Sh_y(tan(θ/2)) * T(-cx, -cy)
    #         * Sh_x(-sin θ) * T(cx, cy) * Sh_y(tan(θ/2)) * T(-cx, -cy)
    img = _shift_translate(img, -cy, -cx)
    img = _shear_y(img, tan_half)
    img = _shear_x(img, -sin_th)
    img = _shear_y(img, tan_half)
    img = _shift_translate(img, cy, cx)
    return img


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
            ref_crop = ref[yi - ap_half:yi + ap_half + 1, xi - ap_half:xi + ap_half + 1]
            frame_crop = frame[yi - ap_half:yi + ap_half + 1,
                               xi - ap_half:xi + ap_half + 1]
            # Multi-octave phase correlation
            dy_t, dx_t, log_snr = 0.0, 0.0, 0.0
            n_ok = 0
            for oct in (0, 1, 2):
                ro = _laplacian_octave(ref_crop, oct)
                fo = _laplacian_octave(frame_crop, oct)
                try:
                    dy_o, dx_o, snr_o = _phase_corr_shift(ro, fo)
                except Exception:
                    continue
                dy_t += dy_o * (2 ** oct)
                dx_t += dx_o * (2 ** oct)
                log_snr += math.log(max(snr_o, 1e-3))
                n_ok += 1
            if n_ok > 0:
                per_frame_drift[k, i] = (dy_t, dx_t)
                per_frame_snr[k, i] = math.exp(log_snr / n_ok)
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

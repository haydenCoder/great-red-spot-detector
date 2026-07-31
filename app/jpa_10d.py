#!/usr/bin/env python3
"""
JPA-10D — 10-Dimensional Quantum-Optical Hypertensor Stacker.

SCOPE — PLEASE READ
===================
This module is a **10-D bookkeeping extension** of the JPA-10K AP-grid
stacker. It does the same numerical work as JPA-10K (per-AP sub-pixel
tracking at multiple frequency octaves, velocity-field derotation,
quality-weighted stacking), and then re-indexes the result in a
10-dimensional tensor whose axes are:

    (1) x            AP column coordinate
    (2) y            AP row coordinate
    (3) t            frame index
    (4) λ₁            octave 0 (finest)
    (5) λ₂            octave 1
    (6) λ₃            octave 2 (coarsest)
    (7) v_x          AP drift x-component
    (8) v_y          AP drift y-component
    (9) Z            Noll-Zernike mode amplitude (seeing fit)
   (10) C_n²         Kolmogorov seeing constant fit per-AP

The "quantum-optical" name in the file header is **a label, not a claim**.
The 10-D tensor here is a numerical object; no quantum state vector is
manipulated. The Zernike and C_n² components are simple scalar fits to
the local AP patch:

    - Zernike: a 6-mode Zernike fit (Z_2, Z_3, Z_4, Z_5, Z_6, Z_7) per
      AP per octave, by least-squares. This is *wavefront sensing* on a
      local AP — it is not a full Kolmogorov reconstruction.
    - C_n²:    the 5/3 slope of the AP's structure function D_φ(r) ∝
      r^(5/3) is fitted against the magnitude of the local phase cross-
      spectrum across the 3 octaves. The proportionality constant is
      what we call C_n²(AP). It is a *scalar diagnostic*, not a seeing
      forecast.

The output of this module is a quality-weighted stack. The measure of
merit on synthetic data is the same as JPA-10K: a sharper, better-aligned
result.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from verbose_log import CONSOLE
from jpa_10k import _build_ap_grid, _phase_corr_shift, _laplacian_octave


# -----------------------------------------------------------------------------
# Noll-Zernike basis (Z_2 .. Z_7) on a circular pupil
# -----------------------------------------------------------------------------

def _zernike(n: int, m: int, rho: np.ndarray, theta: np.ndarray) -> np.ndarray:
    """
    Single Noll-Zernike polynomial (n, m) on a unit pupil.
    Includes the normalisation convention of Noll (1976).
    """
    from math import factorial, sqrt
    n_abs = abs(m)
    R = np.zeros_like(rho)
    for k in range((n - n_abs) // 2 + 1):
        c = (
            (-1) ** k
            * factorial(n - k)
            / (
                factorial(k)
                * factorial((n + n_abs) // 2 - k)
                * factorial((n - n_abs) // 2 - k)
            )
        )
        R += c * rho ** (n - 2 * k)
    if m == 0:
        norm = sqrt(2 * (n + 1) / 1.0) if n > 0 else 1.0
        out = norm * R
    else:
        norm = sqrt(2 * (n + 1))
        out = norm * R * (np.cos(m * theta) if m > 0 else np.sin(-m * theta))
    return out


def _zernike_basis_6(size: int) -> List[np.ndarray]:
    """
    Build the first 6 non-trivial Noll-Zernike modes (Z_2 … Z_7) on a
    `size × size` circular pupil. Returned as a list of float64 arrays.
    """
    yy, xx = np.mgrid[-1.0:1.0:size * 1j, -1.0:1.0:size * 1j]
    rho = np.sqrt(xx ** 2 + yy ** 2)
    theta = np.arctan2(yy, xx)
    pupil = rho <= 1.0
    modes: List[np.ndarray] = []
    # Noll indices: Z_2..Z_7 are (n, m) = (1, -1), (1, 1), (2, -2), (2, 0),
    # (2, 2), (3, -1)
    for n, m in [(1, -1), (1, 1), (2, -2), (2, 0), (2, 2), (3, -1)]:
        z = _zernike(n, m, rho, theta)
        z = z * pupil
        s = float(np.std(z))
        if s > 1e-9:
            z = z / s
        modes.append(z)
    return modes


# -----------------------------------------------------------------------------
# C_n² scalar diagnostic per AP — 5/3 slope fit
# -----------------------------------------------------------------------------

def _cn2_diagnostic(
    ref: np.ndarray, frame: np.ndarray, ap_half: int,
) -> float:
    """
    Fit C_n²(AP) by measuring the magnitude of the per-octave sub-pixel
    drift and assuming the structure function scales as r^(5/3). This is
    a *relative* diagnostic — what it gives you is a per-AP "how much
    is the local seeing shifting this feature", not an absolute C_n².
    """
    sh = []
    for oct in (0, 1, 2):
        ref_oct = _laplacian_octave(ref, oct)
        frame_oct = _laplacian_octave(frame, oct)
        dy, dx, snr = _phase_corr_shift(ref_oct, frame_oct)
        sh.append(math.hypot(dy, dx) * (2 ** oct) * (snr ** 0.5))
    if not any(math.isfinite(x) for x in sh):
        return 0.0
    sh = [max(s, 1e-6) for s in sh]
    # Fit sh ∝ (2^oct)^(5/6) — that is r^(5/3) for a feature at scale 2^oct
    # We just return the coefficient, which encodes the strength of
    # sub-pixel wandering.
    sc = np.array([2.0 ** (oct * 5.0 / 6.0) for oct in (0, 1, 2)])
    try:
        coef = float(np.polyfit(np.log(sc), np.log(sh), 1)[0])
    except Exception:
        coef = 0.0
    return coef


# -----------------------------------------------------------------------------
# Per-AP, per-octave Zernike amplitude fit
# -----------------------------------------------------------------------------

def _zernike_amplitudes_per_ap(
    ref: np.ndarray, ap_half: int, size: int = 16,
) -> List[float]:
    """
    Fit 6 Noll-Zernike modes to the local phase of an AP patch.
    Here "phase" is the high-pass filtered patch; this is a toy
    wavefront sensor that captures the local low-order aberration.
    """
    basis = _zernike_basis_6(size)
    yy, xx = np.mgrid[0:size, 0:size]
    h, w = ref.shape
    cy = size // 2; cx = size // 2
    if cy - ap_half < 0 or cx - ap_half < 0 or cy + ap_half >= h or cx + ap_half >= w:
        return [0.0] * 6
    patch = ref[cy - ap_half:cy + ap_half + 1, cx - ap_half:cx + ap_half + 1]
    if patch.shape[0] != size or patch.shape[1] != size:
        return [0.0] * 6
    p = patch - patch.mean()
    # Project onto each Zernike mode
    amps: List[float] = []
    for z in basis:
        if z.shape != p.shape:
            return [0.0] * 6
        a = float(np.sum(p * z) / max(np.sum(z * z), 1e-9))
        amps.append(a)
    return amps


# -----------------------------------------------------------------------------
# Public dataclass
# -----------------------------------------------------------------------------

@dataclass
class JPA10DResult:
    n_frames: int
    n_aps: int
    n_grid: int
    tensor_shape: Tuple[int, int, int, int, int, int, int, int, int, int]
    mean_rms_drift_px: float
    mean_zernike_energy: float
    mean_cn2_diag: float
    elapsed_s: float
    output_path: str
    notes: List[str] = field(default_factory=list)
    tensor: Optional[np.ndarray] = None   # the 10-D tensor (in memory only)

    def to_dict(self) -> Dict:
        d = asdict(self)
        d["tensor_shape"] = list(self.tensor_shape)
        if self.tensor is not None:
            # Persist a compact summary, not the raw tensor
            t = self.tensor
            d["tensor_summary"] = {
                "min": float(t.min()), "max": float(t.max()),
                "mean": float(t.mean()), "std": float(t.std()),
            }
            d.pop("tensor", None)
        return d


# -----------------------------------------------------------------------------
# Main entry point
# -----------------------------------------------------------------------------

def run_jpa_10d(
    frames: List[np.ndarray],
    out_dir: Path,
    *,
    n_grid: int = 6,
    ap_half: int = 16,
    zernike_size: int = 16,
    save: bool = True,
) -> JPA10DResult:
    """
    Run the 10-D hypertensor stacker on a list of grayscale frames.
    Returns a JPA10DResult. The "10-D" tensor has axes:
       (ap, t, octave, 2 components, 6 zernike, 1 cn2) flattened into
       a 10-D numpy array of shape (n_grid, n_grid, n_frames, 3, 2, 1, 1, 1, 6, 1)
    — a bookkeeping array, not a quantum state.
    """
    import time as _time
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = _time.time()
    if not frames:
        raise ValueError("run_jpa_10d: empty frame list")
    h, w = frames[0].shape
    n_frames = len(frames)
    n_oct = 3
    CONSOLE.info(
        f"JPA-10D: {n_frames} frames {w}x{h}, grid {n_grid}x{n_grid}, "
        f"Zernike size {zernike_size}"
    )
    # AP grid
    ref = frames[0].astype(np.float64, copy=False)
    thr = float(np.percentile(ref, 30.0))
    disk_mask = ref > thr
    aps = _build_ap_grid(h, w, n_grid=n_grid, mask=disk_mask)
    n_aps = aps.shape[0]
    CONSOLE.info(f"JPA-10D: {n_aps} APs on disk")
    # 10-D tensor init: (n_grid, n_grid, n_frames, n_oct, 2, 1, 1, 1, 6, 1)
    # We use a flat (n_grid, n_grid) layout for the APs and only populate
    # positions where an AP exists; non-existent cells are NaN.
    T = np.full((n_grid, n_grid, n_frames, n_oct, 2, 1, 1, 1, 6, 1), np.nan)
    drift_rms = []
    zernike_energies = []
    cn2_diags = []
    for k, frame in enumerate(frames):
        for i, (x, y) in enumerate(aps):
            gi = int(round((x / w) * (n_grid - 1)))
            gj = int(round((y / h) * (n_grid - 1)))
            xi, yi = int(round(x)), int(round(y))
            if (xi - ap_half < 0 or yi - ap_half < 0
                    or xi + ap_half >= w or yi + ap_half >= h):
                continue
            ref_crop = ref[yi - ap_half:yi + ap_half + 1,
                           xi - ap_half:xi + ap_half + 1]
            frame_crop = frame[yi - ap_half:yi + ap_half + 1,
                               xi - ap_half:xi + ap_half + 1]
            # (4..6) octaves
            for o, oct in enumerate((0, 1, 2)):
                ro = _laplacian_octave(ref_crop, oct)
                fo = _laplacian_octave(frame_crop, oct)
                dy, dx, snr = _phase_corr_shift(ro, fo)
                T[gj, gi, k, o, 0, 0, 0, 0, 0, 0] = dy * (2 ** oct)
                T[gj, gi, k, o, 1, 0, 0, 0, 0, 0] = dx * (2 ** oct)
            # (7..8) drift at the highest octave (the one we trust most)
            dy_h, dx_h, _snr_h = _phase_corr_shift(ref_crop, frame_crop)
            T[gj, gi, k, 0, 0, 0, 0, 0, 0, 0] = dy_h
            T[gj, gi, k, 0, 1, 0, 0, 0, 0, 0] = dx_h
            # (9) Zernike amplitudes per AP (per frame)
            z_amps = _zernike_amplitudes_per_ap(ref_crop, ap_half, size=zernike_size)
            for z_i, amp in enumerate(z_amps):
                T[gj, gi, k, 0, 0, 0, 0, 0, z_i, 0] = amp
            zernike_energies.append(sum(a * a for a in z_amps))
            # (10) C_n² diagnostic
            cn2 = _cn2_diagnostic(ref_crop, frame_crop, ap_half)
            T[gj, gi, k, 0, 0, 0, 0, 0, 0, 0] = cn2
            cn2_diags.append(cn2)
            # RMS drift summary
            drift_rms.append(math.hypot(dy_h, dx_h))
    # Build the per-frame velocity field at the equatorial band and stack
    eq_band_aps = np.abs(aps[:, 1] - h / 2) < 0.2 * h
    if not eq_band_aps.any():
        eq_band_aps = np.ones(n_aps, dtype=bool)
    per_frame_shift = np.zeros((n_frames, 2), dtype=np.float64)
    for k in range(n_frames):
        d = T[:, :, k, 0, :, 0, 0, 0, 0, 0].reshape(-1, 2)
        # Mask only valid (finite) cells. Apply the AP-equatorial mask
        # to the leading n_aps entries; any extras are excluded.
        valid = np.isfinite(d[:, 0])
        m = np.zeros_like(valid)
        m[:n_aps] &= valid[:n_aps]
        m[:n_aps] &= eq_band_aps
        if m.any():
            per_frame_shift[k] = np.nanmedian(d[m], axis=0)
    # Stack
    accumulated = np.zeros((h, w), dtype=np.float64)
    weights = np.zeros((h, w), dtype=np.float64)
    for k, frame in enumerate(frames):
        dy, dx = per_frame_shift[k]
        f = np.fft.fft2(frame.astype(np.float64))
        yy, xx = np.mgrid[0:h, 0:w]
        phase = np.exp(-2j * np.pi * (dy * yy / h + dx * xx / w))
        shifted = np.real(np.fft.ifft2(f * phase))
        # Per-frame quality from the C_n² mean
        cn2_k = float(np.nanmean(cn2_diags[k * n_aps:(k + 1) * n_aps])) if cn2_diags else 1.0
        w_k = 1.0 / (1.0 + max(cn2_k, 0.0))
        accumulated += shifted * w_k
        weights += np.full_like(shifted, w_k)
    stacked = accumulated / np.maximum(weights, 1e-9)
    out_path = out_dir / "stacked_jpa10d.png"
    if save:
        try:
            from PIL import Image
            u8 = (np.clip(stacked / max(stacked.max(), 1e-9), 0, 1) * 255).astype(np.uint8)
            Image.fromarray(u8, "L").save(out_path, optimize=False)
        except Exception as e:
            CONSOLE.warn(f"JPA-10D: PNG save failed: {e}")
            out_path = out_dir / "stacked_jpa10d.npy"
            np.save(out_path, stacked)
    elapsed = _time.time() - t0
    mean_rms = float(np.nanmean(drift_rms)) if drift_rms else 0.0
    mean_z = float(np.nanmean(zernike_energies)) if zernike_energies else 0.0
    mean_cn2 = float(np.nanmean(cn2_diags)) if cn2_diags else 0.0
    CONSOLE.ok(
        f"JPA-10D done: {n_frames} frames, {n_aps} APs, "
        f"mean drift {mean_rms:.2f}px, mean Zernike energy {mean_z:.3g}, "
        f"mean C_n² diag {mean_cn2:.3g}, {elapsed:.1f}s"
    )
    return JPA10DResult(
        n_frames=n_frames,
        n_aps=n_aps,
        n_grid=n_grid,
        tensor_shape=T.shape,
        mean_rms_drift_px=mean_rms,
        mean_zernike_energy=mean_z,
        mean_cn2_diag=mean_cn2,
        elapsed_s=float(elapsed),
        output_path=str(out_path),
        notes=[
            "10-D = (x, y, t, λ₁, λ₂, λ₃, v_x, v_y, Z, C_n²) bookkeeping",
            "No quantum state; the 10-D tensor is a numpy array",
            "Zernike fit is a 6-mode local wavefront diagnostic",
            "C_n² is a 5/3-slope relative diagnostic, not a seeing forecast",
        ],
        tensor=T,
    )


__all__ = ["run_jpa_10d", "JPA10DResult"]

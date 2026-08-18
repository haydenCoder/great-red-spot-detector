#!/usr/bin/env python3
"""
sharpen_lab.py — post-stack sharpening inside the Observatory (no RegiStax
round-trip): à-trous wavelet layers, Richardson–Lucy deconvolution, unsharp
mask, and wavelet-domain noise shaping.

WHY THIS EXISTS
===============
The classic amateur pipeline is AutoStakkert! → RegiStax wavelets. The second
step exists because stacked planetary frames are seeing-blurred: lucky imaging
removes the *worst* blur but the residue is still a mild low-pass. RegiStax
"wavelets" = the B3-spline à-trous ("starlet") transform with per-layer gain
sliders (Starck & Murtagh, "Astronomical Image and Data Analysis", 2002).
This module implements exactly that, plus:

  - Richardson–Lucy deconvolution (Richardson 1972; Lucy 1974, AJ 79, 745)
    with a Gaussian PSF — the maximum-likelihood sharpen for Poisson-ish data
    and the honest way to undo a measured seeing blur;
  - unsharp mask (the classic baseline);
  - wavelet hard/soft denoising with a robust per-layer MAD noise estimate —
    so you can push sharpening further without amplifying detector noise.

RGB handling: sharpening is applied on an HSV-style value/luma channel and the
chroma is kept (the RegiStax "RGB" behaviour), or strictly per-channel.

HONEST SCOPE
============
Sharpening trades SNR for resolution: every test here verifies recovered
*signal* fidelity (Lapvar/correlation vs an unblurred reference), not just
"looks crisper". Over-sharpening rings — the wavelet gains default to a
conservative profile and the denoise threshold defaults to ON.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional, Sequence, Tuple

import numpy as np

_B3 = np.array([1.0, 4.0, 6.0, 4.0, 1.0]) / 16.0   # B3 spline kernel


# ---------------------------------------------------------------------------
# à-trous (starlet) wavelet transform
# ---------------------------------------------------------------------------

def atrous_decompose(img: np.ndarray, n_layers: int = 5) -> Tuple[List[np.ndarray], np.ndarray]:
    """Starlet decomposition: returns ([w1..wn], c_n) with img = c_n + Σ w_j.

    The à-trous algorithm inserts 2^(j-1)−1 zeros between kernel taps at each
    scale, equivalent to convolving with an upsampled B3 kernel.
    """
    c = np.asarray(img, dtype=np.float64)
    scales = []
    for j in range(n_layers):
        step = 1 << j
        c_next = _convolve_b3(c, step)
        scales.append(c - c_next)
        c = c_next
    return scales, c


def atrous_reconstruct(scales: Sequence[np.ndarray], residual: np.ndarray) -> np.ndarray:
    out = np.asarray(residual, dtype=np.float64).copy()
    for w in scales:
        out = out + w
    return out


def _convolve_b3(img: np.ndarray, step: int) -> np.ndarray:
    """Separable B3-spline convolution with tap distance `step` (à-trous).

    Kernel (1,4,6,4,1)/16 applied at offsets {−2s, −s, 0, +s, +2s} along each
    axis; reflect padding keeps flux at the borders honest.
    """
    from scipy.ndimage import shift as _sshift
    acc = img.astype(np.float64, copy=False)
    for axis in (0, 1):
        out = (6.0 / 16.0) * acc
        for d, wgt in ((1 * step, 4.0 / 16.0), (2 * step, 1.0 / 16.0)):
            off = [0, 0]
            off[axis] = d
            out = out + wgt * (_sshift(acc, tuple(off), order=0, mode="reflect")
                               + _sshift(acc, tuple(-np.array(off)), order=0, mode="reflect"))
        acc = out
    return acc


# ---------------------------------------------------------------------------
# noise estimation / wavelet denoise
# ---------------------------------------------------------------------------

def estimate_noise_mad(img: np.ndarray) -> float:
    """Robust Gaussian-σ estimate from the finest wavelet layer MAD
    (σ ≈ MAD/0.6745 of w1, the standard starlet noise estimator)."""
    w, _ = atrous_decompose(img, n_layers=1)
    mad = float(np.median(np.abs(w[0] - np.median(w[0]))))
    return mad / 0.6745


def wavelet_denoise(
    img: np.ndarray,
    *,
    n_layers: int = 5,
    k_sigma: float = 3.0,
    keep_coarse_from: int = 2,
) -> np.ndarray:
    """Soft-threshold wavelet layers at k_sigma × σ_j (noise scales as ~2^-j).
    Layers at index >= keep_coarse_from are left alone (real structure).
    """
    scales, resid = atrous_decompose(img, n_layers=n_layers)
    sigma = estimate_noise_mad(img)
    out_scales = []
    for j, w in enumerate(scales):
        if j >= keep_coarse_from:
            out_scales.append(w)
            continue
        thr = k_sigma * sigma / (2.0 ** j)
        wa = np.abs(w)
        shrink = np.clip(wa - thr, 0.0, None)
        out_scales.append(np.sign(w) * shrink)
    return atrous_reconstruct(out_scales, resid)


# ---------------------------------------------------------------------------
# sharpening operators
# ---------------------------------------------------------------------------

def wavelet_sharpen(
    img: np.ndarray,
    gains: Sequence[float] = (1.8, 1.5, 1.25, 1.1, 1.0),
    *,
    denoise: bool = True,
    denoise_k: float = 2.5,
    clip: Optional[Tuple[float, float]] = None,
) -> np.ndarray:
    """RegiStax-style per-layer wavelet gain. gains[0] = finest layer."""
    n = len(gains)
    scales, resid = atrous_decompose(img, n_layers=n)
    if denoise:
        sigma = estimate_noise_mad(img)
    else:
        sigma = 0.0
    out_scales = []
    for j, w in enumerate(scales):
        w2 = w
        if denoise and j < 2 and sigma > 0:
            thr = denoise_k * sigma / (2.0 ** j)
            wa = np.abs(w2)
            w2 = np.sign(w2) * np.clip(wa - thr, 0.0, None)
        out_scales.append(gains[j] * w2)
    out = atrous_reconstruct(out_scales, resid)
    if clip is not None:
        out = np.clip(out, clip[0], clip[1])
    return out


def richardson_lucy(
    img: np.ndarray,
    psf_sigma_px: float = 1.5,
    iters: int = 14,
    *,
    psf: Optional[np.ndarray] = None,
    clip: Optional[Tuple[float, float]] = None,
    epsilon: float = 1e-6,
) -> np.ndarray:
    """Classic RL deconvolution with a Gaussian (or supplied) PSF."""
    from scipy.signal import fftconvolve
    from scipy.ndimage import gaussian_filter
    a = np.asarray(img, dtype=np.float64)
    a = np.clip(a, 0.0, None) + epsilon
    if psf is None:
        r = max(2, int(np.ceil(psf_sigma_px * 3)))
        yy, xx = np.mgrid[-r:r + 1, -r:r + 1]
        psf = np.exp(-(yy ** 2 + xx ** 2) / (2.0 * psf_sigma_px ** 2))
        psf /= psf.sum()
    psf_m = psf[::-1, ::-1]
    est = np.maximum(gaussian_filter(a, max(1.0, psf_sigma_px)) * 0.5 + a * 0.5, epsilon)
    for _ in range(int(iters)):
        conv = fftconvolve(est, psf, mode="same") + epsilon
        est = est * fftconvolve(a / conv, psf_m, mode="same")
        est = np.maximum(est, epsilon)
    if clip is not None:
        est = np.clip(est, clip[0], clip[1])
    return est


def unsharp_mask(
    img: np.ndarray,
    radius_px: float = 2.5,
    amount: float = 1.0,
    clip: Optional[Tuple[float, float]] = None,
) -> np.ndarray:
    from scipy.ndimage import gaussian_filter
    a = np.asarray(img, dtype=np.float64)
    low = gaussian_filter(a, radius_px)
    out = a + amount * (a - low)
    if clip is not None:
        out = np.clip(out, clip[0], clip[1])
    return out


# ---------------------------------------------------------------------------
# RGB / luma front end
# ---------------------------------------------------------------------------

def _split_rgb(img: np.ndarray) -> np.ndarray:
    a = np.asarray(img, dtype=np.float64)
    if a.ndim == 3 and a.shape[0] in (3, 4) and a.shape[0] < min(a.shape[1], a.shape[2]):
        a = np.moveaxis(a, 0, -1)
    return a


def apply_luma(img: np.ndarray, op, luma_weights=(0.299, 0.587, 0.114)) -> np.ndarray:
    """Apply a mono operator to the luma of an RGB image, scaling channels
    by the luma ratio (hue/sat preserved, the RegiStax 'RGB' behaviour)."""
    a = _split_rgb(img)
    if a.ndim == 2:
        return op(a)
    lw = np.asarray(luma_weights)
    luma = a[..., 0] * lw[0] + a[..., 1] * lw[1] + a[..., 2] * lw[2]
    luma_out = op(luma)
    ratio = np.divide(luma_out, luma, out=np.ones_like(luma), where=luma > 1e-9)
    return np.clip(a * ratio[..., None], 0.0, None)


def sharpen(
    img: np.ndarray,
    method: str = "wavelet",
    *,
    gains: Sequence[float] = (1.8, 1.5, 1.25, 1.1, 1.0),
    rl_sigma_px: float = 1.5,
    rl_iters: int = 14,
    unsharp_radius_px: float = 2.5,
    unsharp_amount: float = 1.0,
    denoise: bool = True,
    per_channel: bool = False,
    clip: Optional[Tuple[float, float]] = None,
) -> np.ndarray:
    """One-call sharpen. method: wavelet | rl | unsharp. RGB via luma by
    default (or per_channel=True)."""
    a = _split_rgb(img)
    if clip is None and a.size and a.max() <= 1.0 + 1e-6:
        clip = (0.0, 1.0)

    if method == "wavelet":
        op = lambda x: wavelet_sharpen(x, gains, denoise=denoise, clip=clip)
    elif method == "rl":
        op = lambda x: richardson_lucy(x, rl_sigma_px, rl_iters, clip=clip)
    elif method == "unsharp":
        op = lambda x: unsharp_mask(x, unsharp_radius_px, unsharp_amount, clip=clip)
    else:
        raise ValueError(f"unknown sharpen method {method!r}")

    if a.ndim == 2 or per_channel is False and a.ndim == 3:
        if a.ndim == 2:
            return op(a)
        return apply_luma(a, op)
    # per-channel
    chans = [op(a[..., i]) for i in range(a.shape[-1])]
    return np.stack(chans, axis=-1)


# ---------------------------------------------------------------------------
# metrics (for tests, benches and the GUI labels)
# ---------------------------------------------------------------------------

def laplacian_variance(img: np.ndarray) -> float:
    a = np.asarray(img, dtype=np.float64)
    lap = (a[2:, 1:-1] + a[:-2, 1:-1] + a[1:-1, 2:] + a[1:-1, :-2] - 4.0 * a[1:-1, 1:-1])
    return float(np.var(lap))


def gradient_energy(img: np.ndarray) -> float:
    a = np.asarray(img, dtype=np.float64)
    gy = np.diff(a, axis=0)
    gx = np.diff(a, axis=1)
    return float((gy * gy).mean() + (gx * gx).mean())

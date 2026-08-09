#!/usr/bin/env python3
"""
ap_stacker.py — APS: AutoStakkert-style per-alignment-point quality stacking,
with true drizzle super-resolution and an optional rotation-aware prior that
AutoStakkert does not have.

WHY THIS EXISTS
===============
The classic AutoStakkert! recipe for planetary video:

  1. Place a dense grid of *alignment points* (APs) over the planet.
  2. Track every AP in every frame (local subpixel shift, phase correlation).
  3. Score every AP in every frame for *local* sharpness (gradient energy).
  4. For each AP, stack only that AP's own best X% of frames — because seeing
     varies across the disk, not just between frames.
  5. Optionally *drizzle*: deposit frames onto a ×2/×3 finer grid using the
     measured subpixel shifts, recovering detail below the Nyquist limit of a
     single frame (Fruchter & Hook 2002, PASP 114, 144).

Our existing `planetary_stacker` does (2) but stacks *whole frames* — a frame
that is sharp on the left and mushy on the right drags its mush into the whole
map. `ap_stacker` implements (1)–(5) natively, and adds one thing AutoStakkert
simply cannot do: an optional **rotation-aware prior** (`ap_expected_dx` px or
a Planet + frame times) that pre-centres every AP by the System-III/zonal-wind
drift expected between the frame and the reference moment, so a long capture
stacks like a short one. AutoStakkert deliberately ignores rotation (that is
why people round-trip through WinJUPOS); here it is built in.

SHIFT CONVENTION (verified by tests/test_ap_stacker.py)
=======================================================
`_phase_corr_shift(ref, img)` returns the shift to *apply* to img so it aligns
with ref (content moves by +shift under scipy.ndimage.shift). All local and
global measurements in this module use that "apply-shift" convention, and
drizzle deposits each source pixel at (pixel_index + apply_shift).

HONEST SCOPE
============
- Per-AP selection beats global-frame selection whenever sharpness is actually
  spatially variable (real seeing). The tests prove both alignment recovery
  and a sharpness win on locally-blurred synthetic captures.
- Drizzle only super-resolves when the input captures carry *true* subpixel
  diversity (small optical shifts / dither) and the sampling is the limit,
  not the optics. The tests plant exactly that and measure the recovery.
- We do NOT claim APS-in-1s: per-frame FFT work is real. For long captures
  use `align_downsample` and sensible AP spacing.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

import numpy as np

from frame_quality import assess_frames
from jpa_10k import _phase_corr_shift, _laplacian_octave


# ---------------------------------------------------------------------------
# Config / result
# ---------------------------------------------------------------------------

@dataclass
class APStackConfig:
    ap_size_px: int = 32          # AP box side (px, full res)
    spacing_px: int = 0           # 0 -> ap_size // 2 (AutoStakkert-style overlap)
    keep_frac: float = 0.25       # per-AP lucky fraction
    quality: str = "laplacian"    # laplacian | gradient | sobel | contrast
    drizzle: int = 1              # 1 = off; 2 or 3 = super-res grid
    pixfrac: float = 1.0          # drizzle drop size, in input-pixel units
    global_align: bool = True
    local_align: bool = True
    align_downsample: int = 1     # measure global shift on a 1/ds grid (speed; 1,2,4)
    max_local_shift_px: float = 8.0
    min_ap_mean_frac: float = 0.03
    weight_power: float = 1.0     # quality^p stacking weights
    feather_frac: float = 0.25    # AP edge feather, fraction of spacing
    ref_index: int = -1           # -1 = auto (sharpest)
    subpixel_refine: bool = True  # local upsampled-DFT refine of phase-corr peak
    min_snr: float = 1.3          # below this, local shift is untrusted -> 0
    normalize_brightness: bool = True  # median disk brightness match to reference
    # rotation-aware prior: predicted per-frame content drift in px (equatorial).
    # Positive = content moved +x. All-or-none with obstimes.
    ap_expected_dx: Optional[Sequence[float]] = None
    rgb_luma: Tuple[float, float, float] = (0.299, 0.587, 0.114)


@dataclass
class APStackResult:
    stack: np.ndarray             # (H*D, W*D) or (H*D, W*D, 3), float in input units
    weight: np.ndarray            # effective weight map (same 2D shape)
    n_frames: int
    n_aps: int
    ref_index: int
    drizzle: int
    per_frame_used: np.ndarray    # (n_frames,) fraction of APs where frame was selected
    global_shifts: np.ndarray     # (n_frames, 2) measured global apply-shifts (dy, dx)
    mean_local_shift_rms: float
    secs: float
    config: dict

    def to_dict(self) -> dict:
        return {
            "n_frames": self.n_frames, "n_aps": self.n_aps,
            "ref_index": self.ref_index, "drizzle": self.drizzle,
            "mean_local_shift_rms": self.mean_local_shift_rms,
            "secs": self.secs, "config": self.config,
        }


# ---------------------------------------------------------------------------
# Luma / channel helpers
# ---------------------------------------------------------------------------

def _to_luma(frame: np.ndarray, w_rgb: Tuple[float, float, float]) -> np.ndarray:
    a = np.asarray(frame, dtype=np.float64)
    if a.ndim == 2:
        return a
    if a.ndim == 3 and a.shape[0] in (3, 4) and a.shape[0] < min(a.shape[1], a.shape[2]):
        a = np.moveaxis(a, 0, -1)
    return w_rgb[0] * a[..., 0] + w_rgb[1] * a[..., 1] + w_rgb[2] * a[..., 2]


def _channels(frame: np.ndarray) -> List[np.ndarray]:
    a = np.asarray(frame, dtype=np.float64)
    if a.ndim == 2:
        return [a]
    if a.ndim == 3 and a.shape[0] in (3, 4) and a.shape[0] < min(a.shape[1], a.shape[2]):
        a = np.moveaxis(a, 0, -1)
    return [a[..., i] for i in range(min(3, a.shape[-1]))]


def _frame_float(a: np.ndarray) -> np.ndarray:
    a = np.asarray(a)
    if a.dtype == np.uint8:
        return a.astype(np.float64) / 255.0
    return a.astype(np.float64)


# ---------------------------------------------------------------------------
# Subpixel refinement: local upsampled DFT (Guizar et al. 2008 idea, small)
# ---------------------------------------------------------------------------

def _phase_power(ref: np.ndarray, img: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """Windowed, mean-subtracted, phase-only cross-power of ref × img, plus
    the plain correlation surface for SNR estimation."""
    win = np.outer(np.hanning(ref.shape[0]), np.hanning(ref.shape[1]))
    R = np.fft.fft2((ref - ref.mean()) * win)
    I = np.fft.fft2((img - img.mean()) * win)
    cross = R * np.conj(I)
    mag = np.abs(cross)
    eps = max(float(mag.max()) * 1e-9, 1e-300)
    cp = cross / (mag + eps)
    cc = np.real(np.fft.ifft2(cp))
    return cp, cc


def _lk_refine(ref: np.ndarray, img: np.ndarray, ay: float, ax: float,
               iters: int = 4, margin: int = 5, prefilter: float = 1.0) -> Tuple[float, float]:
    """Lucas–Kanade subpixel refinement of an integer apply-shift estimate.

    Solves min_c Σ [img_warped_c − ref]² by Gauss–Newton: warped(p)=img(p−c),
    diff = ref − warped = G·(c − c_true), so c ← c − (GᵀG)⁻¹Gᵀdiff. On
    shift-only band-limited fields this is exact to ~0.001 px (verified);
    a light prefilter makes it noise-robust. Divergence guard: updates
    larger than 2 px abort the refinement (keep the integer estimate).
    """
    from scipy.ndimage import gaussian_filter, map_coordinates, spline_filter
    if prefilter and prefilter > 0:
        ref = gaussian_filter(ref, prefilter)
        img = gaussian_filter(img, prefilter)
    h, w = ref.shape
    margin = int(min(margin, (min(h, w) - 8) // 2))
    if margin < 3:
        return ay, ax
    # PERF (bit-exact): the cubic-spline prefilter is a pure function of
    # `img`, but map_coordinates(order=3, mode="nearest") redoes it (plus a
    # 12-px edge pad) on EVERY call — 3 calls per iteration. Replicate the
    # internal path once (pad 12 edge + spline_filter mode="nearest", the
    # exact scipy recipe) and sample coefficients with coords+12 and
    # prefilter=False: verified max|delta| = 0.0 against map_coordinates on
    # the same field, and bitwise-identical APS golden rigs.
    img_c = spline_filter(np.pad(img, 12, mode="edge"), order=3,
                          mode="nearest")
    P = 12
    ys0 = np.arange(margin, h - margin, dtype=float)
    xs0 = np.arange(margin, w - margin, dtype=float)
    yy, xx = np.meshgrid(ys0, xs0, indexing="ij")
    ref_c = ref[margin:h - margin, margin:w - margin]
    cy, cx = float(ay), float(ax)
    for _ in range(iters):
        ys = yy - cy + P
        xs = xx - cx + P
        warped = map_coordinates(img_c, [ys, xs], order=3, mode="nearest",
                                 prefilter=False)
        gy = 0.5 * (map_coordinates(img_c, [ys + 1, xs], order=3, mode="nearest", prefilter=False)
                    - map_coordinates(img_c, [ys - 1, xs], order=3, mode="nearest", prefilter=False))
        gx = 0.5 * (map_coordinates(img_c, [ys, xs + 1], order=3, mode="nearest", prefilter=False)
                    - map_coordinates(img_c, [ys, xs - 1], order=3, mode="nearest", prefilter=False))
        if float((gy * gy + gx * gx).sum()) < 1e-9:
            break                                    # flat box: no information
        A = np.stack([gy.ravel(), gx.ravel()], 1)
        # Tikhonov regularisation keeps flat/one-sided boxes sane
        AtA = A.T @ A
        lam = 1e-6 * float(np.trace(AtA))
        sol = np.linalg.solve(AtA + lam * np.eye(2), A.T @ diff.ravel()) \
            if (diff := ref_c - warped) is not None else 0.0
        if not np.all(np.isfinite(sol)) or max(abs(sol[0]), abs(sol[1])) > 2.0:
            return float(ay), float(ax)              # diverged: keep integer
        cy -= float(sol[0]); cx -= float(sol[1])
    return cy, cx


def _measure_shift(ref: np.ndarray, img: np.ndarray, refine: bool = True) -> Tuple[float, float, float]:
    """Apply-shift (dy, dx) to move img's content back onto ref's grid, + SNR.

    If img(x) = ref(x − τ) then cross = R·conj(I) = |R|²e^(+i2πkτ) and
    ifft2(cross)(m) = Σ |R|² e^(i2πk(τ+m)) peaks at m = −τ — i.e. the peak
    position IS the apply-shift (no sign flip). Coarse = phase-only integer
    FFT peak; refinement = Lucas–Kanade Gauss–Newton (~0.001 px on clean
    shift-only data; the legacy `_phase_corr_shift` parabola has a row/col
    indexing bug we deliberately do not inherit).
    """
    h, w = ref.shape
    cp, cc = _phase_power(ref, img)
    py, px = np.unravel_index(int(np.argmax(cc)), cc.shape)
    ay = float(py - h if py > h // 2 else py)
    ax = float(px - w if px > w // 2 else px)
    if refine:
        try:
            ay, ax = _lk_refine(ref, img, ay, ax)
        except Exception:
            pass
    # snr: peak vs best peak outside the 5x5 neighbourhood
    yy, xx = np.mgrid[0:h, 0:w]
    near = (np.abs((yy - py + h // 2) % h - h // 2) <= 2) & (np.abs((xx - px + w // 2) % w - w // 2) <= 2)
    second = float(cc[~near].max()) if (~near).any() else 1e-12
    snr = float(cc[py, px] / max(second, 1e-12))
    return float(ay), float(ax), snr


# ---------------------------------------------------------------------------
# Local quality estimators
# ---------------------------------------------------------------------------

def _quality_laplacian(crop: np.ndarray) -> float:
    lap = (crop[2:, 1:-1] + crop[:-2, 1:-1] + crop[1:-1, 2:] + crop[1:-1, :-2]
           - 4.0 * crop[1:-1, 1:-1])
    return float(np.var(lap))


def _quality_gradient(crop: np.ndarray) -> float:
    gy = np.abs(np.diff(crop, axis=0))
    gx = np.abs(np.diff(crop, axis=1))
    return float(gy.mean() + gx.mean())


def _quality_sobel(crop: np.ndarray) -> float:
    c = crop
    kx = (c[:-2, 2:] + 2 * c[1:-1, 2:] + c[2:, 2:]) - (c[:-2, :-2] + 2 * c[1:-1, :-2] + c[2:, :-2])
    ky = (c[2:, :-2] + 2 * c[2:, 1:-1] + c[2:, 2:]) - (c[:-2, :-2] + 2 * c[:-2, 1:-1] + c[:-2, 2:])
    return float((kx * kx + ky * ky).mean())


def _quality_contrast(crop: np.ndarray) -> float:
    return float(crop.std() / (float(crop.mean()) + 1e-9))


_QUALITY_FNS = {
    "laplacian": _quality_laplacian,
    "gradient": _quality_gradient,
    "sobel": _quality_sobel,
    "contrast": _quality_contrast,
}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _feather(size: int, margin: int) -> np.ndarray:
    """Separable raised-cosine edge taper over `margin` px."""
    ramp = np.ones(size)
    m = min(margin, size // 2)
    if m > 0:
        t = np.linspace(0.0, 1.0, m + 1)[1:]        # exclude the zero edge
        edge = 0.5 - 0.5 * np.cos(np.pi * t)
        ramp[:m] = edge
        ramp[-m:] = edge[::-1]
    return np.outer(ramp, ramp)


def _crop_padded(img: np.ndarray, y: int, x: int, box: int) -> np.ndarray:
    """box×box crop centred at (y, x), edge-padded if near the border."""
    half = box // 2
    y0, y1 = y - half, y - half + box
    x0, x1 = x - half, x - half + box
    pad_y0, pad_y1 = max(0, -y0), max(0, y1 - img.shape[0])
    pad_x0, pad_x1 = max(0, -x0), max(0, x1 - img.shape[1])
    crop = img[max(0, y0):min(img.shape[0], y1), max(0, x0):min(img.shape[1], x1)]
    if pad_y0 or pad_y1 or pad_x0 or pad_x1:
        crop = np.pad(crop, ((pad_y0, pad_y1), (pad_x0, pad_x1)), mode="edge")
    return crop


# ---------------------------------------------------------------------------
# Drizzle core
# ---------------------------------------------------------------------------

def _drizzle_deposit(
    num: np.ndarray, den: np.ndarray,
    crop: np.ndarray,
    out_y0: int, out_x0: int,           # canvas origin of this AP block
    dy: float, dx: float,               # apply-shift (align crop to ref)
    factor: int, pixfrac: float,
    q: float, feather: np.ndarray,
    accumulate_den: bool = True,
) -> None:
    """Deposit `crop` into (num, den) on the factor-upsampled canvas.

    Each input pixel is a square drop of side `pixfrac` (input-pixel units)
    = factor*pixfrac output bins, centred where the pixel's light lands on the
    reference grid *after* applying the measured alignment shift:
        centre = (pixel_index + apply_shift + 0.5) * factor - 0.5
    Deposits are weighted by q (per-AP quality) and the AP feather; the same
    weight (without the pixel value) accumulates in `den` so the final
    quotient is an unbiased weighted mean.
    """
    S = crop.shape[0]
    D = factor
    side = D * pixfrac
    yy, xx = np.mgrid[0:S, 0:S]
    # bin-space centre of each input pixel after the apply-shift: input pixel
    # index j covers bin-space [jD, jD+D) so its centre is (j+0.5)D
    cy = (yy.ravel() + dy + 0.5) * D
    cx = (xx.ravel() + dx + 0.5) * D
    vals = (crop * q * feather).ravel()
    wbase = (q * feather).ravel().astype(np.float64)

    n_bins = int(math.floor(side)) + 2
    eps = 1e-12

    left_y = cy - side / 2.0
    left_x = cx - side / 2.0
    right_y = left_y + side
    right_x = left_x + side
    base_y = np.floor(left_y).astype(np.int64)
    base_x = np.floor(left_x).astype(np.int64)

    W = num.shape[1]
    for iy_bin in range(n_bins):
        by = base_y + iy_bin
        wy = np.clip(np.minimum(right_y, by + 1) - np.maximum(left_y, by), 0.0, 1.0)
        if float(wy.max()) <= eps:
            continue
        oy = out_y0 + by
        y_ok = (wy > eps) & (oy >= 0) & (oy < num.shape[0])
        if not y_ok.any():
            continue
        for ix_bin in range(n_bins):
            bx = base_x + ix_bin
            wx = np.clip(np.minimum(right_x, bx + 1) - np.maximum(left_x, bx), 0.0, 1.0)
            if float(wx.max()) <= eps:
                continue
            ox = out_x0 + bx
            ok = y_ok & (wx > eps) & (ox >= 0) & (ox < W)
            if not ok.any():
                continue
            wprod = (wy * wx)[ok]
            idx = oy[ok] * W + ox[ok]
            np.add.at(num.ravel(), idx, vals[ok] * wprod)
            if accumulate_den:
                np.add.at(den.ravel(), idx, wbase[ok] * wprod)


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

def stack_ap(frames: Sequence[np.ndarray], cfg: Optional[APStackConfig] = None) -> APStackResult:
    """Stack frames with per-AP quality selection (+ optional drizzle).

    Pipeline (no interpolation ever touches the data before deposit):

      1. luma for every frame; reference = sharpest (or pinned)
      2. optional per-frame brightness normalisation (median disk ratio)
      3. global apply-shift per frame (phase correlation, upsampled-DFT refine)
      4. AP grid over the disk; per (frame, AP) window placed at
         p + round(global + expected_prior) so only the *subpixel residual*
         is left for the local phase correlation
      5. per (frame, AP) local sharpness quality
      6. per-AP lucky selection (top keep_frac of frames *for that AP*)
      7. deposit RAW pixels with the measured fractional offsets via a
         pixfrac-sized drizzle kernel onto the DxD output grid; feathered
         AP footprints overlap so seams vanish

    frames: sequence of (h, w) mono or (h, w, 3) RGB arrays; uint8 is scaled
    to [0, 1], everything else is used as float64 in its native units.
    """
    t0 = time.time()
    cfg = cfg or APStackConfig()
    if not frames:
        raise ValueError("stack_ap: no frames")
    D = int(max(1, min(3, int(cfg.drizzle))))
    pixfrac = float(np.clip(cfg.pixfrac, 0.2, 1.0))

    # ---- normalise input
    frames_f = [_frame_float(f) for f in frames]
    n = len(frames_f)
    lumas = [_to_luma(f, cfg.rgb_luma) for f in frames_f]
    h, w = lumas[0].shape
    if any(l.shape != (h, w) for l in lumas):
        raise ValueError("stack_ap: frames must share shape")
    is_rgb = len(_channels(frames_f[0])) == 3
    n_ch = 3 if is_rgb else 1

    # ---- reference frame (sharpest unless pinned)
    if cfg.ref_index >= 0:
        ref_idx = int(cfg.ref_index)
    else:
        qs = assess_frames(lumas)
        ref_idx = int(max(range(n), key=lambda i: qs[i].sharpness))
    ref = lumas[ref_idx]

    # ---- brightness normalisation to the reference (disk medians)
    norm = np.ones(n)
    if cfg.normalize_brightness:
        ref_med = _disk_median(ref)
        for i, l in enumerate(lumas):
            m = _disk_median(l)
            norm[i] = ref_med / m if m > 1e-9 else 1.0
        lumas = [l * norm[i] for i, l in enumerate(lumas)]
        frames_f = [f * norm[i] for i, f in enumerate(frames_f)]
        ref = lumas[ref_idx]

    # ---- global apply-shifts (measurement optionally downsampled for speed)
    gshifts = np.zeros((n, 2), dtype=np.float64)
    ds = int(cfg.align_downsample)
    if ds not in (1, 2, 4, 8):
        ds = 1
    oct_ = int(math.log2(ds))
    ref_a = _laplacian_octave(ref, oct_) if ds > 1 else ref
    if cfg.global_align:
        for i, l in enumerate(lumas):
            if i == ref_idx:
                continue
            l_a = _laplacian_octave(l, oct_) if ds > 1 else l
            dy, dx, _ = _measure_shift(ref_a, l_a, refine=cfg.subpixel_refine)
            gshifts[i] = (dy * ds, dx * ds)

    # ---- rotation prior (predicted content drift px per frame)
    if cfg.ap_expected_dx is not None:
        expected = np.asarray(cfg.ap_expected_dx, dtype=np.float64)
        if expected.shape != (n,):
            raise ValueError("ap_expected_dx must be (n_frames,) px offsets")
    else:
        expected = np.zeros(n)
    # Window pre-centre uses CONTENT DISPLACEMENT (+d where frame(x)=ref(x−d)),
    # which is the NEGATIVE of the measured apply-shift g, plus the rotation
    # prior (also a content displacement). The phase correlation then only has
    # to find the subpixel residual.
    pre_int_y = np.rint(-gshifts[:, 0]).astype(np.int64)
    pre_int_x = np.rint(-gshifts[:, 1] + expected).astype(np.int64)
    max_pre = int(max(0, np.abs(pre_int_x).max(), np.abs(pre_int_y).max()))

    # ---- AP grid on the reference frame
    size = int(max(16, cfg.ap_size_px))
    spacing = int(cfg.spacing_px) if cfg.spacing_px else max(8, size // 2)
    half = size // 2
    pad = int(math.ceil(cfg.max_local_shift_px)) + max_pre + 2
    box = size + 2 * pad
    min_mean = float(cfg.min_ap_mean_frac) * _disk_median(ref)
    aps: List[Tuple[int, int]] = []
    if h >= box + 2 and w >= box + 2:
        for y in range(half + pad, h - half - pad + 1, spacing):
            for x in range(half + pad, w - half - pad + 1, spacing):
                if float(ref[y - half:y - half + size, x - half:x - half + size].mean()) >= min_mean:
                    aps.append((x, y))
    if not aps:  # degenerate tiny input: drop the pad, centre one AP
        pad = 0
        box = size
        pre_int_x[:] = 0
        pre_int_y[:] = 0
        aps = [(w // 2, h // 2)]
    n_ap = len(aps)

    qfn = _QUALITY_FNS.get(cfg.quality)
    if qfn is None:
        raise ValueError(f"unknown quality estimator {cfg.quality!r}")
    feather = _feather(size, max(2, int(spacing * cfg.feather_frac)))

    # ---- reference crops (raw, unpadded-shift; padded at borders only)
    ref_crops = [_crop_padded(ref, y, x, box) for (x, y) in aps]

    # ---- per (frame, AP): residual apply-shift (raw crops) + local quality
    Q = np.zeros((n, n_ap), dtype=np.float64)
    DY = np.zeros((n, n_ap), dtype=np.float64)
    DX = np.zeros((n, n_ap), dtype=np.float64)
    for i in range(n):
        img = lumas[i]
        for j, (x, y) in enumerate(aps):
            crop = _crop_padded(img, y + int(pre_int_y[i]), x + int(pre_int_x[i]), box)
            if cfg.local_align and i != ref_idx:
                dy, dx, snr = _measure_shift(ref_crops[j], crop, refine=cfg.subpixel_refine)
                if (snr < cfg.min_snr
                        or abs(dy) > cfg.max_local_shift_px
                        or abs(dx) > cfg.max_local_shift_px):
                    dy, dx, snr = 0.0, 0.0, 0.0
            else:
                dy, dx, snr = 0.0, 0.0, 2.0
            DY[i, j] = dy
            DX[i, j] = dx
            inner = crop[pad:pad + size, pad:pad + size]
            Q[i, j] = qfn(inner) if (snr > 0 or not cfg.local_align) else 0.0

    # ---- per-AP lucky selection
    keep = max(1, int(round(n * float(np.clip(cfg.keep_frac, 0.0, 1.0)))))
    order = np.argsort(-Q, axis=0)                  # best-ranked first, per AP

    # ---- deposit RAW crops
    H, W = h * D, w * D
    num = [np.zeros((H, W), dtype=np.float64) for _ in range(n_ch)]
    den = np.zeros((H, W), dtype=np.float64)
    used = np.zeros(n)
    frame_chans = [_channels(f) for f in frames_f]

    for j, (x, y) in enumerate(aps):
        sel = [i for i in order[:keep, j] if Q[i, j] > 0] or [int(order[0, j])]
        out_y0 = (y - half) * D
        out_x0 = (x - half) * D
        for i in sel:
            used[i] += 1.0 / n_ap
            q = float(Q[i, j]) ** cfg.weight_power
            if q <= 0:
                q = 1e-6
            # Both crops are RAW, so the measured residual already contains
            # every fractional component of the misalignment; the integer
            # part was absorbed by the window placement, not the deposit.
            dy_i = float(DY[i, j])
            dx_i = float(DX[i, j])
            for ci in range(n_ch):
                src = frame_chans[i][ci]
                crop = _crop_padded(src, y + int(pre_int_y[i]), x + int(pre_int_x[i]), box)
                inner = crop[pad:pad + size, pad:pad + size]
                _drizzle_deposit(
                    num[ci], den, inner, out_y0, out_x0,
                    dy_i, dx_i, D, pixfrac, q, feather,
                    accumulate_den=(ci == 0),
                )

    rms = float(np.sqrt(np.mean(DY ** 2 + DX ** 2))) if n_ap else 0.0
    with np.errstate(invalid="ignore", divide="ignore"):
        out_chan = [np.where(den > 1e-12, num_c / den, 0.0) for num_c in num]
    stack = out_chan[0] if n_ch == 1 else np.stack(out_chan, axis=-1)

    return APStackResult(
        stack=stack, weight=den, n_frames=n, n_aps=n_ap, ref_index=ref_idx,
        drizzle=D, per_frame_used=used, global_shifts=gshifts,
        mean_local_shift_rms=rms, secs=time.time() - t0,
        config={k: (str(v) if k in ("planet",) else v) for k, v in cfg.__dict__.items()},
    )


def _disk_median(img: np.ndarray) -> float:
    hi = img[img >= np.percentile(img, 60)]
    return float(np.median(hi)) if hi.size else 1.0


# ---------------------------------------------------------------------------
# Rotation derotation for APS: WinJUPOS's trick, done in-house
# ---------------------------------------------------------------------------

def derotate_frames(
    frames: Sequence[np.ndarray],
    *,
    frame_times: Optional[Sequence] = None,
    dt_s_per_frame: Optional[Sequence[float]] = None,
    planet=None,
    mode: str = "hybrid",            # measurement | prior | hybrid
    ref_index: int = -1,
    n_grid: int = 6,
    ap_half: int = 16,
    sub_lat_deg: float = 0.0,
    north_pa_deg: float = 0.0,
) -> Tuple[List[np.ndarray], dict]:
    """Rotate-compensate a capture BEFORE lucky stacking.

    WHY: AutoStakkert refuses to model rotation, so long captures smear (at
    Jupiter's 36.3 deg/h, a 5-minute video sweeps a GRS-latitude feature by
    ~0.06·R_eq). WinJUPOS derotates but has no per-AP lucky imaging. This
    function is the composition point: it returns per-row-warped frames in
    the reference frame's System-III orientation — stack them with stack_ap
    and you have something neither tool offers alone.

    mode:
      "measurement" — fit dx(|lat|) purely from tracked APs (needs good signal)
      "prior"       — pure planet-model cloud-tracking curve (no image signal)
      "hybrid"      — measurement regularised toward the prior by mean SNR
    dt source: `dt_s_per_frame` (s to reference) wins; else per-frame
    datetimes in `frame_times`. Frames stay in their input units; RGB is
    warped per channel with the mono-measured field. The reference frame is
    auto-picked (sharpest) unless `ref_index>=0`.
    """
    from planet_models import JUPITER as _JUPITER
    planet = planet or _JUPITER
    from precision_engine import fit_limb_nav, to_mono
    from jpa_10k import _build_ap_grid
    from planetary_stacker import (
        _per_pixel_lat, _ap_latitudes, _track_ap_planetary, _frame_dt,
        select_reference_index, fit_dx_vs_latitude, per_row_warp,
        gate_ap_track,
    )

    if not frames:
        raise ValueError("derotate_frames: no frames")
    mode = str(mode or "hybrid").lower()
    if mode not in ("measurement", "prior", "hybrid"):
        raise ValueError(f"derotate mode must be measurement|prior|hybrid, got {mode!r}")
    n = len(frames)
    mono = [to_mono(_frame_float(f)) for f in frames]
    mono = [m if np.isfinite(m).all() else np.nan_to_num(m) for m in mono]
    h, w = mono[0].shape

    ref_idx = int(ref_index) if 0 <= int(ref_index) < n else int(select_reference_index(mono))
    ref = mono[ref_idx]

    if dt_s_per_frame is not None:
        dts = [float(x) for x in dt_s_per_frame]
    elif frame_times is not None:
        times = list(frame_times)
        if len(times) != n or any(t is None for t in times):
            raise ValueError("derotate_frames: frame_times must cover every frame")
        t_ref = times[ref_idx]
        dts = [(t - t_ref).total_seconds() for t in times]
    else:
        raise ValueError("derotate_frames: give dt_s_per_frame or frame_times")
    # Always express dt relative to the ACTUAL reference frame (callers usually
    # supply per-frame epochs, not reference-relative offsets).
    dts = [d - dts[ref_idx] for d in dts]

    nav = fit_limb_nav(ref, cm_iii_deg=0.0, distance_au=planet.default_distance_au)
    lat_map, on_disk = _per_pixel_lat(nav, h, w, sub_lat_deg, north_pa_deg)
    row_lats = np.array([
        float(np.mean(lat_map[r][on_disk[r]])) if on_disk[r].any() else 0.0
        for r in range(h)
    ])
    deg_to_px = nav.a_eq_px / 90.0

    thr = float(np.percentile(ref, 30.0))
    aps = _build_ap_grid(h, w, n_grid=n_grid, mask=ref > thr)
    ap_lats = _ap_latitudes(aps, nav, sub_lat_deg, north_pa_deg)
    n_aps = aps.shape[0]

    def _prior_bins(dt_k, n_bins=11):
        # apply-shift (what per_row_warp consumes) = MINUS the content drift
        centres = (np.arange(n_bins) + 0.5) * (90.0 / n_bins)
        return np.array([-planet.lon_drift_px(float(c), dt_k, nav.a_eq_px)
                         for c in centres])

    is_rgb = np.asarray(frames[0]).ndim == 3
    warped: List[np.ndarray] = []
    med_shift: List[float] = []
    notes: List[str] = []
    dy_fitted_all: List[float] = []
    wind_frames: List[Tuple[float, np.ndarray, np.ndarray]] = []  # (dt, drifts, snrs)
    for k in range(n):
        if k == ref_idx:
            warped.append(_frame_float(frames[k]))
            med_shift.append(0.0)
            continue
        dt_k = _frame_dt(planet, k, ref_idx, [0.0] * n, dts)
        if mode == "prior" or (mode == "hybrid" and n_aps < 4):
            dx_bins = _prior_bins(dt_k)
            dy_g = 0.0
        else:
            drifts = np.full((n_aps, 2), np.nan, dtype=np.float64)
            snrs = np.zeros(n_aps, dtype=np.float64)
            for i, (ax, ay) in enumerate(aps):
                from planetary_stacker import _per_ap_expected_dx_lon
                # correctly scaled (π/180)r cosφ chord prior — the legacy
                # _per_ap_expected_dx under-shifts by up to 1.57x (v6.8 fix)
                exp_dx = _per_ap_expected_dx_lon(planet, float(ap_lats[i]), dt_k,
                                                 deg_to_px * 90.0)
                tdy, tdx, snr = _track_ap_planetary(ref, mono[k], (ax, ay),
                                                    ap_half, expected_dx=exp_dx)
                # AutoStakkert-style gates: limb boxes (lock on the geometric
                # edge, not the clouds) and post-prior residual outliers
                # (mis-locks) become NaN so the fit falls back to the model
                # prior in their band (v6.8.x zonal audit, measured).
                if not gate_ap_track(nav, (ax, ay), tdy, tdx, exp_dx):
                    tdy, tdx, snr = float("nan"), float("nan"), 0.0
                drifts[i] = (tdy, tdx)
                snrs[i] = snr
            if dt_k != 0.0:
                wind_frames.append((dt_k, drifts.copy(), snrs.copy()))
            dx_bins, dy_g = fit_dx_vs_latitude(ap_lats, drifts, snrs, planet,
                                               dt_s=dt_k, deg_to_px=deg_to_px)
            # Rotation is zonal: a real capture's y-wander is tip/tilt, which
            # the stacker's global-align pass removes afterwards anyway. On
            # bland textures the fitted dy is a phantom (measured -0.46 px
            # when the true dy was 0 — v6.8.x fiducial audit), i.e. applying
            # it ADDS y-error. The derotator therefore applies dx-only; the
            # fitted dy is still reported for transparency.
            dy_fitted = float(dy_g)
            dy_fitted_all.append(dy_fitted)
            dy_g = 0.0
            if mode == "hybrid":
                prior = _prior_bins(dt_k, dx_bins.size)
                ok = np.isfinite(drifts[:, 0])
                mean_snr = float(np.nanmean(snrs[ok])) if ok.any() else 0.0
                w_meas = min(1.0, mean_snr / 2.0)
                dx_bins = w_meas * dx_bins + (1.0 - w_meas) * prior
        src = _frame_float(frames[k])
        if is_rgb:
            chans = [per_row_warp(src[..., c], dx_bins, dy_g, on_disk, row_lats)
                     for c in range(min(3, src.shape[-1]))]
            warped.append(np.stack(chans, axis=-1))
        else:
            warped.append(per_row_warp(src, dx_bins, dy_g, on_disk, row_lats))
        centres = (np.arange(dx_bins.size) + 0.5) * (90.0 / dx_bins.size)
        med_shift.append(float(np.median(
            np.abs(np.interp(np.abs(row_lats[on_disk.any(axis=1)]),
                             centres, dx_bins)))) if on_disk.any() else 0.0)

    info = {
        "mode": mode,
        "planet": planet.name,
        "ref_index": ref_idx,
        "n_frames": n,
        "n_track_aps": int(n_aps),
        "median_per_row_shift_px": float(np.median(med_shift)),
        "max_per_row_shift_px": float(np.max(med_shift)),
        "disk_a_eq_px": float(nav.a_eq_px),
        "dt_range_s": [float(min(dts)), float(max(dts))],
        "sub_lat_deg": float(sub_lat_deg),
        "north_pa_deg": float(north_pa_deg),
        "dy_fitted_px": [float(v) for v in dy_fitted_all],
        "wind_report": wind_report_from_drifts(
            planet, ap_lats, wind_frames, nav.a_eq_px),
        "notes": notes + [
            "rotation is zonal: fitted dy reported (dy_fitted_px) but NOT "
            "applied — y-wander is tip/tilt, removed downstream by the "
            "stacker's global align (phantom dy on bland frames measured "
            "-0.46 px, v6.8.x fiducial audit)"
        ],
    }
    return warped, info


# ---------------------------------------------------------------------------
# Zonal-wind measurement: every derotated capture is a wind experiment
# ---------------------------------------------------------------------------


def wind_report_from_drifts(planet, ap_lats, wind_frames, a_eq_px: float,
                            n_bins: int = 11) -> dict:
    """Measured cloud-tracking zonal-wind profile vs |latitude|, from the
    prior-seeded AP drifts `derotate_frames` already collects.

    WHY: WinJUPOS users reduce *drift measurements* to study jet streams;
    AutoStakkert cannot. The same AP tracks that drive derotation are a
    quantitative wind experiment: each track gives the cloud-feature angular
    rate omega = -content_px / (dt · chord_px_per_deg) at its latitude
    (content moves -x for +CM). Aggregated per |lat| bin across frames
    (weighted by |dt|), the residual against the planet's literature
    cloud-tracking profile is exported in m/s:

        Δv = (omega_meas − omega_model) · (π/180) · parallel_radius(φ)

    HONEST SCOPE: this is a *capture-local* measurement at the precision of
    the AP tracks (sub-px ⇒ a few m/s over a typical 2–3 min amateur span —
    see the pinned recovery test in tests/test_ap_stacker.py). Bins with no
    surviving evidence (all APs gated away) are None, never fabricated.
    """
    centres = ((np.arange(n_bins) + 0.5) * (90.0 / n_bins)).astype(np.float64)
    edges = np.linspace(0.0, 90.0, n_bins + 1)
    ap_lats = np.asarray(ap_lats, dtype=np.float64)
    abs_lats = np.abs(ap_lats)
    # Convert EVERY track to a wind residual in m/s FIRST (dt-invariant),
    # then robust-stack per bin. Individual tracks carry fringe-alias
    # outliers of hundreds of m/s on quasi-periodic band texture (measured
    # on the planted-wind benchmark v6.8.x); per-track m/s makes the
    # outlier physics scale-free across mixed dt spans, and the iterated
    # MAD-rejected median is the honest central estimator. Caveat: when a
    # fringe consistently out-votes the truth in a bin (> 50%), no robust
    # estimator can know — the reported MAD is scatter, not aliasing.
    per_bin_du: List[List[float]] = [[] for _ in range(n_bins)]
    per_bin_frames: List[set] = [set() for _ in range(n_bins)]
    for fidx, (dt_k, drifts, snrs) in enumerate(wind_frames):
        if abs(dt_k) < 1e-9:
            continue
        good = np.isfinite(drifts[:, 1]) & (snrs > 0.05)
        for i in np.where(good)[0]:
            la = float(ap_lats[i])
            chord = planet.px_per_deg_lon(la, a_eq_px)
            if chord <= 1e-9:
                continue
            rate = -float(drifts[i, 1]) / (float(dt_k) * chord)
            omega_m = float(planet.cloud_tracking_rate_deg_per_s(la))
            du = (rate - omega_m) * (math.pi / 180.0) \
                * float(planet.surface_parallel_radius_m(la))
            b = int(np.searchsorted(edges, abs_lats[i], side="right") - 1)
            b = min(max(b, 0), n_bins - 1)
            per_bin_du[b].append(float(du))
            per_bin_frames[b].add(fidx)
    rates: List[Optional[float]] = []
    rate_stds: List[Optional[float]] = []
    residuals: List[Optional[float]] = []
    resid_stds: List[Optional[float]] = []
    n_tracks_bins: List[int] = []
    n_frames_bins: List[int] = []
    model: List[float] = []
    for b in range(n_bins):
        c = float(centres[b])
        omega_m = float(planet.cloud_tracking_rate_deg_per_s(c))
        model.append(omega_m)
        k = (math.pi / 180.0) * float(planet.surface_parallel_radius_m(c))
        dus = per_bin_du[b]
        n_tracks_bins.append(len(dus))
        n_frames_bins.append(len(per_bin_frames[b]))
        if not dus:
            rates.append(None); rate_stds.append(None)
            residuals.append(None); resid_stds.append(None)
            continue
        med = float(np.median(dus))
        for _ in range(2):                      # iterated MAD rejection
            mad = float(np.median([abs(d - med) for d in dus]))
            sigma = max(1.4826 * mad, 1e-6)
            keep = [d for d in dus if abs(d - med) <= 3.5 * sigma]
            if len(keep) == len(dus) or len(keep) < 3:
                dus = keep
                break
            dus = keep
            med = float(np.median(dus))
        mad = float(np.median([abs(d - med) for d in dus]))
        std = 1.4826 * mad
        rates.append(omega_m + med / k)
        residuals.append(med)
        rate_stds.append(float(std / k))
        resid_stds.append(float(std))
    finite_res = [r for r in residuals if r is not None]
    return {
        "bins_abs_lat_deg": [float(c) for c in centres],
        "measured_rate_deg_per_s": rates,
        "measured_rate_std_deg_per_s": rate_stds,
        "model_rate_deg_per_s": model,
        "wind_residual_mps_vs_model": residuals,
        "wind_residual_std_mps": resid_stds,
        "n_evidence_tracks": n_tracks_bins,
        "n_evidence_frames": n_frames_bins,
        "max_abs_residual_mps": (max(abs(r) for r in finite_res)
                                  if finite_res else None),
        "note": ("capture-local cloud-tracking wind profile from AP drifts "
                 "(per-track m/s, iterated MAD-rejected median); residual = "
                 "measured − literature model (planet_models), positive = "
                 "prograde/super-rotating relative to model. Scatter is "
                 "MAD-based; systematic fringe aliasing cannot self-report."),
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def aps_report_text(res: APStackResult) -> str:
    lines = []
    lines.append("=" * 74)
    lines.append("APS STACK REPORT — per-alignment-point quality stacking")
    lines.append("=" * 74)
    lines.append(f"frames: {res.n_frames}   APs: {res.n_aps}   reference frame: {res.ref_index}")
    lines.append(f"drizzle: x{res.drizzle}   local-shift RMS: {res.mean_local_shift_rms:.3f} px")
    lines.append(f"elapsed: {res.secs:.1f}s")
    u = res.per_frame_used
    if u.size:
        lines.append(f"frame usage: min {u.min():.2f} / median {float(np.median(u)):.2f} / max {u.max():.2f}")
        best = int(np.argmax(u)); worst = int(np.argmin(u))
        lines.append(f"  most-used frame: #{best} ({u[best]:.2f})   least-used: #{worst} ({u[worst]:.2f})")
    gs = res.global_shifts
    if gs.size:
        mag = np.hypot(gs[:, 0], gs[:, 1])
        lines.append(f"global shift |.| px: median {float(np.median(mag)):.2f}  max {mag.max():.2f}")
    lines.append(f"output: {res.stack.shape[1]}x{res.stack.shape[0]}"
                 + (" RGB" if res.stack.ndim == 3 else ""))
    return "\n".join(lines)

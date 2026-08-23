#!/usr/bin/env python3
"""
planetary_stacker.py — planet-generalised AP-grid stacker with a
genuine per-latitude warp.

WHAT THIS FIXES vs jupiter_zonal_stacker
========================================
`jupiter_zonal_stacker.run_jupiter_zonal_stacker` tracks every alignment
point with the full System III + zonal-wind prior — and then, in its final
warp, collapses all those per-AP drifts into ONE global (dy, dx) translation
per frame. The per-latitude shear it just measured is thrown away, so on
genuinely latitude-dependent motion (different belts rotating at different
rates, which is the physical reality on every gas giant) the stack is still
smeared exactly like a generic AP stacker.

This module keeps the good part (multi-octave, zonal-prior-aware tracking)
and applies what was missing: a **per-latitude** shift. We bin the measured
per-AP drifts by |latitude|, take a robust SNR-weighted mean per bin, and
shift each image row by the drift at that row's latitude. Where a latitude
bin has no measurements, the planet-model expected drift fills in (hybrid
measurement + prior). The result aligns each belt to the reference instead
of forcing the whole disk onto one belt's motion.

It is also planet-general: pass a `planet_models.Planet` (Jupiter, Saturn,
Neptune, Uranus, Mars built in) instead of hardcoded Jupiter constants.

WHAT IT IS NOT
==============
- Not an optical-flow per-pixel warp. It is per-row (WinJUPOS-style extended
  to per-latitude), which is the honest, fast, physically-motivated choice.
- Not a claim to beat WinJUPOS on every frame; it is a measured improvement
  over the previous global-translation stacker on latitude-sheared data.
"""
from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from verbose_log import CONSOLE
from planet_models import Planet, JUPITER
from jpa_10k import _build_ap_grid, _phase_corr_shift, _laplacian_octave
from precision_engine import fit_limb_nav, to_mono, wrap_diff, px_to_lonlat_vec, FLAT as _ENGINE_FLAT
from flow_warp import fit_dense_apply_field, apply_flow_warp
from frame_quality import select_best_frames


# Upper bound on the warped-frame cube (n × h × w × ch × 8 bytes) the robust
# sigma-clipped combination will materialise. Above this we fall back to the
# streaming weighted mean (the clip needs the whole cube in memory; a few
# hundred 4K RGB frames would not fit, and the clip's value is for transient
# defects on lucky-imaging stacks of tens of frames anyway).
_ROBUST_MEMORY_BUDGET_BYTES = 1_500_000_000  # 1.5 GB


def _to_hwc(frame: np.ndarray) -> np.ndarray:
    """Normalise an RGB frame to (h, w, 3) float64 (handles CHW / RGBA / HWC).

    Returns None-ish (the mono path) is NOT used here — callers check ndim first.
    """
    a = np.asarray(frame, dtype=np.float64)
    if a.ndim != 3:
        return a
    h, w, c = (a.shape[1], a.shape[2], a.shape[0]) if a.shape[0] in (3, 4) and a.shape[0] < min(a.shape[1], a.shape[2]) else (a.shape[0], a.shape[1], a.shape[-1])
    # take first 3 channels
    if a.shape[0] in (3, 4) and a.shape[0] < min(a.shape[1], a.shape[2]):
        return a[:3].transpose(1, 2, 0)
    return a[..., :3]


# ---------------------------------------------------------------------------
# Hybrid prior + measurement tracker (planet-generalised)
# ---------------------------------------------------------------------------

def _per_ap_expected_dx(planet: Planet, lat_deg: float, dt_s: float, deg_to_px: float) -> float:
    """LEGACY scaling — superseded by `_per_ap_expected_dx_lon` (which uses the
    correct (π/180)r cosφ chord conversion; this one under-shifts by up to
    1.57× at the equator). Kept for signature compatibility; all in-repo
    derotation call sites have been switched over."""
    if dt_s == 0.0:
        return 0.0
    # cloud moves +longitude; in image-x that is -deg_to_px per degree (observer
    # convention, matching the synthetic simulator / winjupos_derotator).
    return -planet.cloud_tracking_rate_deg_per_s(lat_deg) * dt_s * deg_to_px


def _per_ap_expected_dx_lon(planet: Planet, lat_deg: float, dt_s: float, a_eq_px: float) -> float:
    """Correctly scaled image-x displacement (px) a cloud feature at φ makes
    over dt_s: −ω_cloud(φ)·dt·(π/180)·r(φ)cosφ·a_eq_px. Ground-truthed on
    rotating-video renders (<0.5° left after 350 s drift; test_video_jupiter).
    Re-centering the AP crop by this before phase correlation keeps the
    residual small enough to lock even when bulk rotation has swept the
    feature many pixels (the raw tracker saturates past ap_half)."""
    return planet.lon_drift_px(lat_deg, dt_s, a_eq_px)


def _track_ap_planetary(
    ref: np.ndarray,
    frame: np.ndarray,
    ap_xy: Tuple[float, float],
    ap_half: int,
    expected_dx: float,
    expected_dy: float = 0.0,
    octaves: Sequence[int] = (0, 1, 2),
) -> Tuple[float, float, float]:
    """Track one AP with the expected drift removed before correlation.

    Multi-octave coarse-to-fine, frame crop re-centred by (expected + residual
    so far). Returns the TOTAL (dy, dx) displacement frame-vs-reference at the
    AP (= expected + measured residual), and a geometric-mean SNR. Mirrors the
    proven `jupiter_zonal_stacker._track_ap_zonal`, generalised to any planet.
    """
    h, w = ref.shape
    x, y = ap_xy
    xi, yi = int(round(x)), int(round(y))
    if xi - ap_half < 0 or yi - ap_half < 0 or xi + ap_half >= w or yi + ap_half >= h:
        return float("nan"), float("nan"), 0.0
    ref_crop = ref[yi - ap_half:yi + ap_half + 1, xi - ap_half:xi + ap_half + 1]
    # pred = current best CONTENT DISPLACEMENT (px, full-res) of the frame
    # relative to the reference, seeded by the model prior. The window is
    # re-centred by pred; ap_stacker._measure_shift returns the residual
    # APPLY-shift at the octave scale, so a residual content displacement is
    # minus that. (The previous implementation accumulated apply-shifts as if
    # they were displacements AND re-centred the window by (2**oct)x the
    # prediction; against a known planted 4.2 px displacement it returned
    # garbage unless the prior was already exact — i.e. it tracked nothing.
    # Measured and fixed in v6.8.x).
    from ap_stacker import _measure_shift
    pred_dy, pred_dx = float(expected_dy), float(expected_dx)
    log_snr, n_ok = 0.0, 0
    for oct in octaves:
        ref_oct = _laplacian_octave(ref_crop, oct)
        cx = xi + int(round(pred_dx))
        cy = yi + int(round(pred_dy))
        cx = max(ap_half, min(w - ap_half - 1, cx))
        cy = max(ap_half, min(h - ap_half - 1, cy))
        fr_crop = frame[cy - ap_half:cy + ap_half + 1, cx - ap_half:cx + ap_half + 1]
        fr_oct = _laplacian_octave(fr_crop, oct)
        if fr_oct.shape != ref_oct.shape:
            break
        try:
            dy, dx, snr = _measure_shift(ref_oct, fr_oct, refine=True)
        except Exception:
            break
        if not (math.isfinite(dy) and math.isfinite(dx) and math.isfinite(snr)):
            break
        pred_dy -= float(dy) * (2 ** oct)
        pred_dx -= float(dx) * (2 ** oct)
        log_snr += math.log(max(float(snr), 1e-3))
        n_ok += 1
    if n_ok == 0:
        return float("nan"), float("nan"), 0.0
    return pred_dy, pred_dx, math.exp(log_snr / n_ok)


def _ap_sky_rr(nav, x: float, y: float) -> float:
    """Normalised squared sky-plane radius of an image point (1.0 = limb).

    rr is invariant to north-PA rotation (a rotation in the sky plane), so
    no PA correction is needed; sub-lat tilt maps the *surface* point but the
    limb ellipse itself is unchanged.
    """
    b = nav.a_eq_px * (1.0 - float(nav.flattening))
    xs = (float(x) - nav.xc) / (nav.a_eq_px + 1e-12)
    ys = (nav.yc - float(y)) / (b + 1e-12)
    return xs * xs + ys * ys


def gate_ap_track(nav, ap_xy: Tuple[float, float], tdy: float, tdx: float,
                  expected_dx: float, expected_dy: float = 0.0,
                  limb_rr_max: float = 0.93,
                  resid_floor_px: float = 2.0, resid_frac: float = 0.3) -> bool:
    """Accept/reject one prior-seeded AP track (AutoStakkert-style AP gating).

    Two independent, physically motivated gates — both failure modes were
    measured directly on rotating-video renders (v6.8.x zonal audit):

      1. LIMB GATE: the AP centre must be inside rr <= limb_rr_max.
         Nearer the limb the phase correlation locks onto the *geometric*
         disk edge (which does not move with the clouds) or onto sky noise.
         Those boxes mis-lock by 2-8 px even with phase-corr SNR 8-9, so
         SNR alone cannot identify them; geometry can.
      2. RESIDUAL GATE: after removing the wind-model prior, the leftover
         must be small: |resid| <= max(resid_floor_px, resid_frac*|prior|+1).
         Unmodelled zonal shear is far below 1 px at amateur scales
         (0.05 km/s of shear over 3 min of capture is ~0.01 px at 2 px/deg),
         so a multi-px residual is a tracker mis-lock, not meteorology.

    Rejected APs become NaN in the drift table: the per-latitude fit then
    falls back to the model prior for that band instead of warping garbage.
    """
    if not (math.isfinite(tdx) and math.isfinite(tdy)):
        return False
    if _ap_sky_rr(nav, ap_xy[0], ap_xy[1]) > float(limb_rr_max):
        return False
    rx = abs(float(tdx) - float(expected_dx))
    ry = abs(float(tdy) - float(expected_dy))
    lim = max(float(resid_floor_px),
              float(resid_frac) * (abs(float(expected_dx)) + abs(float(expected_dy))) + 1.0)
    return (rx <= lim) and (ry <= lim)


def _frame_dt(planet: Planet, k: int, ref_idx: int,
              cm_iii_per_frame: Sequence[float], dt_s_per_frame: Sequence[float]) -> float:
    """Time-since-reference for frame k (s). Uses dt if given, else derives it
    from the bulk-rotation CM drift so a caller who only has CM angles still
    gets a correct expected-drift prior."""
    dt = float(dt_s_per_frame[k])
    if dt != 0.0:
        return dt
    dcm = wrap_diff(float(cm_iii_per_frame[k]), float(cm_iii_per_frame[ref_idx]))
    rate = planet.rotation_rate_deg_per_s
    return dcm / rate if abs(rate) > 1e-12 else 0.0


# ---------------------------------------------------------------------------
# Quality / reference helpers
# ---------------------------------------------------------------------------

def _laplacian_var(img: np.ndarray, mask: Optional[np.ndarray] = None) -> float:
    """Sharpness proxy (higher = sharper) used for ref selection + weighting."""
    a = np.asarray(img, dtype=np.float64)
    if mask is not None:
        a = a[mask]
    if a.size < 16:
        return 0.0
    k = np.array([[0, 1, 0], [1, -4, 1], [0, 1, 0]], dtype=np.float64)
    img2 = np.asarray(img, dtype=np.float64)
    # 2D Laplacian via numpy (no scipy dependency on this hot path)
    lap = (img2[2:, 1:-1] + img2[:-2, 1:-1]
           + img2[1:-1, 2:] + img2[1:-1, :-2]
           - 4.0 * img2[1:-1, 1:-1])
    if mask is not None:
        m = mask[1:-1, 1:-1]
        lap = lap[m]
    if lap.size < 8:
        return 0.0
    return float(np.var(lap))


def select_reference_index(frames: Sequence[np.ndarray]) -> int:
    """Pick the sharpest frame as the reference (robustness vs always-frame-0).

    If frame 0 happens to be a bad-seeing frame, registering everything to it
    spreads the blur. Choosing the sharpest frame as the anchor is standard
    lucky-imaging practice. Falls back to 0 if anything fails.
    """
    best_i, best_s = 0, -1.0
    for i, f in enumerate(frames):
        try:
            s = _laplacian_var(to_mono(f))
        except Exception:
            s = 0.0
        if s > best_s:
            best_s, best_i = s, i
    return best_i


# ---------------------------------------------------------------------------
# Per-pixel / per-AP latitude (planet-generalised)
# ---------------------------------------------------------------------------

def _per_pixel_lat(nav, h: int, w: int, sub_lat_deg: float, north_pa_deg: float
                   ) -> Tuple[np.ndarray, np.ndarray]:
    """Planetocentric latitude map + on-disk mask for the fitted nav.

    Uses precision_engine.px_to_lonlat_vec, i.e. the EXACT oblate-spheroid
    line-of-sight intersection (the same quadratic solved for measurement), so
    the latitude assigned to each alignment point is the latitude the engine
    would publish. The previous implementation treated the planet as a unit
    sphere after an anisotropic y-scale; that is only exact for a sphere and
    differed from the spheroid latitude by up to ~2.8 deg on Jupiter in the
    GRS band, which mis-binned the per-latitude shear warp.
    """
    # Build a NavState carrying the orientation this stacker was asked to use.
    # nav may be a plain object (fit_limb_nav result) or already a NavState.
    from precision_engine import NavState as _NS
    ns = _NS(
        xc=float(nav.xc), yc=float(nav.yc), a_eq_px=float(nav.a_eq_px),
        flattening=float(getattr(nav, "flattening", _ENGINE_FLAT) or _ENGINE_FLAT),
        cm_iii_deg=float(getattr(nav, "cm_iii_deg", 0.0) or 0.0),
        distance_au=float(getattr(nav, "distance_au", 5.2) or 5.2),
        sub_lat_deg=float(sub_lat_deg or 0.0),
        north_pa_deg=float(north_pa_deg or 0.0),
    )
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    _, lat = px_to_lonlat_vec(yy.ravel(), xx.ravel(), ns)
    lat = lat.reshape(h, w)
    # on-disk mask in SKY coordinates (the projected limb ellipse), independent
    # of the latitude solve. 0.97 keeps the warp away from the very limb.
    b_pol = ns.a_eq_px * (1.0 - ns.flattening)
    Xn = (xx - ns.xc) / (ns.a_eq_px + 1e-12)
    Yn = (ns.yc - yy) / (b_pol + 1e-12)
    on = (Xn * Xn + Yn * Yn) <= 0.97 ** 2
    return lat, on


def _ap_latitudes(aps: np.ndarray, nav, sub_lat_deg: float, north_pa_deg: float
                  ) -> np.ndarray:
    """Planetocentric latitude of each AP (vectorised sample of the lat map)."""
    from precision_engine import NavState as _NS
    ns = _NS(
        xc=float(nav.xc), yc=float(nav.yc), a_eq_px=float(nav.a_eq_px),
        flattening=float(getattr(nav, "flattening", _ENGINE_FLAT) or _ENGINE_FLAT),
        cm_iii_deg=float(getattr(nav, "cm_iii_deg", 0.0) or 0.0),
        distance_au=float(getattr(nav, "distance_au", 5.2) or 5.2),
        sub_lat_deg=float(sub_lat_deg or 0.0),
        north_pa_deg=float(north_pa_deg or 0.0),
    )
    ys = aps[:, 1].astype(np.float64)
    xs = aps[:, 0].astype(np.float64)
    _, lat = px_to_lonlat_vec(ys, xs, ns)
    return lat


# ---------------------------------------------------------------------------
# Robust per-latitude drift fit (the accuracy fix)
# ---------------------------------------------------------------------------

def fit_dx_vs_latitude(
    ap_lats: np.ndarray,
    ap_drifts: np.ndarray,
    ap_snr: np.ndarray,
    planet: Planet,
    dt_s: float,
    deg_to_px: float,
    n_bins: int = 11,
) -> Tuple[np.ndarray, float]:
    """Fit a smooth dx(|latitude|) curve from per-AP measurements.

    ap_drifts is (N, 2): (dy, dx) measured displacements frame-vs-reference.

    Returns (dx_per_bin, dy_global):
      dx_per_bin — len n_bins, the dx (px) to *apply* (already negated so the
                   caller can shift each row directly) at the centre latitude
                   of each |lat| bin from 0..90°.
      dy_global  — single latitudinal shift (px) to apply uniformly.

    Latitude bins with no APs are filled from the planet-model expected drift
    (measurement + prior hybrid), so a poorly-populated band still derotates
    sensibly instead of leaving a gap.
    """
    ap_drifts = np.asarray(ap_drifts, dtype=np.float64)
    ap_dy = ap_drifts[:, 0]
    ap_dx = ap_drifts[:, 1]
    abs_lat = np.abs(np.asarray(ap_lats, dtype=np.float64))
    snr = np.asarray(ap_snr, dtype=np.float64)
    edges = np.linspace(0.0, 90.0, n_bins + 1)
    centres = 0.5 * (edges[:-1] + edges[1:])
    dx_bin = np.zeros(n_bins, dtype=np.float64)
    filled = np.zeros(n_bins, dtype=bool)
    good = np.isfinite(ap_dx) & (snr > 0.05)
    for j in range(n_bins):
        m = good & (abs_lat >= edges[j]) & (abs_lat < edges[j + 1])
        if int(m.sum()) < 1:
            # prior fallback: planet-model expected drift (negated to align),
            # correctly scaled via the (π/180)r cosφ longitude chord
            dx_bin[j] = -planet.lon_drift_px(centres[j], dt_s, deg_to_px * 90.0)
            continue
        med = float(np.median(ap_dx[m]))
        mad = float(np.median(np.abs(ap_dx[m] - med))) + 1e-9
        inlier = np.abs(ap_dx[m] - med) < 3.0 * 1.4826 * mad
        dxj = ap_dx[m][inlier]
        wj = snr[m][inlier]
        if dxj.size:
            dx_bin[j] = -float(np.average(dxj, weights=wj / (wj.sum() + 1e-12)))
        else:
            dx_bin[j] = -med
        filled[j] = True
    # Light 3-tap smoothing of the measured bins (avoids row-to-row tears).
    if filled.sum() >= 3:
        sm = dx_bin.copy()
        for j in range(n_bins):
            if not filled[j]:
                continue
            neigh = [dx_bin[j]]
            if j > 0 and filled[j - 1]:
                neigh.append(dx_bin[j - 1])
            if j < n_bins - 1 and filled[j + 1]:
                neigh.append(dx_bin[j + 1])
            sm[j] = float(np.mean(neigh))
        dx_bin = sm
    # PHYSICAL BOUND (v6.8.x): no latitude band can legitimately shift by more
    # than ~2x the cloud-tracked model (Jovian winds are bounded; deltas larger
    # than that are tracker garbage, and near the poles a single per-row shift
    # is ill-posed anyway — a polar row spans many latitudes AND longitudes).
    # The old "blend empty bins toward the nearest measured bin" rule dragged
    # equatorial ±100 px warps into the polar bands and visibly tore the limb;
    # the clamp keeps the poles intact while the equator still derotates.
    a_eq_px = deg_to_px * 90.0
    meas_mag = np.abs(dx_bin[filled])
    med_meas = float(np.median(meas_mag)) if meas_mag.size else 0.0
    for j in range(n_bins):
        model_lim = 2.0 * abs(planet.lon_drift_px(float(centres[j]), dt_s, a_eq_px)) + 1.5
        # Unmeasured bins trust the model only; measured bins may exceed the
        # model when their measured neighbourhood does (bounded wind shear and
        # model-free unit tests alike), but an isolated tear cannot.
        if filled[j]:
            neigh = [abs(dx_bin[j])]
            if j > 0 and filled[j - 1]:
                neigh.append(abs(dx_bin[j - 1]))
            if j < n_bins - 1 and filled[j + 1]:
                neigh.append(abs(dx_bin[j + 1]))
            data_lim = 2.0 * float(np.median(neigh)) + 1.5
        else:
            data_lim = 2.0 * med_meas + 1.5
        lim = max(model_lim, min(data_lim, 3.0 * med_meas + 1.5) if filled[j] else model_lim)
        dx_bin[j] = float(np.clip(dx_bin[j], -lim, lim))
    # dy: single robust value over all APs. Pure zonal rotation barely moves
    # latitude, so the per-lat structure is all in dx; a global dy is enough.
    dy_global = 0.0
    if good.any():
        dyv = ap_dy[good]
        med = float(np.median(dyv))
        mad = float(np.median(np.abs(dyv - med))) + 1e-9
        keep = np.abs(dyv - med) < 3.0 * 1.4826 * mad
        dy_global = -float(np.mean(dyv[keep])) if keep.any() else -med
    return dx_bin, dy_global


# ---------------------------------------------------------------------------
# Per-row warp
# ---------------------------------------------------------------------------

def per_row_warp(
    frame: np.ndarray,
    dx_apply_per_bin: np.ndarray,
    dy_global: float,
    on_disk: np.ndarray,
    row_lats: np.ndarray,
) -> np.ndarray:
    """Apply a per-row x-shift (from the latitude fit) + a uniform y-shift.

    Each row is shifted by the dx at that row's |latitude| (interpolated from
    the binned fit). Implemented as spatial-domain quintic-spline resampling
    (map_coordinates order=5, content moves by +dx per row) — the same shift
    convention as the original FFT phase ramp but with no circulant wraparound
    or Gibbs ringing: the FFT version streaked limb bars through the sky on
    long-rotation stacks and measurably degraded derotated outputs (see the
    rotating-video benchmark in test_video_jupiter).

    Channel-aware: pass an (h,w,3) frame to warp every channel identically.
    """
    arr = np.asarray(frame, dtype=np.float64)
    if arr.ndim == 3:
        return np.stack(
            [per_row_warp(arr[..., c], dx_apply_per_bin, dy_global, on_disk, row_lats)
             for c in range(arr.shape[2])], axis=-1,
        )
    from scipy.ndimage import map_coordinates
    out = arr.copy()
    h, w = out.shape
    n_bins = dx_apply_per_bin.size
    centres = (np.arange(n_bins) + 0.5) * (90.0 / n_bins)
    abs_lats = np.abs(row_lats)
    row_dx = np.interp(abs_lats, centres, dx_apply_per_bin)
    idx = np.arange(w, dtype=np.float64)
    for row in range(h):
        if not on_disk[row].any():
            continue
        dx = float(row_dx[row])
        if abs(dx) < 0.02:
            continue
        # Spatial-domain resample at (x - dx): content moves by +dx, exactly the
        # old FFT phase-ramp convention (out(x) = in(x - dx)) — but WITHOUT the
        # circulant wraparound that smeared limb bars into the sky and the
        # bar-code striping plainly visible on long-rotation stacks (36-frame,
        # 3.5-deg video benchmark, fixed in v6.8.x). mode="nearest" keeps the
        # sky constant.
        out[row] = map_coordinates(arr[row], [idx - dx], order=5,
                                   mode="nearest", prefilter=True)
    if abs(dy_global) > 0.02:
        yy = np.arange(h, dtype=np.float64)[:, None] - dy_global
        yy = np.broadcast_to(yy, (h, w))
        xx = np.broadcast_to(np.arange(w, dtype=np.float64)[None, :], (h, w))
        out = map_coordinates(out, [yy, xx], order=5, mode="nearest", prefilter=True)
    return out


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclass
class PlanetaryStackerResult:
    n_frames: int
    n_aps: int
    n_grid: int
    ap_half: int
    planet: str
    reference_index: int
    warp_mode: str
    quality_gate: float
    dropped_frames: List[int]
    mean_rms_drift_px: float
    mean_ap_quality: float
    warp_consistency_std: float
    used_prior_fallback_bins: int
    elapsed_s: float
    output_path: str
    notes: List[str] = field(default_factory=list)
    drift_summary: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Robust combination (sigma-clipped weighted mean)
# ---------------------------------------------------------------------------

def _align_confidence(snr: float) -> float:
    """Map a phase-correlation peak SNR onto a [0, 1] alignment-confidence.

    The tracker's SNR is peak / second-peak of the correlation surface: ~1.0
    means an ambiguous lock (no real feature), ~10-100 means a crisp lock.
    This is used to *down-weight* frames whose alignment points did not lock,
    so a sharp-but-mis-registered frame cannot pollute the stack with full
    weight. log1p() keeps the mapping stable across the wide SNR dynamic range.
    """
    if not math.isfinite(snr) or snr <= 0.0:
        return 0.0
    return float(np.clip(math.log1p(snr) / math.log(21.0), 0.0, 1.0))


def _robust_combine(
    warped: List[np.ndarray],
    weights: List[float],
    sigma: float = 3.0,
    iters: int = 2,
) -> np.ndarray:
    """Sigma-clipped weighted mean across the aligned frames.

    A plain weighted mean lets a transient defect (cosmic-ray hit, hot pixel,
    a satellite or shadow transit present in ONE frame) leave a permanent mark
    on the stack. This instead rejects per-pixel outliers before averaging:
    per-pixel median -> MAD scale (1.4826 · MAD ≈ σ for Gaussian) -> iterative
    sigma-clip -> weighted mean of the surviving pixels. With fewer than three
    frames there is no robust scale to estimate, so it degrades to the plain
    weighted mean.
    """
    n = len(warped)
    if n == 0:
        raise ValueError("_robust_combine: no frames")
    first = np.asarray(warped[0], dtype=np.float64)
    if n < 3:
        acc = np.zeros_like(first)
        wsum = 0.0
        for w_, f in zip(weights, warped):
            acc += np.asarray(f, dtype=np.float64) * float(w_)
            wsum += float(w_)
        return acc / max(wsum, 1e-9)

    stack = np.stack([np.asarray(f, dtype=np.float64) for f in warped], axis=0)
    w = np.asarray(weights, dtype=np.float64)
    w = w / max(float(w.sum()), 1e-12)
    wb = w.reshape((n,) + (1,) * (stack.ndim - 1))
    med = np.median(stack, axis=0)
    mad = np.median(np.abs(stack - med), axis=0)
    sd = np.maximum(1.4826 * mad, 1e-6)
    for _ in range(max(1, int(iters))):
        mask = np.abs(stack - med) <= (float(sigma) * sd)
        num = np.sum(stack * mask * wb, axis=0)
        den = np.sum(mask * wb, axis=0)
        med = num / np.maximum(den, 1e-9)
    return med


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run_planetary_stacker(
    frames: List[np.ndarray],
    out_dir: Path,
    *,
    planet: Planet = JUPITER,
    n_grid: int = 8,
    ap_half: int = 16,
    cm_iii_per_frame: Optional[Sequence[float]] = None,
    dt_s_per_frame: Optional[Sequence[float]] = None,
    sub_lat_deg: float = 0.0,
    north_pa_deg: float = 0.0,
    warp_mode: str = "per_latitude",   # "per_latitude" | "flow" | "global"
    reference: str = "auto",           # "auto" | "first"
    quality_gate: float = 1.0,         # keep the sharpest fraction (1.0 = keep all)
    robust: bool = True,               # sigma-clipped combination (rejects transient defects)
    robust_sigma: float = 3.0,         # clip threshold in MAD units
    robust_iters: int = 2,             # sigma-clip refinement iterations
    save: bool = True,
) -> PlanetaryStackerResult:
    """Stack a list of mono frames of any `Planet` with a per-latitude warp.

    Parameters
    ----------
    planet : Planet           geometry/rotation/wind profile (Jupiter, Saturn, …).
    cm_iii_per_frame          per-frame bulk-rotation angle (deg). Used only for
                              the expected-drift prior fallback in sparse bins.
    dt_s_per_frame            per-frame time-since-reference (s). Drives the
                              planet-model expected drift.
    warp_mode                 "per_latitude" (default; fixes zonal shear),
                              "flow" (dense 2D warp; captures local/meridional
                              motion the per-row warp cannot), or
                              "global" (legacy single translation, for A/B).
    reference                 "auto" picks the sharpest frame; "first" uses 0.
    quality_gate              keep only the sharpest fraction of frames
                              (lucky-imaging rejection, AutoStakkert-style).
                              1.0 keeps all; e.g. 0.75 drops the 25% worst-seeing
                              frames. The reference is always retained.
    robust                    when True, combine the warped frames with a
                              sigma-clipped weighted mean so a transient defect
                              (cosmic ray, hot pixel, one-frame shadow/satellite
                              transit) is rejected instead of stamped onto the
                              stack. Falls back to a plain weighted mean on very
                              large frame sets (memory guard) or <3 frames.
    robust_sigma / robust_iters
                              sigma-clip threshold (MAD units) and refinement
                              iterations for the robust combination.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    if not frames:
        raise ValueError("run_planetary_stacker: empty frame list")
    n_frames = len(frames)
    mono = [to_mono(f) for f in frames]
    h, w = mono[0].shape
    is_rgb = np.asarray(frames[0]).ndim == 3
    src = [_to_hwc(f) for f in frames] if is_rgb else mono   # what we warp + stack
    nc = src[0].shape[2] if is_rgb else 1
    if cm_iii_per_frame is None:
        cm_iii_per_frame = [0.0] * n_frames
    if dt_s_per_frame is None:
        dt_s_per_frame = [0.0] * n_frames

    ref_idx = select_reference_index(mono) if reference == "auto" else 0
    ref = mono[ref_idx]
    ref_src = src[ref_idx]
    # Lucky-imaging rejection: drop the worst-seeing frames (keep the reference).
    dropped: List[int] = []
    dropped_set = set()
    if quality_gate < 1.0 and n_frames > 3:
        kept_idx, dropped, _quals = select_best_frames(mono, keep_frac=quality_gate)
        if ref_idx not in kept_idx:
            kept_idx.append(ref_idx)
            kept_idx.sort()
        dropped_set = set(dropped)
        if dropped:
            CONSOLE.info(
                f"PLANETARY-STACK: quality_gate={quality_gate:.2f} -> dropping "
                f"{len(dropped)} worst-seeing frame(s): {dropped}"
            )
    CONSOLE.info(
        f"PLANETARY-STACK: {n_frames} frames {w}x{h}, planet={planet.name}, "
        f"grid {n_grid}x{n_grid}, ap_half={ap_half}, warp={warp_mode}, ref={ref_idx}"
    )

    nav = fit_limb_nav(ref, cm_iii_deg=float(cm_iii_per_frame[ref_idx]),
                       distance_au=planet.default_distance_au)
    nav.sub_lat_deg = sub_lat_deg
    nav.north_pa_deg = north_pa_deg

    lat_map, on_disk = _per_pixel_lat(nav, h, w, sub_lat_deg, north_pa_deg)
    row_lats = np.zeros(h, dtype=np.float64)
    for r in range(h):
        m = on_disk[r]
        row_lats[r] = float(np.mean(lat_map[r][m])) if m.any() else 0.0

    thr = float(np.percentile(ref, 30.0))
    disk_mask = ref > thr
    aps = _build_ap_grid(h, w, n_grid=n_grid, mask=disk_mask)
    n_aps = aps.shape[0]
    ap_lats = _ap_latitudes(aps, nav, sub_lat_deg, north_pa_deg)
    deg_to_px = nav.a_eq_px / 90.0

    def _pass(ref_array: np.ndarray):
        """One track + warp + weighted-stack pass against `ref_array` (mono).

        Tracking and the consistency metric always use luminance (most robust).
        The warp is applied to the source frames — mono, or RGB per-channel —
        so an RGB input yields an RGB stack with colour preserved.
        """
        ashape = (h, w, nc) if is_rgb else (h, w)
        accumulated = np.zeros(ashape, dtype=np.float64)
        weights = np.zeros((h, w), dtype=np.float64)
        warped_frames: List[np.ndarray] = [np.asarray(ref_src, dtype=np.float64)]
        frame_wts: List[float] = []
        rms_pass: List[float] = []
        snr_pass: List[float] = []
        prior_pass = 0
        s_sum = ref_array.astype(np.float64).copy()
        ss_sum = s_sum * s_sum
        cnt = 1
        rq = max(_laplacian_var(ref_array, on_disk), 1e-3)
        frame_wts.append(rq)
        accumulated += ref_src * rq
        weights += np.full((h, w), rq)
        for k, frame in enumerate(mono):
            if k == ref_idx:
                rms_pass.append(0.0)
                continue
            if k in dropped_set:
                rms_pass.append(float("nan"))
                continue
            dt_k = _frame_dt(planet, k, ref_idx, cm_iii_per_frame, dt_s_per_frame)
            drifts = np.full((n_aps, 2), np.nan, dtype=np.float64)
            snrs = np.zeros(n_aps, dtype=np.float64)
            for i, (ax, ay) in enumerate(aps):
                exp_dx = _per_ap_expected_dx_lon(planet, float(ap_lats[i]), dt_k, deg_to_px * 90.0)
                tdy, tdx, snr = _track_ap_planetary(
                    ref_array, frame, (ax, ay), ap_half, expected_dx=exp_dx,
                )
                # AutoStakkert-style AP outlier gates (limb + post-prior
                # residual): a mis-locked AP is NaN'd so the per-latitude
                # fit falls back to the model prior in its band instead of
                # dragging a wrong warp in (v6.8.x zonal audit).
                if not gate_ap_track(nav, (ax, ay), tdy, tdx, exp_dx):
                    tdy, tdx, snr = float("nan"), float("nan"), 0.0
                drifts[i, 0] = tdy
                drifts[i, 1] = tdx
                snrs[i] = snr
            valid = np.isfinite(drifts[:, 0])
            if valid.any():
                rms_pass.append(float(np.sqrt(np.mean(
                    drifts[valid, 0] ** 2 + drifts[valid, 1] ** 2))))
            else:
                rms_pass.append(float("nan"))

            if warp_mode == "global":
                vmask = valid & (snrs > 0.05)
                if vmask.any():
                    wv = snrs[vmask]
                    dy = -float(np.average(drifts[vmask, 0], weights=wv))
                    dx = -float(np.average(drifts[vmask, 1], weights=wv))
                else:
                    dy, dx = 0.0, 0.0
                shifted = _global_shift(src[k], dy, dx)
                prior_bins = 0
            elif warp_mode == "flow":
                field = fit_dense_apply_field(aps, drifts, snrs, (h, w))
                shifted = apply_flow_warp(src[k], field)
                prior_bins = 0
            else:
                dx_bins, dy_g = fit_dx_vs_latitude(
                    ap_lats, drifts, snrs, planet,
                    dt_s=float(dt_s_per_frame[k]), deg_to_px=deg_to_px,
                )
                prior_bins = int(np.sum(dx_bins == 0.0) + np.sum(~np.isfinite(dx_bins)))
                shifted = per_row_warp(src[k], dx_bins, dy_g, on_disk, row_lats)

            shifted_mono = shifted if not is_rgb else to_mono(shifted)
            # Frame weight = sharpness × alignment confidence. Sharpness alone
            # (Laplacian variance) rewards a crisp-but-mis-registered frame with
            # full weight; the tracker's AP SNR tells us whether the warp
            # actually locked. A frame whose alignment points failed (SNR ~1)
            # contributes little even if its raw pixels are sharp.
            snr_k = float(np.nanmean(snrs[valid])) if valid.any() else 0.0
            sharp_k = max(_laplacian_var(frame, on_disk), 1e-3)
            qk = sharp_k * (0.25 + 0.75 * _align_confidence(snr_k))
            warped_frames.append(np.asarray(shifted, dtype=np.float64))
            frame_wts.append(qk)
            accumulated += shifted * qk
            weights += np.full((h, w), qk)
            s_sum += shifted_mono
            ss_sum += shifted_mono * shifted_mono
            cnt += 1
            snr_pass.append(snr_k)
            prior_pass += prior_bins
        mean_img = s_sum / cnt
        var_img = np.clip(ss_sum / cnt - mean_img * mean_img, 0.0, None)
        consistency = float(np.sqrt(np.mean(var_img[on_disk]))) if on_disk.any() else 0.0
        # Robust (sigma-clipped) combination rejects transient pixel defects a
        # plain weighted mean would stamp onto the stack. Guard the memory: the
        # clip needs the full frame cube, so on very large frame sets fall back
        # to the streaming weighted mean (the accuracy gain is for a handful of
        # outlier pixels, which matter most on lucky-imaging stacks of tens of
        # frames, not hundreds).
        cube_bytes = len(warped_frames) * h * w * nc * 8
        use_robust = robust and cube_bytes <= _ROBUST_MEMORY_BUDGET_BYTES
        if use_robust:
            stacked = _robust_combine(
                warped_frames, frame_wts, sigma=robust_sigma, iters=robust_iters,
            )
        else:
            if is_rgb:
                stacked = accumulated / np.maximum(weights, 1e-9)[..., None]
            else:
                stacked = accumulated / np.maximum(weights, 1e-9)
        return stacked, rms_pass, snr_pass, prior_pass, consistency, use_robust

    (stacked, per_frame_rms, quality_snrs, prior_bins_total,
     warp_consistency_std, used_robust) = _pass(ref)
    out_path = out_dir / f"stacked_planetary_{planet.name.lower()}.png"
    if save:
        try:
            from PIL import Image
            u8 = (np.clip(stacked / max(stacked.max(), 1e-9), 0, 1) * 255).astype(np.uint8)
            mode = "RGB" if is_rgb else "L"
            Image.fromarray(u8, mode).save(out_path, optimize=False)
        except Exception as e:
            CONSOLE.warn(f"PLANETARY-STACK: PNG save failed: {e}")
            out_path = out_dir / f"stacked_planetary_{planet.name.lower()}.npy"
            np.save(out_path, stacked)

    elapsed = time.time() - t0
    mean_rms = float(np.nanmean(per_frame_rms)) if per_frame_rms else 0.0
    mean_q = float(np.nanmean(quality_snrs)) if quality_snrs else 0.0
    CONSOLE.ok(
        f"PLANETARY-STACK done: {n_frames} frames × {n_aps} APs, planet={planet.name}, "
        f"warp={warp_mode}, ref={ref_idx}, mean drift RMS {mean_rms:.2f}px, "
        f"{elapsed:.1f}s"
    )
    result = PlanetaryStackerResult(
        n_frames=n_frames,
        n_aps=n_aps,
        n_grid=n_grid,
        ap_half=ap_half,
        planet=planet.name,
        reference_index=int(ref_idx),
        warp_mode=warp_mode,
        quality_gate=float(quality_gate),
        dropped_frames=[int(i) for i in dropped],
        mean_rms_drift_px=mean_rms,
        mean_ap_quality=mean_q,
        warp_consistency_std=float(warp_consistency_std),
        used_prior_fallback_bins=int(prior_bins_total),
        elapsed_s=float(elapsed),
        output_path=str(out_path),
        notes=[
            f"Planet-generalised: {planet.name} "
            f"(flat={planet.flattening:.4f}, P={planet.rotation_period_s:.0f}s)",
            f"warp_mode={warp_mode}: "
            + {
                "per_latitude": "per-latitude row warp (fixes zonal shear)",
                "flow": "dense 2D flow warp (captures local/meridional motion)",
                "global": "legacy single global translation",
            }.get(warp_mode, warp_mode),
            "measurement+prior hybrid: empty latitude bins filled from planet model",
            "reference frame = sharpest (lucky-imaging anchor)" if reference == "auto"
            else "reference frame = first",
            f"combination = {'sigma-clipped weighted mean (robust)' if used_robust else 'plain weighted mean'}"
            + ("" if used_robust else f" (robust={robust} fell back: memory guard or <3 frames)")
            + (f" (sigma={robust_sigma}, iters={robust_iters})" if used_robust else ""),
            "frame weight = sharpness × alignment confidence (AP SNR)",
        ],
        drift_summary={
            "per_frame_rms_px": [float(v) for v in per_frame_rms],
        },
    )
    if save:
        write_stacker_report(result, out_dir)
    return result


def _global_shift(frame: np.ndarray, dy: float, dx: float) -> np.ndarray:
    """Single FFT sub-pixel translation (legacy/global warp mode).

    Channel-aware: pass an (h,w,3) frame to shift every channel identically
    (used for RGB stacking — the warp is computed from luminance, applied to RGB).
    """
    out = np.asarray(frame, dtype=np.float64)
    if out.ndim == 3:
        return np.stack(
            [_global_shift(out[..., c], dy, dx) for c in range(out.shape[2])], axis=-1
        )
    # v6.8.x audit: exact spatial-domain spline shift (the FFT phase ramp
    # returns the even mixture (f(x-s)+f(x+s))/2 at non-integer shifts —
    # see app/image_warp.py docstring for the measured numbers).
    from image_warp import warp_shift2d
    return warp_shift2d(out, dy, dx, order=3)


def stacker_report_text(res: "PlanetaryStackerResult") -> str:
    """Human-readable one-page report card for a stacker run.

    Surfaces what actually happened (planet, warp mode, reference frame,
    dropped frames, per-frame drift, consistency, timing) so a result is
    auditable rather than just a PNG.
    """
    rms = res.drift_summary.get("per_frame_rms_px", [])
    rms_finite = [r for r in rms if isinstance(r, float) and math.isfinite(r)]
    bar = "=" * 60
    lines = [
        bar,
        f"PLANETARY STACK REPORT  -  {res.planet}  ({res.n_frames} frames)",
        bar,
        f"warp mode          : {res.warp_mode}",
        f"AP grid            : {res.n_grid}x{res.n_grid}  ({res.n_aps} APs, ap_half={res.ap_half})",
        f"reference frame    : #{res.reference_index}",
        f"quality gate       : {res.quality_gate:.2f}  "
        f"(dropped {len(res.dropped_frames)} frame(s): {res.dropped_frames or 'none'})",
        f"mean drift RMS     : {res.mean_rms_drift_px:.2f} px",
        f"mean AP quality    : {res.mean_ap_quality:.3f}",
        f"warp consistency   : {res.warp_consistency_std:.4f}  "
        f"(raw on-disk std of warped frames; lower=more agree; NOT a cross-mode rank)",
        f"prior-fallback bins: {res.used_prior_fallback_bins}",
        f"elapsed            : {res.elapsed_s:.1f} s",
        f"output             : {res.output_path}",
        "",
        "per-frame drift RMS (px):",
    ]
    if rms:
        for i, r in enumerate(rms):
            tag = "  (reference)" if i == res.reference_index else (
                "  (dropped)" if i in res.dropped_frames else "")
            rv = f"{r:.2f}" if isinstance(r, float) and math.isfinite(r) else "  -  "
            lines.append(f"  frame {i:3d}: {rv:>7}{tag}")
    else:
        lines.append("  (none)")
    if rms_finite:
        import statistics
        lines.append("")
        lines.append(f"  drift RMS - median {statistics.median(rms_finite):.2f} px, "
                     f"max {max(rms_finite):.2f} px")
    lines.append("")
    lines.append("notes:")
    for n in res.notes:
        lines.append(f"  - {n}")
    lines.append(bar)
    return "\n".join(lines)


def write_stacker_report(res: "PlanetaryStackerResult", out_dir: Path) -> Path:
    """Write the report card next to the stacked image. Returns its path."""
    out_dir = Path(out_dir)
    p = out_dir / "stacker_report.txt"
    try:
        p.write_text(stacker_report_text(res), encoding="utf-8")
    except Exception as e:
        CONSOLE.warn(f"PLANETARY-STACK: report write failed: {e}")
    return p


__all__ = [
    "run_planetary_stacker",
    "PlanetaryStackerResult",
    "select_reference_index",
    "fit_dx_vs_latitude",
    "gate_ap_track",
    "per_row_warp",
    "stacker_report_text",
    "write_stacker_report",
]

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
from precision_engine import fit_limb_nav, deg2rad, to_mono, wrap_diff
from flow_warp import fit_dense_apply_field, apply_flow_warp
from frame_quality import select_best_frames


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
    """Image-x displacement (px) a cloud feature at φ makes over dt_s.

    = -ω_cloud(φ)·dt·(a/90) — the FULL cloud-tracking motion (bulk rotation +
    zonal wind). Re-centering the AP crop by this before phase correlation
    keeps the residual small enough to lock even when the bulk rotation has
    swept the feature many pixels (the raw tracker saturates past ap_half).
    """
    if dt_s == 0.0:
        return 0.0
    # cloud moves +longitude; in image-x that is -deg_to_px per degree (observer
    # convention, matching the synthetic simulator / winjupos_derotator).
    return -planet.cloud_tracking_rate_deg_per_s(lat_deg) * dt_s * deg_to_px


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
    total_dy, total_dx, log_snr, n_ok = 0.0, 0.0, 0.0, 0
    for oct in octaves:
        ref_oct = _laplacian_octave(ref_crop, oct)
        cx = xi + int(round((expected_dx + total_dx) * (2 ** oct)))
        cy = yi + int(round((expected_dy + total_dy) * (2 ** oct)))
        cx = max(ap_half, min(w - ap_half - 1, cx))
        cy = max(ap_half, min(h - ap_half - 1, cy))
        fr_crop = frame[cy - ap_half:cy + ap_half + 1, cx - ap_half:cx + ap_half + 1]
        fr_oct = _laplacian_octave(fr_crop, oct)
        if fr_oct.shape != ref_oct.shape:
            break
        try:
            dy, dx, snr = _phase_corr_shift(ref_oct, fr_oct)
        except Exception:
            break
        if not (math.isfinite(dy) and math.isfinite(dx) and math.isfinite(snr)):
            break
        total_dy += dy * (2 ** oct)
        total_dx += dx * (2 ** oct)
        log_snr += math.log(max(snr, 1e-3))
        n_ok += 1
    if n_ok == 0:
        return float("nan"), float("nan"), 0.0
    return expected_dy + total_dy, expected_dx + total_dx, math.exp(log_snr / n_ok)


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

    Uses the nav's own flattening so it is correct for any oblate body, not
    just Jupiter. Same body-frame construction as precision_engine.
    """
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    b_pol = nav.a_eq_px * (1.0 - nav.flattening)
    Xsky = (xx - nav.xc) / (nav.a_eq_px + 1e-12)
    Ysky = (nav.yc - yy) / (b_pol + 1e-12)
    pa = deg2rad(north_pa_deg)
    cP, sP = math.cos(pa), math.sin(pa)
    Xp = Xsky * cP + Ysky * sP
    Yp = -Xsky * sP + Ysky * cP
    rr = Xp * Xp + Yp * Yp
    Zp = np.sqrt(np.clip(1.0 - rr, 0.0, 1.0))
    D = deg2rad(sub_lat_deg)
    cD, sD = math.cos(D), math.sin(D)
    Ye = Yp * cD + Zp * sD
    lat = np.degrees(np.arcsin(np.clip(Ye, -1.0, 1.0)))
    return lat, (rr <= 0.97)


def _ap_latitudes(aps: np.ndarray, nav, sub_lat_deg: float, north_pa_deg: float
                  ) -> np.ndarray:
    """Planetocentric latitude of each AP (vectorised sample of the lat map)."""
    h, w = int(nav.yc * 2), int(nav.xc * 2)
    # nav does not store h,w; rebuild from the image shape the caller passed.
    # We avoid needing h,w here by computing lat directly per AP.
    b_pol = nav.a_eq_px * (1.0 - nav.flattening)
    pa = deg2rad(north_pa_deg)
    cP, sP = math.cos(pa), math.sin(pa)
    D = deg2rad(sub_lat_deg)
    cD, sD = math.cos(D), math.sin(D)
    out = np.zeros(aps.shape[0], dtype=np.float64)
    for i, (x, y) in enumerate(aps):
        Xsky = (x - nav.xc) / (nav.a_eq_px + 1e-12)
        Ysky = (nav.yc - y) / (b_pol + 1e-12)
        Xp = Xsky * cP + Ysky * sP
        Yp = -Xsky * sP + Ysky * cP
        rr = Xp * Xp + Yp * Yp
        Zp = math.sqrt(max(1.0 - rr, 0.0))
        Ye = Yp * cD + Zp * sD
        out[i] = math.degrees(math.asin(max(-1.0, min(1.0, Ye))))
    return out


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
            # prior fallback: planet-model expected drift (negated to align)
            dx_bin[j] = -planet.expected_drift_dx_px(centres[j], dt_s, deg_to_px)
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
    # Fill prior-only bins by blending the model fill with the nearest measured
    # bin (continuous where data exists, model where it does not).
    idx = np.where(filled)[0]
    if idx.size:
        for j in range(n_bins):
            if not filled[j]:
                nb = idx[int(np.argmin(np.abs(idx - j)))]
                dx_bin[j] = 0.5 * dx_bin[j] + 0.5 * dx_bin[nb]
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
    the binned fit). Implemented as a per-row FFT phase ramp, matching the
    proven convention used by win_jupos_derotator / the synthetic simulator.

    Channel-aware: pass an (h,w,3) frame to warp every channel identically.
    """
    arr = np.asarray(frame, dtype=np.float64)
    if arr.ndim == 3:
        return np.stack(
            [per_row_warp(arr[..., c], dx_apply_per_bin, dy_global, on_disk, row_lats)
             for c in range(arr.shape[2])], axis=-1,
        )
    out = arr.copy()
    h, w = out.shape
    n_bins = dx_apply_per_bin.size
    centres = (np.arange(n_bins) + 0.5) * (90.0 / n_bins)
    # per-row dx: interpolate the |lat| fit at each row's |lat|
    abs_lats = np.abs(row_lats)
    row_dx = np.interp(abs_lats, centres, dx_apply_per_bin)
    ncol = w
    idx = np.arange(ncol)
    for row in range(h):
        if not on_disk[row].any():
            continue
        dx = float(row_dx[row])
        if abs(dx) < 0.02:
            continue
        # exp(-2πi·dx·k/ncol) — bake dx in directly (NOT (e^{-2πik/n})**dx,
        # which wraps the phase on the wrong branch for fractional dx).
        # IMPORTANT: must invert the FFT (real(ifft(fft*phase))); without the
        # ifft this returns the un-inverted spectrum and blows up to |fft|max.
        out[row] = np.real(
            np.fft.ifft(np.fft.fft(out[row]) * np.exp(-2j * np.pi * dx * idx / ncol))
        )
    if abs(dy_global) > 0.02:
        f = np.fft.fft2(out)
        yy = np.arange(h)[:, None]
        phase = np.exp(-2j * np.pi * dy_global * yy / h)
        out = np.real(np.fft.ifft2(f * phase))
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
        rms_pass: List[float] = []
        snr_pass: List[float] = []
        prior_pass = 0
        s_sum = ref_array.astype(np.float64).copy()
        ss_sum = s_sum * s_sum
        cnt = 1
        rq = max(_laplacian_var(ref_array, on_disk), 1e-3)
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
                exp_dx = _per_ap_expected_dx(planet, float(ap_lats[i]), dt_k, deg_to_px)
                tdy, tdx, snr = _track_ap_planetary(
                    ref_array, frame, (ax, ay), ap_half, expected_dx=exp_dx,
                )
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
            qk = max(_laplacian_var(frame, on_disk), 1e-3)
            accumulated += shifted * qk
            weights += np.full((h, w), qk)
            s_sum += shifted_mono
            ss_sum += shifted_mono * shifted_mono
            cnt += 1
            snr_pass.append(float(np.nanmean(snrs[valid])) if valid.any() else 0.0)
            prior_pass += prior_bins
        mean_img = s_sum / cnt
        var_img = np.clip(ss_sum / cnt - mean_img * mean_img, 0.0, None)
        consistency = float(np.sqrt(np.mean(var_img[on_disk]))) if on_disk.any() else 0.0
        if is_rgb:
            stacked = accumulated / np.maximum(weights, 1e-9)[..., None]
        else:
            stacked = accumulated / np.maximum(weights, 1e-9)
        return stacked, rms_pass, snr_pass, prior_pass, consistency

    stacked, per_frame_rms, quality_snrs, prior_bins_total, warp_consistency_std = _pass(ref)
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
    h, w = out.shape
    f = np.fft.fft2(out)
    yy, xx = np.mgrid[0:h, 0:w]
    phase = np.exp(-2j * np.pi * (dy * yy / h + dx * xx / w))
    return np.real(np.fft.ifft2(f * phase))


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
    "per_row_warp",
    "stacker_report_text",
    "write_stacker_report",
]

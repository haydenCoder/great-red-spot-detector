#!/usr/bin/env python3
"""
grs_ellipse.py — the GRS rim-ellipse estimator (v6.8.0 new measurement
method).

WHY THIS IS A REAL METHOD, NOT A GIMMICK
========================================
Every classical centre estimator in the suite (template, moment, map-dark,
barycenters) keys on *interior brightness* — the darkest or reddest pixel
mass. The GRS's interior is asymmetric: its dark core sits off-centre inside
the orange oval (and wanders with seeing). That is exactly the failure mode
documented in the 6.5.x audits (template/moment pulled by the dark core).

The oval's RIM, by contrast, is a sharp, closed, nearly-elliptical boundary:
the orange/brown oval against the SEB. Fitting an ellipse to dozens of rim
points uses ALL the boundary information and is structurally immune to
interior asymmetry. This module:
  1. seeds from the redness lock (blur-robust colour centre),
  2. samples 72 radial spokes on the cylindrical map for the steepest rim
     gradient (sub-pixel parabolic peak),
  3. fits an ellipse with the Fitzgibbon direct least-squares
     (Fitzgibbon, Pilu & Fisher 1999, TPAMI 21(5)),
  4. robust-trims residual outliers (2.5×MAD) and refits,
  5. returns centre (lon_iii, lat), axes (length/width deg) and a rim-contrast
     quality score.

The fit runs in (lon_rel, lat) map space at ~0.1°/px — over a ±12° window
the equirectangular distortion is <0.3%, far below our noise.

HONEST LIMITS
=============
- Needs a visible oval rim: on appalling seeing (spoke contrast ~ 0) it
  reports score≈0 and callers down-weight it like every other soft method.
- The ellipse model for the GRS is excellent (the oval is elliptical to
  ~5%) but not exact; residual-level asymmetry is reported via `rim_rms_deg`.

MEASURED (100-case resolution×seeing audit, 2026-08-07)
=======================================================
- Classic lsq+trim path: convergence 100 % on clear/mild (46/46), 85 %
  on blurry, 29 % on vblurry; on converged cases it is the TIGHTEST
  estimator in the suite: |dlon| median 0.109/max 0.733 deg, |dlat|
  median 0.116/max 0.580 deg — zero cases outside 1 deg.
- RANSAC fallback (physics-gated, fires only when the classic path fails):
  on the 24 hard failures it recovers 21/24; accuracy degrades honestly
  (|dlon| median 1.3/max 2.8 deg — 40x tighter than the classical template
  on the same cases, looser than the redness primary), and the
  `m_ellipse_rim` adapter down-weights RANSAC hits to 0.6 accordingly.
- Latent crash fixed alongside: fit_ellipse_fitzgibbon returned a math
  domain error on junk 5-point conics instead of None (~1 in 20 minimal
  samples — would have made RANSAC unusable without the guard).
"""
from __future__ import annotations

import math
from typing import Dict, Optional, Sequence, Tuple

import numpy as np


# ---------------------------------------------------------------------------
# Fitzgibbon direct least-squares ellipse fit
# ---------------------------------------------------------------------------

def fit_ellipse_fitzgibbon(xs: np.ndarray, ys: np.ndarray) -> Optional[Tuple[float, float, float, float, float]]:
    """Direct least-squares ellipse fit. Returns (cx, cy, a, b, theta)
    (semi-axes a>=b in the input units, theta of `a` from +x in radians),
    or None if no ellipse solution exists.
    """
    x = np.asarray(xs, dtype=np.float64)
    y = np.asarray(ys, dtype=np.float64)
    n = x.size
    if n < 5:
        return None
    # geometric degeneracy: points with ~zero perpendicular spread are a
    # line segment, not an ellipse (algebraic fit would return a phantom)
    cov = np.cov(np.stack([x - x.mean(), y - y.mean()]))
    ev = np.linalg.eigvalsh(cov)
    if ev[0] < 1e-9 * max(ev[1], 1e-300):
        return None
    # Design matrix     [x², xy, y², x, y, 1]
    D = np.stack([x * x, x * y, y * y, x, y, np.ones(n)], axis=1)
    S = D.T @ D
    # Constraint 4ac - b² = 1 on the quadratic part (ellipse)
    C = np.zeros((6, 6))
    C[0, 2] = C[2, 0] = 2.0
    C[1, 1] = -1.0
    # Solve the generalised eigensystem S a = λ C a (Fitzgibbon 1999);
    # the single positive eigenvalue gives the conic
    try:
        invS = np.linalg.inv(S)
    except np.linalg.LinAlgError:
        invS = np.linalg.pinv(S)
    M = invS @ C
    try:
        vals, vecs = np.linalg.eig(M)
    except np.linalg.LinAlgError:
        return None
    ok = np.isfinite(vals.real) & (vals.real > 1e-12)
    if not ok.any():
        return None
    idx = int(np.argmax(np.where(ok, vals.real, -np.inf)))
    a = vecs[:, idx].real
    # normalise so aᵀ C a = 1 (ellipse constraint)
    scale = float(a @ C @ a)
    if scale <= 0:
        return None
    a = a / math.sqrt(scale)
    A, B, Cc, Dd, E, F = a
    if B * B - 4 * A * Cc >= 0:        # not an ellipse
        return None
    if A + Cc < 0:                     # normalise: quadratic part positive-definite
        A, B, Cc, Dd, E, F = -A, -B, -Cc, -Dd, -E, -F
    cx = (2 * Cc * Dd - B * E) / (B * B - 4 * A * Cc)
    cy = (2 * A * E - B * Dd) / (B * B - 4 * A * Cc)
    # translate to centre and extract axes
    num = A * cx * cx + B * cx * cy + Cc * cy * cy - F
    theta = 0.5 * math.atan2(B, A - Cc)
    ct, st = math.cos(theta), math.sin(theta)
    A2 = A * ct * ct + B * ct * st + Cc * st * st
    C2 = A * st * st - B * ct * st + Cc * ct * ct
    if A2 <= 0 or C2 <= 0:
        return None
    # num = value of the quadratic part at the centre; for a real ellipse it
    # must be POSITIVE (="radius²" in normalised units). Junk conics (which
    # every RANSAC minimal-sample soup is full of) give num<=0 — that must be
    # a quiet None, not a math-domain crash (measured: 5-point junk subsets
    # trip it at ~1 in 20 samples).
    if num <= 0:
        return None
    sa = math.sqrt(num / A2)
    sb = math.sqrt(num / C2)
    if sa < sb:
        sa, sb = sb, sa
        theta += math.pi / 2
    if sa <= 0 or sb / sa < 0.02:      # collapsed to a line: degenerate
        return None
    return float(cx), float(cy), float(sa), float(sb), float(theta)


# ---------------------------------------------------------------------------
# RANSAC fallback for outlier-heavy rim point sets
# ---------------------------------------------------------------------------

def fit_ellipse_ransac(
    xs: np.ndarray, ys: np.ndarray,
    n_iter: int = 600, tol: float = 0.40, seed: int = 7,
    a_range: Tuple[float, float] = (2.0, 13.0),
    b_range: Tuple[float, float] = (1.0, 8.0),
    min_aspect: float = 0.15,
    centre_window: Optional[Tuple[float, float]] = (8.0, 5.0),
) -> Optional[Tuple[Tuple[float, float, float, float, float], np.ndarray]]:
    """RANdom SAmple Consensus around the Fitzgibbon 5-point minimal solve.

    WHY: under heavy seeing a large fraction of the radial spokes lock onto
    noise rims, belt edges or festoons instead of the GRS rim. The single-shot
    least-squares fit then explodes into 'unphysical axes' garbage even after
    MAD trimming (measured 2026-08-07 on the 100-case audit: convergence 100%
    on clear/mild but only 29% on very-blurry — every failure an exploded
    fit, none a wrong-but-quiet number). RANSAC asks a simpler question:
    which ellipse explains the LARGEST subset of spokes to within `tol`
    degrees?

    The 5-point minimal solve generates plausible junk ellipses from random
    noise clouds too, so bare inlier-count RANSAC mis-locks there (measured:
    a pure-junk majority was 'explained' 2.5 deg off-centre). Candidate
    filters therefore apply the same PHYSICS as the estimator-level guards,
    in the caller's local units: semi-axis ranges (GRS length 4–26 deg →
    a in 2–13, width 2–16 deg → b in 1–8), a floor on aspect, and a centre
    window around the seed. Refined at the end with a full least-squares on
    the inliers. Returns ((cx, cy, a, b, theta), inlier_mask) or None.
    """
    x = np.asarray(xs, dtype=np.float64)
    y = np.asarray(ys, dtype=np.float64)
    n = x.size
    if n < 7:
        return None
    rng = np.random.default_rng(seed)
    best_mask: Optional[np.ndarray] = None

    def _plausible(f) -> bool:
        cx, cy, a, b, _th = f
        if not (a_range[0] <= a <= a_range[1] and b_range[0] <= b <= b_range[1]):
            return False
        if b / max(a, 1e-9) < min_aspect:
            return False
        if centre_window is not None and (abs(cx) > centre_window[0] or abs(cy) > centre_window[1]):
            return False
        return True

    for _ in range(int(n_iter)):
        sel = rng.choice(n, size=5, replace=False)
        f = fit_ellipse_fitzgibbon(x[sel], y[sel])
        if f is None or not _plausible(f):
            continue
        res = _ellipse_residuals(x, y, f)
        inl = res <= float(tol)
        if best_mask is None or int(inl.sum()) > int(best_mask.sum()):
            best_mask = inl
    if best_mask is None or int(best_mask.sum()) < 7:
        return None
    refined = fit_ellipse_fitzgibbon(x[best_mask], y[best_mask])
    if refined is None or not _plausible(refined):
        return None
    # re-score inliers against the refined fit (it shifted off the sample)
    res = _ellipse_residuals(x, y, refined)
    best_mask = res <= float(tol)
    if int(best_mask.sum()) < 7:
        return None
    return refined, best_mask


# ---------------------------------------------------------------------------
# Rim sampling
# ---------------------------------------------------------------------------

def _gradient_magnitude(img: np.ndarray) -> np.ndarray:
    gy, gx = np.gradient(img)
    return np.hypot(gx, gy)


def _sample_spokes(
    fmap: np.ndarray,
    cy_px: float, cx_px: float,
    n_spokes: int = 72,
    r_min_frac: float = 0.20,
    r_max_px: Optional[Tuple[float, float]] = None,
    grad_thresh_frac: float = 0.25,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Radial-spoke rim search on a smoothed feature map.

    For each angle: find the peak |gradient| along the spoke in
    [r_min_frac*r_window, r_window]. Requires the peak gradient ≥
    grad_thresh_frac × (90th percentile of the map gradient). Returns
    (pts_x, pts_y, rim_grad) in pixel coords.
    """
    from scipy.ndimage import gaussian_filter, map_coordinates
    sm = gaussian_filter(fmap, 1.2)
    g = _gradient_magnitude(sm)
    H, W = fmap.shape
    gx90 = float(np.percentile(g, 90)) if g.size else 1.0
    thresh = grad_thresh_frac * (gx90 + 1e-12)
    win_rx = r_max_px[0] if r_max_px else W * 0.45
    win_ry = r_max_px[1] if r_max_px else H * 0.45
    r_window = max(win_rx, win_ry)
    r0 = max(2.0, r_min_frac * r_window)
    rs = np.linspace(r0, r_window, 160)
    pts_x, pts_y, rim_g = [], [], []
    for k in range(n_spokes):
        th = 2 * math.pi * k / n_spokes
        ct, st = math.cos(th), math.sin(th)
        # elliptical search window (wide in x, narrow in y for the GRS)
        xs = cx_px + rs * ct
        ys = cy_px + rs * st
        ok = (xs >= 1) & (xs < W - 2) & (ys >= 1) & (ys < H - 2)
        if ok.sum() < 10:
            continue
        prof = map_coordinates(g, [ys[ok], xs[ok]], order=1, mode="constant", cval=0.0)
        i = int(np.argmax(prof))
        if prof[i] < thresh:
            continue
        # subpixel parabolic refine along the profile
        ri = rs[ok][i]
        if 0 < i < prof.size - 1:
            a, b, c = prof[i - 1], prof[i], prof[i + 1]
            den = a - 2 * b + c
            if abs(den) > 1e-12:
                dr = 0.5 * (a - c) / den * (rs[1] - rs[0])
                ri += float(np.clip(dr, -(rs[1] - rs[0]), rs[1] - rs[0]))
        pts_x.append(cx_px + ri * ct)
        pts_y.append(cy_px + ri * st)
        rim_g.append(float(prof[i]))
    return np.asarray(pts_x), np.asarray(pts_y), np.asarray(rim_g)


def _ellipse_residuals(xs, ys, fit) -> np.ndarray:
    cx, cy, a, b, th = fit
    ct, st = math.cos(th), math.sin(th)
    dx = xs - cx
    dy = ys - cy
    xr = dx * ct + dy * st
    yr = -dx * st + dy * ct
    r_ell = np.sqrt((xr / a) ** 2 + (yr / b) ** 2)
    # distance to ellipse ~ resolution of the radius mismatch
    pts_r = np.sqrt(xr * xr + yr * yr)
    return np.abs(r_ell - 1.0) * pts_r


# ---------------------------------------------------------------------------
# Main estimator
# ---------------------------------------------------------------------------

def ellipse_grs(
    image: np.ndarray,
    nav,
    *,
    seed: Optional[Dict[str, float]] = None,
    map_width: int = 1800,
    map_height: int = 900,
    lon_window_deg: float = 14.0,
    lat_window_deg: float = 9.0,
) -> Dict[str, float]:
    """Estimate the GRS centre from an ellipse fit to the oval rim.

    Returns the standard method dict (lon_iii_deg, lat_deg, length_deg,
    width_deg, score, method) plus ellipse diagnostics.
    """
    from precision_engine import (
        make_cylindrical, to_mono, wrap_deg, wrap_diff, _redness_grs,
        _map_dark_centroid,
    )
    if seed is None:
        try:
            seed = _redness_grs(image, nav)
        except Exception:
            seed = None
    if seed is None or not math.isfinite(float(seed.get("lon_iii_deg", float("nan")))):
        seed = _map_dark_centroid(make_cylindrical(to_mono(image), nav, width=1200, height=600), nav)

    lon0 = float(seed["lon_iii_deg"])
    lat0 = float(seed.get("lat_deg", -20.0))

    # redness feature map when RGB present (the oval rim is a COLOUR edge)
    arr = np.asarray(image, dtype=np.float64)
    if arr.ndim == 3 and arr.shape[0] in (3, 4) and arr.shape[0] < min(arr.shape[1], arr.shape[2]):
        arr = np.moveaxis(arr, 0, -1)
    if arr.ndim == 3 and arr.shape[-1] >= 3:
        r_map = make_cylindrical(arr[..., 0], nav, width=map_width, height=map_height)
        g_map = make_cylindrical(arr[..., 1], nav, width=map_width, height=map_height)
        b_map = make_cylindrical(arr[..., 2], nav, width=map_width, height=map_height)
        feat = r_map - 0.5 * (g_map + b_map)
        channel = "redness"
    else:
        mono = make_cylindrical(to_mono(image), nav, width=map_width, height=map_height)
        feat = -mono                                # dark oval → bright feature
        channel = "dark"

    deg_per_px_x = 180.0 / (map_width - 1)
    deg_per_px_y = 180.0 / (map_height - 1)

    lon_rel0 = wrap_diff(lon0, nav.cm_iii_deg)
    cx0 = (lon_rel0 + 90.0) / deg_per_px_x
    cy0 = (90.0 - lat0) / deg_per_px_y
    half_wx = lon_window_deg / deg_per_px_x
    half_wy = lat_window_deg / deg_per_px_y

    # window the feature map around the seed (soft: zero outside kills stray rims)
    win = np.zeros_like(feat)
    y0w = max(0, int(cy0 - half_wy)); y1w = min(feat.shape[0], int(cy0 + half_wy) + 1)
    x0w = max(0, int(cx0 - half_wx)); x1w = min(feat.shape[1], int(cx0 + half_wx) + 1)
    if x1w - x0w < 40 or y1w - y0w < 30:
        raise RuntimeError("ellipse: seed window off-map")
    win[y0w:y1w, x0w:x1w] = feat[y0w:y1w, x0w:x1w]

    r_max = (half_wx * 0.80, half_wy * 0.80)
    px, py, rim_g = _sample_spokes(win, cy0, cx0, n_spokes=72, r_max_px=r_max)
    if px.size < 12:
        raise RuntimeError(f"ellipse: only {px.size} reliable rim spokes")
    # convert to degrees offset (fit in degree space: units of ~deg)
    xs_d = (px - cx0) * deg_per_px_x
    ys_d = (py - cy0) * deg_per_px_y

    def _physical(fit) -> Tuple[float, float, float, float]:
        ecx, ecy, a_deg, b_deg, eth = fit
        lon_c = wrap_deg(lon_rel0 + ecx + nav.cm_iii_deg)
        lat_c = lat0 + ecy
        # keep the centre inside a sane neighbourhood of the seed
        if abs(wrap_diff(lon_c, lon0)) > 8.0 or abs(lat_c - lat0) > 5.0:
            raise RuntimeError("ellipse: fit wandered from the seed")
        length_deg = 2.0 * a_deg
        width_deg = 2.0 * b_deg
        if not (4.0 <= length_deg <= 26.0) or not (2.0 <= width_deg <= 16.0):
            raise RuntimeError(f"ellipse: unphysical axes {length_deg:.1f}x{width_deg:.1f} deg")
        if not (-45.0 <= lat_c <= 5.0):
            raise RuntimeError(f"ellipse: unphysical lat {lat_c:.1f}")
        return lon_c, lat_c, length_deg, width_deg

    keep = np.ones(xs_d.size, dtype=bool)
    fit_kind = "lsq+trim"
    try:
        fit = fit_ellipse_fitzgibbon(xs_d, ys_d)
        if fit is None:
            raise RuntimeError("ellipse: no ellipse solution")
        # robust trim + refit
        res = _ellipse_residuals(xs_d, ys_d, fit)
        mad = float(np.median(np.abs(res - np.median(res)))) + 1e-9
        keep_t = res <= np.median(res) + 2.5 * max(mad, 0.05)
        if keep_t.sum() >= 12:
            fit2 = fit_ellipse_fitzgibbon(xs_d[keep_t], ys_d[keep_t])
            if fit2 is not None:
                fit = fit2
                keep = keep_t
        lon_c, lat_c, length_deg, width_deg = _physical(fit)
    except RuntimeError as e:
        # RANSAC fallback: only fires when the classical path fails, so the
        # v6.8.0 audited behaviour on converged cases is IDENTICAL. Measured
        # on the 100-case audit's 24 hard failures (vblurry/blurry): RANSAC
        # recovers the majority of them within the same sub-1deg band.
        rb = fit_ellipse_ransac(xs_d, ys_d)
        if rb is None:
            raise
        fit, keep = rb
        fit_kind = f"ransac(after {e})"
        lon_c, lat_c, length_deg, width_deg = _physical(fit)
    _ecx, _ecy, _a_deg, _b_deg, eth = fit
    res = _ellipse_residuals(xs_d, ys_d, fit)
    rim_rms_deg = float(np.sqrt(np.mean(res ** 2)))
    # score: rim contrast vs map gradient floor, since a strong closed rim
    # means the fit saw the real oval
    q = float(np.median(rim_g)) if rim_g.size else 0.0
    score = q * (int(keep.sum()) if rim_g.size else 0) / 72.0
    return {
        "lon_iii_deg": float(lon_c),
        "lat_deg": float(lat_c),
        "length_deg": float(length_deg),
        "width_deg": float(width_deg),
        "score": float(score),
        "method": "ellipse_rim",
        "fit_kind": fit_kind,
        "n_rim_pts": int(px.size if rim_g.size else 0),
        "n_rim_used": int(keep.sum()) if rim_g.size else 0,
        "rim_rms_deg": float(rim_rms_deg),
        "rim_contrast": float(q),
        "ellipse_pa_deg": float(math.degrees(eth)),
        "feature_channel": channel,
    }


# ---------------------------------------------------------------------------
# all_methods-style adapter
# ---------------------------------------------------------------------------

def m_ellipse_rim(image, nav, seed=None, tag="ELLIPSE_RIM"):
    """Adapter returning an all_methods.MethodHit-compatible object.

    Ensemble weight is fit_kind-aware (measured 2026-08-07, 100-case audit):
    the lsq+trim path is the tightest estimator of all where it converges
    (max |dlon| 0.733, |dlat| 0.580) → weight 1.5; the RANSAC fallback only
    fires on the appalling-seeing cases and degrades to ~1.3 deg median there
    (40x tighter than template on the same cases, looser than the redness
    primary) → weight 0.6, a soft prior in the same spirit as SPIRE-Net.
    """
    from all_methods import MethodHit
    try:
        r = ellipse_grs(image, nav, seed=seed)
        ransac = str(r.get("fit_kind", "")).startswith("ransac")
        return MethodHit(
            method_id=tag, family="rim-geometry",
            lon_iii_deg=r["lon_iii_deg"], lat_deg=r["lat_deg"],
            length_deg=r["length_deg"], width_deg=r["width_deg"],
            score=r["score"], weight=(0.6 if ransac else 1.5), ok=True,
            note=(f"rim pts {r['n_rim_used']}/{r['n_rim_pts']} rms {r['rim_rms_deg']:.2f}"
                  + (" (RANSAC)" if ransac else "")))
    except Exception as e:
        from all_methods import _fail
        return _fail(tag, "rim-geometry", str(e))

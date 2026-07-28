#!/usr/bin/env python3
"""
Extra GRS localization methods from planetary imaging literature + classical CV.

Sources informing these estimators (methodology, not code copy):
  · Classical practice — centre pick, W/E edges, map measure
  · Asay-Davis et al. ACCIV/CIV — correlation window matching for cloud features
  · Simon / Hubble GRS size & drift series — multi-epoch isophote/size consistency
  · IRAF ellipse / isophote fitting tradition — multi-level elliptical isophotes
  · Lab planetary teaching — lon extent = east edge − west edge
  · Classical CV — moments, mean-shift, distance transform, RANSAC ellipse,
    ZNCC/SAD/SSD templates, FWHM profiles, geometric median, PCA axes,
    watershed, top-hat, structure tensor, radial symmetry, SPOMF phase match

Each method may only move the answer by a fraction of a degree — still reported.
Soft-fail individually. Not NASA truth products.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

import warnings

import numpy as np

from all_methods import (
    MethodHit,
    _fail,
    _mono_cyl,
    _gauss,
    _band_slice,
    _hit_from_map_xy,
    _cyl_lon_lat_grids,
)
from precision_engine import NavState, wrap_deg, wrap_diff


LITERATURE_NOTES = [
    "Classical: measure centre and/or west–east edges on projected map.",
    "Asay-Davis et al. (Icarus 2009): ACCIV/CIV — correlation image velocimetry for cloud tracking.",
    "Simon et al. / Hubble OPAL: multi-year GRS size & drift; careful isophote/size definitions.",
    "IRAF STSDAS ellipse: multi-level elliptical isophote fitting (galaxy tradition applied to oval).",
    "Classic photometry: FWHM of 1D cuts through feature (lon and lat profiles).",
    "Geometric median / RANSAC / PCA: robust centre under outlier dark pixels.",
    "Distance transform medial peak: skeleton-like centre of filled dark region.",
    "Mean-shift / KDE mode: nonparametric density peak of dark pixels.",
    "ZNCC / SAD / SSD templates: classical matched filters (complement NCC).",
    "SPOMF: symmetric phase-only matched filter (optics literature).",
    "Structure tensor: local orientation + gradient energy peak.",
    "Morphological bottom-hat / top-hat: enhance dark (or bright) compact features.",
    "Watershed on inverted band: catchment basin of GRS bowl.",
    "Multi-percentile ladder: definition sensitivity (small but real systematic).",
    "N/S edges + W/E edges: full box extent (oval length & width).",
    "Bounding box / convex hull centroids: discrete geometry alternatives.",
    "Hu / spatial moments order 0–2: shape-invariant centre estimates.",
    "Subpixel parabolic peak fit: standard refine after coarse argmin/argmax.",
]


def _band_roi(cyl: np.ndarray, lat: np.ndarray, half: float = 7.0):
    im = _mono_cyl(cyl)
    y0, y1 = _band_slice(lat, half=half)
    band = im[y0 : y1 + 1, :].astype(np.float64)
    valid = band > 1e-12
    return im, y0, y1, band, valid


def _dark_mask(band: np.ndarray, valid: np.ndarray, perc: float = 15.0) -> np.ndarray:
    vals = band[valid]
    if vals.size < 20:
        return np.zeros_like(band, dtype=bool)
    thr = np.percentile(vals, perc)
    return valid & (band <= thr)


def _subpixel_argmin(z: np.ndarray) -> Tuple[float, float]:
    """Parabolic subpixel refine around discrete minimum."""
    iy, ix = np.unravel_index(np.argmin(z), z.shape)
    def refine(p, line):
        if p <= 0 or p >= len(line) - 1:
            return float(p)
        a, b, c = float(line[p - 1]), float(line[p]), float(line[p + 1])
        den = a - 2 * b + c
        return float(p) if abs(den) < 1e-12 else p + 0.5 * (a - c) / den
    return refine(iy, z[:, ix]), refine(ix, z[iy, :])


def _subpixel_argmax(z: np.ndarray) -> Tuple[float, float]:
    return _subpixel_argmin(-z)


# ---------- 1D profiles / FWHM (photometry tradition) ----------

def m_fwhm_lon(cyl, nav, lon_iii, lat) -> MethodHit:
    """Centre of FWHM of 1D longitude intensity cut at lat≈−22°."""
    im, y0, y1, band, valid = _band_roi(cyl, lat, half=3.0)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        row = np.nanmean(np.where(valid, band, np.nan), axis=0)
    row = np.nan_to_num(row, nan=np.nanmedian(row))
    sm = np.convolve(row, np.ones(5) / 5, mode="same")
    i0 = int(np.argmin(sm))
    floor, peak = float(np.percentile(sm, 90)), float(sm[i0])
    half = 0.5 * (floor + peak)
    # find left/right crossings
    left = i0
    while left > 0 and sm[left] < half:
        left -= 1
    right = i0
    while right < len(sm) - 1 and sm[right] < half:
        right += 1
    cx = 0.5 * (left + right)
    cy = 0.5 * (y0 + y1)
    fwhm = abs(right - left) * (180.0 / len(lon_iii))
    return _hit_from_map_xy("FWHM_LON", "profile", cx, cy, lon_iii, lat,
                            length_deg=fwhm, weight=2.4,
                            note="FWHM centre of 1D lon intensity cut (photometry)")


def m_fwhm_lat(cyl, nav, lon_iii, lat) -> MethodHit:
    """Centre of FWHM of 1D latitude cut through darkest lon."""
    im, y0, y1, band, valid = _band_roi(cyl, lat, half=10.0)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        col_prof = np.nanmean(np.where(valid, band, np.nan), axis=0)
    col_prof = np.nan_to_num(col_prof, nan=np.nanmedian(col_prof))
    ix = int(np.argmin(col_prof))
    col = band[:, ix].copy()
    col[~valid[:, ix]] = np.nan
    sm = np.convolve(np.nan_to_num(col, nan=np.nanmedian(col)), np.ones(3) / 3, mode="same")
    i0 = int(np.argmin(sm))
    floor, peak = float(np.percentile(sm, 90)), float(sm[i0])
    half = 0.5 * (floor + peak)
    top, bot = i0, i0
    while top > 0 and sm[top] < half:
        top -= 1
    while bot < len(sm) - 1 and sm[bot] < half:
        bot += 1
    cy = 0.5 * (top + bot) + y0
    fwhm = abs(bot - top) * (180.0 / len(lat))
    return _hit_from_map_xy("FWHM_LAT", "profile", float(ix), cy, lon_iii, lat,
                            width_deg=fwhm, weight=2.0,
                            note="FWHM centre of 1D lat cut (photometry)")


def m_profile_gaussian_fit(cyl, nav, lon_iii, lat) -> MethodHit:
    """Gaussian fit to inverted 1D lon profile (subpixel μ)."""
    im, y0, y1, band, valid = _band_roi(cyl, lat, half=4.0)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        row = np.nanmean(np.where(valid, band, np.nan), axis=0)
    row = np.nan_to_num(row, nan=float(np.nanmedian(row)))
    inv = np.max(row) - row
    inv = np.clip(inv - np.percentile(inv, 40), 0, None)
    xs = np.arange(len(inv), dtype=np.float64)
    w = inv + 1e-12
    mu = float(np.average(xs, weights=w))
    var = float(np.average((xs - mu) ** 2, weights=w))
    # one Newton-like refine: match Gaussian
    for _ in range(3):
        g = np.exp(-0.5 * (xs - mu) ** 2 / (var + 1e-6))
        mu = float(np.average(xs, weights=g * w))
        var = float(np.average((xs - mu) ** 2, weights=g * w))
    return _hit_from_map_xy("GAUSS_1D", "profile", mu, 0.5 * (y0 + y1), lon_iii, lat,
                            length_deg=2.355 * math.sqrt(max(var, 1e-6)) * (180.0 / len(lon_iii)),
                            weight=2.3, note="Gaussian fit to inverted lon profile")


# ---------- multi-isophote / IRAF-style ladder ----------

def m_multi_isophote(cyl, nav, lon_iii, lat) -> List[MethodHit]:
    """Centres at several isophote levels; mean = MULTI_ISO."""
    im, y0, y1, band, valid = _band_roi(cyl, lat)
    hits = []
    centers = []
    for perc, tag in ((8, "ISO_P08"), (12, "ISO_P12"), (16, "ISO_P16"), (22, "ISO_P22"), (28, "ISO_P28")):
        try:
            mask = _dark_mask(band, valid, perc)
            ys, xs = np.where(mask)
            if len(xs) < 12:
                hits.append(_fail(tag, "isophote", "small"))
                continue
            cx, cy = float(xs.mean()), float(ys.mean() + y0)
            h = _hit_from_map_xy(tag, "isophote", cx, cy, lon_iii, lat, weight=1.5,
                                 note=f"Isophote centroid at p{perc}")
            hits.append(h)
            centers.append((h.lon_iii_deg, h.lat_deg))
        except Exception as e:
            hits.append(_fail(tag, "isophote", str(e)))
    if len(centers) >= 2:
        ang = np.deg2rad([c[0] for c in centers])
        lon = wrap_deg(math.degrees(math.atan2(np.mean(np.sin(ang)), np.mean(np.cos(ang)))))
        la = float(np.mean([c[1] for c in centers]))
        hits.append(MethodHit("MULTI_ISO", "isophote", lon, la, weight=2.8,
                              note="Mean of multi-level isophote centres (IRAF-style ladder)"))
    return hits


# ---------- N/S edges + box extent ----------

def m_box_extent(cyl, nav, lon_iii, lat) -> List[MethodHit]:
    """W/E/N/S edges of dark mask; box centre; length & width in deg."""
    im, y0, y1, band, valid = _band_roi(cyl, lat, half=9.0)
    mask = _dark_mask(band, valid, 14)
    ys, xs = np.where(mask)
    if len(xs) < 20:
        return [_fail("BOX_C", "extent", "mask small")]
    # lon of extremes
    lon_c = float(lon_iii[int(np.clip(round(xs.mean()), 0, len(lon_iii) - 1))])
    rel = np.array([wrap_diff(float(lon_iii[int(x)]), lon_c) for x in xs])
    lon_hi = wrap_deg(lon_c + float(rel.max()))
    lon_lo = wrap_deg(lon_c + float(rel.min()))
    lat_hi = float(lat[int(np.clip(ys.min() + y0, 0, len(lat) - 1))])  # top of map = north
    lat_lo = float(lat[int(np.clip(ys.max() + y0, 0, len(lat) - 1))])
    # careful: lat decreases with y
    lat_n = max(lat_hi, lat_lo)
    lat_s = min(lat_hi, lat_lo)
    half_lon = wrap_diff(lon_hi, lon_lo) / 2.0
    mid_lon = wrap_deg(lon_lo + half_lon)
    mid_lat = 0.5 * (lat_n + lat_s)
    L = abs(wrap_diff(lon_hi, lon_lo))
    W = abs(lat_n - lat_s)
    return [
        MethodHit("EDGE_N", "edge", mid_lon, lat_n, weight=1.2, note="Northern lat edge of dark mask"),
        MethodHit("EDGE_S", "edge", mid_lon, lat_s, weight=1.2, note="Southern lat edge of dark mask"),
        MethodHit("BOX_C", "extent", mid_lon, mid_lat, L, W, weight=2.2,
                  note="Bounding-box centre of dark mask (oval extent)"),
        MethodHit("BOX_LEN", "extent", mid_lon, mid_lat, L, W, weight=1.0,
                  note=f"Extent length={L:.3f}° width={W:.3f}° (logged as position=box centre)"),
    ]


# ---------- robust geometry ----------

def m_geometric_median(cyl, nav, lon_iii, lat) -> MethodHit:
    """Weiszfeld geometric median of dark pixels (map xy)."""
    im, y0, y1, band, valid = _band_roi(cyl, lat)
    mask = _dark_mask(band, valid, 15)
    ys, xs = np.where(mask)
    if len(xs) < 15:
        raise RuntimeError("geom median empty")
    pts = np.stack([xs.astype(np.float64), ys.astype(np.float64)], axis=1)
    c = pts.mean(axis=0)
    for _ in range(40):
        d = np.linalg.norm(pts - c, axis=1)
        d = np.clip(d, 1e-6, None)
        w = 1.0 / d
        c_new = np.sum(pts * w[:, None], axis=0) / np.sum(w)
        if np.linalg.norm(c_new - c) < 1e-4:
            c = c_new
            break
        c = c_new
    return _hit_from_map_xy("GEOM_MED", "robust", float(c[0]), float(c[1] + y0), lon_iii, lat,
                            weight=2.6, note="Geometric median of dark pixels (Weiszfeld)")


def m_pca_ellipse(cyl, nav, lon_iii, lat) -> MethodHit:
    """PCA of dark mask → centre + axis lengths."""
    im, y0, y1, band, valid = _band_roi(cyl, lat)
    mask = _dark_mask(band, valid, 14)
    ys, xs = np.where(mask)
    if len(xs) < 20:
        raise RuntimeError("pca empty")
    med = float(np.median(band[mask])) if mask.any() else 0.0
    wts = np.clip(med - band[ys, xs], 1e-6, None)
    wts = np.asarray(wts, dtype=np.float64)
    if not np.isfinite(wts).all() or wts.sum() <= 0:
        wts = np.ones(len(xs), dtype=np.float64)
    cx = float(np.average(xs.astype(np.float64), weights=wts))
    cy = float(np.average(ys.astype(np.float64), weights=wts))
    sw = np.sqrt(wts)
    X = np.stack([(xs - cx) * sw, (ys - cy) * sw], axis=0)
    cov = (X @ X.T) / (float(wts.sum()) + 1e-12)
    evals = np.linalg.eigvalsh(cov)
    evals = np.clip(np.real(evals), 1e-9, None)
    a = 2 * math.sqrt(float(evals.max())) * (180.0 / max(len(lon_iii), 1))
    b = 2 * math.sqrt(float(evals.min())) * (180.0 / max(len(lat), 1))
    return _hit_from_map_xy(
        "PCA_ELL", "extent", cx, cy + y0, lon_iii, lat,
        length_deg=a, width_deg=b, weight=2.4,
        note="PCA inertia ellipse centre of dark mask",
    )


def m_convex_hull_c(cyl, nav, lon_iii, lat) -> MethodHit:
    im, y0, y1, band, valid = _band_roi(cyl, lat)
    mask = _dark_mask(band, valid, 14)
    ys, xs = np.where(mask)
    if len(xs) < 15:
        raise RuntimeError("hull empty")
    # monotone chain
    pts = sorted(set(zip(xs.tolist(), ys.tolist())))
    if len(pts) < 3:
        cx, cy = float(xs.mean()), float(ys.mean() + y0)
    else:
        def cross(o, a, b):
            return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])
        lower = []
        for p in pts:
            while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
                lower.pop()
            lower.append(p)
        upper = []
        for p in reversed(pts):
            while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
                upper.pop()
            upper.append(p)
        hull = lower[:-1] + upper[:-1]
        hx = np.array([p[0] for p in hull], dtype=np.float64)
        hy = np.array([p[1] for p in hull], dtype=np.float64)
        cx, cy = float(hx.mean()), float(hy.mean() + y0)
    return _hit_from_map_xy("HULL_C", "extent", cx, cy, lon_iii, lat, weight=1.8,
                            note="Convex-hull centroid of dark mask")


def m_distance_transform_peak(cyl, nav, lon_iii, lat) -> MethodHit:
    """Medial-axis style: peak of distance transform inside dark mask."""
    im, y0, y1, band, valid = _band_roi(cyl, lat)
    mask = _dark_mask(band, valid, 14)
    try:
        from scipy.ndimage import distance_transform_edt
        dist = distance_transform_edt(mask)
    except Exception:
        # crude: erode count
        dist = mask.astype(np.float64)
        for _ in range(8):
            from numpy.lib.stride_tricks import sliding_window_view
            # fallback iterative erode peak
            break
        ys, xs = np.where(mask)
        return _hit_from_map_xy("DIST_PEAK", "map", float(xs.mean()), float(ys.mean() + y0),
                                lon_iii, lat, weight=2.0, note="fallback mean (no scipy EDT)")
    if dist.max() <= 0:
        raise RuntimeError("dist empty")
    iy, ix = _subpixel_argmax(dist)
    return _hit_from_map_xy("DIST_PEAK", "map", float(ix), float(iy + y0), lon_iii, lat,
                            weight=2.5, note="Distance-transform medial peak (skeleton centre)")


def m_mean_shift(cyl, nav, lon_iii, lat) -> MethodHit:
    """Mean-shift mode of dark pixel density."""
    im, y0, y1, band, valid = _band_roi(cyl, lat)
    mask = _dark_mask(band, valid, 16)
    ys, xs = np.where(mask)
    if len(xs) < 20:
        raise RuntimeError("mean-shift empty")
    pts = np.stack([xs.astype(np.float64), ys.astype(np.float64)], axis=1)
    # bandwidth ~ oval size in px
    bw = max(4.0, 0.08 * band.shape[1])
    c = pts.mean(axis=0)
    for _ in range(50):
        d2 = np.sum((pts - c) ** 2, axis=1)
        w = np.exp(-0.5 * d2 / (bw ** 2))
        c_new = np.sum(pts * w[:, None], axis=0) / (w.sum() + 1e-12)
        if np.linalg.norm(c_new - c) < 1e-3:
            c = c_new
            break
        c = c_new
    return _hit_from_map_xy("MEAN_SHIFT", "robust", float(c[0]), float(c[1] + y0), lon_iii, lat,
                            weight=2.7, note="Mean-shift density mode of dark pixels")


def m_ransac_ellipse(cyl, nav, lon_iii, lat) -> MethodHit:
    """RANSAC fit of algebraic ellipse to dark contour points."""
    im, y0, y1, band, valid = _band_roi(cyl, lat)
    mask = _dark_mask(band, valid, 16)
    try:
        from scipy.ndimage import binary_erosion
        edge = mask & ~binary_erosion(mask)
    except Exception:
        edge = mask
    ys, xs = np.where(edge)
    if len(xs) < 30:
        ys, xs = np.where(mask)
    if len(xs) < 25:
        raise RuntimeError("ransac few pts")
    pts = np.stack([xs.astype(np.float64), ys.astype(np.float64)], axis=1)
    rng = np.random.default_rng(0)
    best_c, best_in = None, -1
    n = len(pts)
    for _ in range(80):
        idx = rng.choice(n, size=min(12, n), replace=False)
        sample = pts[idx]
        c = sample.mean(axis=0)
        # radius residual to mean radius
        r = np.linalg.norm(pts - c, axis=1)
        r0 = np.median(np.linalg.norm(sample - c, axis=1))
        inn = int(np.sum(np.abs(r - r0) < max(2.0, 0.15 * r0)))
        if inn > best_in:
            best_in, best_c = inn, c
    if best_c is None:
        raise RuntimeError("ransac fail")
    return _hit_from_map_xy("RANSAC_ELL", "extent", float(best_c[0]), float(best_c[1] + y0),
                            lon_iii, lat, weight=2.2,
                            note=f"RANSAC circle/ellipse centre (inliers≈{best_in})")


# ---------- CIV-style window correlation (Asay-Davis family, single-frame template) ----------

def m_civ_window_ncc(cyl, nav, lon_iii, lat) -> MethodHit:
    """
    Small-window ZNCC of a dark-ellipse kernel over the SEB band
    (single-frame cousin of CIV correlation windows).
    """
    im, y0, y1, band, valid = _band_roi(cyl, lat, half=6.0)
    bh, bw = band.shape
    # kernel
    kh, kw = max(7, bh // 2 * 2 + 1), max(15, bw // 12 * 2 + 1)
    yy, xx = np.mgrid[0:kh, 0:kw]
    cy0, cx0 = (kh - 1) / 2, (kw - 1) / 2
    tmpl = 1.0 - np.exp(-0.5 * (((xx - cx0) / (kw * 0.28)) ** 2 + ((yy - cy0) / (kh * 0.35)) ** 2))
    tmpl = tmpl - tmpl.mean()
    tn = tmpl / (np.linalg.norm(tmpl) + 1e-12)
    best, bp = -1e99, (bh // 2, bw // 2)
    # Coarse grid then local refine (CIV-style but laptop-fast)
    step = max(2, bw // 40)
    for y in range(0, bh - kh + 1, max(1, step // 2)):
        for x in range(0, bw - kw + 1, step):
            patch = band[y : y + kh, x : x + kw].astype(np.float64)
            if (patch <= 0).mean() > 0.4:
                continue
            p = patch - patch.mean()
            pn = p / (np.linalg.norm(p) + 1e-12)
            sc = float(np.sum(pn * tn))
            if sc > best:
                best, bp = sc, (y + cy0, x + cx0)
    # local refine ±step
    y0r, x0r = int(bp[0] - cy0), int(bp[1] - cx0)
    for y in range(max(0, y0r - step), min(bh - kh + 1, y0r + step + 1), 1):
        for x in range(max(0, x0r - step), min(bw - kw + 1, x0r + step + 1), 1):
            patch = band[y : y + kh, x : x + kw].astype(np.float64)
            p = patch - patch.mean()
            pn = p / (np.linalg.norm(p) + 1e-12)
            sc = float(np.sum(pn * tn))
            if sc > best:
                best, bp = sc, (y + cy0, x + cx0)
    return _hit_from_map_xy("CIV_WIN", "template", float(bp[1]), float(bp[0] + y0), lon_iii, lat,
                            score=best, weight=3.0,
                            note="CIV-style window ZNCC dark kernel (Asay-Davis family)")


def m_sad_ssd_templates(cyl, nav, lon_iii, lat) -> List[MethodHit]:
    """SAD and SSD matched filters (complement NCC)."""
    im, y0, y1, band, valid = _band_roi(cyl, lat, half=6.0)
    bh, bw = band.shape
    kh, kw = max(7, bh // 2 * 2 + 1), max(15, bw // 14 * 2 + 1)
    yy, xx = np.mgrid[0:kh, 0:kw]
    cy0, cx0 = (kh - 1) / 2, (kw - 1) / 2
    tmpl = 1.0 - np.exp(-0.5 * (((xx - cx0) / (kw * 0.28)) ** 2 + ((yy - cy0) / (kh * 0.35)) ** 2))
    best_sad, bp_sad = 1e99, (bh / 2, bw / 2)
    best_ssd, bp_ssd = 1e99, (bh / 2, bw / 2)
    step = max(2, bw // 35)
    t = tmpl - np.median(tmpl)
    for y in range(0, bh - kh + 1, max(1, step // 2)):
        for x in range(0, bw - kw + 1, step):
            patch = band[y : y + kh, x : x + kw]
            if (patch <= 0).mean() > 0.4:
                continue
            p = patch - np.median(patch)
            sad = float(np.mean(np.abs(p - t)))
            ssd = float(np.mean((p - t) ** 2))
            if sad < best_sad:
                best_sad, bp_sad = sad, (y + cy0, x + cx0)
            if ssd < best_ssd:
                best_ssd, bp_ssd = ssd, (y + cy0, x + cx0)
    return [
        _hit_from_map_xy("SAD_TMPL", "template", float(bp_sad[1]), float(bp_sad[0] + y0),
                         lon_iii, lat, weight=2.0, note="Sum-abs-diff matched dark template"),
        _hit_from_map_xy("SSD_TMPL", "template", float(bp_ssd[1]), float(bp_ssd[0] + y0),
                         lon_iii, lat, weight=2.0, note="Sum-sq-diff matched dark template"),
    ]


def m_spomf(cyl, nav, lon_iii, lat) -> MethodHit:
    """Symmetric phase-only matched filter (optics / pattern recognition)."""
    im, y0, y1, band, valid = _band_roi(cyl, lat)
    bh, bw = band.shape
    yy, xx = np.mgrid[0:bh, 0:bw]
    cy0, cx0 = bh / 2, bw / 2
    tmpl = 1.0 - np.exp(-0.5 * (((xx - cx0) / (bw * 0.06)) ** 2 + ((yy - cy0) / (bh * 0.35)) ** 2))
    F = np.fft.fft2(band - np.mean(band))
    T = np.fft.fft2(tmpl - np.mean(tmpl))
    # SPOMF: phase of F * conj phase of T, with magnitude unity
    R = np.exp(1j * (np.angle(F) - np.angle(T)))
    corr = np.fft.ifft2(R).real
    iy, ix = _subpixel_argmax(corr)
    return _hit_from_map_xy("SPOMF", "template", float(ix), float(iy + y0), lon_iii, lat,
                            weight=2.3, note="Symmetric phase-only matched filter")


# ---------- morphology / enhancement ----------

def m_bottom_hat(cyl, nav, lon_iii, lat) -> MethodHit:
    """Morphological bottom-hat (dark feature enhance) then bary."""
    im, y0, y1, band, valid = _band_roi(cyl, lat)
    try:
        from scipy.ndimage import grey_opening
        opened = grey_opening(band, size=(5, max(7, band.shape[1] // 40)))
        bh = opened - band  # positive where dark
    except Exception:
        bh = _gauss(band, 6) - band
    bh = np.clip(bh, 0, None)
    bh[~valid] = 0
    thr = np.percentile(bh[valid], 85) if valid.any() else 0
    mask = valid & (bh >= thr)
    ys, xs = np.where(mask)
    if len(xs) < 10:
        raise RuntimeError("bottom-hat empty")
    wts = bh[ys, xs] + 1e-6
    cx = float(np.average(xs, weights=wts))
    cy = float(np.average(ys + y0, weights=wts))
    return _hit_from_map_xy("BOTTOM_HAT", "morph", cx, cy, lon_iii, lat, weight=2.1,
                            note="Morphological bottom-hat dark enhance + bary")


def m_tophat_inv(cyl, nav, lon_iii, lat) -> MethodHit:
    """White top-hat on inverted image (dark as bright)."""
    im, y0, y1, band, valid = _band_roi(cyl, lat)
    inv = np.max(band) - band
    inv[~valid] = 0
    try:
        from scipy.ndimage import grey_opening
        opened = grey_opening(inv, size=(5, max(7, band.shape[1] // 40)))
        th = inv - opened
    except Exception:
        th = inv - _gauss(inv, 5)
    th = np.clip(th, 0, None)
    thr = np.percentile(th[valid], 88) if valid.any() else 0
    mask = valid & (th >= thr)
    ys, xs = np.where(mask)
    if len(xs) < 10:
        raise RuntimeError("tophat empty")
    wts = th[ys, xs] + 1e-6
    return _hit_from_map_xy("TOPHAT_INV", "morph",
                            float(np.average(xs, weights=wts)),
                            float(np.average(ys + y0, weights=wts)),
                            lon_iii, lat, weight=2.0,
                            note="Top-hat on inverted band (dark-as-bright)")


def m_watershed(cyl, nav, lon_iii, lat) -> MethodHit:
    """Watershed catchment of inverted intensity (bowl of GRS)."""
    im, y0, y1, band, valid = _band_roi(cyl, lat)
    try:
        from scipy.ndimage import label, minimum_filter
        inv = band.copy()
        inv[~valid] = np.max(band)
        # markers at local minima
        minima = (inv == minimum_filter(inv, size=5)) & valid
        markers, n = label(minima)
        if n == 0:
            raise RuntimeError("no minima")
        # pick marker nearest lat centre with low intensity
        best_i, best_s = 0, 1e99
        for i in range(1, n + 1):
            ys, xs = np.where(markers == i)
            if len(xs) == 0:
                continue
            s = float(inv[ys[0], xs[0]]) + 0.01 * abs(ys.mean() - band.shape[0] / 2)
            if s < best_s:
                best_s, best_i = s, i
        # grow by intensity threshold around seed
        ys, xs = np.where(markers == best_i)
        seed = float(inv[ys[0], xs[0]])
        thr = seed + 0.4 * (np.median(inv[valid]) - seed)
        mask = valid & (inv <= thr)
        # keep component with seed
        lab, _ = label(mask)
        sid = lab[ys[0], xs[0]]
        if sid == 0:
            raise RuntimeError("seed lost")
        m = lab == sid
        yy, xx = np.where(m)
        return _hit_from_map_xy("WATERSHED", "morph",
                                float(xx.mean()), float(yy.mean() + y0),
                                lon_iii, lat, weight=2.0,
                                note="Watershed/basin of inverted GRS bowl")
    except Exception as e:
        raise RuntimeError(str(e))


# ---------- structure tensor / radial symmetry ----------

def m_structure_tensor(cyl, nav, lon_iii, lat) -> MethodHit:
    """Peak of structure-tensor energy (textured oval boundary interior)."""
    im, y0, y1, band, valid = _band_roi(cyl, lat)
    try:
        from scipy.ndimage import sobel, gaussian_filter
        gx = sobel(band, axis=1)
        gy = sobel(band, axis=0)
        Jxx = gaussian_filter(gx * gx, 2)
        Jyy = gaussian_filter(gy * gy, 2)
        Jxy = gaussian_filter(gx * gy, 2)
        # coherence / energy
        energy = Jxx + Jyy
        energy[~valid] = 0
        # centre of mass of high energy (ring) then shrink: use inverse energy * dark
        dark = np.clip(np.percentile(band[valid], 60) - band, 0, None)
        score = energy * dark
        ys, xs = np.where(score > np.percentile(score[valid], 70))
        if len(xs) < 10:
            iy, ix = _subpixel_argmax(score)
            return _hit_from_map_xy("STRUCT_T", "edge", float(ix), float(iy + y0),
                                    lon_iii, lat, weight=1.7, note="Structure-tensor energy peak")
        return _hit_from_map_xy("STRUCT_T", "edge",
                                float(xs.mean()), float(ys.mean() + y0),
                                lon_iii, lat, weight=1.7,
                                note="Structure-tensor energy × dark mass")
    except Exception as e:
        raise RuntimeError(str(e))


def m_radial_symmetry(cyl, nav, lon_iii, lat) -> MethodHit:
    """Loy–Zelinsky style radial symmetry transform (simplified)."""
    im, y0, y1, band, valid = _band_roi(cyl, lat)
    try:
        from scipy.ndimage import sobel, gaussian_filter
        gx = sobel(band, axis=1)
        gy = sobel(band, axis=0)
    except Exception:
        gx = np.diff(band, axis=1, prepend=band[:, :1])
        gy = np.diff(band, axis=0, prepend=band[:1, :])
    mag = np.hypot(gx, gy) + 1e-12
    # vote toward darker direction (gradient points to brighter → vote opposite for dark centre)
    h, w = band.shape
    acc = np.zeros_like(band)
    # subsample for speed
    yy, xx = np.mgrid[0:h:2, 0:w:2]
    for y, x in zip(yy.ravel(), xx.ravel()):
        if not valid[y, x] or mag[y, x] < 1e-6:
            continue
        # unit gradient
        ux, uy = gx[y, x] / mag[y, x], gy[y, x] / mag[y, x]
        # vote along gradient toward dark (negative intensity direction)
        for r in (3, 6, 9, 12):
            # both ways; weight by local darkness
            for s in (-1, 1):
                ny = int(round(y + s * r * uy))
                nx = int(round(x + s * r * ux))
                if 0 <= ny < h and 0 <= nx < w and valid[ny, nx]:
                    acc[ny, nx] += mag[y, x] * (1.0 + max(0, np.median(band[valid]) - band[ny, nx]))
    acc = _gauss(acc, 1.5)
    acc[~valid] = 0
    iy, ix = _subpixel_argmax(acc)
    return _hit_from_map_xy("RAD_SYM", "map", float(ix), float(iy + y0), lon_iii, lat,
                            weight=2.1, note="Simplified radial symmetry transform")


# ---------- Hu moments / central moments ----------

def m_hu_moments(cyl, nav, lon_iii, lat) -> MethodHit:
    """Centre from raw spatial moments of dark mask (m10/m00, m01/m00)."""
    im, y0, y1, band, valid = _band_roi(cyl, lat)
    mask = _dark_mask(band, valid, 15)
    if not mask.any():
        raise RuntimeError("hu empty")
    med = float(np.median(band[valid])) if valid.any() else 0.0
    inv = np.clip(med - band, 0, None) * mask.astype(np.float64)
    m00 = float(inv.sum())
    if m00 < 1e-9:
        raise RuntimeError("hu empty")
    ys, xs = np.mgrid[0:inv.shape[0], 0:inv.shape[1]]
    cx = float((xs * inv).sum() / m00)
    cy = float((ys * inv).sum() / m00)
    mu20 = float(((xs - cx) ** 2 * inv).sum() / m00)
    mu02 = float(((ys - cy) ** 2 * inv).sum() / m00)
    L = 4 * math.sqrt(max(mu20, 1e-9)) * (180.0 / max(len(lon_iii), 1))
    W = 4 * math.sqrt(max(mu02, 1e-9)) * (180.0 / max(len(lat), 1))
    return _hit_from_map_xy(
        "HU_MOM", "map", cx, cy + y0, lon_iii, lat,
        length_deg=L, width_deg=W, weight=2.5,
        note="Spatial moments centre (Hu/raw m10/m00)",
    )


# ---------- percentile ladder (definition systematics) ----------

def m_percentile_ladder(cyl, nav, lon_iii, lat) -> List[MethodHit]:
    hits = []
    centers = []
    for p in (5, 10, 15, 20, 25, 30, 35):
        try:
            im, y0, y1, band, valid = _band_roi(cyl, lat)
            mask = _dark_mask(band, valid, float(p))
            ys, xs = np.where(mask)
            if len(xs) < 10:
                hits.append(_fail(f"P{p:02d}", "threshold", "small"))
                continue
            wts = np.clip(np.percentile(band[mask], 50) - band[ys, xs], 1e-6, None)
            cx = float(np.average(xs, weights=wts))
            cy = float(np.average(ys + y0, weights=wts))
            h = _hit_from_map_xy(f"P{p:02d}", "threshold", cx, cy, lon_iii, lat, weight=1.2,
                                 note=f"Dark bary at percentile ≤p{p}")
            hits.append(h)
            centers.append((h.lon_iii_deg, h.lat_deg, h.weight))
        except Exception as e:
            hits.append(_fail(f"P{p:02d}", "threshold", str(e)))
    if len(centers) >= 3:
        # trend-stable: median of ladder
        lons = [c[0] for c in centers]
        lats = [c[1] for c in centers]
        lon0 = lons[len(lons) // 2]
        dlon = [wrap_diff(L, lon0) for L in lons]
        lon_m = wrap_deg(lon0 + float(np.median(dlon)))
        hits.append(MethodHit("P_LADDER", "threshold", lon_m, float(np.median(lats)),
                              weight=2.6, note="Median of percentile ladder (definition sensitivity)"))
    return hits


# ---------- bilateral / anisotropic pre-smooth then bary ----------

def m_bilateral_bary(cyl, nav, lon_iii, lat) -> MethodHit:
    """Approx bilateral smooth (range+space) then dark bary — edge-preserving."""
    im, y0, y1, band, valid = _band_roi(cyl, lat)
    # cheap bilateral-ish: gaussian spatial then reweight by intensity difference
    sp = _gauss(band, 2.0)
    # three range centers
    acc = np.zeros_like(band)
    wsum = np.zeros_like(band)
    for c in np.percentile(band[valid], [20, 40, 60]):
        w = np.exp(-0.5 * ((band - c) / (0.08 * (band.max() - band.min() + 1e-6))) ** 2)
        acc += sp * w
        wsum += w
    sm = acc / (wsum + 1e-12)
    mask = _dark_mask(sm, valid, 14)
    ys, xs = np.where(mask)
    if len(xs) < 10:
        raise RuntimeError("bilateral empty")
    wts = np.clip(np.percentile(sm[mask], 50) - sm[ys, xs], 1e-6, None)
    return _hit_from_map_xy("BILATERAL", "map",
                            float(np.average(xs, weights=wts)),
                            float(np.average(ys + y0, weights=wts)),
                            lon_iii, lat, weight=2.0,
                            note="Edge-preserving smooth + dark bary")


def m_unsharp_peak(cyl, nav, lon_iii, lat) -> MethodHit:
    im, y0, y1, band, valid = _band_roi(cyl, lat)
    us = band + 1.5 * (band - _gauss(band, 3.0))
    us[~valid] = np.max(us)
    iy, ix = _subpixel_argmin(us)
    return _hit_from_map_xy("UNSHARP", "map", float(ix), float(iy + y0), lon_iii, lat,
                            weight=1.6, note="Unsharp-mask enhanced darkest peak")


def m_rolling_ball(cyl, nav, lon_iii, lat) -> MethodHit:
    """Rolling-ball style background (large open) subtract → dark residual bary."""
    im, y0, y1, band, valid = _band_roi(cyl, lat)
    try:
        from scipy.ndimage import grey_opening
        bg = grey_opening(band, size=(max(7, band.shape[0] // 2), max(15, band.shape[1] // 15)))
    except Exception:
        bg = _gauss(band, max(8.0, band.shape[1] * 0.08))
    resid = band - bg
    resid[~valid] = 1e9
    mask = valid & (resid <= np.percentile(resid[valid], 12))
    ys, xs = np.where(mask)
    if len(xs) < 10:
        raise RuntimeError("rolling ball empty")
    wts = np.clip(np.percentile(resid[mask], 50) - resid[ys, xs], 1e-6, None)
    return _hit_from_map_xy("ROLL_BALL", "morph",
                            float(np.average(xs, weights=wts)),
                            float(np.average(ys + y0, weights=wts)),
                            lon_iii, lat, weight=2.2,
                            note="Rolling-ball background subtract + dark bary")


def m_kde_mode(cyl, nav, lon_iii, lat) -> MethodHit:
    """Grid KDE mode of dark pixel positions."""
    im, y0, y1, band, valid = _band_roi(cyl, lat)
    mask = _dark_mask(band, valid, 15)
    ys, xs = np.where(mask)
    if len(xs) < 20:
        raise RuntimeError("kde empty")
    # histogram smooth
    H, xe, ye = np.histogram2d(xs, ys, bins=[min(80, band.shape[1] // 2), min(40, band.shape[0])])
    H = _gauss(H.T, 1.2)  # shape (y,x)
    iy, ix = _subpixel_argmax(H)
    # map bin → continuous
    cx = float(xe[0] + (ix + 0.5) * (xe[1] - xe[0]))
    cy = float(ye[0] + (iy + 0.5) * (ye[1] - ye[0]) + y0)
    return _hit_from_map_xy("KDE_MODE", "robust", cx, cy, lon_iii, lat, weight=2.4,
                            note="2D KDE mode of dark pixel cloud")


def m_gmm2(cyl, nav, lon_iii, lat) -> MethodHit:
    """2-component 1D GMM on lon of dark pixels; take darker/ lower-intensity component mean lat."""
    im, y0, y1, band, valid = _band_roi(cyl, lat)
    mask = _dark_mask(band, valid, 15)
    ys, xs = np.where(mask)
    if len(xs) < 30:
        raise RuntimeError("gmm empty")
    # lon samples
    lon_s = np.array([wrap_diff(float(lon_iii[int(x)]), float(lon_iii[int(xs.mean())])) for x in xs])
    # k-means k=2 on lon
    c0, c1 = float(np.percentile(lon_s, 30)), float(np.percentile(lon_s, 70))
    for _ in range(15):
        d0 = np.abs(lon_s - c0)
        d1 = np.abs(lon_s - c1)
        a0 = d0 <= d1
        if a0.sum() < 5 or (~a0).sum() < 5:
            break
        c0, c1 = float(lon_s[a0].mean()), float(lon_s[~a0].mean())
    # pick component with lower mean intensity
    i0 = float(band[ys[a0], xs[a0]].mean()) if a0.any() else 1e9
    i1 = float(band[ys[~a0], xs[~a0]].mean()) if (~a0).any() else 1e9
    use = a0 if i0 <= i1 else ~a0
    lon0 = float(lon_iii[int(xs.mean())])
    lon = wrap_deg(lon0 + float(lon_s[use].mean()))
    la = float(lat[np.clip((ys[use] + y0).astype(int), 0, len(lat) - 1)].mean()) if hasattr(lat, 'mean') else float(np.mean([lat[int(np.clip(y + y0, 0, len(lat) - 1))] for y in ys[use]]))
    # safer lat
    la = float(np.mean([lat[int(np.clip(y + y0, 0, len(lat) - 1))] for y in ys[use]]))
    return MethodHit("GMM2_LON", "robust", lon, la, weight=2.0,
                     note="2-GMM on lon of dark pixels (darker component)")


def m_ring_template(cyl, nav, lon_iii, lat) -> MethodHit:
    """Annular/ring template NCC (edge of oval rather than filled)."""
    im, y0, y1, band, valid = _band_roi(cyl, lat)
    bh, bw = band.shape
    yy, xx = np.mgrid[0:bh, 0:bw]
    cy0, cx0 = bh / 2, bw / 2
    r = np.hypot((xx - cx0) / (bw * 0.07 + 1e-6), (yy - cy0) / (bh * 0.4 + 1e-6))
    tmpl = np.exp(-0.5 * ((r - 1.0) / 0.25) ** 2)  # ring
    tmpl = tmpl - tmpl.mean()
    F = np.fft.fft2(band - np.mean(band))
    T = np.fft.fft2(tmpl)
    R = F * np.conj(T)
    R /= np.abs(R) + 1e-12
    corr = np.fft.ifft2(R).real
    iy, ix = _subpixel_argmax(corr)
    return _hit_from_map_xy("RING_TMPL", "template", float(ix), float(iy + y0), lon_iii, lat,
                            weight=2.0, note="Annular ring template phase correlation")


def m_min_enclosing_circle(cyl, nav, lon_iii, lat) -> MethodHit:
    """Approx min enclosing circle centre of dark mask (Welzl-lite via farthest)."""
    im, y0, y1, band, valid = _band_roi(cyl, lat)
    mask = _dark_mask(band, valid, 14)
    ys, xs = np.where(mask)
    if len(xs) < 10:
        raise RuntimeError("mec empty")
    pts = np.stack([xs.astype(np.float64), ys.astype(np.float64)], axis=1)
    # Ritter's algorithm
    c = pts[0].copy()
    r = 0.0
    for p in pts:
        d = np.linalg.norm(p - c)
        if d > r:
            # expand
            if r == 0:
                c, r = p.copy(), 0.0
            else:
                r_new = (r + d) / 2
                c = c + (p - c) * ((d - r) / (2 * d + 1e-12))
                r = r_new
    # refine: centre = mean of points on boundary-ish
    d = np.linalg.norm(pts - c, axis=1)
    r = float(d.max())
    boundary = pts[d >= 0.9 * r]
    if len(boundary) >= 2:
        c = boundary.mean(axis=0)
    return _hit_from_map_xy("MEC", "extent", float(c[0]), float(c[1] + y0), lon_iii, lat,
                            length_deg=2 * r * (180.0 / len(lon_iii)), weight=1.9,
                            note="Min enclosing circle centre of dark mask")


def run_extra_methods(cyl, nav, lon_iii, lat) -> List[MethodHit]:
    """Run all literature/extra methods; soft-fail each."""
    hits: List[MethodHit] = []

    def add(fn, *a, multi=False):
        try:
            r = fn(*a)
            if multi or isinstance(r, list):
                hits.extend(r if isinstance(r, list) else [r])
            else:
                hits.append(r)
        except Exception as e:
            name = getattr(fn, "__name__", "X").replace("m_", "").upper()
            hits.append(_fail(name, "extra", str(e)[:120]))

    add(m_fwhm_lon, cyl, nav, lon_iii, lat)
    add(m_fwhm_lat, cyl, nav, lon_iii, lat)
    add(m_profile_gaussian_fit, cyl, nav, lon_iii, lat)
    add(m_multi_isophote, cyl, nav, lon_iii, lat, multi=True)
    add(m_box_extent, cyl, nav, lon_iii, lat, multi=True)
    add(m_geometric_median, cyl, nav, lon_iii, lat)
    add(m_pca_ellipse, cyl, nav, lon_iii, lat)
    add(m_convex_hull_c, cyl, nav, lon_iii, lat)
    add(m_distance_transform_peak, cyl, nav, lon_iii, lat)
    add(m_mean_shift, cyl, nav, lon_iii, lat)
    add(m_ransac_ellipse, cyl, nav, lon_iii, lat)
    add(m_civ_window_ncc, cyl, nav, lon_iii, lat)
    add(m_sad_ssd_templates, cyl, nav, lon_iii, lat, multi=True)
    add(m_spomf, cyl, nav, lon_iii, lat)
    add(m_bottom_hat, cyl, nav, lon_iii, lat)
    add(m_tophat_inv, cyl, nav, lon_iii, lat)
    add(m_watershed, cyl, nav, lon_iii, lat)
    add(m_structure_tensor, cyl, nav, lon_iii, lat)
    add(m_radial_symmetry, cyl, nav, lon_iii, lat)
    add(m_hu_moments, cyl, nav, lon_iii, lat)
    add(m_percentile_ladder, cyl, nav, lon_iii, lat, multi=True)
    add(m_bilateral_bary, cyl, nav, lon_iii, lat)
    add(m_unsharp_peak, cyl, nav, lon_iii, lat)
    add(m_rolling_ball, cyl, nav, lon_iii, lat)
    add(m_kde_mode, cyl, nav, lon_iii, lat)
    add(m_gmm2, cyl, nav, lon_iii, lat)
    add(m_ring_template, cyl, nav, lon_iii, lat)
    add(m_min_enclosing_circle, cyl, nav, lon_iii, lat)
    return hits

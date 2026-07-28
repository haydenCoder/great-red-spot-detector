#!/usr/bin/env python3
"""
ALL practical GRS localization methods for a laptop optical pipeline.

Philosophy: run every independent estimator that can help. Report each with a
name, lon/lat, optional size, and weight. Consensus / scatter = systematic
knowledge. No single method is "NASA truth".

Groups:
  A) Map (cylindrical deprojection) — WinJUPOS-desk family
  B) Image-plane — limb-nav coordinates
  C) Template / correlation
  D) Threshold / morphology
  E) Edge / isophote / extent
  F) Spectral (R, R−G) when RGB available
  G) Ensemble (robust combinations of the above)

Soft-fail individually: one bad method never kills the suite.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import warnings

import numpy as np

from verbose_log import CONSOLE
from precision_engine import (
    NavState,
    make_cylindrical,
    to_mono,
    wrap_deg,
    wrap_diff,
    px_to_lonlat,
    _moment_mask_grs,
    _template_match_grs,
    _map_dark_centroid,
    measure_grs_precision,
)


@dataclass
class MethodHit:
    method_id: str
    family: str
    lon_iii_deg: float
    lat_deg: float
    length_deg: Optional[float] = None
    width_deg: Optional[float] = None
    score: float = 0.0
    weight: float = 1.0
    ok: bool = True
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Map geometry helpers (must match make_cylindrical: lon_rel −90..+90, lat +90..−90)
# ---------------------------------------------------------------------------

def _cyl_lon_lat_grids(cyl: np.ndarray, nav: NavState) -> Tuple[np.ndarray, np.ndarray]:
    """
    Return (lon_iii_1d, lat_1d) for map columns/rows.

    lon_iii is wrapped absolute System III — do NOT linearly interpolate it
    across the 0° meridian. Prefer lon_rel for sub-pixel work (see _hit_from_map_xy).
    """
    h, w = cyl.shape[:2]
    lon_rel = np.linspace(-90.0, 90.0, w)
    lon_iii = np.array([wrap_deg(nav.cm_iii_deg + x) for x in lon_rel])
    lat = np.linspace(90.0, -90.0, h)
    return lon_iii, lat


def _mono_cyl(cyl: np.ndarray) -> np.ndarray:
    a = np.asarray(cyl, dtype=np.float64)
    if a.ndim == 3:
        a = a.mean(axis=2)
    return a


def _gauss(im: np.ndarray, sigma: float) -> np.ndarray:
    try:
        from scipy.ndimage import gaussian_filter
        return gaussian_filter(im, sigma=max(0.3, float(sigma)))
    except Exception:
        return im


def _band_slice(lat: np.ndarray, lat0: float = -22.0, half: float = 7.0) -> Tuple[int, int]:
    y_ok = np.where((lat >= lat0 - half) & (lat <= lat0 + half))[0]
    if len(y_ok) < 3:
        # full SEB-ish
        y_ok = np.where((lat >= -32) & (lat <= -12))[0]
    if len(y_ok) < 1:
        return 0, len(lat) - 1
    return int(y_ok.min()), int(y_ok.max())


def _hit_from_map_xy(
    method_id: str,
    family: str,
    cx: float,
    cy: float,
    lon_iii: np.ndarray,
    lat: np.ndarray,
    *,
    length_deg: Optional[float] = None,
    width_deg: Optional[float] = None,
    score: float = 0.0,
    weight: float = 1.0,
    note: str = "",
    cm_iii_deg: Optional[float] = None,
) -> MethodHit:
    """
    Sub-pixel map position → MethodHit.

    CRITICAL: interpolate continuous lon_rel ∈ [-90, +90], then wrap with CM.
    Never lerp wrapped lon_iii (0° meridian → ~180° catastrophic blend).
    """
    h, w = len(lat), len(lon_iii)
    x0 = int(np.clip(math.floor(cx), 0, max(w - 2, 0)))
    y0 = int(np.clip(math.floor(cy), 0, max(h - 2, 0)))
    fx = float(cx - x0)
    fy = float(cy - y0)
    # Continuous relative longitude from column index (map contract: −90..+90)
    lon_rel = -90.0 + (float(cx) / max(w - 1, 1)) * 180.0
    lon_rel = float(np.clip(lon_rel, -90.0, 90.0))
    # Recover CM: prefer explicit, else invert from any finite column of lon_iii
    if cm_iii_deg is not None and math.isfinite(float(cm_iii_deg)):
        cm = float(cm_iii_deg)
    else:
        # lon_iii[i] = wrap(cm + lon_rel[i]); mid column lon_rel≈0 → cm ≈ lon_iii[mid]
        mid = w // 2
        lon_rel_mid = -90.0 + (mid / max(w - 1, 1)) * 180.0
        cm = wrap_deg(float(lon_iii[mid]) - lon_rel_mid)
    lon = wrap_deg(cm + lon_rel)
    if h >= 2:
        la = (1.0 - fy) * float(lat[y0]) + fy * float(lat[min(y0 + 1, h - 1)])
    else:
        la = float(lat[0]) if h else -22.0
    return MethodHit(
        method_id, family, float(lon), float(la),
        length_deg, width_deg, score, weight, True, note,
    )


def _fail(method_id: str, family: str, err: str) -> MethodHit:
    return MethodHit(method_id, family, float("nan"), float("nan"), ok=False, note=str(err)[:200])


# ---------------------------------------------------------------------------
# Individual methods
# ---------------------------------------------------------------------------

def m_map_dark(cyl, nav, lon_iii, lat) -> MethodHit:
    m = _map_dark_centroid(cyl, nav)
    return MethodHit(
        "MAP_DARK", "map", float(m["lon_iii_deg"]), float(m["lat_deg"]),
        m.get("length_deg"), m.get("width_deg"), float(m.get("score") or 0), 3.0,
        note="Cylindrical dark centroid",
    )


def m_template(cyl, nav, lon_iii, lat, length=12.0, width=8.0, tag="TMPL") -> MethodHit:
    t = _template_match_grs(cyl, nav, length_deg=length, width_deg=width)
    return MethodHit(
        tag, "template", float(t["lon_iii_deg"]), float(t["lat_deg"]),
        t.get("length_deg"), t.get("width_deg"), float(t.get("score") or 0), 3.0,
        note=f"NCC template L={length} W={width}",
    )


def m_bary_image(im, nav) -> MethodHit:
    mo = _moment_mask_grs(im, nav)
    return MethodHit(
        "BARY_IMG", "image", float(mo["lon_iii_deg"]), float(mo["lat_deg"]),
        mo.get("length_deg"), mo.get("width_deg"), 0.0, 3.0,
        note="Image-plane intensity-weighted dark barycentre",
    )


def m_engine(im, nav) -> MethodHit:
    p = measure_grs_precision(im, cm_iii_deg=nav.cm_iii_deg, distance_au=nav.distance_au, nav=nav, quiet=True)
    return MethodHit(
        "ENGINE", "ensemble", float(p.lon_iii_deg), float(p.lat_deg),
        float(p.length_deg), float(p.width_deg), 0.0, 2.5,
        note="Precision engine multi-method",
    )


def m_multiscale_ncc(cyl, nav, lon_iii, lat) -> MethodHit:
    try:
        from vlbi_metrology import multiscale_template_match
        t = multiscale_template_match(cyl, nav)
        return MethodHit(
            "MS_NCC", "template", float(t["lon_iii_deg"]), float(t["lat_deg"]),
            t.get("length_deg"), t.get("width_deg"), float(t.get("score") or 0), 3.5,
            note="VLBI multiscale NCC",
        )
    except Exception:
        # fallback: 3 template sizes
        hits = []
        for L, W in ((10, 7), (12, 8), (14, 9)):
            try:
                hits.append(m_template(cyl, nav, lon_iii, lat, L, W, tag=f"TMPL_{L}x{W}"))
            except Exception:
                pass
        ok = [h for h in hits if h.ok]
        if not ok:
            raise RuntimeError("MS_NCC fallback empty")
        # circular mean lon
        ang = np.deg2rad([h.lon_iii_deg for h in ok])
        lon = wrap_deg(math.degrees(math.atan2(np.mean(np.sin(ang)), np.mean(np.cos(ang)))))
        la = float(np.mean([h.lat_deg for h in ok]))
        return MethodHit("MS_NCC", "template", lon, la, 12.0, 8.0, 0.0, 3.0, note="fallback multi-template mean")


def m_perc_dark_bary(cyl, nav, lon_iii, lat, perc: float = 12.0, tag: str = "PERC12") -> MethodHit:
    im = _mono_cyl(cyl)
    y0, y1 = _band_slice(lat)
    band = im[y0 : y1 + 1, :]
    valid = band > 0
    if valid.sum() < 30:
        raise RuntimeError("empty band")
    thr = np.percentile(band[valid], perc)
    mask = valid & (band <= thr)
    ys, xs = np.where(mask)
    if len(xs) < 10:
        raise RuntimeError("mask small")
    wts = thr - band[ys, xs] + 1e-6
    cx = float(np.average(xs, weights=wts))
    cy = float(np.average(ys + y0, weights=wts))
    return _hit_from_map_xy(tag, "threshold", cx, cy, lon_iii, lat, weight=2.0,
                            note=f"Map dark percentile ≤p{perc} barycentre")


def m_otsu_bary(cyl, nav, lon_iii, lat) -> MethodHit:
    im = _mono_cyl(cyl)
    y0, y1 = _band_slice(lat)
    band = im[y0 : y1 + 1, :].copy()
    valid = band > 0
    vals = band[valid]
    if vals.size < 50:
        raise RuntimeError("otsu empty")
    # Otsu on inverted intensity (dark = high for separation of dark class)
    inv = vals.max() - vals
    hist, bin_edges = np.histogram(inv, bins=64)
    hist = hist.astype(np.float64)
    p = hist / (hist.sum() + 1e-12)
    omega = np.cumsum(p)
    mu = np.cumsum(p * np.arange(len(p)))
    mu_t = mu[-1]
    sigma_b = (mu_t * omega - mu) ** 2 / (omega * (1 - omega) + 1e-12)
    k = int(np.nanargmax(sigma_b))
    thr_inv = bin_edges[k]
    thr = vals.max() - thr_inv
    mask = valid & (band <= thr)
    ys, xs = np.where(mask)
    if len(xs) < 10:
        raise RuntimeError("otsu mask small")
    wts = thr - band[ys, xs] + 1e-6
    cx = float(np.average(xs, weights=wts))
    cy = float(np.average(ys + y0, weights=wts))
    return _hit_from_map_xy("OTSU", "threshold", cx, cy, lon_iii, lat, weight=2.0, note="Otsu dark class barycentre")


def m_hp_peak(cyl, nav, lon_iii, lat) -> MethodHit:
    im = _mono_cyl(cyl)
    y0, y1 = _band_slice(lat)
    band = im[y0 : y1 + 1, :]
    hp = band - _gauss(band, max(2.0, band.shape[1] * 0.025))
    # darkest residual (most negative)
    valid = band > 0
    tmp = hp.copy()
    tmp[~valid] = 1e9
    iy, ix = np.unravel_index(np.argmin(tmp), tmp.shape)
    # refine centroid in 7x7
    yb0, yb1 = max(0, iy - 3), min(tmp.shape[0], iy + 4)
    xb0, xb1 = max(0, ix - 3), min(tmp.shape[1], ix + 4)
    patch = -tmp[yb0:yb1, xb0:xb1]
    patch = np.clip(patch, 0, None)
    if patch.sum() <= 0:
        cy, cx = float(iy + y0), float(ix)
    else:
        yy, xx = np.mgrid[yb0:yb1, xb0:xb1]
        cx = float(np.average(xx, weights=patch))
        cy = float(np.average(yy, weights=patch) + y0)
    return _hit_from_map_xy("HP_PEAK", "map", cx, cy, lon_iii, lat, weight=2.0, note="High-pass darkest peak")


def m_bandpass_bary(cyl, nav, lon_iii, lat) -> MethodHit:
    im = _mono_cyl(cyl)
    y0, y1 = _band_slice(lat)
    band = im[y0 : y1 + 1, :]
    dog = _gauss(band, 1.5) - _gauss(band, max(4.0, band.shape[1] * 0.04))
    # dark in DoG: take low values
    valid = band > 0
    vals = dog[valid]
    thr = np.percentile(vals, 15)
    mask = valid & (dog <= thr)
    ys, xs = np.where(mask)
    if len(xs) < 10:
        raise RuntimeError("dog mask")
    wts = thr - dog[ys, xs] + 1e-6
    cx = float(np.average(xs, weights=wts))
    cy = float(np.average(ys + y0, weights=wts))
    return _hit_from_map_xy("DOG_BARY", "map", cx, cy, lon_iii, lat, weight=2.2, note="Difference-of-Gaussians dark bary")


def m_log_blob(cyl, nav, lon_iii, lat) -> MethodHit:
    im = _mono_cyl(cyl)
    y0, y1 = _band_slice(lat)
    band = im[y0 : y1 + 1, :].astype(np.float64)
    # LoG ≈ laplacian of gaussian; dark blob → positive response on inverted
    g = _gauss(band, 3.0)
    try:
        from scipy.ndimage import laplace
        log = -laplace(g)  # dark centers often high after -laplace on bright-subtracted
    except Exception:
        log = g - _gauss(g, 5.0)
    valid = band > 0
    tmp = log.copy()
    tmp[~valid] = -1e9
    iy, ix = np.unravel_index(np.argmax(tmp), tmp.shape)
    cy, cx = float(iy + y0), float(ix)
    return _hit_from_map_xy("LOG_BLOB", "map", cx, cy, lon_iii, lat, weight=1.8, note="Laplacian-of-Gaussian blob")


def m_proj_1d(cyl, nav, lon_iii, lat) -> MethodHit:
    """Longitude of minimum mean intensity in GRS lat band (1D scan)."""
    im = _mono_cyl(cyl)
    y0, y1 = _band_slice(lat, half=5.0)
    band = im[y0 : y1 + 1, :]
    col = np.where(band > 0, band, np.nan)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        profile = np.nanmean(col, axis=0)
    if np.all(np.isnan(profile)):
        raise RuntimeError("proj empty")
    # smooth
    k = max(3, len(profile) // 80)
    kernel = np.ones(k) / k
    sm = np.convolve(np.nan_to_num(profile, nan=np.nanmedian(profile)), kernel, mode="same")
    ix = int(np.argmin(sm))
    # parabolic refine
    if 0 < ix < len(sm) - 1:
        a, b, c = sm[ix - 1], sm[ix], sm[ix + 1]
        den = (a - 2 * b + c)
        if abs(den) > 1e-12:
            ix = ix + 0.5 * (a - c) / den
    cy = 0.5 * (y0 + y1)
    return _hit_from_map_xy("PROJ_1D", "map", float(ix), cy, lon_iii, lat, weight=2.5,
                            note="1D lon scan min intensity in SEB band")


def m_lat_track(cyl, nav, lon_iii, lat) -> MethodHit:
    """For each lat row find darkest lon; pick row nearest −22°."""
    im = _mono_cyl(cyl)
    y0, y1 = _band_slice(lat, half=8.0)
    best = None
    for yi in range(y0, y1 + 1):
        row = im[yi, :]
        valid = row > 0
        if valid.sum() < 10:
            continue
        r = row.copy()
        r[~valid] = 1e9
        ix = int(np.argmin(r))
        la = float(lat[yi])
        score = -abs(la + 22.0) - 0.01 * float(row[ix])
        if best is None or score > best[0]:
            best = (score, float(ix), float(yi), la)
    if best is None:
        raise RuntimeError("lat track empty")
    return _hit_from_map_xy("LAT_TRACK", "map", best[1], best[2], lon_iii, lat, weight=2.0,
                            note="Per-latitude darkest lon near −22°")


def m_phase_corr(cyl, nav, lon_iii, lat) -> MethodHit:
    """Phase correlation of band vs dark elliptical template."""
    im = _mono_cyl(cyl)
    y0, y1 = _band_slice(lat)
    band = im[y0 : y1 + 1, :].astype(np.float64)
    bh, bw = band.shape
    # build template same size, dark ellipse center
    yy, xx = np.mgrid[0:bh, 0:bw]
    cy0, cx0 = bh / 2, bw / 2
    # ~12° x 8° in map pixels: lon span of map is 180° across width
    rx = max(3.0, (12.0 / 180.0) * bw / 2)
    ry = max(2.0, (8.0 / 180.0) * len(lat) / 2)  # full map height = 180°
    # band height is only ~14° → scale ry to band
    ry = max(2.0, bh * 0.35)
    tmpl = np.exp(-0.5 * (((xx - cx0) / rx) ** 2 + ((yy - cy0) / ry) ** 2))
    tmpl = 1.0 - tmpl  # dark center
    # phase correlation
    F = np.fft.fft2(band - np.mean(band))
    T = np.fft.fft2(tmpl - np.mean(tmpl))
    R = F * np.conj(T)
    R /= np.abs(R) + 1e-12
    corr = np.fft.ifft2(R).real
    iy, ix = np.unravel_index(np.argmax(corr), corr.shape)
    # peak is shift of template; center ≈ shift
    cy, cx = float(iy + y0), float(ix)
    return _hit_from_map_xy("PHASE_CORR", "template", cx, cy, lon_iii, lat, weight=2.2,
                            note="Phase-only correlation with dark ellipse")


def m_isophote_center(cyl, nav, lon_iii, lat) -> MethodHit:
    """Centroid of isophote at low percentile (dark contour)."""
    im = _mono_cyl(cyl)
    y0, y1 = _band_slice(lat)
    band = im[y0 : y1 + 1, :]
    valid = band > 0
    thr = np.percentile(band[valid], 18)
    mask = valid & (band <= thr)
    try:
        from scipy.ndimage import binary_closing, binary_opening, label
        mask = binary_closing(binary_opening(mask, iterations=1), iterations=1)
        lab, n = label(mask)
        if n == 0:
            raise RuntimeError("no isophote")
        # largest component
        counts = np.bincount(lab.ravel())
        counts[0] = 0
        m = lab == int(np.argmax(counts))
    except Exception:
        m = mask
    ys, xs = np.where(m)
    if len(xs) < 8:
        raise RuntimeError("isophote small")
    cx, cy = float(xs.mean()), float(ys.mean() + y0)
    L = float(xs.max() - xs.min() + 1) * (180.0 / len(lon_iii))
    W = float(ys.max() - ys.min() + 1) * (180.0 / len(lat))
    return _hit_from_map_xy("ISOPHOTE", "edge", cx, cy, lon_iii, lat, length_deg=L, width_deg=W, weight=2.0,
                            note="Isophote dark contour centroid")


def m_quad_moment(cyl, nav, lon_iii, lat) -> MethodHit:
    """Second-moment (inertia) center of dark mask."""
    im = _mono_cyl(cyl)
    y0, y1 = _band_slice(lat)
    band = im[y0 : y1 + 1, :]
    valid = band > 0
    thr = np.percentile(band[valid], 15)
    mask = valid & (band <= thr)
    ys, xs = np.where(mask)
    if len(xs) < 15:
        raise RuntimeError("quad small")
    wts = thr - band[ys, xs] + 1e-6
    cx = float(np.average(xs, weights=wts))
    cy = float(np.average(ys, weights=wts))
    # covariance ellipse sizes
    x = xs - cx
    y = ys - cy
    cov_xx = float(np.average(x * x, weights=wts))
    cov_yy = float(np.average(y * y, weights=wts))
    L = 4 * math.sqrt(max(cov_xx, 1e-6)) * (180.0 / len(lon_iii))
    W = 4 * math.sqrt(max(cov_yy, 1e-6)) * (180.0 / len(lat))
    return _hit_from_map_xy("QUAD_MOM", "map", cx, cy + y0, lon_iii, lat, length_deg=L, width_deg=W, weight=2.3,
                            note="Weighted second-moment ellipse center")


def m_morph_bary(cyl, nav, lon_iii, lat) -> MethodHit:
    im = _mono_cyl(cyl)
    y0, y1 = _band_slice(lat)
    band = im[y0 : y1 + 1, :]
    valid = band > 0
    thr = np.percentile(band[valid], 14)
    mask = valid & (band <= thr)
    try:
        from scipy.ndimage import binary_opening, binary_closing, binary_fill_holes, label
        mask = binary_fill_holes(binary_closing(binary_opening(mask, iterations=2), iterations=2))
        lab, n = label(mask)
        if n == 0:
            raise RuntimeError("morph empty")
        counts = np.bincount(lab.ravel()); counts[0] = 0
        m = lab == int(np.argmax(counts))
    except Exception:
        m = mask
    ys, xs = np.where(m)
    if len(xs) < 10:
        raise RuntimeError("morph small")
    cx, cy = float(xs.mean()), float(ys.mean() + y0)
    return _hit_from_map_xy("MORPH", "threshold", cx, cy, lon_iii, lat, weight=2.0,
                            note="Morphological open/close dark component")


def m_adaptive_bary(cyl, nav, lon_iii, lat) -> MethodHit:
    im = _mono_cyl(cyl)
    y0, y1 = _band_slice(lat)
    band = im[y0 : y1 + 1, :]
    local = _gauss(band, max(5.0, band.shape[1] * 0.05))
    dark = (band < local * 0.97) & (band > 0)
    ys, xs = np.where(dark)
    if len(xs) < 15:
        raise RuntimeError("adaptive empty")
    wts = np.clip(local[ys, xs] - band[ys, xs], 1e-6, None)
    cx = float(np.average(xs, weights=wts))
    cy = float(np.average(ys + y0, weights=wts))
    return _hit_from_map_xy("ADAPTIVE", "threshold", cx, cy, lon_iii, lat, weight=2.0,
                            note="Local adaptive dark (band < local mean)")


def m_seed_grow(cyl, nav, lon_iii, lat) -> MethodHit:
    """Flood-fill grow from darkest pixel in band with intensity gate."""
    im = _mono_cyl(cyl)
    y0, y1 = _band_slice(lat)
    band = im[y0 : y1 + 1, :].copy()
    valid = band > 0
    tmp = band.copy()
    tmp[~valid] = 1e9
    sy, sx = np.unravel_index(np.argmin(tmp), tmp.shape)
    seed_i = float(band[sy, sx])
    thr = seed_i + 0.35 * (np.median(band[valid]) - seed_i)
    # BFS grow
    h, w = band.shape
    seen = np.zeros((h, w), dtype=bool)
    stack = [(sy, sx)]
    seen[sy, sx] = True
    pts = []
    while stack and len(pts) < 50000:
        y, x = stack.pop()
        if band[y, x] > thr or not valid[y, x]:
            continue
        pts.append((y, x))
        for dy, dx in ((0, 1), (0, -1), (1, 0), (-1, 0)):
            ny, nx = y + dy, x + dx
            if 0 <= ny < h and 0 <= nx < w and not seen[ny, nx]:
                seen[ny, nx] = True
                stack.append((ny, nx))
    if len(pts) < 10:
        raise RuntimeError("seed grow small")
    ys = np.array([p[0] for p in pts], dtype=np.float64)
    xs = np.array([p[1] for p in pts], dtype=np.float64)
    wts = thr - band[ys.astype(int), xs.astype(int)] + 1e-6
    cx = float(np.average(xs, weights=wts))
    cy = float(np.average(ys + y0, weights=wts))
    return _hit_from_map_xy("SEED_GROW", "threshold", cx, cy, lon_iii, lat, weight=2.1,
                            note="Region grow from darkest seed")


def m_sobel_ring(cyl, nav, lon_iii, lat) -> MethodHit:
    """Center of mass of gradient magnitude (edge ring of oval)."""
    im = _mono_cyl(cyl)
    y0, y1 = _band_slice(lat)
    band = im[y0 : y1 + 1, :]
    try:
        from scipy.ndimage import sobel
        gx = sobel(band, axis=1)
        gy = sobel(band, axis=0)
        mag = np.hypot(gx, gy)
    except Exception:
        mag = np.abs(np.diff(band, axis=1, prepend=band[:, :1])) + np.abs(np.diff(band, axis=0, prepend=band[:1, :]))
    valid = band > 0
    thr = np.percentile(mag[valid], 75)
    mask = valid & (mag >= thr)
    ys, xs = np.where(mask)
    if len(xs) < 20:
        raise RuntimeError("sobel empty")
    cx, cy = float(xs.mean()), float(ys.mean() + y0)
    return _hit_from_map_xy("SOBEL_RING", "edge", cx, cy, lon_iii, lat, weight=1.5,
                            note="Gradient-magnitude ring centroid")


def m_flux_powers(cyl, nav, lon_iii, lat) -> List[MethodHit]:
    """Inverse-intensity moments with power 1,2,4 (core-weighted)."""
    im = _mono_cyl(cyl)
    y0, y1 = _band_slice(lat)
    band = im[y0 : y1 + 1, :]
    valid = band > 0
    thr = np.percentile(band[valid], 20)
    mask = valid & (band <= thr)
    ys, xs = np.where(mask)
    if len(xs) < 10:
        return [_fail("FLUX_P1", "map", "empty")]
    inv = thr - band[ys, xs] + 1e-6
    out = []
    for p, tag, w in ((1.0, "FLUX_P1", 2.0), (2.0, "FLUX_P2", 2.2), (4.0, "FLUX_P4", 2.0)):
        wts = inv ** p
        cx = float(np.average(xs, weights=wts))
        cy = float(np.average(ys + y0, weights=wts))
        out.append(_hit_from_map_xy(tag, "map", cx, cy, lon_iii, lat, weight=w,
                                    note=f"Inverse-flux moment power={p}"))
    return out


def m_rgb_methods(
    channels: Optional[Dict[str, np.ndarray]],
    nav: NavState,
    cyl_builder: Callable,
) -> List[MethodHit]:
    """Red-only and R−G chromatic methods when RGB available."""
    out: List[MethodHit] = []
    if not channels or "R" not in channels:
        return out
    try:
        R = to_mono(channels["R"])
        mo = _moment_mask_grs(R, nav)
        out.append(MethodHit(
            "RED_BARY", "spectral", float(mo["lon_iii_deg"]), float(mo["lat_deg"]),
            mo.get("length_deg"), mo.get("width_deg"), 0.0, 2.8,
            note="Barycentre on red channel only (GRS is red-dark)",
        ))
    except Exception as e:
        out.append(_fail("RED_BARY", "spectral", str(e)))
    if "G" in channels:
        try:
            R = to_mono(channels["R"]).astype(np.float64)
            G = to_mono(channels["G"]).astype(np.float64)
            # GRS often darker/redder: high R relative? Actually GRS is dark in continuum but redder —
            # use (G-R) residual: higher when R dark relative... simpler: measure on R-G where GRS is often positive brown
            chrom = R - G
            # invert so dark-in-red relative shows as low for moment mask on mono-like
            # Build fake mono: lower where R is low and R-G high
            score_im = R - 0.5 * np.clip(R - G, -1, 1)
            mo = _moment_mask_grs(score_im, nav)
            out.append(MethodHit(
                "CHROM_RG", "spectral", float(mo["lon_iii_deg"]), float(mo["lat_deg"]),
                mo.get("length_deg"), mo.get("width_deg"), 0.0, 2.4,
                note="R−G chromatic-aware dark score",
            ))
        except Exception as e:
            out.append(_fail("CHROM_RG", "spectral", str(e)))
    if "B" in channels and "R" in channels:
        try:
            R = to_mono(channels["R"]).astype(np.float64)
            B = to_mono(channels["B"]).astype(np.float64)
            score_im = 0.6 * R + 0.4 * B
            mo = _moment_mask_grs(score_im, nav)
            out.append(MethodHit(
                "RB_BLEND", "spectral", float(mo["lon_iii_deg"]), float(mo["lat_deg"]),
                mo.get("length_deg"), mo.get("width_deg"), 0.0, 1.8,
                note="0.6R+0.4B blend barycentre",
            ))
        except Exception as e:
            out.append(_fail("RB_BLEND", "spectral", str(e)))
    return out


def m_edges_extent(cyl, nav, lon_iii, lat) -> List[MethodHit]:
    """West/east edges + midpoint + oval (WinJUPOS-like extent)."""
    out: List[MethodHit] = []
    im = _mono_cyl(cyl)
    y0, y1 = _band_slice(lat)
    band = im[y0 : y1 + 1, :]
    valid = band > 0
    if valid.sum() < 40:
        return [_fail("OVAL", "extent", "band empty")]
    thr = np.percentile(band[valid], 14)
    mask = valid & (band <= thr)
    try:
        from scipy.ndimage import binary_opening, binary_closing, label
        mask = binary_closing(binary_opening(mask, iterations=1), iterations=2)
        lab, n = label(mask)
        if n:
            counts = np.bincount(lab.ravel()); counts[0] = 0
            mask = lab == int(np.argmax(counts))
    except Exception:
        pass
    ys, xs = np.where(mask)
    if len(xs) < 20:
        return [_fail("OVAL", "extent", "mask small")]
    wts = thr - band[ys, xs] + 1e-6
    cx = float(np.average(xs, weights=wts))
    cy = float(np.average(ys + y0, weights=wts))
    L = float(xs.max() - xs.min() + 1) * (180.0 / len(lon_iii))
    W = float(ys.max() - ys.min() + 1) * (180.0 / len(lat))
    oval = _hit_from_map_xy("OVAL", "extent", cx, cy, lon_iii, lat, length_deg=L, width_deg=W, weight=2.5, note="Dark oval barycentre")
    out.append(oval)
    # edges at mid lat strip
    lat_c = float(lat[int(np.clip(round(cy), 0, len(lat) - 1))])
    strip = mask & (np.abs(lat[y0 : y1 + 1, None] - lat_c) <= 2.5)[: mask.shape[0], :]
    # fix strip shape
    lat_band = lat[y0 : y1 + 1]
    row_ok = np.abs(lat_band - lat_c) <= 2.5
    strip = mask.copy()
    strip[~row_ok, :] = False
    sy, sx = np.where(strip)
    if len(sx) < 8:
        sy, sx = ys, xs
    lon_c = oval.lon_iii_deg
    rel = np.array([wrap_diff(float(lon_iii[int(x)]), lon_c) for x in sx], dtype=np.float64)
    lon_hi = wrap_deg(lon_c + float(np.percentile(rel, 95)))
    lon_lo = wrap_deg(lon_c + float(np.percentile(rel, 5)))
    out.append(MethodHit("EDGE_W", "edge", lon_hi, lat_c, weight=1.5, note="Higher-lon extent edge"))
    out.append(MethodHit("EDGE_E", "edge", lon_lo, lat_c, weight=1.5, note="Lower-lon extent edge"))
    half = wrap_diff(lon_hi, lon_lo) / 2.0
    mid = wrap_deg(lon_lo + half)
    out.append(MethodHit("MID", "extent", mid, lat_c, abs(wrap_diff(lon_hi, lon_lo)), W, weight=2.0,
                         note="Edge midpoint"))
    return out


def m_symmetry(cyl, nav, lon_iii, lat) -> MethodHit:
    """Autocorrelation peak of inverted band (symmetry center)."""
    im = _mono_cyl(cyl)
    y0, y1 = _band_slice(lat)
    band = im[y0 : y1 + 1, :].astype(np.float64)
    valid = band > 0
    inv = np.zeros_like(band)
    med = np.median(band[valid]) if valid.any() else 0
    inv[valid] = med - band[valid]
    inv = np.clip(inv, 0, None)
    F = np.fft.fft2(inv)
    corr = np.fft.ifft2(F * np.conj(F)).real
    corr = np.fft.fftshift(corr)
    iy, ix = np.unravel_index(np.argmax(corr), corr.shape)
    # autocorrelation peaks at 0 lag for self — use centroid of inv instead as symmetry proxy
    ys, xs = np.where(inv > np.percentile(inv[valid], 70) if valid.any() else 0)
    if len(xs) < 10:
        raise RuntimeError("symmetry empty")
    cx, cy = float(xs.mean()), float(ys.mean() + y0)
    return _hit_from_map_xy("SYMMETRY", "map", cx, cy, lon_iii, lat, weight=1.5,
                            note="High inverse-intensity mass center (symmetry proxy)")


def m_min_pixel(cyl, nav, lon_iii, lat) -> MethodHit:
    im = _mono_cyl(cyl)
    y0, y1 = _band_slice(lat, half=5.0)
    band = im[y0 : y1 + 1, :].copy()
    band[band <= 0] = 1e9
    iy, ix = np.unravel_index(np.argmin(band), band.shape)
    return _hit_from_map_xy("MIN_PIX", "map", float(ix), float(iy + y0), lon_iii, lat, weight=1.0,
                            note="Single darkest pixel (noisy but unbiased seed)")


# ---------------------------------------------------------------------------
# Ensemble methods
# ---------------------------------------------------------------------------

def _circular_mean_lon(lons: Sequence[float]) -> float:
    ang = np.deg2rad(np.asarray(lons, dtype=np.float64))
    return wrap_deg(math.degrees(math.atan2(np.mean(np.sin(ang)), np.mean(np.cos(ang)))))


def ensemble_from_hits(hits: Sequence[MethodHit]) -> List[MethodHit]:
    """Robust combinations of successful center methods (exclude pure edges)."""
    centers = [
        h for h in hits
        if h.ok
        and math.isfinite(h.lon_iii_deg)
        and h.method_id not in ("EDGE_W", "EDGE_E", "EDGE-W", "EDGE-E", "GS-EDGE-W", "GS-EDGE-E", "MIN_PIX")
        and "EDGE" not in h.method_id
    ]
    out: List[MethodHit] = []
    if len(centers) < 2:
        return out
    lons = [h.lon_iii_deg for h in centers]
    lats = [h.lat_deg for h in centers]
    wts = np.array([h.weight for h in centers], dtype=np.float64)
    wts = wts / (wts.sum() + 1e-12)

    # Weighted circular mean
    ang = np.deg2rad(lons)
    lon_w = wrap_deg(math.degrees(math.atan2(np.sum(wts * np.sin(ang)), np.sum(wts * np.cos(ang)))))
    lat_w = float(np.sum(wts * np.asarray(lats)))
    out.append(MethodHit("ENS_WMEAN", "ensemble", lon_w, lat_w, weight=3.5,
                         note=f"Weighted mean of {len(centers)} methods"))

    # Median (componentwise; lon via circular median approx = median of unwrap about mean)
    lon0 = _circular_mean_lon(lons)
    dlon = [wrap_diff(L, lon0) for L in lons]
    lon_med = wrap_deg(lon0 + float(np.median(dlon)))
    lat_med = float(np.median(lats))
    out.append(MethodHit("ENS_MEDIAN", "ensemble", lon_med, lat_med, weight=3.5,
                         note="Robust median of methods"))

    # Trimmed mean (drop 20% outliers by lon residual)
    order = np.argsort(np.abs(dlon))
    k = max(2, int(len(order) * 0.8))
    keep = order[:k]
    lon_t = _circular_mean_lon([lons[i] for i in keep])
    lat_t = float(np.mean([lats[i] for i in keep]))
    out.append(MethodHit("ENS_TRIM", "ensemble", lon_t, lat_t, weight=3.0,
                         note="20% lon-outlier trimmed mean"))

    # Medoid: method closest to median
    best, best_d = None, 1e99
    for h in centers:
        d = abs(wrap_diff(h.lon_iii_deg, lon_med)) + abs(h.lat_deg - lat_med)
        if d < best_d:
            best_d, best = d, h
    if best:
        out.append(MethodHit("ENS_MEDOID", "ensemble", best.lon_iii_deg, best.lat_deg,
                             best.length_deg, best.width_deg, weight=3.2,
                             note=f"Medoid method={best.method_id}"))

    # Family-only ensembles
    for fam in ("map", "template", "threshold", "spectral", "image"):
        sub = [h for h in centers if h.family == fam]
        if len(sub) < 2:
            continue
        lon_f = _circular_mean_lon([h.lon_iii_deg for h in sub])
        lat_f = float(np.mean([h.lat_deg for h in sub]))
        out.append(MethodHit(f"ENS_{fam.upper()}", "ensemble", lon_f, lat_f, weight=2.5,
                             note=f"Mean of family={fam} (n={len(sub)})"))

    return out


# ---------------------------------------------------------------------------
# Master runner
# ---------------------------------------------------------------------------

METHOD_CATALOG: List[Dict[str, str]] = [
    # core
    {"id": "MAP_DARK", "family": "map", "desc": "Cylindrical dark centroid"},
    {"id": "BARY_IMG", "family": "image", "desc": "Image-plane dark barycentre"},
    {"id": "ENGINE", "family": "ensemble", "desc": "Precision engine consensus"},
    {"id": "MS_NCC", "family": "template", "desc": "Multiscale / multi-template NCC"},
    {"id": "TMPL_10x7", "family": "template", "desc": "Template 10×7°"},
    {"id": "TMPL_12x8", "family": "template", "desc": "Template 12×8°"},
    {"id": "TMPL_14x9", "family": "template", "desc": "Template 14×9°"},
    {"id": "PERC08", "family": "threshold", "desc": "p8 dark bary"},
    {"id": "PERC12", "family": "threshold", "desc": "p12 dark bary"},
    {"id": "PERC18", "family": "threshold", "desc": "p18 dark bary"},
    {"id": "OTSU", "family": "threshold", "desc": "Otsu dark class"},
    {"id": "HP_PEAK", "family": "map", "desc": "High-pass darkest peak"},
    {"id": "DOG_BARY", "family": "map", "desc": "Difference-of-Gaussians bary"},
    {"id": "LOG_BLOB", "family": "map", "desc": "Laplacian-of-Gaussian blob"},
    {"id": "PROJ_1D", "family": "map", "desc": "1D longitude intensity scan"},
    {"id": "LAT_TRACK", "family": "map", "desc": "Per-lat darkest track"},
    {"id": "PHASE_CORR", "family": "template", "desc": "Phase correlation"},
    {"id": "ISOPHOTE", "family": "edge", "desc": "Dark isophote centroid"},
    {"id": "QUAD_MOM", "family": "map", "desc": "Second-moment ellipse center"},
    {"id": "MORPH", "family": "threshold", "desc": "Morphological component"},
    {"id": "ADAPTIVE", "family": "threshold", "desc": "Adaptive local dark"},
    {"id": "SEED_GROW", "family": "threshold", "desc": "Region grow from darkest seed"},
    {"id": "SOBEL_RING", "family": "edge", "desc": "Gradient ring centroid"},
    {"id": "FLUX_P1", "family": "map", "desc": "Inverse-flux p=1"},
    {"id": "FLUX_P2", "family": "map", "desc": "Inverse-flux p=2"},
    {"id": "FLUX_P4", "family": "map", "desc": "Inverse-flux p=4 (core)"},
    {"id": "SYMMETRY", "family": "map", "desc": "Symmetry / mass center"},
    {"id": "MIN_PIX", "family": "map", "desc": "Darkest pixel"},
    {"id": "RED_BARY", "family": "spectral", "desc": "Red channel bary"},
    {"id": "CHROM_RG", "family": "spectral", "desc": "R−G chromatic"},
    {"id": "RB_BLEND", "family": "spectral", "desc": "R+B blend"},
    {"id": "OVAL", "family": "extent", "desc": "Oval fit center"},
    {"id": "EDGE_W", "family": "edge", "desc": "West/high lon edge (JUPOS-like)"},
    {"id": "EDGE_E", "family": "edge", "desc": "East/low lon edge (JUPOS-like)"},
    {"id": "MID", "family": "extent", "desc": "Edge midpoint"},
    # literature / extra
    {"id": "FWHM_LON", "family": "profile", "desc": "FWHM centre of 1D lon cut"},
    {"id": "FWHM_LAT", "family": "profile", "desc": "FWHM centre of 1D lat cut"},
    {"id": "GAUSS_1D", "family": "profile", "desc": "Gaussian fit lon profile"},
    {"id": "MULTI_ISO", "family": "isophote", "desc": "Multi-level isophote mean (IRAF-style)"},
    {"id": "ISO_P08", "family": "isophote", "desc": "Isophote p8"},
    {"id": "BOX_C", "family": "extent", "desc": "Bounding-box centre (JUPOS extent)"},
    {"id": "EDGE_N", "family": "edge", "desc": "Northern edge"},
    {"id": "EDGE_S", "family": "edge", "desc": "Southern edge"},
    {"id": "GEOM_MED", "family": "robust", "desc": "Geometric median dark pixels"},
    {"id": "PCA_ELL", "family": "extent", "desc": "PCA inertia ellipse centre"},
    {"id": "HULL_C", "family": "extent", "desc": "Convex-hull centroid"},
    {"id": "DIST_PEAK", "family": "map", "desc": "Distance-transform medial peak"},
    {"id": "MEAN_SHIFT", "family": "robust", "desc": "Mean-shift density mode"},
    {"id": "RANSAC_ELL", "family": "extent", "desc": "RANSAC ellipse/circle centre"},
    {"id": "CIV_WIN", "family": "template", "desc": "CIV-style window ZNCC (Asay-Davis family)"},
    {"id": "SAD_TMPL", "family": "template", "desc": "SAD matched template"},
    {"id": "SSD_TMPL", "family": "template", "desc": "SSD matched template"},
    {"id": "SPOMF", "family": "template", "desc": "Symmetric phase-only matched filter"},
    {"id": "BOTTOM_HAT", "family": "morph", "desc": "Morphological bottom-hat"},
    {"id": "TOPHAT_INV", "family": "morph", "desc": "Top-hat on inverted"},
    {"id": "WATERSHED", "family": "morph", "desc": "Watershed basin centre"},
    {"id": "STRUCT_T", "family": "edge", "desc": "Structure-tensor energy"},
    {"id": "RAD_SYM", "family": "map", "desc": "Radial symmetry transform"},
    {"id": "HU_MOM", "family": "map", "desc": "Spatial/Hu moments centre"},
    {"id": "P_LADDER", "family": "threshold", "desc": "Percentile ladder median"},
    {"id": "BILATERAL", "family": "map", "desc": "Bilateral smooth + bary"},
    {"id": "UNSHARP", "family": "map", "desc": "Unsharp-mask peak"},
    {"id": "ROLL_BALL", "family": "morph", "desc": "Rolling-ball background residual"},
    {"id": "KDE_MODE", "family": "robust", "desc": "2D KDE mode"},
    {"id": "GMM2_LON", "family": "robust", "desc": "2-GMM on lon of dark pixels"},
    {"id": "RING_TMPL", "family": "template", "desc": "Annular ring template"},
    {"id": "MEC", "family": "extent", "desc": "Min enclosing circle centre"},
    # ensembles
    {"id": "ENS_WMEAN", "family": "ensemble", "desc": "Weighted mean ensemble"},
    {"id": "ENS_MEDIAN", "family": "ensemble", "desc": "Median ensemble"},
    {"id": "ENS_TRIM", "family": "ensemble", "desc": "Trimmed mean ensemble"},
    {"id": "ENS_MEDOID", "family": "ensemble", "desc": "Medoid method"},
]


def run_all_methods(
    image: np.ndarray,
    nav: NavState,
    *,
    channels: Optional[Dict[str, np.ndarray]] = None,
    map_width: int = 1800,
    map_height: int = 900,
) -> Dict[str, Any]:
    """
    Run every method. Returns dict with hits list, n_ok, scatter, primary suggestion.
    """
    im = to_mono(image)
    CONSOLE.info("ALL-METHODS suite starting…")
    # Cap map size for speed when running 80+ methods
    map_width = int(min(map_width, 1400))
    map_height = int(min(map_height, 700))
    cyl = make_cylindrical(im, nav, width=map_width, height=map_height)
    lon_iii, lat = _cyl_lon_lat_grids(cyl, nav)

    hits: List[MethodHit] = []

    def add(fn, *a, **k):
        try:
            r = fn(*a, **k)
            if isinstance(r, list):
                hits.extend(r)
            else:
                hits.append(r)
        except Exception as e:
            name = getattr(fn, "__name__", "meth").replace("m_", "").upper()
            hits.append(_fail(name, "?", str(e)))
            CONSOLE.debug(f"method fail {name}: {e}")

    add(m_map_dark, cyl, nav, lon_iii, lat)
    add(m_bary_image, im, nav)
    add(m_engine, im, nav)
    add(m_multiscale_ncc, cyl, nav, lon_iii, lat)
    for L, W, tag in ((10, 7, "TMPL_10x7"), (12, 8, "TMPL_12x8"), (14, 9, "TMPL_14x9")):
        add(m_template, cyl, nav, lon_iii, lat, L, W, tag)
    for p, tag in ((8, "PERC08"), (12, "PERC12"), (18, "PERC18")):
        add(m_perc_dark_bary, cyl, nav, lon_iii, lat, p, tag)
    add(m_otsu_bary, cyl, nav, lon_iii, lat)
    add(m_hp_peak, cyl, nav, lon_iii, lat)
    add(m_bandpass_bary, cyl, nav, lon_iii, lat)
    add(m_log_blob, cyl, nav, lon_iii, lat)
    add(m_proj_1d, cyl, nav, lon_iii, lat)
    add(m_lat_track, cyl, nav, lon_iii, lat)
    add(m_phase_corr, cyl, nav, lon_iii, lat)
    add(m_isophote_center, cyl, nav, lon_iii, lat)
    add(m_quad_moment, cyl, nav, lon_iii, lat)
    add(m_morph_bary, cyl, nav, lon_iii, lat)
    add(m_adaptive_bary, cyl, nav, lon_iii, lat)
    add(m_seed_grow, cyl, nav, lon_iii, lat)
    add(m_sobel_ring, cyl, nav, lon_iii, lat)
    add(m_flux_powers, cyl, nav, lon_iii, lat)
    add(m_symmetry, cyl, nav, lon_iii, lat)
    add(m_min_pixel, cyl, nav, lon_iii, lat)
    hits.extend(m_rgb_methods(channels, nav, lambda x: make_cylindrical(x, nav, map_width, map_height)))
    hits.extend(m_edges_extent(cyl, nav, lon_iii, lat))

    # Literature + classical extras (JUPOS, CIV-style, isophotes, FWHM, mean-shift, …)
    try:
        from all_methods_extra import run_extra_methods, LITERATURE_NOTES
        extra = run_extra_methods(cyl, nav, lon_iii, lat)
        hits.extend(extra)
        CONSOLE.info(f"  extra literature methods: {sum(1 for h in extra if h.ok)}/{len(extra)} ok")
    except Exception as e:
        CONSOLE.warn(f"extra methods package: {e}")
        LITERATURE_NOTES = []

    # Ensembles last (include extras in consensus)
    hits.extend(ensemble_from_hits(hits))

    ok = [h for h in hits if h.ok and math.isfinite(h.lon_iii_deg)]
    # scatter among centers
    centers = [h for h in ok if "EDGE" not in h.method_id]
    scatter_lon = scatter_lat = 0.0
    primary = None
    if centers:
        # prefer ENS_MEDIAN > ENS_WMEAN > MS_NCC > MAP_DARK > BARY
        pref = ["ENS_MEDIAN", "ENS_WMEAN", "ENS_MEDOID", "MS_NCC", "MAP_DARK", "BARY_IMG", "QUAD_MOM", "ENGINE"]
        by_id = {h.method_id: h for h in centers}
        for p in pref:
            if p in by_id:
                primary = by_id[p]
                break
        if primary is None:
            primary = max(centers, key=lambda h: h.weight)
        dl = [wrap_diff(h.lon_iii_deg, primary.lon_iii_deg) for h in centers]
        da = [h.lat_deg - primary.lat_deg for h in centers]
        # reject wild outliers for scatter
        dl2 = [d for d in dl if abs(d) < 12]
        da2 = [d for d in da if abs(d) < 8]
        scatter_lon = float(np.std(dl2)) if len(dl2) >= 2 else 0.0
        scatter_lat = float(np.std(da2)) if len(da2) >= 2 else 0.0

    CONSOLE.ok(
        f"ALL-METHODS done: {len(ok)}/{len(hits)} ok  "
        f"primary={primary.method_id if primary else None}  "
        f"scatter_lon={scatter_lon:.3f}°"
    )

    # family breakdown
    by_fam: Dict[str, int] = {}
    for h in ok:
        by_fam[h.family] = by_fam.get(h.family, 0) + 1

    lit = []
    try:
        from all_methods_extra import LITERATURE_NOTES
        lit = list(LITERATURE_NOTES)
    except Exception:
        pass

    return {
        "n_total": len(hits),
        "n_ok": len(ok),
        "n_failed": len(hits) - len(ok),
        "scatter_lon_deg": scatter_lon,
        "scatter_lat_deg": scatter_lat,
        "primary_method": primary.method_id if primary else None,
        "primary_lon_iii_deg": primary.lon_iii_deg if primary else None,
        "primary_lat_deg": primary.lat_deg if primary else None,
        "methods": [h.to_dict() for h in hits],
        "by_family": by_fam,
        "catalog": METHOD_CATALOG,
        "literature_notes": lit,
        "note": (
            "Exhaustive laptop GRS localization suite: classical CV + map photometry + "
            "JUPOS/WinJUPOS-style edges + CIV-window correlation + isophote ladders + "
            "robust geometry + ensembles. Primary prefers robust ensemble. "
            "Not NASA truth — professional multi-method procedure."
        ),
    }

#!/usr/bin/env python3
"""rgb_combine.py — filter-wheel RGB compositing with full rotation
derotation: the WinJUPOS "RGB/RGBR combine" workflow, done with exact
ephemeris geometry.

WHY THIS MODULE EXISTS
======================
Serious planetary colour work is shot with a monochrome camera through a
filter wheel: a red sequence, then green, then blue (sometimes IR/UV/CH4).
Jupiter rotates 36.3 deg/h on its axis, so the planet turns 1.2-3 deg
between filter sequences — several pixels at amateur scales. A naive RGB
composite of the three mono stacks is colour-fringed everywhere the
albedo has longitudinal gradient (belt edges, the GRS rim, festoons),
which reads as fake colour and destroys fine structure.

WinJUPOS fixes this with "RGB combine": each mono stack is *derotated* to
a common reference time before compositing. AutoStakkert fundamentally
cannot do it (it stacks one recording at a time). This module does it
with the exact oblate-spheroid ephemeris of `precision_engine`, so it
stays correct when north is not up (`north_pa_deg`), away from opposition
geometry, and at the limbs — the cases where per-row analytic derotation
models break.

METHOD
======
For each capture c with mid-time t_c and reference time t_ref
(dt = t_c - t_ref), every OUTPUT pixel (at the t_ref geometry) is mapped
to the body:

  1. inverse-project the ref pixel to (lon_rel_ref, lat) on the spheroid
     (vectorised copy of precision_engine.px_to_lonlat's quadratic),
  2. the SAME cloud feature sat at System-III-relative longitude
         lon_rel_c = wrap180(lon_rel_ref - omega_cloud(lat) * dt)
     at capture time (omega_cloud = System III rate + zonal-wind residual
     rate from `planet_models`, the same convention as
     `Planet.lon_drift_px`; sign ground-truthed on video_synth: content
     moves -x for CM increasing),
  3. forward-project (lon_rel_c, lat) with the same sub-Earth latitude /
     north-PA ephemeris to capture-image pixels and cubic-resample there
     (image_warp field warp; no FFT phase ramps — see image_warp docstring
     for the measured even-mixture failure of that approach).

A per-latitude-band residual polish (integer FFT peak + Lucas-Kanade, the
`_measure_shift` core that all derotation code in this repo shares, gated
at `max_resid_px`) absorbs any leftover: small ephemeris error, filter-
wheel re-acquisition drift, real wind deviations from the literature
table. Channels are gain-matched to the reference channel on their common
disk so exposure differences between filter sessions do not leak into
false colour.

HONEST SCOPE
============
- Inputs must be pre-aligned stacks (or single frames) on the SAME pixel
  grid: global tip/tilt between filter sessions is measured only as a
  per-band displacement up to `max_resid_px` (see above), not a global
  re-centre. In the production flow the APS stacker hands us aligned
  mono stacks, which is the intended use.
- Limb pixels where a channel's capture has rotated the content out of
  view cannot be invented: they are filled from the reference channel's
  luminance (hue carried by the available channels) and counted in the
  report (`coverage_frac`). This is exactly what WinJUPOS calls
  "incomplete coverage"; we count it instead of hiding it.
- The fringe metric reported is a *photometric edge consistency* number
  (median |channel - reference| on high-gradient pixels, normalised);
  it detects residual misregistration, it is not a colour-calibration
  statement.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

try:  # optional C core (v7.0.0) — identical math, compiled speed
    import cspeed as _cspeed
except Exception:  # pragma: no cover - import guard
    _cspeed = None


# ---------------------------------------------------------------------------
# Vectorised exact inverse projection (mirrors precision_engine.px_to_lonlat)
# ---------------------------------------------------------------------------

def _px_to_lonlat_vec(yy: np.ndarray, xx: np.ndarray, nav) -> Tuple[
        np.ndarray, np.ndarray, np.ndarray]:
    """Vectorised pixel -> (System III lon, planetocentric lat, disk mask).

    Same mathematics as precision_engine.px_to_lonlat (undo isotropic plate
    scale, undo north PA, intersect the line of sight with the oblate
    spheroid, untilt by sub-Earth latitude), evaluated on whole grids.
    Pixels off the disk are clamped onto the limb exactly like the scalar
    version; `disk` tells the caller not to trust them.
    """
    from precision_engine import deg2rad
    s = nav.a_eq_px + 1e-12
    Xsky = (xx - nav.xc) / s
    Ysky = (nav.yc - yy) / s
    pa = deg2rad(float(getattr(nav, "north_pa_deg", 0.0) or 0.0))
    cP, sP = math.cos(pa), math.sin(pa)
    Xp = Xsky * cP + Ysky * sP
    Yp = -Xsky * sP + Ysky * cP
    D = deg2rad(float(getattr(nav, "sub_lat_deg", 0.0) or 0.0))
    cD, sD = math.cos(D), math.sin(D)
    k = max(1.0 - float(nav.flattening), 1e-9)
    inv_k2 = 1.0 / (k * k)
    A = cD * cD + (sD * sD) * inv_k2
    B = 2.0 * Yp * sD * cD * (inv_k2 - 1.0)
    C = Xp * Xp + (Yp * Yp) * (cD * cD * inv_k2 + sD * sD) - 1.0
    disc = B * B - 4.0 * A * C
    disk = disc >= 0.0
    # off-limb: shrink to the limb like the scalar path, then solve
    n = np.hypot(Xp, Yp)
    shrink = np.where(disk, 1.0, np.where(n > 1e-12, 0.999999 / np.maximum(n, 1e-12), 1.0))
    Xp = np.where(disk, Xp, Xp * shrink)
    Yp = np.where(disk, Yp, Yp * shrink)
    C2 = np.where(disk, C, Xp * Xp + (Yp * Yp) * (cD * cD * inv_k2 + sD * sD) - 1.0)
    B2 = np.where(disk, B, 2.0 * Yp * sD * cD * (inv_k2 - 1.0))
    disc2 = np.where(disk, disc, np.maximum(B2 * B2 - 4.0 * A * C2, 0.0))
    t = (-B2 + np.sqrt(disc2)) / (2.0 * A)
    Xb = Xp
    Yb = Yp * cD + t * sD
    Zb = -Yp * sD + t * cD
    lon_rel = np.degrees(np.arctan2(Xb, Zb))
    rad = np.sqrt(Xb * Xb + Yb * Yb + Zb * Zb) + 1e-15
    lat = np.degrees(np.arcsin(np.clip(Yb / rad, -1.0, 1.0)))
    lon = (nav.cm_iii_deg + lon_rel) % 360.0
    return lon, lat, disk


def _wrap180(a: np.ndarray) -> np.ndarray:
    return (np.asarray(a) + 180.0) % 360.0 - 180.0


# ---------------------------------------------------------------------------
# Per-capture rotation field
# ---------------------------------------------------------------------------

def rotation_sample_grid(planet, nav_ref, shape: Tuple[int, int],
                         dt_s: float, include_winds: bool = True) -> Tuple[
        np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Sampling grid + validity for one capture dt_s from the reference time.

    Returns (sy, sx, ok, lat, lon_rel_ref): output pixel (y, x) reads the
    capture image at (sy[y, x], sx[y, x]) where `ok` is True. `lat` /
    `lon_rel_ref` are the body coordinates of each ref pixel (exposed for
    diagnostics, the band polish and tests).

    EXACT geometry: full spheroid inverse+forward projection — north-PA and
    sub-Earth-latitude tilts are handled by construction, wind-adjusted
    cloud rate per parallel when `include_winds`.
    """
    from precision_engine import lonlat_to_planet_xyz, planet_xyz_to_px
    h, w = int(shape[0]), int(shape[1])
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    lon, lat, disk = _px_to_lonlat_vec(yy, xx, nav_ref)
    lon_rel_ref = _wrap180(lon - nav_ref.cm_iii_deg)
    if include_winds:
        # per-parallel cloud-tracking rate (System III + zonal wind), deg/s —
        # vectorised twin of Planet.cloud_tracking_rate_deg_per_s (the polar
        # cos(phi) clamp and endpoint-clamped table interp included; a unit
        # test pins equality with the scalar reference).
        tab = planet.zonal_wind_mps
        u = np.interp(np.abs(lat), tab[:, 0], tab[:, 1])
        cos_la = np.clip(np.cos(np.radians(lat)), 0.05, None)
        rate = (planet.rotation_rate_deg_per_s
                + np.degrees(u * 1.0e-3 / planet.req_km) / cos_la)
    else:
        rate = np.full_like(lat, planet.rotation_rate_deg_per_s)
    lon_rel_c = _wrap180(lon_rel_ref - rate * float(dt_s))
    X, Y, Z = lonlat_to_planet_xyz(lon_rel_c, lat, float(nav_ref.flattening))
    xc, yc, z_los = planet_xyz_to_px(X, Y, Z, nav_ref)
    in_bounds = ((xc >= 0.0) & (xc <= w - 1.0) & (yc >= 0.0) & (yc <= h - 1.0))
    ok = disk & in_bounds & (z_los > 1e-3)
    return yc, xc, ok, lat, lon_rel_ref


# ---------------------------------------------------------------------------
# Band-residual polish
# ---------------------------------------------------------------------------

def _lk_refine_windowed(ref: np.ndarray, img: np.ndarray, w: np.ndarray,
                        iters: int = 5, margin: int = 4,
                        prefilter: float = 1.0) -> Tuple[float, float]:
    """Window-aware Lucas-Kanade apply-shift of `img` onto `ref`.

    Unlike ap_stacker._lk_refine (which multiplies BOTH images by any
    window beforehand), this models

        ref(p) = w(p) * img(p - c)

    so the window enters the GRADIENTS as well (A = w * grad(img)(p-c)).
    Measured why-it-matters: with a distance-tapered band window, plain
    masked LK is dragged toward dy=0 by the un-modelled window taper
    (planted (0.45, -1.25) recovered as (-0.02, +0.9)); the window-aware
    solve recovers the plant to ~0.01 px. Seed is (0,0) — this is a LOCAL
    residual polisher, not a global lock (see _band_polish docstring).
    Divergence guard: any single update > 2.5 px aborts to (0, 0).
    """
    from scipy.ndimage import gaussian_filter, map_coordinates, spline_filter
    if prefilter and prefilter > 0:
        ref = gaussian_filter(ref, prefilter)
        img = gaussian_filter(img, prefilter)
        w = gaussian_filter(w, prefilter)
    h, wd = ref.shape
    margin = int(min(margin, (min(h, wd) - 8) // 2))
    if margin < 3:
        return 0.0, 0.0
    # PERF (bit-exact): prefilter `img` once, replicating map_coordinates'
    # internal recipe (12-px edge pad + spline_filter mode="nearest"), then
    # sample coefficients with coords+12, prefilter=False — identical
    # samples (the internal path does exactly this per call), measured
    # max|delta| 0.0, ~10x less spline filtering in the iteration loop.
    img_c = spline_filter(np.pad(img, 12, mode="edge"), order=3,
                          mode="nearest")
    P = 12
    ys0 = np.arange(margin, h - margin, dtype=np.float64)
    xs0 = np.arange(margin, wd - margin, dtype=np.float64)
    yy, xx = np.meshgrid(ys0, xs0, indexing="ij")
    ref_c = ref[margin:h - margin, margin:wd - margin]
    w_c = w[margin:h - margin, margin:wd - margin]
    cy = cx = 0.0
    use_c = _cspeed is not None and _cspeed.have_c()
    if use_c:
        # PERF (C core, v7.0.0): fused compiled pass — value, gradients,
        # windowed normal-equation sums (parity pinned ~1e-14,
        # tests/test_cspeed.py); identical math to the scipy block below.
        ref_f = np.ascontiguousarray(ref_c.ravel(), dtype=np.float64)
        w_f = np.ascontiguousarray(w_c.ravel(), dtype=np.float64)
        y0f = np.ascontiguousarray((yy + P).ravel(), dtype=np.float64)
        x0f = np.ascontiguousarray((xx + P).ravel(), dtype=np.float64)
    for _ in range(iters):
        if use_c:
            a, b, g2, d1, d2 = _cspeed.lk_sums(img_c, ref_f, w_f,
                                               y0f, x0f, cy, cx)
            if a + g2 < 1e-9:
                break
            AtA = np.array([[a, b], [b, g2]])
            AtB = np.array([d1, d2])
        else:
            ys = yy - cy + P
            xs = xx - cx + P
            warped = map_coordinates(img_c, [ys, xs], order=3, mode="nearest",
                                     prefilter=False) * w_c
            gy = 0.5 * (map_coordinates(img_c, [ys + 1, xs], order=3, mode="nearest", prefilter=False)
                        - map_coordinates(img_c, [ys - 1, xs], order=3, mode="nearest", prefilter=False)) * w_c
            gx = 0.5 * (map_coordinates(img_c, [ys, xs + 1], order=3, mode="nearest", prefilter=False)
                        - map_coordinates(img_c, [ys, xs - 1], order=3, mode="nearest", prefilter=False)) * w_c
            if float((gy * gy + gx * gx).sum()) < 1e-9:
                break
            A = np.stack([gy.ravel(), gx.ravel()], 1)
            diff = ref_c - warped
            AtA = A.T @ A
            AtB = A.T @ diff.ravel()
        lam = 1e-6 * float(np.trace(AtA))
        try:
            sol = np.linalg.solve(AtA + lam * np.eye(2), AtB)
        except np.linalg.LinAlgError:
            break
        if not np.all(np.isfinite(sol)) or max(abs(sol[0]), abs(sol[1])) > 2.5:
            return 0.0, 0.0
        cy -= float(sol[0])
        cx -= float(sol[1])
    return cy, cx


def _band_polish(ref_img: np.ndarray, warped: np.ndarray, lat: np.ndarray,
                 disk: np.ndarray, max_resid_px: float,
                 n_bands: int) -> Tuple[np.ndarray, List[Dict[str, float]]]:
    """Per-latitude-band residual apply-shift of `warped` against `ref_img`.

    Returns ((n_bands, 3) array of [dy, dx, quality], per-band dicts).

    DESIGN (measured, not argued): this runs AFTER the ephemeris prior, so
    by construction the residual is a LOCAL correction of <= ~2 px. We
    deliberately do NOT run a global cross-correlation (FFT peak) search
    here: on quasi-periodic belt texture a global lock can alias to a
    fringe a full wavelength away (measured on adversarial sine-texture
    rigs: every coarse strategy locked ~0.4-1.1 px off a planted sub-pixel
    shift). Instead each band is refined by weighted Lucas-Kanade
    Gauss-Newton seeded at (0, 0) (ap_stacker._lk_refine, ~0.001 px on
    clean shift-only fields) inside a distance-tapered band window, which
    follows the local cost surface near the true residual. A band's shift
    is APPLIED only if |d| <= max_resid_px AND the weighted RMS actually
    improves by >= 2%; otherwise it is zeroed and reported (applied=0) —
    a gated zero is honest, an applied alias is not. One physical honesty
    note: on 1-D parallel-degenerate texture (pure sine bands) the
    cross-parallel component is unobservable in-band (aperture problem);
    the polish then reports the along-parallel improvement only, quality
    tells you how much of the mismatch was absorbed.
    """
    from scipy.ndimage import distance_transform_edt, gaussian_filter
    from image_warp import warp_shift2d
    edges = np.linspace(-62.0, 62.0, int(n_bands) + 1)
    h, w = ref_img.shape
    out = np.zeros((int(n_bands), 3), dtype=np.float64)
    info: List[Dict[str, float]] = []
    for b in range(int(n_bands)):
        m = disk & (lat >= edges[b]) & (lat < edges[b + 1])
        info_d: Dict[str, float] = {"lat_lo": float(edges[b]),
                                    "lat_hi": float(edges[b + 1]),
                                    "n_px": float(m.sum())}
        if m.sum() < 600:                      # too little band to measure
            info_d.update(dy=0.0, dx=0.0, quality=0.0, applied=0.0)
            info.append(info_d)
            continue
        ys, xs = np.where(m)
        pad = 12
        y0, y1 = max(int(ys.min()) - pad, 0), min(int(ys.max()) + pad + 1, h)
        x0, x1 = max(int(xs.min()) - pad, 0), min(int(xs.max()) + pad + 1, w)
        mm = m[y0:y1, x0:x1]
        wgt = gaussian_filter(
            np.clip(distance_transform_edt(mm) / 8.0, 0.0, 1.0), sigma=3.0)
        rc = ref_img[y0:y1, x0:x1] * wgt
        wc = warped[y0:y1, x0:x1]
        rms0 = float(np.sqrt(np.mean((rc - wc * wgt) ** 2)))
        try:
            ay, ax = _lk_refine_windowed(rc, wc, wgt)
        except Exception:
            ay, ax = 0.0, 0.0
        impr = 0.0
        if rms0 > 1e-12 and (abs(ay) > 1e-12 or abs(ax) > 1e-12):
            # quality: window FIXED, image resampled (the same model the
            # window-aware LK solves — shifting w*img would drag the taper)
            wc1 = wgt * warp_shift2d(wc, ay, ax)
            rms1 = float(np.sqrt(np.mean((rc - wc1) ** 2)))
            impr = 1.0 - rms1 / rms0
        applied = 1.0 if (abs(ay) <= max_resid_px and abs(ax) <= max_resid_px
                          and impr >= 0.02) else 0.0
        if applied < 1.0:
            ay = ax = 0.0
        out[b] = (ay, ax, impr)
        info_d.update(dy=float(ay), dx=float(ax), quality=float(impr),
                      applied=applied)
        info.append(info_d)
    return out, info


def _band_field(lat: np.ndarray, bands: np.ndarray,
                n_bands: int) -> Tuple[np.ndarray, np.ndarray]:
    """Expand per-band (dy, dx) to per-pixel via nearest-band lookup + 1-bin
    linear blend across band boundaries (avoids stair-step seams on real
    data)."""
    edges = np.linspace(-62.0, 62.0, int(n_bands) + 1)
    centres = 0.5 * (edges[:-1] + edges[1:])
    la = np.clip(lat, centres[0], centres[-1])
    pos = np.interp(la.ravel(), centres, np.arange(len(centres)))
    pos = pos.reshape(lat.shape)
    i0 = np.floor(pos).astype(int)
    fr = np.clip(pos - i0, 0.0, 1.0)
    i1 = np.clip(i0 + 1, 0, len(centres) - 1)
    dy = bands[i0, 0] * (1.0 - fr) + bands[i1, 0] * fr
    dx = bands[i0, 1] * (1.0 - fr) + bands[i1, 1] * fr
    return dy, dx


# ---------------------------------------------------------------------------
# Photometrics
# ---------------------------------------------------------------------------

def _disk_median(img: np.ndarray, mask: np.ndarray) -> float:
    vals = img[mask]
    if vals.size < 100:
        return 1.0
    return float(np.median(vals)) or 1.0


def _fringe_metric(r: np.ndarray, g: np.ndarray, b: np.ndarray,
                   disk: np.ndarray, cov_ok: Optional[np.ndarray] = None
                   ) -> float:
    """Normalised colour-fringe metric: median(|R-G| + |B-G|) on the
    high-gradient (edge) pixels of G inside the disk, divided by the disk
    RMS of G. 0 = achromatic alignment; grows with misregistration /
    residual rotation. Edge pixels are where registration errors hurt —
    flat zones hide them."""
    m = disk.copy()
    if cov_ok is not None:
        m &= cov_ok
    if m.sum() < 200:
        return float("nan")
    gy, gx = np.gradient(g)
    gmag = np.hypot(gy, gx)
    thr = np.percentile(gmag[m], 80.0)
    edges = m & (gmag >= thr)
    if edges.sum() < 100:
        edges = m
    rms_g = float(np.sqrt(np.mean(g[m] ** 2)) + 1e-12)
    return float(np.median(np.abs(r[edges] - g[edges])
                           + np.abs(b[edges] - g[edges])) / rms_g)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

@dataclass
class RGBCombineConfig:
    include_winds: bool = True      # wind-adjusted per-parallel cloud rate
    band_polish: bool = True        # measured per-band residual refinement
    max_resid_px: float = 3.0       # polish acceptance gate (anti-alias)
    n_bands: int = 9                # latitude bands across |lat|<=62 deg
    gain_match: bool = True         # match R/B levels to G on common disk
    fill_uncovered: bool = True     # fill un-covered disk px from ref luma


@dataclass
class RGBCombineResult:
    rgb: np.ndarray                 # (h, w, 3) float64, reference-time geometry
    report: Dict[str, Any] = field(default_factory=dict)


def combine_rgb(r_img: np.ndarray, g_img: np.ndarray, b_img: np.ndarray,
                t_r_s: float, t_g_s: float, t_b_s: float,
                planet, nav_ref, t_ref_s: Optional[float] = None,
                cfg: Optional[RGBCombineConfig] = None) -> RGBCombineResult:
    """Composite three mono filter captures onto the t_ref geometry.

    r/g/b are float mono images on the same pixel grid (APS mono stacks are
    the intended input). Times are any consistent clock (seconds).
    `planet`   : planet_models Planet (rotation rate + zonal wind table,
                 spheroid).
    `nav_ref`  : precision_engine NavState of the reference epoch
                 (xc, yc, a_eq_px, sub_lat_deg, north_pa_deg; cm irrelevant,
                 only time DIFFERENCES enter — cm cancels analytically).
    `t_ref_s`  : reference epoch; default the green-capture time (G is the
                 standard achromatic anchor in amateur workflow).
    """
    from image_warp import warp_field2d
    cfg = cfg or RGBCombineConfig()
    r_img = np.asarray(r_img, dtype=np.float64)
    g_img = np.asarray(g_img, dtype=np.float64)
    b_img = np.asarray(b_img, dtype=np.float64)
    h, w = g_img.shape
    t_ref = float(t_g_s if t_ref_s is None else t_ref_s)

    caps = {"R": (r_img, float(t_r_s)), "G": (g_img, float(t_g_s)),
            "B": (b_img, float(t_b_s))}
    warped: Dict[str, np.ndarray] = {}
    cov: Dict[str, np.ndarray] = {}
    grids: Dict[str, Tuple[np.ndarray, np.ndarray]] = {}
    lat = lon_rel = None
    band_info: Dict[str, Any] = {}
    for name, (img, t_c) in caps.items():
        sy, sx, ok, lat, lon_rel = rotation_sample_grid(
            planet, nav_ref, (h, w), t_c - t_ref,
            include_winds=cfg.include_winds)
        # displacement convention for warp_field2d: out[y,x] = in[y-dy, x-dx]
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
        dy = yy - sy
        dx = xx - sx
        if cfg.band_polish and abs(t_c - t_ref) > 1e-9:
            pre = warp_field2d(img, dy, dx, order=3)
            bands, binfo = _band_polish(g_img, pre, lat, ok,
                                        cfg.max_resid_px, cfg.n_bands)
            band_info[name] = binfo
            fdy, fdx = _band_field(lat, bands, cfg.n_bands)
            # measured: pre[y,x] = ref[y - fdy, x - fdx]  (pre content is
            # displaced by (fdy,fdx) vs ref), i.e. pre[y+fdy, x+fdx] = ref;
            # the capture must therefore be sampled (fdy, fdx) FURTHER along:
            # out[y,x] = cap[y - dy - fdy, x - dx - fdx].
            dy = dy + fdy
            dx = dx + fdx
        grids[name] = (dy.copy(), dx.copy())
        wp = warp_field2d(img, dy, dx, order=3)
        warped[name] = wp
        cov[name] = ok

    # geometry: reference-time disk from the (clamped) projection
    _, _, disk = _px_to_lonlat_vec(
        *np.mgrid[0:h, 0:w].astype(np.float64), nav_ref)

    fringe_before = _fringe_metric(r_img, g_img, b_img, disk)

    # gain match to G on common covered disk
    common = disk & cov["R"] & cov["G"] & cov["B"]
    gains = {"G": 1.0}
    med_g = _disk_median(warped["G"], common if common.sum() > 500 else disk)
    for name in ("R", "B"):
        if cfg.gain_match and common.sum() > 500:
            med_c = _disk_median(warped[name], common)
            gains[name] = med_g / med_c if abs(med_c) > 1e-12 else 1.0
        else:
            gains[name] = 1.0
        warped[name] = warped[name] * gains[name]

    out = np.stack([warped["R"], warped["G"], warped["B"]], axis=-1)

    # uncovered disk pixels: cannot be invented — carry the reference luma,
    # flagged honestly in coverage stats
    cov_all = cov["R"] & cov["G"] & cov["B"]
    n_uncovered = int((disk & ~cov_all).sum())
    if cfg.fill_uncovered and n_uncovered > 0:
        luma = warped["G"]
        need = disk & ~cov_all
        for ci, name in enumerate("RGB"):
            if name == "G":
                fill = luma
            else:
                fill = luma * gains[name]
            ch = out[..., ci]
            ch[need & ~cov[name]] = fill[need & ~cov[name]]

    fringe_after = _fringe_metric(out[..., 0], out[..., 1], out[..., 2],
                                  disk, cov_ok=cov_all)
    report: Dict[str, Any] = {
        "t_ref_s": t_ref,
        "dts_s": {"R": float(t_r_s) - t_ref, "G": float(t_g_s) - t_ref,
                  "B": float(t_b_s) - t_ref},
        "include_winds": bool(cfg.include_winds),
        "band_polish": bool(cfg.band_polish),
        "bands": band_info,
        "gains_vs_g": {k: float(v) for k, v in gains.items()},
        "coverage_frac": {k: float((cov[k] & disk).sum() / max(disk.sum(), 1))
                          for k in "RGB"},
        "n_disk_px": int(disk.sum()),
        "n_uncovered_disk_px": n_uncovered,
        "fringe_before": fringe_before,
        "fringe_after": fringe_after,
        "fringe_improvement": (float(fringe_before / fringe_after)
                               if (math.isfinite(fringe_before)
                                   and math.isfinite(fringe_after)
                                   and fringe_after > 0) else None),
        "note": ("rotation-derotated RGB combine at t_ref geometry; fringe = "
                 "median(|R-G|+|B-G|)/RMS(G) on edge pixels (lower = better). "
                 "Uncovered limb pixels filled from reference luminance and "
                 "COUNTED, not hidden."),
    }
    return RGBCombineResult(rgb=out.astype(np.float64), report=report)


def combine_report_text(res: RGBCombineResult) -> str:
    r = res.report
    lines = ["=" * 70,
             "RGB COMBINE REPORT — rotation-derotated filter compositing",
             "=" * 70]
    lines.append(f"t_ref: {r['t_ref_s']:.1f} s   dts (R/G/B): "
                 f"{r['dts_s']['R']:+.0f} / {r['dts_s']['G']:+.0f} / "
                 f"{r['dts_s']['B']:+.0f} s")
    lines.append(f"winds in model: {r['include_winds']}   band polish: "
                 f"{r['band_polish']}")
    lines.append(f"fringe before: {r['fringe_before']:.4f}   after: "
                 f"{r['fringe_after']:.4f}   improvement: "
                 f"{(r['fringe_improvement'] or float('nan')):.1f}x")
    for k in "RGB":
        lines.append(f"  {k}: coverage {r['coverage_frac'][k] * 100:.1f}%   "
                     f"gain x{r['gains_vs_g'][k]:.4f}")
    if r.get("n_uncovered_disk_px"):
        lines.append(f"uncovered disk px filled from ref luma: "
                     f"{r['n_uncovered_disk_px']} "
                     f"({r['n_uncovered_disk_px'] / max(r['n_disk_px'], 1) * 100:.2f}%)")
    return "\n".join(lines)


__all__ = [
    "RGBCombineConfig", "RGBCombineResult", "combine_rgb",
    "combine_report_text", "rotation_sample_grid",
]

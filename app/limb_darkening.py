#!/usr/bin/env python3
"""limb_darkening.py — measure the limb-darkening law of the planet from a
finished stack: I(mu) = I0 * mu^k, per latitude band and global.

WHY THIS MODULE EXISTS
======================
Limb darkening is radiative transfer you can see: the emergent intensity
falls toward the limb as the line of sight crosses shallower, cooler
layers. Its exponent k is a real atmospheric diagnostic (and a systematic
for every isophote-based disk fit — fit_limb_nav's isophote radius depends
on k explicitly). No amateur tool measures it. We do, on the stack the
user just produced, with belt/zone texture REMOVED from the estimator
(band-normalised median profiles) so k sees geometry, not albedo.

METHOD
======
For every disk pixel we know mu = cos(emission angle) from the exact
ephemeris (same LOS intersection as the measurement chain). Latitude
bands are normalised at a bright reference ring (mu ~ 0.85) to kill
belt/zone contrast; pooled log-median intensity vs log-mu gives k by
weighted least squares with iterated MAD outlier rejection (bright ovals,
moon shadows and the GRS all get voted out). Per-band fits repeat the
same game for the k(lat) table. Global-fit uncertainty is the
residual-scaled formal sigma.

HONEST SCOPE: k is an effective parameter of THIS image — atmospheric
structure, any sharpening applied before stacking (wavelets boost
high-mu gradients differently), and gamma stretches all move it. Report
it as a consistency/diagnostic number, not a physical constant. On a
renderer with a true mu**0.6 law the estimator recovers 0.6 within
a few hundredths; that is the pinned test.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np


@dataclass
class LimbDarkeningFit:
    k: float
    k_std: float
    i0: float
    n_pixels: int
    n_bands: int
    per_band: List[Dict[str, float]]
    rms_log_resid: float
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {"k": self.k, "k_std": self.k_std, "i0": self.i0,
                "n_pixels": self.n_pixels, "n_bands": self.n_bands,
                "per_band": self.per_band, "rms_log_resid": self.rms_log_resid,
                "note": self.note}


def _mu_map(nav, h: int, w: int) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Per-pixel (mu, lat, disk) from the exact spheroid geometry."""
    from rgb_combine import _px_to_lonlat_vec
    from precision_engine import deg2rad
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    lon, lat, disk = _px_to_lonlat_vec(yy, xx, nav)
    # mu = cos(emission angle) = normal . LOS. At sub-lat D with the
    # axis-aligned case the near-side normal projected on LOS is z_hat of
    # the untilted normal; reuse the quadratic's geometry: reconstruct the
    # body-frame point and its spheroid normal.
    k = max(1.0 - float(nav.flattening), 1e-9)
    lon_rel = np.deg2rad((lon - nav.cm_iii_deg + 180.0) % 360.0 - 180.0)
    lat_r = np.deg2rad(lat)
    r = 1.0 / np.sqrt(np.cos(lat_r) ** 2 + (np.sin(lat_r) / k) ** 2)
    X = r * np.cos(lat_r) * np.sin(lon_rel)
    Y = r * np.sin(lat_r)
    Z = r * np.cos(lat_r) * np.cos(lon_rel)
    nx, ny, nz = X, Y / (k * k), Z
    nl = np.sqrt(nx * nx + ny * ny + nz * nz) + 1e-12
    D = deg2rad(float(getattr(nav, "sub_lat_deg", 0.0) or 0.0))
    cD, sD = math.cos(D), math.sin(D)
    # LOS in the tilted frame is +Zp; mu = n . LOS = (ny*sD? ) — apply the
    # same forward tilt as planet_xyz_to_px: Los component = ny*sinD + nz*cosD
    mu = np.clip((ny * sD + nz * cD) / nl, 0.0, 1.0)
    return mu, lat, disk


def _fit_powerlaw(log_mu: np.ndarray, log_i: np.ndarray,
                  weights: np.ndarray) -> Tuple[float, float, float, float]:
    """Weighted least squares of log I = log I0 + k log mu, MAD-clipped."""
    keep = np.ones(len(log_mu), dtype=bool)
    for _ in range(3):
        A = np.column_stack([np.ones(int(keep.sum())),
                             log_mu[keep]])
        w = np.sqrt(np.clip(weights[keep], 1e-12, None))
        coef, _, _, _ = np.linalg.lstsq(A * w[:, None],
                                        log_i[keep] * w, rcond=None)
        resid = log_i[keep] - A @ coef
        med = float(np.median(resid))
        mad = float(np.median(np.abs(resid - med))) + 1e-9
        out = np.abs(resid - med) > 4.0 * 1.4826 * mad
        if not out.any() or out.sum() > len(resid) - 6:
            break
        keep[np.where(keep)[0][out]] = False
    n = int(keep.sum())
    A = np.column_stack([np.ones(n), log_mu[keep]])
    w2 = weights[keep]
    dof = max(n - 2, 1)
    chi2 = float(np.sum(w2 * (log_i[keep] - A @ coef) ** 2))
    try:
        cov = (chi2 / dof) * np.linalg.inv((A * w[:, None]).T @ (A * w[:, None]))
        k_std = float(math.sqrt(max(cov[1, 1], 0.0)))
    except Exception:
        k_std = float("nan")
    rms = float(math.sqrt(np.mean((log_i[keep] - A @ coef) ** 2)))
    return float(coef[1]), float(math.exp(coef[0])), float(k_std), rms


def measure_limb_darkening(stack: np.ndarray, nav,
                           n_bands: int = 6,
                           band_halfwidth_deg: float = 10.0,
                           mu_ref: float = 0.85) -> LimbDarkeningFit:
    """Measure the mu^k exponent on a finished stack.

    stack: finished (ideally derotated) mono or RGB float image.
    nav:   NavState of the stack (xc, yc, a_eq, sub_lat, north_pa).
    Bands are centred on +/- the band axis values of np.linspace with
    |lat| <= 60 deg, normalised at mu ~ mu_ref before pooling.
    """
    from precision_engine import to_mono
    img = to_mono(np.asarray(stack, dtype=np.float64))
    h, w = img.shape
    mu, lat, disk = _mu_map(nav, h, w)
    ok = disk & (mu > 0.05) & (img > 0)
    if int(ok.sum()) < 5000:
        raise ValueError("measure_limb_darkening: too few disk pixels "
                         f"({int(ok.sum())})")

    centres = np.linspace(-55.0, 55.0, int(n_bands))
    per_band: List[Dict[str, float]] = []
    pooled_lm, pooled_li, pooled_w = [], [], []
    eps = 1e-12
    for c in centres:
        m = ok & (np.abs(lat - c) <= float(band_halfwidth_deg) / 2.0)
        if int(m.sum()) < 800:
            continue
        mu_b = mu[m]
        i_b = img[m]
        ref = (mu_b > mu_ref - 0.03) & (mu_b < mu_ref + 0.03)
        i_ref = float(np.median(i_b[ref])) if ref.sum() > 50 else None
        if not i_ref or i_ref <= 0:
            continue
        i_norm = i_b / i_ref
        # log bins over mu for the fit (robust to texture)
        lm = np.log(np.clip(mu_b, mu_ref * 0.25, 1.0))
        li = np.log(np.clip(i_norm, 1e-4, None))
        wb = np.ones_like(lm)
        # sub-sample bins: median per mu-bin beats per-pixel (texture
        # outliers collect in medians)
        edges = np.linspace(lm.min(), 0.0, 26)
        bl, bi, bw = [], [], []
        for e in range(len(edges) - 1):
            mm = (lm >= edges[e]) & (lm < edges[e + 1])
            if mm.sum() < 20:
                continue
            bl.append(float(np.median(lm[mm])))
            bi.append(float(np.median(li[mm])))
            bw.append(float(mm.sum()))
        if len(bl) < 8:
            continue
        kb, i0b, ksb, rmsb = _fit_powerlaw(np.array(bl), np.array(bi),
                                           np.array(bw))
        per_band.append({"lat_deg": float(c), "k": kb, "k_std": ksb,
                         "rms": rmsb, "n_bins": float(len(bl))})
        pooled_lm.extend(bl)
        pooled_li.extend(bi)
        pooled_w.extend(bw)

    if len(pooled_lm) < 10:
        raise ValueError("measure_limb_darkening: not enough band evidence")
    k, i0, k_std, rms = _fit_powerlaw(np.array(pooled_lm),
                                      np.array(pooled_li),
                                      np.array(pooled_w))
    low_mu = sum(1 for b in per_band)
    return LimbDarkeningFit(
        k=k, k_std=k_std, i0=i0, n_pixels=int(ok.sum()),
        n_bands=low_mu, per_band=per_band, rms_log_resid=rms,
        note=("effective mu^k of THIS image (geometry + atmospheric LD + "
              "any sharpening/stretch upstream); band-normalised, MAD-clipped. "
              "Not an absolute physical constant."))


def render_ld_png(fit: LimbDarkeningFit, out_path,
                  width: int = 700, height: int = 560) -> str:
    """Panel: measured log-profile per band (grey) + global fit (line)."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (width, height), (16, 18, 24))
    d = ImageDraw.Draw(img)
    d.text((ml := 60, 20),
           f"Limb darkening: k = {fit.k:.3f} +- {fit.k_std:.3f}  "
           f"({fit.n_bands} bands, {fit.n_pixels} px)", fill=(230, 230, 235))
    mt, mb, mr = 60, 60, 40
    pw, ph = width - ml - mr, height - mt - mb
    # plot per-band k values with error bars vs latitude
    kmin = min(b["k"] - 2 * b["k_std"] for b in fit.per_band) if fit.per_band \
        else fit.k - 0.2
    kmax = max(b["k"] + 2 * b["k_std"] for b in fit.per_band) if fit.per_band \
        else fit.k + 0.2
    kmin, kmax = min(kmin, fit.k - 0.05), max(kmax, fit.k + 0.05)
    span = max(kmax - kmin, 1e-6)

    def X(la):
        return ml + (la + 60.0) / 120.0 * pw

    def Y(kv):
        return mt + (kmax - kv) / span * ph

    d.rectangle([ml, mt, ml + pw, mt + ph], outline=(120, 125, 140))
    d.line([(ml, Y(fit.k)), (ml + pw, Y(fit.k))], fill=(110, 120, 150))
    d.text((ml + pw + 4, Y(fit.k) - 6), f"k={fit.k:.3f}", fill=(160, 165, 180))
    for la in range(-60, 61, 30):
        d.line([(X(la), mt), (X(la), mt + ph)], fill=(36, 40, 52))
        d.text((X(la) - 10, mt + ph + 8), f"{la}", fill=(160, 165, 180))
    for b in fit.per_band:
        x = X(b["lat_deg"])
        d.line([(x, Y(b["k"] - b["k_std"])), (x, Y(b["k"] + b["k_std"]))],
               fill=(120, 170, 235), width=2)
        d.ellipse([x - 4, Y(b["k"]) - 4, x + 4, Y(b["k"]) + 4],
                  fill=(150, 200, 255))
    d.text((ml - 44, mt + ph // 2 - 6), "k(mu)", fill=(200, 205, 215))
    d.text((ml + pw // 2 - 20, mt + ph + 28), "latitude (deg)",
           fill=(200, 205, 215))
    os.makedirs(os.path.dirname(os.path.abspath(str(out_path))), exist_ok=True)
    img.save(str(out_path))
    return str(out_path)


__all__ = ["LimbDarkeningFit", "measure_limb_darkening", "render_ld_png"]

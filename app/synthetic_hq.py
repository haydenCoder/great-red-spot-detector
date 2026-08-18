#!/usr/bin/env python3
"""
Photoreal-oriented synthetic Jupiter + GRS (v3)
===============================================

Renders a full-disk RGB frame that aims to look like a *good amateur
planetary stack* (not a cartoon map):

  • Soft multi-belt albedo (NEB/EZ/SEB/STB …) with natural edges
  • Zonal-streak residual (east–west shear) instead of blotchy noise
  • Subtle waves / chevrons / festoons
  • Soft white ovals & barges (Gaussian falloff)
  • Soft salmon/ochre GRS — no hard black collar, muted filaments
  • Limb darkening + mild blue limb haze
  • Realistic seeing PSF (mild chromatic), gentle unsharp, photon-ish noise
  • Optional GRS close-up crop preview PNG

Truth JSON still drives recovery scoring (lon/lat/CM/disk) via the
intensity-weighted dark barycentre after optics.

Not Hubble/JWST photorealism — procedural; still valid for metrology self-test.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import numpy as np

from verbose_log import CONSOLE
from ram_ssd import choose_max_resolution, free_memory, estimate_rgb_gb
from precision_engine import FLAT, GRS_LAT0, JUP_REQ_KM, wrap_deg

try:
    from PIL import Image
    _HAS_PIL = True
    # 16K RGB is large; allow big synthetics
    try:
        Image.MAX_IMAGE_PIXELS = int(os.environ.get("GRS_MAX_IMAGE_PIXELS", 250_000_000))
    except Exception:
        pass
except ImportError:
    _HAS_PIL = False


@dataclass
class SynthSpec:
    """Synthetic Jupiter frame.

    Observation epoch is random unless random_time=False and user_time_iso is set.

    mode:
      - visual: high wave contrast (presentation / UI stills)
      - metrology: quieter SEB, GRS uniquely dark, for certification / accuracy demos
    """
    user_time_iso: str = ""
    region: str = "global"
    time_error_seconds: float = 0.0
    resolution_preset: str = "auto"  # auto | 1080p | 4K | 8K | 16K
    seed: Optional[int] = None
    random_time: bool = True
    mode: str = "visual"  # visual | metrology
    # photoreal quality knobs (defaults → realistic amateur stack look)
    wave_contrast: float = 1.0       # >1 more belt undulation
    seeing_fwhm_arcsec: float = 0.35  # overridden by mode presets (~0.55–0.65")
    noise_rms: float = 0.004
    write_grs_crop: bool = True
    # True sky orientation of the rendered disk: sub-observer planetocentric
    # latitude D and north-polar-axis position angle P (deg, E of N). Sets the
    # ACTUAL projection geometry since v6.8 (previously only logged); the
    # inverse is precision_engine.px_to_lonlat with the same D/P. Realistic
    # Jupiter ranges: |D| <= ~3.4 deg, P in ~343..17 deg over a Jovian year.
    sub_lat_deg: float = 0.0
    north_pa_deg: float = 0.0
    grs_limb_rel_deg: Optional[float] = None  # relative to CM for GRS placement e.g. 35..95
    distance_au: Optional[float] = None


REGION = {
    "global": (1.0, 0.0, 0.0),
    "grs_closeup": (2.35, 0.06, 0.18),
    "se_belt": (1.45, 0.0, 0.14),
    "equatorial": (1.55, 0.0, 0.0),
    "full_disk": (0.90, 0.0, 0.0),
}


def _seed(user_time: str, region: str, err: float) -> int:
    raw = f"{user_time}|{region}|{err}|{os.urandom(16).hex()}|{dt.datetime.now(dt.timezone.utc).isoformat()}|{os.getpid()}"
    return int(hashlib.sha256(raw.encode()).hexdigest()[:16], 16) % (2**31 - 1)


def _parse_time(s: str) -> dt.datetime:
    s = s.strip().replace("T", " ").replace("Z", "")
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError(f"Bad time: {s}")


# Fixed sampling window for synthetic observation epochs.
#
# This MUST NOT depend on the wall clock. It previously ended at
# datetime.now() + 800 days, which made the span grow by one second per elapsed
# second, so rng.integers(0, span) returned a different epoch for the SAME SEED
# on every run — different CM III, different GRS truth longitude, a completely
# different frame. That silently made every seeded certification run
# irreproducible. Both bounds are now constants.
SYNTH_EPOCH_START = dt.datetime(2010, 1, 1, 0, 0, 0)
SYNTH_EPOCH_END = dt.datetime(2030, 1, 1, 0, 0, 0)


def random_observation_time(rng: Optional[np.random.Generator] = None) -> dt.datetime:
    """Draw a synthetic observation epoch from a FIXED window.

    Deterministic for a given seed: identical input rng state always yields the
    identical epoch, no matter when the function is called.
    """
    rng = rng or np.random.default_rng()
    t0 = SYNTH_EPOCH_START
    span = max(int((SYNTH_EPOCH_END - t0).total_seconds()), 86400)
    sec = int(rng.integers(0, span))
    return (t0 + dt.timedelta(seconds=sec)).replace(microsecond=0)


def _blur(rgb: np.ndarray, sigma: float) -> np.ndarray:
    if sigma < 0.15:
        return rgb
    try:
        from scipy.ndimage import gaussian_filter
        if rgb.ndim == 3:
            out = np.empty_like(rgb)
            for c in range(rgb.shape[2]):
                out[:, :, c] = gaussian_filter(rgb[:, :, c], sigma=sigma, mode="nearest")
            return out
        return gaussian_filter(rgb, sigma=sigma, mode="nearest")
    except Exception:
        return rgb


def _resize_bilinear(small: np.ndarray, h: int, w: int) -> np.ndarray:
    """Smooth upsample (avoids blocky tiles that killed wave look at 8K)."""
    sh, sw = small.shape
    if sh == h and sw == w:
        return small.astype(np.float32)
    # map output coords into small grid
    ys = (np.arange(h, dtype=np.float64) + 0.5) * sh / h - 0.5
    xs = (np.arange(w, dtype=np.float64) + 0.5) * sw / w - 0.5
    y0 = np.floor(ys).astype(np.int64)
    x0 = np.floor(xs).astype(np.int64)
    y1 = np.clip(y0 + 1, 0, sh - 1)
    x1 = np.clip(x0 + 1, 0, sw - 1)
    y0 = np.clip(y0, 0, sh - 1)
    x0 = np.clip(x0, 0, sw - 1)
    wy = (ys - y0).astype(np.float32)
    wx = (xs - x0).astype(np.float32)
    # outer products via broadcasting
    Ia = small[y0][:, x0]
    Ib = small[y0][:, x1]
    Ic = small[y1][:, x0]
    Id = small[y1][:, x1]
    top = Ia * (1 - wx)[None, :] + Ib * wx[None, :]
    bot = Ic * (1 - wx)[None, :] + Id * wx[None, :]
    return (top * (1 - wy)[:, None] + bot * wy[:, None]).astype(np.float32)


def _value_noise(
    h: int, w: int, rng: np.random.Generator, octaves: int = 5, base: int = 48
) -> np.ndarray:
    """Multi-octave smooth noise in [-1,1]."""
    acc = np.zeros((h, w), dtype=np.float32)
    amp = 1.0
    norm = 0.0
    for o in range(octaves):
        sc = max(3, int(base // (2 ** o)))
        gh = max(3, int(math.ceil(h / sc)))
        gw = max(3, int(math.ceil(w / sc)))
        grid = rng.normal(0, 1, (gh, gw)).astype(np.float32)
        up = _resize_bilinear(grid, h, w)
        acc += np.float32(amp) * up
        norm += amp
        amp *= 0.55
    acc /= max(norm, 1e-6)
    # zero-mean unit-ish
    acc -= float(acc.mean())
    s = float(acc.std()) + 1e-6
    return (acc / s).astype(np.float32)


def _belt_profile(lat_n: np.ndarray) -> np.ndarray:
    """
    Soft canonical belt/zone stack (lat in radians).
    Real Jupiter belts have gradual edges, not painted stripes.
    Zones → higher albedo, belts → lower.
    """
    def edge(x0: float, k: float) -> np.ndarray:
        # gentler k → photoreal soft transitions
        return np.tanh(k * (lat_n - x0))

    p = (
        0.54
        + 0.08 * edge(-0.70, 8.0)          # SPR
        - 0.16 * edge(-0.52, 10.0)         # SSTB
        + 0.12 * edge(-0.40, 11.0)         # STZ
        - 0.20 * edge(-0.28, 12.0)         # STB
        + 0.15 * edge(-0.18, 13.0)         # STrZ
        - 0.28 * edge(-0.09, 14.0)         # SEB
        + 0.26 * edge(+0.02, 15.0)         # EZ
        - 0.26 * edge(+0.14, 13.0)         # NEB
        + 0.13 * edge(+0.28, 10.0)         # NTrZ
        - 0.16 * edge(+0.40, 9.0)          # NTB
        + 0.09 * edge(+0.55, 7.0)          # NTZ
        - 0.08 * edge(+0.68, 6.0)          # NPR
    )
    return np.clip(p, 0.16, 0.93).astype(np.float32)


def _wavefield(
    lon_rel: np.ndarray,
    lat_n: np.ndarray,
    rng: np.random.Generator,
    contrast: float,
) -> np.ndarray:
    """Subtle longitudinal waves / chevrons — real stacks show soft undulation."""
    ph = rng.uniform(0, 2 * math.pi, size=8).astype(np.float64)
    waves = (
        0.10 * np.sin(5.0 * lon_rel + 1.8 * lat_n + ph[0])
        + 0.07 * np.sin(9.0 * lon_rel - 2.8 * lat_n + ph[1])
        + 0.05 * np.sin(14.0 * lon_rel + 1.2 * lat_n + ph[2])
        + 0.04 * np.sin(22.0 * lon_rel - 3.2 * lat_n + ph[3])
        + 0.03 * np.sin(30.0 * lon_rel + 0.6 * lat_n + ph[4])
    )
    chev = (
        0.06
        * np.sin(12.0 * lon_rel + 6.5 * lat_n + ph[6])
        * np.exp(-((lat_n + 0.11) / 0.09) ** 2)
    )
    chev += (
        0.05
        * np.sin(15.0 * lon_rel - 7.0 * lat_n + ph[7])
        * np.exp(-((lat_n - 0.15) / 0.09) ** 2)
    )
    fest = (
        0.07
        * np.sin(18.0 * lon_rel + 0.5)
        * np.exp(-((lat_n - 0.01) / 0.07) ** 2)
        * (0.6 + 0.4 * np.sin(2.5 * lon_rel + 1.0))
    )
    hot = 0.035 * np.sin(7.0 * lon_rel + 1.5) * np.exp(-((lat_n - 0.05) / 0.12) ** 2)
    field = (waves + chev + fest + hot) * float(contrast)
    return field.astype(np.float32)


def _zonal_streak_noise(
    h: int,
    w: int,
    lat_n: np.ndarray,
    lon_rel: np.ndarray,
    rng: np.random.Generator,
) -> np.ndarray:
    """
    Atmosphere residual stretched along zonal (east–west) flow —
    the main 'real stack' look vs isotropic blotches.
    """
    base = _value_noise(h, w, rng, octaves=5, base=max(36, w // 48))
    # Physical-ish horizontal shear: shift rows by latitude-dependent amount
    # (cheap stand-in for zonal jet advection — big realism win)
    try:
        lat_row = lat_n[:, w // 2] if lat_n.shape[1] == w else lat_n.mean(axis=1)
        shifts = np.rint(14.0 * np.sin(3.5 * lat_row) + 7.0 * lat_row).astype(np.int64)
        sheared = np.empty_like(base)
        for i in range(h):
            sheared[i] = np.roll(base[i], int(shifts[i]))
        base = 0.30 * base + 0.70 * sheared
    except Exception:
        pass
    shear = 3.2 * lat_n + 0.9 * np.sin(2.5 * lat_n)
    streak = (
        0.50 * base
        + 0.32 * base * np.cos(3.5 * lon_rel + 7.0 * shear)
        + 0.18 * base * np.sin(8.0 * lon_rel - 4.5 * shear)
    )
    pole = np.clip((np.abs(np.degrees(lat_n)) - 45.0) / 30.0, 0.0, 1.0)
    streak = streak * (1.0 - 0.50 * pole)
    streak -= float(streak.mean())
    s = float(streak.std()) + 1e-6
    return (streak / s).astype(np.float32)


def _shear_residual(
    turb: np.ndarray, lat_n: np.ndarray, lon_rel: np.ndarray, strength: float = 0.35
) -> np.ndarray:
    """Approximate zonal shear by phase-modulating residual with latitude."""
    phase = strength * lat_n
    return (
        turb * np.cos(phase)
        + 0.45 * turb * np.sin(5.0 * lon_rel + 5.0 * phase)
        + 0.20 * turb * np.sin(11.0 * lon_rel - 3.0 * phase)
    ).astype(np.float32)


def _paint_ovals(
    rgb: np.ndarray,
    disk: np.ndarray,
    lon_abs: np.ndarray,
    lat_deg: np.ndarray,
    ld: np.ndarray,
    rng: np.random.Generator,
    n: int,
    grs_lon: float = 0.0,
    grs_lat: float = -22.0,
) -> None:
    """Soft white ovals / barges with Gaussian falloff (no hard disks)."""
    for _ in range(n):
        olon = float(rng.uniform(0, 360))
        olat = float(rng.choice([-34, -31, -29, 16, 22, 27, 33]) + rng.normal(0, 1.0))
        oa = float(rng.uniform(1.2, 3.5))
        ob = float(rng.uniform(0.6, 1.6))
        bright = bool(rng.random() > 0.28)
        dlo_g = ((olon - grs_lon + 180.0) % 360.0) - 180.0
        if (not bright) and abs(dlo_g) < 45.0 and abs(olat - grs_lat) < 9.0:
            bright = True
        dlo = ((lon_abs - olon + 180.0) % 360.0) - 180.0
        rr = (dlo / (oa + 1e-6)) ** 2 + ((lat_deg - olat) / (ob + 1e-6)) ** 2
        # soft falloff — no hard ellipse edge
        alpha = np.exp(-rr * 1.35).astype(np.float32)
        alpha = np.where(disk, alpha, 0.0)
        if float(alpha.max()) < 0.05:
            continue
        if bright:
            col = np.array([0.93, 0.90, 0.84], dtype=np.float32)
            a0 = 0.28
        else:
            col = np.array([0.42, 0.30, 0.20], dtype=np.float32)
            a0 = 0.22
        for c in range(3):
            a = a0 * alpha
            rgb[:, :, c] = rgb[:, :, c] * (1.0 - a) + col[c] * ld * a


def _paint_grs(
    rgb: np.ndarray,
    disk: np.ndarray,
    lon_abs: np.ndarray,
    lat_deg: np.ndarray,
    ld: np.ndarray,
    grs_lon: float,
    grs_lat: float,
    grs_L: float,
    grs_W: float,
    rng: np.random.Generator,
    *,
    metrology: bool = False,
) -> Tuple[np.ndarray, float, float]:
    """
    Photoreal-ish GRS: soft salmon/ochre oval, muted filaments, no hard collar.

    metrology=True: slightly darker core so recovery stays honest without a
    cartoon black rim.

    Returns (oval_mask, truth_lon, truth_lat) — intensity-weighted dark barycentre
    (same definition the metrology engine uses).
    """
    dlon = ((lon_abs - grs_lon + 180.0) % 360.0) - 180.0
    dlon_n = dlon / (grs_L * 0.5 + 1e-6)
    dlat_n = (lat_deg - grs_lat) / (grs_W * 0.5 + 1e-6)
    rad2 = dlon_n ** 2 + dlat_n ** 2
    r = np.sqrt(np.clip(rad2, 0, 8)).astype(np.float32)
    # soft mask extending slightly past geometric ellipse
    oval = (rad2 <= 1.15) & disk
    # smooth alpha: no hard rim
    alpha = np.exp(-((r / 0.92) ** 2.15)).astype(np.float32)
    alpha = np.where(disk, alpha, 0.0)
    alpha = np.clip(alpha, 0.0, 1.0)

    ang = np.arctan2(dlat_n, dlon_n)
    ph0 = float(rng.uniform(0, 2 * math.pi))
    # gentle filaments (not cartoon X)
    swirl = (
        0.55
        + 0.12 * np.sin(2.2 * ang + 3.5 * r + ph0)
        + 0.08 * np.sin(3.5 * ang - 2.0 * r + 0.7)
        + 0.05 * np.sin(5.5 * ang + 1.2 * r)
    )
    swirl = np.clip(swirl, 0.0, 1.0).astype(np.float32)
    core = np.exp(-((r / 0.55) ** 2)).astype(np.float32)

    # Real GRS is ochre / salmon; metrology deepens core slightly for lock
    if metrology:
        grs_dark = np.array([0.42, 0.16, 0.09], dtype=np.float32)
        grs_mid = np.array([0.64, 0.30, 0.16], dtype=np.float32)
        grs_hi = np.array([0.78, 0.46, 0.26], dtype=np.float32)
        mix_w = 0.88
        hollow = 0.06 * np.exp(-((r / 0.28) ** 2)).astype(np.float32)
    else:
        grs_dark = np.array([0.52, 0.22, 0.12], dtype=np.float32)
        grs_mid = np.array([0.72, 0.38, 0.20], dtype=np.float32)
        grs_hi = np.array([0.82, 0.52, 0.30], dtype=np.float32)
        mix_w = 0.80
        hollow = 0.10 * np.exp(-((r / 0.28) ** 2)).astype(np.float32)
    for c in range(3):
        col = grs_dark[c] * (1.0 - swirl) + grs_mid[c] * swirl
        col = col * (1.0 - 0.12 * core) + grs_hi[c] * (0.10 * core + hollow)
        a = mix_w * alpha
        rgb[:, :, c] = rgb[:, :, c] * (1.0 - a) + col * a * (0.90 + 0.10 * ld)

    # very soft SEB wake / following turbulence (no hard edge)
    wake = np.exp(-(((dlon_n - 0.85) / 0.55) ** 2) - ((dlat_n / 0.75) ** 2)).astype(np.float32)
    wake = np.where(disk, wake * 0.12, 0.0)
    for c in range(3):
        rgb[:, :, c] = rgb[:, :, c] * (1.0 - wake) + rgb[:, :, c] * 0.92 * wake + grs_mid[c] * 0.08 * wake

    mono = 0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]
    if np.any(oval):
        dark = float(np.percentile(mono[oval], 90)) - mono
        dark = np.where(oval, np.clip(dark, 0, None), 0.0)
        red_dark = float(np.percentile(rgb[:, :, 0][oval], 90)) - rgb[:, :, 0]
        wts = np.where(oval, 0.55 * dark + 0.45 * np.clip(red_dark, 0, None), 0.0)
        s = float(wts.sum())
        if s > 1e-8:
            lon_r = np.deg2rad(lon_abs)
            cx = float((np.cos(lon_r) * wts).sum() / s)
            sx = float((np.sin(lon_r) * wts).sum() / s)
            truth_lon = float(np.rad2deg(np.arctan2(sx, cx)) % 360.0)
            truth_lat = float((lat_deg * wts).sum() / s)
        else:
            truth_lon, truth_lat = float(grs_lon), float(grs_lat)
    else:
        truth_lon, truth_lat = float(grs_lon), float(grs_lat)
    return oval, truth_lon, truth_lat


def _realistic_atmosphere_rgb(
    belt: np.ndarray,
    waves: np.ndarray,
    turb: np.ndarray,
    lat_deg: np.ndarray,
    mu: np.ndarray,
    disk: np.ndarray,
    dtype=np.float32,
) -> np.ndarray:
    """
    Build RGB looking closer to stacked amateur LRGB / RGB planetary frames:
    soft cream zones, brown belts, cool poles, mild limb darkening + blue haze.
    """
    mix = belt + 0.28 * waves + 0.14 * turb
    mix = np.clip(mix, 0.12, 0.95).astype(dtype)

    # Palettes tuned to typical processed Jupiter RGB (not neon)
    cream = np.array([0.93, 0.88, 0.76], dtype=dtype)
    belt_c = np.array([0.55, 0.38, 0.24], dtype=dtype)
    blue_haze = np.array([0.42, 0.50, 0.62], dtype=dtype)
    orange = np.array([0.78, 0.48, 0.26], dtype=dtype)

    zone_w = np.clip(mix, 0, 1)
    polar = np.clip((np.abs(lat_deg) - 42) / 28.0, 0, 1).astype(dtype)
    neb = np.exp(-((lat_deg - 13) / 9.0) ** 2).astype(dtype)
    seb = np.exp(-((lat_deg + 12) / 9.5) ** 2).astype(dtype)

    # Softer limb darkening (real stacks often flatten mid-disk)
    ld = (0.55 + 0.45 * (mu ** 0.65)).astype(dtype)
    # blue Rayleigh-ish haze near limb
    limb = np.clip(1.0 - mu, 0, 1).astype(dtype)
    limb_w = (limb ** 1.35).astype(dtype)

    h, w = belt.shape
    rgb = np.zeros((h, w, 3), dtype=dtype)
    for c in range(3):
        base_c = cream[c] * zone_w + belt_c[c] * (1.0 - zone_w)
        warm = 0.14 * neb + 0.12 * seb
        base_c = base_c * (1.0 - warm) + orange[c] * warm * (0.30 + 0.55 * (1.0 - zone_w))
        base_c = base_c * (1.0 - 0.32 * polar) + blue_haze[c] * 0.32 * polar
        # tiny residual chroma
        base_c = base_c * (1.0 + 0.035 * turb)
        base_c = base_c * ld
        # limb cool haze
        base_c = base_c * (1.0 - 0.22 * limb_w) + blue_haze[c] * 0.18 * limb_w
        rgb[:, :, c] = np.clip(base_c, 0, 1)
    rgb[~disk] = 0.0
    return rgb


def _apply_realistic_optics(
    rgb: np.ndarray,
    disk: np.ndarray,
    a_eq_px: float,
    app_diam_as: float,
    rng: np.random.Generator,
    seeing_fwhm_arcsec: float,
    noise_rms: float,
    res_name: str,
) -> np.ndarray:
    """Seeing PSF, mild chromatic, photon-ish noise, gentle unsharp (like real stacks)."""
    dtype = rgb.dtype
    px_per_as = (2 * a_eq_px) / (app_diam_as + 1e-6)
    fwhm_as = float(seeing_fwhm_arcsec)
    sig = max(0.35, (fwhm_as / 2.355) * px_per_as)
    # Cap blur RELATIVE TO THE DISK, not as an absolute pixel count.
    #
    # The old code did `sig = min(sig, 2.2)` in absolute pixels. At 1080p a
    # 0.38" request already works out to sigma=3.66 px, so the cap bound at
    # EVERY seeing value: 0.38" and 6.0" rendered byte-identically and the
    # seeing knob did nothing. Every "robust to blur" result measured with it
    # was therefore vacuous -- the synthetic had never actually been blurry.
    #
    # A physical seeing limit scales with the disk: a 3" blur on a 40" disk is
    # a fixed FRACTION of the radius no matter the sampling. Cap at 12% of the
    # equatorial radius, which still leaves belts discernible at the worst
    # realistic amateur seeing while allowing genuine mush to be simulated.
    sig_cap = max(2.2, 0.12 * float(a_eq_px))
    sig = min(sig, sig_cap)

    # Mild chromatic PSF (R slightly wider — common in RGB)
    out = np.empty_like(rgb)
    out[:, :, 0] = _blur(rgb[:, :, 0], float(sig * 1.06))
    out[:, :, 1] = _blur(rgb[:, :, 1], float(sig))
    out[:, :, 2] = _blur(rgb[:, :, 2], float(sig * 0.96))

    # soft atmospheric scatter halo (very mild)
    halo = _blur(out, float(sig * 1.8))
    out = np.clip(0.96 * out + 0.04 * halo, 0, 1)

    noise = rng.normal(0, float(noise_rms), out.shape).astype(dtype)
    mono = 0.299 * out[:, :, 0] + 0.587 * out[:, :, 1] + 0.114 * out[:, :, 2]
    nscale = (0.70 + 0.45 * (1.0 - np.clip(mono, 0, 1)))[..., None]
    out = np.clip(out + noise * nscale, 0, 1)

    # moderate unsharp like a carefully processed planetary stack
    try:
        mono2 = 0.299 * out[:, :, 0] + 0.587 * out[:, :, 1] + 0.114 * out[:, :, 2]
        blur_m = _blur(mono2, max(1.0, a_eq_px * 0.014))
        detail = (mono2 - blur_m) * 0.32
        for c in range(3):
            ch = out[:, :, c] + detail
            out[:, :, c] = np.where(disk, np.clip(ch, 0, 1), 0.0)
    except Exception:
        pass

    out[~disk] = 0.0
    return out.astype(dtype)


def generate(spec: SynthSpec, out_dir: Path) -> Tuple[Path, Path, Dict[str, Any]]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if spec.resolution_preset in ("auto", "", None):
        name, w, h = choose_max_resolution("8K")
    else:
        name = spec.resolution_preset
        presets = {
            "480p": (854, 480),
            "540p": (960, 540),
            "720p": (1280, 720),
            "1080p": (1920, 1080),
            "4K": (3840, 2160),
            "8K": (7680, 4320),
            "16K": (15360, 8640),
        }
        if name not in presets:
            name, w, h = choose_max_resolution("8K")
        else:
            w, h = presets[name]
            if estimate_rgb_gb(w, h) * 3.5 > 9.5:
                CONSOLE.warn(f"{name} too large for 16GB safe budget — stepping down")
                name, w, h = choose_max_resolution("8K")

    if spec.seed is not None:
        seed = int(spec.seed)
    else:
        env_seed = os.environ.get("GRS_SYNTH_SEED", "").strip()
        if env_seed:
            try:
                seed = int(env_seed)
            except ValueError:
                seed = _seed(
                    spec.user_time_iso or "random", spec.region, spec.time_error_seconds
                )
        else:
            seed = _seed(
                spec.user_time_iso or "random", spec.region, spec.time_error_seconds
            )
    rng = np.random.default_rng(seed)

    time_was_random = bool(spec.random_time) or not (spec.user_time_iso or "").strip()
    if time_was_random:
        t = random_observation_time(rng)
        t = t + dt.timedelta(seconds=float(rng.uniform(-2.0, 2.0)))
        time_error_used = float(spec.time_error_seconds)
        CONSOLE.info(f"Synthetic uses RANDOM epoch: {t.isoformat(sep=' ')}")
    else:
        t = _parse_time(spec.user_time_iso) + dt.timedelta(seconds=float(spec.time_error_seconds))
        time_error_used = float(spec.time_error_seconds)
        CONSOLE.info(f"Synthetic uses FIXED epoch: {t.isoformat(sep=' ')}")

    user_time_iso_out = t.strftime("%Y-%m-%d %H:%M:%S")
    # Mode presets: both aim for photoreal; metrology keeps GRS recoverable
    mode = (spec.mode or "visual").strip().lower()
    if mode == "metrology":
        if spec.wave_contrast == 1.0:
            spec.wave_contrast = 0.80  # quieter SEB so GRS stays unique
        if spec.seeing_fwhm_arcsec == 0.35:
            spec.seeing_fwhm_arcsec = 0.38
        if spec.noise_rms == 0.004:
            spec.noise_rms = 0.0045
    else:
        if spec.seeing_fwhm_arcsec == 0.35:
            spec.seeing_fwhm_arcsec = 0.50
        if spec.noise_rms == 0.004:
            spec.noise_rms = 0.006
    CONSOLE.info(
        f"Synthetic HQ-v3 photoreal {name} {w}x{h} seed={seed}  mode={mode}  "
        f"wave×{spec.wave_contrast}  seeing={spec.seeing_fwhm_arcsec:.2f}\""
    )

    # Prefer SPICE distance when available (absolute geometry from kernels).
    # Skip if GRS_SKIP_SPICE_SYNTH=1 for fast batch loops (kernels still used in eph).
    # Draw RNG fallback *before* SPICE so seed sequences stay deterministic whether
    # or not SPICE succeeds (avoids divergent GRS placement with the same seed).
    year_frac = t.year + t.timetuple().tm_yday / 365.25
    dist_fallback = 5.2 + 0.55 * math.cos(2 * math.pi * (year_frac - 2000) / 1.09)
    dist_fallback = float(np.clip(dist_fallback + rng.normal(0, 0.02), 3.8, 6.6))

    dist = None
    spice_meta: Dict[str, Any] = {}
    if os.environ.get("GRS_SKIP_SPICE_SYNTH", "").strip() not in ("1", "true", "yes"):
        try:
            from spice_auto import compute_spice_geometry
            sg = compute_spice_geometry(t, auto_download=False)
            if sg is not None and sg.distance_au > 3.0:
                dist = float(sg.distance_au)
                spice_meta = {
                    "spice_source": sg.source,
                    "spice_cm_iii_deg": sg.cm_iii_deg,
                    "spice_sub_lat_deg": sg.sub_obs_lat_deg,
                    "spice_diam_arcsec": sg.apparent_diameter_arcsec,
                }
                CONSOLE.info(f"Synthetic distance from SPICE: {dist:.5f} AU")
        except Exception as e:
            CONSOLE.debug(f"SPICE dist for synth: {e}")

    if dist is None:
        dist = dist_fallback

    # EXTREME GEOMETRY OVERRIDES ("every atom" training: sub_lat ±18°, north_pa ±75°, limb 35-95°)
    if getattr(spec, "distance_au", None) is not None:
        dist = float(spec.distance_au)
        CONSOLE.info(f"[extreme-geo] distance_au -> {dist:.4f}")
    injected_sub_lat = float(getattr(spec, "sub_lat_deg", 0.0) or 0.0)
    injected_north_pa = float(getattr(spec, "north_pa_deg", 0.0) or 0.0)
    if injected_sub_lat or injected_north_pa:
        spice_meta["injected_sub_lat_deg"] = injected_sub_lat
        spice_meta["injected_north_pa_deg"] = injected_north_pa
        CONSOLE.info(f"[extreme-geo] sub_lat={injected_sub_lat:.2f}° north_pa={injected_north_pa:.2f}°")
    grs_limb_override = getattr(spec, "grs_limb_rel_deg", None)

    app_diam = math.degrees(2 * JUP_REQ_KM / (dist * 149597870.7)) * 3600.0
    period_s = 9 * 3600 + 55 * 60 + 29.711
    mjd = (t - dt.datetime(1858, 11, 17)).total_seconds() / 86400.0
    # Image-tied CM (truth for recovery). SPICE CM is recorded separately for provenance.
    cm = (360.0 * ((mjd - 51544.5) / (period_s / 86400.0)) + rng.uniform(-0.5, 0.5)) % 360.0

    # Metrology: keep GRS well on disk (not near limb) for fair recovery demos
    # GRS_LIMB_LON_REL forces placement (e.g. 75 = near limb) for validation harnesses.
    # EXTREME: grs_limb_rel_deg from spec for "every atom" training (35-95°)
    limb_rel = os.environ.get("GRS_LIMB_LON_REL", "").strip()
    if grs_limb_override is not None:
        try:
            grs_lon = (cm + float(grs_limb_override)) % 360.0
            CONSOLE.info(f"[extreme-geo] GRS forced limb_rel={float(grs_limb_override):.1f}°")
        except Exception:
            pass
    elif limb_rel:
        try:
            grs_lon = (cm + float(limb_rel)) % 360.0
            CONSOLE.info(f"Synthetic GRS forced near limb: lon_rel={float(limb_rel):.1f}°")
        except Exception:
            lon_span = 18.0 if mode == "metrology" else 32.0
            grs_lon = (cm + rng.uniform(-lon_span, lon_span)) % 360.0
    else:
        lon_span = 18.0 if mode == "metrology" else 32.0
        grs_lon = (cm + rng.uniform(-lon_span, lon_span)) % 360.0
    # Render the GRS at the SAME physical latitude the engine expects. The
    # literature -22.4 deg is planetographic; GRS_LAT0 is that value converted to
    # planetocentric (~-19.82), which is the convention used throughout the
    # renderer and the measurement stack. Hardcoding -22.0 planetocentric here
    # put the synthetic GRS ~2.2 deg away from the engine's search prior.
    grs_lat = GRS_LAT0 + float(rng.normal(0, 0.08 if mode == "metrology" else 0.12))
    grs_L = 12.0 + float(rng.uniform(-0.6 if mode == "metrology" else -1.0, 1.2 if mode == "metrology" else 1.6))
    grs_W = 8.0 + float(rng.uniform(-0.4 if mode == "metrology" else -0.5, 0.7 if mode == "metrology" else 0.9))

    zoom, pan_x, pan_y = REGION.get(spec.region, REGION["global"])
    a = 0.42 * min(w, h) / zoom
    xc = w * 0.5 + pan_x * w * 0.2
    yc = h * 0.5 + pan_y * h * 0.2
    b = a * (1 - FLAT)

    dtype = np.float32
    yy, xx = np.mgrid[0:h, 0:w].astype(dtype)
    # Render on the TRUE oblate spheroid, using the same geometry contract as
    # precision_engine.px_to_lonlat. The disk outline is an ellipse because the
    # spheroid projects to one, not because the y-axis is squashed by hand.
    #
    # Invert the line of sight analytically: for sky (X, Y) in units of R_eq,
    # the near-side surface point satisfies X^2 + Z^2 + Y^2/k^2 = 1 with
    # k = 1-f, so Z = sqrt(1 - X^2 - Y^2/k^2). The visible disk is where that
    # radicand is non-negative, which reproduces the correct limb ellipse
    # (semi-minor axis b = a(1-f)) without ever dividing Y by b.
    Xsky = (xx - xc) / (a + 1e-6)
    Ysky = (yc - yy) / (a + 1e-6)        # NOTE: equatorial scale on BOTH axes
    k = 1.0 - FLAT
    # TRUE SKY GEOMETRY (v6.8): sub-observer latitude + north-polar-axis PA.
    # The real Jupiter presents sub-lat ~ +/-3 deg and axis PA up to ~ +/-17 deg
    # (verified against SPICE: 2026-08-02 -> sub-lat +0.67 deg, PA 343.4 deg).
    # This block is the EXACT forward model whose inverse is
    # precision_engine.px_to_lonlat (same rotation order: tilt by sub-lat about
    # the sky x-axis, then rotate in the sky plane by PA, isotropic plate scale
    # last), so a frame rendered with (D, P) is measured consistently by a nav
    # carrying the same (D, P) — just like the real sky. With D=P=0 it is
    # algebraically identical to the original axis-aligned code, and we branch
    # so the arithmetic is *bitwise* identical too (campaign reproducibility).
    sub_D = math.radians(float(getattr(spec, "sub_lat_deg", 0.0) or 0.0))
    north_P = math.radians(float(getattr(spec, "north_pa_deg", 0.0) or 0.0))
    if abs(sub_D) < 1e-15 and abs(north_P) < 1e-15:
        X = Xsky
        Ys = Ysky
        radicand = 1.0 - X * X - (Ys / k) ** 2
        disk = radicand >= 0.0
        Z = np.sqrt(np.clip(radicand, 0.0, None)).astype(dtype)
        nx, ny, nz = X, Ys / (k * k), Z
        nlen = np.sqrt(nx * nx + ny * ny + nz * nz) + 1e-9
        mu = np.clip(nz / nlen, 0.0, 1.0).astype(dtype)
    else:
        cP, sP = math.cos(north_P), math.sin(north_P)
        cD, sD = math.cos(sub_D), math.sin(sub_D)
        # undo the sky-plane PA rotation (inverse of planet_xyz_to_px step 2)
        Xp = (Xsky * cP + Ysky * sP).astype(dtype)
        Yp = (-Xsky * sP + Ysky * cP).astype(dtype)
        # spheroid LOS intersection after the sub-lat tilt (px_to_lonlat inverses)
        inv_k2 = 1.0 / (k * k)
        Aq = cD * cD + sD * sD * inv_k2
        Bq = 2.0 * Yp * sD * cD * (inv_k2 - 1.0)
        Cq = Xp * Xp + Yp * Yp * (cD * cD * inv_k2 + sD * sD) - 1.0
        disc = Bq * Bq - 4.0 * Aq * Cq
        disk = disc >= 0.0
        T = (-Bq + np.sqrt(np.clip(disc, 0.0, None))) / (2.0 * Aq)
        X = Xp
        Ys = (Yp * cD + T * sD).astype(dtype)
        Z = (-Yp * sD + T * cD).astype(dtype)
        # emission: LOS in body frame is (0, sin D, cos D)
        nx, ny, nz = X, Ys / (k * k), Z
        nlen = np.sqrt(nx * nx + ny * ny + nz * nz) + 1e-9
        mu = np.clip((ny * sD + nz * cD) / nlen, 0.0, 1.0).astype(dtype)
    lon_rel = np.arctan2(X, np.maximum(Z, 1e-6))
    # planetocentric latitude from the body-frame point (X, Ys, Z)
    rad = np.sqrt(X * X + Ys * Ys + Z * Z) + 1e-9
    lat = np.arcsin(np.clip(Ys / rad, -1, 1))
    lon_abs = (cm + np.degrees(lon_rel)) % 360.0
    lat_deg = np.degrees(lat)
    lat_n = lat

    CONSOLE.info("Rendering photoreal atmosphere (soft belts / zonal streaks / GRS)...")

    belt = _belt_profile(lat_n)
    waves = _wavefield(lon_rel, lat_n, rng, contrast=float(spec.wave_contrast))

    # Zonal-streak residual (real look) + mild isotropic fine grain
    turb = _zonal_streak_noise(h, w, lat_n, lon_rel, rng)
    fine = _value_noise(h, w, rng, octaves=3, base=max(24, w // 80))
    turb = _shear_residual(0.75 * turb + 0.25 * fine, lat_n, lon_rel, strength=0.55)
    belt_mask = (belt < 0.50).astype(np.float32)
    turb = turb * (0.50 + 0.55 * belt_mask)

    # Limb factor for oval/GRS paint (matches atmosphere LD)
    ld = (0.55 + 0.45 * (mu ** 0.65)).astype(dtype)

    rgb = _realistic_atmosphere_rgb(belt, waves, turb, lat_deg, mu, disk, dtype=dtype)

    n_ovals = int(rng.integers(4, 9) if mode == "metrology" else rng.integers(6, 14))
    _paint_ovals(
        rgb, disk, lon_abs, lat_deg, ld, rng, n_ovals,
        grs_lon=grs_lon, grs_lat=grs_lat,
    )
    grs_mask, _, _ = _paint_grs(
        rgb, disk, lon_abs, lat_deg, ld, grs_lon, grs_lat, grs_L, grs_W, rng,
        metrology=(mode == "metrology"),
    )
    # Seed geometric centre kept for diagnostics
    grs_lon_seed, grs_lat_seed = float(grs_lon), float(grs_lat)

    rgb = _apply_realistic_optics(
        rgb,
        disk,
        a_eq_px=float(a),
        app_diam_as=float(app_diam),
        rng=rng,
        seeing_fwhm_arcsec=float(spec.seeing_fwhm_arcsec),
        noise_rms=float(spec.noise_rms),
        res_name=str(name),
    )

    # Truth AFTER blur/noise — same intensity definition the measurer sees
    mono_f = 0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2]
    if np.any(grs_mask):
        dark = float(np.percentile(mono_f[grs_mask], 90)) - mono_f
        red_dark = float(np.percentile(rgb[:, :, 0][grs_mask], 90)) - rgb[:, :, 0]
        wts = np.where(grs_mask, 0.55 * np.clip(dark, 0, None) + 0.45 * np.clip(red_dark, 0, None), 0.0)
        s = float(wts.sum())
        if s > 1e-8:
            lon_r = np.deg2rad(lon_abs)
            cx = float((np.cos(lon_r) * wts).sum() / s)
            sx = float((np.sin(lon_r) * wts).sum() / s)
            grs_lon = float(np.rad2deg(np.arctan2(sx, cx)) % 360.0)
            grs_lat = float((lat_deg * wts).sum() / s)
        else:
            grs_lon, grs_lat = grs_lon_seed, grs_lat_seed
    else:
        grs_lon, grs_lat = grs_lon_seed, grs_lat_seed

    CONSOLE.ok("Photoreal render complete (soft belts, zonal streaks, soft GRS, realistic seeing)")

    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%S")
    base_name = f"synth_{name}_{stamp}_s{seed}"
    png = out_dir / f"{base_name}.png"
    fit = out_dir / f"{base_name}.fit"
    truth_path = out_dir / f"{base_name}_truth.json"

    u8 = (np.clip(rgb, 0, 1) * 255).astype(np.uint8)
    if _HAS_PIL:
        Image.fromarray(u8, "RGB").save(png, optimize=False)
    else:
        ppm = png.with_suffix(".ppm")
        with open(ppm, "wb") as f:
            f.write(f"P6\n{w} {h}\n255\n".encode())
            f.write(u8.tobytes())
        png = ppm

    # GRS crop preview so you can *see* structure without opening 8K
    crop_path = None
    if spec.write_grs_crop and _HAS_PIL and np.any(grs_mask):
        try:
            ys, xs = np.where(grs_mask)
            y0 = max(0, int(ys.min() - 0.15 * (ys.max() - ys.min() + 1)))
            y1 = min(h, int(ys.max() + 0.15 * (ys.max() - ys.min() + 1)))
            x0 = max(0, int(xs.min() - 0.15 * (xs.max() - xs.min() + 1)))
            x1 = min(w, int(xs.max() + 0.15 * (xs.max() - xs.min() + 1)))
            # expand to square-ish window
            pad = int(0.35 * max(y1 - y0, x1 - x0))
            y0, y1 = max(0, y0 - pad), min(h, y1 + pad)
            x0, x1 = max(0, x0 - pad), min(w, x1 + pad)
            crop = u8[y0:y1, x0:x1]
            crop_path = out_dir / f"{base_name}_grs_crop.png"
            Image.fromarray(crop, "RGB").save(crop_path, optimize=False)
            CONSOLE.ok(f"GRS crop preview: {crop_path.name}")
        except Exception as e:
            CONSOLE.debug(f"GRS crop: {e}")

    try:
        import grs_complete_system as grs
        grs.write_fits(fit, np.moveaxis(rgb, 2, 0), {
            "OBJECT": "Jupiter-SynthHQ-v3",
            "DATE-OBS": t.isoformat(),
            "CMIII": float(cm),
            "GRSLON": float(grs_lon),
            "GRSLAT": float(grs_lat),
            "SEED": int(seed),
            "DISTAU": float(dist),
        })
    except Exception as e:
        CONSOLE.warn(f"FITS write: {e}")
        fit = png

    # disk structure metrics (for QA)
    disk_std = float(np.std((0.299 * rgb[:, :, 0] + 0.587 * rgb[:, :, 1] + 0.114 * rgb[:, :, 2])[disk]))

    truth = {
        "seed": seed,
        "resolution": name,
        "width": w,
        "height": h,
        "user_time_iso": user_time_iso_out,
        "time_error_seconds": time_error_used,
        "random_time": time_was_random,
        "requested_user_time_iso": (spec.user_time_iso or None),
        "region": spec.region,
        "cm_iii_deg": float(cm),
        "grs_lon_iii_deg": float(grs_lon),
        "grs_lat_deg": float(grs_lat),
        "grs_lon_seed_deg": float(grs_lon_seed),
        "grs_lat_seed_deg": float(grs_lat_seed),
        "grs_truth_definition": "intensity_weighted_dark_barycentre",
        "grs_length_deg": float(grs_L),
        "grs_width_deg": float(grs_W),
        "disk_xc": float(xc),
        "disk_yc": float(yc),
        "disk_a_eq_px": float(a),
        "sub_obs_lat_deg": float(math.degrees(sub_D)),
        "north_pa_deg": float(math.degrees(north_P)),
        "distance_au": float(dist),
        "apparent_diameter_arcsec": float(app_diam),
        "png": str(png),
        "fit": str(fit),
        "grs_crop_png": str(crop_path) if crop_path else None,
        "renderer": "synthetic_hq_v3_photoreal",
        "synth_mode": mode,
        "wave_contrast": float(spec.wave_contrast),
        "seeing_fwhm_arcsec": float(spec.seeing_fwhm_arcsec),
        "disk_intensity_std": disk_std,
        "target_sky_arcsec": 0.1 if mode == "metrology" else 0.5,
        "spice": spice_meta,
    }
    truth_path.write_text(json.dumps(truth, indent=2), encoding="utf-8")
    free_memory()
    CONSOLE.ok(
        f"Synthetic written: {png.name}  epoch={user_time_iso_out}  "
        f"truth lon={grs_lon:.3f}°  disk_std={disk_std:.4f}  random_time={time_was_random}"
    )
    return png, Path(fit), truth

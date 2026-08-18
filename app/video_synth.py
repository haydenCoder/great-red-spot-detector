#!/usr/bin/env python3
"""
video_synth.py — rotating Jupiter video synthesizer with full ground truth.

WHY THIS EXISTS
===============
`synthetic_hq` renders ONE photoreal frame per call (seconds each) — fine for
still-image validation, useless for 60-frame video benchmarks. Video stacking
(APS, drizzle, derotation) needs a *fast* frame generator whose atmosphere
ROTATES between frames exactly as the real planet does, so we can measure —
not hope — that derotation-aware stacking recovers what a static stack smears.

The generator here is a texture-map renderer:

  1. A deterministic (lat, lon) atmosphere texture is built: zonal belts,
     festoon waves, white ovals, dark spots, and a GRS oval with a darker rim.
  2. For each frame k at central meridian CM_k the texture is projected
     through the EXACT inverse of `precision_engine.px_to_lonlat` (same
     spheroid, same sub-lat tilt + north-PA rotation order as synthetic_hq),
     so campaign-style measurement recovers the planted truth to a fraction
     of a degree (verified by tests/test_video_jupiter.py).
  3. Each frame then gets its own seeing blur, tip/tilt shift, noise and gain
     jitter drawn from per-frame random walks — the four nuisances real
     captures have.

HONEST SCOPE
============
This is a *benchmark* renderer, not a photoreal one. The belts are smooth
analytical bands; the point is truthful geometry + realistic nuisance, which
is everything a stacking/derotation measurement depends on. For visual
realism use synthetic_hq.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

FLAT = 0.06487  # Jupiter dynamic flattening (same as precision_engine / synthetic_hq)


@dataclass
class VideoSynthSpec:
    width: int = 320
    height: int = 240
    n_frames: int = 30
    fps: float = 5.0                       # frames per second of capture
    cm_deg_per_h: float = 36.29            # System III synodic rate (~36.3 deg/h)
    cm0_deg: float = 120.0                 # CM III at frame 0
    grs_lon_iii_deg: float = 145.0         # planted GRS longitude
    grs_lat_deg: float = -20.0             # planetocentric
    sub_lat_deg: float = 0.0               # sub-observer latitude (sky truth)
    north_pa_deg: float = 0.0              # north-polar-axis PA, deg E of N
    disk_frac: float = 0.42                # equatorial radius = frac * min(w,h)
    seeing_fwhm_px: Tuple[float, float] = (0.6, 2.2)   # per-frame U(min, max)
    noise_rms: Tuple[float, float] = (0.002, 0.010)
    shift_rms_px: float = 1.5              # tip/tilt random walk, px/frame
    gain_jitter: float = 0.03              # per-frame brightness scale std
    wave_amp: float = 0.05                 # festoon/streak texture amplitude
    seed: int = 7
    rgb: bool = True


@dataclass
class VideoSynth:
    frames: List[np.ndarray]
    times_s: List[float]                   # seconds since frame 0
    truth: Dict[str, Any]


# ---------------------------------------------------------------------------
# Atmosphere texture
# ---------------------------------------------------------------------------

def _belt_albedo(lat_deg: np.ndarray) -> np.ndarray:
    """Analytical zonal albedo profile (unit-less, ~0.55..0.95)."""
    l = lat_deg
    alb = np.full_like(l, 0.82)
    # bright equatorial zone, SEB dark between ~-7..-21, NEB ~ +7..+17,
    # temperate belts and polar hoods — canonical Jupiter scheme.
    alb += 0.10 * np.exp(-0.5 * (l / 6.0) ** 2)                       # EZ
    alb -= 0.24 * np.exp(-0.5 * ((l + 14.0) / 5.5) ** 2)              # SEB dark
    alb -= 0.18 * np.exp(-0.5 * ((l - 12.0) / 5.0) ** 2)              # NEB dark
    alb += 0.05 * np.exp(-0.5 * ((l + 28.0) / 8.0) ** 2)              # STrZ
    alb += 0.05 * np.exp(-0.5 * ((l - 34.0) / 8.0) ** 2)              # NTrZ
    alb -= 0.12 * np.exp(-0.5 * ((np.abs(l) - 62.0) / 14.0) ** 2)     # polar dinge
    return np.clip(alb, 0.05, 1.1)


def build_texture(spec: VideoSynthSpec, rng: np.random.Generator,
                  n_lon: int = 2048, n_lat: int = 512) -> np.ndarray:
    """(n_lat, n_lon, 3) albedo texture in planetocentric lat / System III lon."""
    lat_axis = np.linspace(-90.0, 90.0, n_lat)
    lon_axis = np.linspace(0.0, 360.0, n_lon, endpoint=False)
    LAT, LON = np.meshgrid(lat_axis, lon_axis, indexing="ij")

    albedo = np.repeat(_belt_albedo(LAT), 1, axis=1)

    # Festoons + waves: STRONG longitudinal texture riding the belt/zone
    # boundaries (this is what rotation registration locks onto — a benchmark
    # texture with pure-lat belts is translation-invariant in longitude and
    # makes every derotator/tracker pass vacuously).
    amp = float(spec.wave_amp)
    belt_edges = (-21.0, -7.0, 7.0, 17.0, -35.0, 40.0, -50.0, 55.0)
    for lat0 in belt_edges:
        for _ in range(2):
            freq = float(rng.uniform(9, 30))       # 9-30 deg wavelength
            phase = float(rng.uniform(0, 2 * np.pi))
            spatial = float(rng.uniform(1.8, 4.5))
            w = amp * float(rng.uniform(1.2, 2.4))
            albedo += (w * np.sin(np.deg2rad(LON * 360.0 / freq) + phase)
                       * np.exp(-0.5 * ((LAT - lat0) / spatial) ** 2))
    # mid-belt meanders
    for k in range(6):
        lat0 = float(rng.uniform(-50, 40))
        freq = float(rng.uniform(18, 60))
        phase = float(rng.uniform(0, 2 * np.pi))
        spatial = float(rng.uniform(3, 14))
        w = amp * float(rng.uniform(0.5, 1.2))
        albedo += (w * np.sin(np.deg2rad(LON * 360.0 / freq) + phase)
                   * np.exp(-0.5 * ((LAT - lat0) / spatial) ** 2))
    # fine zonal streaks
    for k in range(4):
        lat0 = float(rng.uniform(-60, 60))
        albedo += (amp * 0.8 * np.sin(np.deg2rad(LON * 2.0) * float(rng.uniform(8, 24)))
                   * np.exp(-0.5 * ((LAT - lat0) / 2.5) ** 2))

    # white ovals (STZ style), dark spots/festoon pips — small and numerous:
    # local contrast is what AP tracking needs
    for k in range(8):
        la, lo = float(rng.uniform(-45, -25)), float(rng.uniform(0, 360))
        slon, slat = float(rng.uniform(1.5, 4.0)), float(rng.uniform(1.0, 2.5))
        dlon = ((LON - lo + 180.0) % 360.0) - 180.0
        bump = np.exp(-0.5 * ((dlon / slon) ** 2 + ((LAT - la) / slat) ** 2))
        albedo += 0.22 * bump
    for k in range(10):
        la, lo = float(rng.uniform(-35, 30)), float(rng.uniform(0, 360))
        slon, slat = float(rng.uniform(0.8, 2.2)), float(rng.uniform(0.6, 1.6))
        dlon = ((LON - lo + 180.0) % 360.0) - 180.0
        bump = np.exp(-0.5 * ((dlon / slon) ** 2 + ((LAT - la) / slat) ** 2))
        albedo -= 0.25 * bump

    albedo = np.clip(albedo, 0.03, 1.2)

    # base colour: warm white zones, tan belts (redder where dark)
    lum = albedo
    red_tint = np.clip(0.9 - lum, 0.0, None) * 0.6
    r = lum * (1.00 + 0.5 * red_tint)
    g = lum * (0.97 - 0.10 * red_tint)
    b = lum * (0.92 - 0.45 * red_tint)
    tex = np.stack([r, g, b], axis=-1)

    # GRS: orange oval + darker rim + brighter collar, at planted lon/lat
    dlon = ((LON - spec.grs_lon_iii_deg + 180.0) % 360.0) - 180.0
    dlat = LAT - spec.grs_lat_deg
    grs_core = np.exp(-0.5 * ((dlon / 5.2) ** 2 + (dlat / 3.4) ** 2))
    grs_rim = np.exp(-0.5 * ((dlon / 6.8) ** 2 + (dlat / 4.6) ** 2)) - grs_core
    collar = np.exp(-0.5 * ((dlon / 8.5) ** 2 + ((dlat + 6.0) / 3.0) ** 2))
    core = grs_core[..., None]
    tex[..., 0] += 0.28 * core[..., 0]          # red boost
    tex[..., 1] -= 0.10 * core[..., 0]
    tex[..., 2] -= 0.22 * core[..., 0]
    rim = np.clip(grs_rim, 0.0, None)[..., None]
    tex[..., 0] -= 0.05 * rim[..., 0]           # darker rim (all channels)
    tex[..., 1] -= 0.08 * rim[..., 0]
    tex[..., 2] -= 0.08 * rim[..., 0]
    tex += 0.06 * collar[..., None]
    return np.clip(tex, 0.0, 1.5)


# ---------------------------------------------------------------------------
# Frame projection (inverse of precision_engine.px_to_lonlat — same rotations)
# ---------------------------------------------------------------------------

def _project_fields(h: int, w: int, xc: float, yc: float, a: float,
                    cm_deg: float, sub_lat_deg: float, north_pa_deg: float):
    """Per-pixel (disk, mu, lat_deg, lon_deg) for a given CM and orientation."""
    k = 1.0 - FLAT
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    Xsky = (xx - xc) / a
    Ysky = (yc - yy) / a
    D = math.radians(sub_lat_deg)
    P = math.radians(north_pa_deg)
    cP, sP = math.cos(P), math.sin(P)
    cD, sD = math.cos(D), math.sin(D)
    Xp = Xsky * cP + Ysky * sP
    Yp = -Xsky * sP + Ysky * cP
    inv_k2 = 1.0 / (k * k)
    Aq = cD * cD + sD * sD * inv_k2
    Bq = 2.0 * Yp * sD * cD * (inv_k2 - 1.0)
    Cq = Xp * Xp + Yp * Yp * (cD * cD * inv_k2 + sD * sD) - 1.0
    disc = Bq * Bq - 4.0 * Aq * Cq
    disk = disc >= 0.0
    T = np.where(disk, (-Bq + np.sqrt(np.clip(disc, 0.0, None))) / (2.0 * Aq), 0.0)
    X, Ys, Z = Xp, Yp * cD + T * sD, -Yp * sD + T * cD
    nx, ny, nz = X, Ys / (k * k), Z
    nlen = np.sqrt(nx * nx + ny * ny + nz * nz) + 1e-9
    mu = np.clip((ny * sD + nz * cD) / nlen, 0.0, 1.0)
    lon_rel = np.arctan2(X, np.maximum(Z, 1e-9))
    rad = np.sqrt(X * X + Ys * Ys + Z * Z) + 1e-9
    lat = np.degrees(np.arcsin(np.clip(Ys / rad, -1.0, 1.0)))
    lon = (cm_deg + np.degrees(lon_rel)) % 360.0
    return disk, mu, lat, lon


def _sample_texture(tex: np.ndarray, lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    from scipy.ndimage import map_coordinates
    n_lat, n_lon = tex.shape[0], tex.shape[1]
    iy = (lat + 90.0) / 180.0 * (n_lat - 1)
    ix = lon / 360.0 * n_lon
    chans = [map_coordinates(tex[..., c], [iy, ix], order=1, mode="grid-wrap" if c >= 0 else "nearest")
             for c in range(tex.shape[-1])]
    return np.stack(chans, axis=-1)


def render_video(spec: VideoSynthSpec) -> VideoSynth:
    from scipy.ndimage import gaussian_filter, shift as nd_shift

    rng = np.random.default_rng(int(spec.seed))
    h, w = int(spec.height), int(spec.width)
    n = int(spec.n_frames)
    a = float(spec.disk_frac) * min(w, h)
    xc, yc = w * 0.5, h * 0.5
    tex = build_texture(spec, np.random.default_rng(int(spec.seed) + 999))
    sky_bg = 0.012 + 0.004 * rng.random((h, w))

    # tip/tilt random walk (arcsec->px absorption into shift is fine here)
    walk = np.cumsum(rng.normal(0.0, float(spec.shift_rms_px), size=(n, 2)), axis=0)
    walk -= walk.mean(axis=0)
    seeings = rng.uniform(*spec.seeing_fwhm_px, size=n)
    noises = rng.uniform(*spec.noise_rms, size=n)
    gains = 1.0 + rng.normal(0.0, float(spec.gain_jitter), size=n)

    frames: List[np.ndarray] = []
    times_s: List[float] = []
    cms: List[float] = []
    for k in range(n):
        t = k / float(spec.fps)
        cm = (spec.cm0_deg + spec.cm_deg_per_h * t / 3600.0) % 360.0
        disk, mu, lat, lon = _project_fields(h, w, xc, yc, a, cm,
                                             spec.sub_lat_deg, spec.north_pa_deg)
        img = _sample_texture(tex, lat, lon)
        ld = np.clip(mu, 0.0, 1.0) ** 0.6
        img = img * ld[..., None]
        img = np.where(disk[..., None], img, sky_bg[..., None])
        sigma = seeings[k] / 2.354820045
        img = gaussian_filter(img, sigma=(sigma, sigma, 0), mode="nearest")
        dyx = walk[k]
        if abs(dyx[0]) > 1e-9 or abs(dyx[1]) > 1e-9:
            img = nd_shift(img, shift=(dyx[0], dyx[1], 0.0), order=3, mode="nearest")
        img = img * gains[k] + rng.normal(0.0, noises[k], size=img.shape)
        img = np.clip(img, 0.0, 1.5)
        frames.append(img[..., :3] if spec.rgb else img[..., :3].mean(-1))
        times_s.append(t)
        cms.append(cm)

    ref = n // 2
    cm_ref = (spec.cm0_deg + spec.cm_deg_per_h * times_s[ref] / 3600.0) % 360.0
    rel_ref = ((spec.grs_lon_iii_deg - cm_ref + 540.0) % 360.0) - 180.0
    truth: Dict[str, Any] = {
        "driver": "video_synth",
        "width": w, "height": h, "n_frames": n, "fps": float(spec.fps),
        "times_s": times_s,
        "cm_iii_per_frame_deg": cms,
        "ref_index": ref,
        "cm_ref_deg": cm_ref,
        "grs_lon_iii_deg": float(spec.grs_lon_iii_deg),
        "grs_lat_deg": float(spec.grs_lat_deg),
        "grs_rel_at_ref_deg": rel_ref,
        "sub_lat_deg": float(spec.sub_lat_deg),
        "north_pa_deg": float(spec.north_pa_deg),
        "disk_a_eq_px": a, "disk_xc_px": xc, "disk_yc_px": yc,
        "seeing_fwhm_px_per_frame": seeings.tolist(),
    }
    return VideoSynth(frames=frames, times_s=times_s, truth=truth)


def derotate_truth_dt(spec_dt: Sequence[float], ref_index: int) -> List[float]:
    """dt_s[k] relative to the reference frame (what derotators consume)."""
    return [float(t - spec_dt[ref_index]) for t in spec_dt]


__all__ = [
    "VideoSynthSpec", "VideoSynth", "render_video", "build_texture",
    "derotate_truth_dt", "FLAT",
]

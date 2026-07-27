#!/usr/bin/env python3
"""
VLBI-inspired advanced metrology for ground-based GRS photography
================================================================

Literal radio VLBI reaches microarcseconds on *compact* continuum sources with
baselines of Earth diameter, phase referencing, and delay models. An optical
photo of an extended cloud feature cannot match that floor.

This module brings the *methodology* of high-end interferometric metrology to
lucky-imaging / high-res planetary work:

  1) Full geometric model (orientation, distance, light-time, time→CM)
  2) Calibrated instrument response (limb nav bootstrap, PSF/μ-aware maps)
  3) Phase-reference analog: local differential probe injection–recovery
     using the *same* science pipeline (not a weaker dark-peak finder)
  4) Multi-definition closure (template / map / moment / multi-scale)
  5) Multi-filter residual closure after 1/λ² DCR model
  6) Hierarchical Monte Carlo (limb ⊕ map noise ⊕ template hyperparameters ⊕ time)
  7) Formal error budget: random ⊕ systematic ⊕ geometry ⊕ time ⊕ bias uncertainty
  8) Publication bundle with hashes, seeds, and intermediate products

Honest optical envelope (ground-based extended feature):
  - Diffraction: ~0.15″ for 1 m @ 550 nm (Airy); lucky imaging often 0.3–1″
  - GRS is extended → definition systematics usually dominate photon noise
  - Target after this stack: **0.1–0.5″ relative** on high-res frames near CM,
    **1–2″ absolute** when ephemeris/CM are approximate; tighter if SPICE/Horizons
    orientation is supplied.

Naming: "VLBI-grade" = VLBI *methods* applied to planetary imaging, not μas claims.
"""
from __future__ import annotations

import hashlib
import json
import math
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from verbose_log import CONSOLE
from precision_engine import (
    FLAT,
    JUP_REQ_KM,
    JUP_RPOL_KM,
    AU_KM,
    ARCSEC_PER_RAD,
    NavState,
    GRSPrecisionResult,
    deg2rad,
    rad2deg,
    wrap_deg,
    wrap_diff,
    km_per_deg_lon,
    km_per_deg_lat,
    deg_to_arcsec_on_sky,
    sky_error_arcsec,
    to_mono,
    rough_disk_mask,
    fit_limb_nav,
    px_to_lonlat,
    make_cylindrical,
    _gauss,
    _template_match_grs,
    _map_dark_centroid,
    _moment_mask_grs,
    _method_is_sane,
    _circular_weighted_mean,
    _choose_size,
    METHOD_WEIGHTS,
    measure_grs_precision,
    monte_carlo_precision,
)

# ---------------------------------------------------------------------------
# Physical / system clocks
# ---------------------------------------------------------------------------

SYS3_PERIOD_S = 9 * 3600 + 55 * 60 + 29.711  # System III (1965)
DEG_PER_SEC_SYS3 = 360.0 / SYS3_PERIOD_S


@dataclass
class EphemerisState:
    """Observer-centric Jupiter geometry for one epoch."""
    t_utc_iso: str
    distance_au: float = 5.2
    cm_iii_deg: float = 0.0
    sub_obs_lat_deg: float = 0.0
    sub_obs_lon_deg: float = 0.0
    north_pa_deg: float = 0.0
    apparent_diameter_arcsec: float = 40.0
    light_time_s: float = 0.0
    source: str = "approx"
    sigma_cm_deg: float = 0.5  # honest prior if not SPICE-tied
    sigma_distance_frac: float = 0.01
    sigma_pa_deg: float = 1.0
    sigma_sublat_deg: float = 0.5
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AdvancedNav:
    """Navigation with full orientation (VLBI-style geometric model)."""
    xc: float
    yc: float
    a_eq_px: float
    flattening: float = FLAT
    cm_iii_deg: float = 0.0
    distance_au: float = 5.2
    sub_lat_deg: float = 0.0
    north_pa_deg: float = 0.0
    sigma_xc: float = 0.3
    sigma_yc: float = 0.3
    sigma_a: float = 0.5
    limb_n_rays: int = 720
    limb_rms_px: float = float("nan")

    @property
    def b_pol_px(self) -> float:
        return self.a_eq_px * (1.0 - self.flattening)

    def to_nav_state(self) -> NavState:
        return NavState(
            xc=self.xc,
            yc=self.yc,
            a_eq_px=self.a_eq_px,
            flattening=self.flattening,
            cm_iii_deg=self.cm_iii_deg,
            distance_au=self.distance_au,
            sub_lat_deg=self.sub_lat_deg,
            north_pa_deg=self.north_pa_deg,
        )

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["b_pol_px"] = self.b_pol_px
        return d


@dataclass
class ErrorBudget:
    """Formal VLBI-style error budget in degrees and arcsec."""
    sigma_random_lon_deg: float
    sigma_random_lat_deg: float
    sigma_definition_lon_deg: float
    sigma_definition_lat_deg: float
    sigma_nav_lon_deg: float
    sigma_nav_lat_deg: float
    sigma_time_lon_deg: float
    sigma_ephem_lon_deg: float
    sigma_bias_lon_deg: float
    sigma_bias_lat_deg: float
    sigma_total_lon_deg: float
    sigma_total_lat_deg: float
    sigma_random_sky_arcsec: float
    sigma_systematic_sky_arcsec: float
    sigma_total_sky_arcsec: float
    components_sky_arcsec: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class VLBIResult:
    """Publication product — VLBI-inspired ground optical metrology."""
    lon_iii_deg: float
    lat_deg: float
    lat_planetographic_deg: float
    length_deg: float
    width_deg: float
    # raw vs calibrated
    lon_raw_deg: float
    lat_raw_deg: float
    bias_lon_deg: float
    bias_lat_deg: float
    # primary definition
    primary_method: str
    # errors
    error_budget: Dict[str, Any]
    # diagnostics
    grade: str
    optical_floor_arcsec: float
    injection_n: int
    injection_mean_sky_arcsec: float
    definition_n: int
    filter_closure_arcsec: Optional[float]
    hierarchical_mc: Dict[str, Any] = field(default_factory=dict)
    definitions: List[Dict[str, Any]] = field(default_factory=list)
    methods: Dict[str, Any] = field(default_factory=dict)
    ephemeris: Dict[str, Any] = field(default_factory=dict)
    nav: Dict[str, Any] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
    elapsed_s: float = 0.0
    mode: str = "VLBI_INSPIRED_OPTICAL"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Coordinate utilities
# ---------------------------------------------------------------------------

def planetocentric_to_planetographic(lat_c_deg: float, flattening: float = FLAT) -> float:
    """Jupiter planetographic latitude from planetocentric."""
    e2 = flattening * (2.0 - flattening)
    lat_c = deg2rad(lat_c_deg)
    # tan(φg) = tan(φc) / (1-e²) for spheroid (approx)
    lat_g = math.atan(math.tan(lat_c) / max(1e-12, 1.0 - e2))
    return rad2deg(lat_g)


def time_error_to_lon_sigma(time_error_seconds: float) -> float:
    """System III longitude uncertainty from absolute timing error."""
    return abs(float(time_error_seconds)) * DEG_PER_SEC_SYS3


def _hash_array(a: np.ndarray) -> str:
    a = np.ascontiguousarray(a)
    b = a.tobytes()
    return hashlib.sha256(b[: min(len(b), 2_000_000)]).hexdigest()[:16]


def _rotate_points(xs: np.ndarray, ys: np.ndarray, xc: float, yc: float, pa_deg: float) -> Tuple[np.ndarray, np.ndarray]:
    """Rotate image coords so that Jovian north aligns with -y after rotation by -PA."""
    th = deg2rad(-pa_deg)
    c, s = math.cos(th), math.sin(th)
    dx, dy = xs - xc, ys - yc
    xr = xc + c * dx - s * dy
    yr = yc + s * dx + c * dy
    return xr, yr


# ---------------------------------------------------------------------------
# Ephemeris
# ---------------------------------------------------------------------------

def build_ephemeris_approx(
    t_utc_iso: str,
    time_error_seconds: float = 0.0,
    cm_override: Optional[float] = None,
    distance_override: Optional[float] = None,
) -> EphemerisState:
    """Analytical geometry (differentials OK; absolute CM has ~degree-level zero)."""
    import datetime as dt
    s = (t_utc_iso or "").strip().replace("T", " ").replace("Z", "")
    if not s:
        raise ValueError(
            "Observation UTC required for System III geometry "
            "(refusing silent datetime.now() / utcnow())."
        )
    t = None
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            t = dt.datetime.strptime(s[:26], fmt) if "%f" in fmt else dt.datetime.strptime(s[:19], fmt)
            break
        except Exception:
            continue
    if t is None:
        raise ValueError(
            f"Cannot parse observation UTC {t_utc_iso!r}. "
            "Use e.g. '2026-01-10 15:39:26' (mid-exposure)."
        )
    t = t + dt.timedelta(seconds=float(time_error_seconds))

    mjd0 = dt.datetime(1858, 11, 17)
    mjd = (t - mjd0).total_seconds() / 86400.0
    # TT-ish
    tdb = mjd + 69.184 / 86400.0
    period_days = SYS3_PERIOD_S / 86400.0
    year_frac = t.year + t.timetuple().tm_yday / 365.25
    dist = 5.2 + 0.55 * math.cos(2 * math.pi * (year_frac - 2000) / 1.09)
    if distance_override is not None:
        dist = float(distance_override)
    lt = (dist * AU_KM * 1000.0) / 299792458.0

    if cm_override is not None:
        # Caller-supplied CM already matches the image / WinJUPOS / SPICE — do NOT
        # re-apply light-time (that double-shifts absolute longitude by ~0.3–0.5°×c).
        cm = wrap_deg(float(cm_override))
        cm_source = "override"
        sig_cm = 0.05
    else:
        cm = wrap_deg(360.0 * ((tdb - 51544.5) / period_days))
        # emission-time CM correction only for free-running analytical clock
        cm = wrap_deg(cm - DEG_PER_SEC_SYS3 * lt)
        cm_source = "analytical"
        sig_cm = 0.5

    diam = math.degrees(2 * JUP_REQ_KM / (dist * AU_KM)) * 3600.0
    # Seasonal sub-lat model is only a prior; imaging nav defaults it to 0 unless enabled
    sub_lat = 3.0 * math.sin(2 * math.pi * (tdb - 51544.5) / (11.86 * 365.25))

    notes = [
        "Analytical CM III: absolute zero may be offset; excellent for differentials/derotation.",
        "Supply cm_override / SPICE / WinJUPOS CM for absolute System III publication.",
        f"CM source={cm_source}; light-time applied only to free analytical CM.",
        f"Time error {time_error_seconds}s → σ_lon≈{time_error_to_lon_sigma(time_error_seconds):.4f}°",
    ]
    return EphemerisState(
        t_utc_iso=t.isoformat(),
        distance_au=dist,
        cm_iii_deg=cm,
        sub_obs_lat_deg=sub_lat,
        sub_obs_lon_deg=cm,
        north_pa_deg=0.0,
        apparent_diameter_arcsec=diam,
        light_time_s=lt,
        source="analytical" if cm_override is None else "cm_override",
        sigma_cm_deg=sig_cm,
        notes=notes,
    )


def enrich_ephemeris_from_horizons(eph: EphemerisState) -> EphemerisState:
    """Pull distance / diameter from Horizons when online; parse best-effort."""
    try:
        from nasa_compare import fetch_horizons
        import datetime as dt
        t = dt.datetime.fromisoformat(eph.t_utc_iso)
        h = fetch_horizons(t)
        if not h:
            return eph
        # Prefer model fields already filled by nasa_compare
        if h.get("distance_au_model"):
            eph.distance_au = float(h["distance_au_model"])
        if h.get("apparent_diameter_arcsec_model"):
            eph.apparent_diameter_arcsec = float(h["apparent_diameter_arcsec_model"])
        eph.light_time_s = (eph.distance_au * AU_KM * 1000.0) / 299792458.0
        eph.source = "analytical+Horizons_geometry"
        eph.sigma_distance_frac = 0.002
        eph.notes.append("Horizons geometry (distance/diameter model) applied.")
        # Try to parse delta from text excerpt if present
        text = h.get("excerpt") or ""
        for line in text.splitlines():
            parts = line.split()
            # Observer table often has delta as AU in a column — best-effort float scan
            if len(parts) >= 4 and parts[0][:4].isdigit():
                for p in parts:
                    try:
                        v = float(p)
                        if 3.5 < v < 7.0:
                            eph.distance_au = v
                            eph.apparent_diameter_arcsec = (
                                math.degrees(2 * JUP_REQ_KM / (v * AU_KM)) * 3600.0
                            )
                            eph.notes.append(f"Parsed delta≈{v:.5f} AU from Horizons text.")
                            break
                    except ValueError:
                        continue
        return eph
    except Exception as ex:
        eph.notes.append(f"Horizons enrich skipped: {ex}")
        return eph


# ---------------------------------------------------------------------------
# Advanced limb navigation (LSQ + bootstrap)
# ---------------------------------------------------------------------------

def fit_limb_advanced(
    image: np.ndarray,
    eph: EphemerisState,
    n_rays: int = 900,
    bootstrap: int = 40,
    seed: int = 0,
    apply_sub_lat: bool = False,
) -> AdvancedNav:
    """
    Precision limb: trust the stable multi-iteration radial-gradient centre
    (`fit_limb_nav`), then bootstrap ray subsets *around that centre* only to
    estimate σ(xc, yc, a). Re-fitting the centre from a single ray pass is
    unstable on banded planets (SEB/NEB gradients pull the ellipse).

    Sub-observer latitude is applied only when `apply_sub_lat=True` or when
    |sub_lat| is known from a real ephemeris override (not the crude seasonal model).
    """
    im = to_mono(image)
    base = fit_limb_nav(im, n_rays=n_rays, cm_iii_deg=eph.cm_iii_deg, distance_au=eph.distance_au)
    rng = np.random.default_rng(seed)
    h, w = im.shape
    xc0, yc0, a0 = float(base.xc), float(base.yc), float(base.a_eq_px)

    # Collect limb points relative to stable centre (for bootstrap σ only)
    pts = []
    for i in range(min(n_rays, 720)):
        ang = 2 * math.pi * i / min(n_rays, 720)
        rs = np.linspace(0.55 * a0, 1.28 * a0, 280)
        xs_r = xc0 + rs * math.cos(ang)
        ys_r = yc0 + rs * math.sin(ang)
        x0 = np.clip(np.floor(xs_r).astype(int), 0, w - 2)
        y0 = np.clip(np.floor(ys_r).astype(int), 0, h - 2)
        dx = xs_r - x0
        dy = ys_r - y0
        prof = (
            im[y0, x0] * (1 - dx) * (1 - dy)
            + im[y0, x0 + 1] * dx * (1 - dy)
            + im[y0 + 1, x0] * (1 - dx) * dy
            + im[y0 + 1, x0 + 1] * dx * dy
        )
        g = np.gradient(prof)
        j = int(np.argmin(g))
        if 1 <= j < len(rs) - 1:
            gm, g0, gp = g[j - 1], g[j], g[j + 1]
            den = gm - 2 * g0 + gp
            delta = 0.0 if abs(den) < 1e-12 else 0.5 * (gm - gp) / den
            r = rs[j] + delta * (rs[1] - rs[0])
        else:
            r = rs[min(max(j, 0), len(rs) - 1)]
        wgt = abs(float(g[j])) if 0 <= j < len(g) else 1.0
        pts.append((yc0 + r * math.sin(ang), xc0 + r * math.cos(ang), wgt, r))
    pts = np.asarray(pts, dtype=np.float64) if pts else np.zeros((0, 4))

    def fit_subset(sel: np.ndarray) -> Tuple[float, float, float]:
        """Small refinement around base; heavily damped toward (xc0,yc0,a0)."""
        yy, xx, ww = sel[:, 0], sel[:, 1], sel[:, 2]
        ww = np.maximum(ww, 1e-6)
        ww = ww / ww.sum()
        # radius about fixed centre
        rr = np.sqrt((xx - xc0) ** 2 + ((yy - yc0) / (1 - FLAT)) ** 2)
        med = float(np.median(rr))
        mad = float(np.median(np.abs(rr - med))) + 1e-9
        keep = np.abs(rr - med) < 3.5 * 1.4826 * mad
        if keep.sum() < 30:
            return xc0, yc0, a0
        xx, yy, ww, rr = xx[keep], yy[keep], ww[keep], rr[keep]
        ww = ww / ww.sum()
        # residual centre offset (damped)
        dx = float(np.sum(ww * (xx - xc0)))
        dy = float(np.sum(ww * (yy - yc0)))
        # reject large pulls from belts
        dx = float(np.clip(dx, -2.0, 2.0))
        dy = float(np.clip(dy, -2.0, 2.0))
        a = float(np.sum(ww * np.sqrt((xx - (xc0 + dx)) ** 2 + ((yy - (yc0 + dy)) / (1 - FLAT)) ** 2)))
        a = float(np.clip(a, 0.92 * a0, 1.08 * a0))
        return xc0 + 0.35 * dx, yc0 + 0.35 * dy, a

    xc, yc, a = xc0, yc0, a0
    rms = float("nan")
    if len(pts) > 50:
        xc, yc, a = fit_subset(pts)
        rr = np.sqrt((pts[:, 1] - xc) ** 2 + ((pts[:, 0] - yc) / (1 - FLAT)) ** 2)
        rms = float(np.std(rr - a))

    b_xc, b_yc, b_a = [], [], []
    n_boot = max(0, int(bootstrap))
    for _ in range(n_boot):
        if len(pts) < 40:
            break
        idx = rng.choice(len(pts), size=max(40, len(pts) // 2), replace=True)
        try:
            x, y, aa = fit_subset(pts[idx])
            b_xc.append(x); b_yc.append(y); b_a.append(aa)
        except Exception:
            continue
    sx = float(np.std(b_xc, ddof=1)) if len(b_xc) > 5 else 0.25
    sy = float(np.std(b_yc, ddof=1)) if len(b_yc) > 5 else 0.25
    sa = float(np.std(b_a, ddof=1)) if len(b_a) > 5 else 0.4
    # never claim sub-pixel σ tighter than bootstrap allows, but also not absurd
    sx = float(np.clip(sx, 0.05, 2.0))
    sy = float(np.clip(sy, 0.05, 2.0))
    sa = float(np.clip(sa, 0.05, 3.0))

    # Orientation: ONLY when explicitly requested (apply_sub_lat) or pro-eph
    # sets apply_orientation. Never infer from source-name heuristics alone
    # (Horizons sub-lat on a sub-lat=0 synthetic destroys absolute lon).
    apply = bool(apply_sub_lat or getattr(eph, "apply_orientation", False))
    if "apply_ori" in str(getattr(eph, "source", "") or ""):
        apply = True
    if "disabled_for_cm_override" in str(getattr(eph, "source", "") or ""):
        apply = False
    # Also check notes/source from pro bridge
    src = str(getattr(eph, "source", "") or "")
    if "disabled_for_cm_override" in src:
        apply = False
    sub_lat = float(eph.sub_obs_lat_deg or 0.0) if apply else 0.0
    north_pa = float(eph.north_pa_deg or 0.0) if apply else 0.0

    CONSOLE.ok(
        f"Advanced limb (stable): xc={xc:.2f}±{sx:.2f} yc={yc:.2f}±{sy:.2f} "
        f"a={a:.2f}±{sa:.2f}px  rms={rms:.3f}px  boot={len(b_xc)}  "
        f"sub_lat={sub_lat:.2f}° PA={north_pa:.2f}° apply_ori={apply}"
    )
    return AdvancedNav(
        xc=xc, yc=yc, a_eq_px=a, flattening=FLAT,
        cm_iii_deg=eph.cm_iii_deg, distance_au=eph.distance_au,
        sub_lat_deg=sub_lat, north_pa_deg=north_pa,
        sigma_xc=sx, sigma_yc=sy, sigma_a=sa,
        limb_n_rays=n_rays, limb_rms_px=rms if not math.isnan(rms) else 0.5,
    )


# ---------------------------------------------------------------------------
# Oriented cylindrical map (sub-observer latitude)
# ---------------------------------------------------------------------------

def make_cylindrical_oriented(
    image: np.ndarray,
    nav: AdvancedNav,
    width: int = 2880,
    height: int = 1440,
) -> np.ndarray:
    """
    Orthographic sample with sub-observer latitude tilt.
    lon ∈ [-90, 90] about CM, lat planetocentric.
    """
    im = to_mono(image)
    lons = np.linspace(-90.0, 90.0, width)
    lats = np.linspace(90.0, -90.0, height)
    lon_g, lat_g = np.meshgrid(lons, lats)
    lon_r = np.deg2rad(lon_g)
    lat_r = np.deg2rad(lat_g)
    # planetocentric unit vector in body frame (x toward CM, y north, z out)
    # With sub-obs latitude D: rotate about x by -D
    D = deg2rad(nav.sub_lat_deg)
    # Standard: X_e = cos(lat)sin(lon), Y_e = sin(lat), Z_e = cos(lat)cos(lon)  (μ)
    Xe = np.cos(lat_r) * np.sin(lon_r)
    Ye = np.sin(lat_r)
    Ze = np.cos(lat_r) * np.cos(lon_r)
    # rotate sub-lat: Y' = Ye cos D - Ze sin D; Z' = Ye sin D + Ze cos D
    cD, sD = math.cos(D), math.sin(D)
    Yp = Ye * cD - Ze * sD
    Zp = Ye * sD + Ze * cD
    Xp = Xe
    mu = Zp
    # optional NP PA: rotate (X,Y) in the sky plane
    pa = deg2rad(nav.north_pa_deg)
    cP, sP = math.cos(pa), math.sin(pa)
    Xsky = Xp * cP - Yp * sP
    Ysky = Xp * sP + Yp * cP
    xs = nav.xc + Xsky * nav.a_eq_px
    ys = nav.yc - Ysky * nav.b_pol_px
    h, w = im.shape
    x0 = np.floor(xs).astype(np.int64)
    y0 = np.floor(ys).astype(np.int64)
    dx = xs - x0
    dy = ys - y0
    valid = (mu > 0.02) & (x0 >= 0) & (x0 < w - 1) & (y0 >= 0) & (y0 < h - 1)
    out = np.zeros((height, width), dtype=np.float64)
    x0c = np.clip(x0, 0, w - 2)
    y0c = np.clip(y0, 0, h - 2)
    samp = (
        im[y0c, x0c] * (1 - dx) * (1 - dy)
        + im[y0c, x0c + 1] * dx * (1 - dy)
        + im[y0c + 1, x0c] * (1 - dx) * dy
        + im[y0c + 1, x0c + 1] * dx * dy
    )
    # Gentle μ flatten (avoid strong limb darkening false peaks). Keep mild.
    mu_c = np.clip(mu, 0.12, 1.0)
    samp = samp / (0.55 + 0.45 * mu_c)
    out[valid] = samp[valid]
    return out


def px_to_lonlat_oriented(y: float, x: float, nav: AdvancedNav) -> Tuple[float, float]:
    """Inverse of oriented orthographic (planetocentric)."""
    Xsky = (x - nav.xc) / (nav.a_eq_px + 1e-12)
    Ysky = (nav.yc - y) / (nav.b_pol_px + 1e-12)
    pa = deg2rad(nav.north_pa_deg)
    cP, sP = math.cos(pa), math.sin(pa)
    # undo PA
    Xp = Xsky * cP + Ysky * sP
    Yp = -Xsky * sP + Ysky * cP
    rr = Xp * Xp + Yp * Yp
    if rr > 0.999999:
        s = math.sqrt(rr) + 1e-12
        Xp /= s
        Yp /= s
        rr = 0.999999
    Zp = math.sqrt(max(0.0, 1.0 - rr))
    D = deg2rad(nav.sub_lat_deg)
    cD, sD = math.cos(D), math.sin(D)
    # inverse sub-lat rotation
    Ye = Yp * cD + Zp * sD
    Ze = -Yp * sD + Zp * cD
    Xe = Xp
    lon_rel = rad2deg(math.atan2(Xe, Ze))
    lat = rad2deg(math.asin(max(-1.0, min(1.0, Ye))))
    return wrap_deg(nav.cm_iii_deg + lon_rel), lat


# ---------------------------------------------------------------------------
# Multi-scale normalized cross-correlation template (VLBI-grade correlator)
# ---------------------------------------------------------------------------

def _ncc_peak(band: np.ndarray, tmpl: np.ndarray) -> Tuple[float, float, float]:
    """Return subpixel (py, px, peak) via FFT NCC."""
    from numpy.fft import rfft2, irfft2
    th, tw = tmpl.shape
    bh, bw = band.shape
    # zero-mean unit-norm template
    t = tmpl.astype(np.float64)
    t = t - t.mean()
    t = t / (np.sqrt((t * t).sum()) + 1e-12)
    # local energy via box filter of band^2
    b = band.astype(np.float64)
    b0 = b - np.mean(b)
    # correlate
    try:
        from scipy.signal import fftconvolve
        corr = fftconvolve(b0, t[::-1, ::-1], mode="same")
        # local norm of band under template support
        ones = np.ones_like(t)
        local_sum = fftconvolve(b0, ones[::-1, ::-1], mode="same")
        local_sum2 = fftconvolve(b0 * b0, ones[::-1, ::-1], mode="same")
        # for zero-mean patch energy: sum2 - sum^2/n
        n = float(t.size)
        energy = np.sqrt(np.maximum(local_sum2 - (local_sum ** 2) / n, 1e-12))
        ncc = corr / (energy + 1e-12)
    except Exception as e:
        # Never return a zero map (argmax → (0,0) bogus peak). Caller must soft-fail.
        raise RuntimeError(f"NCC correlation failed (need scipy.signal?): {e}") from e
    # mask edges
    ncc[: th // 2 + 1, :] = -1e9
    ncc[-th // 2 - 1 :, :] = -1e9
    ncc[:, : tw // 2 + 1] = -1e9
    ncc[:, -tw // 2 - 1 :] = -1e9
    j = np.unravel_index(np.argmax(ncc), ncc.shape)
    py, px = int(j[0]), int(j[1])
    peak = float(ncc[py, px])
    if not math.isfinite(peak) or peak < -1e8:
        raise RuntimeError("NCC peak invalid after edge mask")

    def sub(p: int, line: np.ndarray) -> float:
        if p <= 0 or p >= len(line) - 1:
            return float(p)
        pm, p0, pp = line[p - 1], line[p], line[p + 1]
        den = pm - 2 * p0 + pp
        return float(p) if abs(den) < 1e-12 else p + 0.5 * (pm - pp) / den

    py_s = sub(py, ncc[:, px])
    px_s = sub(px, ncc[py, :])
    # 2D quadratic refinement on 3x3
    y0, y1 = max(0, py - 1), min(ncc.shape[0], py + 2)
    x0, x1 = max(0, px - 1), min(ncc.shape[1], px + 2)
    patch = ncc[y0:y1, x0:x1]
    if patch.size >= 6 and patch.shape[0] >= 2 and patch.shape[1] >= 2:
        yy, xx = np.mgrid[y0:y1, x0:x1].astype(np.float64)
        w = np.clip(patch - float(np.min(patch)), 0, None) + 1e-9
        s = float(w.sum())
        py_s = float((yy * w).sum() / s)
        px_s = float((xx * w).sum() / s)
    return py_s, px_s, peak


def multiscale_template_match(
    cyl: np.ndarray,
    nav: Any,
    lat0: float = -22.0,
    lengths: Sequence[float] = (9.5, 11.0, 12.0, 13.5, 15.0),
    widths: Sequence[float] = (6.5, 7.5, 8.0, 9.0, 10.0),
) -> Dict[str, float]:
    """
    Multi-scale dark-oval correlator: grid of (L,W) templates, pick max NCC,
    subpixel peak. Primary scientific definition for optical GRS centre.

    Accepts AdvancedNav or precision_engine.NavState (all_methods uses NavState).
    """
    h, w = cyl.shape
    if hasattr(nav, "to_nav_state"):
        nav_s = nav.to_nav_state()
    else:
        nav_s = nav

    def lat_to_y(lat: float) -> int:
        return int(np.clip((90.0 - lat) / 180.0 * (h - 1), 0, h - 1))

    y0 = lat_to_y(lat0 + 14.0)
    y1 = lat_to_y(lat0 - 14.0)
    if y1 < y0:
        y0, y1 = y1, y0
    band = cyl[y0 : y1 + 1, :].copy()
    valid = band > 0
    med = float(np.median(band[valid])) if valid.any() else 0.0
    band = band.copy()
    band[~valid] = med
    # high-pass residual (dark oval rides on SEB)
    band_hp = band - _gauss(band, max(2.0, (y1 - y0) / 18.0))
    # invert so dark → bright for NCC to bright template
    inv = -band_hp

    candidates = []
    for L in lengths:
        for W in widths:
            tw = max(9, int(L / 180.0 * w))
            th = max(7, int(W / 180.0 * h))
            if tw % 2 == 0:
                tw += 1
            if th % 2 == 0:
                th += 1
            yy, xx = np.mgrid[0:th, 0:tw].astype(np.float64)
            cy, cx = (th - 1) / 2.0, (tw - 1) / 2.0
            ell = ((xx - cx) / (tw / 2.0 + 1e-9)) ** 2 + ((yy - cy) / (th / 2.0 + 1e-9)) ** 2
            # dark oval as positive peak on inverted map: bright elliptical blob
            tmpl = np.exp(-0.5 * ell * 2.4)
            tmpl[ell > 1.15] = 0.0
            py, px, peak = _ncc_peak(inv, tmpl)
            lon_rel = -90.0 + (px / max(w - 1, 1)) * 180.0
            lat = 90.0 - ((y0 + py) / max(h - 1, 1)) * 180.0
            # Prior: GRS latitude ≈ -22°, prefer on-disk (not near map edge)
            lat_pen = abs(lat - lat0) / 8.0
            edge_pen = max(0.0, abs(lon_rel) - 70.0) / 10.0
            score = float(peak) - 0.12 * lat_pen - 0.15 * edge_pen
            candidates.append({
                "lon_iii_deg": wrap_deg(nav.cm_iii_deg + lon_rel),
                "lat_deg": float(lat),
                "length_deg": float(L),
                "width_deg": float(W),
                "score": score,
                "ncc": float(peak),
                "method": "multiscale_ncc",
                "map_x": float(px),
                "map_y": float(y0 + py),
            })
    if not candidates:
        t = _template_match_grs(cyl, nav_s)
        t["method"] = "template_fallback"
        return t
    # Prefer candidates in the GRS latitude band
    banded = [c for c in candidates if -34.0 <= c["lat_deg"] <= -12.0]
    pool = banded if banded else candidates
    best = max(pool, key=lambda c: c["score"])
    if best["score"] < 0.05 or not (-36.0 <= best["lat_deg"] <= -10.0):
        t = _template_match_grs(cyl, nav_s)
        t["method"] = "template_fallback_lat"
        return t
    # Cross-check: if classic template disagrees by >8°, trust classic (more stable)
    try:
        tchk = _template_match_grs(cyl, nav_s)
        if abs(wrap_diff(tchk["lon_iii_deg"], best["lon_iii_deg"])) > 8.0:
            if -34 <= tchk["lat_deg"] <= -12:
                tchk = dict(tchk)
                tchk["method"] = "template_override_ncc"
                tchk["ncc_rejected"] = best
                return tchk
    except Exception:
        pass
    return best


def measure_size_isophote(
    cyl: np.ndarray,
    lon_iii: float,
    lat0: float,
    nav: AdvancedNav,
    level_frac: float = 0.45,
) -> Tuple[float, float]:
    """
    Isophote ellipse size around GRS on cylindrical map (degrees).
    More physical than intensity-moment eigenvalues of a tiny blob.
    """
    h, w = cyl.shape
    lon_rel = wrap_diff(lon_iii, nav.cm_iii_deg)
    cx = (lon_rel + 90.0) / 180.0 * (w - 1)
    cy = (90.0 - lat0) / 180.0 * (h - 1)
    # window ~ 20° × 14°
    hx = max(8, int(12.0 / 180.0 * w))
    hy = max(6, int(8.0 / 180.0 * h))
    x0, x1 = int(max(0, cx - hx)), int(min(w, cx + hx + 1))
    y0, y1 = int(max(0, cy - hy)), int(min(h, cy + hy + 1))
    patch = cyl[y0:y1, x0:x1].copy()
    if patch.size < 30:
        return 12.0, 8.0
    valid = patch > 0
    if valid.sum() < 20:
        return 12.0, 8.0
    med = float(np.median(patch[valid]))
    inv = med - patch
    inv[~valid] = 0
    inv[inv < 0] = 0
    if float(inv.max()) <= 0:
        return 12.0, 8.0
    thr = level_frac * float(inv.max())
    m = inv >= thr
    ys, xs = np.where(m)
    if len(xs) < 12:
        return 12.0, 8.0
    # covariance ellipse
    wts = inv[ys, xs]
    s = float(wts.sum()) + 1e-12
    mx = float((xs * wts).sum() / s)
    my = float((ys * wts).sum() / s)
    data = np.stack([(xs - mx) * np.sqrt(wts), (ys - my) * np.sqrt(wts)], axis=0)
    cov = (data @ data.T) / s
    eig = np.linalg.eigvalsh(cov)
    eig = np.sort(np.maximum(eig, 1e-9))[::-1]
    a_px = 2.0 * math.sqrt(eig[0])  # ~2σ
    b_px = 2.0 * math.sqrt(eig[1])
    # map deg per px
    dlon_per_px = 180.0 / max(w - 1, 1)
    dlat_per_px = 180.0 / max(h - 1, 1)
    L = float(np.clip(2 * a_px * dlon_per_px, 5.0, 22.0))
    W = float(np.clip(2 * b_px * dlat_per_px, 3.0, 14.0))
    if L / max(W, 1e-6) > 3.5 or W / max(L, 1e-6) > 2.0:
        return 12.0, 8.0
    return L, W


# ---------------------------------------------------------------------------
# Multi-method consensus (template-locked)
# ---------------------------------------------------------------------------

def measure_grs_vlbi(
    image: np.ndarray,
    nav: AdvancedNav,
    map_width: int = 2880,
    map_height: int = 1440,
    quiet: bool = False,
) -> GRSPrecisionResult:
    """
    VLBI-style correlator primary + multi-method closure for systematics.
    Point estimate is multiscale NCC (locked); others enter scatter only if sane.
    """
    if not quiet:
        CONSOLE.info("VLBI-grade correlator: multi-scale NCC + definition closure")
    im = to_mono(image)
    cyl = make_cylindrical_oriented(im, nav, width=map_width, height=map_height)
    nav_s = nav.to_nav_state()
    methods: Dict[str, Dict[str, float]] = {}
    notes: List[str] = []

    try:
        methods["multiscale_ncc"] = multiscale_template_match(cyl, nav)
    except Exception as e:
        notes.append(f"multiscale failed: {e}")
        methods["multiscale_ncc"] = _template_match_grs(cyl, nav_s)
        methods["multiscale_ncc"]["method"] = "template_fallback"

    try:
        methods["template"] = _template_match_grs(cyl, nav_s)
    except Exception as e:
        notes.append(f"template: {e}")
    try:
        md = _map_dark_centroid(cyl, nav_s)
        if _method_is_sane(md, ref_lon=methods.get("multiscale_ncc", {}).get("lon_iii_deg")):
            methods["map_dark"] = md
        else:
            notes.append("map_dark rejected (pathological)")
    except Exception as e:
        notes.append(f"map_dark: {e}")
    try:
        mo = _moment_mask_grs(im, nav_s)
        if _method_is_sane(mo, ref_lon=methods.get("multiscale_ncc", {}).get("lon_iii_deg")):
            methods["moment"] = mo
        else:
            notes.append("moment rejected (pathological)")
    except Exception as e:
        notes.append(f"moment: {e}")

    primary = methods.get("multiscale_ncc") or methods.get("template")
    if primary is None:
        raise RuntimeError("VLBI measure: no primary correlator result")

    lon = float(primary["lon_iii_deg"])
    lat = float(primary["lat_deg"])
    # light blend toward template if both agree tightly (circular-safe)
    if "template" in methods:
        t = methods["template"]
        if abs(wrap_diff(t["lon_iii_deg"], lon)) < 1.5 and abs(t["lat_deg"] - lat) < 1.5:
            lon = wrap_deg(lon + 0.15 * wrap_diff(t["lon_iii_deg"], lon))
            lat = 0.85 * lat + 0.15 * t["lat_deg"]
            notes.append("soft lock multiscale⊕template (agreement <1.5°)")

    # size from isophotes, not moment blobs
    L, W = measure_size_isophote(cyl, lon, lat, nav)
    if "multiscale_ncc" in methods and 5 <= methods["multiscale_ncc"].get("length_deg", 0) <= 20:
        # average with best template scale from correlator grid
        L = 0.5 * L + 0.5 * float(methods["multiscale_ncc"]["length_deg"])
        W = 0.5 * W + 0.5 * float(methods["multiscale_ncc"]["width_deg"])

    # internal consistency from usable methods near primary
    lons, lats, wts = [], [], []
    weight_map = {
        "multiscale_ncc": 4.0,
        "template": 3.0,
        "map_dark": 1.5,
        "moment": 1.0,
    }
    for name, m in methods.items():
        if abs(wrap_diff(m["lon_iii_deg"], lon)) > 6 or abs(m["lat_deg"] - lat) > 4:
            continue
        if not (-40 <= m["lat_deg"] <= -8):
            continue
        lons.append(m["lon_iii_deg"])
        lats.append(m["lat_deg"])
        wts.append(weight_map.get(name, 1.0))
    if len(lons) >= 2:
        la = np.asarray(lons, dtype=np.float64)
        lb = np.asarray(lats, dtype=np.float64)
        ww = np.asarray(wts, dtype=np.float64)
        dlon = np.array([wrap_diff(x, lon) for x in la])
        err_lon = float(np.sqrt(np.average(dlon ** 2, weights=ww)))
        err_lat = float(np.sqrt(np.average((lb - lat) ** 2, weights=ww)))
    else:
        err_lon, err_lat = 0.15, 0.10

    # nav floor
    deg_per_px = (180.0 / math.pi) / (nav.a_eq_px + 1e-12)
    err_lon = max(err_lon, 0.25 * deg_per_px * math.hypot(nav.sigma_xc, nav.sigma_a * 0.3))
    err_lat = max(err_lat, 0.25 * deg_per_px * math.hypot(nav.sigma_yc, nav.sigma_a * 0.3))

    as_lon = deg_to_arcsec_on_sky(err_lon, km_per_deg_lon(lat), nav.distance_au)
    as_lat = deg_to_arcsec_on_sky(err_lat, km_per_deg_lat(), nav.distance_au)
    as_sky = float(math.hypot(as_lon, as_lat))
    if not quiet:
        CONSOLE.ok(
            f"VLBI measure lon={lon:.5f}° lat={lat:.5f}°  L={L:.2f}° W={W:.2f}°  "
            f"σ_int≈{as_sky:.4f}\"  methods={list(methods.keys())}"
        )
    return GRSPrecisionResult(
        lon_iii_deg=lon,
        lat_deg=lat,
        length_deg=L,
        width_deg=W,
        method="vlbi_multiscale_ncc",
        methods=methods,
        err_lon_deg=err_lon,
        err_lat_deg=err_lat,
        err_sky_arcsec=as_sky,
        err_lon_arcsec=float(as_lon),
        err_lat_arcsec=float(as_lat),
        quality=float(max(0.0, 1.0 - as_sky / 5.0)),
        notes=notes + [
            "Primary: multi-scale normalized cross-correlation on μ-corrected cylindrical map",
            "Size: isophote ellipse on map (moment sizes rejected if pathological)",
            "Internal σ is method scatter only — use hierarchical MC + error budget for total",
        ],
    )


# ---------------------------------------------------------------------------
# Phase-reference analog: injection with SAME science pipeline
# ---------------------------------------------------------------------------

def _local_dark_recover(
    cyl: np.ndarray,
    nav: AdvancedNav,
    lon_hint: float,
    lat_hint: float,
    lon_half_win: float = 10.0,
    lat_half_win: float = 5.0,
) -> Tuple[float, float]:
    """Local dark centroid on cylindrical map (probe recovery fallback)."""
    h, w = cyl.shape

    def lon_to_x(lon_rel: float) -> float:
        return (lon_rel + 90.0) / 180.0 * (w - 1)

    def lat_to_y(lat: float) -> float:
        return (90.0 - lat) / 180.0 * (h - 1)

    lon_rel_hint = wrap_diff(lon_hint, nav.cm_iii_deg)
    x0 = int(np.clip(lon_to_x(lon_rel_hint - lon_half_win), 0, w - 1))
    x1 = int(np.clip(lon_to_x(lon_rel_hint + lon_half_win), 0, w - 1))
    y0 = int(np.clip(lat_to_y(lat_hint + lat_half_win), 0, h - 1))
    y1 = int(np.clip(lat_to_y(lat_hint - lat_half_win), 0, h - 1))
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    if x1 - x0 < 5 or y1 - y0 < 5:
        raise RuntimeError("window too small")
    patch = cyl[y0 : y1 + 1, x0 : x1 + 1].copy()
    valid = patch > 0
    if valid.sum() < 20:
        raise RuntimeError("empty window")
    med = float(np.median(patch[valid]))
    patch[~valid] = med
    inv = med - patch
    inv[inv < 0] = 0
    inv = _gauss(inv, 1.2)
    j = np.unravel_index(np.argmax(inv), inv.shape)
    py, px = int(j[0]), int(j[1])
    y0w, y1w = max(0, py - 3), min(inv.shape[0], py + 4)
    x0w, x1w = max(0, px - 3), min(inv.shape[1], px + 4)
    win = inv[y0w:y1w, x0w:x1w]
    yy, xx = np.mgrid[y0w:y1w, x0w:x1w]
    s = float(win.sum()) + 1e-12
    cy = float((yy * win).sum() / s)
    cx = float((xx * win).sum() / s)
    lon_rel = -90.0 + ((x0 + cx) / max(w - 1, 1)) * 180.0
    lat = 90.0 - ((y0 + cy) / max(h - 1, 1)) * 180.0
    return wrap_deg(nav.cm_iii_deg + lon_rel), float(lat)


def inject_dark_oval_image(
    image: np.ndarray,
    nav: AdvancedNav,
    lon_iii: float,
    lat_deg: float,
    length_deg: float = 11.0,
    width_deg: float = 7.5,
    depth: float = 0.38,
) -> np.ndarray:
    im = to_mono(image).astype(np.float64).copy()
    h, w = im.shape
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    lon_rel = wrap_diff(lon_iii, nav.cm_iii_deg)
    lon_r = math.radians(lon_rel)
    lat_r = math.radians(lat_deg)
    D = deg2rad(nav.sub_lat_deg)
    Xe = math.cos(lat_r) * math.sin(lon_r)
    Ye = math.sin(lat_r)
    Ze = math.cos(lat_r) * math.cos(lon_r)
    cD, sD = math.cos(D), math.sin(D)
    Yp = Ye * cD - Ze * sD
    Zp = Ye * sD + Ze * cD
    if Zp < 0.15 or Xe * Xe + Yp * Yp > 0.90:
        return im  # near limb / back side — skip
    pa = deg2rad(nav.north_pa_deg)
    cP, sP = math.cos(pa), math.sin(pa)
    Xsky = Xe * cP - Yp * sP
    Ysky = Xe * sP + Yp * cP
    cx = nav.xc + Xsky * nav.a_eq_px
    cy = nav.yc - Ysky * nav.b_pol_px
    km_per_px = (2 * JUP_REQ_KM) / (2 * nav.a_eq_px + 1e-12)
    ax = 0.5 * length_deg * km_per_deg_lon(lat_deg) / km_per_px
    by = 0.5 * width_deg * km_per_deg_lat() / km_per_px
    ax = max(ax, 3.0)
    by = max(by, 2.0)
    ell = ((xx - cx) / ax) ** 2 + ((yy - cy) / by) ** 2
    alpha = np.exp(-0.5 * ell * 2.2)
    Xn = (xx - nav.xc) / (nav.a_eq_px + 1e-12)
    Yn = (nav.yc - yy) / (nav.b_pol_px + 1e-12)
    disk = Xn * Xn + Yn * Yn <= 1.0
    local = float(im[disk].mean()) if disk.any() else float(im.mean())
    im = im - depth * local * alpha * disk
    return np.clip(im, 0, None)


def phase_reference_injection(
    image: np.ndarray,
    nav: AdvancedNav,
    grs_lon: float,
    grs_lat: float,
    n_trials: int = 28,
    seed: int = 0,
) -> Tuple[List[Dict[str, float]], float, float, float]:
    """
    VLBI phase-reference analog:
      inject known probes AWAY from real GRS, recover with multiscale science
      correlator (same code path), estimate bias + scatter.
    """
    rng = np.random.default_rng(seed)
    im0 = to_mono(image)
    trials: List[Dict[str, float]] = []
    CONSOLE.info(f"Phase-reference probe suite: N={n_trials} (same-pipeline recovery)")

    for i in range(n_trials):
        ok_place = False
        for _ in range(30):
            dlon = float(rng.choice([-1.0, 1.0]) * rng.uniform(18.0, 48.0))
            true_lon = wrap_deg(grs_lon + dlon)
            true_lat = float(np.clip(grs_lat + rng.uniform(-1.8, 1.8), -30.0, -14.0))
            lon_rel = wrap_diff(true_lon, nav.cm_iii_deg)
            # stay on disk, away from real GRS
            if abs(lon_rel) < 70 and abs(wrap_diff(true_lon, grs_lon)) > 15:
                ok_place = True
                break
        if not ok_place:
            continue

        L = float(rng.uniform(9.5, 14.0))
        W = float(rng.uniform(6.5, 9.5))
        depth = float(rng.uniform(0.32, 0.48))
        inj = inject_dark_oval_image(im0, nav, true_lon, true_lat, L, W, depth)
        try:
            # Recover with SAME multiscale correlator on local map
            cyl = make_cylindrical_oriented(inj, nav, width=2000, height=1000)
            # temporarily null real-GRS band? soft: restrict match lat and prefer near true
            rec = multiscale_template_match(
                cyl, nav, lat0=true_lat,
                lengths=(L * 0.9, L, L * 1.1),
                widths=(W * 0.9, W, W * 1.1),
            )
            # if locked on real GRS, skip
            if abs(wrap_diff(rec["lon_iii_deg"], grs_lon)) < 10 and abs(wrap_diff(true_lon, grs_lon)) > 15:
                # might have stolen real GRS — local inverse peak near truth only
                try:
                    rlon, rlat = _local_dark_recover(cyl, nav, true_lon, true_lat)
                    rec = {"lon_iii_deg": rlon, "lat_deg": rlat}
                except Exception:
                    continue
            dlo = wrap_diff(rec["lon_iii_deg"], true_lon)
            dla = float(rec["lat_deg"] - true_lat)
            if abs(dlo) > 6.0 or abs(dla) > 4.0:
                continue
            sky = sky_error_arcsec(dlo, dla, true_lat, nav.distance_au)
            trials.append({
                "true_lon": true_lon, "true_lat": true_lat,
                "rec_lon": float(rec["lon_iii_deg"]), "rec_lat": float(rec["lat_deg"]),
                "dlon": dlo, "dlat": dla, "sky_arcsec": sky,
            })
        except Exception as e:
            CONSOLE.debug(f"probe {i}: {e}")
            continue
        if (i + 1) % max(1, n_trials // 4) == 0:
            CONSOLE.info(f"  probe progress {i+1}/{n_trials}  ok={len(trials)}")

    if len(trials) < 6:
        CONSOLE.warn(f"Phase-reference weak: only {len(trials)} good probes — bias not applied")
        return trials, 0.0, 0.0, float("nan")

    bias_lon = float(np.mean([t["dlon"] for t in trials]))
    bias_lat = float(np.mean([t["dlat"] for t in trials]))
    # robust: median bias less sensitive to outliers
    bias_lon = float(np.median([t["dlon"] for t in trials]))
    bias_lat = float(np.median([t["dlat"] for t in trials]))
    mean_sky = float(np.mean([t["sky_arcsec"] for t in trials]))
    # Cap pathological bias (broken recovery must not corrupt science)
    if abs(bias_lon) > 2.0 or abs(bias_lat) > 1.5 or mean_sky > 2.5:
        CONSOLE.warn(
            f"Probe bias unphysically large (Δlon={bias_lon:.3f}° Δlat={bias_lat:.3f}° "
            f"mean|err|={mean_sky:.3f}\") — NOT applied"
        )
        return trials, 0.0, 0.0, mean_sky

    CONSOLE.ok(
        f"Phase-ref bias: Δlon={bias_lon:.4f}° Δlat={bias_lat:.4f}°  "
        f"mean|err|={mean_sky:.4f}\"  n={len(trials)}"
    )
    return trials, bias_lon, bias_lat, mean_sky


# ---------------------------------------------------------------------------
# Hierarchical Monte Carlo
# ---------------------------------------------------------------------------

def hierarchical_monte_carlo(
    image: np.ndarray,
    nav: AdvancedNav,
    eph: EphemerisState,
    n_iter: int = 60,
    time_error_seconds: float = 0.0,
    seed: int = 0,
) -> Dict[str, Any]:
    """
    VLBI-style hierarchical error simulation:
      limb jitter → map noise → template scale → CM/time prior.
    """
    n_iter = int(min(max(n_iter, 0), 120))
    if n_iter < 8:
        return {"n_success": 0, "skipped": True}
    rng = np.random.default_rng(seed)
    im0 = to_mono(image)
    cyl0 = make_cylindrical_oriented(im0, nav, width=1600, height=800)
    mask = cyl0 > 0
    residual = cyl0 - _gauss(cyl0, 1.2)
    sigma = float(np.std(residual[mask])) * 0.85 if mask.any() else 0.01
    sigma = max(sigma, 1e-4)
    sig_t = max(float(time_error_seconds), 0.0)
    sig_cm = math.hypot(eph.sigma_cm_deg, time_error_to_lon_sigma(sig_t))

    CONSOLE.info(
        f"Hierarchical MC: N={n_iter}  σ_map={sigma:.4g}  "
        f"σ_cm_prior={sig_cm:.3f}°  limb⊕template⊕time"
    )
    lons: List[float] = []
    lats: List[float] = []
    t0 = time.time()
    lengths = (10.0, 12.0, 14.0)
    widths = (7.0, 8.0, 9.0)

    for i in range(n_iter):
        nav_i = AdvancedNav(
            xc=nav.xc + rng.normal(0, nav.sigma_xc),
            yc=nav.yc + rng.normal(0, nav.sigma_yc),
            a_eq_px=nav.a_eq_px * (1.0 + rng.normal(0, max(nav.sigma_a / max(nav.a_eq_px, 1), 0.0005))),
            flattening=nav.flattening,
            cm_iii_deg=wrap_deg(nav.cm_iii_deg + rng.normal(0, sig_cm * 0.35)),
            distance_au=nav.distance_au * (1.0 + rng.normal(0, eph.sigma_distance_frac * 0.5)),
            sub_lat_deg=nav.sub_lat_deg + rng.normal(0, eph.sigma_sublat_deg * 0.3),
            north_pa_deg=nav.north_pa_deg + rng.normal(0, eph.sigma_pa_deg * 0.3),
            sigma_xc=nav.sigma_xc, sigma_yc=nav.sigma_yc, sigma_a=nav.sigma_a,
        )
        noisy = cyl0 + rng.normal(0, sigma, cyl0.shape)
        # slight nav remap approximation: intensity noise only for speed;
        # centre jitter is in nav_i for lon conversion of peak
        noisy = np.where(mask, noisy, 0.0)
        try:
            # rebuild map under perturbed nav every few iters (expensive)
            if i % 4 == 0:
                cyl_i = make_cylindrical_oriented(im0, nav_i, width=1400, height=700)
                cyl_i = cyl_i + rng.normal(0, sigma * 0.7, cyl_i.shape)
                cyl_i = np.where(cyl_i > 0, cyl_i, 0.0)
            else:
                cyl_i = noisy
                # use nav_i only for CM in conversion — peak on cyl0 frame
            Ls = (float(rng.choice(lengths)),)
            Ws = (float(rng.choice(widths)),)
            # full grid every 5th
            if i % 5 == 0:
                rec = multiscale_template_match(cyl_i, nav_i)
            else:
                rec = multiscale_template_match(cyl_i, nav_i, lengths=Ls, widths=Ws)
            # Absolute System III under perturbed CM is the hierarchical product:
            # when cyl_i was remapped under nav_i, lon already includes CM error.
            # When intensity-only noise on cyl0, peak conversion still uses nav_i.cm
            # so CM jitter remains in the scatter (intended absolute budget term).
            lons.append(float(rec["lon_iii_deg"]))
            lats.append(float(rec["lat_deg"]))
        except Exception:
            continue
        if (i + 1) % max(1, n_iter // 5) == 0:
            CONSOLE.info(f"  H-MC {i+1}/{n_iter} ok={len(lons)}  t={time.time()-t0:.1f}s")

    if len(lons) < 6:
        return {"n_success": len(lons), "n_iter": n_iter, "error": "too few"}

    la = np.asarray(lons, dtype=np.float64)
    lb = np.asarray(lats, dtype=np.float64)
    r = np.deg2rad(la)
    lon_m = wrap_deg(rad2deg(math.atan2(np.sin(r).mean(), np.cos(r).mean())))
    R = math.hypot(np.cos(r).mean(), np.sin(r).mean())
    lon_s = rad2deg(math.sqrt(max(0.0, -2.0 * math.log(max(R, 1e-12)))))
    lat_m = float(np.mean(lb))
    lat_s = float(np.std(lb, ddof=1))
    sky = sky_error_arcsec(lon_s, lat_s, lat_m, nav.distance_au)
    elapsed = time.time() - t0
    CONSOLE.ok(
        f"H-MC DONE {elapsed:.1f}s: σ_lon={lon_s:.4f}°  σ_lat={lat_s:.4f}°  "
        f"σ_sky={sky:.4f}\"  n={len(lons)}"
    )
    return {
        "n_success": len(lons),
        "n_iter": n_iter,
        "elapsed_s": elapsed,
        "mode": "hierarchical_limb_map_template_cm",
        "mean": {"lon_iii_deg": lon_m, "lat_deg": lat_m},
        "std_deg": {"lon_iii_deg": lon_s, "lat_deg": lat_s},
        "std_arcsec": {
            "lon": deg_to_arcsec_on_sky(lon_s, km_per_deg_lon(lat_m), nav.distance_au),
            "lat": deg_to_arcsec_on_sky(lat_s, km_per_deg_lat(), nav.distance_au),
            "sky": sky,
        },
        "target_0_5_arcsec": bool(sky <= 0.5),
        "target_1_arcsec": bool(sky <= 1.0),
        "target_2_arcsec": bool(sky <= 2.0),
    }


# ---------------------------------------------------------------------------
# Definition suite + filter closure
# ---------------------------------------------------------------------------

def definition_suite_vlbi(image: np.ndarray, nav: AdvancedNav) -> List[Dict[str, Any]]:
    im = to_mono(image)
    cyl = make_cylindrical_oriented(im, nav, width=2400, height=1200)
    nav_s = nav.to_nav_state()
    out: List[Dict[str, Any]] = []
    try:
        m = multiscale_template_match(cyl, nav)
        out.append({**m, "name": "multiscale_ncc", "weight": 4.0})
    except Exception as e:
        CONSOLE.debug(f"def ncc: {e}")
    try:
        t = _template_match_grs(cyl, nav_s)
        out.append({**t, "name": "template", "weight": 3.0})
    except Exception:
        pass
    try:
        md = _map_dark_centroid(cyl, nav_s)
        if _method_is_sane(md):
            out.append({**md, "name": "map_dark", "weight": 1.5})
    except Exception:
        pass
    try:
        mo = _moment_mask_grs(im, nav_s)
        if _method_is_sane(mo):
            out.append({**mo, "name": "moment", "weight": 1.0})
    except Exception:
        pass
    try:
        p = measure_grs_vlbi(im, nav, map_width=2000, map_height=1000, quiet=True)
        out.append({
            "name": "engine_vlbi",
            "lon_iii_deg": p.lon_iii_deg,
            "lat_deg": p.lat_deg,
            "length_deg": p.length_deg,
            "width_deg": p.width_deg,
            "weight": 3.5,
            "method": p.method,
        })
    except Exception:
        pass
    return out


def definition_scatter(defs: Sequence[Dict[str, Any]], primary_lon: float, primary_lat: float) -> Tuple[float, float]:
    dlon, dlat, wts = [], [], []
    for d in defs:
        dl = wrap_diff(float(d["lon_iii_deg"]), primary_lon)
        da = float(d["lat_deg"]) - primary_lat
        if abs(dl) > 6 or abs(da) > 4:
            continue
        if not (-40 <= float(d["lat_deg"]) <= -8):
            continue
        dlon.append(dl)
        dlat.append(da)
        wts.append(float(d.get("weight", 1.0)))
    if len(dlon) < 2:
        return 0.12, 0.10
    w = np.asarray(wts, dtype=np.float64)
    w = w / w.sum()
    sys_lon = float(np.sqrt(np.average(np.asarray(dlon) ** 2, weights=w)))
    sys_lat = float(np.sqrt(np.average(np.asarray(dlat) ** 2, weights=w)))
    return min(sys_lon, 2.5), min(sys_lat, 1.5)


def filter_closure_vlbi(channels: Dict[str, np.ndarray], nav: AdvancedNav) -> Optional[Dict[str, Any]]:
    waves = {"R": 620.0, "G": 530.0, "B": 470.0, "IR742": 742.0, "IR685": 685.0}
    avail = [(k, channels[k]) for k in waves if k in channels and channels[k] is not None]
    if len(avail) < 2:
        return None
    measures = []
    for name, img in avail:
        try:
            p = measure_grs_vlbi(img, nav, map_width=1800, map_height=900, quiet=True)
            measures.append({"filter": name, "lam": waves[name], "lon": p.lon_iii_deg, "lat": p.lat_deg})
        except Exception:
            continue
    if len(measures) < 2:
        return None
    pivot = next((m for m in measures if m["filter"] == "G"), measures[0])
    lams = np.array([m["lam"] for m in measures], dtype=np.float64)
    dlons = np.array([wrap_diff(m["lon"], pivot["lon"]) for m in measures], dtype=np.float64)
    dlats = np.array([m["lat"] - pivot["lat"] for m in measures], dtype=np.float64)
    x = 1.0 / (lams ** 2)
    A = np.column_stack([np.ones_like(x), x])
    try:
        coef_lon, *_ = np.linalg.lstsq(A, dlons, rcond=None)
        coef_lat, *_ = np.linalg.lstsq(A, dlats, rcond=None)
        res_lon = dlons - A @ coef_lon
        res_lat = dlats - A @ coef_lat
    except Exception:
        res_lon, res_lat = dlons, dlats
    lat0 = float(np.mean([m["lat"] for m in measures]))
    sys_lon = float(np.std(res_lon, ddof=1)) if len(res_lon) > 1 else float(abs(res_lon[0]))
    sys_lat = float(np.std(res_lat, ddof=1)) if len(res_lat) > 1 else float(abs(res_lat[0]))
    sky = sky_error_arcsec(sys_lon, sys_lat, lat0, nav.distance_au)
    CONSOLE.ok(f"Filter closure (VLBI multi-λ): residual σ_sky≈{sky:.4f}\"")
    return {
        "filters": measures,
        "residual_lon_deg_rms": sys_lon,
        "residual_lat_deg_rms": sys_lat,
        "closure_sky_arcsec": sky,
    }


# ---------------------------------------------------------------------------
# Formal error budget
# ---------------------------------------------------------------------------

def assemble_formal_budget(
    lat_deg: float,
    distance_au: float,
    rand_lon: float,
    rand_lat: float,
    def_lon: float,
    def_lat: float,
    nav: AdvancedNav,
    eph: EphemerisState,
    time_error_seconds: float,
    bias_unc_lon: float,
    bias_unc_lat: float,
    closure_sky: float = 0.0,
) -> ErrorBudget:
    deg_per_px = (180.0 / math.pi) / (nav.a_eq_px + 1e-12)
    # nav centre → lon/lat near equator-ish scale
    nav_lon = deg_per_px * math.hypot(nav.sigma_xc, 0.5 * nav.sigma_a)
    nav_lat = deg_per_px * math.hypot(nav.sigma_yc, 0.5 * nav.sigma_a)
    # near GRS, foreshortening: inflate slightly
    mu_approx = max(0.25, math.cos(deg2rad(wrap_diff(0.0, 0.0))))  # placeholder
    nav_lon /= max(0.35, 0.7)  # conservative
    t_lon = time_error_to_lon_sigma(time_error_seconds)
    eph_lon = float(eph.sigma_cm_deg)

    tot_lon = math.sqrt(
        rand_lon ** 2 + def_lon ** 2 + nav_lon ** 2 + t_lon ** 2 + eph_lon ** 2 + bias_unc_lon ** 2
    )
    tot_lat = math.sqrt(
        rand_lat ** 2 + def_lat ** 2 + nav_lat ** 2 + bias_unc_lat ** 2
    )
    # systematic sky without random
    sys_lon = math.sqrt(def_lon ** 2 + nav_lon ** 2 + t_lon ** 2 + eph_lon ** 2 + bias_unc_lon ** 2)
    sys_lat = math.sqrt(def_lat ** 2 + nav_lat ** 2 + bias_unc_lat ** 2)

    def sky(dl, da):
        return sky_error_arcsec(dl, da, lat_deg, distance_au)

    sig_rand = sky(rand_lon, rand_lat)
    sig_sys = sky(sys_lon, sys_lat)
    sig_tot = float(math.hypot(sig_rand, sig_sys))
    if closure_sky > 0:
        sig_tot = float(math.hypot(sig_tot, 0.5 * closure_sky))

    comps = {
        "random": sig_rand,
        "definition": sky(def_lon, def_lat),
        "navigation": sky(nav_lon, nav_lat),
        "time": sky(t_lon, 0.0),
        "ephemeris_cm": sky(eph_lon, 0.0),
        "bias_uncertainty": sky(bias_unc_lon, bias_unc_lat),
        "filter_closure_half": 0.5 * closure_sky,
        "total": sig_tot,
    }
    return ErrorBudget(
        sigma_random_lon_deg=rand_lon,
        sigma_random_lat_deg=rand_lat,
        sigma_definition_lon_deg=def_lon,
        sigma_definition_lat_deg=def_lat,
        sigma_nav_lon_deg=nav_lon,
        sigma_nav_lat_deg=nav_lat,
        sigma_time_lon_deg=t_lon,
        sigma_ephem_lon_deg=eph_lon,
        sigma_bias_lon_deg=bias_unc_lon,
        sigma_bias_lat_deg=bias_unc_lat,
        sigma_total_lon_deg=tot_lon,
        sigma_total_lat_deg=tot_lat,
        sigma_random_sky_arcsec=sig_rand,
        sigma_systematic_sky_arcsec=sig_sys,
        sigma_total_sky_arcsec=sig_tot,
        components_sky_arcsec=comps,
    )


def optical_diffraction_floor_arcsec(diameter_m: float = 0.35, wavelength_nm: float = 550.0) -> float:
    """λ/D in arcsec — absolute hard floor for a filled aperture (not VLBI baseline)."""
    return 206265.0 * (wavelength_nm * 1e-9) / max(diameter_m, 0.05)


def grade_result(sig_tot: float, inj_mean: float, optical_floor: float) -> str:
    if math.isnan(inj_mean):
        inj_mean = 99.0
    # cannot honestly claim below ~2× optical floor for extended feature
    floor = max(0.15, 2.0 * optical_floor)
    if sig_tot <= max(0.35, floor) and inj_mean <= 0.8:
        return "VLBI_METHOD_EXCELLENT"
    if sig_tot <= 1.0 and inj_mean <= 1.5:
        return "VLBI_METHOD_GOOD"
    if sig_tot <= 2.0:
        return "RESEARCH_GOOD"
    if sig_tot <= 5.0:
        return "RESEARCH_FAIR"
    return "NEEDS_WORK"


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

def run_vlbi_grade(
    image: np.ndarray,
    user_time_iso: str = "",
    time_error_seconds: float = 0.0,
    cm_iii_deg: Optional[float] = None,
    distance_au: Optional[float] = None,
    channels: Optional[Dict[str, np.ndarray]] = None,
    injection_trials: int = 28,
    mc_iter: int = 60,
    seed: int = 0,
    aperture_m: float = 0.35,
    use_horizons: bool = True,
    factory_mode: bool = False,
    nav: Optional[AdvancedNav] = None,
    winjupos_path: Optional[str] = None,
    sub_lat_override: Optional[float] = None,
    north_pa_override: Optional[float] = None,
    use_spice: bool = True,
    use_pro_ephemeris: bool = True,
) -> VLBIResult:
    """
    Full VLBI-inspired optical metrology reduction for one epoch.

    Absolute System III uses professional ephemeris chain when available:
    override → WinJUPOS → SPICE → Horizons full → analytical.
    """
    t0 = time.time()
    im = to_mono(image)
    if factory_mode:
        injection_trials = max(injection_trials, 36)
        mc_iter = max(mc_iter, 80)
        CONSOLE.info("VLBI FACTORY MODE: heavy probe + hierarchical MC suite")

    CONSOLE.info("=" * 64)
    CONSOLE.info("VLBI-INSPIRED OPTICAL METROLOGY (ground-based planetary)")
    CONSOLE.info("Methods: pro-eph · oriented geom · multiscale NCC · phase-ref · H-MC · formal σ")
    CONSOLE.info(f"probes={injection_trials}  H-MC={mc_iter}  aperture={aperture_m}m")

    # Absolute System III requires a real mid-exposure UTC — never wall-clock.
    ut = (user_time_iso or "").strip()
    if not ut:
        raise ValueError(
            "Observation UTC required for VLBI-grade System III "
            "(set mid-exposure time; refusing silent datetime.now())."
        )
    pro_dict: Dict[str, Any] = {}
    if use_pro_ephemeris:
        try:
            from ephemeris_pro import resolve_pro_ephemeris
            pe = resolve_pro_ephemeris(
                ut,
                time_error_seconds=time_error_seconds,
                cm_override=cm_iii_deg,
                distance_override=distance_au,
                sub_lat_override=sub_lat_override,
                north_pa_override=north_pa_override,
                winjupos_path=winjupos_path,
                use_horizons=use_horizons,
                use_spice=use_spice,
            )
            eph = pe.to_vlbi_ephemeris_state()
            # Mark orientation application on EphemerisState via source string
            if pe.apply_orientation:
                eph.source = f"apply_ori|{pe.source}"
            pro_dict = pe.to_dict()
        except Exception as ex:
            CONSOLE.warn(f"Pro ephemeris failed ({ex}); analytical fallback")
            eph = build_ephemeris_approx(
                ut,
                time_error_seconds=time_error_seconds,
                cm_override=cm_iii_deg,
                distance_override=distance_au,
            )
            if use_horizons:
                eph = enrich_ephemeris_from_horizons(eph)
    else:
        eph = build_ephemeris_approx(
            ut,
            time_error_seconds=time_error_seconds,
            cm_override=cm_iii_deg,
            distance_override=distance_au,
        )
        if use_horizons:
            eph = enrich_ephemeris_from_horizons(eph)

    apply_ori = "apply_ori" in str(eph.source) or bool(getattr(eph, "apply_orientation", False))
    if nav is None:
        nav = fit_limb_advanced(
            im, eph, n_rays=900, bootstrap=36, seed=seed, apply_sub_lat=apply_ori,
        )
    else:
        nav.cm_iii_deg = eph.cm_iii_deg
        nav.distance_au = eph.distance_au
        if apply_ori:
            nav.sub_lat_deg = eph.sub_obs_lat_deg
            nav.north_pa_deg = eph.north_pa_deg
        else:
            nav.sub_lat_deg = 0.0
            nav.north_pa_deg = 0.0

    # Primary measure
    primary = measure_grs_vlbi(im, nav, map_width=2880, map_height=1440, quiet=False)
    lon_raw, lat_raw = primary.lon_iii_deg, primary.lat_deg
    L, W = primary.length_deg, primary.width_deg

    # Definitions
    defs = definition_suite_vlbi(im, nav)
    def_lon, def_lat = definition_scatter(defs, lon_raw, lat_raw)
    CONSOLE.ok(f"Definition floor: σ_lon={def_lon:.4f}° σ_lat={def_lat:.4f}°  n={len(defs)}")

    # Phase-reference probes
    trials, bias_lon, bias_lat, inj_mean = phase_reference_injection(
        im, nav, lon_raw, lat_raw, n_trials=injection_trials, seed=seed,
    )
    lon = wrap_deg(lon_raw - bias_lon)
    lat = lat_raw - bias_lat
    # bias uncertainty = SEM of probe residuals
    if len(trials) >= 6:
        bias_unc_lon = float(np.std([t["dlon"] for t in trials], ddof=1) / math.sqrt(len(trials)))
        bias_unc_lat = float(np.std([t["dlat"] for t in trials], ddof=1) / math.sqrt(len(trials)))
        rand_lon = float(np.std([t["dlon"] for t in trials], ddof=1))
        rand_lat = float(np.std([t["dlat"] for t in trials], ddof=1))
    else:
        bias_unc_lon = bias_unc_lat = 0.15
        rand_lon, rand_lat = def_lon, def_lat

    # Hierarchical MC
    hmc = hierarchical_monte_carlo(
        im, nav, eph, n_iter=mc_iter, time_error_seconds=time_error_seconds, seed=seed + 11,
    )
    if hmc.get("std_deg"):
        rand_lon = float(math.sqrt(0.55 * rand_lon ** 2 + 0.45 * hmc["std_deg"]["lon_iii_deg"] ** 2))
        rand_lat = float(math.sqrt(0.55 * rand_lat ** 2 + 0.45 * hmc["std_deg"]["lat_deg"] ** 2))

    # Filter closure
    closure = filter_closure_vlbi(channels, nav) if channels else None
    csky = float(closure["closure_sky_arcsec"]) if closure else 0.0

    budget = assemble_formal_budget(
        lat, nav.distance_au,
        rand_lon, rand_lat, def_lon, def_lat,
        nav, eph, time_error_seconds,
        bias_unc_lon, bias_unc_lat, csky,
    )
    floor = optical_diffraction_floor_arcsec(aperture_m)
    grade = grade_result(budget.sigma_total_sky_arcsec, inj_mean if not math.isnan(inj_mean) else 99.0, floor)
    lat_g = planetocentric_to_planetographic(lat)

    notes = [
        "VLBI-inspired optical metrology — methods, not microarcsecond claims.",
        "Primary centre: multi-scale NCC on μ-corrected oriented cylindrical map.",
        "Phase-reference probes use the same correlator; bias capped if unphysical.",
        "Formal σ = random ⊕ definition ⊕ nav ⊕ time ⊕ ephemeris_CM ⊕ bias_unc (±½ closure).",
        f"Diffraction floor (~λ/D) for {aperture_m}m @550nm ≈ {floor:.3f}\"; extended-feature floor higher.",
        f"image_hash={_hash_array(im)}",
        "For absolute System III publish-grade CM, supply SPICE/WinJUPOS CM override.",
    ]
    if eph.notes:
        notes.extend(eph.notes)
    if closure:
        notes.append(f"Multi-λ closure residual σ_sky≈{csky:.4f}\"")

    elapsed = time.time() - t0
    CONSOLE.ok("=" * 64)
    CONSOLE.ok(
        f"VLBI RESULT: lon={lon:.5f}° lat={lat:.5f}° (pg={lat_g:.5f}°)  "
        f"σ_tot={budget.sigma_total_sky_arcsec:.4f}\"  grade={grade}  ({elapsed:.1f}s)"
    )
    CONSOLE.ok(
        f"  random={budget.sigma_random_sky_arcsec:.4f}\"  "
        f"systematic={budget.sigma_systematic_sky_arcsec:.4f}\"  "
        f"probes={len(trials)} mean|err|={inj_mean}"
    )
    for k, v in budget.components_sky_arcsec.items():
        CONSOLE.info(f"  budget.{k} = {v:.4f}\"")

    return VLBIResult(
        lon_iii_deg=lon,
        lat_deg=lat,
        lat_planetographic_deg=lat_g,
        length_deg=L,
        width_deg=W,
        lon_raw_deg=lon_raw,
        lat_raw_deg=lat_raw,
        bias_lon_deg=bias_lon,
        bias_lat_deg=bias_lat,
        primary_method="vlbi_multiscale_ncc",
        error_budget=budget.to_dict(),
        grade=grade,
        optical_floor_arcsec=floor,
        injection_n=len(trials),
        injection_mean_sky_arcsec=float(inj_mean) if not math.isnan(inj_mean) else float("nan"),
        definition_n=len(defs),
        filter_closure_arcsec=(None if not closure else csky),
        hierarchical_mc=hmc,
        definitions=[{
            "name": d.get("name", d.get("method", "?")),
            "lon_iii_deg": d.get("lon_iii_deg"),
            "lat_deg": d.get("lat_deg"),
            "length_deg": d.get("length_deg"),
            "width_deg": d.get("width_deg"),
            "weight": d.get("weight", 1.0),
        } for d in defs],
        methods={
            "primary": primary.to_dict(),
            "closure": closure,
            "probes": trials,
        },
        ephemeris={**(eph.to_dict()), **({"pro": pro_dict} if pro_dict else {})},
        nav=nav.to_dict(),
        notes=notes,
        elapsed_s=elapsed,
    )


def write_vlbi_bundle(path: Path, result: VLBIResult, extra: Optional[Dict[str, Any]] = None) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = result.to_dict()
    if extra:
        payload["extra"] = extra
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    txt = path.with_suffix(".txt")
    eb = result.error_budget
    lines = [
        "VLBI-INSPIRED OPTICAL GRS METROLOGY REPORT",
        "=" * 56,
        f"Mode: {result.mode}",
        f"Grade: {result.grade}",
        f"Bias-corrected lon III: {result.lon_iii_deg:.6f} deg",
        f"Bias-corrected lat (planetocentric): {result.lat_deg:.6f} deg",
        f"Lat planetographic: {result.lat_planetographic_deg:.6f} deg",
        f"Size L×W: {result.length_deg:.3f} × {result.width_deg:.3f} deg",
        f"Raw lon/lat: {result.lon_raw_deg:.6f} / {result.lat_raw_deg:.6f}",
        f"Probe bias lon/lat: {result.bias_lon_deg:.5f} / {result.bias_lat_deg:.5f}",
        f"Total σ_sky: {eb.get('sigma_total_sky_arcsec', float('nan')):.5f} arcsec",
        f"  random:     {eb.get('sigma_random_sky_arcsec', float('nan')):.5f}",
        f"  systematic: {eb.get('sigma_systematic_sky_arcsec', float('nan')):.5f}",
        f"Optical λ/D floor: {result.optical_floor_arcsec:.4f} arcsec",
        f"Probes: {result.injection_n}  mean|err|: {result.injection_mean_sky_arcsec:.5f} arcsec",
        f"Definitions: {result.definition_n}  closure: {result.filter_closure_arcsec}",
        f"Elapsed: {result.elapsed_s:.1f}s",
        "",
        "BUDGET COMPONENTS (arcsec):",
    ]
    for k, v in (eb.get("components_sky_arcsec") or {}).items():
        lines.append(f"  {k}: {v:.5f}")
    lines += ["", "NOTES:"]
    lines.extend(f"- {n}" for n in result.notes)
    txt.write_text("\n".join(lines), encoding="utf-8")
    CONSOLE.ok(f"VLBI publication bundle: {path.name} + {txt.name}")


def research_grade_compat(result: VLBIResult) -> Dict[str, Any]:
    """Shape compatible with existing UI headline / research_grade fields."""
    eb = result.error_budget
    return {
        "lon_iii_deg": result.lon_raw_deg,
        "lat_deg": result.lat_raw_deg,
        "length_deg": result.length_deg,
        "width_deg": result.width_deg,
        "lon_bias_corrected_deg": result.lon_iii_deg,
        "lat_bias_corrected_deg": result.lat_deg,
        "bias_lon_deg": result.bias_lon_deg,
        "bias_lat_deg": result.bias_lat_deg,
        "sigma_random_sky_arcsec": eb.get("sigma_random_sky_arcsec"),
        "sigma_systematic_sky_arcsec": eb.get("sigma_systematic_sky_arcsec"),
        "sigma_total_sky_arcsec": eb.get("sigma_total_sky_arcsec"),
        "sigma_random_lon_deg": eb.get("sigma_random_lon_deg"),
        "sigma_systematic_lon_deg": eb.get("sigma_definition_lon_deg"),
        "injection_n": result.injection_n,
        "injection_mean_sky_arcsec": result.injection_mean_sky_arcsec,
        "definition_n": result.definition_n,
        "filter_closure_arcsec": result.filter_closure_arcsec,
        "grade": result.grade,
        "methods": result.methods,
        "definitions": result.definitions,
        "injections": result.methods.get("probes", []),
        "notes": result.notes,
        "elapsed_s": result.elapsed_s,
        "vlbi": result.to_dict(),
        "lat_planetographic_deg": result.lat_planetographic_deg,
        "error_budget": result.error_budget,
        "hierarchical_mc": result.hierarchical_mc,
        "optical_floor_arcsec": result.optical_floor_arcsec,
        "primary_method": result.primary_method,
    }

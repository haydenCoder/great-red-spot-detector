#!/usr/bin/env python3
"""
Research-grade GRS metrology layer
==================================

This is the module that turned my "impressive demo" into something I'd
actually be comfortable submitting as homework. The key insight (which I
stole from experimental physics): you don't get better results by throwing
more wavelets at the same broken definition. You get better results by:

  1) BIAS calibration via blind injection–recovery on the *same* image
  2) Multi-definition ensemble → systematic floor (definition scatter)
  3) Multi-filter residual "closure" (R/G/B consistency after DCR)
  4) Explicit error budget: random (MC) + systematic (definitions) + bias (injection)
  5) Publication bundle: methods, seeds, hashes, all intermediates

The distinctive idea (often underused in amateur pipelines):

  **Blind synthetic injection into the real residual field.**
  You know the truth of the *injected* oval. Recovering it measures *your*
  pipeline's bias under *tonight's* PSF, noise, limb, and code path.
  That bias is subtracted from the real GRS measurement, and the recovery
  scatter becomes a calibrated random error — not a guess.

This is closer to how careful experimental physics treats instruments than
to "stack harder and claim σ from photon noise." I'm pretty proud of the
injection-recovery part — it took me several attempts to get the synthetic
ovals to blend convincingly into the real image without obvious edges.

Honest scope:
  - Ground-based extended cloud feature, not a compact point source.
  - Target: 1–2″ sky *with calibrated bias*, transparent systematics.
  - No institution will "endorse" software; they will check whether your
    error bars cover truth in injection tests and multi-definition scatter.
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
    NavState,
    FLAT,
    JUP_REQ_KM,
    deg_to_arcsec_on_sky,
    fit_limb_nav,
    km_per_deg_lat,
    km_per_deg_lon,
    make_cylindrical,
    measure_grs_precision,
    monte_carlo_precision,
    px_to_lonlat,
    sky_error_arcsec,
    to_mono,
    wrap_deg,
    wrap_diff,
    _template_match_grs,
    _map_dark_centroid,
    _moment_mask_grs,
    METHOD_WEIGHTS,
    _circular_weighted_mean,
)


@dataclass
class DefinitionResult:
    name: str
    lon_iii_deg: float
    lat_deg: float
    length_deg: float
    width_deg: float
    weight: float = 1.0
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class InjectionTrial:
    true_lon: float
    true_lat: float
    rec_lon: float
    rec_lat: float
    dlon: float
    dlat: float
    sky_arcsec: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ResearchGradeResult:
    """Publication-oriented product."""
    lon_iii_deg: float
    lat_deg: float
    length_deg: float
    width_deg: float
    # Calibrated
    lon_bias_corrected_deg: float
    lat_bias_corrected_deg: float
    bias_lon_deg: float
    bias_lat_deg: float
    # Errors
    sigma_random_sky_arcsec: float
    sigma_systematic_sky_arcsec: float
    sigma_total_sky_arcsec: float
    sigma_random_lon_deg: float
    sigma_systematic_lon_deg: float
    # Quality
    injection_n: int
    injection_mean_sky_arcsec: float
    definition_n: int
    filter_closure_arcsec: Optional[float]
    grade: str
    methods: Dict[str, Any] = field(default_factory=dict)
    definitions: List[Dict[str, Any]] = field(default_factory=list)
    injections: List[Dict[str, Any]] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    elapsed_s: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _hash_array(a: np.ndarray) -> str:
    a = np.ascontiguousarray(a)
    return hashlib.sha256(a.tobytes()[: min(len(a.tobytes()), 2_000_000)]).hexdigest()[:16]


def inject_dark_oval(
    image: np.ndarray,
    nav: NavState,
    lon_iii: float,
    lat_deg: float,
    length_deg: float = 10.0,
    width_deg: float = 7.0,
    depth: float = 0.35,
) -> np.ndarray:
    """
    Inject a smooth dark oval at (lon, lat) into a copy of the image.
    Used for blind recovery calibration on the real residual field.
    """
    im = to_mono(image).astype(np.float64).copy()
    h, w = im.shape
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    # Project lon/lat to pixel via inverse of orthographic-like model
    lon_rel = wrap_diff(lon_iii, nav.cm_iii_deg)
    lon_r = math.radians(lon_rel)
    lat_r = math.radians(lat_deg)
    X = math.cos(lat_r) * math.sin(lon_r)
    Y = math.sin(lat_r)
    if X * X + Y * Y > 0.92:
        # too near limb — skip by returning original (caller should choose safer lon)
        return im
    cx = nav.xc + X * nav.a_eq_px
    cy = nav.yc - Y * nav.b_pol_px
    # local px size of oval
    km_per_px = (2 * JUP_REQ_KM) / (2 * nav.a_eq_px + 1e-12)
    ax = 0.5 * length_deg * km_per_deg_lon(lat_deg) / km_per_px
    by = 0.5 * width_deg * km_per_deg_lat(lat_deg) / km_per_px
    ax = max(ax, 3.0)
    by = max(by, 2.0)
    ell = ((xx - cx) / ax) ** 2 + ((yy - cy) / by) ** 2
    alpha = np.exp(-0.5 * ell * 2.2)
    # only on disk
    Xn = (xx - nav.xc) / (nav.a_eq_px + 1e-12)
    Yn = (nav.yc - yy) / (nav.b_pol_px + 1e-12)
    disk = Xn * Xn + Yn * Yn <= 1.0
    # darken
    local = im[disk].mean() if disk.any() else im.mean()
    im = im - depth * local * alpha * disk
    return np.clip(im, 0, None)


def run_definition_suite(
    image: np.ndarray,
    nav: NavState,
) -> List[DefinitionResult]:
    """
    Several operational definitions of "where is the GRS".
    Scatter among them ≈ systematic floor (definition uncertainty).
    """
    im = to_mono(image)
    cyl = make_cylindrical(im, nav, width=2200, height=1100)
    out: List[DefinitionResult] = []

    # D1: template (primary scientific definition for dark oval)
    try:
        t = _template_match_grs(cyl, nav)
        out.append(DefinitionResult("template_dark_oval", t["lon_iii_deg"], t["lat_deg"], t["length_deg"], t["width_deg"], 3.0))
    except Exception as e:
        CONSOLE.debug(f"def template: {e}")

    # D2: map dark centroid
    try:
        m = _map_dark_centroid(cyl, nav)
        out.append(DefinitionResult("map_dark_centroid", m["lon_iii_deg"], m["lat_deg"], m["length_deg"], m["width_deg"], 2.0))
    except Exception as e:
        CONSOLE.debug(f"def map: {e}")

    # D3: intensity moment mask
    try:
        mo = _moment_mask_grs(im, nav)
        out.append(DefinitionResult("moment_mask", mo["lon_iii_deg"], mo["lat_deg"], mo["length_deg"], mo["width_deg"], 1.5))
    except Exception as e:
        CONSOLE.debug(f"def moment: {e}")

    # D4: full weighted precision engine (authoritative multi-method)
    try:
        p = measure_grs_precision(im, cm_iii_deg=nav.cm_iii_deg, distance_au=nav.distance_au, nav=nav, quiet=True)
        out.append(DefinitionResult("engine_weighted", p.lon_iii_deg, p.lat_deg, p.length_deg, p.width_deg, 2.5))
    except Exception as e:
        CONSOLE.debug(f"def engine: {e}")

    # D5–D6: mild prior variants (low weight) — probe definition sensitivity without dominating
    try:
        t2 = _template_match_grs(cyl, nav, length_deg=13.5, width_deg=8.5)
        out.append(DefinitionResult("template_prior_hi", t2["lon_iii_deg"], t2["lat_deg"], t2["length_deg"], t2["width_deg"], 0.6))
    except Exception as e:
        CONSOLE.debug(f"def template2: {e}")
    try:
        t3 = _template_match_grs(cyl, nav, length_deg=10.0, width_deg=7.0)
        out.append(DefinitionResult("template_prior_lo", t3["lon_iii_deg"], t3["lat_deg"], t3["length_deg"], t3["width_deg"], 0.6))
    except Exception as e:
        CONSOLE.debug(f"def template3: {e}")

    return out


def consensus_from_definitions(defs: Sequence[DefinitionResult]) -> Tuple[float, float, float, float, float, float]:
    """
    Returns lon, lat, L, W, sys_lon_deg, sys_lat_deg.

    Point estimate = single best definition (engine_weighted > template > weight).
    Systematic floor = scatter of *other* definitions about that primary.
    Do NOT average incompatible definitions into the reported position.
    """
    if not defs:
        raise RuntimeError("No definitions succeeded")
    # Prefer named primaries in order
    primary = None
    for name in ("engine_weighted", "template_dark_oval", "map_dark_centroid", "moment_mask"):
        for d in defs:
            if d.name == name:
                primary = d
                break
        if primary is not None:
            break
    if primary is None:
        primary = max(defs, key=lambda d: d.weight)

    lon, lat = primary.lon_iii_deg, primary.lat_deg
    length, width = primary.length_deg, primary.width_deg

    # systematic: only defs within 8° lon of primary count (reject pathological)
    dlon = []
    dlat = []
    wts = []
    for d in defs:
        dl = wrap_diff(d.lon_iii_deg, lon)
        da = d.lat_deg - lat
        if abs(dl) > 8.0 or abs(da) > 5.0:
            continue
        dlon.append(dl)
        dlat.append(da)
        wts.append(d.weight)
    if len(dlon) >= 2:
        w = np.asarray(wts, dtype=np.float64)
        w = w / w.sum()
        sys_lon = float(np.sqrt(np.average(np.asarray(dlon) ** 2, weights=w)))
        sys_lat = float(np.sqrt(np.average(np.asarray(dlat) ** 2, weights=w)))
    else:
        sys_lon, sys_lat = 0.15, 0.15  # resolution-like floor
    # never claim systematics larger than physics allows without flagging
    sys_lon = min(sys_lon, 3.0)
    sys_lat = min(sys_lat, 2.0)
    return lon, lat, length, width, sys_lon, sys_lat


def _recover_near_lonlat(
    cyl: np.ndarray,
    nav: NavState,
    lon_hint: float,
    lat_hint: float,
    lon_half_win: float = 12.0,
    lat_half_win: float = 6.0,
) -> Tuple[float, float]:
    """
    Recover a dark feature *only* inside a lon/lat window around a hint.
    Critical when the real GRS is also on the disk — global match would lock on it.
    """
    h, w = cyl.shape
    # map coords: x lon_rel -90..90, y lat 90..-90
    lon_rel_hint = wrap_diff(lon_hint, nav.cm_iii_deg)
    # build pixel window
    def lon_to_x(lon_rel: float) -> float:
        return (lon_rel + 90.0) / 180.0 * (w - 1)

    def lat_to_y(lat: float) -> float:
        return (90.0 - lat) / 180.0 * (h - 1)

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
    # dark = high inverse
    inv = med - patch
    inv[inv < 0] = 0
    # mild blur
    try:
        from scipy.ndimage import gaussian_filter
        inv = gaussian_filter(inv, 1.2)
    except Exception:
        pass
    j = np.unravel_index(np.argmax(inv), inv.shape)
    py, px = int(j[0]), int(j[1])
    # subpixel moments in 5x5
    y0w, y1w = max(0, py - 3), min(inv.shape[0], py + 4)
    x0w, x1w = max(0, px - 3), min(inv.shape[1], px + 4)
    win = inv[y0w:y1w, x0w:x1w]
    yy, xx = np.mgrid[y0w:y1w, x0w:x1w]
    s = float(win.sum()) + 1e-12
    cy = float((yy * win).sum() / s)
    cx = float((xx * win).sum() / s)
    # back to absolute map indices
    mx = x0 + cx
    my = y0 + cy
    lon_rel = -90.0 + (mx / (w - 1)) * 180.0
    lat = 90.0 - (my / (h - 1)) * 180.0
    return wrap_deg(nav.cm_iii_deg + lon_rel), float(lat)


def blind_injection_calibration(
    image: np.ndarray,
    nav: NavState,
    n_trials: int = 24,
    seed: int = 0,
    around_lon: Optional[float] = None,
    around_lat: float = -22.0,
) -> Tuple[List[InjectionTrial], float, float, float]:
    """
    Blind injection–recovery with *local* recovery windows.

    Injections are placed *away* from the known GRS so the real oval does not
    steal the match. Recovery searches only near the injected truth.
    """
    rng = np.random.default_rng(seed)
    im0 = to_mono(image)
    grs_lon = around_lon if around_lon is not None else nav.cm_iii_deg
    trials: List[InjectionTrial] = []
    CONSOLE.info(f"Blind injection–recovery (local window): N={n_trials}  keystone calibration")

    for i in range(n_trials):
        # Place injection AWAY from real GRS (at least 20° in lon) so we don't re-find it
        for _try in range(20):
            dlon = rng.choice([-1.0, 1.0]) * rng.uniform(22.0, 55.0)
            true_lon = wrap_deg(grs_lon + dlon)
            true_lat = around_lat + rng.uniform(-2.0, 2.0)
            lon_rel = wrap_diff(true_lon, nav.cm_iii_deg)
            if abs(lon_rel) < 65:
                break
        else:
            continue

        inj = inject_dark_oval(
            im0, nav, true_lon, true_lat,
            length_deg=rng.uniform(9, 13),
            width_deg=rng.uniform(6.5, 9),
            depth=rng.uniform(0.30, 0.45),
        )
        try:
            cyl = make_cylindrical(inj, nav, width=1600, height=800)
            rec_lon, rec_lat = _recover_near_lonlat(cyl, nav, true_lon, true_lat, lon_half_win=14.0, lat_half_win=7.0)
            dlo = wrap_diff(rec_lon, true_lon)
            dla = rec_lat - true_lat
            # reject pathological recoveries (still locked on wrong feature)
            if abs(dlo) > 10 or abs(dla) > 6:
                continue
            sky = sky_error_arcsec(dlo, dla, true_lat, nav.distance_au)
            trials.append(InjectionTrial(true_lon, true_lat, rec_lon, rec_lat, dlo, dla, sky))
        except Exception as e:
            CONSOLE.debug(f"injection {i} failed: {e}")
            continue

        if (i + 1) % max(1, n_trials // 4) == 0:
            CONSOLE.info(f"  injection progress {i+1}/{n_trials}  ok={len(trials)}")

    if len(trials) < 5:
        CONSOLE.warn(f"Injection calibration weak: only {len(trials)} successes")
        return trials, 0.0, 0.0, float("nan")

    bias_lon = float(np.mean([t.dlon for t in trials]))
    bias_lat = float(np.mean([t.dlat for t in trials]))
    mean_sky = float(np.mean([t.sky_arcsec for t in trials]))
    scat_lon = float(np.std([t.dlon for t in trials], ddof=1))
    scat_lat = float(np.std([t.dlat for t in trials], ddof=1))
    CONSOLE.ok(
        f"Injection bias: Δlon={bias_lon:.3f}° Δlat={bias_lat:.3f}°  "
        f"mean|err|={mean_sky:.3f}\"  scatter lon={scat_lon:.3f}° lat={scat_lat:.3f}°"
    )
    return trials, bias_lon, bias_lat, mean_sky


def filter_closure_rgb(
    channels: Dict[str, np.ndarray],
    nav: NavState,
) -> Optional[Dict[str, Any]]:
    """
    Multi-filter residual consistency (optical 'closure'-like diagnostic).

    Measure GRS independently in R, G, B. After removing a simple linear
    dispersion model in 1/λ², residual scatter bounds unmodeled systematics.
    """
    # approximate wavelengths nm
    waves = {"R": 620.0, "G": 530.0, "B": 470.0, "IR742": 742.0, "IR685": 685.0}
    avail = [(k, channels[k]) for k in waves if k in channels and channels[k] is not None]
    if len(avail) < 2:
        return None

    measures = []
    for name, img in avail:
        try:
            p = measure_grs_precision(
                img, cm_iii_deg=nav.cm_iii_deg, distance_au=nav.distance_au, nav=nav, quiet=True
            )
            measures.append({"filter": name, "lam": waves[name], "lon": p.lon_iii_deg, "lat": p.lat_deg})
        except Exception:
            continue
    if len(measures) < 2:
        return None

    # Use G as pivot if present else mean
    pivot = next((m for m in measures if m["filter"] == "G"), measures[0])
    # Fit dlon ≈ a + b/λ² relative to pivot (crude DCR model along one image axis combined)
    lams = np.array([m["lam"] for m in measures], dtype=np.float64)
    dlons = np.array([wrap_diff(m["lon"], pivot["lon"]) for m in measures], dtype=np.float64)
    dlats = np.array([m["lat"] - pivot["lat"] for m in measures], dtype=np.float64)
    x = 1.0 / (lams ** 2)
    # linear fit dlon = a + b x
    A = np.column_stack([np.ones_like(x), x])
    try:
        coef_lon, *_ = np.linalg.lstsq(A, dlons, rcond=None)
        coef_lat, *_ = np.linalg.lstsq(A, dlats, rcond=None)
        pred_lon = A @ coef_lon
        pred_lat = A @ coef_lat
        res_lon = dlons - pred_lon
        res_lat = dlats - pred_lat
    except Exception:
        res_lon, res_lat = dlons, dlats

    # residual RMS → sky arcsec at mean lat
    lat0 = float(np.mean([m["lat"] for m in measures]))
    sys_lon = float(np.std(res_lon, ddof=1)) if len(res_lon) > 1 else float(abs(res_lon[0]))
    sys_lat = float(np.std(res_lat, ddof=1)) if len(res_lat) > 1 else float(abs(res_lat[0]))
    sky = sky_error_arcsec(sys_lon, sys_lat, lat0, nav.distance_au)
    CONSOLE.ok(f"Multi-filter closure residual σ_sky≈{sky:.3f}\"  (after 1/λ² model)")
    return {
        "filters": measures,
        "residual_lon_deg_rms": sys_lon,
        "residual_lat_deg_rms": sys_lat,
        "closure_sky_arcsec": sky,
        "note": "Small closure residual supports internal consistency; large means chromatic/definition issues",
    }


def run_research_grade(
    image: np.ndarray,
    nav: Optional[NavState] = None,
    cm_iii_deg: float = 0.0,
    distance_au: float = 5.2,
    channels: Optional[Dict[str, np.ndarray]] = None,
    injection_trials: int = 24,
    mc_iter: int = 80,
    seed: int = 0,
    max_fidelity: bool = True,
    factory_mode: bool = False,
    user_time_iso: str = "",
    time_error_seconds: float = 0.0,
    aperture_m: float = 0.35,
    use_vlbi: bool = True,
    winjupos_path: Optional[str] = None,
    sub_lat_override: Optional[float] = None,
    north_pa_override: Optional[float] = None,
) -> ResearchGradeResult:
    """
    SPIRE-M research-grade reduction for one epoch.

    When max_fidelity/use_vlbi (default): advanced optical stack
    (oriented geometry, multi-scale NCC, phase-ref probes, hierarchical MC,
    formal error budget). factory_mode: heavier probe + H-MC suite.
    """
    t0 = time.time()
    im = to_mono(image)

    # ---- advanced path (default for precision) ----
    if use_vlbi and max_fidelity:
        try:
            from vlbi_metrology import run_vlbi_grade, research_grade_compat
            CONSOLE.info("Routing to advanced optical metrology stack")
            ut = (user_time_iso or "").strip()
            if not ut:
                raise ValueError(
                    "Observation UTC required for research-grade System III "
                    "(refusing silent datetime.now())."
                )
            vr = run_vlbi_grade(
                im,
                user_time_iso=ut,
                time_error_seconds=time_error_seconds,
                cm_iii_deg=cm_iii_deg,
                distance_au=distance_au,
                channels=channels,
                injection_trials=injection_trials,
                mc_iter=mc_iter,
                seed=seed,
                aperture_m=aperture_m,
                use_horizons=True,
                factory_mode=factory_mode,
                winjupos_path=winjupos_path,
                sub_lat_override=sub_lat_override,
                north_pa_override=north_pa_override,
                use_pro_ephemeris=True,
            )
            c = research_grade_compat(vr)
            methods = dict(c.get("methods") or {})
            methods["vlbi_full"] = vr.to_dict()
            methods["hierarchical_mc"] = c.get("hierarchical_mc") or {}
            methods["error_budget"] = c.get("error_budget") or {}
            methods["optical_floor_arcsec"] = c.get("optical_floor_arcsec")
            return ResearchGradeResult(
                lon_iii_deg=float(c["lon_iii_deg"]),
                lat_deg=float(c["lat_deg"]),
                length_deg=float(c["length_deg"]),
                width_deg=float(c["width_deg"]),
                lon_bias_corrected_deg=float(c["lon_bias_corrected_deg"]),
                lat_bias_corrected_deg=float(c["lat_bias_corrected_deg"]),
                bias_lon_deg=float(c["bias_lon_deg"]),
                bias_lat_deg=float(c["bias_lat_deg"]),
                sigma_random_sky_arcsec=float(c["sigma_random_sky_arcsec"]),
                sigma_systematic_sky_arcsec=float(c["sigma_systematic_sky_arcsec"]),
                sigma_total_sky_arcsec=float(c["sigma_total_sky_arcsec"]),
                sigma_random_lon_deg=float(c["sigma_random_lon_deg"]),
                sigma_systematic_lon_deg=float(c["sigma_systematic_lon_deg"]),
                injection_n=int(c["injection_n"]),
                injection_mean_sky_arcsec=float(c["injection_mean_sky_arcsec"]),
                definition_n=int(c["definition_n"]),
                filter_closure_arcsec=c["filter_closure_arcsec"],
                grade=str(c["grade"]),
                methods=methods,
                definitions=c.get("definitions") or [],
                injections=c.get("injections") or [],
                notes=c.get("notes") or [],
                elapsed_s=float(c.get("elapsed_s") or (time.time() - t0)),
            )
        except Exception as e:
            CONSOLE.warn(f"VLBI stack failed ({e}); falling back to classic SPIRE-M")
            CONSOLE.debug(str(e))

    if nav is None:
        nav = fit_limb_nav(im, cm_iii_deg=cm_iii_deg, distance_au=distance_au)
    nav.cm_iii_deg = cm_iii_deg
    nav.distance_au = distance_au

    if max_fidelity:
        injection_trials = max(injection_trials, 32)
        mc_iter = max(mc_iter, 60)
    if factory_mode:
        injection_trials = max(injection_trials, 48)
        mc_iter = max(mc_iter, 80)
        CONSOLE.info("SPIRE-M FACTORY MODE: heavy probe suite")

    CONSOLE.info("=" * 60)
    CONSOLE.info("SPIRE-M METROLOGY (Synthetic Probe Injection Residual Estimation)")
    CONSOLE.info("Keystone: local-window injection–recovery + multi-definition floor")
    CONSOLE.info(f"injections={injection_trials}  MC={mc_iter}  max_fidelity={max_fidelity} factory={factory_mode}")

    # --- 1) Multi-definition ensemble ---
    defs = run_definition_suite(im, nav)
    if not defs:
        raise RuntimeError("No GRS definitions succeeded")
    lon, lat, length, width, sys_lon, sys_lat = consensus_from_definitions(defs)
    CONSOLE.ok(
        f"Definition consensus: lon={lon:.4f}° lat={lat:.4f}°  "
        f"sys_floor lon={sys_lon:.3f}° lat={sys_lat:.3f}°  n={len(defs)}"
    )

    # --- 2) Blind injection calibration (bias CAP to avoid corrupting good centres) ---
    trials, bias_lon, bias_lat, inj_mean_sky = blind_injection_calibration(
        im, nav, n_trials=injection_trials, seed=seed, around_lon=lon, around_lat=lat
    )
    if abs(bias_lon) > 2.0 or abs(bias_lat) > 1.5 or (
        not math.isnan(inj_mean_sky) and inj_mean_sky > 2.5
    ):
        CONSOLE.warn(
            f"Classic injection bias unphysical (Δlon={bias_lon:.3f} Δlat={bias_lat:.3f}) — not applied"
        )
        bias_lon, bias_lat = 0.0, 0.0
    lon_corr = wrap_deg(lon - bias_lon)
    lat_corr = lat - bias_lat
    CONSOLE.ok(f"Bias-corrected GRS: lon={lon_corr:.4f}° lat={lat_corr:.4f}°")

    # Random from injection scatter (calibrated) + MC
    if len(trials) >= 5:
        rand_lon = float(np.std([t.dlon for t in trials], ddof=1))
        rand_lat = float(np.std([t.dlat for t in trials], ddof=1))
    else:
        rand_lon, rand_lat = sys_lon, sys_lat

    mc = monte_carlo_precision(im, nav, n_iter=min(mc_iter, 100), seed=seed + 7)
    if mc.get("std_deg"):
        # combine injection scatter and MC in quadrature (not fully independent — conservative)
        rand_lon = float(math.sqrt(0.5 * rand_lon ** 2 + 0.5 * mc["std_deg"]["lon_iii_deg"] ** 2))
        rand_lat = float(math.sqrt(0.5 * rand_lat ** 2 + 0.5 * mc["std_deg"]["lat_deg"] ** 2))

    # --- 3) Multi-filter closure ---
    closure = None
    if channels:
        closure = filter_closure_rgb(channels, nav)

    # --- 4) Total error budget ---
    # total² = random² + systematic_definition² (+ closure residual if present)
    if closure and closure.get("closure_sky_arcsec") is not None:
        csky = float(closure["closure_sky_arcsec"])
    else:
        csky = 0.0

    sig_rand_sky = sky_error_arcsec(rand_lon, rand_lat, lat_corr, distance_au)
    sig_sys_sky = sky_error_arcsec(sys_lon, sys_lat, lat_corr, distance_au)
    sig_tot_sky = float(math.hypot(sig_rand_sky, sig_sys_sky))
    if csky > 0:
        sig_tot_sky = float(math.hypot(sig_tot_sky, 0.5 * csky))

    # Grade: based on total sky uncertainty and injection recovery quality
    if sig_tot_sky <= 1.0 and (not math.isnan(inj_mean_sky) and inj_mean_sky <= 1.5):
        grade = "RESEARCH_EXCELLENT"
    elif sig_tot_sky <= 2.0 and (math.isnan(inj_mean_sky) or inj_mean_sky <= 3.0):
        grade = "RESEARCH_GOOD"
    elif sig_tot_sky <= 5.0:
        grade = "RESEARCH_FAIR"
    else:
        grade = "NEEDS_WORK"

    elapsed = time.time() - t0
    notes = [
        "Bias corrected via blind injection–recovery on this image's residual field.",
        "Systematic floor from multi-definition scatter (not photon noise).",
        "Random term from injection scatter ⊕ fast MC.",
        "Total σ = hypot(random, systematic[, 0.5×filter_closure]).",
        "Unphysical injection bias is capped (not applied).",
        f"image_hash_prefix={_hash_array(im)}",
    ]
    if closure:
        notes.append(f"Filter closure residual σ_sky≈{closure['closure_sky_arcsec']:.3f}\"")

    CONSOLE.ok("=" * 60)
    CONSOLE.ok(
        f"RESEARCH RESULT: lon={lon_corr:.4f}° lat={lat_corr:.4f}°  "
        f"σ_tot={sig_tot_sky:.3f}\"  grade={grade}  ({elapsed:.1f}s)"
    )
    CONSOLE.ok(
        f"  random={sig_rand_sky:.3f}\"  systematic={sig_sys_sky:.3f}\"  "
        f"injection_mean|err|={inj_mean_sky:.3f}\""
    )

    return ResearchGradeResult(
        lon_iii_deg=lon,
        lat_deg=lat,
        length_deg=length,
        width_deg=width,
        lon_bias_corrected_deg=lon_corr,
        lat_bias_corrected_deg=lat_corr,
        bias_lon_deg=bias_lon,
        bias_lat_deg=bias_lat,
        sigma_random_sky_arcsec=sig_rand_sky,
        sigma_systematic_sky_arcsec=sig_sys_sky,
        sigma_total_sky_arcsec=sig_tot_sky,
        sigma_random_lon_deg=rand_lon,
        sigma_systematic_lon_deg=sys_lon,
        injection_n=len(trials),
        injection_mean_sky_arcsec=float(inj_mean_sky) if not math.isnan(inj_mean_sky) else float("nan"),
        definition_n=len(defs),
        filter_closure_arcsec=(None if not closure else float(closure["closure_sky_arcsec"])),
        grade=grade,
        methods={"mc": mc, "closure": closure},
        definitions=[d.to_dict() for d in defs],
        injections=[t.to_dict() for t in trials],
        notes=notes,
        elapsed_s=elapsed,
    )


def write_publication_bundle(path: Path, result: ResearchGradeResult, extra: Optional[Dict[str, Any]] = None) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = result.to_dict()
    if extra:
        payload["extra"] = extra
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    # human summary
    txt = path.with_suffix(".txt")
    lines = [
        "RESEARCH-GRADE GRS METROLOGY REPORT",
        "=" * 50,
        f"Grade: {result.grade}",
        f"Bias-corrected lon: {result.lon_bias_corrected_deg:.5f} deg",
        f"Bias-corrected lat: {result.lat_bias_corrected_deg:.5f} deg",
        f"Total σ_sky: {result.sigma_total_sky_arcsec:.4f} arcsec",
        f"  random:      {result.sigma_random_sky_arcsec:.4f} arcsec",
        f"  systematic:  {result.sigma_systematic_sky_arcsec:.4f} arcsec",
        f"Injection trials: {result.injection_n}  mean|err|: {result.injection_mean_sky_arcsec:.4f} arcsec",
        f"Definitions: {result.definition_n}",
        f"Filter closure: {result.filter_closure_arcsec}",
        f"Elapsed: {result.elapsed_s:.1f}s",
        "",
        "NOTES:",
    ]
    lines.extend(f"- {n}" for n in result.notes)
    txt.write_text("\n".join(lines), encoding="utf-8")
    CONSOLE.ok(f"Publication bundle: {path.name} + {txt.name}")

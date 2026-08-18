#!/usr/bin/env python3
"""grs_drift.py — the GRS as a time-series object: longitude drift rate,
zonal velocity vs System II, and transit prediction with honest errors.

WHY THIS MODULE EXISTS
======================
A single measurement of the GRS longitude is trivia; the SAME measurement
over weeks is geophysics — the GRS's drift relative to System II is the
zonal wind at its latitude doing work on a 200-km-tall vortex, and the
JUPOS community tracks exactly this curve. This module turns our (or
JUPOS-imported) per-epoch longitudes into:

  * drift rate (deg/day, deg/30d publishing convention) with sigma-clipped
    weighted least squares and residual-scaled uncertainties,
  * implied zonal velocity in m/s at the GRS latitude (same
    surface_parallel_radius convention as the whole wind chain),
  * curvature test: quadratic vs linear by F-ratio — epochs of genuine
    acceleration exist (the GRS speeds up interacting with SEB
    disturbances), but a quadratic is only reported as preferred when the
    F-test demands it,
  * prediction with a widening 1-sigma cone (parameter covariance), fed to
    transits.py for "when will it cross my meridian next month".

HONEST SCOPE: with amateur cadence (a night per week) the drift rate over
a season is solid; the curvature is usually not — the report's F-test is
what stops us over-claiming. Longitude unwrap assumes gaps < 180 deg of
travel; longer gaps get a loud warning, not a silent guess.
"""
from __future__ import annotations

import csv
import math
import os
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from multi_epoch import weighted_linear_fit


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

@dataclass
class DriftPoint:
    t_utc: datetime
    lon_ii_deg: float
    sigma_deg: float = 1.0
    source: str = ""
    lat_deg: Optional[float] = None      # GRS centre latitude if known

    def to_dict(self) -> Dict[str, Any]:
        return {"t_utc": self.t_utc.isoformat(), "lon_ii_deg": self.lon_ii_deg,
                "sigma_deg": self.sigma_deg, "source": self.source,
                "lat_deg": self.lat_deg}


def _parse_dt(date_s: str, time_s: str) -> Optional[datetime]:
    d = (date_s or "").strip()
    t = (time_s or "").strip()
    if not d:
        return None
    cands = []
    if t:
        cands += [f"{d} {t}", f"{d}T{t}"]
    cands.append(d)
    fmts = ["%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M", "%Y-%m-%d", "%d.%m.%Y %H:%M", "%d.%m.%Y",
            "%Y/%m/%d %H:%M", "%Y/%m/%d"]
    for c in cands:
        for fmt in fmts:
            try:
                return datetime.strptime(c, fmt)
            except ValueError:
                continue
        # decimal-hour JUPOS times like "05.10"? handled as raw hour float
        try:
            return datetime.strptime(d, "%Y-%m-%d")
        except ValueError:
            pass
    return None


def points_from_jupos_csv(path, want_object: Sequence[str] = ("GRS",)
                          ) -> List[DriftPoint]:
    """Import L_II longitude series from a JUPOS-format CSV (our export or
    the community database). Rows without a parseable date or L_II are
    skipped; Object filtered to GRS by default."""
    from jupos_io import read_jupos_csv
    rows = read_jupos_csv(path)
    pts: List[DriftPoint] = []
    want = tuple(w.lower() for w in (want_object or ()))
    for r in rows:
        obj = str(r.get("Object", "") or "")
        if want and obj and not any(w in obj.lower() for w in want):
            continue
        lon = r.get("L_II")
        try:
            lon = float(lon) if lon not in (None, "") else None
        except (TypeError, ValueError):
            lon = None
        dt = _parse_dt(str(r.get("Date", "") or ""), str(r.get("Time", "") or ""))
        if lon is None or dt is None:
            continue
        # JUPOS has no per-point sigma; assign a conservative default so
        # the fit is weighted but honest about unknowns
        pts.append(DriftPoint(t_utc=dt, lon_ii_deg=float(lon) % 360.0,
                              sigma_deg=1.0,
                              source=str(r.get("Observer", "") or "jupos")))
    pts.sort(key=lambda p: p.t_utc)
    return pts


# ---------------------------------------------------------------------------
# Fitting
# ---------------------------------------------------------------------------

@dataclass
class DriftFit:
    t_ref: datetime
    lon0_deg: float
    rate_deg_per_day: float
    sigma_rate_deg_per_day: float
    n_total: int
    n_used: int
    rms_deg: float
    reduced_chi2: float
    clipped: List[int]
    lat_ref_deg: Optional[float]
    quadratic: Optional[Dict[str, float]] = None
    preferred_model: str = "linear"
    unwrap_warning: bool = False

    @property
    def rate_deg_per_30d(self) -> float:
        return self.rate_deg_per_day * 30.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "t_ref": self.t_ref.isoformat(),
            "lon0_deg": self.lon0_deg,
            "rate_deg_per_day": self.rate_deg_per_day,
            "rate_deg_per_30d": self.rate_deg_per_30d,
            "sigma_rate_deg_per_day": self.sigma_rate_deg_per_day,
            "n_used": self.n_used, "n_total": self.n_total,
            "rms_deg": self.rms_deg, "reduced_chi2": self.reduced_chi2,
            "clipped_indices": self.clipped,
            "lat_ref_deg": self.lat_ref_deg,
            "preferred_model": self.preferred_model,
            "quadratic": self.quadratic,
            "unwrap_warning": self.unwrap_warning,
        }


def _design_quadratic(t: np.ndarray) -> np.ndarray:
    return np.column_stack([np.ones_like(t), t, t * t])


def fit_drift(points: Sequence[DriftPoint], clip_sigma: float = 3.0,
              lat_ref_deg: Optional[float] = None) -> DriftFit:
    """Sigma-clipped weighted drift fit on a (sorted) longitude series.

    Longitudes are unwrapped BEFORE fitting (degree-domain unwrap; a gap
    warning is flagged when consecutive points move more than 90 deg, the
    regime where unwrap cannot be trusted). Two rounds of k-sigma clipping
    on standardised residuals; uncertainties residual-scaled via the
    weighted fit (multi_epoch.weighted_linear_fit). A quadratic fit is
    always computed as a control and F-tested against the linear model.
    """
    pts = sorted(points, key=lambda p: p.t_utc)
    if len(pts) < 3:
        raise ValueError("fit_drift: need >= 3 epochs")
    t0 = pts[0].t_utc
    t_days = np.array([(p.t_utc - t0).total_seconds() / 86400.0
                       for p in pts], dtype=np.float64)
    lons = np.array([p.lon_ii_deg for p in pts], dtype=np.float64)
    sig = np.array([max(p.sigma_deg, 1e-3) for p in pts], dtype=np.float64)
    if lat_ref_deg is None:
        lats = [p.lat_deg for p in pts if p.lat_deg is not None]
        lat_ref_deg = float(np.mean(lats)) if lats else None

    unwrap_warning = bool(np.any(np.abs(((np.diff(lons) + 180.0) % 360.0)
                                        - 180.0) > 90.0))
    lon_u = np.rad2deg(np.unwrap(np.deg2rad(lons)))

    keep = np.ones(len(pts), dtype=bool)
    for _ in range(3):
        idx = np.where(keep)[0]
        a, b, sa, sb = weighted_linear_fit(t_days[idx], lon_u[idx],
                                           1.0 / (sig[idx] ** 2))
        resid = lon_u[idx] - (a + b * t_days[idx])
        sc = resid / sig[idx]
        mad = float(np.median(np.abs(sc - np.median(sc))))
        sigma_rob = max(1.4826 * mad, 1e-6)
        out = np.abs(sc - np.median(sc)) > clip_sigma * sigma_rob
        if not out.any():
            break
        keep[idx[out]] = False
    idx = np.where(keep)[0]
    if len(idx) < 3:
        raise ValueError("fit_drift: clipping removed the series")
    a, b, sa, sb = weighted_linear_fit(t_days[idx], lon_u[idx],
                                       1.0 / (sig[idx] ** 2))
    resid = lon_u[idx] - (a + b * t_days[idx])
    rms = float(np.sqrt(np.mean(resid ** 2)))
    w = 1.0 / (sig[idx] ** 2)
    red_chi2 = float(np.sum(w * resid ** 2) / max(len(idx) - 2, 1))

    # quadratic control + F-test: prefer quadratic only if the extra term
    # is demanded by the data (F > 4 ~ the 95% boundary for these dofs)
    quad = None
    preferred = "linear"
    if len(idx) >= 6:
        Aq = _design_quadratic(t_days[idx])
        sw = np.sqrt(np.clip(w, 1e-12, None))
        coef, _, _, _ = np.linalg.lstsq(Aq * sw[:, None], lon_u[idx] * sw,
                                        rcond=None)
        rq = lon_u[idx] - Aq @ coef
        chi2_q = float(np.sum(w * rq ** 2))
        chi2_l = float(np.sum(w * resid ** 2))
        dof_l = len(idx) - 2
        dof_q = len(idx) - 3
        f_stat = ((chi2_l - chi2_q) / max(dof_l - dof_q, 1)) \
            / max(chi2_q / max(dof_q, 1), 1e-12)
        quad = {"a": float(coef[0]), "b": float(coef[1]), "c": float(coef[2]),
                "curvature_deg_per_day2": float(2.0 * coef[2]),
                "reduced_chi2": chi2_q / max(dof_q, 1), "f_stat": float(f_stat)}
        if f_stat > 4.0:
            preferred = "quadratic"

    lon0 = float((a % 360.0))
    return DriftFit(t_ref=t0, lon0_deg=lon0, rate_deg_per_day=float(b),
                    sigma_rate_deg_per_day=float(sb), n_total=len(pts),
                    n_used=len(idx), rms_deg=rms, reduced_chi2=red_chi2,
                    clipped=[int(i) for i in np.where(~keep)[0]],
                    lat_ref_deg=(float(lat_ref_deg) if lat_ref_deg is not None else None),
                    quadratic=quad, preferred_model=preferred,
                    unwrap_warning=unwrap_warning)


def zonal_velocity_mps(planet, fit: DriftFit,
                       lat_deg: Optional[float] = None) -> Optional[Dict[str, float]]:
    """Implied GRS zonal velocity relative to System II at its latitude.

    u = d(lon_II)/dt * (pi/180) * R_parallel(phi) / 86400 in m/s, positive
    PROGRADE (eastward). Uses planet.surface_parallel_radius_m so this is
    exactly consistent with the wind chain's px->deg->m/s conversions.
    """
    la = lat_deg if lat_deg is not None else fit.lat_ref_deg
    if la is None or fit.rate_deg_per_day == 0:
        if la is None:
            return None
    la = float(la)
    k = planet.surface_parallel_radius_m(la) * math.pi / 180.0 / 86400.0
    return {"u_mps": fit.rate_deg_per_day * k,
            "u_std_mps": fit.sigma_rate_deg_per_day * k,
            "lat_deg": la,
            "note": "positive = prograde (eastward) relative to System II"}


def predict(fit: DriftFit, t_utc: datetime,
            points: Optional[Sequence[DriftPoint]] = None
            ) -> Dict[str, float]:
    """Predicted L_II at a future epoch with the 1-sigma prediction cone.

    sigma(t)^2 = sigma_lon0^2 + ((t - t_ref) * sigma_rate)^2 + rms^2 —
    parameter covariance propagated plus the intrinsic scatter (rms); the
    honest statement that long-range predictions inherit weather.
    """
    dt_days = (t_utc - fit.t_ref).total_seconds() / 86400.0
    if fit.preferred_model == "quadratic" and fit.quadratic:
        q = fit.quadratic
        lon_raw = q["a"] + q["b"] * dt_days + q["c"] * dt_days * dt_days
    else:
        lon_raw = fit.lon0_deg + fit.rate_deg_per_day * dt_days
    s = math.sqrt((fit.sigma_rate_deg_per_day * max(dt_days, 0.0)) ** 2
                  + fit.rms_deg ** 2)
    return {"t_utc": t_utc.isoformat(), "lon_ii_deg": float(lon_raw % 360.0),
            "sigma_deg": float(s), "model": fit.preferred_model,
            "days_from_ref": float(dt_days)}


# ---------------------------------------------------------------------------
# Products
# ---------------------------------------------------------------------------

def render_drift_png(points: Sequence[DriftPoint], fit: DriftFit, out_path,
                     horizon_days: int = 45, width: int = 900,
                     height: int = 560) -> str:
    """PIL scatter+fit panel: measured L_II over time (unwrapped), sigma-
    clipped points flagged, fit line, prediction cone over `horizon_days`."""
    from PIL import Image, ImageDraw
    pts = sorted(points, key=lambda p: p.t_utc)
    t0 = fit.t_ref
    t_days = np.array([(p.t_utc - t0).total_seconds() / 86400.0 for p in pts])
    lons = np.array([p.lon_ii_deg for p in pts])
    lon_u = np.rad2deg(np.unwrap(np.deg2rad(lons)))
    img = Image.new("RGB", (width, height), (16, 18, 24))
    d = ImageDraw.Draw(img)
    ml, mr, mt, mb = 80, 40, 46, 60
    pw, ph = width - ml - mr, height - mt - mb
    t_max = float(t_days.max()) + horizon_days
    t_min = float(t_days.min()) - 2.0
    all_lon = list(lon_u)
    fut = np.linspace(float(t_days.max()), t_max, 30)
    if fit.preferred_model == "quadratic" and fit.quadratic:
        q = fit.quadratic
        fut_lon = q["a"] + q["b"] * fut + q["c"] * fut * fut
    else:
        fut_lon = fit.lon0_deg + fit.rate_deg_per_day * fut
    all_lon += list(fut_lon)
    lo, hi = min(all_lon) - 1.5, max(all_lon) + 1.5

    def X(v):
        return ml + (v - t_min) / (t_max - t_min) * pw

    def Y(v):
        return mt + (hi - v) / (hi - lo) * ph

    d.text((ml, 20),
           f"GRS System II drift: {fit.rate_deg_per_30d:+.2f} deg/30d "
           f"[{fit.preferred_model}]", fill=(230, 230, 235))
    # prediction cone
    cone_up, cone_dn = [], []
    for tt, ll in zip(fut, fut_lon):
        s = math.sqrt((fit.sigma_rate_deg_per_day * max(tt, 0)) ** 2
                      + fit.rms_deg ** 2)
        cone_up.append((X(tt), Y(ll + s)))
        cone_dn.append((X(tt), Y(ll - s)))
    if len(cone_up) > 1:
        d.polygon(cone_up + cone_dn[::-1], fill=(45, 55, 75))
    # fit line over history
    tt_h = np.linspace(t_min, float(t_days.max()), 60)
    d.line([(X(tt), Y(fit.lon0_deg + fit.rate_deg_per_day * tt)) for tt in tt_h],
           fill=(110, 170, 240), width=2)
    d.line([(X(tt), Y(ll)) for tt, ll in zip(fut, fut_lon)],
           fill=(110, 170, 240), width=1)
    # points (clipped in red)
    clip = set(fit.clipped)
    for i, (tt, ll) in enumerate(zip(t_days, lon_u)):
        col = (255, 110, 110) if i in clip else (150, 210, 255)
        d.ellipse([X(tt) - 3, Y(ll) - 3, X(tt) + 3, Y(ll) + 3], fill=col)
    # axes
    for g in range(6):
        tv = t_min + (t_max - t_min) * g / 5.0
        d.line([(X(tv), mt), (X(tv), mt + ph)], fill=(36, 40, 52))
        d.text((X(tv) - 12, mt + ph + 8), f"{tv:.0f}d", fill=(160, 165, 180))
    for g in range(5):
        lv = lo + (hi - lo) * g / 4.0
        d.line([(ml, Y(lv)), (ml + pw, Y(lv))], fill=(36, 40, 52))
        d.text((ml - 46, Y(lv) - 6), f"{lv:.1f}", fill=(160, 165, 180))
    d.rectangle([ml, mt, ml + pw, mt + ph], outline=(120, 125, 140))
    os.makedirs(os.path.dirname(os.path.abspath(str(out_path))), exist_ok=True)
    img.save(str(out_path))
    return str(out_path)


def export_drift_csv(points: Sequence[DriftPoint], fit: DriftFit,
                     out_path) -> str:
    pts = sorted(points, key=lambda p: p.t_utc)
    os.makedirs(os.path.dirname(os.path.abspath(str(out_path))), exist_ok=True)
    clip = set(fit.clipped)
    with open(str(out_path), "w", newline="") as f:
        w = csv.writer(f)
        for k in ("t_ref", "lon0_deg", "rate_deg_per_day",
                  "rate_deg_per_30d", "sigma_rate_deg_per_day", "n_used",
                  "n_total", "rms_deg", "reduced_chi2", "preferred_model"):
            w.writerow([f"# {k}: {fit.to_dict()[k]}"])
        w.writerow(["t_utc", "lon_ii_deg", "sigma_deg", "source",
                    "clipped"])
        for i, p in enumerate(pts):
            w.writerow([p.t_utc.isoformat(), f"{p.lon_ii_deg:.4f}",
                        f"{p.sigma_deg:.3f}", p.source,
                        "1" if i in clip else "0"])
    return str(out_path)


def drift_report_text(fit: DriftFit, planet=None) -> str:
    lines = ["=" * 70,
             "GRS DRIFT FIT — System II longitude evolution",
             "=" * 70]
    lines.append(f"reference epoch: {fit.t_ref.isoformat()}   L_II(ref): "
                 f"{fit.lon0_deg:.2f} deg")
    lines.append(f"drift rate: {fit.rate_deg_per_day:+.4f} deg/day "
                 f"= {fit.rate_deg_per_30d:+.2f} deg/30d "
                 f"(sigma {fit.sigma_rate_deg_per_day * 30:.2f} deg/30d)")
    lines.append(f"epochs: {fit.n_used}/{fit.n_total} used "
                 f"({len(fit.clipped)} sigma-clipped)   rms {fit.rms_deg:.2f} deg "
                 f" red. chi2 {fit.reduced_chi2:.2f}")
    if fit.preferred_model == "quadratic" and fit.quadratic:
        q = fit.quadratic
        lines.append(f"QUADRATIC preferred (F={q['f_stat']:.1f}): curvature "
                     f"{q['curvature_deg_per_day2']:+.5f} deg/day^2")
    elif fit.quadratic:
        lines.append(f"quadratic control not significant (F="
                     f"{fit.quadratic['f_stat']:.1f} <= 4) — linear stands")
    if fit.unwrap_warning:
        lines.append("WARNING: >90 deg jumps between epochs — longitude "
                     "unwrap is untrustworthy; split the series")
    if planet is not None:
        z = zonal_velocity_mps(planet, fit)
        if z:
            lines.append(f"implied GRS zonal velocity at {z['lat_deg']:+.1f} deg: "
                         f"{z['u_mps']:+.1f} +- {z['u_std_mps']:.1f} m/s "
                         f"(+ prograde vs System II)")
    return "\n".join(lines)


__all__ = [
    "DriftPoint", "DriftFit", "points_from_jupos_csv", "fit_drift",
    "zonal_velocity_mps", "predict", "render_drift_png",
    "export_drift_csv", "drift_report_text",
]

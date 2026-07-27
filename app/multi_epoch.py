#!/usr/bin/env python3
"""
Multi-epoch differential GRS tracking (VLBI phase-reference across nights)
=========================================================================

Absolute System III on a single night is limited by CM ephemeris zero-point.
**Differentials** across epochs cancel common-mode errors the way VLBI phase
referencing cancels station delays:

  Δlon(t) = lon(t) − lon(t0)   (circular)
  drift model: lon(t) = lon0 + ω·(t−t0) + seasonal terms (optional)
  RTS / Kalman smoother for trajectory under measurement noise

Use cases:
  - GRS drift rate (°/day) for JUPOS-style science
  - Night-to-night residual after derotation (internal consistency)
  - Publication table of bias-corrected epochs with formal σ

API: load epoch JSON list → differential series → drift fit → smooth → report
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import numpy as np

from verbose_log import CONSOLE
from precision_engine import wrap_deg, wrap_diff, sky_error_arcsec, deg_to_arcsec_on_sky, km_per_deg_lon, km_per_deg_lat

try:
    from ephemeris_pro import parse_time, resolve_pro_ephemeris, ProEphemeris
except Exception:
    parse_time = None  # type: ignore


@dataclass
class EpochMeasure:
    """One calibrated GRS measurement."""
    epoch_id: str
    t_utc_iso: str
    lon_iii_deg: float
    lat_deg: float
    length_deg: float = 12.0
    width_deg: float = 8.0
    sigma_lon_deg: float = 0.3
    sigma_lat_deg: float = 0.2
    sigma_sky_arcsec: float = 1.0
    cm_iii_deg: float = float("nan")
    distance_au: float = 5.2
    grade: str = ""
    path: str = ""
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class DifferentialSeries:
    """Phase-referenced differentials relative to reference epoch."""
    ref_epoch_id: str
    ref_t_utc_iso: str
    ref_lon_iii_deg: float
    ref_lat_deg: float
    points: List[Dict[str, Any]] = field(default_factory=list)
    drift_lon_deg_per_day: float = float("nan")
    drift_lat_deg_per_day: float = float("nan")
    drift_lon_sigma: float = float("nan")
    rms_residual_lon_deg: float = float("nan")
    rms_residual_sky_arcsec: float = float("nan")
    smoother: str = "none"
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _t_seconds(iso: str) -> float:
    if parse_time is not None:
        t = parse_time(iso)
    else:
        import datetime as dt
        t = dt.datetime.fromisoformat(iso.replace("Z", "").replace("T", " ")[:19])
    import datetime as dt
    return (t - dt.datetime(2000, 1, 1)).total_seconds()


def epoch_from_research_json(path: Union[str, Path], epoch_id: Optional[str] = None) -> Optional[EpochMeasure]:
    """Ingest research_grade.json / job_result.json / vlbi_metrology.json."""
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    # unwrap common wrappers
    if "research_grade" in data and isinstance(data["research_grade"], dict):
        rg = data["research_grade"]
        headline = data.get("headline") or {}
        t = data.get("user_time") or (data.get("extra") or {}).get("user_time") or ""
    elif "lon_bias_corrected_deg" in data:
        rg = data
        headline = {}
        t = (data.get("extra") or {}).get("user_time") or data.get("t_utc_iso") or ""
    elif "vlbi_full" in (data.get("methods") or {}):
        rg = data
        headline = {}
        t = ""
    else:
        rg = data
        headline = data.get("headline") or {}
        t = data.get("user_time") or ""

    # vlbi_full nested
    vf = None
    if isinstance(rg.get("methods"), dict):
        vf = rg["methods"].get("vlbi_full")
    if vf is None and "lon_iii_deg" in data and "error_budget" in data:
        vf = data

    # Prefer bias-corrected; VLBI export may put corrected under lon_bias_corrected_deg
    # and raw under lon_iii_deg — never invent 0 / −22 as silent defaults.
    def _pick_float(*cands):
        for c in cands:
            if c is None:
                continue
            try:
                v = float(c)
                if math.isfinite(v):
                    return v
            except Exception:
                continue
        return None

    lon = _pick_float(
        headline.get("lon_iii_deg"),
        headline.get("lon_iii_deg_bias_corrected"),
        rg.get("lon_bias_corrected_deg"),
        (vf or {}).get("lon_bias_corrected_deg"),
        (vf or {}).get("lon_iii_deg"),
        rg.get("lon_iii_deg"),
    )
    lat = _pick_float(
        headline.get("lat_deg"),
        headline.get("lat_deg_bias_corrected"),
        rg.get("lat_bias_corrected_deg"),
        (vf or {}).get("lat_deg"),
        rg.get("lat_deg"),
    )
    if lon is None or lat is None:
        return None  # caller skips incomplete epochs
    sig = float(
        headline.get("sigma_total_sky_arcsec")
        or rg.get("sigma_total_sky_arcsec")
        or ((vf or {}).get("error_budget") or {}).get("sigma_total_sky_arcsec")
        or 1.0
    )
    eb = (rg.get("methods") or {}).get("error_budget") or (vf or {}).get("error_budget") or {}
    sig_lon = float(eb.get("sigma_total_lon_deg") or max(0.1, sig / 0.33))
    sig_lat = float(eb.get("sigma_total_lat_deg") or max(0.08, sig / 0.33 * 0.5))
    dist = 5.2
    cm = float("nan")
    if vf and isinstance(vf.get("ephemeris"), dict):
        dist = float(vf["ephemeris"].get("distance_au") or dist)
        cm = float(vf["ephemeris"].get("cm_iii_deg") or float("nan"))
    if not t and vf:
        t = (vf.get("ephemeris") or {}).get("t_utc_iso") or t
    if not t:
        t = path.stem

    return EpochMeasure(
        epoch_id=epoch_id or path.parent.name or path.stem,
        t_utc_iso=str(t),
        lon_iii_deg=lon,
        lat_deg=lat,
        length_deg=float(rg.get("length_deg") or (vf or {}).get("length_deg") or 12.0),
        width_deg=float(rg.get("width_deg") or (vf or {}).get("width_deg") or 8.0),
        sigma_lon_deg=sig_lon,
        sigma_lat_deg=sig_lat,
        sigma_sky_arcsec=sig,
        cm_iii_deg=cm,
        distance_au=dist,
        grade=str(rg.get("grade") or (vf or {}).get("grade") or ""),
        path=str(path),
    )


def load_epochs_from_dir(directory: Union[str, Path]) -> List[EpochMeasure]:
    """Scan job_*/synth_*/ for research_grade.json or vlbi_metrology.json."""
    directory = Path(directory)
    epochs: List[EpochMeasure] = []
    for p in sorted(directory.rglob("research_grade.json")):
        try:
            ep = epoch_from_research_json(p, epoch_id=p.parent.name)
            if ep is not None:
                epochs.append(ep)
        except Exception as e:
            CONSOLE.debug(f"skip {p}: {e}")
    if not epochs:
        for p in sorted(directory.rglob("vlbi_metrology.json")):
            try:
                ep = epoch_from_research_json(p, epoch_id=p.parent.name)
                if ep is not None:
                    epochs.append(ep)
            except Exception as e:
                CONSOLE.debug(f"skip {p}: {e}")
    CONSOLE.info(f"Loaded {len(epochs)} epochs from {directory}")
    return epochs


def load_epochs_from_list(items: Sequence[Dict[str, Any]]) -> List[EpochMeasure]:
    out = []
    for i, it in enumerate(items):
        if "path" in it:
            ep = epoch_from_research_json(it["path"], epoch_id=it.get("epoch_id") or f"e{i}")
            if ep is not None:
                out.append(ep)
        else:
            out.append(EpochMeasure(
                epoch_id=str(it.get("epoch_id") or f"e{i}"),
                t_utc_iso=str(it["t_utc_iso"]),
                lon_iii_deg=float(it["lon_iii_deg"]),
                lat_deg=float(it["lat_deg"]),
                length_deg=float(it.get("length_deg") or 12),
                width_deg=float(it.get("width_deg") or 8),
                sigma_lon_deg=float(it.get("sigma_lon_deg") or 0.3),
                sigma_lat_deg=float(it.get("sigma_lat_deg") or 0.2),
                sigma_sky_arcsec=float(it.get("sigma_sky_arcsec") or 1.0),
                distance_au=float(it.get("distance_au") or 5.2),
            ))
    return out


def weighted_linear_fit(t: np.ndarray, y: np.ndarray, w: np.ndarray) -> Tuple[float, float, float, float]:
    """
    y = a + b t
    Returns a, b, sigma_a, sigma_b (approx).
    """
    t = np.asarray(t, dtype=np.float64)
    y = np.asarray(y, dtype=np.float64)
    w = np.asarray(w, dtype=np.float64)
    w = w / (w.sum() + 1e-12) * len(w)  # normalize mean weight ~1
    A = np.column_stack([np.ones_like(t), t])
    # WLS
    sw = np.sqrt(np.clip(w, 1e-12, None))
    Aw = A * sw[:, None]
    yw = y * sw
    coef, _, _, _ = np.linalg.lstsq(Aw, yw, rcond=None)
    a, b = float(coef[0]), float(coef[1])
    resid = y - (a + b * t)
    dof = max(len(y) - 2, 1)
    chi2 = float(np.sum(w * resid ** 2) / dof)
    # covariance approx
    try:
        cov = chi2 * np.linalg.inv(Aw.T @ Aw)
        sa, sb = float(math.sqrt(max(cov[0, 0], 0))), float(math.sqrt(max(cov[1, 1], 0)))
    except Exception:
        sa, sb = float("nan"), float("nan")
    return a, b, sa, sb


def kalman_rts_1d(
    t: np.ndarray,
    z: np.ndarray,
    r: np.ndarray,
    q_scale: float = 1e-6,
) -> np.ndarray:
    """
    Random-walk + rate state Kalman smoother (RTS).
    State [x, v]; measurement x.
    t in days, z measured lon unwrapped.
    """
    n = len(z)
    if n == 0:
        return z
    x = np.array([z[0], 0.0], dtype=np.float64)
    P = np.diag([r[0] ** 2 + 0.1, 1.0])
    xs = np.zeros((n, 2))
    Ps = np.zeros((n, 2, 2))
    for i in range(n):
        if i > 0:
            dt = max(float(t[i] - t[i - 1]), 1e-9)
            F = np.array([[1.0, dt], [0.0, 1.0]])
            Q = q_scale * np.array([[dt ** 3 / 3, dt ** 2 / 2], [dt ** 2 / 2, dt]]) * max(r[i], 0.05) ** 2
            x = F @ x
            P = F @ P @ F.T + Q
        H = np.array([1.0, 0.0])
        S = float(H @ P @ H + max(r[i], 1e-6) ** 2)
        K = (P @ H) / S
        x = x + K * (z[i] - H @ x)
        P = (np.eye(2) - np.outer(K, H)) @ P
        xs[i] = x
        Ps[i] = P
    # RTS backward
    for i in range(n - 2, -1, -1):
        dt = max(float(t[i + 1] - t[i]), 1e-9)
        F = np.array([[1.0, dt], [0.0, 1.0]])
        Q = q_scale * np.array([[dt ** 3 / 3, dt ** 2 / 2], [dt ** 2 / 2, dt]]) * max(r[i + 1], 0.05) ** 2
        P_pred = F @ Ps[i] @ F.T + Q
        try:
            C = Ps[i] @ F.T @ np.linalg.inv(P_pred)
        except Exception:
            C = np.zeros((2, 2))
        xs[i] = xs[i] + C @ (xs[i + 1] - F @ xs[i])
    return xs[:, 0]


def build_differential_series(
    epochs: Sequence[EpochMeasure],
    ref_index: int = 0,
    smooth: bool = True,
) -> DifferentialSeries:
    """
    Phase-reference all epochs to ref: differentials cancel common CM bias.
    Fit linear drift; optional RTS smoother on unwrapped lon.
    """
    if len(epochs) < 1:
        raise ValueError("Need ≥1 epoch")
    epochs = sorted(epochs, key=lambda e: _t_seconds(e.t_utc_iso))
    ref = epochs[int(np.clip(ref_index, 0, len(epochs) - 1))]
    t0 = _t_seconds(ref.t_utc_iso)
    days = []
    dlon = []
    dlat = []
    wlon = []
    points = []
    for e in epochs:
        d_days = (_t_seconds(e.t_utc_iso) - t0) / 86400.0
        dl = wrap_diff(e.lon_iii_deg, ref.lon_iii_deg)
        da = e.lat_deg - ref.lat_deg
        sky = sky_error_arcsec(dl, da, e.lat_deg, e.distance_au)
        days.append(d_days)
        dlon.append(dl)
        dlat.append(da)
        wlon.append(1.0 / max(e.sigma_lon_deg, 0.05) ** 2)
        points.append({
            "epoch_id": e.epoch_id,
            "t_utc_iso": e.t_utc_iso,
            "days_from_ref": d_days,
            "lon_iii_deg": e.lon_iii_deg,
            "lat_deg": e.lat_deg,
            "dlon_deg": dl,
            "dlat_deg": da,
            "dsky_arcsec": sky,
            "sigma_lon_deg": e.sigma_lon_deg,
            "sigma_lat_deg": e.sigma_lat_deg,
            "sigma_sky_arcsec": e.sigma_sky_arcsec,
            "grade": e.grade,
        })

    td = np.asarray(days, dtype=np.float64)
    yl = np.asarray(dlon, dtype=np.float64)
    ya = np.asarray(dlat, dtype=np.float64)
    wl = np.asarray(wlon, dtype=np.float64)

    drift_lon = drift_lat = sig_b = float("nan")
    rms_lon = float("nan")
    rms_sky = float("nan")
    smoother = "none"

    if len(epochs) >= 2:
        # unwrap lon differentials for fit (already small diffs)
        a, b, sa, sb = weighted_linear_fit(td, yl, wl)
        drift_lon = b  # deg/day
        sig_b = sb
        aa, bb, _, _ = weighted_linear_fit(td, ya, np.ones_like(ya))
        drift_lat = bb
        resid = yl - (a + b * td)
        rms_lon = float(np.sqrt(np.average(resid ** 2, weights=wl)))
        # sky residual from lon+lat residual
        sky_res = [
            sky_error_arcsec(resid[i], ya[i] - (aa + bb * td[i]), epochs[i].lat_deg, epochs[i].distance_au)
            for i in range(len(epochs))
        ]
        rms_sky = float(np.sqrt(np.mean(np.asarray(sky_res) ** 2)))
        for i, p in enumerate(points):
            p["model_dlon_deg"] = float(a + b * td[i])
            p["residual_dlon_deg"] = float(resid[i])
            p["model_dlat_deg"] = float(aa + bb * td[i])

        if smooth and len(epochs) >= 3:
            # absolute lon unwrapped along track
            abs_lon = [epochs[0].lon_iii_deg]
            for i in range(1, len(epochs)):
                abs_lon.append(abs_lon[-1] + wrap_diff(epochs[i].lon_iii_deg, epochs[i - 1].lon_iii_deg))
            z = np.asarray(abs_lon, dtype=np.float64)
            r = np.asarray([e.sigma_lon_deg for e in epochs], dtype=np.float64)
            sm = kalman_rts_1d(td, z, r)
            smoother = "kalman_rts"
            for i, p in enumerate(points):
                p["smoothed_lon_iii_deg"] = wrap_deg(float(sm[i]))
                p["smoothed_dlon_deg"] = wrap_diff(float(sm[i]), ref.lon_iii_deg)

    notes = [
        "Differentials are phase-referenced to the reference epoch (common-mode CM cancels).",
        "Drift is weighted linear fit of Δlon vs days; RTS smoother optional for n≥3.",
        "Absolute System III still requires professional CM on each night for publication lon.",
    ]
    CONSOLE.ok(
        f"Multi-epoch: n={len(epochs)}  drift={drift_lon:.4f}±{sig_b:.4f} °/day  "
        f"RMS resid lon={rms_lon:.4f}°  sky≈{rms_sky:.4f}\"  smooth={smoother}"
    )
    return DifferentialSeries(
        ref_epoch_id=ref.epoch_id,
        ref_t_utc_iso=ref.t_utc_iso,
        ref_lon_iii_deg=ref.lon_iii_deg,
        ref_lat_deg=ref.lat_deg,
        points=points,
        drift_lon_deg_per_day=float(drift_lon),
        drift_lat_deg_per_day=float(drift_lat),
        drift_lon_sigma=float(sig_b),
        rms_residual_lon_deg=float(rms_lon),
        rms_residual_sky_arcsec=float(rms_sky),
        smoother=smoother,
        notes=notes,
    )


def measure_epoch_image(
    image,
    user_time_iso: str,
    time_error_seconds: float = 0.0,
    cm_override: Optional[float] = None,
    winjupos_path: Optional[str] = None,
    **vlbi_kwargs,
) -> EpochMeasure:
    """Run full VLBI-grade measure for one image and package as EpochMeasure."""
    from vlbi_metrology import run_vlbi_grade
    from ephemeris_pro import resolve_pro_ephemeris

    eph = resolve_pro_ephemeris(
        user_time_iso,
        time_error_seconds=time_error_seconds,
        cm_override=cm_override,
        winjupos_path=winjupos_path,
        use_horizons=vlbi_kwargs.pop("use_horizons", True),
    )
    vr = run_vlbi_grade(
        image,
        user_time_iso=user_time_iso,
        time_error_seconds=time_error_seconds,
        cm_iii_deg=eph.cm_iii_deg,
        distance_au=eph.distance_au,
        **vlbi_kwargs,
    )
    eb = vr.error_budget or {}
    return EpochMeasure(
        epoch_id=f"meas_{int(time.time())}",
        t_utc_iso=eph.t_utc_iso,
        lon_iii_deg=vr.lon_iii_deg,
        lat_deg=vr.lat_deg,
        length_deg=vr.length_deg,
        width_deg=vr.width_deg,
        sigma_lon_deg=float(eb.get("sigma_total_lon_deg") or 0.3),
        sigma_lat_deg=float(eb.get("sigma_total_lat_deg") or 0.2),
        sigma_sky_arcsec=float(eb.get("sigma_total_sky_arcsec") or 1.0),
        cm_iii_deg=eph.cm_iii_deg,
        distance_au=eph.distance_au,
        grade=vr.grade,
        notes=["from measure_epoch_image"],
    )


def write_multi_epoch_report(path: Path, series: DifferentialSeries, epochs: Optional[List[EpochMeasure]] = None) -> None:
    path = Path(path)
    payload = series.to_dict()
    if epochs:
        payload["epochs"] = [e.to_dict() for e in epochs]
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    lines = [
        "MULTI-EPOCH DIFFERENTIAL GRS TRACKING",
        "=" * 50,
        f"Reference: {series.ref_epoch_id}  {series.ref_t_utc_iso}",
        f"Ref lon/lat: {series.ref_lon_iii_deg:.5f} / {series.ref_lat_deg:.5f}",
        f"N points: {len(series.points)}",
        f"Drift lon: {series.drift_lon_deg_per_day:.5f} ± {series.drift_lon_sigma:.5f} °/day",
        f"Drift lat: {series.drift_lat_deg_per_day:.5f} °/day",
        f"RMS residual lon: {series.rms_residual_lon_deg:.5f}°",
        f"RMS residual sky: {series.rms_residual_sky_arcsec:.5f}\"",
        f"Smoother: {series.smoother}",
        "",
        "POINTS:",
    ]
    for p in series.points:
        lines.append(
            f"  {p['epoch_id']:20s}  day={p['days_from_ref']:+8.3f}  "
            f"lon={p['lon_iii_deg']:.4f}  Δlon={p['dlon_deg']:+.4f}°  "
            f"Δsky={p['dsky_arcsec']:.3f}\""
        )
    lines += ["", "NOTES:"] + [f"- {n}" for n in series.notes]
    path.with_suffix(".txt").write_text("\n".join(lines), encoding="utf-8")
    CONSOLE.ok(f"Multi-epoch report: {path.name}")

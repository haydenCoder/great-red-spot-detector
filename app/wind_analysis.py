#!/usr/bin/env python3
"""wind_analysis.py — science products from the cloud-tracking wind
measurements that `ap_stacker.wind_report_from_drifts` produces.

WHY THIS MODULE EXISTS
======================
WinJUPOS users reduce drift measurements to study Jupiter's jet streams;
the APS derotator turns every stacked video into just such a measurement.
This module is the ANALYSIS layer on top of the raw per-bin profile:

  * model-discriminated offsets — a uniform m/s advection offset and a
    uniform ANGULAR-rate offset are different physics: the first is a
    whole-atmosphere advection bias, the second is exactly a System-III
    rotation-rate error (u ∝ parallel radius). We fit both and let the
    reduced chi-square say which is present, instead of silently assuming
    one (single-epoch captures cannot separate them within scatter; the
    report says so).
  * jet detection — significant (k-sigma) local extrema of the residual
    profile with parabolic peak refinement; these are unmodelled jets or
    model errors, listed with location/strength, never invented.
  * publication products — a JUPOS-friendly CSV (empty cells where a bin
    has no evidence — never zero-filled) and a PIL-rendered PNG panel
    (profile with 1-sigma error bars, zero line, jets flagged) for the
    desktop/web panels.

HONEST SCOPE: every input comes from ONE capture's AP tracks (or a few
stacked captures). Scatter is MAD-based; systematic fringe aliasing
cannot self-report (see ap_stacker docstring). Claiming a System-III
refinement from a 3-minute video would be nonsense — this module reports
the number with its error bar and the chi-squared, and the caller decides.
"""
from __future__ import annotations

import csv
import math
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np


# ---------------------------------------------------------------------------
# Data access
# ---------------------------------------------------------------------------

def _finite_rows(report: Dict[str, Any]) -> List[Tuple[int, float, float, float]]:
    """(bin_index, abs_lat_deg, residual_mps, std_mps) over bins with
    evidence. Bins the pipeline gated away are SKIPPED (never zero)."""
    out = []
    lats = report.get("bins_abs_lat_deg") or []
    res = report.get("wind_residual_mps_vs_model") or []
    stds = report.get("wind_residual_std_mps") or []
    for i, (la, r, s) in enumerate(zip(lats, res, stds)):
        if r is None or s is None or not (math.isfinite(r) and math.isfinite(s)):
            continue
        out.append((i, float(la), float(r), float(max(s, 1e-9))))
    return out


# ---------------------------------------------------------------------------
# Offset fits: constant m/s advection vs constant angular rate (Sys III)
# ---------------------------------------------------------------------------

def _wmean(vals: np.ndarray, stds: np.ndarray) -> Tuple[float, float, float]:
    w = 1.0 / (stds ** 2)
    mu = float((w * vals).sum() / w.sum())
    sig = float(1.0 / math.sqrt(w.sum()))
    chi2 = float((w * (vals - mu) ** 2).sum())
    return mu, sig, chi2


def fit_uniform_wind_offset(report: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """Fit a single constant zonal-wind offset u0 (m/s, prograde +) across
    all bins: whole-atmosphere advection relative to the literature
    profile. Inverse-variance weighted; returns None with no evidence."""
    rows = _finite_rows(report)
    if len(rows) < 2:
        return None
    res = np.array([r[2] for r in rows])
    stds = np.array([r[3] for r in rows])
    mu, sig, chi2 = _wmean(res, stds)
    return {"u0_mps": mu, "u0_std_mps": sig, "chi2": chi2,
            "reduced_chi2": chi2 / max(len(rows) - 1, 1),
            "n_bins": len(rows)}


def fit_system_iii_offset(planet, report: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """Fit a constant ANGULAR-rate offset (a System-III correction).

    Residual m/s at bin phi converts to angular excess via the parallel
    radius, d_omega = u / R_par(phi); a rotation-rate error predicts
    exactly that shape (u proportional to R_par). Weighted fit in angular
    space; also reported as the implied period correction and the
    m/s-equivalent at the equator (publishing convention).
    """
    rows = _finite_rows(report)
    if len(rows) < 2:
        return None
    # angular excess per bin, deg/day
    doms, stds = [], []
    for _, la, r, s in rows:
        k_m = planet.surface_parallel_radius_m(la)          # metres
        fac = (180.0 / math.pi) * 86400.0 / k_m             # m/s -> deg/day
        doms.append(r * fac)
        stds.append(s * fac)
    mu, sig, chi2 = _wmean(np.array(doms), np.array(stds))
    omega3 = planet.rotation_rate_deg_per_s * 86400.0       # deg/day
    p_s = planet.rotation_period_s
    dp_s = -p_s * mu / omega3                                # period correction
    u_eq = mu * math.pi / 180.0 / 86400.0 \
        * planet.surface_parallel_radius_m(0.0)              # m/s at equator
    return {"d_omega_deg_per_day": mu, "d_omega_std_deg_per_day": sig,
            "chi2": chi2, "reduced_chi2": chi2 / max(len(rows) - 1, 1),
            "n_bins": len(rows),
            "implied_period_correction_s": dp_s,
            "equator_u_equiv_mps": u_eq,
            "note": ("positive d_omega = atmosphere super-rotates vs the "
                     "model System III rate (or the assumed rate is slow); "
                     "period correction = -P * d_omega/omega3")}


# ---------------------------------------------------------------------------
# Jet detection
# ---------------------------------------------------------------------------

@dataclass
class Jet:
    abs_lat_deg: float
    u_mps: float                # signed residual at peak (prograde +)
    std_mps: float
    significance_sigma: float
    direction: str              # "prograde" | "retrograde"
    fwhm_deg: float             # width at half-peak from bin crossings
    bin_index: int

    def to_dict(self) -> Dict[str, Any]:
        return dict(abs_lat_deg=round(self.abs_lat_deg, 3),
                    u_mps=round(self.u_mps, 2),
                    std_mps=round(self.std_mps, 2),
                    significance_sigma=round(self.significance_sigma, 2),
                    direction=self.direction,
                    fwhm_deg=round(self.fwhm_deg, 2),
                    bin_index=self.bin_index)


def detect_jets(report: Dict[str, Any], k_sigma: float = 3.0,
                min_fwhm_deg: float = 0.0) -> List[Jet]:
    """Significant local extrema of the residual wind profile.

    A bin is a jet candidate if |resid| >= k_sigma * std AND it is a
    strict local extremum among adjacent finite bins. Peak latitude and
    amplitude are parabola-refined across the candidate and its two
    neighbours (standard 3-point interpolator); FWHM comes from the bins
    where |resid| crosses half the (signed) peak, linearly interpolated —
    reported in degrees, honestly limited by the bin width. Non-finite
    bins break adjacency (no jets invented across missing evidence).
    """
    rows = _finite_rows(report)
    if len(rows) < 3:
        return []
    lats = report.get("bins_abs_lat_deg") or []
    res_ = report.get("wind_residual_mps_vs_model") or []
    stds_ = report.get("wind_residual_std_mps") or []
    bin_w = float(lats[1] - lats[0]) if len(lats) > 1 else 90.0 / max(len(lats), 1)
    jets: List[Jet] = []
    for k in range(1, len(rows) - 1):
        i_prev, i_cur, i_next = rows[k - 1][0], rows[k][0], rows[k + 1][0]
        if not (i_next == i_cur + 1 and i_cur == i_prev + 1):
            continue                                     # missing evidence gap
        la_c, r_c, s_c = rows[k][1], rows[k][2], rows[k][3]
        r_p, r_n = rows[k - 1][2], rows[k + 1][2]
        if not math.isfinite(r_p) or not math.isfinite(r_n):
            continue
        is_peak = (r_c > r_p and r_c > r_n) or (r_c < r_p and r_c < r_n)
        if not is_peak or s_c <= 0 or abs(r_c) < k_sigma * s_c:
            continue
        # 3-point parabola (equal bin spacing by construction)
        denom = (r_p - 2.0 * r_c + r_n)
        shift = 0.5 * (r_p - r_n) / denom if abs(denom) > 1e-12 else 0.0
        shift = max(-1.0, min(1.0, shift))
        peak_r = r_c - 0.25 * (r_p - r_n) * shift
        peak_la = la_c + shift * bin_w
        half = peak_r / 2.0
        # FWHM: walk outward from the peak while same-sign & above half-peak
        span = 0
        for step in range(1, len(rows)):
            li = i_cur - step
            if li < 0 or res_[li] is None:
                break
            if res_[li] * peak_r > 0 and abs(res_[li]) > abs(half):
                span += 1
            else:
                break
        span_r = 0
        for step in range(1, len(rows)):
            ri = i_cur + step
            if ri >= len(res_) or res_[ri] is None:
                break
            if abs(res_[ri]) > abs(half) and (res_[ri] * peak_r) > 0:
                span_r += 1
            else:
                break
        fwhm = (span + span_r + 1) * bin_w
        jets.append(Jet(abs_lat_deg=float(peak_la), u_mps=float(peak_r),
                        std_mps=float(s_c),
                        significance_sigma=float(abs(peak_r) / s_c),
                        direction="prograde" if peak_r > 0 else "retrograde",
                        fwhm_deg=float(max(fwhm, bin_w)),
                        bin_index=int(i_cur)))
    return jets


# ---------------------------------------------------------------------------
# Reference comparison + summary
# ---------------------------------------------------------------------------

def summarize_profile(planet, report: Dict[str, Any]) -> Dict[str, Any]:
    """One-look summary of a measured wind profile (all numbers from the
    report; nothing recomputed behind different assumptions)."""
    rows = _finite_rows(report)
    n_total = len(report.get("bins_abs_lat_deg") or [])
    out: Dict[str, Any] = {
        "n_bins_total": n_total,
        "n_bins_with_evidence": len(rows),
        "bins_with_evidence_abs_lat_deg": [r[1] for r in rows],
    }
    if rows:
        res = np.array([r[2] for r in rows])
        out["rms_residual_mps"] = float(np.sqrt(np.mean(res ** 2)))
        out["max_abs_residual_mps"] = float(np.abs(res).max())
        # simple correlation with the model rate profile (informational)
        model = report.get("model_rate_deg_per_s") or []
        meas = report.get("measured_rate_deg_per_s") or []
        idx = [r[0] for r in rows]
        if len(model) > max(idx) and len(meas) > max(idx):
            mv = np.array([model[i] for i in idx])
            vv = np.array([meas[i] for i in idx if meas[i] is not None])
            iok = [i for i in idx if meas[i] is not None]
            if len(iok) >= 3:
                mv = np.array([model[i] for i in iok])
                vv = np.array([meas[i] for i in iok])
                mv = (mv - mv.mean())
                vv = vv - vv.mean()
                denom = float(np.sqrt((mv ** 2).sum() * (vv ** 2).sum()))
                if denom > 0:
                    out["rate_correlation_with_model"] = float((mv * vv).sum() / denom)
    u0 = fit_uniform_wind_offset(report)
    dw = fit_system_iii_offset(planet, report)
    out["uniform_wind_fit"] = u0
    out["system_iii_fit"] = dw
    if u0 and dw:
        winner = ("uniform-angular (System-III)"
                  if dw["reduced_chi2"] < u0["reduced_chi2"]
                  else "uniform-m/s advection")
        out["offset_shape_preference"] = winner
        out["offset_shape_note"] = (
            "lower reduced chi-square shape preferred; single-capture "
            "profiles rarely separate these, treat as a diagnostic not a "
            "claim")
    jets = detect_jets(report)
    out["jets"] = [j.to_dict() for j in jets]
    out["n_jets"] = len(jets)
    return out


# ---------------------------------------------------------------------------
# Products: CSV + PNG panel
# ---------------------------------------------------------------------------

def export_profile_csv(report: Dict[str, Any], out_path,
                       summary: Optional[Dict[str, Any]] = None) -> str:
    """JUPOS-friendly per-bin CSV. Empty cells where evidence is absent —
    the community database takes no fabricated zeros."""
    lats = report.get("bins_abs_lat_deg") or []
    keys = [("measured_rate_deg_per_s", "meas_rate_deg_s"),
            ("measured_rate_std_deg_per_s", "meas_rate_std_deg_s"),
            ("model_rate_deg_per_s", "model_rate_deg_s"),
            ("wind_residual_mps_vs_model", "resid_mps"),
            ("wind_residual_std_mps", "resid_std_mps"),
            ("n_evidence_tracks", "n_tracks"),
            ("n_evidence_frames", "n_frames")]
    cols = {k: report.get(k) or [] for k, _ in keys}
    os.makedirs(os.path.dirname(os.path.abspath(str(out_path))), exist_ok=True)
    with open(str(out_path), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if summary:
            w.writerow(["# wind profile analysis summary"])
            for k in ("n_bins_with_evidence", "rms_residual_mps",
                      "max_abs_residual_mps", "offset_shape_preference",
                      "n_jets"):
                if summary.get(k) is not None:
                    w.writerow([f"# {k}: {summary[k]}"])
        w.writerow(["abs_lat_deg"] + [h for _, h in keys])
        for i, la in enumerate(lats):
            row = [f"{la:.3f}"]
            for k, _h in keys:
                v = cols[k][i] if i < len(cols[k]) else None
                row.append("" if v is None else
                           (f"{v:.6g}" if isinstance(v, float) else str(v)))
            w.writerow(row)
    return str(out_path)


def render_profile_png(report: Dict[str, Any], out_path,
                       jets: Optional[List[Jet]] = None,
                       width: int = 640, height: int = 800) -> str:
    """PIL-rendered profile panel (no matplotlib dependency):
    x = residual vs literature model (m/s), y = |latitude| (deg), 1-sigma
    error bars, zero line, jets flagged with markers."""
    from PIL import Image, ImageDraw
    rows = _finite_rows(report)
    img = Image.new("RGB", (width, height), (16, 18, 24))
    d = ImageDraw.Draw(img)
    ml, mr, mt, mb = 90, 40, 50, 70
    pw, ph = width - ml - mr, height - mt - mb
    d.text((ml, 24), "Zonal wind residual vs model (m/s)  — capture-local",
           fill=(230, 230, 235))
    vmax = 10.0
    for _, _, r, s in rows:
        vmax = max(vmax, abs(r) + 2.0 * s)
    vmax *= 1.15
    lat_lo, lat_hi = 0.0, 90.0

    def X(v):
        return ml + (v + vmax) / (2 * vmax) * pw

    def Y(la):
        return mt + (lat_hi - la) / (lat_hi - lat_lo) * ph

    # grid + zero line
    for g in range(-4, 5):
        v = g * vmax / 5.0
        col = (90, 95, 110) if g == 0 else (36, 40, 52)
        if g == 0:
            col = (110, 120, 150)
        d.line([(X(v), mt), (X(v), mt + ph)], fill=col)
        d.text((X(v) - 12, mt + ph + 8), f"{v:.0f}", fill=(160, 165, 180))
    for la in range(0, 91, 15):
        d.line([(ml, Y(la)), (ml + pw, Y(la))], fill=(36, 40, 52))
        d.text((ml - 34, Y(la) - 6), f"{la}", fill=(160, 165, 180))
    d.text((ml - 60, mt + ph // 2), "|lat|", fill=(200, 205, 215))
    d.rectangle([ml, mt, ml + pw, mt + ph], outline=(120, 125, 140))
    # model residual is 0 by definition; measured points with 1-sigma bars
    prev = None
    for _, la, r, s in rows:
        x, y = X(r), Y(la)
        d.line([(X(r - s), y), (X(r + s), y)], fill=(120, 170, 235), width=2)
        d.line([(X(r - s), y - 4), (X(r - s), y + 4)], fill=(120, 170, 235))
        d.line([(X(r + s), y - 4), (X(r + s), y + 4)], fill=(120, 170, 235))
        d.ellipse([x - 3, y - 3, x + 3, y + 3], fill=(150, 200, 255))
        if prev is not None:
            d.line([prev, (x, y)], fill=(80, 120, 180), width=1)
        prev = (x, y)
    for j in (jets or []):
        x, y = X(j.u_mps), Y(j.abs_lat_deg)
        tri = [(x, y - 7), (x - 6, y + 5), (x + 6, y + 5)]
        d.polygon(tri, outline=(255, 205, 90))
        d.text((x + 8, y - 6), f"{j.direction[:4]} {j.u_mps:+.0f}",
               fill=(255, 215, 130))
    os.makedirs(os.path.dirname(os.path.abspath(str(out_path))), exist_ok=True)
    img.save(str(out_path))
    return str(out_path)


def wind_report_text(planet, report: Dict[str, Any]) -> str:
    s = summarize_profile(planet, report)
    lines = ["=" * 70,
             "ZONAL WIND ANALYSIS — cloud-tracking profile vs literature",
             "=" * 70]
    lines.append(f"bins with evidence: {s['n_bins_with_evidence']}/"
                 f"{s['n_bins_total']}")
    if s.get("rms_residual_mps") is not None:
        lines.append(f"rms residual: {s['rms_residual_mps']:.1f} m/s   "
                     f"max |resid|: {s['max_abs_residual_mps']:.1f} m/s")
        if s.get("rate_correlation_with_model") is not None:
            lines.append(f"measured-vs-model rate correlation: "
                         f"{s['rate_correlation_with_model']:.3f}")
    if s.get("uniform_wind_fit"):
        u0 = s["uniform_wind_fit"]
        lines.append(f"uniform advection offset: {u0['u0_mps']:+.1f} +- "
                     f"{u0['u0_std_mps']:.1f} m/s (red. chi2 "
                     f"{u0['reduced_chi2']:.2f})")
    if s.get("system_iii_fit"):
        dw = s["system_iii_fit"]
        lines.append(f"System-III angular offset: {dw['d_omega_deg_per_day']:+.3f} +- "
                     f"{dw['d_omega_std_deg_per_day']:.3f} deg/day "
                     f"(=> period {dw['implied_period_correction_s']:+.2f} s, "
                     f"red. chi2 {dw['reduced_chi2']:.2f})")
    if s.get("offset_shape_preference"):
        lines.append(f"offset shape preference: {s['offset_shape_preference']}")
    if s.get("n_jets"):
        lines.append(f"jets detected: {s['n_jets']}")
        for j in s["jets"]:
            lines.append(f"  {j['direction']:>10s} jet |lat| {j['abs_lat_deg']:.1f} deg:"
                         f" {j['u_mps']:+.1f} m/s ({j['significance_sigma']:.1f} sigma,"
                         f" FWHM {j['fwhm_deg']:.1f} deg)")
    else:
        lines.append("jets detected: none above threshold")
    lines.append("scope: single-capture cloud tracking; scatter is MAD-based; "
                 "systematic fringe aliasing cannot self-report.")
    return "\n".join(lines)


__all__ = [
    "Jet", "summarize_profile", "fit_uniform_wind_offset",
    "fit_system_iii_offset", "detect_jets", "export_profile_csv",
    "render_profile_png", "wind_report_text",
]

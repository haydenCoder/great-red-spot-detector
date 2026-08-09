#!/usr/bin/env python3
"""session_planner.py — physics-derived session planning: how long can one
video be before rotation smears it, how far apart can filter sessions sit,
and when is the GRS on the meridian — one panel, no hand-waving.

WHY THIS MODULE EXISTS
======================
Amateur folklore has fixed numbers ("3 minutes for Jupiter"); the truth is
a formula that depends on your image scale, the latitude you care about,
your smear budget, and whether you derotate. AutoStakkert refuses to model
rotation at all. WinJUPOS gives you the ephemeris but not the budget math.
This module turns `planet_models`' ground-truthed cloud-tracking rates
(the same rates the derotator uses) into:

  * exact smear per capture span (px at YOUR scale, any latitude),
  * maximum video span for a smear budget, raw vs rotation-derotated
    (derotated captures are limited by wind-model residuals, not bulk
    rotation — a very different number, also computed, honestly),
  * filter-wheel session layout: largest gap between filter mid-times for
    a composite budget, and the gap at which rgb_combine's band-polish
    gate (max_resid_px) is the binding constraint,
  * composition with transits.night_planner: the smear math attached to
    tonight's actual GRS/moon windows.

Everything is a closed-form inversion of Planet.lon_drift_px /
Planet.zonal_wind_residual_mps — the tests pin the math against the
planet methods directly, and the ephemeris composition uses the shipped
SPICE-backed transits module.
"""
from __future__ import annotations

import datetime as dt
import math
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np


# ---------------------------------------------------------------------------
# Smear math (exact inversions of the planet model)
# ---------------------------------------------------------------------------

def smear_px(planet, lat_deg: float, a_eq_px: float, span_s: float) -> float:
    """Content displacement (px) at `lat_deg` over `span_s` — the exact
    same chord model the stacker's derotator uses (Planet.lon_drift_px)."""
    return abs(planet.lon_drift_px(float(lat_deg), float(span_s),
                                   float(a_eq_px)))


def max_span_s(planet, lat_deg: float, a_eq_px: float,
               budget_px: float) -> float:
    """Longest capture span whose rotation smear stays under `budget_px`.

    lon_drift_px is exactly linear in dt, so this is a closed form:
        span = budget / (omega_cloud(lat) * px_per_deg_lon(lat))
    (Wind/rotation uncertainty is NOT in this number; it is the raw bulk
    smear bound — see max_span_derotated_s for the derotated regime.)
    """
    rate = planet.cloud_tracking_rate_deg_per_s(float(lat_deg))
    ppd = planet.px_per_deg_lon(float(lat_deg), float(a_eq_px))
    if rate <= 0 or ppd <= 0:
        return float("inf")
    return float(budget_px) / (rate * ppd)


def max_span_derotated_s(planet, lat_deg: float, a_eq_px: float,
                         budget_px: float,
                         wind_uncertainty_mps: float = 30.0) -> float:
    """Longest span after model derotation: then the limiting smear is the
    wind-profile residual u +/- sigma combined with rotation fit wander,
    NOT the bulk rate. span = budget * R_par / (u_resid) in consistent
    units via the same surface_parallel_radius convention as the wind
    chain. wind_uncertainty_mps defaults to a conservative 30 m/s
    (literature profile scatter + temporal variability at amateur scales).
    """
    u = float(wind_uncertainty_mps)
    if u <= 0:
        return float("inf")
    r_par = planet.surface_parallel_radius_m(float(lat_deg))     # metres
    ppd = planet.px_per_deg_lon(float(lat_deg), float(a_eq_px))
    if r_par <= 0 or ppd <= 0:
        return float("inf")
    # content px/s from residual wind: (u/R_par rad/s) -> deg/s -> px
    px_per_s = (u / r_par) * (180.0 / math.pi) * ppd
    if px_per_s <= 0:
        return float("inf")
    return float(budget_px) / px_per_s


def smear_table(planet, a_eq_px: float, budget_px: float = 1.0,
                lats: Sequence[float] = (0, 10, 20, 30, 45, 60)
                ) -> List[Dict[str, float]]:
    """Rows of {abs_lat, rate_deg_h, px_per_deg, max_span_s_raw,
    max_span_s_derotated} across latitudes — the panel content."""
    rows = []
    for la in lats:
        rows.append({
            "abs_lat_deg": float(la),
            "cloud_rate_deg_per_h": planet.cloud_tracking_rate_deg_per_s(la) * 3600.0,
            "px_per_deg": planet.px_per_deg_lon(la, a_eq_px),
            "max_span_raw_s": max_span_s(planet, la, a_eq_px, budget_px),
            "max_span_derotated_s": max_span_derotated_s(
                planet, la, a_eq_px, budget_px),
        })
    return rows


# ---------------------------------------------------------------------------
# Filter-wheel session layout
# ---------------------------------------------------------------------------

@dataclass
class FilterWindowPlan:
    budget_px: float
    lat_of_interest_deg: float
    max_gap_direct_s: float         # direct composite (channels ~aligned)
    max_gap_polish_s: float         # rgb_combine band-polish gate limit
    recommended_gap_s: float        # honest recommendation (min of the two)
    drift_px_per_60s: float
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {"budget_px": self.budget_px,
                "lat_of_interest_deg": self.lat_of_interest_deg,
                "max_gap_direct_s": self.max_gap_direct_s,
                "max_gap_polish_s": self.max_gap_polish_s,
                "recommended_gap_s": self.recommended_gap_s,
                "drift_px_per_60s": self.drift_px_per_60s,
                "notes": self.notes}


def filter_window_plan(planet, a_eq_px: float,
                       lat_of_interest_deg: float = -20.0,
                       budget_px: float = 1.0,
                       polish_gate_px: float = 3.0) -> FilterWindowPlan:
    """How far apart can filter mid-times sit?

    - Direct composite (< budget of inter-channel drift): the naive RGB
      limit, usually seconds-to-a-minute at amateur scales.
    - With rgb_combine rotation derotation: the binding constraint is the
      prior's accuracy vs the band-polish acceptance gate (default 3 px —
      the polish corrects residuals BELOW the gate). Prior error comes
      from the wind table (~m/s-scale truth vs literature), so the polish
      gate gives minutes.
    """
    la = float(lat_of_interest_deg)
    drift_60 = smear_px(planet, la, a_eq_px, 60.0)
    direct = max_span_s(planet, la, a_eq_px, budget_px)
    u_res = 30.0
    px_per_s_resid = (u_res / planet.surface_parallel_radius_m(la)) \
        * (180.0 / math.pi) * planet.px_per_deg_lon(la, a_eq_px)
    polish = float(polish_gate_px) / px_per_s_resid if px_per_s_resid > 0 \
        else float("inf")
    rec = min(polish, 4.0 * direct)             # honest: don't ride the gate
    notes = []
    if direct < 90.0:
        notes.append(f"direct composite needs filters within {direct:.0f} s — "
                     f"unrealistic for manual wheels; plan on rotation "
                     f"derotation (rgb_combine) as the default")
    if rec > 900.0:
        notes.append("gaps beyond ~15 min leave the polish gate idle; large "
                     "gaps are fine for registration but each channel still "
                     "needs its own derotated stack for sharpness")
    notes.append(f"at {drift_60:.2f} px/min at |lat| {abs(la):.0f} deg and "
                 f"{a_eq_px:.0f} px/R_eq scale")
    return FilterWindowPlan(budget_px=float(budget_px),
                            lat_of_interest_deg=la,
                            max_gap_direct_s=float(direct),
                            max_gap_polish_s=float(polish),
                            recommended_gap_s=float(rec),
                            drift_px_per_60s=float(drift_60),
                            notes=notes)


# ---------------------------------------------------------------------------
# Full session plan: physics + tonight's ephemeris
# ---------------------------------------------------------------------------

def session_plan(start_utc: dt.datetime, hours: float,
                 planet=None, a_eq_px: float = 0.0,
                 budget_px: float = 1.0,
                 lat_of_interest_deg: float = -20.0,
                 with_moons: bool = False) -> Dict[str, Any]:
    """One-call panel: smear budgets + session layout + tonight's windows.

    a_eq_px <= 0 is allowed (ephemeris-only plan: smear tables then report
    None, never per-px numbers without a scale)."""
    from planet_models import JUPITER as _JUPITER
    planet = planet or _JUPITER
    plan: Dict[str, Any] = {
        "start_utc": start_utc.isoformat(), "hours": float(hours),
        "planet": planet.name, "a_eq_px": float(a_eq_px),
        "budget_px": float(budget_px),
    }
    have_scale = a_eq_px and a_eq_px > 1.0
    plan["smear_table"] = (smear_table(planet, a_eq_px, budget_px)
                           if have_scale else None)
    plan["filter_plan"] = (filter_window_plan(
        planet, a_eq_px, lat_of_interest_deg, budget_px).to_dict()
        if have_scale else None)
    try:
        import transits
        plan["night"] = transits.night_planner(
            start_utc, days=hours / 24.0,
            moons=("io",) if with_moons else ())
        plan["night_backend"] = transits.moon_backend() if with_moons else None
    except Exception as exc:  # ephemeris is a bonus, never a hard failure
        plan["night"] = None
        plan["night_error"] = f"{type(exc).__name__}: {exc}"
    return plan


def plan_text(plan: Dict[str, Any]) -> str:
    lines = ["=" * 70,
             f"SESSION PLAN — {plan['planet']}   start {plan['start_utc']}",
             "=" * 70]
    if plan.get("smear_table"):
        lines.append(f"smear budget {plan['budget_px']:.1f} px at a_eq "
                     f"{plan['a_eq_px']:.0f} px:")
        lines.append("  |lat|  rate(deg/h) px/deg  max span (raw)  max span (derot)")
        for r in plan["smear_table"]:
            lines.append(f"  {r['abs_lat_deg']:5.0f}  {r['cloud_rate_deg_per_h']:10.2f}"
                         f" {r['px_per_deg']:7.3f}  {r['max_span_raw_s']:11.0f}s  "
                         f"{r['max_span_derotated_s']:12.0f}s")
    fp = plan.get("filter_plan")
    if fp:
        lines.append(f"filter session: direct-composite gap <= "
                     f"{fp['max_gap_direct_s']:.0f} s; with rotation-derotated "
                     f"composite gaps up to ~{fp['max_gap_polish_s'] / 60.0:.0f} min "
                     f"(recommended <= {fp['recommended_gap_s']:.0f} s)")
    night = plan.get("night")
    if night:
        gt = night.get("grs_transits") or []
        lines.append(f"GRS transits this window: {len(gt)}")
        for g in gt[:4]:
            t_ = g.get("t_utc") or g.get("utc") or str(g)[:40]
            lines.append(f"  transit {t_}")
        wins = night.get("grs_windows") or night.get("visibility_windows") or []
        for w_ in wins[:3]:
            if isinstance(w_, dict):
                lines.append(f"  GRS visible {w_.get('start_utc', '?')} -> "
                             f"{w_.get('end_utc', '?')}")
    elif plan.get("night_error"):
        lines.append(f"(ephemeris unavailable: {plan['night_error']})")
    return "\n".join(lines)


def render_plan_png(plan: Dict[str, Any], out_path,
                    width: int = 760, height: int = 560) -> str:
    """One-glance session panel: smear table + filter advice + windows."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (width, height), (16, 18, 24))
    d = ImageDraw.Draw(img)
    y = 18
    d.text((24, y), f"SESSION PLAN — {plan.get('planet', '')}  "
                    f"{plan.get('start_utc', '')}", fill=(230, 230, 235))
    y += 30
    if plan.get("smear_table"):
        d.text((24, y), f"smear budget {plan['budget_px']:.1f} px @ "
                        f"{plan['a_eq_px']:.0f} px/Req", fill=(160, 165, 180))
        y += 22
        d.text((24, y), "|lat|   px/deg   raw span   derot span",
               fill=(200, 205, 215))
        y += 18
        for r in plan["smear_table"]:
            d.text((24, y),
                   f"{r['abs_lat_deg']:5.0f}  {r['px_per_deg']:7.3f}"
                   f" {r['max_span_raw_s']:9.0f}s  {r['max_span_derotated_s']:9.0f}s",
                   fill=(150, 200, 255))
            y += 16
        y += 10
    fp = plan.get("filter_plan")
    if fp:
        d.text((24, y), "filter session", fill=(200, 205, 215))
        y += 18
        d.text((24, y),
               f"direct <= {fp['max_gap_direct_s']:.0f} s   "
               f"derot composite <= {fp['max_gap_polish_s'] / 60.0:.0f} min   "
               f"drift {fp['drift_px_per_60s']:.2f} px/min",
               fill=(255, 215, 130))
        y += 26
    night = plan.get("night")
    if night:
        d.text((24, y), "tonight", fill=(200, 205, 215))
        y += 18
        for g in (night.get("grs_transits") or [])[:4]:
            d.text((24, y), f"GRS transit {g.get('t_utc', g)}",
                   fill=(150, 210, 160))
            y += 16
        if not (night.get("grs_transits") or []):
            d.text((24, y), "no GRS transit in window", fill=(150, 160, 175))
            y += 16
    os.makedirs(os.path.dirname(os.path.abspath(str(out_path))), exist_ok=True)
    img.save(str(out_path))
    return str(out_path)


__all__ = [
    "smear_px", "max_span_s", "max_span_derotated_s", "smear_table",
    "FilterWindowPlan", "filter_window_plan", "session_plan", "plan_text",
    "render_plan_png",
]

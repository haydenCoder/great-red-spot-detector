#!/usr/bin/env python3
"""
GRS ground-truth ephemeris — longitude drift model + literature latitude.

WHY THIS EXISTS
---------------
Absolute System III longitude of the GRS cannot be scored on a real amateur
image without a mid-exposure UTC (no UTC -> no central meridian). But the GRS
longitude as a function of date IS published and tracked continuously by the
amateur/pro community (JUPOS / BAA / ALPO) and served authoritatively by the
IMCCE JGRS ephemeris. This module packages that online-derived truth so a
synthetic frame can be PLANTED at the real GRS longitude for a given epoch and
the measurement scored against it.

LONGITUDE DRIFT MODEL (cited, mean drift)
-----------------------------------------
The GRS drifts westward (to higher System III W longitude) at a slowly
accelerating rate. We use a linear mean-drift model anchored on two Hubble
measurements from program GO17275 (Tollefson et al. 2024, PSJ, doi
10.3847/PSJ/ad71d1, Table 2 central longitudes):

    2023-01-13  ->  350.5 deg W (System III)
    2023-09-09  ->   64.4 deg W (System III)

Over 239 days the longitude increased by 73.9 deg (350.5 -> 360 -> 64.4),
i.e. a mean rate of 0.309 deg/day westward. This is consistent with Simon
et al. 2018 (AJ 155:151, doi 10.3847/1538-3881/aaae01), who give a recent
westward drift of ~0.30-0.36 deg/day relative to System III. We adopt 0.31
deg/day.

IMPORTANT: this is the MEAN drift. The real GRS also exhibits a ~90-day
longitudinal oscillation of ~1 deg amplitude (Sanchez-Lavega et al. 2021,
doi 10.1029/2020JE006686; Wikipedia "Great Red Spot"). That oscillation is an
irreducible ~1 deg scatter in the *true* instant longitude and is NOT applied
to the planted mean here — planting the mean and scoring the estimator's
recovery isolates estimator error from the physical oscillation.

LATITUDE (cited)
----------------
GRS central latitude = -22.4 deg PLANETOGRAPHIC (JUPOS / BAA / NASA), stable to
~0.3 deg from 1979-2017 (Simon et al. 2018). The measurement stack works in
PLANETOCENTRIC coordinates, so the equivalent planetocentric latitude is
~ -19.82 deg (= GRS_LAT0 in precision_engine).

All functions are pure and deterministic; no network access at runtime.
"""
from __future__ import annotations

import datetime as dt
from typing import Tuple

from precision_engine import GRS_LAT0, wrap_deg, wrap_diff

# ---------------------------------------------------------------------------
# Cited literature constants
# ---------------------------------------------------------------------------
GRS_LAT_PLANETOGRAPHIC_LIT = -22.4          # JUPOS/BAA/NASA; Simon+2018
GRS_LAT_PLANETOCENTRIC_LIT = GRS_LAT0       # ~ -19.82 (engine convention)

# GRS zonal length in the 2010s (Sanchez-Lavega+2021): 15.5 deg (Mar 2019)
# shrinking to 13.7 deg (May 2020). Used only as a sanity band.
GRS_LENGTH_DEG_RANGE = (13.0, 16.0)

# Drift-model anchor (Hubble GO17275, Tollefson+2024).
_DRIFT_ANCHOR_DATE = dt.datetime(2023, 1, 13, 0, 0, 0)
_DRIFT_ANCHOR_LON_W = 350.5                 # deg, System III W
_DRIFT_RATE_DEG_PER_DAY = 0.31              # westward, derived + Simon+2018

# System III rotation period (seconds) — mirrors synthetic_hq.generate exactly
# so the analytical CM used for transit timing matches what the renderer plants.
_SYSIII_PERIOD_S = 9 * 3600 + 55 * 60 + 29.711
_MJD_EPOCH = dt.datetime(1858, 11, 17)


def _to_dt(t) -> dt.datetime:
    if isinstance(t, dt.datetime):
        return t
    if isinstance(t, dt.date):
        return dt.datetime(t.year, t.month, t.day)
    s = str(t).strip().replace("T", " ").replace("Z", "")
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S",
                "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise ValueError(f"unparseable datetime: {t!r}")


def grs_longitude_iii_w(t) -> float:
    """GRS centre System III WEST longitude [0,360) at UTC time ``t``.

    Linear mean-drift model anchored on Hubble GO17275 (2023-01-13 = 350.5 deg W)
    at 0.31 deg/day westward. See module docstring for citations and the
    ~1 deg / 90-day oscillation caveat.
    """
    t = _to_dt(t)
    ddays = (t - _DRIFT_ANCHOR_DATE).total_seconds() / 86400.0
    return wrap_deg(_DRIFT_ANCHOR_LON_W + _DRIFT_RATE_DEG_PER_DAY * ddays)


def analytical_cm_iii(t) -> float:
    """Analytical System III central meridian [0,360) at UTC time ``t``.

    Mirrors the formula in synthetic_hq.generate so transit timing and the
    renderer agree on the same CM. (SPICE gives the trusted absolute CM for
    real reductions; the analytical value is used here only for placing the
    synthetic GRS on the visible disk.)
    """
    t = _to_dt(t)
    mjd = (t - _MJD_EPOCH).total_seconds() / 86400.0
    return wrap_deg(360.0 * ((mjd - 51544.5) / (_SYSIII_PERIOD_S / 86400.0)))


def grs_lon_rel_deg(t) -> float:
    """GRS longitude relative to the central meridian at ``t`` [-180,180).

    Positive = GRS west of (past) the meridian, still on the approaching/visible
    side when |value| < ~90. Used to pick observation times that keep the GRS on
    the visible disk.
    """
    return wrap_diff(grs_longitude_iii_w(t), analytical_cm_iii(t))


def grs_transit_time(day, step_minutes: int = 6) -> dt.datetime:
    """UTC time within ``day`` when the GRS is closest to the central meridian.

    This is exactly when a careful observer images the GRS — on the meridian,
    minimum foreshortening. Scan the 24h at ``step_minutes`` resolution.
    """
    day = _to_dt(day)
    start = dt.datetime(day.year, day.month, day.day)
    best_t = start
    best_abs = 1e9
    t = start
    while t < start + dt.timedelta(days=1):
        a = abs(grs_lon_rel_deg(t))
        if a < best_abs:
            best_abs, best_t = a, t
        t = t + dt.timedelta(minutes=step_minutes)
    return best_t


def observe_at_placement(day, lon_rel_target_deg: float = 0.0) -> Tuple[dt.datetime, float]:
    """Pick an observation time on ``day`` that places the GRS at a target
    longitude relative to the meridian, and return (obs_time, achieved_lon_rel).

    lon_rel_target_deg in [-70, 70] keeps the GRS comfortably on the visible
    disk. Internally starts from the transit time and steps around it.
    """
    transit = grs_transit_time(day)
    target = float(lon_rel_target_deg)
    # cm rotates +360 deg per period (~9.925 h) -> ~36.3 deg/h. To move the GRS
    # to lon_rel = +target (further west of meridian), observe earlier; to
    # -target, observe later. Step finely and take the closest achievable.
    best_t = transit
    best_err = 1e9
    for dmin in range(-360, 361, 3):  # +-6 h in 3-min steps
        t = transit + dt.timedelta(minutes=dmin)
        achieved = grs_lon_rel_deg(t)
        if abs(achieved) > 80.0:
            continue
        err = abs(wrap_diff(achieved, target))
        if err < best_err:
            best_err, best_t = err, t
    return best_t, grs_lon_rel_deg(best_t)


def sources() -> dict:
    """Machine-readable citation list for the constants above."""
    return {
        "longitude_anchor": {
            "ref": "Tollefson et al. 2024, PSJ (Hubble program GO17275)",
            "doi": "10.3847/PSJ/ad71d1",
            "points": {
                "2023-01-13": 350.5,
                "2023-09-09": 64.4,
            },
            "system": "System III W longitude (deg)",
        },
        "drift_rate": {
            "value_deg_per_day": _DRIFT_RATE_DEG_PER_DAY,
            "derived_from": "350.5 -> 64.4 deg W over 239 days = 0.309 deg/day",
            "cross_check": "Simon et al. 2018, AJ 155:151, doi 10.3847/1538-3881/aaae01 (~0.30-0.36 deg/day)",
        },
        "latitude_planetographic": {
            "value": GRS_LAT_PLANETOGRAPHIC_LIT,
            "refs": ["JUPOS", "BAA", "NASA", "Simon et al. 2018 (stable ~0.3 deg, 1979-2017)"],
        },
        "oscillation_caveat": (
            "Real GRS has a ~90-day, ~1 deg amplitude longitude oscillation "
            "(Sanchez-Lavega+2021, doi 10.1029/2020JE006686); NOT applied to the planted mean."
        ),
    }

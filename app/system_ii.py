#!/usr/bin/env python3
"""system_ii.py — Jupiter System I / II / III rotation frames and conversion.

WHY THIS EXISTS
===============
The champion measurement publishes the Great Red Spot as a *System III*
(W) longitude, because System III is tied to the planet's interior via the
radio/magnetic rotation and is what SPICE returns in the IAU_JUPITER body
frame. But the amateur/professional community (JUPOS / BAA / ALPO) tracks the
GRS in *System II*, the mean atmospheric rotation at the GRS latitude band —
that is the longitude WinJUPOS draws on its maps and the number you cite
alongside a community database. Until now the repo only exported L_III; this
module provides the exact IAU frame rotation so a single measurement can be
reported in both systems from the same UTC timestamp.

THE MATH (cited)
================
From the IAU/IAG Working Group on Cartographic Coordinates and Rotational
Elements (Seidelmann et al. 2002, doi 10.1023/A:1023931602810; unchanged in
Archinal et al. 2011/2018), for the standard epoch JD 2451545.0 TT
(J2000.0), with d the interval in (TT) days since that epoch:

    System I    W_I   =  67.1  + 877.900 d   deg   (equatorial, |lat| < ~10 deg)
    System II   W_II  =  43.3  + 870.270 d   deg   (GRS band / higher latitudes)
    System III  W_III = 284.95 + 870.536 d   deg   (magnetic / interior)

The prime-meridian angles W rotate about the same pole, so a fixed point has
its two longitudes related by the frame offset alone:

    lon_II = lon_III + (W_II - W_III)        (mod 360)
           = lon_III - 241.65 - 0.266 d      (mod 360)

The observer-position term cancels identically between the two frames, so the
SAME offset converts both central meridians and feature longitudes:

    CM_II  = CM_III + (W_II - W_III)         (mod 360)

Validation: converting the repo's published-mean GRS System III anchor
(2023-01-13 = 350.5 deg W, +0.31 deg/day; Tollefson et al. 2024) into System
II reproduces the JUPOS/Sky & Telescope anchors (GRS L_II ~2 deg in July 2021,
~91 deg on 2026-06-01) to within the known ~4-5 deg real-drift scatter of the
linear mean model — the rotation itself is exact.

SCOPE & FALLBACK
================
Pure Python, no SPICE required. The time argument may be a timezone-aware or
naive datetime/date, or an ISO string. TT is computed with the repo's leap
second table (`grs_complete_system.utc_to_tt_mjd`); if that import fails we
fall back to plain UTC, which is correct to ~1 minute (0.006 deg of rotation).
SPICE stays the authoritative *absolute* CM III source for publication; this
module only performs the frame rotation on top of whatever CM III it is given.
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Union

# ---------------------------------------------------------------------------
# IAU/IAG WGCCRE rotation elements (Jupiter), epoch JD 2451545.0 TT
# ---------------------------------------------------------------------------
MJD_J2000 = 51544.5            # JD 2451545.0 = MJD 51544.5 (TT)

SYS1_W0_DEG = 67.1             # System I  prime meridian at J2000
SYS1_RATE_DEG_PER_DAY = 877.900

SYS2_W0_DEG = 43.3             # System II prime meridian at J2000
SYS2_RATE_DEG_PER_DAY = 870.270

SYS3_W0_DEG = 284.95           # System III prime meridian at J2000
SYS3_RATE_DEG_PER_DAY = 870.536

# W_II - W_III = (43.3 - 284.95) + (870.270 - 870.536) d
_SYS2_MINUS_SYS3_W0 = SYS2_W0_DEG - SYS3_W0_DEG            # -241.65
_SYS2_MINUS_SYS3_RATE = SYS2_RATE_DEG_PER_DAY - SYS3_RATE_DEG_PER_DAY  # -0.266

# W_I - W_III = (67.1 - 284.95) + (877.900 - 870.536) d
_SYS1_MINUS_SYS3_W0 = SYS1_W0_DEG - SYS3_W0_DEG            # -217.85
_SYS1_MINUS_SYS3_RATE = SYS1_RATE_DEG_PER_DAY - SYS3_RATE_DEG_PER_DAY  # +7.364

TimeArg = Union[str, dt.datetime, dt.date]


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def parse_time(t: TimeArg) -> dt.datetime:
    """Coerce a str / date / datetime into a naive-UTC datetime."""
    if isinstance(t, dt.datetime):
        out = t
    elif isinstance(t, dt.date):
        out = dt.datetime(t.year, t.month, t.day)
    else:
        s = str(t).strip().replace("T", " ").replace("Z", "")
        if not s:
            raise ValueError("empty time string")
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S",
                    "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y/%m/%d %H:%M:%S",
                    "%Y/%m/%d %H:%M", "%Y/%m/%d"):
            try:
                out = dt.datetime.strptime(s, fmt)
                break
            except ValueError:
                continue
        else:
            try:
                out = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
            except ValueError as e:
                raise ValueError(f"unparseable time: {t!r}") from e
    if out.tzinfo is not None:
        out = out.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return out


def j2000_days(t: TimeArg) -> float:
    """Days (TT) since J2000.0 (JD 2451545.0) for `t`.

    Uses the repo's UTC->TT conversion (leap-second aware). Falls back to UTC
    arithmetic if that module is unavailable (correct to ~1 minute -> 0.006 deg).
    """
    t = parse_time(t)
    try:
        from grs_complete_system import utc_to_tt_mjd
        tt_mjd = utc_to_tt_mjd(t)
    except Exception:
        mjd0 = dt.datetime(1858, 11, 17)
        tt_mjd = (t - mjd0).total_seconds() / 86400.0
    return float(tt_mjd - MJD_J2000)


# ---------------------------------------------------------------------------
# Prime-meridian angles (degrees, unwrapped — may exceed [0, 360))
# ---------------------------------------------------------------------------

def prime_meridian_deg(t: TimeArg, system: str = "III") -> float:
    """IAU prime-meridian angle W (deg, unwrapped) for a Jupiter rotation system."""
    d = j2000_days(t)
    key = str(system).upper()
    if key in ("III", "3", "SYS3", "SYSIII"):
        return SYS3_W0_DEG + SYS3_RATE_DEG_PER_DAY * d
    if key in ("II", "2", "SYS2", "SYSII"):
        return SYS2_W0_DEG + SYS2_RATE_DEG_PER_DAY * d
    if key in ("I", "1", "SYS1", "SYSI"):
        return SYS1_W0_DEG + SYS1_RATE_DEG_PER_DAY * d
    raise ValueError(f"unknown Jupiter rotation system {system!r} (I/II/III)")


def system_ii_minus_system_iii_deg(t: TimeArg) -> float:
    """W_II - W_III (deg) at `t` — the frame offset used in both directions.

    This is the one number that converts *any* System III longitude (central
    meridian or feature) into its System II value at the same instant.
    """
    d = j2000_days(t)
    return _SYS2_MINUS_SYS3_W0 + _SYS2_MINUS_SYS3_RATE * d


def system_i_minus_system_iii_deg(t: TimeArg) -> float:
    """W_I - W_III (deg) at `t` — equatorial System I frame offset.

    System I applies to |latitude| < ~10 deg (the GRS lives outside it), but
    WinJUPOS reports all three CMs, so the mapping is included for completeness.
    """
    d = j2000_days(t)
    return _SYS1_MINUS_SYS3_W0 + _SYS1_MINUS_SYS3_RATE * d


def wrap_deg(x: float) -> float:
    """Wrap an angle into [0, 360)."""
    return float(x % 360.0)


# ---------------------------------------------------------------------------
# Conversions
# ---------------------------------------------------------------------------

def system_iii_to_system_ii(lon_iii_deg: float, t: TimeArg) -> float:
    """System III (W) longitude -> System II longitude [0, 360) at time `t`."""
    return wrap_deg(float(lon_iii_deg) + system_ii_minus_system_iii_deg(t))


def system_ii_to_system_iii(lon_ii_deg: float, t: TimeArg) -> float:
    """System II longitude -> System III (W) longitude [0, 360) at time `t`."""
    return wrap_deg(float(lon_ii_deg) - system_ii_minus_system_iii_deg(t))


def cm_ii_from_cm_iii(cm_iii_deg: float, t: TimeArg) -> float:
    """System III central meridian -> System II central meridian [0, 360)."""
    return system_iii_to_system_ii(cm_iii_deg, t)


def cm_iii_from_cm_ii(cm_ii_deg: float, t: TimeArg) -> float:
    """System II central meridian -> System III central meridian [0, 360)."""
    return system_ii_to_system_iii(cm_ii_deg, t)


# ---------------------------------------------------------------------------
# Convenience result object
# ---------------------------------------------------------------------------

@dataclass
class SystemIICoords:
    t_utc_iso: str
    cm_iii_deg: float
    cm_ii_deg: float
    cm_i_deg: float = float("nan")
    lon_iii_deg: Optional[float] = None
    lon_ii_deg: Optional[float] = None
    offset_deg: float = 0.0
    source: str = ""
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def derive_system_ii(
    cm_iii_deg: float,
    t_utc: TimeArg,
    *,
    lon_iii_deg: Optional[float] = None,
    source: str = "",
) -> SystemIICoords:
    """Compute System II coordinates from a trusted System III CM.

    `lon_iii_deg` is optional: pass the measured GRS System III longitude to
    also get its System II value. Both are derived with the single exact IAU
    frame offset, so CM and feature stay mutually consistent.
    """
    t = parse_time(t_utc)
    offset = system_ii_minus_system_iii_deg(t)
    out = SystemIICoords(
        t_utc_iso=t.isoformat(),
        cm_iii_deg=wrap_deg(float(cm_iii_deg)),
        cm_ii_deg=wrap_deg(float(cm_iii_deg) + offset),
        cm_i_deg=wrap_deg(float(cm_iii_deg) + system_i_minus_system_iii_deg(t)),
        offset_deg=offset,
        source=source or "system_ii",
    )
    if lon_iii_deg is not None and math.isfinite(float(lon_iii_deg)):
        out.lon_iii_deg = wrap_deg(float(lon_iii_deg))
        out.lon_ii_deg = wrap_deg(float(lon_iii_deg) + offset)
    return out


def system_ii_report_text(coords: SystemIICoords) -> str:
    """One-page human-readable System I/II/III longitude card."""
    lines = [
        "SYSTEM I / II / III LONGITUDE MAP",
        "=================================",
        f"epoch UTC   {coords.t_utc_iso}",
        f"CM III      {coords.cm_iii_deg:.4f} °",
        f"CM II       {coords.cm_ii_deg:.4f} °",
        f"CM I        {coords.cm_i_deg:.4f} °",
        f"offset W2-W3{coords.offset_deg:+.4f} °  [IAU WGCCRE: 43.3+870.270d vs 284.95+870.536d]",
    ]
    if coords.lon_iii_deg is not None:
        lines += [
            f"GRS lon III {coords.lon_iii_deg:.4f} ° W",
            f"GRS lon II  {coords.lon_ii_deg:.4f} °",
        ]
    if coords.source:
        lines.append(f"source      {coords.source}")
    for n in coords.notes:
        lines.append(f"note        {n}")
    lines.append("")
    return "\n".join(lines)


def selftest() -> Dict[str, Any]:
    """Cheap deterministic check of the IAU constants against known anchors."""
    import datetime as _dt
    # JUPOS/Sky & Telescope anchor: GRS at System II 91 deg on 2026-06-01.
    # Repo's published-mean GRS System III model puts it at ~13.35 deg W that day.
    t = _dt.datetime(2026, 6, 1, 0, 0, 0)
    lon_iii = 13.35
    lon_ii = system_iii_to_system_ii(lon_iii, t)
    return {
        "offset_deg": system_ii_minus_system_iii_deg(t),
        "grs_lon_ii_predicted": lon_ii,
        "grs_lon_ii_published": 91.0,
        "roundtrip": system_ii_to_system_iii(lon_ii, t),
    }


if __name__ == "__main__":
    import json
    print(json.dumps(selftest(), indent=2, default=str))

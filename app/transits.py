#!/usr/bin/env python3
"""
transits.py — WinJUPOS-style ephemeris & observing planner.

WHAT YOU GET
============
  - GRS transit events over N days (where WinJUPOS prints "GRS transit time"),
    found as smooth roots of sin(lon_rel) with brentq refinement — sub-minute
    accuracy, not the old 6-minute grid scan.
  - Galilean-moon transit events (Io / Europa / Ganymede / Callisto) computed
    from the shipped NAIF kernels: apparent sky-plane minima that cross the
    Jovian disk with the moon *in front*.
  - Observable windows: the intervals when the GRS is within ±60° of the CM
    (comfortably measurable), so a night plan is one call.
  - A `grs_now` panel dict for dashboards.

MODEL NOTES (honest)
====================
- GRS longitude comes from `grs_ephemeris_truth` (Hubble-anchored mean drift
  + the documented ~1°/90-day oscillation caveat) and the analytical CM;
  over a single night SPICE-vs-analytical CM differences are tiny relative to
  the 36.3°/h sweep rate, and transit *times* inherit the GRS drift-model
  uncertainty (~±0.5° over months ⇒ ~±1.5 min).
- Moon geometry uses de440s + gm/pck kernels; the disk-crossing test uses the
  equatorial radius (limb crossings can extend slightly longer for equatorial
  paths — flagged in the event record).
"""
from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import grs_ephemeris_truth as _truth

JUP_REQ_KM = 71492.0
MOONS = {
    "io": "501",
    "europa": "502",
    "ganymede": "503",
    "callisto": "504",
}

UTC = dt.timezone.utc


def _to_utc(t) -> dt.datetime:
    """Normalise to *naive* UTC (the convention used across the app and
    `grs_ephemeris_truth`; output strings get the "Z" suffix instead)."""
    t = _truth._to_dt(t)
    if t.tzinfo is not None:
        t = t.astimezone(UTC).replace(tzinfo=None)
    return t


def _rel(t: dt.datetime) -> float:
    return _truth.grs_lon_rel_deg(t)


# ---------------------------------------------------------------------------
# GRS events
# ---------------------------------------------------------------------------

@dataclass
class GRSTransit:
    utc: str
    kind: str = "grs_transit"
    method: str = "analytical_cm+brentq"

    def to_dict(self) -> dict:
        return asdict(self)


def grs_transits(start, days: float = 1.0, step_s: float = 120.0,
                 refine: bool = True) -> List[GRSTransit]:
    """All GRS meridian-crossing events in [start, start+days).

    Detection: g(t)=sin(lon_rel(t)) is smooth; transits are its downward
    zero crossings (cos > 0 — the CM sweeps past the slowly-drifting GRS).
    Bracketed on a step_s grid, refined with brentq.
    """
    from scipy.optimize import brentq
    t0 = _to_utc(start)
    n = max(2, int(days * 86400.0 / step_s))
    times = [t0 + dt.timedelta(seconds=i * step_s) for i in range(n + 1)]
    g_prev = None
    out: List[GRSTransit] = []
    for t in times:
        r = math.radians(_rel(t))
        g = (math.sin(r), math.cos(r))
        if g_prev is not None:
            s0, c0 = g_prev
            s1, c1 = g
            if s0 > 0.0 >= s1 and (c0 + c1) > 0.0:      # downward crossing at the meridian
                ta = t - dt.timedelta(seconds=step_s)
                if refine:
                    f = lambda tt: math.sin(math.radians(_rel(t0 + dt.timedelta(seconds=tt))))
                    root = brentq(f, (ta - t0).total_seconds(), (t - t0).total_seconds(),
                                  xtol=0.5, rtol=1e-8)
                    tev = t0 + dt.timedelta(seconds=root)
                else:
                    tev = ta + (t - ta) * (0.0 - s0) / (s1 - s0)
                if not out or (tev - dt.datetime.fromisoformat(out[-1].utc.replace("Z", "+00:00"))).total_seconds() > 3600:
                    out.append(GRSTransit(utc=tev.isoformat().replace("+00:00", "Z")))
        g_prev = g
    return out


@dataclass
class VisibilityWindow:
    start_utc: str
    end_utc: str
    peak_utc: str
    limit_deg: float

    def to_dict(self) -> dict:
        return asdict(self)


def grs_visibility_windows(start, days: float = 1.0, limit_deg: float = 60.0,
                           step_s: float = 300.0) -> List[VisibilityWindow]:
    """Intervals where |lon_rel| <= limit_deg (GRS well on the visible disk)."""
    t0 = _to_utc(start)
    n = max(2, int(days * 86400.0 / step_s))
    inside = False
    win_start = None
    best_t = None
    best_abs = 1e9
    out: List[VisibilityWindow] = []
    for i in range(n + 1):
        t = t0 + dt.timedelta(seconds=i * step_s)
        a = abs(_rel(t))
        is_in = a <= limit_deg
        if is_in and not inside:
            inside, win_start, best_t, best_abs = True, t, t, a
        elif is_in and inside:
            if a < best_abs:
                best_abs, best_t = a, t
        elif not is_in and inside:
            out.append(VisibilityWindow(
                start_utc=win_start.isoformat().replace("+00:00", "Z"),
                end_utc=t.isoformat().replace("+00:00", "Z"),
                peak_utc=best_t.isoformat().replace("+00:00", "Z"),
                limit_deg=limit_deg))
            inside = False
    if inside:
        out.append(VisibilityWindow(
            start_utc=win_start.isoformat().replace("+00:00", "Z"),
            end_utc=(t0 + dt.timedelta(days=days)).isoformat().replace("+00:00", "Z"),
            peak_utc=best_t.isoformat().replace("+00:00", "Z"),
            limit_deg=limit_deg))
    return out


def grs_now(t=None) -> dict:
    """Panel data: GRS longitude, CM, relative angle, transit within 24 h."""
    t = _to_utc(t) if t is not None else dt.datetime.now(UTC).replace(tzinfo=None)
    tr = grs_transits(t, days=1.0, step_s=180.0)
    return {
        "utc": t.isoformat().replace("+00:00", "Z"),
        "grs_lon_iii_w_deg": round(_truth.grs_longitude_iii_w(t), 3),
        "cm_iii_w_deg": round(_truth.analytical_cm_iii(t), 3),
        "grs_lon_rel_deg": round(_rel(t), 3),
        "grs_lat_planetographic_deg": _truth.GRS_LAT_PLANETOGRAPHIC_LIT,
        "next_transits_24h": [e.to_dict() for e in tr],
        "on_disk_now": abs(_rel(t)) <= 60.0,
    }


# ---------------------------------------------------------------------------
# Galilean moon transits (SPICE)
# ---------------------------------------------------------------------------

_KERNELS_LOADED = False
_EPHEM_MOONS = {"io": "Io", "europa": "Europa", "ganymede": "Ganymede", "callisto": "Callisto"}


def _furnish(kernels_dir: Optional[Path] = None) -> None:
    global _KERNELS_LOADED
    if _KERNELS_LOADED:
        return
    import spiceypy as spice
    if kernels_dir is None:
        import spice_auto
        kernels_dir = spice_auto.kernel_dir()
    kernels_dir = Path(kernels_dir)
    for name in ("naif0012.tls", "de440s.bsp", "jup365.bsp", "pck00011.tpc", "gm_de440.tpc"):
        p = kernels_dir / name
        if p.exists():
            spice.furnsh(str(p))
    _KERNELS_LOADED = True


def moon_backend(kernels_dir: Optional[Path] = None) -> str:
    """"spice" if the jup365 satellite SPK is available, else "ephem" if
    PyEphem is installed, else the empty string (feature unavailable)."""
    if kernels_dir is None:
        try:
            import spice_auto
            kernels_dir = spice_auto.kernel_dir()
        except Exception:
            kernels_dir = None
    if kernels_dir is not None and (Path(kernels_dir) / "jup365.bsp").exists():
        try:
            import spiceypy  # noqa: F401
            return "spice"
        except Exception:
            pass
    try:
        import ephem  # noqa: F401
        return "ephem"
    except Exception:
        return ""


@dataclass
class MoonTransit:
    utc: str
    moon: str
    kind: str = "moon_transit"
    separation_rj: float = 0.0
    equatorial_path: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def _geometry(et: float, moon_naif: str):
    """(apparent separation in Jupiter radii, moon-in-front flag)."""
    import spiceypy as spice
    import numpy as np
    pm, _ = spice.spkpos(moon_naif, et, "J2000", "LT+S", "399")
    pj, _ = spice.spkpos("599", et, "J2000", "LT+S", "399")
    dm = float(np.linalg.norm(pm))
    dj = float(np.linalg.norm(pj))
    cosang = float(np.dot(pm, pj) / (dm * dj))
    cosang = min(1.0, max(-1.0, cosang))
    sep_rad = math.acos(cosang)
    rj_ang = math.asin(min(1.0, JUP_REQ_KM / dj))
    return sep_rad / rj_ang, dm < dj


def _moon_sep_ephem(moon_name: str, t: dt.datetime) -> Tuple[float, bool]:
    """Separation (Jupiter radii) and in-front flag for a moon at UTC t.

    PyEphem/XEphem returns (x, y, z) in Jupiter radii on the sky plane; its
    z-axis sign is ground-truthed against Project Pluto's 2026 event table:
    z > 0 = moon in FRONT of the disk (Io transit 2026-08-01 05:06 UTC),
    z < 0 = behind (occultation).
    """
    import ephem
    m = getattr(ephem, _EPHEM_MOONS[moon_name])()
    m.compute(ephem.Date(t))
    return math.hypot(m.x, m.y), m.z > 0.0


def moon_transits(start, days: float = 1.0, moon: str = "io",
                  step_s: float = 120.0, kernels_dir: Optional[Path] = None,
                  backend: str = "auto") -> List[MoonTransit]:
    """Disk-crossing events of a Galilean moon IN FRONT of Jupiter.

    Sky-plane separation minima below ~1.0 Rj (equatorial radius) while the
    moon is closer to Earth than Jupiter's centre; times refined by golden
    section on the separation curve. Backend: jup365 SPICE kernel when
    present, otherwise the Meeus-model PyEphem positions (validated against
    the published Project Pluto 2026 event table to ~4 min).
    """
    from scipy.optimize import minimize_scalar
    moon = moon.lower()
    t0 = _to_utc(start)
    be = backend if backend != "auto" else moon_backend(kernels_dir)
    if be == "spice":
        import spiceypy as spice
        _furnish(kernels_dir)
        naif = MOONS[moon]
        et0 = spice.utc2et(t0.isoformat())

        def sep_at(sec: float) -> Tuple[float, bool]:
            return _geometry(et0 + sec, naif)
    elif be == "ephem":
        def sep_at(sec: float) -> Tuple[float, bool]:
            return _moon_sep_ephem(moon, t0 + dt.timedelta(seconds=sec))
    else:
        raise RuntimeError(
            "no moon-ephemeris backend: install PyEphem (`pip install ephem`) "
            "or provide the jup365.bsp SPICE kernel")

    n = max(2, int(days * 86400.0 / step_s))
    seps = [sep_at(i * step_s) for i in range(n + 1)]
    candidates = []
    for i in range(1, n):
        s0, _ = seps[i - 1]
        s1, front = seps[i]
        s2, _ = seps[i + 1]
        if s1 <= s0 and s1 <= s2 and s1 < 1.02 and front:
            candidates.append(i)
    out: List[MoonTransit] = []
    for i in candidates:
        found = minimize_scalar(
            lambda s: sep_at(s)[0],
            bounds=((i - 1) * step_s, (i + 1) * step_s),
            method="bounded", options={"xatol": 1.0})
        if not found.success:
            continue
        sep_min, front = sep_at(found.x)
        if not front or sep_min > 1.05:
            continue
        tev = t0 + dt.timedelta(seconds=float(found.x))
        out.append(MoonTransit(
            utc=tev.isoformat(timespec="seconds") + "Z",
            moon=moon, separation_rj=round(float(sep_min), 3),
            equatorial_path=bool(sep_min > 0.94),
        ))
    # de-dup
    dedup: List[MoonTransit] = []
    for e in out:
        if not dedup or abs((dt.datetime.fromisoformat(e.utc.replace("Z", "+00:00")).replace(tzinfo=None)
                             - dt.datetime.fromisoformat(dedup[-1].utc.replace("Z", "+00:00")).replace(tzinfo=None)).total_seconds()) > 1800:
            dedup.append(e)
    return dedup


def night_planner(start, days: float = 1.0, moons: Tuple[str, ...] = ("io", "europa", "ganymede", "callisto")) -> dict:
    """Single-call night sheet: GRS transits, windows, moon events."""
    t0 = _to_utc(start)
    out = {
        "start_utc": t0.isoformat().replace("+00:00", "Z"),
        "days": days,
        "grs": grs_now(t0),
        "grs_visibility_windows": [w.to_dict() for w in grs_visibility_windows(t0, days=days)],
        "grs_transits": [e.to_dict() for e in grs_transits(t0, days=days)],
        "moon_transits": [],
    }
    try:
        for m in moons:
            out["moon_transits"] += [e.to_dict() for e in moon_transits(t0, days=days, moon=m)]
    except Exception as e:
        out["moon_transits_error"] = f"{type(e).__name__}: {e}"
    out["moon_transits"].sort(key=lambda e: e["utc"])
    return out


def planner_text(plan: dict) -> str:
    L = []
    L.append("=" * 72)
    L.append("OBSERVING PLANNER — GRS & Galilean moon events")
    L.append("=" * 72)
    g = plan.get("grs", {})
    L.append(f"Epoch: {plan.get('start_utc', '?')}   span: {plan.get('days', 1)} day(s)")
    if g:
        L.append(f"GRS now: III W {g.get('grs_lon_iii_w_deg', '?')}°   CM {g.get('cm_iii_w_deg', '?')}°   "
                 f"rel {g.get('grs_lon_rel_deg', '?')}°   on-disk: {g.get('on_disk_now', '?')}")
    L.append("")
    L.append("GRS transits (meridian crossings):")
    for e in plan.get("grs_transits", []):
        L.append(f"  {e['utc']}")
    L.append("")
    L.append("GRS good-measurement windows (|rel| ≤ 60°):")
    for w in plan.get("grs_visibility_windows", []):
        L.append(f"  {w['start_utc'][11:19]} → {w['end_utc'][11:19]}  (peak {w['peak_utc'][11:19]}Z)")
    L.append("")
    mts = plan.get("moon_transits", [])
    L.append("Galilean moon transits:")
    if mts:
        for e in mts:
            note = "  (near equatorial path)" if e.get("equatorial_path") else ""
            L.append(f"  {e['utc']}  {e['moon']:<9}  sep {e['separation_rj']} Rj{note}")
    else:
        L.append(f"  none in window {plan.get('moon_transits_error', '')}")
    return "\n".join(L)

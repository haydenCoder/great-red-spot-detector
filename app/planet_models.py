#!/usr/bin/env python3
"""
planet_models.py — planet parameters so the stacker / derotator are not
hard-coded to Jupiter.

WHY THIS EXISTS
===============
`jupiter_zonal_stacker.py` and `jupiter_zonal_derotator.py` baked in
Jupiter's equatorial radius (71 492 km), flattening (0.0649), System III
rotation period (9h55m29.7s) and the Porco+2003 zonal-wind residual
profile. Every one of those is a *Jupiter* number. The new
`planetary_stacker.py` / `planetary_derotator.py` take a `Planet` instead,
so the SAME code derotates Saturn, Neptune, Uranus or Mars — you just pass
a different rotation period, oblateness and wind profile.

WHAT A PLANET CARRIES
=====================
  - req_km / rpol_km        equatorial / polar radius → flattening
  - rotation_period_s       bulk rotation period (System III equivalent)
  - zonal_wind_mps          (|lat|, u) table; symmetric N/S, prograde = +
  - cloud_tracking_rate_deg_per_s(lat)
                            ω_bulk + Δu(φ)/(R_eq cos φ) — the *cloud* rate,
                            which is what a derotator must follow, not the
                            radio/interior rate.

HONEST CAVEAT
=============
The radii and rotation periods are published IAU / fact-sheet values and are
good to the last digit shown. The zonal-wind RESIDUAL tables are
*representative literature cloud-tracking profiles*, not precision ephemeris:
they are good enough to serve as a derotation prior (the stacker measures the
true per-latitude motion from the frames and overrides them wherever the data
disagrees), but do not cite them as a wind measurement. Each profile carries
its source in `reference`.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, Tuple

import numpy as np


def _table(rows: Tuple[Tuple[float, float], ...]) -> np.ndarray:
    """(N,2) float64 array from a list of (|lat_deg|, u_mps) tuples."""
    return np.asarray(rows, dtype=np.float64)


@dataclass
class Planet:
    """A rotating oblate body with a zonal-wind cloud-tracking profile.

    All latitudes are planetocentric (the convention used everywhere else in
    this codebase). Wind residuals are symmetric about the equator and given
    as a function of |latitude|, so the same table serves both hemispheres.
    """
    name: str
    req_km: float
    rpol_km: float
    rotation_period_s: float
    zonal_wind_mps: np.ndarray            # (N,2): (|lat_deg|, u_mps)
    default_distance_au: float
    reference: str = ""

    @property
    def flattening(self) -> float:
        return 1.0 - self.rpol_km / self.req_km

    @property
    def rotation_rate_deg_per_s(self) -> float:
        """Bulk (System-III-equivalent) angular rotation rate."""
        return 360.0 / max(self.rotation_period_s, 1e-9)

    @property
    def req_m(self) -> float:
        return self.req_km * 1.0e3

    def zonal_wind_residual_mps(self, lat_deg: float) -> float:
        """Cloud-tracking wind residual Δu(φ) in m/s (prograde = +)."""
        la = abs(float(lat_deg))
        t = self.zonal_wind_mps
        if la <= t[0, 0]:
            return float(t[0, 1])
        if la >= t[-1, 0]:
            return float(t[-1, 1])
        return float(np.interp(la, t[:, 0], t[:, 1]))

    def cloud_tracking_rate_deg_per_s(self, lat_deg: float) -> float:
        """Cloud-feature angular rate at planetocentric latitude φ.

            ω_cloud(φ) = ω_bulk + Δu(φ) / (R_eq · cos φ)        [rad/s]

        Δu/(R_eq cos φ) is the extra angular rate from the zonal wind beyond
        the bulk rotation. Near the poles cos φ → 0 so the wind term would
        blow up; clamp cos φ to keep it finite (APs that close to the limb
        are unmeasurable anyway).
        """
        cos_la = math.cos(math.radians(float(lat_deg)))
        if cos_la < 0.05:
            cos_la = 0.05
        delta_rate_rad = (self.zonal_wind_residual_mps(lat_deg) * 1.0e-3) / self.req_km
        delta_rate_deg = math.degrees(delta_rate_rad) / cos_la
        return self.rotation_rate_deg_per_s + delta_rate_deg

    def expected_drift_dx_px(self, lat_deg: float, dt_s: float, deg_to_px: float) -> float:
        """Image-x drift (px) of a cloud feature at φ over dt_s.

        LEGACY model — superseded by `lon_drift_px`. This version converts
        degrees of longitude with `deg_to_px = a_eq_px / 90`, which misses
        both the π/180 chord geometry (a 1.57× under-shift at the equator)
        and the cos(φ) latitude chord: at the GRS latitude the prior
        under-rotates by ~40% (measured on rotating-video benchmarks
        v6.8.x — see video_synth / test_video_jupiter). Kept for signature
        compatibility; inside the codebase all derotation call sites now
        use `lon_drift_px`.
        """
        if dt_s == 0.0:
            return 0.0
        return self.cloud_tracking_rate_deg_per_s(lat_deg) * dt_s * deg_to_px

    def px_per_deg_lon(self, lat_deg: float, a_eq_px: float) -> float:
        """Image-x px per degree of longitude rotation at planetocentric lat φ.

        Orthographic spheroid: sky X = r(φ) cosφ sinλ · s (s = a_eq_px per
        R_eq), so dX/dλ = r(φ) cosφ at the central meridian — the per-row
        model uses exactly this centre-line chord. Degrees → px:
        (π/180) · r(φ) cosφ · a_eq_px, where r(φ) is the oblate radius
        factor 1/sqrt(cos²φ + sin²φ/k²).
        """
        flat = self.flattening
        k = 1.0 - float(flat)
        la = math.radians(float(lat_deg))
        c, s = math.cos(la), math.sin(la)
        r_phi = 1.0 / math.sqrt(c * c + (s / k) ** 2)
        return (math.pi / 180.0) * r_phi * c * float(a_eq_px)

    def lon_drift_px(self, lat_deg: float, dt_s: float, a_eq_px: float) -> float:
        """Content x-displacement (px) of a cloud feature at φ over dt_s.

        = −ω_cloud(φ)·dt·px_per_deg_lon (content moves −x for CM increasing,
        the same sign convention as `_per_ap_expected_dx`). Ground-truthed on
        rotating-video benchmarks: <0.5° error after 350 s of drift at
        512×384 (test_video_jupiter)."""
        if dt_s == 0.0:
            return 0.0
        return (-self.cloud_tracking_rate_deg_per_s(lat_deg) * float(dt_s)
                * self.px_per_deg_lon(lat_deg, a_eq_px))


# ---------------------------------------------------------------------------
# Built-in profiles. Radii/periods are IAU/nominal fact-sheet values; the wind
# tables are representative cloud-tracking profiles (see each `reference`).
# ---------------------------------------------------------------------------

JUPITER = Planet(
    name="Jupiter",
    req_km=71492.0,
    rpol_km=66854.0,
    rotation_period_s=9 * 3600 + 55 * 60 + 29.711,   # System III (IAU)
    default_distance_au=5.2,
    reference="Radii: IAU 2009. Rotation: System III 9h55m29.711s. "
              "Winds: Porco+2003 / Li+2004 cloud-tracking (representative).",
    zonal_wind_mps=_table((
        (0.0,   5.0),
        (5.0,  30.0),
        (10.0, 50.0),
        (15.0, 35.0),
        (20.0, 18.0),
        (25.0,  8.0),
        (30.0,  2.0),
        (40.0, -5.0),
        (50.0, -8.0),
        (60.0, -5.0),
    )),
)

SATURN = Planet(
    name="Saturn",
    req_km=60268.0,
    rpol_km=54364.0,
    rotation_period_s=10 * 3600 + 32 * 60 + 45.0,    # Cassini-era System III
    default_distance_au=9.5,
    reference="Radii: IAU 2009. Rotation: 10h32m45s (Cassini, Read+2009). "
              "Winds: García-Melendo+2011 cloud-tracking (representative).",
    # Saturn's equatorial super-rotation is much faster than Jupiter's and
    # peaks right at the equator rather than in the jet belts.
    zonal_wind_mps=_table((
        (0.0,  390.0),
        (5.0,  430.0),
        (10.0, 360.0),
        (15.0, 250.0),
        (20.0, 150.0),
        (30.0,  60.0),
        (40.0,  20.0),
        (50.0,  -5.0),
        (60.0, -20.0),
        (75.0, -30.0),
    )),
)

NEPTUNE = Planet(
    name="Neptune",
    req_km=24764.0,
    rpol_km=24341.0,
    rotation_period_s=16 * 3600 + 6 * 60 + 36.0,     # 16h06m36s
    default_distance_au=30.1,
    reference="Radii: IAU 2009. Rotation: 16h06m36s (Karkoschka 2011). "
              "Winds: Sromovsky+1993 / Hammel+1989 cloud-tracking.",
    # Neptune has the fastest winds in the solar system: a strong prograde
    # jet near ~-30° and a retrograde equatorial band. We model the
    # magnitude envelope symmetric in |lat|.
    zonal_wind_mps=_table((
        (0.0,  -50.0),
        (10.0,  80.0),
        (20.0, 250.0),
        (30.0, 400.0),
        (40.0, 300.0),
        (50.0, 150.0),
        (60.0,  40.0),
        (70.0, -20.0),
    )),
)

URANUS = Planet(
    name="Uranus",
    req_km=25559.0,
    rpol_km=24973.0,
    rotation_period_s=17 * 3600 + 14 * 60 + 24.0,    # 17h14m24s (retrograde)
    default_distance_au=19.2,
    reference="Radii: IAU 2009. Rotation: 17h14m24s. Winds: Hammel+2001 "
              "(retrograde broad jet, representative).",
    # Uranus: broad retrograde flow; equator near-0, peak retrograde ~mid-lat.
    # Period is entered positive; the sign of rotation is handled by the
    # caller supplying cm drift of the correct sign.
    zonal_wind_mps=_table((
        (0.0,   -30.0),
        (10.0, -120.0),
        (20.0, -210.0),
        (30.0, -250.0),
        (40.0, -220.0),
        (50.0, -150.0),
        (60.0,  -80.0),
        (75.0,  -20.0),
    )),
)

MARS = Planet(
    name="Mars",
    req_km=3396.19,
    rpol_km=3376.20,
    rotation_period_s=24 * 3600 + 37 * 60 + 22.663,  # sidereal sol
    default_distance_au=1.52,
    reference="Radii: IAU 2015. Rotation: sidereal sol 24h37m22.663s. "
              "Winds: Mars GCM zonal-mean (representative; weak, terrestrial).",
    # Near-spherical, slow, weak winds — included to show the pipeline works
    # for terrestrial / near-spherical bodies, not just gas giants.
    zonal_wind_mps=_table((
        (0.0,  10.0),
        (15.0, 35.0),
        (30.0, 45.0),
        (45.0, 30.0),
        (60.0, 15.0),
        (75.0,  5.0),
    )),
)

_PLANETS: Dict[str, Planet] = {
    p.name.lower(): p for p in (JUPITER, SATURN, NEPTUNE, URANUS, MARS)
}


def get_planet(name: str) -> Planet:
    """Look up a built-in planet by name (case-insensitive)."""
    key = (name or "").strip().lower()
    if key not in _PLANETS:
        raise ValueError(
            f"Unknown planet {name!r}. Known: {sorted(_PLANETS)}"
        )
    return _PLANETS[key]


def known_planets() -> Tuple[str, ...]:
    return tuple(sorted(_PLANETS))


__all__ = [
    "Planet",
    "JUPITER", "SATURN", "NEPTUNE", "URANUS", "MARS",
    "get_planet", "known_planets",
]

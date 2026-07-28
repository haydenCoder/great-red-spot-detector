"""
100-case geometry stress suite — independent-oracle projection audit.

Unlike tests/test_geometry_limb_lonlat.py, which round-trips px_to_lonlat
against a *copy of the engine's own* forward model, this suite compares the
engine against an INDEPENDENT, physically-derived orthographic projection of
an oblate spheroid. A self-consistent-but-wrong projection therefore cannot
pass here, which is how the PA/oblateness defect below was found.

Case budget (100 parametrised geometry cases + supporting checks):
  •  36 zero-PA cases        (sub-lat × lon_rel × lat)
  •  36 rotated-PA cases     (PA × lon_rel × lat)
  •  16 oblateness cases     (latitude convention / radius of curvature)
  •  12 scalar / metric cases (wrap, arcsec, km-per-deg)

Known-defect cases are marked xfail(strict=False) with a quantified budget so
they flip to XPASS the moment the projector is corrected.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import pytest

APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from precision_engine import (  # noqa: E402
    FLAT,
    JUP_REQ_KM,
    JUP_RPOL_KM,
    NavState,
    deg_to_arcsec_on_sky,
    km_per_deg_lat,
    km_per_deg_lon,
    planetocentric_to_planetographic,
    planetographic_to_planetocentric,
    px_to_lonlat,
    sky_error_arcsec,
    wrap_deg,
    wrap_diff,
)

# Tolerances. 0.05° at Jupiter/4.3 AU is ~0.02" — well inside the product's
# own 0.75" median certification gate, so these are not hair-trigger.
TOL_LON_DEG = 0.05
TOL_LAT_DEG = 0.05


# ---------------------------------------------------------------------------
# Independent oracle: orthographic projection of a true oblate spheroid.
# ---------------------------------------------------------------------------
def oracle_project(
    lon_rel_deg: float,
    lat_c_deg: float,
    nav: NavState,
) -> tuple[float, float, float] | None:
    """
    Project a planetocentric (lon_rel, lat) surface point to pixels.

    Derivation (no shared code with precision_engine):
      1. Unit direction from body centre at planetocentric latitude.
      2. Spheroid radius along that direction:
             r(phi) = 1 / sqrt(cos^2 phi + (sin phi / (1-f))^2)   [units of Req]
         so the *surface point*, not the unit sphere point, is projected.
      3. Rotate by sub-observer latitude D about the sky x-axis.
      4. Rotate by north position angle in the sky plane.
      5. Orthographic drop of the line-of-sight component, single plate
         scale a_eq_px in BOTH axes — the limb ellipse is produced by the
         spheroid geometry itself, not by squashing the y-axis.

    Returns (x_px, y_px, z_los) or None if the point is on the far side.
    """
    lr = math.radians(lon_rel_deg)
    la = math.radians(lat_c_deg)
    f = float(nav.flattening)

    r = 1.0 / math.sqrt(math.cos(la) ** 2 + (math.sin(la) / (1.0 - f)) ** 2)
    Px = r * math.cos(la) * math.sin(lr)
    Py = r * math.sin(la)
    Pz = r * math.cos(la) * math.cos(lr)

    D = math.radians(float(nav.sub_lat_deg or 0.0))
    cD, sD = math.cos(D), math.sin(D)
    Yp = Py * cD - Pz * sD
    Zp = Py * sD + Pz * cD
    Xp = Px

    if Zp <= 0.05:
        return None

    pa = math.radians(float(nav.north_pa_deg or 0.0))
    cP, sP = math.cos(pa), math.sin(pa)
    Xs = Xp * cP - Yp * sP
    Ys = Xp * sP + Yp * cP

    return nav.xc + Xs * nav.a_eq_px, nav.yc - Ys * nav.a_eq_px, Zp


def _nav(pa: float = 0.0, sub_lat: float = 0.0, a: float = 300.0) -> NavState:
    return NavState(
        xc=500.0,
        yc=400.0,
        a_eq_px=a,
        cm_iii_deg=100.0,
        distance_au=4.3,
        sub_lat_deg=sub_lat,
        north_pa_deg=pa,
    )


# ---------------------------------------------------------------------------
# Cases 1-36 — zero PA. Isolates the oblateness/latitude defect.
# ---------------------------------------------------------------------------
_SUB_LATS = (0.0, -3.3, 3.0)
_LON_RELS = (-50.0, -20.0, 0.0, 35.0)
_LATS = (-30.0, -22.0, 0.0)
# Latitudes where DEFECT A actually bites (it vanishes identically at the equator).
_LATS_OBLATE = (-30.0, -22.0, 45.0)


@pytest.mark.parametrize("sub_lat", _SUB_LATS)
@pytest.mark.parametrize("lon_rel", _LON_RELS)
@pytest.mark.parametrize("lat", _LATS)
def test_zero_pa_longitude_matches_oracle(sub_lat, lon_rel, lat):
    """Longitude is well behaved at PA=0 even with the oblateness defect."""
    nav = _nav(pa=0.0, sub_lat=sub_lat)
    p = oracle_project(lon_rel, lat, nav)
    assert p is not None
    lon_out, _ = px_to_lonlat(p[1], p[0], nav)
    d = abs(wrap_diff(lon_out, wrap_deg(nav.cm_iii_deg + lon_rel)))
    assert d < 0.15, f"dlon={d:.4f}° at sub_lat={sub_lat} lon_rel={lon_rel} lat={lat}"


# DEFECT A — FIXED. px_to_lonlat now solves the LOS/spheroid intersection, so
# the recovered latitude is genuinely planetocentric.
@pytest.mark.parametrize("sub_lat", _SUB_LATS)
@pytest.mark.parametrize("lon_rel", _LON_RELS)
@pytest.mark.parametrize("lat", _LATS_OBLATE)
def test_zero_pa_latitude_matches_oracle(sub_lat, lon_rel, lat):
    nav = _nav(pa=0.0, sub_lat=sub_lat)
    p = oracle_project(lon_rel, lat, nav)
    assert p is not None
    _, lat_out = px_to_lonlat(p[1], p[0], nav)
    assert abs(lat_out - lat) < TOL_LAT_DEG, f"dlat={lat_out - lat:+.4f}deg"


# ---------------------------------------------------------------------------
# Cases 37-72 — non-zero PA. Isolates the rotation-order defect.
# ---------------------------------------------------------------------------
# PA values taken from real SPICE output for this repo's kernels
# (2025-01-10 -> 7.4deg, 2023-09-01 -> 17.7deg, 2024-01-15 -> 20.5deg).
_PAS = (7.4, 17.7, 25.0, 45.0, -15.0, 90.0)
_PA_LON_RELS = (-35.0, 0.0, 25.0)
_PA_LATS = (-30.0, -22.0)


# DEFECT B — FIXED. The PA rotation now happens in the unscaled planet frame
# and a single isotropic plate scale is applied last.
@pytest.mark.parametrize("pa", _PAS)
@pytest.mark.parametrize("lon_rel", _PA_LON_RELS)
@pytest.mark.parametrize("lat", _PA_LATS)
def test_rotated_pa_roundtrip_matches_oracle(pa, lon_rel, lat):
    nav = _nav(pa=pa, sub_lat=-2.5)
    p = oracle_project(lon_rel, lat, nav)
    assert p is not None
    lon_out, lat_out = px_to_lonlat(p[1], p[0], nav)
    dlon = wrap_diff(lon_out, wrap_deg(nav.cm_iii_deg + lon_rel))
    dlat = lat_out - lat
    assert abs(dlon) < TOL_LON_DEG and abs(dlat) < TOL_LAT_DEG, (
        f"PA={pa} lon_rel={lon_rel} lat={lat}: dlon={dlon:+.4f}deg dlat={dlat:+.4f}deg"
    )


def test_pa_projection_is_accurate_at_every_position_angle():
    """
    Regression guard: DEFECT B is fixed, so the worst-case longitude error over
    a dense PA/lon/lat sweep must stay at numerical-noise level.
    """
    worst = 0.0
    for pa in (0.0, 10.0, 25.0, 45.0, 90.0, 135.0, 180.0):
        for lon_rel in (-50.0, -25.0, 0.0, 25.0, 50.0):
            for lat in (-30.0, -22.0, -10.0, 0.0, 20.0):
                nav = _nav(pa=pa, sub_lat=-2.0)
                p = oracle_project(lon_rel, lat, nav)
                if p is None:
                    continue
                lon_out, _ = px_to_lonlat(p[1], p[0], nav)
                worst = max(worst, abs(wrap_diff(lon_out, wrap_deg(nav.cm_iii_deg + lon_rel))))
    assert worst < 1e-6, f"PA longitude error regressed to {worst:.6f}deg"


@pytest.mark.parametrize("lon_rel", _LON_RELS)
def test_equator_latitude_is_exact_when_untilted(lon_rel):
    """DEFECT A vanishes identically at lat=0 with no tilt (sin phi = 0), which
    localises the bug to the oblate radius term rather than to the plate scale.
    With a non-zero sub-observer latitude the tilt mixes the (wrongly scaled)
    z-component back into y, so the error reappears even on the equator."""
    nav = _nav(pa=0.0, sub_lat=0.0)
    p = oracle_project(lon_rel, 0.0, nav)
    assert p is not None
    _, lat_out = px_to_lonlat(p[1], p[0], nav)
    assert abs(lat_out) < TOL_LAT_DEG


def test_pa_error_vanishes_on_a_sphere():
    """
    Proof that DEFECT B is caused by oblateness+rotation, not by the oracle.

    With flattening set to zero the two axis scales coincide, rotation and
    scaling commute, and the engine must agree with the oracle to machine
    precision at every position angle.
    """
    for pa in (0.0, 17.7, 45.0, 90.0, 137.0):
        nav = NavState(
            xc=500.0, yc=400.0, a_eq_px=300.0, flattening=0.0,
            cm_iii_deg=100.0, distance_au=4.3, sub_lat_deg=-2.0, north_pa_deg=pa,
        )
        for lon_rel in (-40.0, 0.0, 30.0):
            for lat in (-30.0, -22.0, 15.0):
                p = oracle_project(lon_rel, lat, nav)
                assert p is not None
                lon_out, lat_out = px_to_lonlat(p[1], p[0], nav)
                assert abs(wrap_diff(lon_out, wrap_deg(nav.cm_iii_deg + lon_rel))) < 1e-6
                assert abs(lat_out - lat) < 1e-6


# ---------------------------------------------------------------------------
# Cases 73-88 — latitude convention and radius of curvature.
# ---------------------------------------------------------------------------
def _true_planetographic(lat_c_deg: float, f: float = FLAT) -> float:
    """phi_g = atan( tan(phi_c) / (1-f)^2 ). Standard IAU relation."""
    return math.degrees(math.atan(math.tan(math.radians(lat_c_deg)) / (1.0 - f) ** 2))


@pytest.mark.parametrize("lat_c", [-30.0, -22.0, -10.0, 0.0, 15.0, 45.0])
def test_planetographic_roundtrip_is_self_consistent(lat_c):
    g = planetocentric_to_planetographic(lat_c)
    c = planetographic_to_planetocentric(g)
    assert abs(c - lat_c) < 1e-6


@pytest.mark.parametrize("lat_c", [-30.0, -22.0, -10.0, 15.0, 45.0])
def test_planetographic_formula_matches_iau(lat_c):
    """
    The docstring in precision_engine claims
        phi_g = atan( (R_eq/R_pol)^2 tan phi_c )
    and the code implements exactly that, which IS the IAU relation
    (R_eq/R_pol = 1/(1-f)). This confirms the *formula* is right, so any
    latitude discrepancy must come from the projector, not this converter.
    """
    got = planetocentric_to_planetographic(lat_c)
    want = _true_planetographic(lat_c)
    assert abs(got - want) < 1e-9, f"lat_c={lat_c}: got {got:.6f} want {want:.6f}"


def test_planetographic_docstring_grs_example_is_wrong():
    """
    DEFECT C (documentation): the precision_engine docstring says
    "-23 planetocentric -> planetographic is about -24 something".
    The IAU relation gives -24.80 for -22 and -25.94 for -23; more
    importantly the literature GRS latitude of -22.4 is PLANETOGRAPHIC,
    which corresponds to -19.82 planetocentric — yet the engine's search
    band and priors are centred on lat0 = -22 planetocentric.
    """
    lit_planetographic = -22.4
    equivalent_planetocentric = planetographic_to_planetocentric(lit_planetographic)
    assert abs(equivalent_planetocentric - (-19.82)) < 0.05
    # The engine's hard-coded planetocentric prior is 2.2deg away from where the
    # literature GRS actually is in planetocentric coordinates.
    assert abs(-22.0 - equivalent_planetocentric) > 2.0


def test_flattening_matches_nasa_fact_sheet():
    assert abs(JUP_REQ_KM - 71492.0) < 1e-9
    assert abs(JUP_RPOL_KM - 66854.0) < 1e-9
    assert abs(FLAT - 0.06487) < 1e-4


# DEFECT D — FIXED. km_per_deg_lat(lat) now returns the meridian ARC LENGTH per
# degree of planetocentric latitude, matching the convention used everywhere in
# this codebase, instead of a latitude-independent constant.
@pytest.mark.parametrize("lat", [0.0, -22.0, -45.0, -90.0])
def test_km_per_deg_lat_is_planetocentric_arc_length(lat):
    """
    Oracle: differentiate the meridian numerically in polar form.
    ds/dphi_c = sqrt((dr/dphi_c)^2 + r^2), with r the planetocentric radius.

    NOTE this is deliberately NOT the geodetic radius of curvature M(phi_g).
    Latitudes in this codebase are planetocentric, and on Jupiter the two
    measures differ by up to 14%, so using M here would be a fresh bug.
    """
    def r(phi):
        return JUP_REQ_KM / math.sqrt(
            math.cos(phi) ** 2 + (math.sin(phi) / (1.0 - FLAT)) ** 2
        )

    p = math.radians(lat)
    h = 1e-7
    dr = (r(p + h) - r(p - h)) / (2 * h)
    want = math.sqrt(dr * dr + r(p) ** 2) * math.pi / 180.0
    got = km_per_deg_lat(lat)
    assert abs(got - want) / want < 1e-6, f"lat={lat}: got {got:.4f} want {want:.4f}"


def test_km_per_deg_lat_is_not_the_old_constant():
    """The old code returned 1166.8 km at every latitude; that is now only true
    at the poles."""
    assert abs(km_per_deg_lat(0.0) - 1247.77) < 0.05
    assert abs(km_per_deg_lat(-22.0) - 1236.86) < 0.05
    assert abs(km_per_deg_lat(-90.0) - 1166.82) < 0.05


@pytest.mark.parametrize("lat", [0.0, -22.0, -45.0])
def test_km_per_deg_lon_oblate_radius(lat):
    """
    DEFECT E — FIXED. km_per_deg_lon now uses the spheroid parallel radius
    r(phi)*cos(phi) instead of R_eq*cos(phi), so it is exact at all latitudes.
    """
    la = math.radians(lat)
    r = JUP_REQ_KM / math.sqrt(math.cos(la) ** 2 + (math.sin(la) / (1.0 - FLAT)) ** 2)
    want = r * math.cos(la) * math.pi / 180.0
    got = km_per_deg_lon(lat)
    rel = abs(got - want) / want
    assert rel < 1e-12, f"lat={lat}: {100*rel:.4f}% error"


# ---------------------------------------------------------------------------
# Cases 89-100 — scalar helpers and the arcsec error budget.
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "x,want",
    [(-10.0, 350.0), (0.0, 0.0), (360.0, 0.0), (370.0, 10.0), (-360.0, 0.0), (720.5, 0.5)],
)
def test_wrap_deg(x, want):
    assert abs(wrap_deg(x) - want) < 1e-9


@pytest.mark.parametrize(
    "a,b,want",
    [(10.0, 350.0, 20.0), (350.0, 10.0, -20.0), (0.0, 0.0, 0.0), (90.0, 270.0, 180.0)],
)
def test_wrap_diff(a, b, want):
    got = wrap_diff(a, b)
    assert abs(abs(got) - abs(want)) < 1e-9


def test_wrap_diff_antipodal_sign_convention():
    """
    Both wrap_diff(180,0) and wrap_diff(0,180) return -180.0 because the
    half-open interval is [-180,180). Not a bug, but callers that use the
    SIGN of a 180deg difference to pick a hemisphere would be misled. Pinned
    so the convention cannot drift silently.
    """
    assert wrap_diff(180.0, 0.0) == -180.0
    assert wrap_diff(0.0, 180.0) == -180.0


def test_sky_error_is_quadrature_sum():
    lat, dist = -22.0, 4.3
    a = deg_to_arcsec_on_sky(0.4, km_per_deg_lon(lat), dist)
    b = deg_to_arcsec_on_sky(0.3, km_per_deg_lat(lat), dist)
    assert abs(sky_error_arcsec(0.4, 0.3, lat, dist) - math.hypot(a, b)) < 1e-12


def test_sky_error_scales_inversely_with_distance():
    near = sky_error_arcsec(0.5, 0.5, -22.0, 4.0)
    far = sky_error_arcsec(0.5, 0.5, -22.0, 6.0)
    assert abs(near / far - 1.5) < 1e-9


def test_one_arcsec_is_about_three_degrees_of_longitude():
    """Sanity anchor: at 4.3 AU, 1deg of GRS-latitude longitude is ~0.37\"."""
    got = deg_to_arcsec_on_sky(1.0, km_per_deg_lon(-22.0), 4.3)
    assert 0.3 < got < 0.45, got


def test_apparent_diameter_consistency():
    """Jupiter at 4.3 AU should subtend ~45.9\" equatorially."""
    dist_km = 4.3 * 149597870.7
    diam = math.degrees(2 * JUP_REQ_KM / dist_km) * 3600.0
    assert 45.0 < diam < 47.0, diam

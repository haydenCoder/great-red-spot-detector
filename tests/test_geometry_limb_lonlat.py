"""
Geometry regression: limb / orthographic lon-lat / flips / PA.

Guarantees:
  • px_to_lonlat is inverse of make_cylindrical projection
  • L3 > CM maps to +x (right) when PA=0, N-up
  • N is -y (image up)
  • E–W mirror negates lon_rel
  • map_dark_centroid recovers planted GRS lon
  • planetographic round-trip
"""
from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

import numpy as np

APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from precision_engine import (  # noqa: E402
    NavState,
    fit_limb_nav,
    make_cylindrical,
    planetocentric_to_planetographic,
    planetographic_to_planetocentric,
    px_to_lonlat,
    wrap_deg,
    wrap_diff,
    _map_dark_centroid,
)


def _forward_xy(lon_iii: float, lat: float, nav: NavState):
    """Same orthographic contract as make_cylindrical (single point)."""
    lon_rel = wrap_diff(lon_iii, nav.cm_iii_deg)
    lon_r = math.radians(lon_rel)
    lat_r = math.radians(lat)
    Xe = math.cos(lat_r) * math.sin(lon_r)
    Ye = math.sin(lat_r)
    Ze = math.cos(lat_r) * math.cos(lon_r)
    D = math.radians(float(nav.sub_lat_deg or 0.0))
    cD, sD = math.cos(D), math.sin(D)
    Yp = Ye * cD - Ze * sD
    Zp = Ye * sD + Ze * cD
    Xp = Xe
    if Zp <= 0.02:
        return None
    pa = math.radians(float(nav.north_pa_deg or 0.0))
    cP, sP = math.cos(pa), math.sin(pa)
    Xsky = Xp * cP - Yp * sP
    Ysky = Xp * sP + Yp * cP
    xs = nav.xc + Xsky * nav.a_eq_px
    ys = nav.yc - Ysky * nav.b_pol_px
    return xs, ys


class TestWrap(unittest.TestCase):
    def test_wrap(self):
        self.assertAlmostEqual(wrap_deg(-10.0), 350.0)
        self.assertAlmostEqual(wrap_diff(10.0, 350.0), 20.0)
        self.assertAlmostEqual(wrap_diff(350.0, 10.0), -20.0)


class TestPlanetographic(unittest.TestCase):
    def test_roundtrip(self):
        for lat in (-30.0, -22.0, -10.0, 0.0, 15.0, 45.0):
            g = planetocentric_to_planetographic(lat)
            c = planetographic_to_planetocentric(g)
            self.assertAlmostEqual(c, lat, places=6)
        self.assertLess(planetocentric_to_planetographic(-22.0), -22.0)


class TestOrthographicContract(unittest.TestCase):
    def test_roundtrip_zero_pa(self):
        nav = NavState(
            xc=500, yc=400, a_eq_px=300, cm_iii_deg=100.0, distance_au=5.0
        )
        for lon, lat in (
            (100.0, -22.0),
            (120.0, -22.0),
            (80.0, -15.0),
            (130.0, -30.0),
            (70.0, 10.0),
        ):
            xy = _forward_xy(lon, lat, nav)
            self.assertIsNotNone(xy)
            lon2, lat2 = px_to_lonlat(xy[1], xy[0], nav)
            self.assertLess(abs(wrap_diff(lon2, lon)), 0.05)
            self.assertLess(abs(lat2 - lat), 0.05)

    def test_roundtrip_pa_sublat(self):
        for pa, D in ((15.0, 0.0), (0.0, -3.5), (25.0, -3.2), (-40.0, 2.0), (90.0, 0.0)):
            nav = NavState(
                xc=500,
                yc=400,
                a_eq_px=300,
                cm_iii_deg=200.0,
                distance_au=5.0,
                sub_lat_deg=D,
                north_pa_deg=pa,
            )
            lon, lat = 220.0, -22.0
            xy = _forward_xy(lon, lat, nav)
            lon2, lat2 = px_to_lonlat(xy[1], xy[0], nav)
            self.assertLess(abs(wrap_diff(lon2, lon)), 0.08, f"pa={pa} D={D}")
            self.assertLess(abs(lat2 - lat), 0.08, f"pa={pa} D={D}")

    def test_lon_lat_image_sides(self):
        """Internal convention: N-up, L3>CM → +x (right)."""
        nav = NavState(xc=500, yc=400, a_eq_px=300, cm_iii_deg=100.0)
        x_hi, _ = _forward_xy(120.0, -22.0, nav)
        x_lo, _ = _forward_xy(80.0, -22.0, nav)
        _, y_n = _forward_xy(100.0, 20.0, nav)
        _, y_s = _forward_xy(100.0, -20.0, nav)
        self.assertGreater(x_hi, nav.xc)
        self.assertLess(x_lo, nav.xc)
        self.assertLess(y_n, nav.yc)
        self.assertGreater(y_s, nav.yc)

    def test_ew_mirror_negates_lon_rel(self):
        nav = NavState(xc=500, yc=400, a_eq_px=300, cm_iii_deg=100.0)
        x, y = _forward_xy(120.0, -22.0, nav)
        x_flip = 2 * nav.xc - x
        lon_f, _ = px_to_lonlat(y, x_flip, nav)
        self.assertLess(abs(wrap_diff(lon_f, 80.0)), 0.5)


class TestLimbAndMap(unittest.TestCase):
    def _disk_with_grs(self):
        h = w = 800
        yy, xx = np.mgrid[0:h, 0:w]
        nav = NavState(xc=400, yc=400, a_eq_px=300, cm_iii_deg=50.0, distance_au=5.0)
        true_lon, true_lat = 70.0, -22.0
        gx, gy = _forward_xy(true_lon, true_lat, nav)
        img = np.zeros((h, w), dtype=np.float64)
        rr = np.sqrt(
            ((xx - nav.xc) / nav.a_eq_px) ** 2 + ((yy - nav.yc) / nav.b_pol_px) ** 2
        )
        img[rr < 1.0] = 1.0
        img -= 0.6 * np.exp(-(((xx - gx) ** 2 + (yy - gy) ** 2) / (2 * 18.0**2)))
        img = np.clip(img, 0, 1)
        return img, nav, true_lon, true_lat, gx, gy

    def test_limb_fit_and_px(self):
        img, nav0, true_lon, true_lat, gx, gy = self._disk_with_grs()
        nav = fit_limb_nav(img, cm_iii_deg=50.0, distance_au=5.0)
        self.assertLess(abs(nav.xc - 400), 5)
        self.assertLess(abs(nav.yc - 400), 5)
        self.assertLess(abs(nav.a_eq_px - 300), 15)
        lon, lat = px_to_lonlat(gy, gx, nav)
        self.assertLess(abs(wrap_diff(lon, true_lon)), 2.0)
        self.assertLess(abs(lat - true_lat), 2.0)

    def test_map_dark_centroid(self):
        img, nav, true_lon, true_lat, _, _ = self._disk_with_grs()
        cyl = make_cylindrical(img, nav, width=360, height=180)
        md = _map_dark_centroid(cyl, nav, lat0=-22.0)
        self.assertLess(abs(wrap_diff(md["lon_iii_deg"], true_lon)), 3.0)
        self.assertLess(abs(md["lat_deg"] - true_lat), 3.0)

    def test_pa180_recovery(self):
        img, nav0, true_lon, true_lat, gx, gy = self._disk_with_grs()
        h, w = img.shape
        img180 = np.rot90(img, 2)
        gx2, gy2 = (w - 1 - gx), (h - 1 - gy)
        nav_bad = fit_limb_nav(img180, cm_iii_deg=50.0)
        lon_bad, lat_bad = px_to_lonlat(gy2, gx2, nav_bad)
        # Without PA, recovery should be wrong
        bad = abs(wrap_diff(lon_bad, true_lon)) > 5 or abs(lat_bad - true_lat) > 5
        self.assertTrue(bad)
        nav_ok = NavState(
            xc=nav_bad.xc,
            yc=nav_bad.yc,
            a_eq_px=nav_bad.a_eq_px,
            cm_iii_deg=50.0,
            north_pa_deg=180.0,
        )
        lon_ok, lat_ok = px_to_lonlat(gy2, gx2, nav_ok)
        self.assertLess(abs(wrap_diff(lon_ok, true_lon)), 3.0)
        self.assertLess(abs(lat_ok - true_lat), 3.0)


if __name__ == "__main__":
    unittest.main()

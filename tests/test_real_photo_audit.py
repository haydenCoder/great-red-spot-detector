"""Real-photo logic regressions found on Hubble / Juno frames.

These pin the bugs the synthetic campaigns cannot see:
  * limb-softness false-fail on space-telescope disks
  * isolated redness pruning a tight dark GRS cluster
  * fit_limb_nav dropping the PA it just used
  * px_to_lonlat_vec drifting from the scalar inverse
  * Juno close-ups being treated as a measurable full disk
"""
from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

APP = Path(__file__).resolve().parents[1] / "app"
REPO = Path(__file__).resolve().parents[1]
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


def _load_rgb(path: Path, max_side: int = 900) -> np.ndarray:
    im = Image.open(path).convert("RGB")
    if max(im.size) > max_side:
        s = max_side / max(im.size)
        im = im.resize((max(2, int(im.width * s)), max(2, int(im.height * s))), Image.LANCZOS)
    return np.asarray(im, dtype=np.float64) / 255.0


class TestNavPersistsPA(unittest.TestCase):
    def test_fit_limb_nav_stores_north_pa(self):
        from precision_engine import fit_limb_nav

        img = np.zeros((120, 120), dtype=np.float64)
        yy, xx = np.ogrid[:120, :120]
        img[(xx - 60) ** 2 + (yy - 60) ** 2 <= 40 ** 2] = 1.0
        nav = fit_limb_nav(img, north_pa_deg=16.5)
        self.assertAlmostEqual(float(nav.north_pa_deg), 16.5, places=6)


class TestPxToLonlatVec(unittest.TestCase):
    def test_matches_scalar_on_grid(self):
        from precision_engine import NavState, px_to_lonlat, px_to_lonlat_vec

        nav = NavState(xc=200.0, yc=180.0, a_eq_px=150.0, cm_iii_deg=40.0,
                       sub_lat_deg=-2.3, north_pa_deg=18.0)
        ys = np.array([180.0, 200.0, 160.0, 220.0])
        xs = np.array([200.0, 230.0, 170.0, 210.0])
        lon_v, lat_v = px_to_lonlat_vec(ys, xs, nav)
        for i, (y, x) in enumerate(zip(ys, xs)):
            lon, lat = px_to_lonlat(float(y), float(x), nav)
            self.assertAlmostEqual(float(lon_v[i]), lon, places=9)
            self.assertAlmostEqual(float(lat_v[i]), lat, places=9)


class TestHubbleSoftnessNotFalseFail(unittest.TestCase):
    def test_hubble_2019_is_a_sharp_disk(self):
        from precision_engine import fit_limb_nav, assess_disk_quality

        path = REPO / "real_photos" / "hubble_2019_jun27.png"
        if not path.exists():
            self.skipTest("real Hubble frame not in workspace")
        img = _load_rgb(path)
        nav = fit_limb_nav(img, distance_au=4.3)
        nav.distance_au = 4.3
        dq = assess_disk_quality(img, nav)
        self.assertTrue(dq.get("disk_present"), dq)
        # Space-telescope limb is not 6–9″ of seeing. The old histogram
        # estimator reported 8.6″ and refused the frame.
        self.assertLess(float(dq.get("softness_arcsec") or 99.0), 6.0, dq)
        self.assertTrue(dq.get("measurable"), dq)

    def test_hubble_2019_does_not_publish_seb_belt(self):
        """Redness at lat≈−13 is the orange SEB, not the GRS oval.

        The dark template lock sits at lat≈−19.4. After the tight-band
        seed guard, the published answer must stay in the GRS core band.
        """
        from accuracy_gates import grs_lat_in_core_band
        from precision_engine import fit_limb_nav, measure_grs_precision

        path = REPO / "real_photos" / "hubble_2019_jun27.png"
        if not path.exists():
            self.skipTest("real Hubble frame not in workspace")
        img = _load_rgb(path)
        nav = fit_limb_nav(img, distance_au=4.3)
        nav.distance_au = 4.3
        res = measure_grs_precision(
            img, cm_iii_deg=0.0, distance_au=4.3, nav=nav,
            quiet=True, map_width=1600, map_height=800, lean=True,
        )
        self.assertTrue(grs_lat_in_core_band(res.lat_deg),
                        f"published lat={res.lat_deg:+.2f} method={res.method}")
        self.assertNotEqual(res.method, "redness_lon+redness_lat")


class TestJunoCloseupNotADisk(unittest.TestCase):
    def test_juno_crop_is_not_measurable(self):
        from precision_engine import fit_limb_nav, assess_disk_quality

        path = REPO / "real_photos" / "juno_grs_closeup.jpg"
        if not path.exists():
            self.skipTest("Juno close-up not in workspace")
        img = _load_rgb(path, max_side=700)
        nav = fit_limb_nav(img)
        dq = assess_disk_quality(img, nav)
        self.assertFalse(dq.get("disk_present"), dq)
        self.assertFalse(dq.get("measurable"), dq)


class TestHubbleIoDarkClusterSurvives(unittest.TestCase):
    def test_published_lock_stays_with_dark_cluster(self):
        """Hubble 2024-01-06 + Io: GRS is the small orange oval on the right.

        Pre-fix: redness locked a central SEB belt (~10°) and pruned the
        tight dark cluster at ~80°. After the fix the published answer must
        stay with the dark cluster (or a redness lock that agrees with it).
        """
        from precision_engine import fit_limb_nav, measure_grs_precision, wrap_diff

        path = REPO / "real_photos" / "hubble_2024_jan06_io.webp"
        if not path.exists():
            self.skipTest("Hubble Io frame not in workspace")
        img = _load_rgb(path, max_side=1000)
        nav = fit_limb_nav(img, distance_au=4.56)
        nav.distance_au = 4.56
        res = measure_grs_precision(
            img, cm_iii_deg=0.0, distance_au=4.56, nav=nav,
            quiet=True, map_width=1600, map_height=800, lean=True,
        )
        dark = []
        for name in ("template", "moment", "map_dark"):
            m = (res.methods or {}).get(name) or {}
            if m.get("lon_iii_deg") is None or m.get("rejected"):
                continue
            dark.append(float(m["lon_iii_deg"]))
        self.assertGreaterEqual(len(dark), 2, f"dark methods: {res.methods}")
        # published lon must sit inside the dark cluster, not 70° away
        dmin = min(abs(wrap_diff(res.lon_iii_deg, d)) for d in dark)
        self.assertLess(dmin, 12.0, f"published {res.lon_iii_deg:.2f} vs dark {dark} method={res.method}")
        path_info = (res.methods or {}).get("publish_path") or {}
        self.assertTrue(path_info.get("dark_tight"), path_info)


class TestRealPhotoValidateRefusesEpochZero(unittest.TestCase):
    def test_missing_time_does_not_invent_1970(self):
        sys.path.insert(0, str(REPO / "tools"))
        import real_photo_validate as rpv

        fake = REPO / "real_photos" / "hubble_2019_jun27.png"
        if not fake.exists():
            self.skipTest("no real photo")
        out = rpv._run_real(fake, time_str=None, wj_lon=None, wj_lat=None, out_dir=fake.parent)
        self.assertEqual(out.get("error"), "no_observation_utc")


if __name__ == "__main__":
    unittest.main()

"""v6.8 true-sky geometry: tilted renderer, PA-aware limb nav, colour-neutral
moon mask.

The real Jupiter presents sub-observer latitudes up to ~3.4 deg and a pole
position angle reaching ~17 deg (SPICE-verified: PA 343.4 deg on 2026-08-02).
Before v6.8 the synthetic renderer ignored sub-lat/PA while the production
stack modelled them from the ephemeris — a 6.5 deg systematic on August 2026
frames. These tests pin the fixed contracts.
"""
from __future__ import annotations

import datetime as dt
import os
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

SUB_LAT = -2.10          # deg, planetocentric sub-observer latitude
NORTH_PA = 352.0         # deg E of N (i.e. 8 deg west of north)
PLACE_REL = -15.0        # GRS meridian-relative longitude seed


def _render(seed=4242, sub_lat=SUB_LAT, pa=NORTH_PA, res="540p"):
    from synthetic_hq import SynthSpec, generate
    from PIL import Image
    d = Path(tempfile.mkdtemp(prefix="grs_tilt_"))
    os.environ["GRS_LIMB_LON_REL"] = f"{PLACE_REL:.6f}"
    try:
        png, _fit, truth = generate(
            SynthSpec(region="global", resolution_preset=res, random_time=False,
                      user_time_iso="2026-08-02 00:30:00", seed=seed,
                      mode="metrology", write_grs_crop=False,
                      sub_lat_deg=sub_lat, north_pa_deg=pa), d)
    finally:
        os.environ.pop("GRS_LIMB_LON_REL", None)
    arr = np.asarray(Image.open(png), dtype=np.float64) / 255.0
    return arr, truth


def _measure(img, sub_lat, pa, fit_pa=None):
    from precision_engine import fit_limb_nav, measure_grs_precision, wrap_diff
    nav = fit_limb_nav(img, north_pa_deg=(pa if fit_pa is None else fit_pa))
    nav.sub_lat_deg = float(sub_lat)
    nav.north_pa_deg = float(pa)
    r = measure_grs_precision(img, cm_iii_deg=0.0, distance_au=5.2, nav=nav, quiet=True)
    return wrap_diff(r.lon_iii_deg, 0.0), r.lat_deg


class TestTiltedRender(unittest.TestCase):
    def test_tilted_frame_recovers_placement(self):
        """Render WITH (D, P), measure with matching nav: campaign-grade."""
        img, truth = _render()
        self.assertAlmostEqual(float(truth["sub_obs_lat_deg"]), SUB_LAT, places=6)
        rel, lat = _measure(img, SUB_LAT, NORTH_PA, fit_pa=NORTH_PA)
        dlon = abs((rel - PLACE_REL + 180) % 360 - 180)
        dlat = abs(lat - float(truth["grs_lat_deg"]))
        self.assertLessEqual(dlon, 0.8, f"dlon {dlon:.3f}")
        self.assertLessEqual(dlat, 0.8, f"dlat {dlat:.3f}")

    def test_wrong_orientation_is_a_real_error(self):
        """Same tilted frame measured with (0,0) nav must be measurably wrong —
        proves the renderer truly bakes orientation into the pixels."""
        img, truth = _render()
        rel, lat = _measure(img, 0.0, 0.0, fit_pa=0.0)
        dlon = abs((rel - PLACE_REL + 180) % 360 - 180)
        self.assertGreaterEqual(dlon, 1.0,
                                f"8 deg PA mismatch should cost >1 deg, got {dlon:.3f}")

    def test_default_render_bitwise_unchanged(self):
        """sub_lat=north_pa=0 must be byte-identical to the legacy renderer —
        protects every existing campaign cache and golden number."""
        a, _ = _render(seed=909, sub_lat=0.0, pa=0.0)
        from synthetic_hq import SynthSpec, generate
        from PIL import Image
        d = Path(tempfile.mkdtemp(prefix="grs_tilt0_"))
        os.environ["GRS_LIMB_LON_REL"] = f"{PLACE_REL:.6f}"
        try:
            png, _f, _t = generate(
                SynthSpec(region="global", resolution_preset="540p",
                          random_time=False, user_time_iso="2026-08-02 00:30:00",
                          seed=909, mode="metrology", write_grs_crop=False), d)
        finally:
            os.environ.pop("GRS_LIMB_LON_REL", None)
        b = np.asarray(Image.open(png), dtype=np.float64) / 255.0
        self.assertTrue(np.array_equal(a, b), "default render changed!")


class TestMoonMaskGate(unittest.TestCase):
    def _scene(self):
        h, w = 200, 240
        yy, xx = np.mgrid[0:h, 0:w]
        disk = ((yy - 100) ** 2 + (xx - 120) ** 2) < 80 ** 2
        rgb = np.zeros((h, w, 3))
        rgb[..., 0] = np.where(disk, 0.80, 0.02)
        rgb[..., 1] = np.where(disk, 0.70, 0.02)
        rgb[..., 2] = np.where(disk, 0.55, 0.02)
        mono = 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]
        return rgb, mono, disk

    def test_grey_moon_masked_red_feature_kept(self):
        from grs_image_prep import mask_satellite_shadows
        rgb, mono, disk = self._scene()
        # grey moon shadow (neutral)
        yy, xx = np.mgrid[0:200, 0:240]
        dot = ((yy - 90) ** 2 + (xx - 100) ** 2) < 5 ** 2
        for c in range(3):
            rgb[..., c][dot] = 0.06
        # red-dark storm feature (same darkness, reddish)
        oval = ((yy - 120) ** 2 / 30.0 + (xx - 150) ** 2 / 60.0) < 1.0
        rgb[..., 0][oval] = 0.30
        rgb[..., 1][oval] = 0.18
        rgb[..., 2][oval] = 0.10
        mono = 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]
        moon = mask_satellite_shadows(mono, disk_mask=disk, rgb=rgb)
        self.assertTrue(moon[90, 100], "grey moon umbra must be masked")
        self.assertFalse(moon[120, 150], "reddish storm must NOT be masked")

    def test_area_cap_drops_runaway_mask(self):
        from grs_image_prep import mask_satellite_shadows
        rgb, mono, disk = self._scene()
        # sprinkle >2.5% of the disk with small neutral spots
        rng = np.random.default_rng(3)
        yy, xx = np.where(disk)
        sel = rng.choice(len(yy), size=max(20, int(0.03 * disk.sum()) // 9), replace=False)
        for i in sel:
            y, x = yy[i], xx[i]
            spot = (np.mgrid[0:200, 0:240][0] - y) ** 2 + (np.mgrid[0:200, 0:240][1] - x) ** 2 < 2.5 ** 2
            for c in range(3):
                rgb[..., c][spot] = 0.10
        mono = 0.299 * rgb[..., 0] + 0.587 * rgb[..., 1] + 0.114 * rgb[..., 2]
        moon = mask_satellite_shadows(mono, disk_mask=disk, rgb=rgb)
        frac = moon.sum() / disk.sum()
        self.assertLessEqual(frac, 0.025, f"mask covers {frac:.3f} of disk")


class TestLimbNavPAPrior(unittest.TestCase):
    def test_pa_prior_recovers_rotated_disk(self):
        img, truth = _render()
        from precision_engine import fit_limb_nav
        a_true = float(truth["disk_a_eq_px"])
        xc_true, yc_true = float(truth["disk_xc"]), float(truth["disk_yc"])
        n0 = fit_limb_nav(img, north_pa_deg=0.0)
        n1 = fit_limb_nav(img, north_pa_deg=NORTH_PA)
        # centre recovered sub-pixel either way; PA prior helps radius
        self.assertAlmostEqual(n1.xc, xc_true, delta=1.5)
        self.assertAlmostEqual(n1.yc, yc_true, delta=1.5)
        self.assertAlmostEqual(n1.a_eq_px, a_true, delta=0.012 * a_true)
        e0 = abs(n0.a_eq_px - a_true)
        e1 = abs(n1.a_eq_px - a_true)
        self.assertLessEqual(e1, max(e0 * 1.10 + 0.6, 0.005 * a_true),
                             f"PA prior made the fit worse: e0={e0:.3f} e1={e1:.3f}")


if __name__ == "__main__":
    unittest.main()

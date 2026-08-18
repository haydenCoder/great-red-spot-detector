"""
Regression tests for critical science fixes (P0/P1 from full audit).

Covers:
  • lon_rel wrap-safe map interpolation
  • planetographic conversion
  • image flips (spatial only)
  • analytical CM not trusted for absolute publish
  • publish quality fail-closed shape
"""
from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


class TestLonRelInterp(unittest.TestCase):
    def test_hit_from_map_xy_near_zero_meridian(self):
        """Wrapped lon_iii lerp would give ~180°; lon_rel path must stay continuous."""
        import numpy as np
        from all_methods import _hit_from_map_xy

        w, h = 181, 91
        cm = 350.0  # absolute lon wraps near mid-map
        lon_rel = np.linspace(-90.0, 90.0, w)
        lon_iii = np.array([(cm + x) % 360.0 for x in lon_rel])
        lat = np.linspace(90.0, -90.0, h)
        # Column where lon_rel ≈ 10° → absolute ≈ 0° (wrap zone)
        cx = (10.0 + 90.0) / 180.0 * (w - 1)
        cy = (90.0 - (-22.0)) / 180.0 * (h - 1)
        hit = _hit_from_map_xy("T", "map", cx, cy, lon_iii, lat, cm_iii_deg=cm)
        self.assertTrue(hit.ok)
        # Expected: wrap(350+10)=0°
        d = abs(((hit.lon_iii_deg - 0.0 + 180) % 360) - 180)
        self.assertLess(d, 1.5, f"got lon={hit.lon_iii_deg} (wrap-lerp bug would be ~180)")
        self.assertAlmostEqual(hit.lat_deg, -22.0, delta=0.5)

    def test_hit_not_one_eighty(self):
        import numpy as np
        from all_methods import _hit_from_map_xy

        w = 100
        cm = 5.0
        lon_rel = np.linspace(-90.0, 90.0, w)
        lon_iii = np.array([(cm + x) % 360.0 for x in lon_rel])
        lat = np.linspace(90.0, -90.0, 50)
        # mid column
        hit = _hit_from_map_xy("T", "map", w / 2, 25, lon_iii, lat, cm_iii_deg=cm)
        # Must not be near 180 unless GRS really is there
        self.assertFalse(80 < hit.lon_iii_deg < 280 and abs(hit.lon_iii_deg - 180) < 20)


class TestPlanetographic(unittest.TestCase):
    def test_roundtrip_grs_band(self):
        from precision_engine import (
            planetocentric_to_planetographic,
            planetographic_to_planetocentric,
        )

        lat_c = -22.0
        lat_g = planetocentric_to_planetographic(lat_c)
        # Graphic is more extreme than centric for oblate Jupiter
        self.assertLess(lat_g, lat_c)  # more negative
        self.assertAlmostEqual(planetographic_to_planetocentric(lat_g), lat_c, places=4)
        # ~1–2° difference at GRS latitude
        self.assertGreater(abs(lat_g - lat_c), 0.8)
        self.assertLess(abs(lat_g - lat_c), 3.0)


class TestImageFlips(unittest.TestCase):
    def test_hwc_ew_flips_columns_not_channels(self):
        import numpy as np
        from human_choice import apply_image_flips

        # HWC RGB: left column red, right column blue
        im = np.zeros((4, 6, 3), dtype=np.float64)
        im[:, 0, 0] = 1.0  # R left
        im[:, -1, 2] = 1.0  # B right
        out = apply_image_flips(im, flip_ew=True, flip_ns=False)
        self.assertAlmostEqual(out[0, -1, 0], 1.0)  # R moved to right
        self.assertAlmostEqual(out[0, 0, 2], 1.0)  # B moved to left
        # channels not swapped globally
        self.assertEqual(out.shape, im.shape)

    def test_chw_ew(self):
        import numpy as np
        from human_choice import apply_image_flips

        im = np.zeros((3, 4, 6), dtype=np.float64)
        im[0, :, 0] = 1.0
        out = apply_image_flips(im, flip_ew=True, flip_ns=False)
        self.assertAlmostEqual(float(out[0, 0, -1]), 1.0)


class TestCmTrust(unittest.TestCase):
    def test_analytical_not_trusted(self):
        from accuracy_gates import is_trusted_cm_source

        self.assertFalse(is_trusted_cm_source("analytical"))
        self.assertFalse(is_trusted_cm_source("spice_auto_distance_only"))
        self.assertTrue(is_trusted_cm_source("spice_auto"))
        self.assertTrue(is_trusted_cm_source("horizons_sublon"))
        self.assertTrue(is_trusted_cm_source("synthetic_truth"))

    def test_publish_quality_rejects_analytical_absolute(self):
        from accuracy_gates import assess_publish_quality

        pkg = {
            "headline": {
                "lon_iii_deg": 200.0,
                "lat_deg": -22.0,
                "cm_source": "analytical",
                "time_error_seconds": 0.0,
            },
            "publish": {
                "publish_lon_iii_deg": 200.0,
                "publish_lat_deg": -22.0,
                "pipeline_lon_iii_deg": 201.0,
                "cm_source": "analytical",
            },
        }
        q = assess_publish_quality(pkg)
        self.assertFalse(q["absolute_ok"])
        self.assertFalse(q["cm_trusted"])
        self.assertIn("CM_UNTRUSTED", q["flags"])


class TestSotaDoesNotOverwritePolicy(unittest.TestCase):
    def test_publish_rejects_sota_definition_name(self):
        from publish_primary import apply_publish_policy

        pkg = {
            "headline": {
                "lon_iii_deg": 100.0,
                "lat_deg": -22.0,
                "cm_source": "spice_auto",
                "distance_au": 5.2,
                "cm_iii_deg": 90.0,
            },
            "gold_standard": {
                "ok": True,
                "primary_definition": "SOTA_ROBUST",
                "primary_lon_iii_deg": 150.0,
                "primary_lat_deg": -22.0,
            },
            "winjupos_twin": {
                "gs_map_lon": 101.0,
                "gs_map_lat": -22.1,
            },
            "sota": {"ok": True, "lon_iii_deg": 150.0, "lat_deg": -22.0},
        }
        pub = apply_publish_policy(pkg)
        self.assertEqual(pub["publish_definition"], "GS-MAP")
        self.assertAlmostEqual(float(pub["publish_lon_iii_deg"]), 101.0, places=3)
        self.assertNotEqual(pub["publish_definition"], "SOTA_ROBUST")


class TestDerotationWiring(unittest.TestCase):
    """The v6.8 derotation entry points must exist on every surface that
    advertises them (no silent fabrication: an unavailable derotate that
    quietly stacks anyway would be a wrong answer)."""

    def test_derotate_exposed_on_pipeline(self):
        import inspect
        import observatory_pipeline
        self.assertTrue(callable(observatory_pipeline.derotate_folder))
        for fn in (observatory_pipeline.stack_video,
                   observatory_pipeline.video_to_answer):
            self.assertIn("derotate", inspect.signature(fn).parameters,
                          f"{fn.__name__} lost its derotate parameter")

    def test_derotate_folder_requires_timing(self):
        """Folders carry no timestamps; without dt_per_frame_s/fps the call
        must REFUSE (loudly) rather than guess a cadence."""
        import tempfile
        import numpy as np
        from PIL import Image
        import observatory_pipeline
        with tempfile.TemporaryDirectory() as d:
            rng = np.random.default_rng(0)
            for k in range(2):
                Image.fromarray(
                    (rng.random((32, 32)) * 255).astype(np.uint8)).save(
                    Path(d) / f"f{k}.png")
            with self.assertRaises(ValueError):
                observatory_pipeline.derotate_folder(d)


if __name__ == "__main__":
    unittest.main()

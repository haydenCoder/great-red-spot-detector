"""Unit tests for accuracy_gates (real shipped helpers)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


class TestAccuracyGates(unittest.TestCase):
    def test_trusted_cm_sources(self):
        from accuracy_gates import is_trusted_cm_source

        self.assertTrue(is_trusted_cm_source("spice_auto"))
        self.assertTrue(is_trusted_cm_source("horizons+spice_auto"))
        self.assertTrue(is_trusted_cm_source("synthetic_truth"))
        self.assertTrue(is_trusted_cm_source("winjupos_cm_table"))
        self.assertFalse(is_trusted_cm_source("analytical"))
        self.assertFalse(is_trusted_cm_source(""))

    def test_grs_lat_bands(self):
        from accuracy_gates import grs_lat_in_core_band, grs_lat_in_wide_band

        self.assertTrue(grs_lat_in_core_band(-22.0))
        self.assertFalse(grs_lat_in_core_band(5.0))
        self.assertTrue(grs_lat_in_wide_band(-30.0))
        self.assertFalse(grs_lat_in_wide_band(20.0))

    def test_timing_uncertainty(self):
        from accuracy_gates import timing_longitude_uncertainty_deg

        # ~1 minute → ~0.6°
        u = timing_longitude_uncertainty_deg(60.0)
        self.assertGreater(u, 0.55)
        self.assertLess(u, 0.65)

    def test_lon_outlier_reject(self):
        from accuracy_gates import reject_lon_outliers

        methods = {
            "a": {"lon_iii_deg": 100.0, "lat_deg": -22.0},
            "b": {"lon_iii_deg": 101.0, "lat_deg": -22.0},
            "c": {"lon_iii_deg": 200.0, "lat_deg": -22.0},  # wrong feature
        }
        kept, rej, med = reject_lon_outliers(methods, max_delta_deg=18.0)
        self.assertIn("c", rej)
        self.assertIn("a", kept)
        self.assertIn("b", kept)
        self.assertIsNotNone(med)

    def test_assess_publish_quality_good(self):
        from accuracy_gates import assess_publish_quality

        pkg = {
            "headline": {
                "lon_iii_deg": 200.0,
                "lat_deg": -22.0,
                "cm_source": "spice_auto",
                "time_error_seconds": 0.0,
            },
            "publish": {
                "publish_lon_iii_deg": 200.0,
                "publish_lat_deg": -22.0,
                "pipeline_lon_iii_deg": 201.0,
                "cm_source": "spice_auto",
                "limb_outline_sky_spread_arcsec": 0.8,
                "definition_lon_spread_deg": 2.0,
            },
        }
        q = assess_publish_quality(pkg)
        self.assertTrue(q["publish_ok"])
        self.assertTrue(q["absolute_ok"])
        self.assertTrue(q["cm_trusted"])
        self.assertIn(q["grade"], ("GOOD", "FAIR", "CAUTION"))

    def test_assess_publish_quality_bad_lat(self):
        from accuracy_gates import assess_publish_quality

        pkg = {
            "publish": {
                "publish_lon_iii_deg": 100.0,
                "publish_lat_deg": 15.0,  # EZ — not GRS
                "pipeline_lon_iii_deg": 100.0,
                "cm_source": "spice_auto",
            },
            "headline": {},
        }
        q = assess_publish_quality(pkg)
        self.assertFalse(q["publish_ok"])
        self.assertIn("LAT_OUT_OF_BAND", q["flags"])
        self.assertEqual(q["grade"], "REJECT")

    def test_prefer_red_channel(self):
        import numpy as np
        from accuracy_gates import prefer_red_channel

        hwc = np.zeros((10, 10, 3), dtype=np.float64)
        hwc[..., 0] = 1.0
        r = prefer_red_channel(hwc)
        self.assertEqual(r.shape, (10, 10))
        self.assertAlmostEqual(float(r.mean()), 1.0)

    def test_publish_policy_attaches_quality(self):
        from publish_primary import apply_publish_policy

        pkg = {
            "headline": {
                "lon_iii_deg": 210.0,
                "lat_deg": -22.0,
                "distance_au": 5.0,
                "cm_iii_deg": 200.0,
                "cm_source": "spice_auto",
            },
            "research_grade": {
                "lon_bias_corrected_deg": 210.0,
                "lat_bias_corrected_deg": -22.0,
            },
            "winjupos_twin": {
                "gs_map_lon": 210.5,
                "gs_map_lat": -22.1,
                "cm_source": "spice_auto",
                "limb_sky_spread_arcsec": 1.0,
            },
        }
        pub = apply_publish_policy(pkg)
        self.assertIn("quality", pub)
        self.assertIn("publish_quality", pkg)
        self.assertTrue(pkg["publish_quality"]["cm_trusted"])


if __name__ == "__main__":
    unittest.main()

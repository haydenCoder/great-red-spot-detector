"""WinJUPOS+ desk-parity export tests."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


class TestWinJUPOSPlus(unittest.TestCase):
    def test_build_block_planetographic(self):
        from winjupos_plus import build_winjupos_plus_block

        pkg = {
            "headline": {
                "lon_iii_deg": 100.0,
                "lat_deg": -22.0,
                "cm_source": "spice_auto",
                "cm_iii_deg": 90.0,
                "distance_au": 5.2,
            },
            "publish": {
                "publish_definition": "GS-MAP",
                "publish_lon_iii_deg": 100.0,
                "publish_lat_deg": -22.0,
                "cm_source": "spice_auto",
                "cm_iii_deg": 90.0,
                "distance_au": 5.2,
                "winjupos_equality": {"agreement": "NO_MANUAL_PICK"},
            },
            "publish_quality": {"cm_trusted": True},
            "winjupos_twin": {
                "extent_lon_deg": 12.0,
                "limb_sky_spread_arcsec": 0.5,
                "definition_lon_spread_deg": 1.0,
            },
        }
        b = build_winjupos_plus_block(pkg)
        self.assertTrue(b["ok"])
        self.assertEqual(b["publish_definition"], "GS-MAP")
        self.assertIsNotNone(b["lat_planetographic_deg"])
        self.assertLess(b["lat_planetographic_deg"], -22.0)
        self.assertGreaterEqual(b["desk_score_0_100"], 70)
        self.assertIn("citation_line", b)

    def test_analytical_cm_lowers_score(self):
        from winjupos_plus import build_winjupos_plus_block

        pkg = {
            "headline": {"lon_iii_deg": 100.0, "lat_deg": -22.0},
            "publish": {
                "publish_definition": "GS-MAP",
                "publish_lon_iii_deg": 100.0,
                "publish_lat_deg": -22.0,
                "cm_source": "analytical",
                "cm_iii_deg": 0.0,
                "distance_au": 5.2,
            },
            "publish_quality": {"cm_trusted": False},
            "winjupos_twin": {},
        }
        b = build_winjupos_plus_block(pkg)
        self.assertIn("CM_WEAK", b["desk_flags"])
        self.assertLess(b["desk_score_0_100"], 70)

"""SUPERDUPER best-answer card tests."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


class TestSuperduper(unittest.TestCase):
    def test_card_builds(self):
        from superduper import build_superduper_card, format_superduper_txt

        pkg = {
            "headline": {
                "lon_iii_deg": 100.0,
                "lat_deg": -22.0,
                "cm_source": "spice_auto",
                "champion_grade": "CHAMPION",
            },
            "publish": {
                "publish_definition": "GS-MAP",
                "publish_lon_iii_deg": 100.5,
                "publish_lat_deg": -22.1,
                "publish_lat_planetographic_deg": -24.9,
                "cm_source": "spice_auto",
                "cm_iii_deg": 90.0,
                "absolute_ok": True,
                "winjupos_equality": {"agreement": "NO_MANUAL_PICK"},
            },
            "champion": {
                "grade": "UNBEATABLE_AUTO",
                "unbeatable_auto": True,
                "absolute_publish_ok": True,
                "lon_iii_deg": 100.5,
                "lat_planetocentric_deg": -22.1,
                "lat_planetographic_deg": -24.9,
                "sigma_total_sky_arcsec": 0.9,
                "extent_ew_deg": 12.0,
                "ultimate_lock": {"n_pass": 11, "n_total": 11, "failed_checks": []},
            },
        }
        card = build_superduper_card(pkg)
        r = card["report_this"]
        self.assertEqual(r["grade"], "UNBEATABLE_AUTO")
        self.assertTrue(r["unbeatable_auto"])
        self.assertAlmostEqual(r["lon_iii_deg"], 100.5)
        txt = format_superduper_txt(card)
        self.assertIn("REPORT THIS", txt)
        self.assertIn("100.5", txt)

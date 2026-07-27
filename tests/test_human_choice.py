"""Tests for WinJUPOS-style dual human_choice helpers (no GUI)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


class TestHumanChoice(unittest.TestCase):
    def test_from_dict_and_definitions(self):
        from human_choice import HumanChoice, DEFINITIONS

        h = HumanChoice.from_dict({
            "enabled": True,
            "definition": "GS-BARY",
            "limb_scale": 1.05,
            "manual_lon": 200.5,
        })
        self.assertTrue(h.enabled)
        self.assertEqual(h.definition, "GS-BARY")
        self.assertAlmostEqual(h.limb_scale, 1.05)
        self.assertIn("GS-MAP", DEFINITIONS)

    def test_compare_snapshots(self):
        from human_choice import compare_measure_snapshots

        auto = {"lon_iii_deg": 100.0, "lat_deg": -22.0, "publish_definition": "PIPELINE"}
        human = {"lon_iii_deg": 100.5, "lat_deg": -22.1, "publish_definition": "GS-MAP"}
        c = compare_measure_snapshots(auto, human, distance_au=5.0)
        self.assertTrue(c["compared"])
        self.assertIn(c["agreement"], ("MATCH", "NEAR", "FAIR", "DIFFERENT"))
        self.assertTrue(abs(c["dlon_human_minus_auto_deg"]) < 1.0)

    def test_force_publish_manual(self):
        from human_choice import HumanChoice, force_publish_definition, snapshot_publish_block

        pkg = {
            "headline": {"lon_iii_deg": 10.0, "lat_deg": -22.0, "distance_au": 5.0},
            "publish": {
                "publish_lon_iii_deg": 10.0,
                "publish_lat_deg": -22.0,
                "pipeline_lon_iii_deg": 10.0,
                "cm_source": "spice_auto",
                "distance_au": 5.0,
            },
            "winjupos_twin": {"gs_map_lon": 11.0, "gs_map_lat": -22.0},
        }
        force_publish_definition(
            pkg,
            HumanChoice(definition="MANUAL", manual_lon=55.5, manual_lat=-21.0),
        )
        self.assertEqual(pkg["publish"]["publish_lon_iii_deg"], 55.5)
        self.assertEqual(pkg["publish"]["publish_definition"], "MANUAL")
        snap = snapshot_publish_block(pkg, label="human")
        self.assertEqual(snap["lon_iii_deg"], 55.5)

    def test_force_gs_map(self):
        from human_choice import HumanChoice, force_publish_definition

        pkg = {
            "headline": {"lon_iii_deg": 10.0, "lat_deg": -22.0},
            "publish": {"pipeline_lon_iii_deg": 10.0, "distance_au": 5.2, "cm_source": "spice_auto"},
            "winjupos_twin": {"gs_map_lon": 12.3, "gs_map_lat": -21.5},
        }
        force_publish_definition(pkg, HumanChoice(definition="GS-MAP"))
        self.assertAlmostEqual(float(pkg["publish"]["publish_lon_iii_deg"]), 12.3)

    def test_gs_map_plus_rim(self):
        from human_choice import HumanChoice, force_publish_definition, extract_outer_rim

        pkg = {
            "headline": {"lon_iii_deg": 10.0, "lat_deg": -22.0, "width_deg": 8.5},
            "publish": {"pipeline_lon_iii_deg": 10.0, "distance_au": 5.2, "cm_source": "spice_auto"},
            "winjupos_twin": {
                "gs_map_lon": 100.0,
                "gs_map_lat": -22.0,
                "west_edge_lon": 94.0,
                "east_edge_lon": 108.0,
                "extent_lon_deg": 14.0,
            },
        }
        force_publish_definition(pkg, HumanChoice(definition="GS-MAP+RIM"))
        self.assertEqual(pkg["publish"]["publish_definition"], "GS-MAP+RIM")
        self.assertAlmostEqual(float(pkg["publish"]["publish_lon_iii_deg"]), 100.0)
        self.assertAlmostEqual(float(pkg["publish"]["length_deg"]), 14.0)
        rim = pkg.get("outer_rim") or {}
        self.assertEqual(rim.get("role"), "outer_rim_size_only")
        self.assertAlmostEqual(float(rim["west_edge_lon_iii_deg"]), 94.0)
        self.assertAlmostEqual(float(rim["east_edge_lon_iii_deg"]), 108.0)
        r2 = extract_outer_rim(pkg)
        self.assertAlmostEqual(float(r2["extent_lon_deg"]), 14.0)

    def test_adjust_nav(self):
        from human_choice import HumanChoice, adjust_nav_like_outline
        from precision_engine import NavState

        nav = NavState(xc=100.0, yc=100.0, a_eq_px=50.0, cm_iii_deg=0.0, distance_au=5.0)
        n2 = adjust_nav_like_outline(nav, HumanChoice(limb_scale=1.1, limb_dx_frac=0.02))
        self.assertAlmostEqual(n2.a_eq_px, 55.0)
        self.assertGreater(n2.xc, nav.xc)


if __name__ == "__main__":
    unittest.main()

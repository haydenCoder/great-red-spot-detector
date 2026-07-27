"""Champion measure + publish priority tests."""
from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

import numpy as np

APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


def _fake_disk(n: int = 256, xc: float = 128, yc: float = 128, a: float = 90.0) -> np.ndarray:
    """Simple disk with dark GRS-like oval in SEB."""
    yy, xx = np.mgrid[0:n, 0:n]
    X = (xx - xc) / a
    Y = (yc - yy) / (a * 0.935)
    rr = X * X + Y * Y
    disk = np.clip(1.0 - 0.15 * rr, 0, 1) * (rr <= 1.0)
    # dark oval near SEB: lat ~ -22 → Y ~ sin(-22°) ≈ -0.37
    ox, oy = xc + 0.15 * a, yc + 0.37 * a
    ell = ((xx - ox) / (0.12 * a)) ** 2 + ((yy - oy) / (0.08 * a)) ** 2
    disk = disk * (1.0 - 0.55 * np.exp(-0.5 * ell))
    return disk.astype(np.float64)


class TestChampion(unittest.TestCase):
    def test_champion_runs_on_synth_disk(self):
        from champion_measure import run_champion_measure

        img = _fake_disk()
        ch = run_champion_measure(
            img,
            cm_iii_deg=100.0,
            distance_au=5.2,
            cm_source="spice_auto",
            sigma_cm_deg=0.05,
        )
        self.assertTrue(ch.ok)
        self.assertTrue(math.isfinite(ch.lon_iii_deg))
        self.assertTrue(-36 <= ch.lat_planetocentric_deg <= -10)
        self.assertTrue(math.isfinite(ch.lat_planetographic_deg))
        self.assertGreater(ch.sigma_total_sky_arcsec, 0.0)
        self.assertIn(ch.grade, ("WORLD_CLASS", "CHAMPION", "STRONG", "USABLE", "HOLD"))

    def test_analytical_cm_not_absolute(self):
        from champion_measure import run_champion_measure

        img = _fake_disk()
        ch = run_champion_measure(
            img,
            cm_iii_deg=100.0,
            cm_source="analytical",
            sigma_cm_deg=0.5,
        )
        self.assertIn("CM_UNTRUSTED", ch.flags)
        self.assertFalse(ch.absolute_publish_ok)

    def test_publish_prefers_champion_when_absolute(self):
        from publish_primary import apply_publish_policy

        pkg = {
            "headline": {
                "lon_iii_deg": 100.0,
                "lat_deg": -22.0,
                "pipeline_lon_iii_deg": 100.0,
                "pipeline_lat_deg": -22.0,
                "cm_source": "spice_auto",
                "cm_iii_deg": 90.0,
                "distance_au": 5.2,
            },
            "champion": {
                "ok": True,
                "absolute_publish_ok": True,
                "lon_iii_deg": 101.5,
                "lat_planetocentric_deg": -22.1,
                "definition": "CHAMPION-ENGINE",
            },
            "winjupos_twin": {
                "gs_map_lon": 100.2,
                "gs_map_lat": -22.0,
            },
            "gold_standard": {"ok": True},
            "sota": {},
        }
        pub = apply_publish_policy(pkg)
        self.assertTrue(str(pub["publish_definition"]).startswith("CHAMPION") or
                        pub["publish_definition"] == "UNBEATABLE_AUTO")
        self.assertAlmostEqual(float(pub["publish_lon_iii_deg"]), 101.5, places=2)

    def test_publish_unbeatable_label(self):
        from publish_primary import apply_publish_policy

        pkg = {
            "headline": {
                "pipeline_lon_iii_deg": 100.0,
                "pipeline_lat_deg": -22.0,
                "cm_source": "spice_auto",
                "cm_iii_deg": 90.0,
                "distance_au": 5.2,
            },
            "champion": {
                "ok": True,
                "absolute_publish_ok": True,
                "unbeatable_auto": True,
                "lon_iii_deg": 100.5,
                "lat_planetocentric_deg": -22.0,
                "definition": "GS-MAP",
            },
            "winjupos_twin": {},
            "gold_standard": {"ok": True},
            "sota": {},
        }
        pub = apply_publish_policy(pkg)
        self.assertEqual(pub["publish_definition"], "UNBEATABLE_AUTO")


if __name__ == "__main__":
    unittest.main()

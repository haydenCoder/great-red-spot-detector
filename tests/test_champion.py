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

    def test_near_limb_flag_at_45_degrees(self):
        """JUPOS practice: features beyond ±45° from the CM get flagged."""
        from champion_measure import run_champion_measure

        def planted(lon_rel: float):
            yy, xx = np.mgrid[0:256, 0:256]
            X = (xx - 128.0) / 90.0
            Y = (128.0 - yy) / (90.0 * 0.935)
            rr = X * X + Y * Y
            disk = np.clip(1.0 - 0.15 * rr, 0, 1) * (rr <= 1.0)
            ox = 128.0 + math.sin(math.radians(lon_rel)) * 90.0
            oy = 128.0 + 0.37 * 90.0
            ell = ((xx - ox) / (0.12 * 90.0)) ** 2 + ((yy - oy) / (0.08 * 90.0)) ** 2
            return (disk * (1.0 - 0.55 * np.exp(-0.5 * ell))).astype(np.float64)

        near = run_champion_measure(planted(50.0), cm_iii_deg=100.0,
                                    cm_source="spice_auto", sigma_cm_deg=0.05)
        self.assertTrue(near.ok)
        self.assertIn("GRS_NEAR_LIMB", near.flags)
        self.assertNotIn("FINAL_MAP_EDGE", near.flags)

        central = run_champion_measure(planted(20.0), cm_iii_deg=100.0,
                                       cm_source="spice_auto", sigma_cm_deg=0.05)
        self.assertTrue(central.ok)
        self.assertNotIn("GRS_NEAR_LIMB", central.flags)
        self.assertNotIn("FINAL_MAP_EDGE", central.flags)

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


class TestSystemIIWiring(unittest.TestCase):
    """System II mapping (feature 1) must populate champion + publish results."""

    def test_champion_computes_system_ii(self):
        from champion_measure import run_champion_measure

        img = _fake_disk()
        ch = run_champion_measure(
            img,
            cm_iii_deg=100.0,
            cm_source="spice_auto",
            sigma_cm_deg=0.05,
            user_time_iso="2026-07-14 12:00:00",
        )
        self.assertTrue(math.isfinite(ch.cm_ii_deg))
        self.assertTrue(math.isfinite(ch.lon_ii_deg))
        # offset is shared by CM and feature: their separation is preserved
        sep_iii = (ch.lon_iii_deg - ch.cm_iii_deg + 180.0) % 360.0 - 180.0
        sep_ii = (ch.lon_ii_deg - ch.cm_ii_deg + 180.0) % 360.0 - 180.0
        self.assertAlmostEqual(sep_ii, sep_iii, places=6)

    def test_champion_without_time_leaves_system_ii_nan(self):
        from champion_measure import run_champion_measure

        ch = run_champion_measure(_fake_disk(), cm_iii_deg=100.0,
                                  cm_source="spice_auto", sigma_cm_deg=0.05)
        self.assertTrue(math.isnan(ch.cm_ii_deg))
        self.assertTrue(math.isnan(ch.lon_ii_deg))

    def test_attach_champion_writes_headline_keys(self):
        from champion_measure import attach_champion_to_package

        pkg = {"headline": {}}
        attach_champion_to_package(
            pkg, _fake_disk(),
            cm_iii_deg=100.0, cm_source="spice_auto", sigma_cm_deg=0.05,
            user_time_iso="2026-07-14 12:00:00",
        )
        h = pkg["headline"]
        self.assertIn("champion_lon_ii_deg", h)
        self.assertIn("champion_cm_ii_deg", h)
        self.assertTrue(math.isfinite(float(h["champion_cm_ii_deg"])))

    def test_publish_exposes_system_ii(self):
        from publish_primary import apply_publish_policy
        import system_ii

        t = "2026-07-14 12:00:00"
        pkg = {
            "headline": {
                "lon_iii_deg": 100.0, "lat_deg": -22.0,
                "pipeline_lon_iii_deg": 100.0, "pipeline_lat_deg": -22.0,
                "cm_source": "spice_auto", "cm_iii_deg": 90.0,
                "distance_au": 5.2, "user_time": t,
            },
            "champion": {
                "ok": True, "absolute_publish_ok": True,
                "lon_iii_deg": 101.5, "lat_planetocentric_deg": -22.1,
                "definition": "CHAMPION-ENGINE",
                # deliberately inconsistent cm_ii/lon_ii: the publish card must
                # re-derive L_II from the published L_III, not trust these
                "cm_ii_deg": 150.5, "lon_ii_deg": 162.0,
            },
            "winjupos_twin": {}, "gold_standard": {"ok": True}, "sota": {},
        }
        pub = apply_publish_policy(pkg)
        pub_lon_iii = float(pub["publish_lon_iii_deg"])
        # L_II is derived from the *published* L_III, self-consistently
        self.assertAlmostEqual(
            float(pub["publish_lon_ii_deg"]),
            system_ii.system_iii_to_system_ii(pub_lon_iii, t), places=4)
        self.assertAlmostEqual(
            float(pub["cm_ii_deg"]),
            system_ii.system_iii_to_system_ii(float(pub["cm_iii_deg"]), t), places=4)
        # report text carries the System II line
        from publish_primary import format_publish_section
        self.assertIn("lon_II", format_publish_section(pkg))


if __name__ == "__main__":
    unittest.main()

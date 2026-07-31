"""Unit tests for planet_models (the planet-generalised stacker foundation)."""
from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


class TestPlanetProfiles(unittest.TestCase):
    def test_known_planets_present(self):
        from planet_models import known_planets, get_planet
        names = known_planets()
        for n in ("jupiter", "saturn", "neptune", "uranus", "mars"):
            self.assertIn(n, names)
            self.assertEqual(get_planet(n).name.lower(), n)

    def test_jupiter_system_iii_period(self):
        # System III = 9h55m29.711s — a precise, checkable IAU value.
        from planet_models import JUPITER
        self.assertAlmostEqual(
            JUPITER.rotation_period_s,
            9 * 3600 + 55 * 60 + 29.711,
            places=3,
        )

    def test_rotation_rates_are_positive_and_ordered(self):
        # Bulk rotation rates: Jupiter fastest of the gas giants, Mars slowest.
        from planet_models import JUPITER, SATURN, NEPTUNE, URANUS, MARS
        self.assertGreater(JUPITER.rotation_rate_deg_per_s, SATURN.rotation_rate_deg_per_s)
        self.assertGreater(SATURN.rotation_rate_deg_per_s, NEPTUNE.rotation_rate_deg_per_s)
        self.assertGreater(NEPTUNE.rotation_rate_deg_per_s, MARS.rotation_rate_deg_per_s)
        for p in (JUPITER, SATURN, NEPTUNE, URANUS, MARS):
            self.assertGreater(p.rotation_rate_deg_per_s, 0.0)

    def test_flattening_matches_fact_sheets(self):
        # Saturn is the most oblate planet (~0.098), Jupiter next (~0.065),
        # Mars nearly spherical. Real fact-sheet ordering.
        from planet_models import JUPITER, SATURN, MARS
        self.assertAlmostEqual(JUPITER.flattening, 0.06487, places=3)
        self.assertGreater(SATURN.flattening, JUPITER.flattening)
        self.assertLess(MARS.flattening, 0.007)

    def test_wind_residual_symmetric_and_continuous(self):
        from planet_models import JUPITER, SATURN, NEPTUNE
        for p in (JUPITER, SATURN, NEPTUNE):
            for lat in (3, 12, 25, 40, 60):
                self.assertAlmostEqual(
                    p.zonal_wind_residual_mps(lat),
                    p.zonal_wind_residual_mps(-lat),
                    places=9,
                )
            # monotonically queryable across the pole
            self.assertTrue(
                math.isfinite(p.cloud_tracking_rate_deg_per_s(89.0))
            )

    def test_saturn_equatorial_jet_faster_than_jupiter(self):
        # Saturn's equatorial super-rotation is famously much stronger.
        from planet_models import JUPITER, SATURN
        self.assertGreater(
            SATURN.zonal_wind_residual_mps(5.0),
            JUPITER.zonal_wind_residual_mps(5.0),
        )

    def test_cloud_rate_exceeds_bulk_at_prograde_equator(self):
        # At a latitude with a positive (prograde) wind residual, the cloud
        # rate must exceed the bulk radio rate.
        from planet_models import JUPITER
        self.assertGreater(
            JUPITER.cloud_tracking_rate_deg_per_s(10.0),
            JUPITER.rotation_rate_deg_per_s,
        )

    def test_expected_drift_sign_and_scale(self):
        from planet_models import JUPITER
        d = JUPITER.expected_drift_dx_px(10.0, dt_s=100.0, deg_to_px=4.0)
        # positive rate * positive dt * positive scale → positive dx
        self.assertGreater(d, 0.0)
        # zero time → zero drift
        self.assertEqual(JUPITER.expected_drift_dx_px(10.0, 0.0, 4.0), 0.0)

    def test_unknown_planet_raises(self):
        from planet_models import get_planet
        with self.assertRaises(ValueError):
            get_planet("pluto")


if __name__ == "__main__":
    unittest.main()

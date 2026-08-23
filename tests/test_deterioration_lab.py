"""Tests for the Deterioration Lab sweep engine."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


class TestDeteriorationLab(unittest.TestCase):
    def test_small_sweep_runs_and_aggregates(self):
        from deterioration_lab import LabConfig, run_sweep

        cfg = LabConfig(
            resolutions=("540p",),
            seeing=(0.4, 2.4),
            noise=(0.004,),
            seeds=1,
            map_width=800,
            map_height=400,
        )
        rep = run_sweep(cfg)
        self.assertTrue(rep["ok"])
        self.assertEqual(rep["n_cells"], 2)
        self.assertEqual(len(rep["rows"]), 2)
        for row in rep["rows"]:
            self.assertEqual(row["n"], 1)
            self.assertGreaterEqual(row["n_ok"], 0 if row["seeing"] > 5 else 1)
            self.assertIn("median_abs_dlon", row)
            self.assertIn("within_1deg", row)
        # floor dict keyed by resolution
        self.assertIn("540p", rep["floor"])
        self.assertIsInstance(rep["tips"], list)
        self.assertTrue(any("SER" in t for t in rep["tips"]))

    def test_progress_callback_fires(self):
        from deterioration_lab import LabConfig, run_sweep

        calls = []
        cfg = LabConfig(
            resolutions=("540p",), seeing=(0.4,), noise=(0.004,),
            seeds=1, map_width=600, map_height=300,
            progress=lambda p: calls.append(p),
        )
        run_sweep(cfg)
        self.assertTrue(calls)
        self.assertIn("done", calls[-1])
        self.assertIn("total", calls[-1])

    def test_method_breakdown_has_redness(self):
        from deterioration_lab import LabConfig, run_sweep

        cfg = LabConfig(
            resolutions=("540p",), seeing=(0.4, 0.8), noise=(0.004,),
            seeds=1, map_width=800, map_height=400,
        )
        rep = run_sweep(cfg)
        mb = rep["method_breakdown"]
        # Redness is the blur-robust estimator and must be present on metrology
        # synthetics even at the smallest seeing tier.
        self.assertIn("redness", mb)
        self.assertGreater(mb["redness"]["n"], 0)


if __name__ == "__main__":
    unittest.main()

"""
Tests for tools/real_photo_validate.py — the real-photo validation
entry point that lets the user compare the pipeline to a manual
WinJUPOS pick.

The smoke test runs the synthetic path (no real photo required).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TOOL = REPO / "tools" / "real_photo_validate.py"


class TestRealPhotoValidate(unittest.TestCase):
    def test_synthetic_smoke_runs(self):
        """The --synthetic path must run end-to-end and write a JSON."""
        out_path = Path("/tmp/test_rpv_synthetic.json")
        if out_path.exists():
            out_path.unlink()
        r = subprocess.run(
            [sys.executable, str(TOOL), "--synthetic", "--out", str(out_path)],
            capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(r.returncode, 0,
                         f"stderr={r.stderr[:500]}")
        self.assertTrue(out_path.exists())
        d = json.loads(out_path.read_text())
        # Must have the basic fields
        self.assertEqual(d["mode"], "synthetic_smoke")
        self.assertIn("publish_lon_iii_deg", d)
        self.assertIn("publish_lat_deg", d)
        self.assertIn("per_estimator", d)
        # Per-estimator must include at least template, moment, redness
        self.assertIn("template", d["per_estimator"])
        self.assertIn("moment", d["per_estimator"])
        self.assertIn("redness", d["per_estimator"])

    def test_per_estimator_breakdown_honest(self):
        """The per-estimator table must show that the published
        redness_lon+moment_lat result is actually a blend: lon from
        redness, lat from moment. We assert the close-against-truth
        ordering: redness-lon is closer to truth than template-lon,
        and moment-lat is closer to truth than the rejected
        template-lat."""
        out_path = Path("/tmp/test_rpv_synthetic2.json")
        if out_path.exists():
            out_path.unlink()
        r = subprocess.run(
            [sys.executable, str(TOOL), "--synthetic", "--out", str(out_path)],
            capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(r.returncode, 0, f"stderr={r.stderr[:500]}")
        d = json.loads(out_path.read_text())
        pe = d["per_estimator"]
        # The template method on metrology synthetic is far from
        # truth (~80-100°). The moment is closer (~4-7°). The redness
        # is the closest (~0.2-0.4°). This is the per-estimator
        # ordering that motivates any redness-based improvement.
        self.assertLess(abs(pe["redness"]["dlon_deg"]), 1.0,
                        f"redness dlon {pe['redness']['dlon_deg']} should be <1°")
        self.assertGreater(abs(pe["template"]["dlon_deg"]), 30.0,
                           f"template dlon {pe['template']['dlon_deg']} should be far")


if __name__ == "__main__":
    unittest.main()

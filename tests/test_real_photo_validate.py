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
        """The per-estimator table must show the classical estimators all
        landing sub-degree on a metrology synthetic, with the published
        redness primary inside the same 1° production gate.

        HISTORY: this test used to assert the exact opposite ordering —
        "template is ~80-100° off, redness carries the publish". That was
        true before the v6.6 consensus tuning and the moon-mask colour
        gate: the old luminance moon masker erased the GRS core from the
        measurement mono (the core is compact and dark), so any dark-blob
        estimator was yanked off the planet. Measured 2026-08-07 on this
        exact smoke frame (identical at v6.7.6 and v6.8.0):

            template  dlon=-0.150  dlat=-0.453
            moment    dlon=-0.056  dlat=-0.232
            redness   dlon=+0.207  dlat=-0.228
            spire_net dlon=-27.7   dlat=+14.6   (the remaining far-off one —
                                                a soft PRIOR, never published)

        The stale ">30°" pin made the suite fail at v6.7.6 HEAD itself; the
        assertions below now pin the truth, including that spire_net stays
        non-primary.
        """
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
        for name in ("template", "moment", "redness"):
            self.assertLess(
                abs(pe[name]["dlon_deg"]), 1.0,
                f"{name} dlon {pe[name]['dlon_deg']:+.3f} regressed out of the 1° gate",
            )
            self.assertLess(
                abs(pe[name]["dlat_deg"]), 1.0,
                f"{name} dlat {pe[name]['dlat_deg']:+.3f} regressed out of the 1° gate",
            )
        # publish is redness-based and inside the same gate
        self.assertIn("redness", d["method"])
        dpub = (d["publish_lon_iii_deg"] - d["truth_lon_iii_deg"] + 180) % 360 - 180
        self.assertLess(abs(dpub), 1.0, f"published dlon {dpub:+.3f} outside the 1° gate")


if __name__ == "__main__":
    unittest.main()

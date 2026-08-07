"""
Tests for the per-method audit harness (tools/per_method_audit.py).

The audit is the foundation work for any future improvement to the
published path: without per-method lat/lon, you cannot tell which
estimator is wrong on which frame, so you cannot ship a regression-free
change.

These tests are fast and self-contained: they run the audit on a tiny
seed set (4 cases) and assert the per-method summary structure and
signs. The full 100-case audit is run by tools/per_method_audit.py
directly (see docs/IMPROVEMENT_DIAGNOSIS.md).
"""
from __future__ import annotations

import json
import math
import os
import sys
import tempfile
import unittest
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
TOOLS = ROOT / "tools"
for p in (str(APP), str(TOOLS)):
    if p not in sys.path:
        sys.path.insert(0, p)


def _run_audit_subset(n: int = 4, out_path: Path | None = None) -> dict:
    """Run the per-method audit on the first N cases of the 100-case matrix."""
    from per_method_audit import run_per_method, summarise

    # Import the MATRIX constant from the test module
    import importlib.util
    spec = importlib.util.spec_from_file_location("rs100", ROOT / "tests" / "test_resolution_seeing_100.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    matrix = mod.MATRIX[:n]
    rows = [run_per_method(c) for c in matrix]
    return summarise(rows)


class TestPerMethodAudit(unittest.TestCase):
    def test_summary_has_all_methods(self):
        s = _run_audit_subset(n=2)
        for m in ("template", "map_dark", "moment", "redness", "ellipse",
                  "published_hybrid", "v662"):
            self.assertIn(m, s, f"summary missing method {m!r}")

    def test_redness_is_accurate(self):
        """The redness estimator must produce |dlon| and |dlat} both < 0.5° on
        the metrology-mode synthetic. (This is the foundational measurement
        the v6.6.2 published path uses.)"""
        s = _run_audit_subset(n=4)
        r = s["redness"]
        self.assertEqual(r["n"], 4)
        self.assertLessEqual(r["abs_dlon_median"], 0.5)
        self.assertLessEqual(r["abs_dlat_median"], 0.5)

    def test_v662_matches_redness(self):
        """In v6.6.2 the published path is redness_lon+redness_lat. The
        per-method audit's `v662` field mirrors that, so it must be at
        parity with the redness estimator."""
        s = _run_audit_subset(n=4)
        r = s["redness"]
        p = s["v662"]
        # dlon matches by construction
        self.assertAlmostEqual(r["dlon_median"], p["dlon_median"], places=6)
        # dlat must match in v6.6.2 (the hybrid uses redness_lat, not moment_lat)
        self.assertAlmostEqual(r["dlat_median"], p["dlat_median"], places=6)
        # And v662 is a strict improvement on the v6.6.1 published hybrid:
        v661 = s["published_hybrid"]
        self.assertLess(p["dlat_pstdev"], v661["dlat_pstdev"] * 0.5,
                        "v6.6.2 dlat scatter is not better than v6.6.1")

    def test_moment_dlat_bias_stays_fixed(self):
        """v6.6.1 had a +1.5° dlat bias in the moment estimator (the
        redness_lon + moment_lat hybrid bug). The v6.7.x estimator uses the
        full planetocentric latitude tilt + intensity-inverted weights, and
        the bias is gone. Verified 2026-08-06 across 12 matrix cases
        (small_clear/small_blurry/large_mild): dlat mean +0.013, median
        +0.015, max |.| 0.25 — so this pin asserts the bias *stays* fixed and
        the v6.6.1 regression never sneaks back in.
        """
        s = _run_audit_subset(n=4)
        m = s["moment"]
        # bias is the signed mean of dlat; the fixed estimator is ~0
        self.assertLessEqual(
            abs(m["dlat_mean"]), 0.30,
            f"moment dlat bias regressed (v6.6.1 bug back?): "
            f"dlat_mean={m['dlat_mean']:+.3f}",
        )
        # and scatter must be sub-degree on the clear first four cases
        self.assertLessEqual(
            m["abs_dlat_max"], 1.0,
            f"moment dlat scatter regressed: abs_dlat_max={m['abs_dlat_max']:.3f}",
        )


if __name__ == "__main__":
    unittest.main()

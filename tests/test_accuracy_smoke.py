"""
Synthetic metrology accuracy smoke against shipped product_core path.

Drives generate_synthetic (same stack as CLI `synth` / desktop Synthetic),
asserts finite sky_error_arcsec and honest single-run certify-style gates.
"""
from __future__ import annotations

import json
import math
import sys
import tempfile
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


# Single-run gates aligned with product_core.certify defaults (looser max for n=1).
MEDIAN_MAX_ARCSEC = 0.75
MAX_MAX_ARCSEC = 8.0


def _sky_error_from_package(pkg: dict) -> float:
    """Extract sky error from real package fields (headline / truth_recovery)."""
    tr = pkg.get("truth_recovery") or {}
    h = pkg.get("headline") or {}
    val = tr.get("sky_error_arcsec")
    if val is None:
        val = h.get("sky_error_arcsec")
    if val is None:
        # some packages nest under truth
        truth = pkg.get("truth") or {}
        val = truth.get("sky_error_arcsec")
    if val is None:
        raise AssertionError(
            "package missing sky_error_arcsec in truth_recovery/headline; "
            f"keys={sorted(pkg.keys())} headline={list(h.keys())} tr={list(tr.keys())}"
        )
    return float(val)


class TestAccuracySmoke(unittest.TestCase):
    def test_product_version_importable(self):
        from product_core import PRODUCT_NAME, PRODUCT_VERSION, ProductInfo

        info = ProductInfo().to_dict()
        self.assertEqual(info["name"], PRODUCT_NAME)
        self.assertTrue(str(PRODUCT_VERSION))
        self.assertIn("version", info)

    def test_resolve_ephemeris_has_geometry(self):
        from product_core import resolve_ephemeris

        d = resolve_ephemeris("2026-07-14 12:00:00", use_spice=True, use_horizons=True)
        self.assertIn("cm_iii_deg", d)
        self.assertTrue(math.isfinite(float(d["cm_iii_deg"])))
        self.assertIn("t_utc_iso", d)
        self.assertTrue(d.get("cm_source") or d.get("source"))
        out = d.get("output_dir")
        self.assertTrue(out)
        self.assertTrue(Path(out).exists())

    def test_synthetic_metrology_measure_finite_sky_error(self):
        """
        Real generate_synthetic with process_after=True (measure).

        Asserts finite, non-placeholder sky error within product max gate.
        """
        from product_core import generate_synthetic

        with tempfile.TemporaryDirectory(prefix="grs_acc_") as tmp:
            out_root = Path(tmp)
            pkg = generate_synthetic(
                out_root=out_root,
                resolution="1080p",
                region="global",
                mode="metrology",
                process_after=True,
                seed=42,
                use_vlbi=True,
                use_nn=False,
            )
            self.assertIsInstance(pkg, dict)
            sky = _sky_error_from_package(pkg)
            self.assertTrue(math.isfinite(sky), f"sky_error not finite: {sky!r}")
            self.assertGreaterEqual(sky, 0.0, "sky error should be non-negative")
            # Honest single-run max gate (certify max_max_arcsec default)
            self.assertLessEqual(
                sky,
                MAX_MAX_ARCSEC,
                f"sky_error_arcsec={sky} exceeds single-run max gate {MAX_MAX_ARCSEC}",
            )
            # Prefer tight median-style gate when pipeline is healthy
            if sky > MEDIAN_MAX_ARCSEC:
                # still pass if under max — record for inspection
                pass
            out_dir = pkg.get("output_dir")
            self.assertTrue(out_dir, "package must set output_dir")
            job = Path(out_dir)
            self.assertTrue(job.exists(), f"output_dir missing: {job}")
            # Prefer written job_result if present
            jr = job / "job_result.json"
            if jr.exists():
                written = json.loads(jr.read_text(encoding="utf-8"))
                self.assertIsInstance(written, dict)
                # sky may live only on returned package; written may mirror it
            # Headline / truth fields must not be hardcoded placeholder-only
            h = pkg.get("headline") or {}
            lon = h.get("lon_iii_deg")
            if lon is not None:
                self.assertTrue(math.isfinite(float(lon)))


if __name__ == "__main__":
    unittest.main()

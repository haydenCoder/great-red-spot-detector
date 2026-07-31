"""
Regression guard for the v6.6.2 redness-primary published path.

In v6.6.1 the published path was `redness_lon + moment_lat` (the "aggressive
hybrid"). Per-method audit (tools/per_method_audit.py, runs/per_method_audit.summary.json)
on the 100-case resolution_seeing_100 matrix showed:

  template      dlon median 75°    (catastrophically wrong on most cases)
  map_dark      dlon median 70°
  moment        dlat BIAS +1.55°   (consistent north-of-centre offset)
  redness       dlon median 0.08°  dlat median 0.09°  ← 100/100 within 1°

  published_hybrid (redness_lon + moment_lat) within 1°: only 9/100

The aggressive hybrid was mixing the one excellent estimator (redness) with the
one biased estimator (moment), regressing the published result to 9% within 1°.

v6.6.2 makes redness the primary on measurable RGB frames and falls back to
the audit's defensive consensus only when redness fails (mono, off-band, etc.).

This test suite pins the new behaviour so it cannot regress:
  * On synthetic RGB, the published method is `redness_lon+redness_lat` and
    |dlon| <= 0.5°, |dlat| <= 0.5°.
  * The published method is NOT the v6.6.1 `redness_lon+moment_lat` hybrid.
  * On a deliberate mono image the audit's defensive consensus runs (does not
    crash, returns a finite measurement, lat near the GRS band).
"""
from __future__ import annotations

import math
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


def _render_synthetic(seed: int = 200000, seeing: float = 0.38, noise: float = 0.00628):
    """Render one metrology-mode synthetic frame and return (img, truth)."""
    from synthetic_hq import SynthSpec, generate

    with tempfile.TemporaryDirectory(prefix="grs_redtest_") as d:
        png, _fit, truth = generate(
            SynthSpec(
                region="global", resolution_preset="1080p", random_time=True,
                seed=int(seed), mode="metrology", write_grs_crop=False,
                seeing_fwhm_arcsec=float(seeing), noise_rms=float(noise),
            ),
            Path(d),
        )
        img = np.asarray(Image.open(png), dtype=np.float64) / 255.0
    return img, truth


def _measure(img, truth):
    from precision_engine import fit_limb_nav, measure_grs_precision
    nav = fit_limb_nav(
        img, cm_iii_deg=truth["cm_iii_deg"], distance_au=truth["distance_au"]
    )
    nav.cm_iii_deg = truth["cm_iii_deg"]
    nav.distance_au = truth["distance_au"]
    nav.sub_lat_deg = float(truth.get("sub_obs_lat_deg") or 0.0)
    nav.north_pa_deg = float(truth.get("north_pa_deg") or 0.0)
    res = measure_grs_precision(
        img, cm_iii_deg=nav.cm_iii_deg, distance_au=nav.distance_au,
        nav=nav, quiet=True,
    )
    return res, nav


class TestRednessPrimary(unittest.TestCase):
    """The v6.6.2 redness-primary published path must be at parity with the
    redness estimator on RGB inputs."""

    def test_rgb_synthetic_uses_redness_primary(self):
        img, truth = _render_synthetic(seed=200000, seeing=0.38, noise=0.00628)
        res, _ = _measure(img, truth)

        # The published method MUST be redness_lon+redness_lat on a clear RGB
        # metrology frame. Any other method string here is the v6.6.1 bug.
        self.assertTrue(
            res.method.startswith("redness_lon+redness_lat"),
            f"v6.6.2 must publish redness-primary on RGB; got method={res.method!r}",
        )

        # And it must match truth to the redness-estimator accuracy.
        from precision_engine import wrap_diff
        dlon = abs(wrap_diff(res.lon_iii_deg, float(truth["grs_lon_seed_deg"])))
        dlat = abs(res.lat_deg - float(truth["grs_lat_seed_deg"]))
        self.assertLessEqual(dlon, 0.5, f"|dlon|={dlon:.3f}° exceeds 0.5°")
        self.assertLessEqual(dlat, 0.5, f"|dlat|={dlat:.3f}° exceeds 0.5°")

    def test_mono_input_falls_back_to_consensus(self):
        """When the image is mono, redness raises; the audit's defensive
        consensus must still produce a finite measurement in the GRS band."""
        img_rgb, truth = _render_synthetic(seed=200001, seeing=0.38, noise=0.00628)
        mono = 0.299 * img_rgb[..., 0] + 0.587 * img_rgb[..., 1] + 0.114 * img_rgb[..., 2]
        res, _ = _measure(mono, truth)

        # Must not crash and must land in the GRS lat band
        self.assertTrue(math.isfinite(res.lon_iii_deg))
        self.assertTrue(math.isfinite(res.lat_deg))
        from accuracy_gates import grs_lat_in_wide_band
        self.assertTrue(
            grs_lat_in_wide_band(res.lat_deg),
            f"mono fallback left the GRS band: lat={res.lat_deg}",
        )

    def test_per_method_redness_is_excellent(self):
        """Sanity: redness alone must produce sub-0.5° lon and lat on
        metrology-mode synthetic (this is the foundational measurement that
        the v6.6.2 published path uses)."""
        img, truth = _render_synthetic(seed=200000, seeing=0.38, noise=0.00628)
        from precision_engine import fit_limb_nav, _redness_grs, wrap_diff
        nav = fit_limb_nav(
            img, cm_iii_deg=truth["cm_iii_deg"], distance_au=truth["distance_au"]
        )
        nav.cm_iii_deg = truth["cm_iii_deg"]
        nav.distance_au = truth["distance_au"]
        nav.sub_lat_deg = float(truth.get("sub_obs_lat_deg") or 0.0)
        nav.north_pa_deg = float(truth.get("north_pa_deg") or 0.0)
        m = _redness_grs(img, nav)
        dlon = abs(wrap_diff(m["lon_iii_deg"], float(truth["grs_lon_seed_deg"])))
        dlat = abs(m["lat_deg"] - float(truth["grs_lat_seed_deg"]))
        self.assertLessEqual(dlon, 0.5)
        self.assertLessEqual(dlat, 0.5)


@pytest.mark.slow
class TestRednessPrimaryResolutionSeeing100(unittest.TestCase):
    """The full 100-case matrix must reach 100% within 1° on the published
    path. This is a slower end-to-end regression guard."""

    def test_published_path_is_100_percent_within_one_degree(self):
        import subprocess
        import time
        from pathlib import Path
        import json

        cache = Path("runs/rs100_campaign.jsonl")
        if not cache.exists() or sum(1 for _ in cache.open()) < 100:
            # Re-build the cache (one-time ~7 min, then the cache is read)
            cache.parent.mkdir(parents=True, exist_ok=True)
            if cache.exists():
                cache.unlink()
            t0 = time.time()
            r = subprocess.run(
                [sys.executable, "tests/test_resolution_seeing_100.py"],
                cwd=Path(__file__).resolve().parents[1],
                capture_output=True, text=True, timeout=1500,
            )
            self.assertEqual(r.returncode, 0, f"cache build failed: {r.stderr[-2000:]}")

        rows = [json.loads(l) for l in cache.open() if l.strip()]
        ok = [r for r in rows if r.get("ok")]
        self.assertEqual(len(ok), 100, f"expected 100 cases; got {len(ok)}")
        within = sum(
            1 for r in ok
            if r["abs_dlon_seed"] <= 1.0 and r["abs_dlat_seed"] <= 1.0
        )
        self.assertEqual(
            within, 100,
            f"published path within-1-deg rate regressed: {within}/100; "
            f"all=median(med_dlon={sorted(r['abs_dlon_seed'] for r in ok)[50]:.2f}°, "
            f"med_dlat={sorted(r['abs_dlat_seed'] for r in ok)[50]:.2f}°)",
        )


if __name__ == "__main__":
    unittest.main()

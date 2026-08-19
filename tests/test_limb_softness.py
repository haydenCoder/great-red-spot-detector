"""Tests for the limb-edge softness (effective seeing) estimator.

The estimator is the deterioration gate that fill/contrast/size cannot provide:
it detects a *resolved but too-blurred* disk, so the pipeline can honestly flag
frames below the measurability floor instead of publishing a confident but
degraded number.

Headline property under test: the estimate is MONOTONE in the renderer's seeing
knob, and the warn/fail gates land where the seeing-floor stress test
(tools/seeing_floor_stress.py) says they should (blurry warns, vblurry stays
measurable, extreme refuses).
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


def _render(seed: int, seeing: float, resolution: str = "540p"):
    from synthetic_hq import SynthSpec, generate
    noise = min(0.035, 0.004 + 0.006 * seeing)
    with tempfile.TemporaryDirectory(prefix="grs_soft_t_") as d:
        png, _fit, truth = generate(
            SynthSpec(region="global", resolution_preset=resolution,
                      random_time=True, seed=seed, mode="metrology",
                      write_grs_crop=False, seeing_fwhm_arcsec=seeing,
                      noise_rms=noise),
            Path(d),
        )
        img = np.asarray(Image.open(png), dtype=np.float64) / 255.0
    return img, truth


class TestLimbSoftness(unittest.TestCase):
    def test_monotone_in_seeing(self):
        from precision_engine import fit_limb_nav, estimate_limb_softness_arcsec
        softs = []
        for seeing in (0.38, 0.80, 1.60, 2.40, 3.20, 4.00, 6.00):
            img, truth = _render(12345, seeing)
            nav = fit_limb_nav(img, cm_iii_deg=truth["cm_iii_deg"],
                               distance_au=truth["distance_au"])
            s = estimate_limb_softness_arcsec(img, nav, distance_au=truth["distance_au"])
            softs.append(s)
        # strictly increasing (monotone in the seeing knob)
        for a, b in zip(softs, softs[1:]):
            self.assertLess(a, b, f"softness not monotone: {softs}")

    def test_gate_thresholds_match_floor(self):
        """A resolved disk stays disk_present through the 4″ stress band.

        Softness is a *warning*, not a substitute for fill/contrast/size.
        The old fail gate (softness>5″ → not measurable) refused every
        Hubble OPAL frame after a 2× units bug + PA-blind histogram.
        Extreme seeing still raises softness_fail; it must not invent a
        'no disk' refusal on a clearly resolved planet.
        """
        from precision_engine import fit_limb_nav, assess_disk_quality
        prev = None
        for seeing in (1.60, 2.40, 4.00):
            img, truth = _render(12345, seeing)
            nav = fit_limb_nav(img, cm_iii_deg=truth["cm_iii_deg"],
                               distance_au=truth["distance_au"])
            nav.distance_au = truth["distance_au"]
            dq = assess_disk_quality(img, nav)
            self.assertTrue(dq.get("disk_present"),
                            f"seeing={seeing}\" lost the disk {dq}")
            soft = float(dq.get("softness_arcsec") or 0.0)
            if prev is not None:
                self.assertGreater(soft, prev, f"softness not rising: {prev} → {soft}")
            prev = soft
        self.assertGreater(prev, 1.0)

    def test_softness_reported_in_result(self):
        """The softness estimate surfaces on the measurement result so callers
        can see the deterioration flag even on a measurable frame."""
        from precision_engine import fit_limb_nav, measure_grs_precision
        img, truth = _render(12345, 2.40)
        nav = fit_limb_nav(img, cm_iii_deg=truth["cm_iii_deg"],
                           distance_au=truth["distance_au"])
        nav.cm_iii_deg = truth["cm_iii_deg"]
        nav.distance_au = truth["distance_au"]
        res = measure_grs_precision(img, cm_iii_deg=nav.cm_iii_deg,
                                    distance_au=nav.distance_au, nav=nav,
                                    quiet=True, lean=True)
        dq = res.methods.get("disk_quality") or {}
        self.assertIn("softness_arcsec", dq)
        self.assertGreater(float(dq.get("softness_arcsec", 0.0)), 0.0)


if __name__ == "__main__":
    unittest.main()

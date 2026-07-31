"""Tests for frame_quality + the stacker's lucky-imaging quality gate."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

APP = Path(__file__).resolve().parents[1] / "app"
TOOLS = Path(__file__).resolve().parents[1] / "tools"
for p in (str(APP), str(TOOLS)):
    if p not in sys.path:
        sys.path.insert(0, str(p))


def _disk(n=128, xc=64, yc=64, a=50.0):
    yy, xx = np.mgrid[0:n, 0:n]
    rr = ((xx - xc) / a) ** 2 + ((yy - yc) / a) ** 2
    d = np.clip(1.0 - 0.2 * rr, 0, 1) * (rr <= 1.0)
    # add high-frequency texture so sharpness is non-zero
    rng = np.random.default_rng(0)
    d += 0.05 * rng.random(d.shape) * (rr <= 1.0)
    return d


class TestFrameQuality(unittest.TestCase):
    def test_sharp_beats_blurred(self):
        from frame_quality import assess_frames
        from scipy.ndimage import gaussian_filter
        sharp = _disk()
        blurred = gaussian_filter(sharp, 3.0)
        q = assess_frames([sharp, blurred])
        self.assertGreater(q[0].sharpness, q[1].sharpness)
        self.assertAlmostEqual(q[0].relative, 1.0)
        self.assertLess(q[1].relative, 1.0)

    def test_select_best_drops_worst(self):
        from frame_quality import select_best_frames
        from scipy.ndimage import gaussian_filter
        frames = [_disk(), gaussian_filter(_disk(), 4.0), _disk(),
                  gaussian_filter(_disk(), 5.0)]
        kept, dropped, quals = select_best_frames(frames, keep_frac=0.5, min_keep=2)
        # the two blurred frames (1, 3) should be the ones dropped
        self.assertIn(1, dropped)
        self.assertIn(3, dropped)
        self.assertEqual(len(kept), 2)
        self.assertIn(0, kept)

    def test_keep_frac_one_drops_nothing(self):
        from frame_quality import select_best_frames
        frames = [_disk(), _disk(), _disk()]
        kept, dropped, _ = select_best_frames(frames, keep_frac=1.0)
        self.assertEqual(len(dropped), 0)
        self.assertEqual(len(kept), 3)

    def test_lucky_report_shape(self):
        from frame_quality import assess_frames, lucky_report
        q = assess_frames([_disk(), _disk()])
        rep = lucky_report(q)
        self.assertEqual(rep["n"], 2)
        for key in ("sharpness_min", "sharpness_median", "sharpness_max",
                    "dynamic_range"):
            self.assertIn(key, rep)


class TestStackerQualityGate(unittest.TestCase):
    def _ref_and_distorted(self):
        from synthetic_hq import SynthSpec, generate
        from flow_warp import apply_flow_warp
        with tempfile.TemporaryDirectory(prefix="grs_qg_") as d:
            png, _fit, truth = generate(
                SynthSpec(region="global", resolution_preset="720p", random_time=True,
                          seed=2024, mode="metrology", write_grs_crop=False),
                Path(d),
            )
            arr = np.asarray(Image.open(png), dtype=np.float64) / 255.0
        ref = 0.299 * arr[..., 0] + 0.587 * arr[..., 1] + 0.114 * arr[..., 2]
        return ref

    def test_gate_drops_blurred_frames(self):
        """Mix sharp and heavily-blurred frames; quality_gate must drop the
        blurred ones and report them in dropped_frames."""
        from scipy.ndimage import gaussian_filter
        from planetary_stacker import run_planetary_stacker
        ref = self._ref_and_distorted()
        frames = [ref]
        # frames 1,3 sharp-ish (small warp), 2,4 heavily blurred (bad seeing)
        frames.append(np.roll(ref, 2, axis=1))
        frames.append(gaussian_filter(ref, 5.0))
        frames.append(np.roll(ref, -2, axis=1))
        frames.append(gaussian_filter(ref, 6.0))
        frames.append(ref)
        with tempfile.TemporaryDirectory(prefix="grs_qg_run_") as d:
            res = run_planetary_stacker(
                frames, Path(d), n_grid=6, ap_half=16,
                warp_mode="global", reference="first", quality_gate=0.6,
            )
        self.assertGreater(len(res.dropped_frames), 0)
        # the blurred frames (indices 2 and 4) must be among the dropped
        self.assertIn(2, res.dropped_frames)
        self.assertIn(4, res.dropped_frames)
        self.assertAlmostEqual(res.quality_gate, 0.6)


if __name__ == "__main__":
    unittest.main()

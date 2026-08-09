"""
Tests for image_warp.warp_shift2d — the exact spatial-domain sub-pixel
shift that replaced the broken FFT phase ramp in six call sites (v6.8.x
audit). These tests pin both the accuracy and the failure mode that must
never come back: an FFT phase ramp on real input returns the EVEN MIXTURE
(f(x-s)+f(x+s))/2 at non-integer s, i.e. ±s shifts are indistinguishable
(measured: byte-identical MSE 0.001077 for s=±1.5 px); warp_shift2d must
distinguish them to the interpolation floor (~0.005 px measured).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


def _field(seed: int = 3):
    rng = np.random.default_rng(seed)
    a = rng.normal(0.5, 0.02, (120, 120))
    yy, xx = np.mgrid[0:120, 0:120]
    a += 2.5 * np.exp(-((yy - 55.0) ** 2 + (xx - 70.0) ** 2) / 8.0)
    return a


def _centroid(img):
    w = img[40:85, 55:100].astype(np.float64)
    base = float(np.median(w))
    wt = np.clip(w - (base + 0.15), 0, None)
    s = float(wt.sum())
    y, x = np.mgrid[40:85, 55:100]
    return float((wt * x).sum() / s), float((wt * y).sum() / s)


class TestWarpShift2d(unittest.TestCase):
    def test_subpixel_shift_centroid_exact(self):
        from image_warp import warp_shift2d
        a = _field()
        x0, y0 = _centroid(a)
        self.assertAlmostEqual(x0, 70.0, places=1)
        self.assertAlmostEqual(y0, 55.0, places=1)
        # Measured 2026-08-07: worst centroid error 0.005 px over this table.
        for dy, dx in ((1.37, -2.71), (-0.55, 3.91), (0.0, 1.5), (0.0, -1.5)):
            b = warp_shift2d(a, dy, dx)
            x1, y1 = _centroid(b)
            self.assertAlmostEqual(x1 - x0, dx, delta=0.05,
                                   msg=f"dx mismatch for ({dy},{dx})")
            self.assertAlmostEqual(y1 - y0, dy, delta=0.05,
                                   msg=f"dy mismatch for ({dy},{dx})")

    def test_opposite_shifts_are_distinct(self):
        """Critical regression: the legacy FFT phase ramp made +s and -s
        INDISTINGUISHABLE (even mixture). warp_shift2d must separate them."""
        from image_warp import warp_shift2d
        a = _field()
        bp = warp_shift2d(a, 0.0, 1.5)
        bm = warp_shift2d(a, 0.0, -1.5)
        # If the even-mixture bug ever returns these two are identical.
        self.assertGreater(float(np.abs(bp - bm).max()), 0.5,
                           "+1.5 and -1.5 px shifts are indistinguishable — "
                           "FFT even-mixture bug is back")

    def test_rgb_channels_shift_identically(self):
        from image_warp import warp_shift2d
        a = _field()
        rgb = np.stack([a, 2.0 * a, 0.5 * a], axis=-1)
        out = warp_shift2d(rgb, 0.8, -1.2)
        self.assertEqual(out.shape, rgb.shape)
        mono = warp_shift2d(a, 0.8, -1.2)
        np.testing.assert_allclose(out[..., 0], mono, atol=1e-12)
        np.testing.assert_allclose(out[..., 1], 2.0 * mono, atol=1e-9)

    def test_rotate_about_centre_planted_dot(self):
        """win_jupos_derotator._rotate_about_centre: a planted dot must land
        at the R_image(theta)-rotated position to the interpolation floor
        (measured 0.003 px; gate 0.2 px). R_image is image-coords rotation:
        (dx', dy') = (c·dx - s·dy, s·dx + c·dy) — the same convention the
        module's _fit_rigid_rotation fits, verified 2026-08-07."""
        import math
        from win_jupos_derotator import _rotate_about_centre
        a = _field()
        x0, y0 = _centroid(a)
        th = 0.05
        c, s = math.cos(th), math.sin(th)
        ox, oy = x0 - 60.0, y0 - 60.0
        exp_x = 60.0 + c * ox - s * oy
        exp_y = 60.0 + s * ox + c * oy
        r = _rotate_about_centre(a, th, 60.0, 60.0)
        x1, y1 = _centroid(r)
        self.assertAlmostEqual(x1, exp_x, delta=0.2)
        self.assertAlmostEqual(y1, exp_y, delta=0.2)
        # round trip
        r2 = _rotate_about_centre(r, -th, 60.0, 60.0)
        x2, y2 = _centroid(r2)
        self.assertAlmostEqual(x2, x0, delta=0.2)
        self.assertAlmostEqual(y2, y0, delta=0.2)


if __name__ == "__main__":
    unittest.main()

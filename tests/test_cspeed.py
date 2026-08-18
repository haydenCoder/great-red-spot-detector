"""Parity contract for the optional C core (app/cspeed.c).

The C path is only acceptable if it is numerically indistinguishable from
the scipy/numpy reference path it replaces — speed that changes the answer
is a bug, not an optimisation.  These tests pin the measured reality:

- cs_sample3  vs scipy map_coordinates(prefilter=False):  max|d| ~1e-15
- cs_lk_step  vs numpy replication of the same kernel:    max|d| ~1e-14
- A/B of every integrated hot path (with vs without C):
  _lk_refine, warp_shift2d, warp_field2d, and a full stack_ap golden rig —
  stack outputs must agree to < 1e-9 (measured ~1e-13 .. 1e-12).

If no C compiler is available the C-parity tests skip (the scipy fallback
is then trivially "on both sides" and correct by construction).
"""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

import cspeed  # noqa: E402

HAVE = cspeed.HAVE_C


def _texture(seed: int = 5, h: int = 96, w: int = 72, sigma: float = 2.5):
    """Band-limited random texture in [0, 1]-ish (LK-wellposed)."""
    from scipy.ndimage import gaussian_filter
    rng = np.random.default_rng(seed)
    return gaussian_filter(rng.normal(size=(h, w)), sigma)


class TestKernelParity(unittest.TestCase):
    def setUp(self):
        self.rng = np.random.default_rng(11)
        self.img = self.rng.normal(size=(18, 24))
        self.coef = np.ascontiguousarray(cspeed.spline_prefilter(self.img))

    def test_build_selfcheck(self):
        if not HAVE:
            self.skipTest("no C compiler — scipy fallback active "
                          f"({cspeed.status_note()})")
        self.assertEqual(cspeed.LIB.cs_version(), 700)
        self.assertAlmostEqual(cspeed.LIB.cs_selfcheck(), 4.0 / 6.0, places=15)

    def test_sample3_parity(self):
        cspeed.set_enabled(True)
        from scipy.ndimage import map_coordinates
        ys = self.rng.uniform(-3, self.coef.shape[0] + 2, 2000)
        xs = self.rng.uniform(-3, self.coef.shape[1] + 2, 2000)
        want = map_coordinates(self.coef, [ys, xs], order=3,
                               mode="nearest", prefilter=False)
        got = cspeed.sample3(self.coef, ys, xs)
        d = float(np.abs(want - got).max())
        # measured on this codebase: 1.3e-15 (pure summation-order noise)
        self.assertLess(d, 1e-12, msg=f"sample3 max|d|={d:.2e}")

    def test_lk_sums_parity(self):
        n = 400
        ref = self.rng.normal(size=n)
        w = np.abs(self.rng.normal(size=n)) + 0.1
        y0 = self.rng.uniform(14, 30, n)
        x0 = self.rng.uniform(14, 36, n)
        for wnd in (None, w):
            cspeed.set_enabled(False)
            py = cspeed.lk_sums(self.coef, ref, wnd, y0.copy(), x0.copy(),
                                0.63, -1.28)
            cspeed.set_enabled(True)
            cc = cspeed.lk_sums(self.coef, ref, wnd, y0.copy(), x0.copy(),
                                0.63, -1.28)
            scale = max(1.0, max(abs(v) for v in py))
            d = max(abs(p - q) for p, q in zip(py, cc))
            # measured: 2.8e-14 plain / 2.6e-13 windowed
            self.assertLess(d / scale, 1e-12,
                            msg=f"lk_sums max|d|={d:.2e} scale={scale:.1f}")

    def test_field_warp3_parity(self):
        cspeed.set_enabled(True)
        from scipy.ndimage import map_coordinates
        h, w = 32, 24
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
        sy = yy + 0.9 * np.sin(xx / 5.0)
        sx = xx + 1.1 * np.cos(yy / 7.0)
        want = map_coordinates(self.img, [sy, sx], order=3, mode="nearest")
        got = cspeed.field_warp3(self.img, sy, sx)
        d = float(np.abs(want - got).max())
        self.assertLess(d, 1e-12, msg=f"field_warp3 max|d|={d:.2e}")


class TestIntegrationAB(unittest.TestCase):
    """With-C vs without-C on the real production call sites."""

    def test_lk_refine_ab(self):
        import ap_stacker
        base = _texture()
        moved = np.roll(base, 0)  # placeholder moved below by scipy shift
        from scipy.ndimage import shift as nd_shift
        moved = nd_shift(base, (-0.65, 1.30), order=3, mode="nearest")
        results = {}
        for enabled in (False, True):
            cspeed.set_enabled(enabled)
            ay, ax = ap_stacker._lk_refine(base[8:40, 8:40],
                                           moved[8:40, 8:40], 0.0, 0.0)
            results[enabled] = (ay, ax)
        py, cc = results[False], results[True]
        d = max(abs(py[0] - cc[0]), abs(py[1] - cc[1]))
        self.assertLess(d, 1e-9, msg=f"_lk_refine A/B d={d:.2e}")
        # and both must actually recover the plant (~0.001 px class):
        # apply-shift of moved = -(content shift) = (+0.65, -1.30)
        self.assertLess(abs(cc[0] - 0.65), 0.02)
        self.assertLess(abs(cc[1] + 1.30), 0.02)

    def test_warp_shift2d_ab(self):
        import image_warp
        img = _texture()
        outs = {}
        for enabled in (False, True):
            cspeed.set_enabled(enabled)
            outs[enabled] = image_warp.warp_shift2d(img, 0.7, -1.2)
        d = float(np.abs(outs[False] - outs[True]).max())
        self.assertLess(d, 1e-9, msg=f"warp_shift2d A/B max|d|={d:.2e}")
        rgb = np.dstack([img, 2 * img, -img])
        for enabled in (False, True):
            cspeed.set_enabled(enabled)
            outs[enabled] = image_warp.warp_shift2d(rgb, -0.4, 0.9)
        d3 = float(np.abs(outs[False] - outs[True]).max())
        self.assertLess(d3, 1e-9, msg=f"warp_shift2d RGB A/B max|d|={d3:.2e}")

    def test_warp_field2d_ab(self):
        import image_warp
        img = _texture()
        h, w = img.shape
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
        dy = 0.8 * np.sin(xx / 8.0)
        dx = 1.2 * np.cos(yy / 9.0)
        dy[1, 1] = np.nan  # NaN field entries are ZeroDisplacement by contract
        outs = {}
        for enabled in (False, True):
            cspeed.set_enabled(enabled)
            outs[enabled] = image_warp.warp_field2d(img, dy, dx)
        d = float(np.abs(outs[False] - outs[True]).max())
        self.assertLess(d, 1e-9, msg=f"warp_field2d A/B max|d|={d:.2e}")

    def test_stack_ap_golden_ab(self):
        """End-to-end: identical frames planted with subpixel shifts; the
        full APS+quality+blend pipeline with and without C must land within
        1e-9 (measured ~1e-13) and pick identical frame usage."""
        import ap_stacker
        from scipy.ndimage import shift as nd_shift
        base = _texture()
        rng = np.random.default_rng(3)
        frames = []
        for _ in range(10):
            dy, dx = rng.uniform(-1.6, 1.6, 2)
            f = nd_shift(base, (dy, dx), order=3, mode="nearest")
            frames.append(f + rng.normal(scale=0.01, size=base.shape))
        cfg = ap_stacker.APStackConfig(ap_size_px=32, keep_frac=0.5,
                                       drizzle=1)
        out = {}
        for enabled in (False, True):
            cspeed.set_enabled(enabled)
            out[enabled] = ap_stacker.stack_ap(frames, cfg)
        cspeed.set_enabled(True)
        a, b = out[False], out[True]
        ds = float(np.abs(a.stack - b.stack).max())
        dw = float(np.abs(a.weight - b.weight).max())
        dg = float(np.abs(a.global_shifts - b.global_shifts).max())
        self.assertTrue(np.array_equal(a.per_frame_used, b.per_frame_used),
                        msg="frame-usage decisions changed between paths")
        self.assertLess(ds, 1e-9, msg=f"stack max|d|={ds:.2e}")
        self.assertLess(dw, 1e-9, msg=f"weight max|d|={dw:.2e}")
        self.assertLess(dg, 1e-9, msg=f"global shifts max|d|={dg:.2e}")

    def tearDown(self):
        cspeed.set_enabled(True)


if __name__ == "__main__":
    unittest.main()

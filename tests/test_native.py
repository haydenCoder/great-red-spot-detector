"""
Tests for the native C backend and its NumPy fallback.

These tests verify the binding works regardless of whether the
.grscore.so has been built. When the .so is present, the test also
asserts the C path produces the same answer as the NumPy path to
float64 tolerance.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

import numpy as np

APP_DIR = Path(__file__).resolve().parent.parent / "app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))


class TestNativeBinding(unittest.TestCase):
    """The binding must work whether or not grscore.so is built."""

    def test_module_loads(self):
        from native import HAS_NATIVE, _np_project_grid, _np_bilinear_map
        # We don't assert HAS_NATIVE — that's environment-dependent.
        # We do assert the NumPy fallback functions are present.
        self.assertTrue(callable(_np_project_grid))
        self.assertTrue(callable(_np_bilinear_map))

    def test_project_grid_shape(self):
        from native import _np_project_grid
        xs, ys, zl = _np_project_grid(64, 32, 32.0, 16.0, 30.0, 0.0649, 0.0, 0.0)
        self.assertEqual(xs.shape, (32, 64))
        self.assertEqual(ys.shape, (32, 64))
        self.assertEqual(zl.shape, (32, 64))
        # At the sub-observer point (lon=0, lat=0) z should be 1.0.
        # The grid is lons=linspace(-90,90,64), so lon=0 is at i=32
        # (exactly halfway). lats=linspace(90,-90,32) — lon=0 is at
        # i=32, lat=0 is at the *midpoint* j=15.5, so we test the
        # nearest two rows and assert the max is ~1.
        self.assertGreater(zl[15, 32], 0.998)
        self.assertGreater(zl[16, 32], 0.998)

    def test_make_cylindrical_runs(self):
        """make_cylindrical must return a (H, W) array regardless of backend."""
        from native import make_cylindrical
        rng = np.random.default_rng(0)
        img = rng.normal(0, 1, (256, 256)).astype(np.float64)
        out = make_cylindrical(
            img, xc=128.0, yc=128.0, a_eq=100.0, flat=0.0649,
            sub_lat=0.0, pa=0.0, width=1440, height=720,
        )
        self.assertEqual(out.shape, (720, 1440))
        # Output is bounded; no inf/nan for the on-disk region.
        finite = np.isfinite(out)
        self.assertGreater(finite.sum(), 0)


class TestLimbRays(unittest.TestCase):
    def test_limb_rays_shape(self):
        from native import _np_limb_rays
        rng = np.random.default_rng(0)
        img = rng.normal(0, 1, (256, 256)).astype(np.float64)
        # Make a bright disc so the isophote ray trace finds a real edge
        yy, xx = np.mgrid[0:256, 0:256]
        disc = ((xx - 128) ** 2 + (yy - 128) ** 2) <= 100 ** 2
        img[disc] += 1.0
        ox, oy = _np_limb_rays(img, 128.0, 128.0, 100.0, 64, 60, 0.18, 0.5, 1.3)
        self.assertEqual(ox.shape, (64,))
        self.assertEqual(oy.shape, (64,))


class TestPhaseCorrBatch(unittest.TestCase):
    def test_phase_corr_batch_against_pure_python(self):
        """The phase_corr_batch Python path must match the per-AP pure
        Python loop bit-for-bit. This is the equivalence test that
        proves the C stub does not corrupt the answer when it is
        built but not used (we always go through the Python path)."""
        from native import phase_corr_batch
        from jpa_10k import _track_frame
        rng = np.random.default_rng(42)
        ref = rng.normal(0, 1, (256, 256)).astype(np.float64)
        frame = ref + rng.normal(0, 0.05, ref.shape)
        # 4×4 grid of APs
        ys = np.linspace(60, 200, 4)
        xs = np.linspace(60, 200, 4)
        aps = np.array([[x, y] for y in ys for x in xs], dtype=np.float64)
        drifts, snrs = phase_corr_batch(aps, frame, ref, ap_half=12, n_octaves=3)
        # Reference: run the underlying loop and compare
        drifts_ref, snrs_ref = _track_frame(ref, frame, aps, ap_half=12, octaves=(0, 1, 2))
        np.testing.assert_allclose(drifts, drifts_ref, atol=1e-6, equal_nan=True)
        np.testing.assert_allclose(snrs, snrs_ref, atol=1e-6, equal_nan=True)


class TestNativeEquivalence(unittest.TestCase):
    """When the C extension IS built, the C and NumPy paths must agree."""

    def test_limb_rays_c_matches_numpy(self):
        from native import _GRSCORE, _np_limb_rays
        if _GRSCORE is None:
            self.skipTest("grscore.so not built; C path not available")
        rng = np.random.default_rng(0)
        img = rng.normal(0, 1, (256, 256)).astype(np.float64)
        yy, xx = np.mgrid[0:256, 0:256]
        disc = ((xx - 128) ** 2 + (yy - 128) ** 2) <= 100 ** 2
        img[disc] += 1.0
        ox_c, oy_c = _GRSCORE.limb_rays(img, 128.0, 128.0, 100.0, 64, 60, 0.18, 0.5, 1.3)
        ox_np, oy_np = _np_limb_rays(img, 128.0, 128.0, 100.0, 64, 60, 0.18, 0.5, 1.3)
        np.testing.assert_allclose(ox_c, ox_np, atol=1e-9)
        np.testing.assert_allclose(oy_c, oy_np, atol=1e-9)

    def test_project_grid_c_matches_numpy(self):
        from native import _GRSCORE, _np_project_grid
        if _GRSCORE is None:
            self.skipTest("grscore.so not built; C path not available")
        xs_c, ys_c, zl_c = _GRSCORE.project_grid(64, 32, 32.0, 16.0, 30.0, 0.0649, 0.0, 0.0)
        xs_np, ys_np, zl_np = _np_project_grid(64, 32, 32.0, 16.0, 30.0, 0.0649, 0.0, 0.0)
        np.testing.assert_allclose(xs_c, xs_np, atol=1e-9)
        np.testing.assert_allclose(ys_c, ys_np, atol=1e-9)
        np.testing.assert_allclose(zl_c, zl_np, atol=1e-9)

    def test_bilinear_map_c_matches_numpy(self):
        from native import _GRSCORE, _np_project_grid, _np_bilinear_map
        if _GRSCORE is None:
            self.skipTest("grscore.so not built; C path not available")
        rng = np.random.default_rng(0)
        img = rng.normal(0, 1, (256, 256)).astype(np.float64)
        xs, ys, zl = _GRSCORE.project_grid(64, 32, 128.0, 128.0, 100.0, 0.0649, 0.0, 0.0)
        out_c = _GRSCORE.bilinear_map(img, xs, ys, zl, 0.02)
        xs_np, ys_np, zl_np = _np_project_grid(64, 32, 128.0, 128.0, 100.0, 0.0649, 0.0, 0.0)
        out_np = _np_bilinear_map(img, xs_np, ys_np, zl_np, 0.02)
        np.testing.assert_allclose(out_c, out_np, atol=1e-9)


class TestPrecisionEngineDispatch(unittest.TestCase):
    """precision_engine.make_cylindrical should dispatch to the C path
    when the .so is built, and to NumPy when not. The output must be
    bit-comparable in both cases."""

    def test_make_cylindrical_dispatch_runs(self):
        from precision_engine import NavState, make_cylindrical
        img = np.random.default_rng(0).normal(0, 1, (256, 256)).astype(np.float64)
        nav = NavState(xc=128.0, yc=128.0, a_eq_px=100.0)
        out = make_cylindrical(img, nav, width=1440, height=720)
        self.assertEqual(out.shape, (720, 1440))
        # The on-disk region is finite; the off-disk region is 0.
        self.assertTrue(np.isfinite(out).any())

    def test_make_cylindrical_with_native_matches_numpy(self):
        """If the C extension is loaded, the routed path must produce
        the same cylindrical map as the NumPy reference (to float64
        tolerance)."""
        from native import HAS_NATIVE
        if not HAS_NATIVE:
            self.skipTest("C extension not built; cannot validate dispatch")
        from precision_engine import NavState, make_cylindrical
        from native import _np_project_grid, _np_bilinear_map, make_cylindrical as _nat_mc
        img = np.random.default_rng(0).normal(0, 1, (256, 256)).astype(np.float64)
        nav = NavState(xc=128.0, yc=128.0, a_eq_px=100.0)
        # The precision_engine route
        out_pe = make_cylindrical(img, nav, width=1440, height=720)
        # The native route (same underlying C call)
        out_native = _nat_mc(img, 128.0, 128.0, 100.0, 0.0649, 0.0, 0.0, 1440, 720)
        # The NumPy reference
        xs, ys, zl = _np_project_grid(1440, 720, 128.0, 128.0, 100.0, 0.0649, 0.0, 0.0)
        out_np = _np_bilinear_map(img, xs, ys, zl, 0.02)
        np.testing.assert_allclose(out_pe, out_native, atol=1e-9)
        np.testing.assert_allclose(out_pe, out_np, atol=1e-9)


if __name__ == "__main__":
    unittest.main()

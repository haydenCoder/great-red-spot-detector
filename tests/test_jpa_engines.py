"""
Smoke tests for the new experimental engines (DCR, JPA-10K, JPA-10D, JPA-INF).

These are deliberately small and fast. They are NOT a scientific validation
of the engines — that would require a full real-frames pipeline. They verify
that:
  1. Each module imports cleanly.
  2. The shape of the output is what the UI expects.
  3. The "scientific" numbers (DCR shift, AP drift RMS, Fried r0) are in
     plausible ranges.
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


class TestDCR(unittest.TestCase):
    def test_edlen_n_minus_1_550nm(self):
        from dcr import edlen_n_minus_1, Atmosphere
        n = edlen_n_minus_1(550.0, Atmosphere())
        # Standard value is ~2.78e-4
        self.assertTrue(2.7e-4 < n < 2.9e-4, f"unexpected n-1 at 550nm: {n}")

    def test_edlen_decreases_with_wavelength(self):
        from dcr import edlen_n_minus_1, Atmosphere
        n_400 = edlen_n_minus_1(400.0, Atmosphere())
        n_700 = edlen_n_minus_1(700.0, Atmosphere())
        self.assertGreater(n_400, n_700, "n-1 should be larger at shorter wavelength")

    def test_dcr_shift_arcsec(self):
        from dcr import dcr_shift_arcsec
        # R - B at 30° zenith: should be ~0.5" in magnitude
        # (sign convention: B is shifted below R since blue refracts more)
        shift = dcr_shift_arcsec(z_deg=30.0, lam1_nm=658.0, lam2_nm=445.0)
        self.assertTrue(0.3 < abs(shift) < 1.5,
                        f"|R-B shift| at z=30 unexpectedly {abs(shift)}")

    def test_dcr_shift_zero_at_zero_zenith(self):
        from dcr import dcr_shift_arcsec
        shift = dcr_shift_arcsec(z_deg=0.0, lam1_nm=658.0, lam2_nm=445.0)
        self.assertAlmostEqual(shift, 0.0, places=6)

    def test_dcr_shift_per_channel(self):
        from dcr import dcr_shift_per_channel
        # Make a fake channel dict
        channels = {"R": np.zeros((40, 40)), "B": np.zeros((40, 40)), "G": np.zeros((40, 40))}
        shifts = dcr_shift_per_channel(channels, ref_name="G", z_deg=30.0,
                                       planet_radius_px=100.0, apparent_diameter_arcsec=40.0)
        self.assertEqual(shifts["G"], 0.0)  # reference is zero
        # B refracts more than G → B image is shifted toward the zenith
        # (down on the detector, positive pixel offset)
        # R refracts less than G → R image is shifted away from zenith
        # (up on the detector, negative pixel offset)
        self.assertGreater(shifts["B"], 0.0)
        self.assertLess(shifts["R"], 0.0)


class TestJPA10K(unittest.TestCase):
    def _make_frames(self, n: int = 8, h: int = 128, w: int = 128):
        rng = np.random.default_rng(0)
        # Make a synthetic planet-like image: bright disc + dark spot + noise
        yy, xx = np.mgrid[0:h, 0:w]
        cx, cy, r = w // 2, h // 2, min(h, w) // 3
        disk = ((xx - cx) ** 2 + (yy - cy) ** 2) <= r ** 2
        base = np.where(disk, 0.5, 0.05)
        # Add a small drift to each frame so the tracker has work to do
        frames = []
        for k in range(n):
            dy = (k - n / 2) * 0.5
            dx = (k - n / 2) * 0.3
            # Shift the base by integer pixel shifts
            shifted = np.roll(base, (int(round(dy)), int(round(dx))), axis=(0, 1))
            shifted += rng.normal(0, 0.01, shifted.shape)
            frames.append(shifted)
        return frames

    def test_run_jpa_10k_smoke(self):
        from jpa_10k import run_jpa_10k
        frames = self._make_frames(n=6, h=128, w=128)
        out_dir = Path("/tmp/jpa10k_test")
        out_dir.mkdir(parents=True, exist_ok=True)
        res = run_jpa_10k(frames, out_dir, n_grid=4, ap_half=8)
        self.assertEqual(res.n_frames, 6)
        self.assertGreater(res.n_aps, 0)
        self.assertGreaterEqual(res.mean_rms_drift_px, 0.0)
        self.assertTrue(Path(res.output_path).exists())


class TestJPA10D(unittest.TestCase):
    def _make_frames(self, n: int = 6, h: int = 128, w: int = 128):
        rng = np.random.default_rng(0)
        yy, xx = np.mgrid[0:h, 0:w]
        cx, cy, r = w // 2, h // 2, min(h, w) // 3
        disk = ((xx - cx) ** 2 + (yy - cy) ** 2) <= r ** 2
        base = np.where(disk, 0.5, 0.05)
        frames = []
        for k in range(n):
            dy = (k - n / 2) * 0.5
            dx = (k - n / 2) * 0.3
            shifted = np.roll(base, (int(round(dy)), int(round(dx))), axis=(0, 1))
            shifted += rng.normal(0, 0.01, shifted.shape)
            frames.append(shifted)
        return frames

    def test_run_jpa_10d_smoke(self):
        from jpa_10d import run_jpa_10d
        frames = self._make_frames(n=4, h=128, w=128)
        out_dir = Path("/tmp/jpa10d_test")
        out_dir.mkdir(parents=True, exist_ok=True)
        res = run_jpa_10d(frames, out_dir, n_grid=4, ap_half=8, zernike_size=12)
        self.assertEqual(res.n_frames, 4)
        self.assertEqual(len(res.tensor_shape), 10)
        self.assertGreaterEqual(res.mean_rms_drift_px, 0.0)
        self.assertTrue(Path(res.output_path).exists())


class TestJPAInf(unittest.TestCase):
    def _make_frames(self, n: int = 6, h: int = 128, w: int = 128):
        rng = np.random.default_rng(0)
        yy, xx = np.mgrid[0:h, 0:w]
        cx, cy, r = w // 2, h // 2, min(h, w) // 3
        disk = ((xx - cx) ** 2 + (yy - cy) ** 2) <= r ** 2
        base = np.where(disk, 0.5, 0.05)
        frames = []
        for k in range(n):
            dy = (k - n / 2) * 0.5
            dx = (k - n / 2) * 0.3
            shifted = np.roll(base, (int(round(dy)), int(round(dx))), axis=(0, 1))
            shifted += rng.normal(0, 0.01, shifted.shape)
            frames.append(shifted)
        return frames

    def test_run_jpa_inf_smoke(self):
        from jupiter_infinite_tensor_engine import run_jpa_inf
        frames = self._make_frames(n=4, h=128, w=128)
        out_dir = Path("/tmp/jpainf_test")
        out_dir.mkdir(parents=True, exist_ok=True)
        res = run_jpa_inf(frames, out_dir, n_grid=4, ap_half=8, path_samples=8)
        self.assertEqual(res.n_frames, 4)
        self.assertGreaterEqual(res.fried_r0_px, 0.0)
        self.assertGreaterEqual(res.mean_rms_drift_px, 0.0)
        self.assertTrue(Path(res.output_path).exists())

    def test_kolmogorov_structure(self):
        from jupiter_infinite_tensor_engine import kolmogorov_structure
        d = kolmogorov_structure(np.array([1.0, 5.0, 10.0]), r0_px=5.0)
        # D_phi at r0 is 6.88
        self.assertAlmostEqual(d[1], 6.88, places=2)
        # D_phi is monotonically increasing in r
        self.assertLess(d[0], d[1])
        self.assertLess(d[1], d[2])


if __name__ == "__main__":
    unittest.main()

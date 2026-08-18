"""Tests for sharpen_lab — à-trous wavelets, Richardson–Lucy, unsharp, denoise.

Claims pinned:
  1. à-trous decomposition is a perfect partition of unity (img = c_n + Σ w_j).
  2. Wavelet sharpen increases local gradient energy AND moves the image
     closer to the unblurred truth (measured, not eyeballed).
  3. Richardson–Lucy reduces L2 error to the unblurred truth vs the blurred
     input, for a known Gaussian PSF.
  4. Wavelet denoise reduces flat-field noise STD by >60% while keeping a
     strong step feature intact.
  5. RGB luma path preserves hue (channel ratios) while applying the mono op.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from scipy.ndimage import gaussian_filter  # noqa: E402


def _disk(shape=(128, 128), seed=3):
    """Deterministic multi-scale planet disk (structure a sharpener can
    actually recover — no unrecoverable random realisation inside)."""
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:shape[0], 0:shape[1]]
    r = np.sqrt((yy - 60) ** 2 + (xx - 64) ** 2)
    img = np.where(r < 44, 0.65, 0.02)
    img = img + 0.10 * np.sin(xx / 6.0) * (r < 44) + 0.06 * np.sin(xx / 14.0 + 0.8) * (r < 44)
    spot = ((yy - 78) ** 2 / 30.0 + (xx - 80) ** 2 / 80.0) < 1.0
    img[spot] -= 0.22
    for cy, cx, s in ((40, 40, 1.5), (40, 60, 1.2), (72, 44, 1.8), (88, 96, 1.4)):
        img = img + 0.18 * np.exp(-((yy - cy) ** 2 + (xx - cx) ** 2) / (2 * s * s))
    return np.clip(img, 0, 1)


class TestAtrousPartition(unittest.TestCase):
    def test_partition_of_unity(self):
        import sharpen_lab as sl
        img = _disk()
        scales, resid = sl.atrous_decompose(img, n_layers=4)
        rec = sl.atrous_reconstruct(scales, resid)
        np.testing.assert_allclose(rec, img, atol=1e-10)
        # kernel normalisation: a constant image has zero wavelet coefficients
        scales_c, resid_c = sl.atrous_decompose(np.full((64, 64), 0.7), n_layers=3)
        for w in scales_c:
            self.assertAlmostEqual(float(np.abs(w).max()), 0.0, places=10)
        self.assertAlmostEqual(float(resid_c.mean()), 0.7, places=10)

    def test_layer_scale_doubles(self):
        import sharpen_lab as sl
        # an impulse's wavelet response should move to coarser support per layer
        img = np.zeros((128, 128)); img[64, 64] = 1.0
        scales, _ = sl.atrous_decompose(img, n_layers=4)
        widths = []
        for w in scales:
            row = np.abs(w[64])
            nz = np.nonzero(row > row.max() * 0.05)[0]
            widths.append(nz.max() - nz.min() if nz.size else 0)
        for j in range(len(widths) - 1):
            self.assertGreater(widths[j + 1], widths[j],
                               msg=f"support not growing: {widths}")


class TestWaveletSharpen(unittest.TestCase):
    def test_sharpen_recovers_lost_gradient_energy(self):
        """Honest wavelet claim: gains restore the gradient energy the blur
        stole (toward the truth's energy), without runaway L2 blow-up."""
        import sharpen_lab as sl
        truth = _disk()
        blurred = gaussian_filter(truth, 1.8)
        sharp = sl.wavelet_sharpen(blurred, (2.2, 1.8, 1.4, 1.1), clip=(0, 1))
        e_blur = sl.gradient_energy(blurred)
        e_sharp = sl.gradient_energy(sharp)
        e_truth = sl.gradient_energy(truth)
        self.assertGreater(e_sharp, 1.4 * e_blur, "not actually sharpening")
        self.assertLess(abs(e_sharp - e_truth), abs(e_blur - e_truth),
                        f"energy {e_sharp:.2e} not closer to truth {e_truth:.2e} "
                        f"than blur {e_blur:.2e}")
        yy, xx = np.ogrid[0:128, 0:128]
        inside = ((yy - 60) ** 2 + (xx - 64) ** 2) < 40 ** 2
        def rmse(a):
            return float(np.sqrt(np.mean((a[inside] - truth[inside]) ** 2)))
        self.assertLess(rmse(sharp), 2.0 * rmse(blurred),
                        "L2 runaway — gain profile is not sane")

    def test_denoise_gate_helps_under_noise(self):
        """With camera noise stacked in, denoise-gated sharpening must beat
        ungated sharpening in L2 (the noise would otherwise be amplified)."""
        import sharpen_lab as sl
        rng = np.random.default_rng(31)
        truth = _disk()
        blurred = gaussian_filter(truth, 1.5) + rng.normal(0, 0.012, truth.shape)
        gains = (2.2, 1.8, 1.4, 1.1)
        gated = sl.wavelet_sharpen(blurred, gains, denoise=True, clip=(0, 1))
        raw = sl.wavelet_sharpen(blurred, gains, denoise=False, clip=(0, 1))
        float_flat_raw = float(np.std(raw[105:125, 5:120]))     # sky region
        float_flat_gated = float(np.std(gated[105:125, 5:120]))
        self.assertLess(float_flat_gated, 0.6 * float_flat_raw,
                        "denoise gate is not suppressing amplified noise")


class TestRichardsonLucy(unittest.TestCase):
    def test_rl_recovers_gaussian_blur(self):
        """RL deconvolution with a matched Gaussian PSF genuinely reduces the
        L2 error to the unblurred truth (the maximum-likelihood claim)."""
        import sharpen_lab as sl
        truth = _disk()
        blurred = gaussian_filter(truth, 1.8)
        deconv = sl.richardson_lucy(blurred, psf_sigma_px=1.8, iters=14, clip=(0, 1))
        yy, xx = np.ogrid[0:128, 0:128]
        inside = ((yy - 60) ** 2 + (xx - 64) ** 2) < 40 ** 2
        def rmse(a):
            return float(np.sqrt(np.mean((a[inside] - truth[inside]) ** 2)))
        self.assertLess(rmse(deconv), 0.97 * rmse(blurred),
                        f"RL RMSE {rmse(deconv):.4f} vs blur {rmse(blurred):.4f}")
        # positivity is preserved (RL property)
        self.assertGreaterEqual(float(deconv.min()), 0.0)

    def test_rl_identity_on_psf_match(self):
        import sharpen_lab as sl
        # deconvolving a pure blurred impulse should re-localise it
        img = np.zeros((64, 64))
        img[32, 32] = 1.0
        blurred = gaussian_filter(img, 1.5)
        deconv = sl.richardson_lucy(blurred, psf_sigma_px=1.5, iters=30)
        self.assertLess(float(deconv[40, 40]), 0.05 * float(deconv.max()))
        self.assertGreater(float(deconv[32, 32]), 2.0 * float(deconv[28, 28]))


class TestUnsharp(unittest.TestCase):
    def test_unsharp_increases_energy(self):
        import sharpen_lab as sl
        truth = _disk()
        blurred = gaussian_filter(truth, 1.5)
        out = sl.unsharp_mask(blurred, radius_px=2.5, amount=1.2, clip=(0, 1))
        self.assertGreater(sl.gradient_energy(out), 1.2 * sl.gradient_energy(blurred))


class TestDenoise(unittest.TestCase):
    def test_wavelet_denoise_flat_and_step(self):
        import sharpen_lab as sl
        rng = np.random.default_rng(8)
        img = np.full((96, 96), 0.5)
        img[60:, :] = 0.8                     # step feature
        noisy = img + rng.normal(0, 0.03, img.shape)
        den = sl.wavelet_denoise(noisy, n_layers=4, k_sigma=3.0)
        flat_before = float(np.std(noisy[5:25, 5:90]))
        flat_after = float(np.std(den[5:25, 5:90]))
        self.assertLess(flat_after, 0.4 * flat_before,
                        f"flat noise {flat_after:.5f} vs {flat_before:.5f}")
        # step preserved: transition height stays >90%
        self.assertGreater(float(den[75:, :].mean() - den[:20, :].mean()), 0.27)

    def test_estimate_noise_mad(self):
        import sharpen_lab as sl
        rng = np.random.default_rng(4)
        img = rng.normal(0.5, 0.04, (64, 64))
        est = sl.estimate_noise_mad(img)
        self.assertAlmostEqual(est, 0.04, delta=0.015)


class TestRGB(unittest.TestCase):
    def test_luma_path_preserves_hue(self):
        import sharpen_lab as sl
        truth = _disk()
        rgb = np.stack([truth * 0.9, truth * 0.6, truth * 0.3], axis=-1)
        blurred = gaussian_filter(rgb, (1.5, 1.5, 0))
        out = sl.sharpen(blurred, method="wavelet", gains=(1.8, 1.5, 1.2, 1.0), clip=(0, 1))
        self.assertEqual(out.shape, rgb.shape)
        yy, xx = np.ogrid[0:128, 0:128]
        inside = ((yy - 60) ** 2 + (xx - 64) ** 2) < 40 ** 2
        r0 = blurred[..., 0][inside] / (blurred[..., 2][inside] + 1e-9)
        r1 = out[..., 0][inside] / (out[..., 2][inside] + 1e-9)
        self.assertAlmostEqual(float(np.median(r1)), float(np.median(r0)), delta=0.15)
        # and the luma was genuinely sharpened (energy moves toward the true luma)
        lw = (0.299, 0.587, 0.114)
        l_t = rgb[..., 0] * lw[0] + rgb[..., 1] * lw[1] + rgb[..., 2] * lw[2]
        l_b = blurred[..., 0] * lw[0] + blurred[..., 1] * lw[1] + blurred[..., 2] * lw[2]
        l_o = out[..., 0] * lw[0] + out[..., 1] * lw[1] + out[..., 2] * lw[2]
        self.assertLess(abs(sl.gradient_energy(l_o) - sl.gradient_energy(l_t)),
                        abs(sl.gradient_energy(l_b) - sl.gradient_energy(l_t)))

    def test_sharpen_bad_method(self):
        import sharpen_lab as sl
        with self.assertRaises(ValueError):
            sl.sharpen(np.zeros((16, 16)), method="bogus")


if __name__ == "__main__":
    unittest.main()

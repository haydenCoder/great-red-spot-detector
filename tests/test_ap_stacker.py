"""Tests for ap_stacker — APS per-AP quality stacking + drizzle.

The headline claims pinned here:
  1. `_measure_shift` is subpixel-accurate (upsampled-DFT refined).
  2. End-to-end: planted global shifts are recovered and the stack beats a
     naive average.
  3. Local quality: on spatially-variable blur the per-AP stack is sharper on
     BOTH halves than any whole-frame lucky selection (the AutoStakkert trick).
  4. Drizzle super-resolution: with true subpixel dither + pixel-integration
     aliasing, the x2 drizzle reconstructs the fine grid with clearly lower
     RMSE than any upsampled single frame.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

import numpy as np

APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from scipy.ndimage import gaussian_filter, shift as _sshift  # noqa: E402


def _field(shape=(128, 128), sigma=2.0, seed=11):
    rng = np.random.default_rng(seed)
    return gaussian_filter(rng.normal(0.5, 0.18, shape), sigma=sigma)


def _lap_var(img):
    lap = (img[2:, 1:-1] + img[:-2, 1:-1] + img[1:-1, 2:] + img[1:-1, :-2]
           - 4.0 * img[1:-1, 1:-1])
    return float(np.var(lap))


class TestMeasureShift(unittest.TestCase):
    def test_subpixel_accuracy(self):
        from ap_stacker import _measure_shift
        base = _field(sigma=2.5)
        errs = []
        for (dy, dx) in [(2.25, -1.4), (-0.6, 0.5), (0.0, 0.0), (1.3, 1.1), (-2.0, -0.25)]:
            moved = _sshift(base, (dy, dx), order=3, mode="nearest")
            mdy, mdx, snr = _measure_shift(base, moved, refine=True)
            errs.append((abs(mdy + dy), abs(mdx + dx), snr))
        max_err = max(max(e, f) for e, f, _ in errs)
        self.assertLess(max_err, 0.15, msg=f"subpixel refine max err {max_err:.3f}px")
        self.assertGreater(min(s for *_, s in errs), 1.3)


class TestGlobalAlignment(unittest.TestCase):
    def test_recovers_planted_shifts_and_beats_naive(self):
        import ap_stacker
        base = _field(sigma=2.5)
        rng = np.random.default_rng(5)
        planted = [(0.0, 0.0)]
        frames = [base.copy()]
        for k in range(1, 8):
            dy, dx = rng.uniform(-3, 3), rng.uniform(-3, 3)
            planted.append((dy, dx))
            frames.append(_sshift(base, (dy, dx), order=3, mode="nearest")
                          + rng.normal(0, 0.005, base.shape))
        res = ap_stacker.stack_ap(frames, ap_stacker.APStackConfig(
            ap_size_px=24, keep_frac=0.75, drizzle=1, ref_index=0,
            max_local_shift_px=6))
        # global apply-shift must invert the planted content displacement
        for k in range(1, 8):
            gy, gx = res.global_shifts[k]
            self.assertAlmostEqual(gy, -planted[k][0], delta=0.4,
                                   msg=f"frame {k} global dy off")
            self.assertAlmostEqual(gx, -planted[k][1], delta=0.4,
                                   msg=f"frame {k} global dx off")
        naive = np.mean(frames, axis=0)
        sl = np.s_[20:-20, 20:-20]
        c_stack = np.corrcoef(res.stack[sl].ravel(), base[sl].ravel())[0, 1]
        c_naive = np.corrcoef(naive[sl].ravel(), base[sl].ravel())[0, 1]
        self.assertGreater(c_stack, c_naive + 0.01,
                           msg=f"stack corr {c_stack:.4f} vs naive {c_naive:.4f}")
        self.assertGreater(res.n_aps, 4)
        self.assertTrue(np.all(res.per_frame_used > 0))


class TestPerAPQuality(unittest.TestCase):
    def test_local_selection_sharpens_both_halves(self):
        import ap_stacker
        from frame_quality import select_best_frames
        base = _field(sigma=1.5)
        rng = np.random.default_rng(3)
        frames = []
        for k in range(8):
            f = base.copy()
            if k % 2 == 0:
                f[:, :64] = gaussian_filter(f[:, :64], sigma=2.0)   # mush left
            else:
                f[:, 64:] = gaussian_filter(f[:, 64:], sigma=2.0)  # mush right
            f = _sshift(f, (rng.uniform(-0.5, 0.5), rng.uniform(-0.5, 0.5)),
                        order=1, mode="nearest")
            frames.append(f)
        res = ap_stacker.stack_ap(frames, ap_stacker.APStackConfig(
            ap_size_px=24, keep_frac=0.5, drizzle=1, ref_index=0,
            normalize_brightness=False))
        # whole-frame lucky baseline: best 4 sharpest entire frames, averaged
        kept, _, _ = select_best_frames(frames, keep_frac=0.5, min_keep=4)
        lucky = np.mean([frames[i] for i in kept], axis=0)
        sl_l = np.s_[24:56, 16:48]
        sl_r = np.s_[24:56, 80:112]
        for name, sl in (("left", sl_l), ("right", sl_r)):
            aps_q = _lap_var(res.stack[sl])
            lucky_q = _lap_var(lucky[sl])
            self.assertGreater(aps_q, lucky_q * 1.15,
                               msg=f"{name} half: APS {aps_q:.6f} not sharper "
                                   f"than lucky {lucky_q:.6f}")


class TestDrizzleSuperResolution(unittest.TestCase):
    def _make_aliased_frames(self, truth, shifts):
        """Shift truth by subpixel (fine-px) offsets, then 2x2 bin (pixels
        integrate — honest aliasing) to 128x128 frames."""
        frames = []
        for (dy, dx) in shifts:
            moved = _sshift(truth, (dy, dx), order=1, mode="nearest")
            ds = moved.reshape(128, 2, 128, 2).mean(axis=(1, 3))
            frames.append(ds)
        return frames

    def test_drizzle_recovers_fine_grid(self):
        import ap_stacker
        rng = np.random.default_rng(21)
        truth = gaussian_filter(rng.normal(0.5, 0.2, (256, 256)), sigma=1.0)
        # subpixel (fine-px) dither grid: 16 frames at 0/.5/1/1.5 coarse px
        shifts = [(dy, dx) for dy in (0.0, 1.0, 2.0, 3.0) for dx in (0.0, 2.0, 1.0, 3.0)]
        frames = self._make_aliased_frames(truth, [(s[0], s[1]) for s in shifts])
        res = ap_stacker.stack_ap(frames, ap_stacker.APStackConfig(
            ap_size_px=24, spacing_px=12, keep_frac=0.85, drizzle=2,
            pixfrac=0.8, ref_index=0, max_local_shift_px=4,
            normalize_brightness=False))
        self.assertEqual(res.stack.shape, (256, 256))
        # interior only (edges have no coverage)
        sl = np.s_[48:-48, 48:-48]
        got = res.stack[sl]
        want = truth[sl]
        # optimal per-frame gain removal is NOT applied — honest raw RMSE
        rmse_drizzle = float(np.sqrt(np.mean((got - want) ** 2)))
        # baseline: best single upsampled frame (nearest = pixel replication)
        nn = np.repeat(np.repeat(frames[0], 2, axis=0), 2, axis=1)[sl]
        rmse_single = float(np.sqrt(np.mean((nn - want) ** 2)))
        # oracle: same deposits with the TRUE shifts (offline calibration of
        # the deposit machinery; the end-to-end number above must approach it)
        from ap_stacker import _drizzle_deposit
        num = np.zeros((256, 256))
        den = np.zeros((256, 256))
        for (dy, dx), fr in zip(shifts, frames):
            _drizzle_deposit(num, den, fr, 0, 0, -dy / 2.0, -dx / 2.0,
                             2, 0.8, 1.0, np.ones((128, 128)))
        oracle = np.where(den > 1e-12, num / den, 0.0)[sl]
        rmse_oracle = float(np.sqrt(np.mean((oracle - want) ** 2)))
        self.assertLess(rmse_drizzle, 0.65 * rmse_single,
                        msg=f"drizzle RMSE {rmse_drizzle:.5f} not < 65% of "
                            f"single-frame {rmse_single:.5f}")
        self.assertLess(rmse_drizzle, 1.25 * rmse_oracle,
                        msg=f"measured-shift drizzle {rmse_drizzle:.5f} not "
                            f"within 25% of exact-shift oracle {rmse_oracle:.5f}")
        # and drizzle beats the own-module x1 stack upsampled
        rmse1_stack = np.repeat(np.repeat(
            ap_stacker.stack_ap(frames, ap_stacker.APStackConfig(
                ap_size_px=24, spacing_px=12, keep_frac=0.85, drizzle=1,
                ref_index=0, normalize_brightness=False)).stack,
            2, axis=0), 2, axis=1)[sl]
        rmse_x1 = float(np.sqrt(np.mean((rmse1_stack - want) ** 2)))
        self.assertLess(rmse_drizzle, rmse_x1,
                        msg=f"x2 drizzle {rmse_drizzle:.5f} not better than "
                            f"x1 stack {rmse_x1:.5f}")


class TestRGBAndNormalization(unittest.TestCase):
    def test_rgb_shape_and_brightness_gain(self):
        import ap_stacker
        base = _field(sigma=2.0)
        rng = np.random.default_rng(9)
        frames = []
        for k in range(6):
            gain = 0.85 + 0.06 * k
            chans = [_sshift(base * gain, (rng.uniform(-0.5, 0.5),
                                           rng.uniform(-0.5, 0.5)),
                             order=1, mode="nearest")
                     for _ in range(3)]
            frames.append(np.stack(chans, axis=-1) + rng.normal(0, 0.003, base.shape + (3,)))
        res = ap_stacker.stack_ap(frames, ap_stacker.APStackConfig(
            ap_size_px=24, keep_frac=0.8, drizzle=1, ref_index=0,
            normalize_brightness=True))
        self.assertEqual(res.stack.ndim, 3)
        self.assertEqual(res.stack.shape[2], 3)
        self.assertTrue(np.isfinite(res.stack).all())
        # gain-normalised: stack interior mean close to the reference frame's
        ref_mean = float(np.mean(frames[0][30:-30, 30:-30]))
        got_mean = float(np.mean(res.stack[30:-30, 30:-30]))
        self.assertAlmostEqual(got_mean, ref_mean, delta=0.08 * ref_mean)


class TestReportAndGuards(unittest.TestCase):
    def test_report_and_bad_quality_name(self):
        import ap_stacker
        base = _field(sigma=2.0)
        res = ap_stacker.stack_ap([base, base.copy()], ap_stacker.APStackConfig(
            ap_size_px=24, keep_frac=0.5, ref_index=0))
        txt = ap_stacker.aps_report_text(res)
        self.assertIn("APS STACK REPORT", txt)
        self.assertIn("frames: 2", txt)
        with self.assertRaises(ValueError):
            ap_stacker.stack_ap([base], ap_stacker.APStackConfig(quality="bogus"))
        with self.assertRaises(ValueError):
            ap_stacker.stack_ap([], ap_stacker.APStackConfig())


if __name__ == "__main__":
    unittest.main()

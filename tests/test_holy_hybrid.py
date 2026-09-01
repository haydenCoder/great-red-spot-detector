"""
Tests for the holy-hybrid stacker, the WinJUPOS-style derotator, and
the HolyCNN training. These are smoke tests: they verify that the
modules import, run end-to-end on synthetic frames, and produce
physically-plausible numbers. They are NOT a scientific validation.
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


def _synthetic_jupiter_frames(n: int = 8, h: int = 192, w: int = 192,
                               seed: int = 0, dx_per_frame: float = 0.5):
    """
    Build a stack of n synthetic "Jupiter" frames with a known
    per-frame rigid-body rotation about the centre. Each frame is
    a noisy disc with a small dark spot.
    """
    rng = np.random.default_rng(seed)
    yy, xx = np.mgrid[0:h, 0:w]
    cx, cy = w / 2, h / 2
    r = min(h, w) * 0.35
    disk = ((xx - cx) ** 2 + (yy - cy) ** 2) <= r ** 2
    # Base disk: bright centre, falloff toward limb
    base = np.where(disk, 0.6 * (1.0 - 0.4 * np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / r), 0.02)
    # Add a "GRS" dark spot at a known location
    grs_x, grs_y = cx + 0.3 * r, cy
    spot = np.exp(-(((xx - grs_x) / (0.1 * r)) ** 2 + ((yy - grs_y) / (0.05 * r)) ** 2))
    base = np.where(disk, base * (1.0 - 0.5 * spot), base)
    frames = []
    for k in range(n):
        # Apply a small rotation about the centre
        theta = math.radians(k * dx_per_frame)
        cos_t, sin_t = math.cos(theta), math.sin(theta)
        xrot = (xx - cx) * cos_t - (yy - cy) * sin_t + cx
        yrot = (xx - cx) * sin_t + (yy - cy) * cos_t + cy
        # Bilinear resample
        x0 = np.clip(np.floor(xrot).astype(np.int64), 0, w - 2)
        y0 = np.clip(np.floor(yrot).astype(np.int64), 0, h - 2)
        dx = xrot - x0
        dy = yrot - y0
        rotated = (
            base[y0, x0] * (1 - dx) * (1 - dy)
            + base[y0, x0 + 1] * dx * (1 - dy)
            + base[y0 + 1, x0] * (1 - dx) * dy
            + base[y0 + 1, x0 + 1] * dx * dy
        )
        # Add noise
        rotated += rng.normal(0, 0.01, rotated.shape)
        frames.append(rotated.astype(np.float64))
    return frames


import math


class TestHolyCNN(unittest.TestCase):
    def test_init_and_forward(self):
        from holy_hybrid_stacker import HolyCNN, HOLY_CNN_PATCH
        net = HolyCNN(seed=0)
        # Patch = zeros
        patch = np.zeros((HOLY_CNN_PATCH, HOLY_CNN_PATCH), dtype=np.float64)
        q, dy, dx, cache = net._forward(patch)
        self.assertTrue(0.0 <= q <= 1.0)
        self.assertTrue(math.isfinite(dy))
        self.assertTrue(math.isfinite(dx))
        # Cache has the right keys
        self.assertIn("h", cache)

    def test_predict_batch(self):
        from holy_hybrid_stacker import HolyCNN, HOLY_CNN_PATCH
        net = HolyCNN(seed=1)
        patches = np.random.default_rng(0).normal(0, 1, (5, HOLY_CNN_PATCH, HOLY_CNN_PATCH))
        out = net.predict_batch(patches)
        self.assertEqual(out.shape, (5, 3))


class TestWinJuposDerotator(unittest.TestCase):
    def test_run_derotator_smoke(self):
        from win_jupos_derotator import run_win_jupos_derotate
        frames = _synthetic_jupiter_frames(n=6, h=128, w=128, seed=42, dx_per_frame=0.3)
        out_dir = Path("/tmp/wj_derot_test")
        out_dir.mkdir(parents=True, exist_ok=True)
        res = run_win_jupos_derotate(frames, out_dir, n_grid=4, ap_half=8)
        self.assertEqual(res.n_frames, 6)
        self.assertGreater(res.n_aps_eq, 0)
        # The rotation per frame should be ~0.3° (what we set)
        # (allow some tolerance for the noisy phase correlation)
        non_zero = [r for r in res.rotation_per_frame_deg if abs(r) > 0.05]
        self.assertGreater(len(non_zero), 0,
                           "expected non-zero rotation estimate for known input")
        # Output PNG exists
        self.assertTrue(Path(res.output_path).exists())

    def _mini_stack(self, **kw):
        from holy_hybrid_stacker import run_holy_hybrid
        frames = _synthetic_jupiter_frames(n=4, h=128, w=128, seed=7, dx_per_frame=0.2)
        out_dir = Path("/tmp/holy_hybrid_test"); out_dir.mkdir(parents=True, exist_ok=True)
        return run_holy_hybrid(frames, out_dir, n_grid=4, ap_half=8, n_importance=8,
                               train_cnn=False, **kw)

    def test_discarded_rbf_solve_stays_gone(self):
        """The per-frame RBF fit was computed for every frame and thrown away
        (43-74 ms per solve: 17-30 s on a 400-frame run). Pin that no stacking
        path calls it, so it cannot quietly come back as dead weight."""
        import holy_hybrid_stacker as hhs
        real = hhs._fit_rbf_velocity_field
        calls = []

        def spy(*a, **k):
            calls.append(1)
            return real(*a, **k)

        hhs._fit_rbf_velocity_field = spy
        try:
            res = self._mini_stack()
        finally:
            hhs._fit_rbf_velocity_field = real
        self.assertEqual(res.n_frames, 4)
        self.assertEqual(len(calls), 0, "RBF velocity field fitted but unused again")

    def test_rbf_smoothness_sigma_is_still_published(self):
        """`sigma_rbf` also feeds the published diagnostic, so removing the dead
        solve must not remove the number itself."""
        res = self._mini_stack()
        self.assertGreater(float(res.rbf_smoothness_sigma), 0.0)

    def test_rigid_rotation_fit(self):
        from win_jupos_derotator import _fit_rigid_rotation
        # Pure rotation by 0.05 rad about the centre: APs at known
        # positions should all return a fit close to 0.05.
        h, w = 128, 128
        aps = np.array([[64, 50], [80, 50], [48, 50], [96, 64]], dtype=np.float64)
        theta_true = 0.05
        # Drift convention: drifts = (dy, dx) per AP
        #   dy = (x - cx) sin θ + (y - cy)(cos θ - 1)  ≈ θ (x - cx)
        #   dx = (x - cx)(cos θ - 1) - (y - cy) sin θ  ≈ -θ (y - cy)
        drifts = np.column_stack([
            theta_true * (aps[:, 0] - w / 2),   # dy
            -theta_true * (aps[:, 1] - h / 2),  # dx
        ])
        fit = _fit_rigid_rotation(aps, drifts, (w / 2, h / 2))
        self.assertAlmostEqual(fit, theta_true, places=2)


class TestHolyHybridStacker(unittest.TestCase):
    def test_run_holy_hybrid_smoke(self):
        from holy_hybrid_stacker import run_holy_hybrid
        # Use a tiny config for speed
        frames = _synthetic_jupiter_frames(n=4, h=128, w=128, seed=7, dx_per_frame=0.2)
        out_dir = Path("/tmp/holy_hybrid_test")
        out_dir.mkdir(parents=True, exist_ok=True)
        res = run_holy_hybrid(
            frames, out_dir,
            n_grid=4, ap_half=8,
            n_importance=8,
            train_cnn=False,    # use random-init net; no training fallback
        )
        self.assertEqual(res.n_frames, 4)
        self.assertGreater(res.n_aps, 0)
        self.assertGreaterEqual(res.map_quality_mean, 0.0)
        self.assertLessEqual(res.map_quality_mean, 1.0)
        self.assertTrue(Path(res.output_path).exists())


class TestHolyCNNTraining(unittest.TestCase):
    def test_train_holy_cnn_short(self):
        """Run a short self-distillation training and verify it produces finite weights."""
        from holy_hybrid_stacker import train_holy_cnn, HolyCNN
        from pathlib import Path as _P
        out_path = _P("/tmp/holy_cnn_test_weights.npz")
        if out_path.exists():
            out_path.unlink()
        net = train_holy_cnn(
            n_samples=4, epochs=2, lr=0.01,
            out_path=out_path,
        )
        # All weights should be finite
        for name in ("w1", "b1", "w2", "b2", "wf", "bf", "w_q", "b_q", "w_d", "b_d"):
            arr = getattr(net, name)
            self.assertTrue(np.isfinite(arr).all(), f"non-finite {name}")
        # Output file exists and is non-trivial size
        self.assertTrue(out_path.exists())
        self.assertGreater(out_path.stat().st_size, 1000)


if __name__ == "__main__":
    unittest.main()

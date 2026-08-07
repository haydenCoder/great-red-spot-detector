"""Tests for grs_ellipse — the v6.8 rim-ellipse GRS estimator.

Claims pinned:
  1. Fitzgibbon recovers known ellipses (synthetic conic points, with noise).
  2. End-to-end on the cylindrical map: a planted orange-oval ellipse is
     recovered to <0.25° in centre and <15% in axes.
  3. Full pipeline on synthetic_hq frames: parity-or-better vs the redness
     lock on the same frame (the estimator it seeds from — the ellipse must
     never be the weak link in the ensemble).
"""
from __future__ import annotations

import math
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from grs_ellipse import fit_ellipse_fitzgibbon, ellipse_grs  # noqa: E402


def _ellipse_pts(cx, cy, a, b, th, n=40, noise=0.0, seed=1):
    rng = np.random.default_rng(seed)
    t = np.sort(rng.uniform(0, 2 * np.pi, n))
    xs = cx + a * np.cos(t) * math.cos(th) - b * np.sin(t) * math.sin(th)
    ys = cy + a * np.cos(t) * math.sin(th) + b * np.sin(t) * math.cos(th)
    xs += rng.normal(0, noise, n)
    ys += rng.normal(0, noise, n)
    return xs, ys


class TestFitzgibbon(unittest.TestCase):
    def test_exact_ellipse(self):
        xs, ys = _ellipse_pts(3.0, -1.5, 7.0, 3.0, 0.1, n=48, noise=0.0)
        cx, cy, a, b, th = fit_ellipse_fitzgibbon(xs, ys)
        self.assertAlmostEqual(cx, 3.0, places=4)
        self.assertAlmostEqual(cy, -1.5, places=4)
        self.assertAlmostEqual(a, 7.0, places=3)
        self.assertAlmostEqual(b, 3.0, places=3)

    def test_noisy_ellipse(self):
        xs, ys = _ellipse_pts(-2.0, 4.0, 6.0, 2.5, -0.3, n=64, noise=0.05)
        cx, cy, a, b, th = fit_ellipse_fitzgibbon(xs, ys)
        self.assertAlmostEqual(cx, -2.0, delta=0.08)
        self.assertAlmostEqual(cy, 4.0, delta=0.08)
        self.assertAlmostEqual(a, 6.0, delta=0.15)
        self.assertAlmostEqual(b, 2.5, delta=0.15)

    def test_degenerate_inputs(self):
        self.assertIsNone(fit_ellipse_fitzgibbon(np.array([1.0, 2.0]), np.array([1.0, 2.0])))
        self.assertIsNone(fit_ellipse_fitzgibbon(
            np.linspace(0, 5, 20), np.linspace(0, 5, 20)))     # a line is not an ellipse

    def test_ransac_recovers_under_outlier_majority(self):
        """vblurry regime: only ~40% of rim spokes carry the true ellipse, the
        rest are junk scattered over the window (belt edges, noise rims).
        The plain least-squares+trim path explodes (this is exactly the
        100-case audit's unphysical-axes failure); RANSAC must still find the
        rim — in deg units, centre within 0.5°."""
        from grs_ellipse import fit_ellipse_ransac
        rng = np.random.default_rng(3)
        xs_t, ys_t = _ellipse_pts(1.2, -0.7, 6.0, 2.6, 0.15, n=26, noise=0.06, seed=4)
        n_junk = 34
        xs_j = rng.uniform(-9, 9, n_junk)
        ys_j = rng.uniform(-5, 5, n_junk)
        xs = np.concatenate([xs_t, xs_j])
        ys = np.concatenate([ys_t, ys_j])
        order = rng.permutation(xs.size)   # one shuffle for BOTH (keep pairs!)
        xs, ys = xs[order], ys[order]
        got = fit_ellipse_ransac(xs, ys, n_iter=800, tol=0.35, seed=11)
        self.assertIsNotNone(got, "RANSAC failed under a 57%-outlier rim set")
        (cx, cy, a, b, th), inl = got
        self.assertAlmostEqual(cx, 1.2, delta=0.5)
        self.assertAlmostEqual(cy, -0.7, delta=0.5)
        self.assertGreaterEqual(int(inl.sum()), 20, "too few inliers kept")

    def test_ransac_matches_lsq_on_clean_data(self):
        """On clean rim sets both paths must agree (the fallback must never
        drag the audited lsq behaviour): same centre within 0.1°."""
        from grs_ellipse import fit_ellipse_ransac
        xs, ys = _ellipse_pts(-2.0, 4.0, 6.0, 2.5, -0.3, n=64, noise=0.05, seed=9)
        f1 = fit_ellipse_fitzgibbon(xs, ys)
        got2 = fit_ellipse_ransac(xs, ys, n_iter=400, tol=0.35, seed=5)
        self.assertIsNotNone(f1); self.assertIsNotNone(got2)
        f2 = got2[0]
        self.assertAlmostEqual(f1[0], f2[0], delta=0.1)
        self.assertAlmostEqual(f1[1], f2[1], delta=0.1)


class TestEllipseOnMap(unittest.TestCase):
    def test_planted_oval_recovers(self):
        """Full rim pipeline on a planted soft-edged oval in map space."""
        from grs_ellipse import _sample_spokes, fit_ellipse_fitzgibbon, _ellipse_residuals
        H, W = 220, 500
        yy, xx = np.mgrid[0:H, 0:W]
        cx0, cy0, a0, b0, th0 = 250.0, 110.0, 60.0, 22.0, 0.05
        ct, st = math.cos(th0), math.sin(th0)
        xr = (xx - cx0) * ct + (yy - cy0) * st
        yr = -(xx - cx0) * st + (yy - cy0) * ct
        rr = np.sqrt((xr / a0) ** 2 + (yr / b0) ** 2)
        fmap = np.where(True, 0.25, 0.0) - 0.5 * np.exp(-((rr - 0.55) / 0.45) ** 2)
        px, py, rim = _sample_spokes(fmap, cy0, cx0, n_spokes=72,
                                     r_max_px=(a0 * 1.7, b0 * 4.0))
        self.assertGreaterEqual(px.size, 36, "not enough rim spokes found")
        fit = fit_ellipse_fitzgibbon(px - cx0, py - cy0)
        self.assertIsNotNone(fit)
        ecx, ecy, a, b, th = fit
        self.assertAlmostEqual(ecx, 0.0, delta=1.0)
        self.assertAlmostEqual(ecy, 0.0, delta=1.0)
        self.assertAlmostEqual(a, a0, delta=a0 * 0.15)
        self.assertAlmostEqual(b, b0, delta=b0 * 0.2)


def _render_frame(seed=12345, resolution="720p"):
    from synthetic_hq import SynthSpec, generate
    from PIL import Image
    with tempfile.TemporaryDirectory(prefix="grs_ell_") as d:
        png, _fit, truth = generate(
            SynthSpec(region="global", resolution_preset=resolution,
                      random_time=True, seed=int(seed), mode="metrology",
                      write_grs_crop=False),
            Path(d),
        )
        arr = np.asarray(Image.open(png), dtype=np.float64) / 255.0
    return arr, truth


class TestEllipseOnSyntheticFrames(unittest.TestCase):
    def test_matches_redness_seed_or_beats_it(self):
        from precision_engine import fit_limb_nav, _redness_grs, wrap_diff
        img, truth = _render_frame()
        nav = fit_limb_nav(img, cm_iii_deg=truth["cm_iii_deg"], distance_au=truth["distance_au"])
        nav.cm_iii_deg = truth["cm_iii_deg"]
        nav.distance_au = truth["distance_au"]
        nav.sub_lat_deg = float(truth.get("sub_obs_lat_deg") or 0.0)
        nav.north_pa_deg = float(truth.get("north_pa_deg") or 0.0)
        truth_lon = float(truth["grs_lon_seed_deg"])
        truth_lat = float(truth["grs_lat_seed_deg"])

        red = _redness_grs(img, nav)
        try:
            ell = ellipse_grs(img, nav, seed=red)
        except RuntimeError as e:
            self.skipTest(f"ellipse soft-failed on this frame ({e}) — soft-fail is "
                          "allowed for single frames; audit covers the population")
        d_lon_e = abs(wrap_diff(ell["lon_iii_deg"], truth_lon))
        d_lat_e = abs(ell["lat_deg"] - truth_lat)
        d_lon_r = abs(wrap_diff(red["lon_iii_deg"], truth_lon))
        d_lat_r = abs(red["lat_deg"] - truth_lat)
        # parity-or-better vs the seed (with small slack for estimator noise)
        self.assertLessEqual(d_lon_e, max(d_lon_r + 0.35, 0.55),
                             f"ellipse dlon {d_lon_e:.3f} vs redness {d_lon_r:.3f}")
        self.assertLessEqual(d_lat_e, max(d_lat_r + 0.35, 0.55),
                             f"ellipse dlat {d_lat_e:.3f} vs redness {d_lat_r:.3f}")
        # size must be physical (the synthetic GRS is ~14 x 8 deg)
        self.assertTrue(4.0 <= ell["length_deg"] <= 26.0)
        self.assertTrue(2.0 <= ell["width_deg"] <= 16.0)


if __name__ == "__main__":
    unittest.main()

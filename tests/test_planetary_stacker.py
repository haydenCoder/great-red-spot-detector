"""Tests for planet_models + planetary_stacker (the generalised, improved
stacker with a per-latitude warp).

The headline test is an honest A/B: on synthetic frames with a genuine
per-latitude wind shift, the per-latitude warp must align the belts better
than the legacy single-global-translation warp (measured by per-belt
correlation peak — higher = better aligned).
"""
from __future__ import annotations

import math
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


def _render_ref(seed: int = 2024, resolution: str = "720p"):
    from synthetic_hq import SynthSpec, generate
    with tempfile.TemporaryDirectory(prefix="grs_pstack_") as d:
        png, _fit, truth = generate(
            SynthSpec(region="global", resolution_preset=resolution,
                      random_time=True, seed=int(seed), mode="metrology",
                      write_grs_crop=False),
            Path(d),
        )
        arr = np.asarray(Image.open(png), dtype=np.float64) / 255.0
    mono = 0.299 * arr[..., 0] + 0.587 * arr[..., 1] + 0.114 * arr[..., 2]
    return mono, truth


def _make_sheared_frames(ref, truth, n_frames=8, cm_drift=3.0):
    from zonal_stacker_benchmark import _apply_zonal_shift
    cm_ref = float(truth["cm_iii_deg"])
    frames = [ref]
    cm_list = [cm_ref]
    for k in range(1, n_frames):
        cm_k = cm_ref + k * cm_drift
        frames.append(_apply_zonal_shift(
            ref, cm_ref, cm_k,
            distance_au=float(truth["distance_au"]),
            sub_lat_deg=float(truth.get("sub_obs_lat_deg", 0.0) or 0.0),
            north_pa_deg=float(truth.get("north_pa_deg", 0.0) or 0.0),
        ))
        cm_list.append(cm_k)
    return frames, cm_list


class TestFitDxVsLatitude(unittest.TestCase):
    """Unit test for the per-latitude drift fit (the core accuracy piece)."""

    def test_recovers_known_latitude_shear(self):
        from planetary_stacker import fit_dx_vs_latitude
        from planet_models import JUPITER
        # Dense APs (≥4 per |lat| bin) with a known dx(lat) = 2 + 0.05·|lat|.
        ap_lats = np.linspace(-85, 85, 60)
        truth_dx = 2.0 + 0.05 * np.abs(ap_lats)   # 2..6 px, monotone in |lat|
        ap_drifts = np.stack([np.zeros_like(truth_dx), truth_dx], axis=1)
        snr = np.full(ap_lats.shape, 5.0)
        dx_bins, dy_g = fit_dx_vs_latitude(
            ap_lats, ap_drifts, snr, JUPITER, dt_s=0.0, deg_to_px=4.0,
        )
        # The fit is negated (apply = -measured); check it tracks latitude:
        # high correlation with the known (negated) curve and correct endpoints.
        centres = (np.arange(dx_bins.size) + 0.5) * (90.0 / dx_bins.size)
        expected = -(2.0 + 0.05 * centres)
        corr = float(np.corrcoef(dx_bins, expected)[0, 1])
        self.assertGreater(corr, 0.95, msg=f"dx-lat corr {corr:.3f}")
        self.assertAlmostEqual(dx_bins[0], expected[0], delta=1.0)   # equator
        self.assertAlmostEqual(dx_bins[-1], expected[-1], delta=1.5)  # pole
        # monotonicity: |apply shift| grows toward the pole for a monotone truth
        self.assertLess(abs(dx_bins[0]), abs(dx_bins[-1]))
        self.assertAlmostEqual(dy_g, 0.0, places=6)

    def test_empty_bins_filled_from_prior(self):
        from planetary_stacker import fit_dx_vs_latitude
        from planet_models import JUPITER
        # Only equatorial APs → high-latitude bins must come from the model.
        ap_lats = np.array([-2.0, 0.0, 2.0])
        ap_drifts = np.array([[0.0, 3.0], [0.0, 3.0], [0.0, 3.0]])
        snr = np.array([5.0, 5.0, 5.0])
        dx_bins, _ = fit_dx_vs_latitude(
            ap_lats, ap_drifts, snr, JUPITER, dt_s=200.0, deg_to_px=4.0,
        )
        # equatorial bin ~ -3.0 (negated measured); polar bins are model-filled
        # (prior) so they must be finite, not NaN/inf.
        self.assertTrue(np.all(np.isfinite(dx_bins)))


class TestPlanetaryStackerRuns(unittest.TestCase):
    def test_runs_jupiter(self):
        from planetary_stacker import run_planetary_stacker
        from planet_models import JUPITER
        ref, truth = _render_ref(seed=2024, resolution="720p")
        frames, cm_list = _make_sheared_frames(ref, truth, n_frames=5, cm_drift=2.0)
        with tempfile.TemporaryDirectory(prefix="grs_ps_jup_") as d:
            res = run_planetary_stacker(
                frames, Path(d), planet=JUPITER, n_grid=6, ap_half=16,
                cm_iii_per_frame=cm_list, reference="first",
            )
            self.assertTrue(Path(res.output_path).exists())
            self.assertEqual(res.planet, "Jupiter")
            self.assertEqual(res.warp_mode, "per_latitude")
            stack = np.asarray(Image.open(res.output_path), dtype=np.float64) / 255.0
            self.assertEqual(stack.shape, ref.shape)

    def test_runs_saturn_generalisation(self):
        from planetary_stacker import run_planetary_stacker
        from planet_models import SATURN
        # We don't have a Saturn synthetic renderer; reuse a Jupiter frame but
        # pass Saturn's profile to prove the pipeline is planet-agnostic (it
        # must not crash on Saturn's faster rotation / different flattening).
        ref, truth = _render_ref(seed=7, resolution="720p")
        frames = [ref, np.roll(ref, 2, axis=1)]
        with tempfile.TemporaryDirectory(prefix="grs_ps_sat_") as d:
            res = run_planetary_stacker(
                frames, Path(d), planet=SATURN, n_grid=6, ap_half=16,
                sub_lat_deg=float(truth.get("sub_obs_lat_deg", 0.0) or 0.0),
                north_pa_deg=float(truth.get("north_pa_deg", 0.0) or 0.0),
            )
            self.assertEqual(res.planet, "Saturn")
            self.assertTrue(Path(res.output_path).exists())

    def test_writes_report_card(self):
        from planetary_stacker import run_planetary_stacker, stacker_report_text
        ref, truth = _render_ref(seed=2024, resolution="720p")
        frames, cm_list = _make_sheared_frames(ref, truth, n_frames=5, cm_drift=2.0)
        with tempfile.TemporaryDirectory(prefix="grs_ps_rep_") as d:
            res = run_planetary_stacker(
                frames, Path(d), n_grid=6, ap_half=16,
                cm_iii_per_frame=cm_list, reference="first",
            )
            report_path = Path(d) / "stacker_report.txt"
            self.assertTrue(report_path.exists())
            txt = report_path.read_text()
            for key in ("PLANETARY STACK REPORT", "warp mode", "reference frame",
                        "warp consistency", "per-frame drift"):
                self.assertIn(key, txt)
            self.assertEqual(stacker_report_text(res).strip(), txt.strip())

    def test_rgb_input_yields_rgb_stack_with_colour(self):
        """RGB frames in -> RGB stack out, with channels differing (colour
        preserved, not collapsed to grey). Tracking runs on luminance."""
        from synthetic_hq import SynthSpec, generate
        from planetary_stacker import run_planetary_stacker
        with tempfile.TemporaryDirectory(prefix="grs_ps_rgb_") as d0:
            rgb_frames, cm_list = [], []
            for k in range(5):
                png, _fit, truth = generate(
                    SynthSpec(region="global", resolution_preset="720p", random_time=True,
                              seed=2024 + k, mode="metrology", write_grs_crop=False),
                    Path(d0),
                )
                rgb_frames.append(np.asarray(Image.open(png), dtype=np.float64) / 255.0)
                cm_list.append(float(truth["cm_iii_deg"]))
        with tempfile.TemporaryDirectory(prefix="grs_ps_rgb_out_") as d:
            res = run_planetary_stacker(
                rgb_frames, Path(d), n_grid=6, ap_half=16,
                cm_iii_per_frame=cm_list, reference="first",
            )
            stack = np.asarray(Image.open(res.output_path), dtype=np.float64) / 255.0
        self.assertEqual(stack.ndim, 3)
        self.assertEqual(stack.shape[2], 3)
        r, g, b = stack[..., 0], stack[..., 1], stack[..., 2]
        self.assertGreater(float(np.mean(np.abs(r - g))), 1e-3)
        self.assertGreater(float(np.mean(np.abs(r - b))), 1e-3)
        self.assertTrue(np.isfinite(stack).all())


class TestPerLatitudeWarpBeatsGlobal(unittest.TestCase):
    """The headline accuracy test: per-latitude warp > global translation
    on genuinely latitude-sheared synthetic frames."""

    def test_per_latitude_aligns_belts_better(self):
        from planetary_stacker import run_planetary_stacker
        from zonal_stacker_benchmark import _per_belt_residual_motion

        ref, truth = _render_ref(seed=2024, resolution="720p")
        frames, cm_list = _make_sheared_frames(ref, truth, n_frames=8, cm_drift=3.0)
        sub_lat = float(truth.get("sub_obs_lat_deg", 0.0) or 0.0)
        north_pa = float(truth.get("north_pa_deg", 0.0) or 0.0)

        def _stack(warp_mode):
            with tempfile.TemporaryDirectory(prefix=f"grs_ab_{warp_mode}_") as d:
                res = run_planetary_stacker(
                    frames, Path(d), n_grid=6, ap_half=16,
                    cm_iii_per_frame=cm_list,
                    sub_lat_deg=sub_lat, north_pa_deg=north_pa,
                    warp_mode=warp_mode, reference="first",
                )
                return np.asarray(Image.open(res.output_path), dtype=np.float64) / 255.0

        stack_global = _stack("global")
        stack_perlat = _stack("per_latitude")

        belt_g = _per_belt_residual_motion(stack_global, ref)
        belt_p = _per_belt_residual_motion(stack_perlat, ref)
        mean_g = float(np.mean([v["peak"] for v in belt_g.values()]))
        mean_p = float(np.mean([v["peak"] for v in belt_p.values()]))
        # Report the deltas for visibility even on pass.
        print(f"\n[per-lat vs global] mean per-belt peak: "
              f"global={mean_g:.4f}  per_latitude={mean_p:.4f}  "
              f"delta={mean_p - mean_g:+.4f}")
        self.assertGreaterEqual(
            mean_p, mean_g,
            f"per-latitude warp ({mean_p:.4f}) did not beat global ({mean_g:.4f})",
        )


class TestRobustCombine(unittest.TestCase):
    """The robust (sigma-clipped) combination rejects transient pixel defects
    that a plain weighted mean would stamp onto the stack."""

    def test_rejects_transient_cosmic_ray(self):
        from planetary_stacker import _robust_combine
        rng = np.random.default_rng(0)
        base = rng.normal(0.5, 0.02, (48, 48))
        frames = [base + rng.normal(0.0, 0.01, base.shape) for _ in range(9)]
        # a cosmic-ray / hot-pixel / one-frame shadow at a single pixel
        frames[3] = frames[3].copy()
        frames[3][24, 24] = 12.0

        robust = _robust_combine(frames, [1.0] * len(frames), sigma=3.0)
        plain = np.mean(np.stack(frames), axis=0)

        # robust value at the defect is pulled back to the consensus level;
        # the plain mean is dragged up by the 12.0 outlier.
        self.assertLess(float(robust[24, 24]), 1.0,
                        f"robust stack kept the outlier ({robust[24, 24]:.2f})")
        self.assertGreater(float(plain[24, 24]), 1.0,
                           f"plain mean did not register the planted outlier")
        # away from the defect both agree (robust ≈ plain)
        self.assertAlmostEqual(float(robust[10, 10]), float(plain[10, 10]), delta=0.05)

    def test_fewer_than_three_frames_degrades_to_mean(self):
        from planetary_stacker import _robust_combine
        frames = [np.full((8, 8), 0.5), np.full((8, 8), 1.5)]
        out = _robust_combine(frames, [1.0, 1.0])
        self.assertAlmostEqual(float(out[0, 0]), 1.0, places=6)

    def test_align_confidence_is_monotone(self):
        from planetary_stacker import _align_confidence as ac
        self.assertEqual(ac(0.0), 0.0)
        self.assertEqual(ac(float("nan")), 0.0)
        lo, mid, hi = ac(1.0), ac(10.0), ac(100.0)
        self.assertLess(lo, mid)
        self.assertLess(mid, hi)
        self.assertLessEqual(hi, 1.0 + 1e-9)

    def test_robust_stack_runs_on_rgb(self):
        from synthetic_hq import SynthSpec, generate
        from planetary_stacker import run_planetary_stacker
        with tempfile.TemporaryDirectory(prefix="grs_rob_rgb_") as d0:
            rgb_frames, cm_list = [], []
            for k in range(6):
                png, _fit, truth = generate(
                    SynthSpec(region="global", resolution_preset="540p", random_time=True,
                              seed=3100 + k, mode="metrology", write_grs_crop=False),
                    Path(d0),
                )
                rgb_frames.append(np.asarray(Image.open(png), dtype=np.float64) / 255.0)
                cm_list.append(float(truth["cm_iii_deg"]))
        with tempfile.TemporaryDirectory(prefix="grs_rob_rgb_out_") as d:
            res = run_planetary_stacker(
                rgb_frames, Path(d), n_grid=6, ap_half=16,
                cm_iii_per_frame=cm_list, reference="first", robust=True,
            )
            stack = np.asarray(Image.open(res.output_path), dtype=np.float64) / 255.0
        self.assertEqual(stack.shape, rgb_frames[0].shape)
        self.assertTrue(np.isfinite(stack).all())
        # the report must state which combination was used
        self.assertIn("sigma-clipped", "\n".join(res.notes))


if __name__ == "__main__":
    unittest.main()

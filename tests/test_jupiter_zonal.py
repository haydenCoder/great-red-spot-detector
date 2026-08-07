"""
Tests for the Jupiter-specialized zonal-shear AP stacker
(app/jupiter_zonal_stacker.py) and zonal-derotator
(app/jupiter_zonal_derotator.py).

These tests are fast and self-contained: they render a small
synthetic Jupiter, apply a known per-latitude zonal shift, then
verify the zonal-stacker recovers the alignment.

The zonal-stacker should beat the generic JPA-10K on Jupiter-like
data with strong zonal shear. The zonal-derotator's prior mode is
NOT a strict improvement over winjupos and is marked experimental.
"""
from __future__ import annotations

import math
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

APP = Path(__file__).resolve().parents[1] / "app"
TOOLS = Path(__file__).resolve().parents[1] / "tools"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))


def _render_synthetic(seed: int = 2024, resolution: str = "720p"):
    from synthetic_hq import SynthSpec, generate
    with tempfile.TemporaryDirectory(prefix="grs_ztest_") as d:
        png, _fit, truth = generate(
            SynthSpec(
                region="global", resolution_preset=resolution,
                random_time=True, seed=int(seed), mode="metrology",
                write_grs_crop=False,
            ),
            Path(d),
        )
        arr = np.asarray(Image.open(png), dtype=np.float64) / 255.0
    return arr, truth


def _apply_zonal_shift(img, cm_ref, cm_frame, distance_au=5.2, sub_lat=0.0, north_pa=0.0):
    """Reuse the benchmark's helper."""
    from zonal_stacker_benchmark import _apply_zonal_shift as _impl
    return _impl(img, cm_ref, cm_frame,
                 distance_au=distance_au,
                 sub_lat_deg=sub_lat, north_pa_deg=north_pa)


def _per_belt_corr(stack, ref):
    from zonal_stacker_benchmark import _per_belt_residual_motion
    return _per_belt_residual_motion(stack, ref)


def _make_zonal_frames(n_frames: int = 8, cm_drift: float = 0.4):
    """Build a list of frames with zonal-shear shift applied per frame."""
    ref_rgb, truth = _render_synthetic(seed=2024, resolution="720p")
    ref = 0.299 * ref_rgb[..., 0] + 0.587 * ref_rgb[..., 1] + 0.114 * ref_rgb[..., 2]
    cm_ref = float(truth["cm_iii_deg"])
    frames = [ref]
    cm_list = [cm_ref]
    for k in range(1, n_frames):
        cm_k = cm_ref + k * cm_drift
        frames.append(_apply_zonal_shift(
            ref, cm_ref, cm_k,
            distance_au=float(truth["distance_au"]),
            sub_lat=float(truth.get("sub_obs_lat_deg", 0.0) or 0.0),
            north_pa=float(truth.get("north_pa_deg", 0.0) or 0.0),
        ))
        cm_list.append(cm_k)
    return frames, ref, cm_list, truth


class TestZonalWindModel(unittest.TestCase):
    """The zonal-wind model sanity checks."""

    def test_sys3_rate_is_positive(self):
        from jupiter_zonal_stacker import (
            _zonal_wind_rate_at_lat_deg_per_s, SYS3_RATE_DEG_PER_S,
        )
        self.assertGreater(SYS3_RATE_DEG_PER_S, 0.0)
        # The rate at lat=0 should be positive (prograde) and close to SYS3
        rate_eq = _zonal_wind_rate_at_lat_deg_per_s(0.0)
        self.assertGreater(rate_eq, 0.0)

    def test_zonal_wind_residual_is_symmetric(self):
        from jupiter_zonal_stacker import _zonal_wind_residual_mps
        for lat in (5, 10, 20, 30, 45, 60):
            self.assertAlmostEqual(
                _zonal_wind_residual_mps(lat),
                _zonal_wind_residual_mps(-lat),
                places=9,
                msg=f"residual not symmetric at lat={lat}",
            )


class TestZonalStacker(unittest.TestCase):
    """The Jupiter-specialized zonal-stacker must beat generic JPA-10K
    on synthetic data with zonal-shear motion."""

    def test_zonal_stacker_runs(self):
        from jupiter_zonal_stacker import run_jupiter_zonal_stacker
        frames, ref, cm_list, truth = _make_zonal_frames(n_frames=6, cm_drift=0.4)
        with tempfile.TemporaryDirectory(prefix="grs_zstk_") as d:
            res = run_jupiter_zonal_stacker(
                frames, Path(d), n_grid=6, ap_half=16,
                cm_iii_deg=cm_list[0],
                distance_au=float(truth["distance_au"]),
                sub_lat_deg=float(truth.get("sub_obs_lat_deg", 0.0) or 0.0),
                north_pa_deg=float(truth.get("north_pa_deg", 0.0) or 0.0),
                cm_iii_per_frame=cm_list,
                dt_s_per_frame=[k * 8.0 for k in range(6)],
            )
            self.assertTrue(Path(res.output_path).exists())
            # The output should be finite and have the right shape
            stack = np.asarray(Image.open(res.output_path), dtype=np.float64) / 255.0
            self.assertEqual(stack.shape, ref.shape)

    def test_zonal_stacker_beats_generic_on_zonal_shear(self):
        """On synthetic with zonal-shear shift, the zonal-stacker
        should produce a stack with higher per-belt correlation to
        the reference than the generic JPA-10K stacker."""
        from jupiter_zonal_stacker import run_jupiter_zonal_stacker
        from jpa_10k import run_jpa_10k
        frames, ref, cm_list, truth = _make_zonal_frames(n_frames=8, cm_drift=0.4)
        with tempfile.TemporaryDirectory(prefix="grs_zstk_bench_") as d:
            d = Path(d)
            # JPA-10K
            res_jpa = run_jpa_10k(frames, d / "jpa", n_grid=6, ap_half=16)
            stack_jpa = np.asarray(Image.open(res_jpa.output_path), dtype=np.float64) / 255.0
            belt_jpa = _per_belt_corr(stack_jpa, ref)
            # Zonal
            res_z = run_jupiter_zonal_stacker(
                frames, d / "zonal", n_grid=6, ap_half=16,
                cm_iii_deg=cm_list[0],
                distance_au=float(truth["distance_au"]),
                sub_lat_deg=float(truth.get("sub_obs_lat_deg", 0.0) or 0.0),
                north_pa_deg=float(truth.get("north_pa_deg", 0.0) or 0.0),
                cm_iii_per_frame=cm_list,
                dt_s_per_frame=[k * 8.0 for k in range(8)],
            )
            stack_z = np.asarray(Image.open(res_z.output_path), dtype=np.float64) / 255.0
            belt_z = _per_belt_corr(stack_z, ref)
            # Compare overall per-belt mean peak
            mean_jpa = float(np.mean([v["peak"] for v in belt_jpa.values()]))
            mean_z = float(np.mean([v["peak"] for v in belt_z.values()]))
            self.assertGreater(
                mean_z, mean_jpa,
                f"zonal-stacker ({mean_z:.4f}) did not beat JPA-10K ({mean_jpa:.4f}) "
                "on zonal-shear synthetic",
            )


class TestZonalDerotator(unittest.TestCase):
    """The zonal-derotator is a per-row prior-based derotator. It is
    NOT a strict improvement over winjupos on synthetic data with
    rigid rotation; it is marked experimental."""

    def test_zonal_derotator_runs_in_prior_mode(self):
        from jupiter_zonal_derotator import run_jupiter_zonal_derotate
        frames, ref, cm_list, truth = _make_zonal_frames(n_frames=4, cm_drift=0.4)
        with tempfile.TemporaryDirectory(prefix="grs_zder_") as d:
            res = run_jupiter_zonal_derotate(
                frames, Path(d),
                cm_iii_per_frame=cm_list,
                dt_s_per_frame=[k * 8.0 for k in range(4)],
                sub_lat_deg=float(truth.get("sub_obs_lat_deg", 0.0) or 0.0),
                north_pa_deg=float(truth.get("north_pa_deg", 0.0) or 0.0),
                mode="prior",
            )
            self.assertTrue(Path(res.output_path).exists())

    def test_zonal_derotator_runs_in_measurement_mode(self):
        from jupiter_zonal_derotator import run_jupiter_zonal_derotate
        frames, ref, cm_list, truth = _make_zonal_frames(n_frames=4, cm_drift=0.4)
        with tempfile.TemporaryDirectory(prefix="grs_zder_") as d:
            res = run_jupiter_zonal_derotate(
                frames, Path(d),
                cm_iii_per_frame=cm_list,
                dt_s_per_frame=[k * 8.0 for k in range(4)],
                sub_lat_deg=float(truth.get("sub_obs_lat_deg", 0.0) or 0.0),
                north_pa_deg=float(truth.get("north_pa_deg", 0.0) or 0.0),
                mode="measurement",
            )
            self.assertTrue(Path(res.output_path).exists())

    def test_zonal_derotator_physics_on_rotating_video(self):
        """The v6.8-consolidated derotator must do real physics, not just run.

        METHOD (why not a correlation window): the video_synth texture is
        smooth at the GRS (core sigma ~11 px), and we measured that EVERY
        image-domain shift metric on it mis-locks on this data — phase-corr
        windows returned contradictory integers (guard-clamped LK), NCC of
        the smooth spot sat at offset 0 with rho=1.000 for all frames, and
        disk MSE is dominated by tip/tilt walk + seeing/gain jitter, not by
        rotation. So we plant a SHARP 5x5 fiducial dot at the renderer's own
        truth GRS pixel (per frame, from the cm schedule) and centroid it —
        razor: sharp dots centroid to ~0.05 px and cannot mis-lock. Tip/tilt
        walk is disabled so rotation is the ONLY motion in play.

        PHYSICS PINS (measured 2026-08-07, this exact setup):
          truth GRS drift over 180 s: -4.33 px (renderer cm schedule)
          derotated fiducial worst error:  prior 0.273, measurement 0.274,
            hybrid 0.274 px  (>= 93% of the motion removed, all modes)
        """
        import video_synth
        from ap_stacker import _to_luma, derotate_frames
        from precision_engine import (
            NavState, lonlat_to_planet_xyz, planet_xyz_to_px)
        from planet_models import JUPITER

        spec = video_synth.VideoSynthSpec(
            width=512, height=384, n_frames=4, fps=1 / 60.0,
            seeing_fwhm_px=(0.8, 1.4), noise_rms=(0.001, 0.004),
            shift_rms_px=0.0, wave_amp=0.0, seed=5)
        vs = video_synth.render_video(spec)
        tr = vs.truth
        mono = [_to_luma(f, (0.299, 0.587, 0.114)) for f in vs.frames]
        dts = [t - vs.times_s[0] for t in vs.times_s]
        cm_list = [float(c) for c in tr["cm_iii_per_frame_deg"]]

        def grs_px(k):
            nav = NavState(xc=float(tr["disk_xc_px"]), yc=float(tr["disk_yc_px"]),
                           a_eq_px=float(tr["disk_a_eq_px"]),
                           flattening=video_synth.FLAT, distance_au=5.0,
                           cm_iii_deg=cm_list[k], sub_lat_deg=0.0,
                           north_pa_deg=0.0)
            lon_rel = ((float(tr["grs_lon_iii_deg"]) - cm_list[k] + 540.0)
                       % 360.0) - 180.0
            X, Y, Z = lonlat_to_planet_xyz(lon_rel, float(tr["grs_lat_deg"]))
            xx, yy, _ = planet_xyz_to_px(X, Y, Z, nav)
            return xx, yy

        planted = []
        for k, f in enumerate(mono):
            g = f.copy()
            x, y = grs_px(k)
            xi, yi = int(round(x)), int(round(y))
            g[yi - 2:yi + 3, xi - 2:xi + 3] -= 0.45
            planted.append(g)

        def dot_centroid(img, xr, yr, rad=9):
            w_ = img[yr - rad:yr + rad + 1, xr - rad:xr + rad + 1]
            floor = float(np.median(w_))
            weights = np.clip(floor - w_, 0.0, None)
            s = float(weights.sum())
            yy, xx = np.mgrid[yr - rad:yr + rad + 1, xr - rad:xr + rad + 1]
            return float((weights * xx).sum() / s), float((weights * yy).sum() / s), s

        x0, y0 = grs_px(0)
        xr0, yr0 = int(round(x0)), int(round(y0))
        c0 = dot_centroid(planted[0], xr0, yr0)
        # Sanity: the planted marker really moves in the raw frames.
        raw_motion = []
        for k in (1, 2, 3):
            ck = dot_centroid(planted[k], xr0, yr0)
            raw_motion.append(math.hypot(ck[0] - c0[0], ck[1] - c0[1]))
        self.assertGreater(max(raw_motion), 2.0,
                           f"setup too gentle to verify (raw {raw_motion})")
        # Analytic truth drift at frame 3 (renderer-reported geometry):
        x3, y3 = grs_px(3)
        truth_drift = math.hypot(x3 - x0, y3 - y0)
        self.assertGreater(truth_drift, 3.0)

        for mode in ("prior", "measurement", "hybrid"):
            der, info = derotate_frames(planted, dt_s_per_frame=dts,
                                        mode=mode, planet=JUPITER, ref_index=0)
            errs = []
            for k in (1, 2, 3):
                ck = dot_centroid(der[k], xr0, yr0)
                errs.append(math.hypot(ck[0] - c0[0], ck[1] - c0[1]))
            worst = max(errs)
            print(f"\n[zonal-derot physics] mode={mode} fiducial errs "
                  f"{[round(e, 3) for e in errs]} px "
                  f"(truth drift {truth_drift:.2f} px)")
            self.assertLess(worst, 0.5,
                            f"{mode} derotation left {worst:.2f} px of the "
                            f"{truth_drift:.2f} px truth drift")
            self.assertLess(worst, 0.15 * truth_drift,
                            f"{mode} removed < 85% of the rotation drift")
        # Phantom-dy regression: the fiducial must stay on the truth row
        # (the pre-fix blindly-fit dy moved markers ~0.8 px in y on frames
        # whose true dy was 0 — v6.8.x fiducial audit).
        der, _info = derotate_frames(planted, dt_s_per_frame=dts,
                                     mode="measurement", planet=JUPITER,
                                     ref_index=0)
        ck = dot_centroid(der[3], xr0, yr0)
        self.assertLess(abs(ck[1] - c0[1]), 0.4,
                        "derotated fiducial moved in y — phantom dy back?")

    def test_ap_track_gates_unit(self):
        """gate_ap_track: limb boxes and post-prior residual outliers are
        rejected; good on-disk tracks pass. Pins the AutoStakkert-style AP
        gating measured in the v6.8.x zonal audit (limb boxes mis-locked by
        2-8 px with HIGH SNR; residual outliers up to -8.23 px)."""
        from planetary_stacker import gate_ap_track
        from precision_engine import NavState

        nav = NavState(xc=256.0, yc=192.0, a_eq_px=160.0, flattening=0.06487,
                       distance_au=5.2, cm_iii_deg=0.0, sub_lat_deg=0.0,
                       north_pa_deg=0.0)
        # good mid-disk AP with a small residual -> accept
        self.assertTrue(gate_ap_track(nav, (256.0, 192.0), 0.1, -3.2, -3.3))
        # off-disk (rr > 1: sky) -> reject even with a perfect residual
        self.assertFalse(gate_ap_track(nav, (460.0, 99.0), 0.0, -3.3, -3.3))
        # limb-edge box (rr ~0.96: locks on the geometric edge) -> reject
        self.assertFalse(gate_ap_track(nav, (132.0, 99.0), 0.0, -3.3, -3.3))
        # post-prior residual 8 px >> allowed -> reject (mis-lock)
        self.assertFalse(gate_ap_track(nav, (256.0, 192.0), 0.0, 4.7, -3.3))
        self.assertFalse(gate_ap_track(nav, (256.0, 192.0), 6.0, -3.3, -3.3))
        # NaN track -> reject
        self.assertFalse(gate_ap_track(nav, (256.0, 192.0), float("nan"),
                                       float("nan"), -3.3))
        # long captures: the residual allowance grows with the prior magnitude
        self.assertTrue(gate_ap_track(nav, (256.0, 192.0), 0.0, -19.0, -16.0))


if __name__ == "__main__":
    unittest.main()

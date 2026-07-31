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


if __name__ == "__main__":
    unittest.main()

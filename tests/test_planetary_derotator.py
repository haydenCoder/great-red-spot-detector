"""Tests for the planet-generalised per-latitude derotator."""
from __future__ import annotations

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


def _ref_and_sheared(n_frames=5, cm_drift=3.0, seed=2024):
    from synthetic_hq import SynthSpec, generate
    from zonal_stacker_benchmark import _apply_zonal_shift
    with tempfile.TemporaryDirectory(prefix="grs_pder_") as d:
        png, _fit, truth = generate(
            SynthSpec(region="global", resolution_preset="720p", random_time=True,
                      seed=seed, mode="metrology", write_grs_crop=False),
            Path(d),
        )
        arr = np.asarray(Image.open(png), dtype=np.float64) / 255.0
    ref = 0.299 * arr[..., 0] + 0.587 * arr[..., 1] + 0.114 * arr[..., 2]
    cm0 = float(truth["cm_iii_deg"])
    sub = float(truth.get("sub_obs_lat_deg", 0.0) or 0.0)
    pa = float(truth.get("north_pa_deg", 0.0) or 0.0)
    frames = [ref]
    cm_list = [cm0]
    for k in range(1, n_frames):
        cm_k = cm0 + k * cm_drift
        frames.append(_apply_zonal_shift(
            ref, cm0, cm_k, distance_au=float(truth["distance_au"]),
            sub_lat_deg=sub, north_pa_deg=pa))
        cm_list.append(cm_k)
    return ref, frames, cm_list, sub, pa


class TestPlanetaryDerotator(unittest.TestCase):
    def _run(self, mode):
        from planetary_derotator import run_planetary_derotate
        ref, frames, cm_list, sub, pa = _ref_and_sheared()
        with tempfile.TemporaryDirectory(prefix=f"grs_pder_{mode}_") as d:
            res = run_planetary_derotate(
                frames, Path(d), n_grid=6, ap_half=16,
                cm_iii_per_frame=cm_list, sub_lat_deg=sub, north_pa_deg=pa,
                mode=mode, reference="first",
            )
            self.assertTrue(Path(res.output_path).exists())
            stack = np.asarray(Image.open(res.output_path), dtype=np.float64) / 255.0
            self.assertEqual(stack.shape, ref.shape)
            self.assertTrue(np.isfinite(stack).all())
            return res

    def test_runs_measurement_mode(self):
        self.assertEqual(self._run("measurement").mode, "measurement")

    def test_runs_prior_mode(self):
        # prior mode derotates with the planet model only — must not crash and
        # must produce a finite stack even with no AP tracking.
        self.assertEqual(self._run("prior").mode, "prior")

    def test_runs_hybrid_mode(self):
        self.assertEqual(self._run("hybrid").mode, "hybrid")

    def test_generalises_to_saturn(self):
        from planetary_derotator import run_planetary_derotate
        from planet_models import SATURN
        ref, frames, cm_list, sub, pa = _ref_and_sheared(n_frames=3)
        with tempfile.TemporaryDirectory(prefix="grs_pder_sat_") as d:
            res = run_planetary_derotate(
                frames, Path(d), planet=SATURN, n_grid=6, ap_half=16,
                cm_iii_per_frame=cm_list, sub_lat_deg=sub, north_pa_deg=pa,
                mode="prior", reference="first",
            )
            self.assertEqual(res.planet, "Saturn")
            self.assertTrue(Path(res.output_path).exists())

    def test_measurement_beats_raw_on_sheared_data(self):
        """The measurement-mode derotator should align sheared frames at least
        as well as doing nothing (the mean-shift-noise floor), measured by
        per-belt correlation to the reference."""
        from planetary_derotator import run_planetary_derotate
        from zonal_stacker_benchmark import _per_belt_residual_motion
        ref, frames, cm_list, sub, pa = _ref_and_sheared(n_frames=8, cm_drift=3.0)

        with tempfile.TemporaryDirectory(prefix="grs_pder_ab_") as d:
            d = Path(d)
            res = run_planetary_derotate(
                frames, d, n_grid=6, ap_half=16, cm_iii_per_frame=cm_list,
                sub_lat_deg=sub, north_pa_deg=pa, mode="measurement",
                reference="first",
            )
            stack = np.asarray(Image.open(res.output_path), dtype=np.float64) / 255.0
            # naive stack = simple mean (no derotation)
            naive = np.mean(np.stack(frames), axis=0)
            belt_derot = _per_belt_residual_motion(stack, ref)
            belt_naive = _per_belt_residual_motion(naive, ref)
            mean_derot = float(np.mean([v["peak"] for v in belt_derot.values()]))
            mean_naive = float(np.mean([v["peak"] for v in belt_naive.values()]))
            print(f"\n[derot measurement vs naive-mean] per-belt peak: "
                  f"naive={mean_naive:.4f}  derot={mean_derot:.4f}  "
                  f"delta={mean_derot - mean_naive:+.4f}")
            self.assertGreaterEqual(mean_derot, mean_naive)


if __name__ == "__main__":
    unittest.main()

"""
Regression tests for real/synthetic + deterioration-gate bugs found in the
long-form audit. These pin three failures that the synthetic campaigns could
not see on their own:

  1. verify_grs_detection had a corrupt bare ``h_grs(...)`` call where a
     lambda was intended. The whole scale-drift gate (the "is this a real
     feature or belt mottling?" check run on every non-lean measurement)
     raised NameError and was swallowed, so it was a silent no-op on every
     published measurement.

  2. frame_quality._on_disk_mask averaged over (H, W) for an HWC RGB frame,
     returning a (3,) "mask". Every RGB video frame therefore scored a
     sharpness of 0.0, which disabled lucky-imaging frame rejection on all
     colour SER/AVI captures (the observatory video-to-answer path).

  3. grs_complete_system.disk_mask_for_quality / rough_disk_mask did not
     collapse RGB to mono. rough_disk_mask returned an all-False 3-D mask on
     RGB disks, and disk_mask_for_quality's small-mask fallback crashed with
     ``ValueError: too many values to unpack (expected 2)`` on RGB frames.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

APP = Path(__file__).resolve().parents[1] / "app"
TOOLS = Path(__file__).resolve().parents[1] / "tools"
for p in (str(APP), str(TOOLS)):
    if p not in sys.path:
        sys.path.insert(0, p)


class TestVerifyGrsDetectionRuns(unittest.TestCase):
    def test_scale_drift_gate_actually_runs(self):
        from synthetic_hq import SynthSpec, generate
        from precision_engine import (
            fit_limb_nav,
            verify_grs_detection,
        )
        from PIL import Image

        with tempfile.TemporaryDirectory(prefix="grs_verify_") as d:
            png, _fit, truth = generate(
                SynthSpec(
                    region="global",
                    resolution_preset="540p",
                    random_time=True,
                    seed=20260822,
                    mode="metrology",
                    write_grs_crop=False,
                    sub_lat_deg=1.0,
                    north_pa_deg=10.0,
                ),
                Path(d),
            )
            img = np.asarray(Image.open(png), dtype=np.float64) / 255.0

        nav = fit_limb_nav(
            img,
            cm_iii_deg=truth["cm_iii_deg"],
            distance_au=truth["distance_au"],
            north_pa_deg=truth["north_pa_deg"],
        )
        nav.cm_iii_deg = truth["cm_iii_deg"]
        nav.distance_au = truth["distance_au"]
        nav.sub_lat_deg = float(truth.get("sub_obs_lat_deg") or 0.0)
        nav.north_pa_deg = float(truth.get("north_pa_deg") or 0.0)

        out = verify_grs_detection(img, nav, float(truth["grs_lon_iii_deg"]))

        # Before the fix this reported n_scales=0 with reason
        # "scale check failed (name 'h_grs' is not defined)".
        self.assertGreaterEqual(int(out.get("n_scales", 0)), 1, out)
        self.assertTrue(out.get("detected"), out)
        self.assertNotIn("h_grs", str(out.get("reason", "")))
        self.assertTrue(np.isfinite(out.get("drift_deg", float("nan"))), out)


class TestFrameQualityRgb(unittest.TestCase):
    def test_rgb_frames_get_nonzero_sharpness(self):
        from frame_quality import assess_frames, select_best_frames

        rng = np.random.default_rng(42)
        # HWC RGB frames: one sharp (high-frequency), one blurred.
        sharp = rng.random((64, 80, 3))
        blur = np.repeat(np.repeat(rng.random((16, 20, 3)), 4, axis=0), 4, axis=1)
        frames = [blur, sharp]

        q = assess_frames(frames)
        self.assertTrue(all(s.sharpness > 0.0 for s in q),
                        [s.sharpness for s in q])
        # The sharp frame must outrank the blurred one.
        self.assertGreater(q[1].sharpness, q[0].sharpness)

        # Selection must actually pick the sharpest frame (was: first N frames).
        kept, dropped, _ = select_best_frames(frames, keep_frac=0.5, min_keep=1)
        self.assertEqual(kept, [1])
        self.assertEqual(dropped, [0])

    def test_chw_rgb_frames_also_scored(self):
        from frame_quality import assess_frames

        rng = np.random.default_rng(7)
        frames = [rng.random((3, 64, 80)) for _ in range(3)]
        q = assess_frames(frames)
        self.assertTrue(all(s.sharpness > 0.0 for s in q),
                        [s.sharpness for s in q])


class TestDiskMaskQualityRgb(unittest.TestCase):
    def test_bright_rgb_disk_returns_2d_mask(self):
        import grs_complete_system as gcs

        img = np.zeros((80, 80, 3), dtype=np.float64)
        img[15:65, 15:65] = 0.8
        m = gcs.rough_disk_mask(img)
        self.assertEqual(m.ndim, 2)
        self.assertGreater(int(m.sum()), 0)

    def test_small_mask_fallback_does_not_crash_on_rgb(self):
        import grs_complete_system as gcs

        # Nearly-black RGB frame: rough mask is <50 px so the centre fallback
        # must run without ValueError on an HxWx3 array.
        img = np.zeros((60, 60, 3), dtype=np.float64)
        img[30, 30] = 1.0
        m = gcs.disk_mask_for_quality(img)
        self.assertEqual(m.ndim, 2)
        self.assertEqual(m.shape, (60, 60))
        self.assertGreater(int(m.sum()), 0)


class TestInjectionGeometry(unittest.TestCase):
    """Blind-injection ovals must be placed where the engine measures the same
    lon/lat, or the recovery error gets subtracted as fake pipeline bias."""

    def _blank_disk_nav(self, sub_lat=2.0, north_pa=15.0):
        from precision_engine import NavState
        H, W = 540, 960
        img = np.full((H, W), 0.5)
        yy, xx = np.mgrid[0:H, 0:W]
        nav = NavState(xc=W / 2, yc=H / 2, a_eq_px=220.0,
                       cm_iii_deg=100.0, distance_au=5.0,
                       sub_lat_deg=sub_lat, north_pa_deg=north_pa)
        disk = (((xx - nav.xc) / nav.a_eq_px) ** 2
                + ((yy - nav.yc) / nav.b_pol_px) ** 2) <= 1.0
        img[disk] = 0.7
        return img, nav

    def test_research_grade_injection_lands_on_target(self):
        from precision_engine import px_to_lonlat, wrap_diff
        from research_grade import inject_dark_oval

        img, nav = self._blank_disk_nav()
        for lon, lat in [(100.0, -20.0), (120.0, -22.0), (80.0, -18.0)]:
            inj = inject_dark_oval(img, nav, lon, lat,
                                   length_deg=10, width_deg=7, depth=0.5)
            py, px = np.unravel_index(np.argmax(img - inj), img.shape)
            rlon, rlat = px_to_lonlat(float(py), float(px), nav)
            self.assertLess(abs(wrap_diff(rlon, lon)), 0.4,
                            f"lon {lon} -> {rlon}")
            self.assertLess(abs(rlat - lat), 0.3,
                            f"lat {lat} -> {rlat}")

    def test_vlbi_injection_lands_on_target(self):
        from precision_engine import px_to_lonlat, wrap_diff
        from vlbi_metrology import AdvancedNav, inject_dark_oval_image

        img, nav0 = self._blank_disk_nav()
        nav = AdvancedNav(xc=nav0.xc, yc=nav0.yc, a_eq_px=nav0.a_eq_px,
                          cm_iii_deg=nav0.cm_iii_deg, distance_au=nav0.distance_au,
                          sub_lat_deg=nav0.sub_lat_deg,
                          north_pa_deg=nav0.north_pa_deg)
        for lon, lat in [(100.0, -20.0), (120.0, -22.0)]:
            inj = inject_dark_oval_image(img, nav, lon, lat)
            py, px = np.unravel_index(np.argmax(img - inj), img.shape)
            rlon, rlat = px_to_lonlat(float(py), float(px), nav.to_nav_state())
            self.assertLess(abs(wrap_diff(rlon, lon)), 0.4,
                            f"lon {lon} -> {rlon}")
            self.assertLess(abs(rlat - lat), 0.3,
                            f"lat {lat} -> {rlat}")


class TestPerPixelLatitude(unittest.TestCase):
    """The per-pixel / per-AP latitude used to bin per-latitude warps must be
    the true oblate-spheroid planetocentric latitude, not a sphere+anisotropic-y
    approximation that was up to ~2.8 deg off in the GRS band even at D=P=0."""

    def test_lat_map_matches_px_to_lonlat(self):
        from precision_engine import NavState, px_to_lonlat
        from planetary_stacker import _per_pixel_lat

        nav = NavState(xc=480, yc=270, a_eq_px=220.0,
                       sub_lat_deg=2.0, north_pa_deg=15.0)
        lat_map, on = _per_pixel_lat(nav, 540, 960, 2.0, 15.0)
        ys, xs = np.where(on)
        maxd = 0.0
        for k in range(0, len(ys), 40):
            y, x = int(ys[k]), int(xs[k])
            _, lat_exact = px_to_lonlat(float(y), float(x), nav)
            maxd = max(maxd, abs(lat_map[y, x] - lat_exact))
        self.assertLess(maxd, 1e-3, f"lat map off by up to {maxd:.4f} deg")

    def test_ap_latitudes_match_px_to_lonlat(self):
        from precision_engine import NavState, px_to_lonlat
        from planetary_stacker import _ap_latitudes

        nav = NavState(xc=480, yc=270, a_eq_px=220.0,
                       sub_lat_deg=-1.5, north_pa_deg=10.0)
        aps = np.array([[480, 270], [520, 340], [430, 200], [600, 360]],
                       dtype=np.float64)
        lats = _ap_latitudes(aps, nav, -1.5, 10.0)
        for (x, y), la in zip(aps, lats):
            _, lat_exact = px_to_lonlat(float(y), float(x), nav)
            self.assertAlmostEqual(float(la), float(lat_exact), places=4)


class TestDerotatorMeasurementRegularised(unittest.TestCase):
    """On a planted per-latitude shear, measurement-mode derotation must beat a
    naive mean even at large per-frame drifts (regression for the unbinned
    tracker-noise accumulation that made pure measurement worse than nothing)."""

    def test_measurement_beats_naive_on_large_drift(self):
        import tempfile
        from pathlib import Path
        from synthetic_hq import SynthSpec, generate
        from planetary_derotator import run_planetary_derotate
        from zonal_stacker_benchmark import _apply_zonal_shift, _per_belt_residual_motion
        from PIL import Image

        with tempfile.TemporaryDirectory(prefix="grs_derot_reg_") as d:
            png, _fit, truth = generate(
                SynthSpec(region="global", resolution_preset="720p",
                          random_time=True, seed=2024, mode="metrology",
                          write_grs_crop=False), Path(d))
            arr = np.asarray(Image.open(png), dtype=np.float64) / 255.0
        ref = 0.299 * arr[..., 0] + 0.587 * arr[..., 1] + 0.114 * arr[..., 2]
        cm0 = float(truth["cm_iii_deg"])
        frames = [ref]
        cms = [cm0]
        for k in range(1, 8):
            cms.append(cm0 + k * 3.0)
            frames.append(_apply_zonal_shift(
                ref, cm0, cms[-1], distance_au=float(truth["distance_au"])))
        with tempfile.TemporaryDirectory(prefix="grs_derot_out_") as d2:
            res = run_planetary_derotate(
                frames, Path(d2), n_grid=6, ap_half=16,
                cm_iii_per_frame=cms, mode="measurement", reference="first")
            stack = np.asarray(Image.open(res.output_path),
                               dtype=np.float64) / 255.0
        naive = np.mean(np.stack(frames), axis=0)
        derot = float(np.mean([v["peak"] for v in
                               _per_belt_residual_motion(stack, ref).values()]))
        naive_c = float(np.mean([v["peak"] for v in
                                 _per_belt_residual_motion(naive, ref).values()]))
        self.assertGreaterEqual(derot, naive_c,
                                f"derot={derot:.4f} naive={naive_c:.4f}")


if __name__ == "__main__":
    unittest.main()

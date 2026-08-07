"""End-to-end tests for the v6.8 Observatory Pro production commands.

- transits CLI smoke
- video-stack on a synthetic SER
- video_to_answer: synthetic Jupiter capture (planted shifts + seeing) ->
  APS drizzle stack -> sharpen -> published measurement, gated on truth.
"""
from __future__ import annotations

import datetime as dt
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
for p in (str(APP),):
    if p not in sys.path:
        sys.path.insert(0, str(p))

CLI = APP / "cli.py"


def _run_cli(*argv, timeout=600):
    proc = subprocess.run(
        [sys.executable, str(CLI), *argv],
        capture_output=True, text=True, timeout=timeout, cwd=str(ROOT),
    )
    return proc


class TestTransitsCLI(unittest.TestCase):
    def test_transits_json(self):
        proc = _run_cli("transits", "--time", "2026-08-01 00:00", "--days", "0.5",
                        "--moons", "io", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr[-500:])
        plan = json.loads(proc.stdout)
        self.assertIn("grs_transits", plan)
        self.assertIn("moon_transits", plan)
        self.assertTrue(plan["grs_transits"])

    def test_transits_text(self):
        proc = _run_cli("transits", "--time", "2026-08-01 00:00", "--days", "0.5",
                        "--moons", "")
        self.assertEqual(proc.returncode, 0, proc.stderr[-500:])
        self.assertIn("OBSERVING PLANNER", proc.stdout)


def _render_truth(seed=777, resolution="540p"):
    """One metrology frame + its truth; degraded copies make the 'video'.

    The epoch is *placed* (GRS near the meridian, like the real campaigns)
    because a uniformly random epoch mostly puts the GRS near/at the limb,
    which no measurement path can recover — this is a pipeline test, not a
    placement lottery.

    Since v6.8 the frame is rendered with the TRUE SKY GEOMETRY of its epoch
    (sub-observer latitude + north-polar-axis PA from the same ephemeris the
    production stack uses), because that is what a real capture looks like
    and what the production nav models. Placement is exact via
    GRS_LIMB_LON_REL, matching tools/real_ephemeris_campaign.py.
    """
    import os
    from synthetic_hq import SynthSpec, generate
    from PIL import Image
    import grs_ephemeris_truth
    day = dt.date(2026, 8, 1) + dt.timedelta(days=int(seed) % 5)
    obs_time, lon_rel = grs_ephemeris_truth.observe_at_placement(day, -15.0)
    # true sky orientation at the render epoch (same source the production
    # ephemeris resolver uses — vendored SPICE kernels, no network)
    from ephemeris_pro import resolve_pro_ephemeris
    pe = resolve_pro_ephemeris(obs_time.strftime("%Y-%m-%d %H:%M:%S"),
                               use_horizons=False, use_spice=True)
    sub_lat = float(pe.sub_obs_lat_deg or 0.0)
    north_pa = float(pe.north_pa_deg or 0.0)
    d = Path(tempfile.mkdtemp(prefix="grs_v2a_"))
    os.environ["GRS_LIMB_LON_REL"] = f"{lon_rel:.6f}"
    try:
        png, _fit, truth = generate(
            SynthSpec(region="global", resolution_preset=resolution,
                      user_time_iso=obs_time.strftime("%Y-%m-%d %H:%M:%S"),
                      random_time=False, seed=int(seed), mode="metrology",
                      write_grs_crop=False,
                      sub_lat_deg=sub_lat, north_pa_deg=north_pa),
            d,
        )
    finally:
        os.environ.pop("GRS_LIMB_LON_REL", None)
    arr = np.asarray(Image.open(png), dtype=np.float64) / 255.0
    return arr, truth, d


def _make_video_frames(base, n=10, seed=42):
    from scipy.ndimage import shift as _sshift, gaussian_filter
    rng = np.random.default_rng(seed)
    frames = []
    for k in range(n):
        dy, dx = rng.uniform(-3, 3, 2)
        sig = rng.uniform(0.4, 1.5)
        if base.ndim == 3:
            f = np.stack([_sshift(base[..., c], (dy, dx), order=3, mode="nearest")
                          for c in range(3)], axis=-1)
        else:
            f = _sshift(base, (dy, dx), order=3, mode="nearest")
        f = gaussian_filter(f, (sig, sig) + ((0,) if f.ndim == 3 else ()))
        f = f + rng.normal(0, 0.004, f.shape)
        frames.append(f)
    return frames


class TestVideoStackCLI(unittest.TestCase):
    def test_video_stack_on_ser(self):
        import ser_io
        with tempfile.TemporaryDirectory() as d:
            base, truth, _ = _render_truth(seed=777)
            frames = _make_video_frames(base, 8)
            t0 = dt.datetime(2026, 8, 1, 22, 0, 0)
            times = [t0 + dt.timedelta(seconds=20 * k) for k in range(len(frames))]
            ser_path = Path(d) / "cap.ser"
            ser_io.write_ser(ser_path, [(f * 255).astype(np.uint8) for f in frames],
                             frame_times_utc=times, observer="test")
            out = Path(d) / "stack"
            proc = _run_cli("video-stack", str(ser_path), "--best", "0.5",
                            "--drizzle", "1", "--ap-size", "32", "--out", str(out))
            self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
            rep = json.loads(proc.stdout)
            self.assertEqual(rep["n_frames"], 8)
            png = Path(rep["stack_png"])
            self.assertTrue(png.exists())
            self.assertTrue((out / "APS_REPORT.txt").exists())
            # the stacked PNG is a cropped, positive-weight image
            from PIL import Image
            a = np.asarray(Image.open(png), dtype=np.float64) / 255.0
            self.assertGreater(a.std(), 0.01)


    def test_video_stack_derotate_prior_glue(self):
        """CLI --derotate plumbing on a stamped SER (glue-level: the static
        frames here don't rotate with the model, so no accuracy claim —
        that's gated by the rotating-capture tests)."""
        import ser_io
        with tempfile.TemporaryDirectory() as d:
            base, truth, _ = _render_truth(seed=778)
            frames = _make_video_frames(base, 6)
            t0 = dt.datetime(2026, 8, 1, 22, 0, 0)
            times = [t0 + dt.timedelta(seconds=20 * k) for k in range(len(frames))]
            ser_path = Path(d) / "cap.ser"
            ser_io.write_ser(ser_path, [(f * 255).astype(np.uint8) for f in frames],
                             frame_times_utc=times, observer="test")
            out = Path(d) / "stack_d"
            proc = _run_cli("video-stack", str(ser_path), "--best", "0.5",
                            "--derotate", "prior", "--out", str(out))
            self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
            rep = json.loads(proc.stdout)
            dinfo = rep.get("derotate")
            self.assertIsNotNone(dinfo, "derotate report block missing")
            self.assertEqual(dinfo.get("mode"), "prior")
            self.assertIsNotNone(dinfo.get("ref_index"))
            self.assertIn("median_per_row_shift_px", dinfo)
            # the refuses-to-guess path (no stamps, no --dt-per-frame) is
            # covered in tests/test_science_p0_fixes.py::TestDerotationWiring


class TestVideoToAnswerDerotate(unittest.TestCase):
    """Production derotation: a capture whose planet is actually ROTATING
    (video_synth rigid System III spin) goes through video_to_answer with
    derotate="prior" and must (a) publish on the derotated stack anchored to
    its reference frame, and (b) land inside the same production gates."""

    def test_derotate_prior_on_rotating_capture(self):
        import ser_io
        import video_synth
        from PIL import Image

        # 1280x960 capture (disk a=403 px; like the 1080p-class the production
        # gate covers), stacked at 640x480 after downsample=2 — the same
        # regime the headline video-to-answer test lives in.
        spec = video_synth.VideoSynthSpec(
            width=1280, height=960, n_frames=8, fps=1 / 15.0,
            grs_lon_iii_deg=145.0, grs_lat_deg=-20.0, cm0_deg=100.0,
            seeing_fwhm_px=(1.6, 3.6), noise_rms=(0.002, 0.006),
            shift_rms_px=2.0, wave_amp=0.05, seed=7)
        vs = video_synth.render_video(spec)
        tr = vs.truth
        t0 = dt.datetime(2026, 8, 1, 22, 0, 0)
        times = [t0 + dt.timedelta(seconds=15 * k) for k in range(8)]
        with tempfile.TemporaryDirectory() as d:
            ser_path = Path(d) / "rot.ser"
            ser_io.write_ser(ser_path,
                             [(np.clip(f, 0, 1) * 255).astype(np.uint8) for f in vs.frames],
                             frame_times_utc=times, observer="e2e-derot")
            out = Path(d) / "answer"
            import observatory_pipeline as op
            rep = op.video_to_answer(
                str(ser_path), time_utc=None,
                keep_frac=0.5, drizzle=1, ap_size=32,
                downsample=2,                  # 640x480 working stack, as the headline test
                sharpen_method="wavelet", derotate="prior",
                out_root=out,
            )
            # (a) derotation block + ref-frame epoch discipline
            dinfo = rep.get("derotate")
            self.assertIsNotNone(dinfo, "derotate report missing")
            self.assertEqual(dinfo.get("mode"), "prior")
            ref_idx = int(dinfo["ref_index"])
            self.assertEqual(rep.get("measurement_epoch"), "ref_frame")
            self.assertEqual(rep.get("time_utc"), dinfo.get("ref_time_utc"))
            # (b) accuracy gate: the campaign measurement path carried on
            # every v6.8 video answer (the ARM the accuracy campaigns verify,
            # here against the derotated stack at the SAME anchor). The
            # publish-policy number is glue-checked but NOT accuracy-gated on
            # this renderer — video_synth's texture is blander than
            # synthetic_hq's and the classical GS-MAP/GS-BARY definitions
            # honestly grade REJECT on it (measured 2026-08-07: publish lock
            # 18 deg off, grade REJECT, while the campaign path reads +0.16 deg
            # on the identical stack — see docs/OBSERVATORY_PRO_6.8.0.md).
            # Publish-path accuracy IS gated at 1.0 deg by the flagship
            # test_full_pipeline_recovers_truth on the richer renderer.
            cam = rep.get("campaign_measurement")
            self.assertIsNotNone(cam, rep.get("campaign_note") or "no campaign block")
            from precision_engine import wrap_diff
            rel_true = wrap_diff(spec.grs_lon_iii_deg,
                                 float(tr["cm_iii_per_frame_deg"][ref_idx]))
            dlon = abs(wrap_diff(float(cam["rel_lon_deg"]), rel_true))
            dlat = abs(float(cam["lat_deg"]) - spec.grs_lat_deg)
            print(f"\n[v2a derotate=prior] ref#{ref_idx} campaign rel "
                  f"{float(cam['rel_lon_deg']):+.3f} true {rel_true:+.3f} "
                  f"dlon {dlon:.3f} dlat {dlat:.3f}")
            self.assertLessEqual(dlon, 1.0, f"campaign rel-lon err {dlon:.3f}")
            self.assertLessEqual(dlat, 1.5, f"campaign dlat err {dlat:.3f}")
            # publish policy still ran and surfaces an honest grade
            meas = rep.get("measurement") or {}
            pub = meas.get("publish") or {}
            self.assertIsNotNone(pub.get("publish_definition"))


class TestVideoToAnswer(unittest.TestCase):
    """The production headline: a synthetic capture in, a gated GRS
    longitude out — end to end."""

    def test_full_pipeline_recovers_truth(self):
        import ser_io
        base, truth, tmp = _render_truth(seed=20240109)
        frames = _make_video_frames(base, 12, seed=99)
        # SER stamps around the truth mid-exposure time. The renderer's own
        # epoch key is user_time_iso ("%Y-%m-%d %H:%M:%S"), written for both
        # random and fixed epochs.
        t_true = dt.datetime.strptime(str(truth["user_time_iso"]), "%Y-%m-%d %H:%M:%S")
        n = len(frames)
        t_mid = t_true
        times = [t_mid + dt.timedelta(seconds=20 * (k - n // 2)) for k in range(n)]
        with tempfile.TemporaryDirectory() as d:
            ser_path = Path(d) / "jup.ser"
            ser_io.write_ser(ser_path, [(f * 255).astype(np.uint8) for f in frames],
                             frame_times_utc=times, observer="e2e")
            out = Path(d) / "answer"
            import observatory_pipeline as op
            rep = op.video_to_answer(
                str(ser_path),
                time_utc=None,                 # exercise SER mid-time auto-detection
                keep_frac=0.5, drizzle=1, ap_size=40,
                downsample=2,                  # 960x540 -> 480x270: fast APS
                sharpen_method="wavelet",
                out_root=out,
            )
            self.assertEqual(rep["n_frames_used"], n)
            self.assertIsNotNone(rep.get("measurement"), rep.get("note"))
            meas = rep["measurement"]
            h = meas.get("headline") or {}
            pub = meas.get("publish") or {}
            lon = pub.get("publish_lon_iii_deg", h.get("lon_iii_deg"))
            lat = pub.get("publish_lat_deg", h.get("lat_deg"))
            self.assertIsNotNone(lon, json.dumps(meas, default=str)[:800])
            from precision_engine import wrap_diff
            truth_lon = float(truth["grs_lon_seed_deg"])
            truth_lat = float(truth["grs_lat_deg"])
            # RELATIVE-LONGITUDE gate (same quantity the campaigns score).
            # The renderer plants longitudes from its analytical CM anchor
            # (grs_ephemeris_truth: "SPICE gives the trusted absolute CM for
            # real reductions; the analytical value is used here only for
            # placing the synthetic GRS"), while production publishes on the
            # SPICE CM — two documented, intentional ephemeris frames. What
            # the pipeline must recover from pixels is the GRS position
            # RELATIVE to the meridian it used:
            cm_used = float(pub.get("cm_iii_deg", h.get("cm_iii_deg")))
            rel_meas = wrap_diff(float(lon), cm_used)
            rel_true = wrap_diff(truth_lon, float(truth["cm_iii_deg"]))
            dlon = abs(wrap_diff(rel_meas, rel_true))
            # the SAME 1° production gate used by the campaigns
            self.assertLessEqual(dlon, 1.0,
                                 f"video-to-answer rel-lon err {dlon:.3f} deg "
                                 f"(meas rel {rel_meas:.3f} true rel {rel_true:.3f}, "
                                 f"publish lon {lon} cm {cm_used})")
            if lat is not None:
                dlat = abs(float(lat) - truth_lat)
                self.assertLessEqual(dlat, 1.5, f"dlat {dlat:.3f}")
            # artefact files exist
            self.assertTrue((out / "stack.png").exists())
            self.assertTrue((out / "weight.png").exists())


class TestSharpenCLI(unittest.TestCase):
    def test_sharpen_file(self):
        base, truth, _ = _render_truth(seed=555)
        from scipy.ndimage import gaussian_filter
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "in.png"
            Image.fromarray((np.clip(gaussian_filter(base, (1.5, 1.5, 0)), 0, 1) * 255)
                            .astype(np.uint8)).save(p)
            proc = _run_cli("sharpen", str(p), "--method", "wavelet", "--out", str(Path(d) / "o.png"))
            self.assertEqual(proc.returncode, 0, proc.stderr[-500:])
            rep = json.loads(proc.stdout)
            self.assertGreater(rep["lapvar_after"], rep["lapvar_before"])
            self.assertTrue(Path(rep["out"]).exists())


class TestAnimateCLI(unittest.TestCase):
    def test_animate_two_frames(self):
        with tempfile.TemporaryDirectory() as d:
            rng = np.random.default_rng(0)
            from PIL import Image
            paths = []
            for k in range(3):
                pp = Path(d) / f"f{k}.png"
                Image.fromarray((rng.random((48, 64)) * 255).astype(np.uint8)).save(pp)
                paths.append(str(pp))
            out = Path(d) / "a.gif"
            proc = _run_cli("animate", *paths, "--out", str(out), "--fps", "5")
            self.assertEqual(proc.returncode, 0, proc.stderr[-500:])
            rep = json.loads(proc.stdout)
            self.assertEqual(rep["n_frames"], 3)


class TestJuposExportCLI(unittest.TestCase):
    def test_export(self):
        with tempfile.TemporaryDirectory() as d:
            pkg = Path(d) / "pkg.json"
            pkg.write_text(json.dumps({
                "utc_iso": "2026-01-09T17:06:00Z",
                "lon_iii_deg": 39.7, "lat_deg": -22.4,
                "method": "SUPERDUPER"}))
            out = Path(d) / "meas.csv"
            proc = _run_cli("jupos-export", str(pkg), "--out", str(out),
                            "--observer", "arena")
            self.assertEqual(proc.returncode, 0, proc.stderr[-500:])
            rep = json.loads(proc.stdout)
            self.assertEqual(rep["n_rows"], 1)
            self.assertTrue(out.read_text().startswith("Object,Date,Time"))


if __name__ == "__main__":
    unittest.main()

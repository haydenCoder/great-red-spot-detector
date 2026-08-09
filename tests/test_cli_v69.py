"""End-to-end tests for the v6.9 "Analysis Pro" CLI commands:
rgb-combine, filter-wheel (via module e2e), wind-analysis, drift,
session-plan. Every command runs as a real subprocess against synthetic
inputs with planted truth.
"""
from __future__ import annotations

import csv
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
    return subprocess.run([sys.executable, str(CLI), *argv],
                          capture_output=True, text=True, timeout=timeout,
                          cwd=str(ROOT))


class TestRGBCombineCLI(unittest.TestCase):
    def _mono_stacks(self, d):
        """Three mono stacks of the same rotating planet, ~2.4 deg/hop."""
        from video_synth import VideoSynthSpec, render_video
        from PIL import Image
        spec = VideoSynthSpec(width=256, height=192, n_frames=3,
                              fps=1 / 240.0, cm0_deg=104.0,
                              sub_lat_deg=-1.5, north_pa_deg=10.0,
                              disk_frac=0.42, seeing_fwhm_px=(0.8, 0.8),
                              noise_rms=(0.001, 0.001), shift_rms_px=0.0,
                              gain_jitter=0.0, seed=19, rgb=False)
        v = render_video(spec)
        paths = []
        for i, name in enumerate("rgb"):
            im = (np.clip(v.frames[i], 0, 1) * 255).astype(np.uint8)
            pth = Path(d) / f"stack_{name}.png"
            Image.fromarray(im, "L").save(str(pth))
            paths.append(pth)
        return paths, v.truth["times_s"]

    def test_rgb_combine_command(self):
        with tempfile.TemporaryDirectory() as d:
            (pr, pg, pb), ts = self._mono_stacks(d)
            out = Path(d) / "out"
            proc = _run_cli(
                "rgb-combine", "--r", str(pr), "--g", str(pg), "--b", str(pb),
                "--dt-r", str(-240.0), "--dt-b", str(240.0),
                "--sub-lat", "-1.5", "--north-pa", "10.0",
                "--out", str(out))
            self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
            self.assertIn("RGB COMBINE REPORT", proc.stdout)
            self.assertTrue((out / "rgb.png").exists())
            rep = json.loads((out / "rgb_report.json").read_text())
            self.assertLess(rep["fringe_after"], 0.6 * rep["fringe_before"])
            self.assertAlmostEqual(rep["dts_s"]["R"], -240.0, places=1)


class TestWindAnalysisCLI(unittest.TestCase):
    def test_wind_analysis_command(self):
        import math
        with tempfile.TemporaryDirectory() as d:
            # fabricated-but-physical stack report with planted offset
            n = 13
            centres = [(i + 0.5) * 90.0 / n for i in range(n)]
            wr = {
                "bins_abs_lat_deg": centres,
                "measured_rate_deg_per_s": [],
                "measured_rate_std_deg_per_s": [],
                "model_rate_deg_per_s": [],
                "wind_residual_mps_vs_model": [],
                "wind_residual_std_mps": [],
                "n_evidence_tracks": [20] * n,
                "n_evidence_frames": [4] * n,
            }
            from planet_models import JUPITER
            for c in centres:
                om = JUPITER.cloud_tracking_rate_deg_per_s(c)
                k = (math.pi / 180.0) * JUPITER.surface_parallel_radius_m(c)
                wr["model_rate_deg_per_s"].append(om)
                wr["wind_residual_mps_vs_model"].append(22.0)
                wr["wind_residual_std_mps"].append(5.0)
                wr["measured_rate_deg_per_s"].append(om + 22.0 / k)
                wr["measured_rate_std_deg_per_s"].append(1e-7)
            rp = Path(d) / "report.json"
            rp.write_text(json.dumps({"wind_report": wr}))
            png = str(Path(d) / "wind.png")
            csvp = str(Path(d) / "wind.csv")
            proc = _run_cli("wind-analysis", str(rp), "--png", png,
                            "--csv", csvp)
            self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
            self.assertIn("ZONAL WIND ANALYSIS", proc.stdout)
            self.assertIn("+22", proc.stdout)      # recovered offset printed
            self.assertTrue(Path(png).exists() and Path(png).stat().st_size > 4000)
            rows = list(csv.reader(open(csvp)))
            self.assertIn("abs_lat_deg", rows[0] if not rows[0][0].startswith("#") else rows[-(n + 1)][0])

    def test_wind_analysis_rejects_missing_block(self):
        with tempfile.TemporaryDirectory() as d:
            rp = Path(d) / "r.json"
            rp.write_text(json.dumps({"no_wind": True}))
            proc = _run_cli("wind-analysis", str(rp))
            self.assertEqual(proc.returncode, 1)
            self.assertIn("wind_report", proc.stderr)


class TestDriftCLI(unittest.TestCase):
    def test_drift_command(self):
        from jupos_io import JUPOS_FIELDS
        with tempfile.TemporaryDirectory() as d:
            import datetime as dt
            rng = np.random.default_rng(3)
            t0 = dt.datetime(2026, 3, 1, 22, 0, 0)
            p = Path(d) / "series.csv"
            with open(p, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=JUPOS_FIELDS)
                w.writeheader()
                rate = -0.014
                for i, td in enumerate(np.linspace(0, 120, 16)):
                    lon = (220.0 + rate * td + float(rng.normal(0, 0.6))) % 360
                    tt = t0 + dt.timedelta(days=float(td))
                    w.writerow({"Object": "GRS",
                                "Date": tt.strftime("%Y-%m-%d"),
                                "Time": tt.strftime("%H:%M"),
                                "L_II": f"{lon:.3f}", "Lat": "-20.1",
                                "Observer": "synth"})
            png = str(Path(d) / "drift.png")
            proc = _run_cli("drift", str(p), "--png", png,
                            "--predict-days", "30")
            self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
            self.assertIn("GRS DRIFT FIT", proc.stdout)
            self.assertIn("deg/30d", proc.stdout)
            self.assertIn("m/s", proc.stdout)
            self.assertIn("prediction +30d", proc.stdout)
            self.assertTrue(Path(png).exists())
            # planted -0.014 deg/day = -0.42 deg/30d: printed within 0.15
            import re
            mt = re.search(r"([+-]\d+\.\d+) deg/30d", proc.stdout)
            self.assertIsNotNone(mt)
            self.assertAlmostEqual(float(mt.group(1)), -0.42, delta=0.15)


class TestSessionPlanCLI(unittest.TestCase):
    def test_session_plan_command(self):
        with tempfile.TemporaryDirectory() as d:
            png = str(Path(d) / "plan.png")
            proc = _run_cli("session-plan", "--time", "2026-08-01 18:00",
                            "--hours", "8", "--a-eq-px", "120", "--png", png)
            self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
            self.assertIn("SESSION PLAN", proc.stdout)
            self.assertIn("smear budget", proc.stdout)
            self.assertIn("filter session", proc.stdout)
            self.assertTrue(Path(png).exists())

    def test_session_plan_ephemeris_only(self):
        proc = _run_cli("session-plan", "--time", "2026-08-01 18:00",
                        "--hours", "4")
        self.assertEqual(proc.returncode, 0, proc.stderr[-800:])
        self.assertNotIn("smear budget", proc.stdout)


if __name__ == "__main__":
    unittest.main()

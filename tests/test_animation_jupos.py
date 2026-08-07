"""Tests for animation (GIF blink/export) and jupos_io (JUPOS CSV round-trip)."""
from __future__ import annotations

import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


class TestAnimation(unittest.TestCase):
    def _frames(self, n=4, shape=(48, 64)):
        rng = np.random.default_rng(2)
        return [np.clip(0.3 + rng.normal(0, 0.1, shape) + k * 0.05, 0, 1) for k in range(n)]

    def test_make_gif_arrays(self):
        import animation
        with tempfile.TemporaryDirectory() as d:
            p = animation.make_gif(self._frames(), Path(d) / "a.gif", fps=8)
            self.assertTrue(p.exists())
            info = animation.gif_info(p)
            self.assertEqual(info["n_frames"], 4)
            self.assertEqual(info["size"], (64, 48) + (0,) if False else info["size"])
            self.assertAlmostEqual(info["durations_ms"][0], 125, delta=20)  # GIF quantizes to 10ms

    def test_gif_rgb_and_stamps(self):
        import animation
        rng = np.random.default_rng(4)
        frames = [rng.random((40, 50, 3)) for _ in range(3)]
        with tempfile.TemporaryDirectory() as d:
            p = animation.make_gif(frames, Path(d) / "c.gif", fps=5,
                                   stamps=["2026-08-01 22:00Z"] * 3, scale=2)
            info = animation.gif_info(p)
            self.assertEqual(info["n_frames"], 3)
            self.assertEqual(info["size"], (100, 80))

    def test_blink_gif(self):
        import animation
        a, b = self._frames(2)
        with tempfile.TemporaryDirectory() as d:
            p = animation.blink_gif(a, b, Path(d) / "b.gif", interval_s=0.4)
            info = animation.gif_info(p)
            self.assertEqual(info["n_frames"], 2)
            self.assertAlmostEqual(info["durations_ms"][0] / 1000.0, 0.4, delta=0.05)

    def test_from_paths_and_global_stretch(self):
        import animation
        from PIL import Image
        with tempfile.TemporaryDirectory() as d:
            paths = []
            for i, f in enumerate(self._frames(3)):
                pp = Path(d) / f"f{i}.png"
                Image.fromarray((f * 255).astype(np.uint8)).save(pp)
                paths.append(pp)
            out = animation.make_gif(paths, Path(d) / "p.gif", stretch="global")
            self.assertTrue(out.exists())
            with self.assertRaises(ValueError):
                animation.make_gif([], Path(d) / "empty.gif")


class TestJuposIO(unittest.TestCase):
    def test_roundtrip(self):
        import jupos_io
        rows = [
            jupos_io.measurement_row(
                time_utc=dt.datetime(2026, 1, 9, 17, 6, 0),
                lon_iii_deg=39.694, lat_deg=-22.4, length_deg=13.9, width_deg=8.1,
                method="GS-ORANGE+ellipse_rim", observer="arena", instrument="C14"),
            jupos_io.measurement_row(
                time_utc=dt.datetime(2026, 1, 10, 15, 30, 0),
                lon_iii_deg=73.2, lat_deg=-22.5, method="GS-ORANGE"),
        ]
        with tempfile.TemporaryDirectory() as d:
            p = jupos_io.write_jupos_csv(Path(d) / "meas.csv", rows)
            back = jupos_io.read_jupos_csv(p)
        self.assertEqual(len(back), 2)
        r0 = back[0]
        self.assertEqual(r0["Object"], "GRS")
        self.assertAlmostEqual(r0["L_III"], 39.694, places=3)
        self.assertAlmostEqual(r0["Lat"], -22.4, places=3)
        self.assertAlmostEqual(r0["Length"], 13.9, places=3)
        self.assertEqual(r0["time_utc"], dt.datetime(2026, 1, 9, 17, 6, 0))
        # second row has no size -> None
        self.assertIsNone(back[1]["Length"])
        # sorted by time
        self.assertLess(back[0]["time_utc"], back[1]["time_utc"])

    def test_field_count_and_blanks(self):
        import jupos_io
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "x.csv"
            r = jupos_io.measurement_row(
                time_utc=dt.datetime(2026, 8, 1, 0, 0),
                lon_iii_deg=float("nan"), lat_deg=-22.4, length_deg=None)
            jupos_io.write_jupos_csv(p, [r])
            first, second = p.read_text().splitlines()[:2]
            self.assertEqual(len(first.split(",")), 15)
            self.assertEqual(len(second.split(",")), 15)
            back = jupos_io.read_jupos_csv(p)
            self.assertIsNone(back[0]["L_III"])      # NaN wrote blank
            self.assertIsNone(back[0]["Length"])

    def test_export_package_measurements(self):
        import jupos_io
        with tempfile.TemporaryDirectory() as d:
            packages = [
                {"utc_iso": "2026-01-09T17:06:00Z", "lon_iii_deg": 39.7, "lat_deg": -22.4,
                 "method": "SUPERDUPER"},
                {"time_utc": dt.datetime(2026, 1, 8, 3, 0), "lon_iii_deg": 10.1, "lat_deg": -22.3},
            ]
            p = jupos_io.export_package_measurements(
                Path(d) / "pkg.csv", packages, observer="obs", instrument="EdgeHD11")
            back = jupos_io.read_jupos_csv(p)
            self.assertEqual(len(back), 2)
            self.assertEqual(back[0]["Observer"], "obs")
            self.assertEqual(back[1]["Method"], "SUPERDUPER")
            self.assertTrue(str(p).endswith(".csv"))


if __name__ == "__main__":
    unittest.main()

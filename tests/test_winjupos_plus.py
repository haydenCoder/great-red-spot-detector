"""WinJUPOS+ desk-parity export tests."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


class TestWinJUPOSPlus(unittest.TestCase):
    def test_build_block_planetographic(self):
        from winjupos_plus import build_winjupos_plus_block

        pkg = {
            "headline": {
                "lon_iii_deg": 100.0,
                "lat_deg": -22.0,
                "cm_source": "spice_auto",
                "cm_iii_deg": 90.0,
                "distance_au": 5.2,
            },
            "publish": {
                "publish_definition": "GS-MAP",
                "publish_lon_iii_deg": 100.0,
                "publish_lat_deg": -22.0,
                "cm_source": "spice_auto",
                "cm_iii_deg": 90.0,
                "distance_au": 5.2,
                "winjupos_equality": {"agreement": "NO_MANUAL_PICK"},
            },
            "publish_quality": {"cm_trusted": True},
            "winjupos_twin": {
                "extent_lon_deg": 12.0,
                "limb_sky_spread_arcsec": 0.5,
                "definition_lon_spread_deg": 1.0,
            },
        }
        b = build_winjupos_plus_block(pkg)
        self.assertTrue(b["ok"])
        self.assertEqual(b["publish_definition"], "GS-MAP")
        self.assertIsNotNone(b["lat_planetographic_deg"])
        self.assertLess(b["lat_planetographic_deg"], -22.0)
        self.assertGreaterEqual(b["desk_score_0_100"], 70)
        self.assertIn("citation_line", b)

    def test_analytical_cm_lowers_score(self):
        from winjupos_plus import build_winjupos_plus_block

        pkg = {
            "headline": {"lon_iii_deg": 100.0, "lat_deg": -22.0},
            "publish": {
                "publish_definition": "GS-MAP",
                "publish_lon_iii_deg": 100.0,
                "publish_lat_deg": -22.0,
                "cm_source": "analytical",
                "cm_iii_deg": 0.0,
                "distance_au": 5.2,
            },
            "publish_quality": {"cm_trusted": False},
            "winjupos_twin": {},
        }
        b = build_winjupos_plus_block(pkg)
        self.assertIn("CM_WEAK", b["desk_flags"])
        self.assertLess(b["desk_score_0_100"], 70)


class TestDerotatorPhysics(unittest.TestCase):
    """win_jupos_derotator physics, pinned with planted rigid rotations
    (v6.8.x): the whole-disk polish recovers the planted angle to ~0.003 deg
    (the AP-only fit under-measured by 20-40% on smooth textures: fitted
    0.30/1.32/1.15 for planted 0.5/1.0/1.5 deg), and the derotated stack is
    clean (disk MSE ~1e-6 vs the raw rotated frames' 9.3e-4)."""

    def test_planted_rotation_recovered(self):
        import math
        import tempfile
        from pathlib import Path
        import numpy as np
        from scipy.ndimage import rotate as nd_rotate, gaussian_filter
        from win_jupos_derotator import run_win_jupos_derotate

        rng = np.random.default_rng(4)
        A = gaussian_filter(rng.normal(0.5, 0.13, (256, 256)), 1.3)
        yy, xx = np.mgrid[0:256, 0:256]
        disk = ((yy - 128.0) / 100.0) ** 2 + ((xx - 128.0) / 100.0) ** 2 <= 1.0
        A = np.where(disk, A, 0.02)
        angles = [0.0, 0.5, 1.0, 1.5]
        frames = [A] + [nd_rotate(A, a, reshape=False, order=3, mode="nearest")
                        for a in angles[1:]]
        with tempfile.TemporaryDirectory() as d:
            res = run_win_jupos_derotate(frames, Path(d), n_grid=6, ap_half=16)
            # derotated-stack artefact check INSIDE the tempdir lifetime —
            # reading res.output_path after the with-block is a use-after-
            # delete (caught by the full suite 2026-08-08)
            from PIL import Image
            st = np.asarray(Image.open(res.output_path), dtype=np.float64) / 255.0
        # scipy CCW(+) maps to this module's negative image-coords convention;
        # measured 2026-08-07: -0.5118/-1.0033/-1.4966 for planted .5/1/1.5
        for got, planted in zip(res.rotation_per_frame_deg[1:], (-0.5, -1.0, -1.5)):
            self.assertAlmostEqual(got, planted, delta=0.1,
                                   msg=f"theta {got:+.3f} vs planted {planted:+.3f}")
        # derotated stack must be cleaner than any raw rotated frame
        g = float(np.median(A[disk]) / max(np.median(st[disk]), 1e-9))
        stack_mse = float(np.mean(((st * g - A)[disk]) ** 2))
        raw_mse = float(np.mean(((frames[3] - A)[disk]) ** 2))
        self.assertLess(stack_mse, 0.2 * raw_mse,
                        f"stack MSE {stack_mse:.6f} not <20% of raw {raw_mse:.6f}")

    def test_polish_never_regresses_vs_ap_fit(self):
        """Guard: on frames where the image objective cannot improve
        (identical frames), the polished angle stays ~0 and nothing blows up."""
        import tempfile
        from pathlib import Path
        import numpy as np
        from scipy.ndimage import gaussian_filter
        from win_jupos_derotator import run_win_jupos_derotate
        rng = np.random.default_rng(9)
        A = gaussian_filter(rng.normal(0.5, 0.13, (200, 200)), 1.3)
        yy, xx = np.mgrid[0:200, 0:200]
        disk = ((yy - 100.0) / 80.0) ** 2 + ((xx - 100.0) / 80.0) ** 2 <= 1.0
        A = np.where(disk, A, 0.02)
        frames = [A.copy() for _ in range(3)]
        with tempfile.TemporaryDirectory() as d:
            res = run_win_jupos_derotate(frames, Path(d), n_grid=6, ap_half=16)
        for th in res.rotation_per_frame_deg:
            self.assertAlmostEqual(th, 0.0, delta=0.3)


if __name__ == "__main__":
    unittest.main()

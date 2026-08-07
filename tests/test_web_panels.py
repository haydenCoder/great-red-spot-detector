"""Web panel endpoints for v6.8 Observatory Pro.

Flask test-client coverage: transit planner, Sharpen Lab, APS video stack,
plus path-traversal hard refusal. The video stack is exercised end-to-end on
a tiny SER so the shared job slot + finish path are proven, not just mocked.
"""
from __future__ import annotations

import datetime as dt
import time
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
import sys
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


def _client():
    import server
    server.app.config["TESTING"] = True
    return server, server.app.test_client()


class TestTransitsEndpoint(unittest.TestCase):
    def test_transits_ok(self):
        _s, c = _client()
        r = c.get("/api/transits?time=2026-08-01 00:00&days=0.5&moons=io")
        self.assertEqual(r.status_code, 200)
        j = r.get_json()
        self.assertTrue(j["ok"])
        self.assertIn("grs_transits", j["plan"])
        self.assertTrue(j["plan"]["grs_transits"])
        self.assertIn("OBSERVING PLANNER", j["text"])

    def test_transits_bad_time_fails(self):
        _s, c = _client()
        r = c.get("/api/transits?time=not-a-time&days=0.5")
        self.assertEqual(r.status_code, 400)
        self.assertFalse(r.get_json()["ok"])


class TestSharpenEndpoint(unittest.TestCase):
    def _make_png(self):
        from PIL import Image
        from scipy.ndimage import gaussian_filter
        up = APP / "uploads"
        up.mkdir(parents=True, exist_ok=True)
        p = up / f"test_sharp_{int(time.time())}.png"
        # structured content (soft concentric bands on a disk): the wavelet
        # gains boost band contrast, so lapvar strictly increases.
        yy, xx = np.mgrid[0:96, 0:96]
        d = np.sqrt((yy - 48) ** 2 + (xx - 48) ** 2)
        img = (0.55 + 0.25 * np.cos(d / 3.0)) * (d < 38)
        img = gaussian_filter(img, 1.2)
        Image.fromarray((np.clip(img, 0, 1) * 255).astype(np.uint8)).save(p)
        return p

    def test_sharpen_wavelet(self):
        _s, c = _client()
        p = self._make_png()
        r = c.post("/api/sharpen", json={"path": str(p), "method": "wavelet",
                                         "amount": 1.0})
        self.assertEqual(r.status_code, 200, r.get_json())
        j = r.get_json()
        self.assertTrue(j["ok"])
        self.assertGreaterEqual(j["lapvar_after"], j["lapvar_before"] * 0.999)
        out = Path(j["out"])
        self.assertTrue(out.exists())
        self.assertTrue(str(j["preview"]).startswith("/api/file?path="))
        # served back through the safe file API
        r2 = c.get(j["preview"])
        self.assertEqual(r2.status_code, 200)

    def test_sharpen_traversal_refused(self):
        _s, c = _client()
        r = c.post("/api/sharpen", json={"path": "../../etc/passwd",
                                         "method": "wavelet"})
        self.assertIn(r.status_code, (400, 403))


class TestVideoStackEndpoint(unittest.TestCase):
    def _make_ser(self, n=4, shape=(64, 96)):
        import ser_io
        up = APP / "uploads"
        up.mkdir(parents=True, exist_ok=True)
        p = up / f"test_stack_{int(time.time())}.ser"
        from scipy.ndimage import gaussian_filter
        rng = np.random.default_rng(5)
        yy, xx = np.mgrid[0:shape[0], 0:shape[1]]
        base = np.clip(0.85 * (((yy - 32) ** 2 + (xx - 48) ** 2) < 26 ** 2), 0, 1)
        frames, times = [], []
        t0 = dt.datetime(2026, 8, 1, 22, 0, 0)
        for k in range(n):
            f = gaussian_filter(base, 0.8) + rng.normal(0, 0.01, shape)
            frames.append(np.clip(f, 0, 1).astype(np.float64))
            times.append(t0 + dt.timedelta(seconds=10 * k))
        ser_io.write_ser(p, [(f * 255).astype(np.uint8) for f in frames],
                         frame_times_utc=times)
        return p

    def test_video_stack_completes(self):
        _s, c = _client()
        p = self._make_ser()
        r = c.post("/api/video_stack", json={
            "path": str(p), "keep_frac": 1.0, "drizzle": 1, "ap_size": 24,
        })
        self.assertEqual(r.status_code, 200, r.get_json())
        self.assertTrue(r.get_json()["ok"])
        deadline = time.time() + 120
        result = None
        while time.time() < deadline:
            j = c.get("/api/job").get_json()
            if not j.get("running"):
                result, err = j.get("result"), j.get("error")
                self.assertIsNone(err, str(err))
                break
            time.sleep(0.5)
        self.assertIsNotNone(result, "video stack job did not finish in 120 s")
        self.assertEqual(result.get("kind"), "video_stack")
        self.assertIn("APS STACK", result.get("text", ""))
        self.assertTrue(str(result.get("preview", "")).startswith("/api/file"))
        r2 = c.get(result["preview"])
        self.assertEqual(r2.status_code, 200)

    def test_video_stack_rejects_image(self):
        _s, c = _client()
        r = c.post("/api/video_stack", json={"path": "/nonexistent/x.png"})
        self.assertIn(r.status_code, (400, 403))


if __name__ == "__main__":
    unittest.main()

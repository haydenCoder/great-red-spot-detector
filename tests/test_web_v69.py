"""Web panel endpoints for v6.9 Analysis Pro.

Flask test-client coverage: /api/analysis_session (physics budgets +
ephemeris) and /api/analysis_drift (JUPOS CSV drift fit with artifacts),
plus refusal paths.
"""
from __future__ import annotations

import csv
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


class TestAnalysisSessionEndpoint(unittest.TestCase):
    def test_session_with_scale(self):
        _s, c = _client()
        r = c.get("/api/analysis_session?a_eq_px=120&budget_px=1&hours=8"
                  "&time=2026-08-01 18:00")
        self.assertEqual(r.status_code, 200)
        j = r.get_json()
        self.assertTrue(j["ok"])
        self.assertIn("SESSION PLAN", j["text"])
        self.assertIn("smear budget", j["text"])
        self.assertIn("filter session", j["text"])
        self.assertEqual(j["plan"]["a_eq_px"], 120.0)
        # smear rows must be the planet model's numbers, not approximations
        row0 = j["plan"]["smear_table"][0]
        from planet_models import JUPITER
        self.assertAlmostEqual(
            row0["px_per_deg"], JUPITER.px_per_deg_lon(0.0, 120.0), places=9)

    def test_session_ephemeris_only(self):
        _s, c = _client()
        r = c.get("/api/analysis_session?a_eq_px=0&hours=4"
                  "&time=2026-08-01 18:00")
        j = r.get_json()
        self.assertTrue(j["ok"])
        self.assertNotIn("smear budget", j["text"])
        self.assertIsNone(j["plan"]["smear_table"])

    def test_session_bad_time_fails(self):
        _s, c = _client()
        r = c.get("/api/analysis_session?time=bogus")
        self.assertEqual(r.status_code, 400)
        self.assertFalse(r.get_json()["ok"])


class TestAnalysisDriftEndpoint(unittest.TestCase):
    def _make_jupos_csv(self):
        from jupos_io import JUPOS_FIELDS
        up = APP / "uploads"
        up.mkdir(parents=True, exist_ok=True)
        p = up / f"test_drift_{int(time.time())}.csv"
        import datetime as dt
        rng = np.random.default_rng(6)
        t0 = dt.datetime(2026, 3, 1, 22, 0, 0)
        with open(p, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=JUPOS_FIELDS)
            w.writeheader()
            for td in np.linspace(0, 120, 16):
                lon = (220.0 - 0.014 * td + float(rng.normal(0, 0.6))) % 360
                tt = t0 + dt.timedelta(days=float(td))
                w.writerow({"Object": "GRS", "Date": tt.strftime("%Y-%m-%d"),
                            "Time": tt.strftime("%H:%M"),
                            "L_II": f"{lon:.3f}", "Lat": "-20.1",
                            "Observer": "synth"})
        return p

    def test_drift_fit_ok(self):
        _s, c = _client()
        p = self._make_jupos_csv()
        r = c.post("/api/analysis_drift", json={"path": str(p)})
        self.assertEqual(r.status_code, 200, r.get_data(as_text=True)[-400:])
        j = r.get_json()
        self.assertTrue(j["ok"])
        self.assertIn("GRS DRIFT FIT", j["text"])
        self.assertAlmostEqual(j["fit"]["rate_deg_per_30d"], -0.42, delta=0.15)
        self.assertIn("preview", j)

    def test_drift_missing_file(self):
        _s, c = _client()
        r = c.post("/api/analysis_drift", json={"path": "/tmp/nope.csv"})
        self.assertIn(r.status_code, (400, 403))
        self.assertFalse(r.get_json()["ok"])

    def test_drift_rejects_non_csv(self):
        _s, c = _client()
        up = APP / "uploads"
        up.mkdir(parents=True, exist_ok=True)
        p = up / f"test_drift_bad_{int(time.time())}.png"
        p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
        r = c.post("/api/analysis_drift", json={"path": str(p)})
        self.assertEqual(r.status_code, 400)
        self.assertFalse(r.get_json()["ok"])


if __name__ == "__main__":
    unittest.main()

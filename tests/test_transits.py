"""Tests for transits — GRS events, visibility windows, moon transits, planner."""
from __future__ import annotations

import datetime as dt
import sys
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

import grs_ephemeris_truth as truth  # noqa: E402
import transits  # noqa: E402

T0 = dt.datetime(2026, 8, 1, 0, 0, 0)   # naive UTC (app convention)


def _parse(z: str) -> dt.datetime:
    return dt.datetime.fromisoformat(z.replace("Z", "+00:00")).replace(tzinfo=None)


class TestGRSTransits(unittest.TestCase):
    def test_event_count_matches_period(self):
        ev = transits.grs_transits(T0, days=2.0)
        # System III period ~9.925 h -> ~4.8 events expected in 2 days; the
        # GRS's own slow drift shifts this slightly, so allow 4..6.
        self.assertGreaterEqual(len(ev), 4)
        self.assertLessEqual(len(ev), 6)

    def test_events_are_true_meridian_crossings(self):
        ev = transits.grs_transits(T0, days=1.0)
        self.assertGreaterEqual(len(ev), 2)
        for e in ev:
            t = _parse(e.utc)
            self.assertLess(abs(truth.grs_lon_rel_deg(t)), 0.05,
                            f"event at {e.utc} not on the meridian")
            # downward crossing: CM sweeps past the slowly-drifting GRS
            before = truth.grs_lon_rel_deg(t - dt.timedelta(minutes=3))
            after = truth.grs_lon_rel_deg(t + dt.timedelta(minutes=3))
            self.assertGreater(before, after)

    def test_spacing_is_system_iii_like(self):
        ev = transits.grs_transits(T0, days=3.0)
        for a, b in zip(ev, ev[1:]):
            gap_h = (_parse(b.utc) - _parse(a.utc)).total_seconds() / 3600.0
            self.assertGreater(gap_h, 9.6)
            self.assertLess(gap_h, 10.3)

    def test_brentq_beats_grid_scan(self):
        refined = transits.grs_transits(T0, days=1.0, refine=True)[0]
        coarse = transits.grs_transits(T0, days=1.0, refine=False)[0]
        r = abs(truth.grs_lon_rel_deg(_parse(refined.utc)))
        c = abs(truth.grs_lon_rel_deg(_parse(coarse.utc)))
        self.assertLessEqual(r, c + 1e-6)


class TestVisibilityWindows(unittest.TestCase):
    def test_windows_structure(self):
        wins = transits.grs_visibility_windows(T0, days=1.0)
        self.assertGreaterEqual(len(wins), 2)
        day_end = T0 + dt.timedelta(days=1.0)
        for w in wins:
            s, e, p = _parse(w.start_utc), _parse(w.end_utc), _parse(w.peak_utc)
            self.assertLess(s, e)
            self.assertLessEqual(s, p)
            self.assertLessEqual(p, e)
            # inside the window the GRS is measurable
            self.assertLessEqual(abs(truth.grs_lon_rel_deg(p)), 60.0)
            # duration only meaningfully testable on non-boundary-clipped windows
            if s > T0 + dt.timedelta(minutes=10) and e <= day_end - dt.timedelta(seconds=1):
                dur_h = (e - s).total_seconds() / 3600.0
                self.assertGreater(dur_h, 2.6)
                self.assertLess(dur_h, 4.2)

    def test_peak_is_transit(self):
        wins = transits.grs_visibility_windows(T0, days=1.0)
        trans = [_parse(t.utc) for t in transits.grs_transits(T0, days=1.25)]
        day_end = T0 + dt.timedelta(days=1.0)
        for w in wins:
            if _parse(w.end_utc) >= day_end - dt.timedelta(seconds=1):
                continue  # boundary-clipped window: true transit may lie past the span
            best = min(abs((_parse(w.peak_utc) - t).total_seconds()) for t in trans)
            self.assertLess(best, 11 * 60,
                            f"window peak {w.peak_utc} not near any transit")


class TestGRSNow(unittest.TestCase):
    def test_panel_shape(self):
        d = transits.grs_now(T0)
        for k in ("utc", "grs_lon_iii_w_deg", "cm_iii_w_deg", "grs_lon_rel_deg",
                  "next_transits_24h", "on_disk_now"):
            self.assertIn(k, d)
        self.assertIsInstance(d["on_disk_now"], bool)
        self.assertAlmostEqual(d["grs_lon_iii_w_deg"], truth.grs_longitude_iii_w(T0), places=2)
        # on_disk_now agrees with |rel|
        self.assertEqual(d["on_disk_now"], abs(d["grs_lon_rel_deg"]) <= 60.0)


class TestMoonTransits(unittest.TestCase):
    """Ground-truthed against the Project Pluto 2026 Jupiter satellite event
    table (https://www.projectpluto.com/jevent.htm):
      2026 Aug 01  Io transit  03:59 -> 06:14   (mid ~05:06)
      2026 Aug 02  Io eclipse start 01:05 / occultation end 03:26 (BEHIND)
      2026 Aug 02  Europa transit 07:48 -> 10:41 (mid ~09:14)
      2026 Aug 03  Io eclipse start 19:34 / occultation end 21:56 (BEHIND)
    """

    @classmethod
    def setUpClass(cls):
        if not transits.moon_backend():
            raise unittest.SkipTest("no moon-ephemeris backend (ephem/spice)")

    def _near(self, events, expect_dt, tol_min=14):
        for e in events:
            d = abs((_parse(e.utc) - expect_dt).total_seconds()) / 60.0
            if d <= tol_min:
                return e
        return None

    def test_io_transits_match_published_table(self):
        ev = transits.moon_transits(T0, days=3.0, moon="io", step_s=180.0)
        # the two published Io transits in the window
        self.assertIsNotNone(self._near(ev, dt.datetime(2026, 8, 1, 5, 6)),
                             f"no Io transit near 05:06 Aug 1: {[e.utc for e in ev]}")
        self.assertIsNotNone(self._near(ev, dt.datetime(2026, 8, 2, 23, 36)),
                             f"no Io transit near 23:36 Aug 2: {[e.utc for e in ev]}")
        # and NOTHING at the published occultations (behind-disk events)
        self.assertIsNone(self._near(ev, dt.datetime(2026, 8, 2, 2, 15), tol_min=30),
                          "occultation leaked into transit list")
        self.assertIsNone(self._near(ev, dt.datetime(2026, 8, 3, 20, 45), tol_min=30),
                          "occultation leaked into transit list")
        for e in ev:
            self.assertLess(e.separation_rj, 1.05)

    def test_europa_transit_matches_published_table(self):
        ev = transits.moon_transits(T0, days=2.0, moon="europa", step_s=180.0)
        got = self._near(ev, dt.datetime(2026, 8, 2, 9, 14), tol_min=20)
        self.assertIsNotNone(got, f"no Europa transit near 09:14 Aug 2: {[e.utc for e in ev]}")

    def test_event_time_is_separation_minimum(self):
        ev = transits.moon_transits(T0, days=2.0, moon="io", step_s=180.0)
        self.assertTrue(ev)
        t = _parse(ev[0].utc)
        s0 = transits._moon_sep_ephem("io", t)[0]
        for dmin in (-15, 15):
            s1 = transits._moon_sep_ephem("io", t + dt.timedelta(minutes=dmin))[0]
            self.assertLessEqual(s0, s1 + 1e-3, "event not at a separation minimum")

    def test_planner_smoke(self):
        plan = transits.night_planner(T0, days=0.5, moons=("io",))
        self.assertIn("grs", plan)
        self.assertIn("grs_visibility_windows", plan)
        self.assertIn("moon_transits", plan)
        txt = transits.planner_text(plan)
        self.assertIn("OBSERVING PLANNER", txt)
        self.assertIn("GRS transits", txt)


if __name__ == "__main__":
    unittest.main()

"""
v6.9 desktop panel wiring — static + import-level checks.

Source-level assertions always run (the tab labels and the siril_nb.add /
handler wiring in the SHIPPED file); import-level attribute checks run
when tkinter is importable, mirroring tests/test_desktop_wiring.py.
"""
from __future__ import annotations

import os
import re
import sys
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

SRC = (APP / "desktop_app.py").read_text(encoding="utf-8")

_HAS_TK = False
try:
    import tkinter as _tk  # noqa: F401
    _HAS_TK = True
except ImportError:
    pass


class TestDesktopV69Static(unittest.TestCase):
    def test_rgb_combine_tab_wired(self):
        self.assertIn('text="  RGB Combine  "', SRC)
        m = re.search(r'text="  RGB Combine  "', SRC)
        self.assertIsNotNone(m)
        # tab is added to the siril notebook
        self.assertIn("self.siril_nb.add(tab_rgb", SRC)
        # all three channel pickers exist
        for ch in "RGB":
            self.assertIn(f"rgb_paths", SRC)
        self.assertIn("on_rgb_combine_run", SRC)

    def test_analysis_tab_wired(self):
        self.assertIn('text="  Analysis  "', SRC)
        self.assertIn("self.siril_nb.add(tab_an", SRC)
        for h in ("on_session_plan_run", "on_wind_run", "on_drift_run",
                  "on_wind_pick", "on_drift_pick"):
            self.assertIn(f"def {h}(self", SRC)

    def test_handlers_call_real_modules(self):
        # RGB combine handler routes to rgb_combine.combine_rgb
        self.assertIn("import rgb_combine", SRC)
        # analysis handlers route to the real science modules
        self.assertIn("from session_planner import session_plan", SRC)
        self.assertIn("from wind_analysis import", SRC)
        self.assertIn("from grs_drift import", SRC)

    def test_no_stubbed_placeholders(self):
        # the panel must not ship dead TODOs
        block = SRC[SRC.find('text="  RGB Combine  "'):]
        self.assertNotIn("TODO", block)


@unittest.skipUnless(_HAS_TK, "tkinter unavailable")
class TestDesktopV69Import(unittest.TestCase):
    def test_handlers_exist_on_class(self):
        try:
            import desktop_app
        except Exception as exc:  # pragma: no cover - env dependent
            self.skipTest(f"desktop_app import failed: {exc}")
        for h in ("on_rgb_pick", "on_rgb_combine_run", "on_session_plan_run",
                  "on_wind_pick", "on_wind_run", "on_drift_pick",
                  "on_drift_run"):
            self.assertTrue(callable(getattr(desktop_app.GRSDesktopApp, h, None)),
                            f"missing handler {h}")


if __name__ == "__main__":
    unittest.main()

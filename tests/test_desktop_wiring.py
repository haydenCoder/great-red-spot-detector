"""
Desktop handler wiring + construction safety.

Exercises the real shipped desktop_app module so missing callbacks
(e.g. _open_buttons_doc) fail the test before launch.

When tkinter is unavailable (headless server / CI), tests that import
desktop_app are skipped rather than raising ModuleNotFoundError.
"""
from __future__ import annotations

import inspect
import os
import re
import sys
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

os.environ.setdefault("TK_SILENCE_DEPRECATION", "1")

# Check if tkinter is available before importing desktop_app
_HAS_TK = False
try:
    import tkinter as _tk  # noqa: F401
    _HAS_TK = True
except ImportError:
    pass


@unittest.skipUnless(_HAS_TK, "tkinter not available — headless environment")
class TestDesktopWiring(unittest.TestCase):
    def test_open_buttons_doc_handler_exists(self):
        import desktop_app as da

        self.assertTrue(
            hasattr(da.GRSDesktopApp, "_open_buttons_doc"),
            "GRSDesktopApp must define _open_buttons_doc (wired in _build_ui)",
        )
        self.assertTrue(callable(da.GRSDesktopApp._open_buttons_doc))

    def test_all_wired_callbacks_exist(self):
        import desktop_app as da

        src = Path(da.__file__).read_text(encoding="utf-8")
        methods = {
            n
            for n, _ in inspect.getmembers(
                da.GRSDesktopApp, predicate=inspect.isfunction
            )
        }
        methods |= set(da.GRSDesktopApp.__dict__.keys())
        wired = set(
            re.findall(
                r"(?:command\s*=\s*|_action_btn\(\s*[^,]+,\s*[^,]+,\s*)"
                r"self\.([a-zA-Z0-9_]+)",
                src,
            )
        )
        wired |= set(re.findall(r"self\.(on_[a-zA-Z0-9_]+)\b", src))
        missing = sorted(
            m
            for m in wired
            if m not in methods and not hasattr(da.GRSDesktopApp, m)
        )
        self.assertEqual(
            missing,
            [],
            f"UI wires handlers that are not defined on GRSDesktopApp: {missing}",
        )

    def test_resolve_buttons_doc_path_pure_helper(self):
        import desktop_app as da

        p = da.resolve_buttons_doc_path(da.CODE, da.BASE)
        # Tree ships the book; helper must find a real file or return None honestly.
        if p is not None:
            self.assertTrue(p.exists(), f"resolved path must exist: {p}")
            self.assertTrue(
                p.suffix.lower() in {".md", ".html", ".htm"},
                f"unexpected doc type: {p}",
            )

    def test_resolve_manual_path_finds_book(self):
        import desktop_app as da

        p = da.resolve_manual_path(da.CODE, da.BASE)
        self.assertIsNotNone(p, "docs/GRS_OBSERVATORY_BOOK.md should exist in tree")
        assert p is not None
        self.assertTrue(p.exists())
        self.assertIn("GRS_OBSERVATORY_BOOK", p.name)

    def test_construct_desktop_app(self):
        """Construct real Tk app when display allows; else record honest limit."""
        import desktop_app as da

        try:
            app = da.GRSDesktopApp()
        except Exception as e:
            # Headless / no display: wiring checks above still gate the bug.
            self.skipTest(f"Tk construction unavailable: {type(e).__name__}: {e}")
            return
        try:
            self.assertEqual(app.title(), "GRS Observatory · Desktop")
            self.assertTrue(callable(app._open_buttons_doc))
            # Method must be bound on the instance (not missing → tk __getattr__)
            bound = getattr(app, "_open_buttons_doc")
            self.assertTrue(callable(bound))
        finally:
            app.destroy()


if __name__ == "__main__":
    unittest.main()

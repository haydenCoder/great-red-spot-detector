"""No locale-dependent text I/O in app/.

`Path.write_text(s)` and `open(p, "w")` use ``locale.getpreferredencoding(False)``.
Every report this project writes contains σ, °, ″ and em-dashes, so on a machine whose
preferred encoding is not UTF-8 (Linux under ``LC_ALL=C``, cron, a container without a
locale, a LaunchServices spawn with ``LC_CTYPE=C``) a bare write either raises
``UnicodeEncodeError`` mid-job or silently mangles the file.

That is exactly the class of bug that passes every test on a developer's macOS box, so
this is enforced structurally: every text-mode read/write in ``app/`` must say which
encoding it means. JSON dumps are exempt only because ``ensure_ascii=True`` (the default)
makes them pure ASCII — and the test asserts that assumption too, so the day someone adds
``ensure_ascii=False`` to a dump and writes it without an encoding, this fails.
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"


def _violations(src: str) -> list:
    """Return [(lineno, what)] for text I/O without an explicit encoding."""
    out = []
    tree = ast.parse(src)
    lines = src.splitlines()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
           and node.func.attr in ("write_text", "read_text"):
            if any(kw.arg == "encoding" for kw in node.keywords):
                continue
            out.append((node.lineno, f"{node.func.attr}() without encoding="))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
           and node.func.id == "open":
            mode = "r"
            if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                mode = str(node.args[1].value)
            for kw in node.keywords:
                if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                    mode = str(kw.value.value)
            if "b" in mode:
                continue                       # binary: encoding is meaningless
            if any(kw.arg == "encoding" for kw in node.keywords):
                continue
            out.append((node.lineno, f"open(mode={mode!r}) without encoding="))
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
           and node.func.attr == "dumps":
            # an ensure_ascii=False dump is only safe if it is written with an
            # explicit encoding; flag the combination where we can see it
            for kw in node.keywords:
                if kw.arg == "ensure_ascii" and isinstance(kw.value, ast.Constant) \
                   and kw.value.value is False:
                    seg = "\n".join(lines[max(0, node.lineno - 3): node.end_lineno])
                    if "encoding" not in seg:
                        out.append((node.lineno, "json.dumps(ensure_ascii=False) written without encoding="))
    return out


class TestTextEncoding(unittest.TestCase):
    def files(self):
        return sorted(APP.glob("*.py"))

    def test_every_text_io_in_app_declares_its_encoding(self):
        bad = []
        for f in self.files():
            for lineno, what in _violations(f.read_text(encoding="utf-8")):
                bad.append(f"{f.name}:{lineno}: {what}")
        self.assertEqual(bad, [], "text I/O that follows the machine locale:\n  " + "\n  ".join(bad))

    def test_checker_actually_catches_the_bug(self):
        # a self-test: if this passes vacuously the guard above is worth nothing
        sample = (
            "from pathlib import Path\n"
            "def w(p):\n"
            "    Path(p).write_text('σ 22°')\n"
            "    Path(p).write_text('x', encoding='utf-8')\n"
            "    with open(p, 'w') as f:\n        f.write('y')\n"
            "    with open(p, 'wb') as f:\n        f.write(b'y')\n"
        )
        found = [what for _ln, what in _violations(sample)]
        self.assertEqual(len(found), 2, f"expected exactly 2 violations, got {found}")

    def test_the_repo_actually_has_the_calls_we_claim(self):
        # guards against the sweep silently matching nothing (renamed API, typo)
        n = 0
        for f in self.files():
            n += f.read_text(encoding="utf-8").count("write_text(")
        self.assertGreater(n, 60, "write_text vanished from app/? the checker may be dead")


if __name__ == "__main__":
    unittest.main()

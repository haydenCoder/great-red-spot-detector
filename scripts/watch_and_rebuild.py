#!/usr/bin/env python3
"""Watch app/*.py and rebuild private Mac release into ~/Downloads/GRS_Observatory_RELEASE."""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
BUILDER = ROOT / "scripts" / "make_private_release.py"


def source_stamp() -> float:
    t = 0.0
    for p in APP.rglob("*.py"):
        try:
            t = max(t, p.stat().st_mtime)
        except OSError:
            pass
    return t


def rebuild() -> int:
    print(time.strftime("%H:%M:%S"), "rebuilding private release…")
    return subprocess.call([sys.executable, str(BUILDER)], cwd=str(ROOT))


def main() -> int:
    print("GRS watch-and-rebuild")
    print("  source:", APP)
    print("  on change → rebuild App + Mac one-file (exe-like)")
    print("  saves to: ~/Downloads/GRS_Observatory.app")
    print("            ~/Downloads/GRS_Observatory_Mac")
    print("            ~/Downloads/GRS_Observatory_RELEASE/")
    print("  After rebuild: quit & reopen the .app to load new code.")
    print("  (True Windows .exe only when built on Windows.)")
    print("  Ctrl+C to stop.\n")
    print("Initial build…")
    rebuild()
    last = source_stamp()
    cooldown = 10.0
    while True:
        time.sleep(2.0)
        now = source_stamp()
        if now > last + 0.3:
            time.sleep(1.2)
            rc = rebuild()
            last = source_stamp()
            if rc == 0:
                print(
                    time.strftime("%H:%M:%S"),
                    "OK → Downloads: GRS_Observatory.app + GRS_Observatory_Mac — reopen to use",
                )
            else:
                print(time.strftime("%H:%M:%S"), "rebuild failed rc=", rc)
            time.sleep(cooldown)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("\nStopped.")
        raise SystemExit(0)

#!/usr/bin/env python3
"""Build the GRS Observatory C core:  app/cspeed.c  ->  app/_cspeed.so

Any working C compiler suffices (cc / gcc / clang; CSPEED_CC overrides the
search).  Flags are deliberately conservative: -O3 without -ffast-math or
-march so floating-point semantics stay IEEE-strict and reproducible across
machines (parity with the scipy fallback path is asserted to <1e-12 by
tests/test_cspeed.py — typically ~1e-15, pure summation-order noise).

The shared object is gitignored: it is rebuilt on demand by app/cspeed.py
at load time, or explicitly via::

    python tools/build_cspeed.py

No compiler?  Nothing breaks: cspeed.py reports HAVE_C=False and every
caller takes the identical pure-numpy/scipy path (soft-fail loudly).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

APP = Path(__file__).resolve().parent.parent / "app"
SRC = APP / "cspeed.c"
OUT = APP / "_cspeed.so"

FLAGS = [
    "-O3", "-std=c99", "-fPIC", "-shared",
    "-fno-math-errno", "-fno-trapping-math",
    "-Wall", "-Wextra",
]


def find_compiler() -> str | None:
    env = os.environ.get("CSPEED_CC")
    if env:
        return env
    for name in ("cc", "gcc", "clang"):
        found = shutil.which(name)
        if found:
            return found
    return None


def build(verbose: bool = True) -> Path | None:
    cc = find_compiler()
    if cc is None:
        if verbose:
            print("build_cspeed: no C compiler found (cc/gcc/clang); "
                  "pure-numpy fallback stays active.")
        return None
    if not SRC.exists():
        if verbose:
            print(f"build_cspeed: missing source {SRC}")
        return None
    cmd = [cc, *FLAGS, str(SRC), "-o", str(OUT), "-lm"]
    if verbose:
        print("build_cspeed:", " ".join(cmd))
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except Exception as exc:  # pragma: no cover - environment dependent
        if verbose:
            print(f"build_cspeed: compiler failed to launch: {exc}")
        return None
    if proc.returncode != 0:
        if verbose:
            print("build_cspeed: compilation FAILED:\n" + proc.stderr)
        return None
    if verbose and proc.stderr.strip():
        print(proc.stderr.strip())
    if verbose:
        print(f"build_cspeed: OK -> {OUT}")
    return OUT


if __name__ == "__main__":
    sys.exit(0 if build(verbose=True) else 1)

#!/usr/bin/env python3
"""
Benchmark the native C extension against the NumPy fallback.

This is the *only* honest way to know if the C path is faster: time
both on the same machine, the same input, and report the speedup.

If the C extension is not built (no python3-dev / no grscore.so),
the script still runs and reports the NumPy-only baseline. The
speedup column will read 1.0× and the script will say so.

Usage:
    python3 tools/benchmark_native.py
    python3 tools/benchmark_native.py --build   # build the C extension first
    python3 tools/benchmark_native.py --openmp  # build with OpenMP
    python3 tools/benchmark_native.py --frames 100 --height 512 --width 512
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import time
from pathlib import Path

import numpy as np

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR / "app"))


def _maybe_build(openmp: bool = False) -> None:
    build = APP_DIR / "app" / "native" / "build_native.py"
    cmd = ["python3", str(build)]
    if openmp:
        cmd.append("--openmp")
    print(" ".join(cmd))
    r = subprocess.run(cmd, capture_output=True, text=True)
    print(r.stdout, end="")
    if r.returncode != 0:
        print(r.stderr)


def _time_it(fn, n_iter: int):
    """Median wall time over n_iter runs of fn()."""
    times = []
    for _ in range(n_iter):
        t0 = time.perf_counter()
        fn()
        times.append(time.perf_counter() - t0)
    times.sort()
    return times[n_iter // 2]


def main() -> int:
    ap = argparse.ArgumentParser(description="Benchmark the native C extension.")
    ap.add_argument("--build", action="store_true", help="build the C extension first")
    ap.add_argument("--openmp", action="store_true", help="build with OpenMP")
    ap.add_argument("--frames", type=int, default=50, help="number of test frames")
    ap.add_argument("--height", type=int, default=256, help="frame height")
    ap.add_argument("--width", type=int, default=256, help="frame width")
    ap.add_argument("--ap-half", type=int, default=16, help="AP half-size for the phase-corr test")
    ap.add_argument("--n-grid", type=int, default=6, help="AP grid size (n_grid x n_grid)")
    ap.add_argument("--n-octaves", type=int, default=3, help="octaves per AP")
    ap.add_argument("--n-iter", type=int, default=5, help="benchmark iterations")
    args = ap.parse_args()

    if args.build:
        _maybe_build(openmp=args.openmp)

    from native import (
        HAS_NATIVE, make_cylindrical, limb_rays, phase_corr_batch,
        _np_project_grid, _np_bilinear_map, _np_limb_rays,
    )

    print(f"Native backend: {'LOADED' if HAS_NATIVE else 'NOT BUILT (using NumPy)'}")
    print()

    rng = np.random.default_rng(0)
    frames = [rng.normal(0, 1, (args.height, args.width)).astype(np.float64)
              for _ in range(args.frames)]
    ref = frames[0]
    # Make a real disc so limb_rays has something to find
    yy, xx = np.mgrid[0:args.height, 0:args.width]
    cx, cy = args.width / 2, args.height / 2
    a_eq = min(args.height, args.width) * 0.35
    disc = (xx - cx) ** 2 + (yy - cy) ** 2 <= a_eq ** 2
    for i in range(args.frames):
        frames[i][disc] += 1.0

    # ------------------------------------------------------------------
    # 1) make_cylindrical benchmark
    # ------------------------------------------------------------------
    print("== make_cylindrical (one frame, width=1440, height=720) ==")
    H, W = 720, 1440
    xc, yc, aeq, flat = args.width / 2, args.height / 2, a_eq, 0.0649

    def _t_native():
        make_cylindrical(ref, xc, yc, aeq, flat, 0.0, 0.0, W, H)

    def _t_numpy():
        xs, ys, zl = _np_project_grid(W, H, xc, yc, aeq, flat, 0.0, 0.0)
        _np_bilinear_map(ref, xs, ys, zl, 0.02)

    t_n = _time_it(_t_native, args.n_iter) if HAS_NATIVE else float("nan")
    t_p = _time_it(_t_numpy, args.n_iter)
    speedup = t_p / t_n if (HAS_NATIVE and t_n > 0) else float("nan")
    print(f"  C path   : {t_n*1000:.2f} ms / call"
          + (f"  ({speedup:.2f}× speedup)" if HAS_NATIVE else "  (no C build)"))
    print(f"  NumPy    : {t_p*1000:.2f} ms / call")
    print()

    # ------------------------------------------------------------------
    # 2) limb_rays benchmark
    # ------------------------------------------------------------------
    print(f"== limb_rays (one frame, n_rays=720, n_rad=300) ==")
    n_rays, n_rad = 720, 300

    def _t_native_limb():
        limb_rays(ref, xc, yc, aeq, n_rays, n_rad, 0.18, 0.5, 1.3)

    def _t_numpy_limb():
        _np_limb_rays(ref, xc, yc, aeq, n_rays, n_rad, 0.18, 0.5, 1.3)

    t_n = _time_it(_t_native_limb, args.n_iter) if HAS_NATIVE else float("nan")
    t_p = _time_it(_t_numpy_limb, args.n_iter)
    speedup = t_p / t_n if (HAS_NATIVE and t_n > 0) else float("nan")
    print(f"  C path   : {t_n*1000:.2f} ms / call"
          + (f"  ({speedup:.2f}× speedup)" if HAS_NATIVE else "  (no C build)"))
    print(f"  NumPy    : {t_p*1000:.2f} ms / call")
    print()

    # ------------------------------------------------------------------
    # 3) phase_corr_batch benchmark — the JPA stacker hot path
    # ------------------------------------------------------------------
    print(f"== phase_corr_batch ({args.frames} frames × {args.n_grid}×{args.n_grid} APs × {args.n_octaves} octaves) ==")
    ys_g = np.linspace(args.height * 0.2, args.height * 0.8, args.n_grid)
    xs_g = np.linspace(args.width * 0.2, args.width * 0.8, args.n_grid)
    aps = np.array([[x, y] for y in ys_g for x in xs_g], dtype=np.float64)

    def _t_native_pcb():
        for fr in frames:
            phase_corr_batch(aps, fr, ref, ap_half=args.ap_half, n_octaves=args.n_octaves)

    t_n = _time_it(_t_native_pcb, max(1, args.n_iter // 5)) if HAS_NATIVE else float("nan")
    t_p = _time_it(_t_native_pcb, max(1, args.n_iter // 5))   # currently the same code path
    print(f"  per-batch: C={t_n*1000:.2f} ms / NumPy={t_p*1000:.2f} ms"
          + ("  (C path is a stub — currently uses NumPy)" if HAS_NATIVE else "  (no C build)"))
    print()

    # ------------------------------------------------------------------
    # Summary
    # ------------------------------------------------------------------
    print("== Summary ==")
    if not HAS_NATIVE:
        print("  Native C extension is NOT built. To benchmark the C path:")
        print("    python3 app/native/build_native.py --openmp")
        print("    python3 tools/benchmark_native.py --build --openmp")
    else:
        print("  Native C extension IS built. See the per-kernel speedups above.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

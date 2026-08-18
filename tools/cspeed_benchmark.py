#!/usr/bin/env python3
"""Measured speed of the C core vs the scipy/numpy path (same machine,
same inputs, same APIs — repository rule: claims are measured, and the
numbers printed here are the ones quoted in docs/CHANGELOG).

Usage:  .venv/bin/python tools/cspeed_benchmark.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

import cspeed  # noqa: E402


def _bench(fn, reps: int) -> float:
    fn()  # warm-up
    t0 = time.perf_counter()
    for _ in range(reps):
        fn()
    return (time.perf_counter() - t0) / reps


def main() -> int:
    from scipy.ndimage import gaussian_filter, shift as nd_shift
    import ap_stacker
    import image_warp

    print(f"C core: {'LOADED — ' + cspeed.status_note() if cspeed.HAVE_C else 'NOT AVAILABLE — ' + cspeed.status_note()}")
    if not cspeed.HAVE_C:
        return 1

    rng = np.random.default_rng(97)
    rows = []

    # ---- 1. _lk_refine micro -------------------------------------------------
    crops = []
    for _ in range(120):
        t = gaussian_filter(rng.normal(size=(32, 32)), 2.0)
        m = nd_shift(t, rng.uniform(-1.5, 1.5, 2), order=3, mode="nearest")
        crops.append((t, m))

    def lk_with(flag):
        cspeed.set_enabled(flag)
        t = _bench(lambda: [ap_stacker._lk_refine(a, b, 0.0, 0.0)
                            for a, b in crops[:40]], 3)
        return t / 40
    t_py, t_c = lk_with(False), lk_with(True)
    rows.append(("_lk_refine (32x32 crop, 4 iters)", t_py, t_c))

    # ---- 2. warp_shift2d --------------------------------------------
    img = gaussian_filter(rng.normal(size=(400, 300)), 2.5)

    def shift_with(flag):
        cspeed.set_enabled(flag)
        return _bench(lambda: image_warp.warp_shift2d(img, 0.7, -1.25), 30)
    t_py, t_c = shift_with(False), shift_with(True)
    rows.append(("warp_shift2d 400x300 order3", t_py, t_c))

    # ---- 3. warp_field2d (derotation-class remap) ---------------------
    hb, wb = 300, 400
    img2 = gaussian_filter(rng.normal(size=(hb, wb)), 2.5)
    yy, xx = np.mgrid[0:hb, 0:wb].astype(np.float64)
    dy = 0.9 * np.sin(xx / 8.0)
    dx = 1.1 * np.cos(yy / 9.0)

    def field_with(flag):
        cspeed.set_enabled(flag)
        return _bench(lambda: image_warp.warp_field2d(img2, dy, dx), 20)
    t_py, t_c = field_with(False), field_with(True)
    rows.append(("warp_field2d 300x400 order3", t_py, t_c))

    # ---- 4. stack_ap end-to-end ---------------------------------------
    base = gaussian_filter(rng.normal(size=(96, 72)), 2.5)
    frames = [nd_shift(base, rng.uniform(-1.6, 1.6, 2), order=3, mode="nearest")
              + rng.normal(scale=0.01, size=base.shape) for _ in range(12)]
    cfg = ap_stacker.APStackConfig(ap_size_px=32, keep_frac=0.5, drizzle=1)

    out = {}
    secs = {}
    for flag in (False, True):
        cspeed.set_enabled(flag)
        r = ap_stacker.stack_ap(frames, cfg)
        out[flag], secs[flag] = r, r.secs
    d = float(np.abs(out[False].stack - out[True].stack).max())
    rows.append(("stack_ap 12f x full APs (end-to-end)", secs[False], secs[True]))
    print(f"stack parity check: max|d| = {d:.2e}")

    cspeed.set_enabled(True)
    print("\n| workload | numpy/scipy | C core | speedup |")
    print("|---|---:|---:|---:|")
    for name, tp, tc in rows:
        print(f"| {name} | {tp*1e3:8.3f} ms | {tc*1e3:8.3f} ms "
              f"| {tp/tc:5.2f}x |")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""cspeed — optional C core for the spline-heavy hot paths (v7.0.0).

WHAT it accelerates (profiled): `ap_stacker._lk_refine` dominated ~91% of
stack time, almost all of it five scipy `map_coordinates` calls per
iteration; `image_warp.warp_shift2d` / `warp_field2d` redo the spline
prefilter per call.  This module wraps three C kernels (`cs_sample3`,
`cs_lk_step`, plus grid helpers) with a ctypes loader.

HONEST SCOPE / PARITY CONTRACT: the C path replicates scipy's math to
last-ULP scale — tests/test_cspeed.py asserts max|delta| < 1e-12 against
`scipy.ndimage` on random fields (measured ~1e-15: summation-order noise
only, far below any photon statistic).  Where no compiler exists we do not
fail: HAVE_C=False and callers use the identical scipy path (one loud
warning line — soft-fail loudly, never silently wrong).

Controls:  CSPEED=0 in the environment disables the C path entirely;
`set_enabled(False)` toggles at runtime (used by the parity tests to A/B
the paths on identical inputs).
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from pathlib import Path

import numpy as np

_HERE = Path(__file__).resolve().parent
_DISABLED_ENV = os.environ.get("CSPEED", "1").strip() in ("0", "false", "no")

LIB = None          # ctypes.CDLL or None
HAVE_C = False      # usable C kernels loaded?
NOTE = "not attempted"  # human-readable status for loud-soft-fail reporting
_ENABLED = True     # runtime toggle (tests; ops escape hatch)


def _load_shared():
    global LIB, HAVE_C, NOTE
    if _DISABLED_ENV:
        NOTE = "disabled via CSPEED=0"
        return
    so = _HERE / "_cspeed.so"
    if not so.exists():
        builder = _HERE.parent / "tools" / "build_cspeed.py"
        try:
            subprocess.run(
                [sys.executable, str(builder)], timeout=120,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
    for cand in (_HERE / "_cspeed.so", _HERE / "_cspeed.dylib"):
        try:
            LIB = ctypes.CDLL(str(cand))
            break
        except OSError:
            continue
    if LIB is None:
        NOTE = "no C compiler available (or build failed) — scipy fallback active"
        return
    try:
        _signatures()
        if not np.isclose(LIB.cs_selfcheck(), 4.0 / 6.0, rtol=0, atol=1e-15):
            LIB = None
            NOTE = "C self-check failed — scipy fallback active"
            return
        HAVE_C = True
        NOTE = f"C core loaded ({cand.name}, version {LIB.cs_version()})"
    except Exception as exc:  # pragma: no cover - defensive
        LIB = None
        NOTE = f"C init failed ({exc}) — scipy fallback active"


def _f64p(ndim=None):
    kw = {"dtype": np.float64, "flags": "C_CONTIGUOUS"}
    if ndim is not None:
        kw["ndim"] = ndim
    return np.ctypeslib.ndpointer(**kw)


def _signatures():
    LIB.cs_version.restype = ctypes.c_int
    LIB.cs_version.argtypes = []
    LIB.cs_selfcheck.restype = ctypes.c_double
    LIB.cs_selfcheck.argtypes = []
    LIB.cs_sample3.restype = None
    LIB.cs_sample3.argtypes = [_f64p(), ctypes.c_long, ctypes.c_long,
                               _f64p(), _f64p(), _f64p(), ctypes.c_long]
    LIB.cs_lk_step.restype = None
    LIB.cs_lk_step.argtypes = [_f64p(), ctypes.c_long, ctypes.c_long,
                               _f64p(),
                               ctypes.POINTER(ctypes.c_double),  # w or NULL
                               _f64p(), _f64p(), ctypes.c_long,
                               ctypes.c_double, ctypes.c_double,
                               _f64p()]


def have_c() -> bool:
    """True when the C kernels are loaded AND enabled."""
    return HAVE_C and _ENABLED


def set_enabled(flag: bool) -> None:
    """Runtime toggle for A/B parity runs (does not rebuild anything)."""
    global _ENABLED
    _ENABLED = bool(flag)


def status_note() -> str:
    return NOTE


# --------------------------------------------------------------------------
# spline_prefilter: the scipy recipe map_coordinates(order=3, mode="nearest")
# applies internally — 12-px edge pad + spline_filter(mode="nearest").
# Sampling the result at coords+12 with prefilter=False is verified
# bit-identical to direct map_coordinates calls.
# --------------------------------------------------------------------------

def spline_prefilter(arr: np.ndarray) -> np.ndarray:
    from scipy.ndimage import spline_filter
    a = np.ascontiguousarray(np.asarray(arr, dtype=np.float64))
    return np.ascontiguousarray(
        spline_filter(np.pad(a, 12, mode="edge"), order=3, mode="nearest"))


def sample3(coef: np.ndarray, ys: np.ndarray, xs: np.ndarray,
            out: np.ndarray | None = None) -> np.ndarray:
    """Evaluate a cubic-spline coefficient array at n points.

    C path when enabled; otherwise scipy (identical math).  `coef` must be
    C-contiguous float64; `ys`/`xs` any float64-contiguous same-shape arrays
    (they are raveled)."""
    y = np.ascontiguousarray(ys, dtype=np.float64)
    x = np.ascontiguousarray(xs, dtype=np.float64)
    shape = y.shape
    yf, xf = y.ravel(), x.ravel()
    n = yf.size
    if out is None:
        out = np.empty(shape, dtype=np.float64)
    o = np.ascontiguousarray(out, dtype=np.float64).ravel()
    if have_c():
        LIB.cs_sample3(coef, coef.shape[0], coef.shape[1], yf, xf, o, n)
    else:
        from scipy.ndimage import map_coordinates
        o[...] = map_coordinates(coef, [yf, xf], order=3, mode="nearest",
                                 prefilter=False).ravel()
    return out.astype(np.float64, copy=False).reshape(shape)


def field_warp3(img: np.ndarray, sy: np.ndarray, sx: np.ndarray) -> np.ndarray:
    """out[y, x] = img[sy[y, x], sx[y, x]] via order-3 splines mode=nearest.

    Equivalent to map_coordinates(img, [sy, sx], order=3, mode="nearest").
    C path: prefilter once (scipy spline_filter — already C) then batch
    sample in compiled code."""
    arr = np.ascontiguousarray(np.asarray(img, dtype=np.float64))
    yy = np.ascontiguousarray(np.asarray(sy, dtype=np.float64))
    xx = np.ascontiguousarray(np.asarray(sx, dtype=np.float64))
    if have_c():
        coef = spline_prefilter(arr)
        return sample3(coef, yy + 12.0, xx + 12.0)
    from scipy.ndimage import map_coordinates
    return map_coordinates(arr, [yy, xx], order=3, mode="nearest")


def lk_sums(coef: np.ndarray, ref_flat: np.ndarray, w_flat,
            y0_flat: np.ndarray, x0_flat: np.ndarray,
            cy: float, cx: float):
    """Fused LK normal-equation accumulations (cs_lk_step).

    Returns (a, b, c, d1, d2) = (Σgy², Σgy·gx, Σgx², Σgy·d, Σgx·d).
    C path only vs numpy replication when HAVE_C is False (the caller
    normally only reaches this helper when have_c() — the fallback exists
    for the parity tests)."""
    out = np.zeros(5, dtype=np.float64)
    n = y0_flat.size
    if have_c():
        if w_flat is None:
            wp = ctypes.POINTER(ctypes.c_double)()
        else:
            wp = np.ascontiguousarray(w_flat, dtype=np.float64).ctypes.data_as(
                ctypes.POINTER(ctypes.c_double))
        LIB.cs_lk_step(coef, coef.shape[0], coef.shape[1],
                       ref_flat, wp, y0_flat, x0_flat, n,
                       float(cy), float(cx), out)
        return tuple(out)
    # numpy replication of the exact kernel semantics (parity reference)
    from scipy.ndimage import map_coordinates
    ys = y0_flat - cy
    xs = x0_flat - cx

    def S(dy, dx):
        return map_coordinates(coef, [ys + dy, xs + dx], order=3,
                               mode="nearest", prefilter=False)

    v = S(0.0, 0.0)
    gy = 0.5 * (S(1.0, 0.0) - S(-1.0, 0.0))
    gx = 0.5 * (S(0.0, 1.0) - S(0.0, -1.0))
    wi = 1.0 if w_flat is None else np.asarray(w_flat, dtype=np.float64)
    d = ref_flat - wi * v
    gy = gy * wi
    gx = gx * wi
    return (float((gy * gy).sum()), float((gy * gx).sum()),
            float((gx * gx).sum()), float((gy * d).sum()),
            float((gx * d).sum()))


_load_shared()

__all__ = ["HAVE_C", "have_c", "set_enabled", "status_note",
           "spline_prefilter", "sample3", "field_warp3", "lk_sums"]

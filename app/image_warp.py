#!/usr/bin/env python3
"""image_warp.py — exact sub-pixel image translation, shared by every
stacker/derotator in the app.

WHY THIS MODULE EXISTS (the v6.8.x FFT-shift audit)
===================================================
Six call sites used the classic "FFT phase ramp" sub-pixel shift:

    phase = exp(-2j * pi * (dy*yy/h + dx*xx/w))
    shifted = np.real(np.fft.ifft2(np.fft.fft2(img) * phase))

For NON-INTEGER shifts on real input this is mathematically broken:
multiplying a Hermitian spectrum by a linear phase destroys Hermitian
symmetry, so the inverse transform is complex and keeping only the real
part returns the EVEN MIXTURE (f(x+s)+f(x-s))/2, not f(x-s). Measured
2026-08-07 on a 160x160 test field (gaussian-filtered noise, 1.5 px
planted displacement): shifts of +1.5 px and -1.5 px gave byte-identical
MSE 0.001077 (both wrong), while a spline resample recovers 1.3e-05.
Integer shifts under the FFT ramp are exact (2.6e-32), which is why the
bug survived every smooth-texture benchmark. All app shift code now goes
through this one spatial-domain spline implementation.
"""
from __future__ import annotations

import numpy as np

try:  # optional C core (v7.0.0) — identical math, compiled speed
    import cspeed as _cspeed
except Exception:  # pragma: no cover - import guard
    _cspeed = None


def warp_shift2d(img: np.ndarray, dy: float, dx: float, order: int = 3) -> np.ndarray:
    """Translate an image: content moves by (+dy, +dx) pixels.

    Spatial-domain cubic-spline resampling (scipy.ndimage.shift, order=3,
    mode="nearest") — exact at sub-pixel shifts, no circulant wraparound,
    no Hermitian-symmetry pathologies (see module docstring for the
    measured FFT-phase-ramp failure mode this replaces).

    Channel-aware: an (h, w, 3) image shifts every channel identically.
    """
    from scipy.ndimage import shift as _nd_shift
    arr = np.asarray(img, dtype=np.float64)
    if int(order) == 3 and _cspeed is not None and _cspeed.have_c():
        # C fast path (v7.0.0): prefilter once, batch-sample in compiled
        # code (parity pinned ~1e-15 in tests/test_cspeed.py).
        h, w = arr.shape[:2]
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
        yy -= float(dy)
        xx -= float(dx)
        if arr.ndim == 3:
            return np.stack(
                [_cspeed.field_warp3(arr[..., c], yy, xx)
                 for c in range(arr.shape[2])],
                axis=-1,
            )
        return _cspeed.field_warp3(arr, yy, xx)
    if arr.ndim == 3:
        return np.stack(
            [_nd_shift(arr[..., c], shift=(float(dy), float(dx)),
                       order=int(order), mode="nearest")
             for c in range(arr.shape[2])],
            axis=-1,
        )
    return _nd_shift(arr, shift=(float(dy), float(dx)),
                     order=int(order), mode="nearest")


def warp_field2d(img: np.ndarray, dy: np.ndarray, dx: np.ndarray,
                 order: int = 3) -> np.ndarray:
    """Warp an image by a per-pixel displacement field.

    ``dy`` / ``dx`` are (h, w) float arrays giving, for every OUTPUT pixel,
    the displacement of the CONTENT that should land there, i.e.

        out[y, x] = in[y - dy[y, x], x - dx[y, x]]

    (the same "content moves by (+dy, +dx)" convention as `warp_shift2d`,
    which is the special case dy/dx = const).

    Spatial-domain spline resampling (scipy.ndimage.map_coordinates,
    mode="nearest") — no circulant wraparound, no FFT pathologies (see the
    module docstring). Channel-aware: (h, w, 3) warps all channels with the
    same field. Off-disk content is undefined; callers' fields may be NaN
    there — NaNs are replaced by 0 displacement (the resample then just
    reads the input at those pixels, typically sky background).
    """
    from scipy.ndimage import map_coordinates
    arr = np.asarray(img, dtype=np.float64)
    fy = np.nan_to_num(np.asarray(dy, dtype=np.float64), nan=0.0)
    fx = np.nan_to_num(np.asarray(dx, dtype=np.float64), nan=0.0)
    h, w = arr.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    sy = yy - fy
    sx = xx - fx
    if int(order) == 3 and _cspeed is not None and _cspeed.have_c():
        # C fast path (v7.0.0): prefilter once, batch-sample in compiled
        # code (parity pinned ~1e-15 in tests/test_cspeed.py).
        if arr.ndim == 3:
            return np.stack(
                [_cspeed.field_warp3(arr[..., c], sy, sx)
                 for c in range(arr.shape[2])],
                axis=-1,
            )
        return _cspeed.field_warp3(arr, sy, sx)
    if arr.ndim == 3:
        return np.stack(
            [map_coordinates(arr[..., c], [sy, sx], order=int(order),
                             mode="nearest")
             for c in range(arr.shape[2])],
            axis=-1,
        )
    return map_coordinates(arr, [sy, sx], order=int(order), mode="nearest")


__all__ = ["warp_shift2d", "warp_field2d"]

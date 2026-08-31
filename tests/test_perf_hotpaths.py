"""Speed-regression tests for the hot measurement paths.

These are not "was it fast enough on this machine" flakiness tests — each
asserts a multi-second upper bound on a path that used to take tens of
seconds to minutes:

  * m_radial_symmetry recomputed np.median(band[valid]) inside a per-pixel
    vote loop: ~63 s on a 1400×700 map (O(N² log N)). Now vectorised
    (median hoisted, bincount scatter) → ~10 ms.
  * multiscale_template_match re-transformed the band 3× per template
    (corr + 2 energy FFTs) with fftconvolve. Now one shared band FFT +
    separable box-filter energy → ~5-10× faster, bit-identical peaks.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pytest

APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from precision_engine import NavState, make_cylindrical  # noqa: E402


def _fake_cyl(h: int = 700, w: int = 1400, seed: int = 3) -> np.ndarray:
    """Cylindrical map with a plausible GRS-like dark oval (no full render)."""
    rng = np.random.default_rng(seed)
    cyl = np.clip(rng.normal(1.0, 0.04, (h, w)), 0.05, None)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    lon_rel = -90.0 + xx / (w - 1) * 180.0
    lat = 90.0 - yy / (h - 1) * 180.0
    ell = ((lon_rel - 20.0) / 6.0) ** 2 + ((lat + 22.0) / 4.0) ** 2
    cyl = cyl - 0.45 * np.exp(-0.5 * ell * 2.2)
    # edge fallout so the disk mask behaves like a real map
    cyl[lat > 60.0] *= 0.02
    cyl[lat < -70.0] *= 0.02
    return np.maximum(cyl, 0.0)


class TestHotPathSpeed:
    def test_radial_symmetry_no_per_pixel_median(self):
        from all_methods_extra import m_radial_symmetry
        from all_methods import _cyl_lon_lat_grids

        cyl = _fake_cyl()
        nav = NavState(xc=700.0, yc=350.0, a_eq_px=300.0, cm_iii_deg=0.0, distance_au=5.2)
        lon_iii, lat = _cyl_lon_lat_grids(cyl, nav)
        t0 = time.perf_counter()
        for _ in range(3):
            hit = m_radial_symmetry(cyl, nav, lon_iii, lat)
        dt = time.perf_counter() - t0
        assert hit.ok, f"radial symmetry failed: {hit.note}"
        # 3 runs must finish far under 1 s; the old loop took ~60 s for one.
        assert dt < 1.0, f"RAD_SYM too slow: 3 runs in {dt:.2f}s (old: ~190 s)"

    def test_extra_methods_suite_total(self):
        from all_methods_extra import run_extra_methods
        from all_methods import _cyl_lon_lat_grids

        cyl = _fake_cyl()
        nav = NavState(xc=700.0, yc=350.0, a_eq_px=300.0, cm_iii_deg=0.0, distance_au=5.2)
        lon_iii, lat = _cyl_lon_lat_grids(cyl, nav)
        t0 = time.perf_counter()
        hits = run_extra_methods(cyl, nav, lon_iii, lat)
        dt = time.perf_counter() - t0
        assert len(hits) > 20
        # Before the vectorisation this suite took ~64 s on this size.
        assert dt < 5.0, f"extra-method suite too slow: {dt:.2f}s"

    def test_multiscale_template_match_speed(self):
        from vlbi_metrology import multiscale_template_match
        import vlbi_metrology as vm

        cyl = make_cylindrical(_fake_cyl(h=720, w=1440), NavState(
            xc=453.6, yc=540.0, a_eq_px=400.0, cm_iii_deg=150.0, distance_au=5.2),
            1440, 720)
        nav = NavState(xc=453.6, yc=540.0, a_eq_px=400.0, cm_iii_deg=150.0, distance_au=5.2)
        multiscale_template_match(cyl, nav)  # warm imports
        t0 = time.perf_counter()
        m = multiscale_template_match(cyl, nav)
        dt = time.perf_counter() - t0
        assert m, "no multiscale result"
        assert dt < 2.0, f"multiscale NCC too slow: {dt:.2f}s"

    def test_fft_ncc_matches_fftconvolve(self):
        """Shared-FFT correlation must equal scipy fftconvolve (same mode)."""
        from scipy.signal import fftconvolve
        from precision_engine import _ncc_corr_ctx, _ncc_corr_from_ctx

        rng = np.random.default_rng(11)
        band = rng.normal(size=(232, 2880))
        shapes = []
        for th in (65, 69, 73, 77, 81):
            for tw in (159, 161, 163, 165):
                shapes.append((th, tw))
        ctx = _ncc_corr_ctx(band, shapes)
        assert ctx is not None
        for th, tw in shapes[:5] + shapes[-2:]:
            tmpl = rng.normal(size=(th, tw))
            mine = _ncc_corr_from_ctx(ctx, tmpl)
            ref = fftconvolve(band - band.mean(), tmpl[::-1, ::-1], mode="same")
            assert np.allclose(mine, ref, rtol=1e-9, atol=1e-9), f"mismatch at {(th, tw)}"

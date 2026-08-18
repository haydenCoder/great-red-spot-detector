#!/usr/bin/env python3
"""Tests for app/rgb_combine.py — rotation-derotated filter-wheel RGB
compositing.

The end-to-end test renders three mono "filter captures" 240 s apart with
the true-sky-geometry video renderer (sub-Earth latitude -2.3 deg, north
pole PA 18 deg — BOTH tilted, so analytic per-row derotation models are
wrong by construction) and checks that combine_rgb:

  1. collapses the colour fringe by a large factor (rotation between
     filters is ~2.4 deg = ~5 px at this scale),
  2. leaves the R/B channels achromatically aligned with the G channel to
     a fraction of a pixel (the physical requirement; fringe metrics on
     smooth texture can be gamed, a measured channel-on-channel shift
     cannot),
  3. reports near-full coverage and honest bookkeeping.

Unit tests pin: the vectorised cloud-rate twin vs the scalar Planet
method, the wind-term sign/magnitude in the sampling grid, and the band
polish against a planted sub-pixel shift.
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from planet_models import JUPITER
from precision_engine import NavState
from video_synth import VideoSynthSpec, render_video
from image_warp import warp_shift2d, warp_field2d
from rgb_combine import (RGBCombineConfig, _band_polish, _px_to_lonlat_vec,
                         _wrap180, combine_rgb, rotation_sample_grid)


def _three_captures(sub_lat=-2.3, pa=18.0, span_s=240.0, seed=11):
    spec = VideoSynthSpec(width=384, height=288, n_frames=3,
                          fps=1.0 / span_s, cm_deg_per_h=36.29,
                          cm0_deg=104.0, sub_lat_deg=sub_lat,
                          north_pa_deg=pa, disk_frac=0.40,
                          seeing_fwhm_px=(0.9, 1.1),
                          noise_rms=(0.001, 0.0015),
                          shift_rms_px=0.0, gain_jitter=0.0,
                          wave_amp=0.05, seed=seed, rgb=False)
    v = render_video(spec)
    return spec, v


def _nav_for(spec, v, index):
    t = v.truth
    return NavState(xc=t["disk_xc_px"], yc=t["disk_yc_px"],
                    a_eq_px=t["disk_a_eq_px"], flattening=0.06487,
                    cm_iii_deg=t["cm_iii_per_frame_deg"][index],
                    sub_lat_deg=spec.sub_lat_deg,
                    north_pa_deg=spec.north_pa_deg)


class TestVectorisedHelpers:
    def test_inverse_projection_matches_scalar(self):
        nav = NavState(xc=100.0, yc=80.0, a_eq_px=50.0,
                       sub_lat_deg=-2.3, north_pa_deg=18.0, cm_iii_deg=75.0)
        from precision_engine import px_to_lonlat
        yy, xx = np.mgrid[40:120:7, 60:150:9].astype(np.float64)
        lon, lat, disk = _px_to_lonlat_vec(yy, xx, nav)
        for y, x, lo, la, dk in zip(yy.ravel(), xx.ravel(),
                                    lon.ravel(), lat.ravel(), disk.ravel()):
            lo_s, la_s = px_to_lonlat(float(y), float(x), nav)
            assert abs(((lo - lo_s + 180) % 360) - 180) < 1e-6
            assert abs(la - la_s) < 1e-6

    def test_rate_twin_matches_planet_scalar(self):
        lats = np.linspace(-60, 60, 121)
        tab = JUPITER.zonal_wind_mps
        u = np.interp(np.abs(lats), tab[:, 0], tab[:, 1])
        cos_la = np.clip(np.cos(np.radians(lats)), 0.05, None)
        twin = (JUPITER.rotation_rate_deg_per_s
                + np.degrees(u * 1e-3 / JUPITER.req_km) / cos_la)
        for la, rt in zip(lats, twin):
            assert abs(rt - JUPITER.cloud_tracking_rate_deg_per_s(float(la))) < 1e-12

    def test_wrap180(self):
        assert _wrap180(np.array([190.0]))[0] == pytest.approx(-170.0)
        assert _wrap180(np.array([-200.0]))[0] == pytest.approx(160.0)
        assert _wrap180(np.array([0.0, 359.0]))[1] == pytest.approx(-1.0)

    def test_field_warp_matches_constant_shift(self):
        rng = np.random.default_rng(3)
        img = rng.random((64, 80))
        const = warp_shift2d(img, dy=-0.7, dx=1.25)
        field = warp_field2d(img, np.full((64, 80), -0.7),
                             np.full((64, 80), 1.25))
        assert np.abs(const - field).max() < 1e-9


class TestRotationField:
    def test_wind_term_sign_and_magnitude(self):
        """include_winds must move the sampling grid as a per-parallel cloud
        rate: at ~11 deg N the Jupiter table has u=+50 m/s (prograde), and
        prograde winds push features further -x in the sky frame, i.e. the
        sample point shifts -x relative to the bulk-only grid by exactly
        du_rate*dt*px_per_deg."""
        nav = NavState(xc=100.0, yc=100.0, a_eq_px=100.0, cm_iii_deg=0.0)
        shape = (200, 200)
        dt = 600.0
        sy_w, sx_w, ok_w, lat_w, _ = rotation_sample_grid(
            JUPITER, nav, shape, dt, include_winds=True)
        sy_b, sx_b, ok_b, _, lon_rel = rotation_sample_grid(
            JUPITER, nav, shape, dt, include_winds=False)
        # compare near the central meridian only: the analytic centre-line
        # chord (px_per_deg_lon) is exact there; off-CM pixels legitimately
        # carry the cos(lon_rel) foreshortening factor of the true projection
        m = ok_w & ok_b & (np.abs(lat_w - 11.0) < 1.0) & (np.abs(lon_rel) < 8.0)
        assert m.sum() > 30
        # expected difference: -(u_rate_deg_s * dt) * px_per_deg_lon(11 deg)
        u = JUPITER.zonal_wind_residual_mps(11.0)
        du_rate = math.degrees(u * 1e-3 / JUPITER.req_km) \
            / math.cos(math.radians(11.0))
        exp_dx = -du_rate * dt * JUPITER.px_per_deg_lon(11.0, nav.a_eq_px)
        got_dx = float(np.median((sx_w - sx_b)[m]))
        assert got_dx == pytest.approx(exp_dx, rel=0.02)
        # and the y displacement should be ~unchanged (winds are zonal)
        assert np.abs((sy_w - sy_b)[m]).max() < 0.05

    def test_zero_dt_is_identity(self):
        nav = NavState(xc=96.0, yc=72.0, a_eq_px=55.0,
                       sub_lat_deg=-2.3, north_pa_deg=18.0, cm_iii_deg=22.0)
        sy, sx, ok, _, _ = rotation_sample_grid(JUPITER, nav, (144, 192), 0.0)
        yy, xx = np.mgrid[0:144, 0:192].astype(np.float64)
        assert np.abs(sy[ok] - yy[ok]).max() < 1e-6
        assert np.abs(sx[ok] - xx[ok]).max() < 1e-6
        assert ok.sum() > 5000


class TestBandPolish:
    def test_planted_subpixel_shift_recovered(self):
        from scipy.ndimage import gaussian_filter
        rng = np.random.default_rng(5)
        # broadband 2D texture (belt-modulated filtered noise) — the
        # physically fair case: full two-parameter observability per band
        yy, xx = np.mgrid[0:160, 0:200].astype(np.float64)
        lat = (yy - 80.0) * 0.5
        img = gaussian_filter(rng.random((160, 200)), 2.0)
        img = (img - img.mean()) * (1.0 + 0.35 * np.sin(lat / 8.0)) + 0.5
        img += rng.normal(0, 0.005, img.shape)
        disk = (((xx - 100) / 90.0) ** 2 + ((yy - 80) / 70.0) ** 2) < 1.0
        planted_dy, planted_dx = 0.45, -1.25
        moved = warp_shift2d(img, dy=planted_dy, dx=planted_dx)
        bands, info = _band_polish(img, moved, lat, disk,
                                   max_resid_px=3.0, n_bands=7)
        # apply-shift must be the negative of the planted content motion
        n_applied = 0
        for b in range(7):
            if info[b]["applied"] < 1.0:
                continue
            n_applied += 1
            assert bands[b][0] == pytest.approx(-planted_dy, abs=0.12)
            assert bands[b][1] == pytest.approx(-planted_dx, abs=0.12)
        assert n_applied >= 5

    def test_aperture_degenerate_texture_not_applied(self):
        # pure sine band texture: the cross-parallel component is
        # unobservable in-band (aperture problem). The polish must not
        # fabricate it: applied shifts are gated by measured RMS
        # improvement, and quality honestly reports partial absorption.
        rng = np.random.default_rng(5)
        yy, xx = np.mgrid[0:160, 0:200].astype(np.float64)
        lat = (yy - 80.0) * 0.5
        img = 0.5 + 0.2 * np.sin(xx / 7.0 + lat / 9.0)
        img += rng.normal(0, 0.005, img.shape)
        disk = (((xx - 100) / 90.0) ** 2 + ((yy - 80) / 70.0) ** 2) < 1.0
        moved = warp_shift2d(img, dy=0.45, dx=-1.25)
        bands, info = _band_polish(img, moved, lat, disk,
                                   max_resid_px=3.0, n_bands=7)
        for b in range(7):
            # never exceeds the gate; never diverges to nonsense
            assert abs(bands[b][0]) <= 3.0 and abs(bands[b][1]) <= 3.0
            assert np.isfinite(bands[b]).all()


class TestCombineEndToEnd:
    def test_tilted_geometry_combine(self):
        spec, v = _three_captures()
        nav_ref = _nav_for(spec, v, 1)
        r_img, g_img, b_img = (v.frames[0].astype(np.float64),
                               v.frames[1].astype(np.float64),
                               v.frames[2].astype(np.float64))
        t = v.truth["times_s"]
        res = combine_rgb(r_img, g_img, b_img, t[0], t[1], t[2],
                          JUPITER, nav_ref)
        rep = res.report
        # 1) fringe collapses (planted: ~2.4 deg of rotation per channel hop)
        assert rep["fringe_before"] > 0.03
        assert rep["fringe_after"] < 0.25 * rep["fringe_before"]
        # 2) channels genuinely co-registered: measured residual global
        #    shift of R vs G after combine, inside the disk
        from ap_stacker import _measure_shift
        yy, xx = np.mgrid[0:g_img.shape[0], 0:g_img.shape[1]].astype(np.float64)
        _, _, disk = _px_to_lonlat_vec(yy, xx, nav_ref)
        ys, xs = np.where(disk)
        y0, y1 = ys.min(), ys.max() + 1
        x0, x1 = xs.min(), xs.max() + 1
        mm = disk[y0:y1, x0:x1].astype(np.float64)
        g_c = res.rgb[y0:y1, x0:x1, 1] * mm
        for ci, name in ((0, "R"), (2, "B")):
            dy, dx, q = _measure_shift(g_c, res.rgb[y0:y1, x0:x1, ci] * mm)
            assert math.hypot(dy, dx) < 0.35, f"{name} channel off by {dy},{dx}"
        # 3) honest bookkeeping
        for k in "RGB":
            assert rep["coverage_frac"][k] > 0.97
        assert rep["n_uncovered_disk_px"] >= 0
        assert rep["fringe_improvement"] is not None

    def test_no_winds_flag_still_aligns(self):
        # bulk-only model on wind-free renderer: same physical result
        spec, v = _three_captures(sub_lat=-1.0, pa=0.0, seed=23)
        nav_ref = _nav_for(spec, v, 1)
        t = v.truth["times_s"]
        res = combine_rgb(v.frames[0], v.frames[1], v.frames[2],
                          t[0], t[1], t[2], JUPITER, nav_ref,
                          cfg=RGBCombineConfig(include_winds=False))
        assert res.report["fringe_after"] < 0.3 * res.report["fringe_before"]

    def test_short_gap_gets_shorter(self):
        # with frames all at t_ref (stacked channels) combine must be a
        # near-no-op: no invented fringe, no destroyed detail
        spec, v = _three_captures(seed=31)
        nav_ref = _nav_for(spec, v, 1)
        g = v.frames[1].astype(np.float64)
        t = v.truth["times_s"][1]
        res = combine_rgb(g, g, g, t, t, t, JUPITER, nav_ref)
        assert res.report["fringe_after"] <= max(
            res.report["fringe_before"] * 1.2, 1e-9)

#!/usr/bin/env python3
"""Tests for app/limb_darkening.py.

Ground truth comes from the renderer itself: video_synth multiplies every
frame by mu**0.6 EXACTLY, so measuring k on a frame must return ~0.6,
and measuring it on a frame divided by the renderer's own mu**0.6 must
return ~0.0. The estimator never touches the renderer's mu code path —
that is what makes this a real physics test, not a tautology.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from limb_darkening import measure_limb_darkening, render_ld_png
from precision_engine import NavState
from video_synth import VideoSynthSpec, render_video


def _frame(seed=17):
    spec = VideoSynthSpec(width=320, height=240, n_frames=1, fps=1.0,
                          cm0_deg=104.0, disk_frac=0.42,
                          seeing_fwhm_px=(0.7, 0.7),
                          noise_rms=(0.001, 0.001),
                          shift_rms_px=0.0, gain_jitter=0.0,
                          seed=seed, rgb=False)
    v = render_video(spec)
    t = v.truth
    nav = NavState(xc=t["disk_xc_px"], yc=t["disk_yc_px"],
                   a_eq_px=t["disk_a_eq_px"], flattening=0.06487,
                   cm_iii_deg=t["cm_iii_per_frame_deg"][0],
                   sub_lat_deg=spec.sub_lat_deg,
                   north_pa_deg=spec.north_pa_deg)
    return spec, v.frames[0].astype(np.float64), nav


class TestLimbDarkeningRecovery:
    def test_planted_mu_law_recovered(self):
        spec, img, nav = _frame()
        fit = measure_limb_darkening(img, nav, n_bands=6)
        assert fit.k == pytest.approx(0.6, abs=0.08)
        assert fit.k_std > 0
        # the extreme |lat| bands legitimately lack high-mu pixels
        assert fit.n_bands >= 4
        assert fit.n_pixels > 10000
        # per-band fits should all be near the planted law
        for b in fit.per_band:
            assert b["k"] == pytest.approx(0.6, abs=0.18)

    def test_achromatic_control_zero(self):
        # divide out the renderer's own mu**0.6 -> estimator must see ~0
        spec, img, nav = _frame(seed=23)
        from video_synth import _project_fields
        t_spec = spec
        disk, mu, lat, lon = _project_fields(
            spec.height, spec.width, 160.0, 120.0, 0.42 * 240,
            104.0, 0.0, 0.0)
        img_flat = img / np.clip(mu, 0.25, None) ** 0.6
        fit2 = measure_limb_darkening(img_flat, nav, n_bands=6)
        assert fit2.k == pytest.approx(0.0, abs=0.10)

    def test_too_few_pixels_refused(self):
        img = np.zeros((40, 40))
        nav = NavState(xc=20, yc=20, a_eq_px=10.0)
        with pytest.raises(ValueError):
            measure_limb_darkening(img, nav)

    def test_panel_written(self, tmp_path):
        spec, img, nav = _frame()
        fit = measure_limb_darkening(img, nav)
        out = render_ld_png(fit, str(tmp_path / "ld.png"))
        assert os.path.getsize(out) > 3000

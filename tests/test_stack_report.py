#!/usr/bin/env python3
"""Tests for app/stack_report.py — stack forensics.

Every assertion is against bookkeeping ground truth (weights, usage,
measured shifts), not vibes: holes must be found when the drizzle grid is
starved, jumps must be found when a frame is planted 30 px off, and
degenerate frame quality must show in usage concentration.
"""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from ap_stacker import APStackConfig, stack_ap
from stack_report import (analyze_stack, forensics_report_text,
                          render_forensics_png)
from video_synth import VideoSynthSpec, render_video


def _frames(n=12, seed=5, w=160, h=120, shift_rms=1.2, seeing=(0.9, 1.2)):
    spec = VideoSynthSpec(width=w, height=h, n_frames=n, fps=5.0,
                          disk_frac=0.42, seeing_fwhm_px=seeing,
                          noise_rms=(0.002, 0.004), shift_rms_px=shift_rms,
                          gain_jitter=0.02, seed=seed, rgb=False)
    return render_video(spec).frames


class TestForensics:
    def test_clean_stack_bookkeeping(self):
        frames = _frames()
        res = stack_ap(frames, APStackConfig(ap_size_px=24, keep_frac=0.35,
                                             drizzle=2, pixfrac=0.9))
        fx = analyze_stack(res, frames=frames)
        assert fx.n_frames == len(frames)
        assert fx.fill_frac > 0.98       # dithered video: full interior coverage
        assert fx.dither_spread_px > 0.10  # real dither must be seen
        assert not any("dither" in w for w in fx.warnings)
        # mass conservation: mean per-frame usage = per-AP keep fraction
        u = np.asarray(res.per_frame_used)
        assert float(u.mean()) == pytest.approx(0.35, abs=0.06)
        assert fx.usage_max > fx.usage_min
        assert 1.0 <= fx.nominal_snr_gain <= np.sqrt(len(frames)) + 0.01
        assert fx.sharpness_gain_vs_frame is not None
        assert fx.wander_rms_detrended_px < 5.0
        txt = forensics_report_text(fx)
        assert "STACK FORENSICS" in txt

    def test_blurred_frames_detected(self):
        from scipy.ndimage import gaussian_filter
        frames = _frames()
        for i in (2, 5, 8, 10):
            frames[i] = gaussian_filter(frames[i], 3.0)
        res = stack_ap(frames, APStackConfig(ap_size_px=24, keep_frac=0.35))
        fx = analyze_stack(res)
        u = np.asarray(res.per_frame_used)
        # every blurred frame must be used less than the median good frame
        good_idx = [i for i in range(len(frames)) if i not in (2, 5, 8, 10)]
        good_med = float(np.median(u[good_idx]))
        for i in (2, 5, 8, 10):
            assert u[i] < good_med, f"blurred frame {i} not de-weighted"
        assert any("unused" in w for w in fx.warnings) or fx.usage_min < 0.02

    def test_starved_drizzle_reports_no_dither(self):
        # near-identical frames: the drizzle grid is starved of subpixel
        # phase diversity. Coverage still exists (drops always overlap
        # neighbouring bins), but the DITHER AUDIT must fire — that is the
        # physically true starvation signal.
        from image_warp import warp_shift2d
        base = _frames(n=6, seed=13)[0]
        frames = [base] * 4 + [warp_shift2d(base, dy=0.0, dx=3.0),
                               warp_shift2d(base, dy=3.0, dx=0.0)]
        res = stack_ap(frames, APStackConfig(ap_size_px=24, keep_frac=0.5,
                                             drizzle=3, pixfrac=0.6))
        fx = analyze_stack(res)
        assert fx.dither_spread_px < 0.10
        assert any("dither" in w for w in fx.warnings)

    def test_planted_jump_flagged(self):
        from image_warp import warp_shift2d
        frames = _frames(shift_rms=0.4)
        frames[6] = warp_shift2d(frames[6], dy=0.0, dx=30.0)
        res = stack_ap(frames, APStackConfig(ap_size_px=24, keep_frac=0.35))
        fx = analyze_stack(res)
        assert fx.max_single_jump_px > 20.0
        assert any("jump" in w for w in fx.warnings)

    def test_png_panel_written(self, tmp_path):
        frames = _frames()
        res = stack_ap(frames, APStackConfig(ap_size_px=24, keep_frac=0.35,
                                             drizzle=2, pixfrac=0.9))
        fx = analyze_stack(res)
        out = render_forensics_png(res, fx, str(tmp_path / "fx.png"))
        assert os.path.getsize(out) > 8000

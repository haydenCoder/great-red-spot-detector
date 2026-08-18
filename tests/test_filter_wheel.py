#!/usr/bin/env python3
"""End-to-end test for app/filter_wheel.py on real SER files.

Three mono SER "filter sessions" (R/G/B) are written to disk with true
SER timestamps 240 s apart and the planet rotated accordingly (same base
texture, same geometry, per-hop 2.42 deg = ~2.8 px at this scale). The
workflow must stack each, re-centre, derotate to the G epoch and produce
a composite whose colour fringe is far below the naive one — with every
artefact on disk and the rotation spans in the report.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from ap_stacker import APStackConfig
from filter_wheel import run_filter_wheel
from ser_io import write_ser
from video_synth import VideoSynthSpec, render_video

GAP_S = 240.0
CM_RATE = 36.29  # deg/h


def _session_ser(tmp_path, name, cm0, t0, seed=41, n=6):
    spec = VideoSynthSpec(width=192, height=144, n_frames=n, fps=10.0,
                          cm_deg_per_h=CM_RATE, cm0_deg=cm0,
                          disk_frac=0.42, seeing_fwhm_px=(0.9, 1.2),
                          noise_rms=(0.002, 0.004), shift_rms_px=0.6,
                          gain_jitter=0.02, seed=seed, rgb=False)
    v = render_video(spec)
    frames = [(np.clip(f, 0, 1) * 255).astype(np.uint8) for f in v.frames]
    times = [t0 + timedelta(seconds=i / 10.0) for i in range(n)]
    p = tmp_path / f"{name}.ser"
    write_ser(p, frames, frame_times_utc=times, observer="synth",
              instrument="testrig")
    return p


@pytest.fixture(scope="module")
def wheel_dir(tmp_path_factory):
    d = tmp_path_factory.mktemp("wheel")
    t0 = datetime(2026, 8, 1, 22, 0, 0)
    paths = {}
    cms = {}
    for i, ch in enumerate("RGB"):
        tau = (i - 1) * GAP_S                    # R: -240, G: 0, B: +240
        cm0 = 104.0 + CM_RATE * tau / 3600.0
        # same seed => same base texture; only the CM differs, exactly like
        # a real filter-wheel session against a rotating planet
        paths[ch] = _session_ser(d, ch, cm0, t0 + timedelta(seconds=tau))
    return d, paths


def test_filter_wheel_end_to_end(wheel_dir):
    d, paths = wheel_dir
    out = d / "out"
    res = run_filter_wheel(paths, out, derotate_mode="prior",
                           stack_cfg=APStackConfig(ap_size_px=24,
                                                   keep_frac=0.5))
    # artefacts
    for ch in "RGB":
        assert os.path.exists(out / f"{ch}_stack.png")
    assert os.path.exists(res.rgb_path) and os.path.getsize(res.rgb_path) > 5000
    rep = json.loads(open(res.report_json_path).read())
    # true rotation spans recovered from SER timestamps (SER carries UTC)
    assert rep["dts_s"]["R"] == pytest.approx(-GAP_S, abs=0.5)
    assert rep["dts_s"]["B"] == pytest.approx(+GAP_S, abs=0.5)
    assert rep["mid_times_utc"]["G"] is not None
    # fringe collapsed (planted: ~2.42 deg/hop)
    assert rep["fringe_after"] < 0.55 * rep["fringe_before"]
    assert rep["fringe_improvement"] > 1.8
    # coverage honest & high (identical geometry -> ~full)
    for ch in "RGB":
        assert rep["coverage_frac"][ch] > 0.8
    # report text exists and mentions rotation spans
    txt = open(res.report_text_path).read()
    assert "FILTER-WHEEL WORKFLOW" in txt
    assert "+240" in txt or "240" in txt
    # channel results carry mid-times
    for ch in res.channels:
        assert ch.t_mid_utc is not None


def test_filter_wheel_no_times_warns_not_fails(wheel_dir, tmp_path):
    d, _ = wheel_dir
    # frames passed directly with NO times: must warn, not fabricate
    from ser_io import read_video
    paths = {c: _session_ser(d, f"silent{c}", 104.0,
                             datetime(2026, 8, 1, 23, 0, 0), seed=7, n=4)
             for c in "RGB"}
    vids = {c: read_video(p) for c, p in paths.items()}
    caps = {c: [vids[c].to_float(vids[c].frame_raw(i)) for i in range(4)]
            for c in "RGB"}
    res = run_filter_wheel(caps, d / "out_notimes", derotate_mode="off",
                           stack_cfg=APStackConfig(ap_size_px=24,
                                                   keep_frac=0.5))
    assert any("no capture times" in w for w in res.warnings)
    assert os.path.exists(res.rgb_path)

#!/usr/bin/env python3
"""filter_wheel.py — the full mono-filter-wheel production workflow:
SER/AVI per filter -> per-filter APS stacks -> rotation-derotated RGB
composite, one command, all artefacts on disk.

WHY THIS MODULE EXISTS
======================
This is the exact amateur colour-imaging workflow that makes people keep
WinJUPOS installed: shoot R, then G, then B with a monochrome camera and
a filter wheel; stack each; and because the planet turned between
sequences, derotate onto a common epoch before compositing. v6.8 built
every piece (SER I/O, APS stacking, rotation derotation); v6.9 composes
them into one production function with artefacts:

  out_dir/
    R_stack.png G_stack.png B_stack.png      per-filter stacks
    rgb.png                                  derotated composite
    rgb_report.json                          combine reports (times, fringe,
                                             coverage, band residuals)
    filter_wheel_report.txt                  human summary

Also handled, because real data has it: a global re-centre of each filter
stack onto the reference-filter stack before combining (mount re-acquire
drift between filter sessions; measured with the same LK core as the
derotator), and per-filter UTC mid-times from SER frame timestamps so the
rotation model uses true epochs, not assumptions.

HONEST SCOPE: colour calibration (filter bandwidths, atmospheric
extinction) is NOT done here — the composite is gain-matched to the
reference channel on the common disk, which is registration-true but not
a photometric pipeline. For colour science use calibrated flats/darks
before stacking. Sub-Earth latitude and pole PA are caller inputs
(ephemeris layer supplies them in the CLI); with both at 0 the geometry
collapses to the axis-aligned case.
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np


@dataclass
class FilterChannelResult:
    channel: str
    path: str
    n_frames_used: int
    t_mid_utc: Optional[str]
    stack_path: str
    recentre_shift_px: Tuple[float, float]
    stack_secs: float


@dataclass
class FilterWheelResult:
    rgb_path: str
    channels: List[FilterChannelResult]
    combine_report: Dict[str, Any]
    report_json_path: str
    report_text_path: str
    nav_used: Dict[str, float]
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rgb_path": self.rgb_path,
            "channels": [c.__dict__ for c in self.channels],
            "combine_report": self.combine_report,
            "report_json_path": self.report_json_path,
            "report_text_path": self.report_text_path,
            "nav_used": self.nav_used,
            "warnings": self.warnings,
        }


def _save_png(path: Path, arr: np.ndarray) -> Path:
    from PIL import Image
    a = np.asarray(arr, dtype=np.float64)
    if a.ndim == 2:
        lo, hi = float(a.min()), float(a.max())
        u = ((a - lo) / max(hi - lo, 1e-12) * 255.0).astype(np.uint8)
        Image.fromarray(u, "L").save(str(path))
    else:
        a = a.astype(np.float64)
        lo = float(a.min())
        hi = np.percentile(a, 99.7)
        u = np.clip((a - lo) / max(hi - lo, 1e-12), 0.0, 1.0)
        Image.fromarray((u * 255.0).astype(np.uint8), "RGB").save(str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _disk_crop(stack: np.ndarray, pad: int = 6) -> np.ndarray:
    """Crop to the non-sky bounding box (isophote threshold on median+6 MAD
    of the sky, the same spirit as precision_engine.rough_disk_mask)."""
    from precision_engine import rough_disk_mask, to_mono
    m = rough_disk_mask(to_mono(stack))
    if not m.any():
        return stack
    ys, xs = np.where(m)
    y0, y1 = max(int(ys.min()) - pad, 0), min(int(ys.max()) + pad + 1,
                                              stack.shape[0])
    x0, x1 = max(int(xs.min()) - pad, 0), min(int(xs.max()) + pad + 1,
                                              stack.shape[1])
    return stack[y0:y1, x0:x1]


def run_filter_wheel(
        captures: Dict[str, Any],
        out_dir,
        *,
        planet=None,
        sub_lat_deg: float = 0.0,
        north_pa_deg: float = 0.0,
        ref_channel: str = "G",
        max_frames_per_capture: int = 0,
        derotate_mode: str = "hybrid",
        stack_cfg=None,
        combine_cfg=None,
        t_mid_override: Optional[Dict[str, datetime]] = None,
) -> FilterWheelResult:
    """Run the whole filter-wheel workflow.

    captures: {"R": path-or-frames, "G": ..., "B": ...}. Values may be
    SER/AVI paths (str/Path) OR already-loaded frame sequences with
    `t_mid_override` provided. out_dir receives every artefact.
    """
    import time as _time
    from planet_models import JUPITER as _JUPITER
    from ser_io import read_video
    from ap_stacker import APStackConfig, stack_ap, derotate_frames, _measure_shift
    from precision_engine import fit_limb_nav, to_mono
    from image_warp import warp_shift2d
    from rgb_combine import RGBCombineConfig, combine_rgb, combine_report_text

    planet = planet or _JUPITER
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    warnings: List[str] = []
    channels: Dict[str, str] = {}
    for want in ("R", "G", "B"):
        if want not in captures:
            raise ValueError(f"captures missing channel {want!r}")
        channels[want] = str(captures[want])

    # ------------------------------------------------------------ per-filter stacks
    stacks: Dict[str, np.ndarray] = {}
    tmids: Dict[str, Optional[datetime]] = {}
    ch_results: List[FilterChannelResult] = []
    for name in ("R", "G", "B"):
        t_start = _time.time()
        src = captures[name]
        times: List[Optional[datetime]] = []
        if isinstance(src, (str, Path)):
            vid = read_video(src)
            n = len(vid) if not max_frames_per_capture else min(
                len(vid), int(max_frames_per_capture))
            frames = [vid.to_float(vid.frame_raw(i)) for i in range(n)]
            times = [vid.frame_utc(i) for i in range(n)]
        else:
            frames = [np.asarray(f, dtype=np.float64) for f in src]
            times = [None] * len(frames)
        frames = [to_mono(f) if f.ndim == 3 else f for f in frames]
        if any(f.shape != frames[0].shape for f in frames):
            raise ValueError(f"channel {name}: inconsistent frame shapes")
        utc = [t for t in times if t is not None]
        t_mid = utc[len(utc) // 2] if utc else None
        if t_mid_override and name in t_mid_override:
            t_mid = t_mid_override[name]
        tmids[name] = t_mid
        # within-capture derotation (long captures): pure model prior by
        # default — measurement mode lives in observatory_pipeline already
        if t_mid is not None and utc and derotate_mode != "off":
            t0 = utc[0]
            dts = [(t - t0).total_seconds() if t is not None else 0.0
                   for t in times]
            frames, _info = derotate_frames(
                frames, dt_s_per_frame=dts, planet=planet,
                mode=("prior" if derotate_mode == "hybrid" else derotate_mode),
                sub_lat_deg=sub_lat_deg, north_pa_deg=north_pa_deg)
        cfg = stack_cfg or APStackConfig(ap_size_px=32, keep_frac=0.35)
        res = stack_ap(frames, cfg)
        stacks[name] = np.asarray(res.stack, dtype=np.float64)
        ch_results.append(FilterChannelResult(
            channel=name, path=str(src), n_frames_used=len(frames),
            t_mid_utc=(t_mid.isoformat() if t_mid else None),
            stack_path="", recentre_shift_px=(0.0, 0.0),
            stack_secs=_time.time() - t_start))

    # ------------------------------------------------------------ shapes & re-centre
    ref = ref_channel.upper()
    hmin = min(stacks[c].shape[0] for c in "RGB")
    wmin = min(stacks[c].shape[1] for c in "RGB")
    if any(stacks[c].shape != (hmin, wmin) for c in "RGB"):
        warnings.append("channel stack sizes differ — centre-cropped to "
                        f"{wmin}x{hmin}")
        for c in "RGB":
            s = stacks[c]
            y0 = (s.shape[0] - hmin) // 2
            x0 = (s.shape[1] - wmin) // 2
            stacks[c] = s[y0:y0 + hmin, x0:x0 + wmin]

    ref_stack = stacks[ref]
    for c in "RGB":
        if c == ref:
            continue
        dy, dx, q = _measure_shift(ref_stack, stacks[c], refine=True)
        if abs(dy) > 0.05 or abs(dx) > 0.05:
            stacks[c] = warp_shift2d(stacks[c], dy=dy, dx=dx)
            ch = next(r for r in ch_results if r.channel == c)
            ch.recentre_shift_px = (float(dy), float(dx))
            warnings.append(
                f"channel {c} re-centred by ({dy:+.2f}, {dx:+.2f}) px onto "
                f"{ref} (mount/re-acquire drift between filter sessions)")

    # ------------------------------------------------------------ nav + times
    nav = fit_limb_nav(ref_stack, cm_iii_deg=0.0,
                       distance_au=planet.default_distance_au,
                       north_pa_deg=north_pa_deg)
    nav.flattening = planet.flattening
    nav.sub_lat_deg = float(sub_lat_deg)
    nav.north_pa_deg = float(north_pa_deg)

    t_ref = tmids[ref]
    epoch_s = {c: (tmids[c],) for c in "RGB"}
    if any(tmids[c] is None for c in "RGB"):
        if ref_channel.upper() not in tmids or tmids[ref] is None:
            # no timing information at all: fall back to zero-gaps and SAY SO
            warnings.append("no capture times available — combined with "
                            "dt=0 for every channel (no true rotation "
                            "compensation possible)")
        dts = {c: 0.0 for c in "RGB"}
        t_ref_s = 0.0
    else:
        t_ref_s = tmids[ref].timestamp()
        dts = {c: (tmids[c].timestamp() if tmids[c] is not None else t_ref_s)
               for c in "RGB"}
        for c in "RGB":
            if tmids[c] is None:
                dts[c] = t_ref_s
                warnings.append(f"channel {c}: no time — assumed at {ref}")

    cc = combine_cfg or RGBCombineConfig()
    res = combine_rgb(stacks["R"], stacks["G"], stacks["B"],
                      dts["R"], dts["G"], dts["B"], planet, nav,
                      t_ref_s=t_ref_s, cfg=cc)

    # ------------------------------------------------------------ artefacts
    ch_paths = {}
    for c in "RGB":
        p = _save_png(out_dir / f"{c}_stack.png", _disk_crop(stacks[c]))
        ch_paths[c] = str(p)
        next(r for r in ch_results if r.channel == c).stack_path = str(p)
    rgb_path = _save_png(out_dir / "rgb.png", _disk_crop(res.rgb))
    report = dict(res.report)
    report["recentre_shifts_px"] = {
        r.channel: list(r.recentre_shift_px) for r in ch_results}
    report["filter_wheel_warnings"] = warnings
    report["mid_times_utc"] = {c: (tmids[c].isoformat() if tmids[c] else None)
                               for c in "RGB"}
    report["derotate_mode_within_capture"] = derotate_mode
    report_json = out_dir / "rgb_report.json"
    report_json.write_text(json.dumps(report, indent=2, default=str))
    txt = out_dir / "filter_wheel_report.txt"
    txt.write_text(filter_wheel_report_text(
        FilterWheelResult(rgb_path=str(rgb_path), channels=ch_results,
                          combine_report=report,
                          report_json_path=str(report_json),
                          report_text_path="",
                          nav_used={"xc": nav.xc, "yc": nav.yc,
                                    "a_eq_px": nav.a_eq_px},
                          warnings=warnings)))
    return FilterWheelResult(
        rgb_path=str(rgb_path), channels=ch_results, combine_report=report,
        report_json_path=str(report_json), report_text_path=str(txt),
        nav_used={"xc": float(nav.xc), "yc": float(nav.yc),
                  "a_eq_px": float(nav.a_eq_px)},
        warnings=warnings)


def filter_wheel_report_text(res: FilterWheelResult) -> str:
    lines = ["=" * 70,
             "FILTER-WHEEL WORKFLOW — mono captures to derotated RGB",
             "=" * 70]
    for ch in res.channels:
        lines.append(f"  [{ch.channel}] {ch.n_frames_used} frames "
                     f"| mid {ch.t_mid_utc or '??'} | "
                     f"re-centre ({ch.recentre_shift_px[0]:+.2f}, "
                     f"{ch.recentre_shift_px[1]:+.2f}) px | "
                     f"{ch.stack_secs:.1f}s")
    dts = res.combine_report.get("dts_s", {})
    lines.append(f"rotation compensation spans (R/G/B): "
                 f"{dts.get('R', 0):+.0f} / {dts.get('G', 0):+.0f} / "
                 f"{dts.get('B', 0):+.0f} s")
    fb = res.combine_report.get("fringe_before")
    fa = res.combine_report.get("fringe_after")
    if fb and fa:
        lines.append(f"colour fringe: {fb:.4f} -> {fa:.4f} "
                     f"({fb / max(fa, 1e-12):.1f}x better)")
    cov = res.combine_report.get("coverage_frac", {})
    if cov:
        lines.append(f"coverage (R/G/B): "
                     + " / ".join(f"{cov.get(c, 0) * 100:.1f}%" for c in "RGB"))
    if res.warnings:
        lines.append("WARNINGS:")
        for w in res.warnings:
            lines.append(f"  - {w}")
    lines.append("artefacts: R/G/B stacks, rgb.png, rgb_report.json")
    lines.append("scope: gain-matched to the reference channel; this is "
                 "registration-true, not a photometric pipeline.")
    return "\n".join(lines)


__all__ = ["FilterWheelResult", "FilterChannelResult", "run_filter_wheel",
           "filter_wheel_report_text"]

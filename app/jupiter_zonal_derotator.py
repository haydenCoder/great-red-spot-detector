#!/usr/bin/env python3
"""
Jupiter zonal-shear-aware derotator.

WHAT THIS IS
============
A derotator that, unlike the existing `win_jupos_derotator` (which
applies a single global rotation about the disk centre), applies a
*per-latitude* rotation. Each latitude band is rotated by the
zonal-wind-residual rate at that latitude, so the output stack has
zero *zonal-shear* smearing.

This is the missing piece between WinJUPOS (single rotation) and a
full optical-flow warp (per-pixel). It is fast (one FFT per latitude
row) and physically motivated (the zonal-wind residual profile).

ALGORITHM (v6.8 — consolidated on the tested engine)
====================================================
  1) `ap_stacker.derotate_frames` does the physics: limb nav, AP grid,
     prior-seeded sub-pixel AP tracking (_measure_shift), robust
     per-latitude fit with a physical bound, spatial-domain per-row
     resampling with the correct (π/180)r cosφ longitude chord and
     correct content-shift sign. All regression-pinned in
     tests/test_video_jupiter.py.
  2) This wrapper keeps the reporting metric of the original module
     (1D zonal-profile lat cross-correlation vs the reference frame)
     and the same public API.

The pre-v6.8 internals (a/90 plate scale → 1.57× under-shift at the
equator; bare phase-corr tracker that saturated past ap_half; per-row
FFT phase ramps with circulant wrap + limb ringing) were retired after
the v6.8 derotation regressions — see docs/ESSAY.md.

WHAT IT IS NOT
==============
- Not an "infinite-tensor" anything. Just standard image-registration
  math applied with a Jupiter-specific prior.
- Not a "HolyCNN" anything. No neural network in the loop.
- Not a per-pixel flow warp. We use per-row shifts, which is the
  WinJUPOS way extended to per-latitude.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import numpy as np

from verbose_log import CONSOLE
from jupiter_zonal_stacker import (
    _zonal_profile,
    _zonal_profile_shift,
)


@dataclass
class JupiterZonalDerotatorResult:
    n_frames: int
    mean_per_row_shift_px: float
    zonal_profile_shift_median: float
    elapsed_s: float
    output_path: str
    notes: List[str] = field(default_factory=list)
    drift_summary: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return asdict(self)


def run_jupiter_zonal_derotate(
    frames: List[np.ndarray],
    out_dir: Path,
    *,
    cm_iii_per_frame: Optional[Sequence[float]] = None,
    dt_s_per_frame: Optional[Sequence[float]] = None,
    sub_lat_deg: float = 0.0,
    north_pa_deg: float = 0.0,
    mode: str = "measurement",  # "measurement" or "prior"
    save: bool = True,
    ref_index: Optional[int] = None,
) -> JupiterZonalDerotatorResult:
    """
    Derotate a list of frames with per-latitude shifts.

    Two modes:
      - "measurement" (default): use the AP-grid per-AP measurements
        to fit a per-latitude shift, then derotate per-row. This is
        the *honest* mode: it uses the data, not a prior. When the
        per-AP fits are accurate (typical amateur frames), this is
        strictly better than the single-rotation winjupos.
      - "prior": use the zonal-wind-residual *prior* to predict the
        per-row shift without measuring. Useful when the AP tracker
        fails (very low SNR, very few APs). Less accurate than
        measurement on well-tracked frames.

    The output is a per-row derotated stack, plus the per-row shift
    history and the latitudinal profile match residual.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    if not frames:
        raise ValueError("run_jupiter_zonal_derotate: empty frame list")
    h, w = frames[0].shape
    n_frames = len(frames)
    if cm_iii_per_frame is None:
        cm_iii_per_frame = [0.0] * n_frames
    if dt_s_per_frame is None:
        dt_s_per_frame = [0.0] * n_frames
    if len(cm_iii_per_frame) != n_frames or len(dt_s_per_frame) != n_frames:
        raise ValueError("cm_iii_per_frame / dt_s_per_frame must match n_frames")
    CONSOLE.info(
        f"JUPITER-ZONAL-DEROT: {n_frames} frames {w}x{h}, mode={mode}"
    )
    # ---------------------------------------------------------------
    # v6.8: delegate the derotation physics to the tested engine
    # (ap_stacker.derotate_frames): physical (pi/180)r cos(phi) chord
    # px/deg, correct content-shift signs, prior-seeded AP tracking and
    # spatial-domain per-row resampling. This module previously rolled its
    # own: a/90 plate scale (under-shifted 1.57x at the equator — the
    # measured v6.8 bug), a bare phase-corr AP tracker with NO prior
    # windowing (saturated past ap_half under rotation), and per-row FFT
    # phase ramps (circulant wrap + limb Gibbs ringing). Same public API
    # and result fields; the physics underneath is the verified one now.
    # ---------------------------------------------------------------
    from ap_stacker import derotate_frames
    from planet_models import JUPITER

    frames_fixed: List[np.ndarray] = []
    for frame in frames:
        if frame.shape != (h, w):
            fh, fw = frame.shape
            y0 = max(0, (fh - h) // 2); x0 = max(0, (fw - w) // 2)
            frame = frame[y0:y0 + h, x0:x0 + w]
        frames_fixed.append(np.asarray(frame, dtype=np.float64))
    dts = [float(dt_s_per_frame[k]) for k in range(n_frames)]
    der_mode = mode if mode in ("prior", "hybrid", "measurement") else "measurement"
    derotated, dinfo = derotate_frames(
        frames_fixed, dt_s_per_frame=dts, mode=der_mode, planet=JUPITER,
        ref_index=(ref_index if ref_index is not None else -1),
    )
    ref = frames_fixed[int(dinfo.get("ref_index") or 0)]

    # Reporting-only metric kept from the original module: median
    # latitudinal (y) zonal-profile match residual vs the reference frame.
    ref_lat_centres, ref_profile = _zonal_profile(
        ref, cm_iii_per_frame[int(dinfo.get("ref_index") or 0)], 5.2,
        sub_lat_deg=sub_lat_deg, north_pa_deg=north_pa_deg,
    )
    per_frame_ly_shifts: List[float] = []
    for k, frame in enumerate(frames_fixed):
        lat_centres, profile = _zonal_profile(
            np.asarray(frame, dtype=np.float64), cm_iii_per_frame[k], 5.2,
            sub_lat_deg=sub_lat_deg, north_pa_deg=north_pa_deg,
        )
        prof_shift = _zonal_profile_shift(ref_profile, profile)
        per_frame_ly_shifts.append(prof_shift)
    accumulated = np.zeros((h, w), dtype=np.float64)
    weights = np.zeros((h, w), dtype=np.float64)
    for k, frame in enumerate(derotated):
        w_k = 1.0
        accumulated += frame * w_k
        weights += np.full_like(frame, w_k)
    stacked = accumulated / np.maximum(weights, 1e-9)
    out_path = out_dir / f"stacked_derotated_zonal_{mode}.png"
    if save:
        try:
            from PIL import Image
            u8 = (np.clip(stacked / max(stacked.max(), 1e-9), 0, 1) * 255).astype(np.uint8)
            Image.fromarray(u8, "L").save(out_path, optimize=False)
        except Exception as e:
            CONSOLE.warn(f"JUPITER-ZONAL-DEROT: PNG save failed: {e}")
            out_path = out_dir / f"stacked_derotated_zonal_{mode}.npy"
            np.save(out_path, stacked)
    elapsed = time.time() - t0
    mean_per_row = float(dinfo.get("median_per_row_shift_px") or 0.0)
    med_lat_shift = float(np.median(per_frame_ly_shifts)) if per_frame_ly_shifts else 0.0
    CONSOLE.ok(
        f"JUPITER-ZONAL-DEROT done: {n_frames} frames, mode={der_mode}, "
        f"median per-row |dx| {mean_per_row:.2f}px, "
        f"profile lat shift med {med_lat_shift:+.2f}°, "
        f"{elapsed:.1f}s"
    )
    return JupiterZonalDerotatorResult(
        n_frames=n_frames,
        mean_per_row_shift_px=mean_per_row,
        zonal_profile_shift_median=med_lat_shift,
        elapsed_s=float(elapsed),
        output_path=str(out_path),
        notes=[
            f"Jupiter-specialized, mode={der_mode}: "
            "per-row derotation via ap_stacker.derotate_frames (v6.8 physics: "
            "chord px/deg, prior-seeded AP tracking, spatial resample)",
            "Zonal-profile match (1D lat cross-corr) reports latitudinal bias",
        ],
        drift_summary={
            "derotate_ref_index": int(dinfo.get("ref_index") or 0),
            "median_per_row_shift_px": float(dinfo.get("median_per_row_shift_px") or 0.0),
            "max_per_row_shift_px": float(dinfo.get("max_per_row_shift_px") or 0.0),
            "per_frame_zonal_profile_shift_deg": [
                float(s) for s in per_frame_ly_shifts
            ],
        },
    )


__all__ = ["run_jupiter_zonal_derotate", "JupiterZonalDerotatorResult"]

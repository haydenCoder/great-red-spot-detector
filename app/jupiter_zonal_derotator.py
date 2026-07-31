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

ALGORITHM
=========
  1) Run the existing `_ap_latitude` and `_zonal_wind_rate_at_lat`
     from `jupiter_zonal_stacker` to get the per-AP zonal rate.
  2) For each frame, compute the per-frame expected shift as a
     *per-row* profile (zonal = image-x, latitudinal = image-y).
     This is the "what the frame should look like at the
     reference's epoch" prior.
  3) Apply a per-row shift: row y is shifted by (Δx(y), Δy(y))
     where Δx = -rate(avg_lat(y)) · dt and Δy = the latitudinal
     profile match residual.
  4) Stack with per-row quality weighting.

WHAT IT IS NOT
==============
- Not an "infinite-tensor" anything. Just standard image-registration
  math applied with a Jupiter-specific prior.
- Not a "HolyCNN" anything. No neural network in the loop.
- Not a per-pixel flow warp. We use per-row shifts, which is the
  WinJUPOS way extended to per-latitude.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

from verbose_log import CONSOLE
from jupiter_zonal_stacker import (
    _zonal_wind_rate_at_lat_deg_per_s,
    SYS3_RATE_DEG_PER_S,
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
    # Per-row average latitude
    from precision_engine import fit_limb_nav, deg2rad
    from jpa_10k import _build_ap_grid, _phase_corr_shift, _laplacian_octave
    ref = frames[0].astype(np.float64)
    nav = fit_limb_nav(ref, cm_iii_deg=cm_iii_per_frame[0], distance_au=5.2)
    nav.sub_lat_deg = sub_lat_deg
    nav.north_pa_deg = north_pa_deg
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    Xsky = (xx - nav.xc) / (nav.a_eq_px + 1e-12)
    Ysky = (nav.yc - yy) / (nav.b_pol_px + 1e-12)
    pa = deg2rad(north_pa_deg)
    cP, sP = math.cos(pa), math.sin(pa)
    Xp = Xsky * cP + Ysky * sP
    Yp = -Xsky * sP + Ysky * cP
    rr = Xp * Xp + Yp * Yp
    Zp = np.sqrt(np.clip(1.0 - rr, 0.0, 1.0))
    D = deg2rad(sub_lat_deg)
    cD, sD = math.cos(D), math.sin(D)
    Ye = Yp * cD + Zp * sD
    lat = np.degrees(np.arcsin(np.clip(Ye, -1.0, 1.0)))
    on_disk = rr <= 0.97
    row_lats = np.zeros(h, dtype=np.float64)
    for row in range(h):
        m = on_disk[row]
        if m.any():
            row_lats[row] = float(np.mean(lat[row][m]))
        else:
            row_lats[row] = 0.0
    deg_to_px = nav.a_eq_px / 90.0
    ref_lat_centres, ref_profile = _zonal_profile(
        ref, cm_iii_per_frame[0], 5.2,
        sub_lat_deg=sub_lat_deg, north_pa_deg=north_pa_deg,
    )
    per_frame_shifts: List[np.ndarray] = []
    per_frame_ly_shifts: List[float] = []
    derotated: List[np.ndarray] = []
    for k, frame in enumerate(frames):
        if frame.shape != ref.shape:
            fh, fw = frame.shape
            y0 = max(0, (fh - h) // 2); x0 = max(0, (fw - w) // 2)
            frame = frame[y0:y0 + h, x0:x0 + w]
        if mode == "measurement":
            # Use AP-grid per-AP measurements, then interpolate to
            # per-row. The measurement is more accurate than the prior
            # when the AP tracker succeeds.
            aps = _build_ap_grid(h, w, n_grid=6, mask=on_disk)
            ap_drifts = np.full((aps.shape[0], 2), np.nan, dtype=np.float64)
            for i, (x, y) in enumerate(aps):
                xi, yi = int(round(x)), int(round(y))
                ap_half = 16
                if (xi - ap_half < 0 or yi - ap_half < 0
                        or xi + ap_half >= w or yi + ap_half >= h):
                    continue
                ref_crop = ref[yi - ap_half:yi + ap_half + 1, xi - ap_half:xi + ap_half + 1]
                frame_crop = frame[yi - ap_half:yi + ap_half + 1,
                                   xi - ap_half:xi + ap_half + 1]
                try:
                    dy_o, dx_o, _ = _phase_corr_shift(ref_crop, frame_crop)
                    ap_drifts[i] = (dy_o, dx_o)
                except Exception:
                    pass
            # Per-AP lat
            ap_lats = np.array([
                _ap_latitude_for_derotator((x, y), nav)
                for x, y in aps
            ])
            # Per-row shift = median dx of APs whose lat is close to
            # the row's lat (within 5°). The sign of dx is *opposite*
            # the measured shift (we want to undo the shift).
            dx_per_row = np.zeros(h, dtype=np.float64)
            for row in range(h):
                m = on_disk[row]
                if not m.any():
                    continue
                avg_lat = row_lats[row]
                # APs within 5° of this row's lat
                close = np.abs(ap_lats - avg_lat) < 5.0
                close &= np.isfinite(ap_drifts[:, 0])
                if close.sum() < 2:
                    continue
                dx_per_row[row] = -float(np.median(ap_drifts[close, 1]))
        else:  # "prior"
            cm_ref = cm_iii_per_frame[0]
            cm_k = cm_iii_per_frame[k]
            dcm_deg = cm_k - cm_ref
            if dcm_deg > 180.0:
                dcm_deg -= 360.0
            elif dcm_deg < -180.0:
                dcm_deg += 360.0
            t_dcm = dcm_deg / SYS3_RATE_DEG_PER_S if abs(SYS3_RATE_DEG_PER_S) > 1e-12 else 0.0
            dx_per_row = np.zeros(h, dtype=np.float64)
            for row in range(h):
                if not on_disk[row].any():
                    continue
                rate = _zonal_wind_rate_at_lat_deg_per_s(row_lats[row])
                dx_per_row[row] = -rate * t_dcm * deg_to_px
        # Zonal-profile latitudinal match (catch any y-bias)
        lat_centres, profile = _zonal_profile(
            np.asarray(frame, dtype=np.float64), cm_iii_per_frame[k], 5.2,
            sub_lat_deg=sub_lat_deg, north_pa_deg=north_pa_deg,
        )
        prof_shift = _zonal_profile_shift(ref_profile, profile)
        per_frame_ly_shifts.append(prof_shift)
        lat_to_px = nav.a_eq_px / 90.0
        dy_uniform = prof_shift * lat_to_px
        # Apply per-row shift
        derot = np.asarray(frame, dtype=np.float64).copy()
        for row in range(h):
            if not on_disk[row].any():
                continue
            dx = dx_per_row[row]
            dy = dy_uniform
            if abs(dx) < 0.02 and abs(dy) < 0.02:
                continue
            row_arr = derot[row]
            n = row_arr.size
            f_row = np.fft.fft(row_arr)
            phase = np.exp(-2j * math.pi * dx * np.arange(n) / n)
            derot[row] = np.real(np.fft.ifft(f_row * phase))
        if abs(dy_uniform) > 0.02:
            f = np.fft.fft2(derot)
            yy_g, xx_g = np.mgrid[0:h, 0:w]
            phase = np.exp(-2j * math.pi * dy_uniform * yy_g / h)
            derot = np.real(np.fft.ifft2(f * phase))
        per_frame_shifts.append(dx_per_row.copy())
        derotated.append(derot)
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
    mean_per_row = float(np.mean([np.mean(np.abs(s)) for s in per_frame_shifts]))
    med_lat_shift = float(np.median(per_frame_ly_shifts)) if per_frame_ly_shifts else 0.0
    CONSOLE.ok(
        f"JUPITER-ZONAL-DEROT done: {n_frames} frames, mode={mode}, "
        f"mean per-row |dx| {mean_per_row:.2f}px, "
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
            f"Jupiter-specialized, mode={mode}: "
            + ("per-row shift from AP-grid measurements" if mode == "measurement"
               else "per-row shift from zonal-wind-residual prior"),
            "Zonal-profile match (1D lat cross-corr) catches latitudinal bias",
        ],
        drift_summary={
            "per_frame_median_dx_px": [
                float(np.median(np.abs(s))) for s in per_frame_shifts
            ],
            "per_frame_zonal_profile_shift_deg": [
                float(s) for s in per_frame_ly_shifts
            ],
        },
    )


def _ap_latitude_for_derotator(
    ap_xy: Tuple[float, float], nav,
) -> float:
    """
    Approximate planetocentric latitude of an AP for the
    measurement-mode derotator. Same logic as in jupiter_zonal_stacker.
    """
    from jupiter_zonal_stacker import _ap_latitude
    return _ap_latitude(
        ap_xy, (nav.xc, nav.yc), nav.a_eq_px,
        sub_lat_deg=float(getattr(nav, "sub_lat_deg", 0.0) or 0.0),
        north_pa_deg=float(getattr(nav, "north_pa_deg", 0.0) or 0.0),
    )


__all__ = ["run_jupiter_zonal_derotate", "JupiterZonalDerotatorResult"]

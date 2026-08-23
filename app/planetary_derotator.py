#!/usr/bin/env python3
"""
planetary_derotator.py — planet-generalised per-latitude derotator.

WHAT THIS IS
============
A derotator that, for any `planet_models.Planet`, undoes the latitude-dependent
rotation between frames so a stack does not smear. It fixes the two weaknesses
of the old Jupiter derotator (`jupiter_zonal_derotator`):

  1) its "measurement" mode used a single-octave phase correlation with NO
     prior bootstrap, so it could not lock once the bulk rotation had swept a
     feature past the AP window. We use the hybrid prior+measurement tracker
     from planetary_stacker (re-centre the crop by the planet-model expected
     drift, then correlate the small residual).
  2) it was hard-coded to Jupiter. This module takes a Planet (Jupiter, Saturn,
     Neptune, Uranus, Mars built in).

THREE MODES
===========
  "measurement" (default) — measure per-AP drift with the prior-bootstrapped
                tracker, fit dx(|lat|), warp per-row. The accurate mode.
  "prior"       — derotate using ONLY the planet model (bulk rotation + zonal
                wind), no image tracking. The WinJUPOS "I know the rotation,
                just undo it" path, generalised. Use when SNR is too low to
                track (this is a genuinely new capability vs the old module).
  "hybrid"      — measurement, but blend each latitude bin's measured drift
                toward the planet-model prior where SNR is low (regulariser).

It reuses the tested internals of planetary_stacker (tracker, fit, per-row
warp) rather than duplicating them.
"""
from __future__ import annotations

import math
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

import numpy as np

from verbose_log import CONSOLE
from planet_models import Planet, JUPITER
from precision_engine import fit_limb_nav, to_mono
from jpa_10k import _build_ap_grid
from planetary_stacker import (
    _per_pixel_lat, _ap_latitudes, _track_ap_planetary,
    _per_ap_expected_dx, _per_ap_expected_dx_lon, _frame_dt,
    select_reference_index, _laplacian_var,
    fit_dx_vs_latitude, per_row_warp,
)

# Light fixed regularisation of the measured per-latitude drift toward the
# planet-model prior in "measurement" mode. See run_planetary_derotate for the
# measured justification; 0.25 damps tracker/bin noise while leaving bounded
# zonal-shear corrections intact.
_MEAS_PRIOR_BLEND = 0.25


@dataclass
class PlanetaryDerotatorResult:
    n_frames: int
    planet: str
    mode: str
    reference_index: int
    mean_per_row_shift_px: float
    elapsed_s: float
    output_path: str
    notes: List[str] = field(default_factory=list)
    drift_summary: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> Dict:
        return asdict(self)


def _prior_dx_per_bin(planet: Planet, dt_s: float, deg_to_px: float, n_bins: int = 11) -> np.ndarray:
    """Pure-model dx(|lat|) curve (the 'prior' mode). Returns the apply-shift
    (already negated) per |lat| bin, correctly scaled by the (π/180)r cosφ
    longitude chord (`lon_drift_px`)."""
    centres = (np.arange(n_bins) + 0.5) * (90.0 / n_bins)
    return np.array([
        -_per_ap_expected_dx_lon(planet, float(c), dt_s, deg_to_px * 90.0) for c in centres
    ])


def run_planetary_derotate(
    frames: List[np.ndarray],
    out_dir: Path,
    *,
    planet: Planet = JUPITER,
    n_grid: int = 6,
    ap_half: int = 16,
    cm_iii_per_frame: Optional[Sequence[float]] = None,
    dt_s_per_frame: Optional[Sequence[float]] = None,
    sub_lat_deg: float = 0.0,
    north_pa_deg: float = 0.0,
    mode: str = "measurement",   # measurement | prior | hybrid
    reference: str = "auto",
    save: bool = True,
) -> PlanetaryDerotatorResult:
    """Derotate + stack frames of any Planet with a per-latitude warp."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    if not frames:
        raise ValueError("run_planetary_derotate: empty frame list")
    n_frames = len(frames)
    mono = [to_mono(f) for f in frames]
    h, w = mono[0].shape
    if cm_iii_per_frame is None:
        cm_iii_per_frame = [0.0] * n_frames
    if dt_s_per_frame is None:
        dt_s_per_frame = [0.0] * n_frames

    ref_idx = select_reference_index(mono) if reference == "auto" else 0
    ref = mono[ref_idx]
    CONSOLE.info(
        f"PLANETARY-DEROT: {n_frames} frames {w}x{h}, planet={planet.name}, "
        f"mode={mode}, ref={ref_idx}"
    )
    nav = fit_limb_nav(ref, cm_iii_deg=float(cm_iii_per_frame[ref_idx]),
                       distance_au=planet.default_distance_au)
    nav.sub_lat_deg = sub_lat_deg
    nav.north_pa_deg = north_pa_deg
    lat_map, on_disk = _per_pixel_lat(nav, h, w, sub_lat_deg, north_pa_deg)
    row_lats = np.array([
        float(np.mean(lat_map[r][on_disk[r]])) if on_disk[r].any() else 0.0
        for r in range(h)
    ])
    deg_to_px = nav.a_eq_px / 90.0

    thr = float(np.percentile(ref, 30.0))
    aps = _build_ap_grid(h, w, n_grid=n_grid, mask=ref > thr)
    ap_lats = _ap_latitudes(aps, nav, sub_lat_deg, north_pa_deg)
    n_aps = aps.shape[0]

    accumulated = np.zeros((h, w), dtype=np.float64)
    weights = np.zeros((h, w), dtype=np.float64)
    ref_q = max(_laplacian_var(ref, on_disk), 1e-3)
    accumulated += ref * ref_q
    weights += np.full((h, w), ref_q)
    per_frame_med_shift: List[float] = []

    for k, frame in enumerate(mono):
        if k == ref_idx:
            per_frame_med_shift.append(0.0)
            continue
        dt_k = _frame_dt(planet, k, ref_idx, cm_iii_per_frame, dt_s_per_frame)

        if mode == "prior":
            dx_bins = _prior_dx_per_bin(planet, dt_k, deg_to_px)
            dy_g = 0.0
        else:
            drifts = np.full((n_aps, 2), np.nan, dtype=np.float64)
            snrs = np.zeros(n_aps, dtype=np.float64)
            for i, (ax, ay) in enumerate(aps):
                exp_dx = _per_ap_expected_dx_lon(planet, float(ap_lats[i]), dt_k, deg_to_px * 90.0)
                tdy, tdx, snr = _track_ap_planetary(ref, frame, (ax, ay), ap_half, expected_dx=exp_dx)
                drifts[i] = (tdy, tdx)
                snrs[i] = snr
            dx_bins, dy_g = fit_dx_vs_latitude(
                ap_lats, drifts, snrs, planet, dt_s=dt_k, deg_to_px=deg_to_px,
            )
            # Regularise the measured dx(|lat|) curve toward the planet-model
            # prior. The bulk-rotation prior is known to high precision from the
            # CM angles, and the tracker only needs to correct BOUNDED zonal
            # shear (a few px at most), so a light pull toward the prior damps
            # per-bin tracker/rounding noise without attenuating real wind
            # signal. Measured on the 8-frame, 3 deg/frame rotating-video
            # benchmark: no prior blend (alpha=0) accumulated ~1-1.7 px/bin of
            # noise and scored 0.68 correlation (WORSE than doing nothing at
            # 0.76), while a 0.25 blend scored 0.91 and a 0.5 blend 0.93 --
            # with no regression on small 0.5 deg/frame drifts. hybrid mode
            # additionally makes the blend weight SNR-dependent.
            prior = _prior_dx_per_bin(planet, dt_k, deg_to_px, dx_bins.size)
            if mode == "hybrid":
                mean_snr = float(np.nanmean(snrs[np.isfinite(drifts[:, 0])])) if np.isfinite(drifts[:, 0]).any() else 0.0
                w_meas = min(1.0, mean_snr / 2.0)   # 0 at no signal → pure prior
            else:  # measurement
                w_meas = 1.0 - _MEAS_PRIOR_BLEND
            dx_bins = w_meas * dx_bins + (1.0 - w_meas) * prior

        shifted = per_row_warp(frame, dx_bins, dy_g, on_disk, row_lats)
        centres = (np.arange(dx_bins.size) + 0.5) * (90.0 / dx_bins.size)
        per_frame_med_shift.append(float(np.median(np.abs(np.interp(np.abs(row_lats), centres, dx_bins)))))

        qk = max(_laplacian_var(frame, on_disk), 1e-3)
        accumulated += shifted * qk
        weights += np.full((h, w), qk)

    stacked = accumulated / np.maximum(weights, 1e-9)
    out_path = out_dir / f"derotated_planetary_{planet.name.lower()}_{mode}.png"
    if save:
        try:
            from PIL import Image
            u8 = (np.clip(stacked / max(stacked.max(), 1e-9), 0, 1) * 255).astype(np.uint8)
            Image.fromarray(u8, "L").save(out_path, optimize=False)
        except Exception as e:
            CONSOLE.warn(f"PLANETARY-DEROT: PNG save failed: {e}")
            out_path = out_dir / f"derotated_planetary_{planet.name.lower()}_{mode}.npy"
            np.save(out_path, stacked)

    elapsed = time.time() - t0
    mean_shift = float(np.mean(per_frame_med_shift)) if per_frame_med_shift else 0.0
    CONSOLE.ok(
        f"PLANETARY-DEROT done: {n_frames} frames, planet={planet.name}, "
        f"mode={mode}, mean per-row |dx| {mean_shift:.2f}px, {elapsed:.1f}s"
    )
    result = PlanetaryDerotatorResult(
        n_frames=n_frames,
        planet=planet.name,
        mode=mode,
        reference_index=int(ref_idx),
        mean_per_row_shift_px=mean_shift,
        elapsed_s=float(elapsed),
        output_path=str(out_path),
        notes=[
            f"Planet-generalised per-latitude derotator: {planet.name}",
            f"mode={mode}: " + {
                "measurement": "AP-tracked per-lat drift (prior-bootstrapped tracker)",
                "prior": "pure planet-model drift (no image tracking)",
                "hybrid": "measurement regularised toward planet-model prior by SNR",
            }.get(mode, mode),
        ],
        drift_summary={"per_frame_median_abs_dx_px": [float(v) for v in per_frame_med_shift]},
    )
    if save:
        write_derotator_report(result, out_dir)
    return result


def derotator_report_text(res: "PlanetaryDerotatorResult") -> str:
    """Human-readable one-page report card for a derotator run."""
    bar = "=" * 60
    shifts = res.drift_summary.get("per_frame_median_abs_dx_px", [])
    lines = [
        bar,
        f"PLANETARY DEROTATOR REPORT  -  {res.planet}  ({res.n_frames} frames)",
        bar,
        f"mode               : {res.mode}",
        f"reference frame    : #{res.reference_index}",
        f"mean per-row |dx|  : {res.mean_per_row_shift_px:.2f} px",
        f"elapsed            : {res.elapsed_s:.1f} s",
        f"output             : {res.output_path}",
        "",
        "per-frame median |dx| (px):",
    ]
    if shifts:
        for i, s in enumerate(shifts):
            tag = "  (reference)" if i == res.reference_index else ""
            lines.append(f"  frame {i:3d}: {s:7.2f}{tag}")
    else:
        lines.append("  (none)")
    lines.append("")
    lines.append("notes:")
    for n in res.notes:
        lines.append(f"  - {n}")
    lines.append(bar)
    return "\n".join(lines)


def write_derotator_report(res: "PlanetaryDerotatorResult", out_dir: Path) -> Path:
    out_dir = Path(out_dir)
    p = out_dir / "derotator_report.txt"
    try:
        p.write_text(derotator_report_text(res), encoding="utf-8")
    except Exception as e:
        CONSOLE.warn(f"PLANETARY-DEROT: report write failed: {e}")
    return p


__all__ = [
    "run_planetary_derotate",
    "PlanetaryDerotatorResult",
    "derotator_report_text",
    "write_derotator_report",
]

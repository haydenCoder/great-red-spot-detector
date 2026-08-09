#!/usr/bin/env python3
"""stack_report.py — post-stack forensics: what did the APS stacker actually
do to your data, and should you trust the result?

WHY THIS MODULE EXISTS
======================
AutoStakkert shows APs and a quality graph; serious users read them.
Production stacking needs the same honesty, machine-readable: drizzle hole
fraction (fill), frame-usage concentration (was it really lucky imaging or
one frame doing everything?), alignment wander statistics (mount drift vs
seeing windshake), and an actionable warning list when any of them is off.
Every number in the report is computed from the APStackResult's own
bookkeeping (weight map, per-frame usage, measured global shifts) — the
module is a PANEL over the stacker, not a parallel opinion of it.

Warnings are phrased as actions (raise pixfrac, reject frames, shorten
derotation spans) because that is what a production tool owes its user.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np


@dataclass
class StackForensics:
    n_frames: int
    n_aps: int
    drizzle: int
    fill_frac: float                 # interior coverage (holes in the footprint)
    dither_spread_px: float          # std of fractional alignment phases
    weight_cv: float                 # grid modulation of the weight map
    usage_min: float
    usage_median: float
    usage_max: float
    usage_concentration: float       # max frame's share of total usage
    wander_rms_detrended_px: float   # global-align residual wander
    drift_slope_px_per_frame: float  # systematic mount-drift term
    max_single_jump_px: float        # biggest frame-to-frame alignment jump
    nominal_snr_gain: float          # sqrt(n_frames * mean usage), upper bound
    sharpness_gain_vs_frame: Optional[float] = None   # Tenengrad ratio (if frames)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {k: (list(v) if isinstance(v, list) else v)
                for k, v in self.__dict__.items()}


def _tenengrad(img: np.ndarray) -> float:
    from scipy.ndimage import sobel
    a = np.asarray(img, dtype=np.float64)
    if a.ndim == 3:
        a = a.mean(axis=-1)
    gx = sobel(a, axis=1)
    gy = sobel(a, axis=0)
    return float(np.mean(gx * gx + gy * gy))


def analyze_stack(res, frames: Optional[Sequence[np.ndarray]] = None,
                  disk_mask: Optional[np.ndarray] = None) -> StackForensics:
    """Forensics over an APStackResult (`res`), optionally against the input
    frames for the sharpness-gain measurement."""
    w = np.asarray(res.weight, dtype=np.float64)
    lit = w > 0
    # "fill" that means something: APs are only placed over the disk, so
    # sky pixels are zero-weight BY DESIGN, and our drizzle drops always
    # overlap neighbouring bins (side = D*pixfrac bins, +1-bin overlap
    # wings), so coverage holes from pixfrac alone cannot occur either.
    # Fill = enclosed-hole coverage inside the coverage FOOTPRINT
    # (binary_fill_holes): catches dropped APs / off-canvas deposits, not
    # ragged edges, not non-holes.
    from scipy.ndimage import binary_fill_holes
    if disk_mask is not None:
        region = np.asarray(disk_mask, dtype=bool)
        fill = float(lit[region].mean()) if region.any() else 0.0
    elif w.size and lit.any():
        footprint = binary_fill_holes(lit)
        fill = float(lit[footprint].mean()) if footprint.any() else 0.0
    else:
        fill = 0.0
    wv = w[lit]
    cv = float(wv.std() / (wv.mean() + 1e-12)) if wv.size > 10 else 0.0

    u = np.asarray(res.per_frame_used, dtype=np.float64)
    n = int(res.n_frames)
    if u.size and u.sum() > 0:
        usage_min = float(u.min())
        usage_med = float(np.median(u))
        usage_max = float(u.max())
        concentration = float(u.max() / u.sum())
    else:
        usage_min = usage_med = usage_max = concentration = 0.0

    gs = np.asarray(res.global_shifts, dtype=np.float64)
    dither = 0.0
    if gs.size and gs.ndim == 2 and gs.shape[0] > 2:
        mag = gs.copy()
        t = np.arange(gs.shape[0], dtype=np.float64)
        # detrend each axis; wander = residual rms, drift = fitted slope
        slopes = []
        resid = []
        for ax in range(2):
            A = np.column_stack([np.ones_like(t), t])
            coef, _, _, _ = np.linalg.lstsq(A, mag[:, ax], rcond=None)
            slopes.append(float(coef[1]))
            resid.append(mag[:, ax] - (coef[0] + coef[1] * t))
        resid = np.concatenate(resid)
        wander = float(np.sqrt(np.mean(resid ** 2)))
        slope = float(math.hypot(*slopes))
        jumps = np.hypot(np.diff(mag[:, 0]), np.diff(mag[:, 1]))
        max_jump = float(jumps.max()) if jumps.size else 0.0
        # dither audit: spread of the FRACTIONAL parts of the alignment
        # phases — drizzle super-res is fuelled by subpixel diversity, and
        # identical fractional phases deposit on the same lattice
        fr = np.abs(gs - np.round(gs))
        fr = np.minimum(fr, 1.0 - fr)                    # wrap distance
        dither = float(np.mean(np.std(fr, axis=0)))
    else:
        wander = slope = max_jump = 0.0

    mean_use = float(u.mean()) if u.size else 0.0
    snr_gain = math.sqrt(max(n * mean_use, 1e-9))

    sharp = None
    if frames:
        idx = np.linspace(0, len(frames) - 1, min(5, len(frames))).astype(int)
        base = float(np.median([_tenengrad(frames[i]) for i in idx]))
        st = _tenengrad(res.stack)
        if base > 0:
            # fair comparison needs the same sampling: downsample drizzle
            # stacks to input scale first
            if int(res.drizzle) > 1:
                from scipy.ndimage import zoom
                st_small = zoom(np.asarray(res.stack, dtype=np.float64)
                                if np.asarray(res.stack).ndim == 2 else
                                np.asarray(res.stack, dtype=np.float64).mean(-1),
                                1.0 / int(res.drizzle), order=1)
                st = _tenengrad(st_small)
            sharp = float(st / base)

    warnings: List[str] = []
    if int(res.drizzle) > 1 and fill < 0.98:
        warnings.append(
            f"coverage fill {fill * 100:.1f}% — holes INSIDE the footprint "
            f"(dropped APs or off-canvas deposits); check the AP grid over "
            f"the disk and the alignment shifts")
    if int(res.drizzle) > 1 and gs.ndim == 2 and gs.shape[0] >= 6 \
            and dither < 0.10:
        warnings.append(
            f"subpixel dither spread {dither:.2f} px — the drizzle grid is "
            f"starved of phase diversity (frames land on the same lattice); "
            f"drizzle super-res gains nothing without real dither")
    if n >= 6 and concentration > 0.30:
        k = int(np.argmax(u)) if u.size else -1
        warnings.append(
            f"frame #{k} carries {concentration * 100:.0f}% of the stack — "
            f"lucky imaging is degenerate; check seeing selection or record "
            f"more frames")
    if n >= 6 and usage_max - usage_min < 0.02 and math.isfinite(usage_max):
        warnings.append(
            "per-frame usage is flat — quality ranking found no difference; "
            "either near-perfect seeing (fine) or a broken quality metric")
    if wander > 3.0:
        warnings.append(
            f"alignment wander {wander:.1f} px RMS after detrending — "
            f"windshake-grade jitter; consider shorter spans with derotation")
    if max_jump > 25.0:
        warnings.append(
            f"single-frame jump of {max_jump:.0f} px — likely a re-centre or "
            f"dropout; split the capture around it")
    if sharp is not None and sharp < 1.02:
        warnings.append(
            f"stack sharpness gain {sharp:.2f}x vs input frames — stacking "
            f"did not add detail; check focus and alignment residuals")
    if u.size and usage_min < 0.005 and n >= 8:
        warnings.append(
            "some frames essentially unused (usage < 0.5%) — the lucky "
            "selection is doing its job, but verify those frames are real "
            "signal (clouds, focus hunts)")

    return StackForensics(
        n_frames=n, n_aps=int(res.n_aps), drizzle=int(res.drizzle),
        fill_frac=fill, dither_spread_px=dither, weight_cv=cv,
        usage_min=usage_min, usage_median=usage_med, usage_max=usage_max,
        usage_concentration=concentration,
        wander_rms_detrended_px=wander, drift_slope_px_per_frame=slope,
        max_single_jump_px=max_jump, nominal_snr_gain=float(snr_gain),
        sharpness_gain_vs_frame=sharp, warnings=warnings)


def forensics_report_text(fx: StackForensics) -> str:
    lines = ["=" * 70,
             "STACK FORENSICS — trust accounting for the APS stack",
             "=" * 70]
    lines.append(f"frames {fx.n_frames}   APs {fx.n_aps}   drizzle x{fx.drizzle}")
    lines.append(f"interior fill: {fx.fill_frac * 100:.2f}%   weight CV: "
                 f"{fx.weight_cv:.2f}   subpixel dither spread: "
                 f"{fx.dither_spread_px:.2f} px")
    lines.append(f"frame usage: min {fx.usage_min:.3f} / med {fx.usage_median:.3f} "
                 f"/ max {fx.usage_max:.3f} (top share "
                 f"{fx.usage_concentration * 100:.0f}%)")
    lines.append(f"alignment: wander {fx.wander_rms_detrended_px:.2f} px RMS "
                 f"(detrended)   drift {fx.drift_slope_px_per_frame:.3f} px/frame   "
                 f"max jump {fx.max_single_jump_px:.1f} px")
    lines.append(f"nominal SNR gain <= {fx.nominal_snr_gain:.2f}x")
    if fx.sharpness_gain_vs_frame is not None:
        lines.append(f"measured sharpness gain vs median frame: "
                     f"{fx.sharpness_gain_vs_frame:.2f}x")
    if fx.warnings:
        lines.append("WARNINGS:")
        for w_ in fx.warnings:
            lines.append(f"  - {w_}")
    else:
        lines.append("warnings: none — stack bookkeeping is healthy")
    return "\n".join(lines)


def render_forensics_png(res, fx: StackForensics, out_path,
                         width: int = 1000, height: int = 620) -> str:
    """Panel: weight map (lit fraction visible), per-frame usage bars,
    measured global (dx, dy) tracks, warnings list."""
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (width, height), (16, 18, 24))
    d = ImageDraw.Draw(img)
    d.text((30, 16), "STACK FORENSICS", fill=(230, 230, 235))
    # --- weight map thumbnail (log stretch so holes read)
    w = np.asarray(res.weight, dtype=np.float64)
    if w.ndim == 2 and w.size:
        wl = np.log10(w + 1.0)
        wl = (wl - wl.min()) / max(wl.max() - wl.min(), 1e-12)
        th = 240
        twl = int(th * w.shape[1] / w.shape[0])
        from PIL import Image as _I
        th_img = _I.fromarray((wl * 255).astype(np.uint8)).resize(
            (twl, th), _I.NEAREST).convert("RGB")
        img.paste(th_img, (30, 60))
        d.rectangle([30, 60, 30 + twl, 60 + th], outline=(120, 125, 140))
        d.text((30, 60 + th + 8), f"weight map (log)  fill {fx.fill_frac * 100:.2f}%",
               fill=(160, 165, 180))
    # --- usage bars
    bx, by, bw, bh = 30, 380, 420, 200
    u = np.asarray(res.per_frame_used, dtype=np.float64)
    d.text((bx, by - 22), "per-frame usage", fill=(160, 165, 180))
    d.rectangle([bx, by, bx + bw, by + bh], outline=(120, 125, 140))
    if u.size:
        umax = float(u.max()) or 1.0
        bwid = max(bw // max(u.size, 1), 1)
        for i, v in enumerate(u):
            hh = int(v / umax * (bh - 12))
            col = (110, 170, 240) if v < 0.9 * umax else (255, 200, 90)
            d.rectangle([bx + 4 + i * bwid, by + bh - 4 - hh,
                         bx + 4 + (i + 1) * bwid - 1, by + bh - 4], fill=col)
    # --- shift tracks
    sx, sy_, sw, sh = 540, 60, 430, 240
    d.text((sx, sy_ - 22), "global apply-shifts (px)", fill=(160, 165, 180))
    d.rectangle([sx, sy_, sx + sw, sy_ + sh], outline=(120, 125, 140))
    gs = np.asarray(res.global_shifts, dtype=np.float64)
    if gs.ndim == 2 and gs.shape[0] > 1:
        span = float(np.abs(gs).max()) or 1.0
        for ax, col in ((0, (120, 200, 255)), (1, (255, 200, 120))):
            pts = [(sx + 8 + i / (gs.shape[0] - 1) * (sw - 16),
                    sy_ + sh / 2 - gs[i, ax] / span * (sh / 2 - 10))
                   for i in range(gs.shape[0])]
            d.line(pts, fill=col, width=2)
        d.line([(sx + 8, sy_ + sh / 2), (sx + sw - 8, sy_ + sh / 2)],
               fill=(70, 76, 92))
        d.text((sx + 6, sy_ + sh - 18), f"+-{span:.1f} px full scale",
               fill=(120, 125, 140))
    # --- warnings
    wy = 340
    d.text((540, wy - 20), "warnings", fill=(160, 165, 180))
    if fx.warnings:
        for i, w_ in enumerate(fx.warnings[:6]):
            d.text((540, wy + i * 18), f"- {w_[:92]}", fill=(255, 190, 110))
    else:
        d.text((540, wy), "none", fill=(140, 210, 140))
    os.makedirs(os.path.dirname(os.path.abspath(str(out_path))), exist_ok=True)
    img.save(str(out_path))
    return str(out_path)


__all__ = ["StackForensics", "analyze_stack", "forensics_report_text",
           "render_forensics_png"]

#!/usr/bin/env python3
"""
observatory_pipeline.py — the v6.8 production pipeline: video capture in,
publishable GRS answer out. One call does:

    SER/AVI → lucky-frame & per-AP APS stack (drizzle) → Sharpen Lab →
    precleaned PNG → the standard published measurement path (process_image)
    → SUPERDUPER best-answer card + JUPOS-exportable row.

Also hosts the small end-to-end helpers the CLI binds:
  stack_video / ap_stack_dir / sharpen_file / animate_frames / export_jupos
"""
from __future__ import annotations

import datetime as dt
import json
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np


def _save_png(path: Path, arr: np.ndarray) -> Path:
    from PIL import Image
    a = np.asarray(arr, dtype=np.float64)
    if a.ndim == 3 and a.shape[0] in (3, 4) and a.shape[0] < min(a.shape[1], a.shape[2]):
        a = np.moveaxis(a, 0, -1)
    lo, hi = (0.0, 1.0) if a.max() <= 1.0 + 1e-6 else (float(a.min()), float(a.max()))
    if hi <= lo:
        hi = lo + 1.0
    u8 = ((np.clip((a - lo) / (hi - lo), 0, 1) * 255) + 0.5).astype(np.uint8)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(u8).save(path)
    return path


def _crop_to_weight(stack: np.ndarray, weight: np.ndarray, margin: int = 2
                     ) -> Tuple[np.ndarray, np.ndarray]:
    """Crop [stack, weight] to the positive-weight bounding box (+margin)."""
    m = weight > 0
    if not m.any():
        return stack, weight
    ys, xs = np.where(m)
    y0 = max(0, ys.min() - margin); y1 = min(weight.shape[0], ys.max() + margin + 1)
    x0 = max(0, xs.min() - margin); x1 = min(weight.shape[1], xs.max() + margin + 1)
    if stack.ndim == 3:
        return stack[y0:y1, x0:x1, :], weight[y0:y1, x0:x1]
    return stack[y0:y1, x0:x1], weight[y0:y1, x0:x1]


# ---------------------------------------------------------------------------
# Video / APS stacking
# ---------------------------------------------------------------------------

def load_frames(
    *,
    video_path: Optional[str] = None,
    frames_dir: Optional[str] = None,
    step: int = 1,
    limit: int = 0,
    downsample: int = 1,
) -> Tuple[List[np.ndarray], dict]:
    """Load a capture into float64 frames in [0,1] (mono or RGB).

    video_path: .ser / .avi (SER preferred; AVI must be uncompressed DIB).
    frames_dir: PNG/JPG/FITS folder (sorted by name).
    downsample: 1,2,4 keep every factor-th row/col (box-decimated) for speed.
    """
    meta: Dict[str, object] = {"source": video_path or frames_dir}
    frames: List[np.ndarray] = []
    if video_path:
        import ser_io
        vid = ser_io.read_video(video_path)
        meta.update(vid.meta.to_dict())
        for i, f in vid.iter_frames(step=max(1, step), limit=limit):
            frames.append(f)
        meta["n_frames_total"] = len(vid)
    elif frames_dir:
        d = Path(frames_dir)
        exts = (".png", ".jpg", ".jpeg", ".fits", ".fit", ".bmp", ".tif", ".tiff")
        files = sorted([f for f in d.iterdir() if f.suffix.lower() in exts])
        if not files:
            raise ValueError(f"no image frames in {d}")
        from PIL import Image
        count = 0
        for k, f in enumerate(files):
            if step > 1 and (k % step) != 0:
                continue
            if f.suffix.lower() in (".fits", ".fit"):
                import grs_complete_system as grs
                arr, _ = grs.read_fits(f)
                a = np.asarray(arr, dtype=np.float64)
                if a.ndim == 3 and a.shape[0] == 3:
                    a = 0.299 * a[0] + 0.587 * a[1] + 0.114 * a[2]
                if a.max() > 1.0:
                    a = a / max(255.0, float(a.max()))
            else:
                a = np.asarray(Image.open(f), dtype=np.float64) / 255.0
            frames.append(a)
            count += 1
            if limit and count >= limit:
                break
        meta.update({"container": "dir", "n_frames_total": len(files)})
    else:
        raise ValueError("load_frames: need video_path or frames_dir")
    if not frames:
        raise ValueError("load_frames: zero frames loaded")

    ds = int(downsample)
    if ds > 1:
        def _dec(a):
            if a.ndim == 3:
                h, w, c = a.shape
                return (a[: h // ds * ds, : w // ds * ds]
                        .reshape(h // ds, ds, w // ds, ds, c).mean(axis=(1, 3)))
            h, w = a.shape
            return (a[: h // ds * ds, : w // ds * ds]
                    .reshape(h // ds, ds, w // ds, ds).mean(axis=(1, 3)))
        frames = [_dec(f) for f in frames]
        meta["downsample"] = ds
    meta["n_frames_loaded"] = len(frames)
    return frames, meta


def _video_frame_times(video_path: str, step: int, limit: int) -> List[Optional[dt.datetime]]:
    """Per-frame UTC stamps for exactly the frames load_frames() keeps."""
    import ser_io
    vid = ser_io.read_video(video_path)
    out: List[Optional[dt.datetime]] = []
    for i, _f in vid.iter_frames(step=max(1, step), limit=limit):
        out.append(vid.frame_utc(i))
    return out


def _resolve_derotate_dts(
    *,
    source_video: Optional[str],
    step: int,
    limit: int,
    n_frames: int,
    dt_per_frame_s: Optional[float],
) -> List[float]:
    """Timing (s, arbitrary zero) for derotation — stamps first, uniform second.

    An explicit derotation request without any timing must FAIL LOUDLY: a
    silent no-op derotate on a long capture is a wrong answer, and this repo
    does not fabricate.
    """
    if source_video:
        times = _video_frame_times(source_video, step, limit)
        known = [t for t in times if t is not None]
        if len(known) == n_frames and n_frames > 0:
            t0 = known[0]
            return [(t - t0).total_seconds() for t in times if t is not None]  # type: ignore[union-attr]
    if dt_per_frame_s and dt_per_frame_s > 0:
        return [k * float(dt_per_frame_s) for k in range(n_frames)]
    raise ValueError(
        "derotate requested but no per-frame timing is available: use a "
        "stamped SER capture (per-frame UTC timestamps) or pass "
        "dt_per_frame_s / --dt-per-frame (uniform cadence). Refusing to guess."
    )


def stack_video(
    video_path: Optional[str] = None,
    *,
    frames_dir: Optional[str] = None,
    out_dir: Optional[Path] = None,
    keep_frac: float = 0.25,
    drizzle: int = 1,
    ap_size: int = 32,
    spacing: int = 0,
    quality: str = "laplacian",
    pixfrac: float = 1.0,
    step: int = 1,
    limit: int = 0,
    downsample: int = 1,
    align_downsample: int = 1,
    sharpen_method: str = "none",
    sharpen_gains: Sequence[float] = (1.8, 1.5, 1.25, 1.1, 1.0),
    png: bool = True,
    derotate: str = "none",
    dt_per_frame_s: Optional[float] = None,
) -> Dict[str, object]:
    """Capture → APS stack. Returns report dict (+ PNGs in out_dir).

    derotate: "none" | "prior" | "hybrid" | "measurement" — per-latitude
    rotation derotation (ap_stacker.derotate_frames) BEFORE stacking, the
    WinJUPOS-derotate step AutoStakkert does not have. Needs per-frame timing:
    SER stamps (preferred) or dt_per_frame_s for a uniform cadence. The stack
    is anchored to the derotation reference frame (report.derotate.ref_index).
    """
    from ap_stacker import stack_ap, APStackConfig, aps_report_text
    frames, meta = load_frames(video_path=video_path, frames_dir=frames_dir,
                               step=step, limit=limit, downsample=downsample)
    derotate = str(derotate or "none")
    derot_report: Optional[Dict[str, object]] = None
    if derotate != "none":
        if derotate not in ("prior", "hybrid", "measurement"):
            raise ValueError(f"derotate must be none|prior|hybrid|measurement, got {derotate!r}")
        import ap_stacker
        dts = _resolve_derotate_dts(
            source_video=video_path if video_path else None,
            step=step, limit=limit, n_frames=len(frames),
            dt_per_frame_s=dt_per_frame_s)
        frames, dinfo = ap_stacker.derotate_frames(
            frames, dt_s_per_frame=dts, mode=derotate)
        wind = dinfo.get("wind_report") or {}
        wind_res = wind.get("wind_residual_mps_vs_model") or []
        derot_report = {
            "mode": derotate,
            "ref_index": dinfo.get("ref_index"),
            "median_per_row_shift_px": dinfo.get("median_per_row_shift_px"),
            "max_per_row_shift_px": dinfo.get("max_per_row_shift_px"),
            "dt_span_s": float(max(dts) - min(dts)),
            # capture-local cloud-tracking wind experiment (v6.8.x): None in
            # prior mode (no image evidence) by design, never fabricated
            "wind_evidence_bins": sum(1 for r in wind_res if r is not None),
            "wind_max_abs_residual_mps": wind.get("max_abs_residual_mps"),
            "wind_report": wind,
        }
    cfg = APStackConfig(
        ap_size_px=ap_size, spacing_px=spacing, keep_frac=keep_frac,
        quality=quality, drizzle=drizzle, pixfrac=pixfrac,
        align_downsample=align_downsample,
    )
    res = stack_ap(frames, cfg)
    stack, weight = _crop_to_weight(res.stack, res.weight)
    report: Dict[str, object] = res.to_dict()
    report["input"] = meta
    if derot_report is not None:
        report["derotate"] = derot_report
    if out_dir:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        if png:
            _save_png(out_dir / "aps_stack.png", stack)
            _save_png(out_dir / "aps_weight.png", weight / max(1e-9, float(weight.max())))
        np.save(out_dir / "aps_stack.npy", stack)
        (out_dir / "APS_REPORT.txt").write_text(aps_report_text(res), encoding="utf-8")
        report["out_dir"] = str(out_dir)
        report["stack_png"] = str(out_dir / "aps_stack.png")
    if sharpen_method and sharpen_method != "none":
        import sharpen_lab
        sharp = sharpen_lab.sharpen(stack, method=sharpen_method, gains=sharpen_gains,
                                    clip=(0.0, 1.0 if stack.max() <= 1.0 + 1e-6 else None))
        report["sharpen_method"] = sharpen_method
        if out_dir and png:
            _save_png(out_dir / "aps_stack_sharp.png", sharp)
            report["sharp_png"] = str(out_dir / "aps_stack_sharp.png")
    else:
        sharp = stack
    return report


def derotate_folder(
    frames_dir: str,
    *,
    out_dir: Optional[Path] = None,
    mode: str = "measurement",
    dt_per_frame_s: Optional[float] = None,
    fps: Optional[float] = None,
    step: int = 1,
    limit: int = 0,
    downsample: int = 1,
) -> Dict[str, object]:
    """Per-latitude derotate a folder of frames (PNG/JPG/FITS), saving the
    derotated frames to out_dir (or <frames_dir>_derot/ next to it).

    Timing: folders carry no timestamps, so pass dt_per_frame_s (capture
    cadence) or fps. Without either, this raises — a guessed cadence would
    silently mis-rotate every frame, which is a wrong answer, not a saving
    grace. mode: "prior" | "hybrid" | "measurement".
    """
    import ap_stacker
    if mode not in ("prior", "hybrid", "measurement"):
        raise ValueError(f"mode must be prior|hybrid|measurement, got {mode!r}")
    if (dt_per_frame_s is None or dt_per_frame_s <= 0) and (fps is None or fps <= 0):
        raise ValueError(
            "derotate_folder needs per-frame timing: pass dt_per_frame_s or "
            "fps (folders carry no timestamps). Refusing to guess.")
    dtpf = float(dt_per_frame_s) if (dt_per_frame_s and dt_per_frame_s > 0) else 1.0 / float(fps)
    frames, meta = load_frames(frames_dir=frames_dir, step=step, limit=limit,
                               downsample=downsample)
    dts = [k * dtpf for k in range(len(frames))]
    warped, info = ap_stacker.derotate_frames(frames, dt_s_per_frame=dts, mode=mode)
    out = Path(out_dir) if out_dir else Path(str(frames_dir) + "_derot")
    out.mkdir(parents=True, exist_ok=True)
    saved = []
    for k, w in enumerate(warped):
        p = out / f"frame_{k:04d}.png"
        _save_png(p, w)
        saved.append(str(p))
    report: Dict[str, object] = {
        "frames_dir": str(frames_dir),
        "out_dir": str(out),
        "n_frames": len(frames),
        "dt_per_frame_s": dtpf,
        "dt_span_s": float(max(dts) - min(dts)) if dts else 0.0,
        "derotate": {
            "mode": mode,
            "ref_index": info.get("ref_index"),
            "median_per_row_shift_px": info.get("median_per_row_shift_px"),
            "max_per_row_shift_px": info.get("max_per_row_shift_px"),
        },
        "saved": saved,
        "input": meta,
    }
    (out / "DEROTATE_REPORT.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8")
    return report


def sharpen_file(
    image_path: str,
    *,
    method: str = "wavelet",
    out: Optional[str] = None,
    gains: Sequence[float] = (1.8, 1.5, 1.25, 1.1, 1.0),
    rl_sigma: float = 1.5,
    rl_iters: int = 14,
    radius: float = 2.5,
    amount: float = 1.0,
    denoise: bool = True,
) -> Dict[str, object]:
    """Sharpen an image file (PNG/JPG) with the chosen Sharpen Lab method."""
    from PIL import Image
    import sharpen_lab
    p = Path(image_path)
    a = np.asarray(Image.open(p), dtype=np.float64) / 255.0
    sharp = sharpen_lab.sharpen(
        a, method=method, gains=gains, rl_sigma_px=rl_sigma, rl_iters=rl_iters,
        unsharp_radius_px=radius, unsharp_amount=amount, denoise=denoise, clip=(0.0, 1.0))
    out_path = Path(out) if out else p.with_name(p.stem + f"_sharp_{method}.png")
    _save_png(out_path, sharp)
    return {
        "in": str(p), "out": str(out_path), "method": method,
        "lapvar_before": sharpen_lab.laplacian_variance(
            a if a.ndim == 2 else a.mean(axis=-1)),
        "lapvar_after": sharpen_lab.laplacian_variance(
            sharp if sharp.ndim == 2 else sharp.mean(axis=-1)),
    }


def animate_frames(
    paths: Sequence[str],
    out: str,
    *,
    fps: float = 4.0,
    stamps: Optional[Sequence[str]] = None,
    stretch: str = "global",
    scale: int = 1,
) -> Dict[str, object]:
    """GIF from image paths (or a --frames-dir)."""
    import animation
    out_path = animation.make_gif(list(paths), out, fps=fps,
                                  stamps=list(stamps) if stamps else None,
                                  stretch=stretch, scale=scale)
    info = animation.gif_info(out_path)
    return {"out": str(out_path), **info}


def export_jupos(
    packages: Sequence[Dict[str, object]],
    out: str,
    **meta,
) -> Dict[str, object]:
    """JUPOS CSV from measurement package dicts (or the SUPERDUPER JSON)."""
    import jupos_io
    p = jupos_io.export_package_measurements(out, packages, **meta)
    rows = jupos_io.read_jupos_csv(p)
    return {"out": str(p), "n_rows": len(rows)}


# ---------------------------------------------------------------------------
# The one-command production answer
# ---------------------------------------------------------------------------

def video_to_answer(
    video_path: str,
    *,
    time_utc: Optional[str] = None,
    mid_time_from_video: bool = True,
    keep_frac: float = 0.25,
    drizzle: int = 1,
    ap_size: int = 32,
    step: int = 1,
    limit: int = 0,
    downsample: int = 1,
    sharpen_method: str = "wavelet",
    out_root: Optional[Path] = None,
    process_kwargs: Optional[Dict[str, object]] = None,
    derotate: str = "none",
) -> Dict[str, object]:
    """Full production answer from a planetary capture.

    1. read video (SER/AVI)
    2. APS stack (per-AP lucky selection + drizzle)
    3. Sharpen Lab
    4. save stack PNG -> run the standard published measurement path on it
    5. return the measurement package + stack diagnostics

    time_utc: mid-exposure UTC "YYYY-MM-DD HH:MM:SS". With
    mid_time_from_video=True and time_utc=None, the SER per-frame stamps
    compute the mid-exposure time of the SELECTED frames.

    derotate: "none" | "prior" | "hybrid" | "measurement" per-latitude
    rotation derotation before stacking (needs stamped SER). When used, the
    stack is anchored to the derotation reference frame, so the measurement
    epoch is reported as that frame's stamp ("measurement_epoch": "ref_frame")
    — the same convention WinJUPOS reductions use for derotated maps.
    """
    from ap_stacker import stack_ap, APStackConfig
    from frame_quality import select_best_frames
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d_%H%M%S")
    root = Path(out_root) if out_root else Path("outputs") / f"v2a_{stamp}"
    root.mkdir(parents=True, exist_ok=True)

    import ser_io
    vid = ser_io.read_video(video_path)
    times = [vid.frame_utc(i) for i in range(len(vid))]
    frames = []
    kept_times: List[Optional[dt.datetime]] = []
    for i, f in vid.iter_frames(step=max(1, step), limit=limit):
        frames.append(f)
        kept_times.append(times[i] if i < len(times) else None)

    ds = int(downsample)
    if ds > 1:
        # box-decimate (same helper as load_frames: anti-aliased, no shift)
        def _dec(a):
            if a.ndim == 3:
                h, w, c = a.shape
                return (a[: h // ds * ds, : w // ds * ds]
                        .reshape(h // ds, ds, w // ds, ds, c).mean(axis=(1, 3)))
            h, w = a.shape
            return (a[: h // ds * ds, : w // ds * ds]
                    .reshape(h // ds, ds, w // ds, ds).mean(axis=(1, 3)))
        frames = [_dec(f) for f in frames]

    # lucky pre-selection: APS already ranks, but pre-cutting the tail saves time
    if frames and keep_frac < 1.0 and len(frames) * keep_frac >= 400:
        idx, _, _ = select_best_frames(frames, keep_frac=min(1.0, keep_frac * 3.0))
        frames = [frames[i] for i in sorted(idx)]
        kept_times = [kept_times[i] for i in sorted(idx)]

    # rotation derotation BEFORE stacking (WinJUPOS derotate step).
    # The stack is then anchored to the derotation REFERENCE frame, so the
    # measurement epoch must be the ref frame's stamp, not the mid-exposure.
    derotate = str(derotate or "none")
    derot_report: Optional[Dict[str, object]] = None
    ref_time: Optional[dt.datetime] = None
    if derotate != "none":
        if derotate not in ("prior", "hybrid", "measurement"):
            raise ValueError(f"derotate must be none|prior|hybrid|measurement, got {derotate!r}")
        if any(t is None for t in kept_times):
            raise ValueError(
                "derotate requested but this capture has no per-frame UTC "
                "stamps (AVI/DIB has none). Use a stamped SER or stack without "
                "derotation — refusing to guess timing.")
        import ap_stacker
        t0 = kept_times[0]
        dts = [(t - t0).total_seconds() for t in kept_times]  # type: ignore[union-attr]
        frames, dinfo = ap_stacker.derotate_frames(
            frames, dt_s_per_frame=dts, mode=derotate)
        ref_idx = int(dinfo.get("ref_index") or 0)
        ref_time = kept_times[ref_idx]
        derot_report = {
            "mode": derotate,
            "ref_index": ref_idx,
            "ref_time_utc": ref_time.strftime("%Y-%m-%d %H:%M:%S") if ref_time else None,
            "median_per_row_shift_px": dinfo.get("median_per_row_shift_px"),
            "max_per_row_shift_px": dinfo.get("max_per_row_shift_px"),
            "dt_span_s": float(max(dts) - min(dts)),
        }

    cfg = APStackConfig(ap_size_px=ap_size, keep_frac=keep_frac, drizzle=drizzle)
    res = stack_ap(frames, cfg)
    stack, weight = _crop_to_weight(res.stack, res.weight)

    import sharpen_lab
    if sharpen_method and sharpen_method != "none":
        stack_out = sharpen_lab.sharpen(stack, method=sharpen_method, clip=(0.0, 1.0))
    else:
        stack_out = stack

    png_stack = _save_png(root / "stack.png", stack)
    if stack_out is not stack:
        png_sharp = _save_png(root / "stack_sharp.png", stack_out)
    else:
        png_sharp = png_stack
    _save_png(root / "weight.png", weight / max(1e-9, float(weight.max())))
    np.save(root / "stack.npy", stack_out)

    # measurement epoch: a derotated stack is anchored to the REF frame —
    # publish on that frame's stamp; otherwise the capture mid-time.
    measurement_epoch = "mid_exposure"
    if derot_report is not None and ref_time is not None and time_utc is None:
        time_utc = ref_time.strftime("%Y-%m-%d %H:%M:%S")
        measurement_epoch = "ref_frame"
    if time_utc is None and mid_time_from_video:
        known = [t for t in kept_times if t is not None]
        if known:
            t_mid = known[0] + (known[-1] - known[0]) / 2
            time_utc = t_mid.strftime("%Y-%m-%d %H:%M:%S")

    result: Dict[str, object] = {
        "video": str(video_path),
        "n_frames_video": len(vid),
        "n_frames_used": len(frames),
        "stack": res.to_dict(),
        "time_utc": time_utc,
        "measurement_epoch": measurement_epoch,
        "out_dir": str(root),
        "stack_png": str(png_sharp),
    }
    if derot_report is not None:
        result["derotate"] = derot_report

    if time_utc:
        from product_core import process_image
        kw = dict(process_kwargs or {})
        kw.setdefault("use_nn", False)
        pkg = process_image(str(png_sharp), time_utc, out_root=root, **kw)
        result["measurement"] = {
            "output_dir": pkg.get("output_dir"),
            "headline": pkg.get("headline"),
            "publish": pkg.get("publish"),
        }
        # SUPERDUPER card path, if produced
        sup = list((root / pkg.get("output_dir", "")).glob("SUPERDUPER*.txt")) if pkg.get("output_dir") else []
        if sup:
            result["superduper_card"] = str(sup[0])
        # Campaign-path cross-check (measured value of adding in v6.8: on
        # bland captures the classical publish definitions can mis-lock —
        # honestly flagged grade REJECT — while the campaign measurement
        # (fit_limb_nav + measure_grs_precision, the exact path all accuracy
        # campaigns gate) still recovers the spot. The report always carries
        # it beside the publish decision so a REJECT still yields a number.
        try:
            from precision_engine import (
                fit_limb_nav, measure_grs_precision, to_mono, wrap_diff)
            h0 = pkg.get("headline") or {}
            p0 = pkg.get("publish") or {}
            cm_used = float(p0.get("cm_iii_deg", h0.get("cm_iii_deg")))
            dist_used = float(p0.get("distance_au", h0.get("distance_au", 5.2)) or 5.2)
            stack_raw = np.load(root / "stack.npy")
            mono = to_mono(stack_raw)
            nav = fit_limb_nav(
                mono, cm_iii_deg=cm_used, distance_au=dist_used,
                north_pa_deg=float(h0.get("north_pa_deg") or 0.0))
            nav.cm_iii_deg = cm_used
            nav.distance_au = dist_used
            if h0.get("sub_obs_lat_deg") is not None:
                nav.sub_lat_deg = float(h0["sub_obs_lat_deg"])
            if h0.get("north_pa_deg") is not None:
                nav.north_pa_deg = float(h0["north_pa_deg"])
            res2 = measure_grs_precision(stack_raw, cm_iii_deg=cm_used,
                                         distance_au=dist_used, nav=nav, quiet=True)
            cam = {
                "lon_iii_deg": float(res2.lon_iii_deg),
                "lat_deg": float(res2.lat_deg),
                "rel_lon_deg": float(wrap_diff(res2.lon_iii_deg, cm_used)),
                "cm_iii_deg": cm_used,
                "method": res2.method,
                "quality": float(res2.quality),
                "path": "fit_limb_nav+measure_grs_precision (campaign gate)",
            }
            plon = p0.get("publish_lon_iii_deg")
            if plon is not None:
                cam["delta_vs_publish_deg"] = float(
                    abs(wrap_diff(res2.lon_iii_deg, float(plon))))
            result["campaign_measurement"] = cam
        except Exception as e:
            result["campaign_measurement"] = None
            result["campaign_note"] = f"campaign cross-check unavailable: {e}"
    else:
        result["measurement"] = None
        result["note"] = "no mid-exposure time available (video has no stamps; pass --time)"
    return result

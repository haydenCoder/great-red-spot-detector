#!/usr/bin/env python3
"""
real_photo_stack.py — run every planetary-stacker warp mode on a folder of REAL
planetary frames and write all the artifacts so you can judge which is best.

WHY THIS SHAPE
==============
No-reference "auto mode selection" is ill-posed (see the v6.7.2 changelog:
sharpness / split-half / consistency metrics are all confounded). So this tool
does NOT pretend to pick a winner. Instead it runs every warp mode on your real
frames and writes, per mode:

  - the stacked PNG (stacked_planetary_<planet>_<mode>.png)
  - the stacker report card (stacker_report_<mode>.txt)
  - a naive-mean stack for reference (no derotation)

plus a Markdown comparison index (COMPARISON.md) that tabulates the report-card
highlights with the honest caveat that, without a ground-truth reference, you
must judge by eye.

USAGE
=====
    python3 tools/real_photo_stack.py --frames-dir ./my_jupiter_frames \\
        --planet Jupiter --quality-gate 0.75 --out ./real_stack_out

Frames may be PNG/JPG/FITS, mono or RGB (RGB is stacked per-channel, v6.7.4).
If you have a mid-exposure UTC per frame, pass --cm-csv (one CM III deg per line,
frame order) so the planet-model expected-drift prior is correct.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import List, Optional

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
for p in (str(APP), str(ROOT / "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")


def _load_frames(d: Path, limit: int = 0) -> List[np.ndarray]:
    raster_exts = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp")
    fits_exts = (".fits", ".fit")
    raster = sorted([f for f in d.iterdir() if f.suffix.lower() in raster_exts])
    fits = sorted([f for f in d.iterdir() if f.suffix.lower() in fits_exts])
    files = raster if raster else fits          # a real folder is one format
    if limit:
        files = files[:limit]
    if not files:
        raise SystemExit(f"no image frames found in {d}")
    out = []
    for f in files:
        if f.suffix.lower() in (".fits", ".fit"):
            import grs_complete_system as grs
            arr, _ = grs.read_fits(f)
            out.append(np.asarray(arr, dtype=np.float64))
        else:
            im = Image.open(f)
            if im.mode in ("L", "I;16"):
                out.append(np.asarray(im.convert("L"), dtype=np.float64) / 255.0)
            else:
                out.append(np.asarray(im.convert("RGB"), dtype=np.float64) / 255.0)
    # Real captures are uniform; normalise any odd sizes by centre-cropping to
    # the common minimum shape so the stacker (and naive mean) get uniform frames.
    if len(out) > 1:
        hs = [a.shape[0] for a in out]
        ws = [a.shape[1] for a in out]
        if len(set(hs)) > 1 or len(set(ws)) > 1:
            mh, mw = min(hs), min(ws)
            norm = []
            for a in out:
                y0 = (a.shape[0] - mh) // 2
                x0 = (a.shape[1] - mw) // 2
                norm.append(a[y0:y0 + mh, x0:x0 + mw])
            out = norm
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--frames-dir", required=True)
    ap.add_argument("--planet", default="Jupiter")
    ap.add_argument("--n-grid", type=int, default=8)
    ap.add_argument("--ap-half", type=int, default=16)
    ap.add_argument("--quality-gate", type=float, default=1.0)
    ap.add_argument("--warp-modes", default="per_latitude,flow,global")
    ap.add_argument("--limit", type=int, default=0, help="cap number of frames loaded")
    ap.add_argument("--cm-csv", default="", help="optional one CM III deg per line, frame order")
    ap.add_argument("--out", default="runs/real_photo_stack")
    args = ap.parse_args()

    from planet_models import get_planet
    from planetary_stacker import run_planetary_stacker, stacker_report_text
    planet = get_planet(args.planet)
    frames = _load_frames(Path(args.frames_dir), limit=args.limit)
    n = len(frames)
    is_rgb = frames[0].ndim == 3
    cm_list: Optional[List[float]] = None
    if args.cm_csv:
        cm_list = [float(x) for x in Path(args.cm_csv).read_text().split() if x.strip()][:n]
        if len(cm_list) != n:
            print(f"WARN: --cm-csv has {len(cm_list)} values for {n} frames; ignoring")
            cm_list = None

    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    modes = [m.strip() for m in args.warp_modes.split(",") if m.strip()]
    print(f"[real] {n} frames ({'RGB' if is_rgb else 'mono'}), planet={planet.name}, "
          f"modes={modes}, quality_gate={args.quality_gate}", flush=True)

    # naive mean for reference
    naive = np.mean(np.stack([np.asarray(f, dtype=np.float64) for f in frames]), axis=0)
    _save_png(naive, out / "naive_mean.png", is_rgb)

    summary = []
    for mode in modes:
        t0 = time.time()
        sub = out / mode
        res = run_planetary_stacker(
            frames, sub, planet=planet, n_grid=args.n_grid, ap_half=args.ap_half,
            cm_iii_per_frame=cm_list, warp_mode=mode, reference="auto",
            quality_gate=args.quality_gate, save=True,
        )
        # the stacker writes stacked_planetary_<planet>.png into `sub`; copy/label it
        produced = Path(res.output_path)
        labelled = out / f"stacked_{planet.name.lower()}_{mode}.png"
        try:
            labelled.write_bytes(produced.read_bytes())
        except Exception:
            labelled = produced
        # also keep a labelled report
        (out / f"stacker_report_{mode}.txt").write_text(stacker_report_text(res), encoding="utf-8")
        summary.append({
            "mode": mode,
            "mean_rms_drift_px": res.mean_rms_drift_px,
            "warp_consistency_std": res.warp_consistency_std,
            "dropped": len(res.dropped_frames),
            "elapsed_s": res.elapsed_s,
            "png": labelled.name,
        })
        print(f"[real] {mode:14s} rms={res.mean_rms_drift_px:7.2f}px "
              f"consistency={res.warp_consistency_std:.4f} dropped={len(res.dropped_frames)} "
              f"({time.time()-t0:.1f}s) -> {labelled.name}", flush=True)

    _write_comparison(out, planet.name, n, is_rgb, args.quality_gate, summary)
    print(f"\n[real] wrote {len(summary)} stacks + naive_mean + COMPARISON.md to {out}")
    print("[real] HONEST NOTE: without a ground-truth reference there is no auto-")
    print("[real] 'best mode' (no-reference selection is ill-posed). Compare the PNGs")
    print("[real] by eye and read the report cards. Pick per_latitude by default.")


def _save_png(arr: np.ndarray, path: Path, is_rgb: bool) -> None:
    a = np.asarray(arr, dtype=np.float64)
    u8 = (np.clip(a / max(a.max(), 1e-9), 0, 1) * 255).astype(np.uint8)
    Image.fromarray(u8, "RGB" if is_rgb else "L").save(path, optimize=False)


def _write_comparison(out: Path, planet: str, n: int, is_rgb: bool,
                      qg: float, summary: list) -> None:
    lines = [
        f"# Real-photo stack comparison — {planet} ({n} frames, "
        f"{'RGB' if is_rgb else 'mono'}, quality_gate={qg})",
        "",
        "**Honest note:** without a ground-truth reference there is no automatic",
        "'best mode' — no-reference warp-mode selection is ill-posed (see the",
        "v6.7.2 changelog). Compare the PNGs by eye and read the report cards.",
        "Default recommendation: `per_latitude` (robust); `flow` if the data is",
        "clean with strong local/2D motion.",
        "",
        "| mode | mean drift RMS (px) | warp consistency (raw) | dropped | png |",
        "|---|---|---|---|---|",
    ]
    for s in summary:
        lines.append(f"| {s['mode']} | {s['mean_rms_drift_px']:.2f} | "
                     f"{s['warp_consistency_std']:.4f} | {s['dropped']} | "
                     f"`{s['png']}` |")
    lines += ["", "Files: `naive_mean.png` (no derotation), one `stacked_*.png` + "
              "`stacker_report_*.txt` per mode."]
    (out / "COMPARISON.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()

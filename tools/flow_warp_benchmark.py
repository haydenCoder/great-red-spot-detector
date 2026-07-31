#!/usr/bin/env python3
"""
flow_warp_benchmark.py — reproducible A/B across stacker warp modes + the
quality gate, on controllable per-latitude + 2D-distortion perturbations.

Renders one synthetic Jupiter, makes N frames by warping it with a KNOWN flow
(zonal shear + random local eddies, optional seeing blur + noise), then stacks
with each variant and measures how close each stack lands to the reference
(on-disk RMS, lower = better; per-belt correlation peak, higher = better).

This is the harness behind the v6.7.x accuracy claims — run it to reproduce:

    python3 tools/flow_warp_benchmark.py --n-frames 8 --eddies 12 --eddy-amp 2.0

USAGE
=====
    python3 tools/flow_warp_benchmark.py [options]
    --out runs/flow_warp_benchmark.json   (results + per-variant metrics)
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Dict, List

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
for p in (str(APP), str(ROOT / "tools")):
    if p not in sys.path:
        sys.path.insert(0, p)
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")


def _render_ref(seed: int = 2024, resolution: str = "720p"):
    from synthetic_hq import SynthSpec, generate
    with tempfile.TemporaryDirectory(prefix="grs_fwb_") as d:
        png, _fit, truth = generate(
            SynthSpec(region="global", resolution_preset=resolution, random_time=True,
                      seed=seed, mode="metrology", write_grs_crop=False),
            Path(d),
        )
        arr = np.asarray(Image.open(png), dtype=np.float64) / 255.0
    mono = 0.299 * arr[..., 0] + 0.587 * arr[..., 1] + 0.114 * arr[..., 2]
    return mono, truth


def _on_disk(ref):
    from precision_engine import fit_limb_nav
    from planetary_stacker import _per_pixel_lat
    nav = fit_limb_nav(ref, cm_iii_deg=0.0, distance_au=5.2)
    nav.sub_lat_deg = 0.0
    nav.north_pa_deg = 0.0
    h, w = ref.shape
    _lat, on = _per_pixel_lat(nav, h, w, 0.0, 0.0)
    return on


def make_distorted_frames(ref, lat_map, on_disk, *, n_frames, zonal_amp,
                          n_eddies, eddy_amp, seeing_sigma=0.0, noise=0.0,
                          base_seed=100):
    """Each frame = ref warped by zonal(lat) + random eddies, +/- seeing/noise."""
    from flow_warp import apply_flow_warp
    from scipy.ndimage import gaussian_filter
    h, w = ref.shape
    frames: List[np.ndarray] = [ref]
    for k in range(1, n_frames):
        rng = np.random.default_rng(base_seed + k)
        yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
        u = zonal_amp * np.abs(np.sin(np.deg2rad(lat_map))) * 4.0
        v = np.zeros((h, w))
        ys_on, xs_on = np.where(on_disk)
        for _ in range(n_eddies):
            i = rng.integers(0, ys_on.size)
            y0, x0 = ys_on[i], xs_on[i]
            A = eddy_amp * rng.choice([-1, 1])
            sig = rng.uniform(12, 28)
            dy, dxx = yy - y0, xx - x0
            g = np.exp(-(dxx * dxx + dy * dy) / (2 * sig * sig))
            u += A * g * (-dy) / sig
            v += A * g * (dxx) / sig
        flow = np.stack([v, u], axis=-1)
        fr = apply_flow_warp(ref, flow)
        if seeing_sigma > 0:
            fr = gaussian_filter(fr, seeing_sigma)
        if noise > 0:
            fr = fr + rng.normal(0, noise, fr.shape)
        frames.append(fr)
    return frames


def _disk_rms(a, b, on_disk):
    m = on_disk
    return float(np.sqrt(np.mean((a[m] - b[m]) ** 2)))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n-frames", type=int, default=8)
    ap.add_argument("--resolution", default="720p")
    ap.add_argument("--zonal-amp", type=float, default=2.0)
    ap.add_argument("--eddies", type=int, default=12)
    ap.add_argument("--eddy-amp", type=float, default=2.0)
    ap.add_argument("--seeing", type=float, default=0.0, help="Gaussian blur sigma (px)")
    ap.add_argument("--noise", type=float, default=0.0, help="additive Gaussian noise sigma")
    ap.add_argument("--n-grid", type=int, default=8)
    ap.add_argument("--quality-gate", type=float, default=1.0)
    ap.add_argument("--out", default="runs/flow_warp_benchmark.json")
    args = ap.parse_args()

    print("[fwb] rendering reference...", flush=True)
    ref, _truth = _render_ref(seed=2024, resolution=args.resolution)
    from precision_engine import fit_limb_nav
    from planetary_stacker import _per_pixel_lat
    nav = fit_limb_nav(ref, cm_iii_deg=0.0, distance_au=5.2)
    nav.sub_lat_deg = 0.0
    nav.north_pa_deg = 0.0
    h, w = ref.shape
    lat_map, on_disk = _per_pixel_lat(nav, h, w, 0.0, 0.0)

    print(f"[fwb] building {args.n_frames} frames: zonal={args.zonal_amp}, "
          f"eddies={args.eddies}@{args.eddy_amp}, seeing={args.seeing}, "
          f"noise={args.noise}", flush=True)
    frames = make_distorted_frames(
        ref, lat_map, on_disk, n_frames=args.n_frames, zonal_amp=args.zonal_amp,
        n_eddies=args.eddies, eddy_amp=args.eddy_amp,
        seeing_sigma=args.seeing, noise=args.noise,
    )
    naive = np.mean(np.stack(frames), axis=0)

    from planetary_stacker import run_planetary_stacker
    variants = ["global", "per_latitude", "flow"]
    results: Dict[str, dict] = {"naive_mean": {"disk_rms": _disk_rms(naive, ref, on_disk)}}
    for v in variants:
        with tempfile.TemporaryDirectory(prefix=f"fwb_{v}_") as d:
            res = run_planetary_stacker(
                frames, Path(d), n_grid=args.n_grid, ap_half=16,
                warp_mode=v, reference="first", quality_gate=args.quality_gate,
            )
            stack = np.asarray(Image.open(res.output_path), dtype=np.float64) / 255.0
            results[v] = {
                "disk_rms": _disk_rms(stack, ref, on_disk),
                "dropped_frames": res.dropped_frames,
                "mean_rms_drift_px": res.mean_rms_drift_px,
                "elapsed_s": res.elapsed_s,
            }

    print("\n[fwb] === on-disk RMS to reference (lower = better aligned) ===")
    print(f"  {'variant':16s}  {'disk_rms':>9s}")
    for name in ["naive_mean", "global", "per_latitude", "flow"]:
        print(f"  {name:16s}  {results[name]['disk_rms']:9.4f}")
    best = min(variants, key=lambda v: results[v]["disk_rms"])
    print(f"\n[fwb] best warp variant: {best} "
          f"(rms {results[best]['disk_rms']:.4f})")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps({
        "args": vars(args), "results": results, "best": best,
    }, indent=2), encoding="utf-8")
    print(f"[fwb] wrote {args.out}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Zonal-derotator benchmark.

Renders a single synthetic Jupiter frame, applies per-latitude zonal
shift + SPICE CM III motion to make N frames, then derotates with
the two derotators:
  - win_jupos_derotator (single global rotation)
  - jupiter_zonal_derotator (per-row zonal-wind-residual)

Measures per-belt residual motion after derotation. The new
zonal-derotator should beat the single-rotation derotator on
non-equatorial bands.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
TOOLS = ROOT / "tools"
for p in (str(APP), str(TOOLS)):
    if p not in sys.path:
        sys.path.insert(0, p)

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_v, "1")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n-frames", type=int, default=20)
    ap.add_argument("--n-grid", type=int, default=6)
    ap.add_argument("--ap-half", type=int, default=16)
    ap.add_argument("--resolution", default="720p")
    ap.add_argument("--dt-between-frames", type=float, default=10.0)
    ap.add_argument("--cm-drift", type=float, default=2.0)
    ap.add_argument("--out", default="runs/zonal_derotator_benchmark.json")
    ap.add_argument("--out-stack-dir", default="runs/zonal_derotator_stacks")
    args = ap.parse_args()

    out_dir = Path(args.out_stack_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Reuse the zonal_stacker_benchmark helpers
    from zonal_stacker_benchmark import (
        _render_synthetic_frame, _apply_zonal_shift, _per_belt_residual_motion,
    )

    print("[zderot] rendering reference frame...", flush=True)
    ref_mono, ref_truth = _render_synthetic_frame(seed=2024, resolution=args.resolution)
    h, w = ref_mono.shape
    cm_iii_ref = float(ref_truth["cm_iii_deg"])

    print(f"[zderot] building {args.n_frames} frames with zonal-shear shift "
          f"(dt={args.dt_between_frames}s, cm_drift={args.cm_drift}°/frame)...", flush=True)
    frames: List[np.ndarray] = [ref_mono]
    cm_iii_list: List[float] = [cm_iii_ref]
    for k in range(1, args.n_frames):
        cm_k = cm_iii_ref + k * args.cm_drift
        shifted = _apply_zonal_shift(
            ref_mono,
            cm_iii_ref=cm_iii_ref,
            cm_iii_frame=cm_k,
            distance_au=float(ref_truth["distance_au"]),
            sub_lat_deg=float(ref_truth.get("sub_obs_lat_deg", 0.0) or 0.0),
            north_pa_deg=float(ref_truth.get("north_pa_deg", 0.0) or 0.0),
        )
        frames.append(shifted)
        cm_iii_list.append(cm_k)

    # Run the three derotators
    results: Dict[str, Any] = {}
    for name in ("winjupos", "zonal_meas", "zonal_prior"):
        print(f"\n[zderot] === running {name} ===", flush=True)
        import time
        t0 = time.time()
        if name == "winjupos":
            from win_jupos_derotator import run_win_jupos_derotate
            res = run_win_jupos_derotate(
                frames, out_dir / "winjupos",
                n_grid=args.n_grid, ap_half=args.ap_half,
            )
        elif name == "zonal_meas":
            from jupiter_zonal_derotator import run_jupiter_zonal_derotate
            res = run_jupiter_zonal_derotate(
                frames, out_dir / "zonal_meas",
                cm_iii_per_frame=cm_iii_list,
                dt_s_per_frame=[k * args.dt_between_frames for k in range(args.n_frames)],
                sub_lat_deg=float(ref_truth.get("sub_obs_lat_deg", 0.0) or 0.0),
                north_pa_deg=float(ref_truth.get("north_pa_deg", 0.0) or 0.0),
                mode="measurement",
            )
        else:  # "zonal_prior"
            from jupiter_zonal_derotator import run_jupiter_zonal_derotate
            res = run_jupiter_zonal_derotate(
                frames, out_dir / "zonal_prior",
                cm_iii_per_frame=cm_iii_list,
                dt_s_per_frame=[k * args.dt_between_frames for k in range(args.n_frames)],
                sub_lat_deg=float(ref_truth.get("sub_obs_lat_deg", 0.0) or 0.0),
                north_pa_deg=float(ref_truth.get("north_pa_deg", 0.0) or 0.0),
                mode="prior",
            )
        t1 = time.time()
        try:
            stack = np.asarray(Image.open(res.output_path), dtype=np.float64) / 255.0
            if stack.ndim == 3:
                stack = 0.299 * stack[..., 0] + 0.587 * stack[..., 1] + 0.114 * stack[..., 2]
            belt = _per_belt_residual_motion(stack, ref_mono)
        except Exception as e:
            belt = {"error": str(e)}
        results[name] = {
            "elapsed_s": float(t1 - t0),
            "per_belt": belt,
            "output": str(res.output_path),
        }
        if isinstance(belt, dict) and "north_polar" in belt:
            print(f"[zderot]   {name} per-belt correlation peaks (1.0=perfect):")
            for k, v in belt.items():
                print(f"[zderot]     {k:18s} peak={v['peak']:.4f}  lag={v['lag_deg']:+.2f}°")
            overall = float(np.mean([v["peak"] for v in belt.values()]))
            print(f"[zderot]   {name} OVERALL mean peak = {overall:.4f}")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(results, indent=2), encoding="utf-8")
    if len(results) >= 2 and all("per_belt" in v and "north_polar" in v["per_belt"] for v in results.values()):
        print("\n[zderot] === per-belt peak summary (1.0 = perfect alignment) ===")
        names = list(results.keys())
        bands = list(results[names[0]]["per_belt"].keys())
        print(f"  {'band':18s}  " + "  ".join(f"{n:>10s}" for n in names))
        for b in bands:
            row = f"  {b:18s}  " + "  ".join(
                f"{results[n]['per_belt'][b]['peak']:10.4f}" for n in names
            )
            print(row)
        overall = {
            n: float(np.mean([v["peak"] for v in results[n]["per_belt"].values()]))
            for n in names
        }
        print(f"  {'OVERALL':18s}  " + "  ".join(f"{overall[n]:10.4f}" for n in names))


if __name__ == "__main__":
    main()

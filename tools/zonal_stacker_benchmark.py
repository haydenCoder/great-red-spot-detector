#!/usr/bin/env python3
"""
Zonal-shear stacker benchmark.

Renders a single synthetic Jupiter frame, then makes N "frames" by
applying a known per-latitude wind shift to the original. The shift
is exactly the zonal-wind residual profile (so the ground truth is
known: per-latitude, the expected alignment error is 0).

Then stacks with three stackers:
  - JPA-10K (generic AP-grid, no zonal prior)
  - holy-hybrid (CNN + physics MAP, generic)
  - jupiter-zonal (System III + zonal-wind prior, optional GRS anchor)

Measures the per-belt residual motion in each stack: the lower
the better. Reports the per-belt SNR improvement of the new
stacker over the old.

USAGE
=====
    python3 tools/zonal_stacker_benchmark.py \\
        --n-frames 30 --n-grid 8 --ap-half 16 \\
        --dt-between-frames 10.0 \\
        --out runs/zonal_benchmark.json
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
import time
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

# Keep BLAS single-threaded so the per-case process pool is the only parallelism
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_v, "1")


def _render_synthetic_frame(seed: int, resolution: str = "720p"):
    """Render one metrology-mode synthetic frame and return (mono, truth)."""
    from synthetic_hq import SynthSpec, generate
    with tempfile.TemporaryDirectory(prefix="grs_zbench_") as d:
        png, _fit, truth = generate(
            SynthSpec(
                region="global", resolution_preset=resolution, random_time=True,
                seed=int(seed), mode="metrology", write_grs_crop=False,
            ),
            Path(d),
        )
        arr = np.asarray(Image.open(png), dtype=np.float64) / 255.0
    mono = 0.299 * arr[..., 0] + 0.587 * arr[..., 1] + 0.114 * arr[..., 2]
    return mono, truth


def _apply_zonal_shift(
    img: np.ndarray,
    cm_iii_ref: float,
    cm_iii_frame: float,
    distance_au: float = 5.2,
    sub_lat_deg: float = 0.0,
    north_pa_deg: float = 0.0,
) -> np.ndarray:
    """
    Apply a per-latitude zonal-wind shift to the image, simulating
    cloud-tracking motion between two epochs.

    Method (simple, honest, no resampling artifacts):
      1) Compute the planetocentric latitude of every pixel using
         the limb-nav body-frame geometry (same as precision_engine
         _moment_mask_grs). This is accurate to ~0.5° on real data.
      2) For each image row, compute the row's mean latitude
         (restricted to on-disk pixels).
      3) Compute the per-row image-x shift:
            Δx_row = Δx_sys3 + Δx_zonal(lat_row)
         where Δx_sys3 is the equatorial shift (single rotation)
         and Δx_zonal(lat) is the extra per-latitude shift from
         the zonal-wind residual profile.
      4) Apply a 1-D FFT sub-pixel shift to each row.

    The "ground truth" the stacker is being measured against is:
    a perfect stack aligns the frames, so per-belt correlation
    peaks vs the reference should be 1.0 and the lag should be 0.
    A smeared stack has lower peaks (and a non-zero lag if the
    smear is biased).
    """
    from precision_engine import (
        fit_limb_nav, deg2rad, FLAT as FLAT_CONST, px_to_lonlat_vec,
    )
    from jupiter_zonal_stacker import (
        _zonal_wind_rate_at_lat_deg_per_s, SYS3_RATE_DEG_PER_S,
    )
    h, w = img.shape
    nav = fit_limb_nav(img, cm_iii_deg=cm_iii_ref, distance_au=distance_au)
    nav.cm_iii_deg = cm_iii_ref
    nav.distance_au = distance_au
    nav.sub_lat_deg = sub_lat_deg
    nav.north_pa_deg = north_pa_deg
    # Per-pixel planetocentric latitude via the EXACT oblate-spheroid LOS
    # intersection (same geometry used for measurement and for the derotator's
    # AP latitudes). The previous sphere+anisotropic-y approximation differed
    # from the true spheroid latitude by ~1.3 deg (up to ~1.9 at the GRS band
    # even at D=P=0), so the planted shear was evaluated at the wrong latitude.
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    _, lat = px_to_lonlat_vec(yy.ravel(), xx.ravel(), nav)
    lat = lat.reshape(h, w)
    on_disk = (
        ((xx - nav.xc) / (nav.a_eq_px + 1e-12)) ** 2
        + ((nav.yc - yy) / (nav.b_pol_px + 1e-12)) ** 2
    ) <= 0.97 ** 2
    # CM III rotation in degrees (wrapped to [-180, 180])
    dcm_deg = cm_iii_frame - cm_iii_ref
    if dcm_deg > 180.0:
        dcm_deg -= 360.0
    elif dcm_deg < -180.0:
        dcm_deg += 360.0
    # Plate scale at the equator, CORRECTED in v6.8.x to the physical chord:
    # one degree of longitude rotation moves a surface point by
    # (pi/180)*r(phi)*cos(phi)*a px in image-x (the old a/90 under-shifted by
    # 1.57x at the equator, making the simulated shear ~60% gentler than real
    # Jupiter — derotators were being graded against fiction).
    a_eq = nav.a_eq_px
    k_flat = 1.0 - FLAT_CONST
    def _px_per_deg(la_deg: float) -> float:
        la = deg2rad(la_deg)
        c, sn = math.cos(la), math.sin(la)
        r_phi = 1.0 / math.sqrt(c * c + (sn / k_flat) ** 2)
        return (math.pi / 180.0) * r_phi * c * a_eq
    # The equatorial shift is the CM III rotation projected to image-x.
    dx_sys3 = 0.0  # folded into the per-row latitude-aware shift below
    # Pre-compute per-row latitudes
    row_lats = np.zeros(h, dtype=np.float64)
    for row in range(h):
        m = on_disk[row]
        if m.any():
            row_lats[row] = float(np.mean(lat[row][m]))
        else:
            row_lats[row] = 0.0
    out = np.asarray(img, dtype=np.float64).copy()
    for row in range(h):
        if not on_disk[row].any():
            continue
        avg_lat = row_lats[row]
        rate = _zonal_wind_rate_at_lat_deg_per_s(avg_lat)
        if abs(SYS3_RATE_DEG_PER_S) < 1e-12:
            extra_lon = 0.0
        else:
            extra_lon = (rate - SYS3_RATE_DEG_PER_S) * (dcm_deg / SYS3_RATE_DEG_PER_S)
        dx_row = -(dcm_deg + extra_lon) * _px_per_deg(avg_lat)  # content moves -x
        if abs(dx_row) < 0.02:
            continue
        # Spatial-domain cubic resample at (x - dx): content moves +dx with NO
        # circulant wraparound and NO Gibbs ringing at the hard sky/limb edge.
        # (The FFT phase ramp used here before v6.8.x did both, so planted
        # frames carried bright bars in the sky and combs at the limb —
        # stacking "methods" were then grading who best handled an artifact
        # the simulator invented. mode="nearest" keeps the sky constant and
        # is the honest no-more-planet boundary.)
        from scipy.ndimage import map_coordinates
        out[row] = map_coordinates(
            out[row], [np.arange(out.shape[1], dtype=np.float64) - dx_row],
            order=3, mode="nearest", prefilter=True)
    return out


def _per_belt_residual_motion(
    stack: np.ndarray, ref: np.ndarray,
) -> Dict[str, Any]:
    """
    Measure per-belt correlation between the stack and the reference.
    A perfect stack has peak 1.0 and lag 0° at every band; a smeared
    stack has lower peaks (and a non-zero lag if the smear is biased).
    """
    from precision_engine import fit_limb_nav, make_cylindrical
    nav_r = fit_limb_nav(ref, cm_iii_deg=0.0, distance_au=5.2)
    nav_s = fit_limb_nav(stack, cm_iii_deg=0.0, distance_au=5.2)
    cyl_r = make_cylindrical(ref, nav_r, width=1440, height=720)
    cyl_s = make_cylindrical(stack, nav_s, width=1440, height=720)
    bands = {
        "north_polar":    (-90.0, -60.0),
        "north_mid":      (-60.0, -30.0),
        "north_tropics":  (-30.0, -10.0),
        "south_tropics":  (-10.0, +10.0),
        "south_mid":      (+10.0, +30.0),
        "south_polar":    (+30.0, +60.0),
    }
    H, W = cyl_r.shape
    lat_centres = 90.0 - (np.arange(H) + 0.5) * 180.0 / H
    res: Dict[str, Any] = {}
    for name, (lo, hi) in bands.items():
        m = (lat_centres >= lo) & (lat_centres < hi)
        if m.sum() < 20:
            continue
        a = cyl_r[m].mean(axis=0)
        b = cyl_s[m].mean(axis=0)
        a = a - a.mean()
        b = b - b.mean()
        eps = max(float(np.max(np.abs(a * np.conj(a))) * 1e-9), 1e-9) if a.size else 1e-9
        cc = np.real(
            np.fft.ifft(
                np.fft.fft(a) * np.conj(np.fft.fft(b)) /
                (np.abs(np.fft.fft(a) * np.conj(np.fft.fft(b))) + eps)
            )
        )
        peak = float(np.max(cc))
        py = int(np.argmax(cc))
        if py > cc.size / 2:
            py -= cc.size
        lag_deg = float(py) * 180.0 / cc.size
        res[name] = {"peak": peak, "lag_deg": lag_deg}
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n-frames", type=int, default=20)
    ap.add_argument("--n-grid", type=int, default=8)
    ap.add_argument("--ap-half", type=int, default=16)
    ap.add_argument("--resolution", default="720p")
    ap.add_argument("--dt-between-frames", type=float, default=10.0,
                    help="simulated time between frames (sec) — drives wind shear")
    ap.add_argument("--cm-drift", type=float, default=2.0,
                    help="simulated CM III drift per frame (deg)")
    ap.add_argument("--out", default="runs/zonal_benchmark.json")
    ap.add_argument("--out-stack-dir", default="runs/zonal_benchmark_stacks")
    ap.add_argument("--stacker", default="all",
                    help="comma-separated: jpa10k,holy,zonal,all")
    args = ap.parse_args()

    stackers = ["jpa10k", "holy", "zonal"] if args.stacker == "all" else args.stacker.split(",")

    out_stack_dir = Path(args.out_stack_dir)
    out_stack_dir.mkdir(parents=True, exist_ok=True)

    # Render the reference frame
    print("[zbench] rendering reference frame...", flush=True)
    ref_mono, ref_truth = _render_synthetic_frame(seed=2024, resolution=args.resolution)
    h, w = ref_mono.shape
    cm_iii_ref = float(ref_truth["cm_iii_deg"])
    print(f"[zbench]   ref shape {w}x{h}, CM III = {cm_iii_ref:.3f}°")

    # Build the N frames: each is the reference frame with a
    # per-latitude zonal-wind shift applied.
    print(f"[zbench] building {args.n_frames} frames with zonal-shear shift "
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
    dt_s = args.dt_between_frames

    results: Dict[str, Any] = {}
    for name in stackers:
        print(f"\n[zbench] === running {name} ===", flush=True)
        t0 = time.time()
        if name == "jpa10k":
            from jpa_10k import run_jpa_10k
            res = run_jpa_10k(
                frames, out_stack_dir / f"jpa10k",
                n_grid=args.n_grid, ap_half=args.ap_half,
            )
        elif name == "holy":
            from holy_hybrid_stacker import run_holy_hybrid
            res = run_holy_hybrid(
                frames, out_stack_dir / f"holy",
                n_grid=args.n_grid, ap_half=args.ap_half,
                n_importance=4,
                auto_train=False,
            )
        elif name == "zonal":
            from jupiter_zonal_stacker import run_jupiter_zonal_stacker
            res = run_jupiter_zonal_stacker(
                frames, out_stack_dir / f"zonal",
                n_grid=args.n_grid, ap_half=args.ap_half,
                cm_iii_deg=cm_iii_ref,
                distance_au=float(ref_truth["distance_au"]),
                sub_lat_deg=float(ref_truth.get("sub_obs_lat_deg", 0.0) or 0.0),
                north_pa_deg=float(ref_truth.get("north_pa_deg", 0.0) or 0.0),
                cm_iii_per_frame=cm_iii_list,
                dt_s_per_frame=[k * dt_s for k in range(args.n_frames)],
            )
        else:
            print(f"[zbench]   unknown stacker: {name}")
            continue
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
            "mean_rms_drift_px": float(getattr(res, "mean_rms_drift_px", 0.0)),
            "mean_ap_quality": float(getattr(res, "mean_ap_quality", 0.0)),
            "zonal_rotation_deg": float(getattr(res, "zonal_rotation_deg", 0.0)),
            "per_belt": belt,
            "output": str(res.output_path),
        }
        if isinstance(belt, dict) and "north_polar" in belt:
            print(f"[zbench]   {name} per-belt correlation peaks (1.0=perfect):")
            for k, v in belt.items():
                print(f"[zbench]     {k:18s} peak={v['peak']:.4f}  lag={v['lag_deg']:+.2f}°")
            overall = float(np.mean([v["peak"] for v in belt.values()]))
            print(f"[zbench]   {name} OVERALL mean peak = {overall:.4f}")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(results, indent=2), encoding="utf-8")
    if len(results) >= 2 and all("per_belt" in v and "north_polar" in v["per_belt"] for v in results.values()):
        print("\n[zbench] === per-belt peak summary (1.0 = perfect alignment) ===")
        names = list(results.keys())
        bands = list(results[names[0]]["per_belt"].keys())
        print(f"  {'band':18s}  " + "  ".join(f"{n:>8s}" for n in names))
        for b in bands:
            row = f"  {b:18s}  " + "  ".join(
                f"{results[n]['per_belt'][b]['peak']:8.4f}" for n in names
            )
            print(row)
        overall = {
            n: float(np.mean([v["peak"] for v in results[n]["per_belt"].values()]))
            for n in names
        }
        print(f"  {'OVERALL':18s}  " + "  ".join(f"{overall[n]:8.4f}" for n in names))
        print(f"\n[zbench] === per-belt lag (deg) — should be ≈ 0 for a perfect stack ===")
        print(f"  {'band':18s}  " + "  ".join(f"{n:>8s}" for n in names))
        for b in bands:
            row = f"  {b:18s}  " + "  ".join(
                f"{results[n]['per_belt'][b]['lag_deg']:+8.2f}" for n in names
            )
            print(row)


if __name__ == "__main__":
    main()

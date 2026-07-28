#!/usr/bin/env python3
"""
Batch synthetic proof suite — 50–100 runs with saved results
============================================================

Generates independent synthetic Jupiter frames, measures GRS with the
precision / research stack, scores truth recovery in arcseconds, and writes:

  outputs/batch_prove_<stamp>/
    runs/run_XXXX/...
    batch_summary.json
    batch_summary.csv
    batch_report.txt
    spice_status.json

Usage:
  cd app && python3 batch_prove.py --n 60 --resolution 1080p
  cd app && python3 batch_prove.py --n 50 --resolution 4K --fast
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import statistics
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

from verbose_log import CONSOLE
from synthetic_hq import SynthSpec, generate
from precision_engine import (
    fit_limb_nav,
    measure_grs_precision,
    sky_error_arcsec,
    wrap_diff,
)


def _percentile(xs: List[float], p: float) -> float:
    if not xs:
        return float("nan")
    a = sorted(xs)
    if len(a) == 1:
        return float(a[0])
    k = (len(a) - 1) * (p / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return float(a[int(k)])
    return float(a[f] * (c - k) + a[c] * (k - f))


def run_one(
    out_dir: Path,
    *,
    resolution: str,
    region: str,
    seed: Optional[int],
    fast: bool,
    use_research: bool,
) -> Dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    png, fit, truth = generate(
        SynthSpec(
            user_time_iso="",
            region=region,
            resolution_preset=resolution,
            random_time=True,
            seed=seed,
            wave_contrast=1.15,
            seeing_fwhm_arcsec=0.32,
            write_grs_crop=True,
        ),
        out_dir,
    )

    # load image (prefer CHW RGB for red-aware moment)
    try:
        import grs_complete_system as grs
        arr, _ = grs.read_fits(fit)
        img = np.asarray(arr, dtype=np.float64)
        if img.ndim == 3 and img.shape[0] == 3:
            meas = img[0]
            channels = {"R": img[0], "G": img[1], "B": img[2]}
            meas_rgb = img
        else:
            meas = img
            channels = None
            meas_rgb = img
    except Exception:
        from PIL import Image
        rgb = np.asarray(Image.open(png).convert("RGB"), dtype=np.float64) / 255.0
        meas = rgb[:, :, 0]
        channels = {"R": rgb[:, :, 0], "G": rgb[:, :, 1], "B": rgb[:, :, 2]}
        meas_rgb = np.moveaxis(rgb, 2, 0)

    if meas.size > 25_000_000:
        meas = meas[::2, ::2]
        if channels:
            channels = {k: v[::2, ::2] for k, v in channels.items()}
        if meas_rgb.ndim == 3:
            meas_rgb = meas_rgb[:, ::2, ::2]

    nav = fit_limb_nav(
        meas,
        cm_iii_deg=float(truth["cm_iii_deg"]),
        distance_au=float(truth["distance_au"]),
    )
    nav.cm_iii_deg = float(truth["cm_iii_deg"])
    nav.distance_au = float(truth["distance_au"])

    result: Dict[str, Any] = {
        "truth": truth,
        "png": str(png),
        "fit": str(fit),
        "mode": "fast_precision" if (fast or not use_research) else "research_grade",
    }

    # NN prior can pull latitude far off on pure synthetics — off for proof suite
    measure_grs_precision._use_nn = False

    if fast or not use_research:
        pr = measure_grs_precision(
            meas_rgb,
            cm_iii_deg=nav.cm_iii_deg,
            distance_au=nav.distance_au,
            nav=nav,
            map_width=1800 if resolution in ("1080p",) else 2400,
            map_height=900 if resolution in ("1080p",) else 1200,
        )
        lon = pr.lon_iii_deg
        lat = pr.lat_deg
        result["precision"] = pr.to_dict()
        result["measured"] = {"lon_iii_deg": lon, "lat_deg": lat, "method": pr.method}
    else:
        from research_grade import run_research_grade, write_publication_bundle

        rg = run_research_grade(
            meas,
            nav=nav,
            cm_iii_deg=nav.cm_iii_deg,
            distance_au=nav.distance_au,
            channels=channels,
            injection_trials=12,
            mc_iter=24,
            seed=int(truth["seed"]) % 10000,
            max_fidelity=True,
            factory_mode=False,
            user_time_iso=truth["user_time_iso"],
            time_error_seconds=0.0,
            aperture_m=0.35,
            use_vlbi=True,
        )
        write_publication_bundle(out_dir / "research_grade.json", rg, extra={"truth": truth})
        lon = rg.lon_bias_corrected_deg
        lat = rg.lat_bias_corrected_deg
        result["research_grade"] = rg.to_dict()
        result["measured"] = {
            "lon_iii_deg": lon,
            "lat_deg": lat,
            "method": "research_bias_corrected",
        }

    dlon = wrap_diff(lon, truth["grs_lon_iii_deg"])
    dlat = float(lat) - float(truth["grs_lat_deg"])
    sky = sky_error_arcsec(dlon, dlat, float(truth["grs_lat_deg"]), float(truth["distance_au"]))
    recovery = {
        "dlon_deg": float(dlon),
        "dlat_deg": float(dlat),
        "sky_error_arcsec": float(sky),
        "target_0_1_arcsec": bool(sky <= 0.1),
        "target_0_25_arcsec": bool(sky <= 0.25),
        "target_0_5_arcsec": bool(sky <= 0.5),
        "target_1_arcsec": bool(sky <= 1.0),
        "grade": (
            "EXCELLENT_0.1"
            if sky <= 0.1
            else (
                "EXCELLENT"
                if sky <= 0.25
                else ("GOOD" if sky <= 0.5 else ("FAIR" if sky <= 1.0 else "POOR"))
            )
        ),
    }
    result["truth_recovery"] = recovery
    result["nav"] = {
        "xc": nav.xc,
        "yc": nav.yc,
        "a_eq_px": nav.a_eq_px,
        "truth_xc": truth["disk_xc"],
        "truth_yc": truth["disk_yc"],
        "truth_a": truth["disk_a_eq_px"],
        "d_xc_px": float(nav.xc - truth["disk_xc"]),
        "d_yc_px": float(nav.yc - truth["disk_yc"]),
        "d_a_px": float(nav.a_eq_px - truth["disk_a_eq_px"]),
    }
    (out_dir / "run_result.json").write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    return result


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Batch synthetic GRS proof suite")
    ap.add_argument("--n", type=int, default=60, help="number of synthetic runs (50–100)")
    ap.add_argument("--resolution", default="1080p", choices=["1080p", "4K", "8K"])
    ap.add_argument("--region", default="global")
    ap.add_argument("--out", default="", help="output root (default app/outputs/batch_prove_*)")
    ap.add_argument("--fast", action="store_true", help="precision engine only (recommended for N≥50)")
    ap.add_argument("--research", action="store_true", help="full research_grade each run (slow)")
    ap.add_argument("--seed0", type=int, default=None, help="optional base seed")
    args = ap.parse_args(argv)

    n = max(1, min(int(args.n), 200))
    app_dir = Path(__file__).resolve().parent
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    root = Path(args.out) if args.out else (app_dir / "outputs" / f"batch_prove_{stamp}")
    runs_dir = root / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)

    CONSOLE.info("=" * 64)
    CONSOLE.info(f"BATCH PROVE  N={n}  res={args.resolution}  fast={args.fast or not args.research}")
    CONSOLE.info(f"out={root}")

    # SPICE self-status / download
    spice_status: Dict[str, Any] = {}
    try:
        from spice_auto import ensure_kernels, selftest
        spice_status = selftest()
        (root / "spice_status.json").write_text(json.dumps(spice_status, indent=2, default=str))
        CONSOLE.ok(f"SPICE selftest ok={spice_status.get('ok')}")
    except Exception as e:
        spice_status = {"ok": False, "error": str(e)}
        CONSOLE.warn(f"SPICE selftest: {e}")
        (root / "spice_status.json").write_text(json.dumps(spice_status, indent=2))

    rows: List[Dict[str, Any]] = []
    skys: List[float] = []
    t0 = time.time()
    use_research = bool(args.research) and not args.fast

    for i in range(n):
        run_id = f"run_{i+1:04d}"
        out_i = runs_dir / run_id
        seed = None if args.seed0 is None else int(args.seed0) + i * 9973
        CONSOLE.info(f"—— [{i+1}/{n}] {run_id} ——")
        try:
            r = run_one(
                out_i,
                resolution=args.resolution,
                region=args.region,
                seed=seed,
                fast=bool(args.fast) or not use_research,
                use_research=use_research,
            )
            rec = r["truth_recovery"]
            sky = float(rec["sky_error_arcsec"])
            skys.append(sky)
            row = {
                "run": run_id,
                "ok": True,
                "seed": r["truth"]["seed"],
                "epoch": r["truth"]["user_time_iso"],
                "truth_lon": r["truth"]["grs_lon_iii_deg"],
                "truth_lat": r["truth"]["grs_lat_deg"],
                "meas_lon": r["measured"]["lon_iii_deg"],
                "meas_lat": r["measured"]["lat_deg"],
                "dlon_deg": rec["dlon_deg"],
                "dlat_deg": rec["dlat_deg"],
                "sky_error_arcsec": sky,
                "grade": rec["grade"],
                "target_0_1": rec["target_0_1_arcsec"],
                "disk_std": r["truth"].get("disk_intensity_std"),
                "png": r.get("png"),
            }
            CONSOLE.ok(
                f"{run_id}: Δsky={sky:.4f}\"  grade={rec['grade']}  "
                f"dlon={rec['dlon_deg']:.4f}° dlat={rec['dlat_deg']:.4f}°"
            )
        except Exception as e:
            CONSOLE.warn(f"{run_id} FAILED: {e}")
            traceback.print_exc()
            row = {"run": run_id, "ok": False, "error": str(e)}
        rows.append(row)
        # live partial save
        (root / "batch_partial.json").write_text(
            json.dumps({"completed": i + 1, "n": n, "rows": rows}, indent=2, default=str),
            encoding="utf-8",
        )

    elapsed = time.time() - t0
    ok_rows = [r for r in rows if r.get("ok")]
    n_ok = len(ok_rows)
    n_01 = sum(1 for r in ok_rows if r.get("target_0_1"))
    n_025 = sum(1 for r in ok_rows if float(r.get("sky_error_arcsec", 99)) <= 0.25)
    n_05 = sum(1 for r in ok_rows if float(r.get("sky_error_arcsec", 99)) <= 0.5)
    n_1 = sum(1 for r in ok_rows if float(r.get("sky_error_arcsec", 99)) <= 1.0)

    summary = {
        "created": datetime.now(timezone.utc).isoformat(),
        "n_requested": n,
        "n_ok": n_ok,
        "n_fail": n - n_ok,
        "resolution": args.resolution,
        "region": args.region,
        "mode": "research" if use_research else "fast_precision",
        "elapsed_s": elapsed,
        "sky_error_arcsec": {
            "mean": float(statistics.fmean(skys)) if skys else None,
            "median": float(statistics.median(skys)) if skys else None,
            "stdev": float(statistics.stdev(skys)) if len(skys) > 1 else 0.0,
            "p16": _percentile(skys, 16),
            "p84": _percentile(skys, 84),
            "p95": _percentile(skys, 95),
            "max": max(skys) if skys else None,
            "min": min(skys) if skys else None,
        },
        "pass_rates": {
            "le_0_1_arcsec": n_01 / n_ok if n_ok else 0.0,
            "le_0_25_arcsec": n_025 / n_ok if n_ok else 0.0,
            "le_0_5_arcsec": n_05 / n_ok if n_ok else 0.0,
            "le_1_0_arcsec": n_1 / n_ok if n_ok else 0.0,
            "n_le_0_1": n_01,
            "n_le_0_25": n_025,
            "n_le_0_5": n_05,
            "n_le_1_0": n_1,
        },
        "claim": {
            "target": "0.1 arcsec sky truth recovery on synthetic GRS",
            "median_meets_0_1": bool(skys and statistics.median(skys) <= 0.1),
            "p95_meets_0_25": bool(skys and _percentile(skys, 95) <= 0.25),
            "fraction_le_0_1": n_01 / n_ok if n_ok else 0.0,
        },
        "spice_status_ok": bool(spice_status.get("ok")),
        "output_dir": str(root),
        "rows": rows,
    }

    (root / "batch_summary.json").write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")

    # CSV
    csv_path = root / "batch_summary.csv"
    fields = [
        "run", "ok", "seed", "epoch", "truth_lon", "truth_lat", "meas_lon", "meas_lat",
        "dlon_deg", "dlat_deg", "sky_error_arcsec", "grade", "target_0_1", "disk_std", "png", "error",
    ]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)

    # human report
    se = summary["sky_error_arcsec"]
    pr = summary["pass_rates"]
    lines = [
        "GRS OBSERVATORY — BATCH SYNTHETIC PROOF REPORT",
        "=" * 50,
        f"N ok / requested : {n_ok} / {n}",
        f"Resolution       : {args.resolution}",
        f"Mode             : {summary['mode']}",
        f"Elapsed          : {elapsed:.1f} s",
        f"SPICE ready      : {summary['spice_status_ok']}",
        "",
        "TRUTH RECOVERY (sky arcsec)",
        f"  mean   = {se['mean']}",
        f"  median = {se['median']}",
        f"  stdev  = {se['stdev']}",
        f"  p16    = {se['p16']}",
        f"  p84    = {se['p84']}",
        f"  p95    = {se['p95']}",
        f"  min    = {se['min']}",
        f"  max    = {se['max']}",
        "",
        "PASS RATES",
        f"  ≤ 0.10″ : {pr['n_le_0_1']}/{n_ok}  ({100*pr['le_0_1_arcsec']:.1f}%)",
        f"  ≤ 0.25″ : {pr['n_le_0_25']}/{n_ok}  ({100*pr['le_0_25_arcsec']:.1f}%)",
        f"  ≤ 0.50″ : {pr['n_le_0_5']}/{n_ok}  ({100*pr['le_0_5_arcsec']:.1f}%)",
        f"  ≤ 1.00″ : {pr['n_le_1_0']}/{n_ok}  ({100*pr['le_1_0_arcsec']:.1f}%)",
        "",
        "CLAIM CHECK",
        f"  median ≤ 0.1″ : {summary['claim']['median_meets_0_1']}",
        f"  p95 ≤ 0.25″   : {summary['claim']['p95_meets_0_25']}",
        f"  fraction ≤0.1″: {summary['claim']['fraction_le_0_1']:.3f}",
        "",
        f"Output: {root}",
    ]
    report = "\n".join(lines)
    (root / "batch_report.txt").write_text(report, encoding="utf-8")
    print(report)
    CONSOLE.ok(f"Batch complete → {root}")
    return 0 if n_ok >= max(1, int(0.9 * n)) else 2


if __name__ == "__main__":
    raise SystemExit(main())

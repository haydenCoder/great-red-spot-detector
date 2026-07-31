#!/usr/bin/env python3
"""
Diagnose the actual failure modes of the published GRS measurement pipeline
on the synthetic validation suite.

This tool does NOT change any pipeline behaviour. It runs the full
measurement on every frame in the test campaign, and for each frame it
tallies which estimator was the closest to planted-centre truth and which
was the farthest. The output is a histogram that tells you:

  - On clear/mild frames, what fraction does the dark consensus get right
    vs. wrong vs. ambiguous?
  - On the 0.20-0.35 deg tail, which estimator is the right one and
    which estimator is pulling the consensus wrong?
  - When the dark methods split, does the redness lock really win
    (as the 6.6.1 audit claims)?

This is the foundation work for "improve the result" — until we have a
honest count of which estimator is wrong on which frame, we cannot tell
whether a new estimator is helping or hurting.

Usage:
    python3 tools/diagnose_failure_modes.py
    python3 tools/diagnose_failure_modes.py --out /tmp/diag.json
    python3 tools/diagnose_failure_modes.py --n 30   # 30 frames per tier
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR / "app"))


def _run_one(resolution: str, clarity: str, seed: int) -> Optional[Dict[str, Any]]:
    """Run a single synthetic measurement, return the per-method + headline."""
    from synthetic_hq import SynthSpec, generate
    from precision_engine import (
        fit_limb_nav, measure_grs_precision, NavState, to_mono,
    )
    import grs_complete_system as grs

    out_dir = Path("/tmp/grs_diag")
    out_dir.mkdir(parents=True, exist_ok=True)
    spec = SynthSpec(
        user_time_iso="",
        region="global",
        resolution_preset=resolution,
        random_time=True,
        seed=seed,
        mode="metrology",
        write_grs_crop=False,
    )
    try:
        _png, fit, truth = generate(spec, out_dir)
    except Exception as e:
        return None
    try:
        arr, _ = grs.read_fits(fit)
        img = np.asarray(arr, dtype=np.float64)
        # IMPORTANT: pass the full RGB image (or the raw 3D array).
        # Passing a single channel strips the colour information and
        # the redness estimator fails to fire, which is the dominant
        # dark-vs-colour fix path in the 6.6.1 consensus. We then
        # need an RGB-aware limb nav. Re-arrange to CHW if HWC.
        if img.ndim == 3 and img.shape[-1] == 3 and img.shape[0] != 3:
            img = np.moveaxis(img, -1, 0)  # HWC -> CHW
        # fit_limb_nav wants a 2D image for the disk fit. Use the
        # red channel (which is what jpa_10k and the precision path
        # also use for the limb fit, since R is the most
        # limb-darkening-stable for Jupiter).
        if img.ndim == 3 and img.shape[0] == 3:
            meas_2d = img[0]
        else:
            meas_2d = img
        nav = fit_limb_nav(meas_2d, cm_iii_deg=truth["cm_iii_deg"],
                            distance_au=truth["distance_au"])
        nav.cm_iii_deg = truth["cm_iii_deg"]
        nav.distance_au = truth["distance_au"]
        # lean=True skips the multi-scale verify + NN prior, so the
        # per-method breakdown we care about is the same as the full
        # path but cheaper. Pass the *RGB* image so redness fires.
        res = measure_grs_precision(
            img, cm_iii_deg=truth["cm_iii_deg"],
            distance_au=truth["distance_au"], nav=nav, quiet=True, lean=True,
        )
    except Exception:
        return None

    truth_lon = float(truth["grs_lon_iii_deg"])
    truth_lat = float(truth["grs_lat_deg"])
    pub_lon = float(res.lon_iii_deg)
    pub_lat = float(res.lat_deg)
    dlon = (pub_lon - truth_lon + 180) % 360 - 180
    dlat = pub_lat - truth_lat
    sky = math.hypot(dlon, dlat)

    per_method = {}
    methods = res.methods or {}
    for name, m in methods.items():
        if not isinstance(m, dict) or m.get("lon_iii_deg") is None:
            continue
        try:
            ml = float(m["lon_iii_deg"])
            mla = float(m.get("lat_deg", 0.0))
        except (TypeError, ValueError):
            continue
        m_dlon = (ml - truth_lon + 180) % 360 - 180
        m_dlat = mla - truth_lat
        m_sky = math.hypot(m_dlon, m_dlat)
        per_method[name] = {
            "lon_iii_deg": ml, "lat_deg": mla,
            "dlon_deg": m_dlon, "dlat_deg": m_dlat, "sky_arcsec_proxy": m_sky,
            "rejected": bool(m.get("rejected", False)),
            "score": float(m.get("score", 0.0) or 0.0),
        }

    # Also report against the seed (geometric centre) truth if available
    seed_lon = truth.get("grs_lon_seed_deg")
    seed_lat = truth.get("grs_lat_seed_deg")
    dlon_seed = dlat_seed = float("nan")
    if seed_lon is not None and seed_lat is not None:
        dlon_seed = (pub_lon - float(seed_lon) + 180) % 360 - 180
        dlat_seed = pub_lat - float(seed_lat)
    per_method_seed = {}
    if seed_lon is not None and seed_lat is not None:
        for name, m in methods.items():
            if not isinstance(m, dict) or m.get("lon_iii_deg") is None:
                continue
            try:
                ml = float(m["lon_iii_deg"])
                mla = float(m.get("lat_deg", 0.0))
            except (TypeError, ValueError):
                continue
            m_dlon = (ml - float(seed_lon) + 180) % 360 - 180
            m_dlat = mla - float(seed_lat)
            per_method_seed[name] = {
                "dlon_deg": m_dlon, "dlat_deg": m_dlat,
                "sky_arcsec_proxy": math.hypot(m_dlon, m_dlat),
            }

    return {
        "case": {"resolution": resolution, "clarity": clarity, "seed": seed},
        "truth": {"lon_iii_deg": truth_lon, "lat_deg": truth_lat,
                  "lon_seed_deg": seed_lon, "lat_seed_deg": seed_lat},
        "publish": {"lon_iii_deg": pub_lon, "lat_deg": pub_lat,
                    "dlon_deg": dlon, "dlat_deg": dlat, "sky_arcsec_proxy": sky,
                    "dlon_seed_deg": dlon_seed, "dlat_seed_deg": dlat_seed,
                    "method": res.method,
                    "quality": float(res.quality),
                    "lat_kind": res.lat_kind},
        "per_method": per_method,
        "per_method_seed": per_method_seed,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Diagnose GRS measurement failure modes.")
    ap.add_argument("--n", type=int, default=20,
                    help="frames per (resolution, clarity) bin")
    ap.add_argument("--out", default="/tmp/grs_diag.json",
                    help="output JSON path")
    ap.add_argument("--resolutions", nargs="+",
                    default=["540p", "720p", "1080p"],
                    help="resolutions to test")
    ap.add_argument("--clarities", nargs="+",
                    default=["clear", "mild"],
                    help="seeing tiers to test (excluded: blurry, very-blurry)")
    args = ap.parse_args()

    rows: List[Dict[str, Any]] = []
    t0 = time.time()
    for res in args.resolutions:
        for clarity in args.clarities:
            for k in range(args.n):
                seed = hash((res, clarity, k)) % (2 ** 31)
                r = _run_one(res, clarity, seed)
                if r is None:
                    continue
                rows.append(r)
                if (len(rows)) % 5 == 0:
                    print(f"  {len(rows)} done ({time.time() - t0:.1f}s)", flush=True)

    print(f"Total frames: {len(rows)}")
    if not rows:
        print("No frames completed; check synthetic_hq and pipeline imports.")
        return 1

    # ------------------------------------------------------------------
    # Aggregate: per-tier, per-method: median |sky|, fraction within 0.2°
    # ------------------------------------------------------------------
    methods_seen: set = set()
    for r in rows:
        methods_seen.update(r["per_method"].keys())

    summary = {
        "n_frames": len(rows),
        "by_tier": {},
        "estimator_table": {},
        "failure_modes": {},
    }
    for res in args.resolutions:
        for clarity in args.clarities:
            tier = f"{res}/{clarity}"
            tier_rows = [r for r in rows
                         if r["case"]["resolution"] == res
                         and r["case"]["clarity"] == clarity]
            if not tier_rows:
                continue
            skys = sorted(abs(r["publish"]["sky_arcsec_proxy"]) for r in tier_rows)
            n_within_02 = sum(1 for r in tier_rows
                              if abs(r["publish"]["sky_arcsec_proxy"]) < 0.2)
            n_within_05 = sum(1 for r in tier_rows
                              if abs(r["publish"]["sky_arcsec_proxy"]) < 0.5)
            summary["by_tier"][tier] = {
                "n": len(tier_rows),
                "publish_median_sky_deg": skys[len(skys) // 2],
                "publish_p90_sky_deg": skys[int(len(skys) * 0.9)] if skys else 0.0,
                "publish_pct_within_0p2_deg": n_within_02 / len(tier_rows) * 100,
                "publish_pct_within_0p5_deg": n_within_05 / len(tier_rows) * 100,
            }

    # Per-estimator table: for each method, for each tier, median |sky|
    for method_name in sorted(methods_seen):
        row = {}
        for res in args.resolutions:
            for clarity in args.clarities:
                tier = f"{res}/{clarity}"
                tier_rows = [r for r in rows
                             if r["case"]["resolution"] == res
                             and r["case"]["clarity"] == clarity
                             and method_name in r["per_method"]]
                if not tier_rows:
                    continue
                skys = sorted(abs(m["sky_arcsec_proxy"])
                              for m in (r["per_method"][method_name]
                                        for r in tier_rows))
                row[tier] = {
                    "n": len(tier_rows),
                    "median_sky_deg": skys[len(skys) // 2],
                    "p90_sky_deg": skys[int(len(skys) * 0.9)] if skys else 0.0,
                }
        summary["estimator_table"][method_name] = row

    # ------------------------------------------------------------------
    # Failure-mode analysis
    # ------------------------------------------------------------------
    # For each frame, identify which estimator was closest to truth.
    # Categorise: "dark_right", "colour_right", "tie", "all_wrong".
    cat_counts = Counter()
    dark_split_count = 0
    dark_split_rescued_by_redness = 0
    dark_split_rescued_by_template = 0
    dark_split_rescued_by_moment = 0
    for r in rows:
        methods = r["per_method"]
        if not methods:
            continue
        ranked = sorted(methods.items(),
                        key=lambda kv: abs(kv[1]["sky_arcsec_proxy"]))
        best_name, best = ranked[0]
        pub_sky = abs(r["publish"]["sky_arcsec_proxy"])
        # Did the publish pick the right one?
        pub_method = r["publish"].get("method", "?")
        if best_name in ("template", "moment") and best_name in methods:
            cat_counts["dark_right"] += 1
        elif best_name == "redness":
            cat_counts["colour_right"] += 1
        else:
            cat_counts["other"] += 1
        # Dark split: template and moment disagree by > 12 deg
        if "template" in methods and "moment" in methods:
            tmpl_dlon = abs(methods["template"]["dlon_deg"])
            mom_dlon = abs(methods["moment"]["dlon_deg"])
            tmpl_sky = methods["template"]["sky_arcsec_proxy"]
            mom_sky = methods["moment"]["sky_arcsec_proxy"]
            if abs(tmpl_dlon - mom_dlon) > 12.0:
                dark_split_count += 1
                # Who is right and which way did publish go?
                if tmpl_sky < mom_sky and "redness" in methods:
                    redness_sky = abs(methods["redness"]["sky_arcsec_proxy"])
                    if redness_sky < tmpl_sky:
                        dark_split_rescued_by_redness += 1
                elif mom_sky < tmpl_sky and "redness" in methods:
                    redness_sky = abs(methods["redness"]["sky_arcsec_proxy"])
                    if redness_sky < mom_sky:
                        dark_split_rescued_by_redness += 1

    summary["failure_modes"] = {
        "best_estimator_counts": dict(cat_counts),
        "dark_split_count": dark_split_count,
        "dark_split_rescued_by_redness": dark_split_rescued_by_redness,
        "dark_split_total_pct": (dark_split_count / max(len(rows), 1)) * 100,
    }

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2, default=str), encoding="utf-8")
    print(f"\nWrote {out_path}")

    # Human-readable summary
    print("\n=== Publish agreement by tier ===")
    for tier, d in summary["by_tier"].items():
        print(f"  {tier:18s}: n={d['n']:3d}  median={d['publish_median_sky_deg']:.4f}°  "
              f"p90={d['publish_p90_sky_deg']:.4f}°  "
              f"<0.2°: {d['publish_pct_within_0p2_deg']:.1f}%  "
              f"<0.5°: {d['publish_pct_within_0p5_deg']:.1f}%")
    print("\n=== Per-estimator median |sky| (deg) ===")
    for m, row in summary["estimator_table"].items():
        cells = []
        for tier in sorted(row.keys()):
            cells.append(f"{tier}={row[tier]['median_sky_deg']:.3f}")
        print(f"  {m:18s}  n={list(row.values())[0]['n']:3d}  " + "  ".join(cells))
    print("\n=== Failure modes ===")
    print(f"  best estimator: {summary['failure_modes']['best_estimator_counts']}")
    print(f"  dark split: {dark_split_count} frames "
          f"({summary['failure_modes']['dark_split_total_pct']:.1f}%)")
    print(f"  dark split rescued by redness: {dark_split_rescued_by_redness} "
          f"({dark_split_rescued_by_redness / max(dark_split_count, 1) * 100:.0f}% of splits)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""
Limb / multi-mode validation harness for Jupiter Great Red Spot Detector.

Generates synthetic GRS near the limb (large lon_rel from CM), runs SOTA consensus
with a pipeline seed, and checks we do not lock onto CM with SOTA_EXCELLENT.

Usage:
  cd app && python3 limb_validation.py --n 5
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any, Dict, List

APP = Path(__file__).resolve().parent
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


def _sky_ok(pkg: Dict[str, Any]) -> float:
    tr = pkg.get("truth_recovery") or {}
    h = pkg.get("headline") or {}
    for k in ("sky_error_arcsec", "truth_recovery_sky_arcsec"):
        if tr.get(k) is not None:
            return float(tr[k])
        if h.get(k) is not None:
            return float(h[k])
    return float("nan")


def run_one(out_root: Path, seed: int, limb_lon_rel: float = 75.0) -> Dict[str, Any]:
    from precision_engine import wrap_diff
    from desktop_pipeline import run_synthetic_full
    import os

    os.environ["GRS_SYNTH_SEED"] = str(seed)
    # Force GRS near limb (this was previously unused — critical for CM-lock tests)
    os.environ["GRS_LIMB_LON_REL"] = str(float(limb_lon_rel))
    try:
        # Full desktop stack (same as UI)
        pkg = run_synthetic_full(
            out_root,
            region="global",
            resolution="1080p",
            factory_mode=False,
            use_vlbi=True,
            use_nn=False,
            nasa=False,
            process_after=True,
            mc_iter=20,
            injection_trials=8,
        )
    finally:
        os.environ.pop("GRS_LIMB_LON_REL", None)
    truth = pkg.get("truth") or {}
    h = pkg.get("headline") or {}
    sota = pkg.get("sota") or {}
    tlon = truth.get("grs_lon_iii_deg")
    tlat = truth.get("grs_lat_deg")
    cm = truth.get("cm_iii_deg") or h.get("cm_iii_deg")
    slon = sota.get("lon_iii_deg") or h.get("sota_lon_iii_deg") or h.get("gold_lon_iii_deg")
    grade = sota.get("quality_grade") or h.get("sota_quality") or ""
    d_truth = abs(wrap_diff(float(slon), float(tlon))) if slon is not None and tlon is not None else 999.0
    d_cm = abs(wrap_diff(float(slon), float(cm))) if slon is not None and cm is not None else 999.0
    # Fail if claims EXCELLENT while far from truth, or locks CM when GRS is off-CM
    flags = list(sota.get("quality_flags") or [])
    fail = False
    reasons = []
    if "EXCELLENT" in str(grade) and d_truth > 15:
        fail = True
        reasons.append("false_excellent")
    if d_cm < 8 and d_truth > 40:
        fail = True
        reasons.append("cm_lock_while_truth_far")
    return {
        "seed": seed,
        "truth_lon": tlon,
        "sota_lon": slon,
        "cm": cm,
        "d_truth_deg": d_truth,
        "d_cm_deg": d_cm,
        "grade": grade,
        "sky_error_arcsec": _sky_ok(pkg),
        "fail": fail,
        "reasons": reasons,
        "flags": flags[:8],
        "output_dir": pkg.get("output_dir"),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Limb / multi-mode validation")
    ap.add_argument("--n", type=int, default=3)
    ap.add_argument("--out", default="")
    args = ap.parse_args(argv)
    out = Path(args.out) if args.out else (APP / "outputs" / "limb_validation")
    out.mkdir(parents=True, exist_ok=True)
    rows: List[Dict[str, Any]] = []
    for i in range(max(1, args.n)):
        print(f"limb_validation run {i+1}/{args.n}…")
        try:
            rows.append(run_one(out, seed=900_000 + i * 97))
        except Exception as e:
            rows.append({"seed": 900_000 + i * 97, "fail": True, "error": str(e)})
    n_fail = sum(1 for r in rows if r.get("fail"))
    report = {
        "n": len(rows),
        "n_fail": n_fail,
        "pass": n_fail == 0,
        "rows": rows,
    }
    (out / "limb_validation_report.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"pass": report["pass"], "n_fail": n_fail, "n": len(rows)}, indent=2))
    return 0 if report["pass"] else 3


if __name__ == "__main__":
    raise SystemExit(main())

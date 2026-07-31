#!/usr/bin/env python3
"""
Per-method audit on the 100-case matrix.

Runs each frame through the published measurement path AND collects the
per-method lat/lon results. Reports the per-estimator bias and scatter vs
the planted geometric truth, so we can see *which* method is wrong, not
just that the published hybrid is.

This is the foundation for any honest change to the published path: the
audit must show that the *new* method is a strict improvement on every
case in the 100-case matrix, not just a tighter median.
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
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

# Single-threaded BLAS so the per-case process pool is the only parallelism
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_v, "1")


def run_per_method(case: dict) -> dict:
    """Render one frame, run all four primary methods individually, return
    the per-method lon/lat and the published hybrid result.

    Truth: `grs_*_seed_deg` is the planted geometric centre (test gate).
    """
    import datetime as dt
    from synthetic_hq import SynthSpec, generate
    from precision_engine import (
        fit_limb_nav, measure_grs_precision, sky_error_arcsec, wrap_diff,
        _template_match_grs, _moment_mask_grs, _redness_grs, _map_dark_centroid,
        GRS_LAT0, make_cylindrical, to_mono,
    )
    try:
        from accuracy_campaign import run_one as _run_one
    except Exception:
        _run_one = None

    t0 = time.time()
    try:
        with tempfile.TemporaryDirectory(prefix="grs_pmaudit_") as d:
            spec_kw = dict(
                region="global",
                resolution_preset=case["resolution"],
                random_time=True,
                seed=int(case["seed"]),
                mode="metrology",
                write_grs_crop=False,
            )
            if case.get("seeing_arcsec") is not None:
                spec_kw["seeing_fwhm_arcsec"] = float(case["seeing_arcsec"])
            if case.get("noise_rms") is not None:
                spec_kw["noise_rms"] = float(case["noise_rms"])
            png, _fit, truth = generate(SynthSpec(**spec_kw), Path(d))
            img = np.asarray(Image.open(png), dtype=np.float64) / 255.0

        nav = fit_limb_nav(
            img, cm_iii_deg=truth["cm_iii_deg"], distance_au=truth["distance_au"]
        )
        nav.cm_iii_deg = truth["cm_iii_deg"]
        nav.distance_au = truth["distance_au"]
        nav.sub_lat_deg = float(truth.get("sub_obs_lat_deg") or 0.0)
        nav.north_pa_deg = float(truth.get("north_pa_deg") or 0.0)

        methods_run: Dict[str, Dict[str, Any]] = {}
        for name, fn in (
            ("template", lambda: _template_match_grs(make_cylindrical(img, nav, width=2400, height=1200), nav)),
            ("map_dark", lambda: _map_dark_centroid(make_cylindrical(img, nav, width=2400, height=1200), nav)),
            ("moment", lambda: _moment_mask_grs(img, nav)),
            ("redness", lambda: _redness_grs(img, nav)),
        ):
            try:
                m = fn()
                lon_iii = float(m["lon_iii_deg"])
                lat = float(m["lat_deg"])
                methods_run[name] = {
                    "lon_iii_deg": lon_iii,
                    "lat_deg": lat,
                    "score": float(m.get("score", float("nan"))),
                    "method": m.get("method", name),
                }
            except Exception as e:
                methods_run[name] = {"error": f"{type(e).__name__}: {e}"}

        # The published hybrid: redness_lon + moment_lat  (v6.6.1)
        pub_lon = methods_run.get("redness", {}).get("lon_iii_deg")
        pub_lat = methods_run.get("moment", {}).get("lat_deg")

        # The v6.6.2 published path: redness_lon + redness_lat
        v662_lon = methods_run.get("redness", {}).get("lon_iii_deg")
        v662_lat = methods_run.get("redness", {}).get("lat_deg")

        out = {
            "case_id": case["case_id"],
            "idx": case["idx"],
            "stratum": case["stratum"],
            "resolution": case["resolution"],
            "seeing_arcsec": case["seeing_arcsec"],
            "noise_rms": case["noise_rms"],
            "seed": case["seed"],
            "lon_truth_geom": float(truth["grs_lon_seed_deg"]),
            "lat_truth_geom": float(truth["grs_lat_seed_deg"]),
            "lon_truth_bary": float(truth["grs_lon_iii_deg"]),
            "lat_truth_bary": float(truth["grs_lat_deg"]),
            "distance_au": float(truth["distance_au"]),
            "methods": methods_run,
            "published_hybrid_lon": pub_lon,
            "published_hybrid_lat": pub_lat,
            "v662_lon": v662_lon,
            "v662_lat": v662_lat,
            "secs": time.time() - t0,
            "ok": True,
        }
        return out
    except Exception as e:
        return {
            "case_id": case["case_id"], "idx": case["idx"], "ok": False,
            "error": f"{type(e).__name__}: {e}", "secs": time.time() - t0,
        }


def summarise(rows: List[dict]) -> dict:
    import statistics as st
    ok = [r for r in rows if r.get("ok")]
    out: Dict[str, Any] = {"n": len(rows), "n_ok": len(ok)}
    if not ok:
        return out

    method_names = ("template", "map_dark", "moment", "redness", "published_hybrid", "v662")
    for m in method_names:
        dlons = []
        dlats = []
        abs_dlons = []
        abs_dlats = []
        for r in ok:
            ms = r.get("methods", {}) or {}
            if m == "published_hybrid":
                lon = r.get("published_hybrid_lon")
                lat = r.get("published_hybrid_lat")
            elif m == "v662":
                lon = r.get("v662_lon")
                lat = r.get("v662_lat")
            else:
                entry = ms.get(m) or {}
                if "error" in entry:
                    continue
                lon = entry.get("lon_iii_deg")
                lat = entry.get("lat_deg")
            if lon is None or lat is None or not math.isfinite(lon) or not math.isfinite(lat):
                continue
            dlon = wrap_diff_audit(lon, r["lon_truth_geom"])
            dlat = lat - r["lat_truth_geom"]
            dlons.append(dlon)
            dlats.append(dlat)
            abs_dlons.append(abs(dlon))
            abs_dlats.append(abs(dlat))
        if not dlons:
            out[m] = {"n": 0}
            continue
        out[m] = {
            "n": len(dlons),
            "dlon_mean": st.fmean(dlons), "dlon_median": st.median(dlons), "dlon_pstdev": st.pstdev(dlons) if len(dlons) > 1 else 0.0,
            "dlat_mean": st.fmean(dlats), "dlat_median": st.median(dlats), "dlat_pstdev": st.pstdev(dlats) if len(dlats) > 1 else 0.0,
            "abs_dlon_mean": st.fmean(abs_dlons), "abs_dlon_median": st.median(abs_dlons),
            "abs_dlon_p90": st.quantiles(abs_dlons, n=10)[-1] if len(abs_dlons) >= 10 else max(abs_dlons),
            "abs_dlon_max": max(abs_dlons),
            "abs_dlat_mean": st.fmean(abs_dlats), "abs_dlat_median": st.median(abs_dlats),
            "abs_dlat_p90": st.quantiles(abs_dlats, n=10)[-1] if len(abs_dlats) >= 10 else max(abs_dlats),
            "abs_dlat_max": max(abs_dlats),
            "within_1deg": sum(1 for i in range(len(dlons)) if abs_dlons[i] <= 1.0 and abs_dlats[i] <= 1.0) / len(dlons),
        }
    return out


def wrap_diff_audit(a: float, b: float) -> float:
    return float((a - b + 180.0) % 360.0 - 180.0)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--matrix", default=str(ROOT / "tests" / "test_resolution_seeing_100.py"))
    ap.add_argument("--n", type=int, default=0, help="limit; 0 = all 100")
    ap.add_argument("--out", default="runs/per_method_audit.jsonl")
    ap.add_argument("--summary", default="runs/per_method_audit.summary.json")
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2)))
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()

    # import the MATRIX constant from the test module
    sys.path.insert(0, str(Path(args.matrix).parent))
    import importlib.util
    spec = importlib.util.spec_from_file_location("rs100", args.matrix)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    matrix = mod.MATRIX
    if args.n > 0:
        matrix = matrix[: args.n]

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    cache: Dict[int, dict] = {}
    if args.resume and out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
                if rec.get("ok"):
                    cache[int(rec["idx"])] = rec
            except Exception:
                pass

    todo = [c for c in matrix if c["idx"] not in cache]
    print(f"[per_method_audit] {len(cache)} cached, {len(todo)} to run", flush=True)
    rows = list(cache.values())
    if todo:
        from concurrent.futures import ProcessPoolExecutor, as_completed
        with out.open("a", encoding="utf-8") as fh, \
                ProcessPoolExecutor(max_workers=args.workers) as ex:
            futs = {ex.submit(run_per_method, c): c for c in todo}
            t0 = time.time()
            for i, fut in enumerate(as_completed(futs), 1):
                rec = fut.result()
                rows.append(rec)
                if rec.get("ok"):
                    cache[int(rec["idx"])] = rec
                fh.write(json.dumps(rec) + "\n")
                fh.flush()
                if i % 5 == 0 or i == len(todo):
                    el = time.time() - t0
                    rate = i / max(el, 1e-9)
                    eta = (len(todo) - i) / max(rate, 1e-9)
                    print(f"[per_method_audit] {i}/{len(todo)} rate={rate*60:.1f}/min eta={eta/60:.1f}m", flush=True)

    s = summarise(rows)
    print(json.dumps(s, indent=2))
    Path(args.summary).write_text(json.dumps(s, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

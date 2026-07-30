#!/usr/bin/env python3
"""
Detailed audit test for 1000 pictures: small->big, blurry->clear.
Uses synthetics as proxy (real downloads limited).
Runs matrix: res {540p,720p,1080p} x quality {clear,mild,blurry,vblurry} ~1000 cases total.
Scores against planted geometric seed (truth).
Reports per cell + overall:
  within_0.2, within_0.5, max errors, medians etc.
Goal: clear cases <0.2 guaranteed; all <0.5 .
Resumable to runs/detailed_audit_1000.jsonl
"""
from __future__ import annotations
import argparse
import json
import os
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
for _p in (APP,):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

CACHE_PATH = ROOT / "runs" / "detailed_audit_1000.jsonl"
SEED0 = 42_000_000
STRIDE = 7919

# Seeing tiers and per-cell target (clear prefer <0.2; all guarantee <0.5)
QUALITY: List[Tuple[str, float, float]] = [
    ("clear",   0.38, 0.2),   # target clear <0.2
    ("mild",    0.80, 0.5),
    ("blurry",  1.60, 0.5),
    ("vblurry", 2.40, 0.5),
]
# Resolution tiers large->small, total ~1000
RES_COUNTS: List[Tuple[str, int]] = [
    ("1080p", 280),
    ("720p",  320),
    ("540p",  400),
]
assert sum(n for _, n in RES_COUNTS) == 1000

def _noise(seeing: float) -> float:
    return float(min(0.035, 0.004 + 0.006 * seeing))

def build_matrix() -> List[Dict[str, Any]]:
    cases: List[Dict[str, Any]] = []
    idx = 0
    for res, total in RES_COUNTS:
        nq = len(QUALITY)
        per_q = total // nq
        for qname, seeing, limit in QUALITY:
            for _ in range(per_q):
                cases.append({
                    "case_id": f"{res}_{qname}#{idx:05d}",
                    "idx": idx,
                    "resolution": res,
                    "quality": qname,
                    "seeing_arcsec": seeing,
                    "noise_rms": _noise(seeing),
                    "limit_deg": limit,
                    "seed": SEED0 + idx * STRIDE,
                })
                idx += 1
    # pad if needed
    while len(cases) < 1000:
        last = cases[-1]
        cases.append({**last, "idx": idx, "case_id": f"pad_{idx}", "seed": SEED0 + idx*STRIDE})
        idx += 1
    return cases[:1000]

MATRIX = build_matrix()

def run_case(case: Dict[str, Any]) -> Dict[str, Any]:
    import numpy as np
    from PIL import Image
    from precision_engine import fit_limb_nav, measure_grs_precision, wrap_diff, sky_error_arcsec
    from synthetic_hq import SynthSpec, generate

    t0 = time.time()
    out = dict(case)
    try:
        with tempfile.TemporaryDirectory(prefix="grs_det_") as d:
            png, _fit, truth = generate(SynthSpec(
                region="global", resolution_preset=case["resolution"],
                random_time=True, seed=case["seed"], mode="metrology",
                write_grs_crop=False, seeing_fwhm_arcsec=case["seeing_arcsec"],
                noise_rms=case["noise_rms"],
            ), Path(d))
            img = np.asarray(Image.open(png), dtype=np.float64) / 255.0
        nav = fit_limb_nav(img, cm_iii_deg=truth["cm_iii_deg"],
                           distance_au=truth["distance_au"])
        nav.cm_iii_deg = truth["cm_iii_deg"]
        nav.distance_au = truth["distance_au"]
        res = measure_grs_precision(img, cm_iii_deg=nav.cm_iii_deg,
                                    distance_au=nav.distance_au, nav=nav,
                                    quiet=True, lean=True,
                                    map_width=1200, map_height=600)
        dlon = wrap_diff(res.lon_iii_deg, truth["grs_lon_seed_deg"])
        dlat = res.lat_deg - truth["grs_lat_seed_deg"]
        out.update({
            "ok": True,
            "dlon": float(dlon), "dlat": float(dlat),
            "abs_dlon": abs(float(dlon)), "abs_dlat": abs(float(dlat)),
            "sky_arcsec": float(sky_error_arcsec(dlon, dlat, truth["grs_lat_seed_deg"], truth["distance_au"])),
            "lon_meas": float(res.lon_iii_deg), "lat_meas": float(res.lat_deg),
            "method": res.method, "quality_flag": float(res.quality),
            "disk_a_px": float(truth["disk_a_eq_px"]),
            "secs": time.time() - t0,
        })
    except Exception as e:
        out.update({"ok": False, "error": f"{type(e).__name__}: {e}",
                    "secs": time.time() - t0})
    return out

def _load_cache() -> Dict[int, Dict[str, Any]]:
    done: Dict[int, Dict[str, Any]] = {}
    if CACHE_PATH.exists():
        for line in CACHE_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line: continue
            try:
                rec = json.loads(line)
                done[int(rec["idx"])] = rec
            except Exception:
                continue
    return done

def build_results(force: bool = False, workers: int = 0) -> List[Dict[str, Any]]:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    cache = {} if force else _load_cache()

    def _stale(rec, case):
        return (not rec.get("ok")
                or str(rec.get("resolution")) != case["resolution"]
                or str(rec.get("quality")) != case["quality"]
                or int(rec.get("seed", -1)) != case["seed"])

    todo = [c for c in MATRIX if _stale(cache.get(c["idx"], {}), c)]
    if not todo:
        return [cache[c["idx"]] for c in MATRIX]

    nw = workers or max(1, (os.cpu_count() or 2))
    print(f"\n[detailed1000] building {len(todo)}/{len(MATRIX)} on {nw} workers", flush=True)
    with CACHE_PATH.open("a", encoding="utf-8") as fh, \
            ProcessPoolExecutor(max_workers=nw) as ex:
        futs = {ex.submit(run_case, c): c for c in todo}
        t0 = time.time()
        for i, fut in enumerate(as_completed(futs), 1):
            rec = fut.result()
            cache[rec["idx"]] = rec
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            if i % 50 == 0 or i == len(todo):
                el = time.time() - t0
                rate = i / max(el, 1e-9)
                eta = (len(todo) - i) / max(rate, 1e-9)
                ok = sum(1 for r in cache.values() if r.get("ok"))
                w02 = sum(1 for r in cache.values() if r.get("ok") and r["abs_dlon"] <= 0.2 and r["abs_dlat"] <= 0.2)
                w05 = sum(1 for r in cache.values() if r.get("ok") and r["abs_dlon"] <= 0.5 and r["abs_dlat"] <= 0.5)
                print(f"[detailed1000] {ok}/{len(MATRIX)} done | "
                      f"{rate*60:.0f}/min eta {eta/60:.0f}m | "
                      f"<=0.2 {w02/max(ok,1)*100:.1f}% <=0.5 {w05/max(ok,1)*100:.1f}%", flush=True)
    return [cache[c["idx"]] for c in MATRIX]

def summarise(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    import statistics as st
    ok = [r for r in rows if r.get("ok")]
    if not ok:
        return {"n": len(rows), "n_ok": 0}

    def pct(a, p):
        a = sorted(a)
        k = (len(a) - 1) * p / 100.0
        f, c = int(k), min(int(k) + 1, len(a) - 1)
        return a[f] * (c - k) + a[c] * (k - f) if f != c else a[f]

    dl = [r["abs_dlon"] for r in ok]
    db = [r["abs_dlat"] for r in ok]
    by = {}
    for res in ("540p", "720p", "1080p"):
        for q in ("clear", "mild", "blurry", "vblurry"):
            tr = [r for r in ok if r["resolution"] == res and r["quality"] == q]
            if not tr: continue
            by[f"{res}_{q}"] = {
                "n": len(tr),
                "lon_med": pct([r["abs_dlon"] for r in tr], 50),
                "lon_p90": pct([r["abs_dlon"] for r in tr], 90),
                "lon_max": max(r["abs_dlon"] for r in tr),
                "lat_med": pct([r["abs_dlat"] for r in tr], 50),
                "lat_max": max(r["abs_dlat"] for r in tr),
                "within_02": sum(1 for r in tr if r["abs_dlon"]<=0.2 and r["abs_dlat"]<=0.2) / len(tr),
                "within_05": sum(1 for r in tr if r["abs_dlon"]<=0.5 and r["abs_dlat"]<=0.5) / len(tr),
            }
    return {
        "n": len(rows), "n_ok": len(ok),
        "lon": {"med": pct(dl, 50), "p90": pct(dl, 90), "p99": pct(dl, 99), "max": max(dl)},
        "lat": {"med": pct(db, 50), "p90": pct(db, 90), "p99": pct(db, 99), "max": max(db)},
        "within_02": sum(1 for r in ok if r["abs_dlon"] <= 0.2 and r["abs_dlat"] <= 0.2) / len(ok),
        "within_05": sum(1 for r in ok if r["abs_dlon"] <= 0.5 and r["abs_dlat"] <= 0.5) / len(ok),
        "clear_within_02": sum(1 for r in ok if r["quality"]=="clear" and r["abs_dlon"]<=0.2 and r["abs_dlat"]<=0.2) / max(1, sum(1 for r in ok if r["quality"]=="clear")),
        "by_cell": by,
    }

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2)))
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--summary", action="store_true")
    args = ap.parse_args()

    if args.summary:
        rows = list(_load_cache().values())
    else:
        rows = build_results(force=args.force, workers=args.workers)
    s = summarise(rows)
    print("\n" + json.dumps(s, indent=2))
    # also write summary
    (CACHE_PATH.parent / "detailed_audit_1000.summary.json").write_text(json.dumps(s, indent=2))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())

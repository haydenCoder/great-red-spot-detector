#!/usr/bin/env python3
"""
Deep audit harness — large-N (target 7000) accuracy sweep across resolution
(large -> small) and seeing (clear -> blurry), using the LEAN measurement path
for throughput.

Why lean: the full measurement re-runs the whole estimator at 2 reduced
resolutions (verify_grs_detection) and blends a neural prior, neither of which
changes the physics consensus we are auditing here. Skipping them (lean=True)
cuts per-frame cost ~2-3x with no effect on the reported lon/lat. The published
product path still uses the full measurement; this characterises the core
physics logic at scale.

Matrix: resolution {540p, 720p, 1080p} x seeing {clear, mild, blurry, vblurry},
noise scaled with seeing, GRS planted at the literature latitude and scored
against the planted geometric centre (exact truth, no ephemeris jitter).

Targets: every frame < 0.5 deg (the guarantee); clearest < 0.2 deg (preference).

Resumable: streams to runs/deep_audit_7000.jsonl. Run in batches:
    python tools/deep_audit_7000.py            # build all missing (resumable)
    python tools/deep_audit_7000.py --summary  # just print stats from cache
"""
from __future__ import annotations

import argparse
import json
import math
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

CACHE_PATH = ROOT / "runs" / "deep_audit_7000.jsonl"
SEED0 = 7_000_000
STRIDE = 7919

# Seeing tiers (arcsec FWHM) and their per-case gate (deg). User target: all
# < 0.5; clearest (clear) preference < 0.2.
QUALITY: List[Tuple[str, float, float]] = [
    ("clear",   0.38, 0.5),
    ("mild",    0.80, 0.5),
    ("blurry",  1.60, 0.5),
    ("vblurry", 2.40, 0.5),
]
# Resolution tiers (large -> small) with case counts. Weighted toward the fast
# smaller frames so 7000 completes in a few hours on a 2-vCPU box.
RES_COUNTS: List[Tuple[str, int]] = [
    ("1080p", 1000),   # large  (1920x1080, ~454px disk)
    ("720p",  2200),   # mid    (1280x720,  ~302px disk)
    ("540p",  3800),   # small  (960x540,   ~227px disk)
]
assert sum(n for _, n in RES_COUNTS) == 7000, sum(n for _, n in RES_COUNTS)


def _noise(seeing: float) -> float:
    return float(min(0.035, 0.004 + 0.006 * seeing))


def build_matrix() -> List[Dict[str, Any]]:
    cases: List[Dict[str, Any]] = []
    idx = 0
    for res, _ in RES_COUNTS:
        for qname, seeing, limit in QUALITY:
            n = next(c for r, c in RES_COUNTS if r == res) // len(QUALITY)
            for _ in range(n):
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
    return cases


MATRIX = build_matrix()


def run_case(case: Dict[str, Any]) -> Dict[str, Any]:
    import numpy as np
    from PIL import Image
    from precision_engine import fit_limb_nav, measure_grs_precision, wrap_diff
    from synthetic_hq import SynthSpec, generate

    t0 = time.time()
    out = dict(case)
    try:
        with tempfile.TemporaryDirectory(prefix="grs_deep_") as d:
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
            "sky_arcsec": float(__import__("precision_engine").sky_error_arcsec(
                dlon, dlat, truth["grs_lat_seed_deg"], truth["distance_au"])),
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
            if not line:
                continue
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
    print(f"\n[deep7000] building {len(todo)}/{len(MATRIX)} on {nw} workers", flush=True)
    with CACHE_PATH.open("a", encoding="utf-8") as fh, \
            ProcessPoolExecutor(max_workers=nw) as ex:
        futs = {ex.submit(run_case, c): c for c in todo}
        t0 = time.time()
        for i, fut in enumerate(as_completed(futs), 1):
            rec = fut.result()
            cache[rec["idx"]] = rec
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            if i % 200 == 0 or i == len(todo):
                el = time.time() - t0
                rate = i / max(el, 1e-9)
                eta = (len(todo) - i) / max(rate, 1e-9)
                ok = sum(1 for r in cache.values() if r.get("ok"))
                w5 = sum(1 for r in cache.values() if r.get("ok")
                         and r["abs_dlon"] <= 0.5 and r["abs_dlat"] <= 0.5)
                print(f"[deep7000] {ok}/{len(MATRIX)} done | "
                      f"{rate*60:.0f}/min eta {eta/60:.0f}m | "
                      f"<=0.5deg {w5/max(ok,1)*100:.1f}%", flush=True)
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
            if not tr:
                continue
            by[f"{res}_{q}"] = {
                "n": len(tr),
                "lon_med": pct([r["abs_dlon"] for r in tr], 50),
                "lon_p90": pct([r["abs_dlon"] for r in tr], 90),
                "lon_max": max(r["abs_dlon"] for r in tr),
                "lat_med": pct([r["abs_dlat"] for r in tr], 50),
                "lat_max": max(r["abs_dlat"] for r in tr),
            }
    return {
        "n": len(rows), "n_ok": len(ok),
        "lon": {"med": pct(dl, 50), "p90": pct(dl, 90), "p99": pct(dl, 99), "max": max(dl)},
        "lat": {"med": pct(db, 50), "p90": pct(db, 90), "p99": pct(db, 99), "max": max(db)},
        "within_02": sum(1 for r in ok if r["abs_dlon"] <= 0.2 and r["abs_dlat"] <= 0.2) / len(ok),
        "within_05": sum(1 for r in ok if r["abs_dlon"] <= 0.5 and r["abs_dlat"] <= 0.5) / len(ok),
        "within_10": sum(1 for r in ok if r["abs_dlon"] <= 1.0 and r["abs_dlat"] <= 1.0) / len(ok),
        "clear_lon_med": pct([r["abs_dlon"] for r in ok if r["quality"] == "clear"], 50),
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

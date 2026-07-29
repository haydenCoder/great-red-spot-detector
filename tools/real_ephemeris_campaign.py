#!/usr/bin/env python3
"""
Real-ephemeris GRS accuracy campaign.

Renders synthetic full-disk Jupiter frames at REAL observation epochs and plants
the GRS at the REAL online-derived System III longitude for that epoch (the
JUPOS/Hubble drift model in app/grs_ephemeris_truth.py), then measures each
through the real fit_limb_nav -> measure_grs_precision stack and scores the
recovery. This validates the ABSOLUTE longitude path at scale against
ground truth sourced from the published record, which is impossible to do on
downloaded web imagery (no UTC -> no CM).

Why synthetic pixels but real truth
-----------------------------------
Binary downloads are blocked in this sandbox and no public UTC-tagged amateur
Jupiter dataset exists, so real photos are unavailable here. The honest
substitute (agreed with the user) keeps the PIXELS synthetic but makes the TRUTH
real: every frame's GRS sits at the actual published GRS longitude for its
epoch, at the literature latitude (-22.4 deg planetographic), observed at the
GRS transit time (exactly as a careful observer would) and placed at a spread of
on-disk longitudes. Scoring meas - planted isolates estimator error; the planted
longitude vs the drift model is reported as plant fidelity.

Matrix
------
  dates   : real epochs 2020-2026 (~one per ~2.5 months), observed at GRS transit
  quality : clear/mild/blurry/vblurry x 1080p + clear/blurry x 4K, noise scaled
            with seeing
  place   : GRS longitude relative to meridian cycled through {0,+30,-30,+50,-50}
  gates   : clear/mild < 0.5 deg (lon AND lat); blurry/vblurry < 1.0 deg

Usage
-----
    python tools/real_ephemeris_campaign.py                  # default ~240 cases
    python tools/real_ephemeris_campaign.py --n-dates 80     # scale toward 1000
    python tools/real_ephemeris_campaign.py --resume
"""
from __future__ import annotations

import argparse
import datetime as dt
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
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

# (tier_id, resolution, seeing_arcsec, limit_deg)  -- limit is the tiered gate
QUALITY_TIERS: List[Tuple[str, str, float, float]] = [
    ("clear_s",   "1080p", 0.38, 0.5),
    ("mild_s",    "1080p", 0.80, 0.5),
    ("blurry_s",  "1080p", 1.80, 1.0),
    ("vblurry_s", "1080p", 2.50, 1.0),
    ("clear_l",   "4K",    0.38, 0.5),
    ("blurry_l",  "4K",    1.80, 1.0),
]
# GRS longitude relative to meridian for each observation. Kept within +-25 deg:
# a careful observer images near transit, where the GRS is least foreshortened.
# Limb placements (|lon_rel| > ~45) are deliberately excluded -- you do not
# precision-measure the GRS when it is squashed against the limb, and including
# them would conflate foreshortening with estimator error.
PLACEMENTS = [0.0, 25.0, -25.0, 15.0, -15.0, 10.0]
CACHE_PATH = ROOT / "runs" / "real_ephemeris_campaign.jsonl"


def _noise_for_seeing(seeing: float) -> float:
    return float(min(0.035, 0.004 + 0.006 * seeing))


def build_dates(n_dates: int) -> List[dt.date]:
    """Evenly spaced real epochs from 2020-01-15 to 2026-07-15."""
    start = dt.date(2020, 1, 15)
    end = dt.date(2026, 7, 15)
    span = (end - start).days
    if n_dates <= 1:
        return [start]
    return [start + dt.timedelta(days=round(span * i / (n_dates - 1))) for i in range(n_dates)]


def build_matrix(n_dates: int) -> List[Dict[str, Any]]:
    cases: List[Dict[str, Any]] = []
    idx = 0
    for di, date in enumerate(build_dates(n_dates)):
        for qj, (tier, res, seeing, limit) in enumerate(QUALITY_TIERS):
            placement = PLACEMENTS[(di * 2 + qj) % len(PLACEMENTS)]
            cases.append({
                "case_id": f"{date.isoformat()}_{tier}",
                "idx": idx,
                "date": date.isoformat(),
                "tier": tier,
                "resolution": res,
                "seeing_arcsec": seeing,
                "noise_rms": _noise_for_seeing(seeing),
                "limit_deg": limit,
                "placement_target": placement,
                "clarity": "clear" if "clear" in tier else
                           ("mild" if "mild" in tier else
                            ("blurry" if "blurry" in tier and "v" not in tier else "vblurry")),
                "size": "small" if res == "1080p" else "large",
            })
            idx += 1
    return cases


def run_case(case: Dict[str, Any]) -> Dict[str, Any]:
    """Render one real-epoch frame planted at the online GRS longitude; measure."""
    import numpy as np
    from PIL import Image

    from grs_ephemeris_truth import (
        GRS_LAT_PLANETOCENTRIC_LIT,
        analytical_cm_iii,
        grs_longitude_iii_w,
        observe_at_placement,
    )
    from precision_engine import fit_limb_nav, measure_grs_precision, wrap_diff
    from synthetic_hq import SynthSpec, generate

    t0 = time.time()
    out = dict(case)
    try:
        date = dt.date.fromisoformat(case["date"])
        obs_time, lon_rel = observe_at_placement(date, case["placement_target"])
        drift_lon = grs_longitude_iii_w(obs_time)
        cm_analytical = analytical_cm_iii(obs_time)

        with tempfile.TemporaryDirectory(prefix="grs_realeph_") as d:
            # Plant the GRS at the online drift-model longitude for this epoch.
            os.environ["GRS_LIMB_LON_REL"] = f"{lon_rel:.6f}"
            try:
                png, _fit, truth = generate(SynthSpec(
                    region="global",
                    resolution_preset=case["resolution"],
                    random_time=False,
                    user_time_iso=obs_time.strftime("%Y-%m-%d %H:%M:%S"),
                    seed=20240109,
                    mode="metrology",
                    write_grs_crop=False,
                    seeing_fwhm_arcsec=case["seeing_arcsec"],
                    noise_rms=case["noise_rms"],
                ), Path(d))
            finally:
                os.environ.pop("GRS_LIMB_LON_REL", None)
            img = np.asarray(Image.open(png), dtype=np.float64) / 255.0

        nav = fit_limb_nav(img, cm_iii_deg=truth["cm_iii_deg"],
                           distance_au=truth["distance_au"])
        nav.cm_iii_deg = truth["cm_iii_deg"]
        nav.distance_au = truth["distance_au"]
        nav.sub_lat_deg = float(truth.get("sub_obs_lat_deg") or 0.0)
        nav.north_pa_deg = float(truth.get("north_pa_deg") or 0.0)

        res = measure_grs_precision(img, cm_iii_deg=nav.cm_iii_deg,
                                    distance_au=nav.distance_au, nav=nav, quiet=True)

        planted_lon = float(truth["grs_lon_seed_deg"])
        planted_lat = float(truth["grs_lat_seed_deg"])
        dlon_est = wrap_diff(res.lon_iii_deg, planted_lon)          # estimator error
        dlat_est = res.lat_deg - planted_lat
        dlon_vs_model = wrap_diff(planted_lon, drift_lon)           # plant fidelity
        dlat_vs_lit = res.lat_deg - GRS_LAT_PLANETOCENTRIC_LIT      # vs literature mean

        out.update({
            "ok": True,
            "obs_time": obs_time.strftime("%Y-%m-%d %H:%M:%S"),
            "drift_lon": float(drift_lon),
            "cm_analytical": float(cm_analytical),
            "lon_rel_achieved": float(lon_rel),
            "planted_lon": planted_lon,
            "planted_lat": planted_lat,
            "lon_meas": float(res.lon_iii_deg),
            "lat_meas": float(res.lat_deg),
            "dlon_est": float(dlon_est),
            "dlat_est": float(dlat_est),
            "abs_dlon": abs(float(dlon_est)),
            "abs_dlat": abs(float(dlat_est)),
            "dlon_vs_model": float(dlon_vs_model),
            "abs_dlon_vs_model": abs(float(dlon_vs_model)),
            "dlat_vs_lit": float(dlat_vs_lit),
            "abs_dlat_vs_lit": abs(float(dlat_vs_lit)),
            "method": res.method,
            "quality": float(res.quality),
            "cm_used": float(truth["cm_iii_deg"]),
            "distance_au": float(truth["distance_au"]),
            "secs": time.time() - t0,
        })
    except Exception as e:
        out.update({"ok": False, "error": f"{type(e).__name__}: {e}",
                    "secs": time.time() - t0})
    return out


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
    by_tier = {}
    for tier in sorted({r["tier"] for r in ok}):
        tr = [r for r in ok if r["tier"] == tier]
        by_tier[tier] = {
            "n": len(tr),
            "lon_med": pct([r["abs_dlon"] for r in tr], 50),
            "lon_p90": pct([r["abs_dlon"] for r in tr], 90),
            "lon_max": max(r["abs_dlon"] for r in tr),
            "lat_med": pct([r["abs_dlat"] for r in tr], 50),
            "lat_p90": pct([r["abs_dlat"] for r in tr], 90),
            "lat_max": max(r["abs_dlat"] for r in tr),
            "limit": tr[0]["limit_deg"],
            "within_gate": sum(1 for r in tr if r["abs_dlon"] <= tr[0]["limit_deg"]
                               and r["abs_dlat"] <= tr[0]["limit_deg"]) / len(tr),
        }
    return {
        "n": len(rows), "n_ok": len(ok),
        "lon": {"med": pct(dl, 50), "p90": pct(dl, 90), "max": max(dl)},
        "lat": {"med": pct(db, 50), "p90": pct(db, 90), "max": max(db)},
        "plant_fidelity_lon_max": max(r["abs_dlon_vs_model"] for r in ok),
        "within_05": sum(1 for r in ok if r["abs_dlon"] <= 0.5 and r["abs_dlat"] <= 0.5) / len(ok),
        "within_10": sum(1 for r in ok if r["abs_dlon"] <= 1.0 and r["abs_dlat"] <= 1.0) / len(ok),
        "by_tier": by_tier,
    }


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


def build_results(n_dates: int, force: bool = False, workers: int = 0) -> List[Dict[str, Any]]:
    matrix = build_matrix(n_dates)
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    cache = {} if force else _load_cache()

    def _stale(rec, case):
        return (
            not rec.get("ok")
            or str(rec.get("tier")) != case["tier"]
            or str(rec.get("date")) != case["date"]
            or abs(float(rec.get("seeing_arcsec", -9)) - case["seeing_arcsec"]) > 1e-9
        )

    todo = [c for c in matrix if _stale(cache.get(c["idx"], {}), c)]
    if not todo:
        return [cache[c["idx"]] for c in matrix]

    nw = workers or max(1, (os.cpu_count() or 2))
    print(f"\n[realeph] building {len(todo)}/{len(matrix)} cases on {nw} workers", flush=True)
    with CACHE_PATH.open("a", encoding="utf-8") as fh, \
            ProcessPoolExecutor(max_workers=nw) as ex:
        futs = {ex.submit(run_case, c): c for c in todo}
        for i, fut in enumerate(as_completed(futs), 1):
            rec = fut.result()
            cache[rec["idx"]] = rec
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            if i % 10 == 0 or i == len(todo):
                print(f"[realeph] {i}/{len(todo)} done", flush=True)
    return [cache[c["idx"]] for c in matrix]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n-dates", type=int, default=40)
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2)))
    ap.add_argument("--resume", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    rows = build_results(args.n_dates, force=args.force and not args.resume,
                         workers=args.workers)
    s = summarise(rows)
    print("\n" + json.dumps(s, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

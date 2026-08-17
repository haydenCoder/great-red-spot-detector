#!/usr/bin/env python3
"""Seeing floor stress test — deliberately try to BREAK the measurement.

The published matrix tops out at 2.40" seeing. This tool pushes far past it
(2.4" -> 6.0", and optionally beyond) to find the TRUE measurability floor:
where does the redness lock itself fail, and does the catastrophic decoy-lock
mode come back?

For each seeing tier it reports:
  - catastrophic (>10 deg) rate
  - >1 deg rate, median / p90 / max |lon| error
  - published-method histogram
  - how often redness was rejected / failed to produce a lock

This is the "doubt the fix" instrument: instead of assuming the 2.4" fix
generalises, we measure exactly where it stops holding.
"""
from __future__ import annotations

import argparse
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

import numpy as np

APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP))

from precision_engine import fit_limb_nav, measure_grs_precision, wrap_diff  # noqa: E402
from synthetic_hq import SynthSpec, generate  # noqa: E402
from PIL import Image  # noqa: E402


def noise_for(seeing: float) -> float:
    return float(min(0.035, 0.004 + 0.006 * seeing))


def run(seed: int, resolution: str, seeing: float):
    with tempfile.TemporaryDirectory(prefix="grs_floor_") as d:
        png, _fit, truth = generate(SynthSpec(
            region="global", resolution_preset=resolution, random_time=True,
            seed=seed, mode="metrology", write_grs_crop=False,
            seeing_fwhm_arcsec=seeing, noise_rms=noise_for(seeing),
        ), Path(d))
        img = np.asarray(Image.open(png), dtype=np.float64) / 255.0
    nav = fit_limb_nav(img, cm_iii_deg=truth["cm_iii_deg"], distance_au=truth["distance_au"])
    nav.cm_iii_deg = truth["cm_iii_deg"]
    nav.distance_au = truth["distance_au"]
    res = measure_grs_precision(img, cm_iii_deg=nav.cm_iii_deg,
                                distance_au=nav.distance_au, nav=nav,
                                quiet=True, lean=True, map_width=1200, map_height=600)
    dlon = wrap_diff(res.lon_iii_deg, truth["grs_lon_seed_deg"])
    dlat = res.lat_deg - truth["grs_lat_seed_deg"]
    # redness status: was there a surviving redness lock?
    red = res.methods.get("redness")
    red_rejected = bool(red.get("rejected")) if isinstance(red, dict) else None
    red_present = isinstance(red, dict) and not red.get("rejected")
    return abs(dlon), abs(dlat), res.method, red_present, red_rejected


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n", type=int, default=30, help="frames per seeing tier")
    ap.add_argument("--resolution", default="540p", help="540p | 720p | 1080p")
    ap.add_argument("--seeing", nargs="+", type=float,
                    default=[2.4, 2.8, 3.2, 3.6, 4.0, 4.5, 5.0, 6.0])
    args = ap.parse_args()

    SEED0 = 7_000_000
    STRIDE = 7919
    print(f"=== SEEING FLOOR STRESS TEST ({args.resolution}, n={args.n}/tier) ===\n")
    for seeing in args.seeing:
        lon_errs, lat_errs = [], []
        methods: Counter = Counter()
        red_ok = red_rej = 0
        t0 = time.time()
        for k in range(args.n):
            seed = SEED0 + k * STRIDE + int(seeing * 1000)
            try:
                dl, db, method, rp, rr = run(seed, args.resolution, seeing)
            except Exception:
                continue
            lon_errs.append(dl)
            lat_errs.append(db)
            methods[method] += 1
            red_ok += int(rp)
            red_rej += int(rr)
        n = len(lon_errs)
        if n == 0:
            print(f"  {seeing:>4.1f}\"  (all frames failed)")
            continue
        lon = np.array(lon_errs)
        lat = np.array(lat_errs)
        cat = int((lon > 10).sum())
        gt1 = int(((lon > 1.0) | (lat > 1.0)).sum())
        print(f"  {seeing:>4.1f}\"  n={n:3d}  catastrophic={cat:3d}  >1deg={gt1:3d}  "
              f"lon med/p90/max={np.median(lon):.3f}/{np.percentile(lon,90):.3f}/{lon.max():.3f}  "
              f"redness_ok={red_ok}/{n}  redness_rejected={red_rej}/{n}  "
              f"[{time.time()-t0:.0f}s]")
        if cat or (lon.max() > 3.0):
            # show the method mix when things go wrong
            print(f"          methods: {dict(methods)}")
    print("\n(done)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

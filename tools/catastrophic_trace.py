#!/usr/bin/env python3
"""Reproduce very-blurry catastrophic locks and dump the internal decision trace.

Companion to `vblurry_sweep.py` (which just counts the catastrophic rate).
This tool renders very-blurry frames and, for any frame whose |dlon| > 10 deg,
prints the full per-estimator breakdown (lon/lat/score/rejected + reason) and
the consensus `notes`, so the root cause of a decoy fallback lock is visible
end-to-end: which estimators agreed, which was the colour lock, and why the
cluster seeded on the wrong one.

Usage:
    python tools/catastrophic_trace.py --n 40 --resolutions 540p 720p
"""
from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

import numpy as np

APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP))

from precision_engine import fit_limb_nav, measure_grs_precision, wrap_diff  # noqa: E402
from synthetic_hq import SynthSpec, generate  # noqa: E402
from PIL import Image  # noqa: E402

SEED0 = 42_000_000
STRIDE = 7919
SEEING = 2.40
NOISE = min(0.035, 0.004 + 0.006 * SEEING)


def run(seed: int, resolution: str):
    with tempfile.TemporaryDirectory(prefix="grs_cat_") as d:
        png, _fit, truth = generate(SynthSpec(
            region="global", resolution_preset=resolution,
            random_time=True, seed=seed, mode="metrology",
            write_grs_crop=False, seeing_fwhm_arcsec=SEEING, noise_rms=NOISE,
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
    return res, dlon, dlat


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n", type=int, default=30, help="frames per resolution")
    ap.add_argument("--resolutions", nargs="+", default=["540p", "720p", "1080p"])
    ap.add_argument("--max-dlon", type=float, default=10.0,
                    help="print the trace only when |dlon| exceeds this (deg)")
    args = ap.parse_args()

    found = 0
    for res in args.resolutions:
        for k in range(args.n):
            seed = SEED0 + k * STRIDE
            try:
                res_obj, dlon, dlat = run(seed, res)
            except Exception as e:
                print(f"{res}#{k:03d} seed={seed} ERROR {type(e).__name__}: {e}")
                continue
            if abs(dlon) <= args.max_dlon:
                continue
            found += 1
            print(f"\n===== CATASTROPHIC {res}#{k:03d} seed={seed} "
                  f"dlon={dlon:.1f} dlat={dlat:.2f} =====")
            print("published:", res_obj.method, "quality:", round(res_obj.quality, 3))
            for name, m in res_obj.methods.items():
                if not isinstance(m, dict) or name in ("disk_quality", "grs_detection"):
                    continue
                print(f"  {name:16s} lon={m.get('lon_iii_deg'):.3f} "
                      f"lat={m.get('lat_deg'):.3f} score={m.get('score')} "
                      f"rejected={m.get('rejected')} ({m.get('reject_reason') or ''})")
            print("  --- notes ---")
            for nt in res_obj.notes:
                print("   ", nt)
    print(f"\n{found} catastrophic frame(s) out of "
          f"{len(args.resolutions) * args.n} scanned")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

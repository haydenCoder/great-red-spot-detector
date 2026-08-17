#!/usr/bin/env python3
"""Small vblurry-only sweep: measure the catastrophic (>10 deg) lock rate.

Used to verify the colour-lock isolation fix: before the fix, ~5% of 2.40"
very-blurry frames locked a decoy SEB oval up to ~102 deg off. After the fix,
a sanity-checked colour lock is never pruned as a cluster outlier, so those
frames fall back to the (correct) redness primary instead.
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

import numpy as np

APP = Path(__file__).resolve().parents[1] / "app"
sys.path.insert(0, str(APP))

from precision_engine import fit_limb_nav, measure_grs_precision, wrap_diff  # noqa: E402
from synthetic_hq import SynthSpec, generate  # noqa: E402
from PIL import Image  # noqa: E402

NOISE = min(0.035, 0.004 + 0.006 * 2.40)


def run(seed: int, resolution: str):
    with tempfile.TemporaryDirectory(prefix="grs_vb_") as d:
        png, _fit, truth = generate(SynthSpec(
            region="global", resolution_preset=resolution, random_time=True,
            seed=seed, mode="metrology", write_grs_crop=False,
            seeing_fwhm_arcsec=2.40, noise_rms=NOISE,
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
    return abs(dlon), abs(dlat), res.method


def main() -> int:
    SEED0 = 42_000_000
    STRIDE = 7919
    plan = [("540p", 40), ("720p", 40), ("1080p", 30)]
    tot = cat = gt05 = 0
    lon_errs = []
    t0 = time.time()
    k = 0
    for res, n in plan:
        for _ in range(n):
            seed = SEED0 + (k * STRIDE)
            k += 1
            try:
                dl, db, method = run(seed, res)
            except Exception as e:
                print(f"  {res} seed={seed} ERROR {e}", flush=True)
                continue
            tot += 1
            lon_errs.append(dl)
            if dl > 10:
                cat += 1
                print(f"  CATASTROPHIC {res} seed={seed} dlon={dl:.1f} dlat={db:.2f} method={method}", flush=True)
            if dl > 0.5 or db > 0.5:
                gt05 += 1
        print(f"  ...{res} done ({time.time()-t0:.0f}s so far)", flush=True)
    lon_errs = np.array(lon_errs)
    print("\n=== VBLURRY (2.40\") SWEEP ===", flush=True)
    print(f"frames: {tot}", flush=True)
    print(f"catastrophic (>10 deg lon): {cat}  ({100*cat/tot:.1f}%)", flush=True)
    print(f">0.5 deg (lon or lat):      {gt05}  ({100*gt05/tot:.1f}%)", flush=True)
    print(f"lon |err| median={np.median(lon_errs):.3f} p90={np.percentile(lon_errs,90):.3f} max={lon_errs.max():.3f}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

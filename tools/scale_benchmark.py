#!/usr/bin/env python3
"""
Multi-scale real-image benchmark.

Real Jupiter frames vary enormously in apparent disk size: a small refractor at
opposition gives ~30 px radius, a C11 with a barlow gives ~250 px, Hubble/Voyager
give 600-700 px. Accuracy that only holds at one scale is not useful.

Most web frames have no mid-exposure UTC, so ABSOLUTE System III longitude is
unmeasurable on them. What this benchmark measures instead are properties that
have real ground truth or a real invariance requirement:

  scale_invariance   Downsample the SAME frame by 2x/3x/4x. The measured
                     lon/lat must not move -- it is the same photograph. Any
                     drift is pure algorithm error, with the full-resolution
                     result as the reference. This is the core multi-scale
                     accuracy number.

  rotation_equivar   Rotate by a known angle, declare it via north_pa, and the
                     recovered System III longitude must not change. Ground
                     truth = the angle we applied.

  noise_stability    Add read noise; centre must not wander.

  literature_lat     GRS latitude -22.4 planetographic (JUPOS/BAA/NASA), stable
                     for decades.

Usage:
    python tools/scale_benchmark.py --glob 'image-search/*' --out runs/scale.jsonl
"""
from __future__ import annotations

import argparse
import glob as _glob
import json
import math
import os
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

GRS_LAT_PLANETOGRAPHIC_LIT = -22.4


def _measure(img: np.ndarray, pa: float = 0.0):
    from precision_engine import assess_disk_quality, fit_limb_nav, measure_grs_precision

    nav = fit_limb_nav(img, cm_iii_deg=0.0)
    nav.north_pa_deg = pa
    q = assess_disk_quality(img, nav)
    if not q.get("measurable"):
        return None, nav, q
    res = measure_grs_precision(img, cm_iii_deg=0.0, distance_au=5.2, nav=nav, quiet=True)
    return res, nav, q


def _load(path: str, max_side: int = 1600) -> np.ndarray:
    im = Image.open(path).convert("RGB")
    if max(im.size) > max_side:
        s = max_side / max(im.size)
        im = im.resize((max(2, int(im.width * s)), max(2, int(im.height * s))), Image.LANCZOS)
    return np.asarray(im, dtype=np.float64) / 255.0


def analyse(path: str) -> dict:
    from precision_engine import planetocentric_to_planetographic, wrap_diff

    out: dict = {"file": Path(path).name}
    try:
        img = _load(path)
    except Exception as e:
        return {**out, "measurable": False, "error": f"load: {e}"}

    res, nav, q = _measure(img)
    out.update(
        measurable=bool(q.get("measurable")),
        a_eq_px=float(nav.a_eq_px),
        disk_fill=q.get("disk_fill"),
        disk_contrast=q.get("disk_contrast"),
    )
    if res is None:
        out["skipped"] = "; ".join(q.get("reasons") or ["no resolved disk"])
        return out

    lat_g = planetocentric_to_planetographic(res.lat_deg)
    out.update(
        lon_rel=float(wrap_diff(res.lon_iii_deg, 0.0)),
        lat_planetocentric=float(res.lat_deg),
        lat_planetographic=float(lat_g),
        length_deg=float(res.length_deg),
        quality=float(res.quality),
        lat_err_vs_literature=float(lat_g - GRS_LAT_PLANETOGRAPHIC_LIT),
    )

    # --- scale invariance: same photo, fewer pixels ------------------------
    pil = Image.fromarray((np.clip(img, 0, 1) * 255).astype(np.uint8))
    scale_rows = []
    for k in (2, 3, 4):
        w, h = max(8, pil.width // k), max(8, pil.height // k)
        small = np.asarray(pil.resize((w, h), Image.LANCZOS), dtype=np.float64) / 255.0
        r2, nav2, q2 = _measure(small)
        if r2 is None:
            scale_rows.append({"k": k, "a_px": float(nav2.a_eq_px), "measurable": False})
            continue
        scale_rows.append({
            "k": k,
            "a_px": float(nav2.a_eq_px),
            "measurable": True,
            "dlon": float(wrap_diff(r2.lon_iii_deg, res.lon_iii_deg)),
            "dlat": float(r2.lat_deg - res.lat_deg),
        })
    out["scale"] = scale_rows
    ok = [r for r in scale_rows if r.get("measurable")]
    if ok:
        out["scale_max_dlon"] = float(max(abs(r["dlon"]) for r in ok))
        out["scale_max_dlat"] = float(max(abs(r["dlat"]) for r in ok))

    # --- rotation equivariance (ground truth = applied angle) --------------
    worst_lon = worst_lat = 0.0
    n_rot = 0
    for theta in (10.0, 25.0, -15.0):
        rot = np.asarray(pil.rotate(theta, resample=Image.BICUBIC, expand=False),
                         dtype=np.float64) / 255.0
        r3, _, q3 = _measure(rot, pa=theta)
        if r3 is None:
            continue
        worst_lon = max(worst_lon, abs(wrap_diff(r3.lon_iii_deg, res.lon_iii_deg)))
        worst_lat = max(worst_lat, abs(r3.lat_deg - res.lat_deg))
        n_rot += 1
    if n_rot:
        out["rot_max_dlon"] = float(worst_lon)
        out["rot_max_dlat"] = float(worst_lat)

    # --- noise stability ----------------------------------------------------
    rng = np.random.default_rng(0)
    nl, nb = [], []
    for _ in range(3):
        n = np.clip(img + rng.normal(0, 0.012, img.shape), 0.0, 1.0)
        r4, _, q4 = _measure(n)
        if r4 is None:
            continue
        nl.append(wrap_diff(r4.lon_iii_deg, res.lon_iii_deg))
        nb.append(r4.lat_deg - res.lat_deg)
    if nl:
        out["noise_max_dlon"] = float(max(abs(x) for x in nl))
        out["noise_max_dlat"] = float(max(abs(x) for x in nb))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--glob", default="image-search/*")
    ap.add_argument("--out", default="runs/scale.jsonl")
    args = ap.parse_args()

    files = [f for f in sorted(_glob.glob(args.glob))
             if Path(f).suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif"}]
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    rows = []
    with out.open("w", encoding="utf-8") as fh:
        for f in files:
            try:
                r = analyse(f)
            except Exception as e:
                r = {"file": Path(f).name, "measurable": False,
                     "error": f"{type(e).__name__}: {e}"}
            rows.append(r)
            fh.write(json.dumps(r) + "\n")
            fh.flush()
            if r.get("measurable"):
                print(f"{r['file'][:44]:46} a={r['a_eq_px']:6.1f}px "
                      f"scale={r.get('scale_max_dlon', float('nan')):6.2f}/"
                      f"{r.get('scale_max_dlat', float('nan')):5.2f} "
                      f"rot={r.get('rot_max_dlon', float('nan')):6.2f} "
                      f"noise={r.get('noise_max_dlon', float('nan')):5.2f} "
                      f"latG={r.get('lat_planetographic', float('nan')):+6.2f}", flush=True)
            else:
                print(f"{r['file'][:44]:46} SKIP  {r.get('skipped') or r.get('error')}", flush=True)

    ok = [r for r in rows if r.get("measurable")]

    def med(key):
        v = sorted(r[key] for r in ok if isinstance(r.get(key), float) and r[key] == r[key])
        return float(v[len(v) // 2]) if v else float("nan")

    def worst(key):
        v = [r[key] for r in ok if isinstance(r.get(key), float) and r[key] == r[key]]
        return float(max(v)) if v else float("nan")

    small = [r for r in ok if r["a_eq_px"] < 150]
    large = [r for r in ok if r["a_eq_px"] >= 150]

    def grp(rs, key):
        v = [r[key] for r in rs if isinstance(r.get(key), float) and r[key] == r[key]]
        return float(np.median(v)) if v else float("nan")

    summary = {
        "n": len(rows),
        "n_measurable": len(ok),
        "radius_px_range": [min((r["a_eq_px"] for r in ok), default=float("nan")),
                            max((r["a_eq_px"] for r in ok), default=float("nan"))],
        "scale_invariance": {"median_dlon": med("scale_max_dlon"),
                             "median_dlat": med("scale_max_dlat"),
                             "worst_dlon": worst("scale_max_dlon")},
        "rotation_equivariance": {"median_dlon": med("rot_max_dlon"),
                                  "worst_dlon": worst("rot_max_dlon")},
        "noise_stability": {"median_dlon": med("noise_max_dlon")},
        "literature_latitude": {"median_abs_err":
                                float(np.median([abs(r["lat_err_vs_literature"]) for r in ok]))
                                if ok else float("nan")},
        "by_size": {
            "small_lt150px": {"n": len(small), "scale_dlon": grp(small, "scale_max_dlon"),
                              "rot_dlon": grp(small, "rot_max_dlon")},
            "large_ge150px": {"n": len(large), "scale_dlon": grp(large, "scale_max_dlon"),
                              "rot_dlon": grp(large, "rot_max_dlon")},
        },
        "note": ("Absolute System III longitude is unmeasurable without a mid-exposure "
                 "UTC. Scale invariance, rotation equivariance and noise stability are "
                 "pure algorithm error with a real reference."),
    }
    print("\n" + json.dumps(summary, indent=2))
    Path(str(out) + ".summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

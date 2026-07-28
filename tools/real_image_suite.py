#!/usr/bin/env python3
"""
Real-image consistency suite.

Web/telescope images of Jupiter carry no mid-exposure UTC and no published GRS
longitude, so ABSOLUTE System III accuracy is unmeasurable on them: without a
timestamp there is no central meridian to reference. Claiming a "degrees from
truth" number on these frames would be fabricating a reference.

What IS measurable, and is what this suite reports:

  1. lock_rate        - does the pipeline find a GRS-band feature at all?
  2. limb_stability   - limb fit repeatability under noise / rescale
  3. method_agreement - spread between template / map_dark / moment, which are
                        independent estimators; large spread = untrustworthy
  4. noise_repeat     - centre scatter under added read noise
  5. rotation_equiv   - EQUIVARIANCE test with a real reference: rotate the
                        image by a known angle, tell the engine via north_pa,
                        and the recovered System III longitude must not change.
                        This one DOES have ground truth (the rotation we applied)
                        and directly exercises the PA path that Defect B broke.
  6. scale_equiv      - same idea for rescaling: measured lon/lat must be
                        invariant to image resolution.

Usage:
    python tools/real_image_suite.py --glob 'image-search/*.jpg' --out runs/real.jsonl
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


def _load(path: str, max_side: int = 1400) -> np.ndarray:
    im = Image.open(path).convert("RGB")
    if max(im.size) > max_side:
        s = max_side / max(im.size)
        im = im.resize((max(2, int(im.width * s)), max(2, int(im.height * s))), Image.LANCZOS)
    return np.asarray(im, dtype=np.float64) / 255.0


def _measure(img: np.ndarray, cm: float = 0.0, pa: float = 0.0):
    from precision_engine import fit_limb_nav, measure_grs_precision

    nav = fit_limb_nav(img, cm_iii_deg=cm)
    nav.cm_iii_deg = cm
    nav.north_pa_deg = pa
    res = measure_grs_precision(img, cm_iii_deg=cm, distance_au=nav.distance_au,
                                nav=nav, quiet=True)
    return nav, res


def analyse(path: str) -> dict:
    from accuracy_gates import grs_lat_in_wide_band
    from precision_engine import wrap_diff

    out: dict = {"file": Path(path).name}
    try:
        img = _load(path)
    except Exception as e:
        return {**out, "ok": False, "error": f"load: {e}"}

    try:
        nav, res = _measure(img)
    except Exception as e:
        return {**out, "ok": False, "error": f"measure: {type(e).__name__}: {e}"}

    out.update(
        ok=True,
        h=img.shape[0], w=img.shape[1],
        a_eq_px=nav.a_eq_px, xc=nav.xc, yc=nav.yc,
        lon=res.lon_iii_deg, lat=res.lat_deg,
        method=res.method, quality=res.quality,
        in_grs_band=bool(grs_lat_in_wide_band(res.lat_deg)),
    )

    # --- method agreement (independent estimators on the same frame) --------
    try:
        ms = {k: v for k, v in (res.methods or {}).items()
              if isinstance(v, dict) and not v.get("rejected") and "lon_iii_deg" in v}
        if len(ms) >= 2:
            lons = [float(v["lon_iii_deg"]) for v in ms.values()]
            ref = lons[0]
            spread = max(abs(wrap_diff(x, ref)) for x in lons)
            out["method_spread_deg"] = float(spread)
            out["n_methods"] = len(ms)
        else:
            out["method_spread_deg"] = float("nan")
            out["n_methods"] = len(ms)
    except Exception:
        out["method_spread_deg"] = float("nan")

    # --- noise repeatability -------------------------------------------------
    try:
        rng = np.random.default_rng(0)
        lo, la = [], []
        for _ in range(3):
            n = np.clip(img + rng.normal(0, 0.01, img.shape), 0.0, 1.0)
            _, r2 = _measure(n)
            lo.append(r2.lon_iii_deg)
            la.append(r2.lat_deg)
        out["noise_lon_spread_deg"] = float(max(abs(wrap_diff(x, lo[0])) for x in lo))
        out["noise_lat_spread_deg"] = float(max(abs(x - la[0]) for x in la))
    except Exception as e:
        out["noise_error"] = str(e)

    # --- rotation equivariance (HAS ground truth: the angle we applied) ------
    # Rotating the frame by +theta and declaring north_pa = theta must leave the
    # recovered System III longitude unchanged. This is the exact path the old
    # PA/oblateness bug corrupted.
    try:
        from PIL import Image as _I
        worst_lon = worst_lat = 0.0
        for theta in (10.0, 25.0):
            pil = _I.fromarray((np.clip(img, 0, 1) * 255).astype(np.uint8))
            rot = np.asarray(pil.rotate(theta, resample=_I.BICUBIC, expand=False),
                             dtype=np.float64) / 255.0
            _, rr = _measure(rot, pa=theta)
            worst_lon = max(worst_lon, abs(wrap_diff(rr.lon_iii_deg, res.lon_iii_deg)))
            worst_lat = max(worst_lat, abs(rr.lat_deg - res.lat_deg))
        out["rot_equiv_lon_deg"] = float(worst_lon)
        out["rot_equiv_lat_deg"] = float(worst_lat)
    except Exception as e:
        out["rot_error"] = str(e)

    # --- scale invariance ----------------------------------------------------
    try:
        pil = Image.fromarray((np.clip(img, 0, 1) * 255).astype(np.uint8))
        half = np.asarray(
            pil.resize((max(2, pil.width // 2), max(2, pil.height // 2)), Image.LANCZOS),
            dtype=np.float64) / 255.0
        _, rh = _measure(half)
        out["scale_lon_deg"] = float(abs(wrap_diff(rh.lon_iii_deg, res.lon_iii_deg)))
        out["scale_lat_deg"] = float(abs(rh.lat_deg - res.lat_deg))
    except Exception as e:
        out["scale_error"] = str(e)

    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--glob", default="image-search/*")
    ap.add_argument("--out", default="runs/real.jsonl")
    args = ap.parse_args()

    files = [f for f in sorted(_glob.glob(args.glob))
             if Path(f).suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif"}]
    if not files:
        print(f"no images matched {args.glob}")
        return 1

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    with out.open("w", encoding="utf-8") as fh:
        for f in files:
            r = analyse(f)
            rows.append(r)
            fh.write(json.dumps(r) + "\n")
            fh.flush()
            if r.get("ok"):
                print(f"{r['file'][:44]:46} lat={r['lat']:+7.2f} band={r['in_grs_band']} "
                      f"spread={r.get('method_spread_deg', float('nan')):6.2f} "
                      f"rot={r.get('rot_equiv_lon_deg', float('nan')):6.2f} "
                      f"scale={r.get('scale_lon_deg', float('nan')):5.2f}", flush=True)
            else:
                print(f"{r['file'][:44]:46} FAILED {r.get('error')}", flush=True)

    ok = [r for r in rows if r.get("ok")]
    def med(key):
        v = sorted(x[key] for x in ok if isinstance(x.get(key), float) and x[key] == x[key])
        return float(v[len(v) // 2]) if v else float("nan")

    summary = {
        "n": len(rows),
        "n_ok": len(ok),
        "lock_rate": (sum(1 for r in ok if r.get("in_grs_band")) / len(ok)) if ok else 0.0,
        "median_method_spread_deg": med("method_spread_deg"),
        "median_noise_lon_spread_deg": med("noise_lon_spread_deg"),
        "median_rot_equiv_lon_deg": med("rot_equiv_lon_deg"),
        "median_rot_equiv_lat_deg": med("rot_equiv_lat_deg"),
        "median_scale_lon_deg": med("scale_lon_deg"),
        "note": (
            "Absolute System III accuracy is NOT measurable on these frames: no "
            "mid-exposure UTC and no published GRS longitude, so there is no CM "
            "to reference. Rotation/scale equivariance DO have ground truth (the "
            "transform applied) and are real accuracy checks of the PA path."
        ),
    }
    print("\n" + json.dumps(summary, indent=2))
    Path(str(out) + ".summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

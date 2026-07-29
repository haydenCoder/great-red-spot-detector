#!/usr/bin/env python3
"""
Real-image accuracy against PUBLISHED ground truth.

Most web imagery cannot be scored for absolute System III longitude: with no
mid-exposure UTC there is no central meridian to reference. This suite uses the
subset where an independent, checkable reference DOES exist.

Case 1 — Hubble WFC3, 2014-04-21 (Ganymede shadow transiting the GRS)
    NASA/STScI published this frame explicitly as "the shadow of Ganymede swept
    across the CENTER of the Great Red Spot". That statement is a geometric
    ground truth that does not depend on any ephemeris we compute: whatever the
    exposure time was, the shadow centre and the GRS centre coincide on the
    sky. So we can score:

        shadow_grs_offset_deg  - angular separation between the measured GRS
                                 centre and the (independently located) dark
                                 circular shadow. Published truth: ~0.

    This is a genuine absolute check of the measured GRS centre against a
    reference nobody in this codebase produced.

Case 2 — literature latitude
    The GRS latitude is one of the best-constrained numbers in amateur and
    professional planetary science: -22.4 deg PLANETOGRAPHIC (JUPOS/BAA/NASA),
    stable for decades because the jets pin it. Every resolved real frame can
    be scored against it. Note the convention: the engine works planetocentric,
    so -22.4 planetographic == -19.82 planetocentric.

Case 3 — size
    GRS zonal length ~13-15 deg in the 2010s (Sanchez-Lavega et al. 2021 report
    15.5 deg in Mar 2019 shrinking to 13.7 deg by May 2020).

Usage:
    python tools/real_truth_suite.py --image <file> --out runs/truth.jsonl
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

# Published references ------------------------------------------------------
GRS_LAT_PLANETOGRAPHIC_LIT = -22.4      # JUPOS / BAA / NASA, stable for decades
GRS_LENGTH_DEG_2010s = (13.0, 16.0)     # Sanchez-Lavega 2021 range
LAT_TOL_DEG = 2.0                       # generous: real GRS wanders ~1 deg


def find_dark_shadow(img: np.ndarray, nav, exclude_frac: float = 0.55):
    """Locate a satellite shadow: a small, very dark, near-circular blob on disk.

    A moon shadow is far darker than any cloud feature and close to circular,
    which makes it separable from the GRS itself. Returns (y, x, radius_px) or
    None.
    """
    from precision_engine import to_mono

    mono = to_mono(img)
    h, w = mono.shape
    b = nav.a_eq_px * (1.0 - nav.flattening)
    yy, xx = np.mgrid[0:h, 0:w]
    on_disk = (((xx - nav.xc) / nav.a_eq_px) ** 2 + ((yy - nav.yc) / b) ** 2) <= 0.92
    if on_disk.sum() < 100:
        return None

    vals = mono[on_disk]
    # shadow is in the extreme dark tail of the ON-DISK distribution
    thr = float(np.percentile(vals, 0.5))
    cand = on_disk & (mono <= thr)
    try:
        from scipy.ndimage import binary_closing, binary_opening, label

        cand = binary_closing(binary_opening(cand, iterations=1), iterations=2)
        lab, n = label(cand)
        if n == 0:
            return None
        best = None
        for i in range(1, n + 1):
            m = lab == i
            area = int(m.sum())
            if area < 12:
                continue
            ys, xs = np.where(m)
            ry = (ys.max() - ys.min() + 1) / 2.0
            rx = (xs.max() - xs.min() + 1) / 2.0
            if max(rx, ry) < 1e-6:
                continue
            circ = min(rx, ry) / max(rx, ry)          # 1.0 == circular
            fill = area / (math.pi * rx * ry + 1e-9)  # 1.0 == filled disc
            if circ < 0.55 or fill < 0.55:
                continue
            score = area * circ * fill
            if best is None or score > best[0]:
                best = (score, float(ys.mean()), float(xs.mean()), float((rx + ry) / 2))
        if best is None:
            return None
        return best[1], best[2], best[3]
    except Exception:
        return None


def analyse(path: str, expect_shadow: bool = False, utc: str | None = None) -> dict:
    from precision_engine import (
        assess_disk_quality,
        fit_limb_nav,
        measure_grs_precision,
        planetocentric_to_planetographic,
        px_to_lonlat,
        wrap_diff,
    )

    im = Image.open(path).convert("RGB")
    if max(im.size) > 1600:
        s = 1600 / max(im.size)
        im = im.resize((int(im.width * s), int(im.height * s)), Image.LANCZOS)
    img = np.asarray(im, dtype=np.float64) / 255.0

    nav = fit_limb_nav(img, cm_iii_deg=0.0)

    # Real frames are NOT north-up with zero tilt. Feeding PA=0 / sub-lat=0 to a
    # frame taken at PA=-7.06 deg, sub-lat=+1.51 deg costs ~1.9 deg of latitude
    # accuracy -- an error in the HARNESS, not the engine. When the observation
    # date is known, take the true orientation from SPICE.
    cm_ref = 0.0
    if utc:
        try:
            from spice_auto import compute_spice_geometry
            g = compute_spice_geometry(utc)
            if g is not None:
                nav.north_pa_deg = float(g.north_pa_deg)
                nav.sub_lat_deg = float(g.sub_obs_lat_deg)
                nav.distance_au = float(g.distance_au)
        except Exception:
            pass

    q = assess_disk_quality(img, nav)
    out = {
        "file": Path(path).name,
        "measurable": bool(q.get("measurable")),
        "disk_fill": q.get("disk_fill"),
        "disk_contrast": q.get("disk_contrast"),
        "a_eq_px": nav.a_eq_px,
        "north_pa_deg": float(nav.north_pa_deg),
        "sub_lat_deg": float(nav.sub_lat_deg),
        "utc": utc,
    }
    if not q.get("measurable"):
        out["skipped"] = "no resolved disk"
        return out

    res = measure_grs_precision(img, cm_iii_deg=0.0, distance_au=nav.distance_au,
                                nav=nav, quiet=True)
    lat_c = float(res.lat_deg)
    lat_g = planetocentric_to_planetographic(lat_c)
    out.update(
        lon_rel_deg=float(wrap_diff(res.lon_iii_deg, 0.0)),
        lat_planetocentric=lat_c,
        lat_planetographic=lat_g,
        length_deg=float(res.length_deg),
        width_deg=float(res.width_deg),
        quality=float(res.quality),
    )

    # --- TRUTH 1: literature latitude -------------------------------------
    out["lat_err_vs_literature_deg"] = float(lat_g - GRS_LAT_PLANETOGRAPHIC_LIT)
    out["lat_within_tol"] = bool(abs(lat_g - GRS_LAT_PLANETOGRAPHIC_LIT) <= LAT_TOL_DEG)

    # --- TRUTH 2: size ------------------------------------------------------
    L = float(res.length_deg)
    out["length_plausible"] = bool(GRS_LENGTH_DEG_2010s[0] <= L <= GRS_LENGTH_DEG_2010s[1])

    # --- TRUTH 3: Ganymede shadow coincident with GRS centre ---------------
    if expect_shadow:
        sh = find_dark_shadow(img, nav)
        if sh is None:
            out["shadow"] = "not found"
        else:
            sy, sx, sr = sh
            s_lon, s_lat = px_to_lonlat(sy, sx, nav)
            out.update(
                shadow_px=[sx, sy], shadow_r_px=sr,
                shadow_lon_rel=float(wrap_diff(s_lon, 0.0)),
                shadow_lat=float(s_lat),
            )
            dlon = wrap_diff(res.lon_iii_deg, s_lon)
            dlat = lat_c - s_lat
            out["shadow_grs_dlon_deg"] = float(dlon)
            out["shadow_grs_dlat_deg"] = float(dlat)
            out["shadow_grs_offset_deg"] = float(math.hypot(dlon, dlat))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--image", action="append", required=True)
    ap.add_argument("--shadow", action="append", default=[],
                    help="filename substring of frames with a satellite shadow on the GRS")
    ap.add_argument("--utc", action="append", default=[],
                    help="mid-exposure UTC per --image, in the same order")
    ap.add_argument("--out", default="runs/truth.jsonl")
    args = ap.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    with out.open("w", encoding="utf-8") as fh:
        for i, p in enumerate(args.image):
            want_shadow = any(s in p for s in args.shadow)
            u = args.utc[i] if i < len(args.utc) else None
            try:
                r = analyse(p, expect_shadow=want_shadow, utc=u)
            except Exception as e:
                r = {"file": Path(p).name, "error": f"{type(e).__name__}: {e}"}
            rows.append(r)
            fh.write(json.dumps(r) + "\n")
            print(json.dumps(r, indent=2), flush=True)

    scored = [r for r in rows if r.get("measurable")]
    summary = {
        "n": len(rows),
        "n_measurable": len(scored),
        "lat_pass_rate": (sum(1 for r in scored if r.get("lat_within_tol")) / len(scored))
        if scored else 0.0,
        "lat_err_median_deg": float(np.median([abs(r["lat_err_vs_literature_deg"])
                                               for r in scored])) if scored else float("nan"),
        "shadow_offsets_deg": [r["shadow_grs_offset_deg"] for r in scored
                               if "shadow_grs_offset_deg" in r],
    }
    print("\nSUMMARY\n" + json.dumps(summary, indent=2))
    Path(str(out) + ".summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""
Validate the published GRS measurement pipeline on a REAL photo.

This is the only honest way to know if the synthetic-tuned
measurement is actually correct: run it on a real FITS/SER/PNG
of Jupiter with a known mid-exposure UTC, optionally paste
your manual WinJUPOS pick, and compare.

What this tool does NOT do:
  - It does not download photos for you. The sandbox cannot
    reach the internet and we do not bundle amateur images
    in this repo. You point it at a file you have.
  - It does not auto-detect a WinJUPOS answer. If you have a
    manual WinJUPOS pick (lon, lat, or the cm-iii + grs-lon
    difference), paste it via --wj-lon and --wj-lat. The tool
    reports Δsky in arcseconds.
  - It does not overfit. If the real-photo result is bad,
    the diagnostic tells you *which* estimator was wrong,
    not just "the answer is bad".

What this tool DOES do:
  - Load a FITS, SER, or PNG of Jupiter.
  - Run the full measurement pipeline: SPICE ephemeris → limb
    fit → research-grade measurement → gold standard → WinJUPOS
    twin → champion → publish → SUPERDUPER.
  - Report lon_iii, lat (centric + graphic), CM III, σ_sky, and
    the per-estimator breakdown (template / moment / map_dark
    / redness / etc.) so you can see which one was the
    closest to your manual pick.
  - If --wj-lon is given, report Δsky in arcsec against your
    manual pick.
  - Save a full result JSON next to the input file so you can
    diff runs.

Usage:
  # Synthetic smoke (always works, useful for sanity)
  python3 tools/real_photo_validate.py --synthetic

  # Real photo
  python3 tools/real_photo_validate.py \\
      --fits /path/to/jupiter_stack.fits \\
      --time "2026-07-14 12:00:00" \\
      --wj-lon 247.5 --wj-lat -22.4

  # Auto-detect UTC from the FITS header
  python3 tools/real_photo_validate.py \\
      --fits /path/to/jupiter_stack.fits \\
      --wj-lon 247.5 --wj-lat -22.4
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import numpy as np

APP_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(APP_DIR / "app"))


def _km_per_deg_lon(lat_deg: float) -> float:
    """Jupiter meridian arc length per degree of longitude (planetocentric).
    Mirrors precision_engine.km_per_deg_lon."""
    JUP_REQ_KM = 71492.0
    JUP_RPOL_KM = 66854.0
    FLAT = 1.0 - JUP_RPOL_KM / JUP_REQ_KM
    la = math.radians(lat_deg)
    k = max(1.0 - FLAT, 1e-9)
    r = JUP_REQ_KM / math.sqrt(math.cos(la) ** 2 + (math.sin(la) / k) ** 2)
    return r * math.cos(la) * math.pi / 180.0


def _km_per_deg_lat(lat_deg: float = 0.0) -> float:
    JUP_REQ_KM = 71492.0
    JUP_RPOL_KM = 66854.0
    FLAT = 1.0 - JUP_RPOL_KM / JUP_REQ_KM
    la = math.radians(lat_deg)
    k = max(1.0 - FLAT, 1e-9)
    u = math.cos(la) ** 2 + (math.sin(la) / k) ** 2
    du = math.sin(2.0 * la) * (1.0 / (k * k) - 1.0)
    r = JUP_REQ_KM * u ** -0.5
    dr = -0.5 * JUP_REQ_KM * u ** -1.5 * du
    return math.sqrt(dr * dr + r * r) * math.pi / 180.0


def _sky_error_arcsec(dlon_deg: float, dlat_deg: float,
                       lat_deg: float, distance_au: float) -> float:
    AU_KM = 149597870.7
    ARCSEC_PER_RAD = 206264.80624709636
    as_lon = abs(dlon_deg) * _km_per_deg_lon(lat_deg) / (distance_au * AU_KM) * ARCSEC_PER_RAD
    as_lat = abs(dlat_deg) * _km_per_deg_lat(lat_deg) / (distance_au * AU_KM) * ARCSEC_PER_RAD
    return math.hypot(as_lon, as_lat)


def _per_estimator_breakdown(package: Dict[str, Any],
                              truth_lon: Optional[float] = None,
                              truth_lat: Optional[float] = None
                              ) -> Dict[str, Any]:
    """Pull the per-estimator lon/lat from a measured package and (if
    truth provided) report Δsky against truth for each."""
    methods = package.get("methods", {})
    breakdown = {}
    for name, m in methods.items():
        if not isinstance(m, dict) or m.get("lon_iii_deg") is None:
            continue
        try:
            ml = float(m["lon_iii_deg"])
            mla = float(m.get("lat_deg", 0.0))
        except (TypeError, ValueError):
            continue
        entry = {
            "lon_iii_deg": ml, "lat_deg": mla,
            "rejected": bool(m.get("rejected", False)),
            "score": float(m.get("score", 0.0) or 0.0),
        }
        if truth_lon is not None and truth_lat is not None:
            dlon = (ml - truth_lon + 180) % 360 - 180
            dlat = mla - truth_lat
            entry["dlon_deg"] = dlon
            entry["dlat_deg"] = dlat
        breakdown[name] = entry
    return breakdown


def _run_synthetic_smoke() -> Dict[str, Any]:
    """Run on one metrology-mode synthetic frame, no real photo required."""
    from synthetic_hq import SynthSpec, generate
    from precision_engine import (
        fit_limb_nav, measure_grs_precision, NavState,
    )
    import grs_complete_system as grs
    import tempfile

    with tempfile.TemporaryDirectory(prefix="grs_real_smoke_") as d:
        spec = SynthSpec(
            user_time_iso="",
            region="global",
            resolution_preset="1080p",
            random_time=True,
            seed=20260109,
            mode="metrology",
            write_grs_crop=False,
        )
        _png, fit, truth = generate(spec, Path(d))
        arr, _ = grs.read_fits(fit)
        img = np.asarray(arr, dtype=np.float64)
        nav = fit_limb_nav(img, cm_iii_deg=truth["cm_iii_deg"],
                            distance_au=truth["distance_au"])
        nav.cm_iii_deg = truth["cm_iii_deg"]
        nav.distance_au = truth["distance_au"]
        res = measure_grs_precision(
            img, cm_iii_deg=truth["cm_iii_deg"],
            distance_au=truth["distance_au"], nav=nav, quiet=True,
        )
    return {
        "mode": "synthetic_smoke",
        "truth_lon_iii_deg": truth["grs_lon_iii_deg"],
        "truth_lat_deg": truth["grs_lat_deg"],
        "publish_lon_iii_deg": res.lon_iii_deg,
        "publish_lat_deg": res.lat_deg,
        "method": res.method,
        "quality": float(res.quality),
        "per_estimator": _per_estimator_breakdown(
            {"methods": res.methods},
            truth_lon=truth["grs_lon_iii_deg"],
            truth_lat=truth["grs_lat_deg"],
        ),
    }


def _run_real(fits_path: Path, time_str: Optional[str],
              wj_lon: Optional[float], wj_lat: Optional[float],
              out_dir: Path) -> Dict[str, Any]:
    """Run the full pipeline on a real image."""
    from precision_engine import fit_limb_nav, measure_grs_precision
    from fits_time import require_observation_time, format_utc
    from desktop_pipeline import run_process_full
    from product_core import process_image
    import tempfile

    if not fits_path.exists():
        raise FileNotFoundError(fits_path)

    with tempfile.TemporaryDirectory(prefix="grs_real_") as tmp:
        out = Path(tmp) / "real_job"
        out.mkdir(parents=True, exist_ok=True)
        try:
            pkg = process_image(
                str(fits_path),
                time_str or "1970-01-01 00:00:00",
                out_root=out,
            )
        except Exception as e:
            return {
                "mode": "real_photo",
                "input": str(fits_path),
                "error": str(e),
                "hint": (
                    "process_image requires a valid mid-exposure UTC. "
                    "Pass --time 'YYYY-MM-DD HH:MM:SS' or rely on the FITS "
                    "header / filename. If the file is RGB, the redness "
                    "estimator needs the colour information."
                ),
            }

    pub = pkg.get("publish", {})
    h = pkg.get("headline", {})
    pe = pkg.get("pro_ephemeris", {})
    method_str = pub.get("publish_definition") or h.get("method", "?")
    truth = (wj_lon, wj_lat) if (wj_lon is not None and wj_lat is not None) else (None, None)
    if wj_lon is not None or wj_lat is not None:
        # Compute the per-estimator breakdown against the WJ pick
        truth_lon = wj_lon if wj_lon is not None else None
        truth_lat = wj_lat if wj_lat is not None else None
    else:
        truth_lon = truth_lat = None

    # Sky error
    delta = {}
    if wj_lon is not None and wj_lat is not None:
        pub_lon = float(pub.get("publish_lon_iii_deg") or h.get("lon_iii_deg") or 0.0)
        pub_lat = float(pub.get("publish_lat_deg") or h.get("lat_deg") or 0.0)
        dlon = (pub_lon - wj_lon + 180) % 360 - 180
        dlat = pub_lat - wj_lat
        dist_au = float(pe.get("distance_au", 5.2) or 5.2)
        sky = _sky_error_arcsec(dlon, dlat, wj_lat, dist_au)
        delta = {
            "dlon_deg": dlon, "dlat_deg": dlat,
            "sky_arcsec": sky,
            "equal_wj_1arcsec": sky <= 1.0,
            "equal_wj_2arcsec": sky <= 2.0,
        }

    breakdown = _per_estimator_breakdown(pkg, truth_lon=truth_lon, truth_lat=truth_lat)
    # If WJ provided, add per-estimator Δsky to the breakdown
    if wj_lon is not None and wj_lat is not None:
        dist_au = float(pe.get("distance_au", 5.2) or 5.2)
        for name, entry in breakdown.items():
            if "dlon_deg" in entry:
                entry["sky_arcsec"] = _sky_error_arcsec(
                    entry["dlon_deg"], entry["dlat_deg"], wj_lat, dist_au
                )

    result = {
        "mode": "real_photo",
        "input": str(fits_path),
        "user_time_utc": time_str,
        "publish_lon_iii_deg": float(pub.get("publish_lon_iii_deg", float("nan"))),
        "publish_lat_deg": float(pub.get("publish_lat_deg", float("nan"))),
        "publish_lat_planetographic_deg": float(pub.get("publish_lat_planetographic_deg", float("nan"))),
        "cm_iii_deg": float(pe.get("cm_iii_deg", float("nan"))),
        "cm_source": pe.get("cm_source", "?"),
        "distance_au": float(pe.get("distance_au", float("nan"))),
        "method": method_str,
        "sigma_total_sky_arcsec": float(pub.get("publish_sigma_sky_arcsec",
                                              h.get("sigma_total_sky_arcsec",
                                                    float("nan")))),
        "absolute_ok": bool(pub.get("absolute_ok", False)),
        "unbeatable_auto": bool(pub.get("unbeatable_auto", False)),
        "per_estimator": breakdown,
    }
    if wj_lon is not None:
        result["winjupos_lon_iii_deg"] = wj_lon
    if wj_lat is not None:
        result["winjupos_lat_deg"] = wj_lat
    if delta:
        result["delta_vs_wj"] = delta
    return result


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Validate the GRS measurement pipeline on a real photo."
    )
    ap.add_argument("--fits", help="path to a FITS/SER/PNG of Jupiter")
    ap.add_argument("--time", default=None,
                    help="mid-exposure UTC (e.g. '2026-07-14 12:00:00'). "
                         "If omitted, the FITS header / filename is used.")
    ap.add_argument("--wj-lon", type=float, default=None,
                    help="your manual WinJUPOS pick for GRS lon_iii (deg)")
    ap.add_argument("--wj-lat", type=float, default=None,
                    help="your manual WinJUPOS pick for GRS lat (deg)")
    ap.add_argument("--synthetic", action="store_true",
                    help="run a synthetic smoke test (no real photo needed)")
    ap.add_argument("--out", default=None,
                    help="output JSON path (default: <input>.real_photo_validate.json)")
    args = ap.parse_args()

    if not args.synthetic and not args.fits:
        ap.error("either --synthetic or --fits is required")

    if args.synthetic:
        result = _run_synthetic_smoke()
    else:
        fits_path = Path(args.fits).resolve()
        out_dir = fits_path.parent
        result = _run_real(
            fits_path=fits_path,
            time_str=args.time,
            wj_lon=args.wj_lon,
            wj_lat=args.wj_lat,
            out_dir=out_dir,
        )

    # Determine output path
    if args.out:
        out_path = Path(args.out)
    elif args.synthetic:
        out_path = Path("/tmp/real_photo_validate_synthetic.json")
    else:
        out_path = Path(args.fits).with_suffix(".real_photo_validate.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")

    # Human-readable summary
    print("=" * 60)
    print(f"Real-photo validation — {result.get('mode', '?')}")
    print("=" * 60)
    for k, v in result.items():
        if k == "per_estimator":
            continue
        if isinstance(v, dict):
            print(f"  {k}:")
            for k2, v2 in v.items():
                if isinstance(v2, float):
                    print(f"    {k2}: {v2:.4f}")
                else:
                    print(f"    {k2}: {v2}")
        elif isinstance(v, float):
            print(f"  {k}: {v:.4f}")
        else:
            print(f"  {k}: {v}")
    if "per_estimator" in result and result["per_estimator"]:
        print()
        print("Per-estimator Δsky vs WinJUPOS (if --wj-lon was given):")
        for name, entry in sorted(
                result["per_estimator"].items(),
                key=lambda kv: kv[1].get("sky_arcsec", 1e9)):
            sky = entry.get("sky_arcsec", None)
            sky_s = f"{sky:.3f}\"" if sky is not None else "  n/a"
            rej = " (rejected)" if entry.get("rejected") else ""
            print(f"  {name:18s}  sky={sky_s}  "
                  f"dlon={entry.get('dlon_deg', float('nan')):+.3f}  "
                  f"dlat={entry.get('dlat_deg', float('nan')):+.3f}{rej}")
    print()
    print(f"Full result written to: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

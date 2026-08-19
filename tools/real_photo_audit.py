#!/usr/bin/env python3
"""Audit the published GRS measurement on real photos.

Unlike certify / resolution_seeing_100 this does NOT invent a System III
truth for untimed JPEGs. It reports the things that *are* measurable:

  disk_present / softness / GRS-band lock / method agreement /
  RGB vs mono divergence / redness vs dark-cluster split.

Usage:
  python tools/real_photo_audit.py --glob 'real_photos/*' --out runs/real_photo_audit.json
"""
from __future__ import annotations

import argparse
import json
import sys
from glob import glob
from pathlib import Path

import numpy as np
from PIL import Image

APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


def _load(path: str, max_side: int = 1000) -> np.ndarray:
    im = Image.open(path).convert("RGB")
    if max(im.size) > max_side:
        s = max_side / max(im.size)
        im = im.resize((max(2, int(im.width * s)), max(2, int(im.height * s))), Image.LANCZOS)
    return np.asarray(im, dtype=np.float64) / 255.0


def analyse(path: str) -> dict:
    from accuracy_gates import grs_lat_in_core_band, grs_lat_in_wide_band
    from precision_engine import (
        fit_limb_nav, measure_grs_precision, to_mono, wrap_diff, GRS_LAT0,
    )

    out: dict = {"file": Path(path).name}
    try:
        rgb = _load(path)
    except Exception as e:
        return {**out, "ok": False, "error": f"load: {e}"}
    try:
        nav = fit_limb_nav(rgb)
        res = measure_grs_precision(
            rgb, cm_iii_deg=0.0, distance_au=nav.distance_au, nav=nav,
            quiet=True, map_width=1600, map_height=800, lean=True,
        )
        mono = to_mono(rgb)
        nav_m = fit_limb_nav(mono)
        res_m = measure_grs_precision(
            mono, cm_iii_deg=0.0, distance_au=nav_m.distance_au, nav=nav_m,
            quiet=True, map_width=1600, map_height=800, lean=True,
        )
    except Exception as e:
        return {**out, "ok": False, "error": f"{type(e).__name__}: {e}"}

    dq = (res.methods or {}).get("disk_quality") or {}
    methods = {}
    for name, m in (res.methods or {}).items():
        if isinstance(m, dict) and "lon_iii_deg" in m:
            methods[name] = {
                "lon": float(m["lon_iii_deg"]),
                "lat": float(m.get("lat_deg", float("nan"))),
                "rejected": bool(m.get("rejected", False)),
            }
    out.update(
        ok=True,
        a_eq_px=float(nav.a_eq_px),
        north_pa_stored=float(nav.north_pa_deg),
        lon=float(res.lon_iii_deg),
        lat=float(res.lat_deg),
        method=res.method,
        quality=float(res.quality),
        in_core=bool(grs_lat_in_core_band(res.lat_deg)),
        in_wide=bool(grs_lat_in_wide_band(res.lat_deg)),
        disk_present=bool(dq.get("disk_present", dq.get("measurable"))),
        measurable=bool(dq.get("measurable")),
        softness_arcsec=dq.get("softness_arcsec"),
        disk_fill=dq.get("disk_fill"),
        disk_contrast=dq.get("disk_contrast"),
        publish_path=(res.methods or {}).get("publish_path"),
        methods=methods,
        mono_lon=float(res_m.lon_iii_deg),
        mono_lat=float(res_m.lat_deg),
        rgb_vs_mono_dlon=float(wrap_diff(res.lon_iii_deg, res_m.lon_iii_deg)),
        GRS_LAT0=float(GRS_LAT0),
    )
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--glob", default="real_photos/*")
    ap.add_argument("--out", default="runs/real_photo_audit.json")
    args = ap.parse_args()
    files = [
        f for f in sorted(glob(args.glob))
        if Path(f).suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".tif", ".fits", ".fit"}
    ]
    if not files:
        print(f"no images matched {args.glob}")
        return 1
    rows = []
    for f in files:
        r = analyse(f)
        rows.append(r)
        if r.get("ok"):
            print(
                f"{r['file'][:42]:44} lat={r['lat']:+7.2f} core={r['in_core']} "
                f"soft={r.get('softness_arcsec')} present={r['disk_present']} "
                f"meth={r['method']}",
                flush=True,
            )
        else:
            print(f"{Path(f).name:44} FAIL {r.get('error')}", flush=True)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, indent=2, default=str), encoding="utf-8")
    print("wrote", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""
Post-process the detailed_audit_1000 jsonl cache into a human-readable
deterioration + error-detection report.

Produces (printed to stdout, and written to runs/audit_report_analysis.json):

  1. Overall head-to-head: lon vs lat error (median / p90 / p99 / max),
     % within 0.2 deg, 0.5 deg, 1.0 deg, plus sky arcsec error.
  2. Per-cell (resolution x seeing) table: n, lon med/p90/max, lat med/max,
     % <=0.2, % <=0.5, sky-arcsec median.
  3. Deterioration curves:
     a. error vs seeing (clear->mild->blurry->vblurry) at each resolution
     b. error vs resolution (1080p->720p->540p) at each seeing tier
  4. Worst-case tail: the top-N frames by combined error, with their
     method + quality flag, so we can see *what* fails as data degrades.

Usage: python tools/audit_report_analysis.py
"""
from __future__ import annotations

import json
import statistics as st
from pathlib import Path
from typing import Any, Dict, List

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "runs" / "detailed_audit_1000.jsonl"


def load() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not CACHE.exists():
        return rows
    for line in CACHE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def pct(a: List[float], p: float) -> float:
    if not a:
        return float("nan")
    a = sorted(a)
    k = (len(a) - 1) * p / 100.0
    f, c = int(k), min(int(k) + 1, len(a) - 1)
    return a[f] * (c - k) + a[c] * (k - f) if f != c else a[f]


def main() -> int:
    rows = [r for r in load() if r.get("ok")]
    if not rows:
        print("No ok rows in cache yet.")
        return 1

    n = len(rows)
    dl = [r["abs_dlon"] for r in rows]
    db = [r["abs_dlat"] for r in rows]
    sky = [r.get("sky_arcsec", float("nan")) for r in rows]
    sky = [s for s in sky if s == s]  # drop nan

    def within(a, b, thr):
        return sum(1 for x, y in zip(a, b) if x <= thr and y <= thr) / n

    out: Dict[str, Any] = {
        "n": n,
        "overall": {
            "lon_deg": {"med": pct(dl, 50), "p90": pct(dl, 90), "p99": pct(dl, 99), "max": max(dl)},
            "lat_deg": {"med": pct(db, 50), "p90": pct(db, 90), "p99": pct(db, 99), "max": max(db)},
            "sky_arcsec": {"med": pct(sky, 50), "p90": pct(sky, 90), "max": max(sky)} if sky else {},
            "within_02": within(dl, db, 0.2),
            "within_05": within(dl, db, 0.5),
            "within_10": within(dl, db, 1.0),
        },
        "by_cell": {},
    }

    res_list = ("1080p", "720p", "540p")
    qual_list = ("clear", "mild", "blurry", "vblurry")

    for res in res_list:
        for q in qual_list:
            tr = [r for r in rows if r["resolution"] == res and r["quality"] == q]
            if not tr:
                continue
            key = f"{res}/{q}"
            out["by_cell"][key] = {
                "n": len(tr),
                "lon_med": pct([r["abs_dlon"] for r in tr], 50),
                "lon_p90": pct([r["abs_dlon"] for r in tr], 90),
                "lon_max": max(r["abs_dlon"] for r in tr),
                "lat_med": pct([r["abs_dlat"] for r in tr], 50),
                "lat_p90": pct([r["abs_dlat"] for r in tr], 90),
                "lat_max": max(r["abs_dlat"] for r in tr),
                "sky_med_arcsec": pct([r.get("sky_arcsec", 0.0) for r in tr], 50),
                "within_02": sum(1 for r in tr if r["abs_dlon"] <= 0.2 and r["abs_dlat"] <= 0.2) / len(tr),
                "within_05": sum(1 for r in tr if r["abs_dlon"] <= 0.5 and r["abs_dlat"] <= 0.5) / len(tr),
                "within_10": sum(1 for r in tr if r["abs_dlon"] <= 1.0 and r["abs_dlat"] <= 1.0) / len(tr),
            }

    # Deterioration curves
    curve_seeing: Dict[str, Dict[str, float]] = {}  # res -> {quality: lon_med}
    curve_res: Dict[str, Dict[str, float]] = {}      # quality -> {res: lon_med}
    for res in res_list:
        curve_seeing[res] = {}
        for q in qual_list:
            tr = [r for r in rows if r["resolution"] == res and r["quality"] == q]
            curve_seeing[res][q] = pct([r["abs_dlon"] for r in tr], 50) if tr else float("nan")
    for q in qual_list:
        curve_res[q] = {}
        for res in res_list:
            tr = [r for r in rows if r["resolution"] == res and r["quality"] == q]
            curve_res[q][res] = pct([r["abs_dlon"] for r in tr], 50) if tr else float("nan")
    out["curve_lon_med_vs_seeing"] = curve_seeing
    out["curve_lon_med_vs_resolution"] = curve_res

    # Worst tail: combined error = max(abs_dlon, abs_dlat) or hypot; use lon+lat sum
    worst = sorted(rows, key=lambda r: r["abs_dlon"] + r["abs_dlat"], reverse=True)[:15]
    out["worst_tail"] = [
        {
            "case": r["case_id"],
            "resolution": r["resolution"],
            "quality": r["quality"],
            "dlon": round(r["abs_dlon"], 3),
            "dlat": round(r["abs_dlat"], 3),
            "sky_arcsec": round(r.get("sky_arcsec", 0.0), 3),
            "method": r.get("method"),
            "quality_flag": round(r.get("quality_flag", -1), 3),
            "disk_a_px": r.get("disk_a_px"),
        }
        for r in worst
    ]

    # method distribution among failures (>0.5 deg)
    fails = [r for r in rows if r["abs_dlon"] > 0.5 or r["abs_dlat"] > 0.5]
    from collections import Counter
    out["fail_method_hist"] = dict(Counter(r.get("method", "?") for r in fails))

    out_path = CACHE.parent / "audit_report_analysis.json"
    out_path.write_text(json.dumps(out, indent=2))
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

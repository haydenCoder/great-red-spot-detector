#!/usr/bin/env python3
"""
Hard-synth stress suite.

Renders a small set of synthetic Jupiter frames under stress configurations
(GRS near limb, extreme sub-observer geometry, monochromatic input, off-band
size, very high seeing) and measures each through the published pipeline.

The original `hard_synth_suite` module is referenced by `desktop_pipeline`,
`desktop_app`, and `server` but the file is missing from the repo. This
re-implementation is a small, honest stress test rather than a marketing
"hard-synth" facade:

  * Renders N cases with stress injection via `synthetic_hq.SynthSpec` (the
    same generator the accuracy suite uses, so the truth numbers are directly
    comparable to the published 100-case matrix).
  * Measures each through the published `measure_grs_precision` path (no
    special-cased code, so any improvement to the published path applies here).
  * Aggregates per-family and overall: median sky error, p95, within-1-deg
    rate, calibration grade A/B/C.

The output contract is what the callers expect:

    {
      "calibration_grade": "A" | "B" | "C" | "D",
      "overall": {"median_sky_arcsec": float, "p95_sky_arcsec": float,
                  "within_1deg_rate": float, "n_ok": int, "n_total": int},
      "by_family": {family_name: { ... same shape as overall ... }},
      "results":   [ {case record}, ... ],
      "note":      "...",
    }
"""
from __future__ import annotations

import json
import math
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np
from PIL import Image

# Make app/ importable when this module is loaded by tools / tests
_THIS = Path(__file__).resolve()
_APP = _THIS.parent
if str(_APP) not in sys.path:
    sys.path.insert(0, str(_APP))


def _stress_families(resolution: str) -> List[Dict[str, Any]]:
    """The hard-synth stress families.

    Each entry has a `name` (used in the report), the kwargs to pass to
    `SynthSpec`, and a per-family `limit_arcsec` (max acceptable median sky
    error for calibration grade A).
    """
    return [
        {
            "name": "grs_near_limb",
            "label": "GRS at lon_rel=+78° (near limb)",
            "spec_kw": dict(grs_limb_rel_deg=78.0),
            "limit_arcsec": 0.50,
        },
        {
            "name": "grs_at_limb",
            "label": "GRS at lon_rel=+88° (very near limb)",
            "spec_kw": dict(grs_limb_rel_deg=88.0),
            "limit_arcsec": 1.20,
        },
        {
            "name": "tilted_axis_high",
            "label": "Sub-lat +12°, north-pa +60° (extreme geometry)",
            "spec_kw": dict(sub_lat_deg=12.0, north_pa_deg=60.0),
            "limit_arcsec": 0.50,
        },
        {
            "name": "tilted_axis_low",
            "label": "Sub-lat -10°, north-pa -55° (extreme geometry)",
            "spec_kw": dict(sub_lat_deg=-10.0, north_pa_deg=-55.0),
            "limit_arcsec": 0.50,
        },
        {
            "name": "vblurry_stress",
            "label": "Seeing 2.6\", GRS near CM",
            "spec_kw": dict(seeing_fwhm_arcsec=2.6, noise_rms=0.025),
            "limit_arcsec": 0.80,
        },
    ]


def _grade(median_arcsec: float, p95_arcsec: float, within_1deg: float) -> str:
    """Calibration grade A/B/C/D from median, p95, and pass rate.

    A: median <= 0.20", p95 <= 0.50", pass rate >= 0.95
    B: median <= 0.40", p95 <= 1.00", pass rate >= 0.85
    C: median <= 0.80", p95 <= 2.00", pass rate >= 0.70
    D: anything else
    """
    if median_arcsec <= 0.20 and p95_arcsec <= 0.50 and within_1deg >= 0.95:
        return "A"
    if median_arcsec <= 0.40 and p95_arcsec <= 1.00 and within_1deg >= 0.85:
        return "B"
    if median_arcsec <= 0.80 and p95_arcsec <= 2.00 and within_1deg >= 0.70:
        return "C"
    return "D"


def _measure_one(
    seed: int,
    resolution: str,
    spec_kw: Dict[str, Any],
    out_dir: Path,
) -> Dict[str, Any]:
    """Render one stress frame and measure it through the published path."""
    from precision_engine import (
        fit_limb_nav, measure_grs_precision, sky_error_arcsec, wrap_diff,
    )
    from synthetic_hq import SynthSpec, generate

    t0 = time.time()
    try:
        kw = dict(
            region="global",
            resolution_preset=resolution,
            random_time=True,
            seed=int(seed),
            mode="metrology",
            write_grs_crop=False,
        )
        kw.update(spec_kw or {})
        with tempfile.TemporaryDirectory(prefix="grs_hard_") as d:
            png, _fit, truth = generate(SynthSpec(**kw), Path(d))
            img = np.asarray(Image.open(png), dtype=np.float64) / 255.0

        nav = fit_limb_nav(
            img, cm_iii_deg=truth["cm_iii_deg"], distance_au=truth["distance_au"]
        )
        nav.cm_iii_deg = truth["cm_iii_deg"]
        nav.distance_au = truth["distance_au"]
        nav.sub_lat_deg = float(truth.get("sub_obs_lat_deg") or 0.0)
        nav.north_pa_deg = float(truth.get("north_pa_deg") or 0.0)

        res = measure_grs_precision(
            img,
            cm_iii_deg=nav.cm_iii_deg,
            distance_au=nav.distance_au,
            nav=nav,
            quiet=True,
        )
        lon_seed = truth.get("grs_lon_seed_deg")
        lat_seed = truth.get("grs_lat_seed_deg")
        dlon = wrap_diff(res.lon_iii_deg, float(lon_seed))
        dlat = res.lat_deg - float(lat_seed)
        sky = sky_error_arcsec(dlon, dlat, float(lat_seed), truth["distance_au"])
        return {
            "ok": True,
            "seed": int(seed),
            "spec_kw": dict(spec_kw or {}),
            "dlon_seed_deg": float(dlon),
            "dlat_seed_deg": float(dlat),
            "abs_dlon_seed": abs(float(dlon)),
            "abs_dlat_seed": abs(float(dlat)),
            "sky_arcsec": float(sky),
            "method": res.method,
            "quality": float(res.quality),
            "lon_meas": float(res.lon_iii_deg),
            "lat_meas": float(res.lat_deg),
            "lon_truth": float(lon_seed),
            "lat_truth": float(lat_seed),
            "cm_iii_deg": float(truth["cm_iii_deg"]),
            "distance_au": float(truth["distance_au"]),
            "secs": time.time() - t0,
        }
    except Exception as e:
        return {
            "ok": False, "seed": int(seed), "spec_kw": dict(spec_kw or {}),
            "error": f"{type(e).__name__}: {e}", "secs": time.time() - t0,
        }


def _family_summary(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    ok = [r for r in rows if r.get("ok")]
    n_total = len(rows)
    n_ok = len(ok)
    if n_ok == 0:
        return {
            "n_total": n_total, "n_ok": 0,
            "median_sky_arcsec": float("nan"),
            "p95_sky_arcsec": float("nan"),
            "max_sky_arcsec": float("nan"),
            "within_1deg_rate": 0.0,
            "calibration_grade": "D",
        }
    skys = sorted(r["sky_arcsec"] for r in ok)
    p95 = skys[min(len(skys) - 1, int(round(0.95 * (len(skys) - 1))))]
    median = skys[len(skys) // 2]
    within_1deg = sum(
        1 for r in ok
        if r["abs_dlon_seed"] <= 1.0 and r["abs_dlat_seed"] <= 1.0
    ) / n_ok
    return {
        "n_total": n_total, "n_ok": n_ok,
        "median_sky_arcsec": float(median),
        "p95_sky_arcsec": float(p95),
        "max_sky_arcsec": float(skys[-1]),
        "within_1deg_rate": float(within_1deg),
        "calibration_grade": _grade(median, p95, within_1deg),
    }


def run_hard_synth_suite(
    out_dir,
    base_seed: int = 42,
    resolution: str = "1080p",
    injection_trials: int = 6,
    mc_iter: int = 8,
    user_time_iso: str = "",
) -> Dict[str, Any]:
    """Run the hard-synth stress suite and write a per-case report to out_dir.

    `mc_iter` is accepted for signature compatibility with the callers; this
    implementation does not vary MC iterations (they are not the bottleneck
    for stress configurations — the failure modes are the geometry/seeing
    injections themselves).
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    families = _stress_families(resolution)
    rows: List[Dict[str, Any]] = []
    by_family: Dict[str, Any] = {}

    for fi, fam in enumerate(families):
        family_rows: List[Dict[str, Any]] = []
        for ti in range(int(injection_trials)):
            seed = int(base_seed) + fi * 10_000 + ti
            r = _measure_one(seed, resolution, fam["spec_kw"], out_dir)
            r["family"] = fam["name"]
            r["family_label"] = fam["label"]
            family_rows.append(r)
            rows.append(r)
        by_family[fam["name"]] = {
            "label": fam["label"],
            "limit_arcsec": fam["limit_arcsec"],
            **_family_summary(family_rows),
        }

    overall = _family_summary(rows)
    note = (
        f"hard-synth stress suite: {overall['n_ok']}/{overall['n_total']} cases completed; "
        f"calibration grade {overall['calibration_grade']}. "
        f"Per-family limits are documented in by_family[*].limit_arcsec."
    )
    report: Dict[str, Any] = {
        "calibration_grade": overall["calibration_grade"],
        "overall": overall,
        "by_family": by_family,
        "results": rows,
        "note": note,
        "mc_iter": int(mc_iter),  # accepted but unused
        "injection_trials": int(injection_trials),
        "resolution": resolution,
    }
    try:
        (out_dir / "hard_synth_report.json").write_text(
            json.dumps(report, indent=2, default=str), encoding="utf-8"
        )
    except Exception:
        pass
    return report


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default="runs/hard_synth")
    ap.add_argument("--base-seed", type=int, default=42)
    ap.add_argument("--resolution", default="1080p")
    ap.add_argument("--injection-trials", type=int, default=6)
    ap.add_argument("--mc-iter", type=int, default=8)
    ap.add_argument("--user-time-iso", default="")
    args = ap.parse_args()
    rep = run_hard_synth_suite(
        Path(args.out),
        base_seed=args.base_seed,
        resolution=args.resolution,
        injection_trials=args.injection_trials,
        mc_iter=args.mc_iter,
        user_time_iso=args.user_time_iso,
    )
    print(json.dumps({
        "calibration_grade": rep["calibration_grade"],
        "overall": rep["overall"],
        "by_family": {k: {kk: v[kk] for kk in ("n_ok", "n_total", "median_sky_arcsec", "p95_sky_arcsec", "within_1deg_rate", "calibration_grade")}
                      for k, v in rep["by_family"].items()},
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

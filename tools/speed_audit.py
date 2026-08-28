#!/usr/bin/env python3
"""Stage-by-stage speed audit of the GRS measurement stack.

Renders (or reuses) a 1080p synthetic and times every hot stage: limb nav,
cylindrical maps, template/NCC searches, the VLBI-style definition suite,
phase-reference probes, hierarchical MC, and the all-methods suite.

Usage:
    python tools/speed_audit.py                          # generate + audit
    python tools/speed_audit.py --image /tmp/x.npy       # reuse a saved RGB array
    python tools/speed_audit.py --json out.json          # machine-readable rows

The per-method breakdown (all_methods_extra) catches O(N²) regressions like
the per-pixel-median radial-symmetry rewrite (63 s -> 10 ms on a 1400x700
map) with the same harness.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

import numpy as np  # noqa: E402


def _t(fn):
    t0 = time.perf_counter()
    r = fn()
    return time.perf_counter() - t0, r


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--image", default="", help="existing RGB .npy instead of rendering")
    ap.add_argument("--json", default="", help="write rows to this JSON file")
    args = ap.parse_args()

    rows: list[dict] = []
    out = rows.append

    if args.image:
        im = np.load(args.image)
    else:
        import tempfile
        from PIL import Image
        from synthetic_hq import SynthSpec, generate

        t0 = time.perf_counter()
        with tempfile.TemporaryDirectory(prefix="grs_audit_") as d:
            png, _fit, _truth = generate(SynthSpec(
                region="global", resolution_preset="1080p", random_time=True,
                seed=42, mode="metrology", write_grs_crop=False), Path(d))
            im = np.asarray(Image.open(png).convert("RGB"))
        dt = time.perf_counter() - t0
        out({"stage": "render_1080p", "seconds": round(dt, 4)})
        print(f"render 1080p synthetic: {dt:.2f}s")

    mono = im.mean(axis=2)

    def staged(name, fn, extra=""):
        dt, r = _t(fn)
        out({"stage": name, "seconds": round(dt, 4), "extra": str(extra(r) if callable(extra) else extra)})
        print(f"{name:56s} {dt:9.3f}s {extra(r) if callable(extra) else extra}")
        return r

    from precision_engine import NavState, fit_limb_nav, make_cylindrical, _template_match_grs
    from vlbi_metrology import (fit_limb_advanced, build_ephemeris_approx,
                                make_cylindrical_oriented, multiscale_template_match,
                                measure_grs_vlbi, phase_reference_injection,
                                hierarchical_monte_carlo, definition_suite_vlbi)

    nav = staged("fit_limb_nav (720 rays)", lambda: fit_limb_nav(
        mono, n_rays=720, cm_iii_deg=150.0, distance_au=5.2),
        lambda r: f"a={r.a_eq_px:.1f}px")

    eph = build_ephemeris_approx("2026-08-01 04:30:00")
    eph.cm_iii_deg = 150.0
    anav = staged("fit_limb_advanced (bootstrap=36)",
                  lambda: fit_limb_advanced(mono, eph, n_rays=900, bootstrap=36, seed=0))

    staged("make_cylindrical 2400x1200", lambda: make_cylindrical(mono, nav, 2400, 1200))
    staged("make_cylindrical 2880x1440", lambda: make_cylindrical(mono, nav, 2880, 1440))
    cyl28 = make_cylindrical(mono, nav, 2880, 1440)
    staged("_template_match_grs (9 scales, 2880x1440)", lambda: _template_match_grs(cyl28, nav))
    staged("multiscale_template_match (25 scales, 2880x1440)",
           lambda: multiscale_template_match(cyl28, anav))
    staged("measure_grs_vlbi 2880x1440", lambda: measure_grs_vlbi(mono, anav, quiet=True),
           lambda r: f"lon={r.lon_iii_deg:.3f}")
    staged("definition_suite_vlbi 2400x1200", lambda: definition_suite_vlbi(mono, anav),
           lambda r: f"n={len(r)}")
    staged("phase_reference_injection n=4 (×7 = 28)",
           lambda: phase_reference_injection(mono, anav, 135.0, -22.0, n_trials=4, seed=0),
           lambda r: f"ok={len(r[0])}")
    staged("hierarchical_monte_carlo n=8 (×7.5 = 60)",
           lambda: hierarchical_monte_carlo(mono, anav, eph, n_iter=8, seed=11),
           lambda r: f"ok={r.get('n_success')}")

    from all_methods import run_all_methods
    staged("run_all_methods (cap 1400x700)",
           lambda: run_all_methods(mono, nav, map_width=1800, map_height=900),
           lambda r: f"ok={r['n_ok']}/{r['n_total']}")

    # per-method breakdown of the extra suite (catches per-pixel-loop regressions)
    from all_methods import _cyl_lon_lat_grids
    import all_methods_extra as ax
    cyl = make_cylindrical(mono, nav, 1400, 700)
    lon_iii, lat = _cyl_lon_lat_grids(cyl, nav)
    per: list[tuple[str, float]] = []
    for name in sorted(n for n in dir(ax) if n.startswith("m_")):
        fn = getattr(ax, name)
        if not callable(fn):
            continue
        t0 = time.perf_counter()
        try:
            fn(cyl, nav, lon_iii, lat)
        except Exception:
            pass
        per.append((name, time.perf_counter() - t0))
    per.sort(key=lambda x: -x[1])
    print("\n--- per extra-method timings (1400x700 map) ---")
    for name, dtm in per[:12]:
        print(f"{name:32s} {dtm:8.3f}s")
        out({"stage": f"extra.{name}", "seconds": round(dtm, 4)})

    if args.json:
        Path(args.json).write_text(json.dumps(rows, indent=2))
        print(f"\nrows -> {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

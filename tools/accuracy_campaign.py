#!/usr/bin/env python3
"""
Large-N accuracy campaign for the GRS measurement stack.

Runs the CORE metrology loop (render -> limb nav -> measure) over many seeds and
reports the distribution of |dlon| / |dlat| against truth. Deliberately skips
the report/publish/Monte-Carlo layers of the full desktop pipeline: those cost
~150 s per frame and do not change the measured centre, which is what we are
characterising here.

Parallel over processes (2 vCPU box), results streamed to JSONL so a run can be
resumed or inspected while in flight.

Usage:
    python tools/accuracy_campaign.py --n 200 --out runs/campaign.jsonl
    python tools/accuracy_campaign.py --n 200 --resume --out runs/campaign.jsonl
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

# Keep BLAS single-threaded; we parallelise over processes instead.
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ.setdefault(_v, "1")


def run_one(seed: int, resolution: str = "1080p", mode: str = "metrology",
            seeing: float | None = None, noise: float | None = None) -> dict:
    """Render one synthetic frame and measure it. Returns a result record."""
    import numpy as np
    from PIL import Image

    from precision_engine import (
        fit_limb_nav,
        measure_grs_precision,
        sky_error_arcsec,
        wrap_diff,
    )
    from synthetic_hq import SynthSpec, generate

    t0 = time.time()
    try:
        with tempfile.TemporaryDirectory(prefix="grs_camp_") as d:
            spec_kw = dict(
                region="global",
                resolution_preset=resolution,
                random_time=True,
                seed=int(seed),
                mode=mode,
                write_grs_crop=False,
            )
            if seeing is not None:
                spec_kw["seeing_fwhm_arcsec"] = float(seeing)
            if noise is not None:
                spec_kw["noise_rms"] = float(noise)
            png, _fit, truth = generate(SynthSpec(**spec_kw), Path(d))
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

        dlon = wrap_diff(res.lon_iii_deg, truth["grs_lon_iii_deg"])
        dlat = res.lat_deg - truth["grs_lat_deg"]
        sky = sky_error_arcsec(dlon, dlat, truth["grs_lat_deg"], truth["distance_au"])

        # Second truth channel: the GEOMETRIC oval centre the renderer planted.
        # truth["grs_*_deg"] is an intensity-weighted barycentre computed only
        # inside grs_mask, so it is pulled off-centre by the oval's own
        # brightness asymmetry (~0.24 deg north). Scoring against both keeps the
        # definition mismatch visible instead of blaming the estimator for it.
        lon_seed = truth.get("grs_lon_seed_deg")
        lat_seed = truth.get("grs_lat_seed_deg")
        dlon_s = dlat_s = float("nan")
        if lon_seed is not None and lat_seed is not None:
            dlon_s = wrap_diff(res.lon_iii_deg, float(lon_seed))
            dlat_s = res.lat_deg - float(lat_seed)

        # Limb-fit quality, the dominant systematic on real frames.
        # The synthetic truth stores the planted disk as top-level keys
        # disk_xc / disk_yc / disk_a_eq_px; some other truth sources nest it
        # under truth["nav"]. Read whichever is present so this column is not
        # silently NaN (it previously read only truth["nav"], which the synth
        # never sets, so every limb residual was NaN).
        nav_t = truth.get("nav") or {}
        d_xc = d_yc = d_a = float("nan")
        tx = nav_t.get("xc", truth.get("disk_xc"))
        ty = nav_t.get("yc", truth.get("disk_yc"))
        ta = nav_t.get("a_eq_px", nav_t.get("a_px", truth.get("disk_a_eq_px")))
        try:
            if tx is not None:
                d_xc = nav.xc - float(tx)
            if ty is not None:
                d_yc = nav.yc - float(ty)
            if ta is not None:
                d_a = nav.a_eq_px - float(ta)
        except (TypeError, ValueError):
            pass

        return {
            "seed": int(seed),
            "ok": True,
            "dlon_deg": float(dlon),
            "dlat_deg": float(dlat),
            "abs_dlon": abs(float(dlon)),
            "abs_dlat": abs(float(dlat)),
            "dlon_seed_deg": float(dlon_s),
            "dlat_seed_deg": float(dlat_s),
            "abs_dlon_seed": abs(float(dlon_s)),
            "abs_dlat_seed": abs(float(dlat_s)),
            "sky_arcsec": float(sky),
            "lon_meas": float(res.lon_iii_deg),
            "lat_meas": float(res.lat_deg),
            "lon_truth": float(truth["grs_lon_iii_deg"]),
            "lat_truth": float(truth["grs_lat_deg"]),
            "cm_iii_deg": float(truth["cm_iii_deg"]),
            "lon_rel_truth": float(wrap_diff(truth["grs_lon_iii_deg"], truth["cm_iii_deg"])),
            "distance_au": float(truth["distance_au"]),
            "method": res.method,
            "quality": float(res.quality),
            "d_xc": d_xc,
            "d_yc": d_yc,
            "d_a_px": d_a,
            "secs": time.time() - t0,
            "seeing": seeing,
            "noise": noise,
        }
    except Exception as e:
        return {"seed": int(seed), "ok": False, "error": f"{type(e).__name__}: {e}",
                "secs": time.time() - t0}


def summarise(rows: list[dict]) -> dict:
    import statistics as st

    ok = [r for r in rows if r.get("ok")]
    if not ok:
        return {"n": len(rows), "n_ok": 0}
    dl = sorted(r["abs_dlon"] for r in ok)
    db = sorted(r["abs_dlat"] for r in ok)
    sk = sorted(r["sky_arcsec"] for r in ok)

    def pct(a, p):
        if not a:
            return float("nan")
        k = (len(a) - 1) * p / 100.0
        f, c = int(k), min(int(k) + 1, len(a) - 1)
        return float(a[f] * (c - k) + a[c] * (k - f)) if f != c else float(a[f])

    sd = [r["dlon_deg"] for r in ok]
    sb = [r["dlat_deg"] for r in ok]
    seed_ok = [r for r in ok if r.get("abs_dlon_seed") == r.get("abs_dlon_seed")]
    gd = sorted(r["abs_dlon_seed"] for r in seed_ok)
    gb = sorted(r["abs_dlat_seed"] for r in seed_ok)
    return {
        "n": len(rows),
        "n_ok": len(ok),
        "fail_rate": 1.0 - len(ok) / max(len(rows), 1),
        "lon": {"median": pct(dl, 50), "p90": pct(dl, 90), "p99": pct(dl, 99), "max": dl[-1],
                "bias": st.fmean(sd), "sd": st.pstdev(sd) if len(sd) > 1 else 0.0},
        "lat": {"median": pct(db, 50), "p90": pct(db, 90), "p99": pct(db, 99), "max": db[-1],
                "bias": st.fmean(sb), "sd": st.pstdev(sb) if len(sb) > 1 else 0.0},
        "sky_arcsec": {"median": pct(sk, 50), "p90": pct(sk, 90), "max": sk[-1]},
        "vs_geometric_centre": {
            "n": len(seed_ok),
            "lon_median": pct(gd, 50), "lon_p90": pct(gd, 90), "lon_max": gd[-1] if gd else float("nan"),
            "lat_median": pct(gb, 50), "lat_p90": pct(gb, 90), "lat_max": gb[-1] if gb else float("nan"),
            "lon_bias": st.fmean([r["dlon_seed_deg"] for r in seed_ok]) if seed_ok else float("nan"),
            "lat_bias": st.fmean([r["dlat_seed_deg"] for r in seed_ok]) if seed_ok else float("nan"),
            "within_1deg": (sum(1 for r in seed_ok if r["abs_dlon_seed"] <= 1.0 and r["abs_dlat_seed"] <= 1.0) / len(seed_ok)) if seed_ok else float("nan"),
        },
        "within_1deg": sum(1 for r in ok if r["abs_dlon"] <= 1.0 and r["abs_dlat"] <= 1.0) / len(ok),
        "within_2deg": sum(1 for r in ok if r["abs_dlon"] <= 2.0 and r["abs_dlat"] <= 2.0) / len(ok),
        "mean_secs": st.fmean([r["secs"] for r in ok]),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--seed0", type=int, default=100_000)
    ap.add_argument("--stride", type=int, default=7919)
    ap.add_argument("--res", default="1080p")
    ap.add_argument("--seeing", type=float, default=None,
                    help="seeing FWHM arcsec; omit for the mode default")
    ap.add_argument("--seeing-range", default=None,
                    help="lo,hi -- draw seeing per frame, deterministic in the seed")
    ap.add_argument("--noise", type=float, default=None)
    ap.add_argument("--workers", type=int, default=max(1, (os.cpu_count() or 2)))
    ap.add_argument("--out", default="runs/campaign.jsonl")
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    seeds = [args.seed0 + i * args.stride for i in range(args.n)]
    done: dict[int, dict] = {}
    if args.resume and out.exists():
        for line in out.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(line)
                done[int(r["seed"])] = r
            except Exception:
                pass
        seeds = [s for s in seeds if s not in done]
        print(f"resume: {len(done)} already done, {len(seeds)} remaining", flush=True)

    rows = list(done.values())
    t0 = time.time()
    with out.open("a", encoding="utf-8") as fh, \
            ProcessPoolExecutor(max_workers=args.workers) as ex:
        rng_lo = rng_hi = None
        if args.seeing_range:
            rng_lo, rng_hi = (float(x) for x in args.seeing_range.split(","))

        def _seeing_for(sd: int):
            if rng_lo is None:
                return args.seeing
            # deterministic per-seed draw so the run stays reproducible
            frac = ((sd * 2654435761) % 10_000) / 10_000.0
            return rng_lo + frac * (rng_hi - rng_lo)

        def _noise_for(sv):
            if args.noise is not None:
                return args.noise
            if sv is None:
                return None
            # noise grows with seeing, as it does on real stacks
            return float(min(0.035, 0.004 + 0.006 * sv))

        futs = {}
        for s in seeds:
            sv = _seeing_for(s)
            futs[ex.submit(run_one, s, args.res, "metrology", sv, _noise_for(sv))] = s
        for i, fut in enumerate(as_completed(futs), 1):
            r = fut.result()
            rows.append(r)
            fh.write(json.dumps(r) + "\n")
            fh.flush()
            if i % 10 == 0 or i == len(seeds):
                el = time.time() - t0
                rate = i / max(el, 1e-9)
                eta = (len(seeds) - i) / max(rate, 1e-9)
                s = summarise(rows)
                print(
                    f"[{i}/{len(seeds)}] {rate*60:.1f}/min eta {eta/60:.1f}m | "
                    f"lon med {s['lon']['median']:.3f} p90 {s['lon']['p90']:.3f} | "
                    f"lat med {s['lat']['median']:.3f} | <=1deg {100*s['within_1deg']:.1f}%",
                    flush=True,
                )

    s = summarise(rows)
    print("\n" + json.dumps(s, indent=2))
    Path(str(out) + ".summary.json").write_text(json.dumps(s, indent=2), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

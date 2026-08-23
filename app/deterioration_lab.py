#!/usr/bin/env python3
"""
deterioration_lab.py — Jupiter deterioration & stacking power analyser
=====================================================================

A self-contained "how does accuracy fall off as the data degrades?" instrument
for the web UI. It sweeps the three things that actually break a GRS
measurement --

  * resolution  (480p .. 1080p, i.e. plate scale / disk radius in pixels)
  * seeing      (atmospheric FWHM in arcsec, the v6.8 seeing PSF)
  * noise       (photon-ish RMS)

-- and for every cell renders a synthetic Jupiter+GRS, measures it with the
published precision engine, and records:

  * |dLon|, |dLat|, sky error (arcsec) vs planted truth
  * the published method (redness-primary / template / consensus / ...)
  * disk softness (arcsec) and measurability gate
  * per-method votes (template / map_dark / moment / redness)

From the matrix it fits an honest error *floor* -- the seeing/resolution where
the sub-1-degree guarantee breaks -- and returns everything as plain dicts so
the Flask layer can JSON-ify it and the UI can chart it.

Design constraints (these matter):
  * Every cell uses ``lean=True`` so the scale-drift re-detection does not
    re-run the whole measurement 3x; a UI sweep must finish in seconds, not
    minutes. The lean path is what the batch accuracy campaigns use.
  * Rendering is 540p by default and 2 seeds/cell for the live UI; a
    ``full`` preset raises both for an offline report.
  * No global state, no network, no writes outside the caller's temp dir.

This is measurement, not ML: there is no training here. SPIRE-Net weights are
frozen by design; the "tuning" knob is the derotator prior blend (see
planetary_derotator._MEAS_PRIOR_BLEND), which is surfaced read-only.
"""
from __future__ import annotations

import math
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

import numpy as np

from precision_engine import (
    assess_disk_quality,
    fit_limb_nav,
    measure_grs_precision,
    wrap_diff,
)
from synthetic_hq import SynthSpec, generate

# Resolution presets swept by the lab. Keys are UI labels; values are the
# synthetic_hq preset name + an approximate disk-radius multiplier so the
# matrix covers "barely resolved" -> "good amateur stack".
RESOLUTIONS: Dict[str, str] = {
    "480p": "480p",
    "540p": "540p",
    "720p": "720p",
    "1080p": "1080p",
}

# Seeing tiers (arcsec FWHM). 0.4 = excellent, 1.0 = good, 2.4 = the published
# guarantee edge, 3.2/4.0/6.0 = the "where does it break" floor.
SEEING_TIERS: Tuple[float, ...] = (0.4, 0.8, 1.2, 1.8, 2.4, 3.2, 4.0, 6.0)

# Photon-ish noise RMS tiers (fraction of full scale on [0,1]).
NOISE_TIERS: Tuple[float, ...] = (0.004, 0.012, 0.025, 0.040)


@dataclass
class LabConfig:
    """Knobs for a deterioration sweep."""
    resolutions: Sequence[str] = field(default_factory=lambda: ("540p", "720p"))
    seeing: Sequence[float] = field(default_factory=lambda: SEEING_TIERS)
    noise: Sequence[float] = field(default_factory=lambda: (0.004,))
    seeds: int = 2
    seed_base: int = 7_100_000
    seed_stride: int = 7919
    sub_lat_deg: float = 0.0
    north_pa_deg: float = 0.0
    map_width: int = 1200
    map_height: int = 600
    # early-exit when a cell is already unmeasurable: don't burn more seeds
    early_exit: bool = True
    progress: Optional[Callable[[Dict[str, Any]], None]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "resolutions": list(self.resolutions),
            "seeing": [float(s) for s in self.seeing],
            "noise": [float(n) for n in self.noise],
            "seeds": int(self.seeds),
            "sub_lat_deg": float(self.sub_lat_deg),
            "north_pa_deg": float(self.north_pa_deg),
        }


def _noise_for(seeing: float) -> float:
    """Noise floor that scales mildly with seeing (matches seeing_floor_stress)."""
    return float(min(0.035, 0.004 + 0.006 * seeing))


def _measure_one(
    seed: int,
    resolution: str,
    seeing: float,
    noise: float,
    cfg: LabConfig,
    work_dir: Path,
) -> Dict[str, Any]:
    """Render one synthetic frame and measure it. Never raises -- a failure is a
    recorded cell with ``ok=False`` so the sweep keeps going."""
    t0 = time.time()
    try:
        spec = SynthSpec(
            region="global",
            resolution_preset=resolution,
            random_time=True,
            seed=int(seed),
            mode="metrology",
            write_grs_crop=False,
            seeing_fwhm_arcsec=float(seeing),
            noise_rms=float(noise),
            sub_lat_deg=float(cfg.sub_lat_deg),
            north_pa_deg=float(cfg.north_pa_deg),
        )
        png, _fit, truth = generate(spec, work_dir)
        from PIL import Image
        img = np.asarray(Image.open(png), dtype=np.float64) / 255.0

        nav = fit_limb_nav(
            img,
            cm_iii_deg=truth["cm_iii_deg"],
            distance_au=truth["distance_au"],
            north_pa_deg=float(truth.get("north_pa_deg") or 0.0),
        )
        nav.cm_iii_deg = float(truth["cm_iii_deg"])
        nav.distance_au = float(truth["distance_au"])
        nav.sub_lat_deg = float(truth.get("sub_obs_lat_deg") or cfg.sub_lat_deg)
        nav.north_pa_deg = float(truth.get("north_pa_deg") or cfg.north_pa_deg)

        res = measure_grs_precision(
            img,
            cm_iii_deg=nav.cm_iii_deg,
            distance_au=nav.distance_au,
            nav=nav,
            quiet=True,
            lean=True,
            map_width=cfg.map_width,
            map_height=cfg.map_height,
        )

        dlon = wrap_diff(float(res.lon_iii_deg), float(truth["grs_lon_iii_deg"]))
        dlat = float(res.lat_deg) - float(truth["grs_lat_deg"])

        dq = (res.methods or {}).get("disk_quality") or {}
        per_method: Dict[str, Dict[str, float]] = {}
        for name in ("template", "map_dark", "moment", "redness"):
            m = (res.methods or {}).get(name)
            if isinstance(m, dict) and m.get("lon_iii_deg") is not None:
                per_method[name] = {
                    "dlon": float(wrap_diff(
                        float(m["lon_iii_deg"]), float(truth["grs_lon_iii_deg"]))),
                    "dlat": float(m.get("lat_deg", float("nan"))
                                  ) - float(truth["grs_lat_deg"]),
                    "rejected": bool(m.get("rejected", False)),
                }

        return {
            "ok": True,
            "seed": int(seed),
            "resolution": resolution,
            "seeing": float(seeing),
            "noise": float(noise),
            "dlon": float(dlon),
            "dlat": float(dlat),
            "abs_dlon": float(abs(dlon)),
            "abs_dlat": float(abs(dlat)),
            "method": str(res.method),
            "quality": float(res.quality),
            "measurable": bool(dq.get("measurable", True)),
            "disk_present": bool(dq.get("disk_present", True)),
            "softness_arcsec": float(dq.get("softness_arcsec") or float("nan")),
            "err_sky_arcsec": float(res.err_sky_arcsec),
            "per_method": per_method,
            "elapsed_s": float(time.time() - t0),
        }
    except Exception as e:  # noqa: BLE001 - sweep must continue
        return {
            "ok": False,
            "seed": int(seed),
            "resolution": resolution,
            "seeing": float(seeing),
            "noise": float(noise),
            "error": f"{type(e).__name__}: {e}",
            "elapsed_s": float(time.time() - t0),
        }


def _aggregate(cells: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Reduce the per-seed cells of one (res, seeing, noise) bucket."""
    good = [c for c in cells if c.get("ok")]
    out: Dict[str, Any] = {
        "n": len(cells),
        "n_ok": len(good),
        "n_fail": len(cells) - len(good),
    }
    if not good:
        out.update({
            "median_abs_dlon": float("nan"),
            "p90_abs_dlon": float("nan"),
            "max_abs_dlon": float("nan"),
            "median_abs_dlat": float("nan"),
            "within_1deg": 0.0,
            "within_0p5deg": 0.0,
            "catastrophic_rate": 1.0,
            "measurable_rate": 0.0,
            "softness_arcsec": float("nan"),
            "methods": {},
        })
        return out

    dlon = np.array([c["abs_dlon"] for c in good], dtype=np.float64)
    dlat = np.array([c["abs_dlat"] for c in good], dtype=np.float64)
    soft = np.array([c.get("softness_arcsec", float("nan")) for c in good],
                    dtype=np.float64)
    soft = soft[np.isfinite(soft)]

    methods: Dict[str, int] = {}
    for c in good:
        methods[c["method"]] = methods.get(c["method"], 0) + 1

    out.update({
        "median_abs_dlon": float(np.median(dlon)),
        "p90_abs_dlon": float(np.percentile(dlon, 90)),
        "max_abs_dlon": float(np.max(dlon)),
        "median_abs_dlat": float(np.median(dlat)),
        "within_1deg": float(np.mean((dlon <= 1.0) & (dlat <= 1.0))),
        "within_0p5deg": float(np.mean((dlon <= 0.5) & (dlat <= 0.5))),
        "catastrophic_rate": float(np.mean(dlon > 10.0)),
        "measurable_rate": float(np.mean([c.get("measurable", True) for c in good])),
        "softness_arcsec": float(np.median(soft)) if soft.size else float("nan"),
        "methods": methods,
    })
    return out


def run_sweep(cfg: Optional[LabConfig] = None) -> Dict[str, Any]:
    """Run the resolution × seeing × noise deterioration matrix.

    Returns a JSON-serialisable report with the grid, per-cell aggregates, and
    a fitted error-floor summary.
    """
    cfg = cfg or LabConfig()
    t0 = time.time()

    rows: List[Dict[str, Any]] = []
    cells_all: List[Dict[str, Any]] = []
    total = (len(cfg.resolutions) * len(cfg.seeing) * len(cfg.noise)
             * max(1, cfg.seeds))
    done = 0

    with tempfile.TemporaryDirectory(prefix="grs_deterioration_") as tmp:
        work = Path(tmp)
        for res in cfg.resolutions:
            for seeing in cfg.seeing:
                for noise in cfg.noise:
                    bucket: List[Dict[str, Any]] = []
                    for s in range(max(1, cfg.seeds)):
                        seed = int(cfg.seed_base
                                   + s * int(cfg.seed_stride)
                                   + int(round(seeing * 1000))
                                   + hash(res) % 1009)
                        cell = _measure_one(
                            seed, res, float(seeing), float(noise), cfg, work)
                        bucket.append(cell)
                        cells_all.append(cell)
                        done += 1
                        if cfg.progress:
                            cfg.progress({
                                "done": done, "total": total,
                                "resolution": res, "seeing": seeing,
                                "noise": noise,
                            })
                        # Early exit: if the first seeds at a cell are all
                        # unmeasurable/catastrophic the rest won't help.
                        if (cfg.early_exit and s >= 1
                                and bucket and all(
                                    (not c.get("ok")) or c.get("abs_dlon", 99) > 10.0
                                    for c in bucket)):
                            break
                    agg = _aggregate(bucket)
                    agg.update({
                        "resolution": res, "seeing": float(seeing),
                        "noise": float(noise),
                    })
                    rows.append(agg)

    floor = _fit_error_floor(rows)
    method_breakdown = _method_breakdown(cells_all)
    report = {
        "ok": True,
        "config": cfg.to_dict(),
        "elapsed_s": float(time.time() - t0),
        "n_cells": len(cells_all),
        "rows": rows,
        "floor": floor,
        "method_breakdown": method_breakdown,
        "tips": TIPS,
    }
    return report


def _fit_error_floor(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Find the seeing at which the median |dLon| crosses 0.5 / 1.0 deg per
    resolution, by linear interpolation between the two bracketing tiers.

    This is an honest *measured* floor (from the sweep), not a claim.
    """
    by_res: Dict[str, List[Dict[str, Any]]] = {}
    for r in rows:
        by_res.setdefault(r["resolution"], []).append(r)

    floors: Dict[str, Dict[str, float]] = {}
    for res, items in by_res.items():
        items = sorted(items, key=lambda x: x["seeing"])
        f: Dict[str, float] = {}
        for threshold, key in ((0.5, "floor_0p5deg_seeing"),
                               (1.0, "floor_1deg_seeing")):
            cross = float("nan")
            for a, b in zip(items, items[1:]):
                ma = a.get("median_abs_dlon", float("nan"))
                mb = b.get("median_abs_dlon", float("nan"))
                if not (math.isfinite(ma) and math.isfinite(mb)):
                    continue
                if ma <= threshold < mb:
                    # linear interp in seeing
                    frac = ((threshold - ma) / (mb - ma)) if mb != ma else 0.0
                    cross = a["seeing"] + frac * (b["seeing"] - a["seeing"])
                    break
            if not math.isfinite(cross):
                # never crossed within the swept range
                worst = items[-1]
                cross = float(worst["seeing"]) if (
                    math.isfinite(worst.get("median_abs_dlon", float("nan")))
                    and worst["median_abs_dlon"] < threshold
                ) else float("nan")
            f[key] = cross
        # best (lowest seeing) cell's median = the resolution-limited floor
        best = min(
            (x for x in items if math.isfinite(x.get("median_abs_dlon", float("nan")))),
            key=lambda x: x["seeing"], default=None)
        f["best_median_abs_dlon"] = (
            float(best["median_abs_dlon"]) if best else float("nan"))
        floors[res] = f
    return floors


def _method_breakdown(cells: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Per-estimator accuracy across all swept cells (which method degrades
    first?). Tells the UI which estimator to trust as seeing worsens."""
    by_method: Dict[str, List[float]] = {}
    for c in cells:
        if not c.get("ok"):
            continue
        for name, mm in (c.get("per_method") or {}).items():
            if mm.get("rejected"):
                continue
            d = mm.get("dlon")
            if d is not None and math.isfinite(d):
                by_method.setdefault(name, []).append(abs(float(d)))
    out: Dict[str, Dict[str, float]] = {}
    for name, vals in by_method.items():
        a = np.asarray(vals, dtype=np.float64)
        out[name] = {
            "n": int(a.size),
            "median_abs_dlon": float(np.median(a)),
            "p90_abs_dlon": float(np.percentile(a, 90)),
            "within_1deg": float(np.mean(a <= 1.0)),
        }
    return out


# Best-practice tips surfaced in the UI. These are established amateur
# planetary-imaging discipline (AutoStakkert/RegiStax/WinJUPOS workflow), not
# claims invented for the UI.
TIPS: List[str] = [
    "Capture SER/AVI, not single JPEGs — lucky imaging needs thousands of frames to pick the sharpest 10-25%.",
    "The GRS is redder than the belts: an RGB frame lets the colour lock survive seeing that destroys dark-oval shape.",
    "Seeing FWHM below ~1.5\" keeps the engine in its sub-1° regime; above ~3\" the softness gate correctly drops confidence.",
    "Record mid-exposure UTC to the second: 1 minute of timing error ≈ 0.6° System III longitude.",
    "Derotate long captures (WinJUPOS-style) — at 36°/h a 5-minute video smears a GRS feature by ~3° unless undone.",
    "Use the red channel for the GRS oval; blue is weakest for belts and adds noise to the lock.",
    "A tight 3-way dark-method agreement in the GRS latitude band beats a single crisp template peak.",
    "Don't over-sharpen: noise-gated wavelets keep the oval; aggressive RL deconvolution invents dark cores.",
]


def analyse_real_image(
    image: np.ndarray,
    distance_au: float = 5.2,
    north_pa_deg: float = 0.0,
    max_side: int = 1400,
) -> Dict[str, Any]:
    """Grade ONE real (or loaded) Jupiter image the same way the sweep grades
    synthetic cells: disk/softness gate, per-method GRS votes, measurability.

    Pure numpy/PIL -- no disk, no network. Used by the web UI's "analyse your
    image" panel so a user can compare a real stack against the deterioration
    curves without NASA access.
    """
    from PIL import Image as _Image

    arr = np.asarray(image, dtype=np.float64)
    if arr.ndim == 2:
        arr = np.stack([arr] * 3, axis=-1)
    if arr.shape[0] > 3 and arr.shape[-1] not in (3, 4):
        # CHW -> HWC
        arr = np.moveaxis(arr, 0, -1)
    if arr.shape[-1] == 4:
        arr = arr[..., :3]
    if arr.max() > 1.5:
        arr = arr / (65535.0 if arr.max() > 4000 else 255.0)
    arr = np.clip(arr, 0.0, 1.0)

    h, w = arr.shape[:2]
    if max(h, w) > max_side:
        s = max_side / max(h, w)
        nh, nw = int(round(h * s)), int(round(w * s))
        pil = _Image.fromarray((arr * 255).astype(np.uint8)).resize(
            (nw, nh), _Image.LANCZOS)
        arr = np.asarray(pil, dtype=np.float64) / 255.0

    nav = fit_limb_nav(arr, cm_iii_deg=0.0, distance_au=distance_au,
                       north_pa_deg=north_pa_deg)
    nav.distance_au = distance_au
    nav.north_pa_deg = north_pa_deg

    dq = assess_disk_quality(arr, nav)
    out: Dict[str, Any] = {
        "ok": True,
        "width": int(arr.shape[1]),
        "height": int(arr.shape[0]),
        "disk_present": bool(dq.get("disk_present")),
        "measurable": bool(dq.get("measurable")),
        "disk_fill": float(dq.get("disk_fill", float("nan"))),
        "disk_contrast": float(dq.get("disk_contrast", float("nan"))),
        "softness_arcsec": float(dq.get("softness_arcsec", float("nan"))),
        "reasons": list(dq.get("reasons", [])),
        "per_method": {},
    }

    try:
        res = measure_grs_precision(
            arr, cm_iii_deg=0.0, distance_au=distance_au, nav=nav,
            quiet=True, lean=True, map_width=1200, map_height=600,
        )
        out["method"] = str(res.method)
        out["quality"] = float(res.quality)
        out["lon_iii_deg"] = float(res.lon_iii_deg)
        out["lat_deg"] = float(res.lat_deg)
        out["err_sky_arcsec"] = float(res.err_sky_arcsec)
        for name in ("template", "map_dark", "moment", "redness"):
            m = (res.methods or {}).get(name)
            if isinstance(m, dict) and m.get("lon_iii_deg") is not None:
                out["per_method"][name] = {
                    "lon_iii_deg": float(m["lon_iii_deg"]),
                    "lat_deg": float(m.get("lat_deg", float("nan"))),
                    "score": float(m.get("score", float("nan")))
                    if isinstance(m.get("score"), (int, float)) else None,
                    "rejected": bool(m.get("rejected", False)),
                }
    except Exception as e:  # noqa: BLE001
        out["measurement_error"] = f"{type(e).__name__}: {e}"

    # Plain-English verdict so the UI is useful for a homework report.
    verdict = []
    if not out["disk_present"]:
        verdict.append(
            "No dark-sky disk detected (tight crop / bright background / logo). "
            "The disk-present gate is intentionally strict.")
    elif not out["measurable"]:
        verdict.append("Disk present but below the measurability floor.")
    else:
        verdict.append("Disk detected and within the measurable regime.")
    if math.isfinite(out["softness_arcsec"]):
        s = out["softness_arcsec"]
        if s < 1.5:
            verdict.append(f"Limb softness {s:.2f}\" — sharp (sub-1° regime).")
        elif s < 3.0:
            verdict.append(f"Limb softness {s:.2f}\" — moderate seeing.")
        else:
            verdict.append(f"Limb softness {s:.2f}\" — soft; confidence reduced.")
    if out.get("per_method"):
        n_ok = sum(1 for m in out["per_method"].values() if not m["rejected"])
        verdict.append(f"{n_ok}/{len(out['per_method'])} estimators produced a vote.")
    out["verdict"] = verdict
    return out


def quick_deterioration_report() -> Dict[str, Any]:
    """A small, fast default report (2 resolutions, 2 seeds) for the UI's
    'Run demo' button."""
    cfg = LabConfig(
        resolutions=("540p", "720p"),
        seeing=SEEING_TIERS,
        noise=(0.004,),
        seeds=2,
    )
    return run_sweep(cfg)


if __name__ == "__main__":  # pragma: no cover
    import json
    rep = quick_deterioration_report()
    print(json.dumps(rep, indent=2)[:4000])

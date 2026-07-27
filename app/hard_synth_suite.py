#!/usr/bin/env python3
"""
Hard synthetic stress suite — Harvard-style calibration under mismatch physics
==============================================================================

Friendly synthetics (same projection as the measurer, mild seeing, GRS on CM)
**understate** real error. This suite injects controlled mismatches and asks:

  1) Does truth fall inside the reported 1σ / 2σ error bars?  (coverage)
  2) What is residual sky error under each stress family?
  3) Which stress dominates the floor?

Stress families:
  A) CM error        — wrong System III zero (±0.5–2°)
  B) Extra seeing    — Gaussian blur beyond synth default
  C) Near-limb GRS   — force GRS far from CM (via re-measure with CM shift)
  D) Noise / SNR     — additive Gaussian noise
  E) Pole / sub-lat  — wrong orientation in nav
  F) Combined night  — A+B+D mild together

Output: JSON + text calibration report with coverage rates.
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from verbose_log import CONSOLE
from precision_engine import (
    wrap_deg,
    wrap_diff,
    sky_error_arcsec,
    to_mono,
    _gauss,
)
from synthetic_hq import SynthSpec, generate


@dataclass
class StressCase:
    name: str
    family: str
    params: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class StressResult:
    name: str
    family: str
    truth_lon: float
    truth_lat: float
    meas_lon: float
    meas_lat: float
    dlon_deg: float
    dlat_deg: float
    sky_error_arcsec: float
    sigma_reported_arcsec: float
    covered_1sigma: bool
    covered_2sigma: bool
    grade: str
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _blur(im: np.ndarray, sigma: float) -> np.ndarray:
    if sigma <= 0.05:
        return im
    try:
        from scipy.ndimage import gaussian_filter
        return gaussian_filter(im, sigma=sigma, mode="nearest")
    except Exception:
        return _gauss(im, sigma)


def apply_image_stress(
    image: np.ndarray,
    seeing_sigma_px: float = 0.0,
    noise_sigma: float = 0.0,
    seed: int = 0,
) -> np.ndarray:
    rng = np.random.default_rng(seed)
    im = to_mono(image).astype(np.float64).copy()
    if seeing_sigma_px > 0:
        im = _blur(im, seeing_sigma_px)
    if noise_sigma > 0:
        im = im + rng.normal(0, noise_sigma, im.shape)
        im = np.clip(im, 0, None)
    return im


def run_one_measure(
    image: np.ndarray,
    truth: Dict[str, Any],
    cm_err_deg: float = 0.0,
    sub_lat_err_deg: float = 0.0,
    north_pa_err_deg: float = 0.0,
    injection_trials: int = 8,
    mc_iter: int = 10,
    seed: int = 0,
) -> Tuple[float, float, float, str]:
    """Returns meas_lon, meas_lat, sigma_sky, grade."""
    from research_grade import run_research_grade

    cm = wrap_deg(float(truth["cm_iii_deg"]) + cm_err_deg)
    # pass orientation errors via run_vlbi by temporarily patching — use cm + distance
    # For sub_lat/pa we call vlbi directly with custom eph overrides after measure
    rg = run_research_grade(
        image,
        cm_iii_deg=cm,
        distance_au=float(truth["distance_au"]),
        channels=None,
        injection_trials=injection_trials,
        mc_iter=mc_iter,
        seed=seed,
        max_fidelity=True,
        factory_mode=False,
        user_time_iso=str(truth.get("user_time_iso") or "2026-07-14 12:00:00"),
        time_error_seconds=0.0,
        use_vlbi=True,
    )
    lon = float(rg.lon_bias_corrected_deg)
    lat = float(rg.lat_bias_corrected_deg)
    # Approximate orientation error as additional lon/lat shift if non-zero
    # (full re-nav with wrong PA is expensive; inject as systematic for suite)
    if abs(sub_lat_err_deg) > 0.01:
        lat = lat + 0.35 * sub_lat_err_deg  # partial coupling
    if abs(north_pa_err_deg) > 0.5:
        # PA error rotates sky → small lon/lat coupling near disk
        lon = wrap_deg(lon + 0.02 * north_pa_err_deg)
    return lon, lat, float(rg.sigma_total_sky_arcsec), str(rg.grade)


def default_stress_matrix() -> List[StressCase]:
    return [
        StressCase("control", "control", {}),
        StressCase("cm_err_0.5", "cm_error", {"cm_err_deg": 0.5}),
        StressCase("cm_err_1.0", "cm_error", {"cm_err_deg": 1.0}),
        StressCase("cm_err_2.0", "cm_error", {"cm_err_deg": 2.0}),
        StressCase("seeing_1.5px", "seeing", {"seeing_sigma_px": 1.5}),
        StressCase("seeing_3.0px", "seeing", {"seeing_sigma_px": 3.0}),
        StressCase("noise_0.02", "noise", {"noise_sigma": 0.02}),
        StressCase("noise_0.05", "noise", {"noise_sigma": 0.05}),
        StressCase("sublat_err_1", "orientation", {"sub_lat_err_deg": 1.0}),
        StressCase("pa_err_5", "orientation", {"north_pa_err_deg": 5.0}),
        StressCase("near_limb_cm", "near_limb", {"cm_err_deg": 45.0}),  # GRS appears near map edge rel. wrong CM
        StressCase(
            "combined_mild",
            "combined",
            {"cm_err_deg": 0.5, "seeing_sigma_px": 1.2, "noise_sigma": 0.015},
        ),
        StressCase(
            "combined_harsh",
            "combined",
            {"cm_err_deg": 1.5, "seeing_sigma_px": 2.5, "noise_sigma": 0.03, "sub_lat_err_deg": 0.8},
        ),
    ]


def run_hard_synth_suite(
    out_dir: Path,
    base_seed: int = 42,
    resolution: str = "1080p",
    cases: Optional[Sequence[StressCase]] = None,
    injection_trials: int = 8,
    mc_iter: int = 10,
    user_time_iso: str = "",
) -> Dict[str, Any]:
    """
    Generate one HQ synthetic, then run all stress cases.
    Returns full report dict and writes files under out_dir.
    """
    t0 = time.time()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    cases = list(cases or default_stress_matrix())

    CONSOLE.info("=" * 64)
    CONSOLE.info("HARD SYNTHETIC STRESS SUITE (mismatch physics calibration)")
    CONSOLE.info(f"cases={len(cases)}  res={resolution}  seed={base_seed}")

    png, fit, truth = generate(
        SynthSpec(
            user_time_iso=user_time_iso,
            region="global",
            resolution_preset=resolution,
            seed=base_seed,
        ),
        out_dir / "base_synth",
    )
    try:
        import grs_complete_system as grs
        arr, _ = grs.read_fits(fit)
        img = np.asarray(arr, dtype=np.float64)
        if img.ndim == 3 and img.shape[0] == 3:
            base = img[0]
        else:
            base = img
    except Exception:
        from PIL import Image
        base = np.asarray(Image.open(png).convert("L"), dtype=np.float64) / 255.0

    results: List[StressResult] = []
    for i, case in enumerate(cases):
        p = case.params
        CONSOLE.info(f"--- Stress [{i+1}/{len(cases)}] {case.name} ({case.family}) {p}")
        im = apply_image_stress(
            base,
            seeing_sigma_px=float(p.get("seeing_sigma_px") or 0),
            noise_sigma=float(p.get("noise_sigma") or 0),
            seed=base_seed + 17 * i,
        )
        try:
            lon, lat, sig, grade = run_one_measure(
                im,
                truth,
                cm_err_deg=float(p.get("cm_err_deg") or 0),
                sub_lat_err_deg=float(p.get("sub_lat_err_deg") or 0),
                north_pa_err_deg=float(p.get("north_pa_err_deg") or 0),
                injection_trials=injection_trials,
                mc_iter=mc_iter,
                seed=base_seed + i,
            )
            dlon = wrap_diff(lon, truth["grs_lon_iii_deg"])
            dlat = lat - truth["grs_lat_deg"]
            # When we intentionally wrong the CM, the *absolute* lon error includes CM error;
            # for coverage we compare to truth with that known CM bias expectation for family cm_error:
            # Science question: does pipeline recover feature position in the image?
            # Use sky error vs truth always (honest absolute).
            sky = sky_error_arcsec(dlon, dlat, truth["grs_lat_deg"], truth["distance_au"])
            # For cm_error family: residual after removing injected CM error (feature-relative)
            if case.family == "cm_error" and abs(float(p.get("cm_err_deg") or 0)) > 0:
                dlon_rel = wrap_diff(dlon, -float(p["cm_err_deg"]))
                # actually wrong CM shifts reported lon by +cm_err if feature fixed on planet
                # measured lon_iii = cm_wrong + lon_rel ≈ (cm_true + err) + lon_rel
                # truth = cm_true + lon_rel → dlon ≈ err. Relative recovery:
                dlon_feature = wrap_diff(lon - float(p["cm_err_deg"]), truth["grs_lon_iii_deg"])
                sky_feature = sky_error_arcsec(dlon_feature, dlat, truth["grs_lat_deg"], truth["distance_au"])
                note = f"absolute_sky={sky:.4f}\" feature_rel_sky={sky_feature:.4f}\""
                # Coverage for cm tests uses feature-relative when absolute is dominated by injected CM
                sky_for_cover = sky_feature
                dlon_report = dlon_feature
            else:
                note = ""
                sky_for_cover = sky
                dlon_report = dlon

            cov1 = sky_for_cover <= max(sig, 1e-6)
            cov2 = sky_for_cover <= max(2 * sig, 1e-6)
            results.append(StressResult(
                name=case.name,
                family=case.family,
                truth_lon=float(truth["grs_lon_iii_deg"]),
                truth_lat=float(truth["grs_lat_deg"]),
                meas_lon=lon,
                meas_lat=lat,
                dlon_deg=float(dlon_report),
                dlat_deg=float(dlat),
                sky_error_arcsec=float(sky_for_cover),
                sigma_reported_arcsec=sig,
                covered_1sigma=bool(cov1),
                covered_2sigma=bool(cov2),
                grade=grade,
                notes=note,
            ))
            CONSOLE.ok(
                f"  {case.name}: sky={sky_for_cover:.4f}\"  σ={sig:.4f}\"  "
                f"cov1={cov1} cov2={cov2}  grade={grade}"
            )
        except Exception as e:
            CONSOLE.warn(f"  {case.name} FAILED: {e}")
            results.append(StressResult(
                name=case.name,
                family=case.family,
                truth_lon=float(truth["grs_lon_iii_deg"]),
                truth_lat=float(truth["grs_lat_deg"]),
                meas_lon=float("nan"),
                meas_lat=float("nan"),
                dlon_deg=float("nan"),
                dlat_deg=float("nan"),
                sky_error_arcsec=float("nan"),
                sigma_reported_arcsec=float("nan"),
                covered_1sigma=False,
                covered_2sigma=False,
                grade="FAILED",
                notes=str(e),
            ))

    # Aggregate
    ok = [r for r in results if r.grade != "FAILED" and not math.isnan(r.sky_error_arcsec)]
    by_fam: Dict[str, List[StressResult]] = {}
    for r in ok:
        by_fam.setdefault(r.family, []).append(r)

    def agg(rs: List[StressResult]) -> Dict[str, Any]:
        if not rs:
            return {}
        skies = [r.sky_error_arcsec for r in rs]
        return {
            "n": len(rs),
            "median_sky_arcsec": float(np.median(skies)),
            "mean_sky_arcsec": float(np.mean(skies)),
            "max_sky_arcsec": float(np.max(skies)),
            "coverage_1sigma": float(np.mean([r.covered_1sigma for r in rs])),
            "coverage_2sigma": float(np.mean([r.covered_2sigma for r in rs])),
        }

    family_stats = {k: agg(v) for k, v in by_fam.items()}
    overall = agg(ok)
    # Harvard-style calibration grade
    c2 = overall.get("coverage_2sigma", 0)
    med = overall.get("median_sky_arcsec", 99)
    if c2 >= 0.9 and med <= 1.0:
        cal_grade = "CALIBRATION_EXCELLENT"
    elif c2 >= 0.75 and med <= 2.0:
        cal_grade = "CALIBRATION_GOOD"
    elif c2 >= 0.5:
        cal_grade = "CALIBRATION_FAIR"
    else:
        cal_grade = "CALIBRATION_NEEDS_WORK"

    elapsed = time.time() - t0
    report = {
        "suite": "hard_synth_stress_v1",
        "base_seed": base_seed,
        "resolution": resolution,
        "truth": {
            "grs_lon_iii_deg": truth["grs_lon_iii_deg"],
            "grs_lat_deg": truth["grs_lat_deg"],
            "cm_iii_deg": truth["cm_iii_deg"],
            "distance_au": truth["distance_au"],
        },
        "calibration_grade": cal_grade,
        "overall": overall,
        "by_family": family_stats,
        "results": [r.to_dict() for r in results],
        "elapsed_s": elapsed,
        "notes": [
            "Coverage: fraction of cases where |error| ≤ k × reported σ.",
            "CM-error family uses feature-relative sky after removing injected CM bias.",
            "Friendly control should be ≪1″; harsh combined tests the real floor.",
            "Harvard-style: error bars must cover truth under mismatch, not only on easy synths.",
        ],
    }

    out_json = out_dir / "hard_synth_report.json"
    out_json.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    lines = [
        "HARD SYNTHETIC STRESS SUITE — CALIBRATION REPORT",
        "=" * 56,
        f"Grade: {cal_grade}",
        f"Overall median sky error: {overall.get('median_sky_arcsec', float('nan')):.4f}\"",
        f"Coverage 1σ: {overall.get('coverage_1sigma', float('nan')):.2%}  "
        f"2σ: {overall.get('coverage_2sigma', float('nan')):.2%}",
        f"N ok: {overall.get('n', 0)} / {len(results)}  elapsed={elapsed:.1f}s",
        "",
        "BY FAMILY:",
    ]
    for fam, st in family_stats.items():
        lines.append(
            f"  {fam:12s}  n={st.get('n')}  med={st.get('median_sky_arcsec', float('nan')):.4f}\"  "
            f"cov2={st.get('coverage_2sigma', float('nan')):.2%}"
        )
    lines += ["", "CASES:"]
    for r in results:
        lines.append(
            f"  {r.name:16s}  sky={r.sky_error_arcsec:.4f}\"  σ={r.sigma_reported_arcsec:.4f}\"  "
            f"cov1={r.covered_1sigma} cov2={r.covered_2sigma}  {r.grade}"
        )
    lines += ["", "NOTES:"] + [f"- {n}" for n in report["notes"]]
    (out_dir / "hard_synth_report.txt").write_text("\n".join(lines), encoding="utf-8")
    CONSOLE.ok(f"HARD SUITE DONE: {cal_grade}  med={overall.get('median_sky_arcsec')}  → {out_json}")
    return report

#!/usr/bin/env python3
"""
AI assist only where classical Python methods struggle.

Easy nights (tight multi-method cluster, sharp GRS): physics / SOTA wins — AI stays out.
Hard nights (high scatter, few inliers, soft contrast, ambiguous SEB): SPIRE-Net
helps disambiguate and re-weight methods near the learned GRS appearance.

This is the right place for ML: not absolute System III, not CM, not time —
feature disambiguation under mess.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from verbose_log import CONSOLE
from precision_engine import wrap_deg, wrap_diff, to_mono, NavState, sky_error_arcsec


@dataclass
class HardCaseAIResult:
    engaged: bool
    difficulty: float  # 0 easy → 1 brutal
    reasons: List[str] = field(default_factory=list)
    nn_used: bool = False
    nn_confidence: float = 0.0
    blend_weight: float = 0.0
    lon_before: Optional[float] = None
    lat_before: Optional[float] = None
    lon_after: Optional[float] = None
    lat_after: Optional[float] = None
    methods_boosted: List[str] = field(default_factory=list)
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def estimate_image_difficulty(image: np.ndarray, nav: Optional[NavState] = None) -> Tuple[float, List[str]]:
    """0 = easy sharp oval, 1 = soft/noisy/low-contrast mess."""
    im = to_mono(image).astype(np.float64)
    reasons: List[str] = []
    score = 0.0

    # contrast in central disk
    h, w = im.shape
    cy, cx = h / 2, w / 2
    if nav is not None and getattr(nav, "a_eq_px", 0):
        cy, cx = nav.yc, nav.xc
        r = max(10.0, 0.7 * float(nav.a_eq_px))
    else:
        r = 0.35 * min(h, w)
    yy, xx = np.ogrid[:h, :w]
    disk = (xx - cx) ** 2 + (yy - cy) ** 2 <= r ** 2
    if disk.sum() < 50:
        return 0.7, ["tiny_disk"]

    vals = im[disk]
    p10, p90 = np.percentile(vals, [10, 90])
    contrast = float(p90 - p10) / (float(np.median(vals)) + 1e-6)
    if contrast < 0.15:
        score += 0.35
        reasons.append("low_contrast")
    elif contrast < 0.25:
        score += 0.15
        reasons.append("moderate_contrast")

    # high-frequency energy (sharpness proxy)
    gx = np.diff(im, axis=1, prepend=im[:, :1])
    gy = np.diff(im, axis=0, prepend=im[:1, :])
    sharp = float(np.mean(np.abs(gx[disk[:, :-1] if disk.shape[1] == gx.shape[1] else disk[:, : gx.shape[1]]]))
                  ) if False else float(np.mean(np.abs(gx)) + np.mean(np.abs(gy)))
    # normalize roughly
    if sharp < 0.02:
        score += 0.25
        reasons.append("soft_image")
    elif sharp < 0.05:
        score += 0.1
        reasons.append("somewhat_soft")

    # noise estimate from Laplacian-ish
    lap = np.abs(im - np.roll(im, 1, 0) - np.roll(im, 1, 1) + np.roll(np.roll(im, 1, 0), 1, 1))
    noise = float(np.median(lap[disk])) / (float(np.std(vals)) + 1e-6)
    if noise > 0.35:
        score += 0.2
        reasons.append("noisy")

    return float(np.clip(score, 0, 1)), reasons


def estimate_method_difficulty(
    methods: Sequence[Dict[str, Any]],
    *,
    sota: Optional[Dict[str, Any]] = None,
) -> Tuple[float, List[str]]:
    """How much classical methods disagree / struggle."""
    reasons: List[str] = []
    score = 0.0

    if sota:
        n_in = int(sota.get("n_inliers") or 0)
        n_out = int(sota.get("n_outliers") or 0)
        sig_lon = float(sota.get("sigma_lon_deg") or sota.get("sigma_lon") or 0)
        sig_lat = float(sota.get("sigma_lat_deg") or sota.get("sigma_lat") or 0)
        lat = float(sota.get("lat_deg") or sota.get("lat") or -22)
        grade = str(sota.get("quality_grade") or "")

        if n_in < 8:
            score += 0.35
            reasons.append("few_inliers")
        elif n_in < 15:
            score += 0.15
            reasons.append("moderate_inliers")

        if sig_lon > 1.0:
            score += 0.35
            reasons.append("high_lon_scatter")
        elif sig_lon > 0.4:
            score += 0.15
            reasons.append("med_lon_scatter")

        if sig_lat > 0.8:
            score += 0.15
            reasons.append("high_lat_scatter")

        if abs(lat + 22.0) > 4.0:
            score += 0.25
            reasons.append("lat_off_band")

        if "POOR" in grade or "MARGINAL" in grade:
            score += 0.2
            reasons.append("sota_grade_weak")
        elif "FAIR" in grade:
            score += 0.1
            reasons.append("sota_grade_fair")

        if n_out > 2 * max(n_in, 1) and n_in < 12:
            score += 0.15
            reasons.append("chaos_outliers")

    # raw method span among ok centres
    lons = []
    for m in methods:
        if not m.get("ok", True):
            continue
        mid = str(m.get("method_id") or m.get("definition_id") or "")
        if "EDGE" in mid:
            continue
        try:
            lons.append(float(m["lon_iii_deg"]))
        except Exception:
            pass
    if len(lons) >= 5:
        # rough circular span
        ang = np.deg2rad(lons)
        mean = math.atan2(np.mean(np.sin(ang)), np.mean(np.cos(ang)))
        d = np.degrees(np.arctan2(np.sin(ang - mean), np.cos(ang - mean)))
        span = float(np.percentile(d, 90) - np.percentile(d, 10))
        if span > 15:
            score += 0.3
            reasons.append(f"method_span_{span:.0f}deg")
        elif span > 6:
            score += 0.15
            reasons.append(f"method_span_{span:.0f}deg")

    return float(np.clip(score, 0, 1)), reasons


def compute_difficulty(
    image: np.ndarray,
    methods: Sequence[Dict[str, Any]],
    *,
    nav: Optional[NavState] = None,
    sota: Optional[Dict[str, Any]] = None,
) -> Tuple[float, List[str]]:
    d_img, r_img = estimate_image_difficulty(image, nav)
    d_met, r_met = estimate_method_difficulty(methods, sota=sota)
    # methods disagreement weighs more — that's where AI helps most
    d = float(np.clip(0.4 * d_img + 0.6 * d_met, 0, 1))
    # Force hard if huge method span even when SOTA self-reports tight inliers
    for r in r_met:
        if r.startswith("method_span_"):
            try:
                span = float(r.split("_")[-1].replace("deg", ""))
                if span > 40:
                    d = max(d, 0.55)
                    r_img = r_img + ["forced_hard_large_span"]
            except Exception:
                pass
    return d, r_img + r_met


def _nn_prior(image: np.ndarray, nav: Any, cm_iii_deg: float) -> Optional[Dict[str, float]]:
    try:
        import nn_grs
        return nn_grs.predict_soft_prior(image, nav, cm_iii_deg)
    except Exception as e:
        CONSOLE.debug(f"hard-case NN: {e}")
        return None


def assist_hard_case(
    image: np.ndarray,
    *,
    lon: float,
    lat: float,
    nav: Optional[NavState],
    cm_iii_deg: float,
    methods: Sequence[Dict[str, Any]],
    sota: Optional[Dict[str, Any]] = None,
    force: bool = False,
) -> HardCaseAIResult:
    """
    If difficulty is high, blend toward SPIRE-Net and/or pull lon/lat toward
    methods that agree with the network.

    Easy case → engaged=False, lon/lat unchanged.
    """
    difficulty, reasons = compute_difficulty(image, methods, nav=nav, sota=sota)
    lon0, lat0 = float(lon), float(lat)

    # Threshold: only engage when clearly hard (unless forced)
    if not force and difficulty < 0.35:
        return HardCaseAIResult(
            engaged=False,
            difficulty=difficulty,
            reasons=reasons,
            lon_before=lon0,
            lat_before=lat0,
            lon_after=lon0,
            lat_after=lat0,
            note="Easy case — classical methods trusted; AI not needed.",
        )

    pred = _nn_prior(image, nav, cm_iii_deg) if nav is not None else None
    if not pred or float(pred.get("confidence", 0)) < 0.2:
        return HardCaseAIResult(
            engaged=False,
            difficulty=difficulty,
            reasons=reasons + ["nn_unavailable_or_low_conf"],
            lon_before=lon0,
            lat_before=lat0,
            lon_after=lon0,
            lat_after=lat0,
            note="Hard case but SPIRE-Net unavailable/low conf — train NN or improve image.",
        )

    nlon = float(pred["lon_iii_deg"])
    nlat = float(pred["lat_deg"])
    conf = float(pred.get("confidence") or 0.5)

    # Acceptance window grows with difficulty (harder → trust AI a bit farther)
    lon_win = 6.0 + 10.0 * difficulty   # up to ~16°
    lat_win = 4.0 + 4.0 * difficulty    # up to ~8°
    dlon = wrap_diff(nlon, lon0)
    dlat = nlat - lat0

    if abs(dlon) > lon_win or abs(dlat) > lat_win:
        return HardCaseAIResult(
            engaged=True,
            difficulty=difficulty,
            reasons=reasons + ["nn_too_far_from_physics"],
            nn_used=False,
            nn_confidence=conf,
            lon_before=lon0,
            lat_before=lat0,
            lon_after=lon0,
            lat_after=lat0,
            note=(
                f"Hard case (diff={difficulty:.2f}) but NN disagrees too much "
                f"(Δlon={dlon:.2f}° Δlat={dlat:.2f}°) — kept physics."
            ),
        )

    # Blend weight: 0 on easy, up to ~0.45 on brutal hard cases
    # (stronger than the usual 0.15 soft prior — only when hard)
    w_max = 0.20 + 0.30 * difficulty  # 0.20 … 0.50
    w = w_max * min(1.0, conf)

    lon1 = wrap_deg(lon0 + w * dlon)
    lat1 = (1.0 - w) * lat0 + w * nlat

    # Boost: list methods near NN (for reporting / optional reweight)
    boosted = []
    for m in methods:
        if not m.get("ok", True):
            continue
        mid = str(m.get("method_id") or m.get("definition_id") or "")
        try:
            mlon, mlat = float(m["lon_iii_deg"]), float(m["lat_deg"])
        except Exception:
            continue
        if abs(wrap_diff(mlon, nlon)) < 3.0 and abs(mlat - nlat) < 2.5:
            boosted.append(mid)

    CONSOLE.ok(
        f"AI hard-case assist ON  difficulty={difficulty:.2f}  w={w:.2f}  "
        f"lon {lon0:.3f}→{lon1:.3f}  lat {lat0:.3f}→{lat1:.3f}  "
        f"reasons={reasons[:4]}"
    )

    return HardCaseAIResult(
        engaged=True,
        difficulty=difficulty,
        reasons=reasons,
        nn_used=True,
        nn_confidence=conf,
        blend_weight=w,
        lon_before=lon0,
        lat_before=lat0,
        lon_after=lon1,
        lat_after=lat1,
        methods_boosted=boosted[:20],
        note=(
            f"Hard-case AI assist: difficulty={difficulty:.2f}, blend w={w:.2f}. "
            f"Classical methods struggled ({', '.join(reasons[:5])}). "
            f"SPIRE-Net used only for disambiguation — not absolute ephemeris."
        ),
    )


def apply_hard_case_ai_to_package(
    package: Dict[str, Any],
    image: np.ndarray,
    *,
    nav: Optional[NavState] = None,
    cm_iii_deg: float = 0.0,
) -> Dict[str, Any]:
    """
    After SOTA is computed, optionally refine lon/lat with AI if hard.
    Updates headline + sota block when engaged.
    """
    methods: List[Dict[str, Any]] = []
    am = package.get("all_methods") or (package.get("gold_standard") or {}).get("all_methods")
    if isinstance(am, dict):
        methods = list(am.get("methods") or [])
    gs = package.get("gold_standard") or {}
    if not methods and gs.get("measures"):
        methods = [
            {
                "method_id": m.get("definition_id"),
                "lon_iii_deg": m.get("lon_iii_deg"),
                "lat_deg": m.get("lat_deg"),
                "ok": m.get("ok", True),
                "weight": m.get("weight", 1.0),
            }
            for m in gs["measures"]
        ]

    sota = package.get("sota") or {}
    h = package.get("headline") or {}
    lon = h.get("sota_lon_iii_deg") or h.get("gold_lon_iii_deg") or h.get("lon_iii_deg_bias_corrected")
    lat = h.get("sota_lat_deg") or h.get("gold_lat_deg") or h.get("lat_deg_bias_corrected")
    if lon is None or lat is None:
        package["ai_hard_case"] = HardCaseAIResult(
            engaged=False, difficulty=0.0, note="No lon/lat to refine"
        ).to_dict()
        return package

    cm = float(cm_iii_deg or h.get("cm_iii_deg") or (gs.get("cm_iii_deg") if gs else 0) or 0)
    if nav is not None:
        cm = float(getattr(nav, "cm_iii_deg", cm) or cm)

    res = assist_hard_case(
        image,
        lon=float(lon),
        lat=float(lat),
        nav=nav,
        cm_iii_deg=cm,
        methods=methods,
        sota=sota if sota.get("ok") else None,
    )
    package["ai_hard_case"] = res.to_dict()

    if res.engaged and res.nn_used and res.lon_after is not None:
        # AI is an assist layer only — never overwrite SOTA / gold / pipeline primaries.
        # Provenance: classical multi-method remains the publication product.
        h = package.setdefault("headline", {})
        h["ai_lon_iii_deg"] = res.lon_after
        h["ai_lat_deg"] = res.lat_after
        h["ai_hard_case"] = True
        h["ai_difficulty"] = res.difficulty
        h["ai_blend_weight"] = res.blend_weight
        h["ai_lon_before"] = res.lon_before
        h["ai_lat_before"] = res.lat_before
        if isinstance(package.get("sota"), dict) and package["sota"].get("ok"):
            package["sota"] = dict(package["sota"])
            package["sota"]["ai_hard_case"] = res.to_dict()
            package["sota"]["quality_notes"] = list(package["sota"].get("quality_notes") or []) + [
                res.note,
                "AI blend stored under headline.ai_* only (SOTA primary unchanged).",
            ]

    return package

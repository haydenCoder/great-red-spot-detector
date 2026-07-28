#!/usr/bin/env python3
"""
Accuracy gates for GRS System III metrology
==========================================

Implements professional practice for careful planetary measurement,
BAA Jupiter Section timing notes, and SPICE/System III ephemeris discipline:

  • Absolute lon needs trusted CM (SPICE / Horizons / override)
  • Mid-exposure timing: ~1 min ≈ 0.6° System III (BAA)
  • Limb outline quality dominates lat/lon error
  • GRS lives in a SEB latitude band — reject polar / EZ locks
  • Publish one definition (GS-MAP / GS-BARY); soup is scatter only
  • Reject wrong-feature candidates far from the GRS-band consensus

Pure helpers — unit-testable without Tk.
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from precision_engine import wrap_diff, sky_error_arcsec

# ── CM / ephemeris (absolute System III) ────────────────────────────────────
TRUSTED_CM_SOURCES = frozenset({
    "spice",
    "spice_auto",
    "spice_legacy",
    "horizons",
    "horizons_sublon",
    "winjupos",
    "override",
    "user",
    "cm_override",
    # Synthetic self-test: CM is image-tied truth (not analytical guess)
    "synthetic_truth",
    "synthetic",
})

# Analytical / unknown — OK for relative work, weak for absolute publish
WEAK_CM_SOURCES = frozenset({
    "analytical",
    "analytic",
    "fallback",
    "unknown",
    "spice_auto_distance_only",
    "spice_distance_only",
    "",
})

# ── GRS feature band (planetographic-ish lat, deg) ──────────────────────────
# Tight band: typical GRS core (SEB)
GRS_LAT_CORE_MIN = -28.0
GRS_LAT_CORE_MAX = -16.0
# Wide band: still plausible for GRS-family dark ovals (reject poles/EZ)
GRS_LAT_WIDE_MIN = -36.0
GRS_LAT_WIDE_MAX = -10.0

# Lon cluster: methods beyond this vs robust median are wrong-feature
METHOD_LON_CLUSTER_DEG = 18.0
# Publish candidate vs pipeline (already used in publish_primary)
PUBLISH_VS_PIPE_MAX_DEG = 30.0

# Limb outline sky spread (arcsec) — limb is the dominant error source.
# Automated twin limb probes are noisier than careful human outline → warn first.
LIMB_SPREAD_WARN_ARCSEC = 2.5
LIMB_SPREAD_FAIL_ARCSEC = 10.0  # catastrophic nav only

# Definition scatter among GS-* centres (deg lon)
DEF_SPREAD_WARN_DEG = 12.0
DEF_SPREAD_FAIL_DEG = 40.0  # hard fail only for catastrophic multi-definition chaos

# Timing: Sys III ≈ 870.5°/day → ~0.604 °/min
SYS3_DEG_PER_MINUTE = 870.536 / (24.0 * 60.0)


def is_trusted_cm_source(cm_source: Optional[str]) -> bool:
    s = (cm_source or "").strip().lower()
    if not s:
        return False
    if s in WEAK_CM_SOURCES:
        return False
    for tok in TRUSTED_CM_SOURCES:
        if tok in s:
            return True
    return False


def grs_lat_in_core_band(lat_deg: Optional[float]) -> bool:
    if lat_deg is None or not math.isfinite(float(lat_deg)):
        return False
    return GRS_LAT_CORE_MIN <= float(lat_deg) <= GRS_LAT_CORE_MAX


def grs_lat_in_wide_band(lat_deg: Optional[float]) -> bool:
    if lat_deg is None or not math.isfinite(float(lat_deg)):
        return False
    return GRS_LAT_WIDE_MIN <= float(lat_deg) <= GRS_LAT_WIDE_MAX


def timing_longitude_uncertainty_deg(time_error_seconds: float) -> float:
    """BAA-style: mid-exposure error maps to System III lon uncertainty."""
    return abs(float(time_error_seconds)) / 60.0 * SYS3_DEG_PER_MINUTE


def method_passes_grs_band(
    lat_deg: Optional[float],
    *,
    strict: bool = False,
) -> bool:
    return grs_lat_in_core_band(lat_deg) if strict else grs_lat_in_wide_band(lat_deg)


def filter_methods_grs_band(
    methods: Dict[str, Dict[str, Any]],
    *,
    strict: bool = False,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, str]]:
    """
    Keep only methods whose latitude is in the GRS band.
    Returns (kept, rejected_reasons).
    """
    kept: Dict[str, Dict[str, Any]] = {}
    rejected: Dict[str, str] = {}
    for name, m in methods.items():
        if not isinstance(m, dict):
            rejected[name] = "not_a_dict"
            continue
        if m.get("rejected"):
            rejected[name] = str(m.get("reject_reason") or "already_rejected")
            continue
        lat = m.get("lat_deg")
        try:
            lat_f = float(lat) if lat is not None else None
        except (TypeError, ValueError):
            lat_f = None
        if not method_passes_grs_band(lat_f, strict=strict):
            rejected[name] = f"lat_out_of_grs_band lat={lat}"
            continue
        kept[name] = m
    return kept, rejected


def robust_circular_median_lon(lons: Sequence[float]) -> Optional[float]:
    """
    Robust lon centre: pick the point with the most neighbours within
    METHOD_LON_CLUSTER_DEG (mode of densest cluster), not the mean of
    all points (which is pulled by wrong-feature outliers).
    """
    xs = [float(x) % 360.0 for x in lons if x is not None and math.isfinite(float(x))]
    if not xs:
        return None
    if len(xs) == 1:
        return xs[0]
    best_i = 0
    best_n = -1
    for i, xi in enumerate(xs):
        n = sum(1 for xj in xs if abs(wrap_diff(xj, xi)) <= METHOD_LON_CLUSTER_DEG)
        if n > best_n:
            best_n = n
            best_i = i
    # circular mean of the densest neighbourhood only
    core = [x for x in xs if abs(wrap_diff(x, xs[best_i])) <= METHOD_LON_CLUSTER_DEG]
    cx = sum(math.cos(math.radians(x)) for x in core) / len(core)
    sx = sum(math.sin(math.radians(x)) for x in core) / len(core)
    return float((math.degrees(math.atan2(sx, cx)) + 360.0) % 360.0)


def reject_lon_outliers(
    methods: Dict[str, Dict[str, Any]],
    *,
    max_delta_deg: float = METHOD_LON_CLUSTER_DEG,
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, str], Optional[float]]:
    """
    Outlier removal: drop methods far from densest lon cluster.
    """
    lons = []
    names_ok = []
    for name, m in methods.items():
        try:
            lons.append(float(m["lon_iii_deg"]))
            names_ok.append(name)
        except Exception:
            pass
    med = robust_circular_median_lon(lons)
    if med is None:
        return methods, {}, None
    kept: Dict[str, Dict[str, Any]] = {}
    rejected: Dict[str, str] = {}
    for name, m in methods.items():
        try:
            lon = float(m["lon_iii_deg"])
        except Exception:
            rejected[name] = "missing_lon"
            continue
        d = abs(wrap_diff(lon, med))
        if d > max_delta_deg:
            rejected[name] = f"lon_cluster_outlier dlon={d:.2f} vs med={med:.2f}"
            continue
        kept[name] = m
    # Never empty the set if we had methods — keep densest single method
    if not kept and methods:
        # keep the method closest to med
        best_n, best_d = None, 1e9
        for name, m in methods.items():
            try:
                d = abs(wrap_diff(float(m["lon_iii_deg"]), med))
            except Exception:
                continue
            if d < best_d:
                best_d = d
                best_n = name
        if best_n is not None:
            kept[best_n] = methods[best_n]
            rejected.pop(best_n, None)
    return kept, rejected, med


def assess_publish_quality(package: Dict[str, Any]) -> Dict[str, Any]:
    """
    Post-publish quality assessment for the official answer.

    Mutates nothing; returns a quality block to attach under package['publish_quality'].
    """
    h = package.get("headline") or {}
    pub = package.get("publish") or {}
    twin = package.get("winjupos_twin") or {}
    flags: List[str] = []
    warnings: List[str] = []

    pub_lon = _f(pub.get("publish_lon_iii_deg") or h.get("publish_lon_iii_deg") or h.get("lon_iii_deg"))
    pub_lat = _f(pub.get("publish_lat_deg") or h.get("publish_lat_deg") or h.get("lat_deg"))
    pipe_lon = _f(pub.get("pipeline_lon_iii_deg") or h.get("pipeline_lon_iii_deg"))
    cm_source = str(pub.get("cm_source") or h.get("cm_source") or "")
    cm_trusted = is_trusted_cm_source(cm_source)

    limb = _f(
        pub.get("limb_outline_sky_spread_arcsec")
        or twin.get("limb_sky_spread_arcsec")
        or h.get("limb_outline_sky_spread_arcsec")
    )
    def_spread = _f(
        pub.get("definition_lon_spread_deg")
        or twin.get("definition_lon_spread_deg")
        or h.get("definition_lon_spread_deg")
    )
    time_err = _f(h.get("time_error_seconds") or package.get("time_error_seconds") or 0.0) or 0.0
    timing_lon_unc = timing_longitude_uncertainty_deg(time_err)

    lat_core_ok = grs_lat_in_core_band(pub_lat)
    lat_wide_ok = grs_lat_in_wide_band(pub_lat)

    if not cm_trusted:
        flags.append("CM_UNTRUSTED")
        warnings.append(
            f"cm_source={cm_source!r} is not SPICE/Horizons/override — "
            "absolute System III may be offset (use SPICE)."
        )
    if not lat_wide_ok:
        flags.append("LAT_OUT_OF_BAND")
        warnings.append(f"publish lat={pub_lat} outside GRS wide band [{GRS_LAT_WIDE_MIN},{GRS_LAT_WIDE_MAX}]")
    elif not lat_core_ok:
        flags.append("LAT_OUTSIDE_CORE")
        warnings.append(f"publish lat={pub_lat} outside core GRS band [{GRS_LAT_CORE_MIN},{GRS_LAT_CORE_MAX}]")

    if limb is not None:
        if limb > LIMB_SPREAD_FAIL_ARCSEC:
            flags.append("LIMB_SPREAD_FAIL")
            warnings.append(f"limb sky spread {limb:.2f}\" > {LIMB_SPREAD_FAIL_ARCSEC}\" (JUPOS: limb is critical)")
        elif limb > LIMB_SPREAD_WARN_ARCSEC:
            flags.append("LIMB_SPREAD_WARN")
            warnings.append(f"limb sky spread {limb:.2f}\" elevated")

    if def_spread is not None:
        if def_spread > DEF_SPREAD_FAIL_DEG:
            flags.append("DEFINITION_SCATTER_FAIL")
            warnings.append(f"definition lon spread {def_spread:.1f}° > {DEF_SPREAD_FAIL_DEG}°")
        elif def_spread > DEF_SPREAD_WARN_DEG:
            flags.append("DEFINITION_SCATTER_WARN")
            warnings.append(f"definition lon spread {def_spread:.1f}° elevated")

    if pub_lon is not None and pipe_lon is not None:
        dpp = abs(wrap_diff(pub_lon, pipe_lon))
        if dpp > PUBLISH_VS_PIPE_MAX_DEG:
            flags.append("PUBLISH_VS_PIPE_FAIL")
            warnings.append(f"|publish−pipeline| lon={dpp:.1f}° > {PUBLISH_VS_PIPE_MAX_DEG}°")
    else:
        dpp = None

    if timing_lon_unc > 0.6:
        flags.append("TIMING_UNCERTAINTY_HIGH")
        warnings.append(
            f"time_error={time_err:.0f}s → ~{timing_lon_unc:.2f}° Sys III "
            f"(BAA: 1 min ≈ 0.6°). Prefer ≤2–3 min stacks, mid-exposure UTC."
        )

    # Colour / orange GRS seed: when publish is the orange oval with core lat,
    # method-soup scatter is expected (belts vs oval) and must not hard-reject.
    pub_def = str(pub.get("publish_definition") or h.get("publish_definition") or "")
    og = package.get("orange_grs") or {}
    orange_seed_ok = bool(
        og.get("ok")
        and not og.get("near_limb")
        and lat_core_ok
        and (
            "ORANGE" in pub_def.upper()
            or (
                pub_lon is not None
                and og.get("lon_iii_deg") is not None
                and abs(wrap_diff(float(pub_lon), float(og["lon_iii_deg"]))) < 3.0
            )
        )
    )
    if orange_seed_ok:
        # Soup can span 180° while the oval is correct — demote scatter to warn only
        flags = [f for f in flags if f not in ("DEFINITION_SCATTER_FAIL", "PUBLISH_VS_PIPE_FAIL")]
        if def_spread is not None and def_spread > DEF_SPREAD_WARN_DEG:
            if "DEFINITION_SCATTER_WARN" not in flags:
                flags.append("DEFINITION_SCATTER_WARN")
            warnings.append(
                "Method soup scatter ignored for quality: GS-ORANGE colour seed locked GRS oval."
            )

    # Hard reject when feature ID is broken (wrong lat / chaos).
    hard_fail = any(
        f in flags
        for f in (
            "LAT_OUT_OF_BAND",
            "PUBLISH_VS_PIPE_FAIL",
            "DEFINITION_SCATTER_FAIL",
        )
    )
    soft_fail = any(
        f in flags
        for f in (
            "LIMB_SPREAD_FAIL",
            "LAT_OUTSIDE_CORE",
        )
    )
    # Absolute publish needs trusted CM + core lat
    absolute_ok = (
        cm_trusted
        and not hard_fail
        and lat_core_ok
        and lat_wide_ok
        and (orange_seed_ok or not soft_fail)
    )
    # Orange seed with core lat is publishable (still validate with manual picks)
    publish_ok = (not hard_fail and lat_core_ok) or orange_seed_ok

    if hard_fail or not lat_wide_ok:
        grade = "REJECT"
    elif orange_seed_ok and lat_core_ok and cm_trusted:
        grade = "GOOD" if not soft_fail else "CAUTION"
    elif soft_fail or not cm_trusted or "LAT_OUTSIDE_CORE" in flags or "LIMB_SPREAD_WARN" in flags:
        grade = "CAUTION"
    elif flags:
        grade = "FAIR"
    else:
        grade = "GOOD"

    notes = [
        "Sources: professional practice (limb/outline, timing, outlier reject); "
        "BAA Jupiter Section (transit timing ~0.6°/min); SPICE/System III absolute CM.",
        "Publish definition should be GS-MAP/GS-BARY core — not method soup.",
        "Prefer red/IR channel for GRS contrast.",
    ]

    return {
        "publish_ok": publish_ok,
        "absolute_ok": absolute_ok,
        "grade": grade,
        "flags": flags,
        "warnings": warnings,
        "notes": notes,
        "cm_source": cm_source,
        "cm_trusted": cm_trusted,
        "publish_lon_iii_deg": pub_lon,
        "publish_lat_deg": pub_lat,
        "lat_core_ok": lat_core_ok,
        "lat_wide_ok": lat_wide_ok,
        "limb_sky_spread_arcsec": limb,
        "definition_lon_spread_deg": def_spread,
        "publish_vs_pipeline_lon_deg": dpp,
        "time_error_seconds": time_err,
        "timing_lon_uncertainty_deg": timing_lon_unc,
        "grs_lat_core_band_deg": [GRS_LAT_CORE_MIN, GRS_LAT_CORE_MAX],
        "grs_lat_wide_band_deg": [GRS_LAT_WIDE_MIN, GRS_LAT_WIDE_MAX],
    }


def prefer_red_channel(image) -> Any:
    """
    Visual/red preferred for GRS; blue often weaker for belts.
    Accepts mono HxW, HWC RGB, or CHW RGB.
    """
    import numpy as np

    a = np.asarray(image)
    if a.ndim == 2:
        return a
    if a.ndim == 3 and a.shape[0] in (3, 4) and a.shape[0] < a.shape[-1]:
        return a[0]  # CHW → R
    if a.ndim == 3 and a.shape[-1] >= 3:
        return a[..., 0]  # HWC → R
    return a


def _f(x) -> Optional[float]:
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except Exception:
        return None

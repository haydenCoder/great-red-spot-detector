#!/usr/bin/env python3
"""
State-of-the-art accuracy layer for ground-based GRS metrology (laptop).

IMPORTANT: this is SCATTER DIAGNOSTICS only — it does NOT produce the
published GRS centre. The published centre comes from GS-MAP / Champion
through the publish hierarchy. SOTA is just a sanity check showing how
much the ~80 different estimators disagree with each other. I know it's
tempting to use the SOTA consensus as "the answer" when it looks tight,
but it's correlated estimators sharing the same mask and priors — the
tight consensus might be a systematic bias, not accuracy.

Does NOT invent NASA truth. Implements best-practice *procedure* used worldwide:

  1) Run every estimator (all_methods)
  2) Reliability priors by method family (empirical pro practice)
  3) MAD / IQR outlier rejection (reject methods that left the GRS)
  4) Robust circular consensus for lon + robust lat
  5) Quality gates (lat band, scatter, inlier count, limb-ish flags)
  6) Optional WinJUPOS manual Δ as external validation
  7) FITS DATE-OBS / mid-time extraction for absolute System III

References (practice, not code):
  JUPOS/WinJUPOS multi-measure discipline; multi-method scatter as systematic;
  robust statistics (MAD) standard in metrology; Asay-Davis-style correlation
  methods downweighted when inconsistent with core cluster.
"""
from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from verbose_log import CONSOLE
from precision_engine import wrap_deg, wrap_diff, sky_error_arcsec, to_mono, NavState


# ---------------------------------------------------------------------------
# Reliability priors — methods that historically track dark *core* well
# get higher base weight; edge / global / phase methods lower unless inlier
# ---------------------------------------------------------------------------

FAMILY_PRIOR = {
    "ensemble": 1.15,
    "map": 1.10,
    "threshold": 1.05,
    "profile": 1.00,
    "isophote": 1.00,
    "robust": 1.10,
    "template": 0.95,
    "morph": 0.95,
    "image": 0.90,
    "spectral": 0.95,
    "extent": 0.85,
    "edge": 0.40,       # not centres — usually excluded from primary
    "extra": 0.90,
    "?": 0.70,
}

# Explicit per-method priors (multiplicative). High = trust for centre.
METHOD_PRIOR = {
    "ENS_MEDIAN": 1.4,
    "ENS_WMEAN": 1.3,
    "ENS_TRIM": 1.25,
    "ENS_MEDOID": 1.2,
    "MAP_DARK": 1.25,
    "QUAD_MOM": 1.2,
    "HU_MOM": 1.15,
    "FLUX_P1": 1.15,
    "FLUX_P2": 1.15,
    "GEOM_MED": 1.2,
    "MEAN_SHIFT": 1.15,
    "KDE_MODE": 1.1,
    "P_LADDER": 1.15,
    "MULTI_ISO": 1.15,
    "OTSU": 1.1,
    "PERC12": 1.15,
    "PERC18": 1.1,
    "CIV_WIN": 1.05,
    "MS_NCC": 1.0,
    "ENGINE": 1.05,
    "GS-MAP": 1.25,
    "GS-BARY": 1.2,
    # often outlier-prone on single frames
    "SPOMF": 0.35,
    "PHASE_CORR": 0.40,
    "RING_TMPL": 0.45,
    "PROJ_1D": 0.50,
    "FWHM_LON": 0.55,
    "MIN_PIX": 0.30,
    "MEC": 0.50,
    "RAD_SYM": 0.55,
    "STRUCT_T": 0.50,
    "SOBEL_RING": 0.45,
    "EDGE_W": 0.15,
    "EDGE_E": 0.15,
    "EDGE_N": 0.15,
    "EDGE_S": 0.15,
}

EDGE_IDS = frozenset({
    "EDGE_W", "EDGE_E", "EDGE_N", "EDGE_S", "GS-EDGE-W", "GS-EDGE-E",
    "BOX_LEN", "MIN_PIX",
})


@dataclass
class SOTAResult:
    ok: bool
    lon_iii_deg: float
    lat_deg: float
    sigma_lon_deg: float
    sigma_lat_deg: float
    sigma_sky_arcsec_approx: float
    n_methods_total: int
    n_methods_ok: int
    n_inliers: int
    n_outliers: int
    inlier_ids: List[str] = field(default_factory=list)
    outlier_ids: List[str] = field(default_factory=list)
    primary_label: str = "SOTA_ROBUST"
    quality_grade: str = "—"
    quality_score: float = 0.0
    quality_flags: List[str] = field(default_factory=list)
    quality_notes: List[str] = field(default_factory=list)
    method_weights: Dict[str, float] = field(default_factory=dict)
    consensus_detail: Dict[str, Any] = field(default_factory=dict)
    fits_time: Optional[str] = None
    recommendations: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _mad(x: np.ndarray) -> float:
    x = np.asarray(x, dtype=np.float64)
    if x.size == 0:
        return 0.0
    med = np.median(x)
    return float(np.median(np.abs(x - med))) * 1.4826  # → σ-equivalent


def _circular_median(lons: Sequence[float]) -> float:
    if not lons:
        return float("nan")
    # geometric median on circle via mean of unit vectors as start + refine
    ang = np.deg2rad(np.asarray(lons, dtype=np.float64))
    # use coordinate-wise median of unwrapped deltas about circular mean
    cmean = math.atan2(np.mean(np.sin(ang)), np.mean(np.cos(ang)))
    d = np.arctan2(np.sin(ang - cmean), np.cos(ang - cmean))
    return wrap_deg(math.degrees(cmean + np.median(d)))


def _circular_weighted_mean(lons: Sequence[float], wts: Sequence[float]) -> float:
    ang = np.deg2rad(np.asarray(lons, dtype=np.float64))
    w = np.asarray(wts, dtype=np.float64)
    w = w / (w.sum() + 1e-12)
    return wrap_deg(math.degrees(math.atan2(np.sum(w * np.sin(ang)), np.sum(w * np.cos(ang)))))


# Centres sometimes mis-tagged as family=edge in all_methods exporters
_CENTRE_DESPITE_EDGE_FAMILY = frozenset({
    "ISOPHOTE", "OVAL", "MID", "MORPH", "MEAN_SHIFT", "BARY_IMG",
})


def is_centre_method(method_id: str, family: str = "") -> bool:
    mid = str(method_id)
    if mid in EDGE_IDS or mid.startswith("EDGE_"):
        return False
    if family == "edge" and mid not in _CENTRE_DESPITE_EDGE_FAMILY:
        return False
    return True


def base_weight(method_id: str, family: str, declared_weight: float = 1.0) -> float:
    w = float(declared_weight or 1.0)
    w *= FAMILY_PRIOR.get(family, 0.9)
    w *= METHOD_PRIOR.get(method_id, 1.0)
    return max(w, 1e-6)


def is_map_edge_lock(lon: float, cm_iii_deg: Optional[float], margin_deg: float = 2.5) -> bool:
    """
    Cylindrical maps only cover CM±90°. *Exact* hits on the map boundary columns
    (lon_rel ≈ ±90°) are classic false peaks when a method slides to the edge.

    Margin must stay tight (~2–3°). A wide margin (e.g. 10°) falsely kills real
    GRS detections when the spot is legitimately near the limb (lon_rel ~80–88°).
    """
    if cm_iii_deg is None or not math.isfinite(float(cm_iii_deg)):
        return False
    rel = abs(wrap_diff(float(lon), float(cm_iii_deg)))  # 0..180
    return abs(rel - 90.0) < margin_deg


def _near_pipeline(lon: float, pipeline_lon: Optional[float], tol_deg: float = 20.0) -> bool:
    if pipeline_lon is None or not math.isfinite(float(pipeline_lon)):
        return False
    return abs(wrap_diff(float(lon), float(pipeline_lon))) < tol_deg


def _cluster_centres(
    centres: List[Dict[str, Any]],
    *,
    radius_deg: float = 12.0,
) -> List[List[Dict[str, Any]]]:
    """Greedy circular clustering by lon (lat used only for seed quality later)."""
    unused = list(centres)
    clusters: List[List[Dict[str, Any]]] = []
    while unused:
        # seed = highest weight remaining
        unused.sort(key=lambda c: -c["w0"])
        seed = unused[0]
        cl = [seed]
        unused = unused[1:]
        changed = True
        while changed:
            changed = False
            lon_c = _circular_median([c["lon"] for c in cl])
            still = []
            for c in unused:
                if abs(wrap_diff(c["lon"], lon_c)) <= radius_deg and abs(c["lat"] - seed["lat"]) < 8.0:
                    cl.append(c)
                    changed = True
                else:
                    still.append(c)
            unused = still
        clusters.append(cl)
    return clusters


def _score_cluster(
    cl: List[Dict[str, Any]],
    *,
    cm_iii_deg: Optional[float],
    pipeline_lon: Optional[float],
    lat0: float = -22.0,
) -> float:
    """Higher = better cluster to trust as GRS centre."""
    if not cl:
        return -1e9
    lon_c = _circular_median([c["lon"] for c in cl])
    lat_c = float(np.median([c["lat"] for c in cl]))
    n = len(cl)
    wsum = sum(c["w0"] for c in cl)
    lat_pen = abs(lat_c - lat0)
    # Density alone must NOT beat an independent VLBI/pipeline seed (AS_P5 failure mode:
    # 40 threshold methods pile on CM while real GRS sits near limb).
    score = 2.0 * n + 1.0 * wsum - 2.5 * lat_pen

    # Exact map-boundary column clusters (not "near limb" science)
    if is_map_edge_lock(lon_c, cm_iii_deg, margin_deg=2.5):
        # Only punish if this is NOT the pipeline neighborhood (limb GRS is valid)
        if not _near_pipeline(lon_c, pipeline_lon, tol_deg=18.0):
            score -= 60.0

    # Pipeline seed breaks CM-lock densest-wrong-mode, but must not dominate score
    # so much that SOTA is a copy of the pipeline (circular confidence).
    if pipeline_lon is not None and math.isfinite(float(pipeline_lon)):
        d = abs(wrap_diff(lon_c, float(pipeline_lon)))
        if d < 12.0:
            score += 35.0 - 1.5 * d  # modest prior (was +140 — hijacked primary)
        elif d < 25.0:
            score += 12.0 - 0.6 * (d - 12.0)
        else:
            # Far from pipeline while pipeline exists → dense wrong mode
            score -= 40.0 + 0.5 * min(d, 90.0)

    # Bonus if cluster contains strong physics methods
    strong = {
        "ENGINE", "MS_NCC", "MAP_DARK", "BARY_IMG", "ENS_MEDIAN", "QUAD_MOM",
        "MULTI_ISO", "PIPELINE_SEED", "OVAL", "MID", "MORPH", "ISOPHOTE",
    }
    ids = {c["id"] for c in cl}
    score += 4.0 * len(ids & strong)

    # Fake CM peak: many methods lock on central meridian dark band while pipeline
    # (and often true GRS) is elsewhere — classic AS_P5 / near-limb failure.
    if cm_iii_deg is not None and abs(wrap_diff(lon_c, float(cm_iii_deg))) < 8.0:
        if pipeline_lon is not None and abs(wrap_diff(lon_c, float(pipeline_lon))) > 25.0:
            score -= 100.0
        else:
            score -= 4.0  # slight caution even when GRS is truly on CM

    return float(score)


def robust_consensus(
    methods: Sequence[Dict[str, Any]],
    *,
    mad_k: float = 2.8,
    min_inliers: int = 5,
    lat0: float = -22.0,
    lat_half: float = 8.0,
    cm_iii_deg: Optional[float] = None,
    pipeline_lon: Optional[float] = None,
    pipeline_lat: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Multi-cluster + MAD consensus on centre methods.

    Critical fixes vs naive median:
      • drop *exact* cylindrical map-edge locks (CM±90° columns only)
      • never drop methods near VLBI/pipeline seed (near-limb GRS is valid)
      • pick best cluster with strong pipeline prior (not densest CM mode)
      • inject PIPELINE_SEED if all near-limb methods were filtered
    """
    centres = []
    edge_out: List[Dict[str, Any]] = []
    for m in methods:
        if not m.get("ok", True):
            continue
        mid = str(m.get("method_id") or m.get("definition_id") or "")
        fam = str(m.get("family") or "")
        lon = m.get("lon_iii_deg")
        lat = m.get("lat_deg")
        if lon is None or lat is None:
            continue
        try:
            lon = float(lon)
            lat = float(lat)
        except Exception:
            continue
        if not (math.isfinite(lon) and math.isfinite(lat)):
            continue
        if not is_centre_method(mid, fam):
            continue
        # Ensembles average the same suite — not independent votes (double-count).
        if mid.startswith("ENS_") or mid in ("P_LADDER",):
            continue
        # Reject exact map-boundary artifacts (CM±90 columns) — but NEVER discard
        # methods that agree with the independent VLBI/pipeline centre (limb GRS).
        near_pipe = _near_pipeline(lon, pipeline_lon, tol_deg=22.0)
        if is_map_edge_lock(lon, cm_iii_deg, margin_deg=2.5) and not near_pipe:
            edge_out.append({
                "id": mid, "family": fam, "lon": lon, "lat": lat,
                "w0": 0.0, "lat_ok": False, "edge_lock": True,
            })
            continue
        centres.append({
            "id": mid,
            "family": fam,
            "lon": lon,
            "lat": lat,
            "w0": base_weight(mid, fam, float(m.get("weight") or 1.0)),
            "lat_ok": abs(lat - lat0) <= lat_half,
            "edge_lock": False,
        })

    # Family diversity: at most 2 methods per family in consensus (correlated flood).
    # Prefer higher base weight within each family.
    if len(centres) > 8:
        by_fam: Dict[str, List[Dict[str, Any]]] = {}
        for c in centres:
            by_fam.setdefault(str(c.get("family") or "?"), []).append(c)
        diverse: List[Dict[str, Any]] = []
        for fam_k, lst in by_fam.items():
            lst.sort(key=lambda x: -float(x.get("w0") or 0))
            diverse.extend(lst[:2])
        # keep at least 6 if possible
        if len(diverse) >= 6:
            centres = diverse

    # If pipeline exists but no method survived near it (all wrongly edge-dropped),
    # inject pipeline as a synthetic high-weight centre so cluster selection can work.
    if pipeline_lon is not None and math.isfinite(float(pipeline_lon)):
        near = [c for c in centres if _near_pipeline(c["lon"], pipeline_lon, tol_deg=20.0)]
        if not near:
            plat = float(pipeline_lat) if pipeline_lat is not None and math.isfinite(float(pipeline_lat)) else lat0
            centres.append({
                "id": "PIPELINE_SEED",
                "family": "map",
                "lon": float(pipeline_lon),
                "lat": plat,
                "w0": 4.0,
                "lat_ok": abs(plat - lat0) <= lat_half + 2,
                "edge_lock": False,
            })

    if len(centres) < 2:
        if centres:
            c = centres[0]
            return {
                "ok": True,
                "lon": c["lon"], "lat": c["lat"],
                # Never claim σ=0 from a single estimator
                "sigma_lon": 1.5, "sigma_lat": 0.8,
                "inliers": [c["id"]], "outliers": [e["id"] for e in edge_out],
                "weights": {c["id"]: c["w0"]},
                "n_inliers": 1,
                "n_outliers": len(edge_out),
                "n_clusters": 1,
                "cluster_note": "single centre after edge filter (σ floored)",
                "edge_locks_dropped": len(edge_out),
            }
        # Last resort: pipeline alone
        if pipeline_lon is not None and math.isfinite(float(pipeline_lon)):
            plat = float(pipeline_lat) if pipeline_lat is not None and math.isfinite(float(pipeline_lat)) else lat0
            return {
                "ok": True,
                "lon": float(pipeline_lon), "lat": plat,
                "sigma_lon": 2.0, "sigma_lat": 1.0,
                "inliers": ["PIPELINE_SEED"], "outliers": [e["id"] for e in edge_out],
                "weights": {"PIPELINE_SEED": 1.0},
                "n_inliers": 1,
                "n_outliers": len(edge_out),
                "n_clusters": 1,
                "cluster_score": 0.0,
                "cluster_note": "fallback to pipeline seed (all map methods edge-locked)",
                "edge_locks_dropped": len(edge_out),
            }
        return {"ok": False, "error": "no centre methods (all edge-locked or empty)"}

    # --- Multi-cluster selection (fixes wrong-mode median when GRS near limb) ---
    clusters = _cluster_centres(centres, radius_deg=14.0)
    scored = [
        (_score_cluster(cl, cm_iii_deg=cm_iii_deg, pipeline_lon=pipeline_lon, lat0=lat0), cl)
        for cl in clusters
    ]
    scored.sort(key=lambda t: -t[0])
    best_score, best_cl = scored[0]

    # Soft rescue: if densest cluster is far from pipeline, prefer a nearby cluster
    # when density is also competitive — do not blindly force pipeline (+200 was hijack).
    if pipeline_lon is not None and math.isfinite(float(pipeline_lon)):
        best_lon = _circular_median([c["lon"] for c in best_cl])
        if abs(wrap_diff(best_lon, float(pipeline_lon))) > 30.0:
            pipe_cands = []
            for sc, cl in scored:
                lon_c = _circular_median([c["lon"] for c in cl])
                d = abs(wrap_diff(lon_c, float(pipeline_lon)))
                if d < 22.0 and len(cl) >= 2:
                    pipe_cands.append((sc + 25.0 - 0.5 * d, cl, lon_c, d))
            if pipe_cands:
                pipe_cands.sort(key=lambda t: -t[0])
                best_score, best_cl = pipe_cands[0][0], pipe_cands[0][1]

    # Seed lon from cluster median (pipeline only soft-seeds if already inside cluster)
    lon0 = _circular_median([c["lon"] for c in best_cl])
    lat0m = float(np.median([c["lat"] for c in best_cl]))
    if pipeline_lon is not None and math.isfinite(float(pipeline_lon)):
        if abs(wrap_diff(float(pipeline_lon), lon0)) < 12.0:
            # light circular blend — consensus still owns the answer
            lon0 = wrap_deg(lon0 + 0.15 * wrap_diff(float(pipeline_lon), lon0))
            if pipeline_lat is not None and math.isfinite(float(pipeline_lat)):
                lat0m = 0.85 * lat0m + 0.15 * float(pipeline_lat)

    inliers = list(best_cl)
    outliers: List[Dict[str, Any]] = list(edge_out)
    # everyone not in best cluster starts as outlier
    best_ids = {c["id"] for c in best_cl}
    for c in centres:
        if c["id"] not in best_ids:
            outliers.append(c)

    # MAD refine within chosen cluster — do not re-kill pipeline-near as edge locks
    for _ in range(4):
        if len(inliers) < 3:
            break
        dlon = np.array([wrap_diff(c["lon"], lon0) for c in inliers], dtype=np.float64)
        dlat = np.array([c["lat"] - lat0m for c in inliers], dtype=np.float64)
        mad_lon = max(_mad(dlon), 0.05)
        mad_lat = max(_mad(dlat), 0.05)
        keep, drop = [], []
        for c, dl, da in zip(inliers, dlon, dlat):
            bad = (abs(dl) > mad_k * mad_lon) or (abs(da) > mad_k * mad_lat)
            if not c["lat_ok"] and abs(c["lat"] - lat0) > lat_half + 2:
                bad = True
            # Only drop exact boundary locks that are NOT near pipeline
            if is_map_edge_lock(c["lon"], cm_iii_deg, margin_deg=2.5) and not _near_pipeline(
                c["lon"], pipeline_lon, tol_deg=22.0
            ):
                bad = True
            (drop if bad else keep).append(c)
        if len(keep) < min_inliers and len(keep) < len(inliers):
            res = [abs(wrap_diff(c["lon"], lon0)) + abs(c["lat"] - lat0m) for c in inliers]
            order = np.argsort(res)
            nkeep = max(min_inliers, len(inliers) // 2)
            keep = [inliers[i] for i in order[:nkeep]]
            drop = [inliers[i] for i in order[nkeep:]]
        if len(keep) == len(inliers):
            outliers.extend(drop)
            break
        outliers.extend(drop)
        inliers = keep
        lon0 = _circular_median([c["lon"] for c in inliers])
        lat0m = float(np.median([c["lat"] for c in inliers]))

    if not inliers:
        return {"ok": False, "error": "no inliers after cluster filter"}

    dlon = np.array([wrap_diff(c["lon"], lon0) for c in inliers], dtype=np.float64)
    dlat = np.array([c["lat"] - lat0m for c in inliers], dtype=np.float64)
    mad_lon = max(_mad(dlon), 0.05)
    mad_lat = max(_mad(dlat), 0.05)
    weights = {}
    wlist = []
    for c, dl, da in zip(inliers, dlon, dlat):
        rl = abs(dl) / mad_lon
        ra = abs(da) / mad_lat
        soft = 1.0 / (1.0 + rl ** 2 + ra ** 2)
        lat_w = 1.0 if c["lat_ok"] else 0.35
        # boost if near pipeline
        pipe_w = 1.0
        if pipeline_lon is not None:
            pipe_w = 1.0 + 1.0 * max(0.0, 1.0 - abs(wrap_diff(c["lon"], float(pipeline_lon))) / 15.0)
        w = c["w0"] * soft * lat_w * pipe_w
        weights[c["id"]] = w
        wlist.append(w)

    lon_f = _circular_weighted_mean([c["lon"] for c in inliers], wlist)
    lat_f = float(np.average([c["lat"] for c in inliers], weights=wlist))

    # Light optional blend toward pipeline for limb rescue only — report residual in notes.
    # Heavy 30–45% pull removed: SOTA must remain multi-method, not smoothed pipeline.
    if pipeline_lon is not None and abs(wrap_diff(float(pipeline_lon), lon_f)) < 18.0:
        d_pipe = abs(wrap_diff(float(pipeline_lon), lon_f))
        if d_pipe > 4.0:
            pull = 0.12  # soft rescue only when soup and pipeline moderately disagree
            lon_f = wrap_deg(lon_f + pull * wrap_diff(float(pipeline_lon), lon_f))
            if pipeline_lat is not None:
                lat_f = (1.0 - pull) * lat_f + pull * float(pipeline_lat)

    dlon_f = np.array([wrap_diff(c["lon"], lon_f) for c in inliers])
    dlat_f = np.array([c["lat"] - lat_f for c in inliers])
    w = np.asarray(wlist, dtype=np.float64)
    w = w / (w.sum() + 1e-12)
    sig_lon = float(np.sqrt(np.sum(w * dlon_f ** 2)))
    sig_lat = float(np.sqrt(np.sum(w * dlat_f ** 2)))
    neff = 1.0 / (np.sum(w ** 2) + 1e-12)
    # Methods are highly correlated (same dark-mask family). Do NOT publish
    # σ / √neff as independent random error — that understates uncertainty.
    # Floor at population scatter (MAD-like RMS) with a small mean reduction only.
    shrink = min(1.0, 1.0 / math.sqrt(max(min(neff, 3.0), 1.0)))
    sig_lon_mean = max(sig_lon * shrink, mad_lon * 0.5, 0.08)
    sig_lat_mean = max(sig_lat * shrink, mad_lat * 0.5, 0.05)

    # de-dupe outlier ids
    in_ids = {c["id"] for c in inliers}
    out_ids = []
    seen = set()
    for c in outliers:
        if c["id"] not in in_ids and c["id"] not in seen:
            out_ids.append(c["id"])
            seen.add(c["id"])

    return {
        "ok": True,
        "lon": lon_f,
        "lat": lat_f,
        "sigma_lon": sig_lon_mean,
        "sigma_lat": sig_lat_mean,
        "sigma_lon_pop": sig_lon,
        "sigma_lat_pop": sig_lat,
        "inliers": [c["id"] for c in inliers],
        "outliers": out_ids,
        "weights": weights,
        "n_inliers": len(inliers),
        "n_outliers": len(out_ids),
        "neff": float(neff),
        "mad_lon": mad_lon,
        "mad_lat": mad_lat,
        "cluster_score": best_score,
        "n_clusters": len(clusters),
        "cluster_note": (
            f"chose 1 of {len(clusters)} lon-clusters (score={best_score:.1f}); "
            f"edge_locks_dropped={len(edge_out)}; "
            f"pipeline_seed={pipeline_lon}"
        ),
        "edge_locks_dropped": len(edge_out),
    }


def _grade_from_score(
    score: float,
    *,
    flags: Sequence[str],
    n_in: int,
) -> str:
    """Map score→grade with hard vetoes against false EXCELLENT."""
    fl = set(flags)
    # Never EXCELLENT on chaos / limb / bad pipeline / lat fail
    block_excellent = bool(
        fl & {
            "PIPELINE_NEEDS_WORK",
            "NEAR_LIMB",
            "LAT_OFF_GRS_BAND",
            "HIGH_LON_SCATTER",
            "MULTI_CLUSTER",
            "PIPELINE_DISAGREE",
            "LARGE_PIPELINE_SIGMA",
            "WJ_MISMATCH",
        }
    ) or n_in < 10

    if score >= 85 and not block_excellent and "LAT_OFF_GRS_BAND" not in fl:
        return "SOTA_EXCELLENT"
    if score >= 70:
        return "SOTA_GOOD"
    if score >= 55:
        return "SOTA_FAIR"
    if score >= 40:
        return "SOTA_MARGINAL"
    return "SOTA_POOR"


def assess_quality(
    *,
    consensus: Dict[str, Any],
    nav: Optional[NavState] = None,
    distance_au: float = 5.2,
    cm_source: str = "",
    user_time_iso: str = "",
    time_error_seconds: float = 0.0,
    sigma_pipeline_sky: Optional[float] = None,
    pipeline_grade: Optional[str] = None,
    pipeline_lon: Optional[float] = None,
    cm_iii_deg: Optional[float] = None,
) -> Tuple[str, float, List[str], List[str], List[str]]:
    """
    Returns quality_grade, score 0-100, flags, notes, recommendations.

    Design goals (post AS_P5 audit):
      • Never false EXCELLENT on limb / multi-mode / pipeline-bad nights
      • Do not stack penalties into meaningless 0 when SOTA agrees with pipeline
      • Reward pipeline agreement (independent VLBI seed)
    """
    flags: List[str] = []
    notes: List[str] = []
    recs: List[str] = []
    score = 100.0

    n_in = int(consensus.get("n_inliers") or 0)
    n_out = int(consensus.get("n_outliers") or 0)
    sig_lon = float(consensus.get("sigma_lon") or 99)
    sig_lat = float(consensus.get("sigma_lat") or 99)
    lat = float(consensus.get("lat") or 0)
    lon = float(consensus.get("lon") or 0)
    n_clusters = int(consensus.get("n_clusters") or 1)
    edge_drop = int(consensus.get("edge_locks_dropped") or 0)

    if consensus.get("cluster_note"):
        notes.append(str(consensus["cluster_note"]))

    # --- Pipeline agreement (strong independent prior) ---
    d_pipe: Optional[float] = None
    if pipeline_lon is not None and math.isfinite(float(pipeline_lon)):
        d_pipe = abs(wrap_diff(lon, float(pipeline_lon)))
        if d_pipe < 8.0:
            flags.append("PIPELINE_AGREE")
            score += 12
            notes.append(f"SOTA agrees with pipeline seed (Δlon={d_pipe:.2f}°).")
        elif d_pipe < 15.0:
            flags.append("PIPELINE_NEAR")
            score += 6
            notes.append(f"SOTA near pipeline seed (Δlon={d_pipe:.2f}°).")
        elif d_pipe > 30.0:
            flags.append("PIPELINE_DISAGREE")
            score -= 25
            recs.append(
                f"SOTA lon is {d_pipe:.0f}° from pipeline — do not publish without WinJUPOS check."
            )

    if n_in < 4:
        flags.append("LOW_INLIERS")
        score -= 25
        recs.append("Too few agreeing methods — check image sharpness / GRS on disk.")
    elif n_in < 8:
        flags.append("MODERATE_INLIERS")
        score -= 8  # was -10; less harsh when pipeline agrees

    if n_clusters >= 3:
        flags.append("MULTI_CLUSTER")
        # Multi-cluster is expected on hard nights; lighter penalty if pipeline agrees
        score -= 6 if "PIPELINE_AGREE" in flags or "PIPELINE_NEAR" in flags else 12
        recs.append("Multiple lon clusters — GRS may be near limb; verify against WinJUPOS.")
    if edge_drop >= 3:
        notes.append(f"Dropped {edge_drop} map-edge artifact methods (CM±90° locks).")

    # With 80+ estimators, many outliers is normal; only penalize if inliers are scarce
    # AND we do not have pipeline agreement (false CM pile vs true limb cluster).
    if n_out > n_in and n_in < 12 and "PIPELINE_AGREE" not in flags and "PIPELINE_NEAR" not in flags:
        flags.append("MANY_OUTLIERS")
        score -= 12
        notes.append("Many methods diverged and few inliers remain — check image / feature.")
    elif n_out > n_in:
        notes.append(
            f"Excluded {n_out} outlier methods (normal with large method suite); "
            f"{n_in} inliers drive SOTA primary."
        )

    if sig_lon > 1.5:
        flags.append("HIGH_LON_SCATTER")
        score -= 18
        recs.append("High lon scatter — improve stack quality or CM; avoid limb.")
    elif sig_lon > 0.6:
        flags.append("MED_LON_SCATTER")
        score -= 6

    if sig_lat > 1.0:
        flags.append("HIGH_LAT_SCATTER")
        score -= 10

    if abs(lat + 22.0) > 6.0:
        flags.append("LAT_OFF_GRS_BAND")
        score -= 25
        recs.append("Latitude far from ~−22° — may have locked SEB feature, not GRS.")
    elif abs(lat + 22.0) > 3.5:
        flags.append("LAT_MARGINAL")
        score -= 8

    # Limb warning: |lon−CM| large on orthographic maps is hard
    if cm_iii_deg is not None and math.isfinite(float(cm_iii_deg)):
        rel = abs(wrap_diff(lon, float(cm_iii_deg)))
        if rel > 70:
            flags.append("NEAR_LIMB")
            score -= 12  # foreshortening — still measurable if pipeline agrees
            recs.append(
                f"GRS ~{rel:.0f}° from CM (near limb) — foreshortening hurts accuracy; "
                "prefer frames with GRS closer to central meridian."
            )
        elif rel > 50:
            flags.append("OFF_CM")
            score -= 6
            notes.append(f"Feature is {rel:.0f}° from CM — moderately foreshortened.")

    cs = (cm_source or "").lower()
    if any(k in cs for k in ("spice", "winjupos", "horizons", "override")):
        notes.append(f"CM source looks pro-grade: {cm_source}")
        score += 5
    elif "synth" in cs:
        notes.append("Synthetic truth CM (lab mode).")
    else:
        flags.append("WEAK_CM_SOURCE")
        score -= 10
        recs.append("Use SPICE or WinJUPOS CM for absolute System III.")

    if not user_time_iso:
        flags.append("NO_TIME")
        score -= 12
        recs.append("Set mid-exposure UTC (FITS DATE-OBS or manual).")
    elif time_error_seconds > 30:
        flags.append("LARGE_TIME_ERROR")
        score -= 10
        recs.append("Time error >30s adds large Sys-III uncertainty (~0.3° per 30s).")

    if sigma_pipeline_sky is not None:
        if sigma_pipeline_sky <= 1.0:
            score += 5
            notes.append(f"Pipeline σ_sky={sigma_pipeline_sky:.3f}″ excellent.")
        elif sigma_pipeline_sky > 3.0:
            score -= 12
            flags.append("LARGE_PIPELINE_SIGMA")
            recs.append(
                f"VLBI/pipeline σ_sky={sigma_pipeline_sky:.1f}″ is large (NEEDS_WORK territory) — "
                "do not treat SOTA as high confidence."
            )

    pg = (pipeline_grade or "").upper()
    if "NEEDS_WORK" in pg or "POOR" in pg:
        flags.append("PIPELINE_NEEDS_WORK")
        # Don't double-destroy score if we already agree on a hard night
        score -= 12 if ("PIPELINE_AGREE" in flags or "PIPELINE_NEAR" in flags) else 20
        notes.append(f"Pipeline grade={pipeline_grade} — SOTA capped (no EXCELLENT).")
    elif "FAIR" in pg:
        score -= 6

    if nav is not None:
        a = getattr(nav, "a_eq_px", 0) or 0
        if a < 40:
            flags.append("SMALL_DISK_PX")
            score -= 15
            recs.append("Disk too small in pixels — use higher resolution / longer FL.")
        elif a > 80:
            score += 3

    score = float(np.clip(score, 0, 100))

    # Caps: never call EXCELLENT/GOOD on known-hard conditions
    if "PIPELINE_NEEDS_WORK" in flags or "NEAR_LIMB" in flags:
        score = min(score, 68.0)
    if "LARGE_PIPELINE_SIGMA" in flags:
        score = min(score, 72.0)
    if "PIPELINE_DISAGREE" in flags:
        score = min(score, 45.0)
    if "MULTI_CLUSTER" in flags and "PIPELINE_AGREE" not in flags:
        score = min(score, 69.0)

    # Floor: if we locked onto pipeline neighborhood with sane lat, score isn't "zero"
    # (AS_P5-class nights: correct limb cluster + honest hardship flags).
    if (
        ("PIPELINE_AGREE" in flags or "PIPELINE_NEAR" in flags)
        and "LAT_OFF_GRS_BAND" not in flags
        and n_in >= 4
    ):
        floor = 42.0 if "NEAR_LIMB" in flags or "PIPELINE_NEEDS_WORK" in flags else 50.0
        if score < floor:
            notes.append(
                f"Score floored to {floor:.0f}: SOTA↔pipeline agreement on hard night "
                "(usable rough measure, not publication-grade)."
            )
            score = floor

    grade = _grade_from_score(score, flags=flags, n_in=n_in)

    # sky approx from lon/lat σ at GRS lat
    try:
        sky = sky_error_arcsec(sig_lon, sig_lat, lat, distance_au)
    except Exception:
        sky = float("nan")

    notes.append(
        f"Robust consensus from {n_in} inliers "
        f"(excluded {n_out} outlier methods). "
        f"σ_lon≈{sig_lon:.4f}° σ_lat≈{sig_lat:.4f}° (~{sky:.3f}″ if geometry OK)."
    )
    recs.append("For publication: report SOTA lon/lat + definition=SOTA_ROBUST + CM source + σ.")
    recs.append("Validate with WinJUPOS manual pick when possible (not auto-detect).")
    recs.append("Best absolute accuracy needs excellent stack + SPICE/WJ CM + accurate UTC.")

    return grade, float(score), flags, notes, recs


def extract_fits_time(path: Optional[str]) -> Optional[str]:
    """Best-effort mid-exposure time from FITS header."""
    if not path:
        return None
    p = Path(path)
    if p.suffix.lower() not in (".fit", ".fits", ".fts"):
        return None
    try:
        import grs_complete_system as grs
        _, hdr = grs.read_fits(p)
    except Exception as e:
        CONSOLE.debug(f"FITS time read: {e}")
        return None

    def pick(*keys):
        for k in keys:
            for kk, vv in (hdr or {}).items():
                if str(kk).upper() == k.upper() and vv not in (None, ""):
                    return str(vv).strip()
        return None

    date = pick("DATE-OBS", "DATE_OBS", "DATE")
    time = pick("TIME-OBS", "TIME_OBS", "UT", "UTC")
    exp = pick("EXPTIME", "EXPOSURE", "TELAPSE")

    raw = None
    if date and "T" in date:
        raw = date.replace("T", " ")
    elif date and time:
        raw = f"{date} {time}"
    elif date:
        raw = date
    if not raw:
        return None

    # normalize
    raw = raw.replace("T", " ").replace("Z", "").strip()
    raw = re.sub(r"\s+", " ", raw)
    # try parse
    dt = None
    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
        "%Y-%m-%d",
    ):
        try:
            dt = datetime.strptime(raw[:26], fmt)
            break
        except Exception:
            continue
    if dt is None:
        # last resort
        try:
            from grs_complete_system import parse_time_string
            dt = parse_time_string(raw)
        except Exception:
            return raw[:19] if len(raw) >= 10 else None

    # mid-exposure if EXPTIME present
    try:
        if exp is not None:
            ex = float(exp)
            if ex > 0 and ex < 86400:
                dt = dt + timedelta(seconds=ex / 2.0)
    except Exception:
        pass

    return dt.strftime("%Y-%m-%d %H:%M:%S")


def run_sota_accuracy(
    methods: Sequence[Dict[str, Any]],
    *,
    nav: Optional[NavState] = None,
    distance_au: float = 5.2,
    cm_source: str = "",
    user_time_iso: str = "",
    time_error_seconds: float = 0.0,
    sigma_pipeline_sky: Optional[float] = None,
    pipeline_grade: Optional[str] = None,
    pipeline_lon: Optional[float] = None,
    pipeline_lat: Optional[float] = None,
    cm_iii_deg: Optional[float] = None,
    fits_path: Optional[str] = None,
    winjupos_manual_lon: Optional[float] = None,
    winjupos_manual_lat: Optional[float] = None,
) -> SOTAResult:
    """
    Build SOTA robust primary from full method list.
    """
    fits_time = extract_fits_time(fits_path)
    if fits_time and not user_time_iso:
        user_time_iso = fits_time
        CONSOLE.ok(f"SOTA: FITS time → {fits_time}")

    if cm_iii_deg is None and nav is not None:
        cm_iii_deg = float(getattr(nav, "cm_iii_deg", 0) or 0)

    cons = robust_consensus(
        list(methods),
        cm_iii_deg=cm_iii_deg,
        pipeline_lon=pipeline_lon,
        pipeline_lat=pipeline_lat,
    )
    if not cons.get("ok"):
        return SOTAResult(
            ok=False,
            lon_iii_deg=float("nan"),
            lat_deg=float("nan"),
            sigma_lon_deg=99.0,
            sigma_lat_deg=99.0,
            sigma_sky_arcsec_approx=99.0,
            n_methods_total=len(methods),
            n_methods_ok=sum(1 for m in methods if m.get("ok", True)),
            n_inliers=0,
            n_outliers=0,
            quality_grade="SOTA_FAILED",
            quality_notes=[cons.get("error", "consensus failed")],
            fits_time=fits_time,
            recommendations=["Check image and re-run."],
        )

    grade, score, flags, notes, recs = assess_quality(
        consensus=cons,
        nav=nav,
        distance_au=distance_au,
        cm_source=cm_source,
        user_time_iso=user_time_iso,
        time_error_seconds=time_error_seconds,
        sigma_pipeline_sky=sigma_pipeline_sky,
        pipeline_grade=pipeline_grade,
        pipeline_lon=pipeline_lon,
        cm_iii_deg=cm_iii_deg,
    )

    try:
        sky = sky_error_arcsec(
            float(cons["sigma_lon"]),
            float(cons["sigma_lat"]),
            float(cons["lat"]),
            distance_au,
        )
    except Exception:
        sky = float("nan")

    # optional WJ validation note — re-grade after score change
    if winjupos_manual_lon is not None:
        dlon = wrap_diff(cons["lon"], float(winjupos_manual_lon))
        dlat = float(cons["lat"]) - float(winjupos_manual_lat if winjupos_manual_lat is not None else cons["lat"])
        try:
            sky_wj = sky_error_arcsec(
                dlon, dlat,
                float(winjupos_manual_lat if winjupos_manual_lat is not None else cons["lat"]),
                distance_au,
            )
        except Exception:
            sky_wj = float("nan")
        notes.append(
            f"vs WinJUPOS manual: Δlon={dlon:.4f}° Δlat={dlat:.4f}° sky≈{sky_wj:.3f}″"
        )
        if math.isfinite(sky_wj) and sky_wj <= 2.0:
            score = min(100.0, score + 8)
            flags = list(flags) + (["WJ_MATCH"] if "WJ_MATCH" not in flags else [])
            grade = _grade_from_score(score, flags=flags, n_in=int(cons["n_inliers"]))
            if not grade.endswith("+WJ"):
                grade = grade + "+WJ"
        elif math.isfinite(sky_wj) and sky_wj > 5.0:
            if "WJ_MISMATCH" not in flags:
                flags = list(flags) + ["WJ_MISMATCH"]
            score = min(score, 55.0)
            grade = _grade_from_score(score, flags=flags, n_in=int(cons["n_inliers"]))
            recs.append("Large Δ vs your WinJUPOS pick — check definition (core vs edge) and CM.")

    CONSOLE.ok(
        f"SOTA primary lon={cons['lon']:.5f}° lat={cons['lat']:.5f}°  "
        f"inliers={cons['n_inliers']}/{cons['n_inliers']+cons['n_outliers']}  "
        f"σ_lon={cons['sigma_lon']:.4f}°  {grade} score={score:.0f}"
    )

    return SOTAResult(
        ok=True,
        lon_iii_deg=float(cons["lon"]),
        lat_deg=float(cons["lat"]),
        sigma_lon_deg=float(cons["sigma_lon"]),
        sigma_lat_deg=float(cons["sigma_lat"]),
        sigma_sky_arcsec_approx=float(sky) if math.isfinite(sky) else float("nan"),
        n_methods_total=len(methods),
        n_methods_ok=sum(1 for m in methods if m.get("ok", True)),
        n_inliers=int(cons["n_inliers"]),
        n_outliers=int(cons["n_outliers"]),
        inlier_ids=list(cons["inliers"]),
        outlier_ids=list(cons["outliers"]),
        primary_label="SOTA_ROBUST",
        quality_grade=grade,
        quality_score=float(score),
        quality_flags=flags,
        quality_notes=notes,
        method_weights={k: float(v) for k, v in (cons.get("weights") or {}).items()},
        consensus_detail={
            "mad_lon": cons.get("mad_lon"),
            "mad_lat": cons.get("mad_lat"),
            "neff": cons.get("neff"),
            "sigma_lon_pop": cons.get("sigma_lon_pop"),
            "sigma_lat_pop": cons.get("sigma_lat_pop"),
            "cluster_note": cons.get("cluster_note"),
            "n_clusters": cons.get("n_clusters"),
            "edge_locks_dropped": cons.get("edge_locks_dropped"),
            "pipeline_seed_lon": pipeline_lon,
            "pipeline_seed_lat": pipeline_lat,
        },
        fits_time=fits_time,
        recommendations=recs,
    )


def apply_sota_to_package(
    package: Dict[str, Any],
    *,
    nav: Optional[NavState] = None,
    distance_au: float = 5.2,
    cm_source: str = "",
    user_time_iso: str = "",
    time_error_seconds: float = 0.0,
    fits_path: Optional[str] = None,
    winjupos_manual_lon: Optional[float] = None,
    winjupos_manual_lat: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Read methods from package gold_standard / all_methods and write SOTA primary.
    Overwrites headline gold_* with SOTA robust values when ok.
    """
    methods: List[Dict[str, Any]] = []
    am = package.get("all_methods") or (package.get("gold_standard") or {}).get("all_methods")
    if isinstance(am, dict) and am.get("methods"):
        methods = list(am["methods"])
    gs = package.get("gold_standard") or {}
    if not methods and gs.get("measures"):
        for m in gs["measures"]:
            methods.append({
                "method_id": m.get("definition_id"),
                "family": "map",
                "lon_iii_deg": m.get("lon_iii_deg"),
                "lat_deg": m.get("lat_deg"),
                "weight": m.get("weight", 1.0),
                "ok": m.get("ok", True),
            })

    rg = package.get("research_grade") or {}
    sig_sky = rg.get("sigma_total_sky_arcsec")
    h = package.get("headline") or {}
    if sig_sky is None:
        sig_sky = h.get("sigma_total_sky_arcsec")
    # Prefer unbiased VLBI/pipeline centre as cluster seed (before SOTA overwrite)
    pipe_lon = rg.get("lon_bias_corrected_deg")
    if pipe_lon is None:
        pipe_lon = rg.get("lon_iii_deg")
    if pipe_lon is None:
        pipe_lon = h.get("lon_iii_deg")  # desktop headline raw
    pipe_lat = rg.get("lat_bias_corrected_deg")
    if pipe_lat is None:
        pipe_lat = rg.get("lat_deg")
    if pipe_lat is None:
        pipe_lat = h.get("lat_deg")
    pipe_grade = rg.get("grade") or h.get("grade")
    cm_use = None
    if nav is not None:
        cm_use = float(getattr(nav, "cm_iii_deg", 0) or 0)
    if not cm_use:
        cm_use = h.get("cm_iii_deg") or gs.get("cm_iii_deg")
    pe = package.get("pro_ephemeris") or {}
    if not cm_use and pe.get("cm_iii_deg") is not None:
        cm_use = pe.get("cm_iii_deg")

    sota = run_sota_accuracy(
        methods,
        nav=nav,
        distance_au=distance_au or float(h.get("distance_au") or pe.get("distance_au") or 5.2),
        cm_source=cm_source or str(h.get("cm_source") or gs.get("cm_source") or pe.get("cm_source") or ""),
        user_time_iso=user_time_iso or str(package.get("user_time") or h.get("user_time") or ""),
        time_error_seconds=float(time_error_seconds or package.get("time_error_seconds") or 0),
        sigma_pipeline_sky=float(sig_sky) if sig_sky is not None else None,
        pipeline_grade=str(pipe_grade) if pipe_grade else None,
        pipeline_lon=float(pipe_lon) if pipe_lon is not None else None,
        pipeline_lat=float(pipe_lat) if pipe_lat is not None else None,
        cm_iii_deg=float(cm_use) if cm_use is not None else None,
        fits_path=fits_path or package.get("path"),
        winjupos_manual_lon=winjupos_manual_lon,
        winjupos_manual_lat=winjupos_manual_lat,
    )
    package["sota"] = sota.to_dict()

    if sota.ok:
        h = package.setdefault("headline", {})
        # Preserve VLBI/pipeline bias-corrected as its own product (do not clobber with SOTA).
        # NASA/YOUR table should prefer research_grade; gold/SOTA are procedure primaries.
        if pipe_lon is not None and h.get("pipeline_lon_iii_deg") is None:
            h["pipeline_lon_iii_deg"] = float(pipe_lon)
        if pipe_lat is not None and h.get("pipeline_lat_deg") is None:
            h["pipeline_lat_deg"] = float(pipe_lat)
        if (
            rg.get("lon_bias_corrected_deg") is not None
            and h.get("pipeline_lon_bias_corrected_deg") is None
        ):
            h["pipeline_lon_bias_corrected_deg"] = float(rg["lon_bias_corrected_deg"])
            h["pipeline_lat_bias_corrected_deg"] = float(
                rg.get("lat_bias_corrected_deg")
                if rg.get("lat_bias_corrected_deg") is not None
                else (pipe_lat if pipe_lat is not None else float("nan"))
            )
        # Keep pipeline bias-corrected on headline if not already set from research_grade
        if h.get("lon_iii_deg_bias_corrected") is None and rg.get("lon_bias_corrected_deg") is not None:
            h["lon_iii_deg_bias_corrected"] = float(rg["lon_bias_corrected_deg"])
            h["lat_deg_bias_corrected"] = float(
                rg.get("lat_bias_corrected_deg")
                if rg.get("lat_bias_corrected_deg") is not None
                else (pipe_lat if pipe_lat is not None else float("nan"))
            )

        # SOTA = multi-method scatter / confidence only — NEVER replace named GS primary
        h["sota_lon_iii_deg"] = sota.lon_iii_deg
        h["sota_lat_deg"] = sota.lat_deg
        h["sota_quality"] = sota.quality_grade
        h["sota_score"] = sota.quality_score
        h["sota_sigma_lon_deg"] = sota.sigma_lon_deg
        h["sota_sigma_lat_deg"] = sota.sigma_lat_deg
        h["sota_n_inliers"] = sota.n_inliers
        h["sota_role"] = "scatter_confidence_only"
        # Preserve classic gold headline keys if gold already attached
        if isinstance(gs, dict) and gs.get("ok") is not False:
            if h.get("gold_primary_definition") is None:
                h["gold_primary_definition"] = gs.get("primary_definition")
            if h.get("gold_lon_iii_deg") is None and gs.get("primary_lon_iii_deg") is not None:
                h["gold_lon_iii_deg"] = gs.get("primary_lon_iii_deg")
                h["gold_lat_deg"] = gs.get("primary_lat_deg")
            if h.get("gold_procedure_grade") is None:
                h["gold_procedure_grade"] = gs.get("grade")
            # Attach SOTA alongside gold — do not mutate named definition primary
            gs = dict(gs)
            gs["sota"] = sota.to_dict()
            gs["sota_role"] = "scatter_confidence_only"
            package["gold_standard"] = gs
        if sota.fits_time:
            h["fits_time"] = sota.fits_time
            package.setdefault("fits_time_extracted", sota.fits_time)

    return package


def format_sota_section(sota: Dict[str, Any]) -> str:
    if not sota:
        return "  (SOTA layer not run)\n"
    lines = [
        "SOTA ROBUST PRIMARY (best accuracy procedure)",
        f"  Lon III           {sota.get('lon_iii_deg')}",
        f"  Lat               {sota.get('lat_deg')}",
        f"  σ_lon (SE)        {sota.get('sigma_lon_deg')} °",
        f"  σ_lat (SE)        {sota.get('sigma_lat_deg')} °",
        f"  σ_sky (approx)    {sota.get('sigma_sky_arcsec_approx')} ″",
        f"  Quality           {sota.get('quality_grade')}  score={sota.get('quality_score')}",
        f"  Inliers/outliers  {sota.get('n_inliers')}/{sota.get('n_outliers')}",
        f"  Methods ok/total  {sota.get('n_methods_ok')}/{sota.get('n_methods_total')}",
        f"  FITS time         {sota.get('fits_time')}",
        f"  COPY: SOTA_LON={sota.get('lon_iii_deg')}  SOTA_LAT={sota.get('lat_deg')}",
        "  Inliers: " + ", ".join((sota.get("inlier_ids") or [])[:40]),
        "  Outliers excluded: " + ", ".join((sota.get("outlier_ids") or [])[:30]),
    ]
    for n in sota.get("quality_notes") or []:
        lines.append(f"  · {n}")
    for f in sota.get("quality_flags") or []:
        lines.append(f"  FLAG: {f}")
    for r in sota.get("recommendations") or []:
        lines.append(f"  → {r}")
    return "\n".join(lines) + "\n"

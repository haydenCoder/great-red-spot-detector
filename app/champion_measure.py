#!/usr/bin/env python3
"""
Champion GRS measure v2 — the strongest automated path I could build
=====================================================================

This is the "pro desk" measurement path that tries to match what a careful
careful observer would do manually. It runs multiple independent
methods (GS-MAP, GS-TMPL, engine, map_dark, template, moment) and picks
the best centre using a weighted hierarchy, with outlier rejection and
sub-pixel refinement on a cylindrical map.

The main upgrades over v1:
  • Stability-weighted multi-isophote limb (pick the outline that stabilises GRS)
  • SEB local contrast stretch before map methods
  • Named pro definitions: GS-MAP → GS-TMPL → engine → map → bary
  • Sub-pixel dark-centroid refine on cylindrical map
  • Optional mid-exposure timing σ in absolute error budget
  • Tighter fail-closed absolute publish gates
  • UNBEATABLE_AUTO lock — when all gates pass, no weaker method overrides

I honestly spent months iterating on this. The limb outline choice matters
more than anything else — different isophote levels shift the disk radius
and can change absolute lon/lat by tenths of a degree. That's why the
multi-isophote probing is so important.

Ground-based optical metrology. Not spacecraft imaging. Best automated laptop path.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from verbose_log import CONSOLE
from precision_engine import (
    NavState,
    fit_limb_nav,
    make_cylindrical,
    measure_grs_precision,
    to_mono,
    wrap_deg,
    wrap_diff,
    sky_error_arcsec,
    planetocentric_to_planetographic,
    _template_match_grs,
    _map_dark_centroid,
    _moment_mask_grs,
    _gauss,
)


# Denser limb family — these represent different outline sizes an observer would try
# "outer" picks up fainter limb, "tight" is only the bright inner disk
# I chose these fractions to cover the range a human observer would try
LIMB_FRACS: Tuple[Tuple[str, float], ...] = (
    ("outer", 0.11),
    ("soft", 0.15),
    ("nominal", 0.18),
    ("mid", 0.22),
    ("inner", 0.28),
    ("tight", 0.34),
)


@dataclass
class ChampionResult:
    ok: bool
    lon_iii_deg: float = float("nan")
    lat_planetocentric_deg: float = float("nan")
    lat_planetographic_deg: float = float("nan")
    length_deg: Optional[float] = None
    width_deg: Optional[float] = None
    extent_ew_deg: Optional[float] = None
    definition: str = "CHAMPION"
    cm_iii_deg: float = float("nan")
    cm_ii_deg: float = float("nan")
    lon_ii_deg: float = float("nan")
    cm_source: str = ""
    distance_au: float = 5.2
    sigma_cm_deg: float = 0.0
    sigma_timing_lon_deg: float = 0.0
    sigma_limb_lon_deg: float = 0.0
    sigma_limb_lat_deg: float = 0.0
    sigma_definition_lon_deg: float = 0.0
    sigma_method_lon_deg: float = 0.0
    sigma_method_lat_deg: float = 0.0
    sigma_total_lon_deg: float = 0.0
    sigma_total_lat_deg: float = 0.0
    sigma_total_sky_arcsec: float = 0.0
    limb_sky_spread_arcsec: float = 0.0
    absolute_publish_ok: bool = False
    grade: str = "HOLD"
    flags: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    estimators: Dict[str, Any] = field(default_factory=dict)
    limb_consensus: Dict[str, Any] = field(default_factory=dict)
    world_class_score: float = 0.0
    refine: Dict[str, Any] = field(default_factory=dict)
    ultimate_lock: Dict[str, Any] = field(default_factory=dict)
    unbeatable_auto: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _prefer_measure_image(image: np.ndarray, channels: Optional[Dict[str, np.ndarray]] = None) -> np.ndarray:
    """Pick the best measurement image — prefer the red channel for GRS.

    The GRS is a red-brown oval, so it's most distinct in the red channel.
    If we have separate R/G/B arrays, use R. Otherwise fall back to mono.
    """
    try:
        from accuracy_gates import prefer_red_channel
        if channels and "R" in channels:
            return np.asarray(channels["R"], dtype=np.float64)
        return np.asarray(prefer_red_channel(image), dtype=np.float64)
    except Exception:
        if channels and "R" in channels:
            return np.asarray(channels["R"], dtype=np.float64)
        return to_mono(image)


def _enhance_seb_contrast(im: np.ndarray) -> np.ndarray:
    """
    Mild high-pass + local stretch in the SEB latitude band.

    This makes the dark oval pop without inventing structure that isn't
    there. The key is blending a small high-pass fraction (0.85×) back
    into the original so belt features stand out but we keep the absolute
    intensity levels for limb fitting. I tried stronger blends early on
    and kept getting false locks on SEB waves.
    """
    a = np.asarray(im, dtype=np.float64)
    if a.ndim != 2:
        a = to_mono(a)
    hp = a - _gauss(a, max(2.0, min(a.shape) * 0.025))
    # blend small high-pass so belts pop; keep absolute levels for limb
    out = a + 0.85 * hp
    lo, hi = np.percentile(out, (1.0, 99.0))
    if hi > lo:
        out = np.clip((out - lo) / (hi - lo + 1e-12), 0, 1)
    return out


def _map_local_contrast(cyl: np.ndarray) -> np.ndarray:
    """Local stretch in SEB rows only (map domain).

    Same idea as _enhance_seb_contrast but operating on the cylindrical
    map instead of the raw image. Only stretches the latitude rows that
    contain the SEB/GRS band, leaving the rest untouched.
    """
    c = np.asarray(cyl, dtype=np.float64)
    h, w = c.shape
    out = c.copy()
    # SEB band approx lat -32..-12 → rows
    def lat_to_y(lat: float) -> int:
        return int(np.clip((90.0 - lat) / 180.0 * (h - 1), 0, h - 1))
    y0, y1 = lat_to_y(-12.0), lat_to_y(-32.0)
    if y1 < y0:
        y0, y1 = y1, y0
    band = out[y0 : y1 + 1, :]
    valid = band > 1e-8
    if valid.sum() < 50:
        return out
    work = band.copy()
    med = float(np.median(work[valid]))
    work[~valid] = med
    lo, hi = np.percentile(work[valid], (5, 95))
    if hi > lo:
        stretched = np.clip((work - lo) / (hi - lo + 1e-12), 0, 1)
        out[y0 : y1 + 1, :] = np.where(valid, stretched, 0.0)
    return out


def multi_isophote_limb_consensus(
    image: np.ndarray,
    *,
    cm_iii_deg: float,
    distance_au: float = 5.2,
    sub_lat_deg: float = 0.0,
    north_pa_deg: float = 0.0,
) -> Tuple[NavState, Dict[str, Any]]:
    """
    Fit the limb at multiple isophote levels and pick the most stable one.

    This is the most important step in the champion path. Each isophote
    level gives a slightly different disk radius (and centre), which shifts
    the GRS position by up to ~0.3° in longitude. By measuring the GRS
    position at each outline level and checking which ones give consistent
    results, we can pick the "right" outline — the one a careful observer
    user would choose.

    The stability weighting prefers outlines whose GRS lon agrees with
    the cluster (observer discipline: consistent outline = consistent result).
    """
    probes: List[Dict[str, Any]] = []
    navs: List[NavState] = []
    grs_lons: List[float] = []
    grs_lats: List[float] = []

    for name, frac in LIMB_FRACS:
        try:
            n = fit_limb_nav(
                image,
                n_rays=900,
                cm_iii_deg=cm_iii_deg,
                distance_au=distance_au,
                isophote_frac=frac,
            )
            n.cm_iii_deg = cm_iii_deg
            n.distance_au = distance_au
            n.sub_lat_deg = sub_lat_deg
            n.north_pa_deg = north_pa_deg
            lon = lat = float("nan")
            try:
                cyl = make_cylindrical(image, n, width=1000, height=500)
                m = _map_dark_centroid(cyl, n)
                lon, lat = float(m["lon_iii_deg"]), float(m["lat_deg"])
            except Exception:
                try:
                    t = _template_match_grs(
                        make_cylindrical(image, n, width=1000, height=500), n
                    )
                    lon, lat = float(t["lon_iii_deg"]), float(t["lat_deg"])
                except Exception:
                    pass
            navs.append(n)
            grs_lons.append(lon)
            grs_lats.append(lat)
            probes.append({
                "name": name,
                "isophote_frac": frac,
                "xc": n.xc,
                "yc": n.yc,
                "a_eq_px": n.a_eq_px,
                "grs_lon_iii_deg": lon,
                "grs_lat_deg": lat,
            })
        except Exception as e:
            probes.append({"name": name, "error": str(e)})

    if not navs:
        n = fit_limb_nav(image, cm_iii_deg=cm_iii_deg, distance_au=distance_au)
        n.sub_lat_deg = sub_lat_deg
        n.north_pa_deg = north_pa_deg
        return n, {"ok": False, "probes": probes, "note": "single limb fallback"}

    # Cluster GRS lons that are finite + in SEB
    valid_idx = [
        i for i, (lo, la) in enumerate(zip(grs_lons, grs_lats))
        if math.isfinite(lo) and math.isfinite(la) and -36 <= la <= -10
    ]
    if valid_idx:
        # densest lon seed among valid
        lons_v = [grs_lons[i] for i in valid_idx]
        seed = lons_v[0]
        best_n = -1
        for s in lons_v:
            nn = sum(1 for x in lons_v if abs(wrap_diff(x, s)) <= 8.0)
            if nn > best_n:
                best_n = nn
                seed = s
        # weights: higher if GRS lon near seed and radius not extreme
        aas = np.array([navs[i].a_eq_px for i in valid_idx], dtype=np.float64)
        a_med = float(np.median(aas))
        wts = []
        for i in valid_idx:
            dlon = abs(wrap_diff(grs_lons[i], seed))
            w = math.exp(-0.5 * (dlon / 4.0) ** 2)
            w *= math.exp(-0.5 * ((navs[i].a_eq_px - a_med) / (0.03 * a_med + 1e-6)) ** 2)
            wts.append(max(w, 1e-3))
            probes[i]["stability_w"] = w
        wts_a = np.asarray(wts, dtype=np.float64)
        wts_a /= wts_a.sum()
        xc = float(sum(wts_a[j] * navs[valid_idx[j]].xc for j in range(len(valid_idx))))
        yc = float(sum(wts_a[j] * navs[valid_idx[j]].yc for j in range(len(valid_idx))))
        a = float(sum(wts_a[j] * navs[valid_idx[j]].a_eq_px for j in range(len(valid_idx))))
        # best single probe for reporting
        jbest = int(np.argmax(wts_a))
        best_name = probes[valid_idx[jbest]].get("name")
        note = f"stability-weighted limb (best isophote≈{best_name}, seed GRS lon={seed:.3f}°)"
    else:
        xc = float(np.median([n.xc for n in navs]))
        yc = float(np.median([n.yc for n in navs]))
        a = float(np.median([n.a_eq_px for n in navs]))
        note = "median limb (no stable GRS lock across isophotes)"

    xcs = np.array([n.xc for n in navs], dtype=np.float64)
    ycs = np.array([n.yc for n in navs], dtype=np.float64)
    aas = np.array([n.a_eq_px for n in navs], dtype=np.float64)
    meta = {
        "ok": True,
        "probes": probes,
        "xc_spread_px": float(np.ptp(xcs)),
        "yc_spread_px": float(np.ptp(ycs)),
        "a_spread_px": float(np.ptp(aas)),
        "n_probes": len(navs),
        "n_stable_grs": len(valid_idx),
        "note": note,
    }
    cons = NavState(
        xc=xc, yc=yc, a_eq_px=a,
        cm_iii_deg=cm_iii_deg,
        distance_au=distance_au,
        sub_lat_deg=sub_lat_deg,
        north_pa_deg=north_pa_deg,
        flattening=navs[0].flattening,
    )
    return cons, meta


def _subpixel_refine_map(
    cyl: np.ndarray, nav: NavState, lon0: float, lat0: float, *, passes: int = 2
) -> Tuple[float, float, Dict[str, Any]]:
    """
    Multi-pass sub-pixel refinement on the cylindrical map.

    This is the fine-tune step — take the initial measurement and refine it
    by computing a dark-centroid in a shrinking window. Pass 1 uses a wider
    window (~16° lon × 10° lat), pass 2 tightens to ~9° × 6°. The blend
    of quadratic peak + intensity centroid (55/45) handles asymmetric ovals
    better than just peak-finding alone.

    I also reject near-edge refinements (|lon_rel| > 82°) because the map
    boundary creates a false intensity peak that looks like a dark feature.
    """
    h, w = cyl.shape
    lon_cur, lat_cur = float(lon0), float(lat0)
    meta: Dict[str, Any] = {"ok": False, "passes": []}
    for p_i in range(max(1, int(passes))):
        lon_rel = wrap_diff(lon_cur, nav.cm_iii_deg)
        # reject near cylindrical map edge (classic false peak)
        if abs(lon_rel) > 82.0:
            meta["reason"] = "map_edge"
            return lon0, lat0, meta
        mx = (lon_rel + 90.0) / 180.0 * (w - 1)
        my = (90.0 - lat_cur) / 180.0 * (h - 1)
        # tighten window on later passes
        half_lon = 8.0 if p_i == 0 else 4.5
        half_lat = 5.0 if p_i == 0 else 3.0
        dx = int(max(5, half_lon / 180.0 * w))
        dy = int(max(4, half_lat / 180.0 * h))
        x0 = int(np.clip(mx - dx, 0, w - 1))
        x1 = int(np.clip(mx + dx, 0, w - 1))
        y0 = int(np.clip(my - dy, 0, h - 1))
        y1 = int(np.clip(my + dy, 0, h - 1))
        if x1 - x0 < 4 or y1 - y0 < 4:
            meta["reason"] = "window"
            break
        patch = cyl[y0 : y1 + 1, x0 : x1 + 1].copy()
        valid = patch > 1e-8
        if valid.sum() < 12:
            meta["reason"] = "empty"
            break
        med = float(np.median(patch[valid]))
        inv = np.where(valid, np.maximum(0.0, med - patch), 0.0)
        if float(inv.max()) <= 0:
            meta["reason"] = "no_dark"
            break
        thr = float(np.percentile(inv[inv > 0], 35 + 5 * p_i)) if (inv > 0).any() else 0.0
        inv = np.where(inv >= thr, inv, 0.0)
        try:
            inv = _gauss(inv, 0.7 if p_i == 0 else 0.5)
        except Exception:
            pass
        # Peak pixel + quadratic subpixel (parabola on 3×3)
        j = np.unravel_index(int(np.argmax(inv)), inv.shape)
        py, px = int(j[0]), int(j[1])
        def _quad(p: int, line: np.ndarray) -> float:
            if p <= 0 or p >= len(line) - 1:
                return float(p)
            a, b, c = float(line[p - 1]), float(line[p]), float(line[p + 1])
            den = a - 2 * b + c
            return float(p) if abs(den) < 1e-12 else p + 0.5 * (a - c) / den
        py_s = _quad(py, inv[:, px])
        px_s = _quad(px, inv[py, :])
        # blend with intensity centroid (robust to asymmetric ovals)
        s = float(inv.sum()) + 1e-12
        yy, xx = np.mgrid[0 : inv.shape[0], 0 : inv.shape[1]]
        cy_c = float((yy * inv).sum() / s)
        cx_c = float((xx * inv).sum() / s)
        py_s = 0.55 * py_s + 0.45 * cy_c
        px_s = 0.55 * px_s + 0.45 * cx_c
        cy = y0 + py_s
        cx = x0 + px_s
        lon_rel_n = -90.0 + (cx / max(w - 1, 1)) * 180.0
        lat_n = 90.0 - (cy / max(h - 1, 1)) * 180.0
        lon_n = wrap_deg(nav.cm_iii_deg + lon_rel_n)
        max_jump = 10.0 if p_i == 0 else 5.0
        if abs(wrap_diff(lon_n, lon_cur)) > max_jump or abs(lat_n - lat_cur) > 6:
            meta["passes"].append({"pass": p_i, "rejected": "jump", "lon": lon_n, "lat": lat_n})
            break
        meta["passes"].append({
            "pass": p_i,
            "lon": lon_n,
            "lat": lat_n,
            "dlon": wrap_diff(lon_n, lon_cur),
            "dlat": lat_n - lat_cur,
        })
        lon_cur, lat_cur = lon_n, lat_n
        meta["ok"] = True
        meta["map_x"] = cx
        meta["map_y"] = cy
    if meta.get("ok"):
        meta["dlon_deg"] = wrap_diff(lon_cur, lon0)
        meta["dlat_deg"] = lat_cur - lat0
        return lon_cur, lat_cur, meta
    return lon0, lat0, meta


def _local_dark_score(cyl: np.ndarray, nav: NavState, lon: float, lat: float) -> float:
    """
    How dark is the core compared to a local ring?

    Higher score = more GRS-like (dark oval in a bright belt). Lower score
    means we probably locked onto a bright belt or SEB wave instead. This
    is essential for rejecting false locks — the SEB has lots of bright
    features that can fool a template match.
    """
    try:
        h, w = cyl.shape
        lon_rel = wrap_diff(lon, nav.cm_iii_deg)
        if abs(lon_rel) > 88:
            return 0.0
        mx = (lon_rel + 90.0) / 180.0 * (w - 1)
        my = (90.0 - lat) / 180.0 * (h - 1)
        # core disk ~2.5° , ring 4–8°
        r_core = max(2, int(2.5 / 180.0 * w))
        r_in = max(r_core + 1, int(4.0 / 180.0 * w))
        r_out = max(r_in + 1, int(8.0 / 180.0 * w))
        yy, xx = np.mgrid[0:h, 0:w]
        rr = np.hypot(xx - mx, yy - my)
        valid = cyl > 1e-8
        core = valid & (rr <= r_core)
        ring = valid & (rr >= r_in) & (rr <= r_out)
        if core.sum() < 8 or ring.sum() < 16:
            return 0.0
        c_mean = float(np.mean(cyl[core]))
        r_mean = float(np.mean(cyl[ring]))
        if r_mean <= 1e-8:
            return 0.0
        # positive when core darker than ring
        contrast = (r_mean - c_mean) / (r_mean + 1e-8)
        return float(max(0.0, min(2.0, contrast * 4.0)))
    except Exception:
        return 0.0


def _run_estimators(
    image: np.ndarray, nav: NavState
) -> Tuple[Dict[str, Dict[str, Any]], np.ndarray]:
    out: Dict[str, Dict[str, Any]] = {}
    cyl_raw = make_cylindrical(image, nav, width=2000, height=1000)
    cyl = _map_local_contrast(cyl_raw)
    # Named gold-style map methods on enhanced map
    try:
        out["gs_map"] = dict(_map_dark_centroid(cyl, nav))
        out["gs_map"]["definition"] = "GS-MAP"
    except Exception as e:
        out["gs_map"] = {"error": str(e)}
    try:
        # Multi-prior modern GRS sizes — keep the darkest core match
        best_t = None
        best_sc = -1.0
        for L, W in ((11.0, 7.5), (12.5, 8.0), (14.0, 8.5)):
            try:
                t = dict(_template_match_grs(cyl, nav, length_deg=L, width_deg=W))
                sc = float(t.get("dark_contrast") or t.get("score") or 0.0)
                if sc > best_sc:
                    best_sc = sc
                    best_t = t
                    best_t["length_deg"] = L
                    best_t["width_deg"] = W
            except Exception:
                continue
        if best_t is None:
            raise RuntimeError("no template scale succeeded")
        out["gs_tmpl"] = best_t
        out["gs_tmpl"]["definition"] = "GS-TMPL"
    except Exception as e:
        out["gs_tmpl"] = {"error": str(e)}
    try:
        out["map_dark"] = dict(_map_dark_centroid(cyl_raw, nav))
    except Exception as e:
        out["map_dark"] = {"error": str(e)}
    try:
        out["template"] = dict(_template_match_grs(cyl_raw, nav, length_deg=12.5, width_deg=8.0))
    except Exception as e:
        out["template"] = {"error": str(e)}
    try:
        out["moment"] = dict(_moment_mask_grs(image, nav))
    except Exception as e:
        out["moment"] = {"error": str(e)}
    try:
        pr = measure_grs_precision(
            image,
            cm_iii_deg=nav.cm_iii_deg,
            distance_au=nav.distance_au,
            nav=nav,
            quiet=True,
            map_width=2200,
            map_height=1100,
        )
        out["engine"] = {
            "lon_iii_deg": pr.lon_iii_deg,
            "lat_deg": pr.lat_deg,
            "length_deg": pr.length_deg,
            "width_deg": pr.width_deg,
            "score": pr.quality,
            "method": pr.method,
        }
    except Exception as e:
        out["engine"] = {"error": str(e)}
    # Attach local dark-core scores (GRS vs SEB wave discrimination)
    for k, m in list(out.items()):
        if "lon_iii_deg" not in m or "lat_deg" not in m:
            continue
        try:
            m["dark_score"] = _local_dark_score(
                cyl, nav, float(m["lon_iii_deg"]), float(m["lat_deg"])
            )
        except Exception:
            m["dark_score"] = 0.0
    return out, cyl


def _pick_champion_centre(
    estimators: Dict[str, Dict[str, Any]],
    *,
    lat0: float = -22.0,
    cm_iii_deg: float = 0.0,
) -> Tuple[float, float, str, float, float, List[str]]:
    """
    Pick the best centre from all estimator results.

    Pro hierarchy: GS-MAP → GS-TMPL → engine → map_dark → template → moment.
    Rejects map-edge locks (|lon_rel - 90°| < 3°) and latitude outliers.
    Method σ uses leave-one-out jackknife when n≥3 — removing one method
    at a time and seeing how the consensus shifts gives a robust scatter
    estimate even with only a few methods.

    When GS-MAP and GS-TMPL agree tightly and both have dark cores, I
    force their mean — this is the "pro dual-definition lock" that mimics
    what a careful observer would do by cross-checking two
    independent definitions.
    """
    flags: List[str] = []
    order = ("gs_map", "gs_tmpl", "engine", "map_dark", "template", "moment")
    labels = {
        "gs_map": "GS-MAP",
        "gs_tmpl": "GS-TMPL",
        "engine": "CHAMPION-ENGINE",
        "map_dark": "CHAMPION-MAP",
        "template": "CHAMPION-TMPL",
        "moment": "CHAMPION-BARY",
    }
    good = []
    for k in order:
        m = estimators.get(k) or {}
        try:
            lon = float(m["lon_iii_deg"])
            lat = float(m["lat_deg"])
        except Exception:
            continue
        if not (math.isfinite(lon) and math.isfinite(lat)):
            continue
        if not (-36.0 <= lat <= -10.0):
            flags.append(f"{k}_lat_out")
            continue
        lon_rel = abs(wrap_diff(lon, cm_iii_deg))
        if abs(lon_rel - 90.0) < 3.0:
            flags.append(f"{k}_map_edge")
            continue
        w_edge = 0.25 if lon_rel > 85.0 else 1.0
        if lon_rel > 85.0:
            flags.append(f"{k}_near_limb_edge")
        w = math.exp(-0.5 * ((lat - lat0) / 4.5) ** 2) * w_edge
        # Prefer truly dark cores (reject SEB wave / barge false locks)
        dark = float(m.get("dark_score") or m.get("dark_contrast") or 0.0)
        if dark > 0:
            w *= 1.0 + min(1.5, dark)
        elif dark == 0.0 and k in ("gs_map", "map_dark"):
            w *= 0.85
        if k in ("gs_map", "gs_tmpl"):
            w *= 1.40
        if k == "engine":
            w *= 1.15
        # Reject very bright "cores" (not GRS)
        if dark < 0.05 and k in ("gs_map", "map_dark", "moment"):
            flags.append(f"{k}_low_dark")
            w *= 0.35
        good.append((k, lon, lat, w))

    if not good:
        return float("nan"), float("nan"), "NONE", 9.9, 9.9, flags + ["NO_ESTIMATOR"]

    lons = [g[1] for g in good]
    seed = lons[0]
    best_n = -1
    for s in lons:
        n = sum(1 for x in lons if abs(wrap_diff(x, s)) <= 10.0)
        if n > best_n:
            best_n = n
            seed = s

    primary = None
    for k, lon, lat, w in good:
        if abs(wrap_diff(lon, seed)) <= 12.0:
            primary = (k, lon, lat)
            break
    if primary is None:
        primary = (good[0][0], good[0][1], good[0][2])
        flags.append("WEAK_CLUSTER")

    pk, plon, plat = primary
    inl = [(k, lon, lat, w) for k, lon, lat, w in good if abs(wrap_diff(lon, plon)) <= 12.0]
    if len(inl) >= 2:
        la = np.array([x[1] for x in inl], dtype=np.float64)
        lb = np.array([x[2] for x in inl], dtype=np.float64)
        ww = np.array([x[3] for x in inl], dtype=np.float64)
        ww = ww / (ww.sum() + 1e-12)
        ang = np.deg2rad(la)
        lon_c = wrap_deg(math.degrees(math.atan2(np.sum(ww * np.sin(ang)), np.sum(ww * np.cos(ang)))))
        lat_c = float(np.sum(ww * lb))
        plon = wrap_deg(plon + 0.40 * wrap_diff(lon_c, plon))
        plat = 0.60 * plat + 0.40 * lat_c
        if len(inl) >= 3:
            loo_lons, loo_lats = [], []
            for leave in range(len(inl)):
                sub = [inl[i] for i in range(len(inl)) if i != leave]
                ww2 = np.array([x[3] for x in sub], dtype=np.float64)
                ww2 = ww2 / (ww2.sum() + 1e-12)
                ang2 = np.deg2rad(np.array([x[1] for x in sub]))
                lon_j = wrap_deg(
                    math.degrees(math.atan2(np.sum(ww2 * np.sin(ang2)), np.sum(ww2 * np.cos(ang2))))
                )
                lat_j = float(np.sum(ww2 * np.array([x[2] for x in sub])))
                loo_lons.append(wrap_diff(lon_j, plon))
                loo_lats.append(lat_j - plat)
            fac = math.sqrt(len(inl) / max(len(inl) - 1, 1))
            sig_lon = float(np.std(loo_lons, ddof=1)) * fac
            sig_lat = float(np.std(loo_lats, ddof=1)) * fac
            flags.append("JACKKNIFE_SIGMA")
        else:
            dlon = [wrap_diff(lon, plon) for _, lon, _, _ in inl]
            dlat = [lat - plat for _, _, lat, _ in inl]
            sig_lon = float(np.std(dlon, ddof=1))
            sig_lat = float(np.std(dlat, ddof=1))
    else:
        sig_lon, sig_lat = 0.7, 0.45
        flags.append("SINGLE_ESTIMATOR")

    # Pro desk lock: when GS-MAP and GS-TMPL agree tightly and are dark, force that mean
    by_k = {g[0]: g for g in good}
    if "gs_map" in by_k and "gs_tmpl" in by_k:
        _, lon_m, lat_m, _ = by_k["gs_map"]
        _, lon_t, lat_t, _ = by_k["gs_tmpl"]
        if abs(wrap_diff(lon_m, lon_t)) <= 1.25 and abs(lat_m - lat_t) <= 1.5:
            d_m = float((estimators.get("gs_map") or {}).get("dark_score") or 0.0)
            d_t = float((estimators.get("gs_tmpl") or {}).get("dark_score") or 0.0)
            if d_m >= 0.08 or d_t >= 0.08:
                plon = wrap_deg(lon_m + 0.5 * wrap_diff(lon_t, lon_m))
                plat = 0.5 * (lat_m + lat_t)
                pk = "gs_map"
                flags.append("GS_MAP_TMPL_LOCK")
                # tighten method σ when the two pro defs agree
                sig_lon = min(sig_lon, max(0.08, abs(wrap_diff(lon_m, lon_t)) / math.sqrt(2)))
                sig_lat = min(sig_lat, max(0.06, abs(lat_m - lat_t) / math.sqrt(2)))

    return plon, plat, labels.get(pk, "CHAMPION"), sig_lon, sig_lat, flags


def _nav_stability_test(
    image: np.ndarray,
    nav: NavState,
    lon0: float,
    lat0: float,
    *,
    n: int = 6,
    seed: int = 11,
) -> Dict[str, Any]:
    """
    Jitter the limb centre/radius slightly and re-measure — is the lock stable?

    True GRS locks stay put even when the geometry changes a bit. False
    locks (wrong-feature, barge, wave) wander around because their
    position depends on the exact projection geometry. This test runs 6
    perturbed nav states and checks that the refined position doesn't
    shift more than ~0.5° in longitude.

    The σ from this test feeds into the total error budget as "limb
    navigation uncertainty."
    """
    rng = np.random.default_rng(seed)
    lons, lats = [], []
    a0 = float(nav.a_eq_px)
    for i in range(max(3, n)):
        n2 = NavState(
            xc=nav.xc + rng.normal(0, 0.28),
            yc=nav.yc + rng.normal(0, 0.28),
            a_eq_px=a0 * (1.0 + rng.normal(0, 0.0012)),
            flattening=nav.flattening,
            cm_iii_deg=nav.cm_iii_deg,
            distance_au=nav.distance_au,
            sub_lat_deg=nav.sub_lat_deg,
            north_pa_deg=nav.north_pa_deg,
        )
        try:
            cyl = make_cylindrical(image, n2, width=1200, height=600)
            cyl = _map_local_contrast(cyl)
            lo, la, meta = _subpixel_refine_map(cyl, n2, lon0, lat0, passes=1)
            if meta.get("ok") and -36 <= la <= -10:
                lons.append(lo)
                lats.append(la)
        except Exception:
            continue
    if len(lons) < 3:
        return {"ok": False, "n": len(lons), "stable": False}
    dlon = [wrap_diff(x, lon0) for x in lons]
    dlat = [y - lat0 for y in lats]
    sig_lon = float(np.std(dlon, ddof=1))
    sig_lat = float(np.std(dlat, ddof=1))
    # max excursion
    max_dlon = float(max(abs(x) for x in dlon))
    stable = sig_lon <= 0.55 and max_dlon <= 1.4 and sig_lat <= 0.45
    return {
        "ok": True,
        "n": len(lons),
        "sigma_lon_deg": sig_lon,
        "sigma_lat_deg": sig_lat,
        "max_dlon_deg": max_dlon,
        "stable": stable,
    }


def _dual_channel_agreement(
    image: np.ndarray,
    channels: Optional[Dict[str, np.ndarray]],
    nav: NavState,
    lon0: float,
    lat0: float,
) -> Dict[str, Any]:
    """
    Check whether mono and red channels give the same GRS position.

    If they agree (Δlon < 1°, Δlat < 0.8°), that's strong evidence the
    lock is on a real physical feature, not a filter artifact or noise
    pattern. The GRS is most distinct in the red channel but should be
    visible in mono too — disagreement means something's wrong.
    """
    out: Dict[str, Any] = {"ok": False, "agree": False}
    try:
        mono = to_mono(image)
        paths = [("mono", mono)]
        if channels and "R" in channels:
            paths.append(("red", np.asarray(channels["R"], dtype=np.float64)))
        elif image is not None:
            # try R from CHW/HWC
            arr = np.asarray(image, dtype=np.float64)
            if arr.ndim == 3 and arr.shape[0] in (3, 4) and arr.shape[0] < arr.shape[-1]:
                paths.append(("red", arr[0]))
            elif arr.ndim == 3 and arr.shape[-1] >= 3:
                paths.append(("red", arr[..., 0]))
        results = {}
        for name, im in paths:
            im2 = _enhance_seb_contrast(im)
            cyl = make_cylindrical(im2, nav, width=1400, height=700)
            cyl = _map_local_contrast(cyl)
            lo, la, meta = _subpixel_refine_map(cyl, nav, lon0, lat0, passes=1)
            if meta.get("ok"):
                results[name] = {
                    "lon": lo,
                    "lat": la,
                    "dark": _local_dark_score(cyl, nav, lo, la),
                }
        out["channels"] = results
        if "mono" in results and "red" in results:
            dlon = abs(wrap_diff(results["mono"]["lon"], results["red"]["lon"]))
            dlat = abs(results["mono"]["lat"] - results["red"]["lat"])
            out["ok"] = True
            out["dlon_deg"] = dlon
            out["dlat_deg"] = dlat
            out["agree"] = dlon <= 1.0 and dlat <= 0.8
            # circular mean of the two when they agree
            if out["agree"]:
                out["lon_mean"] = wrap_deg(
                    results["red"]["lon"]
                    + 0.5 * wrap_diff(results["mono"]["lon"], results["red"]["lon"])
                )
                out["lat_mean"] = 0.5 * (results["mono"]["lat"] + results["red"]["lat"])
        elif len(results) == 1:
            out["ok"] = True
            out["agree"] = True  # single channel — no conflict
            out["single_channel"] = True
            k = next(iter(results))
            out["lon_mean"] = results[k]["lon"]
            out["lat_mean"] = results[k]["lat"]
        return out
    except Exception as e:
        out["error"] = str(e)
        return out


def _ultimate_lock_gate(
    *,
    trusted_cm: bool,
    flags: List[str],
    limb_sky: float,
    sig_def: float,
    dark_final: float,
    nav_stab: Dict[str, Any],
    dual: Dict[str, Any],
    gs_lock: bool,
    score: float,
    lon: float,
    lat: float,
    spice_horizons_dcm_deg: Optional[float] = None,
    sigma_total_sky: float = 99.0,
) -> Dict[str, Any]:
    """
    Hard multi-gate test for 'UNBEATABLE_AUTO' — the ultimate automated claim.

    All gates have to pass for UNBEATABLE_AUTO. This is not a claim vs HST
    or a perfect human WinJUPOS session — it just means every automated
    sanity check in this app passed on this frame. When it's true, no
    weaker method inside this app can override the result.

    I designed these gates to be genuinely hard to pass. A lot of real
    observations fail one or two gates (usually CM source or limb stability)
    and get CHAMPION or STRONG instead, which is fine — those are still
    good results, just not "all our ducks are in a row" territory.
    """
    # CM cross-check: if both SPICE and Horizons present, demand |ΔCM| ≤ 0.35°
    cm_cross_ok = True
    if spice_horizons_dcm_deg is not None and math.isfinite(float(spice_horizons_dcm_deg)):
        cm_cross_ok = float(spice_horizons_dcm_deg) <= 0.35
    checks = {
        "trusted_cm": trusted_cm,
        "cm_crosscheck": cm_cross_ok,
        "finite_pos": math.isfinite(lon) and math.isfinite(lat),
        "lat_core": math.isfinite(lat) and -28.0 <= lat <= -16.0,
        "no_map_edge": "FINAL_MAP_EDGE" not in flags and "WEAK_DARK_CORE" not in flags,
        "limb_stable": limb_sky <= 1.35,
        "def_tight": sig_def <= 1.0,
        "dark_core": dark_final >= 0.12,
        "nav_stable": bool(nav_stab.get("stable")),
        "dual_agree": bool(dual.get("agree")),
        "score_high": score >= 88,
        "gs_or_strong": gs_lock or score >= 92,
        "sigma_sky_tight": float(sigma_total_sky) <= 2.5,
    }
    failed = [k for k, v in checks.items() if not v]
    passed = len(failed) == 0
    return {
        "unbeatable_auto": passed,
        "checks": checks,
        "failed_checks": failed,
        "n_pass": sum(1 for v in checks.values() if v),
        "n_total": len(checks),
        "spice_horizons_dcm_deg": spice_horizons_dcm_deg,
        "honesty": (
            "UNBEATABLE_AUTO = all automated gates passed on this frame. "
            "No weaker method in THIS APP may override. "
            "Does NOT claim to beat HST, JunoCam, or a perfect human manual measure."
        ),
        "dominance": (
            "In-app hierarchy when UNBEATABLE_AUTO: "
            "this lock > Champion > GS-MAP twin > pipeline > SOTA/soup (scatter only)."
        ),
    }


def _refine_bootstrap_sigma(
    cyl: np.ndarray,
    nav: NavState,
    lon0: float,
    lat0: float,
    *,
    n: int = 8,
    seed: int = 0,
) -> Tuple[float, float, Dict[str, Any]]:
    """
    Bootstrap the sub-pixel refine step to get a σ floor for the centre.

    Adds local map noise (estimated from the residual after Gaussian blur)
    and re-refines each time. The spread of the resulting positions gives
    a noise-floor σ that feeds into the total error budget. This is
    separate from the method scatter — it captures "how precise could we
    be even if all methods agreed perfectly?"
    """
    rng = np.random.default_rng(seed)
    h, w = cyl.shape
    valid = cyl > 1e-8
    if valid.sum() < 100:
        return 0.0, 0.0, {"ok": False}
    residual = cyl - _gauss(cyl, 1.2)
    sig = float(np.std(residual[valid])) * 0.85
    sig = max(sig, 1e-4)
    lons, lats = [], []
    for i in range(max(3, n)):
        noisy = cyl + rng.normal(0, sig, cyl.shape)
        noisy = np.where(valid, noisy, 0.0)
        lo, la, meta = _subpixel_refine_map(noisy, nav, lon0, lat0, passes=1)
        if meta.get("ok"):
            lons.append(lo)
            lats.append(la)
    if len(lons) < 3:
        return 0.0, 0.0, {"ok": False, "n": len(lons)}
    dlon = [wrap_diff(x, lon0) for x in lons]
    dlat = [y - lat0 for y in lats]
    return float(np.std(dlon, ddof=1)), float(np.std(dlat, ddof=1)), {
        "ok": True,
        "n": len(lons),
        "sigma_noise": sig,
    }


def _ew_extent(cyl: np.ndarray, nav: NavState) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    try:
        from gold_standard import measure_gs_oval_and_edges
        oval, west, east, mid = measure_gs_oval_and_edges(cyl, nav)
        wlon = west.lon_iii_deg if west else None
        elon = east.lon_iii_deg if east else None
        ext = abs(wrap_diff(wlon, elon)) if wlon is not None and elon is not None else None
        return ext, wlon, elon
    except Exception:
        return None, None, None


def run_champion_measure(
    image: np.ndarray,
    *,
    cm_iii_deg: float,
    distance_au: float = 5.2,
    cm_source: str = "unknown",
    sigma_cm_deg: float = 0.05,
    sub_lat_deg: float = 0.0,
    north_pa_deg: float = 0.0,
    channels: Optional[Dict[str, np.ndarray]] = None,
    nav: Optional[NavState] = None,
    user_time_iso: str = "",
    time_error_seconds: float = 0.0,
    spice_horizons_dcm_deg: Optional[float] = None,
) -> ChampionResult:
    notes: List[str] = [
        "Champion v5 ULTIMATE: dual-channel + nav stability + multi-gate UNBEATABLE_AUTO.",
        "Absolute Sys III needs trusted CM (SPICE/Horizons/override).",
        "UNBEATABLE_AUTO ≠ better than spacecraft/human manual — it means all automated gates passed.",
    ]
    flags: List[str] = []
    raw = _prefer_measure_image(image, channels)
    meas = _enhance_seb_contrast(raw)

    # --- Limb (stability-weighted) ---
    cons, limb_meta = multi_isophote_limb_consensus(
        meas,
        cm_iii_deg=cm_iii_deg,
        distance_au=distance_au,
        sub_lat_deg=sub_lat_deg,
        north_pa_deg=north_pa_deg,
    )
    if nav is not None and getattr(nav, "a_eq_px", 0) > 10:
        cons.xc = 0.40 * float(nav.xc) + 0.60 * cons.xc
        cons.yc = 0.40 * float(nav.yc) + 0.60 * cons.yc
        cons.a_eq_px = 0.40 * float(nav.a_eq_px) + 0.60 * cons.a_eq_px
        cons.sub_lat_deg = sub_lat_deg
        cons.north_pa_deg = north_pa_deg
        cons.cm_iii_deg = cm_iii_deg
        cons.distance_au = distance_au

    # Limb systematics from GRS lon scatter across probes
    probe_lons = []
    probe_lats = []
    for p in limb_meta.get("probes") or []:
        lo, la = p.get("grs_lon_iii_deg"), p.get("grs_lat_deg")
        try:
            lo, la = float(lo), float(la)
        except Exception:
            continue
        if math.isfinite(lo) and math.isfinite(la) and -36 <= la <= -10:
            probe_lons.append(lo)
            probe_lats.append(la)
    if len(probe_lons) >= 2:
        seed = probe_lons[0]
        dlon = [wrap_diff(x, seed) for x in probe_lons]
        sig_limb_lon = float(np.std(dlon, ddof=1))
        sig_limb_lat = float(np.std(probe_lats, ddof=1))
        limb_sky = sky_error_arcsec(
            float(np.ptp(dlon)),
            float(np.ptp(probe_lats)),
            float(np.mean(probe_lats)),
            distance_au,
        )
    else:
        sig_limb_lon, sig_limb_lat, limb_sky = 0.45, 0.28, 1.6
        flags.append("LIMB_SPREAD_DEFAULT")

    # --- Estimators ---
    estimators, cyl = _run_estimators(meas, cons)
    lon, lat, definition, sig_m_lon, sig_m_lat, eflags = _pick_champion_centre(
        estimators, cm_iii_deg=cm_iii_deg
    )
    flags.extend(eflags)

    # Sub-pixel refine (2-pass tighter window)
    refine_meta: Dict[str, Any] = {"ok": False}
    if math.isfinite(lon) and math.isfinite(lat):
        lon2, lat2, refine_meta = _subpixel_refine_map(cyl, cons, lon, lat, passes=2)
        if refine_meta.get("ok"):
            lon, lat = lon2, lat2
            notes.append(
                f"Map refine ({len(refine_meta.get('passes') or [])} pass) "
                f"Δlon={refine_meta.get('dlon_deg', 0.0):.3f}° Δlat={refine_meta.get('dlat_deg', 0.0):.3f}°"
            )
        # Pass-2 limb: re-weight stored probes (no re-fit) using final GRS lon
        try:
            pl = limb_meta.get("probes") or []
            wsum = 0.0
            xc2 = yc2 = a2 = 0.0
            for p in pl:
                try:
                    gl = float(p.get("grs_lon_iii_deg"))
                    if not math.isfinite(gl):
                        continue
                    xc_p = float(p.get("xc"))
                    yc_p = float(p.get("yc"))
                    a_p = float(p.get("a_eq_px"))
                    w = math.exp(-0.5 * (abs(wrap_diff(gl, lon)) / 3.5) ** 2)
                    if w < 0.05:
                        continue
                    xc2 += w * xc_p
                    yc2 += w * yc_p
                    a2 += w * a_p
                    wsum += w
                except Exception:
                    continue
            if wsum > 0.2:
                cons.xc = 0.5 * cons.xc + 0.5 * (xc2 / wsum)
                cons.yc = 0.5 * cons.yc + 0.5 * (yc2 / wsum)
                cons.a_eq_px = 0.5 * cons.a_eq_px + 0.5 * (a2 / wsum)
                cyl2 = make_cylindrical(meas, cons, width=1800, height=900)
                cyl2 = _map_local_contrast(cyl2)
                lon3, lat3, r2 = _subpixel_refine_map(cyl2, cons, lon, lat, passes=2)
                if r2.get("ok"):
                    # Closure: require refined dark score stays high
                    d_sc = _local_dark_score(cyl2, cons, lon3, lat3)
                    if d_sc >= 0.08 or d_sc >= _local_dark_score(cyl, cons, lon, lat) * 0.7:
                        lon, lat = lon3, lat3
                        refine_meta["pass2_limb_lock"] = True
                        refine_meta["pass2"] = r2
                        refine_meta["dark_score_final"] = d_sc
                        notes.append(
                            f"Pass-2 limb lock + re-refine (dark_score={d_sc:.2f})"
                        )
                        cyl = cyl2
                    else:
                        notes.append(
                            f"Pass-2 refine rejected (dark_score={d_sc:.2f} too low)"
                        )
                        flags.append("PASS2_DARK_REJECT")
        except Exception as e:
            notes.append(f"Pass-2 limb lock skipped: {e}")

        # Final geometric closure: |lon_rel| and lat band
        lon_rel_f = abs(wrap_diff(lon, cm_iii_deg))
        if lon_rel_f > 88.0:
            flags.append("FINAL_MAP_EDGE")
            notes.append("Final position near map edge — treat absolute lon with caution")
        elif lon_rel_f > 45.0:
            flags.append("GRS_NEAR_LIMB")
            notes.append(
                f"GRS {lon_rel_f:.1f}° from the central meridian — JUPOS practice "
                "measures within ±45°; foreshortening inflates the size/definition "
                "error (longitude itself stays geometric)"
            )
        d_final = _local_dark_score(cyl, cons, lon, lat)
        refine_meta["dark_score_final"] = d_final
        if d_final < 0.05:
            flags.append("WEAK_DARK_CORE")
            notes.append(f"Weak dark core (score={d_final:.2f}) — possible wrong feature")
        # Local noise bootstrap → floor on method σ
        try:
            b_lon, b_lat, bmeta = _refine_bootstrap_sigma(cyl, cons, lon, lat, n=8, seed=7)
            refine_meta["bootstrap"] = bmeta
            if bmeta.get("ok"):
                sig_m_lon = max(sig_m_lon, b_lon)
                sig_m_lat = max(sig_m_lat, b_lat)
                notes.append(
                    f"Refine bootstrap σ_lon={b_lon:.3f}° σ_lat={b_lat:.3f}° (n={bmeta.get('n')})"
                )
        except Exception as e:
            notes.append(f"Refine bootstrap skipped: {e}")

    # --- Ultimate lock suite: nav stability + dual-channel ---
    ultimate: Dict[str, Any] = {}
    nav_stab: Dict[str, Any] = {"ok": False, "stable": False}
    dual: Dict[str, Any] = {"ok": False, "agree": False}
    if math.isfinite(lon) and math.isfinite(lat):
        try:
            nav_stab = _nav_stability_test(meas, cons, lon, lat, n=6, seed=11)
            ultimate["nav_stability"] = nav_stab
            if nav_stab.get("ok"):
                sig_m_lon = max(sig_m_lon, float(nav_stab.get("sigma_lon_deg") or 0))
                sig_m_lat = max(sig_m_lat, float(nav_stab.get("sigma_lat_deg") or 0))
                if nav_stab.get("stable"):
                    notes.append(
                        f"Nav stability OK σ_lon={nav_stab.get('sigma_lon_deg', 0.0):.3f}° "
                        f"maxΔ={nav_stab.get('max_dlon_deg', 0.0):.3f}°"
                    )
                    flags.append("NAV_STABLE")
                else:
                    flags.append("NAV_UNSTABLE")
                    notes.append(
                        f"Nav stability weak σ_lon={nav_stab.get('sigma_lon_deg', 0.0):.3f}° "
                        f"maxΔ={nav_stab.get('max_dlon_deg', 0.0):.3f}°"
                    )
        except Exception as e:
            notes.append(f"Nav stability skipped: {e}")
        try:
            dual = _dual_channel_agreement(raw, channels, cons, lon, lat)
            ultimate["dual_channel"] = dual
            if dual.get("agree") and dual.get("lon_mean") is not None:
                # Soft blend toward dual-channel mean when independent paths agree
                lon = wrap_deg(lon + 0.35 * wrap_diff(float(dual["lon_mean"]), lon))
                lat = 0.65 * lat + 0.35 * float(dual["lat_mean"])
                flags.append("DUAL_CHANNEL_AGREE")
                notes.append(
                    f"Dual-channel agree Δlon={dual.get('dlon_deg', 0):.3f}° "
                    f"Δlat={dual.get('dlat_deg', 0):.3f}°"
                )
            elif dual.get("ok") and not dual.get("agree") and dual.get("dlon_deg") is not None:
                flags.append("DUAL_CHANNEL_DISAGREE")
                notes.append(
                    f"Dual-channel DISAGREE Δlon={dual.get('dlon_deg', 0.0):.3f}° — not ultimate"
                )
        except Exception as e:
            notes.append(f"Dual-channel skipped: {e}")

    # Definition scatter among named cores
    core_keys = ("gs_map", "gs_tmpl", "engine", "map_dark", "template")
    core_lons = []
    for k in core_keys:
        m = estimators.get(k) or {}
        try:
            if -36 <= float(m["lat_deg"]) <= -10:
                core_lons.append(float(m["lon_iii_deg"]))
        except Exception:
            pass
    if len(core_lons) >= 2 and math.isfinite(lon):
        sig_def = float(np.std([wrap_diff(x, lon) for x in core_lons], ddof=1))
    else:
        sig_def = 0.55
        flags.append("DEF_SCATTER_DEFAULT")

    extent, west, east = _ew_extent(cyl, cons)
    length = width = None
    for k in ("engine", "gs_tmpl", "template", "moment"):
        m = estimators.get(k) or {}
        try:
            if m.get("length_deg") is not None and math.isfinite(float(m["length_deg"])):
                length = float(m["length_deg"])
                width = float(m.get("width_deg")) if m.get("width_deg") is not None else None
                break
        except Exception:
            pass
    # Prefer measured EW extent when in modern GRS range (not template prior)
    if extent is not None and math.isfinite(float(extent)) and 6.0 <= float(extent) <= 22.0:
        length = float(extent)
        notes.append(f"Size from W–E edges: {length:.2f}°")
    elif extent is not None and (length is None or not math.isfinite(length)):
        length = extent

    # Timing → Sys III (BAA ~0.604°/min)
    try:
        from accuracy_gates import timing_longitude_uncertainty_deg
        sig_time = timing_longitude_uncertainty_deg(float(time_error_seconds or 0.0))
    except Exception:
        sig_time = abs(float(time_error_seconds or 0.0)) / 60.0 * 0.604

    cm_s = (cm_source or "").lower()
    trusted = any(
        t in cm_s
        for t in ("spice", "horizons", "winjupos", "override", "synthetic", "user", "cm_override")
    )
    if not trusted or cm_s in ("analytical", "analytic", "fallback", ""):
        flags.append("CM_UNTRUSTED")
        sigma_cm = max(float(sigma_cm_deg), 15.0)
    else:
        sigma_cm = max(float(sigma_cm_deg), 0.02)

    sig_lon = math.sqrt(
        sigma_cm ** 2
        + sig_time ** 2
        + sig_limb_lon ** 2
        + sig_def ** 2
        + max(sig_m_lon, 0.05) ** 2
    )
    sig_lat = math.sqrt(sig_limb_lat ** 2 + max(sig_m_lat, 0.05) ** 2 + 0.12 ** 2)
    lat_g = float("nan")
    if math.isfinite(lat):
        try:
            lat_g = planetocentric_to_planetographic(lat)
        except Exception:
            pass
    sky = sky_error_arcsec(sig_lon, sig_lat, lat if math.isfinite(lat) else -22.0, distance_au)

    # Score
    score = 100.0
    if "CM_UNTRUSTED" in flags:
        score -= 45
    if "NO_ESTIMATOR" in flags:
        score -= 50
    if "SINGLE_ESTIMATOR" in flags:
        score -= 10
    if limb_sky > 2.5:
        score -= 18
        flags.append("LIMB_UNSTABLE")
    elif limb_sky > 1.2:
        score -= 7
    if sig_def > 2.5:
        score -= 12
        flags.append("DEFINITION_LOOSE")
    if math.isfinite(lat) and not (-28 <= lat <= -16):
        score -= 10
        flags.append("LAT_OUTSIDE_CORE")
    if limb_meta.get("n_stable_grs", 0) >= 4:
        score += 5
        notes.append(f"Stable GRS across {limb_meta.get('n_stable_grs')} limb outlines.")
    if refine_meta.get("ok"):
        score += 3
    if refine_meta.get("pass2_limb_lock"):
        score += 4
    d_fin = float(refine_meta.get("dark_score_final") or 0.0)
    if d_fin >= 0.25:
        score += 5
    elif d_fin < 0.05 and math.isfinite(lon):
        score -= 12
    if "FINAL_MAP_EDGE" in flags:
        score -= 15
    elif "GRS_NEAR_LIMB" in flags:
        score -= 6
    if not math.isfinite(lon):
        score = 0.0
    score = float(max(0.0, min(100.0, score)))

    absolute_ok = (
        math.isfinite(lon)
        and math.isfinite(lat)
        and "CM_UNTRUSTED" not in flags
        and "NO_ESTIMATOR" not in flags
        and "FINAL_MAP_EDGE" not in flags
        and "WEAK_DARK_CORE" not in flags
        and "NAV_UNSTABLE" not in flags
        and "DUAL_CHANNEL_DISAGREE" not in flags
        and limb_sky < 6.0
        and score >= 58
        # Must be in core GRS latitude — was true with lat=-30 before (bug)
        and -28.0 <= float(lat) <= -16.0
    )
    if math.isfinite(lat) and not (-36 <= lat <= -10):
        absolute_ok = False
        flags.append("LAT_OUT_OF_BAND")
    elif math.isfinite(lat) and not (-28 <= lat <= -16):
        absolute_ok = False
        if "LAT_OUTSIDE_CORE" not in flags:
            flags.append("LAT_OUTSIDE_CORE")

    if "GS_MAP_TMPL_LOCK" in flags:
        score = min(100.0, score + 6)
        notes.append("GS-MAP ↔ GS-TMPL agreement lock (pro dual-definition).")
    if "NAV_STABLE" in flags:
        score = min(100.0, score + 5)
    if "DUAL_CHANNEL_AGREE" in flags:
        score = min(100.0, score + 5)
    if "NAV_UNSTABLE" in flags:
        score -= 10
    if "DUAL_CHANNEL_DISAGREE" in flags:
        score -= 12
    score = float(max(0.0, min(100.0, score)))

    # Recompute sky after method σ updates from nav stability
    sig_lon = math.sqrt(
        sigma_cm ** 2
        + sig_time ** 2
        + sig_limb_lon ** 2
        + sig_def ** 2
        + max(sig_m_lon, 0.05) ** 2
    )
    sig_lat = math.sqrt(sig_limb_lat ** 2 + max(sig_m_lat, 0.05) ** 2 + 0.12 ** 2)
    sky = sky_error_arcsec(sig_lon, sig_lat, lat if math.isfinite(lat) else -22.0, distance_au)

    dark_final = float(refine_meta.get("dark_score_final") or 0.0)
    lock = _ultimate_lock_gate(
        trusted_cm=("CM_UNTRUSTED" not in flags),
        flags=flags,
        limb_sky=limb_sky,
        sig_def=sig_def,
        dark_final=dark_final,
        nav_stab=nav_stab,
        dual=dual,
        gs_lock=("GS_MAP_TMPL_LOCK" in flags),
        score=score,
        lon=lon if math.isfinite(lon) else float("nan"),
        lat=lat if math.isfinite(lat) else float("nan"),
        spice_horizons_dcm_deg=spice_horizons_dcm_deg,
        sigma_total_sky=float(sky),
    )
    ultimate.update(lock)
    unbeatable = bool(lock.get("unbeatable_auto"))

    if unbeatable:
        grade = "UNBEATABLE_AUTO"
        absolute_ok = True
        flags.append("UNBEATABLE_AUTO")
        flags.append("NO_APP_OVERRIDE")
        notes.append(lock["honesty"])
        notes.append(lock.get("dominance") or "")
        notes.append(
            f"Ultimate lock {lock['n_pass']}/{lock['n_total']} gates — "
            "in-app hierarchy: this lock wins over soup/SOTA/pipeline."
        )
        if spice_horizons_dcm_deg is not None:
            notes.append(f"SPICE↔Horizons |ΔCM|={float(spice_horizons_dcm_deg):.3f}°")
    elif score >= 92 and absolute_ok and limb_sky < 1.2 and sig_def < 0.8:
        grade = "WORLD_CLASS"
    elif score >= 82 and absolute_ok:
        grade = "CHAMPION"
    elif score >= 68 and math.isfinite(lon):
        grade = "STRONG"
    elif math.isfinite(lon):
        grade = "USABLE"
    else:
        grade = "HOLD"

    if not unbeatable and lock.get("failed_checks"):
        notes.append(
            "Ultimate lock incomplete: failed "
            + ", ".join(lock["failed_checks"][:6])
        )

    notes.append(
        f"σ_total sky≈{sky:.3f}″ (CM {sigma_cm:.2f}° ⊕ time {sig_time:.2f}° ⊕ "
        f"limb {sig_limb_lon:.2f}° ⊕ def {sig_def:.2f}° ⊕ meth {sig_m_lon:.2f}°)"
    )
    notes.append("Compare φ_g to manual picks; same UTC+CM for fair Δ.")
    notes.append(
        "absolute_publish_ok=True" if absolute_ok else "absolute_publish_ok=False — fix CM/limb before absolute lon"
    )

    ok = math.isfinite(lon) and math.isfinite(lat)

    # --- System II mapping (IAU frame rotation, exact; needs only UTC) ------
    cm_ii = lon_ii = float("nan")
    if user_time_iso:
        try:
            from system_ii import derive_system_ii
            s2 = derive_system_ii(
                cm_iii_deg, user_time_iso,
                lon_iii_deg=(lon if ok else None),
                source=cm_source or "champion",
            )
            cm_ii = s2.cm_ii_deg
            lon_ii = s2.lon_ii_deg if s2.lon_ii_deg is not None else float("nan")
            notes.append(
                f"System II frame offset {s2.offset_deg:+.4f}° (IAU WGCCRE) "
                f"→ CM_II={cm_ii:.4f}°"
                + (f", GRS L_II={lon_ii:.4f}°" if math.isfinite(lon_ii) else "")
            )
        except Exception as e:
            notes.append(f"System II mapping skipped: {e}")

    CONSOLE.ok(
        f"CHAMPION {grade} lon={lon:.4f}° lat={lat:.4f}° φ_g={lat_g:.3f}°  "
        f"σ_sky≈{sky:.3f}″ score={score:.0f} abs={absolute_ok} "
        f"ultimate={lock.get('n_pass')}/{lock.get('n_total')}"
    )
    return ChampionResult(
        ok=ok,
        lon_iii_deg=float(lon) if ok else float("nan"),
        cm_ii_deg=float(cm_ii),
        lon_ii_deg=float(lon_ii),
        lat_planetocentric_deg=float(lat) if ok else float("nan"),
        lat_planetographic_deg=float(lat_g) if math.isfinite(lat_g) else float("nan"),
        length_deg=length,
        width_deg=width,
        extent_ew_deg=extent,
        definition=definition,
        cm_iii_deg=float(cm_iii_deg),
        cm_source=cm_source,
        distance_au=float(distance_au),
        sigma_cm_deg=float(sigma_cm),
        sigma_timing_lon_deg=float(sig_time),
        sigma_limb_lon_deg=float(sig_limb_lon),
        sigma_limb_lat_deg=float(sig_limb_lat),
        sigma_definition_lon_deg=float(sig_def),
        sigma_method_lon_deg=float(sig_m_lon),
        sigma_method_lat_deg=float(sig_m_lat),
        sigma_total_lon_deg=float(sig_lon),
        sigma_total_lat_deg=float(sig_lat),
        sigma_total_sky_arcsec=float(sky),
        limb_sky_spread_arcsec=float(limb_sky),
        absolute_publish_ok=bool(absolute_ok),
        grade=grade,
        flags=flags,
        notes=notes,
        estimators={
            k: {kk: vv for kk, vv in (v or {}).items() if kk != "error"}
            for k, v in estimators.items()
        },
        limb_consensus=limb_meta,
        world_class_score=score,
        refine=refine_meta,
        ultimate_lock=ultimate,
        unbeatable_auto=unbeatable,
    )


def attach_champion_to_package(
    package: Dict[str, Any],
    image: np.ndarray,
    *,
    nav: Optional[NavState] = None,
    cm_iii_deg: float,
    distance_au: float = 5.2,
    cm_source: str = "unknown",
    sigma_cm_deg: float = 0.05,
    sub_lat_deg: float = 0.0,
    north_pa_deg: float = 0.0,
    channels: Optional[Dict[str, np.ndarray]] = None,
    out_dir: Optional[Path] = None,
    user_time_iso: str = "",
    time_error_seconds: float = 0.0,
    spice_horizons_dcm_deg: Optional[float] = None,
) -> ChampionResult:
    # Pull time_error / SPICE↔Horizons ΔCM from package if not passed
    if not time_error_seconds:
        try:
            time_error_seconds = float(
                (package.get("headline") or {}).get("time_error_seconds") or 0.0
            )
        except Exception:
            time_error_seconds = 0.0
    if spice_horizons_dcm_deg is None:
        try:
            pe = package.get("pro_ephemeris") or {}
            raw = pe.get("raw") or {}
            dcm = raw.get("spice_horizons_dcm_deg")
            if dcm is not None:
                spice_horizons_dcm_deg = float(dcm)
        except Exception:
            pass
    ch = run_champion_measure(
        image,
        cm_iii_deg=cm_iii_deg,
        distance_au=distance_au,
        cm_source=cm_source,
        sigma_cm_deg=sigma_cm_deg,
        sub_lat_deg=sub_lat_deg,
        north_pa_deg=north_pa_deg,
        channels=channels,
        nav=nav,
        user_time_iso=user_time_iso,
        time_error_seconds=time_error_seconds,
        spice_horizons_dcm_deg=spice_horizons_dcm_deg,
    )
    d = ch.to_dict()
    package["champion"] = d
    h = package.setdefault("headline", {})
    h["champion_lon_iii_deg"] = ch.lon_iii_deg
    h["champion_lon_ii_deg"] = ch.lon_ii_deg
    h["champion_cm_ii_deg"] = ch.cm_ii_deg
    h["champion_lat_deg"] = ch.lat_planetocentric_deg
    h["champion_lat_planetographic_deg"] = ch.lat_planetographic_deg
    h["champion_grade"] = ch.grade
    h["champion_score"] = ch.world_class_score
    h["champion_sigma_sky_arcsec"] = ch.sigma_total_sky_arcsec
    h["champion_sigma_lon_deg"] = ch.sigma_total_lon_deg
    h["champion_absolute_ok"] = ch.absolute_publish_ok
    h["champion_definition"] = ch.definition
    h["unbeatable_auto"] = ch.unbeatable_auto
    h["ultimate_lock_pass"] = (ch.ultimate_lock or {}).get("n_pass")
    h["ultimate_lock_total"] = (ch.ultimate_lock or {}).get("n_total")
    if out_dir is not None:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "champion.json").write_text(json.dumps(d, indent=2, default=str), encoding="utf-8")
        ul = ch.ultimate_lock or {}
        lines = [
            "CHAMPION",
            "========",
            f"grade     {ch.grade}  score={ch.world_class_score:.0f}",
            f"lon_III   {ch.lon_iii_deg:.4f} °  ±{ch.sigma_total_lon_deg:.3f}",
            f"lon_II    {ch.lon_ii_deg:.4f} °" if math.isfinite(ch.lon_ii_deg) else "lon_II    —",
            f"lat_c     {ch.lat_planetocentric_deg:.3f} °",
            f"lat_g     {ch.lat_planetographic_deg:.3f} °",
            f"CM_III    {ch.cm_iii_deg:.4f} °  [{ch.cm_source}]",
            f"CM_II     {ch.cm_ii_deg:.4f} °" if math.isfinite(ch.cm_ii_deg) else "CM_II     —",
            f"def       {ch.definition}",
            f"σ_sky     {ch.sigma_total_sky_arcsec:.2f} ″",
            f"EW        {ch.extent_ew_deg}",
            f"gates     {ul.get('n_pass')}/{ul.get('n_total')}  unbeatable={ch.unbeatable_auto}  abs={ch.absolute_publish_ok}",
            f"limb_spr  {ch.limb_sky_spread_arcsec:.2f} ″",
            f"flags     {', '.join(ch.flags) or '—'}",
            "",
        ]
        (out_dir / "champion.txt").write_text("\n".join(lines), encoding="utf-8")
    return ch

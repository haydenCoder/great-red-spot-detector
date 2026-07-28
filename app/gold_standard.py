#!/usr/bin/env python3
"""
Professional GRS metrology procedures (ground-based, laptop-replicable).

Goal: replicate *how professionals work* so results approach published / agency-
quality *geometry and measurement discipline* — NOT invent a fake "NASA GRS answer".

Pro stack (what this module implements):
  1) Geometry discipline  — CM III / sub-lat / PA source tagged (WinJUPOS, SPICE, Horizons)
  2) Map-based measure    — cylindrical deprojection then measure (WinJUPOS-like desk)
  3) Fixed definitions    — named gold standards (barycentre, oval fit, W/E edges)
  4) Definition scatter   — systematic floor from incompatible definitions
  5) Optional human check — paste your WinJUPOS manual lon/lat → Δ (validation, not truth)
  6) Export               — notebook / PVOL-style text for archives

WinJUPOS is geometry + measuring desk, NOT an automatic GRS detector.
Horizons/SPICE are Jupiter geometry, NOT official GRS longitude products.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from verbose_log import CONSOLE
from precision_engine import (
    NavState,
    fit_limb_nav,
    make_cylindrical,
    to_mono,
    wrap_deg,
    wrap_diff,
    sky_error_arcsec,
    _moment_mask_grs,
    _template_match_grs,
    _map_dark_centroid,
    measure_grs_precision,
)


# ---------------------------------------------------------------------------
# Named gold-standard definitions (community-style, fixed rules)
# ---------------------------------------------------------------------------

GOLD_DEFINITIONS = {
    "GS-BARY": {
        "name": "GS-BARY",
        "full": "Intensity-weighted dark barycentre in GRS latitude band",
        "role": "primary_automated",
        "notes": "Stable core position; closest automated analog to a careful dark-core pick.",
    },
    "GS-MAP": {
        "name": "GS-MAP",
        "full": "Cylindrical-map dark centroid (WinJUPOS-desk style)",
        "role": "map_primary",
        "notes": "Measure after deprojection — how most careful amateurs work in WinJUPOS.",
    },
    "GS-TMPL": {
        "name": "GS-TMPL",
        "full": "Dark-oval template match on cylindrical map",
        "role": "template",
        "notes": "Explicit size prior; good when oval contrast is clean.",
    },
    "GS-OVAL": {
        "name": "GS-OVAL",
        "full": "Ellipse fit to dark mask (center + length/width)",
        "role": "extent",
        "notes": "Center of fitted ellipse; reports size like published oval dimensions.",
    },
    "GS-EDGE-W": {
        "name": "GS-EDGE-W",
        "full": "West end of dark oval at mid-latitude (System III)",
        "role": "extent_edge",
        "notes": "WinJUPOS-like longitude extent (west). Not a 'center'.",
    },
    "GS-EDGE-E": {
        "name": "GS-EDGE-E",
        "full": "East end of dark oval at mid-latitude (System III)",
        "role": "extent_edge",
        "notes": "WinJUPOS-like longitude extent (east). Not a 'center'.",
    },
    "GS-MID": {
        "name": "GS-MID",
        "full": "Mid-longitude of west/east edges (extent midpoint)",
        "role": "extent_mid",
        "notes": "0.5*(W+E); alternative center when edges are cleaner than the core.",
    },
    "GS-ENGINE": {
        "name": "GS-ENGINE",
        "full": "Multi-method precision engine consensus",
        "role": "ensemble",
        "notes": "Internal pipeline consensus — report alongside a single fixed definition.",
    },
}

# Preferred primary for "pro-style" reported position (one number, named)
PRIMARY_ORDER = ("GS-MAP", "GS-BARY", "GS-TMPL", "GS-OVAL", "GS-MID", "GS-ENGINE")


@dataclass
class GoldMeasure:
    definition_id: str
    lon_iii_deg: float
    lat_deg: float
    length_deg: Optional[float] = None
    width_deg: Optional[float] = None
    weight: float = 1.0
    ok: bool = True
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class GoldStandardResult:
    """Full professional procedure product — methodology first, not a magic answer."""
    ok: bool
    primary_definition: str
    primary_lon_iii_deg: float
    primary_lat_deg: float
    primary_length_deg: Optional[float]
    primary_width_deg: Optional[float]
    # Geometry discipline
    cm_iii_deg: float
    cm_source: str
    distance_au: float
    user_time_iso: str
    # Procedure products
    measures: List[Dict[str, Any]] = field(default_factory=list)
    definition_scatter_lon_deg: float = 0.0
    definition_scatter_lat_deg: float = 0.0
    west_edge_lon_iii_deg: Optional[float] = None
    east_edge_lon_iii_deg: Optional[float] = None
    extent_lon_deg: Optional[float] = None
    # Optional human WinJUPOS validation (not truth)
    winjupos_manual: Optional[Dict[str, Any]] = None
    # Meta
    procedure_steps: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    grade: str = "—"
    elapsed_s: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        am = getattr(self, "_all_methods", None)
        if am is not None:
            d["all_methods"] = am
            d["n_methods_ok"] = am.get("n_ok")
            d["n_methods_total"] = am.get("n_total")
        soup = getattr(self, "_soup_scatter", None)
        if soup:
            d.update(soup)
        return d


def _wrap_lon(lon: float) -> float:
    return float(wrap_deg(lon))


def _cyl_axes(cyl: np.ndarray, nav: NavState) -> Tuple[np.ndarray, np.ndarray]:
    """Return lon_iii grid (W) and planetographic-ish lat grid (H) for cylindrical map.

    MUST match precision_engine.make_cylindrical: lon_rel ∈ [-90°, +90°] about CM
    (visible hemisphere only). Using ±180 was a systematic 2× lon-scale bug on
    GS-OVAL / GS-EDGE / GS-MID / extent products.
    """
    h, w = cyl.shape[:2]
    lon_rel = np.linspace(-90.0, 90.0, w)
    lon_iii = np.array([_wrap_lon(nav.cm_iii_deg + x) for x in lon_rel])
    # Rows: lat from +90 (top) to -90 (bottom) — match make_cylindrical convention
    lat = np.linspace(90.0, -90.0, h)
    return lon_iii, lat


def _grs_band_mask_cyl(cyl: np.ndarray, lat: np.ndarray, lat0: float = -22.0, half: float = 6.0) -> np.ndarray:
    yy = ((lat >= (lat0 - half)) & (lat <= (lat0 + half)))
    return np.broadcast_to(yy[:, None], cyl.shape[:2])


def measure_gs_bary(image: np.ndarray, nav: NavState) -> GoldMeasure:
    mo = _moment_mask_grs(image, nav)
    return GoldMeasure(
        "GS-BARY",
        float(mo["lon_iii_deg"]),
        float(mo["lat_deg"]),
        float(mo.get("length_deg") or 0) or None,
        float(mo.get("width_deg") or 0) or None,
        weight=3.0,
        note=GOLD_DEFINITIONS["GS-BARY"]["full"],
    )


def measure_gs_map(cyl: np.ndarray, nav: NavState) -> GoldMeasure:
    m = _map_dark_centroid(cyl, nav)
    return GoldMeasure(
        "GS-MAP",
        float(m["lon_iii_deg"]),
        float(m["lat_deg"]),
        float(m.get("length_deg") or 0) or None,
        float(m.get("width_deg") or 0) or None,
        weight=3.5,
        note=GOLD_DEFINITIONS["GS-MAP"]["full"],
    )


def measure_gs_tmpl(cyl: np.ndarray, nav: NavState) -> GoldMeasure:
    t = _template_match_grs(cyl, nav)
    return GoldMeasure(
        "GS-TMPL",
        float(t["lon_iii_deg"]),
        float(t["lat_deg"]),
        float(t.get("length_deg") or 0) or None,
        float(t.get("width_deg") or 0) or None,
        weight=3.0,
        note=GOLD_DEFINITIONS["GS-TMPL"]["full"],
    )


def measure_gs_engine(image: np.ndarray, nav: NavState) -> GoldMeasure:
    p = measure_grs_precision(
        to_mono(image),
        cm_iii_deg=nav.cm_iii_deg,
        distance_au=nav.distance_au,
        nav=nav,
        quiet=True,
    )
    return GoldMeasure(
        "GS-ENGINE",
        float(p.lon_iii_deg),
        float(p.lat_deg),
        float(p.length_deg),
        float(p.width_deg),
        weight=2.5,
        note=GOLD_DEFINITIONS["GS-ENGINE"]["full"],
    )


def _dark_mask_cyl(cyl: np.ndarray, lat: np.ndarray, lat0: float = -22.0) -> np.ndarray:
    """
    Binary dark mask in GRS band on cylindrical map.

    Uses band-local darkness (not a loose global percentile) so SEB waves
    and flat residuals do not inflate EW size to tens of degrees.
    """
    im = np.asarray(cyl, dtype=np.float64)
    if im.ndim == 3:
        im = im.mean(axis=2)
    h, w = im.shape[:2]
    band = _grs_band_mask_cyl(im, lat, lat0=lat0, half=6.5)
    try:
        from scipy.ndimage import gaussian_filter
        blur = gaussian_filter(im, sigma=max(2.5, im.shape[1] * 0.015))
    except Exception:
        k = max(3, im.shape[1] // 50)
        kernel = np.ones(k) / k
        tmp = np.apply_along_axis(lambda m: np.convolve(m, kernel, mode="same"), 1, im)
        blur = np.apply_along_axis(lambda m: np.convolve(m, kernel, mode="same"), 0, tmp)
    # Positive = darker than local background
    dark = np.maximum(0.0, blur - im)
    dark[~band] = 0.0
    vals = dark[band]
    if vals.size < 50 or float(vals.max()) <= 0:
        thr = np.percentile(im[band], 8) if band.any() else 0
        cand = band & (im <= thr)
    else:
        # Peak-relative: keep only strong dark core (not entire SEB trough)
        peak = float(np.percentile(vals[vals > 0], 99)) if np.any(vals > 0) else float(vals.max())
        thr = max(0.35 * peak, float(np.percentile(vals, 92)))
        cand = band & (dark >= thr)
    # Max component area ~ 22° × 12° on 180° maps
    max_area = max(80, int((22.0 / 180.0) * (12.0 / 180.0) * h * w * 1.2))
    try:
        from scipy.ndimage import binary_opening, binary_closing, label
        cand = binary_opening(cand, iterations=1)
        cand = binary_closing(cand, iterations=1)
        lab, n = label(cand)
        if n == 0:
            # fallback: darkest percentile core
            thr2 = np.percentile(im[band], 4) if band.any() else 0
            cand = band & (im <= thr2)
            lab, n = label(cand)
        best_i, best_s = 0, -1e99
        for i in range(1, n + 1):
            m = lab == i
            area = int(m.sum())
            if area < 25 or area > max_area:
                continue
            ys, xs = np.where(m)
            cy = float(ys.mean())
            lat_c = float(lat[int(np.clip(round(cy), 0, len(lat) - 1))])
            xspan = float(xs.max() - xs.min() + 1) * (180.0 / w)
            yspan = float(ys.max() - ys.min() + 1) * (180.0 / h)
            if xspan > 26 or yspan > 16:
                continue  # not a modern GRS oval
            mean_dark = float(np.mean(dark[m])) if dark[m].size else 0.0
            compact = area / ((xs.max() - xs.min() + 1) * (ys.max() - ys.min() + 1) + 1e-6)
            score = (
                3.0 * mean_dark
                + math.log(area + 1)
                - 4.0 * abs(lat_c - lat0)
                + 2.5 * compact
            )
            if score > best_s:
                best_s, best_i = score, i
        if best_i:
            return lab == best_i
        # If all rejected by size, take best score ignoring size cap once
        best_i, best_s = 0, -1e99
        for i in range(1, n + 1):
            m = lab == i
            area = int(m.sum())
            if area < 25:
                continue
            ys, xs = np.where(m)
            cy = float(ys.mean())
            lat_c = float(lat[int(np.clip(round(cy), 0, len(lat) - 1))])
            score = math.log(area + 1) - 4.0 * abs(lat_c - lat0)
            if score > best_s:
                best_s, best_i = score, i
        if best_i:
            return lab == best_i
    except Exception:
        pass
    return cand


def measure_gs_oval_and_edges(
    cyl: np.ndarray, nav: NavState
) -> Tuple[Optional[GoldMeasure], Optional[GoldMeasure], Optional[GoldMeasure], Optional[GoldMeasure]]:
    """
    Ellipse-ish center + west/east edges at mid-latitude.
    WinJUPOS-like extent measure (edges are not 'the GRS position' alone).
    """
    h, w = cyl.shape[:2]
    lon_iii, lat = _cyl_axes(cyl, nav)
    mask = _dark_mask_cyl(cyl, lat)
    ys, xs = np.where(mask)
    if len(xs) < 30:
        return None, None, None, None

    # intensity weights (darker = heavier) on mono
    im = np.asarray(cyl, dtype=np.float64)
    if im.ndim == 3:
        im = im.mean(axis=2)
    wts = np.max(im[ys, xs]) - im[ys, xs] + 1e-6
    cx = float(np.average(xs, weights=wts))
    cy = float(np.average(ys, weights=wts))
    # lon/lat of barycentre on map
    ix = int(np.clip(round(cx), 0, w - 1))
    iy = int(np.clip(round(cy), 0, h - 1))
    lon_c = float(lon_iii[ix])
    lat_c = float(lat[iy])

    # Size from intensity-weighted 2nd moments (deg). Map is lon_rel ∈ [-90,+90] → 180°/w.
    # Bounding-box alone overestimates when mask has thin tails; moments match published oval L/W better.
    deg_x = 180.0 / max(w, 1)
    deg_y = 180.0 / max(h, 1)
    x_rel = (xs.astype(np.float64) - cx) * deg_x
    y_rel = (ys.astype(np.float64) - cy) * deg_y
    wts_n = wts / (float(np.sum(wts)) + 1e-12)
    var_x = float(np.sum(wts_n * x_rel ** 2))
    var_y = float(np.sum(wts_n * y_rel ** 2))
    # 4σ diameter ≈ full width of a ~Gaussian dark core (ellipse major/minor)
    x_span = max(4.0 * math.sqrt(max(var_x, 1e-6)), float(xs.max() - xs.min() + 1) * deg_x * 0.55)
    y_span = max(4.0 * math.sqrt(max(var_y, 1e-6)), float(ys.max() - ys.min() + 1) * deg_y * 0.55)
    # Prefer the more conservative of moments vs 55% bbox (limits both inflate and under-fill)
    bbox_x = float(xs.max() - xs.min() + 1) * deg_x
    bbox_y = float(ys.max() - ys.min() + 1) * deg_y
    x_span = float(min(max(x_span, 4.0), bbox_x))
    y_span = float(min(max(y_span, 3.0), bbox_y))
    # Physical sanity for modern GRS (Simon-era ~10–17° EW)
    x_span = float(np.clip(x_span, 4.0, 28.0))
    y_span = float(np.clip(y_span, 3.0, 16.0))
    oval = GoldMeasure(
        "GS-OVAL",
        lon_c,
        lat_c,
        length_deg=x_span,
        width_deg=y_span,
        weight=2.5,
        note=GOLD_DEFINITIONS["GS-OVAL"]["full"],
    )

    # Edges at mid-latitude strip around lat_c
    strip = (np.abs(lat[:, None] - lat_c) <= 2.0) & mask
    sy, sx = np.where(strip)
    if len(sx) < 10:
        # fall back to full mask x extremes
        sx = xs
        sy = ys
    # west = larger System III longitude in classic SEB sense is ambiguous;
    # use geometric min/max of *unwrapped* lon relative to center
    rel = []
    for x in sx:
        rel.append(wrap_diff(float(lon_iii[int(x)]), lon_c))
    rel = np.asarray(rel, dtype=np.float64)
    west_rel = float(np.percentile(rel, 95))   # more positive relative = one side
    east_rel = float(np.percentile(rel, 5))
    # Assign by sign: more +rel = west-ish in CM frame depends on map orientation;
    # report both as edge high/low rel and also as lon
    lon_hi = _wrap_lon(lon_c + max(west_rel, east_rel))
    lon_lo = _wrap_lon(lon_c + min(west_rel, east_rel))
    # Convention: EDGE-W = higher System III (often preceding), EDGE-E = lower
    # Jupiter System III: features often quoted with west in direction of rotation —
    # we label by geometric extremes and document.
    edge_w = GoldMeasure(
        "GS-EDGE-W",
        lon_hi,
        lat_c,
        note="Higher-lon edge of dark oval (extent). " + GOLD_DEFINITIONS["GS-EDGE-W"]["notes"],
        weight=1.5,
    )
    edge_e = GoldMeasure(
        "GS-EDGE-E",
        lon_lo,
        lat_c,
        note="Lower-lon edge of dark oval (extent). " + GOLD_DEFINITIONS["GS-EDGE-E"]["notes"],
        weight=1.5,
    )
    mid_lon = _wrap_lon(lon_c)  # bary already mid-ish; use mean of edges
    # true midpoint of edges (wrap-aware)
    half = wrap_diff(lon_hi, lon_lo) / 2.0
    mid_lon = _wrap_lon(lon_lo + half)
    mid = GoldMeasure(
        "GS-MID",
        mid_lon,
        lat_c,
        length_deg=abs(wrap_diff(lon_hi, lon_lo)),
        width_deg=y_span,
        weight=2.0,
        note=GOLD_DEFINITIONS["GS-MID"]["full"],
    )
    return oval, edge_w, edge_e, mid


def compare_to_winjupos_manual(
    primary_lon: float,
    primary_lat: float,
    wj_lon: Optional[float],
    wj_lat: Optional[float],
    distance_au: float = 5.2,
) -> Optional[Dict[str, Any]]:
    """
    Validation against *your* careful WinJUPOS manual measure.
    This is NOT NASA truth — it is pro-workflow cross-check.
    """
    if wj_lon is None and wj_lat is None:
        return None
    out: Dict[str, Any] = {
        "role": "human_winjupos_validation",
        "note": (
            "Δ = this pipeline primary − your WinJUPOS manual pick. "
            "WinJUPOS is not an auto detector; this checks whether automated "
            "code matches careful human procedure."
        ),
        "pipeline_primary_lon_iii_deg": primary_lon,
        "pipeline_primary_lat_deg": primary_lat,
        "winjupos_manual_lon_iii_deg": wj_lon,
        "winjupos_manual_lat_deg": wj_lat,
    }
    if wj_lon is not None:
        dlon = wrap_diff(primary_lon, float(wj_lon))
        out["delta_lon_deg"] = dlon
        out["abs_delta_lon_deg"] = abs(dlon)
    if wj_lat is not None:
        dlat = float(primary_lat) - float(wj_lat)
        out["delta_lat_deg"] = dlat
        out["abs_delta_lat_deg"] = abs(dlat)
    if wj_lon is not None and wj_lat is not None:
        out["sky_error_arcsec"] = sky_error_arcsec(
            wrap_diff(primary_lon, float(wj_lon)),
            float(primary_lat) - float(wj_lat),
            float(wj_lat),
            distance_au,
        )
        sky = out["sky_error_arcsec"]
        if sky <= 1.0:
            out["agreement"] = "EXCELLENT (≤1″ vs your WinJUPOS pick)"
        elif sky <= 2.0:
            out["agreement"] = "GOOD (≤2″ — pro-amateur agreement)"
        elif sky <= 5.0:
            out["agreement"] = "FAIR — check definition / CM / time"
        else:
            out["agreement"] = "POOR — different feature or geometry mismatch"
    return out


def _pick_primary(measures: Sequence[GoldMeasure]) -> GoldMeasure:
    by_id = {m.definition_id: m for m in measures if m.ok}
    for pid in PRIMARY_ORDER:
        if pid in by_id:
            return by_id[pid]
    return max(measures, key=lambda m: m.weight)


def _scatter(primary: GoldMeasure, measures: Sequence[GoldMeasure]) -> Tuple[float, float]:
    dl, da, w = [], [], []
    for m in measures:
        if m.definition_id in ("GS-EDGE-W", "GS-EDGE-E"):
            continue  # edges are not centers
        if not m.ok:
            continue
        dlon = wrap_diff(m.lon_iii_deg, primary.lon_iii_deg)
        dlat = m.lat_deg - primary.lat_deg
        if abs(dlon) > 10 or abs(dlat) > 6:
            continue
        dl.append(dlon)
        da.append(dlat)
        w.append(m.weight)
    if len(dl) < 2:
        return 0.0, 0.0
    ww = np.asarray(w, dtype=np.float64)
    ww = ww / ww.sum()
    return (
        float(np.sqrt(np.average(np.asarray(dl) ** 2, weights=ww))),
        float(np.sqrt(np.average(np.asarray(da) ** 2, weights=ww))),
    )


def run_gold_standard(
    image: np.ndarray,
    nav: Optional[NavState] = None,
    *,
    cm_iii_deg: float = 0.0,
    distance_au: float = 5.2,
    cm_source: str = "unknown",
    user_time_iso: str = "",
    winjupos_manual_lon: Optional[float] = None,
    winjupos_manual_lat: Optional[float] = None,
    map_width: int = 2200,
    map_height: int = 1100,
    channels: Optional[Dict[str, Any]] = None,
    run_every_method: bool = True,
) -> GoldStandardResult:
    """
    Run the professional procedure suite on one image.

    Returns named definitions, primary GS measure, edge extent, optional
    WinJUPOS manual Δ. Does NOT claim NASA GRS truth.
    """
    import time as _time
    t0 = _time.time()
    im = to_mono(image)
    steps: List[str] = []
    notes: List[str] = [
        "This module replicates professional *procedure*, not a NASA GRS catalog.",
        "Primary product is a NAMED definition (e.g. GS-MAP) + CM source + scatter.",
        "WinJUPOS = geometry + human measuring desk, not an auto detector.",
        "JPL Horizons/SPICE = Jupiter geometry only, not official GRS longitude.",
        "To approach published quality: excellent stack + accurate UTC + SPICE/WJ CM + fixed definition.",
    ]

    if nav is None:
        steps.append("1. Limb navigation (disk fit) for projection")
        nav = fit_limb_nav(im, cm_iii_deg=cm_iii_deg, distance_au=distance_au)
    else:
        steps.append("1. Using provided navigation state")
    nav.cm_iii_deg = float(cm_iii_deg)
    nav.distance_au = float(distance_au)
    steps.append(f"2. Geometry: CM III={nav.cm_iii_deg:.5f}° source={cm_source}  Δ={nav.distance_au:.5f} AU")
    steps.append(f"3. Epoch: {user_time_iso or '(not set)'}")

    steps.append("4. Cylindrical deprojection (WinJUPOS-desk style map)")
    cyl = make_cylindrical(im, nav, width=map_width, height=map_height)

    measures: List[GoldMeasure] = []
    steps.append("5. Gold-standard definitions (fixed rules)")

    for name, fn in (
        ("GS-MAP", lambda: measure_gs_map(cyl, nav)),
        ("GS-BARY", lambda: measure_gs_bary(im, nav)),
        ("GS-TMPL", lambda: measure_gs_tmpl(cyl, nav)),
        ("GS-ENGINE", lambda: measure_gs_engine(im, nav)),
    ):
        try:
            measures.append(fn())
            CONSOLE.info(f"  {name}: lon={measures[-1].lon_iii_deg:.4f} lat={measures[-1].lat_deg:.4f}")
        except Exception as e:
            CONSOLE.debug(f"  {name} failed: {e}")
            measures.append(GoldMeasure(name, float("nan"), float("nan"), ok=False, note=str(e)))

    west = east = None
    try:
        oval, edge_w, edge_e, mid = measure_gs_oval_and_edges(cyl, nav)
        if oval:
            measures.append(oval)
        if mid:
            measures.append(mid)
        if edge_w:
            measures.append(edge_w)
            west = edge_w.lon_iii_deg
        if edge_e:
            measures.append(edge_e)
            east = edge_e.lon_iii_deg
        steps.append("6. Oval + W/E edges (WinJUPOS-like extent)")
    except Exception as e:
        CONSOLE.debug(f"oval/edges: {e}")
        steps.append(f"6. Oval/edges soft-fail: {e}")

    ok_meas = [m for m in measures if m.ok and m.definition_id not in ("GS-EDGE-W", "GS-EDGE-E") and math.isfinite(m.lon_iii_deg)]
    if not ok_meas:
        return GoldStandardResult(
            ok=False,
            primary_definition="NONE",
            primary_lon_iii_deg=float("nan"),
            primary_lat_deg=float("nan"),
            primary_length_deg=None,
            primary_width_deg=None,
            cm_iii_deg=nav.cm_iii_deg,
            cm_source=cm_source,
            distance_au=nav.distance_au,
            user_time_iso=user_time_iso,
            measures=[m.to_dict() for m in measures],
            procedure_steps=steps,
            notes=notes + ["No gold-standard definition succeeded."],
            grade="FAILED",
            elapsed_s=_time.time() - t0,
        )

    primary = _pick_primary(ok_meas)
    steps.append(f"7. Classic GS primary = {primary.definition_id}")
    # Classic GS definition scatter only (MAP/BARY/TMPL/OVAL…) — never replace with soup
    classic_gs = [m for m in ok_meas if str(m.definition_id).startswith("GS-")]
    scat_lon, scat_lat = _scatter(primary, classic_gs if classic_gs else ok_meas)
    steps.append(f"8. Classic GS scatter: σ_lon={scat_lon:.4f}°  σ_lat={scat_lat:.4f}°")
    soup_scat_lon = float("nan")
    soup_scat_lat = float("nan")

    # --- EVERY practical method (full suite) ---
    all_methods_block: Optional[Dict[str, Any]] = None
    if run_every_method:
        try:
            from all_methods import run_all_methods
            steps.append("9. ALL-METHODS suite (every laptop estimator)")
            all_methods_block = run_all_methods(
                im, nav, channels=channels, map_width=min(map_width, 1800), map_height=min(map_height, 900),
            )
            # Soup scatter is secondary product — does NOT redefine GS procedure grade
            if all_methods_block.get("scatter_lon_deg") is not None:
                soup_scat_lon = float(all_methods_block.get("scatter_lon_deg"))
                soup_scat_lat = float(all_methods_block.get("scatter_lat_deg") or float("nan"))
            for mh in all_methods_block.get("methods") or []:
                if not mh.get("ok"):
                    continue
                measures.append(GoldMeasure(
                    definition_id=str(mh.get("method_id")),
                    lon_iii_deg=float(mh.get("lon_iii_deg")),
                    lat_deg=float(mh.get("lat_deg")),
                    length_deg=mh.get("length_deg"),
                    width_deg=mh.get("width_deg"),
                    weight=float(mh.get("weight") or 1.0),
                    note=f"[{mh.get('family')}] {mh.get('note') or ''}",
                ))
            steps.append(
                f"10. ALL-METHODS: {all_methods_block.get('n_ok')}/{all_methods_block.get('n_total')} ok  "
                f"(secondary; GS primary stays {primary.definition_id}; "
                f"soup_scatter_lon={soup_scat_lon if math.isfinite(soup_scat_lon) else float('nan'):.3f}°)"
            )
        except Exception as e:
            CONSOLE.warn(f"ALL-METHODS suite soft-fail: {e}")
            steps.append(f"9. ALL-METHODS soft-fail: {e}")

    extent = None
    if west is not None and east is not None:
        extent = abs(wrap_diff(west, east))
        steps.append(f"11. Lon extent |W−E| = {extent:.3f}°")

    wj = compare_to_winjupos_manual(
        primary.lon_iii_deg,
        primary.lat_deg,
        winjupos_manual_lon,
        winjupos_manual_lat,
        distance_au=nav.distance_au,
    )
    if wj:
        steps.append(
            f"12. vs WinJUPOS manual: Δlon={wj.get('delta_lon_deg')}  "
            f"Δlat={wj.get('delta_lat_deg')}  sky={wj.get('sky_error_arcsec')}″"
        )

    # Grade on *classic* GS definition agreement only (not method soup)
    if scat_lon <= 0.3 and scat_lat <= 0.2:
        grade = "PRO_TIGHT"  # definitions agree — good internal procedure
    elif scat_lon <= 0.8 and scat_lat <= 0.5:
        grade = "PRO_GOOD"
    elif scat_lon <= 1.5:
        grade = "PRO_FAIR"
    else:
        grade = "PRO_LOOSE"
    if wj and wj.get("sky_error_arcsec") is not None:
        if wj["sky_error_arcsec"] <= 2.0:
            grade = grade + "+WJ_MATCH"
        elif wj["sky_error_arcsec"] > 5.0:
            grade = grade + "+WJ_MISMATCH"
    if all_methods_block and all_methods_block.get("n_ok", 0) >= 15:
        grade = grade + f"+M{all_methods_block['n_ok']}"

    steps.append("13. Export-ready package (no fake NASA GRS answer)")
    notes.append(
        f"Full method count: {len(measures)} entries "
        f"(classic GS + all-methods). Primary={primary.definition_id}."
    )
    notes.append(
        "definition_scatter_* = classic GS only; all_methods soup scatter is separate "
        f"(soup_lon={soup_scat_lon if math.isfinite(soup_scat_lon) else 'n/a'})."
    )
    CONSOLE.ok(
        f"GOLD STANDARD primary={primary.definition_id}  "
        f"lon={primary.lon_iii_deg:.4f}° lat={primary.lat_deg:.4f}°  "
        f"scatter_lon={scat_lon:.3f}°  {grade}"
    )

    result = GoldStandardResult(
        ok=True,
        primary_definition=primary.definition_id,
        primary_lon_iii_deg=float(primary.lon_iii_deg),
        primary_lat_deg=float(primary.lat_deg),
        primary_length_deg=primary.length_deg,
        primary_width_deg=primary.width_deg,
        cm_iii_deg=float(nav.cm_iii_deg),
        cm_source=cm_source,
        distance_au=float(nav.distance_au),
        user_time_iso=user_time_iso,
        measures=[m.to_dict() for m in measures],
        definition_scatter_lon_deg=scat_lon,
        definition_scatter_lat_deg=scat_lat,
        west_edge_lon_iii_deg=west,
        east_edge_lon_iii_deg=east,
        extent_lon_deg=extent,
        winjupos_manual=wj,
        procedure_steps=steps,
        notes=notes,
        grade=grade,
        elapsed_s=_time.time() - t0,
    )
    # stash full all-methods on dict after to_dict via attach
    result._all_methods = all_methods_block  # type: ignore[attr-defined]
    if math.isfinite(soup_scat_lon):
        result._soup_scatter = {  # type: ignore[attr-defined]
            "all_methods_scatter_lon_deg": soup_scat_lon,
            "all_methods_scatter_lat_deg": soup_scat_lat if math.isfinite(soup_scat_lat) else None,
        }
    return result


def format_gold_report(gs: GoldStandardResult) -> str:
    """Human text: professional procedure, not 'the NASA answer'."""
    lines = [
        "╔" + "═" * 70 + "╗",
        "║" + " PROFESSIONAL GRS PROCEDURE (GOLD STANDARD)".center(70) + "║",
        "║" + " methodology · named definitions · WinJUPOS check · not NASA truth".center(70) + "║",
        "╚" + "═" * 70 + "╝",
        "",
        "PHILOSOPHY",
        "  We do NOT output a fake 'NASA GRS answer'.",
        "  We replicate professional code/procedure so YOUR measure is close to",
        "  what careful WinJUPOS / published work would get on the same data.",
        "",
        "GEOMETRY (agency-grade sources when available)",
        f"  CM III           {gs.cm_iii_deg:.6f} °",
        f"  CM source        {gs.cm_source}",
        f"  Distance         {gs.distance_au:.6f} AU",
        f"  Epoch            {gs.user_time_iso or '—'}",
        "",
        "PRIMARY PRODUCT (one named definition)",
        f"  Definition       {gs.primary_definition}",
        f"  Lon III          {gs.primary_lon_iii_deg:.6f} °",
        f"  Lat              {gs.primary_lat_deg:.6f} °",
        f"  Length           {gs.primary_length_deg}",
        f"  Width            {gs.primary_width_deg}",
        f"  Procedure grade  {gs.grade}",
        f"  Def scatter lon  {gs.definition_scatter_lon_deg:.4f} °",
        f"  Def scatter lat  {gs.definition_scatter_lat_deg:.4f} °",
        "",
        "COPY-PASTE (primary)",
        f"  GS_LON_III_DEG = {gs.primary_lon_iii_deg:.8f}",
        f"  GS_LAT_DEG     = {gs.primary_lat_deg:.8f}",
        f"  GS_DEFINITION  = {gs.primary_definition}",
        f"  GS_CM_SOURCE   = {gs.cm_source}",
        "",
    ]
    if gs.west_edge_lon_iii_deg is not None:
        lines += [
            "WINJUPOS-LIKE EXTENT (edges, not center)",
            f"  West/high edge lon  {gs.west_edge_lon_iii_deg:.6f} °",
            f"  East/low edge lon   {gs.east_edge_lon_iii_deg:.6f} °",
            f"  |W−E| extent        {gs.extent_lon_deg:.4f} °",
            "",
        ]
    lines.append("ALL METHODS / DEFINITIONS (every estimator that ran)")
    lines.append(f"  count={len(gs.measures)}")
    lines.append("  id                lon_III        lat         L       W      ok   note")
    for m in gs.measures:
        note = (m.get("note") or "")[:40]
        lines.append(
            f"  {str(m.get('definition_id', '?')):<16}  "
            f"{m.get('lon_iii_deg', float('nan'))!s:>12}  "
            f"{m.get('lat_deg', float('nan'))!s:>10}  "
            f"{str(m.get('length_deg')):>7}  "
            f"{str(m.get('width_deg')):>7}  "
            f"{m.get('ok')}  {note}"
        )
    lines.append("")
    am = getattr(gs, "_all_methods", None) or {}
    if am:
        lines.append("ALL-METHODS SUMMARY")
        lines.append(f"  n_ok/n_total     {am.get('n_ok')}/{am.get('n_total')}")
        lines.append(f"  suite primary    {am.get('primary_method')}")
        lines.append(f"  suite scatter lon {am.get('scatter_lon_deg')}")
        lines.append(f"  suite scatter lat {am.get('scatter_lat_deg')}")
        lines.append("")

    if gs.winjupos_manual:
        w = gs.winjupos_manual
        lines += [
            "VS YOUR WINJUPOS MANUAL PICK (validation, not NASA truth)",
            f"  Pipeline primary lon  {w.get('pipeline_primary_lon_iii_deg')}",
            f"  WinJUPOS manual lon   {w.get('winjupos_manual_lon_iii_deg')}",
            f"  Δlon (pipe − WJ)      {w.get('delta_lon_deg')}",
            f"  Pipeline primary lat  {w.get('pipeline_primary_lat_deg')}",
            f"  WinJUPOS manual lat   {w.get('winjupos_manual_lat_deg')}",
            f"  Δlat                  {w.get('delta_lat_deg')}",
            f"  On-sky |Δ|            {w.get('sky_error_arcsec')} arcsec",
            f"  Agreement             {w.get('agreement')}",
            f"  Note                  {w.get('note')}",
            "",
        ]

    lines.append("PROCEDURE STEPS")
    for s in gs.procedure_steps:
        lines.append(f"  {s}")
    lines.append("")
    lines.append("NOTES")
    for n in gs.notes:
        lines.append(f"  · {n}")
    lines.append("")
    lines.append("DEFINITION CATALOG")
    for k, meta in GOLD_DEFINITIONS.items():
        lines.append(f"  {k}: {meta['full']}")
        lines.append(f"       role={meta['role']} — {meta['notes']}")
    lines.append("")
    lines.append("FULL JSON")
    lines.append(json.dumps(gs.to_dict(), indent=2, default=str))
    return "\n".join(lines)


def write_gold_standard_bundle(out_dir: Path, gs: GoldStandardResult) -> Dict[str, str]:
    """Write gold_standard.json/txt + winjupos-compatible measure export."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = {}
    jpath = out_dir / "gold_standard.json"
    tpath = out_dir / "gold_standard.txt"
    jpath.write_text(json.dumps(gs.to_dict(), indent=2, default=str), encoding="utf-8")
    tpath.write_text(format_gold_report(gs), encoding="utf-8")
    paths["json"] = str(jpath)
    paths["txt"] = str(tpath)

    # WinJUPOS-compatible / notebook export (what a pro would log)
    export_lines = [
        "# GRS measure export — professional procedure",
        f"# generated {datetime.now().isoformat(timespec='seconds')}",
        f"# primary_definition {gs.primary_definition}",
        f"# cm_source {gs.cm_source}",
        f"# NOT a NASA GRS catalog product",
        f"utc={gs.user_time_iso}",
        f"cm_iii_deg={gs.cm_iii_deg:.6f}",
        f"distance_au={gs.distance_au:.6f}",
        f"grs_lon_iii_deg={gs.primary_lon_iii_deg:.6f}",
        f"grs_lat_deg={gs.primary_lat_deg:.6f}",
        f"grs_length_deg={gs.primary_length_deg}",
        f"grs_width_deg={gs.primary_width_deg}",
        f"definition={gs.primary_definition}",
        f"definition_scatter_lon_deg={gs.definition_scatter_lon_deg:.5f}",
        f"definition_scatter_lat_deg={gs.definition_scatter_lat_deg:.5f}",
        f"west_edge_lon_iii_deg={gs.west_edge_lon_iii_deg}",
        f"east_edge_lon_iii_deg={gs.east_edge_lon_iii_deg}",
        f"extent_lon_deg={gs.extent_lon_deg}",
        f"procedure_grade={gs.grade}",
    ]
    if gs.winjupos_manual:
        export_lines.append(f"winjupos_manual_lon={gs.winjupos_manual.get('winjupos_manual_lon_iii_deg')}")
        export_lines.append(f"winjupos_manual_lat={gs.winjupos_manual.get('winjupos_manual_lat_deg')}")
        export_lines.append(f"delta_vs_winjupos_lon_deg={gs.winjupos_manual.get('delta_lon_deg')}")
        export_lines.append(f"delta_vs_winjupos_sky_arcsec={gs.winjupos_manual.get('sky_error_arcsec')}")
    epath = out_dir / "winjupos_compatible_measure.txt"
    epath.write_text("\n".join(export_lines) + "\n", encoding="utf-8")
    paths["export"] = str(epath)
    return paths


def attach_gold_to_package(
    package: Dict[str, Any],
    image: np.ndarray,
    *,
    nav: Optional[NavState] = None,
    cm_iii_deg: float = 0.0,
    distance_au: float = 5.2,
    cm_source: str = "unknown",
    user_time_iso: str = "",
    winjupos_manual_lon: Optional[float] = None,
    winjupos_manual_lat: Optional[float] = None,
    out_dir: Optional[Path] = None,
    channels: Optional[Dict[str, Any]] = None,
    run_every_method: bool = True,
) -> Dict[str, Any]:
    """Run gold standard + every method, attach to package, optionally write files."""
    gs = run_gold_standard(
        image,
        nav=nav,
        cm_iii_deg=cm_iii_deg,
        distance_au=distance_au,
        cm_source=cm_source,
        user_time_iso=user_time_iso,
        winjupos_manual_lon=winjupos_manual_lon,
        winjupos_manual_lat=winjupos_manual_lat,
        channels=channels,
        run_every_method=run_every_method,
    )
    gd = gs.to_dict()
    package["gold_standard"] = gd
    if gd.get("all_methods"):
        package["all_methods"] = gd["all_methods"]
    # Promote primary into headline for dashboards
    h = package.setdefault("headline", {})
    h["gold_primary_definition"] = gs.primary_definition
    h["gold_lon_iii_deg"] = gs.primary_lon_iii_deg
    h["gold_lat_deg"] = gs.primary_lat_deg
    h["gold_procedure_grade"] = gs.grade
    h["gold_scatter_lon_deg"] = gs.definition_scatter_lon_deg
    h["n_methods_ok"] = gd.get("n_methods_ok")
    h["n_methods_total"] = gd.get("n_methods_total")
    h["cm_source"] = h.get("cm_source") or gs.cm_source
    if gs.winjupos_manual:
        h["vs_winjupos_sky_arcsec"] = gs.winjupos_manual.get("sky_error_arcsec")
        h["vs_winjupos_dlon_deg"] = gs.winjupos_manual.get("delta_lon_deg")
        package["winjupos_validation"] = gs.winjupos_manual
    if out_dir is not None:
        package["gold_standard_files"] = write_gold_standard_bundle(Path(out_dir), gs)
        # also dump all_methods.json alone for easy browsing
        if gd.get("all_methods"):
            p = Path(out_dir) / "all_methods.json"
            p.write_text(json.dumps(gd["all_methods"], indent=2, default=str), encoding="utf-8")
            package["gold_standard_files"]["all_methods"] = str(p)

    # State-of-the-art robust consensus (MAD outlier rejection + quality gates)
    try:
        from sota_accuracy import apply_sota_to_package, format_sota_section
        apply_sota_to_package(
            package,
            nav=nav,
            distance_au=distance_au,
            cm_source=cm_source,
            user_time_iso=user_time_iso,
            fits_path=package.get("path") or package.get("fits_path"),
            winjupos_manual_lon=winjupos_manual_lon,
            winjupos_manual_lat=winjupos_manual_lat,
        )
        if out_dir is not None and package.get("sota"):
            sp = Path(out_dir) / "sota_accuracy.json"
            sp.write_text(json.dumps(package["sota"], indent=2, default=str), encoding="utf-8")
            st = Path(out_dir) / "sota_accuracy.txt"
            st.write_text(format_sota_section(package["sota"]), encoding="utf-8")
            package.setdefault("gold_standard_files", {})["sota_json"] = str(sp)
            package["gold_standard_files"]["sota_txt"] = str(st)
            CONSOLE.ok(
                f"SOTA files → {sp.name}  grade={package['sota'].get('quality_grade')}"
            )
    except Exception as e:
        CONSOLE.warn(f"SOTA accuracy layer soft-fail: {e}")

    # AI only on hard cases (where classical methods scatter / image is soft)
    try:
        from ai_hard_cases import apply_hard_case_ai_to_package
        apply_hard_case_ai_to_package(
            package,
            image,
            nav=nav,
            cm_iii_deg=cm_iii_deg,
        )
        if out_dir is not None and package.get("ai_hard_case"):
            ap = Path(out_dir) / "ai_hard_case.json"
            ap.write_text(json.dumps(package["ai_hard_case"], indent=2, default=str), encoding="utf-8")
            package.setdefault("gold_standard_files", {})["ai_hard_case"] = str(ap)
            ah = package["ai_hard_case"]
            if ah.get("engaged") and ah.get("nn_used"):
                CONSOLE.ok(
                    f"AI hard-case assist: difficulty={ah.get('difficulty', 0.0):.2f} "
                    f"w={ah.get('blend_weight', 0.0):.2f}"
                )
            else:
                CONSOLE.info(
                    f"AI hard-case: not needed (difficulty={ah.get('difficulty'):.2f}) — "
                    f"{ah.get('note', '')[:80]}"
                )
    except Exception as e:
        CONSOLE.warn(f"AI hard-case soft-fail: {e}")

    return package

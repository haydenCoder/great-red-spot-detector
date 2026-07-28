#!/usr/bin/env python3
"""
WinJUPOS twin mode + limb / definition sensitivity
==================================================

WinJUPOS is accurate because pros lock:
  1) mid-exposure UTC
  2) CM III (from WinJUPOS / SPICE / paste)
  3) a *fixed* measurement definition (core vs W/E edge vs mid)
  4) a consistent limb outline size (human can draw larger or smaller)

Yes — choosing a larger vs smaller limb outline **does** change absolute lon/lat.
Same for GRS: dark core ≠ west edge ≠ east edge ≠ mid of edges.

This module:
  • Forces WinJUPOS-style reporting: GS-MAP / GS-BARY as twin primaries
  • Quantifies limb outline sensitivity (outer / nominal / inner isophotes)
  • Quantifies GRS definition scatter (core vs edges vs mid)
  • Optional Δ vs your manual WinJUPOS pick

NOT a NASA GRS catalog. Matches *procedure*, not a secret answer key.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
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
    _map_dark_centroid,
    _moment_mask_grs,
)


# Default isophote fractions ≈ human outline size
# Smaller frac → fainter edge → LARGER disk outline (outer limb)
# Larger frac → brighter edge → SMALLER disk outline (inner limb)
LIMB_OUTER_FRAC = 0.12   # large outline (soft edge)
LIMB_NOMINAL_FRAC = 0.18  # default pipeline
LIMB_INNER_FRAC = 0.30   # small outline (harder edge)


@dataclass
class LimbProbe:
    name: str
    isophote_frac: float
    a_eq_px: float
    xc: float
    yc: float
    lon_iii_deg: float
    lat_deg: float
    note: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class TwinResult:
    ok: bool
    mode: str = "winjupos_twin"
    # Twin primaries (fixed definitions — WinJUPOS-like)
    twin_primary_definition: str = "GS-MAP"
    twin_lon_iii_deg: float = float("nan")
    twin_lat_deg: float = float("nan")
    gs_map_lon: float = float("nan")
    gs_map_lat: float = float("nan")
    gs_bary_lon: float = float("nan")
    gs_bary_lat: float = float("nan")
    # Geometry
    cm_iii_deg: float = float("nan")
    cm_source: str = ""
    distance_au: float = 5.2
    # Limb outline sensitivity (human larger/smaller edge)
    limb_probes: List[Dict[str, Any]] = field(default_factory=list)
    limb_radius_spread_px: float = 0.0
    limb_lon_spread_deg: float = 0.0
    limb_lat_spread_deg: float = 0.0
    limb_sky_spread_arcsec: float = 0.0
    limb_note: str = ""
    # GRS definition sensitivity (core vs edges)
    definition_table: List[Dict[str, Any]] = field(default_factory=list)
    definition_lon_spread_deg: float = 0.0
    definition_lat_spread_deg: float = 0.0
    west_edge_lon: Optional[float] = None
    east_edge_lon: Optional[float] = None
    mid_edge_lon: Optional[float] = None
    extent_lon_deg: Optional[float] = None
    # Manual WinJUPOS check
    winjupos_manual: Optional[Dict[str, Any]] = None
    notes: List[str] = field(default_factory=list)
    grade: str = "—"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _measure_map_and_bary(
    image: np.ndarray, nav: NavState
) -> Tuple[Optional[Dict[str, float]], Optional[Dict[str, float]], Optional[np.ndarray]]:
    im = to_mono(image)
    cyl = None
    map_m = None
    bary = None
    try:
        cyl = make_cylindrical(im, nav, width=1440, height=720)
        map_m = _map_dark_centroid(cyl, nav)
    except Exception as e:
        CONSOLE.debug(f"twin map: {e}")
    try:
        bary = _moment_mask_grs(im, nav)
    except Exception as e:
        CONSOLE.debug(f"twin bary: {e}")
    return map_m, bary, cyl


def limb_outline_sensitivity(
    image: np.ndarray,
    *,
    cm_iii_deg: float,
    distance_au: float = 5.2,
    sub_lat_deg: float = 0.0,
    north_pa_deg: float = 0.0,
    fracs: Optional[Sequence[Tuple[str, float, str]]] = None,
) -> Dict[str, Any]:
    """
    Re-nav with outer / nominal / inner limb isophotes and re-measure GRS.

    This answers: "If I draw the WinJUPOS outline larger or smaller, how much
    does GRS lon/lat move?"
    """
    fracs = list(fracs or (
        ("outer_large_outline", LIMB_OUTER_FRAC, "Fainter limb (larger disk) — soft seeing edge"),
        ("nominal", LIMB_NOMINAL_FRAC, "Default pipeline isophote"),
        ("inner_small_outline", LIMB_INNER_FRAC, "Brighter limb (smaller disk) — tight outline"),
    ))
    probes: List[LimbProbe] = []
    for name, frac, note in fracs:
        nav = fit_limb_nav(
            image,
            cm_iii_deg=cm_iii_deg,
            distance_au=distance_au,
            isophote_frac=frac,
        )
        nav.cm_iii_deg = cm_iii_deg
        nav.distance_au = distance_au
        nav.sub_lat_deg = sub_lat_deg
        nav.north_pa_deg = north_pa_deg
        map_m, bary, _ = _measure_map_and_bary(image, nav)
        # Prefer map centroid; fall back to bary
        if map_m is not None:
            lon, lat = float(map_m["lon_iii_deg"]), float(map_m["lat_deg"])
        elif bary is not None:
            lon, lat = float(bary["lon_iii_deg"]), float(bary["lat_deg"])
        else:
            lon, lat = float("nan"), float("nan")
        probes.append(LimbProbe(
            name=name,
            isophote_frac=frac,
            a_eq_px=float(nav.a_eq_px),
            xc=float(nav.xc),
            yc=float(nav.yc),
            lon_iii_deg=lon,
            lat_deg=lat,
            note=note,
        ))

    lons = [p.lon_iii_deg for p in probes if math.isfinite(p.lon_iii_deg)]
    lats = [p.lat_deg for p in probes if math.isfinite(p.lat_deg)]
    radii = [p.a_eq_px for p in probes]
    lon_spread = float(max(lons) - min(lons)) if len(lons) >= 2 else 0.0
    # circular-safe lon spread
    if len(lons) >= 2:
        lon_spread = 0.0
        for i in range(len(lons)):
            for j in range(i + 1, len(lons)):
                lon_spread = max(lon_spread, abs(wrap_diff(lons[i], lons[j])))
    lat_spread = float(max(lats) - min(lats)) if len(lats) >= 2 else 0.0
    r_spread = float(max(radii) - min(radii)) if len(radii) >= 2 else 0.0
    sky = sky_error_arcsec(lon_spread, lat_spread, lats[0] if lats else -22.0, distance_au) if lons else 0.0

    # Pairwise outer vs inner (the human larger/smaller question)
    outer = next((p for p in probes if "outer" in p.name), None)
    inner = next((p for p in probes if "inner" in p.name), None)
    pair = None
    if outer and inner and math.isfinite(outer.lon_iii_deg) and math.isfinite(inner.lon_iii_deg):
        dlon = wrap_diff(outer.lon_iii_deg, inner.lon_iii_deg)
        dlat = outer.lat_deg - inner.lat_deg
        pair = {
            "outer_minus_inner_lon_deg": dlon,
            "outer_minus_inner_lat_deg": dlat,
            "outer_minus_inner_sky_arcsec": sky_error_arcsec(dlon, dlat, outer.lat_deg, distance_au),
            "radius_outer_px": outer.a_eq_px,
            "radius_inner_px": inner.a_eq_px,
            "radius_delta_px": outer.a_eq_px - inner.a_eq_px,
        }

    note = (
        "YES: larger vs smaller limb outline changes absolute GRS lon/lat. "
        "Spread below is a systematic floor from outline size alone (same feature pick). "
        "Always use one consistent outline style night-to-night (WinJUPOS discipline)."
    )
    return {
        "probes": [p.to_dict() for p in probes],
        "radius_spread_px": r_spread,
        "lon_spread_deg": lon_spread,
        "lat_spread_deg": lat_spread,
        "sky_spread_arcsec": float(sky),
        "outer_vs_inner": pair,
        "note": note,
    }


def grs_definition_sensitivity(
    image: np.ndarray,
    nav: NavState,
) -> Dict[str, Any]:
    """
    Core vs W/E edges vs mid — different human picks, different System III lon.

    Same reason WinJUPOS users must write *which* point they measured.
    """
    from gold_standard import (
        measure_gs_map,
        measure_gs_bary,
        measure_gs_oval_and_edges,
    )
    table: List[Dict[str, Any]] = []
    im = to_mono(image)
    try:
        cyl = make_cylindrical(im, nav)
    except Exception as e:
        return {"ok": False, "error": str(e), "table": []}

    try:
        m = measure_gs_map(cyl, nav)
        table.append({
            "definition": "GS-MAP (dark map core)",
            "role": "core",
            "lon_iii_deg": m.lon_iii_deg,
            "lat_deg": m.lat_deg,
            "note": "Closest automated analog to a careful dark-core pick on the map",
        })
    except Exception as e:
        CONSOLE.debug(f"def map: {e}")
    try:
        b = measure_gs_bary(im, nav)
        table.append({
            "definition": "GS-BARY (image barycentre)",
            "role": "core",
            "lon_iii_deg": b.lon_iii_deg,
            "lat_deg": b.lat_deg,
            "note": "Intensity-weighted dark core in image plane",
        })
    except Exception as e:
        CONSOLE.debug(f"def bary: {e}")

    west = east = mid = oval = None
    try:
        oval, west, east, mid = measure_gs_oval_and_edges(cyl, nav)
        if oval:
            table.append({
                "definition": "GS-OVAL (ellipse centre)",
                "role": "core",
                "lon_iii_deg": oval.lon_iii_deg,
                "lat_deg": oval.lat_deg,
                "length_deg": oval.length_deg,
                "width_deg": oval.width_deg,
            })
        if west:
            table.append({
                "definition": "GS-EDGE-W (higher-lon edge)",
                "role": "edge",
                "lon_iii_deg": west.lon_iii_deg,
                "lat_deg": west.lat_deg,
                "note": "Not a centre — extent. Differs from core by ~half the oval length.",
            })
        if east:
            table.append({
                "definition": "GS-EDGE-E (lower-lon edge)",
                "role": "edge",
                "lon_iii_deg": east.lon_iii_deg,
                "lat_deg": east.lat_deg,
                "note": "Not a centre — extent.",
            })
        if mid:
            table.append({
                "definition": "GS-MID (mid of W/E edges)",
                "role": "extent_mid",
                "lon_iii_deg": mid.lon_iii_deg,
                "lat_deg": mid.lat_deg,
                "length_deg": mid.length_deg,
                "note": "Often close to core but not identical if oval is asymmetric.",
            })
    except Exception as e:
        CONSOLE.debug(f"def edges: {e}")

    cores = [t for t in table if t.get("role") == "core" and math.isfinite(float(t.get("lon_iii_deg", float("nan"))))]
    all_ok = [t for t in table if math.isfinite(float(t.get("lon_iii_deg", float("nan"))))]
    lon_spread = 0.0
    lat_spread = 0.0
    if len(all_ok) >= 2:
        for i in range(len(all_ok)):
            for j in range(i + 1, len(all_ok)):
                lon_spread = max(lon_spread, abs(wrap_diff(all_ok[i]["lon_iii_deg"], all_ok[j]["lon_iii_deg"])))
                lat_spread = max(lat_spread, abs(all_ok[i]["lat_deg"] - all_ok[j]["lat_deg"]))
    core_spread = 0.0
    if len(cores) >= 2:
        for i in range(len(cores)):
            for j in range(i + 1, len(cores)):
                core_spread = max(core_spread, abs(wrap_diff(cores[i]["lon_iii_deg"], cores[j]["lon_iii_deg"])))

    extent = None
    if west and east:
        extent = abs(wrap_diff(west.lon_iii_deg, east.lon_iii_deg))

    return {
        "ok": len(table) > 0,
        "table": table,
        "lon_spread_all_defs_deg": lon_spread,
        "lat_spread_all_defs_deg": lat_spread,
        "lon_spread_cores_only_deg": core_spread,
        "west_edge_lon_iii_deg": west.lon_iii_deg if west else None,
        "east_edge_lon_iii_deg": east.lon_iii_deg if east else None,
        "mid_edge_lon_iii_deg": mid.lon_iii_deg if mid else None,
        "extent_lon_deg": extent,
        "note": (
            "YES: edge picks vs core picks differ by design (often several degrees in lon). "
            "Publish one fixed definition every night. Do not mix EDGE-W with MAP core."
        ),
    }


def run_winjupos_twin(
    image: np.ndarray,
    *,
    nav: Optional[NavState] = None,
    cm_iii_deg: float,
    distance_au: float = 5.2,
    cm_source: str = "winjupos_or_override",
    sub_lat_deg: float = 0.0,
    north_pa_deg: float = 0.0,
    user_time_iso: str = "",
    winjupos_manual_lon: Optional[float] = None,
    winjupos_manual_lat: Optional[float] = None,
    run_limb_sensitivity: bool = True,
) -> TwinResult:
    """
    WinJUPOS twin reduction: fixed CM + fixed definitions + sensitivity budgets.
    """
    notes = [
        "WinJUPOS twin: CM locked from your source; primary = GS-MAP then GS-BARY.",
        "Limb outline size (larger/smaller edge) is a real systematic — see limb_probes.",
        "GRS edge vs core definitions differ — report which one you publish.",
        "Not a NASA GRS longitude catalog.",
    ]
    im = to_mono(image)
    if nav is None:
        nav = fit_limb_nav(im, cm_iii_deg=cm_iii_deg, distance_au=distance_au)
    nav = NavState(
        xc=nav.xc,
        yc=nav.yc,
        a_eq_px=nav.a_eq_px,
        flattening=nav.flattening,
        cm_iii_deg=float(cm_iii_deg),
        distance_au=float(distance_au),
        sub_lat_deg=float(sub_lat_deg if sub_lat_deg is not None else getattr(nav, "sub_lat_deg", 0.0) or 0.0),
        north_pa_deg=float(north_pa_deg if north_pa_deg is not None else getattr(nav, "north_pa_deg", 0.0) or 0.0),
    )

    map_m, bary, _ = _measure_map_and_bary(im, nav)
    gs_map_lon = float(map_m["lon_iii_deg"]) if map_m else float("nan")
    gs_map_lat = float(map_m["lat_deg"]) if map_m else float("nan")
    gs_bary_lon = float(bary["lon_iii_deg"]) if bary else float("nan")
    gs_bary_lat = float(bary["lat_deg"]) if bary else float("nan")

    if math.isfinite(gs_map_lon):
        twin_def, twin_lon, twin_lat = "GS-MAP", gs_map_lon, gs_map_lat
    elif math.isfinite(gs_bary_lon):
        twin_def, twin_lon, twin_lat = "GS-BARY", gs_bary_lon, gs_bary_lat
    else:
        twin_def, twin_lon, twin_lat = "NONE", float("nan"), float("nan")
        notes.append("No twin primary succeeded.")

    limb_block: Dict[str, Any] = {}
    if run_limb_sensitivity:
        try:
            limb_block = limb_outline_sensitivity(
                im,
                cm_iii_deg=cm_iii_deg,
                distance_au=distance_au,
                sub_lat_deg=nav.sub_lat_deg,
                north_pa_deg=nav.north_pa_deg,
            )
        except Exception as e:
            notes.append(f"Limb sensitivity soft-fail: {e}")
            limb_block = {}

    def_block: Dict[str, Any] = {}
    try:
        def_block = grs_definition_sensitivity(im, nav)
    except Exception as e:
        notes.append(f"Definition sensitivity soft-fail: {e}")

    wj = None
    if winjupos_manual_lon is not None or winjupos_manual_lat is not None:
        from gold_standard import compare_to_winjupos_manual
        wj = compare_to_winjupos_manual(
            twin_lon, twin_lat, winjupos_manual_lon, winjupos_manual_lat, distance_au=distance_au
        )

    # Grade: twin ok + limb spread not insane
    limb_sky = float(limb_block.get("sky_spread_arcsec") or 0)
    if not math.isfinite(twin_lon):
        grade = "FAILED"
    elif limb_sky <= 1.0 and (def_block.get("lon_spread_cores_only_deg") or 0) <= 1.5:
        grade = "TWIN_EXCELLENT"
    elif limb_sky <= 2.5:
        grade = "TWIN_GOOD"
    elif limb_sky <= 5.0:
        grade = "TWIN_FAIR"
    else:
        grade = "TWIN_CHECK_LIMB"

    notes.append(
        f"Limb outline larger↔smaller sky spread ≈ {limb_sky:.3f}″ "
        f"(lon spread {float(limb_block.get('lon_spread_deg') or 0):.3f}°)."
    )
    if def_block.get("extent_lon_deg") is not None:
        notes.append(
            f"W–E extent ≈ {def_block['extent_lon_deg']:.2f}° — edges are not the core position."
        )

    return TwinResult(
        ok=math.isfinite(twin_lon),
        twin_primary_definition=twin_def,
        twin_lon_iii_deg=twin_lon,
        twin_lat_deg=twin_lat,
        gs_map_lon=gs_map_lon,
        gs_map_lat=gs_map_lat,
        gs_bary_lon=gs_bary_lon,
        gs_bary_lat=gs_bary_lat,
        cm_iii_deg=float(cm_iii_deg),
        cm_source=cm_source,
        distance_au=float(distance_au),
        limb_probes=list(limb_block.get("probes") or []),
        limb_radius_spread_px=float(limb_block.get("radius_spread_px") or 0),
        limb_lon_spread_deg=float(limb_block.get("lon_spread_deg") or 0),
        limb_lat_spread_deg=float(limb_block.get("lat_spread_deg") or 0),
        limb_sky_spread_arcsec=limb_sky,
        limb_note=str(limb_block.get("note") or ""),
        definition_table=list(def_block.get("table") or []),
        definition_lon_spread_deg=float(def_block.get("lon_spread_all_defs_deg") or 0),
        definition_lat_spread_deg=float(def_block.get("lat_spread_all_defs_deg") or 0),
        west_edge_lon=def_block.get("west_edge_lon_iii_deg"),
        east_edge_lon=def_block.get("east_edge_lon_iii_deg"),
        mid_edge_lon=def_block.get("mid_edge_lon_iii_deg"),
        extent_lon_deg=def_block.get("extent_lon_deg"),
        winjupos_manual=wj,
        notes=notes,
        grade=grade,
    )


def format_twin_report(tr: TwinResult) -> str:
    lines = [
        "WINJUPOS TWIN + LIMB / DEFINITION SENSITIVITY",
        "=" * 56,
        f"Grade: {tr.grade}",
        f"CM III = {tr.cm_iii_deg:.4f}°  source={tr.cm_source}",
        f"Twin primary ({tr.twin_primary_definition}): "
        f"lon={tr.twin_lon_iii_deg:.4f}°  lat={tr.twin_lat_deg:.4f}°",
        f"GS-MAP:  lon={tr.gs_map_lon:.4f}°  lat={tr.gs_map_lat:.4f}°",
        f"GS-BARY: lon={tr.gs_bary_lon:.4f}°  lat={tr.gs_bary_lat:.4f}°",
        "",
        "LIMB OUTLINE (larger vs smaller edge) — YES this matters",
        f"  Radius spread: {tr.limb_radius_spread_px:.2f} px",
        f"  Lon spread:    {tr.limb_lon_spread_deg:.4f}°",
        f"  Lat spread:    {tr.limb_lat_spread_deg:.4f}°",
        f"  Sky spread:    {tr.limb_sky_spread_arcsec:.4f}″",
    ]
    for p in tr.limb_probes:
        lines.append(
            f"  · {p.get('name', '—')}: isophote={p.get('isophote_frac', 0.0)}  "
            f"a={p.get('a_eq_px', 0.0):.1f}px  lon={p.get('lon_iii_deg', 0.0):.4f}°  "
            f"lat={p.get('lat_deg', 0.0):.4f}°"
        )
    lines.append("")
    lines.append("GRS DEFINITION (core vs edges) — YES this matters")
    lines.append(f"  Lon spread (all defs): {tr.definition_lon_spread_deg:.4f}°")
    lines.append(f"  Extent W–E: {tr.extent_lon_deg}")
    for d in tr.definition_table:
        lines.append(
            f"  · {d.get('definition')}: lon={d.get('lon_iii_deg')}  lat={d.get('lat_deg')}  "
            f"[{d.get('role')}]"
        )
    if tr.winjupos_manual:
        w = tr.winjupos_manual
        lines.append("")
        lines.append("VS YOUR WINJUPOS MANUAL PICK")
        lines.append(f"  Δlon={w.get('delta_lon_deg')}  Δlat={w.get('delta_lat_deg')}  "
                     f"sky={w.get('sky_error_arcsec')}″  {w.get('agreement')}")
    lines.append("")
    lines.append("NOTES")
    for n in tr.notes:
        lines.append(f"  · {n}")
    if tr.limb_note:
        lines.append(f"  · {tr.limb_note}")
    lines.append("")
    return "\n".join(lines)


def attach_winjupos_twin_to_package(
    package: Dict[str, Any],
    image: np.ndarray,
    *,
    nav: Optional[NavState] = None,
    cm_iii_deg: float,
    distance_au: float = 5.2,
    cm_source: str = "unknown",
    sub_lat_deg: float = 0.0,
    north_pa_deg: float = 0.0,
    user_time_iso: str = "",
    winjupos_manual_lon: Optional[float] = None,
    winjupos_manual_lat: Optional[float] = None,
    out_dir: Optional[Path] = None,
    run_limb_sensitivity: bool = True,
) -> TwinResult:
    tr = run_winjupos_twin(
        image,
        nav=nav,
        cm_iii_deg=cm_iii_deg,
        distance_au=distance_au,
        cm_source=cm_source,
        sub_lat_deg=sub_lat_deg,
        north_pa_deg=north_pa_deg,
        user_time_iso=user_time_iso,
        winjupos_manual_lon=winjupos_manual_lon,
        winjupos_manual_lat=winjupos_manual_lat,
        run_limb_sensitivity=run_limb_sensitivity,
    )
    d = tr.to_dict()
    # nest outer_vs_inner from limb if present in probes context
    package["winjupos_twin"] = d
    h = package.setdefault("headline", {})
    h["twin_definition"] = tr.twin_primary_definition
    h["twin_lon_iii_deg"] = tr.twin_lon_iii_deg
    h["twin_lat_deg"] = tr.twin_lat_deg
    h["twin_grade"] = tr.grade
    h["limb_outline_sky_spread_arcsec"] = tr.limb_sky_spread_arcsec
    h["limb_outline_lon_spread_deg"] = tr.limb_lon_spread_deg
    h["definition_lon_spread_deg"] = tr.definition_lon_spread_deg
    if out_dir is not None:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "winjupos_twin.json").write_text(json.dumps(d, indent=2, default=str), encoding="utf-8")
        (out_dir / "winjupos_twin.txt").write_text(format_twin_report(tr), encoding="utf-8")
        package.setdefault("gold_standard_files", {})["winjupos_twin_json"] = str(out_dir / "winjupos_twin.json")
        package["gold_standard_files"]["winjupos_twin_txt"] = str(out_dir / "winjupos_twin.txt")
    CONSOLE.ok(
        f"WinJUPOS twin: {tr.twin_primary_definition} lon={tr.twin_lon_iii_deg:.4f}°  "
        f"limb_spread={tr.limb_sky_spread_arcsec:.3f}″  {tr.grade}"
    )
    return tr

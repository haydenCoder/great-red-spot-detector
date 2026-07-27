#!/usr/bin/env python3
"""
WinJUPOS-style dual measure: automatic stack + human definition/outline choices
=============================================================================

JUPOS practice (Mettig Tips for Measurers; WinJUPOS outline workflow):
  • Auto outline is only a first guess — human fine-tunes limb size/position
  • Feature definition is a choice: dark core (centre) vs outline/edges (size)
  • Same mid-exposure UTC + CM discipline for absolute System III
  • Compare auto vs human to quantify definition / limb sensitivity

This module does not replace WinJUPOS; it encodes the same *discipline* so
Process and Synthetic can store both answers and an honest delta.
"""
from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from precision_engine import wrap_diff, sky_error_arcsec

# Definitions aligned with publish / twin policy (WinJUPOS-like)
DEFINITIONS = (
    "GS-MAP+RIM",   # recommended: core lon/lat + outer W–E rim for size
    "GS-MAP",       # dark core centre only
    "GS-BARY",      # intensity barycentre
    "OUTLINE_MID",  # mid between W/E edges (extent centre — not core)
    "PIPELINE",     # raw automatic stack
    "MANUAL",       # user-typed lon/lat (e.g. from WinJUPOS paste)
)

# Limb scale: 1.0 = auto; <1 tighter (like smaller outline frame); >1 larger
LIMB_SCALE_MIN = 0.90
LIMB_SCALE_MAX = 1.12


# Full tip sheet (JUPOS Tips for Measurers, WinJUPOS outline practice, BAA timing, SPICE)
ALL_MEASURE_TIPS: List[str] = [
    "Process full = AUTO limb (green) + BY EYE limb (cyan) in one job.",
    "JUPOS: automatic outline is only a first guess — always fine-tune by eye.",
    "WinJUPOS: arrows move outline; Page Up/Down change size; fit the true limb, not the belts.",
    "Put cyan on the planet edge; if cyan is too small/large, lon and lat both shift.",
    "Recommended: GS-MAP+RIM = core lon/lat (GS-MAP) + outer W–E rim for length.",
    "Publish longitude: dark core (GS-MAP). Outer rim = size only — not the published lon.",
    "Use edges (OUTLINE / W–E) for size/extent — not as a silent substitute for core lon.",
    "Same definition every night (core vs core) when comparing to WinJUPOS or past runs.",
    "Mid-exposure UTC of the stack only — not start time, not wall-clock now.",
    "BAA: ~0.6° System III per minute of timing error — clocks matter.",
    "Stacks should span only a few minutes for cartography (JUPOS image requirements).",
    "Trusted CM: SPICE / Horizons / WinJUPOS CML / override — not bare analytical for publish.",
    "NASA Horizons = geometry (CM, distance), NOT a GRS longitude catalog.",
    "Prefer red / visual-red channel for GRS contrast; blue is often weaker for this feature.",
    "Check orientation: N-up, E–W not mirrored (use flip checkboxes if stack is reversed).",
    "Paste your WinJUPOS lon/lat to test equality (Δsky ″) — that is the real accuracy check.",
    "dual_measure.json: large Δsky usually means definition or limb, not “NASA wrong.”",
    "Method soup / SOTA = scatter only — never the published centre.",
    "Quality flags: lat outside GRS band or untrusted CM → do not treat as absolute System III.",
    "On synthetics, truth_recovery sky_error_arcsec is the honest self-test metric.",
]


@dataclass
class HumanChoice:
    """User choices for the human measure pass (WinJUPOS-style)."""
    enabled: bool = True
    definition: str = "GS-MAP+RIM"
    limb_scale: float = 1.0          # scale auto limb radius
    limb_dx_frac: float = 0.0        # shift centre as fraction of a_eq (+E-ish in map sense)
    limb_dy_frac: float = 0.0        # + north on sky (up in image if N-up)
    flip_ew: bool = False
    flip_ns: bool = False
    manual_lon: Optional[float] = None
    manual_lat: Optional[float] = None
    use_as_publish: bool = True      # human pass becomes official publish
    notes: str = ""
    # provenance
    tips_applied: List[str] = field(default_factory=lambda: list(ALL_MEASURE_TIPS))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: Optional[Dict[str, Any]]) -> "HumanChoice":
        if not d:
            return cls(enabled=False)
        defn = str(d.get("definition") or "GS-MAP+RIM").strip().upper().replace(" ", "")
        if defn in ("GSMAP+RIM", "GS_MAP+RIM", "MAP+RIM"):
            defn = "GS-MAP+RIM"
        if defn not in DEFINITIONS:
            defn = "GS-MAP+RIM"
        scale = float(d.get("limb_scale") or 1.0)
        scale = max(LIMB_SCALE_MIN, min(LIMB_SCALE_MAX, scale))
        return cls(
            enabled=bool(d.get("enabled", True)),
            definition=defn,
            limb_scale=scale,
            limb_dx_frac=float(d.get("limb_dx_frac") or 0.0),
            limb_dy_frac=float(d.get("limb_dy_frac") or 0.0),
            flip_ew=bool(d.get("flip_ew")),
            flip_ns=bool(d.get("flip_ns")),
            manual_lon=_opt_float(d.get("manual_lon")),
            manual_lat=_opt_float(d.get("manual_lat")),
            use_as_publish=bool(d.get("use_as_publish", True)),
            notes=str(d.get("notes") or ""),
        )


def _opt_float(x) -> Optional[float]:
    if x is None or x == "":
        return None
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except Exception:
        return None


def snapshot_publish_block(package: Dict[str, Any], *, label: str) -> Dict[str, Any]:
    """Capture current publish/headline as an automatic or human snapshot."""
    pub = package.get("publish") or {}
    h = package.get("headline") or {}
    return {
        "label": label,
        "publish_definition": pub.get("publish_definition") or h.get("publish_definition"),
        "lon_iii_deg": pub.get("publish_lon_iii_deg", h.get("lon_iii_deg")),
        "lat_deg": pub.get("publish_lat_deg", h.get("lat_deg")),
        "length_deg": h.get("length_deg") or pub.get("length_deg"),
        "width_deg": h.get("width_deg") or pub.get("width_deg"),
        "west_edge_lon_iii_deg": h.get("west_edge_lon_iii_deg")
            or (pub.get("outer_rim") or {}).get("west_edge_lon_iii_deg"),
        "east_edge_lon_iii_deg": h.get("east_edge_lon_iii_deg")
            or (pub.get("outer_rim") or {}).get("east_edge_lon_iii_deg"),
        "extent_lon_deg": h.get("extent_lon_deg")
            or (pub.get("outer_rim") or {}).get("extent_lon_deg"),
        "cm_iii_deg": pub.get("cm_iii_deg", h.get("cm_iii_deg")),
        "cm_source": pub.get("cm_source", h.get("cm_source")),
        "pipeline_lon_iii_deg": pub.get("pipeline_lon_iii_deg", h.get("pipeline_lon_iii_deg")),
        "pipeline_lat_deg": pub.get("pipeline_lat_deg", h.get("pipeline_lat_deg")),
        "quality_grade": (pub.get("quality") or package.get("publish_quality") or {}).get("grade"),
        "publish_ok": (pub.get("quality") or package.get("publish_quality") or {}).get("publish_ok"),
        "outer_rim": pub.get("outer_rim") or h.get("outer_rim") or package.get("outer_rim"),
    }


def compare_measure_snapshots(
    auto: Dict[str, Any],
    human: Dict[str, Any],
    *,
    distance_au: float = 5.2,
) -> Dict[str, Any]:
    """Δ between automatic and human answers (definition/limb sensitivity)."""
    a_lon = _opt_float(auto.get("lon_iii_deg"))
    h_lon = _opt_float(human.get("lon_iii_deg"))
    a_lat = _opt_float(auto.get("lat_deg"))
    h_lat = _opt_float(human.get("lat_deg"))
    out: Dict[str, Any] = {
        "compared": a_lon is not None and h_lon is not None,
        "auto_definition": auto.get("publish_definition"),
        "human_definition": human.get("publish_definition"),
    }
    if a_lon is None or h_lon is None:
        out["note"] = "missing lon on auto or human snapshot"
        return out
    dlon = wrap_diff(float(h_lon), float(a_lon))
    dlat = (float(h_lat) - float(a_lat)) if (h_lat is not None and a_lat is not None) else 0.0
    lat0 = float(h_lat if h_lat is not None else (a_lat if a_lat is not None else -22.0))
    sky = sky_error_arcsec(dlon, dlat, lat0, float(distance_au or 5.2))
    out.update({
        "dlon_human_minus_auto_deg": dlon,
        "dlat_human_minus_auto_deg": dlat,
        "sky_delta_arcsec": sky,
        "agreement": (
            "MATCH" if sky <= 1.0
            else ("NEAR" if sky <= 2.0 else ("FAIR" if sky <= 5.0 else "DIFFERENT"))
        ),
        "note": (
            "Large Δ usually means definition (core vs outline) or limb scale, "
            "not necessarily a bug — same as WinJUPOS core vs edge choice."
        ),
    })
    return out


def apply_image_flips(image, flip_ew: bool, flip_ns: bool):
    """
    Flip image for orientation (common WinJUPOS prep when stack is mirrored).

    Handles mono (H,W), HWC (H,W,C), and CHW (C,H,W). Never flips colour channels.
    """
    import numpy as np
    im = np.asarray(image)
    if not flip_ew and not flip_ns:
        return im
    # CHW: first dim is channels (3 or 4) and smaller than spatial
    is_chw = (
        im.ndim == 3
        and im.shape[0] in (3, 4)
        and im.shape[0] < min(im.shape[1], im.shape[2])
    )
    is_hwc = im.ndim == 3 and im.shape[-1] in (3, 4) and not is_chw
    if flip_ew:
        if im.ndim == 2:
            im = im[:, ::-1]
        elif is_chw:
            im = im[:, :, ::-1]  # flip width
        elif is_hwc:
            im = im[:, ::-1, :]  # flip width, not channels
        else:
            im = np.flip(im, axis=-1)
    if flip_ns:
        if im.ndim == 2:
            im = im[::-1, :]
        elif is_chw:
            im = im[:, ::-1, :]  # flip height
        elif is_hwc:
            im = im[::-1, :, :]
        else:
            im = np.flip(im, axis=0)
    return im


def adjust_nav_like_outline(nav, choice: HumanChoice):
    """
    Emulate WinJUPOS outline fine-tune: scale radius and shift centre.
    Returns a shallow-copied NavState-like object when possible.
    """
    import copy
    n = copy.copy(nav)
    s = float(choice.limb_scale)
    a = float(getattr(n, "a_eq_px", 100.0))
    n.a_eq_px = a * s
    # shift: +dx moves disk centre (fraction of radius)
    n.xc = float(getattr(n, "xc", 0.0)) + float(choice.limb_dx_frac) * a
    n.yc = float(getattr(n, "yc", 0.0)) - float(choice.limb_dy_frac) * a  # +dy = north = up
    return n


def extract_outer_rim(package: Dict[str, Any]) -> Dict[str, Any]:
    """
    Outer-rim (W–E edges) metrics from twin/gold — for size, not published lon.
    WinJUPOS-style: core position and rim length are different quantities.
    """
    twin = package.get("winjupos_twin") or {}
    gs = package.get("gold_standard") or {}
    we = twin.get("west_edge_lon")
    if we is None:
        we = twin.get("west_edge_lon_iii_deg")
    ee = twin.get("east_edge_lon")
    if ee is None:
        ee = twin.get("east_edge_lon_iii_deg")
    extent = twin.get("extent_lon_deg")
    if extent is None and we is not None and ee is not None:
        try:
            extent = abs(wrap_diff(float(ee), float(we)))
        except Exception:
            extent = None
    if extent is None:
        extent = gs.get("extent_lon_deg")
    if we is None:
        we = gs.get("west_edge_lon_iii_deg")
    if ee is None:
        ee = gs.get("east_edge_lon_iii_deg")
    mid = None
    try:
        if we is not None and ee is not None:
            wr, er = math.radians(float(we)), math.radians(float(ee))
            mid = (math.degrees(math.atan2(
                0.5 * (math.sin(wr) + math.sin(er)),
                0.5 * (math.cos(wr) + math.cos(er)),
            )) + 360.0) % 360.0
    except Exception:
        mid = None
    # NS width from headline / research if present
    h = package.get("headline") or {}
    width_deg = h.get("width_deg")
    length_deg = float(extent) if extent is not None else h.get("length_deg")
    return {
        "role": "outer_rim_size_only",
        "west_edge_lon_iii_deg": we,
        "east_edge_lon_iii_deg": ee,
        "outline_mid_lon_iii_deg": mid,
        "extent_lon_deg": extent,
        "length_deg_from_rim": length_deg,
        "width_deg": width_deg,
        "note": (
            "Outer rim defines W–E extent (size). "
            "Do not use rim mid as the published GRS centre unless definition=OUTLINE_MID."
        ),
    }


def attach_gs_map_plus_rim(package: Dict[str, Any], *, lon: Any, lat: Any) -> None:
    """Write combined GS-MAP centre + outer rim size into publish/headline."""
    rim = extract_outer_rim(package)
    pub = package.setdefault("publish", {})
    h = package.setdefault("headline", {})
    pub["gs_map_lon_iii_deg"] = lon
    pub["gs_map_lat_deg"] = lat
    pub["outer_rim"] = rim
    if rim.get("length_deg_from_rim") is not None:
        pub["length_deg"] = rim["length_deg_from_rim"]
        h["length_deg"] = rim["length_deg_from_rim"]
    if rim.get("width_deg") is not None:
        pub["width_deg"] = rim["width_deg"]
        h["width_deg"] = rim["width_deg"]
    h["outer_rim"] = rim
    h["gs_map_lon_iii_deg"] = lon
    h["west_edge_lon_iii_deg"] = rim.get("west_edge_lon_iii_deg")
    h["east_edge_lon_iii_deg"] = rim.get("east_edge_lon_iii_deg")
    h["extent_lon_deg"] = rim.get("extent_lon_deg")
    package["outer_rim"] = rim


def force_publish_definition(package: Dict[str, Any], choice: HumanChoice) -> Dict[str, Any]:
    """
    Re-apply publish policy with a forced definition (human pass without full remeasure).
    GS-MAP+RIM: published centre = GS-MAP core; size = outer W–E rim (WinJUPOS-like).
    MANUAL uses typed lon/lat as the published centre.
    """
    twin = package.get("winjupos_twin") or {}
    gs = package.get("gold_standard") or {}
    h = package.setdefault("headline", {})
    pub = package.setdefault("publish", {})
    cm = pub.get("cm_iii_deg", h.get("cm_iii_deg"))
    cm_source = str(pub.get("cm_source") or h.get("cm_source") or "")

    defn = choice.definition
    if defn in ("GSMAP+RIM", "GS_MAP+RIM", "MAP+RIM"):
        defn = "GS-MAP+RIM"
    lon = lat = None
    use_rim_size = False

    if defn == "MANUAL":
        lon, lat = choice.manual_lon, choice.manual_lat
    elif defn in ("GS-MAP", "GS-MAP+RIM"):
        use_rim_size = defn == "GS-MAP+RIM"
        lon = twin.get("gs_map_lon")
        lat = twin.get("gs_map_lat")
        if lon is None and str(gs.get("primary_definition") or "").startswith("GS-MAP"):
            lon, lat = gs.get("primary_lon_iii_deg"), gs.get("primary_lat_deg")
        if lon is None:
            lon, lat = gs.get("primary_lon_iii_deg"), gs.get("primary_lat_deg")
    elif defn == "GS-BARY":
        lon = twin.get("gs_bary_lon")
        lat = twin.get("gs_bary_lat")
        if lon is None and "BARY" in str(gs.get("primary_definition") or "").upper():
            lon, lat = gs.get("primary_lon_iii_deg"), gs.get("primary_lat_deg")
    elif defn == "OUTLINE_MID":
        we = twin.get("west_edge_lon") or twin.get("west_edge_lon_iii_deg")
        ee = twin.get("east_edge_lon") or twin.get("east_edge_lon_iii_deg")
        try:
            if we is not None and ee is not None:
                wr, er = math.radians(float(we)), math.radians(float(ee))
                lon = (math.degrees(math.atan2(
                    0.5 * (math.sin(wr) + math.sin(er)),
                    0.5 * (math.cos(wr) + math.cos(er)),
                )) + 360.0) % 360.0
                lat = twin.get("gs_map_lat") or twin.get("twin_lat_deg") or h.get("lat_deg")
        except Exception:
            lon = lat = None
    elif defn == "PIPELINE":
        lon = pub.get("pipeline_lon_iii_deg") or h.get("pipeline_lon_iii_deg") or h.get("lon_iii_deg")
        lat = pub.get("pipeline_lat_deg") or h.get("pipeline_lat_deg") or h.get("lat_deg")

    # Fallback chain
    if lon is None:
        lon = twin.get("gs_map_lon") or twin.get("twin_lon_iii_deg") or h.get("lon_iii_deg")
        lat = twin.get("gs_map_lat") or twin.get("twin_lat_deg") or h.get("lat_deg")
        defn = defn if lon is not None else "PIPELINE"

    pub["publish_definition"] = defn
    pub["publish_lon_iii_deg"] = lon
    pub["publish_lat_deg"] = lat
    pub["human_forced"] = True
    if use_rim_size or defn == "GS-MAP+RIM":
        attach_gs_map_plus_rim(package, lon=lon, lat=lat)
        rim = package.get("outer_rim") or {}
        L = rim.get("extent_lon_deg")
        cite_size = f" length_W–E={L}° (outer rim)" if L is not None else ""
        pub["how_to_cite"] = (
            f"GRS GS-MAP+RIM centre lon={lon}° lat={lat}°{cite_size} "
            f"(CM III={cm}° source={cm_source}; core=GS-MAP, size=outer rim). "
            "Method soup not used for the published centre."
        )
        h["primary_method"] = "GS-MAP+RIM"
    else:
        # still attach rim for reporting even if not size-primary
        rim = extract_outer_rim(package)
        package["outer_rim"] = rim
        pub["outer_rim"] = rim
        h["outer_rim"] = rim
        pub["how_to_cite"] = (
            f"GRS {defn} lon={lon}° lat={lat}° "
            f"(CM III={cm}° source={cm_source}; human-choice pass). "
            "Method soup not used for the published centre."
        )
        h["primary_method"] = defn

    h["publish_definition"] = defn
    h["publish_lon_iii_deg"] = lon
    h["publish_lat_deg"] = lat
    h["lon_iii_deg"] = lon
    h["lat_deg"] = lat
    h["human_choice"] = True

    try:
        from accuracy_gates import assess_publish_quality
        q = assess_publish_quality(package)
        package["publish_quality"] = q
        pub["quality"] = q
        pub["publish_ok"] = q.get("publish_ok")
        pub["absolute_ok"] = q.get("absolute_ok")
        h["quality_grade"] = q.get("grade")
        h["publish_ok"] = q.get("publish_ok")
    except Exception:
        pass

    package["publish"] = pub
    package["headline"] = h
    return package


def build_dual_block(
    package: Dict[str, Any],
    auto_snap: Dict[str, Any],
    human_snap: Dict[str, Any],
    choice: HumanChoice,
) -> Dict[str, Any]:
    dist = float(
        (package.get("publish") or {}).get("distance_au")
        or (package.get("headline") or {}).get("distance_au")
        or 5.2
    )
    cmp_ = compare_measure_snapshots(auto_snap, human_snap, distance_au=dist)
    return {
        "mode": "auto_plus_human",
        "winjupos_style": True,
        "human_choice": choice.to_dict(),
        "automatic": auto_snap,
        "human": human_snap,
        "comparison": cmp_,
        "official": "human" if choice.use_as_publish else "automatic",
        "guidance": choice.tips_applied,
    }


def write_dual_reports(out_dir: Path, dual: Dict[str, Any]) -> None:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "dual_measure.json").write_text(json.dumps(dual, indent=2, default=str), encoding="utf-8")
    a = dual.get("automatic") or {}
    h = dual.get("human") or {}
    c = dual.get("comparison") or {}
    lines = [
        "DUAL MEASURE (WinJUPOS-style): AUTOMATIC + HUMAN CHOICE",
        "=" * 60,
        f"Official publish source: {dual.get('official')}",
        "",
        "AUTOMATIC",
        f"  definition  {a.get('publish_definition')}",
        f"  lon III     {a.get('lon_iii_deg')}",
        f"  lat         {a.get('lat_deg')}",
        "",
        "HUMAN CHOICE",
        f"  definition  {h.get('publish_definition')}",
        f"  lon III     {h.get('lon_iii_deg')}  (centre — GS-MAP if GS-MAP+RIM)",
        f"  lat         {h.get('lat_deg')}",
        "",
        "OUTER RIM (size — not published lon)",
        f"  west edge   {h.get('west_edge_lon_iii_deg')}",
        f"  east edge   {h.get('east_edge_lon_iii_deg')}",
        f"  extent W–E  {h.get('extent_lon_deg')} °",
        f"  length      {h.get('length_deg')} °",
        "",
        "COMPARISON (human − automatic)",
        f"  Δlon        {c.get('dlon_human_minus_auto_deg')} °",
        f"  Δlat        {c.get('dlat_human_minus_auto_deg')} °",
        f"  Δsky        {c.get('sky_delta_arcsec')} ″",
        f"  agreement   {c.get('agreement')}",
        f"  note        {c.get('note')}",
        "",
        "TIPS (JUPOS / WinJUPOS practice)",
    ]
    for t in dual.get("guidance") or []:
        lines.append(f"  · {t}")
    lines.append("")
    (out_dir / "dual_measure.txt").write_text("\n".join(lines), encoding="utf-8")


def _load_preview_rgb(path: Optional[Path] = None, array=None, max_side: int = 720):
    """Load RGB uint8 preview for limb editor (path or mono/RGB array)."""
    import numpy as np
    from PIL import Image

    if path is not None and Path(path).exists():
        p = Path(path)
        ext = p.suffix.lower()
        if ext in (".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"):
            im = Image.open(p).convert("RGB")
            arr = np.asarray(im)
        else:
            try:
                import grs_complete_system as grs
                data, _ = grs.read_fits(p)
                a = np.asarray(data, dtype=np.float64)
                if a.ndim == 3 and a.shape[0] in (3, 4):
                    a = np.moveaxis(a[:3], 0, -1)
                elif a.ndim == 2:
                    a = np.stack([a, a, a], axis=-1)
                lo, hi = np.percentile(a, (1, 99.5))
                if hi <= lo:
                    hi = lo + 1e-6
                arr = np.clip((a - lo) / (hi - lo), 0, 1)
                arr = (arr * 255).astype(np.uint8)
            except Exception:
                arr = np.zeros((400, 400, 3), dtype=np.uint8)
    elif array is not None:
        a = np.asarray(array, dtype=np.float64)
        if a.ndim == 3 and a.shape[0] in (3, 4) and a.shape[0] < a.shape[-1]:
            a = np.moveaxis(a[:3], 0, -1)
        elif a.ndim == 2:
            a = np.stack([a, a, a], axis=-1)
        if a.dtype != np.uint8:
            lo, hi = np.percentile(a, (1, 99.5))
            if hi <= lo:
                hi = lo + 1e-6
            a = np.clip((a - lo) / (hi - lo), 0, 1) * 255
            arr = a.astype(np.uint8)
        else:
            arr = a
    else:
        arr = np.zeros((400, 400, 3), dtype=np.uint8)

    h, w = arr.shape[:2]
    scale = min(1.0, max_side / float(max(h, w)))
    if scale < 1.0:
        nh, nw = max(1, int(h * scale)), max(1, int(w * scale))
        im = Image.fromarray(arr, "RGB").resize((nw, nh), Image.Resampling.BILINEAR)
        arr = np.asarray(im)
        return arr, scale
    return arr, 1.0


def prompt_human_choice_dialog(
    parent,
    *,
    title: str = "Limb outline: AUTO + BY EYE (WinJUPOS-style)",
    preset: Optional[HumanChoice] = None,
    wj_lon: Optional[float] = None,
    wj_lat: Optional[float] = None,
    image_path: Optional[Path] = None,
    image_array=None,
) -> Optional[HumanChoice]:
    """
    Modal dialog: automatic limb outline (green) + by-eye outline (cyan).

    Keys (WinJUPOS-like): arrows = move, PgUp/PgDn = size, R = reset to auto.
    Returns HumanChoice or None if cancelled.
    """
    import tkinter as tk
    from tkinter import ttk, messagebox
    import numpy as np
    from PIL import Image, ImageDraw, ImageTk

    preset = preset or HumanChoice()
    result: Dict[str, Any] = {"ok": False, "choice": None}

    # --- auto limb on full-res (or preview coords scaled back) ---
    auto_xc = auto_yc = auto_a = None
    disp_rgb, disp_scale = _load_preview_rgb(image_path, image_array, max_side=700)
    dh, dw = disp_rgb.shape[:2]

    try:
        from precision_engine import fit_limb_nav, FLAT
        # fit on display image mono for speed / same frame user sees
        mono = (
            0.299 * disp_rgb[:, :, 0]
            + 0.587 * disp_rgb[:, :, 1]
            + 0.114 * disp_rgb[:, :, 2]
        ).astype(np.float64)
        nav0 = fit_limb_nav(mono, cm_iii_deg=0.0, distance_au=5.2)
        auto_xc, auto_yc, auto_a = float(nav0.xc), float(nav0.yc), float(nav0.a_eq_px)
    except Exception:
        auto_xc, auto_yc = dw / 2.0, dh / 2.0
        auto_a = min(dw, dh) * 0.38

    # human outline state in *display* pixels
    state = {
        "xc": auto_xc + float(preset.limb_dx_frac) * auto_a,
        "yc": auto_yc - float(preset.limb_dy_frac) * auto_a,
        "a": auto_a * float(preset.limb_scale),
        "photo": None,
    }

    win = tk.Toplevel(parent)
    win.title(title)
    win.transient(parent)
    win.grab_set()
    win.geometry("980x720")

    root = ttk.Frame(win, padding=8)
    root.pack(fill=tk.BOTH, expand=True)

    left = ttk.Frame(root)
    left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
    right = ttk.Frame(root, width=300)
    right.pack(side=tk.RIGHT, fill=tk.Y, padx=(10, 0))

    ttk.Label(
        left,
        text="GREEN = automatic limb  ·  CYAN = your by-eye outline  ·  "
             "Arrows move · PgUp/PgDn size · R reset to auto · drag centre with mouse",
        wraplength=640,
    ).pack(anchor=tk.W)

    canvas = tk.Canvas(left, width=dw, height=dh, bg="#111", highlightthickness=0)
    canvas.pack(pady=6)

    status = tk.StringVar(value="")
    ttk.Label(left, textvariable=status).pack(anchor=tk.W)

    def_var = tk.StringVar(value=preset.definition)
    ttk.Label(right, text="GRS definition (recommended: GS-MAP+RIM)").pack(anchor=tk.W)
    ttk.Combobox(
        right, textvariable=def_var, values=list(DEFINITIONS), state="readonly", width=26
    ).pack(anchor=tk.W, pady=2)
    ttk.Label(
        right,
        text="GS-MAP+RIM = core lon/lat + outer W–E size\n"
             "GS-MAP = dark core only\n"
             "OUTLINE_MID = W/E edge mid (not core)\n"
             "MANUAL = type lon/lat below",
        wraplength=280,
        foreground="#444",
    ).pack(anchor=tk.W, pady=(0, 8))

    flip_ew = tk.BooleanVar(value=preset.flip_ew)
    flip_ns = tk.BooleanVar(value=preset.flip_ns)
    ttk.Checkbutton(right, text="Flip E–W (mirror)", variable=flip_ew).pack(anchor=tk.W)
    ttk.Checkbutton(right, text="Flip N–S", variable=flip_ns).pack(anchor=tk.W)

    lon_var = tk.StringVar(value="" if preset.manual_lon is None else str(preset.manual_lon))
    lat_var = tk.StringVar(value="" if preset.manual_lat is None else str(preset.manual_lat))
    if wj_lon is not None and not lon_var.get():
        lon_var.set(str(wj_lon))
    if wj_lat is not None and not lat_var.get():
        lat_var.set(str(wj_lat))
    ttk.Label(right, text="Manual lon / lat (MANUAL def)").pack(anchor=tk.W, pady=(8, 0))
    r2 = ttk.Frame(right)
    r2.pack(fill=tk.X)
    ttk.Entry(r2, textvariable=lon_var, width=12).pack(side=tk.LEFT)
    ttk.Entry(r2, textvariable=lat_var, width=12).pack(side=tk.LEFT, padx=4)

    use_pub = tk.BooleanVar(value=preset.use_as_publish)
    ttk.Checkbutton(
        right,
        text="Human outline/definition = official PUBLISH",
        variable=use_pub,
    ).pack(anchor=tk.W, pady=8)

    ttk.Label(
        right,
        text="Two outlines:\n"
             "• AUTO (green) from software limb fit\n"
             "• BY EYE (cyan) — adjust until it sits\n"
             "  on the planet edge like WinJUPOS\n\n"
             "Both are used: auto pass + human pass.",
        wraplength=280,
        foreground="#333",
    ).pack(anchor=tk.W, pady=8)

    def _draw():
        from precision_engine import FLAT
        base = Image.fromarray(disp_rgb.copy(), "RGB")
        # apply flip preview only on draw for human view
        if flip_ew.get():
            base = base.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
        if flip_ns.get():
            base = base.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
        draw = ImageDraw.Draw(base)
        # auto green
        b_auto = auto_a * (1.0 - FLAT)
        draw.ellipse(
            [auto_xc - auto_a, auto_yc - b_auto, auto_xc + auto_a, auto_yc + b_auto],
            outline=(40, 220, 80),
            width=2,
        )
        draw.line([auto_xc - 8, auto_yc, auto_xc + 8, auto_yc], fill=(40, 220, 80), width=1)
        draw.line([auto_xc, auto_yc - 8, auto_xc, auto_yc + 8], fill=(40, 220, 80), width=1)
        # human cyan (if flipped, still in image coords of flipped image — approximate:
        # we re-map human coords when flipped for simplicity recompute from state on unflipped then flip image)
        hx, hy, ha = state["xc"], state["yc"], state["a"]
        if flip_ew.get():
            hx = dw - 1 - hx
        if flip_ns.get():
            hy = dh - 1 - hy
        hb = ha * (1.0 - FLAT)
        draw.ellipse(
            [hx - ha, hy - hb, hx + ha, hy + hb],
            outline=(0, 220, 255),
            width=3,
        )
        draw.line([hx - 10, hy, hx + 10, hy], fill=(0, 220, 255), width=2)
        draw.line([hx, hy - 10, hx, hy + 10], fill=(0, 220, 255), width=2)
        # equator line for human
        draw.line([hx - ha, hy, hx + ha, hy], fill=(0, 180, 220), width=1)

        state["photo"] = ImageTk.PhotoImage(base)
        canvas.delete("all")
        canvas.create_image(0, 0, anchor=tk.NW, image=state["photo"])
        # legend
        canvas.create_text(8, 12, anchor=tk.NW, fill="#3cdc50", text="AUTO limb", font=("Helvetica", 11, "bold"))
        canvas.create_text(8, 28, anchor=tk.NW, fill="#00dcff", text="BY EYE limb", font=("Helvetica", 11, "bold"))

        scale = state["a"] / (auto_a + 1e-9)
        dx_frac = (state["xc"] - auto_xc) / (auto_a + 1e-9)
        dy_frac = (auto_yc - state["yc"]) / (auto_a + 1e-9)
        status.set(
            f"AUTO  xc={auto_xc:.1f} yc={auto_yc:.1f} a={auto_a:.1f}px   |   "
            f"BY EYE  xc={state['xc']:.1f} yc={state['yc']:.1f} a={state['a']:.1f}px   "
            f"scale={scale:.3f}  dx={dx_frac:+.3f}  dy={dy_frac:+.3f}"
        )

    def _reset_to_auto():
        state["xc"], state["yc"], state["a"] = auto_xc, auto_yc, auto_a
        _draw()

    def _nudge(dx=0, dy=0, dscale=0.0):
        state["xc"] += dx
        state["yc"] += dy
        if dscale:
            state["a"] = max(10.0, state["a"] * (1.0 + dscale))
        _draw()

    def on_key(e):
        k = e.keysym
        step = 2 if not (e.state & 0x1) else 8  # Shift = coarse
        if k in ("Left", "KP_Left"):
            _nudge(dx=-step)
        elif k in ("Right", "KP_Right"):
            _nudge(dx=step)
        elif k in ("Up", "KP_Up"):
            _nudge(dy=-step)
        elif k in ("Down", "KP_Down"):
            _nudge(dy=step)
        elif k in ("Prior", "Page_Up", "KP_Prior"):
            _nudge(dscale=0.015 if not (e.state & 0x1) else 0.04)
        elif k in ("Next", "Page_Down", "KP_Next"):
            _nudge(dscale=-0.015 if not (e.state & 0x1) else -0.04)
        elif k in ("r", "R"):
            _reset_to_auto()

    drag = {"on": False, "x0": 0, "y0": 0, "xc0": 0.0, "yc0": 0.0}

    def on_press(e):
        drag["on"] = True
        drag["x0"], drag["y0"] = e.x, e.y
        drag["xc0"], drag["yc0"] = state["xc"], state["yc"]

    def on_drag(e):
        if not drag["on"]:
            return
        # account for flip when dragging in canvas coords of displayed image
        ddx, ddy = e.x - drag["x0"], e.y - drag["y0"]
        if flip_ew.get():
            ddx = -ddx
        if flip_ns.get():
            ddy = -ddy
        state["xc"] = drag["xc0"] + ddx
        state["yc"] = drag["yc0"] + ddy
        _draw()

    def on_release(_e):
        drag["on"] = False

    canvas.bind("<ButtonPress-1>", on_press)
    canvas.bind("<B1-Motion>", on_drag)
    canvas.bind("<ButtonRelease-1>", on_release)
    win.bind("<Key>", on_key)
    flip_ew.trace_add("write", lambda *_: _draw())
    flip_ns.trace_add("write", lambda *_: _draw())

    btns = ttk.Frame(right)
    btns.pack(fill=tk.X, pady=12)

    def _choice_from_ui(enabled: bool) -> HumanChoice:
        scale = float(state["a"] / (auto_a + 1e-9))
        scale = max(LIMB_SCALE_MIN, min(LIMB_SCALE_MAX, scale))
        dx_frac = float((state["xc"] - auto_xc) / (auto_a + 1e-9))
        dy_frac = float((auto_yc - state["yc"]) / (auto_a + 1e-9))
        return HumanChoice(
            enabled=enabled,
            definition=def_var.get().strip().upper() or "GS-MAP",
            limb_scale=scale,
            limb_dx_frac=dx_frac,
            limb_dy_frac=dy_frac,
            flip_ew=bool(flip_ew.get()),
            flip_ns=bool(flip_ns.get()),
            manual_lon=_opt_float(lon_var.get()),
            manual_lat=_opt_float(lat_var.get()),
            use_as_publish=bool(use_pub.get()),
            notes=f"by_eye_limb auto_a={auto_a:.2f} human_a={state['a']:.2f}",
        )

    def on_ok():
        defn = def_var.get().strip().upper()
        if defn == "MANUAL" and _opt_float(lon_var.get()) is None:
            messagebox.showwarning("Manual", "MANUAL needs longitude.", parent=win)
            return
        result["ok"] = True
        result["choice"] = _choice_from_ui(True)
        win.destroy()

    def on_auto_only():
        result["ok"] = True
        result["choice"] = HumanChoice(enabled=False)
        win.destroy()

    def on_cancel():
        result["ok"] = False
        win.destroy()

    ttk.Button(btns, text="Reset cyan → auto", command=_reset_to_auto).pack(fill=tk.X, pady=2)
    ttk.Button(btns, text="Use BOTH outlines (auto + by eye)", command=on_ok).pack(fill=tk.X, pady=2)
    ttk.Button(btns, text="Automatic outline only", command=on_auto_only).pack(fill=tk.X, pady=2)
    ttk.Button(btns, text="Cancel", command=on_cancel).pack(fill=tk.X, pady=2)

    _draw()
    canvas.focus_set()
    parent.wait_window(win)
    if not result["ok"]:
        return None
    return result["choice"]

#!/usr/bin/env python3
"""
Compact human reports for GRS jobs — key numbers only for fast scrolling.
Full machine data stays in job_result.json.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

ACCURACY_TIPS: List[str] = []


def _f(v: Any, d: int = 4) -> str:
    if v is None:
        return "—"
    try:
        x = float(v)
        if x != x:
            return "nan"
        return f"{x:.{d}f}"
    except Exception:
        return str(v)


def _s(v: Any) -> str:
    return "—" if v is None else str(v)


def format_human_report(package: Dict[str, Any]) -> str:
    """Short full-result text: publish numbers first, no essay."""
    pkg = package or {}
    h = pkg.get("headline") or {}
    pub = pkg.get("publish") or {}
    ch = pkg.get("champion") or {}
    pe = pkg.get("pro_ephemeris") or (pkg.get("stages") or {}).get("pro_ephemeris") or {}
    if not isinstance(pe, dict):
        pe = {}
    dual = pkg.get("dual_measure") or {}
    eq = (pub.get("winjupos_equality") or {}) if isinstance(pub, dict) else {}
    tr = pkg.get("truth_recovery") or {}

    lon = pub.get("publish_lon_iii_deg", h.get("publish_lon_iii_deg", h.get("lon_iii_deg")))
    lat = pub.get("publish_lat_deg", h.get("publish_lat_deg", h.get("lat_deg")))
    lat_g = pub.get("publish_lat_planetographic_deg", h.get("lat_planetographic_deg"))
    cm = pub.get("cm_iii_deg", h.get("cm_iii_deg", pe.get("cm_iii_deg")))
    cm_src = pub.get("cm_source", h.get("cm_source", pe.get("cm_source")))
    definition = pub.get("publish_definition", h.get("publish_definition", h.get("primary_method")))
    grade = (
        h.get("superduper_grade")
        or ch.get("grade")
        or h.get("champion_grade")
        or h.get("grade")
        or "—"
    )
    utc = h.get("user_time") or h.get("synth_epoch") or pkg.get("user_time") or "—"
    dist = pe.get("distance_au") or h.get("distance_au")
    sig = (
        pub.get("publish_sigma_sky_arcsec")
        or h.get("champion_sigma_sky_arcsec")
        or h.get("sigma_total_sky_arcsec")
    )
    ew = h.get("extent_ew_deg") or ch.get("extent_ew_deg") or h.get("length_deg")

    lines = [
        "RESULTS",
        "=======",
        f"UTC        {_s(utc)}",
        f"lon_III    {_f(lon, 4)} °",
        f"lat_c      {_f(lat, 3)} °",
        f"lat_g      {_f(lat_g, 3)} °",
        f"CM_III     {_f(cm, 4)} °  [{_s(cm_src)}]",
        f"def        {_s(definition)}",
        f"grade      {_s(grade)}",
        f"σ_sky      {_f(sig, 2)} ″",
        f"EW         {_f(ew, 2)} °",
        f"dist       {_f(dist, 5)} AU" if dist is not None else None,
        f"vs_WJ      {_s(eq.get('agreement') or h.get('winjupos_agreement'))}  "
        f"Δsky={_f(eq.get('sky_error_arcsec') if eq.get('sky_error_arcsec') is not None else h.get('vs_winjupos_sky_arcsec'), 2)} ″",
        f"job        {_s(pkg.get('job_id') or pkg.get('output_folder') or pkg.get('output_dir'))}",
        "",
    ]

    if dual:
        a = dual.get("automatic") or {}
        hu = dual.get("human") or {}
        cmp_ = dual.get("comparison") or {}
        lines += [
            "DUAL",
            f"  use   {_s(dual.get('official'))}",
            f"  auto  {_f(a.get('lon_iii_deg'), 4)} / {_f(a.get('lat_deg'), 3)}",
            f"  hand  {_f(hu.get('lon_iii_deg'), 4)} / {_f(hu.get('lat_deg'), 3)}",
            f"  Δsky  {_f(cmp_.get('sky_delta_arcsec'), 2)} ″  ({_s(cmp_.get('agreement'))})",
            "",
        ]

    if tr and tr.get("sky_error_arcsec") is not None:
        lines += [
            "TRUTH (synth only)",
            f"  Δsky  {_f(tr.get('sky_error_arcsec'), 3)} ″  grade={_s(tr.get('grade'))}",
            "",
        ]

    # Optional one-line cite
    cite = h.get("superduper_citation") or h.get("citation_line") or h.get("how_to_cite")
    if cite:
        lines += [f"cite  {cite}", ""]

    lines.append("(full JSON → job_result.json)")
    lines.append("")
    return "\n".join(x for x in lines if x is not None)


def write_human_report(path: Union[str, Path], package: Dict[str, Any]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(format_human_report(package), encoding="utf-8")
    return path


def format_nasa_txt(comp_dict: Dict[str, Any]) -> str:
    """Short geometry compare text."""
    m = (comp_dict or {}).get("measured") or {}
    r = (comp_dict or {}).get("reference") or {}
    d = (comp_dict or {}).get("deltas") or {}
    return "\n".join([
        "GEOMETRY",
        "========",
        f"meas lon/lat  {_f(m.get('lon_iii_deg'), 4)} / {_f(m.get('lat_deg'), 3)}",
        f"ctx  lon/lat  {_f(r.get('lon_iii_deg'), 4)} / {_f(r.get('lat_deg'), 3)}",
        f"Δ             {_f(d.get('lon_iii_deg'), 3)} / {_f(d.get('lat_deg'), 3)}",
        f"grade         {_s((comp_dict or {}).get('grade'))}",
        "",
    ])

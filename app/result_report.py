#!/usr/bin/env python3
"""
Result text for GRS jobs.

- format_dashboard_table: small table for the Dashboard tab
- format_human_report: full results (everything useful) for Full Results / FULL_REPORT.txt
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

ACCURACY_TIPS: List[str] = [
    "Use mid-exposure UTC; ~0.6° System III per minute of time error.",
    "Publish GS-ORANGE / GS-MAP core — not soup / SOTA scatter.",
    "Paste WinJUPOS core lon/lat for Δsky check.",
    "Compare φ_g (planetographic) to WinJUPOS latitude.",
    "Horizons/SPICE = planet geometry only, not GRS lon catalogue.",
]


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


def _line(label: str, value: Any, unit: str = "", width: int = 22) -> str:
    u = f" {unit}" if unit else ""
    return f"  {label:<{width}} {_s(value)}{u}"


def _pull(package: Dict[str, Any]) -> Dict[str, Any]:
    """Common fields for dashboard / full report."""
    pkg = package or {}
    h = pkg.get("headline") or {}
    pub = pkg.get("publish") or {}
    ch = pkg.get("champion") or {}
    pe = pkg.get("pro_ephemeris") or {}
    if not isinstance(pe, dict):
        pe = {}
    eq = (pub.get("winjupos_equality") or {}) if isinstance(pub, dict) else {}
    dual = pkg.get("dual_measure") or {}
    return {
        "pkg": pkg,
        "h": h,
        "pub": pub,
        "ch": ch,
        "pe": pe,
        "eq": eq,
        "dual": dual,
        "lon": pub.get("publish_lon_iii_deg", h.get("publish_lon_iii_deg", h.get("lon_iii_deg"))),
        "lat": pub.get("publish_lat_deg", h.get("publish_lat_deg", h.get("lat_deg"))),
        "lat_g": pub.get("publish_lat_planetographic_deg", h.get("lat_planetographic_deg")),
        "cm": pub.get("cm_iii_deg", h.get("cm_iii_deg", pe.get("cm_iii_deg"))),
        "cm_src": pub.get("cm_source", h.get("cm_source", pe.get("cm_source"))),
        "definition": pub.get("publish_definition", h.get("publish_definition", h.get("primary_method"))),
        "grade": (
            h.get("superduper_grade")
            or ch.get("grade")
            or h.get("champion_grade")
            or h.get("grade")
            or "—"
        ),
        "utc": h.get("user_time") or h.get("synth_epoch") or pkg.get("user_time") or "—",
        "dist": pe.get("distance_au") or h.get("distance_au"),
        "sig": (
            pub.get("publish_sigma_sky_arcsec")
            or h.get("champion_sigma_sky_arcsec")
            or h.get("sigma_total_sky_arcsec")
        ),
        "ew": h.get("extent_ew_deg") or ch.get("extent_ew_deg") or h.get("length_deg"),
        "cite": h.get("superduper_citation") or h.get("citation_line") or h.get("how_to_cite"),
    }


def format_dashboard_table(package: Dict[str, Any]) -> str:
    """Small table for Dashboard — easy to scan."""
    d = _pull(package)
    rows = [
        ("UTC", _s(d["utc"])),
        ("lon_III °", _f(d["lon"], 4)),
        ("lat_c °", _f(d["lat"], 3)),
        ("lat_g °", _f(d["lat_g"], 3)),
        ("CM_III °", f"{_f(d['cm'], 4)}  [{_s(d['cm_src'])}]"),
        ("definition", _s(d["definition"])),
        ("grade", _s(d["grade"])),
        ("σ_sky ″", _f(d["sig"], 2)),
        ("EW °", _f(d["ew"], 2)),
        ("dist AU", _f(d["dist"], 5) if d["dist"] is not None else "—"),
        ("vs_WJ", _s(d["eq"].get("agreement") or d["h"].get("winjupos_agreement"))),
        ("Δsky_WJ ″", _f(
            d["eq"].get("sky_error_arcsec")
            if d["eq"].get("sky_error_arcsec") is not None
            else d["h"].get("vs_winjupos_sky_arcsec"),
            2,
        )),
        ("gates", f"{d['h'].get('ultimate_lock_pass')}/{d['h'].get('ultimate_lock_total')}"),
        ("abs_ok", _s(
            d["pub"].get("absolute_ok")
            if d["pub"].get("absolute_ok") is not None
            else d["ch"].get("absolute_publish_ok")
        )),
    ]
    dual = d["dual"]
    if dual:
        a = dual.get("automatic") or {}
        hu = dual.get("human") or {}
        cmp_ = dual.get("comparison") or {}
        rows += [
            ("dual", _s(dual.get("official"))),
            ("auto lon", _f(a.get("lon_iii_deg"), 4)),
            ("hand lon", _f(hu.get("lon_iii_deg"), 4)),
            ("Δsky dual ″", f"{_f(cmp_.get('sky_delta_arcsec'), 2)}  ({_s(cmp_.get('agreement'))})"),
        ]

    w_lab = max(len(r[0]) for r in rows)
    w_val = max(len(str(r[1])) for r in rows)
    w_lab = max(w_lab, 10)
    w_val = min(max(w_val, 12), 48)
    bar = f"+-{'-' * w_lab}-+-{'-' * w_val}-+"
    lines = [
        "DASHBOARD",
        bar,
        f"| {'field':<{w_lab}} | {'value':<{w_val}} |",
        bar,
    ]
    for lab, val in rows:
        v = str(val)[:w_val]
        lines.append(f"| {lab:<{w_lab}} | {v:<{w_val}} |")
    lines.append(bar)
    if d["cite"]:
        lines += ["", f"cite  {d['cite']}"]
    lines.append("")
    return "\n".join(lines)


def format_human_report(package: Dict[str, Any]) -> str:
    """Full Results — all important blocks + full headline + budgets + JSON."""
    d = _pull(package)
    pkg, h, pub, ch, pe, dual = (
        d["pkg"], d["h"], d["pub"], d["ch"], d["pe"], d["dual"]
    )
    lines: List[str] = []

    lines.append("FULL RESULTS")
    lines.append("=" * 56)
    lines.append(f"generated  {datetime.now(timezone.utc).isoformat(timespec='seconds')}")
    lines.append("")

    # 1) Publish table (same as dashboard, embedded)
    lines.append(format_dashboard_table(pkg).rstrip())
    lines.append("")

    # 2) SUPERDUPER / publish text if available
    try:
        from superduper import build_superduper_card, format_superduper_txt
        if not pkg.get("superduper"):
            pkg["superduper"] = build_superduper_card(pkg)
        lines.append(format_superduper_txt(pkg["superduper"]).rstrip())
        lines.append("")
    except Exception:
        pass

    try:
        from publish_primary import format_publish_section, apply_publish_policy
        if not pkg.get("publish"):
            apply_publish_policy(pkg)
        lines.append(format_publish_section(pkg).rstrip())
        lines.append("")
    except Exception:
        pass

    # 3) Champion
    if ch:
        lines.append("CHAMPION")
        lines.append("--------")
        for k in (
            "grade", "definition", "lon_iii_deg", "lat_planetocentric_deg",
            "lat_planetographic_deg", "cm_iii_deg", "cm_source",
            "sigma_total_sky_arcsec", "extent_ew_deg", "unbeatable_auto",
            "absolute_publish_ok", "world_class_score",
        ):
            if k in ch and ch[k] is not None:
                lines.append(_line(k, ch[k]))
        ul = ch.get("ultimate_lock") or {}
        if ul:
            lines.append(_line("gates", f"{ul.get('n_pass')}/{ul.get('n_total')}"))
            if ul.get("failed_checks"):
                lines.append(_line("failed", ul.get("failed_checks")))
        lines.append("")

    # 4) Dual
    if dual:
        a = dual.get("automatic") or {}
        hu = dual.get("human") or {}
        cmp_ = dual.get("comparison") or {}
        lines.append("DUAL")
        lines.append("----")
        lines.append(_line("use", dual.get("official")))
        lines.append(_line("auto", f"{a.get('lon_iii_deg')} / {a.get('lat_deg')}  [{a.get('publish_definition')}]"))
        lines.append(_line("hand", f"{hu.get('lon_iii_deg')} / {hu.get('lat_deg')}  [{hu.get('publish_definition')}]"))
        lines.append(_line("Δsky", f"{cmp_.get('sky_delta_arcsec')} ″  ({cmp_.get('agreement')})"))
        lines.append(_line("EW", hu.get("extent_lon_deg") or hu.get("length_deg")))
        lines.append(_line("W edge", hu.get("west_edge_lon_iii_deg")))
        lines.append(_line("E edge", hu.get("east_edge_lon_iii_deg")))
        lines.append("")

    # 5) Ephemeris
    if pe:
        lines.append("EPHEMERIS")
        lines.append("---------")
        for k in (
            "cm_iii_deg", "cm_source", "distance_au", "apparent_diameter_arcsec",
            "sub_obs_lat_deg", "sub_obs_lon_deg", "north_pa_deg", "light_time_s",
            "t_utc_iso", "source",
        ):
            if pe.get(k) is not None:
                lines.append(_line(k, pe[k]))
        lines.append("")

    # 6) Error budget
    eb = pkg.get("error_budget")
    rg = pkg.get("research_grade") or {}
    methods = rg.get("methods") if isinstance(rg, dict) else {}
    if not eb and isinstance(methods, dict):
        eb = methods.get("error_budget")
        if not eb and isinstance(methods.get("vlbi_full"), dict):
            eb = methods["vlbi_full"].get("error_budget")
    if eb and isinstance(eb, dict):
        lines.append("ERROR BUDGET (″)")
        lines.append("---------------")
        comps = eb.get("components_sky_arcsec") or eb
        if isinstance(comps, dict):
            for k, v in comps.items():
                lines.append(_line(str(k), _f(v, 4), "″"))
        if eb.get("sigma_total_sky_arcsec") is not None:
            lines.append(_line("total", _f(eb.get("sigma_total_sky_arcsec"), 4), "″"))
        lines.append("")

    # 7) Scatter / SOTA (short)
    sota = pkg.get("sota") or {}
    if sota:
        lines.append("SCATTER (not publish)")
        lines.append("--------------------")
        lines.append(_line("lon/lat", f"{sota.get('lon_iii_deg')} / {sota.get('lat_deg')}"))
        lines.append(_line("grade", sota.get("quality_grade")))
        lines.append(_line("inliers", f"{sota.get('n_inliers')}/{sota.get('n_outliers')} out"))
        lines.append("")

    # 8) Gold
    gs = pkg.get("gold_standard") or {}
    if gs and gs.get("ok"):
        lines.append("GOLD / METHODS")
        lines.append("--------------")
        lines.append(_line("primary", gs.get("primary_definition")))
        lines.append(_line("lon/lat", f"{gs.get('primary_lon_iii_deg')} / {gs.get('primary_lat_deg')}"))
        lines.append(_line("grade", gs.get("grade")))
        lines.append(_line("n ok", f"{gs.get('n_methods_ok') or h.get('n_methods_ok')}/{gs.get('n_methods_total') or h.get('n_methods_total')}"))
        lines.append("")

    # 9) Truth recovery
    tr = pkg.get("truth_recovery") or {}
    if tr and tr.get("sky_error_arcsec") is not None:
        lines.append("TRUTH (synth)")
        lines.append("-------------")
        lines.append(_line("Δsky", f"{_f(tr.get('sky_error_arcsec'), 3)} ″  grade={_s(tr.get('grade'))}"))
        lines.append("")

    # 10) NASA / geometry compare
    nasa = pkg.get("nasa") or {}
    if nasa and (nasa.get("measured") or nasa.get("reference")):
        m = nasa.get("measured") or {}
        r = nasa.get("reference") or {}
        dd = nasa.get("deltas") or {}
        lines.append("GEOMETRY CTX")
        lines.append("------------")
        lines.append(_line("meas", f"{m.get('lon_iii_deg')} / {m.get('lat_deg')}"))
        lines.append(_line("ctx", f"{r.get('lon_iii_deg')} / {r.get('lat_deg')}"))
        lines.append(_line("Δ", f"{dd.get('lon_iii_deg')} / {dd.get('lat_deg')}"))
        lines.append(_line("grade", nasa.get("grade")))
        lines.append("")

    # 11) Quality flags / warnings
    flags = h.get("quality_flags") or pub.get("quality_flags") or []
    warns = h.get("quality_warnings") or []
    if flags or warns:
        lines.append("FLAGS")
        lines.append("-----")
        for f in flags:
            lines.append(f"  · {_s(f)}")
        for w in warns:
            lines.append(f"  · {_s(w)}")
        lines.append("")

    # 12) Full headline dict
    if h:
        lines.append("HEADLINE (all keys)")
        lines.append("-------------------")
        for k in sorted(h.keys()):
            v = h[k]
            if isinstance(v, (dict, list)):
                lines.append(f"  {k}: {json.dumps(v, default=str)[:500]}")
            else:
                lines.append(f"  {k}: {v}")
        lines.append("")

    # 13) Tips short
    lines.append("TIPS")
    lines.append("----")
    for i, tip in enumerate(ACCURACY_TIPS, 1):
        lines.append(f"  {i}. {tip}")
    lines.append("")

    # 14) Full JSON
    lines.append("FULL JSON PACKAGE")
    lines.append("=" * 56)
    dump = {k: v for k, v in pkg.items() if k not in ("text", "report_text")}
    lines.append(json.dumps(dump, indent=2, default=str))
    lines.append("")
    lines.append("=== END ===")
    lines.append("")
    return "\n".join(lines)


def write_human_report(path: Union[str, Path], package: Dict[str, Any]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(format_human_report(package), encoding="utf-8")
    return path


def format_nasa_txt(comp_dict: Dict[str, Any]) -> str:
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

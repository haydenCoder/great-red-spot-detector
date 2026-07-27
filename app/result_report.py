#!/usr/bin/env python3
"""
Human-readable full result reports for GRS Observatory.

Goal: not a wall of pure JSON — clear YOUR numbers, Horizons geometry,
truth recovery, error bars, tips. Long is fine; garbage is not.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union

# Same tips as server — kept here so reports are self-contained offline
ACCURACY_TIPS: List[str] = [
    "Process runs auto limb (green) + by-eye limb (cyan) — dual limb.",
    "Fine-tune the cyan outline on the true limb if the auto fit is off.",
    "Keys: arrows move outline, PgUp/PgDn size, R = reset cyan to green, drag = centre.",
    "Publish lon: GS-ORANGE / GS-MAP core. Edges are for size, not the centre.",
    "Use the same definition (core vs core) when comparing to WinJUPOS or prior nights.",
    "Mid-exposure UTC only; ~0.6° System III per minute of time error (BAA).",
    "Trusted CM: SPICE / Horizons / WinJUPOS CML for absolute work.",
    "Horizons = planet geometry only — not a GRS longitude catalogue.",
    "Prefer red channel for GRS; check E–W flip if lon is wildly wrong.",
    "Paste WinJUPOS lon/lat → Δsky ″ is the real accuracy check.",
    "dual_measure.json: auto vs hand Δsky — large Δ means limb or definition sensitivity.",
    "Extra methods / consensus = scatter only; report the publish centre.",
    "MC samples: 50 quick, 200 solid (slower).",
    "Injection trials: 16 draft, 28–36 solid.",
    "σ_tot of a few arcseconds is normal for an extended cloud feature.",
    "Lat ~−22° typical; large lat offsets often mean limb/nav issues.",
    "Truth recovery arcsec only on synthetics (known ground truth).",
    "Multi-night drift needs ≥2 nights with the same definition.",
]
# Merge JUPOS tip sheet when available
try:
    from human_choice import ALL_MEASURE_TIPS
    _seen = set(ACCURACY_TIPS)
    for _t in ALL_MEASURE_TIPS:
        if _t not in _seen:
            ACCURACY_TIPS.append(_t)
            _seen.add(_t)
except Exception:
    pass


def _f(v: Any, d: int = 6) -> str:
    if v is None:
        return "—"
    try:
        x = float(v)
        if x != x:  # NaN
            return "nan"
        return f"{x:.{d}f}"
    except Exception:
        return str(v)


def _s(v: Any) -> str:
    return "—" if v is None else str(v)


def _line(label: str, value: Any, unit: str = "", width: int = 36) -> str:
    u = f" {unit}" if unit else ""
    return f"  {label:<{width}} {_s(value)}{u}"


def _section(title: str, char: str = "═") -> List[str]:
    bar = char * 72
    return ["", bar, f"  {title}", bar, ""]


def _box(title: str, rows: Sequence[str]) -> List[str]:
    out = [
        "┌" + "─" * 70 + "┐",
        f"│  {title:<66}│",
        "├" + "─" * 70 + "┤",
    ]
    for r in rows:
        # wrap long rows
        text = r if len(r) <= 68 else r[:65] + "..."
        out.append(f"│  {text:<68}│" if False else f"│ {r[:68]:<68}│")
        if len(r) > 68:
            rest = r[68:]
            while rest:
                chunk, rest = rest[:68], rest[68:]
                out.append(f"│ {chunk:<68}│")
    out.append("└" + "─" * 70 + "┘")
    return out


def _pull_measured(pkg: Dict[str, Any]) -> Dict[str, Any]:
    """Best-effort YOUR answer from any package shape.

    Prefer VLBI/research-grade pipeline bias-corrected over SOTA gold primary.
    SOTA is reported in its own section; mixing it into YOUR vs NASA confused users.
    """
    h = pkg.get("headline") or {}
    rg = pkg.get("research_grade") or {}
    stages = pkg.get("stages") or {}
    meas = stages.get("measure") or {}
    nasa = pkg.get("nasa") or {}
    nm = (nasa.get("measured") or {}) if isinstance(nasa, dict) else {}

    lon = (
        rg.get("lon_bias_corrected_deg")
        if rg.get("lon_bias_corrected_deg") is not None
        else h.get("pipeline_lon_bias_corrected_deg")
        if h.get("pipeline_lon_bias_corrected_deg") is not None
        else h.get("lon_iii_deg_bias_corrected")
        if h.get("lon_iii_deg_bias_corrected") is not None
        else h.get("lon_iii_deg")
        if h.get("lon_iii_deg") is not None
        else h.get("lon")
        if h.get("lon") is not None
        else meas.get("lon")
        if meas.get("lon") is not None
        else nm.get("lon_iii_deg")
    )
    lat = (
        rg.get("lat_bias_corrected_deg")
        if rg.get("lat_bias_corrected_deg") is not None
        else h.get("pipeline_lat_bias_corrected_deg")
        if h.get("pipeline_lat_bias_corrected_deg") is not None
        else h.get("lat_deg_bias_corrected")
        if h.get("lat_deg_bias_corrected") is not None
        else h.get("lat_deg")
        if h.get("lat_deg") is not None
        else h.get("lat")
        if h.get("lat") is not None
        else meas.get("lat")
        if meas.get("lat") is not None
        else nm.get("lat_deg")
    )
    lon_raw = h.get("lon_raw") if h.get("lon_raw") is not None else rg.get("lon_iii_deg")
    lat_raw = h.get("lat_raw") if h.get("lat_raw") is not None else rg.get("lat_deg")
    length = h.get("length_deg") if h.get("length_deg") is not None else rg.get("length_deg")
    if length is None:
        length = nm.get("length_deg")
    width = h.get("width_deg") if h.get("width_deg") is not None else rg.get("width_deg")
    if width is None:
        width = nm.get("width_deg")
    sigma = (
        h.get("sigma_total_sky_arcsec")
        if h.get("sigma_total_sky_arcsec") is not None
        else rg.get("sigma_total_sky_arcsec")
        if rg.get("sigma_total_sky_arcsec") is not None
        else meas.get("sigma_total_sky_arcsec")
    )
    grade = (
        h.get("measure_grade")
        or h.get("grade")
        or h.get("research_grade")
        or rg.get("grade")
        or meas.get("grade")
    )
    return {
        "lon_bias_corrected": lon,
        "lat_bias_corrected": lat,
        "lon_raw": lon_raw,
        "lat_raw": lat_raw,
        "length_deg": length,
        "width_deg": width,
        "sigma_total_sky_arcsec": sigma,
        "sigma_random": h.get("sigma_random_sky_arcsec") or rg.get("sigma_random_sky_arcsec"),
        "sigma_systematic": h.get("sigma_systematic_sky_arcsec") or rg.get("sigma_systematic_sky_arcsec"),
        "bias_lon": h.get("bias_lon_deg") if h.get("bias_lon_deg") is not None else rg.get("bias_lon_deg"),
        "bias_lat": h.get("bias_lat_deg") if h.get("bias_lat_deg") is not None else rg.get("bias_lat_deg"),
        "grade": grade,
        "injection_n": h.get("injection_n") or rg.get("injection_n"),
        "definition_n": h.get("definition_n") or rg.get("definition_n"),
        "filter_closure": h.get("filter_closure_arcsec") or rg.get("filter_closure_arcsec"),
    }


def _format_nasa_block(nasa: Dict[str, Any]) -> List[str]:
    lines: List[str] = []
    if not nasa:
        lines.append("  (no Horizons geometry block — enable “NASA geometry” if desired)")
        return lines

    measured = nasa.get("measured") or {}
    reference = nasa.get("reference") or {}
    grade = nasa.get("grade")
    if grade is None:
        grade = "—"

    lines.extend(_section("YOUR GRS MEASURE + JPL HORIZONS GEOMETRY", "─"))
    lines.append("  HOW TO READ THIS")
    lines.append("  · YOUR GRS = measured on your image (this software)")
    lines.append("  · HORIZONS = real NASA/JPL Jupiter geometry (CM, distance, orientation)")
    lines.append("  · There is NO NASA “official GRS lon” column (removed — was misleading)")
    lines.append("  · Validate GRS lon with WinJUPOS / same-CM peer measure, not a fake REF")
    lines.append("")

    lines.append("  YOUR GRS MEASUREMENT")
    lines.append("  ┌────────────────────┬──────────────────┐")
    lines.append("  │ quantity           │ YOUR answer      │")
    lines.append("  ├────────────────────┼──────────────────┤")

    def row_y(name: str, y: Any, unit: str = "°"):
        ys = f"{_f(y, 6)} {unit}" if y is not None else "—"
        lines.append(f"  │ {name:<18} │ {ys:<16} │")

    row_y("Longitude III", measured.get("lon_iii_deg"))
    row_y("Latitude", measured.get("lat_deg"))
    row_y("Length (oval)", measured.get("length_deg"))
    row_y("Width (oval)", measured.get("width_deg"))
    lines.append("  └────────────────────┴──────────────────┘")
    lines.append("")

    lines.append("  JUPITER GEOMETRY (JPL Horizons — not GRS centre)")
    lines.append("  ┌────────────────────────────┬──────────────────┐")
    lines.append("  │ quantity                   │ value            │")
    lines.append("  ├────────────────────────────┼──────────────────┤")
    for key, label, unit in (
        ("horizons_cm_iii_deg", "CM III (geometry)", "°"),
        ("jupiter_distance_au", "Distance Earth–Jupiter", "AU"),
        ("jupiter_app_diam_arcsec", "Apparent diameter", "″"),
        ("sub_obs_lat_deg", "Sub-observer latitude", "°"),
        ("sub_obs_lon_deg", "Sub-observer longitude", "°"),
        ("north_pa_deg", "North pole PA", "°"),
        ("light_time_s", "Light time", "s"),
    ):
        if key in reference and reference[key] is not None:
            lines.append(f"  │ {label:<26} │ {_f(reference[key], 6)} {unit:<4} │")
    lines.append("  └────────────────────────────┴──────────────────┘")
    lines.append("")

    lines.append("  PLAIN ENGLISH")
    lines.append("  · Your GRS lon/lat above is from the image pipeline — not from NASA.")
    lines.append("  · Horizons CM III orients the planet; it is not the GRS longitude.")
    lines.append(f"  · Geometry status: {_s(grade)}")
    lines.append(f"  · Source: {_s(nasa.get('source'))}")
    lines.append(f"  · Epoch used: {_s(nasa.get('user_time_iso'))}")
    lines.append("")

    if nasa.get("flags"):
        lines.append("  FLAGS")
        for fl in nasa["flags"]:
            lines.append(f"  · ⚠ {fl}")
        lines.append("")

    if nasa.get("notes"):
        lines.append("  NOTES")
        for n in nasa["notes"]:
            lines.append(f"  · {n}")
        lines.append("")

    lines.append("  COPY-PASTE NUMBERS")
    lines.append(f"  YOUR_LON_III_DEG      = {_f(measured.get('lon_iii_deg'), 8)}")
    lines.append(f"  YOUR_LAT_DEG          = {_f(measured.get('lat_deg'), 8)}")
    lines.append(f"  YOUR_LENGTH_DEG       = {_f(measured.get('length_deg'), 8)}")
    lines.append(f"  YOUR_WIDTH_DEG        = {_f(measured.get('width_deg'), 8)}")
    lines.append(f"  HORIZONS_CM_III_DEG   = {_f(reference.get('horizons_cm_iii_deg'), 8)}  # geometry only")
    lines.append(f"  HORIZONS_DISTANCE_AU  = {_f(reference.get('jupiter_distance_au'), 8)}")
    lines.append("")
    return lines


def _format_truth_block(pkg: Dict[str, Any]) -> List[str]:
    tr = pkg.get("truth_recovery") or {}
    stages = pkg.get("stages") or {}
    if not tr and stages.get("measure"):
        tr = (stages["measure"] or {}).get("truth_recovery") or {}
    truth = pkg.get("truth") or (stages.get("measure") or {}).get("truth") or {}
    h = pkg.get("headline") or {}
    if not tr and h.get("truth_recovery_sky_arcsec") is None and not truth:
        return []

    lines = list(_section("SYNTHETIC TRUTH RECOVERY (only for fake planets)", "─"))
    lines.append("  This section is ONLY meaningful when the image was computer-generated.")
    lines.append("  For real photos there is no known ‘true’ lon/lat — use SPICE CM + fixed definition.")
    lines.append("")

    if truth:
        lines.append("  KNOWN TRUTH (what the simulator planted)")
        lines.append(_line("Truth lon III", _f(truth.get("grs_lon_iii_deg"), 6), "°"))
        lines.append(_line("Truth lat", _f(truth.get("grs_lat_deg"), 6), "°"))
        lines.append(_line("Truth length", _f(truth.get("grs_length_deg"), 6), "°"))
        lines.append(_line("Truth width", _f(truth.get("grs_width_deg"), 6), "°"))
        lines.append(_line("Truth CM III", _f(truth.get("cm_iii_deg"), 6), "°"))
        lines.append(_line("Truth distance", _f(truth.get("distance_au"), 6), "AU"))
        lines.append(_line("Synth epoch", truth.get("user_time_iso")))
        lines.append(_line("Seed", truth.get("seed")))
        lines.append("")

    m = _pull_measured(pkg)
    lines.append("  YOUR RECOVERY vs TRUTH")
    lines.append(
        f"  ┌────────────────────┬──────────────────┬──────────────────┬──────────────────┐"
    )
    lines.append(
        f"  │ quantity           │ YOUR answer      │ TRUTH            │ DIFFERENCE       │"
    )
    lines.append(
        f"  ├────────────────────┼──────────────────┼──────────────────┼──────────────────┤"
    )

    def trow(name, y, t, d=None):
        if d is None and y is not None and t is not None:
            try:
                if "lon" in name.lower():
                    d = ((float(y) - float(t) + 180) % 360) - 180
                else:
                    d = float(y) - float(t)
            except Exception:
                d = None
        ys = f"{_f(y, 6)} °" if y is not None else "—"
        ts = f"{_f(t, 6)} °" if t is not None else "—"
        if d is not None:
            try:
                df = float(d)
                sign = "+" if df >= 0 else ""
                ds = f"{sign}{_f(df, 6)} °"
            except Exception:
                ds = str(d)
        else:
            ds = "—"
        lines.append(f"  │ {name:<18} │ {ys:<16} │ {ts:<16} │ {ds:<16} │")

    tlon = truth.get("grs_lon_iii_deg") if truth else h.get("truth_lon")
    tlat = truth.get("grs_lat_deg") if truth else h.get("truth_lat")
    trow("Longitude III", m.get("lon_bias_corrected"), tlon, tr.get("dlon_deg") or h.get("dlon_deg"))
    trow("Latitude", m.get("lat_bias_corrected"), tlat, tr.get("dlat_deg") or h.get("dlat_deg"))
    lines.append(
        f"  └────────────────────┴──────────────────┴──────────────────┴──────────────────┘"
    )
    lines.append("")
    sky = tr.get("sky_error_arcsec") if tr.get("sky_error_arcsec") is not None else h.get("truth_recovery_sky_arcsec")
    lines.append(_line("On-sky error |Δ|", _f(sky, 6), "arcsec"))
    lines.append(_line("Truth recovery grade", tr.get("grade") or h.get("truth_recovery_grade")))
    if sky is not None:
        try:
            s = float(sky)
            if s <= 0.5:
                verdict = "EXCELLENT (≤0.5″)"
            elif s <= 1.0:
                verdict = "VERY GOOD (≤1″)"
            elif s <= 2.0:
                verdict = "GOOD (≤2″ target met)"
            elif s <= 5.0:
                verdict = "FAIR — check nav / definition"
            else:
                verdict = "POOR — pipeline mismatch"
            lines.append(f"  Verdict: {verdict}")
        except Exception:
            pass
    lines.append("")
    return lines


def format_human_report(package: Dict[str, Any]) -> str:
    """Long, clear, human report. Prefer this over dumping raw JSON alone."""
    pkg = package or {}
    h = pkg.get("headline") or {}
    lines: List[str] = []

    lines.append("╔" + "═" * 70 + "╗")
    lines.append("║" + " GRS OBSERVATORY — FULL HUMAN REPORT".center(70) + "║")
    lines.append("║" + " your numbers · Horizons geometry · truth · tips · full dump".center(70) + "║")
    lines.append("╚" + "═" * 70 + "╝")
    lines.append(f"Generated: {datetime.now().isoformat(timespec='seconds')}")
    lines.append("")

    # Identity
    lines.extend(_section("1 · JOB IDENTITY"))
    sk = pkg.get("source_kind") or h.get("source_kind") or pkg.get("kind") or "unknown"
    lines.append(_line("Source kind", sk))
    lines.append(_line("Job kind", pkg.get("kind") or h.get("mode") or "—"))
    lines.append(_line("Run #", pkg.get("run_n") if pkg.get("run_n") is not None else h.get("run_n")))
    lines.append(_line("Job id", pkg.get("job_id")))
    lines.append(_line("Output folder", pkg.get("output_folder") or pkg.get("output_dir")))
    lines.append(_line("User time (UTC)", pkg.get("user_time") or h.get("user_time") or h.get("synth_epoch") or pkg.get("synth_epoch")))
    lines.append(_line("Country", pkg.get("country")))
    lines.append(_line("Time error (s)", pkg.get("time_error_seconds") if pkg.get("time_error_seconds") is not None else h.get("time_error_seconds")))
    lines.append(_line("Mode / metrology", pkg.get("mode") or h.get("metrology")))
    lines.append("")

    # PHILOSOPHY
    lines.extend(_section("2 · HOW TO READ THIS REPORT"))
    lines.append("  PUBLISH / best-answer card = the GRS centre to report (policy hierarchy).")
    lines.append("  Gates all passed = automated checks in this app OK (not a claim vs HST/human desk).")
    lines.append("  Multi-method list = scatter / confidence only — not the answer.")
    lines.append("  Equal to WinJUPOS only if you paste WJ lon/lat, same CM, small Δsky.")
    lines.append("  Horizons/SPICE = Jupiter geometry only, not a GRS longitude catalogue.")
    if pkg.get("philosophy"):
        lines.append(f"  · {pkg['philosophy']}")
    lines.append("")

    # SUPERDUPER card first
    try:
        from superduper import build_superduper_card, format_superduper_txt
        if not pkg.get("superduper"):
            pkg["superduper"] = build_superduper_card(pkg)
        lines.append(format_superduper_txt(pkg["superduper"]).rstrip())
        lines.append("")
    except Exception as e:
        lines.append(f"  (superduper card unavailable: {e})")
        lines.append("")

    # Official publish block
    if not pkg.get("publish"):
        try:
            from publish_primary import apply_publish_policy
            apply_publish_policy(pkg)
        except Exception:
            pass
    try:
        from publish_primary import format_publish_section
        lines.append(format_publish_section(pkg).rstrip())
        lines.append("")
    except Exception as e:
        lines.append(f"  (publish policy unavailable: {e})")
        lines.append("")

    # AI hard-case assist
    ah = pkg.get("ai_hard_case") or {}
    if ah:
        lines.extend(_section("2b · HARD-FRAME ASSIST (only when the stack is difficult)"))
        lines.append(f"  Engaged          {ah.get('engaged')}")
        lines.append(f"  Difficulty 0–1   {ah.get('difficulty')}")
        lines.append(f"  NN used          {ah.get('nn_used')}")
        lines.append(f"  Blend weight     {ah.get('blend_weight')}")
        lines.append(f"  Lon before→after {ah.get('lon_before')} → {ah.get('lon_after')}")
        lines.append(f"  Lat before→after {ah.get('lat_before')} → {ah.get('lat_after')}")
        lines.append(f"  Reasons          {ah.get('reasons')}")
        lines.append(f"  Note             {ah.get('note')}")
        lines.append("  AI does NOT replace the published GS-MAP centre.")
        lines.append("")

    # SOTA = scatter only
    sota = pkg.get("sota") or {}
    lines.extend(_section("3 · MULTI-METHOD SCATTER (not the published centre)"))
    lines.append("  Do not report SOTA lon as your official GRS position.")
    lines.append("  Official number is in PUBLISH THIS (GS-MAP / GS-BARY) above.")
    lines.append("")
    if sota and sota.get("ok"):
        try:
            from sota_accuracy import format_sota_section
            lines.append(format_sota_section(sota).rstrip())
        except Exception:
            lines.append(_line("Lon III SOTA (scatter)", _f(sota.get("lon_iii_deg"), 6), "°"))
            lines.append(_line("Lat SOTA (scatter)", _f(sota.get("lat_deg"), 6), "°"))
            lines.append(_line("Quality", sota.get("quality_grade")))
        lines.append("")
        lines.append("  COPY-PASTE SOTA (scatter only)")
        lines.append(f"  SOTA_LON_III_DEG = {_f(sota.get('lon_iii_deg'), 8)}  # NOT publish")
        lines.append(f"  SOTA_LAT_DEG     = {_f(sota.get('lat_deg'), 8)}")
        lines.append(f"  SOTA_QUALITY     = {_s(sota.get('quality_grade'))}")
        lines.append("")
    else:
        lines.append("  (SOTA layer not available)")
        lines.append("")

    # GOLD STANDARD — professional primary product
    gs = pkg.get("gold_standard") or {}
    lines.extend(_section("3b · GOLD STANDARD / ALL METHODS (full catalogue)"))
    if gs and gs.get("ok"):
        lines.append(f"  Primary definition     {gs.get('primary_definition')}")
        lines.append(_line("Lon III (primary)", _f(gs.get("primary_lon_iii_deg"), 6), "°"))
        lines.append(_line("Lat (primary)", _f(gs.get("primary_lat_deg"), 6), "°"))
        lines.append(_line("Length", _f(gs.get("primary_length_deg"), 6), "°"))
        lines.append(_line("Width", _f(gs.get("primary_width_deg"), 6), "°"))
        lines.append(_line("Procedure grade", gs.get("grade")))
        lines.append(_line("CM III used", _f(gs.get("cm_iii_deg"), 6), "°"))
        lines.append(_line("CM source", gs.get("cm_source")))
        lines.append(_line("Def scatter lon", _f(gs.get("definition_scatter_lon_deg"), 4), "°"))
        lines.append(_line("Def scatter lat", _f(gs.get("definition_scatter_lat_deg"), 4), "°"))
        if gs.get("west_edge_lon_iii_deg") is not None:
            lines.append(_line("West/high edge lon", _f(gs.get("west_edge_lon_iii_deg"), 6), "°"))
            lines.append(_line("East/low edge lon", _f(gs.get("east_edge_lon_iii_deg"), 6), "°"))
            lines.append(_line("|W−E| extent", _f(gs.get("extent_lon_deg"), 4), "°"))
        lines.append("")
        lines.append("  COPY-PASTE GOLD PRIMARY")
        lines.append(f"  GS_LON_III_DEG = {_f(gs.get('primary_lon_iii_deg'), 8)}")
        lines.append(f"  GS_LAT_DEG     = {_f(gs.get('primary_lat_deg'), 8)}")
        lines.append(f"  GS_DEFINITION  = {_s(gs.get('primary_definition'))}")
        lines.append(f"  GS_CM_SOURCE   = {_s(gs.get('cm_source'))}")
        lines.append("")
        if gs.get("measures"):
            lines.append("  All named definitions:")
            for mm in gs["measures"]:
                if not mm.get("ok", True):
                    continue
                lines.append(
                    f"    {mm.get('definition_id')}: lon={_f(mm.get('lon_iii_deg'), 4)}°  "
                    f"lat={_f(mm.get('lat_deg'), 4)}°"
                )
            lines.append("")
        if gs.get("winjupos_manual"):
            w = gs["winjupos_manual"]
            lines.append("  VS YOUR WINJUPOS MANUAL PICK (validation — not NASA truth)")
            lines.append(_line("Pipeline lon", _f(w.get("pipeline_primary_lon_iii_deg"), 6), "°"))
            lines.append(_line("WinJUPOS lon", _f(w.get("winjupos_manual_lon_iii_deg"), 6), "°"))
            lines.append(_line("Δlon (pipe−WJ)", _f(w.get("delta_lon_deg"), 6), "°"))
            lines.append(_line("Pipeline lat", _f(w.get("pipeline_primary_lat_deg"), 6), "°"))
            lines.append(_line("WinJUPOS lat", _f(w.get("winjupos_manual_lat_deg"), 6), "°"))
            lines.append(_line("Δlat", _f(w.get("delta_lat_deg"), 6), "°"))
            lines.append(_line("On-sky |Δ|", _f(w.get("sky_error_arcsec"), 4), "″"))
            lines.append(_line("Agreement", w.get("agreement")))
            lines.append("")
        if gs.get("procedure_steps"):
            lines.append("  Procedure steps:")
            for s in gs["procedure_steps"]:
                lines.append(f"    {s}")
            lines.append("")
    else:
        lines.append("  (gold standard not run or failed on this job)")
        if gs.get("error"):
            lines.append(f"  error: {gs.get('error')}")
        lines.append("")

    # WinJUPOS twin + limb outline sensitivity
    twin = pkg.get("winjupos_twin") or {}
    lines.extend(_section("3b · WINJUPOS TWIN + LIMB OUTLINE (larger vs smaller edge)"))
    if twin:
        lines.append("  YES — human limb size and GRS edge vs core definitions change lon/lat.")
        lines.append(_line("Twin primary", twin.get("twin_primary_definition")))
        lines.append(_line("Twin lon III", _f(twin.get("twin_lon_iii_deg"), 6), "°"))
        lines.append(_line("Twin lat", _f(twin.get("twin_lat_deg"), 6), "°"))
        lines.append(_line("Twin grade", twin.get("grade")))
        lines.append(_line("Limb lon spread (outline size)", _f(twin.get("limb_lon_spread_deg"), 4), "°"))
        lines.append(_line("Limb sky spread", _f(twin.get("limb_sky_spread_arcsec"), 4), "″"))
        lines.append(_line("Definition lon spread", _f(twin.get("definition_lon_spread_deg"), 4), "°"))
        lines.append(_line("W–E extent", _f(twin.get("extent_lon_deg"), 4), "°"))
        if twin.get("limb_probes"):
            lines.append("  Limb probes (outer = larger outline, inner = smaller):")
            for p in twin["limb_probes"]:
                lines.append(
                    f"    {p.get('name')}: a={_f(p.get('a_eq_px'), 1)}px  "
                    f"lon={_f(p.get('lon_iii_deg'), 4)}°  lat={_f(p.get('lat_deg'), 4)}°"
                )
        if twin.get("definition_table"):
            lines.append("  Definitions (core ≠ edges):")
            for d in twin["definition_table"]:
                lines.append(
                    f"    [{d.get('role')}] {d.get('definition')}: "
                    f"lon={_f(d.get('lon_iii_deg'), 4)}°  lat={_f(d.get('lat_deg'), 4)}°"
                )
        lines.append("")
        lines.append("  Use one outline style + one definition every night (WinJUPOS discipline).")
        lines.append("")
    else:
        lines.append("  (winjupos twin not run)")
        lines.append("")

    # YOUR ANSWER — pipeline measure (bias-corrected VLBI/SPIRE)
    m = _pull_measured(pkg)
    lines.extend(_section("4 · PIPELINE MEASURE (bias-corrected stack)"))
    lines.append("  Secondary product: research-grade stack. Prefer §3 gold primary for pro-style lon.")
    lines.append("")
    lines.append("  ★ BIAS-CORRECTED")
    lines.append(_line("Longitude System III", _f(m.get("lon_bias_corrected"), 6), "°"))
    lines.append(_line("Latitude", _f(m.get("lat_bias_corrected"), 6), "°"))
    lines.append(_line("Length (oval)", _f(m.get("length_deg"), 6), "°"))
    lines.append(_line("Width (oval)", _f(m.get("width_deg"), 6), "°"))
    lines.append(_line("Grade", m.get("grade")))
    lines.append("")
    lines.append("  Uncertainty (on-sky arcseconds)")
    lines.append(_line("σ_total", _f(m.get("sigma_total_sky_arcsec"), 6), "″"))
    lines.append(_line("σ_random", _f(m.get("sigma_random"), 6), "″"))
    lines.append(_line("σ_systematic", _f(m.get("sigma_systematic"), 6), "″"))
    lines.append("")
    lines.append("  Calibration extras")
    lines.append(_line("Raw lon (pre-bias)", _f(m.get("lon_raw"), 6), "°"))
    lines.append(_line("Raw lat (pre-bias)", _f(m.get("lat_raw"), 6), "°"))
    lines.append(_line("Bias lon applied", _f(m.get("bias_lon"), 6), "°"))
    lines.append(_line("Bias lat applied", _f(m.get("bias_lat"), 6), "°"))
    lines.append(_line("Injection trials N", m.get("injection_n")))
    lines.append(_line("Definition stack N", m.get("definition_n")))
    lines.append(_line("Filter closure", _f(m.get("filter_closure"), 6), "″"))
    lines.append("")
    lines.append("  COPY-PASTE PIPELINE")
    lines.append(f"  LON_III_DEG = {_f(m.get('lon_bias_corrected'), 8)}")
    lines.append(f"  LAT_DEG     = {_f(m.get('lat_bias_corrected'), 8)}")
    lines.append(f"  SIGMA_TOT_ARCSEC = {_f(m.get('sigma_total_sky_arcsec'), 8)}")
    lines.append(f"  GRADE       = {_s(m.get('grade'))}")
    lines.append("")

    # NASA / geometry context — NOT GRS truth
    nasa = pkg.get("nasa")
    if not nasa and (pkg.get("stages") or {}).get("nasa"):
        nasa = pkg["stages"]["nasa"]
    lines.extend(_section("5 · GEOMETRY CONTEXT (JPL Horizons — planet only, not GRS lon)", "─"))
    lines.append("  Horizons = Jupiter CM / distance / orientation. No NASA GRS lon catalog.")
    lines.append("  Use for sanity only. Gold standard + WinJUPOS manual Δ are the pro checks.")
    lines.append("")
    lines.extend(_format_nasa_block(nasa if isinstance(nasa, dict) else {}))

    # Truth recovery
    lines.extend(_format_truth_block(pkg))

    # Ephemeris
    pe = pkg.get("pro_ephemeris") or (pkg.get("stages") or {}).get("ephemeris")
    if pe and isinstance(pe, dict):
        lines.extend(_section("6 · PRO EPHEMERIS (Jupiter geometry for this epoch)", "─"))
        for k, label, unit in (
            ("t_utc_iso", "Epoch UTC", ""),
            ("cm_iii_deg", "CM III", "°"),
            ("cm_source", "CM source", ""),
            ("distance_au", "Distance", "AU"),
            ("distance_source", "Distance source", ""),
            ("sub_obs_lat_deg", "Sub-obs lat", "°"),
            ("sub_obs_lon_deg", "Sub-obs lon", "°"),
            ("north_pa_deg", "North PA", "°"),
            ("apply_orientation", "Apply orientation", ""),
            ("source", "Ephemeris source", ""),
        ):
            if k in pe and pe[k] is not None:
                val = pe[k]
                if isinstance(val, float):
                    val = _f(val, 6)
                lines.append(_line(label, val, unit))
        lines.append("")

    # Error budget
    eb = pkg.get("error_budget")
    rg = pkg.get("research_grade") or {}
    methods = rg.get("methods") if isinstance(rg, dict) else {}
    if not eb and isinstance(methods, dict):
        eb = methods.get("error_budget")
        if not eb and isinstance(methods.get("vlbi_full"), dict):
            eb = methods["vlbi_full"].get("error_budget")
    if eb and isinstance(eb, dict):
        lines.extend(_section("7 · FORMAL ERROR BUDGET (arcsec)", "─"))
        comps = eb.get("components_sky_arcsec") or eb
        if isinstance(comps, dict):
            for k, v in comps.items():
                lines.append(_line(str(k), _f(v, 6), "″"))
        if eb.get("sigma_total_sky_arcsec") is not None:
            lines.append(_line("sigma_total_sky_arcsec", _f(eb.get("sigma_total_sky_arcsec"), 6), "″"))
        lines.append("")

    # Multi-epoch / hard / factory stages
    multi = pkg.get("multi_epoch") or (pkg.get("stages") or {}).get("multi_epoch")
    if multi and isinstance(multi, dict):
        lines.extend(_section("8 · MULTI-EPOCH DIFFERENTIALS", "─"))
        for k in (
            "n_epochs", "drift_lon_deg_per_day", "drift_lon_sigma",
            "rms_residual_sky_arcsec", "smoother", "note", "error",
        ):
            if k in multi and multi[k] is not None:
                lines.append(_line(k, multi[k]))
        lines.append("")

    hard = pkg.get("hard_synth") or (pkg.get("stages") or {}).get("hard_synth")
    if hard and isinstance(hard, dict) and not hard.get("skipped"):
        lines.extend(_section("9 · HARD-SYNTH CALIBRATION", "─"))
        lines.append(_line("calibration_grade", hard.get("calibration_grade")))
        overall = hard.get("overall") or {}
        if isinstance(overall, dict):
            for k, v in overall.items():
                lines.append(_line(f"overall.{k}", v))
        by_fam = hard.get("by_family") or {}
        if isinstance(by_fam, dict):
            lines.append("  by_family:")
            for fam, stats in by_fam.items():
                lines.append(f"    [{fam}] {json.dumps(stats, default=str)}")
        lines.append("")

    # Tips — all of them
    lines.extend(_section("10 · ALL ACCURACY / PRO-PROCEDURE TIPS", "─"))
    pro_tips = [
        "Primary product = gold_standard named definition (GS-MAP etc.), not a NASA GRS answer.",
        "Absolute System III needs good CM: paste WinJUPOS CM or use SPICE — analytic CM is weaker.",
        "Paste your WinJUPOS *manual* lon/lat to validate automation (WJ does not auto-detect GRS).",
        "Keep one fixed definition across nights for drift science.",
        "Report definition id + CM source + σ always — that is publication hygiene.",
    ] + ACCURACY_TIPS
    for i, tip in enumerate(pro_tips, 1):
        lines.append(f"  {i:02d}. {tip}")
    # package notes
    notes = []
    if isinstance(rg, dict) and rg.get("notes"):
        notes.extend(rg["notes"])
    if isinstance(nasa, dict) and nasa.get("notes"):
        notes.extend(nasa["notes"])
    if notes:
        lines.append("")
        lines.append("  Notes attached to this run:")
        for n in notes:
            lines.append(f"  · {n}")
    lines.append("")

    # Research grade detail (no truncation of keys)
    if rg and isinstance(rg, dict):
        lines.extend(_section("11 · RESEARCH-GRADE DETAIL", "─"))
        for k in sorted(rg.keys()):
            if k in ("methods", "injections", "definitions"):
                continue
            v = rg[k]
            if isinstance(v, (dict, list)):
                lines.append(f"  {k}:")
                lines.append(json.dumps(v, indent=4, default=str))
            else:
                lines.append(_line(k, v))
        if rg.get("definitions"):
            lines.append("")
            lines.append("  DEFINITIONS STACK (every definition used)")
            lines.append(json.dumps(rg["definitions"], indent=2, default=str))
        if rg.get("injections"):
            lines.append("")
            lines.append(f"  INJECTION RECOVERIES (N={len(rg['injections'])})")
            lines.append(json.dumps(rg["injections"], indent=2, default=str))
        if rg.get("methods"):
            lines.append("")
            lines.append("  METHODS DICT (full)")
            lines.append(json.dumps(rg["methods"], indent=2, default=str))
        lines.append("")

    # Stages for factory
    if pkg.get("stages"):
        lines.extend(_section("12 · FACTORY / STAGES RAW", "─"))
        lines.append(json.dumps(pkg["stages"], indent=2, default=str))
        lines.append("")

    # Headline raw
    if h:
        lines.extend(_section("13 · HEADLINE DICT (raw)", "─"))
        lines.append(json.dumps(h, indent=2, default=str))
        lines.append("")

    # Full package JSON last — complete, no silent truncation
    lines.extend(_section("14 · FULL JSON PACKAGE (complete machine dump)", "═"))
    lines.append("  Everything the job returned, for archives / scripts.")
    lines.append("")
    # Avoid re-embedding the huge text field if present
    dump = {k: v for k, v in pkg.items() if k not in ("text", "report_text")}
    lines.append(json.dumps(dump, indent=2, default=str))
    lines.append("")
    lines.append("═══ END OF REPORT ═══")
    return "\n".join(lines)


def write_human_report(path: Union[str, Path], package: Dict[str, Any]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = format_human_report(package)
    path.write_text(text, encoding="utf-8")
    return path


def format_nasa_txt(comp_dict: Dict[str, Any]) -> str:
    """Standalone NASA comparison text file body."""
    return "\n".join(_format_nasa_block(comp_dict))

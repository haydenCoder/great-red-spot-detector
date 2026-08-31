#!/usr/bin/env python3
"""
WinJUPOS+ export — pro desk parity for automated GRS measures
=============================================================

WinJUPOS is strong because humans fix: UTC, CM, limb outline, and definition.
This module packages *your* automated stack into the same report language so
you can beat *careless* WinJUPOS and match careful desk work.

I wrote this because I kept having to manually compare my automated results
to my WinJUPOS manual picks — typing the same numbers into a calculator
every time got old fast. Now there's a desk score and a side-by-side
comparison built in.

Key things this module does:
  • Planetocentric + planetographic latitude (WJ uses planetographic)
  • Recommended limb outline from multi-isophote probes
  • EW edge extent (W/E) as size product (not template prior)
  • Side-by-side equality score vs manual WJ pick
  • Single citation line for logs / papers

Does **not** claim to beat HST or Juno. It's optical ground-based metrology.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, List, Optional

from verbose_log import CONSOLE
from accuracy_gates import safe_float as _f



def _wrap_diff(a: float, b: float) -> float:
    return float((a - b + 180.0) % 360.0 - 180.0)


def build_winjupos_plus_block(package: Dict[str, Any]) -> Dict[str, Any]:
    """
    Assemble a WinJUPOS-comparable product block from an existing job package.

    Pure aggregation — does not remeasure (fast, deterministic). It just
    pulls together results from publish, champion, twin, gold_standard, etc.
    into a single block that speaks the same language as WinJUPOS output.

    The desk score is a heuristic I came up with to quantify how close our
    automated pipeline is to what a careful human would do in WinJUPOS. It's
    not perfect but it's a useful sanity check.
    """
    h = package.get("headline") or {}
    pub = package.get("publish") or {}
    twin = package.get("winjupos_twin") or {}
    gs = package.get("gold_standard") or {}
    pe = package.get("pro_ephemeris") or {}
    pq = package.get("publish_quality") or pub.get("quality") or {}

    lon = _f(pub.get("publish_lon_iii_deg") or h.get("publish_lon_iii_deg") or h.get("lon_iii_deg"))
    lat_c = _f(pub.get("publish_lat_deg") or h.get("publish_lat_deg") or h.get("lat_deg"))
    lat_g = _f(
        pub.get("publish_lat_planetographic_deg")
        or h.get("publish_lat_planetographic_deg")
        or h.get("lat_planetographic_deg")
    )
    if lat_g is None and lat_c is not None:
        try:
            from precision_engine import planetocentric_to_planetographic
            lat_g = planetocentric_to_planetographic(lat_c)
        except Exception:
            lat_g = None

    # Size: prefer edge extent, then gold oval, then headline L/W
    extent = _f(twin.get("extent_lon_deg") or gs.get("extent_lon_deg"))
    length = _f(
        gs.get("primary_length_deg")
        or h.get("length_deg")
        or pub.get("publish_length_deg")
    )
    width = _f(gs.get("primary_width_deg") or h.get("width_deg"))
    west = _f(gs.get("west_edge_lon_iii_deg") or twin.get("west_edge_lon_iii_deg"))
    east = _f(gs.get("east_edge_lon_iii_deg") or twin.get("east_edge_lon_iii_deg"))
    if extent is None and west is not None and east is not None:
        extent = abs(_wrap_diff(west, east))

    # Recommended limb from twin probes (smallest sky residual vs twin primary if available)
    limb_probes = twin.get("limb_probes") or []
    rec_limb = None
    if limb_probes and lon is not None:
        best = None
        best_d = 1e99
        for p in limb_probes:
            pl = _f(p.get("lon_iii_deg"))
            if pl is None:
                continue
            d = abs(_wrap_diff(pl, lon))
            if d < best_d:
                best_d = d
                best = p
        rec_limb = best

    cm = _f(pub.get("cm_iii_deg") or h.get("cm_iii_deg") or pe.get("cm_iii_deg"))
    cm_source = str(pub.get("cm_source") or h.get("cm_source") or pe.get("cm_source") or "")
    dist = _f(pub.get("distance_au") or h.get("distance_au") or pe.get("distance_au")) or 5.2
    definition = str(pub.get("publish_definition") or h.get("publish_definition") or "GS-MAP")

    equality = pub.get("winjupos_equality") or twin.get("winjupos_manual") or {}
    sky_vs_wj = _f(equality.get("sky_error_arcsec") or h.get("vs_winjupos_sky_arcsec"))
    agreement = str(equality.get("agreement") or h.get("winjupos_agreement") or "NO_MANUAL_PICK")

    # Desk score: higher = closer to careful WinJUPOS practice
    # (not a claim of truth, just a rough "how confident am I" metric)
    # I spent a while tuning the penalties — CM source matters a LOT
    score = 100.0
    flags: List[str] = []
    if cm_source.lower() in ("analytical", "analytic", "fallback", "", "unknown"):
        score -= 40  # analytical CM is really bad for precision work
        flags.append("CM_WEAK")
    if not (pq.get("cm_trusted") if pq else False) and "CM_WEAK" not in flags:
        if not any(k in cm_source.lower() for k in ("spice", "horizons", "winjupos", "override", "synthetic")):
            score -= 25  # CM source we can't verify — risky
            flags.append("CM_UNTRUSTED")
    limb_spread = _f(twin.get("limb_sky_spread_arcsec") or h.get("limb_outline_sky_spread_arcsec"))
    if limb_spread is not None:
        if limb_spread > 5.0:
            score -= 20  # limb fit is all over the place — bad sign
            flags.append("LIMB_UNSTABLE")
        elif limb_spread > 2.5:
            score -= 8  # elevated but not terrible
            flags.append("LIMB_ELEVATED")
    def_spread = _f(twin.get("definition_lon_spread_deg") or h.get("definition_lon_spread_deg"))
    if def_spread is not None and def_spread > 12.0:
        score -= 15  # GRS definition is too scattered across methods
        flags.append("DEFINITION_SCATTER")
    if lat_c is not None and not (-36 <= lat_c <= -10):
        score -= 30  # GRS is always around -23°, anything outside this band is suspicious
        flags.append("LAT_OUT_OF_BAND")
    if sky_vs_wj is not None:
        if sky_vs_wj <= 1.0:
            score += 5  # nice — our answer matches your WinJUPOS pick
            flags.append("MATCHES_YOUR_WJ")
        elif sky_vs_wj > 5.0:
            score -= 10  # something's off — we disagree with your WJ by a lot
            flags.append("DIFFERS_FROM_YOUR_WJ")
    score = float(max(0.0, min(100.0, score)))

    if score >= 85 and "CM_WEAK" not in flags and "LAT_OUT_OF_BAND" not in flags:
        desk_grade = "DESK_EXCELLENT"
    elif score >= 70:
        desk_grade = "DESK_GOOD"
    elif score >= 50:
        desk_grade = "DESK_FAIR"
    else:
        desk_grade = "DESK_HOLD"

    cite = (
        f"GRS {definition}  λ_III={lon:.4f}°  "
        f"φ_c={lat_c:.3f}°  φ_g={lat_g:.3f}°  "
        f"EW={extent if extent is not None else length}°  "
        f"CM={cm:.4f}° ({cm_source})  Δ={dist:.4f} AU"
        if lon is not None and lat_c is not None and lat_g is not None and cm is not None and dist is not None
        else "GRS measure incomplete"
    )

    block = {
        "mode": "measure_plus",
        "ok": lon is not None and lat_c is not None,
        "publish_definition": definition,
        "lon_iii_deg": lon,
        "lat_planetocentric_deg": lat_c,
        "lat_planetographic_deg": lat_g,
        "length_deg_isophote_or_oval": length,
        "extent_ew_deg": extent,
        "west_edge_lon_iii_deg": west,
        "east_edge_lon_iii_deg": east,
        "width_deg": width,
        "cm_iii_deg": cm,
        "cm_source": cm_source,
        "distance_au": dist,
        "user_time_iso": h.get("user_time") or h.get("synth_epoch") or gs.get("user_time_iso"),
        "recommended_limb_outline": rec_limb,
        "limb_sky_spread_arcsec": limb_spread,
        "definition_lon_spread_deg": def_spread,
        "vs_manual_winjupos": {
            "agreement": agreement,
            "sky_error_arcsec": sky_vs_wj,
            "equal_to_winjupos": bool(equality.get("equal_to_winjupos")),
        },
        "desk_score_0_100": score,
        "desk_grade": desk_grade,
        "desk_flags": flags,
        "citation_line": cite,
        "how_to_use": [
            "Paste the same mid-exposure UTC and CM into WinJUPOS for a fair Δ — otherwise you're comparing apples to oranges.",
            "Compare φ_g (planetographic) to WinJUPOS latitude, not only φ_c — WinJUPOS uses planetographic.",
            "Use GS-MAP / core definition to match a careful WJ core pick.",
            "If desk_grade is DESK_HOLD, fix CM (SPICE/WJ) or limb before publishing — the numbers aren't trustworthy yet.",
            "Method soup / SOTA are scatter only — not the published centre.",
        ],
        "honesty": (
            "Automated optical metrology with fixed definitions and formal gates. "
            "Not radio VLBI; not a Nobel claim. I'm trying to compete with careful WinJUPOS "
            "on the same frame, not beat spacecraft imaging."
        ),
        "champion_grade": (package.get("champion") or {}).get("grade"),
        "champion_score": (package.get("champion") or {}).get("world_class_score"),
        "champion_sigma_sky_arcsec": (package.get("champion") or {}).get("sigma_total_sky_arcsec"),
    }
    return block


def format_winjupos_plus_txt(block: Dict[str, Any]) -> str:
    """Compact desk-check card — key numbers only."""
    vj = block.get("vs_manual_winjupos") or {}
    return "\n".join([
        "DESK",
        "====",
        f"lon_III   {block.get('lon_iii_deg')} °",
        f"lat_c     {block.get('lat_planetocentric_deg')} °",
        f"lat_g     {block.get('lat_planetographic_deg')} °",
        f"CM_III    {block.get('cm_iii_deg')}  [{block.get('cm_source')}]",
        f"UTC       {block.get('user_time_iso')}",
        f"def       {block.get('publish_definition')}",
        f"grade     {block.get('desk_grade')}  ({block.get('desk_score_0_100')})",
        f"EW        {block.get('extent_ew_deg')} °",
        f"vs_WJ     {vj.get('agreement')}  Δsky={vj.get('sky_error_arcsec')} ″",
        f"cite      {block.get('citation_line')}",
        "",
    ])


def attach_winjupos_plus(package: Dict[str, Any], out_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Attach WinJUPOS+ block + optional files; mutate package.

    This is called near the end of the pipeline — after champion and publish
    are done. It adds the WinJUPOS+ block to the package dict and writes
    the JSON + TXT files to the output directory.
    """
    block = build_winjupos_plus_block(package)
    package["measure_plus"] = block
    h = package.setdefault("headline", {})
    h["desk_grade"] = block.get("desk_grade")
    h["desk_score"] = block.get("desk_score_0_100")
    h["citation_line"] = block.get("citation_line")
    if block.get("lat_planetographic_deg") is not None:
        h.setdefault("lat_planetographic_deg", block["lat_planetographic_deg"])
    if block.get("extent_ew_deg") is not None:
        h.setdefault("extent_ew_deg", block["extent_ew_deg"])
    if out_dir is not None:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "winjupos_plus.json").write_text(
            json.dumps(block, indent=2, default=str), encoding="utf-8"
        )
        (out_dir / "winjupos_plus.txt").write_text(
            format_winjupos_plus_txt(block), encoding="utf-8"
        )
        # Single-line paste file for logs
        (out_dir / "winjupos_compatible_measure.txt").write_text(
            (block.get("citation_line") or "") + "\n", encoding="utf-8"
        )
    CONSOLE.ok(
        f"WinJUPOS+ {block.get('desk_grade')} score={block.get('desk_score_0_100')}  "
        f"lon={block.get('lon_iii_deg')}  φ_g={block.get('lat_planetographic_deg')}"
    )
    return block

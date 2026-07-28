#!/usr/bin/env python3
"""
SUPERDUPER best-answer card
===========================

One short, human file that answers: *what number do I report tonight?*

This pulls together the champion ultimate lock + publish policy into a single
card so you don't have to dig through 5 different JSON files to find your
answer. It's not a new measurement — it's just packaging the best result
the pipeline already computed.

I added this because I kept losing track of which number was "the real one"
after a run. Now there's one file that says "REPORT THIS" and you're done.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional


def _f(x):
    """safe float — returns None if it can't convert or if it's NaN/inf"""
    try:
        v = float(x)
        if v != v:  # NaN check (NaN != NaN is True, weird but handy)
            return None
        return v
    except Exception:
        return None


def build_superduper_card(package: Dict[str, Any]) -> Dict[str, Any]:
    """Build the one-card summary of the best answer from this job.

    Tries to pull from publish first (that's the official number), then
    champion, then headline — whatever's actually available. The cascade
    logic was annoying to get right but it means this works even if the
    pipeline only partially completed.
    """
    h = package.get("headline") or {}
    pub = package.get("publish") or {}
    ch = package.get("champion") or {}
    wj = package.get("winjupos_plus") or {}
    eq = pub.get("winjupos_equality") or {}

    lon = _f(pub.get("publish_lon_iii_deg") or h.get("publish_lon_iii_deg") or ch.get("lon_iii_deg"))
    lat_c = _f(pub.get("publish_lat_deg") or h.get("publish_lat_deg") or ch.get("lat_planetocentric_deg"))
    lat_g = _f(
        pub.get("publish_lat_planetographic_deg")
        or h.get("lat_planetographic_deg")
        or ch.get("lat_planetographic_deg")
    )
    sig = _f(
        pub.get("publish_sigma_sky_arcsec")
        or ch.get("sigma_total_sky_arcsec")
        or h.get("champion_sigma_sky_arcsec")
        or h.get("sigma_total_sky_arcsec")
    )
    grade = (
        ch.get("grade")
        or h.get("champion_grade")
        or pub.get("quality_grade")
        or h.get("grade")
        or "—"
    )
    definition = pub.get("publish_definition") or h.get("publish_definition") or ch.get("definition")
    unbeatable = bool(ch.get("unbeatable_auto") or h.get("unbeatable_auto"))
    abs_ok = bool(
        ch.get("absolute_publish_ok")
        if ch.get("absolute_publish_ok") is not None
        else pub.get("absolute_ok")
    )
    cm_source = pub.get("cm_source") or h.get("cm_source") or ch.get("cm_source")
    cm = _f(pub.get("cm_iii_deg") or h.get("cm_iii_deg") or ch.get("cm_iii_deg"))
    extent = _f(ch.get("extent_ew_deg") or h.get("extent_ew_deg") or h.get("extent_lon_deg"))
    ultimate = ch.get("ultimate_lock") or {}

    citation = (
        f"GRS {definition}  λ_III={lon:.4f}°  φ_c={lat_c:.3f}°  φ_g={lat_g:.3f}°  "
        f"σ_sky≈{sig:.3f}″  CM={cm:.4f}° ({cm_source})  grade={grade}"
        if lon is not None and lat_c is not None and lat_g is not None and sig is not None and cm is not None
        else "Measure incomplete — check champion.txt / publish.txt"
    )

    card = {
        "title": "SUPERDUPER BEST ANSWER",
        "report_this": {
            "definition": definition,
            "lon_iii_deg": lon,
            "lat_planetocentric_deg": lat_c,
            "lat_planetographic_deg": lat_g,
            "extent_ew_deg": extent,
            "sigma_sky_arcsec": sig,
            "cm_iii_deg": cm,
            "cm_source": cm_source,
            "grade": grade,
            "unbeatable_auto": unbeatable,
            "absolute_publish_ok": abs_ok,
        },
        "vs_winjupos": {
            "agreement": eq.get("agreement") or h.get("winjupos_agreement"),
            "sky_error_arcsec": eq.get("sky_error_arcsec") or h.get("vs_winjupos_sky_arcsec"),
            "equal_to_winjupos": eq.get("equal_to_winjupos"),
        },
        "ultimate_gates": {
            "n_pass": ultimate.get("n_pass") or h.get("ultimate_lock_pass"),
            "n_total": ultimate.get("n_total") or h.get("ultimate_lock_total"),
            "failed": ultimate.get("failed_checks"),
        },
        "desk_grade": wj.get("desk_grade") or h.get("desk_grade"),
        "citation_line": citation,
        "rules": [
            "Use lon_iii + φ_g (planetographic) when comparing to WinJUPOS — they use planetographic.",
            "Same mid-exposure UTC and CM source as your WinJUPOS session or the comparison is meaningless.",
            "If absolute_publish_ok is false: do NOT publish absolute System III — the error budget is too weak.",
            "If unbeatable_auto is true: no weaker method in this app overrides it (that's the whole point).",
            "Method soup / SOTA are scatter only — they're for confidence, not the published centre.",
            "This is still ground-based optical metrology. Not a claim vs HST / Juno / perfect human WinJUPOS.",
        ],
        "honesty": (
            "Best consolidated product of this job. Optical ground metrology. "
            "When UNBEATABLE_AUTO: in-app hierarchy is locked. "
            "Honest ground-based optical metrology — not spacecraft imaging."
        ),
        "in_app_dominance": bool(unbeatable),
        "in_app_message": (
            "Best automated path in this app tonight."
            if unbeatable
            else "Ultimate gates incomplete — check the failed list; improve CM/UTC/stack and try again."
        ),
    }
    return card


def format_superduper_txt(card: Dict[str, Any]) -> str:
    """Format the SUPERDUPER card as a readable text file for the output folder.

    This is the one you paste into your observation log or email to your
    supervisor. It's designed to be human-readable at a glance.
    """
    r = card.get("report_this") or {}
    v = card.get("vs_winjupos") or {}
    u = card.get("ultimate_gates") or {}
    lines = [
        "╔" + "═" * 58 + "╗",
        "║" + " SUPERDUPER BEST ANSWER — REPORT THIS".center(58) + "║",
        "╚" + "═" * 58 + "╝",
        "",
        f"  Grade              {r.get('grade')}",
        f"  UNBEATABLE_AUTO    {r.get('unbeatable_auto')}",
        f"  Absolute OK        {r.get('absolute_publish_ok')}",
        f"  Ultimate gates     {u.get('n_pass')}/{u.get('n_total')}",
        f"  Failed gates       {u.get('failed') or '—'}",
        f"  In-app dominance   {card.get('in_app_message')}",
        "",
        f"  Definition         {r.get('definition')}",
        f"  Lon III            {r.get('lon_iii_deg')} °",
        f"  Lat centric        {r.get('lat_planetocentric_deg')} °",
        f"  Lat graphic (WJ)   {r.get('lat_planetographic_deg')} °",
        f"  EW extent          {r.get('extent_ew_deg')} °",
        f"  σ_sky (total)      {r.get('sigma_sky_arcsec')} ″",
        f"  CM III             {r.get('cm_iii_deg')} °  [{r.get('cm_source')}]",
        "",
        f"  vs WinJUPOS        {v.get('agreement')}",
        f"  Δ sky vs WJ        {v.get('sky_error_arcsec')} ″",
        f"  Equal WJ?          {v.get('equal_to_winjupos')}",
        f"  Desk grade         {card.get('desk_grade')}",
        "",
        "  CITATION",
        f"  {card.get('citation_line')}",
        "",
        "  RULES",
    ]
    for rule in card.get("rules") or []:
        lines.append(f"  · {rule}")
    lines += ["", f"  {card.get('honesty')}", ""]
    return "\n".join(lines)


def attach_superduper(package: Dict[str, Any], out_dir: Optional[Path] = None) -> Dict[str, Any]:
    """Build the SUPERDUPER card, attach it to the package, and write files.

    This is the last step in the pipeline — after champion, publish, and
    WinJUPOS+ are all done. The card goes into the package dict and also
    gets written as JSON + TXT + a one-line citation file.
    """
    card = build_superduper_card(package)
    package["superduper"] = card
    h = package.setdefault("headline", {})
    h["superduper_grade"] = (card.get("report_this") or {}).get("grade")
    h["superduper_citation"] = card.get("citation_line")
    if out_dir is not None:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "SUPERDUPER_BEST_ANSWER.json").write_text(
            json.dumps(card, indent=2, default=str), encoding="utf-8"
        )
        (out_dir / "SUPERDUPER_BEST_ANSWER.txt").write_text(
            format_superduper_txt(card), encoding="utf-8"
        )
        cite = (card.get("citation_line") or "") + "\n"
        (out_dir / "REPORT_THIS_ONE_LINE.txt").write_text(cite, encoding="utf-8")
    try:
        from verbose_log import CONSOLE
        r = card.get("report_this") or {}
        CONSOLE.ok(
            f"SUPERDUPER {r.get('grade')} lon={r.get('lon_iii_deg')}  "
            f"unbeatable={r.get('unbeatable_auto')}"
        )
    except Exception:
        pass
    return card

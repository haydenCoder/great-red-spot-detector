#!/usr/bin/env python3
"""
Publication policy — what number you should report
==================================================

Rule (pro / WinJUPOS-aligned):
  • PUBLISH: GS-MAP twin (else GS-BARY) as the official lon/lat
  • SOUP / SOTA: scatter and confidence only — not the published answer
  • EQUAL to WinJUPOS only when:
        same CM source discipline + Δ vs your manual WJ pick is small

This module rewrites package["headline"] so dashboards/CLI/UI show the
published number first.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional

from precision_engine import wrap_diff, sky_error_arcsec


# Agreement gates (sky arcsec vs your WinJUPOS manual pick)
EQUAL_SKY_ARCSEC = 1.0       # treat as same result (pro-amateur agreement)
NEAR_SKY_ARCSEC = 2.0        # close
FAIR_SKY_ARCSEC = 5.0


def _f(x) -> Optional[float]:
    try:
        v = float(x)
        return v if math.isfinite(v) else None
    except Exception:
        return None


def assess_winjupos_equality(
    *,
    publish_lon: Optional[float],
    publish_lat: Optional[float],
    wj_lon: Optional[float],
    wj_lat: Optional[float],
    distance_au: float,
    cm_source: str,
) -> Dict[str, Any]:
    """
    When can we say 'same as WinJUPOS'?

    Requires a manual WJ pick. CM source should be winjupos / override / spice
    for a strong equality claim (not pure analytical).
    """
    out: Dict[str, Any] = {
        "compared": False,
        "equal_to_winjupos": False,
        "agreement": "NO_MANUAL_PICK",
        "note": (
            "Paste your WinJUPOS manual GRS lon/lat to test equality. "
            "Also use the same CM (WinJUPOS table or CM override) and the same definition (core)."
        ),
    }
    if publish_lon is None or wj_lon is None:
        return out
    out["compared"] = True
    dlon = wrap_diff(float(publish_lon), float(wj_lon))
    dlat = 0.0
    if publish_lat is not None and wj_lat is not None:
        dlat = float(publish_lat) - float(wj_lat)
    lat0 = float(wj_lat) if wj_lat is not None else (float(publish_lat) if publish_lat is not None else -22.0)
    sky = sky_error_arcsec(dlon, dlat, lat0, float(distance_au or 5.2))
    out["delta_lon_deg"] = dlon
    out["delta_lat_deg"] = dlat
    out["sky_error_arcsec"] = sky
    out["cm_source"] = cm_source
    cm_ok = any(k in (cm_source or "").lower() for k in ("winjupos", "override", "spice", "horizons"))
    out["cm_source_trusted"] = cm_ok

    if sky <= EQUAL_SKY_ARCSEC and cm_ok:
        out["equal_to_winjupos"] = True
        out["agreement"] = "EQUAL_TO_WINJUPOS"
        out["note"] = (
            f"Same result class as your WinJUPOS pick (Δsky={sky:.3f}″ ≤ {EQUAL_SKY_ARCSEC}″) "
            f"with trusted CM source ({cm_source})."
        )
    elif sky <= EQUAL_SKY_ARCSEC and not cm_ok:
        out["equal_to_winjupos"] = False
        out["agreement"] = "MATCH_FEATURE_CM_WEAK"
        out["note"] = (
            f"Feature match excellent (Δsky={sky:.3f}″) but CM source is '{cm_source}'. "
            "Paste WinJUPOS CM or enable SPICE for absolute System III equality."
        )
    elif sky <= NEAR_SKY_ARCSEC:
        out["agreement"] = "NEAR_WINJUPOS"
        out["note"] = f"Close to your WinJUPOS pick (Δsky={sky:.3f}″). Check outline size / definition."
    elif sky <= FAIR_SKY_ARCSEC:
        out["agreement"] = "FAIR_VS_WINJUPOS"
        out["note"] = f"Fair (Δsky={sky:.3f}″). Likely definition or limb outline mismatch."
    else:
        out["agreement"] = "DIFFERENT_FROM_WINJUPOS"
        out["note"] = (
            f"Not the same (Δsky={sky:.3f}″). Different definition (edge vs core), "
            "wrong CM/time, or wrong feature."
        )
    return out


def apply_publish_policy(package: Dict[str, Any]) -> Dict[str, Any]:
    """
    After gold + twin + sota are attached, set the official published answer.

    Mutates package in place; returns the publish block.
    """
    h = package.setdefault("headline", {})
    twin = package.get("winjupos_twin") or {}
    gs = package.get("gold_standard") or {}
    sota = package.get("sota") or {}
    am = package.get("all_methods") or gs.get("all_methods") or {}
    rg = package.get("research_grade") or {}
    champ = package.get("champion") or {}

    # Pipeline stack (secondary)
    pipe_lon = _f(h.get("pipeline_lon_iii_deg") or h.get("lon_iii_deg"))
    pipe_lat = _f(h.get("pipeline_lat_deg") or h.get("lat_deg"))
    if pipe_lon is None:
        pipe_lon = _f(rg.get("lon_bias_corrected_deg"))
    if pipe_lat is None:
        pipe_lat = _f(rg.get("lat_bias_corrected_deg"))

    # Prefer: lat-core GRS band candidates first (not near-limb pipeline locks).
    # Old bug: pipeline ~189° forced reject of good GS-MAP ~13°/290° because
    # |Δ|>30° — that made moon/limb locks "win" over real GRS.
    try:
        from accuracy_gates import grs_lat_in_core_band, grs_lat_in_wide_band
    except Exception:
        def grs_lat_in_core_band(lat):  # type: ignore
            return lat is not None and -28 <= float(lat) <= -16
        def grs_lat_in_wide_band(lat):  # type: ignore
            return lat is not None and -36 <= float(lat) <= -10

    cm_for_edge = _f(h.get("cm_iii_deg")) or _f(twin.get("cm_iii_deg"))

    def _cand_score(cdef: str, clon: Optional[float], clat: Optional[float]) -> float:
        if clon is None:
            return -1e9
        s = 0.0
        if clat is not None and grs_lat_in_core_band(clat):
            s += 100.0
        elif clat is not None and grs_lat_in_wide_band(clat):
            s += 40.0
        else:
            s -= 80.0  # reject poles / EZ
        if cm_for_edge is not None:
            rel = abs(wrap_diff(float(clon), float(cm_for_edge)))
            if rel > 75.0:
                s -= 50.0  # near limb / map edge
            elif rel > 55.0:
                s -= 20.0
            else:
                s += 10.0
        # Prefer colour oval / GS-MAP over soup barycentres
        cu = (cdef or "").upper()
        if "ORANGE" in cu:
            s += 45.0
        elif cu.startswith("GS-MAP"):
            s += 25.0
        elif cu.startswith("GS-BARY"):
            s += 5.0
        if pipe_lon is not None and clat is not None and grs_lat_in_core_band(clat):
            dpp = abs(wrap_diff(float(clon), float(pipe_lon)))
            if dpp <= 30.0:
                s += 15.0
        return s

    pub_def = "PIPELINE"
    pub_lon = pipe_lon
    pub_lat = pipe_lat
    candidates = []
    # Champion / UNBEATABLE_AUTO first when absolute or ultimate gates pass
    if champ.get("ok") and (champ.get("unbeatable_auto") or champ.get("absolute_publish_ok")):
        cl = _f(champ.get("lon_iii_deg"))
        ca = _f(champ.get("lat_planetocentric_deg") or champ.get("lat_deg"))
        if cl is not None:
            label = (
                "UNBEATABLE_AUTO"
                if champ.get("unbeatable_auto")
                else str(champ.get("definition") or "CHAMPION")
            )
            candidates.append((label, cl, ca))
    # Colour orange GRS seed (RGB stacks) — highest priority when lat is core-band
    og = package.get("orange_grs") or {}
    if og.get("ok") and not og.get("near_limb"):
        candidates.append(("GS-ORANGE", _f(og.get("lon_iii_deg")), _f(og.get("lat_deg"))))
    if _f(twin.get("gs_map_lon")) is not None:
        label = "GS-MAP"
        if twin.get("orange_grs_seed"):
            label = "GS-MAP+ORANGE"
        candidates.append((label, _f(twin.get("gs_map_lon")), _f(twin.get("gs_map_lat"))))
    if _f(twin.get("twin_lon_iii_deg")) is not None and str(
        twin.get("twin_primary_definition") or ""
    ).startswith("GS-"):
        candidates.append((
            str(twin.get("twin_primary_definition")),
            _f(twin.get("twin_lon_iii_deg")),
            _f(twin.get("twin_lat_deg")),
        ))
    if _f(twin.get("gs_bary_lon")) is not None:
        candidates.append(("GS-BARY", _f(twin.get("gs_bary_lon")), _f(twin.get("gs_bary_lat"))))
    if gs.get("ok") and _f(gs.get("primary_lon_iii_deg")) is not None:
        gd = str(gs.get("primary_definition") or "")
        # Only named GS-* definitions become publish candidates (never SOTA_ROBUST / soup)
        if gd.startswith("GS-") and not gd.startswith("GS-EDGE") and "SOTA" not in gd.upper():
            candidates.append((gd, _f(gs.get("primary_lon_iii_deg")), _f(gs.get("primary_lat_deg"))))
    # Champion even if not absolute_ok — only as last GS-like candidate if sane
    if champ.get("ok") and not champ.get("absolute_publish_ok"):
        cl = _f(champ.get("lon_iii_deg"))
        ca = _f(champ.get("lat_planetocentric_deg"))
        if cl is not None:
            candidates.append((str(champ.get("definition") or "CHAMPION"), cl, ca))

    best_s = -1e18
    for cdef, clon, clat in candidates:
        if clon is None:
            continue
        sc = _cand_score(str(cdef), clon, clat)
        if sc > best_s:
            best_s = sc
            pub_def = cdef
            pub_lon = clon
            pub_lat = clat

    cm_source = str(h.get("cm_source") or twin.get("cm_source") or gs.get("cm_source") or "")
    dist = _f(h.get("distance_au")) or _f(twin.get("distance_au")) or 5.2
    cm_iii = _f(h.get("cm_iii_deg")) or _f(twin.get("cm_iii_deg"))

    # Manual WJ from twin or gold
    wj = twin.get("winjupos_manual") or gs.get("winjupos_manual") or package.get("winjupos_validation") or {}
    wj_lon = _f(wj.get("winjupos_manual_lon_iii_deg"))
    wj_lat = _f(wj.get("winjupos_manual_lat_deg"))

    equality = assess_winjupos_equality(
        publish_lon=pub_lon,
        publish_lat=pub_lat,
        wj_lon=wj_lon,
        wj_lat=wj_lat,
        distance_au=float(dist),
        cm_source=cm_source,
    )

    # Soup / SOTA = scatter only
    n_soup = int(am.get("n_total") or am.get("n_ok") or 0)
    if not n_soup and isinstance(am.get("methods"), list):
        n_soup = len(am["methods"])
    sota_lon = _f(sota.get("lon_iii_deg")) if sota.get("ok") else None
    sota_lat = _f(sota.get("lat_deg")) if sota.get("ok") else None
    soup_note = (
        f"Method soup ({n_soup} estimators) and SOTA consensus are for "
        "definition scatter / confidence only — NOT the published GRS position."
    )

    dlon_pipe = wrap_diff(pub_lon, pipe_lon) if pub_lon is not None and pipe_lon is not None else None
    dlon_sota = wrap_diff(pub_lon, sota_lon) if pub_lon is not None and sota_lon is not None else None

    publish = {
        "policy": "GS-MAP_THEN_GS-BARY_PUBLISH; SOUP_SCATTER_ONLY",
        "publish_definition": pub_def,
        "publish_lon_iii_deg": pub_lon,
        "publish_lat_deg": pub_lat,
        "cm_iii_deg": cm_iii,
        "cm_source": cm_source,
        "distance_au": dist,
        "pipeline_lon_iii_deg": pipe_lon,
        "pipeline_lat_deg": pipe_lat,
        "pipeline_delta_lon_deg": dlon_pipe,
        "sota_lon_iii_deg": sota_lon,
        "sota_lat_deg": sota_lat,
        "sota_delta_lon_deg": dlon_sota,
        "sota_role": "scatter_confidence_only",
        "soup_n_methods": n_soup,
        "soup_role": "scatter_only",
        "soup_note": soup_note,
        "limb_outline_sky_spread_arcsec": _f(twin.get("limb_sky_spread_arcsec") or h.get("limb_outline_sky_spread_arcsec")),
        "definition_lon_spread_deg": _f(twin.get("definition_lon_spread_deg") or h.get("definition_lon_spread_deg")),
        "winjupos_equality": equality,
        "how_to_cite": (
            f"GRS {pub_def} lon={pub_lon}° lat={pub_lat}° "
            f"(CM III={cm_iii}° source={cm_source}). "
            "Method soup not used for the published centre."
        ),
    }

    # Prefer candidates whose lat is in GRS wide band (JUPOS feature ID discipline)
    try:
        from accuracy_gates import grs_lat_in_wide_band
        if pub_lat is not None and not grs_lat_in_wide_band(pub_lat):
            if pipe_lat is not None and grs_lat_in_wide_band(pipe_lat):
                pub_def = "PIPELINE_GRS_BAND"
                pub_lon = pipe_lon
                pub_lat = pipe_lat
                publish["publish_definition"] = pub_def
                publish["publish_lon_iii_deg"] = pub_lon
                publish["publish_lat_deg"] = pub_lat
                publish["pipeline_delta_lon_deg"] = 0.0
                publish["how_to_cite"] = (
                    f"GRS {pub_def} lon={pub_lon}° lat={pub_lat}° "
                    f"(CM III={cm_iii}° source={cm_source}). "
                    "Candidate lat was out of GRS band; pipeline used."
                )
                equality = assess_winjupos_equality(
                    publish_lon=pub_lon,
                    publish_lat=pub_lat,
                    wj_lon=wj_lon,
                    wj_lat=wj_lat,
                    distance_au=float(dist),
                    cm_source=cm_source,
                )
                publish["winjupos_equality"] = equality
    except Exception:
        pass

    package["publish"] = publish

    # Professional quality gate (CM trust, limb, lat band, scatter)
    try:
        from accuracy_gates import assess_publish_quality
        quality = assess_publish_quality(package)
        package["publish_quality"] = quality
        publish["quality"] = quality
        publish["publish_ok"] = quality.get("publish_ok")
        publish["absolute_ok"] = quality.get("absolute_ok")
        publish["cm_trusted"] = quality.get("cm_trusted")
        publish["quality_grade"] = quality.get("grade")
    except Exception as e:
        # Fail closed: unknown quality is not "OK to publish absolute"
        quality = {
            "publish_ok": False,
            "absolute_ok": False,
            "grade": "ERROR",
            "error": str(e),
            "flags": ["QUALITY_ASSESS_FAILED"],
            "warnings": [f"publish quality assessment failed: {e}"],
        }
        package["publish_quality"] = quality
        publish["quality"] = quality
        publish["publish_ok"] = False
        publish["absolute_ok"] = False
        publish["quality_grade"] = "ERROR"

    # Dual latitude: planetocentric (engine) + planetographic (WinJUPOS-style)
    try:
        from precision_engine import planetocentric_to_planetographic
        if pub_lat is not None:
            pub_lat_g = planetocentric_to_planetographic(float(pub_lat))
            # Prefer champion graphic lat when that is the publish product
            if champ.get("ok") and champ.get("lat_planetographic_deg") is not None:
                if str(pub_def).upper().startswith("CHAMPION") or str(pub_def) in ("GS-MAP", "GS-TMPL"):
                    try:
                        cg = float(champ["lat_planetographic_deg"])
                        if math.isfinite(cg):
                            pub_lat_g = cg
                    except Exception:
                        pass
            publish["publish_lat_planetocentric_deg"] = pub_lat
            publish["publish_lat_planetographic_deg"] = pub_lat_g
            publish["lat_kind_primary"] = "planetocentric"
            publish["lat_note"] = (
                "Primary lat is planetocentric (engine geometry). "
                "WinJUPOS/JUPOS usually quote planetographic — both exported."
            )
    except Exception:
        pass

    # Attach champion total σ when available (best absolute budget)
    if champ.get("ok"):
        publish["champion_grade"] = champ.get("grade")
        publish["champion_score"] = champ.get("world_class_score")
        publish["champion_sigma_sky_arcsec"] = champ.get("sigma_total_sky_arcsec")
        publish["champion_sigma_lon_deg"] = champ.get("sigma_total_lon_deg")
        publish["champion_absolute_ok"] = champ.get("absolute_publish_ok")
        if str(pub_def).upper().startswith("CHAMPION") or pub_def in ("GS-MAP", "GS-TMPL"):
            if champ.get("sigma_total_sky_arcsec") is not None:
                publish["publish_sigma_sky_arcsec"] = champ.get("sigma_total_sky_arcsec")
                publish["publish_sigma_lon_deg"] = champ.get("sigma_total_lon_deg")

    # Rewrite headline so UI shows published answer first
    h["publish_definition"] = pub_def
    h["publish_lon_iii_deg"] = pub_lon
    h["publish_lat_deg"] = pub_lat
    if publish.get("publish_lat_planetographic_deg") is not None:
        h["publish_lat_planetographic_deg"] = publish["publish_lat_planetographic_deg"]
        h["lat_planetographic_deg"] = publish["publish_lat_planetographic_deg"]
    h["publish_policy"] = publish["policy"]
    h["soup_role"] = "scatter_only"
    h["soup_n_methods"] = n_soup
    h["equal_to_winjupos"] = equality.get("equal_to_winjupos")
    h["winjupos_agreement"] = equality.get("agreement")
    h["vs_winjupos_sky_arcsec"] = equality.get("sky_error_arcsec")
    # Keep pipeline under separate keys; headline lon/lat become PUBLISHED
    h["pipeline_lon_iii_deg"] = pipe_lon
    h["pipeline_lat_deg"] = pipe_lat
    h["cm_source"] = cm_source
    h["publish_ok"] = quality.get("publish_ok")
    h["absolute_ok"] = quality.get("absolute_ok")
    h["quality_grade"] = quality.get("grade")
    h["quality_flags"] = quality.get("flags")
    if pub_lon is not None:
        h["lon_iii_deg"] = pub_lon
        h["lat_deg"] = pub_lat
        h["primary_method"] = pub_def
        qg = quality.get("grade")
        if qg == "REJECT":
            h["grade"] = "REJECT"
        elif equality.get("compared"):
            h["grade"] = equality.get("agreement")
        else:
            h["grade"] = qg or (h.get("twin_grade") or h.get("gold_procedure_grade") or h.get("grade"))
    h["how_to_cite"] = publish["how_to_cite"]
    if quality.get("warnings"):
        h["quality_warnings"] = quality["warnings"][:6]
    return publish


def format_publish_section(package: Dict[str, Any]) -> str:
    p = package.get("publish") or apply_publish_policy(package)
    eq = p.get("winjupos_equality") or {}
    q = p.get("quality") or package.get("publish_quality") or {}
    lines = [
        "╔" + "═" * 60 + "╗",
        "║" + " PUBLISH THIS (official GRS position)".center(60) + "║",
        "╚" + "═" * 60 + "╝",
        f"  Definition     {p.get('publish_definition')}",
        f"  Lon III        {p.get('publish_lon_iii_deg')} °",
        f"  Lat            {p.get('publish_lat_deg')} °",
        f"  Quality        {q.get('grade')}  publish_ok={q.get('publish_ok')}  "
        f"absolute_ok={q.get('absolute_ok')}  cm_trusted={q.get('cm_trusted')}",
        f"  CM III         {p.get('cm_iii_deg')} °  ({p.get('cm_source')})",
        f"  How to cite    {p.get('how_to_cite')}",
        "",
        "  METHOD SOUP / SOTA — scatter only, NOT published centre",
        f"  Soup methods   {p.get('soup_n_methods')}  role={p.get('soup_role')}",
        f"  SOTA lon       {p.get('sota_lon_iii_deg')}  (Δ vs publish={p.get('sota_delta_lon_deg')}°)",
        f"  Pipeline lon   {p.get('pipeline_lon_iii_deg')}  (Δ vs publish={p.get('pipeline_delta_lon_deg')}°)",
        f"  Limb outline spread   {p.get('limb_outline_sky_spread_arcsec')} ″",
        f"  Definition lon spread {p.get('definition_lon_spread_deg')} °",
        "",
        "  VS WINJUPOS",
        f"  Agreement      {eq.get('agreement')}",
        f"  Equal?         {eq.get('equal_to_winjupos')}",
        f"  Δsky           {eq.get('sky_error_arcsec')} ″",
        f"  Note           {eq.get('note')}",
        "",
    ]
    if q.get("warnings"):
        lines.append("  QUALITY WARNINGS")
        for w in q["warnings"][:5]:
            lines.append(f"  • {w}")
        lines.append("")
    return "\n".join(lines)

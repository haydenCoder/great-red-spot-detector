#!/usr/bin/env python3
"""
publish_primary.py — decides what number you should actually report

The core rule (WinJUPOS-aligned discipline):
  - PUBLISH: GS-MAP twin (or GS-BARY as fallback) for the official lon/lat
  - SOUP / SOTA: scatter and confidence ONLY — never the published answer
  - EQUAL_TO_WINJUPOS: only when you have same CM discipline AND Δsky ≤ 1″

I rewrote the headline dict in-place here so the UI and CLI always show the
published number first instead of some random pipeline or soup result.

Bug fix note (July 2026): _cand_score was silently giving champion candidates
0 bonus while GS-MAP got +25 — so GS-MAP always "won" over champion even
when champion had UNBEATABLE_AUTO grade. Added +50 for UNBEATABLE_AUTO and
+35 for CHAMPION-prefix so the hierarchy actually works. Took me an embarrassingly
long time to notice this one.
"""
from __future__ import annotations

import math
from typing import Any, Dict, Optional

from precision_engine import wrap_diff, sky_error_arcsec


# how close to your WinJUPOS pick counts as "same result"?
EQUAL_SKY_ARCSEC = 1.0       # treat as same result (pro-amateur agreement)
NEAR_SKY_ARCSEC = 2.0        # close but not equal
FAIR_SKY_ARCSEC = 5.0         # reasonable given definition differences


def _f(x) -> Optional[float]:
    """safe float conversion — returns None for non-finite values"""
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
    """Check if our publish agrees with your WinJUPOS manual pick.

    Needs an actual WJ pick to compare against. CM source has to be
    winjupos / override / spice / horizons for a strong equality claim
    — pure analytical CM doesn't count (too much zero-point drift)."""
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
    # assume GRS latitude if neither has it (it's ~-22°, close enough for sky calc)
    lat0 = float(wj_lat) if wj_lat is not None else (float(publish_lat) if publish_lat is not None else -22.0)
    sky = sky_error_arcsec(dlon, dlat, lat0, float(distance_au or 5.2))
    out["delta_lon_deg"] = dlon
    out["delta_lat_deg"] = dlat
    out["sky_error_arcsec"] = sky
    out["cm_source"] = cm_source
    cm_ok = any(k in (cm_source or "").lower() for k in ("winjupos", "override", "spice", "horizons"))
    out["cm_source_trusted"] = cm_ok

    # now classify the agreement level
    if sky <= EQUAL_SKY_ARCSEC and cm_ok:
        out["equal_to_winjupos"] = True
        out["agreement"] = "EQUAL_TO_WINJUPOS"
        out["note"] = (
            f"Same result class as your WinJUPOS pick (Δsky={sky:.3f}\" ≤ {EQUAL_SKY_ARCSEC}\") "
            f"with trusted CM source ({cm_source})."
        )
    elif sky <= EQUAL_SKY_ARCSEC and not cm_ok:
        out["equal_to_winjupos"] = False
        out["agreement"] = "MATCH_FEATURE_CM_WEAK"
        out["note"] = (
            f"Feature match excellent (Δsky={sky:.3f}\") but CM source is '{cm_source}'. "
            "Paste WinJUPOS CM or enable SPICE for absolute System III equality."
        )
    elif sky <= NEAR_SKY_ARCSEC:
        out["agreement"] = "NEAR_WINJUPOS"
        out["note"] = f"Close to your WinJUPOS pick (Δsky={sky:.3f}\"). Check outline size / definition."
    elif sky <= FAIR_SKY_ARCSEC:
        out["agreement"] = "FAIR_VS_WINJUPOS"
        out["note"] = f"Fair (Δsky={sky:.3f}\"). Likely definition or limb outline mismatch."
    else:
        out["agreement"] = "DIFFERENT_FROM_WINJUPOS"
        out["note"] = (
            f"Not the same (Δsky={sky:.3f}\"). Different definition (edge vs core), "
            "wrong CM/time, or wrong feature."
        )
    return out


def apply_publish_policy(package: Dict[str, Any]) -> Dict[str, Any]:
    """After gold + twin + sota are computed, pick the official published answer.

    Mutates the package in-place and returns the publish block.
    This is where the publish hierarchy actually gets enforced — before
    this function runs, headline lon/lat could be anything (pipeline, soup,
    whatever happened to be last). After it, headline shows the ONE number
    you should report."""
    h = package.setdefault("headline", {})
    twin = package.get("winjupos_twin") or {}
    gs = package.get("gold_standard") or {}
    sota = package.get("sota") or {}
    am = package.get("all_methods") or gs.get("all_methods") or {}
    rg = package.get("research_grade") or {}
    champ = package.get("champion") or {}

    # Pipeline stack (secondary reference, not the primary publish)
    pipe_lon = _f(h.get("pipeline_lon_iii_deg") or h.get("lon_iii_deg"))
    pipe_lat = _f(h.get("pipeline_lat_deg") or h.get("lat_deg"))
    if pipe_lon is None:
        pipe_lon = _f(rg.get("lon_bias_corrected_deg"))
    if pipe_lat is None:
        pipe_lat = _f(rg.get("lat_bias_corrected_deg"))

    # We prefer candidates whose latitude falls in the GRS core band
    # (roughly -16° to -28°). This avoids moon locks and near-limb
    # pipeline false hits that look "good" but are wrong features.
    # I originally had this backwards — pipeline ~189° was "winning"
    # over a good GS-MAP at ~290° because Δ was >30° and it got
    # rejected. That was a really annoying bug to track down.
    try:
        from accuracy_gates import grs_lat_in_core_band, grs_lat_in_wide_band
    except Exception:
        def grs_lat_in_core_band(lat):  # type: ignore
            return lat is not None and -28 <= float(lat) <= -16
        def grs_lat_in_wide_band(lat):  # type: ignore
            return lat is not None and -36 <= float(lat) <= -10

    cm_for_edge = _f(h.get("cm_iii_deg")) or _f(twin.get("cm_iii_deg"))

    def _cand_score(cdef: str, clon: Optional[float], clat: Optional[float]) -> float:
        """Score a publish candidate so the best one wins.

        Lat band matching is the biggest signal — if the candidate's
        latitude is in the GRS core band it gets +100, wide band +40,
        poles or EZ get -80. Then CM proximity, label bonuses (orange,
        champion, GS-MAP), and pipeline agreement all add up.

        The big bug I fixed here: champion was getting 0 bonus while
        GS-MAP got +25, so GS-MAP always won. Now UNBEATABLE_AUTO gets
        +50 and CHAMPION gets +35 so the hierarchy is real."""
        if clon is None:
            return -1e9
        s = 0.0
        # latitude band — this is the biggest signal
        if clat is not None and grs_lat_in_core_band(clat):
            s += 100.0
        elif clat is not None and grs_lat_in_wide_band(clat):
            s += 40.0
        else:
            s -= 80.0  # reject poles / EZ locks

        # CM proximity — near-limb features get penalised
        if cm_for_edge is not None:
            rel = abs(wrap_diff(float(clon), float(cm_for_edge)))
            if rel > 75.0:
                s -= 50.0  # near limb / map edge
            elif rel > 55.0:
                s -= 20.0
            else:
                s += 10.0
        # Prefer colour oval / champion / GS-MAP over soup barycentres
        cu = (cdef or "").upper()
        if "ORANGE" in cu:
            s += 45.0
        elif cu == "UNBEATABLE_AUTO":
            s += 50.0
        elif cu.startswith("CHAMPION"):
            s += 35.0
        elif cu.startswith("GS-MAP"):
            s += 25.0
        elif cu.startswith("GS-BARY"):
            s += 5.0

        # Pipeline agreement bonus when candidate is in GRS band
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

    # Colour orange GRS (RGB stacks) — high priority when lat is core-band
    og = package.get("orange_grs") or {}
    if og.get("ok") and not og.get("near_limb"):
        candidates.append(("GS-ORANGE", _f(og.get("lon_iii_deg")), _f(og.get("lat_deg"))))

    # GS-MAP twin (the classic publish definition)
    if _f(twin.get("gs_map_lon")) is not None:
        label = "GS-MAP"
        if twin.get("orange_grs_seed"):
            label = "GS-MAP+ORANGE"
        candidates.append((label, _f(twin.get("gs_map_lon")), _f(twin.get("gs_map_lat"))))

    # Other twin candidates with GS-* definitions
    if _f(twin.get("twin_lon_iii_deg")) is not None and str(
        twin.get("twin_primary_definition") or ""
    ).startswith("GS-"):
        candidates.append((
            str(twin.get("twin_primary_definition")),
            _f(twin.get("twin_lon_iii_deg")),
            _f(twin.get("twin_lat_deg")),
        ))

    # GS-BARY fallback
    if _f(twin.get("gs_bary_lon")) is not None:
        candidates.append(("GS-BARY", _f(twin.get("gs_bary_lon")), _f(twin.get("gs_bary_lat"))))

    # Gold standard primary (only real GS-* definitions, never SOTA/soup)
    if gs.get("ok") and _f(gs.get("primary_lon_iii_deg")) is not None:
        gd = str(gs.get("primary_definition") or "")
        if gd.startswith("GS-") and not gd.startswith("GS-EDGE") and "SOTA" not in gd.upper():
            candidates.append((gd, _f(gs.get("primary_lon_iii_deg")), _f(gs.get("primary_lat_deg"))))

    # Champion as last GS-like candidate (even if not absolute_ok, if it's sane)
    if champ.get("ok") and not champ.get("absolute_publish_ok"):
        cl = _f(champ.get("lon_iii_deg"))
        ca = _f(champ.get("lat_planetocentric_deg"))
        if cl is not None:
            candidates.append((str(champ.get("definition") or "CHAMPION"), cl, ca))

    # Pick the best-scoring candidate
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

    # WinJUPOS manual comparison
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

    # Soup / SOTA = scatter only, never the published centre
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

    # System II mapping (IAU frame rotation). The published L_II / CM_II are
    # derived from the published L_III / CM_III (not the champion's), so the
    # card stays internally consistent whichever method wins. The champion's
    # cm_ii is kept only as a fallback when no observation time is available.
    cm_ii = None
    pub_lon_ii = None
    sys2_offset = None
    obs_time = h.get("user_time") or h.get("synth_epoch") or h.get("time_utc")
    if cm_iii is not None and obs_time:
        try:
            from system_ii import derive_system_ii
            s2 = derive_system_ii(
                float(cm_iii), str(obs_time),
                lon_iii_deg=(float(pub_lon) if pub_lon is not None else None),
                source=str(cm_source or ""),
            )
            cm_ii = s2.cm_ii_deg
            pub_lon_ii = s2.lon_ii_deg
            sys2_offset = s2.offset_deg
        except Exception:
            pass
    if cm_ii is None:
        cm_ii = _f(champ.get("cm_ii_deg"))

    publish = {
        "policy": "GS-MAP_THEN_GS-BARY_PUBLISH; SOUP_SCATTER_ONLY",
        "publish_definition": pub_def,
        "publish_lon_iii_deg": pub_lon,
        "publish_lon_ii_deg": pub_lon_ii,
        "publish_lat_deg": pub_lat,
        "cm_iii_deg": cm_iii,
        "cm_ii_deg": cm_ii,
        "system_ii_offset_deg": sys2_offset,
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

    # Safety check: if publish lat falls outside the GRS band, fall back
    # to pipeline (which might still be in the band). This shouldn't
    # happen normally but I've seen it on some weird frames.
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
        pass  # if accuracy_gates isn't available we just skip the check

    package["publish"] = publish

    # Quality assessment gate (CM trust, limb, lat band, scatter)
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
        # fail closed: if we can't assess quality, it's NOT safe to publish
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

    # Dual latitude: planetocentric (engine) vs planetographic (WinJUPOS-style)
    # WinJUPOS users always quote planetographic so we export both
    try:
        from precision_engine import planetocentric_to_planetographic
        if pub_lat is not None:
            pub_lat_g = planetocentric_to_planetographic(float(pub_lat))
            # when champion is the publish product, prefer its own graphic lat
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

    # Rewrite headline so UI/CLI always shows the PUBLISHED answer first
    # (before this, headline could be pipeline or soup — now it's the real publish)
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
    """Compact publish card — key numbers only."""
    p = package.get("publish") or apply_publish_policy(package)
    eq = p.get("winjupos_equality") or {}
    q = p.get("quality") or package.get("publish_quality") or {}
    lat_g = p.get("publish_lat_planetographic_deg")
    lines = [
        "PUBLISH",
        "=======",
        f"lon_III   {p.get('publish_lon_iii_deg')} °",
        f"lon_II    {p.get('publish_lon_ii_deg')} °" if p.get("publish_lon_ii_deg") is not None else None,
        f"lat_c     {p.get('publish_lat_deg')} °",
        f"lat_g     {lat_g} °" if lat_g is not None else None,
        f"CM_III    {p.get('cm_iii_deg')} °  [{p.get('cm_source')}]",
        f"CM_II     {p.get('cm_ii_deg')} °" if p.get("cm_ii_deg") is not None else None,
        f"def       {p.get('publish_definition')}",
        f"grade     {q.get('grade')}  ok={q.get('publish_ok')}  abs={q.get('absolute_ok')}",
        f"vs_WJ     {eq.get('agreement')}  Δsky={eq.get('sky_error_arcsec')} ″",
        "",
    ]
    return "\n".join(x for x in lines if x is not None)

#!/usr/bin/env python3
"""
JPL Horizons Jupiter geometry only — planet orientation, not a GRS position catalog.

Reports YOUR measured GRS lon/lat/size as-is, plus real Horizons fields
(distance, CM III, sub-observer lat, NP PA, light time). Does **not** invent
a fake “NASA GRS lon” for comparison.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import ssl
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from verbose_log import CONSOLE
from netutil import secure_ssl_context as _ssl_context

CACHE = Path(__file__).resolve().parent / "nasa_cache"
CACHE.mkdir(exist_ok=True)




@dataclass
class NASAComparison:
    ok: bool
    source: str
    user_time_iso: str
    measured: Dict[str, float]
    reference: Dict[str, float]  # Horizons geometry only (legacy key name)
    deltas: Dict[str, float]  # empty — no fake GRS REF deltas
    flags: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def grade(self) -> str:
        if self.ok and (self.reference.get("horizons_cm_iii_deg") is not None
                        or self.reference.get("jupiter_distance_au") is not None):
            return "GEOMETRY_OK (Horizons Jupiter geometry; no NASA GRS lon product)"
        if self.flags:
            return "GEOMETRY_PARTIAL / OFFLINE"
        return "NO_HORIZONS"


def fetch_horizons(t: dt.datetime, timeout: float = 10.0) -> Optional[Dict[str, Any]]:
    """Legacy wrapper — prefer ephemeris_pro.fetch_horizons_full for research geometry."""
    key = t.strftime("%Y%m%dT%H%M")
    cf = CACHE / f"horizons_{key}.json"
    if cf.exists():
        try:
            return json.loads(cf.read_text())
        except Exception:
            pass
    # Delegate to full parser when available
    try:
        from ephemeris_pro import fetch_horizons_full
        full = fetch_horizons_full(t, timeout=timeout)
        if full:
            dist = float(full.get("distance_au") or 5.2)
            out = {
                "ok": True,
                "distance_au_model": dist,
                "apparent_diameter_arcsec_model": float(
                    full.get("apparent_diameter_arcsec")
                    or math.degrees(2 * 71492e3 / (dist * 1.495978707e11)) * 3600
                ),
                "sub_obs_lat_deg": full.get("sub_obs_lat_deg"),
                "sub_obs_lon_deg": full.get("sub_obs_lon_deg"),
                "north_pa_deg": full.get("north_pa_deg"),
                "cm_iii_deg": full.get("cm_iii_deg"),
                "light_time_s": full.get("light_time_s"),
                "excerpt": full.get("excerpt", "")[:1500],
                "full": full,
            }
            cf.write_text(json.dumps(out, indent=2))
            return out
    except Exception as e:
        CONSOLE.debug(f"full Horizons delegate: {e}")

    start = t.strftime("%Y-%m-%d %H:%M")
    stop = (t + dt.timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M")
    params = {
        "format": "text",
        "COMMAND": "'599'",
        "OBJ_DATA": "YES",
        "MAKE_EPHEM": "YES",
        "EPHEM_TYPE": "OBSERVER",
        "CENTER": "'500@399'",
        "START_TIME": f"'{start}'",
        "STOP_TIME": f"'{stop}'",
        "STEP_SIZE": "'1 m'",
        "QUANTITIES": "'1,9,13,14,20,23,24,31,32'",
    }
    url = "https://ssd.jpl.nasa.gov/api/horizons.api?" + urllib.parse.urlencode(params)
    CONSOLE.info("Contacting NASA JPL Horizons...")
    text = None
    for label, ctx in (("secure", _ssl_context()), ("unverified", ssl._create_unverified_context())):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "GRS-Observatory/6.5"})
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                text = resp.read().decode("utf-8", errors="replace")
            if label == "unverified":
                CONSOLE.warn("Horizons used unverified SSL fallback")
            break
        except Exception as e:
            CONSOLE.debug(f"Horizons {label}: {e}")
    if text is None:
        CONSOLE.warn("Horizons offline — geometry model only")
        return None
    # Parse the response — never invent AU and label it as Horizons success.
    try:
        from ephemeris_pro import parse_horizons_observer_text
        parsed = parse_horizons_observer_text(text)
    except Exception as e:
        CONSOLE.warn(f"Horizons text present but parse failed: {e}")
        return None
    dist = parsed.get("distance_au")
    if dist is None or not math.isfinite(float(dist)):
        CONSOLE.warn("Horizons response could not yield distance — not caching as success")
        return None
    dist = float(dist)
    out = {
        "ok": True,
        "distance_au_model": dist,
        "distance_au": dist,
        "apparent_diameter_arcsec_model": float(
            parsed.get("apparent_diameter_arcsec")
            or math.degrees(2 * 71492e3 / (dist * 1.495978707e11)) * 3600
        ),
        "sub_obs_lat_deg": parsed.get("sub_obs_lat_deg"),
        "sub_obs_lon_deg": parsed.get("sub_obs_lon_deg"),
        "north_pa_deg": parsed.get("north_pa_deg"),
        "cm_iii_deg": parsed.get("cm_iii_deg") or parsed.get("sub_obs_lon_deg"),
        "light_time_s": parsed.get("light_time_s"),
        "excerpt": text[:1500],
        "parser": parsed.get("parser"),
    }
    cf.write_text(json.dumps(out, indent=2))
    CONSOLE.ok("Horizons response parsed and cached")
    return out


def compare_measurement_to_nasa(measured: Dict[str, float], user_time_iso: str, time_error_seconds: float = 0.0) -> NASAComparison:
    t = dt.datetime(2000, 1, 1)
    raw = (user_time_iso or "").strip().replace("T", " ").replace("Z", "")
    if not raw:
        raise ValueError("Observation UTC required for NASA/geometry compare (no silent now).")
    parsed = False
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            t = dt.datetime.strptime(raw[:19] if "%H" in fmt else raw[:10], fmt)
            parsed = True
            break
        except Exception:
            pass
    if not parsed:
        try:
            t = dt.datetime.fromisoformat(raw.split(".")[0])
            parsed = True
        except Exception as e:
            raise ValueError(f"Cannot parse observation time {user_time_iso!r}") from e
    t = t + dt.timedelta(seconds=float(time_error_seconds))
    # Geometry only — no invented GRS lon/lat/size "reference"
    geom: Dict[str, Any] = {
        "kind": "horizons_jupiter_geometry_only",
        "note": (
            "NASA/JPL Horizons provides Jupiter geometry (CM, distance, orientation). "
            "There is no official NASA GRS longitude product for this table."
        ),
    }
    h = fetch_horizons(t)
    source = "Horizons_geometry" if h else "Horizons_offline"
    flags: List[str] = []
    if h:
        geom["jupiter_distance_au"] = h.get("distance_au_model") or h.get("distance_au")
        geom["jupiter_app_diam_arcsec"] = h.get("apparent_diameter_arcsec_model")
        if h.get("sub_obs_lat_deg") is not None:
            geom["sub_obs_lat_deg"] = h.get("sub_obs_lat_deg")
        if h.get("north_pa_deg") is not None:
            geom["north_pa_deg"] = h.get("north_pa_deg")
        if h.get("cm_iii_deg") is not None:
            geom["horizons_cm_iii_deg"] = h.get("cm_iii_deg")
        if h.get("light_time_s") is not None:
            geom["light_time_s"] = h.get("light_time_s")
        if h.get("sub_obs_lon_deg") is not None:
            geom["sub_obs_lon_deg"] = h.get("sub_obs_lon_deg")
    else:
        flags.append("HORIZONS_UNAVAILABLE")

    notes = [
        "YOUR GRS lon/lat/size = measured on your image (this software).",
        "JPL Horizons = Jupiter geometry only (distance, CM III, orientation) — NOT GRS centre.",
        "NASA does not publish a continuous GRS System III lon catalog for arbitrary epochs.",
        "Validate absolute lon with SPICE/WinJUPOS CM + same definition (e.g. GS-MAP core).",
        "On synthetics, use truth_recovery sky_error_arcsec as the accuracy metric.",
    ]
    measured_out = {}
    for k in ("lon_iii_deg", "lat_deg", "length_deg", "width_deg"):
        try:
            measured_out[k] = float(measured.get(k))  # type: ignore[arg-type]
        except Exception:
            measured_out[k] = float("nan")

    return NASAComparison(
        ok="HORIZONS_UNAVAILABLE" not in flags,
        source=source,
        user_time_iso=t.isoformat(),
        measured=measured_out,
        reference=geom,
        deltas={},  # deliberately empty — no fake GRS REF deltas
        flags=flags,
        notes=notes,
    )


def write_comparison_report(path: Path, comp: NASAComparison) -> None:
    """Write JSON + human TXT: YOUR measure + Horizons geometry (no fake GRS REF)."""
    path = Path(path)
    d = comp.to_dict()
    d["grade"] = comp.grade()
    path.write_text(json.dumps(d, indent=2), encoding="utf-8")
    try:
        from result_report import format_nasa_txt
        body = format_nasa_txt(d)
    except Exception:
        m, r = d.get("measured") or {}, d.get("reference") or {}
        body = "\n".join([
            "YOUR GRS MEASURE + JPL HORIZONS GEOMETRY",
            f"Grade: {comp.grade()}",
            f"Source: {comp.source}",
            f"Epoch: {comp.user_time_iso}",
            "",
            f"YOUR GRS  lon={m.get('lon_iii_deg')}  lat={m.get('lat_deg')}  "
            f"L={m.get('length_deg')}  W={m.get('width_deg')}",
            "",
            f"HORIZONS CM III = {r.get('horizons_cm_iii_deg')}  (geometry, not GRS)",
            f"Distance AU    = {r.get('jupiter_distance_au')}",
            f"Sub-obs lat    = {r.get('sub_obs_lat_deg')}",
            "",
            "NOTE: No NASA GRS longitude catalog is used. Deltas vs a fake REF lon were removed.",
            "",
            "FULL JSON:",
            json.dumps(d, indent=2, default=str),
        ])
    path.with_suffix(".txt").write_text(body, encoding="utf-8")

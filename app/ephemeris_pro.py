#!/usr/bin/env python3
"""
Professional Jupiter ephemeris for research-grade absolute System III work
=========================================================================

This is the module that resolves Central Meridian III and all the other
geometry parameters (distance, sub-observer latitude, north pole PA) that
you need for an absolute longitude measurement. Without good CM, your
lon is just relative — and analytical CM can be 10–15° off, which I found
out the hard way when my "absolute" measurements were drifting all over
the place compared to WinJUPOS.

Priority chain (first success wins for each field, with provenance):

  1) Explicit overrides (cm_iii, distance, sub-lat, NP PA) — WinJUPOS / user paste
  2) CM CSV or JSON table at epoch
  3) **SPICE auto** (spice_auto: online kernel download + spiceypy) — preferred absolute path
  4) NASA JPL Horizons full observer parse (Δ, light-time, sub-obs lon/lat, NP.ang)
  5) Analytical fallback (differentials OK; absolute CM zero may be offset)

SPICE kernels are auto-searched/downloaded — users never need to hunt NAIF files.
Horizons is **geometry**, not a GRS longitude product. GRS lon still comes from
your image measurement; CM III ties that measurement to System III.
"""
from __future__ import annotations

import csv
import datetime as dt
import json
import math
import os
import re
import ssl
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from verbose_log import CONSOLE

APP_DIR = Path(__file__).resolve().parent
CACHE = APP_DIR / "nasa_cache"
EPH_DIR = APP_DIR / "ephemeris_data"
CACHE.mkdir(exist_ok=True)
EPH_DIR.mkdir(exist_ok=True)

JUP_REQ_KM = 71492.0
AU_KM = 149597870.7
SYS3_PERIOD_S = 9 * 3600 + 55 * 60 + 29.711
DEG_PER_SEC_SYS3 = 360.0 / SYS3_PERIOD_S
C_M_S = 299792458.0


def wrap_deg(x: float) -> float:
    return float(x % 360.0)


def wrap_diff(a: float, b: float) -> float:
    return float((a - b + 180.0) % 360.0 - 180.0)


def _ssl_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        try:
            return ssl.create_default_context()
        except Exception:
            return ssl._create_unverified_context()


def parse_time(s: str) -> dt.datetime:
    s = (s or "").strip().replace("T", " ").replace("Z", "")
    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%Y/%m/%d %H:%M:%S",
        "%d.%m.%Y %H:%M:%S",
        "%Y%m%d %H%M%S",
    ):
        try:
            n = 26 if "%f" in fmt else (19 if "%H" in fmt else 10)
            return dt.datetime.strptime(s[:n], fmt)
        except Exception:
            continue
    raise ValueError(f"Cannot parse time: {s}")


@dataclass
class ProEphemeris:
    """Observer-centric Jupiter geometry + provenance for publication."""
    t_utc_iso: str
    distance_au: float = 5.2
    cm_iii_deg: float = 0.0  # central meridian System III (≈ sub-observer lon III)
    sub_obs_lat_deg: float = 0.0
    sub_obs_lon_deg: float = 0.0
    north_pa_deg: float = 0.0  # Jovian north position angle (E of N) on sky
    apparent_diameter_arcsec: float = 40.0
    light_time_s: float = 0.0
    # quality
    source: str = "analytical"
    cm_source: str = "analytical"
    orientation_source: str = "none"
    distance_source: str = "analytical"
    sigma_cm_deg: float = 0.5
    sigma_distance_frac: float = 0.01
    sigma_pa_deg: float = 2.0
    sigma_sublat_deg: float = 0.5
    apply_orientation: bool = False  # True when orientation is research-grade
    notes: List[str] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        return d

    def to_vlbi_ephemeris_state(self):
        """Bridge to vlbi_metrology.EphemerisState without circular import at module load."""
        from vlbi_metrology import EphemerisState
        return EphemerisState(
            t_utc_iso=self.t_utc_iso,
            distance_au=self.distance_au,
            cm_iii_deg=self.cm_iii_deg,
            sub_obs_lat_deg=self.sub_obs_lat_deg,
            sub_obs_lon_deg=self.sub_obs_lon_deg,
            north_pa_deg=self.north_pa_deg,
            apparent_diameter_arcsec=self.apparent_diameter_arcsec,
            light_time_s=self.light_time_s,
            source=self.source,
            sigma_cm_deg=self.sigma_cm_deg,
            sigma_distance_frac=self.sigma_distance_frac,
            sigma_pa_deg=self.sigma_pa_deg,
            sigma_sublat_deg=self.sigma_sublat_deg,
            notes=list(self.notes),
        )


# ---------------------------------------------------------------------------
# Analytical fallback
# ---------------------------------------------------------------------------

def analytical_geometry(t: dt.datetime) -> Dict[str, float]:
    mjd0 = dt.datetime(1858, 11, 17)
    mjd = (t - mjd0).total_seconds() / 86400.0
    tdb = mjd + 69.184 / 86400.0
    period_days = SYS3_PERIOD_S / 86400.0
    year_frac = t.year + t.timetuple().tm_yday / 365.25
    dist = 5.2 + 0.55 * math.cos(2 * math.pi * (year_frac - 2000) / 1.09)
    lt = (dist * AU_KM * 1000.0) / C_M_S
    cm = wrap_deg(360.0 * ((tdb - 51544.5) / period_days) - DEG_PER_SEC_SYS3 * lt)
    diam = math.degrees(2 * JUP_REQ_KM / (dist * AU_KM)) * 3600.0
    sub_lat = 3.0 * math.sin(2 * math.pi * (tdb - 51544.5) / (11.86 * 365.25))
    return {
        "distance_au": dist,
        "cm_iii_deg": cm,
        "sub_obs_lat_deg": sub_lat,
        "sub_obs_lon_deg": cm,
        "north_pa_deg": 0.0,
        "apparent_diameter_arcsec": diam,
        "light_time_s": lt,
    }


# ---------------------------------------------------------------------------
# Horizons full observer parse
# ---------------------------------------------------------------------------

def fetch_horizons_full(
    t: dt.datetime,
    site_lon: float = 0.0,
    site_lat: float = 0.0,
    site_elev_m: float = 0.0,
    timeout: float = 15.0,
    force: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    JPL Horizons observer table for Jupiter (599).

    QUANTITIES include range, light-time, sub-observer lon/lat, NP angle.
    Note: sub-observer longitude from Horizons is the geometric CM in the
    planet's longitude system (for 599, System III-related body frame).
    """
    # Second resolution: Sys III ~0.0084°/s — minute keys alone can bias CM by ~0.5°
    key = t.strftime("%Y%m%dT%H%M%S")
    site_tag = f"_{site_lat:.2f}_{site_lon:.2f}" if (site_lat or site_lon) else ""
    cf = CACHE / f"horizons_full_{key}{site_tag}.json"
    if cf.exists() and not force:
        try:
            return json.loads(cf.read_text(encoding="utf-8"))
        except Exception:
            pass

    start = t.strftime("%Y-%m-%d %H:%M")
    stop = (t + dt.timedelta(minutes=1)).strftime("%Y-%m-%d %H:%M")
    # 13=delta, 14=light-time, 20=obs range, 31=sub-obs lon/lat, 32=N.pole PA & dist
    # Also 1=RA/Dec for diagnostics
    center = "'500@399'"  # geocenter default
    if abs(site_lat) > 0.01 or abs(site_lon) > 0.01:
        # custom site: lon, lat, elev — Horizons coord format
        center = "coord@399"  # will also set SITE_COORD if supported; geocenter fallback
    params = {
        "format": "text",
        "COMMAND": "'599'",
        "OBJ_DATA": "NO",
        "MAKE_EPHEM": "YES",
        "EPHEM_TYPE": "OBSERVER",
        "CENTER": center if center.startswith("'") else "'500@399'",
        "START_TIME": f"'{start}'",
        "STOP_TIME": f"'{stop}'",
        "STEP_SIZE": "'1 m'",
        "QUANTITIES": "'1,13,14,20,31,32'",
        "ANG_FORMAT": "DEG",
        "EXTRA_PREC": "YES",
        "CSV_FORMAT": "YES",
    }
    url = "https://ssd.jpl.nasa.gov/api/horizons.api?" + urllib.parse.urlencode(params)
    CONSOLE.info("Horizons FULL geometry (Δ, LT, sub-obs, NP.ang)...")
    text = None
    for label, ctx in (("secure", _ssl_context()), ("unverified", ssl._create_unverified_context())):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "GRS-Observatory-Pro/4.0 (research)"})
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                text = resp.read().decode("utf-8", errors="replace")
            if label == "unverified":
                CONSOLE.warn("Horizons SSL unverified fallback")
            break
        except Exception as e:
            CONSOLE.debug(f"Horizons full {label}: {e}")
    if text is None:
        CONSOLE.warn("Horizons full offline")
        return None

    parsed = parse_horizons_observer_text(text)
    parsed["ok"] = True
    parsed["excerpt"] = text[:2500]
    parsed["query_time"] = t.isoformat()
    try:
        cf.write_text(json.dumps(parsed, indent=2), encoding="utf-8")
    except Exception:
        pass
    CONSOLE.ok(
        f"Horizons parsed: Δ={parsed.get('distance_au')} AU  "
        f"sub-lon={parsed.get('sub_obs_lon_deg')}  sub-lat={parsed.get('sub_obs_lat_deg')}  "
        f"NP.PA={parsed.get('north_pa_deg')}"
    )
    return parsed


def parse_horizons_observer_text(text: str) -> Dict[str, Any]:
    """
    Parse Horizons observer SOE block for QUANTITIES 1,13,14,20,31,32.

    Preferred path: labeled fields in the full response + CSV columns in fixed
    QUANTITIES order (RA, Dec, delta, deldot, light-time, range, Ob-lon, Ob-lat,
    NP.ang, NP.dist). Heuristic float-picking is last resort only.
    """
    out: Dict[str, Any] = {"parser": "horizons_observer_v2"}
    # --- Strong path: labeled tokens anywhere in response ---
    for pat, key in (
        (r"(?:delta|Δ)\s*[=:]\s*([0-9]+\.[0-9]+)", "distance_au"),
        (r"([0-9]+\.[0-9]+)\s*AU\b", "distance_au"),
        (r"(?:Ob-lon|Obs.?lon|sub-?observer\s+lon(?:gitude)?)\s*[=:]?\s*([0-9]+\.[0-9]+)", "sub_obs_lon_deg"),
        (r"(?:Ob-lat|Obs.?lat|sub-?observer\s+lat(?:itude)?)\s*[=:]?\s*([+\-0-9]+\.[0-9]+)", "sub_obs_lat_deg"),
        (r"(?:NP\.?\s*ang|N\.P\.\s*ang|North\s*pole\s*PA)\s*[=:]?\s*([0-9]+\.[0-9]+)", "north_pa_deg"),
        (r"(?:light.?time|LT)\s*[=:]?\s*([0-9]+\.[0-9]+)\s*(?:s|sec)?", "light_time_s"),
    ):
        mm = re.search(pat, text, re.I)
        if mm and key not in out:
            try:
                out[key] = float(mm.group(1))
            except Exception:
                pass

    # Extract SOE ... EOE
    m = re.search(r"\$\$SOE(.*?)\$\$EOE", text, re.S)
    block = m.group(1).strip() if m else text
    lines = [ln.strip() for ln in block.splitlines() if ln.strip() and not ln.strip().startswith("SOE")]

    data_line = None
    for ln in lines:
        if any(k in ln.upper() for k in ("DATE", "R.A.", "RA_", "COL", "****")):
            if re.search(r"\d{4}-[A-Za-z]{3}-\d{2}", ln) or re.search(r"\d{4}-\d{2}-\d{2}", ln):
                data_line = ln
                break
            continue
        if re.search(r"\d{4}-", ln) or re.search(r"\d+\.\d+", ln):
            data_line = ln
            break
    if data_line is None and lines:
        data_line = lines[0]

    parts: List[str] = []
    if data_line:
        if "," in data_line:
            parts = [p.strip() for p in data_line.split(",")]
        else:
            parts = data_line.split()

    floats: List[float] = []
    for p in parts:
        p2 = p.replace("*", "").replace("n.a.", "").strip()
        # skip calendar tokens
        if re.match(r"^\d{4}-", p2):
            continue
        try:
            floats.append(float(p2))
        except Exception:
            continue

    # QUANTITIES='1,13,14,20,31,32' with CSV_FORMAT + ANG_FORMAT=DEG typically yields:
    #   [date skipped] RA Dec  delta  deldot  LT  range  Ob-lon Ob-lat  NP.ang NP.dist
    # After skipping date string, look for: RA, Dec, delta(~4-7), ...
    if len(floats) >= 8:
        # Find delta as first float in (3.5, 7.5)
        di = None
        for i, v in enumerate(floats):
            if 3.5 < v < 7.5:
                di = i
                break
        if di is not None:
            if "distance_au" not in out:
                out["distance_au"] = float(floats[di])
            # After delta: deldot, LT (s or min), optional range, then Ob-lon, Ob-lat, NP.ang
            tail = floats[di + 1 :]
            # light time: seconds 1500-4000 or minutes 25-60
            for v in tail[:4]:
                if 1500 < v < 4000 and "light_time_s" not in out:
                    out["light_time_s"] = float(v)
                elif 25 < v < 60 and "light_time_s" not in out:
                    out["light_time_s"] = float(v) * 60.0
            # sub-lon / sub-lat / NP.ang: prefer last three plausible values
            # Ob-lat for Jupiter is small (-5..+5); Ob-lon and NP.ang are 0..360
            sublat_i = None
            for i, v in enumerate(tail):
                if -5.5 <= v <= 5.5 and abs(v) < 5.0:
                    # skip if this is clearly deldot (small) right after delta
                    if i == 0 and abs(v) < 0.5:
                        continue
                    sublat_i = i
            if sublat_i is not None and "sub_obs_lat_deg" not in out:
                out["sub_obs_lat_deg"] = float(tail[sublat_i])
                # lon usually immediately before lat in Q31
                if sublat_i >= 1 and "sub_obs_lon_deg" not in out:
                    cand = tail[sublat_i - 1]
                    if 0 <= cand <= 360:
                        out["sub_obs_lon_deg"] = float(cand)
                # NP.ang often immediately after lat
                if sublat_i + 1 < len(tail) and "north_pa_deg" not in out:
                    cand = tail[sublat_i + 1]
                    if 0 <= cand < 360:
                        out["north_pa_deg"] = float(cand)

    # Fallback distance / LT only if still missing
    if "distance_au" not in out:
        delta_cands = [v for v in floats if 3.5 < v < 7.5]
        if delta_cands:
            out["distance_au"] = float(delta_cands[0])
    if "light_time_s" not in out:
        lt_cands = [v for v in floats if 1500 < v < 4000]
        lt_min = [v for v in floats if 25 < v < 60]
        if lt_cands:
            out["light_time_s"] = float(lt_cands[0])
        elif lt_min:
            out["light_time_s"] = float(lt_min[0]) * 60.0

    # Last-resort sub-lon/lat only when labeled + ordered parse failed
    if "sub_obs_lat_deg" not in out:
        sublat_cands = [v for v in floats if -5.5 <= v <= 5.5 and abs(v) < 5.0]
        if sublat_cands:
            out["sub_obs_lat_deg"] = float(sorted(sublat_cands, key=lambda x: abs(x))[0])
    if "sub_obs_lon_deg" not in out:
        # Prefer float just before sublat in list; else last mid-range 0-360 after delta
        if "sub_obs_lat_deg" in out and floats:
            try:
                si = floats.index(out["sub_obs_lat_deg"])
                if si >= 1 and 0 <= floats[si - 1] <= 360:
                    out["sub_obs_lon_deg"] = float(floats[si - 1])
            except ValueError:
                pass
        if "sub_obs_lon_deg" not in out:
            after_delta = False
            cands = []
            for v in floats:
                if 3.5 < v < 7.5:
                    after_delta = True
                    continue
                if after_delta and 0 <= v <= 360 and not (-5.5 <= v <= 5.5):
                    cands.append(v)
            if cands:
                out["sub_obs_lon_deg"] = float(cands[0])

    if "sub_obs_lon_deg" in out:
        out["cm_iii_deg"] = float(out["sub_obs_lon_deg"])

    if "north_pa_deg" not in out and len(floats) >= 2:
        # Prefer value after sub-lat
        if "sub_obs_lat_deg" in out:
            try:
                si = floats.index(out["sub_obs_lat_deg"])
                if si + 1 < len(floats) and 0 <= floats[si + 1] < 360:
                    out["north_pa_deg"] = float(floats[si + 1])
            except ValueError:
                pass

    if "distance_au" in out:
        d = float(out["distance_au"])
        out["apparent_diameter_arcsec"] = math.degrees(2 * JUP_REQ_KM / (d * AU_KM)) * 3600.0
        if "light_time_s" not in out:
            out["light_time_s"] = (d * AU_KM * 1000.0) / C_M_S

    out["floats_found"] = floats[:40]
    out["data_line"] = data_line
    return out


# ---------------------------------------------------------------------------
# WinJUPOS / JUPOS table import
# ---------------------------------------------------------------------------

def load_winjupos_table(path: Union[str, Path]) -> List[Dict[str, Any]]:
    """
    Load WinJUPOS-like CSV/JSON of CM or measurements.

    Accepted columns (case-insensitive aliases):
      time/date/datetime/epoch, cm_iii/cml_iii/cml3/cmiii, optional sublat, np_pa
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)
    rows: List[Dict[str, Any]] = []
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and "rows" in data:
            data = data["rows"]
        for r in data:
            rows.append(dict(r))
        return rows

    # CSV
    text = path.read_text(encoding="utf-8", errors="replace")
    # delimiter sniff
    dialect = csv.Sniffer().sniff(text[:2000], delimiters=",;\t")
    reader = csv.DictReader(text.splitlines(), dialect=dialect)
    alias = {
        "time": "time", "date": "time", "datetime": "time", "epoch": "time",
        "jd": "jd", "mjd": "mjd",
        "cm_iii": "cm_iii", "cml_iii": "cm_iii", "cml3": "cm_iii", "cmiii": "cm_iii",
        "cml": "cm_iii", "cm": "cm_iii", "longitude_cm": "cm_iii",
        "sub_lat": "sub_lat", "sublat": "sub_lat", "de": "sub_lat",
        "np_pa": "np_pa", "pa": "np_pa", "p": "np_pa", "north_pa": "np_pa",
        "distance_au": "distance_au", "delta": "distance_au",
    }
    for raw in reader:
        norm = { (k or "").strip().lower().replace(" ", "_"): (v or "").strip() for k, v in raw.items() }
        rec: Dict[str, Any] = {}
        for k, v in norm.items():
            key = alias.get(k)
            if not key or v == "":
                continue
            if key == "time":
                rec["time"] = v
            else:
                try:
                    rec[key] = float(v.replace(",", "."))
                except Exception:
                    rec[key] = v
        if rec:
            rows.append(rec)
    CONSOLE.ok(f"WinJUPOS table loaded: {len(rows)} rows from {path.name}")
    return rows


def interpolate_winjupos_cm(
    rows: Sequence[Dict[str, Any]],
    t: dt.datetime,
) -> Optional[Dict[str, float]]:
    """Linear circular interpolation of CM III (and friends) to epoch t."""
    pts: List[Tuple[float, Dict[str, float]]] = []
    t0 = dt.datetime(2000, 1, 1)
    for r in rows:
        try:
            if "time" in r:
                tt = parse_time(str(r["time"]))
            elif "jd" in r:
                # JD to datetime approx
                jd = float(r["jd"])
                tt = dt.datetime(2000, 1, 1, 12) + dt.timedelta(days=jd - 2451545.0)
            else:
                continue
            if "cm_iii" not in r:
                continue
            sec = (tt - t0).total_seconds()
            pts.append((sec, {
                "cm_iii_deg": float(r["cm_iii"]),
                "sub_lat": float(r.get("sub_lat", 0.0) or 0.0),
                "np_pa": float(r.get("np_pa", 0.0) or 0.0),
                "distance_au": float(r["distance_au"]) if r.get("distance_au") not in (None, "") else float("nan"),
            }))
        except Exception:
            continue
    if not pts:
        return None
    pts.sort(key=lambda x: x[0])
    target = (t - t0).total_seconds()
    if target <= pts[0][0]:
        return pts[0][1]
    if target >= pts[-1][0]:
        return pts[-1][1]
    for i in range(len(pts) - 1):
        t1, a = pts[i]
        t2, b = pts[i + 1]
        if t1 <= target <= t2:
            u = (target - t1) / max(t2 - t1, 1e-9)
            # circular CM
            dcm = wrap_diff(b["cm_iii_deg"], a["cm_iii_deg"])
            cm = wrap_deg(a["cm_iii_deg"] + u * dcm)
            out = {
                "cm_iii_deg": cm,
                "sub_obs_lat_deg": a["sub_lat"] + u * (b["sub_lat"] - a["sub_lat"]),
                "north_pa_deg": a["np_pa"] + u * (wrap_diff(b["np_pa"], a["np_pa"])),
            }
            if not math.isnan(a["distance_au"]) and not math.isnan(b["distance_au"]):
                out["distance_au"] = a["distance_au"] + u * (b["distance_au"] - a["distance_au"])
            return out
    return pts[-1][1]


def save_example_winjupos_template(path: Optional[Path] = None) -> Path:
    path = path or (EPH_DIR / "winjupos_cm_template.csv")
    path.write_text(
        "datetime,cm_iii,sub_lat,np_pa,distance_au\n"
        "2026-01-01 00:00:00,100.0,-2.1,15.0,5.10\n"
        "2026-01-02 00:00:00,187.3,-2.1,15.2,5.11\n",
        encoding="utf-8",
    )
    return path


# ---------------------------------------------------------------------------
# SPICE (auto-download kernels online — users never hunt NAIF files)
# ---------------------------------------------------------------------------

def try_spice_geometry(t: dt.datetime, kernels_dir: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """
    SPICE geometry via spice_auto:
      - ensures spiceypy
      - downloads de440s + LSK + PCK if missing
      - returns distance, light-time, CM III / sub-lat when body frame works
    """
    try:
        from spice_auto import compute_spice_geometry, ensure_kernels, kernel_dir
    except Exception as e:
        CONSOLE.debug(f"spice_auto import: {e}")
        return _try_spice_geometry_legacy(t, kernels_dir)

    kdir = kernel_dir(kernels_dir) if kernels_dir else None
    try:
        st = ensure_kernels(kdir)
        if not st.ok:
            CONSOLE.warn(f"SPICE kernels not ready: {st.last_error}")
            # still try compute in case partial set works
        g = compute_spice_geometry(t, kdir=kdir, auto_download=False)
        if g is None:
            return _try_spice_geometry_legacy(t, kernels_dir)
        out: Dict[str, Any] = {
            "distance_au": g.distance_au,
            "light_time_s": g.light_time_s,
            "apparent_diameter_arcsec": g.apparent_diameter_arcsec,
            "source": g.source,
            "kernels": list(g.kernels),
            "notes": list(g.notes),
            "north_pa_deg": g.north_pa_deg,
        }
        if g.source != "spice_auto_distance_only":
            out["cm_iii_deg"] = g.cm_iii_deg
            out["sub_obs_lon_deg"] = g.sub_obs_lon_deg
            out["sub_obs_lat_deg"] = g.sub_obs_lat_deg
        return out
    except Exception as e:
        CONSOLE.debug(f"SPICE auto failed: {e}")
        return _try_spice_geometry_legacy(t, kernels_dir)


def _try_spice_geometry_legacy(t: dt.datetime, kernels_dir: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Fallback if spice_auto unavailable: local kernels only."""
    try:
        import spiceypy as spice  # type: ignore
    except Exception:
        return None
    kdir = Path(kernels_dir or os.environ.get("GRS_SPICE_KERNELS", str(EPH_DIR / "spice")))
    if not kdir.exists():
        return None
    kernels = list(kdir.glob("*.bsp")) + list(kdir.glob("*.tls")) + list(kdir.glob("*.tpc")) + list(kdir.glob("*.tf"))
    if not kernels:
        return None
    try:
        for k in kernels:
            spice.furnsh(str(k))
        et = spice.str2et(t.strftime("%Y-%m-%dT%H:%M:%S"))
        state, lt = spice.spkezr("JUPITER BARYCENTER", et, "J2000", "LT+S", "EARTH")
        dist_km = math.sqrt(state[0] ** 2 + state[1] ** 2 + state[2] ** 2)
        dist_au = dist_km / AU_KM
        try:
            pos, _ = spice.spkpos("EARTH", et - lt, "IAU_JUPITER", "LT+S", "JUPITER")
            lon = math.degrees(math.atan2(pos[1], pos[0]))
            lat = math.degrees(math.atan2(pos[2], math.hypot(pos[0], pos[1])))
            cm = wrap_deg(-lon)
            out = {
                "distance_au": dist_au,
                "light_time_s": float(lt),
                "cm_iii_deg": wrap_deg(cm),
                "sub_obs_lon_deg": wrap_deg(cm),
                "sub_obs_lat_deg": float(lat),
                "apparent_diameter_arcsec": math.degrees(2 * JUP_REQ_KM / (dist_au * AU_KM)) * 3600.0,
                "source": "spice_legacy",
            }
            CONSOLE.ok(f"SPICE legacy geometry: Δ={dist_au:.5f} AU  sublat={lat:.3f}°")
            return out
        except Exception as e:
            CONSOLE.debug(f"SPICE body frame: {e}")
            return {
                "distance_au": dist_au,
                "light_time_s": float(lt),
                "apparent_diameter_arcsec": math.degrees(2 * JUP_REQ_KM / (dist_au * AU_KM)) * 3600.0,
                "source": "spice_distance_only",
            }
    except Exception as e:
        CONSOLE.debug(f"SPICE failed: {e}")
        return None
    finally:
        try:
            spice.kclear()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Master resolver
# ---------------------------------------------------------------------------

def resolve_pro_ephemeris(
    user_time_iso: str,
    time_error_seconds: float = 0.0,
    cm_override: Optional[float] = None,
    distance_override: Optional[float] = None,
    sub_lat_override: Optional[float] = None,
    north_pa_override: Optional[float] = None,
    winjupos_path: Optional[Union[str, Path]] = None,
    site_lat: float = 0.0,
    site_lon: float = 0.0,
    use_horizons: bool = True,
    use_spice: bool = True,
    force_horizons: bool = False,
) -> ProEphemeris:
    """
    Build the best available ProEphemeris for absolute System III metrology.
    """
    t = parse_time(user_time_iso) + dt.timedelta(seconds=float(time_error_seconds))
    notes: List[str] = []
    ana = analytical_geometry(t)

    eph = ProEphemeris(
        t_utc_iso=t.isoformat(),
        distance_au=ana["distance_au"],
        cm_iii_deg=ana["cm_iii_deg"],
        sub_obs_lat_deg=0.0,  # don't apply crude seasonal lat unless real eph says so
        sub_obs_lon_deg=ana["sub_obs_lon_deg"],
        north_pa_deg=0.0,
        apparent_diameter_arcsec=ana["apparent_diameter_arcsec"],
        light_time_s=ana["light_time_s"],
        source="analytical",
        cm_source="analytical",
        orientation_source="none",
        distance_source="analytical",
        sigma_cm_deg=0.5,
        apply_orientation=False,
        notes=notes,
        raw={"analytical": ana},
    )
    notes.append("Base: analytical Sys-III clock (differentials robust; absolute zero may be offset).")

    # --- SPICE (auto-download; preferred absolute geometry) ---
    if use_spice:
        sp = try_spice_geometry(t)
        if sp:
            eph.raw["spice"] = sp
            if "distance_au" in sp:
                eph.distance_au = float(sp["distance_au"])
                eph.distance_source = str(sp.get("source", "spice"))
                eph.sigma_distance_frac = 1e-7
            if "light_time_s" in sp:
                eph.light_time_s = float(sp["light_time_s"])
            if "apparent_diameter_arcsec" in sp:
                eph.apparent_diameter_arcsec = float(sp["apparent_diameter_arcsec"])
            if "cm_iii_deg" in sp and cm_override is None:
                try:
                    cm_v = float(sp["cm_iii_deg"])
                except (TypeError, ValueError):
                    cm_v = float("nan")
                # Distance-only SPICE must not inject CM=0 / NaN as absolute Sys III
                if math.isfinite(cm_v) and str(sp.get("source", "")).find("distance_only") < 0:
                    eph.cm_iii_deg = wrap_deg(cm_v)
                    eph.sub_obs_lon_deg = eph.cm_iii_deg
                    eph.cm_source = "spice_auto"
                    eph.sigma_cm_deg = 0.01
                else:
                    notes.append(
                        "SPICE distance-only or non-finite CM — keeping prior CM source "
                        f"(was {eph.cm_source}); not using CM=0."
                    )
            if "sub_obs_lat_deg" in sp:
                try:
                    slat = float(sp["sub_obs_lat_deg"])
                except (TypeError, ValueError):
                    slat = float("nan")
                if math.isfinite(slat):
                    eph.sub_obs_lat_deg = slat
                    eph.orientation_source = "spice_auto"
                    eph.apply_orientation = True
                    eph.sigma_sublat_deg = 0.02
            if sp.get("north_pa_deg") is not None and float(sp.get("north_pa_deg") or 0) != 0.0:
                eph.north_pa_deg = float(sp["north_pa_deg"])
                eph.orientation_source = "spice_auto"
                eph.apply_orientation = True
                eph.sigma_pa_deg = 0.15
            eph.source = "spice_auto+" + eph.source
            nker = len(sp.get("kernels") or [])
            notes.append(f"SPICE auto geometry ({nker} kernels; online download if needed).")

    # --- Horizons full ---
    if use_horizons:
        h = fetch_horizons_full(t, site_lat=site_lat, site_lon=site_lon, force=force_horizons)
        if h:
            eph.raw["horizons"] = h
            if h.get("distance_au") is not None:
                eph.distance_au = float(h["distance_au"])
                eph.distance_source = "horizons"
                eph.sigma_distance_frac = 0.001
            if h.get("light_time_s") is not None:
                eph.light_time_s = float(h["light_time_s"])
            if h.get("apparent_diameter_arcsec") is not None:
                eph.apparent_diameter_arcsec = float(h["apparent_diameter_arcsec"])
            elif eph.distance_au:
                eph.apparent_diameter_arcsec = (
                    math.degrees(2 * JUP_REQ_KM / (eph.distance_au * AU_KM)) * 3600.0
                )
            # Orientation from Horizons is research-grade when present
            got_ori = False
            if h.get("sub_obs_lat_deg") is not None:
                eph.sub_obs_lat_deg = float(h["sub_obs_lat_deg"])
                got_ori = True
            if h.get("north_pa_deg") is not None:
                eph.north_pa_deg = float(h["north_pa_deg"])
                got_ori = True
            if h.get("sub_obs_lon_deg") is not None and cm_override is None and eph.cm_source in (
                "analytical", "spice_distance_only"
            ):
                # Horizons sub-lon as CM when no better CM
                eph.cm_iii_deg = wrap_deg(float(h["sub_obs_lon_deg"]))
                eph.sub_obs_lon_deg = eph.cm_iii_deg
                eph.cm_source = "horizons_sublon"
                eph.sigma_cm_deg = 0.05
            elif h.get("cm_iii_deg") is not None and cm_override is None and eph.cm_source == "analytical":
                eph.cm_iii_deg = wrap_deg(float(h["cm_iii_deg"]))
                eph.cm_source = "horizons"
                eph.sigma_cm_deg = 0.05
            if got_ori:
                eph.orientation_source = "horizons"
                eph.apply_orientation = True
                eph.sigma_pa_deg = 0.2
                eph.sigma_sublat_deg = 0.1
            eph.source = "horizons+" + eph.source if "horizons" not in eph.source else eph.source
            notes.append("Horizons full observer geometry applied where parsed.")

    # --- Manual CM override or table (more reliable than analytical CM) ---
    wpath = winjupos_path
    if wpath is None:
        # auto-discover
        for cand in (
            EPH_DIR / "winjupos_cm.csv",
            EPH_DIR / "cml_iii.csv",
            EPH_DIR / "jupos_cm.csv",
            Path(os.environ.get("GRS_WINJUPOS_CM", "")) if os.environ.get("GRS_WINJUPOS_CM") else None,
        ):
            if cand and Path(cand).exists():
                wpath = cand
                break
    if wpath:
        try:
            rows = load_winjupos_table(wpath)
            w = interpolate_winjupos_cm(rows, t)
            if w:
                eph.raw["winjupos"] = w
                eph.cm_iii_deg = wrap_deg(float(w["cm_iii_deg"]))
                eph.sub_obs_lon_deg = eph.cm_iii_deg
                eph.cm_source = "winjupos"
                eph.sigma_cm_deg = 0.03
                if "sub_obs_lat_deg" in w:
                    eph.sub_obs_lat_deg = float(w["sub_obs_lat_deg"])
                    eph.orientation_source = "winjupos+" + eph.orientation_source
                    eph.apply_orientation = True
                if "north_pa_deg" in w:
                    eph.north_pa_deg = float(w["north_pa_deg"])
                    eph.apply_orientation = True
                if "distance_au" in w:
                    eph.distance_au = float(w["distance_au"])
                    eph.distance_source = "winjupos"
                eph.source = "winjupos+" + eph.source
                notes.append(f"WinJUPOS/JUPOS CM interpolated from {Path(wpath).name}.")
        except Exception as e:
            notes.append(f"WinJUPOS load failed: {e}")

    # --- Explicit overrides (highest priority) ---
    if distance_override is not None:
        eph.distance_au = float(distance_override)
        eph.distance_source = "override"
        eph.apparent_diameter_arcsec = (
            math.degrees(2 * JUP_REQ_KM / (eph.distance_au * AU_KM)) * 3600.0
        )
        eph.light_time_s = (eph.distance_au * AU_KM * 1000.0) / C_M_S
        eph.sigma_distance_frac = 0.0005
        notes.append("Distance override applied.")
    if cm_override is not None:
        eph.cm_iii_deg = wrap_deg(float(cm_override))
        eph.sub_obs_lon_deg = eph.cm_iii_deg
        eph.cm_source = "override"
        eph.sigma_cm_deg = 0.02
        notes.append("CM III override applied (no light-time re-shift).")
        # Image-tied CM (synth truth / WinJUPOS paste for this frame) must not
        # inherit a foreign Horizons sub-lat/PA that the image was not rendered with.
        # Keep orientation only if user also supplied it or WinJUPOS/SPICE did.
        ori_trusted = eph.orientation_source not in ("none",) and any(
            k in eph.orientation_source for k in ("winjupos", "spice", "override")
        )
        if not ori_trusted and sub_lat_override is None and north_pa_override is None:
            eph.apply_orientation = False
            eph.sub_obs_lat_deg = 0.0
            eph.north_pa_deg = 0.0
            eph.orientation_source = "disabled_for_cm_override"
            notes.append(
                "Orientation disabled with CM override (avoid Horizons sub-lat mismatch on frame)."
            )
    if sub_lat_override is not None:
        eph.sub_obs_lat_deg = float(sub_lat_override)
        eph.apply_orientation = True
        eph.orientation_source = "override+" + eph.orientation_source
        eph.sigma_sublat_deg = 0.05
        notes.append("Sub-observer latitude override.")
    if north_pa_override is not None:
        eph.north_pa_deg = float(north_pa_override)
        eph.apply_orientation = True
        eph.orientation_source = "override+" + eph.orientation_source
        eph.sigma_pa_deg = 0.1
        notes.append("North PA override.")

    # Cross-check SPICE vs Horizons CM when both present (publication gate helper)
    try:
        sp_raw = (eph.raw or {}).get("spice") or {}
        hz_raw = (eph.raw or {}).get("horizons") or {}
        sp_cm = sp_raw.get("cm_iii_deg")
        hz_cm = hz_raw.get("cm_iii_deg") or hz_raw.get("sub_obs_lon_deg")
        if sp_cm is not None and hz_cm is not None:
            dcm = abs(wrap_diff(float(sp_cm), float(hz_cm)))
            eph.raw["spice_horizons_dcm_deg"] = dcm
            if dcm > 0.25 and eph.cm_source in ("spice_auto", "horizons", "horizons_sublon"):
                notes.append(
                    f"SPICE↔Horizons |ΔCM|={dcm:.3f}° > 0.25° — prefer WinJUPOS/override for absolute lon."
                )
                eph.sigma_cm_deg = max(float(eph.sigma_cm_deg), dcm)
    except Exception:
        pass

    if eph.cm_source == "analytical":
        notes.append(
            "WARNING: CM source is analytical (not IAU Sys III zero). "
            "Absolute System III lon is NOT publication-safe until SPICE/Horizons/WinJUPOS/override."
        )
        eph.sigma_cm_deg = max(float(eph.sigma_cm_deg), 15.0)

    notes.append(
        f"PROVENANCE: cm={eph.cm_source} σ={eph.sigma_cm_deg}° | "
        f"dist={eph.distance_source} | ori={eph.orientation_source} "
        f"apply_ori={eph.apply_orientation}"
    )
    eph.notes = notes
    CONSOLE.ok(
        f"Pro ephemeris: CM III={eph.cm_iii_deg:.4f}° ({eph.cm_source})  "
        f"Δ={eph.distance_au:.5f} AU  sublat={eph.sub_obs_lat_deg:.3f}°  "
        f"NP.PA={eph.north_pa_deg:.2f}°  apply_ori={eph.apply_orientation}"
    )
    return eph


def write_ephemeris_report(path: Path, eph: ProEphemeris) -> None:
    path = Path(path)
    path.write_text(json.dumps(eph.to_dict(), indent=2, default=str), encoding="utf-8")
    path.with_suffix(".txt").write_text(
        "\n".join([
            "PROFESSIONAL JUPITER EPHEMERIS",
            "=" * 40,
            f"Epoch: {eph.t_utc_iso}",
            f"CM III: {eph.cm_iii_deg:.6f}°  [{eph.cm_source}]  σ={eph.sigma_cm_deg}°",
            f"Distance: {eph.distance_au:.8f} AU  [{eph.distance_source}]",
            f"App. diam: {eph.apparent_diameter_arcsec:.4f}″",
            f"Sub-obs lat: {eph.sub_obs_lat_deg:.4f}°",
            f"Sub-obs lon: {eph.sub_obs_lon_deg:.4f}°",
            f"North PA: {eph.north_pa_deg:.4f}°",
            f"Light time: {eph.light_time_s:.3f} s",
            f"Apply orientation in projector: {eph.apply_orientation}",
            f"Source chain: {eph.source}",
            "",
            "NOTES:",
            *[f"- {n}" for n in eph.notes],
        ]),
        encoding="utf-8",
    )

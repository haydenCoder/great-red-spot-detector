#!/usr/bin/env python3
"""fits_meta.py — FITS header & metadata extraction for the observatory.

WHY THIS EXISTS
===============
`fits_time.py` already fails-closed on observation time. But a FITS header
carries the rest of the reduction metadata too — exposure time, telescope
aperture, filter passband, and the target coordinates (RA/Dec) — and until now
every consumer re-derived (or, worse, ignored) those keywords itself. This
module centralises that extraction so the stacker, the CLI and the desktop
report all agree on the same, honestly-labelled numbers.

DESIGN
======
  * Accepts a path OR an already-read header mapping (the `(data, header)`
    tuple `grs_complete_system.read_fits` returns) so it works with the
    astropy path and the pure-Python fallback alike.
  * Every field is Optional; nothing is fabricated. A missing keyword is
    reported as None plus a human-readable `notes` line, never a guess.
  * Sexagesimal RA/Dec ("12 34 56.7" / "12:34:56.7") and decimal degrees are
    both parsed. Aperture keywords accept metres or millimetres and are
    normalised to metres.
  * Observation time is delegated to `fits_time.extract_fits_mid_time` so the
    mid-exposure policy stays in exactly one place.
"""
from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Keyword aliases per field, most-authoritative first.
_APERTURE_KEYS = (
    "APERTURE", "APERTUR", "TELAPERT", "TELDIAM", "APERDIA",
    "APERTURE_M", "APERTURE_MM", "PRIMARYAPERTURE",
)
_FILTER_KEYS = (
    "FILTER", "FILTER1", "FILTER2", "INSFLNAM", "FILTBAND", "BAND",
    "WAVELENG", "WAVEBAND", "PASSBAND", "FILTERNAME",
)
_TELESCOPE_KEYS = ("TELESCOP", "TELNAME", "OBSERVAT")
_INSTRUMENT_KEYS = ("INSTRUME", "CAMERA", "DETECTOR", "IMAGETYP")
_TARGET_KEYS = ("OBJECT", "TARGNAME", "OBJNAME", "TITLE")
_RA_KEYS = ("OBJCTRA", "RA", "TELRA", "CRVAL1")
_DEC_KEYS = ("OBJCTDEC", "DEC", "TELDEC", "CRVAL2")
# RA keywords that are conventionally sexagesimal HOURS (HH:MM:SS.s) rather
# than degrees. `RA` / `CRVAL1` are decimal degrees per the FITS WCS rules.
_RA_HOURS_KEYS = ("OBJCTRA", "TELRA")


def _hdr_get_key(hdr: Any, *keys: str) -> Optional[Tuple[str, str]]:
    """Case-insensitive keyword lookup returning (matched_key, value)."""
    if hdr is None:
        return None
    for k in keys:
        try:
            if hasattr(hdr, "get"):
                v = hdr.get(k) or hdr.get(k.upper()) or hdr.get(k.lower())
            else:
                v = hdr[k] if k in hdr else None
            if v is not None and str(v).strip() not in ("", "None", "nan", "NaN"):
                return k, str(v).strip().strip("'").strip('"')
        except Exception:
            continue
        try:
            for kk in hdr.keys() if hasattr(hdr, "keys") else []:
                if str(kk).upper() == k.upper():
                    vv = hdr[kk]
                    if vv is not None and str(vv).strip():
                        return k, str(vv).strip().strip("'").strip('"')
        except Exception:
            pass
    return None


def _hdr_get(hdr: Any, *keys: str) -> Optional[str]:
    hit = _hdr_get_key(hdr, *keys)
    return hit[1] if hit else None


def _as_float(v: Any) -> Optional[float]:
    try:
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _parse_sexagesimal(s: str) -> Optional[float]:
    """'HH:MM:SS.ss' / 'HH MM SS.ss' / '±DD:MM:SS.ss' -> decimal degrees.

    The sign on the first component is honoured (so "-12 34 56" == -12.582...).
    A leading '-' anywhere in the first token is treated as a negative sign.
    """
    s = (s or "").strip()
    if not s:
        return None
    neg = s.lstrip().startswith("-")
    body = s.lstrip().lstrip("+-").strip()
    parts = [p for p in re.split(r"[:hmsd\s]+", body) if p]
    if not parts:
        return None
    try:
        vals = [float(p) for p in parts]
    except ValueError:
        return None
    deg = 0.0
    for i, v in enumerate(vals[:3]):
        deg += v / (60.0 ** i)
    if neg:
        deg = -deg
    return deg


def _parse_angle(value: Any, *, hours: bool = False) -> Optional[float]:
    """An angle keyword -> decimal degrees.

    Handles decimal floats and sexagesimal strings. When ``hours=True`` (RA
    keywords that are conventionally HH:MM:SS.s), a sexagesimal string is read
    as hours and multiplied by 15 to get degrees; a plain decimal is always
    assumed to be degrees (the FITS WCS convention).
    """
    if value is None:
        return None
    s = str(value).strip().strip("'").strip('"')
    if not s:
        return None
    # plain decimal (degrees)
    try:
        return float(s)
    except ValueError:
        pass
    d = _parse_sexagesimal(s)
    if d is None:
        return None
    if hours and _looks_sexagesimal(s):
        d *= 15.0
    return d


def _looks_sexagesimal(s: str) -> bool:
    """True if the string is separated into components (HH:MM, HH MM, 5h30m)."""
    return bool(re.search(r"[:hmsd\s]", s)) and len(re.split(r"[:hmsd\s]+", s.strip())) >= 2


def _parse_aperture_m(value: Any) -> Tuple[Optional[float], str]:
    """Normalise an aperture keyword to metres, with a provenance note."""
    if value is None:
        return None, ""
    s = str(value).strip().lower().replace(",", ".")
    m = re.search(r"([-+]?\d*\.?\d+(?:[eE][-+]?\d+)?)\s*(mm|cm|m|in|inch|ft)?", s)
    if not m:
        return None, f"aperture unparsed: {value!r}"
    num = float(m.group(1))
    unit = (m.group(2) or "").strip()
    if unit in ("mm",):
        num /= 1000.0
    elif unit in ("cm",):
        num /= 100.0
    elif unit in ("in", "inch"):
        num *= 0.0254
    elif unit in ("ft",):
        num *= 0.3048
    elif unit in ("", "m"):
        # FITS aperture is conventionally metres; a bare number > 40 is mm
        if num > 40.0:
            num /= 1000.0
            unit = "mm"
    return num, f"aperture {unit or 'm'}"


@dataclass
class FitsMeta:
    path: Optional[str] = None
    # time (delegated to fits_time, mid-exposure policy)
    mid_time_utc: Optional[str] = None
    time_source: str = ""
    # exposure / optics
    exposure_time_s: Optional[float] = None
    telescope: Optional[str] = None
    instrument: Optional[str] = None
    aperture_m: Optional[float] = None
    aperture_source: str = ""
    filter: Optional[str] = None
    # target
    target_name: Optional[str] = None
    target_ra_deg: Optional[float] = None
    target_dec_deg: Optional[float] = None
    target_ra_str: Optional[str] = None
    target_dec_str: Optional[str] = None
    # array geometry
    naxis: Optional[int] = None
    bitpix: Optional[int] = None
    notes: List[str] = field(default_factory=list)
    extra: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def extract_fits_meta(
    path: Optional[str | Path] = None,
    header: Any = None,
) -> FitsMeta:
    """Read observation metadata from a FITS path or an already-read header.

    Pass either `path` (the file is read here — requires astropy or the repo's
    pure-Python reader) or `header` (a dict-like from a previous read, so the
    data array isn't decoded twice). Returns a FitsMeta with every field
    Optional and a `notes` list documenting what was (and wasn't) found.
    """
    meta = FitsMeta()
    notes: List[str] = []
    hdr: Any = header
    if hdr is None and path is not None:
        p = Path(path)
        meta.path = str(p)
        if p.suffix.lower() not in (".fit", ".fits", ".fts"):
            notes.append(f"not a FITS file: {p.name}")
            meta.notes = notes
            return meta
        try:
            import grs_complete_system as grs
            _data, hdr = grs.read_fits(p)
        except Exception as e:
            notes.append(f"FITS read failed: {e}")
            meta.notes = notes
            return meta
    elif path is not None:
        meta.path = str(Path(path))

    if hdr is None:
        notes.append("no header available")
        meta.notes = notes
        return meta

    # --- observation time (single source of truth: fits_time) -------------
    try:
        from fits_time import extract_fits_mid_time
        mid, note = extract_fits_mid_time(path=None, hdr=hdr)
        if mid is not None:
            meta.mid_time_utc = mid.isoformat()
        meta.time_source = note
        notes.append(f"time: {note}")
    except Exception as e:
        notes.append(f"time extraction failed: {e}")

    # --- exposure time -----------------------------------------------------
    for k in ("EXPTIME", "EXPOSURE", "INTTIME", "TELAPSE", "EXPOS"):
        v = _hdr_get(hdr, k)
        if v is not None:
            f = _as_float(v)
            if f is not None and 0 <= f < 1e6:
                meta.exposure_time_s = f
                notes.append(f"exposure_time_s from {k}={f}")
                break
    if meta.exposure_time_s is None:
        notes.append("exposure_time_s not found (EXPTIME/EXPOSURE/INTTIME/TELAPSE)")

    # --- optics -----------------------------------------------------------
    meta.telescope = _hdr_get(hdr, *_TELESCOPE_KEYS)
    meta.instrument = _hdr_get(hdr, *_INSTRUMENT_KEYS)
    if meta.telescope:
        notes.append(f"telescope: {meta.telescope}")
    if meta.instrument:
        notes.append(f"instrument: {meta.instrument}")

    for k in _APERTURE_KEYS:
        v = _hdr_get(hdr, k)
        if v is not None:
            ap, prov = _parse_aperture_m(v)
            if ap is not None:
                meta.aperture_m = ap
                meta.aperture_source = f"{k} ({prov})"
                notes.append(f"aperture_m={ap:.3f} from {k} ({prov})")
                break
    if meta.aperture_m is None:
        notes.append("aperture not found (APERTURE/APERTUR/TELAPERT/... )")

    # --- filter -----------------------------------------------------------
    meta.filter = _hdr_get(hdr, *_FILTER_KEYS)
    if meta.filter:
        notes.append(f"filter: {meta.filter}")
    else:
        notes.append("filter not found (FILTER/INSFLNAM/BAND/...)")

    # --- target -----------------------------------------------------------
    meta.target_name = _hdr_get(hdr, *_TARGET_KEYS)
    if meta.target_name:
        notes.append(f"target: {meta.target_name}")

    ra_hit = _hdr_get_key(hdr, *_RA_KEYS)
    dec_hit = _hdr_get_key(hdr, *_DEC_KEYS)
    ra_raw = ra_hit[1] if ra_hit else None
    dec_raw = dec_hit[1] if dec_hit else None
    meta.target_ra_str = ra_raw
    meta.target_dec_str = dec_raw
    ra_hours = bool(ra_hit and ra_hit[0] in _RA_HOURS_KEYS)
    meta.target_ra_deg = _parse_angle(ra_raw, hours=ra_hours)
    meta.target_dec_deg = _parse_angle(dec_raw, hours=False)
    if meta.target_ra_deg is not None:
        notes.append(
            f"RA={meta.target_ra_deg:.6f} deg ({ra_raw}"
            + (" as HH:MM:SS hours)" if ra_hours else ")")
        )
    else:
        notes.append("RA not found/parseable (OBJCTRA/RA/TELRA)")
    if meta.target_dec_deg is not None:
        notes.append(f"Dec={meta.target_dec_deg:.6f} deg ({dec_raw})")
    else:
        notes.append("Dec not found/parseable (OBJCTDEC/DEC/TELDEC)")

    # --- array geometry ---------------------------------------------------
    na = _hdr_get(hdr, "NAXIS")
    meta.naxis = int(float(na)) if na is not None and _as_float(na) is not None else None
    bp = _hdr_get(hdr, "BITPIX")
    meta.bitpix = int(float(bp)) if bp is not None and _as_float(bp) is not None else None

    # keep the raw interesting keywords for debugging
    for k in (_APERTURE_KEYS + _FILTER_KEYS + _TELESCOPE_KEYS + _INSTRUMENT_KEYS
              + _TARGET_KEYS + _RA_KEYS + _DEC_KEYS + ("EXPTIME", "NAXIS", "BITPIX")):
        v = _hdr_get(hdr, k)
        if v is not None:
            meta.extra[k] = v

    meta.notes = notes
    return meta


def meta_report_text(meta: FitsMeta) -> str:
    """Human-readable FITS metadata card."""
    lines = [
        "FITS METADATA",
        "=============",
        f"file         {meta.path or '—'}",
        f"mid-time UTC {meta.mid_time_utc or '—'}  [{meta.time_source or 'no time'}]",
        f"exposure     {meta.exposure_time_s if meta.exposure_time_s is not None else '—'} s",
        f"telescope    {meta.telescope or '—'}",
        f"instrument   {meta.instrument or '—'}",
        f"aperture     {meta.aperture_m if meta.aperture_m is not None else '—'} m  [{meta.aperture_source or '—'}]",
        f"filter       {meta.filter or '—'}",
        f"target       {meta.target_name or '—'}",
        f"RA           {meta.target_ra_str or '—'}  ({meta.target_ra_deg if meta.target_ra_deg is not None else '—'} deg)",
        f"Dec          {meta.target_dec_str or '—'}  ({meta.target_dec_deg if meta.target_dec_deg is not None else '—'} deg)",
        f"array        NAXIS={meta.naxis if meta.naxis is not None else '—'}  BITPIX={meta.bitpix if meta.bitpix is not None else '—'}",
        "",
        "notes:",
    ]
    lines += [f"  - {n}" for n in meta.notes]
    lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("usage: python fits_meta.py <file.fits>")
        raise SystemExit(1)
    print(meta_report_text(extract_fits_meta(path=sys.argv[1])))

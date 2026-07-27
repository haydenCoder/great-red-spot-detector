#!/usr/bin/env python3
"""
FITS mid-exposure UTC extraction — never silently use datetime.now().

Policy:
  • Prefer DATE-OBS + TIME-OBS / UT / DATE-AVG / MJD-OBS / EXPTIME mid
  • If nothing found → return None and callers MUST fail or demand user time
  • Never default to wall-clock "now" for System III geometry
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple


def _parse_isoish(s: str) -> Optional[datetime]:
    s = (s or "").strip().strip("'").strip('"')
    if not s:
        return None
    s = s.replace("T", " ").replace("Z", "").strip()
    # truncate fractional if too long
    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d %H:%M",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(s[:26], fmt)
        except Exception:
            continue
    # 2026-01-10T15:39:26 already handled
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
    except Exception:
        return None


def _hdr_get(hdr: Any, *keys: str) -> Optional[str]:
    if hdr is None:
        return None
    # dict-like or astropy header
    for k in keys:
        try:
            if hasattr(hdr, "get"):
                v = hdr.get(k) or hdr.get(k.upper()) or hdr.get(k.lower())
            else:
                v = hdr[k] if k in hdr else None
            if v is not None and str(v).strip() not in ("", "None"):
                return str(v).strip().strip("'").strip('"')
        except Exception:
            continue
        # case-insensitive scan
        try:
            for kk in hdr.keys() if hasattr(hdr, "keys") else []:
                if str(kk).upper() == k.upper():
                    vv = hdr[kk]
                    if vv is not None and str(vv).strip():
                        return str(vv).strip().strip("'").strip('"')
        except Exception:
            pass
    return None


def extract_fits_mid_time(
    path: Optional[str | Path] = None,
    hdr: Any = None,
) -> Tuple[Optional[datetime], str]:
    """
    Returns (mid_utc_naive, source_note).
    mid is timezone-naive UTC wall time for geometry callers.
    """
    if hdr is None and path:
        p = Path(path)
        if p.suffix.lower() not in (".fit", ".fits", ".fts"):
            return None, "not_a_fits"
        try:
            import grs_complete_system as grs
            _, hdr = grs.read_fits(p)
        except Exception as e:
            return None, f"fits_read_fail:{e}"

    if hdr is None:
        return None, "no_header"

    # DATE-AVG preferred when present
    for key in ("DATE-AVG", "DATE_AVG", "DATE-OBS", "DATE_OBS", "DATE"):
        raw = _hdr_get(hdr, key)
        if not raw:
            continue
        dt = _parse_isoish(raw)
        if dt is None:
            # date only + separate time
            time_raw = _hdr_get(hdr, "TIME-OBS", "TIME_OBS", "UT", "UTC", "TIME")
            if time_raw and re.match(r"^\d{4}", raw):
                dt = _parse_isoish(f"{raw[:10]} {time_raw}")
        if dt is None:
            continue
        # mid-exposure if EXPTIME / TELAPSE
        exp_s = 0.0
        for ek in ("EXPTIME", "EXPOSURE", "TELAPSE", "INTTIME"):
            ev = _hdr_get(hdr, ek)
            if ev:
                try:
                    exp_s = float(ev)
                    break
                except Exception:
                    pass
        # If DATE-OBS is start, add half exposure; DATE-AVG already mid
        if exp_s > 0 and exp_s < 86400 and key.upper() not in ("DATE-AVG", "DATE_AVG"):
            dt = dt + timedelta(seconds=exp_s / 2.0)
        note = f"{key}" + (f"+EXPTIME/2({exp_s:.3f}s)" if exp_s > 0 else "")
        return dt, note

    # MJD-OBS
    mjd = _hdr_get(hdr, "MJD-OBS", "MJD_OBS", "MJD")
    if mjd:
        try:
            m = float(mjd)
            # MJD → datetime (approx)
            # JD = MJD + 2400000.5; unix epoch JD 2440587.5
            jd = m + 2400000.5
            unix = (jd - 2440587.5) * 86400.0
            dt = datetime.fromtimestamp(unix, tz=timezone.utc).replace(tzinfo=None)
            return dt, "MJD-OBS"
        except Exception:
            pass

    return None, "no_fits_time"


def require_observation_time(
    *,
    user_time: Optional[str] = None,
    fits_path: Optional[str | Path] = None,
    hdr: Any = None,
    allow_user_only: bool = True,
) -> Tuple[datetime, str]:
    """
    Resolve observation UTC or raise ValueError.
    Never returns datetime.now().
    """
    if user_time and str(user_time).strip():
        dt = _parse_isoish(str(user_time).strip())
        if dt is not None:
            return dt, "user_time"
        # try ephemeris_pro
        try:
            from ephemeris_pro import parse_time
            return parse_time(str(user_time)), "user_time_ephemeris_pro"
        except Exception as e:
            if not allow_user_only:
                raise ValueError(f"Could not parse user time: {user_time!r} ({e})") from e
            raise ValueError(
                f"Could not parse observation time {user_time!r}. "
                "Use UTC like '2026-01-10 15:39:26'."
            ) from e

    dt, note = extract_fits_mid_time(fits_path, hdr=hdr)
    if dt is not None:
        return dt, note

    # AutoStakkert and many planetary stacks have empty DATE-OBS — time is in the filename
    if fits_path is not None:
        try:
            from grs_image_prep import parse_time_from_filename

            dt_fn, note_fn = parse_time_from_filename(fits_path)
            if dt_fn is not None:
                return dt_fn, note_fn
        except Exception:
            pass

    raise ValueError(
        "No observation UTC available. Set mid-exposure time (user field), put "
        "DATE-OBS/TIME-OBS/MJD-OBS in the FITS header, or name the file like "
        "2026-01-09-1540_….fit. Refusing silent datetime.now() which would corrupt "
        "System III longitude."
    )


def format_utc(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")

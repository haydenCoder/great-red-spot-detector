#!/usr/bin/env python3
"""
jupos_io.py — JUPOS community-database export & import.

The JUPOS project (jupos.org / jupos.privat.t-online.de) maintains the
amateur measurement database WinJUPOS writes to. Publishing our measurements
means emitting the JUPOS observation format, and verifying community data
means reading it back.

EXPORT schema (what we write; a documented, stable subset of the JUPOS
"Messungen"/measurement record used by WinJUPOS IM files):

    Object,Date,Time,Observer,Instrument,Seeing,L_I,L_II,L_III,Lat,Length,Width,Method,Ref,Comment

  - Date as YYYY-MM-DD, Time as HH:MM:SS (UTC, mid-exposure — the same
    discipline the rest of this app enforces)
  - L_III/Lat are the measured System III longitude (deg W) and planetographic
    latitude (deg); L_I/L_II blank unless resolved
  - Length/Width in degrees (our ellipse/moment size definitions)
  - Method: the estimator tag (e.g. GS-ORANGE+ellipse_rim)
  - Ref: citation/audit trail string

IMPORT: `read_jupos_csv` parses the same columns (tolerant of the historical
whitespace/ordering variants) into dict rows; used to fold community truth
into validation campaigns.
"""
from __future__ import annotations

import csv
import datetime as dt
import io
from pathlib import Path
from typing import Dict, List, Optional, Sequence

JUPOS_FIELDS = (
    "Object", "Date", "Time", "Observer", "Instrument", "Seeing",
    "L_I", "L_II", "L_III", "Lat", "Length", "Width", "Method", "Ref", "Comment",
)


def _fmt_deg(v, places: int = 3) -> str:
    if v is None:
        return ""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return ""
    if f != f:          # NaN
        return ""
    return f"{f:.{places}f}"


def measurement_row(
    *,
    time_utc: dt.datetime,
    lon_iii_deg: float,
    lat_deg: float,
    object_name: str = "GRS",
    observer: str = "",
    instrument: str = "",
    seeing: str = "",
    lon_i_deg=None,
    lon_ii_deg=None,
    length_deg=None,
    width_deg=None,
    method: str = "GS-ORANGE",
    ref: str = "GRS-Observatory",
    comment: str = "",
) -> Dict[str, str]:
    """One JUPOS observation row (System III + planetographic latitude)."""
    if time_utc.tzinfo is not None:
        time_utc = time_utc.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return {
        "Object": object_name,
        "Date": time_utc.strftime("%Y-%m-%d"),
        "Time": time_utc.strftime("%H:%M:%S"),
        "Observer": observer,
        "Instrument": instrument,
        "Seeing": str(seeing or ""),
        "L_I": _fmt_deg(lon_i_deg),
        "L_II": _fmt_deg(lon_ii_deg),
        "L_III": _fmt_deg(lon_iii_deg),
        "Lat": _fmt_deg(lat_deg),
        "Length": _fmt_deg(length_deg),
        "Width": _fmt_deg(width_deg),
        "Method": method,
        "Ref": ref,
        "Comment": comment,
    }


def write_jupos_csv(path, rows: Sequence[Dict[str, str]]) -> Path:
    """Write rows (from `measurement_row` or pre-normalised) to CSV."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=JUPOS_FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in JUPOS_FIELDS})
    return path


def read_jupos_csv(path) -> List[Dict[str, object]]:
    """Parse a JUPOS CSV back into typed rows.

    - numeric L_* / Lat / Length / Width -> float (empty -> None)
    - Date+Time -> single `time_utc` datetime (naive UTC)
    Rows with an unparseable date are skipped (counts in `skipped`).
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    sample = text[:2048]
    dialect = csv.Sniffer().sniff(sample, delimiters=",;\t") if sample.strip() else csv.excel
    rows: List[Dict[str, object]] = []
    skipped = 0
    for rec in csv.DictReader(io.StringIO(text), dialect=dialect):
        out: Dict[str, object] = {}
        for f in JUPOS_FIELDS:
            v = (rec.get(f) or "").strip()
            out[f] = v
        # typed numerics
        for f in ("L_I", "L_II", "L_III", "Lat", "Length", "Width"):
            v = out[f]
            if v == "":
                out[f] = None
            else:
                try:
                    out[f] = float(str(v).replace(",", "."))
                except ValueError:
                    out[f] = None
        # typed time
        try:
            d = str(out.get("Date") or "")
            t = str(out.get("Time") or "00:00:00")
            for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%d.%m.%Y %H:%M:%S",
                        "%d.%m.%Y %H:%M", "%Y/%m/%d %H:%M:%S"):
                try:
                    out["time_utc"] = dt.datetime.strptime(f"{d} {t}", fmt)
                    break
                except ValueError:
                    continue
            if "time_utc" not in out:
                raise ValueError("no time parse")
        except Exception:
            skipped += 1
            continue
        rows.append(out)
    rows.sort(key=lambda r: r["time_utc"])
    return rows


def export_package_measurements(path, packages: Sequence[Dict[str, object]], **meta) -> Path:
    """Convenience: turn our published measurement packages into JUPOS rows.

    Each package dict needs `time_utc` (or `utc_iso`), `lon_iii_deg`,
    `lat_deg`; optional `length_deg`, `width_deg`, `method`.
    """
    rows = []
    for p in packages:
        t = p.get("time_utc")
        if t is None and p.get("utc_iso"):
            t = dt.datetime.fromisoformat(str(p["utc_iso"]).replace("Z", "+00:00")).replace(tzinfo=None)
        if isinstance(t, str):
            t = dt.datetime.fromisoformat(t.replace("Z", "+00:00")).replace(tzinfo=None)
        if t is None:
            continue
        rows.append(measurement_row(
            time_utc=t,
            lon_iii_deg=float(p["lon_iii_deg"]),
            lat_deg=float(p["lat_deg"]),
            length_deg=p.get("length_deg"),
            width_deg=p.get("width_deg"),
            method=str(p.get("method", meta.pop("method", "GS-ORANGE"))),
            comment=str(p.get("comment", "")),
            **{k: v for k, v in meta.items() if k in ("observer", "instrument", "seeing", "object_name", "ref")},
        ))
    return write_jupos_csv(path, rows)

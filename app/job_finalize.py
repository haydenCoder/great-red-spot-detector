#!/usr/bin/env python3
"""
Job finalize — plateau product stack (Champion → publish → SUPERDUPER)
=====================================================================

Single function so desktop and server cannot diverge on the archival answer.
Does not remeasure research-grade; attaches the ultimate automated product suite.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from verbose_log import CONSOLE


# Artifacts expected on a complete science job (best-effort)
EXPECTED_FILES = (
    "publish.json",
    "publish.txt",
    "champion.json",
    "champion.txt",
    "SUPERDUPER_BEST_ANSWER.txt",
    "SUPERDUPER_BEST_ANSWER.json",
    "REPORT_THIS_ONE_LINE.txt",
    "pro_ephemeris.json",
    "job_result.json",
)


def finalize_science_package(
    package: Dict[str, Any],
    image,
    *,
    nav=None,
    cm_iii_deg: float,
    distance_au: float = 5.2,
    cm_source: str = "unknown",
    sigma_cm_deg: float = 0.05,
    sub_lat_deg: float = 0.0,
    north_pa_deg: float = 0.0,
    channels: Optional[Dict[str, Any]] = None,
    out_dir: Optional[Path] = None,
    user_time_iso: str = "",
    time_error_seconds: float = 0.0,
    spice_horizons_dcm_deg: Optional[float] = None,
    write_reports: bool = False,
) -> Dict[str, Any]:
    """
    Attach champion + publish + winjupos_plus + superduper + completeness.
    Mutates package. Returns package.
    """
    out_dir = Path(out_dir) if out_dir else None

    # Ensure pro_ephemeris on package for champion CM cross-check
    if spice_horizons_dcm_deg is None:
        try:
            pe = package.get("pro_ephemeris") or {}
            raw = pe.get("raw") or {}
            if raw.get("spice_horizons_dcm_deg") is not None:
                spice_horizons_dcm_deg = float(raw["spice_horizons_dcm_deg"])
        except Exception:
            pass

    try:
        from champion_measure import attach_champion_to_package
        attach_champion_to_package(
            package,
            image,
            nav=nav,
            cm_iii_deg=float(cm_iii_deg),
            distance_au=float(distance_au),
            cm_source=str(cm_source),
            sigma_cm_deg=float(sigma_cm_deg),
            sub_lat_deg=float(sub_lat_deg),
            north_pa_deg=float(north_pa_deg),
            channels=channels,
            out_dir=out_dir,
            user_time_iso=user_time_iso,
            time_error_seconds=float(time_error_seconds or 0.0),
            spice_horizons_dcm_deg=spice_horizons_dcm_deg,
        )
    except Exception as e:
        CONSOLE.warn(f"finalize champion: {e}")

    try:
        from publish_primary import apply_publish_policy, format_publish_section
        apply_publish_policy(package)
        if out_dir is not None:
            pub = package.get("publish") or {}
            (out_dir / "publish.json").write_text(
                json.dumps(pub, indent=2, default=str), encoding="utf-8"
            )
            (out_dir / "publish.txt").write_text(
                format_publish_section(package), encoding="utf-8"
            )
    except Exception as e:
        CONSOLE.warn(f"finalize publish: {e}")

    try:
        from winjupos_plus import attach_winjupos_plus
        attach_winjupos_plus(package, out_dir=out_dir)
    except Exception as e:
        CONSOLE.warn(f"finalize winjupos_plus: {e}")

    try:
        from superduper import attach_superduper
        attach_superduper(package, out_dir=out_dir)
    except Exception as e:
        CONSOLE.warn(f"finalize superduper: {e}")

    complete = write_job_completeness(package, out_dir)
    package["job_completeness"] = complete

    if write_reports and out_dir is not None:
        try:
            from desktop_pipeline import write_package_reports
            write_package_reports(out_dir, package)
        except Exception as e:
            CONSOLE.warn(f"finalize reports: {e}")

    return package


def write_job_completeness(
    package: Dict[str, Any], out_dir: Optional[Path]
) -> Dict[str, Any]:
    """List expected archival files; mark complete when SUPERDUPER + publish + champion exist."""
    present: List[str] = []
    missing: List[str] = []
    if out_dir is not None and Path(out_dir).is_dir():
        for name in EXPECTED_FILES:
            p = Path(out_dir) / name
            if p.exists() and p.stat().st_size > 10:
                present.append(name)
            else:
                missing.append(name)
    else:
        missing = list(EXPECTED_FILES)

    ch = package.get("champion") or {}
    pub = package.get("publish") or {}
    sd = package.get("superduper") or {}
    complete = {
        "ok": (
            "SUPERDUPER_BEST_ANSWER.txt" in present
            and "publish.json" in present
            and ("champion.json" in present or ch.get("ok") is True)
        ),
        "present": present,
        "missing": missing,
        "has_champion": bool(ch.get("ok")),
        "has_publish": bool(pub.get("publish_lon_iii_deg") is not None or pub.get("publish_definition")),
        "has_superduper": bool(sd.get("report_this") or sd.get("citation_line")),
        "unbeatable_auto": bool(ch.get("unbeatable_auto")),
        "publish_definition": pub.get("publish_definition"),
        "plateau_note": (
            "Automated optical product stack is complete for this app version. "
            "Further gains require better input data, not more estimator soup."
        ),
    }
    if out_dir is not None:
        try:
            Path(out_dir).mkdir(parents=True, exist_ok=True)
            (Path(out_dir) / "JOB_COMPLETE.json").write_text(
                json.dumps(complete, indent=2), encoding="utf-8"
            )
            status = "COMPLETE" if complete["ok"] else "PARTIAL"
            (Path(out_dir) / "JOB_COMPLETE.txt").write_text(
                f"Job product stack: {status}\n"
                f"UNBEATABLE_AUTO: {complete['unbeatable_auto']}\n"
                f"Publish: {complete['publish_definition']}\n"
                f"Present: {', '.join(present) or '—'}\n"
                f"Missing: {', '.join(missing) or '—'}\n"
                f"\n{complete['plateau_note']}\n",
                encoding="utf-8",
            )
        except Exception as e:
            complete["write_error"] = str(e)
    return complete

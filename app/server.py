#!/usr/bin/env python3
"""
GRS Observatory server — Flask API for processing GRS measurements remotely

This is the web API version of the desktop pipeline. I built it so I could
process images from a browser without having to run the desktop app locally
(my laptop is old and slow, and sometimes I want to process images from the
lab machines).

It wraps the same measurement stack as the desktop app — research-grade
measurement, VLBI metrology, Monte Carlo, gold standard, champion, publish
policy, SUPERDUPER, etc. — and exposes it via REST endpoints.

Target: best ground-based optical metrology I can manage (formal error
budgets, multi-scale NCC, phase-reference probes, hierarchical MC).
Not radio-VLBI microarcseconds — honest optical floor for an extended
cloud feature. I'm a student, not JPL.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import traceback
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import numpy as np

APP_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(APP_DIR))

from flask import Flask, jsonify, render_template, request, send_from_directory

from verbose_log import CONSOLE
from ram_ssd import cleanup_ssd_cache, free_memory
from synthetic_hq import SynthSpec, generate
from precision_engine import (
    fit_limb_nav,
    measure_grs_precision,
    monte_carlo_precision,
    sky_error_arcsec,
    wrap_diff,
    cap_mc_iterations,
)
from nasa_compare import compare_measurement_to_nasa, write_comparison_report
from research_grade import run_research_grade, write_publication_bundle
from vlbi_metrology import write_vlbi_bundle
from ephemeris_pro import (
    resolve_pro_ephemeris,
    write_ephemeris_report,
    save_example_winjupos_template,
    EPH_DIR,
)
from multi_epoch import (
    load_epochs_from_dir,
    load_epochs_from_list,
    build_differential_series,
    write_multi_epoch_report,
)
from hard_synth_suite import run_hard_synth_suite
from desktop_pipeline import (
    write_image_preview,
    next_run_id,
    metrics_filename_suffix,
    _load_image,
)
from result_report import format_human_report
from gold_standard import attach_gold_to_package
import nn_grs
import grs_complete_system as grs


def _wj_manual_from_data(data: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
    """Optional human WinJUPOS lon/lat paste for validation (not NASA truth)."""
    lon = data.get("winjupos_manual_lon")
    lat = data.get("winjupos_manual_lat")
    try:
        lon = float(lon) if lon is not None and lon != "" else None
    except Exception:
        lon = None
    try:
        lat = float(lat) if lat is not None and lat != "" else None
    except Exception:
        lat = None
    return lon, lat


def _run_gold(
    package: Dict[str, Any],
    meas: np.ndarray,
    *,
    nav: Any = None,
    cm_iii_deg: float = 0.0,
    distance_au: float = 5.2,
    cm_source: str = "unknown",
    user_time_iso: str = "",
    data: Optional[Dict[str, Any]] = None,
    out: Optional[Path] = None,
    channels: Optional[Dict[str, Any]] = None,
    fits_path: Optional[str] = None,
    time_error_seconds: float = 0.0,
) -> Dict[str, Any]:
    data = data or {}
    wj_lon, wj_lat = _wj_manual_from_data(data)
    # Prefer FITS header mid-time when user left default-ish empty
    try:
        from sota_accuracy import extract_fits_time
        ft = extract_fits_time(fits_path or data.get("path") or package.get("path"))
        if ft and (not user_time_iso or data.get("prefer_fits_time")):
            package["fits_time_extracted"] = ft
            if data.get("prefer_fits_time") or not str(user_time_iso).strip():
                user_time_iso = ft
                package["user_time"] = ft
                CONSOLE.ok(f"Using FITS mid-time for geometry: {ft}")
    except Exception:
        pass
    try:
        attach_gold_to_package(
            package,
            meas,
            nav=nav,
            cm_iii_deg=cm_iii_deg,
            distance_au=distance_au,
            cm_source=cm_source,
            user_time_iso=user_time_iso,
            winjupos_manual_lon=wj_lon,
            winjupos_manual_lat=wj_lat,
            out_dir=out,
            channels=channels,
            run_every_method=True,
        )
        # SOTA layer needs path for FITS + time error
        if package.get("sota") is None:
            try:
                from sota_accuracy import apply_sota_to_package
                apply_sota_to_package(
                    package,
                    nav=nav,
                    distance_au=distance_au,
                    cm_source=cm_source,
                    user_time_iso=user_time_iso,
                    time_error_seconds=time_error_seconds,
                    fits_path=fits_path or data.get("path") or package.get("path"),
                    winjupos_manual_lon=wj_lon,
                    winjupos_manual_lat=wj_lat,
                )
            except Exception as e2:
                CONSOLE.debug(f"SOTA second pass: {e2}")
        CONSOLE.ok(
            f"SOTA primary={package.get('headline', {}).get('gold_primary_definition')}  "
            f"lon={package.get('headline', {}).get('gold_lon_iii_deg')}  "
            f"grade={package.get('headline', {}).get('sota_quality') or package.get('headline', {}).get('gold_procedure_grade')}  "
            f"inliers={package.get('headline', {}).get('sota_n_inliers')}  "
            f"methods={package.get('headline', {}).get('n_methods_ok')}/{package.get('headline', {}).get('n_methods_total')}"
        )
    except Exception as e:
        CONSOLE.warn(f"Gold standard soft-fail: {e}")
        package.setdefault("gold_standard", {"ok": False, "error": str(e)})
    return package

UPLOAD = APP_DIR / "uploads"
OUTPUT = APP_DIR / "outputs"
for d in (UPLOAD, OUTPUT):
    d.mkdir(exist_ok=True)

# Observer country → UTC offset hours (standard time; DST not auto-applied)
COUNTRIES: Dict[str, Dict[str, Any]] = {
    "UTC": {"name": "UTC (no offset)", "utc_offset_h": 0.0, "hint": "Enter times in UTC"},
    "US": {"name": "United States", "utc_offset_h": -5.0, "hint": "ET ≈ UTC−5 (standard)"},
    "CA": {"name": "Canada", "utc_offset_h": -5.0, "hint": "Eastern ≈ UTC−5"},
    "GB": {"name": "United Kingdom", "utc_offset_h": 0.0, "hint": "GMT ≈ UTC"},
    "IE": {"name": "Ireland", "utc_offset_h": 0.0, "hint": "GMT ≈ UTC"},
    "FR": {"name": "France", "utc_offset_h": 1.0, "hint": "CET ≈ UTC+1"},
    "DE": {"name": "Germany", "utc_offset_h": 1.0, "hint": "CET ≈ UTC+1"},
    "ES": {"name": "Spain", "utc_offset_h": 1.0, "hint": "CET ≈ UTC+1"},
    "IT": {"name": "Italy", "utc_offset_h": 1.0, "hint": "CET ≈ UTC+1"},
    "NL": {"name": "Netherlands", "utc_offset_h": 1.0, "hint": "CET ≈ UTC+1"},
    "AU": {"name": "Australia", "utc_offset_h": 10.0, "hint": "AEST ≈ UTC+10"},
    "NZ": {"name": "New Zealand", "utc_offset_h": 12.0, "hint": "NZST ≈ UTC+12"},
    "JP": {"name": "Japan", "utc_offset_h": 9.0, "hint": "JST ≈ UTC+9"},
    "CN": {"name": "China", "utc_offset_h": 8.0, "hint": "CST ≈ UTC+8"},
    "IN": {"name": "India", "utc_offset_h": 5.5, "hint": "IST ≈ UTC+5:30"},
    "BR": {"name": "Brazil", "utc_offset_h": -3.0, "hint": "BRT ≈ UTC−3"},
    "AR": {"name": "Argentina", "utc_offset_h": -3.0, "hint": "ART ≈ UTC−3"},
    "ZA": {"name": "South Africa", "utc_offset_h": 2.0, "hint": "SAST ≈ UTC+2"},
    "AE": {"name": "UAE", "utc_offset_h": 4.0, "hint": "GST ≈ UTC+4"},
    "SG": {"name": "Singapore", "utc_offset_h": 8.0, "hint": "SGT ≈ UTC+8"},
}


app = Flask(__name__, template_folder=str(APP_DIR / "templates"), static_folder=str(APP_DIR / "static"))
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 ** 3
# Avoid stale CSS/JS during rapid UI iteration (browsers still honor ?v= query)
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

try:
    from security_hard import (
        SecurityError,
        rate_limit_ok,
        sanitize_filename,
        safe_upload_extension,
        assert_safe_process_path,
        safe_resolve_under,
        host_allowed,
        security_headers,
        data_roots,
        strip_control_chars,
    )
    _SEC = True
except Exception:
    _SEC = False
    SecurityError = PermissionError  # type: ignore


@app.before_request
def _security_before():
    """Rate limit + host checks for common abuse patterns."""
    if not _SEC:
        return None
    # skip static
    if request.path.startswith("/static/"):
        return None
    bind = os.environ.get("GRS_HOST", "127.0.0.1")
    if not host_allowed(request.host or "", bind_host=bind):
        return jsonify({"ok": False, "error": "host not allowed"}), 403
    ip = request.remote_addr or "local"
    if not rate_limit_ok(ip):
        return jsonify({"ok": False, "error": "rate limit — slow down"}), 429
    return None


@app.after_request
def _no_cache_static(resp):
    try:
        if _SEC:
            for k, v in security_headers().items():
                resp.headers.setdefault(k, v)
        if request.path.startswith("/static/"):
            resp.headers["Cache-Control"] = "no-cache, must-revalidate"
    except Exception:
        pass
    return resp

_lock = threading.Lock()
_job: Dict[str, Any] = {
    "running": False, "id": None, "run_n": None, "folder": None,
    "kind": None, "result": None, "error": None,
}


def _start(kind: str) -> Tuple[str, int, Path]:
    """Start job. Returns (short_hex, run_n, output_dir with detailed name)."""
    with _lock:
        if _job["running"]:
            raise RuntimeError("Job already running")
        run_n, short, folder = next_run_id(OUTPUT, kind)
        out = OUTPUT / folder
        out.mkdir(parents=True, exist_ok=True)
        _job.update(
            running=True, id=short, run_n=run_n, folder=folder,
            kind=kind, result=None, error=None,
        )
        return short, run_n, out


def _finish(result=None, error=None):
    with _lock:
        _job["running"] = False
        _job["result"] = result
        _job["error"] = error


def _find_output_dir(job_id: str) -> Optional[Path]:
    """Locate output folder by short hex id (suffix) or full folder name."""
    if not job_id:
        return None
    # Exact legacy: job_<id>, synth_<id>, …
    for prefix in ("job_", "synth_", "multi_", "hard_", "eph_", "factory_"):
        p = OUTPUT / f"{prefix}{job_id}"
        if p.is_dir():
            return p
    # Detailed: *_{job_id} or folder contains job_id
    for p in OUTPUT.iterdir():
        if p.is_dir() and (p.name.endswith(f"_{job_id}") or job_id in p.name):
            return p
    return None


def _attach_human_report(package: Dict[str, Any], out: Path, run_n: Optional[int] = None) -> Dict[str, Any]:
    """
    Build the long human report (YOUR vs NASA, diffs, tips, full dump),
    write FULL_REPORT*.txt, and put text on the package for the web UI.
    """
    try:
        text = format_human_report(package)
        package["text"] = text
        rn = run_n if run_n is not None else package.get("run_n")
        name = f"FULL_REPORT_run{int(rn):04d}.txt" if rn is not None else "FULL_REPORT.txt"
        (out / name).write_text(text, encoding="utf-8")
        (out / "FULL_REPORT.txt").write_text(text, encoding="utf-8")
        CONSOLE.ok(f"Human report → {name}  ({len(text.splitlines())} lines)")
    except Exception as e:
        CONSOLE.warn(f"Human report failed: {e}")
        package.setdefault("text", f"(report builder error: {e})\n\n" + json.dumps(package, indent=2, default=str))
    return package


ACCURACY_TIPS = [
    "MC iterations: 50 = quick, 200 = good, 500–1000 = research-grade precision (slower).",
    "Injection trials: 16 = draft, 28–36 = solid, 48–64 = tight bias calibration.",
    "Always enter the mid-exposure time of your real photo (UTC preferred).",
    "Pick your country so local clock vs UTC is clear; ephemeris uses UTC.",
    "Time error (s): set honest clock uncertainty — it becomes System III σ.",
    "Real FITS/PNG Process uses YOUR file. Synthetic is a fake planet for tests only.",
    "Factory Night with a loaded file processes that file; without a file it generates synthetic.",
    "Paste WinJUPOS CM III when you need absolute System III longitude.",
    "Enable VLBI + Max fidelity for the full optical metrology stack.",
    "σ_tot ≤ 1–2″ is the honest ground-based target for an extended cloud feature.",
]


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def health():
    try:
        from product_core import PRODUCT_VERSION
        _ver = PRODUCT_VERSION
    except Exception:
        _ver = "6.5.0"
    return jsonify({
        "ok": True,
        "app": "GRS Observatory — optical GRS metrology",
        "version": _ver,
        "pipeline": getattr(grs, "__version__", "?"),
        "technology": (
            "Pro ephemeris (WinJUPOS/SPICE/Horizons) + optical metrology stack + "
            "multi-epoch differential tracking + hard-synth stress calibration"
        ),
        "ram_gb": 16,
        "target_arcsec": "0.1-2 optical (methods from VLBI; not μas radio)",
        "modes": ["vlbi_optical", "spire_m_classic", "multi_epoch", "hard_synth", "real_image", "factory_night"],
        "pillars": [
            "pro_cm_ephemeris",
            "horizons_orientation",
            "multi_epoch_phase_ref",
            "hard_synth_calibration",
        ],
        "time": datetime.now(timezone.utc).isoformat(),
        "tips": ACCURACY_TIPS,
    })


@app.route("/api/logs")
def logs():
    return jsonify({"lines": CONSOLE.since(int(request.args.get("after", 0))), "verbose": CONSOLE.verbose})


@app.route("/api/logs/clear", methods=["POST"])
def logs_clear():
    CONSOLE.clear()
    return jsonify({"ok": True})


@app.route("/api/verbose", methods=["POST"])
def verbose():
    data = request.get_json(force=True, silent=True) or {}
    CONSOLE.verbose = bool(data.get("verbose", True))
    return jsonify({"ok": True, "verbose": CONSOLE.verbose})


@app.route("/api/job")
def job():
    with _lock:
        return jsonify(dict(_job))


@app.route("/api/regions")
def regions():
    """Synthetic image framing only — not observer location."""
    return jsonify({
        "global": "Full disk (synthetic framing)",
        "grs_closeup": "GRS close-up (synthetic)",
        "se_belt": "SEB band (synthetic)",
        "equatorial": "Equatorial (synthetic)",
        "full_disk": "Full disk margin (synthetic)",
    })


@app.route("/api/countries")
def countries():
    """Observer country for timezone clarity (not synthetic framing)."""
    return jsonify(COUNTRIES)


@app.route("/api/tips")
def tips():
    return jsonify({"tips": ACCURACY_TIPS})


@app.route("/api/resolutions")
def resolutions():
    return jsonify({
        "auto": {"note": "Max safe for 16GB (usually 8K)"},
        "4K": {"width": 3840, "height": 2160},
        "8K": {"width": 7680, "height": 4320},
        "16K": {"width": 15360, "height": 8640, "note": "May downshift if RAM tight"},
    })


@app.route("/api/nn/status")
def nn_status():
    return jsonify(nn_grs.get_train_status())


@app.route("/api/nn/train", methods=["POST"])
def nn_train():
    data = request.get_json(force=True, silent=True) or {}
    epochs = int(data.get("epochs") or 30)
    samples = int(data.get("samples_per_epoch") or 16)
    lr = float(data.get("lr") or 0.01)
    seed = int(data.get("seed") or 0)
    fine_tune = bool(data.get("fine_tune", True))
    overnight = bool(data.get("overnight", False))
    hours = float(data.get("hours") or 8.0)
    # Keep training with lid closed / idle (macOS caffeinate) unless user opts out
    prevent_sleep = bool(data.get("prevent_sleep", True))
    epochs = max(1, min(epochs, 500))
    samples = max(4, min(samples, 64))
    hours = max(0.1, min(hours, 72.0))

    if nn_grs.get_train_status().get("running"):
        return jsonify({"ok": False, "error": "Training already running"}), 409

    def worker():
        if overnight:
            nn_grs.overnight_train(
                hours=hours,
                samples_per_epoch=samples,
                use_existing=fine_tune,
                resume=True,
                prevent_sleep=prevent_sleep,
            )
        else:
            nn_grs.auto_train(
                epochs=epochs,
                samples_per_epoch=samples,
                lr=lr,
                seed=seed,
                use_existing=fine_tune,
                prevent_sleep=prevent_sleep,
            )

    threading.Thread(target=worker, daemon=True).start()
    CONSOLE.info(
        f"SPIRE-Net train started: "
        f"{'overnight '+str(hours)+'h' if overnight else 'epochs='+str(epochs)}  "
        f"samples/ep={samples}  prevent_sleep={prevent_sleep}  NaN-guard=ON"
    )
    return jsonify({
        "ok": True,
        "message": "training started",
        "epochs": epochs,
        "samples_per_epoch": samples,
        "overnight": overnight,
        "hours": hours,
        "prevent_sleep": prevent_sleep,
        "nan_guard": True,
    })


@app.route("/api/nn/stop", methods=["POST"])
def nn_stop():
    nn_grs.request_train_stop()
    return jsonify({"ok": True, "message": "stop requested — will save safely and exit loop"})


@app.route("/api/upload", methods=["POST"])
def upload():
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "No file"}), 400
    try:
        if _SEC:
            ext = safe_upload_extension(f.filename)
            safe_name = sanitize_filename(f.filename)
        else:
            ext = Path(f.filename).suffix.lower()
            safe_name = Path(f.filename).name
            if ext not in (".fit", ".fits", ".fts", ".ser", ".png", ".jpg", ".jpeg"):
                return jsonify({"ok": False, "error": f"Bad type {ext}"}), 400
    except SecurityError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    short = uuid.uuid4().hex[:8]
    name = f"{stamp}_{short}{ext}"
    dest = UPLOAD / name
    f.save(dest)
    # Always build a sharp PNG from FITS/SER so the web preview shows the real photo
    preview_name = f"{stamp}_{short}_preview.png"
    preview_path = UPLOAD / preview_name
    preview_url = None
    try:
        write_image_preview(dest, preview_path, max_side=2048)
        preview_url = f"/api/file?path={preview_path}"
        CONSOLE.ok(f"Upload preview → {preview_name}")
    except Exception as e:
        CONSOLE.warn(f"Upload preview failed: {e}")
    CONSOLE.ok(f"Uploaded REAL image {f.filename} → {name}")
    return jsonify({
        "ok": True,
        "path": str(dest),
        "name": name,
        "original": f.filename,
        "source_kind": "real_file",
        "preview": preview_url,
        "preview_path": str(preview_path) if preview_url else None,
    })


@app.route("/api/process", methods=["POST"])
def process():
    data = request.get_json(force=True, silent=True) or {}
    path = data.get("path")
    user_time = (data.get("user_time") or "").strip()
    if not user_time:
        return jsonify({
            "ok": False,
            "error": "Observation UTC required (refusing silent datetime.now())",
        }), 400
    if _SEC:
        user_time = strip_control_chars(user_time, 80)
    time_error = float(data.get("time_error_seconds") or 0)
    region = data.get("region") or "global"
    verbose = bool(data.get("verbose", True))
    nasa = bool(data.get("nasa_compare", True))
    mc_iter_req = int(data.get("mc_iterations") or 50)
    max_fidelity = bool(data.get("max_fidelity", True))
    injection_n = int(data.get("injection_trials") or (32 if max_fidelity else 16))
    try:
        if _SEC:
            roots = data_roots(APP_DIR)
            p_check = assert_safe_process_path(path, *roots)
            path = str(p_check)
        elif not path or not Path(path).exists():
            return jsonify({"ok": False, "error": "Missing file"}), 400
    except SecurityError as e:
        return jsonify({"ok": False, "error": str(e)}), 403
    CONSOLE.verbose = verbose
    try:
        jid, run_n, out = _start("job")
    except RuntimeError as e:
        return jsonify({"ok": False, "error": str(e)}), 409

    def worker():
        try:
            mc_iter = cap_mc_iterations(mc_iter_req, megapixels=8.0)
            CONSOLE.info("=" * 60)
            CONSOLE.info("REAL IMAGE PROCESS START (not synthetic)")
            CONSOLE.info(
                f"run=#{run_n:04d}  Target: 1–2″  |  MC={mc_iter}  injections={injection_n}  "
                f"max_fidelity={max_fidelity}"
            )
            CONSOLE.info(f"output → {out.name}")
            p = Path(path)
            meas, channels, _prev = _load_image(p)
            channels = dict(channels) if channels else {}

            # Sharp preview of the user's actual FITS/PNG (not a soft pipeline product)
            source_preview = out / f"source_preview_run{run_n:04d}.png"
            try:
                if channels and all(k in channels for k in ("R", "G", "B")):
                    write_image_preview(
                        np.stack([channels["R"], channels["G"], channels["B"]], axis=0),
                        source_preview,
                    )
                else:
                    write_image_preview(meas, source_preview)
                CONSOLE.ok(f"Source preview (your file) → {source_preview.name}")
            except Exception as e:
                CONSOLE.warn(f"Source preview failed: {e}")
                source_preview = None

            try:
                cfg = grs.replace(
                    grs.PRESET_FAST_PREVIEW, mode="imaging", out_dir=str(out),
                    work_dir=str(out / "work"), min_frames=1, max_clip_frac=1.0, derot_enable=False,
                )
                pipe = grs.GRSCompletePipeline(cfg)
                pipe.process_path(path, "RGB")
                pipe.build_channels()
                pipe.run_imaging()
                if pipe.channels:
                    channels = dict(pipe.channels)
                    if "R" in channels:
                        meas = channels["R"]
            except Exception as e:
                CONSOLE.warn(f"Imaging branch soft-fail: {e}")

            factory_mode = bool(data.get("factory_mode", True))
            use_vlbi = bool(data.get("use_vlbi", True))
            aperture_m = float(data.get("aperture_m") or 0.35)
            winjupos_path = data.get("winjupos_path")
            cm_override = data.get("cm_iii_override")
            cm_override = float(cm_override) if cm_override is not None else None
            sub_lat_ov = data.get("sub_lat_override")
            sub_lat_ov = float(sub_lat_ov) if sub_lat_ov is not None else None
            north_pa_ov = data.get("north_pa_override")
            north_pa_ov = float(north_pa_ov) if north_pa_ov is not None else None
            # Professional ephemeris FIRST — fail loud (never silent CM=0)
            pe = resolve_pro_ephemeris(
                user_time, time_error_seconds=time_error,
                cm_override=cm_override if cm_override is not None else None,
                winjupos_path=winjupos_path,
                sub_lat_override=sub_lat_ov,
                north_pa_override=north_pa_ov,
            )
            write_ephemeris_report(out / "pro_ephemeris.json", pe)
            nav = fit_limb_nav(meas, cm_iii_deg=pe.cm_iii_deg, distance_au=pe.distance_au)
            nav.cm_iii_deg = pe.cm_iii_deg
            nav.distance_au = pe.distance_au
            if pe.apply_orientation:
                nav.sub_lat_deg = float(pe.sub_obs_lat_deg or 0.0)
                nav.north_pa_deg = float(pe.north_pa_deg or 0.0)
            measure_grs_precision._use_nn = bool(data.get("use_nn", True))
            rg = run_research_grade(
                meas, nav=nav, cm_iii_deg=nav.cm_iii_deg, distance_au=nav.distance_au,
                channels=channels or None, injection_trials=injection_n, mc_iter=mc_iter,
                seed=42, max_fidelity=max_fidelity, factory_mode=factory_mode,
                user_time_iso=user_time, time_error_seconds=time_error,
                aperture_m=aperture_m, use_vlbi=use_vlbi,
                winjupos_path=winjupos_path,
                sub_lat_override=sub_lat_ov,
                north_pa_override=north_pa_ov,
            )
            write_publication_bundle(out / "research_grade.json", rg, extra={
                "user_time": user_time, "time_error_seconds": time_error, "path": path,
                "mode": "vlbi_optical" if use_vlbi and max_fidelity else "spire_m",
            })
            try:
                vf = (rg.methods or {}).get("vlbi_full")
                if isinstance(vf, dict):
                    (out / "vlbi_metrology.json").write_text(
                        json.dumps(vf, indent=2, default=str), encoding="utf-8"
                    )
                    eb = vf.get("error_budget") or {}
                    (out / "vlbi_metrology.txt").write_text(
                        "\n".join([
                            "VLBI-INSPIRED OPTICAL METROLOGY",
                            f"Grade: {vf.get('grade')}",
                            f"lon={vf.get('lon_iii_deg')} lat={vf.get('lat_deg')}",
                            f"σ_tot={eb.get('sigma_total_sky_arcsec')} arcsec",
                            f"components: {eb.get('components_sky_arcsec')}",
                        ]),
                        encoding="utf-8",
                    )
            except Exception as e:
                CONSOLE.debug(f"vlbi bundle write: {e}")

            measured = {
                "lon_iii_deg": rg.lon_bias_corrected_deg,
                "lat_deg": rg.lat_bias_corrected_deg,
                "length_deg": rg.length_deg,
                "width_deg": rg.width_deg,
            }
            # Geometry context only — NOT an official GRS longitude product
            nasa_rep = None
            if nasa:
                comp = compare_measurement_to_nasa(measured, user_time, time_error)
                write_comparison_report(out / "nasa_comparison.json", comp)
                nasa_rep = comp.to_dict()
                nasa_rep["grade"] = comp.grade()
                nasa_rep["role"] = "geometry_context_only"
                nasa_rep["disclaimer"] = (
                    "Horizons/model = Jupiter geometry + schematic GRS trend. "
                    "NOT NASA official GRS lon. Prefer gold_standard + WinJUPOS manual check."
                )

            country = str(data.get("country") or "UTC")
            cm_src = (pe.cm_source if pe is not None else "analytical_or_nav")
            mtag = metrics_filename_suffix(
                lon=rg.lon_bias_corrected_deg,
                lat=rg.lat_bias_corrected_deg,
                sigma=rg.sigma_total_sky_arcsec,
                grade=rg.grade,
            )
            report = {
                "job_id": jid,
                "run_n": run_n,
                "source_kind": "real_file",
                "kind": "process",
                "user_time": user_time,
                "time_error_seconds": time_error,
                "country": country,
                "region": region,
                "mode": "vlbi_optical" if use_vlbi and max_fidelity else "spire_m",
                "philosophy": (
                    "Replicate professional procedure (geometry + named definitions + "
                    "optional WinJUPOS manual check). Do not treat NASA/model as GRS truth."
                ),
                "headline": {
                    "source_kind": "REAL FILE (your upload)",
                    "run_n": run_n,
                    "lon_iii_deg_bias_corrected": rg.lon_bias_corrected_deg,
                    "lat_deg_bias_corrected": rg.lat_bias_corrected_deg,
                    "lon_raw": rg.lon_iii_deg,
                    "lat_raw": rg.lat_deg,
                    "length_deg": rg.length_deg,
                    "width_deg": rg.width_deg,
                    "sigma_total_sky_arcsec": rg.sigma_total_sky_arcsec,
                    "sigma_random_sky_arcsec": rg.sigma_random_sky_arcsec,
                    "sigma_systematic_sky_arcsec": rg.sigma_systematic_sky_arcsec,
                    "injection_mean_recovery_arcsec": rg.injection_mean_sky_arcsec,
                    "bias_lon_deg": rg.bias_lon_deg,
                    "bias_lat_deg": rg.bias_lat_deg,
                    "filter_closure_arcsec": rg.filter_closure_arcsec,
                    "grade": rg.grade,
                    "cm_source": cm_src,
                    "target_0_5_arcsec": rg.sigma_total_sky_arcsec <= 0.5,
                    "target_1_arcsec": rg.sigma_total_sky_arcsec <= 1.0,
                    "target_2_arcsec": rg.sigma_total_sky_arcsec <= 2.0,
                    "definition_n": rg.definition_n,
                    "injection_n": rg.injection_n,
                    "metrology": "pro-procedure + VLBI-inspired optical" if use_vlbi and max_fidelity else "pro-procedure + SPIRE-M",
                },
                "research_grade": rg.to_dict(),
                "nasa": nasa_rep,
                "pro_ephemeris": (pe.to_dict() if pe is not None else None),
                "target": {"sky_arcsec": 2.0, "met": rg.sigma_total_sky_arcsec <= 2.0},
                "output_dir": str(out),
                "output_folder": out.name,
            }
            report["path"] = str(path)
            _run_gold(
                report, meas, nav=nav,
                cm_iii_deg=nav.cm_iii_deg, distance_au=nav.distance_au,
                cm_source=cm_src, user_time_iso=user_time, data=data, out=out,
                channels=channels or None, fits_path=str(path),
                time_error_seconds=time_error,
            )
            # Prefer gold primary in headline when available
            gs = report.get("gold_standard") or {}
            if gs.get("ok"):
                report["headline"]["lon_iii_deg_bias_corrected"] = gs.get(
                    "primary_lon_iii_deg", rg.lon_bias_corrected_deg
                )
                report["headline"]["lat_deg_bias_corrected"] = gs.get(
                    "primary_lat_deg", rg.lat_bias_corrected_deg
                )
            # Plateau product stack (same as desktop): Champion → publish → SUPERDUPER
            try:
                from job_finalize import finalize_science_package
                _dcm = None
                try:
                    _dcm = (pe.raw or {}).get("spice_horizons_dcm_deg")
                    if _dcm is not None:
                        _dcm = float(_dcm)
                except Exception:
                    _dcm = None
                finalize_science_package(
                    report,
                    meas,
                    nav=nav,
                    cm_iii_deg=float(nav.cm_iii_deg),
                    distance_au=float(nav.distance_au),
                    cm_source=str(cm_src),
                    sigma_cm_deg=float(getattr(pe, "sigma_cm_deg", 0.05) or 0.05),
                    sub_lat_deg=float(getattr(nav, "sub_lat_deg", 0.0) or 0.0),
                    north_pa_deg=float(getattr(nav, "north_pa_deg", 0.0) or 0.0),
                    channels=channels or None,
                    out_dir=out,
                    user_time_iso=user_time,
                    time_error_seconds=float(time_error or 0.0),
                    spice_horizons_dcm_deg=_dcm,
                )
                # Align headline with publish / champion
                pub = report.get("publish") or {}
                if pub.get("publish_lon_iii_deg") is not None:
                    report["headline"]["lon_iii_deg"] = pub.get("publish_lon_iii_deg")
                    report["headline"]["lat_deg"] = pub.get("publish_lat_deg")
                    report["headline"]["publish_definition"] = pub.get("publish_definition")
            except Exception as e:
                CONSOLE.warn(f"Server finalize (champion/SUPERDUPER): {e}")
            CONSOLE.ok(
                f"HEADLINE run#{run_n:04d} (REAL): lon={report['headline'].get('lon_iii_deg_bias_corrected', 0.0):.4f}° "
                f"lat={report['headline'].get('lat_deg_bias_corrected', 0.0):.4f}°  "
                f"σ_tot={rg.sigma_total_sky_arcsec:.3f}\"  {rg.grade}  "
                f"GS={report['headline'].get('gold_primary_definition')}  "
                f"champ={ (report.get('champion') or {}).get('grade') }"
            )
            # Prefer sharp source preview of YOUR FITS; fall back to pipeline product
            if source_preview and Path(source_preview).exists():
                report["preview"] = f"/api/file?path={source_preview}"
                report["preview_label"] = "Your uploaded file (sharp stretch)"
            elif (out / "lrgb_final.png").exists():
                report["preview"] = f"/api/file?path={out / 'lrgb_final.png'}"
                report["preview_label"] = "Pipeline LRGB product"
            result_name = f"job_result_run{run_n:04d}{mtag}.json"
            _attach_human_report(report, out, run_n)
            # JSON without the huge text field (text lives in FULL_REPORT.txt)
            dump = {k: v for k, v in report.items() if k != "text"}
            (out / result_name).write_text(json.dumps(dump, indent=2, default=str))
            (out / "job_result.json").write_text(json.dumps(dump, indent=2, default=str))
            CONSOLE.ok(f"PROCESS COMPLETE → {out.name} / {result_name}")
            free_memory()
            _finish(report)
        except Exception as e:
            CONSOLE.error(str(e))
            CONSOLE.debug(traceback.format_exc())
            _finish(error=str(e))

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"ok": True, "job_id": jid, "run_n": run_n, "output_dir": str(out)})


@app.route("/api/synthetic", methods=["POST"])
def synthetic():
    data = request.get_json(force=True, silent=True) or {}
    # Synthetic ALWAYS draws a random observation epoch (no time required from user).
    # Session time is ignored for synthetic; process/real data still uses user_time.
    region = data.get("region") or "global"
    time_error = float(data.get("time_error_seconds") or 0)
    verbose = bool(data.get("verbose", True))
    preset = data.get("resolution_preset") or "auto"
    mc_iter = int(data.get("mc_iterations") or 0)
    process_after = bool(data.get("process_after", True))
    nasa = bool(data.get("nasa_compare", True))
    seed = data.get("seed")
    seed = int(seed) if seed is not None else None
    CONSOLE.verbose = verbose
    try:
        jid, run_n, out = _start("synth")
    except RuntimeError as e:
        return jsonify({"ok": False, "error": str(e)}), 409

    def worker():
        try:
            cleanup_ssd_cache()
            CONSOLE.info("=" * 60)
            CONSOLE.info("SYNTHETIC PRECISION RUN (FAKE planet — not your photo)")
            CONSOLE.info(f"run=#{run_n:04d} region={region} res={preset} process_after={process_after}")
            CONSOLE.info(f"output → {out.name}")
            png, fit, truth = generate(
                SynthSpec(
                    user_time_iso="",
                    region=region,
                    time_error_seconds=time_error,
                    resolution_preset=preset,
                    seed=seed,
                    random_time=True,  # always random for synthetic
                ),
                out,
            )
            # Measure / NASA use the epoch actually drawn for the frame
            synth_time = truth.get("user_time_iso") or ""
            if not str(synth_time).strip():
                raise ValueError(
                    "Synthetic truth missing user_time_iso — refusing silent datetime.now() for System III."
                )
            random_time = True
            mp = truth["width"] * truth["height"] / 1e6
            mc_iter_use = cap_mc_iterations(mc_iter if mc_iter > 0 else 50, megapixels=mp)
            CONSOLE.info(f"Using MC iterations: {mc_iter_use} (precision turnaround cap)")

            result: Dict[str, Any] = {
                "job_id": jid,
                "run_n": run_n,
                "source_kind": "synthetic",
                "kind": "synthetic",
                "truth": truth,
                "png": f"/api/file?path={png}",
                "preview": f"/api/file?path={png}",
                "preview_label": "SYNTHETIC (computer-generated — not a real photo)",
                "fit": str(fit),
                "output_dir": str(out),
                "output_folder": out.name,
                "synth_epoch": synth_time,
                "random_time": bool(truth.get("random_time", random_time)),
            }

            if process_after:
                if str(fit).endswith((".fit", ".fits")):
                    arr, _ = grs.read_fits(fit)
                    img = np.asarray(arr, dtype=np.float64)
                    if img.ndim == 3 and img.shape[0] == 3:
                        meas = img[0]
                        channels = {"R": img[0], "G": img[1], "B": img[2]}
                    else:
                        meas = img
                        channels = None
                else:
                    from PIL import Image
                    rgb = np.asarray(Image.open(png).convert("RGB"), dtype=np.float64) / 255.0
                    meas = rgb[:, :, 0]
                    channels = {"R": rgb[:, :, 0], "G": rgb[:, :, 1], "B": rgb[:, :, 2]}

                if meas.size > 25_000_000:
                    CONSOLE.info("Half-scale for research metrology (full PNG kept)")
                    meas = meas[::2, ::2]
                    if channels:
                        channels = {k: v[::2, ::2] for k, v in channels.items()}

                nav = fit_limb_nav(meas, cm_iii_deg=truth["cm_iii_deg"], distance_au=truth["distance_au"])
                nav.cm_iii_deg = truth["cm_iii_deg"]
                nav.distance_au = truth["distance_au"]

                max_fid = bool(data.get("max_fidelity", True))
                factory_mode = bool(data.get("factory_mode", True))
                use_vlbi = bool(data.get("use_vlbi", True))
                aperture_m = float(data.get("aperture_m") or 0.35)
                inj_n = int(data.get("injection_trials") or (36 if factory_mode else (28 if max_fid else 16)))
                rg = run_research_grade(
                    meas, nav=nav, cm_iii_deg=nav.cm_iii_deg, distance_au=nav.distance_au,
                    channels=channels, injection_trials=inj_n, mc_iter=mc_iter_use,
                    seed=truth["seed"] % 10000, max_fidelity=max_fid, factory_mode=factory_mode,
                    user_time_iso=synth_time, time_error_seconds=float(truth.get("time_error_seconds") or 0),
                    aperture_m=aperture_m, use_vlbi=use_vlbi,
                )
                write_publication_bundle(out / "research_grade.json", rg, extra={
                    "truth": truth, "mode": "vlbi_optical" if use_vlbi and max_fid else "spire_m",
                })
                try:
                    vf = (rg.methods or {}).get("vlbi_full")
                    if isinstance(vf, dict):
                        (out / "vlbi_metrology.json").write_text(
                            json.dumps(vf, indent=2, default=str), encoding="utf-8"
                        )
                except Exception:
                    pass

                result["mode"] = "vlbi_optical" if use_vlbi and max_fid else "spire_m"
                result["synth_epoch"] = synth_time
                result["random_time"] = bool(truth.get("random_time", random_time))
                result["philosophy"] = (
                    "Professional procedure + synthetic truth test. "
                    "Known planted GRS = lab calibration, not NASA catalog."
                )
                result["headline"] = {
                    "source_kind": "SYNTHETIC (test image — not real data)",
                    "run_n": run_n,
                    "lon_iii_deg_bias_corrected": rg.lon_bias_corrected_deg,
                    "lat_deg_bias_corrected": rg.lat_bias_corrected_deg,
                    "truth_lon": truth["grs_lon_iii_deg"],
                    "truth_lat": truth["grs_lat_deg"],
                    "synth_epoch": synth_time,
                    "random_time": bool(truth.get("random_time", random_time)),
                    "sigma_total_sky_arcsec": rg.sigma_total_sky_arcsec,
                    "sigma_random_sky_arcsec": rg.sigma_random_sky_arcsec,
                    "sigma_systematic_sky_arcsec": rg.sigma_systematic_sky_arcsec,
                    "injection_mean_recovery_arcsec": rg.injection_mean_sky_arcsec,
                    "bias_lon_deg": rg.bias_lon_deg,
                    "bias_lat_deg": rg.bias_lat_deg,
                    "research_grade": rg.grade,
                    "cm_source": "synthetic_truth",
                    "metrology": "pro-procedure + VLBI-inspired optical" if use_vlbi and max_fid else "pro-procedure",
                }
                result["research_grade"] = rg.to_dict()
                # Gold-standard professional definitions (primary product)
                _run_gold(
                    result, meas, nav=nav,
                    cm_iii_deg=nav.cm_iii_deg, distance_au=nav.distance_au,
                    cm_source="synthetic_truth", user_time_iso=synth_time,
                    data=data, out=out, channels=channels,
                )
                # Finalize: champion → publish → WinJUPOS+ → SUPERDUPER → completeness
                # (parity with desktop Process — was previously missing on server synth)
                try:
                    from job_finalize import finalize_science_package
                    finalize_science_package(
                        result, meas,
                        nav=nav,
                        cm_iii_deg=nav.cm_iii_deg,
                        distance_au=nav.distance_au,
                        cm_source="synthetic_truth",
                        sigma_cm_deg=0.05,
                        sub_lat_deg=float(nav.sub_lat_deg or 0.0),
                        north_pa_deg=float(nav.north_pa_deg or 0.0),
                        channels=channels,
                        out_dir=out,
                        user_time_iso=synth_time,
                        time_error_seconds=float(truth.get("time_error_seconds") or 0),
                    )
                except Exception as finalize_err:
                    CONSOLE.warn(f"Synthetic finalize (parity): {finalize_err}")
                gs = result.get("gold_standard") or {}
                lon_use = float(gs.get("primary_lon_iii_deg") if gs.get("ok") else rg.lon_bias_corrected_deg)
                lat_use = float(gs.get("primary_lat_deg") if gs.get("ok") else rg.lat_bias_corrected_deg)
                dlon = wrap_diff(lon_use, truth["grs_lon_iii_deg"])
                dlat = lat_use - truth["grs_lat_deg"]
                sky = sky_error_arcsec(dlon, dlat, truth["grs_lat_deg"], truth["distance_au"])
                recovery = {
                    "dlon_deg": dlon,
                    "dlat_deg": dlat,
                    "sky_error_arcsec": sky,
                    "target_2_arcsec": sky <= 2.0,
                    "target_1_arcsec": sky <= 1.0,
                    "grade": "EXCELLENT" if sky <= 1 else ("GOOD" if sky <= 2 else ("FAIR" if sky <= 5 else "POOR")),
                    "note": (
                        "Gold-standard primary vs synthetic planted truth "
                        f"(definition={gs.get('primary_definition')}). Lab calibration only."
                    ),
                    "primary_definition": gs.get("primary_definition"),
                }
                result["truth_recovery"] = recovery
                result["headline"]["lon_iii_deg_bias_corrected"] = lon_use
                result["headline"]["lat_deg_bias_corrected"] = lat_use
                result["headline"]["truth_recovery_sky_arcsec"] = sky
                result["headline"]["truth_recovery_grade"] = recovery["grade"]
                result["headline"]["target_0_5_arcsec"] = sky <= 0.5
                result["headline"]["target_1_arcsec"] = sky <= 1.0 and rg.sigma_total_sky_arcsec <= 1.0
                result["headline"]["target_2_arcsec"] = sky <= 2.0 and rg.sigma_total_sky_arcsec <= 2.0
                mtag = metrics_filename_suffix(
                    lon=lon_use, lat=lat_use,
                    sigma=rg.sigma_total_sky_arcsec, grade=rg.grade, truth_sky=sky,
                )
                result["metrics_tag"] = mtag
                CONSOLE.ok(
                    f"HEADLINE run#{run_n:04d} SYNTH: GS={gs.get('primary_definition')}  "
                    f"lon={lon_use:.4f}°  Δsky={sky:.3f}\"  σ_tot={rg.sigma_total_sky_arcsec:.3f}\"  {rg.grade}"
                )

                if nasa:
                    # Geometry context only — synthetic accuracy is truth_recovery
                    comp = compare_measurement_to_nasa(
                        {
                            "lon_iii_deg": lon_use,
                            "lat_deg": lat_use,
                            "length_deg": truth["grs_length_deg"],
                            "width_deg": truth["grs_width_deg"],
                        },
                        synth_time,
                        float(truth.get("time_error_seconds") or 0),
                    )
                    write_comparison_report(out / "nasa_comparison.json", comp)
                    result["nasa"] = comp.to_dict()
                    result["nasa"]["grade"] = comp.grade()
                    result["nasa"]["role"] = "geometry_context_only"
                    result["nasa"]["note"] = (
                        "Geometry context only. For synthetic accuracy use truth_recovery "
                        "(planted GRS), not NASA/model."
                    )

            mtag = result.get("metrics_tag") or metrics_filename_suffix()
            result_name = f"job_result_run{run_n:04d}{mtag}.json"
            _attach_human_report(result, out, run_n)
            dump = {k: v for k, v in result.items() if k != "text"}
            (out / result_name).write_text(json.dumps(dump, indent=2, default=str))
            (out / "job_result.json").write_text(json.dumps(dump, indent=2, default=str))
            CONSOLE.ok(f"SYNTHETIC COMPLETE → {out.name} / {result_name}")
            free_memory()
            _finish(result)
        except Exception as e:
            CONSOLE.error(str(e))
            CONSOLE.debug(traceback.format_exc())
            _finish(error=str(e))

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"ok": True, "job_id": jid, "run_n": run_n, "output_dir": str(out)})


@app.route("/api/ephemeris", methods=["POST"])
def api_ephemeris():
    """Resolve professional Jupiter ephemeris (WinJUPOS / SPICE / Horizons / analytical)."""
    data = request.get_json(force=True, silent=True) or {}
    user_time = (data.get("user_time") or "").strip()
    if not user_time:
        return jsonify({
            "ok": False,
            "error": "Observation UTC required for ephemeris (refusing silent datetime.now())",
        }), 400
    try:
        pe = resolve_pro_ephemeris(
            user_time,
            time_error_seconds=float(data.get("time_error_seconds") or 0),
            cm_override=(float(data["cm_iii_override"]) if data.get("cm_iii_override") is not None else None),
            distance_override=(float(data["distance_au"]) if data.get("distance_au") is not None else None),
            sub_lat_override=(float(data["sub_lat_override"]) if data.get("sub_lat_override") is not None else None),
            north_pa_override=(float(data["north_pa_override"]) if data.get("north_pa_override") is not None else None),
            winjupos_path=data.get("winjupos_path"),
            use_horizons=bool(data.get("use_horizons", True)),
            use_spice=bool(data.get("use_spice", True)),
            force_horizons=bool(data.get("force_horizons", False)),
        )
        out = OUTPUT / f"eph_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        out.mkdir(exist_ok=True)
        write_ephemeris_report(out / "pro_ephemeris.json", pe)
        return jsonify({"ok": True, "ephemeris": pe.to_dict(), "output_dir": str(out)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 400


@app.route("/api/winjupos/template", methods=["GET"])
def winjupos_template():
    p = save_example_winjupos_template()
    return jsonify({"ok": True, "path": str(p), "dir": str(EPH_DIR)})


@app.route("/api/winjupos/upload", methods=["POST"])
def winjupos_upload():
    f = request.files.get("file")
    if not f or not f.filename:
        return jsonify({"ok": False, "error": "No file"}), 400
    dest = EPH_DIR / "winjupos_cm.csv"
    if Path(f.filename).suffix.lower() == ".json":
        dest = EPH_DIR / "winjupos_cm.json"
    f.save(dest)
    CONSOLE.ok(f"WinJUPOS CM table → {dest}")
    return jsonify({"ok": True, "path": str(dest)})


@app.route("/api/multi_epoch", methods=["POST"])
def api_multi_epoch():
    """
    Differential multi-epoch tracking (VLBI phase-ref across nights).

    Body:
      directory: scan outputs dir (default app/outputs)
      epochs: optional list of {path} or {t_utc_iso, lon_iii_deg, lat_deg, ...}
      ref_index: 0
    """
    data = request.get_json(force=True, silent=True) or {}
    try:
        jid, run_n, out = _start("multi")
    except RuntimeError as e:
        return jsonify({"ok": False, "error": str(e)}), 409

    def worker():
        try:
            if data.get("epochs"):
                epochs = load_epochs_from_list(data["epochs"])
            else:
                d = Path(data.get("directory") or str(OUTPUT))
                if _SEC:
                    d = safe_resolve_under(d, OUTPUT.resolve(), *data_roots(APP_DIR))
                epochs = load_epochs_from_dir(d)
            if len(epochs) < 1:
                raise RuntimeError("No epochs found — run process/synthetic first or pass epochs[]")
            series = build_differential_series(
                epochs,
                ref_index=int(data.get("ref_index") or 0),
                smooth=bool(data.get("smooth", True)),
            )
            write_multi_epoch_report(out / "multi_epoch.json", series, epochs)
            report = {
                "job_id": jid,
                "run_n": run_n,
                "kind": "multi_epoch",
                "source_kind": "multi_epoch",
                "n_epochs": len(epochs),
                "series": series.to_dict(),
                "output_dir": str(out),
                "output_folder": out.name,
                "headline": {
                    "run_n": run_n,
                    "drift_lon_deg_per_day": series.drift_lon_deg_per_day,
                    "drift_lon_sigma": series.drift_lon_sigma,
                    "rms_residual_sky_arcsec": series.rms_residual_sky_arcsec,
                    "n": len(epochs),
                },
            }
            _attach_human_report(report, out, run_n)
            dump = {k: v for k, v in report.items() if k != "text"}
            (out / f"job_result_run{run_n:04d}.json").write_text(json.dumps(dump, indent=2, default=str))
            (out / "job_result.json").write_text(json.dumps(dump, indent=2, default=str))
            CONSOLE.ok(f"MULTI-EPOCH COMPLETE run#{run_n:04d} → {out.name}")
            _finish(report)
        except Exception as e:
            CONSOLE.error(str(e))
            CONSOLE.debug(traceback.format_exc())
            _finish(error=str(e))

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"ok": True, "job_id": jid, "run_n": run_n})


@app.route("/api/hard_synth", methods=["POST"])
def api_hard_synth():
    """Run hard synthetic stress suite (mismatch physics calibration)."""
    data = request.get_json(force=True, silent=True) or {}
    try:
        jid, run_n, out = _start("hard")
    except RuntimeError as e:
        return jsonify({"ok": False, "error": str(e)}), 409

    def worker():
        try:
            report = run_hard_synth_suite(
                out,
                base_seed=int(data.get("seed") or 42),
                resolution=str(data.get("resolution") or "1080p"),
                injection_trials=int(data.get("injection_trials") or 8),
                mc_iter=int(data.get("mc_iterations") or 10),
                user_time_iso=str(
                    data.get("user_time")
                    or ""  # hard_synth may invent random epochs internally if empty
                ),
            )
            report["job_id"] = jid
            report["run_n"] = run_n
            report["kind"] = "hard_synth"
            report["source_kind"] = "synthetic"
            report["output_dir"] = str(out)
            report["output_folder"] = out.name
            _attach_human_report(report, out, run_n)
            dump = {k: v for k, v in report.items() if k != "text"}
            (out / f"job_result_run{run_n:04d}.json").write_text(json.dumps(dump, indent=2, default=str))
            (out / "job_result.json").write_text(json.dumps(dump, indent=2, default=str))
            CONSOLE.ok(f"HARD SYNTH COMPLETE run#{run_n:04d}: {report.get('calibration_grade')} → {out.name}")
            free_memory()
            _finish(report)
        except Exception as e:
            CONSOLE.error(str(e))
            CONSOLE.debug(traceback.format_exc())
            _finish(error=str(e))

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"ok": True, "job_id": jid, "run_n": run_n})


@app.route("/api/capabilities")
def capabilities():
    """Everything the advanced stack can do — for UI discovery."""
    spice_ok = False
    try:
        import spiceypy  # noqa: F401
        spice_ok = True
    except Exception:
        pass
    wj = list(EPH_DIR.glob("winjupos_cm.*"))
    return jsonify({
        "ok": True,
        "version": (lambda: __import__("product_core", fromlist=["PRODUCT_VERSION"]).PRODUCT_VERSION)(),
        "pillars": {
            "pro_ephemeris": True,
            "horizons_orientation": True,
            "winjupos": True,
            "spice": spice_ok,
            "vlbi_optical": True,
            "multi_epoch": True,
            "hard_synth": True,
            "factory_night": True,
            "spire_net": True,
            "synthetic_hq": True,
        },
        "winjupos_files": [str(p.name) for p in wj],
        "ephemeris_dir": str(EPH_DIR),
        "outputs_dir": str(OUTPUT),
        "endpoints": [
            "/api/process", "/api/synthetic", "/api/ephemeris",
            "/api/winjupos/upload", "/api/winjupos/template",
            "/api/multi_epoch", "/api/hard_synth", "/api/factory_night",
            "/api/nn/train", "/api/capabilities",
        ],
    })


@app.route("/api/factory_night", methods=["POST"])
def api_factory_night():
    """
    One-command Harvard-grade night:
      1) Pro ephemeris
      2) HQ synthetic (+ VLBI measure)  OR  process uploaded path
      3) Multi-epoch differential (scan outputs)
      4) Hard-synth stress suite
    """
    data = request.get_json(force=True, silent=True) or {}
    user_time = (data.get("user_time") or "").strip()
    if not user_time:
        return jsonify({
            "ok": False,
            "error": "Observation UTC required for factory night (no silent now)",
        }), 400
    if _SEC:
        user_time = strip_control_chars(user_time, 80)
    # Pre-validate optional process path (no arbitrary disk access)
    if data.get("path") and _SEC:
        try:
            data = dict(data)
            data["path"] = str(assert_safe_process_path(data["path"], *data_roots(APP_DIR)))
        except SecurityError as e:
            return jsonify({"ok": False, "error": str(e)}), 403
    if data.get("winjupos_path") and _SEC:
        try:
            data = dict(data)
            data["winjupos_path"] = str(
                safe_resolve_under(data["winjupos_path"], EPH_DIR.resolve(), *data_roots(APP_DIR), APP_DIR / "ephemeris_data")
            )
        except SecurityError as e:
            return jsonify({"ok": False, "error": f"winjupos_path: {e}"}), 403
    try:
        jid, run_n, out = _start("factory")
    except RuntimeError as e:
        return jsonify({"ok": False, "error": str(e)}), 409

    def worker():
        try:
            stages: Dict[str, Any] = {}
            CONSOLE.info("=" * 64)
            CONSOLE.info("FACTORY NIGHT — full optical metrology pipeline")
            CONSOLE.info(f"run=#{run_n:04d}  job={jid}  time={user_time}")
            CONSOLE.info(f"output → {out.name}")

            # --- 1) Pro ephemeris ---
            CONSOLE.info("[1/4] Professional ephemeris…")
            cm_ov = data.get("cm_iii_override")
            pe = resolve_pro_ephemeris(
                user_time,
                time_error_seconds=float(data.get("time_error_seconds") or 0),
                cm_override=(float(cm_ov) if cm_ov is not None else None),
                sub_lat_override=(float(data["sub_lat_override"]) if data.get("sub_lat_override") is not None else None),
                north_pa_override=(float(data["north_pa_override"]) if data.get("north_pa_override") is not None else None),
                winjupos_path=data.get("winjupos_path"),
                use_horizons=bool(data.get("use_horizons", True)),
                use_spice=bool(data.get("use_spice", True)),
            )
            write_ephemeris_report(out / "pro_ephemeris.json", pe)
            stages["ephemeris"] = pe.to_dict()
            CONSOLE.ok(f"[1/4] CM III={pe.cm_iii_deg:.4f}° ({pe.cm_source})")

            # --- 2) Measure: synthetic or process path ---
            path = data.get("path")
            res_preset = str(data.get("resolution_preset") or "1080p")
            mc_iter = cap_mc_iterations(int(data.get("mc_iterations") or 40), megapixels=8.0)
            inj_n = int(data.get("injection_trials") or 16)
            measure_block: Dict[str, Any] = {}

            if path and Path(path).exists():
                CONSOLE.info("[2/4] Process REAL uploaded image (FITS/PNG/JPG) with VLBI stack…")
                p = Path(path)
                meas, channels, _prev = _load_image(p)
                channels = dict(channels) if channels else None
                # Sharp preview of the real file (works for FITS and normal images)
                src_prev = out / f"source_preview_run{run_n:04d}.png"
                try:
                    if channels and all(k in channels for k in ("R", "G", "B")):
                        write_image_preview(
                            np.stack([channels["R"], channels["G"], channels["B"]], axis=0),
                            src_prev,
                        )
                    else:
                        write_image_preview(meas, src_prev)
                    CONSOLE.ok(f"[2/4] Real-file preview → {src_prev.name}")
                except Exception as e:
                    CONSOLE.warn(f"[2/4] preview: {e}")
                    src_prev = None
                rg = run_research_grade(
                    meas, cm_iii_deg=pe.cm_iii_deg, distance_au=pe.distance_au,
                    channels=channels, injection_trials=inj_n, mc_iter=mc_iter,
                    seed=int(data.get("seed") or 42), max_fidelity=True,
                    factory_mode=bool(data.get("factory_mode", True)),
                    user_time_iso=user_time,
                    time_error_seconds=float(data.get("time_error_seconds") or 0),
                    aperture_m=float(data.get("aperture_m") or 0.35),
                    use_vlbi=True,
                    winjupos_path=data.get("winjupos_path"),
                    sub_lat_override=(float(data["sub_lat_override"]) if data.get("sub_lat_override") is not None else None),
                    north_pa_override=(float(data["north_pa_override"]) if data.get("north_pa_override") is not None else None),
                )
                write_publication_bundle(out / "research_grade.json", rg, extra={"factory_night": True, "path": path, "source_kind": "real_file"})
                measure_block = {
                    "mode": "process",
                    "source_kind": "real_file",
                    "lon": rg.lon_bias_corrected_deg,
                    "lat": rg.lat_bias_corrected_deg,
                    "sigma_total_sky_arcsec": rg.sigma_total_sky_arcsec,
                    "grade": rg.grade,
                    "research_grade": rg.to_dict(),
                    "png": (f"/api/file?path={src_prev}" if src_prev and Path(src_prev).exists() else None),
                    "preview_label": "Your uploaded file (real)",
                }
                # Professional gold-standard definitions
                try:
                    gs_pkg: Dict[str, Any] = {"headline": {}}
                    _run_gold(
                        gs_pkg, meas, nav=None,
                        cm_iii_deg=pe.cm_iii_deg, distance_au=pe.distance_au,
                        cm_source=pe.cm_source, user_time_iso=user_time, data=data, out=out,
                        channels=channels,
                    )
                    measure_block["gold_standard"] = gs_pkg.get("gold_standard")
                    if (gs_pkg.get("gold_standard") or {}).get("ok"):
                        measure_block["lon"] = gs_pkg["gold_standard"]["primary_lon_iii_deg"]
                        measure_block["lat"] = gs_pkg["gold_standard"]["primary_lat_deg"]
                except Exception as e:
                    CONSOLE.warn(f"Factory gold: {e}")
            else:
                CONSOLE.info("[2/4] No upload → HQ SYNTHETIC + VLBI measure (random epoch)…")
                synth_dir = out / f"synth_run{run_n:04d}"
                png, fit, truth = generate(
                    SynthSpec(
                        user_time_iso="",
                        region=str(data.get("region") or "global"),
                        time_error_seconds=float(data.get("time_error_seconds") or 0),
                        resolution_preset=res_preset,
                        seed=int(data["seed"]) if data.get("seed") is not None else None,
                        random_time=True,  # synthetic never uses session time
                    ),
                    synth_dir,
                )
                user_time = truth.get("user_time_iso") or user_time  # measure at drawn epoch
                if str(fit).endswith((".fit", ".fits")):
                    arr, _ = grs.read_fits(fit)
                    img = np.asarray(arr, dtype=np.float64)
                    meas = img[0] if img.ndim == 3 else img
                    channels = {"R": img[0], "G": img[1], "B": img[2]} if img.ndim == 3 and img.shape[0] == 3 else None
                else:
                    from PIL import Image
                    rgb = np.asarray(Image.open(png).convert("RGB"), dtype=np.float64) / 255.0
                    meas = rgb[:, :, 0]
                    channels = {"R": rgb[:, :, 0], "G": rgb[:, :, 1], "B": rgb[:, :, 2]}
                # Use synthetic truth CM (image-tied)
                rg = run_research_grade(
                    meas, cm_iii_deg=truth["cm_iii_deg"], distance_au=truth["distance_au"],
                    channels=channels, injection_trials=inj_n, mc_iter=mc_iter,
                    seed=int(truth["seed"]) % 10000, max_fidelity=True,
                    factory_mode=bool(data.get("factory_mode", True)),
                    user_time_iso=user_time, use_vlbi=True,
                    aperture_m=float(data.get("aperture_m") or 0.35),
                )
                write_publication_bundle(out / "research_grade.json", rg, extra={"truth": truth, "factory_night": True})
                gs_pkg = {"headline": {}}
                _run_gold(
                    gs_pkg, meas, nav=None,
                    cm_iii_deg=truth["cm_iii_deg"], distance_au=truth["distance_au"],
                    cm_source="synthetic_truth", user_time_iso=user_time, data=data, out=out,
                    channels=channels,
                )
                gs = gs_pkg.get("gold_standard") or {}
                lon_use = float(gs["primary_lon_iii_deg"]) if gs.get("ok") else rg.lon_bias_corrected_deg
                lat_use = float(gs["primary_lat_deg"]) if gs.get("ok") else rg.lat_bias_corrected_deg
                dlon = wrap_diff(lon_use, truth["grs_lon_iii_deg"])
                dlat = lat_use - truth["grs_lat_deg"]
                sky = sky_error_arcsec(dlon, dlat, truth["grs_lat_deg"], truth["distance_au"])
                recovery = {
                    "dlon_deg": dlon, "dlat_deg": dlat, "sky_error_arcsec": sky,
                    "grade": "EXCELLENT" if sky <= 1 else ("GOOD" if sky <= 2 else "FAIR"),
                    "primary_definition": gs.get("primary_definition"),
                    "note": "Gold-standard primary vs planted synthetic truth (lab only).",
                }
                measure_block = {
                    "mode": "synthetic",
                    "source_kind": "synthetic",
                    "truth": truth,
                    "truth_recovery": recovery,
                    "lon": lon_use,
                    "lat": lat_use,
                    "sigma_total_sky_arcsec": rg.sigma_total_sky_arcsec,
                    "grade": rg.grade,
                    "png": f"/api/file?path={png}",
                    "preview_label": "SYNTHETIC (not a real photo)",
                    "research_grade": rg.to_dict(),
                    "gold_standard": gs,
                }
                CONSOLE.ok(f"[2/4] SYNTH GS={gs.get('primary_definition')} Δsky={sky:.4f}\"  grade={rg.grade}")

            stages["measure"] = measure_block
            vf = (rg.methods or {}).get("vlbi_full")
            if isinstance(vf, dict):
                (out / "vlbi_metrology.json").write_text(json.dumps(vf, indent=2, default=str))

            # --- 3) Multi-epoch ---
            CONSOLE.info("[3/4] Multi-epoch differential scan…")
            multi_block: Dict[str, Any] = {}
            try:
                epochs = load_epochs_from_dir(OUTPUT)
                if len(epochs) >= 2:
                    series = build_differential_series(epochs, ref_index=0, smooth=True)
                    write_multi_epoch_report(out / "multi_epoch.json", series, epochs)
                    multi_block = {
                        "n_epochs": len(epochs),
                        "drift_lon_deg_per_day": series.drift_lon_deg_per_day,
                        "drift_lon_sigma": series.drift_lon_sigma,
                        "rms_residual_sky_arcsec": series.rms_residual_sky_arcsec,
                        "smoother": series.smoother,
                        "series": series.to_dict(),
                    }
                    CONSOLE.ok(
                        f"[3/4] n={len(epochs)}  drift={series.drift_lon_deg_per_day:.4f}°/d  "
                        f"RMS={series.rms_residual_sky_arcsec:.4f}\""
                    )
                else:
                    multi_block = {
                        "n_epochs": len(epochs),
                        "note": "Need ≥2 measured epochs in outputs for differential tracking",
                    }
                    CONSOLE.warn(f"[3/4] Only {len(epochs)} epoch(s) — run more nights for drift")
            except Exception as e:
                multi_block = {"error": str(e)}
                CONSOLE.warn(f"[3/4] multi-epoch: {e}")
            stages["multi_epoch"] = multi_block

            # --- 4) Hard-synth suite ---
            hard_block: Dict[str, Any] = {}
            if bool(data.get("run_hard_synth", True)):
                CONSOLE.info("[4/4] Hard synthetic stress suite…")
                try:
                    hard = run_hard_synth_suite(
                        out / "hard_synth",
                        base_seed=int(data.get("seed") or 42),
                        resolution=str(data.get("hard_resolution") or "1080p"),
                        injection_trials=int(data.get("hard_injection_trials") or 6),
                        mc_iter=int(data.get("hard_mc_iterations") or 8),
                        user_time_iso=user_time,
                    )
                    hard_block = {
                        "calibration_grade": hard.get("calibration_grade"),
                        "overall": hard.get("overall"),
                        "by_family": hard.get("by_family"),
                        "n_cases": len(hard.get("results") or []),
                    }
                    CONSOLE.ok(f"[4/4] {hard.get('calibration_grade')}  med={((hard.get('overall') or {}).get('median_sky_arcsec'))}\"")
                except Exception as e:
                    hard_block = {"error": str(e)}
                    CONSOLE.warn(f"[4/4] hard-synth: {e}")
            else:
                hard_block = {"skipped": True}
                CONSOLE.info("[4/4] Hard-synth skipped by request")
            stages["hard_synth"] = hard_block

            # --- Headline ---
            tr = (measure_block.get("truth_recovery") or {})
            src_kind = measure_block.get("source_kind") or ("real_file" if path else "synthetic")
            mtag = metrics_filename_suffix(
                lon=measure_block.get("lon"),
                lat=measure_block.get("lat"),
                sigma=measure_block.get("sigma_total_sky_arcsec"),
                grade=measure_block.get("grade"),
                truth_sky=tr.get("sky_error_arcsec"),
            )
            headline = {
                "factory_night": True,
                "job_id": jid,
                "run_n": run_n,
                "source_kind": (
                    "REAL FILE (your upload)" if src_kind == "real_file"
                    else "SYNTHETIC (test image — not real data)"
                ),
                "cm_iii_deg": pe.cm_iii_deg,
                "cm_source": pe.cm_source,
                "measure_grade": measure_block.get("grade"),
                "lon_iii_deg": measure_block.get("lon"),
                "lat_deg": measure_block.get("lat"),
                "sigma_total_sky_arcsec": measure_block.get("sigma_total_sky_arcsec"),
                "truth_recovery_sky_arcsec": tr.get("sky_error_arcsec"),
                "truth_recovery_grade": tr.get("grade"),
                "multi_epoch_n": multi_block.get("n_epochs"),
                "drift_lon_deg_per_day": multi_block.get("drift_lon_deg_per_day"),
                "calibration_grade": hard_block.get("calibration_grade"),
                "hard_median_sky_arcsec": (hard_block.get("overall") or {}).get("median_sky_arcsec"),
                "hard_coverage_2sigma": (hard_block.get("overall") or {}).get("coverage_2sigma"),
            }
            report = {
                "job_id": jid,
                "run_n": run_n,
                "kind": "factory_night",
                "source_kind": src_kind,
                "headline": headline,
                "stages": stages,
                "output_dir": str(out),
                "output_folder": out.name,
                "png": measure_block.get("png"),
                "preview": measure_block.get("png"),
                "preview_label": measure_block.get("preview_label"),
            }
            report_name = f"factory_night_report_run{run_n:04d}{mtag}.json"
            # Pull research_grade / gold / nasa into top-level for the human report
            if measure_block.get("research_grade"):
                report["research_grade"] = measure_block["research_grade"]
            if measure_block.get("gold_standard"):
                report["gold_standard"] = measure_block["gold_standard"]
                headline["gold_primary_definition"] = measure_block["gold_standard"].get("primary_definition")
                headline["gold_procedure_grade"] = measure_block["gold_standard"].get("grade")
            if measure_block.get("truth_recovery"):
                report["truth_recovery"] = measure_block["truth_recovery"]
            if measure_block.get("truth"):
                report["truth"] = measure_block["truth"]
            report["philosophy"] = (
                "Professional procedure first. NASA/Horizons = geometry context only, "
                "not official GRS longitude truth."
            )
            # Geometry context only (not NASA GRS answer)
            try:
                if measure_block.get("lon") is not None:
                    comp = compare_measurement_to_nasa(
                        {
                            "lon_iii_deg": measure_block.get("lon"),
                            "lat_deg": measure_block.get("lat"),
                            "length_deg": (measure_block.get("research_grade") or {}).get("length_deg") or 0,
                            "width_deg": (measure_block.get("research_grade") or {}).get("width_deg") or 0,
                        },
                        user_time,
                        float(data.get("time_error_seconds") or 0),
                    )
                    write_comparison_report(out / "nasa_comparison.json", comp)
                    report["nasa"] = comp.to_dict()
                    report["nasa"]["grade"] = comp.grade()
                    report["nasa"]["role"] = "geometry_context_only"
                    report["nasa"]["disclaimer"] = (
                        "Not NASA GRS truth. Use gold_standard + optional WinJUPOS manual Δ."
                    )
            except Exception as e:
                CONSOLE.warn(f"Factory NASA geometry compare: {e}")

            _attach_human_report(report, out, run_n)
            dump = {k: v for k, v in report.items() if k != "text"}
            (out / report_name).write_text(json.dumps(dump, indent=2, default=str))
            (out / "factory_night_report.json").write_text(json.dumps(dump, indent=2, default=str))
            (out / "job_result.json").write_text(json.dumps(dump, indent=2, default=str))
            # Short summary still written for quick glance
            lines = [
                "FACTORY NIGHT — QUICK SUMMARY",
                "=" * 50,
                f"Run #: {run_n:04d}",
                f"Job: {jid}",
                f"Folder: {out.name}",
                f"Source: {headline['source_kind']}",
                f"CM III: {pe.cm_iii_deg:.5f}° [{pe.cm_source}]",
                f"YOUR lon/lat: {measure_block.get('lon')} / {measure_block.get('lat')}",
                f"σ_tot: {measure_block.get('sigma_total_sky_arcsec')}\"",
                f"Grade: {measure_block.get('grade')}",
                f"Truth recovery: {tr.get('sky_error_arcsec')}\"  ({tr.get('grade')})",
            ]
            if report.get("nasa"):
                nd = report["nasa"].get("deltas") or {}
                nm = report["nasa"].get("measured") or {}
                nr = report["nasa"].get("reference") or {}
                lines += [
                    "",
                    "NASA COMPARE",
                    f"  YOUR  lon={nm.get('lon_iii_deg')}  lat={nm.get('lat_deg')}",
                    f"  NASA  lon={nr.get('lon_iii_deg')}  lat={nr.get('lat_deg')}",
                    f"  Δ     lon={nd.get('lon_iii_deg')}  lat={nd.get('lat_deg')}",
                    f"  grade={report['nasa'].get('grade')}",
                ]
            lines += [
                f"Multi-epoch n={multi_block.get('n_epochs')}  drift={multi_block.get('drift_lon_deg_per_day')}",
                f"Hard suite: {hard_block.get('calibration_grade')}  med={headline.get('hard_median_sky_arcsec')}\"",
                f"2σ coverage: {headline.get('hard_coverage_2sigma')}",
                "",
                "See FULL_REPORT.txt for the complete human report (YOUR vs NASA tables, tips, dump).",
            ]
            (out / f"factory_night_report_run{run_n:04d}.txt").write_text("\n".join(lines), encoding="utf-8")
            (out / "factory_night_report.txt").write_text("\n".join(lines), encoding="utf-8")
            CONSOLE.ok("=" * 64)
            CONSOLE.ok(f"FACTORY NIGHT COMPLETE run#{run_n:04d} → {out}")
            free_memory()
            _finish(report)
        except Exception as e:
            CONSOLE.error(str(e))
            CONSOLE.debug(traceback.format_exc())
            _finish(error=str(e))

    threading.Thread(target=worker, daemon=True).start()
    return jsonify({"ok": True, "job_id": jid, "run_n": run_n, "output_dir": str(out)})


@app.route("/api/output/<job_id>/<path:filename>")
def output_file(job_id, filename):
    base = _find_output_dir(job_id)
    if base is not None:
        try:
            if _SEC:
                # block traversal in filename
                from security_hard import has_traversal, BLOCKED_BASENAMES
                if has_traversal(filename) or Path(filename).name.lower() in BLOCKED_BASENAMES:
                    return jsonify({"error": "forbidden"}), 403
                p = (base / Path(filename).name).resolve()
                p.relative_to(base.resolve())
            else:
                p = base / filename
            if p.exists() and p.is_file():
                return send_from_directory(base, p.name)
        except Exception:
            return jsonify({"error": "forbidden"}), 403
    return jsonify({"error": "missing"}), 404


@app.route("/api/file")
def file_api():
    """Serve only job outputs and uploads — never license / owner logs / models."""
    raw = request.args.get("path", "")
    if not raw:
        return jsonify({"error": "missing path"}), 400
    try:
        if _SEC:
            p = safe_resolve_under(raw, *data_roots(APP_DIR))
            if not p.is_file():
                return jsonify({"error": "missing"}), 404
            return send_from_directory(p.parent, p.name)
        # fallback without security module
        p = Path(raw).resolve()
        for root in data_roots(APP_DIR):
            try:
                p.relative_to(root)
                if p.is_file():
                    return send_from_directory(p.parent, p.name)
            except ValueError:
                continue
        return jsonify({"error": "forbidden"}), 403
    except SecurityError as e:
        return jsonify({"error": str(e)}), 403
    except Exception:
        return jsonify({"error": "forbidden"}), 403


def main():
    host = os.environ.get("GRS_HOST", "127.0.0.1")
    port = int(os.environ.get("GRS_PORT", "8765"))
    try:
        from product_core import PRODUCT_VERSION
        ver = PRODUCT_VERSION
    except Exception:
        ver = "6.5.0"
    CONSOLE.clear()
    CONSOLE.ok(f"GRS Observatory v{ver} — optical GRS metrology")
    CONSOLE.info(f"http://{host}:{port}  |  16GB RAM  |  SSD: app/ssd_cache")
    CONSOLE.info("Pillars: pro-eph · VLBI · multi-epoch · hard-synth · FACTORY NIGHT")
    CONSOLE.info("UI: set TIME → Factory Night (or Process / Synthetic / tools)")
    if host not in ("127.0.0.1", "localhost", "::1"):
        CONSOLE.warn("Non-localhost bind — do not expose without auth; path APIs are local-trust.")
    app.run(host=host, port=port, debug=False, threaded=True, use_reloader=False)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
Shared advanced processing for the desktop app.
Runs the full research-oriented stack and writes a complete job package.
"""
from __future__ import annotations

import json
import traceback
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import numpy as np

from verbose_log import CONSOLE
from precision_engine import (
    fit_limb_nav,
    wrap_diff,
    sky_error_arcsec,
    cap_mc_iterations,
    measure_grs_precision,
)
from research_grade import run_research_grade, write_publication_bundle
from synthetic_hq import SynthSpec, generate
from ephemeris_pro import resolve_pro_ephemeris, write_ephemeris_report
from nasa_compare import compare_measurement_to_nasa, write_comparison_report
from multi_epoch import load_epochs_from_dir, build_differential_series, write_multi_epoch_report
from hard_synth_suite import run_hard_synth_suite
import grs_complete_system as grs


def array_to_rgb_u8(arr: np.ndarray, max_side: int = 2048) -> np.ndarray:
    """Convert mono/CHW/HWC float arrays to sharp RGB uint8 for web/desktop preview."""
    a = np.asarray(arr, dtype=np.float64)
    if a.ndim == 3 and a.shape[0] in (3, 4) and a.shape[0] < a.shape[-1]:
        # CHW → HWC
        a = np.moveaxis(a[:3], 0, -1)
    elif a.ndim == 3 and a.shape[-1] >= 3:
        a = a[..., :3]
    elif a.ndim == 2:
        a = np.stack([a, a, a], axis=-1)
    else:
        a = np.squeeze(a)
        if a.ndim == 2:
            a = np.stack([a, a, a], axis=-1)
        elif a.ndim == 3 and a.shape[0] in (3, 4):
            a = np.moveaxis(a[:3], 0, -1)
        else:
            raise ValueError(f"Unsupported image shape for preview: {arr.shape}")

    h, w = a.shape[:2]
    if max(h, w) > max_side:
        scale = max_side / float(max(h, w))
        nh, nw = max(1, int(round(h * scale))), max(1, int(round(w * scale)))
        try:
            from PIL import Image
            u = a.astype(np.float32)
            # temporary linear stretch before resize so LANCZOS has good range
            lo, hi = np.percentile(u, (0.5, 99.5))
            if hi <= lo:
                lo, hi = float(u.min()), float(u.max()) + 1e-9
            u = np.clip((u - lo) / (hi - lo + 1e-12), 0, 1)
            im = Image.fromarray((u * 255).astype(np.uint8), "RGB")
            im = im.resize((nw, nh), Image.Resampling.LANCZOS)
            return np.asarray(im, dtype=np.uint8)
        except Exception:
            ys = max(1, h // nh)
            xs = max(1, w // nw)
            a = a[::ys, ::xs][:nh, :nw]

    # Per-channel percentile stretch (preserves colour balance of RGB FITS)
    out = np.empty(a.shape, dtype=np.uint8)
    for c in range(3):
        ch = a[..., c]
        lo, hi = np.percentile(ch, (0.5, 99.5))
        if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
            lo, hi = float(np.nanmin(ch)), float(np.nanmax(ch)) + 1e-9
        scaled = np.clip((ch - lo) / (hi - lo + 1e-12), 0.0, 1.0)
        out[..., c] = (scaled * 255.0).astype(np.uint8)
    return out


def write_image_preview(
    arr_or_path: Any,
    dest: Path,
    *,
    max_side: int = 2048,
) -> Path:
    """
    Write a sharp browser-ready PNG preview of a FITS/array/image path.
    Prefer this over pipeline lrgb products which can look soft or missing.
    """
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(arr_or_path, (str, Path)):
        p = Path(arr_or_path)
        ext = p.suffix.lower()
        if ext in (".png", ".jpg", ".jpeg", ".webp"):
            from PIL import Image
            im = Image.open(p).convert("RGB")
            w, h = im.size
            if max(w, h) > max_side:
                im.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
            im.save(dest, format="PNG", optimize=False)
            return dest
        meas, channels, _ = _load_image(p)
        if channels and all(k in channels for k in ("R", "G", "B")):
            arr = np.stack([channels["R"], channels["G"], channels["B"]], axis=0)
        else:
            arr = meas
    else:
        arr = arr_or_path
    u8 = array_to_rgb_u8(arr, max_side=max_side)
    from PIL import Image
    Image.fromarray(u8, "RGB").save(dest, format="PNG", optimize=False)
    return dest


def next_run_id(out_root: Path, kind: str) -> Tuple[int, str, str]:
    """
    Allocate a sequential run number + detailed job slug.
    Returns (run_n, short_hex, folder_name) e.g. (42, 'a1b2c3d4e5f6', 'job_run0042_20260715T163045_a1b2c3d4e5f6')
    """
    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    counter = out_root / "run_counter.txt"
    n = 0
    try:
        if counter.exists():
            n = int(counter.read_text(encoding="utf-8").strip() or "0")
    except Exception:
        n = 0
    n += 1
    counter.write_text(str(n), encoding="utf-8")
    short = uuid.uuid4().hex[:12]
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    folder = f"{kind}_run{n:04d}_{stamp}_{short}"
    return n, short, folder


def metrics_filename_suffix(
    *,
    lon: Optional[float] = None,
    lat: Optional[float] = None,
    sigma: Optional[float] = None,
    grade: Optional[str] = None,
    truth_sky: Optional[float] = None,
) -> str:
    """Build a human-readable metric tag for output file names."""
    parts = []
    if lon is not None and np.isfinite(lon):
        parts.append(f"lon{lon:.4f}".replace("-", "m").replace(".", "p"))
    if lat is not None and np.isfinite(lat):
        parts.append(f"lat{lat:.4f}".replace("-", "m").replace(".", "p"))
    if sigma is not None and np.isfinite(sigma):
        parts.append(f"sig{sigma:.4f}".replace(".", "p"))
    if truth_sky is not None and np.isfinite(truth_sky):
        parts.append(f"truth{truth_sky:.4f}".replace(".", "p"))
    if grade:
        parts.append(str(grade).replace(" ", "_")[:24])
    return ("_" + "_".join(parts)) if parts else ""


def _load_image(path: Path) -> Tuple[np.ndarray, Optional[Dict[str, np.ndarray]], Optional[Path]]:
    """Return mono-or-CHW array, optional RGB channels, optional preview png path."""
    path = Path(path)
    preview = None
    if path.suffix.lower() in (".fit", ".fits", ".fts"):
        arr, _ = grs.read_fits(path)
        img = np.asarray(arr, dtype=np.float64)
    elif path.suffix.lower() == ".ser":
        cube = grs.read_ser(path)
        img = np.asarray(cube.frames[0], dtype=np.float64) if hasattr(cube, "frames") else np.asarray(cube, dtype=np.float64)
    else:
        from PIL import Image
        rgb = np.asarray(Image.open(path).convert("RGB"), dtype=np.float64) / 255.0
        img = np.moveaxis(rgb, 2, 0)
        preview = path

    channels = None
    if img.ndim == 3 and img.shape[0] in (3, 4):
        channels = {"R": img[0], "G": img[1], "B": img[2]}
        meas = img[0]  # R — JUPOS preferred for GRS
    elif img.ndim == 3 and img.shape[-1] >= 3:
        channels = {"R": img[..., 0], "G": img[..., 1], "B": img[..., 2]}
        try:
            from accuracy_gates import prefer_red_channel
            meas = prefer_red_channel(img)
        except Exception:
            meas = img[..., 0]
    else:
        meas = img
    return meas, channels, preview


def _try_imaging_pipeline(path: Path, out: Path, channels: Optional[Dict], meas: np.ndarray):
    """Run grs full imaging branch when possible (lucky-ish path for stacks)."""
    try:
        cfg = grs.replace(
            grs.PRESET_FAST_PREVIEW,
            mode="imaging",
            out_dir=str(out),
            work_dir=str(out / "work"),
            min_frames=1,
            max_clip_frac=1.0,
            derot_enable=True,
        )
        pipe = grs.GRSCompletePipeline(cfg)
        pipe.process_path(str(path), "RGB")
        pipe.build_channels()
        pipe.run_imaging()
        if pipe.channels:
            channels = dict(pipe.channels)
            if "R" in channels:
                meas = channels["R"]
            CONSOLE.ok("Imaging pipeline branch applied (channels/stack)")
        return meas, channels, pipe
    except Exception as e:
        CONSOLE.warn(f"Imaging branch soft-fail (continuing with raw frame): {e}")
        return meas, channels, None


def format_full_report(package: Dict[str, Any]) -> str:
    """Human-readable full report: YOUR vs NASA, differences, tips, complete dump."""
    from result_report import format_human_report
    return format_human_report(package)


def write_package_reports(out: Path, package: Dict[str, Any]) -> Dict[str, Any]:
    """Attach human text + write FULL_REPORT.txt / job_result.json next to outputs."""
    text = format_full_report(package)
    package["text"] = text
    try:
        out = Path(out)
        (out / "FULL_REPORT.txt").write_text(text, encoding="utf-8")
        rn = package.get("run_n")
        if rn is not None:
            (out / f"FULL_REPORT_run{int(rn):04d}.txt").write_text(text, encoding="utf-8")
        CONSOLE.ok(f"FULL_REPORT.txt written ({len(text.splitlines())} lines)")
    except Exception as e:
        CONSOLE.warn(f"FULL_REPORT write: {e}")
    dump = {k: v for k, v in package.items() if k != "text"}
    (Path(out) / "job_result.json").write_text(json.dumps(dump, indent=2, default=str), encoding="utf-8")
    return package


def run_synthetic_full(
    out_root: Path,
    *,
    region: str = "global",
    resolution: str = "4K",
    mc_iter: int = 60,
    injection_trials: int = 28,
    factory_mode: bool = True,
    use_vlbi: bool = True,
    use_nn: bool = True,
    nasa: bool = True,
    aperture_m: float = 0.35,
    process_after: bool = True,
    mode: str = "metrology",
    seed: Optional[int] = None,
    human_choice: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Generate random-epoch synthetic + full VLBI measure + complete package.

    human_choice: optional dual auto+human pass (WinJUPOS-style definition/limb).
    """
    import os

    jid = uuid.uuid4().hex[:12]
    out = Path(out_root) / f"synth_{jid}"
    out.mkdir(parents=True, exist_ok=True)
    # Honor explicit seed, then GRS_SYNTH_SEED (set by product_core / CLI)
    if seed is None:
        env_seed = os.environ.get("GRS_SYNTH_SEED", "").strip()
        if env_seed:
            try:
                seed = int(env_seed)
            except ValueError:
                seed = None
    synth_mode = (mode or "metrology").strip().lower() or "metrology"
    CONSOLE.info("=" * 60)
    CONSOLE.info(
        f"DESKTOP SYNTHETIC FULL  res={resolution}  region={region}  "
        f"mode={synth_mode}  seed={seed}"
    )
    CONSOLE.info("Epoch: RANDOM (always)")

    png, fit, truth = generate(
        SynthSpec(
            user_time_iso="",
            region=region,
            resolution_preset=resolution,
            random_time=True,
            seed=seed,
            mode=synth_mode,
            write_grs_crop=True,
        ),
        out,
    )
    package: Dict[str, Any] = {
        "job_id": jid,
        "mode": "synthetic_full",
        "truth": truth,
        "png": str(png),
        "fit": str(fit),
        "output_dir": str(out),
        "random_time": True,
        "synth_epoch": truth.get("user_time_iso"),
    }

    if not process_after:
        package["headline"] = {
            "mode": "synthetic_generate_only",
            "synth_epoch": truth.get("user_time_iso"),
            "random_time": True,
            "resolution": truth.get("resolution"),
            "width": truth.get("width"),
            "height": truth.get("height"),
            "output_dir": str(out),
        }
        write_package_reports(out, package)
        package["preview"] = str(png)
        return package

    mp = truth["width"] * truth["height"] / 1e6
    mc = cap_mc_iterations(mc_iter, megapixels=mp)
    arr, _ = grs.read_fits(fit)
    img = np.asarray(arr, dtype=np.float64)
    # JUPOS: prefer red / visual-red for GRS contrast (blue often weaker)
    try:
        from accuracy_gates import prefer_red_channel
    except Exception:
        prefer_red_channel = None  # type: ignore
    if img.ndim == 3 and img.shape[0] == 3:
        channels = {"R": img[0], "G": img[1], "B": img[2]}
        meas = img[0]
    elif img.ndim == 3 and img.shape[-1] >= 3:
        channels = {"R": img[..., 0], "G": img[..., 1], "B": img[..., 2]}
        meas = prefer_red_channel(img) if prefer_red_channel else img[..., 0]
    else:
        channels = None
        meas = prefer_red_channel(img) if prefer_red_channel else img

    # optional half-scale for huge maps (16K) like server
    if meas.size > 25_000_000:
        CONSOLE.info("Half-scale for metrology (full PNG kept)")
        meas = meas[::2, ::2]
        if channels:
            channels = {k: v[::2, ::2] for k, v in channels.items()}

    measure_grs_precision._use_nn = use_nn
    nav = fit_limb_nav(meas, cm_iii_deg=truth["cm_iii_deg"], distance_au=truth["distance_au"])
    nav.cm_iii_deg = truth["cm_iii_deg"]
    nav.distance_au = truth["distance_au"]
    # Match real-process geometry contract: apply orientation when truth provides it
    # (synth currently renders D_E=0/PA=0 by default; still set fields for future oriented synth)
    try:
        nav.sub_lat_deg = float(truth.get("sub_obs_lat_deg") or truth.get("sub_lat_deg") or 0.0)
        nav.north_pa_deg = float(truth.get("north_pa_deg") or 0.0)
    except Exception:
        nav.sub_lat_deg = 0.0
        nav.north_pa_deg = 0.0

    rg = run_research_grade(
        meas,
        nav=nav,
        cm_iii_deg=nav.cm_iii_deg,
        distance_au=nav.distance_au,
        channels=channels,
        injection_trials=max(injection_trials, 24 if factory_mode else 16),
        mc_iter=mc,
        seed=int(truth["seed"]) % 10000,
        max_fidelity=True,
        factory_mode=factory_mode,
        user_time_iso=truth["user_time_iso"],
        time_error_seconds=0.0,
        aperture_m=aperture_m,
        use_vlbi=use_vlbi,
    )
    write_publication_bundle(out / "research_grade.json", rg, extra={"truth": truth, "desktop": True})

    dlon = wrap_diff(rg.lon_bias_corrected_deg, truth["grs_lon_iii_deg"])
    dlat = rg.lat_bias_corrected_deg - truth["grs_lat_deg"]
    sky = sky_error_arcsec(dlon, dlat, truth["grs_lat_deg"], truth["distance_au"])
    recovery = {
        "dlon_deg": dlon,
        "dlat_deg": dlat,
        "sky_error_arcsec": sky,
        "target_0_5_arcsec": sky <= 0.5,
        "target_1_arcsec": sky <= 1.0,
        "target_2_arcsec": sky <= 2.0,
        "grade": "EXCELLENT" if sky <= 1 else ("GOOD" if sky <= 2 else ("FAIR" if sky <= 5 else "POOR")),
    }

    methods = rg.methods or {}
    eb = methods.get("error_budget") or {}
    vf = methods.get("vlbi_full") or {}
    if isinstance(vf, dict):
        (out / "vlbi_metrology.json").write_text(json.dumps(vf, indent=2, default=str))

    nasa_rep = None
    if nasa:
        try:
            comp = compare_measurement_to_nasa(
                {
                    "lon_iii_deg": truth["grs_lon_iii_deg"],
                    "lat_deg": truth["grs_lat_deg"],
                    "length_deg": truth["grs_length_deg"],
                    "width_deg": truth["grs_width_deg"],
                },
                truth["user_time_iso"],
                0.0,
            )
            write_comparison_report(out / "nasa_comparison.json", comp)
            nasa_rep = comp.to_dict()
            nasa_rep["grade"] = comp.grade()
        except Exception as e:
            CONSOLE.warn(f"NASA compare: {e}")

    package["research_grade"] = rg.to_dict()
    package["truth_recovery"] = recovery
    package["error_budget"] = eb
    package["nasa"] = nasa_rep
    package["headline"] = {
        "mode": "synthetic_full_vlbi",
        "grade": rg.grade,
        "synth_epoch": truth["user_time_iso"],
        "random_time": True,
        "resolution": truth.get("resolution"),
        "width": truth.get("width"),
        "height": truth.get("height"),
        "lon_iii_deg": rg.lon_bias_corrected_deg,
        "lat_deg": rg.lat_bias_corrected_deg,
        "lat_planetographic_deg": vf.get("lat_planetographic_deg") if isinstance(vf, dict) else None,
        "length_deg": rg.length_deg,
        "width_deg": rg.width_deg,
        "sigma_total_sky_arcsec": rg.sigma_total_sky_arcsec,
        "sigma_random_sky_arcsec": rg.sigma_random_sky_arcsec,
        "sigma_systematic_sky_arcsec": rg.sigma_systematic_sky_arcsec,
        # Canonical + alias keys so CLI / certify / UI all find sky error
        "truth_recovery_sky_arcsec": sky,
        "sky_error_arcsec": sky,
        "oracle_sky_error_arcsec": recovery.get("oracle_sky_error_arcsec"),
        "truth_recovery_grade": recovery["grade"],
        "truth_lon": truth["grs_lon_iii_deg"],
        "truth_lat": truth["grs_lat_deg"],
        "dlon_deg": dlon,
        "dlat_deg": dlat,
        "cm_iii_deg": truth["cm_iii_deg"],
        "distance_au": truth["distance_au"],
        "injection_n": rg.injection_n,
        "definition_n": rg.definition_n,
        "filter_closure_arcsec": rg.filter_closure_arcsec,
        "bias_lon_deg": rg.bias_lon_deg,
        "bias_lat_deg": rg.bias_lat_deg,
        "optical_floor_arcsec": methods.get("optical_floor_arcsec") or (vf.get("optical_floor_arcsec") if isinstance(vf, dict) else None),
        "primary_method": methods.get("primary_method") or (vf.get("primary_method") if isinstance(vf, dict) else "vlbi"),
        "synth_mode": synth_mode,
        "synth_seed": truth.get("seed"),
        "cm_source": "synthetic_truth",  # image-tied CM; SPICE used for distance when available
        "time_error_seconds": 0.0,
        "output_dir": str(out),
    }
    try:
        from gold_standard import attach_gold_to_package
        attach_gold_to_package(
            package,
            meas,
            nav=nav,
            cm_iii_deg=truth["cm_iii_deg"],
            distance_au=truth["distance_au"],
            cm_source="synthetic_truth",
            user_time_iso=truth.get("user_time_iso") or "",
            out_dir=out,
        )
    except Exception as e:
        CONSOLE.warn(f"Gold standard: {e}")
    try:
        from winjupos_twin import attach_winjupos_twin_to_package
        attach_winjupos_twin_to_package(
            package,
            meas,
            nav=nav,
            cm_iii_deg=float(truth["cm_iii_deg"]),
            distance_au=float(truth["distance_au"]),
            cm_source="synthetic_truth",
            sub_lat_deg=float(getattr(nav, "sub_lat_deg", 0.0) or 0.0),
            north_pa_deg=float(getattr(nav, "north_pa_deg", 0.0) or 0.0),
            user_time_iso=truth.get("user_time_iso") or "",
            out_dir=out,
            run_limb_sensitivity=True,
        )
    except Exception as e:
        CONSOLE.warn(f"WinJUPOS twin: {e}")
    try:
        from job_finalize import finalize_science_package
        finalize_science_package(
            package,
            meas,
            nav=nav,
            cm_iii_deg=float(truth["cm_iii_deg"]),
            distance_au=float(truth["distance_au"]),
            cm_source="synthetic_truth",
            sigma_cm_deg=0.02,
            sub_lat_deg=float(getattr(nav, "sub_lat_deg", 0.0) or 0.0),
            north_pa_deg=float(getattr(nav, "north_pa_deg", 0.0) or 0.0),
            channels=channels,
            out_dir=out,
            user_time_iso=str(truth.get("user_time_iso") or ""),
        )
    except Exception as e:
        CONSOLE.warn(f"Finalize (synth pre-truth): {e}")
    # Score truth recovery against the *published* answer (official product output)
    # while retaining pipeline recovery for diagnostics.
    package["truth_recovery_pipeline"] = dict(recovery)
    pub = package.get("publish") or {}
    pub_lon = pub.get("publish_lon_iii_deg")
    pub_lat = pub.get("publish_lat_deg")
    if pub_lon is not None and truth.get("grs_lon_iii_deg") is not None:
        try:
            dlon_p = wrap_diff(float(pub_lon), float(truth["grs_lon_iii_deg"]))
            dlat_p = (
                float(pub_lat) - float(truth["grs_lat_deg"])
                if pub_lat is not None and truth.get("grs_lat_deg") is not None
                else 0.0
            )
            sky_p = sky_error_arcsec(
                dlon_p, dlat_p, float(truth["grs_lat_deg"]), float(truth["distance_au"])
            )
            recovery_pub = {
                "dlon_deg": dlon_p,
                "dlat_deg": dlat_p,
                "sky_error_arcsec": sky_p,
                "target_0_5_arcsec": sky_p <= 0.5,
                "target_1_arcsec": sky_p <= 1.0,
                "target_2_arcsec": sky_p <= 2.0,
                "grade": (
                    "EXCELLENT" if sky_p <= 1
                    else ("GOOD" if sky_p <= 2 else ("FAIR" if sky_p <= 5 else "POOR"))
                ),
                "vs": "publish",
                "publish_definition": pub.get("publish_definition"),
                "pipeline_sky_error_arcsec": recovery.get("sky_error_arcsec"),
            }
            package["truth_recovery"] = recovery_pub
            sky = sky_p
            h = package.get("headline") or {}
            h["truth_recovery_sky_arcsec"] = sky_p
            h["sky_error_arcsec"] = sky_p
            h["truth_recovery_grade"] = recovery_pub["grade"]
            h["dlon_deg"] = dlon_p
            h["dlat_deg"] = dlat_p
            h["pipeline_sky_error_arcsec"] = recovery.get("sky_error_arcsec")
            package["headline"] = h
        except Exception as e:
            CONSOLE.warn(f"Publish truth recovery: {e}")

    package = apply_dual_human_pass(
        package,
        meas=meas,
        nav=nav,
        cm_iii_deg=float(truth["cm_iii_deg"]),
        distance_au=float(truth["distance_au"]),
        cm_source="synthetic_truth",
        user_time_iso=str(truth.get("user_time_iso") or ""),
        out_dir=out,
        human_choice=human_choice,
        channels=channels,
        light_remeasure=True,
    )
    # Re-score truth vs official publish after dual (if human became official)
    if package.get("dual_measure") and package["dual_measure"].get("official") == "human":
        try:
            pub = package.get("publish") or {}
            plon = pub.get("publish_lon_iii_deg")
            plat = pub.get("publish_lat_deg")
            if plon is not None and truth.get("grs_lon_iii_deg") is not None:
                dlon_p = wrap_diff(float(plon), float(truth["grs_lon_iii_deg"]))
                dlat_p = (
                    float(plat) - float(truth["grs_lat_deg"])
                    if plat is not None else 0.0
                )
                sky_p = sky_error_arcsec(
                    dlon_p, dlat_p, float(truth["grs_lat_deg"]), float(truth["distance_au"])
                )
                package["truth_recovery"] = {
                    **(package.get("truth_recovery") or {}),
                    "dlon_deg": dlon_p,
                    "dlat_deg": dlat_p,
                    "sky_error_arcsec": sky_p,
                    "vs": "publish_after_dual",
                    "grade": (
                        "EXCELLENT" if sky_p <= 1
                        else ("GOOD" if sky_p <= 2 else ("FAIR" if sky_p <= 5 else "POOR"))
                    ),
                }
                h = package.setdefault("headline", {})
                h["sky_error_arcsec"] = sky_p
                h["truth_recovery_sky_arcsec"] = sky_p
                sky = sky_p
        except Exception as e:
            CONSOLE.warn(f"Dual truth recovery: {e}")

    # Re-card SUPERDUPER after dual / truth re-score
    try:
        from superduper import attach_superduper
        from job_finalize import write_job_completeness
        attach_superduper(package, out_dir=out)
        write_job_completeness(package, out)
    except Exception as e:
        CONSOLE.warn(f"SUPERDUPER (synth final): {e}")

    write_package_reports(out, package)
    package["preview"] = str(png)
    pub = package.get("publish") or {}
    CONSOLE.ok(
        f"SYNTH DONE epoch={truth['user_time_iso']}  Δsky={sky:.4f}\"  "
        f"publish={pub.get('publish_lon_iii_deg')}  "
        f"σ={rg.sigma_total_sky_arcsec:.4f}\"  {pub.get('publish_definition') or rg.grade}"
    )
    return package


def run_process_full(
    path: Path,
    out_root: Path,
    *,
    user_time: str,
    time_error: float = 0.0,
    mc_iter: int = 80,
    injection_trials: int = 32,
    factory_mode: bool = True,
    use_vlbi: bool = True,
    use_nn: bool = True,
    nasa: bool = True,
    aperture_m: float = 0.35,
    cm_override: Optional[float] = None,
    sub_lat_override: Optional[float] = None,
    north_pa_override: Optional[float] = None,
    winjupos_path: Optional[str] = None,
    use_horizons: bool = True,
    use_spice: bool = True,
    run_imaging: bool = True,
    winjupos_manual_lon: Optional[float] = None,
    winjupos_manual_lat: Optional[float] = None,
    human_choice: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Process real image with every advanced stage available.

    human_choice: optional WinJUPOS-style dual pass (definition + limb fine-tune).
    When set, runs automatic publish then a human pass; see dual_measure.json.
    """
    jid = uuid.uuid4().hex[:12]
    out = Path(out_root) / f"job_{jid}"
    out.mkdir(parents=True, exist_ok=True)
    path = Path(path)
    CONSOLE.info("=" * 60)
    CONSOLE.info("DESKTOP PROCESS FULL — all advanced stages")
    CONSOLE.info(f"file={path.name}  time={user_time}")

    meas, channels, preview = _load_image(path)
    # Resolve mid-exposure UTC: FITS header / filename beat stale UI defaults.
    # AutoStakkert often has empty DATE-OBS but time in the name (2026-01-09-1540_…).
    from fits_time import require_observation_time, format_utc, extract_fits_mid_time, _parse_isoish
    try:
        ut = (user_time or "").strip() or None
        dt_file, note_file = extract_fits_mid_time(path)
        if dt_file is None and path is not None:
            try:
                from grs_image_prep import parse_time_from_filename
                dt_file, note_file = parse_time_from_filename(path)
            except Exception:
                dt_file, note_file = None, ""
        # Always prefer file/filename when present and user time disagrees by >2 min
        # or is a different calendar day (common UI leftover from a previous job).
        if dt_file is not None:
            use_file = True
            if ut:
                ut_dt = _parse_isoish(ut)
                if ut_dt is not None:
                    same = (
                        ut_dt.date() == dt_file.date()
                        and abs((ut_dt - dt_file).total_seconds()) <= 120
                    )
                    if same:
                        use_file = False  # user matches file — keep user (fine)
                    else:
                        CONSOLE.warn(
                            f"UI time {ut!r} ≠ file time {format_utc(dt_file)} ({note_file}). "
                            "Using FILE time for System III (UI was ignored)."
                        )
            if use_file:
                ut = None  # force require_observation_time → header/filename
        t_obs, t_src = require_observation_time(
            user_time=ut,
            fits_path=path,
        )
        user_time = format_utc(t_obs)
        CONSOLE.info(f"Observation UTC from {t_src} → {user_time}")
    except ValueError as e:
        raise ValueError(str(e)) from e

    # Moon mask + orange GRS as dark + auto N–S flip (common AutoStakkert pitfall)
    # Always write a sharp PNG of the source (FITS included) so preview is the real photo
    try:
        src_prev = out / "source_preview.png"
        if channels and all(k in channels for k in ("R", "G", "B")):
            write_image_preview(
                np.stack([channels["R"], channels["G"], channels["B"]], axis=0),
                src_prev,
            )
        else:
            write_image_preview(meas, src_prev)
        preview = src_prev
        CONSOLE.ok(f"Source preview → {src_prev.name}")
    except Exception as e:
        CONSOLE.warn(f"Source preview failed: {e}")
    pipe = None
    if run_imaging:
        meas, channels, pipe = _try_imaging_pipeline(path, out, channels, meas)

    # AFTER imaging: moon mask + orange GRS darken + auto N–S flip
    # (must be after imaging so Autostakkert reload cannot undo orientation)
    prep_meta: Dict[str, Any] = {}
    try:
        from grs_image_prep import prepare_grs_measure_image
        rgb_src = None
        if channels and all(k in channels for k in ("R", "G", "B")):
            rgb_src = np.stack([channels["R"], channels["G"], channels["B"]], axis=-1)
        else:
            rgb_src = meas
        meas, channels, prep_meta = prepare_grs_measure_image(
            rgb_src, channels=channels, auto_flip_ns=True
        )
        CONSOLE.ok(
            f"GRS image prep: flip_ns={prep_meta.get('auto_flip_ns')} "
            f"moon_px={prep_meta.get('moon_pixels')} {prep_meta.get('prep')}"
        )
        if prep_meta.get("orientation"):
            CONSOLE.info(str(prep_meta["orientation"].get("note") or ""))
        # Refresh preview to show corrected orientation used for measure
        try:
            corr_prev = out / "source_preview_corrected.png"
            if channels and all(k in channels for k in ("R", "G", "B")):
                write_image_preview(
                    np.stack([channels["R"], channels["G"], channels["B"]], axis=0),
                    corr_prev,
                )
            else:
                write_image_preview(meas, corr_prev)
            preview = corr_prev
        except Exception:
            pass
    except Exception as e:
        CONSOLE.warn(f"GRS image prep skipped: {e}")
        prep_meta = {}

    pe = resolve_pro_ephemeris(
        user_time,
        time_error_seconds=time_error,
        cm_override=cm_override,
        sub_lat_override=sub_lat_override,
        north_pa_override=north_pa_override,
        winjupos_path=winjupos_path,
        use_horizons=use_horizons,
        use_spice=use_spice,
    )
    write_ephemeris_report(out / "pro_ephemeris.json", pe)

    nav = fit_limb_nav(meas, cm_iii_deg=pe.cm_iii_deg, distance_au=pe.distance_au)
    nav.cm_iii_deg = pe.cm_iii_deg
    nav.distance_au = pe.distance_au
    # One geometry contract: orientation on NavState for map + moment methods
    if pe.apply_orientation:
        nav.sub_lat_deg = float(pe.sub_obs_lat_deg or 0.0)
        nav.north_pa_deg = float(pe.north_pa_deg or 0.0)
    else:
        nav.sub_lat_deg = 0.0
        nav.north_pa_deg = 0.0

    mc = cap_mc_iterations(mc_iter, megapixels=float(meas.size) / 1e6)
    measure_grs_precision._use_nn = use_nn
    rg = run_research_grade(
        meas,
        nav=nav,
        cm_iii_deg=pe.cm_iii_deg,
        distance_au=pe.distance_au,
        channels=channels,
        injection_trials=max(injection_trials, 28 if factory_mode else 16),
        mc_iter=mc,
        seed=42,
        max_fidelity=True,
        factory_mode=factory_mode,
        user_time_iso=user_time,
        time_error_seconds=time_error,
        aperture_m=aperture_m,
        use_vlbi=use_vlbi,
        winjupos_path=winjupos_path,
        sub_lat_override=sub_lat_override,
        north_pa_override=north_pa_override,
    )
    write_publication_bundle(out / "research_grade.json", rg, extra={
        "user_time": user_time, "path": str(path), "desktop": True, "max_stack": True,
    })

    methods = rg.methods or {}
    eb = methods.get("error_budget") or {}
    vf = methods.get("vlbi_full") or {}
    if isinstance(vf, dict):
        (out / "vlbi_metrology.json").write_text(json.dumps(vf, indent=2, default=str))

    nasa_rep = None
    if nasa:
        try:
            comp = compare_measurement_to_nasa(
                {
                    "lon_iii_deg": rg.lon_bias_corrected_deg,
                    "lat_deg": rg.lat_bias_corrected_deg,
                    "length_deg": rg.length_deg,
                    "width_deg": rg.width_deg,
                },
                user_time,
                time_error,
            )
            write_comparison_report(out / "nasa_comparison.json", comp)
            nasa_rep = comp.to_dict()
            nasa_rep["grade"] = comp.grade()
        except Exception as e:
            CONSOLE.warn(f"NASA: {e}")

    package = {
        "job_id": jid,
        "mode": "process_full_advanced",
        "path": str(path),
        "output_dir": str(out),
        "pro_ephemeris": pe.to_dict(),
        "research_grade": rg.to_dict(),
        "error_budget": eb,
        "nasa": nasa_rep,
        "imaging_pipeline": bool(pipe is not None),
        "grs_image_prep": prep_meta,
        "headline": {
            "mode": "process_full_advanced",
            "grade": rg.grade,
            "user_time": user_time,
            "lon_iii_deg": rg.lon_bias_corrected_deg,
            "lat_deg": rg.lat_bias_corrected_deg,
            "lat_planetographic_deg": vf.get("lat_planetographic_deg") if isinstance(vf, dict) else None,
            "length_deg": rg.length_deg,
            "width_deg": rg.width_deg,
            "sigma_total_sky_arcsec": rg.sigma_total_sky_arcsec,
            "sigma_random_sky_arcsec": rg.sigma_random_sky_arcsec,
            "sigma_systematic_sky_arcsec": rg.sigma_systematic_sky_arcsec,
            "cm_iii_deg": pe.cm_iii_deg,
            "cm_source": pe.cm_source,
            "distance_au": pe.distance_au,
            "injection_n": rg.injection_n,
            "definition_n": rg.definition_n,
            "filter_closure_arcsec": rg.filter_closure_arcsec,
            "bias_lon_deg": rg.bias_lon_deg,
            "bias_lat_deg": rg.bias_lat_deg,
            "optical_floor_arcsec": methods.get("optical_floor_arcsec") or (vf.get("optical_floor_arcsec") if isinstance(vf, dict) else None),
            "primary_method": methods.get("primary_method") or (vf.get("primary_method") if isinstance(vf, dict) else "vlbi"),
            "output_dir": str(out),
        },
    }
    # Professional gold-standard procedure (named definitions, not NASA GRS answer)
    try:
        from gold_standard import attach_gold_to_package
        attach_gold_to_package(
            package,
            meas,
            nav=nav,
            cm_iii_deg=pe.cm_iii_deg,
            distance_au=pe.distance_au,
            cm_source=pe.cm_source,
            user_time_iso=user_time,
            winjupos_manual_lon=winjupos_manual_lon,
            winjupos_manual_lat=winjupos_manual_lat,
            out_dir=out,
        )
    except Exception as e:
        CONSOLE.warn(f"Gold standard: {e}")
    # WinJUPOS twin: fixed GS-MAP/BARY + limb outline sensitivity (larger vs smaller edge)
    try:
        from winjupos_twin import attach_winjupos_twin_to_package
        attach_winjupos_twin_to_package(
            package,
            meas,
            nav=nav,
            cm_iii_deg=pe.cm_iii_deg,
            distance_au=pe.distance_au,
            cm_source=pe.cm_source,
            sub_lat_deg=float(getattr(nav, "sub_lat_deg", 0.0) or 0.0),
            north_pa_deg=float(getattr(nav, "north_pa_deg", 0.0) or 0.0),
            user_time_iso=user_time,
            winjupos_manual_lon=winjupos_manual_lon,
            winjupos_manual_lat=winjupos_manual_lat,
            out_dir=out,
            run_limb_sensitivity=True,
        )
    except Exception as e:
        CONSOLE.warn(f"WinJUPOS twin: {e}")
    # Colour-first orange GRS seed (beats moon/belt dark locks on RGB stacks)
    try:
        from grs_image_prep import orange_grs_lonlat, suggest_ns_flip_for_grs
        rgb_for_orange = None
        if channels and all(k in channels for k in ("R", "G", "B")):
            rgb_for_orange = np.stack(
                [channels["R"], channels["G"], channels["B"]], axis=-1
            )
        flip_ns = bool(prep_meta.get("auto_flip_ns"))
        if rgb_for_orange is None:
            # re-load original for colour if meas is mono
            try:
                _m, _ch, _ = _load_image(path)
                if _ch and all(k in _ch for k in ("R", "G", "B")):
                    rgb_for_orange = np.stack([_ch["R"], _ch["G"], _ch["B"]], axis=-1)
                    if not flip_ns:
                        _, finfo = suggest_ns_flip_for_grs(rgb_for_orange)
                        flip_ns = bool(finfo.get("flip_ns"))
            except Exception:
                pass
        if rgb_for_orange is not None:
            # If prep already flipped channels, do not flip again
            og = orange_grs_lonlat(
                rgb_for_orange,
                cm_iii_deg=float(pe.cm_iii_deg),
                distance_au=float(pe.distance_au),
                sub_lat_deg=float(getattr(nav, "sub_lat_deg", 0.0) or 0.0),
                north_pa_deg=float(
                    north_pa_override
                    if north_pa_override is not None
                    else (0.0 if prep_meta.get("auto_flip_ns") else (getattr(nav, "north_pa_deg", 0.0) or 0.0))
                ),
                # channels already N–S corrected by prep when auto_flip_ns
                flip_ns=False if prep_meta.get("auto_flip_ns") else flip_ns,
            )
            package["orange_grs"] = og
            (out / "orange_grs.json").write_text(
                json.dumps(og, indent=2, default=str), encoding="utf-8"
            )
            if og.get("ok") and not og.get("near_limb"):
                # Inject as high-priority twin-like GS-MAP substitute
                twin = package.setdefault("winjupos_twin", {})
                # Only override if lat looks like GRS
                olat = og.get("lat_deg")
                if olat is not None and -28.5 <= float(olat) <= -15.5:
                    twin["gs_map_lon"] = og["lon_iii_deg"]
                    twin["gs_map_lat"] = og["lat_deg"]
                    twin["orange_grs_seed"] = True
                    h = package.setdefault("headline", {})
                    h["orange_grs_lon_iii_deg"] = og["lon_iii_deg"]
                    h["orange_grs_lat_deg"] = og["lat_deg"]
                    CONSOLE.ok(
                        f"ORANGE GRS seed lon={og['lon_iii_deg']:.3f}° lat={og['lat_deg']:.3f}° "
                        f"(relCM={og.get('lon_rel_cm_deg'):.1f}°)"
                    )
            else:
                CONSOLE.warn(f"Orange GRS seed weak: {og}")
    except Exception as e:
        CONSOLE.warn(f"Orange GRS seed: {e}")

    # Plateau stack: Champion → publish → (then dual may rewrite) → finalize again
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
            package,
            meas,
            nav=nav,
            cm_iii_deg=float(pe.cm_iii_deg),
            distance_au=float(pe.distance_au),
            cm_source=str(pe.cm_source),
            sigma_cm_deg=float(getattr(pe, "sigma_cm_deg", 0.05) or 0.05),
            sub_lat_deg=float(getattr(nav, "sub_lat_deg", 0.0) or 0.0),
            north_pa_deg=float(getattr(nav, "north_pa_deg", 0.0) or 0.0),
            channels=channels,
            out_dir=out,
            user_time_iso=user_time,
            time_error_seconds=float(time_error or 0.0),
            spice_horizons_dcm_deg=_dcm,
        )
        pub = package.get("publish") or {}
        CONSOLE.ok(
            f"PUBLISH {pub.get('publish_definition')} lon={pub.get('publish_lon_iii_deg')}°  "
            f"WJ={ (pub.get('winjupos_equality') or {}).get('agreement') }"
        )
    except Exception as e:
        CONSOLE.warn(f"Finalize (pre-dual): {e}")
    # Dual measure: keep automatic snapshot, then optional human WinJUPOS-style pass
    package = apply_dual_human_pass(
        package,
        meas=meas,
        nav=nav,
        cm_iii_deg=float(pe.cm_iii_deg),
        distance_au=float(pe.distance_au),
        cm_source=str(pe.cm_source),
        user_time_iso=user_time,
        out_dir=out,
        human_choice=human_choice,
        winjupos_manual_lon=winjupos_manual_lon,
        winjupos_manual_lat=winjupos_manual_lat,
        channels=channels,
        light_remeasure=True,
    )
    # Re-finalize SUPERDUPER/publish surface after dual (human may become official)
    try:
        from publish_primary import apply_publish_policy, format_publish_section
        from superduper import attach_superduper
        from job_finalize import write_job_completeness
        apply_publish_policy(package)
        (out / "publish.json").write_text(
            __import__("json").dumps(package.get("publish") or {}, indent=2, default=str),
            encoding="utf-8",
        )
        (out / "publish.txt").write_text(format_publish_section(package), encoding="utf-8")
        attach_superduper(package, out_dir=out)
        write_job_completeness(package, out)
    except Exception as e:
        CONSOLE.warn(f"Finalize (post-dual): {e}")

    write_package_reports(out, package)
    package["preview"] = str(preview) if preview else None
    pub = package.get("publish") or {}
    CONSOLE.ok(
        f"PROCESS DONE publish_lon={pub.get('publish_lon_iii_deg', rg.lon_bias_corrected_deg)}°  "
        f"pipeline={rg.lon_bias_corrected_deg:.4f}°  σ={rg.sigma_total_sky_arcsec:.4f}\"  "
        f"{pub.get('publish_definition') or rg.grade}"
    )
    return package


def apply_dual_human_pass(
    package: Dict[str, Any],
    *,
    meas,
    nav,
    cm_iii_deg: float,
    distance_au: float,
    cm_source: str,
    user_time_iso: str,
    out_dir: Path,
    human_choice: Optional[Dict[str, Any]] = None,
    winjupos_manual_lon: Optional[float] = None,
    winjupos_manual_lat: Optional[float] = None,
    channels=None,
    light_remeasure: bool = True,
) -> Dict[str, Any]:
    """
    After automatic gold/twin/publish, optionally run human-choice pass.

    Stores dual_measure.{json,txt} with automatic vs human and Δsky.
    """
    from human_choice import (
        HumanChoice,
        snapshot_publish_block,
        force_publish_definition,
        build_dual_block,
        write_dual_reports,
        adjust_nav_like_outline,
        apply_image_flips,
    )

    choice = HumanChoice.from_dict(human_choice)
    auto_snap = snapshot_publish_block(package, label="automatic")
    package["dual_measure_automatic_only"] = auto_snap

    if not choice.enabled:
        package["dual_measure"] = {
            "mode": "automatic_only",
            "automatic": auto_snap,
            "official": "automatic",
        }
        return package

    CONSOLE.info(
        f"HUMAN PASS definition={choice.definition} limb_scale={choice.limb_scale:.3f} "
        f"flip_ew={choice.flip_ew} flip_ns={choice.flip_ns}"
    )
    need_remeasure = (
        light_remeasure
        and (
            abs(choice.limb_scale - 1.0) > 1e-3
            or abs(choice.limb_dx_frac) > 1e-4
            or abs(choice.limb_dy_frac) > 1e-4
            or choice.flip_ew
            or choice.flip_ns
        )
    )

    work_meas = meas
    work_nav = nav
    if need_remeasure:
        try:
            work_meas = apply_image_flips(meas, choice.flip_ew, choice.flip_ns)
            work_nav = adjust_nav_like_outline(nav, choice)
            work_nav.cm_iii_deg = cm_iii_deg
            work_nav.distance_au = distance_au
            # Light remeasure: precision + gold + twin (not full VLBI MC stack)
            from precision_engine import measure_grs_precision
            res = measure_grs_precision(
                work_meas, cm_iii_deg=cm_iii_deg, distance_au=distance_au, nav=work_nav, quiet=True
            )
            rd = res.to_dict() if hasattr(res, "to_dict") else dict(getattr(res, "__dict__", {}) or {})
            h = package.setdefault("headline", {})
            h["pipeline_lon_iii_deg"] = rd.get("lon_iii_deg") or rd.get("lon_bias_corrected_deg")
            h["pipeline_lat_deg"] = rd.get("lat_deg") or rd.get("lat_bias_corrected_deg")
            h["length_deg"] = rd.get("length_deg", h.get("length_deg"))
            h["width_deg"] = rd.get("width_deg", h.get("width_deg"))
            h["human_remeasure"] = True
            package["human_research_lite"] = rd
            try:
                from gold_standard import attach_gold_to_package
                attach_gold_to_package(
                    package,
                    work_meas,
                    nav=work_nav,
                    cm_iii_deg=cm_iii_deg,
                    distance_au=distance_au,
                    cm_source=cm_source,
                    user_time_iso=user_time_iso,
                    winjupos_manual_lon=winjupos_manual_lon or choice.manual_lon,
                    winjupos_manual_lat=winjupos_manual_lat or choice.manual_lat,
                    out_dir=out_dir,
                )
            except Exception as e:
                CONSOLE.warn(f"Human gold: {e}")
            try:
                from winjupos_twin import attach_winjupos_twin_to_package
                attach_winjupos_twin_to_package(
                    package,
                    work_meas,
                    nav=work_nav,
                    cm_iii_deg=cm_iii_deg,
                    distance_au=distance_au,
                    cm_source=cm_source,
                    sub_lat_deg=float(getattr(work_nav, "sub_lat_deg", 0.0) or 0.0),
                    north_pa_deg=float(getattr(work_nav, "north_pa_deg", 0.0) or 0.0),
                    user_time_iso=user_time_iso,
                    winjupos_manual_lon=winjupos_manual_lon or choice.manual_lon,
                    winjupos_manual_lat=winjupos_manual_lat or choice.manual_lat,
                    out_dir=out_dir,
                    run_limb_sensitivity=True,
                )
            except Exception as e:
                CONSOLE.warn(f"Human twin: {e}")
            try:
                from publish_primary import apply_publish_policy
                apply_publish_policy(package)
            except Exception as e:
                CONSOLE.warn(f"Human publish policy: {e}")
        except Exception as e:
            CONSOLE.warn(f"Human remeasure failed, definition-only pass: {e}")
            need_remeasure = False

    # Force definition (always) so human choice of GS-MAP / MANUAL etc. is explicit
    force_publish_definition(package, choice)
    if choice.manual_lon is not None and choice.definition == "MANUAL":
        pass  # already applied
    human_snap = snapshot_publish_block(package, label="human")
    dual = build_dual_block(package, auto_snap, human_snap, choice)
    dual["remeasured_limb"] = bool(need_remeasure)
    package["dual_measure"] = dual
    write_dual_reports(out_dir, dual)

    if not choice.use_as_publish:
        # restore automatic as official publish numbers
        pub = package.setdefault("publish", {})
        h = package.setdefault("headline", {})
        pub["publish_definition"] = auto_snap.get("publish_definition")
        pub["publish_lon_iii_deg"] = auto_snap.get("lon_iii_deg")
        pub["publish_lat_deg"] = auto_snap.get("lat_deg")
        h["lon_iii_deg"] = auto_snap.get("lon_iii_deg")
        h["lat_deg"] = auto_snap.get("lat_deg")
        h["publish_definition"] = auto_snap.get("publish_definition")
        dual["official"] = "automatic"
        package["dual_measure"] = dual

    try:
        from publish_primary import format_publish_section
        (out_dir / "publish.json").write_text(
            json.dumps(package.get("publish") or {}, indent=2, default=str), encoding="utf-8"
        )
        (out_dir / "publish.txt").write_text(format_publish_section(package), encoding="utf-8")
    except Exception:
        pass

    cmp_ = dual.get("comparison") or {}
    CONSOLE.ok(
        f"DUAL auto={auto_snap.get('lon_iii_deg')} human={human_snap.get('lon_iii_deg')} "
        f"Δsky={cmp_.get('sky_delta_arcsec')}″ ({cmp_.get('agreement')})"
    )
    return package


def run_factory_night_full(
    out_root: Path,
    *,
    session_time: str,
    region: str = "global",
    resolution: str = "4K",
    mc_iter: int = 50,
    injection_trials: int = 24,
    run_hard: bool = True,
    aperture_m: float = 0.35,
) -> Dict[str, Any]:
    jid = uuid.uuid4().hex[:12]
    out = Path(out_root) / f"factory_{jid}"
    out.mkdir(parents=True, exist_ok=True)
    CONSOLE.info("FACTORY NIGHT FULL (desktop)")

    st = (session_time or "").strip()
    if not st:
        raise ValueError(
            "Factory night requires session_time (UTC). "
            "Refusing silent datetime.now() for System III."
        )
    pe = resolve_pro_ephemeris(st)
    write_ephemeris_report(out / "pro_ephemeris.json", pe)

    synth_pkg = run_synthetic_full(
        out,
        region=region,
        resolution=resolution if resolution != "auto" else "4K",
        mc_iter=mc_iter,
        injection_trials=injection_trials,
        factory_mode=True,
        aperture_m=aperture_m,
        process_after=True,
    )
    # re-home synth already under out if nested
    multi = {}
    try:
        # Only this factory job folder — not the entire outputs tree (pollutes drift).
        epochs = load_epochs_from_dir(out)
        # Never load sibling jobs from the entire outputs tree (pollutes drift rates)
        if len(epochs) >= 2:
            series = build_differential_series(epochs, smooth=True)
            write_multi_epoch_report(out / "multi_epoch.json", series, epochs)
            multi = {
                "n": len(epochs),
                "drift_lon_deg_per_day": series.drift_lon_deg_per_day,
                "rms_residual_sky_arcsec": series.rms_residual_sky_arcsec,
                "smoother": series.smoother,
            }
    except Exception as e:
        multi = {"error": str(e)}

    hard = {}
    if run_hard:
        try:
            hard = run_hard_synth_suite(
                out / "hard_synth",
                resolution="1080p",
                injection_trials=6,
                mc_iter=8,
                user_time_iso=(synth_pkg.get("truth") or {}).get("user_time_iso") or session_time,
            )
            hard = {
                "calibration_grade": hard.get("calibration_grade"),
                "overall": hard.get("overall"),
                "by_family": hard.get("by_family"),
            }
        except Exception as e:
            hard = {"error": str(e)}

    package = {
        "job_id": jid,
        "mode": "factory_night_full",
        "output_dir": str(out),
        "pro_ephemeris": pe.to_dict(),
        "synthetic": synth_pkg.get("headline"),
        "truth_recovery": synth_pkg.get("truth_recovery"),
        "research_grade": synth_pkg.get("research_grade"),
        "error_budget": synth_pkg.get("error_budget"),
        "multi_epoch": multi,
        "hard_synth": hard,
        "truth": synth_pkg.get("truth"),
        "headline": {
            "mode": "factory_night_full",
            "grade": (synth_pkg.get("headline") or {}).get("grade"),
            "synth_epoch": (synth_pkg.get("headline") or {}).get("synth_epoch"),
            "random_time": True,
            "lon_iii_deg": (synth_pkg.get("headline") or {}).get("lon_iii_deg"),
            "lat_deg": (synth_pkg.get("headline") or {}).get("lat_deg"),
            "sigma_total_sky_arcsec": (synth_pkg.get("headline") or {}).get("sigma_total_sky_arcsec"),
            "truth_recovery_sky_arcsec": (synth_pkg.get("truth_recovery") or {}).get("sky_error_arcsec"),
            "calibration_grade": hard.get("calibration_grade") if isinstance(hard, dict) else None,
            "multi_epoch_n": multi.get("n"),
            "drift_lon_deg_per_day": multi.get("drift_lon_deg_per_day"),
            "output_dir": str(out),
        },
    }
    write_package_reports(out, package)
    (out / "factory_night_report.json").write_text(
        json.dumps({k: v for k, v in package.items() if k != "text"}, indent=2, default=str),
        encoding="utf-8",
    )
    package["preview"] = synth_pkg.get("preview")
    return package

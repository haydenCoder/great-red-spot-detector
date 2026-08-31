#!/usr/bin/env python3
"""
product_core.py — the main entry point for all product workflows

Every shippable workflow (Process, Synthetic, Ephemeris, Certify) should
go through this module instead of duplicating logic between desktop and
server. I originally had separate code paths and it was a mess — this
file is the cleanup that makes sure CLI and desktop give the same answers.

Version comes from ../VERSION file, hardcoded fallback is 6.5.0 (the
version string bug was a headache I spent an afternoon tracking down).
"""
from __future__ import annotations

import json
import math
import platform
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

APP_DIR = Path(__file__).resolve().parent
ROOT_DIR = APP_DIR.parent


def product_version() -> str:
    """Read the version from the VERSION file — the single source of truth.

    Returns "unknown" rather than a hardcoded literal when the file is missing,
    so a stale number can never be advertised as the real one.
    """
    for p in (ROOT_DIR / "VERSION", APP_DIR / "VERSION"):
        try:
            if p.exists():
                v = p.read_text(encoding="utf-8").strip()
                if v:
                    return v
        except Exception:
            pass
    return "unknown"


PRODUCT_NAME = "Jupiter Great Red Spot Detector"
PRODUCT_TAGLINE = "Professional ground-based GRS optical metrology"
PRODUCT_VERSION = product_version()


@dataclass
class ProductInfo:
    name: str = PRODUCT_NAME
    version: str = PRODUCT_VERSION
    tagline: str = PRODUCT_TAGLINE
    python: str = field(default_factory=lambda: sys.version.split()[0])
    platform: str = field(default_factory=lambda: platform.platform())
    app_dir: str = field(default_factory=lambda: str(APP_DIR))

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def default_out_root() -> Path:
    """Writable outputs directory — tries the portable paths module first,
    falls back to app/outputs if that's not available (happens in frozen
    PyInstaller bundles)."""
    try:
        from paths import outputs_dir
        return outputs_dir()
    except Exception:
        # frozen app or weird install — just make a local outputs dir
        p = APP_DIR / "outputs"
        p.mkdir(parents=True, exist_ok=True)
        return p


def process_image(
    path: str | Path,
    user_time: str,
    *,
    out_root: Optional[Path] = None,
    time_error: float = 0.0,
    use_spice: bool = True,
    use_horizons: bool = True,
    use_vlbi: bool = True,
    use_nn: bool = False,
    nasa: bool = True,
    cm_override: Optional[float] = None,
    aperture_m: float = 0.35,
    mc_iter: int = 60,
    injection_trials: int = 24,
    winjupos_path: Optional[str] = None,
    winjupos_manual_lon: Optional[float] = None,
    winjupos_manual_lat: Optional[float] = None,
) -> Dict[str, Any]:
    """Process a real image — this is the main science entry point.
    Delegates to desktop_pipeline.run_process_full so we get the same
    stack the GUI uses (no divergent code paths)."""
    from desktop_pipeline import run_process_full

    out_root = Path(out_root or default_out_root())
    pkg = run_process_full(
        Path(path),
        out_root,
        user_time=user_time,
        time_error=time_error,
        mc_iter=mc_iter,
        injection_trials=injection_trials,
        factory_mode=True,
        use_vlbi=use_vlbi,
        use_nn=use_nn,
        nasa=nasa,
        aperture_m=aperture_m,
        cm_override=cm_override,
        use_horizons=use_horizons,
        use_spice=use_spice,
        run_imaging=True,
        winjupos_path=winjupos_path,
        winjupos_manual_lon=winjupos_manual_lon,
        winjupos_manual_lat=winjupos_manual_lat,
    )
    pkg["product"] = ProductInfo().to_dict()
    return pkg


def generate_synthetic(
    *,
    out_root: Optional[Path] = None,
    resolution: str = "1080p",
    region: str = "global",
    mode: str = "metrology",
    process_after: bool = True,
    seed: Optional[int] = None,
    wave_contrast: Optional[float] = None,
    use_vlbi: bool = True,
    use_nn: bool = False,
    human_choice: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Generate a synthetic Jupiter frame and optionally measure it.

    I deliberately use the SAME full desktop stack here (not a quick
    shortcut) so that CLI certify numbers actually match what the GUI
    buttons produce — spent ages debugging discrepancies caused by
    two different code paths before I unified them here.

    human_choice: dict from the dual-limb dialog if you want auto+human.
    """
    from desktop_pipeline import run_synthetic_full

    out_root = Path(out_root or default_out_root())
    # pass seed through env var so synthetic_hq picks it up
    if seed is not None:
        import os
        os.environ["GRS_SYNTH_SEED"] = str(int(seed))

    if not process_after:
        # just generate the image, don't measure (for quick preview runs)
        from synthetic_hq import SynthSpec, generate
        job = out_root / f"synth_product_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        job.mkdir(parents=True, exist_ok=True)
        png, fit, truth = generate(
            SynthSpec(
                region=region,
                resolution_preset=resolution,
                random_time=True,
                seed=seed,
                mode=mode,
                write_grs_crop=True,
            ),
            job,
        )
        package = {
            "product": ProductInfo().to_dict(),
            "mode": f"synthetic_{mode}",
            "truth": truth,
            "png": str(png),
            "fit": str(fit),
            "output_dir": str(job),
        }
        (job / "job_result.json").write_text(json.dumps(package, indent=2, default=str))
        return package

    package = run_synthetic_full(
        out_root,
        region=region,
        resolution=resolution if resolution != "auto" else "4K",
        factory_mode=True,
        use_vlbi=use_vlbi,
        use_nn=use_nn,
        nasa=True,
        process_after=True,
        mode=mode,
        seed=seed,
        human_choice=human_choice,
    )
    package["product"] = ProductInfo().to_dict()
    package["mode"] = f"synthetic_{mode}_desktop_full"

    # Make sure sky error fields are always findable under canonical names
    # — I kept getting confused by different packages using different key names
    # so I normalise them here so certify/CLI always works
    h = dict(package.get("headline") or {})
    tr = dict(package.get("truth_recovery") or {})
    sky = tr.get("sky_error_arcsec")
    if sky is None:
        sky = h.get("sky_error_arcsec")
    if sky is None:
        sky = h.get("truth_recovery_sky_arcsec")
    if sky is not None:
        tr["sky_error_arcsec"] = float(sky)
        h["sky_error_arcsec"] = float(sky)
        h.setdefault("truth_recovery_sky_arcsec", float(sky))
    package["truth_recovery"] = tr
    package["headline"] = h
    return package


def resolve_ephemeris(user_time: str, *, use_spice: bool = True, use_horizons: bool = True) -> Dict[str, Any]:
    """Just resolve ephemeris — no image, no measurement."""
    from ephemeris_pro import resolve_pro_ephemeris, write_ephemeris_report

    pe = resolve_pro_ephemeris(user_time, use_spice=use_spice, use_horizons=use_horizons)
    out = default_out_root() / f"eph_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
    out.mkdir(parents=True, exist_ok=True)
    write_ephemeris_report(out / "pro_ephemeris.json", pe)
    d = pe.to_dict()
    d["product"] = ProductInfo().to_dict()
    d["output_dir"] = str(out)
    return d


def certify(
    *,
    n: int = 40,
    resolution: str = "1080p",
    out_root: Optional[Path] = None,
    median_max_arcsec: float = 0.75,
    p95_max_arcsec: float = 2.5,
    max_max_arcsec: float = 8.0,
    oracle_median_max_arcsec: float = 0.35,
) -> Dict[str, Any]:
    """
    Certification suite — run N synthetics, check truth recovery, gate results.

    The thresholds are honest (not fantasy 0.00″). Real sky will be worse
    than synthetic, so if this passes you're in good shape but not guaranteed
    perfect. I set these after running ~200 tests and seeing where the floor
    actually sits.
    """
    import statistics
    import time
    from spice_auto import selftest as spice_selftest

    out_root = Path(out_root or (default_out_root() / f"certify_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"))
    out_root.mkdir(parents=True, exist_ok=True)
    runs_dir = out_root / "runs"
    runs_dir.mkdir(exist_ok=True)

    # check SPICE first — if kernels can't load everything else is moot
    spice = spice_selftest()
    skys: List[float] = []
    skys_o: List[float] = []  # oracle nav results (truth-based disk placement)
    rows: List[Dict[str, Any]] = []
    t0 = time.time()

    for i in range(max(1, int(n))):
        sub = runs_dir / f"run_{i+1:04d}"
        try:
            pkg = generate_synthetic(
                out_root=sub,
                resolution=resolution,
                mode="metrology",
                process_after=True,
                seed=10_000 + i * 7919,  # deterministic seeds so runs are reproducible
            )
            tr = pkg.get("truth_recovery") or {}
            tro = pkg.get("truth_recovery_oracle_nav") or {}
            sky = float(tr.get("sky_error_arcsec", 99))
            sky_o = float(tro.get("sky_error_arcsec", 99))
            skys.append(sky)
            skys_o.append(sky_o)
            rows.append({
                "run": i + 1,
                "ok": True,
                "sky_error_arcsec": sky,
                "oracle_sky_error_arcsec": sky_o,
                "output_dir": pkg.get("output_dir"),
            })
        except Exception as e:
            # a failed run still gets recorded — we gate on ok ratio later
            rows.append({"run": i + 1, "ok": False, "error": str(e)})

    def _pct(xs: List[float], p: float) -> float:
        """manual percentile — statistics module doesn't have one pre-3.8"""
        if not xs:
            return float("nan")
        a = sorted(xs)
        k = (len(a) - 1) * p / 100.0
        f, c = int(k), min(int(k) + 1, len(a) - 1)
        if f == c:
            return float(a[f])
        return float(a[f] * (c - k) + a[c] * (k - f))

    n_ok = sum(1 for r in rows if r.get("ok"))
    median = statistics.median(skys) if skys else 99.0
    p95 = _pct(skys, 95)
    mx = max(skys) if skys else 99.0
    o_med = statistics.median(skys_o) if skys_o else 99.0

    # oracle nav is optional — some packages don't have it, only gate when present
    has_oracle = bool(skys_o) and all(math.isfinite(x) for x in skys_o)

    gates = {
        "spice_ok": bool(spice.get("ok")),
        "n_ok_ratio": n_ok / max(n, 1) >= 0.95,
        "median_le": median <= median_max_arcsec,
        "p95_le": p95 <= p95_max_arcsec,
        "max_le": mx <= max_max_arcsec,
    }
    if has_oracle:
        gates["oracle_median_le"] = o_med <= oracle_median_max_arcsec
    passed = all(gates.values())

    report = {
        "product": ProductInfo().to_dict(),
        "certified_utc": datetime.now(timezone.utc).isoformat(),
        "n_requested": n,
        "n_ok": n_ok,
        "resolution": resolution,
        "mode": "metrology",
        "elapsed_s": time.time() - t0,
        "spice": spice,
        "full_pipeline_sky_arcsec": {
            "median": median,
            "mean": statistics.fmean(skys) if skys else None,
            "p95": p95,
            "max": mx,
            "min": min(skys) if skys else None,
        },
        "oracle_nav_sky_arcsec": {
            "median": o_med,
            "p95": _pct(skys_o, 95),
            "max": max(skys_o) if skys_o else None,
            "min": min(skys_o) if skys_o else None,
        },
        "gates": gates,
        "thresholds": {
            "median_max_arcsec": median_max_arcsec,
            "p95_max_arcsec": p95_max_arcsec,
            "max_max_arcsec": max_max_arcsec,
            "oracle_median_max_arcsec": oracle_median_max_arcsec,
        },
        "passed": passed,
        "grade": "SHIP" if passed else "HOLD",
        "rows": rows,
        "output_dir": str(out_root),
        "disclaimer": (
            "Certification is on synthetic metrology-mode frames. Real-sky performance "
            "depends on seeing, timing, and CM quality. See docs/GRS_CODE_WALKTHROUGH_ESSAY.md."
        ),
    }

    (out_root / "certification.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    # human-readable certification report — the JSON is for machines, this is for us
    lines = [
        f"{PRODUCT_NAME} v{PRODUCT_VERSION} — PRODUCT CERTIFICATION",
        "=" * 56,
        f"UTC:     {report['certified_utc']}",
        f"Result:  {'PASS (SHIP)' if passed else 'FAIL (HOLD)'}",
        f"N ok:    {n_ok}/{n}",
        f"SPICE:   {gates['spice_ok']}",
        "",
        "FULL PIPELINE (measured limb nav)",
        f"  median = {median:.4f}\"  (gate ≤ {median_max_arcsec}\")",
        f"  p95    = {p95:.4f}\"  (gate ≤ {p95_max_arcsec}\")",
        f"  max    = {mx:.4f}\"  (gate ≤ {max_max_arcsec}\")",
        "",
        "ORACLE NAV (truth disk — detector floor)",
        f"  median = {o_med:.4f}\"  (gate ≤ {oracle_median_max_arcsec}\")",
        "",
        "GATES",
        *[f"  {k}: {v}" for k, v in gates.items()],
        "",
        report["disclaimer"],
        f"Output: {out_root}",
    ]
    text = "\n".join(lines)
    (out_root / "certification.txt").write_text(text, encoding="utf-8")
    report["text"] = text
    return report

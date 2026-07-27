#!/usr/bin/env python3
"""
GRS Observatory — product core (single professional entry surface)
=================================================================

All shippable workflows should call into this module rather than
duplicating process/synthetic logic across desktop and server.

Product version is read from ../VERSION when available.
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
    for p in (ROOT_DIR / "VERSION", APP_DIR / "VERSION"):
        if p.exists():
            return p.read_text(encoding="utf-8").strip() or "5.2.0"
    return "5.2.0"


PRODUCT_NAME = "Great Red Spot Detector"
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
    """Writable outputs — uses paths.outputs_dir() when available (frozen-app safe)."""
    try:
        from paths import outputs_dir
        return outputs_dir()
    except Exception:
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
    """Professional Process entry — real image metrology (+ WinJUPOS twin)."""
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
    """
    Synthetic generation (+ optional measure).

    Uses the SAME desktop full stack as the UI (VLBI/research-grade) so
    CLI certify numbers match Process / Synthetic buttons.

    human_choice: optional dual auto+human (WinJUPOS-style) pass dict.
    """
    from desktop_pipeline import run_synthetic_full

    out_root = Path(out_root or default_out_root())
    # seed is honored by re-exporting into env for synthetic_hq when set
    if seed is not None:
        import os
        os.environ["GRS_SYNTH_SEED"] = str(int(seed))

    if not process_after:
        # generation-only still goes through desktop synth without measure
        from synthetic_hq import SynthSpec, generate
        job = out_root / f"synth_product_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
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
    # Ensure certify / CLI can always find sky error fields (canonical aliases)
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
    from ephemeris_pro import resolve_pro_ephemeris, write_ephemeris_report

    pe = resolve_pro_ephemeris(user_time, use_spice=use_spice, use_horizons=use_horizons)
    out = default_out_root() / f"eph_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
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
    Product certification suite — metrology synthetics + SPICE + dual recovery.

    Exit criteria are professional (honest) gates for shipping, not fantasy 0.00″.
    """
    import statistics
    import time
    from spice_auto import selftest as spice_selftest

    out_root = Path(out_root or (default_out_root() / f"certify_{datetime.now().strftime('%Y%m%d_%H%M%S')}"))
    out_root.mkdir(parents=True, exist_ok=True)
    runs_dir = out_root / "runs"
    runs_dir.mkdir(exist_ok=True)

    spice = spice_selftest()
    skys: List[float] = []
    skys_o: List[float] = []
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
                seed=10_000 + i * 7919,
            )
            # re-home: generate_synthetic nests another folder; use package paths
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
            rows.append({"run": i + 1, "ok": False, "error": str(e)})

    def _pct(xs: List[float], p: float) -> float:
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
    # Oracle nav is optional — only gate when present (many packages omit it)
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
            "depends on seeing, timing, and CM quality. See docs/GRS_OBSERVATORY_BOOK.md."
        ),
    }

    (out_root / "certification.json").write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    lines = [
        f"{PRODUCT_NAME} v{PRODUCT_VERSION} — PRODUCT CERTIFICATION",
        "=" * 56,
        f"UTC:     {report['certified_utc']}",
        f"Result:  {'PASS (SHIP)' if passed else 'FAIL (HOLD)'}",
        f"N ok:    {n_ok}/{n}",
        f"SPICE:   {gates['spice_ok']}",
        "",
        "FULL PIPELINE (measured limb nav)",
        f"  median = {median:.4f}″  (gate ≤ {median_max_arcsec}″)",
        f"  p95    = {p95:.4f}″  (gate ≤ {p95_max_arcsec}″)",
        f"  max    = {mx:.4f}″  (gate ≤ {max_max_arcsec}″)",
        "",
        "ORACLE NAV (truth disk — detector floor)",
        f"  median = {o_med:.4f}″  (gate ≤ {oracle_median_max_arcsec}″)",
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

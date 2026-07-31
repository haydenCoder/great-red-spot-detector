#!/usr/bin/env python3
"""
Jupiter Great Red Spot Detector — professional command-line interface
=====================================================

Examples:
  python3 cli.py version
  python3 cli.py eph "2026-07-14 12:00:00"
  python3 cli.py synth --mode metrology --res 1080p
  python3 cli.py process /path/to/jupiter.fits --time "2026-01-09 17:06:00"
  python3 cli.py certify --n 30
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np


def main(argv=None) -> int:
    # ensure app dir imports
    app_dir = Path(__file__).resolve().parent
    if str(app_dir) not in sys.path:
        sys.path.insert(0, str(app_dir))

    from product_core import (
        PRODUCT_NAME,
        PRODUCT_VERSION,
        ProductInfo,
        process_image,
        generate_synthetic,
        resolve_ephemeris,
        certify,
        default_out_root,
    )
    import license_manager as lic

    def _load_or_render_frames(args):
        """Return (frames_gray, cm_list) for the planet-* subcommands.

        Loads PNG/JPG/FITS from --frames-dir if given, else renders N synthetic
        Jupiter-like frames (the synthetic renderer is Jupiter-only, so for
        other planets you must supply --frames-dir with real frames).
        """
        from pathlib import Path as _P
        frames = []
        if getattr(args, "frames_dir", ""):
            d = _P(args.frames_dir)
            exts = (".png", ".jpg", ".jpeg", ".fits", ".fit")
            files = sorted([f for f in d.iterdir() if f.suffix.lower() in exts])
            if not files:
                raise SystemExit(f"no image frames found in {d}")
            from precision_engine import to_mono
            import grs_complete_system as grs
            for f in files:
                if f.suffix.lower() in (".fits", ".fit"):
                    arr, _ = grs.read_fits(f)
                    frames.append(np.asarray(arr, dtype=np.float64))
                else:
                    from PIL import Image as _PIL
                    a = np.asarray(_PIL.open(f).convert("L"), dtype=np.float64) / 255.0
                    frames.append(a)
                if len(frames) >= args.n:
                    break
            cm_list = [0.0] * len(frames)
            return frames, cm_list
        # synthetic render (Jupiter renderer)
        from synthetic_hq import SynthSpec, generate
        import grs_complete_system as grs
        import tempfile, os
        tmp = _P(tempfile.mkdtemp(prefix="grs_planetcli_"))
        cm_list = []
        for k in range(args.n):
            spec = SynthSpec(
                user_time_iso="", region="global",
                resolution_preset=args.res, random_time=True,
                seed=args.seed * 1000 + k * 31, mode="metrology",
                write_grs_crop=False,
            )
            _png, fit, truth = generate(spec, tmp)
            arr, _ = grs.read_fits(fit)
            img = np.asarray(arr, dtype=np.float64)
            if img.ndim == 3 and img.shape[0] == 3:
                img = 0.3 * img[0] + 0.5 * img[1] + 0.2 * img[2]
            frames.append(img)
            cm_list.append(float(truth["cm_iii_deg"]))
        return frames, cm_list

    # Data dir for license (same as app outputs parent)
    data_dir = app_dir

    p = argparse.ArgumentParser(
        prog="grs-observatory",
        description=f"{PRODUCT_NAME} v{PRODUCT_VERSION} — professional GRS optical metrology",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("version", help="Print product version and environment")

    pl = sub.add_parser("license", help="License status / activate / vendor generate")
    pl_sub = pl.add_subparsers(dest="lic_cmd", required=True)
    pl_sub.add_parser("status", help="Show current license")
    pl_sub.add_parser("machine", help="Print this machine id (for bound keys)")
    pla = pl_sub.add_parser("activate", help="Activate a license key")
    pla.add_argument("key", help="License key GRS-1-…")
    plg = pl_sub.add_parser("generate", help="Vendor: generate a key (set GRS_LICENSE_SECRET)")
    plg.add_argument("--plan", default="PRO", choices=list(lic.PLANS))
    plg.add_argument("--customer", default="CUSTOMER")
    plg.add_argument("--days", type=int, default=0, help="0 = no expiry")
    plg.add_argument("--bind", action="store_true", help="Bind to this machine")

    po = sub.add_parser("owner", help="Group owner: who used the app (access logs)")
    po_sub = po.add_subparsers(dest="owner_cmd", required=True)
    po_sub.add_parser("summary", help="Summarize usage by user/machine/action")
    pot = po_sub.add_parser("tail", help="Show recent events")
    pot.add_argument("-n", type=int, default=30)
    po_sub.add_parser("paths", help="Show model + log paths on this device")
    po_sub.add_parser("models", help="Verify CNN weights present on this device")

    pe = sub.add_parser("eph", help="Resolve pro ephemeris (SPICE auto + Horizons)")
    pe.add_argument("time", help="UTC time, e.g. '2026-07-14 12:00:00'")
    pe.add_argument("--no-spice", action="store_true")
    pe.add_argument("--no-horizons", action="store_true")

    ps = sub.add_parser("synth", help="Generate synthetic Jupiter (+ measure)")
    ps.add_argument("--mode", choices=["metrology", "visual"], default="metrology")
    ps.add_argument("--res", default="1080p", choices=["1080p", "4K", "8K", "16K"])
    ps.add_argument("--region", default="global")
    ps.add_argument("--no-measure", action="store_true")
    ps.add_argument("--seed", type=int, default=None)
    ps.add_argument("--out", default="", help="Output root directory")

    pp = sub.add_parser("process", help="Process a real image (full stack)")
    pp.add_argument("path", help="FITS/SER/PNG/JPEG path")
    pp.add_argument("--time", required=True, help="Observation UTC")
    pp.add_argument("--time-error", type=float, default=0.0)
    pp.add_argument("--cm", type=float, default=None, help="CM III override degrees")
    pp.add_argument("--winjupos", default="", help="WinJUPOS CM table CSV/JSON path")
    pp.add_argument("--wj-lon", type=float, default=None, help="Your WinJUPOS manual GRS lon III")
    pp.add_argument("--wj-lat", type=float, default=None, help="Your WinJUPOS manual GRS lat")
    pp.add_argument("--no-spice", action="store_true")
    pp.add_argument(
        "--no-nn",
        action="store_true",
        default=False,
        help="Disable SPIRE-Net soft prior (default: NN allowed if weights present)",
    )
    pp.add_argument("--out", default="")

    pc = sub.add_parser("certify", help="Run product certification suite (metrology)")
    pc.add_argument("--n", type=int, default=30)
    pc.add_argument("--res", default="1080p")
    pc.add_argument("--out", default="")
    pc.add_argument("--median-max", type=float, default=0.75)
    pc.add_argument("--p95-max", type=float, default=2.5)
    pc.add_argument("--max-max", type=float, default=8.0)
    pc.add_argument("--oracle-median-max", type=float, default=0.35)

    # Holy-hybrid stacker + WinJUPOS derotator subcommands
    ph = sub.add_parser(
        "holy-stack",
        help="Run the hybrid CNN+physics stacker on a synthetic or real video (auto-trains the CNN if needed)",
    )
    ph.add_argument("--n", type=int, default=24, help="number of frames to render and stack")
    ph.add_argument("--res", default="720p")
    ph.add_argument("--region", default="global")
    ph.add_argument("--importance", type=int, default=32)
    ph.add_argument("--n-grid", type=int, default=6)
    ph.add_argument("--ap-half", type=int, default=16)
    ph.add_argument("--seed", type=int, default=0)
    ph.add_argument("--out", default="")
    ph.add_argument("--no-train", action="store_true",
                    help="do not (re)train the HolyCNN even if weights are missing")

    pwj = sub.add_parser(
        "wj-derotate",
        help="Run the WinJUPOS-style rigid-rotation derotator on a synthetic or real video",
    )
    pwj.add_argument("--n", type=int, default=24)
    pwj.add_argument("--res", default="720p")
    pwj.add_argument("--n-grid", type=int, default=6)
    pwj.add_argument("--ap-half", type=int, default=16)
    pwj.add_argument("--eq-band-frac", type=float, default=0.2)
    pwj.add_argument("--seed", type=int, default=0)
    pwj.add_argument("--out", default="")

    # Jupiter-specialized zonal-shear stacker + derotator
    pzs = sub.add_parser(
        "zonal-stack",
        help="Run the Jupiter-specialized zonal-shear AP stacker on a synthetic or real video. "
             "Uses System III + zonal-wind-residual as a per-AP prior; GRS-anchor mode "
             "demotes APs that disagree with a localised GRS.",
    )
    pzs.add_argument("--n", type=int, default=24)
    pzs.add_argument("--res", default="720p")
    pzs.add_argument("--n-grid", type=int, default=6)
    pzs.add_argument("--ap-half", type=int, default=16)
    pzs.add_argument("--cm-drift", type=float, default=0.0,
                     help="synthetic CM III drift per frame in deg (for synthetic test runs)")
    pzs.add_argument("--grs-xy", default="",
                     help="optional 'x,y' pixel coords of GRS in reference frame")
    pzs.add_argument("--seed", type=int, default=0)
    pzs.add_argument("--out", default="")

    pzd = sub.add_parser(
        "zonal-derotate",
        help="Run the Jupiter-specialized zonal-derotator on a synthetic or real video. "
             "Per-row shifts from the zonal-wind-residual profile (prior mode) or "
             "from AP-grid measurements (measurement mode). EXPERIMENTAL: not a "
             "strict improvement over winjupos on synthetic data with rigid rotation.",
    )
    pzd.add_argument("--n", type=int, default=24)
    pzd.add_argument("--res", default="720p")
    pzd.add_argument("--n-grid", type=int, default=6)
    pzd.add_argument("--ap-half", type=int, default=16)
    pzd.add_argument("--mode", choices=["measurement", "prior"], default="measurement")
    pzd.add_argument("--cm-drift", type=float, default=0.0)
    pzd.add_argument("--seed", type=int, default=0)
    pzd.add_argument("--out", default="")

    # ------------------------------------------------------------------
    # Planet-generalised stacker / derotator (v6.7.0): not Jupiter-only.
    # ------------------------------------------------------------------
    pps = sub.add_parser(
        "planet-stack",
        help="Stack frames of ANY planet (Jupiter/Saturn/Neptune/Uranus/Mars) "
             "with a per-latitude warp (fixes zonal shear that a single global "
             "translation smears).",
    )
    pps.add_argument("--planet", default="Jupiter",
                     help="planet name (Jupiter, Saturn, Neptune, Uranus, Mars)")
    pps.add_argument("--n", type=int, default=12)
    pps.add_argument("--res", default="720p")
    pps.add_argument("--frames-dir", default="",
                     help="load frames from a folder (PNG/JPG/FITS) instead of rendering synthetic")
    pps.add_argument("--n-grid", type=int, default=8)
    pps.add_argument("--ap-half", type=int, default=16)
    pps.add_argument("--warp-mode", choices=["per_latitude", "flow", "global"], default="per_latitude")
    pps.add_argument("--reference", choices=["auto", "first"], default="auto")
    pps.add_argument("--quality-gate", type=float, default=1.0,
                     help="keep the sharpest fraction of frames (0..1, lucky-imaging rejection)")
    pps.add_argument("--seed", type=int, default=0)
    pps.add_argument("--out", default="")

    ppd = sub.add_parser(
        "planet-derotate",
        help="Derotate frames of ANY planet with a per-latitude warp "
             "(measurement / prior / hybrid modes).",
    )
    ppd.add_argument("--planet", default="Jupiter")
    ppd.add_argument("--n", type=int, default=12)
    ppd.add_argument("--res", default="720p")
    ppd.add_argument("--frames-dir", default="")
    ppd.add_argument("--n-grid", type=int, default=6)
    ppd.add_argument("--ap-half", type=int, default=16)
    ppd.add_argument("--mode", choices=["measurement", "prior", "hybrid"], default="measurement")
    ppd.add_argument("--reference", choices=["auto", "first"], default="auto")
    ppd.add_argument("--seed", type=int, default=0)
    ppd.add_argument("--out", default="")

    args = p.parse_args(argv)

    if args.cmd == "version":
        info = ProductInfo().to_dict()
        info["license"] = lic.load_status(data_dir).to_dict()
        print(json.dumps(info, indent=2))
        return 0

    if args.cmd == "license":
        if args.lic_cmd == "status":
            print(json.dumps(lic.load_status(data_dir).to_dict(), indent=2))
            return 0
        if args.lic_cmd == "machine":
            print(lic.machine_fingerprint())
            return 0
        if args.lic_cmd == "activate":
            st = lic.save_license(data_dir, args.key)
            print(json.dumps(st.to_dict(), indent=2))
            return 0 if st.valid and st.licensed else 2
        if args.lic_cmd == "generate":
            if lic._secret() == lic._DEFAULT_SECRET.encode():  # type: ignore
                print(
                    "WARNING: using default evaluation secret. "
                    "Export GRS_LICENSE_SECRET before selling keys.",
                    file=sys.stderr,
                )
            key = lic.generate_key(
                plan=args.plan,
                customer=args.customer,
                days=args.days,
                bind_machine=args.bind,
            )
            print(key)
            return 0

    if args.cmd == "owner":
        import group_access
        from paths import ensure_tree, ensure_models_present, model_dir
        if args.owner_cmd == "summary":
            print(json.dumps(group_access.summarize(), indent=2, default=str))
            return 0
        if args.owner_cmd == "tail":
            ev = group_access.read_events(limit=args.n)
            print(json.dumps(ev, indent=2, default=str))
            return 0
        if args.owner_cmd == "paths":
            print(json.dumps(ensure_tree(), indent=2))
            return 0
        if args.owner_cmd == "models":
            d = ensure_models_present()
            w = d / "spire_net_weights.npz"
            m = d / "spire_net_meta.json"
            print(json.dumps({
                "model_dir": str(d),
                "weights_exist": w.exists(),
                "weights_bytes": w.stat().st_size if w.exists() else 0,
                "meta_exist": m.exists(),
                "ok": w.exists() and w.stat().st_size > 1_000_000,
            }, indent=2))
            return 0 if w.exists() else 2

    if args.cmd == "eph":
        d = resolve_ephemeris(
            args.time,
            use_spice=not args.no_spice,
            use_horizons=not args.no_horizons,
        )
        print(json.dumps({
            "t_utc_iso": d.get("t_utc_iso"),
            "cm_iii_deg": d.get("cm_iii_deg"),
            "cm_source": d.get("cm_source"),
            "distance_au": d.get("distance_au"),
            "distance_source": d.get("distance_source"),
            "sub_obs_lat_deg": d.get("sub_obs_lat_deg"),
            "north_pa_deg": d.get("north_pa_deg"),
            "source": d.get("source"),
            "output_dir": d.get("output_dir"),
        }, indent=2))
        return 0

    if args.cmd == "synth":
        out = Path(args.out) if args.out else default_out_root()
        pkg = generate_synthetic(
            out_root=out,
            resolution=args.res,
            region=args.region,
            mode=args.mode,
            process_after=not args.no_measure,
            seed=args.seed,
        )
        h = pkg.get("headline") or {}
        tr = pkg.get("truth_recovery") or {}
        sky = tr.get("sky_error_arcsec")
        if sky is None:
            sky = h.get("sky_error_arcsec")
        if sky is None:
            sky = h.get("truth_recovery_sky_arcsec")
        oracle = h.get("oracle_sky_error_arcsec")
        if oracle is None:
            oracle = (pkg.get("truth_recovery_oracle_nav") or {}).get("sky_error_arcsec")
        pq = pkg.get("publish_quality") or (pkg.get("publish") or {}).get("quality") or {}
        print(json.dumps({
            "mode": pkg.get("mode"),
            "output_dir": pkg.get("output_dir"),
            "truth_lon": (pkg.get("truth") or {}).get("grs_lon_iii_deg"),
            "meas_lon": h.get("lon_iii_deg"),
            "sky_error_arcsec": sky,
            "oracle_sky_error_arcsec": oracle,
            "truth_recovery_grade": tr.get("grade") or h.get("truth_recovery_grade"),
            "dlon_deg": tr.get("dlon_deg", h.get("dlon_deg")),
            "dlat_deg": tr.get("dlat_deg", h.get("dlat_deg")),
            "cm_source": h.get("cm_source") or (pkg.get("publish") or {}).get("cm_source"),
            "publish_ok": pq.get("publish_ok", h.get("publish_ok")),
            "absolute_ok": pq.get("absolute_ok", h.get("absolute_ok")),
            "quality_grade": pq.get("grade") or h.get("quality_grade"),
            "quality_flags": pq.get("flags") or h.get("quality_flags"),
            "png": pkg.get("png"),
        }, indent=2))
        return 0

    if args.cmd == "process":
        try:
            lic.assert_feature(data_dir, "process")
        except PermissionError as e:
            print(f"LICENSE: {e}", file=sys.stderr)
            return 4
        out = Path(args.out) if args.out else default_out_root()
        if not Path(args.path).exists():
            print(f"ERROR: file not found: {args.path}", file=sys.stderr)
            return 2
        pkg = process_image(
            args.path,
            args.time,
            out_root=out,
            time_error=args.time_error,
            use_spice=not args.no_spice,
            use_nn=not args.no_nn,
            cm_override=args.cm,
            winjupos_path=args.winjupos or None,
            winjupos_manual_lon=args.wj_lon,
            winjupos_manual_lat=args.wj_lat,
        )
        h = pkg.get("headline") or {}
        pub = pkg.get("publish") or {}
        eq = pub.get("winjupos_equality") or {}
        print(json.dumps({
            "mode": pkg.get("mode"),
            "output_dir": pkg.get("output_dir"),
            "PUBLISH_definition": pub.get("publish_definition") or h.get("publish_definition"),
            "PUBLISH_lon_iii_deg": pub.get("publish_lon_iii_deg", h.get("lon_iii_deg")),
            "PUBLISH_lat_deg": pub.get("publish_lat_deg", h.get("lat_deg")),
            "pipeline_lon_iii_deg": pub.get("pipeline_lon_iii_deg") or h.get("pipeline_lon_iii_deg"),
            "sigma_total_sky_arcsec": h.get("sigma_total_sky_arcsec"),
            "cm_iii_deg": h.get("cm_iii_deg"),
            "cm_source": h.get("cm_source"),
            "soup_n_methods": pub.get("soup_n_methods"),
            "soup_role": "scatter_only",
            "winjupos_agreement": eq.get("agreement"),
            "equal_to_winjupos": eq.get("equal_to_winjupos"),
            "vs_winjupos_sky_arcsec": eq.get("sky_error_arcsec"),
            "limb_outline_sky_spread_arcsec": h.get("limb_outline_sky_spread_arcsec"),
            "how_to_cite": pub.get("how_to_cite") or h.get("how_to_cite"),
        }, indent=2))
        return 0

    if args.cmd == "certify":
        try:
            lic.assert_feature(data_dir, "certify", resolution=args.res)
        except PermissionError as e:
            print(f"LICENSE: {e}", file=sys.stderr)
            return 4
        out = Path(args.out) if args.out else None
        rep = certify(
            n=args.n,
            resolution=args.res,
            out_root=out,
            median_max_arcsec=args.median_max,
            p95_max_arcsec=args.p95_max,
            max_max_arcsec=args.max_max,
            oracle_median_max_arcsec=args.oracle_median_max,
        )
        print(rep.get("text") or json.dumps(rep, indent=2, default=str))
        return 0 if rep.get("passed") else 3

    if args.cmd == "holy-stack":
        from holy_hybrid_stacker import run_holy_hybrid
        from synthetic_hq import SynthSpec, generate
        import os
        out_root = Path(args.out) if args.out else default_out_root() / "holy_stack"
        out_root.mkdir(parents=True, exist_ok=True)
        run_dir = out_root / f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        run_dir.mkdir(parents=True, exist_ok=True)
        tmp = run_dir / "frames"
        tmp.mkdir(exist_ok=True)
        # Render N synthetic frames
        frames = []
        for k in range(args.n):
            spec = SynthSpec(
                user_time_iso="",
                region=args.region,
                resolution_preset=args.res,
                random_time=True,
                seed=args.seed * 1000 + k * 31,
                mode="metrology",
                write_grs_crop=False,
            )
            _png, fit, _truth = generate(spec, tmp)
            import grs_complete_system as grs
            arr, _ = grs.read_fits(fit)
            img = np.asarray(arr, dtype=np.float64)
            if img.ndim == 3 and img.shape[0] == 3:
                img = 0.3 * img[0] + 0.5 * img[1] + 0.2 * img[2]
            frames.append(img)
        res = run_holy_hybrid(
            frames, run_dir,
            n_grid=args.n_grid,
            ap_half=args.ap_half,
            n_importance=args.importance,
            auto_train=not args.no_train,
            seed=args.seed,
        )
        print(json.dumps(res.to_dict(), indent=2, default=str))
        return 0

    if args.cmd == "wj-derotate":
        from win_jupos_derotator import run_win_jupos_derotate
        from synthetic_hq import SynthSpec, generate
        out_root = Path(args.out) if args.out else default_out_root() / "wj_derotate"
        out_root.mkdir(parents=True, exist_ok=True)
        run_dir = out_root / f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        run_dir.mkdir(parents=True, exist_ok=True)
        tmp = run_dir / "frames"
        tmp.mkdir(exist_ok=True)
        frames = []
        for k in range(args.n):
            spec = SynthSpec(
                user_time_iso="",
                region="global",
                resolution_preset=args.res,
                random_time=True,
                seed=args.seed * 1000 + k * 31,
                mode="metrology",
                write_grs_crop=False,
            )
            _png, fit, _truth = generate(spec, tmp)
            import grs_complete_system as grs
            arr, _ = grs.read_fits(fit)
            img = np.asarray(arr, dtype=np.float64)
            if img.ndim == 3 and img.shape[0] == 3:
                img = 0.3 * img[0] + 0.5 * img[1] + 0.2 * img[2]
            frames.append(img)
        res = run_win_jupos_derotate(
            frames, run_dir,
            n_grid=args.n_grid,
            ap_half=args.ap_half,
            eq_band_frac=args.eq_band_frac,
        )
        print(json.dumps(res.to_dict(), indent=2, default=str))
        return 0

    if args.cmd == "zonal-stack":
        from jupiter_zonal_stacker import run_jupiter_zonal_stacker
        from synthetic_hq import SynthSpec, generate
        out_root = Path(args.out) if args.out else default_out_root() / "zonal_stack"
        out_root.mkdir(parents=True, exist_ok=True)
        run_dir = out_root / f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        run_dir.mkdir(parents=True, exist_ok=True)
        tmp = run_dir / "frames"
        tmp.mkdir(exist_ok=True)
        frames = []
        cm_list = []
        for k in range(args.n):
            spec = SynthSpec(
                user_time_iso="",
                region="global",
                resolution_preset=args.res,
                random_time=True,
                seed=args.seed * 1000 + k * 31,
                mode="metrology",
                write_grs_crop=False,
            )
            _png, fit, truth = generate(spec, tmp)
            import grs_complete_system as grs
            arr, _ = grs.read_fits(fit)
            img = np.asarray(arr, dtype=np.float64)
            if img.ndim == 3 and img.shape[0] == 3:
                img = 0.3 * img[0] + 0.5 * img[1] + 0.2 * img[2]
            frames.append(img)
            cm_list.append(float(truth["cm_iii_deg"]))
        # If cm-drift is set, use it for synthetic zonal-shear
        # (otherwise the dt-since-reference defaults to 0, which
        # means the per-row zonal-wind shift is zero, and the
        # stacker behaves like a generic AP-grid stacker).
        if args.cm_drift > 0:
            dt_list = [k * (args.cm_drift / (360.0 / 35729.7)) for k in range(args.n)]
        else:
            dt_list = [0.0] * args.n
        grs_xy = None
        if args.grs_xy:
            try:
                x_str, y_str = args.grs_xy.split(",")
                grs_xy = (float(x_str), float(y_str))
            except Exception:
                pass
        res = run_jupiter_zonal_stacker(
            frames, run_dir,
            n_grid=args.n_grid, ap_half=args.ap_half,
            cm_iii_deg=cm_list[0],
            distance_au=5.2,
            sub_lat_deg=0.0, north_pa_deg=0.0,
            cm_iii_per_frame=cm_list,
            dt_s_per_frame=dt_list,
            grs_xy=grs_xy,
        )
        print(json.dumps(res.to_dict(), indent=2, default=str))
        return 0

    if args.cmd == "zonal-derotate":
        from jupiter_zonal_derotator import run_jupiter_zonal_derotate
        from synthetic_hq import SynthSpec, generate
        out_root = Path(args.out) if args.out else default_out_root() / "zonal_derotate"
        out_root.mkdir(parents=True, exist_ok=True)
        run_dir = out_root / f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        run_dir.mkdir(parents=True, exist_ok=True)
        tmp = run_dir / "frames"
        tmp.mkdir(exist_ok=True)
        frames = []
        cm_list = []
        for k in range(args.n):
            spec = SynthSpec(
                user_time_iso="",
                region="global",
                resolution_preset=args.res,
                random_time=True,
                seed=args.seed * 1000 + k * 31,
                mode="metrology",
                write_grs_crop=False,
            )
            _png, fit, truth = generate(spec, tmp)
            import grs_complete_system as grs
            arr, _ = grs.read_fits(fit)
            img = np.asarray(arr, dtype=np.float64)
            if img.ndim == 3 and img.shape[0] == 3:
                img = 0.3 * img[0] + 0.5 * img[1] + 0.2 * img[2]
            frames.append(img)
            cm_list.append(float(truth["cm_iii_deg"]))
        if args.cm_drift > 0:
            dt_list = [k * (args.cm_drift / (360.0 / 35729.7)) for k in range(args.n)]
        else:
            dt_list = [0.0] * args.n
        res = run_jupiter_zonal_derotate(
            frames, run_dir,
            cm_iii_per_frame=cm_list,
            dt_s_per_frame=dt_list,
            mode=args.mode,
        )
        print(json.dumps(res.to_dict(), indent=2, default=str))
        return 0

    if args.cmd == "planet-stack":
        from planet_models import get_planet
        from planetary_stacker import run_planetary_stacker
        planet = get_planet(args.planet)
        frames, cm_list = _load_or_render_frames(args)
        out_root = Path(args.out) if args.out else default_out_root() / f"planet_stack_{planet.name.lower()}"
        out_root.mkdir(parents=True, exist_ok=True)
        res = run_planetary_stacker(
            frames, out_root, planet=planet,
            n_grid=args.n_grid, ap_half=args.ap_half,
            cm_iii_per_frame=cm_list, warp_mode=args.warp_mode,
            reference=args.reference, quality_gate=args.quality_gate,
        )
        print(json.dumps(res.to_dict(), indent=2, default=str))
        return 0

    if args.cmd == "planet-derotate":
        from planet_models import get_planet
        from planetary_derotator import run_planetary_derotate
        planet = get_planet(args.planet)
        frames, cm_list = _load_or_render_frames(args)
        out_root = Path(args.out) if args.out else default_out_root() / f"planet_derotate_{planet.name.lower()}"
        out_root.mkdir(parents=True, exist_ok=True)
        res = run_planetary_derotate(
            frames, out_root, planet=planet,
            n_grid=args.n_grid, ap_half=args.ap_half,
            cm_iii_per_frame=cm_list, mode=args.mode,
            reference=args.reference,
        )
        print(json.dumps(res.to_dict(), indent=2, default=str))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())

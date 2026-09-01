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

    # ------------------------------------------------------------------
    # v6.8.0 Observatory Pro commands
    # ------------------------------------------------------------------
    pvs = sub.add_parser(
        "video-stack",
        help="AutoStakkert-class APS stack from a SER/AVI capture: per-alignment-"
             "point quality maps + drizzle super-resolution + optional sharpen.",
    )
    pvs.add_argument("capture", help=".ser or .avi capture file")
    pvs.add_argument("--best", type=float, default=0.25, help="per-AP lucky fraction (0..1)")
    pvs.add_argument("--drizzle", type=int, default=1, choices=[1, 2, 3])
    pvs.add_argument("--pixfrac", type=float, default=1.0)
    pvs.add_argument("--ap-size", type=int, default=32)
    pvs.add_argument("--spacing", type=int, default=0)
    pvs.add_argument("--quality", default="laplacian",
                     choices=["laplacian", "gradient", "sobel", "contrast"])
    pvs.add_argument("--step", type=int, default=1)
    pvs.add_argument("--limit", type=int, default=0)
    pvs.add_argument("--downsample", type=int, default=1)
    pvs.add_argument("--align-downsample", type=int, default=1)
    pvs.add_argument("--derotate", default="none",
                     choices=["none", "prior", "hybrid", "measurement"],
                     help="per-latitude rotation derotation before stacking "
                          "(stamped SER, or --dt-per-frame for uniform cadence)")
    pvs.add_argument("--dt-per-frame", type=float, default=0.0,
                     help="seconds between frames when no stamps are available")
    pvs.add_argument("--sharpen", default="none", choices=["none", "wavelet", "rl", "unsharp"])
    pvs.add_argument("--out", default="")

    pas = sub.add_parser(
        "ap-stack",
        help="APS stack a folder of frames (PNG/JPG/FITS) with per-AP quality + drizzle.",
    )
    pas.add_argument("--frames-dir", required=True)
    pas.add_argument("--best", type=float, default=0.25)
    pas.add_argument("--drizzle", type=int, default=1, choices=[1, 2, 3])
    pas.add_argument("--pixfrac", type=float, default=1.0)
    pas.add_argument("--ap-size", type=int, default=32)
    pas.add_argument("--spacing", type=int, default=0)
    pas.add_argument("--quality", default="laplacian",
                     choices=["laplacian", "gradient", "sobel", "contrast"])
    pas.add_argument("--step", type=int, default=1)
    pas.add_argument("--limit", type=int, default=0)
    pas.add_argument("--derotate", default="none",
                     choices=["none", "prior", "hybrid", "measurement"])
    pas.add_argument("--dt-per-frame", type=float, default=0.0)
    pas.add_argument("--sharpen", default="none", choices=["none", "wavelet", "rl", "unsharp"])
    pas.add_argument("--out", default="")

    psh = sub.add_parser(
        "sharpen",
        help="Sharpen an image (RegiStax-style wavelets, Richardson-Lucy, unsharp).",
    )
    psh.add_argument("image")
    psh.add_argument("--method", default="wavelet", choices=["wavelet", "rl", "unsharp"])
    psh.add_argument("--gains", default="1.8,1.5,1.25,1.1,1.0")
    psh.add_argument("--rl-sigma", type=float, default=1.5)
    psh.add_argument("--rl-iters", type=int, default=14)
    psh.add_argument("--radius", type=float, default=2.5)
    psh.add_argument("--amount", type=float, default=1.0)
    psh.add_argument("--no-denoise", action="store_true")
    psh.add_argument("--out", default="")

    pt = sub.add_parser(
        "transits",
        help="GRS transit & Galilean-moon planner (WinJUPOS-style night sheet).",
    )
    pt.add_argument("--time", default="", help="start UTC (default: now)")
    pt.add_argument("--days", type=float, default=1.0)
    pt.add_argument("--moons", default="io,europa,ganymede,callisto")
    pt.add_argument("--json", action="store_true")

    pan = sub.add_parser(
        "animate",
        help="Export a blink/animation GIF (WinJUPOS-style derotation QA).",
    )
    pan.add_argument("frames", nargs="+", help="image paths (2+ for blink)")
    pan.add_argument("--out", required=True)
    pan.add_argument("--fps", type=float, default=4.0)
    pan.add_argument("--stretch", default="global", choices=["global", "per_frame"])
    pan.add_argument("--scale", type=int, default=1)
    pan.add_argument("--stamps", default="", help="comma-separated per-frame text")

    pje = sub.add_parser(
        "jupos-export",
        help="Export measurement packages to the JUPOS community CSV format.",
    )
    pje.add_argument("packages", nargs="+", help="package JSON files")
    pje.add_argument("--out", required=True)
    pje.add_argument("--observer", default="")
    pje.add_argument("--instrument", default="")
    pje.add_argument("--seeing", default="")

    pv2a = sub.add_parser(
        "video-to-answer",
        help="Production one-shot: SER/AVI capture -> APS drizzle stack -> sharpen "
             "-> published GRS measurement (SUPERDUPER card).",
    )
    pv2a.add_argument("capture")
    pv2a.add_argument("--time", default="", help="mid-exposure UTC (SER stamps auto-if-empty)")
    pv2a.add_argument("--best", type=float, default=0.25)
    pv2a.add_argument("--drizzle", type=int, default=1, choices=[1, 2, 3])
    pv2a.add_argument("--ap-size", type=int, default=32)
    pv2a.add_argument("--step", type=int, default=1)
    pv2a.add_argument("--limit", type=int, default=0)
    pv2a.add_argument("--downsample", type=int, default=1)
    pv2a.add_argument("--sharpen", default="wavelet", choices=["none", "wavelet", "rl", "unsharp"])
    pv2a.add_argument("--derotate", default="none",
                      choices=["none", "prior", "hybrid", "measurement"],
                      help="derotate before stacking (needs stamped SER); the "
                           "measurement epoch becomes the derotation ref frame")
    pv2a.add_argument("--no-nn", action="store_true", default=True)
    pv2a.add_argument("--out", default="")

    prgb = sub.add_parser(
        "rgb-combine",
        help="Filter-wheel RGB composite with exact ephemeris rotation "
             "derotation (WinJUPOS RGB-combine parity, AutoStakkert can't).",
    )
    prgb.add_argument("--r", required=True, help="red-channel mono stack image")
    prgb.add_argument("--g", required=True, help="green-channel mono stack image")
    prgb.add_argument("--b", required=True, help="blue-channel mono stack image")
    prgb.add_argument("--tr", default="", help="red mid-time UTC ISO")
    prgb.add_argument("--tg", default="", help="green mid-time UTC ISO")
    prgb.add_argument("--tb", default="", help="blue mid-time UTC ISO")
    prgb.add_argument("--dt-r", type=float, default=0.0,
                      help="red offset from green, seconds (times unknown)")
    prgb.add_argument("--dt-b", type=float, default=0.0,
                      help="blue offset from green, seconds")
    prgb.add_argument("--planet", default="jupiter")
    prgb.add_argument("--a-eq-px", type=float, default=0.0,
                      help="equatorial radius px (default: fit from G stack)")
    prgb.add_argument("--sub-lat", type=float, default=0.0,
                      help="sub-Earth latitude deg at session")
    prgb.add_argument("--north-pa", type=float, default=0.0,
                      help="north pole PA deg E of N at session")
    prgb.add_argument("--no-winds", action="store_true")
    prgb.add_argument("--no-polish", action="store_true")
    prgb.add_argument("--out", default="")

    pfw = sub.add_parser(
        "filter-wheel",
        help="Full mono filter workflow: R/G/B SER or AVI -> per-filter APS "
             "stacks -> rotation-derotated RGB composite + reports.",
    )
    pfw.add_argument("--r", required=True)
    pfw.add_argument("--g", required=True)
    pfw.add_argument("--b", required=True)
    pfw.add_argument("--planet", default="jupiter")
    pfw.add_argument("--sub-lat", type=float, default=0.0)
    pfw.add_argument("--north-pa", type=float, default=0.0)
    pfw.add_argument("--derotate", default="hybrid",
                     choices=["off", "prior", "hybrid", "measurement"])
    pfw.add_argument("--best", type=float, default=0.35)
    pfw.add_argument("--limit", type=int, default=0)
    pfw.add_argument("--out", required=True)

    pwa = sub.add_parser(
        "wind-analysis",
        help="Cloud-tracking wind science from a video-stack report: profile "
             "fit, System-III check, jets, CSV + PNG panel.",
    )
    pwa.add_argument("report", help="video-stack report JSON (with wind_report)")
    pwa.add_argument("--planet", default="jupiter")
    pwa.add_argument("--png", default="")
    pwa.add_argument("--csv", default="")

    pdr = sub.add_parser(
        "drift",
        help="GRS System-II drift fit from JUPOS CSV epochs: rate, curvature "
             "F-test, zonal velocity, prediction.",
    )
    pdr.add_argument("csv", help="JUPOS-format CSV with L_II epochs")
    pdr.add_argument("--lat", type=float, default=-20.0,
                     help="GRS centre latitude (planetocentric)")
    pdr.add_argument("--planet", default="jupiter")
    pdr.add_argument("--object", default="GRS")
    pdr.add_argument("--predict-days", type=float, default=30.0)
    pdr.add_argument("--png", default="")
    pdr.add_argument("--csv-out", default="")

    psp = sub.add_parser(
        "session-plan",
        help="Physics-derived session budget: smear spans, filter gaps, "
             "tonight's GRS windows.",
    )
    psp.add_argument("--time", default="", help="start UTC (default: now)")
    psp.add_argument("--hours", type=float, default=8.0)
    psp.add_argument("--planet", default="jupiter")
    psp.add_argument("--a-eq-px", type=float, default=0.0)
    psp.add_argument("--budget-px", type=float, default=1.0)
    psp.add_argument("--lat", type=float, default=-20.0)
    psp.add_argument("--png", default="")

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

    # ------------------------------------------------------------------
    # v6.8.0 Observatory Pro handlers
    # ------------------------------------------------------------------
    if args.cmd == "video-stack":
        import observatory_pipeline as op
        rep = op.stack_video(
            args.capture,
            out_dir=Path(args.out) if args.out else default_out_root() / "video_stack",
            keep_frac=args.best, drizzle=args.drizzle, ap_size=args.ap_size,
            spacing=args.spacing, quality=args.quality, pixfrac=args.pixfrac,
            step=args.step, limit=args.limit, downsample=args.downsample,
            align_downsample=args.align_downsample, sharpen_method=args.sharpen,
            derotate=args.derotate,
            dt_per_frame_s=(args.dt_per_frame or None),
        )
        print(json.dumps(rep, indent=2, default=str))
        return 0

    if args.cmd == "ap-stack":
        import observatory_pipeline as op
        rep = op.stack_video(
            None,
            frames_dir=args.frames_dir,
            out_dir=Path(args.out) if args.out else default_out_root() / "ap_stack",
            keep_frac=args.best, drizzle=args.drizzle, ap_size=args.ap_size,
            spacing=args.spacing, quality=args.quality, step=args.step,
            limit=args.limit, sharpen_method=args.sharpen, pixfrac=args.pixfrac,
            derotate=args.derotate,
            dt_per_frame_s=(args.dt_per_frame or None),
        )
        print(json.dumps(rep, indent=2, default=str))
        return 0

    if args.cmd == "sharpen":
        import observatory_pipeline as op
        gains = tuple(float(x) for x in str(args.gains).split(",") if x.strip())
        rep = op.sharpen_file(
            args.image, method=args.method, out=args.out or None,
            gains=gains, rl_sigma=args.rl_sigma, rl_iters=args.rl_iters,
            radius=args.radius, amount=args.amount, denoise=not args.no_denoise,
        )
        print(json.dumps(rep, indent=2, default=str))
        return 0

    if args.cmd == "transits":
        import transits as _tr
        start = args.time or None
        moons = tuple(m.strip() for m in args.moons.split(",") if m.strip())
        plan = _tr.night_planner(start or __import__("datetime").datetime.now(), days=args.days, moons=moons)
        if args.json:
            print(json.dumps(plan, indent=2, default=str))
        else:
            print(_tr.planner_text(plan))
        return 0

    if args.cmd == "animate":
        import observatory_pipeline as op
        stamps = [s.strip() for s in args.stamps.split(",") if s.strip()] or None
        rep = op.animate_frames(args.frames, args.out, fps=args.fps,
                                stamps=stamps, stretch=args.stretch, scale=args.scale)
        print(json.dumps(rep, indent=2, default=str))
        return 0

    if args.cmd == "jupos-export":
        import observatory_pipeline as op
        packages = []
        for f in args.packages:
            try:
                packages.append(json.loads(Path(f).read_text(encoding="utf-8")))
            except Exception as e:
                print(f"WARNING: {f}: {e}", file=sys.stderr)
        rep = op.export_jupos(
            packages, args.out,
            observer=args.observer, instrument=args.instrument, seeing=args.seeing,
        )
        print(json.dumps(rep, indent=2, default=str))
        return 0

    if args.cmd == "video-to-answer":
        import observatory_pipeline as op
        rep = op.video_to_answer(
            args.capture,
            time_utc=args.time or None,
            keep_frac=args.best, drizzle=args.drizzle, ap_size=args.ap_size,
            step=args.step, limit=args.limit, downsample=args.downsample,
            sharpen_method=args.sharpen,
            derotate=args.derotate,
            out_root=Path(args.out) if args.out else None,
        )
        def _slim(d, depth=0):
            if isinstance(d, dict):
                return {k: _slim(v, depth + 1) for k, v in d.items()
                        if k not in ("notes", "all_methods", "debug", "raw")}
            if isinstance(d, list) and len(d) > 12:
                return d[:12] + [f"...({len(d) - 12} more)"]
            return d
        print(json.dumps(_slim(rep), indent=2, default=str))
        return 0

    if args.cmd == "rgb-combine":
        import numpy as _np
        import rgb_combine as _rc
        from planet_models import get_planet as _gp
        from precision_engine import NavState, fit_limb_nav, to_mono
        from PIL import Image as _Image

        def _load_mono(pth):
            im = _np.asarray(_Image.open(pth))
            if im.dtype == _np.uint8:
                im = im.astype(_np.float64) / 255.0
            else:
                im = im.astype(_np.float64)
            return to_mono(im)

        g_img = _load_mono(args.g)
        r_img = _load_mono(args.r)
        b_img = _load_mono(args.b)
        for name, im in (("R", r_img), ("B", b_img)):
            if im.shape != g_img.shape:
                print(f"ERROR: {name} shape {im.shape} != G shape {g_img.shape}",
                      file=sys.stderr)
                return 1
        planet = _gp(args.planet)
        nav = fit_limb_nav(g_img, cm_iii_deg=0.0,
                           distance_au=planet.default_distance_au,
                           north_pa_deg=args.north_pa)
        nav.flattening = planet.flattening
        nav.sub_lat_deg = float(args.sub_lat)
        nav.north_pa_deg = float(args.north_pa)
        if args.a_eq_px and args.a_eq_px > 0:
            nav.a_eq_px = float(args.a_eq_px)

        def _tm(s):
            import datetime as _dt
            try:
                return _dt.datetime.fromisoformat(s).timestamp() if s else None
            except ValueError:
                return None

        tg = _tm(args.tg)
        if tg is not None:
            t_ref = tg
            tr = _tm(args.tr) if _tm(args.tr) is not None else t_ref + args.dt_r
            tb = _tm(args.tb) if _tm(args.tb) is not None else t_ref + args.dt_b
        else:
            t_ref = 0.0
            tr, tb = float(args.dt_r), float(args.dt_b)
            if abs(tr) + abs(tb) < 1e-9:
                print("NOTE: no times given — combined at dt=0 (no rotation "
                      "compensation needed if stacks share an epoch)",
                      file=sys.stderr)
        cfg = _rc.RGBCombineConfig(include_winds=not args.no_winds,
                                   band_polish=not args.no_polish)
        res = _rc.combine_rgb(r_img, g_img, b_img, tr, t_ref, tb,
                              planet, nav, t_ref_s=t_ref, cfg=cfg)
        print(_rc.combine_report_text(res))
        out = Path(args.out) if args.out else default_out_root() / "rgb_combine"
        out.mkdir(parents=True, exist_ok=True)
        from observatory_pipeline import _save_png
        rgb_path = _save_png(out / "rgb.png", res.rgb)
        (out / "rgb_report.json").write_text(
            json.dumps(res.report, indent=2, default=str), encoding="utf-8")
        print(f"rgb: {rgb_path}\nreport: {out / 'rgb_report.json'}")
        return 0

    if args.cmd == "filter-wheel":
        from filter_wheel import run_filter_wheel
        from planet_models import get_planet as _gp
        from ap_stacker import APStackConfig
        res = run_filter_wheel(
            {"R": args.r, "G": args.g, "B": args.b}, args.out,
            planet=_gp(args.planet), sub_lat_deg=args.sub_lat,
            north_pa_deg=args.north_pa, derotate_mode=args.derotate,
            max_frames_per_capture=args.limit,
            stack_cfg=APStackConfig(ap_size_px=32, keep_frac=args.best))
        print(json.dumps(res.to_dict(), indent=2, default=str))
        return 0

    if args.cmd == "wind-analysis":
        from planet_models import get_planet as _gp
        from wind_analysis import (wind_report_text, render_profile_png,
                                   export_profile_csv, detect_jets,
                                   summarize_profile)
        rep = json.loads(Path(args.report).read_text(encoding="utf-8"))
        wr = rep.get("wind_report")
        if not wr:
            print("ERROR: report has no wind_report block (stack with "
                  "--derotate measurement/hybrid)", file=sys.stderr)
            return 1
        planet = _gp(args.planet)
        print(wind_report_text(planet, wr))
        outs = {}
        if args.png:
            outs["png"] = render_profile_png(wr, args.png, jets=detect_jets(wr))
        if args.csv:
            outs["csv"] = export_profile_csv(
                wr, args.csv, summary=summarize_profile(planet, wr))
        if outs:
            print("artefacts:", json.dumps(outs, indent=2))
        return 0

    if args.cmd == "drift":
        from planet_models import get_planet as _gp
        from grs_drift import (points_from_jupos_csv, fit_drift, predict,
                               drift_report_text, zonal_velocity_mps,
                               render_drift_png, export_drift_csv)
        import datetime as _dt
        planet = _gp(args.planet)
        pts = points_from_jupos_csv(args.csv, want_object=(args.object,))
        if len(pts) < 3:
            print(f"ERROR: only {len(pts)} usable {args.object} epochs in "
                  f"{args.csv}", file=sys.stderr)
            return 1
        fit = fit_drift(pts, lat_ref_deg=args.lat)
        print(drift_report_text(fit, planet=planet))
        if args.predict_days > 0:
            t_f = pts[-1].t_utc + _dt.timedelta(days=args.predict_days)
            prd = predict(fit, t_f, points=pts)
            print(f"prediction +{args.predict_days:.0f}d: L_II "
                  f"{prd['lon_ii_deg']:.1f} +- {prd['sigma_deg']:.1f} deg "
                  f"({prd['model']})")
        outs = {}
        if args.png:
            outs["png"] = render_drift_png(pts, fit, args.png)
        if args.csv_out:
            outs["csv"] = export_drift_csv(pts, fit, args.csv_out)
        if outs:
            print("artefacts:", json.dumps(outs, indent=2))
        return 0

    if args.cmd == "session-plan":
        from planet_models import get_planet as _gp
        from session_planner import session_plan, plan_text, render_plan_png
        import datetime as _dt
        start = (_dt.datetime.fromisoformat(args.time) if args.time
                 else _dt.datetime.utcnow())
        plan = session_plan(start, args.hours, planet=_gp(args.planet),
                            a_eq_px=args.a_eq_px, budget_px=args.budget_px,
                            lat_of_interest_deg=args.lat)
        print(plan_text(plan))
        if args.png:
            print("panel:", render_plan_png(plan, args.png))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())

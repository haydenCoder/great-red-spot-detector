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
from pathlib import Path


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

    return 1


if __name__ == "__main__":
    raise SystemExit(main())

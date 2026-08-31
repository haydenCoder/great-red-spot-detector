#!/usr/bin/env python3
"""
SPICE kernel auto-discovery + online download (zero user kernel hunting)
======================================================================

Most planetary-imaging users will never find NAIF kernels by hand. This module:

  1) Ensures spiceypy is importable
  2) Auto-downloads the minimal generic kernel set for Jupiter observer geometry
  3) Verifies kernels load (furnsh) and returns a ready kernel set
  4) Computes observer→Jupiter geometry at an epoch (distance, light-time,
     sub-observer lon/lat in IAU_JUPITER ≈ System III body frame)
  5) Caches under app/ephemeris_data/spice/  (or $GRS_SPICE_KERNELS)

Mirrors (tried in order):
  - NAIF public generic_kernels
  - NAIF /pub/naif mirror path variants

This is the only absolute-geometry path the observatory should rely on for
publication-grade System III work when no WinJUPOS override is pasted.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from verbose_log import CONSOLE
from netutil import secure_ssl_context as _ssl_context

APP_DIR = Path(__file__).resolve().parent
DEFAULT_KERNEL_DIR = Path(
    os.environ.get("GRS_SPICE_KERNELS", str(APP_DIR / "ephemeris_data" / "spice"))
)
MANIFEST_NAME = "kernel_manifest.json"
AU_KM = 149597870.7
JUP_REQ_KM = 71492.0
C_KM_S = 299792.458

# Minimal set for Earth–Jupiter geometry + IAU body orientation
# de440s: compact planetary SPK (~30–100 MB)
# naif0012.tls: leap seconds
# pck00011.tpc: orientation / radii constants (IAU frames)
# gm_de440.tpc: GM constants (optional but nice)
KERNEL_CATALOG: List[Dict[str, Any]] = [
    {
        "name": "naif0012.tls",
        "kind": "lsk",
        "required": True,
        "urls": [
            "https://naif.jpl.nasa.gov/pub/naif/generic_kernels/lsk/naif0012.tls",
            "https://naif.jpl.nasa.gov/pub/naif/generic_kernels/lsk/naif0012.tls.pc",
        ],
        "min_bytes": 4_000,
    },
    {
        "name": "pck00011.tpc",
        "kind": "pck",
        "required": True,
        "urls": [
            "https://naif.jpl.nasa.gov/pub/naif/generic_kernels/pck/pck00011.tpc",
            "https://naif.jpl.nasa.gov/pub/naif/generic_kernels/pck/pck00010.tpc",
        ],
        "min_bytes": 50_000,
        "alt_names": ["pck00010.tpc"],
    },
    {
        "name": "gm_de440.tpc",
        "kind": "pck",
        "required": False,
        "urls": [
            "https://naif.jpl.nasa.gov/pub/naif/generic_kernels/pck/gm_de440.tpc",
        ],
        "min_bytes": 1_000,
    },
    {
        "name": "de440s.bsp",
        "kind": "spk",
        "required": True,
        "urls": [
            "https://naif.jpl.nasa.gov/pub/naif/generic_kernels/spk/planets/de440s.bsp",
            "https://naif.jpl.nasa.gov/pub/naif/generic_kernels/spk/planets/a_old_versions/de430s.bsp",
        ],
        "min_bytes": 5_000_000,
        "alt_names": ["de430s.bsp", "de441.bsp"],
    },
]


@dataclass
class SpiceStatus:
    ok: bool
    spiceypy: bool
    kernel_dir: str
    kernels_loaded: List[str] = field(default_factory=list)
    missing_required: List[str] = field(default_factory=list)
    downloaded: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    last_error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class SpiceGeometry:
    t_utc_iso: str
    distance_au: float
    light_time_s: float
    cm_iii_deg: float
    sub_obs_lon_deg: float
    sub_obs_lat_deg: float
    apparent_diameter_arcsec: float
    north_pa_deg: float = 0.0
    source: str = "spice_auto"
    body: str = "JUPITER"
    frame: str = "IAU_JUPITER"
    kernels: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)




def _sha256_file(path: Path, max_bytes: int = 32_000_000) -> str:
    h = hashlib.sha256()
    n = 0
    with open(path, "rb") as f:
        while True:
            chunk = f.read(1 << 20)
            if not chunk:
                break
            h.update(chunk)
            n += len(chunk)
            if n >= max_bytes:
                break
    return h.hexdigest()


def has_spiceypy() -> bool:
    try:
        import spiceypy  # noqa: F401
        return True
    except Exception:
        return False


def kernel_dir(path: Optional[Path] = None) -> Path:
    d = Path(path or DEFAULT_KERNEL_DIR)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _existing_kernel(kdir: Path, entry: Dict[str, Any]) -> Optional[Path]:
    names = [entry["name"]] + list(entry.get("alt_names") or [])
    for n in names:
        p = kdir / n
        if p.exists() and p.stat().st_size >= int(entry.get("min_bytes", 100)):
            return p
    # also accept any matching extension glob for SPK if name differs
    if entry["kind"] == "spk":
        for p in sorted(kdir.glob("de*.bsp")):
            if p.stat().st_size >= int(entry.get("min_bytes", 100)):
                return p
    if entry["kind"] == "pck" and "pck" in entry["name"]:
        for p in sorted(kdir.glob("pck*.tpc")):
            if p.stat().st_size >= 10_000:
                return p
    if entry["kind"] == "lsk":
        for p in sorted(kdir.glob("naif*.tls*")):
            if p.stat().st_size >= 1_000:
                return p
    return None


def _download(url: str, dest: Path, timeout: float = 120.0) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".partial")
    headers = {
        "User-Agent": "GRS-Observatory-SPICE-Auto/5.0 (research; +https://naif.jpl.nasa.gov)",
    }
    last_err: Optional[Exception] = None
    for label, ctx in (
        ("secure", _ssl_context()),
        ("unverified", ssl._create_unverified_context()),
    ):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                total = 0
                with open(tmp, "wb") as f:
                    while True:
                        chunk = resp.read(1 << 20)
                        if not chunk:
                            break
                        f.write(chunk)
                        total += len(chunk)
            if total < 100:
                raise RuntimeError(f"download too small ({total} B)")
            tmp.replace(dest)
            if label == "unverified":
                CONSOLE.warn(f"SPICE download used unverified SSL: {dest.name}")
            return
        except Exception as e:
            last_err = e
            try:
                if tmp.exists():
                    tmp.unlink()
            except Exception:
                pass
    raise RuntimeError(f"download failed {url}: {last_err}")


def ensure_kernels(
    kdir: Optional[Path] = None,
    force: bool = False,
    timeout: float = 180.0,
    allow_download: bool = False,
) -> SpiceStatus:
    """
    Search local bundled kernel cache. Online download is OFF by default
    (release ships kernels under ephemeris_data/spice/).
    """
    kdir = kernel_dir(kdir)
    notes: List[str] = []
    downloaded: List[str] = []
    missing: List[str] = []
    loaded: List[str] = []

    if not has_spiceypy():
        return SpiceStatus(
            ok=False,
            spiceypy=False,
            kernel_dir=str(kdir),
            notes=["spiceypy not installed — pip install spiceypy"],
            last_error="no_spiceypy",
        )

    for entry in KERNEL_CATALOG:
        existing = None if force else _existing_kernel(kdir, entry)
        if existing is not None:
            loaded.append(existing.name)
            notes.append(f"found local {existing.name} ({existing.stat().st_size} B)")
            continue
        if not allow_download:
            if entry.get("required"):
                missing.append(entry["name"])
                notes.append(
                    f"REQUIRED missing {entry['name']} (online download disabled — "
                    "ship kernels under ephemeris_data/spice/)"
                )
            else:
                notes.append(f"optional missing {entry['name']} (download disabled)")
            continue
        # optional legacy download path (disabled in production default)
        ok = False
        last = ""
        for url in entry["urls"]:
            try:
                dest_name = entry["name"]
                url_base = url.rstrip("/").split("/")[-1].replace(".pc", "")
                if url_base.endswith(".tpc") or url_base.endswith(".bsp") or url_base.endswith(".tls"):
                    dest_name = url_base
                dest = kdir / dest_name
                CONSOLE.info(f"SPICE online fetch: {dest_name} …")
                _download(url, dest, timeout=timeout)
                if dest.stat().st_size < int(entry.get("min_bytes", 100)):
                    dest.unlink(missing_ok=True)
                    raise RuntimeError("file smaller than min_bytes")
                downloaded.append(dest.name)
                loaded.append(dest.name)
                notes.append(f"downloaded {dest.name} from {url}")
                CONSOLE.ok(f"SPICE kernel ready: {dest.name} ({dest.stat().st_size/1e6:.1f} MB)")
                ok = True
                break
            except Exception as e:
                last = str(e)
                CONSOLE.debug(f"SPICE fetch fail {url}: {e}")
        if not ok:
            if entry.get("required"):
                missing.append(entry["name"])
                notes.append(f"REQUIRED missing {entry['name']}: {last}")
            else:
                notes.append(f"optional missing {entry['name']}: {last}")

    # write manifest
    man = {
        "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "kernel_dir": str(kdir),
        "kernels": loaded,
        "downloaded_this_call": downloaded,
        "missing_required": missing,
        "notes": notes,
    }
    try:
        (kdir / MANIFEST_NAME).write_text(json.dumps(man, indent=2), encoding="utf-8")
    except Exception:
        pass

    ok = len(missing) == 0 and has_spiceypy()
    st = SpiceStatus(
        ok=ok,
        spiceypy=True,
        kernel_dir=str(kdir),
        kernels_loaded=loaded,
        missing_required=missing,
        downloaded=downloaded,
        notes=notes,
        last_error="" if ok else f"missing: {missing}",
    )
    if ok:
        CONSOLE.ok(f"SPICE kernel set ready ({len(loaded)} files) in {kdir}")
    else:
        CONSOLE.warn(f"SPICE incomplete: missing {missing}")
    return st


def list_local_kernels(kdir: Optional[Path] = None) -> List[Path]:
    kdir = kernel_dir(kdir)
    exts = (".bsp", ".tls", ".tpc", ".tf", ".ti", ".bpc")
    out: List[Path] = []
    for p in sorted(kdir.iterdir()) if kdir.exists() else []:
        if p.is_file() and p.suffix.lower() in exts:
            out.append(p)
    return out


def _furnsh_all(kdir: Path) -> List[str]:
    import spiceypy as spice

    spice.kclear()
    names: List[str] = []
    # order: lsk, pck, spk, rest
    order_key = {".tls": 0, ".tpc": 1, ".tf": 2, ".bsp": 3}
    paths = list_local_kernels(kdir)
    paths.sort(key=lambda p: (order_key.get(p.suffix.lower(), 9), p.name))
    for p in paths:
        try:
            spice.furnsh(str(p))
            names.append(p.name)
        except Exception as e:
            CONSOLE.debug(f"furnsh skip {p.name}: {e}")
    return names


def wrap_deg(x: float) -> float:
    return float(x % 360.0)


def compute_spice_geometry(
    t_utc,
    kdir: Optional[Path] = None,
    auto_download: bool = False,
    observer: str = "EARTH",
    target: str = "JUPITER",
) -> Optional[SpiceGeometry]:
    """
    Full SPICE geometry at UTC datetime or ISO string.

    Sub-observer lon/lat in IAU_JUPITER body-fixed frame.
    CM III is taken as the body-fixed sub-observer west-style longitude
    (convention: wrap_deg of atan2 body-frame observer direction).
    """
    import datetime as dt

    if isinstance(t_utc, str):
        s = t_utc.strip().replace("T", " ").replace("Z", "")
        for fmt in (
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
        ):
            try:
                n = 26 if "%f" in fmt else (19 if "%H" in fmt else 10)
                t_utc = dt.datetime.strptime(s[:n], fmt)
                break
            except Exception:
                continue
        else:
            raise ValueError(f"bad time {t_utc}")

    kdir = kernel_dir(kdir)
    st = ensure_kernels(kdir, allow_download=bool(auto_download))
    if not st.ok:
        CONSOLE.warn(f"SPICE kernels not ready: {st.last_error or st.missing_required}")
        return None
    elif not has_spiceypy():
        return None

    try:
        import spiceypy as spice
    except Exception:
        return None

    notes: List[str] = []
    try:
        knames = _furnsh_all(kdir)
        if not knames:
            notes.append("no kernels furnished")
            return None

        # Keep subseconds when present (strftime %S alone truncates → ~0.5s Sys III error)
        try:
            et_str = t_utc.strftime("%Y-%m-%dT%H:%M:%S.%f")
        except Exception:
            et_str = t_utc.strftime("%Y-%m-%dT%H:%M:%S")
        et = spice.str2et(et_str)
        # de440s has Jupiter *barycenter* (5), not always body 599 — use barycenter
        dist_au = None
        lt = None
        used_target = "JUPITER BARYCENTER"
        state = None
        for tgt in ("JUPITER BARYCENTER", "5", target, "JUPITER", "599"):
            try:
                state, lt_s = spice.spkezr(tgt, et, "J2000", "LT+S", observer)
                dist_km = math.sqrt(state[0] ** 2 + state[1] ** 2 + state[2] ** 2)
                dist_au = dist_km / AU_KM
                lt = float(lt_s)
                used_target = tgt
                break
            except Exception as e:
                notes.append(f"spkezr {tgt}: {e}")
        if dist_au is None or lt is None or state is None:
            return None

        # Body-fixed sub-observer WITHOUT requiring SPK for body 599:
        # transform Earth←Jupiter vector (J2000) into IAU_JUPITER at light-time epoch.
        # state = position of target relative to observer → vector target→observer = -state
        cm = 0.0
        sublat = 0.0
        sublon = 0.0
        body_ok = False
        try:
            mat = spice.pxform("J2000", "IAU_JUPITER", et - lt)
            # observer position in body frame relative to target centre
            obs_j2000 = [-float(state[0]), -float(state[1]), -float(state[2])]
            pos = spice.mxv(mat, obs_j2000)
            rxy = math.hypot(pos[0], pos[1])
            lon_e = math.degrees(math.atan2(pos[1], pos[0]))
            lat = math.degrees(math.atan2(pos[2], rxy + 1e-12))
            # West longitude 0–360 (common planetary CM III convention)
            sublon = wrap_deg(-lon_e)
            sublat = float(lat)
            cm = sublon
            body_ok = True
            notes.append("body frame via pxform(J2000→IAU_JUPITER) on barycenter LOS (de440s-safe)")
        except Exception as e:
            notes.append(f"pxform body frame failed: {e}")

        diam = math.degrees(2 * JUP_REQ_KM / (dist_au * AU_KM)) * 3600.0

        # North pole position angle on sky
        north_pa = 0.0
        try:
            mat_p = spice.pxform("IAU_JUPITER", "J2000", et - lt)
            pole_j2000 = spice.mxv(mat_p, [0.0, 0.0, 1.0])
            los_n = spice.vhat(state[:3])
            pole = spice.vhat(pole_j2000)
            proj = spice.vsub(pole, spice.vscl(spice.vdot(pole, los_n), los_n))
            if spice.vnorm(proj) > 1e-12:
                cel_n = spice.vsub([0, 0, 1], spice.vscl(los_n[2], los_n))
                if spice.vnorm(cel_n) > 1e-12:
                    cel_n = spice.vhat(cel_n)
                    cel_e = spice.vhat(spice.vcrss(los_n, cel_n))
                    pn = spice.vdot(spice.vhat(proj), cel_n)
                    pe = spice.vdot(spice.vhat(proj), cel_e)
                    north_pa = wrap_deg(math.degrees(math.atan2(pe, pn)))
                    notes.append("north_pa from pole projection")
        except Exception as e:
            notes.append(f"north_pa skip: {e}")

        # Never emit CM=0 when body frame failed — that silently corrupts System III.
        # Callers must treat non-finite CM as "distance only" (no absolute lon).
        if body_ok:
            cm_out = float(cm)
            sublon_out = float(sublon)
            sublat_out = float(sublat)
        else:
            cm_out = float("nan")
            sublon_out = float("nan")
            sublat_out = float("nan")
            notes.append("CM/sub-lon unset (NaN): body frame failed — do not use as absolute Sys III")
        geom = SpiceGeometry(
            t_utc_iso=t_utc.isoformat(),
            distance_au=float(dist_au),
            light_time_s=float(lt),
            cm_iii_deg=cm_out,
            sub_obs_lon_deg=sublon_out,
            sub_obs_lat_deg=sublat_out,
            apparent_diameter_arcsec=float(diam),
            north_pa_deg=float(north_pa),
            source="spice_auto" if body_ok else "spice_auto_distance_only",
            body=used_target,
            frame="IAU_JUPITER",
            kernels=knames,
            notes=notes,
            raw={
                "body_ok": body_ok,
                "et": float(et),
                "observer": observer,
            },
        )
        cm_s = f"{geom.cm_iii_deg:.4f}°" if math.isfinite(geom.cm_iii_deg) else "NaN (distance-only)"
        CONSOLE.ok(
            f"SPICE geometry: Δ={geom.distance_au:.6f} AU  CM≈{cm_s}  "
            f"sublat={geom.sub_obs_lat_deg if math.isfinite(geom.sub_obs_lat_deg) else float('nan'):.4f}°  "
            f"diam={geom.apparent_diameter_arcsec:.3f}″  "
            f"[{geom.source}]"
        )
        return geom
    except Exception as e:
        CONSOLE.warn(f"SPICE geometry failed: {e}")
        return None
    finally:
        try:
            import spiceypy as spice
            spice.kclear()
        except Exception:
            pass


def selftest() -> Dict[str, Any]:
    """Download kernels if needed and evaluate one epoch."""
    import datetime as dt

    st = ensure_kernels()
    t = dt.datetime(2020, 7, 14, 12, 0, 0)
    g = compute_spice_geometry(t, auto_download=False)
    return {
        "status": st.to_dict(),
        "geometry": None if g is None else g.to_dict(),
        "ok": bool(st.ok and g is not None and g.distance_au > 3.0),
    }


if __name__ == "__main__":
    import json as _json

    print(_json.dumps(selftest(), indent=2, default=str))

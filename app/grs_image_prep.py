#!/usr/bin/env python3
"""
GRS image prep for real amateur stacks
======================================

Fixes that routinely break auto GRS metrology:

  1) AutoStakkert FITS with **no DATE-OBS** → parse time from filename
     e.g. 2026-01-09-1540_…fit → 2026-01-09 15:40:00 UTC
  2) **Moon / satellite shadow** near GRS → compact dark blob mask
  3) **Orange GRS** (not black) → red/orange-as-dark measure mono
  4) **N–S flipped** stacks → auto flip when reddish oval is in the north

Used by desktop_pipeline Process before limb / map measurement.
"""
from __future__ import annotations

import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import numpy as np

from precision_engine import fit_limb_nav, px_to_lonlat, to_mono


def parse_time_from_filename(path: str | Path) -> Tuple[Optional[datetime], str]:
    """
    Common AutoStakkert / planetary naming:
      2026-01-09-1540_…      → 2026-01-09 15:40:00
      2026-01-09-15-40-26_…  → 2026-01-09 15:40:26
      20260109T154026_…      → 2026-01-09 15:40:26
      2026_01_09_1540_…
    """
    name = Path(path).name
    patterns = [
        # 2026-01-09-154026 or 2026-01-09-1540
        (r"(20\d{2})-(\d{2})-(\d{2})-(\d{2})(\d{2})(\d{2})?", "%Y-%m-%d %H%M%S"),
        (r"(20\d{2})-(\d{2})-(\d{2})-(\d{2})-(\d{2})-(\d{2})", "%Y-%m-%d %H-%M-%S"),
        (r"(20\d{2})-(\d{2})-(\d{2})-(\d{2})-(\d{2})", "%Y-%m-%d %H-%M"),
        (r"(20\d{2})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})", "%Y%m%dT%H%M%S"),
        (r"(20\d{2})(\d{2})(\d{2})[_-](\d{2})(\d{2})(\d{2})?", "%Y%m%d_%H%M%S"),
        (r"(20\d{2})_(\d{2})_(\d{2})_(\d{2})(\d{2})", "%Y_%m_%d_%H%M"),
    ]
    for pat, _ in patterns:
        m = re.search(pat, name)
        if not m:
            continue
        g = m.groups()
        try:
            if len(g) >= 6 and g[5]:
                y, mo, d, hh, mm, ss = g[0], g[1], g[2], g[3], g[4], g[5]
            elif len(g) >= 5:
                y, mo, d, hh, mm = g[0], g[1], g[2], g[3], g[4]
                ss = "00"
            else:
                continue
            # handle 1540 style where hhmm is combined already matched as hh,mm
            if len(hh) == 4 and not mm:
                pass
            dt = datetime(int(y), int(mo), int(d), int(hh), int(mm), int(ss or 0))
            return dt, f"filename:{m.group(0)}"
        except Exception:
            continue
    # 2026-01-09-1540 with hhmm glued
    m = re.search(r"(20\d{2})-(\d{2})-(\d{2})-(\d{4})(?:\D|$)", name)
    if m:
        try:
            y, mo, d, hm = m.groups()
            dt = datetime(int(y), int(mo), int(d), int(hm[:2]), int(hm[2:4]), 0)
            return dt, f"filename:{m.group(0)}"
        except Exception:
            pass
    return None, "no_filename_time"


def _as_hwc_rgb(image: np.ndarray) -> Optional[np.ndarray]:
    a = np.asarray(image, dtype=np.float64)
    if a.ndim == 3 and a.shape[0] in (3, 4) and a.shape[0] < min(a.shape[1], a.shape[2]):
        a = np.transpose(a, (1, 2, 0))  # CHW → HWC
    if a.ndim == 3 and a.shape[-1] >= 3:
        return a[..., :3]
    return None


def _norm01(x: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=np.float64)
    lo, hi = np.percentile(x, (1.0, 99.5))
    if not math.isfinite(lo) or not math.isfinite(hi) or hi <= lo:
        m = float(np.nanmax(x)) or 1.0
        return np.clip(x / m, 0, 1)
    return np.clip((x - lo) / (hi - lo + 1e-12), 0, 1)


def mask_satellite_shadows(
    mono: np.ndarray,
    *,
    disk_mask: Optional[np.ndarray] = None,
) -> np.ndarray:
    """
    Boolean mask of compact dark blobs (moon umbra / satellites).
    True = pixel is moon/shadow — should not drive GRS dark centroid.
    """
    im = np.asarray(mono, dtype=np.float64)
    if im.ndim != 2:
        im = to_mono(im)
    h, w = im.shape
    if disk_mask is None:
        thr = float(np.percentile(im, 55))
        disk_mask = im > thr * 0.35
    disk = np.asarray(disk_mask, dtype=bool)
    out = np.zeros((h, w), dtype=bool)
    if disk.sum() < 100:
        return out
    try:
        from scipy.ndimage import binary_dilation, label
    except Exception:
        return out

    dark = (im < float(np.percentile(im[disk], 9.0))) & disk
    lab, n = label(dark)
    for i in range(1, n + 1):
        m = lab == i
        area = int(m.sum())
        # compact: not a whole belt segment
        if area < 5 or area > max(80, int(0.012 * disk.sum())):
            continue
        ys, xs = np.where(m)
        bb = max(1, (int(xs.max()) - int(xs.min()) + 1) * (int(ys.max()) - int(ys.min()) + 1))
        fill = area / bb
        if fill < 0.28:
            continue
        # roughly round
        aspect = (xs.max() - xs.min() + 1) / max(1, (ys.max() - ys.min() + 1))
        if aspect > 3.5 or aspect < 1 / 3.5:
            continue
        out |= binary_dilation(m, iterations=3)
    return out


def orange_score_rgb(rgb_hwc: np.ndarray) -> np.ndarray:
    """Positive score for orange/red GRS-like colour (not mono-dark belts)."""
    r = _norm01(rgb_hwc[..., 0])
    g = _norm01(rgb_hwc[..., 1])
    b = _norm01(rgb_hwc[..., 2])
    orange = np.clip(r - g, 0, None) * np.clip(r - b, 0, None)
    red = np.clip(r - 0.5 * (g + b), 0, None)
    return orange * (0.4 + red)


def suggest_ns_flip_for_grs(image: np.ndarray) -> Tuple[bool, Dict[str, Any]]:
    """
    If the strongest orange oval on-disk is in the *northern* half (lat>0),
    the stack is likely N–S flipped for GRS work → recommend flip_ns=True.
    """
    info: Dict[str, Any] = {"ok": False, "flip_ns": False}
    rgb = _as_hwc_rgb(image)
    if rgb is None:
        mono = to_mono(image)
        nav = fit_limb_nav(mono)
        info["note"] = "mono_only_no_colour_flip"
        return False, info
    r = _norm01(rgb[..., 0])
    nav = fit_limb_nav(r)
    score = orange_score_rgb(rgb)
    yy, xx = np.mgrid[0 : r.shape[0], 0 : r.shape[1]]
    disk = (yy - nav.yc) ** 2 + (xx - nav.xc) ** 2 < (0.90 * nav.a_eq_px) ** 2
    sc = np.where(disk, score, 0.0)
    if float(sc.max()) <= 1e-8:
        info["note"] = "no_orange_peak"
        return False, info
    jy, jx = np.unravel_index(int(np.argmax(sc)), sc.shape)
    lon, lat = px_to_lonlat(float(jy), float(jx), nav)
    info.update(
        {
            "ok": True,
            "orange_xy": (int(jx), int(jy)),
            "orange_lon": float(lon),
            "orange_lat": float(lat),
            "score": float(sc[jy, jx]),
        }
    )
    # GRS lives south; orange peak north ⇒ flip
    if lat > 5.0 and sc[jy, jx] > 0:
        info["flip_ns"] = True
        info["note"] = (
            f"orange peak at lat={lat:.1f}° (north) — stack likely N–S flipped; "
            "applying flip_ns so GRS is southern"
        )
        return True, info
    info["flip_ns"] = False
    info["note"] = f"orange peak lat={lat:.1f}° — no N–S flip"
    return False, info


def orange_grs_lonlat(
    image: np.ndarray,
    *,
    cm_iii_deg: float,
    distance_au: float = 5.2,
    sub_lat_deg: float = 0.0,
    north_pa_deg: float = 0.0,
    flip_ns: bool = False,
) -> Dict[str, Any]:
    """
    Colour-first GRS centre: peak orange oval in SEB latitude after optional N–S flip.
    Returns lon/lat suitable as publish seed when score is strong.
    """
    from human_choice import apply_image_flips

    rgb = _as_hwc_rgb(image)
    out: Dict[str, Any] = {"ok": False}
    if rgb is None:
        out["error"] = "need_rgb"
        return out
    if flip_ns:
        rgb = apply_image_flips(rgb, False, True)
    r = _norm01(rgb[..., 0])
    score = orange_score_rgb(rgb)
    nav = fit_limb_nav(r, cm_iii_deg=cm_iii_deg, distance_au=distance_au)
    nav.cm_iii_deg = float(cm_iii_deg)
    nav.distance_au = float(distance_au)
    nav.sub_lat_deg = float(sub_lat_deg or 0.0)
    nav.north_pa_deg = float(north_pa_deg or 0.0)

    yy, xx = np.mgrid[0 : r.shape[0], 0 : r.shape[1]]
    disk = (yy - nav.yc) ** 2 + (xx - nav.xc) ** 2 < (0.90 * nav.a_eq_px) ** 2
    # mask moons on luminance
    L = 0.299 * r + 0.587 * _norm01(rgb[..., 1]) + 0.114 * _norm01(rgb[..., 2])
    moon = mask_satellite_shadows(L, disk_mask=disk)
    score = score.copy()
    score[moon] = 0
    score[~disk] = 0
    try:
        from scipy.ndimage import gaussian_filter

        score = gaussian_filter(score, 2.0)
    except Exception:
        pass

    # Keep only SEB-ish image rows by projecting lat of each row centre
    best = None
    ys, xs = np.where(score >= max(float(score.max()) * 0.55, 1e-8))
    cands = []
    for y, x in zip(ys.tolist(), xs.tolist()):
        lon, lat = px_to_lonlat(float(y), float(x), nav)
        if -28.5 <= lat <= -15.5:
            cands.append((float(score[y, x]), float(lon), float(lat), int(x), int(y)))
    if not cands:
        # relax lat
        for y, x in zip(ys.tolist(), xs.tolist()):
            lon, lat = px_to_lonlat(float(y), float(x), nav)
            if -32 <= lat <= -12:
                cands.append((float(score[y, x]), float(lon), float(lat), int(x), int(y)))
    if not cands:
        out["error"] = "no_orange_in_seb"
        return out
    cands.sort(reverse=True)
    # cluster around best
    b = cands[0]
    cl = [
        c
        for c in cands[:80]
        if abs(((c[1] - b[1] + 180) % 360) - 180) < 14 and abs(c[2] - b[2]) < 5
    ]
    w = np.array([c[0] for c in cl], dtype=np.float64)
    ang = np.deg2rad([c[1] for c in cl])
    lon = float(
        np.rad2deg(
            np.arctan2(np.average(np.sin(ang), weights=w), np.average(np.cos(ang), weights=w))
        )
        % 360.0
    )
    lat = float(np.average([c[2] for c in cl], weights=w))
    # lon relative to CM — reject near-limb orange junk
    rel = abs(((lon - cm_iii_deg + 180) % 360) - 180)
    out.update(
        {
            "ok": True,
            "lon_iii_deg": lon,
            "lat_deg": lat,
            "score": float(b[0]),
            "n_cluster": len(cl),
            "lon_rel_cm_deg": float(rel),
            "xy": (b[3], b[4]),
            "method": "ORANGE_GRS",
            "near_limb": rel > 70.0,
        }
    )
    return out


def prepare_grs_measure_image(
    image: np.ndarray,
    channels: Optional[Dict[str, np.ndarray]] = None,
    *,
    auto_flip_ns: bool = True,
) -> Tuple[np.ndarray, Dict[str, np.ndarray], Dict[str, Any]]:
    """
    Returns (measure_mono, channels_out, meta).

    measure_mono: red-based mono with moon masked and orange GRS darkened
    so GS-MAP style dark centroid locks on the oval, not the shadow.
    """
    meta: Dict[str, Any] = {"auto_flip_ns": False, "moon_pixels": 0}
    rgb = _as_hwc_rgb(image)
    ch_out: Dict[str, np.ndarray] = {}
    if channels:
        ch_out = {k: np.asarray(v, dtype=np.float64) for k, v in channels.items()}

    flip_ns = False
    if auto_flip_ns and rgb is not None:
        flip_ns, finfo = suggest_ns_flip_for_grs(rgb)
        meta["orientation"] = finfo
        meta["auto_flip_ns"] = bool(flip_ns)
        if flip_ns:
            from human_choice import apply_image_flips

            rgb = apply_image_flips(rgb, False, True)
            image = rgb
            if ch_out:
                for k in list(ch_out.keys()):
                    ch_out[k] = np.flipud(ch_out[k])

    if rgb is not None:
        r = _norm01(rgb[..., 0])
        g = _norm01(rgb[..., 1])
        b = _norm01(rgb[..., 2])
        ch_out.setdefault("R", r)
        ch_out.setdefault("G", g)
        ch_out.setdefault("B", b)
        orange = np.clip(r - g, 0, None) * np.clip(r - b, 0, None)
        # Darken orange GRS so dark-core estimators find it
        meas = r - 2.2 * orange
        lo, hi = np.percentile(meas, (1.0, 99.0))
        meas = np.clip((meas - lo) / (hi - lo + 1e-12), 0, 1)
        mono_for_moon = 0.299 * r + 0.587 * g + 0.114 * b
    else:
        meas = _norm01(to_mono(image))
        mono_for_moon = meas
        if "R" in ch_out:
            meas = _norm01(ch_out["R"])
            mono_for_moon = meas

    # disk + moon mask
    try:
        nav = fit_limb_nav(meas)
        yy, xx = np.mgrid[0 : meas.shape[0], 0 : meas.shape[1]]
        disk = (yy - nav.yc) ** 2 + (xx - nav.xc) ** 2 < (0.95 * nav.a_eq_px) ** 2
    except Exception:
        disk = mono_for_moon > float(np.percentile(mono_for_moon, 40))

    moon = mask_satellite_shadows(mono_for_moon, disk_mask=disk)
    meta["moon_pixels"] = int(moon.sum())
    if moon.any():
        fill = float(np.median(meas[disk & ~moon])) if (disk & ~moon).any() else float(np.median(meas))
        meas = meas.copy()
        meas[moon] = fill
        meta["moon_masked"] = True
        # also blank moons in channels so dual/refine cannot re-lock
        for k in ch_out:
            arr = ch_out[k].copy()
            arr[moon] = float(np.median(arr[disk & ~moon])) if (disk & ~moon).any() else float(np.median(arr))
            ch_out[k] = arr
    else:
        meta["moon_masked"] = False

    meta["prep"] = "red+orange_darken+moon_mask"
    return meas.astype(np.float64), ch_out, meta

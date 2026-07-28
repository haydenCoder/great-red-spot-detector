#!/usr/bin/env python3
"""
Precision GRS engine — the core measurement machinery

I built this for my astrophysics coursework to try to get the GRS position
as precisely as possible from ground-based images. The target is ≤0.1″ sky
on ideal synthetic frames, which at Jupiter ~5 AU means about 0.3° longitude
near the equator. That's ambitious but reachable on high-contrast synthetics
if you stack enough careful steps:

  1) sub-pixel limb navigation (ray-trace isophote boundary)
  2) multi-scale cylindrical dark-oval template match (primary method)
  3) intensity barycentre + ellipse consensus
  4) multi-method weighted consensus with outlier rejection
  5) Monte Carlo for uncertainty in arcseconds

Real ground-based extended-cloud floors are usually higher (seeing + definition).
The engine still tries for research-grade recovery when the feature is well resolved.

One thing I learned: the limb outline choice matters a LOT. A slightly larger or
smaller outline shifts the disk radius and can change the absolute lon/lat by
tenths of a degree. That's why the champion path does multi-isophote probing.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from verbose_log import CONSOLE

# Jupiter physical constants — I pulled these from NASA fact sheets
JUP_REQ_KM = 71492.0       # equatorial radius (km)
JUP_RPOL_KM = 66854.0      # polar radius (km)
FLAT = 1.0 - JUP_RPOL_KM / JUP_REQ_KM   # flattening ≈ 0.0649
AU_KM = 149597870.7        # astronomical unit in km
ARCSEC_PER_RAD = 206264.80624709636  # conversion factor


@dataclass
class NavState:
    xc: float
    yc: float
    a_eq_px: float
    flattening: float = FLAT
    cm_iii_deg: float = 0.0
    distance_au: float = 5.2
    sub_lat_deg: float = 0.0
    north_pa_deg: float = 0.0

    @property
    def b_pol_px(self) -> float:
        return self.a_eq_px * (1.0 - self.flattening)


@dataclass
class GRSPrecisionResult:
    lon_iii_deg: float
    lat_deg: float  # planetocentric
    length_deg: float
    width_deg: float
    method: str
    methods: Dict[str, Dict[str, float]] = field(default_factory=dict)
    err_lon_deg: float = float("nan")
    err_lat_deg: float = float("nan")
    err_sky_arcsec: float = float("nan")
    err_lon_arcsec: float = float("nan")
    err_lat_arcsec: float = float("nan")
    quality: float = 0.0
    notes: List[str] = field(default_factory=list)
    lat_planetographic_deg: float = float("nan")
    lat_kind: str = "planetocentric"

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if not math.isfinite(d.get("lat_planetographic_deg", float("nan"))):
            try:
                d["lat_planetographic_deg"] = planetocentric_to_planetographic(self.lat_deg)
            except Exception:
                pass
        return d


def deg2rad(d: float) -> float:
    """Quick degree → radian helper (math.pi / 180)."""
    return d * math.pi / 180.0


def rad2deg(r: float) -> float:
    """Quick radian → degree helper."""
    return r * 180.0 / math.pi


def wrap_deg(x: float) -> float:
    """Wrap angle into [0, 360) — System III longitude convention."""
    return float(x % 360.0)


def wrap_diff(a: float, b: float) -> float:
    """Signed difference a-b wrapped into [-180, 180].

    This is essential for longitude comparisons because System III
    wraps at 360°. If you just subtract you get nonsense like
    lon1=5°, lon2=355° → Δ=-350° instead of the correct Δ=+10°.
    """
    return float((a - b + 180.0) % 360.0 - 180.0)


def km_per_deg_lon(lat_deg: float) -> float:
    """Kilometres per degree of longitude at a given latitude on Jupiter.

    Depends on latitude because Jupiter is oblate — 1° of longitude
    covers less km near the poles than at the equator.
    """
    return (2 * math.pi * JUP_REQ_KM / 360.0) * math.cos(deg2rad(lat_deg))


def km_per_deg_lat() -> float:
    """Kilometres per degree of latitude on Jupiter (uses polar radius)."""
    return 2 * math.pi * JUP_RPOL_KM / 360.0


def deg_to_arcsec_on_sky(deg: float, km_per_deg: float, distance_au: float) -> float:
    """Convert angular size on the planet surface (degrees of lon/lat) to sky arcsec.

    This is the key conversion for the error budget — turning degrees of
    longitude/latitude on Jupiter's surface into arcseconds as seen from Earth.
    """
    km = abs(deg) * km_per_deg
    dist_km = distance_au * AU_KM
    return (km / dist_km) * ARCSEC_PER_RAD


def sky_error_arcsec(dlon_deg: float, dlat_deg: float, lat_deg: float, distance_au: float) -> float:
    """Combined on-sky error in arcseconds from Δlon and Δlat.

    Uses hypot (quadrature sum) because the two directions are independent.
    This is what you report as your total sky error — it's the combined
    positional uncertainty in both longitude and latitude projected onto
    the sky plane.
    """
    as_lon = deg_to_arcsec_on_sky(dlon_deg, km_per_deg_lon(lat_deg), distance_au)
    as_lat = deg_to_arcsec_on_sky(dlat_deg, km_per_deg_lat(), distance_au)
    return float(math.hypot(as_lon, as_lat))


def planetocentric_to_planetographic(lat_c_deg: float, flattening: float = FLAT) -> float:
    """
    Convert planetocentric latitude → planetographic.

    WinJUPOS uses planetographic lat, so this conversion is essential for
    comparing our results to WinJUPOS output. The formula is:
    φ_g = atan( (R_eq/R_pol)^2 tan φ_c )

    I kept messing this up early on — the difference matters! At the GRS
    latitude (~-23° planetocentric), planetographic is about -24° something.
    If you compare the wrong kind of latitude to WinJUPOS you'll see a
    ~1.5° offset that's just a coordinate convention difference, not a
    real measurement error.
    """
    f = float(flattening)
    ratio = 1.0 / max(1.0 - f, 1e-9)  # R_eq / R_pol
    t = math.tan(deg2rad(float(lat_c_deg)))
    return rad2deg(math.atan((ratio ** 2) * t))


def planetographic_to_planetocentric(lat_g_deg: float, flattening: float = FLAT) -> float:
    """Reverse: planetographic → planetocentric (inverse of the above)."""
    f = float(flattening)
    ratio = 1.0 / max(1.0 - f, 1e-9)
    t = math.tan(deg2rad(float(lat_g_deg)))
    return rad2deg(math.atan(t / (ratio ** 2)))


def _gauss(img: np.ndarray, sigma: float) -> np.ndarray:
    """Gaussian blur — tries scipy first, falls back to FFT convolution.

    The scipy fallback was originally just `return img` which is... not
    a blur at all. I fixed that to actually do a box-filter approximation
    via FFT convolution so it still works when scipy isn't available
    (which happens in some deployment environments).
    """
    if sigma <= 0.05:
        return np.asarray(img, dtype=np.float64)
    try:
        from scipy.ndimage import gaussian_filter
        return gaussian_filter(img, sigma=sigma, mode="nearest")
    except Exception:
        # Box-filter approximation when scipy unavailable
        k = max(3, int(sigma * 4) | 1)  # kernel size, always odd
        ker = np.ones((k, k), dtype=np.float64) / (k * k)
        from numpy.fft import fft2, ifft2
        padded = np.pad(img, k // 2, mode='edge')
        ker_padded = np.zeros_like(padded)
        ker_padded[k // 2:k // 2 + k, k // 2:k // 2 + k] = ker
        result = np.real(ifft2(fft2(padded) * fft2(ker_padded)))
        return result[k // 2:k // 2 + img.shape[0], k // 2:k // 2 + img.shape[1]]


def to_mono(image: np.ndarray) -> np.ndarray:
    """Convert any image array to greyscale (mono float64).

    Uses the standard NTSC weights (0.299R + 0.587G + 0.114B) which
    are close enough to what Jupiter looks like in mono for our purposes.
    Handles CHW, HWC, and already-mono formats.
    """
    im = np.asarray(image, dtype=np.float64)
    if im.ndim == 2:
        return im
    if im.ndim == 3 and im.shape[0] in (3, 4) and im.shape[0] < min(im.shape[1], im.shape[2]):
        # CHW
        r, g, b = im[0], im[1], im[2]
        return 0.299 * r + 0.587 * g + 0.114 * b
    if im.ndim == 3 and im.shape[-1] >= 3:
        return 0.299 * im[..., 0] + 0.587 * im[..., 1] + 0.114 * im[..., 2]
    return im.reshape(im.shape[0], im.shape[1])


def rough_disk_mask(image: np.ndarray) -> np.ndarray:
    """Quick binary mask of Jupiter's disk — used to seed the limb fit.

    Thresholds at 22% of the 99.5th percentile intensity, then does
    simple morphological cleanup (opening + closing) to remove noise
    speckles. Not precise enough for measurement, but good enough to
    find where the planet is in the frame.
    """
    im = to_mono(image)
    thr = 0.22 * np.percentile(im, 99.5)
    m = im > thr
    try:
        from scipy.ndimage import binary_opening, binary_closing, label
        m = binary_closing(binary_opening(m, iterations=1), iterations=2)
        lab, n = label(m)
        if n:
            counts = np.bincount(lab.ravel())
            counts[0] = 0
            m = lab == int(np.argmax(counts))
    except Exception:
        pass
    return m


def fit_limb_nav(
    image: np.ndarray,
    n_rays: int = 720,
    cm_iii_deg: float = 0.0,
    distance_au: float = 5.2,
    isophote_frac: float = 0.18,
) -> NavState:
    """
    Sub-pixel limb navigation — the most critical step in the whole pipeline.

    I ray-trace from a seed centre outward at hundreds of angles, finding where
    the intensity drops to isophote_frac × peak for each ray. Then I compute a
    robust median centre and equatorial radius using MAD-based outlier rejection
    (3σ clip). This is much more stable under limb darkening than algebraic
    circle fits, which kept blowing up on images with strong Limb Darkening.

    The isophote_frac parameter is basically the WinJUPOS "outline size" knob:
    - smaller fraction → larger outline (fainter outer edge) → bigger disk radius
    - larger fraction → smaller outline (brighter inner edge) → smaller disk radius

    This can shift absolute lon/lat by tenths of a degree! The champion path
    probes multiple isophote levels and picks the one that stabilises the GRS
    measurement, which is how WinJUPOS practitioners do it by eye.
    """
    im = to_mono(image)
    h, w = im.shape
    thr_frac = float(np.clip(isophote_frac, 0.05, 0.55))
    m = rough_disk_mask(im)
    ys, xs = np.where(m)
    if len(xs) < 50:
        return NavState(xc=w / 2, yc=h / 2, a_eq_px=min(h, w) * 0.4, cm_iii_deg=cm_iii_deg, distance_au=distance_au)
    # geometric seed from bounding box (less polar-hood bias than intensity mean)
    cx0 = 0.5 * (float(xs.min()) + float(xs.max()))
    cy0 = 0.5 * (float(ys.min()) + float(ys.max()))
    r_est = 0.25 * ((xs.max() - xs.min()) + (ys.max() - ys.min()))

    xc, yc = cx0, cy0
    a = float(r_est)
    n_iter = 6 if n_rays >= 800 else 5
    n_rad = 360 if n_rays >= 800 else 300
    for _ in range(n_iter):
        pts_x = []
        pts_y = []
        for i in range(n_rays):
            ang = 2 * math.pi * i / n_rays
            rs = np.linspace(0.48 * a, 1.30 * a, n_rad)
            xs_r = xc + rs * math.cos(ang)
            ys_r = yc + rs * math.sin(ang)
            x0 = np.clip(np.floor(xs_r).astype(int), 0, w - 2)
            y0 = np.clip(np.floor(ys_r).astype(int), 0, h - 2)
            dx = xs_r - x0
            dy = ys_r - y0
            prof = (
                im[y0, x0] * (1 - dx) * (1 - dy)
                + im[y0, x0 + 1] * dx * (1 - dy)
                + im[y0 + 1, x0] * (1 - dx) * dy
                + im[y0 + 1, x0 + 1] * dx * dy
            )
            # peak in inner half of ray
            imid = max(2, len(prof) // 2)
            pmax = float(np.max(prof[:imid]))
            if pmax <= 1e-12:
                continue
            # Low isophote → larger outline (outer limb); high → smaller outline
            thr = thr_frac * pmax
            above = np.where(prof >= thr)[0]
            if len(above) == 0:
                g = np.gradient(prof)
                j = int(np.argmin(g))
                r = rs[min(max(j, 0), len(rs) - 1)]
            else:
                j = int(above[-1])
                if 0 <= j < len(prof) - 1:
                    p0, p1 = float(prof[j]), float(prof[j + 1])
                    if abs(p0 - p1) < 1e-12:
                        r = float(rs[j])
                    else:
                        u = (p0 - thr) / (p0 - p1)
                        u = min(max(u, 0.0), 1.0)
                        r = float(rs[j] + u * (rs[1] - rs[0]))
                else:
                    r = float(rs[min(j, len(rs) - 1)])
            pts_x.append(xc + r * math.cos(ang))
            pts_y.append(yc + r * math.sin(ang))
        if len(pts_x) < 40:
            break
        xs_p = np.asarray(pts_x, dtype=np.float64)
        ys_p = np.asarray(pts_y, dtype=np.float64)
        # robust centre
        xc_n = float(np.median(xs_p))
        yc_n = float(np.median(ys_p))
        rr = np.sqrt((xs_p - xc_n) ** 2 + ((ys_p - yc_n) / (1.0 - FLAT)) ** 2)
        med = float(np.median(rr))
        mad = float(np.median(np.abs(rr - med))) + 1e-9
        keep = np.abs(rr - med) < 3.0 * 1.4826 * mad
        if int(keep.sum()) > 40:
            xs_p, ys_p = xs_p[keep], ys_p[keep]
            xc_n = float(np.mean(xs_p))
            yc_n = float(np.mean(ys_p))
            a_n = float(np.median(np.sqrt((xs_p - xc_n) ** 2 + ((ys_p - yc_n) / (1.0 - FLAT)) ** 2)))
        else:
            a_n = med
        # damp large jumps (stability)
        if abs(xc_n - xc) > 0.15 * a or abs(yc_n - yc) > 0.15 * a or abs(a_n - a) > 0.2 * a:
            # reject pathological iteration — blend gently toward bbox seed
            xc = 0.7 * xc + 0.3 * cx0
            yc = 0.7 * yc + 0.3 * cy0
            a = 0.7 * a + 0.3 * r_est
        else:
            xc, yc, a = xc_n, yc_n, a_n

    CONSOLE.debug(f"Limb nav: xc={xc:.2f} yc={yc:.2f} a={a:.2f}px")
    return NavState(xc=xc, yc=yc, a_eq_px=a, cm_iii_deg=cm_iii_deg, distance_au=distance_au)


def px_to_lonlat(y: float, x: float, nav: NavState) -> Tuple[float, float]:
    """
    Image pixel → System III longitude + planetocentric latitude.

    This is the inverse of the orthographic projection used by make_cylindrical.
    It applies north_pa_deg (sky rotation) and sub_lat_deg when present so
    moment / bary / map methods all share the same geometry contract.

    The coordinate system took me ages to get right — the sky-plane PA
    rotation and sub-earth latitude tilt both need to be accounted for,
    otherwise you get systematic lat offsets that look like bugs but are
    really just wrong geometry.
    """
    # Sky plane (east, north-ish) relative to disk centre
    Xsky = (x - nav.xc) / (nav.a_eq_px + 1e-12)
    Ysky = (nav.yc - y) / (nav.b_pol_px + 1e-12)
    # Undo north PA: sky = R(pa) · planet_xy  →  planet_xy = R(-pa) · sky
    pa = deg2rad(float(getattr(nav, "north_pa_deg", 0.0) or 0.0))
    cP, sP = math.cos(pa), math.sin(pa)
    Xp = Xsky * cP + Ysky * sP
    Yp = -Xsky * sP + Ysky * cP
    rr = Xp * Xp + Yp * Yp
    if rr > 1.0:
        s = math.sqrt(rr) + 1e-12
        Xp /= s
        Yp /= s
        rr = 1.0
    Zp = math.sqrt(max(0.0, 1.0 - rr))
    # Undo sub-observer latitude tilt (same as make_cylindrical)
    D = deg2rad(float(getattr(nav, "sub_lat_deg", 0.0) or 0.0))
    cD, sD = math.cos(D), math.sin(D)
    Ye = Yp * cD + Zp * sD
    Ze = -Yp * sD + Zp * cD
    Xe = Xp
    lon_rel = rad2deg(math.atan2(Xe, Ze))
    lat = rad2deg(math.asin(max(-1.0, min(1.0, Ye))))
    return wrap_deg(nav.cm_iii_deg + lon_rel), lat


def make_cylindrical(image: np.ndarray, nav: NavState, width: int = 1440, height: int = 720) -> np.ndarray:
    """
    Build a cylindrical map from the disk image — this is where all the
    measurement methods actually do their work.

    Maps the visible hemisphere: lon∈[-90°,+90°] about CM, lat∈[-90°,+90°].
    Uses sub_lat_deg + north_pa_deg from NavState when non-zero so all
    methods share the same geometry (same contract as VLBI oriented maps).

    The default size (1440×720) gives 0.125°/pixel which is fine for most
    ground-based images. Higher resolutions are used in the champion path.
    """
    im = to_mono(image)
    lons = np.linspace(-90.0, 90.0, width)
    lats = np.linspace(90.0, -90.0, height)
    lon_g, lat_g = np.meshgrid(lons, lats)
    lon_r = np.deg2rad(lon_g)
    lat_r = np.deg2rad(lat_g)
    Xe = np.cos(lat_r) * np.sin(lon_r)
    Ye = np.sin(lat_r)
    Ze = np.cos(lat_r) * np.cos(lon_r)
    D = deg2rad(float(getattr(nav, "sub_lat_deg", 0.0) or 0.0))
    cD, sD = math.cos(D), math.sin(D)
    Yp = Ye * cD - Ze * sD
    Zp = Ye * sD + Ze * cD
    Xp = Xe
    mu = Zp
    pa = deg2rad(float(getattr(nav, "north_pa_deg", 0.0) or 0.0))
    cP, sP = math.cos(pa), math.sin(pa)
    Xsky = Xp * cP - Yp * sP
    Ysky = Xp * sP + Yp * cP
    xs = nav.xc + Xsky * nav.a_eq_px
    ys = nav.yc - Ysky * nav.b_pol_px
    h, w = im.shape
    x0 = np.floor(xs).astype(np.int64)
    y0 = np.floor(ys).astype(np.int64)
    dx = xs - x0
    dy = ys - y0
    valid = (mu > 0.02) & (x0 >= 0) & (x0 < w - 1) & (y0 >= 0) & (y0 < h - 1)
    out = np.zeros((height, width), dtype=np.float64)
    x0c = np.clip(x0, 0, w - 2)
    y0c = np.clip(y0, 0, h - 2)
    samp = (
        im[y0c, x0c] * (1 - dx) * (1 - dy)
        + im[y0c, x0c + 1] * dx * (1 - dy)
        + im[y0c + 1, x0c] * (1 - dx) * dy
        + im[y0c + 1, x0c + 1] * dx * dy
    )
    out[valid] = samp[valid]
    return out


def _template_match_grs(cyl: np.ndarray, nav: NavState,
                        lat0: float = -22.0, length_deg: float = 12.0, width_deg: float = 8.0) -> Dict[str, float]:
    """
    Dark elliptical template match on cylindrical map — the primary method.

    This is the most reliable dark-oval lock I've found for Jupiter. It uses
    zero-mean normalized cross-correlation (NCC) with multi-scale elliptical
    templates, restricted to the SEB/GRS latitude band, plus a local
    dark-centroid refine so we don't accidentally lock onto random SEB waves.

    The scale grid probes sizes from 90% to 112% of the nominal oval — the
    GRS has been shrinking over the last few decades so the prior size isn't
    exact, but it's close enough to anchor the search.
    """
    h, w = cyl.shape

    def lat_to_y(lat: float) -> int:
        return int(np.clip((90.0 - lat) / 180.0 * (h - 1), 0, h - 1))

    def subpixel(corr: np.ndarray, py: int, px: int) -> Tuple[float, float]:
        def sub1(p: int, line: np.ndarray) -> float:
            if p <= 0 or p >= len(line) - 1:
                return float(p)
            pm, p0, pp = float(line[p - 1]), float(line[p]), float(line[p + 1])
            den = pm - 2 * p0 + pp
            return float(p) if abs(den) < 1e-12 else p + 0.5 * (pm - pp) / den
        return sub1(py, corr[:, px]), sub1(px, corr[py, :])

    # Narrow GRS/SEB band — wide bands let NEB/SEB waves win the correlation
    y0 = lat_to_y(min(-12.0, lat0 + width_deg * 0.95))
    y1 = lat_to_y(max(-32.0, lat0 - width_deg * 0.95))
    if y1 < y0:
        y0, y1 = y1, y0
    band = cyl[y0 : y1 + 1, :].copy()
    med = float(np.median(band[band > 0])) if np.any(band > 0) else 0.0
    band[band <= 0] = med
    # highpass + invert so dark ovals are bright peaks
    band_hp = band - _gauss(band, max(1.2, min(band.shape) * 0.035))
    inv = -band_hp  # dark → positive
    inv = inv - float(np.mean(inv))

    scale_grid = [
        (length_deg * sL, width_deg * sW, 1.0 - 0.08 * abs(sL - 1.0) - 0.06 * abs(sW - 1.0))
        for sL in (0.90, 1.0, 1.12)
        for sW in (0.90, 1.0, 1.10)
    ]
    best: Optional[Dict[str, float]] = None

    try:
        from scipy.signal import fftconvolve
    except Exception as e:
        CONSOLE.debug(f"template match no scipy: {e}")
        fftconvolve = None  # type: ignore

    if fftconvolve is not None:
        for Ltry, Wtry, size_prior in scale_grid:
            tw = max(9, int(Ltry / 180.0 * w))
            th = max(7, int(Wtry / 180.0 * h))
            if tw % 2 == 0:
                tw += 1
            if th % 2 == 0:
                th += 1
            if th >= band.shape[0] - 2 or tw >= band.shape[1] - 2:
                continue
            yy, xx = np.mgrid[0:th, 0:tw].astype(np.float64)
            cy, cx = (th - 1) / 2.0, (tw - 1) / 2.0
            ell = ((xx - cx) / (tw / 2.0 + 1e-9)) ** 2 + ((yy - cy) / (th / 2.0 + 1e-9)) ** 2
            # bright-on-dark template for inverted map (matches dark oval)
            tmpl = np.exp(-0.5 * ell * 2.6)
            tmpl = (tmpl - tmpl.mean()) / (tmpl.std() + 1e-12)
            # NCC ≈ convolution of z-scored images
            corr = fftconvolve(inv, tmpl[::-1, ::-1], mode="same")
            # edge mask
            corr[: th // 2 + 1, :] = -1e99
            corr[-(th // 2 + 1) :, :] = -1e99
            corr[:, : tw // 2 + 1] = -1e99
            corr[:, -(tw // 2 + 1) :] = -1e99
            # mild latitude prior: prefer ~lat0 row
            bh = corr.shape[0]
            rows = np.arange(bh, dtype=np.float64)
            lat_rows = 90.0 - ((y0 + rows) / max(h - 1, 1)) * 180.0
            lat_w = np.exp(-0.5 * ((lat_rows - lat0) / 4.5) ** 2)
            corr = corr * lat_w[:, None]
            j = np.unravel_index(int(np.argmax(corr)), corr.shape)
            py, px = int(j[0]), int(j[1])
            peak = float(corr[py, px]) * float(size_prior)
            # require local map to actually be dark vs surroundings
            y0w, y1w = max(0, py - th // 3), min(band.shape[0], py + th // 3 + 1)
            x0w, x1w = max(0, px - tw // 3), min(band.shape[1], px + tw // 3 + 1)
            local = band[y0w:y1w, x0w:x1w]
            ring_med = float(np.median(band[max(0, py - th): min(band.shape[0], py + th + 1),
                                           max(0, px - tw): min(band.shape[1], px + tw + 1)]))
            local_mean = float(np.mean(local)) if local.size else ring_med
            if local_mean > ring_med * 0.98:
                # not darker than neighborhood — skip
                continue
            py_s, px_s = subpixel(corr, py, px)
            # dark-centroid refine in inverted window
            win = inv[y0w:y1w, x0w:x1w].copy()
            win[win < 0] = 0
            if float(win.sum()) > 0:
                yy2, xx2 = np.mgrid[y0w:y1w, x0w:x1w]
                s = float(win.sum()) + 1e-12
                py_s = float((yy2 * win).sum() / s)
                px_s = float((xx2 * win).sum() / s)
            lon_rel = -90.0 + (px_s / max(w - 1, 1)) * 180.0
            lat = 90.0 - ((y0 + py_s) / max(h - 1, 1)) * 180.0
            # reject far from GRS latitude band
            if not (-35.0 <= lat <= -12.0):
                continue
            if abs(lon_rel) > 80.0:
                continue
            cand = {
                "lon_iii_deg": wrap_deg(nav.cm_iii_deg + lon_rel),
                "lat_deg": float(lat),
                "length_deg": float(Ltry),
                "width_deg": float(Wtry),
                "score": peak,
                "method": "template",
                "map_x": float(px_s),
                "map_y": float(y0 + py_s),
                "dark_contrast": float(ring_med - local_mean),
            }
            if best is None or peak > float(best["score"]):
                best = cand

    if best is not None:
        return best

    # fallback: darkest local patch energy in band
    inv2 = _gauss(np.maximum(0.0, med - band), 1.5)
    j = np.unravel_index(int(np.argmax(inv2)), inv2.shape)
    py, px = int(j[0]), int(j[1])
    lon_rel = -90.0 + (px / max(w - 1, 1)) * 180.0
    lat = 90.0 - ((y0 + py) / max(h - 1, 1)) * 180.0
    return {
        "lon_iii_deg": wrap_deg(nav.cm_iii_deg + lon_rel),
        "lat_deg": float(lat) if -40 <= lat <= -8 else lat0,
        "length_deg": length_deg,
        "width_deg": width_deg,
        "score": float(inv2[py, px]),
        "method": "dark_profile",
    }


def _moment_mask_grs(image: np.ndarray, nav: NavState) -> Dict[str, float]:
    im = to_mono(image)
    h, w = im.shape
    # Prefer red channel for GRS (red-brown oval is darkest in R)
    im_r = None
    arr = np.asarray(image, dtype=np.float64)
    if arr.ndim == 3 and arr.shape[0] in (3, 4) and arr.shape[0] < min(arr.shape[1], arr.shape[2]):
        im_r = arr[0]
    elif arr.ndim == 3 and arr.shape[-1] >= 3:
        im_r = arr[..., 0]
    # SEB band in *oriented* planetocentric lat (same contract as px_to_lonlat)
    yy, xx = np.mgrid[0:h, 0:w]
    Xsky = (xx - nav.xc) / (nav.a_eq_px + 1e-12)
    Ysky = (nav.yc - yy) / (nav.b_pol_px + 1e-12)
    pa = deg2rad(float(getattr(nav, "north_pa_deg", 0.0) or 0.0))
    cP, sP = math.cos(pa), math.sin(pa)
    Xp = Xsky * cP + Ysky * sP
    Yp = -Xsky * sP + Ysky * cP
    rr = Xp * Xp + Yp * Yp
    Zp = np.sqrt(np.clip(1.0 - rr, 0.0, 1.0))
    D = deg2rad(float(getattr(nav, "sub_lat_deg", 0.0) or 0.0))
    cD, sD = math.cos(D), math.sin(D)
    Ye = Yp * cD + Zp * sD
    lat = np.degrees(np.arcsin(np.clip(Ye, -1.0, 1.0)))
    band = (rr <= 0.98) & (lat > -28) & (lat < -16)
    if band.sum() < 30:
        band = rr <= 0.95
    # Blend mono highpass with red darkness (GRS is redder/darker)
    hp = im - _gauss(im, max(2.0, nav.a_eq_px * 0.03))
    if im_r is not None:
        hp_r = im_r - _gauss(im_r, max(2.0, nav.a_eq_px * 0.03))
        hp = 0.45 * hp + 0.55 * hp_r
    # GRS is darker: take low percentile residual in band
    vals = hp[band]
    thr = np.percentile(vals, 10)
    cand = band & (hp <= thr)
    try:
        from scipy.ndimage import binary_opening, binary_closing, label
        cand = binary_closing(binary_opening(cand, iterations=1), iterations=2)
        lab, n = label(cand)
        if n == 0:
            raise RuntimeError("no component")
        best = None
        best_score = -1e99
        for i in range(1, n + 1):
            m = lab == i
            area = int(m.sum())
            if area < 40 or area > 0.18 * band.sum():
                continue
            ys, xs = np.where(m)
            cy, cx = float(ys.mean()), float(xs.mean())
            lon, la = px_to_lonlat(cy, cx, nav)
            # Prefer SEB/GRS latitude, large area, low intensity (dark), compact
            mean_i = float(np.mean(im[m]))
            compactness = area / (1.0 + (xs.max() - xs.min()) * (ys.max() - ys.min()) + 1e-6)
            score = (
                1.2 * math.log(area + 1.0)
                - 3.5 * abs(la + 22.0)
                - 18.0 * mean_i
                + 4.0 * compactness
            )
            if im_r is not None:
                score -= 10.0 * float(np.mean(im_r[m]))  # red-dark GRS
            if score > best_score:
                best_score = score
                best = m
        if best is None:
            # darkest large component near -22
            counts = np.bincount(lab.ravel())
            counts[0] = 0
            best = lab == int(np.argmax(counts))
    except Exception:
        best = cand

    ys, xs = np.where(best)
    if len(xs) < 5:
        raise RuntimeError("moment mask empty")
    # intensity-inverted weights for dark oval
    wts = np.max(im[ys, xs]) - im[ys, xs] + 1e-6
    cx = float(np.sum(xs * wts) / np.sum(wts))
    cy = float(np.sum(ys * wts) / np.sum(wts))
    lon, lat = px_to_lonlat(cy, cx, nav)
    # Size on cylindrical map (uniform °/px) — not raw image-plane foreshortened axes
    try:
        cyl = make_cylindrical(im, nav, width=900, height=450)
        h_c, w_c = cyl.shape
        lon_rel = wrap_diff(lon, nav.cm_iii_deg)
        mx = (lon_rel + 90.0) / 180.0 * (w_c - 1)
        my = (90.0 - lat) / 180.0 * (h_c - 1)
        # dark residual window around centre
        y0w = max(0, int(my) - 25)
        y1w = min(h_c, int(my) + 26)
        x0w = max(0, int(mx) - 40)
        x1w = min(w_c, int(mx) + 41)
        patch = cyl[y0w:y1w, x0w:x1w]
        valid = patch > 1e-8
        med = float(np.median(patch[valid])) if valid.any() else 0.0
        inv = np.where(valid, np.maximum(0.0, med - patch), 0.0)
        thr = float(np.percentile(inv[inv > 0], 55)) if (inv > 0).any() else 0.0
        mask = inv >= thr
        if mask.sum() >= 8:
            yys, xxs = np.where(mask)
            # 4σ-like extent in map degrees
            sx = float(np.std(xxs.astype(np.float64))) * 4.0
            sy = float(np.std(yys.astype(np.float64))) * 4.0
            length = sx * (180.0 / max(w_c - 1, 1))
            width = sy * (180.0 / max(h_c - 1, 1))
        else:
            length, width = float("nan"), float("nan")
    except Exception:
        length, width = float("nan"), float("nan")
    if not math.isfinite(length) or not math.isfinite(width):
        # last resort: image-plane scale (documented as approximate)
        data = np.stack([xs - cx, ys - cy], axis=1).astype(np.float64)
        cov = np.cov((data * np.sqrt(wts)[:, None]).T)
        eig = np.linalg.eigvalsh(cov)
        eig = np.sort(eig)[::-1]
        a_px = 2.0 * math.sqrt(max(eig[0], 1e-6))
        b_px = 2.0 * math.sqrt(max(eig[1], 1e-6))
        km_per_px_eq = JUP_REQ_KM / (nav.a_eq_px + 1e-12)
        km_per_px_pol = JUP_RPOL_KM / (nav.b_pol_px + 1e-12)
        length = (a_px * km_per_px_eq) / (km_per_deg_lon(lat) + 1e-12)
        width = (b_px * km_per_px_pol) / (km_per_deg_lat() + 1e-12)
    try:
        score_out = float(best_score)
    except NameError:
        score_out = float(len(xs))
    return {
        "lon_iii_deg": lon,
        "lat_deg": lat,
        "length_deg": float(length),
        "width_deg": float(width),
        "score": score_out,
        "method": "moment_mask",
        "size_definition": "map_4sigma_or_image_approx",
    }


def _map_dark_centroid(cyl: np.ndarray, nav: NavState, lat0: float = -22.0) -> Dict[str, float]:
    """Dark peak only inside SEB/GRS latitude band — position-only, no size estimate.

    Early versions of this searched the full map and kept locking onto the
    north polar hood (lat ~90°) which is always dark. Restricting to the
    SEB band fixed that completely. Lesson learned: always constrain your
    search domain when you know where the feature should be.
    """
    h, w = cyl.shape

    def lat_to_y(lat: float) -> int:
        return int(np.clip((90.0 - lat) / 180.0 * (h - 1), 0, h - 1))

    y0 = lat_to_y(-14.0)
    y1 = lat_to_y(-32.0)
    if y1 < y0:
        y0, y1 = y1, y0
    band = cyl[y0 : y1 + 1, :].copy()
    valid = band > 1e-8
    if valid.sum() < 30:
        raise RuntimeError("empty SEB map band")
    work = band.copy()
    med = float(np.median(work[valid]))
    work[~valid] = med
    inv = med - work
    inv[inv < 0] = 0
    inv = _gauss(inv, 1.5)
    col_ok = valid.mean(axis=0) > 0.15
    inv[:, ~col_ok] = 0
    if float(inv.max()) <= 0:
        raise RuntimeError("no dark peak in SEB band")
    j = np.unravel_index(np.argmax(inv), inv.shape)
    py, px = int(j[0]), int(j[1])
    y0w, y1w = max(0, py - 6), min(inv.shape[0], py + 7)
    x0w, x1w = max(0, px - 10), min(inv.shape[1], px + 11)
    win = inv[y0w:y1w, x0w:x1w]
    yy, xx = np.mgrid[y0w:y1w, x0w:x1w]
    s = float(win.sum()) + 1e-12
    cy = float((yy * win).sum() / s)
    cx = float((xx * win).sum() / s)
    abs_y = y0 + cy
    abs_x = cx
    lon_rel = -90.0 + (abs_x / max(w - 1, 1)) * 180.0
    lat = 90.0 - (abs_y / max(h - 1, 1)) * 180.0
    if not (-35.0 <= lat <= -12.0):
        raise RuntimeError(f"map_dark lat {lat:.1f} outside GRS band")
    if abs(lon_rel) > 88.0:
        raise RuntimeError("map_dark too near map edge")
    return {
        "lon_iii_deg": wrap_deg(nav.cm_iii_deg + lon_rel),
        "lat_deg": float(lat),
        # Position-only method — do not invent a measured oval size
        "length_deg": float("nan"),
        "width_deg": float("nan"),
        "score": float(inv[py, px]),
        "method": "map_dark_centroid",
        "size_definition": "unmeasured",
    }


def _method_is_sane(m: Dict[str, float], ref_lon: Optional[float] = None) -> bool:
    """Sanity check: is this measurement result anywhere near the GRS?

    Rejects results that are clearly locked on the wrong feature:
    - latitude outside [-36°, -10°] (GRS is always around -23°)
    - longitude too far from a reference position
    - wildly wrong size estimates

    This is pass-1 filtering — a wider band, with tighter cluster
    rejection applied later. JUPOS/ALPO practice: always reject EZ
    and polar locks before publishing.
    """
    lat = float(m.get("lat_deg", 99))
    lon = float(m.get("lon_iii_deg", 0))
    L = float(m.get("length_deg", 12))
    W = float(m.get("width_deg", 8))
    if not (-36.0 <= lat <= -10.0):
        return False
    if ref_lon is not None and abs(wrap_diff(lon, ref_lon)) > 18.0:
        return False
    if m.get("method") == "moment_mask":
        # position can be excellent even when size is unmeasured (NaN)
        if math.isfinite(L) and math.isfinite(W):
            if L < 1.5 or L > 30.0 or W < 1.0 or W > 20.0:
                return False
            if L / max(W, 1e-6) > 8.0:
                return False
    return True


def _choose_size(methods: Dict[str, Dict[str, float]]) -> Tuple[float, float, str]:
    """
    Pick the best size estimate from available methods.

    Moment mask gives measured extents (isophote-based) — prefer this.
    Template L/W are search priors, not measured isophote size — only use
    them as a fallback and tag them accordingly. If nothing works we fall
    back to literature defaults for the 2020s GRS (~12° × 8°).
    """
    if "moment" in methods and not methods["moment"].get("rejected"):
        m = methods["moment"]
        try:
            L, W = float(m["length_deg"]), float(m["width_deg"])
        except Exception:
            L, W = float("nan"), float("nan")
        if math.isfinite(L) and math.isfinite(W) and _method_is_sane(m):
            if 4.0 <= L <= 28.0 and 2.0 <= W <= 16.0:
                return L, W, "moment_map_extent"
    if "template" in methods and not methods["template"].get("rejected"):
        t = methods["template"]
        try:
            L, W = float(t["length_deg"]), float(t["width_deg"])
        except Exception:
            L, W = float("nan"), float("nan")
        if math.isfinite(L) and math.isfinite(W) and 5.0 <= L <= 22.0 and 3.0 <= W <= 14.0:
            return L, W, "template_prior_size_not_isophote"
    return 12.0, 8.0, "literature_default_size_2020s"


# Balanced weights: map_dark/template for GS-MAP-style core; moment as backup.
# I spent a while tuning these — over-weighting map_dark alone can lock onto
# SEB dark barges on soft stacks, which was a frustrating bug to track down.
METHOD_WEIGHTS = {
    "template": 2.6,
    "map_dark": 2.5,
    "moment": 2.6,
}


def _circular_weighted_mean(lons: np.ndarray, weights: np.ndarray) -> float:
    w = np.asarray(weights, dtype=np.float64)
    w = w / (w.sum() + 1e-12)
    r = np.deg2rad(np.asarray(lons, dtype=np.float64))
    x = float(np.sum(w * np.cos(r)))
    y = float(np.sum(w * np.sin(r)))
    return wrap_deg(rad2deg(math.atan2(y, x)))


def measure_grs_precision(
    image: np.ndarray,
    cm_iii_deg: float = 0.0,
    distance_au: float = 5.2,
    nav: Optional[NavState] = None,
    quiet: bool = False,
    map_width: int = 2400,
    map_height: int = 1200,
) -> GRSPrecisionResult:
    """
    The main multi-method GRS measurement — this is what everything else calls.

    Runs template match, map dark centroid, and moment mask on a high-res
    cylindrical map, then does weighted consensus with outlier rejection.
    Template is preferred for longitude (it's the most reliable dark-oval
    lock). SPIRE-Net gets blended in as a prior when available.

    The consensus logic was the hardest part to get right — you have to
    reject pathological methods (wrong-feature locks, thin barges) while
    still letting good methods agree. I went through several iterations
    before the pass-1 / pass-2 / cluster-seed approach worked reliably.
    """
    if not quiet:
        CONSOLE.info("Precision engine: multi-method GRS (template-weighted consensus)")
    if nav is None:
        nav = fit_limb_nav(image, cm_iii_deg=cm_iii_deg, distance_au=distance_au)
    else:
        nav.cm_iii_deg = cm_iii_deg
        nav.distance_au = distance_au

    cyl = make_cylindrical(image, nav, width=map_width, height=map_height)
    raw_methods: Dict[str, Dict[str, float]] = {}
    methods: Dict[str, Dict[str, float]] = {}
    rejected: Dict[str, str] = {}
    notes: List[str] = []

    try:
        raw_methods["template"] = _template_match_grs(cyl, nav)
    except Exception as e:
        notes.append(f"template failed: {e}")
    try:
        raw_methods["map_dark"] = _map_dark_centroid(cyl, nav)
    except Exception as e:
        notes.append(f"map_dark failed/rejected: {e}")
    try:
        raw_methods["moment"] = _moment_mask_grs(image, nav)
    except Exception as e:
        notes.append(f"moment failed: {e}")

    # Pass 1: GRS latitude band + size sanity (JUPOS: reject wrong-feature locks)
    lat_sane: Dict[str, Dict[str, float]] = {}
    for name, m in raw_methods.items():
        if not _method_is_sane(m, ref_lon=None):
            rejected[name] = (
                f"rejected lat={m.get('lat_deg')} L={m.get('length_deg')} W={m.get('width_deg')}"
            )
            notes.append(f"{name} rejected as pathological / out of GRS lat band")
            m = dict(m)
            m["rejected"] = True
            methods[name] = m
            continue
        lat_sane[name] = m
        methods[name] = m

    # Pass 1b: lon cluster outlier reject (JUPOS Tips: remove outliers vs peer measures)
    if len(lat_sane) >= 2:
        try:
            from accuracy_gates import reject_lon_outliers
            kept_c, rej_c, med_lon = reject_lon_outliers(lat_sane, max_delta_deg=18.0)
            for n, reason in rej_c.items():
                if n in lat_sane:
                    rejected[n] = reason
                    notes.append(f"{n} {reason}")
                    mm = dict(lat_sane[n])
                    mm["rejected"] = True
                    methods[n] = mm
                    del lat_sane[n]
            if med_lon is not None:
                notes.append(f"lon cluster median={med_lon:.3f}° (GRS-band methods)")
        except Exception as e:
            notes.append(f"lon cluster filter skipped: {e}")

    if not lat_sane:
        if "template" in raw_methods:
            lat_sane = {"template": raw_methods["template"]}
            methods["template"] = raw_methods["template"]
            notes.append("fallback: only raw template available")
        else:
            raise RuntimeError("All GRS methods failed or rejected")

    # Pass 2: quality-aware lon cluster
    # High dark_contrast template is the most reliable dark-oval lock on Jupiter.
    # Do NOT let a thin wrong moment/map barge veto a good template.
    names = list(lat_sane.keys())
    lons = np.array([lat_sane[n]["lon_iii_deg"] for n in names], dtype=np.float64)
    lats = np.array([lat_sane[n]["lat_deg"] for n in names], dtype=np.float64)
    wts = np.array([METHOD_WEIGHTS.get(n, 1.0) for n in names], dtype=np.float64)

    tmpl = lat_sane.get("template")
    mom = lat_sane.get("moment")
    tmpl_quality = 0.0
    if tmpl is not None:
        tmpl_quality = float(tmpl.get("dark_contrast", 0.0)) + 0.01 * float(tmpl.get("score", 0.0))
        # latitude prior quality
        tmpl_quality *= float(np.exp(-0.5 * ((float(tmpl["lat_deg"]) + 22.0) / 5.0) ** 2))

    mom_quality = 0.0
    if mom is not None:
        L, W = float(mom.get("length_deg", 0)), float(mom.get("width_deg", 0))
        # thin filaments / micro-blobs are low quality
        size_ok = (4.0 <= L <= 22.0) and (2.0 <= W <= 14.0)
        mom_quality = (2.0 if size_ok else 0.15) * float(np.exp(-0.5 * ((float(mom["lat_deg"]) + 22.0) / 5.0) ** 2))

    # Seed: prefer high-quality template; else size-sane moment; else first
    if tmpl is not None and tmpl_quality >= 0.05:
        seed_lon = float(tmpl["lon_iii_deg"])
        notes.append(f"cluster seed = template (quality={tmpl_quality:.3f})")
    elif mom is not None and mom_quality >= 1.0:
        seed_lon = float(mom["lon_iii_deg"])
        notes.append(f"cluster seed = moment (quality={mom_quality:.3f})")
    else:
        seed_lon = float(lons[int(np.argmax(wts))])
        notes.append("cluster seed = highest weight method")

    d = np.array([abs(wrap_diff(x, seed_lon)) for x in lons])
    thr = 12.0
    keep = d <= thr

    # If template is high quality and moment is low quality far away, drop moment/map not template
    if tmpl is not None and mom is not None and "template" in names and "moment" in names:
        ti, mi = names.index("template"), names.index("moment")
        if abs(wrap_diff(lons[mi], lons[ti])) > 10.0:
            if tmpl_quality >= 0.05 and mom_quality < 1.0:
                keep[mi] = False
                keep[ti] = True
                notes.append("dropped low-quality moment (thin/wrong); kept template")
            elif mom_quality >= 1.0 and tmpl_quality < 0.03:
                keep[ti] = False
                keep[mi] = True
                notes.append("dropped weak template; kept size-sane moment")
            # else keep both if within thr of seed already

    # map_dark far from high-quality template → drop
    if tmpl is not None and "map_dark" in names and tmpl_quality >= 0.05:
        di = names.index("map_dark")
        ti = names.index("template")
        if abs(wrap_diff(lons[di], lons[ti])) > 10.0:
            keep[di] = False
            notes.append("dropped map_dark far from high-quality template")

    if keep.sum() == 0:
        keep = np.ones(len(lons), dtype=bool)
    # mark rejected cluster members
    for n, k in zip(names, keep):
        if not k and n in methods and not methods[n].get("rejected"):
            methods[n] = dict(methods[n])
            methods[n]["rejected"] = True
            methods[n]["reject_reason"] = "lon_cluster_outlier"
            rejected[n] = "lon_cluster_outlier"
            notes.append(f"{n} rejected as lon cluster outlier")

    lons_k, lats_k, wts_k = lons[keep], lats[keep], wts[keep]
    # boost template weight when high quality
    names_k = [n for n, k in zip(names, keep) if k]
    if "template" in names_k and tmpl_quality >= 0.05:
        for i, n in enumerate(names_k):
            if n == "template":
                wts_k[i] *= 2.5
    usable = {n: lat_sane[n] for n in names_k}

    lon = _circular_weighted_mean(lons_k, wts_k)
    lat = float(np.average(lats_k, weights=wts_k))

    # Template lock when quality is high (dark oval contrast)
    pos_tag = "consensus"
    if "template" in usable:
        tlon = usable["template"]["lon_iii_deg"]
        tlat = usable["template"]["lat_deg"]
        tq = float(usable["template"].get("dark_contrast", 0.0))
        others = [n for n in ("map_dark", "moment") if n in usable]
        if tq >= 0.04 or not others:
            lon = tlon
            lat = 0.80 * tlat + 0.20 * lat
            pos_tag = "template_pos"
            notes.append(f"position locked to template (dark_contrast={tq:.3f})")
        elif others:
            o_lons = np.array([usable[n]["lon_iii_deg"] for n in others], dtype=np.float64)
            o_mean = _circular_weighted_mean(o_lons, np.ones(len(o_lons)))
            if abs(wrap_diff(tlon, o_mean)) <= 6.0:
                lon = tlon
                lat = 0.75 * tlat + 0.25 * lat
                pos_tag = "template_pos"
                notes.append("position locked to template (agrees with dark methods)")
            else:
                notes.append("template blended via weighted consensus")

    # Optional SPIRE-Net: light on easy nights, stronger when physics methods scatter (hard cases)
    nn_prior = None
    try:
        import nn_grs
        if getattr(measure_grs_precision, "_use_nn", True):
            nn_prior = nn_grs.predict_soft_prior(image, nav, nav.cm_iii_deg)
    except Exception:
        nn_prior = None
    if nn_prior and float(nn_prior.get("confidence", 0)) > 0.25:
        nlon, nlat = float(nn_prior["lon_iii_deg"]), float(nn_prior["lat_deg"])
        # Physics hardness: scatter among usable methods
        hard = 0.0
        if len(lons_k) >= 2:
            dlon_p = np.array([wrap_diff(x, lon) for x in lons_k], dtype=np.float64)
            hard = float(np.std(dlon_p))
        # Easy: hard~0 → w≤0.12; hard≥2° scatter → w up to ~0.40
        conf = float(nn_prior["confidence"])
        w_base = 0.12 + 0.28 * min(1.0, hard / 2.0)
        lon_win = 8.0 + 6.0 * min(1.0, hard / 2.0)
        lat_win = 5.0 + 3.0 * min(1.0, hard / 2.0)
        if abs(wrap_diff(nlon, lon)) < lon_win and abs(nlat - lat) < lat_win:
            wnn = w_base * min(1.0, conf)
            lon = wrap_deg(lon + wnn * wrap_diff(nlon, lon))
            lat = (1 - wnn) * lat + wnn * nlat
            tag = "hard-case" if hard >= 0.8 else "soft"
            notes.append(
                f"SPIRE-Net {tag} prior blended w={wnn:.2f} conf={conf:.2f} "
                f"phys_scatter={hard:.3f}°"
            )
            methods["spire_net"] = {**nn_prior, "blend_w": wnn, "phys_scatter_deg": hard, "mode": tag}
        else:
            notes.append(
                f"SPIRE-Net prior ignored (disagrees with physics "
                f"Δlon={wrap_diff(nlon, lon):.2f}° / window={lon_win:.1f}°)"
            )
            nn_prior["rejected"] = True
            methods["spire_net"] = nn_prior

    length, width, size_src = _choose_size(usable)
    primary = f"{pos_tag}+{size_src}"

    if len(lons_k) >= 2:
        dlon = np.array([wrap_diff(x, lon) for x in lons_k])
        err_lon = float(np.sqrt(np.average(dlon ** 2, weights=wts_k)))
        err_lat = float(np.sqrt(np.average((lats_k - lat) ** 2, weights=wts_k)))
    else:
        err_lon, err_lat = 0.5, 0.3

    px_to_deg = (180.0 / math.pi) / (nav.a_eq_px + 1e-12)
    err_lon = max(err_lon, 0.35 * px_to_deg)
    err_lat = max(err_lat, 0.35 * px_to_deg)

    as_lon = deg_to_arcsec_on_sky(err_lon, km_per_deg_lon(lat), nav.distance_au)
    as_lat = deg_to_arcsec_on_sky(err_lat, km_per_deg_lat(), nav.distance_au)
    as_sky = float(math.hypot(as_lon, as_lat))
    quality = float(max(0.0, 1.0 - as_sky / 10.0))

    if not quiet:
        CONSOLE.ok(
            f"RESULT lon={lon:.4f}° lat={lat:.4f}°  L={length:.2f}° W={width:.2f}°  "
            f"σ_sky≈{as_sky:.3f}\"  keep={names_k}  rejected={list(rejected.keys())}"
        )
        if as_sky <= 2.0:
            CONSOLE.ok(f"Internal consistency TARGET: σ_sky={as_sky:.3f}\" ≤ 2.0\"")
        else:
            CONSOLE.warn(f"Methods disagree: σ_sky={as_sky:.3f}\"")

    try:
        lat_g = planetocentric_to_planetographic(lat)
    except Exception:
        lat_g = float("nan")
    return GRSPrecisionResult(
        lon_iii_deg=lon,
        lat_deg=lat,
        length_deg=length,
        width_deg=width,
        method=primary,
        methods=methods,
        err_lon_deg=err_lon,
        err_lat_deg=err_lat,
        err_sky_arcsec=as_sky,
        err_lon_arcsec=float(as_lon),
        err_lat_arcsec=float(as_lat),
        quality=quality,
        notes=notes
        + [
            "Position: template-first; pathological map_dark/moment rejected",
            "Size: prefer map-measured moment extent; template L/W are priors only",
            "lat_deg is planetocentric; lat_planetographic_deg is WinJUPOS-style",
            "err_sky is method consistency (not total systematic); MC is separate",
            "truth_recovery is absolute accuracy on synthetics",
        ],
        lat_planetographic_deg=lat_g,
        lat_kind="planetocentric",
    )


def monte_carlo_precision(
    image: np.ndarray,
    nav: NavState,
    n_iter: int = 60,
    seed: int = 0,
    max_iter: int = 100,
) -> Dict[str, Any]:
    """
    Fast Monte Carlo for measurement uncertainty.

    Perturbs the limb nav (centre + radius) and adds map-domain noise
    each iteration, then re-measures with template + map_dark only (faster
    than running all three methods every time). This gives the *measurement
    process* uncertainty, not the total systematic — you still need to add
    CM-source σ and definition systematics in quadrature.

    The key insight: remapping the cylindrical map each trial (with perturbed
    nav) captures geometry error, not just map noise. That's why this is more
    honest than just adding noise to a fixed map.
    """
    n_iter = int(min(max(n_iter, 0), max_iter))
    if n_iter < 5:
        return {"n_success": 0, "n_iter": n_iter, "skipped": True, "note": "MC skipped (n_iter < 5)"}

    rng = np.random.default_rng(seed)
    im0 = to_mono(image)
    # Base residual scale from nominal nav map (noise amplitude only)
    cyl0 = make_cylindrical(im0, nav, width=1000, height=500)
    residual = cyl0 - _gauss(cyl0, 1.5)
    mask0 = cyl0 > 0
    sigma = float(np.std(residual[mask0])) * 0.9 if mask0.any() else 0.01
    sigma = max(sigma, 1e-4)

    CONSOLE.info(
        f"Precision MC (remap nav each iter): N={n_iter}  (cap={max_iter})  "
        "limb centre/radius noise included"
    )
    lons: List[float] = []
    lats: List[float] = []
    t0 = __import__("time").time()

    for i in range(n_iter):
        # Perturb limb nav — re-project so MC captures geometry error (not only map noise)
        nav_i = NavState(
            xc=nav.xc + rng.normal(0, 0.35),
            yc=nav.yc + rng.normal(0, 0.35),
            a_eq_px=nav.a_eq_px * (1.0 + rng.normal(0, 0.0015)),
            flattening=nav.flattening,
            cm_iii_deg=nav.cm_iii_deg,
            distance_au=nav.distance_au,
            sub_lat_deg=float(getattr(nav, "sub_lat_deg", 0.0) or 0.0),
            north_pa_deg=float(getattr(nav, "north_pa_deg", 0.0) or 0.0),
        )
        try:
            cyl_i = make_cylindrical(im0, nav_i, width=1000, height=500)
            mask = cyl_i > 0
            noisy = cyl_i + rng.normal(0, sigma, cyl_i.shape)
            noisy = np.where(mask, noisy, 0.0)
            a = _template_match_grs(noisy, nav_i)
            b = _map_dark_centroid(noisy, nav_i)
            lon = _circular_weighted_mean(
                np.array([a["lon_iii_deg"], b["lon_iii_deg"]]),
                np.array([METHOD_WEIGHTS["template"], METHOD_WEIGHTS["map_dark"]]),
            )
            lat = 0.6 * a["lat_deg"] + 0.4 * b["lat_deg"]
            lons.append(lon)
            lats.append(lat)
        except Exception:
            continue

        if (i + 1) % max(1, n_iter // 5) == 0:
            CONSOLE.info(f"MC progress {i+1}/{n_iter}  ok={len(lons)}  elapsed={__import__('time').time()-t0:.1f}s")

    if len(lons) < 5:
        CONSOLE.warn(f"MC too few successes ({len(lons)})")
        return {"n_success": len(lons), "n_iter": n_iter, "error": "too few MC successes"}

    la = np.asarray(lons, dtype=np.float64)
    lb = np.asarray(lats, dtype=np.float64)
    r = np.deg2rad(la)
    lon_m = wrap_deg(rad2deg(math.atan2(np.sin(r).mean(), np.cos(r).mean())))
    R = math.hypot(np.cos(r).mean(), np.sin(r).mean())
    lon_s = rad2deg(math.sqrt(max(0.0, -2.0 * math.log(max(R, 1e-12)))))
    lat_m = float(np.mean(lb))
    lat_s = float(np.std(lb, ddof=1))
    sky = sky_error_arcsec(lon_s, lat_s, lat_m, nav.distance_au)
    as_lon = deg_to_arcsec_on_sky(lon_s, km_per_deg_lon(lat_m), nav.distance_au)
    as_lat = deg_to_arcsec_on_sky(lat_s, km_per_deg_lat(), nav.distance_au)
    elapsed = __import__("time").time() - t0
    CONSOLE.ok(
        f"MC DONE in {elapsed:.1f}s: σ_lon={lon_s:.3f}° ({as_lon:.3f}\")  "
        f"σ_lat={lat_s:.3f}° ({as_lat:.3f}\")  σ_sky={sky:.3f}\""
    )
    return {
        "n_success": len(lons),
        "n_iter": n_iter,
        "elapsed_s": elapsed,
        "mode": "remap_nav_template+map_dark",
        "mean": {"lon_iii_deg": lon_m, "lat_deg": lat_m},
        "std_deg": {"lon_iii_deg": lon_s, "lat_deg": lat_s},
        "std_arcsec": {"lon": float(as_lon), "lat": float(as_lat), "sky": float(sky)},
        "p16": {
            "lon_iii_deg": float(np.percentile(la, 16)),
            "lat_deg": float(np.percentile(lb, 16)),
        },
        "p84": {
            "lon_iii_deg": float(np.percentile(la, 84)),
            "lat_deg": float(np.percentile(lb, 84)),
        },
        "target_2_arcsec": bool(sky <= 2.0),
        "target_1_arcsec": bool(sky <= 1.0),
        "note": (
            "MC remaps cylindrical map each trial with limb-nav noise; "
            "still omits CM-source σ and definition systematics — add in quadrature for total. "
            "truth_recovery measures absolute accuracy on synthetics."
        ),
    }


def cap_mc_iterations(requested: int, megapixels: float = 8.0) -> int:
    """
    RAM-aware cap on MC iterations so huge frames don't crash.

    This was a practical necessity — my laptop has 16GB RAM and running
    1000 MC iterations on a 50MP image would absolutely OOM it. The caps
    are conservative but still allow research-grade requests on moderate
    images. If you need more iterations, crop the image first.
    """
    req = int(max(0, requested))
    # Absolute research ceiling
    absolute = 1000
    if megapixels > 50:
        hard = 80
    elif megapixels > 20:
        hard = 200
    elif megapixels > 8:
        hard = 500
    else:
        hard = absolute
    hard = min(hard, absolute)
    if req == 0:
        return min(60, hard)
    if req > hard:
        CONSOLE.warn(
            f"MC requested {req} → capped to {hard} "
            f"(~{megapixels:.1f} MP; raise by cropping / lower res for full {absolute})"
        )
    return min(req, hard)

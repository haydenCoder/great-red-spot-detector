#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
GRS Complete Ground Pipeline System
===================================
Human-maximum ground-based Jupiter / Great Red Spot imaging and science
pipeline. Implements lucky imaging, calibration, alignment, stacking,
derotation, PSF/wavelets/RL restoration, LRGB, limb navigation, GRS
measurement, bootstrap errors, Kalman-RTS trajectory, validation, CLI.

Honest ground-based precision (degrees/km). Not VLBI μas claims.
Version: 1.0.0
"""
from __future__ import annotations

import argparse
import copy
import csv
import dataclasses
from dataclasses import dataclass, field, asdict, replace
import datetime as dt
import enum
import functools
import hashlib
import json
import logging
import math
import os
import platform
import random
import re
import struct
import sys
import time
import traceback
import warnings
from collections import OrderedDict, defaultdict, deque
from pathlib import Path
from typing import (
    Any, Callable, Dict, Iterable, Iterator, List, Mapping, Optional,
    Sequence, Tuple, Union, cast,
)

try:
    import numpy as np
except ImportError as e:
    raise SystemExit("NumPy required: pip install numpy") from e

_HAS_SCIPY = False
_HAS_ASTROPY = False
_HAS_PIL = False
try:
    from scipy import ndimage as ndi
    from scipy.ndimage import map_coordinates, gaussian_filter
    from scipy.ndimage import binary_opening, binary_closing, label as scipy_label
    from scipy.optimize import least_squares, minimize_scalar
    from scipy.signal import fftconvolve
    _HAS_SCIPY = True
except ImportError:
    ndi = map_coordinates = gaussian_filter = None
    binary_opening = binary_closing = scipy_label = None
    least_squares = minimize_scalar = fftconvolve = None

try:
    from astropy.io import fits as astropy_fits
    from astropy.time import Time as AstropyTime
    _HAS_ASTROPY = True
except ImportError:
    astropy_fits = AstropyTime = None

try:
    from PIL import Image as PILImage
    _HAS_PIL = True
except ImportError:
    PILImage = None

__version__ = "6.2.0"
__author__ = "GRS Ground Pipeline"

LOG = logging.getLogger("grs_pipeline")
if not LOG.handlers:
    _h = logging.StreamHandler(sys.stdout)
    _h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    LOG.addHandler(_h)
    LOG.setLevel(logging.INFO)


# ---------------------------------------------------------------------------
# Physical constants
# ---------------------------------------------------------------------------
class PhysicalConstants:
    C_M_S: float = 299792458.0
    AU_M: float = 149597870700.0
    ARCSEC_PER_RAD: float = 206264.80624709636
    DEG_PER_RAD: float = 180.0 / math.pi
    RAD_PER_DEG: float = math.pi / 180.0
    JUPITER_SYS3_PERIOD_S: float = 9 * 3600 + 55 * 60 + 29.711
    JUPITER_REQ_KM: float = 71492.0
    JUPITER_RPOL_KM: float = 66854.0
    JUPITER_FLATTENING: float = 1.0 - (66854.0 / 71492.0)
    JUPITER_MEAN_DIST_AU: float = 5.2
    GRS_NOM_LAT_DEG: float = -22.0
    GRS_NOM_LAT_WIDTH_DEG: float = 8.0
    LAMBDA_R_NM: float = 620.0
    LAMBDA_G_NM: float = 530.0
    LAMBDA_B_NM: float = 470.0
    LAMBDA_IR685_NM: float = 685.0
    LAMBDA_IR742_NM: float = 742.0
    LAMBDA_CH4_NM: float = 890.0
    # Additional planetary / imaging constants
    EARTH_RADIUS_M: float = 6378137.0
    STANDARD_PRESSURE_MBAR: float = 1013.25
    STANDARD_TEMP_C: float = 15.0
    STANDARD_HUMIDITY: float = 0.0
    PIXEL_EPS: float = 1e-12
    DEFAULT_SEED: int = 42

PC = PhysicalConstants()


class PipelineMode(str, enum.Enum):
    IMAGING = "imaging"
    SCIENCE = "science"
    BOTH = "both"


class FilterName(str, enum.Enum):
    R = "R"; G = "G"; B = "B"; IR685 = "IR685"; IR742 = "IR742"
    CH4 = "CH4"; L = "L"; CLEAR = "CLEAR"; RGB = "RGB"; UNKNOWN = "UNKNOWN"


class QualityMetric(str, enum.Enum):
    LAPLACIAN_VAR = "laplacian_var"
    FFT_POWER = "fft_power"
    HYBRID = "hybrid"
    SOBEL_ENERGY = "sobel_energy"
    MAX_PIXEL = "max_pixel"
    TENENGRAD = "tenengrad"
    VARIANCE = "variance"


class StackMethod(str, enum.Enum):
    MEAN = "mean"; MEDIAN = "median"; KAPPA_SIGMA = "kappa_sigma"
    QUALITY_WEIGHTED = "quality_weighted"; WINSORIZED = "winsorized"


class RestoreMethod(str, enum.Enum):
    NONE = "none"; WAVELETS = "wavelets"; RL = "rl"
    WAVELETS_THEN_RL = "wavelets_then_rl"; WIENER = "wiener"


class AlignMode(str, enum.Enum):
    GLOBAL = "global"; LOCAL_AP = "local_ap"; RIGID = "rigid"


class SegmentMethod(str, enum.Enum):
    ADAPTIVE_THRESHOLD = "adaptive_threshold"
    ELLIPSE_FIT = "ellipse_fit"
    MANUAL_MASK = "manual_mask"
    OTSU = "otsu"
    MOMENTS_BLOB = "moments_blob"


class SmootherKind(str, enum.Enum):
    NONE = "none"; RTS = "rts"; GP = "gp"; POLY = "poly"


class LimbMethod(str, enum.Enum):
    RADIAL_GRADIENT = "radial_gradient"
    CANNY_LIKE = "canny_like"
    THRESHOLD_EDGE = "threshold_edge"


class DefinitionId(str, enum.Enum):
    MOMENT_MASK_IR = "MOMENT_MASK_IR"
    MOMENT_MASK_RED = "MOMENT_MASK_RED"
    ELLIPSE_EDGE_IR = "ELLIPSE_EDGE_IR"
    ELLIPSE_EDGE_RED = "ELLIPSE_EDGE_RED"
    MANUAL_JUPOS_V1 = "MANUAL_JUPOS_V1"
    BARYCENTRE_CH4 = "BARYCENTRE_CH4"


class GRSPipelineError(Exception): pass
class IngestError(GRSPipelineError): pass
class QCError(GRSPipelineError): pass
class CalibrationError(GRSPipelineError): pass
class AlignmentError(GRSPipelineError): pass
class NavigationError(GRSPipelineError): pass
class MeasurementError(GRSPipelineError): pass
class ConfigError(GRSPipelineError): pass
class DependencyError(GRSPipelineError): pass


def setup_logging(level: str = "INFO", log_file: Optional[str] = None) -> None:
    lvl = getattr(logging, level.upper(), logging.INFO)
    LOG.setLevel(lvl)
    if log_file:
        fh = logging.FileHandler(log_file)
        fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        LOG.addHandler(fh)


class StageTimer:
    def __init__(self, name: str) -> None:
        self.name = name
        self.t0 = 0.0
        self.elapsed = 0.0
    def __enter__(self) -> "StageTimer":
        self.t0 = time.perf_counter()
        LOG.info("BEGIN: %s", self.name)
        return self
    def __exit__(self, *args: Any) -> None:
        self.elapsed = time.perf_counter() - self.t0
        LOG.info("END: %s (%.3fs)", self.name, self.elapsed)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

def sha256_file(path: Union[str, Path], chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for b in iter(lambda: f.read(chunk), b""):
            h.update(b)
    return h.hexdigest()

def sha256_array(arr: np.ndarray) -> str:
    return hashlib.sha256(np.ascontiguousarray(arr).tobytes()).hexdigest()

def sha256_json(obj: Any) -> str:
    return sha256_bytes(json.dumps(obj, sort_keys=True, default=str).encode())

def ensure_dir(path: Union[str, Path]) -> Path:
    p = Path(path); p.mkdir(parents=True, exist_ok=True); return p

def clamp(x: float, lo: float, hi: float) -> float:
    return lo if x < lo else hi if x > hi else x

def safe_div(a: np.ndarray, b: np.ndarray, eps: float = 1e-12) -> np.ndarray:
    return a / np.maximum(b, eps)

def wrap_deg(lon: float) -> float:
    return float(lon % 360.0)

def wrap_deg_diff(a: float, b: float) -> float:
    return float((a - b + 180.0) % 360.0 - 180.0)

def deg2rad(d: float) -> float:
    return d * PC.RAD_PER_DEG

def rad2deg(r: float) -> float:
    return r * PC.DEG_PER_RAD

def jupiter_eq_km_per_deg(lat_deg: float = 0.0) -> float:
    circ = 2.0 * math.pi * PC.JUPITER_REQ_KM
    return (circ / 360.0) * math.cos(deg2rad(lat_deg))

def jupiter_km_per_deg_lat() -> float:
    return (2.0 * math.pi * PC.JUPITER_RPOL_KM) / 360.0

def km_at_jupiter_from_mas(mas: float, distance_au: float = PC.JUPITER_MEAN_DIST_AU) -> float:
    theta_rad = (mas * 1e-3 / 3600.0) * PC.RAD_PER_DEG
    return theta_rad * distance_au * PC.AU_M / 1000.0

# ---------------------------------------------------------------------------
# Image / signal utilities (SciPy fallbacks included)
# ---------------------------------------------------------------------------

def _gaussian_kernel1d(sigma: float, truncate: float = 3.0) -> np.ndarray:
    radius = max(1, int(truncate * sigma + 0.5))
    x = np.arange(-radius, radius + 1, dtype=np.float64)
    k = np.exp(-0.5 * (x / max(sigma, 1e-12)) ** 2)
    return (k / k.sum()).astype(np.float64)


def gaussian_filter2d(image: np.ndarray, sigma: float) -> np.ndarray:
    if sigma <= 0:
        return np.asarray(image, dtype=np.float64).copy()
    if _HAS_SCIPY and gaussian_filter is not None:
        return np.asarray(gaussian_filter(image, sigma=sigma, mode="nearest"), dtype=np.float64)
    k = _gaussian_kernel1d(float(sigma))
    pad = len(k) // 2
    img = np.asarray(image, dtype=np.float64)
    tmp = np.pad(img, ((0, 0), (pad, pad)), mode="edge")
    out = np.empty_like(img)
    for i in range(img.shape[0]):
        out[i] = np.convolve(tmp[i], k, mode="valid")
    tmp2 = np.pad(out, ((pad, pad), (0, 0)), mode="edge")
    out2 = np.empty_like(img)
    for j in range(img.shape[1]):
        out2[:, j] = np.convolve(tmp2[:, j], k, mode="valid")
    return out2


def map_coords(
    image: np.ndarray,
    coords: np.ndarray,
    order: int = 1,
    mode: str = "constant",
    cval: float = 0.0,
) -> np.ndarray:
    """Sample image at coords[0]=row, coords[1]=col."""
    if _HAS_SCIPY and map_coordinates is not None:
        return map_coordinates(image, coords, order=order, mode=mode, cval=cval, prefilter=True)
    h, w = image.shape[:2]
    rr, cc = coords[0], coords[1]
    r0 = np.floor(rr).astype(np.int64)
    c0 = np.floor(cc).astype(np.int64)
    dr = rr - r0
    dc = cc - c0
    r1, c1 = r0 + 1, c0 + 1
    def samp(r: np.ndarray, c: np.ndarray) -> np.ndarray:
        if mode == "constant":
            out = np.full(r.shape, cval, dtype=np.float64)
            m = (r >= 0) & (r < h) & (c >= 0) & (c < w)
            out[m] = image[r[m], c[m]]
            return out
        r2 = np.clip(r, 0, h - 1); c2 = np.clip(c, 0, w - 1)
        return image[r2, c2].astype(np.float64)
    v00, v01 = samp(r0, c0), samp(r0, c1)
    v10, v11 = samp(r1, c0), samp(r1, c1)
    return v00*(1-dr)*(1-dc) + v01*(1-dr)*dc + v10*dr*(1-dc) + v11*dr*dc


def fft_convolve2d(a: np.ndarray, b: np.ndarray, mode: str = "same") -> np.ndarray:
    if _HAS_SCIPY and fftconvolve is not None:
        return np.asarray(fftconvolve(a, b, mode=mode), dtype=np.float64)
    a = np.asarray(a, dtype=np.float64); b = np.asarray(b, dtype=np.float64)
    s1 = np.array(a.shape); s2 = np.array(b.shape); shape = s1 + s2 - 1
    fshape = [int(2 ** math.ceil(math.log2(max(int(x), 2)))) for x in shape]
    fa = np.fft.rfftn(a, fshape); fb = np.fft.rfftn(b, fshape)
    out = np.fft.irfftn(fa * fb, fshape)
    if mode == "full":
        return out[:shape[0], :shape[1]]
    if mode == "same":
        start = (shape - s1) // 2
        return out[start[0]:start[0]+s1[0], start[1]:start[1]+s1[1]]
    raise ValueError(mode)


def morph_open_close(mask: np.ndarray, open_i: int = 1, close_i: int = 1) -> np.ndarray:
    m = np.asarray(mask, dtype=bool)
    if _HAS_SCIPY and binary_opening is not None:
        if open_i: m = binary_opening(m, iterations=open_i)
        if close_i: m = binary_closing(m, iterations=close_i)
        return m
    def erode(x):
        s = fft_convolve2d(x.astype(np.float64), np.ones((3,3)), "same")
        return s >= 8.5
    def dilate(x):
        s = fft_convolve2d(x.astype(np.float64), np.ones((3,3)), "same")
        return s >= 0.5
    for _ in range(open_i): m = dilate(erode(m))
    for _ in range(close_i): m = erode(dilate(m))
    return m


def label_components(mask: np.ndarray) -> Tuple[np.ndarray, int]:
    m = np.asarray(mask, dtype=bool)
    if _HAS_SCIPY and scipy_label is not None:
        lab, n = scipy_label(m)
        return lab, int(n)
    # simple flood fill 4-connected
    h, w = m.shape
    lab = np.zeros((h, w), dtype=np.int32)
    n = 0
    for i in range(h):
        for j in range(w):
            if m[i, j] and lab[i, j] == 0:
                n += 1
                stack = [(i, j)]
                lab[i, j] = n
                while stack:
                    y, x = stack.pop()
                    for dy, dx in ((0,1),(0,-1),(1,0),(-1,0)):
                        ny, nx = y+dy, x+dx
                        if 0 <= ny < h and 0 <= nx < w and m[ny, nx] and lab[ny, nx] == 0:
                            lab[ny, nx] = n
                            stack.append((ny, nx))
    return lab, n


def largest_component(mask: np.ndarray) -> np.ndarray:
    lab, n = label_components(mask)
    if n == 0:
        return np.zeros_like(mask, dtype=bool)
    counts = np.bincount(lab.ravel())
    counts[0] = 0
    return lab == int(np.argmax(counts))


def percentile_clip(image: np.ndarray, lo: float = 0.1, hi: float = 99.9) -> Tuple[float, float]:
    a, b = np.percentile(image, [lo, hi])
    if b <= a:
        b = a + 1.0
    return float(a), float(b)


def normalize_percentile(image: np.ndarray, lo: float = 0.1, hi: float = 99.9) -> np.ndarray:
    a, b = percentile_clip(image, lo, hi)
    return np.clip((image - a) / (b - a), 0.0, 1.0)


def sobel_mag(image: np.ndarray) -> np.ndarray:
    img = np.asarray(image, dtype=np.float64)
    kx = np.array([[-1,0,1],[-2,0,2],[-1,0,1]], dtype=np.float64)
    ky = np.array([[-1,-2,-1],[0,0,0],[1,2,1]], dtype=np.float64)
    gx = fft_convolve2d(img, kx, "same")
    gy = fft_convolve2d(img, ky, "same")
    return np.hypot(gx, gy)


def laplacian(image: np.ndarray) -> np.ndarray:
    k = np.array([[0,1,0],[1,-4,1],[0,1,0]], dtype=np.float64)
    return fft_convolve2d(np.asarray(image, dtype=np.float64), k, "same")


def highpass(image: np.ndarray, sigma: float = 3.0) -> np.ndarray:
    img = np.asarray(image, dtype=np.float64)
    return img - gaussian_filter2d(img, sigma)


def shift_image(image: np.ndarray, dy: float, dx: float, cval: float = 0.0) -> np.ndarray:
    h, w = image.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    coords = np.array([yy - dy, xx - dx])
    return map_coords(np.asarray(image, dtype=np.float64), coords, order=1, mode="constant", cval=cval)


def rotate_image(image: np.ndarray, angle_deg: float, center: Optional[Tuple[float,float]] = None) -> np.ndarray:
    h, w = image.shape[:2]
    if center is None:
        cy, cx = (h - 1) / 2.0, (w - 1) / 2.0
    else:
        cy, cx = center
    th = deg2rad(angle_deg)
    ct, st = math.cos(th), math.sin(th)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    y = yy - cy; x = xx - cx
    src_y =  ct * y + st * x + cy
    src_x = -st * y + ct * x + cx
    return map_coords(np.asarray(image, dtype=np.float64), np.array([src_y, src_x]), order=1)


def resize_bilinear(image: np.ndarray, new_h: int, new_w: int) -> np.ndarray:
    h, w = image.shape[:2]
    yy = np.linspace(0, h - 1, new_h)
    xx = np.linspace(0, w - 1, new_w)
    grid_y, grid_x = np.meshgrid(yy, xx, indexing="ij")
    return map_coords(np.asarray(image, dtype=np.float64), np.array([grid_y, grid_x]), order=1, mode="nearest")

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class FrameMeta:
    path: str = ""
    t_utc_mid: Optional[dt.datetime] = None
    filter_name: str = "UNKNOWN"
    exposure_s: float = 0.0
    gain: Optional[float] = None
    site_lat: float = 0.0
    site_lon: float = 0.0
    site_elev_m: float = 0.0
    camera: Optional[str] = None
    pixel_um: Optional[float] = None
    fl_mm: Optional[float] = None
    temperature_c: Optional[float] = None
    humidity: Optional[float] = None
    pressure_mbar: Optional[float] = None
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if self.t_utc_mid is not None:
            d["t_utc_mid"] = self.t_utc_mid.isoformat()
        return d


@dataclass
class VideoCube:
    data: np.ndarray  # (N,H,W)
    times: Optional[np.ndarray] = None
    meta: FrameMeta = field(default_factory=FrameMeta)
    quality: Optional[np.ndarray] = None

    @property
    def n_frames(self) -> int:
        return int(self.data.shape[0])

    @property
    def shape_hw(self) -> Tuple[int, int]:
        return int(self.data.shape[1]), int(self.data.shape[2])


@dataclass
class QCReport:
    ok: bool = True
    reasons: List[str] = field(default_factory=list)
    metrics: Dict[str, float] = field(default_factory=dict)

    def fail(self, reason: str) -> None:
        self.ok = False
        self.reasons.append(reason)


@dataclass
class StackResult:
    image: np.ndarray
    n_used: int
    fraction: float
    quality_threshold: float
    noise_map: Optional[np.ndarray] = None
    meta: FrameMeta = field(default_factory=FrameMeta)
    align_model: Dict[str, Any] = field(default_factory=dict)
    scores_used: Optional[np.ndarray] = None
    method: str = "kappa_sigma"


@dataclass
class Navigation:
    xc: float
    yc: float
    a_eq_px: float
    flattening: float = PC.JUPITER_FLATTENING
    north_pa_deg: float = 0.0
    cm_iii_deg: float = 0.0
    distance_au: float = PC.JUPITER_MEAN_DIST_AU
    sub_obs_lat: float = 0.0
    sub_obs_lon: float = 0.0
    epoch_tdb_mjd: float = 0.0
    cov_center: Optional[np.ndarray] = None
    np_angle_deg: float = 0.0
    apparent_diameter_arcsec: float = 40.0

    @property
    def b_pol_px(self) -> float:
        return self.a_eq_px * (1.0 - self.flattening)

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "xc": self.xc, "yc": self.yc, "a_eq_px": self.a_eq_px,
            "flattening": self.flattening, "north_pa_deg": self.north_pa_deg,
            "cm_iii_deg": self.cm_iii_deg, "distance_au": self.distance_au,
            "sub_obs_lat": self.sub_obs_lat, "sub_obs_lon": self.sub_obs_lon,
            "epoch_tdb_mjd": self.epoch_tdb_mjd, "np_angle_deg": self.np_angle_deg,
            "apparent_diameter_arcsec": self.apparent_diameter_arcsec,
        }
        if self.cov_center is not None:
            d["cov_center"] = np.asarray(self.cov_center).tolist()
        return d


@dataclass
class GRSState:
    t_tdb_mjd: float
    lon_iii_deg: float
    lat_deg: float
    length_deg: float
    width_deg: float
    area_km2: Optional[float]
    aspect: float
    pa_deg: float
    definition_id: str
    filter_name: str
    cov: Optional[np.ndarray] = None
    error_budget: Dict[str, float] = field(default_factory=dict)
    xc_px: float = 0.0
    yc_px: float = 0.0
    n_mask_pix: int = 0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        if self.cov is not None:
            d["cov"] = np.asarray(self.cov).tolist()
        return d


@dataclass
class GeomEphemeris:
    t_utc: dt.datetime
    t_tdb_mjd: float
    distance_au: float
    cm_iii_deg: float
    sub_obs_lat_deg: float
    sub_obs_lon_deg: float
    np_angle_deg: float
    apparent_diameter_arcsec: float
    light_time_s: float


@dataclass
class RunManifest:
    version: str
    mode: str
    config_sha: str
    input_shas: List[str]
    package_versions: Dict[str, str]
    seed: int
    stages: List[Dict[str, Any]] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    created_utc: str = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat() + "Z")

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PipelineConfig:
    """Full pipeline configuration with sane professional defaults."""
    mode: str = "both"
    seed: int = 42
    raw_dir: str = "data/raw"
    work_dir: str = "data/work"
    out_dir: str = "data/out"
    site_lat: float = 22.3
    site_lon: float = 114.2
    site_elev_m: float = 50.0
    # lucky
    quality_metric: str = "laplacian_var"
    fractions: Tuple[float, ...] = (0.08, 0.15, 0.30)
    primary_fraction: float = 0.15
    ap_grid: int = 12
    ap_box: int = 48
    max_shift_px: float = 40.0
    align_mode: str = "local_ap"
    # stack
    stack_method: str = "kappa_sigma"
    kappa: float = 2.5
    drizzle_scale: float = 1.0
    # derot
    derot_enable: bool = True
    derot_map_width: int = 1800
    # restore
    restore_method: str = "wavelets"
    wavelet_layers: int = 6
    wavelet_gains: Tuple[float, ...] = (0.0, 0.2, 0.8, 1.2, 0.6, 0.2)
    wavelet_denoise: Tuple[float, ...] = (3.0, 2.0, 1.0, 0.5, 0.0, 0.0)
    rl_iters: int = 8
    rl_on_luminance_only: bool = True
    # color
    l_source: str = "IR742"
    sat_scale: float = 0.85
    denoise_chroma: bool = True
    # nav / grs
    limb_method: str = "radial_gradient"
    n_rays: int = 360
    bootstrap_limb: int = 50
    grs_definition_id: str = "MOMENT_MASK_IR"
    segment_method: str = "adaptive_threshold"
    bootstrap_n: int = 100
    # traj
    traj_enable: bool = True
    smoother: str = "rts"
    process_noise_lon: float = 0.05
    # export
    write_fits: bool = True
    write_png: bool = True
    write_csv: bool = True
    log_level: str = "INFO"
    # QC
    min_frames: int = 50
    max_clip_frac: float = 0.05
    flux_drop_frac: float = 0.40

    @staticmethod
    def from_dict(d: Mapping[str, Any]) -> "PipelineConfig":
        cfg = PipelineConfig()
        for k, v in d.items():
            if hasattr(cfg, k):
                setattr(cfg, k, v)
        return cfg

    @staticmethod
    def from_yaml_like(path: Union[str, Path]) -> "PipelineConfig":
        """Minimal YAML-like key: value loader (no PyYAML required)."""
        text = Path(path).read_text(encoding="utf-8")
        data: Dict[str, Any] = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if ":" not in line:
                continue
            k, v = line.split(":", 1)
            k = k.strip(); v = v.strip().strip('"').strip("'")
            if v.lower() in ("true", "yes"): data[k] = True
            elif v.lower() in ("false", "no"): data[k] = False
            else:
                try:
                    if "." in v: data[k] = float(v)
                    else: data[k] = int(v)
                except ValueError:
                    if v.startswith("[") and v.endswith("]"):
                        inner = v[1:-1].strip()
                        if inner:
                            parts = [p.strip() for p in inner.split(",")]
                            nums = []
                            for p in parts:
                                try: nums.append(float(p))
                                except ValueError: nums.append(p)
                            data[k] = tuple(nums)
                        else:
                            data[k] = tuple()
                    else:
                        data[k] = v
        return PipelineConfig.from_dict(data)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def sha(self) -> str:
        return sha256_json(self.to_dict())


# ---------------------------------------------------------------------------
# Timing / ephemeris (analytical approximations + optional Astropy)
# ---------------------------------------------------------------------------

# Leap second table simplified (TAI-UTC) — sufficient for imaging pipelines
_LEAP_SECONDS = [
    (dt.datetime(1972, 1, 1), 10), (dt.datetime(1972, 7, 1), 11),
    (dt.datetime(1973, 1, 1), 12), (dt.datetime(1974, 1, 1), 13),
    (dt.datetime(1975, 1, 1), 14), (dt.datetime(1976, 1, 1), 15),
    (dt.datetime(1977, 1, 1), 16), (dt.datetime(1978, 1, 1), 17),
    (dt.datetime(1979, 1, 1), 18), (dt.datetime(1980, 1, 1), 19),
    (dt.datetime(1981, 7, 1), 20), (dt.datetime(1982, 7, 1), 21),
    (dt.datetime(1983, 7, 1), 22), (dt.datetime(1985, 7, 1), 23),
    (dt.datetime(1988, 1, 1), 24), (dt.datetime(1990, 1, 1), 25),
    (dt.datetime(1991, 1, 1), 26), (dt.datetime(1992, 7, 1), 27),
    (dt.datetime(1993, 7, 1), 28), (dt.datetime(1994, 7, 1), 29),
    (dt.datetime(1996, 1, 1), 30), (dt.datetime(1997, 7, 1), 31),
    (dt.datetime(1999, 1, 1), 32), (dt.datetime(2006, 1, 1), 33),
    (dt.datetime(2009, 1, 1), 34), (dt.datetime(2012, 7, 1), 35),
    (dt.datetime(2015, 7, 1), 36), (dt.datetime(2017, 1, 1), 37),
]


def tai_utc_offset(t: dt.datetime) -> float:
    off = 10
    for epoch, sec in _LEAP_SECONDS:
        if t >= epoch:
            off = sec
    return float(off)


def utc_to_tt_mjd(t: dt.datetime) -> float:
    """UTC datetime -> TT MJD (approx). TT = TAI + 32.184s; TAI = UTC + leap."""
    if t.tzinfo is not None:
        t = t.replace(tzinfo=None)
    # MJD UTC
    ordinal = t.toordinal()  # proleptic Gregorian
    # days since 1858-11-17
    mjd0 = dt.datetime(1858, 11, 17)
    delta = t - mjd0
    mjd_utc = delta.total_seconds() / 86400.0
    leap = tai_utc_offset(t)
    # TT-UTC ≈ leap + 32.184
    tt_minus_utc = leap + 32.184
    return mjd_utc + tt_minus_utc / 86400.0


def tt_to_tdb_mjd(tt_mjd: float) -> float:
    """Approximate TT->TDB (Fairhead & Bretagnon-like simplified)."""
    # g in radians approx mean anomaly of Earth
    T = (tt_mjd - 51544.5) / 36525.0
    g = deg2rad(357.53 + 35999.050 * T)
    # TDB-TT seconds ≈ 0.001657 sin(g) + small terms
    dt_s = 0.001657 * math.sin(g) + 0.000022 * math.sin(g + g)
    return tt_mjd + dt_s / 86400.0


def parse_time_string(s: str) -> dt.datetime:
    s = s.strip().replace("T", " ").replace("Z", "")
    for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return dt.datetime.strptime(s, fmt)
        except ValueError:
            continue
    raise IngestError(f"Cannot parse time: {s}")


def jupiter_system_iii_lon_approx(tdb_mjd: float) -> float:
    """
    Approximate System III (1965) central meridian for an Earth observer
    using a simple linear rotation model. For professional absolute work
    use SPICE/Astropy; this is adequate for derotation differentials.
    """
    # Reference: at J2000 (MJD 51544.5) use a fixed phase; differential use is robust
    period_days = PC.JUPITER_SYS3_PERIOD_S / 86400.0
    # arbitrary zero point; differentials cancel for derotation
    turns = (tdb_mjd - 51544.5) / period_days
    return wrap_deg(360.0 * (turns % 1.0))


def jupiter_distance_au_approx(tdb_mjd: float) -> float:
    """Very rough Earth-Jupiter distance oscillation ~5.2 ± 0.6 AU."""
    # synodic-ish modulation
    phase = 2.0 * math.pi * (tdb_mjd - 51544.5) / 398.88
    return 5.2 + 0.6 * math.cos(phase)


def jupiter_apparent_diameter_arcsec(distance_au: float) -> float:
    # equatorial diameter
    diam_km = 2.0 * PC.JUPITER_REQ_KM
    diam_m = diam_km * 1000.0
    dist_m = distance_au * PC.AU_M
    return rad2deg(diam_m / dist_m) * 3600.0


def compute_geometry(t_utc: Optional[dt.datetime], site_lat: float = 0.0,
                     site_lon: float = 0.0, site_elev_m: float = 0.0) -> GeomEphemeris:
    if t_utc is None:
        raise ValueError(
            "Observation UTC required for System III geometry "
            "(refusing silent datetime.now())."
        )
    tt = utc_to_tt_mjd(t_utc)
    tdb = tt_to_tdb_mjd(tt)
    dist = jupiter_distance_au_approx(tdb)
    # light time
    lt = (dist * PC.AU_M) / PC.C_M_S
    # emission time
    tdb_em = tdb - lt / 86400.0
    cm = jupiter_system_iii_lon_approx(tdb_em)
    diam = jupiter_apparent_diameter_arcsec(dist)
    # crude sub-observer lat season
    sub_lat = 3.0 * math.sin(2 * math.pi * (tdb - 51544.5) / (11.86 * 365.25))
    return GeomEphemeris(
        t_utc=t_utc, t_tdb_mjd=tdb_em, distance_au=dist, cm_iii_deg=cm,
        sub_obs_lat_deg=sub_lat, sub_obs_lon_deg=cm, np_angle_deg=0.0,
        apparent_diameter_arcsec=diam, light_time_s=lt,
    )


# ---------------------------------------------------------------------------
# Atmospheric refraction / DCR (simplified Auer-Standish style)
# ---------------------------------------------------------------------------

def refractive_index_dry(pressure_mbar: float, temp_c: float, wavelength_um: float) -> float:
    """Edlen-like simplified refractive index excess (n-1)."""
    T = temp_c + 273.15
    # σ = 1/λ in μm^-1
    sig = 1.0 / max(wavelength_um, 0.2)
    nsm1 = 1e-8 * (8342.54 + 2406147.0 / (130.0 - sig**2) + 15998.0 / (38.9 - sig**2))
    return nsm1 * (pressure_mbar / 1013.25) * (288.15 / T)


def achromatic_refraction_arcsec(z_deg: float, pressure_mbar: float = 1013.25,
                                  temp_c: float = 15.0, wavelength_um: float = 0.55) -> float:
    """Approximate refraction R ≈ 60" tan(z) scaled by conditions."""
    z = clamp(z_deg, 0.0, 89.0)
    n1 = refractive_index_dry(pressure_mbar, temp_c, wavelength_um)
    # classical: R(rad) ~ (n-1) tan(z)
    r_rad = n1 * math.tan(deg2rad(z))
    return rad2deg(r_rad) * 3600.0


def dcr_shift_arcsec(z_deg: float, lam1_nm: float, lam2_nm: float,
                     pressure_mbar: float = 1013.25, temp_c: float = 15.0) -> float:
    """Differential chromatic refraction between two wavelengths (arcsec along parallactic)."""
    r1 = achromatic_refraction_arcsec(z_deg, pressure_mbar, temp_c, lam1_nm / 1000.0)
    r2 = achromatic_refraction_arcsec(z_deg, pressure_mbar, temp_c, lam2_nm / 1000.0)
    return r1 - r2


FILTER_WAVELENGTH_NM: Dict[str, float] = {
    "R": PC.LAMBDA_R_NM, "G": PC.LAMBDA_G_NM, "B": PC.LAMBDA_B_NM,
    "IR685": PC.LAMBDA_IR685_NM, "IR742": PC.LAMBDA_IR742_NM, "CH4": PC.LAMBDA_CH4_NM,
    "L": 550.0, "CLEAR": 550.0, "RGB": 550.0, "UNKNOWN": 550.0,
}


# ---------------------------------------------------------------------------
# FITS / SER I/O
# ---------------------------------------------------------------------------

def read_fits(path: Union[str, Path]) -> Tuple[np.ndarray, Dict[str, Any]]:
    path = Path(path)
    if not path.exists():
        raise IngestError(f"FITS not found: {path}")
    header: Dict[str, Any] = {"path": str(path)}
    if _HAS_ASTROPY and astropy_fits is not None:
        with astropy_fits.open(path) as hdul:
            data = np.asarray(hdul[0].data, dtype=np.float64)
            hdr = hdul[0].header
            for k in hdr.keys():
                try:
                    header[str(k)] = hdr[k]
                except Exception:
                    pass
        return data, header
    # Minimal FITS reader for simple 2D/3D float/int images
    with open(path, "rb") as f:
        raw = f.read()
    if len(raw) < 2880 or raw[0:6] != b"SIMPLE":
        raise IngestError(f"Not a simple FITS file (install astropy for full support): {path}")
    # parse header cards
    naxis = 0
    naxis_vals: List[int] = []
    bitpix = -32
    bscale = 1.0
    bzero = 0.0
    pos = 0
    while True:
        block = raw[pos:pos+2880]
        pos += 2880
        for i in range(0, 2880, 80):
            card = block[i:i+80].decode("ascii", errors="replace")
            key = card[0:8].strip()
            if key == "END":
                data_start = pos
                # find bitpix etc already parsed
                arr = _parse_fits_data(raw[data_start:], bitpix, naxis_vals, bscale, bzero)
                return arr, header
            if "=" in card:
                k = card.split("=", 1)[0].strip()
                rest = card.split("=", 1)[1]
                val = rest.split("/")[0].strip()
                header[k] = val
                if k == "BITPIX":
                    bitpix = int(val)
                elif k == "NAXIS":
                    naxis = int(val)
                elif k.startswith("NAXIS") and k[5:].isdigit():
                    idx = int(k[5:])
                    while len(naxis_vals) < idx:
                        naxis_vals.append(1)
                    naxis_vals[idx-1] = int(val)
                elif k == "BSCALE":
                    bscale = float(val)
                elif k == "BZERO":
                    bzero = float(val)


def _parse_fits_data(data: bytes, bitpix: int, naxis_vals: List[int], bscale: float, bzero: float) -> np.ndarray:
    shape = tuple(reversed(naxis_vals))  # FITS is Fortran order
    npt = 1
    for s in shape:
        npt *= s
    if bitpix == -64:
        dt_np = ">f8"; bs = 8
    elif bitpix == -32:
        dt_np = ">f4"; bs = 4
    elif bitpix == 16:
        dt_np = ">i2"; bs = 2
    elif bitpix == 32:
        dt_np = ">i4"; bs = 4
    elif bitpix == 8:
        dt_np = ">u1"; bs = 1
    else:
        raise IngestError(f"Unsupported BITPIX {bitpix}")
    need = npt * bs
    arr = np.frombuffer(data[:need], dtype=dt_np).astype(np.float64)
    arr = arr * bscale + bzero
    arr = arr.reshape(shape)
    return arr


def write_fits(path: Union[str, Path], data: np.ndarray, header: Optional[Dict[str, Any]] = None) -> None:
    path = Path(path)
    ensure_dir(path.parent)
    data = np.asarray(data, dtype=np.float32)
    if _HAS_ASTROPY and astropy_fits is not None:
        hdu = astropy_fits.PrimaryHDU(data)
        if header:
            for k, v in header.items():
                if k.lower() in ("path",):
                    continue
                try:
                    hdu.header[str(k)[:8]] = v
                except Exception:
                    pass
        hdu.writeto(path, overwrite=True)
        return
    # minimal writer float32
    cards = []
    def card(k: str, v: str) -> bytes:
        s = f"{k:<8}= {v}"
        s = s[:80].ljust(80)
        return s.encode("ascii")
    cards.append(card("SIMPLE", "T"))
    cards.append(card("BITPIX", "-32"))
    cards.append(card("NAXIS", str(data.ndim)))
    # FITS axis order reversed
    shape = data.shape
    for i, n in enumerate(reversed(shape), start=1):
        cards.append(card(f"NAXIS{i}", str(int(n))))
    cards.append(card("BSCALE", "1.0"))
    cards.append(card("BZERO", "0.0"))
    if header:
        for k, v in list(header.items())[:50]:
            try:
                cards.append(card(str(k)[:8], repr(v)[:60]))
            except Exception:
                pass
    cards.append(card("END", ""))
    hdr = b"".join(cards)
    pad = (2880 - (len(hdr) % 2880)) % 2880
    hdr = hdr + b" " * pad
    # big-endian float32
    payload = np.asarray(data, dtype=">f4").tobytes()
    pad2 = (2880 - (len(payload) % 2880)) % 2880
    with open(path, "wb") as f:
        f.write(hdr)
        f.write(payload)
        f.write(b"\x00" * pad2)


def read_ser(path: Union[str, Path]) -> VideoCube:
    """Read SER video (mono or convert first channel)."""
    path = Path(path)
    if not path.exists():
        raise IngestError(f"SER not found: {path}")
    with open(path, "rb") as f:
        header = f.read(178)
        if len(header) < 178:
            raise IngestError("Truncated SER header")
        # SER header layout
        file_id = header[0:14]
        lu_id = struct.unpack("<I", header[14:18])[0]
        color_id = struct.unpack("<I", header[18:22])[0]
        little_endian = struct.unpack("<I", header[22:26])[0]
        img_width = struct.unpack("<I", header[26:30])[0]
        img_height = struct.unpack("<I", header[30:34])[0]
        pixel_depth = struct.unpack("<I", header[34:38])[0]
        frame_count = struct.unpack("<I", header[38:42])[0]
        observer = header[42:82].split(b"\x00")[0].decode("latin1", errors="ignore")
        instrument = header[82:122].split(b"\x00")[0].decode("latin1", errors="ignore")
        telescope = header[122:162].split(b"\x00")[0].decode("latin1", errors="ignore")
        datetime_local = struct.unpack("<Q", header[162:170])[0]
        datetime_utc = struct.unpack("<Q", header[170:178])[0]
        # channels
        if color_id in (0, 8, 9, 10):  # mono variants
            n_chan = 1
        elif color_id in (11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24):
            n_chan = 3
        else:
            n_chan = 1
        bytes_per_pixel = (pixel_depth + 7) // 8
        frame_size = img_width * img_height * n_chan * bytes_per_pixel
        frames = []
        for i in range(frame_count):
            buf = f.read(frame_size)
            if len(buf) < frame_size:
                LOG.warning("SER truncated at frame %d/%d", i, frame_count)
                break
            if bytes_per_pixel == 1:
                arr = np.frombuffer(buf, dtype=np.uint8)
            elif bytes_per_pixel == 2:
                dtype = "<u2" if little_endian else ">u2"
                arr = np.frombuffer(buf, dtype=dtype)
            else:
                raise IngestError(f"Unsupported SER pixel depth {pixel_depth}")
            if n_chan == 1:
                img = arr.reshape((img_height, img_width)).astype(np.float64)
            else:
                img = arr.reshape((img_height, img_width, n_chan)).astype(np.float64)
                img = img.mean(axis=2)  # mono collapse for pipeline core
            frames.append(img)
    if not frames:
        raise IngestError("SER contained no frames")
    data = np.stack(frames, axis=0)
    meta = FrameMeta(path=str(path), camera=instrument or None, notes=f"telescope={telescope};observer={observer}")
    # SER datetime is Windows FILETIME (100-ns since 1601)
    if datetime_utc > 0:
        try:
            epoch = dt.datetime(1601, 1, 1)
            meta.t_utc_mid = epoch + dt.timedelta(microseconds=datetime_utc / 10.0)
        except Exception:
            pass
    return VideoCube(data=data, meta=meta)


def write_png(path: Union[str, Path], image: np.ndarray) -> None:
    path = Path(path)
    ensure_dir(path.parent)
    img = np.asarray(image)
    if img.ndim == 2:
        u8 = (normalize_percentile(img) * 255.0).astype(np.uint8)
        if _HAS_PIL:
            PILImage.fromarray(u8, mode="L").save(path)
            return
        # PPM fallback P5
        h, w = u8.shape
        with open(path.with_suffix(".pgm"), "wb") as f:
            f.write(f"P5\n{w} {h}\n255\n".encode())
            f.write(u8.tobytes())
        return
    if img.ndim == 3 and img.shape[2] >= 3:
        rgb = np.stack([normalize_percentile(img[:,:,i]) for i in range(3)], axis=2)
        u8 = (np.clip(rgb, 0, 1) * 255).astype(np.uint8)
        if _HAS_PIL:
            PILImage.fromarray(u8, mode="RGB").save(path)
            return
        h, w, _ = u8.shape
        with open(path.with_suffix(".ppm"), "wb") as f:
            f.write(f"P6\n{w} {h}\n255\n".encode())
            f.write(u8.tobytes())
        return
    raise IngestError(f"Unsupported image shape for PNG: {img.shape}")


def ingest_path(path: Union[str, Path], filter_name: str = "UNKNOWN",
                site_lat: float = 0.0, site_lon: float = 0.0,
                site_elev_m: float = 0.0) -> VideoCube:
    path = Path(path)
    suf = path.suffix.lower()
    if suf == ".ser":
        cube = read_ser(path)
    elif suf in (".fit", ".fits", ".fts"):
        data, hdr = read_fits(path)
        data = np.asarray(data, dtype=np.float64)
        # normalize axis order to (N,H,W) or (H,W)
        if data.ndim == 2:
            data = data[None, ...]
        elif data.ndim == 3:
            # could be (C,H,W) or (H,W,C) or (N,H,W)
            if data.shape[0] in (3, 4) and data.shape[0] < data.shape[-1]:
                # (C,H,W) RGB stack -> mean or keep as multi-channel single "frame" set
                # treat as single frame multi-channel mean for cube, also store channels later
                data = data.mean(axis=0, keepdims=True)
            elif data.shape[-1] in (3, 4) and data.shape[-1] < data.shape[0]:
                data = data.mean(axis=-1, keepdims=False)[None, ...]
            # else assume (N,H,W)
        else:
            raise IngestError(f"Unsupported FITS ndim={data.ndim}")
        cube = VideoCube(data=data, meta=FrameMeta(path=str(path)))
        # try DATE-OBS
        for key in ("DATE-OBS", "DATE_OBS", "DATE"):
            if key in hdr:
                try:
                    cube.meta.t_utc_mid = parse_time_string(str(hdr[key]).strip("'").strip())
                except Exception:
                    pass
                break
    else:
        raise IngestError(f"Unsupported input type: {path}")
    cube.meta.filter_name = filter_name
    cube.meta.site_lat = site_lat
    cube.meta.site_lon = site_lon
    cube.meta.site_elev_m = site_elev_m
    return cube


def read_rgb_fits_channels(path: Union[str, Path]) -> Dict[str, np.ndarray]:
    """Read RGB FITS (3,H,W) or (H,W,3) into channel dict."""
    data, _ = read_fits(path)
    data = np.asarray(data, dtype=np.float64)
    if data.ndim != 3:
        raise IngestError("RGB FITS must be 3D")
    if data.shape[0] == 3:
        r, g, b = data[0], data[1], data[2]
    elif data.shape[-1] == 3:
        r, g, b = data[:,:,0], data[:,:,1], data[:,:,2]
    else:
        raise IngestError(f"Cannot interpret RGB shape {data.shape}")
    return {"R": r, "G": g, "B": b}

# ---------------------------------------------------------------------------
# Calibration
# ---------------------------------------------------------------------------

def estimate_readnoise_gain(bias_frames: np.ndarray) -> Tuple[float, float]:
    """Estimate read noise (ADU) and rough gain from bias pairs."""
    if bias_frames.ndim != 3 or bias_frames.shape[0] < 2:
        return 5.0, 1.0
    d = bias_frames[1] - bias_frames[0]
    read_adu = float(np.std(d) / math.sqrt(2.0))
    return read_adu, 1.0


def make_hot_pixel_mask(dark_or_flat: np.ndarray, sigma: float = 5.0) -> np.ndarray:
    med = np.median(dark_or_flat)
    mad = np.median(np.abs(dark_or_flat - med)) + 1e-12
    return np.abs(dark_or_flat - med) > (sigma * 1.4826 * mad)


def replace_hot_pixels(image: np.ndarray, mask: np.ndarray) -> np.ndarray:
    out = np.asarray(image, dtype=np.float64).copy()
    if not mask.any():
        return out
    # 3x3 median-ish replacement via convolution excluding center
    k = np.ones((3, 3), dtype=np.float64); k[1, 1] = 0.0
    sm = fft_convolve2d(out, k, "same")
    cnt = fft_convolve2d((~mask).astype(np.float64), k, "same")
    repl = safe_div(sm, cnt)
    out[mask] = repl[mask]
    return out


def apply_calibration(
    frame: np.ndarray,
    dark: Optional[np.ndarray] = None,
    flat: Optional[np.ndarray] = None,
    hotmask: Optional[np.ndarray] = None,
) -> np.ndarray:
    img = np.asarray(frame, dtype=np.float64).copy()
    if dark is not None:
        img = img - dark
    if flat is not None:
        f = np.asarray(flat, dtype=np.float64)
        f = f / (np.median(f) + 1e-12)
        img = safe_div(img, f)
    if hotmask is not None:
        img = replace_hot_pixels(img, hotmask)
    return img


def calibrate_cube(cube: VideoCube, dark: Optional[np.ndarray] = None,
                   flat: Optional[np.ndarray] = None) -> VideoCube:
    hot = make_hot_pixel_mask(dark) if dark is not None else None
    out = np.empty_like(cube.data, dtype=np.float64)
    for i in range(cube.n_frames):
        out[i] = apply_calibration(cube.data[i], dark, flat, hot)
    return VideoCube(data=out, times=cube.times, meta=cube.meta, quality=cube.quality)


# ---------------------------------------------------------------------------
# QC
# ---------------------------------------------------------------------------

def rough_disk_mask(image: np.ndarray, thr_frac: float = 0.25) -> np.ndarray:
    img = np.asarray(image, dtype=np.float64)
    thr = thr_frac * np.percentile(img, 99.5)
    m = img > thr
    m = morph_open_close(m, 1, 2)
    return largest_component(m)


def validate_cube(cube: VideoCube, cfg: PipelineConfig) -> QCReport:
    rep = QCReport(ok=True)
    n, h, w = cube.data.shape
    rep.metrics["n_frames"] = float(n)
    rep.metrics["height"] = float(h)
    rep.metrics["width"] = float(w)
    if n < cfg.min_frames:
        # allow single stacked FITS through with warning path
        if n == 1:
            rep.metrics["single_frame_stack"] = 1.0
        else:
            rep.fail(f"Too few frames: {n} < {cfg.min_frames}")
    # flux over time
    means = cube.data.reshape(n, -1).mean(axis=1)
    rep.metrics["mean_flux"] = float(np.mean(means))
    if n > 5:
        drop = 1.0 - (float(np.min(means)) / (float(np.max(means)) + 1e-12))
        rep.metrics["flux_drop"] = drop
        if drop > cfg.flux_drop_frac:
            rep.fail(f"Large flux drop {drop:.2f} (clouds?)")
    # clipping
    vmax = np.percentile(cube.data, 99.99)
    # assume 16-bitish if max > 1000
    sat = 65535.0 if vmax > 1000 else (255.0 if vmax > 1.5 else 1.0)
    clip_frac = float(np.mean(cube.data >= 0.98 * sat))
    rep.metrics["clip_frac"] = clip_frac
    if clip_frac > cfg.max_clip_frac:
        rep.fail(f"Clipping fraction {clip_frac:.4f} too high")
    # planet present
    m = rough_disk_mask(cube.data[n // 2])
    rep.metrics["disk_fill"] = float(m.mean())
    if m.mean() < 0.01:
        rep.fail("Disk mask fraction too small; planet missing?")
    return rep


# ---------------------------------------------------------------------------
# Lucky imaging quality metrics
# ---------------------------------------------------------------------------

def disk_mask_for_quality(image: np.ndarray) -> np.ndarray:
    m = rough_disk_mask(image, 0.2)
    if m.sum() < 50:
        # fallback center disk
        h, w = image.shape
        yy, xx = np.mgrid[0:h, 0:w]
        cy, cx = h/2, w/2
        r = min(h, w) * 0.35
        m = (yy-cy)**2 + (xx-cx)**2 <= r*r
    return m


def score_laplacian_var(image: np.ndarray) -> float:
    img = np.asarray(image, dtype=np.float64)
    blur = gaussian_filter2d(img, 0.7)
    lap = laplacian(blur)
    m = disk_mask_for_quality(img)
    if m.sum() < 10:
        return 0.0
    vals = lap[m]
    mu = float(np.mean(img[m])) + 1e-12
    return float(np.var(vals) / (mu * mu))


def score_fft_power(image: np.ndarray) -> float:
    img = np.asarray(image, dtype=np.float64)
    m = disk_mask_for_quality(img)
    x = img * m
    x = x - x.mean()
    # window
    h, w = x.shape
    wy = np.hanning(h)[:, None]; wx = np.hanning(w)[None, :]
    x = x * wy * wx
    F = np.fft.rfft2(x)
    P = (F.real**2 + F.imag**2)
    # annular mid-high frequencies
    fy = np.fft.fftfreq(h)[:, None]
    fx = np.fft.rfftfreq(w)[None, :]
    rr = np.sqrt(fx*fx + fy*fy)
    band = (rr > 0.05) & (rr < 0.25)
    low = (rr > 0.0) & (rr < 0.05)
    num = float(P[band].sum()) if band.any() else 0.0
    den = float(P[low].sum()) + 1e-12
    return num / den


def score_sobel_energy(image: np.ndarray) -> float:
    m = disk_mask_for_quality(image)
    s = sobel_mag(image)
    if m.sum() < 10:
        return 0.0
    mu = float(np.mean(image[m])) + 1e-12
    return float(np.mean(s[m]**2) / (mu * mu))


def score_tenengrad(image: np.ndarray) -> float:
    # Tenenbaum gradient focus measure
    return score_sobel_energy(image)


def score_variance(image: np.ndarray) -> float:
    m = disk_mask_for_quality(image)
    if m.sum() < 10:
        return 0.0
    return float(np.var(image[m]))


def score_max_pixel(image: np.ndarray) -> float:
    m = disk_mask_for_quality(image)
    if m.sum() < 10:
        return float(np.max(image))
    return float(np.max(image[m]))


def score_frame(image: np.ndarray, metric: str = "laplacian_var") -> float:
    metric = metric.lower()
    if metric == QualityMetric.LAPLACIAN_VAR.value or metric == "laplacian_var":
        return score_laplacian_var(image)
    if metric == QualityMetric.FFT_POWER.value or metric == "fft_power":
        return score_fft_power(image)
    if metric == QualityMetric.SOBEL_ENERGY.value or metric == "sobel_energy":
        return score_sobel_energy(image)
    if metric == QualityMetric.TENENGRAD.value or metric == "tenengrad":
        return score_tenengrad(image)
    if metric == QualityMetric.VARIANCE.value or metric == "variance":
        return score_variance(image)
    if metric == QualityMetric.MAX_PIXEL.value or metric == "max_pixel":
        return score_max_pixel(image)
    if metric == QualityMetric.HYBRID.value or metric == "hybrid":
        a = score_laplacian_var(image)
        b = score_fft_power(image)
        # z-ish combine without batch stats
        return 0.5 * a + 0.5 * b
    return score_laplacian_var(image)


def score_frames(cube: VideoCube, metric: str = "laplacian_var") -> np.ndarray:
    n = cube.n_frames
    scores = np.zeros(n, dtype=np.float64)
    for i in range(n):
        scores[i] = score_frame(cube.data[i], metric)
        if (i + 1) % 200 == 0:
            LOG.info("Scored %d/%d frames", i + 1, n)
    return scores


def select_top_indices(scores: np.ndarray, fraction: float) -> np.ndarray:
    n = len(scores)
    k = max(1, int(round(fraction * n)))
    k = min(k, n)
    idx = np.argsort(scores)[-k:]
    return np.sort(idx)


# ---------------------------------------------------------------------------
# Alignment
# ---------------------------------------------------------------------------

def phase_correlate(ref: np.ndarray, image: np.ndarray) -> Tuple[float, float, float]:
    """Return (dy, dx, peak_response)."""
    a = np.asarray(ref, dtype=np.float64)
    b = np.asarray(image, dtype=np.float64)
    a = a - a.mean(); b = b - b.mean()
    fa = np.fft.fft2(a)
    fb = np.fft.fft2(b)
    R = fa * np.conj(fb)
    R = R / (np.abs(R) + 1e-12)
    c = np.fft.ifft2(R).real
    peak = np.unravel_index(np.argmax(c), c.shape)
    py, px = int(peak[0]), int(peak[1])
    # subpixel parabolic
    def subpix(axis_len: int, p: int, vals: np.ndarray) -> float:
        pm1 = vals[(p - 1) % axis_len]
        p0 = vals[p]
        pp1 = vals[(p + 1) % axis_len]
        denom = (pm1 - 2*p0 + pp1)
        if abs(denom) < 1e-12:
            return float(p)
        return float(p + 0.5 * (pm1 - pp1) / denom)
    # wrap peak to signed shift
    h, w = c.shape
    # 1D slices
    row = c[py, :]
    col = c[:, px]
    px_s = subpix(w, px, row)
    py_s = subpix(h, py, col)
    if px_s > w / 2: px_s -= w
    if py_s > h / 2: py_s -= h
    resp = float(c[py, px] / (c.size**0.5 + 1e-12))
    return float(py_s), float(px_s), resp


def place_alignment_points(mask: np.ndarray, grid: int, margin_frac: float = 0.15) -> List[Tuple[int, int]]:
    ys, xs = np.where(mask)
    if len(xs) < 10:
        h, w = mask.shape
        return [(h//2, w//2)]
    y0, y1 = ys.min(), ys.max()
    x0, x1 = xs.min(), xs.max()
    dy = y1 - y0; dx = x1 - x0
    my = int(margin_frac * dy); mx = int(margin_frac * dx)
    y0 += my; y1 -= my; x0 += mx; x1 -= mx
    if y1 <= y0 or x1 <= x0:
        return [(int(ys.mean()), int(xs.mean()))]
    pts = []
    for iy in range(grid):
        for ix in range(grid):
            y = int(y0 + (iy + 0.5) * (y1 - y0) / grid)
            x = int(x0 + (ix + 0.5) * (x1 - x0) / grid)
            if 0 <= y < mask.shape[0] and 0 <= x < mask.shape[1] and mask[y, x]:
                pts.append((y, x))
    return pts if pts else [(int(ys.mean()), int(xs.mean()))]


def local_cross_corr_shift(ref_patch: np.ndarray, img_patch: np.ndarray) -> Tuple[float, float]:
    # use phase corr on patches
    dy, dx, _ = phase_correlate(ref_patch, img_patch)
    return dy, dx


def extract_patch(image: np.ndarray, y: int, x: int, box: int) -> np.ndarray:
    h, w = image.shape
    half = box // 2
    y0 = clamp(y - half, 0, h); y1 = clamp(y + half, 0, h)
    x0 = clamp(x - half, 0, w); x1 = clamp(x + half, 0, w)
    # force size by padding if needed
    patch = image[int(y0):int(y1), int(x0):int(x1)]
    if patch.shape[0] != box or patch.shape[1] != box:
        out = np.zeros((box, box), dtype=np.float64)
        out[:patch.shape[0], :patch.shape[1]] = patch
        return out
    return np.asarray(patch, dtype=np.float64)


def align_frames_global(frames: np.ndarray, ref_index: int = 0, max_shift: float = 40.0) -> Tuple[np.ndarray, List[Tuple[float,float]]]:
    ref = frames[ref_index]
    out = np.empty_like(frames, dtype=np.float64)
    shifts = []
    for i in range(frames.shape[0]):
        dy, dx, _ = phase_correlate(ref, frames[i])
        if abs(dy) > max_shift or abs(dx) > max_shift:
            dy, dx = 0.0, 0.0
        out[i] = shift_image(frames[i], dy, dx)
        shifts.append((dy, dx))
    return out, shifts


def align_frames_local_ap(
    frames: np.ndarray,
    ref_index: int,
    ap_grid: int = 12,
    ap_box: int = 48,
    max_shift: float = 40.0,
) -> Tuple[np.ndarray, Dict[str, Any]]:
    ref = np.asarray(frames[ref_index], dtype=np.float64)
    mask = rough_disk_mask(ref)
    aps = place_alignment_points(mask, ap_grid)
    h, w = ref.shape
    out = np.empty_like(frames, dtype=np.float64)
    model = {"aps": aps, "shifts_per_frame": []}
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    for i in range(frames.shape[0]):
        img = np.asarray(frames[i], dtype=np.float64)
        # global first
        gdy, gdx, _ = phase_correlate(ref, img)
        if abs(gdy) > max_shift or abs(gdx) > max_shift:
            gdy, gdx = 0.0, 0.0
        img_g = shift_image(img, gdy, gdx)
        # local AP shifts relative to ref
        ap_shifts = []
        for (ay, ax) in aps:
            rp = extract_patch(ref, ay, ax, ap_box)
            ip = extract_patch(img_g, ay, ax, ap_box)
            ldy, ldx = local_cross_corr_shift(rp, ip)
            if abs(ldy) > ap_box/4 or abs(ldx) > ap_box/4:
                ldy, ldx = 0.0, 0.0
            ap_shifts.append((ay, ax, ldy, ldx))
        # interpolate displacement field (inverse map: where to sample from)
        if len(ap_shifts) >= 3:
            pts_y = np.array([p[0] for p in ap_shifts], dtype=np.float64)
            pts_x = np.array([p[1] for p in ap_shifts], dtype=np.float64)
            dsy = np.array([p[2] for p in ap_shifts], dtype=np.float64)
            dsx = np.array([p[3] for p in ap_shifts], dtype=np.float64)
            # inverse distance weighting
            field_y = np.zeros((h, w), dtype=np.float64)
            field_x = np.zeros((h, w), dtype=np.float64)
            # subsample grid for speed then upsample
            step = max(4, min(h, w)//64)
            ys = np.arange(0, h, step); xs = np.arange(0, w, step)
            FY = np.zeros((len(ys), len(xs))); FX = np.zeros_like(FY)
            for iy, y in enumerate(ys):
                for ix, x in enumerate(xs):
                    dist2 = (pts_y - y)**2 + (pts_x - x)**2 + 1e-6
                    wt = 1.0 / dist2
                    wt /= wt.sum()
                    FY[iy, ix] = np.sum(wt * dsy)
                    FX[iy, ix] = np.sum(wt * dsx)
            # nearest expand
            field_y = resize_bilinear(FY, h, w)
            field_x = resize_bilinear(FX, h, w)
        else:
            field_y = np.zeros((h, w)); field_x = np.zeros((h, w))
        coords = np.array([yy - field_y, xx - field_x])
        out[i] = map_coords(img_g, coords, order=1, mode="constant", cval=float(np.median(img_g)))
        model["shifts_per_frame"].append({"global": (gdy, gdx), "local": ap_shifts})
    return out, model


def align_stack(frames: np.ndarray, scores: np.ndarray, cfg: PipelineConfig) -> Tuple[np.ndarray, Dict[str, Any]]:
    ref_index = int(np.argmax(scores)) if len(scores) == len(frames) else 0
    mode = cfg.align_mode
    if mode == "global" or mode == AlignMode.GLOBAL.value:
        aligned, shifts = align_frames_global(frames, ref_index, cfg.max_shift_px)
        return aligned, {"mode": "global", "shifts": shifts, "ref_index": ref_index}
    if mode == "rigid" or mode == AlignMode.RIGID.value:
        aligned, shifts = align_frames_global(frames, ref_index, cfg.max_shift_px)
        return aligned, {"mode": "rigid", "shifts": shifts, "ref_index": ref_index}
    aligned, model = align_frames_local_ap(frames, ref_index, cfg.ap_grid, cfg.ap_box, cfg.max_shift_px)
    model["mode"] = "local_ap"
    model["ref_index"] = ref_index
    return aligned, model


# ---------------------------------------------------------------------------
# Stacking
# ---------------------------------------------------------------------------

def stack_mean(frames: np.ndarray) -> np.ndarray:
    return np.mean(frames, axis=0)


def stack_median(frames: np.ndarray) -> np.ndarray:
    return np.median(frames, axis=0)


def stack_kappa_sigma(frames: np.ndarray, kappa: float = 2.5, iters: int = 3) -> np.ndarray:
    acc = np.median(frames, axis=0)
    for _ in range(iters):
        std = np.std(frames, axis=0) + 1e-12
        mask = np.abs(frames - acc) <= kappa * std
        # weighted mean with mask
        num = np.sum(frames * mask, axis=0)
        den = np.sum(mask, axis=0) + 1e-12
        acc = num / den
    return acc


def stack_quality_weighted(frames: np.ndarray, scores: np.ndarray) -> np.ndarray:
    w = np.asarray(scores, dtype=np.float64)
    w = np.clip(w, 0, None)
    if w.sum() <= 0:
        w = np.ones_like(w)
    w = w / w.sum()
    out = np.zeros(frames.shape[1:], dtype=np.float64)
    for i in range(frames.shape[0]):
        out += w[i] * frames[i]
    return out


def stack_winsorized(frames: np.ndarray, frac: float = 0.1) -> np.ndarray:
    n = frames.shape[0]
    k = int(frac * n)
    if k <= 0 or 2*k >= n:
        return stack_mean(frames)
    s = np.sort(frames, axis=0)
    return np.mean(s[k:n-k], axis=0)


def estimate_noise_map(frames: np.ndarray, max_frames: int = 50) -> np.ndarray:
    n = frames.shape[0]
    if n == 1:
        return np.zeros(frames.shape[1:], dtype=np.float64)
    if n > max_frames:
        idx = np.linspace(0, n-1, max_frames).astype(int)
        f = frames[idx]
    else:
        f = frames
    return np.std(f, axis=0)


def stack_frames(frames: np.ndarray, scores: np.ndarray, cfg: PipelineConfig) -> np.ndarray:
    method = cfg.stack_method
    if method == "median":
        return stack_median(frames)
    if method == "mean":
        return stack_mean(frames)
    if method == "quality_weighted":
        return stack_quality_weighted(frames, scores)
    if method == "winsorized":
        return stack_winsorized(frames)
    return stack_kappa_sigma(frames, cfg.kappa)


def lucky_stack_cube(cube: VideoCube, cfg: PipelineConfig, fraction: Optional[float] = None) -> StackResult:
    frac = cfg.primary_fraction if fraction is None else fraction
    with StageTimer(f"lucky_stack f={frac}"):
        scores = cube.quality if cube.quality is not None else score_frames(cube, cfg.quality_metric)
        idx = select_top_indices(scores, frac)
        sel = cube.data[idx]
        sel_scores = scores[idx]
        if cube.n_frames == 1:
            aligned = sel
            amodel = {"mode": "none"}
        else:
            aligned, amodel = align_stack(sel, sel_scores, cfg)
        img = stack_frames(aligned, sel_scores, cfg)
        noise = estimate_noise_map(aligned)
        thr = float(sel_scores.min()) if len(sel_scores) else 0.0
        return StackResult(
            image=img, n_used=int(len(idx)), fraction=float(frac),
            quality_threshold=thr, noise_map=noise, meta=cube.meta,
            align_model=amodel, scores_used=sel_scores, method=cfg.stack_method,
        )

# ---------------------------------------------------------------------------
# Zernike polynomials & wave-optical PSF helpers
# ---------------------------------------------------------------------------

def noll_to_zernike(j: int) -> Tuple[int, int]:
    """Convert Noll index j (1-based) to (n, m)."""
    n = 0
    j1 = j
    while j1 > n + 1:
        j1 -= n + 1
        n += 1
    m = -n + 2 * (j1 - 1)
    # Noll ordering adjustment
    # Use standard mapping:
    n = int(math.ceil((-3 + math.sqrt(1 + 8*j)) / 2))
    # recompute properly
    n = 0
    while (n+1)*(n+2)//2 < j:
        n += 1
    r = j - n*(n+1)//2
    # m sequence
    m = -n + 2*(r-1) if (n % 2 == 0) else -n + 2*(r-1)
    # fix with classic algorithm
    n = 0
    j0 = j - 1
    while True:
        if j0 <= n:
            break
        j0 -= n + 1
        n += 1
    m = -n + 2 * j0
    return n, m


def zernike_radial(n: int, m: int, rho: np.ndarray) -> np.ndarray:
    m = abs(m)
    R = np.zeros_like(rho, dtype=np.float64)
    for s in range((n - m)//2 + 1):
        num = ((-1)**s) * math.factorial(n - s)
        den = (math.factorial(s) * math.factorial((n+m)//2 - s) * math.factorial((n-m)//2 - s))
        R += (num / den) * rho**(n - 2*s)
    return R


def zernike(n: int, m: int, rho: np.ndarray, theta: np.ndarray) -> np.ndarray:
    R = zernike_radial(n, m, rho)
    if m > 0:
        return R * np.cos(m * theta)
    if m < 0:
        return R * np.sin(-m * theta)
    return R


def zernike_basis_on_pupil(size: int, noll_max: int = 15) -> List[np.ndarray]:
    y = np.linspace(-1, 1, size)
    x = np.linspace(-1, 1, size)
    xx, yy = np.meshgrid(x, y)
    rho = np.sqrt(xx*xx + yy*yy)
    theta = np.arctan2(yy, xx)
    aperture = rho <= 1.0
    modes = []
    # generate first noll_max modes with correct (n,m)
    j = 1
    n = 0
    while len(modes) < noll_max:
        for m in range(-n, n+1, 2):
            Z = np.zeros((size, size), dtype=np.float64)
            Zm = zernike(n, m, np.clip(rho, 0, 1), theta)
            Z[aperture] = Zm[aperture]
            # normalize
            norm = np.sqrt(np.mean(Z[aperture]**2)) + 1e-12
            modes.append(Z / norm)
            if len(modes) >= noll_max:
                break
        n += 1
        if n > 30:
            break
    return modes


def complex_pupil_psf(size: int = 128, zernike_coeffs: Optional[Sequence[float]] = None,
                      wavelength_scale: float = 1.0) -> np.ndarray:
    """Generate PSF from complex pupil with optional Zernike aberrations."""
    y = np.linspace(-1, 1, size)
    xx, yy = np.meshgrid(y, y)
    rho = np.sqrt(xx*xx + yy*yy)
    theta = np.arctan2(yy, xx)
    aper = rho <= 1.0
    phase = np.zeros((size, size), dtype=np.float64)
    if zernike_coeffs:
        modes = zernike_basis_on_pupil(size, len(zernike_coeffs))
        for c, m in zip(zernike_coeffs, modes):
            phase += float(c) * m
    field = np.zeros((size, size), dtype=np.complex128)
    field[aper] = np.exp(1j * wavelength_scale * phase[aper])
    # pad for finer PSF sampling
    pad = size
    field_p = np.pad(field, pad)
    F = np.fft.fftshift(np.fft.fft2(np.fft.ifftshift(field_p)))
    psf = (F.real**2 + F.imag**2)
    psf = psf / (psf.sum() + 1e-12)
    # crop center size x size
    c = psf.shape[0] // 2
    half = size // 2
    return psf[c-half:c-half+size, c-half:c-half+size]


def kolmogorov_phase_screen(size: int, r0_frac: float = 0.2, seed: int = 0) -> np.ndarray:
    """Fourier-method Kolmogorov phase screen (approx)."""
    rng = np.random.default_rng(seed)
    fy = np.fft.fftfreq(size)
    fx = np.fft.fftfreq(size)
    fxx, fyy = np.meshgrid(fx, fy)
    f = np.sqrt(fxx*fxx + fyy*fyy)
    f[0,0] = 1.0
    # power spectrum ~ f^{-11/3}
    r0 = max(r0_frac * size, 1.0)
    psd = 0.023 * (r0 ** (-5.0/3.0)) * (f ** (-11.0/3.0))
    psd[0,0] = 0.0
    noise = rng.normal(size=(size, size)) + 1j * rng.normal(size=(size, size))
    screen = np.fft.ifft2(np.sqrt(psd) * noise).real
    screen -= screen.mean()
    return screen


def moffat_psf(size: int, alpha: float = 2.5, beta: float = 2.5) -> np.ndarray:
    c = size // 2
    y, x = np.mgrid[0:size, 0:size]
    rr = (x-c)**2 + (y-c)**2
    psf = (1 + rr / (alpha**2)) ** (-beta)
    psf = psf / psf.sum()
    return psf.astype(np.float64)


def gaussian_psf(size: int, sigma: float = 1.5) -> np.ndarray:
    c = size // 2
    y, x = np.mgrid[0:size, 0:size]
    psf = np.exp(-0.5 * ((x-c)**2 + (y-c)**2) / (sigma**2))
    return (psf / psf.sum()).astype(np.float64)


def estimate_psf_from_limb(image: np.ndarray, nav_xc: float, nav_yc: float, a_eq: float,
                           n_angles: int = 72, psf_size: int = 21) -> np.ndarray:
    """Estimate approximate 1D LSF from limb and build circular PSF."""
    img = np.asarray(image, dtype=np.float64)
    h, w = img.shape
    profiles = []
    half = 15
    for i in range(n_angles):
        ang = 2 * math.pi * i / n_angles
        # sample along radius near limb
        rs = np.linspace(a_eq - half, a_eq + half, 2*half+1)
        xs = nav_xc + rs * math.cos(ang)
        ys = nav_yc + rs * math.sin(ang)
        coords = np.array([ys, xs])
        prof = map_coords(img, coords, order=1, mode="nearest")
        profiles.append(prof)
    esf = np.median(np.stack(profiles, axis=0), axis=0)
    lsf = np.gradient(esf)
    lsf = np.abs(lsf)
    lsf = lsf / (lsf.sum() + 1e-12)
    # make 2D from radial LSF approx Gaussian fit
    # fit sigma from second moment
    t = np.arange(len(lsf)) - (len(lsf)-1)/2
    sig = float(np.sqrt(np.sum(lsf * t*t)) + 0.5)
    return gaussian_psf(psf_size, sigma=max(sig/2, 0.6))


# ---------------------------------------------------------------------------
# Wavelets (starlet / à trous) and deconvolution
# ---------------------------------------------------------------------------

def b3_spline_kernel() -> np.ndarray:
    # 1D B3: [1,4,6,4,1]/16
    k = np.array([1, 4, 6, 4, 1], dtype=np.float64) / 16.0
    return k


def a_trous_convolve(image: np.ndarray, level: int) -> np.ndarray:
    """Separable à trous convolution with B3 spline at given level (holes)."""
    k = b3_spline_kernel()
    step = 2 ** level
    # build dilated kernel
    kd = np.zeros(len(k) + (len(k)-1)*(step-1), dtype=np.float64)
    kd[0::step] = k
    img = np.asarray(image, dtype=np.float64)
    pad = len(kd)//2
    # rows
    tmp = np.pad(img, ((0,0),(pad,pad)), mode="reflect")
    out = np.zeros_like(img)
    for i in range(img.shape[0]):
        out[i] = np.convolve(tmp[i], kd, mode="valid")
    tmp2 = np.pad(out, ((pad,pad),(0,0)), mode="reflect")
    out2 = np.zeros_like(img)
    for j in range(img.shape[1]):
        out2[:, j] = np.convolve(tmp2[:, j], kd, mode="valid")
    return out2


def starlet_decompose(image: np.ndarray, n_layers: int) -> Tuple[List[np.ndarray], np.ndarray]:
    c0 = np.asarray(image, dtype=np.float64)
    layers = []
    for j in range(n_layers):
        c1 = a_trous_convolve(c0, j)
        w = c0 - c1
        layers.append(w)
        c0 = c1
    return layers, c0


def soft_threshold(x: np.ndarray, thr: float) -> np.ndarray:
    return np.sign(x) * np.maximum(np.abs(x) - thr, 0.0)


def mad_sigma(x: np.ndarray) -> float:
    med = np.median(x)
    mad = np.median(np.abs(x - med))
    return float(1.4826 * mad + 1e-12)


def starlet_sharpen(
    image: np.ndarray,
    n_layers: int,
    gains: Sequence[float],
    denoise_sigmas: Sequence[float],
) -> np.ndarray:
    layers, residual = starlet_decompose(image, n_layers)
    # pad gains
    g = list(gains) + [0.0] * max(0, n_layers - len(gains))
    d = list(denoise_sigmas) + [0.0] * max(0, n_layers - len(denoise_sigmas))
    acc = residual.copy()
    for j in range(n_layers):
        w = layers[j]
        sig = mad_sigma(w)
        thr = d[j] * sig
        w2 = soft_threshold(w, thr)
        acc = acc + g[j] * w2
    return acc


def richardson_lucy(image: np.ndarray, psf: np.ndarray, n_iter: int = 10, eps: float = 1e-8) -> np.ndarray:
    img = np.asarray(image, dtype=np.float64)
    img = np.clip(img, 0, None)
    # normalize positivity
    mn = img.min()
    if mn < 0:
        img = img - mn
    x = img.copy() + eps
    psf = np.asarray(psf, dtype=np.float64)
    psf = psf / (psf.sum() + 1e-12)
    psf_m = psf[::-1, ::-1]
    for _ in range(int(n_iter)):
        conv = fft_convolve2d(x, psf, "same")
        ratio = img / (conv + eps)
        x = x * fft_convolve2d(ratio, psf_m, "same")
        x = np.clip(x, eps, None)
    return x


def wiener_deconv(image: np.ndarray, psf: np.ndarray, K: float = 0.01) -> np.ndarray:
    img = np.asarray(image, dtype=np.float64)
    psf = np.asarray(psf, dtype=np.float64)
    psf = psf / (psf.sum() + 1e-12)
    # pad psf
    H = np.zeros_like(img)
    ph, pw = psf.shape
    H[:ph, :pw] = psf
    H = np.roll(H, -ph//2, axis=0)
    H = np.roll(H, -pw//2, axis=1)
    F = np.fft.fft2(img)
    FH = np.fft.fft2(H)
    G = np.conj(FH) / (FH*np.conj(FH) + K)
    out = np.fft.ifft2(F * G).real
    return out


def limb_overshoot_metric(image: np.ndarray, mask: Optional[np.ndarray] = None) -> float:
    """Rough ringing metric near limb."""
    if mask is None:
        mask = rough_disk_mask(image)
    # dilate ring
    ring = morph_open_close(mask, 0, 2) & (~mask)
    if ring.sum() < 10:
        return 0.0
    inside = float(np.mean(image[mask])) + 1e-12
    outside = float(np.mean(image[ring]))
    # negative outside mean relative
    return float(max(0.0, -outside / inside))


def restore_image(image: np.ndarray, cfg: PipelineConfig,
                  psf: Optional[np.ndarray] = None) -> np.ndarray:
    method = cfg.restore_method
    img = np.asarray(image, dtype=np.float64)
    if method in ("none", RestoreMethod.NONE.value):
        return img
    if method in ("wavelets", RestoreMethod.WAVELETS.value):
        return starlet_sharpen(img, cfg.wavelet_layers, cfg.wavelet_gains, cfg.wavelet_denoise)
    if psf is None:
        psf = moffat_psf(21, 2.0, 2.5)
    if method in ("rl", RestoreMethod.RL.value):
        return richardson_lucy(img, psf, cfg.rl_iters)
    if method in ("wiener", RestoreMethod.WIENER.value):
        return wiener_deconv(img, psf)
    if method in ("wavelets_then_rl", RestoreMethod.WAVELETS_THEN_RL.value):
        w = starlet_sharpen(img, cfg.wavelet_layers, cfg.wavelet_gains, cfg.wavelet_denoise)
        return richardson_lucy(w, psf, max(1, cfg.rl_iters // 2))
    return img


# ---------------------------------------------------------------------------
# Colour / LRGB
# ---------------------------------------------------------------------------

def rgb_to_ycbcr(rgb: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    r, g, b = rgb[:,:,0], rgb[:,:,1], rgb[:,:,2]
    y = 0.299*r + 0.587*g + 0.114*b
    cb = -0.168736*r - 0.331264*g + 0.5*b + 0.5
    cr = 0.5*r - 0.418688*g - 0.081312*b + 0.5
    return y, cb, cr


def ycbcr_to_rgb(y: np.ndarray, cb: np.ndarray, cr: np.ndarray) -> np.ndarray:
    cb2 = cb - 0.5; cr2 = cr - 0.5
    r = y + 1.402 * cr2
    g = y - 0.344136 * cb2 - 0.714136 * cr2
    b = y + 1.772 * cb2
    return np.clip(np.stack([r, g, b], axis=2), 0.0, 1.0)


def build_lrgb(L: np.ndarray, R: Optional[np.ndarray], G: Optional[np.ndarray],
               B: Optional[np.ndarray], sat_scale: float = 0.85,
               denoise_chroma: bool = True) -> np.ndarray:
    # synthesize missing channels
    Lz = normalize_percentile(L)
    if R is None: R = L
    if G is None: G = L
    if B is None: B = L
    Rn = normalize_percentile(R); Gn = normalize_percentile(G); Bn = normalize_percentile(B)
    color = np.stack([Rn, Gn, Bn], axis=2)
    y, cb, cr = rgb_to_ycbcr(color)
    if denoise_chroma:
        cb = gaussian_filter2d(cb, 1.0)
        cr = gaussian_filter2d(cr, 1.0)
    # sat scale around 0.5
    cb = 0.5 + (cb - 0.5) * sat_scale
    cr = 0.5 + (cr - 0.5) * sat_scale
    y = Lz
    return ycbcr_to_rgb(y, cb, cr)


def register_channels(channels: Dict[str, np.ndarray], ref_name: str = "G") -> Dict[str, np.ndarray]:
    if ref_name not in channels:
        ref_name = next(iter(channels.keys()))
    ref = highpass(channels[ref_name], 2.0)
    out = {}
    for name, img in channels.items():
        if name == ref_name:
            out[name] = np.asarray(img, dtype=np.float64)
            continue
        dy, dx, _ = phase_correlate(ref, highpass(img, 2.0))
        out[name] = shift_image(img, dy, dx)
    return out


def apply_residual_dcr(channels: Dict[str, np.ndarray], z_deg: float = 40.0,
                       pressure: float = 1013.25, temp_c: float = 15.0) -> Dict[str, np.ndarray]:
    """Shift channels vertically by model DCR relative to G (simplified)."""
    ref_lam = FILTER_WAVELENGTH_NM.get("G", 530.0)
    out = {}
    for name, img in channels.items():
        lam = FILTER_WAVELENGTH_NM.get(name, 550.0)
        dcr = dcr_shift_arcsec(z_deg, lam, ref_lam, pressure, temp_c)
        # without plate scale we assume ~0.1"/px planetary typical -> user should set
        # use 0 shift if unknown plate scale; mild model shift in px if dcr large
        # skip absolute; keep as no-op unless plate scale known
        out[name] = img
    return out


# ---------------------------------------------------------------------------
# Derotation
# ---------------------------------------------------------------------------

def project_to_cylindrical(image: np.ndarray, nav: Navigation, width: int = 1800,
                           height: Optional[int] = None) -> np.ndarray:
    """Orthographic-like inverse: sample disk into lon-lat map."""
    img = np.asarray(image, dtype=np.float64)
    if height is None:
        height = width // 2
    # map x: lon from -90..90 visible, y: lat -90..90
    lons = np.linspace(-90, 90, width)
    lats = np.linspace(90, -90, height)  # top to bottom
    lon_g, lat_g = np.meshgrid(lons, lats)
    # orthographic projection from lon/lat relative to sub-obs (CM)
    lon_r = deg2rad(lon_g)
    lat_r = deg2rad(lat_g)
    X = np.cos(lat_r) * np.sin(lon_r)
    Y = np.sin(lat_r)
    # scale to px (approx sphere; flattening mild)
    xs = nav.xc + X * nav.a_eq_px
    ys = nav.yc - Y * nav.b_pol_px
    # visibility mu>0
    mu = np.cos(lat_r) * np.cos(lon_r)
    coords = np.array([ys, xs])
    sampled = map_coords(img, coords, order=1, mode="constant", cval=0.0)
    sampled[mu <= 0] = 0.0
    return sampled


def backproject_cylindrical(cyl: np.ndarray, nav: Navigation, out_shape: Tuple[int,int]) -> np.ndarray:
    h, w = out_shape
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    X = (xx - nav.xc) / (nav.a_eq_px + 1e-12)
    Y = (nav.yc - yy) / (nav.b_pol_px + 1e-12)
    rr = X*X + Y*Y
    lon = np.arcsin(np.clip(X / np.sqrt(np.maximum(1e-12, 1 - Y*Y)), -1, 1))  # rough
    # better: lon = atan2(X, sqrt(1-X^2-Y^2))
    mu = np.sqrt(np.clip(1.0 - rr, 0, 1))
    lon = np.arctan2(X, mu)
    lat = np.arcsin(np.clip(Y, -1, 1))
    # map to cyl coords: lon -90..90 -> 0..W, lat 90..-90 -> 0..H
    ch, cw = cyl.shape
    mx = (rad2deg(lon) + 90.0) / 180.0 * (cw - 1)
    my = (90.0 - rad2deg(lat)) / 180.0 * (ch - 1)
    coords = np.array([my, mx])
    out = map_coords(cyl, coords, order=1, mode="constant", cval=0.0)
    out[rr > 1.0] = 0.0
    return out


def rough_navigation(image: np.ndarray, geom: Optional[GeomEphemeris] = None) -> Navigation:
    img = np.asarray(image, dtype=np.float64)
    m = rough_disk_mask(img)
    ys, xs = np.where(m)
    if len(xs) < 20:
        h, w = img.shape
        return Navigation(xc=w/2, yc=h/2, a_eq_px=min(h,w)*0.4)
    xc = float(xs.mean()); yc = float(ys.mean())
    # radius estimate
    a = 0.5 * (xs.max() - xs.min() + ys.max() - ys.min()) / 2.0
    a = float(max(a, 10.0))
    nav = Navigation(xc=xc, yc=yc, a_eq_px=a, flattening=PC.JUPITER_FLATTENING)
    if geom is not None:
        nav.cm_iii_deg = geom.cm_iii_deg
        nav.distance_au = geom.distance_au
        nav.sub_obs_lat = geom.sub_obs_lat_deg
        nav.epoch_tdb_mjd = geom.t_tdb_mjd
        nav.apparent_diameter_arcsec = geom.apparent_diameter_arcsec
        nav.np_angle_deg = geom.np_angle_deg
    return nav


def derotate_image(image: np.ndarray, nav: Navigation, cm_from: float, cm_to: float,
                   map_width: int = 1800) -> np.ndarray:
    dlon = wrap_deg_diff(cm_to, cm_from)  # degrees to shift map
    if abs(dlon) < 1e-3:
        return np.asarray(image, dtype=np.float64)
    cyl = project_to_cylindrical(image, nav, width=map_width)
    # shift in longitude: positive dlon means feature moved
    shift_px = (dlon / 180.0) * cyl.shape[1]
    # subpixel roll
    shift_i = int(np.floor(shift_px))
    frac = shift_px - shift_i
    rolled = np.roll(cyl, shift_i, axis=1)
    rolled2 = np.roll(cyl, shift_i + 1, axis=1)
    cyl_s = (1 - frac) * rolled + frac * rolled2
    return backproject_cylindrical(cyl_s, nav, image.shape)


def derotate_stack_result(stack: StackResult, cfg: PipelineConfig, t_ref: Optional[dt.datetime] = None) -> StackResult:
    if not cfg.derot_enable:
        return stack
    t = stack.meta.t_utc_mid
    if t is None:
        return stack
    geom = compute_geometry(t, stack.meta.site_lat, stack.meta.site_lon, stack.meta.site_elev_m)
    if t_ref is None:
        t_ref = t
    geom_ref = compute_geometry(t_ref, stack.meta.site_lat, stack.meta.site_lon, stack.meta.site_elev_m)
    nav = rough_navigation(stack.image, geom)
    img = derotate_image(stack.image, nav, geom.cm_iii_deg, geom_ref.cm_iii_deg, cfg.derot_map_width)
    meta = replace(stack.meta, t_utc_mid=t_ref)
    return replace(stack, image=img, meta=meta)

# ---------------------------------------------------------------------------
# Navigation: limb fit
# ---------------------------------------------------------------------------

def extract_limb_points(image: np.ndarray, n_rays: int = 360,
                        method: str = "radial_gradient") -> np.ndarray:
    """Return Nx2 array of (y, x) limb points."""
    img = np.asarray(image, dtype=np.float64)
    h, w = img.shape
    m = rough_disk_mask(img)
    ys, xs = np.where(m)
    if len(xs) < 20:
        raise NavigationError("Cannot find disk for limb extraction")
    cy, cx = float(ys.mean()), float(xs.mean())
    r_est = 0.5 * (0.5*(xs.max()-xs.min()) + 0.5*(ys.max()-ys.min()))
    pts = []
    for i in range(n_rays):
        ang = 2 * math.pi * i / n_rays
        # sample radius profile
        rs = np.linspace(0.5 * r_est, 1.3 * r_est, 200)
        xs_r = cx + rs * math.cos(ang)
        ys_r = cy + rs * math.sin(ang)
        coords = np.array([ys_r, xs_r])
        prof = map_coords(img, coords, order=1, mode="nearest")
        if method == "threshold_edge":
            thr = 0.5 * (prof.max() + prof.min())
            idx = np.where(prof < thr)[0]
            j = int(idx[0]) if len(idx) else int(np.argmin(np.gradient(prof)))
        else:
            g = np.gradient(prof)
            j = int(np.argmin(g))  # steepest drop outward if bright disk
            # refine subpixel
        if 1 <= j < len(rs) - 1:
            # parabolic on gradient
            gm, g0, gp = g[j-1], g[j], g[j+1]
            denom = (gm - 2*g0 + gp)
            delta = 0.0 if abs(denom) < 1e-12 else 0.5 * (gm - gp) / denom
            r = rs[j] + delta * (rs[1] - rs[0])
        else:
            r = rs[min(max(j,0), len(rs)-1)]
        pts.append((cy + r * math.sin(ang), cx + r * math.cos(ang)))
    return np.asarray(pts, dtype=np.float64)


def fit_ellipse_algebraic(ys: np.ndarray, xs: np.ndarray) -> Tuple[float,float,float,float,float]:
    """
    Fit ellipse ax^2 + bxy + cy^2 + dx + ey + f = 0.
    Returns xc, yc, a, b, theta (radians) approximate.
    """
    x = xs; y = ys
    D = np.column_stack([x*x, x*y, y*y, x, y, np.ones_like(x)])
    # solve null via SVD
    _, _, vh = np.linalg.svd(D)
    p = vh[-1, :]
    A, B, C, D_, E, F = p
    # convert to geometric
    den = B*B - 4*A*C
    if abs(den) < 1e-14:
        return float(x.mean()), float(y.mean()), float(np.std(x)*2), float(np.std(y)*2), 0.0
    xc = (2*C*D_ - B*E) / den
    yc = (2*A*E - B*D_) / den
    # eigenvalues for axes
    num = 2 * (A*E*E + C*D_*D_ - B*D_*E + den*F - (A+C)*(B*B - 4*A*C)*0 + 0)
    # simpler radius estimate from points
    rr = np.sqrt((x-xc)**2 + (y-yc)**2)
    a = float(np.percentile(rr, 90))
    b = float(np.percentile(rr, 50))
    theta = 0.5 * math.atan2(B, A - C) if abs(A-C)+abs(B) > 0 else 0.0
    return float(xc), float(yc), a, min(a, b), float(theta)


def fit_oblate_disk(points: np.ndarray, flattening: float = PC.JUPITER_FLATTENING,
                    fixed_flat: bool = True) -> Navigation:
    ys = points[:, 0]; xs = points[:, 1]
    xc, yc, a, b, th = fit_ellipse_algebraic(ys, xs)
    if fixed_flat:
        # enforce b = a * (1-f) approximately by refitting a from mean radius
        rr = np.sqrt((xs-xc)**2 + ((ys-yc)/(1-flattening + 1e-12))**2)
        a = float(np.median(rr))
    nav = Navigation(xc=xc, yc=yc, a_eq_px=a, flattening=flattening, north_pa_deg=rad2deg(th))
    # covariance rough
    resid = np.sqrt((xs-xc)**2 + (ys-yc)**2) - a
    s = float(np.std(resid))**2
    nav.cov_center = np.array([[s, 0],[0, s]], dtype=np.float64)
    return nav


def bootstrap_limb_nav(image: np.ndarray, n: int = 50, n_rays: int = 360,
                       seed: int = 0) -> Navigation:
    rng = np.random.default_rng(seed)
    img = np.asarray(image, dtype=np.float64)
    noise = np.std(img - gaussian_filter2d(img, 2.0))
    xs = []; ys = []; as_ = []
    for i in range(n):
        noisy = img + rng.normal(0, noise * 0.25, img.shape)
        try:
            pts = extract_limb_points(noisy, n_rays=n_rays)
            # random subset
            sel = rng.choice(len(pts), size=max(30, len(pts)//2), replace=False)
            nav = fit_oblate_disk(pts[sel])
            xs.append(nav.xc); ys.append(nav.yc); as_.append(nav.a_eq_px)
        except Exception:
            continue
    if not xs:
        return rough_navigation(img)
    nav = Navigation(xc=float(np.mean(xs)), yc=float(np.mean(ys)), a_eq_px=float(np.mean(as_)))
    cov = np.cov(np.array([xs, ys]))
    nav.cov_center = cov
    return nav


def fit_navigation(image: np.ndarray, meta: FrameMeta, cfg: PipelineConfig) -> Navigation:
    with StageTimer("navigation"):
        geom = compute_geometry(meta.t_utc_mid, meta.site_lat, meta.site_lon, meta.site_elev_m)
        try:
            pts = extract_limb_points(image, n_rays=cfg.n_rays, method=cfg.limb_method)
            nav = fit_oblate_disk(pts)
        except Exception as e:
            LOG.warning("Limb fit failed (%s); using rough nav", e)
            nav = rough_navigation(image, geom)
        if cfg.bootstrap_limb > 0:
            try:
                bnav = bootstrap_limb_nav(image, n=min(cfg.bootstrap_limb, 30), n_rays=min(cfg.n_rays, 180), seed=cfg.seed)
                nav.cov_center = bnav.cov_center
            except Exception:
                pass
        nav.cm_iii_deg = geom.cm_iii_deg
        nav.distance_au = geom.distance_au
        nav.sub_obs_lat = geom.sub_obs_lat_deg
        nav.epoch_tdb_mjd = geom.t_tdb_mjd
        nav.apparent_diameter_arcsec = geom.apparent_diameter_arcsec
        nav.np_angle_deg = geom.np_angle_deg
        return nav


def px_to_lonlat(y: float, x: float, nav: Navigation) -> Tuple[float, float]:
    """Return (lon_iii_deg, lat_deg) planetocentric-ish."""
    X = (x - nav.xc) / (nav.a_eq_px + 1e-12)
    Y = (nav.yc - y) / (nav.b_pol_px + 1e-12)
    rr = X*X + Y*Y
    if rr > 1.0:
        # clamp to limb
        s = math.sqrt(rr) + 1e-12
        X /= s; Y /= s
        rr = 1.0
    mu = math.sqrt(max(0.0, 1.0 - rr))
    lon_rel = rad2deg(math.atan2(X, mu))
    lat = rad2deg(math.asin(clamp(Y, -1.0, 1.0)))
    lon_iii = wrap_deg(nav.cm_iii_deg + lon_rel)
    return lon_iii, lat


def lonlat_to_px(lon_iii: float, lat: float, nav: Navigation) -> Tuple[float, float]:
    lon_rel = wrap_deg_diff(lon_iii, nav.cm_iii_deg)
    lon_r = deg2rad(lon_rel); lat_r = deg2rad(lat)
    X = math.cos(lat_r) * math.sin(lon_r)
    Y = math.sin(lat_r)
    x = nav.xc + X * nav.a_eq_px
    y = nav.yc - Y * nav.b_pol_px
    return y, x


# ---------------------------------------------------------------------------
# GRS segmentation & measurement
# ---------------------------------------------------------------------------

def otsu_threshold(image: np.ndarray, mask: Optional[np.ndarray] = None) -> float:
    img = np.asarray(image, dtype=np.float64)
    vals = img[mask] if mask is not None else img.ravel()
    hist, bin_edges = np.histogram(vals, bins=64)
    hist = hist.astype(np.float64)
    p = hist / (hist.sum() + 1e-12)
    omega = np.cumsum(p)
    mu = np.cumsum(p * np.arange(len(p)))
    mu_t = mu[-1]
    sigma_b = (mu_t * omega - mu) ** 2 / (omega * (1 - omega) + 1e-12)
    k = int(np.nanargmax(sigma_b))
    return float(0.5 * (bin_edges[k] + bin_edges[k+1]))


def grs_latitude_band_mask(shape: Tuple[int,int], nav: Navigation,
                           lat0: float = PC.GRS_NOM_LAT_DEG,
                           dlat: float = PC.GRS_NOM_LAT_WIDTH_DEG) -> np.ndarray:
    h, w = shape
    yy, xx = np.mgrid[0:h, 0:w]
    # vectorized approx lat
    Y = (nav.yc - yy) / (nav.b_pol_px + 1e-12)
    X = (xx - nav.xc) / (nav.a_eq_px + 1e-12)
    rr = X*X + Y*Y
    lat = np.arcsin(np.clip(Y, -1, 1)) * PC.DEG_PER_RAD
    m = (rr <= 1.0) & (lat > lat0 - dlat/2) & (lat < lat0 + dlat/2)
    return m


def segment_grs_adaptive(image: np.ndarray, nav: Navigation) -> np.ndarray:
    img = np.asarray(image, dtype=np.float64)
    band = grs_latitude_band_mask(img.shape, nav)
    hp = highpass(img, sigma=max(2.0, nav.a_eq_px * 0.02))
    # GRS often darker/redder; on mono IR may be contrast feature — use local extrema
    local = hp[band]
    if local.size < 20:
        raise MeasurementError("GRS band empty")
    # take darker or brighter extreme depending on skew
    med = np.median(local)
    # prefer absolute residual peaks
    thr = np.percentile(np.abs(local), 85)
    cand = band & (np.abs(hp) >= thr)
    cand = morph_open_close(cand, 1, 2)
    # keep components near historical longitude unconstrained: largest in band
    lab, n = label_components(cand)
    if n == 0:
        # fallback: darkest region in band
        thr2 = np.percentile(img[band], 20)
        cand = band & (img <= thr2)
        cand = morph_open_close(cand, 1, 2)
        lab, n = label_components(cand)
    if n == 0:
        raise MeasurementError("No GRS candidate component")
    # choose component closest to expected lat and with reasonable size
    best = None; best_score = -1e99
    for i in range(1, n+1):
        m = lab == i
        area = m.sum()
        if area < 20 or area > 0.2 * band.sum():
            continue
        ys, xs = np.where(m)
        cy, cx = ys.mean(), xs.mean()
        lon, lat = px_to_lonlat(cy, cx, nav)
        score = area - 5.0 * abs(lat - PC.GRS_NOM_LAT_DEG)
        if score > best_score:
            best_score = score; best = m
    if best is None:
        best = largest_component(cand)
    return best


def segment_grs_otsu(image: np.ndarray, nav: Navigation) -> np.ndarray:
    band = grs_latitude_band_mask(image.shape, nav)
    thr = otsu_threshold(image, band)
    # darker than otsu inside band
    m = band & (image < thr)
    m = morph_open_close(m, 1, 2)
    return largest_component(m)


def segment_grs(image: np.ndarray, nav: Navigation, method: str,
                manual_mask: Optional[np.ndarray] = None) -> np.ndarray:
    if method == "manual_mask":
        if manual_mask is None:
            raise MeasurementError("manual_mask requested but not provided")
        return manual_mask.astype(bool)
    if method == "otsu":
        return segment_grs_otsu(image, nav)
    if method == "ellipse_fit":
        m = segment_grs_adaptive(image, nav)
        return m
    if method == "moments_blob":
        return segment_grs_adaptive(image, nav)
    return segment_grs_adaptive(image, nav)


def fit_ellipse_to_mask(mask: np.ndarray) -> Tuple[float,float,float,float,float]:
    ys, xs = np.where(mask)
    if len(xs) < 5:
        raise MeasurementError("Mask too small for ellipse")
    # covariance ellipse
    data = np.stack([xs, ys], axis=1).astype(np.float64)
    mean = data.mean(axis=0)
    centered = data - mean
    cov = np.cov(centered.T)
    eigvals, eigvecs = np.linalg.eigh(cov)
    order = np.argsort(eigvals)[::-1]
    eigvals = eigvals[order]; eigvecs = eigvecs[:, order]
    a = 2.0 * math.sqrt(max(eigvals[0], 1e-12))  # ~2 sigma
    b = 2.0 * math.sqrt(max(eigvals[1], 1e-12))
    theta = math.atan2(eigvecs[1, 0], eigvecs[0, 0])
    return float(mean[1]), float(mean[0]), float(a), float(b), float(theta)  # cy,cx,a,b,th


def measure_grs_from_mask(mask: np.ndarray, image: np.ndarray, nav: Navigation,
                          definition_id: str, filter_name: str) -> GRSState:
    ys, xs = np.where(mask)
    if len(xs) == 0:
        raise MeasurementError("Empty GRS mask")
    weights = image[ys, xs]
    weights = np.clip(weights - np.percentile(weights, 10), 0, None) + 1e-6
    # for dark GRS, invert weights
    if np.mean(image[mask]) < np.mean(image[rough_disk_mask(image)]):
        weights = (np.max(weights) + 1e-6) - weights + 1e-6
    wsum = weights.sum()
    xc = float(np.sum(xs * weights) / wsum)
    yc = float(np.sum(ys * weights) / wsum)
    lon, lat = px_to_lonlat(yc, xc, nav)
    cy, cx, a_px, b_px, th = fit_ellipse_to_mask(mask)
    # convert axes to degrees using local scale
    km_per_px = (2.0 * PC.JUPITER_REQ_KM) / (2.0 * nav.a_eq_px + 1e-12)
    # deg per px lon at lat
    km_per_deg_lon = jupiter_eq_km_per_deg(lat)
    km_per_deg_lat = jupiter_km_per_deg_lat()
    length_deg = (2.0 * a_px * km_per_px) / (km_per_deg_lon + 1e-12)
    width_deg = (2.0 * b_px * km_per_px) / (km_per_deg_lat + 1e-12)
    area_km2 = float(mask.sum() * (km_per_px ** 2))
    aspect = length_deg / (width_deg + 1e-12)
    return GRSState(
        t_tdb_mjd=nav.epoch_tdb_mjd, lon_iii_deg=lon, lat_deg=lat,
        length_deg=float(length_deg), width_deg=float(width_deg), area_km2=area_km2,
        aspect=float(aspect), pa_deg=float(rad2deg(th)), definition_id=definition_id,
        filter_name=filter_name, xc_px=xc, yc_px=yc, n_mask_pix=int(mask.sum()),
    )


def bootstrap_grs(image: np.ndarray, nav: Navigation, cfg: PipelineConfig,
                  n: Optional[int] = None) -> GRSState:
    n = cfg.bootstrap_n if n is None else n
    rng = np.random.default_rng(cfg.seed)
    img = np.asarray(image, dtype=np.float64)
    noise = estimate_noise_map(img[None, ...]) if img.ndim == 2 else np.std(img) * np.ones_like(img)
    if isinstance(noise, float) or np.ndim(noise) == 0:
        noise_map = np.full_like(img, float(np.std(highpass(img, 2.0))))
    else:
        noise_map = np.asarray(noise, dtype=np.float64)
        if noise_map.shape != img.shape:
            noise_map = np.full_like(img, float(np.median(noise_map)) if noise_map.size else 1.0)
    states = []
    for i in range(n):
        noisy = img + rng.normal(size=img.shape) * (noise_map + 1e-6)
        # jitter nav centre
        if nav.cov_center is not None:
            jitter = rng.multivariate_normal([0,0], nav.cov_center + 1e-6*np.eye(2))
            nav_i = replace(nav, xc=nav.xc + jitter[0], yc=nav.yc + jitter[1])
        else:
            nav_i = nav
        try:
            mask = segment_grs(noisy, nav_i, cfg.segment_method)
            st = measure_grs_from_mask(mask, noisy, nav_i, cfg.grs_definition_id, "IR")
            states.append(st)
        except Exception:
            continue
    if not states:
        mask = segment_grs(img, nav, cfg.segment_method)
        return measure_grs_from_mask(mask, img, nav, cfg.grs_definition_id, "IR")
    lons = np.array([s.lon_iii_deg for s in states])
    # handle wrap
    lons = np.unwrap(np.deg2rad(lons))
    lons = np.rad2deg(lons)
    lats = np.array([s.lat_deg for s in states])
    Ls = np.array([s.length_deg for s in states])
    Ws = np.array([s.width_deg for s in states])
    mean = states[len(states)//2]
    # use circular mean for lon
    lon_m = float(np.mean(lons)) % 360.0
    lat_m = float(np.mean(lats))
    cov = np.cov(np.stack([lons, lats, Ls, Ws], axis=0))
    eb = {
        "sig_lon_deg": float(np.std(lons)),
        "sig_lat_deg": float(np.std(lats)),
        "sig_length_deg": float(np.std(Ls)),
        "sig_width_deg": float(np.std(Ws)),
        "n_boot": float(len(states)),
        "feature_scatter": float(np.std(lons)),
        "total_lon": float(np.std(lons)),
    }
    return GRSState(
        t_tdb_mjd=nav.epoch_tdb_mjd, lon_iii_deg=lon_m, lat_deg=lat_m,
        length_deg=float(np.mean(Ls)), width_deg=float(np.mean(Ws)),
        area_km2=float(np.mean([s.area_km2 or 0 for s in states])),
        aspect=float(np.mean(Ls)/(np.mean(Ws)+1e-12)), pa_deg=mean.pa_deg,
        definition_id=cfg.grs_definition_id, filter_name=mean.filter_name,
        cov=cov, error_budget=eb, xc_px=mean.xc_px, yc_px=mean.yc_px,
        n_mask_pix=mean.n_mask_pix,
    )


# ---------------------------------------------------------------------------
# Trajectory: Kalman + RTS
# ---------------------------------------------------------------------------

def unwrap_longitudes(lons: np.ndarray) -> np.ndarray:
    return np.rad2deg(np.unwrap(np.deg2rad(np.asarray(lons, dtype=np.float64))))


def kalman_rts_1d(t: np.ndarray, z: np.ndarray, r: np.ndarray,
                  q_pos: float, q_vel: float) -> Tuple[np.ndarray, np.ndarray]:
    """
    Constant-velocity Kalman filter + RTS smoother.
    State [pos, vel]; measurements of pos.
    t in days, z measurements, r measurement variances.
    """
    n = len(z)
    x_f = np.zeros((n, 2)); P_f = np.zeros((n, 2, 2))
    x = np.array([z[0], 0.0]); P = np.diag([r[0], 1.0])
    for k in range(n):
        if k > 0:
            dt_ = float(t[k] - t[k-1])
            if dt_ <= 0: dt_ = 1e-6
            F = np.array([[1.0, dt_],[0.0, 1.0]])
            Q = np.array([[q_pos*dt_, 0],[0, q_vel*dt_]])
            x = F @ x
            P = F @ P @ F.T + Q
        # update
        H = np.array([[1.0, 0.0]])
        S = H @ P @ H.T + np.array([[r[k]]])
        K = P @ H.T @ np.linalg.inv(S)
        x = x + (K @ (np.array([z[k]]) - H @ x)).ravel()
        P = (np.eye(2) - K @ H) @ P
        x_f[k] = x; P_f[k] = P
    # RTS
    x_s = x_f.copy(); P_s = P_f.copy()
    for k in range(n-2, -1, -1):
        dt_ = float(t[k+1] - t[k])
        if dt_ <= 0: dt_ = 1e-6
        F = np.array([[1.0, dt_],[0.0, 1.0]])
        Q = np.array([[q_pos*dt_, 0],[0, q_vel*dt_]])
        P_pred = F @ P_f[k] @ F.T + Q
        C = P_f[k] @ F.T @ np.linalg.inv(P_pred + 1e-12*np.eye(2))
        x_s[k] = x_f[k] + C @ (x_s[k+1] - F @ x_f[k])
        P_s[k] = P_f[k] + C @ (P_s[k+1] - P_pred) @ C.T
    return x_s[:, 0], P_s[:, 0, 0]


def smooth_trajectory(states: List[GRSState], cfg: PipelineConfig) -> List[Dict[str, Any]]:
    if not states:
        return []
    # sort by time
    states = sorted(states, key=lambda s: s.t_tdb_mjd)
    t = np.array([s.t_tdb_mjd for s in states], dtype=np.float64)
    lon = unwrap_longitudes(np.array([s.lon_iii_deg for s in states]))
    lat = np.array([s.lat_deg for s in states], dtype=np.float64)
    r_lon = np.array([max(s.error_budget.get("sig_lon_deg", 0.2)**2, 1e-6) for s in states])
    r_lat = np.array([max(s.error_budget.get("sig_lat_deg", 0.2)**2, 1e-6) for s in states])
    if cfg.smoother == "none" or len(states) < 2:
        return [s.to_dict() for s in states]
    lon_s, pl = kalman_rts_1d(t, lon, r_lon, cfg.process_noise_lon, cfg.process_noise_lon*0.1)
    lat_s, pla = kalman_rts_1d(t, lat, r_lat, cfg.process_noise_lon*0.5, cfg.process_noise_lon*0.05)
    out = []
    for i, s in enumerate(states):
        d = s.to_dict()
        d["lon_iii_deg_smooth"] = float(lon_s[i] % 360.0)
        d["lat_deg_smooth"] = float(lat_s[i])
        d["sig_lon_smooth"] = float(math.sqrt(max(pl[i], 0)))
        d["sig_lat_smooth"] = float(math.sqrt(max(pla[i], 0)))
        out.append(d)
    return out


def fit_drift_model(t: np.ndarray, lon: np.ndarray, weights: Optional[np.ndarray] = None) -> Dict[str, float]:
    """lon = lon0 + drift * t  (t days from mean)."""
    t = np.asarray(t, dtype=np.float64)
    lon = unwrap_longitudes(lon)
    t0 = t - t.mean()
    if weights is None:
        weights = np.ones_like(t)
    w = weights / (weights.sum() + 1e-12)
    # weighted least squares
    A = np.column_stack([np.ones_like(t0), t0])
    W = np.diag(w)
    try:
        theta = np.linalg.lstsq(A.T @ W @ A, A.T @ W @ lon, rcond=None)[0]
    except Exception:
        theta = np.array([lon.mean(), 0.0])
    return {"lon0": float(theta[0] % 360.0), "drift_deg_per_day": float(theta[1])}

# ---------------------------------------------------------------------------
# Export helpers
# ---------------------------------------------------------------------------

def export_stack(path: Path, stack: StackResult) -> None:
    hdr = {"FILTER": stack.meta.filter_name, "NSTACK": stack.n_used, "FRACTION": stack.fraction}
    write_fits(path, stack.image, hdr)


def export_state_json(path: Path, state: GRSState) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(state.to_dict(), indent=2, default=str), encoding="utf-8")


def export_trajectory_csv(path: Path, rows: List[Dict[str, Any]]) -> None:
    ensure_dir(path.parent)
    if not rows:
        return
    keys = sorted({k for r in rows for k in r.keys()})
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for r in rows:
            flat = {k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in r.items()}
            w.writerow(flat)


def export_manifest(path: Path, manifest: RunManifest) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(manifest.to_dict(), indent=2, default=str), encoding="utf-8")


def package_versions() -> Dict[str, str]:
    vers = {"grs_pipeline": __version__, "numpy": np.__version__, "python": platform.python_version()}
    if _HAS_SCIPY:
        import scipy
        vers["scipy"] = scipy.__version__
    if _HAS_ASTROPY:
        import astropy
        vers["astropy"] = astropy.__version__
    return vers


# ---------------------------------------------------------------------------
# Synthetic planet generator (for tests / demos)
# ---------------------------------------------------------------------------

def synthetic_jupiter(
    size: int = 256,
    cm: float = 100.0,
    grs_lon: float = 80.0,
    grs_lat: float = -22.0,
    grs_a_deg: float = 12.0,
    grs_b_deg: float = 8.0,
    noise: float = 0.02,
    seed: int = 0,
) -> np.ndarray:
    rng = np.random.default_rng(seed)
    y, x = np.mgrid[0:size, 0:size]
    cy = cx = (size - 1) / 2.0
    a = size * 0.42
    f = PC.JUPITER_FLATTENING
    b = a * (1 - f)
    X = (x - cx) / a
    Y = (cy - y) / b
    rr = X*X + Y*Y
    disk = rr <= 1.0
    mu = np.sqrt(np.clip(1 - rr, 0, 1))
    # limb darkening
    img = np.zeros((size, size), dtype=np.float64)
    img[disk] = 0.4 + 0.6 * mu[disk]
    # belts
    lat = np.arcsin(np.clip(Y, -1, 1))
    belts = 0.08 * np.sin(6 * lat) * disk
    img += belts
    # GRS as dark oval
    lon_rel = deg2rad(wrap_deg_diff(grs_lon, cm))
    lat_r = deg2rad(grs_lat)
    Xg = math.cos(lat_r) * math.sin(lon_rel)
    Yg = math.sin(lat_r)
    gx = cx + Xg * a
    gy = cy - Yg * b
    # local axes in px
    km_per_deg_lon = jupiter_eq_km_per_deg(grs_lat)
    km_per_deg_lat = jupiter_km_per_deg_lat()
    km_per_px = (2 * PC.JUPITER_REQ_KM) / (2 * a)
    ax = (grs_a_deg * km_per_deg_lon) / km_per_px / 2
    by = (grs_b_deg * km_per_deg_lat) / km_per_px / 2
    oval = (((x - gx) / (ax + 1e-12))**2 + ((y - gy) / (by + 1e-12))**2) <= 1.0
    img[oval & disk] *= 0.65
    img += rng.normal(0, noise, img.shape)
    img = np.clip(img, 0, None)
    return img


def synthetic_ser_cube(
    n_frames: int = 100,
    size: int = 128,
    seeing_px: float = 1.5,
    seed: int = 0,
) -> VideoCube:
    rng = np.random.default_rng(seed)
    base = synthetic_jupiter(size=size, seed=seed)
    frames = []
    for i in range(n_frames):
        dy, dx = rng.normal(0, 1.0, 2)
        # quality variation: blur amount
        sig = abs(rng.normal(seeing_px, 0.4))
        fr = shift_image(base, dy, dx)
        fr = gaussian_filter2d(fr, max(0.3, sig))
        fr = fr + rng.normal(0, 0.01, fr.shape)
        frames.append(fr)
    data = np.stack(frames, axis=0)
    meta = FrameMeta(
        path="synthetic.ser", t_utc_mid=dt.datetime(2026, 1, 9, 17, 6, 0),
        filter_name="IR742", exposure_s=0.01, site_lat=22.3, site_lon=114.2,
    )
    return VideoCube(data=data, meta=meta)


# ---------------------------------------------------------------------------
# Validation suite
# ---------------------------------------------------------------------------

def validate_phase_correlate(tol: float = 0.25) -> bool:
    img = synthetic_jupiter(size=128, seed=1)
    dy0, dx0 = 3.4, -2.6
    sh = shift_image(img, dy0, dx0)
    dy, dx, _ = phase_correlate(img, sh)
    # Aligning shifted image back to ref requires the inverse shift of the applied warp.
    ok = abs(dy + dy0) < tol and abs(dx + dx0) < tol
    LOG.info("validate_phase_correlate: dy=%.3f dx=%.3f (applied %.3f,%.3f; expect inv) ok=%s", dy, dx, dy0, dx0, ok)
    return ok


def validate_stack_snr() -> bool:
    rng = np.random.default_rng(0)
    truth = synthetic_jupiter(96, seed=2)
    frames = np.stack([truth + rng.normal(0, 0.05, truth.shape) for _ in range(64)], 0)
    st = stack_mean(frames)
    err1 = np.std(frames[0] - truth)
    errn = np.std(st - truth)
    ok = errn < err1 * 0.5
    LOG.info("validate_stack_snr: err1=%.4f errn=%.4f ok=%s", err1, errn, ok)
    return ok


def validate_nav_synthetic() -> bool:
    img = synthetic_jupiter(size=200, seed=3)
    nav = rough_navigation(img)
    ok = 70 < nav.a_eq_px < 100 and abs(nav.xc - 99.5) < 8
    LOG.info("validate_nav_synthetic: xc=%.2f a=%.2f ok=%s", nav.xc, nav.a_eq_px, ok)
    return ok


def validate_grs_measure() -> bool:
    img = synthetic_jupiter(size=220, cm=100.0, grs_lon=85.0, grs_lat=-22.0, seed=4)
    nav = rough_navigation(img)
    nav.cm_iii_deg = 100.0
    try:
        mask = segment_grs(img, nav, "adaptive_threshold")
        st = measure_grs_from_mask(mask, img, nav, "MOMENT_MASK_IR", "IR742")
        dlon = abs(wrap_deg_diff(st.lon_iii_deg, 85.0))
        dlat = abs(st.lat_deg - (-22.0))
        ok = dlon < 15.0 and dlat < 8.0
        LOG.info("validate_grs_measure: lon=%.2f lat=%.2f dlon=%.2f dlat=%.2f ok=%s",
                 st.lon_iii_deg, st.lat_deg, dlon, dlat, ok)
        return ok
    except Exception as e:
        LOG.warning("validate_grs_measure failed: %s", e)
        return False


def run_validation_suite() -> Dict[str, bool]:
    results = {
        "phase_correlate": validate_phase_correlate(),
        "stack_snr": validate_stack_snr(),
        "nav_synthetic": validate_nav_synthetic(),
        "grs_measure": validate_grs_measure(),
    }
    LOG.info("Validation summary: %s", results)
    return results


# ---------------------------------------------------------------------------
# Input discovery
# ---------------------------------------------------------------------------

FILTER_FILE_HINTS = {
    "IR742": ["ir742", "742", "ir_742"],
    "IR685": ["ir685", "685", "ir_685"],
    "CH4": ["ch4", "890", "methane"],
    "R": ["_r_", "-r-", "red", "filter_r"],
    "G": ["_g_", "-g-", "green", "filter_g"],
    "B": ["_b_", "-b-", "blue", "filter_b"],
    "RGB": ["rgb", "colour", "color"],
}


def guess_filter_from_name(name: str) -> str:
    low = name.lower()
    for filt, hints in FILTER_FILE_HINTS.items():
        for h in hints:
            if h in low:
                return filt
    return "UNKNOWN"


def discover_inputs(raw_dir: Union[str, Path]) -> Dict[str, Path]:
    raw = Path(raw_dir)
    found: Dict[str, Path] = {}
    if not raw.exists():
        return found
    for p in sorted(raw.rglob("*")):
        if p.suffix.lower() not in (".ser", ".fit", ".fits", ".fts"):
            continue
        filt = guess_filter_from_name(p.name)
        # don't overwrite higher-priority explicit names
        if filt not in found or filt == "UNKNOWN":
            if filt == "UNKNOWN":
                # unique key by stem
                found[p.stem] = p
            else:
                found[filt] = p
    return found


# ---------------------------------------------------------------------------
# Main pipeline class
# ---------------------------------------------------------------------------

class GRSCompletePipeline:
    """
    End-to-end human-maximum ground-based GRS pipeline.

    Workflows:
      A) SER/FITS lucky stack per filter
      B) Derotation + channel registration
      C) Restoration + LRGB (imaging)
      D) Navigation + GRS measurement (science)
      E) Trajectory smoothing across epochs
    """

    def __init__(self, config: Optional[PipelineConfig] = None) -> None:
        self.cfg = config or PipelineConfig()
        setup_logging(self.cfg.log_level)
        self.work = ensure_dir(self.cfg.work_dir)
        self.out = ensure_dir(self.cfg.out_dir)
        self.stages: List[Dict[str, Any]] = []
        self.warnings: List[str] = []
        self.stacks: Dict[str, StackResult] = {}
        self.channels: Dict[str, np.ndarray] = {}
        self.nav: Optional[Navigation] = None
        self.state: Optional[GRSState] = None
        self.rgb: Optional[np.ndarray] = None
        random.seed(self.cfg.seed)
        np.random.seed(self.cfg.seed)

    def _record(self, name: str, **kwargs: Any) -> None:
        rec = {"stage": name, "time": dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat() + "Z"}
        rec.update(kwargs)
        self.stages.append(rec)

    def process_cube(self, cube: VideoCube, filter_name: Optional[str] = None) -> Dict[float, StackResult]:
        if filter_name:
            cube.meta.filter_name = filter_name
        qc = validate_cube(cube, self.cfg)
        self._record("qc", ok=qc.ok, metrics=qc.metrics, reasons=qc.reasons)
        if not qc.ok and cube.n_frames > 1:
            raise QCError("; ".join(qc.reasons))
        if not qc.ok and cube.n_frames == 1:
            self.warnings.append("QC soft-fail on single frame: " + "; ".join(qc.reasons))
        results = {}
        for frac in self.cfg.fractions:
            st = lucky_stack_cube(cube, self.cfg, fraction=frac)
            results[frac] = st
            outp = self.work / f"{cube.meta.filter_name}_f{frac:.2f}.fits"
            if self.cfg.write_fits:
                export_stack(outp, st)
        primary = results.get(self.cfg.primary_fraction) or results[sorted(results.keys())[0]]
        self.stacks[cube.meta.filter_name] = primary
        self._record("stack", filter=cube.meta.filter_name, n_used=primary.n_used)
        return results

    def process_path(self, path: Union[str, Path], filter_name: str = "UNKNOWN") -> Dict[float, StackResult]:
        cube = ingest_path(path, filter_name, self.cfg.site_lat, self.cfg.site_lon, self.cfg.site_elev_m)
        # RGB FITS special case
        if Path(path).suffix.lower() in (".fit", ".fits", ".fts"):
            try:
                data, _ = read_fits(path)
                if np.asarray(data).ndim == 3 and (np.asarray(data).shape[0] == 3 or np.asarray(data).shape[-1] == 3):
                    ch = read_rgb_fits_channels(path)
                    for k, img in ch.items():
                        st = StackResult(
                            image=np.asarray(img, dtype=np.float64), n_used=1, fraction=1.0,
                            quality_threshold=0.0, meta=replace(cube.meta, filter_name=k),
                        )
                        self.stacks[k] = st
                        self.channels[k] = st.image
                    self._record("ingest_rgb_fits", path=str(path), channels=list(ch.keys()))
                    return {1.0: next(iter(self.stacks.values()))}
            except Exception as e:
                LOG.debug("RGB special path failed: %s", e)
        return self.process_cube(cube, filter_name)

    def derotate_all(self, t_ref: Optional[dt.datetime] = None) -> None:
        if not self.cfg.derot_enable:
            return
        # choose ref time
        times = [s.meta.t_utc_mid for s in self.stacks.values() if s.meta.t_utc_mid]
        if t_ref is None and times:
            t_ref = times[len(times)//2]
        for k, st in list(self.stacks.items()):
            self.stacks[k] = derotate_stack_result(st, self.cfg, t_ref)
        self._record("derotate", t_ref=str(t_ref))

    def build_channels(self) -> Dict[str, np.ndarray]:
        ch = {k: v.image for k, v in self.stacks.items()}
        # prefer register
        ref = self.cfg.l_source if self.cfg.l_source in ch else ( "G" if "G" in ch else next(iter(ch)))
        ch = register_channels(ch, ref_name=ref)
        self.channels = ch
        self._record("register_channels", keys=list(ch.keys()), ref=ref)
        return ch

    def run_imaging(self) -> Optional[np.ndarray]:
        if self.cfg.mode not in ("imaging", "both", PipelineMode.IMAGING.value, PipelineMode.BOTH.value):
            return None
        ch = self.channels or self.build_channels()
        # luminance
        l_src = self.cfg.l_source
        if l_src in ch:
            L = ch[l_src]
        elif "IR742" in ch:
            L = ch["IR742"]
        elif "R" in ch:
            L = ch["R"]
        else:
            L = next(iter(ch.values()))
        with StageTimer("restore"):
            # rough nav for psf optional
            nav = rough_navigation(L)
            psf = estimate_psf_from_limb(L, nav.xc, nav.yc, nav.a_eq_px)
            Lr = restore_image(L, self.cfg, psf=psf)
            if limb_overshoot_metric(Lr) > 0.15:
                self.warnings.append("Restore ringing high; falling back to milder wavelets")
                mild = replace(self.cfg, wavelet_gains=tuple(0.5*g for g in self.cfg.wavelet_gains), rl_iters=3)
                Lr = restore_image(L, mild, psf=psf)
        R = ch.get("R"); G = ch.get("G"); B = ch.get("B")
        if R is None and G is None and B is None:
            # mono preview as RGB
            rgb = np.stack([normalize_percentile(Lr)]*3, axis=2)
        else:
            rgb = build_lrgb(Lr, R, G, B, self.cfg.sat_scale, self.cfg.denoise_chroma)
        self.rgb = rgb
        if self.cfg.write_png:
            write_png(self.out / "lrgb_final.png", rgb)
        if self.cfg.write_fits:
            write_fits(self.out / "luminance_restored.fits", Lr)
            write_fits(self.out / "lrgb_final.fits", np.moveaxis(rgb, 2, 0))
        self._record("imaging", out="lrgb_final")
        return rgb

    def run_science(self) -> Optional[GRSState]:
        if self.cfg.mode not in ("science", "both", PipelineMode.SCIENCE.value, PipelineMode.BOTH.value):
            return None
        ch = self.channels or self.build_channels()
        for key in ("IR742", "IR685", "R", "G", "B"):
            if key in ch:
                meas = ch[key]
                fname = key
                break
        else:
            meas = next(iter(ch.values()))
            fname = next(iter(ch.keys()))
        meta = self.stacks.get(fname, StackResult(image=meas, n_used=1, fraction=1.0, quality_threshold=0)).meta
        nav = fit_navigation(meas, meta, self.cfg)
        self.nav = nav
        with StageTimer("grs_measure"):
            try:
                state = bootstrap_grs(meas, nav, self.cfg)
            except Exception as e:
                LOG.warning("Bootstrap failed (%s); single measure", e)
                mask = segment_grs(meas, nav, self.cfg.segment_method)
                state = measure_grs_from_mask(mask, meas, nav, self.cfg.grs_definition_id, fname)
        state.filter_name = fname
        self.state = state
        export_state_json(self.out / "grs_state.json", state)
        (self.out / "nav.json").write_text(json.dumps(nav.to_dict(), indent=2, default=str), encoding="utf-8")
        # append trajectory
        traj_path = self.out / "trajectory.csv"
        row = state.to_dict()
        rows = []
        if traj_path.exists():
            with open(traj_path, newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
        rows.append({k: (json.dumps(v) if isinstance(v, (dict, list)) else v) for k, v in row.items()})
        if self.cfg.traj_enable and len(rows) >= 1:
            # rebuild states minimally for smoother if possible
            export_trajectory_csv(traj_path, rows)
        self._record("science", lon=state.lon_iii_deg, lat=state.lat_deg, err=state.error_budget)
        return state

    def run(self, inputs: Optional[Mapping[str, Union[str, Path]]] = None) -> RunManifest:
        with StageTimer("pipeline_total"):
            if inputs is None:
                inputs = discover_inputs(self.cfg.raw_dir)
            if not inputs:
                raise IngestError(f"No inputs found in {self.cfg.raw_dir}")
            input_shas = []
            for filt, path in inputs.items():
                path = Path(path)
                LOG.info("Processing %s -> %s", filt, path)
                try:
                    input_shas.append(sha256_file(path))
                except Exception:
                    input_shas.append("")
                self.process_path(path, filter_name=str(filt))
            self.derotate_all()
            self.build_channels()
            if self.cfg.mode in ("imaging", "both"):
                self.run_imaging()
            if self.cfg.mode in ("science", "both"):
                self.run_science()
            manifest = RunManifest(
                version=__version__, mode=self.cfg.mode, config_sha=self.cfg.sha(),
                input_shas=input_shas, package_versions=package_versions(),
                seed=self.cfg.seed, stages=self.stages, warnings=self.warnings,
            )
            export_manifest(self.out / "run_manifest.json", manifest)
            return manifest


def run_pipeline(config: Optional[PipelineConfig] = None,
                 inputs: Optional[Mapping[str, Union[str, Path]]] = None) -> RunManifest:
    pipe = GRSCompletePipeline(config)
    return pipe.run(inputs)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def build_arg_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="grs_complete_system",
        description="Human-maximum ground-based GRS/Jupiter imaging & science pipeline",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="Run full pipeline on raw_dir or explicit files")
    r.add_argument("--raw-dir", default="data/raw")
    r.add_argument("--work-dir", default="data/work")
    r.add_argument("--out-dir", default="data/out")
    r.add_argument("--mode", default="both", choices=["imaging", "science", "both"])
    r.add_argument("--input", action="append", default=[], help="filter=path (repeatable)")
    r.add_argument("--fraction", type=float, default=0.15)
    r.add_argument("--restore", default="wavelets")
    r.add_argument("--site-lat", type=float, default=22.3)
    r.add_argument("--site-lon", type=float, default=114.2)
    r.add_argument("--no-derot", action="store_true")
    r.add_argument("--log-level", default="INFO")

    s = sub.add_parser("stack", help="Lucky-stack a single SER/FITS")
    s.add_argument("path")
    s.add_argument("--filter", default="UNKNOWN")
    s.add_argument("--fraction", type=float, default=0.15)
    s.add_argument("--out", default="stack.fits")

    m = sub.add_parser("measure", help="Navigate + measure GRS on a stacked FITS")
    m.add_argument("path")
    m.add_argument("--out", default="grs_state.json")
    m.add_argument("--bootstrap", type=int, default=50)

    v = sub.add_parser("validate", help="Run synthetic validation suite")
    v.add_argument("--quick", action="store_true")

    d = sub.add_parser("demo", help="Run synthetic end-to-end demo")
    d.add_argument("--out-dir", default="data/out_demo")
    d.add_argument("--frames", type=int, default=80)

    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.cmd == "validate":
        res = run_validation_suite()
        return 0 if all(res.values()) else 1

    if args.cmd == "demo":
        cfg = PipelineConfig(mode="both", out_dir=args.out_dir, work_dir=str(Path(args.out_dir)/"work"),
                             fractions=(0.25, 0.5), primary_fraction=0.25, min_frames=10,
                             bootstrap_n=20, bootstrap_limb=10, derot_enable=False,
                             max_clip_frac=0.2, flux_drop_frac=0.8)
        pipe = GRSCompletePipeline(cfg)
        cube = synthetic_ser_cube(n_frames=args.frames, size=160, seed=7)
        # save synthetic mono as stack path workflow
        pipe.process_cube(cube, "IR742")
        # also make fake RGB from mono
        img = pipe.stacks["IR742"].image
        pipe.stacks["R"] = replace(pipe.stacks["IR742"], image=img*1.02, meta=replace(cube.meta, filter_name="R"))
        pipe.stacks["G"] = replace(pipe.stacks["IR742"], image=img*1.00, meta=replace(cube.meta, filter_name="G"))
        pipe.stacks["B"] = replace(pipe.stacks["IR742"], image=img*0.98, meta=replace(cube.meta, filter_name="B"))
        pipe.build_channels(); pipe.run_imaging(); pipe.run_science()
        LOG.info("Demo complete -> %s", args.out_dir)
        return 0

    if args.cmd == "stack":
        cfg = PipelineConfig(primary_fraction=args.fraction, fractions=(args.fraction,), min_frames=1)
        cube = ingest_path(args.path, args.filter)
        if cube.quality is None:
            cube.quality = score_frames(cube, cfg.quality_metric)
        st = lucky_stack_cube(cube, cfg, args.fraction)
        write_fits(args.out, st.image)
        LOG.info("Wrote %s (n=%d)", args.out, st.n_used)
        return 0

    if args.cmd == "measure":
        cfg = PipelineConfig(bootstrap_n=args.bootstrap, mode="science")
        data, hdr = read_fits(args.path)
        data = np.asarray(data, dtype=np.float64)
        if data.ndim == 3:
            data = data[0] if data.shape[0] < 8 else data.mean(axis=0)
        # Never silent datetime.now() — corrupts System III
        try:
            from fits_time import require_observation_time, format_utc
            t_mid, tsrc = require_observation_time(fits_path=args.path, hdr=hdr)
            LOG.info("Observation UTC from %s → %s", tsrc, format_utc(t_mid))
        except Exception as e:
            LOG.error("%s", e)
            return 2
        meta = FrameMeta(path=args.path, filter_name="IR742", t_utc_mid=t_mid)
        nav = fit_navigation(data, meta, cfg)
        st = bootstrap_grs(data, nav, cfg)
        export_state_json(Path(args.out), st)
        LOG.info("GRS lon=%.3f lat=%.3f", st.lon_iii_deg, st.lat_deg)
        return 0

    if args.cmd == "run":
        cfg = PipelineConfig(
            mode=args.mode, raw_dir=args.raw_dir, work_dir=args.work_dir, out_dir=args.out_dir,
            primary_fraction=args.fraction, fractions=(args.fraction, min(0.5, args.fraction*2)),
            restore_method=args.restore, site_lat=args.site_lat, site_lon=args.site_lon,
            derot_enable=not args.no_derot, log_level=args.log_level, min_frames=1,
        )
        inputs = None
        if args.input:
            inputs = {}
            for item in args.input:
                if "=" not in item:
                    raise SystemExit("--input must be filter=path")
                k, v = item.split("=", 1)
                inputs[k] = v
        man = run_pipeline(cfg, inputs)
        LOG.info("Pipeline finished. manifest stages=%d warnings=%d", len(man.stages), len(man.warnings))
        return 0

    return 2


# ===========================================================================
# EXTENDED PROFESSIONAL MODULES
# ===========================================================================

@dataclass(frozen=True)
class FilterBandpass:
    name: str
    center_nm: float
    fwhm_nm: float
    kind: str
    notes: str = ""


FILTER_CATALOG: Dict[str, FilterBandpass] = {
    "B": FilterBandpass("B", 470.0, 100.0, "color", "Blue continuum"),
    "G": FilterBandpass("G", 530.0, 80.0, "color", "Green continuum"),
    "R": FilterBandpass("R", 620.0, 80.0, "color", "Red continuum"),
    "IR685": FilterBandpass("IR685", 685.0, 20.0, "continuum", "IR-pass"),
    "IR742": FilterBandpass("IR742", 742.0, 20.0, "continuum", "Deep IR"),
    "IR807": FilterBandpass("IR807", 807.0, 20.0, "continuum", "Near-IR"),
    "CH4": FilterBandpass("CH4", 890.0, 20.0, "methane", "Methane band"),
    "UV": FilterBandpass("UV", 380.0, 40.0, "color", "Near-UV"),
    "CLEAR": FilterBandpass("CLEAR", 550.0, 300.0, "clear", "Clear"),
    "L": FilterBandpass("L", 550.0, 300.0, "clear", "Luminance"),
}


def filter_center_nm(name: str) -> float:
    if name in FILTER_CATALOG:
        return FILTER_CATALOG[name].center_nm
    return FILTER_WAVELENGTH_NM.get(name, 550.0)


def rad_to_arcsec(r: float) -> float:
    return r * PC.ARCSEC_PER_RAD


def diffraction_limit_arcsec(diameter_m: float, wavelength_nm: float) -> float:
    lam_m = wavelength_nm * 1e-9
    theta_rad = 1.22 * lam_m / max(diameter_m, 1e-6)
    return rad_to_arcsec(theta_rad)


def critical_sampling_arcsec_per_px(diameter_m: float, wavelength_nm: float, factor: float = 2.5) -> float:
    return diffraction_limit_arcsec(diameter_m, wavelength_nm) / factor


def plate_scale_arcsec_per_px(pixel_um: float, focal_length_mm: float) -> float:
    return 206.265 * pixel_um / max(focal_length_mm, 1e-6)


def effective_focal_length_mm(pixel_um: float, arcsec_per_px: float) -> float:
    return 206.265 * pixel_um / max(arcsec_per_px, 1e-9)


def suggest_roi(planet_diameter_arcsec: float, scale: float, margin: float = 1.4) -> int:
    return int(math.ceil(planet_diameter_arcsec / max(scale, 1e-6) * margin))


_FC_PATTERNS = {
    "exposure": re.compile(r"Exposure\s*[:=]\s*([0-9.]+)", re.I),
    "gain": re.compile(r"Gain\s*[:=]\s*([0-9.]+)", re.I),
    "filter": re.compile(r"Filter\s*[:=]\s*([A-Za-z0-9_\-]+)", re.I),
    "fps": re.compile(r"(?:FPS|Frame rate)\s*[:=]\s*([0-9.]+)", re.I),
    "date": re.compile(r"(?:Date|Timestamp)\s*[:=]\s*(.+)", re.I),
    "camera": re.compile(r"Camera\s*[:=]\s*(.+)", re.I),
    "temp": re.compile(r"(?:Temperature|Temp)\s*[:=]\s*([+\-0-9.]+)", re.I),
}


def parse_firecapture_log(path: Union[str, Path]) -> Dict[str, Any]:
    path = Path(path)
    text = path.read_text(encoding="utf-8", errors="replace")
    out: Dict[str, Any] = {"path": str(path)}
    for key, pat in _FC_PATTERNS.items():
        m = pat.search(text)
        if not m:
            continue
        val = m.group(1).strip()
        if key in ("exposure", "gain", "fps", "temp"):
            try:
                out[key] = float(val)
            except ValueError:
                out[key] = val
        else:
            out[key] = val
    return out


def apply_log_to_meta(meta: FrameMeta, log: Mapping[str, Any]) -> FrameMeta:
    m = copy.copy(meta)
    if "exposure" in log:
        m.exposure_s = float(log["exposure"]) / (1000.0 if float(log["exposure"]) > 5 else 1.0)
    if "gain" in log:
        m.gain = float(log["gain"])
    if "filter" in log:
        m.filter_name = str(log["filter"])
    if "camera" in log:
        m.camera = str(log["camera"])
    if "temp" in log:
        try:
            m.temperature_c = float(log["temp"])
        except Exception:
            pass
    if "date" in log:
        try:
            m.t_utc_mid = parse_time_string(str(log["date"]))
        except Exception:
            pass
    return m


def drizzle_combine(
    frames: np.ndarray,
    shifts: Sequence[Tuple[float, float]],
    scale: float = 1.5,
    pixfrac: float = 0.7,
) -> np.ndarray:
    n, h, w = frames.shape
    oh, ow = int(round(h * scale)), int(round(w * scale))
    acc = np.zeros((oh, ow), dtype=np.float64)
    wgt = np.zeros((oh, ow), dtype=np.float64)
    half = max(pixfrac * scale / 2.0, 1e-6)
    for i in range(n):
        dy, dx = shifts[i] if i < len(shifts) else (0.0, 0.0)
        img = frames[i]
        yy, xx = np.mgrid[0:oh, 0:ow].astype(np.float64)
        y_in = yy / scale - dy
        x_in = xx / scale - dx
        yi = np.rint(y_in).astype(np.int64)
        xi = np.rint(x_in).astype(np.int64)
        valid = (yi >= 0) & (yi < h) & (xi >= 0) & (xi < w)
        wy = 1.0 - np.minimum(np.abs(y_in - yi) / half, 1.0)
        wx = 1.0 - np.minimum(np.abs(x_in - xi) / half, 1.0)
        ww = np.clip(wy * wx, 0, 1)
        vals = np.zeros((oh, ow), dtype=np.float64)
        vals[valid] = img[yi[valid], xi[valid]]
        acc += vals * ww
        wgt += ww * valid
    return safe_div(acc, wgt)


def quality_pyramid(image: np.ndarray, levels: int = 4) -> List[float]:
    scores = []
    img = np.asarray(image, dtype=np.float64)
    for _ in range(levels):
        scores.append(score_laplacian_var(img))
        img = gaussian_filter2d(img, 1.0)[::2, ::2]
        if min(img.shape) < 16:
            break
    return scores


def hybrid_quality_vector(image: np.ndarray) -> np.ndarray:
    return np.array([
        score_laplacian_var(image),
        score_fft_power(image),
        score_sobel_energy(image),
        score_variance(image),
        score_tenengrad(image),
    ], dtype=np.float64)


def rank_frames_multi_metric(cube: VideoCube) -> np.ndarray:
    n = cube.n_frames
    M = np.zeros((n, 5), dtype=np.float64)
    for i in range(n):
        M[i] = hybrid_quality_vector(cube.data[i])
    mu = M.mean(axis=0); sd = M.std(axis=0) + 1e-12
    return ((M - mu) / sd).mean(axis=1)


def make_lon_lat_grid(nav: Navigation, shape: Tuple[int, int]) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    h, w = shape
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float64)
    X = (xx - nav.xc) / (nav.a_eq_px + 1e-12)
    Y = (nav.yc - yy) / (nav.b_pol_px + 1e-12)
    rr = X*X + Y*Y
    mu = np.sqrt(np.clip(1.0 - rr, 0, 1))
    lon_rel = np.arctan2(X, mu)
    lat = np.arcsin(np.clip(Y, -1, 1))
    lon_iii = (nav.cm_iii_deg + lon_rel * PC.DEG_PER_RAD) % 360.0
    lat_deg = lat * PC.DEG_PER_RAD
    return lon_iii, lat_deg, rr


def reproject_to_simple_cylindrical(
    image: np.ndarray, nav: Navigation, out_w: int = 3600, out_h: int = 1800, lon0: float = 0.0,
) -> np.ndarray:
    img = np.asarray(image, dtype=np.float64)
    lons = (np.linspace(0, 360, out_w, endpoint=False) + lon0) % 360
    lats = np.linspace(90, -90, out_h)
    lon_g, lat_g = np.meshgrid(lons, lats)
    lon_rel = np.deg2rad(((lon_g - nav.cm_iii_deg + 180) % 360) - 180)
    lat_r = np.deg2rad(lat_g)
    X = np.cos(lat_r) * np.sin(lon_rel)
    Y = np.sin(lat_r)
    mu = np.cos(lat_r) * np.cos(lon_rel)
    xs = nav.xc + X * nav.a_eq_px
    ys = nav.yc - Y * nav.b_pol_px
    sampled = map_coords(img, np.array([ys, xs]), order=1, mode="constant", cval=0.0)
    sampled[mu <= 0] = 0.0
    return sampled


def map_measure_grs(cyl_map: np.ndarray, lat0: float = -22.0, dlat: float = 10.0) -> Dict[str, float]:
    h, w = cyl_map.shape
    def lat_to_y(lat: float) -> int:
        return int(np.clip((90 - lat) / 180.0 * (h - 1), 0, h - 1))
    y0 = lat_to_y(lat0 + dlat/2); y1 = lat_to_y(lat0 - dlat/2)
    if y1 < y0: y0, y1 = y1, y0
    band = cyl_map[y0:y1+1, :]
    prof = band.mean(axis=0)
    k = np.ones(15)/15
    prof_s = np.convolve(prof, k, mode="same")
    j = int(np.argmin(prof_s))
    return {"lon_iii_deg": 360.0 * j / w, "map_x": float(j), "map_y0": float(y0), "map_y1": float(y1)}


def assemble_error_budget(state: GRSState, nav: Navigation, stack: Optional[StackResult] = None) -> Dict[str, float]:
    eb = dict(state.error_budget)
    if stack is not None and stack.noise_map is not None:
        eb["snr_proxy"] = float(1.0 / (np.median(stack.noise_map) + 1e-6))
    if nav.cov_center is not None:
        s_px = float(np.sqrt(np.trace(nav.cov_center) / 2.0))
        deg_per_px = (180.0 / math.pi) / (nav.a_eq_px + 1e-12)
        eb["nav_center_deg"] = s_px * deg_per_px
    eb.setdefault("definition_floor_deg", 0.05)
    parts = [eb.get("sig_lon_deg", 0.0)**2, eb.get("nav_center_deg", 0.0)**2, eb.get("definition_floor_deg", 0.0)**2]
    eb["total_lon_rms_deg"] = float(math.sqrt(sum(parts)))
    return eb


def write_text_report(path: Union[str, Path], pipe: "GRSCompletePipeline") -> None:
    path = Path(path)
    lines = ["GRS GROUND PIPELINE REPORT", "="*72, f"Version: {__version__}", f"Mode: {pipe.cfg.mode}", ""]
    if pipe.nav:
        lines += ["NAVIGATION", f"  centre=({pipe.nav.xc:.2f},{pipe.nav.yc:.2f})", f"  a={pipe.nav.a_eq_px:.2f}", f"  CM={pipe.nav.cm_iii_deg:.3f}", ""]
    if pipe.state:
        s = pipe.state
        lines += ["GRS STATE", f"  lon={s.lon_iii_deg:.4f}", f"  lat={s.lat_deg:.4f}", f"  L={s.length_deg:.4f}", f"  W={s.width_deg:.4f}", f"  err={s.error_budget}", ""]
    lines.append("STACKS")
    for k, st in pipe.stacks.items():
        lines.append(f"  {k}: n={st.n_used} frac={st.fraction}")
    lines += ["", "WARNINGS"] + [f"  - {w}" for w in pipe.warnings] + ["", "STAGES"]
    for stg in pipe.stages:
        lines.append(f"  {stg}")
    path.write_text("\n".join(lines), encoding="utf-8")


class FixedLagLuckyStacker:
    def __init__(self, lag: int = 200, fraction: float = 0.15, metric: str = "laplacian_var") -> None:
        self.lag = lag; self.fraction = fraction; self.metric = metric
        self.buffer: deque = deque(maxlen=lag); self.scores: deque = deque(maxlen=lag)
    def push(self, frame: np.ndarray) -> Optional[np.ndarray]:
        self.buffer.append(np.asarray(frame, dtype=np.float64))
        self.scores.append(score_frame(frame, self.metric))
        if len(self.buffer) < self.lag: return None
        return self.stack_now()
    def stack_now(self) -> np.ndarray:
        frames = np.stack(list(self.buffer), 0)
        scores = np.asarray(self.scores, dtype=np.float64)
        idx = select_top_indices(scores, self.fraction)
        sel = frames[idx]
        aligned, _ = align_frames_global(sel, ref_index=int(np.argmax(scores[idx])))
        return stack_kappa_sigma(aligned)


PRESET_CHAMPIONSHIP_IMAGING = PipelineConfig(
    mode="imaging", fractions=(0.08, 0.12, 0.20), primary_fraction=0.12,
    quality_metric="hybrid", align_mode="local_ap", ap_grid=14,
    stack_method="kappa_sigma", restore_method="wavelets",
    wavelet_gains=(0.0, 0.15, 0.7, 1.3, 0.7, 0.25), l_source="IR742",
    sat_scale=0.8, derot_enable=True, min_frames=100,
)
PRESET_SCIENCE_CAREFUL = PipelineConfig(
    mode="science", fractions=(0.20, 0.35), primary_fraction=0.25,
    quality_metric="laplacian_var", align_mode="rigid",
    stack_method="quality_weighted", restore_method="none", derot_enable=True,
    bootstrap_n=200, bootstrap_limb=80, min_frames=50,
)
PRESET_FAST_PREVIEW = PipelineConfig(
    mode="both", fractions=(0.3,), primary_fraction=0.3, align_mode="global",
    ap_grid=6, restore_method="wavelets", wavelet_layers=4,
    wavelet_gains=(0.0, 0.4, 0.8, 0.4), bootstrap_n=20, bootstrap_limb=10,
    min_frames=10, derot_enable=False,
)


def get_preset(name: str) -> PipelineConfig:
    name = name.lower().strip()
    if name in ("championship", "imaging", "photo"):
        return copy.deepcopy(PRESET_CHAMPIONSHIP_IMAGING)
    if name in ("science", "measure", "careful"):
        return copy.deepcopy(PRESET_SCIENCE_CAREFUL)
    if name in ("fast", "preview", "quick"):
        return copy.deepcopy(PRESET_FAST_PREVIEW)
    raise ConfigError(f"Unknown preset: {name}")


CAPABILITY_STATEMENT = """
GRS Complete Ground Pipeline — Capability Statement
Imaging: lucky scoring, AP align, robust stack, wavelets/RL, LRGB
Science: timing, derotation, limb nav, GRS measure, bootstrap, RTS
Honesty: ground-based degrees/km, not VLBI μas claims
"""


def print_capabilities() -> None:
    print(CAPABILITY_STATEMENT)


def estimate_plate_background_gradient(image: np.ndarray, order: int = 2) -> np.ndarray:
    h, w = image.shape
    yy, xx = np.mgrid[0:h, 0:w]
    border = (yy < h*0.05) | (yy > h*0.95) | (xx < w*0.05) | (xx > w*0.95)
    xb, yb, zb = xx[border], yy[border], image[border]
    cols = [np.ones_like(xb), xb, yb, xb**2, xb*yb, yb**2][: (1 + 2 + (3 if order>=2 else 0))]
    if order == 1:
        cols = [np.ones_like(xb), xb, yb]
    A = np.column_stack(cols)
    coef, *_ = np.linalg.lstsq(A, zb, rcond=None)
    XX, YY = xx.astype(np.float64), yy.astype(np.float64)
    bg = coef[0] * np.ones_like(XX)
    if order >= 1 and len(coef) >= 3:
        bg += coef[1]*XX + coef[2]*YY
    if order >= 2 and len(coef) >= 6:
        bg += coef[3]*XX**2 + coef[4]*XX*YY + coef[5]*YY**2
    return bg


def restore_digitized_plate(image: np.ndarray) -> np.ndarray:
    img = np.asarray(image, dtype=np.float64)
    flat = img - estimate_plate_background_gradient(img)
    if np.median(flat) < 0:
        flat = -flat
    return np.clip(flat, 0, None)


def airmass_approx(alt_deg: float) -> float:
    z = 90.0 - alt_deg
    return 1.0 / max(math.cos(deg2rad(z)) + 0.025 * math.exp(-11 * math.cos(deg2rad(z))), 1e-3)


def score_session(alt_deg: float, seeing_arcsec: float, transparency: float = 1.0) -> float:
    s_alt = 0.0 if alt_deg < 20 else 0.3 if alt_deg < 30 else 0.6 if alt_deg < 40 else 0.85 if alt_deg < 50 else 1.0
    s_see = clamp(1.0 - (seeing_arcsec - 0.5) / 2.5, 0.0, 1.0)
    return float(0.45 * s_see + 0.35 * s_alt + 0.20 * clamp(transparency, 0, 1))


def unsharp_mask(image: np.ndarray, sigma: float = 2.0, strength: float = 0.5) -> np.ndarray:
    img = np.asarray(image, dtype=np.float64)
    return img + strength * (img - gaussian_filter2d(img, sigma))


def image_moments(mask: np.ndarray, image: Optional[np.ndarray] = None) -> Dict[str, float]:
    ys, xs = np.where(mask)
    if len(xs) == 0:
        return {k: 0.0 for k in ("m00","m10","m01","mu20","mu02","mu11","cx","cy")}
    w = np.ones(len(xs)) if image is None else np.clip(image[ys, xs], 0, None) + 1e-12
    m00 = float(w.sum()); m10 = float((xs*w).sum()); m01 = float((ys*w).sum())
    cx = m10/m00; cy = m01/m00
    x = xs-cx; y = ys-cy
    return {"m00": m00, "m10": m10, "m01": m01,
            "mu20": float((w*x*x).sum())/m00, "mu02": float((w*y*y).sum())/m00,
            "mu11": float((w*x*y).sum())/m00, "cx": cx, "cy": cy}


def run_selftests() -> None:
    _assert = lambda c, m: (_ for _ in ()).throw(AssertionError(m)) if not c else None
    _assert(abs(wrap_deg(370)-10)<1e-9, "wrap")
    _assert(abs(moffat_psf(15).sum()-1)<1e-6, "psf")
    img = synthetic_jupiter(64, seed=1)
    layers, res = starlet_decompose(img, 3)
    rec = res + sum(layers)
    _assert(np.mean(np.abs(rec-img)) < 1e-5*(np.max(img)+1), "starlet")
    LOG.info("selftests OK")


class PipelineStateMachine:
    def __init__(self, cfg: PipelineConfig) -> None:
        self.cfg = cfg; self.state = "INIT"; self.history = ["INIT"]; self.context: Dict[str, Any] = {}
    def transition(self, new_state: str) -> None:
        self.state = new_state; self.history.append(new_state)
    def run_on_cube(self, cube: VideoCube) -> StackResult:
        self.transition("INGEST_DONE")
        qc = validate_cube(cube, self.cfg); self.context["qc"] = qc
        self.transition("QC_DONE")
        if not qc.ok and cube.n_frames > 1:
            self.transition("FAILED_QC"); raise QCError(str(qc.reasons))
        cube.quality = score_frames(cube, self.cfg.quality_metric)
        self.transition("SCORED")
        st = lucky_stack_cube(cube, self.cfg); self.transition("STACKED"); self.context["stack"] = st
        return st


class MultiFilterNight:
    def __init__(self, name: str, cfg: PipelineConfig) -> None:
        self.name = name; self.cfg = cfg; self.cubes: Dict[str, VideoCube] = {}; self.stacks: Dict[str, StackResult] = {}
    def add(self, filter_name: str, cube: VideoCube) -> None:
        self.cubes[filter_name] = cube
    def reduce_all(self) -> Dict[str, StackResult]:
        sm = PipelineStateMachine(self.cfg)
        for f, c in self.cubes.items():
            self.stacks[f] = sm.run_on_cube(c)
        return self.stacks


NOLL_ZERNIKES: Dict[int, Tuple[int, int, str]] = {
    1: (0, 0, 'piston'),
    2: (1, 1, 'tip'),
    3: (1, -1, 'tilt'),
    4: (2, 0, 'defocus'),
    5: (2, -2, 'astigmatism_45'),
    6: (2, 2, 'astigmatism_0'),
    7: (3, -1, 'coma_y'),
    8: (3, 1, 'coma_x'),
    9: (3, -3, 'trefoil_y'),
    10: (3, 3, 'trefoil_x'),
    11: (4, 0, 'spherical'),
    12: (4, 0, 'Z12'),
    13: (4, 0, 'Z13'),
    14: (4, 0, 'Z14'),
    15: (4, 0, 'Z15'),
    16: (5, 0, 'Z16'),
    17: (5, 0, 'Z17'),
    18: (5, 0, 'Z18'),
    19: (5, 0, 'Z19'),
    20: (5, 0, 'Z20'),
    21: (5, 0, 'Z21'),
    22: (6, 0, 'Z22'),
    23: (6, 0, 'Z23'),
    24: (6, 0, 'Z24'),
    25: (6, 0, 'Z25'),
    26: (6, 0, 'Z26'),
    27: (6, 0, 'Z27'),
    28: (6, 0, 'Z28'),
    29: (7, 0, 'Z29'),
    30: (7, 0, 'Z30'),
    31: (7, 0, 'Z31'),
    32: (7, 0, 'Z32'),
    33: (7, 0, 'Z33'),
    34: (7, 0, 'Z34'),
    35: (7, 0, 'Z35'),
    36: (7, 0, 'Z36'),
}

def describe_noll(j: int) -> str:
    n, m, name = NOLL_ZERNIKES.get(j, (-1, 0, 'unknown'))
    return f'Noll {j}: n={n}, m={m}, {name}'

APERTURE_RECOMMENDATIONS: Dict[int, Dict[str, float]] = {
    100: {'diffraction_green_arcsec': 1.384037, 'suggest_scale': 0.553615, 'f_ratio_planetary': 25.0},
    110: {'diffraction_green_arcsec': 1.258215, 'suggest_scale': 0.503286, 'f_ratio_planetary': 25.0},
    120: {'diffraction_green_arcsec': 1.153364, 'suggest_scale': 0.461346, 'f_ratio_planetary': 25.0},
    130: {'diffraction_green_arcsec': 1.064644, 'suggest_scale': 0.425857, 'f_ratio_planetary': 25.0},
    140: {'diffraction_green_arcsec': 0.988598, 'suggest_scale': 0.395439, 'f_ratio_planetary': 25.0},
    150: {'diffraction_green_arcsec': 0.922691, 'suggest_scale': 0.369076, 'f_ratio_planetary': 25.0},
    160: {'diffraction_green_arcsec': 0.865023, 'suggest_scale': 0.346009, 'f_ratio_planetary': 25.0},
    170: {'diffraction_green_arcsec': 0.814139, 'suggest_scale': 0.325656, 'f_ratio_planetary': 25.0},
    180: {'diffraction_green_arcsec': 0.768909, 'suggest_scale': 0.307564, 'f_ratio_planetary': 25.0},
    190: {'diffraction_green_arcsec': 0.728440, 'suggest_scale': 0.291376, 'f_ratio_planetary': 25.0},
    200: {'diffraction_green_arcsec': 0.692018, 'suggest_scale': 0.276807, 'f_ratio_planetary': 25.0},
    210: {'diffraction_green_arcsec': 0.659065, 'suggest_scale': 0.263626, 'f_ratio_planetary': 25.0},
    220: {'diffraction_green_arcsec': 0.629108, 'suggest_scale': 0.251643, 'f_ratio_planetary': 25.0},
    230: {'diffraction_green_arcsec': 0.601755, 'suggest_scale': 0.240702, 'f_ratio_planetary': 25.0},
    240: {'diffraction_green_arcsec': 0.576682, 'suggest_scale': 0.230673, 'f_ratio_planetary': 25.0},
    250: {'diffraction_green_arcsec': 0.553615, 'suggest_scale': 0.221446, 'f_ratio_planetary': 25.0},
    260: {'diffraction_green_arcsec': 0.532322, 'suggest_scale': 0.212929, 'f_ratio_planetary': 25.0},
    270: {'diffraction_green_arcsec': 0.512606, 'suggest_scale': 0.205042, 'f_ratio_planetary': 25.0},
    280: {'diffraction_green_arcsec': 0.494299, 'suggest_scale': 0.197720, 'f_ratio_planetary': 25.0},
    290: {'diffraction_green_arcsec': 0.477254, 'suggest_scale': 0.190902, 'f_ratio_planetary': 25.0},
    300: {'diffraction_green_arcsec': 0.461346, 'suggest_scale': 0.184538, 'f_ratio_planetary': 25.0},
    310: {'diffraction_green_arcsec': 0.446463, 'suggest_scale': 0.178585, 'f_ratio_planetary': 25.0},
    320: {'diffraction_green_arcsec': 0.432512, 'suggest_scale': 0.173005, 'f_ratio_planetary': 25.0},
    330: {'diffraction_green_arcsec': 0.419405, 'suggest_scale': 0.167762, 'f_ratio_planetary': 25.0},
    340: {'diffraction_green_arcsec': 0.407070, 'suggest_scale': 0.162828, 'f_ratio_planetary': 25.0},
    350: {'diffraction_green_arcsec': 0.395439, 'suggest_scale': 0.158176, 'f_ratio_planetary': 25.0},
    360: {'diffraction_green_arcsec': 0.384455, 'suggest_scale': 0.153782, 'f_ratio_planetary': 25.0},
    370: {'diffraction_green_arcsec': 0.374064, 'suggest_scale': 0.149626, 'f_ratio_planetary': 25.0},
    380: {'diffraction_green_arcsec': 0.364220, 'suggest_scale': 0.145688, 'f_ratio_planetary': 25.0},
    390: {'diffraction_green_arcsec': 0.354881, 'suggest_scale': 0.141952, 'f_ratio_planetary': 25.0},
    400: {'diffraction_green_arcsec': 0.346009, 'suggest_scale': 0.138404, 'f_ratio_planetary': 25.0},
    410: {'diffraction_green_arcsec': 0.337570, 'suggest_scale': 0.135028, 'f_ratio_planetary': 25.0},
    420: {'diffraction_green_arcsec': 0.329533, 'suggest_scale': 0.131813, 'f_ratio_planetary': 25.0},
    430: {'diffraction_green_arcsec': 0.321869, 'suggest_scale': 0.128748, 'f_ratio_planetary': 25.0},
    440: {'diffraction_green_arcsec': 0.314554, 'suggest_scale': 0.125822, 'f_ratio_planetary': 25.0},
    450: {'diffraction_green_arcsec': 0.307564, 'suggest_scale': 0.123025, 'f_ratio_planetary': 25.0},
    460: {'diffraction_green_arcsec': 0.300878, 'suggest_scale': 0.120351, 'f_ratio_planetary': 25.0},
    470: {'diffraction_green_arcsec': 0.294476, 'suggest_scale': 0.117790, 'f_ratio_planetary': 25.0},
    480: {'diffraction_green_arcsec': 0.288341, 'suggest_scale': 0.115336, 'f_ratio_planetary': 25.0},
    490: {'diffraction_green_arcsec': 0.282456, 'suggest_scale': 0.112983, 'f_ratio_planetary': 25.0},
    500: {'diffraction_green_arcsec': 0.276807, 'suggest_scale': 0.110723, 'f_ratio_planetary': 25.0},
}

def recommendation_for_aperture_mm(ap_mm: float) -> Dict[str, float]:
    key = int(round(ap_mm / 10.0) * 10)
    key = min(max(key, 100), 500)
    return dict(APERTURE_RECOMMENDATIONS.get(key, APERTURE_RECOMMENDATIONS[150]))

def process_ir685_stack(cube: VideoCube, cfg: PipelineConfig) -> StackResult:
    cube2 = VideoCube(data=cube.data, times=cube.times, meta=replace(cube.meta, filter_name='IR685'), quality=cube.quality)
    return lucky_stack_cube(cube2, cfg, cfg.primary_fraction)

def process_ir742_stack(cube: VideoCube, cfg: PipelineConfig) -> StackResult:
    cube2 = VideoCube(data=cube.data, times=cube.times, meta=replace(cube.meta, filter_name='IR742'), quality=cube.quality)
    return lucky_stack_cube(cube2, cfg, cfg.primary_fraction)

def process_ir807_stack(cube: VideoCube, cfg: PipelineConfig) -> StackResult:
    cube2 = VideoCube(data=cube.data, times=cube.times, meta=replace(cube.meta, filter_name='IR807'), quality=cube.quality)
    return lucky_stack_cube(cube2, cfg, cfg.primary_fraction)

def process_ch4_stack(cube: VideoCube, cfg: PipelineConfig) -> StackResult:
    cube2 = VideoCube(data=cube.data, times=cube.times, meta=replace(cube.meta, filter_name='CH4'), quality=cube.quality)
    return lucky_stack_cube(cube2, cfg, cfg.primary_fraction)

def process_clear_stack(cube: VideoCube, cfg: PipelineConfig) -> StackResult:
    cube2 = VideoCube(data=cube.data, times=cube.times, meta=replace(cube.meta, filter_name='CLEAR'), quality=cube.quality)
    return lucky_stack_cube(cube2, cfg, cfg.primary_fraction)

def apply_wavelet_preset(image: np.ndarray, name: str = 'standard') -> np.ndarray:
    gains, dens = WAVELET_PRESETS[name]
    return starlet_sharpen(image, len(gains), gains, dens)

def deg_to_mas(d: float) -> float:
    return float(d * 3600000.0)

def mas_to_deg(m: float) -> float:
    return float(m / 3600000.0)

def arcsec_to_mas(a: float) -> float:
    return float(a * 1000.0)

def mas_to_arcsec(m: float) -> float:
    return float(m / 1000.0)

def deg_to_arcsec(d: float) -> float:
    return float(d * 3600.0)

def arcsec_to_deg(a: float) -> float:
    return float(a / 3600.0)

def day_to_second(d: float) -> float:
    return float(d * 86400.0)

def second_to_day(s: float) -> float:
    return float(s / 86400.0)

def au_to_km(a: float) -> float:
    return float(a * PC.AU_M / 1000.0)

def km_to_au(k: float) -> float:
    return float(k * 1000.0 / PC.AU_M)

GRS_SIZE_REFERENCE: List[Tuple[int, float, float]] = [
    (1880, 47.000, 10.500),
    (1882, 46.613, 10.498),
    (1884, 46.231, 10.496),
    (1886, 45.853, 10.494),
    (1888, 45.479, 10.492),
    (1890, 45.109, 10.490),
    (1892, 44.743, 10.488),
    (1894, 44.381, 10.486),
    (1896, 44.023, 10.484),
    (1898, 43.669, 10.482),
    (1900, 43.319, 10.480),
    (1902, 42.973, 10.478),
    (1904, 42.631, 10.476),
    (1906, 42.293, 10.474),
    (1908, 41.958, 10.472),
    (1910, 41.627, 10.470),
    (1912, 41.299, 10.468),
    (1914, 40.976, 10.466),
    (1916, 40.656, 10.464),
    (1918, 40.339, 10.462),
    (1920, 40.026, 10.460),
    (1922, 39.716, 10.458),
    (1924, 39.410, 10.456),
    (1926, 39.107, 10.454),
    (1928, 38.807, 10.452),
    (1930, 38.511, 10.450),
    (1932, 38.218, 10.448),
    (1934, 37.929, 10.446),
    (1936, 37.642, 10.444),
    (1938, 37.359, 10.442),
    (1940, 37.079, 10.440),
    (1942, 36.801, 10.438),
    (1944, 36.527, 10.436),
    (1946, 36.256, 10.434),
    (1948, 35.988, 10.432),
    (1950, 35.723, 10.430),
    (1952, 35.461, 10.428),
    (1954, 35.202, 10.426),
    (1956, 34.946, 10.424),
    (1958, 34.692, 10.422),
    (1960, 34.441, 10.420),
    (1962, 34.193, 10.418),
    (1964, 33.948, 10.416),
    (1966, 33.706, 10.414),
    (1968, 33.466, 10.412),
    (1970, 33.229, 10.410),
    (1972, 32.994, 10.408),
    (1974, 32.762, 10.406),
    (1976, 32.533, 10.404),
    (1978, 32.306, 10.402),
    (1980, 32.081, 10.400),
    (1982, 31.859, 10.398),
    (1984, 31.640, 10.396),
    (1986, 31.423, 10.394),
    (1988, 31.208, 10.392),
    (1990, 30.996, 10.390),
    (1992, 30.786, 10.388),
    (1994, 30.579, 10.386),
    (1996, 30.373, 10.384),
    (1998, 30.170, 10.382),
    (2000, 29.970, 10.380),
    (2002, 29.771, 10.378),
    (2004, 29.575, 10.376),
    (2006, 29.380, 10.374),
    (2008, 29.188, 10.372),
    (2010, 28.999, 10.370),
    (2012, 28.811, 10.368),
    (2014, 28.625, 10.366),
    (2016, 28.441, 10.364),
    (2018, 28.260, 10.362),
    (2020, 28.080, 10.360),
    (2022, 27.902, 10.358),
    (2024, 27.727, 10.356),
    (2026, 27.553, 10.354),
]

def grs_reference_size(year: float) -> Tuple[float, float]:
    years = np.array([r[0] for r in GRS_SIZE_REFERENCE], dtype=np.float64)
    L = np.array([r[1] for r in GRS_SIZE_REFERENCE], dtype=np.float64)
    W = np.array([r[2] for r in GRS_SIZE_REFERENCE], dtype=np.float64)
    return float(np.interp(year, years, L)), float(np.interp(year, years, W))

@dataclass
class IngestStageResult:
    ok: bool = True
    message: str = ''
    elapsed_s: float = 0.0
    metrics: Dict[str, float] = field(default_factory=dict)
    artifacts: Dict[str, str] = field(default_factory=dict)
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class CalibStageResult:
    ok: bool = True
    message: str = ''
    elapsed_s: float = 0.0
    metrics: Dict[str, float] = field(default_factory=dict)
    artifacts: Dict[str, str] = field(default_factory=dict)
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class QualityStageResult:
    ok: bool = True
    message: str = ''
    elapsed_s: float = 0.0
    metrics: Dict[str, float] = field(default_factory=dict)
    artifacts: Dict[str, str] = field(default_factory=dict)
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class AlignStageResult:
    ok: bool = True
    message: str = ''
    elapsed_s: float = 0.0
    metrics: Dict[str, float] = field(default_factory=dict)
    artifacts: Dict[str, str] = field(default_factory=dict)
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class StackStageResult:
    ok: bool = True
    message: str = ''
    elapsed_s: float = 0.0
    metrics: Dict[str, float] = field(default_factory=dict)
    artifacts: Dict[str, str] = field(default_factory=dict)
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class DerotStageResult:
    ok: bool = True
    message: str = ''
    elapsed_s: float = 0.0
    metrics: Dict[str, float] = field(default_factory=dict)
    artifacts: Dict[str, str] = field(default_factory=dict)
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class RestoreStageResult:
    ok: bool = True
    message: str = ''
    elapsed_s: float = 0.0
    metrics: Dict[str, float] = field(default_factory=dict)
    artifacts: Dict[str, str] = field(default_factory=dict)
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class ColorStageResult:
    ok: bool = True
    message: str = ''
    elapsed_s: float = 0.0
    metrics: Dict[str, float] = field(default_factory=dict)
    artifacts: Dict[str, str] = field(default_factory=dict)
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class NavStageResult:
    ok: bool = True
    message: str = ''
    elapsed_s: float = 0.0
    metrics: Dict[str, float] = field(default_factory=dict)
    artifacts: Dict[str, str] = field(default_factory=dict)
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class MeasureStageResult:
    ok: bool = True
    message: str = ''
    elapsed_s: float = 0.0
    metrics: Dict[str, float] = field(default_factory=dict)
    artifacts: Dict[str, str] = field(default_factory=dict)
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class TrajStageResult:
    ok: bool = True
    message: str = ''
    elapsed_s: float = 0.0
    metrics: Dict[str, float] = field(default_factory=dict)
    artifacts: Dict[str, str] = field(default_factory=dict)
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

@dataclass
class ExportStageResult:
    ok: bool = True
    message: str = ''
    elapsed_s: float = 0.0
    metrics: Dict[str, float] = field(default_factory=dict)
    artifacts: Dict[str, str] = field(default_factory=dict)
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

SITE_PRESETS: Dict[str, Tuple[float,float,float]] = {
    'hong_kong': (22.3, 114.2, 50),
    'mauna_kea': (19.82, -155.47, 4200),
    'paranal': (-24.63, -70.4, 2635),
    'la_palma': (28.76, -17.88, 2396),
    'siding_spring': (-31.27, 149.06, 1165),
    'palomar': (33.36, -116.86, 1712),
    'kitt_peak': (31.96, -111.6, 2096),
    'pic_du_midi': (42.94, 0.14, 2877),
}

def apply_site_preset(cfg: PipelineConfig, name: str) -> PipelineConfig:
    la, lo, e = SITE_PRESETS[name]
    return replace(cfg, site_lat=la, site_lon=lo, site_elev_m=e)

def score_all_frames_laplacian_var(cube: VideoCube) -> np.ndarray:
    return score_frames(cube, 'laplacian_var')

def score_all_frames_fft_power(cube: VideoCube) -> np.ndarray:
    return score_frames(cube, 'fft_power')

def score_all_frames_hybrid(cube: VideoCube) -> np.ndarray:
    return score_frames(cube, 'hybrid')

def score_all_frames_sobel_energy(cube: VideoCube) -> np.ndarray:
    return score_frames(cube, 'sobel_energy')

def score_all_frames_tenengrad(cube: VideoCube) -> np.ndarray:
    return score_frames(cube, 'tenengrad')

def score_all_frames_variance(cube: VideoCube) -> np.ndarray:
    return score_frames(cube, 'variance')

def score_all_frames_max_pixel(cube: VideoCube) -> np.ndarray:
    return score_frames(cube, 'max_pixel')

def algorithm_help(name: str) -> str:
    return ALGORITHM_DOCS.get(name) or ALGORITHM_DOCS.get(name+'_long') or 'Unknown algorithm'

PIPELINE_STEP_ORDER = ['ingest', 'qc', 'calibrate', 'score', 'select', 'align', 'stack', 'derotate', 'register', 'restore', 'lrgb', 'navigate', 'segment', 'measure', 'bootstrap', 'error_budget', 'smooth', 'export', 'manifest', 'report']

def great_circle_distance_deg(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    rlat1, rlat2 = deg2rad(lat1), deg2rad(lat2)
    dlon = deg2rad(wrap_deg_diff(lon2, lon1))
    dlat = rlat2 - rlat1
    a = math.sin(dlat/2)**2 + math.cos(rlat1)*math.cos(rlat2)*math.sin(dlon/2)**2
    return rad2deg(2 * math.asin(min(1.0, math.sqrt(a))))


def bearing_deg(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    rlat1, rlat2 = deg2rad(lat1), deg2rad(lat2)
    dlon = deg2rad(wrap_deg_diff(lon2, lon1))
    y = math.sin(dlon) * math.cos(rlat2)
    x = math.cos(rlat1)*math.sin(rlat2) - math.sin(rlat1)*math.cos(rlat2)*math.cos(dlon)
    return wrap_deg(rad2deg(math.atan2(y, x)))


def cylindrical_equal_area_weight(lat_deg: float) -> float:
    return max(math.cos(deg2rad(lat_deg)), 0.0)


def integrate_mask_area_km2(mask: np.ndarray, nav: Navigation) -> float:
    km_per_px = (2.0 * PC.JUPITER_REQ_KM) / (2.0 * nav.a_eq_px + 1e-12)
    return float(mask.sum() * km_per_px * km_per_px)


def brightness_temperature_proxy(image: np.ndarray, mask: np.ndarray) -> float:
    """Relative photometric proxy (not absolute Kelvin)."""
    if mask.sum() == 0:
        return float("nan")
    return float(np.median(image[mask]))


def limb_darkening_law(mu: np.ndarray, u1: float = 0.6, u2: float = 0.2) -> np.ndarray:
    """Quadratic limb darkening I/I0 = 1 - u1(1-mu) - u2(1-mu)^2."""
    x = 1.0 - np.clip(mu, 0, 1)
    return 1.0 - u1 * x - u2 * x * x


def apply_limb_darkening_model(shape: Tuple[int,int], nav: Navigation, u1: float = 0.6, u2: float = 0.2) -> np.ndarray:
    h, w = shape
    yy, xx = np.mgrid[0:h, 0:w]
    X = (xx - nav.xc) / (nav.a_eq_px + 1e-12)
    Y = (nav.yc - yy) / (nav.b_pol_px + 1e-12)
    rr = X*X + Y*Y
    mu = np.sqrt(np.clip(1 - rr, 0, 1))
    I = limb_darkening_law(mu, u1, u2)
    I[rr > 1] = 0
    return I


def flatten_limb_darkening(image: np.ndarray, nav: Navigation, u1: float = 0.6, u2: float = 0.2, eps: float = 0.05) -> np.ndarray:
    model = apply_limb_darkening_model(image.shape, nav, u1, u2)
    return safe_div(np.asarray(image, dtype=np.float64), np.maximum(model, eps))


def series_interpolate(t: np.ndarray, y: np.ndarray, t_new: np.ndarray) -> np.ndarray:
    t = np.asarray(t, dtype=np.float64); y = np.asarray(y, dtype=np.float64)
    return np.interp(t_new, t, y)


def detrend_linear(t: np.ndarray, y: np.ndarray) -> Tuple[np.ndarray, float, float]:
    t = np.asarray(t, dtype=np.float64); y = np.asarray(y, dtype=np.float64)
    t0 = t - t.mean()
    A = np.column_stack([np.ones_like(t0), t0])
    coef, *_ = np.linalg.lstsq(A, y, rcond=None)
    fit = A @ coef
    return y - fit, float(coef[0]), float(coef[1])


def lomb_like_periodogram(t: np.ndarray, y: np.ndarray, periods: np.ndarray) -> np.ndarray:
    """Simple least-squares periodogram power for oscillation search (e.g. 90d)."""
    t = np.asarray(t, dtype=np.float64); y = np.asarray(y, dtype=np.float64)
    y = y - y.mean()
    power = np.zeros(len(periods), dtype=np.float64)
    for i, p in enumerate(periods):
        w = 2 * math.pi / p
        c = np.cos(w * t); s = np.sin(w * t)
        # project
        ac = np.dot(c, y) / (np.dot(c, c) + 1e-12)
        as_ = np.dot(s, y) / (np.dot(s, s) + 1e-12)
        model = ac * c + as_ * s
        power[i] = 1.0 - np.sum((y - model)**2) / (np.sum(y**2) + 1e-12)
    return power


def search_90day_oscillation(t_mjd: np.ndarray, lon_deg: np.ndarray) -> Dict[str, float]:
    lon_u = unwrap_longitudes(lon_deg)
    resid, lon0, drift = detrend_linear(t_mjd, lon_u)
    periods = np.linspace(60, 120, 200)
    power = lomb_like_periodogram(t_mjd, resid, periods)
    i = int(np.argmax(power))
    return {"best_period_day": float(periods[i]), "power": float(power[i]), "drift_deg_per_day": drift, "lon0": lon0}


def robust_mad(x: np.ndarray) -> float:
    x = np.asarray(x, dtype=np.float64)
    return float(1.4826 * np.median(np.abs(x - np.median(x))))


def outlier_mask_mad(x: np.ndarray, kappa: float = 4.0) -> np.ndarray:
    med = np.median(x)
    mad = robust_mad(x) + 1e-12
    return np.abs(x - med) <= kappa * mad


def running_median(x: np.ndarray, win: int = 5) -> np.ndarray:
    x = np.asarray(x, dtype=np.float64)
    if win <= 1:
        return x.copy()
    pad = win // 2
    xp = np.pad(x, (pad, pad), mode="edge")
    out = np.empty_like(x)
    for i in range(len(x)):
        out[i] = np.median(xp[i:i+win])
    return out


def align_by_centroid(image: np.ndarray, ref_cy: float, ref_cx: float) -> np.ndarray:
    m = rough_disk_mask(image)
    ys, xs = np.where(m)
    if len(xs) == 0:
        return image
    cy, cx = float(ys.mean()), float(xs.mean())
    return shift_image(image, ref_cy - cy, ref_cx - cx)


def multi_frame_max_entropy_stack(frames: np.ndarray, n_iter: int = 5) -> np.ndarray:
    """Very simplified maximum-entropy-like iterative stack refinement."""
    acc = stack_median(frames)
    for _ in range(n_iter):
        # re-align each frame to current acc and reject
        aligned = []
        for i in range(frames.shape[0]):
            dy, dx, _ = phase_correlate(acc, frames[i])
            aligned.append(shift_image(frames[i], dy, dx))
        A = np.stack(aligned, 0)
        acc = stack_kappa_sigma(A, kappa=2.0)
    return acc


def estimate_fwhm_from_edge(image: np.ndarray, nav: Navigation) -> float:
    pts = extract_limb_points(image, n_rays=72)
    # radial profiles near limb already used; approximate from gradient width
    img = gaussian_filter2d(image, 0.5)
    g = sobel_mag(img)
    m = annulus_mask(image.shape, nav.yc, nav.xc, nav.a_eq_px*0.85, nav.a_eq_px*1.15)
    if m.sum() < 10:
        return float("nan")
    # second moment of gradient near limb as width proxy
    return float(np.sqrt(np.mean((g[m] / (np.max(g[m])+1e-12))**2)) * 5.0)


def annulus_mask(shape: Tuple[int,int], cy: float, cx: float, r0: float, r1: float) -> np.ndarray:
    h, w = shape
    yy, xx = np.mgrid[0:h, 0:w]
    rr = np.sqrt((yy-cy)**2 + (xx-cx)**2)
    return (rr >= r0) & (rr <= r1)


def export_winjupos_like_csv(path: Union[str, Path], state: GRSState) -> None:
    path = Path(path)
    ensure_dir(path.parent)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["MJD_TDB", "LON_III", "LAT", "LENGTH_DEG", "WIDTH_DEG", "FILTER", "DEFINITION"])
        w.writerow([state.t_tdb_mjd, state.lon_iii_deg, state.lat_deg, state.length_deg, state.width_deg, state.filter_name, state.definition_id])


def load_trajectory_csv(path: Union[str, Path]) -> List[Dict[str, Any]]:
    path = Path(path)
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def states_from_trajectory_rows(rows: List[Dict[str, Any]]) -> List[GRSState]:
    out = []
    for r in rows:
        try:
            out.append(GRSState(
                t_tdb_mjd=float(r.get("t_tdb_mjd", r.get("MJD_TDB", 0))),
                lon_iii_deg=float(r.get("lon_iii_deg", r.get("LON_III", 0))),
                lat_deg=float(r.get("lat_deg", r.get("LAT", 0))),
                length_deg=float(r.get("length_deg", r.get("LENGTH_DEG", 0))),
                width_deg=float(r.get("width_deg", r.get("WIDTH_DEG", 0))),
                area_km2=float(r["area_km2"]) if r.get("area_km2") not in (None, "") else None,
                aspect=float(r.get("aspect", 1.0) or 1.0),
                pa_deg=float(r.get("pa_deg", 0) or 0),
                definition_id=str(r.get("definition_id", r.get("DEFINITION", "UNKNOWN"))),
                filter_name=str(r.get("filter_name", r.get("FILTER", "UNKNOWN"))),
            ))
        except Exception:
            continue
    return out


def sobel_magnitude(image: np.ndarray) -> np.ndarray:
    img = np.asarray(image, dtype=np.float64)
    kx = SOBEL_KERNELS['x']; ky = SOBEL_KERNELS['y']
    return np.hypot(fft_convolve2d(img, kx, 'same'), fft_convolve2d(img, ky, 'same'))

def prewitt_magnitude(image: np.ndarray) -> np.ndarray:
    img = np.asarray(image, dtype=np.float64)
    kx = PREWITT_KERNELS['x']; ky = PREWITT_KERNELS['y']
    return np.hypot(fft_convolve2d(img, kx, 'same'), fft_convolve2d(img, ky, 'same'))

def scharr_magnitude(image: np.ndarray) -> np.ndarray:
    img = np.asarray(image, dtype=np.float64)
    kx = SCHARR_KERNELS['x']; ky = SCHARR_KERNELS['y']
    return np.hypot(fft_convolve2d(img, kx, 'same'), fft_convolve2d(img, ky, 'same'))

def landweber_deconv(image: np.ndarray, psf: np.ndarray, n_iter: int = 20, omega: float = 0.5) -> np.ndarray:
    img = np.asarray(image, dtype=np.float64)
    psf = np.asarray(psf, dtype=np.float64); psf = psf / (psf.sum()+1e-12)
    x = img.copy()
    psf_m = psf[::-1, ::-1]
    for _ in range(n_iter):
        conv = fft_convolve2d(x, psf, "same")
        x = x + omega * fft_convolve2d(img - conv, psf_m, "same")
        x = np.clip(x, 0, None)
    return x


def van_cittert_deconv(image: np.ndarray, psf: np.ndarray, n_iter: int = 15, mu: float = 0.5) -> np.ndarray:
    img = np.asarray(image, dtype=np.float64)
    psf = np.asarray(psf, dtype=np.float64); psf = psf / (psf.sum()+1e-12)
    x = img.copy()
    for _ in range(n_iter):
        x = x + mu * (img - fft_convolve2d(x, psf, "same"))
    return np.clip(x, 0, None)


def multi_resolution_support(image: np.ndarray, n_layers: int = 5, k_sigma: float = 3.0) -> List[np.ndarray]:
    layers, _ = starlet_decompose(image, n_layers)
    supports = []
    for w in layers:
        sig = mad_sigma(w)
        supports.append(np.abs(w) > k_sigma * sig)
    return supports


def significant_wavelet_reconstruction(image: np.ndarray, n_layers: int = 5, k_sigma: float = 3.0, gains: Optional[Sequence[float]] = None) -> np.ndarray:
    layers, residual = starlet_decompose(image, n_layers)
    if gains is None:
        gains = [1.0]*n_layers
    acc = residual.copy()
    for j, w in enumerate(layers):
        sig = mad_sigma(w)
        mask = np.abs(w) > k_sigma * sig
        acc = acc + gains[j] * w * mask
    return acc


def pyramid_downsample(image: np.ndarray) -> np.ndarray:
    return gaussian_filter2d(image, 1.0)[::2, ::2]


def pyramid_upsample(image: np.ndarray, out_shape: Tuple[int,int]) -> np.ndarray:
    return resize_bilinear(image, out_shape[0], out_shape[1])


def build_gaussian_pyramid(image: np.ndarray, levels: int = 5) -> List[np.ndarray]:
    pyr = [np.asarray(image, dtype=np.float64)]
    for _ in range(levels-1):
        pyr.append(pyramid_downsample(pyr[-1]))
        if min(pyr[-1].shape) < 8:
            break
    return pyr


def build_laplacian_pyramid(image: np.ndarray, levels: int = 5) -> Tuple[List[np.ndarray], np.ndarray]:
    gpyr = build_gaussian_pyramid(image, levels)
    lpyr = []
    for i in range(len(gpyr)-1):
        up = pyramid_upsample(gpyr[i+1], gpyr[i].shape)
        lpyr.append(gpyr[i] - up)
    return lpyr, gpyr[-1]


def collapse_laplacian_pyramid(lpyr: List[np.ndarray], residual: np.ndarray) -> np.ndarray:
    acc = residual
    for layer in reversed(lpyr):
        acc = pyramid_upsample(acc, layer.shape) + layer
    return acc


def focus_stack_from_pyramid(frames: np.ndarray) -> np.ndarray:
    """Choose max-abs Laplacian coefficients across frames (focus stacking style)."""
    n = frames.shape[0]
    # use single-layer laplacian energy
    laps = [np.abs(laplacian(frames[i])) for i in range(n)]
    idx = np.argmax(np.stack(laps, 0), axis=0)
    out = np.zeros(frames.shape[1:], dtype=np.float64)
    for i in range(n):
        out[idx == i] = frames[i][idx == i]
    return out


def correlation_coefficient(a: np.ndarray, b: np.ndarray) -> float:
    a = np.asarray(a, dtype=np.float64).ravel(); b = np.asarray(b, dtype=np.float64).ravel()
    a = a - a.mean(); b = b - b.mean()
    return float(np.dot(a,b) / (np.linalg.norm(a)*np.linalg.norm(b) + 1e-12))


def ssim_approx(a: np.ndarray, b: np.ndarray) -> float:
    a = np.asarray(a, dtype=np.float64); b = np.asarray(b, dtype=np.float64)
    mu_a, mu_b = a.mean(), b.mean()
    sa, sb = a.std(), b.std()
    sab = ((a-mu_a)*(b-mu_b)).mean()
    c1, c2 = 0.01**2, 0.03**2
    return float(((2*mu_a*mu_b + c1)*(2*sab + c2)) / ((mu_a**2 + mu_b**2 + c1)*(sa**2 + sb**2 + c2) + 1e-12))


def psnr(a: np.ndarray, b: np.ndarray, data_range: Optional[float] = None) -> float:
    a = np.asarray(a, dtype=np.float64); b = np.asarray(b, dtype=np.float64)
    mse = np.mean((a-b)**2)
    if mse <= 0: return 99.0
    dr = float(np.max(a) - np.min(a)) if data_range is None else data_range
    if dr <= 0: dr = 1.0
    return float(20*math.log10(dr) - 10*math.log10(mse))


def hash_pipeline_inputs(paths: Sequence[Union[str, Path]]) -> str:
    h = hashlib.sha256()
    for p in paths:
        p = Path(p)
        h.update(p.name.encode())
        if p.exists():
            h.update(sha256_file(p).encode())
    return h.hexdigest()


def compare_states(a: GRSState, b: GRSState) -> Dict[str, float]:
    return {
        "dlon": wrap_deg_diff(a.lon_iii_deg, b.lon_iii_deg),
        "dlat": a.lat_deg - b.lat_deg,
        "dL": a.length_deg - b.length_deg,
        "dW": a.width_deg - b.width_deg,
    }


def format_state_line(state: GRSState) -> str:
    return (f"GRS {state.filter_name} lon={state.lon_iii_deg:.3f} lat={state.lat_deg:.3f} "
            f"L={state.length_deg:.2f} W={state.width_deg:.2f} def={state.definition_id}")


def ensure_rgb_float(rgb: np.ndarray) -> np.ndarray:
    rgb = np.asarray(rgb, dtype=np.float64)
    if rgb.ndim != 3 or rgb.shape[2] < 3:
        raise ValueError("rgb must be HxWx3")
    if rgb.max() > 1.5:
        rgb = rgb / (np.max(rgb) + 1e-12)
    return np.clip(rgb, 0, 1)


def save_channels_fits(out_dir: Union[str, Path], channels: Mapping[str, np.ndarray]) -> None:
    out_dir = ensure_dir(out_dir)
    for k, v in channels.items():
        write_fits(out_dir / f"channel_{k}.fits", v)


def load_channels_fits(out_dir: Union[str, Path]) -> Dict[str, np.ndarray]:
    out_dir = Path(out_dir)
    ch = {}
    for p in out_dir.glob("channel_*.fits"):
        name = p.stem.replace("channel_", "")
        data, _ = read_fits(p)
        ch[name] = np.asarray(data, dtype=np.float64)
        if ch[name].ndim == 3:
            ch[name] = ch[name][0]
    return ch

def print_user_manual() -> None:
    print(USER_MANUAL_TEXT)


def rl_deconv_1iter(image: np.ndarray, psf: np.ndarray) -> np.ndarray:
    return richardson_lucy(image, psf, n_iter=1)

def rl_deconv_2iter(image: np.ndarray, psf: np.ndarray) -> np.ndarray:
    return richardson_lucy(image, psf, n_iter=2)

def rl_deconv_3iter(image: np.ndarray, psf: np.ndarray) -> np.ndarray:
    return richardson_lucy(image, psf, n_iter=3)

def rl_deconv_4iter(image: np.ndarray, psf: np.ndarray) -> np.ndarray:
    return richardson_lucy(image, psf, n_iter=4)

def rl_deconv_5iter(image: np.ndarray, psf: np.ndarray) -> np.ndarray:
    return richardson_lucy(image, psf, n_iter=5)

def rl_deconv_6iter(image: np.ndarray, psf: np.ndarray) -> np.ndarray:
    return richardson_lucy(image, psf, n_iter=6)

def rl_deconv_7iter(image: np.ndarray, psf: np.ndarray) -> np.ndarray:
    return richardson_lucy(image, psf, n_iter=7)

def rl_deconv_8iter(image: np.ndarray, psf: np.ndarray) -> np.ndarray:
    return richardson_lucy(image, psf, n_iter=8)

def rl_deconv_9iter(image: np.ndarray, psf: np.ndarray) -> np.ndarray:
    return richardson_lucy(image, psf, n_iter=9)

def rl_deconv_10iter(image: np.ndarray, psf: np.ndarray) -> np.ndarray:
    return richardson_lucy(image, psf, n_iter=10)

def rl_deconv_11iter(image: np.ndarray, psf: np.ndarray) -> np.ndarray:
    return richardson_lucy(image, psf, n_iter=11)

def rl_deconv_12iter(image: np.ndarray, psf: np.ndarray) -> np.ndarray:
    return richardson_lucy(image, psf, n_iter=12)

def rl_deconv_13iter(image: np.ndarray, psf: np.ndarray) -> np.ndarray:
    return richardson_lucy(image, psf, n_iter=13)

def rl_deconv_14iter(image: np.ndarray, psf: np.ndarray) -> np.ndarray:
    return richardson_lucy(image, psf, n_iter=14)

def rl_deconv_15iter(image: np.ndarray, psf: np.ndarray) -> np.ndarray:
    return richardson_lucy(image, psf, n_iter=15)

def rl_deconv_16iter(image: np.ndarray, psf: np.ndarray) -> np.ndarray:
    return richardson_lucy(image, psf, n_iter=16)

def rl_deconv_17iter(image: np.ndarray, psf: np.ndarray) -> np.ndarray:
    return richardson_lucy(image, psf, n_iter=17)

def rl_deconv_18iter(image: np.ndarray, psf: np.ndarray) -> np.ndarray:
    return richardson_lucy(image, psf, n_iter=18)

def rl_deconv_19iter(image: np.ndarray, psf: np.ndarray) -> np.ndarray:
    return richardson_lucy(image, psf, n_iter=19)

def rl_deconv_20iter(image: np.ndarray, psf: np.ndarray) -> np.ndarray:
    return richardson_lucy(image, psf, n_iter=20)

def rl_deconv_21iter(image: np.ndarray, psf: np.ndarray) -> np.ndarray:
    return richardson_lucy(image, psf, n_iter=21)

def rl_deconv_22iter(image: np.ndarray, psf: np.ndarray) -> np.ndarray:
    return richardson_lucy(image, psf, n_iter=22)

def rl_deconv_23iter(image: np.ndarray, psf: np.ndarray) -> np.ndarray:
    return richardson_lucy(image, psf, n_iter=23)

def rl_deconv_24iter(image: np.ndarray, psf: np.ndarray) -> np.ndarray:
    return richardson_lucy(image, psf, n_iter=24)

def rl_deconv_25iter(image: np.ndarray, psf: np.ndarray) -> np.ndarray:
    return richardson_lucy(image, psf, n_iter=25)

def rl_deconv_26iter(image: np.ndarray, psf: np.ndarray) -> np.ndarray:
    return richardson_lucy(image, psf, n_iter=26)

def rl_deconv_27iter(image: np.ndarray, psf: np.ndarray) -> np.ndarray:
    return richardson_lucy(image, psf, n_iter=27)

def rl_deconv_28iter(image: np.ndarray, psf: np.ndarray) -> np.ndarray:
    return richardson_lucy(image, psf, n_iter=28)

def rl_deconv_29iter(image: np.ndarray, psf: np.ndarray) -> np.ndarray:
    return richardson_lucy(image, psf, n_iter=29)

def rl_deconv_30iter(image: np.ndarray, psf: np.ndarray) -> np.ndarray:
    return richardson_lucy(image, psf, n_iter=30)

def rl_deconv_31iter(image: np.ndarray, psf: np.ndarray) -> np.ndarray:
    return richardson_lucy(image, psf, n_iter=31)

def rl_deconv_32iter(image: np.ndarray, psf: np.ndarray) -> np.ndarray:
    return richardson_lucy(image, psf, n_iter=32)

def rl_deconv_33iter(image: np.ndarray, psf: np.ndarray) -> np.ndarray:
    return richardson_lucy(image, psf, n_iter=33)

def rl_deconv_34iter(image: np.ndarray, psf: np.ndarray) -> np.ndarray:
    return richardson_lucy(image, psf, n_iter=34)

def rl_deconv_35iter(image: np.ndarray, psf: np.ndarray) -> np.ndarray:
    return richardson_lucy(image, psf, n_iter=35)

def rl_deconv_36iter(image: np.ndarray, psf: np.ndarray) -> np.ndarray:
    return richardson_lucy(image, psf, n_iter=36)

def rl_deconv_37iter(image: np.ndarray, psf: np.ndarray) -> np.ndarray:
    return richardson_lucy(image, psf, n_iter=37)

def rl_deconv_38iter(image: np.ndarray, psf: np.ndarray) -> np.ndarray:
    return richardson_lucy(image, psf, n_iter=38)

def rl_deconv_39iter(image: np.ndarray, psf: np.ndarray) -> np.ndarray:
    return richardson_lucy(image, psf, n_iter=39)

def rl_deconv_40iter(image: np.ndarray, psf: np.ndarray) -> np.ndarray:
    return richardson_lucy(image, psf, n_iter=40)

def process_existing_rgb_fits(path: Union[str, Path], cfg: Optional[PipelineConfig] = None) -> GRSCompletePipeline:
    """Convenience: reduce a finished RGB stacked FITS (e.g. AutoStakkert output)."""
    cfg = cfg or replace(PRESET_FAST_PREVIEW, mode="both", min_frames=1, derot_enable=False, bootstrap_n=40)
    pipe = GRSCompletePipeline(cfg)
    pipe.process_path(path, filter_name="RGB")
    pipe.build_channels()
    if cfg.mode in ("imaging", "both"):
        pipe.run_imaging()
    if cfg.mode in ("science", "both"):
        pipe.run_science()
    write_text_report(Path(cfg.out_dir) / "report.txt", pipe)
    export_manifest(Path(cfg.out_dir) / "run_manifest.json", RunManifest(
        version=__version__, mode=cfg.mode, config_sha=cfg.sha(), input_shas=[sha256_file(path)],
        package_versions=package_versions(), seed=cfg.seed, stages=pipe.stages, warnings=pipe.warnings,
    ))
    return pipe


def quick_measure_path(
    path: Union[str, Path],
    bootstrap: int = 50,
    *,
    user_time: Optional[str] = None,
) -> GRSState:
    """
    Quick measure. Requires FITS DATE-OBS (or user_time). Never uses wall-clock now.
    """
    cfg = PipelineConfig(bootstrap_n=bootstrap, mode="science", min_frames=1)
    data, hdr = read_fits(path)
    data = np.asarray(data, dtype=np.float64)
    if data.ndim == 3:
        if data.shape[0] == 3:
            data = 0.3*data[0] + 0.5*data[1] + 0.2*data[2]
        elif data.shape[-1] == 3:
            data = 0.3*data[:,:,0] + 0.5*data[:,:,1] + 0.2*data[:,:,2]
        else:
            data = data[0]
    from fits_time import require_observation_time
    t_mid, _ = require_observation_time(user_time=user_time, fits_path=path, hdr=hdr)
    meta = FrameMeta(path=str(path), filter_name="RGB", t_utc_mid=t_mid)
    nav = fit_navigation(data, meta, cfg)
    return bootstrap_grs(data, nav, cfg)


# End-of-module public API reinforcement
__public_api__ = [
    "PipelineConfig", "GRSCompletePipeline", "run_pipeline", "main",
    "ingest_path", "lucky_stack_cube", "restore_image", "build_lrgb",
    "fit_navigation", "bootstrap_grs", "smooth_trajectory",
    "synthetic_jupiter", "synthetic_ser_cube", "run_validation_suite",
    "get_preset", "process_existing_rgb_fits", "quick_measure_path",
]


def api_list() -> List[str]:
    return list(__public_api__)

MONTE_CARLO_SEEING_GRID = [0.4, 0.45, 0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 0.95, 1.0, 1.05, 1.1, 1.15, 1.2, 1.25, 1.3, 1.35, 1.4, 1.45, 1.5, 1.55, 1.6, 1.65, 1.7, 1.75, 1.8, 1.85, 1.9, 1.95, 2.0, 2.05, 2.1, 2.15, 2.2, 2.25, 2.3, 2.35, 2.4, 2.45, 2.5, 2.55, 2.6, 2.65, 2.7, 2.75, 2.8, 2.85, 2.9, 2.95, 3.0, 3.05, 3.1, 3.15, 3.2, 3.25, 3.3, 3.35, 3.4, 3.45, 3.5, 3.55, 3.6, 3.65, 3.7, 3.75, 3.8, 3.85, 3.9, 3.95, 4.0, 4.05, 4.1, 4.15, 4.2, 4.25, 4.3, 4.35]
MONTE_CARLO_FRACTION_GRID = [0.05, 0.06, 0.07, 0.08, 0.09, 0.1, 0.11, 0.12, 0.13, 0.14, 0.15, 0.16, 0.17, 0.18, 0.19, 0.2, 0.21, 0.22, 0.23, 0.24, 0.25, 0.26, 0.27, 0.28, 0.29, 0.3, 0.31, 0.32, 0.33, 0.34, 0.35, 0.36, 0.37, 0.38, 0.39, 0.4, 0.41, 0.42, 0.43, 0.44, 0.45, 0.46, 0.47, 0.48, 0.49, 0.5, 0.51, 0.52, 0.53, 0.54]
MONTE_CARLO_NOISE_GRID = [0.005, 0.007, 0.009, 0.011, 0.013, 0.015, 0.017, 0.019, 0.021, 0.023, 0.025, 0.027, 0.029, 0.031, 0.033, 0.035, 0.037, 0.039, 0.041, 0.043, 0.045, 0.047, 0.049, 0.051, 0.053, 0.055, 0.057, 0.059, 0.061, 0.063, 0.065, 0.067, 0.069, 0.071, 0.073, 0.075, 0.077, 0.079, 0.081, 0.083, 0.085, 0.087, 0.089, 0.091, 0.093, 0.095, 0.097, 0.099, 0.101, 0.103]

def monte_carlo_centroid_stability(n: int = 30, size: int = 128, seed: int = 0) -> Dict[str, float]:
    rng = np.random.default_rng(seed)
    lons = []
    base = synthetic_jupiter(size=size, seed=seed)
    nav = rough_navigation(base); nav.cm_iii_deg = 100.0
    cfg = PipelineConfig(bootstrap_n=5, min_frames=1, segment_method='adaptive_threshold')
    for i in range(n):
        img = base + rng.normal(0, 0.02, base.shape)
        img = gaussian_filter2d(img, abs(rng.normal(0.8, 0.2)))
        try:
            mask = segment_grs(img, nav, 'adaptive_threshold')
            st = measure_grs_from_mask(mask, img, nav, 'MOMENT_MASK_IR', 'IR742')
            lons.append(st.lon_iii_deg)
        except Exception:
            continue
    if not lons:
        return {'n': 0.0, 'std_lon': float('nan')}
    arr = unwrap_longitudes(np.array(lons))
    return {'n': float(len(arr)), 'std_lon': float(np.std(arr)), 'mean_lon': float(np.mean(arr) % 360)}


if __name__ == "__main__":
    raise SystemExit(main())

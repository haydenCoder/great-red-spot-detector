#!/usr/bin/env python3
"""
16 GB RAM budget manager + SSD memmap cache.

Target machine: 16 GB unified RAM. Keep peak working set under ~10 GB so the
OS stays responsive. Large arrays spill to SSD under app/ssd_cache (project disk).
"""
from __future__ import annotations

import gc
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Optional, Tuple

import numpy as np

from verbose_log import CONSOLE

APP_DIR = Path(__file__).resolve().parent
SSD_CACHE = Path(os.environ.get("GRS_SSD_CACHE", str(APP_DIR / "ssd_cache")))
SSD_CACHE.mkdir(parents=True, exist_ok=True)

# Conservative budgets for 16 GB machine
TOTAL_RAM_GB = float(os.environ.get("GRS_RAM_GB", "16"))
MAX_WORKING_GB = min(10.0, TOTAL_RAM_GB * 0.60)
MAX_ARRAY_GB = min(4.0, MAX_WORKING_GB * 0.45)


def bytes_gb(n: int) -> float:
    return n / (1024 ** 3)


def estimate_rgb_gb(w: int, h: int, dtype=np.float32) -> float:
    item = np.dtype(dtype).itemsize
    return bytes_gb(w * h * 3 * item)


def choose_max_resolution(prefer: str = "8K") -> Tuple[str, int, int]:
    """
    Pick largest safe resolution for 16 GB.
    16K float32 RGB ~ 1.5 GB raw + temps → tight.
    8K ~ 0.4 GB → comfortable.
    """
    presets = {
        "1080p": (1920, 1080),
        "4K": (3840, 2160),
        "8K": (7680, 4320),
        "16K": (15360, 8640),
        "16K_square": (16384, 16384),
    }
    order = ["16K_square", "16K", "8K", "4K", "1080p"]
    # start from preferred
    if prefer in order:
        order = order[order.index(prefer) :] + [p for p in order if p not in order[order.index(prefer) :]]
        # actually: try prefer first then step down
        order = [prefer] + [p for p in ["16K", "8K", "4K", "1080p"] if p != prefer]

    for name in order:
        if name not in presets:
            continue
        w, h = presets[name]
        need = estimate_rgb_gb(w, h) * 3.5  # rgb + work + mono + blur temps
        if need <= MAX_WORKING_GB * 0.85:
            CONSOLE.info(f"RAM budget: selecting {name} ({w}x{h}), est peak ~{need:.2f} GB (limit {MAX_WORKING_GB:.1f} GB)")
            return name, w, h
        CONSOLE.warn(f"Skip {name}: est {need:.2f} GB exceeds safe working set")
    return "4K", 3840, 2160


def ssd_temp_path(suffix: str = ".npy") -> Path:
    SSD_CACHE.mkdir(parents=True, exist_ok=True)
    return SSD_CACHE / f"tmp_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}{suffix}"


def memmap_zeros(shape: Tuple[int, ...], dtype=np.float32) -> Tuple[np.ndarray, Path]:
    path = ssd_temp_path(".dat")
    mm = np.memmap(path, dtype=dtype, mode="w+", shape=shape)
    CONSOLE.debug(f"SSD memmap {shape} {dtype} → {path.name}")
    return mm, path


def array_to_ssd(arr: np.ndarray) -> Path:
    path = ssd_temp_path(".npy")
    np.save(path, np.asarray(arr))
    return path


def load_ssd(path: Path) -> np.ndarray:
    return np.load(path, mmap_mode="r")


def free_memory() -> None:
    gc.collect()


def cleanup_ssd_cache(max_age_sec: float = 86400.0) -> int:
    now = time.time()
    n = 0
    for p in SSD_CACHE.glob("tmp_*"):
        try:
            if now - p.stat().st_mtime > max_age_sec:
                p.unlink()
                n += 1
        except Exception:
            pass
    if n:
        CONSOLE.info(f"SSD cache cleaned: {n} files")
    return n


def recommend_mc_iterations(resolution_mp: float) -> int:
    """Fewer MC iters at huge res to stay within RAM/time."""
    if resolution_mp > 100:
        return 20
    if resolution_mp > 30:
        return 40
    if resolution_mp > 8:
        return 80
    return 120

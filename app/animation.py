#!/usr/bin/env python3
"""
animation.py — WinJUPOS-style blink & animation export (GIF).

WinJUPOS users derotate a series, then *blink* through the frames to spot
defects and to show rotation as an animation. This module produces those
animations dependency-free (Pillow GIF):

  - make_gif(frames, out, fps, loop) — from arrays or file paths
  - blink_gif(path_a, path_b, out, interval_s) — classic two-image blink
  - annotate optional UTC stamps (derotation QA needs the frame time burned in)

Frames are normalised per-image or globally ('stretch': 'global' uses one
p2/p98 scale across all frames — the right choice for disk photometry;
'per_frame' fixes brightness flicker at the cost of photometric meaning).
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, Sequence, Union

import numpy as np
from PIL import Image, ImageDraw, ImageFont

FrameLike = Union[np.ndarray, str, Path]


def _load(f: FrameLike) -> np.ndarray:
    if isinstance(f, (str, Path)):
        return np.asarray(Image.open(f), dtype=np.float64) / 255.0
    a = np.asarray(f, dtype=np.float64)
    if a.dtype == np.uint8:
        return a / 255.0
    return a


def _normalise(frames, stretch="global", lo_p=2.0, hi_p=99.5):
    arrs = [_load(f) for f in frames]
    if arrs[0].ndim == 3:
        flat = np.stack([a[..., :3].reshape(-1, 3).mean(axis=1) for a in arrs])
    else:
        flat = np.stack([a.ravel() for a in arrs])
    if stretch == "global":
        lo = float(np.percentile(flat, lo_p))
        hi = float(np.percentile(flat, hi_p))
        los = [lo] * len(arrs)
        his = [hi] * len(arrs)
    else:
        los = [float(np.percentile(f, lo_p)) for f in flat]
        his = [float(np.percentile(f, hi_p)) for f in flat]
    out = []
    for a, lo, hi in zip(arrs, los, his):
        if hi <= lo:
            hi = lo + 1e-6
        out.append(np.clip((a - lo) / (hi - lo), 0.0, 1.0))
    return out


def _to_pil(a: np.ndarray, stamp: Optional[str] = None, scale: int = 1) -> Image.Image:
    if a.ndim == 2:
        img = Image.fromarray((a * 255.0 + 0.5).astype(np.uint8), mode="L").convert("RGB")
    else:
        img = Image.fromarray((a[..., :3] * 255.0 + 0.5).astype(np.uint8), mode="RGB")
    if scale != 1 and scale > 0:
        img = img.resize((img.width * scale, img.height * scale), Image.NEAREST)
    if stamp:
        draw = ImageDraw.Draw(img)
        draw.rectangle([0, 0, min(img.width, 8 + 7 * len(stamp)), 16], fill=(0, 0, 0))
        draw.text((4, 2), stamp, fill=(255, 220, 60))
    return img


def make_gif(
    frames: Sequence[FrameLike],
    out_path: Union[str, Path],
    *,
    fps: float = 4.0,
    loop: int = 0,
    stamps: Optional[Sequence[str]] = None,
    stretch: str = "global",
    scale: int = 1,
) -> Path:
    """Write an animated GIF from arrays or image paths.

    frames: arrays or paths. stamps: optional per-frame burned-in text
    (e.g. UTC). stretch: 'global' (one photometric scale — default) or
    'per_frame'.
    """
    if not frames:
        raise ValueError("no frames")
    out_path = Path(out_path)
    norm = _normalise(frames, stretch=stretch)
    pil = []
    for i, a in enumerate(norm):
        st = stamps[i] if stamps and i < len(stamps) else None
        pil.append(_to_pil(a, stamp=st, scale=max(1, int(scale))))
    dur_ms = int(round(1000.0 / max(0.2, fps)))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    pil[0].save(out_path, save_all=True, append_images=pil[1:], duration=dur_ms,
                loop=loop, optimize=False)
    return out_path


def blink_gif(
    frame_a: FrameLike,
    frame_b: FrameLike,
    out_path: Union[str, Path],
    *,
    interval_s: float = 0.5,
    stamps: Optional[Sequence[str]] = None,
) -> Path:
    """Two-frame blink comparator (before/after derotation QA)."""
    fps = 1.0 / max(0.05, interval_s)
    return make_gif([frame_a, frame_b], out_path, fps=fps, stamps=stamps)


def gif_info(path: Union[str, Path]) -> dict:
    """Small self-check helper: frames, size, duration of a written GIF."""
    img = Image.open(path)
    n = getattr(img, "n_frames", 1)
    durs = []
    for i in range(n):
        img.seek(i)
        durs.append(img.info.get("duration", 0))
    return {"n_frames": n, "size": img.size, "durations_ms": durs,
            "loop": img.info.get("loop")}

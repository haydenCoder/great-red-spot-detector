#!/usr/bin/env python3
"""grs_overlay.py — interactive measurement overlay + annotated export.

WHY THIS EXISTS
===============
The desktop pipeline measures the GRS automatically, but a careful operator
wants to *see* the fitted limb and the GRS ellipse on top of the real pixels,
tweak the semi-major / semi-minor axes by hand, toggle a latitude/longitude
grid, and then export the result as a 16-bit TIFF (for further reduction) or an
annotated PNG (for a report figure). This module provides both:

  * pure, headless geometry (the same orthographic projection the engine uses)
    so the grid/ellipse overlays are testable without a display, and
  * an optional Tkinter canvas widget (`MeasureOverlay`) that the desktop app
    can open without blocking its worker queue (the widget only ever draws on
    the Tk main thread; all the heavy math stays in numpy).

The overlay deliberately reuses `precision_engine.lonlat_to_planet_xyz` /
`planet_xyz_to_px` so a pixel drawn here is the same pixel the measurement
engine measured — one projection, one answer.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np

from precision_engine import (planetographic_to_planetocentric,
                              lonlat_to_planet_xyz, planet_xyz_to_px)

FLAT = 0.06487  # Jupiter flattening (1/15.41); keep in sync with precision_engine.FLAT


# ---------------------------------------------------------------------------
# Overlay geometry (headless)
# ---------------------------------------------------------------------------

@dataclass
class EllipseSpec:
    """A labelled ellipse in image pixels (semi-major a, semi-minor b, PA deg)."""
    cx: float
    cy: float
    a: float
    b: float
    pa_deg: float = 0.0
    color: str = "#00ff66"
    label: str = ""
    width: int = 2

    def to_dict(self) -> Dict[str, Any]:
        return {"cx": self.cx, "cy": self.cy, "a": self.a, "b": self.b,
                "pa_deg": self.pa_deg, "label": self.label}


def ellipse_polyline(spec: EllipseSpec, n: int = 256) -> np.ndarray:
    """Sample an ellipse boundary as an (n, 2) pixel array (for drawing/drag)."""
    th = np.linspace(0.0, 2.0 * math.pi, n, endpoint=True)
    x = spec.cx + spec.a * np.cos(th)
    y = spec.cy + spec.b * np.sin(th)
    if spec.pa_deg:
        pa = math.radians(spec.pa_deg)
        c, s = math.cos(pa), math.sin(pa)
        dx, dy = x - spec.cx, y - spec.cy
        x = spec.cx + dx * c - dy * s
        y = spec.cy + dx * s + dy * c
    return np.column_stack([x, y])


def ellipse_from_lonlat_deg(
    nav: Any,
    lon_iii_deg: float,
    lat_c_deg: float,
    length_deg: float,
    width_deg: float,
    *,
    color: str = "#ff4444",
    label: str = "GRS",
    width_px: int = 2,
) -> EllipseSpec:
    """Build a physically-grounded ellipse for a feature at (lon, lat).

    The semi-major / semi-minor axes are the *true* on-sky extents, computed by
    projecting the feature's zonal (E-W) and meridional (N-S) half-extents
    through the same `lonlat_to_planet_xyz` / `planet_xyz_to_px` projection the
    measurement engine uses — so foreshortening and limb projection are handled
    correctly instead of assuming a constant px-per-degree scale at disk centre.
    ``length_deg``/``width_deg`` are full extents; the returned `a`/`b` are
    semi-axes. `pa_deg` is the image-plane orientation of the zonal axis.
    """
    from precision_engine import lonlat_to_planet_xyz, planet_xyz_to_px

    cm = float(getattr(nav, "cm_iii_deg", 0.0) or 0.0)

    def _px(lon_rel: float, lat: float) -> np.ndarray:
        X, Y, Z = lonlat_to_planet_xyz(lon_rel, lat)
        x, y, _z = planet_xyz_to_px(X, Y, Z, nav)
        return np.asarray([float(x), float(y)])

    c = _px(lon_iii_deg - cm, lat_c_deg)
    v_ew = _px(lon_iii_deg - cm + length_deg / 2.0, lat_c_deg) - \
           _px(lon_iii_deg - cm - length_deg / 2.0, lat_c_deg)
    v_ns = _px(lon_iii_deg - cm, lat_c_deg + width_deg / 2.0) - \
           _px(lon_iii_deg - cm, lat_c_deg - width_deg / 2.0)
    a = float(np.hypot(*v_ew)) / 2.0
    b = float(np.hypot(*v_ns)) / 2.0
    # orientation of the zonal axis in image coordinates (x right, y down)
    pa = math.degrees(math.atan2(v_ew[1], v_ew[0])) if a > 0 else 0.0
    return EllipseSpec(cx=float(c[0]), cy=float(c[1]), a=max(a, 1.0),
                       b=max(b, 1.0), pa_deg=pa, color=color, label=label,
                       width=width_px)


def _nav_or_default(nav: Optional[Any], shape: Sequence[int]) -> Any:
    if nav is not None:
        return nav
    h, w = int(shape[0]), int(shape[1])
    from precision_engine import NavState
    return NavState(xc=(w - 1) / 2.0, yc=(h - 1) / 2.0, a_eq_px=min(w, h) / 2.0)


def lat_lon_grid_polylines(
    nav: Optional[Any],
    shape: Optional[Sequence[int]] = None,
    *,
    lon_step_deg: float = 30.0,
    lat_step_deg: float = 20.0,
    samples: int = 240,
    cm_iii_deg: Optional[float] = None,
) -> Dict[str, List[Dict[str, Any]]]:
    """Build latitude/longitude grid polylines in image pixels.

    Returns ``{"meridians": [...], "parallels": [...]}`` where each element is
    ``{"points": (N,2) array, "label": str, "value_deg": float}``. Meridian
    longitudes are measured relative to the central meridian (or absolute
    System III if ``cm_iii_deg`` is given); parallels use planetographic
    latitude, the convention WinJUPOS draws. Only the visible hemisphere
    (z_los > 0) is emitted — the same limb cut the engine uses.
    """
    if nav is None:
        nav = _nav_or_default(None, shape or (512, 512))
    fl = float(getattr(nav, "flattening", FLAT) or FLAT)
    a = float(nav.a_eq_px)
    if a <= 1.0:
        return {"meridians": [], "parallels": []}

    meridians: List[Dict[str, Any]] = []
    parallels: List[Dict[str, Any]] = []

    lat_c = np.deg2rad(np.linspace(-90.0, 90.0, samples))
    lon_c = np.deg2rad(np.linspace(-90.0, 90.0, samples))

    # meridians: constant longitude offset from CM
    lons = np.arange(-90.0, 90.0 + 1e-9, float(lon_step_deg))
    for lon_rel in lons:
        X, Y, Z = lonlat_to_planet_xyz(np.full_like(lat_c, lon_rel),
                                       np.rad2deg(lat_c), fl)
        xp, yp, z = planet_xyz_to_px(X, Y, Z, nav)
        m = z > 0.0
        if int(m.sum()) < 2:
            continue
        if cm_iii_deg is not None:
            abs_lon = float(cm_iii_deg + lon_rel) % 360.0
            label = f"{abs_lon:.0f}°"
        else:
            label = f"{lon_rel:+.0f}°"
        meridians.append({"points": np.column_stack([xp[m], yp[m]]),
                          "label": label, "value_deg": float(lon_rel),
                          "abs_lon_deg": abs_lon if cm_iii_deg is not None else None})

    # parallels: constant planetographic latitude -> planetocentric
    lats = np.arange(-80.0, 80.0 + 1e-9, float(lat_step_deg))
    for lat_g in lats:
        latc = planetographic_to_planetocentric(lat_g, fl)
        X, Y, Z = lonlat_to_planet_xyz(np.rad2deg(lon_c), latc, fl)
        xp, yp, z = planet_xyz_to_px(X, Y, Z, nav)
        m = z > 0.0
        if int(m.sum()) < 2:
            continue
        parallels.append({"points": np.column_stack([xp[m], yp[m]]),
                          "label": f"{lat_g:+.0f}°", "value_deg": float(lat_g)})

    return {"meridians": meridians, "parallels": parallels}


# ---------------------------------------------------------------------------
# Export (headless): 16-bit TIFF + annotated PNG
# ---------------------------------------------------------------------------

def _to_uint16(arr: np.ndarray, clip: Optional[Tuple[float, float]] = None) -> np.ndarray:
    """Float [0,1] (or any numeric) array -> uint16 for 16-bit export."""
    a = np.asarray(arr, dtype=np.float64)
    lo, hi = clip if clip is not None else (0.0, 1.0)
    a = np.clip(a, lo, hi)
    a = (a - lo) / max(hi - lo, 1e-12)
    return np.rint(a * 65535.0).astype(np.uint16)


def export_16bit_tiff(
    path: str | Path,
    arr: np.ndarray,
    *,
    clip: Optional[Tuple[float, float]] = None,
) -> Path:
    """Write a 16-bit grayscale TIFF (float [0,1] or uint arrays accepted).

    Tries `tifffile` first (full astronomical fidelity), then falls back to
    Pillow's 16-bit TIFF writer. Raises a clear error only if both are missing.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    a = np.asarray(arr)
    if a.dtype != np.uint16:
        a = _to_uint16(a, clip=clip)
    try:
        import tifffile  # type: ignore
        tifffile.imwrite(str(path), a)
        return path
    except Exception:
        pass
    try:
        from PIL import Image
        Image.fromarray(a).save(path)  # uint16 -> PIL mode "I;16" automatically
        return path
    except Exception as e:
        raise RuntimeError(
            "16-bit TIFF export needs tifffile or Pillow; neither wrote the "
            f"file ({e}). Install tifffile or Pillow."
        ) from e


def render_annotated_png(
    path: str | Path,
    arr: np.ndarray,
    *,
    nav: Optional[Any] = None,
    limb: Optional[EllipseSpec] = None,
    grs: Optional[EllipseSpec] = None,
    show_grid: bool = True,
    lon_step_deg: float = 30.0,
    lat_step_deg: float = 20.0,
    cm_iii_deg: Optional[float] = None,
    title: str = "",
    clip: Optional[Tuple[float, float]] = None,
) -> Path:
    """Render an annotated PNG: image + limb ellipse + GRS ellipse + lat/lon grid.

    Pure-PIL, headless, and byte-reproducible given the same inputs. Labels the
    grid in the same planetographic/System-III conventions the engine uses.
    """
    from PIL import Image, ImageDraw

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    a = np.asarray(arr, dtype=np.float64)
    if a.ndim == 3:
        a = 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]
    lo, hi = clip if clip is not None else (0.0, 1.0)
    if lo != 0.0 or hi != 1.0:
        a = np.clip(a, lo, hi)
    a = np.clip(a, 0.0, 1.0)
    u8 = np.rint(a * 255.0).astype(np.uint8)
    rgb = np.stack([u8, u8, u8], axis=-1)
    im = Image.fromarray(rgb, mode="RGB")
    d = ImageDraw.Draw(im)

    nav = _nav_or_default(nav, u8.shape)
    if show_grid:
        grid = lat_lon_grid_polylines(nav, u8.shape, lon_step_deg=lon_step_deg,
                                      lat_step_deg=lat_step_deg, cm_iii_deg=cm_iii_deg)
        for line in grid["meridians"]:
            pts = [(float(x), float(y)) for x, y in line["points"]]
            if len(pts) >= 2:
                d.line(pts, fill=(90, 150, 255), width=1)
        for line in grid["parallels"]:
            pts = [(float(x), float(y)) for x, y in line["points"]]
            if len(pts) >= 2:
                d.line(pts, fill=(90, 150, 255), width=1)

    def _ellipse(spec: EllipseSpec):
        if spec is None or spec.a <= 0 or spec.b <= 0:
            return
        pts = ellipse_polyline(spec, n=256)
        d.line([(float(x), float(y)) for x, y in pts],
               fill=spec.color, width=max(1, int(spec.width)))
        if spec.label:
            d.text((float(spec.cx - spec.a), float(spec.cy - spec.b - 12)),
                   spec.label, fill=spec.color)

    _ellipse(limb)
    _ellipse(grs)

    if title:
        d.text((4, 2), title, fill=(255, 255, 255))

    im.save(path)
    return path


# ---------------------------------------------------------------------------
# Interactive Tk overlay (imported lazily — no Tk required for the rest)
# ---------------------------------------------------------------------------

def _load_tk():
    import tkinter as tk
    from PIL import Image, ImageTk
    return tk, Image, ImageTk


class MeasureOverlay:
    """A Tk canvas for inspecting and tweaking the limb + GRS ellipses.

    Zero-blocking: the widget is constructed and drawn on the calling (main)
    thread; it holds no threads of its own. Open it from the desktop app after
    a job completes, never from inside a worker.

    Interactions:
      * drag the centre handle to move an ellipse,
      * drag the E/W handles to change the semi-major axis a,
      * drag the N/S handles to change the semi-minor axis b,
      * "Grid" toggles the latitude/longitude overlay,
      * "TIFF" / "PNG" export the current view.
    """

    _HANDLE = 6  # px radius of a drag handle

    def __init__(
        self,
        image: np.ndarray,
        *,
        nav: Optional[Any] = None,
        limb: Optional[EllipseSpec] = None,
        grs: Optional[EllipseSpec] = None,
        cm_iii_deg: Optional[float] = None,
        title: str = "Measurement overlay",
        out_dir: Optional[Path] = None,
    ):
        tk, Image, ImageTk = _load_tk()
        self.tk = tk
        self.Image = Image
        self.ImageTk = ImageTk

        a = np.asarray(image, dtype=np.float64)
        if a.ndim == 3:
            a = 0.299 * a[..., 0] + 0.587 * a[..., 1] + 0.114 * a[..., 2]
        a = np.clip(a, 0.0, 1.0)
        self.image = a
        self.nav = _nav_or_default(nav, a.shape)
        self.cm_iii_deg = cm_iii_deg
        self.out_dir = Path(out_dir) if out_dir else Path("outputs") / "overlay"
        self.out_dir.mkdir(parents=True, exist_ok=True)

        self.limb = limb or EllipseSpec(
            cx=self.nav.xc, cy=self.nav.yc, a=self.nav.a_eq_px,
            b=self.nav.a_eq_px * (1.0 - float(getattr(self.nav, "flattening", FLAT))),
            color="#00ff66", label="limb")
        self.grs = grs
        self.show_grid = True
        self.scale = 1.0

        self.root = tk.Toplevel()
        self.root.title(title)
        self.canvas = tk.Canvas(self.root, bg="#000000", highlightthickness=0)
        self.canvas.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        bar = tk.Frame(self.root)
        bar.pack(side=tk.BOTTOM, fill=tk.X)
        self.grid_btn = tk.Button(bar, text="Grid: ON", command=self._toggle_grid)
        self.grid_btn.pack(side=tk.LEFT, padx=4, pady=4)
        tk.Button(bar, text="Export 16-bit TIFF", command=self._export_tiff).pack(
            side=tk.LEFT, padx=4, pady=4)
        tk.Button(bar, text="Export annotated PNG", command=self._export_png).pack(
            side=tk.LEFT, padx=4, pady=4)
        tk.Label(bar, text="drag: centre/EW/NS handles", fg="#888888").pack(
            side=tk.RIGHT, padx=8)

        self._drag_item: Optional[str] = None
        self._drag_which: Optional[str] = None
        self.canvas.bind("<Configure>", lambda e: self._redraw())
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", lambda e: self._end_drag())
        self._redraw()

    # ---- drawing ---------------------------------------------------------
    def _redraw(self):
        c = self.canvas
        c.delete("all")
        h, w = self.image.shape
        cw = max(c.winfo_width(), 16)
        ch = max(c.winfo_height(), 16)
        self.scale = min(cw / w, ch / h)
        dw, dh = int(w * self.scale), int(h * self.scale)
        ox, oy = (cw - dw) // 2, (ch - dh) // 2

        u8 = np.rint(self.image * 255.0).astype(np.uint8)
        pil = self.Image.fromarray(np.stack([u8, u8, u8], axis=-1), mode="RGB")
        pil = pil.resize((max(dw, 1), max(dh, 1)), self.Image.Resampling.LANCZOS)
        self._photo = self.ImageTk.PhotoImage(pil)
        c.create_image(ox + dw / 2, oy + dh / 2, image=self._photo)

        def s(x, y):
            return ox + x * self.scale, oy + y * self.scale

        if self.show_grid:
            grid = lat_lon_grid_polylines(self.nav, self.image.shape,
                                          cm_iii_deg=self.cm_iii_deg)
            for line in grid["meridians"]:
                pts = [tuple(p) for p in line["points"]]
                if len(pts) >= 2:
                    c.create_line(*[coord for p in pts for coord in s(*p)],
                                  fill="#5a96ff", width=1)
            for line in grid["parallels"]:
                pts = [tuple(p) for p in line["points"]]
                if len(pts) >= 2:
                    c.create_line(*[coord for p in pts for coord in s(*p)],
                                  fill="#5a96ff", width=1)
        self._draw_ellipse(self.limb, ox, oy, handles=True)
        if self.grs is not None:
            self._draw_ellipse(self.grs, ox, oy, handles=True)

    def _draw_ellipse(self, spec: EllipseSpec, ox: float, oy: float, handles: bool):
        c = self.canvas

        def s(x, y):
            return ox + x * self.scale, oy + y * self.scale

        pts = ellipse_polyline(spec, n=256)
        coords = [coord for p in pts for coord in s(*p)]
        c.create_line(*coords, fill=spec.color, width=max(1, int(spec.width)))
        tag = f"ell:{spec.label}"
        if handles:
            # centre + 4 cardinal handles (a along PA, b perpendicular)
            pa = math.radians(spec.pa_deg)
            cpa, spa = math.cos(pa), math.sin(pa)
            cx, cy = s(spec.cx, spec.cy)
            c.create_oval(cx - self._HANDLE, cy - self._HANDLE,
                          cx + self._HANDLE, cy + self._HANDLE,
                          fill="#ffffff", outline="#000000", tags=(tag, "centre"))
            for which, dx, dy in (
                ("e", spec.a * cpa, spec.a * spa),
                ("w", -spec.a * cpa, -spec.a * spa),
                ("n", -spec.b * spa, spec.b * cpa),
                ("s", spec.b * spa, -spec.b * cpa),
            ):
                hx, hy = s(spec.cx + dx, spec.cy + dy)
                c.create_oval(hx - self._HANDLE, hy - self._HANDLE,
                              hx + self._HANDLE, hy + self._HANDLE,
                              fill="#ffff00", outline="#000000",
                              tags=(tag, which))

    # ---- interaction -----------------------------------------------------
    def _on_press(self, event):
        c = self.canvas
        item = c.find_withtag("current")
        if not item:
            return
        tags = c.gettags(item[0])
        which = next((t for t in tags if t in ("centre", "e", "w", "n", "s")), None)
        if which is None:
            return
        label = next((t[4:] for t in tags if t.startswith("ell:")), None)
        if label is None:
            return
        self._drag_item = label
        self._drag_which = which
        self._last_xy = (event.x, event.y)

    def _on_drag(self, event):
        if not self._drag_item:
            return
        spec = self.limb if self._drag_item == self.limb.label else self.grs
        if spec is None:
            return
        dx = (event.x - self._last_xy[0]) / max(self.scale, 1e-6)
        dy = (event.y - self._last_xy[1]) / max(self.scale, 1e-6)
        self._last_xy = (event.x, event.y)
        pa = math.radians(spec.pa_deg)
        cpa, spa = math.cos(pa), math.sin(pa)
        # deltas expressed in the ellipse's own frame
        de = dx * cpa - dy * spa
        dn = -dx * spa - dy * cpa
        w = self._drag_which
        if w == "centre":
            spec.cx += dx
            spec.cy += dy
        elif w in ("e", "w"):
            sign = 1.0 if w == "e" else -1.0
            spec.a = max(2.0, spec.a + sign * de)
        elif w in ("n", "s"):
            sign = 1.0 if w == "n" else -1.0
            spec.b = max(2.0, spec.b + sign * dn)
        self._redraw()

    def _end_drag(self):
        self._drag_item = None
        self._drag_which = None

    # ---- actions ---------------------------------------------------------
    def _toggle_grid(self):
        self.show_grid = not self.show_grid
        self.grid_btn.config(text=f"Grid: {'ON' if self.show_grid else 'OFF'}")
        self._redraw()

    def _export_tiff(self):
        path = self.out_dir / "overlay_16bit.tiff"
        export_16bit_tiff(path, self.image)
        self._status(f"saved {path}")

    def _export_png(self):
        path = self.out_dir / "overlay_annotated.png"
        render_annotated_png(
            path, self.image, nav=self.nav, limb=self.limb, grs=self.grs,
            show_grid=self.show_grid, cm_iii_deg=self.cm_iii_deg)
        self._status(f"saved {path}")

    def _status(self, msg: str):
        try:
            self.root.title(f"{self.root.title()} — {msg}")
        except Exception:
            pass


def open_overlay(*args, **kwargs) -> Optional[MeasureOverlay]:
    """Open a MeasureOverlay, or return None with a clear error when Tk is
    unavailable (e.g. a headless CI box). Never raises from a worker thread."""
    try:
        return MeasureOverlay(*args, **kwargs)
    except Exception as e:  # noqa: BLE001 - surfaced to caller, not swallowed
        import sys
        print(f"overlay unavailable: {e}", file=sys.stderr)
        return None

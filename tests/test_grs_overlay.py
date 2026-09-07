"""Tests for app/grs_overlay.py — headless overlay geometry + export."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from precision_engine import NavState  # noqa: E402
from grs_overlay import (  # noqa: E402
    EllipseSpec, ellipse_from_lonlat_deg, ellipse_polyline,
    export_16bit_tiff, lat_lon_grid_polylines, render_annotated_png)


def _disk(n=256, xc=128.0, yc=128.0, a=96.0):
    yy, xx = np.mgrid[0:n, 0:n]
    r = np.sqrt((xx - xc) ** 2 + (yy - yc) ** 2) / a
    return np.clip(1.0 - 0.2 * r, 0, 1) * (r <= 1.0)


class TestEllipse:
    def test_polyline_shape_and_radius(self):
        spec = EllipseSpec(cx=100, cy=90, a=50, b=30, pa_deg=0.0)
        pts = ellipse_polyline(spec, n=360)
        assert pts.shape == (360, 2)
        r = np.hypot(pts[:, 0] - spec.cx, pts[:, 1] - spec.cy)
        assert r.max() == pytest.approx(spec.a, abs=0.5)
        assert r.min() == pytest.approx(spec.b, abs=0.5)

    def test_polyline_respects_pa(self):
        spec = EllipseSpec(cx=100, cy=90, a=50, b=30, pa_deg=90.0)
        pts = ellipse_polyline(spec)
        # after 90 deg rotation the long axis is vertical
        xs = pts[:, 0] - spec.cx
        ys = pts[:, 1] - spec.cy
        assert np.max(np.abs(xs)) == pytest.approx(spec.b, abs=0.6)
        assert np.max(np.abs(ys)) == pytest.approx(spec.a, abs=0.6)


class TestEllipseFromLonLat:
    def _nav(self, **kw):
        d = dict(xc=128.0, yc=128.0, a_eq_px=96.0, cm_iii_deg=0.0)
        d.update(kw)
        return NavState(**d)

    def test_center_matches_direct_projection(self):
        from precision_engine import lonlat_to_planet_xyz, planet_xyz_to_px
        nav = self._nav(cm_iii_deg=40.0)
        lon, lat = 65.0, -22.0
        spec = ellipse_from_lonlat_deg(nav, lon, lat, 10.0, 6.0)
        X, Y, Z = lonlat_to_planet_xyz(lon - nav.cm_iii_deg, lat)
        gx, gy, _ = planet_xyz_to_px(X, Y, Z, nav)
        assert spec.cx == pytest.approx(float(gx), abs=1e-6)
        assert spec.cy == pytest.approx(float(gy), abs=1e-6)

    def test_disk_centre_small_angle_scale(self):
        # at the disk centre, a zonal half-extent of 5 deg spans a_eq_px*sin(5 deg)
        nav = self._nav(cm_iii_deg=0.0, sub_lat_deg=0.0, north_pa_deg=0.0)
        spec = ellipse_from_lonlat_deg(nav, 0.0, 0.0, 10.0, 6.0)
        assert spec.a == pytest.approx(nav.a_eq_px * np.sin(np.deg2rad(5.0)), rel=1e-3)
        assert spec.b == pytest.approx(nav.a_eq_px * np.sin(np.deg2rad(3.0)), rel=1e-3)

    def test_foreshortening_near_limb(self):
        # a feature at lon_rel ~60 deg is compressed in longitude vs disk centre
        nav = self._nav(cm_iii_deg=0.0, sub_lat_deg=0.0, north_pa_deg=0.0)
        centre = ellipse_from_lonlat_deg(nav, 0.0, 0.0, 10.0, 6.0)
        limb = ellipse_from_lonlat_deg(nav, 60.0, 0.0, 10.0, 6.0)
        assert limb.a < centre.a * 0.8
        # both semi-axes remain finite and positive
        assert limb.a > 0 and limb.b > 0

    def test_pa_of_zonal_axis(self):
        nav = self._nav(cm_iii_deg=0.0, sub_lat_deg=0.0, north_pa_deg=0.0)
        spec = ellipse_from_lonlat_deg(nav, 0.0, 0.0, 10.0, 6.0)
        assert spec.pa_deg == pytest.approx(0.0, abs=1e-6)


class TestGrid:
    def test_grid_counts(self):
        nav = NavState(xc=128, yc=128, a_eq_px=96.0, cm_iii_deg=0.0)
        g = lat_lon_grid_polylines(nav, (256, 256), lon_step_deg=30, lat_step_deg=20)
        assert len(g["meridians"]) == 7   # -90..+90 step 30
        assert len(g["parallels"]) == 9   # -80..+80 step 20

    def test_grid_points_inside_disk(self):
        nav = NavState(xc=128, yc=128, a_eq_px=96.0, cm_iii_deg=45.0,
                       sub_lat_deg=-2.0, north_pa_deg=15.0)
        g = lat_lon_grid_polylines(nav, (256, 256))
        for kind in ("meridians", "parallels"):
            for line in g[kind]:
                pts = line["points"]
                rr = np.hypot(pts[:, 0] - nav.xc, pts[:, 1] - nav.yc)
                assert rr.max() <= nav.a_eq_px * 1.02

    def test_meridian_labels_use_cm(self):
        nav = NavState(xc=128, yc=128, a_eq_px=96.0, cm_iii_deg=91.44)
        g = lat_lon_grid_polylines(nav, (256, 256), cm_iii_deg=91.44)
        labels = [m["label"] for m in g["meridians"]]
        assert any(lbl == "91°" for lbl in labels)

    def test_meridian_labels_wrap_at_360(self):
        nav = NavState(xc=128, yc=128, a_eq_px=96.0, cm_iii_deg=350.0)
        g = lat_lon_grid_polylines(nav, (256, 256), cm_iii_deg=350.0)
        labels = [m["label"] for m in g["meridians"]]
        assert "20°" in labels      # 350 + 30 = 380 -> 20
        assert all(lbl not in ("380°", "410°") for lbl in labels)

    def test_empty_for_degenerate_nav(self):
        nav = NavState(xc=128, yc=128, a_eq_px=0.5)
        g = lat_lon_grid_polylines(nav, (256, 256))
        assert g == {"meridians": [], "parallels": []}


class TestExport:
    def test_16bit_tiff(self, tmp_path):
        p = tmp_path / "s.tiff"
        out = export_16bit_tiff(p, _disk())
        assert out == p and p.exists()
        from PIL import Image
        arr = np.asarray(Image.open(p))
        assert arr.dtype == np.uint16
        assert arr.max() == 65535

    def test_annotated_png_has_overlay_colours(self, tmp_path):
        nav = NavState(xc=128, yc=128, a_eq_px=96.0, cm_iii_deg=91.44,
                       sub_lat_deg=-2.0, north_pa_deg=10.0)
        limb = EllipseSpec(cx=nav.xc, cy=nav.yc, a=nav.a_eq_px, b=nav.b_pol_px,
                           color="#00ff66", label="limb")
        grs = EllipseSpec(cx=140, cy=150, a=24, b=15, color="#ff4444", label="GRS")
        p = tmp_path / "a.png"
        render_annotated_png(p, _disk(), nav=nav, limb=limb, grs=grs,
                             cm_iii_deg=91.44, title="t")
        from PIL import Image
        rgb = np.asarray(Image.open(p).convert("RGB"))
        r, g, b = rgb[..., 0].astype(int), rgb[..., 1].astype(int), rgb[..., 2].astype(int)
        assert ((r < 120) & (g > 150) & (b > 60)).sum() > 0   # green limb
        assert (b > r + 60).sum() > 0                        # blue grid (b >> r)
        assert ((r > 150) & (g < 120) & (b < 120)).sum() > 0  # red GRS

    def test_no_grid_flag(self, tmp_path):
        nav = NavState(xc=128, yc=128, a_eq_px=96.0, cm_iii_deg=0.0)
        p = tmp_path / "ng.png"
        render_annotated_png(p, _disk(), nav=nav, show_grid=False)
        from PIL import Image
        rgb = np.asarray(Image.open(p).convert("RGB"))
        r, b = rgb[..., 0].astype(int), rgb[..., 2].astype(int)
        # no blue grid pixels remain (grid is the only strongly-blue colour)
        assert (b > r + 60).sum() == 0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

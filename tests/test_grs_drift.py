#!/usr/bin/env python3
"""Tests for app/grs_drift.py — multi-epoch GRS System II drift science.

Planted truth in every test: synthetic longitude series with known drift
rates, curvature, noise and outliers; the module must recover the plant
or loudly refuse — never silently invent.
"""
from __future__ import annotations

import csv
import math
import os
import sys
from datetime import datetime, timedelta

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from planet_models import JUPITER
from grs_drift import (DriftPoint, fit_drift, points_from_jupos_csv,
                       predict, render_drift_png, export_drift_csv,
                       zonal_velocity_mps, drift_report_text)
from jupos_io import JUPOS_FIELDS


def _series(n=18, span_days=120.0, rate_dpd=-0.014, lon0=225.0,
            noise=0.7, seed=4, quad=0.0, outliers=()):
    rng = np.random.default_rng(seed)
    t0 = datetime(2026, 3, 1, 22, 0, 0)
    pts = []
    for i, td in enumerate(np.linspace(0, span_days, n)):
        lon = (lon0 + rate_dpd * td + quad * td * td
               + float(rng.normal(0, noise))) % 360.0
        if i in outliers:
            lon = (lon + 6.0) % 360.0
        pts.append(DriftPoint(t_utc=t0 + timedelta(days=float(td)),
                              lon_ii_deg=lon, sigma_deg=noise,
                              source="synth", lat_deg=-20.0))
    return pts


class TestFit:
    def test_rate_recovered(self):
        pts = _series(rate_dpd=-0.42 / 30.0)     # -0.42 deg/30d
        fit = fit_drift(pts)
        assert fit.rate_deg_per_30d == pytest.approx(-0.42, abs=0.10)
        assert fit.sigma_rate_deg_per_day > 0
        assert fit.preferred_model == "linear"
        assert not fit.unwrap_warning

    def test_wrapped_series_recovered(self):
        # strong drift across the 0/360 seam: unwrap must handle it
        pts = _series(rate_dpd=-1.8 / 30.0, lon0=5.0)
        fit = fit_drift(pts)
        assert fit.rate_deg_per_30d == pytest.approx(-1.8, abs=0.15)

    def test_outliers_clipped(self):
        pts = _series(rate_dpd=-0.30 / 30.0, outliers=(4, 11))
        fit = fit_drift(pts)
        assert set(fit.clipped) == {4, 11}
        assert fit.rate_deg_per_30d == pytest.approx(-0.30, abs=0.10)
        assert fit.n_used == fit.n_total - 2

    def test_quadratic_demanded_when_planted(self):
        pts = _series(n=26, span_days=150.0, rate_dpd=-0.01,
                      quad=0.00012, noise=0.35)
        fit = fit_drift(pts)
        assert fit.preferred_model == "quadratic"
        assert fit.quadratic["curvature_deg_per_day2"] == pytest.approx(
            0.00024, abs=0.00008)
        assert fit.quadratic["f_stat"] > 4.0

    def test_quadratic_not_claimed_on_linear(self):
        pts = _series(rate_dpd=-0.02, noise=0.5)
        fit = fit_drift(pts)
        assert fit.preferred_model == "linear"
        assert fit.quadratic is not None  # control still computed

    def test_too_few_epochs_refused(self):
        with pytest.raises(ValueError):
            fit_drift(_series(n=2))


class TestPhysics:
    def test_zonal_velocity_sign_and_value(self):
        pts = _series(rate_dpd=-0.42 / 30.0)
        fit = fit_drift(pts)
        z = zonal_velocity_mps(JUPITER, fit)
        assert z["lat_deg"] == pytest.approx(-20.0)
        # hand: u = rate * (pi/180) * R_par(-20) / 86400
        k = (JUPITER.surface_parallel_radius_m(-20.0) * math.pi / 180.0
             / 86400.0)
        assert z["u_mps"] == pytest.approx(fit.rate_deg_per_day * k, rel=1e-9)
        # negative drift at -20 deg => retrograde (westward) vs System II
        assert z["u_mps"] < 0

    def test_prediction_cone(self):
        pts = _series(rate_dpd=-0.5 / 30.0)
        fit = fit_drift(pts)
        t_fut = pts[-1].t_utc + timedelta(days=30)
        p = predict(fit, t_fut)
        true_continuation = (225.0 - 0.5 / 30.0 * (120 + 30)) % 360.0
        # predicted within 4 sigma of the true walk continuation
        assert abs(((p["lon_ii_deg"] - true_continuation + 180) % 360) - 180) \
            < 4 * p["sigma_deg"]
        # cone widens with horizon
        p_far = predict(fit, pts[-1].t_utc + timedelta(days=90))
        assert p_far["sigma_deg"] > p["sigma_deg"]


class TestIOAndProducts:
    def _write_jupos(self, path, pts):
        with open(path, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=JUPOS_FIELDS)
            w.writeheader()
            for p in pts:
                w.writerow({
                    "Object": "GRS", "Date": p.t_utc.strftime("%Y-%m-%d"),
                    "Time": p.t_utc.strftime("%H:%M"),
                    "L_II": f"{p.lon_ii_deg:.3f}", "Lat": "-20.1",
                    "Observer": "synth", "Method": "template",
                })
            # one non-GRS row + one broken row, must be skipped
            w.writerow({"Object": "Oval BA", "Date": "2026-05-01",
                        "Time": "10:00", "L_II": "12.0"})
            w.writerow({"Object": "GRS", "Date": "garbage", "Time": "",
                        "L_II": "999"})

    def test_jupos_roundtrip(self, tmp_path):
        pts = _series(n=8, rate_dpd=-0.35 / 30.0)
        p = tmp_path / "m.csv"
        self._write_jupos(str(p), pts)
        got = points_from_jupos_csv(str(p))
        assert len(got) == len(pts)
        for a, b in zip(pts, got):
            assert a.lon_ii_deg == pytest.approx(b.lon_ii_deg, abs=1e-3)

    def test_png_and_csv_products(self, tmp_path):
        pts = _series(rate_dpd=-0.4 / 30.0, outliers=(7,))
        fit = fit_drift(pts)
        png = render_drift_png(pts, fit, str(tmp_path / "drift.png"))
        assert os.path.getsize(png) > 5000
        out = export_drift_csv(pts, fit, str(tmp_path / "drift.csv"))
        rows = [r for r in csv.reader(open(out)) if r and not r[0].startswith("#")]
        hdr = rows[0]
        assert hdr[:3] == ["t_utc", "lon_ii_deg", "sigma_deg"]
        assert len(rows) - 1 == len(pts)
        clipped = [i for i, r in enumerate(rows[1:])
                   if r[hdr.index("clipped")] == "1"]
        assert clipped == [7]

    def test_report_text(self):
        pts = _series(rate_dpd=-0.42 / 30.0)
        fit = fit_drift(pts)
        txt = drift_report_text(fit, planet=JUPITER)
        assert "GRS DRIFT FIT" in txt
        assert "deg/30d" in txt
        assert "m/s" in txt

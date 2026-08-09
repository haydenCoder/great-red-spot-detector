#!/usr/bin/env python3
"""Tests for app/wind_analysis.py — cloud-tracking wind profile science.

Physics-pinned cases:
  * uniform angular-rate offset (a System-III error) must be recovered as
    delta_omega, with the right period-correction sign, and the
    shape discriminator must prefer the angular model;
  * uniform m/s advection must be recovered by the m/s fit, and the shape
    discriminator must prefer advection;
  * a planted unmodelled jet must be detected at the right latitude with
    the right sign and significance;
  * bins without evidence are skipped everywhere — never zero-filled.
"""
from __future__ import annotations

import csv
import math
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from planet_models import JUPITER
from wind_analysis import (detect_jets, export_profile_csv,
                           fit_system_iii_offset, fit_uniform_wind_offset,
                           render_profile_png, summarize_profile,
                           wind_report_text)

N_BINS = 13


def _report(resid_mps, std=5.0, skip=()):
    centres = ((np.arange(N_BINS) + 0.5) * (90.0 / N_BINS)).astype(float)
    res, stds, rates = [], [], []
    for i, c in enumerate(centres):
        if i in skip:
            res.append(None); stds.append(None); rates.append(None)
            continue
        r = float(resid_mps(c)) if callable(resid_mps) else float(resid_mps)
        res.append(r)
        stds.append(float(std))
        om = JUPITER.cloud_tracking_rate_deg_per_s(float(c))
        k = (math.pi / 180.0) * JUPITER.surface_parallel_radius_m(float(c))
        rates.append(om + r / k)
    return {
        "bins_abs_lat_deg": [float(c) for c in centres],
        "measured_rate_deg_per_s": rates,
        "measured_rate_std_deg_per_s": [1e-7 if i not in skip else None
                                        for i in range(N_BINS)],
        "model_rate_deg_per_s": [
            float(JUPITER.cloud_tracking_rate_deg_per_s(float(c)))
            for c in centres],
        "wind_residual_mps_vs_model": res,
        "wind_residual_std_mps": stds,
        "n_evidence_tracks": [0 if i in skip else 20 for i in range(N_BINS)],
        "n_evidence_frames": [0 if i in skip else 4 for i in range(N_BINS)],
    }


class TestOffsetFits:
    def test_uniform_mps_recovered(self):
        rep = _report(25.0)
        fit = fit_uniform_wind_offset(rep)
        assert fit["u0_mps"] == pytest.approx(25.0, abs=2.0)
        assert fit["u0_std_mps"] > 0
        # shape discrimination must prefer advection
        s = summarize_profile(JUPITER, rep)
        assert "advection" in s["offset_shape_preference"]

    def test_uniform_angular_offset_recovered_as_system_iii(self):
        # plant a true System-III error: u(phi) = d_omega * R_par(phi)
        d_om = 0.8  # deg/day
        def plant(c):
            return (d_om * (math.pi / 180.0) / 86400.0
                    * JUPITER.surface_parallel_radius_m(float(c)))
        rep = _report(plant, std=4.0)
        fit_a = fit_system_iii_offset(JUPITER, rep)
        assert fit_a["d_omega_deg_per_day"] == pytest.approx(d_om, abs=0.15)
        # positive offset (super-rotation) => SHORTER period
        assert fit_a["implied_period_correction_s"] < 0
        # hand-check the period correction: -P * d_om / omega3
        omega3 = JUPITER.rotation_rate_deg_per_s * 86400.0
        assert fit_a["implied_period_correction_s"] == pytest.approx(
            -JUPITER.rotation_period_s * d_om / omega3, rel=1e-6)
        s = summarize_profile(JUPITER, rep)
        assert "System-III" in s["offset_shape_preference"]

    def test_too_few_bins_returns_none(self):
        rep = _report(25.0, skip=tuple(range(N_BINS - 1)))  # only 1 bin left
        assert fit_uniform_wind_offset(rep) is None
        assert fit_system_iii_offset(JUPITER, rep) is None


class TestJets:
    def test_planted_jet_detected(self):
        centre_lat = 38.7
        def plant(c):
            return 42.0 * math.exp(-0.5 * ((c - centre_lat) / 3.0) ** 2)
        rep = _report(plant, std=4.0)
        jets = detect_jets(rep, k_sigma=3.0)
        assert len(jets) == 1
        j = jets[0]
        assert abs(j.abs_lat_deg - centre_lat) < 90.0 / N_BINS
        assert j.direction == "prograde"
        assert j.u_mps > 30.0
        assert j.significance_sigma >= 3.0
        assert j.fwhm_deg > 0

    def test_retrograde_jet_and_gap(self):
        centre_lat = 52.0
        def plant(c):
            return -35.0 * math.exp(-0.5 * ((c - centre_lat) / 2.6) ** 2)
        # knock out a bin next to the jet: jets must not span evidence gaps
        skip_bin = int(round(centre_lat / 90.0 * N_BINS - 0.5)) - 1
        rep = _report(plant, std=4.0, skip=(skip_bin,))
        jets = detect_jets(rep, k_sigma=3.0)
        assert len(jets) == 0  # strict local extremum impossible across gap

    def test_no_jets_when_flat(self):
        rep = _report(2.0, std=5.0)
        assert detect_jets(rep, k_sigma=3.0) == []


class TestProducts:
    def test_csv_empty_cells_for_missing_bins(self, tmp_path):
        rep = _report(10.0, std=5.0, skip=(0, 5))
        out = tmp_path / "prof.csv"
        export_profile_csv(rep, str(out), summary=summarize_profile(JUPITER, rep))
        rows = [r for r in csv.reader(open(out)) if r and not r[0].startswith("#")]
        hdr, data = rows[0], rows[1:]
        assert hdr[0] == "abs_lat_deg"
        assert len(data) == N_BINS
        # skipped bins have EMPTY cells, not zeros
        assert data[0][hdr.index("resid_mps")] == ""
        assert data[5][hdr.index("resid_mps")] == ""
        assert data[1][hdr.index("resid_mps")] != ""

    def test_png_written(self, tmp_path):
        rep = _report(lambda c: 30.0 * math.exp(-0.5 * ((c - 30) / 4.0) ** 2),
                      std=5.0)
        s = summarize_profile(JUPITER, rep)
        out = tmp_path / "wind.png"
        from wind_analysis import detect_jets as _dj
        render_profile_png(rep, str(out), jets=_dj(rep))
        assert out.exists() and out.stat().st_size > 5000

    def test_text_report_runs(self):
        rep = _report(18.0, std=5.0)
        txt = wind_report_text(JUPITER, rep)
        assert "ZONAL WIND ANALYSIS" in txt
        assert "advection" in txt

    def test_summary_never_invents_bins(self):
        rep = _report(10.0, skip=(0, 1, 2, 3))
        s = summarize_profile(JUPITER, rep)
        assert s["n_bins_with_evidence"] == N_BINS - 4
        assert s["n_bins_total"] == N_BINS

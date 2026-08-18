#!/usr/bin/env python3
"""Tests for app/session_planner.py — physics-derived session planning.

The math is pinned against the planet model directly (the module must be
a faithful inversion of Planet.lon_drift_px, not a parallel opinion), and
the ephemeris composition runs against the real transits backend.
"""
from __future__ import annotations

import datetime as dt
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

from planet_models import JUPITER
from session_planner import (filter_window_plan, max_span_derotated_s,
                             max_span_s, plan_text, render_plan_png,
                             session_plan, smear_px, smear_table)


class TestSmearMath:
    def test_smear_is_lon_drift(self):
        for la in (0, 20, 45):
            v = smear_px(JUPITER, la, 100.0, 300.0)
            assert v == pytest.approx(
                abs(JUPITER.lon_drift_px(la, 300.0, 100.0)), rel=1e-12)
            assert v > 0

    def test_max_span_exact_inversion(self):
        # span such that smear == budget must invert smear_px exactly
        for la, budget in ((0.0, 1.0), (20.0, 0.5), (45.0, 2.0)):
            s = max_span_s(JUPITER, la, 120.0, budget)
            assert smear_px(JUPITER, la, 120.0, s) == pytest.approx(
                budget, rel=1e-9)
            # hand value
            exp = budget / (JUPITER.cloud_tracking_rate_deg_per_s(la)
                            * JUPITER.px_per_deg_lon(la, 120.0))
            assert s == pytest.approx(exp, rel=1e-12)

    def test_span_monotonic_in_budget_and_lat(self):
        a = 100.0
        assert max_span_s(JUPITER, 20, a, 2.0) > max_span_s(JUPITER, 20, a, 1.0)
        assert max_span_s(JUPITER, 45, a, 1.0) > max_span_s(JUPITER, 0, a, 1.0)

    def test_derotated_span_far_longer_and_consistent(self):
        for la in (0.0, 20.0):
            raw = max_span_s(JUPITER, la, 100.0, 1.0)
            der = max_span_derotated_s(JUPITER, la, 100.0, 1.0,
                                       wind_uncertainty_mps=30.0)
            assert der > 5 * raw
            # hand value with the shared parallel-radius convention
            r_par = JUPITER.surface_parallel_radius_m(la)
            ppd = JUPITER.px_per_deg_lon(la, 100.0)
            px_per_s = (30.0 / r_par) * (180.0 / math.pi) * ppd
            assert der == pytest.approx(1.0 / px_per_s, rel=1e-12)

    def test_smear_table_rows(self):
        rows = smear_table(JUPITER, 100.0, 1.0)
        assert len(rows) == 6
        assert rows[0]["abs_lat_deg"] == 0.0
        for r in rows:
            assert r["max_span_raw_s"] > 0
            assert r["max_span_derotated_s"] > r["max_span_raw_s"]
            assert r["px_per_deg"] > 0


class TestFilterPlan:
    def test_gaps_consistent_with_smear(self):
        fp = filter_window_plan(JUPITER, 100.0, -20.0, 1.0)
        # the direct limit must satisfy the smear budget at GRS latitude
        assert smear_px(JUPITER, -20.0, 100.0,
                        fp.max_gap_direct_s) == pytest.approx(1.0, rel=1e-9)
        assert fp.max_gap_polish_s > fp.max_gap_direct_s
        assert fp.recommended_gap_s <= fp.max_gap_polish_s
        assert fp.drift_px_per_60s == pytest.approx(
            smear_px(JUPITER, -20.0, 100.0, 60.0), rel=1e-12)

    def test_small_scale_forbids_direct(self):
        fp = filter_window_plan(JUPITER, 300.0, -20.0, 1.0)
        # 300 px/Req is a serious scope: direct composite window ~seconds
        assert fp.max_gap_direct_s < 90.0
        assert any("direct composite" in n for n in fp.notes)


class TestSessionPlan:
    def test_plan_with_ephemeris(self):
        start = dt.datetime(2026, 8, 1, 18, 0, 0)
        plan = session_plan(start, 12.0, planet=JUPITER, a_eq_px=100.0)
        assert plan["smear_table"] is not None
        assert plan["filter_plan"] is not None
        assert plan["night"] is not None
        txt = plan_text(plan)
        assert "SESSION PLAN" in txt
        assert "GRS transits" in txt

    def test_plan_without_scale_is_ephemeris_only(self):
        start = dt.datetime(2026, 8, 1, 18, 0, 0)
        plan = session_plan(start, 6.0, planet=JUPITER, a_eq_px=0.0)
        assert plan["smear_table"] is None
        assert plan["filter_plan"] is None
        # never reports per-px numbers without a scale
        txt = plan_text(plan)
        assert "smear budget" not in txt

    def test_plan_png(self, tmp_path):
        start = dt.datetime(2026, 8, 1, 18, 0, 0)
        plan = session_plan(start, 8.0, planet=JUPITER, a_eq_px=120.0)
        out = render_plan_png(plan, str(tmp_path / "plan.png"))
        assert os.path.getsize(out) > 3000

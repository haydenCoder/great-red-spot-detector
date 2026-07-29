"""
Real-ephemeris GRS accuracy suite -- online-sourced truth at scale.

Companion to tests/test_resolution_seeing_100.py. That suite sweeps image
QUALITY on synthetic pixels. This one fixes the TRUTH to the real record: every
frame is rendered at a REAL observation epoch (2020-2026) with the GRS planted
at the REAL online-derived System III longitude for that epoch (the
JUPOS/Hubble drift model in app/grs_ephemeris_truth.py) and at the literature
latitude (-22.4 deg planetographic), observed at the GRS transit time and placed
at a spread of on-disk longitudes. The measurement is then scored against that
planted truth.

Why synthetic pixels but real truth: binary downloads are blocked in this
sandbox and no public UTC-tagged amateur Jupiter dataset exists, so real photos
are unavailable here. The agreed substitute keeps the PIXELS synthetic but makes
the TRUTH real -- the absolute longitude a frame is scored against is the
published GRS longitude for its epoch, not an invented number.

Guarantee (tiered, as agreed):
  * clear / mild data (small + large): within 0.5 deg lon AND lat -- every frame.
  * all data (incl. blurry / very-blurry): within 1.0 deg lon AND lat -- every frame.

Ground truth for scoring is the planted position (estimator recovery); the
planted longitude vs the drift model is reported as plant fidelity (<= ~0.5 deg,
the synthetic mid-exposure CM jitter), and latitude vs the literature mean is
reported separately.

The heavy work is cached in runs/real_ephemeris_campaign.jsonl (gitignored).
    python tools/real_ephemeris_campaign.py --n-dates 40        # build
    pytest tests/test_real_ephemeris_truth.py -m slow            # campaign
    pytest tests/test_real_ephemeris_truth.py -m "not slow"      # drift-model checks
"""
from __future__ import annotations

import math
import os
import statistics as st
import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
TOOLS = ROOT / "tools"
for _p in (APP, TOOLS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import grs_ephemeris_truth as geph  # noqa: E402
from real_ephemeris_campaign import build_matrix, build_results  # noqa: E402

N_DATES = 40
MATRIX = build_matrix(N_DATES)  # 40 dates x 6 tiers = 240 cases


# ---------------------------------------------------------------------------
# Fast: drift-model unit checks (no rendering). pytest -m "not slow".
# ---------------------------------------------------------------------------
class TestDriftModel:
    """The online-sourced GRS longitude model must reproduce its cited anchors."""

    def test_anchor_2023_01_13_matches_hubble(self):
        # Tollefson+2024 (Hubble GO17275): 350.5 deg W on 2023-01-13.
        assert abs(geph.grs_longitude_iii_w("2023-01-13") - 350.5) < 0.05

    def test_anchor_2023_09_09_matches_hubble(self):
        # Tollefson+2024: 64.4 deg W on 2023-09-09.
        assert abs(geph.grs_longitude_iii_w("2023-09-09") - 64.4) < 0.2

    def test_drift_is_westward_and_in_cited_rate_band(self):
        # ~0.31 deg/day (Simon+2018: 0.30-0.36). Over 365 days the longitude
        # must advance (wrap) by ~113 deg, i.e. well away from the start.
        from precision_engine import wrap_diff
        d0 = geph.grs_longitude_iii_w("2024-01-01")
        d1 = geph.grs_longitude_iii_w("2025-01-01")
        moved = wrap_diff(d1, d0) % 360.0
        assert 100.0 < moved < 130.0, f"yearly drift {moved:.1f} deg outside ~0.31 deg/day"

    def test_longitude_is_always_in_valid_range(self):
        for y in range(2015, 2027):
            v = geph.grs_longitude_iii_w(f"{y}-06-01")
            assert 0.0 <= v < 360.0

    def test_literature_latitude_constants(self):
        assert geph.GRS_LAT_PLANETOGRAPHIC_LIT == -22.4
        # planetocentric equivalent ~ -19.82
        assert -20.3 < geph.GRS_LAT_PLANETOCENTRIC_LIT < -19.4

    def test_sources_are_cited(self):
        s = geph.sources()
        assert "10.3847/PSJ/ad71d1" in s["longitude_anchor"]["doi"]
        assert "10.3847/1538-3881/aaae01" in s["drift_rate"]["cross_check"]
        assert s["latitude_planetographic"]["value"] == -22.4

    def test_transit_puts_grs_on_the_meridian(self):
        import datetime as dt
        tr = geph.grs_transit_time(dt.date(2026, 1, 9))
        assert abs(geph.grs_lon_rel_deg(tr)) < 1.0


# ---------------------------------------------------------------------------
# Fast: matrix well-formedness.
# ---------------------------------------------------------------------------
class TestRealEphemerisMatrix:
    def test_240_cases(self):
        assert len(MATRIX) == 240

    def test_dates_are_real_and_span_2020_2026(self):
        years = {int(c["date"][:4]) for c in MATRIX}
        assert min(years) <= 2020 and max(years) >= 2026

    def test_every_case_has_a_tiered_limit(self):
        for c in MATRIX:
            if c["clarity"] in ("clear", "mild"):
                assert c["limit_deg"] == 0.5
            else:
                assert c["limit_deg"] == 1.0

    def test_quality_and_resolution_coverage(self):
        tiers = {c["tier"] for c in MATRIX}
        assert tiers == {"clear_s", "mild_s", "blurry_s", "vblurry_s", "clear_l", "blurry_l"}
        sizes = {c["size"] for c in MATRIX}
        assert sizes == {"small", "large"}


# ---------------------------------------------------------------------------
# Slow: the 240-case campaign, scored against the online drift-model truth.
# ---------------------------------------------------------------------------
@pytest.mark.slow
class TestRealEphemerisCampaign:
    @classmethod
    def setup_class(cls):
        cls.rows = build_results(N_DATES)
        cls.ok = [r for r in cls.rows if r.get("ok")]
        cls.by_case = {r["case_id"]: r for r in cls.rows}

    def test_all_240_cases_complete_without_crash(self):
        failed = [(r["case_id"], r.get("error")) for r in self.rows if not r.get("ok")]
        assert failed == [], f"crashed on {len(failed)} cases: {failed}"

    @pytest.mark.parametrize("case", MATRIX, ids=[c["case_id"] for c in MATRIX])
    def test_case_meets_tiered_degree_gate(self, case):
        """clear/mild < 0.5 deg; blurry/vblurry < 1.0 deg -- lon AND lat."""
        r = self.by_case[case["case_id"]]
        lim = case["limit_deg"]
        assert r["abs_dlon"] <= lim and r["abs_dlat"] <= lim, (
            f"{case['case_id']} ({case['resolution']}, {case['clarity']}, "
            f"seeing={case['seeing_arcsec']:.2f}\", lon_rel={r['lon_rel_achieved']:+.0f}): "
            f"|dlon|={r['abs_dlon']:.3f} |dlat|={r['abs_dlat']:.3f} > {lim} deg "
            f"(drift_lon={r['drift_lon']:.1f} planted={r['planted_lon']:.1f} "
            f"meas={r['lon_meas']:.1f}, method={r['method']})"
        )

    def test_every_clear_mild_frame_is_sub_half_degree(self):
        """The 0.5 deg preference holds on ALL good-data frames."""
        good = [r for r in self.ok if r["clarity"] in ("clear", "mild")]
        worst_lon = max(r["abs_dlon"] for r in good)
        worst_lat = max(r["abs_dlat"] for r in good)
        assert worst_lon < 0.5, f"clear/mild worst lon {worst_lon:.3f} deg"
        assert worst_lat < 0.5, f"clear/mild worst lat {worst_lat:.3f} deg"

    def test_every_frame_is_sub_one_degree(self):
        """The 1.0 deg guarantee holds on ALL 240 frames (incl. very-blurry)."""
        worst = max(max(r["abs_dlon"], r["abs_dlat"]) for r in self.ok)
        assert worst < 1.0, f"worst residual {worst:.3f} deg exceeds the 1 deg floor"

    def test_overall_within_half_degree_rate(self):
        n = len(self.ok)
        within = sum(1 for r in self.ok if r["abs_dlon"] <= 0.5 and r["abs_dlat"] <= 0.5)
        print(f"\n[realeph] within 0.5 deg: {within}/{n} = {within/n*100:.1f}%")
        assert within / n >= 0.90

    def test_no_systematic_longitude_bias_vs_online_truth(self):
        """Mean signed longitude residual vs the online drift model ~ 0."""
        dlons = [r["dlon_est"] for r in self.ok]
        mean = st.fmean(dlons)
        spread = st.pstdev(dlons)
        assert abs(mean) < max(0.15, 2.0 * spread), (
            f"systematic lon bias {mean:+.4f} deg vs scatter {spread:.4f} deg"
        )

    def test_plant_fidelity_honours_online_model(self):
        """Planted longitude must match the drift model within the ~0.5 deg
        synthetic mid-exposure CM jitter (a realistic timing uncertainty the
        tool handles, since plant and measurement share the same CM)."""
        worst = max(r["abs_dlon_vs_model"] for r in self.ok)
        print(f"\n[realeph] plant-vs-model lon fidelity worst: {worst:.3f} deg")
        assert worst < 0.6

    def test_latitude_agrees_with_literature(self):
        """Measured planetocentric latitude vs the literature mean (-22.4 deg
        planetographic ~= -19.82 planetocentric). Real GRS latitude is only
        known to ~0.3 deg and the barycentre definition adds ~0.24 deg, so this
        is a loose 'right place' check, not the sub-0.5 gate."""
        meds = [r["abs_dlat_vs_lit"] for r in self.ok]
        print(f"\n[realeph] |lat| vs literature: median {st.median(meds):.3f} deg")
        assert st.median(meds) < 0.5

    def test_drift_longitudes_span_the_full_circle(self):
        """The planted longitudes must cover [0, 360) -- confirms the campaign
        exercises the absolute-longitude path at many real GRS positions, not a
        narrow window."""
        lons = [r["drift_lon"] for r in self.ok]
        assert min(lons) < 30.0 and max(lons) > 330.0, "drift longitudes do not wrap fully"

    def test_per_tier_table(self):
        print("\n[realeph] per-tier estimator error (deg), scored vs planted truth:")
        print(f"  {'tier':<12}{'n':>4}{'lon_med':>9}{'lon_p90':>9}{'lon_max':>9}"
              f"{'lat_med':>9}{'lat_max':>9}{'<=gate':>8}")
        for tier in ("clear_s", "mild_s", "blurry_s", "vblurry_s", "clear_l", "blurry_l"):
            tr = [r for r in self.ok if r["tier"] == tier]

            def pct(a, p):
                a = sorted(a)
                k = (len(a) - 1) * p / 100.0
                f, c = int(k), min(int(k) + 1, len(a) - 1)
                return a[f] * (c - k) + a[c] * (k - f) if f != c else a[f]

            dl = [r["abs_dlon"] for r in tr]
            db = [r["abs_dlat"] for r in tr]
            lim = tr[0]["limit_deg"]
            w = sum(1 for r in tr if r["abs_dlon"] <= lim and r["abs_dlat"] <= lim)
            print(f"  {tier:<12}{len(tr):>4}{pct(dl,50):>9.3f}{pct(dl,90):>9.3f}"
                  f"{max(dl):>9.3f}{pct(db,50):>9.3f}{max(db):>9.3f}{w/len(tr)*100:>7.0f}%")


if __name__ == "__main__":
    rows = build_results(N_DATES)
    ok = [r for r in rows if r.get("ok")]
    print(f"\n[realeph] {len(ok)}/{len(rows)} complete")
    if len(ok) == len(rows):
        w5 = sum(1 for r in ok if r["abs_dlon"] <= 0.5 and r["abs_dlat"] <= 0.5)
        w1 = sum(1 for r in ok if r["abs_dlon"] <= 1.0 and r["abs_dlat"] <= 1.0)
        print(f"[realeph] within 0.5 deg: {w5}/{len(ok)} = {w5/len(ok)*100:.1f}%")
        print(f"[realeph] within 1.0 deg: {w1}/{len(ok)} = {w1/len(ok)*100:.1f}%")

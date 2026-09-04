"""Tests for app/system_ii.py — Jupiter System I/II/III frame rotation.

The rotation elements are the IAU/IAG WGCCRE values (Seidelmann et al. 2002;
Archinal et al. 2011/2018), so these tests pin the *constants and their
algebra* rather than re-deriving planetary rotation. The one published-data
check asserts our System III -> System II conversion lands within the known
real-GRS drift scatter of the JUPOS / Sky & Telescope anchor, which documents
(not hides) the difference between an exact frame rotation and the GRS's own
non-linear mean drift.
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

import system_ii as s2  # noqa: E402


class TestConstants:
    def test_prime_meridian_rates(self):
        t0 = "2026-01-01 00:00:00"
        t1 = "2026-01-11 00:00:00"  # +10 days
        d_iii = s2.prime_meridian_deg(t1, "III") - s2.prime_meridian_deg(t0, "III")
        d_ii = s2.prime_meridian_deg(t1, "II") - s2.prime_meridian_deg(t0, "II")
        d_i = s2.prime_meridian_deg(t1, "I") - s2.prime_meridian_deg(t0, "I")
        assert d_iii == pytest.approx(870.536 * 10.0, rel=1e-9)
        assert d_ii == pytest.approx(870.270 * 10.0, rel=1e-9)
        assert d_i == pytest.approx(877.900 * 10.0, rel=1e-9)

    def test_offset_at_j2000(self):
        # at J2000 (d=0): W_II - W_III = 43.3 - 284.95 = -241.65 deg
        off = s2.system_ii_minus_system_iii_deg("2000-01-01 12:00:00")
        assert off == pytest.approx(-241.65, abs=0.01)

    def test_offset_rate_is_rate_difference(self):
        t0 = "2020-01-01 00:00:00"
        t1 = "2021-01-01 00:00:00"
        doff = s2.system_ii_minus_system_iii_deg(t1) - s2.system_ii_minus_system_iii_deg(t0)
        # ~366 days * (870.270 - 870.536)
        days = (s2.parse_time(t1) - s2.parse_time(t0)).total_seconds() / 86400.0
        assert doff == pytest.approx((870.270 - 870.536) * days, rel=1e-6)


class TestConversions:
    def test_roundtrip_iii_to_ii(self):
        t = "2026-07-14 12:00:00"
        for lon in (0.0, 45.0, 180.0, 359.999, 123.456):
            ii = s2.system_iii_to_system_ii(lon, t)
            back = s2.system_ii_to_system_iii(ii, t)
            assert back == pytest.approx(lon % 360.0, abs=1e-9)

    def test_lon_ii_minus_lon_iii_is_offset(self):
        t = "2026-07-14 12:00:00"
        lon_iii = 120.0
        lon_ii = s2.system_iii_to_system_ii(lon_iii, t)
        delta = ((lon_ii - lon_iii + 180.0) % 360.0) - 180.0
        off = ((s2.system_ii_minus_system_iii_deg(t) + 180.0) % 360.0) - 180.0
        assert delta == pytest.approx(off, abs=1e-9)

    def test_cm_mapping_matches_feature_mapping(self):
        """The CM and a feature shift by the SAME offset, so their mutual
        separation is preserved between the two systems."""
        t = "2026-06-01 00:00:00"
        cm_iii, feat_iii = 91.44, 13.35
        cm_ii = s2.cm_ii_from_cm_iii(cm_iii, t)
        feat_ii = s2.system_iii_to_system_ii(feat_iii, t)
        assert (cm_ii - feat_ii) % 360.0 == pytest.approx(
            (cm_iii - feat_iii) % 360.0, abs=1e-9)


class TestPublishedAnchor:
    def test_published_grs_system_ii_anchor(self):
        """Converting the repo's published-mean GRS System III (13.35 deg W on
        2026-06-01) must land near the JUPOS/Sky & Telescope anchor of 91 deg
        System II — within the known ~6 deg real-drift scatter of the linear
        mean model (the frame rotation itself is exact)."""
        t = "2026-06-01 00:00:00"
        lon_ii = s2.system_iii_to_system_ii(13.35, t)
        assert lon_ii == pytest.approx(85.46, abs=0.05)   # exact formula value
        assert abs(((lon_ii - 91.0 + 180.0) % 360.0) - 180.0) < 7.0

    def test_published_grs_system_ii_anchor_2021(self):
        t = "2021-07-01 00:00:00"
        lon_ii = s2.system_iii_to_system_ii(176.59, t)
        assert abs(((lon_ii - 2.0 + 180.0) % 360.0) - 180.0) < 7.0


class TestDerive:
    def test_derive_system_ii_shape(self):
        c = s2.derive_system_ii(100.0, "2026-07-14 12:00:00", lon_iii_deg=120.0,
                                source="spice_auto")
        assert c.cm_iii_deg == pytest.approx(100.0)
        assert c.cm_ii_deg == pytest.approx(
            s2.system_iii_to_system_ii(100.0, "2026-07-14 12:00:00"))
        assert c.lon_ii_deg == pytest.approx(
            s2.system_iii_to_system_ii(120.0, "2026-07-14 12:00:00"))
        assert c.source == "spice_auto"
        d = c.to_dict()
        assert "cm_ii_deg" in d and "lon_ii_deg" in d

    def test_derive_without_lon_leaves_none(self):
        c = s2.derive_system_ii(100.0, "2026-07-14 12:00:00")
        assert c.lon_iii_deg is None
        assert c.lon_ii_deg is None

    def test_system_i_included(self):
        t = "2026-07-14 12:00:00"
        c = s2.derive_system_ii(100.0, t)
        delta = ((c.cm_i_deg - c.cm_iii_deg + 180.0) % 360.0) - 180.0
        off = ((s2.system_i_minus_system_iii_deg(t) + 180.0) % 360.0) - 180.0
        assert delta == pytest.approx(off, abs=1e-9)
        assert "CM I" in s2.system_ii_report_text(c)

    def test_report_text(self):
        c = s2.derive_system_ii(100.0, "2026-07-14 12:00:00", lon_iii_deg=120.0)
        txt = s2.system_ii_report_text(c)
        assert "CM II" in txt and "GRS lon II" in txt

    def test_bad_time_raises(self):
        with pytest.raises(ValueError):
            s2.parse_time("not a time")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

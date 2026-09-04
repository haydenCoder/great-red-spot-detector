"""Tests for app/fits_meta.py — FITS header & metadata extraction."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

from fits_meta import extract_fits_meta, meta_report_text, _parse_angle, _parse_aperture_m  # noqa: E402

try:
    import astropy.io.fits as fits
    _HAS_ASTROPY = True
except Exception:
    _HAS_ASTROPY = False


def _write_fits(tmp_path, **cards):
    hdr = fits.Header()
    for k, v in cards.items():
        hdr[k] = v
    p = tmp_path / "sample.fits"
    fits.writeto(str(p), np.zeros((8, 8), dtype=np.float32), hdr, overwrite=True)
    return p


@pytest.mark.skipif(not _HAS_ASTROPY, reason="astropy not installed")
class TestExtractFitsMeta:
    def test_full_header(self, tmp_path):
        p = _write_fits(
            tmp_path,
            **{"DATE-OBS": "2026-01-10T15:39:26", "EXPTIME": 0.03,
               "TELESCOP": "C14", "APERTURE": 0.35, "FILTER": "Red",
               "OBJECT": "Jupiter", "OBJCTRA": "05 30 12.5",
               "OBJCTDEC": "+22 10 30"},
        )
        m = extract_fits_meta(path=p)
        assert m.exposure_time_s == pytest.approx(0.03)
        assert m.telescope == "C14"
        assert m.aperture_m == pytest.approx(0.35)
        assert m.filter == "Red"
        assert m.target_name == "Jupiter"
        # OBJCTRA "05 30 12.5" is sexagesimal HOURS -> 5h30m12.5s = 82.55 deg
        assert m.target_ra_deg == pytest.approx((5.0 + 30 / 60 + 12.5 / 3600) * 15.0, abs=1e-3)
        assert m.target_dec_deg == pytest.approx(22.0 + 10 / 60 + 30 / 3600, abs=1e-4)
        # DATE-OBS + EXPTIME/2 mid-exposure
        assert m.mid_time_utc is not None
        assert m.mid_time_utc.startswith("2026-01-10T15:39:26")

    def test_aperture_mm_normalised(self, tmp_path):
        p = _write_fits(tmp_path, **{"APERTURE": 350.0})
        m = extract_fits_meta(path=p)
        assert m.aperture_m == pytest.approx(0.35)

    def test_missing_fields_are_none_not_fabricated(self, tmp_path):
        p = _write_fits(tmp_path, **{"SIMPLE": True})
        m = extract_fits_meta(path=p)
        assert m.exposure_time_s is None
        assert m.telescope is None
        assert m.aperture_m is None
        assert m.filter is None
        assert m.target_ra_deg is None
        assert any("not found" in n for n in m.notes)

    def test_objctra_hours_vs_ra_degrees(self, tmp_path):
        # OBJCTRA "05:30:12" is hours (82.55 deg); a bare `RA` is decimal degrees
        p_hours = _write_fits(tmp_path, **{"OBJCTRA": "05:30:12"})
        assert extract_fits_meta(path=p_hours).target_ra_deg == pytest.approx(82.55, abs=1e-2)
        p_deg = _write_fits(tmp_path, **{"RA": "82.55"})
        assert extract_fits_meta(path=p_deg).target_ra_deg == pytest.approx(82.55)

    def test_plate_scale_from_pixel_and_focal(self, tmp_path):
        p = _write_fits(tmp_path, **{"XPIXSZ": 3.75, "YPIXSZ": 3.75,
                                     "FOCALLEN": 2350.0})
        m = extract_fits_meta(path=p)
        assert m.pixel_size_um == pytest.approx(3.75)
        assert m.focal_length_mm == pytest.approx(2350.0)
        # 206265 * 3.75e-6 / 2.35 rad -> arcsec/px
        expect = 206264.80624709636 * 3.75 / (2350.0 * 1000.0)
        assert m.plate_scale_arcsec_per_px == pytest.approx(expect, rel=1e-9)
        assert m.plate_scale_arcsec_per_px == pytest.approx(0.329, abs=0.01)

    def test_plate_scale_absent_when_incomplete(self, tmp_path):
        p = _write_fits(tmp_path, **{"XPIXSZ": 3.75})  # no focal length
        m = extract_fits_meta(path=p)
        assert m.pixel_size_um == pytest.approx(3.75)
        assert m.plate_scale_arcsec_per_px is None

    def test_capture_keywords(self, tmp_path):
        p = _write_fits(tmp_path, **{"GAIN": 40, "CCD-TEMP": 23.7,
                                     "ROWORDER": "TOP-DOWN",
                                     "EQUINOX": 2000.0,
                                     "OBSERVER": "Ada", "FRAMETYP": "Light"})
        m = extract_fits_meta(path=p)
        assert m.gain == pytest.approx(40.0)
        assert m.gain_str == "40"
        assert m.ccd_temp_c == pytest.approx(23.7)
        assert m.row_order == "TOP-DOWN"
        assert m.equinox == pytest.approx(2000.0)
        assert m.observer == "Ada"
        assert m.frame_type == "Light"

    def test_bscale_bzero_via_header_mapping(self):
        # astropy drops BSCALE/BZERO when writing unscaled float data, so test
        # those through a raw header mapping (the astropy-header path).
        m = extract_fits_meta(header={"BSCALE": 1.0, "BZERO": 32768.0})
        assert m.bscale == pytest.approx(1.0)
        assert m.bzero == pytest.approx(32768.0)

    def test_sharpcap_style_header(self, tmp_path):
        """A realistic SharpCap planetary header (from the SharpCap forum)."""
        cards = {
            "OBJECT": "Jupiter", "GAIN": 40, "FILTER": "Red",
            "OBJCTRA": "18 28 51.000", "OBJCTDEC": "+00 00 00.000",
            "RA": 277.213894941816, "DATE-OBS": "2021-10-01T17:55:17.3886948",
            "CCD-TEMP": 23.7, "FRAMETYP": "Light", "XPIXSZ": 3.75,
            "YPIXSZ": 3.75, "EXPTIME": 0.05, "ROWORDER": "TOP-DOWN",
            "INSTRUME": "ZWO ASI120MM-S", "BSCALE": 1,
        }
        p = _write_fits(tmp_path, **cards)
        m = extract_fits_meta(path=p)
        assert m.instrument == "ZWO ASI120MM-S"
        assert m.target_ra_deg == pytest.approx(277.213, abs=1e-2)  # OBJCTRA hours
        assert m.exposure_time_s == pytest.approx(0.05)
        assert m.mid_time_utc is not None and m.mid_time_utc.startswith("2021-10-01T17:55:17")
        assert m.row_order == "TOP-DOWN"

    def test_not_a_fits(self, tmp_path):
        p = tmp_path / "image.png"
        p.write_bytes(b"\x89PNG")
        m = extract_fits_meta(path=p)
        assert m.mid_time_utc is None
        assert any("not a FITS" in n for n in m.notes)

    def test_header_mapping_accepted(self, tmp_path):
        p = _write_fits(tmp_path, **{"EXPTIME": 0.05})
        m1 = extract_fits_meta(path=p)
        hdr = fits.getheader(str(p))
        m2 = extract_fits_meta(header=hdr)
        assert m2.exposure_time_s == m1.exposure_time_s

    def test_report_text(self, tmp_path):
        p = _write_fits(tmp_path, **{"EXPTIME": 0.03, "TELESCOP": "C14"})
        txt = meta_report_text(extract_fits_meta(path=p))
        assert "exposure" in txt and "0.03" in txt and "C14" in txt
        # provenance is documented, not hidden
        assert "exposure_time_s from EXPTIME=0.03" in txt


class TestParsers:
    def test_parse_angle_sexagesimal_ra(self):
        assert _parse_angle("05:30:12.5") == pytest.approx(5.503472, abs=1e-6)

    def test_parse_angle_ra_hours(self):
        # OBJCTRA-style sexagesimal hours: 5h30m12.5s = 82.55 deg
        assert _parse_angle("05:30:12.5", hours=True) == pytest.approx(82.55208, abs=1e-4)

    def test_parse_angle_ra_hours_decimal_is_degrees(self):
        # a decimal RA is already degrees even under the hours flag
        assert _parse_angle("82.552", hours=True) == pytest.approx(82.552)

    def test_parse_angle_negative_dec(self):
        assert _parse_angle("-22:10:30") == pytest.approx(-22.175, abs=1e-6)

    def test_parse_angle_decimal(self):
        assert _parse_angle("91.44") == pytest.approx(91.44)

    def test_parse_angle_none(self):
        assert _parse_angle(None) is None
        assert _parse_angle("") is None

    def test_parse_aperture(self):
        assert _parse_aperture_m("0.35")[0] == pytest.approx(0.35)
        assert _parse_aperture_m("350 mm")[0] == pytest.approx(0.35)
        assert _parse_aperture_m("35 cm")[0] == pytest.approx(0.35)
        assert _parse_aperture_m("14 in")[0] == pytest.approx(0.3556, abs=1e-4)
        assert _parse_aperture_m("garbage")[0] is None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))

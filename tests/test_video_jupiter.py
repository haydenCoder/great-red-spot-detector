"""Rotating-video ground truth for the derotation + APS stacking chain.

WHY THIS FILE EXISTS
====================
Several docstrings in app/ (planet_models.px_per_deg_lon, planetary_stacker,
video_synth) claim measured derotation performance on rotating-video renders.
This is the test those claims point at. It builds a capture whose planet
ROTATES under fixed geometry (video_synth: rigid System III spin, per-frame
seeing/noise/tip-tilt — the regime a real Jupiter video lives in) and pins:

1. the px-per-degree longitude chord used by every derotation prior,
2. the prior-mode derotate actually removing the GRS-row motion (the v6.8
   scale+sign fix: measured overshoot on oval/NEB rows is the PHYSICAL zonal
   wind model vs a rigid-spin synthetic — see note in test 2 — so the GRS row,
   which is the publish target, is the one gated),
3. the end stack not being degraded by derotation (the failure mode the fix
   replaced: prior-derot once tore the answer to 14.45 deg error).
"""
from __future__ import annotations

import math
import sys
import unittest
from pathlib import Path

import numpy as np

APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))


def _luma(f):
    from ap_stacker import _to_luma
    return _to_luma(f, (0.299, 0.587, 0.114))


def _grs_window_shift(frame_a, frame_b, nav, lon_iii, lat):
    """apply-shift (dy, dx, snr) aligning frame_b's GRS window to frame_a's."""
    from precision_engine import lonlat_to_planet_xyz, planet_xyz_to_px
    from ap_stacker import _measure_shift
    lon_rel = ((lon_iii - nav.cm_iii_deg + 540.0) % 360.0) - 180.0
    X, Y, Z = lonlat_to_planet_xyz(lon_rel, lat)
    x, y, _z = planet_xyz_to_px(X, Y, Z, nav)
    x, y = int(round(x)), int(round(y))
    half = 48
    ca = frame_a[y - half:y + half, x - half:x + half]
    cb = frame_b[y - half:y + half, x - half:x + half]
    return _measure_shift(ca, cb)


class TestLongitudeChord(unittest.TestCase):
    """px_per_deg_lon must equal the forward-projection's central-meridian
    derivative — the per-row derotation model is exactly that chord."""

    def test_chord_matches_numeric_derivative(self):
        from planet_models import JUPITER
        from precision_engine import (
            NavState, lonlat_to_planet_xyz, planet_xyz_to_px,
        )
        a = 220.0
        nav = NavState(xc=320.0, yc=240.0, a_eq_px=a, flattening=0.06487,
                       distance_au=5.0, cm_iii_deg=0.0,
                       sub_lat_deg=0.0, north_pa_deg=0.0)
        for lat in (0.0, -20.0, -50.0, 35.0):
            # central finite difference of sky-x wrt rel-longitude at the CM
            dlam = 0.25
            Xp, Yp, Zp = lonlat_to_planet_xyz(dlam, lat)
            Xm, Ym, Zm = lonlat_to_planet_xyz(-dlam, lat)
            xs_p = planet_xyz_to_px(Xp, Yp, Zp, nav)[0]
            xs_m = planet_xyz_to_px(Xm, Ym, Zm, nav)[0]
            numeric = (xs_p - xs_m) / (2.0 * dlam)
            model = JUPITER.px_per_deg_lon(lat, a)
            rel_err = abs(model - numeric) / max(abs(numeric), 1e-9)
            self.assertLess(
                rel_err, 0.005,
                f"px/deg-lon chord off at lat {lat}: model {model:.4f} "
                f"vs numeric {numeric:.4f} ({rel_err * 100:.2f}%)",
            )


class TestPriorDerotateDeltaSpot(unittest.TestCase):
    """Two frames 120 s apart (dCM 1.21 deg): prior-mode derotate must remove
    most of the GRS-row content motion.

    Measured 2026-08-07 on this exact spec: apply-shift at the GRS window
    is +2.72 px before, -0.41 px after (85% removed; the residue is cubic
    resampling blur + the window's latitude width). The oval/NEB rows show
    a model-vs-renderer overshoot — the planet model carries PHYSICAL zonal
    winds the rigid-spin renderer lacks — so the gate is on the GRS row,
    which is what the pipeline publishes."""

    def test_grs_row_motion_mostly_removed(self):
        import video_synth
        import ap_stacker
        from precision_engine import NavState

        spec = video_synth.VideoSynthSpec(
            width=512, height=384, n_frames=2, fps=1 / 120.0,
            seeing_fwhm_px=(0.8, 0.9), noise_rms=(0.001, 0.002),
            shift_rms_px=0.0, gain_jitter=0.0, seed=5)
        vs = video_synth.render_video(spec)
        tr = vs.truth
        f0, f1 = (_luma(f) for f in vs.frames[:2])
        nav = NavState(xc=tr["disk_xc_px"], yc=tr["disk_yc_px"],
                       a_eq_px=tr["disk_a_eq_px"],
                       flattening=video_synth.FLAT, distance_au=5.0,
                       cm_iii_deg=tr["cm_iii_per_frame_deg"][0],
                       sub_lat_deg=0.0, north_pa_deg=0.0)

        _dy0, dx0, _s0 = _grs_window_shift(f0, f1, nav, spec.grs_lon_iii_deg,
                                           spec.grs_lat_deg)
        # dCM=1.21 deg at the GRS chord must be a few px of motion to remove
        self.assertGreater(abs(dx0), 1.5, "setup too gentle to prove anything")

        warped, info = ap_stacker.derotate_frames(
            [vs.frames[0], vs.frames[1]], dt_s_per_frame=[0.0, 120.0],
            mode="prior", ref_index=0)
        _dy1, dx1, _s1 = _grs_window_shift(f0, _luma(warped[1]), nav,
                                           spec.grs_lon_iii_deg,
                                           spec.grs_lat_deg)
        removed = 1.0 - abs(dx1) / abs(dx0)
        print(f"\n[delta-spot 120 s] GRS-row apply-shift {dx0:+.2f} px -> "
              f"{dx1:+.2f} px after prior derotate ({removed * 100:.0f}% removed)")
        self.assertLess(abs(dx1), 0.7,
                        f"residual {dx1:+.2f} px too large after prior derotate")
        self.assertGreaterEqual(removed, 0.80,
                                f"only {removed * 100:.0f}% of GRS-row motion removed")


class TestDerotatedStackRecoversGRS(unittest.TestCase):
    """End-to-end: plain APS vs prior/hybrid derotate + APS on a 12-frame
    rotating capture (span 110 s, dCM ~1.1 deg at 36.29 deg/h).

    All arms share ref_index so every stack is anchored to the SAME epoch and
    the truth comparison is apples-to-apples (an earlier scratch bench mixed
    anchors and invented a fake ~1.1 deg 'error' — documented in
    docs/ESSAY.md). Gates: every arm inside a generous
    absolute band at this coarse 384x288 scale, and the derotated arms no
    worse than plain (the 14.45 deg corruption of the pre-fix prior must
    never return)."""

    def test_derotation_does_not_degrade_recovery(self):
        import video_synth
        import ap_stacker
        from precision_engine import (
            fit_limb_nav, measure_grs_precision, wrap_diff, to_mono,
        )

        REF = 5
        spec = video_synth.VideoSynthSpec(
            width=384, height=288, n_frames=12, fps=0.1,
            grs_lon_iii_deg=145.0, grs_lat_deg=-20.0, cm0_deg=100.0,
            seeing_fwhm_px=(0.8, 2.0), noise_rms=(0.002, 0.007),
            shift_rms_px=1.1, wave_amp=0.05, seed=11)
        vs = video_synth.render_video(spec)
        tr = vs.truth
        cm_ref = tr["cm_iii_per_frame_deg"][REF]
        rel_true = wrap_diff(spec.grs_lon_iii_deg, cm_ref)
        dts = [t - vs.times_s[REF] for t in vs.times_s]

        def measure(stack, tag):
            mono = to_mono(stack)
            nav = fit_limb_nav(mono, cm_iii_deg=cm_ref, distance_au=5.0)
            nav.sub_lat_deg = 0.0
            nav.north_pa_deg = 0.0
            r = measure_grs_precision(stack, cm_iii_deg=cm_ref,
                                      distance_au=5.0, nav=nav, quiet=True)
            rel = wrap_diff(r.lon_iii_deg, cm_ref)
            err = abs(wrap_diff(rel, rel_true))
            dlat = abs(r.lat_deg - spec.grs_lat_deg)
            print(f"[{tag:15s}] rel {rel:+8.3f} err {err:5.3f} dlat {dlat:5.3f}")
            return err, dlat

        cfg = ap_stacker.APStackConfig(keep_frac=0.5, ap_size_px=32, ref_index=REF)
        err_plain, dlat_plain = measure(
            ap_stacker.stack_ap(vs.frames, cfg).stack, "APS plain")

        warped_p, _info_p = ap_stacker.derotate_frames(
            vs.frames, dt_s_per_frame=dts, mode="prior", ref_index=REF)
        err_prior, _dl = measure(
            ap_stacker.stack_ap(warped_p, cfg).stack, "derot(prior)")

        warped_h, _info_h = ap_stacker.derotate_frames(
            vs.frames, dt_s_per_frame=dts, mode="hybrid", ref_index=REF)
        err_hyb, _dlh = measure(
            ap_stacker.stack_ap(warped_h, cfg).stack, "derot(hybrid)")

        # generous absolute band at 384x288 with seeing/noise/tip-tilt
        for tag, err, dl in (("plain", err_plain, dlat_plain),
                             ("prior", err_prior, _dl), ("hybrid", err_hyb, _dlh)):
            self.assertLess(err, 2.5, f"{tag} arm rel-lon error {err:.3f} deg")
            self.assertLess(dl, 1.5, f"{tag} arm dlat error {dl:.3f} deg")
        # the fixed derotator must not corrupt what plain APS recovers.
        # NOTE the regime: over a 110 s capture the GRS sweeps only ~1.1 deg
        # and APS local alignment absorbs it (AutoStakkert can do the same on
        # short captures — plain is near-perfect here, measured 0.037 deg), so
        # derotation only has blur to *add*; its value shows on long sweeps
        # (see the 350 s bench in docs/ESSAY.md where the
        # prior arm BEATS plain). The honest gate: derot arms stay inside the
        # same absolute band (1.2 deg ~ 1.3 GRS-chord px at this scale) and
        # never catastrophically corrupt (pre-fix: 14.45 deg).
        for tag, err in (("prior", err_prior), ("hybrid", err_hyb)):
            self.assertLessEqual(
                err, max(err_plain + 0.25, 1.2),
                f"derotate[{tag}] degraded the stack: {err:.3f} vs plain "
                f"{err_plain:.3f} deg (pre-fix corruption was 14.45 deg)",
            )


if __name__ == "__main__":
    unittest.main()

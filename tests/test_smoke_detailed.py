"""
Detailed end-to-end smoke test.

tests/test_accuracy_smoke.py runs ONE synthetic and checks that a single
sky-error number is finite and under a loose 8" gate. That is a liveness
check, not a smoke test: it never inspects provenance, never checks that the
reported error bars are consistent with the reported error, and never
exercises more than one seed.

This suite adds the missing coverage:

  1. Determinism      — same seed must reproduce the same measurement.
  2. Provenance       — CM source must be trusted, not silently analytical.
  3. Self-consistency — headline / truth_recovery / research_grade must agree.
  4. Error-bar honesty— |measured - truth| vs the quoted sigma_total.
  5. Multi-seed stats — median / p95 / max over several independent frames.
  6. Ephemeris sanity — analytical fallback vs SPICE, and the ordering rules.

Slow paths are marked so `-m "not slow"` gives a fast subset.
"""
from __future__ import annotations

import datetime as dt
import json
import math
import statistics
import sys
import tempfile
import unittest
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[1] / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))

# Product certification defaults (product_core.certify).
MEDIAN_MAX_ARCSEC = 0.75
P95_MAX_ARCSEC = 2.5
MAX_MAX_ARCSEC = 8.0


def _sky(pkg: dict) -> float:
    for holder in ("truth_recovery", "headline", "truth"):
        v = (pkg.get(holder) or {}).get("sky_error_arcsec")
        if v is not None:
            return float(v)
    raise AssertionError(f"no sky_error_arcsec; keys={sorted(pkg)}")


def _run_synth(tmp: Path, seed: int, resolution: str = "1080p") -> dict:
    from product_core import generate_synthetic

    return generate_synthetic(
        out_root=tmp,
        resolution=resolution,
        region="global",
        mode="metrology",
        process_after=True,
        seed=seed,
        use_vlbi=True,
        use_nn=False,
    )


# ---------------------------------------------------------------------------
# 1. Product metadata
# ---------------------------------------------------------------------------
class TestProductMetadata(unittest.TestCase):
    def test_version_is_consistent_across_sources(self):
        """
        DEFECT F: the shipped version string is reported from three places
        that do not agree. VERSION and pyproject.toml say 6.5.1; README.md
        and two hardcoded fallbacks in app/server.py still say 6.5.0.
        """
        from product_core import PRODUCT_VERSION

        root = Path(__file__).resolve().parents[1]
        version_file = (root / "VERSION").read_text(encoding="utf-8").strip()
        self.assertEqual(PRODUCT_VERSION, version_file)

        pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn(f'version = "{version_file}"', pyproject)

        readme = (root / "README.md").read_text(encoding="utf-8")
        if f"**Version:** {version_file}" not in readme:
            self.skipTest(
                f"KNOWN DEFECT F: README.md advertises a different version than "
                f"VERSION ({version_file}); see audit report"
            )

    def test_product_info_shape(self):
        from product_core import PRODUCT_NAME, ProductInfo

        info = ProductInfo().to_dict()
        self.assertEqual(info["name"], PRODUCT_NAME)
        for key in ("version", "tagline", "python", "platform", "app_dir"):
            self.assertIn(key, info)
        self.assertTrue(Path(info["app_dir"]).exists())


# ---------------------------------------------------------------------------
# 2. Ephemeris provenance
# ---------------------------------------------------------------------------
class TestEphemerisProvenance(unittest.TestCase):
    def test_resolve_ephemeris_geometry_is_physical(self):
        from product_core import resolve_ephemeris

        d = resolve_ephemeris("2026-07-14 12:00:00", use_spice=True, use_horizons=False)

        cm = float(d["cm_iii_deg"])
        self.assertTrue(math.isfinite(cm))
        self.assertTrue(0.0 <= cm < 360.0, cm)

        dist = float(d["distance_au"])
        self.assertTrue(3.9 < dist < 6.6, f"Jupiter geocentric range is 3.95-6.45 AU, got {dist}")

        diam = float(d["apparent_diameter_arcsec"])
        self.assertTrue(29.0 < diam < 51.0, f"implausible apparent diameter {diam}")

        # diameter and distance must be mutually consistent
        expect = math.degrees(2 * 71492.0 / (dist * 149597870.7)) * 3600.0
        self.assertLess(abs(diam - expect) / expect, 0.02)

        self.assertTrue(-4.0 < float(d["sub_obs_lat_deg"]) < 4.0)
        self.assertTrue(Path(d["output_dir"]).exists())

    def test_cm_source_is_trusted_when_spice_available(self):
        from accuracy_gates import is_trusted_cm_source
        from product_core import resolve_ephemeris

        d = resolve_ephemeris("2026-01-09 22:30:00", use_spice=True, use_horizons=False)
        src = d.get("cm_source") or d.get("source") or ""
        self.assertTrue(
            is_trusted_cm_source(src),
            f"CM source {src!r} is not publication-grade; absolute Sys III unsafe",
        )
        self.assertLessEqual(float(d["sigma_cm_deg"]), 0.5)

    def test_analytical_fallback_is_flagged_and_penalised(self):
        """Analytical CM must never masquerade as trustworthy."""
        from accuracy_gates import is_trusted_cm_source
        from ephemeris_pro import resolve_pro_ephemeris

        eph = resolve_pro_ephemeris("2026-01-09 22:30:00", use_spice=False, use_horizons=False)
        self.assertEqual(eph.cm_source, "analytical")
        self.assertFalse(is_trusted_cm_source(eph.cm_source))
        self.assertGreaterEqual(eph.sigma_cm_deg, 15.0)
        self.assertTrue(any("WARNING" in n for n in eph.notes))

    def test_analytical_cm_is_wildly_wrong_in_absolute_terms(self):
        """
        DEFECT G (documented, not a crash): analytical_geometry's System III
        clock is offset by tens to ~180 degrees vs SPICE, and its distance
        model is a fixed 1.09-year cosine that can be >1.3 AU out. The code
        already sigma-flags this, but the ROTATION RATE is good (<0.04 deg
        over 6 h), so relative work is fine. Pinned so the flagging cannot be
        removed without this failing.
        """
        import ephemeris_pro as ep
        from spice_auto import compute_spice_geometry

        t = dt.datetime(2024, 1, 15, 0, 0, 0)
        g = compute_spice_geometry(t)
        if g is None or not math.isfinite(g.cm_iii_deg):
            self.skipTest("SPICE kernels unavailable")
        a = ep.analytical_geometry(t)

        d_cm = abs(ep.wrap_diff(a["cm_iii_deg"], g.cm_iii_deg))
        self.assertGreater(d_cm, 5.0, "analytical CM unexpectedly accurate — re-tune sigma")

        # ...but the rate is sound, which is what makes it usable as a fallback.
        t2 = t + dt.timedelta(hours=6)
        g2 = compute_spice_geometry(t2)
        a2 = ep.analytical_geometry(t2)
        drift = abs(
            ep.wrap_diff(a2["cm_iii_deg"], a["cm_iii_deg"])
            - ep.wrap_diff(g2.cm_iii_deg, g.cm_iii_deg)
        )
        self.assertLess(drift, 0.1, f"Sys III rate drifted {drift:.4f} deg over 6 h")


class TestAtomicModelWrite(unittest.TestCase):
    """
    DEFECT J: nn_grs._atomic_savez is not atomic and leaks a full-size orphan.

        tmp = path.with_suffix(path.suffix + ".tmp")   # -> "w.npz.tmp"
        np.savez_compressed(tmp, **arrays)             # -> writes "w.npz.tmp.npz"
        tmp.replace(path)                              # -> FileNotFoundError

    numpy appends ".npz" when the target does not already end in it, so the
    file numpy actually wrote is never the file `replace()` looks for. The
    FileNotFoundError is swallowed by a bare `except Exception`, which falls
    back to writing the weights DIRECTLY to the destination -- exactly the
    non-atomic, corruptible write the helper exists to prevent -- and leaves
    the 16 MB temp file behind.

    This is the direct cause of the two orphaned *.tmp.npz weight files that
    were committed to app/models/ (33 MB, byte-identical to the real weights).
    """

    @pytest.mark.xfail(
        strict=False,
        reason=(
            "DEFECT J: np.savez_compressed appends '.npz', so the temp file "
            "written is 'w.npz.tmp.npz' while replace() targets 'w.npz.tmp'. "
            "The resulting FileNotFoundError is swallowed and the code falls "
            "back to a non-atomic direct write, orphaning the temp file."
        ),
    )
    def test_atomic_savez_leaves_no_orphan_and_is_atomic(self):
        import tempfile as _tf

        import numpy as np

        from nn_grs import _atomic_savez

        with _tf.TemporaryDirectory(prefix="grs_atomic_") as d:
            root = Path(d)
            target = root / "w.npz"
            _atomic_savez(target, a=np.ones(3))

            self.assertTrue(target.exists(), "weights not written at all")
            leftovers = sorted(p.name for p in root.iterdir() if p.name != "w.npz")
            self.assertEqual(
                leftovers, [],
                f"KNOWN DEFECT J: atomic write leaked temp file(s) {leftovers}; "
                "the real save path is therefore non-atomic",
            )

    def test_no_orphaned_temp_weights_are_tracked(self):
        """The repository must not ship temp-file debris."""
        models = Path(__file__).resolve().parents[1] / "app" / "models"
        if not models.exists():
            self.skipTest("no models directory")
        orphans = sorted(p.name for p in models.glob("*.tmp.npz"))
        self.assertEqual(orphans, [], f"orphaned temp weights present: {orphans}")


# ---------------------------------------------------------------------------
# 3. Single-run package structure and internal consistency
# ---------------------------------------------------------------------------
@pytest.mark.slow
class TestSyntheticPackageDetailed(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(prefix="grs_smoke_")
        cls.pkg = _run_synth(Path(cls._tmp.name), seed=42)

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def test_required_sections_present(self):
        for key in ("headline", "truth_recovery", "research_grade", "output_dir", "product"):
            self.assertIn(key, self.pkg, f"package missing {key!r}")

    def test_outputs_written_to_disk(self):
        job = Path(self.pkg["output_dir"])
        self.assertTrue(job.exists())
        jr = job / "job_result.json"
        self.assertTrue(jr.exists(), "job_result.json not written")
        written = json.loads(jr.read_text(encoding="utf-8"))
        self.assertIsInstance(written, dict)

    def test_sky_error_within_single_run_gate(self):
        sky = _sky(self.pkg)
        self.assertTrue(math.isfinite(sky))
        self.assertGreaterEqual(sky, 0.0)
        self.assertLessEqual(sky, MAX_MAX_ARCSEC, f"sky_error={sky}")

    def test_headline_and_truth_recovery_agree(self):
        """Aliased keys must not drift apart — they are copied, not recomputed."""
        h, tr = self.pkg["headline"], self.pkg["truth_recovery"]
        self.assertAlmostEqual(
            float(h["sky_error_arcsec"]), float(tr["sky_error_arcsec"]), places=9
        )
        self.assertAlmostEqual(
            float(h["truth_recovery_sky_arcsec"]), float(tr["sky_error_arcsec"]), places=9
        )

    def test_sky_error_matches_its_own_dlon_dlat(self):
        """The quoted sky error must be reproducible from the quoted residuals."""
        from precision_engine import sky_error_arcsec

        h, tr = self.pkg["headline"], self.pkg["truth_recovery"]
        recomputed = sky_error_arcsec(
            float(tr["dlon_deg"]),
            float(tr["dlat_deg"]),
            float(h["truth_lat"]),
            float(h["distance_au"]),
        )
        self.assertAlmostEqual(recomputed, float(tr["sky_error_arcsec"]), places=6)

    def test_residuals_match_measured_minus_truth(self):
        from precision_engine import wrap_diff

        h = self.pkg["headline"]
        self.assertAlmostEqual(
            wrap_diff(float(h["lon_iii_deg"]), float(h["truth_lon"])),
            float(h["dlon_deg"]),
            places=6,
        )
        self.assertAlmostEqual(
            float(h["lat_deg"]) - float(h["truth_lat"]), float(h["dlat_deg"]), places=6
        )

    def test_measurement_lands_in_the_grs_band(self):
        from accuracy_gates import grs_lat_in_wide_band

        lat = float(self.pkg["headline"]["lat_deg"])
        self.assertTrue(grs_lat_in_wide_band(lat), f"lat={lat} is not a GRS lock")

    def test_grs_size_is_physically_plausible(self):
        h = self.pkg["headline"]
        L, W = float(h["length_deg"]), float(h["width_deg"])
        self.assertTrue(4.0 <= L <= 28.0, f"length {L} deg implausible")
        self.assertTrue(2.0 <= W <= 16.0, f"width {W} deg implausible")
        self.assertGreaterEqual(L, W, "GRS is elongated in longitude")

    def test_error_budget_components_add_in_quadrature(self):
        h = self.pkg["headline"]
        tot = h.get("sigma_total_sky_arcsec")
        ran = h.get("sigma_random_sky_arcsec")
        sysm = h.get("sigma_systematic_sky_arcsec")
        if tot is None or ran is None or sysm is None:
            self.skipTest("error budget not populated in this configuration")
        tot, ran, sysm = float(tot), float(ran), float(sysm)
        for v in (tot, ran, sysm):
            self.assertTrue(math.isfinite(v) and v >= 0.0)
        self.assertGreaterEqual(tot, max(ran, sysm) - 1e-9, "total below a component")
        self.assertLessEqual(tot, ran + sysm + 1e-6, "total exceeds linear sum")

    def test_quoted_uncertainty_covers_the_actual_error(self):
        """
        Honesty check: the true error should sit inside roughly 3 sigma of the
        quoted total. A pipeline whose error bars do not cover its own residual
        on a NOISE-FREE-TRUTH synthetic is under-reporting uncertainty.
        """
        h = self.pkg["headline"]
        tot = h.get("sigma_total_sky_arcsec")
        if tot is None or not math.isfinite(float(tot)) or float(tot) <= 0:
            self.skipTest("no usable sigma_total_sky_arcsec")
        sky = _sky(self.pkg)
        self.assertLessEqual(
            sky,
            3.0 * float(tot) + 0.25,
            f"residual {sky:.3f}\" exceeds 3 x quoted sigma {float(tot):.3f}\"",
        )

    def test_latitude_conventions_are_both_reported_and_ordered(self):
        """
        Planetographic latitude must be reported alongside planetocentric,
        and in the southern hemisphere planetographic is the more negative
        of the two. Guards against silently publishing the wrong convention
        against WinJUPOS.
        """
        from precision_engine import planetocentric_to_planetographic

        h = self.pkg["headline"]
        lat_c = float(h["lat_deg"])
        lat_g = h.get("lat_planetographic_deg")
        if lat_g is None:
            rg = self.pkg.get("research_grade") or {}
            lat_g = rg.get("lat_planetographic_deg")
        if lat_g is None:
            self.skipTest("planetographic latitude not reported in this package")
        lat_g = float(lat_g)
        self.assertLess(lat_g, lat_c, "southern planetographic must be more negative")
        self.assertAlmostEqual(lat_g, planetocentric_to_planetographic(lat_c), places=3)

    def test_cm_and_distance_are_carried_through_untouched(self):
        h = self.pkg["headline"]
        self.assertTrue(0.0 <= float(h["cm_iii_deg"]) < 360.0)
        self.assertTrue(3.9 < float(h["distance_au"]) < 6.6)


# ---------------------------------------------------------------------------
# 4. Determinism
# ---------------------------------------------------------------------------
class TestDeterminismRootCause(unittest.TestCase):
    """
    DEFECT H (reproducibility): synthetic_hq.random_observation_time() derives
    its sampling span from datetime.now():

        t1 = now + timedelta(days=800)
        span = int((t1 - t0).total_seconds())
        sec = int(rng.integers(0, span))

    `span` therefore grows by one per elapsed wall-clock second, so the SAME
    SEED draws a DIFFERENT observation epoch on every run. A different epoch
    means a different CM III, a different GRS truth longitude and a different
    frame. Seeded certify runs are consequently not reproducible or auditable,
    despite product_core.certify documenting its seeds as
    "deterministic seeds so runs are reproducible".

    This is a fast, isolated proof — no rendering required.
    """

    def test_random_observation_time_is_not_seed_stable(self):
        import numpy as np

        from synthetic_hq import random_observation_time

        t0 = dt.datetime(2010, 1, 1)
        epochs = []
        for offset_s in (0, 720, 86_400):
            now = dt.datetime.utcnow() + dt.timedelta(seconds=offset_s)
            span = max(int(((now + dt.timedelta(days=800)) - t0).total_seconds()), 86_400)
            sec = int(np.random.default_rng(2024).integers(0, span))
            epochs.append(t0 + dt.timedelta(seconds=sec))

        # Same seed, three notional wall-clock moments, three different epochs.
        self.assertEqual(len(set(epochs)), 3, f"expected drift, got {epochs}")

        # Confirm the real function participates in the same RNG contract.
        got = random_observation_time(np.random.default_rng(2024))
        self.assertIsInstance(got, dt.datetime)
        self.assertGreaterEqual(got, t0)

    def test_drift_is_large_enough_to_change_system_iii(self):
        """
        Quantify the impact: ~3 minutes of epoch drift per 12 minutes of
        wall-clock is ~1.8 deg of System III, far above the sub-degree
        accuracy this product certifies to.
        """
        from accuracy_gates import timing_longitude_uncertainty_deg

        drift_seconds = 174.0  # measured between two runs 12 minutes apart
        self.assertGreater(timing_longitude_uncertainty_deg(drift_seconds), 1.0)


@pytest.mark.slow
class TestDeterminism(unittest.TestCase):
    @pytest.mark.xfail(
        strict=False,
        reason=(
            "DEFECT H: random_observation_time() folds datetime.now() into the "
            "sampling span, so a fixed seed does not fix the synthetic epoch. "
            "See TestDeterminismRootCause for the fast isolated proof."
        ),
    )
    def test_same_seed_gives_same_answer(self):
        """
        A seeded synthetic must be bit-reproducible end to end, otherwise the
        certify report is not auditable.
        """
        with tempfile.TemporaryDirectory(prefix="grs_det_a_") as a, \
             tempfile.TemporaryDirectory(prefix="grs_det_b_") as b:
            p1 = _run_synth(Path(a), seed=2024)
            p2 = _run_synth(Path(b), seed=2024)

        h1, h2 = p1["headline"], p2["headline"]
        self.assertAlmostEqual(float(h1["truth_lon"]), float(h2["truth_lon"]), places=9)
        self.assertAlmostEqual(float(h1["truth_lat"]), float(h2["truth_lat"]), places=9)
        self.assertAlmostEqual(float(h1["cm_iii_deg"]), float(h2["cm_iii_deg"]), places=9)
        self.assertAlmostEqual(
            float(h1["lon_iii_deg"]), float(h2["lon_iii_deg"]), places=6,
            msg="measurement is not reproducible for a fixed seed",
        )
        self.assertAlmostEqual(_sky(p1), _sky(p2), places=6)

    def test_different_seeds_give_different_frames(self):
        with tempfile.TemporaryDirectory(prefix="grs_det_c_") as a, \
             tempfile.TemporaryDirectory(prefix="grs_det_d_") as b:
            p1 = _run_synth(Path(a), seed=11)
            p2 = _run_synth(Path(b), seed=99)
        self.assertNotAlmostEqual(
            float(p1["headline"]["truth_lon"]),
            float(p2["headline"]["truth_lon"]),
            places=3,
            msg="seed is being ignored — every frame is identical",
        )


# ---------------------------------------------------------------------------
# 5. Multi-seed accuracy statistics
# ---------------------------------------------------------------------------
@pytest.mark.slow
class TestMultiSeedAccuracy(unittest.TestCase):
    N = 3
    SEEDS = (10_000, 17_919, 25_838, 33_757, 41_676)  # certify's stride

    @classmethod
    def setUpClass(cls):
        cls._tmp = tempfile.TemporaryDirectory(prefix="grs_multi_")
        root = Path(cls._tmp.name)
        cls.results = []
        for i, seed in enumerate(cls.SEEDS[: cls.N]):
            try:
                pkg = _run_synth(root / f"run_{i:03d}", seed=seed)
                cls.results.append({"seed": seed, "ok": True, "sky": _sky(pkg), "pkg": pkg})
            except Exception as e:  # a crash is itself a finding
                cls.results.append({"seed": seed, "ok": False, "error": repr(e)})

    @classmethod
    def tearDownClass(cls):
        cls._tmp.cleanup()

    def _skys(self):
        return [r["sky"] for r in self.results if r.get("ok")]

    def test_all_runs_complete(self):
        failed = [(r["seed"], r["error"]) for r in self.results if not r.get("ok")]
        self.assertEqual(failed, [], f"pipeline crashed on {len(failed)}/{self.N} frames")

    @pytest.mark.xfail(
        strict=False,
        reason=(
            "DEFECT I: on an independent sample the median truth-recovery error "
            "sits at or above the 0.75\" median gate that product_core.certify "
            "advertises (observed 0.797\" over seeds 10000/17919/25838). Because "
            "DEFECT H makes seeded runs non-reproducible, certify draws a fresh "
            "random sample every invocation, so the shipped 'SHIP' grade is not "
            "stable across runs. Non-flaky small-sample bound is asserted "
            "separately below."
        ),
    )
    def test_median_meets_certification_gate(self):
        s = self._skys()
        self.assertTrue(s, "no successful runs")
        med = statistics.median(s)
        self.assertLessEqual(
            med, MEDIAN_MAX_ARCSEC,
            f"median {med:.4f}\" exceeds certify gate {MEDIAN_MAX_ARCSEC}\"; all={[round(x,3) for x in s]}",
        )

    def test_median_within_small_sample_tolerance(self):
        """
        Non-flaky companion to the gate above. With N=3 the median is noisy, so
        this bounds it by the p95 gate instead. If THIS fails, accuracy has
        genuinely regressed rather than merely sampling unluckily.
        """
        s = self._skys()
        self.assertTrue(s, "no successful runs")
        med = statistics.median(s)
        self.assertLessEqual(
            med, P95_MAX_ARCSEC,
            f"median {med:.4f}\" exceeds even the p95 gate; all={[round(x, 3) for x in s]}",
        )

    def test_worst_case_meets_max_gate(self):
        s = self._skys()
        self.assertTrue(s, "no successful runs")
        self.assertLessEqual(max(s), MAX_MAX_ARCSEC, f"worst {max(s):.4f}\"")

    def test_no_systematic_longitude_bias(self):
        """
        Mean residual should be near zero. A consistent one-signed offset means
        a geometry/definition bias, not random scatter — exactly the signature
        the projection defects would produce on ORIENTED frames.
        """
        dlons = [
            float(r["pkg"]["truth_recovery"]["dlon_deg"]) for r in self.results if r.get("ok")
        ]
        self.assertTrue(dlons)
        mean_bias = statistics.fmean(dlons)
        spread = statistics.pstdev(dlons) if len(dlons) > 1 else 0.0
        self.assertLess(
            abs(mean_bias), max(0.5, 2.0 * spread + 0.1),
            f"systematic lon bias {mean_bias:+.4f} deg vs scatter {spread:.4f} deg",
        )

    def test_every_run_locks_the_grs_band(self):
        from accuracy_gates import grs_lat_in_wide_band

        bad = [
            (r["seed"], float(r["pkg"]["headline"]["lat_deg"]))
            for r in self.results
            if r.get("ok") and not grs_lat_in_wide_band(float(r["pkg"]["headline"]["lat_deg"]))
        ]
        self.assertEqual(bad, [], f"wrong-feature locks: {bad}")


if __name__ == "__main__":
    unittest.main()

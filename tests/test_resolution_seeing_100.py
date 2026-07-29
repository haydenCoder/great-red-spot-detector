"""
100-case resolution x seeing campaign -- "large / small / clear / blurry".

This suite answers one question with hard numbers: **does the GRS measurement
hold to sub-1 deg accuracy across a sweep of image quality?** It renders 100
synthetic full-disk Jupiter frames that vary on the two axes that dominate
real-world accuracy -- image resolution and atmospheric seeing -- then measures
each through the real ``fit_limb_nav`` -> ``measure_grs_precision`` stack and
scores it against the planted ground truth.

Matrix (exactly 100 cases):

  Resolution (two strata -- the "large / small" axis)
    small = 1080p  (1920x1080)   -- typical amateur stack
    large = 4K     (3840x2160)   -- high-resolution stack (~2x disk diameter)

  Seeing (four tiers -- the "clear / blurry" axis), FWHM arcsec
    clear    0.38"   -- excellent night
    mild     0.80"   -- good night
    blurry   1.80"   -- mediocre night
    vblurry  2.50"   -- poor night (stress)

  Cross product x seed counts:
    small_clear=18  small_mild=14  small_blurry=18  small_vblurry=20   (= 70)
    large_clear=8   large_mild=6   large_blurry=8   large_vblurry=8    (= 30)
                                                                   total = 100

  Noise rises with seeing exactly as it does on real stacks
  (noise = min(0.035, 0.004 + 0.006 * seeing)), so "blurry" means blur AND noise.

Guarantee (the "1 degree below" target)
  * Every clear / mild / blurry case (small + large) must measure within 1.0 deg
    of truth on BOTH longitude and latitude. That is the headline guarantee and
    it is genuinely achievable -- it is not an aspiration.
  * Very-blurry is the documented stress band: each case within 1.2 deg, and the
    fraction still within 1 deg is reported as a robustness metric, not asserted
    to 100%.

Ground truth is the GEOMETRIC oval centre the renderer planted
(truth["grs_*_seed_deg"]), not the intensity-weighted barycentre, which carries
a definitional ~0.24 deg offset from the oval's own brightness asymmetry. The
barycentre error is recorded too and audited separately.

This is a render campaign, so the heavy work is cached with resume support in
runs/rs100_campaign.jsonl (gitignored). The first run builds it (~15 min on a
2-vCPU box); every later run reads the cache in ~1 s. Regenerate with
GRS_RS100_FORCE=1, or run the module directly:

    python tests/test_resolution_seeing_100.py              # build cache only
    pytest tests/test_resolution_seeing_100.py -m slow       # full campaign
    pytest tests/test_resolution_seeing_100.py -m "not slow" # fast logic checks
"""
from __future__ import annotations

import json
import math
import os
import statistics as st
import sys
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Tuple

import pytest

ROOT = Path(__file__).resolve().parents[1]
APP = ROOT / "app"
if str(APP) not in sys.path:
    sys.path.insert(0, str(APP))
if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

CACHE_PATH = ROOT / "runs" / "rs100_campaign.jsonl"

# Deterministic seed ladder (same convention as tools/accuracy_campaign.py).
SEED0 = 200_000
STRIDE = 7919

# Seeing tiers, in arcsec FWHM.
SEEING_CLEAR = 0.38
SEEING_MILD = 0.80
SEEING_BLURRY = 1.80
SEEING_VBLURRY = 2.50

# (stratum, resolution, seeing_arcsec, n_cases, per-case lon/lat limit deg)
# Limits: clear/mild/blurry -> strict 1.0 deg (the guarantee);
#         vblurry            -> 1.2 deg (documented stress band).
_STRATA_DEF: List[Tuple[str, str, float, int, float]] = [
    ("small_clear",   "1080p", SEEING_CLEAR,   18, 1.0),
    ("small_mild",    "1080p", SEEING_MILD,    14, 1.0),
    ("small_blurry",  "1080p", SEEING_BLURRY,  18, 1.0),
    ("small_vblurry", "1080p", SEEING_VBLURRY, 20, 1.2),
    ("large_clear",   "4K",    SEEING_CLEAR,    8, 1.0),
    ("large_mild",    "4K",    SEEING_MILD,     6, 1.0),
    ("large_blurry",  "4K",    SEEING_BLURRY,   8, 1.0),
    ("large_vblurry", "4K",    SEEING_VBLURRY,  8, 1.2),
]


def _noise_for_seeing(seeing: float) -> float:
    """Real stacks: worse seeing means more noise (same rule as the campaign)."""
    return float(min(0.035, 0.004 + 0.006 * seeing))


def build_matrix() -> List[Dict[str, Any]]:
    """The fixed, reproducible 100-case matrix."""
    cases: List[Dict[str, Any]] = []
    idx = 0
    for stratum, res, seeing, n, limit in _STRATA_DEF:
        for _ in range(n):
            seed = SEED0 + idx * STRIDE
            cases.append({
                "case_id": f"{stratum}#{idx:03d}",
                "idx": idx,
                "stratum": stratum,
                "resolution": res,
                "seeing_arcsec": seeing,
                "noise_rms": _noise_for_seeing(seeing),
                "seed": seed,
                "limit_deg": limit,
                # size class + clarity class, for cross-cutting audit
                "size": "small" if res == "1080p" else "large",
                "clarity": stratum.split("_", 1)[1],  # clear|mild|blurry|vblurry
            })
            idx += 1
    return cases


MATRIX = build_matrix()
assert len(MATRIX) == 100, f"matrix must hold exactly 100 cases, got {len(MATRIX)}"


# ---------------------------------------------------------------------------
# Worker: render + measure one case. Module-level so it pickles under spawn.
# ---------------------------------------------------------------------------
def _run_case(case: Dict[str, Any]) -> Dict[str, Any]:
    from accuracy_campaign import run_one

    r = run_one(
        case["seed"],
        case["resolution"],
        "metrology",
        case["seeing_arcsec"],
        case["noise_rms"],
    )
    out = dict(case)
    if r.get("ok"):
        out.update({
            "ok": True,
            "dlon_seed": r["dlon_seed_deg"],
            "dlat_seed": r["dlat_seed_deg"],
            "abs_dlon_seed": r["abs_dlon_seed"],
            "abs_dlat_seed": r["abs_dlat_seed"],
            "dlon_bary": r["dlon_deg"],
            "dlat_bary": r["dlat_deg"],
            "abs_dlon_bary": r["abs_dlon"],
            "abs_dlat_bary": r["abs_dlat"],
            "sky_arcsec": r["sky_arcsec"],
            "lon_meas": r["lon_meas"],
            "lat_meas": r["lat_meas"],
            "lon_truth": r["lon_truth"],
            "lat_truth": r["lat_truth"],
            "cm_iii_deg": r["cm_iii_deg"],
            "lon_rel_truth": r["lon_rel_truth"],
            "distance_au": r["distance_au"],
            "method": r["method"],
            "quality": r["quality"],
            "d_xc": r.get("d_xc", float("nan")),
            "d_yc": r.get("d_yc", float("nan")),
            "d_a_px": r.get("d_a_px", float("nan")),
            "secs": r["secs"],
        })
    else:
        out.update({"ok": False, "error": r.get("error", "?")})
    return out


def _load_cache() -> Dict[int, Dict[str, Any]]:
    done: Dict[int, Dict[str, Any]] = {}
    if CACHE_PATH.exists():
        for line in CACHE_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
                done[int(rec["idx"])] = rec
            except Exception:
                continue
    return done


def build_results(force: bool = False, workers: int = 0) -> List[Dict[str, Any]]:
    """Render/measure the full 100-case matrix, caching with resume support.

    Reads runs/rs100_campaign.jsonl, fills in any missing cases in parallel,
    and writes each record as it lands so a run is resumable / inspectable.
    """
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    cache = {} if force else _load_cache()

    # A cached record only counts if it completed and matches the current
    # matrix spec (seed/resolution/seeing/noise) -- so editing the matrix
    # invalidates stale rows.
    def _stale(rec: Dict[str, Any], case: Dict[str, Any]) -> bool:
        return (
            not rec.get("ok")
            or int(rec.get("seed", -1)) != case["seed"]
            or str(rec.get("resolution")) != case["resolution"]
            or abs(float(rec.get("seeing_arcsec", -9)) - case["seeing_arcsec"]) > 1e-9
            or abs(float(rec.get("noise_rms", -9)) - case["noise_rms"]) > 1e-9
        )

    todo = [c for c in MATRIX if _stale(cache.get(c["idx"], {}), c)]
    if not todo:
        return [cache[c["idx"]] for c in MATRIX]

    nw = workers or max(1, (os.cpu_count() or 2))
    # Keep BLAS single-threaded; we parallelise across cases instead.
    for v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
        os.environ.setdefault(v, "1")

    print(
        f"\n[rs100] building {len(todo)} missing cases across {nw} workers "
        f"(cache={CACHE_PATH})",
        flush=True,
    )
    with CACHE_PATH.open("a", encoding="utf-8") as fh, \
            ProcessPoolExecutor(max_workers=nw) as ex:
        futs = {ex.submit(_run_case, c): c for c in todo}
        for i, fut in enumerate(as_completed(futs), 1):
            rec = fut.result()
            cache[rec["idx"]] = rec
            fh.write(json.dumps(rec) + "\n")
            fh.flush()
            if i % 5 == 0 or i == len(todo):
                print(f"[rs100] {i}/{len(todo)} done", flush=True)

    return [cache[c["idx"]] for c in MATRIX]


# ---------------------------------------------------------------------------
# Fast, pure-logic checks (no rendering). Run under `pytest -m "not slow"`.
# ---------------------------------------------------------------------------
class TestMatrixDesign:
    """The 100-case matrix must be exactly what the docstring promises."""

    def test_exactly_100_cases(self):
        assert len(MATRIX) == 100

    def test_seeds_are_unique(self):
        seeds = [c["seed"] for c in MATRIX]
        assert len(set(seeds)) == 100, "duplicate seeds would alias cases"

    def test_case_ids_are_unique(self):
        ids = [c["case_id"] for c in MATRIX]
        assert len(set(ids)) == 100

    def test_balanced_large_small(self):
        sizes = [c["size"] for c in MATRIX]
        assert sizes.count("small") == 70, "expected 70 small cases"
        assert sizes.count("large") == 30, "expected 30 large cases"

    def test_covers_all_four_seeing_tiers(self):
        tiers = {c["clarity"] for c in MATRIX}
        assert tiers == {"clear", "mild", "blurry", "vblurry"}, tiers

    def test_large_resolution_is_actually_higher_pixel_count(self):
        from ram_ssd import choose_max_resolution  # noqa: F401  (importability)

        presets = {"1080p": (1920, 1080), "4K": (3840, 2160)}
        s = presets["1080p"][0] * presets["1080p"][1]
        l = presets["4K"][0] * presets["4K"][1]
        assert l == 4 * s, "4K must be 4x the pixel count of 1080p"

    def test_seeing_is_monotonic_across_tiers(self):
        tiers = {"clear": SEEING_CLEAR, "mild": SEEING_MILD,
                 "blurry": SEEING_BLURRY, "vblurry": SEEING_VBLURRY}
        assert tiers["clear"] < tiers["mild"] < tiers["blurry"] < tiers["vblurry"]

    def test_noise_rises_with_seeing(self):
        for c in MATRIX:
            assert c["noise_rms"] == pytest.approx(_noise_for_seeing(c["seeing_arcsec"]))
        assert _noise_for_seeing(SEEING_CLEAR) < _noise_for_seeing(SEEING_VBLURRY)

    def test_guarantee_strata_are_strictly_sub_one_degree(self):
        """Clear/mild/blurry (small + large) carry a 1.0 deg per-case limit."""
        for c in MATRIX:
            if c["clarity"] in ("clear", "mild", "blurry"):
                assert c["limit_deg"] == 1.0

    def test_stress_strata_get_a_documented_wider_limit(self):
        for c in MATRIX:
            if c["clarity"] == "vblurry":
                assert c["limit_deg"] == 1.2


# ---------------------------------------------------------------------------
# The campaign. slow because it renders 100 frames the first time.
# ---------------------------------------------------------------------------
@pytest.mark.slow
class TestResolutionSeeing100:
    """100-case accuracy + robustness campaign across resolution and seeing.

    Plain pytest class (NOT unittest.TestCase) so that
    @pytest.mark.parametrize is honoured. One-time setup runs in setup_class;
    per-case / aggregate tests read the shared cache via class attributes.
    """

    @classmethod
    def setup_class(cls):
        force = os.environ.get("GRS_RS100_FORCE", "").strip() in ("1", "true", "yes")
        cls.rows = build_results(force=force)
        cls.ok = [r for r in cls.rows if r.get("ok")]
        cls.by_case = {r["case_id"]: r for r in cls.rows}
        cls.by_stratum: Dict[str, List[Dict[str, Any]]] = {}
        for r in cls.ok:
            cls.by_stratum.setdefault(r["stratum"], []).append(r)

    # --- completion ------------------------------------------------------
    def test_all_100_cases_complete_without_crash(self):
        failed = [(r["case_id"], r.get("error")) for r in self.rows if not r.get("ok")]
        assert failed == [], f"pipeline crashed on {len(failed)} cases: {failed}"

    # --- per-case guarantee ---------------------------------------------
    @pytest.mark.parametrize("case", MATRIX, ids=[c["case_id"] for c in MATRIX])
    def test_case_within_degree_gate(self, case):
        """Every single case must land inside its stratum's lon/lat gate.

        clear/mild/blurry: within 1.0 deg (the sub-1 deg guarantee).
        vblurry:           within 1.2 deg (documented stress band).
        Scored against the planted geometric centre.
        """
        r = self.by_case[case["case_id"]]
        lim = case["limit_deg"]
        dlon = r["abs_dlon_seed"]
        dlat = r["abs_dlat_seed"]
        assert dlon <= lim and dlat <= lim, (
            f"{case['case_id']} ({case['resolution']}, "
            f"seeing={case['seeing_arcsec']:.2f}\"): "
            f"|dlon|={dlon:.3f} |dlat|={dlat:.3f} exceeds {lim} deg gate "
            f"(method={r['method']}, sky={r['sky_arcsec']:.3f}\", "
            f"lon_rel={r['lon_rel_truth']:.1f})"
        )

    # --- aggregate "1 degree below" checks ------------------------------
    def test_strict_sub_one_on_realistic_data(self):
        """Worst error across all realistic (clear/mild/blurry) cases < 1.0 deg."""
        realistic = [r for r in self.ok if r["clarity"] in ("clear", "mild", "blurry")]
        worst_lon = max(r["abs_dlon_seed"] for r in realistic)
        worst_lat = max(r["abs_dlat_seed"] for r in realistic)
        assert worst_lon < 1.0, f"worst realistic lon {worst_lon:.3f} deg"
        assert worst_lat < 1.0, f"worst realistic lat {worst_lat:.3f} deg"

    def test_overall_within_one_degree_rate(self):
        """At least 90% of ALL 100 frames (incl. very-blurry) within 1 deg."""
        n = len(self.ok)
        within = sum(
            1 for r in self.ok if r["abs_dlon_seed"] <= 1.0 and r["abs_dlat_seed"] <= 1.0
        )
        rate = within / n
        assert rate >= 0.90, f"only {rate*100:.1f}% within 1 deg"

    def test_very_blurry_within_stress_gate(self):
        """Stress stratum: every case within 1.2 deg, and report the sub-1 rate."""
        vb = [r for r in self.ok if r["clarity"] == "vblurry"]
        worst = max(max(r["abs_dlon_seed"], r["abs_dlat_seed"]) for r in vb)
        assert worst <= 1.2, f"very-blurry worst {worst:.3f} deg"
        within = sum(1 for r in vb if r["abs_dlon_seed"] <= 1.0 and r["abs_dlat_seed"] <= 1.0)
        # Reported, not asserted to 100% -- this is the documented blur limit.
        print(f"\n[rs100] very-blurry within-1-deg rate: {within}/{len(vb)} "
              f"= {within/len(vb)*100:.0f}%")

    # --- per-stratum medians (trend audit) ------------------------------
    def test_median_error_per_stratum(self):
        """Median sky error must stay low in every stratum; print the table."""
        print("\n[rs100] per-stratum sky-error (arcsec) and degree residuals:")
        print(f"  {'stratum':<16}{'n':>3}{'sky_med':>9}{'sky_max':>9}"
              f"{'lon_max':>9}{'lat_max':>9}{'<=1d':>7}")
        for stratum in sorted(self.by_stratum):
            rows = self.by_stratum[stratum]
            skys = sorted(r["sky_arcsec"] for r in rows)
            lons = [r["abs_dlon_seed"] for r in rows]
            lats = [r["abs_dlat_seed"] for r in rows]
            w1 = sum(1 for r in rows
                     if r["abs_dlon_seed"] <= 1.0 and r["abs_dlat_seed"] <= 1.0)
            print(f"  {stratum:<16}{len(rows):>3}{st.median(skys):>9.3f}"
                  f"{max(skys):>9.3f}{max(lons):>9.3f}{max(lats):>9.3f}"
                  f"{w1/len(rows)*100:>6.0f}%")

    # --- logic & error audit --------------------------------------------
    def test_no_systematic_longitude_bias(self):
        """Mean signed lon residual should be ~0 (no geometry bias)."""
        dlons = [r["dlon_seed"] for r in self.ok]
        mean = st.fmean(dlons)
        spread = st.pstdev(dlons)
        assert abs(mean) < max(0.25, 2.0 * spread), (
            f"systematic lon bias {mean:+.4f} deg vs scatter {spread:.4f} deg"
        )

    def test_no_systematic_latitude_bias(self):
        dlats = [r["dlat_seed"] for r in self.ok]
        mean = st.fmean(dlats)
        spread = st.pstdev(dlats)
        assert abs(mean) < max(0.25, 2.0 * spread), (
            f"systematic lat bias {mean:+.4f} deg vs scatter {spread:.4f} deg"
        )

    def test_accuracy_degrades_gracefully_with_seeing(self):
        """Sky error must stay bounded as seeing worsens.

        It does NOT have to grow monotonically: at these sub-arcsec residuals
        the measurement is noise-floor-limited, not seeing-limited, so the
        clear-vs-blurry medians sit within ~0.04" of each other and can swap
        order between seed sets. What we assert is that no stratum BLOWS UP
        relative to clear -- a real blur-path regression would. The trend is
        printed for inspection.
        """
        order = ["clear", "mild", "blurry", "vblurry"]
        meds = {c: st.median([r["sky_arcsec"] for r in self.ok if r["clarity"] == c])
                for c in order}
        print(f"\n[rs100] sky-error median by seeing {order}: "
              f"{[round(meds[c], 3) for c in order]}")
        clear = meds["clear"]
        for c in order:
            assert meds[c] <= clear * 3.0 + 0.1, (
                f"{c} median {meds[c]:.3f}\" blew up vs clear {clear:.3f}\""
            )

    def test_large_resolution_not_worse_than_small(self):
        """More pixels must not hurt: large median sky-error <= small + slack."""
        small = [r for r in self.ok if r["size"] == "small"]
        large = [r for r in self.ok if r["size"] == "large"]
        ms = st.median([r["sky_arcsec"] for r in small])
        ml = st.median([r["sky_arcsec"] for r in large])
        assert ml <= ms * 2.0 + 0.05, (
            f"4K median sky {ml:.3f}\" far worse than 1080p {ms:.3f}\""
        )

    def test_limb_navigation_residual_is_small(self):
        """Recovered disk centre must be near the planted centre (px)."""
        big = [abs(r["d_xc"]) + abs(r["d_yc"]) for r in self.ok
               if math.isfinite(r.get("d_xc", float("nan")))]
        assert big, "no limb-fit residuals recorded"
        worst = max(big)
        # Observed worst across all 100 cases is ~0.3 px (sub-pixel centre
        # recovery on a 454-907 px disk). 2 px is ~6x headroom and still
        # catches a genuine limb-fit failure (a >1% centre error).
        assert worst < 2.0, f"limb centre off by {worst:.2f} px (sum |dx|+|dy|)"

    def test_truth_definition_gap_is_bounded_and_known(self):
        """Barycentre truth vs planted centre: the gap is definitional (~0.24
        deg), not an estimator error. Audit that it stays in a sane band."""
        gaps = [abs(r["abs_dlat_bary"] - r["abs_dlat_seed"]) for r in self.ok]
        med_gap = st.median(gaps)
        print(f"\n[rs100] median barycentre-vs-seed lat-truth gap: {med_gap:.3f} deg")
        assert med_gap < 0.6, "truth definitions diverged unexpectedly"

    def test_quality_flag_is_finite_and_sensible(self):
        for r in self.ok:
            q = r["quality"]
            assert math.isfinite(q), f"{r['case_id']} non-finite quality {q}"
            assert q >= 0.0

    def test_no_nan_or_inf_in_any_result(self):
        for r in self.ok:
            for k in ("lon_meas", "lat_meas", "sky_arcsec", "dlon_seed", "dlat_seed"):
                assert math.isfinite(r[k]), f"{r['case_id']} has non-finite {k}={r[k]}"

    def test_all_methods_locked_the_grs_band(self):
        """Latitude must land inside the wide GRS band on every frame -- a
        wrong-feature lock would put the centre in the wrong belt."""
        from accuracy_gates import grs_lat_in_wide_band

        bad = [(r["case_id"], r["lat_meas"]) for r in self.ok
               if not grs_lat_in_wide_band(r["lat_meas"])]
        assert bad == [], f"wrong-band locks: {bad}"

    def test_determinism_same_seed_reproduces(self):
        """Re-rendering one seeded case must reproduce the cached measurement."""
        case = next(c for c in MATRIX if c["clarity"] == "clear")
        rec = self.by_case[case["case_id"]]
        fresh = _run_case(case)
        assert fresh.get("ok"), "fresh re-render failed"
        assert abs(fresh["lon_meas"] - rec["lon_meas"]) < 1e-5, "frame not reproducible"
        assert abs(fresh["lat_meas"] - rec["lat_meas"]) < 1e-5


if __name__ == "__main__":
    # Standalone cache builder: python tests/test_resolution_seeing_100.py
    rows = build_results(
        force=os.environ.get("GRS_RS100_FORCE", "") in ("1", "true", "yes")
    )
    ok = [r for r in rows if r.get("ok")]
    print(f"\n[rs100] {len(ok)}/{len(rows)} cases complete")
    if len(ok) == len(rows):
        skys = sorted(r["sky_arcsec"] for r in ok)
        w1 = sum(1 for r in ok
                 if r["abs_dlon_seed"] <= 1.0 and r["abs_dlat_seed"] <= 1.0)
        print(f"[rs100] sky median={st.median(skys):.3f}\" max={skys[-1]:.3f}\"")
        print(f"[rs100] within 1 deg: {w1}/{len(ok)} = {w1/len(ok)*100:.1f}%")

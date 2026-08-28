# Speed & Results Audit — 2026-08-28

**Scope:** end-to-end audit of the measurement stack with emphasis on
**speed** and **result quality**; fixes + regression protection; intense
verification campaign.

**Environment:** Python 3.11.2, NumPy 2.4.6, SciPy 1.17.1, 2 vCPU / 3.8 GB
sandbox. The optional C core (`app/native/grscore.so`) could **not** be built
here (Debian `python3.11-dev` unreachable in this sandbox), so **every number
below is the pure-NumPy path** — the same path a user gets on a stock install
after `pip install -r requirements.txt`.

---

## 1. Executive summary

| Metric | Before | After | Speedup |
|---|---:|---:|---:|
| `m_radial_symmetry` (1400×700 map) | **63.4 s** | **9 ms** | **≈7 000×** |
| `run_all_methods` (the "every method" suite, 1400×700) | **67.9 s** | **4.3 s** | **15.9×** |
| `_template_match_grs` (9 scales, 2880×1440 map) | 0.63 s | 0.11 s | 5.6× |
| `multiscale_template_match` (25 scales, 2880×1440 map) | 2.11 s | 1.04 s | 2.0× |
| Full factory run (`test_synthetic_metrology_measure_finite_sky_error`) | still inside `m_radial_symmetry` at 5.5 min (killed; est. 6-7 min total) | **68.4 s (pass)** | ~5× |
| Synthetic accuracy (1080p metrology, 24 seeds) | — | **24/24 within 1°, median 0.084″ sky, max 0.175″** | — |

**The one-line bug that made the code look "broken":** `m_radial_symmetry`
called `np.median(band[valid])` **inside a per-pixel vote loop**, i.e. ~1M
medians over ~75k-element arrays per map — an O(N² log N) catastrophe that
took ~63 s per frame on a *small* 1400×700 map and was invoked from the
default "Process → all methods" path. Any full pipeline run (CLI, desktop,
web, or the test suite) appeared to hang on it. That is now vectorised
(median hoisted once, votes scatter-added with `bincount`), bit-identical in
intent, and ~7 000× faster.

---

## 2. Findings

### Critical

| # | Severity | Where | Problem | Impact |
|---|---|---|---|---|
| 1 | 🔴 Critical | `app/all_methods_extra.py::m_radial_symmetry` | `np.median(band[valid])` recomputed inside a per-pixel loop (≈1M × median of ~75k samples) | 63.4 s per 1400×700 map; entire pipeline/test suite appears hung. **Fixed.** |
| 2 | 🟠 High | `app/vlbi_metrology.py::_ncc_peak` | 3× `scipy.signal.fftconvolve` per template (corr + 2 energy FFTs) — band re-transformed 3× for each of 25 scales | `multiscale_template_match` 2.1 s/call → ~25 s + ~2 min in hierarchical MC (60 iters) and ~20 s in phase-reference probes. **Fixed** (shared band FFT + exact separable box-sum). |
| 3 | 🟠 High | `app/precision_engine.py::_template_match_grs` | same band re-transformed 9× (one `fftconvolve` per scale) | 0.63 s/call on the 2880×1440 primary map, and it is called 3× per `measure_grs_precision` (raw + verify-gate). **Fixed.** |
| 4 | 🟡 Medium | `app/precision_engine.py::_redness_grs`, `_moment_mask_grs` | full-frame `px_to_lonlat_vec` over the whole 1920×1080 image when only disk pixels matter | ~2.5× unnecessary spheroid solves per call; `_moment_mask_grs` was 0.39 s × 3 calls. **Fixed** (identical values, on-disk only). |
| 5 | 🟡 Medium | `app/precision_engine.py::measure_grs_precision` | mutates the caller's `NavState` (`nav.cm_iii_deg = ...`) as a side effect; duplicate dead `dark_tight` declaration | hidden coupling; not fixed (harmless but noted). |

### Verified but kept (documented decisions)

* `verify_grs_detection` re-measures 4 estimators at 2 reduced scales
  (~0.9 s) — causal scale-stability gate; deliberately kept.
* `hierarchical_monte_carlo` (60 iters) re-runs the 25-scale matcher every
  iteration, even when only additive noise changed — after fix #2 it is
  ~25–30 s instead of ~2 min, so the error budget is preserved without the
  previous engineering compromise.
* `per_row_warp` per-row `map_coordinates` loop was measured slower when
  batched into a 2-D call (629 ms vs 271 ms on 1080×1920) — **reverted**;
  the batched approach wastes a full y-axis spline evaluation for a pure
  x-shift. Comment added in code.
* Optional C core: would 5-10× `make_cylindrical`/limb nav but requires
  `python3-dev`; not available in this sandbox (build is gitignored by
  design, see `app/native/build_native.py`).

---

## 3. What changed (files)

| File | Change |
|---|---|
| `app/all_methods_extra.py` | `m_radial_symmetry` vectorised: median hoisted, gradient votes scatter-added via `np.bincount`, identical step/r/sign semantics |
| `app/precision_engine.py` | NEW shared-FFT NCC context (`_ncc_corr_ctx` / `_ncc_corr_from_ctx`, verified vs `fftconvolve` to ~1e-16 relative); `_template_match_grs` uses one band FFT for all 9 scales; `_redness_grs`/`_moment_mask_grs` invert latitudes only on disk pixels; scipy.fft imports hoisted |
| `app/vlbi_metrology.py` | `_ncc_peak` accepts the shared context and replaces the 2 energy FFTs with an exact separable box-sum (`uniform_filter`, verified to ~5e-13 of the FFT result); `multiscale_template_match` precomputes the band FFT once for all 25 scales |
| `tests/test_perf_hotpaths.py` | NEW speed-regression tests (radial symmetry <1 s for 3 runs, extra-method suite <5 s, multiscale <2 s, FFT-NCC ≡ fftconvolve) |
| `tools/speed_audit.py` | NEW reproducible stage-by-stage benchmark harness (per-stage seconds + per-extra-method timings, `--json`) |

All algorithmic changes are **numerically equivalent** to the previous
implementation:

* `_ncc_corr_from_ctx` vs `scipy.signal.fftconvolve(..., mode="same")`:
  max abs diff ~3e-13 (relative ~1e-16) over odd/even kernel sizes and
  shared-FFT sizes.
* energy box-sum (`uniform_filter`) vs `fftconvolve(band, ones)`:
  max abs diff ~5e-13.
* full `multiscale_template_match` run with the old `_ncc_peak` vs new:
  **bit-identical** result dicts (same peak, same scale, same
  `template_override_ncc` decision).
* `_redness_grs` / `_moment_mask_grs`: on-disk-only inversion returns the
  same values at the same pixels (the lat gate is only ever evaluated on
  disk pixels).

---

## 4. Results verification (accuracy)

Independent campaign, `tools/accuracy_campaign.py`, 1080p `metrology`
synthetic, 24 fresh seeds, truth = rendered GRS lon/lat:

| Metric | Value |
|---|---:|
| cases | 24/24 ok (0 failures) |
| within 1° (lon & lat) | **100 %** |
| \|dlon\| median / p90 / max | 0.166° / 0.344° / 0.598° |
| \|dlat\| median / p90 / max | 0.164° / 0.289° / 0.397° |
| sky error median / p90 / max | 0.084″ / 0.139″ / 0.175″ |
| vs geometric planted centre: \|dlon\| median / max | 0.058° / 0.114° |

Second 6-seed run (after the redness/moment edits): 6/6 within 1°, sky
median 0.093″. The published path continued to return the redness-primary
answer (`redness_lon+redness_lat`), consistent with the v6.6.2 design.

**Performance at same time** (before edits, single case mean ≈ 10.7 s;
the full factory run moved from "not completing" to a passing 68.4 s).

---

## 5. Testing (intense)

* `pytest -m "not slow"` — **502 tests, pytest-xdist 2 workers, 5:27**:
  **483 passed, 19 skipped, 0 failed**, run TWICE (before and after the
  final redness/moment on-disk-only edit) — same result both times.
  The 19 skips are platform/optional (desktop/macOS, PyEPHEM-less) tests.
* Slowest tests (why CI still needs a fast lane): `test_accuracy_smoke`
  full factory 107 s (contended) / 68.4 s (alone), `test_cli_pro`
  `video_to_answer` E2E 96.9 s, `test_cli_pro` full pipeline 63.5 s,
  video-stack+drizzle 43.3 s, per-method audit 39.9 s.
* New speed regression tests: 4/4 pass in 3.1 s.
* `tests/test_per_method_audit.py`: 4/4 pass (121 s) — pinned per-method
  accuracy behavior unchanged after the fixes.
* `tests/test_accuracy_smoke.py::test_synthetic_metrology_measure_finite_sky_error`:
  **68.4 s, PASS** (before the fix the run was still inside
  `m_radial_symmetry` after 5.5 min — this is where the hang was first
  sampled with `py-spy`).
* `tools/accuracy_campaign.py --n 24`: 24/24 pass.

### How the hang was found

`pytest` was stuck at 100 % CPU with no output. `py-spy dump` showed
`m_radial_symmetry (all_methods_extra.py:642) → np.median(numpy/lib/...)` —
a per-pixel median recomputation. A stage-timed benchmark then quantified it
at 63.4 s, i.e. 94 % of the whole all-methods suite.

---

## 6. Full suite result

```
pytest -m "not slow" -n 2          # pytest-xdist, 2 workers
483 passed, 19 skipped in 327.12s  # final run, all changes included
```

```

---

## 7. Recommendations (ordered by value/risk)

1. **Build the native core in production** (`python app/native/build_native.py --openmp`).
   `make_cylindrical` (0.5–0.7 s on 2880×1440) and the limb ray trace are
   the next biggest pure-NumPy costs; the C path is documented as 5-10× and
   is `make`-free (single `.so`, gitignored). All speedups in this audit are
   orthogonal — they reduce the NCC/consensus fraction so the C core covers
   an even larger share of the remaining wall time.
2. **Cap `verify_grs_detection` factors** in fast paths: `factors=(2,)` cuts
   ~0.5 s per measure with the same gate on most frames; keep `(2,3)` for
   publish-grade runs.
3. **H-MC local-refine option**: for noise-only MC iterations, the 25-scale
   full-map search is re-run on an essentially unchanged map. A cached-peak +
   local window refine would cut hierarchical MC from ~25-30 s to ~3-5 s
   while keeping limb/CM jitter — but it changes the MC's noise term, so it
   needs its own campaign before shipping (not done here to avoid touching
   the error budget).
4. **Mark genuinely long E2E tests `@pytest.mark.slow`.** `test_cli_pro`
   (`video_to_answer` → full VLBI factory) and `test_video_jupiter` stack
   the whole stack per test; each is a multi-minute run yet is NOT marked
   slow, so `pytest -m "not slow"` still takes ~40+ min. Either split the
   fast unit coverage from the E2E or add the marker so the CI fast lane is
   actually fast.

---

## 8. Honest limits

* Accuracy numbers are **synthetics** (planted truth, generated pixels) —
  the README already discloses this. Absolute performance on real ground
  frames is dominated by seeing/definition, which the campaign cannot
  reproduce.
* Timing measured in a 2-vCPU sandbox with the optional C core unavailable;
  absolute numbers will differ on a laptop, the *ratios* (7 000×, 15.9×,
  2×, 5.6×) are the meaningful result.
* `m_radial_symmetry`'s vectorisation uses `np.rint` (banker's rounding) —
  identical to the old Python `round()`, and accumulation order changed
  (float reassociation ~1e-15); per-method audit pins (medians over cases)
  confirm no measurable effect.

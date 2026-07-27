# Module API: `hard_synth_suite.py`

**Path:** `app/hard_synth_suite.py`  
**Lines of code:** 383  
**Generated:** 2026-07-14T14:36:01.277076+00:00

## Module documentation

Hard synthetic stress suite — Harvard-style calibration under mismatch physics
==============================================================================

Friendly synthetics (same projection as the measurer, mild seeing, GRS on CM)
**understate** real error. This suite injects controlled mismatches and asks:

  1) Does truth fall inside the reported 1σ / 2σ error bars?  (coverage)
  2) What is residual sky error under each stress family?
  3) Which stress dominates the floor?

Stress families:
  A) CM error        — wrong System III zero (±0.5–2°)
  B) Extra seeing    — Gaussian blur beyond synth default
  C) Near-limb GRS   — force GRS far from CM (via re-measure with CM shift)
  D) Noise / SNR     — additive Gaussian noise
  E) Pole / sub-lat  — wrong orientation in nav
  F) Combined night  — A+B+D mild together

Output: JSON + text calibration report with coverage rates.

## Overview statistics

| Item | Count |
|------|------:|
| Classes | 2 |
| Top-level functions | 5 |
| Methods | 2 |

## Symbol index

- **class** `StressCase` — line 46
  - `StressCase.to_dict()` — line 51
- **class** `StressResult` — line 56
  - `StressResult.to_dict()` — line 72
- **function** `_blur()` — line 76
- **function** `apply_image_stress()` — line 86
- **function** `run_one_measure()` — line 102
- **function** `default_stress_matrix()` — line 144
- **function** `run_hard_synth_suite()` — line 170

## Classes (full detail)

### class `StressCase`

- **Defined at:** line 46
- **Methods:** 1

_No class docstring._

#### Methods

##### `StressCase.to_dict(self)`

- **Line:** 51–52

_No docstring. Inferred role: member of `StressCase` used by the hard_synth_suite subsystem._

**Signature notes:** Accepts parameters `self`. See source `app/hard_synth_suite.py` around line 51 for implementation.

**Related features:** Any feature that imports `hard_synth_suite` may call this method.

---

### class `StressResult`

- **Defined at:** line 56
- **Methods:** 1

_No class docstring._

#### Methods

##### `StressResult.to_dict(self)`

- **Line:** 72–73

_No docstring. Inferred role: member of `StressResult` used by the hard_synth_suite subsystem._

**Signature notes:** Accepts parameters `self`. See source `app/hard_synth_suite.py` around line 72 for implementation.

**Related features:** Any feature that imports `hard_synth_suite` may call this method.

---

## Top-level functions (full detail)

### `_blur(im, sigma)`

- **Module:** `hard_synth_suite.py`
- **Line:** 76–83

_No docstring in source._ This function is part of `hard_synth_suite.py`. Open `app/hard_synth_suite.py` at line 76 for the full implementation.

**Parameters:** `im, sigma`

**How to find callers:** search the repo for `_blur(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `apply_image_stress(image, seeing_sigma_px, noise_sigma, seed)`

- **Module:** `hard_synth_suite.py`
- **Line:** 86–99

_No docstring in source._ This function is part of `hard_synth_suite.py`. Open `app/hard_synth_suite.py` at line 86 for the full implementation.

**Parameters:** `image, seeing_sigma_px, noise_sigma, seed`

**How to find callers:** search the repo for `apply_image_stress(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `run_one_measure(image, truth, cm_err_deg, sub_lat_err_deg, north_pa_err_deg, injection_trials, mc_iter, seed)`

- **Module:** `hard_synth_suite.py`
- **Line:** 102–141

**Docstring:**

Returns meas_lon, meas_lat, sigma_sky, grade.

**Parameters:** `image, truth, cm_err_deg, sub_lat_err_deg, north_pa_err_deg, injection_trials, mc_iter, seed`

**How to find callers:** search the repo for `run_one_measure(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `default_stress_matrix()`

- **Module:** `hard_synth_suite.py`
- **Line:** 144–167

_No docstring in source._ This function is part of `hard_synth_suite.py`. Open `app/hard_synth_suite.py` at line 144 for the full implementation.

**Parameters:** `(none / *args via definition)`

**How to find callers:** search the repo for `default_stress_matrix(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `run_hard_synth_suite(out_dir, base_seed, resolution, cases, injection_trials, mc_iter, user_time_iso)`

- **Module:** `hard_synth_suite.py`
- **Line:** 170–383

**Docstring:**

Generate one HQ synthetic, then run all stress cases.
Returns full report dict and writes files under out_dir.

**Parameters:** `out_dir, base_seed, resolution, cases, injection_trials, mc_iter, user_time_iso`

**How to find callers:** search the repo for `run_hard_synth_suite(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---


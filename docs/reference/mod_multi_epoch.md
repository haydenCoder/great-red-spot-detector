# Module API: `multi_epoch.py`

**Path:** `app/multi_epoch.py`  
**Lines of code:** 470  
**Generated:** 2026-07-14T14:36:01.277523+00:00

## Module documentation

Multi-epoch differential GRS tracking (VLBI phase-reference across nights)
=========================================================================

Absolute System III on a single night is limited by CM ephemeris zero-point.
**Differentials** across epochs cancel common-mode errors the way VLBI phase
referencing cancels station delays:

  Δlon(t) = lon(t) − lon(t0)   (circular)
  drift model: lon(t) = lon0 + ω·(t−t0) + seasonal terms (optional)
  RTS / Kalman smoother for trajectory under measurement noise

Use cases:
  - GRS drift rate (°/day) for JUPOS-style science
  - Night-to-night residual after derotation (internal consistency)
  - Publication table of bias-corrected epochs with formal σ

API: load epoch JSON list → differential series → drift fit → smooth → report

## Overview statistics

| Item | Count |
|------|------:|
| Classes | 2 |
| Top-level functions | 9 |
| Methods | 2 |

## Symbol index

- **class** `EpochMeasure` — line 42
  - `EpochMeasure.to_dict()` — line 59
- **class** `DifferentialSeries` — line 64
  - `DifferentialSeries.to_dict()` — line 79
- **function** `_t_seconds()` — line 83
- **function** `epoch_from_research_json()` — line 93
- **function** `load_epochs_from_dir()` — line 172
- **function** `load_epochs_from_list()` — line 191
- **function** `weighted_linear_fit()` — line 212
- **function** `kalman_rts_1d()` — line 240
- **function** `build_differential_series()` — line 286
- **function** `measure_epoch_image()` — line 397
- **function** `write_multi_epoch_report()` — line 442

## Classes (full detail)

### class `EpochMeasure`

- **Defined at:** line 42
- **Methods:** 1

**Class docstring:**

One calibrated GRS measurement.

#### Methods

##### `EpochMeasure.to_dict(self)`

- **Line:** 59–60

_No docstring. Inferred role: member of `EpochMeasure` used by the multi_epoch subsystem._

**Signature notes:** Accepts parameters `self`. See source `app/multi_epoch.py` around line 59 for implementation.

**Related features:** Any feature that imports `multi_epoch` may call this method.

---

### class `DifferentialSeries`

- **Defined at:** line 64
- **Methods:** 1

**Class docstring:**

Phase-referenced differentials relative to reference epoch.

#### Methods

##### `DifferentialSeries.to_dict(self)`

- **Line:** 79–80

_No docstring. Inferred role: member of `DifferentialSeries` used by the multi_epoch subsystem._

**Signature notes:** Accepts parameters `self`. See source `app/multi_epoch.py` around line 79 for implementation.

**Related features:** Any feature that imports `multi_epoch` may call this method.

---

## Top-level functions (full detail)

### `_t_seconds(iso)`

- **Module:** `multi_epoch.py`
- **Line:** 83–90

_No docstring in source._ This function is part of `multi_epoch.py`. Open `app/multi_epoch.py` at line 83 for the full implementation.

**Parameters:** `iso`

**How to find callers:** search the repo for `_t_seconds(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `epoch_from_research_json(path, epoch_id)`

- **Module:** `multi_epoch.py`
- **Line:** 93–169

**Docstring:**

Ingest research_grade.json / job_result.json / vlbi_metrology.json.

**Parameters:** `path, epoch_id`

**How to find callers:** search the repo for `epoch_from_research_json(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `load_epochs_from_dir(directory)`

- **Module:** `multi_epoch.py`
- **Line:** 172–188

**Docstring:**

Scan job_*/synth_*/ for research_grade.json or vlbi_metrology.json.

**Parameters:** `directory`

**How to find callers:** search the repo for `load_epochs_from_dir(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `load_epochs_from_list(items)`

- **Module:** `multi_epoch.py`
- **Line:** 191–209

_No docstring in source._ This function is part of `multi_epoch.py`. Open `app/multi_epoch.py` at line 191 for the full implementation.

**Parameters:** `items`

**How to find callers:** search the repo for `load_epochs_from_list(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `weighted_linear_fit(t, y, w)`

- **Module:** `multi_epoch.py`
- **Line:** 212–237

**Docstring:**

y = a + b t
Returns a, b, sigma_a, sigma_b (approx).

**Parameters:** `t, y, w`

**How to find callers:** search the repo for `weighted_linear_fit(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `kalman_rts_1d(t, z, r, q_scale)`

- **Module:** `multi_epoch.py`
- **Line:** 240–283

**Docstring:**

Random-walk + rate state Kalman smoother (RTS).
State [x, v]; measurement x.
t in days, z measured lon unwrapped.

**Parameters:** `t, z, r, q_scale`

**How to find callers:** search the repo for `kalman_rts_1d(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `build_differential_series(epochs, ref_index, smooth)`

- **Module:** `multi_epoch.py`
- **Line:** 286–394

**Docstring:**

Phase-reference all epochs to ref: differentials cancel common CM bias.
Fit linear drift; optional RTS smoother on unwrapped lon.

**Parameters:** `epochs, ref_index, smooth`

**How to find callers:** search the repo for `build_differential_series(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `measure_epoch_image(image, user_time_iso, time_error_seconds, cm_override, winjupos_path)`

- **Module:** `multi_epoch.py`
- **Line:** 397–439

**Docstring:**

Run full VLBI-grade measure for one image and package as EpochMeasure.

**Parameters:** `image, user_time_iso, time_error_seconds, cm_override, winjupos_path`

**How to find callers:** search the repo for `measure_epoch_image(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `write_multi_epoch_report(path, series, epochs)`

- **Module:** `multi_epoch.py`
- **Line:** 442–470

_No docstring in source._ This function is part of `multi_epoch.py`. Open `app/multi_epoch.py` at line 442 for the full implementation.

**Parameters:** `path, series, epochs`

**How to find callers:** search the repo for `write_multi_epoch_report(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---


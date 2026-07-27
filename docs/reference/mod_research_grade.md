# Module API: `research_grade.py`

**Path:** `app/research_grade.py`  
**Lines of code:** 733  
**Generated:** 2026-07-14T14:36:01.278352+00:00

## Module documentation

Research-grade GRS metrology layer
==================================

What actually moves a result from "impressive demo" toward something a
research group would trust is not more wavelets or more MC iterations on the
same broken definition. It is:

  1) BIAS calibration via blind injection–recovery on the *same* image
  2) Multi-definition ensemble → systematic floor (definition scatter)
  3) Multi-filter residual "closure" (R/G/B consistency after DCR)
  4) Explicit error budget: random (MC) + systematic (definitions) + bias (injection)
  5) Publication bundle: methods, seeds, hashes, all intermediates

The distinctive idea (often underused in amateur pipelines):

  **Blind synthetic injection into the real residual field.**
  You know the truth of the *injected* oval. Recovering it measures *your*
  pipeline's bias under *tonight's* PSF, noise, limb, and code path.
  That bias is subtracted from the real GRS measurement, and the recovery
  scatter becomes a calibrated random error — not a guess.

This is closer to how careful experimental physics treats instruments than
to "stack harder and claim σ from photon noise."

Honest scope:
  - Ground-based extended cloud feature, not VLBI compact source.
  - Target: 1–2″ sky *with calibrated bias*, transparent systematics.
  - No institution will "endorse" software; they will check whether your
    error bars cover truth in injection tests and multi-definition scatter.

## Overview statistics

| Item | Count |
|------|------:|
| Classes | 3 |
| Top-level functions | 9 |
| Methods | 3 |

## Symbol index

- **class** `DefinitionResult` — line 71
  - `DefinitionResult.to_dict()` — line 80
- **class** `InjectionTrial` — line 85
  - `InjectionTrial.to_dict()` — line 94
- **class** `ResearchGradeResult` — line 99
  - `ResearchGradeResult.to_dict()` — line 128
- **function** `_hash_array()` — line 132
- **function** `inject_dark_oval()` — line 137
- **function** `run_definition_suite()` — line 182
- **function** `consensus_from_definitions()` — line 237
- **function** `_recover_near_lonlat()` — line 287
- **function** `blind_injection_calibration()` — line 353
- **function** `filter_closure_rgb()` — line 424
- **function** `run_research_grade()` — line 486
- **function** `write_publication_bundle()` — line 706

## Classes (full detail)

### class `DefinitionResult`

- **Defined at:** line 71
- **Methods:** 1

_No class docstring._

#### Methods

##### `DefinitionResult.to_dict(self)`

- **Line:** 80–81

_No docstring. Inferred role: member of `DefinitionResult` used by the research_grade subsystem._

**Signature notes:** Accepts parameters `self`. See source `app/research_grade.py` around line 80 for implementation.

**Related features:** Any feature that imports `research_grade` may call this method.

---

### class `InjectionTrial`

- **Defined at:** line 85
- **Methods:** 1

_No class docstring._

#### Methods

##### `InjectionTrial.to_dict(self)`

- **Line:** 94–95

_No docstring. Inferred role: member of `InjectionTrial` used by the research_grade subsystem._

**Signature notes:** Accepts parameters `self`. See source `app/research_grade.py` around line 94 for implementation.

**Related features:** Any feature that imports `research_grade` may call this method.

---

### class `ResearchGradeResult`

- **Defined at:** line 99
- **Methods:** 1

**Class docstring:**

Publication-oriented product.

#### Methods

##### `ResearchGradeResult.to_dict(self)`

- **Line:** 128–129

_No docstring. Inferred role: member of `ResearchGradeResult` used by the research_grade subsystem._

**Signature notes:** Accepts parameters `self`. See source `app/research_grade.py` around line 128 for implementation.

**Related features:** Any feature that imports `research_grade` may call this method.

---

## Top-level functions (full detail)

### `_hash_array(a)`

- **Module:** `research_grade.py`
- **Line:** 132–134

_No docstring in source._ This function is part of `research_grade.py`. Open `app/research_grade.py` at line 132 for the full implementation.

**Parameters:** `a`

**How to find callers:** search the repo for `_hash_array(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `inject_dark_oval(image, nav, lon_iii, lat_deg, length_deg, width_deg, depth)`

- **Module:** `research_grade.py`
- **Line:** 137–179

**Docstring:**

Inject a smooth dark oval at (lon, lat) into a copy of the image.
Used for blind recovery calibration on the real residual field.

**Parameters:** `image, nav, lon_iii, lat_deg, length_deg, width_deg, depth`

**How to find callers:** search the repo for `inject_dark_oval(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `run_definition_suite(image, nav)`

- **Module:** `research_grade.py`
- **Line:** 182–234

**Docstring:**

Several operational definitions of "where is the GRS".
Scatter among them ≈ systematic floor (definition uncertainty).

**Parameters:** `image, nav`

**How to find callers:** search the repo for `run_definition_suite(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `consensus_from_definitions(defs)`

- **Module:** `research_grade.py`
- **Line:** 237–284

**Docstring:**

Returns lon, lat, L, W, sys_lon_deg, sys_lat_deg.

Point estimate = single best definition (engine_weighted > template > weight).
Systematic floor = scatter of *other* definitions about that primary.
Do NOT average incompatible definitions into the reported position.

**Parameters:** `defs`

**How to find callers:** search the repo for `consensus_from_definitions(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `_recover_near_lonlat(cyl, nav, lon_hint, lat_hint, lon_half_win, lat_half_win)`

- **Module:** `research_grade.py`
- **Line:** 287–350

**Docstring:**

Recover a dark feature *only* inside a lon/lat window around a hint.
Critical when the real GRS is also on the disk — global match would lock on it.

**Parameters:** `cyl, nav, lon_hint, lat_hint, lon_half_win, lat_half_win`

**How to find callers:** search the repo for `_recover_near_lonlat(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `blind_injection_calibration(image, nav, n_trials, seed, around_lon, around_lat)`

- **Module:** `research_grade.py`
- **Line:** 353–421

**Docstring:**

Blind injection–recovery with *local* recovery windows.

Injections are placed *away* from the known GRS so the real oval does not
steal the match. Recovery searches only near the injected truth.

**Parameters:** `image, nav, n_trials, seed, around_lon, around_lat`

**How to find callers:** search the repo for `blind_injection_calibration(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `filter_closure_rgb(channels, nav)`

- **Module:** `research_grade.py`
- **Line:** 424–483

**Docstring:**

Multi-filter residual consistency (optical 'closure'-like diagnostic).

Measure GRS independently in R, G, B. After removing a simple linear
dispersion model in 1/λ², residual scatter bounds unmodeled systematics.

**Parameters:** `channels, nav`

**How to find callers:** search the repo for `filter_closure_rgb(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `run_research_grade(image, nav, cm_iii_deg, distance_au, channels, injection_trials, mc_iter, seed, max_fidelity, factory_mode, user_time_iso, time_error_seconds, aperture_m, use_vlbi, winjupos_path, sub_lat_override, north_pa_override)`

- **Module:** `research_grade.py`
- **Line:** 486–703

**Docstring:**

SPIRE-M research-grade reduction for one epoch.

When max_fidelity/use_vlbi (default): VLBI-inspired optical stack
(oriented geometry, multi-scale NCC, phase-ref probes, hierarchical MC,
formal error budget). factory_mode: heavier probe + H-MC suite.

**Parameters:** `image, nav, cm_iii_deg, distance_au, channels, injection_trials, mc_iter, seed, max_fidelity, factory_mode, user_time_iso, time_error_seconds, aperture_m, use_vlbi, winjupos_path, sub_lat_override, north_pa_override`

**How to find callers:** search the repo for `run_research_grade(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `write_publication_bundle(path, result, extra)`

- **Module:** `research_grade.py`
- **Line:** 706–733

_No docstring in source._ This function is part of `research_grade.py`. Open `app/research_grade.py` at line 706 for the full implementation.

**Parameters:** `path, result, extra`

**How to find callers:** search the repo for `write_publication_bundle(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

## Large-module guide

`research_grade.py` is a large module (733 lines). When editing:

1. Prefer adding helpers near related symbols rather than new global state.
2. Keep I/O (files, network) at the edges.
3. After changes, run `python3 cli.py certify --n 15` from `app/`.


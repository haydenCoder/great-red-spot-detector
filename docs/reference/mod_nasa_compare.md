# Module API: `nasa_compare.py`

**Path:** `app/nasa_compare.py`  
**Lines of code:** 207  
**Generated:** 2026-07-14T14:36:01.277970+00:00

## Module documentation

NASA/JPL Horizons geometry compare + offline GRS trend model.

## Overview statistics

| Item | Count |
|------|------:|
| Classes | 1 |
| Top-level functions | 5 |
| Methods | 2 |

## Symbol index

- **class** `NASAComparison` — line 33
  - `NASAComparison.to_dict()` — line 43
  - `NASAComparison.grade()` — line 46
- **function** `_ssl_context()` — line 21
- **function** `grs_reference_model()` — line 55
- **function** `fetch_horizons()` — line 67
- **function** `compare_measurement_to_nasa()` — line 145
- **function** `write_comparison_report()` — line 201

## Classes (full detail)

### class `NASAComparison`

- **Defined at:** line 33
- **Methods:** 2

_No class docstring._

#### Methods

##### `NASAComparison.to_dict(self)`

- **Line:** 43–44

_No docstring. Inferred role: member of `NASAComparison` used by the nasa_compare subsystem._

**Signature notes:** Accepts parameters `self`. See source `app/nasa_compare.py` around line 43 for implementation.

**Related features:** Any feature that imports `nasa_compare` may call this method.

---

##### `NASAComparison.grade(self)`

- **Line:** 46–52

_No docstring. Inferred role: member of `NASAComparison` used by the nasa_compare subsystem._

**Signature notes:** Accepts parameters `self`. See source `app/nasa_compare.py` around line 46 for implementation.

**Related features:** Any feature that imports `nasa_compare` may call this method.

---

## Top-level functions (full detail)

### `_ssl_context()`

- **Module:** `nasa_compare.py`
- **Line:** 21–29

_No docstring in source._ This function is part of `nasa_compare.py`. Open `app/nasa_compare.py` at line 21 for the full implementation.

**Parameters:** `(none / *args via definition)`

**How to find callers:** search the repo for `_ssl_context(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `grs_reference_model(t)`

- **Module:** `nasa_compare.py`
- **Line:** 55–64

_No docstring in source._ This function is part of `nasa_compare.py`. Open `app/nasa_compare.py` at line 55 for the full implementation.

**Parameters:** `t`

**How to find callers:** search the repo for `grs_reference_model(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `fetch_horizons(t, timeout)`

- **Module:** `nasa_compare.py`
- **Line:** 67–142

**Docstring:**

Legacy wrapper — prefer ephemeris_pro.fetch_horizons_full for research geometry.

**Parameters:** `t, timeout`

**How to find callers:** search the repo for `fetch_horizons(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `compare_measurement_to_nasa(measured, user_time_iso, time_error_seconds)`

- **Module:** `nasa_compare.py`
- **Line:** 145–198

_No docstring in source._ This function is part of `nasa_compare.py`. Open `app/nasa_compare.py` at line 145 for the full implementation.

**Parameters:** `measured, user_time_iso, time_error_seconds`

**How to find callers:** search the repo for `compare_measurement_to_nasa(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `write_comparison_report(path, comp)`

- **Module:** `nasa_compare.py`
- **Line:** 201–207

_No docstring in source._ This function is part of `nasa_compare.py`. Open `app/nasa_compare.py` at line 201 for the full implementation.

**Parameters:** `path, comp`

**How to find callers:** search the repo for `write_comparison_report(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---


# Module API: `ephemeris_pro.py`

**Path:** `app/ephemeris_pro.py`  
**Lines of code:** 817  
**Generated:** 2026-07-14T14:36:01.274671+00:00

## Module documentation

Professional Jupiter ephemeris for Harvard-grade absolute System III work
=========================================================================

Priority chain (first success wins for each field, with provenance):

  1) Explicit overrides (cm_iii, distance, sub-lat, NP PA) — WinJUPOS / user paste
  2) WinJUPOS / JUPOS CSV or JSON table at epoch
  3) **SPICE auto** (spice_auto: online kernel download + spiceypy) — preferred absolute path
  4) NASA JPL Horizons full observer parse (Δ, light-time, sub-obs lon/lat, NP.ang)
  5) Analytical fallback (differentials OK; absolute CM zero may be offset)

SPICE kernels are auto-searched/downloaded — users never need to hunt NAIF files.
Horizons is **geometry**, not a GRS longitude product. GRS lon still comes from
your image measurement; CM III ties that measurement to System III.

## Overview statistics

| Item | Count |
|------|------:|
| Classes | 1 |
| Top-level functions | 14 |
| Methods | 2 |

## Symbol index

- **class** `ProEphemeris` — line 87
  - `ProEphemeris.to_dict()` — line 110
  - `ProEphemeris.to_vlbi_ephemeris_state()` — line 114
- **function** `wrap_deg()` — line 48
- **function** `wrap_diff()` — line 52
- **function** `_ssl_context()` — line 56
- **function** `parse_time()` — line 67
- **function** `analytical_geometry()` — line 139
- **function** `fetch_horizons_full()` — line 165
- **function** `parse_horizons_observer_text()` — line 245
- **function** `load_winjupos_table()` — line 361
- **function** `interpolate_winjupos_cm()` — line 414
- **function** `save_example_winjupos_template()` — line 469
- **function** `try_spice_geometry()` — line 484
- **function** `_try_spice_geometry_legacy()` — line 525
- **function** `resolve_pro_ephemeris()` — line 582
- **function** `write_ephemeris_report()` — line 795

## Classes (full detail)

### class `ProEphemeris`

- **Defined at:** line 87
- **Methods:** 2

**Class docstring:**

Observer-centric Jupiter geometry + provenance for publication.

#### Methods

##### `ProEphemeris.to_dict(self)`

- **Line:** 110–112

_No docstring. Inferred role: member of `ProEphemeris` used by the ephemeris_pro subsystem._

**Signature notes:** Accepts parameters `self`. See source `app/ephemeris_pro.py` around line 110 for implementation.

**Related features:** Any feature that imports `ephemeris_pro` may call this method.

---

##### `ProEphemeris.to_vlbi_ephemeris_state(self)`

- **Line:** 114–132

Bridge to vlbi_metrology.EphemerisState without circular import at module load.

**Signature notes:** Accepts parameters `self`. See source `app/ephemeris_pro.py` around line 114 for implementation.

**Related features:** Any feature that imports `ephemeris_pro` may call this method.

---

## Top-level functions (full detail)

### `wrap_deg(x)`

- **Module:** `ephemeris_pro.py`
- **Line:** 48–49

_No docstring in source._ This function is part of `ephemeris_pro.py`. Open `app/ephemeris_pro.py` at line 48 for the full implementation.

**Parameters:** `x`

**How to find callers:** search the repo for `wrap_deg(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `wrap_diff(a, b)`

- **Module:** `ephemeris_pro.py`
- **Line:** 52–53

_No docstring in source._ This function is part of `ephemeris_pro.py`. Open `app/ephemeris_pro.py` at line 52 for the full implementation.

**Parameters:** `a, b`

**How to find callers:** search the repo for `wrap_diff(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `_ssl_context()`

- **Module:** `ephemeris_pro.py`
- **Line:** 56–64

_No docstring in source._ This function is part of `ephemeris_pro.py`. Open `app/ephemeris_pro.py` at line 56 for the full implementation.

**Parameters:** `(none / *args via definition)`

**How to find callers:** search the repo for `_ssl_context(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `parse_time(s)`

- **Module:** `ephemeris_pro.py`
- **Line:** 67–83

_No docstring in source._ This function is part of `ephemeris_pro.py`. Open `app/ephemeris_pro.py` at line 67 for the full implementation.

**Parameters:** `s`

**How to find callers:** search the repo for `parse_time(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `analytical_geometry(t)`

- **Module:** `ephemeris_pro.py`
- **Line:** 139–158

_No docstring in source._ This function is part of `ephemeris_pro.py`. Open `app/ephemeris_pro.py` at line 139 for the full implementation.

**Parameters:** `t`

**How to find callers:** search the repo for `analytical_geometry(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `fetch_horizons_full(t, site_lon, site_lat, site_elev_m, timeout, force)`

- **Module:** `ephemeris_pro.py`
- **Line:** 165–242

**Docstring:**

JPL Horizons observer table for Jupiter (599).

QUANTITIES include range, light-time, sub-observer lon/lat, NP angle.
Note: sub-observer longitude from Horizons is the geometric CM in the
planet's longitude system (for 599, System III-related body frame).

**Parameters:** `t, site_lon, site_lat, site_elev_m, timeout, force`

**How to find callers:** search the repo for `fetch_horizons_full(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `parse_horizons_observer_text(text)`

- **Module:** `ephemeris_pro.py`
- **Line:** 245–354

**Docstring:**

Best-effort parse of Horizons observer SOE block (CSV or fixed).

Typical CSV columns after date/time vary with QUANTITIES order.
We scan for physically plausible fields.

**Parameters:** `text`

**How to find callers:** search the repo for `parse_horizons_observer_text(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `load_winjupos_table(path)`

- **Module:** `ephemeris_pro.py`
- **Line:** 361–411

**Docstring:**

Load WinJUPOS-like CSV/JSON of CM or measurements.

Accepted columns (case-insensitive aliases):
  time/date/datetime/epoch, cm_iii/cml_iii/cml3/cmiii, optional sublat, np_pa

**Parameters:** `path`

**How to find callers:** search the repo for `load_winjupos_table(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `interpolate_winjupos_cm(rows, t)`

- **Module:** `ephemeris_pro.py`
- **Line:** 414–466

**Docstring:**

Linear circular interpolation of CM III (and friends) to epoch t.

**Parameters:** `rows, t`

**How to find callers:** search the repo for `interpolate_winjupos_cm(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `save_example_winjupos_template(path)`

- **Module:** `ephemeris_pro.py`
- **Line:** 469–477

_No docstring in source._ This function is part of `ephemeris_pro.py`. Open `app/ephemeris_pro.py` at line 469 for the full implementation.

**Parameters:** `path`

**How to find callers:** search the repo for `save_example_winjupos_template(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `try_spice_geometry(t, kernels_dir)`

- **Module:** `ephemeris_pro.py`
- **Line:** 484–522

**Docstring:**

SPICE geometry via spice_auto:
  - ensures spiceypy
  - downloads de440s + LSK + PCK if missing
  - returns distance, light-time, CM III / sub-lat when body frame works

**Parameters:** `t, kernels_dir`

**How to find callers:** search the repo for `try_spice_geometry(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `_try_spice_geometry_legacy(t, kernels_dir)`

- **Module:** `ephemeris_pro.py`
- **Line:** 525–575

**Docstring:**

Fallback if spice_auto unavailable: local kernels only.

**Parameters:** `t, kernels_dir`

**How to find callers:** search the repo for `_try_spice_geometry_legacy(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `resolve_pro_ephemeris(user_time_iso, time_error_seconds, cm_override, distance_override, sub_lat_override, north_pa_override, winjupos_path, site_lat, site_lon, use_horizons, use_spice, force_horizons)`

- **Module:** `ephemeris_pro.py`
- **Line:** 582–792

**Docstring:**

Build the best available ProEphemeris for absolute System III metrology.

**Parameters:** `user_time_iso, time_error_seconds, cm_override, distance_override, sub_lat_override, north_pa_override, winjupos_path, site_lat, site_lon, use_horizons, use_spice, force_horizons`

**How to find callers:** search the repo for `resolve_pro_ephemeris(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `write_ephemeris_report(path, eph)`

- **Module:** `ephemeris_pro.py`
- **Line:** 795–817

_No docstring in source._ This function is part of `ephemeris_pro.py`. Open `app/ephemeris_pro.py` at line 795 for the full implementation.

**Parameters:** `path, eph`

**How to find callers:** search the repo for `write_ephemeris_report(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

## Large-module guide

`ephemeris_pro.py` is a large module (817 lines). When editing:

1. Prefer adding helpers near related symbols rather than new global state.
2. Keep I/O (files, network) at the edges.
3. After changes, run `python3 cli.py certify --n 15` from `app/`.


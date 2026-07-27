# Module API: `spice_auto.py`

**Path:** `app/spice_auto.py`  
**Lines of code:** 534  
**Generated:** 2026-07-14T14:36:01.278751+00:00

## Module documentation

SPICE kernel auto-discovery + online download (zero user kernel hunting)
======================================================================

Most planetary-imaging users will never find NAIF kernels by hand. This module:

  1) Ensures spiceypy is importable
  2) Auto-downloads the minimal generic kernel set for Jupiter observer geometry
  3) Verifies kernels load (furnsh) and returns a ready kernel set
  4) Computes observer→Jupiter geometry at an epoch (distance, light-time,
     sub-observer lon/lat in IAU_JUPITER ≈ System III body frame)
  5) Caches under app/ephemeris_data/spice/  (or $GRS_SPICE_KERNELS)

Mirrors (tried in order):
  - NAIF public generic_kernels
  - NAIF /pub/naif mirror path variants

This is the only absolute-geometry path the observatory should rely on for
publication-grade System III work when no WinJUPOS override is pasted.

## Overview statistics

| Item | Count |
|------|------:|
| Classes | 2 |
| Top-level functions | 12 |
| Methods | 2 |

## Symbol index

- **class** `SpiceStatus` — line 98
  - `SpiceStatus.to_dict()` — line 108
- **class** `SpiceGeometry` — line 113
  - `SpiceGeometry.to_dict()` — line 129
- **function** `_ssl_context()` — line 133
- **function** `_sha256_file()` — line 144
- **function** `has_spiceypy()` — line 159
- **function** `kernel_dir()` — line 167
- **function** `_existing_kernel()` — line 173
- **function** `_download()` — line 195
- **function** `ensure_kernels()` — line 235
- **function** `list_local_kernels()` — line 329
- **function** `_furnsh_all()` — line 339
- **function** `wrap_deg()` — line 357
- **function** `compute_spice_geometry()` — line 361
- **function** `selftest()` — line 517

## Classes (full detail)

### class `SpiceStatus`

- **Defined at:** line 98
- **Methods:** 1

_No class docstring._

#### Methods

##### `SpiceStatus.to_dict(self)`

- **Line:** 108–109

_No docstring. Inferred role: member of `SpiceStatus` used by the spice_auto subsystem._

**Signature notes:** Accepts parameters `self`. See source `app/spice_auto.py` around line 108 for implementation.

**Related features:** Any feature that imports `spice_auto` may call this method.

---

### class `SpiceGeometry`

- **Defined at:** line 113
- **Methods:** 1

_No class docstring._

#### Methods

##### `SpiceGeometry.to_dict(self)`

- **Line:** 129–130

_No docstring. Inferred role: member of `SpiceGeometry` used by the spice_auto subsystem._

**Signature notes:** Accepts parameters `self`. See source `app/spice_auto.py` around line 129 for implementation.

**Related features:** Any feature that imports `spice_auto` may call this method.

---

## Top-level functions (full detail)

### `_ssl_context()`

- **Module:** `spice_auto.py`
- **Line:** 133–141

_No docstring in source._ This function is part of `spice_auto.py`. Open `app/spice_auto.py` at line 133 for the full implementation.

**Parameters:** `(none / *args via definition)`

**How to find callers:** search the repo for `_ssl_context(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `_sha256_file(path, max_bytes)`

- **Module:** `spice_auto.py`
- **Line:** 144–156

_No docstring in source._ This function is part of `spice_auto.py`. Open `app/spice_auto.py` at line 144 for the full implementation.

**Parameters:** `path, max_bytes`

**How to find callers:** search the repo for `_sha256_file(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `has_spiceypy()`

- **Module:** `spice_auto.py`
- **Line:** 159–164

_No docstring in source._ This function is part of `spice_auto.py`. Open `app/spice_auto.py` at line 159 for the full implementation.

**Parameters:** `(none / *args via definition)`

**How to find callers:** search the repo for `has_spiceypy(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `kernel_dir(path)`

- **Module:** `spice_auto.py`
- **Line:** 167–170

_No docstring in source._ This function is part of `spice_auto.py`. Open `app/spice_auto.py` at line 167 for the full implementation.

**Parameters:** `path`

**How to find callers:** search the repo for `kernel_dir(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `_existing_kernel(kdir, entry)`

- **Module:** `spice_auto.py`
- **Line:** 173–192

_No docstring in source._ This function is part of `spice_auto.py`. Open `app/spice_auto.py` at line 173 for the full implementation.

**Parameters:** `kdir, entry`

**How to find callers:** search the repo for `_existing_kernel(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `_download(url, dest, timeout)`

- **Module:** `spice_auto.py`
- **Line:** 195–232

_No docstring in source._ This function is part of `spice_auto.py`. Open `app/spice_auto.py` at line 195 for the full implementation.

**Parameters:** `url, dest, timeout`

**How to find callers:** search the repo for `_download(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `ensure_kernels(kdir, force, timeout)`

- **Module:** `spice_auto.py`
- **Line:** 235–326

**Docstring:**

Search local cache; if required kernels missing, download them online.
Returns status (does not leave kernels furnished).

**Parameters:** `kdir, force, timeout`

**How to find callers:** search the repo for `ensure_kernels(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `list_local_kernels(kdir)`

- **Module:** `spice_auto.py`
- **Line:** 329–336

_No docstring in source._ This function is part of `spice_auto.py`. Open `app/spice_auto.py` at line 329 for the full implementation.

**Parameters:** `kdir`

**How to find callers:** search the repo for `list_local_kernels(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `_furnsh_all(kdir)`

- **Module:** `spice_auto.py`
- **Line:** 339–354

_No docstring in source._ This function is part of `spice_auto.py`. Open `app/spice_auto.py` at line 339 for the full implementation.

**Parameters:** `kdir`

**How to find callers:** search the repo for `_furnsh_all(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `wrap_deg(x)`

- **Module:** `spice_auto.py`
- **Line:** 357–358

_No docstring in source._ This function is part of `spice_auto.py`. Open `app/spice_auto.py` at line 357 for the full implementation.

**Parameters:** `x`

**How to find callers:** search the repo for `wrap_deg(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `compute_spice_geometry(t_utc, kdir, auto_download, observer, target)`

- **Module:** `spice_auto.py`
- **Line:** 361–514

**Docstring:**

Full SPICE geometry at UTC datetime or ISO string.

Sub-observer lon/lat in IAU_JUPITER body-fixed frame.
CM III is taken as the body-fixed sub-observer west-style longitude
(convention: wrap_deg of atan2 body-frame observer direction).

**Parameters:** `t_utc, kdir, auto_download, observer, target`

**How to find callers:** search the repo for `compute_spice_geometry(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `selftest()`

- **Module:** `spice_auto.py`
- **Line:** 517–528

**Docstring:**

Download kernels if needed and evaluate one epoch.

**Parameters:** `(none / *args via definition)`

**How to find callers:** search the repo for `selftest(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

## Large-module guide

`spice_auto.py` is a large module (534 lines). When editing:

1. Prefer adding helpers near related symbols rather than new global state.
2. Keep I/O (files, network) at the edges.
3. After changes, run `python3 cli.py certify --n 15` from `app/`.


# Module API: `product_core.py`

**Path:** `app/product_core.py`  
**Lines of code:** 386  
**Generated:** 2026-07-14T14:36:01.278228+00:00

## Module documentation

GRS Observatory — product core (single professional entry surface)
=================================================================

All shippable workflows should call into this module rather than
duplicating process/synthetic logic across desktop and server.

Product version is read from ../VERSION when available.

## Overview statistics

| Item | Count |
|------|------:|
| Classes | 1 |
| Top-level functions | 6 |
| Methods | 1 |

## Symbol index

- **class** `ProductInfo` — line 38
  - `ProductInfo.to_dict()` — line 46
- **function** `product_version()` — line 25
- **function** `default_out_root()` — line 50
- **function** `process_image()` — line 56
- **function** `generate_synthetic()` — line 97
- **function** `resolve_ephemeris()` — line 225
- **function** `certify()` — line 238

## Classes (full detail)

### class `ProductInfo`

- **Defined at:** line 38
- **Methods:** 1

_No class docstring._

#### Methods

##### `ProductInfo.to_dict(self)`

- **Line:** 46–47

_No docstring. Inferred role: member of `ProductInfo` used by the product_core subsystem._

**Signature notes:** Accepts parameters `self`. See source `app/product_core.py` around line 46 for implementation.

**Related features:** Any feature that imports `product_core` may call this method.

---

## Top-level functions (full detail)

### `product_version()`

- **Module:** `product_core.py`
- **Line:** 25–29

_No docstring in source._ This function is part of `product_core.py`. Open `app/product_core.py` at line 25 for the full implementation.

**Parameters:** `(none / *args via definition)`

**How to find callers:** search the repo for `product_version(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `default_out_root()`

- **Module:** `product_core.py`
- **Line:** 50–53

_No docstring in source._ This function is part of `product_core.py`. Open `app/product_core.py` at line 50 for the full implementation.

**Parameters:** `(none / *args via definition)`

**How to find callers:** search the repo for `default_out_root(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `process_image(path, user_time)`

- **Module:** `product_core.py`
- **Line:** 56–94

**Docstring:**

Professional Process entry — real image metrology.

**Parameters:** `path, user_time`

**How to find callers:** search the repo for `process_image(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `generate_synthetic()`

- **Module:** `product_core.py`
- **Line:** 97–222

**Docstring:**

Synthetic generation (+ optional measure).

mode:
  - metrology: quieter SEB, GRS dominant, for certification / sale demos
  - visual: high wave contrast for presentation stills

**Parameters:** `(none / *args via definition)`

**How to find callers:** search the repo for `generate_synthetic(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `resolve_ephemeris(user_time)`

- **Module:** `product_core.py`
- **Line:** 225–235

_No docstring in source._ This function is part of `product_core.py`. Open `app/product_core.py` at line 225 for the full implementation.

**Parameters:** `user_time`

**How to find callers:** search the repo for `resolve_ephemeris(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `certify()`

- **Module:** `product_core.py`
- **Line:** 238–386

**Docstring:**

Product certification suite — metrology synthetics + SPICE + dual recovery.

Exit criteria are professional (honest) gates for shipping, not fantasy 0.00″.

**Parameters:** `(none / *args via definition)`

**How to find callers:** search the repo for `certify(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---


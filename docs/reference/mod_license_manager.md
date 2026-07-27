# Module API: `license_manager.py`

**Path:** `app/license_manager.py`  
**Lines of code:** 373  
**Generated:** 2026-07-14T14:36:01.277435+00:00

## Module documentation

GRS Observatory — commercial license key system (production-ready stub)
======================================================================

Key format:
  GRS-1-<PLAN>-<PAYLOAD>-<SIG4>

  PLAN:    PERS | PRO | SITE | TRIAL
  PAYLOAD: base32-ish payload (customer id + expiry days code)
  SIG4:    first 4 groups of HMAC-SHA256 over canonical string

Vendor secret:
  Environment GRS_LICENSE_SECRET  (set this before generating keys for sale)
  Default secret is for evaluation only — change before selling.

Machine binding (Pro/Site optional):
  If bind=True, payload includes a short machine fingerprint.

Storage:
  <data_dir>/license.json

This is a real, usable license gate for a paid desktop product. Rotate the
secret for production; keep a private generator on your sales machine only.

## Overview statistics

| Item | Count |
|------|------:|
| Classes | 1 |
| Top-level functions | 12 |
| Methods | 2 |

## Symbol index

- **class** `LicenseStatus` — line 127
  - `LicenseStatus.__post_init__()` — line 140
  - `LicenseStatus.to_dict()` — line 144
- **function** `_secret()` — line 91
- **function** `machine_fingerprint()` — line 96
- **function** `_b32ish()` — line 107
- **function** `_sign()` — line 120
- **function** `generate_key()` — line 148
- **function** `parse_and_verify()` — line 185
- **function** `license_path()` — line 248
- **function** `save_license()` — line 252
- **function** `load_status()` — line 269
- **function** `status_from_fields()` — line 294
- **function** `require_feature()` — line 329
- **function** `vendor_generate_batch()` — line 340

## Classes (full detail)

### class `LicenseStatus`

- **Defined at:** line 127
- **Methods:** 2

_No class docstring._

#### Methods

##### `LicenseStatus.__post_init__(self)`

- **Line:** 140–142

_No docstring. Inferred role: member of `LicenseStatus` used by the license_manager subsystem._

**Signature notes:** Accepts parameters `self`. See source `app/license_manager.py` around line 140 for implementation.

**Related features:** Any feature that imports `license_manager` may call this method.

---

##### `LicenseStatus.to_dict(self)`

- **Line:** 144–145

_No docstring. Inferred role: member of `LicenseStatus` used by the license_manager subsystem._

**Signature notes:** Accepts parameters `self`. See source `app/license_manager.py` around line 144 for implementation.

**Related features:** Any feature that imports `license_manager` may call this method.

---

## Top-level functions (full detail)

### `_secret()`

- **Module:** `license_manager.py`
- **Line:** 91–93

_No docstring in source._ This function is part of `license_manager.py`. Open `app/license_manager.py` at line 91 for the full implementation.

**Parameters:** `(none / *args via definition)`

**How to find callers:** search the repo for `_secret(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `machine_fingerprint()`

- **Module:** `license_manager.py`
- **Line:** 96–104

**Docstring:**

Stable short machine id (not cryptographically private).

**Parameters:** `(none / *args via definition)`

**How to find callers:** search the repo for `machine_fingerprint(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `_b32ish(data)`

- **Module:** `license_manager.py`
- **Line:** 107–117

_No docstring in source._ This function is part of `license_manager.py`. Open `app/license_manager.py` at line 107 for the full implementation.

**Parameters:** `data`

**How to find callers:** search the repo for `_b32ish(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `_sign(canonical)`

- **Module:** `license_manager.py`
- **Line:** 120–123

_No docstring in source._ This function is part of `license_manager.py`. Open `app/license_manager.py` at line 120 for the full implementation.

**Parameters:** `canonical`

**How to find callers:** search the repo for `_sign(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `generate_key(plan, customer, days, bind_machine, machine_id)`

- **Module:** `license_manager.py`
- **Line:** 148–182

**Docstring:**

Vendor-side key generation.

days: 0 = no expiry; else valid for N days from generation.
bind_machine: if True, key only works on machine_id (default: this machine).

**Parameters:** `plan, customer, days, bind_machine, machine_id`

**How to find callers:** search the repo for `generate_key(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `parse_and_verify(key)`

- **Module:** `license_manager.py`
- **Line:** 185–245

**Docstring:**

Return (ok, fields, message).

**Parameters:** `key`

**How to find callers:** search the repo for `parse_and_verify(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `license_path(data_dir)`

- **Module:** `license_manager.py`
- **Line:** 248–249

_No docstring in source._ This function is part of `license_manager.py`. Open `app/license_manager.py` at line 248 for the full implementation.

**Parameters:** `data_dir`

**How to find callers:** search the repo for `license_path(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `save_license(data_dir, key, meta)`

- **Module:** `license_manager.py`
- **Line:** 252–266

_No docstring in source._ This function is part of `license_manager.py`. Open `app/license_manager.py` at line 252 for the full implementation.

**Parameters:** `data_dir, key, meta`

**How to find callers:** search the repo for `save_license(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `load_status(data_dir)`

- **Module:** `license_manager.py`
- **Line:** 269–291

_No docstring in source._ This function is part of `license_manager.py`. Open `app/license_manager.py` at line 269 for the full implementation.

**Parameters:** `data_dir`

**How to find callers:** search the repo for `load_status(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `status_from_fields(ok, fields, msg)`

- **Module:** `license_manager.py`
- **Line:** 294–326

_No docstring in source._ This function is part of `license_manager.py`. Open `app/license_manager.py` at line 294 for the full implementation.

**Parameters:** `ok, fields, msg`

**How to find callers:** search the repo for `status_from_fields(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `require_feature(data_dir, feature)`

- **Module:** `license_manager.py`
- **Line:** 329–337

_No docstring in source._ This function is part of `license_manager.py`. Open `app/license_manager.py` at line 329 for the full implementation.

**Parameters:** `data_dir, feature`

**How to find callers:** search the repo for `require_feature(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `vendor_generate_batch(plan, customers, days, bind)`

- **Module:** `license_manager.py`
- **Line:** 340–350

_No docstring in source._ This function is part of `license_manager.py`. Open `app/license_manager.py` at line 340 for the full implementation.

**Parameters:** `plan, customers, days, bind`

**How to find callers:** search the repo for `vendor_generate_batch(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---


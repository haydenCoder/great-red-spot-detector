# Module API: `ram_ssd.py`

**Path:** `app/ram_ssd.py`  
**Lines of code:** 122  
**Generated:** 2026-07-14T14:36:01.278287+00:00

## Module documentation

16 GB RAM budget manager + SSD memmap cache.

Target machine: 16 GB unified RAM. Keep peak working set under ~10 GB so the
OS stays responsive. Large arrays spill to SSD under app/ssd_cache (project disk).

## Overview statistics

| Item | Count |
|------|------:|
| Classes | 0 |
| Top-level functions | 10 |
| Methods | 0 |

## Symbol index

- **function** `bytes_gb()` — line 32
- **function** `estimate_rgb_gb()` — line 36
- **function** `choose_max_resolution()` — line 41
- **function** `ssd_temp_path()` — line 73
- **function** `memmap_zeros()` — line 78
- **function** `array_to_ssd()` — line 85
- **function** `load_ssd()` — line 91
- **function** `free_memory()` — line 95
- **function** `cleanup_ssd_cache()` — line 99
- **function** `recommend_mc_iterations()` — line 114

## Top-level functions (full detail)

### `bytes_gb(n)`

- **Module:** `ram_ssd.py`
- **Line:** 32–33

_No docstring in source._ This function is part of `ram_ssd.py`. Open `app/ram_ssd.py` at line 32 for the full implementation.

**Parameters:** `n`

**How to find callers:** search the repo for `bytes_gb(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `estimate_rgb_gb(w, h, dtype)`

- **Module:** `ram_ssd.py`
- **Line:** 36–38

_No docstring in source._ This function is part of `ram_ssd.py`. Open `app/ram_ssd.py` at line 36 for the full implementation.

**Parameters:** `w, h, dtype`

**How to find callers:** search the repo for `estimate_rgb_gb(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `choose_max_resolution(prefer)`

- **Module:** `ram_ssd.py`
- **Line:** 41–70

**Docstring:**

Pick largest safe resolution for 16 GB.
16K float32 RGB ~ 1.5 GB raw + temps → tight.
8K ~ 0.4 GB → comfortable.

**Parameters:** `prefer`

**How to find callers:** search the repo for `choose_max_resolution(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `ssd_temp_path(suffix)`

- **Module:** `ram_ssd.py`
- **Line:** 73–75

_No docstring in source._ This function is part of `ram_ssd.py`. Open `app/ram_ssd.py` at line 73 for the full implementation.

**Parameters:** `suffix`

**How to find callers:** search the repo for `ssd_temp_path(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `memmap_zeros(shape, dtype)`

- **Module:** `ram_ssd.py`
- **Line:** 78–82

_No docstring in source._ This function is part of `ram_ssd.py`. Open `app/ram_ssd.py` at line 78 for the full implementation.

**Parameters:** `shape, dtype`

**How to find callers:** search the repo for `memmap_zeros(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `array_to_ssd(arr)`

- **Module:** `ram_ssd.py`
- **Line:** 85–88

_No docstring in source._ This function is part of `ram_ssd.py`. Open `app/ram_ssd.py` at line 85 for the full implementation.

**Parameters:** `arr`

**How to find callers:** search the repo for `array_to_ssd(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `load_ssd(path)`

- **Module:** `ram_ssd.py`
- **Line:** 91–92

_No docstring in source._ This function is part of `ram_ssd.py`. Open `app/ram_ssd.py` at line 91 for the full implementation.

**Parameters:** `path`

**How to find callers:** search the repo for `load_ssd(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `free_memory()`

- **Module:** `ram_ssd.py`
- **Line:** 95–96

_No docstring in source._ This function is part of `ram_ssd.py`. Open `app/ram_ssd.py` at line 95 for the full implementation.

**Parameters:** `(none / *args via definition)`

**How to find callers:** search the repo for `free_memory(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `cleanup_ssd_cache(max_age_sec)`

- **Module:** `ram_ssd.py`
- **Line:** 99–111

_No docstring in source._ This function is part of `ram_ssd.py`. Open `app/ram_ssd.py` at line 99 for the full implementation.

**Parameters:** `max_age_sec`

**How to find callers:** search the repo for `cleanup_ssd_cache(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `recommend_mc_iterations(resolution_mp)`

- **Module:** `ram_ssd.py`
- **Line:** 114–122

**Docstring:**

Fewer MC iters at huge res to stay within RAM/time.

**Parameters:** `resolution_mp`

**How to find callers:** search the repo for `recommend_mc_iterations(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---


# Module API: `desktop_pipeline.py`

**Path:** `app/desktop_pipeline.py`  
**Lines of code:** 610  
**Generated:** 2026-07-14T14:36:01.274613+00:00

## Module documentation

Shared advanced processing for the desktop app.
Runs the full Harvard-grade stack and writes a complete job package.

## Overview statistics

| Item | Count |
|------|------:|
| Classes | 0 |
| Top-level functions | 6 |
| Methods | 0 |

## Symbol index

- **function** `_load_image()` — line 34
- **function** `_try_imaging_pipeline()` — line 62
- **function** `format_full_report()` — line 89
- **function** `run_synthetic_full()` — line 188
- **function** `run_process_full()` — line 371
- **function** `run_factory_night_full()` — line 517

## Top-level functions (full detail)

### `_load_image(path)`

- **Module:** `desktop_pipeline.py`
- **Line:** 34–59

**Docstring:**

Return mono-or-CHW array, optional RGB channels, optional preview png path.

**Parameters:** `path`

**How to find callers:** search the repo for `_load_image(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `_try_imaging_pipeline(path, out, channels, meas)`

- **Module:** `desktop_pipeline.py`
- **Line:** 62–86

**Docstring:**

Run grs full imaging branch when possible (lucky-ish path for stacks).

**Parameters:** `path, out, channels, meas`

**How to find callers:** search the repo for `_try_imaging_pipeline(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `format_full_report(package)`

- **Module:** `desktop_pipeline.py`
- **Line:** 89–185

**Docstring:**

Human-readable + complete JSON dump for Results panel.

**Parameters:** `package`

**How to find callers:** search the repo for `format_full_report(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `run_synthetic_full(out_root)`

- **Module:** `desktop_pipeline.py`
- **Line:** 188–368

**Docstring:**

Generate random-epoch synthetic + full VLBI measure + complete package.

**Parameters:** `out_root`

**How to find callers:** search the repo for `run_synthetic_full(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `run_process_full(path, out_root)`

- **Module:** `desktop_pipeline.py`
- **Line:** 371–514

**Docstring:**

Process real image with every advanced stage available.

**Parameters:** `path, out_root`

**How to find callers:** search the repo for `run_process_full(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `run_factory_night_full(out_root)`

- **Module:** `desktop_pipeline.py`
- **Line:** 517–610

_No docstring in source._ This function is part of `desktop_pipeline.py`. Open `app/desktop_pipeline.py` at line 517 for the full implementation.

**Parameters:** `out_root`

**How to find callers:** search the repo for `run_factory_night_full(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

## Large-module guide

`desktop_pipeline.py` is a large module (610 lines). When editing:

1. Prefer adding helpers near related symbols rather than new global state.
2. Keep I/O (files, network) at the edges.
3. After changes, run `python3 cli.py certify --n 15` from `app/`.


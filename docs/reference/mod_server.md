# Module API: `server.py`

**Path:** `app/server.py`  
**Lines of code:** 1075  
**Generated:** 2026-07-14T14:36:01.278441+00:00

## Module documentation

GRS Observatory v3 — VLBI-inspired optical metrology for ground-based GRS photos.

Target: best-in-class planetary imaging metrology (formal error budgets, multi-scale
NCC, phase-reference probes, hierarchical MC). Not radio-VLBI microarcseconds —
honest optical floor for an extended cloud feature.

## Overview statistics

| Item | Count |
|------|------:|
| Classes | 0 |
| Top-level functions | 26 |
| Methods | 0 |

## Symbol index

- **function** `_no_cache_static()` — line 70
- **function** `_start()` — line 82
- **function** `_finish()` — line 91
- **function** `index()` — line 99
- **function** `health()` — line 104
- **function** `logs()` — line 128
- **function** `logs_clear()` — line 133
- **function** `verbose()` — line 139
- **function** `job()` — line 146
- **function** `regions()` — line 152
- **function** `resolutions()` — line 163
- **function** `nn_status()` — line 173
- **function** `nn_train()` — line 178
- **function** `upload()` — line 206
- **function** `process()` — line 221
- **function** `synthetic()` — line 423
- **function** `api_ephemeris()` — line 606
- **function** `winjupos_template()` — line 632
- **function** `winjupos_upload()` — line 638
- **function** `api_multi_epoch()` — line 651
- **function** `api_hard_synth()` — line 708
- **function** `capabilities()` — line 743
- **function** `api_factory_night()` — line 780
- **function** `output_file()` — line 1042
- **function** `file_api()` — line 1052
- **function** `main()` — line 1063

## Top-level functions (full detail)

### `_no_cache_static(resp)`

- **Module:** `server.py`
- **Line:** 70–76

_No docstring in source._ This function is part of `server.py`. Open `app/server.py` at line 70 for the full implementation.

**Parameters:** `resp`

**How to find callers:** search the repo for `_no_cache_static(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `_start(kind)`

- **Module:** `server.py`
- **Line:** 82–88

_No docstring in source._ This function is part of `server.py`. Open `app/server.py` at line 82 for the full implementation.

**Parameters:** `kind`

**How to find callers:** search the repo for `_start(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `_finish(result, error)`

- **Module:** `server.py`
- **Line:** 91–95

_No docstring in source._ This function is part of `server.py`. Open `app/server.py` at line 91 for the full implementation.

**Parameters:** `result, error`

**How to find callers:** search the repo for `_finish(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `index()`

- **Module:** `server.py`
- **Line:** 99–100

_No docstring in source._ This function is part of `server.py`. Open `app/server.py` at line 99 for the full implementation.

**Parameters:** `(none / *args via definition)`

**How to find callers:** search the repo for `index(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `health()`

- **Module:** `server.py`
- **Line:** 104–124

_No docstring in source._ This function is part of `server.py`. Open `app/server.py` at line 104 for the full implementation.

**Parameters:** `(none / *args via definition)`

**How to find callers:** search the repo for `health(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `logs()`

- **Module:** `server.py`
- **Line:** 128–129

_No docstring in source._ This function is part of `server.py`. Open `app/server.py` at line 128 for the full implementation.

**Parameters:** `(none / *args via definition)`

**How to find callers:** search the repo for `logs(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `logs_clear()`

- **Module:** `server.py`
- **Line:** 133–135

_No docstring in source._ This function is part of `server.py`. Open `app/server.py` at line 133 for the full implementation.

**Parameters:** `(none / *args via definition)`

**How to find callers:** search the repo for `logs_clear(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `verbose()`

- **Module:** `server.py`
- **Line:** 139–142

_No docstring in source._ This function is part of `server.py`. Open `app/server.py` at line 139 for the full implementation.

**Parameters:** `(none / *args via definition)`

**How to find callers:** search the repo for `verbose(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `job()`

- **Module:** `server.py`
- **Line:** 146–148

_No docstring in source._ This function is part of `server.py`. Open `app/server.py` at line 146 for the full implementation.

**Parameters:** `(none / *args via definition)`

**How to find callers:** search the repo for `job(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `regions()`

- **Module:** `server.py`
- **Line:** 152–159

_No docstring in source._ This function is part of `server.py`. Open `app/server.py` at line 152 for the full implementation.

**Parameters:** `(none / *args via definition)`

**How to find callers:** search the repo for `regions(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `resolutions()`

- **Module:** `server.py`
- **Line:** 163–169

_No docstring in source._ This function is part of `server.py`. Open `app/server.py` at line 163 for the full implementation.

**Parameters:** `(none / *args via definition)`

**How to find callers:** search the repo for `resolutions(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `nn_status()`

- **Module:** `server.py`
- **Line:** 173–174

_No docstring in source._ This function is part of `server.py`. Open `app/server.py` at line 173 for the full implementation.

**Parameters:** `(none / *args via definition)`

**How to find callers:** search the repo for `nn_status(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `nn_train()`

- **Module:** `server.py`
- **Line:** 178–202

_No docstring in source._ This function is part of `server.py`. Open `app/server.py` at line 178 for the full implementation.

**Parameters:** `(none / *args via definition)`

**How to find callers:** search the repo for `nn_train(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `upload()`

- **Module:** `server.py`
- **Line:** 206–217

_No docstring in source._ This function is part of `server.py`. Open `app/server.py` at line 206 for the full implementation.

**Parameters:** `(none / *args via definition)`

**How to find callers:** search the repo for `upload(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `process()`

- **Module:** `server.py`
- **Line:** 221–419

_No docstring in source._ This function is part of `server.py`. Open `app/server.py` at line 221 for the full implementation.

**Parameters:** `(none / *args via definition)`

**How to find callers:** search the repo for `process(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `synthetic()`

- **Module:** `server.py`
- **Line:** 423–602

_No docstring in source._ This function is part of `server.py`. Open `app/server.py` at line 423 for the full implementation.

**Parameters:** `(none / *args via definition)`

**How to find callers:** search the repo for `synthetic(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `api_ephemeris()`

- **Module:** `server.py`
- **Line:** 606–628

**Docstring:**

Resolve professional Jupiter ephemeris (WinJUPOS / SPICE / Horizons / analytical).

**Parameters:** `(none / *args via definition)`

**How to find callers:** search the repo for `api_ephemeris(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `winjupos_template()`

- **Module:** `server.py`
- **Line:** 632–634

_No docstring in source._ This function is part of `server.py`. Open `app/server.py` at line 632 for the full implementation.

**Parameters:** `(none / *args via definition)`

**How to find callers:** search the repo for `winjupos_template(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `winjupos_upload()`

- **Module:** `server.py`
- **Line:** 638–647

_No docstring in source._ This function is part of `server.py`. Open `app/server.py` at line 638 for the full implementation.

**Parameters:** `(none / *args via definition)`

**How to find callers:** search the repo for `winjupos_upload(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `api_multi_epoch()`

- **Module:** `server.py`
- **Line:** 651–704

**Docstring:**

Differential multi-epoch tracking (VLBI phase-ref across nights).

Body:
  directory: scan outputs dir (default app/outputs)
  epochs: optional list of {path} or {t_utc_iso, lon_iii_deg, lat_deg, ...}
  ref_index: 0

**Parameters:** `(none / *args via definition)`

**How to find callers:** search the repo for `api_multi_epoch(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `api_hard_synth()`

- **Module:** `server.py`
- **Line:** 708–739

**Docstring:**

Run hard synthetic stress suite (mismatch physics calibration).

**Parameters:** `(none / *args via definition)`

**How to find callers:** search the repo for `api_hard_synth(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `capabilities()`

- **Module:** `server.py`
- **Line:** 743–776

**Docstring:**

Everything the advanced stack can do — for UI discovery.

**Parameters:** `(none / *args via definition)`

**How to find callers:** search the repo for `capabilities(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `api_factory_night()`

- **Module:** `server.py`
- **Line:** 780–1038

**Docstring:**

One-command Harvard-grade night:
  1) Pro ephemeris
  2) HQ synthetic (+ VLBI measure)  OR  process uploaded path
  3) Multi-epoch differential (scan outputs)
  4) Hard-synth stress suite

**Parameters:** `(none / *args via definition)`

**How to find callers:** search the repo for `api_factory_night(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `output_file(job_id, filename)`

- **Module:** `server.py`
- **Line:** 1042–1048

_No docstring in source._ This function is part of `server.py`. Open `app/server.py` at line 1042 for the full implementation.

**Parameters:** `job_id, filename`

**How to find callers:** search the repo for `output_file(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `file_api()`

- **Module:** `server.py`
- **Line:** 1052–1060

_No docstring in source._ This function is part of `server.py`. Open `app/server.py` at line 1052 for the full implementation.

**Parameters:** `(none / *args via definition)`

**How to find callers:** search the repo for `file_api(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `main()`

- **Module:** `server.py`
- **Line:** 1063–1071

_No docstring in source._ This function is part of `server.py`. Open `app/server.py` at line 1063 for the full implementation.

**Parameters:** `(none / *args via definition)`

**How to find callers:** search the repo for `main(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

## Large-module guide

`server.py` is a large module (1075 lines). When editing:

1. Prefer adding helpers near related symbols rather than new global state.
2. Keep I/O (files, network) at the edges.
3. After changes, run `python3 cli.py certify --n 15` from `app/`.


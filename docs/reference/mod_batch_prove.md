# Module API: `batch_prove.py`

**Path:** `app/batch_prove.py`  
**Lines of code:** 400  
**Generated:** 2026-07-14T14:36:01.274361+00:00

## Module documentation

Batch synthetic proof suite — 50–100 runs with saved results
============================================================

Generates independent synthetic Jupiter frames, measures GRS with the
precision / research stack, scores truth recovery in arcseconds, and writes:

  outputs/batch_prove_<stamp>/
    runs/run_XXXX/...
    batch_summary.json
    batch_summary.csv
    batch_report.txt
    spice_status.json

Usage:
  cd app && python3 batch_prove.py --n 60 --resolution 1080p
  cd app && python3 batch_prove.py --n 50 --resolution 4K --fast

## Overview statistics

| Item | Count |
|------|------:|
| Classes | 0 |
| Top-level functions | 3 |
| Methods | 0 |

## Symbol index

- **function** `_percentile()` — line 45
- **function** `run_one()` — line 59
- **function** `main()` — line 207

## Top-level functions (full detail)

### `_percentile(xs, p)`

- **Module:** `batch_prove.py`
- **Line:** 45–56

_No docstring in source._ This function is part of `batch_prove.py`. Open `app/batch_prove.py` at line 45 for the full implementation.

**Parameters:** `xs, p`

**How to find callers:** search the repo for `_percentile(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `run_one(out_dir)`

- **Module:** `batch_prove.py`
- **Line:** 59–204

_No docstring in source._ This function is part of `batch_prove.py`. Open `app/batch_prove.py` at line 59 for the full implementation.

**Parameters:** `out_dir`

**How to find callers:** search the repo for `run_one(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `main(argv)`

- **Module:** `batch_prove.py`
- **Line:** 207–396

_No docstring in source._ This function is part of `batch_prove.py`. Open `app/batch_prove.py` at line 207 for the full implementation.

**Parameters:** `argv`

**How to find callers:** search the repo for `main(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---


# Module API: `cli.py`

**Path:** `app/cli.py`  
**Lines of code:** 214  
**Generated:** 2026-07-14T14:36:01.274428+00:00

## Module documentation

GRS Observatory — professional command-line interface
=====================================================

Examples:
  python3 cli.py version
  python3 cli.py eph "2026-07-14 12:00:00"
  python3 cli.py synth --mode metrology --res 1080p
  python3 cli.py process /path/to/jupiter.fits --time "2026-01-09 17:06:00"
  python3 cli.py certify --n 30

## Overview statistics

| Item | Count |
|------|------:|
| Classes | 0 |
| Top-level functions | 1 |
| Methods | 0 |

## Symbol index

- **function** `main()` — line 21

## Top-level functions (full detail)

### `main(argv)`

- **Module:** `cli.py`
- **Line:** 21–210

_No docstring in source._ This function is part of `cli.py`. Open `app/cli.py` at line 21 for the full implementation.

**Parameters:** `argv`

**How to find callers:** search the repo for `main(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---


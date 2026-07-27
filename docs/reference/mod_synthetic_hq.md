# Module API: `synthetic_hq.py`

**Path:** `app/synthetic_hq.py`  
**Lines of code:** 671  
**Generated:** 2026-07-14T14:36:01.278858+00:00

## Module documentation

High-fidelity synthetic Jupiter + GRS (visible waves, belts, GRS swirl)
======================================================================

Previous renderer looked flat at 8K because:
  - wave amplitudes were tiny (~0.04–0.10)
  - residual "turbulence" was blocky nearest-neighbor noise
  - heavy Gaussian seeing washed fine structure
  - belt transitions were soft tanh mush

This rewrite builds a *zonal jet / wavefield* atmosphere that remains readable
at 1080p–16K:

  • Sharp multi-belt albedo profile (NEB/EZ/SEB/STB …)
  • High-contrast longitudinal waves, chevrons, festoons
  • Multi-octave smooth value-noise (bilinear) — not block tiles
  • Latitude-dependent zonal shear of the residual field
  • White ovals / barges
  • Structured GRS dark oval + internal spiral filaments
  • Mild limb darkening; *light* seeing so waves stay visible
  • Optional GRS close-up crop preview PNG

Truth JSON still drives recovery scoring (lon/lat/CM/disk).

## Overview statistics

| Item | Count |
|------|------:|
| Classes | 1 |
| Top-level functions | 12 |
| Methods | 0 |

## Symbol index

- **class** `SynthSpec` — line 56
- **function** `_seed()` — line 88
- **function** `_parse_time()` — line 93
- **function** `random_observation_time()` — line 103
- **function** `_blur()` — line 112
- **function** `_resize_bilinear()` — line 127
- **function** `_value_noise()` — line 153
- **function** `_belt_profile()` — line 176
- **function** `_wavefield()` — line 204
- **function** `_shear_residual()` — line 245
- **function** `_paint_ovals()` — line 257
- **function** `_paint_grs()` — line 296
- **function** `generate()` — line 372

## Classes (full detail)

### class `SynthSpec`

- **Defined at:** line 56
- **Methods:** 0

**Class docstring:**

Synthetic Jupiter frame.

Observation epoch is random unless random_time=False and user_time_iso is set.

mode:
  - visual: high wave contrast (presentation / UI stills)
  - metrology: quieter SEB, GRS uniquely dark, for certification / accuracy demos

## Top-level functions (full detail)

### `_seed(user_time, region, err)`

- **Module:** `synthetic_hq.py`
- **Line:** 88–90

_No docstring in source._ This function is part of `synthetic_hq.py`. Open `app/synthetic_hq.py` at line 88 for the full implementation.

**Parameters:** `user_time, region, err`

**How to find callers:** search the repo for `_seed(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `_parse_time(s)`

- **Module:** `synthetic_hq.py`
- **Line:** 93–100

_No docstring in source._ This function is part of `synthetic_hq.py`. Open `app/synthetic_hq.py` at line 93 for the full implementation.

**Parameters:** `s`

**How to find callers:** search the repo for `_parse_time(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `random_observation_time(rng)`

- **Module:** `synthetic_hq.py`
- **Line:** 103–109

_No docstring in source._ This function is part of `synthetic_hq.py`. Open `app/synthetic_hq.py` at line 103 for the full implementation.

**Parameters:** `rng`

**How to find callers:** search the repo for `random_observation_time(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `_blur(rgb, sigma)`

- **Module:** `synthetic_hq.py`
- **Line:** 112–124

_No docstring in source._ This function is part of `synthetic_hq.py`. Open `app/synthetic_hq.py` at line 112 for the full implementation.

**Parameters:** `rgb, sigma`

**How to find callers:** search the repo for `_blur(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `_resize_bilinear(small, h, w)`

- **Module:** `synthetic_hq.py`
- **Line:** 127–150

**Docstring:**

Smooth upsample (avoids blocky tiles that killed wave look at 8K).

**Parameters:** `small, h, w`

**How to find callers:** search the repo for `_resize_bilinear(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `_value_noise(h, w, rng, octaves, base)`

- **Module:** `synthetic_hq.py`
- **Line:** 153–173

**Docstring:**

Multi-octave smooth noise in [-1,1].

**Parameters:** `h, w, rng, octaves, base`

**How to find callers:** search the repo for `_value_noise(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `_belt_profile(lat_n)`

- **Module:** `synthetic_hq.py`
- **Line:** 176–201

**Docstring:**

High-contrast canonical belt/zone stack (lat in radians, planetocentric-ish).
Zones → high albedo, belts → low.

**Parameters:** `lat_n`

**How to find callers:** search the repo for `_belt_profile(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `_wavefield(lon_rel, lat_n, rng, contrast)`

- **Module:** `synthetic_hq.py`
- **Line:** 204–242

**Docstring:**

Longitudinal waves + chevrons + festoons — high enough amplitude to *see*.

**Parameters:** `lon_rel, lat_n, rng, contrast`

**How to find callers:** search the repo for `_wavefield(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `_shear_residual(turb, lat_n, lon_rel, strength)`

- **Module:** `synthetic_hq.py`
- **Line:** 245–254

**Docstring:**

Approximate zonal shear by phase-modulating residual with latitude.

**Parameters:** `turb, lat_n, lon_rel, strength`

**How to find callers:** search the repo for `_shear_residual(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `_paint_ovals(rgb, disk, lon_abs, lat_deg, ld, rng, n, grs_lon, grs_lat)`

- **Module:** `synthetic_hq.py`
- **Line:** 257–293

**Docstring:**

White ovals / barges — avoid placing competing dark barges on the GRS.

**Parameters:** `rgb, disk, lon_abs, lat_deg, ld, rng, n, grs_lon, grs_lat`

**How to find callers:** search the repo for `_paint_ovals(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `_paint_grs(rgb, disk, lon_abs, lat_deg, ld, grs_lon, grs_lat, grs_L, grs_W, rng)`

- **Module:** `synthetic_hq.py`
- **Line:** 296–369

**Docstring:**

Dark red oval + internal spiral / filament structure.

Returns (oval_mask, truth_lon, truth_lat) where truth lon/lat are the
*intensity-weighted dark barycentre* of the painted GRS — the same
definition the metrology engine measures (not the geometric ellipse seed).

**Parameters:** `rgb, disk, lon_abs, lat_deg, ld, grs_lon, grs_lat, grs_L, grs_W, rng`

**How to find callers:** search the repo for `_paint_grs(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `generate(spec, out_dir)`

- **Module:** `synthetic_hq.py`
- **Line:** 372–671

_No docstring in source._ This function is part of `synthetic_hq.py`. Open `app/synthetic_hq.py` at line 372 for the full implementation.

**Parameters:** `spec, out_dir`

**How to find callers:** search the repo for `generate(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

## Large-module guide

`synthetic_hq.py` is a large module (671 lines). When editing:

1. Prefer adding helpers near related symbols rather than new global state.
2. Keep I/O (files, network) at the edges.
3. After changes, run `python3 cli.py certify --n 15` from `app/`.


# Module API: `precision_engine.py`

**Path:** `app/precision_engine.py`  
**Lines of code:** 993  
**Generated:** 2026-07-14T14:36:01.278131+00:00

## Module documentation

Precision GRS engine — synthetic truth-recovery target ≤0.1″ sky (ideal frames)

At Jupiter ~5 AU: 1° System-III longitude ≈ 0.3–0.35″ on sky near the equator.
So 0.1″ sky ≈ 0.3° longitude near the equator — ambitious but reachable on
high-contrast synthetics with:

  1) sub-pixel limb navigation
  2) multi-scale cylindrical dark-oval template match (primary)
  3) intensity barycentre + ellipse consensus
  4) multi-method weighted consensus with outlier rejection
  5) Monte Carlo for uncertainty in arcseconds

Real ground-based extended-cloud floors are usually higher (seeing + definition).
This engine still targets research-grade recovery when the feature is well resolved.

## Overview statistics

| Item | Count |
|------|------:|
| Classes | 2 |
| Top-level functions | 23 |
| Methods | 2 |

## Symbol index

- **class** `NavState` — line 37
  - `NavState.b_pol_px()` — line 48
- **class** `GRSPrecisionResult` — line 53
  - `GRSPrecisionResult.to_dict()` — line 68
- **function** `deg2rad()` — line 72
- **function** `rad2deg()` — line 76
- **function** `wrap_deg()` — line 80
- **function** `wrap_diff()` — line 84
- **function** `km_per_deg_lon()` — line 88
- **function** `km_per_deg_lat()` — line 92
- **function** `deg_to_arcsec_on_sky()` — line 96
- **function** `sky_error_arcsec()` — line 103
- **function** `_gauss()` — line 109
- **function** `to_mono()` — line 124
- **function** `rough_disk_mask()` — line 137
- **function** `fit_limb_nav()` — line 155
- **function** `px_to_lonlat()` — line 251
- **function** `make_cylindrical()` — line 266
- **function** `_template_match_grs()` — line 299
- **function** `_moment_mask_grs()` — line 439
- **function** `_map_dark_centroid()` — line 533
- **function** `_method_is_sane()` — line 585
- **function** `_choose_size()` — line 604
- **function** `_circular_weighted_mean()` — line 626
- **function** `measure_grs_precision()` — line 635
- **function** `monte_carlo_precision()` — line 879
- **function** `cap_mc_iterations()` — line 980

## Classes (full detail)

### class `NavState`

- **Defined at:** line 37
- **Methods:** 1

_No class docstring._

#### Methods

##### `NavState.b_pol_px(self)`

- **Line:** 48–49

_No docstring. Inferred role: member of `NavState` used by the precision_engine subsystem._

**Signature notes:** Accepts parameters `self`. See source `app/precision_engine.py` around line 48 for implementation.

**Related features:** Any feature that imports `precision_engine` may call this method.

---

### class `GRSPrecisionResult`

- **Defined at:** line 53
- **Methods:** 1

_No class docstring._

#### Methods

##### `GRSPrecisionResult.to_dict(self)`

- **Line:** 68–69

_No docstring. Inferred role: member of `GRSPrecisionResult` used by the precision_engine subsystem._

**Signature notes:** Accepts parameters `self`. See source `app/precision_engine.py` around line 68 for implementation.

**Related features:** Any feature that imports `precision_engine` may call this method.

---

## Top-level functions (full detail)

### `deg2rad(d)`

- **Module:** `precision_engine.py`
- **Line:** 72–73

_No docstring in source._ This function is part of `precision_engine.py`. Open `app/precision_engine.py` at line 72 for the full implementation.

**Parameters:** `d`

**How to find callers:** search the repo for `deg2rad(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `rad2deg(r)`

- **Module:** `precision_engine.py`
- **Line:** 76–77

_No docstring in source._ This function is part of `precision_engine.py`. Open `app/precision_engine.py` at line 76 for the full implementation.

**Parameters:** `r`

**How to find callers:** search the repo for `rad2deg(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `wrap_deg(x)`

- **Module:** `precision_engine.py`
- **Line:** 80–81

_No docstring in source._ This function is part of `precision_engine.py`. Open `app/precision_engine.py` at line 80 for the full implementation.

**Parameters:** `x`

**How to find callers:** search the repo for `wrap_deg(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `wrap_diff(a, b)`

- **Module:** `precision_engine.py`
- **Line:** 84–85

_No docstring in source._ This function is part of `precision_engine.py`. Open `app/precision_engine.py` at line 84 for the full implementation.

**Parameters:** `a, b`

**How to find callers:** search the repo for `wrap_diff(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `km_per_deg_lon(lat_deg)`

- **Module:** `precision_engine.py`
- **Line:** 88–89

_No docstring in source._ This function is part of `precision_engine.py`. Open `app/precision_engine.py` at line 88 for the full implementation.

**Parameters:** `lat_deg`

**How to find callers:** search the repo for `km_per_deg_lon(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `km_per_deg_lat()`

- **Module:** `precision_engine.py`
- **Line:** 92–93

_No docstring in source._ This function is part of `precision_engine.py`. Open `app/precision_engine.py` at line 92 for the full implementation.

**Parameters:** `(none / *args via definition)`

**How to find callers:** search the repo for `km_per_deg_lat(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `deg_to_arcsec_on_sky(deg, km_per_deg, distance_au)`

- **Module:** `precision_engine.py`
- **Line:** 96–100

**Docstring:**

Convert angular size on planet (deg of lon/lat) to sky arcsec.

**Parameters:** `deg, km_per_deg, distance_au`

**How to find callers:** search the repo for `deg_to_arcsec_on_sky(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `sky_error_arcsec(dlon_deg, dlat_deg, lat_deg, distance_au)`

- **Module:** `precision_engine.py`
- **Line:** 103–106

_No docstring in source._ This function is part of `precision_engine.py`. Open `app/precision_engine.py` at line 103 for the full implementation.

**Parameters:** `dlon_deg, dlat_deg, lat_deg, distance_au`

**How to find callers:** search the repo for `sky_error_arcsec(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `_gauss(img, sigma)`

- **Module:** `precision_engine.py`
- **Line:** 109–121

_No docstring in source._ This function is part of `precision_engine.py`. Open `app/precision_engine.py` at line 109 for the full implementation.

**Parameters:** `img, sigma`

**How to find callers:** search the repo for `_gauss(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `to_mono(image)`

- **Module:** `precision_engine.py`
- **Line:** 124–134

_No docstring in source._ This function is part of `precision_engine.py`. Open `app/precision_engine.py` at line 124 for the full implementation.

**Parameters:** `image`

**How to find callers:** search the repo for `to_mono(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `rough_disk_mask(image)`

- **Module:** `precision_engine.py`
- **Line:** 137–152

_No docstring in source._ This function is part of `precision_engine.py`. Open `app/precision_engine.py` at line 137 for the full implementation.

**Parameters:** `image`

**How to find callers:** search the repo for `rough_disk_mask(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `fit_limb_nav(image, n_rays, cm_iii_deg, distance_au)`

- **Module:** `precision_engine.py`
- **Line:** 155–248

**Docstring:**

Sub-pixel limb navigation.

Half-intensity isophote on each ray + robust median centre / radius
(stable under limb darkening; avoids unstable algebraic circle fits).

**Parameters:** `image, n_rays, cm_iii_deg, distance_au`

**How to find callers:** search the repo for `fit_limb_nav(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `px_to_lonlat(y, x, nav)`

- **Module:** `precision_engine.py`
- **Line:** 251–263

_No docstring in source._ This function is part of `precision_engine.py`. Open `app/precision_engine.py` at line 251 for the full implementation.

**Parameters:** `y, x, nav`

**How to find callers:** search the repo for `px_to_lonlat(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `make_cylindrical(image, nav, width, height)`

- **Module:** `precision_engine.py`
- **Line:** 266–296

**Docstring:**

Orthographic sample → simple cylindrical map lon∈[-90,90] about CM, lat∈[-90,90].

**Parameters:** `image, nav, width, height`

**How to find callers:** search the repo for `make_cylindrical(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `_template_match_grs(cyl, nav, lat0, length_deg, width_deg)`

- **Module:** `precision_engine.py`
- **Line:** 299–436

**Docstring:**

Dark elliptical template match on cylindrical map (visible hemisphere).

Uses zero-mean normalized cross-correlation (NCC), a narrow SEB/GRS
latitude band, multi-scale sizes with a prior around the nominal oval,
and a local dark-centroid refine so we do not lock onto random SEB waves.

**Parameters:** `cyl, nav, lat0, length_deg, width_deg`

**How to find callers:** search the repo for `_template_match_grs(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `_moment_mask_grs(image, nav)`

- **Module:** `precision_engine.py`
- **Line:** 439–530

_No docstring in source._ This function is part of `precision_engine.py`. Open `app/precision_engine.py` at line 439 for the full implementation.

**Parameters:** `image, nav`

**How to find callers:** search the repo for `_moment_mask_grs(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `_map_dark_centroid(cyl, nav, lat0)`

- **Module:** `precision_engine.py`
- **Line:** 533–582

**Docstring:**

Dark peak only inside SEB/GRS latitude band — never full-map (avoids lat~90 bugs).

**Parameters:** `cyl, nav, lat0`

**How to find callers:** search the repo for `_map_dark_centroid(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `_method_is_sane(m, ref_lon)`

- **Module:** `precision_engine.py`
- **Line:** 585–601

_No docstring in source._ This function is part of `precision_engine.py`. Open `app/precision_engine.py` at line 585 for the full implementation.

**Parameters:** `m, ref_lon`

**How to find callers:** search the repo for `_method_is_sane(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `_choose_size(methods)`

- **Module:** `precision_engine.py`
- **Line:** 604–615

**Docstring:**

Never trust tiny moment blobs; prefer template size.

**Parameters:** `methods`

**How to find callers:** search the repo for `_choose_size(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `_circular_weighted_mean(lons, weights)`

- **Module:** `precision_engine.py`
- **Line:** 626–632

_No docstring in source._ This function is part of `precision_engine.py`. Open `app/precision_engine.py` at line 626 for the full implementation.

**Parameters:** `lons, weights`

**How to find callers:** search the repo for `_circular_weighted_mean(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `measure_grs_precision(image, cm_iii_deg, distance_au, nav, quiet, map_width, map_height)`

- **Module:** `precision_engine.py`
- **Line:** 635–876

**Docstring:**

Multi-method GRS measurement for best *result* accuracy.

Point estimate uses high-res cylindrical map + weighted consensus
(template preferred for longitude of a dark oval).

**Parameters:** `image, cm_iii_deg, distance_au, nav, quiet, map_width, map_height`

**How to find callers:** search the repo for `measure_grs_precision(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `monte_carlo_precision(image, nav, n_iter, seed, max_iter)`

- **Module:** `precision_engine.py`
- **Line:** 879–977

**Docstring:**

Fast MC for uncertainty of the *measurement process*.

Uses map-domain noise (template + map_dark only) so it finishes quickly.
Point estimate stays from full measure_grs_precision (call separately).

**Parameters:** `image, nav, n_iter, seed, max_iter`

**How to find callers:** search the repo for `monte_carlo_precision(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `cap_mc_iterations(requested, megapixels)`

- **Module:** `precision_engine.py`
- **Line:** 980–993

**Docstring:**

Hard caps so results return while keeping useful σ.

**Parameters:** `requested, megapixels`

**How to find callers:** search the repo for `cap_mc_iterations(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

## Large-module guide

`precision_engine.py` is a large module (993 lines). When editing:

1. Prefer adding helpers near related symbols rather than new global state.
2. Keep I/O (files, network) at the edges.
3. After changes, run `python3 cli.py certify --n 15` from `app/`.


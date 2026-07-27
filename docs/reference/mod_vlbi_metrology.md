# Module API: `vlbi_metrology.py`

**Path:** `app/vlbi_metrology.py`  
**Lines of code:** 1726  
**Generated:** 2026-07-14T14:36:01.279020+00:00

## Module documentation

VLBI-inspired advanced metrology for ground-based GRS photography
================================================================

Literal radio VLBI reaches microarcseconds on *compact* continuum sources with
baselines of Earth diameter, phase referencing, and delay models. An optical
photo of an extended cloud feature cannot match that floor.

This module brings the *methodology* of high-end interferometric metrology to
lucky-imaging / high-res planetary work:

  1) Full geometric model (orientation, distance, light-time, time→CM)
  2) Calibrated instrument response (limb nav bootstrap, PSF/μ-aware maps)
  3) Phase-reference analog: local differential probe injection–recovery
     using the *same* science pipeline (not a weaker dark-peak finder)
  4) Multi-definition closure (template / map / moment / multi-scale)
  5) Multi-filter residual closure after 1/λ² DCR model
  6) Hierarchical Monte Carlo (limb ⊕ map noise ⊕ template hyperparameters ⊕ time)
  7) Formal error budget: random ⊕ systematic ⊕ geometry ⊕ time ⊕ bias uncertainty
  8) Publication bundle with hashes, seeds, and intermediate products

Honest optical envelope (ground-based extended feature):
  - Diffraction: ~0.15″ for 1 m @ 550 nm (Airy); lucky imaging often 0.3–1″
  - GRS is extended → definition systematics usually dominate photon noise
  - Target after this stack: **0.1–0.5″ relative** on high-res frames near CM,
    **1–2″ absolute** when ephemeris/CM are approximate; tighter if SPICE/Horizons
    orientation is supplied.

Naming: "VLBI-grade" = VLBI *methods* applied to planetary imaging, not μas claims.

## Overview statistics

| Item | Count |
|------|------:|
| Classes | 4 |
| Top-level functions | 26 |
| Methods | 6 |

## Symbol index

- **class** `EphemerisState` — line 87
  - `EphemerisState.to_dict()` — line 104
- **class** `AdvancedNav` — line 109
  - `AdvancedNav.b_pol_px()` — line 126
  - `AdvancedNav.to_nav_state()` — line 129
  - `AdvancedNav.to_dict()` — line 141
- **class** `ErrorBudget` — line 148
  - `ErrorBudget.to_dict()` — line 167
- **class** `VLBIResult` — line 172
  - `VLBIResult.to_dict()` — line 204
- **function** `planetocentric_to_planetographic()` — line 212
- **function** `time_error_to_lon_sigma()` — line 221
- **function** `_hash_array()` — line 226
- **function** `_rotate_points()` — line 232
- **function** `build_ephemeris_approx()` — line 246
- **function** `enrich_ephemeris_from_horizons()` — line 315
- **function** `fit_limb_advanced()` — line 361
- **function** `make_cylindrical_oriented()` — line 497
- **function** `px_to_lonlat_oriented()` — line 555
- **function** `_ncc_peak()` — line 586
- **function** `multiscale_template_match()` — line 644
- **function** `measure_size_isophote()` — line 734
- **function** `measure_grs_vlbi()` — line 796
- **function** `_local_dark_recover()` — line 928
- **function** `inject_dark_oval_image()` — line 979
- **function** `phase_reference_injection()` — line 1024
- **function** `hierarchical_monte_carlo()` — line 1122
- **function** `definition_suite_vlbi()` — line 1241
- **function** `definition_scatter()` — line 1284
- **function** `filter_closure_vlbi()` — line 1305
- **function** `assemble_formal_budget()` — line 1349
- **function** `optical_diffraction_floor_arcsec()` — line 1422
- **function** `grade_result()` — line 1427
- **function** `run_vlbi_grade()` — line 1447
- **function** `write_vlbi_bundle()` — line 1655
- **function** `research_grade_compat()` — line 1693

## Classes (full detail)

### class `EphemerisState`

- **Defined at:** line 87
- **Methods:** 1

**Class docstring:**

Observer-centric Jupiter geometry for one epoch.

#### Methods

##### `EphemerisState.to_dict(self)`

- **Line:** 104–105

_No docstring. Inferred role: member of `EphemerisState` used by the vlbi_metrology subsystem._

**Signature notes:** Accepts parameters `self`. See source `app/vlbi_metrology.py` around line 104 for implementation.

**Related features:** Any feature that imports `vlbi_metrology` may call this method.

---

### class `AdvancedNav`

- **Defined at:** line 109
- **Methods:** 3

**Class docstring:**

Navigation with full orientation (VLBI-style geometric model).

#### Methods

##### `AdvancedNav.b_pol_px(self)`

- **Line:** 126–127

_No docstring. Inferred role: member of `AdvancedNav` used by the vlbi_metrology subsystem._

**Signature notes:** Accepts parameters `self`. See source `app/vlbi_metrology.py` around line 126 for implementation.

**Related features:** Any feature that imports `vlbi_metrology` may call this method.

---

##### `AdvancedNav.to_nav_state(self)`

- **Line:** 129–139

_No docstring. Inferred role: member of `AdvancedNav` used by the vlbi_metrology subsystem._

**Signature notes:** Accepts parameters `self`. See source `app/vlbi_metrology.py` around line 129 for implementation.

**Related features:** Any feature that imports `vlbi_metrology` may call this method.

---

##### `AdvancedNav.to_dict(self)`

- **Line:** 141–144

_No docstring. Inferred role: member of `AdvancedNav` used by the vlbi_metrology subsystem._

**Signature notes:** Accepts parameters `self`. See source `app/vlbi_metrology.py` around line 141 for implementation.

**Related features:** Any feature that imports `vlbi_metrology` may call this method.

---

### class `ErrorBudget`

- **Defined at:** line 148
- **Methods:** 1

**Class docstring:**

Formal VLBI-style error budget in degrees and arcsec.

#### Methods

##### `ErrorBudget.to_dict(self)`

- **Line:** 167–168

_No docstring. Inferred role: member of `ErrorBudget` used by the vlbi_metrology subsystem._

**Signature notes:** Accepts parameters `self`. See source `app/vlbi_metrology.py` around line 167 for implementation.

**Related features:** Any feature that imports `vlbi_metrology` may call this method.

---

### class `VLBIResult`

- **Defined at:** line 172
- **Methods:** 1

**Class docstring:**

Publication product — VLBI-inspired ground optical metrology.

#### Methods

##### `VLBIResult.to_dict(self)`

- **Line:** 204–205

_No docstring. Inferred role: member of `VLBIResult` used by the vlbi_metrology subsystem._

**Signature notes:** Accepts parameters `self`. See source `app/vlbi_metrology.py` around line 204 for implementation.

**Related features:** Any feature that imports `vlbi_metrology` may call this method.

---

## Top-level functions (full detail)

### `planetocentric_to_planetographic(lat_c_deg, flattening)`

- **Module:** `vlbi_metrology.py`
- **Line:** 212–218

**Docstring:**

Jupiter planetographic latitude from planetocentric.

**Parameters:** `lat_c_deg, flattening`

**How to find callers:** search the repo for `planetocentric_to_planetographic(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `time_error_to_lon_sigma(time_error_seconds)`

- **Module:** `vlbi_metrology.py`
- **Line:** 221–223

**Docstring:**

System III longitude uncertainty from absolute timing error.

**Parameters:** `time_error_seconds`

**How to find callers:** search the repo for `time_error_to_lon_sigma(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `_hash_array(a)`

- **Module:** `vlbi_metrology.py`
- **Line:** 226–229

_No docstring in source._ This function is part of `vlbi_metrology.py`. Open `app/vlbi_metrology.py` at line 226 for the full implementation.

**Parameters:** `a`

**How to find callers:** search the repo for `_hash_array(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `_rotate_points(xs, ys, xc, yc, pa_deg)`

- **Module:** `vlbi_metrology.py`
- **Line:** 232–239

**Docstring:**

Rotate image coords so that Jovian north aligns with -y after rotation by -PA.

**Parameters:** `xs, ys, xc, yc, pa_deg`

**How to find callers:** search the repo for `_rotate_points(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `build_ephemeris_approx(t_utc_iso, time_error_seconds, cm_override, distance_override)`

- **Module:** `vlbi_metrology.py`
- **Line:** 246–312

**Docstring:**

Analytical geometry (differentials OK; absolute CM has ~degree-level zero).

**Parameters:** `t_utc_iso, time_error_seconds, cm_override, distance_override`

**How to find callers:** search the repo for `build_ephemeris_approx(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `enrich_ephemeris_from_horizons(eph)`

- **Module:** `vlbi_metrology.py`
- **Line:** 315–354

**Docstring:**

Pull distance / diameter from Horizons when online; parse best-effort.

**Parameters:** `eph`

**How to find callers:** search the repo for `enrich_ephemeris_from_horizons(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `fit_limb_advanced(image, eph, n_rays, bootstrap, seed, apply_sub_lat)`

- **Module:** `vlbi_metrology.py`
- **Line:** 361–490

**Docstring:**

Precision limb: trust the stable multi-iteration radial-gradient centre
(`fit_limb_nav`), then bootstrap ray subsets *around that centre* only to
estimate σ(xc, yc, a). Re-fitting the centre from a single ray pass is
unstable on banded planets (SEB/NEB gradients pull the ellipse).

Sub-observer latitude is applied only when `apply_sub_lat=True` or when
|sub_lat| is known from a real ephemeris override (not the crude seasonal model).

**Parameters:** `image, eph, n_rays, bootstrap, seed, apply_sub_lat`

**How to find callers:** search the repo for `fit_limb_advanced(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `make_cylindrical_oriented(image, nav, width, height)`

- **Module:** `vlbi_metrology.py`
- **Line:** 497–552

**Docstring:**

Orthographic sample with sub-observer latitude tilt.
lon ∈ [-90, 90] about CM, lat planetocentric.

**Parameters:** `image, nav, width, height`

**How to find callers:** search the repo for `make_cylindrical_oriented(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `px_to_lonlat_oriented(y, x, nav)`

- **Module:** `vlbi_metrology.py`
- **Line:** 555–579

**Docstring:**

Inverse of oriented orthographic (planetocentric).

**Parameters:** `y, x, nav`

**How to find callers:** search the repo for `px_to_lonlat_oriented(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `_ncc_peak(band, tmpl)`

- **Module:** `vlbi_metrology.py`
- **Line:** 586–641

**Docstring:**

Return subpixel (py, px, peak) via FFT NCC.

**Parameters:** `band, tmpl`

**How to find callers:** search the repo for `_ncc_peak(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `multiscale_template_match(cyl, nav, lat0, lengths, widths)`

- **Module:** `vlbi_metrology.py`
- **Line:** 644–731

**Docstring:**

Multi-scale dark-oval correlator: grid of (L,W) templates, pick max NCC,
subpixel peak. Primary scientific definition for optical GRS centre.

**Parameters:** `cyl, nav, lat0, lengths, widths`

**How to find callers:** search the repo for `multiscale_template_match(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `measure_size_isophote(cyl, lon_iii, lat0, nav, level_frac)`

- **Module:** `vlbi_metrology.py`
- **Line:** 734–789

**Docstring:**

Isophote ellipse size around GRS on cylindrical map (degrees).
More physical than intensity-moment eigenvalues of a tiny blob.

**Parameters:** `cyl, lon_iii, lat0, nav, level_frac`

**How to find callers:** search the repo for `measure_size_isophote(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `measure_grs_vlbi(image, nav, map_width, map_height, quiet)`

- **Module:** `vlbi_metrology.py`
- **Line:** 796–921

**Docstring:**

VLBI-style correlator primary + multi-method closure for systematics.
Point estimate is multiscale NCC (locked); others enter scatter only if sane.

**Parameters:** `image, nav, map_width, map_height, quiet`

**How to find callers:** search the repo for `measure_grs_vlbi(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `_local_dark_recover(cyl, nav, lon_hint, lat_hint, lon_half_win, lat_half_win)`

- **Module:** `vlbi_metrology.py`
- **Line:** 928–976

**Docstring:**

Local dark centroid on cylindrical map (probe recovery fallback).

**Parameters:** `cyl, nav, lon_hint, lat_hint, lon_half_win, lat_half_win`

**How to find callers:** search the repo for `_local_dark_recover(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `inject_dark_oval_image(image, nav, lon_iii, lat_deg, length_deg, width_deg, depth)`

- **Module:** `vlbi_metrology.py`
- **Line:** 979–1021

_No docstring in source._ This function is part of `vlbi_metrology.py`. Open `app/vlbi_metrology.py` at line 979 for the full implementation.

**Parameters:** `image, nav, lon_iii, lat_deg, length_deg, width_deg, depth`

**How to find callers:** search the repo for `inject_dark_oval_image(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `phase_reference_injection(image, nav, grs_lon, grs_lat, n_trials, seed)`

- **Module:** `vlbi_metrology.py`
- **Line:** 1024–1115

**Docstring:**

VLBI phase-reference analog:
  inject known probes AWAY from real GRS, recover with multiscale science
  correlator (same code path), estimate bias + scatter.

**Parameters:** `image, nav, grs_lon, grs_lat, n_trials, seed`

**How to find callers:** search the repo for `phase_reference_injection(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `hierarchical_monte_carlo(image, nav, eph, n_iter, time_error_seconds, seed)`

- **Module:** `vlbi_metrology.py`
- **Line:** 1122–1234

**Docstring:**

VLBI-style hierarchical error simulation:
  limb jitter → map noise → template scale → CM/time prior.

**Parameters:** `image, nav, eph, n_iter, time_error_seconds, seed`

**How to find callers:** search the repo for `hierarchical_monte_carlo(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `definition_suite_vlbi(image, nav)`

- **Module:** `vlbi_metrology.py`
- **Line:** 1241–1281

_No docstring in source._ This function is part of `vlbi_metrology.py`. Open `app/vlbi_metrology.py` at line 1241 for the full implementation.

**Parameters:** `image, nav`

**How to find callers:** search the repo for `definition_suite_vlbi(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `definition_scatter(defs, primary_lon, primary_lat)`

- **Module:** `vlbi_metrology.py`
- **Line:** 1284–1302

_No docstring in source._ This function is part of `vlbi_metrology.py`. Open `app/vlbi_metrology.py` at line 1284 for the full implementation.

**Parameters:** `defs, primary_lon, primary_lat`

**How to find callers:** search the repo for `definition_scatter(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `filter_closure_vlbi(channels, nav)`

- **Module:** `vlbi_metrology.py`
- **Line:** 1305–1342

_No docstring in source._ This function is part of `vlbi_metrology.py`. Open `app/vlbi_metrology.py` at line 1305 for the full implementation.

**Parameters:** `channels, nav`

**How to find callers:** search the repo for `filter_closure_vlbi(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `assemble_formal_budget(lat_deg, distance_au, rand_lon, rand_lat, def_lon, def_lat, nav, eph, time_error_seconds, bias_unc_lon, bias_unc_lat, closure_sky)`

- **Module:** `vlbi_metrology.py`
- **Line:** 1349–1419

_No docstring in source._ This function is part of `vlbi_metrology.py`. Open `app/vlbi_metrology.py` at line 1349 for the full implementation.

**Parameters:** `lat_deg, distance_au, rand_lon, rand_lat, def_lon, def_lat, nav, eph, time_error_seconds, bias_unc_lon, bias_unc_lat, closure_sky`

**How to find callers:** search the repo for `assemble_formal_budget(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `optical_diffraction_floor_arcsec(diameter_m, wavelength_nm)`

- **Module:** `vlbi_metrology.py`
- **Line:** 1422–1424

**Docstring:**

λ/D in arcsec — absolute hard floor for a filled aperture (not VLBI baseline).

**Parameters:** `diameter_m, wavelength_nm`

**How to find callers:** search the repo for `optical_diffraction_floor_arcsec(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `grade_result(sig_tot, inj_mean, optical_floor)`

- **Module:** `vlbi_metrology.py`
- **Line:** 1427–1440

_No docstring in source._ This function is part of `vlbi_metrology.py`. Open `app/vlbi_metrology.py` at line 1427 for the full implementation.

**Parameters:** `sig_tot, inj_mean, optical_floor`

**How to find callers:** search the repo for `grade_result(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `run_vlbi_grade(image, user_time_iso, time_error_seconds, cm_iii_deg, distance_au, channels, injection_trials, mc_iter, seed, aperture_m, use_horizons, factory_mode, nav, winjupos_path, sub_lat_override, north_pa_override, use_spice, use_pro_ephemeris)`

- **Module:** `vlbi_metrology.py`
- **Line:** 1447–1652

**Docstring:**

Full VLBI-inspired optical metrology reduction for one epoch.

Absolute System III uses professional ephemeris chain when available:
override → WinJUPOS → SPICE → Horizons full → analytical.

**Parameters:** `image, user_time_iso, time_error_seconds, cm_iii_deg, distance_au, channels, injection_trials, mc_iter, seed, aperture_m, use_horizons, factory_mode, nav, winjupos_path, sub_lat_override, north_pa_override, use_spice, use_pro_ephemeris`

**How to find callers:** search the repo for `run_vlbi_grade(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `write_vlbi_bundle(path, result, extra)`

- **Module:** `vlbi_metrology.py`
- **Line:** 1655–1690

_No docstring in source._ This function is part of `vlbi_metrology.py`. Open `app/vlbi_metrology.py` at line 1655 for the full implementation.

**Parameters:** `path, result, extra`

**How to find callers:** search the repo for `write_vlbi_bundle(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `research_grade_compat(result)`

- **Module:** `vlbi_metrology.py`
- **Line:** 1693–1726

**Docstring:**

Shape compatible with existing UI headline / research_grade fields.

**Parameters:** `result`

**How to find callers:** search the repo for `research_grade_compat(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

## Large-module guide

`vlbi_metrology.py` is a large module (1726 lines). When editing:

1. Prefer adding helpers near related symbols rather than new global state.
2. Keep I/O (files, network) at the edges.
3. After changes, run `python3 cli.py certify --n 15` from `app/`.


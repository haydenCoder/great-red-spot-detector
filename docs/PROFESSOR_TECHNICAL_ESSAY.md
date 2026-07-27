GRS Observatory
========================================================================

Automated Optical Metrology of Jupiter's Great Red Spot:
System Design, Geometric Foundations, and Software Architecture

Software version: 6.5.0
Document revised: 2026-07-28
Codebase scale: 41 application modules under app/ (~31k lines); imaging monolith reduced
(~4.5k lines live path after dead-bulk removal). User-facing product centres on
Champion Ultimate, job_finalize parity, publication hierarchy, and SUPERDUPER archival cards.

------------------------------------------------------------------------

# Abstract

This essay presents a comprehensive technical account of GRS Observatory, a software system
for automated measurement of Jupiter's Great Red Spot (GRS) on ground-based optical images.
The system couples observation-time discipline, multi-source planetary ephemerides (SPICE,
JPL Horizons, and WinJUPOS central-meridian tables), multi-isophote limb navigation,
cylindrical deprojection, multi-estimator localization under a fixed publication definition,
a **Champion Ultimate** automated path with dual-channel and nav-stability tests, an
**UNBEATABLE_AUTO** multi-gate lock (in-app hierarchy only), SUPERDUPER one-page archival
cards, formal uncertainty bookkeeping (CM ⊕ timing ⊕ limb ⊕ definition ⊕ method), synthetic
stress testing, and optional convolutional assistance with bundled network weights. The
exposition proceeds from the scientific measurement equation through layered software
architecture to module-level analysis. Empirical performance evaluation is reserved for a
subsequent results section and is not presupposed herein.

**Honesty clause.** Optical ground-based metrology is not radio VLBI. Horizons and SPICE
supply planet geometry, not a NASA GRS longitude catalog. The grade UNBEATABLE_AUTO asserts
that every automated quality gate *in this application* passed on a given frame; it does
not assert superiority over HST, JunoCam, or a carefully executed human WinJUPOS measure.

# 1. Introduction

## 1.1 Scientific setting

The Great Red Spot is a persistent anticyclonic vortex in Jupiter's southern hemisphere,
classically situated near approximately 22° south. Its east–west angular extent has
diminished over the modern instrumental era; contemporary longitudinal lengths are typically
of order ten to fifteen degrees of System III, subject to epoch and to the precise isophotal
or morphological definition adopted by the observer. Longitudinal position is customarily
reported in System III (1965), tied to the central meridian of the illuminated disk at the
instant of observation.

A single resolved planetary image therefore defines a composite inference problem. One must
establish the mid-exposure epoch; recover the planet's apparent geometry (range, light-time,
central meridian in System III, sub-observer latitude, and north position angle on the sky);
locate the planetary limb in detector coordinates; adopt a fixed definition of the GRS (dark
core, edge extrema, elliptical envelope, or related constructs); and report both the
estimate and its provenance so that the measurement remains interpretable in later analysis.

Interactive software such as WinJUPOS has long supplied the geometric desk upon which
careful observers navigate the limb and mark features. Automation does not displace that
discipline; it encodes it. GRS Observatory is constructed around the same algebraic
decomposition of absolute longitude, while adding batch reduction, multi-method diagnostics,
synthetic verification harnesses, and machine-readable job archives.

A first-class **dual measure** path (version 6.1+) is **mandatory on desktop Process full**:
the operator always sees two limb outlines on the image—**green automatic** limb fit and
**cyan by-eye** outline—then the pipeline runs the full automatic stack and a human pass
that applies the cyan limb (scale/shift), optional flips, and an explicit feature
**definition** (GS-MAP dark core, GS-BARY, outline mid-edge, raw pipeline, or manual
lon/lat paste). Synthetic jobs may open the same dialog optionally. Both answers are
stored in `dual_measure.json` with a sky-arcsecond delta so definition and limb
sensitivity are visible rather than hidden inside a single opaque number.

This design follows JUPOS *Tips for Measurers* and WinJUPOS outline practice: automatic
outline is approximate; the centre of the GRS (not an arbitrary rim) is the preferred
longitude target; larger versus smaller outline choices change size and can shift
longitude and must be declared, not confused with “error versus NASA.” There is no
NASA GRS longitude catalog for arbitrary epochs—Horizons/SPICE supply geometry only.
Accuracy is assessed by agreement with a careful WinJUPOS (or equivalent) measure under
the same mid-exposure UTC, CM source, and definition.

## 1.2 Scope of the system

GRS Observatory version 6.5.0 is a Python application oriented to high-resolution planetary
stills and short sequences (FITS, SER, PNG, JPEG). It provides a native desktop interface, a
command-line interface, and an optional local web service. Geometry is obtained
preferentially from SPICE kernels with automatic acquisition, with fallback and cross-check
paths through JPL Horizons and user-supplied WinJUPOS central-meridian tables; when both
SPICE and Horizons succeed, |ΔCM| is retained for ultimate-lock gates. Localization proceeds
from multi-isophote limb navigation and oriented cylindrical mapping through classical
estimators and a **Champion** path (dark-core scoring, multi-size templates, sub-pixel
refine, dual-channel agreement, nav-stability jitter tests). The designated published
center follows a hierarchy: UNBEATABLE_AUTO / Champion absolute product when gates pass,
else GS-MAP (map-plane dark centroid), else GS-BARY. Approximately eighty auxiliary
estimators contribute definition and algorithm scatter rather than the published coordinate.
Every science job writes SUPERDUPER_BEST_ANSWER.* and champion.* alongside publish.*.
Optional neural inference employs SPIRE-Net weights distributed with the application (soft
prior only).

## 1.3 Organization of this essay

Section 2 states the measurement equation and publication policy, including Champion Ultimate
and SUPERDUPER archival products (§2.5). Section 3 describes system architecture and data
flow. Sections 4 through 11 develop each functional layer with reference to the implementing
modules. Section 12 situates the design relative to interactive WinJUPOS practice, including
the dual automatic/human measure path. Section 13 discusses limitations and extensions.
Section 14 is reserved for experimental results. Appendices inventory modules and glossary
terms; line-count tables in older appendices reflect pre-6.4 inventory and should be treated
as historical where they conflict with the live tree.

### Dual automatic + human measure (implemented)

Modules: `app/human_choice.py` (dialog with **green AUTO** and **cyan BY EYE** ellipses,
keyboard fine-tune, definition picker), `desktop_pipeline.apply_dual_human_pass`,
desktop **Process full (auto limb + by-eye limb)** always opens the dual limb UI.

| Pass | Limb | Role |
|------|------|------|
| Automatic | Green auto-fit | Full research-grade / twin / publish baseline |
| Human (by eye) | Cyan outline you adjust | Definition (default **GS-MAP+RIM**: core lon + outer W–E size) + limb + flip |
| dual_measure.* | both | Side-by-side lon/lat, Δsky ″, agreement; official = human by default |

**Operator controls (WinJUPOS-like):** arrow keys move outline; Page Up/Down change size;
R resets cyan to green; mouse drag moves centre; GS-MAP recommended for published lon.

**Tip sheet** is stored on every dual job (`human_choice.tips_applied` / `ALL_MEASURE_TIPS`)
and merged into full-report accuracy tips: mid-exposure UTC; ~0.6°/min Sys III time error;
SPICE/Horizons CM; red channel for GRS; soup/SOTA scatter only; no fake NASA GRS REF lon;
validate with WinJUPOS paste equality (Δsky).

# 2. Measurement Equation and Publication Policy

## 2.1 Decomposition of System III longitude

An absolute System III longitude of a cloud feature may be written

    λ_III = CM_III(t_mid) + λ_rel(image, navigation, definition)

The first term is ephemeris geometry at mid-exposure. System III rotates with a sidereal
period of approximately 9 h 55 m 29.711 s, corresponding to roughly 36° per hour or 0.6° per
minute. Errors in t_mid therefore map linearly into absolute longitude. The implementation
treats mid-exposure Universal Time as mandatory on absolute paths: user-supplied UTC, or
extraction from FITS metadata (DATE-AVG, DATE-OBS with exposure midpoint where appropriate,
MJD-OBS). Silent substitution of wall-clock time is refused.

The second term is the feature's longitudinal offset from the central meridian in the
navigated image. It depends on limb parameters (disk center, equatorial scale, flattening),
map orientation (sub-observer latitude and north position angle when applied), and the
adopted morphological definition of the GRS. Changing definition from dark core to western
edge is not random noise; it is a different observable, often displaced by a substantial
fraction of the oval's length.

## 2.2 Circular statistics

Longitudes are represented on [0, 360). Angular differences employ the principal value

    Δλ = ((λ1 − λ2 + 180) mod 360) − 180

as implemented by wrap_deg and wrap_diff. Ensemble combinations of longitude use unit-vector
averages of sine and cosine components, avoiding discontinuities at the 0°/360° cut.

## 2.3 Designated publication product

Module `publish_primary.py` enforces an ordered publication rule. When the Champion path
reports absolute (or ultimate) success, its centre is preferred; the label UNBEATABLE_AUTO is
used when all ultimate gates pass. Otherwise the classic order is GS-MAP (map-plane dark
centroid in the GRS latitude band), then GS-BARY. The multi-method catalog and the robust
SOTA consensus are retained as scatter diagnostics only. Edge measures (west, east, mid of
edges) support extent science and are not substituted for the published center. Job packages
write `publish.json` / `publish.txt` and `SUPERDUPER_BEST_ANSWER.*` so that the designated
product is unambiguous in archival use.

Planetocentric latitude (engine geometry) and planetographic latitude (WinJUPOS-style) are
both exported. Operators comparing to interactive desks should quote φ_g for latitude and
hold the same CM and mid-exposure UTC.

## 2.4 Optional concordance with interactive measures

When an operator pastes a carefully obtained interactive longitude and latitude (for example
from WinJUPOS) together with a trusted central-meridian source, the software computes the
sky-plane separation between the published automated center and the interactive pick. A
concordance flag is raised when that separation lies at or below one arcsecond and the
ephemeris source is among {winjupos, override, spice, horizons}. The flag is a computational
criterion on a single comparison, not a seasonal statistical study.

## 2.5 Champion Ultimate, UNBEATABLE_AUTO, and SUPERDUPER

Module `champion_measure.py` implements the strongest automated optical path in the
application. Conceptually it stacks professional desk discipline in software:

1. Prefer red / visual-red intensity for GRS contrast (JUPOS spectral practice).  
2. Multi-isophote limb fits with stability weights (outline size systematics).  
3. Oriented cylindrical map (sub-observer latitude and north PA when ephemeris supplies them).  
4. Named estimators: GS-MAP, multi-size GS-TMPL, precision engine, map dark, barycentre.  
5. Local dark-core score (core versus annular ring) to demote SEB wave false locks.  
6. Two-pass map-domain sub-pixel refine and local noise bootstrap.  
7. Nav-stability: jitter limb centre/radius and require the GRS lock not to wander.  
8. Dual-channel: independent refine on mono versus red; require agreement when both exist.  
9. Full absolute error budget:  
   σ_lon² ≈ σ_CM² + σ_timing² + σ_limb² + σ_definition² + σ_method²  
   converted to sky arcseconds at the adopted range.  

**UNBEATABLE_AUTO** is a multi-gate boolean (trusted CM; SPICE↔Horizons |ΔCM| when both
exist; SEB latitude; dark core; limb and definition tightness; nav stability; dual-channel
agreement; score and optional GS-MAP↔GS-TMPL lock; total σ_sky bound). When true, the
publication hierarchy treats the Champion product as dominant over pipeline, soup, and SOTA
*within this codebase*. The grade is intentionally named with an `_AUTO` suffix so it cannot
be misread as a claim against spacecraft imaging or perfect interactive work.

Module `superduper.py` does not remeasure. It consolidates publish + Champion + WinJUPOS+
into `SUPERDUPER_BEST_ANSWER.txt` / `.json` and `REPORT_THIS_ONE_LINE.txt`—a single human
card answering “what do I report tonight?” The full human report (`result_report.py`) leads
with that card.

# 3. System Architecture

## 3.1 Layered structure

```
Presentation   desktop_app.py | cli.py | server.py
Orchestration  product_core.py | desktop_pipeline.py
Publication    champion_measure | superduper | publish_primary
               winjupos_plus | winjupos_twin | gold_standard
Localization   precision_engine | research_grade | vlbi_metrology
               sota_accuracy | all_methods | all_methods_extra
Geometry       fits_time | spice_auto | ephemeris_pro | nasa_compare
Verification   synthetic_hq | hard_synth_suite | batch_prove | multi_epoch | limb_validation
Learning       nn_grs | ai_hard_cases   (+ app/models/*.npz)
Imaging        grs_complete_system.py  (live science path; generated dead bulk removed)
Infrastructure paths | result_report | verbose_log | ram_ssd | security_hard
               group_access | accounts | admin_console | license_manager
```

## 3.2 Canonical reduction of a science image

Function `desktop_pipeline.run_process_full` implements the reference path: (i) load mono or
multichannel data (prefer red for GRS); (ii) resolve mid-exposure UTC through
`fits_time.require_observation_time`; (iii) resolve planetary geometry through
`resolve_pro_ephemeris`; (iv) fit limb parameters and attach orientation to NavState when
applicable; (v) execute research-grade reduction (optical metrology stack by default);
(vi) attach gold-standard definitions; (vii) attach WinJUPOS-twin products including
limb-outline and definition sensitivity; (viii) attach **Champion Ultimate** (ultimate gates,
full σ); (ix) apply publication policy; (x) optional dual human pass; (xi) attach WinJUPOS+
and **SUPERDUPER** cards; (xii) serialize the job archive under outputs/.

## 3.3 Synthetic verification path

synthetic_hq.generate constructs a controlled planetary scene with injected GRS parameters
and image-tied geometry. Measurement reuses the same orchestration as science frames,
enabling recovery tests under prescribed degradations. Such tests probe algorithmic
consistency; they do not by themselves establish performance on filamentary, seeing-limited
natural imagery.

## 3.4 Quantitative scale of the implementation

Application modules analyzed: 35
Approximate total lines in app/*.py: 32347

| Module | Lines | Classes | Top-level functions |
|--------|------:|--------:|--------------------:|
| grs_complete_system.py | 10350 | 47 | 1806 |
| desktop_app.py | 1850 | 2 | 3 |
| vlbi_metrology.py | 1747 | 4 | 26 |
| nn_grs.py | 1694 | 1 | 42 |
| server.py | 1664 | 0 | 34 |
| sota_accuracy.py | 1192 | 1 | 16 |
| precision_engine.py | 1065 | 2 | 23 |
| all_methods.py | 995 | 1 | 33 |
| gold_standard.py | 990 | 2 | 16 |
| all_methods_extra.py | 905 | 0 | 33 |
| ephemeris_pro.py | 873 | 1 | 14 |
| desktop_pipeline.py | 809 | 0 | 11 |
| result_report.py | 781 | 0 | 11 |
| research_grade.py | 740 | 3 | 9 |
| synthetic_hq.py | 682 | 1 | 12 |
| winjupos_twin.py | 577 | 2 | 6 |
| spice_auto.py | 540 | 2 | 12 |
| multi_epoch.py | 494 | 2 | 9 |
| license_manager.py | 455 | 1 | 14 |
| batch_prove.py | 401 | 0 | 3 |
| ai_hard_cases.py | 399 | 1 | 6 |
| hard_synth_suite.py | 384 | 2 | 5 |
| accounts.py | 356 | 1 | 21 |
| product_core.py | 341 | 1 | 6 |
| cli.py | 280 | 0 | 1 |
| publish_primary.py | 267 | 0 | 4 |
| nasa_compare.py | 262 | 1 | 5 |
| security_hard.py | 218 | 1 | 10 |
| group_access.py | 187 | 0 | 6 |
| paths.py | 187 | 0 | 10 |
| admin_console.py | 183 | 1 | 8 |
| fits_time.py | 177 | 0 | 5 |
| limb_validation.py | 124 | 0 | 3 |
| ram_ssd.py | 123 | 0 | 10 |
| verbose_log.py | 55 | 1 | 0 |

# 4. Interfaces and Product Surface

Three presentation surfaces—desktop, command line, and local HTTP—share scientific
orchestration through product_core and desktop_pipeline, minimizing permanent divergence
among reduction paths.

### 4.`desktop_app.py` (1850 lines)

Module documentation as implemented in source:

GRS Observatory — native macOS desktop app (no web browser).  Full feature set: synthetic
(1080p–16K), max-stack process, pro ephemeris, WinJUPOS, multi-epoch, hard-synth, factory
night, SPIRE-Net, complete results.

#### Classes

**LogBridge** (source line 301)

Methods:

- `__init__(q)` — line 302
- `poll()` — line 306

**GRSDesktopApp** (source line 315)

Methods:

- `__init__()` — line 316
- `_build_menu()` — line 365
- `_license_show()` — line 379
- `_license_activate()` — line 398
- `_license_copy_machine()` — line 416
- `_manual_path()` — line 426
  Only user guide: GRS_OBSERVATORY_BOOK.md
- `_open_manual()` — line 439
- `_about()` — line 459
- `_refresh_license_badge()` — line 480
- `_build_style()` — line 493
- `_build_ui()` — line 556
- `_section(parent, title)` — line 986
  Section title: black bold on light grey strip.
- `_labeled_entry(parent, label, var, desc)` — line 995
- `_labeled_combo(parent, label, var, values, desc)` — line 1014
- `_check(parent, text, var, desc)` — line 1030
  Checkbox with black text + grey description under it.
- `_action_btn(parent, text, cmd, desc, color, secondary)` — line 1055
  Full-width button with grey description underneath (no ? icons).
- `_set_busy(busy, status)` — line 1094
- `_log_ui(level, msg)` — line 1111
- `_results(text)` — line 1116
- `_update_metrics(package)` — line 1121
- `_show_preview(path)` — line 1192
- `_tick()` — line 1223
- `_gate(feature)` — line 1273
  Feature gate (fail-open for free local use).
- `_run_bg(name, fn)` — line 1277
- `_mc()` — line 1325
- `_inj()` — line 1331
- `_float_opt(var)` — line 1337
- `_aperture()` — line 1346
- `_time_error()` — line 1352
- `on_clear()` — line 1359
- `on_open_outputs()` — line 1366
- `on_save_results()` — line 1371
- `on_open_file()` — line 1384
- `on_winjupos()` — line 1398
- `on_synthetic()` — line 1411
- `on_synthetic_only()` — line 1432
  Generate image only — no metrology (clear separate button).
- `on_process()` — line 1451
- `on_ephemeris()` — line 1489
- `on_multi()` — line 1528
- `on_hard()` — line 1562
- `on_factory()` — line 1594
- `_nn_epochs()` — line 1612
- `_nn_samples()` — line 1618
- `_nn_lr()` — line 1624
- `_nn_hours()` — line 1631
- `_nn_cache()` — line 1637
- `on_nn_stop()` — line 1643
- `on_nn_train()` — line 1653

#### Functions

- **`app_base_dir()`** — line 22

- **`bundle_code_dir()`** — line 34

- **`main()`** — line 1840

Free open — no login passcode.

### 4.`cli.py` (280 lines)

Module documentation as implemented in source:

GRS Observatory — professional command-line interface
=====================================================  Examples:   python3 cli.py version
python3 cli.py eph "2026-07-14 12:00:00"   python3 cli.py synth --mode metrology --res 1080p
python3 cli.py process /path/to/jupiter.fits --time "2026-01-09 17:06:00"   python3 cli.py
certify --n 30

#### Functions

- **`main(argv)`** — line 21

### 4.`server.py` (1664 lines)

Module documentation as implemented in source:

GRS Observatory v3 — optical metrology optical metrology for ground-based GRS photos.  Target:
best-in-class planetary imaging metrology (formal error budgets, multi-scale honest optical
floor for an extended cloud feature.

#### Functions

- **`_wj_manual_from_data(data)`** — line 67

- **`_run_gold(package, meas)`** — line 82

- **`_security_before()`** — line 210

Rate limit + host checks for common abuse patterns.

- **`_no_cache_static(resp)`** — line 227

- **`_start(kind)`** — line 245

Start job. Returns (short_hex, run_n, output_dir with detailed name).

- **`_finish(result, error)`** — line 260

- **`_find_output_dir(job_id)`** — line 267

Locate output folder by short hex id (suffix) or full folder name.

- **`_attach_human_report(package, out, run_n)`** — line 283

Build the long human report (YOUR vs NASA, diffs, tips, full dump), write FULL_REPORT*.txt,
and put text on the package for the web UI.

- **`index()`** — line 317

- **`health()`** — line 322

- **`logs()`** — line 352

- **`logs_clear()`** — line 357

- **`verbose()`** — line 363

- **`job()`** — line 370

- **`regions()`** — line 376

Synthetic image framing only — not observer location.

- **`countries()`** — line 388

Observer country for timezone clarity (not synthetic framing).

- **`tips()`** — line 394

- **`resolutions()`** — line 399

- **`nn_status()`** — line 409

- **`nn_train()`** — line 414

- **`nn_stop()`** — line 470

- **`upload()`** — line 476

- **`process()`** — line 519

- **`synthetic()`** — line 779

- **`api_ephemeris()`** — line 1000

Resolve professional Jupiter ephemeris (WinJUPOS / SPICE / Horizons / analytical).

- **`winjupos_template()`** — line 1031

- **`winjupos_upload()`** — line 1037

- **`api_multi_epoch()`** — line 1050

Differential multi-epoch tracking (VLBI phase-ref across nights).  Body:   directory: scan
outputs dir (default app/outputs)   epochs: optional list of {path} or {t_utc_iso,
lon_iii_deg, lat_deg, ...}   ref_index: 0

- **`api_hard_synth()`** — line 1115

Run hard synthetic stress suite (mismatch physics calibration).

- **`capabilities()`** — line 1159

Everything the advanced stack can do — for UI discovery.

- **`api_factory_night()`** — line 1196

1) Pro ephemeris   2) HQ synthetic (+ VLBI measure)  OR  process uploaded path   3) Multi-
epoch differential (scan outputs)   4) Hard-synth stress suite

- **`output_file(job_id, filename)`** — line 1596

- **`file_api()`** — line 1617

Serve only job outputs and uploads — never license / owner logs / models.

- **`main()`** — line 1644

### 4.`product_core.py` (341 lines)

Module documentation as implemented in source:

GRS Observatory — product core (single professional entry surface)
=================================================================  All shippable workflows
should call into this module rather than duplicating process/synthetic logic across desktop
and server.  Product version is read from ../VERSION when available.

#### Classes

**ProductInfo** (source line 38)

Methods:

- `to_dict()` — line 46

#### Functions

- **`product_version()`** — line 25

- **`default_out_root()`** — line 50

- **`process_image(path, user_time)`** — line 56

Professional Process entry — real image metrology (+ WinJUPOS twin).

- **`generate_synthetic()`** — line 103

Synthetic generation (+ optional measure).  Uses the SAME desktop full stack as the UI
(VLBI/research-grade) so CLI certify numbers match Process / Synthetic buttons.

- **`resolve_ephemeris(user_time)`** — line 179

- **`certify()`** — line 192

Product certification suite — metrology synthetics + SPICE + dual recovery.  Exit criteria
are professional (honest) gates for shipping, not fantasy 0.00″.

### 4.`desktop_pipeline.py` (809 lines)

Module documentation as implemented in source:

Shared advanced processing for the desktop app. Runs the full research-oriented stack and
writes a complete job package.

#### Functions

- **`array_to_rgb_u8(arr, max_side)`** — line 34

Convert mono/CHW/HWC float arrays to sharp RGB uint8 for web/desktop preview.

- **`write_image_preview(arr_or_path, dest)`** — line 85

Write a sharp browser-ready PNG preview of a FITS/array/image path. Prefer this over
pipeline lrgb products which can look soft or missing.

- **`next_run_id(out_root, kind)`** — line 121

Allocate a sequential run number + detailed job slug. Returns (run_n, short_hex,
folder_name) e.g. (42, 'a1b2c3d4e5f6', 'job_run0042_20260715T163045_a1b2c3d4e5f6')

- **`metrics_filename_suffix()`** — line 143

Build a human-readable metric tag for output file names.

- **`_load_image(path)`** — line 166

Return mono-or-CHW array, optional RGB channels, optional preview png path.

- **`_try_imaging_pipeline(path, out, channels, meas)`** — line 194

Run grs full imaging branch when possible (lucky-ish path for stacks).

- **`format_full_report(package)`** — line 221

Human-readable full report: YOUR vs NASA, differences, tips, complete dump.

- **`write_package_reports(out, package)`** — line 227

Attach human text + write FULL_REPORT.txt / job_result.json next to outputs.

- **`run_synthetic_full(out_root)`** — line 245

Generate random-epoch synthetic + full VLBI measure + complete package.

- **`run_process_full(path, out_root)`** — line 469

Process real image with every advanced stage available.

- **`run_factory_night_full(out_root)`** — line 702

## 4.1 Desktop operational surface

The Tkinter application exposes mid-exposure UTC (initially empty), timing uncertainty,
central-meridian and orientation overrides, Horizons and SPICE toggles, WinJUPOS central-
meridian table import, optional interactive longitude/latitude paste, Process, Synthetic,
ephemeris query, factory-night packaging, training controls for the optional network, live
logging, and a results notebook. Dashboard metrics prioritize the published GS-MAP
coordinates. Documentation entry points to the operator handbook.

# 5. Timekeeping and Planetary Ephemerides

Absolute System III position is zero-point limited by central-meridian knowledge. The
geometry stack therefore records provenance on every field and prefers professional kernels
and services over purely analytic rotation models.

### 5.`fits_time.py` (177 lines)

Module documentation as implemented in source:

FITS mid-exposure UTC extraction — never silently use datetime.now().  Policy:   • Prefer
DATE-OBS + TIME-OBS / UT / DATE-AVG / MJD-OBS / EXPTIME mid   • If nothing found → return
None and callers MUST fail or demand user time   • Never default to wall-clock "now" for
System III geometry

#### Functions

- **`_parse_isoish(s)`** — line 19

- **`_hdr_get(hdr)`** — line 44

- **`extract_fits_mid_time(path, hdr)`** — line 70

Returns (mid_utc_naive, source_note). mid is timezone-naive UTC wall time for geometry
callers.

- **`require_observation_time()`** — line 137

Resolve observation UTC or raise ValueError. Never returns datetime.now().

- **`format_utc(dt)`** — line 175

### 5.`spice_auto.py` (540 lines)

Module documentation as implemented in source:

```
SPICE kernel auto-discovery + online download (zero user kernel hunting)
======================================================================

Most planetary-imaging users will never find NAIF kernels by hand. This module:

  1) Ensures spiceypy is importable
  2) Auto-downloads the minimal generic kernel set for Jupiter observer geometry
  3) Verifies kernels load (furnsh) and returns a ready kernel set
  4) Computes observer→Jupiter geometry at an epoch (distance, light-time,
     sub-observer lon/lat in IAU_JUPITER ≈ System III body frame)
  5) Caches under app/ephemeris_data/spice/  (or $GRS_SPICE_KERNELS)

Mirrors (tried in order):
  - NAIF public generic_kernels
  - NAIF /pub/naif mirror path variants

This is the only absolute-geometry path the observatory should rely on for
publication-grade System III work when no WinJUPOS override is pasted.
```

#### Classes

**SpiceStatus** (source line 98)

Methods:

- `to_dict()` — line 108

**SpiceGeometry** (source line 113)

Methods:

- `to_dict()` — line 129

#### Functions

- **`_ssl_context()`** — line 133

- **`_sha256_file(path, max_bytes)`** — line 144

- **`has_spiceypy()`** — line 159

- **`kernel_dir(path)`** — line 167

- **`_existing_kernel(kdir, entry)`** — line 173

- **`_download(url, dest, timeout)`** — line 195

- **`ensure_kernels(kdir, force, timeout)`** — line 235

Search local cache; if required kernels missing, download them online. Returns status (does
not leave kernels furnished).

- **`list_local_kernels(kdir)`** — line 329

- **`_furnsh_all(kdir)`** — line 339

- **`wrap_deg(x)`** — line 357

- **`compute_spice_geometry(t_utc, kdir, auto_download, observer, target)`** — line 361

Full SPICE geometry at UTC datetime or ISO string.  Sub-observer lon/lat in IAU_JUPITER
body-fixed frame. CM III is taken as the body-fixed sub-observer west-style longitude
(convention: wrap_deg of atan2 body-frame observer direction).

- **`selftest()`** — line 522

Download kernels if needed and evaluate one epoch.

### 5.`ephemeris_pro.py` (873 lines)

Module documentation as implemented in source:

```
Professional Jupiter ephemeris for research-grade absolute System III work
=========================================================================

Priority chain (first success wins for each field, with provenance):

  1) Explicit overrides (cm_iii, distance, sub-lat, NP PA) — WinJUPOS / user paste
  2) WinJUPOS / JUPOS CSV or JSON table at epoch
  3) **SPICE auto** (spice_auto: online kernel download + spiceypy) — preferred absolute path
  4) NASA JPL Horizons full observer parse (Δ, light-time, sub-obs lon/lat, NP.ang)
  5) Analytical fallback (differentials OK; absolute CM zero may be offset)

SPICE kernels are auto-searched/downloaded — users never need to hunt NAIF files.
Horizons is **geometry**, not a GRS longitude product. GRS lon still comes from
your image measurement; CM III ties that measurement to System III.
```

#### Classes

**ProEphemeris** (source line 87)

Observer-centric Jupiter geometry + provenance for publication.

Methods:

- `to_dict()` — line 110
- `to_vlbi_ephemeris_state()` — line 114
  Bridge to vlbi_metrology.EphemerisState without circular import at module load.

#### Functions

- **`wrap_deg(x)`** — line 48

- **`wrap_diff(a, b)`** — line 52

- **`_ssl_context()`** — line 56

- **`parse_time(s)`** — line 67

- **`analytical_geometry(t)`** — line 139

- **`fetch_horizons_full(t, site_lon, site_lat, site_elev_m, timeout, force)`** — line 165

JPL Horizons observer table for Jupiter (599).  QUANTITIES include range, light-time, sub-
observer lon/lat, NP angle. Note: sub-observer longitude from Horizons is the geometric CM
in the planet's longitude system (for 599, System III-related body frame).

- **`parse_horizons_observer_text(text)`** — line 245

Parse Horizons observer SOE block for QUANTITIES 1,13,14,20,31,32.  Preferred path: labeled
fields in the full response + CSV columns in fixed QUANTITIES order (RA, Dec, delta, deldot,
light-time, range, Ob-lon, Ob-lat, NP.ang, NP.dist). Heuristic float-picking is last resort
only.

- **`load_winjupos_table(path)`** — line 416

Load WinJUPOS-like CSV/JSON of CM or measurements.  Accepted columns (case-insensitive
aliases):   time/date/datetime/epoch, cm_iii/cml_iii/cml3/cmiii, optional sublat, np_pa

- **`interpolate_winjupos_cm(rows, t)`** — line 469

Linear circular interpolation of CM III (and friends) to epoch t.

- **`save_example_winjupos_template(path)`** — line 524

- **`try_spice_geometry(t, kernels_dir)`** — line 539

SPICE geometry via spice_auto:   - ensures spiceypy   - downloads de440s + LSK + PCK if
missing   - returns distance, light-time, CM III / sub-lat when body frame works

- **`_try_spice_geometry_legacy(t, kernels_dir)`** — line 580

Fallback if spice_auto unavailable: local kernels only.

- **`resolve_pro_ephemeris(user_time_iso, time_error_seconds, cm_override, distance_override, sub_lat_override, north_pa_override, winjupos_path, site_lat, site_lon, use_horizons, use_spice, force_horizons)`** — line 637

Build the best available ProEphemeris for absolute System III metrology.

- **`write_ephemeris_report(path, eph)`** — line 850

### 5.`nasa_compare.py` (262 lines)

#### Classes

**NASAComparison** (source line 33)

Methods:

- `to_dict()` — line 43
- `grade()` — line 46

#### Functions

- **`_ssl_context()`** — line 21

- **`grs_reference_model(t)`** — line 57

Literature-aligned schematic GRS state for sanity checks.  NOT an official NASA GRS
longitude product. Absolute lon uses a simple westward drift model anchored near modern
published rates (Simon et al. 2018 style: ~0.30–0.36 °/day west relative to System III, plus
a ~90-day oscillation).  Size uses Simon-like linear shrink rates from a 2015-ish anchor:
dL/dt ≈ −0.194 °/yr (EW), dW/dt ≈ −0.048 °/yr (NS).

- **`fetch_horizons(t, timeout)`** — line 89

Legacy wrapper — prefer ephemeris_pro.fetch_horizons_full for research geometry.

- **`compare_measurement_to_nasa(measured, user_time_iso, time_error_seconds)`** — line 167

- **`write_comparison_report(path, comp)`** — line 235

Write JSON + a clear human TXT: YOUR vs reference, deltas, numbers.

## 5.1 Ephemeris precedence

1. Explicit overrides of CM III, distance, sub-observer latitude, and north PA
2. Interpolation of a WinJUPOS/JUPOS central-meridian table at the observation epoch
3. SPICE geometry via spice_auto (kernel discovery and furnishing)
4. JPL Horizons observer quantities (range, light-time, sub-observer lon/lat, NP angle)
5. Analytic fallback suitable primarily for short-interval differentials

SPICE and Horizons constrain the planet's orientation and range. Feature longitude on the
cloud deck remains an image-domain inference conditioned on that geometry. Ancillary
schematic trend models used for contextual comparison are labeled as such in product
outputs.

# 6. Limb Navigation and Map Geometry

### 6.`precision_engine.py` (1065 lines)

Module documentation as implemented in source:

```
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
```

#### Classes

**NavState** (source line 37)

Methods:

- `b_pol_px()` — line 48

**GRSPrecisionResult** (source line 53)

Methods:

- `to_dict()` — line 68

#### Functions

- **`deg2rad(d)`** — line 72

- **`rad2deg(r)`** — line 76

- **`wrap_deg(x)`** — line 80

- **`wrap_diff(a, b)`** — line 84

- **`km_per_deg_lon(lat_deg)`** — line 88

- **`km_per_deg_lat()`** — line 92

- **`deg_to_arcsec_on_sky(deg, km_per_deg, distance_au)`** — line 96

Convert angular size on planet (deg of lon/lat) to sky arcsec.

- **`sky_error_arcsec(dlon_deg, dlat_deg, lat_deg, distance_au)`** — line 103

- **`_gauss(img, sigma)`** — line 109

- **`to_mono(image)`** — line 124

- **`rough_disk_mask(image)`** — line 137

- **`fit_limb_nav(image, n_rays, cm_iii_deg, distance_au, isophote_frac)`** — line 155

Sub-pixel limb navigation.  Ray isophote at ``isophote_frac`` × peak intensity + robust
median centre / radius (stable under limb darkening; avoids unstable algebraic circle fits).
**Human WinJUPOS analogy:** choosing a *larger* outline (fainter edge, smaller
``isophote_frac``) vs *smaller* outline (brighter edge, larger ``isophote_frac``) changes
disk radius and can shift absolute lon/lat by tenths of a degree. Use
``limb_outline_sensitivity`` / winjupos_twin to quantify that systematic.

- **`px_to_lonlat(y, x, nav)`** — line 262

Image pixel → System III lon + planetocentric lat.  Inverse of the same oriented
orthographic used by make_cylindrical: applies north_pa_deg (sky rotation) and sub_lat_deg
when present so moment / bary / map methods share one geometry contract with VLBI.

- **`make_cylindrical(image, nav, width, height)`** — line 296

Orthographic → cylindrical map lon∈[-90°,+90°] about CM, lat∈[-90°,+90°].  Uses sub_lat_deg
+ north_pa_deg from NavState when non-zero so gold/all_methods/ precision share the same
geometry family as VLBI oriented maps (one contract).

- **`_template_match_grs(cyl, nav, lat0, length_deg, width_deg)`** — line 343

Dark elliptical template match on cylindrical map (visible hemisphere).  Uses zero-mean
normalized cross-correlation (NCC), a narrow SEB/GRS latitude band, multi-scale sizes with a
prior around the nominal oval, and a local dark-centroid refine so we do not lock onto
random SEB waves.

- **`_moment_mask_grs(image, nav)`** — line 483

- **`_map_dark_centroid(cyl, nav, lat0)`** — line 577

Dark peak only inside SEB/GRS latitude band — never full-map (avoids lat~90 bugs).

- **`_method_is_sane(m, ref_lon)`** — line 629

- **`_choose_size(methods)`** — line 648

Never trust tiny moment blobs; prefer template size.

- **`_circular_weighted_mean(lons, weights)`** — line 670

- **`measure_grs_precision(image, cm_iii_deg, distance_au, nav, quiet, map_width, map_height)`** — line 679

Multi-method GRS measurement for best *result* accuracy.  Point estimate uses high-res
cylindrical map + weighted consensus (template preferred for longitude of a dark oval).

- **`monte_carlo_precision(image, nav, n_iter, seed, max_iter)`** — line 939

Fast MC for uncertainty of the *measurement process*.  Uses map-domain noise (template +
map_dark only) so it finishes quickly. Point estimate stays from full measure_grs_precision
(call separately).

- **`cap_mc_iterations(requested, megapixels)`** — line 1040

Soft RAM-aware caps so huge frames stay responsive, while still allowing research-grade
requests (up to 1000) on moderate images.

### 6.`limb_validation.py` (124 lines)

Module documentation as implemented in source:

Limb / multi-mode validation harness for GRS Observatory.  Generates synthetic GRS near the
limb (large lon_rel from CM), runs SOTA consensus with a pipeline seed, and checks we do not
lock onto CM with SOTA_EXCELLENT.  Usage:   cd app && python3 limb_validation.py --n 5

#### Functions

- **`_sky_ok(pkg)`** — line 25

- **`run_one(out_root, seed, limb_lon_rel)`** — line 36

- **`main(argv)`** — line 96

## 6.1 Isophotal limb size

Limb rays locate an intensity threshold expressed as a fraction of the local peak. Smaller
fractions track fainter outer isophotes and yield larger disk outlines; larger fractions
track brighter inner isophotes and yield tighter outlines. The twin sensitivity analysis
repeats navigation at outer, nominal, and inner fractions and re-estimates the GRS, thereby
quantifying the systematic contribution of outline size—the automated counterpart of
interactive outline judgment.

## 6.2 Cylindrical map contract

The visible hemisphere is mapped to longitudinal offset λ_rel ∈ [−90°, +90°] about the
central meridian (180° total width) and to latitude from +90° at the top of the map to −90°
at the bottom. Longitude scaling on the map is therefore 180°/W pixels, not 360°/W.
Orientation applies sub-observer latitude and north position angle consistently in the
forward map and in the inverse pixel-to-longitude transform, so that moment-based and map-
based estimators share a single geometric contract.

## 6.3 Navigation state

NavState stores disk center (xc, yc), equatorial radius in pixels, flattening, CM III,
distance in AU, and optional sub-latitude and north PA. When professional ephemeris
authorizes orientation application, process orchestration copies those angles onto NavState
before localization.

# 7. Localization Engines and Definition Control

### 7.`research_grade.py` (740 lines)

Module documentation as implemented in source:

```
Research-grade GRS metrology layer
==================================

What actually moves a result from "impressive demo" toward something a
research group would trust is not more wavelets or more MC iterations on the
same broken definition. It is:

  1) BIAS calibration via blind injection–recovery on the *same* image
  2) Multi-definition ensemble → systematic floor (definition scatter)
  3) Multi-filter residual "closure" (R/G/B consistency after DCR)
  4) Explicit error budget: random (MC) + systematic (definitions) + bias (injection)
  5) Publication bundle: methods, seeds, hashes, all intermediates

The distinctive idea (often underused in amateur pipelines):

  **Blind synthetic injection into the real residual field.**
  You know the truth of the *injected* oval. Recovering it measures *your*
  pipeline's bias under *tonight's* PSF, noise, limb, and code path.
  That bias is subtracted from the real GRS measurement, and the recovery
  scatter becomes a calibrated random error — not a guess.

This is closer to how careful experimental physics treats instruments than
to "stack harder and claim σ from photon noise."

Honest scope:
  - Target: 1–2″ sky *with calibrated bias*, transparent systematics.
  - No institution will "endorse" software; they will check whether your
    error bars cover truth in injection tests and multi-definition scatter.
```

#### Classes

**DefinitionResult** (source line 71)

Methods:

- `to_dict()` — line 80

**InjectionTrial** (source line 85)

Methods:

- `to_dict()` — line 94

**ResearchGradeResult** (source line 99)

Publication-oriented product.

Methods:

- `to_dict()` — line 128

#### Functions

- **`_hash_array(a)`** — line 132

- **`inject_dark_oval(image, nav, lon_iii, lat_deg, length_deg, width_deg, depth)`** — line 137

Inject a smooth dark oval at (lon, lat) into a copy of the image. Used for blind recovery
calibration on the real residual field.

- **`run_definition_suite(image, nav)`** — line 182

Several operational definitions of "where is the GRS". Scatter among them ≈ systematic floor
(definition uncertainty).

- **`consensus_from_definitions(defs)`** — line 237

Returns lon, lat, L, W, sys_lon_deg, sys_lat_deg.  Point estimate = single best definition
(engine_weighted > template > weight). Systematic floor = scatter of *other* definitions
about that primary. Do NOT average incompatible definitions into the reported position.

- **`_recover_near_lonlat(cyl, nav, lon_hint, lat_hint, lon_half_win, lat_half_win)`** — line 287

Recover a dark feature *only* inside a lon/lat window around a hint. Critical when the real
GRS is also on the disk — global match would lock on it.

- **`blind_injection_calibration(image, nav, n_trials, seed, around_lon, around_lat)`** — line 353

Blind injection–recovery with *local* recovery windows.  Injections are placed *away* from
the known GRS so the real oval does not steal the match. Recovery searches only near the
injected truth.

- **`filter_closure_rgb(channels, nav)`** — line 424

Multi-filter residual consistency (optical 'closure'-like diagnostic).  Measure GRS
independently in R, G, B. After removing a simple linear dispersion model in 1/λ², residual
scatter bounds unmodeled systematics.

- **`run_research_grade(image, nav, cm_iii_deg, distance_au, channels, injection_trials, mc_iter, seed, max_fidelity, factory_mode, user_time_iso, time_error_seconds, aperture_m, use_vlbi, …)`** — line 486

SPIRE-M research-grade reduction for one epoch.  When max_fidelity/use_vlbi (default): VLBI-
inspired optical stack (oriented geometry, multi-scale NCC, phase-ref probes, hierarchical
MC, formal error budget). factory_mode: heavier probe + H-MC suite.

- **`write_publication_bundle(path, result, extra)`** — line 712

### 7.`vlbi_metrology.py` (1747 lines)

Module documentation as implemented in source:

```
optical metrology advanced metrology for ground-based GRS photography
================================================================

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
```

#### Classes

**EphemerisState** (source line 87)

Observer-centric Jupiter geometry for one epoch.

Methods:

- `to_dict()` — line 104

**AdvancedNav** (source line 109)

Navigation with full orientation (VLBI-style geometric model).

Methods:

- `b_pol_px()` — line 126
- `to_nav_state()` — line 129
- `to_dict()` — line 141

**ErrorBudget** (source line 148)

Formal VLBI-style error budget in degrees and arcsec.

Methods:

- `to_dict()` — line 167

**VLBIResult** (source line 172)

Publication product — optical metrology ground optical metrology.

Methods:

- `to_dict()` — line 204

#### Functions

- **`planetocentric_to_planetographic(lat_c_deg, flattening)`** — line 212

Jupiter planetographic latitude from planetocentric.

- **`time_error_to_lon_sigma(time_error_seconds)`** — line 221

System III longitude uncertainty from absolute timing error.

- **`_hash_array(a)`** — line 226

- **`_rotate_points(xs, ys, xc, yc, pa_deg)`** — line 232

Rotate image coords so that Jovian north aligns with -y after rotation by -PA.

- **`build_ephemeris_approx(t_utc_iso, time_error_seconds, cm_override, distance_override)`** — line 246

Analytical geometry (differentials OK; absolute CM has ~degree-level zero).

- **`enrich_ephemeris_from_horizons(eph)`** — line 323

Pull distance / diameter from Horizons when online; parse best-effort.

- **`fit_limb_advanced(image, eph, n_rays, bootstrap, seed, apply_sub_lat)`** — line 369

Precision limb: trust the stable multi-iteration radial-gradient centre (`fit_limb_nav`),
then bootstrap ray subsets *around that centre* only to estimate σ(xc, yc, a). Re-fitting
the centre from a single ray pass is unstable on banded planets (SEB/NEB gradients pull the
ellipse).  Sub-observer latitude is applied only when `apply_sub_lat=True` or when |sub_lat|
is known from a real ephemeris override (not the crude seasonal model).

- **`make_cylindrical_oriented(image, nav, width, height)`** — line 505

Orthographic sample with sub-observer latitude tilt. lon ∈ [-90, 90] about CM, lat
planetocentric.

- **`px_to_lonlat_oriented(y, x, nav)`** — line 563

Inverse of oriented orthographic (planetocentric).

- **`_ncc_peak(band, tmpl)`** — line 594

Return subpixel (py, px, peak) via FFT NCC.

- **`multiscale_template_match(cyl, nav, lat0, lengths, widths)`** — line 654

Multi-scale dark-oval correlator: grid of (L,W) templates, pick max NCC, subpixel peak.
Primary scientific definition for optical GRS centre.  Accepts AdvancedNav or
precision_engine.NavState (all_methods uses NavState).

- **`measure_size_isophote(cyl, lon_iii, lat0, nav, level_frac)`** — line 749

Isophote ellipse size around GRS on cylindrical map (degrees). More physical than intensity-
moment eigenvalues of a tiny blob.

- **`measure_grs_vlbi(image, nav, map_width, map_height, quiet)`** — line 811

VLBI-style correlator primary + multi-method closure for systematics. Point estimate is
multiscale NCC (locked); others enter scatter only if sane.

- **`_local_dark_recover(cyl, nav, lon_hint, lat_hint, lon_half_win, lat_half_win)`** — line 943

Local dark centroid on cylindrical map (probe recovery fallback).

- **`inject_dark_oval_image(image, nav, lon_iii, lat_deg, length_deg, width_deg, depth)`** — line 994

- **`phase_reference_injection(image, nav, grs_lon, grs_lat, n_trials, seed)`** — line 1039

VLBI phase-reference analog:   inject known probes AWAY from real GRS, recover with
multiscale science   correlator (same code path), estimate bias + scatter.

- **`hierarchical_monte_carlo(image, nav, eph, n_iter, time_error_seconds, seed)`** — line 1137

VLBI-style hierarchical error simulation:   limb jitter → map noise → template scale →
CM/time prior.

- **`definition_suite_vlbi(image, nav)`** — line 1254

- **`definition_scatter(defs, primary_lon, primary_lat)`** — line 1297

- **`filter_closure_vlbi(channels, nav)`** — line 1318

- **`assemble_formal_budget(lat_deg, distance_au, rand_lon, rand_lat, def_lon, def_lat, nav, eph, time_error_seconds, bias_unc_lon, bias_unc_lat, closure_sky)`** — line 1362

- **`optical_diffraction_floor_arcsec(diameter_m, wavelength_nm)`** — line 1435

- **`grade_result(sig_tot, inj_mean, optical_floor)`** — line 1440

- **`run_vlbi_grade(image, user_time_iso, time_error_seconds, cm_iii_deg, distance_au, channels, injection_trials, mc_iter, seed, aperture_m, use_horizons, factory_mode, nav, winjupos_path, …)`** — line 1460

Full optical metrology optical metrology reduction for one epoch.  Absolute System III uses
professional ephemeris chain when available: override → WinJUPOS → SPICE → Horizons full →
analytical.

- **`write_vlbi_bundle(path, result, extra)`** — line 1675

- **`research_grade_compat(result)`** — line 1713

Shape compatible with existing UI headline / research_grade fields.

### 7.`gold_standard.py` (990 lines)

Module documentation as implemented in source:

```
Professional GRS metrology procedures (ground-based, laptop-replicable).

Goal: replicate *how professionals work* so results approach published / agency-

Pro stack (what this module implements):
  1) Geometry discipline  — CM III / sub-lat / PA source tagged (WinJUPOS, SPICE, Horizons)
  2) Map-based measure    — cylindrical deprojection then measure (WinJUPOS-like desk)
  3) Fixed definitions    — named gold standards (barycentre, oval fit, W/E edges)
  4) Definition scatter   — systematic floor from incompatible definitions
  5) Optional human check — paste your WinJUPOS manual lon/lat → Δ (validation, not truth)
  6) Export               — notebook / PVOL-style text for archives

WinJUPOS is geometry + measuring desk, NOT an automatic GRS detector.
Horizons/SPICE are Jupiter geometry, NOT official GRS longitude products.
```

#### Classes

**GoldMeasure** (source line 106)

Methods:

- `to_dict()` — line 116

**GoldStandardResult** (source line 121)

Full professional procedure product — methodology first, not a magic answer.

Methods:

- `to_dict()` — line 149

#### Functions

- **`_wrap_lon(lon)`** — line 159

- **`_cyl_axes(cyl, nav)`** — line 163

Return lon_iii grid (W) and planetographic-ish lat grid (H) for cylindrical map.  MUST match
precision_engine.make_cylindrical: lon_rel ∈ [-90°, +90°] about CM (visible hemisphere
only). Using ±180 was a systematic 2× lon-scale bug on GS-OVAL / GS-EDGE / GS-MID / extent
products.

- **`_grs_band_mask_cyl(cyl, lat, lat0, half)`** — line 178

- **`measure_gs_bary(image, nav)`** — line 183

- **`measure_gs_map(cyl, nav)`** — line 196

- **`measure_gs_tmpl(cyl, nav)`** — line 209

- **`measure_gs_engine(image, nav)`** — line 222

- **`_dark_mask_cyl(cyl, lat, lat0)`** — line 241

Binary dark mask in GRS band on cylindrical map.  Uses band-local darkness (not a loose
global percentile) so SEB waves and flat residuals do not inflate EW size to tens of
degrees.

- **`measure_gs_oval_and_edges(cyl, nav)`** — line 330

Ellipse-ish center + west/east edges at mid-latitude. WinJUPOS-like extent measure (edges
are not 'the GRS position' alone).

- **`compare_to_winjupos_manual(primary_lon, primary_lat, wj_lon, wj_lat, distance_au)`** — line 439

Validation against *your* careful WinJUPOS manual measure.

- **`_pick_primary(measures)`** — line 491

- **`_scatter(primary, measures)`** — line 499

- **`run_gold_standard(image, nav)`** — line 523

Run the professional procedure suite on one image.  Returns named definitions, primary GS
measure, edge extent, optional WinJUPOS manual Δ. Does NOT claim NASA GRS truth.

- **`format_gold_report(gs)`** — line 736

Human text: professional procedure, not 'the NASA answer'.

- **`write_gold_standard_bundle(out_dir, gs)`** — line 837

Write gold_standard.json/txt + winjupos-compatible measure export.

- **`attach_gold_to_package(package, image)`** — line 882

Run gold standard + every method, attach to package, optionally write files.

### 7.`winjupos_twin.py` (577 lines)

Module documentation as implemented in source:

```
WinJUPOS twin mode + limb / definition sensitivity
==================================================

WinJUPOS is accurate because pros lock:
  1) mid-exposure UTC
  2) CM III (from WinJUPOS / SPICE / paste)
  3) a *fixed* measurement definition (core vs W/E edge vs mid)
  4) a consistent limb outline size (human can draw larger or smaller)

Yes — choosing a larger vs smaller limb outline **does** change absolute lon/lat.
Same for GRS: dark core ≠ west edge ≠ east edge ≠ mid of edges.

This module:
  • Forces WinJUPOS-style reporting: GS-MAP / GS-BARY as twin primaries
  • Quantifies limb outline sensitivity (outer / nominal / inner isophotes)
  • Quantifies GRS definition scatter (core vs edges vs mid)
  • Optional Δ vs your manual WinJUPOS pick
```

#### Classes

**LimbProbe** (source line 56)

Methods:

- `to_dict()` — line 66

**TwinResult** (source line 71)

Methods:

- `to_dict()` — line 106

#### Functions

- **`_measure_map_and_bary(image, nav)`** — line 110

- **`limb_outline_sensitivity(image)`** — line 129

Re-nav with outer / nominal / inner limb isophotes and re-measure GRS.  This answers: "If I
draw the WinJUPOS outline larger or smaller, how much does GRS lon/lat move?"

- **`grs_definition_sensitivity(image, nav)`** — line 226

Core vs W/E edges vs mid — different human picks, different System III lon.  Same reason
WinJUPOS users must write *which* point they measured.

- **`run_winjupos_twin(image)`** — line 346

WinJUPOS twin reduction: fixed CM + fixed definitions + sensitivity budgets.

- **`format_twin_report(tr)`** — line 477

- **`attach_winjupos_twin_to_package(package, image)`** — line 525

### 7.`publish_primary.py` (267 lines)

Module documentation as implemented in source:

Publication policy — what number you should report
==================================================  Rule (pro / WinJUPOS-aligned):   •
PUBLISH: GS-MAP twin (else GS-BARY) as the official lon/lat   • SOUP / SOTA: scatter and
confidence only — not the published answer   • EQUAL to WinJUPOS only when:         same CM
source discipline + Δ vs your manual WJ pick is small  This module rewrites
package["headline"] so dashboards/CLI/UI show the published number first.

#### Functions

- **`_f(x)`** — line 29

- **`assess_winjupos_equality()`** — line 37

When can we say 'same as WinJUPOS'?  Requires a manual WJ pick. CM source should be winjupos
/ override / spice for a strong equality claim (not pure analytical).

- **`apply_publish_policy(package)`** — line 106

After gold + twin + sota are attached, set the official published answer.  Mutates package
in place; returns the publish block.

- **`format_publish_section(package)`** — line 239

### 7.`sota_accuracy.py` (1192 lines)

Module documentation as implemented in source:

```
State-of-the-art accuracy layer for ground-based GRS metrology (laptop).

Does NOT invent NASA truth. Implements best-practice *procedure* used worldwide:

  1) Run every estimator (all_methods)
  2) Reliability priors by method family (empirical pro practice)
  3) MAD / IQR outlier rejection (reject methods that left the GRS)
  4) Robust circular consensus for lon + robust lat
  5) Quality gates (lat band, scatter, inlier count, limb-ish flags)
  6) Optional WinJUPOS manual Δ as external validation
  7) FITS DATE-OBS / mid-time extraction for absolute System III

References (practice, not code):
  JUPOS/WinJUPOS multi-measure discipline; multi-method scatter as systematic;
  robust statistics (MAD) standard in metrology; Asay-Davis-style correlation
  methods downweighted when inconsistent with core cluster.
```

#### Classes

**SOTAResult** (source line 105)

Methods:

- `to_dict()` — line 128

#### Functions

- **`_mad(x)`** — line 132

- **`_circular_median(lons)`** — line 140

- **`_circular_weighted_mean(lons, wts)`** — line 151

- **`is_centre_method(method_id, family)`** — line 164

- **`base_weight(method_id, family, declared_weight)`** — line 173

- **`is_map_edge_lock(lon, cm_iii_deg, margin_deg)`** — line 180

Cylindrical maps only cover CM±90°. *Exact* hits on the map boundary columns (lon_rel ≈
±90°) are classic false peaks when a method slides to the edge.  Margin must stay tight
(~2–3°). A wide margin (e.g. 10°) falsely kills real GRS detections when the spot is
legitimately near the limb (lon_rel ~80–88°).

- **`_near_pipeline(lon, pipeline_lon, tol_deg)`** — line 194

- **`_cluster_centres(centres)`** — line 200

Greedy circular clustering by lon (lat used only for seed quality later).

- **`_score_cluster(cl)`** — line 230

Higher = better cluster to trust as GRS centre.

- **`robust_consensus(methods)`** — line 285

Multi-cluster + MAD consensus on centre methods.  Critical fixes vs naive median:   • drop
*exact* cylindrical map-edge locks (CM±90° columns only)   • never drop methods near
VLBI/pipeline seed (near-limb GRS is valid)   • pick best cluster with strong pipeline prior
(not densest CM mode)   • inject PIPELINE_SEED if all near-limb methods were filtered

- **`_grade_from_score(score)`** — line 568

Map score→grade with hard vetoes against false EXCELLENT.

- **`assess_quality()`** — line 601

Returns quality_grade, score 0-100, flags, notes, recommendations.  Design goals (post AS_P5
audit):   • Never false EXCELLENT on limb / multi-mode / pipeline-bad nights   • Do not
stack penalties into meaningless 0 when SOTA agrees with pipeline   • Reward pipeline
agreement (independent VLBI seed)

- **`extract_fits_time(path)`** — line 818

Best-effort mid-exposure time from FITS header.

- **`run_sota_accuracy(methods)`** — line 889

Build SOTA robust primary from full method list.

- **`apply_sota_to_package(package)`** — line 1034

Read methods from package gold_standard / all_methods and write SOTA primary. Overwrites
headline gold_* with SOTA robust values when ok.

- **`format_sota_section(sota)`** — line 1167

### 7.`all_methods.py` (995 lines)

Module documentation as implemented in source:

```
ALL practical GRS localization methods for a laptop optical pipeline.

Philosophy: run every independent estimator that can help. Report each with a
name, lon/lat, optional size, and weight. Consensus / scatter = systematic
knowledge. No single method is "NASA truth".

Groups:
  A) Map (cylindrical deprojection) — WinJUPOS-desk family
  B) Image-plane — limb-nav coordinates
  C) Template / correlation
  D) Threshold / morphology
  E) Edge / isophote / extent
  F) Spectral (R, R−G) when RGB available
  G) Ensemble (robust combinations of the above)

Soft-fail individually: one bad method never kills the suite.
```

#### Classes

**MethodHit** (source line 44)

Methods:

- `to_dict()` — line 56

#### Functions

- **`_cyl_lon_lat_grids(cyl, nav)`** — line 64

- **`_mono_cyl(cyl)`** — line 72

- **`_gauss(im, sigma)`** — line 79

- **`_band_slice(lat, lat0, half)`** — line 87

- **`_hit_from_map_xy(method_id, family, cx, cy, lon_iii, lat)`** — line 97

- **`_fail(method_id, family, err)`** — line 127

- **`m_map_dark(cyl, nav, lon_iii, lat)`** — line 135

- **`m_template(cyl, nav, lon_iii, lat, length, width, tag)`** — line 144

- **`m_bary_image(im, nav)`** — line 153

- **`m_engine(im, nav)`** — line 162

- **`m_multiscale_ncc(cyl, nav, lon_iii, lat)`** — line 171

- **`m_perc_dark_bary(cyl, nav, lon_iii, lat, perc, tag)`** — line 198

- **`m_otsu_bary(cyl, nav, lon_iii, lat)`** — line 217

- **`m_hp_peak(cyl, nav, lon_iii, lat)`** — line 247

- **`m_bandpass_bary(cyl, nav, lon_iii, lat)`** — line 271

- **`m_log_blob(cyl, nav, lon_iii, lat)`** — line 290

- **`m_proj_1d(cyl, nav, lon_iii, lat)`** — line 309

Longitude of minimum mean intensity in GRS lat band (1D scan).

- **`m_lat_track(cyl, nav, lon_iii, lat)`** — line 334

For each lat row find darkest lon; pick row nearest −22°.

- **`m_phase_corr(cyl, nav, lon_iii, lat)`** — line 357

Phase correlation of band vs dark elliptical template.

- **`m_isophote_center(cyl, nav, lon_iii, lat)`** — line 386

Centroid of isophote at low percentile (dark contour).

- **`m_quad_moment(cyl, nav, lon_iii, lat)`** — line 416

Second-moment (inertia) center of dark mask.

- **`m_morph_bary(cyl, nav, lon_iii, lat)`** — line 441

- **`m_adaptive_bary(cyl, nav, lon_iii, lat)`** — line 466

- **`m_seed_grow(cyl, nav, lon_iii, lat)`** — line 482

Flood-fill grow from darkest pixel in band with intensity gate.

- **`m_sobel_ring(cyl, nav, lon_iii, lat)`** — line 520

Center of mass of gradient magnitude (edge ring of oval).

- **`m_flux_powers(cyl, nav, lon_iii, lat)`** — line 543

Inverse-intensity moments with power 1,2,4 (core-weighted).

- **`m_rgb_methods(channels, nav, cyl_builder)`** — line 565

Red-only and R−G chromatic methods when RGB available.

- **`m_edges_extent(cyl, nav, lon_iii, lat)`** — line 618

West/east edges + midpoint + oval (WinJUPOS-like extent).

- **`m_symmetry(cyl, nav, lon_iii, lat)`** — line 672

Autocorrelation peak of inverted band (symmetry center).

- **`m_min_pixel(cyl, nav, lon_iii, lat)`** — line 695

- **`_circular_mean_lon(lons)`** — line 709

- **`ensemble_from_hits(hits)`** — line 714

Robust combinations of successful center methods (exclude pure edges).

- **`run_all_methods(image, nav)`** — line 861

Run every method. Returns dict with hits list, n_ok, scatter, primary suggestion.

### 7.`all_methods_extra.py` (905 lines)

Module documentation as implemented in source:

```
Extra GRS localization methods from planetary imaging literature + classical CV.

Sources informing these estimators (methodology, not code copy):
  · JUPOS / WinJUPOS practice — centre pick, W/E edges, map measure (Jacquesson 2008)
  · Asay-Davis et al. ACCIV/CIV — correlation window matching for cloud features
  · Simon / Hubble GRS size & drift series — multi-epoch isophote/size consistency
  · IRAF ellipse / isophote fitting tradition — multi-level elliptical isophotes
  · Lab planetary teaching — lon extent = east edge − west edge
  · Classical CV — moments, mean-shift, distance transform, RANSAC ellipse,
    ZNCC/SAD/SSD templates, FWHM profiles, geometric median, PCA axes,
    watershed, top-hat, structure tensor, radial symmetry, SPOMF phase match

Each method may only move the answer by a fraction of a degree — still reported.
```

#### Functions

- **`_band_roi(cyl, lat, half)`** — line 59

- **`_dark_mask(band, valid, perc)`** — line 67

- **`_subpixel_argmin(z)`** — line 75

Parabolic subpixel refine around discrete minimum.

- **`_subpixel_argmax(z)`** — line 87

- **`m_fwhm_lon(cyl, nav, lon_iii, lat)`** — line 93

Centre of FWHM of 1D longitude intensity cut at lat≈−22°.

- **`m_fwhm_lat(cyl, nav, lon_iii, lat)`** — line 117

Centre of FWHM of 1D latitude cut through darkest lon.

- **`m_profile_gaussian_fit(cyl, nav, lon_iii, lat)`** — line 141

Gaussian fit to inverted 1D lon profile (subpixel μ).

- **`m_multi_isophote(cyl, nav, lon_iii, lat)`** — line 164

Centres at several isophote levels; mean = MULTI_ISO.

- **`m_box_extent(cyl, nav, lon_iii, lat)`** — line 194

W/E/N/S edges of dark mask; box centre; length & width in deg.

- **`m_geometric_median(cyl, nav, lon_iii, lat)`** — line 228

Weiszfeld geometric median of dark pixels (map xy).

- **`m_pca_ellipse(cyl, nav, lon_iii, lat)`** — line 250

PCA of dark mask → centre + axis lengths.

- **`m_convex_hull_c(cyl, nav, lon_iii, lat)`** — line 278

- **`m_distance_transform_peak(cyl, nav, lon_iii, lat)`** — line 309

Medial-axis style: peak of distance transform inside dark mask.

- **`m_mean_shift(cyl, nav, lon_iii, lat)`** — line 333

Mean-shift mode of dark pixel density.

- **`m_ransac_ellipse(cyl, nav, lon_iii, lat)`** — line 356

RANSAC fit of algebraic ellipse to dark contour points.

- **`m_civ_window_ncc(cyl, nav, lon_iii, lat)`** — line 393

Small-window ZNCC of a dark-ellipse kernel over the SEB band (single-frame cousin of CIV
correlation windows).

- **`m_sad_ssd_templates(cyl, nav, lon_iii, lat)`** — line 435

SAD and SSD matched filters (complement NCC).

- **`m_spomf(cyl, nav, lon_iii, lat)`** — line 467

Symmetric phase-only matched filter (optics / pattern recognition).

- **`m_bottom_hat(cyl, nav, lon_iii, lat)`** — line 486

Morphological bottom-hat (dark feature enhance) then bary.

- **`m_tophat_inv(cyl, nav, lon_iii, lat)`** — line 509

White top-hat on inverted image (dark as bright).

- **`m_watershed(cyl, nav, lon_iii, lat)`** — line 534

Watershed catchment of inverted intensity (bowl of GRS).

- **`m_structure_tensor(cyl, nav, lon_iii, lat)`** — line 577

Peak of structure-tensor energy (textured oval boundary interior).

- **`m_radial_symmetry(cyl, nav, lon_iii, lat)`** — line 606

Loy–Zelinsky style radial symmetry transform (simplified).

- **`m_hu_moments(cyl, nav, lon_iii, lat)`** — line 644

Centre from raw spatial moments of dark mask (m10/m00, m01/m00).

- **`m_percentile_ladder(cyl, nav, lon_iii, lat)`** — line 671

- **`m_bilateral_bary(cyl, nav, lon_iii, lat)`** — line 705

Approx bilateral smooth (range+space) then dark bary — edge-preserving.

- **`m_unsharp_peak(cyl, nav, lon_iii, lat)`** — line 730

- **`m_rolling_ball(cyl, nav, lon_iii, lat)`** — line 739

Rolling-ball style background (large open) subtract → dark residual bary.

- **`m_kde_mode(cyl, nav, lon_iii, lat)`** — line 761

Grid KDE mode of dark pixel positions.

- **`m_gmm2(cyl, nav, lon_iii, lat)`** — line 779

2-component 1D GMM on lon of dark pixels; take darker/ lower-intensity component mean lat.

- **`m_ring_template(cyl, nav, lon_iii, lat)`** — line 810

Annular/ring template NCC (edge of oval rather than filled).

- **`m_min_enclosing_circle(cyl, nav, lon_iii, lat)`** — line 829

Approx min enclosing circle centre of dark mask (Welzl-lite via farthest).

- **`run_extra_methods(cyl, nav, lon_iii, lat)`** — line 861

Run all literature/extra methods; soft-fail each.

## 7.1 Named gold-standard definitions

- GS-BARY — intensity-weighted dark barycentre in the GRS latitude band
- GS-MAP — cylindrical-map dark centroid (publication center)
- GS-TMPL — multiscale dark-oval template correlation
- GS-OVAL — elliptical characterization with length and width
- GS-EDGE-W / GS-EDGE-E — longitudinal extrema of the dark mask (extent)
- GS-MID — wrap-aware midpoint of the edge longitudes
- GS-ENGINE — internal multi-method engine consensus

## 7.2 Multi-estimator catalog

The combined method modules assemble classical estimators: map centroids, percentile
thresholds, templates and normalized cross-correlation, isophotal ladders, morphological
operators, robust location statistics, edge and extent measures, spectral cues on RGB data,
and explicit ensembles. A typical execution yields on the order of eighty method records.
Shared dependence on dark residuals in the South Equatorial Belt implies strong correlation;
ensemble dispersion is therefore interpreted as definition and algorithm scatter rather than
as a set of independent Gaussian errors. The SOTA layer applies circular clustering, median
absolute deviation rejection, map-boundary filters, and optional pipeline-seed preference to
mitigate known multimodal failures such as central-meridian dark-band lock.

## 7.3 Optical metrology stack

The research-grade default path routes to an optical metrology stack that emphasizes
oriented geometry, multiscale correlation, injection-style bias probes, hierarchical Monte
Carlo sampling, and a multi-component error budget. The design borrows organizational ideas
from precision measurement practice—explicit systematics, repeated probes, archival error
decomposition—applied to extended cloud morphology on a single-telescope image.

# 8. Synthetic Planets, Stress Analysis, and Multi-Epoch Series

### 8.`synthetic_hq.py` (682 lines)

Module documentation as implemented in source:

```
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
```

#### Classes

**SynthSpec** (source line 56)

Synthetic Jupiter frame.  Observation epoch is random unless random_time=False and
user_time_iso is set.  mode:   - visual: high wave contrast (presentation / UI stills)   -
metrology: quieter SEB, GRS uniquely dark, for certification / accuracy demos

#### Functions

- **`_seed(user_time, region, err)`** — line 88

- **`_parse_time(s)`** — line 93

- **`random_observation_time(rng)`** — line 103

- **`_blur(rgb, sigma)`** — line 112

- **`_resize_bilinear(small, h, w)`** — line 127

Smooth upsample (avoids blocky tiles that killed wave look at 8K).

- **`_value_noise(h, w, rng, octaves, base)`** — line 153

Multi-octave smooth noise in [-1,1].

- **`_belt_profile(lat_n)`** — line 176

High-contrast canonical belt/zone stack (lat in radians, planetocentric-ish). Zones → high
albedo, belts → low.

- **`_wavefield(lon_rel, lat_n, rng, contrast)`** — line 204

Longitudinal waves + chevrons + festoons — high enough amplitude to *see*.

- **`_shear_residual(turb, lat_n, lon_rel, strength)`** — line 245

Approximate zonal shear by phase-modulating residual with latitude.

- **`_paint_ovals(rgb, disk, lon_abs, lat_deg, ld, rng, n, grs_lon, grs_lat)`** — line 257

White ovals / barges — avoid placing competing dark barges on the GRS.

- **`_paint_grs(rgb, disk, lon_abs, lat_deg, ld, grs_lon, grs_lat, grs_L, grs_W, rng)`** — line 296

Dark red oval + internal spiral / filament structure.  Returns (oval_mask, truth_lon,
truth_lat) where truth lon/lat are the *intensity-weighted dark barycentre* of the painted
GRS — the same definition the metrology engine measures (not the geometric ellipse seed).

- **`generate(spec, out_dir)`** — line 372

### 8.`hard_synth_suite.py` (384 lines)

Module documentation as implemented in source:

```
==============================================================================

Friendly synthetics (same projection as the measurer, mild seeing, GRS on CM)
**understate** real error. This suite injects controlled mismatches and asks:

  1) Does truth fall inside the reported 1σ / 2σ error bars?  (coverage)
  2) What is residual sky error under each stress family?
  3) Which stress dominates the floor?

Stress families:
  A) CM error        — wrong System III zero (±0.5–2°)
  B) Extra seeing    — Gaussian blur beyond synth default
  C) Near-limb GRS   — force GRS far from CM (via re-measure with CM shift)
  D) Noise / SNR     — additive Gaussian noise
  E) Pole / sub-lat  — wrong orientation in nav
  F) Combined night  — A+B+D mild together

Output: JSON + text calibration report with coverage rates.
```

#### Classes

**StressCase** (source line 46)

Methods:

- `to_dict()` — line 51

**StressResult** (source line 56)

Methods:

- `to_dict()` — line 72

#### Functions

- **`_blur(im, sigma)`** — line 76

- **`apply_image_stress(image, seeing_sigma_px, noise_sigma, seed)`** — line 86

- **`run_one_measure(image, truth, cm_err_deg, sub_lat_err_deg, north_pa_err_deg, injection_trials, mc_iter, seed)`** — line 102

Returns meas_lon, meas_lat, sigma_sky, grade.

- **`default_stress_matrix()`** — line 144

- **`run_hard_synth_suite(out_dir, base_seed, resolution, cases, injection_trials, mc_iter, user_time_iso)`** — line 170

Generate one HQ synthetic, then run all stress cases. Returns full report dict and writes
files under out_dir.

### 8.`batch_prove.py` (401 lines)

Module documentation as implemented in source:

```
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
```

#### Functions

- **`_percentile(xs, p)`** — line 45

- **`run_one(out_dir)`** — line 59

- **`main(argv)`** — line 207

### 8.`multi_epoch.py` (494 lines)

Module documentation as implemented in source:

```
Multi-epoch differential GRS tracking (VLBI phase-reference across nights)
=========================================================================

Absolute System III on a single night is limited by CM ephemeris zero-point.
**Differentials** across epochs cancel common-mode errors the way VLBI phase
referencing cancels station delays:

  Δlon(t) = lon(t) − lon(t0)   (circular)
  drift model: lon(t) = lon0 + ω·(t−t0) + seasonal terms (optional)
  RTS / Kalman smoother for trajectory under measurement noise

Use cases:
  - GRS drift rate (°/day) for JUPOS-style science
  - Night-to-night residual after derotation (internal consistency)
  - Publication table of bias-corrected epochs with formal σ

API: load epoch JSON list → differential series → drift fit → smooth → report
```

#### Classes

**EpochMeasure** (source line 42)

One calibrated GRS measurement.

Methods:

- `to_dict()` — line 59

**DifferentialSeries** (source line 64)

Phase-referenced differentials relative to reference epoch.

Methods:

- `to_dict()` — line 79

#### Functions

- **`_t_seconds(iso)`** — line 83

- **`epoch_from_research_json(path, epoch_id)`** — line 93

Ingest research_grade.json / job_result.json / vlbi_metrology.json.

- **`load_epochs_from_dir(directory)`** — line 189

Scan job_*/synth_*/ for research_grade.json or vlbi_metrology.json.

- **`load_epochs_from_list(items)`** — line 212

- **`weighted_linear_fit(t, y, w)`** — line 235

y = a + b t Returns a, b, sigma_a, sigma_b (approx).

- **`kalman_rts_1d(t, z, r, q_scale)`** — line 263

Random-walk + rate state Kalman smoother (RTS). State [x, v]; measurement x. t in days, z
measured lon unwrapped.

- **`build_differential_series(epochs, ref_index, smooth)`** — line 309

Phase-reference all epochs to ref: differentials cancel common CM bias. Fit linear drift;
optional RTS smoother on unwrapped lon.

- **`measure_epoch_image(image, user_time_iso, time_error_seconds, cm_override, winjupos_path)`** — line 420

Run full VLBI-grade measure for one image and package as EpochMeasure.

- **`write_multi_epoch_report(path, series, epochs)`** — line 465

## 8.1 Role of controlled imagery

Synthetic scenes supply ground truth for regression testing, certification gates, and stress
families (blur, noise, central-meridian mismatch, and related degradations). Environment
controls can force large longitudinal offsets from the central meridian to exercise near-
limb behavior. Coverage of injected truth by reported uncertainty, and distributions of
recovery error, are empirical quantities to be tabulated from executed runs rather than
assumed a priori.

# 9. Learning-Based Assistance

### 9.`nn_grs.py` (1694 lines)

Module documentation as implemented in source:

```
SPIRE-Net — multi-layer CNN for GRS localization (soft prior).

Architecture (NumPy, always available; optional PyTorch if installed):
  Input: 1×H×W cylindrical intensity map (default 64×128)
  Conv blocks → global features → heatmap + (lon_rel, lat) regression head

Auto-train:
  Generates synthetic Jupiter/GRS truth via synthetic_hq, projects to maps,
  trains with MSE on heatmap + coordinate loss. Weights saved under models/.

Important:
  Final metrology still uses physics/template/SPIRE-M. The network is a
  *soft prior* (ROI hint), not a replacement for injection calibration.
```

#### Classes

**SpireNet** (source line 476)

Complicated multi-stage CNN:   conv1 1→16 k3 → relu → pool   conv2 16→32 k3 → relu → pool
conv3 32→64 k3 → relu → pool   flatten → FC 256 → relu → FC 128 → relu   heads: heatmap
(H'×W') via FC, coords (2,) via FC

Methods:

- `create(seed)` — line 506
- `_pad_same(x, k)` — line 526
- `forward(x, cache)` — line 530
  x: (1,H,W) or (H,W) normalized ~0..1 returns heatmap (8,16), coords (2,) in [0,1] for
  (x_frac, y_frac)
- `predict_lonlat(cyl_map, cm_iii_deg)` — line 565
  Map network output to planetocentric lon/lat (map is lon_rel -90..90, lat 90..-90).
- `save(path, quiet)` — line 587
  Atomic durable save — refuses NaN/Inf; never overwrites good weights with corrupt ones.
- `load(path)` — line 651

#### Functions

- **`_app_dir()`** — line 40

- **`_resolve_model_paths()`** — line 44

Return (model_dir, weights_path, meta_path); never raises.

- **`_train_cache_dir()`** — line 57

Writable folder for temporary training synthetics.

- **`_train_log_dir()`** — line 68

Durable train logs — survive app restart / most quits.

- **`_atomic_write_text(path, text)`** — line 79

Write via temp file + replace so a crash mid-write doesn't wipe the file.

- **`_atomic_savez(path)`** — line 99

Atomic np.savez_compressed — refuse to write arrays that contain NaN/Inf.

- **`weights_are_finite(net)`** — line 132

- **`snapshot_weights(net)`** — line 145

- **`restore_weights(net, snap)`** — line 149

- **`good_weights_path()`** — line 154

- **`save_good_backup(net)`** — line 162

Write known-good backup only if finite.

- **`restore_from_good_backup(net)`** — line 177

- **`start_prevent_sleep(reason)`** — line 201

macOS: spawn `caffeinate -dims` so training continues with lid closed (as long as the
machine is not fully powered off / battery dead). Other OS: best-effort no-op True.

- **`stop_prevent_sleep()`** — line 243

- **`_write_live_report(meta)`** — line 264

Always-on-disk report (updated every epoch / emergency flush).

- **`_emergency_flush(reason)`** — line 315

Save last network + report on SIGTERM/SIGINT/atexit. Cannot run on SIGKILL.

- **`_install_save_handlers()`** — line 336

Register once: flush weights on normal exit / Ctrl+C / kill (TERM).

- **`_relu(x)`** — line 373

- **`_relu_bwd(x, g)`** — line 377

- **`_sigmoid(x)`** — line 381

- **`conv2d(x, w, b)`** — line 385

x: (C_in, H, W), w: (C_out, C_in, kH, kW), b: (C_out,) valid padding → out smaller.

- **`conv2d_fast(x, w, b)`** — line 405

Faster conv using scipy if available, else conv2d.

- **`maxpool2(x)`** — line 430

2×2 max pool. Returns out, argmax linear index in each window for bwd.

- **`maxpool2_bwd(gout, idx, shape_in)`** — line 446

- **`conv2d_bwd(x, w, gout)`** — line 459

Return gx, gw, gb.

- **`_resize_map(img, nh, nw)`** — line 687

- **`map_to_nn_input(cyl)`** — line 709

- **`truth_to_targets(lon_iii, lat, cm_iii)`** — line 716

- **`get_train_status()`** — line 742

Status for UI — always safe; refreshes portable model paths.

- **`_sgd_step(net, x, heat_t, coord_t, lr)`** — line 776

One-sample SGD with NaN/Inf protection. If forward/backward would corrupt weights, skip the
update and return NaN.

- **`rng_noise(shape, scale)`** — line 857

- **`auto_train(epochs, samples_per_epoch, lr, seed, use_existing, prevent_sleep)`** — line 861

Auto-train SPIRE-Net on synthetic labeled maps. NaN-safe, optional prevent-sleep for lid-
close durability.

- **`_checkpoint_path()`** — line 1148

- **`_save_checkpoint(payload)`** — line 1152

- **`_load_checkpoint()`** — line 1160

- **`_inject_weight_noise(net, scale, rng)`** — line 1170

- **`_reinit_heads(net, rng)`** — line 1178

Plateau escape: re-init heatmap/coord heads, keep feature extractors.

- **`_make_train_sample(rng, out_tmp, strategy)`** — line 1190

One labeled map sample. Returns (x, heat_t, coord_t) or None.

- **`overnight_train(hours, max_epochs, samples_per_epoch, base_lr, seed, use_existing, resume, plateau_patience, plateau_min_delta, sample_cache_size, stop_flag, prevent_sleep)`** — line 1239

Long / durable training for SPIRE-Net.  - prevent_sleep: keep Mac awake (caffeinate) so lid-
close doesn't stop train - Checkpoints every epoch; resume after restart / wake - NaN/Inf
protection + GOOD weight backup - Only full power-off or Stop request ends training
intentionally

- **`durable_background_train(hours, samples_per_epoch, fine_tune)`** — line 1631

Entry for detached / lid-close training. Always prevent_sleep + resume checkpoint + NaN
guards.

- **`request_train_stop()`** — line 1675

Ask overnight/auto train loops to stop (cooperative).

- **`predict_soft_prior(image, nav, cm_iii_deg)`** — line 1680

Load net if trained and predict GRS lon/lat soft prior.

### 9.`ai_hard_cases.py` (399 lines)

Module documentation as implemented in source:

AI assist only where classical Python methods struggle.  Easy nights (tight multi-method
cluster, sharp GRS): physics / SOTA wins — AI stays out. Hard nights (high scatter, few
inliers, soft contrast, ambiguous SEB): SPIRE-Net helps disambiguate and re-weight methods
near the learned GRS appearance.  This is the right place for ML: not absolute System III,
not CM, not time — feature disambiguation under mess.

#### Classes

**HardCaseAIResult** (source line 25)

Methods:

- `to_dict()` — line 39

#### Functions

- **`estimate_image_difficulty(image, nav)`** — line 43

0 = easy sharp oval, 1 = soft/noisy/low-contrast mess.

- **`estimate_method_difficulty(methods)`** — line 95

How much classical methods disagree / struggle.

- **`compute_difficulty(image, methods)`** — line 173

- **`_nn_prior(image, nav, cm_iii_deg)`** — line 197

- **`assist_hard_case(image)`** — line 206

If difficulty is high, blend toward SPIRE-Net and/or pull lon/lat toward methods that agree
with the network.  Easy case → engaged=False, lon/lat unchanged.

- **`apply_hard_case_ai_to_package(package, image)`** — line 326

After SOTA is computed, optionally refine lon/lat with AI if hard. Updates headline + sota
block when engaged.

## 9.1 Distributed weights

SPIRE-Net parameters are distributed as NumPy archives under app/models
(spire_net_weights.npz, spire_net_meta.json, with a GOOD snapshot for recovery).
paths.ensure_models_present installs bundled weights into the active model directory on
first use. Packaging scripts include the model tree in application bundles. The network
supplies an optional soft prior; classical geometry and publication definitions remain
available when weights are absent.

# 10. Imaging Monolith

### 10.`grs_complete_system.py` (10350 lines)

Module documentation as implemented in source:

GRS Complete Ground Pipeline System =================================== Human-maximum
ground-based Jupiter / Great Red Spot imaging and science pipeline. Implements lucky
imaging, calibration, alignment, stacking, derotation, PSF/wavelets/RL restoration, LRGB,
limb navigation, GRS measurement, bootstrap errors, Kalman-RTS trajectory, validation, CLI.
Version: 1.0.0

#### Classes

**PhysicalConstants** (source line 91)

**PipelineMode** (source line 121)

**FilterName** (source line 127)

**QualityMetric** (source line 132)

**StackMethod** (source line 142)

**RestoreMethod** (source line 147)

**AlignMode** (source line 152)

**SegmentMethod** (source line 156)

**SmootherKind** (source line 164)

**LimbMethod** (source line 168)

**DefinitionId** (source line 174)

**GRSPipelineError** (source line 183)

**IngestError** (source line 184)

**QCError** (source line 185)

**CalibrationError** (source line 186)

**AlignmentError** (source line 187)

**NavigationError** (source line 188)

**MeasurementError** (source line 189)

**ConfigError** (source line 190)

**DependencyError** (source line 191)

**StageTimer** (source line 203)

Methods:

- `__init__(name)` — line 204
- `__enter__()` — line 208
- `__exit__()` — line 212

**FrameMeta** (source line 457)

Methods:

- `to_dict()` — line 474

**VideoCube** (source line 482)

Methods:

- `n_frames()` — line 489
- `shape_hw()` — line 493

**QCReport** (source line 498)

Methods:

- `fail(reason)` — line 503

**StackResult** (source line 509)

**Navigation** (source line 522)

Methods:

- `b_pol_px()` — line 538
- `to_dict()` — line 541

**GRSState** (source line 556)

Methods:

- `to_dict()` — line 573

**GeomEphemeris** (source line 581)

**RunManifest** (source line 594)

Methods:

- `to_dict()` — line 605

**PipelineConfig** (source line 610)

Full pipeline configuration with sane professional defaults.

Methods:

- `from_dict(d)` — line 668
- `from_yaml_like(path)` — line 676
  Minimal YAML-like key: value loader (no PyYAML required).
- `to_dict()` — line 710
- `sha()` — line 713

**GRSCompletePipeline** (source line 2824)

End-to-end human-maximum ground-based GRS pipeline.  Workflows:   A) SER/FITS lucky stack
per filter   B) Derotation + channel registration   C) Restoration + LRGB (imaging)   D)
Navigation + GRS measurement (science)   E) Trajectory smoothing across epochs

Methods:

- `__init__(config)` — line 2836
- `_record(name)` — line 2851
- `process_cube(cube, filter_name)` — line 2856
- `process_path(path, filter_name)` — line 2877
- `derotate_all(t_ref)` — line 2898
- `build_channels()` — line 2909
- `run_imaging()` — line 2918
- `run_science()` — line 2956
- `run(inputs)` — line 2996

**FilterBandpass** (source line 3162)

**FixedLagLuckyStacker** (source line 3407)

Methods:

- `__init__(lag, fraction, metric)` — line 3408
- `push(frame)` — line 3411
- `stack_now()` — line 3416

**PipelineStateMachine** (source line 3536)

Methods:

- `__init__(cfg)` — line 3537
- `transition(new_state)` — line 3539
- `run_on_cube(cube)` — line 3541

**MultiFilterNight** (source line 3553)

Methods:

- `__init__(name, cfg)` — line 3554
- `add(filter_name, cube)` — line 3556
- `reduce_all()` — line 3558

**IngestStageResult** (source line 3860)

Methods:

- `to_dict()` — line 3866

**CalibStageResult** (source line 3870)

Methods:

- `to_dict()` — line 3876

**QualityStageResult** (source line 3880)

Methods:

- `to_dict()` — line 3886

**AlignStageResult** (source line 3890)

Methods:

- `to_dict()` — line 3896

**StackStageResult** (source line 3900)

Methods:

- `to_dict()` — line 3906

**DerotStageResult** (source line 3910)

Methods:

- `to_dict()` — line 3916

**RestoreStageResult** (source line 3920)

Methods:

- `to_dict()` — line 3926

**ColorStageResult** (source line 3930)

Methods:

- `to_dict()` — line 3936

**NavStageResult** (source line 3940)

Methods:

- `to_dict()` — line 3946

**MeasureStageResult** (source line 3950)

Methods:

- `to_dict()` — line 3956

**TrajStageResult** (source line 3960)

Methods:

- `to_dict()` — line 3966

**ExportStageResult** (source line 3970)

Methods:

- `to_dict()` — line 3976

#### Functions

- **`setup_logging(level, log_file)`** — line 194

- **`sha256_bytes(data)`** — line 217

- **`sha256_file(path, chunk)`** — line 220

- **`sha256_array(arr)`** — line 227

- **`sha256_json(obj)`** — line 230

- **`ensure_dir(path)`** — line 233

- **`clamp(x, lo, hi)`** — line 236

- **`safe_div(a, b, eps)`** — line 239

- **`wrap_deg(lon)`** — line 242

- **`wrap_deg_diff(a, b)`** — line 245

- **`deg2rad(d)`** — line 248

- **`rad2deg(r)`** — line 251

- **`jupiter_eq_km_per_deg(lat_deg)`** — line 254

- **`jupiter_km_per_deg_lat()`** — line 258

- **`km_at_jupiter_from_mas(mas, distance_au)`** — line 261

- **`_gaussian_kernel1d(sigma, truncate)`** — line 269

- **`gaussian_filter2d(image, sigma)`** — line 276

- **`map_coords(image, coords, order, mode, cval)`** — line 295

Sample image at coords[0]=row, coords[1]=col.

- **`fft_convolve2d(a, b, mode)`** — line 325

- **`morph_open_close(mask, open_i, close_i)`** — line 341

- **`label_components(mask)`** — line 358

- **`largest_component(mask)`** — line 383

- **`percentile_clip(image, lo, hi)`** — line 392

- **`normalize_percentile(image, lo, hi)`** — line 399

- **`sobel_mag(image)`** — line 404

- **`laplacian(image)`** — line 413

- **`highpass(image, sigma)`** — line 418

- **`shift_image(image, dy, dx, cval)`** — line 423

- **`rotate_image(image, angle_deg, center)`** — line 430

- **`resize_bilinear(image, new_h, new_w)`** — line 445

- **`tai_utc_offset(t)`** — line 740

- **`utc_to_tt_mjd(t)`** — line 748

UTC datetime -> TT MJD (approx). TT = TAI + 32.184s; TAI = UTC + leap.

- **`tt_to_tdb_mjd(tt_mjd)`** — line 764

Approximate TT->TDB (Fairhead & Bretagnon-like simplified).

- **`parse_time_string(s)`** — line 774

- **`jupiter_system_iii_lon_approx(tdb_mjd)`** — line 784

Approximate System III (1965) central meridian for an Earth observer using a simple linear
rotation model. For professional absolute work use SPICE/Astropy; this is adequate for
derotation differentials.

- **`jupiter_distance_au_approx(tdb_mjd)`** — line 797

Very rough Earth-Jupiter distance oscillation ~5.2 ± 0.6 AU.

- **`jupiter_apparent_diameter_arcsec(distance_au)`** — line 804

- **`compute_geometry(t_utc, site_lat, site_lon, site_elev_m)`** — line 812

- **`refractive_index_dry(pressure_mbar, temp_c, wavelength_um)`** — line 841

Edlen-like simplified refractive index excess (n-1).

- **`achromatic_refraction_arcsec(z_deg, pressure_mbar, temp_c, wavelength_um)`** — line 850

Approximate refraction R ≈ 60" tan(z) scaled by conditions.

- **`dcr_shift_arcsec(z_deg, lam1_nm, lam2_nm, pressure_mbar, temp_c)`** — line 860

Differential chromatic refraction between two wavelengths (arcsec along parallactic).

- **`read_fits(path)`** — line 879

- **`_parse_fits_data(data, bitpix, naxis_vals, bscale, bzero)`** — line 937

- **`write_fits(path, data, header)`** — line 961

- **`read_ser(path)`** — line 1011

Read SER video (mono or convert first channel).

- **`write_png(path, image)`** — line 1076

- **`ingest_path(path, filter_name, site_lat, site_lon, site_elev_m)`** — line 1105

- **`read_rgb_fits_channels(path)`** — line 1147

Read RGB FITS (3,H,W) or (H,W,3) into channel dict.

- **`estimate_readnoise_gain(bias_frames)`** — line 1165

Estimate read noise (ADU) and rough gain from bias pairs.

- **`make_hot_pixel_mask(dark_or_flat, sigma)`** — line 1174

- **`replace_hot_pixels(image, mask)`** — line 1180

- **`apply_calibration(frame, dark, flat, hotmask)`** — line 1193

- **`calibrate_cube(cube, dark, flat)`** — line 1211

- **`rough_disk_mask(image, thr_frac)`** — line 1224

- **`validate_cube(cube, cfg)`** — line 1232

- **`disk_mask_for_quality(image)`** — line 1272

- **`score_laplacian_var(image)`** — line 1284

- **`score_fft_power(image)`** — line 1296

- **`score_sobel_energy(image)`** — line 1318

- **`score_tenengrad(image)`** — line 1327

- **`score_variance(image)`** — line 1332

- **`score_max_pixel(image)`** — line 1339

- **`score_frame(image, metric)`** — line 1346

- **`score_frames(cube, metric)`** — line 1368

- **`select_top_indices(scores, fraction)`** — line 1378

- **`phase_correlate(ref, image)`** — line 1390

Return (dy, dx, peak_response).

- **`place_alignment_points(mask, grid, margin_frac)`** — line 1424

- **`local_cross_corr_shift(ref_patch, img_patch)`** — line 1446

- **`extract_patch(image, y, x, box)`** — line 1452

- **`align_frames_global(frames, ref_index, max_shift)`** — line 1466

- **`align_frames_local_ap(frames, ref_index, ap_grid, ap_box, max_shift)`** — line 1479

- **`align_stack(frames, scores, cfg)`** — line 1540

- **`stack_mean(frames)`** — line 1559

- **`stack_median(frames)`** — line 1563

- **`stack_kappa_sigma(frames, kappa, iters)`** — line 1567

- **`stack_quality_weighted(frames, scores)`** — line 1579

- **`stack_winsorized(frames, frac)`** — line 1591

- **`estimate_noise_map(frames, max_frames)`** — line 1600

- **`stack_frames(frames, scores, cfg)`** — line 1612

- **`lucky_stack_cube(cube, cfg, fraction)`** — line 1625

- **`noll_to_zernike(j)`** — line 1650

Convert Noll index j (1-based) to (n, m).

- **`zernike_radial(n, m, rho)`** — line 1680

- **`zernike(n, m, rho, theta)`** — line 1690

- **`zernike_basis_on_pupil(size, noll_max)`** — line 1699

- **`complex_pupil_psf(size, zernike_coeffs, wavelength_scale)`** — line 1726

Generate PSF from complex pupil with optional Zernike aberrations.

- **`kolmogorov_phase_screen(size, r0_frac, seed)`** — line 1753

Fourier-method Kolmogorov phase screen (approx).

- **`moffat_psf(size, alpha, beta)`** — line 1771

- **`gaussian_psf(size, sigma)`** — line 1780

- **`estimate_psf_from_limb(image, nav_xc, nav_yc, a_eq, n_angles, psf_size)`** — line 1787

Estimate approximate 1D LSF from limb and build circular PSF.

- **`b3_spline_kernel()`** — line 1818

- **`a_trous_convolve(image, level)`** — line 1824

Separable à trous convolution with B3 spline at given level (holes).

- **`starlet_decompose(image, n_layers)`** — line 1845

- **`soft_threshold(x, thr)`** — line 1856

- **`mad_sigma(x)`** — line 1860

- **`starlet_sharpen(image, n_layers, gains, denoise_sigmas)`** — line 1866

- **`richardson_lucy(image, psf, n_iter, eps)`** — line 1886

- **`wiener_deconv(image, psf, K)`** — line 1905

- **`limb_overshoot_metric(image, mask)`** — line 1922

Rough ringing metric near limb.

- **`restore_image(image, cfg, psf)`** — line 1936

- **`rgb_to_ycbcr(rgb)`** — line 1960

- **`ycbcr_to_rgb(y, cb, cr)`** — line 1968

- **`build_lrgb(L, R, G, B, sat_scale, denoise_chroma)`** — line 1976

- **`register_channels(channels, ref_name)`** — line 1997

- **`apply_residual_dcr(channels, z_deg, pressure, temp_c)`** — line 2011

Shift channels vertically by model DCR relative to G (simplified).

- **`project_to_cylindrical(image, nav, width, height)`** — line 2030

Orthographic-like inverse: sample disk into lon-lat map.

- **`backproject_cylindrical(cyl, nav, out_shape)`** — line 2056

- **`rough_navigation(image, geom)`** — line 2077

- **`derotate_image(image, nav, cm_from, cm_to, map_width)`** — line 2099

- **`derotate_stack_result(stack, cfg, t_ref)`** — line 2116

- **`extract_limb_points(image, n_rays, method)`** — line 2135

Return Nx2 array of (y, x) limb points.

- **`fit_ellipse_algebraic(ys, xs)`** — line 2175

Fit ellipse ax^2 + bxy + cy^2 + dx + ey + f = 0. Returns xc, yc, a, b, theta (radians)
approximate.

- **`fit_oblate_disk(points, flattening, fixed_flat)`** — line 2202

- **`bootstrap_limb_nav(image, n, n_rays, seed)`** — line 2218

- **`fit_navigation(image, meta, cfg)`** — line 2242

- **`px_to_lonlat(y, x, nav)`** — line 2266

Return (lon_iii_deg, lat_deg) planetocentric-ish.

- **`lonlat_to_px(lon_iii, lat, nav)`** — line 2283

- **`otsu_threshold(image, mask)`** — line 2297

- **`grs_latitude_band_mask(shape, nav, lat0, dlat)`** — line 2311

- **`segment_grs_adaptive(image, nav)`** — line 2325

- **`segment_grs_otsu(image, nav)`** — line 2367

- **`segment_grs(image, nav, method, manual_mask)`** — line 2376

- **`fit_ellipse_to_mask(mask)`** — line 2392

- **`measure_grs_from_mask(mask, image, nav, definition_id, filter_name)`** — line 2410

- **`bootstrap_grs(image, nav, cfg, n)`** — line 2442

- **`unwrap_longitudes(lons)`** — line 2508

- **`kalman_rts_1d(t, z, r, q_pos, q_vel)`** — line 2512

Constant-velocity Kalman filter + RTS smoother. State [pos, vel]; measurements of pos. t in
days, z measurements, r measurement variances.

- **`smooth_trajectory(states, cfg)`** — line 2551

- **`fit_drift_model(t, lon, weights)`** — line 2576

lon = lon0 + drift * t  (t days from mean).

- **`export_stack(path, stack)`** — line 2597

- **`export_state_json(path, state)`** — line 2602

- **`export_trajectory_csv(path, rows)`** — line 2607

- **`export_manifest(path, manifest)`** — line 2620

- **`package_versions()`** — line 2625

- **`synthetic_jupiter(size, cm, grs_lon, grs_lat, grs_a_deg, grs_b_deg, noise, seed)`** — line 2640

- **`synthetic_ser_cube(n_frames, size, seeing_px, seed)`** — line 2688

- **`validate_phase_correlate(tol)`** — line 2717

- **`validate_stack_snr()`** — line 2728

- **`validate_nav_synthetic()`** — line 2740

- **`validate_grs_measure()`** — line 2748

- **`run_validation_suite()`** — line 2766

- **`guess_filter_from_name(name)`** — line 2792

- **`discover_inputs(raw_dir)`** — line 2801

- **`run_pipeline(config, inputs)`** — line 3026

- **`build_arg_parser()`** — line 3036

- **`main(argv)`** — line 3078

- **`filter_center_nm(name)`** — line 3184

- **`rad_to_arcsec(r)`** — line 3190

- **`diffraction_limit_arcsec(diameter_m, wavelength_nm)`** — line 3194

- **`critical_sampling_arcsec_per_px(diameter_m, wavelength_nm, factor)`** — line 3200

- **`plate_scale_arcsec_per_px(pixel_um, focal_length_mm)`** — line 3204

- **`effective_focal_length_mm(pixel_um, arcsec_per_px)`** — line 3208

- **`suggest_roi(planet_diameter_arcsec, scale, margin)`** — line 3212

- **`parse_firecapture_log(path)`** — line 3227

- **`apply_log_to_meta(meta, log)`** — line 3246

- **`drizzle_combine(frames, shifts, scale, pixfrac)`** — line 3269

- **`quality_pyramid(image, levels)`** — line 3299

- **`hybrid_quality_vector(image)`** — line 3310

- **`rank_frames_multi_metric(cube)`** — line 3320

- **`make_lon_lat_grid(nav, shape)`** — line 3329

- **`reproject_to_simple_cylindrical(image, nav, out_w, out_h, lon0)`** — line 3343

- **`map_measure_grs(cyl_map, lat0, dlat)`** — line 3362

- **`assemble_error_budget(state, nav, stack)`** — line 3376

- **`write_text_report(path, pipe)`** — line 3390

- **`get_preset(name)`** — line 3446

- **`print_capabilities()`** — line 3465

- **`estimate_plate_background_gradient(image, order)`** — line 3469

- **`restore_digitized_plate(image)`** — line 3488

- **`airmass_approx(alt_deg)`** — line 3496

- **`score_session(alt_deg, seeing_arcsec, transparency)`** — line 3501

- **`unsharp_mask(image, sigma, strength)`** — line 3507

- **`image_moments(mask, image)`** — line 3512

- **`run_selftests()`** — line 3525

- **`describe_noll(j)`** — line 3604

- **`recommendation_for_aperture_mm(ap_mm)`** — line 3652

- **`process_r_stack(cube, cfg)`** — line 3657

- **`process_g_stack(cube, cfg)`** — line 3661

- **`process_b_stack(cube, cfg)`** — line 3665

- **`process_ir685_stack(cube, cfg)`** — line 3669

- **`process_ir742_stack(cube, cfg)`** — line 3673

- **`process_ir807_stack(cube, cfg)`** — line 3677

- **`process_ch4_stack(cube, cfg)`** — line 3681

- **`process_clear_stack(cube, cfg)`** — line 3685

- **`dcr_b_minus_g_arcsec(z_deg, pressure_mbar, temp_c)`** — line 3689

- **`dcr_b_minus_r_arcsec(z_deg, pressure_mbar, temp_c)`** — line 3692

- **`dcr_b_minus_ir685_arcsec(z_deg, pressure_mbar, temp_c)`** — line 3695

- **`dcr_b_minus_ir742_arcsec(z_deg, pressure_mbar, temp_c)`** — line 3698

- **`dcr_b_minus_ch4_arcsec(z_deg, pressure_mbar, temp_c)`** — line 3701

- **`dcr_g_minus_r_arcsec(z_deg, pressure_mbar, temp_c)`** — line 3704

- **`dcr_g_minus_ir685_arcsec(z_deg, pressure_mbar, temp_c)`** — line 3707

- **`dcr_g_minus_ir742_arcsec(z_deg, pressure_mbar, temp_c)`** — line 3710

- **`dcr_ir685_minus_r_arcsec(z_deg, pressure_mbar, temp_c)`** — line 3713

- **`dcr_ir685_minus_ir742_arcsec(z_deg, pressure_mbar, temp_c)`** — line 3716

- **`dcr_ir742_minus_r_arcsec(z_deg, pressure_mbar, temp_c)`** — line 3719

- **`dcr_ch4_minus_g_arcsec(z_deg, pressure_mbar, temp_c)`** — line 3722

- **`dcr_ch4_minus_r_arcsec(z_deg, pressure_mbar, temp_c)`** — line 3725

- **`dcr_ch4_minus_ir685_arcsec(z_deg, pressure_mbar, temp_c)`** — line 3728

- **`dcr_ch4_minus_ir742_arcsec(z_deg, pressure_mbar, temp_c)`** — line 3731

- **`apply_wavelet_preset(image, name)`** — line 3742

- **`deg_to_mas(d)`** — line 3746

- **`mas_to_deg(m)`** — line 3749

- **`arcsec_to_mas(a)`** — line 3752

- **`mas_to_arcsec(m)`** — line 3755

- **`deg_to_arcsec(d)`** — line 3758

- **`arcsec_to_deg(a)`** — line 3761

- **`day_to_second(d)`** — line 3764

- **`second_to_day(s)`** — line 3767

- **`au_to_km(a)`** — line 3770

- **`km_to_au(k)`** — line 3773

- **`grs_reference_size(year)`** — line 3853

- **`apply_site_preset(cfg, name)`** — line 3990

- **`box_smooth_1(image)`** — line 3994

- **`box_smooth_2(image)`** — line 3999

- **`box_smooth_3(image)`** — line 4004

- **`box_smooth_4(image)`** — line 4009

- **`box_smooth_5(image)`** — line 4014

- **`box_smooth_6(image)`** — line 4019

- **`box_smooth_7(image)`** — line 4024

- **`box_smooth_8(image)`** — line 4029

- **`box_smooth_9(image)`** — line 4034

- **`box_smooth_10(image)`** — line 4039

- **`box_smooth_11(image)`** — line 4044

- **`box_smooth_12(image)`** — line 4049

- **`box_smooth_13(image)`** — line 4054

- **`box_smooth_14(image)`** — line 4059

- **`box_smooth_15(image)`** — line 4064

- **`box_smooth_16(image)`** — line 4069

- **`box_smooth_17(image)`** — line 4074

- **`box_smooth_18(image)`** — line 4079

- **`box_smooth_19(image)`** — line 4084

- **`box_smooth_20(image)`** — line 4089

- **`box_smooth_21(image)`** — line 4094

- **`box_smooth_22(image)`** — line 4099

- **`box_smooth_23(image)`** — line 4104

- **`box_smooth_24(image)`** — line 4109

- **`box_smooth_25(image)`** — line 4114

- **`box_smooth_26(image)`** — line 4119

- **`box_smooth_27(image)`** — line 4124

- **`box_smooth_28(image)`** — line 4129

- **`box_smooth_29(image)`** — line 4134

- **`box_smooth_30(image)`** — line 4139

- **`box_smooth_31(image)`** — line 4144

- **`box_smooth_32(image)`** — line 4149

- **`box_smooth_33(image)`** — line 4154

- **`box_smooth_34(image)`** — line 4159

- **`box_smooth_35(image)`** — line 4164

- **`box_smooth_36(image)`** — line 4169

- **`box_smooth_37(image)`** — line 4174

- **`box_smooth_38(image)`** — line 4179

- **`box_smooth_39(image)`** — line 4184

- **`box_smooth_40(image)`** — line 4189

- **`box_smooth_41(image)`** — line 4194

- **`box_smooth_42(image)`** — line 4199

- **`box_smooth_43(image)`** — line 4204

- **`box_smooth_44(image)`** — line 4209

- **`box_smooth_45(image)`** — line 4214

- **`box_smooth_46(image)`** — line 4219

- **`box_smooth_47(image)`** — line 4224

- **`box_smooth_48(image)`** — line 4229

- **`box_smooth_49(image)`** — line 4234

- **`box_smooth_50(image)`** — line 4239

- **`box_smooth_51(image)`** — line 4244

- **`box_smooth_52(image)`** — line 4249

- **`box_smooth_53(image)`** — line 4254

- **`box_smooth_54(image)`** — line 4259

- **`box_smooth_55(image)`** — line 4264

- **`box_smooth_56(image)`** — line 4269

- **`box_smooth_57(image)`** — line 4274

- **`box_smooth_58(image)`** — line 4279

- **`box_smooth_59(image)`** — line 4284

- **`box_smooth_60(image)`** — line 4289

- **`box_smooth_61(image)`** — line 4294

- **`box_smooth_62(image)`** — line 4299

- **`box_smooth_63(image)`** — line 4304

- **`box_smooth_64(image)`** — line 4309

- **`box_smooth_65(image)`** — line 4314

- **`box_smooth_66(image)`** — line 4319

- **`box_smooth_67(image)`** — line 4324

- **`box_smooth_68(image)`** — line 4329

- **`box_smooth_69(image)`** — line 4334

- **`box_smooth_70(image)`** — line 4339

- **`box_smooth_71(image)`** — line 4344

- **`box_smooth_72(image)`** — line 4349

- **`box_smooth_73(image)`** — line 4354

- **`box_smooth_74(image)`** — line 4359

- **`box_smooth_75(image)`** — line 4364

- **`box_smooth_76(image)`** — line 4369

- **`box_smooth_77(image)`** — line 4374

- **`box_smooth_78(image)`** — line 4379

- **`box_smooth_79(image)`** — line 4384

- **`box_smooth_80(image)`** — line 4389

- **`box_smooth_81(image)`** — line 4394

- **`box_smooth_82(image)`** — line 4399

- **`box_smooth_83(image)`** — line 4404

- **`box_smooth_84(image)`** — line 4409

- **`box_smooth_85(image)`** — line 4414

- **`box_smooth_86(image)`** — line 4419

- **`box_smooth_87(image)`** — line 4424

- **`box_smooth_88(image)`** — line 4429

- **`box_smooth_89(image)`** — line 4434

- **`box_smooth_90(image)`** — line 4439

- **`box_smooth_91(image)`** — line 4444

- **`box_smooth_92(image)`** — line 4449

- **`box_smooth_93(image)`** — line 4454

- **`box_smooth_94(image)`** — line 4459

- **`box_smooth_95(image)`** — line 4464

- **`box_smooth_96(image)`** — line 4469

- **`box_smooth_97(image)`** — line 4474

- **`box_smooth_98(image)`** — line 4479

- **`box_smooth_99(image)`** — line 4484

- **`box_smooth_100(image)`** — line 4489

- **`box_smooth_101(image)`** — line 4494

- **`box_smooth_102(image)`** — line 4499

- **`box_smooth_103(image)`** — line 4504

- **`box_smooth_104(image)`** — line 4509

- **`box_smooth_105(image)`** — line 4514

- **`box_smooth_106(image)`** — line 4519

- **`box_smooth_107(image)`** — line 4524

- **`box_smooth_108(image)`** — line 4529

- **`box_smooth_109(image)`** — line 4534

- **`box_smooth_110(image)`** — line 4539

- **`box_smooth_111(image)`** — line 4544

- **`box_smooth_112(image)`** — line 4549

- **`box_smooth_113(image)`** — line 4554

- **`box_smooth_114(image)`** — line 4559

- **`box_smooth_115(image)`** — line 4564

- **`box_smooth_116(image)`** — line 4569

- **`box_smooth_117(image)`** — line 4574

- **`box_smooth_118(image)`** — line 4579

- **`box_smooth_119(image)`** — line 4584

- **`box_smooth_120(image)`** — line 4589

- **`score_all_frames_laplacian_var(cube)`** — line 4594

- **`score_all_frames_fft_power(cube)`** — line 4597

- **`score_all_frames_hybrid(cube)`** — line 4600

- **`score_all_frames_sobel_energy(cube)`** — line 4603

- **`score_all_frames_tenengrad(cube)`** — line 4606

- **`score_all_frames_variance(cube)`** — line 4609

- **`score_all_frames_max_pixel(cube)`** — line 4612

- **`algorithm_help(name)`** — line 4638

- **`step_ingest_description()`** — line 4643

- **`step_qc_description()`** — line 4646

- **`step_calibrate_description()`** — line 4649

- **`step_score_description()`** — line 4652

- **`step_select_description()`** — line 4655

- **`step_align_description()`** — line 4658

- **`step_stack_description()`** — line 4661

- **`step_derotate_description()`** — line 4664

- **`step_register_description()`** — line 4667

- **`step_restore_description()`** — line 4670

- **`step_lrgb_description()`** — line 4673

- **`step_navigate_description()`** — line 4676

- **`step_segment_description()`** — line 4679

- **`step_measure_description()`** — line 4682

- **`step_bootstrap_description()`** — line 4685

- **`step_error_budget_description()`** — line 4688

- **`step_smooth_description()`** — line 4691

- **`step_export_description()`** — line 4694

- **`step_manifest_description()`** — line 4697

- **`step_report_description()`** — line 4700

- **`gaussian_blur_s1(image)`** — line 4703

- **`gaussian_blur_s2(image)`** — line 4706

- **`gaussian_blur_s3(image)`** — line 4709

- **`gaussian_blur_s4(image)`** — line 4712

- **`gaussian_blur_s5(image)`** — line 4715

- **`gaussian_blur_s6(image)`** — line 4718

- **`gaussian_blur_s7(image)`** — line 4721

- **`gaussian_blur_s8(image)`** — line 4724

- **`gaussian_blur_s9(image)`** — line 4727

- **`gaussian_blur_s10(image)`** — line 4730

- **`gaussian_blur_s11(image)`** — line 4733

- **`gaussian_blur_s12(image)`** — line 4736

- **`gaussian_blur_s13(image)`** — line 4739

- **`gaussian_blur_s14(image)`** — line 4742

- **`gaussian_blur_s15(image)`** — line 4745

- **`gaussian_blur_s16(image)`** — line 4748

- **`gaussian_blur_s17(image)`** — line 4751

- **`gaussian_blur_s18(image)`** — line 4754

- **`gaussian_blur_s19(image)`** — line 4757

- **`gaussian_blur_s20(image)`** — line 4760

- **`gaussian_blur_s21(image)`** — line 4763

- **`gaussian_blur_s22(image)`** — line 4766

- **`gaussian_blur_s23(image)`** — line 4769

- **`gaussian_blur_s24(image)`** — line 4772

- **`gaussian_blur_s25(image)`** — line 4775

- **`gaussian_blur_s26(image)`** — line 4778

- **`gaussian_blur_s27(image)`** — line 4781

- **`gaussian_blur_s28(image)`** — line 4784

- **`gaussian_blur_s29(image)`** — line 4787

- **`gaussian_blur_s30(image)`** — line 4790

- **`gaussian_blur_s31(image)`** — line 4793

- **`gaussian_blur_s32(image)`** — line 4796

- **`gaussian_blur_s33(image)`** — line 4799

- **`gaussian_blur_s34(image)`** — line 4802

- **`gaussian_blur_s35(image)`** — line 4805

- **`gaussian_blur_s36(image)`** — line 4808

- **`gaussian_blur_s37(image)`** — line 4811

- **`gaussian_blur_s38(image)`** — line 4814

- **`gaussian_blur_s39(image)`** — line 4817

- **`gaussian_blur_s40(image)`** — line 4820

- **`gaussian_blur_s41(image)`** — line 4823

- **`gaussian_blur_s42(image)`** — line 4826

- **`gaussian_blur_s43(image)`** — line 4829

- **`gaussian_blur_s44(image)`** — line 4832

- **`gaussian_blur_s45(image)`** — line 4835

- **`gaussian_blur_s46(image)`** — line 4838

- **`gaussian_blur_s47(image)`** — line 4841

- **`gaussian_blur_s48(image)`** — line 4844

- **`gaussian_blur_s49(image)`** — line 4847

- **`gaussian_blur_s50(image)`** — line 4850

- **`gaussian_blur_s51(image)`** — line 4853

- **`gaussian_blur_s52(image)`** — line 4856

- **`gaussian_blur_s53(image)`** — line 4859

- **`gaussian_blur_s54(image)`** — line 4862

- **`gaussian_blur_s55(image)`** — line 4865

- **`gaussian_blur_s56(image)`** — line 4868

- **`gaussian_blur_s57(image)`** — line 4871

- **`gaussian_blur_s58(image)`** — line 4874

- **`gaussian_blur_s59(image)`** — line 4877

- **`gaussian_blur_s60(image)`** — line 4880

- **`shift_dym5_dxm5(image)`** — line 4883

- **`shift_dym5_dxm4(image)`** — line 4886

- **`shift_dym5_dxm3(image)`** — line 4889

- **`shift_dym5_dxm2(image)`** — line 4892

- **`shift_dym5_dxm1(image)`** — line 4895

- **`shift_dym5_dx0(image)`** — line 4898

- **`shift_dym5_dx1(image)`** — line 4901

- **`shift_dym5_dx2(image)`** — line 4904

- **`shift_dym5_dx3(image)`** — line 4907

- **`shift_dym5_dx4(image)`** — line 4910

- **`shift_dym5_dx5(image)`** — line 4913

- **`shift_dym4_dxm5(image)`** — line 4916

- **`shift_dym4_dxm4(image)`** — line 4919

- **`shift_dym4_dxm3(image)`** — line 4922

- **`shift_dym4_dxm2(image)`** — line 4925

- **`shift_dym4_dxm1(image)`** — line 4928

- **`shift_dym4_dx0(image)`** — line 4931

- **`shift_dym4_dx1(image)`** — line 4934

- **`shift_dym4_dx2(image)`** — line 4937

- **`shift_dym4_dx3(image)`** — line 4940

- **`shift_dym4_dx4(image)`** — line 4943

- **`shift_dym4_dx5(image)`** — line 4946

- **`shift_dym3_dxm5(image)`** — line 4949

- **`shift_dym3_dxm4(image)`** — line 4952

- **`shift_dym3_dxm3(image)`** — line 4955

- **`shift_dym3_dxm2(image)`** — line 4958

- **`shift_dym3_dxm1(image)`** — line 4961

- **`shift_dym3_dx0(image)`** — line 4964

- **`shift_dym3_dx1(image)`** — line 4967

- **`shift_dym3_dx2(image)`** — line 4970

- **`shift_dym3_dx3(image)`** — line 4973

- **`shift_dym3_dx4(image)`** — line 4976

- **`shift_dym3_dx5(image)`** — line 4979

- **`shift_dym2_dxm5(image)`** — line 4982

- **`shift_dym2_dxm4(image)`** — line 4985

- **`shift_dym2_dxm3(image)`** — line 4988

- **`shift_dym2_dxm2(image)`** — line 4991

- **`shift_dym2_dxm1(image)`** — line 4994

- **`shift_dym2_dx0(image)`** — line 4997

- **`shift_dym2_dx1(image)`** — line 5000

- **`shift_dym2_dx2(image)`** — line 5003

- **`shift_dym2_dx3(image)`** — line 5006

- **`shift_dym2_dx4(image)`** — line 5009

- **`shift_dym2_dx5(image)`** — line 5012

- **`shift_dym1_dxm5(image)`** — line 5015

- **`shift_dym1_dxm4(image)`** — line 5018

- **`shift_dym1_dxm3(image)`** — line 5021

- **`shift_dym1_dxm2(image)`** — line 5024

- **`shift_dym1_dxm1(image)`** — line 5027

- **`shift_dym1_dx0(image)`** — line 5030

- **`shift_dym1_dx1(image)`** — line 5033

- **`shift_dym1_dx2(image)`** — line 5036

- **`shift_dym1_dx3(image)`** — line 5039

- **`shift_dym1_dx4(image)`** — line 5042

- **`shift_dym1_dx5(image)`** — line 5045

- **`shift_dy0_dxm5(image)`** — line 5048

- **`shift_dy0_dxm4(image)`** — line 5051

- **`shift_dy0_dxm3(image)`** — line 5054

- **`shift_dy0_dxm2(image)`** — line 5057

- **`shift_dy0_dxm1(image)`** — line 5060

- **`shift_dy0_dx1(image)`** — line 5063

- **`shift_dy0_dx2(image)`** — line 5066

- **`shift_dy0_dx3(image)`** — line 5069

- **`shift_dy0_dx4(image)`** — line 5072

- **`shift_dy0_dx5(image)`** — line 5075

- **`shift_dy1_dxm5(image)`** — line 5078

- **`shift_dy1_dxm4(image)`** — line 5081

- **`shift_dy1_dxm3(image)`** — line 5084

- **`shift_dy1_dxm2(image)`** — line 5087

- **`shift_dy1_dxm1(image)`** — line 5090

- **`shift_dy1_dx0(image)`** — line 5093

- **`shift_dy1_dx1(image)`** — line 5096

- **`shift_dy1_dx2(image)`** — line 5099

- **`shift_dy1_dx3(image)`** — line 5102

- **`shift_dy1_dx4(image)`** — line 5105

- **`shift_dy1_dx5(image)`** — line 5108

- **`shift_dy2_dxm5(image)`** — line 5111

- **`shift_dy2_dxm4(image)`** — line 5114

- **`shift_dy2_dxm3(image)`** — line 5117

- **`shift_dy2_dxm2(image)`** — line 5120

- **`shift_dy2_dxm1(image)`** — line 5123

- **`shift_dy2_dx0(image)`** — line 5126

- **`shift_dy2_dx1(image)`** — line 5129

- **`shift_dy2_dx2(image)`** — line 5132

- **`shift_dy2_dx3(image)`** — line 5135

- **`shift_dy2_dx4(image)`** — line 5138

- **`shift_dy2_dx5(image)`** — line 5141

- **`shift_dy3_dxm5(image)`** — line 5144

- **`shift_dy3_dxm4(image)`** — line 5147

- **`shift_dy3_dxm3(image)`** — line 5150

- **`shift_dy3_dxm2(image)`** — line 5153

- **`shift_dy3_dxm1(image)`** — line 5156

- **`shift_dy3_dx0(image)`** — line 5159

- **`shift_dy3_dx1(image)`** — line 5162

- **`shift_dy3_dx2(image)`** — line 5165

- **`shift_dy3_dx3(image)`** — line 5168

- **`shift_dy3_dx4(image)`** — line 5171

- **`shift_dy3_dx5(image)`** — line 5174

- **`shift_dy4_dxm5(image)`** — line 5177

- **`shift_dy4_dxm4(image)`** — line 5180

- **`shift_dy4_dxm3(image)`** — line 5183

- **`shift_dy4_dxm2(image)`** — line 5186

- **`shift_dy4_dxm1(image)`** — line 5189

- **`shift_dy4_dx0(image)`** — line 5192

- **`shift_dy4_dx1(image)`** — line 5195

- **`shift_dy4_dx2(image)`** — line 5198

- **`shift_dy4_dx3(image)`** — line 5201

- **`shift_dy4_dx4(image)`** — line 5204

- **`shift_dy4_dx5(image)`** — line 5207

- **`shift_dy5_dxm5(image)`** — line 5210

- **`shift_dy5_dxm4(image)`** — line 5213

- **`shift_dy5_dxm3(image)`** — line 5216

- **`shift_dy5_dxm2(image)`** — line 5219

- **`shift_dy5_dxm1(image)`** — line 5222

- **`shift_dy5_dx0(image)`** — line 5225

- **`shift_dy5_dx1(image)`** — line 5228

- **`shift_dy5_dx2(image)`** — line 5231

- **`shift_dy5_dx3(image)`** — line 5234

- **`shift_dy5_dx4(image)`** — line 5237

- **`shift_dy5_dx5(image)`** — line 5240

- **`great_circle_distance_deg(lon1, lat1, lon2, lat2)`** — line 5249

- **`bearing_deg(lon1, lat1, lon2, lat2)`** — line 5257

- **`cylindrical_equal_area_weight(lat_deg)`** — line 5265

- **`integrate_mask_area_km2(mask, nav)`** — line 5269

- **`brightness_temperature_proxy(image, mask)`** — line 5274

Relative photometric proxy (not absolute Kelvin).

- **`limb_darkening_law(mu, u1, u2)`** — line 5281

Quadratic limb darkening I/I0 = 1 - u1(1-mu) - u2(1-mu)^2.

- **`apply_limb_darkening_model(shape, nav, u1, u2)`** — line 5287

- **`flatten_limb_darkening(image, nav, u1, u2, eps)`** — line 5299

- **`series_interpolate(t, y, t_new)`** — line 5304

- **`detrend_linear(t, y)`** — line 5309

- **`lomb_like_periodogram(t, y, periods)`** — line 5318

Simple least-squares periodogram power for oscillation search (e.g. 90d).

- **`search_90day_oscillation(t_mjd, lon_deg)`** — line 5334

- **`robust_mad(x)`** — line 5343

- **`outlier_mask_mad(x, kappa)`** — line 5348

- **`running_median(x, win)`** — line 5354

- **`align_by_centroid(image, ref_cy, ref_cx)`** — line 5366

- **`multi_frame_max_entropy_stack(frames, n_iter)`** — line 5375

Very simplified maximum-entropy-like iterative stack refinement.

- **`estimate_fwhm_from_edge(image, nav)`** — line 5389

- **`annulus_mask(shape, cy, cx, r0, r1)`** — line 5401

- **`export_winjupos_like_csv(path, state)`** — line 5408

- **`load_trajectory_csv(path)`** — line 5417

- **`states_from_trajectory_rows(rows)`** — line 5425

- **`photometric_aperture_radius_1(nav)`** — line 5446

Aperture radius as fraction-based px for photometry slot 1.

- **`photometric_aperture_radius_2(nav)`** — line 5450

Aperture radius as fraction-based px for photometry slot 2.

- **`photometric_aperture_radius_3(nav)`** — line 5454

Aperture radius as fraction-based px for photometry slot 3.

- **`photometric_aperture_radius_4(nav)`** — line 5458

Aperture radius as fraction-based px for photometry slot 4.

- **`photometric_aperture_radius_5(nav)`** — line 5462

Aperture radius as fraction-based px for photometry slot 5.

- **`photometric_aperture_radius_6(nav)`** — line 5466

Aperture radius as fraction-based px for photometry slot 6.

- **`photometric_aperture_radius_7(nav)`** — line 5470

Aperture radius as fraction-based px for photometry slot 7.

- **`photometric_aperture_radius_8(nav)`** — line 5474

Aperture radius as fraction-based px for photometry slot 8.

- **`photometric_aperture_radius_9(nav)`** — line 5478

Aperture radius as fraction-based px for photometry slot 9.

- **`photometric_aperture_radius_10(nav)`** — line 5482

Aperture radius as fraction-based px for photometry slot 10.

- **`photometric_aperture_radius_11(nav)`** — line 5486

Aperture radius as fraction-based px for photometry slot 11.

- **`photometric_aperture_radius_12(nav)`** — line 5490

Aperture radius as fraction-based px for photometry slot 12.

- **`photometric_aperture_radius_13(nav)`** — line 5494

Aperture radius as fraction-based px for photometry slot 13.

- **`photometric_aperture_radius_14(nav)`** — line 5498

Aperture radius as fraction-based px for photometry slot 14.

- **`photometric_aperture_radius_15(nav)`** — line 5502

Aperture radius as fraction-based px for photometry slot 15.

- **`photometric_aperture_radius_16(nav)`** — line 5506

Aperture radius as fraction-based px for photometry slot 16.

- **`photometric_aperture_radius_17(nav)`** — line 5510

Aperture radius as fraction-based px for photometry slot 17.

- **`photometric_aperture_radius_18(nav)`** — line 5514

Aperture radius as fraction-based px for photometry slot 18.

- **`photometric_aperture_radius_19(nav)`** — line 5518

Aperture radius as fraction-based px for photometry slot 19.

- **`photometric_aperture_radius_20(nav)`** — line 5522

Aperture radius as fraction-based px for photometry slot 20.

- **`photometric_aperture_radius_21(nav)`** — line 5526

Aperture radius as fraction-based px for photometry slot 21.

- **`photometric_aperture_radius_22(nav)`** — line 5530

Aperture radius as fraction-based px for photometry slot 22.

- **`photometric_aperture_radius_23(nav)`** — line 5534

Aperture radius as fraction-based px for photometry slot 23.

- **`photometric_aperture_radius_24(nav)`** — line 5538

Aperture radius as fraction-based px for photometry slot 24.

- **`photometric_aperture_radius_25(nav)`** — line 5542

Aperture radius as fraction-based px for photometry slot 25.

- **`photometric_aperture_radius_26(nav)`** — line 5546

Aperture radius as fraction-based px for photometry slot 26.

- **`photometric_aperture_radius_27(nav)`** — line 5550

Aperture radius as fraction-based px for photometry slot 27.

- **`photometric_aperture_radius_28(nav)`** — line 5554

Aperture radius as fraction-based px for photometry slot 28.

- **`photometric_aperture_radius_29(nav)`** — line 5558

Aperture radius as fraction-based px for photometry slot 29.

- **`photometric_aperture_radius_30(nav)`** — line 5562

Aperture radius as fraction-based px for photometry slot 30.

- **`photometric_aperture_radius_31(nav)`** — line 5566

Aperture radius as fraction-based px for photometry slot 31.

- **`photometric_aperture_radius_32(nav)`** — line 5570

Aperture radius as fraction-based px for photometry slot 32.

- **`photometric_aperture_radius_33(nav)`** — line 5574

Aperture radius as fraction-based px for photometry slot 33.

- **`photometric_aperture_radius_34(nav)`** — line 5578

Aperture radius as fraction-based px for photometry slot 34.

- **`photometric_aperture_radius_35(nav)`** — line 5582

Aperture radius as fraction-based px for photometry slot 35.

- **`photometric_aperture_radius_36(nav)`** — line 5586

Aperture radius as fraction-based px for photometry slot 36.

- **`photometric_aperture_radius_37(nav)`** — line 5590

Aperture radius as fraction-based px for photometry slot 37.

- **`photometric_aperture_radius_38(nav)`** — line 5594

Aperture radius as fraction-based px for photometry slot 38.

- **`photometric_aperture_radius_39(nav)`** — line 5598

Aperture radius as fraction-based px for photometry slot 39.

- **`photometric_aperture_radius_40(nav)`** — line 5602

Aperture radius as fraction-based px for photometry slot 40.

- **`photometric_aperture_radius_41(nav)`** — line 5606

Aperture radius as fraction-based px for photometry slot 41.

- **`photometric_aperture_radius_42(nav)`** — line 5610

Aperture radius as fraction-based px for photometry slot 42.

- **`photometric_aperture_radius_43(nav)`** — line 5614

Aperture radius as fraction-based px for photometry slot 43.

- **`photometric_aperture_radius_44(nav)`** — line 5618

Aperture radius as fraction-based px for photometry slot 44.

- **`photometric_aperture_radius_45(nav)`** — line 5622

Aperture radius as fraction-based px for photometry slot 45.

- **`photometric_aperture_radius_46(nav)`** — line 5626

Aperture radius as fraction-based px for photometry slot 46.

- **`photometric_aperture_radius_47(nav)`** — line 5630

Aperture radius as fraction-based px for photometry slot 47.

- **`photometric_aperture_radius_48(nav)`** — line 5634

Aperture radius as fraction-based px for photometry slot 48.

- **`photometric_aperture_radius_49(nav)`** — line 5638

Aperture radius as fraction-based px for photometry slot 49.

- **`photometric_aperture_radius_50(nav)`** — line 5642

Aperture radius as fraction-based px for photometry slot 50.

- **`photometric_aperture_radius_51(nav)`** — line 5646

Aperture radius as fraction-based px for photometry slot 51.

- **`photometric_aperture_radius_52(nav)`** — line 5650

Aperture radius as fraction-based px for photometry slot 52.

- **`photometric_aperture_radius_53(nav)`** — line 5654

Aperture radius as fraction-based px for photometry slot 53.

- **`photometric_aperture_radius_54(nav)`** — line 5658

Aperture radius as fraction-based px for photometry slot 54.

- **`photometric_aperture_radius_55(nav)`** — line 5662

Aperture radius as fraction-based px for photometry slot 55.

- **`photometric_aperture_radius_56(nav)`** — line 5666

Aperture radius as fraction-based px for photometry slot 56.

- **`photometric_aperture_radius_57(nav)`** — line 5670

Aperture radius as fraction-based px for photometry slot 57.

- **`photometric_aperture_radius_58(nav)`** — line 5674

Aperture radius as fraction-based px for photometry slot 58.

- **`photometric_aperture_radius_59(nav)`** — line 5678

Aperture radius as fraction-based px for photometry slot 59.

- **`photometric_aperture_radius_60(nav)`** — line 5682

Aperture radius as fraction-based px for photometry slot 60.

- **`photometric_aperture_radius_61(nav)`** — line 5686

Aperture radius as fraction-based px for photometry slot 61.

- **`photometric_aperture_radius_62(nav)`** — line 5690

Aperture radius as fraction-based px for photometry slot 62.

- **`photometric_aperture_radius_63(nav)`** — line 5694

Aperture radius as fraction-based px for photometry slot 63.

- **`photometric_aperture_radius_64(nav)`** — line 5698

Aperture radius as fraction-based px for photometry slot 64.

- **`photometric_aperture_radius_65(nav)`** — line 5702

Aperture radius as fraction-based px for photometry slot 65.

- **`photometric_aperture_radius_66(nav)`** — line 5706

Aperture radius as fraction-based px for photometry slot 66.

- **`photometric_aperture_radius_67(nav)`** — line 5710

Aperture radius as fraction-based px for photometry slot 67.

- **`photometric_aperture_radius_68(nav)`** — line 5714

Aperture radius as fraction-based px for photometry slot 68.

- **`photometric_aperture_radius_69(nav)`** — line 5718

Aperture radius as fraction-based px for photometry slot 69.

- **`photometric_aperture_radius_70(nav)`** — line 5722

Aperture radius as fraction-based px for photometry slot 70.

- **`photometric_aperture_radius_71(nav)`** — line 5726

Aperture radius as fraction-based px for photometry slot 71.

- **`photometric_aperture_radius_72(nav)`** — line 5730

Aperture radius as fraction-based px for photometry slot 72.

- **`photometric_aperture_radius_73(nav)`** — line 5734

Aperture radius as fraction-based px for photometry slot 73.

- **`photometric_aperture_radius_74(nav)`** — line 5738

Aperture radius as fraction-based px for photometry slot 74.

- **`photometric_aperture_radius_75(nav)`** — line 5742

Aperture radius as fraction-based px for photometry slot 75.

- **`photometric_aperture_radius_76(nav)`** — line 5746

Aperture radius as fraction-based px for photometry slot 76.

- **`photometric_aperture_radius_77(nav)`** — line 5750

Aperture radius as fraction-based px for photometry slot 77.

- **`photometric_aperture_radius_78(nav)`** — line 5754

Aperture radius as fraction-based px for photometry slot 78.

- **`photometric_aperture_radius_79(nav)`** — line 5758

Aperture radius as fraction-based px for photometry slot 79.

- **`photometric_aperture_radius_80(nav)`** — line 5762

Aperture radius as fraction-based px for photometry slot 80.

- **`photometric_aperture_radius_81(nav)`** — line 5766

Aperture radius as fraction-based px for photometry slot 81.

- **`photometric_aperture_radius_82(nav)`** — line 5770

Aperture radius as fraction-based px for photometry slot 82.

- **`photometric_aperture_radius_83(nav)`** — line 5774

Aperture radius as fraction-based px for photometry slot 83.

- **`photometric_aperture_radius_84(nav)`** — line 5778

Aperture radius as fraction-based px for photometry slot 84.

- **`photometric_aperture_radius_85(nav)`** — line 5782

Aperture radius as fraction-based px for photometry slot 85.

- **`photometric_aperture_radius_86(nav)`** — line 5786

Aperture radius as fraction-based px for photometry slot 86.

- **`photometric_aperture_radius_87(nav)`** — line 5790

Aperture radius as fraction-based px for photometry slot 87.

- **`photometric_aperture_radius_88(nav)`** — line 5794

Aperture radius as fraction-based px for photometry slot 88.

- **`photometric_aperture_radius_89(nav)`** — line 5798

Aperture radius as fraction-based px for photometry slot 89.

- **`photometric_aperture_radius_90(nav)`** — line 5802

Aperture radius as fraction-based px for photometry slot 90.

- **`photometric_aperture_radius_91(nav)`** — line 5806

Aperture radius as fraction-based px for photometry slot 91.

- **`photometric_aperture_radius_92(nav)`** — line 5810

Aperture radius as fraction-based px for photometry slot 92.

- **`photometric_aperture_radius_93(nav)`** — line 5814

Aperture radius as fraction-based px for photometry slot 93.

- **`photometric_aperture_radius_94(nav)`** — line 5818

Aperture radius as fraction-based px for photometry slot 94.

- **`photometric_aperture_radius_95(nav)`** — line 5822

Aperture radius as fraction-based px for photometry slot 95.

- **`photometric_aperture_radius_96(nav)`** — line 5826

Aperture radius as fraction-based px for photometry slot 96.

- **`photometric_aperture_radius_97(nav)`** — line 5830

Aperture radius as fraction-based px for photometry slot 97.

- **`photometric_aperture_radius_98(nav)`** — line 5834

Aperture radius as fraction-based px for photometry slot 98.

- **`photometric_aperture_radius_99(nav)`** — line 5838

Aperture radius as fraction-based px for photometry slot 99.

- **`photometric_aperture_radius_100(nav)`** — line 5842

Aperture radius as fraction-based px for photometry slot 100.

- **`reference_grs_length_year_1990()`** — line 5846

- **`reference_grs_width_year_1990()`** — line 5849

- **`reference_grs_length_year_1991()`** — line 5852

- **`reference_grs_width_year_1991()`** — line 5855

- **`reference_grs_length_year_1992()`** — line 5858

- **`reference_grs_width_year_1992()`** — line 5861

- **`reference_grs_length_year_1993()`** — line 5864

- **`reference_grs_width_year_1993()`** — line 5867

- **`reference_grs_length_year_1994()`** — line 5870

- **`reference_grs_width_year_1994()`** — line 5873

- **`reference_grs_length_year_1995()`** — line 5876

- **`reference_grs_width_year_1995()`** — line 5879

- **`reference_grs_length_year_1996()`** — line 5882

- **`reference_grs_width_year_1996()`** — line 5885

- **`reference_grs_length_year_1997()`** — line 5888

- **`reference_grs_width_year_1997()`** — line 5891

- **`reference_grs_length_year_1998()`** — line 5894

- **`reference_grs_width_year_1998()`** — line 5897

- **`reference_grs_length_year_1999()`** — line 5900

- **`reference_grs_width_year_1999()`** — line 5903

- **`reference_grs_length_year_2000()`** — line 5906

- **`reference_grs_width_year_2000()`** — line 5909

- **`reference_grs_length_year_2001()`** — line 5912

- **`reference_grs_width_year_2001()`** — line 5915

- **`reference_grs_length_year_2002()`** — line 5918

- **`reference_grs_width_year_2002()`** — line 5921

- **`reference_grs_length_year_2003()`** — line 5924

- **`reference_grs_width_year_2003()`** — line 5927

- **`reference_grs_length_year_2004()`** — line 5930

- **`reference_grs_width_year_2004()`** — line 5933

- **`reference_grs_length_year_2005()`** — line 5936

- **`reference_grs_width_year_2005()`** — line 5939

- **`reference_grs_length_year_2006()`** — line 5942

- **`reference_grs_width_year_2006()`** — line 5945

- **`reference_grs_length_year_2007()`** — line 5948

- **`reference_grs_width_year_2007()`** — line 5951

- **`reference_grs_length_year_2008()`** — line 5954

- **`reference_grs_width_year_2008()`** — line 5957

- **`reference_grs_length_year_2009()`** — line 5960

- **`reference_grs_width_year_2009()`** — line 5963

- **`reference_grs_length_year_2010()`** — line 5966

- **`reference_grs_width_year_2010()`** — line 5969

- **`reference_grs_length_year_2011()`** — line 5972

- **`reference_grs_width_year_2011()`** — line 5975

- **`reference_grs_length_year_2012()`** — line 5978

- **`reference_grs_width_year_2012()`** — line 5981

- **`reference_grs_length_year_2013()`** — line 5984

- **`reference_grs_width_year_2013()`** — line 5987

- **`reference_grs_length_year_2014()`** — line 5990

- **`reference_grs_width_year_2014()`** — line 5993

- **`reference_grs_length_year_2015()`** — line 5996

- **`reference_grs_width_year_2015()`** — line 5999

- **`reference_grs_length_year_2016()`** — line 6002

- **`reference_grs_width_year_2016()`** — line 6005

- **`reference_grs_length_year_2017()`** — line 6008

- **`reference_grs_width_year_2017()`** — line 6011

- **`reference_grs_length_year_2018()`** — line 6014

- **`reference_grs_width_year_2018()`** — line 6017

- **`reference_grs_length_year_2019()`** — line 6020

- **`reference_grs_width_year_2019()`** — line 6023

- **`reference_grs_length_year_2020()`** — line 6026

- **`reference_grs_width_year_2020()`** — line 6029

- **`reference_grs_length_year_2021()`** — line 6032

- **`reference_grs_width_year_2021()`** — line 6035

- **`reference_grs_length_year_2022()`** — line 6038

- **`reference_grs_width_year_2022()`** — line 6041

- **`reference_grs_length_year_2023()`** — line 6044

- **`reference_grs_width_year_2023()`** — line 6047

- **`reference_grs_length_year_2024()`** — line 6050

- **`reference_grs_width_year_2024()`** — line 6053

- **`reference_grs_length_year_2025()`** — line 6056

- **`reference_grs_width_year_2025()`** — line 6059

- **`reference_grs_length_year_2026()`** — line 6062

- **`reference_grs_width_year_2026()`** — line 6065

- **`sobel_magnitude(image)`** — line 6081

- **`prewitt_magnitude(image)`** — line 6086

- **`scharr_magnitude(image)`** — line 6091

- **`stage_name_planning()`** — line 6097

- **`stage_name_acquisition()`** — line 6101

- **`stage_name_ingest()`** — line 6105

- **`stage_name_qc()`** — line 6109

- **`stage_name_calibration()`** — line 6113

- **`stage_name_lucky_score()`** — line 6117

- **`stage_name_frame_select()`** — line 6121

- **`stage_name_global_align()`** — line 6125

- **`stage_name_local_ap_align()`** — line 6129

- **`stage_name_stack()`** — line 6133

- **`stage_name_noise_estimate()`** — line 6137

- **`stage_name_derotation()`** — line 6141

- **`stage_name_channel_register()`** — line 6145

- **`stage_name_dcr_correct()`** — line 6149

- **`stage_name_psf_estimate()`** — line 6153

- **`stage_name_wavelet_restore()`** — line 6157

- **`stage_name_rl_deconv()`** — line 6161

- **`stage_name_lrgb_merge()`** — line 6165

- **`stage_name_color_grade()`** — line 6169

- **`stage_name_limb_extract()`** — line 6173

- **`stage_name_ellipse_fit()`** — line 6177

- **`stage_name_bootstrap_nav()`** — line 6181

- **`stage_name_lonlat_project()`** — line 6185

- **`stage_name_grs_segment()`** — line 6189

- **`stage_name_grs_measure()`** — line 6193

- **`stage_name_bootstrap_grs()`** — line 6197

- **`stage_name_error_budget()`** — line 6201

- **`stage_name_trajectory_rts()`** — line 6205

- **`stage_name_drift_fit()`** — line 6209

- **`stage_name_export_fits()`** — line 6213

- **`stage_name_export_png()`** — line 6217

- **`stage_name_export_csv()`** — line 6221

- **`stage_name_export_manifest()`** — line 6225

- **`stage_name_validate()`** — line 6229

- **`stage_name_report()`** — line 6233

- **`select_top_1pct(scores)`** — line 6236

- **`select_top_2pct(scores)`** — line 6239

- **`select_top_3pct(scores)`** — line 6242

- **`select_top_4pct(scores)`** — line 6245

- **`select_top_5pct(scores)`** — line 6248

- **`select_top_6pct(scores)`** — line 6251

- **`select_top_7pct(scores)`** — line 6254

- **`select_top_8pct(scores)`** — line 6257

- **`select_top_9pct(scores)`** — line 6260

- **`select_top_10pct(scores)`** — line 6263

- **`select_top_11pct(scores)`** — line 6266

- **`select_top_12pct(scores)`** — line 6269

- **`select_top_13pct(scores)`** — line 6272

- **`select_top_14pct(scores)`** — line 6275

- **`select_top_15pct(scores)`** — line 6278

- **`select_top_16pct(scores)`** — line 6281

- **`select_top_17pct(scores)`** — line 6284

- **`select_top_18pct(scores)`** — line 6287

- **`select_top_19pct(scores)`** — line 6290

- **`select_top_20pct(scores)`** — line 6293

- **`select_top_21pct(scores)`** — line 6296

- **`select_top_22pct(scores)`** — line 6299

- **`select_top_23pct(scores)`** — line 6302

- **`select_top_24pct(scores)`** — line 6305

- **`select_top_25pct(scores)`** — line 6308

- **`select_top_26pct(scores)`** — line 6311

- **`select_top_27pct(scores)`** — line 6314

- **`select_top_28pct(scores)`** — line 6317

- **`select_top_29pct(scores)`** — line 6320

- **`select_top_30pct(scores)`** — line 6323

- **`select_top_31pct(scores)`** — line 6326

- **`select_top_32pct(scores)`** — line 6329

- **`select_top_33pct(scores)`** — line 6332

- **`select_top_34pct(scores)`** — line 6335

- **`select_top_35pct(scores)`** — line 6338

- **`select_top_36pct(scores)`** — line 6341

- **`select_top_37pct(scores)`** — line 6344

- **`select_top_38pct(scores)`** — line 6347

- **`select_top_39pct(scores)`** — line 6350

- **`select_top_40pct(scores)`** — line 6353

- **`select_top_41pct(scores)`** — line 6356

- **`select_top_42pct(scores)`** — line 6359

- **`select_top_43pct(scores)`** — line 6362

- **`select_top_44pct(scores)`** — line 6365

- **`select_top_45pct(scores)`** — line 6368

- **`select_top_46pct(scores)`** — line 6371

- **`select_top_47pct(scores)`** — line 6374

- **`select_top_48pct(scores)`** — line 6377

- **`select_top_49pct(scores)`** — line 6380

- **`select_top_50pct(scores)`** — line 6383

- **`select_top_51pct(scores)`** — line 6386

- **`select_top_52pct(scores)`** — line 6389

- **`select_top_53pct(scores)`** — line 6392

- **`select_top_54pct(scores)`** — line 6395

- **`select_top_55pct(scores)`** — line 6398

- **`select_top_56pct(scores)`** — line 6401

- **`select_top_57pct(scores)`** — line 6404

- **`select_top_58pct(scores)`** — line 6407

- **`select_top_59pct(scores)`** — line 6410

- **`select_top_60pct(scores)`** — line 6413

- **`select_top_61pct(scores)`** — line 6416

- **`select_top_62pct(scores)`** — line 6419

- **`select_top_63pct(scores)`** — line 6422

- **`select_top_64pct(scores)`** — line 6425

- **`select_top_65pct(scores)`** — line 6428

- **`select_top_66pct(scores)`** — line 6431

- **`select_top_67pct(scores)`** — line 6434

- **`select_top_68pct(scores)`** — line 6437

- **`select_top_69pct(scores)`** — line 6440

- **`select_top_70pct(scores)`** — line 6443

- **`select_top_71pct(scores)`** — line 6446

- **`select_top_72pct(scores)`** — line 6449

- **`select_top_73pct(scores)`** — line 6452

- **`select_top_74pct(scores)`** — line 6455

- **`select_top_75pct(scores)`** — line 6458

- **`select_top_76pct(scores)`** — line 6461

- **`select_top_77pct(scores)`** — line 6464

- **`select_top_78pct(scores)`** — line 6467

- **`select_top_79pct(scores)`** — line 6470

- **`select_top_80pct(scores)`** — line 6473

- **`select_top_81pct(scores)`** — line 6476

- **`select_top_82pct(scores)`** — line 6479

- **`select_top_83pct(scores)`** — line 6482

- **`select_top_84pct(scores)`** — line 6485

- **`select_top_85pct(scores)`** — line 6488

- **`select_top_86pct(scores)`** — line 6491

- **`select_top_87pct(scores)`** — line 6494

- **`select_top_88pct(scores)`** — line 6497

- **`select_top_89pct(scores)`** — line 6500

- **`select_top_90pct(scores)`** — line 6503

- **`select_top_91pct(scores)`** — line 6506

- **`select_top_92pct(scores)`** — line 6509

- **`select_top_93pct(scores)`** — line 6512

- **`select_top_94pct(scores)`** — line 6515

- **`select_top_95pct(scores)`** — line 6518

- **`select_top_96pct(scores)`** — line 6521

- **`select_top_97pct(scores)`** — line 6524

- **`select_top_98pct(scores)`** — line 6527

- **`select_top_99pct(scores)`** — line 6530

- **`select_top_100pct(scores)`** — line 6533

- **`landweber_deconv(image, psf, n_iter, omega)`** — line 6542

- **`van_cittert_deconv(image, psf, n_iter, mu)`** — line 6554

- **`multi_resolution_support(image, n_layers, k_sigma)`** — line 6563

- **`significant_wavelet_reconstruction(image, n_layers, k_sigma, gains)`** — line 6572

- **`pyramid_downsample(image)`** — line 6584

- **`pyramid_upsample(image, out_shape)`** — line 6588

- **`build_gaussian_pyramid(image, levels)`** — line 6592

- **`build_laplacian_pyramid(image, levels)`** — line 6601

- **`collapse_laplacian_pyramid(lpyr, residual)`** — line 6610

- **`focus_stack_from_pyramid(frames)`** — line 6617

Choose max-abs Laplacian coefficients across frames (focus stacking style).

- **`correlation_coefficient(a, b)`** — line 6629

- **`ssim_approx(a, b)`** — line 6635

- **`psnr(a, b, data_range)`** — line 6644

- **`radial_cut_pa_000(image, cy, cx, rmax)`** — line 6653

- **`radial_cut_pa_005(image, cy, cx, rmax)`** — line 6662

- **`radial_cut_pa_010(image, cy, cx, rmax)`** — line 6671

- **`radial_cut_pa_015(image, cy, cx, rmax)`** — line 6680

- **`radial_cut_pa_020(image, cy, cx, rmax)`** — line 6689

- **`radial_cut_pa_025(image, cy, cx, rmax)`** — line 6698

- **`radial_cut_pa_030(image, cy, cx, rmax)`** — line 6707

- **`radial_cut_pa_035(image, cy, cx, rmax)`** — line 6716

- **`radial_cut_pa_040(image, cy, cx, rmax)`** — line 6725

- **`radial_cut_pa_045(image, cy, cx, rmax)`** — line 6734

- **`radial_cut_pa_050(image, cy, cx, rmax)`** — line 6743

- **`radial_cut_pa_055(image, cy, cx, rmax)`** — line 6752

- **`radial_cut_pa_060(image, cy, cx, rmax)`** — line 6761

- **`radial_cut_pa_065(image, cy, cx, rmax)`** — line 6770

- **`radial_cut_pa_070(image, cy, cx, rmax)`** — line 6779

- **`radial_cut_pa_075(image, cy, cx, rmax)`** — line 6788

- **`radial_cut_pa_080(image, cy, cx, rmax)`** — line 6797

- **`radial_cut_pa_085(image, cy, cx, rmax)`** — line 6806

- **`radial_cut_pa_090(image, cy, cx, rmax)`** — line 6815

- **`radial_cut_pa_095(image, cy, cx, rmax)`** — line 6824

- **`radial_cut_pa_100(image, cy, cx, rmax)`** — line 6833

- **`radial_cut_pa_105(image, cy, cx, rmax)`** — line 6842

- **`radial_cut_pa_110(image, cy, cx, rmax)`** — line 6851

- **`radial_cut_pa_115(image, cy, cx, rmax)`** — line 6860

- **`radial_cut_pa_120(image, cy, cx, rmax)`** — line 6869

- **`radial_cut_pa_125(image, cy, cx, rmax)`** — line 6878

- **`radial_cut_pa_130(image, cy, cx, rmax)`** — line 6887

- **`radial_cut_pa_135(image, cy, cx, rmax)`** — line 6896

- **`radial_cut_pa_140(image, cy, cx, rmax)`** — line 6905

- **`radial_cut_pa_145(image, cy, cx, rmax)`** — line 6914

- **`radial_cut_pa_150(image, cy, cx, rmax)`** — line 6923

- **`radial_cut_pa_155(image, cy, cx, rmax)`** — line 6932

- **`radial_cut_pa_160(image, cy, cx, rmax)`** — line 6941

- **`radial_cut_pa_165(image, cy, cx, rmax)`** — line 6950

- **`radial_cut_pa_170(image, cy, cx, rmax)`** — line 6959

- **`radial_cut_pa_175(image, cy, cx, rmax)`** — line 6968

- **`radial_cut_pa_180(image, cy, cx, rmax)`** — line 6977

- **`radial_cut_pa_185(image, cy, cx, rmax)`** — line 6986

- **`radial_cut_pa_190(image, cy, cx, rmax)`** — line 6995

- **`radial_cut_pa_195(image, cy, cx, rmax)`** — line 7004

- **`radial_cut_pa_200(image, cy, cx, rmax)`** — line 7013

- **`radial_cut_pa_205(image, cy, cx, rmax)`** — line 7022

- **`radial_cut_pa_210(image, cy, cx, rmax)`** — line 7031

- **`radial_cut_pa_215(image, cy, cx, rmax)`** — line 7040

- **`radial_cut_pa_220(image, cy, cx, rmax)`** — line 7049

- **`radial_cut_pa_225(image, cy, cx, rmax)`** — line 7058

- **`radial_cut_pa_230(image, cy, cx, rmax)`** — line 7067

- **`radial_cut_pa_235(image, cy, cx, rmax)`** — line 7076

- **`radial_cut_pa_240(image, cy, cx, rmax)`** — line 7085

- **`radial_cut_pa_245(image, cy, cx, rmax)`** — line 7094

- **`radial_cut_pa_250(image, cy, cx, rmax)`** — line 7103

- **`radial_cut_pa_255(image, cy, cx, rmax)`** — line 7112

- **`radial_cut_pa_260(image, cy, cx, rmax)`** — line 7121

- **`radial_cut_pa_265(image, cy, cx, rmax)`** — line 7130

- **`radial_cut_pa_270(image, cy, cx, rmax)`** — line 7139

- **`radial_cut_pa_275(image, cy, cx, rmax)`** — line 7148

- **`radial_cut_pa_280(image, cy, cx, rmax)`** — line 7157

- **`radial_cut_pa_285(image, cy, cx, rmax)`** — line 7166

- **`radial_cut_pa_290(image, cy, cx, rmax)`** — line 7175

- **`radial_cut_pa_295(image, cy, cx, rmax)`** — line 7184

- **`radial_cut_pa_300(image, cy, cx, rmax)`** — line 7193

- **`radial_cut_pa_305(image, cy, cx, rmax)`** — line 7202

- **`radial_cut_pa_310(image, cy, cx, rmax)`** — line 7211

- **`radial_cut_pa_315(image, cy, cx, rmax)`** — line 7220

- **`radial_cut_pa_320(image, cy, cx, rmax)`** — line 7229

- **`radial_cut_pa_325(image, cy, cx, rmax)`** — line 7238

- **`radial_cut_pa_330(image, cy, cx, rmax)`** — line 7247

- **`radial_cut_pa_335(image, cy, cx, rmax)`** — line 7256

- **`radial_cut_pa_340(image, cy, cx, rmax)`** — line 7265

- **`radial_cut_pa_345(image, cy, cx, rmax)`** — line 7274

- **`radial_cut_pa_350(image, cy, cx, rmax)`** — line 7283

- **`radial_cut_pa_355(image, cy, cx, rmax)`** — line 7292

- **`longitude_bin_photometry_18(image, nav, lat0, dlat)`** — line 7301

- **`longitude_bin_photometry_24(image, nav, lat0, dlat)`** — line 7313

- **`longitude_bin_photometry_30(image, nav, lat0, dlat)`** — line 7325

- **`longitude_bin_photometry_36(image, nav, lat0, dlat)`** — line 7337

- **`longitude_bin_photometry_45(image, nav, lat0, dlat)`** — line 7349

- **`longitude_bin_photometry_60(image, nav, lat0, dlat)`** — line 7361

- **`longitude_bin_photometry_72(image, nav, lat0, dlat)`** — line 7373

- **`longitude_bin_photometry_90(image, nav, lat0, dlat)`** — line 7385

- **`longitude_bin_photometry_120(image, nav, lat0, dlat)`** — line 7397

- **`longitude_bin_photometry_180(image, nav, lat0, dlat)`** — line 7409

- **`longitude_bin_photometry_360(image, nav, lat0, dlat)`** — line 7421

- **`cfg_set_mode(cfg, value)`** — line 7433

- **`cfg_get_mode(cfg)`** — line 7436

- **`cfg_set_seed(cfg, value)`** — line 7439

- **`cfg_get_seed(cfg)`** — line 7442

- **`cfg_set_raw_dir(cfg, value)`** — line 7445

- **`cfg_get_raw_dir(cfg)`** — line 7448

- **`cfg_set_work_dir(cfg, value)`** — line 7451

- **`cfg_get_work_dir(cfg)`** — line 7454

- **`cfg_set_out_dir(cfg, value)`** — line 7457

- **`cfg_get_out_dir(cfg)`** — line 7460

- **`cfg_set_site_lat(cfg, value)`** — line 7463

- **`cfg_get_site_lat(cfg)`** — line 7466

- **`cfg_set_site_lon(cfg, value)`** — line 7469

- **`cfg_get_site_lon(cfg)`** — line 7472

- **`cfg_set_site_elev_m(cfg, value)`** — line 7475

- **`cfg_get_site_elev_m(cfg)`** — line 7478

- **`cfg_set_quality_metric(cfg, value)`** — line 7481

- **`cfg_get_quality_metric(cfg)`** — line 7484

- **`cfg_set_primary_fraction(cfg, value)`** — line 7487

- **`cfg_get_primary_fraction(cfg)`** — line 7490

- **`cfg_set_ap_grid(cfg, value)`** — line 7493

- **`cfg_get_ap_grid(cfg)`** — line 7496

- **`cfg_set_ap_box(cfg, value)`** — line 7499

- **`cfg_get_ap_box(cfg)`** — line 7502

- **`cfg_set_max_shift_px(cfg, value)`** — line 7505

- **`cfg_get_max_shift_px(cfg)`** — line 7508

- **`cfg_set_align_mode(cfg, value)`** — line 7511

- **`cfg_get_align_mode(cfg)`** — line 7514

- **`cfg_set_stack_method(cfg, value)`** — line 7517

- **`cfg_get_stack_method(cfg)`** — line 7520

- **`cfg_set_kappa(cfg, value)`** — line 7523

- **`cfg_get_kappa(cfg)`** — line 7526

- **`cfg_set_drizzle_scale(cfg, value)`** — line 7529

- **`cfg_get_drizzle_scale(cfg)`** — line 7532

- **`cfg_set_derot_enable(cfg, value)`** — line 7535

- **`cfg_get_derot_enable(cfg)`** — line 7538

- **`cfg_set_derot_map_width(cfg, value)`** — line 7541

- **`cfg_get_derot_map_width(cfg)`** — line 7544

- **`cfg_set_restore_method(cfg, value)`** — line 7547

- **`cfg_get_restore_method(cfg)`** — line 7550

- **`cfg_set_wavelet_layers(cfg, value)`** — line 7553

- **`cfg_get_wavelet_layers(cfg)`** — line 7556

- **`cfg_set_rl_iters(cfg, value)`** — line 7559

- **`cfg_get_rl_iters(cfg)`** — line 7562

- **`cfg_set_l_source(cfg, value)`** — line 7565

- **`cfg_get_l_source(cfg)`** — line 7568

- **`cfg_set_sat_scale(cfg, value)`** — line 7571

- **`cfg_get_sat_scale(cfg)`** — line 7574

- **`cfg_set_denoise_chroma(cfg, value)`** — line 7577

- **`cfg_get_denoise_chroma(cfg)`** — line 7580

- **`cfg_set_limb_method(cfg, value)`** — line 7583

- **`cfg_get_limb_method(cfg)`** — line 7586

- **`cfg_set_n_rays(cfg, value)`** — line 7589

- **`cfg_get_n_rays(cfg)`** — line 7592

- **`cfg_set_bootstrap_limb(cfg, value)`** — line 7595

- **`cfg_get_bootstrap_limb(cfg)`** — line 7598

- **`cfg_set_grs_definition_id(cfg, value)`** — line 7601

- **`cfg_get_grs_definition_id(cfg)`** — line 7604

- **`cfg_set_segment_method(cfg, value)`** — line 7607

- **`cfg_get_segment_method(cfg)`** — line 7610

- **`cfg_set_bootstrap_n(cfg, value)`** — line 7613

- **`cfg_get_bootstrap_n(cfg)`** — line 7616

- **`cfg_set_traj_enable(cfg, value)`** — line 7619

- **`cfg_get_traj_enable(cfg)`** — line 7622

- **`cfg_set_smoother(cfg, value)`** — line 7625

- **`cfg_get_smoother(cfg)`** — line 7628

- **`cfg_set_process_noise_lon(cfg, value)`** — line 7631

- **`cfg_get_process_noise_lon(cfg)`** — line 7634

- **`cfg_set_write_fits(cfg, value)`** — line 7637

- **`cfg_get_write_fits(cfg)`** — line 7640

- **`cfg_set_write_png(cfg, value)`** — line 7643

- **`cfg_get_write_png(cfg)`** — line 7646

- **`cfg_set_write_csv(cfg, value)`** — line 7649

- **`cfg_get_write_csv(cfg)`** — line 7652

- **`cfg_set_log_level(cfg, value)`** — line 7655

- **`cfg_get_log_level(cfg)`** — line 7658

- **`cfg_set_min_frames(cfg, value)`** — line 7661

- **`cfg_get_min_frames(cfg)`** — line 7664

- **`cfg_set_max_clip_frac(cfg, value)`** — line 7667

- **`cfg_get_max_clip_frac(cfg)`** — line 7670

- **`cfg_set_flux_drop_frac(cfg, value)`** — line 7673

- **`cfg_get_flux_drop_frac(cfg)`** — line 7676

- **`make_demo_cube_1(size, n_frames)`** — line 7679

- **`make_demo_cube_2(size, n_frames)`** — line 7682

- **`make_demo_cube_3(size, n_frames)`** — line 7685

- **`make_demo_cube_4(size, n_frames)`** — line 7688

- **`make_demo_cube_5(size, n_frames)`** — line 7691

- **`make_demo_cube_6(size, n_frames)`** — line 7694

- **`make_demo_cube_7(size, n_frames)`** — line 7697

- **`make_demo_cube_8(size, n_frames)`** — line 7700

- **`make_demo_cube_9(size, n_frames)`** — line 7703

- **`make_demo_cube_10(size, n_frames)`** — line 7706

- **`make_demo_cube_11(size, n_frames)`** — line 7709

- **`make_demo_cube_12(size, n_frames)`** — line 7712

- **`make_demo_cube_13(size, n_frames)`** — line 7715

- **`make_demo_cube_14(size, n_frames)`** — line 7718

- **`make_demo_cube_15(size, n_frames)`** — line 7721

- **`make_demo_cube_16(size, n_frames)`** — line 7724

- **`make_demo_cube_17(size, n_frames)`** — line 7727

- **`make_demo_cube_18(size, n_frames)`** — line 7730

- **`make_demo_cube_19(size, n_frames)`** — line 7733

- **`make_demo_cube_20(size, n_frames)`** — line 7736

- **`make_demo_cube_21(size, n_frames)`** — line 7739

- **`make_demo_cube_22(size, n_frames)`** — line 7742

- **`make_demo_cube_23(size, n_frames)`** — line 7745

- **`make_demo_cube_24(size, n_frames)`** — line 7748

- **`make_demo_cube_25(size, n_frames)`** — line 7751

- **`make_demo_cube_26(size, n_frames)`** — line 7754

- **`make_demo_cube_27(size, n_frames)`** — line 7757

- **`make_demo_cube_28(size, n_frames)`** — line 7760

- **`make_demo_cube_29(size, n_frames)`** — line 7763

- **`make_demo_cube_30(size, n_frames)`** — line 7766

- **`make_demo_cube_31(size, n_frames)`** — line 7769

- **`make_demo_cube_32(size, n_frames)`** — line 7772

- **`make_demo_cube_33(size, n_frames)`** — line 7775

- **`make_demo_cube_34(size, n_frames)`** — line 7778

- **`make_demo_cube_35(size, n_frames)`** — line 7781

- **`make_demo_cube_36(size, n_frames)`** — line 7784

- **`make_demo_cube_37(size, n_frames)`** — line 7787

- **`make_demo_cube_38(size, n_frames)`** — line 7790

- **`make_demo_cube_39(size, n_frames)`** — line 7793

- **`make_demo_cube_40(size, n_frames)`** — line 7796

- **`make_demo_cube_41(size, n_frames)`** — line 7799

- **`make_demo_cube_42(size, n_frames)`** — line 7802

- **`make_demo_cube_43(size, n_frames)`** — line 7805

- **`make_demo_cube_44(size, n_frames)`** — line 7808

- **`make_demo_cube_45(size, n_frames)`** — line 7811

- **`make_demo_cube_46(size, n_frames)`** — line 7814

- **`make_demo_cube_47(size, n_frames)`** — line 7817

- **`make_demo_cube_48(size, n_frames)`** — line 7820

- **`make_demo_cube_49(size, n_frames)`** — line 7823

- **`make_demo_cube_50(size, n_frames)`** — line 7826

- **`hash_pipeline_inputs(paths)`** — line 7829

- **`compare_states(a, b)`** — line 7839

- **`format_state_line(state)`** — line 7848

- **`ensure_rgb_float(rgb)`** — line 7853

- **`save_channels_fits(out_dir, channels)`** — line 7862

- **`load_channels_fits(out_dir)`** — line 7868

- **`print_user_manual()`** — line 8154

- **`polynomial_background_order_0(image)`** — line 8159

- **`polynomial_background_order_1(image)`** — line 8162

- **`polynomial_background_order_2(image)`** — line 8165

- **`polynomial_background_order_3(image)`** — line 8168

- **`polynomial_background_order_4(image)`** — line 8171

- **`polynomial_background_order_5(image)`** — line 8174

- **`polynomial_background_order_6(image)`** — line 8177

- **`polynomial_background_order_7(image)`** — line 8180

- **`polynomial_background_order_8(image)`** — line 8183

- **`polynomial_background_order_9(image)`** — line 8186

- **`polynomial_background_order_10(image)`** — line 8189

- **`polynomial_background_order_11(image)`** — line 8192

- **`polynomial_background_order_12(image)`** — line 8195

- **`polynomial_background_order_13(image)`** — line 8198

- **`polynomial_background_order_14(image)`** — line 8201

- **`polynomial_background_order_15(image)`** — line 8204

- **`threshold_percentile_5(image)`** — line 8207

- **`threshold_percentile_10(image)`** — line 8211

- **`threshold_percentile_15(image)`** — line 8215

- **`threshold_percentile_20(image)`** — line 8219

- **`threshold_percentile_25(image)`** — line 8223

- **`threshold_percentile_30(image)`** — line 8227

- **`threshold_percentile_35(image)`** — line 8231

- **`threshold_percentile_40(image)`** — line 8235

- **`threshold_percentile_45(image)`** — line 8239

- **`threshold_percentile_50(image)`** — line 8243

- **`threshold_percentile_55(image)`** — line 8247

- **`threshold_percentile_60(image)`** — line 8251

- **`threshold_percentile_65(image)`** — line 8255

- **`threshold_percentile_70(image)`** — line 8259

- **`threshold_percentile_75(image)`** — line 8263

- **`threshold_percentile_80(image)`** — line 8267

- **`threshold_percentile_85(image)`** — line 8271

- **`threshold_percentile_90(image)`** — line 8275

- **`threshold_percentile_95(image)`** — line 8279

- **`morph_open_1(mask)`** — line 8283

- **`morph_close_1(mask)`** — line 8286

- **`morph_open_2(mask)`** — line 8289

- **`morph_close_2(mask)`** — line 8292

- **`morph_open_3(mask)`** — line 8295

- **`morph_close_3(mask)`** — line 8298

- **`morph_open_4(mask)`** — line 8301

- **`morph_close_4(mask)`** — line 8304

- **`morph_open_5(mask)`** — line 8307

- **`morph_close_5(mask)`** — line 8310

- **`morph_open_6(mask)`** — line 8313

- **`morph_close_6(mask)`** — line 8316

- **`morph_open_7(mask)`** — line 8319

- **`morph_close_7(mask)`** — line 8322

- **`morph_open_8(mask)`** — line 8325

- **`morph_close_8(mask)`** — line 8328

- **`morph_open_9(mask)`** — line 8331

- **`morph_close_9(mask)`** — line 8334

- **`morph_open_10(mask)`** — line 8337

- **`morph_close_10(mask)`** — line 8340

- **`morph_open_11(mask)`** — line 8343

- **`morph_close_11(mask)`** — line 8346

- **`morph_open_12(mask)`** — line 8349

- **`morph_close_12(mask)`** — line 8352

- **`morph_open_13(mask)`** — line 8355

- **`morph_close_13(mask)`** — line 8358

- **`morph_open_14(mask)`** — line 8361

- **`morph_close_14(mask)`** — line 8364

- **`morph_open_15(mask)`** — line 8367

- **`morph_close_15(mask)`** — line 8370

- **`morph_open_16(mask)`** — line 8373

- **`morph_close_16(mask)`** — line 8376

- **`morph_open_17(mask)`** — line 8379

- **`morph_close_17(mask)`** — line 8382

- **`morph_open_18(mask)`** — line 8385

- **`morph_close_18(mask)`** — line 8388

- **`morph_open_19(mask)`** — line 8391

- **`morph_close_19(mask)`** — line 8394

- **`morph_open_20(mask)`** — line 8397

- **`morph_close_20(mask)`** — line 8400

- **`morph_open_21(mask)`** — line 8403

- **`morph_close_21(mask)`** — line 8406

- **`morph_open_22(mask)`** — line 8409

- **`morph_close_22(mask)`** — line 8412

- **`morph_open_23(mask)`** — line 8415

- **`morph_close_23(mask)`** — line 8418

- **`morph_open_24(mask)`** — line 8421

- **`morph_close_24(mask)`** — line 8424

- **`morph_open_25(mask)`** — line 8427

- **`morph_close_25(mask)`** — line 8430

- **`morph_open_26(mask)`** — line 8433

- **`morph_close_26(mask)`** — line 8436

- **`morph_open_27(mask)`** — line 8439

- **`morph_close_27(mask)`** — line 8442

- **`morph_open_28(mask)`** — line 8445

- **`morph_close_28(mask)`** — line 8448

- **`morph_open_29(mask)`** — line 8451

- **`morph_close_29(mask)`** — line 8454

- **`morph_open_30(mask)`** — line 8457

- **`morph_close_30(mask)`** — line 8460

- **`moffat_psf_size_5(alpha, beta)`** — line 8463

- **`gaussian_psf_size_5(sigma)`** — line 8466

- **`moffat_psf_size_7(alpha, beta)`** — line 8469

- **`gaussian_psf_size_7(sigma)`** — line 8472

- **`moffat_psf_size_9(alpha, beta)`** — line 8475

- **`gaussian_psf_size_9(sigma)`** — line 8478

- **`moffat_psf_size_11(alpha, beta)`** — line 8481

- **`gaussian_psf_size_11(sigma)`** — line 8484

- **`moffat_psf_size_13(alpha, beta)`** — line 8487

- **`gaussian_psf_size_13(sigma)`** — line 8490

- **`moffat_psf_size_15(alpha, beta)`** — line 8493

- **`gaussian_psf_size_15(sigma)`** — line 8496

- **`moffat_psf_size_17(alpha, beta)`** — line 8499

- **`gaussian_psf_size_17(sigma)`** — line 8502

- **`moffat_psf_size_19(alpha, beta)`** — line 8505

- **`gaussian_psf_size_19(sigma)`** — line 8508

- **`moffat_psf_size_21(alpha, beta)`** — line 8511

- **`gaussian_psf_size_21(sigma)`** — line 8514

- **`moffat_psf_size_23(alpha, beta)`** — line 8517

- **`gaussian_psf_size_23(sigma)`** — line 8520

- **`moffat_psf_size_25(alpha, beta)`** — line 8523

- **`gaussian_psf_size_25(sigma)`** — line 8526

- **`moffat_psf_size_27(alpha, beta)`** — line 8529

- **`gaussian_psf_size_27(sigma)`** — line 8532

- **`moffat_psf_size_29(alpha, beta)`** — line 8535

- **`gaussian_psf_size_29(sigma)`** — line 8538

- **`moffat_psf_size_31(alpha, beta)`** — line 8541

- **`gaussian_psf_size_31(sigma)`** — line 8544

- **`moffat_psf_size_33(alpha, beta)`** — line 8547

- **`gaussian_psf_size_33(sigma)`** — line 8550

- **`moffat_psf_size_35(alpha, beta)`** — line 8553

- **`gaussian_psf_size_35(sigma)`** — line 8556

- **`moffat_psf_size_37(alpha, beta)`** — line 8559

- **`gaussian_psf_size_37(sigma)`** — line 8562

- **`moffat_psf_size_39(alpha, beta)`** — line 8565

- **`gaussian_psf_size_39(sigma)`** — line 8568

- **`moffat_psf_size_41(alpha, beta)`** — line 8571

- **`gaussian_psf_size_41(sigma)`** — line 8574

- **`moffat_psf_size_43(alpha, beta)`** — line 8577

- **`gaussian_psf_size_43(sigma)`** — line 8580

- **`moffat_psf_size_45(alpha, beta)`** — line 8583

- **`gaussian_psf_size_45(sigma)`** — line 8586

- **`moffat_psf_size_47(alpha, beta)`** — line 8589

- **`gaussian_psf_size_47(sigma)`** — line 8592

- **`moffat_psf_size_49(alpha, beta)`** — line 8595

- **`gaussian_psf_size_49(sigma)`** — line 8598

- **`moffat_psf_size_51(alpha, beta)`** — line 8601

- **`gaussian_psf_size_51(sigma)`** — line 8604

- **`moffat_psf_size_53(alpha, beta)`** — line 8607

- **`gaussian_psf_size_53(sigma)`** — line 8610

- **`moffat_psf_size_55(alpha, beta)`** — line 8613

- **`gaussian_psf_size_55(sigma)`** — line 8616

- **`moffat_psf_size_57(alpha, beta)`** — line 8619

- **`gaussian_psf_size_57(sigma)`** — line 8622

- **`moffat_psf_size_59(alpha, beta)`** — line 8625

- **`gaussian_psf_size_59(sigma)`** — line 8628

- **`moffat_psf_size_61(alpha, beta)`** — line 8631

- **`gaussian_psf_size_61(sigma)`** — line 8634

- **`moffat_psf_size_63(alpha, beta)`** — line 8637

- **`gaussian_psf_size_63(sigma)`** — line 8640

- **`rl_deconv_1iter(image, psf)`** — line 8643

- **`rl_deconv_2iter(image, psf)`** — line 8646

- **`rl_deconv_3iter(image, psf)`** — line 8649

- **`rl_deconv_4iter(image, psf)`** — line 8652

- **`rl_deconv_5iter(image, psf)`** — line 8655

- **`rl_deconv_6iter(image, psf)`** — line 8658

- **`rl_deconv_7iter(image, psf)`** — line 8661

- **`rl_deconv_8iter(image, psf)`** — line 8664

- **`rl_deconv_9iter(image, psf)`** — line 8667

- **`rl_deconv_10iter(image, psf)`** — line 8670

- **`rl_deconv_11iter(image, psf)`** — line 8673

- **`rl_deconv_12iter(image, psf)`** — line 8676

- **`rl_deconv_13iter(image, psf)`** — line 8679

- **`rl_deconv_14iter(image, psf)`** — line 8682

- **`rl_deconv_15iter(image, psf)`** — line 8685

- **`rl_deconv_16iter(image, psf)`** — line 8688

- **`rl_deconv_17iter(image, psf)`** — line 8691

- **`rl_deconv_18iter(image, psf)`** — line 8694

- **`rl_deconv_19iter(image, psf)`** — line 8697

- **`rl_deconv_20iter(image, psf)`** — line 8700

- **`rl_deconv_21iter(image, psf)`** — line 8703

- **`rl_deconv_22iter(image, psf)`** — line 8706

- **`rl_deconv_23iter(image, psf)`** — line 8709

- **`rl_deconv_24iter(image, psf)`** — line 8712

- **`rl_deconv_25iter(image, psf)`** — line 8715

- **`rl_deconv_26iter(image, psf)`** — line 8718

- **`rl_deconv_27iter(image, psf)`** — line 8721

- **`rl_deconv_28iter(image, psf)`** — line 8724

- **`rl_deconv_29iter(image, psf)`** — line 8727

- **`rl_deconv_30iter(image, psf)`** — line 8730

- **`rl_deconv_31iter(image, psf)`** — line 8733

- **`rl_deconv_32iter(image, psf)`** — line 8736

- **`rl_deconv_33iter(image, psf)`** — line 8739

- **`rl_deconv_34iter(image, psf)`** — line 8742

- **`rl_deconv_35iter(image, psf)`** — line 8745

- **`rl_deconv_36iter(image, psf)`** — line 8748

- **`rl_deconv_37iter(image, psf)`** — line 8751

- **`rl_deconv_38iter(image, psf)`** — line 8754

- **`rl_deconv_39iter(image, psf)`** — line 8757

- **`rl_deconv_40iter(image, psf)`** — line 8760

- **`process_existing_rgb_fits(path, cfg)`** — line 8763

Convenience: reduce a finished RGB stacked FITS (e.g. AutoStakkert output).

- **`quick_measure_path(path, bootstrap)`** — line 8781

Quick measure. Requires FITS DATE-OBS (or user_time). Never uses wall-clock now.

- **`api_list()`** — line 8817

- **`monte_carlo_centroid_stability(n, size, seed)`** — line 8824

- **`mc_case_000()`** — line 8844

- **`mc_case_001()`** — line 8847

- **`mc_case_002()`** — line 8850

- **`mc_case_003()`** — line 8853

- **`mc_case_004()`** — line 8856

- **`mc_case_005()`** — line 8859

- **`mc_case_006()`** — line 8862

- **`mc_case_007()`** — line 8865

- **`mc_case_008()`** — line 8868

- **`mc_case_009()`** — line 8871

- **`mc_case_010()`** — line 8874

- **`mc_case_011()`** — line 8877

- **`mc_case_012()`** — line 8880

- **`mc_case_013()`** — line 8883

- **`mc_case_014()`** — line 8886

- **`mc_case_015()`** — line 8889

- **`mc_case_016()`** — line 8892

- **`mc_case_017()`** — line 8895

- **`mc_case_018()`** — line 8898

- **`mc_case_019()`** — line 8901

- **`mc_case_020()`** — line 8904

- **`mc_case_021()`** — line 8907

- **`mc_case_022()`** — line 8910

- **`mc_case_023()`** — line 8913

- **`mc_case_024()`** — line 8916

- **`mc_case_025()`** — line 8919

- **`mc_case_026()`** — line 8922

- **`mc_case_027()`** — line 8925

- **`mc_case_028()`** — line 8928

- **`mc_case_029()`** — line 8931

- **`mc_case_030()`** — line 8934

- **`mc_case_031()`** — line 8937

- **`mc_case_032()`** — line 8940

- **`mc_case_033()`** — line 8943

- **`mc_case_034()`** — line 8946

- **`mc_case_035()`** — line 8949

- **`mc_case_036()`** — line 8952

- **`mc_case_037()`** — line 8955

- **`mc_case_038()`** — line 8958

- **`mc_case_039()`** — line 8961

- **`mc_case_040()`** — line 8964

- **`mc_case_041()`** — line 8967

- **`mc_case_042()`** — line 8970

- **`mc_case_043()`** — line 8973

- **`mc_case_044()`** — line 8976

- **`mc_case_045()`** — line 8979

- **`mc_case_046()`** — line 8982

- **`mc_case_047()`** — line 8985

- **`mc_case_048()`** — line 8988

- **`mc_case_049()`** — line 8991

- **`mc_case_050()`** — line 8994

- **`mc_case_051()`** — line 8997

- **`mc_case_052()`** — line 9000

- **`mc_case_053()`** — line 9003

- **`mc_case_054()`** — line 9006

- **`mc_case_055()`** — line 9009

- **`mc_case_056()`** — line 9012

- **`mc_case_057()`** — line 9015

- **`mc_case_058()`** — line 9018

- **`mc_case_059()`** — line 9021

- **`mc_case_060()`** — line 9024

- **`mc_case_061()`** — line 9027

- **`mc_case_062()`** — line 9030

- **`mc_case_063()`** — line 9033

- **`mc_case_064()`** — line 9036

- **`mc_case_065()`** — line 9039

- **`mc_case_066()`** — line 9042

- **`mc_case_067()`** — line 9045

- **`mc_case_068()`** — line 9048

- **`mc_case_069()`** — line 9051

- **`mc_case_070()`** — line 9054

- **`mc_case_071()`** — line 9057

- **`mc_case_072()`** — line 9060

- **`mc_case_073()`** — line 9063

- **`mc_case_074()`** — line 9066

- **`mc_case_075()`** — line 9069

- **`mc_case_076()`** — line 9072

- **`mc_case_077()`** — line 9075

- **`mc_case_078()`** — line 9078

- **`mc_case_079()`** — line 9081

- **`mc_case_080()`** — line 9084

- **`mc_case_081()`** — line 9087

- **`mc_case_082()`** — line 9090

- **`mc_case_083()`** — line 9093

- **`mc_case_084()`** — line 9096

- **`mc_case_085()`** — line 9099

- **`mc_case_086()`** — line 9102

- **`mc_case_087()`** — line 9105

- **`mc_case_088()`** — line 9108

- **`mc_case_089()`** — line 9111

- **`mc_case_090()`** — line 9114

- **`mc_case_091()`** — line 9117

- **`mc_case_092()`** — line 9120

- **`mc_case_093()`** — line 9123

- **`mc_case_094()`** — line 9126

- **`mc_case_095()`** — line 9129

- **`mc_case_096()`** — line 9132

- **`mc_case_097()`** — line 9135

- **`mc_case_098()`** — line 9138

- **`mc_case_099()`** — line 9141

- **`mc_case_100()`** — line 9144

- **`mc_case_101()`** — line 9147

- **`mc_case_102()`** — line 9150

- **`mc_case_103()`** — line 9153

- **`mc_case_104()`** — line 9156

- **`mc_case_105()`** — line 9159

- **`mc_case_106()`** — line 9162

- **`mc_case_107()`** — line 9165

- **`mc_case_108()`** — line 9168

- **`mc_case_109()`** — line 9171

- **`mc_case_110()`** — line 9174

- **`mc_case_111()`** — line 9177

- **`mc_case_112()`** — line 9180

- **`mc_case_113()`** — line 9183

- **`mc_case_114()`** — line 9186

- **`mc_case_115()`** — line 9189

- **`mc_case_116()`** — line 9192

- **`mc_case_117()`** — line 9195

- **`mc_case_118()`** — line 9198

- **`mc_case_119()`** — line 9201

- **`mc_case_120()`** — line 9204

- **`mc_case_121()`** — line 9207

- **`mc_case_122()`** — line 9210

- **`mc_case_123()`** — line 9213

- **`mc_case_124()`** — line 9216

- **`mc_case_125()`** — line 9219

- **`mc_case_126()`** — line 9222

- **`mc_case_127()`** — line 9225

- **`mc_case_128()`** — line 9228

- **`mc_case_129()`** — line 9231

- **`mc_case_130()`** — line 9234

- **`mc_case_131()`** — line 9237

- **`mc_case_132()`** — line 9240

- **`mc_case_133()`** — line 9243

- **`mc_case_134()`** — line 9246

- **`mc_case_135()`** — line 9249

- **`mc_case_136()`** — line 9252

- **`mc_case_137()`** — line 9255

- **`mc_case_138()`** — line 9258

- **`mc_case_139()`** — line 9261

- **`mc_case_140()`** — line 9264

- **`mc_case_141()`** — line 9267

- **`mc_case_142()`** — line 9270

- **`mc_case_143()`** — line 9273

- **`mc_case_144()`** — line 9276

- **`mc_case_145()`** — line 9279

- **`mc_case_146()`** — line 9282

- **`mc_case_147()`** — line 9285

- **`mc_case_148()`** — line 9288

- **`mc_case_149()`** — line 9291

- **`mc_case_150()`** — line 9294

- **`mc_case_151()`** — line 9297

- **`mc_case_152()`** — line 9300

- **`mc_case_153()`** — line 9303

- **`mc_case_154()`** — line 9306

- **`mc_case_155()`** — line 9309

- **`mc_case_156()`** — line 9312

- **`mc_case_157()`** — line 9315

- **`mc_case_158()`** — line 9318

- **`mc_case_159()`** — line 9321

- **`mc_case_160()`** — line 9324

- **`mc_case_161()`** — line 9327

- **`mc_case_162()`** — line 9330

- **`mc_case_163()`** — line 9333

- **`mc_case_164()`** — line 9336

- **`mc_case_165()`** — line 9339

- **`mc_case_166()`** — line 9342

- **`mc_case_167()`** — line 9345

- **`mc_case_168()`** — line 9348

- **`mc_case_169()`** — line 9351

- **`mc_case_170()`** — line 9354

- **`mc_case_171()`** — line 9357

- **`mc_case_172()`** — line 9360

- **`mc_case_173()`** — line 9363

- **`mc_case_174()`** — line 9366

- **`mc_case_175()`** — line 9369

- **`mc_case_176()`** — line 9372

- **`mc_case_177()`** — line 9375

- **`mc_case_178()`** — line 9378

- **`mc_case_179()`** — line 9381

- **`mc_case_180()`** — line 9384

- **`mc_case_181()`** — line 9387

- **`mc_case_182()`** — line 9390

- **`mc_case_183()`** — line 9393

- **`mc_case_184()`** — line 9396

- **`mc_case_185()`** — line 9399

- **`mc_case_186()`** — line 9402

- **`mc_case_187()`** — line 9405

- **`mc_case_188()`** — line 9408

- **`mc_case_189()`** — line 9411

- **`mc_case_190()`** — line 9414

- **`mc_case_191()`** — line 9417

- **`mc_case_192()`** — line 9420

- **`mc_case_193()`** — line 9423

- **`mc_case_194()`** — line 9426

- **`mc_case_195()`** — line 9429

- **`mc_case_196()`** — line 9432

- **`mc_case_197()`** — line 9435

- **`mc_case_198()`** — line 9438

- **`mc_case_199()`** — line 9441

- **`highpass_sigma_1(image)`** — line 9446

- **`highpass_sigma_2(image)`** — line 9449

- **`highpass_sigma_3(image)`** — line 9452

- **`highpass_sigma_4(image)`** — line 9455

- **`highpass_sigma_5(image)`** — line 9458

- **`highpass_sigma_6(image)`** — line 9461

- **`highpass_sigma_7(image)`** — line 9464

- **`highpass_sigma_8(image)`** — line 9467

- **`highpass_sigma_9(image)`** — line 9470

- **`highpass_sigma_10(image)`** — line 9473

- **`highpass_sigma_11(image)`** — line 9476

- **`highpass_sigma_12(image)`** — line 9479

- **`highpass_sigma_13(image)`** — line 9482

- **`highpass_sigma_14(image)`** — line 9485

- **`highpass_sigma_15(image)`** — line 9488

- **`highpass_sigma_16(image)`** — line 9491

- **`highpass_sigma_17(image)`** — line 9494

- **`highpass_sigma_18(image)`** — line 9497

- **`highpass_sigma_19(image)`** — line 9500

- **`highpass_sigma_20(image)`** — line 9503

- **`highpass_sigma_21(image)`** — line 9506

- **`highpass_sigma_22(image)`** — line 9509

- **`highpass_sigma_23(image)`** — line 9512

- **`highpass_sigma_24(image)`** — line 9515

- **`highpass_sigma_25(image)`** — line 9518

- **`highpass_sigma_26(image)`** — line 9521

- **`highpass_sigma_27(image)`** — line 9524

- **`highpass_sigma_28(image)`** — line 9527

- **`highpass_sigma_29(image)`** — line 9530

- **`highpass_sigma_30(image)`** — line 9533

- **`highpass_sigma_31(image)`** — line 9536

- **`highpass_sigma_32(image)`** — line 9539

- **`highpass_sigma_33(image)`** — line 9542

- **`highpass_sigma_34(image)`** — line 9545

- **`highpass_sigma_35(image)`** — line 9548

- **`highpass_sigma_36(image)`** — line 9551

- **`highpass_sigma_37(image)`** — line 9554

- **`highpass_sigma_38(image)`** — line 9557

- **`highpass_sigma_39(image)`** — line 9560

- **`highpass_sigma_40(image)`** — line 9563

- **`highpass_sigma_41(image)`** — line 9566

- **`highpass_sigma_42(image)`** — line 9569

- **`highpass_sigma_43(image)`** — line 9572

- **`highpass_sigma_44(image)`** — line 9575

- **`highpass_sigma_45(image)`** — line 9578

- **`highpass_sigma_46(image)`** — line 9581

- **`highpass_sigma_47(image)`** — line 9584

- **`highpass_sigma_48(image)`** — line 9587

- **`highpass_sigma_49(image)`** — line 9590

- **`highpass_sigma_50(image)`** — line 9593

- **`highpass_sigma_51(image)`** — line 9596

- **`highpass_sigma_52(image)`** — line 9599

- **`highpass_sigma_53(image)`** — line 9602

- **`highpass_sigma_54(image)`** — line 9605

- **`highpass_sigma_55(image)`** — line 9608

- **`highpass_sigma_56(image)`** — line 9611

- **`highpass_sigma_57(image)`** — line 9614

- **`highpass_sigma_58(image)`** — line 9617

- **`highpass_sigma_59(image)`** — line 9620

- **`highpass_sigma_60(image)`** — line 9623

- **`highpass_sigma_61(image)`** — line 9626

- **`highpass_sigma_62(image)`** — line 9629

- **`highpass_sigma_63(image)`** — line 9632

- **`highpass_sigma_64(image)`** — line 9635

- **`highpass_sigma_65(image)`** — line 9638

- **`highpass_sigma_66(image)`** — line 9641

- **`highpass_sigma_67(image)`** — line 9644

- **`highpass_sigma_68(image)`** — line 9647

- **`highpass_sigma_69(image)`** — line 9650

- **`highpass_sigma_70(image)`** — line 9653

- **`highpass_sigma_71(image)`** — line 9656

- **`highpass_sigma_72(image)`** — line 9659

- **`highpass_sigma_73(image)`** — line 9662

- **`highpass_sigma_74(image)`** — line 9665

- **`highpass_sigma_75(image)`** — line 9668

- **`highpass_sigma_76(image)`** — line 9671

- **`highpass_sigma_77(image)`** — line 9674

- **`highpass_sigma_78(image)`** — line 9677

- **`highpass_sigma_79(image)`** — line 9680

- **`highpass_sigma_80(image)`** — line 9683

- **`highpass_sigma_81(image)`** — line 9686

- **`highpass_sigma_82(image)`** — line 9689

- **`highpass_sigma_83(image)`** — line 9692

- **`highpass_sigma_84(image)`** — line 9695

- **`highpass_sigma_85(image)`** — line 9698

- **`highpass_sigma_86(image)`** — line 9701

- **`highpass_sigma_87(image)`** — line 9704

- **`highpass_sigma_88(image)`** — line 9707

- **`highpass_sigma_89(image)`** — line 9710

- **`highpass_sigma_90(image)`** — line 9713

- **`highpass_sigma_91(image)`** — line 9716

- **`highpass_sigma_92(image)`** — line 9719

- **`highpass_sigma_93(image)`** — line 9722

- **`highpass_sigma_94(image)`** — line 9725

- **`highpass_sigma_95(image)`** — line 9728

- **`highpass_sigma_96(image)`** — line 9731

- **`highpass_sigma_97(image)`** — line 9734

- **`highpass_sigma_98(image)`** — line 9737

- **`highpass_sigma_99(image)`** — line 9740

- **`highpass_sigma_100(image)`** — line 9743

- **`highpass_sigma_101(image)`** — line 9746

- **`highpass_sigma_102(image)`** — line 9749

- **`highpass_sigma_103(image)`** — line 9752

- **`highpass_sigma_104(image)`** — line 9755

- **`highpass_sigma_105(image)`** — line 9758

- **`highpass_sigma_106(image)`** — line 9761

- **`highpass_sigma_107(image)`** — line 9764

- **`highpass_sigma_108(image)`** — line 9767

- **`highpass_sigma_109(image)`** — line 9770

- **`highpass_sigma_110(image)`** — line 9773

- **`highpass_sigma_111(image)`** — line 9776

- **`highpass_sigma_112(image)`** — line 9779

- **`highpass_sigma_113(image)`** — line 9782

- **`highpass_sigma_114(image)`** — line 9785

- **`highpass_sigma_115(image)`** — line 9788

- **`highpass_sigma_116(image)`** — line 9791

- **`highpass_sigma_117(image)`** — line 9794

- **`highpass_sigma_118(image)`** — line 9797

- **`highpass_sigma_119(image)`** — line 9800

- **`highpass_sigma_120(image)`** — line 9803

- **`highpass_sigma_121(image)`** — line 9806

- **`highpass_sigma_122(image)`** — line 9809

- **`highpass_sigma_123(image)`** — line 9812

- **`highpass_sigma_124(image)`** — line 9815

- **`highpass_sigma_125(image)`** — line 9818

- **`highpass_sigma_126(image)`** — line 9821

- **`highpass_sigma_127(image)`** — line 9824

- **`highpass_sigma_128(image)`** — line 9827

- **`highpass_sigma_129(image)`** — line 9830

- **`highpass_sigma_130(image)`** — line 9833

- **`highpass_sigma_131(image)`** — line 9836

- **`highpass_sigma_132(image)`** — line 9839

- **`highpass_sigma_133(image)`** — line 9842

- **`highpass_sigma_134(image)`** — line 9845

- **`highpass_sigma_135(image)`** — line 9848

- **`highpass_sigma_136(image)`** — line 9851

- **`highpass_sigma_137(image)`** — line 9854

- **`highpass_sigma_138(image)`** — line 9857

- **`highpass_sigma_139(image)`** — line 9860

- **`highpass_sigma_140(image)`** — line 9863

- **`highpass_sigma_141(image)`** — line 9866

- **`highpass_sigma_142(image)`** — line 9869

- **`highpass_sigma_143(image)`** — line 9872

- **`highpass_sigma_144(image)`** — line 9875

- **`highpass_sigma_145(image)`** — line 9878

- **`highpass_sigma_146(image)`** — line 9881

- **`highpass_sigma_147(image)`** — line 9884

- **`highpass_sigma_148(image)`** — line 9887

- **`highpass_sigma_149(image)`** — line 9890

- **`highpass_sigma_150(image)`** — line 9893

- **`highpass_sigma_151(image)`** — line 9896

- **`highpass_sigma_152(image)`** — line 9899

- **`highpass_sigma_153(image)`** — line 9902

- **`highpass_sigma_154(image)`** — line 9905

- **`highpass_sigma_155(image)`** — line 9908

- **`highpass_sigma_156(image)`** — line 9911

- **`highpass_sigma_157(image)`** — line 9914

- **`highpass_sigma_158(image)`** — line 9917

- **`highpass_sigma_159(image)`** — line 9920

- **`highpass_sigma_160(image)`** — line 9923

- **`highpass_sigma_161(image)`** — line 9926

- **`highpass_sigma_162(image)`** — line 9929

- **`highpass_sigma_163(image)`** — line 9932

- **`highpass_sigma_164(image)`** — line 9935

- **`highpass_sigma_165(image)`** — line 9938

- **`highpass_sigma_166(image)`** — line 9941

- **`highpass_sigma_167(image)`** — line 9944

- **`highpass_sigma_168(image)`** — line 9947

- **`highpass_sigma_169(image)`** — line 9950

- **`highpass_sigma_170(image)`** — line 9953

- **`highpass_sigma_171(image)`** — line 9956

- **`highpass_sigma_172(image)`** — line 9959

- **`highpass_sigma_173(image)`** — line 9962

- **`highpass_sigma_174(image)`** — line 9965

- **`highpass_sigma_175(image)`** — line 9968

- **`highpass_sigma_176(image)`** — line 9971

- **`highpass_sigma_177(image)`** — line 9974

- **`highpass_sigma_178(image)`** — line 9977

- **`highpass_sigma_179(image)`** — line 9980

- **`highpass_sigma_180(image)`** — line 9983

- **`highpass_sigma_181(image)`** — line 9986

- **`highpass_sigma_182(image)`** — line 9989

- **`highpass_sigma_183(image)`** — line 9992

- **`highpass_sigma_184(image)`** — line 9995

- **`highpass_sigma_185(image)`** — line 9998

- **`highpass_sigma_186(image)`** — line 10001

- **`highpass_sigma_187(image)`** — line 10004

- **`highpass_sigma_188(image)`** — line 10007

- **`highpass_sigma_189(image)`** — line 10010

- **`highpass_sigma_190(image)`** — line 10013

- **`highpass_sigma_191(image)`** — line 10016

- **`highpass_sigma_192(image)`** — line 10019

- **`highpass_sigma_193(image)`** — line 10022

- **`highpass_sigma_194(image)`** — line 10025

- **`highpass_sigma_195(image)`** — line 10028

- **`highpass_sigma_196(image)`** — line 10031

- **`highpass_sigma_197(image)`** — line 10034

- **`highpass_sigma_198(image)`** — line 10037

- **`highpass_sigma_199(image)`** — line 10040

- **`highpass_sigma_200(image)`** — line 10043

- **`normalize_clip_lo1(image)`** — line 10046

- **`normalize_clip_lo2(image)`** — line 10049

- **`normalize_clip_lo3(image)`** — line 10052

- **`normalize_clip_lo4(image)`** — line 10055

- **`normalize_clip_lo5(image)`** — line 10058

- **`normalize_clip_lo6(image)`** — line 10061

- **`normalize_clip_lo7(image)`** — line 10064

- **`normalize_clip_lo8(image)`** — line 10067

- **`normalize_clip_lo9(image)`** — line 10070

- **`normalize_clip_lo10(image)`** — line 10073

- **`normalize_clip_lo11(image)`** — line 10076

- **`normalize_clip_lo12(image)`** — line 10079

- **`normalize_clip_lo13(image)`** — line 10082

- **`normalize_clip_lo14(image)`** — line 10085

- **`normalize_clip_lo15(image)`** — line 10088

- **`normalize_clip_lo16(image)`** — line 10091

- **`normalize_clip_lo17(image)`** — line 10094

- **`normalize_clip_lo18(image)`** — line 10097

- **`normalize_clip_lo19(image)`** — line 10100

- **`normalize_clip_lo20(image)`** — line 10103

- **`normalize_clip_lo21(image)`** — line 10106

- **`normalize_clip_lo22(image)`** — line 10109

- **`normalize_clip_lo23(image)`** — line 10112

- **`normalize_clip_lo24(image)`** — line 10115

- **`normalize_clip_lo25(image)`** — line 10118

- **`normalize_clip_lo26(image)`** — line 10121

- **`normalize_clip_lo27(image)`** — line 10124

- **`normalize_clip_lo28(image)`** — line 10127

- **`normalize_clip_lo29(image)`** — line 10130

- **`normalize_clip_lo30(image)`** — line 10133

- **`normalize_clip_lo31(image)`** — line 10136

- **`normalize_clip_lo32(image)`** — line 10139

- **`normalize_clip_lo33(image)`** — line 10142

- **`normalize_clip_lo34(image)`** — line 10145

- **`normalize_clip_lo35(image)`** — line 10148

- **`normalize_clip_lo36(image)`** — line 10151

- **`normalize_clip_lo37(image)`** — line 10154

- **`normalize_clip_lo38(image)`** — line 10157

- **`normalize_clip_lo39(image)`** — line 10160

- **`normalize_clip_lo40(image)`** — line 10163

- **`normalize_clip_lo41(image)`** — line 10166

- **`normalize_clip_lo42(image)`** — line 10169

- **`normalize_clip_lo43(image)`** — line 10172

- **`normalize_clip_lo44(image)`** — line 10175

- **`normalize_clip_lo45(image)`** — line 10178

- **`normalize_clip_lo46(image)`** — line 10181

- **`normalize_clip_lo47(image)`** — line 10184

- **`normalize_clip_lo48(image)`** — line 10187

- **`normalize_clip_lo49(image)`** — line 10190

- **`normalize_clip_lo50(image)`** — line 10193

- **`normalize_clip_lo51(image)`** — line 10196

- **`normalize_clip_lo52(image)`** — line 10199

- **`normalize_clip_lo53(image)`** — line 10202

- **`normalize_clip_lo54(image)`** — line 10205

- **`normalize_clip_lo55(image)`** — line 10208

- **`normalize_clip_lo56(image)`** — line 10211

- **`normalize_clip_lo57(image)`** — line 10214

- **`normalize_clip_lo58(image)`** — line 10217

- **`normalize_clip_lo59(image)`** — line 10220

- **`normalize_clip_lo60(image)`** — line 10223

- **`normalize_clip_lo61(image)`** — line 10226

- **`normalize_clip_lo62(image)`** — line 10229

- **`normalize_clip_lo63(image)`** — line 10232

- **`normalize_clip_lo64(image)`** — line 10235

- **`normalize_clip_lo65(image)`** — line 10238

- **`normalize_clip_lo66(image)`** — line 10241

- **`normalize_clip_lo67(image)`** — line 10244

- **`normalize_clip_lo68(image)`** — line 10247

- **`normalize_clip_lo69(image)`** — line 10250

- **`normalize_clip_lo70(image)`** — line 10253

- **`normalize_clip_lo71(image)`** — line 10256

- **`normalize_clip_lo72(image)`** — line 10259

- **`normalize_clip_lo73(image)`** — line 10262

- **`normalize_clip_lo74(image)`** — line 10265

- **`normalize_clip_lo75(image)`** — line 10268

- **`normalize_clip_lo76(image)`** — line 10271

- **`normalize_clip_lo77(image)`** — line 10274

- **`normalize_clip_lo78(image)`** — line 10277

- **`normalize_clip_lo79(image)`** — line 10280

- **`normalize_clip_lo80(image)`** — line 10283

- **`normalize_clip_lo81(image)`** — line 10286

- **`normalize_clip_lo82(image)`** — line 10289

- **`normalize_clip_lo83(image)`** — line 10292

- **`normalize_clip_lo84(image)`** — line 10295

- **`normalize_clip_lo85(image)`** — line 10298

- **`normalize_clip_lo86(image)`** — line 10301

- **`normalize_clip_lo87(image)`** — line 10304

- **`normalize_clip_lo88(image)`** — line 10307

- **`normalize_clip_lo89(image)`** — line 10310

- **`normalize_clip_lo90(image)`** — line 10313

- **`normalize_clip_lo91(image)`** — line 10316

- **`normalize_clip_lo92(image)`** — line 10319

- **`normalize_clip_lo93(image)`** — line 10322

- **`normalize_clip_lo94(image)`** — line 10325

- **`normalize_clip_lo95(image)`** — line 10328

- **`normalize_clip_lo96(image)`** — line 10331

- **`normalize_clip_lo97(image)`** — line 10334

- **`normalize_clip_lo98(image)`** — line 10337

- **`normalize_clip_lo99(image)`** — line 10340

- **`normalize_clip_lo100(image)`** — line 10343

This module constitutes the imaging and legacy science monolith. It encompasses ingestion of
planetary sequences, quality scoring, stacking pathways, navigation utilities, and export
helpers. The primary interactive reduction path for still frames is orchestrated externally
through desktop_pipeline, which invokes precision and research-grade components; the
monolith remains available for imaging branches and command-line measure workflows.

Optional imaging branches may preprocess multi-frame inputs before metrology. Absolute
geometric products for archival science still depend on mid-exposure time, ephemeris
provenance, and the publication definition established above.

# 11. Reporting, Paths, and Operational Infrastructure

### 11.`result_report.py` (781 lines)

Module documentation as implemented in source:

Human-readable full result reports for GRS Observatory.  Goal: not a wall of pure JSON —
clear YOUR numbers vs NASA, differences, truth recovery, error bars, tips. Long is fine;
garbage is not.

#### Functions

- **`_f(v, d)`** — line 40

- **`_s(v)`** — line 52

- **`_line(label, value, unit, width)`** — line 56

- **`_section(title, char)`** — line 61

- **`_box(title, rows)`** — line 66

- **`_pull_measured(pkg)`** — line 85

Best-effort YOUR answer from any package shape.  Prefer VLBI/research-grade pipeline bias-
corrected over SOTA gold primary. SOTA is reported in its own section; mixing it into YOUR
vs NASA confused users.

- **`_format_nasa_block(nasa)`** — line 169

- **`_format_truth_block(pkg)`** — line 307

- **`format_human_report(package)`** — line 399

Long, clear, human report. Prefer this over dumping raw JSON alone.

- **`write_human_report(path, package)`** — line 770

- **`format_nasa_txt(comp_dict)`** — line 778

Standalone NASA comparison text file body.

### 11.`verbose_log.py` (55 lines)

Module documentation as implemented in source:

Thread-safe console log for the web UI.

#### Classes

**ConsoleLog** (source line 12)

Methods:

- `__init__(max_lines)` — line 13
- `clear()` — line 19
- `log(message, level, verbose_only)` — line 24
- `info(msg, verbose_only)` — line 34
- `warn(msg, verbose_only)` — line 37
- `error(msg, verbose_only)` — line 40
- `ok(msg, verbose_only)` — line 43
- `debug(msg)` — line 46
- `since(after_id)` — line 49

### 11.`paths.py` (187 lines)

Module documentation as implemented in source:

```
Central paths for GRS Observatory (works on every device)
=========================================================

Resolves:
  - CODE_DIR   — Python modules (may be PyInstaller extract dir)
  - DATA_DIR   — writable data (outputs, logs, license, owner access)
  - MODEL_DIR  — SPIRE-Net weights (bundled + copied into DATA on first run)
  - OWNER_DIR  — usage logs the group owner can collect

Environment overrides (owner/group deploy):
  GRS_DATA_DIR        writable root
  GRS_MODEL_DIR       force model directory
  GRS_OWNER_LOG_DIR   shared folder (Dropbox/NAS) where usage is mirrored
  GRS_USER_NAME       display name written into usage logs
```

#### Functions

- **`_frozen()`** — line 27

- **`code_dir()`** — line 31

- **`data_dir()`** — line 37

- **`model_dir()`** — line 57

- **`bundled_model_dir()`** — line 69

Read-only models shipped with the code/bundle.

- **`ensure_models_present()`** — line 74

Copy bundled SPIRE-Net weights into DATA models/ if missing. So every device starts with the
same network without re-training.  Ships with the app: app/models/spire_net_weights.npz (+
meta). Falls back to spire_net_weights.GOOD.npz if primary missing.

- **`owner_log_dir()`** — line 132

Local owner/access logs. Always written. If GRS_OWNER_LOG_DIR is set (shared Drive/NAS),
also mirrored there.

- **`owner_shared_dir()`** — line 142

- **`outputs_dir()`** — line 158

- **`ensure_tree()`** — line 164

Create standard folders on every device.

### 11.`ram_ssd.py` (123 lines)

Module documentation as implemented in source:

16 GB RAM budget manager + SSD memmap cache.  Target machine: 16 GB unified RAM. Keep peak
working set under ~10 GB so the OS stays responsive. Large arrays spill to SSD under
app/ssd_cache (project disk).

#### Functions

- **`bytes_gb(n)`** — line 32

- **`estimate_rgb_gb(w, h, dtype)`** — line 36

- **`choose_max_resolution(prefer)`** — line 41

Pick largest safe resolution for 16 GB. 16K float32 RGB ~ 1.5 GB raw + temps → tight. 8K ~
0.4 GB → comfortable.

- **`ssd_temp_path(suffix)`** — line 73

- **`memmap_zeros(shape, dtype)`** — line 78

- **`array_to_ssd(arr)`** — line 85

- **`load_ssd(path)`** — line 91

- **`free_memory()`** — line 95

- **`cleanup_ssd_cache(max_age_sec)`** — line 99

- **`recommend_mc_iterations(resolution_mp)`** — line 114

Fewer MC iters at huge res to stay within RAM/time.

### 11.`group_access.py` (187 lines)

Module documentation as implemented in source:

```
Usage log for every person who runs GRS Observatory
===================================================

No password. Every major action is appended so the owner can see who used
the app and what they ran.

Where logs go
-------------
1) Always:  <DATA>/owner_access/usage.jsonl
2) Also:    $GRS_OWNER_LOG_DIR/usage_<machine>.jsonl   (if set)
3) Also:    <project>/OWNER_SHARED_LOGS/  if that folder exists

Set identity (optional):
  export GRS_USER_NAME="Hayden"
  export GRS_OWNER_LOG_DIR="/Users/you/Dropbox/GRS_OwnerLogs"

Owner tools:
  python3 cli.py owner summary
  python3 cli.py owner tail
```

#### Functions

- **`_user_name()`** — line 38

- **`device_record()`** — line 47

- **`log_event(action, detail)`** — line 63

Append one usage event (best-effort, never crashes the app).

- **`read_events(limit, path)`** — line 125

- **`summarize(limit)`** — line 144

- **`logging_enabled_message()`** — line 182

### 11.`accounts.py` (356 lines)

Module documentation as implemented in source:

User identity + owner data log for GRS Observatory
=================================================  Optional name/email tag so owner logs
show who ran the app. No password required (self-use / group). Identity is optional.  Owner
data file (everybody's activity):   data_dir/owner_access/EVERY_USER_DATA.jsonl   +
OWNER_SHARED_LOGS/EVERY_USER_DATA.jsonl if that folder exists  Each line = one event (login,
logout, job, account create) with user email.

#### Classes

**AccountSession** (source line 43)

Methods:

- `to_dict()` — line 50

#### Functions

- **`require_gmail()`** — line 38

- **`accounts_path()`** — line 54

- **`session_path()`** — line 58

- **`everyone_data_path()`** — line 62

Central file where every user's events are appended (owner view).

- **`_load()`** — line 69

- **`_save(db)`** — line 79

- **`_hash_password(password, salt)`** — line 87

- **`_verify_password(password, salt_hex, hash_hex)`** — line 93

- **`_device()`** — line 102

- **`uuid_node()`** — line 116

- **`log_user_data(action, email, detail)`** — line 121

Append one event for ANY user to the owner data file(s). Never stores raw passwords.

- **`validate_email(email)`** — line 178

- **`create_account(email, password, display_name)`** — line 187

Register with email/name. Password optional (ignored for open group use).

- **`login(email, password)`** — line 229

Open identity for logging. Password not required.

- **`logout()`** — line 270

- **`current_session()`** — line 287

- **`require_login()`** — line 304

Jobs need an active Gmail or admin session. Default ON (passcode always).

- **`google_oauth_available()`** — line 309

- **`google_oauth_instructions()`** — line 313

- **`list_accounts_summary()`** — line 329

- **`owner_export_users_file()`** — line 344

Write a readable summary of all accounts for the owner.

### 11.`admin_console.py` (183 lines)

Module documentation as implemented in source:

```
Owner / Admin console for GRS Observatory (group oversight)
===========================================================

No password. Anyone running the app on this machine can open the owner view;
usage from every device is written to OWNER_SHARED_LOGS / owner_access so you
can see who ran what.

Admin can view:
  • Registered accounts (email, name, last seen) — no passwords stored readable
  • EVERY_USER_DATA.jsonl activity log
  • usage.jsonl device/job log
  • Paths to job outputs / previews on this machine
```

#### Classes

**AdminSession** (source line 35)

Methods:

- `to_dict()` — line 40

#### Functions

- **`admin_session_path()`** — line 30

- **`admin_login(username, password)`** — line 44

Open owner view — no password required (self-use / group deploy).

- **`admin_logout()`** — line 69

- **`admin_current()`** — line 83

- **`list_accounts()`** — line 99

- **`usage_tail(n)`** — line 117

- **`owner_summary()`** — line 148

Everything you need to see who used the app.

- **`write_owner_summary(path)`** — line 178

### 11.`license_manager.py` (455 lines)

Module documentation as implemented in source:

```
======================================================================

Key format:
  GRS-1-<PLAN>-<PAYLOAD>-<SIG4>

  PLAN:    PERS | PRO | SITE | TRIAL
  PAYLOAD: base32-ish payload (customer id + expiry days code)
  SIG4:    first 4 groups of HMAC-SHA256 over canonical string

Vendor secret:
  Default secret is for evaluation only — change before selling.

Machine binding (Pro/Site optional):
  If bind=True, payload includes a short machine fingerprint.

Storage:
  <data_dir>/license.json

This is a real, usable license gate for a paid desktop product. Rotate the
secret for production; keep a private generator on your sales machine only.
```

#### Classes

**LicenseStatus** (source line 165)

Methods:

- `__post_init__()` — line 178
- `to_dict()` — line 182

#### Functions

- **`using_default_secret()`** — line 124

- **`_secret()`** — line 129

- **`machine_fingerprint()`** — line 134

Stable short machine id (not cryptographically private).

- **`_b32ish(data)`** — line 145

- **`_sign(canonical)`** — line 158

- **`generate_key(plan, customer, days, bind_machine, machine_id)`** — line 186

Vendor-side key generation.  days: 0 = no expiry; else valid for N days from generation.
bind_machine: if True, key only works on machine_id (default: this machine).

- **`parse_and_verify(key)`** — line 232

Return (ok, fields, message).

- **`license_path(data_dir)`** — line 295

- **`save_license(data_dir, key, meta)`** — line 299

- **`load_status(data_dir)`** — line 316

- **`status_from_fields(ok, fields, msg)`** — line 343

- **`require_feature(data_dir, feature)`** — line 378

Real gate used by desktop / CLI / server before expensive jobs.

- **`assert_feature(data_dir, feature)`** — line 415

- **`vendor_generate_batch(plan, customers, days, bind)`** — line 421

### 11.`security_hard.py` (218 lines)

Module documentation as implemented in source:

GRS Observatory — security hardening (practical, not magic)  Blocks common attack patterns
relevant to this local Flask + desktop product:    OWASP-style: path traversal, unrestricted
upload, injection via paths,   SSRF-ish arbitrary process paths, DoS flood (basic rate
limit),   secret file exfil, host-header abuse, dangerous filenames.  Honest limit: no
software can block "all hacking methods." This reduces the attack surface of *this* app when
the web UI is running.

#### Classes

**SecurityError** (source line 54)

Raised when a request is blocked as unsafe.

#### Functions

- **`rate_limit_ok(client_key)`** — line 65

Return False if client exceeded request budget.

- **`sanitize_filename(name)`** — line 81

Strip path components and dangerous chars from an upload name.

- **`has_traversal(s)`** — line 97

- **`safe_resolve_under(path)`** — line 110

Resolve path and require it lives under one of allowed_roots. Raises SecurityError
otherwise.

- **`safe_upload_extension(filename)`** — line 137

- **`assert_safe_process_path(path)`** — line 144

Only process images that live under uploads/outputs (or explicit roots).

- **`host_allowed(host_header)`** — line 158

Block obvious Host header abuse when bound to localhost.

- **`strip_control_chars(s, max_len)`** — line 173

- **`security_headers()`** — line 178

- **`data_roots(app_dir)`** — line 198

## 11.1 Archival job contents

- publish.json / publish.txt — designated GS-MAP (or GS-BARY) product
- winjupos_twin.* — twin definitions and limb-outline sensitivity
- gold_standard.* — full named-definition set
- all_methods.json — multi-estimator catalog (scatter)
- research_grade.* / vlbi_metrology.* — optical metrology stack
- pro_ephemeris.* — central meridian, distance, and provenance
- nasa_comparison.* — geometric context and schematic trend fields
- source_preview.png — reduced-size view of the input
- Human-readable FULL_REPORT text

# 12. Relation to Interactive Planetary Measurement Practice

Interactive reduction with WinJUPOS rests on mid-exposure timing, ephemeris-consistent
central meridians, deliberate limb outlines, and an explicit choice of feature definition.
GRS Observatory encodes the same elements: fail-closed time handling; override, table,
SPICE, and Horizons geometry with recorded source tags; isophotal limb probes that quantify
outline-size systematics; and a fixed published definition (GS-MAP). Automation accelerates
batch work, packages provenance, and supports synthetic regression; interactive software
remains the natural reference for limb judgment on difficult nights and for human validation
of the automated center.

A scientifically meaningful comparison between the two therefore requires identical input
frames, identical mid-exposure epochs, identical central meridians, and identical
morphological definitions. Discrepancies that mix edge picks with core picks, or analytic
central meridians with interactive ones, are definitional rather than indicative of a single
scalar 'error of the code.'

# 13. Limitations and Extensions

## 13.1 Intrinsic limitations

- Extended cloud features under atmospheric seeing possess higher practical floors than compact-source interferometric regimes.
- Multi-estimator catalogs are strongly correlated; dispersion reflects shared masks and priors as much as independent information.
- Synthetic morphology is smoother and more controlled than natural GRS filamentation and South Equatorial Belt complexity.
- Near-limb foreshortening and competing dark structures remain difficult for fully automatic centers.
- Ephemeris text parsers must remain robust to service format drift.
- The imaging monolith's size increases maintenance cost relative to a fully factored package layout.

## 13.2 Directions for development

- Multi-frame correlation imaging velocimetry in the sense of the planetary atmosphere literature, requiring image pairs or cubes rather than single-frame analogs alone
- Expanded real-image validation sets against interactive measures and published monitoring series
- Automated regression tests for map scale, time refusal, and publication policy invariants
- Continued modularization of the imaging monolith

# 14. Experimental Results

This section is reserved for quantitative evaluation obtained from executed reductions.
Suggested contents include synthetic recovery distributions, real-image comparisons against
interactive measures under matched time and central meridian, limb-outline sensitivity on
soft disks, size measures in context of the historical record, and documented failure modes.
Tables below are templates.

## 14.1 Synthetic recovery

| Experiment | N | Median sky error (arcsec) | p95 (arcsec) | Maximum (arcsec) | Notes |
|------------|--:|--------------------------:|-------------:|-----------------:|-------|
| | | | | | |

## 14.2 Real images versus interactive measures

| Frame / UTC | CM source | Published λ_III | Interactive λ_III | Δλ (deg) | Δsky (arcsec) |
|-------------|-----------|----------------:|------------------:|---------:|--------------:|
| | | | | | |

## 14.3 Limb-outline sensitivity

| Frame | Outer−inner Δλ (deg) | Sky spread (arcsec) | Radius spread (px) |
|-------|---------------------:|--------------------:|-------------------:|
| | | | |

## 14.4 Discussion

Interpretation of the completed tables should address domain gap between synthetic and
natural imagery, the role of definition choice, conditions under which interactive limb
adjustment remains preferable, and the adequacy of reported uncertainty relative to external
differences.

# 15. Conclusion

GRS Observatory implements an end-to-end automated pathway from planetary imagery to System
III coordinates of the Great Red Spot. Its distinctive engineering commitments are explicit
mid-exposure time control, multi-source ephemerides with provenance, a unified oriented map
geometry, a fixed publication definition (GS-MAP), quantified sensitivity to limb outline
and morphological definition, multi-estimator scatter diagnostics, synthetic verification
machinery, optional learned assistance with distributed weights, and archival job packages
suitable for audit. The system is best understood as encoding the geometric discipline of
careful planetary measurement in software form, augmented by automation and testing
infrastructure. Quantitative performance follows from experiments recorded in Section 14.

# Appendix A. Module Inventory

| # | File | Lines | Classes | Functions | Role |
|--:|------|------:|--------:|----------:|------|
| 1 | accounts.py | 356 | 1 | 21 | Optional operator identity |
| 2 | admin_console.py | 183 | 1 | 8 | Owner activity summary |
| 3 | ai_hard_cases.py | 399 | 1 | 6 | Difficult-case assistance |
| 4 | all_methods.py | 995 | 1 | 33 | Primary multi-estimator suite |
| 5 | all_methods_extra.py | 905 | 0 | 33 | Extended estimators |
| 6 | batch_prove.py | 401 | 0 | 3 | Batch synthetic certification |
| 7 | cli.py | 280 | 0 | 1 | Command-line interface |
| 8 | desktop_app.py | 1850 | 2 | 3 | Native graphical interface |
| 9 | desktop_pipeline.py | 809 | 0 | 11 | Process, synthetic, and factory orchestration |
| 10 | ephemeris_pro.py | 873 | 1 | 14 | Ephemeris fusion chain |
| 11 | fits_time.py | 177 | 0 | 5 | FITS mid-exposure extraction |
| 12 | gold_standard.py | 990 | 2 | 16 | Named measurement definitions |
| 13 | group_access.py | 187 | 0 | 6 | Usage logging |
| 14 | grs_complete_system.py | 10350 | 47 | 1806 | Imaging and legacy science monolith |
| 15 | hard_synth_suite.py | 384 | 2 | 5 | Stress calibration suite |
| 16 | license_manager.py | 455 | 1 | 14 | License key management |
| 17 | limb_validation.py | 124 | 0 | 3 | Near-limb validation harness |
| 18 | multi_epoch.py | 494 | 2 | 9 | Multi-epoch differentials |
| 19 | nasa_compare.py | 262 | 1 | 5 | Horizons geometry and contextual models |
| 20 | nn_grs.py | 1694 | 1 | 42 | SPIRE-Net convolutional model |
| 21 | paths.py | 187 | 0 | 10 | Portable paths and model installation |
| 22 | precision_engine.py | 1065 | 2 | 23 | Limb, map, template, moment, Monte Carlo |
| 23 | product_core.py | 341 | 1 | 6 | Unified product API and versioning |
| 24 | publish_primary.py | 267 | 0 | 4 | Publication product selection |
| 25 | ram_ssd.py | 123 | 0 | 10 | Memory and cache budgeting |
| 26 | research_grade.py | 740 | 3 | 9 | Research reduction entry |
| 27 | result_report.py | 781 | 0 | 11 | Human-readable full reports |
| 28 | security_hard.py | 218 | 1 | 10 | Local service hardening |
| 29 | server.py | 1664 | 0 | 34 | Local HTTP API and browser UI |
| 30 | sota_accuracy.py | 1192 | 1 | 16 | Robust multi-method consensus |
| 31 | spice_auto.py | 540 | 2 | 12 | SPICE kernels and geometry |
| 32 | synthetic_hq.py | 682 | 1 | 12 | Synthetic planet generator |
| 33 | verbose_log.py | 55 | 1 | 0 | Console logging |
| 34 | vlbi_metrology.py | 1747 | 4 | 26 | Optical metrology stack and error budget |
| 35 | winjupos_twin.py | 577 | 2 | 6 | Twin definitions and limb sensitivity |

# Appendix B. Classes and Functions

The following catalog is produced by static analysis of each module under app/. It serves as
an index into the implementation.

## B.accounts

File app/accounts.py — 356 lines

Classes:
- AccountSession (L43): to_dict

Top-level functions:

- require_gmail (L38)
- accounts_path (L54)
- session_path (L58)
- everyone_data_path (L62)
- _load (L69)
- _save (L79)
- _hash_password (L87)
- _verify_password (L93)
- _device (L102)
- uuid_node (L116)
- log_user_data (L121)
- validate_email (L178)
- create_account (L187)
- login (L229)
- logout (L270)
- current_session (L287)
- require_login (L304)
- google_oauth_available (L309)
- google_oauth_instructions (L313)
- list_accounts_summary (L329)
- owner_export_users_file (L344)

## B.admin_console

File app/admin_console.py — 183 lines

Classes:
- AdminSession (L35): to_dict

Top-level functions:

- admin_session_path (L30)
- admin_login (L44)
- admin_logout (L69)
- admin_current (L83)
- list_accounts (L99)
- usage_tail (L117)
- owner_summary (L148)
- write_owner_summary (L178)

## B.ai_hard_cases

File app/ai_hard_cases.py — 399 lines

Classes:
- HardCaseAIResult (L25): to_dict

Top-level functions:

- estimate_image_difficulty (L43)
- estimate_method_difficulty (L95)
- compute_difficulty (L173)
- _nn_prior (L197)
- assist_hard_case (L206)
- apply_hard_case_ai_to_package (L326)

## B.all_methods

File app/all_methods.py — 995 lines

Classes:
- MethodHit (L44): to_dict

Top-level functions:

- _cyl_lon_lat_grids (L64)
- _mono_cyl (L72)
- _gauss (L79)
- _band_slice (L87)
- _hit_from_map_xy (L97)
- _fail (L127)
- m_map_dark (L135)
- m_template (L144)
- m_bary_image (L153)
- m_engine (L162)
- m_multiscale_ncc (L171)
- m_perc_dark_bary (L198)
- m_otsu_bary (L217)
- m_hp_peak (L247)
- m_bandpass_bary (L271)
- m_log_blob (L290)
- m_proj_1d (L309)
- m_lat_track (L334)
- m_phase_corr (L357)
- m_isophote_center (L386)
- m_quad_moment (L416)
- m_morph_bary (L441)
- m_adaptive_bary (L466)
- m_seed_grow (L482)
- m_sobel_ring (L520)
- m_flux_powers (L543)
- m_rgb_methods (L565)
- m_edges_extent (L618)
- m_symmetry (L672)
- m_min_pixel (L695)
- _circular_mean_lon (L709)
- ensemble_from_hits (L714)
- run_all_methods (L861)

## B.all_methods_extra

File app/all_methods_extra.py — 905 lines

Top-level functions:

- _band_roi (L59)
- _dark_mask (L67)
- _subpixel_argmin (L75)
- _subpixel_argmax (L87)
- m_fwhm_lon (L93)
- m_fwhm_lat (L117)
- m_profile_gaussian_fit (L141)
- m_multi_isophote (L164)
- m_box_extent (L194)
- m_geometric_median (L228)
- m_pca_ellipse (L250)
- m_convex_hull_c (L278)
- m_distance_transform_peak (L309)
- m_mean_shift (L333)
- m_ransac_ellipse (L356)
- m_civ_window_ncc (L393)
- m_sad_ssd_templates (L435)
- m_spomf (L467)
- m_bottom_hat (L486)
- m_tophat_inv (L509)
- m_watershed (L534)
- m_structure_tensor (L577)
- m_radial_symmetry (L606)
- m_hu_moments (L644)
- m_percentile_ladder (L671)
- m_bilateral_bary (L705)
- m_unsharp_peak (L730)
- m_rolling_ball (L739)
- m_kde_mode (L761)
- m_gmm2 (L779)
- m_ring_template (L810)
- m_min_enclosing_circle (L829)
- run_extra_methods (L861)

## B.batch_prove

File app/batch_prove.py — 401 lines

Top-level functions:

- _percentile (L45)
- run_one (L59)
- main (L207)

## B.cli

File app/cli.py — 280 lines

Top-level functions:

- main (L21)

## B.desktop_app

File app/desktop_app.py — 1850 lines

Classes:
- LogBridge (L301): __init__, poll
- GRSDesktopApp (L315): __init__, _build_menu, _license_show, _license_activate, _license_copy_machine, _manual_path, _open_manual, _about, _refresh_license_badge, _build_style, _build_ui, _section, _labeled_entry, _labeled_combo, _check, _action_btn, _set_busy, _log_ui, _results, _update_metrics, _show_preview, _tick, _gate, _run_bg, _mc, _inj, _float_opt, _aperture, _time_error, on_clear, on_open_outputs, on_save_results, on_open_file, on_winjupos, on_synthetic, on_synthetic_only, on_process, on_ephemeris, on_multi, on_hard, on_factory, _nn_epochs, _nn_samples, _nn_lr, _nn_hours, _nn_cache, on_nn_stop, on_nn_train

Top-level functions:

- app_base_dir (L22)
- bundle_code_dir (L34)
- main (L1840)

GRSDesktopApp._section: Section title: black bold on light grey strip.

GRSDesktopApp._check: Checkbox with black text + grey description under it.

GRSDesktopApp._action_btn: Full-width button with grey description underneath (no ? icons).

GRSDesktopApp._gate: Feature gate (fail-open for free local use).

GRSDesktopApp.on_synthetic_only: Generate image only — no metrology (clear separate button).

## B.desktop_pipeline

File app/desktop_pipeline.py — 809 lines

Top-level functions:

- array_to_rgb_u8 (L34)
- write_image_preview (L85)
- next_run_id (L121)
- metrics_filename_suffix (L143)
- _load_image (L166)
- _try_imaging_pipeline (L194)
- format_full_report (L221)
- write_package_reports (L227)
- run_synthetic_full (L245)
- run_process_full (L469)
- run_factory_night_full (L702)

## B.ephemeris_pro

File app/ephemeris_pro.py — 873 lines

Classes:
- ProEphemeris (L87): to_dict, to_vlbi_ephemeris_state

Top-level functions:

- wrap_deg (L48)
- wrap_diff (L52)
- _ssl_context (L56)
- parse_time (L67)
- analytical_geometry (L139)
- fetch_horizons_full (L165)
- parse_horizons_observer_text (L245)
- load_winjupos_table (L416)
- interpolate_winjupos_cm (L469)
- save_example_winjupos_template (L524)
- try_spice_geometry (L539)
- _try_spice_geometry_legacy (L580)
- resolve_pro_ephemeris (L637)
- write_ephemeris_report (L850)

ProEphemeris.to_vlbi_ephemeris_state: Bridge to vlbi_metrology.EphemerisState without circular import at module load.

## B.fits_time

File app/fits_time.py — 177 lines

Top-level functions:

- _parse_isoish (L19)
- _hdr_get (L44)
- extract_fits_mid_time (L70)
- require_observation_time (L137)
- format_utc (L175)

## B.gold_standard

File app/gold_standard.py — 990 lines

Classes:
- GoldMeasure (L106): to_dict
- GoldStandardResult (L121): to_dict

Top-level functions:

- _wrap_lon (L159)
- _cyl_axes (L163)
- _grs_band_mask_cyl (L178)
- measure_gs_bary (L183)
- measure_gs_map (L196)
- measure_gs_tmpl (L209)
- measure_gs_engine (L222)
- _dark_mask_cyl (L241)
- measure_gs_oval_and_edges (L330)
- compare_to_winjupos_manual (L439)
- _pick_primary (L491)
- _scatter (L499)
- run_gold_standard (L523)
- format_gold_report (L736)
- write_gold_standard_bundle (L837)
- attach_gold_to_package (L882)

## B.group_access

File app/group_access.py — 187 lines

Top-level functions:

- _user_name (L38)
- device_record (L47)
- log_event (L63)
- read_events (L125)
- summarize (L144)
- logging_enabled_message (L182)

## B.grs_complete_system

File app/grs_complete_system.py — 10350 lines

Classes:
- PhysicalConstants (L91): 
- PipelineMode (L121): 
- FilterName (L127): 
- QualityMetric (L132): 
- StackMethod (L142): 
- RestoreMethod (L147): 
- AlignMode (L152): 
- SegmentMethod (L156): 
- SmootherKind (L164): 
- LimbMethod (L168): 
- DefinitionId (L174): 
- GRSPipelineError (L183): 
- IngestError (L184): 
- QCError (L185): 
- CalibrationError (L186): 
- AlignmentError (L187): 
- NavigationError (L188): 
- MeasurementError (L189): 
- ConfigError (L190): 
- DependencyError (L191): 
- StageTimer (L203): __init__, __enter__, __exit__
- FrameMeta (L457): to_dict
- VideoCube (L482): n_frames, shape_hw
- QCReport (L498): fail
- StackResult (L509): 
- Navigation (L522): b_pol_px, to_dict
- GRSState (L556): to_dict
- GeomEphemeris (L581): 
- RunManifest (L594): to_dict
- PipelineConfig (L610): from_dict, from_yaml_like, to_dict, sha
- GRSCompletePipeline (L2824): __init__, _record, process_cube, process_path, derotate_all, build_channels, run_imaging, run_science, run
- FilterBandpass (L3162): 
- FixedLagLuckyStacker (L3407): __init__, push, stack_now
- PipelineStateMachine (L3536): __init__, transition, run_on_cube
- MultiFilterNight (L3553): __init__, add, reduce_all
- IngestStageResult (L3860): to_dict
- CalibStageResult (L3870): to_dict
- QualityStageResult (L3880): to_dict
- AlignStageResult (L3890): to_dict
- StackStageResult (L3900): to_dict
- DerotStageResult (L3910): to_dict
- RestoreStageResult (L3920): to_dict
- ColorStageResult (L3930): to_dict
- NavStageResult (L3940): to_dict
- MeasureStageResult (L3950): to_dict
- TrajStageResult (L3960): to_dict
- ExportStageResult (L3970): to_dict

Top-level functions:

- setup_logging (L194)
- sha256_bytes (L217)
- sha256_file (L220)
- sha256_array (L227)
- sha256_json (L230)
- ensure_dir (L233)
- clamp (L236)
- safe_div (L239)
- wrap_deg (L242)
- wrap_deg_diff (L245)
- deg2rad (L248)
- rad2deg (L251)
- jupiter_eq_km_per_deg (L254)
- jupiter_km_per_deg_lat (L258)
- km_at_jupiter_from_mas (L261)
- _gaussian_kernel1d (L269)
- gaussian_filter2d (L276)
- map_coords (L295)
- fft_convolve2d (L325)
- morph_open_close (L341)
- label_components (L358)
- largest_component (L383)
- percentile_clip (L392)
- normalize_percentile (L399)
- sobel_mag (L404)
- laplacian (L413)
- highpass (L418)
- shift_image (L423)
- rotate_image (L430)
- resize_bilinear (L445)
- tai_utc_offset (L740)
- utc_to_tt_mjd (L748)
- tt_to_tdb_mjd (L764)
- parse_time_string (L774)
- jupiter_system_iii_lon_approx (L784)
- jupiter_distance_au_approx (L797)
- jupiter_apparent_diameter_arcsec (L804)
- compute_geometry (L812)
- refractive_index_dry (L841)
- achromatic_refraction_arcsec (L850)
- dcr_shift_arcsec (L860)
- read_fits (L879)
- _parse_fits_data (L937)
- write_fits (L961)
- read_ser (L1011)
- write_png (L1076)
- ingest_path (L1105)
- read_rgb_fits_channels (L1147)
- estimate_readnoise_gain (L1165)
- make_hot_pixel_mask (L1174)
- replace_hot_pixels (L1180)
- apply_calibration (L1193)
- calibrate_cube (L1211)
- rough_disk_mask (L1224)
- validate_cube (L1232)
- disk_mask_for_quality (L1272)
- score_laplacian_var (L1284)
- score_fft_power (L1296)
- score_sobel_energy (L1318)
- score_tenengrad (L1327)
- score_variance (L1332)
- score_max_pixel (L1339)
- score_frame (L1346)
- score_frames (L1368)
- select_top_indices (L1378)
- phase_correlate (L1390)
- place_alignment_points (L1424)
- local_cross_corr_shift (L1446)
- extract_patch (L1452)
- align_frames_global (L1466)
- align_frames_local_ap (L1479)
- align_stack (L1540)
- stack_mean (L1559)
- stack_median (L1563)
- stack_kappa_sigma (L1567)
- stack_quality_weighted (L1579)
- stack_winsorized (L1591)
- estimate_noise_map (L1600)
- stack_frames (L1612)
- lucky_stack_cube (L1625)
- noll_to_zernike (L1650)
- zernike_radial (L1680)
- zernike (L1690)
- zernike_basis_on_pupil (L1699)
- complex_pupil_psf (L1726)
- kolmogorov_phase_screen (L1753)
- moffat_psf (L1771)
- gaussian_psf (L1780)
- estimate_psf_from_limb (L1787)
- b3_spline_kernel (L1818)
- a_trous_convolve (L1824)
- starlet_decompose (L1845)
- soft_threshold (L1856)
- mad_sigma (L1860)
- starlet_sharpen (L1866)
- richardson_lucy (L1886)
- wiener_deconv (L1905)
- limb_overshoot_metric (L1922)
- restore_image (L1936)
- rgb_to_ycbcr (L1960)
- ycbcr_to_rgb (L1968)
- build_lrgb (L1976)
- register_channels (L1997)
- apply_residual_dcr (L2011)
- project_to_cylindrical (L2030)
- backproject_cylindrical (L2056)
- rough_navigation (L2077)
- derotate_image (L2099)
- derotate_stack_result (L2116)
- extract_limb_points (L2135)
- fit_ellipse_algebraic (L2175)
- fit_oblate_disk (L2202)
- bootstrap_limb_nav (L2218)
- fit_navigation (L2242)
- px_to_lonlat (L2266)
- lonlat_to_px (L2283)
- otsu_threshold (L2297)
- grs_latitude_band_mask (L2311)
- segment_grs_adaptive (L2325)
- segment_grs_otsu (L2367)
- segment_grs (L2376)
- fit_ellipse_to_mask (L2392)
- measure_grs_from_mask (L2410)
- bootstrap_grs (L2442)
- unwrap_longitudes (L2508)
- kalman_rts_1d (L2512)
- smooth_trajectory (L2551)
- fit_drift_model (L2576)
- export_stack (L2597)
- export_state_json (L2602)
- export_trajectory_csv (L2607)
- export_manifest (L2620)
- package_versions (L2625)
- synthetic_jupiter (L2640)
- synthetic_ser_cube (L2688)
- validate_phase_correlate (L2717)
- validate_stack_snr (L2728)
- validate_nav_synthetic (L2740)
- validate_grs_measure (L2748)
- run_validation_suite (L2766)
- guess_filter_from_name (L2792)
- discover_inputs (L2801)
- run_pipeline (L3026)
- build_arg_parser (L3036)
- main (L3078)
- filter_center_nm (L3184)
- rad_to_arcsec (L3190)
- diffraction_limit_arcsec (L3194)
- critical_sampling_arcsec_per_px (L3200)
- plate_scale_arcsec_per_px (L3204)
- effective_focal_length_mm (L3208)
- suggest_roi (L3212)
- parse_firecapture_log (L3227)
- apply_log_to_meta (L3246)
- drizzle_combine (L3269)
- quality_pyramid (L3299)
- hybrid_quality_vector (L3310)
- rank_frames_multi_metric (L3320)
- make_lon_lat_grid (L3329)
- reproject_to_simple_cylindrical (L3343)
- map_measure_grs (L3362)
- assemble_error_budget (L3376)
- write_text_report (L3390)
- get_preset (L3446)
- print_capabilities (L3465)
- estimate_plate_background_gradient (L3469)
- restore_digitized_plate (L3488)
- airmass_approx (L3496)
- score_session (L3501)
- unsharp_mask (L3507)
- image_moments (L3512)
- run_selftests (L3525)
- describe_noll (L3604)
- recommendation_for_aperture_mm (L3652)
- process_r_stack (L3657)
- process_g_stack (L3661)
- process_b_stack (L3665)
- process_ir685_stack (L3669)
- process_ir742_stack (L3673)
- process_ir807_stack (L3677)
- process_ch4_stack (L3681)
- process_clear_stack (L3685)
- dcr_b_minus_g_arcsec (L3689)
- dcr_b_minus_r_arcsec (L3692)
- dcr_b_minus_ir685_arcsec (L3695)
- dcr_b_minus_ir742_arcsec (L3698)
- dcr_b_minus_ch4_arcsec (L3701)
- dcr_g_minus_r_arcsec (L3704)
- dcr_g_minus_ir685_arcsec (L3707)
- dcr_g_minus_ir742_arcsec (L3710)
- dcr_ir685_minus_r_arcsec (L3713)
- dcr_ir685_minus_ir742_arcsec (L3716)
- dcr_ir742_minus_r_arcsec (L3719)
- dcr_ch4_minus_g_arcsec (L3722)
- dcr_ch4_minus_r_arcsec (L3725)
- dcr_ch4_minus_ir685_arcsec (L3728)
- dcr_ch4_minus_ir742_arcsec (L3731)
- apply_wavelet_preset (L3742)
- deg_to_mas (L3746)
- mas_to_deg (L3749)
- arcsec_to_mas (L3752)
- mas_to_arcsec (L3755)
- deg_to_arcsec (L3758)
- arcsec_to_deg (L3761)
- day_to_second (L3764)
- second_to_day (L3767)
- au_to_km (L3770)
- km_to_au (L3773)
- grs_reference_size (L3853)
- apply_site_preset (L3990)
- box_smooth_1 (L3994)
- box_smooth_2 (L3999)
- box_smooth_3 (L4004)
- box_smooth_4 (L4009)
- box_smooth_5 (L4014)
- box_smooth_6 (L4019)
- box_smooth_7 (L4024)
- box_smooth_8 (L4029)
- box_smooth_9 (L4034)
- box_smooth_10 (L4039)
- box_smooth_11 (L4044)
- box_smooth_12 (L4049)
- box_smooth_13 (L4054)
- box_smooth_14 (L4059)
- box_smooth_15 (L4064)
- box_smooth_16 (L4069)
- box_smooth_17 (L4074)
- box_smooth_18 (L4079)
- box_smooth_19 (L4084)
- box_smooth_20 (L4089)
- box_smooth_21 (L4094)
- box_smooth_22 (L4099)
- box_smooth_23 (L4104)
- box_smooth_24 (L4109)
- box_smooth_25 (L4114)
- box_smooth_26 (L4119)
- box_smooth_27 (L4124)
- box_smooth_28 (L4129)
- box_smooth_29 (L4134)
- box_smooth_30 (L4139)
- box_smooth_31 (L4144)
- box_smooth_32 (L4149)
- box_smooth_33 (L4154)
- box_smooth_34 (L4159)
- box_smooth_35 (L4164)
- box_smooth_36 (L4169)
- box_smooth_37 (L4174)
- box_smooth_38 (L4179)
- box_smooth_39 (L4184)
- box_smooth_40 (L4189)
- box_smooth_41 (L4194)
- box_smooth_42 (L4199)
- box_smooth_43 (L4204)
- box_smooth_44 (L4209)
- box_smooth_45 (L4214)
- box_smooth_46 (L4219)
- box_smooth_47 (L4224)
- box_smooth_48 (L4229)
- box_smooth_49 (L4234)
- box_smooth_50 (L4239)
- box_smooth_51 (L4244)
- box_smooth_52 (L4249)
- box_smooth_53 (L4254)
- box_smooth_54 (L4259)
- box_smooth_55 (L4264)
- box_smooth_56 (L4269)
- box_smooth_57 (L4274)
- box_smooth_58 (L4279)
- box_smooth_59 (L4284)
- box_smooth_60 (L4289)
- box_smooth_61 (L4294)
- box_smooth_62 (L4299)
- box_smooth_63 (L4304)
- box_smooth_64 (L4309)
- box_smooth_65 (L4314)
- box_smooth_66 (L4319)
- box_smooth_67 (L4324)
- box_smooth_68 (L4329)
- box_smooth_69 (L4334)
- box_smooth_70 (L4339)
- box_smooth_71 (L4344)
- box_smooth_72 (L4349)
- box_smooth_73 (L4354)
- box_smooth_74 (L4359)
- box_smooth_75 (L4364)
- box_smooth_76 (L4369)
- box_smooth_77 (L4374)
- box_smooth_78 (L4379)
- box_smooth_79 (L4384)
- box_smooth_80 (L4389)
- box_smooth_81 (L4394)
- box_smooth_82 (L4399)
- box_smooth_83 (L4404)
- box_smooth_84 (L4409)
- box_smooth_85 (L4414)
- box_smooth_86 (L4419)
- box_smooth_87 (L4424)
- box_smooth_88 (L4429)
- box_smooth_89 (L4434)
- box_smooth_90 (L4439)
- box_smooth_91 (L4444)
- box_smooth_92 (L4449)
- box_smooth_93 (L4454)
- box_smooth_94 (L4459)
- box_smooth_95 (L4464)
- box_smooth_96 (L4469)
- box_smooth_97 (L4474)
- box_smooth_98 (L4479)
- box_smooth_99 (L4484)
- box_smooth_100 (L4489)
- box_smooth_101 (L4494)
- box_smooth_102 (L4499)
- box_smooth_103 (L4504)
- box_smooth_104 (L4509)
- box_smooth_105 (L4514)
- box_smooth_106 (L4519)
- box_smooth_107 (L4524)
- box_smooth_108 (L4529)
- box_smooth_109 (L4534)
- box_smooth_110 (L4539)
- box_smooth_111 (L4544)
- box_smooth_112 (L4549)
- box_smooth_113 (L4554)
- box_smooth_114 (L4559)
- box_smooth_115 (L4564)
- box_smooth_116 (L4569)
- box_smooth_117 (L4574)
- box_smooth_118 (L4579)
- box_smooth_119 (L4584)
- box_smooth_120 (L4589)
- score_all_frames_laplacian_var (L4594)
- score_all_frames_fft_power (L4597)
- score_all_frames_hybrid (L4600)
- score_all_frames_sobel_energy (L4603)
- score_all_frames_tenengrad (L4606)
- score_all_frames_variance (L4609)
- score_all_frames_max_pixel (L4612)
- algorithm_help (L4638)
- step_ingest_description (L4643)
- step_qc_description (L4646)
- step_calibrate_description (L4649)
- step_score_description (L4652)
- step_select_description (L4655)
- step_align_description (L4658)
- step_stack_description (L4661)
- step_derotate_description (L4664)
- step_register_description (L4667)
- step_restore_description (L4670)
- step_lrgb_description (L4673)
- step_navigate_description (L4676)
- step_segment_description (L4679)
- step_measure_description (L4682)
- step_bootstrap_description (L4685)
- step_error_budget_description (L4688)
- step_smooth_description (L4691)
- step_export_description (L4694)
- step_manifest_description (L4697)
- step_report_description (L4700)
- gaussian_blur_s1 (L4703)
- gaussian_blur_s2 (L4706)
- gaussian_blur_s3 (L4709)
- gaussian_blur_s4 (L4712)
- gaussian_blur_s5 (L4715)
- gaussian_blur_s6 (L4718)
- gaussian_blur_s7 (L4721)
- gaussian_blur_s8 (L4724)
- gaussian_blur_s9 (L4727)
- gaussian_blur_s10 (L4730)
- gaussian_blur_s11 (L4733)
- gaussian_blur_s12 (L4736)
- gaussian_blur_s13 (L4739)
- gaussian_blur_s14 (L4742)
- gaussian_blur_s15 (L4745)
- gaussian_blur_s16 (L4748)
- gaussian_blur_s17 (L4751)
- gaussian_blur_s18 (L4754)
- gaussian_blur_s19 (L4757)
- gaussian_blur_s20 (L4760)
- gaussian_blur_s21 (L4763)
- gaussian_blur_s22 (L4766)
- gaussian_blur_s23 (L4769)
- gaussian_blur_s24 (L4772)
- gaussian_blur_s25 (L4775)
- gaussian_blur_s26 (L4778)
- gaussian_blur_s27 (L4781)
- gaussian_blur_s28 (L4784)
- gaussian_blur_s29 (L4787)
- gaussian_blur_s30 (L4790)
- gaussian_blur_s31 (L4793)
- gaussian_blur_s32 (L4796)
- gaussian_blur_s33 (L4799)
- gaussian_blur_s34 (L4802)
- gaussian_blur_s35 (L4805)
- gaussian_blur_s36 (L4808)
- gaussian_blur_s37 (L4811)
- gaussian_blur_s38 (L4814)
- gaussian_blur_s39 (L4817)
- gaussian_blur_s40 (L4820)
- gaussian_blur_s41 (L4823)
- gaussian_blur_s42 (L4826)
- gaussian_blur_s43 (L4829)
- gaussian_blur_s44 (L4832)
- gaussian_blur_s45 (L4835)
- gaussian_blur_s46 (L4838)
- gaussian_blur_s47 (L4841)
- gaussian_blur_s48 (L4844)
- gaussian_blur_s49 (L4847)
- gaussian_blur_s50 (L4850)
- gaussian_blur_s51 (L4853)
- gaussian_blur_s52 (L4856)
- gaussian_blur_s53 (L4859)
- gaussian_blur_s54 (L4862)
- gaussian_blur_s55 (L4865)
- gaussian_blur_s56 (L4868)
- gaussian_blur_s57 (L4871)
- gaussian_blur_s58 (L4874)
- gaussian_blur_s59 (L4877)
- gaussian_blur_s60 (L4880)
- shift_dym5_dxm5 (L4883)
- shift_dym5_dxm4 (L4886)
- shift_dym5_dxm3 (L4889)
- shift_dym5_dxm2 (L4892)
- shift_dym5_dxm1 (L4895)
- shift_dym5_dx0 (L4898)
- shift_dym5_dx1 (L4901)
- shift_dym5_dx2 (L4904)
- shift_dym5_dx3 (L4907)
- shift_dym5_dx4 (L4910)
- shift_dym5_dx5 (L4913)
- shift_dym4_dxm5 (L4916)
- shift_dym4_dxm4 (L4919)
- shift_dym4_dxm3 (L4922)
- shift_dym4_dxm2 (L4925)
- shift_dym4_dxm1 (L4928)
- shift_dym4_dx0 (L4931)
- shift_dym4_dx1 (L4934)
- shift_dym4_dx2 (L4937)
- shift_dym4_dx3 (L4940)
- shift_dym4_dx4 (L4943)
- shift_dym4_dx5 (L4946)
- shift_dym3_dxm5 (L4949)
- shift_dym3_dxm4 (L4952)
- shift_dym3_dxm3 (L4955)
- shift_dym3_dxm2 (L4958)
- shift_dym3_dxm1 (L4961)
- shift_dym3_dx0 (L4964)
- shift_dym3_dx1 (L4967)
- shift_dym3_dx2 (L4970)
- shift_dym3_dx3 (L4973)
- shift_dym3_dx4 (L4976)
- shift_dym3_dx5 (L4979)
- shift_dym2_dxm5 (L4982)
- shift_dym2_dxm4 (L4985)
- shift_dym2_dxm3 (L4988)
- shift_dym2_dxm2 (L4991)
- shift_dym2_dxm1 (L4994)
- shift_dym2_dx0 (L4997)
- shift_dym2_dx1 (L5000)
- shift_dym2_dx2 (L5003)
- shift_dym2_dx3 (L5006)
- shift_dym2_dx4 (L5009)
- shift_dym2_dx5 (L5012)
- shift_dym1_dxm5 (L5015)
- shift_dym1_dxm4 (L5018)
- shift_dym1_dxm3 (L5021)
- shift_dym1_dxm2 (L5024)
- shift_dym1_dxm1 (L5027)
- shift_dym1_dx0 (L5030)
- shift_dym1_dx1 (L5033)
- shift_dym1_dx2 (L5036)
- shift_dym1_dx3 (L5039)
- shift_dym1_dx4 (L5042)
- shift_dym1_dx5 (L5045)
- shift_dy0_dxm5 (L5048)
- shift_dy0_dxm4 (L5051)
- shift_dy0_dxm3 (L5054)
- shift_dy0_dxm2 (L5057)
- shift_dy0_dxm1 (L5060)
- shift_dy0_dx1 (L5063)
- shift_dy0_dx2 (L5066)
- shift_dy0_dx3 (L5069)
- shift_dy0_dx4 (L5072)
- shift_dy0_dx5 (L5075)
- shift_dy1_dxm5 (L5078)
- shift_dy1_dxm4 (L5081)
- shift_dy1_dxm3 (L5084)
- shift_dy1_dxm2 (L5087)
- shift_dy1_dxm1 (L5090)
- shift_dy1_dx0 (L5093)
- shift_dy1_dx1 (L5096)
- shift_dy1_dx2 (L5099)
- shift_dy1_dx3 (L5102)
- shift_dy1_dx4 (L5105)
- shift_dy1_dx5 (L5108)
- shift_dy2_dxm5 (L5111)
- shift_dy2_dxm4 (L5114)
- shift_dy2_dxm3 (L5117)
- shift_dy2_dxm2 (L5120)
- shift_dy2_dxm1 (L5123)
- shift_dy2_dx0 (L5126)
- shift_dy2_dx1 (L5129)
- shift_dy2_dx2 (L5132)
- shift_dy2_dx3 (L5135)
- shift_dy2_dx4 (L5138)
- shift_dy2_dx5 (L5141)
- shift_dy3_dxm5 (L5144)
- shift_dy3_dxm4 (L5147)
- shift_dy3_dxm3 (L5150)
- shift_dy3_dxm2 (L5153)
- shift_dy3_dxm1 (L5156)
- shift_dy3_dx0 (L5159)
- shift_dy3_dx1 (L5162)
- shift_dy3_dx2 (L5165)
- shift_dy3_dx3 (L5168)
- shift_dy3_dx4 (L5171)
- shift_dy3_dx5 (L5174)
- shift_dy4_dxm5 (L5177)
- shift_dy4_dxm4 (L5180)
- shift_dy4_dxm3 (L5183)
- shift_dy4_dxm2 (L5186)
- shift_dy4_dxm1 (L5189)
- shift_dy4_dx0 (L5192)
- shift_dy4_dx1 (L5195)
- shift_dy4_dx2 (L5198)
- shift_dy4_dx3 (L5201)
- shift_dy4_dx4 (L5204)
- shift_dy4_dx5 (L5207)
- shift_dy5_dxm5 (L5210)
- shift_dy5_dxm4 (L5213)
- shift_dy5_dxm3 (L5216)
- shift_dy5_dxm2 (L5219)
- shift_dy5_dxm1 (L5222)
- shift_dy5_dx0 (L5225)
- shift_dy5_dx1 (L5228)
- shift_dy5_dx2 (L5231)
- shift_dy5_dx3 (L5234)
- shift_dy5_dx4 (L5237)
- shift_dy5_dx5 (L5240)
- great_circle_distance_deg (L5249)
- bearing_deg (L5257)
- cylindrical_equal_area_weight (L5265)
- integrate_mask_area_km2 (L5269)
- brightness_temperature_proxy (L5274)
- limb_darkening_law (L5281)
- apply_limb_darkening_model (L5287)
- flatten_limb_darkening (L5299)
- series_interpolate (L5304)
- detrend_linear (L5309)
- lomb_like_periodogram (L5318)
- search_90day_oscillation (L5334)
- robust_mad (L5343)
- outlier_mask_mad (L5348)
- running_median (L5354)
- align_by_centroid (L5366)
- multi_frame_max_entropy_stack (L5375)
- estimate_fwhm_from_edge (L5389)
- annulus_mask (L5401)
- export_winjupos_like_csv (L5408)
- load_trajectory_csv (L5417)
- states_from_trajectory_rows (L5425)
- photometric_aperture_radius_1 (L5446)
- photometric_aperture_radius_2 (L5450)
- photometric_aperture_radius_3 (L5454)
- photometric_aperture_radius_4 (L5458)
- photometric_aperture_radius_5 (L5462)
- photometric_aperture_radius_6 (L5466)
- photometric_aperture_radius_7 (L5470)
- photometric_aperture_radius_8 (L5474)
- photometric_aperture_radius_9 (L5478)
- photometric_aperture_radius_10 (L5482)
- photometric_aperture_radius_11 (L5486)
- photometric_aperture_radius_12 (L5490)
- photometric_aperture_radius_13 (L5494)
- photometric_aperture_radius_14 (L5498)
- photometric_aperture_radius_15 (L5502)
- photometric_aperture_radius_16 (L5506)
- photometric_aperture_radius_17 (L5510)
- photometric_aperture_radius_18 (L5514)
- photometric_aperture_radius_19 (L5518)
- photometric_aperture_radius_20 (L5522)
- photometric_aperture_radius_21 (L5526)
- photometric_aperture_radius_22 (L5530)
- photometric_aperture_radius_23 (L5534)
- photometric_aperture_radius_24 (L5538)
- photometric_aperture_radius_25 (L5542)
- photometric_aperture_radius_26 (L5546)
- photometric_aperture_radius_27 (L5550)
- photometric_aperture_radius_28 (L5554)
- photometric_aperture_radius_29 (L5558)
- photometric_aperture_radius_30 (L5562)
- photometric_aperture_radius_31 (L5566)
- photometric_aperture_radius_32 (L5570)
- photometric_aperture_radius_33 (L5574)
- photometric_aperture_radius_34 (L5578)
- photometric_aperture_radius_35 (L5582)
- photometric_aperture_radius_36 (L5586)
- photometric_aperture_radius_37 (L5590)
- photometric_aperture_radius_38 (L5594)
- photometric_aperture_radius_39 (L5598)
- photometric_aperture_radius_40 (L5602)
- photometric_aperture_radius_41 (L5606)
- photometric_aperture_radius_42 (L5610)
- photometric_aperture_radius_43 (L5614)
- photometric_aperture_radius_44 (L5618)
- photometric_aperture_radius_45 (L5622)
- photometric_aperture_radius_46 (L5626)
- photometric_aperture_radius_47 (L5630)
- photometric_aperture_radius_48 (L5634)
- photometric_aperture_radius_49 (L5638)
- photometric_aperture_radius_50 (L5642)
- photometric_aperture_radius_51 (L5646)
- photometric_aperture_radius_52 (L5650)
- photometric_aperture_radius_53 (L5654)
- photometric_aperture_radius_54 (L5658)
- photometric_aperture_radius_55 (L5662)
- photometric_aperture_radius_56 (L5666)
- photometric_aperture_radius_57 (L5670)
- photometric_aperture_radius_58 (L5674)
- photometric_aperture_radius_59 (L5678)
- photometric_aperture_radius_60 (L5682)
- photometric_aperture_radius_61 (L5686)
- photometric_aperture_radius_62 (L5690)
- photometric_aperture_radius_63 (L5694)
- photometric_aperture_radius_64 (L5698)
- photometric_aperture_radius_65 (L5702)
- photometric_aperture_radius_66 (L5706)
- photometric_aperture_radius_67 (L5710)
- photometric_aperture_radius_68 (L5714)
- photometric_aperture_radius_69 (L5718)
- photometric_aperture_radius_70 (L5722)
- photometric_aperture_radius_71 (L5726)
- photometric_aperture_radius_72 (L5730)
- photometric_aperture_radius_73 (L5734)
- photometric_aperture_radius_74 (L5738)
- photometric_aperture_radius_75 (L5742)
- photometric_aperture_radius_76 (L5746)
- photometric_aperture_radius_77 (L5750)
- photometric_aperture_radius_78 (L5754)
- photometric_aperture_radius_79 (L5758)
- photometric_aperture_radius_80 (L5762)
- photometric_aperture_radius_81 (L5766)
- photometric_aperture_radius_82 (L5770)
- photometric_aperture_radius_83 (L5774)
- photometric_aperture_radius_84 (L5778)
- photometric_aperture_radius_85 (L5782)
- photometric_aperture_radius_86 (L5786)
- photometric_aperture_radius_87 (L5790)
- photometric_aperture_radius_88 (L5794)
- photometric_aperture_radius_89 (L5798)
- photometric_aperture_radius_90 (L5802)
- photometric_aperture_radius_91 (L5806)
- photometric_aperture_radius_92 (L5810)
- photometric_aperture_radius_93 (L5814)
- photometric_aperture_radius_94 (L5818)
- photometric_aperture_radius_95 (L5822)
- photometric_aperture_radius_96 (L5826)
- photometric_aperture_radius_97 (L5830)
- photometric_aperture_radius_98 (L5834)
- photometric_aperture_radius_99 (L5838)
- photometric_aperture_radius_100 (L5842)
- reference_grs_length_year_1990 (L5846)
- reference_grs_width_year_1990 (L5849)
- reference_grs_length_year_1991 (L5852)
- reference_grs_width_year_1991 (L5855)
- reference_grs_length_year_1992 (L5858)
- reference_grs_width_year_1992 (L5861)
- reference_grs_length_year_1993 (L5864)
- reference_grs_width_year_1993 (L5867)
- reference_grs_length_year_1994 (L5870)
- reference_grs_width_year_1994 (L5873)
- reference_grs_length_year_1995 (L5876)
- reference_grs_width_year_1995 (L5879)
- reference_grs_length_year_1996 (L5882)
- reference_grs_width_year_1996 (L5885)
- reference_grs_length_year_1997 (L5888)
- reference_grs_width_year_1997 (L5891)
- reference_grs_length_year_1998 (L5894)
- reference_grs_width_year_1998 (L5897)
- reference_grs_length_year_1999 (L5900)
- reference_grs_width_year_1999 (L5903)
- reference_grs_length_year_2000 (L5906)
- reference_grs_width_year_2000 (L5909)
- reference_grs_length_year_2001 (L5912)
- reference_grs_width_year_2001 (L5915)
- reference_grs_length_year_2002 (L5918)
- reference_grs_width_year_2002 (L5921)
- reference_grs_length_year_2003 (L5924)
- reference_grs_width_year_2003 (L5927)
- reference_grs_length_year_2004 (L5930)
- reference_grs_width_year_2004 (L5933)
- reference_grs_length_year_2005 (L5936)
- reference_grs_width_year_2005 (L5939)
- reference_grs_length_year_2006 (L5942)
- reference_grs_width_year_2006 (L5945)
- reference_grs_length_year_2007 (L5948)
- reference_grs_width_year_2007 (L5951)
- reference_grs_length_year_2008 (L5954)
- reference_grs_width_year_2008 (L5957)
- reference_grs_length_year_2009 (L5960)
- reference_grs_width_year_2009 (L5963)
- reference_grs_length_year_2010 (L5966)
- reference_grs_width_year_2010 (L5969)
- reference_grs_length_year_2011 (L5972)
- reference_grs_width_year_2011 (L5975)
- reference_grs_length_year_2012 (L5978)
- reference_grs_width_year_2012 (L5981)
- reference_grs_length_year_2013 (L5984)
- reference_grs_width_year_2013 (L5987)
- reference_grs_length_year_2014 (L5990)
- reference_grs_width_year_2014 (L5993)
- reference_grs_length_year_2015 (L5996)
- reference_grs_width_year_2015 (L5999)
- reference_grs_length_year_2016 (L6002)
- reference_grs_width_year_2016 (L6005)
- reference_grs_length_year_2017 (L6008)
- reference_grs_width_year_2017 (L6011)
- reference_grs_length_year_2018 (L6014)
- reference_grs_width_year_2018 (L6017)
- reference_grs_length_year_2019 (L6020)
- reference_grs_width_year_2019 (L6023)
- reference_grs_length_year_2020 (L6026)
- reference_grs_width_year_2020 (L6029)
- reference_grs_length_year_2021 (L6032)
- reference_grs_width_year_2021 (L6035)
- reference_grs_length_year_2022 (L6038)
- reference_grs_width_year_2022 (L6041)
- reference_grs_length_year_2023 (L6044)
- reference_grs_width_year_2023 (L6047)
- reference_grs_length_year_2024 (L6050)
- reference_grs_width_year_2024 (L6053)
- reference_grs_length_year_2025 (L6056)
- reference_grs_width_year_2025 (L6059)
- reference_grs_length_year_2026 (L6062)
- reference_grs_width_year_2026 (L6065)
- sobel_magnitude (L6081)
- prewitt_magnitude (L6086)
- scharr_magnitude (L6091)
- stage_name_planning (L6097)
- stage_name_acquisition (L6101)
- stage_name_ingest (L6105)
- stage_name_qc (L6109)
- stage_name_calibration (L6113)
- stage_name_lucky_score (L6117)
- stage_name_frame_select (L6121)
- stage_name_global_align (L6125)
- stage_name_local_ap_align (L6129)
- stage_name_stack (L6133)
- stage_name_noise_estimate (L6137)
- stage_name_derotation (L6141)
- stage_name_channel_register (L6145)
- stage_name_dcr_correct (L6149)
- stage_name_psf_estimate (L6153)
- stage_name_wavelet_restore (L6157)
- stage_name_rl_deconv (L6161)
- stage_name_lrgb_merge (L6165)
- stage_name_color_grade (L6169)
- stage_name_limb_extract (L6173)
- stage_name_ellipse_fit (L6177)
- stage_name_bootstrap_nav (L6181)
- stage_name_lonlat_project (L6185)
- stage_name_grs_segment (L6189)
- stage_name_grs_measure (L6193)
- stage_name_bootstrap_grs (L6197)
- stage_name_error_budget (L6201)
- stage_name_trajectory_rts (L6205)
- stage_name_drift_fit (L6209)
- stage_name_export_fits (L6213)
- stage_name_export_png (L6217)
- stage_name_export_csv (L6221)
- stage_name_export_manifest (L6225)
- stage_name_validate (L6229)
- stage_name_report (L6233)
- select_top_1pct (L6236)
- select_top_2pct (L6239)
- select_top_3pct (L6242)
- select_top_4pct (L6245)
- select_top_5pct (L6248)
- select_top_6pct (L6251)
- select_top_7pct (L6254)
- select_top_8pct (L6257)
- select_top_9pct (L6260)
- select_top_10pct (L6263)
- select_top_11pct (L6266)
- select_top_12pct (L6269)
- select_top_13pct (L6272)
- select_top_14pct (L6275)
- select_top_15pct (L6278)
- select_top_16pct (L6281)
- select_top_17pct (L6284)
- select_top_18pct (L6287)
- select_top_19pct (L6290)
- select_top_20pct (L6293)
- select_top_21pct (L6296)
- select_top_22pct (L6299)
- select_top_23pct (L6302)
- select_top_24pct (L6305)
- select_top_25pct (L6308)
- select_top_26pct (L6311)
- select_top_27pct (L6314)
- select_top_28pct (L6317)
- select_top_29pct (L6320)
- select_top_30pct (L6323)
- select_top_31pct (L6326)
- select_top_32pct (L6329)
- select_top_33pct (L6332)
- select_top_34pct (L6335)
- select_top_35pct (L6338)
- select_top_36pct (L6341)
- select_top_37pct (L6344)
- select_top_38pct (L6347)
- select_top_39pct (L6350)
- select_top_40pct (L6353)
- select_top_41pct (L6356)
- select_top_42pct (L6359)
- select_top_43pct (L6362)
- select_top_44pct (L6365)
- select_top_45pct (L6368)
- select_top_46pct (L6371)
- select_top_47pct (L6374)
- select_top_48pct (L6377)
- select_top_49pct (L6380)
- select_top_50pct (L6383)
- select_top_51pct (L6386)
- select_top_52pct (L6389)
- select_top_53pct (L6392)
- select_top_54pct (L6395)
- select_top_55pct (L6398)
- select_top_56pct (L6401)
- select_top_57pct (L6404)
- select_top_58pct (L6407)
- select_top_59pct (L6410)
- select_top_60pct (L6413)
- select_top_61pct (L6416)
- select_top_62pct (L6419)
- select_top_63pct (L6422)
- select_top_64pct (L6425)
- select_top_65pct (L6428)
- select_top_66pct (L6431)
- select_top_67pct (L6434)
- select_top_68pct (L6437)
- select_top_69pct (L6440)
- select_top_70pct (L6443)
- select_top_71pct (L6446)
- select_top_72pct (L6449)
- select_top_73pct (L6452)
- select_top_74pct (L6455)
- select_top_75pct (L6458)
- select_top_76pct (L6461)
- select_top_77pct (L6464)
- select_top_78pct (L6467)
- select_top_79pct (L6470)
- select_top_80pct (L6473)
- select_top_81pct (L6476)
- select_top_82pct (L6479)
- select_top_83pct (L6482)
- select_top_84pct (L6485)
- select_top_85pct (L6488)
- select_top_86pct (L6491)
- select_top_87pct (L6494)
- select_top_88pct (L6497)
- select_top_89pct (L6500)
- select_top_90pct (L6503)
- select_top_91pct (L6506)
- select_top_92pct (L6509)
- select_top_93pct (L6512)
- select_top_94pct (L6515)
- select_top_95pct (L6518)
- select_top_96pct (L6521)
- select_top_97pct (L6524)
- select_top_98pct (L6527)
- select_top_99pct (L6530)
- select_top_100pct (L6533)
- landweber_deconv (L6542)
- van_cittert_deconv (L6554)
- multi_resolution_support (L6563)
- significant_wavelet_reconstruction (L6572)
- pyramid_downsample (L6584)
- pyramid_upsample (L6588)
- build_gaussian_pyramid (L6592)
- build_laplacian_pyramid (L6601)
- collapse_laplacian_pyramid (L6610)
- focus_stack_from_pyramid (L6617)
- correlation_coefficient (L6629)
- ssim_approx (L6635)
- psnr (L6644)
- radial_cut_pa_000 (L6653)
- radial_cut_pa_005 (L6662)
- radial_cut_pa_010 (L6671)
- radial_cut_pa_015 (L6680)
- radial_cut_pa_020 (L6689)
- radial_cut_pa_025 (L6698)
- radial_cut_pa_030 (L6707)
- radial_cut_pa_035 (L6716)
- radial_cut_pa_040 (L6725)
- radial_cut_pa_045 (L6734)
- radial_cut_pa_050 (L6743)
- radial_cut_pa_055 (L6752)
- radial_cut_pa_060 (L6761)
- radial_cut_pa_065 (L6770)
- radial_cut_pa_070 (L6779)
- radial_cut_pa_075 (L6788)
- radial_cut_pa_080 (L6797)
- radial_cut_pa_085 (L6806)
- radial_cut_pa_090 (L6815)
- radial_cut_pa_095 (L6824)
- radial_cut_pa_100 (L6833)
- radial_cut_pa_105 (L6842)
- radial_cut_pa_110 (L6851)
- radial_cut_pa_115 (L6860)
- radial_cut_pa_120 (L6869)
- radial_cut_pa_125 (L6878)
- radial_cut_pa_130 (L6887)
- radial_cut_pa_135 (L6896)
- radial_cut_pa_140 (L6905)
- radial_cut_pa_145 (L6914)
- radial_cut_pa_150 (L6923)
- radial_cut_pa_155 (L6932)
- radial_cut_pa_160 (L6941)
- radial_cut_pa_165 (L6950)
- radial_cut_pa_170 (L6959)
- radial_cut_pa_175 (L6968)
- radial_cut_pa_180 (L6977)
- radial_cut_pa_185 (L6986)
- radial_cut_pa_190 (L6995)
- radial_cut_pa_195 (L7004)
- radial_cut_pa_200 (L7013)
- radial_cut_pa_205 (L7022)
- radial_cut_pa_210 (L7031)
- radial_cut_pa_215 (L7040)
- radial_cut_pa_220 (L7049)
- radial_cut_pa_225 (L7058)
- radial_cut_pa_230 (L7067)
- radial_cut_pa_235 (L7076)
- radial_cut_pa_240 (L7085)
- radial_cut_pa_245 (L7094)
- radial_cut_pa_250 (L7103)
- radial_cut_pa_255 (L7112)
- radial_cut_pa_260 (L7121)
- radial_cut_pa_265 (L7130)
- radial_cut_pa_270 (L7139)
- radial_cut_pa_275 (L7148)
- radial_cut_pa_280 (L7157)
- radial_cut_pa_285 (L7166)
- radial_cut_pa_290 (L7175)
- radial_cut_pa_295 (L7184)
- radial_cut_pa_300 (L7193)
- radial_cut_pa_305 (L7202)
- radial_cut_pa_310 (L7211)
- radial_cut_pa_315 (L7220)
- radial_cut_pa_320 (L7229)
- radial_cut_pa_325 (L7238)
- radial_cut_pa_330 (L7247)
- radial_cut_pa_335 (L7256)
- radial_cut_pa_340 (L7265)
- radial_cut_pa_345 (L7274)
- radial_cut_pa_350 (L7283)
- radial_cut_pa_355 (L7292)
- longitude_bin_photometry_18 (L7301)
- longitude_bin_photometry_24 (L7313)
- longitude_bin_photometry_30 (L7325)
- longitude_bin_photometry_36 (L7337)
- longitude_bin_photometry_45 (L7349)
- longitude_bin_photometry_60 (L7361)
- longitude_bin_photometry_72 (L7373)
- longitude_bin_photometry_90 (L7385)
- longitude_bin_photometry_120 (L7397)
- longitude_bin_photometry_180 (L7409)
- longitude_bin_photometry_360 (L7421)
- cfg_set_mode (L7433)
- cfg_get_mode (L7436)
- cfg_set_seed (L7439)
- cfg_get_seed (L7442)
- cfg_set_raw_dir (L7445)
- cfg_get_raw_dir (L7448)
- cfg_set_work_dir (L7451)
- cfg_get_work_dir (L7454)
- cfg_set_out_dir (L7457)
- cfg_get_out_dir (L7460)
- cfg_set_site_lat (L7463)
- cfg_get_site_lat (L7466)
- cfg_set_site_lon (L7469)
- cfg_get_site_lon (L7472)
- cfg_set_site_elev_m (L7475)
- cfg_get_site_elev_m (L7478)
- cfg_set_quality_metric (L7481)
- cfg_get_quality_metric (L7484)
- cfg_set_primary_fraction (L7487)
- cfg_get_primary_fraction (L7490)
- cfg_set_ap_grid (L7493)
- cfg_get_ap_grid (L7496)
- cfg_set_ap_box (L7499)
- cfg_get_ap_box (L7502)
- cfg_set_max_shift_px (L7505)
- cfg_get_max_shift_px (L7508)
- cfg_set_align_mode (L7511)
- cfg_get_align_mode (L7514)
- cfg_set_stack_method (L7517)
- cfg_get_stack_method (L7520)
- cfg_set_kappa (L7523)
- cfg_get_kappa (L7526)
- cfg_set_drizzle_scale (L7529)
- cfg_get_drizzle_scale (L7532)
- cfg_set_derot_enable (L7535)
- cfg_get_derot_enable (L7538)
- cfg_set_derot_map_width (L7541)
- cfg_get_derot_map_width (L7544)
- cfg_set_restore_method (L7547)
- cfg_get_restore_method (L7550)
- cfg_set_wavelet_layers (L7553)
- cfg_get_wavelet_layers (L7556)
- cfg_set_rl_iters (L7559)
- cfg_get_rl_iters (L7562)
- cfg_set_l_source (L7565)
- cfg_get_l_source (L7568)
- cfg_set_sat_scale (L7571)
- cfg_get_sat_scale (L7574)
- cfg_set_denoise_chroma (L7577)
- cfg_get_denoise_chroma (L7580)
- cfg_set_limb_method (L7583)
- cfg_get_limb_method (L7586)
- cfg_set_n_rays (L7589)
- cfg_get_n_rays (L7592)
- cfg_set_bootstrap_limb (L7595)
- cfg_get_bootstrap_limb (L7598)
- cfg_set_grs_definition_id (L7601)
- cfg_get_grs_definition_id (L7604)
- cfg_set_segment_method (L7607)
- cfg_get_segment_method (L7610)
- cfg_set_bootstrap_n (L7613)
- cfg_get_bootstrap_n (L7616)
- cfg_set_traj_enable (L7619)
- cfg_get_traj_enable (L7622)
- cfg_set_smoother (L7625)
- cfg_get_smoother (L7628)
- cfg_set_process_noise_lon (L7631)
- cfg_get_process_noise_lon (L7634)
- cfg_set_write_fits (L7637)
- cfg_get_write_fits (L7640)
- cfg_set_write_png (L7643)
- cfg_get_write_png (L7646)
- cfg_set_write_csv (L7649)
- cfg_get_write_csv (L7652)
- cfg_set_log_level (L7655)
- cfg_get_log_level (L7658)
- cfg_set_min_frames (L7661)
- cfg_get_min_frames (L7664)
- cfg_set_max_clip_frac (L7667)
- cfg_get_max_clip_frac (L7670)
- cfg_set_flux_drop_frac (L7673)
- cfg_get_flux_drop_frac (L7676)
- make_demo_cube_1 (L7679)
- make_demo_cube_2 (L7682)
- make_demo_cube_3 (L7685)
- make_demo_cube_4 (L7688)
- make_demo_cube_5 (L7691)
- make_demo_cube_6 (L7694)
- make_demo_cube_7 (L7697)
- make_demo_cube_8 (L7700)
- make_demo_cube_9 (L7703)
- make_demo_cube_10 (L7706)
- make_demo_cube_11 (L7709)
- make_demo_cube_12 (L7712)
- make_demo_cube_13 (L7715)
- make_demo_cube_14 (L7718)
- make_demo_cube_15 (L7721)
- make_demo_cube_16 (L7724)
- make_demo_cube_17 (L7727)
- make_demo_cube_18 (L7730)
- make_demo_cube_19 (L7733)
- make_demo_cube_20 (L7736)
- make_demo_cube_21 (L7739)
- make_demo_cube_22 (L7742)
- make_demo_cube_23 (L7745)
- make_demo_cube_24 (L7748)
- make_demo_cube_25 (L7751)
- make_demo_cube_26 (L7754)
- make_demo_cube_27 (L7757)
- make_demo_cube_28 (L7760)
- make_demo_cube_29 (L7763)
- make_demo_cube_30 (L7766)
- make_demo_cube_31 (L7769)
- make_demo_cube_32 (L7772)
- make_demo_cube_33 (L7775)
- make_demo_cube_34 (L7778)
- make_demo_cube_35 (L7781)
- make_demo_cube_36 (L7784)
- make_demo_cube_37 (L7787)
- make_demo_cube_38 (L7790)
- make_demo_cube_39 (L7793)
- make_demo_cube_40 (L7796)
- make_demo_cube_41 (L7799)
- make_demo_cube_42 (L7802)
- make_demo_cube_43 (L7805)
- make_demo_cube_44 (L7808)
- make_demo_cube_45 (L7811)
- make_demo_cube_46 (L7814)
- make_demo_cube_47 (L7817)
- make_demo_cube_48 (L7820)
- make_demo_cube_49 (L7823)
- make_demo_cube_50 (L7826)
- hash_pipeline_inputs (L7829)
- compare_states (L7839)
- format_state_line (L7848)
- ensure_rgb_float (L7853)
- save_channels_fits (L7862)
- load_channels_fits (L7868)
- print_user_manual (L8154)
- polynomial_background_order_0 (L8159)
- polynomial_background_order_1 (L8162)
- polynomial_background_order_2 (L8165)
- polynomial_background_order_3 (L8168)
- polynomial_background_order_4 (L8171)
- polynomial_background_order_5 (L8174)
- polynomial_background_order_6 (L8177)
- polynomial_background_order_7 (L8180)
- polynomial_background_order_8 (L8183)
- polynomial_background_order_9 (L8186)
- polynomial_background_order_10 (L8189)
- polynomial_background_order_11 (L8192)
- polynomial_background_order_12 (L8195)
- polynomial_background_order_13 (L8198)
- polynomial_background_order_14 (L8201)
- polynomial_background_order_15 (L8204)
- threshold_percentile_5 (L8207)
- threshold_percentile_10 (L8211)
- threshold_percentile_15 (L8215)
- threshold_percentile_20 (L8219)
- threshold_percentile_25 (L8223)
- threshold_percentile_30 (L8227)
- threshold_percentile_35 (L8231)
- threshold_percentile_40 (L8235)
- threshold_percentile_45 (L8239)
- threshold_percentile_50 (L8243)
- threshold_percentile_55 (L8247)
- threshold_percentile_60 (L8251)
- threshold_percentile_65 (L8255)
- threshold_percentile_70 (L8259)
- threshold_percentile_75 (L8263)
- threshold_percentile_80 (L8267)
- threshold_percentile_85 (L8271)
- threshold_percentile_90 (L8275)
- threshold_percentile_95 (L8279)
- morph_open_1 (L8283)
- morph_close_1 (L8286)
- morph_open_2 (L8289)
- morph_close_2 (L8292)
- morph_open_3 (L8295)
- morph_close_3 (L8298)
- morph_open_4 (L8301)
- morph_close_4 (L8304)
- morph_open_5 (L8307)
- morph_close_5 (L8310)
- morph_open_6 (L8313)
- morph_close_6 (L8316)
- morph_open_7 (L8319)
- morph_close_7 (L8322)
- morph_open_8 (L8325)
- morph_close_8 (L8328)
- morph_open_9 (L8331)
- morph_close_9 (L8334)
- morph_open_10 (L8337)
- morph_close_10 (L8340)
- morph_open_11 (L8343)
- morph_close_11 (L8346)
- morph_open_12 (L8349)
- morph_close_12 (L8352)
- morph_open_13 (L8355)
- morph_close_13 (L8358)
- morph_open_14 (L8361)
- morph_close_14 (L8364)
- morph_open_15 (L8367)
- morph_close_15 (L8370)
- morph_open_16 (L8373)
- morph_close_16 (L8376)
- morph_open_17 (L8379)
- morph_close_17 (L8382)
- morph_open_18 (L8385)
- morph_close_18 (L8388)
- morph_open_19 (L8391)
- morph_close_19 (L8394)
- morph_open_20 (L8397)
- morph_close_20 (L8400)
- morph_open_21 (L8403)
- morph_close_21 (L8406)
- morph_open_22 (L8409)
- morph_close_22 (L8412)
- morph_open_23 (L8415)
- morph_close_23 (L8418)
- morph_open_24 (L8421)
- morph_close_24 (L8424)
- morph_open_25 (L8427)
- morph_close_25 (L8430)
- morph_open_26 (L8433)
- morph_close_26 (L8436)
- morph_open_27 (L8439)
- morph_close_27 (L8442)
- morph_open_28 (L8445)
- morph_close_28 (L8448)
- morph_open_29 (L8451)
- morph_close_29 (L8454)
- morph_open_30 (L8457)
- morph_close_30 (L8460)
- moffat_psf_size_5 (L8463)
- gaussian_psf_size_5 (L8466)
- moffat_psf_size_7 (L8469)
- gaussian_psf_size_7 (L8472)
- moffat_psf_size_9 (L8475)
- gaussian_psf_size_9 (L8478)
- moffat_psf_size_11 (L8481)
- gaussian_psf_size_11 (L8484)
- moffat_psf_size_13 (L8487)
- gaussian_psf_size_13 (L8490)
- moffat_psf_size_15 (L8493)
- gaussian_psf_size_15 (L8496)
- moffat_psf_size_17 (L8499)
- gaussian_psf_size_17 (L8502)
- moffat_psf_size_19 (L8505)
- gaussian_psf_size_19 (L8508)
- moffat_psf_size_21 (L8511)
- gaussian_psf_size_21 (L8514)
- moffat_psf_size_23 (L8517)
- gaussian_psf_size_23 (L8520)
- moffat_psf_size_25 (L8523)
- gaussian_psf_size_25 (L8526)
- moffat_psf_size_27 (L8529)
- gaussian_psf_size_27 (L8532)
- moffat_psf_size_29 (L8535)
- gaussian_psf_size_29 (L8538)
- moffat_psf_size_31 (L8541)
- gaussian_psf_size_31 (L8544)
- moffat_psf_size_33 (L8547)
- gaussian_psf_size_33 (L8550)
- moffat_psf_size_35 (L8553)
- gaussian_psf_size_35 (L8556)
- moffat_psf_size_37 (L8559)
- gaussian_psf_size_37 (L8562)
- moffat_psf_size_39 (L8565)
- gaussian_psf_size_39 (L8568)
- moffat_psf_size_41 (L8571)
- gaussian_psf_size_41 (L8574)
- moffat_psf_size_43 (L8577)
- gaussian_psf_size_43 (L8580)
- moffat_psf_size_45 (L8583)
- gaussian_psf_size_45 (L8586)
- moffat_psf_size_47 (L8589)
- gaussian_psf_size_47 (L8592)
- moffat_psf_size_49 (L8595)
- gaussian_psf_size_49 (L8598)
- moffat_psf_size_51 (L8601)
- gaussian_psf_size_51 (L8604)
- moffat_psf_size_53 (L8607)
- gaussian_psf_size_53 (L8610)
- moffat_psf_size_55 (L8613)
- gaussian_psf_size_55 (L8616)
- moffat_psf_size_57 (L8619)
- gaussian_psf_size_57 (L8622)
- moffat_psf_size_59 (L8625)
- gaussian_psf_size_59 (L8628)
- moffat_psf_size_61 (L8631)
- gaussian_psf_size_61 (L8634)
- moffat_psf_size_63 (L8637)
- gaussian_psf_size_63 (L8640)
- rl_deconv_1iter (L8643)
- rl_deconv_2iter (L8646)
- rl_deconv_3iter (L8649)
- rl_deconv_4iter (L8652)
- rl_deconv_5iter (L8655)
- rl_deconv_6iter (L8658)
- rl_deconv_7iter (L8661)
- rl_deconv_8iter (L8664)
- rl_deconv_9iter (L8667)
- rl_deconv_10iter (L8670)
- rl_deconv_11iter (L8673)
- rl_deconv_12iter (L8676)
- rl_deconv_13iter (L8679)
- rl_deconv_14iter (L8682)
- rl_deconv_15iter (L8685)
- rl_deconv_16iter (L8688)
- rl_deconv_17iter (L8691)
- rl_deconv_18iter (L8694)
- rl_deconv_19iter (L8697)
- rl_deconv_20iter (L8700)
- rl_deconv_21iter (L8703)
- rl_deconv_22iter (L8706)
- rl_deconv_23iter (L8709)
- rl_deconv_24iter (L8712)
- rl_deconv_25iter (L8715)
- rl_deconv_26iter (L8718)
- rl_deconv_27iter (L8721)
- rl_deconv_28iter (L8724)
- rl_deconv_29iter (L8727)
- rl_deconv_30iter (L8730)
- rl_deconv_31iter (L8733)
- rl_deconv_32iter (L8736)
- rl_deconv_33iter (L8739)
- rl_deconv_34iter (L8742)
- rl_deconv_35iter (L8745)
- rl_deconv_36iter (L8748)
- rl_deconv_37iter (L8751)
- rl_deconv_38iter (L8754)
- rl_deconv_39iter (L8757)
- rl_deconv_40iter (L8760)
- process_existing_rgb_fits (L8763)
- quick_measure_path (L8781)
- api_list (L8817)
- monte_carlo_centroid_stability (L8824)
- mc_case_000 (L8844)
- mc_case_001 (L8847)
- mc_case_002 (L8850)
- mc_case_003 (L8853)
- mc_case_004 (L8856)
- mc_case_005 (L8859)
- mc_case_006 (L8862)
- mc_case_007 (L8865)
- mc_case_008 (L8868)
- mc_case_009 (L8871)
- mc_case_010 (L8874)
- mc_case_011 (L8877)
- mc_case_012 (L8880)
- mc_case_013 (L8883)
- mc_case_014 (L8886)
- mc_case_015 (L8889)
- mc_case_016 (L8892)
- mc_case_017 (L8895)
- mc_case_018 (L8898)
- mc_case_019 (L8901)
- mc_case_020 (L8904)
- mc_case_021 (L8907)
- mc_case_022 (L8910)
- mc_case_023 (L8913)
- mc_case_024 (L8916)
- mc_case_025 (L8919)
- mc_case_026 (L8922)
- mc_case_027 (L8925)
- mc_case_028 (L8928)
- mc_case_029 (L8931)
- mc_case_030 (L8934)
- mc_case_031 (L8937)
- mc_case_032 (L8940)
- mc_case_033 (L8943)
- mc_case_034 (L8946)
- mc_case_035 (L8949)
- mc_case_036 (L8952)
- mc_case_037 (L8955)
- mc_case_038 (L8958)
- mc_case_039 (L8961)
- mc_case_040 (L8964)
- mc_case_041 (L8967)
- mc_case_042 (L8970)
- mc_case_043 (L8973)
- mc_case_044 (L8976)
- mc_case_045 (L8979)
- mc_case_046 (L8982)
- mc_case_047 (L8985)
- mc_case_048 (L8988)
- mc_case_049 (L8991)
- mc_case_050 (L8994)
- mc_case_051 (L8997)
- mc_case_052 (L9000)
- mc_case_053 (L9003)
- mc_case_054 (L9006)
- mc_case_055 (L9009)
- mc_case_056 (L9012)
- mc_case_057 (L9015)
- mc_case_058 (L9018)
- mc_case_059 (L9021)
- mc_case_060 (L9024)
- mc_case_061 (L9027)
- mc_case_062 (L9030)
- mc_case_063 (L9033)
- mc_case_064 (L9036)
- mc_case_065 (L9039)
- mc_case_066 (L9042)
- mc_case_067 (L9045)
- mc_case_068 (L9048)
- mc_case_069 (L9051)
- mc_case_070 (L9054)
- mc_case_071 (L9057)
- mc_case_072 (L9060)
- mc_case_073 (L9063)
- mc_case_074 (L9066)
- mc_case_075 (L9069)
- mc_case_076 (L9072)
- mc_case_077 (L9075)
- mc_case_078 (L9078)
- mc_case_079 (L9081)
- mc_case_080 (L9084)
- mc_case_081 (L9087)
- mc_case_082 (L9090)
- mc_case_083 (L9093)
- mc_case_084 (L9096)
- mc_case_085 (L9099)
- mc_case_086 (L9102)
- mc_case_087 (L9105)
- mc_case_088 (L9108)
- mc_case_089 (L9111)
- mc_case_090 (L9114)
- mc_case_091 (L9117)
- mc_case_092 (L9120)
- mc_case_093 (L9123)
- mc_case_094 (L9126)
- mc_case_095 (L9129)
- mc_case_096 (L9132)
- mc_case_097 (L9135)
- mc_case_098 (L9138)
- mc_case_099 (L9141)
- mc_case_100 (L9144)
- mc_case_101 (L9147)
- mc_case_102 (L9150)
- mc_case_103 (L9153)
- mc_case_104 (L9156)
- mc_case_105 (L9159)
- mc_case_106 (L9162)
- mc_case_107 (L9165)
- mc_case_108 (L9168)
- mc_case_109 (L9171)
- mc_case_110 (L9174)
- mc_case_111 (L9177)
- mc_case_112 (L9180)
- mc_case_113 (L9183)
- mc_case_114 (L9186)
- mc_case_115 (L9189)
- mc_case_116 (L9192)
- mc_case_117 (L9195)
- mc_case_118 (L9198)
- mc_case_119 (L9201)
- mc_case_120 (L9204)
- mc_case_121 (L9207)
- mc_case_122 (L9210)
- mc_case_123 (L9213)
- mc_case_124 (L9216)
- mc_case_125 (L9219)
- mc_case_126 (L9222)
- mc_case_127 (L9225)
- mc_case_128 (L9228)
- mc_case_129 (L9231)
- mc_case_130 (L9234)
- mc_case_131 (L9237)
- mc_case_132 (L9240)
- mc_case_133 (L9243)
- mc_case_134 (L9246)
- mc_case_135 (L9249)
- mc_case_136 (L9252)
- mc_case_137 (L9255)
- mc_case_138 (L9258)
- mc_case_139 (L9261)
- mc_case_140 (L9264)
- mc_case_141 (L9267)
- mc_case_142 (L9270)
- mc_case_143 (L9273)
- mc_case_144 (L9276)
- mc_case_145 (L9279)
- mc_case_146 (L9282)
- mc_case_147 (L9285)
- mc_case_148 (L9288)
- mc_case_149 (L9291)
- mc_case_150 (L9294)
- mc_case_151 (L9297)
- mc_case_152 (L9300)
- mc_case_153 (L9303)
- mc_case_154 (L9306)
- mc_case_155 (L9309)
- mc_case_156 (L9312)
- mc_case_157 (L9315)
- mc_case_158 (L9318)
- mc_case_159 (L9321)
- mc_case_160 (L9324)
- mc_case_161 (L9327)
- mc_case_162 (L9330)
- mc_case_163 (L9333)
- mc_case_164 (L9336)
- mc_case_165 (L9339)
- mc_case_166 (L9342)
- mc_case_167 (L9345)
- mc_case_168 (L9348)
- mc_case_169 (L9351)
- mc_case_170 (L9354)
- mc_case_171 (L9357)
- mc_case_172 (L9360)
- mc_case_173 (L9363)
- mc_case_174 (L9366)
- mc_case_175 (L9369)
- mc_case_176 (L9372)
- mc_case_177 (L9375)
- mc_case_178 (L9378)
- mc_case_179 (L9381)
- mc_case_180 (L9384)
- mc_case_181 (L9387)
- mc_case_182 (L9390)
- mc_case_183 (L9393)
- mc_case_184 (L9396)
- mc_case_185 (L9399)
- mc_case_186 (L9402)
- mc_case_187 (L9405)
- mc_case_188 (L9408)
- mc_case_189 (L9411)
- mc_case_190 (L9414)
- mc_case_191 (L9417)
- mc_case_192 (L9420)
- mc_case_193 (L9423)
- mc_case_194 (L9426)
- mc_case_195 (L9429)
- mc_case_196 (L9432)
- mc_case_197 (L9435)
- mc_case_198 (L9438)
- mc_case_199 (L9441)
- highpass_sigma_1 (L9446)
- highpass_sigma_2 (L9449)
- highpass_sigma_3 (L9452)
- highpass_sigma_4 (L9455)
- highpass_sigma_5 (L9458)
- highpass_sigma_6 (L9461)
- highpass_sigma_7 (L9464)
- highpass_sigma_8 (L9467)
- highpass_sigma_9 (L9470)
- highpass_sigma_10 (L9473)
- highpass_sigma_11 (L9476)
- highpass_sigma_12 (L9479)
- highpass_sigma_13 (L9482)
- highpass_sigma_14 (L9485)
- highpass_sigma_15 (L9488)
- highpass_sigma_16 (L9491)
- highpass_sigma_17 (L9494)
- highpass_sigma_18 (L9497)
- highpass_sigma_19 (L9500)
- highpass_sigma_20 (L9503)
- highpass_sigma_21 (L9506)
- highpass_sigma_22 (L9509)
- highpass_sigma_23 (L9512)
- highpass_sigma_24 (L9515)
- highpass_sigma_25 (L9518)
- highpass_sigma_26 (L9521)
- highpass_sigma_27 (L9524)
- highpass_sigma_28 (L9527)
- highpass_sigma_29 (L9530)
- highpass_sigma_30 (L9533)
- highpass_sigma_31 (L9536)
- highpass_sigma_32 (L9539)
- highpass_sigma_33 (L9542)
- highpass_sigma_34 (L9545)
- highpass_sigma_35 (L9548)
- highpass_sigma_36 (L9551)
- highpass_sigma_37 (L9554)
- highpass_sigma_38 (L9557)
- highpass_sigma_39 (L9560)
- highpass_sigma_40 (L9563)
- highpass_sigma_41 (L9566)
- highpass_sigma_42 (L9569)
- highpass_sigma_43 (L9572)
- highpass_sigma_44 (L9575)
- highpass_sigma_45 (L9578)
- highpass_sigma_46 (L9581)
- highpass_sigma_47 (L9584)
- highpass_sigma_48 (L9587)
- highpass_sigma_49 (L9590)
- highpass_sigma_50 (L9593)
- highpass_sigma_51 (L9596)
- highpass_sigma_52 (L9599)
- highpass_sigma_53 (L9602)
- highpass_sigma_54 (L9605)
- highpass_sigma_55 (L9608)
- highpass_sigma_56 (L9611)
- highpass_sigma_57 (L9614)
- highpass_sigma_58 (L9617)
- highpass_sigma_59 (L9620)
- highpass_sigma_60 (L9623)
- highpass_sigma_61 (L9626)
- highpass_sigma_62 (L9629)
- highpass_sigma_63 (L9632)
- highpass_sigma_64 (L9635)
- highpass_sigma_65 (L9638)
- highpass_sigma_66 (L9641)
- highpass_sigma_67 (L9644)
- highpass_sigma_68 (L9647)
- highpass_sigma_69 (L9650)
- highpass_sigma_70 (L9653)
- highpass_sigma_71 (L9656)
- highpass_sigma_72 (L9659)
- highpass_sigma_73 (L9662)
- highpass_sigma_74 (L9665)
- highpass_sigma_75 (L9668)
- highpass_sigma_76 (L9671)
- highpass_sigma_77 (L9674)
- highpass_sigma_78 (L9677)
- highpass_sigma_79 (L9680)
- highpass_sigma_80 (L9683)
- highpass_sigma_81 (L9686)
- highpass_sigma_82 (L9689)
- highpass_sigma_83 (L9692)
- highpass_sigma_84 (L9695)
- highpass_sigma_85 (L9698)
- highpass_sigma_86 (L9701)
- highpass_sigma_87 (L9704)
- highpass_sigma_88 (L9707)
- highpass_sigma_89 (L9710)
- highpass_sigma_90 (L9713)
- highpass_sigma_91 (L9716)
- highpass_sigma_92 (L9719)
- highpass_sigma_93 (L9722)
- highpass_sigma_94 (L9725)
- highpass_sigma_95 (L9728)
- highpass_sigma_96 (L9731)
- highpass_sigma_97 (L9734)
- highpass_sigma_98 (L9737)
- highpass_sigma_99 (L9740)
- highpass_sigma_100 (L9743)
- highpass_sigma_101 (L9746)
- highpass_sigma_102 (L9749)
- highpass_sigma_103 (L9752)
- highpass_sigma_104 (L9755)
- highpass_sigma_105 (L9758)
- highpass_sigma_106 (L9761)
- highpass_sigma_107 (L9764)
- highpass_sigma_108 (L9767)
- highpass_sigma_109 (L9770)
- highpass_sigma_110 (L9773)
- highpass_sigma_111 (L9776)
- highpass_sigma_112 (L9779)
- highpass_sigma_113 (L9782)
- highpass_sigma_114 (L9785)
- highpass_sigma_115 (L9788)
- highpass_sigma_116 (L9791)
- highpass_sigma_117 (L9794)
- highpass_sigma_118 (L9797)
- highpass_sigma_119 (L9800)
- highpass_sigma_120 (L9803)
- highpass_sigma_121 (L9806)
- highpass_sigma_122 (L9809)
- highpass_sigma_123 (L9812)
- highpass_sigma_124 (L9815)
- highpass_sigma_125 (L9818)
- highpass_sigma_126 (L9821)
- highpass_sigma_127 (L9824)
- highpass_sigma_128 (L9827)
- highpass_sigma_129 (L9830)
- highpass_sigma_130 (L9833)
- highpass_sigma_131 (L9836)
- highpass_sigma_132 (L9839)
- highpass_sigma_133 (L9842)
- highpass_sigma_134 (L9845)
- highpass_sigma_135 (L9848)
- highpass_sigma_136 (L9851)
- highpass_sigma_137 (L9854)
- highpass_sigma_138 (L9857)
- highpass_sigma_139 (L9860)
- highpass_sigma_140 (L9863)
- highpass_sigma_141 (L9866)
- highpass_sigma_142 (L9869)
- highpass_sigma_143 (L9872)
- highpass_sigma_144 (L9875)
- highpass_sigma_145 (L9878)
- highpass_sigma_146 (L9881)
- highpass_sigma_147 (L9884)
- highpass_sigma_148 (L9887)
- highpass_sigma_149 (L9890)
- highpass_sigma_150 (L9893)
- highpass_sigma_151 (L9896)
- highpass_sigma_152 (L9899)
- highpass_sigma_153 (L9902)
- highpass_sigma_154 (L9905)
- highpass_sigma_155 (L9908)
- highpass_sigma_156 (L9911)
- highpass_sigma_157 (L9914)
- highpass_sigma_158 (L9917)
- highpass_sigma_159 (L9920)
- highpass_sigma_160 (L9923)
- highpass_sigma_161 (L9926)
- highpass_sigma_162 (L9929)
- highpass_sigma_163 (L9932)
- highpass_sigma_164 (L9935)
- highpass_sigma_165 (L9938)
- highpass_sigma_166 (L9941)
- highpass_sigma_167 (L9944)
- highpass_sigma_168 (L9947)
- highpass_sigma_169 (L9950)
- highpass_sigma_170 (L9953)
- highpass_sigma_171 (L9956)
- highpass_sigma_172 (L9959)
- highpass_sigma_173 (L9962)
- highpass_sigma_174 (L9965)
- highpass_sigma_175 (L9968)
- highpass_sigma_176 (L9971)
- highpass_sigma_177 (L9974)
- highpass_sigma_178 (L9977)
- highpass_sigma_179 (L9980)
- highpass_sigma_180 (L9983)
- highpass_sigma_181 (L9986)
- highpass_sigma_182 (L9989)
- highpass_sigma_183 (L9992)
- highpass_sigma_184 (L9995)
- highpass_sigma_185 (L9998)
- highpass_sigma_186 (L10001)
- highpass_sigma_187 (L10004)
- highpass_sigma_188 (L10007)
- highpass_sigma_189 (L10010)
- highpass_sigma_190 (L10013)
- highpass_sigma_191 (L10016)
- highpass_sigma_192 (L10019)
- highpass_sigma_193 (L10022)
- highpass_sigma_194 (L10025)
- highpass_sigma_195 (L10028)
- highpass_sigma_196 (L10031)
- highpass_sigma_197 (L10034)
- highpass_sigma_198 (L10037)
- highpass_sigma_199 (L10040)
- highpass_sigma_200 (L10043)
- normalize_clip_lo1 (L10046)
- normalize_clip_lo2 (L10049)
- normalize_clip_lo3 (L10052)
- normalize_clip_lo4 (L10055)
- normalize_clip_lo5 (L10058)
- normalize_clip_lo6 (L10061)
- normalize_clip_lo7 (L10064)
- normalize_clip_lo8 (L10067)
- normalize_clip_lo9 (L10070)
- normalize_clip_lo10 (L10073)
- normalize_clip_lo11 (L10076)
- normalize_clip_lo12 (L10079)
- normalize_clip_lo13 (L10082)
- normalize_clip_lo14 (L10085)
- normalize_clip_lo15 (L10088)
- normalize_clip_lo16 (L10091)
- normalize_clip_lo17 (L10094)
- normalize_clip_lo18 (L10097)
- normalize_clip_lo19 (L10100)
- normalize_clip_lo20 (L10103)
- normalize_clip_lo21 (L10106)
- normalize_clip_lo22 (L10109)
- normalize_clip_lo23 (L10112)
- normalize_clip_lo24 (L10115)
- normalize_clip_lo25 (L10118)
- normalize_clip_lo26 (L10121)
- normalize_clip_lo27 (L10124)
- normalize_clip_lo28 (L10127)
- normalize_clip_lo29 (L10130)
- normalize_clip_lo30 (L10133)
- normalize_clip_lo31 (L10136)
- normalize_clip_lo32 (L10139)
- normalize_clip_lo33 (L10142)
- normalize_clip_lo34 (L10145)
- normalize_clip_lo35 (L10148)
- normalize_clip_lo36 (L10151)
- normalize_clip_lo37 (L10154)
- normalize_clip_lo38 (L10157)
- normalize_clip_lo39 (L10160)
- normalize_clip_lo40 (L10163)
- normalize_clip_lo41 (L10166)
- normalize_clip_lo42 (L10169)
- normalize_clip_lo43 (L10172)
- normalize_clip_lo44 (L10175)
- normalize_clip_lo45 (L10178)
- normalize_clip_lo46 (L10181)
- normalize_clip_lo47 (L10184)
- normalize_clip_lo48 (L10187)
- normalize_clip_lo49 (L10190)
- normalize_clip_lo50 (L10193)
- normalize_clip_lo51 (L10196)
- normalize_clip_lo52 (L10199)
- normalize_clip_lo53 (L10202)
- normalize_clip_lo54 (L10205)
- normalize_clip_lo55 (L10208)
- normalize_clip_lo56 (L10211)
- normalize_clip_lo57 (L10214)
- normalize_clip_lo58 (L10217)
- normalize_clip_lo59 (L10220)
- normalize_clip_lo60 (L10223)
- normalize_clip_lo61 (L10226)
- normalize_clip_lo62 (L10229)
- normalize_clip_lo63 (L10232)
- normalize_clip_lo64 (L10235)
- normalize_clip_lo65 (L10238)
- normalize_clip_lo66 (L10241)
- normalize_clip_lo67 (L10244)
- normalize_clip_lo68 (L10247)
- normalize_clip_lo69 (L10250)
- normalize_clip_lo70 (L10253)
- normalize_clip_lo71 (L10256)
- normalize_clip_lo72 (L10259)
- normalize_clip_lo73 (L10262)
- normalize_clip_lo74 (L10265)
- normalize_clip_lo75 (L10268)
- normalize_clip_lo76 (L10271)
- normalize_clip_lo77 (L10274)
- normalize_clip_lo78 (L10277)
- normalize_clip_lo79 (L10280)
- normalize_clip_lo80 (L10283)
- normalize_clip_lo81 (L10286)
- normalize_clip_lo82 (L10289)
- normalize_clip_lo83 (L10292)
- normalize_clip_lo84 (L10295)
- normalize_clip_lo85 (L10298)
- normalize_clip_lo86 (L10301)
- normalize_clip_lo87 (L10304)
- normalize_clip_lo88 (L10307)
- normalize_clip_lo89 (L10310)
- normalize_clip_lo90 (L10313)
- normalize_clip_lo91 (L10316)
- normalize_clip_lo92 (L10319)
- normalize_clip_lo93 (L10322)
- normalize_clip_lo94 (L10325)
- normalize_clip_lo95 (L10328)
- normalize_clip_lo96 (L10331)
- normalize_clip_lo97 (L10334)
- normalize_clip_lo98 (L10337)
- normalize_clip_lo99 (L10340)
- normalize_clip_lo100 (L10343)

PipelineConfig.from_yaml_like: Minimal YAML-like key: value loader (no PyYAML required).

## B.hard_synth_suite

File app/hard_synth_suite.py — 384 lines

Classes:
- StressCase (L46): to_dict
- StressResult (L56): to_dict

Top-level functions:

- _blur (L76)
- apply_image_stress (L86)
- run_one_measure (L102)
- default_stress_matrix (L144)
- run_hard_synth_suite (L170)

## B.license_manager

File app/license_manager.py — 455 lines

Classes:
- LicenseStatus (L165): __post_init__, to_dict

Top-level functions:

- using_default_secret (L124)
- _secret (L129)
- machine_fingerprint (L134)
- _b32ish (L145)
- _sign (L158)
- generate_key (L186)
- parse_and_verify (L232)
- license_path (L295)
- save_license (L299)
- load_status (L316)
- status_from_fields (L343)
- require_feature (L378)
- assert_feature (L415)
- vendor_generate_batch (L421)

## B.limb_validation

File app/limb_validation.py — 124 lines

Top-level functions:

- _sky_ok (L25)
- run_one (L36)
- main (L96)

## B.multi_epoch

File app/multi_epoch.py — 494 lines

Classes:
- EpochMeasure (L42): to_dict
- DifferentialSeries (L64): to_dict

Top-level functions:

- _t_seconds (L83)
- epoch_from_research_json (L93)
- load_epochs_from_dir (L189)
- load_epochs_from_list (L212)
- weighted_linear_fit (L235)
- kalman_rts_1d (L263)
- build_differential_series (L309)
- measure_epoch_image (L420)
- write_multi_epoch_report (L465)

## B.nasa_compare

File app/nasa_compare.py — 262 lines

Classes:
- NASAComparison (L33): to_dict, grade

Top-level functions:

- _ssl_context (L21)
- grs_reference_model (L57)
- fetch_horizons (L89)
- compare_measurement_to_nasa (L167)
- write_comparison_report (L235)

## B.nn_grs

File app/nn_grs.py — 1694 lines

Classes:
- SpireNet (L476): create, _pad_same, forward, predict_lonlat, save, load

Top-level functions:

- _app_dir (L40)
- _resolve_model_paths (L44)
- _train_cache_dir (L57)
- _train_log_dir (L68)
- _atomic_write_text (L79)
- _atomic_savez (L99)
- weights_are_finite (L132)
- snapshot_weights (L145)
- restore_weights (L149)
- good_weights_path (L154)
- save_good_backup (L162)
- restore_from_good_backup (L177)
- start_prevent_sleep (L201)
- stop_prevent_sleep (L243)
- _write_live_report (L264)
- _emergency_flush (L315)
- _install_save_handlers (L336)
- _relu (L373)
- _relu_bwd (L377)
- _sigmoid (L381)
- conv2d (L385)
- conv2d_fast (L405)
- maxpool2 (L430)
- maxpool2_bwd (L446)
- conv2d_bwd (L459)
- _resize_map (L687)
- map_to_nn_input (L709)
- truth_to_targets (L716)
- get_train_status (L742)
- _sgd_step (L776)
- rng_noise (L857)
- auto_train (L861)
- _checkpoint_path (L1148)
- _save_checkpoint (L1152)
- _load_checkpoint (L1160)
- _inject_weight_noise (L1170)
- _reinit_heads (L1178)
- _make_train_sample (L1190)
- overnight_train (L1239)
- durable_background_train (L1631)
- request_train_stop (L1675)
- predict_soft_prior (L1680)

SpireNet.forward: x: (1,H,W) or (H,W) normalized ~0..1 returns heatmap (8,16), coords (2,) in [0,1] for (x_frac, y_frac)

SpireNet.predict_lonlat: Map network output to planetocentric lon/lat (map is lon_rel -90..90, lat 90..-90).

SpireNet.save: Atomic durable save — refuses NaN/Inf; never overwrites good weights with corrupt ones.

## B.paths

File app/paths.py — 187 lines

Top-level functions:

- _frozen (L27)
- code_dir (L31)
- data_dir (L37)
- model_dir (L57)
- bundled_model_dir (L69)
- ensure_models_present (L74)
- owner_log_dir (L132)
- owner_shared_dir (L142)
- outputs_dir (L158)
- ensure_tree (L164)

## B.precision_engine

File app/precision_engine.py — 1065 lines

Classes:
- NavState (L37): b_pol_px
- GRSPrecisionResult (L53): to_dict

Top-level functions:

- deg2rad (L72)
- rad2deg (L76)
- wrap_deg (L80)
- wrap_diff (L84)
- km_per_deg_lon (L88)
- km_per_deg_lat (L92)
- deg_to_arcsec_on_sky (L96)
- sky_error_arcsec (L103)
- _gauss (L109)
- to_mono (L124)
- rough_disk_mask (L137)
- fit_limb_nav (L155)
- px_to_lonlat (L262)
- make_cylindrical (L296)
- _template_match_grs (L343)
- _moment_mask_grs (L483)
- _map_dark_centroid (L577)
- _method_is_sane (L629)
- _choose_size (L648)
- _circular_weighted_mean (L670)
- measure_grs_precision (L679)
- monte_carlo_precision (L939)
- cap_mc_iterations (L1040)

## B.product_core

File app/product_core.py — 341 lines

Classes:
- ProductInfo (L38): to_dict

Top-level functions:

- product_version (L25)
- default_out_root (L50)
- process_image (L56)
- generate_synthetic (L103)
- resolve_ephemeris (L179)
- certify (L192)

## B.publish_primary

File app/publish_primary.py — 267 lines

Top-level functions:

- _f (L29)
- assess_winjupos_equality (L37)
- apply_publish_policy (L106)
- format_publish_section (L239)

## B.ram_ssd

File app/ram_ssd.py — 123 lines

Top-level functions:

- bytes_gb (L32)
- estimate_rgb_gb (L36)
- choose_max_resolution (L41)
- ssd_temp_path (L73)
- memmap_zeros (L78)
- array_to_ssd (L85)
- load_ssd (L91)
- free_memory (L95)
- cleanup_ssd_cache (L99)
- recommend_mc_iterations (L114)

## B.research_grade

File app/research_grade.py — 740 lines

Classes:
- DefinitionResult (L71): to_dict
- InjectionTrial (L85): to_dict
- ResearchGradeResult (L99): to_dict

Top-level functions:

- _hash_array (L132)
- inject_dark_oval (L137)
- run_definition_suite (L182)
- consensus_from_definitions (L237)
- _recover_near_lonlat (L287)
- blind_injection_calibration (L353)
- filter_closure_rgb (L424)
- run_research_grade (L486)
- write_publication_bundle (L712)

## B.result_report

File app/result_report.py — 781 lines

Top-level functions:

- _f (L40)
- _s (L52)
- _line (L56)
- _section (L61)
- _box (L66)
- _pull_measured (L85)
- _format_nasa_block (L169)
- _format_truth_block (L307)
- format_human_report (L399)
- write_human_report (L770)
- format_nasa_txt (L778)

## B.security_hard

File app/security_hard.py — 218 lines

Classes:
- SecurityError (L54): 

Top-level functions:

- rate_limit_ok (L65)
- sanitize_filename (L81)
- has_traversal (L97)
- safe_resolve_under (L110)
- safe_upload_extension (L137)
- assert_safe_process_path (L144)
- host_allowed (L158)
- strip_control_chars (L173)
- security_headers (L178)
- data_roots (L198)

## B.server

File app/server.py — 1664 lines

Top-level functions:

- _wj_manual_from_data (L67)
- _run_gold (L82)
- _security_before (L210)
- _no_cache_static (L227)
- _start (L245)
- _finish (L260)
- _find_output_dir (L267)
- _attach_human_report (L283)
- index (L317)
- health (L322)
- logs (L352)
- logs_clear (L357)
- verbose (L363)
- job (L370)
- regions (L376)
- countries (L388)
- tips (L394)
- resolutions (L399)
- nn_status (L409)
- nn_train (L414)
- nn_stop (L470)
- upload (L476)
- process (L519)
- synthetic (L779)
- api_ephemeris (L1000)
- winjupos_template (L1031)
- winjupos_upload (L1037)
- api_multi_epoch (L1050)
- api_hard_synth (L1115)
- capabilities (L1159)
- api_factory_night (L1196)
- output_file (L1596)
- file_api (L1617)
- main (L1644)

## B.sota_accuracy

File app/sota_accuracy.py — 1192 lines

Classes:
- SOTAResult (L105): to_dict

Top-level functions:

- _mad (L132)
- _circular_median (L140)
- _circular_weighted_mean (L151)
- is_centre_method (L164)
- base_weight (L173)
- is_map_edge_lock (L180)
- _near_pipeline (L194)
- _cluster_centres (L200)
- _score_cluster (L230)
- robust_consensus (L285)
- _grade_from_score (L568)
- assess_quality (L601)
- extract_fits_time (L818)
- run_sota_accuracy (L889)
- apply_sota_to_package (L1034)
- format_sota_section (L1167)

## B.spice_auto

File app/spice_auto.py — 540 lines

Classes:
- SpiceStatus (L98): to_dict
- SpiceGeometry (L113): to_dict

Top-level functions:

- _ssl_context (L133)
- _sha256_file (L144)
- has_spiceypy (L159)
- kernel_dir (L167)
- _existing_kernel (L173)
- _download (L195)
- ensure_kernels (L235)
- list_local_kernels (L329)
- _furnsh_all (L339)
- wrap_deg (L357)
- compute_spice_geometry (L361)
- selftest (L522)

## B.synthetic_hq

File app/synthetic_hq.py — 682 lines

Classes:
- SynthSpec (L56): 

Top-level functions:

- _seed (L88)
- _parse_time (L93)
- random_observation_time (L103)
- _blur (L112)
- _resize_bilinear (L127)
- _value_noise (L153)
- _belt_profile (L176)
- _wavefield (L204)
- _shear_residual (L245)
- _paint_ovals (L257)
- _paint_grs (L296)
- generate (L372)

## B.verbose_log

File app/verbose_log.py — 55 lines

Classes:
- ConsoleLog (L12): __init__, clear, log, info, warn, error, ok, debug, since

Top-level functions:

## B.vlbi_metrology

File app/vlbi_metrology.py — 1747 lines

Classes:
- EphemerisState (L87): to_dict
- AdvancedNav (L109): b_pol_px, to_nav_state, to_dict
- ErrorBudget (L148): to_dict
- VLBIResult (L172): to_dict

Top-level functions:

- planetocentric_to_planetographic (L212)
- time_error_to_lon_sigma (L221)
- _hash_array (L226)
- _rotate_points (L232)
- build_ephemeris_approx (L246)
- enrich_ephemeris_from_horizons (L323)
- fit_limb_advanced (L369)
- make_cylindrical_oriented (L505)
- px_to_lonlat_oriented (L563)
- _ncc_peak (L594)
- multiscale_template_match (L654)
- measure_size_isophote (L749)
- measure_grs_vlbi (L811)
- _local_dark_recover (L943)
- inject_dark_oval_image (L994)
- phase_reference_injection (L1039)
- hierarchical_monte_carlo (L1137)
- definition_suite_vlbi (L1254)
- definition_scatter (L1297)
- filter_closure_vlbi (L1318)
- assemble_formal_budget (L1362)
- optical_diffraction_floor_arcsec (L1435)
- grade_result (L1440)
- run_vlbi_grade (L1460)
- write_vlbi_bundle (L1675)
- research_grade_compat (L1713)

## B.winjupos_twin

File app/winjupos_twin.py — 577 lines

Classes:
- LimbProbe (L56): to_dict
- TwinResult (L71): to_dict

Top-level functions:

- _measure_map_and_bary (L110)
- limb_outline_sensitivity (L129)
- grs_definition_sensitivity (L226)
- run_winjupos_twin (L346)
- format_twin_report (L477)
- attach_winjupos_twin_to_package (L525)

# Appendix C. Glossary

- **System III:** Jupiter longitude system associated with the planet's magnetic/radio rotation (1965).
- **CM III:** Central meridian System III longitude at the observation epoch.
- **GS-MAP:** Map-plane dark centroid; classic fixed publication definition.
- **GS-BARY:** Intensity-weighted dark barycentre; ordered publication fallback.
- **Champion Ultimate:** Automated optical path (`champion_measure.py`) with multi-isophote limb, dark-core scoring, dual-channel and nav-stability tests, and full error budget.
- **UNBEATABLE_AUTO:** Grade when all ultimate automated gates pass; in-app hierarchy lock—not a claim vs HST/Juno/perfect human WinJUPOS.
- **SUPERDUPER:** One-page best-answer card (`superduper.py`) consolidating publish + Champion for the job folder.
- **φ_c / φ_g:** Planetocentric vs planetographic latitude; WinJUPOS-style comparisons should use φ_g.
- **WinJUPOS:** Interactive planetary measurement environment for limb navigation and feature marking.
- **SPICE:** NAIF toolkit and kernels for solar-system geometry.
- **Horizons:** JPL ephemeris service for observer-centric geometric quantities.
- **Limb:** Apparent planetary edge in the image plane.
- **Cylindrical map:** Deprojection of the visible hemisphere to relative longitude ∈ [−90°, +90°] about CM and latitude.
- **Isophote:** Locus of constant intensity; controls automated limb outline size.
- **Definition scatter:** Disagreement among legitimate morphological definitions of the same feature.
- **Multi-estimator catalog / soup:** Large set of classical localization algorithms used for scatter analysis only.
- **SOTA layer:** Robust consensus procedure over the multi-estimator catalog (scatter; must not overwrite GS primary).
- **Truth recovery:** Synthetic-only difference between measured and injected coordinates.
- **Sky arcsecond:** On-sky angular equivalent of planetary lon/lat differentials at the adopted range.
- **SPIRE-Net:** Optional convolutional model distributed with the application (soft prior).
- **Publication policy:** Software hierarchy selecting UNBEATABLE/Champion when gates pass, else GS-MAP/GS-BARY.
- **NavState:** Structure holding disk navigation and geometry parameters (including sub-lat and north PA when applied).

# Appendix D. Extended Technical Notes

## D.1 Physical conversion to sky angle

Longitudinal and latitudinal differentials on the planet are converted to sky arcseconds
using local kilometers per degree and the Earth–Jupiter range. With s_λ and s_φ the metric
scales and D the range,

    σ_sky ≈ hypot( Δλ · s_λ / D , Δφ · s_φ / D )

implemented in spirit by sky_error_arcsec. Reported optical floors combine such geometric
conversions with algorithmic dispersion and, where modeled, resolution terms.

## D.2 Map length scale

Because the cylindrical map spans 180° in longitude, an east–west pixel span Δx corresponds
to Δλ = Δx · (180°/W). Size products that assumed a 360° map width would inflate lengths by
a factor of two; the gold-standard oval pathway uses the 180° convention together with
moment-based widths and physical clamps appropriate to modern GRS scales.

## D.3 Edge versus core observables

West and east edges characterize longitudinal extent. Their midpoint can approximate the
center for a symmetric oval yet diverge under asymmetric masks or residual waves. Archival
series should hold the definition fixed across nights; mixing edge and core products without
label destroys trend interpretability.

## D.4 Reproducibility requirements

Reproduction of a Process product requires the input file, mid-exposure UTC, software
version, central-meridian source and value, and the publication definition. Attachment of
publish.json and pro_ephemeris.json alongside any scientific claim is recommended.

## D.5 Multi-estimator families

Without ranking empirical accuracy, the catalog groups into map methods, threshold masks,
template and correlation matches, isophotal ladders, morphological operators, robust
location statistics, edge and extent measures, spectral cues, and ensembles. Correlation
across families is expected because many operate on related dark residuals in the same
latitude band.

## D.6 Classical human workflow and automation

A conventional sequence—video capture, lucky imaging stack, optional sharpening, interactive
navigation and marking, notebook logging—is only partly internalized here. GRS Observatory
targets stacked or single high-quality frames and automates navigation, localization,
provenance, and packaging. Dedicated stacking applications remain appropriate upstream
tools.

## D.7 Closing remark on method multiplicity

A large estimator count is valuable only when subordinated to a named definition and when
outliers are examined rather than averaged away. The present publication policy and SOTA
filtering encode that principle in software.

------------------------------------------------------------------------

# Addendum (v6.5.0, 2026-07-28) — Bug fixes, P2 pattern remediation, and UI polish

A full line-by-line audit was conducted against the 6.5.0 codebase (41 app/*.py
modules, 10 test files, config files — approximately 32,600 lines total). All
discovered bugs have been fixed:

**P0 (critical — all fixed):**
- publish_primary._cand_score() champion candidate was not preferred over
  GS-MAP because the champion label bonus was missing; now UNBEATABLE_AUTO +50
  and CHAMPION-prefix +35 bonuses are applied.
- winjupos_plus and superduper f-string .Nf format crashed with TypeError
  when variables were None; guards now check all formatted fields before
  rendering.
- Server /api/synthetic path now calls job_finalize for parity with desktop
  Process, producing SUPERDUPER and champion archival products.

**P1 (important — all fixed):**
- Hardcoded fallback version "5.2.0" changed to "6.5.0" in product_core.py.
- Stale User-Agent "GRS-Observatory/6.1" changed to "GRS-Observatory/6.5" in
  server.py.
- Stale __version__ = "6.2.0" changed to "6.5.0" in grs_complete_system.py.
- precision_engine._gauss() broken fallback (returned img unchanged) now
  performs actual FFT box-filter convolution via numpy.fft.fft2/ifft2.
- datetime.now() calls replaced with datetime.now(timezone.utc) across
  desktop_app.py, desktop_pipeline.py, and server.py for reproducible
  timestamps.

**P2 (pattern-level fixes applied to key modules):**
- champion_measure.py: .get() results formatted in f-strings now supply
  default values (e.g. .get('dlon_deg', 0.0)) instead of risking
  None:.3f crash.
- Desktop wiring tests now skip gracefully when tkinter is unavailable.
- Desktop UI polished: refined colour palette, metric card redesign with
  per-card accent headers, improved typography hierarchy, better status
  indicators.

Sections 1-2 and 3.1-3.2 above were revised for Champion Ultimate, SUPERDUPER,
and the current publication hierarchy. Module line-count tables elsewhere in
this essay reflect a static inventory circa 6.1.0 and will not match the
live tree after dead-code removal from grs_complete_system.py and addition of
champion_measure.py, superduper.py, winjupos_plus.py, job_finalize.py,
and related tests. Operators should treat docs/GRS_OBSERVATORY_BOOK.md as the
authoritative short user guide, docs/PLATEAU.md for cannot improve more
inside this app, and this essay as the long-form scientific explanation.

**Recommended archival triple for any claim based on a Process job:**

1. SUPERDUPER_BEST_ANSWER.txt (or .json)
2. champion.txt / champion.json (gates and sigma budget)
3. pro_ephemeris.json (CM provenance)

plus JOB_COMPLETE.json, the source image, mid-exposure UTC, and software
VERSION.

------------------------------------------------------------------------
End of essay. Core narrative revised for software version 6.5.0.
Bug-fix addendum: 2026-07-28.
# v6.9.0 "Analysis Pro" upgrade — plan & measured results

Baseline: v6.8.0 (working tree; full suite re-validated in CI of this branch).
Theme: **the analysis layer AutoStakkert doesn't have and WinJUPOS does by
hand** — rotation-derotated RGB compositing, cloud-tracking wind science,
GRS drift geophysics, stack forensics, limb-darkening, and session planning.
Every number below is measured by the cited test; run it yourself.

## Work items

| # | Feature | Module | Why (state of the art) |
|---|---------|--------|------------------------|
| 1 | Filter-wheel RGB combine with exact ephemeris derotation | `app/rgb_combine.py` | WinJUPOS "RGB combine" parity; AutoStakkert fundamentally cannot do it. Exact spheroid projection (north-PA + sub-Earth-lat safe), wind-adjusted per-parallel cloud rate, band-residual polish |
| 2 | Cloud-tracking wind analysis | `app/wind_analysis.py` | shape-discriminated offsets (uniform m/s advection vs System-III angular error), jet detection, JUPOS-friendly CSV, PNG panel |
| 3 | GRS System-II drift geophysics | `app/grs_drift.py` | sigma-clipped drift fit, F-tested curvature, m/s implied zonal velocity, prediction cone, JUPOS ingest |
| 4 | Stack forensics | `app/stack_report.py` | coverage fill, dither-diversity audit, usage concentration, wander/jump stats, actionable warnings, PNG panel |
| 5 | Filter-wheel production orchestrator | `app/filter_wheel.py` | 3× SER → 3× APS stacks → derotated RGB + artefacts, one call |
| 6 | Session planner | `app/session_planner.py` | exact smear/span budgets, filter-gap limits, derotated-span regime, tonight-composition via transits |
| 7 | Limb-darkening measurement | `app/limb_darkening.py` | band-normalised mu^k fit, per-band k(lat) table; renderer-pinned |
| 8 | Generic displacement-field warp | `app/image_warp.py` (added `warp_field2d`) | spline-resampled per-pixel fields; the rgb_combine engine |

## Physics & engineering findings measured along the way

- **Exact rotation field beats per-row analytic models — and the wind-term
  test showed why tests must respect foreshortening.** The rgb_combine
  sampling grid is built by full spheroid inverse+forward projection. A
  first-cut unit test compared the wind term against the centre-line chord
  (px_per_deg_lon) across whole bands and read 0.864× the model: not a bug —
  the true projection carries a cos(lon_rel) foreshortening factor that the
  centre-line model lacks. The test now checks near-CM pixels where the
  analytic chord is exact (agreement to 2% — `TestRotationField`).
- **Global cross-correlation locks are unsafe for residual polish.** Every
  FFT-peak coarse strategy (phase-only, NCC, tapered-window variants) was
  measured to lock 0.4–1.1 px off on adversarial quasi-sinusoidal band
  texture. The polish is therefore local-only: window-aware Lucas–Kanade
  seeded at (0,0), gated at max_resid_px and a >=2% RMS-improvement gate —
  planted (0.45, −1.25) px recovered as (−0.429, +1.255).
- **Windowed LK must model the window.** Naively multiplying both images by
  the band taper biases dy to zero (planted 0.45 → recovered −0.02);
  `_lk_refine_windowed` puts the window in the gradients
  (ref = w·img(p−c)) and recovers the plant to ~0.02 px.
- **Drizzle "holes" from pixfrac alone cannot exist in our deposit scheme.**
  Drops are side = D·pixfrac bins plus 1-bin overlap wings, so coverage is
  structural. Forensics therefore measures (a) enclosed-hole fill inside the
  coverage footprint and (b) the subpixel *dither-diversity audit* from the
  measured global alignment phases — the physically true starvation signal
  (identical frames: spread 0.000 → warning; real ±1.2 px dither: 0.145).
- **Globally-uniform seeing ⇒ degenerate lucky imaging, and the forensics
  will say so.** On synthetic video with per-frame global blur the same few
  frames win every AP (usage median 0, concentration warning fires). Mass
  conservation (mean per-frame usage == keep_frac) is the pinned invariant.

## Measured results (reproduced by the cited tests)

- **RGB combine, tilted geometry** (`tests/test_rgb_combine.py::test_tilted_geometry_combine`):
  three mono captures 240 s apart (2.419°/hop ≈ 4.86 px at the equator),
  sub-Earth −2.3°, pole PA 18° (both tilted): fringe 0.1927 → **0.0322
  (6.0×)** with polish, 0.042 without (polish adds 23%); channels
  co-registered to < 0.35 px measured channel-on-channel; coverage
  99.9–100%.
- **Filter-wheel end-to-end on SER files** (`tests/test_filter_wheel.py`):
  3× 6-frame SERs with true timestamps, 4-min hops: mid-times recovered
  from SER ticks (±240.0 s in report), re-centre of ±0.6 px applied where
  planted, fringe **0.1069 → 0.0357 (3.0×)**, coverage ≥ 99.9%.
- **Wind analysis** (`tests/test_wind_analysis.py`): uniform +25 m/s
  advection recovered to ±2; planted System-III error 0.8°/day recovered
  to ±0.15 with correct period-correction sign (−P·δω/ω3 exact); shape
  discriminator picks the right model in both directions; planted
  42 m/s jet at 38.7° detected within one bin; **jets never span evidence
  gaps**; empty CSV cells (not zeros) for missing bins.
- **GRS drift** (`tests/test_grs_drift.py`): −0.42°/30d recovered to
  ±0.10 over 18 epochs incl. 0/360 wrap; 2 planted outliers sigma-clipped
  exactly; planted curvature 2.4e-4 °/day² recovered to ±0.8e-4 with
  F-test preference; linear data keeps the linear model; prediction cone
  widens with horizon; velocity conversion hand-checked vs
  surface_parallel_radius_m.
- **Session planner** (`tests/test_session_planner.py`): max_span_s is an
  exact inversion of lon_drift_px (rel err 1e-9); derotated spans are
  ~5–10× raw (wind-residual-limited, shared conventions hand-checked);
  filter direct-composite gap satisfies the smear budget exactly.
- **Limb darkening** (`tests/test_limb_darkening.py`): renderer plants
  μ**0.6 → measured k = **0.653 ± 0.020**; frame divided by the
  renderer's own μ**0.6 → k ≈ 0 (achromatic control); extreme |lat|
  bands honestly absent (no high-μ pixels to fit).
- **Stack forensics** (`tests/test_stack_report.py`): dithered stack
  interior fill = 1.000, dither spread 0.145 px; zero-dither stack fires
  the dither-audit warning; blurred frames de-weighted below the good
  median; planted 30 px frame jump flagged in warnings.

Suite: the v6.9 modules add 57 tests (all green standalone); the full
repo suite is re-run for every landed batch (see CHANGELOG for the
release-state number).

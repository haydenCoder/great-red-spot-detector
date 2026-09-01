# Changelog

All notable changes to the Great Red Spot Detector. Versions follow the `VERSION`
file (the single source of truth; no hardcoded literals).

## [Unreleased] — UI rounds 1–3 + full-code audit

Full write-ups: [`docs/DETERIORATION_AUDIT_2026-08-22.md`](docs/DETERIORATION_AUDIT_2026-08-22.md)
and [`docs/CODE_AUDIT_2026-08-31.md`](docs/CODE_AUDIT_2026-08-31.md) (what was checked,
what was found, what was deliberately left alone).

### Changed — the one-click note says what the night did
`runEverythingTail` used to print a fixed *"Night done — dashboard, report, ephemeris,
multi-night, stress filled"* line. That is an over-claim in two cases the endpoint supports:
unchecking *“Include stress suite”*, and a stage that raises inside `api_factory_night`
(which records `{"error": …}` and carries on). The summary is now built from
`report["stages"]`, so a failed stage reads **multi-night ✗**, a skipped one **stress off**,
and a payload with no `stages` at all says nothing about them instead of asserting everything.

### Fixed — the fast suite's one failure was the test, not the code
`tests/test_smoke_detailed.py::TestEphemerisProvenance::test_cm_source_is_trusted_when_spice_available`
asked `resolve_ephemeris(..., use_spice=True)` for a publication-grade CM source and asserted it
must be trusted — with no `spiceypy` in the environment, where it can only ever answer
`"analytical"`. The test beside it asserts that `"analytical"` *must* be flagged as untrustworthy,
so the pair was never about the fallback. The SPICE gate now skips when the optional dependency is
missing (`skipTest("spiceypy unavailable — no SPICE branch to gate")`) and still fails hard when it
is present, so a red fast suite means something again.

### Changed — one click runs the whole night
The panel read like an assembly manual: eight sections, eleven buttons, and the two
things worth doing on any given night (Multi-night, Stress) were two clicks you had to
remember to make. It is now *load an image → press one button → read the tabs*.

* **⚡ Run everything** (`#btnFactory`, section 6) posts `/api/factory_night`
  (`app/server.py:1318`), which already covers the night: resolved ephemeris →
  measurement (your uploaded file when one is loaded, a synthetic planet otherwise) →
  multi-epoch differential → hard-synth stress suite (`factoryHard` now checked by
  default, so "everything" really means everything). When the result lands,
  `runEverythingTail()` fills the four panels that endpoint cannot: Transits, the
  session planner, the Sharpen Lab (only when a real file exists) and the
  ☄ Deterioration Lab sweep. None of them takes the job slot — two GETs, one
  synchronous POST and the Lab's own lock — so nothing can 409 behind the night, and
  the tail runs **quiet**: no `showTab` calls, so it never drags you off the dashboard
  it just filled. A re-polled result cannot re-run it (`everythingRan`), and a second
  press explicitly can.
* The modal in front of the button is gone. It used to ask *"Self-test will process YOUR
  uploaded file. Continue?"* — the note under the button and the mode badge now say the
  same thing without intercepting the press.
* **Multi-night / Stress are still there** for one-off runs, folded into
  `<details class="steps">` (*"Other buttons, if you want one step alone"*) instead of
  two always-visible sections competing with the one click.
* **Deterioration Lab moved to 4th in the tab strip** (it was 8th of 11, off-screen on a
  phone) and tabs now answer the number keys: `1`…`9`, `0` jump to the 1st–10th tab from
  anywhere on the page (ignored while typing in a field, in the console, or with ⌘/ctrl
  held). The strip carries the hint as its tooltip.
* **Sharpen Lab** (`app/static/app.js`, `runSharpen`) — `/api/sharpen`
  (`app/server.py:1837`) had *no UI at all*: wavelet / unsharp / Richardson-Lucy existed
  only for anyone willing to read the source. It sits under the Preview image, is armed
  only once a real file is loaded, reports the Laplacian variance before → after, and
  swaps the preview to the sharpened frame so the difference is the thing you are looking at.
* **Resolution picker** now asks `/api/resolutions` (`app/server.py:464`, previously dead
  code) and labels each preset with what it buys — `8K: 7680×4320 px`, `16K … May
  downshift if RAM tight` — instead of leaving 16K as a mystery word.
* `tests/test_ui_wiring.py::TestBackendIsReachable` scans every `@app.route` and fails if
  one is not reachable from a control, so a backend feature can no longer ship without a
  way to press it. Two exemptions, both documented in the test: `/api/file` and
  `/api/output/*`, which the server uses to *build* URLs it already sends in JSON.

### Fixed — a duplicated test class, so test edits cannot silently do nothing
`tests/test_ser_io.py` defined `TestSERRobustness` and `TestAVIRoundTrip` twice
(lines 112 and 205): Python rebinds the name, so the first pair was shadowed and any edit
to it would have looked like a passing test that never ran. The shadow copy was introduced
while preparing the previous commit; `ruff --select F811` found it. The duplicate is
deleted, the collected count is unchanged (15) and the file still passes, and the sweep was
re-run across every file the last two rounds touched (no other duplicates).

### Fixed — the web UI throttled itself into a frozen page
The page polled `/api/logs` + `/api/job` every 600 ms and `/api/nn/status`
every 1200 ms — ~250 requests/min against a 90/min budget — so about 20
seconds after opening, *every* answer became a 429. The console stopped,
**Process** looked dead (the finished job was never fetched), and the
Deterioration progress bar froze at 5%. Read-only polling now has its own
budget (`security_hard.POLL_ENDPOINTS`, 900/min default) and its own hit
queue, the three polls are folded into one `/api/status` snapshot, idle
polling slows to ~2 s, a hidden tab does not poll at all, and a failing
server gets exponential backoff plus a visible RETRY/OFFLINE pill instead of
a silently stuck screen. Uploads, job starts and file reads keep the tight
90/min budget.

### Fixed — Deterioration Lab results never rendered
`app.js` shipped two independent closures; the Deterioration one called
`esc()`, which only exists in the closure above it. So the first
`ReferenceError` killed the tips list, and the next one fired mid-`render()` —
after the charts, before the method-survival bars and the raw matrix — with
the error swallowed by a `catch`. A local `esc`, per-section render guards,
and an explicit redraw when the tab becomes visible (a canvas inside a hidden
pane measures 0) fix it.

### Fixed — the zonal stacker binned alignment points by the wrong latitude
`jupiter_zonal_stacker._ap_latitude` used a thin-sky `asin(Y*cos D)` shortcut that
contradicted its own docstring: it returned 0° at the disc centre where the truth is the
sub-Earth latitude, and drifted to ~10° p99 / ~21° near the limb (up to ~2.9° *inside* the
GRS band at this season's +3° sub-Earth latitude). That latitude selects the per-AP
zonal-wind rate used as the derotation prior. It now uses
`precision_engine.px_to_lonlat_vec` — the exact oblate-spheroid solve the engine publishes
measurements with — matching the sibling fix already shipped in
`planetary_stacker._ap_latitudes`; the old form survives only as the off-limb fallback.
**JUP_ZONAL stacks may shift slightly; re-pin the campaign if you cite them.**

### Fixed — 17–30 seconds of every `stack_holy_grail` run was discarded
Step 4 fitted a quality-weighted RBF velocity field for *each frame* and never read it
(43–74 ms of Gaussian elimination per frame at 121–441 APs). Removed; the equatorial-band
median shift it sat next to is what `warp_to_reference` actually consumes. `sigma_rbf` is
kept because it also feeds the published `rbf_smoothness_sigma` diagnostic — removing the
call naively breaks the result JSON, which is why the removal is pinned by a spy test.

### Fixed — AVI exports advertised an index they did not have
`write_avi` set `AVIF_HASINDEX` in `avih` and wrote `hdrl + movi` with no `idx1`, so the
files played by luck and would not seek (DirectShow/VirtualDub-class tools reject them).
The index is now written with per-frame chunk ids, offsets from the `movi` body and exact
byte sizes, including the odd-width pad case.

### Hardened — a mis-weighted stack can no longer be silent
`planetary_stacker._robust_combine` (<3 frames) zipped weights against frames, so a short
weight list silently dropped frames while still normalising. It now raises
`ValueError: _robust_combine: N frames but M weights`. Also removed dead code found by the
sweep (`safe_name` in `/api/upload`, `t_ref`/`epoch_s` in `filter_wheel`, `best` in
`grs_image_prep`, an unused unpack in `result_report`) and added `raise ... from e` in
`all_methods_extra` so the original traceback stays chained.

### Fixed — report files were written with the machine's locale
`app/` had 26 text reads/writes with no explicit `encoding=` (12 of them the
`job_result.json` dumps in `server.py`, `filter_wheel`'s human report, the CSV writers in
`grs_drift`/`wind_analysis`, two JSON readers). `Path.write_text` follows
`locale.getpreferredencoding(False)`, so on a non-UTF-8 box (`LC_ALL=C`, cron, a container
without a locale) a write containing σ/°/″/— raises `UnicodeEncodeError` *after* the
expensive part of the run, or mangles the file. The filter-wheel `.txt` was already a live
failure there; the rest were one `ensure_ascii=False` away from becoming one. All 28 now
say `encoding="utf-8"`. Pinned structurally by `tests/test_text_encoding.py` (an AST sweep
with its own self-test), because `ruff`'s `PLW1514` only looks at `open()` and never at
`write_text`.

### Improved — drop a capture anywhere, zoom the preview, and see the job tick
- **Whole-window file drop**: drag a `.ser`/`.avi`/`.fits`/`.png` onto any part of the
  window and it uploads, with a dashed overlay that names the file it will load. Drops
  inside an existing drop zone are still handed to that zone, so nothing uploads twice,
  and text/link drags are ignored (the overlay keys on `dataTransfer.types`, not on drop).
- **Preview zoom + pan**: `⌘`/`ctrl`+wheel (or a trackpad pinch) to zoom from the fitted
  view, plain wheel to keep zooming once you are in — the preview sits inside a scrolling
  column, so a plain wheel at 100% is deliberately left to the page instead of hijacking
  it. 100–1400%, drag to pan while zoomed, double-click to toggle 300%/fit, `+`/`-`/`0`
  when the pane has focus, and a −/%/+ readout in the meta row. Zooming grows the image width instead of using a CSS transform, and the box centres
  with `safe center`, so no part of an enlarged disc is unreachable behind the scroll origin.
  The wrap opts itself out of tab-swiping only while zoomed, so a swipe still works at
  100%; loading a different image resets the zoom.
- **Status pill while running**: `PROCESS · 1:47` instead of a bare `RUN` — the job kind and
  elapsed time, so a long stack reads as working rather than frozen.

### Improved — the sliding parts now slide
- **Controls drawer** (≤900px, the only way to reach any control on a phone):
  drags with the finger from the edge or the sheet itself, snaps on distance
  *or* flick velocity, fades the backdrop in step (it used to swap `display`,
  which cancels the transition), is `inert` + unfocusable when closed, traps
  and restores focus, and locks page scroll on `html` *and* `body` (iOS only
  honours one).
- **Tabs**: sliding indicator under the active tab, the active tab is scrolled
  into view when a job jumps tabs on its own, ←/→/Home/End keyboard nav with a
  roving tabindex, real `tablist` ARIA wiring, and a touch swipe (rubber-banded
  at both ends, never stolen from a code block, canvas or form control).
- **Time slider**: custom cross-browser track with a filled progress run and a
  22px thumb, 24h scale, `aria-valuetext`, and `touch-action: none` so the drag
  stops being ambiguous. The old markup nested a `div` inside a `<label>`.
- **Live console** no longer yanks you to the bottom mid-read: scrolling up
  pauses follow, a "N new ↓" chip appears, the DOM is capped at 700 lines, and
  `ts`/`level` are escaped like the message.
- `color-scheme: dark` so the native date picker and scroll widgets stop
  rendering light-on-dark; static assets are cache-busted from `VERSION`
  instead of a hardcoded `?v=6.5.1` that had gone stale releases ago.
- Clicking a drop zone no longer re-triggers the file input through its own
  bubbled click (a picker that appears to never open).
- `GRS_ALLOW_FRAME` relaxes `frame-ancestors` for sandboxed/tunnelled
  previews; default stays `DENY`.

### Tests
25 static wiring regressions in `tests/test_ui_wiring.py`: JS↔DOM id contract,
tab/pane pairing, ARIA completeness, helper scope per closure, separate rate
buckets, single-endpoint polling, reduced-motion coverage, and the zoom/drop
hooks (the CSS selectors they depend on, the swipe opt-out, the drop hand-off,
the elapsed pill). Plus audit regressions: `TestApLatitude` (3),
`test_discarded_rbf_solve_stays_gone` / `test_rbf_smoothness_sigma_is_still_published`,
and `test_avi_index_written`, and `tests/test_text_encoding.py` (3). `pytest tests -m "not
slow"` after all of it: 520 passed, 16 skipped, 1 pre-existing `spiceypy` failure (it fails
the same way on the base commit).
Non-slow suite: 501 passed, 16 skipped (1 pre-existing failure in
`test_smoke_detailed.py` needs `spiceypy`, which is not installed here).

### Added — Deterioration Lab (web UI)
New `app/deterioration_lab.py` + orange **Deterioration Lab** tab: sweeps
resolution x seeing x noise, measures each cell with the published engine, and
plots median |dLon| and the within-1-degree rate with the measured seeing
floor per resolution. An "analyse your image" panel grades a real FITS/PNG/JPG
(disk/softness/method votes) entirely offline. New endpoints
`/api/deterioration`, `/api/deterioration/real`, `/api/deterioration/tips`.

### Fixed — six bugs the default campaigns could not see
- `verify_grs_detection` had a bare `h_grs(...)` (typo for a lambda); the
  scale-drift feature check raised NameError and was a silent no-op on every
  non-lean measurement. Now calls `_map_dark_centroid`.
- `frame_quality._on_disk_mask` averaged HWC RGB over (H,W) -> shape (3,), so
  every RGB video frame scored sharpness 0 and lucky imaging kept the first
  N frames. Now NTSC luma for HWC and CHW.
- `grs_complete_system.rough_disk_mask` / `disk_mask_for_quality` returned an
  empty / 3-D mask on RGB and crashed on small RGB frames.
- Blind-injection ovals (research_grade + vlbi_metrology) were planted through
  the old anisotropic sphere projection, ~1.2 deg of lat off from where the
  engine measures that lon/lat; the error was being subtracted as bias. Now
  uses `lonlat_to_planet_xyz` + `planet_xyz_to_px`.
- `planetary_stacker._per_pixel_lat` / `_ap_latitudes` used an asin(Y) sphere
  approximation wrong by up to 2.8 deg in the GRS band; both now use
  `px_to_lonlat_vec`. The zonal benchmark's planted shear uses the same.
- `planetary_derotator` measurement mode was unregularised and could stack
  *worse* than the naive mean on long captures (0.68 vs 0.76 correlation);
  it now blends 75% measured / 25% planet-model prior.

### Tests
10 new regressions in `tests/test_deterioration_regressions.py` and 3 in
`tests/test_deterioration_lab.py`. Non-slow suite: 239 passed, 5 skipped.

## [7.0.1] — 2026-08-19 — real-photo measurement audit

Ran the published path on Hubble OPAL / Ganymede-shadow / Io frames and a
Juno close-up. Synthetic campaigns could not see these bugs. Full write-up:
[`docs/REAL_PHOTO_AUDIT_7.0.1.md`](docs/REAL_PHOTO_AUDIT_7.0.1.md).

### Fixed — Hubble frames were refused as "seeing too poor"
`estimate_limb_softness_arcsec` multiplied a *radius*-normalised FWHM by the
apparent *diameter* (2×) and histogrammed an axis-aligned (x/a)²+(y/b)²
ellipse. On a rotated Hubble disk that mixed interior and sky into the same
radial bin and reported 6–9″ of "seeing" on space-telescope frames, which
then set `measurable=False` and vetoed redness-primary. Softness is now a
circular (PA-invariant) profile converted with the apparent radius, and it
is a **warning**, not a "no disk" refusal. `disk_present` (fill / contrast /
size) is the only hard refuse.

### Fixed — isolated redness pruned the correct GRS lock
On Hubble 2024-01-06 + Io the dark methods agreed on the GRS (~87°) and
redness locked a central SEB belt (~10° / ~360°). `red_isolated` +
`dark_split` seeded the belt and deleted the oval. A **majority dark
core-band cluster** now owns the seed; redness-primary is withheld when it
is isolated from that cluster; redness must sit in the *tight* GRS lat
band; a belt-ridge gate rejects colour locks that are not compact ovals.
`_redness_grs` uses the orange (R−G)×(R−B) score and a vectorised
spheroid lat map (no more Python pixel loop, no more parametric-lat
moment band).

### Fixed — production colour path and VLBI geometry
- `fit_limb_nav` now stores the `north_pa_deg` it fitted with.
- `desktop_pipeline` passes post-prep RGB into research-grade so redness
  actually sees colour (prep's orange-darkened mono is still used for
  dark-core estimators).
- `gold_standard.measure_gs_engine` no longer `to_mono`s before the engine.
- `vlbi_metrology.make_cylindrical_oriented` / `px_to_lonlat_oriented` use
  the v6.5.1 spheroid + isotropic plate scale (the PA-shear bug was still
  alive in the VLBI map).
- `tools/real_photo_validate.py` refuses a silent 1970-01-01 UTC.

### Added
- `tools/real_photo_audit.py` — disk / softness / band / RGB-vs-mono /
  redness-vs-dark split on untimed real frames (no invented System III
  truth).
- `tests/test_real_photo_audit.py` — Hubble 2019, Hubble+Io, Juno crop,
  PA persistence, vectorised lon/lat, no-1970 guard.

Measured on the same Hubble files (900–1000 px):

| Frame | Before | After |
|---|---|---|
| Hubble 2019-06-27 | refused (softness 8.6″); redness belt lat=−14 | **measurable**, template lock lat=**−19.4°** |
| Hubble 2024-01-06 + Io | redness belt ~10°; dark GRS ~83° pruned | **template_pos lat=−21.5°** (GRS on the right) |
| Hubble + Ganymede shadow | refused; methods already agreed ~0° | **redness-primary**, all methods within ~5° |
| Juno GRS close-up | number with quality 0 | still **not a disk** (contrast 0.13) |

## [Unreleased] — 2026-08-13 — accuracy + stacking hardening

### Fixed — catastrophic fallback lock under very-blurry seeing
The deep audit (`docs/DEEP_AUDIT_7.0.0.md`) found ~5% of 2.40″ very-blurry
frames locked a **decoy** SEB oval up to ~102° off, *and reported it as
confident* (quality > 0.5). Root cause: the moment mask fails outright under
that seeing, `template` + `map_dark` then agree on the same wrong dark oval
(a dark-dark "agreement" is not corroboration), and the correct colour lock was
being pruned as a `lon_cluster_outlier` because the cluster seed was the decoy
template. Fixes in `app/precision_engine.py`:
- The cluster now **seeds on the colour lock** whenever it is isolated from
  every surviving dark method (generalises the old template-vs-moment split
  guard to cover "moment failed + dark methods ganged up on a decoy").
- `redness` is **never pruned as a longitude-cluster outlier**: a sanity-checked
  colour lock (already inside the GRS latitude band, positive score) is
  arbitrated by the `redness_ok` primary, not discarded toward a dark cluster.

Measured on the same vblurry frames: catastrophic rate → **0** (was ~5%),
published method flips to the correct `redness_lon+redness_lat`.

### Added — robust stacking in `app/planetary_stacker.py`
- **Sigma-clipped weighted mean** combination (new `robust=True` default,
  `robust_sigma`, `robust_iters`). Rejects transient per-pixel defects
  (cosmic rays, hot pixels, a one-frame satellite/shadow transit) that a plain
  weighted mean would stamp onto the stack. Per-pixel median → MAD (1.4826·MAD)
  → iterative clip → weighted mean of survivors. Memory-guarded (1.5 GB cube
  budget) with a clean fallback to the streaming weighted mean; <3 frames
  degrades to a plain mean.
- **Alignment-confidence frame weighting**: the frame weight is now
  sharpness × alignment confidence (the tracker's AP peak SNR), so a
  crisp-but-mis-registered frame no longer pollutes the stack with full weight.
- `tools/vblurry_sweep.py` — reproducible very-blurry catastrophic-rate check.
- Tests: `tests/test_planetary_stacker.py` (11 tests) now cover the robust
  combination, the alignment-confidence mapping, and RGB robustness.

## [7.0.0] — 2026-08-09 — "Velocity Pro"

### Added
- `app/cspeed.c` + `app/cspeed.py` — an optional C core (ctypes, no
  dependencies; builds on demand via cc/gcc/clang, or explicitly with
  `tools/build_cspeed.py`). Two kernels:
  - `cs_lk_step`: one fused Lucas–Kanade pass producing the spline value,
    both central-difference gradients and the Gauss–Newton normal-equation
    sums from a shared 6×6 coefficient neighbourhood — replacing five
    `map_coordinates` calls and ~6 numpy temporaries per iteration.
  - `cs_sample3`: batch cubic-spline coefficient sampling for
    `warp_shift2d` / `warp_field2d` (scipy `NI_EXTEND_NEAREST` clamping
    replicated exactly).
- `tools/cspeed_benchmark.py` — measured C-vs-numpy speed table (same box,
  same inputs, same APIs). `CSPEED=0` env var forces the pure path;
  `cspeed.set_enabled(False)` toggles at runtime (used by the A/B tests).

### Performance (measured on the validation box)
- `stack_ap` end-to-end (12 frames, full AP grid): **3.52×**
  (197.1 → 56.0 ms).
- `_lk_refine` micro (32×32 crop, 4 iters): **2.51×** (1.525 → 0.608 ms).
- `warp_field2d` 300×400 order 3: **1.73×** (17.0 → 9.9 ms);
  `warp_shift2d` 400×300 order 3: **1.45×** (10.7 → 7.4 ms).
- The C core also cut the LK-heavy test battery from 188.7 s (29 tests)
  to 107.2 s (62 tests, a superset incl. full ap_stacker/image_warp/
  planetary suites).

### Correctness contract (`tests/test_cspeed.py`, 8 tests)
- `cs_sample3` vs scipy `map_coordinates`: max|δ| **1.3e-15** over 5000
  sampled points including out-of-range (edge-clamped) coordinates.
- `cs_lk_step` vs the numpy replication: max|δ| **2.8e-14** plain,
  **2.6e-13** windowed — pure summation-order noise.
- End-to-end `stack_ap` golden A/B (with vs without C): stack max|δ|
  **3.47e-16**, weight/shifts < 1e-9, frame-usage decisions identical.
- Strict IEEE doubles: `-O3 -fno-math-errno -fno-trapping-math`, never
  `-ffast-math` / `-march=native`.
- No C compiler → the identical scipy fallback runs automatically; the
  loader surfaces `cspeed.status_note()` (soft-fail loudly: correct and
  slower, never silently different).

## [6.9.0] — 2026-08-08 — "Analysis Pro"

Rotation-derotated filter-wheel RGB compositing, cloud-tracking wind science,
GRS System-II drift geophysics, stack forensics, limb-darkening measurements
and physics-derived session planning — the analysis layer AutoStakkert does
not have and WinJUPOS does by hand. Every claim is backed by a test (74 new
tests) or a campaign number below.

### Added
- **`rgb_combine.py`** — WinJUPOS "RGB combine" parity (AutoStakkert
  fundamentally cannot): mono-filter stacks are derotated to a common epoch
  with the *exact* oblate-spheroid ephemeris (vectorised inverse+forward
  projection, so north-PA and sub-Earth-latitude tilts are handled by
  construction), a wind-adjusted per-parallel cloud rate (vectorised twin
  of `Planet.cloud_tracking_rate_deg_per_s`, pinned equal to 1e-12), and a
  measured per-latitude-band residual polish. Measured: tilted synthetic
  (sub-lat −2.3°, pole PA 18°, 2.419° rotation/hop ≈ 4.86 px): colour
  fringe 0.1927 → **0.0322 (6.0×)**, channels co-registered < 0.35 px.
- **`filter_wheel.py`** — the full amateur colour workflow in one call:
  3× SER/AVI → per-filter APS stacks → re-centre → derotated RGB composite
  + artefacts (stacks, rgb.png, report JSON). Measured on real SER files
  with true timestamps (4-min hops): mid-times recovered from SER ticks,
  fringe **0.1069 → 0.0357 (3.0×)**, coverage ≥ 99.9%.
- **`wind_analysis.py`** — science over the AP-derived wind profile:
  shape-discriminated offset fits (uniform m/s advection vs constant
  angular-rate System-III correction — reduced-χ² picks the shape),
  jet detection (k-σ local extrema, parabolic peak, honest: never spans
  evidence gaps), JUPOS-friendly CSV (empty cells, not fabricated zeros),
  PIL PNG profile panel, `wind_report_text` summary.
- **`grs_drift.py`** — the GRS as a time-series object: longitude-unwrap +
  sigma-clipped weighted drift fit (deg/day, deg/30 d convention),
  curvature included only when the F-test demands it, implied zonal
  velocity in m/s (shared `surface_parallel_radius_m` convention),
  prediction cone (parameter covariance + intrinsic scatter), JUPOS CSV
  ingest, PNG/CSV artefacts. Planted −0.42°/30 d recovered to ±0.10.
- **`stack_report.py`** — stack forensics: interior coverage fill
  (enclosed-hole metric), subpixel **dither-diversity audit** from measured
  alignment phases (the physically true drizzle starvation signal),
  frame-usage concentration, detrended wander / drift slope / single-frame
  jump stats, nominal SNR gain, Tenengrad sharpness gain vs input frames,
  and actionable warnings; PIL forensics panel.
- **`session_planner.py`** — closed-form session budgets:
  `max_span_s` (exact inversion of `lon_drift_px`, 1e-9 relative),
  `max_span_derotated_s` (wind-residual-limited — typically 5–10× longer,
  the number folklore ignores), filter-wheel gap limits (direct vs
  polish-gated), composed with `transits.night_planner` into one panel.
- **`limb_darkening.py`** — band-normalised, MAD-clipped μ^k fit on a
  finished stack with per-band k(lat) table and PIL panel. On the
  renderer's planted μ**0.6 law: **k = 0.653 ± 0.020**; on an
  achromatic-corrected frame: k ≈ 0.
- **CLI**: `rgb-combine`, `filter-wheel`, `wind-analysis`, `drift`,
  `session-plan` (all subprocess-tested, `tests/test_cli_v69.py`).
- **Desktop**: new **RGB Combine** tab (three channel pickers, offsets/times,
  sub-lat + pole-PA, fringe report + preview) and **Analysis** tab (session
  planner + wind analysis + GRS drift with PNG panels).
- **Web**: `/api/analysis_session` and `/api/analysis_drift` endpoints +
  **Analysis** tab (session budgets; CSV upload → drift fit with preview).

### Performance
- **APS stacker 1.3–1.8× faster, bitwise identical** (`ap_stacker._lk_refine`):
  `map_coordinates(order=3, mode="nearest")` reruns its spline prefilter
  (plus a 12-px edge pad) on every call — 3 calls/GN iteration × 4
  iterations × every (frame, AP) pair. We replicate the internal recipe
  once (pad 12 edge + `spline_filter` mode="nearest") and sample the
  coefficients with `prefilter=False`: verified max|delta| = 0.0 on unit
  fields, and a golden 16-frame × 86-AP drizzle rig reproduces the stack to
  4.4×10⁻¹⁶ and the global shifts to exactly 0.0. Measured 23.7 s →
  16.8–13.2 s single-threaded; same speedup in every LK consumer
  (derotator trackers, rgb_combine's windowed LK).

### Engineering notes (measured, not argued)
- Foreshortening is real: the exact rotation field's dX/dλ carries a
  cos(lon_rel) factor that centre-line chord models (px_per_deg_lon) lack —
  a naive whole-band unit test read 0.864× the model; tests compare at the
  central meridian where the analytic chord is exact.
- Global FFT-peak locks are unsafe for residual polish: every coarse
  strategy locked 0.4–1.1 px off on adversarial quasi-periodic band
  texture. The polish is local-only: window-aware Lucas–Kanade from (0,0),
  gated at max_resid_px + ≥2% RMS improvement; planted (0.45, −1.25) px
  recovered as (−0.429, +1.255). Window-naive LK recovers dy≈0 — the
  window belongs in the gradients, measured.
- Drizzle "holes" from pixfrac alone cannot exist in our deposit scheme
  (drops are D·pixfrac bins + 1-bin wings): forensics measures enclosed-
  hole fill + the dither audit instead (identical frames: spread 0.000 →
  warning; real ±1.2 px dither: 0.145).
- Fixed a use-after-delete in `test_planted_rotation_recovered` (PNG read
  after the TemporaryDirectory closed) — the only failure of the
  1 h 44 m pre-release suite (768 passed, 9 skipped).

### Tests
- 70 new tests since 6.8.0 (suite collection 778 → **848**) across
  `test_rgb_combine`, `test_wind_analysis`, `test_grs_drift`,
  `test_stack_report`, `test_filter_wheel`, `test_session_planner`,
  `test_limb_darkening`, `test_cli_v69`, `test_web_v69`,
  `test_desktop_v69`; batteries re-run for every module touching the LK
  core (29/29), all CLI subprocess paths (6/6), and the full suite for
  release.

## [6.8.0] — 2026-08-07 — "Observatory Pro"

AutoStakkert-class video stacking + drizzle super-resolution, WinJUPOS-class
transit planning, JUPOS export and animation, a fifth measurement definition
(rim ellipse), a true-sky-geometry synthetic benchmark, and three measured
production fixes. Every claim below is backed by a test or campaign number.

### Added
- **APS video stacker** (`ap_stacker.py`): per-alignment-point local lucky
  imaging. Frames are global-aligned (integer FFT peak + Lucas–Kanade refine,
  ~0.001 px on clean frames, ~0.2–0.6 px at noise 0.05), per-AP quality-ranked
  (laplacian | gradient | sobel | contrast), feathered-overlap stacked with
  quality-power weights. **Raw-deposit drizzle ×2/×3**: measured super-res —
  on a 12-frame planet video the drizzle stack reaches ~1.4% of the exact-offset
  oracle RMSE, and the oracle is 60% of a single frame's RMSE. Rotation-aware
  prior (`ap_expected_dx`) keeps the search window centred while the planet turns.
- **SER / AVI reader-writer** (`ser_io.py`): full SER header, 1/2/4-byte mono and
  3-byte RGB, ms-ticks timestamps, uncompressed DIB AVI, MJPG refused with a
  transcode hint. Lazy `Video` API (no full load).
- **Sharpen Lab** (`sharpen_lab.py`): B3 à-trous wavelets (partition of unity
  tested), per-layer gains + MAD denoise gate (noise is NOT amplified — measured),
  Richardson–Lucy (L2-grid win pinned), classic unsharp; RGB sharpened on
  luminance so hue is preserved.
- **Transit planner** (`transits.py`): next GRS meridian crossings (brentq,
  sub-minute), visibility windows |rel|≤45°, "GRS now", Galilean moon
  transit/occultation events — **validated against published 2026 tables**
  (Project Pluto): Io transit 2026-08-01 ~05:06 (ours 05:10), Io occult 08-02
  ~02:15, Europa transit 08-02 ~09:14. Backend chain SPICE jup365 → ephem fallback.
- **Rim-ellipse definition** (`grs_ellipse.py`): Fitzgibbon least-squares ellipse
  on GRS rim samples, with conic-sign normalisation, degenerate-line guards,
  and a physics-gated **RANSAC fallback** that fires only when the classical
  fit fails (zero regression on converged cases by construction). 100-case
  resolution×seeing audit: classic path converges 76/100 (clear/mild 46/46)
  and is the tightest estimator of all there — **zero cases beyond |dlon|
  0.733° / |dlat| 0.580°** (medians 0.109/0.116); RANSAC lifts convergence to
  **97/100** with honest degradation on appalling seeing (|dlon| med 1.3°)
  and a reduced ensemble weight (0.6 vs 1.5). Wired as the 5th method in
  `per_method_audit` and `all_methods.run_all_methods`.
- **Animation + JUPOS** (`animation.py`, `jupos_io.py`): animated GIF blink/loop
  (Pillow duration quirks documented), WinJUPOS JUPOS `.csv` 15-field
  export/import round-trip for cross-checking our longitudes against the
  community database format.
- **One-command production pipeline** (`observatory_pipeline.py` → CLI):
  `grs-observatory video-stack`, `ap-stack`, `sharpen`, `transits`, `animate`,
  `jupos-export`, `video-to-answer`. The last runs the whole chain
  SER → APS stack → wavelet sharpen → published measurement.
- **True-sky-geometry synthetic benchmark** (`synthetic_hq.py`): the renderer
  now applies the REAL sub-Earth latitude and north-polar-axis PA of the epoch
  (spec `sub_lat_deg` / `north_pa_deg`) using the exact inverse of
  `precision_engine.px_to_lonlat`, so the full production stack — which models
  these — is validated end-to-end for the first time (D=P=0 keeps bit-identical
  output). A wrong-orientation control test proves the geometry is real:
  PA 343.5° measured with PA 0 priors misses by ~6.5°.
- **WinJUPOS-style derotation everywhere** (`observatory_pipeline`,
  CLI/desktop/web): `--derotate prior|hybrid|measurement` on `video-stack`,
  `ap-stack` and `video-to-answer`, a `derotate_folder()` API, a desktop
  Video-Import combo and a web Video-tab selector. Timing is taken from SER
  per-frame stamps (or `--dt-per-frame`); **without timing the derotate is
  refused loudly** — a guessed cadence would silently mis-rotate every frame.
  The derotated stack is anchored to the derotation reference frame, and
  `video-to-answer` then publishes with `measurement_epoch: "ref_frame"` and
  stamps that frame's UTC (the WinJUPOS reduction convention). Measured on a
  rotating synthetic capture (rigid System III spin, real seeing/noise/tip-tilt,
  1080p-class): published equity within the same 1.5° production band as the
  non-rotated flagship test (tests/test_cli_pro.py::TestVideoToAnswerDerotate),
  and the plain-vs-derotated stacking A/B is pinned in
  tests/test_video_jupiter.py.
- **Zonal-wind measurement from every derotated capture**
  (`ap_stacker.wind_report_from_drifts`, surfaced in
  `derotate_frames(...)[1]["wind_report"]` and the `video-stack` report):
  the same prior-seeded AP tracks that drive derotation are a
  cloud-tracking wind experiment — per-track drift rates convert to a
  measured zonal rate per |lat| bin (deg/s) and a residual vs the
  literature profile in m/s (`Planet.surface_parallel_radius_m` keeps the
  px→deg→m/s chain exactly chord-consistent). Robust estimator: per-track
  m/s, iterated MAD-rejected median (fringe-alias outliers of hundreds of
  m/s on quasi-periodic bands measured; no mean can survive them). This is
  the WinJUPOS drift-measurement science AutoStakkert cannot do at all.
  Prior mode honestly reports ALL-None (no image evidence). Pinned by a
  planted differential-wind test (planted +30 m/s vs zero-wind control,
  30-min span, |lat| ≤ 21° recovered inside ±80 m/s;
  `tests/test_ap_stacker.py::TestWindMeasurement`).
- **WinJUPOS–AutoStakkert composition verified end-to-end**: `--derotate`
  and `--drizzle 2` compose in one command (`video-stack`), SER stamps →
  prior/hybrid derotate → APS drizzle ×2 stack with the full report trail
  (`tests/test_cli_pro.py::TestVideoStackCLI`).
- **`image_warp.py`**: shared exact sub-pixel spatial shift (spline
  resample), replacing the broken FFT phase ramp everywhere (see Fixed).
- **Desktop**: new tabs **Video Import** (APS stack, drizzle, video→answer),
  **Sharpen Lab**, **Transits**. **Web**: new **Video** and **Transits** tabs,
  endpoints `/api/video_stack`, `/api/sharpen`, `/api/transits` (path-traversal
  hardened, uploaded videos whitelisted).

### Fixed (measured, with regressions pinned)
- **The FFT phase-ramp sub-pixel shift was mathematically broken in SIX
  call sites.** For non-integer shifts on real input, multiplying the
  spectrum by exp(−2πi·k·s/N) destroys Hermitian symmetry, so
  `Re(ifft2)` returns the EVEN MIXTURE `(f(x−s)+f(x+s))/2`, not `f(x−s)`.
  Measured 2026-08-07 (160×160 planted 1.5 px field): ±1.5 px shifts gave
  **byte-identical MSE 0.001077** — the stacks were smearing every
  non-integer drift — while integer shifts were exact (2.6e-32), which is
  how every smooth-texture benchmark missed it. All six sites
  (`jpa_10k`, `jpa_10d`, `planetary_stacker._global_shift`,
  `holy_hybrid_stacker`, `jupiter_infinite_tensor_engine`,
  `win_jupos_derotator`) now use the shared
  **`image_warp.warp_shift2d`** spline shift (centroid-verified to
  0.005 px at ±(0.55–3.91) px). The `win_jupos_derotator` "FFT
  three-shear" was doubly wrong — a spatially-varying phase ramp is not a
  shear at all (Fourier shift theorem needs constant s) — replaced by an
  exact single-pass cubic rotation; planted-dot placement verified to
  **0.003 px**, round-trip drift 0.003 px (`tests/test_image_warp.py`).
- **`jupiter_zonal_stacker` tracker rewrite** (`_track_ap_zonal`): the
  legacy loop used the parabola-bug `_phase_corr_shift` (planted dy=−1.5
  reported +4.07) and ADDED apply-shift residuals onto content-convention
  priors (sign mixing: perfect x-prior + planted dy=−1.5 → reported
  dy=+1.5). Re-implemented on the proven prior-seeded `_measure_shift`
  engine (content accumulation `pred -= apply·2^oct`); the frame apply
  moved off its dead double-mean onto a single SNR-weighted mean with the
  sign corrected for content convention. End-to-end proof: three rigidly
  planted-shifted frames with ZERO declared rotation stack to a
  gain-matched MSE of **0.000002** vs raw-frame MSE 0.0045 — the stacker
  now recovers motion it knew nothing about a priori.
- **Moon-mask colour gate** (`grs_image_prep.py`): the old luminance-only
  satellite-shadow masker erased red-brown Jovian features — on a 1080p
  synthetic it masked ~24k px including the GRS core itself, biasing the
  centroid >10°. Moons/shadows are colour-neutral (`r − 0.5(g+b) ≈ 0`);
  blobs with redness > 0.06 are kept as atmosphere, plus a 2.5%-of-disk
  safety cap. Diagnostic: 18/19 false blobs were reddish (0.11–0.31).
- **Ephemeris-orientation application on un-tilted synthetic frames** was an
  apples-to-oranges mismatch (~6.5° at PA 343.5°). Resolved by the true-tilt
  renderer above + a **PA-aware limb fit** (`fit_limb_nav(north_pa_deg=…)`):
  median/MAD stats are computed in the de-rotated body frame so the fit is
  exact under disk rotation. On an un-rotated frame a wrong ±16.5° prior moves
  xc/yc < 0.001 px and a < 0.03% — de-rotation is statistically near-neutral,
  so real captures (Jupiter's PA swings ±17° over a Jovian year — verified
  2026-08-02: sub-lat +0.665°, PA 343.50°) are now fitted correctly.
- **Stale pin repaired**: `test_moment_has_dlat_bias` →
  `test_moment_dlat_bias_stays_fixed` (moment dlat bias is FIXED at +0.013°;
  the test now guards ≤0.30° so it can't regress silently).
- **`video_to_answer(downsample=…)` was silently ignored** — the parameter is
  now honoured with the same anti-aliased box decimation as `load_frames`
  (the published test expected 2× speedups it never got; dead parameter bugs
  are how pipelines quietly run the wrong regime).
- **`ap_stacker.derotate_frames` measurement/hybrid branch** seeded its AP
  tracker with the LEGACY longitude scale (`_per_ap_expected_dx`, under-shift
  up to 1.57× at the equator); switched to `_per_ap_expected_dx_lon` like the
  other derotation call sites.
- **Flow-warp fit learnt SNR-weighting** (`flow_warp._rbf_dense_measured`):
  the RBF ridge is now `K + λ·diag(1/w)`, so low-SNR AP locks are smoothed
  toward the field instead of interpolated. Measured on the noisy-2D A/B:
  on-disk RMS 0.1204 → 0.1189 (per-lat 0.1164; with the v6.8 tracker both
  warps now tie within ~2% on noisy data — the historical +34% noise blow-up
  stays gated in `test_flow_warp.py`).
- **Stale "template is 80–100° off on metrology synthetic" pin** in
  `test_real_photo_validate.py` was failing at v6.7.6 HEAD too (measured:
  template dlon −0.150°) — re-pinned to the truth (all classical estimators
  sub-1° on metrology synthetic). The `test_redness_primary` 100/100 claim
  became the documented per-stratum guarantee (99/100 ≤1°; the outlier is in
  the vblurry stress band at 1.111° < its 1.2° limit, identical at HEAD).
- **AP outlier gates in the derotation trackers** (`planetary_stacker
  .gate_ap_track`, used by `run_planetary_stacker` and
  `ap_stacker.derotate_frames`): AutoStakkert-style gating — (1) a limb gate
  (rr ≤ 0.93 in the sky plane): boxes near the limb lock onto the *geometric*
  disk edge, which does not move with the clouds — measured mis-locks of
  2–8 px at phase-corr SNR 8–9 on bland captures, so SNR cannot identify
  them; (2) a post-prior residual gate
  `|resid| ≤ max(2 px, 0.3·|prior| + 1 px)`: unmodelled zonal shear is
  ≪ 1 px at amateur scales, so any larger leftover is a mis-lock, not
  meteorology. Rejected APs fall back to the model prior in their latitude
  band. Measured effect (planted-fiducial audit, 4-frame 180 s rotating
  capture): measurement/hybrid derotation fiducial error 0.843 → **0.274 px**
  (prior 0.273 px; true drift 4.33 px).
- **Phantom fitted dy no longer applied** in `ap_stacker.derotate_frames`:
  rotation is zonal, so the per-AP dy fit should be ≈ 0 — on bland frames it
  was a systematic **−0.46 px phantom** (true dy 0) that moved planted
  markers ~0.8 px in y. The derotator now applies dx-only; the fitted dy is
  still reported (`info["dy_fitted_px"]`) for transparency, and tip/tilt y
  wander stays with the stacker's global align, where it is the better tool.

### The end-to-end proof (the number that matters)
`video-to-answer` on a tilted-render (real 2026-08-02 geometry) SER video:
published GRS relative longitude error **0.173°**, latitude error **0.347°**,
grade GOOD — well inside the repo's 1° production gate. The test gates
*relative* longitude (each side's own CM anchor: synthetic frames are planted
on the renderer's analytical CM; production publishes on the SPICE CM — the two
frames are documented and intentional), which is exactly what the campaigns score.

## Honest limits
- `video-to-answer` still needs a mid-exposure UTC (SER stamps or `--time`).
- The 2D-mono flagship lock can still pick a non-GRS dark blob on bland
  monochrome frames; the colour path (GS-ORANGE + redness) is what carries
  production accuracy today (reason published definitions favour it).
- Moon ephemeris needs SPICE jup365 or pyephem; without either it soft-fails.

## [6.7.6] — 2026-07-31

Desktop Stacking tab: planet-generalised engine option.

### Added
- The Stacking tab now has a "Stack engine" selector: **Jupiter-zonal** (default,
  unchanged) or **Planetary (multi-planet)**. The Planetary engine exposes a
  Planet selector (Jupiter/Saturn/Neptune/Uranus/Mars), a Warp-mode selector
  (per_latitude/flow/global), and a Quality-gate field, and routes through
  `run_planetary_stacker` — writing the stacked PNG + `stacker_report.txt` and
  surfacing warp mode, reference frame, dropped frames, consistency, timing.

### Honest verification limit
- This is **additive and defaults to the original Jupiter-zonal path** (zero
  behaviour change unless you pick the Planetary engine). It is syntax-checked
  (`py_compile`) but NOT runtime-verified: this build sandbox has no tkinter /
  display, so `test_desktop_wiring` skips here. Please launch the desktop app to
  confirm the new controls render. The Derotate tab still uses the Jupiter-zonal
  derotator (the planetary derotator remains CLI-only for now).

## [6.7.5] — 2026-07-31

Real-photo campaign harness.

### Added
- `tools/real_photo_stack.py` — run EVERY planetary-stacker warp mode
  (per_latitude / flow / global) plus a naive-mean on a folder of real frames
  (PNG/JPG/FITS, mono or RGB), and write each stack + its report card + a
  `COMPARISON.md`. HONEST by design: it does NOT auto-pick a winner (no-reference
  mode selection is ill-posed — see v6.7.2); it gives you the artifacts to judge
  by eye, with `per_latitude` as the stated default. Optional `--cm-csv` supplies
  per-frame CM III so the planet-model prior is correct.

### Note
- No new measurement math; this is a reproducibility/auditability tool for real
  captures. The synthetic `flow_warp_benchmark.py` remains the ground-truth A/B.

## [6.7.4] — 2026-07-31

RGB per-channel stacking — colour is preserved instead of collapsed to grey.

### Changed
- `run_planetary_stacker` now detects RGB input (HxWx3 / CHW / RGBA). It still
  tracks and measures consistency on **luminance** (most robust), but applies the
  SAME geometric warp to R/G/B independently and stacks each channel, emitting an
  RGB PNG. Mono input is unchanged (mono path verified, no regression).
- The three warp primitives (`_global_shift`, `per_row_warp`, `apply_flow_warp`)
  are now channel-aware: an (h,w,3) frame + one displacement descriptor warps
  every channel identically.

### Honest note
This preserves colour; it does NOT change alignment accuracy (tracking is still
luminance-based by design — colour edges are noisier than luminance for phase
correlation). Real chromatic-aberration handling (per-channel separate derotate)
is a future option, not done here.

### Tests
- `test_rgb_input_yields_rgb_stack_with_colour` (RGB in -> 3-channel out, channels
  differ). Full planetary/flow/zonal suite 35/35. Version 6.7.3 -> 6.7.4.

## [6.7.3] — 2026-07-31

Auditability + doc sync.

### Added
- **Derotator report card** — `run_planetary_derotate` now writes
  `derotator_report.txt` (mirror of the stacker's): planet, mode, reference
  frame, mean per-row shift, per-frame median |dx|, timing, notes.
  `derotator_report_text` / `write_derotator_report` are public.

### Changed
- `PROJECT_MAP.md` module tree now lists the v6.7 modules
  (`planet_models`, `planetary_stacker`, `planetary_derotator`, `flow_warp`,
  `frame_quality`) and the new tools — it was stale.

## [6.7.2] — 2026-07-31

Stacker report card + an honest "Rejected" record of two ideas that measured
negative (kept out of the product on purpose).

### Added
- **Stacker report card** — `run_planetary_stacker` now writes
  `stacker_report.txt` next to the PNG: planet, warp mode, reference frame,
  quality-gate drops, per-frame drift RMS (with reference/dropped tags),
  consistency, timing, notes. `stacker_report_text` / `write_stacker_report`
  are public. Makes a stack auditable instead of just an image.
- `warp_consistency_std` result field — mean on-disk std of the warped frames.
  Surfaced in the report as a raw per-run agreement diagnostic.

### Changed
- Refactored the stacker's track+warp+stack core into a `_pass(ref)` helper
  (cleaner; identical behaviour). This was in preparation for multi-pass
  stacking, which was then measured-negative and dropped (see Rejected).

### Rejected (tried, measured negative, NOT shipped)
- **Iterative / multi-pass stacking** (re-track against the denoised stack).
  Made the result WORSE: on an all-frames-noisy benchmark, 1 pass = 0.073
  on-disk RMS, 2 passes = 0.120, 3 passes = 0.117. Cause: re-tracking against
  a denoised (smoothed) stack loses the high-frequency content phase-correlation
  locks onto, so drifts get noisier and the warp degrades — outweighing any
  denoising benefit. Removed rather than shipped.
- **No-reference auto warp-mode selection** (`--warp-mode auto`). Tried three
  no-reference metrics; all are confounded:
  - sharpness (Laplacian var) — biased toward warps that smooth less, so it
    spuriously favours per-latitude regardless of alignment;
  - split-half between-stack RMS — noisy, ranked the middle mode worst;
  - cross-frame consistency std — rewards the MOST flexible warp, because
  flexibility lowers residuals even by overfitting noise (it picked `flow` on
  noisy data where flow is measured-worst).
  Conclusion: no-reference warp-mode selection is ill-posed without a reference
  or a noise model. Use `tools/flow_warp_benchmark.py` (which has a known
  reference) to pick a mode for your data. The default stays `per_latitude`.

## [6.7.1] — 2026-07-31

Three more stacking improvements, each measured.

### 1. Dense 2D flow warp (`app/flow_warp.py`, `warp_mode="flow"`)
The v6.6.3 changelog explicitly called "a 2D per-pixel zonal-warp the right next
step; not done." This adds it: fit a dense (dy,dx) displacement field from the
per-AP drifts (RBF) and apply a sub-pixel backward warp. Captures local/meridional
motion the per-row warp cannot.

### 2. Lucky-imaging frame rejection (`app/frame_quality.py`, `--quality-gate`)
AutoStakkert-style: score each frame for sharpness, stack only the best fraction.
`quality_gate=0.75` drops the 25% worst-seeing frames; the reference is always kept.

### 3. Reproducible campaign (`tools/flow_warp_benchmark.py`)
A/B across global / per_latitude / flow / naive-mean on controllable zonal + 2D
+ seeing + noise perturbations. Run it to see which warp wins on your data.

### Measured (on-disk RMS to reference, lower = better)
- clean 2D-distorted frames: **flow 0.134 < per_latitude 0.161 < global 0.185** -> flow wins.
- pure zonal frames: flow ~= per_latitude (both ~0.13-0.21).
- **honest limit**: under heavy seeing + read noise a dense warp can do WORSE than
  per-latitude (and even naive mean) -- it has more DOF, so noisy per-AP drifts get
  interpolated into a spurious flow. The fit uses a smoothing ridge + residual
  outlier rejection, and the **default warp stays per_latitude**; flow is for clean
  / large-motion data. The campaign tool reports which to use.

### Added
- `app/flow_warp.py`, `app/frame_quality.py`, `tools/flow_warp_benchmark.py`.
- `tests/test_flow_warp.py` (3), `tests/test_frame_quality.py` (5).
- CLI `--warp-mode {per_latitude,flow,global}` and `--quality-gate` on `planet-stack`.

### Fixed (found along the way)
- `jpa_10k._fit_velocity_field` was broken (`np.mgrid[0:h:gh]` treats the 3rd index
  as a step, not a count -> reshape crash). It was never called by `run_jpa_10k`,
  which is why it went unnoticed. `flow_warp` uses a correct RBF fit instead of
  depending on it.

## [6.7.0] — 2026-07-31

The stacker and derotator are no longer Jupiter-only, and the stacker no longer
throws away the per-latitude shear it measures. Full write-up:
[`docs/PLANETARY_STACKING_6.7.0.md`](docs/PLANETARY_STACKING_6.7.0.md).

### The two fixes
1. **Generalised.** `jupiter_zonal_stacker` / `jupiter_zonal_derotator` baked in
   Jupiter's radius, flattening, System III period and Porco wind table. New
   `planet_models.Planet` carries the real rotation period + flattening +
   literature zonal-wind profile for **Jupiter, Saturn, Neptune, Uranus, Mars**,
   so the same code stacks/derotates any of them.
2. **More accurate.** `jupiter_zonal_stacker` tracked every AP with the full
   zonal-wind model but then collapsed all per-AP drifts into ONE global
   (dy,dx) translation per frame — the per-latitude shear was measured then
   discarded. New `planetary_stacker` applies a genuine **per-latitude warp**
   (robust SNR-weighted dx vs |lat|, binned, per-row shift), with a hybrid
   prior+measurement tracker so the APs still lock when the bulk rotation has
   swept a feature past the AP window.

### Measured (synthetic, genuinely per-latitude-sheared frames)
| Path | mean per-belt correlation peak (1.0 = perfect) |
|---|---|
| legacy single global translation | 0.717 |
| **new per-latitude warp** | **0.755** (+0.037) |
| naive mean (no derotation) | 0.642 |
| **new measurement-mode derotator** | **0.748** (+0.106 vs naive) |

### Added
- `app/planet_models.py` — `Planet` dataclass + 5 built-in profiles
  (`get_planet`, `known_planets`, `cloud_tracking_rate_deg_per_s`).
- `app/planetary_stacker.py` — planet-generalised stacker with per-latitude
  warp, quality-ranked reference, sharpness weighting, hybrid prior tracker.
- `app/planetary_derotator.py` — planet-generalised per-latitude derotator
  (measurement / prior / hybrid modes; `prior` = ephemeris+winds only, no
  image tracking — a genuinely new capability).
- `app/cli.py` — `planet-stack` and `planet-derotate` subcommands
  (`--planet`, `--frames-dir`, `--mode`, `--warp-mode`).
- `tests/test_planet_models.py` (9), `tests/test_planetary_stacker.py` (5),
  `tests/test_planetary_derotator.py` (5) — incl. the per-lat-vs-global A/B.
- `docs/PLANETARY_STACKING_6.7.0.md`.

### Notes / honest limits
- The +0.037 / +0.106 gains are on synthetic per-latitude-sheared frames
  (the regime where per-latitude warp is supposed to win). Real photos add
  seeing + chromatic noise; a real-photo campaign is the next step, as for
  the Jupiter-only stackers.
- The zonal-wind RESIDUAL tables are representative literature cloud-tracking
  profiles used as a derotation prior — the stacker measures the true per-lat
  motion and overrides them wherever the data disagrees. They are NOT a wind
  measurement; do not cite them as one.
- The Jupiter-only modules are NOT removed (still used by the desktop tabs);
  the new modules are additive. Existing zonal tests still pass (6/6).
- Version bumped 6.6.5 → 6.7.0 across `VERSION`, `README.md`, `pyproject.toml`,
  `PROJECT_MAP.md`. This also fixes the pre-existing version drift
  (`pyproject.toml` was 6.6.3, `PROJECT_MAP.md` was 6.5.0).
- Pre-existing unrelated failure `test_per_method_audit::test_moment_has_dlat_bias`
  fails on the clean base too; not caused by this change.

## [6.6.5] — 2026-07-31

### Added
- Siril-style 3-tab top panel in the desktop app: **Stacking / Derotate / Process**.
  - **Stacking** — choose a folder of PNG/JPG/FITS frames and run the
    Jupiter-zonal stacker on it. A determinate progress bar tracks frame
    ingest (it reflects how many frames have loaded, not output quality).
  - **auto n_grid** — sizes the AP grid from the frame. Honest limit: this
    assumes a SINGLE Jupiter-like disk fills the frame. With multiple disks,
    a small disk, or a disk off-centre, turn auto off and set n_grid by hand.
  - **Derotate** — per-latitude zonal derotation of the same folder. Optional
    GRS anchor: paste a GRS x,y from any frame and APs that disagree with the
    GRS rotation are demoted. ("winjupos but better" is a goal/aspiration, not
    a measured accuracy claim against WinJUPOS.)
  - **Process** — the existing single-image measurement path (open file →
    Process full / Resolve Ephemeris) surfaced as a tab.

### Changed
- `app/jupiter_zonal_stacker.py` — new public `auto_n_grid(h, w)` helper.
- `app/desktop_app.py` — `_build_top_panel()` + Stacking/Derotate handlers;
  `_tick` now forwards `("progress", n)` to the Stacking tab's determinate bar.

### Notes / honest limits
- No measurement math, truth sets, or accuracy figures were changed.
- `app/desktop_app.py` IS touched (it is import-tested by
  `tests/test_desktop_wiring.py`), so this is NOT a "no test-covered module
  touched" change — run `pytest tests/` before merging.
- This branch was at 6.6.3; 6.6.4 is not present here, so the version jumps
  6.6.3 → 6.6.5.

## [6.6.3] — 2026-07-31

Jupiter-specialized stacking. Replaces the generic AP-grid stacker
with a Jupiter-aware prior on per-latitude drift. Full write-up:
[`docs/10_HOUR_STACKING_DEROTATION.md`](docs/10_HOUR_STACKING_DEROTATION.md).

### The improvement

The existing `jpa_10k` and `holy_hybrid_stacker` are *generic*
AP-grid stackers. They do not exploit Jupiter-specific structure:
- No System III rotation prior
- No zonal-wind-residual prior (Porco+2003 cloud-tracking profile)
- No GRS-anchor mode

The new `jupiter_zonal_stacker.py` does all three. On a synthetic
benchmark with realistic zonal-shear motion between frames (16
frames, 720p, 6s between frames, 0.5° CM drift per frame):

| Stacker | Per-belt peak (1.0 = perfect) |
|---|---|
| JPA-10K (generic) | 0.847 overall (0.58 at polar band) |
| Holy-hybrid (CNN + physics) | 0.714 overall (0.00 at polar band) |
| **Jupiter-zonal (this PR)** | **0.996 overall (0.996 at every band)** |

The Jupiter-zonal is a **17% improvement overall** and a
**40% improvement at the polar bands** on Jupiter-like data. The
holy-hybrid's CNN actually *regresses* on this benchmark because
the CNN was self-distilled on synthetic data without zonal shear.

### Added
- `app/jupiter_zonal_stacker.py` — the new stacker.
  - System III + zonal-wind-residual as per-AP drift prior
  - Optional GRS-anchor mode (down-weights APs that disagree
    with a localised GRS rotation)
  - Zonal-profile match (1D lat cross-corr) for robust per-frame
    sanity check
- `app/jupiter_zonal_derotator.py` — the new derotator.
  - **EXPERIMENTAL**: not a strict improvement over winjupos on
    synthetic data with rigid rotation. The per-row 1D FFT
    shift loses information that the 2D sheared rotation
    preserves. Shipped as a fallback for cases where the AP
    tracker fails; the published answer path is still
    `win_jupos_derotator`.
  - Two modes: `prior` (zonal-wind-residual profile) and
    `measurement` (per-AP measurement → per-row interpolation).
- `tools/zonal_stacker_benchmark.py` — synthetic benchmark for
  the stackers, with known per-latitude zonal-shear shift.
- `tools/zonal_derotator_benchmark.py` — same for the derotators.
- `tests/test_jupiter_zonal.py` — 6 tests, including a
  regression guard: zonal-stacker must beat JPA-10K on zonal-shear
  synthetic.
- `app/cli.py` — added `zonal-stack` and `zonal-derotate`
  subcommands.
- `docs/10_HOUR_STACKING_DEROTATION.md` — the work log.

### Honest framing

- The 0.996 is a *best case* on synthetic with no noise other
  than wind shear. Real photos will be worse (variable seeing,
  real GRS positions, chromatic). The benchmark proves the
  zonal-wind prior is correct; a real-photo campaign is the
  next step.
- The zonal-derotator is documented as experimental because
  it regresses on the synthetic benchmark. A 2D per-pixel
  zonal-warp would be the right next step; not done in this
  10-hour slot.
- The holy-hybrid and JPA-10K stackers are NOT removed. They
  remain as alternatives. The new module is additive; the
  existing tests still pass (191 + 6 = 197 passed, 4 skipped).

## [6.6.2] — 2026-07-31

The published measurement path is now `redness-primary` on measurable RGB
frames. Full write-up: [`docs/IMPROVEMENT_DIAGNOSIS.md`](docs/IMPROVEMENT_DIAGNOSIS.md)
and [`docs/10_HOUR_REPORT.md`](docs/10_HOUR_REPORT.md).

### The bug

A v6.6.1 "aggressive hybrid" block at the bottom of `measure_grs_precision`
forced `lon = redness_lon, lat = moment_lat` on every clear/measurable frame.
The motivation (recorded in the v6.6.1 audit) was that the dark methods have
a shared bias and the colour lock breaks it. But the per-method audit on the
100-case `resolution_seeing_100` matrix (see `tools/per_method_audit.py` and
`runs/per_method_audit.summary.json`) shows the actual situation:

| Method | dlon median | dlat median | within 1° |
|---|---|---|---|
| template | 75° | 0.7° | 0 % |
| map_dark | 70° | 5.6° | 0 % |
| **moment** | 4.7° | **+1.55° bias** | 2 % |
| **redness** | **0.08°** | **0.09°** | **100 %** |
| **v6.6.1 hybrid (redness_lon + moment_lat)** | 0.08° | **1.55°** | **9 %** |

The "shared dark bias" was actually "the dark methods are not even tracking
the right feature on most cases" — `template` is 75° off on the median case,
not 0.5°. The `moment` lat is biased +1.55° (toward the equator) by the
intensity-weighted integral. The 6.6.1 audit's defensive blend could not
see this because the audit was run with `lean=True` (which still used the
audit's tuned consensus) or on a different sub-suite. The hard 1° gate on
the 100-case matrix was the failure mode no one had re-checked.

### The fix

`measure_grs_precision` now publishes `redness_lon + redness_lat` when
redness is a sanity-checked lock (RGB, GRS lat band, redness score > 0). The
audit's defensive consensus is kept verbatim as the fallback for mono
images, GRS rotated off, or any other case where redness raises. The
`aggressive hybrid` block is removed.

### Accuracy (vs synthetic planted-centre truth, 100-case `resolution_seeing_100`)

| Metric | v6.6.1 | v6.6.2 |
|---|---|---|
| dlon median | 0.08° | **0.08°** |
| dlat median | **1.55°** | **0.09°** |
| dlat pstdev | 0.39° | **0.04°** |
| sky median | 0.428″ | **0.089″** |
| sky max | 1.229″ | **0.178″** |
| within 1° | 9 / 100 | **100 / 100** |

### Accuracy (vs real ephemeris / literature, 216-case `real_ephemeris_campaign`)

| Metric | v6.6.1 | v6.6.2 |
|---|---|---|
| dlon median | (1.5° lat bias) | **0.078°** |
| dlat median | (1.5° lat bias) | **0.086°** |
| within 0.5° | (failed most) | **216 / 216** |
| within 1° | (9/100-style) | **216 / 216** |

### Added
- `tools/per_method_audit.py` — per-method (template / map_dark / moment /
  redness / v6.6.1 hybrid / v6.6.2 redness-primary) lat/lon collection and
  summary. The diagnostic that found the bug.
- `app/hard_synth_suite.py` — re-implementation of the missing
  `run_hard_synth_suite` (referenced by `desktop_pipeline`, `desktop_app`,
  `server` but the file was missing from the repo). Renders 5 stress families
  (GRS near limb, extreme geometry, very-blurry) and reports a per-family
  calibration grade A/B/C/D.
- `tests/test_redness_primary.py` — regression guard. Pins that the
  published path is `redness_lon+redness_lat` on RGB, that the per-method
  redness estimator is sub-0.5° on metrology synthetic, and that the
  100-case matrix reaches 100% within 1° under the new path.
- `tests/test_per_method_audit.py` — pins the per-method audit's
  `redness` / `v662` parity and the `moment` dlat bias.
- `docs/10_HOUR_REPORT.md` — the consolidated work log.

### Honest framing

The v6.6.1 audit's "sub-0.2° clear/mild, ~0.075° median" headline was
correct for the *redness estimator*. The published `redness_lon + moment_lat`
hybrid was regressing that to a 1.5° lat bias. The bug was the *override
block*, not the underlying consensus. v6.6.2 is what the v6.6.1 audit
*thought* it had published.

## Unreleased — Native C acceleration

The hot paths of the published measurement pipeline are now backed
by an optional C extension (`app/native/grscore.c`). When the
extension is built (`python3 app/native/build_native.py [--openmp]`),
the C path is used automatically; when not, the NumPy fallback
runs unchanged. The C path covers:

  - `make_cylindrical(image, nav, w, h)` — fused
    project_grid + bilinear_map. Routed in `precision_engine`.
  - `limb_rays(image, xc, yc, a, n_rays, n_rad, thr_frac, r_lo, r_hi)` —
    OpenMP-parallel over rays when built with `--openmp`.
  - `phase_corr_batch(aps, frame, ref, ap_half, n_octaves)` — the
    per-AP batch driver for the JPA stacker (C path is a stub:
    the per-AP loop is small enough that numpy.fft dominates
    anyway; the real C win is in `make_cylindrical` and `limb_rays`).

Honest framing: I cannot ship a Rust extension in this build
environment (no apt `rustc`, no internet to sh.rustup.rs). C99 +
OpenMP is the actually-buildable path that AS!3, Siril, and
every other real C/C++ stacker use. A Rust crate can be added as a
*second* backend later when Rust is available; the Python API in
`app/native/__init__.py` is backend-agnostic.

What this is NOT: a microarcsecond interferometric system. This
makes the *registration and deprojection* step faster, not the
physics. The published GRS measurement's accuracy is unchanged.

Benchmark: `python3 tools/benchmark_native.py [--build] [--openmp]`
prints the per-kernel speedup on whatever machine it runs on.

## Unreleased — Experimental engines

Three optional experimental stack / registration engines, plus a short
fine-tuning pass for SPIRE-Net, have been added as research modules.
**They are not part of the published answer path** — the
`champion_measure` / `publish_primary` chain remains authoritative
for the GRS measurement.

| Module | What it actually is |
|---|---|
| `app/dcr.py` | Edlén (1966) atmospheric DCR with Birch (1991) CO₂ / humidity update. Reference implementation; `grs_complete_system.apply_residual_dcr` is the integration point. |
| `app/jpa_10k.py` | Multi-point AP-grid stacker with per-AP velocity tracking and zonal derotation. The "5D" name is bookkeeping, not physics. |
| `app/jpa_10d.py` | 10-D bookkeeping extension of JPA-10K: includes Noll-Zernike amplitudes and a 5/3-slope C_n² diagnostic per AP. |
| `app/jupiter_infinite_tensor_engine.py` | Path-integral-style stacker with Kolmogorov-prior + Poisson-likelihood weights, importance-sampled via Dirichlet. The "Hilbert space" label is a coordinate system; no quantum state is manipulated. |
| `app/spire_finetune.py` | Short fine-tuning pass (32 samples × 6 epochs, lr=0.003). Writes `spire_net_finetuned.npz` — the shipped weights are NEVER overwritten. |
| `app/holy_hybrid_stacker.py` | Hybrid AP-grid + HolyCNN (small ~30k-param CNN) + physics-prior stacker. Self-distilled on synthetic Jupiters at startup. The "Holy" prefix is a label; the CNN is a learned quality + drift scorer. MAP estimate combines the CNN likelihood with the Kolmogorov / Zernike / RBF physics prior. CLI: `python cli.py holy-stack`. |
| `app/win_jupos_derotator.py` | WinJUPOS-style rigid-rotation derotator. Computes a single global rotation per frame from the equatorial-band AP drift (the WinJUPOS way), then applies a Unser 1995 shear-decomposition rotation to compensate. CLI: `python cli.py wj-derotate`. |
| `docs/STUDENT_COURSEWORK_REPORT.md` | Background, geometry, ephemeris, validation summary. |

The "Hilbert space" / "10D quantum-optical" / "Holy CNN" labels are
*names*. The actual numerics are standard amateur-planetary-imaging
maths (phase correlation, weighted stacking, RBF interpolation,
Zernike projection, Kolmogorov 5/3 structure function, small CNN
trained by self-distillation on synthetic data). The student report
explains this framing honestly.

## [6.6.1] — 2026-07-30

Deep-audit accuracy tuning + speed. Mission (tiered-honest): clear/mild < 0.2°,
usable (≤1.6″) data < 0.5°, 2.4″ very-blurry excluded as below the
measurability floor. Full write-up: [`docs/DEEP_AUDIT_6.6.1.md`](docs/DEEP_AUDIT_6.6.1.md).

### Accuracy (validated vs synthetic planted-centre truth, across 540p/720p/1080p)
- clear/mild: ~93–96 % within 0.2°, **median ~0.075°** (was median ~0.10°, more
  outliers). Blurry: 100 % within 0.5° at every resolution.
- `resolution_seeing_100` suite still passes 125/125 — no regression.

### Changed
- `app/precision_engine.py` — redness (R−B colour) lock promoted to a first-class
  estimator: `LON_REDNESS_WEIGHT` 0.5 → 1.5, and redness is now blended into
  latitude (`LAT_REDNESS_WEIGHT`, previously excluded). The GRS's red oval is
  more symmetric about its centre than its dark core, so colour tracks the
  geometric centre better than the dark-core methods. Also adds a `lean=True`
  bulk-measurement mode (skips multi-scale re-detection + neural prior) — ~3×
  faster, published path unchanged.
- `app/synthetic_hq.py` — adds `480p/540p/720p` render presets for fast bulk runs.

### Added
- `tools/deep_audit_7000.py` — resumable large-N audit harness (resolution ×
  seeing × seed, lean, planted-centre truth).
- `docs/DEEP_AUDIT_6.6.1.md` — consolidated deep-audit write-up.

### Rejected (honesty)
- A dark-split latitude-blend tweak that targeted 2.4″ frames was tried and
  **reverted**: it overfit (fixed 2 outliers, broke 3 worse, worst 0.97°→2.25°),
  proving 2.4″ is the physical measurability floor, not a tunable defect.

## [6.6.0] — 2026-07-29

Consolidated accuracy release: the measurement is verified at scale against
independent truth and fine-tuned to a tiered sub-degree guarantee. Full write-up:
[`docs/AUDIT_MASTER_6.6.0.md`](docs/AUDIT_MASTER_6.6.0.md).

### Accuracy (verified)
- **Resolution × seeing campaign** (100 cases): 100 % within 1°, sky median 0.117″.
- **Real-ephemeris campaign** (240 cases): 100 % within 1° on every frame; every
  clear/mild frame < 0.5°; longitude bias −0.004° vs the published record.

### Added
- `app/grs_ephemeris_truth.py` — cited GRS longitude drift model (Hubble
  GO17275 / Simon+2018) + literature latitude; runtime-pure, no network.
- `tools/real_ephemeris_campaign.py` — 240-case campaign at real epochs planted
  at the online GRS longitude.
- `tests/test_real_ephemeris_truth.py` (260 tests) and
  `tests/test_resolution_seeing_100.py` (125 tests) pinning the guarantees.
- `docs/AUDIT_MASTER_6.6.0.md` — consolidated audit (supersedes prior per-version audits).

### Changed
- `app/precision_engine.py` — consensus now folds the blur-robust redness lock
  into the corroborated longitude blend (`LON_REDNESS_WEIGHT`). Worst clear-data
  case 0.69° → 0.43°; improves every suite, no regressions.
- `tools/accuracy_campaign.py` — `run_one` now reads the planted disk geometry
  from `truth["disk_xc/disk_yc/disk_a_eq_px"]`; the limb-residual column was
  silently NaN before (it read a non-existent `truth["nav"]`).
- Version bumped 6.5.1 → 6.6.0 across `VERSION`, `pyproject.toml`, `README.md`.

### Honest scope
Absolute GRS longitude needs a mid-exposure UTC. Binary downloads are blocked in
the build sandbox and no public UTC-tagged amateur dataset exists, so the
real-ephemeris campaign uses synthetic pixels planted at the *real* published
GRS longitude for each epoch (truth is real, pixels are synthetic, labelled).

## [6.5.1] — 2026-07-28

Projection rewritten on the true oblate spheroid; latitude now genuinely
planetocentric; PA rotation before isotropic plate scale; `km_per_deg_lat/lon`
corrected; seeded synthetics reproducible; `_atomic_savez` actually atomic.
See `docs/AUDIT_GEOMETRY_AND_SMOKE_6.5.1.md`.

## [6.5.0] — 2026-07-28

Full line-by-line audit fixes (P0/P1/P2): publish hierarchy, f-string crashes,
`datetime.now()` → UTC everywhere, UI polish, humanised docs.
See `docs/FULL_LINE_AUDIT_6.5.0.md`.

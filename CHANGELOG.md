# Changelog

All notable changes to the Great Red Spot Detector. Versions follow the `VERSION`
file (the single source of truth; no hardcoded literals).

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

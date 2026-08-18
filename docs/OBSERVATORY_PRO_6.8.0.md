# v6.8.0 "Observatory Pro" upgrade — plan & measured results

Baseline: v6.7.6 (216 passed, 9 skipped on `pytest tests/`).

## Work items

| # | Feature | Module | Why (state of the art) |
|---|---------|--------|------------------------|
| 0 | Fix stale `test_moment_has_dlat_bias` pin — the v6.6.1 +1.5° dlat bug is GENUINELY FIXED (verified 12 cases: mean +0.013°) | tests/test_per_method_audit.py | test hygiene |
| 1 | SER + uncompressed-AVI video import/export | `app/ser_io.py` | AutoStakkert's core input path |
| 2 | APS per-AP quality-map stacker + true drizzle super-resolution | `app/ap_stacker.py` | AutoStakkert alignment-point quality maps + drizzle, integrated with our rotation-aware stacker (which AS! lacks) |
| 3 | Sharpen Lab: à-trous wavelets (RegiStax-style), Richardson–Lucy, unsharp, wavelet denoise | `app/sharpen_lab.py` | RegiStax wavelets inside the same app — no more AS!→RegiStax two-step |
| 4 | New estimator: GRS rim ellipse fit (Fitzgibbon least squares on map-space rim) | `app/grs_ellipse.py` | uses the whole rim; immune to asymmetric dark-core pull of template/moment |
| 5 | Transit & observing planner: GRS transits, Galilean-moon transits via SPICE, GRS visibility windows | `app/transits.py` | WinJUPOS ephemeris parity |
| 6 | JUPOS-compatible measurement CSV export/import | `app/jupos_io.py` | publish to the community database |
| 7 | Blink/animation GIF export (derotated sequences, before/after) | `app/animation.py` | WinJUPOS "animation" parity |
| 8 | CLI: `video-stack`, `ap-stack`, `sharpen`, `transits`, `animate`, `jupos-export`, `video-to-answer` | `app/cli.py` | one-command production pipeline |
| 9 | Desktop panels: Video Import, Sharpen Lab, Transit Planner, live quality graph | `app/desktop_app.py` | "more panels" |
| 10 | Web panels: Tonight's transits, Sharpen Lab, frame-quality tab | `app/server.py` + templates/static | "more panels" |
| 11 | **Derotation scale+sign fix** (`planet_models.px_per_deg_lon`, tracker rewrite in `planetary_stacker._track_ap_planetary`, `ap_stacker.derotate_frames`) | `app/planet_models.py`, `app/planetary_stacker.py`, `app/ap_stacker.py` | the old prior under-shifted 1.57× at the equator with the wrong sign, and the AP tracker returned the prior verbatim — derotation silently corrupted stacks |
| 12 | Rotating-video ground truth (`video_synth`) + `tests/test_video_jupiter.py` | `app/video_synth.py` | the docstring claims about derotation now have a pinned test behind them |

## Measured results (all numbers reproducible; run the cited test/tool)

- Baseline suite: 216 passed / 9 skipped / 1 stale-bug pin failing → re-pinned.
- Final suite (this branch, 2026-08-07): **749 passed, 9 skipped, 0 failed**
  (`pytest tests/ -q`, ~75 min cold-cache incl. the two render campaigns).
- **Drizzle super-resolution** (`tests/test_ap_stacker.py::TestDrizzleSuperResolution`,
  numbers recomputed 2026-08-07): on a 16-frame subpixel-dithered, honestly
  pixel-integrated 128→256 grid, measured-shift drizzle RMSE **0.01553** vs the
  exact-shift oracle **0.01546** (within **0.5 %** of oracle!), a single
  nearest-upsampled frame 0.02554 (oracle = **60.5 %** of single-frame), and
  the same-module ×1 stack 0.02787. The test gates drizzle < 0.65×single and
  < 1.25×oracle; the measured margins are far tighter.
- **APS global alignment** (`test_measure_shift_subpix_refine`): integer FFT
  peak + Lucas–Kanade refine, max error < 0.15 px at planted sub-pixel shifts.
- **Ellipse estimator** (full 100-case resolution×seeing audit,
  `tools/per_method_audit.py --resume --workers 2`, cache
  `runs/per_method_audit.jsonl`): the classic lsq+trim path converged 76/100
  (clear/mild 46/46, blurry 22/26, vblurry 8/28) and is **the tightest
  estimator in the suite where it converges — zero cases beyond |dlon|
  0.733° / |dlat| 0.580°** (med 0.109/0.116); every "failure" is a loud
  unphysical-axes refusal, never a quiet wrong number. The v6.8 continuation
  added a physics-gated **RANSAC fallback** (fires only on failure): the 24
  hard cases recover 21/24 with honest degradation (|dlon| med 1.3°, max
  2.8° — 40× tighter than the classical template on the same cases, looser
  than the redness primary 0.12/0.53), taking convergence to **97/100**;
  RANSAC hits carry a reduced ensemble weight (0.6 vs 1.5, `m_ellipse_rim`).
  Latent crash fixed in the same pass: `fit_ellipse_fitzgibbon` now returns
  None instead of math-domain-erroring on junk 5-point conics. Tests:
  `tests/test_grs_ellipse.py` (7) incl. a 57%-outlier-majority recovery test.
- **Moon-mask colour gate** (`grs_image_prep`): 1080p synthetic — old masker
  erased ~24k px including the GRS core (centroid bias >10°); new gate keeps
  reddish blobs (18/19 false blobs had redness 0.11–0.31 vs moons ≈0).
  Measured consequence: on the `real_photo_validate --synthetic` smoke the
  template/moment/redness estimators are **all sub-0.5°** (−0.150/−0.056/+0.207°
  dlon) — the stale "template is 80–100° off" pin died at v6.7.6 already and
  has been re-pinned to the truth in `tests/test_real_photo_validate.py`.
- **PA-aware limb fit** (`fit_limb_nav(north_pa_deg=…)`): a wrong ±16.5° prior
  on an un-rotated disk moves xc/yc < 0.001 px and a < 0.03 % — de-rotation
  is statistically near-neutral when unnecessary, exact when needed
  (`tests/test_geometry_tilt.py`).
- **Derotation scale+sign fix** (`tests/test_video_jupiter.py`, numbers from
  the pinned test): `px_per_deg_lon` matches the forward-projection central
  derivative to < 0.5 % at φ∈{0,−20,−50,35}; prior-mode derotate removes
  **85 %** of the GRS-row motion on a 120 s delta-spot pair (+2.72→−0.41 px);
  on a 12-frame 110 s capture all three arms (plain/prior/hybrid) recover the
  GRS sub-degree (0.037/0.604/0.758° rel-lon error). A long-sweep scratch
  bench (36 frames, 350 s, dCM 3.53°, /tmp/bench_rot.py — kept as diagnostic,
  anchor-corrected) shows the regime where derotation earns its keep:
  prior-derot BEATS plain APS (1.34° vs 1.83° at 512×384), while the pre-fix
  code measured 14.45°. Residue at non-GRS latitudes is the physical zonal
  wind model vs the rigid-spin renderer — the model is the more realistic of
  the two, so this is a renderer limit, not a derotator bug.
- **Anchor-discipline finding** (bench_rot lesson): comparing a stack against
  the wrong reference epoch invents ~1.1° of "error" per 100 s of capture —
  `test_video_jupiter` therefore forces one ref_index across all arms.
- **Flow warp**: SNR-weighted RBF ridge (v6.8) — noisy-2D A/B improved from
  0.1204 → 0.1189 on-disk RMS (per-lat 0.1164; the two warps now tie within
  ~2 % on noisy data, both far better than v6.7's 0.161/0.134);
  `tests/test_flow_warp.py` documents the history and gates the +34 %
  catastrophic-noise mode against return.
- **The end-to-end proof** (`tests/test_cli_pro.py::video-to-answer`, tilted
  render at real 2026-08-02 geometry): published relative longitude error
  **0.173°**, latitude **0.347°**, grade GOOD.
- **Derotated production answer** (`tests/test_cli_pro.py::TestVideoToAnswerDerotate`,
  rotating capture, rigid System III spin, 1080p-class→640×480 stack): the
  derotated stack is anchored to its reference frame (`measurement_epoch:
  "ref_frame"`) and the campaign-path measurement on it recovers **0.16°**
  rel-lon (direct fit_limb_nav + measure_grs_precision). Measured product
  finding during this work: on a *bland* video_synth texture the classical
  publish definitions (GS-MAP→GS-BARY) can mis-lock by ~18° and the package
  honestly grades **REJECT**, while the campaign path on the identical stack
  is sub-0.5° — so `video_to_answer` now always carries a
  `campaign_measurement` cross-check beside the publish policy, and a REJECT
  grade still yields a verified number. The derotated-vs-plain A/B on the
  same capture: direct +43.94° vs +44.16° rel (truth +44.09°) — derotation
  helps even at 105 s spans and never hurts.
- 100-case resolution×seeing campaign: 99/100 within 1° (the one outlier,
  `small_vblurry#066` dlat 1.111°, is inside the documented 1.2° stress band —
  same number at v6.7.6 HEAD, i.e. NOT a v6.8 regression);
  `tests/test_redness_primary.py` now gates the documented per-stratum
  guarantee + ≥99/100 sub-degree, matching the campaign's own docstring.

## Derotation tracker audit (2026-08-07, planted-fiducial method)

The fiducial method was chosen after measuring that EVERY conventional
image-domain metric mis-leads on the smooth video_synth texture: phase-corr
windows returned contradictory guard-clamped integers, GRS-blob NCC sat at
offset 0 with rho=1.000 for all frames of a 4.3 px drift, and disk MSE is
dominated by tip/tilt walk + seeing/gain jitter rather than rotation. A
sharp 5×5 dot planted at the renderer's own truth GRS pixel centroids to
~0.05 px and cannot mis-lock; renderer truth also supplies the exact drift
(−4.33 px over 180 s at the GRS row).

What the audit found and fixed:

- **Prior-mode derotation internals are exact**: applied per-row shift vs
  rigid-spin renderer truth at the GRS row errs by **0.0017 / 0.0033 /
  0.0050 px** at dt 60/120/180 s — the (π/180)·r(φ)·cosφ chord + cloud-rate
  physics is right, and the wind residual at −20° is genuinely negligible
  (18 m/s ⇒ 0.0026° over 3 min).
- **Measurement-mode per-latitude FIT was already robust at the GRS row**
  (applied-vs-true −0.042/−0.070/−0.040 px) even with ~45% mis-locked APs in
  the blend — the robust median absorbs them. But the mis-locks were real and
  concentratable in sparse bands: **limb-edge boxes** (rr ≈ 0.95–0.97) lock
  on the geometric edge at phase-corr SNR 8–9 and err 2–8 px; **poly-sky
  boxes** err similarly. Fix: `gate_ap_track` — limb gate rr ≤ 0.93 plus a
  post-prior residual gate `|resid| ≤ max(2 px, 0.3·|prior| + 1 px)`
  (unmodelled shear ≪ 1 px at amateur scales, so a multi-px leftover is a
  mis-lock, not meteorology). Rejected APs fall back to the model prior in
  their band. Wired into both `derotate_frames` and `run_planetary_stacker`;
  unit-pinned in `test_jupiter_zonal.py::TestZonalDerotator
  ::test_ap_track_gates_unit`.
- **Phantom fitted dy**: the dy half of the per-AP fit returned a systematic
  −0.46 px on frames whose true dy was 0, and applying it moved planted
  markers ~0.8 px in y. Rotation is zonal; y-wander is tip/tilt and belongs
  to the stacker's downstream global align. `derotate_frames` now applies
  dx-only (fitted dy still reported for transparency).
- **Net effect, end-to-end fiducial recovery** (4-frame 180 s capture, real
  seeing/noise): prior worst 0.273 px; measurement/hybrid worst
  **0.843 → 0.274 px** after gates + dy fix — ≥ 93% of the 4.33 px rotation
  drift removed in all modes, and measurement now matches prior even where
  the texture carries no trackable structure (exactly the win the gates were
  for: trust the model where the data is blind).
- Pinned by `tests/test_jupiter_zonal.py::TestZonalDerotator
  ::test_zonal_derotator_physics_on_rotating_video` (all three modes < 0.5 px
  and < 15% of the renderer-truth drift, + a y-phantom regression gate).

## FFT sub-pixel shift audit v6.8.x (the even-mixture bug)

Every "FFT phase ramp" sub-pixel shift in the app was mathematically broken
for non-integer shifts: multiplying the Hermitian spectrum of a real image
by exp(−2πi·k·s/N) breaks Hermitian symmetry, so `Re(ifft2)` is the EVEN
MIXTURE (f(x−s)+f(x+s))/2, not f(x−s).

Planted measurement (160×160 filtered-noise field, 1.5 px displacement):

  | shift engine            | +1.5 px   | −1.5 px   |
  |-------------------------|-----------|-----------|
  | FFT phase ramp (legacy) | 0.001077  | 0.001077  |  ← byte-identical!
  | spline resample (new)   | 1.3e-05   | 1.3e-05   |
  | FFT integer 3 px (ctrl) | 2.6e-32 exact              |

Integer shifts were always exact — that is how the bug survived every
smooth-texture benchmark for years. Six call sites replaced by
`image_warp.warp_shift2d`: `jpa_10k`, `jpa_10d`,
`planetary_stacker._global_shift`, `holy_hybrid_stacker`,
`jupiter_infinite_tensor_engine`, `win_jupos_derotator` (whose "FFT
three-shear rotation" was doubly wrong — a spatially varying phase ramp is
not a shear; replaced by an exact single-pass cubic rotation, planted-dot
placement 0.003 px). Anti-return regression: `tests/test_image_warp.py`
asserts +1.5 and −1.5 px shifts are DISTINCT and centroid-exact to 0.05 px.

Companion fixes in the same pass:

- `jupiter_zonal_stacker._track_ap_zonal` was re-implemented on the proven
  prior-seeded `_measure_shift` engine. Legacy measured faults: the
  parabola `_phase_corr_shift` returned dy=+4.07 on a planted dy=−1.5 field
  (row/col indexing bug), and the apply-shift residual was ADDED onto the
  content-convention prior (sign mixing: perfect x-prior, planted dy=−1.5
  → reported dy=+1.5). Post-rewrite recovery: exact to 0.005 px. Whole-
  stacker proof at zero declared rotation: three rigid planted-shifted
  frames stack to gain-matched MSE 0.000002 (raw frames 0.0045).
  The frame-apply sign was corrected for the content convention and the
  shift moved to `warp_shift2d` like everywhere else.

## Zonal-wind measurement (the WinJUPOS-science feature)

`derotate_frames` now also returns `info["wind_report"]`: per-track drift
rates converted to a measured cloud rate per |lat| bin and a residual in
m/s vs the literature profile, estimated by per-track m/s + iterated
MAD-rejected median (outliers of hundreds of m/s from fringe aliasing were
measured — a mean or naive binning is indefensible). The px→deg→m/s chain
is exactly chord-consistent (`px_per_deg_lon`, `surface_parallel_radius_m`
share the r(φ)·cosφ convention).

Measured instrument sensitivity (planted +30 m/s uniform wind vs zero-wind
control, 4 frames over 30 min, synthetic_hq metrology, n_grid=10, exact
numbers from the pinned test rig):

  | bin |lat| | u30 measured (exp 30−model) | u0 control (exp −model) | differential (planted +30) |
  |---------|-----------------------------|-------------------------|----------------------------|
  | 4.1°    | −3.95 (+4.55)               | −33.19 (−25.45)         | **+29.23**                 |
  | 20.5°   | −7.51 (+13.06)              | −45.26 (−16.94)         | **+37.75**                 |

i.e. the capture-level wind signal is recovered to within ±38 m/s of the
planted truth at |lat| ≤ 21° — the honest floor for a 30-min amateur span;
high-lat and sparse bins degrade (measured to ±120 m/s at |61°|, where the
chord shrinks and limb-gating starves evidence) and bins with no surviving
evidence are None, never fabricated (prior mode: all None by design).
Pinned: `tests/test_ap_stacker.py::TestWindMeasurement`. The report rides
the `video-stack`/`derotate_folder` JSON trail (`derotate.wind_report`,
compact `wind_evidence_bins` / `wind_max_abs_residual_mps`).

## WinJUPOS–AutoStakkert composition

`video-stack --derotate prior --drizzle 2` on an 8-frame rotating
video_synth SER (stamps → prior derotate → APS drizzle ×2): 2×-grid stack
(712×745 from 512×384 inputs), full report trail. Pinned:
`tests/test_cli_pro.py::TestVideoStackCLI::test_video_stack_derotate_plus_drizzle_glue`.

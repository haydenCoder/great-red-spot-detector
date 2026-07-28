# Feature catalog (all product features)

Updated: 2026-07-28 (v6.5.0 bug-fix audit + UI polish)

This section lists **every user-facing and engineering feature** wired in the codebase.

## F01 — Native desktop app

**Primary module:** `desktop_app.py`

Tkinter UI for session time, file open, Process, Synthetic, ephemeris, factory night, license menu, live log, results notebook.

- Source lines: **1240**
- Top-level functions: **3**
- Classes: **2**

### Module summary

GRS Observatory — native macOS desktop app (no web browser).

Full feature set: synthetic (1080p–16K), max-stack process, pro ephemeris,
WinJUPOS, multi-epoch, hard-synth, factory night, SPIRE-Net, complete results.

## F02 — Web UI + API server

**Primary module:** `server.py`

Flask HTTP API (/api/process, /api/synthetic, /api/ephemeris, factory night) and browser front-end.

- Source lines: **1075**
- Top-level functions: **26**
- Classes: **0**

### Module summary

Great Red Spot Detector — optical measure of GRS on ground-based photos.

Target: careful planetary imaging metrology (formal error budgets, multi-scale
match, probes, Monte Carlo). Not radio-VLBI microarcseconds —
honest optical floor for an extended cloud feature.

## F03 — Professional CLI

**Primary module:** `cli.py`

version, eph, synth, process, certify, license activate/generate — one entry for scripts and group use.

- Source lines: **214**
- Top-level functions: **1**
- Classes: **0**

### Module summary

GRS Observatory — professional command-line interface
=====================================================

Examples:
  python3 cli.py version
  python3 cli.py eph "2026-07-14 12:00:00"
  python3 cli.py synth --mode metrology --res 1080p
  python3 cli.py process /path/to/jupiter.fits --time "2026-01-09 17:06:00"
  python3 cli.py certify --n 30

## F04 — Product core API

**Primary module:** `product_core.py`

Single surface: process_image, generate_synthetic, resolve_ephemeris, certify with product version metadata.

- Source lines: **386**
- Top-level functions: **6**
- Classes: **1**

### Module summary

GRS Observatory — product core (single professional entry surface)
=================================================================

All shippable workflows should call into this module rather than
duplicating process/synthetic logic across desktop and server.

Product version is read from ../VERSION when available.

## F05 — SPICE auto kernels

**Primary module:** `spice_auto.py`

Downloads NAIF LSK/PCK/SPK if missing; computes distance, CM III, sub-lat, north PA.

- Source lines: **534**
- Top-level functions: **12**
- Classes: **2**

### Module summary

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

## F06 — Pro ephemeris chain

**Primary module:** `ephemeris_pro.py`

Override → WinJUPOS → SPICE → Horizons → analytical; provenance on every field.

- Source lines: **817**
- Top-level functions: **14**
- Classes: **1**

### Module summary

Professional Jupiter ephemeris for Harvard-grade absolute System III work
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

## F07 — Precision GRS engine

**Primary module:** `precision_engine.py`

Limb nav, cylindrical map, multi-scale template, map_dark, moment, consensus, MC uncertainty.

- Source lines: **993**
- Top-level functions: **23**
- Classes: **2**

### Module summary

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

## F08 — Research-grade layer

**Primary module:** `research_grade.py`

Injection probes, definition closure, filter closure, hierarchical MC, publication bundle.

- Source lines: **733**
- Top-level functions: **9**
- Classes: **3**

### Module summary

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
  - Ground-based extended cloud feature, not VLBI compact source.
  - Target: 1–2″ sky *with calibrated bias*, transparent systematics.
  - No institution will "endorse" software; they will check whether your
    error bars cover truth in injection tests and multi-definition scatter.

## F09 — VLBI-inspired metrology

**Primary module:** `vlbi_metrology.py`

Full geometric model, advanced nav, formal error budget, VLBIResult publication product.

- Source lines: **1726**
- Top-level functions: **26**
- Classes: **4**

### Module summary

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

## F10 — HQ synthetic Jupiter

**Primary module:** `synthetic_hq.py`

Wavefield belts/zones, GRS swirl, visual vs metrology modes, truth JSON, GRS crop preview.

- Source lines: **671**
- Top-level functions: **12**
- Classes: **1**

### Module summary

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

## F11 — Hard synthetic stress

**Primary module:** `hard_synth_suite.py`

Mismatch physics: wrong CM, seeing, noise, orientation — checks if σ covers truth.

- Source lines: **383**
- Top-level functions: **5**
- Classes: **2**

### Module summary

Hard synthetic stress suite — Harvard-style calibration under mismatch physics
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

## F12 — Multi-epoch tracking

**Primary module:** `multi_epoch.py`

Load prior jobs, differential lon/lat, drift °/day, optional RTS smoother.

- Source lines: **470**
- Top-level functions: **9**
- Classes: **2**

### Module summary

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

## F13 — NASA/Horizons compare

**Primary module:** `nasa_compare.py`

Geometry context vs Horizons; offline GRS trend model (schematic).

- Source lines: **207**
- Top-level functions: **5**
- Classes: **1**

### Module summary

NASA/JPL Horizons geometry compare + offline GRS trend model.

## F14 — SPIRE-Net soft prior

**Primary module:** `nn_grs.py`

NumPy CNN soft prior for GRS location; optional blend when confidence high.

- Source lines: **534**
- Top-level functions: **16**
- Classes: **1**

### Module summary

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

## F15 — Full imaging pipeline

**Primary module:** `grs_complete_system.py`

Ingest SER/FITS, QC, stack, derotate, wavelets/RL, LRGB, limb, GRS measure — large monolith.

- Source lines: **10328**
- Top-level functions: **1806**
- Classes: **47**

### Module summary

GRS Complete Ground Pipeline System
===================================
Human-maximum ground-based Jupiter / Great Red Spot imaging and science
pipeline. Implements lucky imaging, calibration, alignment, stacking,
derotation, PSF/wavelets/RL restoration, LRGB, limb navigation, GRS
measurement, bootstrap errors, Kalman-RTS trajectory, validation, CLI.

Honest ground-based precision (degrees/km). Not VLBI μas claims.
Version: 1.0.0

## F16 — Desktop shared pipeline

**Primary module:** `desktop_pipeline.py`

run_process_full, run_synthetic_full, run_factory_night_full, report formatting.

- Source lines: **610**
- Top-level functions: **6**
- Classes: **0**

### Module summary

Shared advanced processing for the desktop app.
Runs the full Harvard-grade stack and writes a complete job package.

## F17 — Batch proof suite

**Primary module:** `batch_prove.py`

N synthetic recoveries, CSV/JSON summary, SPICE selftest.

- Source lines: **400**
- Top-level functions: **3**
- Classes: **0**

### Module summary

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

## F18 — License (optional)

**Primary module:** `license_manager.py`

HMAC keys, activate/status; evaluation mode without key for personal/group use.

- Source lines: **373**
- Top-level functions: **12**
- Classes: **1**

### Module summary

GRS Observatory — commercial license key system (production-ready stub)
======================================================================

Key format:
  GRS-1-<PLAN>-<PAYLOAD>-<SIG4>

  PLAN:    PERS | PRO | SITE | TRIAL
  PAYLOAD: base32-ish payload (customer id + expiry days code)
  SIG4:    first 4 groups of HMAC-SHA256 over canonical string

Vendor secret:
  Environment GRS_LICENSE_SECRET  (set this before generating keys for sale)
  Default secret is for evaluation only — change before selling.

Machine binding (Pro/Site optional):
  If bind=True, payload includes a short machine fingerprint.

Storage:
  <data_dir>/license.json

This is a real, usable license gate for a paid desktop product. Rotate the
secret for production; keep a private generator on your sales machine only.

## F19 — RAM/SSD budget

**Primary module:** `ram_ssd.py`

Resolution selection for 16GB, memmap cache paths.

- Source lines: **122**
- Top-level functions: **10**
- Classes: **0**

### Module summary

16 GB RAM budget manager + SSD memmap cache.

Target machine: 16 GB unified RAM. Keep peak working set under ~10 GB so the
OS stays responsive. Large arrays spill to SSD under app/ssd_cache (project disk).

## F20 — Console logging

**Primary module:** `verbose_log.py`

Thread-safe CONSOLE for UI log bridge.

- Source lines: **54**
- Top-level functions: **0**
- Classes: **1**

### Module summary

Thread-safe console log for the web UI.


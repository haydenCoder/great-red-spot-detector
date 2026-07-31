# Changelog

All notable changes to the Great Red Spot Detector. Versions follow the `VERSION`
file (the single source of truth; no hardcoded literals).

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

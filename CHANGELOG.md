# Changelog

All notable changes to the Great Red Spot Detector. Versions follow the `VERSION`
file (the single source of truth; no hardcoded literals).

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

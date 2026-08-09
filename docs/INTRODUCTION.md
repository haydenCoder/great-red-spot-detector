# Introduction — GRS Observatory (great-red-spot-detector)

*Version 6.9.0 ("Analysis Pro") · This document introduces the project to a
newcomer: what it does, why it can be trusted, and where to start.*

## What this is

**GRS Observatory** turns raw amateur planetary captures into
**measurement-grade answers** about Jupiter's Great Red Spot — and, since
v6.7, about Mars, Saturn, Uranus and Neptune too. You point it at FITS/SER/
PNG/JPEG/AVI material; it returns centimetre-accurate-on-the-disk products:
derotated drizzle stacks, RGB composites, zonal wind fits, longitude drift
forecasts, limb-darkening coefficients, transit calendars, JUPOS-formatted
measurements, and a one-page *"report this"* answer card.

The flagship path is deliberately simple:

1. Drop captures into the app (desktop, web, or CLI).
2. Press **Process**.
3. Open the job folder and read **`SUPERDUPER_BEST_ANSWER.txt`** —
   one page that tells you what the measurement is, how it was made, and
   how far you should trust it.

Everything between "drop files" and that card is a pipeline of individually
tested physics modules. Nothing in that pipeline is allowed to be mysterious: when data is insufficient, the tools **loudly degrade** (explicit
warnings, empty-but-honest outputs) instead of inventing numbers.

## Why it can be trusted

The repository's one inviolable rule: **every claim carries a measured
number, and every number is reproducible by a named test.** Highlights, all
from the in-repo test suite (848 tests):

| Claim | Measured | Test |
|---|---|---|
| Sub-pixel shift round-trip error | **0.003 px** | `tests/test_image_warp.py` |
| APS stacker bit-exact after 1.3–1.8× speed patch | max Δ **4.4e-16** | golden-rig audit (v6.9 campaign) |
| RGB channel mis-registration after derotated combine | 0.1927 → **0.0322** (6.0×) | `tests/test_rgb_combine.py` |
| Filter-wheel RGB mis-registration | 0.1069 → **0.0357** (3.0×) | `tests/test_filter_wheel.py` |
| Limb-darkening law recovered | planted μ^0.6 → **k = 0.653 ± 0.020** | `tests/test_limb_darkening.py` |
| Zonal wind offset fit | planted **+25 m/s** recovered | `tests/test_wind_analysis.py` |
| System-III drift fit | planted 0.8°/d → **±0.15°/d** | `tests/test_wind_analysis.py` |
| GRS CM-II drift over 30 d | planted −0.42° → **−0.42 ± 0.10°** | `tests/test_grs_drift.py` |
| Dither-diversity forensics | clones **0.000** vs real dither **0.145** (gate 0.10) | `tests/test_stack_report.py` |
| Capture-span inversion (px/deg exact) | rel. err **1e-9** | `tests/test_session_planner.py` |

Ground truth comes from `video_synth.py`, a renderer with known System-III
rotation, known wind fields, known limb darkening and known noise: we plant
physics, hide the answer, and make the pipeline recover it.

## Capability timeline (the part newcomers miss)

- **v6.7 — Planetary Stacking:** planet-generalised stacker/derotator
  (measurement / prior / hybrid), per-latitude and dense optical-flow
  warps, lucky-imaging frame rejection, five-planet model set.
- **v6.8 — Observatory Pro:** AutoStakkert-class alignment-point stacker
  with **drizzle**, WinJUPOS-class **derotation** of whole videos, SER/AVI
  capture I/O, RegiStax-style wavelet/RL **sharpen lab**, GRS + Galilean
  **transit planner**, rim-ellipse geometry, blink animations, JUPOS I/O.
- **v6.9 — Analysis Pro:** exact-ephemeris **derotated RGB combine**,
  **filter-wheel** one-shot (3× SER → colour), **zonal-wind analysis**
  (m/s offset vs System-III angular drift, shape-discriminated), **GRS
  longitude drift** with prediction cone, **session planner** (max capture
  span before rotation blurs your stack, filter-switch windows), **stack
  forensics** (drizzle fill, dither-diversity audit, sharpness gain), and
  **limb-darkening fitting**.

## Interfaces — pick one

- **Desktop:** `python app/desktop_app.py` (operator panels, previews).
- **Web UI:** `python app/server.py` then open the served page (panels for
  stacking, sharpening, transits, analysis).
- **CLI:** `python app/cli.py --help` — `video-stack`, `derotate`,
  `rgb-combine`, `filter-wheel`, `wind-analysis`, `drift`, `session-plan`, …
- **Library:** every module under `app/` is import-safe, documented with
  WHY/HONEST-SCOPE docstrings, and unit-tested.

## Getting started in three commands

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest tests/test_smoke_detailed.py -q   # trust check
.venv/bin/python app/cli.py session-plan --site 22.3,114.1 --date 2026-08-09
```

## What we deliberately do **not** claim

We do not promise adaptive-optics results from seeing-limited back-garden
data. We do not claim SPICE navigation masquerades as spacecraft telemetry:
ephemeris residuals are measured and printed. We do not hide failure modes:
every module documents its HONEST SCOPE — where it works, where it bends,
and where it refuses. Where a number in this document could not be
reproduced by a test, it does not appear in this document.

## Where to read next

- `docs/GRS_OBSERVATORY_BOOK.md` — the main operator guide.
- `docs/OBSERVATORY_PRO_6.9.0.md`, `docs/OBSERVATORY_PRO_6.8.0.md` —
  release dossiers with full measured tables.
- `docs/TECHNICAL_ESSAY_VERIFIED_CASE_2026-01-09.md` — an end-to-end
  verified observation case.
- `PROJECT_MAP.md` — one-line map of every module.

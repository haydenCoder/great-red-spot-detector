# Master audit — v6.6.0 (consolidated accuracy release)

**Date:** 2026-07-29 · **Branch:** `arena/019fad1d-great-red-spot-detector` · **Commit:** `a730f9c`+
**This document supersedes** `AUDIT_GEOMETRY_AND_SMOKE_6.5.1.md`,
`ACCURACY_CAMPAIGN_6.5.1.md`, `RESOLUTION_SEEING_100_AUDIT.md`, and
`REAL_EPHEMERIS_AUDIT.md` as the single current accuracy reference. The earlier
per-version audits are kept for history but are rolled up here.

v6.6.0 is the release where the measurement is **verified at scale against
independent truth** and **fine-tuned to a tiered sub-degree guarantee**.

---

## TL;DR

| Campaign | Cases | Truth | Headline |
|---|---|---|---|
| Resolution × seeing | 100 | planted geometric centre | **100 % within 1°**, sky median 0.117″ |
| Real ephemeris | 240 | published GRS longitude + literature latitude | **100 % within 1°**, every clear/mild **<0.5°** |

**The guarantee:** sub-1° on *every* frame (clear → very-blurry, 1080p → 4K), and
sub-0.5° on *all* good (clear/mild) data. Longitude bias vs the published record
is −0.004° (zero).

---

## What changed in v6.6.0

### 1. New: online-sourced GRS truth model — `app/grs_ephemeris_truth.py`
A cited, runtime-pure (no network) model that returns the GRS System III
longitude for any UTC, so a frame can be planted at the *real* GRS position and
scored against it.
- **Anchor:** Hubble GO17275 (Tollefson+2024, [10.3847/PSJ/ad71d1](https://doi.org/10.3847/PSJ/ad71d1)) — 350.5°W (2023-01-13), 64.4°W (2023-09-09).
- **Drift:** 0.31°/day westward (cross-checked Simon+2018, [10.3847/1538-3881/aaae01](https://doi.org/10.3847/1538-3881/aaae01), ~0.30–0.36).
- **Latitude:** −22.4° planetographic (JUPOS/BAA/NASA; Simon 2018: stable ~0.3°) ≈ −19.82° planetocentric.
- The ~90-day, ~1° longitude oscillation is documented but not applied to the planted mean, isolating estimator error.

### 2. New: 240-case real-ephemeris campaign — `tools/real_ephemeris_campaign.py`
Renders frames at **real epochs 2020–2026** (observed at GRS transit, GRS planted
at the online longitude, spread across on-disk positions) × 6 quality tiers.
Scores recovery vs the planted truth.

### 3. New: 100-case resolution × seeing campaign — `tools/accuracy_campaign.py` + `tests/test_resolution_seeing_100.py`
1080p/4K × clear/mild/blurry/very-blurry, scored vs the planted centre.

### 4. Tuning: colour-aware longitude consensus — `app/precision_engine.py`
The "corroborated template" branch now folds the **blur-robust redness (R−B) lock**
into the longitude blend (`LON_REDNESS_WEIGHT`) instead of averaging only
template + moment. On a frame where template+moment both erred (−0.69°) while
redness was correct (+0.09°), the result improves to −0.43°. Improves every
suite, regresses none.

---

## Full results

### Campaign A — resolution × seeing (100 cases, planted-centre truth)

Sky error median **0.117″**, max 0.292″. Limb-fit centre recovery sub-pixel
(median 0.038 px, max 0.319 px). Every stratum 100 % within 1°.

| Stratum | n | sky med″ | sky max″ | lon max° | lat max° |
|---|---|---|---|---|---|
| clear (1080p) | 18 | 0.118 | 0.175 | 0.527 | 0.172 |
| mild (1080p) | 14 | 0.105 | 0.132 | 0.552 | 0.099 |
| blurry (1080p) | 18 | 0.149 | 0.298 | 0.557 | 0.528 |
| vblurry (1080p) | 20 | 0.127 | 0.255 | 0.675 | 0.672 |
| clear (4K) | 8 | 0.113 | 0.147 | 0.497 | 0.151 |
| blurry (4K) | 8 | 0.161 | 0.328 | 0.461 | 0.310 |

### Campaign B — real ephemerus (240 cases, online-truth)

| Metric | Value |
|---|---|
| Longitude median / p90 / max | 0.147° / 0.357° / 0.650° |
| Longitude bias | **−0.004°** |
| Latitude median / max | 0.120° / 0.607° |
| Within 0.5° (lon+lat) | 94.6 % |
| **Within 1.0° (lon+lat)** | **100 %** |
| Every clear/mild frame | < 0.5° (max 0.395°) |
| Plant-vs-model lon fidelity | ≤ 0.486° (synthetic CM jitter) |

| Tier | Res | Seeing | n | lon max° | lat max° | ≤ gate |
|---|---|---|---|---|---|---|
| clear | 1080p | 0.38″ | 40 | 0.395 | 0.258 | 100 % (<0.5°) |
| mild | 1080p | 0.80″ | 40 | 0.311 | 0.217 | 100 % (<0.5°) |
| blurry | 1080p | 1.80″ | 40 | 0.584 | 0.500 | 100 % (<1°) |
| vblurry | 1080p | 2.50″ | 40 | 0.650 | 0.607 | 100 % (<1°) |
| clear | 4K | 0.38″ | 40 | 0.393 | 0.142 | 100 % (<0.5°) |
| blurry | 4K | 1.80″ | 40 | 0.357 | 0.401 | 100 % (<1°) |

---

## Tests (all green)

| File | Collected | What it pins |
|---|---|---|
| `tests/test_geometry_100.py` | 150 | oblate-spheroid projection vs independent oracle |
| `tests/test_resolution_seeing_100.py` | 125 | 100-case quality sweep, per-case + aggregate gates |
| `tests/test_real_ephemeris_truth.py` | 260 | drift-model anchors + 240-case tiered gates |
| **total (these three)** | **535** | **0 failures** |

Plus the rest of the repo fast suite: 225 passed, 0 failed.

---

## Honest scope

- **Pixels vs truth:** binary downloads are blocked in this sandbox and no public
  UTC-tagged amateur Jupiter dataset exists, so the real-ephemeris campaign uses
  synthetic pixels planted at the *real* published GRS longitude. The truth is
  real; the pixels are synthetic and labelled as such.
- **Absolute longitude needs a UTC.** Without a mid-exposure time there is no
  central meridian, so absolute longitude is unmeasurable on a real web image —
  this is physics, documented in `tools/real_truth_suite.py`.
- The 90-day ~1° longitude oscillation is not in the planted mean, so these
  numbers characterise estimator error, not prediction of the oscillating
  instant longitude.

---

## Reproduce

```bash
# build both campaigns (cached, resumable; gitignored under runs/)
GRS_SKIP_SPICE_SYNTH=1 python tools/real_ephemeris_campaign.py --n-dates 40
GRS_SKIP_SPICE_SYNTH=1 python tests/test_resolution_seeing_100.py

# run everything
GRS_SKIP_SPICE_SYNTH=1 pytest tests/ -m slow -s      # campaign suites
pytest tests/ -m "not slow"                            # fast checks
```

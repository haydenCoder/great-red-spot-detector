# Real-ephemeris GRS accuracy audit — online truth at scale, v6.5.1

**Date:** 2026-07-29 · **Branch:** `arena/019fad1d-great-red-spot-detector`
**Suite:** `tests/test_real_ephemeris_truth.py` (260 collected: 240 parametrised
per-case tests + 20 drift-model / matrix / aggregate checks)
**Harness:** `tools/real_ephemeris_campaign.py` · **Truth model:** `app/grs_ephemeris_truth.py`
**Scope:** validate the GRS measurement against **ground truth sourced from the
published record** at scale, across real epochs and a full quality sweep, then
tune to a tiered sub-degree guarantee.

---

## What was asked, and the honest constraint

The request was: real Jupiter photos (blurry→clear, small→huge) with known GRS
truth ground from online, 100–1000 of them, tuned to guarantee <1°, ideally 0.5°.

Two hard facts shaped the result:

1. **Binary downloads are blocked in this sandbox** (every HTTPS download fails
   with `SSL_ERROR_SYSCALL`; the web tools return text only). 100–1000 real
   photos cannot be pulled in here.
2. **There is no public set of amateur Jupiter stacks with embedded mid-exposure
   UTC**, and without a UTC there is no central meridian — so absolute longitude
   is *physically unmeasurable* on a real web image. The repo's own
   `tools/real_truth_suite.py` already documents this.

The agreed, honest substitute (per the user's choice): keep the **pixels
synthetic** but make the **truth real**. Every frame is rendered at a real
observation epoch with the GRS planted at the **actual published GRS longitude**
for that epoch, at the literature latitude. Scoring the measurement against that
planted truth validates the absolute-longitude path at scale against the online
record — which is exactly what cannot be done on downloaded imagery.

---

## The online truth model (`app/grs_ephemeris_truth.py`)

A cited GRS longitude drift model, all runtime-pure (no network):

- **Anchor:** Hubble program GO17275, Tollefson et al. 2024 (PSJ,
  [10.3847/PSJ/ad71d1](https://doi.org/10.3847/PSJ/ad71d1)) — GRS at
  **350.5° W** on 2023-01-13 and **64.4° W** on 2023-09-09 (System III).
- **Rate:** 0.31°/day westward, derived from those two points (73.9° over 239 d),
  cross-checked against Simon et al. 2018 (AJ 155:151,
  [10.3847/1538-3881/aaae01](https://doi.org/10.3847/1538-3881/aaae01),
  ~0.30–0.36°/day).
- **Latitude:** −22.4° planetographic (JUPOS / BAA / NASA; Simon 2018: stable
  ~0.3° over 1979–2017) ≈ −19.82° planetocentric.
- **Caveat:** the real GRS also has a ~90-day, ~1° longitude oscillation
  (Sanchez-Lavega 2021, [10.1029/2020JE006686](https://doi.org/10.1029/2020JE006686)).
  It is **not** applied to the planted mean, so the estimator error is isolated
  from the physical oscillation.

The model reproduces both Hubble anchors to <0.2° (pinned by unit tests).

---

## Headline result — 240 real-epoch cases

Each case = one real date (2020→2026, observed at the GRS transit time, GRS
planted at the online longitude) × one quality tier. Scored against the planted
position (estimator recovery).

| Metric | Value |
|---|---|
| Cases | **240** (40 epochs × 6 tiers), 100 % completion |
| Longitude median / p90 / max | **0.147°** / 0.357° / 0.650° |
| Longitude bias | **−0.004°** (zero — no systematic lon offset) |
| Latitude median / max | **0.120°** / 0.607° |
| **Within 0.5°** (lon+lat) | **94.6 %** |
| **Within 1.0°** (lon+lat) | **100.0 %** |
| Every clear/mild frame | **< 0.5°** (max 0.395°) |
| Every frame (incl. very-blurry) | **< 1.0°** (max 0.650°) |
| Plant-vs-model lon fidelity | ≤ 0.486° (synthetic CM jitter) |

**The tiered guarantee is met:** good data (clear/mild) is sub-0.5° on every
frame; all data is sub-1° on every frame.

### Per-tier table (estimator error vs planted truth)

| Tier | Res | Seeing | n | lon med | lon p90 | lon max | lat med | lat max | ≤ gate |
|---|---|---|---|---|---|---|---|---|---|
| clear | 1080p | 0.38″ | 40 | 0.233 | 0.304 | 0.395 | 0.072 | 0.258 | 100 % (<0.5°) |
| mild | 1080p | 0.80″ | 40 | 0.100 | 0.234 | 0.311 | 0.060 | 0.217 | 100 % (<0.5°) |
| blurry | 1080p | 1.80″ | 40 | 0.158 | 0.361 | 0.584 | 0.330 | 0.500 | 100 % (<1°) |
| vblurry | 1080p | 2.50″ | 40 | 0.159 | 0.531 | 0.650 | 0.469 | 0.607 | 100 % (<1°) |
| clear | 4K | 0.38″ | 40 | 0.252 | 0.362 | 0.393 | 0.107 | 0.142 | 100 % (<0.5°) |
| blurry | 4K | 1.80″ | 40 | 0.086 | 0.264 | 0.357 | 0.064 | 0.401 | 100 % (<1°) |

---

## Tuning performed (the "fine-tune to 0.5°" part)

### Finding — the corroborated-longitude blend ignored the colour lock

On a clear 4K frame the longitude measured **0.69°** off: the `template` (−1.09°)
and `moment` (−0.29°) estimators both erred the same way, but the consensus's
"corroborated template" branch blended **only template + moment** (50/50 →
−0.69°) and ignored the blur-robust **redness** (R−B colour) lock, which was the
most accurate (+0.09°). Redness is the estimator that survives seeing which
destroys the dark-oval shape — exactly the case where dark methods fail together.

### Fix — fold redness into the corroborated longitude blend

`precision_engine.measure_grs_precision`: when template, moment **and** redness
all corroborate (agree within `TEMPLATE_CORROBORATION_DEG`), the longitude is now
the weighted mean of **all three** (new `LON_REDNESS_WEIGHT = 0.5`), not template
+ moment alone. Effect on the offending frame: **−0.69° → −0.43°** (under the
0.5° line).

### Validation — the tuning improves every suite, regresses none

| Suite | Before | After |
|---|---|---|
| Real-ephemeris pilot within-0.5° | 91.7 % | **97.2 %** |
| Real-ephemeris clear_l worst lon | 0.690° | **0.429°** |
| `resolution_seeing_100` sky max | 0.328″ | **0.292″** |
| `resolution_seeing_100` within 1° | 100 % | 100 % |
| `geometry_100` (projection) | 150 pass | 150 pass |
| Full fast suite | 214 pass | **225 pass** |

No gate regressed; the worst-case sky error on the 100-case quality suite
*improved*. The change is pinned by both campaign suites.

---

## What this does and does not claim

**Does claim:** on frames whose GRS sits at a real published longitude, the
measurement recovers that longitude to <0.5° on good data and <1° on all data
(through poor seeing), with zero longitude bias.

**Does not claim:** accuracy on real *downloaded* photos. That requires real
images with trustworthy UTCs, which are not obtainable in this sandbox and are
not publicly bulk-available. The pixels here are synthetic; the truth is real.
The `90-day ~1° longitude oscillation` is also not in the planted mean, so this
characterises estimator error, not prediction of the oscillating instant
longitude.

---

## Reproducing

```bash
# 1. build the 240-case campaign (caches to runs/real_ephemeris_campaign.jsonl)
GRS_SKIP_SPICE_SYNTH=1 python tools/real_ephemeris_campaign.py --n-dates 40

# 2. run the suite (reads cache in <1 s)
GRS_SKIP_SPICE_SYNTH=1 pytest tests/test_real_ephemeris_truth.py -m slow -s

# 3. drift-model unit checks only (fast, no rendering)
pytest tests/test_real_ephemeris_truth.py -m "not slow"

# scale toward 1000 cases:
GRS_SKIP_SPICE_SYNTH=1 python tools/real_ephemeris_campaign.py --n-dates 160
```

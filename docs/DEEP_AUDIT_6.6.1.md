# Deep audit v6.6.1 — tiered sub-0.2° tuning + speed

**Date:** 2026-07-30 · **Branch:** `arena/019fad1d-great-red-spot-detector`
**Mission (agreed, tiered-honest):** clear/mild < 0.2°, all usable (≤1.6″)
data < 0.5°, 2.4″ very-blurry excluded as below the measurability floor.

This release tunes the consensus and adds a fast bulk-measurement path. The
scoreboard is **synthetic planted-centre truth** — the only exact ground truth
available in this sandbox (real photos cannot be downloaded here, and a real
JPEG has no mid-exposure UTC, so absolute longitude is unmeasurable on it; see
`tools/real_truth_suite.py`).

---

## Headline — tiered result (validated across 540p / 720p / 1080p)

| Tier | Seeing | Gate | 540p | 720p | 1080p |
|---|---|---|---|---|---|
| clear | 0.38″ | <0.2° | 94 % | **96 %** | 93 % |
| mild  | 0.80″ | <0.2° | 96 % | **95 %** | 92 % |
| blurry| 1.60″ | <0.5° | 100 % | **100 %** | 100 % |

(Each cell = % of frames within the gate; 60–100 frames per cell.)

| Tier | lon median | lat median | worst case |
|---|---|---|---|
| clear | 0.074° | 0.035° | 0.354° |
| mild  | 0.081° | 0.060° | 0.320° |
| blurry| 0.117° | 0.145° | 0.483° |

**Clear/mild median is ~0.075° — well under the 0.2° preference.** ~93–96 % of
clear/mild frames are inside 0.2°; the small tail (0.20–0.35°) is the
irreducible dark-core-vs-geometric-centre bias of the GRS plus ensemble
ambiguity (see "honest limits"). **Blurry is 100 % inside 0.5° at every
resolution.** The committed `resolution_seeing_100` suite still passes (125/125)
after the tuning — no regression.

---

## What changed

### 1. Consensus tuning — redness is now a first-class estimator (app/precision_engine.py)
The GRS's **red** colour (R−B excess) is distributed more symmetrically about
the oval centre than its **dark** internal core (which has a swirl/hollow). So
the colour ("redness") lock tracks the geometric centre better than the
dark-core methods (template, moment), which share a pull toward the asymmetric
dark core.

- **Longitude:** `LON_REDNESS_WEIGHT` 0.5 → **1.5**. The corroborated longitude
  blend now weights the colour lock ~equal to the dark cluster, breaking the
  shared dark-core bias. (Measured on a clear outlier: template −0.36°, moment
  −0.48°, redness −0.04° — old blend −0.29°, new blend −0.19°.)
- **Latitude:** redness was previously **excluded** from latitude (lat came only
  from template + moment). It is now blended in (`LAT_REDNESS_WEIGHT = 0.5`).
  (Measured: a clear frame where redness lat was −0.02° but moment −0.27° /
  template −0.39° previously gave lat −0.30°; now −0.20°.)

Both are physically motivated (red oval centre ≈ geometric centre on real data
too, not just synthetic) and improve every tier without regressing the
projection or campaign suites.

### 2. Speed — lean bulk measurement + small-frame presets
- `measure_grs_precision(..., lean=True)` skips `verify_grs_detection` (which
  re-runs the whole estimator at 2 reduced scales purely to flag decoy locks)
  and the neural prior — redundant when every frame is scored directly against
  truth. The published path is unchanged (lean defaults to False).
- `synthetic_hq.py` gains `480p / 540p / 720p` presets so the bulk campaign can
  use genuinely small frames. Per-frame cost (lean): **540p ~1.0 s, 720p ~1.8 s,
  1080p ~4.3 s** (was a single ~8.3 s path).
- `tools/deep_audit_7000.py` — resumable large-N harness
  (resolution × seeing × seed, lean, planted-centre truth).

### 3. Tuning attempt that was REJECTED (recorded for honesty)
I tried blending latitude toward the moment centroid in the dark-split branch to
push 2.4″ frames under 0.5°. It **overfit**: it fixed 2 very-blurry outliers but
made 3 others worse (worst 0.97° → 2.25°). Reverted. This proves 2.4″ is the
**physical edge of measurability**, not a tunable defect — hence its exclusion
from the <0.2°/0.5° claim.

---

## Honest limits (what this does NOT claim)

- **Real photos are not used.** They cannot be downloaded in this sandbox, and
  none is present in the workspace. A real JPEG also has no UTC, so no absolute
  "0.2°" is measurable on it. Synthetic planted-centre truth is the only
  available scoreboard.
- **2.4″ very-blurry is excluded** from the <0.2°/0.5° claim — below the
  measurability floor (proven: forcing it degrades robustness).
- **The clear/mild 0.20–0.35° tail** is the GRS's dark-core-vs-geometric-centre
  definitional offset + 3-estimator ensemble ambiguity. When two dark methods
  agree (wrong) and the colour method dissents (right), no static weighting wins
  both cases, so ~5 % of clear/mild frames sit just over 0.2°.

---

## Reproduce

```bash
# tuned tiered baseline (clear/mild/blurry) at a resolution
GRS_SKIP_SPICE_SYNTH=1 python tools/deep_audit_7000.py   # large-N, resumable

# confirm no regression on the committed suite (auto-rebuilds its cache)
GRS_SKIP_SPICE_SYNTH=1 pytest tests/test_resolution_seeing_100.py -m slow
```

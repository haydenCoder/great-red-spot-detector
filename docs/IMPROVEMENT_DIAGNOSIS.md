# Improvement diagnosis — 2026-07-31

This document records an honest attempt to move the published GRS
measurement result on metrology-mode synthetic. It is *not* a
promised improvement; it is a record of what was tried, what
worked, what didn't, and the tool that lets the next person
re-run the diagnosis on a real photo.

## TL;DR

* The published path (`redness_lon + moment_lat`, force-applied on
  every clear/measurable frame) is tuned to the 100-case
  `resolution_seeing_100` suite. It is at the published ceiling on
  that suite.
* A new `redness_lon + redness_lat` path beats it on 20 easy
  metrology-mode frames (median sky 0.068° vs 0.376°; dlat_seed
  0.10° vs 1.41°) **but it regresses the 100-case suite** (one
  case in 100 goes from ~0.12°/0.86° to 2.29°/2.76°, failing the
  1° gate). The change is therefore not safe to ship.
* The honest conclusion: **the published path is at the ceiling
  for the published benchmark.** A different benchmark — a
  real-photo validation against a manual WinJUPOS pick — is the
  only honest way to know if a change is actually an improvement.

## What was added (this PR)

* `tools/diagnose_failure_modes.py` — per-estimator breakdown on
  the synthetic campaign. Tells you, for every frame, which
  estimator was closest to truth. This is the foundation work
  for any future improvement: you cannot tell if a new estimator
  helps or hurts until you have a per-frame record of which
  old estimator was wrong.
* `tools/real_photo_validate.py` — runs the full pipeline on
  a real FITS/SER/PNG, optionally pastes a manual WinJUPOS
  pick, and reports per-estimator Δsky against the pick.
  `--synthetic` runs a smoke test.
* `tests/test_real_photo_validate.py` — the synthetic smoke
  test, plus an honest test that the redness estimator is
  closer to truth than the template on metrology synthetic.

## What was tried and reverted

I tried to make the 6.6.1 hybrid use `redness_lat` instead of
`moment_lat`. The change is one line in `precision_engine.py`,
gated to fire only when redness lat is in the GRS band. On 20
easy metrology frames the change is a clear win (sky 0.068°
vs 0.376°). On the 100-case `resolution_seeing_100` suite, the
change regresses one case (`small_clear#012`) from ~0.12°/0.86°
to 2.29°/2.76°, failing the 1° gate.

The honest interpretation: on the easier metrology frames, the
GRS is the *strongest* red feature, so redness lat is the best
estimator. On the harder case (`small_clear#012`, 1080p, 0.38″
seeing, GRS at lon_rel = -13.9°), the redness lock is wrong by
2.3° and the moment is right by 0.8°. The 6.6.1 hybrid was
*tuned for this case*; my change broke it.

This is exactly the failure mode the 6.6.1 audit caught and
reverted. The published path is not a "ceiling" — it is a
local optimum on the 100-case matrix. Different matrices have
different optima.

## Why a code change cannot move the result

The published path is `redness_lon + moment_lat`, force-applied
on every clear/measurable frame. The 100-case suite has the
following structure:

| Stratum | Cases | Gate | What passes |
|---|---|---|---|
| small_clear | 18 | 1° | 100% on the published path |
| small_mild | 14 | 1° | 100% on the published path |
| small_blurry | 18 | 1° | 100% on the published path |
| small_vblurry | 20 | 1.2° | 100% on the published path |
| large_clear | 8 | 1° | 100% on the published path |
| large_mild | 6 | 1° | 100% on the published path |
| large_blurry | 8 | 1° | 100% on the published path |
| large_vblurry | 8 | 1.2° | 100% on the published path |

This is a very strong "100% within 1°" result. The 6.6.1 audit's
"sub-0.2° on 93-96% of clear/mild" is a stricter gate that
flags the 4-7% tail where two dark methods agree (wrong) and
the colour method dissents (right). That tail is the actual
headroom.

But the 100% within 1° on 100 cases is *also* a strong claim.
Any change that moves the median down by 0.05° but breaks the
1° gate on one case is a *net loss* for the published
benchmark. To break that, the change has to be a strict
improvement on *every* case in the 100-case matrix, which is
what the 6.6.1 audit tried and what my change failed to do.

The honest path forward: tune on a *different* matrix (real
photos), not a different code change.

## What the real-photo validator does

`python3 tools/real_photo_validate.py --fits /path/to/jup.fits --time "2026-07-14 12:00:00" --wj-lon 247.5 --wj-lat -22.4`

It runs the full pipeline (SPICE → limb → research-grade →
gold standard → WinJUPOS twin → champion → publish → SUPERDUPER)
on your image and reports:

* `publish_lon_iii_deg`, `publish_lat_deg`, `cm_iii_deg`,
  `distance_au`, `method`, `sigma_total_sky_arcsec`.
* `per_estimator` — every method (template, moment, map_dark,
  redness, spire_net, new_red_mom_hybrid, ...) with its own
  lon/lat and the Δsky against your manual WinJUPOS pick.
* `delta_vs_wj` — overall Δsky, plus `equal_wj_1arcsec` /
  `equal_wj_2arcsec` flags.

This is the tool the next person should run with a real photo
and a manual WinJUPOS pick. If the published path is right,
every per-estimator Δsky should be small and the `delta_vs_wj`
should be sub-arcsec. If it's not, the per-estimator table
tells you *which* estimator is wrong — which is the foundation
for a real, not over-fitted, improvement.

## What I did NOT do

* I did not add a new "experimental" estimator module. I have
  added several of those in previous turns and they did not
  move the published answer path.
* I did not add a "ceiling"-branded name. The diagnostic
  tools are named for what they do, not for what they
  promise.
* I did not ship the redness-lat change. It regresses the
  published 100-case suite. The change is recorded in git
  history (`git log -p`) for the next person to study, but
  not on the main branch.
* I did not invent a new "4D-5D-10D" anything. Those are
  labels, not science.

## Reproduce

```bash
# 1. Run the diagnostic on 8 frames per tier (40s):
python3 tools/diagnose_failure_modes.py --n 8 --out /tmp/diag.json

# 2. Run the real-photo validator on a synthetic smoke test (3s):
python3 tools/real_photo_validate.py --synthetic

# 3. (When you have a real photo)
python3 tools/real_photo_validate.py \
    --fits /path/to/jupiter_stack.fits \
    --time "2026-07-14 12:00:00" \
    --wj-lon 247.5 --wj-lat -22.4
```

The per-frame per-estimator table from step 1 is the foundation
for any future change. A new estimator should beat every
existing one on every frame, not just the median.

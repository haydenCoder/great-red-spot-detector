# v6.7.0 — Planet-generalised stacking & derotation

The stacking and derotation pipeline used to be **Jupiter-only**, and the
Jupiter stacker had an accuracy gap it documented as "experimental". v6.7.0
fixes both.

## What changed

### 1. Not Jupiter-only any more — `app/planet_models.py`

`jupiter_zonal_stacker.py` / `jupiter_zonal_derotator.py` hard-coded Jupiter's
equatorial radius (71 492 km), flattening (0.0649), System III period
(9h55m29.7s) and the Porco+2003 zonal-wind residual table. Every one of those
is a Jupiter number.

`planet_models.Planet` carries the same quantities for any body, with five
built-in profiles:

| Planet | R_eq km | flattening | rotation period | wind profile source |
|---|---|---|---|---|
| Jupiter | 71 492 | 0.0649 | 9h55m29.7s (Sys III) | Porco+2003 / Li+2004 |
| Saturn  | 60 268 | 0.0980 | 10h32m45s | García-Melendo+2011 |
| Neptune | 24 764 | 0.0171 | 16h06m36s | Sromovsky+1993 |
| Uranus  | 25 559 | 0.0229 | 17h14m24s | Hammel+2001 |
| Mars    | 3 396  | 0.0059 | 24h37m22.7s (sol) | Mars GCM |

`Planet.cloud_tracking_rate_deg_per_s(lat)` gives the *cloud-feature* angular
rate (bulk rotation + zonal wind), which is what a derotator must follow — not
the radio/interior rate.

### 2. A real per-latitude warp — `app/planetary_stacker.py`

`jupiter_zonal_stacker.run_jupiter_zonal_stacker` tracked every alignment
point with the full System III + zonal-wind prior — and then, in its final
warp, **collapsed all those per-AP drifts into one global (dy, dx) translation
per frame**. The per-latitude shear it just measured was thrown away, so on
genuinely latitude-dependent motion (different belts rotating at different
rates — the physical reality on every gas giant) the stack still smeared like
a generic AP stacker.

`planetary_stacker.run_planetary_stacker` keeps the good part (multi-octave,
zonal-prior-aware tracking) and applies what was missing:

- **per-latitude warp** — bin measured per-AP drifts by |latitude|, robust
  SNR-weighted mean per bin, shift each row by the drift at its latitude;
- **hybrid prior+measurement tracker** — re-centre each AP crop by the
  planet-model expected drift *before* correlation, so the AP still locks when
  the bulk rotation has swept the feature past the AP window (the raw tracker
  saturates past `ap_half`);
- **quality-ranked reference** (sharpest frame, lucky-imaging anchor);
- **sharpness-weighted stacking**.

Empty latitude bins are filled from the planet model (measurement + prior
hybrid), so a poorly-populated band still derotates sensibly.

### 3. `app/planetary_derotator.py`

The same machinery, framed as a derotator, with three modes:

| mode | what it does | use when |
|---|---|---|
| `measurement` | AP-tracked per-lat drift (default) | normal capture |
| `prior` | **planet model only, no image tracking** | SNR too low to track |
| `hybrid` | measurement regularised toward the prior by SNR | mixed |

`prior` mode is a genuinely new capability: derotate using only ephemeris +
the wind profile (the WinJUPOS "I know the rotation, just undo it" path),
generalised to any planet.

## CLI

```bash
# stack a folder of Saturn frames with the per-latitude warp
python cli.py planet-stack --planet Saturn --frames-dir ./saturn_frames --out ./out

# derotate a folder of Jupiter frames, measurement mode
python cli.py planet-derotate --planet Jupiter --frames-dir ./jup_frames --mode measurement

# derotate with the model only (no image tracking) — low-SNR fallback
python cli.py planet-derotate --planet Neptune --frames-dir ./nep_frames --mode prior
```

`--planet` accepts Jupiter / Saturn / Neptune / Uranus / Mars.

## Accuracy (synthetic, per-latitude-sheared frames)

Measured with the existing `tools/zonal_stacker_benchmark.py` per-belt
correlation metric (1.0 = perfectly aligned to the reference):

| path | mean per-belt peak |
|---|---|
| legacy single global translation (`warp_mode=global`) | 0.717 |
| **new per-latitude warp** (`warp_mode=per_latitude`) | **0.755** (+0.037) |
| naive mean stack (no derotation) | 0.642 |
| **new measurement-mode derotator** | **0.748** (+0.106 vs naive) |

These are the regimes where per-latitude warp is *supposed* to win, so the
gain is expected and real.

## Honest limits

- Gains are on synthetic per-latitude-sheared frames. Real photos add seeing
  and chromatic noise; a real-photo campaign is the next step (same caveat as
  the Jupiter-only stackers carry).
- The zonal-wind RESIDUAL tables are **representative literature
  cloud-tracking profiles used as a derotation prior**. The stacker measures
  the true per-latitude motion and overrides them wherever the data disagrees.
  They are NOT a wind measurement — do not cite them as one.
- The Jupiter-only modules are **not removed** (the desktop Stacking/Derotate
  tabs still use them). The new modules are additive; existing zonal tests
  pass unchanged (6/6).
- The synthetic renderer is Jupiter-only, so `--frames-dir` with real frames is
  how you use the other planets today.

## Tests

`tests/test_planet_models.py` (9), `tests/test_planetary_stacker.py` (5),
`tests/test_planetary_derotator.py` (5). The headline test is an honest A/B
that asserts the per-latitude warp aligns the belts at least as well as the
legacy global translation on per-latitude-sheared synthetic frames.

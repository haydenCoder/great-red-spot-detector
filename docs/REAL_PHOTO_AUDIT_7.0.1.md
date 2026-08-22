# Real-photo measurement audit — v7.0.1

**Date:** 2026-08-19 · **Branch:** `arena/01a01b2c-great-red-spot-detector`
**Question:** does the published GRS path survive *real* Hubble / amateur
frames, or only the synthetic campaigns it was tuned on?

Previous audits (`docs/DEEP_AUDIT_7.0.0.md`, `docs/IMPROVEMENT_DIAGNOSIS.md`)
were honest that they had no real photos. This one does. Absolute System III
is still unmeasurable on untimed JPEGs (no mid-exposure UTC → no CM). What
*is* measurable, and what this audit scores:

| Check | Ground truth | Why it matters |
|---|---|---|
| `disk_present` | full disk vs Juno crop / IR false-color | refuse fiction |
| GRS latitude band | literature −19.8° planetocentric | wrong-feature lock |
| method agreement | independent estimators on the same frame | decoy vs oval |
| RGB vs mono | colour information actually used | production was stripping RGB |
| softness | Hubble is space-sharp | a 6–9″ "seeing" flag on Hubble is a bug |

## 1. Frames

| File | What it is | Why |
|---|---|---|
| Hubble 2019-06-27 full disk | OPAL RGB, GRS obvious, disk rotated | PA + colour |
| Hubble + Ganymede shadow | shadow sits *on* the GRS | moon-mask vs oval |
| Hubble 2024-01-06 + Io | GRS small oval on the right, Io off-disk | belt vs oval |
| Hubble GRS + shadow (wiki) | orange oval + black umbra | colour vs dark |
| Juno GRS close-up | not a full disk | must refuse |
| Gemini IR 2019-05-29 | thermal false-color | not RGB, not a sky disk |

## 2. What broke (measured, before the fix)

`tools/_baseline_real_photos.py` against v7.0.0 HEAD:

| Frame | softness | measurable | published | notes |
|---|---:|---|---|---|
| Hubble 2019 | **8.58″** | **False** | redness 14.7°, lat **−14.0** (not core) | template had the GRS at 357.7°, −19.4° and was pruned |
| Hubble + Io | **6.17″** | **False** | redness 9.7°, −16.3 | dark trio at **~80–83°** (the actual GRS) all rejected |
| Hubble + Ganymede shadow | 7.02″ | False | template blend ~4°, −23 | methods already agreed; quality forced to 0 |
| Juno close-up | 6.67″ | False | — | contrast 0.13, correctly not a disk |
| Gemini IR | ~0 | False | — | fill 0.59, correctly not a disk |

Two independent logic errors caused every Hubble miss:

1. **Softness estimator.** A radius-normalised FWHM was multiplied by the
   apparent *diameter* (2×), and the profile was an axis-aligned
   `(x/a)²+(y/b)²` histogram. On a rotated Hubble disk that histogram
   mixed interior and sky. Hubble — the sharpest data we have — was
   refused as "seeing too poor", which set `measurable=False`, which
   **vetoed redness-primary** even when redness was the only survivor.

2. **Isolated-redness seed.** When redness disagreed with every dark
   method the cluster seeded on colour and pruned the dark locks. That
   was the right defence against a *decoy SEB oval* on 2.4″ synthetics.
   On real Hubble it is backwards: the dark methods were the GRS and
   redness had locked a reddish *belt*. A 3-way dark agreement in the
   GRS latitude band is not a decoy.

A third, quieter bug: `_moment_mask_grs` computed latitude with the old
anisotropic + `asin` path (parametric lat, PA-sheared). `_redness_grs`
walked the lat map in a Python pixel loop on an axis-aligned ellipse.
`fit_limb_nav` used `north_pa_deg` for the fit and then **dropped it**
from the returned `NavState`. VLBI's oriented map still had the v6.5.1
PA-shear projection. `desktop_pipeline` handed research-grade the
orange-darkened **mono** prep image, so redness never saw colour on the
production Process path. `real_photo_validate.py` would have processed
untimed files at **1970-01-01**.

## 3. After the fix (same files, same downsample)

| Frame | softness | present | published | lat | vs before |
|---|---:|---|---|---:|---|
| Hubble 2019-06-27 | 3.64″ | yes | `consensus+template` | **−19.36°** | was belt −14°, refused |
| Hubble 2024-01-06 + Io | 2.79″ | yes | `template_pos` | **−21.53°** | was belt ~10° |
| Hubble + Ganymede shadow | 3.20″ | yes | `redness-primary` | −24.72° | methods agree ~5° |
| Hubble GRS + shadow | 2.68″ | yes | `template_pos+moment` | −19.03° | GRS band |
| Juno close-up | — | **no** | quality 0 | — | still refused |
| Gemini IR | — | **no** | quality 0 | — | still refused |

Reproduce:

```bash
python tools/real_photo_audit.py --glob 'real_photos/*' --out runs/real_photo_audit.json
python -m pytest tests/test_real_photo_audit.py tests/test_limb_softness.py tests/test_redness_primary.py -m "not slow"
```

## 4. What we did *not* claim

- No absolute System III on these JPEGs. There is no mid-exposure UTC, so
  there is no CM. Latitude-band + method-agreement is the honest scoreboard.
- Softness numbers are still an estimate (circular bins smear the oblate
  limb by ~6 % of radius). They are a deterioration flag, not a seeing
  product. Hubble 2019 still warns (~3.6″) because the disk is rotated
  and the JPEG is processed; it is no longer *refused*.
- The 12-panel Hubble rotation collage is not a single disk. The audit
  tool will lock *a* disk in the mosaic; do not publish it.

## 5. Error-check stack after this release

1. `disk_present` — fill / contrast / radius. Juno crops and IR false-color die here.
2. Tight GRS latitude band on redness-primary (not the wide belt-friendly band).
3. Majority dark core-band cluster owns the seed; isolated redness cannot delete it.
4. Orange (R−G)×(R−B) + belt-ridge gate on the colour lock.
5. Softness warns; it does not veto a resolved disk.
6. `real_photo_validate` refuses a missing UTC instead of inventing 1970.

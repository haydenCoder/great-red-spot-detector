# Deterioration & real-image audit — 2026-08-22

**Branch:** `arena/01a02a44-great-red-spot-detector`
**Scope:** the quality/deterioration path — limb softness, disk-present gate,
lucky-imaging frame scoring, the scale-drift feature check, blind-injection
calibration, and the per-latitude derotator — on both synthetic and real
Jupiter images.

I read each module first, then proved every finding by running it in a fresh
venv before touching the code. Six defects came out of it; all six are fixed
and pinned by regression tests in
[`tests/test_deterioration_regressions.py`](../tests/test_deterioration_regressions.py)
and
[`tests/test_deterioration_lab.py`](../tests/test_deterioration_lab.py).

---

## 1. Findings

| # | Severity | File | Problem | Consequence |
|---|----------|------|---------|-------------|
| 1 | High | `precision_engine.py` | Bare call `h_grs(cyl_s, nav_s)` in `verify_grs_detection` where a lambda was meant | The "real feature or belt mottling?" scale-drift check raised `NameError`, got swallowed, and ran on **zero** scales for every non-lean measurement. `detected` was always `True`, `drift_deg` always NaN. The safety net was a no-op. |
| 2 | High | `frame_quality.py` | `_on_disk_mask` averaged an HWC RGB frame over (H,W), giving a `(3,)` mask | **Every RGB video frame scored sharpness 0.0.** Lucky imaging kept the *first N* frames rather than the sharpest. Colour SER/AVI captures got no frame rejection. |
| 3 | Medium | `grs_complete_system.py` | `rough_disk_mask` / `disk_mask_for_quality` didn't flatten RGB | Mask was all-False on bright RGB disks; the small-mask fallback crashed `ValueError: too many values to unpack` on RGB. |
| 4 | Medium | `research_grade.py`, `vlbi_metrology.py` | Injection ovals used a hand-rolled sphere + `b_pol` y-scale, not the engine's isotropic spheroid projection | At sub-lat 2° / PA 15° an oval was planted ~4–5 px (~1.2° lat) from where the engine measures that lon/lat. Recovery then *subtracted that placement error as pipeline bias*. |
| 5 | Medium | `planetary_stacker.py`, `zonal_stacker_benchmark.py` | Per-pixel/per-AP latitude was `degrees(asin(Y))` after an anisotropic y-scale — a sphere, not the spheroid | Even at D=P=0 latitude was off by **median 1.05°, max 2.84°** in the GRS band. Alignment points were mis-binned for the per-latitude shear warp. The benchmark simulator had the same bug, so they were a matched pair. |
| 6 | Medium | `planetary_derotator.py` | Measurement mode applied raw fitted `dx(|lat|)` bins with no regularisation | On an 8-frame / 3°-per-frame capture the derotated stack scored **0.68 correlation vs 0.76 for doing nothing** — derotation was making it worse. |

Findings 1–3 are the same family as the v7.0.1 real-photo fixes: code paths
the synthetic campaigns only ever hit on mono arrays. Findings 4–6 only show up
on *sheared/oriented* synthetic frames, which the default D=P=0 campaigns
don't generate.

### Things I checked and deliberately left alone

- A planet that fills the whole frame scores `disk_contrast ≈ 0` and is refused.
  That is intentional ("is there a sky background?"), not a bug.
- Some audit scripts (`seeing_floor_stress`, `vblurry_sweep`, etc.) don't
  propagate `sub_lat_deg`/`north_pa_deg` after `fit_limb_nav`. The synthetic
  renderer defaults to D=P=0 so the numbers are still reproducible; the
  production paths (desktop pipeline, observatory pipeline, VLBI) all do it
  correctly.
- `ser_io` falls back to `datetime.now(utc)` for a malformed SER timestamp
  block. That is metadata only; per-frame stamps are read separately and
  `real_photo_validate` already refuses a missing UTC instead of inventing one.

---

## 2. Evidence

### 2.1 The dead feature-verification gate

Before:

```
verify result: {'detected': True, 'drift_deg': nan, 'n_scales': 0,
 'reason': "scale check failed (name 'h_grs' is not defined); not treated as a rejection"}
```

`h_grs` is not a function; the map-dark estimator is `_map_dark_centroid`.
Python evaluated the bad expression while *building* the tuple of callables,
the loop body raised, and the outer `except` converted it into
`detected=True` ("infrastructure failure must not suppress a measurement").
`pyflakes` flagged it independently (`undefined name 'h_grs'`). After replacing
it with the missing lambda:

```
FIX1 verify: {'detected': True, 'drift_deg': 0.026, 'n_scales': 2,
 'reason': 'confirmed at 2 scales (drift 0.03deg)'}
```

### 2.2 RGB frame scoring was zero

```
a = rng.random((60,80,3))                 # HWC RGB
# old: a.mean(axis=(0,1)) -> shape (3,)   # mask shape (3,), sum 1
assess_frames sharpnesses: [0.0, 0.0, 0.0, 0.0]
select_best_frames(keep=0.5): kept [0,1,2,3,4] dropped [5,6,7,8,9]
```

Every colour frame tied at 0.0 and the stable sort kept input order. After
flattening to NTSC luma (the same weights `to_mono` uses) for both HWC and
CHW:

```
FIX2 RGB sharpness nonzero: True [1.33, 1.38, 1.33, 1.38]
```

A 4×-blurred frame now correctly loses to a sharp one.

### 2.3 RGB disk masks

```
rough_disk_mask(bright 60x60x3 disk): shape (60,60,3) sum: 0   # before
disk_mask_for_quality(nearly-black 40x40x3): ValueError: too many values to unpack
```

After the luma collapse: `rough_disk_mask` is 2-D with a real mask, and the
fallback centre disk works on RGB.

### 2.4 Injection ovals were planted in the wrong place

The engine projection round-trips exactly (`lonlat_to_planet_xyz` +
`planet_xyz_to_px` + `px_to_lonlat` agrees to 0.000°). The injection code did
not:

```
planted lon=100 lat=-20: inject_px=(501.3,344.4) engine_px=(501.2,348.9)  recovered=(100.37,-18.77)
planted lon=120 lat=-22: inject_px=(570.4,333.6) engine_px=(569.5,337.3)  recovered=(120.39,-21.02)
planted lon=80  lat=-18: inject_px=(430.2,354.9) engine_px=(430.6,360.2)  recovered=(80.49,-16.54)
```

That is a ~0.4° lon / ~1.2° lat placement error being measured as "pipeline
bias." After routing both injectors through the shared geometry:

```
planted (100,-20) darkest@(501,349) recover=(99.95,-20.00) dlon=-0.045 dlat=-0.004
planted (120,-22) darkest@(570,337) recover=(120.15,-21.96) dlon=0.149 dlat=0.045
```

### 2.5 Per-pixel latitude was a sphere approximation

Direct comparison against `px_to_lonlat` on a 720p disk (D=P=0):

```
lat diff on disk:  max=2.839 deg   median=1.048
in GRS band:      max=2.616 deg   median=1.182 deg   n=26617
```

After switching both helpers to `px_to_lonlat_vec`:

```
after fix: max=0.0000  median=0.000000 deg
```

The docstring's "correct for any oblate body" claim had simply not been true.
The benchmark's planted-shear simulator used the same approximation, so I fixed
it too — otherwise the derotator would be graded against a ground truth that
disagreed with the measurement geometry.

### 2.6 Measurement-mode derotation made things worse

8 frames, 3° CM drift per frame, mean per-belt correlation vs reference:

```
prior        derot=0.9802  naive=0.7638
hybrid       derot=0.6823  naive=0.7638
measurement  derot=0.6823  naive=0.7638
```

The per-AP tracks were individually fine (residuals <1.5 px), but the fitted
`dx(|lat|)` bins carried ~1–1.7 px of rounding/tracker noise that accumulated
across seven frames into visible tearing. The bulk-rotation *prior* is known
precisely; measurement only needs to add bounded zonal shear. Sweeping a fixed
measurement/prior blend:

```
cm_drift=0.5°/f:  naive=0.799   α=0.00→0.985   α=0.25→0.989
cm_drift=1.0°/f:  naive=0.802   α=0.00→0.955   α=0.25→0.966
cm_drift=3.0°/f:  naive=0.764   α=0.00→0.679   α=0.25→0.908
```

α = 0.25 fixes the long-capture regression without hurting small drifts, so
measurement mode now blends **75 % measured / 25 % model**. `hybrid` keeps its
stronger SNR-dependent blend. I did not push α higher: on *real* Jupiter the
per-latitude wind shear is the whole point of the derotator, and tuning to a
zero-shear synthetic would overfit the simulator.

---

## 3. New: Deterioration Lab

To make the above measurable from the UI I added
[`app/deterioration_lab.py`](../app/deterioration_lab.py) and an orange
**☄ Deterioration Lab** browser tab:

- Sweeps **resolution × seeing × noise**, renders a synthetic Jupiter+GRS per
  cell, measures with the published engine (lean path, as the batch campaigns
  use), and records `|Δlon|`, `|Δlat|`, sky error, method, softness and the
  per-estimator votes.
- Fits the **measured seeing floor** where median `|Δlon|` crosses 0.5° / 1.0°
  per resolution by linear interpolation.
- Has an **"analyse your image"** panel: upload a real FITS/PNG/JPG and get the
  same disk/softness/method grading entirely offline. No NASA endpoint is
  contacted — in this sandbox those hosts are SNI-blocked anyway.
- Two Flask endpoints (`POST/GET /api/deterioration`,
  `/api/deterioration/real`, `/api/deterioration/tips`) run the sweep in a
  background thread with live progress.

Verified end-to-end with the server running: a Quick sweep (2 resolutions ×
8 seeing tiers × 2 seeds) finishes in ~80 s and returns

```
540p: sub-1° breaks at ~1.2″ seeing
720p: sub-1° breaks at ~4.0″ seeing, sub-0.5° holds to ~2.4″
```

i.e. the floor moves more with plate scale than I expected. The per-method
breakdown on the same sweep had `moment` median 0.09°, `redness` 0.29°,
`template` 0.43°, `map_dark` 80.8° — a concrete reminder of why the dark
methods need the colour lock and outlier rejection.

### Real image

GitHub was reachable where NASA/Wikimedia were not, so I pulled a real 2048×1024
Jupiter texture map (`xibuka/SolarSystem`, committed binary — most other hits
were logos, 404s or Git-LFS pointers to blocked hosts) and projected it
orthographically to a disk for the measurement path:

```
real_photo_audit:  lat=-22.26  core=True  soft=0.19″  present=True
analyse_real_image:
  disk_present=True  measurable=True  fill=0.976  contrast=0.647  softness=0.24″
  method=consensus+template  quality=0.979
  template vote: lon=350.20, lat=-23.02
```

It is a texture-map projection, not a Hubble exposure, and I am not claiming
otherwise — but it is real Jupiter imagery going through the actual code, and
the disk gate / softness / estimator voting all behave correctly on it.

---

## 4. Verification

- `python -m py_compile app/*.py tools/*.py tests/*.py` — clean.
- `pyflakes` — the single undefined name in the tree was Bug #1; gone.
- Non-slow suite: **239 passed, 5 skipped** across geometry, derotator/stacker/
  zonal, video, frame-quality, AP stacker, real-photo, limb-softness, redness,
  ellipse and the new deterioration tests. The two Tk desktop tests can't run
  headless and are the only ones skipped.
- Server boots cleanly; `/api/health`, `/api/deterioration`, `/api/deterioration/real`,
  index and assets all return 200; no errors in the log.

No measurement thresholds, publish gates or accuracy constants were changed
apart from the one documented derotator prior blend. The fixes are scoped to
making the existing safety/quality gates actually run on the RGB and oriented
data the real path feeds them.

---

## 5. Files changed

```
app/precision_engine.py        h_grs -> _map_dark_centroid lambda (Bug 1)
app/frame_quality.py           luma-collapse HWC/CHW in _on_disk_mask + assess_frames (Bug 2)
app/grs_complete_system.py     luma-collapse rough_disk_mask + disk_mask_for_quality (Bug 3)
app/research_grade.py          inject_dark_oval via lonlat_to_planet_xyz (Bug 4)
app/vlbi_metrology.py          inject_dark_oval_image via shared geometry (Bug 4)
app/planetary_stacker.py       _per_pixel_lat / _ap_latitudes via px_to_lonlat_vec (Bug 5)
app/planetary_derotator.py     25% prior blend in measurement mode (Bug 6)
tools/zonal_stacker_benchmark  planted shear uses exact spheroid latitude (Bug 5)
app/deterioration_lab.py       NEW — sweep engine + real-image grader
app/server.py                  NEW — /api/deterioration* endpoints
app/templates/index.html       NEW — Deterioration Lab tab + real-image panel
app/static/app.js, style.css   NEW — Lab UI, canvas charts, drop-to-analyse
tests/test_deterioration_regressions.py  NEW — 10 pinned regressions
tests/test_deterioration_lab.py           NEW — 3 sweep-engine tests
docs/DETERIORATION_AUDIT_2026-08-22.md    this file
```

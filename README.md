# Great Red Spot Detector

*A Jupiter metrology tool I built for my astrophysics coursework — turns a stacked FITS/SER/PNG into a publishable System III longitude & latitude for the GRS, with SPICE geometry, dual-limb discipline, and colour-first feature lock.*

---

**Version:** 7.0.1 · **Platform:** macOS / Linux / Python 3.10+ · **Formats:** FITS, SER, PNG, JPEG, AVI  
**Repo:** https://github.com/haydenCoder/great-red-spot-detector

---

## Why I wrote this

Most Jupiter software just makes pretty pictures. I needed actual *numbers* — reproducible System III lon/lat I could defend in a report. This tool takes your stacked image and outputs a measurement package you can actually cite: longitude, latitude, CM source, quality flags, and a one-page "report this" card.

It mirrors how careful WinJUPOS operators work (time → CM → limb → definition → publish), but automates the tedious parts so you don't spend an hour hand-picking outlines on 30 frames.

---

## How it works (the pipeline)

```
Your image (FITS/PNG/SER)
    │
    ├─► pull mid-exposure UTC from header or filename
    ├─► SPICE CM III + distance (local kernels, no hunting NAIF files)
    ├─► prep: auto N-S flip, moon mask, red+orange mono for RGB
    ├─► multi-isophote limb navigation
    ├─► orthographic → cylindrical map (System III)
    ├─► GS-ORANGE colour centre (orange oval on RGB, not just "darkest pixel")
    ├─► ~80 classical estimators → scatter/confidence only
    ├─► dual: automatic + by-eye cyan limb
    └─► publish + SUPERDUPER best-answer card
```

| Piece | What it does |
|-------|--------------|
| SPICE ephemeris | CM & distance for absolute System III |
| Limb fit | Finds the disk; you can fine-tune with cyan outline (WinJUPOS style) |
| GS-ORANGE | Primary centre — locks onto the orange oval, not random dark pixels |
| GS-MAP / bary / templates | Classical dark-core / map definitions |
| SPIRE-Net (frozen) | Optional soft prior — NOT the published centre |
| Dual measure | Auto vs hand agreement (MATCH = we trust it internally) |

---

## Accuracy — honest version

| Compared to | This tool |
|-------------|-----------|
| Hobby sky apps | Way more rigorous — real UTC/CM, limb, map, publish gates |
| "Find the spot" one-click | Much better when your time & orientation are right |
| Careful human WinJUPOS | Built on the same discipline. On good data it can match or approach a careful desk — but **paste your WJ pick** to prove Δsky. It does NOT claim to beat every expert on every messy night |

In short: WinJUPOS-class methodology with automation, not a magic wand.

### v6.6.0 — accuracy verified at scale (2026-07-29)

Two pinned test campaigns, **340 cases total**, score the measurement against
independent truth. Full write-up: [`docs/AUDIT_MASTER_6.6.0.md`](docs/AUDIT_MASTER_6.6.0.md).

| Campaign | Truth source | Result |
|---|---|---|
| **Resolution × seeing** (100 cases) | planted geometric centre | 100 % within 1°, sky median **0.117″** |
| **Real ephemeris** (240 cases) | published GRS longitude (Hubble/JUPOS) + literature latitude | **100 % within 1°**, every clear/mild frame **<0.5°**, lon bias −0.004° |

The headline guarantee: **sub-1° on every frame across clear→very-blurry and
1080p→4K, and sub-0.5° on all good (clear/mild) data.** A consensus tuning
(folding the blur-robust colour/redness lock into the longitude blend) drove the
worst clear-data case from 0.69° to 0.43° and improved every suite with no
regressions.

> Note on real photos: absolute longitude needs a mid-exposure UTC, which web
> imagery lacks, so the real-ephemeris campaign uses synthetic pixels planted at
> the *real* published GRS longitude for each epoch. See the master audit for the
> honest framing.

### v6.7.0 — stacking & derotation are no longer Jupiter-only (2026-07-31)

The stacker and derotator generalised to **Jupiter, Saturn, Neptune, Uranus,
Mars** via a `Planet` model (`app/planet_models.py`), and the stacker got a
real **per-latitude warp** (it used to measure per-latitude shear then throw it
away by collapsing to one global translation per frame). CLI:

```bash
python cli.py planet-stack    --planet Saturn --frames-dir ./saturn_frames
python cli.py planet-derotate --planet Jupiter --frames-dir ./jup_frames --mode measurement
```

On per-latitude-sheared synthetic frames the per-latitude warp beats the legacy
global translation (+0.037 mean per-belt correlation) and the new measurement
derotator beats naive stacking (+0.106). Full write-up:
[`docs/PLANETARY_STACKING_6.7.0.md`](docs/PLANETARY_STACKING_6.7.0.md).

### v6.8.0 — Observatory Pro: from video to published answer (2026-08-07)

The whole pre-processing chain AutoStakkert/RegiStax/WinJUPOS users hop between
now lives here:

- **SER/AVI video import** (`ser_io.py`) and an **APS stacker** (`ap_stacker.py`):
  per-alignment-point quality maps + lucky imaging + **true drizzle ×2/×3**
  super-resolution, with a rotation-aware search window so the planet can turn
  during the capture.
- **Sharpen Lab** (`sharpen_lab.py`): à-trous wavelets (RegiStax-style), RL
  deconvolution, unsharp — noise-gated so sharpening doesn't amplify grain.
- **Transit planner** (`transits.py`): GRS crossings, visibility windows,
  Galilean moon events — checked against published 2026 tables.
- **JUPOS export** (`jupos_io.py`) and **blink GIFs** (`animation.py`).
- Fifth measurement definition: **rim-ellipse fit** (`grs_ellipse.py`) — the
  tightest latitude of any estimator (12-case audit: |dlat| median 0.107°).
- One command for the full chain:

```bash
grs-observatory video-to-answer capture.ser --drizzle 2 --sharpen wavelet
```

Measured end-to-end proof on a tilted-geometry synthetic capture (real
2026-08-02 sub-lat + pole PA): published relative longitude error **0.173°**,
latitude **0.347°**. Three more measured production fixes (moon-mask colour
gate, pole-PA-aware limb fit, derotation scale/sign) — all in
[`CHANGELOG.md`](CHANGELOG.md) and [`docs/OBSERVATORY_PRO_6.8.0.md`](docs/OBSERVATORY_PRO_6.8.0.md).
Desktop/web got **Video Import**, **Sharpen Lab** and **Transits** tabs.

### v7.0.1 — real Hubble frames (2026-08-19)

The published path was run on Hubble OPAL / Ganymede-shadow / Io frames.
Two logic errors that synthetic campaigns cannot see are fixed: the
limb-softness gate refused space-telescope disks as "seeing too poor"
(2× units + PA-blind histogram), and isolated redness pruned a tight
dark GRS cluster in favour of a reddish belt. Juno close-ups are still
correctly refused. Write-up: [`docs/REAL_PHOTO_AUDIT_7.0.1.md`](docs/REAL_PHOTO_AUDIT_7.0.1.md).

### v7.0.0 — Velocity Pro: the C core (2026-08-09)

Hand-written C99 kernels (`app/cspeed.c`, ctypes, zero dependencies, builds
on demand via cc/gcc/clang) around the two proven hot spots: profiling
showed **91% of stack time** was cubic-spline sampling inside the
Lucas–Kanade refinement — five separate scipy evaluations per iteration.
One fused compiled pass now produces value + gradients + the Gauss–Newton
normal equations from a shared 6×6 tap neighbourhood, and warps batch-sample
spline coefficients in compiled code.

Measured on the validation box (`tools/cspeed_benchmark.py` — claims are
measured, not argued):

| workload | numpy/scipy | C core | speedup |
|---|---:|---:|---:|
| `stack_ap` end-to-end (12 f, full AP grid) | 197.1 ms | 56.0 ms | **3.52×** |
| `_lk_refine` (32×32 crop, 4 iters) | 1.525 ms | 0.608 ms | 2.51× |
| `warp_field2d` 300×400 order 3 | 17.0 ms | 9.9 ms | 1.73× |
| `warp_shift2d` 400×300 order 3 | 10.7 ms | 7.4 ms | 1.45× |

Speed that changes the answer is a bug, not an optimisation: the C path is
pinned to scipy at **1.3e-15** kernel parity, `stack_ap` with vs without C
differs by **3.5e-16** with byte-identical frame-usage decisions
(`tests/test_cspeed.py`). Strict IEEE doubles — no `-ffast-math`, ever. No
compiler → the identical scipy fallback keeps running (status via
`cspeed.status_note()`); `CSPEED=0` forces the fallback.

Full dossier: [`docs/PERFORMANCE_7.0.0.md`](docs/PERFORMANCE_7.0.0.md).

### v6.9.0 — Analysis Pro: from stacks to science (2026-08-08)

The layer AutoStakkert doesn't have and WinJUPOS does by hand:

- **Filter-wheel RGB combine** (`rgb_combine.py`): derotate mono R/G/B stacks
  to a common epoch with the *exact* spheroid ephemeris (north-PA and
  sub-Earth-lat safe) + measured band-residual polish. AutoStakkert cannot do
  this at all. On a tilted-geometry synthetic (2.42° rotation/hop): colour
  fringe **0.193 → 0.032 (6.0×)**. One-command workflow `filter-wheel`
  (3× SER → stacks → RGB) runs the whole thing: fringe 0.107 → 0.036 (3.0×),
  with re-centre and times recovered from SER stamps.
- **Cloud-tracking wind science** (`wind_analysis.py`): shape-discriminated
  offset fits (uniform advection vs a System-III angular error), jet
  detection with honest gaps, JUPOS-friendly CSV + PNG profile panels.
- **GRS System-II drift** (`grs_drift.py`): sigma-clipped rate fits
  (deg/30 d publishing convention), curvature by F-test only when demanded,
  implied zonal velocity in m/s, prediction cones. Planted −0.42°/30 d
  recovered to ±0.10 with 0/360 wrap and outliers handled.
- **Stack forensics** (`stack_report.py`): interior fill, subpixel dither
  audit, usage concentration, wander/jump stats — with actionable warnings.
- **Session planner** (`session_planner.py`): exact smear/span budgets
  (raw vs derotated — a ~5–10× difference), filter-gap limits.
- **Limb darkening** (`limb_darkening.py`): band-normalised μ^k from your
  stack — recovers the renderer's planted k=0.6 as 0.653±0.020.
- **1.3–1.8× faster APS** with bitwise-identical stacks (prefilter-once LK;
  `ap_stacker.py`), verified 0.0 max-delta on golden rigs.
- New CLI commands `rgb-combine`, `filter-wheel`, `wind-analysis`, `drift`,
  `session-plan`; new desktop tabs **RGB Combine** and **Analysis**; new web
  endpoints `/api/analysis_session` and `/api/analysis_drift` + Analysis tab.

Full numbers: [`CHANGELOG.md`](CHANGELOG.md) and
[`docs/OBSERVATORY_PRO_6.9.0.md`](docs/OBSERVATORY_PRO_6.9.0.md).

---

## Get it running

```bash
git clone https://github.com/haydenCoder/great-red-spot-detector.git
cd great-red-spot-detector
./RUN_ME.command
```

Or the manual way:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cd app && python desktop_app.py
```

### Basic workflow

1. Open your stack (FITS / SER / PNG)
2. Enter mid-exposure UTC if the header doesn't have it
3. Hit **Process full** — green auto limb + cyan by-eye limb
4. Read `SUPERDUPER_BEST_ANSWER.txt` and `publish.txt`
5. Optionally paste your WinJUPOS lon/lat to check Δsky agreement

---

## Verified result on real data

Full write-up: [`docs/TECHNICAL_ESSAY_VERIFIED_CASE_2026-01-09.md`](docs/TECHNICAL_ESSAY_VERIFIED_CASE_2026-01-09.md)

**Stack:** AutoStakkert RGB · `2026-01-09 15:40:00 UTC`

| Field | Result |
|-------|--------|
| CM III (SPICE) | 310.428° |
| GRS λ_III (GS-ORANGE) | ~289.90° |
| φ_c / φ_g | ~−22.73° / −25.60° |
| Independent reprocess | Δλ ~0.08°, Δφ ~0.10° |
| Dual path | MATCH |

---

## What's included vs. what's left out

| In | Out (by design) |
|----|------------------|
| Process + dual limb | SPIRE-Net training (weights frozen, reproducible forever) |
| GS-ORANGE + publish gates | WinJUPOS CM table upload/download |
| Frozen CNN soft prior | Factory night / hard-synth / multi-epoch buttons (in CLI only) |
| Bundled SPICE kernels | Online SPICE auto-download |
| Champion Ultimate + SUPERDUPER archival cards | — |

### v6.5.1 accuracy fixes (2026-07-28)

Full geometry + smoke audit; see `docs/AUDIT_GEOMETRY_AND_SMOKE_6.5.1.md`.
All ten findings are fixed and pinned by tests:

- **Projection rewritten on the true oblate spheroid.** Latitude is now genuinely
  planetocentric (was the *parametric* latitude — up to 1.7° bias), and the
  north-PA rotation now happens before the isotropic plate scale, so rotated
  disks are no longer sheared (was up to 1.06° longitude at real Jupiter PA).
- Forward and inverse projection share one helper, so they cannot drift apart;
  the synthetic renderer uses the same geometry (agreement ~1e-12).
- `km_per_deg_lat()` returns the planetocentric meridian arc length instead of a
  constant (was 5.7% low at the GRS); `km_per_deg_lon()` uses the spheroid
  parallel radius. Both feed every quoted arcsecond error bar.
- GRS latitude prior converted from the literature planetographic −22.4° to
  −19.82° planetocentric instead of being hardcoded −22.0°.
- **Seeded synthetics are reproducible again**: the epoch sampling window no
  longer depends on `datetime.now()`, so certify runs are auditable.
- `_atomic_savez` is actually atomic (the temp path now keeps `.npz` last), and
  no longer orphans a 16 MB file or silently degrades to a corrupting in-place
  write.
- `_gauss` scipy-free fallback is a real separable Gaussian — the old box/FFT
  path shifted the image by `k//2` px, biasing every centroid.
- Version strings read the `VERSION` file everywhere; no hardcoded literals.

### v6.5.0 audit fixes (2026-07-28)

All P0/P1/P2 bugs from the full line-by-line audit are fixed:
- Champion candidate now correctly preferred over GS-MAP in publish hierarchy
- f-string None-format crashes in winjupos_plus, superduper, winjupos_twin, desktop_pipeline, gold_standard, and server patched
- Server /api/synthetic now produces SUPERDUPER archival products
- Stale version strings and User-Agent updated to 6.5.0 (all two instances in server.py, plus nasa_compare.py)
- _gauss() fallback now actually performs FFT convolution
- ALL `datetime.now()` replaced with `datetime.now(timezone.utc)` for reproducible timestamps — even the non-science ones (filenames, logs, seeds)
- Desktop UI polished: refined colour palette, improved metric cards
- Documentation humanised: student voice across README, book, essay, and all key module docstrings
- See docs/FULL_LINE_AUDIT_6.5.0.md and docs/DEEP_AUDIT_RESULTS.md for the complete audit

## License

See [`LICENSE`](LICENSE).

---

*Great Red Spot Detector* — open the stack, run Process, publish a real System III centre.  
Built because I care about measurement, not just pretty Jupiter pictures.

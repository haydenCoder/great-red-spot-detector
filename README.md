# Great Red Spot Detector

*A Jupiter metrology tool I built for my astrophysics coursework — turns a stacked FITS/SER/PNG into a publishable System III longitude & latitude for the GRS, with SPICE geometry, dual-limb discipline, and colour-first feature lock.*

---

**Version:** 6.6.0 · **Platform:** macOS / Python 3.10+ · **Formats:** FITS, SER, PNG, JPEG  
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

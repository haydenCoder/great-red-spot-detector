# Great Red Spot Detector

A Python tool I wrote for my astrophysics coursework. It takes a stacked
Jupiter image (FITS / SER / AVI / PNG / JPEG) and measures the Great Red Spot
as a System III longitude and latitude, with a documented uncertainty budget
instead of just "there it is."

**Version:** 7.0.1 · **Python:** 3.10+ · **Platforms:** macOS / Linux ·
**License:** see [`LICENSE`](LICENSE)

---

## Why I built this

Most Jupiter software is for making pretty pictures. I needed a *number* I could
actually defend in a report — a reproducible GRS lon/lat with a CM source,
quality flags and an error bar. The workflow is stolen from careful WinJUPOS
operators (time → CM → limb → definition → publish); I just automated the
hour of hand-picking outlines.

It is not magic. On good data it can match a careful WinJUPOS desk; on a messy
night it cannot do better than a human looking carefully. Paste your WinJUPOS
pick into the dual-limb dialog and it tells you the Δsky — that agreement is
what you cite, not a marketing claim.

---

## What it does

```
Stacked image (FITS / SER / AVI / PNG / JPEG)
   │
   ├─ mid-exposure UTC (header / filename / typed in)
   ├─ SPICE CM III + distance (local kernels, no download)
   ├─ prep: N-S flip, moon mask, red+orange mono for RGB
   ├─ multi-isophote limb fit  (auto green + by-eye cyan outline)
   ├─ orthographic → cylindrical map on the true oblate spheroid
   ├─ GRS estimators: template · map-dark · moment · redness · rim-ellipse
   ├─ per-method scatter → systematic floor; Monte Carlo → random term
   └─ publish card + SUPERDUPER best answer + FULL_REPORT.txt
```

The colour (redness) lock is the workhorse: colour survives the atmospheric
blur that destroys dark-oval shape, so it stays locked when the template starts
chasing SEB barges. The CNN (`SPIRE-Net`) is a *frozen* soft prior only — it is
never the published centre.

### Observatory Pro (the video path)

The whole AutoStakkert!/RegiStax/WinJUPOS chain in one place:

- **SER/AVI import** + APS stacker: per-alignment-point quality maps, lucky
  imaging, true drizzle ×2/×3, rotation-aware search window.
- **Per-latitude derotation** for any of Jupiter, Saturn, Neptune, Uranus,
  Mars (planet rotation + zonal wind), with `prior` / `hybrid` / `measurement`
  modes.
- **Sharpen Lab** — à-trous wavelets, RL deconvolution, unsharp, all
  noise-gated so grain doesn't get sharpened into fake detail.
- **Transit planner** — GRS meridian crossings, visibility, Galilean moon
  events (checked against published 2026 tables).
- **JUPOS export**, blink GIFs, filter-wheel RGB combine, cloud-tracking wind
  analysis, GRS drift fits, stack forensics, session planner.

```bash
grs-observatory video-to-answer capture.ser --drizzle 2 --sharpen wavelet
```

### Deterioration Lab (new in this build)

A browser tab that sweeps **resolution × seeing × noise**, measures every cell
with the published engine, and plots how accuracy falls off — plus the measured
seeing at which the sub-1° guarantee breaks for each resolution, and a
"analyse *your* uploaded image" panel that reports disk/softness/method votes
without any network access.

```bash
python app/server.py        # open http://127.0.0.1:8765  →  ☄ Deterioration Lab
```

---

## Accuracy, honestly

| Compared to | Honest verdict |
|---|---|
| Hobby phone apps | Far more rigorous — real UTC/CM, limb, map, publish gates |
| One-click "find the spot" | Better when your time and orientation are right |
| Careful human WinJUPOS | Same methodology; on good nights it can match a desk — **paste your WJ pick to prove Δsky** |

### Pinned campaigns

| Campaign | Cases | Truth | Result |
|---|---:|---|---|
| Resolution × seeing | 100 | planted geometric centre | 100% within 1°, sky median 0.117″ |
| Real ephemeris | 240 | published GRS lon + literature lat | 100% within 1°; clear/mild all <0.5° |
| Velocity Pro (C core) | 12-frame APS | scipy at 1.3e-15 parity | 3.5× faster, byte-identical decisions |
| Real Hubble OPAL frames | 3 | visual lat band | softness gate fixed; dark cluster kept; Juno crops refused |

The real-ephemeris campaign uses *synthetic pixels planted at the real published
GRS longitude*. The truth is real; the pixels are generated, because an
untimed JPEG has no mid-exposure UTC and therefore no measurable absolute
longitude. I'm calling that out rather than pretending it's a real photo.

Sweep result from the new Deterioration Lab (8 seeing tiers, 2 seeds):

| Disk radius | breaks sub-1° at | holds sub-0.5° to |
|---|---:|---:|
| 540p | ~1.2″ seeing | ~0.8″ |
| 720p | ~4.0″ seeing | ~2.4″ |

i.e. plate scale matters more than I'd assumed — 720p keeps a usable lock
through seeing that completely breaks 540p.

---

## Running it

```bash
git clone https://github.com/haydenCoder/great-red-spot-detector.git
cd great-red-spot-detector
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

python app/server.py          # web UI (recommended)
# or
python app/cli.py --help      # command line
```

Drop a stack in, enter the mid-exposure UTC, hit **Process**, then read
`SUPERDUPER_BEST_ANSWER.txt` and `publish.txt`. Paste a WinJUPOS lon/lat to get
the Δsky check.

---

## What's deliberately not in scope

- **No NN training.** The SPIRE-Net weights ship frozen so results are
  reproducible forever; training is a research project, not coursework.
- **No online SPICE download.** Kernels are bundled under
  `app/ephemeris_data/spice/`.
- It does not beat a careful human on a bad night. `UNBEATABLE_AUTO` means the
  in-app gates passed — nothing more.

---

## Bugs I fixed while writing this (with tests)

- The scale-drift feature-verification gate had a typo'd call (`h_grs`) that
  made it a silent no-op on every measurement.
- Lucky-imaging frame scoring returned 0.0 for every RGB video (a `(3,)` mask),
  so colour captures weren't actually frame-selected.
- The disk-quality masks crashed / returned empty on RGB inputs.
- Blind-injection ovals were planted through the old anisotropic projection,
  putting the "bias calibration" ~1° off where the engine actually measures.
- Per-pixel latitude used a sphere shortcut, wrong by up to 2.8° vs the
  spheroid in the GRS band; the derotator was mis-binning shear because of it.
- Measurement-mode derotation was unregularised and could stack *worse* than
  doing nothing on long captures; it now blends 75% measured / 25% model.

Each one is pinned by a regression test — the before/after behaviour lives in
`tests/test_deterioration_regressions.py`.

---

## Layout

```
app/                 measurement engine, stacker, derotator, UI, SPICE kernels
  precision_engine.py   core projection + consensus publish
  deterioration_lab.py  resolution × seeing × noise sweep
  ap_stacker.py         APS lucky-imaging stack + drizzle
  ser_io.py / video     SER/AVI container I/O
  planetary_*.py        planet-generalised stacker/derotator
  cspeed.c / cspeed.py  optional C99 hot-path kernels
  templates/, static/   web UI
docs/                the observatory book, one walkthrough essay, security notes
tests/               pytest suite (run with `pytest -m "not slow"`)
```

---

*Built because I care about measurement, not just pretty Jupiter pictures.*

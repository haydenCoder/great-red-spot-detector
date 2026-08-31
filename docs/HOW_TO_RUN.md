# How to Run the Great Red Spot Detector

Everything you need to install, run, and test this project on a laptop.
The app is pure Python (plus an optional C extension that is not required).
Target machine: macOS or Linux, 16 GB RAM recommended, Python 3.10+.

---

## 1. One-time setup

```bash
cd great-red-spot-detector

# Create an isolated environment (recommended)
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Optional but recommended for development
pip install pytest pytest-xdist ruff
```

`requirements.txt` installs: numpy, flask, Pillow, scipy, astropy, certifi,
spiceypy, pyephem. No database, no node, no build step.

That's the whole setup. SPICE kernels are downloaded automatically on first
use (`app/spice_auto.py`); SPIRE-Net CNN weights ship with the repo under
`app/models/`. Nothing needs to be compiled.

---

## 2. Quick start (try it in 60 seconds)

```bash
source .venv/bin/activate

# 1. Generate a synthetic Jupiter frame (fake planet, known truth)
python app/cli.py synth --mode metrology --res 1080p

# 2. Resolve the professional ephemeris (CM, distance, sub-lat, PA)
python app/cli.py eph "2026-07-14 12:00:00"

# 3. Run the product certification suite (metrology self-test)
#    Each frame takes ~75 s on this machine; start with 2, use 30 for a
#    real certification run (published gate: median ≤ 0.75″).
python app/cli.py certify --n 2

# 4. (macOS) Double-click RUN_ME.command, or launch the web UI:
python app/server.py
#    then open http://127.0.0.1:8765 in a browser
```

The server is the easiest way to see everything working: upload or generate a
frame, enter the observation time, press **Factory Night** (or **Process**),
and read the published longitude/latitude plus the uncertainty budget.

---

## 3. Running the full test suite

```bash
source .venv/bin/activate

# Fast suite (the default): 487 passed / 15 skipped, ~6 min on 2 cores
pytest -m "not slow" -n 2

# Everything, including slow campaign tests
pytest -m "not slow" -n 2

# A single test module
pytest tests/test_geometry_100.py -v

# With verbose skip reasons (to see which 15 are skipped and why)
pytest -m "not slow" -n 2 -rs
```

Skips are intentional: Tk desktop tests need a display, native `.so` tests need
the C extension built, and a few real-photo tests need image fixtures that are
not shipped.

---

## 4. The main entry points

| What you want | Command |
|---|---|
| Version + environment | `python app/cli.py version` |
| Licence status | `python app/cli.py license status` |
| Professional ephemeris | `python app/cli.py eph "YYYY-MM-DD HH:MM:SS"` |
| Generate synthetic Jupiter | `python app/cli.py synth --mode metrology --res 1080p` |
| Process a real image (full stack) | `python app/cli.py process /path/image.fits --time "YYYY-MM-DD HH:MM:SS"` |
| Product certification | `python app/cli.py certify --n 30` |
| Desktop app (GUI) | `python app/desktop_app.py` |
| Web UI (browser) | `python app/server.py` → http://127.0.0.1:8765 |
| Video → publishable answer | `python app/cli.py video-to-answer /path/video.ser --time "..."` |
| APS stack a SER/AVI capture | `python app/cli.py video-stack /path/video.ser --best 0.25 --drizzle 2` |
| Stack a folder of frames | `python app/cli.py ap-stack --frames-dir ./frames` |
| WinJUPOS manual check | paste your CM + lon/lat in the UI's "vs WinJUPOS" field |

For the desktop app and the server, the recommended workflow is:

1. **Open file** — FITS / PNG / JPEG / SER / AVI.
2. **Set time** — mid-exposure UTC. Never wall-clock. (One minute of error
   ≈ 0.6° longitude.)
3. **Process** — full advanced stack: pro ephemeris → limb nav → VLBI
   correlator → multi-method consensus → phase-ref injection bias calibration
   → hierarchical Monte Carlo → definitions → publish.
4. **Read the result** — grade, Lon III, Lat, total sky σ, and (on synthetics)
   truth recovery.

---

## 5. Validation campaigns (audit tools)

The tools under `tools/` measure the accuracy of the published answer against
known planted truth. All write resumable JSONL caches under `runs/` (gitignored):

```bash
# 24-seed accuracy campaign (clear 1080p): ~9 s per frame
python tools/accuracy_campaign.py --n 24 --res 1080p

# 1000-case resolution × seeing matrix (resumable)
python tools/per_method_audit.py

# Very-blurry stress: find the seeing at which the lock degrades
python tools/seeing_floor_stress.py

# Speed audit of every hot stage
python tools/speed_audit.py
```

Reference results: median |Δlon| ≈ 0.17°, worst 0.60° on clear
1080p; sky error median ≈ 0.084″. The 1000-case matrix holds every
clear/mild/blurry frame within 1.0°.

---

## 6. Repo layout (short version)

```
app/         measurement engine, stackers, derotators, UI, server, SPICE kernels
  precision_engine.py   core projection + consensus measure
  all_methods.py        ~80-estimator catalog
  champion_measure.py   pro-desk measurement path
  publish_primary.py    which number is the official answer
  ephemeris_pro.py      CM / distance / orientation resolver
  spice_auto.py         kernel discovery + auto-download
  ser_io.py, ap_stacker.py, planetary_*.py, sharpen_lab.py   imaging side
  nn_grs.py             SPIRE-Net soft prior
  desktop_app.py        Tk desktop UI
  server.py             Flask UI
tests/                  487 passing pytest modules
tools/                  accuracy campaigns + benchmarks
docs/                   the book (user guide), the essay, how-to-run, security
```

Read `PROJECT_MAP.md` for the annotated tree, and `docs/ESSAY.md` for a
module-by-module walkthrough that explains *why* each file exists.

---

## 7. Troubleshooting

| Symptom | Fix |
|---|---|
| `ModuleNotFoundError: numpy` | `source .venv/bin/activate`, then `pip install -r requirements.txt` |
| SPICE geometry fails offline | The app falls back to Horizons → analytical, and labels the source + σ honestly. |
| No display error from desktop app | Run `python app/server.py` instead, or use CLI. |
| `pytest-timeout` doesn't exist | Don't pass `--timeout`; the suite is written to finish in ~6 min. |
| Slow first run | SPICE kernels download once and are cached under `app/ephemeris_data/spice/`. |
| Server won't start on a port | Set `GRS_PORT=8766 python app/server.py`. |
| Native C speedup not active | `tools/cspeed_benchmark.py` will say "not built"; everything still works via NumPy. |

---

## 8. Science notes worth knowing

- **Mid-exposure time, not start time.** 0.6° System III per minute.
- **Absolute longitude needs a trusted CM** (SPICE / Horizons / WinJUPOS
  table / override). Analytical CM is a fallback with a large σ.
- **The pipeline works planetocentric;** reports convert to planetographic
  (−22.4° planetographic ≈ −19.82° planetocentric) for WinJUPOS comparison.
- **The GRS is a colour feature first** — the redness estimator survives
  seeing that destroys the dark-oval shape.
- **Method SOTA/soup is scatter, never the published answer.** The published
  lon/lat comes from the gold-standard/champion definitions via
  `publish_primary.py`.

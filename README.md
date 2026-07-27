# Great Red Spot Detector

**Also known as:** GRS Observatory (legacy internal package name)

Process-focused software for **ground-based optical measurement** of Jupiter’s **Great Red Spot** — System III longitude and latitude from a single stacked frame (FITS / PNG / SER).

**Keywords for search:** Jupiter · Great Red Spot · GRS · detector · System III · WinJUPOS · SPICE · planetary imaging · AutoStakkert

| | |
|--|--|
| **GitHub** | https://github.com/haydenCoder/great-red-spot-detector |
| **Release** | [v6.5.0](https://github.com/haydenCoder/great-red-spot-detector/releases/tag/v6.5.0) |
| **Version** | 6.5.0 |

**Not** a NASA GRS catalog, radio interferometry, or Gaia-style catalog.  
SPIRE-Net weights ship **frozen** (CNN prior ON; training disabled).

---

## Download the code

### One command (recommended)

```bash
git clone https://github.com/haydenCoder/great-red-spot-detector.git
cd great-red-spot-detector
./RUN_ME.command
```

### ZIP (no git)

```bash
curl -L -o great-red-spot-detector.zip \
  https://github.com/haydenCoder/great-red-spot-detector/archive/refs/heads/main.zip
unzip great-red-spot-detector.zip
cd great-red-spot-detector-main
./RUN_ME.command
```

### Release ZIP (v6.5.0)

```bash
curl -L -o great-red-spot-detector-v6.5.0.zip \
  https://github.com/haydenCoder/great-red-spot-detector/archive/refs/tags/v6.5.0.zip
unzip great-red-spot-detector-v6.5.0.zip
cd great-red-spot-detector-6.5.0
./RUN_ME.command
```

---

## Run (macOS)

```bash
./RUN_ME.command
```

Or:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cd app
python desktop_app.py
```

1. **Open** your stack (FITS / PNG / SER)  
2. UTC: leave blank if the filename embeds time (`2026-01-09-1540_…`), or enter mid-exposure UTC  
3. **Process full** (auto limb + by-eye cyan limb)  
4. Read `app/outputs/job_*/SUPERDUPER_BEST_ANSWER.txt` and `publish.txt`

**Requirements:** Python 3.10+ · SPICE kernels under `app/ephemeris_data/spice/` (bundled).

---

## Verified case study

See [`docs/TECHNICAL_ESSAY_VERIFIED_CASE_2026-01-09.md`](docs/TECHNICAL_ESSAY_VERIFIED_CASE_2026-01-09.md).

| Field | Value |
|-------|--------|
| UTC | 2026-01-09 15:40:00 |
| CM III (SPICE) | 310.428° |
| GRS λ_III (GS-ORANGE) | ≈ 289.90° |
| φ_c / φ_g | ≈ −22.73° / −25.60° |

---

## Finding this project in Safari / Google

GitHub does **not** guarantee first-page results immediately. To help discovery:

1. Search: **`great red spot detector github`** or **`haydenCoder great-red-spot-detector`**  
2. Or open the link above and **star** the repo (helps ranking slightly)  
3. Share the URL on school pages / social — the best way people find student projects  

Safari search uses Google/Bing; they index public GitHub pages over time (hours to days).

---

## What this release includes

- **Process** UI (open image → dual limb → publish)  
- **GS-ORANGE** colour centre for RGB stacks  
- **Frozen** SPIRE-Net weights (soft prior; no train UI)  
- Bundled SPICE kernels  
- Technical essay + geometry tests  

## License

See [`LICENSE`](LICENSE).

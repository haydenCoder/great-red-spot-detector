# GRS Observatory

Process-focused software for **ground-based optical measurement** of Jupiter’s **Great Red Spot** (System III longitude and latitude) from a single stacked frame (FITS / PNG / SER).

**Not** a NASA GRS catalog, radio interferometry, or Gaia-style astrometry.  
SPIRE-Net weights ship **frozen** (CNN prior ON; training disabled).

| | |
|--|--|
| **Repo** | https://github.com/haydenCoder/GRS-Observatory |
| **Release** | [v6.5.0](https://github.com/haydenCoder/GRS-Observatory/releases/tag/v6.5.0) |
| **Version** | 6.5.0 |

---

## Download the code

### Option A — clone with git (recommended)

```bash
git clone https://github.com/haydenCoder/GRS-Observatory.git
cd GRS-Observatory
```

### Option B — download ZIP (no git)

```bash
curl -L -o GRS-Observatory.zip https://github.com/haydenCoder/GRS-Observatory/archive/refs/heads/main.zip
unzip GRS-Observatory.zip
cd GRS-Observatory-main
```

### Option C — latest release ZIP

```bash
curl -L -o GRS-Observatory-v6.5.0.zip \
  https://github.com/haydenCoder/GRS-Observatory/archive/refs/tags/v6.5.0.zip
unzip GRS-Observatory-v6.5.0.zip
cd GRS-Observatory-6.5.0
```

---

## Run (macOS)

```bash
./RUN_ME.command
```

Or manually:

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

**Requirements:** Python 3.10+ · local SPICE kernels under `app/ephemeris_data/spice/` (bundled) · no online kernel download required.

---

## Verified case study

See [`docs/TECHNICAL_ESSAY_VERIFIED_CASE_2026-01-09.md`](docs/TECHNICAL_ESSAY_VERIFIED_CASE_2026-01-09.md).

| Field | Value |
|-------|--------|
| UTC | 2026-01-09 15:40:00 |
| CM III (SPICE) | 310.428° |
| GRS λ_III (GS-ORANGE) | ≈ 289.90° |
| φ_c / φ_g | ≈ −22.73° / −25.60° |

Independent reprocess agreed to ≈ 0.08° lon / 0.10° lat.

---

## What this release includes

- **Process** UI (open image → dual limb → publish)  
- **GS-ORANGE** colour centre for RGB stacks  
- **Frozen** SPIRE-Net weights (optional soft prior; no train UI)  
- Bundled SPICE kernels  
- Technical essay + geometry unit tests  

## What is intentionally not in the UI

- SPIRE-Net **training**  
- WinJUPOS CM table upload/download  
- Factory night / hard-synth / multi-epoch buttons  
- Online SPICE download (local kernels only)  

---

## License

See [`LICENSE`](LICENSE).

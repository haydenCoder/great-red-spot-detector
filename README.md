# Great Red Spot Detector

A small desktop app for measuring **where Jupiter’s Great Red Spot is** on your stacked image — System III longitude and latitude.

It is built for real telescope stacks (FITS, PNG, SER), not for pretty planet wallpapers. You open a file, set the mid-exposure time (or let the filename supply it), fit the limb (auto + by eye), and get a published centre with a short report.

**Repo:** https://github.com/haydenCoder/great-red-spot-detector  
**Release:** [v6.5.0](https://github.com/haydenCoder/great-red-spot-detector/releases/tag/v6.5.0)

---

## What it does

1. Reads your stack and mid-exposure **UTC** (from the FITS header, or from names like `2026-01-09-1540_…`).
2. Gets Jupiter geometry from **SPICE** (bundled kernels): CM III, distance, size.
3. Finds the limb, builds a simple cylindrical map, and estimates the GRS centre.
4. On RGB images, **GS-ORANGE** prefers the orange oval over random dark belts or a moon shadow.
5. Opens a dual limb step (green auto / cyan hand) so you can correct the outline.
6. Writes `publish.txt` and a short “best answer” file under `app/outputs/job_…/`.

Optional: paste a WinJUPOS core lon/lat to see how close you are (Δsky).

SPIRE-Net weights are included and can be used as a soft prior. **Training is turned off** so results stay stable.

---

## How it works (methods, short)

| Step | Method |
|------|--------|
| Time | FITS mid-time or filename; never wall-clock “now” |
| Geometry | SPICE (local kernels); optional Horizons geometry report |
| Limb | Multi-isophote fit + optional hand adjust |
| Map | Orthographic disk → lon/lat around CM |
| Centre | GS-ORANGE (colour) and/or GS-MAP / bary; many other estimators only for scatter |
| Check | Dual auto vs hand; optional WinJUPOS paste |

This is ordinary planetary imaging metrology: time, CM, limb, definition. It is **not** radio interferometry and **not** a NASA “official GRS catalogue.”

### Accuracy — honest version

- More complete than typical “draw a circle on Jupiter” hobby tools (real UTC/CM, dual limb, publish gates).
- Aimed at the same *discipline* as a careful WinJUPOS session. On a good frame it can land close; on a messy frame it can still be wrong.
- Always compare to your own WinJUPOS core pick when you care about the number.
- It does **not** beat HST/Juno or a perfect human desk by magic.

---

## Install / download

```bash
git clone https://github.com/haydenCoder/great-red-spot-detector.git
cd great-red-spot-detector
./RUN_ME.command
```

ZIP without git:

```bash
curl -L -o grs-detector.zip \
  https://github.com/haydenCoder/great-red-spot-detector/archive/refs/heads/main.zip
unzip grs-detector.zip
cd great-red-spot-detector-main
./RUN_ME.command
```

Manual:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cd app && python desktop_app.py
```

Needs Python 3.10+. SPICE kernels ship under `app/ephemeris_data/spice/`.

---

## One verified night (real stack)

Details: [`docs/TECHNICAL_ESSAY_VERIFIED_CASE_2026-01-09.md`](docs/TECHNICAL_ESSAY_VERIFIED_CASE_2026-01-09.md)

| | |
|--|--|
| Time | 2026-01-09 15:40:00 UTC |
| CM III (SPICE) | 310.428° |
| GRS λ_III (GS-ORANGE) | ≈ 289.90° |
| φ_c / φ_g | ≈ −22.73° / −25.60° |
| Second independent run | within ~0.1° of the app |

Earlier mistakes on the same file (wrong day in the UI, dark-core lock, N–S flip) are documented in that essay so others do not repeat them.

---

## What is not in the UI (on purpose)

- Neural-net **training** (weights stay frozen)
- WinJUPOS CM-table upload/download
- Factory night / hard-synth / multi-epoch buttons
- Online SPICE kernel download (local files only)

---

## License

See [`LICENSE`](LICENSE).

---

Questions and WinJUPOS comparisons: open a [discussion](https://github.com/haydenCoder/great-red-spot-detector/discussions) or issue on the repo.

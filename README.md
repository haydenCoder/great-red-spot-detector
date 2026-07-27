# GRS Observatory

Process-focused software for **ground-based optical measurement** of Jupiter’s **Great Red Spot** (System III longitude and latitude) from a single stacked frame (FITS / PNG / SER).

**Not** a NASA GRS catalog, radio interferometry, or Gaia-style astrometry.  
**Not** a continuous CNN training product — SPIRE-Net weights ship **frozen**.

## Quick start (macOS)

```bash
cd path/to/GRS_Observatory
./RUN_ME.command
```

1. **Open** your stack  
2. UTC: leave blank if the filename embeds time (`2026-01-09-1540_…`), or enter mid-exposure UTC  
3. **Process full** (auto limb + by-eye cyan limb)  
4. Read `app/outputs/job_*/SUPERDUPER_BEST_ANSWER.txt` and `publish.txt`

## Dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Requires local SPICE kernels under `app/ephemeris_data/spice/` (bundled). **No kernel auto-download** in this release.

## Verified case study

See [`docs/TECHNICAL_ESSAY_VERIFIED_CASE_2026-01-09.md`](docs/TECHNICAL_ESSAY_VERIFIED_CASE_2026-01-09.md).

Example result (independent reprocess agrees to ~0.1°):

| Field | Value |
|-------|--------|
| UTC | 2026-01-09 15:40:00 |
| CM III (SPICE) | 310.428° |
| GRS λ_III (GS-ORANGE) | ≈ 289.90° |
| φ_c / φ_g | ≈ −22.73° / −25.60° |

## What was removed from the UI

- SPIRE-Net **training** (weights remain for optional soft prior)  
- WinJUPOS **CM table** upload/download  
- Factory night / hard-synth / multi-epoch buttons  
- Online SPICE **download** by default  

## License

See `LICENSE`.

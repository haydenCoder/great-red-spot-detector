# Project map

**User guide:** `docs/GRS_OBSERVATORY_BOOK.md`
**Walkthrough essay:** `docs/ESSAY.md`
**How to run:** `docs/HOW_TO_RUN.md`
**Version:** see `VERSION` (**7.0.1**)

```
great-red-spot-detector/
├── RUN_ME.command
├── VERSION
├── README.md
├── docs/
│   ├── GRS_OBSERVATORY_BOOK.md      ← main operator guide
│   ├── ESSAY.md                     ← one code walkthrough essay
│   ├── HOW_TO_RUN.md                ← setup + run instructions
│   └── SECURITY.md                  ← security notes
├── app/
│   ├── desktop_app.py               ← macOS/desktop UI
│   ├── desktop_pipeline.py          ← process orchestration (shared by UI + server)
│   ├── server.py + templates/       ← optional local web UI
│   ├── precision_engine.py          ← core projection + consensus measure
│   ├── all_methods.py               ← ~80 estimator catalog
│   ├── gold_standard.py             ← named measurement definitions
│   ├── champion_measure.py          ← pro-desk measurement path
│   ├── publish_primary.py           ← official publish policy
│   ├── superduper.py                ← best-answer card
│   ├── spice_auto.py · ephemeris_pro.py
│   ├── planet_models.py             ← planet profiles (Jupiter/Saturn/Neptune/Uranus/Mars)
│   ├── planetary_stacker.py         ← planet-generalised stacker (per-lat / flow / global warp)
│   ├── planetary_derotator.py       ← planet-generalised derotator
│   ├── flow_warp.py                 ← dense 2D optical-flow warp
│   ├── frame_quality.py             ← lucky-imaging frame rejection
│   ├── ser_io.py                    ← SER/AVI capture reader-writer
│   ├── ap_stacker.py                ← ANS stacker + drizzle + derotate_frames
│   ├── observatory_pipeline.py      ← video-stack / sharpen / animate / jupos / video-to-answer
│   ├── sharpen_lab.py               ← wavelets / RL / unsharp
│   ├── grs_ellipse.py               ← rim-ellipse estimator
│   ├── nn_grs.py                    ← SPIRE-Net CNN (soft prior)
│   ├── vlbi_metrology.py            ← VLBI-inspired metrology
│   ├── netutil.py                   ← shared SSL context + array hash
│   └── paths.py · verbose_log.py    ← paths + logging plumbing
├── tests/        pytest suite (487 passing, 15 skipped)
└── tools/        audit harnesses + benchmarks
```

Entry points: `python app/cli.py`, `python app/desktop_app.py`, `python app/server.py`,
`python app/observatory_pipeline.py`. See `docs/HOW_TO_RUN.md`.

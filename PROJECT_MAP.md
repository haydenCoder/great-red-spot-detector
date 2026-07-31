# Project map

**User guide:** `docs/GRS_OBSERVATORY_BOOK.md`  
**Case study:** `docs/TECHNICAL_ESSAY_VERIFIED_CASE_2026-01-09.md`  
**Long technical essay:** `docs/PROFESSOR_TECHNICAL_ESSAY.md`  
**Version:** see `VERSION` (**6.7.6**)

```
great-red-spot-detector/   (or GRS_Observatory/)
├── RUN_ME.command
├── VERSION
├── README.md
├── docs/
│   ├── GRS_OBSERVATORY_BOOK.md      ← main operator guide
│   ├── TECHNICAL_ESSAY_VERIFIED_CASE_2026-01-09.md
│   ├── PROFESSOR_TECHNICAL_ESSAY.md
│   ├── PROMOTE_COPY_PASTE.md
│   ├── SECURITY.md
│   └── reference/                   ← architecture / module notes
├── app/
│   ├── desktop_app.py               ← macOS desktop UI
│   ├── desktop_pipeline.py          ← Process orchestration
│   ├── server.py + templates/       ← optional local web UI
│   ├── champion_measure.py
│   ├── superduper.py                ← best-answer card (SUPERDUPER_*.txt)
│   ├── publish_primary.py
│   ├── spice_auto.py · ephemeris_pro.py
│   ├── planet_models.py             ← Planet profiles (v6.7): Jupiter/Saturn/Neptune/Uranus/Mars
│   ├── planetary_stacker.py         ← planet-generalised stacker (per-lat / flow / global warp)
│   ├── planetary_derotator.py       ← planet-generalised derotator (measurement/prior/hybrid)
│   ├── flow_warp.py                 ← dense 2D optical-flow warp (v6.7.1)
│   ├── frame_quality.py             ← lucky-imaging frame rejection (v6.7.1)
│   ├── models/                      ← SPIRE-Net weights (frozen)
│   └── outputs/                     ← job folders after Process
├── tools/
│   ├── zonal_stacker_benchmark.py   ← Jupiter-zonal stacker benchmark
│   ├── flow_warp_benchmark.py       ← reproducible warp-mode A/B (v6.7.1)
│   └── real_photo_stack.py          ← all-modes run on real frames + report (v6.7.5)
└── tests/
```

After **Process**, open the job folder and read **`SUPERDUPER_BEST_ANSWER.txt`** (one-page “report this” card).

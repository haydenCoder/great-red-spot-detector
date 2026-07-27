# Project map

**User guide:** `docs/GRS_OBSERVATORY_BOOK.md`  
**Case study:** `docs/TECHNICAL_ESSAY_VERIFIED_CASE_2026-01-09.md`  
**Long technical essay:** `docs/PROFESSOR_TECHNICAL_ESSAY.md`  
**Version:** see `VERSION` (**6.5.0**)

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
│   ├── models/                      ← SPIRE-Net weights (frozen)
│   └── outputs/                     ← job folders after Process
└── tests/
```

After **Process**, open the job folder and read **`SUPERDUPER_BEST_ANSWER.txt`** (one-page “report this” card).

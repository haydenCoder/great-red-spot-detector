# Project map

**Only user guide:** `docs/GRS_OBSERVATORY_BOOK.md`  
**Technical essay:** `docs/PROFESSOR_TECHNICAL_ESSAY.md`  
**Version:** see `VERSION` (**6.5.0** — Champion Ultimate + SUPERDUPER + job finalize)

```
GRS_Observatory/
├── RUN_ME.command
├── VERSION
├── README.md
├── docs/
│   ├── GRS_OBSERVATORY_BOOK.md      ← THE operator book
│   ├── PROFESSOR_TECHNICAL_ESSAY.md ← full scientific essay
│   ├── SECURITY.md
│   ├── FULL_CODE_AUDIT_*.md         ← historical audits (pre-6.4)
│   └── reference/                   ← architecture / glossary
├── app/
│   ├── desktop_app.py
│   ├── desktop_pipeline.py          ← Process / Synthetic orchestration
│   ├── champion_measure.py          ← Ultimate automated path
│   ├── superduper.py                ← SUPERDUPER_BEST_ANSWER.*
│   ├── publish_primary.py
│   ├── precision_engine.py
│   ├── ephemeris_pro.py · spice_auto.py
│   ├── models/                      ← SPIRE-Net weights (ship with app)
│   └── ...
└── tests/
```

After **Process**, open the job folder and read **`SUPERDUPER_BEST_ANSWER.txt`**.

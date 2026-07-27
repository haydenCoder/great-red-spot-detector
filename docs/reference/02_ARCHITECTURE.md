# Architecture & data flow

**Software version:** 6.5.0  
**Updated:** 2026-07-19

## One-click entry

```
RUN_ME.command  →  venv + deps  →  app/desktop_app.py
```

## Layers

```
┌──────────────────────────────────────────────────────────┐
│  UI: desktop_app.py  |  server.py  |  cli.py               │
├──────────────────────────────────────────────────────────┤
│  product_core.py  ·  desktop_pipeline.py                   │
│  job_finalize.py  (Champion → publish → SUPERDUPER parity) │
├──────────────────────────────────────────────────────────┤
│  champion_measure · superduper · publish_primary           │
│  winjupos_plus · winjupos_twin · gold_standard             │
│  precision_engine · research_grade · vlbi_metrology        │
│  synthetic_hq · ephemeris_pro · spice_auto                 │
│  sota_accuracy · all_methods (scatter only)                │
├──────────────────────────────────────────────────────────┤
│  grs_complete_system (imaging; dead bulk stripped)         │
└──────────────────────────────────────────────────────────┘
```

## Process path (real image)

1. Load image (FITS/SER/PNG) — prefer red for GRS  
2. Optional imaging branch (`grs_complete_system`)  
3. `require_observation_time` (never silent wall-clock)  
4. `resolve_pro_ephemeris` (override → WJ → SPICE → Horizons → analytical)  
5. Limb nav + orientation on `NavState`  
6. `run_research_grade` / optical metrology stack  
7. Gold-standard definitions + WinJUPOS twin (limb/definition sensitivity)  
8. **`job_finalize.finalize_science_package`** (Champion + publish + WinJUPOS+ + SUPERDUPER + `JOB_COMPLETE`)  
9. Optional dual human pass  
10. Re-publish + SUPERDUPER after dual (official may become human)  
11. `FULL_REPORT` / `job_result.json` under `outputs/job_*`  

Server `/api/process` attaches the same finalize stack after gold.

## Synthetic path

1. `synthetic_hq.generate` → PNG + FITS + truth JSON  
2. Same measure stack as Process (including champion / SUPERDUPER)  
3. Truth recovery vs published centre (arcsec)

## Certification path

`cli.py certify` / `product_core.certify` → N metrology synthetics → gates → SHIP/HOLD

## Key modules (6.5)

| Module | Role |
|--------|------|
| `champion_measure.py` | Automated ultimate path + UNBEATABLE_AUTO gates |
| `superduper.py` | One-page best-answer card |
| `publish_primary.py` | Fixed publication hierarchy |
| `winjupos_plus.py` | Desk-parity export (φ_g, EW, desk score) |
| `precision_engine.py` | Limb, map, template, sky σ |
| `ephemeris_pro.py` | Geometry chain + SPICE↔Horizons ΔCM |

## Outputs

`app/outputs/` in source tree, or `GRS_Observatory_Data/outputs` when frozen (`paths.py`).

Every science job should include at least:

- `SUPERDUPER_BEST_ANSWER.txt`  
- `champion.txt`  
- `publish.txt`  
- `pro_ephemeris.json`  

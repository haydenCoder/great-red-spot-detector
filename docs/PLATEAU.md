# Plateau — why “you can’t improve more” (inside this app)

**Software:** 6.5.0  
**Date:** 2026-07-19

## What “done” means here

For **automated ground-based optical GRS centre metrology on a single stacked frame**, this codebase has reached a **product plateau**:

| Layer | Status |
|-------|--------|
| Time fail-closed | Done (`fits_time`) |
| CM chain + provenance | Done (SPICE / Horizons / WJ / override) |
| Fixed publish definition | Done (Champion / GS-MAP hierarchy) |
| Multi-method scatter only | Done (soup/SOTA not published) |
| Full absolute σ budget | Done (CM ⊕ time ⊕ limb ⊕ def ⊕ method) |
| Ultimate multi-gate lock | Done (`UNBEATABLE_AUTO`, 13 gates) |
| Dual-channel + nav stability | Done |
| One-page archival answer | Done (`SUPERDUPER_BEST_ANSWER.*`) |
| Desktop ↔ server product parity | Done (`job_finalize.py`) |
| Dead generated bulk stripped | Done (monolith ~4.5k live lines) |
| Docs / essay aligned | Done (Book + professor essay) |

Further **estimator soup** or **grade renames** will not materially beat a careful desk on the same CM and definition.

## What would actually beat this product

These require **new science or data**, not more Python:

1. **Multi-frame ACCIV / wind-field** GRS centre (literature: Asay-Davis / Wong-class pipelines)  
2. **Spacecraft or HST** resolution and absolute navigation  
3. **Human WinJUPOS** on messy limbs / weird seasons  
4. **Real multi-night labeled archive** (your measurements vs published tables) to calibrate floors  
5. **Instrument metadata** (pixel scale, PA flip, filter) for every night  

## Operator rule after plateau

1. Maximize **data quality** (UTC, CM, red stack, limb).  
2. Open **`SUPERDUPER_BEST_ANSWER.txt`**.  
3. If not `UNBEATABLE_AUTO`, fix the **failed gates** in `champion.txt` — don’t add more methods.  

## Honesty

Optical · ground-based · not VLBI μas · not NASA GRS catalog ·  
`UNBEATABLE_AUTO` = unbeatable **inside this app**, not versus the entire scientific world.

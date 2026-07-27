# Plateau — what “good enough” means for this app

**Software:** 6.5.0  
**Date:** 2026-07-27

## What “done” means here

For **automated ground-based optical GRS centre measurement on a single stacked frame**, further renames and extra estimators rarely beat a careful desk on the same CM and definition.

| Layer | Status |
|-------|--------|
| Time fail-closed | Done (`fits_time`) |
| CM chain + provenance | Done (SPICE / Horizons / WJ / override) |
| Fixed publish definition | Done (champion / GS-MAP / GS-ORANGE hierarchy) |
| Multi-method scatter only | Done (extra methods not published as the centre) |
| Absolute σ budget | Done (CM ⊕ time ⊕ limb ⊕ def ⊕ method) |
| Quality multi-gate lock | Done (`unbeatable_auto`) |
| Dual limb + nav stability | Done |
| One-page answer card | Done (`SUPERDUPER_BEST_ANSWER.*`) |
| Desktop ↔ server parity | Done (`job_finalize.py`) |
| Docs aligned | Done (Book + case essay) |

## What would actually improve results

These need **new data or science**, not more marketing terms:

1. Multi-frame wind-field style GRS centres  
2. Spacecraft or HST resolution and navigation  
3. Careful human WinJUPOS on messy limbs  
4. A real multi-night archive (your measures vs published tables)  
5. Full instrument metadata (pixel scale, PA flip, filter) every night  

## Operator rule

1. Maximize **data quality** (UTC, CM, stack, limb).  
2. Open **`SUPERDUPER_BEST_ANSWER.txt`**.  
3. If quality gates failed, fix those items in `champion.txt` — don’t add more methods.  

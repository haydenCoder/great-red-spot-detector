# GRS Observatory — Full Code Audit (v5.9.3)

**Date:** 2026-07-15  
**Scope:** All `app/*.py` modules (~29,700 lines, 28 files)  
**Method:** Parallel deep read of every module + static pattern scan + smoke regression (AS_P5)

This is a **module-complete** audit (every file reviewed). It is **not** a literal printed comment on each of 29k lines — that would be unreadable noise. Findings are ranked by impact.

---

## Inventory

| Lines | Module | Role |
|------:|--------|------|
| 10328 | `grs_complete_system.py` | Monolith I/O, nav, measure, CLI |
| 1784 | `desktop_app.py` | Tk desktop UI |
| 1726 | `vlbi_metrology.py` | Optical “VLBI-method” stack |
| 1693 | `nn_grs.py` | SPIRE-Net CNN |
| 1542 | `server.py` | Flask local web API |
| 1167 | `sota_accuracy.py` | Multi-method robust primary |
| 1020 | `precision_engine.py` | Nav, map, core measure |
| 994 | `all_methods.py` | Method suite |
| 937 | `gold_standard.py` | Named GS definitions |
| 904 | `all_methods_extra.py` | Extra CV methods |
| 817 | `ephemeris_pro.py` | CM/ephemeris fusion |
| 733 | `result_report.py` | FULL_REPORT text |
| 733 | `research_grade.py` | Research error budget |
| 710 | `desktop_pipeline.py` | Desktop full process |
| 671 | `synthetic_hq.py` | Synthetic Jupiter |
| 534 | `spice_auto.py` | SPICE kernels + geometry |
| 470 | `multi_epoch.py` | Drift / multi-epoch |
| 404 | `ai_hard_cases.py` | Hard-case NN assist |
| 400 | `batch_prove.py` | Synth certify batch |
| 386 | `product_core.py` | Product entry surface |
| 383 | `hard_synth_suite.py` | Stress calibration |
| 373 | `license_manager.py` | License keys |
| 248 | `cli.py` | CLI |
| 225 | `nasa_compare.py` | Schematic NASA compare |
| 174 | `group_access.py` | Usage logs |
| 159 | `paths.py` | Data/code paths |
| 122 | `ram_ssd.py` | RAM/SSD budget |
| 54 | `verbose_log.py` | Console ring buffer |

---

## Fixes applied in this pass (v5.9.3)

| Severity | Fix |
|----------|-----|
| **P0 science** | `gold_standard._cyl_axes`: lon_rel **±90°** (was ±180 → wrong GS oval/edges) |
| **P0 science** | `vlbi` `_ncc_peak`: no more zero-map → (0,0) bogus peak |
| **P0 science** | `multiscale_template_match` accepts **NavState** (MS_NCC was always broken in suite) |
| **P0 product** | AI hard-case **no longer overwrites** SOTA/gold/pipeline primaries |
| **P1** | CLI `--no-nn` actually works (`default=False`, `use_nn=not args.no_nn`) |
| **P1** | `multi_epoch` crash when `error_budget is None` |
| **P1** | Server `/api/file` allowlist **outputs/ + uploads/** only |
| **P1** | Factory multi-epoch scans **job folder**, not all `outputs/` |
| **P1** | SOTA excludes **ENS_*** double-count; single-method σ floored |
| **P2** | SPICE ET keeps subseconds |
| **P2** | Server version from `PRODUCT_VERSION`; less “Harvard VLBI” oversell |

---

## Cross-cutting issues (still open)

### Critical if you sell / expose the app
1. **Default license secret** — anyone can mint keys if secret not rotated.  
2. **License not enforced** at process/synth (UI only).  
3. **Server is local-trust** — do not bind `0.0.0.0` without auth.

### Science / accuracy
1. **Two map geometries** — flat `make_cylindrical` vs oriented VLBI map.  
2. **CLI/product_core synth ≠ desktop full VLBI** — certify can pass a lighter path.  
3. **nasa_compare lon is schematic**, not a NASA GRS catalog.  
4. **Correlated methods** — many threshold/bary variants share one mask family.  
5. **FITS time** incomplete in `grs_complete_system` measure paths (DATE-OBS gaps → risk of “now”).  
6. **Pipeline seed still strong prior in SOTA** — correct for AS_P5, wrong if pipeline is wrong.  
7. **hard_synth** “orientation/limb” stresses are partial (not full nav physics).

### Engineering
1. **`grs_complete_system.py` monolith** (~10k) — hard to test, high surface area.  
2. **Silent `except: pass`** density in `nn_grs`, spice, etc.  
3. **Version strings** historically drifted (partially fixed).  
4. **Global `_use_nn`** flag race if concurrent.  
5. **FULL_REPORT** dumps entire JSON (huge files).

---

## Per-module grade (honest)

| Module | Health | Notes |
|--------|--------|-------|
| `sota_accuracy` | **B+** | AS_P5 lessons applied; still pipeline-heavy |
| `precision_engine` | **B** | Solid core; orientation gap |
| `vlbi_metrology` | **B** | Heavy, naming oversells; NCC fixed |
| `gold_standard` | **B** | Axes bug fixed; multi-primary still confusing |
| `all_methods` (+extra) | **B-** | Useful flood; correlated |
| `research_grade` | **B** | Defaults to VLBI; classic path weaker gates |
| `ephemeris_pro` / `spice_auto` | **B** | Good chain; Horizons parser fragile |
| `nasa_compare` | **C+** | Honest docs, easy to misread UI |
| `desktop_pipeline` | **B** | Full stack; factory epoch root fixed |
| `desktop_app` | **B-** | Feature-rich; license not gated |
| `server` | **C+** | Powerful + dangerous if exposed; file API locked |
| `cli` / `product_core` | **C+** | Light certify vs desktop mismatch |
| `nn_grs` / `ai_hard_cases` | **B-** | Synth domain gap; AI no longer clobber |
| `license_manager` | **D** for commercial | Default secret + no enforce |
| `grs_complete_system` | **C** | Works; too big; time/nav risks |
| helpers (paths, ram, log, group) | **B** | Fine for local group deploy |

---

## What “peak” would still need

1. Shared geometry contract (one cylindrical map).  
2. One metrology entry for CLI = desktop = web.  
3. Real FITS mid-time required (fail loud).  
4. Unit tests: synthetic blob at known lon → GS edges + SOTA.  
5. License secret + enforce if selling.  
6. WinJUPOS validation set of real FITS (including limb).  
7. Shrink or modularize `grs_complete_system.py`.

---

## Regression smoke (post-fix)

- Gold axes: CM±90 map columns  
- Multiscale on `NavState`: OK  
- AS_P5: SOTA ~**186.57°**, **SOTA_FAIR ~61**, near pipeline, not EXCELLENT  

---

*Generated as part of full-code audit; code changes shipped as VERSION 5.9.3.*

# Glossary & cross-feature map

**Version note:** 6.5.0 product vocabulary (Champion / SUPERDUPER / UNBEATABLE_AUTO / job_finalize).

## Terms

| Term | Meaning |
|------|---------|
| System III | Jupiter longitude system tied to magnetic rotation (1965) |
| CM III | Central meridian longitude in System III at mid-exposure |
| Limb navigation | Fitting planet centre + equatorial radius from the disk edge |
| Multi-isophote limb | Several outline sizes; stability-weighted consensus |
| Cylindrical map | Lon ∈ [−90°, +90°] about CM × latitude (visible hemisphere) |
| GS-MAP | Map dark core — classic fixed publish definition |
| GS-BARY | Image-plane dark barycentre — ordered fallback |
| Champion Ultimate | Automated best path (`champion_measure.py`) with full σ budget |
| UNBEATABLE_AUTO | All ultimate gates passed; in-app lock (not vs HST) |
| SUPERDUPER | One-page best-answer card for the job folder |
| φ_c / φ_g | Planetocentric / planetographic latitude |
| Truth recovery | Synthetic-only: \|measured − truth\| in sky arcseconds |
| Error budget | CM ⊕ timing ⊕ limb ⊕ definition ⊕ method → σ_sky |
| SPICE | NASA NAIF toolkit + kernels for planetary geometry |
| Horizons | JPL geometry service (not a GRS lon catalog) |
| Soup / SOTA | Multi-estimator scatter diagnostics only |
| Dual measure | Automatic stack + human by-eye limb/definition |

## Import / call graph (simplified)

- `desktop_app` → `desktop_pipeline`, `ephemeris_pro`, `license_manager`
- `cli` → `product_core`, `license_manager`
- `product_core` → `desktop_pipeline`, `synthetic_hq`, `spice_auto`
- `desktop_pipeline` → `research_grade`, `champion_measure`, `publish_primary`, `superduper`, `winjupos_plus`, `gold_standard`, `ephemeris_pro`, …
- `champion_measure` → `precision_engine`, `gold_standard` (extent)
- `publish_primary` → hierarchy over champion / twin / gold
- `ephemeris_pro` → `spice_auto`

## File responsibilities cheat-sheet

| Want to change… | Edit |
|-----------------|------|
| UI buttons / metrics | `desktop_app.py` |
| Process stages | `desktop_pipeline.py` / `product_core.py` |
| Automated ultimate measure | `champion_measure.py` |
| One-page answer card | `superduper.py` |
| Publish hierarchy | `publish_primary.py` |
| GRS finding math | `precision_engine.py` |
| Error bars / optical stack | `vlbi_metrology.py`, `research_grade.py` |
| Fake Jupiter | `synthetic_hq.py` |
| Kernels / SPICE / Horizons | `spice_auto.py`, `ephemeris_pro.py` |
| Stacking / SER | `grs_complete_system.py` |
| User guide | `docs/GRS_OBSERVATORY_BOOK.md` |
| Professor essay | `docs/PROFESSOR_TECHNICAL_ESSAY.md` |

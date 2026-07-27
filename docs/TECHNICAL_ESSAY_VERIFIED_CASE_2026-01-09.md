# Optical Measurement of Jupiter’s Great Red Spot from a Single Amateur Stack

## A verified System III case study (2026-01-09 15:40:00 UTC)

**Software:** Great Red Spot Detector (process-focused release)  
**Data:** AutoStakkert!3 RGB FITS — `2026-01-09-1540_4-U-RGB-Jup_lapl6_ap141.fit`  
**Methods:** SPICE geometry · multi-isophote limb · cylindrical map · GS-ORANGE colour centre · dual limb  
**Verification date:** 2026-07-27  

---

## Abstract

This note walks through one real night: a stacked planetary image reduced to a **System III longitude** and **latitude** for Jupiter’s Great Red Spot (GRS). The pipeline is ordinary optical work (not radio interferometry, not spacecraft navigation): mid-exposure UTC, SPICE central-meridian geometry, limb fit, map projection, and a **colour-first** centre (GS-ORANGE) suited to RGB stacks where the GRS is orange rather than a dark intensity core.

On the study frame, a first reduction with a **wrong UI time** produced a false centre near **λ_III ≈ 184.8°, φ ≈ −30.7°**. After enforcing **filename UTC**, **automatic N–S flip**, **moon/shadow masking**, and **GS-ORANGE**, application and independent reprocess agree at the **0.1°** level:

| Quantity | Application (job cd90037acbe4) | Independent reprocess |
|----------|--------------------------------|------------------------|
| UTC | 2026-01-09 15:40:00 | same |
| CM III (SPICE) | 310.428° | 310.428° |
| GRS λ_III | **289.902°** | **289.825°** |
| φ_c | **−22.728°** | **−22.824°** |
| φ_g | **−25.595°** | **−25.701°** |
| \|λ − CM\| | ≈ 20.5° | ≈ 20.6° |
| \|Δ\| app vs reprocess | — | **0.08° lon · 0.10° lat** |

Chatbot-generated ephemerides (e.g. CM III ≈ 77°, GRS L3 ≈ 350°) **fail SPICE** for this epoch and are rejected. Absolute equality to a human **WinJUPOS** core pick was not available (`NO_MANUAL_PICK`) and remains the recommended desk confirmation.

---

## 1. Purpose and scope

### 1.1 Goal

Given one stacked RGB image of Jupiter and a reliable mid-exposure UTC, publish:

1. **System III west longitude** of the GRS centre (λ_III)  
2. **Planetocentric** and **planetographic** latitude (φ_c, φ_g)  
3. The **CM III** and distance used for absolute geometry  
4. Quality flags that state when the product must not be treated as absolute

### 1.2 Explicit non-goals

| Topic | Status |
|-------|--------|
| NASA “official GRS lon of the night” catalog | Does not exist in Horizons/SPICE |
| Radio interferometric (μas) absolute positions | Out of scope |
| Gaia stellar-astrometry style catalogues | Not used |
| Multi-frame wind-field (ACCIV) pipelines | Out of scope |
| Continuous CNN retraining | **Disabled** — frozen weights only |

### 1.3 Longitude systems

| System | Role |
|--------|------|
| I | Equatorial rotation |
| II | Mid-latitude (historical GRS tables often L2) |
| III | Magnetic / modern standard (this software’s publish L3) |

**L2 and L3 are different clocks.** Comparing L2 = 83° to L3 ≈ 290° without conversion is invalid.

Planetographic latitude (WinJUPOS-style):

\[
\varphi_g = \arctan\!\Big(\big(R_\mathrm{eq}/R_\mathrm{pol}\big)^2 \tan\varphi_c\Big),
\quad R_\mathrm{eq}=71492\,\mathrm{km},\; R_\mathrm{pol}=66854\,\mathrm{km}.
\]

---

## 2. Software design (process-focused release)

### 2.1 Operator path

```text
Open FITS/PNG
    → UTC from filename if header empty (e.g. 2026-01-09-1540)
    → SPICE CM III + distance (bundled kernels; no online download required)
    → Image prep: N–S auto-flip · moon mask · orange-as-dark mono
    → Limb fit + cylindrical map
    → Multi-method suite (scatter only)
    → GS-ORANGE colour seed (primary for RGB)
    → Dual: auto + by-eye limb dialog
    → publish.txt + SUPERDUPER_BEST_ANSWER.txt
```

### 2.2 Modules (authoritative)

| Module | Role |
|--------|------|
| `desktop_app.py` | Process-focused UI (train/factory/download removed) |
| `desktop_pipeline.py` | `run_process_full` orchestration |
| `fits_time.py` / `grs_image_prep.py` | Time + orientation + orange GRS |
| `spice_auto.py` | Local SPICE geometry (download off by default) |
| `precision_engine.py` | Limb, map, lon/lat, moments |
| `champion_measure.py` / `publish_primary.py` | Gates + publish hierarchy |
| `human_choice.py` | Dual auto / by-eye limb |
| `app/models/spire_net_weights.npz` | **Frozen** optional soft prior (no train UI) |

### 2.3 Geometry contract

`NavState` holds disk centre, radius, flattening, CM III, sub-latitude, north PA.  
`make_cylindrical` and `px_to_lonlat` are inverses (unit-tested).  
Internal convention with PA = 0 and N-up: L3 > CM → right of centre; north → image up.

### 2.4 Publish policy

1. Prefer **core latitude band** φ ∈ [−28°, −16°].  
2. Prefer **GS-ORANGE** when colour seed is strong and \|λ − CM\| ≲ 70°.  
3. Prefer GS-MAP / GS-BARY over soup.  
4. **SOTA / 80+ methods = scatter only** — not the official centre when they conflict.  
5. Do not let a near-limb pipeline seed veto a coherent orange core.

---

## 3. Case data and reduction history

### 3.1 Input

| Field | Value |
|-------|--------|
| File | `2026-01-09-1540_4-U-RGB-Jup_lapl6_ap141.fit` |
| Shape | 3 × 504 × 504 float RGB |
| Header DATE-OBS | **Absent** (AutoStakkert HISTORY only) |
| Time source | **Filename → 2026-01-09 15:40:00 UTC** |

### 3.2 Failure product (wrong path — for the record)

| Field | Wrong value |
|-------|-------------|
| UI time | 2026-01-10 15:39:26 |
| CM III | ≈ 100.76° |
| Publish | GS-MAP λ_III ≈ **184.82°**, φ ≈ **−30.72°** |
| Dual | DIFFERENT (Δλ ~172°, Δsky ~61″) |
| Verdict | **Reject** — wrong day; dark false lock; lat outside GRS core |

### 3.3 Success product (application)

| Field | Value |
|-------|--------|
| Job id | `cd90037acbe4` (representative good run) |
| Definition | **GS-ORANGE** |
| λ_III | **289.9023°** |
| φ_c | **−22.7275°** |
| φ_g | **−25.5948°** |
| CM III | **310.4283°** (`spice_auto`) |
| Distance | **4.2317 AU** |
| Apparent diameter | **≈ 46.59″** |
| Quality | **GOOD**, publish_ok, absolute_ok |
| Dual | **MATCH**, Δ = 0 |

### 3.4 Independent verification (reprocess of the same FITS)

An external script (not the desktop UI) reloaded the FITS, applied N–S flip, SPICE CM, and GS-ORANGE:

| Check | Result |
|-------|--------|
| Filename time | PASS |
| SPICE CM ≈ 310.43° | PASS |
| Lat in [−28, −16]° | PASS |
| \|λ − CM\| ≈ 20.6° (not near limb) | PASS |
| Matches app within 0.1° | **PASS (0.08° lon, 0.10° lat)** |
| Chatbot CM III 77° matches SPICE | **FAIL** |
| Chatbot L3 350° matches measure | **FAIL** |

Annotated verification image: `app/outputs/_independent_verify_grs.png`.

### 3.5 Orientation ablation

| Orientation | Orange lock | Lat |
|-------------|-------------|-----|
| As filed | fail (oval northern) | +22° peak |
| **N–S flip** | **λ ≈ 289.8°** | **−22.8°** ← adopted |
| E–W only | fail | — |
| E–W + N–S | λ ≈ 331° | −22.8° (mirror family) |

---

## 4. Geometry used (real SPICE numbers)

At **2026-01-09 15:40:00 UTC**, local kernels (`de440s.bsp`, `pck00011.tpc`, `naif0012.tls`):

| Quantity | Value |
|----------|--------|
| CM III (sub-observer lon) | **310.428°** |
| Sub-observer latitude | **+1.389°** |
| Earth–Jupiter distance | **4.231701 AU** |
| Apparent equatorial diameter | **46.588″** |
| Light time (one-way, SPICE) | **≈ 2112 s** |

Horizons distance/diameter agree at the **0.001 AU / 0.01″** level. Absolute GRS longitude is **not** a Horizons field.

---

## 5. Why this result is considered accurate

1. **Correct UTC** from the stack name (not a leftover UI day).  
2. **CM III from SPICE** for that UTC.  
3. **Latitude in the GRS band** (~−22.7° / φ_g ~−25.6°).  
4. **Colour lock** on the orange oval (not dark belt/moon).  
5. **On-disk offset ~20°** matches “left of centre” after N–S correction.  
6. **Dual MATCH** on the good job.  
7. **Independent reprocess** within **0.1°** of the app.

Not yet proven: formal **WinJUPOS** core equality (operator must paste L3 and φ_g).

---

## 6. Rejected external claims (chatbots)

Example chatbot set for the same UTC:

| Claim | Value | Assessment |
|-------|--------|------------|
| CM III | 77.2° | **Fails SPICE (310.43°)** |
| GRS L3 | 350.1° | **Δ ≈ 60° vs image measure** |
| GRS offset from its CM | ~−87° | Near-limb; **conflicts with image** |
| Lat 22° S | — | **Roughly consistent** with SEB |

**Do not publish chatbot ephemerides as NASA or desk truth.**

---

## 7. Production constraints of this release

| Feature | Status |
|---------|--------|
| Process + dual limb | **Primary UI** |
| Frozen SPIRE-Net weights | Shipped; **training UI disabled** |
| Online SPICE kernel download | **Off** (local kernels only) |
| WinJUPOS CM table download/upload | **Removed** (use CM override field) |
| Factory night / hard-synth / overnight train | **Removed from UI** |
| Multi-method optical stack | Kept (labelled honestly — not radio VLBI) |

---

## 8. Citation

```text
Optical GRS reduction of AutoStakkert stack 2026-01-09-1540 (UTC 2026-01-09 15:40:00)
using GRS Observatory (GS-ORANGE).
λ_III = 289.90° · φ_c = −22.73° · φ_g = −25.60° · CM III = 310.43° (SPICE).
Independent reprocess: Δλ = 0.08°, Δφ = 0.10°.
WinJUPOS desk paste: pending.
```

---

## 9. Operator checklist

1. Open the stack; leave time blank if the name embeds UTC, or set mid-exposure UTC.  
2. Process full → fit cyan limb to the true edge.  
3. Confirm publish definition **GS-ORANGE** or **GS-MAP** with lat in band.  
4. Read `SUPERDUPER_BEST_ANSWER.txt` and `publish.txt`.  
5. Optionally paste WinJUPOS core lon/lat for Δsky.  
6. Ignore method-soup / SOTA as the official centre when they disagree with GS-ORANGE.

---

## Appendix — Unit tests

```text
python -m unittest tests.test_geometry_limb_lonlat tests.test_science_p0_fixes
# geometry round-trip, planetographic, flips, map dark — OK
```

---

*End of essay. Companion software: GRS Observatory process release.*

# GRS Observatory — Full Code + Science Audit (v6.1.0)

**Date:** 2026-07-15  
**Tree:** `/Users/haydenchung/Downloads/GRS_Observatory`  
**Version file:** `6.1.0`  
**Scope:** All `app/*.py` (~31,100 lines, 34 modules) + launch scripts + security/docs + literature cross-check  
**Method:** Module inventory, dual deep-read agents, static pattern scan, import smoke, synthetic length-scale verification, online literature map (Simon 2018, ACCIV, WinJUPOS practice, Horizons/SPICE)

This supersedes `FULL_CODE_AUDIT_5.9.3.md` for the current tree. Prior P0 fixes (gold ±90 map axes, NCC zero-map, multiscale NavState, AI not overwriting primaries) remain in force; **new and residual** findings are ranked below.

---

## Executive scorecard

| Domain | Grade | One-line |
|--------|------:|----------|
| Self-use laptop GRS detector | **B** | Real, usable multi-layer stack; not vaporware |
| Absolute System III science integrity | **B− / C+** | Good architecture; residual silent-time + size-scale + dual geometry |
| Synthetic truth-recovery discipline | **B+** | Strong; certify path now uses desktop full stack |
| Commercial / multi-user security | **D–F** | Hardcoded admin password, default HMAC secret, license fail-open |
| Product honesty vs SCIENCE_CLAIMS | **B** | Policy docs excellent; code still says Harvard/VLBI often |
| Engineering hygiene | **C−** | Monolith dead clones, no tests/, version drift |
| Nobel / “best human can achieve on a laptop” | **Not yet** | Needs fail-closed time, one geometry, lean methods, real-FITS validation |

**Bottom line for self-use:** Keep and harden. This is a serious optical metrology workspace. It is **not** yet publication-safe for absolute GRS longitude without strict user discipline (time + CM source + fixed definition). It is **not** radio VLBI and must never claim μas or “Nobel algorithm.”

---

## Inventory (current)

| Lines | Module | Role |
|------:|--------|------|
| 10346 | `grs_complete_system.py` | Monolith imaging + science + ~300 dead wrappers |
| 1800 | `desktop_app.py` | Tk desktop UI |
| 1731 | `vlbi_metrology.py` | Optical “VLBI-method” stack |
| 1693 | `nn_grs.py` | SPIRE-Net CNN |
| 1665 | `server.py` | Local Flask API |
| 1187 | `sota_accuracy.py` | Robust multi-method primary |
| 1034 | `precision_engine.py` | Nav, map, core measure |
| 994 | `all_methods.py` | Method suite |
| 941 | `gold_standard.py` | Named GS definitions |
| 904 | `all_methods_extra.py` | Extra CV methods |
| 817 | `ephemeris_pro.py` | CM / geometry fusion |
| 733 | `research_grade.py` | Research error budget |
| 733 | `result_report.py` | FULL_REPORT text |
| 714 | `desktop_pipeline.py` | Desktop full process |
| 671 | `synthetic_hq.py` | Synthetic Jupiter |
| 539 | `spice_auto.py` | SPICE kernels + geometry |
| ≤470 | remaining helpers | multi_epoch, license, security, fits_time, … |

**Tests:** `pyproject.toml` declares `testpaths = ["tests"]` but **`tests/` does not exist**.

---

## Literature map (what “best” actually means)

| Source / practice | What professionals do | Your code |
|-------------------|----------------------|-----------|
| **WinJUPOS / JUPOS** | Limb outline + CM + human measure; System III | Pro ephemeris + cylindrical map desk analogs |
| **Simon et al. 2018 (AJ)** | Size ~0.194°/yr lon shrink; lat ~0.048°/yr; drift increasing | Not used for sanity; toy model in `nasa_compare` instead |
| **Asay-Davis ACCIV (2009)** | Multi-frame advection-corrected CIV for winds | Single-frame NCC / CIV_WIN only (honest gap in METHOD_LITERATURE) |
| **Wong et al. 2021 (GRL)** | ACCIV 2-pass, ellipse + spoke max azimuthal | No wind field product |
| **Sánchez-Lavega 2024** | WinJUPOS navigate; EW size ±0.5° ground, ±0.1° HST | Edges exist; scale bug on GS-OVAL |
| **Horizons / SPICE** | Geometry only — **not** GRS longitude | Mostly correctly documented; UI can still confuse |
| **Honest real-night floor** | ~0.5–2″ relative on good lucky imaging | SCIENCE_CLAIMS matches; grade labels can oversell |

**Physical anchors (2020s GRS):** ~12–15° EW-class (shrinking; Damian Peach ~12.5k km ~2023), lat ~−22° planetographic, CM System III (1965) period ≈ 9h55m29.711s.

---

## P0 — Critical science (wrong answers)

### P0-S1. Silent wall-clock UTC corrupts System III

Several paths still fall back to **now** when observation time is empty/unparseable:

| File | Behavior |
|------|----------|
| `vlbi_metrology.py` | `user_time_iso or time.strftime(...)`; parse fail → `datetime.utcnow()` |
| `research_grade.py` | Same strftime default into VLBI |
| `grs_complete_system.compute_geometry` | `t_utc is None` → `datetime.now(utc)` |
| `server.py` `/api/ephemeris` | empty → now |
| `desktop_pipeline` factory | empty session_time → now |
| `desktop_app` | time field **prefilled** with now |

Sys III rotates ~**36°/hour**. Wrong epoch = wrong absolute lon by tens of degrees.

**Good contrast:** `fits_time.require_observation_time`, CLI `measure`, and `/api/process` refuse silent now.

**Fix:** One rule everywhere: call `require_observation_time` or hard-fail. Prefill desktop time **empty** with red required field.

---

### P0-S2. GS-OVAL longitude size scaled **2×** (180° map treated as 360°)

`make_cylindrical` spans lon_rel ∈ **[−90°, +90°]** (180° total).  
`gold_standard.measure_gs_oval_and_edges` still does:

```python
x_span = float(xs.max() - xs.min() + 1) * (360.0 / w)  # BUG → use 180.0 / w
```

`_cyl_axes` was fixed for the ±180 grid bug (audit 5.9.3) but **length scale was left wrong**.  
Template matching in `precision_engine` correctly uses `Ltry / 180.0 * w`.

**Impact:** Reported EW extent systematically large vs Simon/Peach-class sizes.

---

### P0-S3. Split geometry contract (oriented map vs unoriented inverse)

- `make_cylindrical` applies `sub_lat` + `north_pa`.
- `px_to_lonlat` (moment / GS-BARY path) **does not**.
- VLBI has `px_to_lonlat_oriented` only inside its stack.

**Impact:** With pro orientation on, map methods and image-plane methods disagree systematically; ensembles average incompatible geometries.

**Fix:** Single inverse on `NavState` shared by all modules.

---

### P0-S4. Horizons observer parse is float-heuristic

`ephemeris_pro` picks sub-lon/lat/PA from “floats in ranges,” not fixed QUANTITIES columns. RA, NP.ang, rates can collide with lon ranges.

**Impact:** Wrong CM or PA can be accepted with tight-looking σ.

**Fix:** Header-driven CSV parse; require SPICE↔Horizons |ΔCM| ≲ 0.2° or demote grade.

---

### P0-S5. “NASA compare” GRS lon is a schematic toy

```python
lon = (40.0 + 0.30 * days + 1.0 * sin(...89.8d...)) % 360
```

Not a NASA catalog. Drift/size do not match Simon 2018 rates. Easy to misread as validation.

**Fix:** Rename to `schematic_grs_trend`; never label “NASA_REF_LON”; optional JUPOS table only.

---

### P0-S6. Limb validation does not place GRS near limb

`limb_validation.limb_lon_rel` is unused; synth still random near-CM. Hard-synth “near_limb” is a 45° CM fudge, not foreshortened geometry.

**Impact:** False confidence that AS_P5-class CM-lock failures are gone.

---

## P0 — Critical security (if anyone else uses the tree)

| ID | Finding | File |
|----|---------|------|
| P0-X1 | **Hardcoded admin password** in source (default if `GRS_ADMIN_PASSWORD` unset) | `admin_console.py` |
| P0-X2 | **Default license HMAC secret** ships in source; verification accepts keys minted with it | `license_manager.py` |
| P0-X3 | Desktop `_gate()` is **`return True`** (license matrix is theater for UI) | `desktop_app.py:1227–1229` |
| P0-X4 | Group logs mirror email/hostname/machine_id into `OWNER_SHARED_LOGS` when present | `group_access.py`, `paths.py` |

**Self-use only:** X3 is fine if you never sell seats. **X1 is not fine** — rotate password, require env, remove plaintext default from any copy you share.

---

## P1 — High integrity / product

1. **Gold primary hijacked by ENS_*** — named GS-MAP/BARY overwritten by all-methods ensemble (`gold_standard` ~582+). Pro practice is a **fixed definition**.
2. **SOTA pulls hard toward pipeline seed** (score +140, lon pull 30–45%) — not independent confirmation if pipeline is wrong.
3. **SOTA σ ÷ √neff** understates error (correlated threshold family).
4. **Dual lon fields in VLBI export** — `lon_iii_deg` vs `lon_bias_corrected_deg` naming inverted risk for multi-epoch consumers.
5. **Version drift:** `VERSION=6.1.0`, `pyproject=5.2.0`, server capabilities `"5.3.0"`, health `"5.9.2"`, monolith `__version__=1.0.0`.
6. **~300 dead functions** in monolith (`mc_case_*` ×200, `normalize_clip_lo*` ×100) — audit noise, no science.
7. **SSL unverified fallback** on SPICE/Horizons downloads (`spice_auto`, `nasa_compare`).
8. **Server soft-fail** on geometry parse can leave CM=0 briefly (`except: pass`).
9. **No automated tests.**

---

## P2 — Medium / architecture

| Topic | Note |
|-------|------|
| Planetocentric vs planetographic lat | Conversion mostly only at VLBI export |
| Lat prior hard-coded −22° | Fine for modern GRS; not epoch-adaptive |
| Method flood (50+) | Most share one dark-mask family — definition scatter, not independence |
| SPIRE-Net | Synth domain gap; batch_prove wisely disables NN on synth |
| FITS EXPTIME/2 always | Risk if header already mid-exposure |
| `except Exception` density | `nn_grs`, spice, server — hides failures |
| Unpinned deps | Reproducibility risk |
| Marketing language | “Harvard-grade”, “VLBI stack” still in code strings |

---

## What works well (keep)

1. **`docs/SCIENCE_CLAIMS.md`** — correct, publishable honesty policy.  
2. **Pro ephemeris chain:** override → WinJUPOS → SPICE auto → Horizons → analytical + provenance.  
3. **`fits_time` policy** when used; process APIs that refuse empty time.  
4. **Cylindrical map contract** mostly unified (−90…+90); gold axes fix retained.  
5. **SOTA multi-cluster + map-edge-lock filter + drop ENS_*** — real AS_P5 lessons.  
6. **AI hard-case does not overwrite primaries.**  
7. **Formal optical error budget** (VLBI-inspired methods, not μas claims).  
8. **`product_core` + desktop full stack for certify** (improved vs light-path certify).  
9. **`security_hard`** path roots, rate limit, CSP — good local defaults (127.0.0.1).  
10. **Accounts** PBKDF2 + no raw passwords in logs.  
11. **METHOD_LITERATURE.md** correctly does not claim full ACCIV wind fields.

---

## Smoke checks run this audit

| Check | Result |
|-------|--------|
| Import science stack (16 modules) | All OK |
| `PRODUCT_VERSION` from VERSION | `6.1.0` |
| `using_default_secret()` | `True` |
| GS-OVAL uses `360.0/w` | Confirmed |
| Monolith dead wrappers | 200 `mc_case_*` + 100 `normalize_clip_lo*` |
| Desktop license gate | Always True |
| Admin default password in source | Present |
| Unit tests directory | Missing |

---

## Nobel-grade roadmap (best on a laptop)

Ordered by impact for **self-use science quality**:

### Phase A — Fail-closed science (1–2 days)
1. Delete every observation-time `datetime.now()` / `utcnow()` fallback; use `require_observation_time`.  
2. Fix `360.0/w` → `180.0/w` in gold oval size.  
3. Unify `px_to_lonlat` with oriented inverse.  
4. Desktop time field empty + required for Process.  
5. Rename schematic “NASA” GRS lon everywhere.

### Phase B — One truth surface (3–5 days)
6. All process/synth/factory only via `product_core`.  
7. Gold primary = named definition only; SOTA/all-methods as secondary.  
8. SOTA: report population MAD / definition scatter as systematic; mark “pipeline-seeded” grades.  
9. Horizons CSV column parse + SPICE cross-check.  
10. Single `VERSION` wired to server/pyproject/monolith.

### Phase C — Prove it (1 week)
11. Create `tests/`: known-lon synth recovery, empty-time refuse, path traversal, gold length scale.  
12. Real public Jupiter FITS + WinJUPOS manual Δ (even one night).  
13. Limb validation that **forces** GRS at lon_rel ≈ 70–80°.  
14. Delete dead monolith wrappers (~1.5k lines).

### Phase D — Optional excellence
15. Multi-frame ACCIV-lite on SER cubes (true literature path for winds).  
16. Pin deps; opt-in owner logs only; remove hardcoded admin secret.  
17. Lean default method set (5–8) + optional “all methods” for definition scatter.

### Language rules (already in SCIENCE_CLAIMS — enforce in UI)
- **Use:** research-oriented · formal error budget · SPICE-backed geometry · certified synthetic recovery  
- **Avoid:** Nobel · Harvard official · μas VLBI optical · guaranteed 0.1″ on any photo  

---

## Honest capability table (self-use)

| Task | Ready? | Notes |
|------|--------|-------|
| Detect / localize GRS on good image | **Yes** | Multi-method + VLBI stack |
| Absolute Sys-III lon for a paper | **Conditional** | Only with correct mid-UTC + SPICE/WinJUPOS CM + fixed definition + reported σ |
| GRS size (EW°) like OPAL/Simon | **Broken until P0-S2** | 2× scale on GS-OVAL |
| Wind / vorticity fields | **No** | Needs multi-frame ACCIV |
| Beat WinJUPOS human measure | **Unproven** | No real-FITS validation set |
| Commercial multi-seat product | **No** | Security/license fail-open |
| “Nobel-level” claim | **No** | Not a scientific claim software can make |

---

## Suggested next action

If you want the tree to move toward “best on a laptop,” implement **Phase A** immediately (fail-closed time + size scale + geometry unify). I can apply those patches next without expanding scope into a full rewrite.

*Audit generated 2026-07-15 against VERSION 6.1.0.*

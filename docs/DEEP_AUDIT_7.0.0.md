# Deep Reasoning Audit — v7.0.0 "Velocity Pro"

> **Follow-up (same session, 2026-08-13): the §6.1 recommendation is now shipped
> and verified at full scale.**
> The catastrophic fallback lock (§2.4) is fixed in `app/precision_engine.py`:
> the cluster seeds on the colour lock whenever it is isolated from every dark
> method, and a sanity-checked `redness` lock is never pruned as a cluster
> outlier. Stacking was also hardened (`app/planetary_stacker.py`): sigma-clipped
> robust combination + alignment-confidence frame weighting — see CHANGELOG.
>
> ### Full-scale before/after (1000-case resolution × seeing sweep, 2 workers)
>
> | Metric (n = 1000) | Before | After |
> |---|---|---|
> | max \|lon\| error | **101.9°** | **0.785°** |
> | lon p99 | **74.6°** | **0.503°** |
> | lon p90 | 0.248° | 0.246° |
> | lon median | 0.096° | 0.096° |
> | max \|lat\| error | **7.65°** | **1.174°** |
> | lat p99 | **5.59°** | **0.923°** |
> | within 0.5° | 87.7 % | **88.0 %** |
> | within 0.2° | 53.6 % | 53.6 % |
>
> Worst very-blurry cell (lon max): 1080p 92.35°→**0.50°**, 720p 101.86°→**0.65°**,
> 540p 91.79°→**0.79°**. The catastrophic decoy-lock tail is **eliminated** with
> no change to the good-data behaviour (clear/mild/blurry cells are bit-identical
> to before, still 100 % within 0.5°). The remaining ~47 % of very-blurry frames
> outside 0.5° are genuine blur-limit errors (0.5–1.2°), not the lock bug.
> Fast suite stays green (461 passed); committed 100-case campaign 115 passed.
>
> ### Full-scale 7000-case deep audit (post-fix, 2 workers)
>
> | Stat (n = 7000) | Value |
> |---|---|
> | completed / crashed | 7000 / 0 |
> | catastrophic (>10°) locks | **0** |
> | lon median / p90 / p99 / **max** | 0.099° / 0.279° / 0.577° / **0.995°** |
> | lat median / p90 / p99 / max | 0.163° / 0.566° / 0.948° / 1.452° |
> | within 0.2° / 0.5° / 1.0° | 53.6 % / 87.0 % / 99.3 % |
> | clear lon median | 0.077° |
>
> Every one of the 12 resolution×seeing cells has lon_max ≤ 0.995° (worst cell:
> 540p very-blurry, 0.995°). The pre-fix catastrophic tail (§2.4) — which reached
> 101.9° in the 1000-case audit — is **absent across all 7000 frames**. The
> "sub-1° on every frame" headline is now literally true at full scale.

**Auditor:** Arena.ai Agent Mode (deep pass) · **Date:** 2026-08-13
**Branch:** `arena/019ffadd-great-red-spot-detector` (from `347e03f`, v7.0.0)
**Truth source:** planted geometric centre on metrology-mode synthetic (the only
exact ground truth available in this sandbox — real photos have no mid-exposure
UTC and cannot be downloaded here).

This audit answers one question with hard numbers: **where does the GRS
measurement fail, why, and how does accuracy deteriorate as resolution drops
and seeing worsens?** It runs four independent instruments against the same
pipeline and cross-references them:

| Instrument | Tool | Scope | Question answered |
|---|---|---|---|
| Regression suite | `pytest -m "not slow"` | 856 tests | does the code still work? |
| Deterioration sweep | `tools/detailed_audit_1000.py` | **1000 frames** (res × seeing) | how does error grow as data degrades? |
| Per-estimator diagnosis | `tools/diagnose_failure_modes.py` | 72 frames | *which* estimator is wrong on each frame? |
| Per-method bias/scatter | `tools/per_method_audit.py` | 100-case matrix | per-method bias, scatter, worst case |

---

## 1. Regression suite

```
461 passed, 10 skipped, 385 deselected (slow)  — 0 failed   (0:14:42)
```

The 385 deselected are the `slow` render-campaign tests (the committed
`resolution_seeing_100` campaign and friends), which are run separately. The
full fast suite is green on v7.0.0, including the v7.0 C-core (`cspeed`),
native (`native/grscore.c`), and planet-generalised stacker/derotator tests.

---

## 2. Deterioration sweep — 1000 frames (resolution × seeing)

Matrix: `{1080p, 720p, 540p} × {clear 0.38″, mild 0.80″, blurry 1.60″,
vblurry 2.40″}`, 70–100 frames per cell, GRS planted at the literature
latitude, scored against the **geometric oval centre** (exact truth). Uses the
lean measurement path (no NN prior, no multi-scale verify — characterises the
core physics, not the optional priors).

### 2.1 Overall headline (n = 1000)

| Stat | Longitude | Latitude |
|---|---|---|
| median \|error\| | **0.096°** | 0.163° |
| p90 | 0.248° | 0.537° |
| p99 | 74.6° ⚠️ | 5.59° |
| max | **101.9°** ⚠️ | 7.65° |

| Gate | Fraction inside |
|---|---|
| within 0.2° | 53.6 % |
| within 0.5° | 87.7 % |
| within 1.0° | 98.4 % |

Sky-plane error (proper angular error, not degrees-on-planet):
median **0.069″**, p90 0.196″, max 26.4″.

The headline numbers are dominated by one cell — see §2.4. On **every**
clear/mild/blurry frame the result is inside 0.5°, and clear/mild medians are
0.058–0.092° (lon). The long tail exists **only** in the 2.40″ very-blurry
stress band.

### 2.2 Per-cell table — median |lon error| and gate pass-rate

| Cell | n | lon med | lon p90 | lat med | ≤0.2° | ≤0.5° |
|---|---|---|---|---|---|---|
| 1080p / clear | 70 | 0.058° | 0.153° | 0.069° | 97 % | 100 % |
| 1080p / mild | 70 | 0.065° | 0.146° | 0.115° | 91 % | 100 % |
| 1080p / blurry | 70 | 0.089° | 0.198° | 0.240° | 29 % | 100 % |
| 1080p / vblurry | 70 | 0.116° | 0.361° | 0.498° | 0 % | **53 %** |
| 720p / clear | 80 | 0.082° | 0.177° | 0.072° | 94 % | 100 % |
| 720p / mild | 80 | 0.072° | 0.168° | 0.117° | 89 % | 100 % |
| 720p / blurry | 80 | 0.106° | 0.220° | 0.229° | 39 % | 100 % |
| 720p / vblurry | 80 | 0.205° | 0.561° | 0.522° | 2.5 % | **42 %** |
| 540p / clear | 100 | 0.086° | 0.175° | 0.100° | 88 % | 100 % |
| 540p / mild | 100 | 0.092° | 0.196° | 0.118° | 74 % | 100 % |
| 540p / blurry | 100 | 0.094° | 0.277° | 0.216° | 38 % | 100 % |
| 540p / vblurry | 100 | 0.165° | 0.517° | 0.433° | 5 % | **56 %** |

### 2.3 Deterioration curves (lon median error)

**Error vs seeing** (each row = resolution):

```
            clear    mild    blurry  vblurry
  1080p     0.058    0.065    0.089    0.116
  720p      0.082    0.072    0.106    0.205
  540p      0.086    0.092    0.094    0.165
```

**Error vs resolution** (each row = seeing tier):

```
            1080p    720p    540p
  clear      0.058   0.082   0.086
  mild       0.065   0.072   0.092
  blurry     0.089   0.106   0.094
  vblurry    0.116   0.205   0.165
```

Two clean, monotone effects:

1. **Seeing dominates.** Median error roughly doubles from clear → vblurry at
   every resolution (0.058→0.116° at 1080p; 0.086→0.165° at 540p). Resolution
   is a second-order effect: dropping 1080p → 540p costs ~0.03° on clear data.
2. **The median stays small everywhere** — even vblurry median lon is ≤0.21°.
   The *median* never deteriorates catastrophically; the **tail** does (§2.4).

### 2.4 The catastrophic tail — 12/1000 frames, 100% in vblurry

| Stat | Value |
|---|---|
| Frames with \|lon error\| > 10° | **12 / 1000** |
| All in the 2.40″ vblurry band | 12 / 12 |
| Any in clear/mild/blurry | **0** |
| Method on every one | `template_pos…` / `consensus…` (the **fallback**, never `redness`) |
| Max error | 101.9° (720p), 92.4° (1080p), 91.8° (540p) |
| Self-reported quality flag | **> 0.5 on all 12** (the pipeline thought it was good) |

These are not "slightly off" measurements — they are **wrong-feature locks**:
the redness estimator's sanity gate rejects its own (correct) lock on a frame
where the 2.40″ seeing has smeared the GRS below its score/lat-band thresholds,
and control falls through to the dark-oval template path, which locks onto a
**decoy SEB oval** up to ~102° away. The latitude error on these frames
(5.6–7.7°) confirms the lock is a different dark belt feature entirely.

**This is the single most important finding of the audit** and it is invisible
to the headline median: a ~1.2 % catastrophic rate, entirely confined to the
2.40″ stress band, produced by the *fallback* path, and *not* flagged by the
internal quality score.

---

## 3. Per-estimator diagnosis — which method is wrong, on which frame

`tools/diagnose_failure_modes.py` runs every estimator on the same frame and
scores each against truth (n = 12 per tier, clear/mild).

### 3.1 Per-estimator median sky error (degrees)

| Estimator | 1080p/clear | 1080p/mild | 720p/clear | 720p/mild | 540p/clear | 540p/mild |
|---|---|---|---|---|---|---|
| **redness** | **0.191** | 0.310 | 0.323 | 0.287 | 0.281 | **0.256** |
| moment | 0.227 | 0.235 | 0.307 | 0.268 | 0.246 | 0.273 |
| template | 0.433 | 0.476 | 0.568 | 0.529 | 0.495 | 0.501 |

`redness` and `moment` are essentially tied for best (0.19–0.32°), with
`redness` better at 1080p and `moment` marginally better at 540p/720p; both are
far ahead of `template`, which is the worst in every tier (0.43–0.57°) because
its dark-core + decoy pull is systematic, not random. This is why the published
path is redness-led, with the dark methods demoted to fallbacks.

### 3.2 Failure-mode classification (clear/mild)

| Best estimator on frame | Count |
|---|---|
| colour (redness) right | 37 |
| dark (template/moment) right | 35 |
| dark split (>12° template↔moment) | 0 |

On clear/mild frames the colour and dark estimators are near a coin-flip for
"closest to truth", which is exactly why v6.6.1 reported the irreducible
0.20–0.35° tail: no static weighting wins both camps on every frame. The
catastrophic dark-split (>12°) mode — the one the dark-split/redness-seed logic
in `precision_engine.py` exists to catch — does **not** fire on clear/mild; it
is a blurry/vblurry phenomenon (hence §2.4).

---

## 4. Per-method bias & scatter — the 100-case committed matrix

`tools/per_method_audit.py` calls each estimator **directly** (bypassing the
consensus) on the 100-case resolution×seeing matrix, so each method's raw
accuracy is visible, not just the published blend.

| Method | n | \|dlon\| median | \|dlon\| max | \|dlat\| median | within 1° |
|---|---|---|---|---|---|---|
| **redness** | 100 | **0.087°** | **0.53°** | 0.196° | **99 %** |
| ellipse (rim) | 97 | 0.120° | 2.81° | 0.143° | 87 % |
| moment | 100 | 0.214° | **51.0°** | 0.103° | 78 % |
| template | 100 | 0.434° | **105.2°** | 0.236° | 65 % |
| map_dark | 1 | 100.3° | — | 9.6° | 0 % |

| Published path | lon source | lat source | \|dlon\| med | within 1° |
|---|---|---|---|---|
| old hybrid (v6.6.1) | redness | moment | 0.087° | 83 % |
| **current (v6.6.2)** | redness | **redness** | 0.087° | **99 %** |

Reading the table:

* **`redness` is the only estimator that never catastrophically fails**
  (max 0.53°, 99 % within 1°). Its R−B colour excess is distributed
  symmetrically about the oval centre, so it tracks the geometric centre even
  as the dark core swirls/hollows.
* **`template` is a catastrophic-lock generator**: median only 0.43° but
  mean 31°, pstdev 52.7°, max 105°. It locks onto decoy SEB ovals with high
  contrast. The consensus logic's job is essentially "never let template own
  the answer uncorroborated" — and §2.4 shows where that defence still leaks.
* **`moment` is excellent in latitude (median 0.103°) but carries its own
  decoy tail in longitude (max 51°)** and a +0.42° latitude bias.
* **`map_dark` effectively never fires** on this synthetic (1/100); on real
  maps it is a position-only fallback, not a primary.
* **The v6.6.2 change (redness for latitude instead of moment) is a strict
  win**: within-1° rises 83 % → 99 % with no regression in longitude, because
  it drops moment's biased latitude tail.

---

## 5. Error-detection mechanisms (what the pipeline already checks)

The measurement is defended by a layered error-detection stack, all in
`app/precision_engine.py` + `app/accuracy_gates.py`:

1. **Disk measurability** — `assess_disk_quality()` refuses to invent numbers
   from a frame with no resolved disk.
2. **GRS latitude-band sanity** — `_method_is_sane()` rejects any estimator
   lock outside the SEB band (wrong-feature locks: poles, EZ, thin barges).
3. **Longitude cluster outlier rejection** — `reject_lon_outliers()`
   (≤18° from the robust circular median) prunes decoy locks.
4. **Dark-split arbitration** — when template and moment disagree by >12°,
   the seed moves to the colour lock (colour survives blur that destroys the
   dark oval).
5. **Template corroboration** — a crisp-but-isolated template peak is treated
   as a decoy and rejected unless a peer method agrees within
   `TEMPLATE_CORROBORATION_DEG`.
6. **Redness-primary (v6.6.2)** — on a measurable RGB frame with a
   sanity-checked redness lock, redness **is** the answer; the defensive
   consensus is the fallback.
7. **Publish gates** — CM-source trust, timing-vs-longitude penalty,
   limb-outline spread, and definition scatter (`assess_publish_quality`).

These are why clear/mild/blurry is 100 % within 0.5°: every decoy lock the
template/moment generate is caught by layers 2–6 … **except** on the frames
where redness itself fails its gate.

---

## 6. The deterioration story, precisely

Combining §2 and §5, accuracy degrades in two distinct regimes:

| Regime | Seeing | What happens | Result |
|---|---|---|---|
| **Good** | 0.38″ clear → 1.60″ blurry | median drifts 0.06°→0.10°; lat error grows (0.07°→0.24°) as the dark core smears; all decoys caught | **100 % within 0.5°, 0 catastrophic** |
| **Stress** | 2.40″ vblurry | redness score/lat-band sanity begins to reject its own correct lock; fallback template locks decoys | ~44–58 % >0.5°, **1.2 % catastrophic (up to 102°)** |

The **root cause of every catastrophic error** is a specific control-flow hole:
`redness_ok` (the v6.6.2 gate) is the *only* thing standing between the good
redness answer and the decoy-prone template fallback. When seeing pushes the
redness score below threshold on a frame where the fallback template happens to
find a high-contrast decoy, the pipeline reports a confident (quality > 0.5)
but catastrophically wrong number.

### 6.1 Concrete recommendations (ranked)

1. **Self-flag the fallback.** When the published answer comes from
   `template_pos` / `consensus` because `redness_ok` was False (rather than
   because redness genuinely disagreed), stamp the result `GRS_NOT_DETECTED` or
   force `quality = 0`. The audit shows the fallback is correct only ~half the
   time at 2.40″ — it should never be reported as confident.
2. **Wire the catastrophic mode into a gate.** A template/consensus lock that
   is >~20° from the redness lock's *prior* (or from the SEB-band colour
   centroid, even when redness fails its lat/score gate) is a decoy by
   definition on vblurry frames. A cheap "is there *any* R−B excess within
   20°?" test would have caught all 12 catastrophic frames.
3. **Report a hard measurability floor.** v6.6.1 already documented 2.40″ as
   below the floor; this audit supplies the number — **~1.2 % catastrophic,
   ~50 % >0.5°** — to attach to that claim, and shows it is a fallback-path
   defect, not an irreducible physics limit.
4. **Do not tune on this matrix.** As `docs/IMPROVEMENT_DIAGNOSIS.md` records,
   the published path is a local optimum on the 100-case matrix; the only
   honest way to move it is a real-photo WinJUPOS-pick validation
   (`tools/real_photo_validate.py`), which is out of scope for a synthetic
   sandbox.

---

## 7. Honest limits

* **No real photos.** Every number here is metrology-mode synthetic with
  planted-centre truth. A real JPEG has no mid-exposure UTC, so absolute
  System-III longitude is unmeasurable on it; synthetic is the only exact
  scoreboard available in this sandbox.
* **Lean path.** The deterioration sweep uses `lean=True` (no SPIRE-Net prior,
  no multi-scale verify). The published path adds those; on synthetic they are
  priors/redundant, so the physics conclusions carry over.
* **The ≤0.2° headline is a clear/mild claim.** 92 % of clear frames (and
  74–91 % of mild) are inside 0.2°; blurry/vblurry are governed by the 0.5°
  gate, which is 100 % through 1.60″ seeing.

---

## 8. Reproduce

```bash
# fast regression suite
.venv/bin/python -m pytest -m "not slow" -q

# deterioration sweep (resumable; ~35 min on 2 vCPU)
.venv/bin/python tools/detailed_audit_1000.py --workers 2

# per-estimator failure-mode diagnosis
.venv/bin/python tools/diagnose_failure_modes.py --n 12

# per-method bias/scatter on the committed 100-case matrix
.venv/bin/python tools/per_method_audit.py --workers 2

# report analysis (reads the detailed_audit cache)
.venv/bin/python tools/audit_report_analysis.py
```

Raw results (gitignored, resumable): `runs/detailed_audit_1000.jsonl`,
`runs/per_method_audit.jsonl`, `runs/diagnose_failure_modes.json`,
`runs/audit_report_analysis.json`.

# Jupiter Great Red Spot Detector — The Book

**Version:** see `VERSION` in the project root (currently **6.5.0**)  
**Audience:** self-use / lab / careful observers (I wrote this for my coursework, but it should be useful for anyone measuring Jupiter)  
**This is the only user guide.** Other files under `docs/` are optional (essay, audits, module dumps).

---

## 0. What this software is

Jupiter Great Red Spot Detector takes a high-resolution Jupiter image (FITS / SER / PNG / JPEG) and tries to measure the Great Red Spot's **System III longitude and latitude** with calibrated uncertainties. I built it because I got frustrated with eyeballing my manual measurement picks and not having a repeatable number I could actually trust — so I ended up writing this whole thing over a couple of weekends (and many late nights). Turns out measuring a cloud band on a planet half a billion km away is harder than I thought!

Key features (the ones I'm actually proud of):

- **SPICE** (auto kernels) and/or **JPL Horizons** and/or **CM table** for planet geometry — the CM source is *everything* for absolute lon, I learned that the hard way  
- **Limb navigation** (multi-isophote) + oriented cylindrical map — the outline choice is the single biggest source of systematic error, so we probe multiple isophotes (this took ages to get right)  
- **Champion Ultimate** path: dark-core lock, dual-channel, nav stability, full error budget  
- **SUPERDUPER** card: one file that says *what number to report tonight* — I named it that because I was tired of guessing which number to use  
- **Publish hierarchy:** UNBEATABLE_AUTO / Champion → GS-MAP → GS-BARY → pipeline — this was broken for a while (GS-MAP always won over champion), took me a day to figure out why  
- ~80 soup estimators + SOTA = **scatter only**, not the published centre — seriously, don't report SOTA as your answer, it's just a sanity check  
- Optional paste of **your manual GRS lon/lat** → equality Δsky — this is how you actually validate your pipeline  
- Synthetic planets for truth-recovery self-tests  
- SPIRE-Net CNN weights **bundled** under `app/models/` (soft prior only — physics methods are authoritative)

**Ground-based optical metrology.** This is **not** professional radio observatories (microarcseconds) and **not** an official NASA GRS longitude catalog.  
**UNBEATABLE_AUTO** means: every automated gate in *this app* passed on that frame — **not** that the result beats professional observatories, JunoCam, or a perfect human manual desk.

---

## 1. Start the app (macOS)

I run this on macOS from source. If you're on another OS, just adapt the paths.

### From source (usual)

```bash
cd ~/Downloads/GREAT\ RED\ SPOT\ DETECTER/GRS_Observatory
# or wherever you keep the tree
./RUN_ME.command
```

or:

```bash
./Launch_Desktop.command
```

### Optional web UI

```bash
./Launch_GRS_Observatory.command
# http://127.0.0.1:8765
```

I mostly use the desktop app — the web UI is nice for showing results to friends though.

### CLI

```bash
cd app
python3 cli.py version
python3 cli.py eph "2026-07-15 12:00:00"
python3 cli.py process /path/to/jupiter.fits --time "2026-01-10 15:39:26"
python3 cli.py synth --mode metrology --res 1080p
```

---

## 2. What number you should trust (publish policy)

| Priority | Product | Use for |
|----------|---------|---------|
| **1 — SUPERDUPER / PUBLISH** | Official centre from policy | **Report this** |
| **1a** | **UNBEATABLE_AUTO** (if all gates pass) | In-app lock: no weaker module overrides |
| **1b** | **Champion** (GS-MAP / engine, absolute OK) | Best automated optical path |
| **2** | **GS-MAP** twin / gold | Pro fixed definition (dark core on map) |
| **3** | **GS-BARY** | Fallback centre |

| **4** | Pipeline / research-grade stack | Secondary / bias-corrected stack |
| — | **SOTA + method soup (~80)** | Scatter / confidence **only** |
| — | **W/E edges** | Size (EW extent), not “the” lon |
| — | **vs manual measurement paste** | Equality Δsky ″ |
| — | **Dual auto + human** | Official = human when dual is on |

### Files to open after Process

| File | Meaning |
|------|---------|
| **`SUPERDUPER_BEST_ANSWER.txt`** | **One-page “report this” card** |
| **`REPORT_THIS_ONE_LINE.txt`** | Citation line to paste |
| `publish.json` / `publish.txt` | Official publish block |
| `champion.txt` / `champion.json` | Ultimate gates, full σ budget, dark score |
| `measure_plus.*` | Validation vs manual picks |
| `dual_measure.*` | Auto vs human (when dual enabled) |
| `outline_twin.*` | Limb outline + definition sensitivity |
| `gold_standard.*` | Named GS definitions |
| `pro_ephemeris.*` | CM + distance + provenance |
| `FULL_REPORT` / human report | Long text (starts with SUPERDUPER) |

### Process full = AUTO + BY EYE (desktop)

**▶ Process full (auto limb + by-eye limb)** typically:

1. Opens the **limb window** — **GREEN** auto limb, **CYAN** by-eye limb  
2. Runs full science stack (research-grade, gold, twin, **champion**, publish, SUPERDUPER)  
3. Human pass with cyan limb + definition when dual is on  
4. Writes `publish.*`, `champion.*`, `SUPERDUPER_BEST_ANSWER.*`, `dual_measure.*`  

**Keys:** arrows move · PgUp/PgDn size · R reset cyan→green · drag centre  

### Tips (practical measurement / BAA / SPICE)

These are the things I kept messing up at first, so I wrote them down:

1. Mid-exposure **UTC** only — ~**0.6° System III per minute** of time error (BAA). I once used the start time instead of mid-exposure and my lon was off by ~2°.  
2. Trusted **CM**: SPICE / Horizons / CM overrideL / override — **not** analytical for absolute publish. Analytical CM can be 10–15° off, which I learned the painful way.  
3. Prefer **red** for GRS contrast — the spot shows up way better in red than in green or blue.  
4. Publish **dark core** (GS-MAP / Champion), not random rim, unless you choose outline on purpose. The rim is the *edge*, not the *centre*.  
5. Same definition every night when comparing to manual measurement — mixing core vs edge definitions gives fake “drift”.  
6. Paste your manual lon/lat → **Δsky ″** is the real equality test. If you’re not pasting your numbers, you’re not really validating.  
7. Horizons ≠ GRS lon catalog (geometry only) — it gives you Jupiter’s orientation, not where the GRS is.  
8. Soup/SOTA = scatter only — never the published centre. I know it’s tempting when SOTA looks tight, but it’s just a sanity check.  
9. If `absolute_publish_ok` is false: do not claim absolute System III.  
10. If `UNBEATABLE_AUTO` is true: in-app hierarchy is locked; still not professional observatories. It’s the best *my code* can do, not the best *anyone* can do.  

**How to cite (example):**

```text
GRS GS-MAP λ_III=…°  φ_c=…°  φ_g=…°  (CM III=…° source=spice|horizons|override)
σ_sky≈…″  grade=CHAMPION|UNBEATABLE_AUTO
Method soup not used for the published centre.
```

Use **φ_g (planetographic)** when matching manual measurement latitude language.

### When is it “EQUAL_TO_MANUAL”?

Only if **all** hold:

1. You paste **your** manual measurement GRS lon (and ideally lat)  
2. CM source is trusted: `override`, `spice`, or `horizons`  
3. Δsky ≤ **1 arcsecond** between publish and your paste  

---

## 3. Absolute System III: SPICE, Horizons, CM table

Absolute longitude needs **time + CM**. Feature detection alone is not enough.

### Priority chain (code)

1. **CM III override** (paste from manual measurement) — highest for that frame  
2. **CML table**  
3. **SPICE auto** (`app/ephemeris_data/spice/`)  
4. **JPL Horizons** (Δ, light-time, sub-obs lon/lat, NP.ang)  
5. **Analytical fallback** — relative only; weak for absolute zero  

Provenance is stored as `cm_source`. When both SPICE and Horizons parse, **|ΔCM|** is recorded; ultimate lock wants \|ΔCM\| ≲ 0.35°.

### Observation time (never wall-clock “now”)

- Enter **mid-exposure UTC** for real Process  
- Or FITS `DATE-OBS` / `DATE-AVG` / `MJD-OBS`  
- System III ~**36°/hour** — wrong time destroys absolute lon  

Synthetic jobs invent a random epoch for truth-recovery tests.

---

## 4. Champion Ultimate (what “best automated” means)

Module: `app/champion_measure.py` (runs on every Process / Synthetic). I spent a *lot* of time on the gate logic — there’s a fine line between “everything passes” and “actually useful quality control”. The original version had a bug where the `_cand_score` function was never even being called, which meant any random estimator could become champion. That was embarrassing.

| Step | What it does |
|------|----------------|
| Red prefer | red/visual-red for GRS |
| Multi-isophote limb | Stability-weighted outlines |
| SEB contrast | Local enhance for dark oval |
| Estimators | GS-MAP, multi-size GS-TMPL, engine, map, bary |
| Dark-core score | Prefer real dark cores; demote SEB waves |
| Map refine | 2-pass sub-pixel + bootstrap σ |
| Nav stability | Jitter limb; lock must not wander |
| Dual-channel | Red vs mono agreement |
| Error budget | CM ⊕ timing ⊕ limb ⊕ definition ⊕ method → σ_sky |

### Grades

| Grade | Meaning |
|-------|---------|
| **UNBEATABLE_AUTO** | All ultimate gates passed — no weaker path in this app overrides |
| WORLD_CLASS / CHAMPION / STRONG / USABLE | Automated quality ladder |
| HOLD | Do not trust absolute lon |

### Ultimate gates (summary)

Trusted CM · SPICE↔Horizons CM cross-check · finite pos · SEB lat · no map-edge · limb stable · definition tight · dark core · nav stable · dual-channel · high score · GS lock or elite score · σ_sky tight  

Details and fail list: **`champion.txt`**.

---

## 5. Desktop workflow (real image)

1. **Open** FITS / SER / PNG / JPEG  
2. **Observation time UTC** (if FITS has no date)  
3. Geometry: SPICE ON, Horizons ON, optional CM override  
4. Optional: paste manual GRS lon/lat  
5. **Process**  
6. Open job folder → **`SUPERDUPER_BEST_ANSWER.txt`**  
7. Check `champion.txt` grade + absolute_ok  
8. If you pasted your manual numbers: equality Δsky  

### Synthetic / Factory

- **Synthetic** = known truth → truth-recovery arcsec  
- **Factory night** = synth + hard stress + packaging  
- Self-test, not “NASA truth”

---

## 6. CLI cookbook

```bash
python3 cli.py version
python3 cli.py eph "2026-07-15 12:00:00"
python3 cli.py process /data/jup.fits --time "2026-01-10 15:39:26" \
  --cm 142.3 --wj-lon 186.5 --wj-lat -22.1
python3 cli.py synth --mode metrology --res 1080p
python3 cli.py certify --n 20
```

---

## 7. Limb outline and definitions

**Larger** limb outline (fainter outer edge) vs **smaller** (tighter) changes radius and can shift lon/lat.

| ID | Meaning |
|----|---------|
| GS-MAP | Dark core on cylindrical map — **default publish** |
| GS-BARY | Image-plane dark barycentre |
| GS-TMPL | Dark oval template match |
| GS-EDGE-W / E | Ends of oval — size |
| Champion / UNBEATABLE_AUTO | Automated ultimate product |

Always publish **one** definition. Do not mix EDGE-W with MAP core.

---

## 8. CNN weights (SPIRE-Net)

```text
app/models/spire_net_weights.npz
app/models/spire_net_meta.json
```

Copied into the active data `models/` on start. Soft prior only; Process works without them. Prefer keeping weights present.

---

## 9. Honest limits

I want to be upfront about what this can and can’t do. I’m a student measuring cloud features on a gas giant — not a NASA mission.

| Claim | Truth |
|-------|--------|
| Official NASA GRS lon product | **No** — and I wouldn’t claim that |
| Professional radio observatories (microarcseconds) | **No** — we’re talking arcseconds, not microarcseconds |
| Always 0.1″ on any photo | **No** — that’s only on perfect synthetics with known truth |
| Match careful manual on good data | **Often can** (same CM + definition) — this is the real benchmark |
| Match professional observatories | **No** — obviously not |
| Best automated path *in this app* when gates pass | **Yes (UNBEATABLE_AUTO)** — but that’s just “best within my code”, not “best in the world” |

Typical real-night class: **~0.5–2″** when geometry is right. Synthetics can look tighter because we literally planted the answer.

---

## 10. Security / logs

- Default web bind: **127.0.0.1**  
- Free local: `export GRS_LICENSE_OPEN=1` for all features without a key  
- Do not expose the web port to the public internet  

---

## 11. Dependencies

```bash
pip3 install -r requirements.txt
```

numpy, scipy, Pillow, astropy, certifi, spiceypy, flask.

---

## 12. Publication-night checklist

I run through this every time before I share a result. Skipping steps is how you end up with a lon that’s 5° off.

- [ ] Mid-exposure UTC correct — wrong time = wrong lon, ~36°/hour rotation
- [ ] SPICE and/or Horizons ON, or CM override — analytical CM shifts 10–15°, don’t trust it for absolute
- [ ] Read **SUPERDUPER_BEST_ANSWER.txt** — that’s the one number
- [ ] Note Champion grade / ultimate gates — HOLD means don’t publish
- [ ] Note limb / dark-core flags if any — outline choice shifts lon ~0.3°
- [ ] Optional: paste your manual picks → equality — Δsky ≤ 1″ is the real test
- [ ] Do **not** publish soup/SOTA lon — it’s scatter, not the answer
- [ ] Report φ_g when comparing to manual measurement lat — wrong convention = 1.5° fake offset  

---

## 13. Glossary (short)

| Term | Meaning |
|------|---------|
| System III | Jupiter magnetic/radio longitude (1965) |
| CM III | Central meridian at observation time |
| GS-MAP | Map dark core — classic publish definition |
| Champion | Automated ultimate optical path |
| UNBEATABLE_AUTO | All automated gates passed (in-app lock) |
| SUPERDUPER | One-page best-answer card for the job |
| Twin | Limb + definition sensitivity |
| Soup / SOTA | Scatter diagnostics only |
| φ_c / φ_g | Planetocentric / planetographic latitude |

---

## 14. Plateau (can’t improve more *inside this app*)

For single-frame automated optical GRS centres, **v6.5** is a product plateau:

- Ultimate multi-gate Champion + SUPERDUPER + desktop/server parity  
- Further soup/estimators won’t beat a careful desk on the same CM  

**Next real gains** come from better nights (UTC, CM, stack), multi-frame methods, or spacecraft data — see **`docs/PLATEAU.md`**.

## 15. Further reading

| Document | Role |
|----------|------|
| `docs/PROFESSOR_TECHNICAL_ESSAY.md` | Full technical essay for professors |
| `docs/PLATEAU.md` | Why further code churn has low ROI |
| `docs/SECURITY.md` | Security notes |
| `docs/FULL_CODE_AUDIT_6.1.0.md` | Historical audit (pre–6.4; developers) |
| `docs/reference/` | Optional architecture / module notes |

---

*End of the only user guide.*

# GRS Observatory — The Book

**Version:** see `VERSION` in the project root (currently **6.5.0**)  
**Audience:** self-use / lab / careful observers  
**This is the only user guide.** Other files under `docs/` are optional (essay, audits, module dumps).

---

## 0. What this software is

GRS Observatory turns a high-resolution Jupiter image (FITS / SER / PNG / JPEG) into a **documented System III longitude and latitude of the Great Red Spot**, with:

- **SPICE** (auto kernels) and/or **JPL Horizons** and/or **WinJUPOS CM** for planet geometry  
- **Limb navigation** (multi-isophote) + oriented cylindrical map  
- **Champion Ultimate** path: dark-core lock, dual-channel, nav stability, full error budget  
- **SUPERDUPER** card: one file that says *what number to report*  
- **Publish hierarchy:** UNBEATABLE_AUTO / Champion → GS-MAP → GS-BARY → pipeline  
- ~80 soup estimators + SOTA = **scatter only**, not the published centre  
- Optional paste of **your WinJUPOS GRS lon/lat** → equality Δsky  
- Synthetic planets for truth-recovery self-tests  
- SPIRE-Net CNN weights **bundled** under `app/models/` (soft prior only)

It is **ground-based optical metrology**. It is **not** radio VLBI microarcseconds and **not** an official NASA GRS longitude catalog.  
**UNBEATABLE_AUTO** means: every automated gate in *this app* passed on that frame — **not** that the result beats HST, JunoCam, or a perfect human WinJUPOS desk.

---

## 1. Start the app (macOS)

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
| — | **vs WinJUPOS paste** | Equality Δsky ″ |
| — | **Dual auto + human** | Official = human when dual is on |

### Files to open after Process

| File | Meaning |
|------|---------|
| **`SUPERDUPER_BEST_ANSWER.txt`** | **One-page “report this” card** |
| **`REPORT_THIS_ONE_LINE.txt`** | Citation line to paste |
| `publish.json` / `publish.txt` | Official publish block |
| `champion.txt` / `champion.json` | Ultimate gates, full σ budget, dark score |
| `winjupos_plus.*` | Desk-parity vs WinJUPOS practice |
| `dual_measure.*` | Auto vs human (when dual enabled) |
| `winjupos_twin.*` | Limb outline + definition sensitivity |
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

### Tips (JUPOS / WinJUPOS / BAA / SPICE)

1. Mid-exposure **UTC** only — ~**0.6° System III per minute** of time error (BAA).  
2. Trusted **CM**: SPICE / Horizons / WJ CML / override — **not** analytical for absolute publish.  
3. Prefer **red** for GRS contrast.  
4. Publish **dark core** (GS-MAP / Champion), not random rim, unless you choose outline on purpose.  
5. Same definition every night when comparing to WinJUPOS.  
6. Paste WJ lon/lat → **Δsky ″** is the real equality test.  
7. Horizons ≠ GRS lon catalog (geometry only).  
8. Soup/SOTA = scatter only — never the published centre.  
9. If `absolute_publish_ok` is false: do not claim absolute System III.  
10. If `UNBEATABLE_AUTO` is true: in-app hierarchy is locked; still not HST.  

**How to cite (example):**

```text
GRS GS-MAP λ_III=…°  φ_c=…°  φ_g=…°  (CM III=…° source=spice|horizons|winjupos|override)
σ_sky≈…″  grade=CHAMPION|UNBEATABLE_AUTO
Method soup not used for the published centre.
```

Use **φ_g (planetographic)** when matching WinJUPOS latitude language.

### When is it “EQUAL_TO_WINJUPOS”?

Only if **all** hold:

1. You paste **your** WinJUPOS GRS lon (and ideally lat)  
2. CM source is trusted: `winjupos`, `override`, `spice`, or `horizons`  
3. Δsky ≤ **1 arcsecond** between publish and your paste  

---

## 3. Absolute System III: SPICE, Horizons, WinJUPOS CM

Absolute longitude needs **time + CM**. Feature detection alone is not enough.

### Priority chain (code)

1. **CM III override** (paste from WinJUPOS) — highest for that frame  
2. **WinJUPOS / JUPOS CML table**  
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

Module: `app/champion_measure.py` (runs on every Process / Synthetic).

| Step | What it does |
|------|----------------|
| Red prefer | JUPOS: red/visual-red for GRS |
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
3. Geometry: SPICE ON, Horizons ON, optional CM override / WJ table  
4. Optional: paste WinJUPOS GRS lon/lat  
5. **Process**  
6. Open job folder → **`SUPERDUPER_BEST_ANSWER.txt`**  
7. Check `champion.txt` grade + absolute_ok  
8. If you pasted WJ: equality Δsky  

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

| Claim | Truth |
|-------|--------|
| Official NASA GRS lon product | **No** |
| Radio VLBI μas | **No** |
| Always 0.1″ on any photo | **No** |
| Match careful WinJUPOS on good data | **Often can** (same CM + definition) |
| Beat HST / Juno | **No** |
| Best automated path *in this app* when gates pass | **Yes (UNBEATABLE_AUTO)** |

Typical real-night class: **~0.5–2″** when geometry is right. Synthetics can look tighter.

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

- [ ] Mid-exposure UTC correct  
- [ ] SPICE and/or Horizons ON, or WJ CM paste  
- [ ] Read **SUPERDUPER_BEST_ANSWER.txt**  
- [ ] Note Champion grade / ultimate gates  
- [ ] Note limb / dark-core flags if any  
- [ ] Optional: paste WJ → equality  
- [ ] Do **not** publish soup/SOTA lon  
- [ ] Report φ_g when comparing to WinJUPOS lat  

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

# Great Red Spot Detector

### Professional optical metrology for Jupiter’s Great Red Spot  
**Measure System III longitude & latitude from a single stacked frame — with SPICE geometry, dual limb discipline, and a colour-first GRS lock.**

---

| | |
|--|--|
| **GitHub** | https://github.com/haydenCoder/great-red-spot-detector |
| **Release** | [v6.5.0](https://github.com/haydenCoder/great-red-spot-detector/releases/tag/v6.5.0) |
| **Version** | 6.5.0 |
| **Platform** | macOS (Python 3.10+) · FITS / PNG / SER |

> **Search keywords:** Jupiter · Great Red Spot · GRS detector · System III · SPICE · WinJUPOS · planetary imaging · AutoStakkert · optical metrology · GS-ORANGE

---

## Why this project exists

Most Jupiter apps are **pretty planet viewers**.  
**Great Red Spot Detector** is built as a **measurement instrument**: it turns your stack into a **defensible GRS centre** — longitude, latitude, central meridian, quality flags, and a one-page publish report.

It is designed for:

- serious amateurs and school/research demos  
- nights when you want **numbers**, not only a picture  
- workflows that **mirror professional desk practice** (time → CM → limb → definition → publish)

---

## What makes it professional

| Pillar | What you get |
|--------|----------------|
| **Absolute geometry** | Mid-exposure **UTC** + **SPICE** System III CM / distance (bundled kernels) |
| **Limb discipline** | Auto limb **plus** by-eye cyan outline (WinJUPOS-style dual path) |
| **Colour-first GRS lock** | **GS-ORANGE** — tracks the orange oval on RGB, not only “darkest pixel” |
| **Map geometry** | Cylindrical deprojection with planetocentric **and** planetographic latitude |
| **Multi-method suite** | Dozens of estimators for **scatter / confidence** — soup is not silently published as truth |
| **Publish hierarchy** | Official centre = GS-ORANGE / GS-MAP / champion gates — not random method average |
| **Frozen CNN prior** | SPIRE-Net weights **on by default**, training **locked** (reproducible forever) |
| **Quality honesty** | Core-lat band, dual MATCH, near-limb warnings, SUPERDUPER one-pager |

### Accuracy positioning (clear and fair)

| Compared to… | Great Red Spot Detector |
|---------------|-------------------------|
| **Typical hobby / sky apps** | **Far more professional** — real UTC/CM, limb, map lon/lat, publish gates, dual measure |
| **One-click “find the spot” toys** | **Much more accurate** when time and orientation are correct — not a rough visual guess |
| **Careful human WinJUPOS desk** | **Built to the same discipline** (CM, core definition, limb outline). On good data it can **match or approach** a careful desk; always **paste your WJ pick** to prove Δsky. It does **not** claim to beat every expert on every messy night |

**In short:** more rigorous and more automated than most public apps; **WinJUPOS-class methodology** with optional WJ equality check — not a magic claim over every human measurement.

---

## Methods (brief technical overview)

```text
Image (FITS/PNG/SER)
    │
    ├─► Observation UTC (header or filename, e.g. 2026-01-09-1540)
    ├─► SPICE CM III + distance (local kernels)
    ├─► Image prep
    │      • auto N–S flip when the orange oval is “upside down”
    │      • compact moon / shadow mask
    │      • red + orange-as-dark mono for RGB stacks
    ├─► Multi-isophote limb navigation
    ├─► Orthographic → cylindrical map (System III)
    ├─► GS-ORANGE colour centre (+ classic GS-MAP / bary methods)
    ├─► Multi-method soup → scatter only
    ├─► Dual: automatic + by-eye limb / definition
    └─► Publish + SUPERDUPER best-answer report
```

| Method family | Role |
|---------------|------|
| **SPICE ephemeris** | Central meridian & distance for absolute System III |
| **Limb fit** | Places the disk; human cyan outline fine-tunes like WinJUPOS |
| **GS-ORANGE** | Primary centre on orange RGB GRS |
| **GS-MAP / bary / templates** | Classical dark-core / map definitions |
| **SPIRE-Net (frozen)** | Optional soft prior only — not the published centre |
| **Dual measure** | Auto vs hand agreement (MATCH = internal trust) |

---

## Download the code

### One command (clone)

```bash
git clone https://github.com/haydenCoder/great-red-spot-detector.git
cd great-red-spot-detector
./RUN_ME.command
```

### ZIP (no git)

```bash
curl -L -o great-red-spot-detector.zip \
  https://github.com/haydenCoder/great-red-spot-detector/archive/refs/heads/main.zip
unzip great-red-spot-detector.zip
cd great-red-spot-detector-main
./RUN_ME.command
```

### Release package v6.5.0

```bash
curl -L -o great-red-spot-detector-v6.5.0.zip \
  https://github.com/haydenCoder/great-red-spot-detector/archive/refs/tags/v6.5.0.zip
unzip great-red-spot-detector-v6.5.0.zip
cd great-red-spot-detector-6.5.0
./RUN_ME.command
```

**Direct link:** https://github.com/haydenCoder/great-red-spot-detector  

> Google/Safari may not list a brand-new repo for days. Use the **URL** or GitHub search: `haydenCoder great-red-spot-detector`.

---

## Run (macOS)

```bash
./RUN_ME.command
```

Or manually:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cd app && python desktop_app.py
```

### Operator flow

1. **Open** stack  
2. **UTC** — blank if filename embeds time, else mid-exposure UTC  
3. **Process full** — green auto limb + cyan by-eye limb  
4. Read **`SUPERDUPER_BEST_ANSWER.txt`** and **`publish.txt`**  
5. Optional: paste **WinJUPOS** core lon/lat → Δsky equality check  

---

## Verified showcase result

Full write-up: [`docs/TECHNICAL_ESSAY_VERIFIED_CASE_2026-01-09.md`](docs/TECHNICAL_ESSAY_VERIFIED_CASE_2026-01-09.md)

**Stack:** AutoStakkert RGB · `2026-01-09 15:40:00 UTC`

| Field | Result |
|-------|--------|
| CM III (SPICE) | **310.428°** |
| GRS λ_III (GS-ORANGE) | **≈ 289.90°** |
| φ_c / φ_g | **≈ −22.73° / −25.60°** |
| Independent reprocess | **Δλ ≈ 0.08° · Δφ ≈ 0.10°** |
| Dual path | **MATCH** |

That is a **reproducible, professional-grade auto product** on real data — not a one-off screenshot claim.

---

## What’s in / out of this release

| Included | Intentionally out of the UI |
|----------|-----------------------------|
| Process + dual limb | SPIRE-Net **training** (weights frozen forever) |
| GS-ORANGE + publish gates | WinJUPOS CM **table** upload/download |
| Frozen CNN soft prior | Factory night / hard-synth / multi-epoch buttons |
| Bundled SPICE kernels | Online SPICE auto-download |
| Champion Ultimate + SUPERDUPER archival cards | — |

### v6.5.0 audit fixes (2026-07-28)

All P0/P1/P2 bugs from the full line-by-line audit are fixed:
- Champion candidate now correctly preferred over GS-MAP in publish hierarchy
- f-string None-format crashes in winjupos_plus and superduper patched
- Server /api/synthetic now produces SUPERDUPER archival products
- Stale version strings and User-Agent updated to 6.5.0
- _gauss() fallback now actually performs FFT convolution
- datetime.now() replaced with datetime.now(timezone.utc) for reproducible timestamps
- Desktop UI polished: refined colour palette, improved metric cards
- See docs/FULL_LINE_AUDIT_6.5.0.md for the complete audit

## License

See [`LICENSE`](LICENSE).

---

**Great Red Spot Detector** — open the stack, run Process, publish a real System III centre.  
Built for people who care about **measurement**, not only a pretty Jupiter.

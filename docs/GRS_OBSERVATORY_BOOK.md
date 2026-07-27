# Great Red Spot Detector — user guide

**Version:** see `VERSION` in the project root (currently **6.5.0**)  
**Audience:** people reducing their own Jupiter stacks  
**This is the main user guide.** Other files under `docs/` are optional (essays, audits, module notes).

---

## 0. What this is

You open a high-resolution Jupiter stack (FITS / SER / PNG / JPEG) and get a **documented System III longitude and latitude** of the Great Red Spot, plus a short report.

Typical path:

1. Mid-exposure **UTC** (header or filename like `2026-01-09-1540_…`)
2. Planet geometry from **SPICE** (bundled kernels) and/or **Horizons**
3. **Limb** fit — auto (green) + by eye (cyan)
4. Simple cylindrical map and a centre (often **GS-ORANGE** on RGB, or **GS-MAP** dark core)
5. A **publish** block and a one-page **best-answer** card in the job folder

Also available:

- Paste your **WinJUPOS** core lon/lat → Δsky check  
- Synthetic planets for pipeline self-tests  
- SPIRE-Net CNN weights under `app/models/` (soft prior only; **training is off** in the process-focused build)

This is **ground-based optical measurement**. It is not radio interferometry and not a NASA “official GRS of the night” catalogue.  
When the app says quality gates passed (`unbeatable_auto`), it means **this app’s** checks passed on that frame — not that the number beats HST, Juno, or a careful human WinJUPOS desk.

---

## 1. Start the app (macOS)

### From source

```bash
cd /path/to/great-red-spot-detector   # or your local folder
./RUN_ME.command
```

or:

```bash
./Launch_Desktop.command
```

### Optional local web UI

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

## 2. What number to trust

| Priority | Product | Use for |
|----------|---------|---------|
| **1** | **Publish / best-answer card** | **Report this** |
| **1a** | Quality gates all passed (`unbeatable_auto`) | In-app lock: weaker methods do not override |
| **1b** | Champion / GS-MAP / GS-ORANGE path | Main automated optical centre |
| **2** | GS-BARY or pipeline fallback | Secondary |
| — | Multi-method consensus / “soup” | Scatter / confidence **only** |
| — | W/E edges | Size (EW extent), not “the” lon |
| — | vs WinJUPOS paste | Equality Δsky ″ |
| — | Dual auto + hand | Prefer hand limb when dual is on |

### Files after Process

| File | Meaning |
|------|---------|
| **`SUPERDUPER_BEST_ANSWER.txt`** | One-page “report this” card (filename kept for compatibility) |
| **`REPORT_THIS_ONE_LINE.txt`** | Citation line to paste |
| `publish.json` / `publish.txt` | Official publish block |
| `champion.txt` / `champion.json` | Gates, σ budget, dark score |
| `dual_measure.*` | Auto vs hand (when dual enabled) |
| `pro_ephemeris.*` | CM + distance + provenance |
| `FULL_REPORT` | Long human text report |

### Process = auto limb + by-eye limb

**▶ Process full (auto limb + by-eye limb)** typically:

1. Opens the **limb window** — **GREEN** auto, **CYAN** by eye  
2. Runs geometry, map, centre, publish, best-answer card  
3. Writes `publish.*`, `champion.*`, `SUPERDUPER_BEST_ANSWER.*`, `dual_measure.*`  

**Keys:** arrows move · PgUp/PgDn size · R reset cyan→green · drag centre  

### Practical tips

1. Mid-exposure **UTC** only — ~**0.6° System III per minute** of time error (BAA).  
2. Trusted **CM**: SPICE / Horizons / WJ CML / override.  
3. Prefer **red** (or orange lock on RGB) for GRS contrast.  
4. Publish a **core** definition, not a random rim, unless you chose outline on purpose.  
5. Same definition every night when comparing to WinJUPOS.  
6. Paste WJ lon/lat → **Δsky ″** is the real equality test.  
7. Horizons ≠ GRS lon catalogue (geometry only).  
8. Soup / consensus = scatter only — never the published centre.  
9. If `absolute_publish_ok` is false: do not claim absolute System III.  
10. If gates all pass: in-app hierarchy is locked; still not HST.

**How to cite (example):**

```text
GRS GS-ORANGE λ_III=…°  φ_c=…°  φ_g=…°  (CM III=…° source=spice|horizons|winjupos|override)
```

---

## 3. Verified example

See [`TECHNICAL_ESSAY_VERIFIED_CASE_2026-01-09.md`](TECHNICAL_ESSAY_VERIFIED_CASE_2026-01-09.md) for a full write-up of a real AutoStakkert RGB night (2026-01-09 15:40 UTC): λ_III ≈ 289.9°, φ_g ≈ −25.6°.

---

## 4. What is intentionally simple

- No train UI on the desktop process path (weights frozen)  
- No online SPICE download required (kernels in repo)  
- Optional web UI still has advanced tools; normal use is **Process**  

Questions: open a discussion or issue on the GitHub repo.

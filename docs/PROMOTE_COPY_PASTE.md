# Short posts you can paste (keep it human)

Project: **Great Red Spot Detector**  
https://github.com/haydenCoder/great-red-spot-detector

```bash
git clone https://github.com/haydenCoder/great-red-spot-detector.git
cd great-red-spot-detector
./RUN_ME.command
```

Post as yourself. One or two places is enough at first.

---

## X / short caption

```
I made a small open-source tool to measure Jupiter’s Great Red Spot from a stacked FITS/PNG.

It uses SPICE for CM III, auto+hand limb fit, and a colour lock for orange GRS on RGB (GS-ORANGE). Not a sky-viewer — it writes a publish report.

Example on a real AutoStakkert stack (2026-01-09 15:40 UTC): λ_III≈289.9°, φ_g≈−25.6°.

https://github.com/haydenCoder/great-red-spot-detector
```

---

## Cloudy Nights / SGL / IceInSpace

**Title:** Open-source tool for GRS System III lon/lat from one stack

```
Hi,

I put up a process-focused tool for measuring the Great Red Spot from a single stack (FITS/PNG/SER):

https://github.com/haydenCoder/great-red-spot-detector

Rough pipeline:
• mid-exposure UTC (filename works when AutoStakkert left DATE-OBS empty)
• SPICE CM III + distance (kernels in the repo)
• limb fit with optional hand adjust (green/cyan)
• GS-ORANGE for RGB when the spot is orange, not only dark-core methods
• multi-method suite kept as scatter; publish path is explicit
• CNN weights included but frozen (no train UI)

On one AutoStakkert RGB night (2026-01-09 15:40 UTC) I got about:
CM III 310.43°, GRS λ_III 289.90°, φ_g −25.60°. A second independent run was within ~0.1°.

This is ordinary optical metrology, not a NASA GRS catalogue. If you try it, I’d value a WinJUPOS core comparison on the same frame.

Install on macOS:
git clone https://github.com/haydenCoder/great-red-spot-detector.git
cd great-red-spot-detector && ./RUN_ME.command

Essay with the case study:
docs/TECHNICAL_ESSAY_VERIFIED_CASE_2026-01-09.md

Thanks for any feedback.
```

---

## Reddit (r/Astronomy or r/telescopes)

**Title:** Open-source app to measure Jupiter GRS lon/lat from your stack

```
I wrote a small Python app that measures the Great Red Spot (System III lon + lat) from one AutoStakkert-style stack.

https://github.com/haydenCoder/great-red-spot-detector

It uses SPICE for geometry, dual limb (auto + hand), and a colour-based centre (GS-ORANGE) so orange GRS on RGB is less likely to lose to dark belts/moon. Training UI is off; weights are fixed.

One real-night example (2026-01-09 15:40 UTC): λ_III≈289.9°, φ_g≈−25.6°, CM III≈310.4°. Independent reprocess matched ~0.1°.

Not claiming to replace a careful WinJUPOS desk — you can paste WJ lon/lat for a Δsky check. Happy to hear what breaks on your data.
```

---

## Show HN

**Title:** Show HN: Great Red Spot Detector – GRS lon/lat from amateur Jupiter stacks

```
https://github.com/haydenCoder/great-red-spot-detector

Python app for measuring Jupiter’s Great Red Spot from a single stack: SPICE System III geometry, limb navigation, cylindrical map, colour-first centre on RGB, dual auto/hand limb, and a written publish report. Neural weights ship frozen.

Case study on a real AutoStakkert RGB frame (2026-01-09 15:40 UTC) in the docs. Interested in feedback from people who already use WinJUPOS.
```

---

## Email to a teacher

```
Subject: Great Red Spot Detector project

Hi [Name],

I published a small open-source project that measures Jupiter’s Great Red Spot position (System III longitude and latitude) from one telescope stack:

https://github.com/haydenCoder/great-red-spot-detector

It uses SPICE geometry and a dual limb step. There is a short technical write-up with a verified night in the docs folder. I can demo it if useful for class.

Thanks,
[Your name]
```

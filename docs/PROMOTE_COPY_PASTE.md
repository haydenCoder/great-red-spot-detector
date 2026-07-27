# Promotion posts — copy & paste

**Project:** Great Red Spot Detector  
**URL:** https://github.com/haydenCoder/great-red-spot-detector  
**Clone:**
```bash
git clone https://github.com/haydenCoder/great-red-spot-detector.git
cd great-red-spot-detector
./RUN_ME.command
```

Post **as yourself**. Do not spam the same text to every subreddit the same minute (accounts get filtered). Pick 2–3 places first.

---

## 1) Short (X / Twitter / Instagram caption)

```
Great Red Spot Detector — open-source optical metrology for Jupiter’s GRS.

Measure System III lon/lat from your AutoStakkert stack: SPICE CM, dual limb (auto + by-eye), GS-ORANGE colour lock, frozen CNN prior, publish report.

Verified case 2026-01-09 15:40 UTC → λ_III≈289.9°, φ_g≈−25.6°

https://github.com/haydenCoder/great-red-spot-detector

#Jupiter #GreatRedSpot #Astrophotography #WinJUPOS #Astronomy
```

---

## 2) Medium (Cloudy Nights / Stargazers Lounge / IceInSpace)

**Title:** Open-source “Great Red Spot Detector” — System III lon/lat from a single stack (SPICE + dual limb + GS-ORANGE)

```
Hi all,

I released a process-focused open-source tool for measuring Jupiter’s Great Red Spot from one stacked frame (FITS/PNG/SER):

https://github.com/haydenCoder/great-red-spot-detector

What it does
• Mid-exposure UTC (filename if AutoStakkert header has no DATE-OBS)
• SPICE System III CM + distance (kernels bundled)
• Dual limb: automatic + by-eye cyan outline (WinJUPOS-style discipline)
• GS-ORANGE colour centre for RGB stacks (orange oval, not only dark-core)
• Multi-method scatter as confidence only — publish hierarchy is explicit
• Frozen SPIRE-Net weights as optional soft prior (training disabled)
• One-page SUPERDUPER / publish report under outputs/

Verified on a real AutoStakkert RGB stack (2026-01-09 15:40 UTC):
• CM III ≈ 310.43° (SPICE)
• GRS λ_III ≈ 289.90° (GS-ORANGE)
• φ_c / φ_g ≈ −22.73° / −25.60°
• Independent reprocess agreed to ~0.1°

Honest scope: ground-based optical metrology. Not a NASA GRS catalog. Paste your WinJUPOS core pick for Δsky equality.

Install:
git clone https://github.com/haydenCoder/great-red-spot-detector.git
cd great-red-spot-detector
./RUN_ME.command

Technical essay: docs/TECHNICAL_ESSAY_VERIFIED_CASE_2026-01-09.md

Feedback and WJ comparisons welcome — thanks.
```

**Where to post (you log in):**
- https://www.cloudynights.com/forum/71-solar-system-imaging-planetary/
- https://stargazerslounge.com/forum/78-solar-system/
- https://www.iceinspace.com.au/forum/forumdisplay.php?f=36 (planetary)

---

## 3) Reddit

### r/Astronomy or r/telescopes (choose one first)

**Title:** Free open-source tool to measure Jupiter’s Great Red Spot lon/lat from your stack

```
I built Great Red Spot Detector — measures System III longitude/latitude from one AutoStakkert-style stack.

Repo: https://github.com/haydenCoder/great-red-spot-detector

Stack: SPICE CM, dual auto/hand limb, GS-ORANGE colour lock (works when GRS is orange, not only dark), frozen CNN soft prior, publish report.

Example night (2026-01-09 15:40 UTC): λ_III≈289.9°, φ_g≈−25.6°, CM III≈310.4°. Independent reprocess matched ~0.1°.

Not a planet wallpaper app — actual metrology with quality gates. WinJUPOS paste supported for Δsky check.

macOS: git clone … then ./RUN_ME.command
```

**Also try (after a day, reword slightly):**
- r/astrophotography  
- r/AskAstronomy (as “feedback on method?” if rules allow)  
- r/Python (as astronomy project showcase)

Read each sub’s rules (no pure spam; include method + honesty).

---

## 4) Hacker News “Show HN”

**Title:** Show HN: Great Red Spot Detector – measure Jupiter GRS lon/lat from amateur stacks

```
https://github.com/haydenCoder/great-red-spot-detector

Python app for optical metrology of Jupiter’s Great Red Spot: SPICE System III geometry, multi-isophote limb, cylindrical map, colour-first GS-ORANGE centre, dual auto/human limb, publish hierarchy with formal gates. SPIRE-Net weights ship frozen (inference only).

Verified case on real AutoStakkert RGB: λ_III≈289.90°, φ_g≈−25.60°, independent reprocess Δ≈0.1°.

Happy to discuss geometry, failure modes (wrong UTC, N–S flip, dark vs orange locks), and WinJUPOS comparison.
```

Post at: https://news.ycombinator.com/submit

---

## 5) School / teacher email (short)

```
Subject: Great Red Spot Detector — open-source Jupiter measurement project

Hi [Name],

I published an open-source project that measures Jupiter’s Great Red Spot (System III longitude and latitude) from a single telescope stack:

https://github.com/haydenCoder/great-red-spot-detector

It uses SPICE geometry, dual limb fitting, and a colour-based GRS centre (GS-ORANGE). I also wrote a technical essay with a verified case (2026-01-09) and independent reprocess checks.

Happy to demo or share results for class.

Thanks,
[Your name]
```

---

## 6) LinkedIn / portfolio blurb

```
Released Great Red Spot Detector — open-source optical metrology for Jupiter’s GRS.

• System III lon/lat from FITS/PNG stacks
• SPICE CM + dual limb + GS-ORANGE colour lock
• Frozen neural soft prior; training locked for reproducibility
• Verified case with independent reprocess (~0.1° agreement)

GitHub: https://github.com/haydenCoder/great-red-spot-detector
```

---

## Checklist

- [x] GitHub topics added  
- [x] Repo discussion #1  
- [ ] Cloudy Nights (you post)  
- [ ] One Reddit community (you post)  
- [ ] Show HN (optional)  
- [ ] Star your own repo + share URL with friends  

**Remember:** Google will not list you for days. Direct links + forums matter more than Safari search.

# Student Coursework Report — Great Red Spot Detector

**Author:** Hayden Coder (undergraduate, astrophysics)
**Course:** Observational Astronomy / Planetary Imaging
**Project:** Great Red Spot Detector (GRS Observatory)
**Version referenced:** 7.0.1 (+ deterioration audit, 2026-08-22)
**Date:** 2026-08-22

---

## 1. Project abstract

This project is a Python-based ground-based optical metrology tool for
Jupiter's Great Red Spot (GRS). It takes a stacked image of Jupiter — from
AutoStakkert!, Siril, or any pipeline that produces a single good frame
— and returns the GRS position as System III longitude and latitude,
together with a fully documented uncertainty budget, a publishable "what
to report" card, and a head-to-head comparison against a manually-pasted
WinJUPOS pick.

The project was motivated by a single frustration: as an amateur
astrophotographer I could *see* the GRS move over the course of an hour,
but I had no reproducible way to put a number on its position that I
could defend in a written report. WinJUPOS is excellent for the manual
case but requires hand-picking the outline on every frame, and the
result depends on the operator's eyes. This tool automates the manual
discipline without losing the rigor that makes it a measurement rather
than an image.

## 2. Astrophysical background

Jupiter rotates once every **9h 55m 33s** in the System III radio
reference frame (the IAU-adopted period since 1965). At the GRS latitude
(roughly -22° planetographic) one rotation corresponds to ~50,000 km of
linear motion, and at opposition a single second of clock error produces
~0.0084° of longitude error. The System III central meridian
(CM III or CML III) is the sub-observer longitude at the moment of
observation, and the GRS longitude is reported as a value in the same
frame.

The shape of the planet is an oblate spheroid with equatorial radius
71,492 km, polar radius 66,854 km, and a flattening of 0.0649 — small
but not negligible. **Latitude on Jupiter comes in two flavours:
planetocentric (φ_c, measured from the centre) and planetographic
(φ_g, measured from the local surface normal).** They differ by ~2.6°
at the GRS latitude, so they must be quoted correctly when comparing
to WinJUPOS output.

The image of Jupiter is observed through a long atmospheric path. Two
effects matter at the level of precision we care about:

1. **Refraction.** The atmosphere refracts light, and the amount of
   refraction depends on wavelength. The refractive index of air at
   standard conditions follows the **Edlén (1966)** formula:
   `(n-1)_s = 8342.13 / (2406143 + 130/σ² + 0.5999/σ⁴) × 10⁻⁸`
   where σ = 1/λ in μm. At 550 nm, n − 1 ≈ 2.78 × 10⁻⁴, and the
   difference between 445 nm and 658 nm is about 1.5 × 10⁻⁵.
2. **Differential refraction (DCR).** The wavelength-dependence of
   refraction means that the R and B channels of an RGB image are
   vertically offset on the detector by
   `Δy = (n_R − n_B) tan(z) / plate_scale`
   where z is the zenith distance. For z = 30° and a 4K image of
   Jupiter at opposition, this is ~10–25 px — a real and systematic
   effect that must be corrected before sub-pixel phase correlation
   can succeed.

## 3. Measurement geometry

The "limb" of Jupiter is the visible disk boundary. Fitting it gives
three parameters: the planet's centre (x_c, y_c) in image pixels, and
its equatorial radius `a_eq_px` (in pixels). The polar radius
`b_pol_px = a_eq_px × (1 − f)` follows from the flattening.

The pixel-to-(longitude, latitude) transform is the inverse of the
orthographic projection on the oblate spheroid:

1. **Un-scale** the pixel (x, y) by the single equatorial plate scale
   a_eq_px (this is isotropic — both x and y are divided by the
   *equatorial* scale, not by two different scales).
2. **Undo the North position angle** (the rotation of the planet on
   the sky) by applying the rotation matrix R(−PA).
3. **Intersect the line of sight with the spheroid.** This requires
   solving a quadratic in the body-frame z-coordinate:
   `A·t² + B·t + C = 0`
   with A = cos²D + sin²D / k², B = 2Y_p sinD cosD (1/k² − 1), and
   C = X_p² + Y_p² (cos²D / k² + sin²D) − 1.
   The larger root is the near-side intersection.

The result is the **planetocentric** latitude directly (not the
parametric latitude that the simpler "asin(y/b_pol)" shortcut returns;
that shortcut is wrong by up to 1.7° at the GRS).

## 4. Measurement pipeline

The measurement has four independent estimators that vote on the GRS
position:

| Method | What it locks onto | Best case | Worst case |
|---|---|---|---|
| Template match | Dark elliptical oval | 0.2° | 1° (decoy SEB oval) |
| Map dark centroid | Inverted median residual | 0.3° | 5° (barge) |
| Moment mask | Intensity-weighted dark patch | 0.1° | 2° (thin filament) |
| Redness (R−B colour) | Red-brown oval, not just dark | 0.1° | 1° (faint GRS) |

The colour (redness) estimator is the most robust to seeing because
colour survives the blur that destroys the dark-oval shape. The
template is the most reliable *dark* estimator but can lock onto
decoy SEB ovals. The moment mask integrates the whole dark region and
is the most accurate estimator when it agrees with the template.

The consensus logic:
1. Reject any estimator that locks outside the GRS latitude band
   ([-32°, -12°] planetocentric).
2. Drop outliers from a cluster that disagree by more than 18°.
3. If the dark methods split badly (>12°), seed the cluster on the
   redness lock (colour survives blur).
4. If the template is high-contrast and corroborates another method
   to within 8°, blend its longitude with the moment centroid and the
   redness lock with weights (template, moment, redness) = (1 −
   LON_MOMENT_WEIGHT, LON_MOMENT_WEIGHT, LON_REDNESS_WEIGHT) =
   (0.5, 0.5, 3.5).
5. Take latitude from the moment mask (unbiased) when it agrees
   with the template to within 3°.

The weights above were measured on synthetic planted-centre truth over
~1000 cases (DEEP_AUDIT_6.6.1.md). The 3.5 weight on the redness
lock for longitude is the key tuning: it folded the residual
~1° shared template + moment bias into the answer, taking the worst
clear-data case from 0.69° to 0.43°.

## 5. Ephemeris and CM III

The CM III (central meridian longitude) is the absolute tie between
your measurement and System III. Without good CM, you only have a
relative longitude; with good CM, you have a publishable number.

The ephemeris resolver tries sources in priority order:
1. **Manual override** (you paste a CM, distance, etc.)
2. **WinJUPOS / JUPOS CSV table** at the requested epoch
3. **SPICE auto** (uses the NAIF de440s planetary SPK + IAU_JUPITER
   PCK to compute distance, light-time, and the body-frame
   sub-observer lon/lat)
4. **JPL Horizons full observer parse** (sub-obs lon/lat, NP.ang,
   light-time)
5. **Analytical fallback** (differentials robust; absolute CM may
   have a zero-point offset)

The shipped SPICE kernels live under `app/ephemeris_data/spice/`
(naif0012.tls, pck00011.tpc, gm_de440.tpc, de440s.bsp). Online
download is disabled by default in the production release — the
release ships the kernels.

## 6. The 340-case validation campaign

Two pinned test campaigns, run on synthetic planted-centre frames,
score the full pipeline against truth:

| Campaign | Cases | Truth source | Headline result |
|---|---|---|---|
| Resolution × seeing | 100 | planted geometric centre | 100% within 1°, sky median 0.117″ |
| Real ephemeris | 240 | published GRS lon + literature lat | 100% within 1°, all clear/mild <0.5° |

Total: **340 cases, 100% within 1°, every clear/mild frame under
0.5°**. The full audit is in `docs/AUDIT_MASTER_6.6.0.md`. The
fine-tuning to a tiered sub-0.2° on clear data is in
`docs/DEEP_AUDIT_6.6.1.md`.

### Deterioration sweep (added 2026-08-22)

I added a `Deterioration Lab` that sweeps resolution, seeing and noise
across synthetic Jupiter frames and measures the engine on each cell
(`app/deterioration_lab.py`, new browser tab). A Quick sweep (2
resolutions x 8 seeing tiers x 2 seeds, ~80 s) gives a real error floor:

| Disk | sub-1° breaks at | sub-0.5° holds to |
|---|---:|---:|
| 540p | ~1.2 arcsec seeing | ~0.8 arcsec |
| 720p | ~4.0 arcsec seeing | ~2.4 arcsec |

i.e. plate scale matters more than I had assumed — a 720p disk keeps a
usable lock through seeing that completely breaks a 540p disk. The
per-method breakdown on the same sweep (median |dLon|) was moment 0.09°,
redness 0.29°, template 0.43°, map-dark 80.8°, which is a concrete
demonstration of why the publish path leans on the colour/moment vote
and rejects isolated dark locks. While chasing that result I found and
fixed six defects the default D=P=0 synthetic campaigns could not see
(dead feature-verification gate, RGB frame scoring, RGB disk masks,
wrong-projection injection bias, a 2.8° sphere-vs-spheroid latitude error
in the per-latitude stacker, and an unregularised derotator that stacked
*worse* than the naive mean); the before/after numbers are in
`docs/DETERIORATION_AUDIT_2026-08-22.md`. All six are pinned by
regression tests.

**Important honest framing:** the real-ephemeris campaign uses
*synthetic pixels* planted at the *real* published GRS longitude for
each epoch. The GRS lon model is the cited Hubble GO17275 / Simon+2018
drift series, but the frame around it is generated. The reason: a
real JPEG of Jupiter does not have a mid-exposure UTC, so absolute
longitude is unmeasurable on it. The pixels are not real photos, but
the truth they are scored against is real.

## 7. UI design

The desktop UI is a single Tkinter window with a sidebar of action
buttons, a centre panel that switches between "Preview" (the
measurement plot), "Dashboard" (the key result cards), "Report" (the
human-readable FULL_REPORT.txt), and "Log" (the live console). The
metric strip at the top shows: Grade, Lon, Lat, σ, Truth (vs WinJUPOS
sky error if you pasted one), and CM source.

The UI went through several iterations. The first version was a
dark theme that looked cool but was hard to read. The current macOS-
inspired light theme with per-card accent colours was chosen because
the key metrics need to be readable at a glance.

## 8. Honest scope

This tool does **not** replace a careful WinJUPOS session. On messy
nights with poor seeing, it cannot do better than a careful desk. It
automates the *discipline* of a careful WinJUPOS session: time → CM
→ limb → definition → publish, all in one click. The way to validate
it against your own workflow is to **paste your WinJUPOS lon/lat
into the dual-limb dialog** — the tool will report the Δsky in
arcseconds. If Δsky < 1″, the tool agrees with your hand measurement
and you can cite it. If Δsky is large, the tool's report is telling
you that you should look at the data more carefully, not that the
tool is wrong.

**UNBEATABLE_AUTO** means every automated gate in *this app* passed
on that frame. It is *not* a claim that the result beats professional
observatories, JunoCam, or a perfect human manual desk. It is an
in-app lock only.

## 9. Coursework learning outcomes

In building this project I learned:
- The difference between planetocentric and planetographic latitude
  (and why the difference matters at the GRS).
- That a small oblateness change in the geometry can shift absolute
  longitude by tenths of a degree.
- How to read a SPICE kernel manifest and load NAIF kernels
  programmatically.
- The importance of the limb outline choice in the disk-fit step —
  even a 2% isophote-level change moves the measured disk radius
  enough to shift the published lon by ~0.1°.
- How to write a Monte Carlo that re-maps the cylindrical map each
  trial with perturbed limb nav, capturing *geometric* uncertainty
  rather than just map noise.
- The value of multiple independent estimators with explicit
  consensus logic, and the dangers of over-trusting any single
  estimator under poor seeing.
- Writing a deterioration sweep instead of trusting a single
  "it works on my frame" result: plotting accuracy against seeing
  and plate scale showed the sub-1° guarantee breaks at ~1.2″ on a
  540p disk but holds to ~4″ on 720p, which I would never have
  seen from one test image.
- That "the tests pass" is not enough — six real defects (a dead
  feature-verification gate, RGB frame scoring, projection bias in
  the injection calibration, a latitude error in the stacker, and an
  unregularised derotator) only showed up when I ran the code on
  colour frames and oriented/sheared data rather than the default
  synthetic.

## 10. References

1. Edlén, B. (1966). The refractive index of air. *Metrologia* 2, 71.
2. Noll, R. J. (1976). Zernike polynomials and atmospheric turbulence.
   *JOSA* 66, 207.
3. Archinal, B. A. et al. (2018). Report of the IAU/IAG Working
   Group on Cartographic Coordinates and Rotational Elements.
   *Celestial Mechanics and Dynamical Astronomy* 130, 22.
4. Simon, A. A. et al. (2018). Jupiter's Great Red Spot: who's
   zooming whom? *American Astronomical Society* DPS meeting #50.
5. de Pater, I. & Lissauer, J. J. (2015). *Planetary Sciences*
   (Cambridge University Press).
6. WinJUPOS users' manual (Grapenthin, 2018).
7. Acton, C. H. (1996). Ancillary data services of NASA's Navigation
   and Ancillary Information Facility. *Planetary and Space Science*
   44, 65. (SPICE)
8. Giorgini, J. D. et al. (1996). JPL's On-Line Solar System Data
   Service. *Bulletin of the AAS* 28, 1158. (Horizons)

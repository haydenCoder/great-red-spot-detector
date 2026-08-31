# Walking Through the Great Red Spot Detector: the Whole Code, One Topic at a Time

This document is a guided tour of the entire codebase. It is written the way I would want someone to explain a project to me. Start with the problem. Then follow the data from the moment a file arrives to the moment a number gets published. Stop at every module along the way, and say what it actually does and why it exists. Nothing here is marketing. Where the code is clever, I will say so. Where it is a compromise, I will say that too. A codebase is a record of decisions, and the compromises are usually the interesting part.

The project is called the Great Red Spot Detector, and its whole purpose fits in one sentence. Measure the position of Jupiter's Great Red Spot on a photograph. Turn that measurement into a longitude and latitude in the coordinate system the amateur and professional planetary community uses. Add a realistic estimate of how uncertain the number is. About sixty thousand lines of Python exist to make that sentence true under real-world conditions. Bad seeing. Unknown exposure times. Mirrored images. Videos instead of stills. Missing SPICE kernels. Laptop memory limits. And the fact that the spot is not a clean black ellipse — it is a smeared orange storm whose darkest core is not even at its centre.

The essay is long, so it is organized in sections you can read independently. Section 1 explains the astronomy and the coordinate math that everything depends on. Section 2 maps the repository. Sections 3 through 12 follow the pipeline in order. Section 13 covers the neural network. Section 14 covers the synthetic generator that makes ground truth possible. Section 15 covers the test suite and the audit tools. Section 16 covers the auxiliary machinery: accounts, licensing, security, paths, memory management. The last two sections are a plain-language limitations list and a glossary of the names you will meet in the code.

## 1. The Problem: What It Actually Means to Measure the Great Red Spot

Before reading a single line of code it is worth being precise about the science, because almost every design decision in this repository follows from one of these facts.

### 1.1 The spot is not at a fixed position

The Great Red Spot is an anticyclone that has existed for at least a century and a half. It is not fixed to a particular longitude. It drifts relative to the planet's interior rotation, and that drift is exactly what makes it scientifically interesting: the spot is embedded in a jet stream, and its motion is a measurement of the wind at its latitude. Longitude measured once is trivia; longitude measured over months is geophysics. That is why this codebase contains a drift module, a multi-epoch module, and a JUPOS export.

### 1.2 Longitude needs a reference frame

A latitude is easy: the spot sits at about 22.4 degrees south of Jupiter's equator, in the planetographic convention, and the belts pin it there. Longitude is harder, because to say "the spot is at 15 degrees" you have to say "15 degrees relative to what?". The community standard is System III, a frame tied to the planet's radio rotation, which is very close to but not exactly the same as the rotation of the visible clouds. System III has a period of 9 hours, 55 minutes, 29.711 seconds. The frame rotates at 360 degrees per that period, which works out to about 36 degrees per hour, or about 0.6 degrees per minute of time.

That last number is the single most important constant in the whole project. It means an error of one minute in the observation time produces an error of about 0.6 degrees in the reported longitude. A 30-second error produces 0.3 degrees. So the pipeline treats the observation time as a first-class measurement with its own uncertainty, and it refuses to guess.

### 1.3 The central meridian converts time into longitude

Because Jupiter rotates, there is always a unique longitude on the planet that faces the observer at any instant: the central meridian, usually written CM. If you know the UTC of the exposure and you have a good ephemeris, you can compute the CM at that instant. Then the spot's longitude is simply the CM plus the spot's angular offset from the disk centre, measured along the planet's equator. This is the core arithmetic of the whole pipeline:

    lon_system3 = cm_iii + lon_rel_to_cm

and the whole difficulty is in computing each of the two terms well. The CM term needs trustworthy time and ephemeris. The offset term needs a good measurement of where the spot is on the image, which needs a good model of the disk, which needs the limb, which needs to be fit from the image itself.

### 1.4 Planetographic versus planetocentric latitude

Jupiter is an oblate spheroid: its equatorial radius (71,492 km) is about 6.5 percent larger than its polar radius. Because of that, there are two reasonable ways to quote latitude. Planetographic latitude is the angle you would measure with a protractor on the planet's surface, where the local vertical points; planetocentric latitude is the angle from the equator as seen from the planet's centre. They differ by up to a few degrees at the spot's latitude: 22.4 degrees planetographic is about 19.82 degrees planetocentric. The literature quotes the spot at -22.4 planetographic. This codebase works internally in planetocentric, because the projection math is cleaner that way, and converts at the boundary where reports are written. Getting this wrong was a real bug once — the synthetic renderer used to plant the spot at -22.0 planetocentric, which put it about 2.2 degrees away from where the measurement engine looks for it, and the error masqueraded as measurement bias.

### 1.5 The limb is a reference you can measure internally

To convert pixels on the image into angles on the planet you need to know where the disk is and how big it is: the centre and the equatorial radius in pixels. The limb — the bright edge of the planet where the line of sight grazes the atmosphere — gives you that. The code shines many rays outward from an initial guess, finds where the intensity crosses the limb's isophote, and iterates so the fit is stable and sub-pixel. This is done from the image itself, so no external calibration is required.

### 1.6 Seeing destroys shape before it destroys colour

Atmospheric turbulence blurs the image. On a poor night the dark oval's shape is smeared beyond recognition and template matching locks onto the wrong feature — a dark belt segment, a barge, a moon shadow. What survives blur much better is colour: the spot is redder than its surroundings, and redness is an integrated property that a Gaussian-ish point spread function preserves far better than a sharp edge. This is why the engine keeps a redness-based estimator as an independent vote and refuses to let the dark methods outvote it when they disagree badly.

### 1.7 Absolute accuracy needs a trusted CM source

The analytical calculation of the CM from the rotation period is only good to tens of degrees because the fine details of the radio rotation and the observer's position matter. For absolute publishable longitude the code uses, in order of preference: SPICE kernels (planetary ephemeris files that give exact geometry), the JPL Horizons web service, a WinJUPOS central-meridian table pasted by the user, and only then the analytical model — and when only the analytical model is available, the reported uncertainty reflects that.

## 2. The Repository at a Glance

The repository root holds the version file, the README, the project map, a changelog, the requirements file, the run-first shell script for macOS, and the SPIRE-Net training launcher. The code itself lives in four places.

`app/` is where almost everything happens. It contains 76 Python modules. The largest is `grs_complete_system.py`, an inherited legacy pipeline with its own complete stack — ingestion, quality control, calibration, stacking, derotation, restoration, navigation, measurement, bootstrap error estimation, and report generation — that the newer code still calls for video handling and that a large number of smaller, more focused modules were written to replace or extend. The second largest is `desktop_app.py`, the Tkinter desktop interface, which is mostly presentation and wiring rather than science. The scientific core in the modern sense is `precision_engine.py`, the measurement engine, plus the layers built around it: `accuracy_gates.py` for publication discipline, `gold_standard.py` for named measurement definitions, `champion_measure.py` for the strongest automated path, `publish_primary.py` for deciding the official answer, `research_grade.py` and `vlbi_metrology.py` for the advanced error-budget layers, and `all_methods.py` plus `all_methods_extra.py` for the large catalog of independent estimators.

`app/` also contains the image side: video readers and writers (`ser_io.py`), an AutoStakkert-style stacker (`ap_stacker.py`), planet-generalised stackers and derotators (`planetary_stacker.py`, `planetary_derotator.py`), a Jupiter-specific zonal stacker and derotator, optical-flow warping (`flow_warp.py`), exact sub-pixel translation (`image_warp.py`), frame quality assessment (`frame_quality.py`), sharpening (`sharpen_lab.py`), differential dispersion correction (`dcr.py`), RGB compositing from filtered sequences (`rgb_combine.py`, `filter_wheel.py`), and GIF animation (`animation.py`).

The astronomy side is in `spice_auto.py` (kernel discovery and download), `ephemeris_pro.py` (the professional geometry resolver), `nasa_compare.py` (Horizons geometry for comparison), `grs_ephemeris_truth.py` (the drift model used as synthetic ground truth), `transits.py` (transit and visibility planning), `grs_drift.py` (time-series drift analysis), `multi_epoch.py` (differential tracking across nights), and `jupos_io.py` (community database import and export).

The learning side is in `nn_grs.py` (SPIRE-Net, a small convolutional network), `ai_hard_cases.py` (a gated helper that only intervenes when the classical methods disagree badly), `holy_hybrid_stacker.py` (a stacker that combines a learned per-patch quality model with a physics prior), and `spire_finetune.py` (the training entry point that is frozen in production).

The interface side is `cli.py`, `desktop_app.py`, `server.py` with its templates and static assets, `product_core.py` (the product identity and certification entry), `observatory_pipeline.py` (the one-call video-to-answer path), and `desktop_pipeline.py` (the shared processing orchestration used by both the desktop app and the server so the two cannot diverge).

Supporting plumbing lives in `paths.py` (cross-platform path resolution), `verbose_log.py` (thread-safe logging), `netutil.py` (the single shared SSL context and array digest used by the three network modules), `ram_ssd.py` (memory budget and SSD spill cache), `accounts.py`, `group_access.py`, `admin_console.py` (usage logging and owner views), `license_manager.py` (an HMAC-signed licence key stub), `security_hard.py` (web request hardening), and `result_report.py` plus `superduper.py` plus `job_finalize.py` (report writing and the "best answer" card).

`tests/` contains 53 test modules that run under pytest. `tools/` contains the audit harnesses and benchmarks: accuracy campaigns, deterioration sweeps, stress tests that try to break the measurement, and timing audits. `scripts/` contains the release builders. `app/native/` and `app/cspeed.c` hold an optional C extension for the hottest numerical paths; it is not built by default and everything has a pure-NumPy fallback that is bit-comparable.

### 2.1 Why there are so many modules

A new reader will rightly ask: why is `measure_grs_precision` in `precision_engine.py` while `run_gold_standard` is in `gold_standard.py` while `run_champion_measure` is in `champion_measure.py`? The short answer is that measurement in this domain is not one operation. It is a family of related operations with different philosophies. The precision engine measures and blends. The gold standard module encodes definitions — different defensible answers to the question "what part of the spot are we calling its position?". The champion module tries to replicate what a careful human at a pro desk would do, running several estimators and weighting them by evidence quality. The research-grade and VLBI modules add calibration: inject a fake oval, measure it, and subtract the bias you just measured. The publish module decides which of all these answers is the one reported. Keeping them separate makes each one testable and makes the hierarchy explicit.

## 3. The Physics and Geometry Primitives

Everything starts in `precision_engine.py` and `planet_models.py` with a small set of constants and coordinate helpers, and with `paths.py` for where things live.

### 3.1 The planet model

`planet_models.py` defines a `Planet` dataclass with the numbers that planetary code needs: equatorial radius, polar radius or flattening, rotation period (in System III for Jupiter), and a zonal wind profile — the difference between the cloud-tracking rotation rate and the interior rate as a function of latitude. Jupiter, Saturn, Neptune, Uranus, and Mars are all defined there. The reason this module exists is that the earlier stackers hard-coded Jupiter's numbers into themselves; the planet-generalised versions take a `Planet` parameter instead, so the same stacker can be pointed at Saturn by passing a different object. The wind profile is stored as a list of latitude and velocity pairs and interpolated, because the jet structure (the equatorial jet, the polar jets, the alternating belts) is exactly what creates the latitude-dependent rotation that a derotator must undo.

### 3.2 The coordinate helpers

Three functions appear in several modules, always with identical bodies: `wrap_deg(x)` returns x modulo 360; `wrap_diff(a, b)` returns the signed difference a minus b wrapped into [-180, 180); and `deg2rad` / `rad2deg` convert. They look trivial and the codebase treats them as trivial, but the wrapping matters constantly: longitudes are periodic, a naive subtraction of 355 from 5 gives -350 rather than +10, and every comparison of two longitudes anywhere in the code goes through `wrap_diff`.

The full pixel-to-planet mapping lives in `precision_engine.py`. `planet_xyz_to_px` takes a point on the planet in Cartesian planet-centred coordinates and projects it into the image given the navigation state: centre pixels, equatorial radius in pixels, flattening, sub-observer latitude, and north pole position angle. This is an oblate orthographic projection with the sub-observer tilt and the roll applied. The inverse, `px_to_lonlat`, goes the other way: given a pixel and the navigation, it returns the System III longitude and the planetocentric latitude. A vectorised form, `px_to_lonlat_vec`, produces grids for whole maps at once and is the backbone of cylindrical mapping.

The reason a `NavState` carries both sub-observer latitude and north pole position angle is that the image may be rolled relative to celestial north. A stacked image from a camera on an alt-azimuth mount has an arbitrary rotation. Jupiter's own north pole position angle changes over the years too. To map a pixel to a planetocentric latitude you need both the tilt of the pole toward the observer and the roll in the image plane. This entire geometry chain (tilt, roll, oblateness, central meridian) is what earlier versions got wrong by treating the planet as a sphere, and the fix propagates through everything from limb fitting to derotation.

### 3.3 The navigation fit

`fit_limb_nav` is one of the most important functions in the codebase. It takes the image, the CM, and the distance, and returns a `NavState`. Its algorithm is deliberately old-fashioned and robust: cast many rays outward from an initial centre estimate, walk along each ray until the intensity crosses the limb isophote (the outermost brightness edge), collect those points, fit a circle or ellipse in a robust way, iterate, and repeat a fixed number of times while rejecting outliers with a MAD-based threshold. The limb is fit at an isophote level — not the maximum, not the sky level — because the planet edge is not a hard line; it is a smooth falloff, and which contour you call the edge changes the fitted radius. The position angle, when available, is used to de-rotate the rays so the fit is aligned with the planet's own axes rather than with the image axes.

The function is vectorised over rays and radii, so a 720-ray fit is a single array operation per iteration, and it converges in a few iterations. Its output is the centre, the equatorial radius in pixels, the flattening (from the planet model), and the sub-observer latitude and PA that were passed in. Almost every measurement in the codebase begins with this function, or with the multi-isophote version inside `champion_measure`. That version fits the limb at several isophote levels and picks the one that makes the subsequent spot measurement most stable. The reason is simple: a slightly larger or smaller limb outline changes the derived longitude and latitude. The professional workflow checks that sensitivity rather than hiding it.

### 3.4 The cylindrical map

Most estimators do not work in the raw image plane; they work on a cylindrical map. `make_cylindrical` creates a rectangular image whose horizontal axis is System III longitude relative to the central meridian, from -90 to +90 degrees, and whose vertical axis is latitude from -90 to +90. It does this by building a grid of planet-centred coordinates, projecting each to a pixel with the navigation state, and sampling the image with bilinear interpolation. On a map, the spot is an oval of approximately constant shape regardless of where on the disk it appears — it is not foreshortened near the limb — which makes template matching and size measurement much easier.

The map also makes "map edge" a meaningful concept. The visible hemisphere is only from -90 to +90 degrees of longitude relative to the CM, so a feature that appears near the edge of the disk maps to the edge of the cylinder, and measurements there are unreliable because only half the oval is visible. The code has a dedicated guard for this: a lock within about three degrees of the 90-degree edge is flagged and down-weighted.
## 4. The Measurement Engine

`precision_engine.py` is the heart of the project. It is where a single deprojected image becomes a longitude and a latitude, and it is the file I would point a new reader at first. It contains the geometry helpers from the previous section, the limb fit, the cylindrical mapper, four independent estimators, and the blend logic that turns them into one answer.

### 4.1 The four estimators

The engine runs four independent ways of finding the spot on a frame. Their independence is the whole point: they fail in different ways, so when they agree the answer is trustworthy, and when they disagree the disagreement itself is information about image quality.

**Template matching.** `_template_match_grs` builds a synthetic dark oval of a given length and width — about 12 by 8 degrees by default — embeds it into a map that has had the SEB band's background removed, and slides it across the map computing a normalised cross-correlation. The peak of the correlation surface, refined to sub-pixel precision with a parabolic fit, is the template answer. Templates are the classic approach and they are strong when the oval's contrast is clean. They are weak when the image is so blurred that the correlation surface is flat or when the peak locks onto a different dark feature.

**Moment mask.** `_moment_mask_grs` thresholds the map inside the GRS latitude band to isolate the dark oval, then computes the intensity-weighted centroid — the first moment of the dark mass. This is the barycentre definition. It is simple, it is what many careful amateurs do by eye, and it is stable; but if the mask catches a dark belt segment or a moon shadow the centroid is dragged.

**Map dark centroid.** `_map_dark_centroid` searches only the SEB band for the darkest compact region, applies a small Gaussian blur to the inverted image so the darkest pixel does not dominate, and computes the centroid of the resulting peak in a local window. Its latitude is constrained to the band by construction. Historically this method locked onto the polar hood — always dark, always north — which is exactly why the band restriction exists; the code comments note that lesson explicitly.

**Redness.** `_redness_grs` is the colour method. Given an RGB image, it builds an orange-oval score map — (R−G)×(R−B), the product of the two red excesses — and finds the compact peak of that map in the GRS latitude band. Brown SEB belts have R≈G, so the G−component zeroes them out; only the truly orange oval survives both factors. The spot is redder than the belts around it, and colour survives seeing far better than shape. This is the estimator that remains correct when all three dark methods have locked onto the wrong thing, and it is why the blend logic protects it from being pruned as an "outlier".

### 4.2 Sanity gates before blending

Before any of these four are allowed to vote, each result passes `_method_is_sane`: the latitude must lie inside the GRS band (about -36 to -10 planetocentric), the longitude must not be an obvious far-field lock, and sizes must be physically plausible. Then a pass of outlier rejection runs: results are clustered by longitude and anything outside the densest cluster is dropped — but with one carefully documented exception. The redness result is never pruned here, because all three dark methods share a correlated failure mode (they can all lock onto the same decoy dark feature), while redness is independent. The code comment explains the observed failure: at 2.6 arcsecond seeing, the template was 97.7 degrees off and the moment 28.5 degrees off on the same frame while redness was correct to 0.21 degrees. If the cluster prune had run before the arbiter, the correct answer would have been discarded as an outlier.

### 4.3 The blend

With the surviving estimators, the engine computes a quality-weighted consensus. Weights come from a table that encodes prior knowledge about which families of estimator are reliable. Template gets a high base weight when its dark contrast is strong. Moment gets a weight scaled by whether its size looks plausible. Redness gets its own weight. And every weight is multiplied by a Gaussian in latitude centred on the spot's expected latitude. A seed is chosen — preferring the highest-quality template, else the size-sane moment, and with a special rule: when the two dark methods disagree badly, the seed comes from the colour lock instead, because a wrong dark seed drags the whole weighted average toward the wrong feature.

The final longitude is a circular weighted mean of the inliers; the final latitude is a weighted mean. The engine also records which estimator was primary, the definition used, and a scatter measure that becomes part of the uncertainty budget. This consensus is what the campaign tests score, and the numbers are good: across 24 random clear 1080p frames the median absolute longitude error against planted geometric truth was about 0.17 degrees, the worst case 0.60 degrees, and latitude was similar. Against the intensity-weighted barycentre rather than the geometric centre the errors are even smaller, which makes sense: the engine's dark-core bias and the barycentre definition partially cancel.

### 4.4 The hard gate: when to refuse

`assess_disk_quality` and the softness machinery exist so the engine does not return a confident answer on garbage. If the disk is not resolved, if the limb fit residual is enormous, or if the image is so blurred that the estimators cannot agree, the result is marked accordingly and the publish layer down-grades it. Honesty about uncertainty is a design goal stated throughout the code: the engine would rather say "untrusted" than print a number with false confidence.

## 5. The Catalog of Estimators

`all_methods.py` and `all_methods_extra.py` take the idea of "run several independent estimators" to its logical conclusion: a catalog of 71 methods. The count matters less than the spread, but it is worth being exact: the `METHOD_CATALOG` constant holds 71 entries. They are grouped by family — map-based, image-plane, template, threshold, edge, spectral, robust ensemble — and each returns a `MethodHit` with a name, longitude, latitude, optional size, score, weight, and a note. Any method can fail individually without taking the suite down; the suite records the failure and keeps going. This is deliberately the "let a hundred flowers bloom" approach, and it serves two purposes.

First, it is a real estimate of the systematic floor. The methods share some assumptions, but they do not share all of them: the standard deviation of their answers is a real measurement of how definition-sensitive the position is. A tight cluster of seventy-one methods means the answer is robust to methodology; a spread of ten degrees means the answer is not.

Second, it is a catalogue of techniques from the literature and from classical computer vision, each implemented in good faith: percentile dark barycentres, Otsu thresholding, difference-of-Gaussians and Laplacian-of-Gaussian blob detection, phase correlation, isophote centroids at multiple levels, second-moment ellipse centres, morphological component analysis, seeded region growing, Sobel ring centroids, inverse-flux moments at several powers, FWHM cuts through the oval in longitude and latitude, Gaussian fits to profiles, box extents, geometric medians, PCA ellipses, convex hull centroids, distance-transform medial peaks, mean-shift modes, RANSAC ellipse fits, windowed ZNCC, SAD and SSD templates, symmetric phase-only matched filters, bottom-hat morphology, watersheds, structure tensors, radial symmetry transforms, Hu moments, percentile ladders, bilateral filtering, unsharp masking, rolling-ball background subtraction, kernel density modes, two-component Gaussian mixtures, ring templates, minimum enclosing circles, and the ensemble combinations. The `METHOD_CATALOG` constant is the index, with each entry's family, description, and a weight prior used by the SOTA layer.

`run_all_methods` executes the whole catalog, returns the hits, counts successes, computes a scatter, and suggests a primary. On a 1400 by 700 map the full run takes about 8.4 seconds on this two-core machine; the speed audit reports how many of the catalog entries succeeded, so a regression that breaks a whole family of methods shows up as a drop in that count rather than as a silent wrong number.

### 5.1 Why not just use the median of all seventy-one?

The `sota_accuracy.py` module answers this question in its own docstring, and the answer is a point about correlated errors that matters. The seventy-one methods are not independent measurements. They share the same mask, the same map, the same navigation, the same image. A tight consensus among them can be a tight consensus of the same systematic bias. The module is explicit: it produces scatter diagnostics only, never the published answer. The published answer comes from the gold standard and the champion, through the publish hierarchy. This is the difference between a demo that looks good and a measurement discipline that survives scrutiny.

`all_methods_extra.py` is the literature-flavoured half of the catalog, with a `LITERATURE_NOTES` constant citing the traditions each method comes from: classical centre-pick practice, the ACCIV/CIV correlation-image-velocimetry methods of Asay-Davis and colleagues for cloud tracking, the Hubble OPAL multi-year spot size and drift series, IRAF ellipse fitting, standard photometric FWHM cuts, geometric medians and RANSAC from robust statistics, mean-shift from density estimation, ZNCC and SAD templates from matched-filter theory, SPOMF from optical pattern recognition, structure tensors from image analysis, watersheds from mathematical morphology, distance transforms and skeletons from discrete geometry, and Hu moments from invariant shape analysis. It is a tour of the toolbox, and for each method it says what it measures and why that might be wrong.

## 6. Definitions: the Gold Standard and the Twin

### 6.1 Why "where is the spot" has no single answer

Ask three careful observers to mark the spot on the same image and you will get three different marks: one marks the darkest core, one traces the oval rim, one puts the crosshair on the middle of the west and east edges. All three are defensible. All three give different longitudes. The difference between them — the definition scatter — is a genuine systematic error that no amount of image processing removes. The professional community handles this by fixing a definition and by comparing like with like.

### 6.2 The gold standard module

`gold_standard.py` encodes the professional definitions as named constants. `GOLD_DEFINITIONS` maps each name to a full description: GS-BARY is the intensity-weighted dark barycentre in the GRS band; GS-MAP is the cylindrical-map dark centroid in the WinJUPOS-desk style; GS-TMPL is the dark-oval template match; GS-OVAL is the ellipse fit to the dark mask which also reports size; GS-EDGE-W and GS-EDGE-E are the west and east ends of the oval at mid-latitude, which are extent measurements and not centres; GS-MID is the midpoint of those edges; GS-ENGINE is the multi-method consensus. `PRIMARY_ORDER` says which definition the module would prefer to report: GS-MAP first, then GS-BARY, then GS-TMPL, then GS-OVAL, then GS-MID, then GS-ENGINE.

`run_gold_standard` runs all of them on one image, picks a primary by the order, computes the scatter among all definitions (which becomes the definition term in the error budget), and writes the results out with a `GoldStandardResult`. It also includes `compare_to_winjupos_manual`, which compares the automated answer to a human's WinJUPOS pick when one is supplied — the real accuracy check, because comparing two automated methods tells you nothing about absolute correctness.

The dark mask used by the oval and edge measurements is binary, built with a percentile threshold inside the GRS band, and cleaned morphologically. The oval measurement fits an ellipse to that mask — or rather computes moment ellipses and edge extents — with size clipped to physical plausibility: about 4 to 28 degrees in the east-west direction and 3 to 16 degrees in latitude. The cap exists because an unfiltered mask sometimes latches onto a belt segment and returns a thirty-degree "oval".

`attach_gold_to_package` is what the pipeline actually calls: it runs the suite, attaches the block to the job package, and optionally writes the JSON, text, and a WinJUPOS-compatible measure export file. The export is important because it means the user can paste their own WinJUPOS number next to ours and the code will compute the sky separation — the cross-check that actually means something.

### 6.3 The WinJUPOS twin

`winjupos_twin.py` takes the definition discipline one step further and quantifies the two things a careful human controls that an automated pipeline used to hide: the limb outline size and the measurement definition. `limb_outline_sensitivity` re-fits the navigation with an outer isophote (about 12 percent down from peak), a nominal one (18 percent), and an inner one (30 percent), re-measures the spot with each, and reports how much the answer moved. `grs_definition_sensitivity` measures core, west edge, east edge, and mid-of-edges and reports the spread. The twin result carries both sensitivities in sky arcseconds, and `attach_winjupos_twin_to_package` attaches them. A large limb or definition sensitivity is a warning that the published number is fragile — not evidence that the software is wrong, as the code's own tips reiterate.

## 7. The Champion Path and the Publish Decision

### 7.1 The champion

`champion_measure.py` is the closest the code comes to replicating a careful pro desk. It is layered: a stability-weighted multi-isophote limb fit, a local contrast enhancement of the SEB band, six estimators (GS-MAP, GS-TMPL, the engine, map dark, template, moment), a weighted hierarchy pick, sub-pixel refinement on the map, a nav-stability jitter test, a dual-channel agreement test, an ultimate lock gate, and a bootstrap for the method uncertainty floor.

The picker, `_pick_champion_centre`, is where the hierarchy lives. It iterates the estimators in order of preference. Results whose latitude falls outside the GRS band are rejected, as are locks within three degrees of the map edge. Each survivor is weighted by a Gaussian in latitude around the expected spot latitude, with boosts for strongly dark cores, for the GS pair, and for the engine. Then it finds the densest longitude cluster among the survivors, and uses that to seed the choice. The primary is the first estimator within twelve degrees of that seed. A weighted circular mean of the inliers produces the longitude; a weighted mean produces the latitude. When three or more estimators survive, a leave-one-out jackknife estimates the method scatter — removing one estimator at a time and seeing how much the consensus moves, then rescaling by the usual n/(n-1) factor.

There is also a special rule for the GS pair. If GS-MAP and GS-TMPL agree tightly — within about 1.25 degrees in longitude and 1.5 in latitude — and at least one has a strong dark score, the champion forces their mean and tightens the method sigma. The code calls this the pro dual-definition lock. It is the thing a careful observer does by cross-checking two independent definitions before committing.

The nav-stability test jitters the limb centre and radius by small amounts, re-runs the sub-pixel refinement, and checks whether the spot answer wobbles. The dual-channel test compares a mono measurement and a red-channel measurement of the same frame: if they agree within about a degree, the lock is on a real feature rather than a filter artifact. The ultimate lock gate then checks thirteen factors — trusted CM source, CM cross-check, latitude in core band, limb sky spread, definition spread, dark core strength, score, sky scatter, stability — before it will mark a run `UNBEATABLE_AUTO`. The module is explicit that this name means only "all automated gates passed", not "better than spacecraft or a careful human".

The refine step, `_subpixel_refine_map`, operates on the cylindrical map: it takes the integer peak, weights a small window by local darkness, computes the centroid in sub-pixel map coordinates, and repeats with a tighter window. A bootstrap around the refine step gives the noise floor for the method uncertainty. The final method sigma is the max of the jackknife scatter and the bootstrap floor, which prevents the code from reporting unrealistically tiny uncertainty on a noisy frame.

`attach_champion_to_package` is the production entry point: it pulls time-error and SPICE-versus-Horizons delta-CM from the package if available, runs the champion, and attaches the result. The desktop pipeline, the server, and the finalize step all call it, so the champion result is available wherever a job runs.

### 7.2 The publish policy

`publish_primary.py` answers one question: of all the numbers computed in a job, which one is the official answer? Its docstring states the core rules: publish the GS-MAP twin (or GS-BARY as fallback), treat method soup and SOTA as scatter only, and claim equality with a WinJUPOS manual pick only when the CM discipline matches and the sky separation is under an arcsecond.

The implementation is a scored candidate competition. `apply_publish_policy` gathers candidates from the pipeline stack, the champion, the gold standard, the orange GRS colour measure, the WinJUPOS twin, and the research-grade layer, and scores each one. The biggest term by far is latitude band: a candidate in the core band gains a hundred points, one in the wide band forty, one outside loses eighty. Then CM proximity is scored (near-limb candidates penalised), then label bonuses: orange colour, champion grades, GS-MAP, GS-BARY. Pipeline agreement adds a small bonus when the candidate is in the GRS band and near the pipeline answer. The top scorer wins and becomes the published longitude and latitude, and the headline block is rewritten in place so the UI and CLI always show the published answer first. A documented bug fix in the module history explains why the champion bonus exists: at one point champion candidates were silently getting zero bonus while GS-MAP got twenty-five, so GS-MAP always won even when the champion had the ultimate grade.

### 7.3 The best-answer card

`superduper.py` turns the publish decision into a single human-readable card: what number do I report tonight? It cascades from publish to champion to headline, whichever has data, and writes `SUPERDUPER_BEST_ANSWER.txt` and `.json`. It adds nothing new; it packages. `job_finalize.py` defines the expected file set — publish, champion, SUPERDUPER, report-this-one-line, pro ephemeris, job result — and marks the job complete only when the set is present, so a truncated run cannot masquerade as a finished one. `result_report.py` formats the same numbers into the dashboard table and the full human report.

## 8. The Calibration Layers

The measurement engine's job is to find the spot. The calibration layers' job is to find the engine's systematic errors and remove them.

### 8.1 Blind injection

`research_grade.py` implements bias calibration by injection and recovery, the experimental-physics trick: take the same image, inject a fake oval at a known position, measure it, and the difference between the recovered position and the injected one is the bias at that location. Do this at several positions and you get a bias field. `blind_injection_calibration` does exactly this with local recovery windows — it only looks near the injected position, which is the point: recovery error is a property of the measurement, not of the search. The injected oval is rendered through the same projection the engine uses, so the bias measured is the bias that applies.

The module also runs a definition suite (each of several operational definitions of "where is the spot", with consensus and scatter), a multi-filter closure diagnostic (R, G, B each measured separately after dispersion correction; if they disagree, something is wrong with the colour data or the alignment), and assembles an explicit error budget. `run_research_grade` is the full reduction; `write_publication_bundle` writes the JSON and text.

### 8.2 The VLBI-inspired layer

`vlbi_metrology.py` is the most methodologically ambitious module, and its docstring is careful to say what it is not: not real VLBI, which reaches microarcseconds with Earth-sized baselines, but the *methodology* of interferometric metrology — phase referencing, hierarchical error simulation, closure — applied to an optical photo of an extended cloud feature, where the real floor is arcseconds.

Its pieces: an `AdvancedNav` with full orientation, a `fit_limb_advanced` that trusts a stable multi-iteration radial-gradient centre, an oriented cylindrical mapper that respects the sub-observer tilt, a multiscale-template correlator that searches a grid of oval sizes, an isophote size measurement, a phase-reference injection that plants and recovers a fake feature to measure local bias, a hierarchical Monte Carlo that simulates seeing and noise in layers so the uncertainty estimate reflects the actual failure modes, definition scatter, filter closure, and a formal error budget that combines all of them: limb, definition, method, timing, CM, and the optical diffraction floor of the aperture. `run_vlbi_grade` assembles the whole thing into a `VLBIResult`, and `research_grade_compat` shapes the output so the existing UI can display it without changes.

### 8.3 The multi-epoch layer

`multi_epoch.py` uses the one trick that really cancels common-mode errors: differences. Absolute System III on a single night is limited by the ephemeris zero-point; the difference between two nights cancels it the way VLBI phase referencing cancels station delays. The module ingests measurements from job result files, phase-references them to a chosen epoch, fits a weighted linear drift and optionally a quadratic, and smooths the series with a Kalman-RTS filter (random-walk position plus rate state, forward filter, backward smoother). The output is a drift series with per-epoch uncertainties — the science product that turns a stack of numbers into a statement about the jet stream.
## 9. Ephemeris and Time: Where Absolute Longitude Comes From

The measurement engine can find the spot's offset from the central meridian beautifully, but the published longitude is only as good as the CM, and the CM is only as good as the time and the ephemeris. This is the discipline layer, and it is built to fail loudly rather than silently.

### 9.1 Extracting the time from a FITS file

`fits_time.py` exists because of a specific, humiliating debugging session documented in its docstring: a longitude was off by two degrees, and the cause was using the start of exposure instead of the middle. Jupiter moves 0.6 degrees per minute, so a 30-second A/D error is a third of a degree. `extract_fits_mid_time` parses the header, tries several field names, handles ISO-ish and FITS-standard formats, and — critically — computes the mid-exposure point from the exposure duration when the header has both DATE-OBS and EXPOSURE. `require_observation_time` is the fail-closed wrapper: no header time, no filename-derived time on an image the pipeline has never seen, no silent wall-clock default. It raises. The alternative, using `datetime.now()`, would produce a plausible-looking longitude that is wrong by however far the clock drifted from the exposure, and the module is designed so that failure mode is impossible.

There is a companion for AutoStakkert-style files in `grs_image_prep.py`: `parse_time_from_filename` handles the common naming convention where the timestamp is in the file name rather than the header.

### 9.2 The professional ephemeris resolver

`ephemeris_pro.py` is the module that computes the CM and everything else for absolute work. Its docstring opens with the lesson that motivated it: analytical CM can be ten to fifteen degrees off, and the author found this out the hard way when absolute measurements drifted compared with WinJUPOS. The resolver follows a priority chain, first success wins, per field: a user-supplied CM override, a WinJUPOS-format CM table, SPICE, JPL Horizons, then the analytical model — and the provenance of whichever source won is recorded.

`analytical_geometry` computes the simple circular model from the rotation period: CM advances at 360 degrees per System III period from a reference epoch. The function exists and works; the resolver just does not trust it for publication.

`fetch_horizons_full` queries the JPL Horizons web API for the Jupiter observer table and parses the observer block. `parse_horizons_observer_text` is tolerant of the minor format variations and pulls the quantities the pipeline needs: apparent distance, sub-observer latitude and longitude, north pole position angle. `load_winjupos_table` and `interpolate_winjupos_cm` accept the CSV or JSON files the community uses, linearly interpolate CM in the circular sense at the target epoch, and fall back to SPICE if the table is stale or missing.

`try_spice_geometry` goes through `spice_auto` (which finds kernels, downloads missing ones, and furnishes them) and computes the geometry from the SPICE kernel state. `resolve_pro_ephemeris` is the one-call entry: it builds the best available `ProEphemeris` object, records the source for every field, and attaches the CM uncertainty appropriate to that source. The reported sigma matters: trusted sources get a small sigma, WinJUPOS tables a moderate one, analytical a large one. The publication discipline in `accuracy_gates.py` keys off the source names precisely because of this.

### 9.3 SPICE kernel auto-discovery

`spice_auto.py` does the unglamorous but essential work of finding planetary ephemeris kernels on disk and downloading the right ones when they are missing. It checks the local kernel directory, verifies sizes, fetches from the NAIF archive with a proper user agent and a secure SSL context, writes to a temporary file and renames it into place atomically, and furnishes everything into the SPICE pool. The zero-user-configuration goal is stated plainly: no hunting for kernels. If the network is unavailable and the kernels are absent, it reports exactly that rather than pretending — and the resolver falls back with a clearly labelled source.

### 9.4 NASA comparison without inventing NASA truth

`nasa_compare.py` is a small module with a strong opinion: Horizons gives planet geometry — distance, CM, sub-observer latitude, PA — and it is not a catalog of the spot's position. `compare_measurement_to_nasa` reports the user's measurement as-is next to the real Horizons fields and never invents a "NASA GRS longitude" to compare against. The distinction matters because several earlier versions of the UI apparently implied such a comparison existed, and a fake reference number would be worse than no number.

### 9.5 The drift model and ground truth

`grs_ephemeris_truth.py` packages the community's published knowledge about the spot's motion: the drift rate, about 0.31 degrees per day westward relative to System III (the observed range is roughly 0.30 to 0.36, and the module adopts 0.31 with the citation on it), anchored to a date, with the literature latitude of -22.4 planetographic. It provides `grs_longitude_iii_w` (the spot's expected System III west longitude at a given time), `analytical_cm_iii`, `grs_lon_rel_deg` (spot relative to CM — the quantity that determines where on the visible disk it appears), `grs_transit_time` (when it crosses the central meridian on a given night), and `observe_at_placement` (pick an observation time that puts the spot at a chosen offset from the CM, which is how the synthetic generators plant a frame with the spot at a known place). The module also outputs machine-readable `sources`, because every constant should be citable.

This module is the bridge between astronomy and testing: because the spot's true longitude is known as a function of time, a synthetic frame can be planted at the true position and the measurement scored against a truth that did not come from the code being tested.

### 9.6 Transits and session planning

`transits.py` computes the night's geometry: when the spot transits, when it is visible above a given horizon, the CM and sub-observer geometry through the night, and the Galilean moon events — eclipses, transits, shadows — that could contaminate the image. `session_planner.py` answers the practical question that amateur folklore handles with fixed numbers: how long can a video be before rotation smears it? The answer is a formula, not a rule of thumb: it depends on the pixel scale, the latitude of interest, the smear budget in pixels, and whether you derotate. `smear_px`, `max_span_s`, and `max_span_derotated_s` compute it; `filter_window_plan` computes how far apart filter exposures can sit; `session_plan` assembles the whole panel. This exists because a fixed "three minutes for Jupiter" is wrong by a factor of several at some scales and latitudes, and the planner shows the actual arithmetic.

## 10. The Ephemeris and Imaging Side: Video to Stack

Most amateur data is not a single image; it is a video, and the quality of the final measurement depends as much on the stacking as on the measurement. This section is about turning a raw capture into a clean stack.

### 10.1 Reading the capture

`ser_io.py` reads and writes the two planetary-video containers: SER, the modern ZWO/Player One/QHY format, and uncompressed AVI, the legacy DIB capture format. It is dependency-free — pure Python with standard-library struct and array work. A SER file has a 178-byte header with the camera, colour, and pixel data, then raw frame bytes, optionally with timestamp chunks; AVI uses the RIFF container with BITMAPINFOHEADER frames. The module exposes a `Video` class that is random-access over the frames and a `read_video` dispatcher that detects the format by magic bytes. The frame timestamps come back as aware UTC datetimes when the container stores them, which feeds the finalize layer's per-frame derotation timing. There is also a writer, so benchmark sequences can round-trip.

### 10.2 Frame quality and lucky imaging

`frame_quality.py` scores every frame of a video and keeps only the best fraction. The score is a blend of local sharpness measures — Laplacian variance, gradient energy, Sobel magnitude, local contrast — computed only on pixels inside a cheap on-disk mask so the sky does not contribute. `select_best_frames` keeps the sharpest keep-fraction; `lucky_report` summarises the distribution. The point is that stacking every frame equally lets the blurry majority drag the result down, and the stacker needs an explicit rejection step rather than a soft weight. A documented regression in the changelog shows why this module exists at all. The on-disk mask used to average an HWC RGB array over the height and width axes, producing a shape-(3,) mask. Every colour video frame then scored sharpness zero, and the stacker effectively kept the first frames regardless of quality. The mask now uses proper NTSC luma for both HWC and CHW layouts.

### 10.3 The AutoStakkert-style stacker

`ap_stacker.py` is the per-alignment-point stacker. Its algorithm is the standard recipe. Place a grid of alignment points over the planet. Track each point in every frame with a local sub-pixel shift estimate — windowed phase correlation for the integer part, Lucas-Kanade for the refinement. Score each point in each frame for local sharpness. Stack per point using only that point's own best fraction of frames. Because seeing varies across the disk, per-point selection beats global selection. The module adds two things AutoStakkert does not: a true drizzle super-resolution path, where each aligned crop is accumulated onto an upsampled canvas with weights, giving a genuine resolution gain; and an optional rotation-aware prior, where the expected drift from the ephemeris is subtracted before correlation so the tracker does not lose features under fast rotation.

The shift estimation is the numerical hot spot. `_measure_shift` does the apply-shift and returns an SNR; `_lk_refine` does the Lucas-Kanade iteration, which is where the optional C extension pays for itself — the profiler showed it dominating about 91 percent of stack time, almost all spent in five spline `map_coordinates` calls per iteration, so `cspeed.py` wraps three C kernels that replicate the spline math to last-ULP scale.

`stack_ap` is the main entry: it takes frames, frames APs, and returns the stacked image plus a per-AP quality map. `derotate_frames` applies rotation compensation before stacking. `wind_report_from_drifts` converts the measured per-AP drifts into a measured zonal wind profile. That is a real science product from the same data: the cloud-tracking velocity as a function of latitude. These are exactly the numbers `wind_analysis.py` interprets.

### 10.4 Planet-generalised stackers and derotators

`planetary_stacker.py` generalises the AP stacker to any `Planet`. Its key fix over the Jupiter-specific version is that the final warp is per-latitude rather than a single global translation. The per-AP drifts are expanded into a dense displacement field, so features at different latitudes can move at different rates. That is the physical reality on a gas giant. `planetary_derotator.py` does the analogous thing for derotation, with a prior-plus-measurement tracker so it can lock even when rotation has moved a feature more than the search window.

`jupiter_zonal_stacker.py` is the Jupiter-specific ancestor. It uses System III rotation as a prior for the expected drift of features in the equatorial band, and a known zonal-wind residual profile for the rate at each latitude. So the tracker knows where a feature should be before it looks. `jupiter_zonal_derotator.py` applies the simpler WinJUPOS-style derotation, a single global rotation about the planet's centre fitted from the equatorial-band drifts — which is exactly what WinJUPOS does, and which is correct for the bulk rotation even if it ignores the fine zonal shear.

`jupiter_zonal_derotator.py` and `win_jupos_derotator.py` are close relatives; the difference is that the latter computes the rotation angle by robust fitting of the AP drift field to a rigid rotation, and applies it with an exact shear-decomposition rotation.

### 10.5 Flow warp and image warp

`image_warp.py` is the exact sub-pixel translation primitive used by every stacker. Its docstring documents the bug it fixes. The classic FFT phase-ramp shift multiplies a Hermitian spectrum by a linear phase. For non-integer shifts that destroys Hermitian symmetry, so the inverse transform comes back complex, and taking the real part silently returns the wrong image. It is a classic "it looks fine" numerical bug. The replacement uses the real FFT with the appropriate packing so the transform stays real, or spline interpolation, and the module is validated to match the reference implementation to the last ULP in the C path.

`flow_warp.py` extends translation to a dense two-dimensional field. The per-latitude warp in the planetary stacker moves each row by one x-shift; that captures pure zonal shear exactly but no local eddy, meridional drift, or limb foreshortening difference. `fit_dense_apply_field` fits a smooth displacement field from the per-AP measurements with a Gaussian radial-basis expansion, and `apply_flow_warp` backward-warps by it. This is the "when the planet is not just shearing" path and it is what the benchmark `flow_warp_benchmark.py` A/B tests against the simpler modes.

### 10.6 Sharpening, colour, and dispersion

`sharpen_lab.py` brings the RegiStax step into the pipeline so no external program is needed. It implements the à-trous B3-spline starlet decomposition, per-layer wavelet gains (the classic RegiStax sliders), noise estimation from the finest layer's MAD with soft thresholding at k-sigma per scale, Richardson-Lucy deconvolution with a Gaussian PSF, and unsharp masking. The module's tips warn about the real failure mode: aggressive deconvolution invents dark cores, which then drag the measurement. `dcr.py` applies differential atmospheric dispersion correction using the Edlén refractive-index formula for the per-channel shifts of a filtered sequence, with the explicit scope note that it only applies when channels are isolated.

`rgb_combine.py` composites the three mono filtered sequences onto a common epoch geometry. Because Jupiter turns between filter runs, a naive composite fringes everywhere there is a longitudinal albedo gradient; the module resamples each channel onto the reference time's geometry using the ephemeris, then does a per-band residual polish alignment, and reports a colour-fringe metric so you can see whether the alignment worked. `filter_wheel.py` orchestrates the whole mono workflow in one call: each filter's SER or AVI gets its own APS stack, the stacks are rotation-derotated to a common epoch, composed, and every artifact is written to disk.

### 10.7 The production video pipeline

`observatory_pipeline.py` is the one-call production path: SER or AVI in, publishable answer out. `stack_video` runs the APS stack with lucky selection and optional drizzle, `sharpen_file` runs the sharpen lab, `animate_frames` exports a blink GIF, `export_jupos` writes the community CSV, and `video_to_answer` runs the whole chain: capture, stack, sharpen, then the standard published measurement path, then the best-answer card. `filter_wheel` and `rgb_combine` plug in above it for colour.

## 11. The Synthetic Renderer: Making Ground Truth

You cannot measure absolute accuracy on a real image of Jupiter without knowing the true position, and on a photograph of a real planet you almost never do. So the project builds fake planets where the truth is planted, and measures those. The quality of the entire validation story hinges on the fidelity of the renderer: if the fake planet differs systematically from a real one, the measured accuracy is an artifact of the fake.

### 11.1 The high-quality synthetic

`synthetic_hq.py` is the renderer that matters. It creates a photoreal-oriented scene. The disk gets the major belts and zones with realistic latitudes and soft edges. The edge is limb-darkened. The spot is planted as an oval whose dark core sits away from its geometric centre — that asymmetry is deliberate, and it is why the rim methods exist. On top of that go a seeing point-spread function of the requested width, noise of the requested RMS, and optional colour structure. The observer geometry is real: sub-observer latitude from the ephemeris, position angle, distance, and the CM at the chosen epoch. The parameter set is a `SynthSpec`: resolution preset, seeing in arcseconds, noise RMS, region of the frame, mode, seed, and optional overrides for distance, sub-latitude, and PA. Every run takes `random_time=True` by default so the epoch is different each time, which stops the campaign being rigged — CM is not held constant.

Two documented hooks matter for the validation harnesses. An environment variable forces the spot near the limb for the limb-validation suite, and a spec field does the same so the "every atom" training can plant spots at extreme limb positions. The renderer also accepts a forced CM seed for reproducible campaigns. And there is a subtle line worth quoting. The spot is planted at `GRS_LAT0`, the planetocentric equivalent of the literature latitude — not at -22.0 planetocentric. That hard-coded value was itself a bug. It put the synthetic spot about 2.2 degrees away from where the engine searches. The comment says it plainly and the constant is shared with the measurement side, so the renderer and the engine cannot drift apart again.

`video_synth.py` extends the renderer to actual rotating video: a sequence of frames with the spot and belts rotating at the true rate, per-frame ground truth, and optional seeing and noise evolution, so the stackers and derotators can be tested on the exact kind of data they consume. `hard_synth_suite.py` renders stress cases: spot near the limb, extreme sub-observer geometry, monochromatic input, off-band sizes, very poor seeing — and measures each through the published path, grading the calibration A/B/C/D from the median, 95th percentile, and pass rate. This is the stress test done straight: it is small, it is not a marketing facade, and its docstring says so.
## 12. The Neural Networks

Machine learning appears in this codebase in exactly three places, and each is scoped deliberately. The design principle, stated repeatedly in docstrings, is that physics methods stay authoritative and the networks are optional soft priors used only where classical methods struggle. There is no attempt to have a network output the published longitude directly.

### 12.1 SPIRE-Net

`nn_grs.py` implements SPIRE-Net, a small convolutional network written in pure NumPy. The architecture takes a cylindrical intensity map (64 by 128 by default), runs several convolution blocks with max-pooling, and produces two outputs: a heatmap over possible positions and a regression head for longitude offset and latitude. The forward pass, convolution, max-pooling, and the backward pass for training are all hand-written in NumPy (`conv2d`, `maxpool2`, `conv2d_bwd`), with a vectorised fast path (`conv2d_fast`). The weights are stored as a flat set of named arrays in a compressed `.npz`, and the whole training loop — `auto_train`, `_sgd_step`, `overnight_train`, checkpointing, plateau escape by re-initialising the heads while keeping the feature extractor, emergency flush on signals, macOS caffeinate so training survives a closed lid — is in the same file.

In production the inference path is what matters: `predict_soft_prior` loads the frozen weights and returns a soft prior for the spot's position, which `precision_engine` and the hard-case assistant can blend with the physics consensus at a modest weight. The shipped weights are frozen under `app/models/spire_net_weights.npz`; the training entry points raise a runtime error in normal usage, and the file's docstring says why: don't retrain for normal use. `map_to_nn_input` converts a cylindrical map to the network's input layout, and `truth_to_targets` turns a known longitude and latitude into the heatmap and regression targets for training.

`spire_finetune.py` is the optional fine-tuning entry point, used by the desktop app's train button and gated by the licence system.

### 12.2 The hard-case assistant

`ai_hard_cases.py` implements the gating policy in code. `estimate_image_difficulty` scores a frame from zero (easy, sharp oval, tight cluster) to one (soft, noisy, low contrast mess); `estimate_method_difficulty` scores how much the classical methods disagree and how badly they struggle. `assist_hard_case` only intervenes when difficulty is high. Even then it blends toward the network prior and pulls the answer toward it with a weight capped at a modest fraction. The code's comment says it plainly: physics and SOTA win on easy nights, and AI only helps disambiguate on hard ones. Machine learning belongs in exactly one place here: not absolute System III, not CM, not time, but feature disambiguation under mess, where a learned prior really does have information.

### 12.3 The HolyCNN stacker

`holy_hybrid_stacker.py` puts a small discriminator CNN inside the stacking path. Each 32-by-32 alignment-point patch is scored by a network with sixteen hidden features and three outputs: a quality score and a two-component drift estimate. The docstring is explicit that "Holy" is a label, not a claim: the network is a learned quality-plus-drift scorer. It is trained by self-distillation on synthetic AP patches at startup if no weights file exists. The interesting part is the fusion: `_map_estimate` is a joint maximum-a-posteriori estimate combining the CNN likelihood with a physics prior built from Kolmogorov turbulence scaling and Zernike amplitudes, and the RBF velocity-field fitting is quality-weighted so patches the network distrusts contribute less. This is as close as the codebase gets to "learned methods and physics methods cooperate inside one estimate".

### 12.4 What the networks are not

Let me say plainly what the networks are not, because their names overpromise. They are not trained on Hubble or Juno images; they are trained on synthetic Jupiters from the same renderer used for validation, which means they inherit the renderer's realism limits. They never output the published answer. They never compute the CM. They do not run on easy nights. The code says all of this in its own docstrings, and the design keeps the claim honest.

## 13. The Interfaces

Four interfaces drive the same core: a command line, a desktop app, a web server, and the one-call pipeline.

### 13.1 The command line

`cli.py` is the professional interface: subcommands for version and environment, licence status and activation, owner log summaries, ephemeris resolution, synthetic generation, full processing of a real image, product certification, the imaging pipeline stack, and the sharpen, filter, RGB, DCR, zonal-stacker, derotator, animation, JUPOS, transit, wind, and analysis tools. It is thin: it parses arguments and delegates to the modules, which is exactly right for a CLI. `product_core.py` holds the product identity — name, tagline, version read from the VERSION file — and the certification entry that runs the metrology suite and reports pass or fail.

### 13.2 The desktop app

`desktop_app.py` is a Tkinter application of about three thousand lines. Its docstring is refreshingly candid about the process: the UI went through dark theme, then light with macOS inspiration, and settled on the current palette per case. The workflow is the one the whole project is built around: open a file, set UTC, process (which offers both an automatic limb and a by-eye limb outline), publish, and read the result in the top strip with grade, longitude, latitude, total sky uncertainty, truth recovery on synthetics, and epoch. The user can generate synthetics, resolve ephemeris, run the multi-epoch differentials across previous jobs, run the hard-synth stress suite, run a factory night end-to-end, or load a WinJUPOS manual measurement to check agreement.

Two pure helpers are extracted for testability: `resolve_manual_path` finds the user guide (the observatory book) from a list of candidate locations, and `resolve_buttons_doc_path` finds the button-to-function guide. The rest of the file is event handlers and panels, but the log bridge and the processing calls all route through `desktop_pipeline.py`, so the desktop app contains no measurement logic of its own.

### 13.3 The server

`server.py` is a Flask API exposing the same stack over HTTP: health, logs, upload, process, synthetic, ephemeris, WinJUPOS template download and upload, multi-epoch, hard-synth, factory night, transits, analysis session, drift, sharpen, video stack, deterioration, and file serving. It wraps every expensive call behind `_security_before` (rate limiting, host checks, upload extension and path checks) and the security module. The processing endpoint is a full job runner: it allocates an output directory, runs the desktop pipeline, writes the report, and returns the package. The UI in `templates/` and `static/` is a single-page app for the same endpoints.

The server's root is `127.0.0.1:8765` by default, configurable with environment variables, and it warns if bound to a non-local interface because the file APIs are local-trust. `security_hard.py` is the companion: allowed upload extensions, blocked basenames (the licence file, model weights, owner logs, the git directory), traversal detection via resolved-path checks, dangerous filename sanitisation, host-header checks, control-character stripping, security headers, and a simple sliding-window rate limit. Its docstring is frank: local apps cannot block every attack, but these reduce the surface.

### 13.4 The pipelines that bind them

`desktop_pipeline.py` is where the shared orchestration lives, deliberately so desktop and web cannot diverge. It loads images in any format, tries the imaging pipeline first and soft-fails gracefully to the raw frame, runs the full advanced stack (research grade, VLBI metrology, Monte Carlo, gold standard, WinJUPOS twin, champion, publish policy, SUPERDUPER), writes the reports, and supports the human-choice second pass. `run_synthetic_full` and `run_process_full` are the two big entry points; `run_factory_night_full` runs the one-button self-test. `job_finalize.py` attaches the champion and publish products and checks completeness.

`product_core.py` also routes: it is the product identity and the certification harness, and the server and CLI both read the version from it rather than hard-coding. `paths.py` is the last piece of plumbing that matters: it resolves CODE_DIR, DATA_DIR, and MODEL_DIR across source checkouts, PyInstaller bundles, and macOS app bundles, and `ensure_tree` creates the standard folder layout, so the outputs, logs, licence, and models land in the right place on every machine.

## 14. The Test Suite

Tests are the part of a project that tells you whether the docstrings are lying. There are 53 modules under `tests/`, and they run clean: 487 passed, 15 skipped, zero failures, in about six minutes on this two-core machine with two workers. The skips are legitimate ones — five need a display for Tk, four need the optional native C extension that is not built here, four need real photo fixtures that are not in the repository, two need network or platform features.

### 14.1 What the suite covers

The geometry tests are the ground floor: projection round-trips (pixel to longitude and latitude and back), the NavState tilt and PA handling, limb-fitting residual under noise, the flattening correction, the map edge guard, and the 100-case resolution by seeing campaign (`test_resolution_seeing_100.py`) that pins the headline accuracy claim: every clear, mild, and blurry case within 1.0 degree, very-blurry within the documented stress band.

The measurement tests exercise the engine's blend logic on synthetic frames with known truth, the gold standard definitions and their scatter, the rim-ellipse estimator, the champion's gates (it must not claim ultimate status on a frame where the gates fail), and the publish hierarchy (champion actually beats soup). The sensor and video tests cover SER/AVI round-trips, the APS stacker on synthetic video, the frame-quality mask on RGB (the regression for the (3,) mask bug), the sharpen labs, DCR, RGB combine, and the filter wheel.

The ephemeris tests compare SPICE and Horizons outputs, test WinJUPOS table interpolation, check that missing time raises rather than defaults (the fail-closed contract), and validate the real-ephemeris synthetic campaign. The neural tests check the forward pass against a reference, the weight sanity (no NaNs), and that the hard-case gate does nothing on easy frames. The interface tests construct the wiring without a display where possible, and the web tests exercise endpoints headlessly. The performance tests pin the hot-path timings so a regression in the C-extension or the vectorisation is caught as a CI failure rather than a surprise.

### 14.2 The audit tools

`tools/` holds the campaigns that produce the numbers this project cites, and they are all resumable: each writes JSON Lines, reads back what it already has, and only computes the missing rows. `accuracy_campaign.py` runs the core loop (render, limb fit, measure) over many seeds and reports the distribution of errors; `per_method_audit.py` sweeps the resolution by seeing matrix and reports per-estimator bias and scatter so the question "which method is wrong?" has an answer; `seeing_floor_stress.py` deliberately tries to break the measurement and finds the seeing at which it does; `real_ephemeris_campaign.py` plants the spot at real historical epochs rather than random drawn ones; `real_photo_validate.py` and `real_photo_stack.py` check the measurable properties of real frames — lock rate, limb stability, method agreement, noise repeat, rotation equivalence — without inventing a truth that does not exist.

`speed_audit.py` times each stage. `cspeed_benchmark.py` compares the C and NumPy paths. `flow_warp_benchmark.py` A/B tests the warp modes. `zonal_stacker_benchmark.py` and `zonal_derotator_benchmark.py` run the Jupiter-specific and planet-generalised stacker and derotator head to head on generated video. `benchmark_native.py` times the same measurement loop with and without the C extension, and `build_cspeed.py` builds it. `real_photo_stack.py` runs every planetary-stacker warp mode on a real frame set so the differences are measured, not argued.

## 15. The Supporting Machinery

The remaining app modules exist to make a real product out of a measurement library.

`paths.py` and `verbose_log.py` are plumbing, but the log is worth mentioning: it is thread-safe and used by both the UI and the server, so a job running in a web request and a job running in the desktop app cannot interleave garbled output.

`ram_ssd.py` implements the memory budget: the target machine has 16 GB, and the peak working set is capped around 10 GB so the OS stays responsive. Large arrays spill to an SSD cache under `app/ssd_cache` via memmaps, resolution choices are capped by the budget, and the number of Monte Carlo iterations is reduced at high resolution to fit the time budget. This module exists because the Monte Carlo layers can allocate many copies of a large image, and the failure mode is a frozen machine, not an exception.

`accounts.py`, `group_access.py`, and `admin_console.py` are the usage-logging and owner-view stack: optional identity, no passwords required for open group use, every major action appended to an owner log, and commands to summarise who ran what. `license_manager.py` is the commercial key system: HMAC-SHA256 signed keys with plan, payload, and machine binding, verified on the CLI and desktop, gating resolution and features. The default secret is explicitly evaluation-only.

`result_report.py`, `stack_report.py`, `wind_analysis.py`, and the drift and animation modules are the reporting science side. `wind_analysis.py` interprets the measured per-bin wind profile. It fits a uniform advection offset and a System III angular-rate offset — two different physics, depending on whether the clouds are moving with the atmosphere or the planet's core rotation. It detects jets as significant extrema of the residual profile, and exports a JUPOS-friendly CSV. `limb_darkening.py` measures the limb-darkening exponent from a finished stack by fitting log intensity against log mu in latitude bands — a real atmospheric diagnostic and a systematic for every isophote fit. `deterioration_lab.py` is the resolution-by-seeing-by-noise sweep with the error floor extraction and per-estimator breakdown, exposed as a web panel.

## 16. Honest Limits

Every real codebase has limits, and this one is unusually explicit about them, so the last section of this essay states them plainly.

The biggest is that absolute System III accuracy is fundamentally bounded by the CM and the time, and no amount of image processing fixes a two-minute clock error. The best clear-sky numbers from the campaigns are around 0.2 to 0.6 degrees of longitude against planted geometric truth, with the sky-plane error around 0.08 arcseconds — that is a good ground-based result, but it is not Hubble, and the code never claims it is.

The four-vote engine's dark methods share a correlated failure mode, and while the redness lock and the disagreement guard mitigate it, they are not a proof against all decoys. The map dark centroid specifically fails near the map edge by construction — the cylindrical map only covers ninety degrees each side of the CM, and the method's own guard rejects those locks, so it contributes nothing when the spot is near the limb. The rim-ellipse estimator handles that regime better because it works on the visible rim, and it is the one method whose error comes from a first-principles geometric source rather than interior brightness.

The champion path, the strongest automated path, is also the most complex, and complexity carries risk: it runs six estimators plus a nested engine measurement, and its gates are only as good as their thresholds. The publish policy is a scoring function over candidates, and its bonuses were tuned against the campaigns — it is a policy, not a theorem. The synthetic training data for the networks comes from the same renderer used for validation, so a renderer realism gap would propagate into both.

Memory and speed constrain the Monte Carlo layers: on the target 16 GB laptop the iteration counts are capped, and the full factory path takes minutes per frame rather than seconds. The native C extension is optional and not built in this checkout; all numbers quoted here are the pure-NumPy path. And the real-photo validation is inherently limited by the lack of trustworthy absolute references — which is why the real-image suites measure invariance properties (rotation, scale, noise) rather than inventing a truth.

None of these limits are hidden in the code. They are in docstrings, in the gate thresholds, and in the way the engine refuses to publish a confident number on garbage. That is the design the whole project is built around, and it is the reason the published numbers are worth reporting at all.

## 17. Glossary

**CM** — central meridian: the System III longitude facing the observer at an instant.

**System III** — the standard rotation frame for Jupiter's longitude, period 9h55m29.711s.

**Planetographic / planetocentric** — two latitude conventions; differ by up to ~2.6 degrees at the spot's latitude. The code works planetocentric and converts for output.

**Limb** — the planet's edge; its fitted outline gives the pixel scale and centre.

**PA** — north pole position angle: the roll of the planet's pole in the image.

**Seeing** — atmospheric blur, quoted as FWHM in arcseconds.

**SEB** — South Equatorial Belt, the dark band that contains the spot.

**APS** — per-alignment-point stacking.

**SER** — the modern planetary video container.

**VLBI** — in this code, the *methodology* (phase referencing, closure, hierarchical error simulation), not the microarcsecond technique.

**DCR** — differential atmospheric dispersion correction.

**Blind injection** — plant a fake oval, measure it, subtract the measured bias.

**Definition scatter** — the systematic difference between measuring the dark core, the oval rim, or the edges.
## 18. The Remaining Modules, Door to Door

This section covers the modules that the pipeline tour touched only in passing. Each one is part of the whole, and each has a reason to exist that a reader would otherwise have to reconstruct from the code.

### 18.1 The rim ellipse estimator

`grs_ellipse.py` is the newest measurement method and the answer to a specific documented problem: every classical centre estimator keys on interior brightness, and the spot's interior is asymmetric — its dark core sits off-centre inside the orange oval and moves around with seeing. That is precisely the failure mode the earlier audits documented: template and moment were being pulled toward the dark core, away from the geometric centre of the oval itself. The rim is a different signal: a sharp, closed, nearly-elliptical boundary between the orange oval and the surrounding belt.

The method works like this. `ellipse_grs` takes the image and a navigation state. It builds a redness feature map on a cylindrical map — or an inverted mono map if no colour is available — and searches outward from a seed position along a set of spokes. There are 72 by default. At each angle it looks for the gradient's crossing. The collected rim points are then fed to `fit_ellipse_fitzgibbon`, a direct least-squares ellipse fit in the tradition of Fitzgibbon's conic-fitting method, which returns the centre, the semi-axes, and the rotation angle. The fit is repeated robustly: outliers are trimmed with a 2.5-times-MAD rule, and a RANSAC variant fits a five-point minimal ellipse to a random subset as a fallback when the direct fit degenerates. Degenerate cases are guarded explicitly, because an unconstrained conic fit on a handful of noisy points can return a hyperbola.

The result is a method dict with the standard fields plus ellipse diagnostics, and an adapter `m_ellipse_rim` produces the `all_methods.MethodHit` shape so the estimator joins the seventy-one-method catalog as a first-class member. The validation numbers are the tightest in the method suite, and the module documents them precisely. On the 100-case resolution-by-seeing audit, the classic least-squares-plus-trim path converged on every clear and mild case, and on the cases that converged the errors were |dlon| median 0.109 / max 0.733 degrees and |dlat| median 0.116 / max 0.580 degrees — zero cases outside 1 degree. The RANSAC fallback fires only when the classic path fails; on the 24 hard failures it recovered 21 of them, with errors degrading in a predictable way, to a median 1.3 and max 2.8 degrees, and the `m_ellipse_rim` adapter down-weights RANSAC hits to 0.6 accordingly. And here is the key difference: the method does not fail when the spot sits near the limb, because it measures the visible rim rather than the interior of a foreshortened oval. The module also records fixing a latent crash alongside the method's development: the direct conic fit returned a math domain error on junk five-point samples around one in twenty minimal sets, which would have made RANSAC unusable; the guard turns that into a None and the fallback takes over.

There is a geometric point worth making about why the fit is legal at all. The ellipse is fitted in (lon_rel, lat) map space at about 0.1 degrees per pixel, over at most a +-12 degree window, and over that window the equirectangular distortion stays under 0.3 percent — far below the noise floor the method operates at. The module's own docstring makes the limits equally plain. It needs a visible oval rim. On appalling seeing the spoke contrast collapses, the score goes to zero, and callers down-weight it like any other soft method. The ellipse model is excellent but not exact — the oval is elliptical to about 5 percent — so the residual asymmetry is reported as `rim_rms_deg` rather than hidden.

### 18.2 The human choice pass

`human_choice.py` encodes the WinJUPOS discipline that the rest of the pipeline automates. Its docstring quotes the JUPOS practice directly: an automatic outline is only a first guess, the feature definition is a choice (dark core versus outline edges), and the same mid-exposure UTC plus CM discipline apply. The module provides the dialogs for a by-eye limb outline, the comparison between automatic and human answers as a delta sky separation, image flips for mirrored stacks, the extraction of the outer rim west-east edges for size (not for the published centre), and the GS-MAP-plus-rim combined definition that the tips recommend.

The important design point is the comparison: `compare_measure_snapshots` measures the difference between the automatic publish and the human pick. A large delta sky error almost always means definition or limb discrepancy, not "NASA wrong" — the module's own tips list says so. This is the accuracy check the real-photo validation was missing, and it is the reason the desktop app asks the user to paste a WinJUPOS measurement: it converts a subjective disagreement into a number.

### 18.3 WinJUPOS+ export

`winjupos_plus.py` packages the automated stack into the report language of the professional tool so a user can compare like with like. It builds a block with planetocentric and planetographic latitude (WinJUPOS uses planetographic), the recommended limb outline from the multi-isophote probes, the EW edge extent as a size product (not a template prior), a side-by-side equality score against a manual WinJUPOS pick, and a single citation line for logs and papers. Its docstring says what it does not do: it does not claim to beat Hubble or Juno. The value of the module is parity of vocabulary: when the automated answer and a human's WinJUPOS answer differ, the difference is measured in the same units and against the same definitions.

### 18.4 The JPA stackers

Three modules with extravagant names — `jpa_10d.py`, `jpa_10k.py`, `jupiter_infinite_tensor_engine.py` — are the experiment space for stacking. The names are engineering-lab humour, and the docstrings are careful to say what is real. `jpa_10k.py` is a 5D spatiotemporal velocity-AP grid stacker with zonal derotation: it fits a velocity field from the AP grid, adds a time dimension, and stacks along the fitted trajectories. `jpa_10d.py` extends the representation with more dimensions of the same idea. `jupiter_infinite_tensor_engine.py` is the Hilbert-space framing: the same estimators expressed as operators, so the code can reason about the stacking as a projection into a basis rather than a heuristic loop. All three are used by the desktop app and by `holy_hybrid_stacker`, which sits on top of them. The framing of the docstrings matters here: these are serious implementations of ideas with playful names, and the code does not pretend the names are claims.

### 18.5 Drift as a science product

`grs_drift.py` turns repeated measurements into geophysics. A single longitude is trivia; the same quantity over weeks is the zonal wind at the spot's latitude doing work on a vortex. The module defines a `DriftPoint` (epoch, longitude, uncertainty), imports the JUPOS-format CSV that the community database uses, and fits the drift with `fit_drift`: a weighted fit with sigma-clipping against a linear (and optionally quadratic) model, with the errors propagated. `zonal_velocity_mps` converts the drift relative to System II into a physical velocity in metres per second at the spot's latitude. `predict` gives the expected longitude at a future epoch with the one-sigma cone. The module also renders a PNG panel with the measured points, the fit, and the sigma band, and exports a CSV in the community format. The JUPOS convention of publishing drift as degrees per 30 days is handled in the report text.

### 18.6 The legacy complete system

`grs_complete_system.py` is the oldest and largest module, inherited and adapted, and it is the part of the code that handles real video stacks. Its own docstring describes the full pipeline it implements: ingestion, quality control, calibration, score, select, align, stack, derotate, register, restore, LRGB, navigate, segment, measure, bootstrap, error budget, smooth, export, manifest, report. Much of the toolkit is self-contained: because the module was written before the project settled on scipy and astropy as dependencies, it implements its own Gaussian filter, FFT convolution, morphology, connected components, percentile clipping, bilinear resizing, and coordinate mapping — with graceful degradation flags when the real libraries are unavailable. It also holds the constants that the newer modules import: physical constants, the recommended sampling for a given telescope aperture, the spot size history back to 1880, and site presets for the observatory locations.

The desktop pipeline tries this module first on real images, and falls back to the raw frame when it fails — which the docstring admits happens a lot with single non-stacked frames. It is also what the CLI's video commands bind to, and it is the source of the `quick_measure_path` used when a FITS file has a proper timestamp. Reading the module is reading the project's history: the design patterns show what the author reached for before the modern layer existed, and the modern layer exists in part precisely because this module's scope was too broad to fix one thing at a time.

### 18.7 The standalone harnesses

`app/batch_prove.py` is the old proof suite: generate a number of independent synthetic frames, measure each through the precision and research stack, and write per-run directories plus a batch summary in JSON, CSV, and text, with a SPICE status file. `app/limb_validation.py` is the specific test that the modern campaigns absorb: it forces the spot near the limb and checks that the pipeline does not lock onto the central meridian with an excellent grade — the CM-lock failure mode that only appears when the spot is off-centre. Both remain usable as standalone scripts, and the modern tools supersede them for large runs.

### 18.8 Stack forensics and reports

`stack_report.py` answers "what did the stacker actually do" after a run: the frame counts, the quality distribution, the AP grid, the drizzle factor, the per-quality selection fractions. `result_report.py` formats the measurement results for humans: the dashboard table and the full report with headline, budgets, reference comparisons, and tips. `animation.py` produces the blink GIF and frames-with-timestamps animations that derotation quality control needs. `jupos_io.py` is the community database bridge: it writes the JUPOS measurement rows in the documented format and reads them back, so the project's measurements can join the long-running community effort.

### 18.9 Launchers and packaging

The repo root has five double-clickable `*.command` files for macOS. `RUN_ME.command` is the one to trust: it clears any quarantine attribute, finds Python 3, creates or repairs the `.venv`, installs the requirements with a progress message, runs the import check, and then hands off — it deliberately works even when the project folder was moved after a previous run. `Launch_Desktop.command` starts the Tk app, `Launch_CLI.command` opens a terminal at the project root, `Launch_GRS_Observatory.command` launches the web UI, and `Train_SPIRE_Background.command` is the overnight trainer with its own log file.

`scripts/` is the release side. `package_release.sh` builds the customer ZIP: it calls `build_mac_app.sh` to compile the PyInstaller app, then bundles the VERSION, the licence text, the docs that ship, and the models folder into `dist/GRS_Observatory_Release_vX.Y.Z`. `make_private_release.py` does the same thing for my own private builds with one extra step that is worth being open about: it strips docstrings and comments from the staged code, which makes a recovered copy of the app harder to read — a deterrent against casual re-licensing, not a secret. `watch_and_rebuild.py` watches `app/*.py` for changes and rebuilds the private release into `~/Downloads` while I develop. `notarize_mac.sh` submits the signed app to Apple's notarization service and staples the ticket, and `entitlements.plist` is the hardened-runtime entitlements file the signing step needs so the app can access its data folder and the network without tripping Gatekeeper.

One detail of the packaging contract matters for anyone reading the docs. The release ZIP ships `GRS_OBSERVATORY_BOOK.md` and `SECURITY.md` (the script copies both explicitly), and the desktop app's wiring test asserts the book file exists in the tree. So those two stay in `docs/` no matter how much the rest of the documentation is pruned. `ESSAY.md` and `HOW_TO_RUN.md` are repository documents — development aids — and are not copied into the customer release.

## 19. A Guided Reading Order

If this essay is the map, here is the order I would actually read the code in, and why.

Start with `planet_models.py` and the coordinate helpers at the top of `precision_engine.py` — fifteen minutes to know the units and the two-latitude problem. Then read `fit_limb_nav` and `make_cylindrical`: these are the functions whose correctness everything else inherits. Then `measure_grs_precision` with `_method_is_sane` open beside it, and notice how the blend logic protects the redness estimator. After that, `gold_standard.py` to understand that there is no single correct answer, and `publish_primary.py` to understand why the project still picks one. Then `champion_measure.py`, which is the most intricate single file and the best place to see how a pro-desk workflow is encoded as gates. By then the astronomy is familiar, so the imaging modules come naturally: `ser_io.py`, `frame_quality.py`, `ap_stacker.py`, then the planet-generalised variants. Read `synthetic_hq.py` before any of the tools, because every number in the campaigns is measured against its output. Read `nn_grs.py`'s docstrings but skim the training math unless you plan to train, and read `ai_hard_cases.py` for the scoping argument — it is the best short statement of where machine learning belongs in this project. Finally, read the tests for the claims you care about, and remember that the tools' JSONL caches are the reproducible evidence for every number quoted anywhere.

## 20. Closing Thoughts

This codebase is a good example of a property that is underrated in student projects: it knows what it does not know. The docstrings are full of paragraphs that begin with "important", "honest", "does not", and "scope". The renderer's comment about the two-degree latitude bug is more useful to a future reader than a perfect diagram would be. The publish module's history of the champion scoring bug is recorded with it. The limb-isophote sensitivity is measured and reported rather than hidden. The refusal to invent a NASA truth is enforced in code, not just recommended.

The engineering lessons are the same ones the measurements teach. Independent estimators with different failure modes beat one good estimator. A disagreement among methods is data, not noise. A definition is a choice, and the choice must be stated. Calibration by injection and recovery is stronger than calibration by confidence. The common-mode error that cannot be removed absolutely can be removed differentially, night over night. Memory, time, and straight talk about seeing are constraints that belong in the error budget.

If you take one thing from this walkthrough, take the number: 0.6 degrees of longitude per minute of time error. That single constant explains why this project has a fail-closed time parser, a professional ephemeris resolver with provenance, a Monte Carlo layer, and a discipline about observation timing that amateur imaging usually treats as a footnote. Everything in the twenty sections before this one is engineering in service of that number.

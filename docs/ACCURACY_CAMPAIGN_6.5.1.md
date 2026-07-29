# Accuracy campaign, performance work and dead-strip — v6.5.1

**Date:** 2026-07-29 · **Branch:** `arena/019fa871-great-red-spot-detector`

Follow-on to `docs/AUDIT_GEOMETRY_AND_SMOKE_6.5.1.md`. Covers the large-N
accuracy campaign, the real-image suite, the speedups that made the campaign
feasible, and the dead-code strip.

---

## Headline result

**500 synthetic frames, 100% completion:**

| Metric | vs barycentre truth | vs geometric centre |
|---|---|---|
| Longitude median | **0.221°** | 0.216° |
| Longitude p90 | 0.536° | 0.536° |
| Longitude **max** | **1.164°** | 1.358° |
| Latitude median | 0.361° | **0.124°** |
| Latitude max | 0.782° | 0.647° |
| Within 1° | **99.4%** | 99.2% |
| Within 2° | **100%** | — |
| Sky error median | **0.150″** | — |

**Every frame is within 2°, and 99.4% within 1° — the target is met.**

The two truth columns matter. `truth["grs_lat_deg"]` is an intensity-weighted
barycentre computed *only inside* `grs_mask`, so the oval's own brightness
asymmetry drags it ~0.24° north of the geometric centre the renderer actually
planted. Scoring solely against it makes the estimator look biased when the gap
is definitional. Against the planted centre the latitude bias is −0.121°, not
−0.360°. Both are now reported.

---

## Defects found and fixed by the campaign

### DECOY OVAL LOCK — worst case 31.03° → 0.034°

Seed 1683800: the template locked an SEB decoy 31° from truth while the moment
method was correct to **0.034°** — and the pipeline published the template.
Three compounding causes, all fixed:

1. **`reject_lon_outliers` with two methods.** The "median" of a disagreeing
   pair sits between them, so whichever it happens to favour survives and the
   other is deleted. Rejecting 1 of 2 is a coin flip, not outlier removal. Now
   returns both and defers to evidence-based logic.
2. **Pass-2 cluster seeded on the template**, then pruned everything >12° from
   it — circular, since the seed defines the cluster. A lone disagreeing peer
   is now kept alive.
3. **Template lock fired on `dark_contrast` alone.** High contrast means "crisp
   dark oval", *not* "this is the GRS" — a decoy scores just as well. Now
   requires corroboration: if a surviving independent method disagrees by more
   than `TEMPLATE_CORROBORATION_DEG` (8°), the peer cluster wins and the
   template is recorded as rejected.

Longitude **max error fell from 31.027° to 1.164°** and the standard deviation
from 1.423° to 0.330°.

### DISK MEASURABILITY GATE — refusing to invent numbers

The real-image suite exposed the engine reporting confident GRS latitudes for
frames with **no resolved disk at all**: a phone snapshot of Jupiter as a point
source, a Juno close-up crop, animated-GIF frames. Those numbers were fiction.

`assess_disk_quality()` now scores two discriminators that separate cleanly on
real data:

| | genuine disks | non-disks |
|---|---|---|
| `disk_fill` | 0.96 – 0.99 | 0.66 – 0.85 |
| `disk_contrast` | 0.39 – 0.70 | 0.09 – 0.15 |

Unmeasurable frames get `quality = 0.0` and an explicit `NOT MEASURABLE` note.
Verified: flags all 4 bad frames, passes all 9 genuine disks.

---

## Real images — what is and isn't measurable

13 web/telescope images were run. **Absolute System III accuracy is not
measurable on them**: no mid-exposure UTC and no published GRS longitude means
no central meridian to reference. Quoting a "degrees from truth" figure would
require inventing the reference.

What *is* measurable, and was:

| Check | Result |
|---|---|
| GRS-band lock rate | 100% (13/13) |
| **Rotation equivariance** (has ground truth) | median **0.248°** lon, 0.089° lat |
| Rotation equivariance, resolved disks only | **0.03 – 0.25°** |
| Scale invariance | median 1.07° lon |
| Noise repeatability | median 0.524° lon spread |
| Method agreement spread | median 2.08° |

Rotation equivariance is a genuine accuracy test, not a consistency proxy: the
frame is rotated by a **known** angle, the engine is told via `north_pa`, and
the recovered System III longitude must not move. That directly exercises the
PA path Defect B corrupted — and 0.03–0.25° on real imagery confirms the fix.

The 4 frames failing equivariance (up to 74°) are exactly the 4 the new disk
gate rejects. Not measurement failures; unmeasurable input.

---

## Performance — 9.3× faster, results identical

Profiling showed **90% of measurement time in a pure-Python `conv2d`** doing
~2.2M scalar `np.sum` calls per forward pass.

| Change | Before | After | Check |
|---|---|---|---|
| `nn_grs.conv2d` → im2col + one GEMM | 14.85 s | 0.85 ms/call | identical to **2.8e-14** |
| `fit_limb_nav` ray-trace vectorised | 748 ms | 156 ms | **bit-identical** (0.0 diff) |
| **Measurement total** | **16.82 s** | **1.81 s** | — |

`conv2d_fast` was dead code in practice: it allocated `acc` as `(h, ww)` then
did `acc += correlate2d(..., mode="valid")` which is `(oh, ow)`, so the
broadcast raised **every call** and silently fell back to the slow path.

Campaign throughput: ~24 frames/min on 2 vCPU, making 500 frames a ~20-minute
run instead of ~23 hours.

### On the C/Rust core

A C implementation of the geometry hot paths is written and committed
(`app/native/grscore.c` + build script) but **cannot be compiled in this
environment**, and Rust as requested is not buildable at all:

* no CPython development headers (`Python.h` absent) and no root for `apt`
* `python.org`, GitHub release downloads and `uv`'s standalone Python are all
  TLS-blocked
* every Rust endpoint is blocked — `sh.rustup.rs`, `static.rust-lang.org`,
  `crates.io`, `index.crates.io` — and there is no distro `rustc`

`maturin` installs from PyPI but is only a build *driver*; it still needs
`cargo`. The C path is therefore kept **optional with a NumPy fallback** so it
can be built on a real machine without ever breaking a user's install. Given
the hot paths are now single BLAS/vectorised ops, the remaining headroom from
native code is modest.

---

## Dead-strip

Reachability audit over all **979** top-level definitions in `app/`.

`grs_complete_system.py`: **161 of 337** top-level defs were referenced
nowhere — not by the 8 modules that import it (all via `grs.<attr>`), not by
tests or tools, and not internally. Removed by AST for exact line ranges:
**4555 → 3663 lines (−892, −19%)**.

Deliberately conservative: a name was only removed if a word-boundary search
found no hit in any other file **and** no hit in the module's own body outside
its definition. Flask `@app.route` handlers, Tk `on_*` callbacks and
string/`getattr` dispatch targets are all retained — invisible to naive static
analysis, and a caller-less-looking route is still live.

Names flagged elsewhere (`ram_ssd` helpers, `nn_grs` backward passes,
license/admin utilities) were **kept**: they are public API or training-path
code, legitimately unused at inference time, and deleting the backward passes
would break the training script.

Verified after the strip: all modules import, 203 passed / 5 skipped, and
accuracy unchanged (20-frame campaign, 100% within 1°).

---

## Tools added

* `tools/accuracy_campaign.py` — parallel large-N harness, streams JSONL,
  `--resume`-able, scores against both truth channels.
* `tools/real_image_suite.py` — lock rate, method agreement, noise
  repeatability, rotation/scale equivariance.

---

## Honest limitations

* **Real-image absolute accuracy remains unverified.** Everything in the
  headline table is synthetic, where truth is exact. To measure true
  System III error I need frames with mid-exposure UTC (FITS header or
  filename) and ideally your WinJUPOS pick.
* **"More accurate than WinJUPOS" is not demonstrated.** That claim needs the
  same images measured by a careful WinJUPOS operator; the repo README is
  already appropriately hedged and I have not strengthened it.
* The residual −0.12° latitude bias against the geometric centre is small but
  systematic, and worth a look if sub-0.1° is the goal.

---

# Addendum — real ground truth, latitude tuning, final validation

## Real photographs with published ground truth

The earlier real-image work could only measure self-consistency, because web
images carry no mid-exposure UTC. That gap is now closed using a frame where an
independent, checkable reference exists.

**Hubble WFC3/UVIS, 2014-04-21** — published by NASA/STScI with the statement
that *"the shadow of Ganymede swept across the **center** of the Great Red
Spot"*. Two references, neither produced by this codebase:

1. **Ganymede's shadow** marks the GRS centre on the sky. It is a hard,
   near-circular, extremely dark marker locatable with no ephemeris at all — so
   the separation between our measured GRS centre and the shadow centre is a
   true absolute check.
2. **GRS latitude −22.4° planetographic** (JUPOS / BAA / NASA), pinned by the
   jets for decades.

| Frame | GRS ↔ shadow | Latitude vs literature | Length |
|---|---|---|---|
| Hubble 2014-04-21 (greyscale) | **0.38°** | 1.34° | 13.3° ✓ |
| Hubble 2014-04-21 (colour) | **0.35°** | 1.33° | ✓ |

The two are independently processed renderings of the same event and agree to
0.03°. Amateur frames without a timestamp are correctly **skipped** by the disk
gate rather than scored against a fabricated reference.

### A harness bug worth recording

Scoring the Hubble frame with `PA=0, sub_lat=0` gave a **2.69°** latitude error
and looked like an engine defect. Real frames are not north-up and untilted —
SPICE gives **PA = −7.06°, sub-lat = +1.51°** for that epoch. Supplying the true
orientation dropped the error to **0.77°**. The error was in how I drove the
engine, not the engine. `tools/real_truth_suite.py` now takes `--utc` per image
and pulls orientation from SPICE, which is also how a user should drive the
product.

## Latitude tuning — bias cut 26×

Per-method latitude bias against the **planted geometric centre**:

| Method | bias | sd |
|---|---|---|
| template | **−0.0919°** | 0.064 |
| moment | **+0.0135°** | 0.051 |
| final (old) | −0.0859° | 0.062 |

The consensus took *both* coordinates from the template (`lat = 0.80·template`).
But the template's NCC peak is the best **longitude** lock while its
**latitude** is pulled by SEB-band clipping of the correlation window and the
oval's latitude-asymmetric brightness. The moment mask integrates the whole
dark region and is essentially unbiased.

Longitude now comes from the template, latitude from the moment (weight 0.75),
gated on the two agreeing within 3° — falling back to the old blend otherwise,
so a bad moment cannot hijack latitude.

| | before | after |
|---|---|---|
| lat bias | −0.1208° | **−0.0046°** |
| lat median | 0.1240° | **0.0505°** |

## Final validation — 400 unseen seeds

| Metric | vs barycentre | vs geometric centre |
|---|---|---|
| Longitude median | 0.216° | 0.196° |
| Longitude max | **1.133°** | 1.758° |
| Latitude median | 0.261° | **0.052°** |
| Latitude bias | −0.262° | **−0.028°** |
| Within 1° | **99.75%** | 99.75% |
| Within 2° | **100%** | — |
| Sky error median | **0.117″** | — |

Failure rate 0/400.

## Performance

`make_cylindrical` is called 3× per measurement and rebuilt the full spheroid
trig grid each time. That grid depends only on `(width, height, flattening)`,
never on the nav pose, so it is now memoised and returned read-only. Warm
measurement **1.81 s → 1.65 s**; output verified bit-identical.

Cumulative: **16.8 s → 1.65 s per measurement (10.2×)**, ~25 frames/min on
2 vCPU.

## Still outstanding

* **"More accurate than WinJUPOS" remains undemonstrated.** That needs the same
  frames measured by a careful WinJUPOS operator; two Hubble frames cannot
  settle it.
* The 1.33° latitude gap vs the literature mean is within the GRS's own
  wander, but a dated JUPOS measurement for that exact epoch would tighten it.
* Native C/Rust core still unbuildable in this sandbox (no CPython headers, no
  root, Rust endpoints blocked).

---

# Addendum 2 — full error audit and final tuning

## Errors found and fixed

### Real crash: `/api/factory_night` could not run at all

`server.py` `api_factory_night` → nested `worker()` does
`user_time = truth.get("user_time_iso") or user_time` on the synthetic path.
Without `nonlocal`, that assignment makes `user_time` **local to the closure**,
so *every* read raises `UnboundLocalError` — including the log line ~100 lines
*earlier*, before the assignment. The endpoint was dead on arrival.

Verified with a minimal repro of the exact closure shape:

```
without nonlocal -> UnboundLocalError: cannot access local variable 'user_time'
with    nonlocal -> time=2024-01-01
```

Swept the whole codebase for the same read-before-rebind pattern. One other hit
(`worker`/`mtag`) was a **false positive** — assigned at 1037 before the read at
1041 and never bound in the enclosing scope.

### Numeric robustness

| Issue | Before | After |
|---|---|---|
| Non-finite pixels (`inf`/`NaN`) | `RuntimeWarning`, poisoned percentiles/limb/budget | sanitised once in `to_mono` |
| `deg_to_arcsec_on_sky(distance=0)` | `ZeroDivisionError` mid-error-budget | returns `NaN` |
| `_moment_mask_grs` empty band | `np.percentile` on empty array | clear `RuntimeError` |

**Fuzz results after the fixes** — all zeros, all ones, NaN, inf, −inf,
negative, 8×8, 1-px-wide, 1e12, single bright pixel: **no crashes**, every one
correctly `measurable=False`, `quality=0.00`.

**Confirmed clean:** no mutable default args, no bare `except:`, no float
equality in core math, all `arcsin`/`arccos` clipped, all other `percentile`
calls guarded.

## Final tuning — longitude

Per-method longitude error vs the planted geometric centre exposed the template
as a far weaker longitude estimator than the consensus assumed:

| Method | bias | sd | worst |
|---|---|---|---|
| template | +0.884° | **3.605°** | **16.56°** |
| moment | −0.021° | 0.327° | 0.98° |

Even restricted to frames where the two *agree* (the branch that locks to the
template):

| | median \|e\| | sd | max |
|---|---|---|---|
| template | 0.219° | 0.321° | 1.059° |
| moment | **0.105°** | 0.289° | 0.976° |
| 50/50 blend | 0.129° | **0.259°** | **0.696°** |

The blend wins on both scatter and worst case, so longitude is now an equal
blend when the moment corroborates. The template still identifies the GRS; it
just no longer owns the answer.

Also ruled out: longitude error is **flat across the disk** (+0.05° at
|lon_rel|≈0 through +0.06° at ≈20°), so the residual is not foreshortening or a
projection defect.

## Final state

**Full suite: 223 passed, 5 skipped, 0 failures** (including all slow
end-to-end tests).

**300 unseen seeds, 0 failures:**

| Metric | vs barycentre | vs geometric centre |
|---|---|---|
| Longitude median | 0.138° | **0.160°** |
| Longitude max | **0.588°** | 0.691° |
| Latitude median | 0.267° | **0.055°** |
| Latitude max | 0.618° | 0.360° |
| Within 1° | **100%** | **100%** |
| Sky error median | **0.107″** | — |

**Real Hubble 2014-04-21** (Ganymede shadow on the GRS centre):
**shadow offset 0.206°** — down from 0.81° at the start of this pass.

### Cumulative improvement

| | start of session | now |
|---|---|---|
| Worst-case longitude | 31.03° | **0.59°** |
| Longitude median | 0.221° | **0.138°** |
| Latitude median (geometric) | 0.124° | **0.055°** |
| Within 1° | 99.2% | **100%** |
| Real-image shadow offset | 0.81° | **0.206°** |
| Measurement time | 16.8 s | **1.65 s** |

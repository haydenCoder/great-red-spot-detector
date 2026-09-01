# Code audit — 2026-08-31

**Repository:** `great-red-spot-detector` @ working tree on top of `fadd979` (Release 7.0.1)
**Scope asked for:** "audit my full code please to check for any error"
**Scope delivered:** every tracked file swept with a parser + a static analyser, then a
hand-read of the paths that actually ship results — the web server, the UI, and the
measurement glue (stackers, derotators, ephemeris, reporters). That is 199 tracked files,
156 of them Python, ~47.8 k lines in `app/` and ~9.5 k lines of tests across 52 files.

Deliberately **out of scope**: `app/desktop_app.py` (2 985 lines of Tkinter, a second UI
that shares none of the web code). It was swept by the same tools — it parses clean and
carries no undefined names — but nothing in it was re-read by hand or changed. Say the
word and that gets its own pass.

---

## 1. Verdict

The codebase is **not** in a dangerous state. The three classes of bug that silently ruin
a metrology repo came back clean on both the base commit and this tree:

| Check | Rule | Base `fadd979` | Now |
|---|---|---|---|
| Undefined names (typos that only blow up at runtime) | `F821` | 0 | 0 |
| Bare `except:` swallowing everything | `E722` | 0 | 0 |
| Mutable default arguments | `B006` | 0 | 0 |
| Syntax errors (whole tree, `compileall`) | `E9` | 0 | 0 |
| Markup: unbalanced tags / duplicate `id=` in `index.html` (168 ids) | — | — | 0 / none |

Suite, `pytest tests -m "not slow"`: **520 passed, 16 skipped, 1 failed** — and that one
(`test_smoke_detailed.py::TestEphemerisProvenance::test_cm_source_is_trusted_when_spice_available`)
fails identically on the untouched base commit in this environment, because `spiceypy` is
not installed and it asserts publication-grade CM provenance only when SPICE is present. The
base commit scored 501 passed / 16 skipped / 1 failed here, so this work added 19 passing
tests and broke none. (The 385 deselected tests are the `slow` campaign — `pytest -m slow`
takes about an hour on two cores; it is not a hang, it is `build_results` grinding through
206 rendered cases, so give it `-k` or `--n-dates` when you want a slice.)

Style noise is plentiful and mostly harmless (557 findings in `app/` under a broad
pylint+bugbear selection — mostly `F401` unused imports and `E702` semicolon-packed
lines). Four real defects were found and fixed, and the largest one produced no error,
no warning and no bad-looking number — which is exactly why it is worth writing down.

---

## 2. Finding A — the alignment-point latitude was wrong by up to 21°

**`app/jupiter_zonal_stacker.py::_ap_latitude`** decides which Jovian latitude band each
alignment point belongs to, and that band selects the zonal-wind rate used as the
derotation prior for the `JUP_ZONAL` method. The implementation was a thin-sky shortcut:

```python
Yp  = -X * sin(PA) + Y * cos(PA)      # undo parallactic angle
Yb  =  Yp * cos(sub_lat)              # "tilt by sub-Earth latitude"
lat = asin(Yb)
```

Two things are wrong with it, and its own docstring contradicts it: the docstring promises
*"for an AP at the disc centre this is `sub_lat_deg`"*, but at the centre `Y = 0`, so the
formula returns **0°** no matter what the sub-Earth latitude is. Measured against
`precision_engine.px_to_lonlat_vec` — the exact oblate-spheroid line-of-sight solve the
engine uses to publish the measurement — over a 400 px Jovian disc:

| Geometry | mean err | p99 err | max err | inside the GRS band |
|---|---|---|---|---|
| sub-lat 0°, PA 0° | 0.99° | 8.56° | 20.05° | 0.57° |
| sub-lat +3.0°, PA 343.4° (season extreme) | **2.74°** | **9.96°** | **20.80°** | **2.90°** |
| 2026-08-02 actual (sub-lat +0.67°) | 1.25° | 8.78° | 20.17° | 0.61° |

This is not a new bug class here: the CHANGELOG records that
`planetary_stacker._per_pixel_lat` / `_ap_latitudes` had *the same* sphere shortcut
("differed from the spheroid latitude by up to ~2.8 deg on Jupiter in the GRS band, which
mis-binned the per-latitude shear warp") and was fixed. `jupiter_zonal_stacker` is the
sibling module that never got the same treatment.

**Fixed** the way the sibling was fixed: `_ap_latitude` now delegates to
`px_to_lonlat_vec` (latitude is independent of CM III and distance, so only the
orientation and limb scale go into the `NavState`), keeping the old thin-sky form only as
the fallback for APs outside the fitted limb, where no real intersection exists. Verified
error after the change: **0.0e+00 deg** at every grid point tested, and the disc centre now
returns `sub_lat_deg` as documented.

Pinned by `tests/test_jupiter_zonal.py::TestApLatitude` (centre returns sub-lat;
agreement with the engine to 1e-6° across the disc for four geometries; off-disc still
finite). `tests/test_jupiter_zonal.py`: **11 passed**.

> Note on interpretation: this changes the *prior* the zonal stacker uses, so JUP_ZONAL
> stacks of existing captures may shift slightly. It moves them toward the latitude the
> rest of the pipeline publishes, which is the direction the last CHANGELOG entry already
> chose — but re-pin the accuracy campaigns if you cite JUP_ZONAL numbers.

---

## 3. Finding B — 17–30 s of every `stack_holy_grail` run was thrown away

**`app/holy_hybrid_stacker.py`**, step 4 of the flagship hybrid stacker, was:

```python
# 4) RBF velocity field per frame, quality-weighted
vfield = _fit_rbf_velocity_field(aps[valid], map_drift[k, valid], (h, w), ...)
```

inside the per-frame loop — and `vfield` was never read again. `_fit_rbf_velocity_field`
ends in a Gaussian elimination over every alignment point. Measured on this machine:
43 ms per call at 121 APs, 48 ms at 225, 74 ms at 441 → **17–30 s per 400-frame run**
spent computing a field nothing sampled. `warp_to_reference` consumes a single global
`(dx, dy)` (the equatorial-band median shift), and the rotation is applied by
`_track_and_derotate`, whose result *is* reported through `derot_deg` / `derot_source`.

Fixed by deleting the call. The audit's own near-miss is worth recording: a naive dead-code
removal breaks the run, because `sigma_rbf` — used only by that call — also feeds the
**published** field `rbf_smoothness_sigma` 85 lines later. `ruff`'s `F821` caught it; that
computation is restored verbatim so the number in `job_result.json` is unchanged, and
`_fit_rbf_velocity_field` now has no production caller at all (it is kept, with a note, for
the per-AP warp path and its tests).

Pinned by `tests/test_holy_hybrid.py`: `test_discarded_rbf_solve_stays_gone` (monkeypatches
the fitter with a spy — if anyone wires it back into the hot path the test fails) and
`test_rbf_smoothness_sigma_is_still_published`.

---

## 4. Finding C — the AVI writer promised an index it never wrote

**`app/ser_io.py::write_avi`** sets `AVIF_HASINDEX` (0x10) in the `avih` header, and the
frame loop even built `idx_entries = []` … and then dropped it, emitting
`hdrl + movi` with no `idx1` chunk. A file whose header advertises an index and has none is
the worst case for strict demuxers: ffmpeg scans and copes, DirectShow/VirtualDub-class
players fail to seek or refuse the stream, and lucky-imaging users are the population most
likely to hand these files to other tools.

Fixed: the index is built as the frames are written — one `<4sIII>` entry per frame
(`00db`, keyframe flag, offset from the start of the `movi` LIST body, byte size) — and
appended as an `idx1` chunk inside `movi`. `idx1` is 16 bytes per frame so it never needs a
pad byte, which keeps the recorded offsets exact for odd-width rows too.

Pinned by `tests/test_ser_io.py::TestAVIRoundTrip::test_avi_index_written`: flags, entry
count, and — for both a 64 px and a 5 px row width — that every offset lands on the right
`00db` chunk with the right size, plus the existing frame-by-frame round trip.
`tests/test_ser_io.py`: **16 passed**.

---

## 5. Finding D — `_robust_combine` could mis-stack silently

**`app/planetary_stacker.py::_robust_combine`** is the sigma-clipped weighted mean that
keeps cosmic rays and satellite transits out of the stack. With fewer than three frames it
took the plain path, which did `for w_, f in zip(weights, warped)` — a short weight list
silently drops frames from the mean while `wsum` only counts the zipped ones, so the output
still looks like a stack, just a wrongly-weighted one. Added:

```python
if len(weights) != n:
    raise ValueError(f"_robust_combine: {n} frames but {len(weights)} weights")
```

No caller can hit it today (`warped_frames` and `frame_wts` are appended in the same loop),
so this is a tripwire, not a behaviour change: it converts a future refactor slip from a
plausible-looking wrong answer into a clear error.

---

## 6. Finding E — text files written with whatever the machine's locale says

`Path.write_text(s)` and `open(p, "w")` without an `encoding=` use
``locale.getpreferredencoding(False)``. On the developer's Mac that is UTF-8 and nothing
ever goes wrong; under ``LC_ALL=C`` — cron, a container without a locale, some
LaunchServices spawns — it is US-ASCII, and a write containing σ, °, ″ or an em dash raises
``UnicodeEncodeError`` *after* the expensive part of the job has run.

`app/` had **26 text I/O calls with no explicit encoding** out of 138. Reading each one:
25 write `json.dumps(...)` with the default `ensure_ascii=True`, so their bytes are pure
ASCII and they were only latent — one keystroke away from live, since the obvious
"make the JSON readable" tweak (`ensure_ascii=False`) turns every one of them into a crash.
The 26th, `app/filter_wheel.py:269`, writes `filter_wheel_report_text(...)` — a human
report that contains an em dash — so it was a **live** bug for any non-UTF-8 environment.
Two CSV writes (`grs_drift.py:366`, `wind_analysis.py:285`) and two JSON reads
(`nasa_compare.py:67`, `nn_grs.py:689`) are in the same file group for consistency: ASCII
today, fragile tomorrow.

Fixed: `encoding="utf-8"` added at all 28 sites (12 of them in `app/server.py`, the paths
that write `job_result.json` for every run). `ruff`'s `PLW1514` only looks at `open()`, so
`write_text` — the form this codebase mostly uses — is invisible to it; hence a structural
guard instead.

Pinned by `tests/test_text_encoding.py`: an AST sweep of `app/` that fails on any
`write_text`/`read_text`/text-mode `open` without an encoding, and on any
`json.dumps(ensure_ascii=False)` written without one — with a self-test that proves the
checker catches the bug when it is reintroduced, and a count assertion that proves the
sweep is still matching something.

---

## 7. Dead code removed (all verified by reading the full function, not just the lint line)

| Site | What it was |
|---|---|
| `app/server.py:550` | `safe_name = sanitize_filename(...)` in `/api/upload` — never read. The stored name is server-generated (`{utcstamp}_{uuid8}{ext}`), so this was leftovers, **not** a path-traversal hole. The import went with it. |
| `app/filter_wheel.py:230` | `t_ref`, `epoch_s` — both superseded by `t_ref_s` / `dts`, which *are* passed to `combine_rgb`. |
| `app/grs_image_prep.py:281` | `best = None` — the clustering step below uses `b = cands[0]`. |
| `app/result_report.py:153` | `eq` unpacked in `format_human_report` and unused. Checked whether a report section had silently vanished: it has not — section 1 embeds `format_dashboard_table`, which prints `vs_WJ` and `Δsky_WJ` from that same data. |
| `app/all_methods_extra.py:580,611` | `raise RuntimeError(str(e))` → `... from e`, so the original traceback stays chained as the cause. |

Unused locals deliberately **kept** (each is a documented intermediate, not a mistake):
`fx` in `all_methods.py::_map_from_cyl` — longitude is deliberately *not* bilinearly
interpolated there, because lerping a wrapped `lon_iii` across the 0°/360° seam is a
catastrophic blend; the code recovers CM III and interpolates the continuous relative
longitude instead, which is the right call. `Xp`-style rotation terms, `b` in
`synthetic_hq.py` (the limb ellipse comes from `1 - X² - (Y/k)²`), and `stds_` in
`wind_analysis.py` are the same category.

---

## 8. Checked and *not* bugs (so nobody re-audits these)

- **All 9 `B023` "function uses loop variable" hits** (`precision_engine.py:1594`,
  `win_jupos_derotator.py:264`, …): every one of those closures is *called inside the same
  iteration*, so the late-binding trap does not apply.
- **All 16 `zip()`-without-`strict` hits in `app/`**: each is length-matched by construction
  — including the two `zip(items, items[1:])` pairwise-idiom sites (`deterioration_lab.py:336`,
  and the `vlbi_metrology.py` shapes loop, which appends exactly once per `(L, W)` so the
  pairing is right). Rather than spray `strict=True` everywhere, the one place where a slip
  would have silently corrupted a *stack* got the explicit guard (§5).
- **Job-state thread safety**: `_lock` guards both `_start()` (which test-and-sets
  `_job["running"]` atomically before returning an output dir) and `_finish()`; the
  deterioration panel has its own `_deterioration_lock` on every mutation. The 21
  `PLW0603` global-statement findings are in `desktop_app.py`/`cspeed.py`/`nn_grs.py`, not
  in the request path.
- **`/api/upload`**: extension allowlist enforced in both the hardened branch
  (`safe_upload_extension`, which also sanitises) and the fallback branch; `.py`-style
  targets cannot be written, and `assert_safe_process_path` re-checks on the way back out.
- **The 429 freeze** (fixed in the previous round) is still fixed: 172 mixed page+poll
  requests in a burst → 172 × HTTP 200.
- **`/api/*` polling**: one adaptive `/api/status` poll, not three endpoints per tick.
- **`except Exception` in the measurement path.** 139 *fully silent* `except …: pass`
  handlers exist in `app/`; the ones that can affect a published number are far fewer —
  64 across the fourteen core modules, and of those `nn_grs.py` holds 23 (model loading and
  optional metrics), while `precision_engine.py` has 2, `holy_hybrid_stacker.py` 1,
  `ap_stacker.py` 1. Read in context, they are import fallbacks and optional-metric guards;
  none of them wraps a step whose failure could be reported as a successful measurement.
- **All 14 `PLW2901` loop-variable redefinitions**: 6 are the deliberate
  `for frame in frames: frame = crop(frame)` re-crop idiom, and the rest are
  normalise-then-use (`ln = ln.strip()`, `path = Path(path)`) or copy-on-write
  (`m = dict(m); m["rejected"] = True`) — none leaves a mutated value for the next
  iteration to misuse.
- **`F401` (141 unused imports) is confirmed harmless *for runtime***: every undefined name
  those could have masked would surface as `F821`, and the tree has zero.

---

## 9. Still open — your call, not mine

1. **`holy_hybrid_stacker` step 5 still does not rotate.** The comment block calls it
   "WinJUPOS-style derotation" but the shipped path applies an equatorial *shift only*
   (`derot_deg = 0.0`, `derot_source = "shift-only (no rotation)"` — the result struct is
   honest, the comments were not). Making it actually rotate (e.g. via
   `win_jupos_derotator._rotate_about_centre`) changes measured longitudes, so it must come
   with a re-pinned campaign, not an audit drive-by.
2. **Silent swallows deserve a policy.** 145 `try` blocks in `app/` are long enough to hide
   a real failure (that is what `PLW0717` counts). Nothing in the core measurement path is
   currently wrong (§8), but the next `except Exception: pass` around a new step will be the
   one that bites. Cheap policy: in `app/` measurement modules only, `except Exception as e:`
   must append a note that reaches `FULL_REPORT.txt`, as `deterioration_lab.safeSection`
   already does. `ruff` can enforce it once `TRY300`/`S110` are enabled for those paths.
3. **`desktop_app.py` unread** (2 985 lines, 56 `except Exception`). It sweeps clean; it has
   not been read.
4. **`/api/nn/train` double-start window.** `get_train_status()["running"]` is checked in the
   request while the flag is set inside the worker thread, so two clicks inside the
   thread-start latency could both launch a trainer on the same checkpoint. The UI disables
   the button while a job runs, which is why it has never bitten; a test-and-set inside
   `_lock` would close it properly.
5. **`tests/test_spice_geometry.py`** has failed on the base commit too (needs `spiceypy`,
   not installed here) — pre-existing, not caused by this work.
6. **Style debt**: `F401` × 141 and `E702` × 106 in `app/`. `ruff check --fix` would clear
   most of it in one commit; it was not done here because that commit would bury the four
   behavioural fixes above in 200 cosmetic hunks.

---

## 10. How to re-run all of it

```bash
# parser + correctness sweep (expect: All checks passed!)
.venv/bin/python -m compileall -q app tests tools scripts
.venv/bin/ruff check --isolated --select F821,E722,B006,E9 --preview app tests tools scripts

# the broad sweep behind the tables in §1
.venv/bin/ruff check --isolated --select F,B,PLW,E7,E9 --ignore E741 --preview --statistics app

# the four regression tests this audit added
.venv/bin/python -m pytest tests/test_jupiter_zonal.py tests/test_holy_hybrid.py \
                          tests/test_ser_io.py tests/test_ui_wiring.py -q

# and the before/after comparison, honestly done against the base commit
git worktree add -f /tmp/base fadd979 && (cd /tmp/base && ruff check … app)

# the interactive parts, driven in a real DOM (needs `npm i jsdom`)
node ~/uitest/ui2.mjs          # 42 assertions: drop overlay, zoom/pan, pill clock
```

## 11. Files touched by this audit

| File | Change |
|---|---|
| `app/jupiter_zonal_stacker.py` | `_ap_latitude` → exact spheroid solve + `_ap_on_disk`, thin-sky fallback kept |
| `app/holy_hybrid_stacker.py` | discarded per-frame RBF solve removed; `sigma_rbf` kept for the published diagnostic; helper documented as unwired |
| `app/ser_io.py` | real `idx1` index written inside `movi` |
| `app/planetary_stacker.py` | `_robust_combine` frame/weight length guard |
| `app/server.py` | dead `safe_name` + its unused import removed |
| `app/filter_wheel.py`, `app/grs_image_prep.py`, `app/result_report.py` | dead locals removed |
| `app/filter_wheel.py`, `app/server.py`, `app/nasa_compare.py`, `app/grs_drift.py`, `app/wind_analysis.py`, `app/cli.py`, `app/batch_prove.py`, `app/limb_validation.py`, `app/nn_grs.py`, `app/product_core.py`, `app/desktop_pipeline.py`, `app/desktop_app.py` | explicit `encoding="utf-8"` on every text read/write (28 sites) |
| `app/all_methods_extra.py` | `raise … from e` at both sites |
| `app/precision_engine.py:2101` | the SPIRE-Net rescue note hard-coded `w=0.18` while the line above set `wnn = 0.18`; the note now interpolates `wnn`, so retuning the blend weight cannot leave a stale number in the published notes. Surfaced by `F541` — an f-string with no placeholder is usually a lost interpolation. |
| `app/ephemeris_pro.py`, `app/gold_standard.py` | two spurious `f` prefixes on placeholder-less strings. The remaining two `F541` hits (`filter_wheel.py:306`, `precision_engine.py:2157`) are the known false positive: implicit concatenation where only the neighbouring literal needs the prefix — left alone on purpose. |
| `tests/test_jupiter_zonal.py` | +3 tests (`TestApLatitude`) |
| `tests/test_holy_hybrid.py` | +2 tests (dead-solve spy, published-diagnostic pin) |
| `tests/test_ser_io.py` | +1 test (`test_avi_index_written`) |
| `tests/test_text_encoding.py` | new: locale-independent text I/O guard (+ its own self-test) |
| `CHANGELOG.md` | audit entry + the UI round described below |

The same working tree also carries the second UI round (whole-window file drop, preview
zoom/pan, elapsed-time status pill) and 11 new `tests/test_ui_wiring.py` guards for them;
those are described in the CHANGELOG entry for this release rather than here, since none of
them changed a number anyone publishes.


---

## Addendum — 2026-09-01 (UI round 3: one click, dead endpoints, one self-inflicted defect)

The third round was driven by *"make sure everything in my code is in this UI and no more
multi-night or something, I want only one click for everything"*. Auditing that claim
meant mapping all 34 Flask routes onto page controls, which found three things.

| Finding | Evidence | Decision |
| --- | --- | --- |
| **Two endpoints had no UI at all** | `POST /api/sharpen` (`server.py:1837`, the only way to reach wavelet/unsharp/Richardson-Lucy sharpening) and `GET /api/resolutions` (`server.py:464`) were referenced by nothing in `app.js`. Earlier greps for `fetch("/api/…` missed the job endpoints because those are dispatched through `startJob(url, …)` — the coverage check now looks at both. | Sharpen Lab added to the Preview pane; resolution picker now labels itself from the server table. Both are wired, not removed: they are science features that had simply never been connected. |
| **`/api/output/<job_id>/<path:filename>` has no caller anywhere** (app, tools, tests) | `_find_output_dir(job_id)` + traversal-guarded `send_from_directory`; nothing emits the URL, unlike `/api/file?path=…` which the server puts into JSON payloads. | **Left alone and unwired.** The route is a working, hardened read-only API that an external tool can use; inventing a UI around an unknown `job_id` convention would be guesswork. Noted as an orphan rather than deleted. |
| **A7 — duplicated test classes** | `tests/test_ser_io.py` defined `TestSERRobustness`/`TestAVIRoundTrip` twice (112/205); the second definitions shadowed the first, so the first copy's edits would have been inert. Introduced by the previous commit while its patch scripts were being written; `ruff --select F811` caught it. | Duplicate pair deleted after diffing the two blocks to prove they were byte-identical (no coverage lost: 15 tests before and after). Every file touched in the last two rounds was re-scanned with an AST pass for top-level and in-class redefinitions — clean. |

One design decision worth recording: the one-click tail deliberately **does not** switch tabs.
`runTransits`/`runSessionPlan` take a `{quiet: true}` flag when the tail calls them, because a
click that filled each panel *and* yanked the visible pane four times in a row would leave the
user reading the last panel instead of the answer. The note under the button names what was
filled and tells you to press `4` for the still-running sweep.

### `/api/sharpen` — first exercise of the endpoint, and what the readout must therefore say

Nothing in the repo had ever called it, so the round wired it up and then measured it against a
deliberately soft synthetic capture (320×240 PNG, red-spot ellipse, 12 % uniform noise added
after the edges were drawn), through the live server with the exact JSON the browser sends
(`{"path": …, "method": …, "amount": 1.8}`):

| method | Laplacian variance before → after | ratio |
| --- | --- | --- |
| `wavelet` | 4.206e-3 → 3.032e-6 | ×0.0007 |
| `unsharp` | 4.206e-3 → 3.297e-2 | ×7.84 |
| `rl` | 4.206e-3 → 6.789e-3 | ×1.62 |

Two things came out of that. The numbers live in 1e-3…1e2, so `toFixed(1)` in the UI would have
printed `0.0 → 0.0` and hidden the entire result — the readout now uses adaptive precision plus a
ratio. And `wavelet` *lowers* Laplacian variance here, because that statistic counts noise as edge
energy and the wavelet step removes noise: the panel therefore states what the number is
(“higher means more edge energy, noise counts as edge energy too”) instead of implying that a
smaller number is a worse image. An unknown method returns a clean `400 {"error": "unknown method
'nope'"}`, which the panel prints verbatim; `amount` outside 0.1–4 is clamped server-side, so the
slider’s range is advisory rather than load-bearing.

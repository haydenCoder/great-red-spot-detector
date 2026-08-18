# Performance 7.0.0 — "Velocity Pro": the C core

*2026-08-09 · Every number below was measured on the validation box
(2-core Linux, Python 3.11, numpy 2.4, scipy ≥1.9, gcc 12.2) and is
reproducible with the commands in §6. Repository rule: claims are
measured, or they do not ship.*

## 1. Profile first — where the time actually goes

Premature optimisation is banned here. The 6.9-campaign profiles showed:

- **~91%** of `stack_ap` wall time inside `_lk_refine`.
- Inside it, ~95% in five `map_coordinates(order=3, mode="nearest",
  prefilter=False)` calls per iteration plus the numpy temporaries that
  build the normal equations — i.e. **per-call overhead, not physics**.
- 6.9's prefilter-once patch removed the redundant spline prefilter
  (1.3–1.8×, bit-exact). What remained: the sampling itself, invoked five
  times per iteration through Python.

Two more shared consumers of the same primitive: `warp_shift2d` /
`warp_field2d` (derotation remaps; scipy redoes the prefilter on every
call).

## 2. Design — `app/cspeed.c` (C99, ctypes, zero dependencies)

### Kernel 1: `cs_sample3` — batch cubic-spline coefficient sampling
Replicates `map_coordinates(C, [ys, xs], order=3, mode="nearest",
prefilter=False)`: uniform cubic B-spline basis on a *coefficient* array
(a `spline_filter` output), with scipy's `NI_EXTEND_NEAREST` boundary
(out-of-range taps clamp to the nearest edge coefficient — verified on
deliberately out-of-range coordinates).

### Kernel 2: `cs_lk_step` — one fused Lucas–Kanade pass
The five LK samples (value at (y,x) plus (y±1,x), (y,x±1) for the
central-difference gradients) share identical spline weights because
y±1 / x±1 keep the same fractional part. One fused sweep over a single
6×6 clamped tap neighbourhood per sample point therefore yields:

- the warped value `v = S(y−cy, x−cx)`,
- `gy = (S(y+1,x) − S(y−1,x))/2`, `gx = (S(y,x+1) − S(y,x−1))/2`,
- the accumulated normal equations of `A = [gy, gx]`, `rhs = diff`:
  `Σgy², Σgy·gx, Σgx², Σgy·d, Σgx·d`,

with optional window weights (`w` = NULL → plain LK, else the
window-in-gradients model `ref = w·img(p−c)` used by `rgb_combine`).
The 2×2 system is still solved by **the same LAPACK call** in Python, so
even the solver path is identical between C and numpy runs.

### Loader & build (`app/cspeed.py`, `tools/build_cspeed.py`)
- First import tries `app/_cspeed.so`, then an on-demand build via
  `cc/gcc/clang` (`CSPEED_CC` overrides), then — no compiler anywhere? —
  the **identical scipy path**, with `cspeed.status_note()` reporting
  which engine is live (soft-fail loudly: correct and slower, never
  silently different). `_cspeed.so` is gitignored by design; it is a
  build artifact, not source.
- Flags: `-O3 -std=c99 -fPIC -shared -fno-math-errno -fno-trapping-math`.
  **Never** `-ffast-math` or `-march=native`: answer-invariance across
  machines outranks the extra few percent.
- Ops escape hatch: `CSPEED=0` in the environment selects the pure path;
  `cspeed.set_enabled(False)` toggles at runtime (the A/B tests use it).

## 3. Parity contract (tests/test_cspeed.py)

Speed that changes the answer is a bug, not an optimisation.

| check | measured max|δ| | gate |
|---|---:|---:|
| `cs_sample3` vs scipy (2000–5000 pts incl. clamped) | **1.33e-15** | 1e-12 |
| `cs_lk_step` vs numpy replication — plain | **2.84e-14** | 1e-12 (scaled) |
| `cs_lk_step` vs numpy replication — windowed | **2.56e-13** | 1e-12 (scaled) |
| `field_warp3` vs `map_coordinates` | ~1e-15 | 1e-12 |
| `_lk_refine` A/B (with vs without C) | < 1e-9 | 1e-9 |
| `warp_shift2d` / `warp_field2d` A/B | < 1e-9 | 1e-9 |
| **`stack_ap` golden A/B — stack** | **3.47e-16** | 1e-9 |
| `stack_ap` frame-usage decisions | identical | exactly equal |

These δ's are summation-order noise (16 FMA-term reductions in C vs
BLAS-paired dot products in numpy) — **ten orders of magnitude below**
one detector count in unit-normalised data.

## 4. Measured speed (tools/cspeed_benchmark.py)

| workload | numpy/scipy | C core | speedup |
|---|---:|---:|---:|
| `stack_ap` end-to-end (12 frames, full AP grid) | 197.1 ms | 56.0 ms | **3.52×** |
| `_lk_refine` micro (32×32 crop, 4 iters) | 1.525 ms | 0.608 ms | 2.51× |
| `warp_field2d` 300×400 order 3 | 17.0 ms | 9.9 ms | 1.73× |
| `warp_shift2d` 400×300 order 3 | 10.7 ms | 7.4 ms | 1.45× |

Side effect: the LK-heavy test battery dropped 188.7 s (29 tests) →
107.2 s (62 tests, a superset).

Why the warp numbers are not larger (HONEST SCOPE): the C sampler only
replaces the *sampling* half of a warp; the other half is scipy's own
prefilter (already compiled C). v6.9 had already removed the redundant
prefilters inside LK loops, so the maximum available warp win is
bounded near 2× per pass. The stacker, conversely, spends most time in
Python-orchestrated iterative sampling — exactly what the fused kernel
eliminates. Unverified marketing numbers (GPU, threads, `-march=native`)
are deliberately absent.

## 5. What changes for you

Nothing to do: first import builds the core in a second. Notes:

- macOS ships `clang`; Linux ships `cc/gcc`. No Xcode/Visual Studio
  toolchain is required beyond a system compiler.
- Watch the note: `python -c "import sys; sys.path.insert(0,'app');
  import cspeed; print(cspeed.HAVE_C, cspeed.status_note())"`.
- `CSPEED=0 python app/cli.py video-stack ...` runs the identical pure
  path (useful for cross-machine reconciliation).

## 6. Reproduce everything

```bash
python tools/build_cspeed.py
.venv/bin/python -m pytest tests/test_cspeed.py -q     # parity contract
.venv/bin/python tools/cspeed_benchmark.py             # speed table
```

## 7. Future headroom (named, not claimed)

- Per-AP reference caching across frames (Python-side structural change;
  measured candidate, not yet shipped).
- Batched multi-AP LK (one C call per frame covering all APs).
- Row-parallel sampling (pthreads/OpenMP) behind a runtime guard.
Each must clear the same parity contract before it lands.

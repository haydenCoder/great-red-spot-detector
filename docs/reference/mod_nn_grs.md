# Module API: `nn_grs.py`

**Path:** `app/nn_grs.py`  
**Lines of code:** 534  
**Generated:** 2026-07-14T14:36:01.278037+00:00

## Module documentation

SPIRE-Net — multi-layer CNN for GRS localization (soft prior).

Architecture (NumPy, always available; optional PyTorch if installed):
  Input: 1×H×W cylindrical intensity map (default 64×128)
  Conv blocks → global features → heatmap + (lon_rel, lat) regression head

Auto-train:
  Generates synthetic Jupiter/GRS truth via synthetic_hq, projects to maps,
  trains with MSE on heatmap + coordinate loss. Weights saved under models/.

Important:
  Final metrology still uses physics/template/SPIRE-M. The network is a
  *soft prior* (ROI hint), not a replacement for injection calibration.

## Overview statistics

| Item | Count |
|------|------:|
| Classes | 1 |
| Top-level functions | 16 |
| Methods | 6 |

## Symbol index

- **class** `SpireNet` — line 143
  - `SpireNet.create()` — line 173
  - `SpireNet._pad_same()` — line 193
  - `SpireNet.forward()` — line 197
  - `SpireNet.predict_lonlat()` — line 232
  - `SpireNet.save()` — line 254
  - `SpireNet.load()` — line 273
- **function** `_relu()` — line 40
- **function** `_relu_bwd()` — line 44
- **function** `_sigmoid()` — line 48
- **function** `conv2d()` — line 52
- **function** `conv2d_fast()` — line 72
- **function** `maxpool2()` — line 97
- **function** `maxpool2_bwd()` — line 113
- **function** `conv2d_bwd()` — line 126
- **function** `_resize_map()` — line 291
- **function** `map_to_nn_input()` — line 313
- **function** `truth_to_targets()` — line 320
- **function** `get_train_status()` — line 346
- **function** `_sgd_step()` — line 360
- **function** `rng_noise()` — line 414
- **function** `auto_train()` — line 418
- **function** `predict_soft_prior()` — line 521

## Classes (full detail)

### class `SpireNet`

- **Defined at:** line 143
- **Methods:** 6

**Class docstring:**

Complicated multi-stage CNN:
  conv1 1→16 k3 → relu → pool
  conv2 16→32 k3 → relu → pool
  conv3 32→64 k3 → relu → pool
  flatten → FC 256 → relu → FC 128 → relu
  heads: heatmap (H'×W') via FC, coords (2,) via FC

#### Methods

##### `SpireNet.create(seed)`

- **Line:** 173–191

_No docstring. Inferred role: member of `SpireNet` used by the nn_grs subsystem._

**Signature notes:** Accepts parameters `seed`. See source `app/nn_grs.py` around line 173 for implementation.

**Related features:** Any feature that imports `nn_grs` may call this method.

---

##### `SpireNet._pad_same(self, x, k)`

- **Line:** 193–195

_No docstring. Inferred role: member of `SpireNet` used by the nn_grs subsystem._

**Signature notes:** Accepts parameters `self, x, k`. See source `app/nn_grs.py` around line 193 for implementation.

**Related features:** Any feature that imports `nn_grs` may call this method.

---

##### `SpireNet.forward(self, x, cache)`

- **Line:** 197–230

x: (1,H,W) or (H,W) normalized ~0..1
returns heatmap (8,16), coords (2,) in [0,1] for (x_frac, y_frac)

**Signature notes:** Accepts parameters `self, x, cache`. See source `app/nn_grs.py` around line 197 for implementation.

**Related features:** Any feature that imports `nn_grs` may call this method.

---

##### `SpireNet.predict_lonlat(self, cyl_map, cm_iii_deg)`

- **Line:** 232–252

Map network output to planetocentric lon/lat (map is lon_rel -90..90, lat 90..-90).

**Signature notes:** Accepts parameters `self, cyl_map, cm_iii_deg`. See source `app/nn_grs.py` around line 232 for implementation.

**Related features:** Any feature that imports `nn_grs` may call this method.

---

##### `SpireNet.save(self, path)`

- **Line:** 254–270

_No docstring. Inferred role: member of `SpireNet` used by the nn_grs subsystem._

**Signature notes:** Accepts parameters `self, path`. See source `app/nn_grs.py` around line 254 for implementation.

**Related features:** Any feature that imports `nn_grs` may call this method.

---

##### `SpireNet.load(path)`

- **Line:** 273–288

_No docstring. Inferred role: member of `SpireNet` used by the nn_grs subsystem._

**Signature notes:** Accepts parameters `path`. See source `app/nn_grs.py` around line 273 for implementation.

**Related features:** Any feature that imports `nn_grs` may call this method.

---

## Top-level functions (full detail)

### `_relu(x)`

- **Module:** `nn_grs.py`
- **Line:** 40–41

_No docstring in source._ This function is part of `nn_grs.py`. Open `app/nn_grs.py` at line 40 for the full implementation.

**Parameters:** `x`

**How to find callers:** search the repo for `_relu(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `_relu_bwd(x, g)`

- **Module:** `nn_grs.py`
- **Line:** 44–45

_No docstring in source._ This function is part of `nn_grs.py`. Open `app/nn_grs.py` at line 44 for the full implementation.

**Parameters:** `x, g`

**How to find callers:** search the repo for `_relu_bwd(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `_sigmoid(x)`

- **Module:** `nn_grs.py`
- **Line:** 48–49

_No docstring in source._ This function is part of `nn_grs.py`. Open `app/nn_grs.py` at line 48 for the full implementation.

**Parameters:** `x`

**How to find callers:** search the repo for `_sigmoid(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `conv2d(x, w, b)`

- **Module:** `nn_grs.py`
- **Line:** 52–69

**Docstring:**

x: (C_in, H, W), w: (C_out, C_in, kH, kW), b: (C_out,)
valid padding → out smaller.

**Parameters:** `x, w, b`

**How to find callers:** search the repo for `conv2d(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `conv2d_fast(x, w, b)`

- **Module:** `nn_grs.py`
- **Line:** 72–94

**Docstring:**

Faster conv using scipy if available, else conv2d.

**Parameters:** `x, w, b`

**How to find callers:** search the repo for `conv2d_fast(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `maxpool2(x)`

- **Module:** `nn_grs.py`
- **Line:** 97–110

**Docstring:**

2×2 max pool. Returns out, argmax linear index in each window for bwd.

**Parameters:** `x`

**How to find callers:** search the repo for `maxpool2(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `maxpool2_bwd(gout, idx, shape_in)`

- **Module:** `nn_grs.py`
- **Line:** 113–123

_No docstring in source._ This function is part of `nn_grs.py`. Open `app/nn_grs.py` at line 113 for the full implementation.

**Parameters:** `gout, idx, shape_in`

**How to find callers:** search the repo for `maxpool2_bwd(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `conv2d_bwd(x, w, gout)`

- **Module:** `nn_grs.py`
- **Line:** 126–139

**Docstring:**

Return gx, gw, gb.

**Parameters:** `x, w, gout`

**How to find callers:** search the repo for `conv2d_bwd(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `_resize_map(img, nh, nw)`

- **Module:** `nn_grs.py`
- **Line:** 291–310

_No docstring in source._ This function is part of `nn_grs.py`. Open `app/nn_grs.py` at line 291 for the full implementation.

**Parameters:** `img, nh, nw`

**How to find callers:** search the repo for `_resize_map(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `map_to_nn_input(cyl)`

- **Module:** `nn_grs.py`
- **Line:** 313–317

_No docstring in source._ This function is part of `nn_grs.py`. Open `app/nn_grs.py` at line 313 for the full implementation.

**Parameters:** `cyl`

**How to find callers:** search the repo for `map_to_nn_input(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `truth_to_targets(lon_iii, lat, cm_iii)`

- **Module:** `nn_grs.py`
- **Line:** 320–332

_No docstring in source._ This function is part of `nn_grs.py`. Open `app/nn_grs.py` at line 320 for the full implementation.

**Parameters:** `lon_iii, lat, cm_iii`

**How to find callers:** search the repo for `truth_to_targets(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `get_train_status()`

- **Module:** `nn_grs.py`
- **Line:** 346–357

_No docstring in source._ This function is part of `nn_grs.py`. Open `app/nn_grs.py` at line 346 for the full implementation.

**Parameters:** `(none / *args via definition)`

**How to find callers:** search the repo for `get_train_status(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `_sgd_step(net, x, heat_t, coord_t, lr)`

- **Module:** `nn_grs.py`
- **Line:** 360–411

**Docstring:**

One-sample SGD: full backprop through dense heads; light Hebbian/noise update on conv
so the multi-layer CNN features adapt without fragile full-conv reverse mode.

**Parameters:** `net, x, heat_t, coord_t, lr`

**How to find callers:** search the repo for `_sgd_step(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `rng_noise(shape, scale)`

- **Module:** `nn_grs.py`
- **Line:** 414–415

_No docstring in source._ This function is part of `nn_grs.py`. Open `app/nn_grs.py` at line 414 for the full implementation.

**Parameters:** `shape, scale`

**How to find callers:** search the repo for `rng_noise(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `auto_train(epochs, samples_per_epoch, lr, seed, use_existing)`

- **Module:** `nn_grs.py`
- **Line:** 418–518

**Docstring:**

Auto-train SPIRE-Net on synthetic labeled maps.
Runs synchronously (caller may thread). Updates _train_state for web UI.

**Parameters:** `epochs, samples_per_epoch, lr, seed, use_existing`

**How to find callers:** search the repo for `auto_train(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `predict_soft_prior(image, nav, cm_iii_deg)`

- **Module:** `nn_grs.py`
- **Line:** 521–534

**Docstring:**

Load net if trained and predict GRS lon/lat soft prior.

**Parameters:** `image, nav, cm_iii_deg`

**How to find callers:** search the repo for `predict_soft_prior(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

## Large-module guide

`nn_grs.py` is a large module (534 lines). When editing:

1. Prefer adding helpers near related symbols rather than new global state.
2. Keep I/O (files, network) at the edges.
3. After changes, run `python3 cli.py certify --n 15` from `app/`.


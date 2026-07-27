# Module API: `desktop_app.py`

**Path:** `app/desktop_app.py`  
**Lines of code:** 1240  
**Generated:** 2026-07-14T14:36:01.274483+00:00

## Module documentation

GRS Observatory — native macOS desktop app (no web browser).

Full feature set: synthetic (1080p–16K), max-stack process, pro ephemeris,
WinJUPOS, multi-epoch, hard-synth, factory night, SPIRE-Net, complete results.

## Overview statistics

| Item | Count |
|------|------:|
| Classes | 2 |
| Top-level functions | 3 |
| Methods | 47 |

## Symbol index

- **class** `LogBridge` — line 296
  - `LogBridge.__init__()` — line 297
  - `LogBridge.poll()` — line 301
- **class** `GRSDesktopApp` — line 310
  - `GRSDesktopApp.__init__()` — line 311
  - `GRSDesktopApp._build_menu()` — line 346
  - `GRSDesktopApp._license_show()` — line 361
  - `GRSDesktopApp._license_activate()` — line 380
  - `GRSDesktopApp._license_copy_machine()` — line 398
  - `GRSDesktopApp._manual_path()` — line 408
  - `GRSDesktopApp._open_manual()` — line 420
  - `GRSDesktopApp._open_science_claims()` — line 428
  - `GRSDesktopApp._about()` — line 439
  - `GRSDesktopApp._refresh_license_badge()` — line 457
  - `GRSDesktopApp._build_style()` — line 470
  - `GRSDesktopApp._build_ui()` — line 502
  - `GRSDesktopApp._show_help()` — line 713
  - `GRSDesktopApp._info_btn()` — line 718
  - `GRSDesktopApp._section()` — line 737
  - `GRSDesktopApp._label_row()` — line 744
  - `GRSDesktopApp._labeled_entry()` — line 752
  - `GRSDesktopApp._labeled_combo()` — line 757
  - `GRSDesktopApp._check()` — line 762
  - `GRSDesktopApp._action_btn()` — line 768
  - `GRSDesktopApp._set_busy()` — line 791
  - `GRSDesktopApp._log_ui()` — line 808
  - `GRSDesktopApp._results()` — line 813
  - `GRSDesktopApp._update_metrics()` — line 818
  - `GRSDesktopApp._show_preview()` — line 850
  - `GRSDesktopApp._tick()` — line 870
  - `GRSDesktopApp._run_bg()` — line 902
  - `GRSDesktopApp._mc()` — line 928
  - `GRSDesktopApp._inj()` — line 934
  - `GRSDesktopApp._float_opt()` — line 940
  - `GRSDesktopApp._aperture()` — line 949
  - `GRSDesktopApp._time_error()` — line 955
  - `GRSDesktopApp.on_clear()` — line 962
  - `GRSDesktopApp.on_open_outputs()` — line 969
  - `GRSDesktopApp.on_save_results()` — line 974
  - `GRSDesktopApp.on_open_file()` — line 987
  - `GRSDesktopApp.on_winjupos()` — line 1001
  - `GRSDesktopApp.on_synthetic()` — line 1014
  - `GRSDesktopApp.on_synthetic_only()` — line 1032
  - `GRSDesktopApp.on_process()` — line 1051
  - `GRSDesktopApp.on_ephemeris()` — line 1084
  - `GRSDesktopApp.on_multi()` — line 1123
  - `GRSDesktopApp.on_hard()` — line 1157
  - `GRSDesktopApp.on_factory()` — line 1189
  - `GRSDesktopApp.on_nn_train()` — line 1204
- **function** `app_base_dir()` — line 22
- **function** `bundle_code_dir()` — line 34
- **function** `main()` — line 1234

## Classes (full detail)

### class `LogBridge`

- **Defined at:** line 296
- **Methods:** 2

_No class docstring._

#### Methods

##### `LogBridge.__init__(self, q)`

- **Line:** 297–299

_No docstring. Inferred role: member of `LogBridge` used by the desktop_app subsystem._

**Signature notes:** Accepts parameters `self, q`. See source `app/desktop_app.py` around line 297 for implementation.

**Related features:** Any feature that imports `desktop_app` may call this method.

---

##### `LogBridge.poll(self)`

- **Line:** 301–307

_No docstring. Inferred role: member of `LogBridge` used by the desktop_app subsystem._

**Signature notes:** Accepts parameters `self`. See source `app/desktop_app.py` around line 301 for implementation.

**Related features:** Any feature that imports `desktop_app` may call this method.

---

### class `GRSDesktopApp`

- **Defined at:** line 310
- **Methods:** 45

_No class docstring._

#### Methods

##### `GRSDesktopApp.__init__(self)`

- **Line:** 311–343

_No docstring. Inferred role: member of `GRSDesktopApp` used by the desktop_app subsystem._

**Signature notes:** Accepts parameters `self`. See source `app/desktop_app.py` around line 311 for implementation.

**Related features:** Any feature that imports `desktop_app` may call this method.

---

##### `GRSDesktopApp._build_menu(self)`

- **Line:** 346–359

_No docstring. Inferred role: member of `GRSDesktopApp` used by the desktop_app subsystem._

**Signature notes:** Accepts parameters `self`. See source `app/desktop_app.py` around line 346 for implementation.

**Related features:** Any feature that imports `desktop_app` may call this method.

---

##### `GRSDesktopApp._license_show(self)`

- **Line:** 361–378

_No docstring. Inferred role: member of `GRSDesktopApp` used by the desktop_app subsystem._

**Signature notes:** Accepts parameters `self`. See source `app/desktop_app.py` around line 361 for implementation.

**Related features:** Any feature that imports `desktop_app` may call this method.

---

##### `GRSDesktopApp._license_activate(self)`

- **Line:** 380–396

_No docstring. Inferred role: member of `GRSDesktopApp` used by the desktop_app subsystem._

**Signature notes:** Accepts parameters `self`. See source `app/desktop_app.py` around line 380 for implementation.

**Related features:** Any feature that imports `desktop_app` may call this method.

---

##### `GRSDesktopApp._license_copy_machine(self)`

- **Line:** 398–406

_No docstring. Inferred role: member of `GRSDesktopApp` used by the desktop_app subsystem._

**Signature notes:** Accepts parameters `self`. See source `app/desktop_app.py` around line 398 for implementation.

**Related features:** Any feature that imports `desktop_app` may call this method.

---

##### `GRSDesktopApp._manual_path(self)`

- **Line:** 408–418

_No docstring. Inferred role: member of `GRSDesktopApp` used by the desktop_app subsystem._

**Signature notes:** Accepts parameters `self`. See source `app/desktop_app.py` around line 408 for implementation.

**Related features:** Any feature that imports `desktop_app` may call this method.

---

##### `GRSDesktopApp._open_manual(self)`

- **Line:** 420–426

_No docstring. Inferred role: member of `GRSDesktopApp` used by the desktop_app subsystem._

**Signature notes:** Accepts parameters `self`. See source `app/desktop_app.py` around line 420 for implementation.

**Related features:** Any feature that imports `desktop_app` may call this method.

---

##### `GRSDesktopApp._open_science_claims(self)`

- **Line:** 428–437

_No docstring. Inferred role: member of `GRSDesktopApp` used by the desktop_app subsystem._

**Signature notes:** Accepts parameters `self`. See source `app/desktop_app.py` around line 428 for implementation.

**Related features:** Any feature that imports `desktop_app` may call this method.

---

##### `GRSDesktopApp._about(self)`

- **Line:** 439–455

_No docstring. Inferred role: member of `GRSDesktopApp` used by the desktop_app subsystem._

**Signature notes:** Accepts parameters `self`. See source `app/desktop_app.py` around line 439 for implementation.

**Related features:** Any feature that imports `desktop_app` may call this method.

---

##### `GRSDesktopApp._refresh_license_badge(self)`

- **Line:** 457–467

_No docstring. Inferred role: member of `GRSDesktopApp` used by the desktop_app subsystem._

**Signature notes:** Accepts parameters `self`. See source `app/desktop_app.py` around line 457 for implementation.

**Related features:** Any feature that imports `desktop_app` may call this method.

---

##### `GRSDesktopApp._build_style(self)`

- **Line:** 470–499

_No docstring. Inferred role: member of `GRSDesktopApp` used by the desktop_app subsystem._

**Signature notes:** Accepts parameters `self`. See source `app/desktop_app.py` around line 470 for implementation.

**Related features:** Any feature that imports `desktop_app` may call this method.

---

##### `GRSDesktopApp._build_ui(self)`

- **Line:** 502–711

_No docstring. Inferred role: member of `GRSDesktopApp` used by the desktop_app subsystem._

**Signature notes:** Accepts parameters `self`. See source `app/desktop_app.py` around line 502 for implementation.

**Related features:** Any feature that imports `desktop_app` may call this method.

---

##### `GRSDesktopApp._show_help(self, key)`

- **Line:** 713–716

_No docstring. Inferred role: member of `GRSDesktopApp` used by the desktop_app subsystem._

**Signature notes:** Accepts parameters `self, key`. See source `app/desktop_app.py` around line 713 for implementation.

**Related features:** Any feature that imports `desktop_app` may call this method.

---

##### `GRSDesktopApp._info_btn(self, parent, key, side, padx)`

- **Line:** 718–735

Small circular ⓘ that explains a feature accurately.

**Signature notes:** Accepts parameters `self, parent, key, side, padx`. See source `app/desktop_app.py` around line 718 for implementation.

**Related features:** Any feature that imports `desktop_app` may call this method.

---

##### `GRSDesktopApp._section(self, parent, title, help_key)`

- **Line:** 737–742

_No docstring. Inferred role: member of `GRSDesktopApp` used by the desktop_app subsystem._

**Signature notes:** Accepts parameters `self, parent, title, help_key`. See source `app/desktop_app.py` around line 737 for implementation.

**Related features:** Any feature that imports `desktop_app` may call this method.

---

##### `GRSDesktopApp._label_row(self, parent, label, help_key)`

- **Line:** 744–750

_No docstring. Inferred role: member of `GRSDesktopApp` used by the desktop_app subsystem._

**Signature notes:** Accepts parameters `self, parent, label, help_key`. See source `app/desktop_app.py` around line 744 for implementation.

**Related features:** Any feature that imports `desktop_app` may call this method.

---

##### `GRSDesktopApp._labeled_entry(self, parent, label, var, help_key)`

- **Line:** 752–755

_No docstring. Inferred role: member of `GRSDesktopApp` used by the desktop_app subsystem._

**Signature notes:** Accepts parameters `self, parent, label, var, help_key`. See source `app/desktop_app.py` around line 752 for implementation.

**Related features:** Any feature that imports `desktop_app` may call this method.

---

##### `GRSDesktopApp._labeled_combo(self, parent, label, var, values, help_key)`

- **Line:** 757–760

_No docstring. Inferred role: member of `GRSDesktopApp` used by the desktop_app subsystem._

**Signature notes:** Accepts parameters `self, parent, label, var, values, help_key`. See source `app/desktop_app.py` around line 757 for implementation.

**Related features:** Any feature that imports `desktop_app` may call this method.

---

##### `GRSDesktopApp._check(self, parent, text, var, help_key)`

- **Line:** 762–766

_No docstring. Inferred role: member of `GRSDesktopApp` used by the desktop_app subsystem._

**Signature notes:** Accepts parameters `self, parent, text, var, help_key`. See source `app/desktop_app.py` around line 762 for implementation.

**Related features:** Any feature that imports `desktop_app` may call this method.

---

##### `GRSDesktopApp._action_btn(self, parent, text, cmd, help_key, color, secondary)`

- **Line:** 768–788

Full-width action with ⓘ on the right explaining exactly what it does.

**Signature notes:** Accepts parameters `self, parent, text, cmd, help_key, color, secondary`. See source `app/desktop_app.py` around line 768 for implementation.

**Related features:** Any feature that imports `desktop_app` may call this method.

---

##### `GRSDesktopApp._set_busy(self, busy, status)`

- **Line:** 791–806

_No docstring. Inferred role: member of `GRSDesktopApp` used by the desktop_app subsystem._

**Signature notes:** Accepts parameters `self, busy, status`. See source `app/desktop_app.py` around line 791 for implementation.

**Related features:** Any feature that imports `desktop_app` may call this method.

---

##### `GRSDesktopApp._log_ui(self, level, msg)`

- **Line:** 808–811

_No docstring. Inferred role: member of `GRSDesktopApp` used by the desktop_app subsystem._

**Signature notes:** Accepts parameters `self, level, msg`. See source `app/desktop_app.py` around line 808 for implementation.

**Related features:** Any feature that imports `desktop_app` may call this method.

---

##### `GRSDesktopApp._results(self, text)`

- **Line:** 813–816

_No docstring. Inferred role: member of `GRSDesktopApp` used by the desktop_app subsystem._

**Signature notes:** Accepts parameters `self, text`. See source `app/desktop_app.py` around line 813 for implementation.

**Related features:** Any feature that imports `desktop_app` may call this method.

---

##### `GRSDesktopApp._update_metrics(self, package)`

- **Line:** 818–848

_No docstring. Inferred role: member of `GRSDesktopApp` used by the desktop_app subsystem._

**Signature notes:** Accepts parameters `self, package`. See source `app/desktop_app.py` around line 818 for implementation.

**Related features:** Any feature that imports `desktop_app` may call this method.

---

##### `GRSDesktopApp._show_preview(self, path)`

- **Line:** 850–868

_No docstring. Inferred role: member of `GRSDesktopApp` used by the desktop_app subsystem._

**Signature notes:** Accepts parameters `self, path`. See source `app/desktop_app.py` around line 850 for implementation.

**Related features:** Any feature that imports `desktop_app` may call this method.

---

##### `GRSDesktopApp._tick(self)`

- **Line:** 870–900

_No docstring. Inferred role: member of `GRSDesktopApp` used by the desktop_app subsystem._

**Signature notes:** Accepts parameters `self`. See source `app/desktop_app.py` around line 870 for implementation.

**Related features:** Any feature that imports `desktop_app` may call this method.

---

##### `GRSDesktopApp._run_bg(self, name, fn)`

- **Line:** 902–926

_No docstring. Inferred role: member of `GRSDesktopApp` used by the desktop_app subsystem._

**Signature notes:** Accepts parameters `self, name, fn`. See source `app/desktop_app.py` around line 902 for implementation.

**Related features:** Any feature that imports `desktop_app` may call this method.

---

##### `GRSDesktopApp._mc(self)`

- **Line:** 928–932

_No docstring. Inferred role: member of `GRSDesktopApp` used by the desktop_app subsystem._

**Signature notes:** Accepts parameters `self`. See source `app/desktop_app.py` around line 928 for implementation.

**Related features:** Any feature that imports `desktop_app` may call this method.

---

##### `GRSDesktopApp._inj(self)`

- **Line:** 934–938

_No docstring. Inferred role: member of `GRSDesktopApp` used by the desktop_app subsystem._

**Signature notes:** Accepts parameters `self`. See source `app/desktop_app.py` around line 934 for implementation.

**Related features:** Any feature that imports `desktop_app` may call this method.

---

##### `GRSDesktopApp._float_opt(self, var)`

- **Line:** 940–947

_No docstring. Inferred role: member of `GRSDesktopApp` used by the desktop_app subsystem._

**Signature notes:** Accepts parameters `self, var`. See source `app/desktop_app.py` around line 940 for implementation.

**Related features:** Any feature that imports `desktop_app` may call this method.

---

##### `GRSDesktopApp._aperture(self)`

- **Line:** 949–953

_No docstring. Inferred role: member of `GRSDesktopApp` used by the desktop_app subsystem._

**Signature notes:** Accepts parameters `self`. See source `app/desktop_app.py` around line 949 for implementation.

**Related features:** Any feature that imports `desktop_app` may call this method.

---

##### `GRSDesktopApp._time_error(self)`

- **Line:** 955–959

_No docstring. Inferred role: member of `GRSDesktopApp` used by the desktop_app subsystem._

**Signature notes:** Accepts parameters `self`. See source `app/desktop_app.py` around line 955 for implementation.

**Related features:** Any feature that imports `desktop_app` may call this method.

---

##### `GRSDesktopApp.on_clear(self)`

- **Line:** 962–967

_No docstring. Inferred role: member of `GRSDesktopApp` used by the desktop_app subsystem._

**Signature notes:** Accepts parameters `self`. See source `app/desktop_app.py` around line 962 for implementation.

**Related features:** Any feature that imports `desktop_app` may call this method.

---

##### `GRSDesktopApp.on_open_outputs(self)`

- **Line:** 969–972

_No docstring. Inferred role: member of `GRSDesktopApp` used by the desktop_app subsystem._

**Signature notes:** Accepts parameters `self`. See source `app/desktop_app.py` around line 969 for implementation.

**Related features:** Any feature that imports `desktop_app` may call this method.

---

##### `GRSDesktopApp.on_save_results(self)`

- **Line:** 974–985

_No docstring. Inferred role: member of `GRSDesktopApp` used by the desktop_app subsystem._

**Signature notes:** Accepts parameters `self`. See source `app/desktop_app.py` around line 974 for implementation.

**Related features:** Any feature that imports `desktop_app` may call this method.

---

##### `GRSDesktopApp.on_open_file(self)`

- **Line:** 987–999

_No docstring. Inferred role: member of `GRSDesktopApp` used by the desktop_app subsystem._

**Signature notes:** Accepts parameters `self`. See source `app/desktop_app.py` around line 987 for implementation.

**Related features:** Any feature that imports `desktop_app` may call this method.

---

##### `GRSDesktopApp.on_winjupos(self)`

- **Line:** 1001–1012

_No docstring. Inferred role: member of `GRSDesktopApp` used by the desktop_app subsystem._

**Signature notes:** Accepts parameters `self`. See source `app/desktop_app.py` around line 1001 for implementation.

**Related features:** Any feature that imports `desktop_app` may call this method.

---

##### `GRSDesktopApp.on_synthetic(self)`

- **Line:** 1014–1030

_No docstring. Inferred role: member of `GRSDesktopApp` used by the desktop_app subsystem._

**Signature notes:** Accepts parameters `self`. See source `app/desktop_app.py` around line 1014 for implementation.

**Related features:** Any feature that imports `desktop_app` may call this method.

---

##### `GRSDesktopApp.on_synthetic_only(self)`

- **Line:** 1032–1049

Generate image only — no metrology (clear separate button).

**Signature notes:** Accepts parameters `self`. See source `app/desktop_app.py` around line 1032 for implementation.

**Related features:** Any feature that imports `desktop_app` may call this method.

---

##### `GRSDesktopApp.on_process(self)`

- **Line:** 1051–1082

_No docstring. Inferred role: member of `GRSDesktopApp` used by the desktop_app subsystem._

**Signature notes:** Accepts parameters `self`. See source `app/desktop_app.py` around line 1051 for implementation.

**Related features:** Any feature that imports `desktop_app` may call this method.

---

##### `GRSDesktopApp.on_ephemeris(self)`

- **Line:** 1084–1121

_No docstring. Inferred role: member of `GRSDesktopApp` used by the desktop_app subsystem._

**Signature notes:** Accepts parameters `self`. See source `app/desktop_app.py` around line 1084 for implementation.

**Related features:** Any feature that imports `desktop_app` may call this method.

---

##### `GRSDesktopApp.on_multi(self)`

- **Line:** 1123–1155

_No docstring. Inferred role: member of `GRSDesktopApp` used by the desktop_app subsystem._

**Signature notes:** Accepts parameters `self`. See source `app/desktop_app.py` around line 1123 for implementation.

**Related features:** Any feature that imports `desktop_app` may call this method.

---

##### `GRSDesktopApp.on_hard(self)`

- **Line:** 1157–1187

_No docstring. Inferred role: member of `GRSDesktopApp` used by the desktop_app subsystem._

**Signature notes:** Accepts parameters `self`. See source `app/desktop_app.py` around line 1157 for implementation.

**Related features:** Any feature that imports `desktop_app` may call this method.

---

##### `GRSDesktopApp.on_factory(self)`

- **Line:** 1189–1202

_No docstring. Inferred role: member of `GRSDesktopApp` used by the desktop_app subsystem._

**Signature notes:** Accepts parameters `self`. See source `app/desktop_app.py` around line 1189 for implementation.

**Related features:** Any feature that imports `desktop_app` may call this method.

---

##### `GRSDesktopApp.on_nn_train(self)`

- **Line:** 1204–1231

_No docstring. Inferred role: member of `GRSDesktopApp` used by the desktop_app subsystem._

**Signature notes:** Accepts parameters `self`. See source `app/desktop_app.py` around line 1204 for implementation.

**Related features:** Any feature that imports `desktop_app` may call this method.

---

## Top-level functions (full detail)

### `app_base_dir()`

- **Module:** `desktop_app.py`
- **Line:** 22–31

_No docstring in source._ This function is part of `desktop_app.py`. Open `app/desktop_app.py` at line 22 for the full implementation.

**Parameters:** `(none / *args via definition)`

**How to find callers:** search the repo for `app_base_dir(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `bundle_code_dir()`

- **Module:** `desktop_app.py`
- **Line:** 34–37

_No docstring in source._ This function is part of `desktop_app.py`. Open `app/desktop_app.py` at line 34 for the full implementation.

**Parameters:** `(none / *args via definition)`

**How to find callers:** search the repo for `bundle_code_dir(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

### `main()`

- **Module:** `desktop_app.py`
- **Line:** 1234–1236

_No docstring in source._ This function is part of `desktop_app.py`. Open `app/desktop_app.py` at line 1234 for the full implementation.

**Parameters:** `(none / *args via definition)`

**How to find callers:** search the repo for `main(`.

**Maintenance notes:** Prefer changing behavior here only with a synthetic `cli.py certify` or `batch_prove` check when the function touches measurement, navigation, or ephemeris.

---

## Large-module guide

`desktop_app.py` is a large module (1240 lines). When editing:

1. Prefer adding helpers near related symbols rather than new global state.
2. Keep I/O (files, network) at the edges.
3. After changes, run `python3 cli.py certify --n 15` from `app/`.


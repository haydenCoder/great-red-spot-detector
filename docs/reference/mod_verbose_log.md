# Module API: `verbose_log.py`

**Path:** `app/verbose_log.py`  
**Lines of code:** 54  
**Generated:** 2026-07-14T14:36:01.278950+00:00

## Module documentation

Thread-safe console log for the web UI.

## Overview statistics

| Item | Count |
|------|------:|
| Classes | 1 |
| Top-level functions | 0 |
| Methods | 9 |

## Symbol index

- **class** `ConsoleLog` — line 12
  - `ConsoleLog.__init__()` — line 13
  - `ConsoleLog.clear()` — line 19
  - `ConsoleLog.log()` — line 24
  - `ConsoleLog.info()` — line 34
  - `ConsoleLog.warn()` — line 37
  - `ConsoleLog.error()` — line 40
  - `ConsoleLog.ok()` — line 43
  - `ConsoleLog.debug()` — line 46
  - `ConsoleLog.since()` — line 49

## Classes (full detail)

### class `ConsoleLog`

- **Defined at:** line 12
- **Methods:** 9

_No class docstring._

#### Methods

##### `ConsoleLog.__init__(self, max_lines)`

- **Line:** 13–17

_No docstring. Inferred role: member of `ConsoleLog` used by the verbose_log subsystem._

**Signature notes:** Accepts parameters `self, max_lines`. See source `app/verbose_log.py` around line 13 for implementation.

**Related features:** Any feature that imports `verbose_log` may call this method.

---

##### `ConsoleLog.clear(self)`

- **Line:** 19–22

_No docstring. Inferred role: member of `ConsoleLog` used by the verbose_log subsystem._

**Signature notes:** Accepts parameters `self`. See source `app/verbose_log.py` around line 19 for implementation.

**Related features:** Any feature that imports `verbose_log` may call this method.

---

##### `ConsoleLog.log(self, message, level, verbose_only)`

- **Line:** 24–32

_No docstring. Inferred role: member of `ConsoleLog` used by the verbose_log subsystem._

**Signature notes:** Accepts parameters `self, message, level, verbose_only`. See source `app/verbose_log.py` around line 24 for implementation.

**Related features:** Any feature that imports `verbose_log` may call this method.

---

##### `ConsoleLog.info(self, msg, verbose_only)`

- **Line:** 34–35

_No docstring. Inferred role: member of `ConsoleLog` used by the verbose_log subsystem._

**Signature notes:** Accepts parameters `self, msg, verbose_only`. See source `app/verbose_log.py` around line 34 for implementation.

**Related features:** Any feature that imports `verbose_log` may call this method.

---

##### `ConsoleLog.warn(self, msg, verbose_only)`

- **Line:** 37–38

_No docstring. Inferred role: member of `ConsoleLog` used by the verbose_log subsystem._

**Signature notes:** Accepts parameters `self, msg, verbose_only`. See source `app/verbose_log.py` around line 37 for implementation.

**Related features:** Any feature that imports `verbose_log` may call this method.

---

##### `ConsoleLog.error(self, msg, verbose_only)`

- **Line:** 40–41

_No docstring. Inferred role: member of `ConsoleLog` used by the verbose_log subsystem._

**Signature notes:** Accepts parameters `self, msg, verbose_only`. See source `app/verbose_log.py` around line 40 for implementation.

**Related features:** Any feature that imports `verbose_log` may call this method.

---

##### `ConsoleLog.ok(self, msg, verbose_only)`

- **Line:** 43–44

_No docstring. Inferred role: member of `ConsoleLog` used by the verbose_log subsystem._

**Signature notes:** Accepts parameters `self, msg, verbose_only`. See source `app/verbose_log.py` around line 43 for implementation.

**Related features:** Any feature that imports `verbose_log` may call this method.

---

##### `ConsoleLog.debug(self, msg)`

- **Line:** 46–47

_No docstring. Inferred role: member of `ConsoleLog` used by the verbose_log subsystem._

**Signature notes:** Accepts parameters `self, msg`. See source `app/verbose_log.py` around line 46 for implementation.

**Related features:** Any feature that imports `verbose_log` may call this method.

---

##### `ConsoleLog.since(self, after_id)`

- **Line:** 49–51

_No docstring. Inferred role: member of `ConsoleLog` used by the verbose_log subsystem._

**Signature notes:** Accepts parameters `self, after_id`. See source `app/verbose_log.py` around line 49 for implementation.

**Related features:** Any feature that imports `verbose_log` may call this method.

---


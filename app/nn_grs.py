#!/usr/bin/env python3
"""
SPIRE-Net — multi-layer CNN for GRS localization (soft prior).

Architecture (NumPy, always available; optional PyTorch if installed):
  Input: 1×H×W cylindrical intensity map (default 64×128)
  Conv blocks → global features → heatmap + (lon_rel, lat) regression head

Production release: weights are FROZEN under app/models/spire_net_weights.npz.
Training entry points raise RuntimeError — do not retrain for normal use.
Inference (load weights as soft prior) remains available when UI enables NN.

Important:
  Final metrology uses limb/map/GS-ORANGE physics. The network is only an
  optional soft prior (ROI hint), not the published centre.
"""
from __future__ import annotations

import atexit
import json
import math
import os
import signal
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from verbose_log import CONSOLE

# Last active network for emergency flush on SIGTERM / atexit (not SIGKILL)
_ACTIVE_NET: Optional["SpireNet"] = None
_ACTIVE_TRAIN_META: Dict[str, Any] = {}

# Portable model paths — same CNN weights on every device (bundled + copied)
# NOTE: never use a bare APP_DIR name inside functions without a local binding;
# always resolve via _app_dir() so reloads / threads cannot hit NameError.
def _app_dir() -> Path:
    return Path(__file__).resolve().parent


def _resolve_model_paths() -> Tuple[Path, Path, Path]:
    """Return (model_dir, weights_path, meta_path); never raises."""
    try:
        from paths import ensure_models_present, model_dir as _model_dir
        md = ensure_models_present()
        if md is None:
            md = _model_dir()
    except Exception:
        md = _app_dir() / "models"
        md.mkdir(parents=True, exist_ok=True)
    return md, md / "spire_net_weights.npz", md / "spire_net_meta.json"


def _train_cache_dir() -> Path:
    """Writable folder for temporary training synthetics."""
    try:
        from paths import outputs_dir
        d = outputs_dir() / "nn_train_cache"
    except Exception:
        d = _app_dir() / "outputs" / "nn_train_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _train_log_dir() -> Path:
    """Durable train logs — survive app restart / most quits."""
    try:
        from paths import model_dir as _md
        d = _md() / "train_logs"
    except Exception:
        d = _resolve_model_paths()[0] / "train_logs"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _atomic_write_text(path: Path, text: str) -> None:
    """Write via temp file + replace so a crash mid-write doesn't wipe the file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    try:
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    except Exception:
        try:
            path.write_text(text, encoding="utf-8")
        except Exception:
            pass
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass


def _atomic_savez(path: Path, **arrays) -> None:
    """Atomic np.savez_compressed — refuse to write arrays that contain NaN/Inf."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    for k, v in arrays.items():
        a = np.asarray(v)
        if a.dtype.kind == "f" and (not np.isfinite(a).all()):
            raise ValueError(f"refusing to save corrupt array '{k}' (NaN/Inf present)")
    # np.savez_compressed APPENDS ".npz" when the target does not already end
    # in it. Naming the temp file "<name>.npz.tmp" therefore made numpy write
    # "<name>.npz.tmp.npz" while replace() looked for "<name>.npz.tmp" — the
    # FileNotFoundError was swallowed below and the fallback wrote the weights
    # directly to the destination (non-atomic, the exact failure this helper
    # exists to prevent), orphaning a full-size temp file every save.
    # Keep the ".npz" suffix LAST so numpy writes exactly the path we replace.
    tmp = path.with_suffix(path.suffix + ".tmp.npz")
    try:
        np.savez_compressed(tmp, **arrays)
        tmp.replace(path)
    except ValueError:
        raise
    except Exception as e:
        # Never fail silently here again: a broken atomic path degrades to a
        # NON-ATOMIC write, which can corrupt live weights if we crash mid-save.
        try:
            CONSOLE.warn(f"atomic weight save failed ({e!r}) — falling back to direct write")
        except Exception:
            pass
        try:
            np.savez_compressed(path, **arrays)
        except Exception:
            pass
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Weight integrity (prevent NaN/Inf from corrupting the CNN)
# ---------------------------------------------------------------------------

_WEIGHT_NAMES = ("w1", "b1", "w2", "b2", "w3", "b3", "wf1", "bf1", "wf2", "bf2", "wh", "bh", "wc", "bc")


def weights_are_finite(net: "SpireNet") -> bool:
    try:
        for name in _WEIGHT_NAMES:
            a = np.asarray(getattr(net, name))
            if a.dtype.kind == "f" and not np.isfinite(a).all():
                return False
            if np.max(np.abs(a)) > 1e6:  # exploding
                return False
        return True
    except Exception:
        return False


def snapshot_weights(net: "SpireNet") -> Dict[str, np.ndarray]:
    return {name: np.array(getattr(net, name), copy=True) for name in _WEIGHT_NAMES}


def restore_weights(net: "SpireNet", snap: Dict[str, np.ndarray]) -> None:
    for name, arr in snap.items():
        setattr(net, name, np.array(arr, copy=True))


def good_weights_path() -> Path:
    try:
        from paths import model_dir
        return model_dir() / "spire_net_weights.GOOD.npz"
    except Exception:
        return _resolve_model_paths()[1].with_name("spire_net_weights.GOOD.npz")


def save_good_backup(net: "SpireNet") -> bool:
    """Write known-good backup only if finite."""
    if not weights_are_finite(net):
        return False
    try:
        path = good_weights_path()
        arrays = {name: getattr(net, name) for name in _WEIGHT_NAMES}
        arrays["trained"] = np.array([1 if net.trained else 0])
        _atomic_savez(path, **arrays)
        return True
    except Exception as e:
        CONSOLE.debug(f"good backup fail: {e}")
        return False


def restore_from_good_backup(net: "SpireNet") -> bool:
    p = good_weights_path()
    if not p.exists():
        return False
    try:
        z = np.load(p)
        for name in _WEIGHT_NAMES:
            if name in z:
                setattr(net, name, np.array(z[name], copy=True))
        if weights_are_finite(net):
            CONSOLE.warn(f"SPIRE-Net restored from GOOD backup: {p}")
            return True
    except Exception as e:
        CONSOLE.debug(f"restore GOOD fail: {e}")
    return False


# ---------------------------------------------------------------------------
# Keep machine awake during train (macOS lid-close / idle sleep)
# ---------------------------------------------------------------------------

_caffeinate_proc = None  # type: ignore


def start_prevent_sleep(reason: str = "SPIRE-Net training") -> bool:
    """
    macOS: spawn `caffeinate -dims` so training continues with lid closed
    (as long as the machine is not fully powered off / battery dead).
    Other OS: best-effort no-op True.
    """
    global _caffeinate_proc
    stop_prevent_sleep()
    try:
        import platform
        import subprocess
        if platform.system() == "Darwin":
            # -d display -i idle -m disk -s system (prevents idle sleep; lid may still sleep
            # on some MacBooks unless "Prevent automatic sleeping when display is off" /
            # power adapter — caffeinate -s helps on AC).
            # Hold prevent-sleep until we terminate this process
            _caffeinate_proc = subprocess.Popen(
                ["caffeinate", "-dims"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            CONSOLE.ok(f"Prevent-sleep ON (caffeinate) · {reason}")
            CONSOLE.info(
                "Tip: keep Mac on power adapter. Lid close may still sleep on battery "
                "depending on macOS settings — use Train_SPIRE_Background.command for max durability."
            )
            return True
        # Linux: try systemd-inhibit if present
        if platform.system() == "Linux":
            _caffeinate_proc = subprocess.Popen(
                ["systemd-inhibit", "--what=idle:sleep", "--who=GRS-Observatory",
                 "--why", reason, "sleep", "infinity"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            CONSOLE.ok(f"Prevent-sleep ON (systemd-inhibit) · {reason}")
            return True
    except Exception as e:
        CONSOLE.warn(f"Prevent-sleep unavailable: {e}")
    return False


def stop_prevent_sleep() -> None:
    global _caffeinate_proc
    if _caffeinate_proc is not None:
        try:
            _caffeinate_proc.terminate()
        except Exception:
            pass
        try:
            _caffeinate_proc.wait(timeout=2)
        except Exception:
            try:
                _caffeinate_proc.kill()
            except Exception:
                pass
        _caffeinate_proc = None
        try:
            CONSOLE.info("Prevent-sleep OFF")
        except Exception:
            pass


def _write_live_report(meta: Dict[str, Any]) -> None:
    """Always-on-disk report (updated every epoch / emergency flush)."""
    log_dir = _train_log_dir()
    md, weights, _ = _resolve_model_paths()
    report = {
        "updated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "weights_path": str(weights),
        "model_dir": str(md),
        **meta,
    }
    _atomic_write_text(log_dir / "LATEST_TRAIN_REPORT.json", json.dumps(report, indent=2, default=str))
    # Human-readable
    lines = [
        "SPIRE-NET LIVE TRAIN REPORT (auto-saved)",
        f"Updated UTC: {report['updated_utc']}",
        f"Mode: {meta.get('mode', '?')}",
        f"Epoch: {meta.get('epoch', '?')} / {meta.get('epochs', '?')}",
        f"Strategy: {meta.get('strategy', '—')}",
        f"Starting loss: {meta.get('initial_loss')}",
        f"Current loss:  {meta.get('loss')}",
        f"Best loss:     {meta.get('best_loss')}",
        f"Final loss:    {meta.get('final_loss')}",
        f"Gain:          {meta.get('improvement')} ({meta.get('improvement_pct')}%)",
        f"Switches:      {meta.get('strategy_switches')}",
        f"Hours left:    {meta.get('hours_left')}",
        f"Message:       {meta.get('message')}",
        f"Weights:       {weights}",
        "",
        "This file is rewritten often so force-quit still leaves a report on disk.",
        "Also see: train_history.jsonl  and  spire_train_checkpoint.json",
    ]
    _atomic_write_text(log_dir / "LATEST_TRAIN_REPORT.txt", "\n".join(lines))
    # Append history line (append is mostly durable)
    try:
        with open(log_dir / "train_history.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "ts": report["updated_utc"],
                "epoch": meta.get("epoch"),
                "loss": meta.get("loss"),
                "best_loss": meta.get("best_loss"),
                "strategy": meta.get("strategy"),
                "gain": meta.get("improvement"),
                "gain_pct": meta.get("improvement_pct"),
                "message": meta.get("message"),
            }, default=str) + "\n")
            f.flush()
            os.fsync(f.fileno())
    except Exception:
        pass


def _emergency_flush(reason: str = "exit") -> None:
    """Save last network + report on SIGTERM/SIGINT/atexit. Cannot run on SIGKILL."""
    global _ACTIVE_NET, _ACTIVE_TRAIN_META
    net = _ACTIVE_NET
    if net is None:
        return
    try:
        net.trained = True
        net.save()
        meta = dict(_ACTIVE_TRAIN_META or {})
        meta["message"] = f"emergency_save ({reason}) · " + str(meta.get("message") or "")
        meta["emergency_save"] = reason
        _write_live_report(meta)
        try:
            CONSOLE.warn(f"SPIRE-Net emergency save ({reason})")
        except Exception:
            pass
    except Exception:
        pass


def _install_save_handlers() -> None:
    """Register once: flush weights on normal exit / Ctrl+C / kill (TERM)."""
    if getattr(_install_save_handlers, "_done", False):
        return

    def _sig(signum, frame):
        _emergency_flush(f"signal_{signum}")
        # re-raise default for SIGINT so process can end
        if signum == signal.SIGINT:
            raise KeyboardInterrupt

    try:
        atexit.register(lambda: _emergency_flush("atexit"))
    except Exception:
        pass
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, _sig)
        except Exception:
            pass
    # SIGHUP on Unix (terminal closed)
    if hasattr(signal, "SIGHUP"):
        try:
            signal.signal(signal.SIGHUP, _sig)
        except Exception:
            pass
    _install_save_handlers._done = True  # type: ignore


MODEL_DIR, WEIGHTS_PATH, META_PATH = _resolve_model_paths()
# Back-compat alias (some older call sites / docs)
APP_DIR = _app_dir()

# Fixed map size for the network
NN_H, NN_W = 64, 128


def _relu(x: np.ndarray) -> np.ndarray:
    return np.maximum(x, 0.0)


def _relu_bwd(x: np.ndarray, g: np.ndarray) -> np.ndarray:
    return g * (x > 0)


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -40, 40)))


def conv2d(x: np.ndarray, w: np.ndarray, b: np.ndarray) -> np.ndarray:
    """
    x: (C_in, H, W), w: (C_out, C_in, kH, kW), b: (C_out,)
    valid padding -> out smaller.

    Vectorised im2col via stride tricks + a single GEMM. The previous version
    looped over (out_channel, in_channel, i, j) in Python and called np.sum per
    output pixel -- 2.2M scalar reductions for one 3-layer forward pass, which
    made SPIRE-Net ~90% of total measurement time. This is numerically
    identical (same accumulation, float64) but two orders of magnitude faster.
    """
    x = np.ascontiguousarray(x, dtype=np.float64)
    w = np.ascontiguousarray(w, dtype=np.float64)
    cin, h, w_ = x.shape
    cout, _, kh, kw = w.shape
    oh, ow = h - kh + 1, w_ - kw + 1
    if oh <= 0 or ow <= 0:
        return np.zeros((cout, max(oh, 0), max(ow, 0)), dtype=np.float64)

    # (cin, oh, ow, kh, kw) sliding view without copying
    s0, s1, s2 = x.strides
    patches = np.lib.stride_tricks.as_strided(
        x, shape=(cin, oh, ow, kh, kw), strides=(s0, s1, s2, s1, s2), writeable=False
    )
    # -> (oh*ow, cin*kh*kw) @ (cin*kh*kw, cout)
    cols = patches.transpose(1, 2, 0, 3, 4).reshape(oh * ow, cin * kh * kw)
    wmat = w.reshape(cout, cin * kh * kw).T
    out = (cols @ wmat).T.reshape(cout, oh, ow)
    return out + np.asarray(b, dtype=np.float64).reshape(cout, 1, 1)


def conv2d_fast(x: np.ndarray, w: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Vectorised convolution.

    conv2d is already a single BLAS GEMM, so there is nothing to gain from a
    scipy path here. The old implementation allocated acc as (h, ww) and then
    did `acc += correlate2d(..., mode="valid")`, which is (oh, ow) -- the
    broadcast raised every call and silently fell back to the slow loop.
    """
    return conv2d(x, w, b)


def maxpool2(x: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """2×2 max pool. Returns out, argmax linear index in each window for bwd."""
    c, h, w = x.shape
    oh, ow = h // 2, w // 2
    out = np.zeros((c, oh, ow), dtype=np.float64)
    idx = np.zeros((c, oh, ow), dtype=np.int64)
    for ci in range(c):
        for i in range(oh):
            for j in range(ow):
                window = x[ci, 2 * i : 2 * i + 2, 2 * j : 2 * j + 2]
                k = int(np.argmax(window))
                out[ci, i, j] = window.ravel()[k]
                idx[ci, i, j] = k
    return out, idx


def maxpool2_bwd(gout: np.ndarray, idx: np.ndarray, shape_in: Tuple[int, int, int]) -> np.ndarray:
    c, h, w = shape_in
    gin = np.zeros(shape_in, dtype=np.float64)
    oh, ow = gout.shape[1], gout.shape[2]
    for ci in range(c):
        for i in range(oh):
            for j in range(ow):
                k = int(idx[ci, i, j])
                di, dj = divmod(k, 2)
                gin[ci, 2 * i + di, 2 * j + dj] = gout[ci, i, j]
    return gin


def conv2d_bwd(x: np.ndarray, w: np.ndarray, gout: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Return gx, gw, gb."""
    cout, cin, kh, kw = w.shape
    oh, ow = gout.shape[1], gout.shape[2]
    gw = np.zeros_like(w)
    gb = gout.sum(axis=(1, 2))
    gx = np.zeros_like(x)
    for oc in range(cout):
        for ic in range(cin):
            for i in range(oh):
                for j in range(ow):
                    gw[oc, ic] += gout[oc, i, j] * x[ic, i : i + kh, j : j + kw]
                    gx[ic, i : i + kh, j : j + kw] += gout[oc, i, j] * w[oc, ic]
    return gx, gw, gb


@dataclass
class SpireNet:
    """
    Complicated multi-stage CNN:
      conv1 1→16 k3 → relu → pool
      conv2 16→32 k3 → relu → pool
      conv3 32→64 k3 → relu → pool
      flatten → FC 256 → relu → FC 128 → relu
      heads: heatmap (H'×W') via FC, coords (2,) via FC
    """
    # weights
    w1: np.ndarray = field(default_factory=lambda: np.zeros(1))
    b1: np.ndarray = field(default_factory=lambda: np.zeros(1))
    w2: np.ndarray = field(default_factory=lambda: np.zeros(1))
    b2: np.ndarray = field(default_factory=lambda: np.zeros(1))
    w3: np.ndarray = field(default_factory=lambda: np.zeros(1))
    b3: np.ndarray = field(default_factory=lambda: np.zeros(1))
    wf1: np.ndarray = field(default_factory=lambda: np.zeros(1))
    bf1: np.ndarray = field(default_factory=lambda: np.zeros(1))
    wf2: np.ndarray = field(default_factory=lambda: np.zeros(1))
    bf2: np.ndarray = field(default_factory=lambda: np.zeros(1))
    wh: np.ndarray = field(default_factory=lambda: np.zeros(1))
    bh: np.ndarray = field(default_factory=lambda: np.zeros(1))
    wc: np.ndarray = field(default_factory=lambda: np.zeros(1))
    bc: np.ndarray = field(default_factory=lambda: np.zeros(1))
    # derived spatial after 3 pools from 64×128: after conv valid shrinks
    # we use fixed pad-to-same by edge pad before each conv to keep size
    trained: bool = False
    train_history: List[Dict[str, float]] = field(default_factory=list)

    @staticmethod
    def create(seed: int = 0) -> "SpireNet":
        rng = np.random.default_rng(seed)
        def w_init(shape):
            # He init
            fan = np.prod(shape[1:]) if len(shape) == 4 else shape[1]
            return rng.normal(0, math.sqrt(2.0 / max(fan, 1)), size=shape).astype(np.float64)

        net = SpireNet()
        net.w1 = w_init((16, 1, 3, 3)); net.b1 = np.zeros(16)
        net.w2 = w_init((32, 16, 3, 3)); net.b2 = np.zeros(32)
        net.w3 = w_init((64, 32, 3, 3)); net.b3 = np.zeros(64)
        # After pad-same conv + 3× pool on 64×128 → 8×16 × 64 = 8192
        flat = 64 * 8 * 16
        net.wf1 = w_init((256, flat)); net.bf1 = np.zeros(256)
        net.wf2 = w_init((128, 256)); net.bf2 = np.zeros(128)
        # heatmap 8×16 = 128
        net.wh = w_init((128, 128)); net.bh = np.zeros(128)
        net.wc = w_init((2, 128)); net.bc = np.zeros(2)
        return net

    def _pad_same(self, x: np.ndarray, k: int = 3) -> np.ndarray:
        p = k // 2
        return np.pad(x, ((0, 0), (p, p), (p, p)), mode="edge")

    def forward(self, x: np.ndarray, cache: bool = False) -> Tuple[np.ndarray, np.ndarray, Optional[dict]]:
        """
        x: (1,H,W) or (H,W) normalized ~0..1
        returns heatmap (8,16), coords (2,) in [0,1] for (x_frac, y_frac)
        """
        if x.ndim == 2:
            x = x[None, ...]
        x = x.astype(np.float64)
        # resize if needed
        if x.shape[1] != NN_H or x.shape[2] != NN_W:
            x = _resize_map(x[0], NN_H, NN_W)[None, ...]

        c: Dict[str, Any] = {}
        z1 = conv2d_fast(self._pad_same(x), self.w1, self.b1)
        a1 = _relu(z1)
        p1, i1 = maxpool2(a1)
        z2 = conv2d_fast(self._pad_same(p1), self.w2, self.b2)
        a2 = _relu(z2)
        p2, i2 = maxpool2(a2)
        z3 = conv2d_fast(self._pad_same(p2), self.w3, self.b3)
        a3 = _relu(z3)
        p3, i3 = maxpool2(a3)
        flat = p3.reshape(-1)
        h1 = _relu(self.wf1 @ flat + self.bf1)
        h2 = _relu(self.wf2 @ h1 + self.bf2)
        heat_logits = self.wh @ h2 + self.bh
        heat = _sigmoid(heat_logits).reshape(8, 16)
        coords = _sigmoid(self.wc @ h2 + self.bc)  # [0,1]^2
        if cache:
            c.update(dict(x=x, z1=z1, a1=a1, p1=p1, i1=i1, z2=z2, a2=a2, p2=p2, i2=i2,
                          z3=z3, a3=a3, p3=p3, i3=i3, flat=flat, h1=h1, h2=h2,
                          heat_logits=heat_logits, heat=heat, coords=coords))
            return heat, coords, c
        return heat, coords, None

    def predict_lonlat(self, cyl_map: np.ndarray, cm_iii_deg: float) -> Dict[str, float]:
        """Map network output to planetocentric lon/lat (map is lon_rel -90..90, lat 90..-90)."""
        heat, coords, _ = self.forward(cyl_map, cache=False)
        # peak of heatmap
        j = np.unravel_index(np.argmax(heat), heat.shape)
        # blend peak + regression head
        y_f = 0.6 * (j[0] + 0.5) / 8.0 + 0.4 * float(coords[1])
        x_f = 0.6 * (j[1] + 0.5) / 16.0 + 0.4 * float(coords[0])
        lon_rel = -90.0 + x_f * 180.0
        lat = 90.0 - y_f * 180.0
        lon = (cm_iii_deg + lon_rel) % 360.0
        conf = float(heat.max())
        return {
            "lon_iii_deg": float(lon),
            "lat_deg": float(lat),
            "confidence": conf,
            "length_deg": 12.0,
            "width_deg": 8.0,
            "method": "spire_net",
            "score": conf,
        }

    def save(self, path: Optional[Path] = None, quiet: bool = False) -> None:
        """Atomic durable save — refuses NaN/Inf; never overwrites good weights with corrupt ones."""
        global _ACTIVE_NET
        _ACTIVE_NET = self
        if not weights_are_finite(self):
            msg = "SPIRE-Net save BLOCKED — weights contain NaN/Inf or exploded; keeping last good file"
            CONSOLE.error(msg)
            # try restore from GOOD backup into memory so subsequent steps are sane
            restore_from_good_backup(self)
            raise ValueError(msg)
        try:
            from paths import ensure_models_present, model_dir
            ensure_models_present()
            path = path or (model_dir() / "spire_net_weights.npz")
            meta_path = model_dir() / "spire_net_meta.json"
        except Exception:
            path = path or WEIGHTS_PATH
            meta_path = META_PATH
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        arrays = dict(
            w1=self.w1, b1=self.b1, w2=self.w2, b2=self.b2, w3=self.w3, b3=self.b3,
            wf1=self.wf1, bf1=self.bf1, wf2=self.wf2, bf2=self.bf2,
            wh=self.wh, bh=self.bh, wc=self.wc, bc=self.bc,
            trained=np.array([1 if self.trained else 0]),
        )
        _atomic_savez(path, **arrays)
        # Maintain known-good backup for recovery
        try:
            save_good_backup(self)
        except Exception:
            pass
        meta = {
            "architecture": "SPIRE-Net CNN 16-32-64 + dual head (heatmap+coords)",
            "input": f"1x{NN_H}x{NN_W}",
            "trained": self.trained,
            "history": self.train_history[-80:],
            "path": str(path),
            "portable": True,
            "note": "Ship this file with the app so every device has the same CNN",
            "saved_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        _atomic_write_text(meta_path, json.dumps(meta, indent=2, default=str))
        # Also refresh bundled copy when running from source (owner master weights)
        try:
            from paths import bundled_model_dir
            import shutil
            b = bundled_model_dir()
            b.mkdir(parents=True, exist_ok=True)
            # atomic-ish copy via temp
            for src_name, dst_name in (
                (path, b / "spire_net_weights.npz"),
                (meta_path, b / "spire_net_meta.json"),
            ):
                if src_name.exists():
                    tmp = dst_name.with_suffix(dst_name.suffix + ".tmp")
                    shutil.copy2(src_name, tmp)
                    tmp.replace(dst_name)
        except Exception:
            pass
        if not quiet:
            CONSOLE.ok(f"SPIRE-Net weights saved: {path} (portable, atomic)")

    @staticmethod
    def load(path: Optional[Path] = None) -> Optional["SpireNet"]:
        try:
            from paths import ensure_models_present, model_dir, bundled_model_dir
            ensure_models_present()
            candidates = []
            if path is not None:
                candidates.append(Path(path))
            candidates.append(model_dir() / "spire_net_weights.npz")
            candidates.append(bundled_model_dir() / "spire_net_weights.npz")
            candidates.append(WEIGHTS_PATH)
        except Exception:
            candidates = [Path(path)] if path else [WEIGHTS_PATH]
        use = None
        for c in candidates:
            if c is not None and Path(c).exists() and Path(c).stat().st_size > 1000:
                use = Path(c)
                break
        if use is None:
            CONSOLE.warn("SPIRE-Net weights missing — train once or copy models/spire_net_weights.npz")
            return None
        z = np.load(use)
        net = SpireNet()
        for k in ("w1", "b1", "w2", "b2", "w3", "b3", "wf1", "bf1", "wf2", "bf2", "wh", "bh", "wc", "bc"):
            setattr(net, k, z[k])
        net.trained = bool(z["trained"][0]) if "trained" in z else True
        meta_path = use.with_name("spire_net_meta.json")
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
                net.train_history = meta.get("history", [])
            except Exception:
                pass
        CONSOLE.info(f"SPIRE-Net loaded from {use} (trained={net.trained})")
        return net


def _resize_map(img: np.ndarray, nh: int, nw: int) -> np.ndarray:
    h, w = img.shape
    ys = (np.linspace(0, h - 1, nh)).astype(np.float64)
    xs = (np.linspace(0, w - 1, nw)).astype(np.float64)
    y0 = np.floor(ys).astype(int)
    x0 = np.floor(xs).astype(int)
    y1 = np.clip(y0 + 1, 0, h - 1)
    x1 = np.clip(x0 + 1, 0, w - 1)
    wy = ys - y0
    wx = xs - x0
    out = np.zeros((nh, nw), dtype=np.float64)
    for i in range(nh):
        for j in range(nw):
            out[i, j] = (
                img[y0[i], x0[j]] * (1 - wy[i]) * (1 - wx[j])
                + img[y0[i], x1[j]] * (1 - wy[i]) * wx[j]
                + img[y1[i], x0[j]] * wy[i] * (1 - wx[j])
                + img[y1[i], x1[j]] * wy[i] * wx[j]
            )
    return out


def map_to_nn_input(cyl: np.ndarray) -> np.ndarray:
    m = cyl.astype(np.float64)
    if m.max() > m.min():
        m = (m - m.min()) / (m.max() - m.min() + 1e-12)
    return _resize_map(m, NN_H, NN_W)


def truth_to_targets(lon_iii: float, lat: float, cm_iii: float) -> Tuple[np.ndarray, np.ndarray]:
    lon_rel = ((lon_iii - cm_iii + 180) % 360) - 180
    # map lon_rel -90..90, lat 90..-90 → x_frac y_frac
    x_f = np.clip((lon_rel + 90.0) / 180.0, 0, 1)
    y_f = np.clip((90.0 - lat) / 180.0, 0, 1)
    heat = np.zeros((8, 16), dtype=np.float64)
    # soft blob on 8×16
    cy, cx = y_f * 7.0, x_f * 15.0
    yy, xx = np.mgrid[0:8, 0:16].astype(np.float64)
    heat = np.exp(-0.5 * (((yy - cy) / 1.2) ** 2 + ((xx - cx) / 1.4) ** 2))
    heat = heat / (heat.max() + 1e-12)
    coords = np.array([x_f, y_f], dtype=np.float64)
    return heat, coords


# Global train state for web UI
_train_state: Dict[str, Any] = {
    "running": False,
    "epoch": 0,
    "epochs": 0,
    "loss": None,
    "message": "idle",
    "history": [],
}


def get_train_status() -> Dict[str, Any]:
    """Status for UI — always safe; refreshes portable model paths."""
    global WEIGHTS_PATH, META_PATH, MODEL_DIR
    try:
        MODEL_DIR, WEIGHTS_PATH, META_PATH = _resolve_model_paths()
    except Exception:
        pass
    st = dict(_train_state)
    st["weights_exist"] = bool(WEIGHTS_PATH.exists() and WEIGHTS_PATH.stat().st_size > 1000)
    st["weights_path"] = str(WEIGHTS_PATH)
    st["model_dir"] = str(MODEL_DIR)
    st["trained"] = False
    # Prefer live train state message; if idle and weights exist, clear stale errors
    if st.get("running"):
        pass
    elif st["weights_exist"] and str(st.get("message", "")).startswith("error:"):
        st["message"] = "weights on disk (ready)"
        _train_state["message"] = st["message"]
    # expose gain fields for UI
    for k in ("initial_loss", "final_loss", "improvement", "improvement_pct"):
        if k in _train_state:
            st[k] = _train_state.get(k)
    if META_PATH.exists():
        try:
            meta = json.loads(META_PATH.read_text(encoding="utf-8"))
            st["trained"] = bool(meta.get("trained"))
            st["meta"] = meta
        except Exception:
            pass
    elif st["weights_exist"]:
        st["trained"] = True  # weights file alone is enough to load
    return st


def _sgd_step(net: SpireNet, x: np.ndarray, heat_t: np.ndarray, coord_t: np.ndarray, lr: float) -> float:
    """
    One-sample SGD with NaN/Inf protection.
    If forward/backward would corrupt weights, skip the update and return NaN.
    """
    # Guard input
    if not np.isfinite(x).all() or not np.isfinite(heat_t).all() or not np.isfinite(coord_t).all():
        return float("nan")
    if not weights_are_finite(net):
        if restore_from_good_backup(net) and weights_are_finite(net):
            pass  # recovered — continue with this step
        else:
            return float("nan")

    snap = snapshot_weights(net)
    try:
        heat, coords, c = net.forward(x, cache=True)
        assert c is not None
        if not (np.isfinite(heat).all() and np.isfinite(coords).all()):
            restore_weights(net, snap)
            return float("nan")
        loss_h = float(np.mean((heat - heat_t) ** 2))
        loss_c = float(np.mean((coords - coord_t) ** 2))
        loss = loss_h + 0.5 * loss_c
        if not math.isfinite(loss) or loss > 1e6:
            restore_weights(net, snap)
            return float("nan")

        g_heat = 2.0 * (heat.ravel() - heat_t.ravel()) / heat.size
        s = _sigmoid(c["heat_logits"])
        g_logits = g_heat * s * (1 - s)
        g_c_pre = (coords - coord_t) * coords * (1 - coords)

        h2 = c["h2"]
        g_wh = np.outer(g_logits, h2)
        g_bh = g_logits.copy()
        g_h2 = net.wh.T @ g_logits
        g_wc = np.outer(g_c_pre, h2)
        g_bc = g_c_pre.copy()
        g_h2 = g_h2 + net.wc.T @ g_c_pre

        h1 = c["h1"]
        g_h2_pre = g_h2 * (h2 > 0)
        g_wf2 = np.outer(g_h2_pre, h1)
        g_bf2 = g_h2_pre
        g_h1 = net.wf2.T @ g_h2_pre
        g_h1_pre = g_h1 * (h1 > 0)
        flat = c["flat"]
        g_wf1 = np.outer(g_h1_pre, flat)
        g_bf1 = g_h1_pre

        # clip for stability (tighter than before)
        def clip(a, t=3.0):
            a = np.nan_to_num(a, nan=0.0, posinf=t, neginf=-t)
            return np.clip(a, -t, t)

        # Apply updates on a trial basis
        net.wh = net.wh - lr * clip(g_wh)
        net.bh = net.bh - lr * clip(g_bh)
        net.wc = net.wc - lr * clip(g_wc)
        net.bc = net.bc - lr * clip(g_bc)
        net.wf2 = net.wf2 - lr * clip(g_wf2)
        net.bf2 = net.bf2 - lr * clip(g_bf2)
        net.wf1 = net.wf1 - lr * clip(g_wf1, 1.5)
        net.bf1 = net.bf1 - lr * clip(g_bf1)
        act = float(np.mean(np.abs(g_h1_pre))) + 1e-6
        if not math.isfinite(act):
            act = 1e-6
        net.w1 = net.w1 - lr * 0.02 * act * np.sign(net.w1) * rng_noise(net.w1.shape, 0.001)
        net.w2 = net.w2 - lr * 0.02 * act * np.sign(net.w2) * rng_noise(net.w2.shape, 0.001)
        net.w3 = net.w3 - lr * 0.03 * act * np.sign(net.w3) * rng_noise(net.w3.shape, 0.001)

        if not weights_are_finite(net):
            restore_weights(net, snap)
            return float("nan")
        return loss
    except Exception:
        restore_weights(net, snap)
        return float("nan")


def rng_noise(shape, scale: float = 0.001) -> np.ndarray:
    return np.random.default_rng().normal(0, scale, size=shape)


def auto_train(
    epochs: int = 40,
    samples_per_epoch: int = 24,
    lr: float = 0.01,
    seed: int = 0,
    use_existing: bool = True,
    prevent_sleep: bool = True,
) -> Dict[str, Any]:
    """
    Train SPIRE-Net on synthetic maps. Resumes / fine-tunes existing weights when
    use_existing=True. Saves to app/models/spire_net_weights.npz with GOOD backup.
    """
    global _train_state, WEIGHTS_PATH, META_PATH, MODEL_DIR, _ACTIVE_NET, _ACTIVE_TRAIN_META
    if _train_state.get("running"):
        return {"ok": False, "error": "Training already running"}

    epochs = max(1, int(epochs))
    samples_per_epoch = max(1, int(samples_per_epoch))
    lr = float(lr)

    _train_state = {
        "running": True,
        "stop_requested": False,
        "epoch": 0,
        "epochs": epochs,
        "loss": None,
        "initial_loss": None,
        "final_loss": None,
        "improvement": None,
        "improvement_pct": None,
        "message": "starting",
        "history": [],
        "nan_skips": 0,
        "prevent_sleep": bool(prevent_sleep),
    }
    CONSOLE.info("=" * 60)
    CONSOLE.info(
        f"SPIRE-Net AUTO-TRAIN  epochs={epochs}  samples/epoch={samples_per_epoch}  "
        f"lr={lr}  prevent_sleep={prevent_sleep}"
    )
    if prevent_sleep:
        start_prevent_sleep("SPIRE-Net auto_train")

    try:
        from synthetic_hq import SynthSpec, generate
        from precision_engine import make_cylindrical, fit_limb_nav, NavState

        MODEL_DIR, WEIGHTS_PATH, META_PATH = _resolve_model_paths()
        out_tmp = _train_cache_dir()

        net = None
        started_from = "scratch"
        if use_existing:
            try:
                net = SpireNet.load()
            except Exception as e:
                CONSOLE.warn(f"Could not load existing weights: {e}")
                net = None
        if net is None:
            net = SpireNet.create(seed=seed)
            CONSOLE.info("Initialized new SPIRE-Net weights")
            started_from = "scratch"
        else:
            CONSOLE.info("Fine-tuning existing SPIRE-Net weights")
            started_from = "existing_weights"
            if not weights_are_finite(net):
                CONSOLE.warn("Loaded weights corrupt — restoring GOOD backup or reinit")
                if not restore_from_good_backup(net):
                    net = SpireNet.create(seed=seed)
                    started_from = "scratch_after_corrupt"

        # Known-good snapshot at start
        try:
            net.trained = True
            save_good_backup(net)
        except Exception:
            pass
        _ACTIVE_NET = net

        rng = np.random.default_rng(seed)
        t0 = time.time()
        epoch_losses: List[float] = []
        best_loss = float("inf")
        nan_skips = 0

        for ep in range(1, epochs + 1):
            if _train_state.get("stop_requested"):
                CONSOLE.warn("Auto-train stop requested")
                break
            epoch_snap = snapshot_weights(net)
            losses = []
            for s in range(samples_per_epoch):
                if _train_state.get("stop_requested"):
                    break
                # Mixed + EXTREME geometry for "every atom" push on hard tail
                extreme_sub = float(rng.choice([-18.0, -16.5, -12.0, 0.0, 8.0, 15.0, 18.0]))
                extreme_pa = float(rng.choice([-75.0, -58.0, -30.0, 0.0, 42.0, 58.0, 75.0]))
                extreme_limb = float(rng.choice([35.0, 48.0, 62.0, 75.0, 88.0, 95.0])) if rng.random() < 0.45 else None
                spec = SynthSpec(
                    user_time_iso=f"2026-01-{(s % 28) + 1:02d} 12:00:00",
                    region=str(rng.choice(["global", "equatorial", "grs_closeup", "se_belt"])),
                    time_error_seconds=0.0,
                    resolution_preset="1080p",
                    seed=int(rng.integers(1, 2**30)),
                    random_time=False,
                    mode="metrology",
                    wave_contrast=0.7,
                    write_grs_crop=False,
                    sub_lat_deg=extreme_sub,
                    north_pa_deg=extreme_pa,
                    grs_limb_rel_deg=extreme_limb,
                    distance_au=float(rng.uniform(4.2, 6.1)) if rng.random() < 0.3 else None,
                )
                try:
                    png, fit, truth = generate(spec, out_tmp)
                except Exception as e:
                    CONSOLE.debug(f"synth fail: {e}")
                    continue
                try:
                    import grs_complete_system as grs
                    arr, _ = grs.read_fits(fit)
                    img = np.asarray(arr, dtype=np.float64)
                    if img.ndim == 3 and img.shape[0] == 3:
                        mono = 0.3 * img[0] + 0.5 * img[1] + 0.2 * img[2]
                    else:
                        mono = img
                    nav = fit_limb_nav(mono, cm_iii_deg=truth["cm_iii_deg"], distance_au=truth["distance_au"])
                    nav.cm_iii_deg = truth["cm_iii_deg"]
                    nav.distance_au = truth["distance_au"]
                    cyl = make_cylindrical(mono, nav, width=256, height=128)
                    x = map_to_nn_input(cyl)
                    heat_t, coord_t = truth_to_targets(
                        truth["grs_lon_iii_deg"], truth["grs_lat_deg"], truth["cm_iii_deg"]
                    )
                    loss = _sgd_step(net, x, heat_t, coord_t, lr=lr * (0.95 ** (ep // 5)))
                    if loss is None or not math.isfinite(float(loss)):
                        nan_skips += 1
                        continue
                    losses.append(float(loss))
                except Exception as e:
                    CONSOLE.debug(f"train step fail: {e}")
                    continue

            # Epoch integrity
            if not weights_are_finite(net):
                CONSOLE.error(f"Epoch {ep}: weights corrupted — rolling back epoch snapshot")
                restore_weights(net, epoch_snap)
                if not weights_are_finite(net):
                    restore_from_good_backup(net)
                nan_skips += 1
                mean_loss = float("nan")
            else:
                mean_loss = float(np.mean(losses)) if losses else float("nan")

            if losses and math.isfinite(mean_loss):
                epoch_losses.append(mean_loss)
                if mean_loss < best_loss:
                    best_loss = mean_loss
                    try:
                        net.trained = True
                        net.save(quiet=True)
                        save_good_backup(net)
                    except Exception as e:
                        CONSOLE.warn(f"best save blocked: {e}")

            _train_state["epoch"] = ep
            _train_state["loss"] = mean_loss if math.isfinite(mean_loss) else None
            _train_state["nan_skips"] = nan_skips
            _train_state["best_loss"] = best_loss if best_loss < float("inf") else None
            if epoch_losses and _train_state.get("initial_loss") is None:
                _train_state["initial_loss"] = epoch_losses[0]
            init_l = _train_state.get("initial_loss")
            gain = None
            gain_pct = None
            if init_l is not None and math.isfinite(mean_loss) and init_l > 0:
                gain = float(init_l - mean_loss)
                gain_pct = float(100.0 * gain / init_l)
            _train_state["improvement"] = gain
            _train_state["improvement_pct"] = gain_pct
            _train_state["message"] = (
                f"epoch {ep}/{epochs}  loss={mean_loss:.5f}  best={best_loss if best_loss < 1e90 else float('nan'):.5f}  "
                f"nan_skips={nan_skips}"
                + (f"  gain={gain:+.5f} ({gain_pct:+.1f}%)" if gain is not None else "")
            )
            _train_state["history"].append({
                "epoch": ep,
                "loss": mean_loss if math.isfinite(mean_loss) else None,
                "gain_from_start": gain,
                "gain_pct_from_start": gain_pct,
                "n_samples": len(losses),
                "nan_skips": nan_skips,
            })
            if math.isfinite(mean_loss):
                net.train_history.append({"epoch": ep, "loss": mean_loss})
            _ACTIVE_TRAIN_META = dict(_train_state)
            CONSOLE.info(_train_state["message"])
            if ep % 5 == 0 or ep == epochs:
                try:
                    if weights_are_finite(net):
                        net.trained = True
                        net.save(quiet=(ep % 5 != 0))
                except Exception as e:
                    CONSOLE.warn(f"periodic save skipped: {e}")

        # Final save only if finite
        try:
            if weights_are_finite(net):
                net.trained = True
                net.save()
            else:
                restore_from_good_backup(net)
                if weights_are_finite(net):
                    net.trained = True
                    net.save()
        except Exception as e:
            CONSOLE.warn(f"final save: {e}")

        elapsed = time.time() - t0
        initial_loss = float(epoch_losses[0]) if epoch_losses else None
        final_loss = float(epoch_losses[-1]) if epoch_losses else None
        improvement = None
        improvement_pct = None
        if initial_loss is not None and final_loss is not None:
            improvement = float(initial_loss - final_loss)
            if abs(initial_loss) > 1e-12:
                improvement_pct = float(100.0 * improvement / initial_loss)

        _train_state["running"] = False
        _train_state["initial_loss"] = initial_loss
        _train_state["final_loss"] = final_loss
        _train_state["loss"] = final_loss
        _train_state["improvement"] = improvement
        _train_state["improvement_pct"] = improvement_pct
        _train_state["nan_skips"] = nan_skips
        if improvement is not None and improvement_pct is not None:
            _train_state["message"] = (
                f"done  start={initial_loss:.5f}  end={final_loss:.5f}  "
                f"gain={improvement:+.5f} ({improvement_pct:+.1f}%)  "
                f"nan_skips={nan_skips}  {elapsed:.1f}s"
            )
        else:
            _train_state["message"] = f"done in {elapsed:.1f}s  final_loss={final_loss}  nan_skips={nan_skips}"

        CONSOLE.ok(
            f"SPIRE-Net complete in {elapsed:.1f}s  "
            f"loss {initial_loss} → {final_loss}  "
            f"gain={improvement} ({improvement_pct}%)  nan_skips={nan_skips}"
        )
        return {
            "ok": True,
            "epochs": epochs,
            "samples_per_epoch": samples_per_epoch,
            "lr": lr,
            "started_from": started_from,
            "initial_loss": initial_loss,
            "final_loss": final_loss,
            "best_loss": best_loss if best_loss < float("inf") else None,
            "improvement": improvement,
            "improvement_pct": improvement_pct,
            "nan_skips": nan_skips,
            "elapsed_s": elapsed,
            "history": list(_train_state.get("history") or []),
            "weights": str(WEIGHTS_PATH),
            "good_backup": str(good_weights_path()),
            "summary": _train_state["message"],
        }
    except Exception as e:
        import traceback
        _train_state["running"] = False
        _train_state["message"] = f"error: {e}"
        CONSOLE.error(f"SPIRE-Net train failed: {e}")
        CONSOLE.debug(traceback.format_exc())
        return {"ok": False, "error": str(e), "traceback": traceback.format_exc()}
    finally:
        stop_prevent_sleep()


# ---------------------------------------------------------------------------
# Overnight / multi-strategy training (sleep-safe, plateau-aware)
# ---------------------------------------------------------------------------

STRATEGIES: List[Dict[str, Any]] = [
    {"name": "baseline", "lr_scale": 1.0, "mode": "metrology", "region": "global", "wave": 0.7, "noise": 0.0},
    {"name": "low_lr", "lr_scale": 0.3, "mode": "metrology", "region": "global", "wave": 0.65, "noise": 0.0},
    {"name": "high_lr_burst", "lr_scale": 2.5, "mode": "metrology", "region": "grs_closeup", "wave": 0.75, "noise": 0.0},
    {"name": "closeup", "lr_scale": 1.0, "mode": "metrology", "region": "grs_closeup", "wave": 0.6, "noise": 0.0},
    {"name": "visual_hard", "lr_scale": 0.7, "mode": "visual", "region": "se_belt", "wave": 1.1, "noise": 0.0},
    {"name": "equatorial", "lr_scale": 1.0, "mode": "metrology", "region": "equatorial", "wave": 0.8, "noise": 0.0},
    {"name": "noise_reg", "lr_scale": 0.5, "mode": "metrology", "region": "global", "wave": 0.7, "noise": 0.015},
    {"name": "tiny_lr", "lr_scale": 0.1, "mode": "metrology", "region": "global", "wave": 0.55, "noise": 0.0},
    {"name": "closeup_low_lr", "lr_scale": 0.25, "mode": "metrology", "region": "grs_closeup", "wave": 0.5, "noise": 0.005},
    {"name": "full_disk", "lr_scale": 0.8, "mode": "metrology", "region": "full_disk", "wave": 0.9, "noise": 0.0},
]


def _checkpoint_path() -> Path:
    return _resolve_model_paths()[0] / "spire_train_checkpoint.json"


def _save_checkpoint(payload: Dict[str, Any]) -> None:
    try:
        p = _checkpoint_path()
        p.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    except Exception:
        pass


def _load_checkpoint() -> Optional[Dict[str, Any]]:
    p = _checkpoint_path()
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _inject_weight_noise(net: "SpireNet", scale: float, rng: np.random.Generator) -> None:
    if scale <= 0:
        return
    for name in ("w1", "w2", "w3", "wf1", "wf2", "wh", "wc"):
        w = getattr(net, name)
        setattr(net, name, w + rng.normal(0, scale * (float(np.std(w)) + 1e-6), size=w.shape))


def _reinit_heads(net: "SpireNet", rng: np.random.Generator) -> None:
    """Plateau escape: re-init heatmap/coord heads, keep feature extractors."""
    def wi(shape):
        fan = shape[1] if len(shape) == 2 else max(np.prod(shape[1:]), 1)
        return rng.normal(0, math.sqrt(2.0 / fan), size=shape)

    net.wh = wi(net.wh.shape)
    net.bh = np.zeros_like(net.bh)
    net.wc = wi(net.wc.shape)
    net.bc = np.zeros_like(net.bc)


def _make_train_sample(
    rng: np.random.Generator,
    out_tmp: Path,
    strategy: Dict[str, Any],
) -> Optional[Tuple[np.ndarray, np.ndarray, np.ndarray]]:
    """One labeled map sample. Returns (x, heat_t, coord_t) or None."""
    from synthetic_hq import SynthSpec, generate
    from precision_engine import make_cylindrical, fit_limb_nav

    try:
        import grs_complete_system as grs
    except Exception:
        return None
    day = int(rng.integers(1, 29))
    # EXTREME geometry injection also here for mixed overnight path
    extreme_sub = float(rng.choice([-18.0, -16.5, -8.0, 0.0, 11.0, 18.0]))
    extreme_pa = float(rng.choice([-75.0, -42.0, 0.0, 35.0, 58.0, 75.0]))
    extreme_limb = float(rng.choice([38.0, 55.0, 72.0, 85.0, 95.0])) if rng.random() < 0.4 else None
    spec = SynthSpec(
        user_time_iso=f"2024-{(int(rng.integers(1, 13))):02d}-{day:02d} {int(rng.integers(0, 24)):02d}:00:00",
        region=str(strategy.get("region", "global")),
        time_error_seconds=0.0,
        resolution_preset="1080p",
        seed=int(rng.integers(1, 2**30)),
        random_time=False,
        mode=str(strategy.get("mode", "metrology")),
        wave_contrast=float(strategy.get("wave", 0.7)),
        write_grs_crop=False,
        seeing_fwhm_arcsec=float(rng.uniform(0.25, 0.45)),
        noise_rms=float(rng.uniform(0.002, 0.008)),
        sub_lat_deg=extreme_sub,
        north_pa_deg=extreme_pa,
        grs_limb_rel_deg=extreme_limb,
        distance_au=float(rng.uniform(4.1, 6.2)) if rng.random() < 0.25 else None,
    )
    try:
        _png, fit, truth = generate(spec, out_tmp)
        arr, _ = grs.read_fits(fit)
        img = np.asarray(arr, dtype=np.float64)
        if img.ndim == 3 and img.shape[0] == 3:
            mono = 0.3 * img[0] + 0.5 * img[1] + 0.2 * img[2]
        else:
            mono = img
        nav = fit_limb_nav(mono, cm_iii_deg=truth["cm_iii_deg"], distance_au=truth["distance_au"])
        nav.cm_iii_deg = truth["cm_iii_deg"]
        nav.distance_au = truth["distance_au"]
        cyl = make_cylindrical(mono, nav, width=256, height=128)
        x = map_to_nn_input(cyl)
        heat_t, coord_t = truth_to_targets(
            truth["grs_lon_iii_deg"], truth["grs_lat_deg"], truth["cm_iii_deg"]
        )
        return x, heat_t, coord_t
    except Exception as e:
        CONSOLE.debug(f"overnight sample fail: {e}")
        return None


def overnight_train(
    hours: float = 8.0,
    max_epochs: int = 5000,
    samples_per_epoch: int = 16,
    base_lr: float = 0.01,
    seed: int = 0,
    use_existing: bool = True,
    resume: bool = True,
    plateau_patience: int = 8,
    plateau_min_delta: float = 1e-4,
    sample_cache_size: int = 64,
    stop_flag: Optional[Dict[str, bool]] = None,
    prevent_sleep: bool = True,
) -> Dict[str, Any]:
    """
    Long-run SPIRE-Net train with checkpoint resume (spire_train_checkpoint.json).
    Used by Train_SPIRE_Background.command and the web/desktop train controls.
    """
    global _train_state, WEIGHTS_PATH, META_PATH, MODEL_DIR, _ACTIVE_NET, _ACTIVE_TRAIN_META
    if _train_state.get("running"):
        return {"ok": False, "error": "Training already running"}

    hours = max(0.05, float(hours))
    max_epochs = max(1, int(max_epochs))
    samples_per_epoch = max(1, min(200, int(samples_per_epoch)))
    sample_cache_size = max(8, min(512, int(sample_cache_size)))
    plateau_patience = max(3, int(plateau_patience))
    deadline = time.time() + hours * 3600.0
    stop_flag = stop_flag if isinstance(stop_flag, dict) else {}

    MODEL_DIR, WEIGHTS_PATH, META_PATH = _resolve_model_paths()
    out_tmp = _train_cache_dir()
    rng = np.random.default_rng(seed)

    if prevent_sleep:
        start_prevent_sleep(f"SPIRE-Net overnight {hours}h")

    ckpt = _load_checkpoint() if resume else None
    start_epoch = 1
    strategy_i = 0
    history: List[Dict[str, Any]] = []
    best_loss = float("inf")
    best_epoch = 0
    plateau_count = 0
    strategy_switches = 0
    nan_skips = 0

    net = None
    if use_existing or resume:
        try:
            net = SpireNet.load()
        except Exception:
            net = None
    if net is None:
        net = SpireNet.create(seed=seed)
        CONSOLE.info("Overnight: new SPIRE-Net weights")
    else:
        CONSOLE.info("Overnight: continuing from existing weights")
        if not weights_are_finite(net):
            CONSOLE.warn("Overnight: corrupt weights — restoring GOOD backup")
            if not restore_from_good_backup(net):
                net = SpireNet.create(seed=seed)

    if ckpt and resume:
        history = list(ckpt.get("history") or [])
        start_epoch = int(ckpt.get("next_epoch") or 1)
        strategy_i = int(ckpt.get("strategy_i") or 0) % len(STRATEGIES)
        best_loss = float(ckpt.get("best_loss") or float("inf"))
        best_epoch = int(ckpt.get("best_epoch") or 0)
        nan_skips = int(ckpt.get("nan_skips") or 0)
        CONSOLE.info(f"Overnight: resumed at epoch {start_epoch}, strategy={STRATEGIES[strategy_i]['name']}")

    try:
        save_good_backup(net)
    except Exception:
        pass
    _ACTIVE_NET = net

    strat = STRATEGIES[strategy_i]
    _train_state = {
        "running": True,
        "stop_requested": False,
        "mode": "overnight",
        "epoch": max(0, start_epoch - 1),
        "epochs": max_epochs,
        "hours_target": hours,
        "hours_left": hours,
        "loss": None,
        "initial_loss": history[0]["loss"] if history else None,
        "final_loss": None,
        "improvement": None,
        "improvement_pct": None,
        "strategy": strat["name"],
        "strategy_switches": 0,
        "best_loss": best_loss if best_loss < float("inf") else None,
        "nan_skips": nan_skips,
        "prevent_sleep": bool(prevent_sleep),
        "message": f"overnight starting · {hours:.1f}h · strategy={strat['name']} · prevent_sleep={prevent_sleep}",
        "history": history[-200:],
    }
    CONSOLE.info("=" * 60)
    CONSOLE.info(
        f"SPIRE-Net OVERNIGHT  hours={hours}  max_epochs={max_epochs}  "
        f"samples/ep={samples_per_epoch}  cache={sample_cache_size}  "
        f"prevent_sleep={prevent_sleep}  NaN-guard=ON"
    )

    # RAM sample cache (uses free memory on big machines)
    sample_cache: List[Tuple[np.ndarray, np.ndarray, np.ndarray]] = []
    t0 = time.time()
    initial_loss_run: Optional[float] = None

    try:
        def _should_stop() -> bool:
            return bool(stop_flag.get("stop") or _train_state.get("stop_requested"))

        ep = start_epoch
        while ep <= max_epochs:
            if _should_stop():
                CONSOLE.warn("Overnight: stop requested")
                break
            now = time.time()
            if now >= deadline:
                CONSOLE.ok("Overnight: time budget reached")
                break

            strat = STRATEGIES[strategy_i % len(STRATEGIES)]
            lr = base_lr * float(strat["lr_scale"])
            hours_left = max(0.0, (deadline - now) / 3600.0)

            # Refill cache if low (background-ish sequential fill)
            while len(sample_cache) < max(samples_per_epoch, sample_cache_size // 2):
                if time.time() >= deadline or _should_stop():
                    break
                s = _make_train_sample(rng, out_tmp, strat)
                if s is not None:
                    sample_cache.append(s)
                if len(sample_cache) >= sample_cache_size:
                    break

            # Train one epoch from cache + fresh mix
            losses: List[float] = []
            for s_i in range(samples_per_epoch):
                if time.time() >= deadline or _should_stop():
                    break
                if sample_cache and rng.random() < 0.7:
                    x, heat_t, coord_t = sample_cache[int(rng.integers(0, len(sample_cache)))]
                else:
                    s = _make_train_sample(rng, out_tmp, strat)
                    if s is None:
                        if not sample_cache:
                            continue
                        x, heat_t, coord_t = sample_cache[int(rng.integers(0, len(sample_cache)))]
                    else:
                        x, heat_t, coord_t = s
                        if len(sample_cache) < sample_cache_size:
                            sample_cache.append(s)
                        else:
                            # replace random slot (reservoir-ish)
                            sample_cache[int(rng.integers(0, len(sample_cache)))] = s
                if float(strat.get("noise") or 0) > 0 and rng.random() < 0.15:
                    snap_n = snapshot_weights(net)
                    _inject_weight_noise(net, float(strat["noise"]) * 0.1, rng)
                    if not weights_are_finite(net):
                        restore_weights(net, snap_n)
                loss = _sgd_step(net, x, heat_t, coord_t, lr=lr)
                if loss is None or not math.isfinite(float(loss)):
                    nan_skips += 1
                    continue
                losses.append(float(loss))

            if not losses:
                ep += 1
                continue

            # Rollback epoch if weights went bad
            if not weights_are_finite(net):
                CONSOLE.error(f"Overnight ep {ep}: NaN/Inf weights — restore GOOD backup")
                if not restore_from_good_backup(net):
                    CONSOLE.error("GOOD backup missing — reinit heads only")
                    _reinit_heads(net, rng)
                nan_skips += 1
                ep += 1
                continue

            mean_loss = float(np.mean(losses))
            if not math.isfinite(mean_loss):
                nan_skips += 1
                ep += 1
                continue
            if initial_loss_run is None:
                initial_loss_run = mean_loss
                if _train_state.get("initial_loss") is None:
                    _train_state["initial_loss"] = mean_loss

            # Best tracking — only save finite improved weights
            improved = mean_loss < (best_loss - plateau_min_delta)
            if improved:
                best_loss = mean_loss
                best_epoch = ep
                plateau_count = 0
                try:
                    net.trained = True
                    net.save(quiet=True)
                    save_good_backup(net)
                except Exception as e:
                    CONSOLE.warn(f"best save blocked (not corrupting disk): {e}")
            else:
                plateau_count += 1

            gain = None
            gain_pct = None
            init_l = _train_state.get("initial_loss")
            if init_l is not None and init_l > 0:
                gain = float(init_l - mean_loss)
                gain_pct = float(100.0 * gain / init_l)

            row = {
                "epoch": ep,
                "loss": mean_loss,
                "strategy": strat["name"],
                "lr": lr,
                "gain_from_start": gain,
                "gain_pct_from_start": gain_pct,
                "best_loss": best_loss,
                "plateau_count": plateau_count,
                "n_samples": len(losses),
                "cache_size": len(sample_cache),
                "hours_left": hours_left,
                "nan_skips": nan_skips,
            }
            history.append(row)
            net.train_history.append({"epoch": ep, "loss": mean_loss})

            _train_state.update({
                "epoch": ep,
                "epochs": max_epochs,
                "loss": mean_loss,
                "final_loss": mean_loss,
                "improvement": gain,
                "improvement_pct": gain_pct,
                "strategy": strat["name"],
                "strategy_switches": strategy_switches,
                "best_loss": best_loss,
                "best_epoch": best_epoch,
                "hours_left": hours_left,
                "hours_elapsed": (time.time() - t0) / 3600.0,
                "nan_skips": nan_skips,
                "prevent_sleep": bool(prevent_sleep),
                "message": (
                    f"overnight ep {ep}  loss={mean_loss:.5f}  best={best_loss:.5f}  "
                    f"strat={strat['name']}  plateau={plateau_count}/{plateau_patience}  "
                    f"nan_skips={nan_skips}  left={hours_left:.2f}h"
                    + (f"  gain={gain:+.5f} ({gain_pct:+.1f}%)" if gain is not None else "")
                ),
                "history": history[-300:],
            })
            _ACTIVE_TRAIN_META = dict(_train_state)
            CONSOLE.info(_train_state["message"])

            # Checkpoint every epoch (crash/sleep/wake resume)
            _save_checkpoint({
                "next_epoch": ep + 1,
                "strategy_i": strategy_i,
                "nan_skips": nan_skips,
                "best_loss": best_loss,
                "best_epoch": best_epoch,
                "history": history[-500:],
                "hours_target": hours,
                "updated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            })
            if ep % 3 == 0:
                try:
                    if weights_are_finite(net):
                        net.trained = True
                        net.save(quiet=True)
                except Exception as e:
                    CONSOLE.warn(f"periodic save skipped: {e}")

            # Plateau → switch strategy / method
            if plateau_count >= plateau_patience:
                strategy_i = (strategy_i + 1) % len(STRATEGIES)
                strategy_switches += 1
                plateau_count = 0
                new_s = STRATEGIES[strategy_i]
                CONSOLE.warn(
                    f"Plateau detected → switch strategy to '{new_s['name']}' "
                    f"(#{strategy_switches})"
                )
                # Clear part of cache so new region/mode refills
                if len(sample_cache) > samples_per_epoch:
                    sample_cache = sample_cache[-samples_per_epoch:]
                # Every 3rd switch: re-init heads to escape deep plateaus
                if strategy_switches % 3 == 0:
                    CONSOLE.warn("Deep plateau → re-init prediction heads (keep CNN body)")
                    snap_h = snapshot_weights(net)
                    _reinit_heads(net, rng)
                    if not weights_are_finite(net):
                        restore_weights(net, snap_h)
                # Every 5th switch: small weight noise
                if strategy_switches % 5 == 0:
                    snap_n = snapshot_weights(net)
                    _inject_weight_noise(net, 0.02, rng)
                    if not weights_are_finite(net):
                        restore_weights(net, snap_n)
                _train_state["strategy"] = new_s["name"]
                _train_state["strategy_switches"] = strategy_switches
                _train_state["message"] = (
                    f"switched method → {new_s['name']}  best_loss={best_loss:.5f}"
                )

            ep += 1

        try:
            if weights_are_finite(net):
                net.trained = True
                net.save()
            else:
                restore_from_good_backup(net)
                if weights_are_finite(net):
                    net.trained = True
                    net.save()
        except Exception as e:
            CONSOLE.warn(f"overnight final save: {e}")
        elapsed = time.time() - t0
        final_loss = float(history[-1]["loss"]) if history else None
        init_l = _train_state.get("initial_loss")
        improvement = None
        improvement_pct = None
        if init_l is not None and final_loss is not None and init_l > 0:
            improvement = float(init_l - final_loss)
            improvement_pct = float(100.0 * improvement / init_l)

        _train_state["running"] = False
        _train_state["final_loss"] = final_loss
        _train_state["improvement"] = improvement
        _train_state["improvement_pct"] = improvement_pct
        _train_state["nan_skips"] = nan_skips
        _train_state["message"] = (
            f"overnight done  start={init_l}  end={final_loss}  best={best_loss}  "
            f"gain={improvement} ({improvement_pct}%)  "
            f"switches={strategy_switches}  nan_skips={nan_skips}  {elapsed/3600:.2f}h"
        )
        CONSOLE.ok(_train_state["message"])
        return {
            "ok": True,
            "mode": "overnight",
            "epochs_ran": len(history),
            "hours_target": hours,
            "elapsed_s": elapsed,
            "elapsed_h": elapsed / 3600.0,
            "samples_per_epoch": samples_per_epoch,
            "sample_cache_size": sample_cache_size,
            "initial_loss": init_l,
            "final_loss": final_loss,
            "best_loss": best_loss if best_loss < float("inf") else None,
            "best_epoch": best_epoch,
            "improvement": improvement,
            "improvement_pct": improvement_pct,
            "strategy_switches": strategy_switches,
            "nan_skips": nan_skips,
            "prevent_sleep": bool(prevent_sleep),
            "strategies_available": [s["name"] for s in STRATEGIES],
            "history": history[-500:],
            "weights": str(WEIGHTS_PATH),
            "good_backup": str(good_weights_path()),
            "checkpoint": str(_checkpoint_path()),
            "summary": _train_state["message"],
        }
    except Exception as e:
        import traceback
        _train_state["running"] = False
        _train_state["message"] = f"error: {e}"
        CONSOLE.error(f"Overnight train failed: {e}")
        CONSOLE.debug(traceback.format_exc())
        try:
            if net is not None and weights_are_finite(net):
                net.trained = True
                net.save()
            else:
                restore_from_good_backup(net) if net is not None else None
        except Exception:
            pass
        return {"ok": False, "error": str(e), "traceback": traceback.format_exc()}
    finally:
        stop_prevent_sleep()


def durable_background_train(
    hours: float = 8.0,
    samples_per_epoch: int = 20,
    fine_tune: bool = True,
) -> Dict[str, Any]:
    """
    Entry for detached / lid-close training.
    Always prevent_sleep + resume checkpoint + NaN guards.
    """
    return overnight_train(
        hours=hours,
        max_epochs=100000,
        samples_per_epoch=samples_per_epoch,
        use_existing=fine_tune,
        resume=True,
        prevent_sleep=True,
    )


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="SPIRE-Net durable trainer")
    ap.add_argument("--hours", type=float, default=8.0)
    ap.add_argument("--samples", type=int, default=20)
    ap.add_argument("--quick-epochs", type=int, default=0, help="If >0, run auto_train instead")
    ap.add_argument("--no-finetune", action="store_true")
    ap.add_argument("--no-prevent-sleep", action="store_true")
    args = ap.parse_args()
    if args.quick_epochs > 0:
        r = auto_train(
            epochs=args.quick_epochs,
            samples_per_epoch=args.samples,
            use_existing=not args.no_finetune,
            prevent_sleep=not args.no_prevent_sleep,
        )
    else:
        r = durable_background_train(
            hours=args.hours,
            samples_per_epoch=args.samples,
            fine_tune=not args.no_finetune,
        )
    print(json.dumps({k: v for k, v in r.items() if k != "history"}, indent=2, default=str))


def request_train_stop() -> None:
    """Ask overnight/auto train loops to stop (cooperative)."""
    _train_state["stop_requested"] = True


def predict_soft_prior(image: np.ndarray, nav: Any, cm_iii_deg: float) -> Optional[Dict[str, float]]:
    """Load net if trained and predict GRS lon/lat soft prior."""
    net = SpireNet.load()
    if net is None or not net.trained:
        return None
    try:
        from precision_engine import make_cylindrical, to_mono
        cyl = make_cylindrical(to_mono(image), nav, width=256, height=128)
        pred = net.predict_lonlat(cyl, cm_iii_deg)
        CONSOLE.info(f"SPIRE-Net prior: lon={pred['lon_iii_deg']:.3f} lat={pred['lat_deg']:.3f} conf={pred['confidence']:.3f}")
        return pred
    except Exception as e:
        CONSOLE.debug(f"NN prior fail: {e}")
        return None

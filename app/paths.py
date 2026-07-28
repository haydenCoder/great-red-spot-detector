#!/usr/bin/env python3
"""
Central paths for Jupiter Great Red Spot Detector (works on every device)
=========================================================

This resolves where all the data lives on different platforms — source,
PyInstaller bundle, macOS .app, etc. The paths change depending on how
you run the app, which caused me a lot of headaches early on (output files
kept going to wrong directories on my friend's laptop).

Resolves:
  - CODE_DIR   — Python modules (may be PyInstaller extract dir)
  - DATA_DIR   — writable data (outputs, logs, license, owner access)
  - MODEL_DIR  — SPIRE-Net weights (bundled + copied into DATA on first run)
  - OWNER_DIR  — usage logs the group owner can collect

Environment overrides (owner/group deploy):
  GRS_DATA_DIR        writable root
  GRS_MODEL_DIR       force model directory
  GRS_OWNER_LOG_DIR   shared folder (Dropbox/NAS) where usage is mirrored
  GRS_USER_NAME       display name written into usage logs
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Optional


def _frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def code_dir() -> Path:
    if _frozen() and getattr(sys, "_MEIPASS", None):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


def data_dir() -> Path:
    env = os.environ.get("GRS_DATA_DIR", "").strip()
    if env:
        p = Path(env).expanduser().resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p
    if _frozen():
        exe = Path(sys.executable).resolve()
        # .app/Contents/MacOS/exe → sibling GRS_Observatory_Data next to .app
        if exe.parent.name == "MacOS":
            p = exe.parents[2].parent / "GRS_Observatory_Data"
        else:
            p = exe.parent / "GRS_Observatory_Data"
        p.mkdir(parents=True, exist_ok=True)
        return p
    # source tree: app/ is code; data stays under app/ for simplicity
    p = Path(__file__).resolve().parent
    return p


def model_dir() -> Path:
    env = os.environ.get("GRS_MODEL_DIR", "").strip()
    if env:
        p = Path(env).expanduser().resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p
    # Prefer writable data copy so training can update weights on each device
    d = data_dir() / "models"
    d.mkdir(parents=True, exist_ok=True)
    return d


def bundled_model_dir() -> Path:
    """Read-only models shipped with the code/bundle."""
    return code_dir() / "models"


def ensure_models_present() -> Path:
    """
    Copy bundled SPIRE-Net weights into DATA models/ if missing.
    So every device starts with the same network without re-training.

    Ships with the app: app/models/spire_net_weights.npz (+ meta).
    Falls back to spire_net_weights.GOOD.npz if primary missing.
    """
    dest = model_dir()
    src = bundled_model_dir()
    # Prefer primary weights; recover from GOOD snapshot if needed
    primary = "spire_net_weights.npz"
    meta = "spire_net_meta.json"
    good = "spire_net_weights.GOOD.npz"

    def _copy(s: Path, t: Path) -> bool:
        try:
            if s.exists() and s.stat().st_size > 1000:
                shutil.copy2(s, t)
                return t.exists() and t.stat().st_size > 1000
        except Exception:
            pass
        return False

    t_w = dest / primary
    if not (t_w.exists() and t_w.stat().st_size > 1000):
        if not _copy(src / primary, t_w):
            # recover from GOOD snapshot (bundled or already in dest)
            for cand in (src / good, dest / good, src / primary):
                if _copy(cand, t_w):
                    break
    t_m = dest / meta
    if not (t_m.exists() and t_m.stat().st_size > 10):
        _copy(src / meta, t_m)
    # Keep GOOD backup next to active weights when available
    t_g = dest / good
    if not (t_g.exists() and t_g.stat().st_size > 1000):
        _copy(src / good, t_g)
        if not t_g.exists() and t_w.exists():
            _copy(t_w, t_g)

    inv = dest / "MODELS_README.txt"
    inv.write_text(
        "SPIRE-Net CNN weights — REQUIRED with the app\n"
        "==============================================\n"
        f"Bundled source: {src}\n"
        f"Active dir:     {dest}\n"
        f"weights present: {(dest / primary).exists()}  "
        f"size={((dest / primary).stat().st_size if (dest / primary).exists() else 0)}\n"
        f"meta present:    {(dest / meta).exists()}\n"
        "Do not delete spire_net_weights.npz when sharing the folder.\n"
        "Train only if you know what you are doing.\n"
        "See docs/GRS_OBSERVATORY_BOOK.md §7.\n",
        encoding="utf-8",
    )
    return dest


def owner_log_dir() -> Path:
    """
    Local owner/access logs. Always written.
    If GRS_OWNER_LOG_DIR is set (shared Drive/NAS), also mirrored there.
    """
    p = data_dir() / "owner_access"
    p.mkdir(parents=True, exist_ok=True)
    return p


def owner_shared_dir() -> Optional[Path]:
    env = os.environ.get("GRS_OWNER_LOG_DIR", "").strip()
    if not env:
        # conventional shared folder next to project for group deploys
        cand = data_dir().parent / "OWNER_SHARED_LOGS"
        if cand.exists():
            return cand
        return None
    p = Path(env).expanduser().resolve()
    try:
        p.mkdir(parents=True, exist_ok=True)
        return p
    except Exception:
        return None


def outputs_dir() -> Path:
    p = data_dir() / "outputs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def ensure_tree() -> dict:
    """Create standard folders on every device."""
    ensure_models_present()
    dirs = {
        "code": code_dir(),
        "data": data_dir(),
        "models": model_dir(),
        "bundled_models": bundled_model_dir(),
        "outputs": outputs_dir(),
        "owner_access": owner_log_dir(),
        "uploads": data_dir() / "uploads",
        "logs": data_dir() / "logs",
        "ssd_cache": data_dir() / "ssd_cache",
        "nasa_cache": data_dir() / "nasa_cache",
        "ephemeris_data": data_dir() / "ephemeris_data",
    }
    for k, v in dirs.items():
        if k != "bundled_models":
            Path(v).mkdir(parents=True, exist_ok=True)
    shared = owner_shared_dir()
    if shared:
        dirs["owner_shared"] = shared
    return {k: str(v) for k, v in dirs.items()}

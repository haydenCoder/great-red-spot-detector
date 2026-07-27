#!/usr/bin/env python3
"""
Usage log for every person who runs GRS Observatory
===================================================

No password. Every major action is appended so the owner can see who used
the app and what they ran.

Where logs go
-------------
1) Always:  <DATA>/owner_access/usage.jsonl
2) Also:    $GRS_OWNER_LOG_DIR/usage_<machine>.jsonl   (if set)
3) Also:    <project>/OWNER_SHARED_LOGS/  if that folder exists

Set identity (optional):
  export GRS_USER_NAME="Hayden"
  export GRS_OWNER_LOG_DIR="/Users/you/Dropbox/GRS_OwnerLogs"

Owner tools:
  python3 cli.py owner summary
  python3 cli.py owner tail
"""
from __future__ import annotations

import json
import os
import platform
import socket
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from paths import data_dir, owner_log_dir, owner_shared_dir, ensure_models_present, model_dir


def _user_name() -> str:
    return (
        os.environ.get("GRS_USER_NAME", "").strip()
        or os.environ.get("USER", "").strip()
        or os.environ.get("USERNAME", "").strip()
        or "unknown"
    )


def device_record() -> Dict[str, Any]:
    try:
        from license_manager import machine_fingerprint
        mid = machine_fingerprint()
    except Exception:
        mid = hex(uuid.getnode())
    return {
        "user_name": _user_name(),
        "machine_id": mid,
        "hostname": socket.gethostname(),
        "platform": platform.platform(),
        "python": platform.python_version(),
        "node": platform.node(),
    }


def log_event(action: str, detail: Optional[Dict[str, Any]] = None) -> None:
    """Append one usage event (best-effort, never crashes the app)."""
    try:
        ensure_models_present()
        # attach logged-in Gmail/account if present
        email = ""
        display = ""
        try:
            import accounts
            sess = accounts.current_session()
            if sess.ok:
                email = sess.email
                display = sess.display_name
            accounts.log_user_data(action, email, detail)
        except Exception:
            pass
        rec = {
            "ts_utc": datetime.now(timezone.utc).isoformat(),
            "unix": time.time(),
            "action": action,
            "email": email,
            "display_name": display,
            "detail": detail or {},
            "device": device_record(),
            "models_dir": str(model_dir()),
            "models_ok": (model_dir() / "spire_net_weights.npz").exists(),
        }
        line = json.dumps(rec, default=str) + "\n"
        # local
        local = owner_log_dir() / "usage.jsonl"
        with open(local, "a", encoding="utf-8") as f:
            f.write(line)
        # per-device snapshot for owner
        dev = owner_log_dir() / f"device_{rec['device']['machine_id']}.json"
        dev.write_text(json.dumps(rec["device"], indent=2), encoding="utf-8")
        # shared mirror
        shared = owner_shared_dir()
        if shared is not None:
            mid = rec["device"]["machine_id"]
            sp = shared / f"usage_{mid}.jsonl"
            with open(sp, "a", encoding="utf-8") as f:
                f.write(line)
            (shared / f"device_{mid}.json").write_text(
                json.dumps(rec["device"], indent=2), encoding="utf-8"
            )
            # master index
            idx = shared / "ALL_DEVICES.json"
            try:
                all_d = json.loads(idx.read_text(encoding="utf-8")) if idx.exists() else {}
            except Exception:
                all_d = {}
            all_d[mid] = {
                "last_seen_utc": rec["ts_utc"],
                "user_name": rec["device"]["user_name"],
                "hostname": rec["device"]["hostname"],
                "last_action": action,
            }
            idx.write_text(json.dumps(all_d, indent=2), encoding="utf-8")
    except Exception:
        pass


def read_events(limit: int = 200, path: Optional[Path] = None) -> List[Dict[str, Any]]:
    p = path or (owner_log_dir() / "usage.jsonl")
    if not p.exists():
        return []
    rows = []
    try:
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    except Exception:
        return []
    return rows[-limit:]


def summarize(limit: int = 5000) -> Dict[str, Any]:
    events = read_events(limit=limit)
    by_user: Dict[str, int] = {}
    by_action: Dict[str, int] = {}
    machines = {}
    for e in events:
        d = e.get("device") or {}
        u = d.get("user_name") or "?"
        by_user[u] = by_user.get(u, 0) + 1
        a = e.get("action") or "?"
        by_action[a] = by_action.get(a, 0) + 1
        mid = d.get("machine_id") or "?"
        machines[mid] = {
            "user_name": u,
            "hostname": d.get("hostname"),
            "last_action": a,
            "last_ts": e.get("ts_utc"),
        }
    # merge shared ALL_DEVICES if present
    shared = owner_shared_dir()
    shared_devices = {}
    if shared and (shared / "ALL_DEVICES.json").exists():
        try:
            shared_devices = json.loads((shared / "ALL_DEVICES.json").read_text(encoding="utf-8"))
        except Exception:
            pass
    return {
        "n_events": len(events),
        "by_user": by_user,
        "by_action": by_action,
        "machines_local": machines,
        "machines_shared": shared_devices,
        "local_log": str(owner_log_dir() / "usage.jsonl"),
        "shared_dir": str(shared) if shared else None,
        "data_dir": str(data_dir()),
    }


def logging_enabled_message() -> str:
    shared = owner_shared_dir()
    if shared:
        return f"Group access log ON → local + shared ({shared.name})"
    return "Group access log ON → app/owner_access/ (local)"

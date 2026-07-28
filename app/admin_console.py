#!/usr/bin/env python3
"""
Owner / Admin console for Jupiter Great Red Spot Detector (group oversight)
===========================================================

No password. Anyone running the app on this machine can open the owner view;
usage from every device is written to OWNER_SHARED_LOGS / owner_access so you
can see who ran what.

Admin can view:
  • Registered accounts (email, name, last seen) — no passwords stored readable
  • EVERY_USER_DATA.jsonl activity log
  • usage.jsonl device/job log
  • Paths to job outputs / previews on this machine
"""
from __future__ import annotations

import json
import secrets
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from paths import data_dir, owner_log_dir, owner_shared_dir

DEFAULT_ADMIN_USER = "owner"


def admin_session_path() -> Path:
    return data_dir() / "admin_session.json"


@dataclass
class AdminSession:
    ok: bool
    username: str = ""
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def admin_login(username: str = "", password: str = "") -> AdminSession:
    """Open owner view — no password required (self-use / group deploy)."""
    user = (username or DEFAULT_ADMIN_USER).strip() or DEFAULT_ADMIN_USER
    admin_session_path().write_text(
        json.dumps({
            "username": user,
            "logged_in_utc": datetime.now(timezone.utc).isoformat(),
            "token": secrets.token_hex(16),
            "no_password": True,
        }, indent=2),
        encoding="utf-8",
    )
    try:
        import accounts
        accounts.log_user_data("admin_open", "owner@local", {"username": user})
    except Exception:
        pass
    try:
        import group_access
        group_access.log_event("admin_open", {"username": user})
    except Exception:
        pass
    return AdminSession(ok=True, username=user, message="Owner view open (no password).")


def admin_logout() -> None:
    p = admin_session_path()
    if p.exists():
        try:
            p.unlink()
        except Exception:
            pass
    try:
        import accounts
        accounts.log_user_data("admin_logout", "owner@local", {})
    except Exception:
        pass


def admin_current() -> AdminSession:
    p = admin_session_path()
    if not p.exists():
        # Auto-open owner session so logs are always viewable without a gate
        return admin_login(DEFAULT_ADMIN_USER)
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return AdminSession(
            ok=True,
            username=str(d.get("username") or DEFAULT_ADMIN_USER),
            message="Owner session active.",
        )
    except Exception:
        return admin_login(DEFAULT_ADMIN_USER)


def list_accounts() -> List[Dict[str, Any]]:
    try:
        import accounts
        db = accounts._load()
        out = []
        for email, u in (db.get("users") or {}).items():
            out.append({
                "email": email,
                "display_name": u.get("display_name"),
                "created_utc": u.get("created_utc"),
                "provider": u.get("provider"),
                # never export password hashes to UI tables if avoidable
            })
        return out
    except Exception as e:
        return [{"error": str(e)}]


def usage_tail(n: int = 200) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    paths: List[Path] = [owner_log_dir() / "usage.jsonl"]
    _sd = owner_shared_dir()
    if _sd is not None:
        paths.append(_sd / "EVERY_USER_DATA.jsonl")
    # also per-device usage in shared
    try:
        sd = owner_shared_dir()
        if sd and sd.exists():
            paths.extend(sorted(sd.glob("usage_*.jsonl"))[-5:])
    except Exception:
        pass
    for p in paths:
        if not p or not Path(p).exists():
            continue
        try:
            lines = Path(p).read_text(encoding="utf-8", errors="replace").splitlines()
            for ln in lines[-n:]:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    rows.append(json.loads(ln))
                except Exception:
                    rows.append({"raw": ln[:500], "source": str(p)})
        except Exception:
            continue
    return rows[-n:]


def owner_summary() -> Dict[str, Any]:
    """Everything you need to see who used the app."""
    devices = {}
    try:
        sd = owner_shared_dir()
        if sd and (sd / "ALL_DEVICES.json").exists():
            devices = json.loads((sd / "ALL_DEVICES.json").read_text(encoding="utf-8"))
    except Exception:
        pass
    local_dev = {}
    try:
        for p in owner_log_dir().glob("device_*.json"):
            try:
                local_dev[p.stem] = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
    except Exception:
        pass
    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "accounts": list_accounts(),
        "devices_shared": devices,
        "devices_local": local_dev,
        "recent_usage": usage_tail(100),
        "owner_log_dir": str(owner_log_dir()),
        "owner_shared_dir": str(owner_shared_dir()) if owner_shared_dir() else None,
        "note": "No password gate. All users are logged; open this summary anytime.",
    }


def write_owner_summary(path: Optional[Path] = None) -> Path:
    path = Path(path or (owner_log_dir() / "owner_summary.json"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(owner_summary(), indent=2, default=str), encoding="utf-8")
    return path

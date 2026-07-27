#!/usr/bin/env python3
"""
User identity + owner data log for GRS Observatory
=================================================

Optional name/email tag so owner logs show who ran the app.
No password required (self-use / group). Identity is optional.

Owner data file (everybody's activity):
  data_dir/owner_access/EVERY_USER_DATA.jsonl
  + OWNER_SHARED_LOGS/EVERY_USER_DATA.jsonl if that folder exists

Each line = one event (login, logout, job, account create) with user email.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import socket
import platform
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from paths import data_dir, owner_log_dir, owner_shared_dir

PBKDF2_ITERS = 200_000
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
GMAIL_RE = re.compile(r"^[^@\s]+@(gmail\.com|googlemail\.com)$", re.I)

# Default open: any email/name tag is fine. Set GRS_REQUIRE_GMAIL=1 to force Gmail only.
def require_gmail() -> bool:
    return os.environ.get("GRS_REQUIRE_GMAIL", "0").strip() in ("1", "true", "yes")


@dataclass
class AccountSession:
    ok: bool
    email: str = ""
    display_name: str = ""
    message: str = ""
    account_id: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def accounts_path() -> Path:
    return data_dir() / "accounts.json"


def session_path() -> Path:
    return data_dir() / "session.json"


def everyone_data_path() -> Path:
    """Central file where every user's events are appended (owner view)."""
    d = owner_log_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / "EVERY_USER_DATA.jsonl"


def _load() -> Dict[str, Any]:
    p = accounts_path()
    if not p.exists():
        return {"users": {}, "version": 2}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"users": {}, "version": 2}


def _save(db: Dict[str, Any]) -> None:
    p = accounts_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(db, indent=2), encoding="utf-8")
    tmp.replace(p)


def _hash_password(password: str, salt: Optional[bytes] = None) -> Tuple[str, str]:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERS)
    return salt.hex(), dk.hex()


def _verify_password(password: str, salt_hex: str, hash_hex: str) -> bool:
    try:
        salt = bytes.fromhex(salt_hex)
        _, h = _hash_password(password, salt=salt)
        return hmac.compare_digest(h, hash_hex)
    except Exception:
        return False


def _device() -> Dict[str, Any]:
    try:
        from license_manager import machine_fingerprint
        mid = machine_fingerprint()
    except Exception:
        mid = hex(uuid_node())
    return {
        "machine_id": mid,
        "hostname": socket.gethostname(),
        "platform": platform.platform(),
        "user_os": os.environ.get("USER") or os.environ.get("USERNAME") or "",
    }


def uuid_node() -> int:
    import uuid
    return uuid.getnode()


def log_user_data(action: str, email: str = "", detail: Optional[Dict[str, Any]] = None) -> None:
    """
    Append one event for ANY user to the owner data file(s).
    Never stores raw passwords.
    """
    try:
        sess = current_session()
        em = (email or sess.email or "").strip().lower()
        rec = {
            "ts_utc": datetime.now(timezone.utc).isoformat(),
            "unix": time.time(),
            "action": action,
            "email": em,
            "display_name": sess.display_name if sess.ok else "",
            "account_id": sess.account_id if sess.ok else "",
            "detail": detail or {},
            "device": _device(),
        }
        # strip any accidental password fields
        if isinstance(rec["detail"], dict):
            for bad in ("password", "passwd", "pwd", "secret", "token"):
                rec["detail"].pop(bad, None)
        line = json.dumps(rec, default=str) + "\n"
        path = everyone_data_path()
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
        # also mirror to shared owner folder if present
        shared = owner_shared_dir()
        if shared is not None:
            sp = shared / "EVERY_USER_DATA.jsonl"
            with open(sp, "a", encoding="utf-8") as f:
                f.write(line)
            # latest snapshot per email
            if em:
                safe = re.sub(r"[^a-z0-9._-]+", "_", em)
                snap = {
                    "email": em,
                    "last_action": action,
                    "last_seen_utc": rec["ts_utc"],
                    "display_name": rec["display_name"],
                    "device": rec["device"],
                }
                (shared / f"user_{safe}.json").write_text(
                    json.dumps(snap, indent=2), encoding="utf-8"
                )
        # update accounts index last_seen
        if em:
            db = _load()
            u = (db.get("users") or {}).get(em)
            if u:
                u["last_seen_utc"] = rec["ts_utc"]
                u["last_action"] = action
                _save(db)
    except Exception:
        pass


def validate_email(email: str) -> Tuple[bool, str]:
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email):
        return False, "Enter a valid email (example: you@gmail.com)."
    if require_gmail() and not GMAIL_RE.match(email):
        return False, "Please use a Gmail address (@gmail.com)."
    return True, email


def create_account(
    email: str,
    password: str = "",
    display_name: str = "",
) -> AccountSession:
    """Register with email/name. Password optional (ignored for open group use)."""
    ok, msg_or_email = validate_email(email)
    if not ok:
        return AccountSession(ok=False, message=msg_or_email)
    email = msg_or_email
    display_name = (display_name or email.split("@")[0]).strip()[:64]
    db = _load()
    users = db.setdefault("users", {})
    if email in users:
        return AccountSession(ok=False, email=email, message="Account already exists — use Log in.")
    # Optional password hash only if user typed one; never required
    salt_hex, hash_hex = "", ""
    if password and len(password) >= 4:
        salt_hex, hash_hex = _hash_password(password)
    uid = secrets.token_hex(8)
    users[email] = {
        "account_id": uid,
        "email": email,
        "display_name": display_name,
        "salt": salt_hex,
        "password_hash": hash_hex,
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "provider": "open_identity",
        "is_gmail": bool(GMAIL_RE.match(email)),
        "no_password": not bool(hash_hex),
    }
    _save(db)
    log_user_data("account_create", email, {"display_name": display_name, "provider": "open_identity"})
    return AccountSession(
        ok=True,
        email=email,
        display_name=display_name,
        account_id=uid,
        message="Identity saved. No password required — owner sees your usage logs.",
    )


def login(email: str, password: str = "") -> AccountSession:
    """Open identity for logging. Password not required."""
    ok, msg_or_email = validate_email(email)
    if not ok:
        return AccountSession(ok=False, message=msg_or_email)
    email = msg_or_email
    db = _load()
    u = (db.get("users") or {}).get(email)
    if not u:
        # Auto-create identity so everyone is logged without a password barrier
        return create_account(email, password="", display_name=email.split("@")[0])
    # If a password was set and supplied wrong, still allow open login (self-use policy)
    if u.get("password_hash") and password:
        if not _verify_password(password, u.get("salt", ""), u.get("password_hash", "")):
            log_user_data("login_note", email, {"reason": "password_mismatch_allowed"})
    sess = AccountSession(
        ok=True,
        email=email,
        display_name=str(u.get("display_name") or email),
        account_id=str(u.get("account_id") or ""),
        message="Identity active (usage will be logged for owner).",
    )
    session_path().write_text(
        json.dumps({
            "email": sess.email,
            "display_name": sess.display_name,
            "account_id": sess.account_id,
            "logged_in_utc": datetime.now(timezone.utc).isoformat(),
            "provider": u.get("provider") or "open_identity",
        }, indent=2),
        encoding="utf-8",
    )
    log_user_data("login_ok", email, {"display_name": sess.display_name})
    try:
        import group_access
        group_access.log_event("login", {"email": email, "display_name": sess.display_name})
    except Exception:
        pass
    return sess


def logout() -> None:
    sess = current_session()
    email = sess.email if sess.ok else ""
    p = session_path()
    if p.exists():
        try:
            p.unlink()
        except Exception:
            pass
    log_user_data("logout", email, {})
    try:
        import group_access
        group_access.log_event("logout", {"email": email})
    except Exception:
        pass


def current_session() -> AccountSession:
    p = session_path()
    if not p.exists():
        return AccountSession(ok=False, message="Not logged in.")
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return AccountSession(
            ok=True,
            email=str(d.get("email") or ""),
            display_name=str(d.get("display_name") or ""),
            account_id=str(d.get("account_id") or ""),
            message="Session active.",
        )
    except Exception:
        return AccountSession(ok=False, message="Corrupt session.")


def require_login() -> bool:
    """Jobs need an active Gmail or admin session. Default ON (passcode always)."""
    return os.environ.get("GRS_REQUIRE_LOGIN", "1").strip().lower() not in ("0", "false", "no")


def google_oauth_available() -> bool:
    return bool(os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "").strip())


def google_oauth_instructions() -> str:
    return (
        "Gmail login in this app:\n\n"
        "1) Create account with your Gmail address (you@gmail.com)\n"
        "2) Choose an APP password (not necessarily your real Gmail password)\n"
        "3) Log in with that Gmail + app password\n\n"
        "All logins and jobs are written to:\n"
        f"  {everyone_data_path()}\n"
        "and OWNER_SHARED_LOGS/EVERY_USER_DATA.jsonl if that folder exists.\n\n"
        "Optional true Google Sign-In (OAuth):\n"
        "  export GOOGLE_OAUTH_CLIENT_ID=...\n"
        "  export GOOGLE_OAUTH_CLIENT_SECRET=...\n"
        "We do NOT collect your real Google password."
    )


def list_accounts_summary() -> List[Dict[str, str]]:
    db = _load()
    out = []
    for email, u in (db.get("users") or {}).items():
        out.append({
            "email": email,
            "display_name": str(u.get("display_name") or ""),
            "provider": str(u.get("provider") or "local"),
            "created_utc": str(u.get("created_utc") or ""),
            "last_seen_utc": str(u.get("last_seen_utc") or ""),
            "last_action": str(u.get("last_action") or ""),
        })
    return out


def owner_export_users_file() -> Path:
    """Write a readable summary of all accounts for the owner."""
    path = owner_log_dir() / "ALL_USERS_SUMMARY.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "exported_utc": datetime.now(timezone.utc).isoformat(),
        "n_users": len(list_accounts_summary()),
        "users": list_accounts_summary(),
        "everyone_data_file": str(everyone_data_path()),
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path

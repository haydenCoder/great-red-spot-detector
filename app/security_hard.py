#!/usr/bin/env python3
"""
Jupiter Great Red Spot Detector — security hardening (practical, not magic)

Blocks common attack patterns relevant to this local Flask + desktop product:

  OWASP-style: path traversal, unrestricted upload, injection via paths,
  SSRF-ish arbitrary process paths, DoS flood (basic rate limit),
  secret file exfil, host-header abuse, dangerous filenames.

Honest limit: no software can block "all hacking methods." This reduces the
attack surface of *this* app when the web UI is running.
"""
from __future__ import annotations

import os
import re
import time
import threading
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Sequence, Set, Tuple

# --- Allowed upload extensions (science / image only) ---
ALLOWED_UPLOAD_EXT: Set[str] = {
    ".fit", ".fits", ".fts",
    ".ser", ".avi",  # planetary video captures (SER preferred; uncompressed AVI)
    ".png", ".jpg", ".jpeg", ".tif", ".tiff",
    ".csv", ".json", ".txt",  # WinJUPOS tables
}

# Never serve or open these names
BLOCKED_BASENAMES: Set[str] = {
    "license.json",
    "accounts.json",
    "session.json",
    "usage.jsonl",
    ".env",
    ".git",
    "id_rsa",
    "id_ed25519",
    "spire_net_weights.npz",
    "spire_train_checkpoint.json",
}

# Path traversal / null-byte / windows device tricks
_TRAVERSAL_RE = re.compile(
    r"(?:\.\.[\\/])|(?:[\\/]\.\.)|(?:\0)|(?:%2e%2e)|(?:%252e)|(?:\.\.%2f)|(?:%c0%ae)",
    re.IGNORECASE,
)
_DANGEROUS_NAME_RE = re.compile(r"[<>:\"|?*\x00-\x1f]|^(?:CON|PRN|AUX|NUL|COM\d|LPT\d)(?:\.|$)", re.I)


class SecurityError(PermissionError):
    """Raised when a request is blocked as unsafe."""


# --- Simple per-IP rate limit (in-process) ---
_rl_lock = threading.Lock()
_rl_hits: Dict[str, Deque[float]] = defaultdict(deque)
_RL_WINDOW_S = 60.0
_RL_MAX = int(os.environ.get("GRS_RATE_LIMIT_PER_MIN", "90"))


def rate_limit_ok(client_key: str, *, max_per_min: Optional[int] = None) -> bool:
    """Return False if client exceeded request budget."""
    limit = max_per_min if max_per_min is not None else _RL_MAX
    if limit <= 0:
        return True
    now = time.time()
    with _rl_lock:
        q = _rl_hits[client_key]
        while q and now - q[0] > _RL_WINDOW_S:
            q.popleft()
        if len(q) >= limit:
            return False
        q.append(now)
        return True


def sanitize_filename(name: str) -> str:
    """Strip path components and dangerous chars from an upload name."""
    base = Path(str(name or "upload")).name
    base = base.replace("\x00", "")
    base = re.sub(r"[^\w.\-]+", "_", base)
    if not base or base in (".", ".."):
        base = "upload.bin"
    if _DANGEROUS_NAME_RE.search(base):
        base = "upload_safe.bin"
    # no double extensions like .fit.php
    parts = base.split(".")
    if len(parts) > 2:
        base = parts[0] + "." + parts[-1]
    return base[:180]


def has_traversal(s: str) -> bool:
    if not s:
        return False
    if "\x00" in s:
        return True
    if _TRAVERSAL_RE.search(s):
        return True
    # absolute path tricks mixed into relative
    if re.search(r"(?i)^[a-z]:[\\/]", s):
        return True
    return False


def safe_resolve_under(path: str | Path, *allowed_roots: Path) -> Path:
    """
    Resolve path and require it lives under one of allowed_roots.
    Raises SecurityError otherwise.
    """
    raw = str(path or "")
    if has_traversal(raw):
        raise SecurityError("Path traversal blocked")
    p = Path(raw).expanduser().resolve()
    if p.name.lower() in BLOCKED_BASENAMES or any(
        part.lower() in ("owner_access", ".ssh", ".git") for part in p.parts
    ):
        raise SecurityError("Access to sensitive path blocked")
    roots = [r.resolve() for r in allowed_roots if r is not None]
    for root in roots:
        try:
            p.relative_to(root)
            if not p.exists():
                raise SecurityError("Path does not exist")
            return p
        except ValueError:
            continue
        except SecurityError:
            raise
    raise SecurityError("Path outside allowed directories")


def safe_upload_extension(filename: str) -> str:
    ext = Path(sanitize_filename(filename)).suffix.lower()
    if ext not in ALLOWED_UPLOAD_EXT:
        raise SecurityError(f"File type not allowed: {ext or '(none)'}")
    return ext


def assert_safe_process_path(path: str | Path, *allowed_roots: Path) -> Path:
    """Only process images that live under uploads/outputs (or explicit roots)."""
    p = safe_resolve_under(path, *allowed_roots)
    ext = p.suffix.lower()
    if ext not in ALLOWED_UPLOAD_EXT:
        raise SecurityError(f"Cannot process file type {ext}")
    if not p.is_file():
        raise SecurityError("Not a file")
    # size cap 2 GiB
    if p.stat().st_size > 2 * 1024 ** 3:
        raise SecurityError("File too large")
    return p


def host_allowed(host_header: str, *, bind_host: str = "127.0.0.1") -> bool:
    """Block obvious Host header abuse when bound to localhost."""
    h = (host_header or "").split(":")[0].strip().lower()
    if not h:
        return True
    allowed = {
        "127.0.0.1", "localhost", "::1",
        (bind_host or "127.0.0.1").lower(),
    }
    # if user intentionally binds 0.0.0.0, allow any host but warn in logs
    if bind_host in ("0.0.0.0", "::", ""):
        return True
    return h in allowed


def strip_control_chars(s: str, max_len: int = 500) -> str:
    s = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(s or ""))
    return s[:max_len]


def security_headers() -> Dict[str, str]:
    return {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Content-Security-Policy": (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; "
            "connect-src 'self'; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'"
        ),
        "Permissions-Policy": "geolocation=(), microphone=(), camera=()",
        "Cache-Control": "no-store",
    }


def data_roots(app_dir: Path) -> List[Path]:
    roots = [
        (app_dir / "outputs").resolve(),
        (app_dir / "uploads").resolve(),
    ]
    try:
        from paths import data_dir
        dd = data_dir().resolve()
        roots.extend([(dd / "outputs").resolve(), (dd / "uploads").resolve()])
    except Exception:
        pass
    # unique
    out: List[Path] = []
    seen = set()
    for r in roots:
        k = str(r)
        if k not in seen:
            seen.add(k)
            out.append(r)
    return out

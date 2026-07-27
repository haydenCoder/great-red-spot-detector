#!/usr/bin/env python3
"""
GRS Observatory — commercial license key system (production-ready stub)
======================================================================

Key format:
  GRS-1-<PLAN>-<PAYLOAD>-<SIG4>

  PLAN:    PERS | PRO | SITE | TRIAL
  PAYLOAD: base32-ish payload (customer id + expiry days code)
  SIG4:    first 4 groups of HMAC-SHA256 over canonical string

Vendor secret:
  Environment GRS_LICENSE_SECRET  (set this before generating keys for sale)
  Default secret is for evaluation only — change before selling.

Machine binding (Pro/Site optional):
  If bind=True, payload includes a short machine fingerprint.

Storage:
  <data_dir>/license.json

This is a real, usable license gate for a paid desktop product. Rotate the
secret for production; keep a private generator on your sales machine only.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import platform
import re
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Default is intentionally weak for open evaluation — override in production.
_DEFAULT_SECRET = "GRS-EVAL-SECRET-CHANGE-BEFORE-SALE-2026"
PRODUCT_PREFIX = "GRS"
KEY_VERSION = "1"
PLANS = ("TRIAL", "PERS", "PRO", "SITE")

# Resolution rank for gates
_RES_RANK = {"1080p": 1, "4K": 2, "8K": 3, "16K": 4, "auto": 2}

PLAN_LABELS = {
    "TRIAL": "Trial (14 days)",
    "PERS": "Personal",
    "PRO": "Professional",
    "SITE": "Site / Lab",
    "EVAL": "Evaluation (no key)",
}

# Feature matrix by plan — ENFORCED at process/synth/certify/factory
PLAN_FEATURES = {
    "TRIAL": {
        "max_resolution": "4K",
        "certify": True,
        "cli": True,
        "process": True,
        "synthetic": True,
        "factory": False,
        "nn_train": False,
        "commercial_use": False,
        "watermark": True,
        "days": 14,
    },
    "PERS": {
        "max_resolution": "8K",
        "certify": True,
        "cli": True,
        "process": True,
        "synthetic": True,
        "factory": True,
        "nn_train": True,
        "commercial_use": False,
        "watermark": False,
        "days": 0,
    },
    "PRO": {
        "max_resolution": "16K",
        "certify": True,
        "cli": True,
        "process": True,
        "synthetic": True,
        "factory": True,
        "nn_train": True,
        "commercial_use": True,
        "watermark": False,
        "days": 0,
    },
    "SITE": {
        "max_resolution": "16K",
        "certify": True,
        "cli": True,
        "process": True,
        "synthetic": True,
        "factory": True,
        "nn_train": True,
        "commercial_use": True,
        "watermark": False,
        "days": 0,
    },
    # Unlicensed EVAL — intentionally limited so a real key has value
    "EVAL": {
        "max_resolution": "4K",
        "certify": False,
        "cli": True,
        "process": True,
        "synthetic": True,
        "factory": False,
        "nn_train": False,
        "commercial_use": False,
        "watermark": True,
        "days": 0,
    },
}


def using_default_secret() -> bool:
    s = os.environ.get("GRS_LICENSE_SECRET", "").strip()
    return not s or s == _DEFAULT_SECRET


def _secret() -> bytes:
    s = os.environ.get("GRS_LICENSE_SECRET", _DEFAULT_SECRET).strip()
    return s.encode("utf-8")


def machine_fingerprint() -> str:
    """Stable short machine id (not cryptographically private)."""
    raw = "|".join([
        platform.node(),
        platform.system(),
        platform.machine(),
        hex(uuid.getnode()),
    ])
    return hashlib.sha256(raw.encode()).hexdigest()[:12].upper()


def _b32ish(data: bytes) -> str:
    # Crockford-like uppercase without confusing chars
    alphabet = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
    n = int.from_bytes(data, "big")
    if n == 0:
        return "0"
    out = []
    while n:
        n, r = divmod(n, 32)
        out.append(alphabet[r])
    return "".join(reversed(out))


def _sign(canonical: str) -> str:
    dig = hmac.new(_secret(), canonical.encode("utf-8"), hashlib.sha256).hexdigest().upper()
    # 4 groups of 4
    return "-".join(dig[i : i + 4] for i in range(0, 16, 4))


@dataclass
class LicenseStatus:
    valid: bool
    plan: str = "NONE"
    plan_label: str = "Unlicensed"
    message: str = ""
    customer: str = ""
    expires_utc: Optional[str] = None
    machine_bound: bool = False
    machine_ok: bool = True
    features: Dict[str, Any] = None  # type: ignore
    key_fingerprint: str = ""
    licensed: bool = False

    def __post_init__(self):
        if self.features is None:
            self.features = {}

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def generate_key(
    plan: str = "PRO",
    customer: str = "customer",
    days: int = 0,
    bind_machine: bool = False,
    machine_id: Optional[str] = None,
) -> str:
    """
    Vendor-side key generation.

    days: 0 = no expiry; else valid for N days from generation.
    bind_machine: if True, key only works on machine_id (default: this machine).
    """
    plan = plan.upper().strip()
    if plan not in PLANS:
        raise ValueError(f"plan must be one of {PLANS}")
    if using_default_secret():
        raise RuntimeError(
            "Refusing to mint sale keys with the default evaluation secret. "
            "Export a private GRS_LICENSE_SECRET on your vendor machine first, e.g.\n"
            "  export GRS_LICENSE_SECRET='$(openssl rand -hex 32)'"
        )
    cust = re.sub(r"[^A-Za-z0-9]", "", customer)[:12].upper() or "USER"
    exp = 0
    # TRIAL defaults to plan days when days==0
    if days <= 0 and plan == "TRIAL":
        days = int(PLAN_FEATURES["TRIAL"].get("days") or 14)
    if days and days > 0:
        exp = int(time.time()) + int(days) * 86400
    mid = ""
    if bind_machine:
        mid = (machine_id or machine_fingerprint())[:12].upper()
    # payload: CUST.EXP.MID (base parts)
    raw = f"{cust}|{exp}|{mid}|{plan}|{KEY_VERSION}".encode()
    payload = _b32ish(hashlib.sha256(raw).digest()[:10])
    # embed exp and cust in readable middle for support (still signed)
    body = f"{cust[:6]}{exp:X}" if exp else f"{cust[:6]}0"
    body = re.sub(r"[^0-9A-Z]", "", body.upper())[:16]
    canonical = f"{PRODUCT_PREFIX}|{KEY_VERSION}|{plan}|{payload}|{body}|{mid}"
    sig = _sign(canonical)
    parts = [PRODUCT_PREFIX, KEY_VERSION, plan, payload, body, sig]
    if mid:
        parts.insert(5, mid)
    return "-".join(parts)


def parse_and_verify(key: str) -> Tuple[bool, Dict[str, Any], str]:
    """Return (ok, fields, message)."""
    key = (key or "").strip().upper().replace(" ", "")
    if not key:
        return False, {}, "Empty key"
    parts = key.split("-")
    if len(parts) < 6:
        return False, {}, "Key format invalid (too short)"
    if parts[0] != PRODUCT_PREFIX or parts[1] != KEY_VERSION:
        return False, {}, "Unknown product/version prefix"
    plan = parts[2]
    if plan not in PLANS:
        return False, {}, f"Unknown plan {plan}"
    # Format with machine: GRS-1-PLAN-PAYLOAD-BODY-MID-SIG(4parts)
    # Format without:      GRS-1-PLAN-PAYLOAD-BODY-SIG(4parts)
    # sig is always last 4 tokens
    if len(parts) < 7:
        return False, {}, "Key format invalid"
    sig_parts = parts[-4:]
    sig = "-".join(sig_parts)
    head = parts[:-4]
    # head: PREFIX VER PLAN PAYLOAD BODY [MID]
    if len(head) < 5:
        return False, {}, "Key body invalid"
    payload = head[3]
    body = head[4]
    mid = head[5] if len(head) >= 6 else ""
    canonical = f"{PRODUCT_PREFIX}|{KEY_VERSION}|{plan}|{payload}|{body}|{mid}"
    expect = _sign(canonical)
    if not hmac.compare_digest(sig, expect):
        # try without mid if present confusion
        return False, {}, "Signature invalid (wrong key or secret)"

    # recover expiry from body when possible: last hex run
    exp = 0
    m = re.search(r"([0-9A-F]{6,})$", body)
    if m:
        try:
            exp = int(m.group(1), 16)
            # ignore tiny numbers (cust fragments)
            if exp < 1_700_000_000:
                exp = 0
        except Exception:
            exp = 0
    cust = re.sub(r"[0-9A-F]{6,}$", "", body) or "USER"
    fields = {
        "plan": plan,
        "payload": payload,
        "body": body,
        "customer": cust,
        "expires_unix": exp,
        "machine_id": mid,
        "key": key,
        "key_fingerprint": hashlib.sha256(key.encode()).hexdigest()[:12],
    }
    if exp and time.time() > exp:
        return False, fields, "License expired"
    if mid:
        if mid != machine_fingerprint():
            return False, fields, f"Machine mismatch (key bound to {mid}, this is {machine_fingerprint()})"
    return True, fields, "OK"


def license_path(data_dir: Path) -> Path:
    return Path(data_dir) / "license.json"


def save_license(data_dir: Path, key: str, meta: Optional[Dict[str, Any]] = None) -> LicenseStatus:
    ok, fields, msg = parse_and_verify(key)
    st = status_from_fields(ok, fields, msg)
    rec = {
        "key": key.strip().upper(),
        "activated_utc": datetime.now(timezone.utc).isoformat(),
        "machine": machine_fingerprint(),
        "fields": fields,
        "status": st.to_dict(),
        "meta": meta or {},
    }
    p = license_path(data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(rec, indent=2), encoding="utf-8")
    return st


def load_status(data_dir: Path, *, allow_trial_without_key: bool = True) -> LicenseStatus:
    p = license_path(data_dir)
    if p.exists():
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
            key = rec.get("key") or ""
            ok, fields, msg = parse_and_verify(key)
            return status_from_fields(ok, fields, msg)
        except Exception as e:
            return LicenseStatus(valid=False, message=f"Corrupt license file: {e}")
    if allow_trial_without_key:
        # Evaluation mode — LIMITED features (gates are real)
        feat = dict(PLAN_FEATURES["EVAL"])
        return LicenseStatus(
            valid=True,
            plan="EVAL",
            plan_label="Evaluation (no key)",
            message=(
                "Evaluation mode: Process/Synth up to 4K only. "
                "Factory Night, certify, NN train, 8K/16K need an activated key."
            ),
            features=feat,
            licensed=False,
        )
    return LicenseStatus(valid=False, plan="NONE", message="No license activated")


def status_from_fields(ok: bool, fields: Dict[str, Any], msg: str) -> LicenseStatus:
    if not ok:
        return LicenseStatus(
            valid=False,
            plan=fields.get("plan", "NONE"),
            plan_label=PLAN_LABELS.get(fields.get("plan", ""), "Invalid"),
            message=msg,
            customer=fields.get("customer", ""),
            machine_bound=bool(fields.get("machine_id")),
            machine_ok="mismatch" not in msg.lower(),
            key_fingerprint=fields.get("key_fingerprint", ""),
            licensed=False,
            features={},
        )
    plan = fields["plan"]
    feat = dict(PLAN_FEATURES.get(plan, {}))
    exp = fields.get("expires_unix") or 0
    exp_s = None
    if exp:
        exp_s = datetime.fromtimestamp(exp, tz=timezone.utc).isoformat()
    return LicenseStatus(
        valid=True,
        plan=plan,
        plan_label=PLAN_LABELS.get(plan, plan),
        message=msg,
        customer=fields.get("customer", ""),
        expires_utc=exp_s,
        machine_bound=bool(fields.get("machine_id")),
        machine_ok=True,
        features=feat,
        key_fingerprint=fields.get("key_fingerprint", ""),
        licensed=True,
    )


def require_feature(
    data_dir: Path,
    feature: str,
    *,
    resolution: Optional[str] = None,
) -> Tuple[bool, str]:
    """
    Real gate used by desktop / CLI / server before expensive jobs.

    feature: process | synthetic | factory | certify | nn_train | cli | commercial_use
    """
    st = load_status(data_dir)
    if not st.valid:
        return False, st.message or "Invalid license"
    feat = st.features or {}
    # boolean features
    if feature in ("process", "synthetic", "factory", "certify", "nn_train", "cli", "commercial_use"):
        if feature == "cli" and feat.get("cli", True):
            return True, "OK"
        if not feat.get(feature, False) and feature != "cli":
            return False, (
                f"Plan {st.plan_label} does not include '{feature}'. "
                f"Activate a PERS/PRO/SITE key (License menu)."
            )
        if feature == "commercial_use" and not feat.get("commercial_use"):
            return False, f"Plan {st.plan} does not allow commercial use (need PRO/SITE)"
    if resolution:
        want = str(resolution).strip() or "4K"
        max_r = str(feat.get("max_resolution") or "4K")
        if _RES_RANK.get(want, 2) > _RES_RANK.get(max_r, 2):
            return False, (
                f"Resolution {want} blocked on plan {st.plan} (max {max_r}). "
                "Upgrade license or choose a lower resolution."
            )
    return True, "OK"


def assert_feature(data_dir: Path, feature: str, **kwargs) -> None:
    ok, msg = require_feature(data_dir, feature, **kwargs)
    if not ok:
        raise PermissionError(msg)


def vendor_generate_batch(
    plan: str,
    customers: List[str],
    days: int = 0,
    bind: bool = False,
) -> List[Dict[str, str]]:
    out = []
    for c in customers:
        k = generate_key(plan=plan, customer=c, days=days, bind_machine=bind)
        out.append({"customer": c, "plan": plan, "key": k})
    return out


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="GRS license tools")
    sub = ap.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("generate")
    g.add_argument("--plan", default="PRO", choices=list(PLANS))
    g.add_argument("--customer", default="CUSTOMER")
    g.add_argument("--days", type=int, default=0)
    g.add_argument("--bind", action="store_true")
    v = sub.add_parser("verify")
    v.add_argument("key")
    m = sub.add_parser("machine")
    args = ap.parse_args()
    if args.cmd == "generate":
        print(generate_key(args.plan, args.customer, args.days, args.bind))
    elif args.cmd == "verify":
        ok, fields, msg = parse_and_verify(args.key)
        print(json.dumps({"ok": ok, "message": msg, "fields": fields}, indent=2))
    elif args.cmd == "machine":
        print(machine_fingerprint())

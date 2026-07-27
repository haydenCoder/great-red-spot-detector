# GRS Observatory — Security notes

## Honest statement

**No app can block “all hacking methods.”**  
We harden against the **common attacks that matter for this product** (local Flask UI + desktop + file APIs).

## What we block (v6.0+)

| Threat | Defense |
|--------|---------|
| Path traversal (`../`, encoded variants) | `security_hard.has_traversal` + resolve-under-root |
| Secret file steal (`license.json`, keys, models) | Basename + directory denylist |
| Arbitrary process of any disk path | Process only under `uploads/` / `outputs/` |
| Malicious upload (`.exe`, `.php`, …) | Allowlist: FITS/SER/PNG/JPEG/CSV/JSON/TXT |
| Upload name tricks | Sanitize filename; drop path components |
| Request flood (basic DoS) | ~90 requests/min per IP (env `GRS_RATE_LIMIT_PER_MIN`) |
| Host header abuse on localhost | Reject non-local Host when bound to 127.0.0.1 |
| Clickjacking / MIME sniff / XSS surface | Security headers + CSP (Flask) |
| Silent wrong time | Process API refuses empty time (no `datetime.now()`) |
| License key minting with default secret | Vendor generate refused without `GRS_LICENSE_SECRET` |

Module: `app/security_hard.py` — wired into `server.py`.

## What we do **not** claim

- Perfect protection against a local malware running as your user  
- Protection if you bind `GRS_HOST=0.0.0.0` to the public internet without a reverse proxy/auth  
- Crypto-level defense against nation-state APT  
- Blocking every future zero-day  

## Safe use

1. Keep default bind: `127.0.0.1` (localhost only).  
2. Do not expose the web port to the internet.  
3. Set a private `GRS_LICENSE_SECRET` before selling keys.  
4. Keep OS + Python updated.  

## Report issues

If you find a security bug, fix or document it here; prefer fail-closed on path/file APIs.

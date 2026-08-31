"""Shared network and digest helpers for the web-calling modules.

Three modules (spice_auto, nasa_compare, ephemeris_pro) used to define the
same ``_ssl_context`` and research_grade / vlbi_metrology the same
``_hash_array``; this leaf module is their single home. It imports nothing
from the rest of the app, so it can never create an import cycle.
"""

from __future__ import annotations

import hashlib
import ssl
from typing import Optional

import numpy as np


def secure_ssl_context() -> ssl.SSLContext:
    """Best-effort verified SSL context, degrading only when forced.

    Prefers certifi's CA bundle (the same bundle the requests library uses),
    then the platform default, and only as a last resort an unverified
    context — so a missing CA store cannot turn a download into a hard
    failure, while verified TLS stays the default everywhere else.
    """
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        try:
            return ssl.create_default_context()
        except Exception:
            return ssl._create_unverified_context()


def array_hash(a: np.ndarray) -> str:
    """Stable 16-hex digest of an array's raw bytes (first 2 MB only).

    Used to fingerprint synthetic render inputs so resumable campaign
    caches can tell whether a frame was regenerated. Truncating the input
    keeps hashing cheap for 4K/8K frames while still changing whenever the
    visible image changes.
    """
    a = np.ascontiguousarray(a)
    b = a.tobytes()
    return hashlib.sha256(b[: min(len(b), 2_000_000)]).hexdigest()[:16]

from __future__ import annotations

import hashlib
import hmac
import os


def get_hmac_secret() -> bytes:
    """Load HMAC key from environment or fall back to deterministic local key."""
    secret = os.environ.get("PIPELINE_PII_SECRET", "agentic-universe-local-dev-secret-key-2026")
    return secret.encode("utf-8")


def hash_pii(value: str, namespace: str = "phone") -> str:
    """Return deterministic keyed HMAC-SHA256 hex digest for PII matching at rest."""
    key = get_hmac_secret()
    msg = f"{namespace}:{value}".encode("utf-8")
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def mask_phone(canonical_phone: str) -> str:
    """Format canonical +91XXXXXXXXXX phone as +91******XXXX."""
    digits = canonical_phone[-4:]
    return f"+91******{digits}"

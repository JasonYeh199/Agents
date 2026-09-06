"""Small signed-cookie admin session boundary for the local developer console."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import time
from typing import Any

from fastapi import HTTPException, Request

from .config import get_settings

COOKIE_NAME = "signalforge_admin"
SENSITIVE = re.compile(r"(api[-_]?key|authorization|admin[-_]?token|access[-_]?token|refresh[-_]?token|session[-_]?signing[-_]?secret|secret|password|cookie)", re.IGNORECASE)


def configured() -> bool:
    settings = get_settings()
    return bool(settings.admin_token and settings.admin_session_secret)


def create_session_cookie() -> str:
    settings = get_settings()
    payload = base64.urlsafe_b64encode(json.dumps({"sub": "admin", "exp": int(time.time()) + settings.admin_session_hours * 3600}).encode()).decode().rstrip("=")
    signature = hmac.new(settings.admin_session_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def verify_token(token: str) -> bool:
    expected = get_settings().admin_token
    return bool(expected) and hmac.compare_digest(token.encode(), expected.encode())


def verify_cookie(value: str | None) -> bool:
    if not value or not configured():
        return False
    try:
        payload, signature = value.rsplit(".", 1)
        expected = hmac.new(get_settings().admin_session_secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected):
            return False
        decoded = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        return decoded.get("sub") == "admin" and int(decoded.get("exp", 0)) > time.time()
    except Exception:
        return False


def verify_origin(request: Request) -> None:
    """Reject browser cross-origin access while retaining CLI/TestClient support.

    Non-browser clients commonly omit Origin. Browsers include it for the
    cross-port Console API calls and for any attempted cross-site request.
    """
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") != get_settings().web_origin.rstrip("/"):
        raise HTTPException(403, "Origin check failed")


async def require_admin(request: Request) -> None:
    if not verify_cookie(request.cookies.get(COOKIE_NAME)):
        raise HTTPException(401, "Admin session required")
    verify_origin(request)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: ("[REDACTED]" if SENSITIVE.search(str(key)) else redact(item)) for key, item in value.items()}
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        value = re.sub(r"(?i)bearer\s+[a-z0-9._-]+", "Bearer [REDACTED]", value)
    return value

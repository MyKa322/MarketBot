"""Telegram Mini App initData validation.

initData arrives from the client and is therefore untrusted. Telegram signs it with a
key derived from the bot token, so verifying the HMAC is the only thing standing
between the API and anyone who can craft an HTTP request. Never skip it, and never
trust the `user` field before the signature checks out.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl


class InitDataError(ValueError):
    """initData was missing, malformed, expired or incorrectly signed."""


@dataclass(frozen=True, slots=True)
class WebAppUser:
    telegram_id: int
    username: str | None
    first_name: str | None
    language_code: str | None = None


def _secret_key(bot_token: str) -> bytes:
    return hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()


def validate_init_data(init_data: str, bot_token: str, *, max_age: int = 86400) -> WebAppUser:
    """Verify the signature and return the authenticated user."""
    if not init_data:
        raise InitDataError("missing initData")

    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        raise InitDataError("missing hash")

    check_string = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    expected = hmac.new(
        _secret_key(bot_token), check_string.encode(), hashlib.sha256
    ).hexdigest()

    # Constant-time comparison: a plain == leaks the signature one byte at a time.
    if not hmac.compare_digest(expected, received_hash):
        raise InitDataError("bad signature")

    auth_date = pairs.get("auth_date")
    if auth_date:
        try:
            age = time.time() - int(auth_date)
        except ValueError as exc:
            raise InitDataError("bad auth_date") from exc
        if age > max_age:
            raise InitDataError("initData expired")

    raw_user = pairs.get("user")
    if not raw_user:
        raise InitDataError("missing user")
    try:
        payload = json.loads(raw_user)
    except json.JSONDecodeError as exc:
        raise InitDataError("bad user payload") from exc

    telegram_id = payload.get("id")
    if not isinstance(telegram_id, int):
        raise InitDataError("bad user id")

    return WebAppUser(
        telegram_id=telegram_id,
        username=payload.get("username"),
        first_name=payload.get("first_name"),
        language_code=payload.get("language_code"),
    )

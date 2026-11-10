"""Mini App initData signature verification."""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import pytest

from marketbot.webapp.auth import InitDataError, validate_init_data

TOKEN = "123456:TEST-TOKEN"


def sign(fields: dict) -> str:
    check = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    digest = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode({**fields, "hash": digest})


@pytest.fixture
def fields() -> dict:
    return {
        "auth_date": str(int(time.time())),
        "query_id": "AAA",
        "user": json.dumps({"id": 777, "username": "tester", "first_name": "Test"}),
    }


def test_valid_init_data(fields):
    user = validate_init_data(sign(fields), TOKEN)
    assert user.telegram_id == 777
    assert user.username == "tester"


def test_tampered_payload_is_rejected(fields):
    signed = sign(fields)
    tampered = signed.replace("777", "888")
    with pytest.raises(InitDataError):
        validate_init_data(tampered, TOKEN)


def test_wrong_token_is_rejected(fields):
    with pytest.raises(InitDataError):
        validate_init_data(sign(fields), "999:OTHER")


def test_expired_init_data(fields):
    fields["auth_date"] = str(int(time.time()) - 90_000)
    with pytest.raises(InitDataError, match="expired"):
        validate_init_data(sign(fields), TOKEN)


@pytest.mark.parametrize("raw", ["", "user=x", "hash=deadbeef"])
def test_malformed_input(raw):
    with pytest.raises(InitDataError):
        validate_init_data(raw, TOKEN)

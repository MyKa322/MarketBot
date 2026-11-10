"""Locale-aware price parsing and URL handling."""
from __future__ import annotations

import pytest

from marketbot.steam.parse import (
    build_market_url,
    extract_item_nameid,
    normalize_hash_name,
    parse_history_timestamp,
    parse_int,
    parse_market_url,
    parse_price,
)


@pytest.mark.parametrize(
    ("raw", "code", "expected"),
    [
        ("$1.15", "USD", 115),
        ("$1,234.56", "USD", 123456),
        ("1,15€", "EUR", 115),
        ("1.234,56€", "EUR", 123456),
        ("£0.87", "GBP", 87),
        ("R$ 12,34", "BRL", 1234),
        ("1 234,56 zł", "PLN", 123456),
        ("0,03€", "EUR", 3),
        ("$0.03", "USD", 3),
    ],
)
def test_locale_formats(raw, code, expected):
    assert parse_price(raw, code) == expected


@pytest.mark.parametrize(("raw", "expected"), [("¥1234", 1234), ("¥1,234", 1234)])
def test_zero_minor_unit_currency(raw, expected):
    """JPY has no subunit, so a comma can only ever be a thousands separator."""
    assert parse_price(raw, "JPY") == expected


def test_ambiguous_three_digit_group_is_thousands():
    assert parse_price("$1,234", "USD") == 123400


@pytest.mark.parametrize("raw", [None, "", "--", "no digits here"])
def test_unparseable_returns_none(raw):
    assert parse_price(raw, "USD") is None


def test_numeric_input_passthrough():
    assert parse_price(12.34, "USD") == 1234
    assert parse_price(12, "USD") == 1200


def test_parse_int_handles_grouping():
    assert parse_int("1,234") == 1234
    assert parse_int("12 345") == 12345
    assert parse_int(None) is None


def test_market_url_roundtrip():
    name = "AK-47 | Redline (Field-Tested)"
    url = build_market_url(730, name)
    assert parse_market_url(url) == (730, name)


@pytest.mark.parametrize(
    "url",
    [
        "https://steamcommunity.com/market/listings/570/Inscribed%20Axe/",
        "steamcommunity.com/market/listings/570/Inscribed%20Axe",
    ],
)
def test_market_url_variants(url):
    assert parse_market_url(url) == (570, "Inscribed Axe")


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/market/listings/730/Thing",
        "https://steamcommunity.com/profiles/76561198000000000",
        "https://steamcommunity.com/market/listings/abc/Thing",
        "not a url",
    ],
)
def test_rejects_non_market_urls(url):
    assert parse_market_url(url) is None


def test_normalize_hash_name():
    assert normalize_hash_name("  AK-47   |   Redline  ") == "AK-47 | Redline"
    assert normalize_hash_name('"Glock-18 | Fade"') == "Glock-18 | Fade"


def test_extract_item_nameid():
    assert extract_item_nameid("x Market_LoadOrderSpread( 176321160 ); y") == "176321160"
    assert extract_item_nameid("nothing here") is None


def test_history_timestamp():
    ts = parse_history_timestamp("Jul 12 2020 01: +0")
    assert ts is not None
    assert (ts.year, ts.month, ts.day, ts.hour) == (2020, 7, 12, 1)
    assert parse_history_timestamp("garbage") is None

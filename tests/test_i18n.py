"""Translation catalogs and the lookup layer.

Two of these guard failure modes that are invisible until a user in that language hits
the screen: a placeholder that does not survive translation renders the wrong text, and
an unbalanced HTML tag makes Telegram reject the entire message.
"""
from __future__ import annotations

import re

import pytest

from marketbot.i18n import CATALOGS, DEFAULT_LANG, LANGUAGES, language_name, normalize_lang, t

PLACEHOLDER = re.compile(r"\{([a-z_]+)\}")
TAG = re.compile(r"</?([a-z]+)>")
NON_DEFAULT = [code for code in CATALOGS if code != DEFAULT_LANG]


def test_every_declared_language_has_a_catalog():
    assert set(LANGUAGES) == set(CATALOGS)
    assert DEFAULT_LANG in CATALOGS


@pytest.mark.parametrize("code", NON_DEFAULT)
def test_catalog_key_sets_match_english_exactly(code):
    english, other = set(CATALOGS[DEFAULT_LANG]), set(CATALOGS[code])
    assert not english - other, f"{code} is missing: {sorted(english - other)[:5]}"
    assert not other - english, f"{code} has unknown keys: {sorted(other - english)[:5]}"


@pytest.mark.parametrize("code", NON_DEFAULT)
def test_placeholders_survive_translation(code):
    """A dropped {placeholder} silently renders the wrong sentence."""
    mismatches = []
    for key, english in CATALOGS[DEFAULT_LANG].items():
        expected = set(PLACEHOLDER.findall(english))
        actual = set(PLACEHOLDER.findall(CATALOGS[code][key]))
        if expected != actual:
            mismatches.append((key, sorted(expected), sorted(actual)))
    assert not mismatches, f"{code} placeholder drift: {mismatches[:5]}"


@pytest.mark.parametrize("code", list(CATALOGS))
def test_html_tags_are_balanced(code):
    """Telegram rejects the whole message on a stray tag, not just that line."""
    broken = []
    for key, text in CATALOGS[code].items():
        depth: list[str] = []
        for match in TAG.finditer(text):
            if match.group(0).startswith("</"):
                if not depth or depth.pop() != match.group(1):
                    broken.append(key)
                    break
            else:
                depth.append(match.group(1))
        else:
            if depth:
                broken.append(key)
    assert not broken, f"{code} unbalanced HTML in: {broken[:5]}"


@pytest.mark.parametrize("code", list(CATALOGS))
def test_no_empty_translations(code):
    empty = [key for key, value in CATALOGS[code].items() if not value.strip()]
    assert not empty, f"{code} has empty strings: {empty[:5]}"


# ------------------------------------------------------------------- lookup


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("uk", "uk"),
        ("uk-UA", "uk"),
        ("UK_ua", "uk"),
        ("en-GB", "en"),
        ("pt-BR", "en"),   # no catalog: fall back
        ("ru", "en"),      # no catalog: fall back
        ("zz", "en"),
        (None, "en"),
        ("", "en"),
    ],
)
def test_normalize_lang(raw, expected):
    assert normalize_lang(raw) == expected


def test_translation_returns_the_right_language():
    assert t("btn.watchlist", "en") != t("btn.watchlist", "uk")
    assert "Список" in t("btn.watchlist", "uk")


def test_missing_key_falls_back_to_the_key_itself():
    assert t("no.such.key", "uk") == "no.such.key"


def test_missing_translation_falls_back_to_english(monkeypatch):
    monkeypatch.setitem(CATALOGS, "zz", {"btn.menu": "ZZ menu"})
    assert t("btn.menu", "zz") == "ZZ menu"
    # Not in the partial catalog: English rather than a raw key.
    assert t("btn.help", "zz") == CATALOGS["en"]["btn.help"]


def test_formatting_applies():
    assert "7" in t("menu.stats", "en", watch=7, rules=2, currency="USD")


def test_bad_format_args_degrade_instead_of_raising():
    """One missing kwarg should cost one line of text, not the whole handler."""
    assert t("menu.stats", "en") == CATALOGS["en"]["menu.stats"]
    assert t("menu.greeting", "en", wrong_kwarg="x") == CATALOGS["en"]["menu.greeting"]


def test_language_names_are_endonyms():
    assert language_name("uk") == "Українська"
    assert language_name("zz") == "English"


@pytest.mark.parametrize("code", list(CATALOGS))
def test_button_labels_stay_short_enough_for_telegram(code):
    """Long button text wraps badly on a phone; keep labels tight in every language."""
    long_labels = [
        key for key, value in CATALOGS[code].items()
        if key.startswith("btn.") and len(value) > 28
    ]
    assert not long_labels, f"{code} over-long buttons: {long_labels}"

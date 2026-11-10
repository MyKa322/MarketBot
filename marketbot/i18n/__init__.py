"""Lightweight translation layer.

Deliberately not gettext: no .po/.mo toolchain, no compilation step, no extraction
pass. Catalogs are plain Python dicts, so adding a language is one file and a line in
LANGUAGES, and a missing key falls back to English instead of showing the raw key to
a user.

Every string the user can see goes through t(). Buttons included -- a half-translated
interface reads worse than an untranslated one.
"""
from __future__ import annotations

from marketbot.i18n.catalogs import CATALOGS, DEFAULT_LANG, LANGUAGES

__all__ = ["CATALOGS", "DEFAULT_LANG", "LANGUAGES", "language_name", "normalize_lang", "t"]


def normalize_lang(code: str | None) -> str:
    """Map a Telegram language_code ('ru-RU', 'en-GB') onto a catalog we have."""
    if not code:
        return DEFAULT_LANG
    lowered = str(code).replace("_", "-").lower()
    if lowered in CATALOGS:
        return lowered
    base = lowered.split("-", 1)[0]
    return base if base in CATALOGS else DEFAULT_LANG


def language_name(code: str) -> str:
    return LANGUAGES.get(code, LANGUAGES[DEFAULT_LANG])


def t(key: str, lang: str | None = None, /, **kwargs: object) -> str:
    """Translate `key`, falling back to English and then to the key itself.

    Formatting errors return the unformatted template rather than raising: a missing
    placeholder should degrade one line of text, not blow up the handler rendering it.
    """
    catalog = CATALOGS.get(normalize_lang(lang), CATALOGS[DEFAULT_LANG])
    template = catalog.get(key)
    if template is None:
        template = CATALOGS[DEFAULT_LANG].get(key, key)
    if not kwargs:
        return template
    try:
        return template.format(**kwargs)
    except (KeyError, IndexError, ValueError):
        return template

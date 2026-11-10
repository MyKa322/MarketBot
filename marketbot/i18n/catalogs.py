"""Catalog registry.

To add a language: create marketbot/i18n/<code>.py exporting MESSAGES (copy en.py and
translate the values), import it here, and add an entry to LANGUAGES. Missing keys fall
back to English automatically, so a partial catalog is safe to ship -- but a complete
one is strongly preferred: a half-translated interface reads worse than an untranslated
one.
"""
from __future__ import annotations

from marketbot.i18n import en, uk

DEFAULT_LANG = "en"

# Values are each language's own name (endonym).
LANGUAGES: dict[str, str] = {
    "en": "English",
    "uk": "Українська",
}

CATALOGS: dict[str, dict[str, str]] = {
    "en": en.MESSAGES,
    "uk": uk.MESSAGES,
}

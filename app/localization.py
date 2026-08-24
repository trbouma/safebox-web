"""Server-side localization primitives for Safebox Web."""

from __future__ import annotations

from functools import lru_cache
import gettext
from pathlib import Path
import re


DEFAULT_LANGUAGE = "en"
SUPPORTED_LANGUAGES = {
    "en": "English",
    "fr": "Français",
    "es": "Español",
    "pt": "Português",
    "de": "Deutsch",
    "it": "Italiano",
    "iu": "ᐃᓄᒃᑎᑐᑦ (Inuktitut)",
}
LOCALE_DIRECTORY = Path(__file__).resolve().parent / "locales"
_LANGUAGE_TAG_PATTERN = re.compile(
    r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*"
)


def normalize_language_tag(value: str) -> str:
    """Return a conservatively canonicalized BCP 47 language tag."""

    if not isinstance(value, str) or not _LANGUAGE_TAG_PATTERN.fullmatch(value):
        raise ValueError("language tag is invalid")
    parts = value.split("-")
    canonical_parts = [parts[0].lower()]
    for part in parts[1:]:
        if len(part) == 4 and part.isalpha():
            canonical_parts.append(part.title())
        elif (len(part) == 2 and part.isalpha()) or (
            len(part) == 3 and part.isdigit()
        ):
            canonical_parts.append(part.upper())
        else:
            canonical_parts.append(part.lower())
    return "-".join(canonical_parts)


def supported_language(value: str | None) -> str:
    """Resolve a supported base language, falling back safely to English."""

    try:
        normalized = normalize_language_tag(value or DEFAULT_LANGUAGE)
    except ValueError:
        return DEFAULT_LANGUAGE
    base_language = normalized.split("-", 1)[0]
    return normalized if base_language in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE


@lru_cache(maxsize=32)
def translations_for(value: str | None) -> gettext.NullTranslations:
    """Load and cache a gettext catalog with English source-text fallback."""

    language = supported_language(value)
    if language == DEFAULT_LANGUAGE:
        return gettext.NullTranslations()
    catalog_languages = [language.replace("-", "_")]
    base_language = language.split("-", 1)[0]
    if base_language != language:
        catalog_languages.append(base_language)
    return gettext.translation(
        "messages",
        localedir=LOCALE_DIRECTORY,
        languages=catalog_languages,
        fallback=True,
    )

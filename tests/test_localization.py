from __future__ import annotations

import pytest

from app.localization import (
    DEFAULT_LANGUAGE,
    normalize_language_tag,
    supported_language,
    translations_for,
)
from app.templating import render_template


def test_language_tags_are_canonicalized_for_session_storage() -> None:
    assert normalize_language_tag("EN") == "en"
    assert normalize_language_tag("fr-ca") == "fr-CA"
    assert normalize_language_tag("zh-hans-cn") == "zh-Hans-CN"

    with pytest.raises(ValueError, match="language tag"):
        normalize_language_tag("english")


def test_unsupported_languages_fall_back_to_english() -> None:
    assert supported_language("fr-CA") == "fr-CA"
    assert supported_language("nl") == DEFAULT_LANGUAGE
    assert supported_language("../fr") == DEFAULT_LANGUAGE


def test_template_renderer_injects_request_localization_without_shared_mutation() -> None:
    rendered = render_template(
        "page.html",
        title="Localization Test",
        body="<p>Localized body</p>",
        language="fr-CA",
    )

    assert '<html lang="fr-CA" data-theme="dark">' in rendered
    assert ">Home</a>" in rendered
    assert "Opening…" in rendered
    assert translations_for("fr-CA").gettext("Home") == "Home"

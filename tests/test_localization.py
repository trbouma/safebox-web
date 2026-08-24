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
    assert supported_language("iu") == "iu"
    assert supported_language("nl") == DEFAULT_LANGUAGE
    assert supported_language("../fr") == DEFAULT_LANGUAGE
    assert translations_for("iu").gettext("Safebox is Connected") == "Safebox ᐊᑕᔪᖅ"
    assert translations_for("iu").gettext("Home") == "ᐱᒋᐊᕐᕕᒃ"
    assert translations_for("iu").gettext("Preferences") == "ᓇᓖᕌᕈᑏᑦ"
    assert translations_for("iu").gettext("Advisories") == "ᖃᐅᔨᒃᑲᐃᔾᔪᑏᑦ"
    assert translations_for("iu").gettext("Manage Balances") == (
        "ᐊᒥᐊᒃᑯᓂᒃ ᐊᐅᓚᑦᑎᓂᖅ"
    )
    assert translations_for("iu").gettext("Manage Records") == (
        "ᑎᑎᖅᑲᓂᒃ ᐊᐅᓚᑦᑎᓂᖅ"
    )
    assert translations_for("iu").gettext("Display preferences updated.") == (
        "ᓇᓖᕌᕈᑏᑦ ᓄᑖᙳᖅᑎᑕᐅᔪᑦ."
    )


@pytest.mark.parametrize(
    ("language", "expected"),
    (
        ("en", "Display preferences updated."),
        ("fr", "Préférences d’affichage mises à jour."),
        ("es", "Preferencias de visualización actualizadas."),
        ("pt", "Preferências de exibição atualizadas."),
        ("de", "Anzeigeeinstellungen aktualisiert."),
        ("it", "Preferenze di visualizzazione aggiornate."),
        ("iu", "ᓇᓖᕌᕈᑏᑦ ᓄᑖᙳᖅᑎᑕᐅᔪᑦ."),
    ),
)
def test_display_preferences_confirmation_is_localized(
    language: str,
    expected: str,
) -> None:
    assert translations_for(language).gettext("Display preferences updated.") == expected


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


@pytest.mark.parametrize(
    ("language", "updating", "unavailable"),
    (
        ("fr", "Mise à jour…", "Le solde précédemment confirmé est temporairement indisponible."),
        ("es", "Actualizando…", "El saldo confirmado previamente no está disponible temporalmente."),
        ("pt", "Atualizando…", "O saldo confirmado anteriormente está temporariamente indisponível."),
        ("de", "Aktualisierung…", "Das zuvor bestätigte Guthaben ist vorübergehend nicht verfügbar."),
        ("it", "Aggiornamento…", "Il saldo precedentemente confermato è temporaneamente non disponibile."),
    ),
)
def test_balance_status_catalog_entries_are_available(
    language: str,
    updating: str,
    unavailable: str,
) -> None:
    translations = translations_for(language)

    assert translations.gettext("Updating…") == updating
    assert (
        translations.gettext(
            "The previously confirmed balance is temporarily unavailable."
        )
        == unavailable
    )


@pytest.mark.parametrize(
    ("language", "credit", "debit"),
    (
        ("fr", "Crédit", "Débit"),
        ("es", "Crédito", "Débito"),
        ("pt", "Crédito", "Débito"),
        ("de", "Gutschrift", "Belastung"),
        ("it", "Accredito", "Addebito"),
    ),
)
def test_transaction_directions_are_localized(
    language: str,
    credit: str,
    debit: str,
) -> None:
    translations = translations_for(language)

    assert translations.gettext("Credit") == credit
    assert translations.gettext("Debit") == debit


@pytest.mark.parametrize(
    ("language", "expected"),
    (
        ("fr", "Adresse Lightning non valide."),
        ("es", "No es una dirección Lightning válida."),
        ("pt", "Não é um endereço Lightning válido."),
        ("de", "Keine gültige Lightning-Adresse."),
        ("it", "L’indirizzo Lightning non è valido."),
    ),
)
def test_invalid_lightning_address_error_is_localized(
    language: str,
    expected: str,
) -> None:
    assert (
        translations_for(language).gettext("Not a valid Lightning address.")
        == expected
    )

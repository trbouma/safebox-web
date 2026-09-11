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
    assert supported_language("zh-Hans") == "zh-Hans"
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
        ("zh-Hans", "显示偏好设置已更新。"),
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
        ("zh-Hans", "正在更新…", "先前确认的余额暂时不可用。"),
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
        ("zh-Hans", "入账", "支出"),
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
        ("zh-Hans", "这不是有效的 Lightning 地址。"),
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


@pytest.mark.parametrize(
    ("language", "scan", "receive_silent_payment"),
    (
        ("fr", "Scanner", "Recevoir un paiement silencieux"),
        ("es", "Escanear", "Recibir un pago silencioso"),
        ("pt", "Escanear", "Receber pagamento silencioso"),
        ("de", "Scannen", "Silent Payment empfangen"),
        ("it", "Scansiona", "Ricevi un pagamento silenzioso"),
        ("iu", "Scan", "Receive Silent Payment"),
        ("zh-Hans", "扫描", "接收静默支付"),
    ),
)
def test_wallet_scan_and_silent_payment_labels_are_localized(
    language: str,
    scan: str,
    receive_silent_payment: str,
) -> None:
    translations = translations_for(language)

    assert translations.gettext("Scan") == scan
    assert translations.gettext("Receive Silent Payment") == receive_silent_payment


@pytest.mark.parametrize(
    (
        "language",
        "availability",
        "within_instance",
        "local_network",
        "across_networks",
    ),
    (
        (
            "fr",
            "Disponibilité",
            "Dans cette instance",
            "Sur le réseau local",
            "Entre réseaux",
        ),
        (
            "es",
            "Disponibilidad",
            "Dentro de esta instancia",
            "En la red local",
            "Entre redes",
        ),
        (
            "pt",
            "Disponibilidade",
            "Nesta instância",
            "Na rede local",
            "Entre redes",
        ),
        (
            "de",
            "Verfügbarkeit",
            "Innerhalb dieser Instanz",
            "Im lokalen Netzwerk",
            "Netzwerkübergreifend",
        ),
        (
            "it",
            "Disponibilità",
            "In questa istanza",
            "Sulla rete locale",
            "Tra reti",
        ),
        ("zh-Hans", "可用范围", "在此实例内", "在本地网络上", "跨网络"),
        (
            "iu",
            "Availability",
            "Within this instance",
            "On the local network",
            "Across networks",
        ),
    ),
)
def test_clear_availability_labels_are_localized(
    language: str,
    availability: str,
    within_instance: str,
    local_network: str,
    across_networks: str,
) -> None:
    translations = translations_for(language)

    assert translations.gettext("Availability") == availability
    assert translations.gettext("Within this instance") == within_instance
    assert translations.gettext("On the local network") == local_network
    assert translations.gettext("Across networks") == across_networks

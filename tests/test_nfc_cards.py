import base64
from dataclasses import replace
import re

import pytest
from fastapi.testclient import TestClient
from stroma import Keys

from test_app import TEST_NSEC, database_settings
from app.main import create_app
from app.dependencies import get_session_credentials
from app.security import SessionCredentials, CsrfProtector
from app import nfc_cards as cards


@pytest.fixture
def setup(tmp_path):
    settings = replace(database_settings(tmp_path), nfc_issuer_origin="https://safebox.example")
    credentials = SessionCredentials(nsec=TEST_NSEC, bootstrap_relay="wss://relay.example")
    app = create_app(settings)
    app.dependency_overrides[get_session_credentials] = lambda: credentials
    with TestClient(app, base_url=settings.nfc_issuer_origin) as client:
        yield client, settings, credentials


def post(setup, path, **data):
    client, settings, _ = setup
    return client.post('/nfc/' + path, data={"csrf_token": CsrfProtector(settings).issue(), **data}, follow_redirects=False)


def prepared(setup):
    response = post(setup, 'prepare', pin='123456', pin_repeat='123456', confirmed='yes')
    assert response.status_code == 200, response.text
    assert TEST_NSEC not in response.text
    assert 'value="123456"' not in response.text
    return re.search(r'data-card-write-payload value="([^"]+)"', response.text)[1]


def policy(setup):
    client, _, credentials = setup
    return cards.policy_for(client.app.state.database_engine, cards.owner(credentials))


def test_issue_read_activate_replace_revoke(setup):
    payload = prepared(setup)
    assert policy(setup).active_generation is None
    response = post(setup, 'read', card_payload=payload)
    assert response.status_code == 200
    assert 'Pending activation' in response.text
    assert '123456' not in response.text
    assert post(setup, 'activate', card_payload=payload, revision=str(policy(setup).revision), confirmed='yes').status_code == 303
    original = policy(setup).active_generation
    replacement = prepared(setup)
    assert replacement != payload
    assert policy(setup).active_generation == original
    revision = str(policy(setup).revision)
    assert post(setup, 'revoke', revision=revision, confirmed='yes').status_code == 303
    assert post(setup, 'activate', card_payload=replacement, revision=revision, confirmed='yes').status_code == 409
    assert policy(setup).active_generation is None
    assert 'Inactive / revoked / superseded' in post(setup, 'read', card_payload=payload).text


def test_pending_expiry(setup, monkeypatch):
    payload = prepared(setup)
    revision = str(policy(setup).revision)
    current = cards.now()
    monkeypatch.setattr(cards, 'now', lambda: current + cards.PENDING_TTL + 1)
    assert post(setup, 'activate', card_payload=payload, revision=revision, confirmed='yes').status_code == 409


@pytest.mark.parametrize('pin,repeat', [('123', '123'), ('123456', '☃'), ('123456', '654321')])
def test_invalid_pin(setup, pin, repeat):
    assert post(setup, 'prepare', pin=pin, pin_repeat=repeat, confirmed='yes').status_code == 400


def test_csrf_and_origin(setup):
    client, _, _ = setup
    assert client.post('/nfc/prepare', data={'pin':'123456'}).status_code == 403
    assert client.post('https://other.example/nfc/prepare', data={
        'csrf_token': CsrfProtector(setup[1]).issue()}).status_code == 403


def test_codec_tampering_owner_and_bounds(setup):
    _, settings, credentials = setup
    payload, _, _ = cards.encode_card(settings, credentials, '123456', 90)
    other, _, _ = cards.encode_card(settings, credentials, '123456', 90)
    assert payload != other
    assert len(payload) < 400
    metadata = cards.decode_card(settings, payload, cards.owner(credentials))
    assert not {'pin', 'nsec'} & metadata.keys()
    for invalid in (payload[:-1], payload + b'\x00', payload[:-1] + bytes([payload[-1] ^ 1]), b'x' * 401):
        with pytest.raises(ValueError):
            cards.decode_card(settings, invalid, cards.owner(credentials))
    with pytest.raises(ValueError):
        cards.decode_card(settings, payload, Keys().public_key_bech32())
    assert post(setup, 'read', card_payload=base64.b64encode(b'x' * 401).decode()).status_code == 400


def test_stale_candidate_cannot_activate(setup):
    old = prepared(setup)
    revision = str(policy(setup).revision)
    prepared(setup)
    assert post(setup, 'activate', card_payload=old, revision=revision, confirmed='yes').status_code == 409


def test_allowlisted_origin_fallback_lifecycle(setup):
    client, settings, credentials = setup
    client.app.state.settings = replace(settings, nfc_issuer_origin='',
                                       nfc_allowed_hosts=('safebox.example',))
    assert 'action="/nfc/prepare"' in client.get('/nfc').text
    payload = prepared(setup)
    metadata = cards.decode_card(settings, base64.b64decode(payload), cards.owner(credentials))
    assert metadata['issuer'] == 'https://safebox.example'
    assert client.app.state.settings.nfc_issuer_origin == ''
    assert post(setup, 'read', card_payload=payload).status_code == 200
    assert post(setup, 'activate', card_payload=payload,
                revision=str(policy(setup).revision), confirmed='yes').status_code == 303


@pytest.mark.parametrize('url,headers', [
    ('https://attacker.example/nfc/prepare', {'X-Forwarded-Host': 'safebox.example'}),
    ('https://safebox.example.attacker.example/nfc/prepare', {}),
    ('https://safebox.example:8443/nfc/prepare', {}),
    ('http://safebox.example/nfc/prepare', {'X-Forwarded-Proto': 'https',
      'Forwarded': 'proto=https;host=safebox.example'}),
])
def test_fallback_rejects_untrusted_origin(setup, url, headers):
    client, settings, _ = setup
    client.app.state.settings = replace(settings, nfc_issuer_origin='',
                                       nfc_allowed_hosts=('safebox.example',))
    response = client.post(url, headers=headers, data={
        'csrf_token': CsrfProtector(settings).issue(), 'pin': '123456',
        'pin_repeat': '123456', 'confirmed': 'yes'}, follow_redirects=False)
    # The application-wide HTTPS guard rejects HTTP before the NFC route.
    assert response.status_code == (400 if url.startswith('http:') else 403)
    assert policy(setup) is None


def test_empty_allowlist_disabled_and_override_precedence(setup):
    client, settings, _ = setup
    client.app.state.settings = replace(settings, nfc_issuer_origin='')
    assert 'action="/nfc/prepare"' not in client.get('/nfc').text
    assert post(setup, 'prepare').status_code == 503
    client.app.state.settings = replace(settings, nfc_issuer_origin='https://canonical.example',
                                       nfc_allowed_hosts=('safebox.example',))
    assert post(setup, 'prepare').status_code == 403


@pytest.mark.parametrize('host', ['*', '*.example', 'https://safebox.example',
    'user@safebox.example', 'safebox.example/path', 'safebox.example:99999',
    'safebox.example:', 'safebox.example\n', 'safebox.example,attacker.example'])
def test_allowlist_validation(setup, host):
    with pytest.raises(ValueError):
        replace(setup[1], nfc_allowed_hosts=(host,))


def test_proxy_scheme_is_used_only_from_trusted_proxy(setup):
    from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
    client, settings, _ = setup
    client.app.state.settings = replace(settings, nfc_issuer_origin='',
                                       nfc_allowed_hosts=('safebox.example',))
    for trusted, expected in [('testclient', 200), ('192.0.2.1', 400)]:
        proxy_client = TestClient(ProxyHeadersMiddleware(client.app, trusted_hosts=trusted),
                                 base_url='http://safebox.example')
        response = proxy_client.post('/nfc/prepare', headers={'X-Forwarded-Proto': 'https'}, data={
            'csrf_token': CsrfProtector(settings).issue(), 'pin': '123456',
            'pin_repeat': '123456', 'confirmed': 'yes'}, follow_redirects=False)
        assert response.status_code == expected

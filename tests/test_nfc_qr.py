import base64
from html import unescape
import re

import pytest
from fastapi.testclient import TestClient

from app import nfc_cards as cards, nfc_qr
from app.security import CsrfProtector, SECURE_COOKIE_NAME
from test_nfc_cards import setup, policy, prepared, post
from test_nfc_login import active, fields, attempt


@pytest.mark.parametrize('size', [1, 2, 3, 171, 399, 400])
def test_text_transport_exact_round_trip(size):
    payload = bytes(range(256)) * (size // 256) + bytes(range(size % 256))
    text = nfc_qr.encode(payload)
    assert nfc_qr.decode(text) == payload
    assert len(text) <= 550
    assert '=' not in text


@pytest.mark.parametrize('text', ['https://example.com', 'safebox-card:v2:AA',
    'safebox-card:v1:', 'safebox-card:v1:AA==', 'safebox-card:v1:AB',
    'safebox-card:v1:A', 'safebox-card:v1:AA\n', 'safebox-card:v1:' + 'A'*535])
def test_reject_invalid_or_noncanonical_qr(text):
    with pytest.raises(ValueError):
        nfc_qr.decode(text)


def export(active):
    setup, encoded = active
    return post(setup, 'qr', card_payload=encoded, qr_confirmed='yes')


def qr_text(response):
    return unescape(re.search(r'<textarea id="login-qr-text"[^>]*>([^<]+)</textarea>', response.text)[1])


def test_export_exact_card_bytes_no_pin_and_no_policy_change(active, monkeypatch):
    before = policy(active[0])
    values = []
    original = nfc_qr.svg
    def render(text):
        values.append(text)
        return original(text)
    monkeypatch.setattr(nfc_qr, 'svg', render)
    response = export(active)
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    assert '<svg ' in response.text
    assert 'data-card-generated-pin' not in response.text
    assert active[0][2].nsec not in response.text
    text = qr_text(response)
    assert values == [text]
    assert nfc_qr.decode(text) == base64.b64decode(active[1])
    assert policy(active[0]).revision == before.revision
    assert policy(active[0]).active_generation == before.active_generation


def test_export_requires_session_confirmation_and_csrf(active):
    setup, encoded = active
    assert post(setup, 'qr', card_payload=encoded).status_code == 400
    assert setup[0].post('/nfc/qr', data={'card_payload': encoded, 'qr_confirmed':'yes'}).status_code == 403
    from app.dependencies import get_session_credentials
    saved = setup[0].app.dependency_overrides.pop(get_session_credentials)
    try:
        client = TestClient(setup[0].app, base_url='https://safebox.example')
        response = client.post('/nfc/qr', data={'csrf_token':CsrfProtector(setup[1]).issue(),
            'card_payload': encoded, 'qr_confirmed':'yes'})
        assert response.status_code == 401
    finally:
        setup[0].app.dependency_overrides[get_session_credentials] = saved


def qr_form(active, text=None):
    setup, encoded = active
    form = fields(setup[0].get('/nfc/login'))
    form['qr_payload'] = text or nfc_qr.encode(base64.b64decode(encoded))
    response = setup[0].post('/nfc/login/read', data=form, follow_redirects=False)
    assert response.status_code == 200, response.text
    assert SECURE_COOKIE_NAME not in response.cookies
    result = fields(response)
    assert result['card_payload'] == encoded
    result['pin'] = '0007'
    return result


def test_qr_login_shares_pin_and_single_use_challenge(active):
    form = qr_form(active, qr_text(export(active)))
    response = active[0][0].post('/nfc/login', data=form, follow_redirects=False)
    assert response.status_code == 303
    assert SECURE_COOKIE_NAME in response.cookies
    assert active[0][0].post('/nfc/login', data=form, follow_redirects=False).status_code == 400


def test_qr_and_nfc_share_lockout_and_revocation(active):
    for _ in range(4):
        assert attempt(active, '9999')[0].status_code == 400
    form = qr_form(active)
    form['pin'] = '9999'
    assert active[0][0].post('/nfc/login', data=form).status_code == 400
    assert policy(active[0]).login_failures == 5
    form = qr_form(active)
    assert active[0][0].post('/nfc/login', data=form).status_code == 400


def test_revoked_card_qr_cannot_login_or_be_exported(active):
    setup, _ = active
    form = qr_form(active)
    cards.compare_update(setup[0].app.state.database_engine, cards.owner(setup[2]),
                         policy(setup).revision, active_generation=None)
    assert setup[0].post('/nfc/login', data=form).status_code == 400
    assert export(active).status_code == 409


def test_pending_qr_export_can_activate_same_credential(setup):
    encoded = prepared(setup)
    response = export((setup, encoded))
    assert response.status_code == 200
    assert 'Pending activation' in response.text
    assert 'action="/nfc/activate"' in response.text
    assert nfc_qr.decode(qr_text(response)) == base64.b64decode(encoded)
    assert post(setup, 'activate', card_payload=encoded, revision=str(policy(setup).revision),
                confirmed='yes').status_code == 303


def test_ambiguous_credentials_and_url_qrs_rejected(active):
    setup, encoded = active
    form = fields(setup[0].get('/nfc/login'))
    for values in ({'qr_payload':'https://attacker.example'},
                   {'qr_payload':nfc_qr.encode(base64.b64decode(encoded)), 'card_payload':encoded}):
        response = setup[0].post('/nfc/login/read', data={**form, **values})
        assert response.status_code == 400
        assert SECURE_COOKIE_NAME not in response.cookies
    assert 'worker-src' in setup[0].get('/nfc/login').headers['content-security-policy']

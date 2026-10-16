import base64
import hashlib
import re
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from sqlmodel import Session

from test_nfc_cards import setup, policy
from app import nfc_cards as cards
from app import nfc_login as login
from app.models import NfcCardPolicy
from app.security import CsrfProtector, SessionCipher, SECURE_COOKIE_NAME


@pytest.fixture
def active(setup):
    client, settings, credentials = setup
    payload, generation, expires = cards.encode_card(settings, credentials, '0007', 90)
    with Session(client.app.state.database_engine) as session:
        session.add(NfcCardPolicy(npub=cards.owner(credentials), active_generation=generation,
                                 active_expires_at=expires))
        session.commit()
    return setup, base64.b64encode(payload).decode()


def fields(response):
    return dict(re.findall(r'name="([^"]+)" value="([^"]*)"', response.text))


def read_form(active, *, payload=None, client=None):
    setup, encoded = active
    client = client or setup[0]
    response = client.get('/nfc/login')
    assert response.status_code == 200, response.text
    form = fields(response)
    form['card_payload'] = payload if payload is not None else encoded
    response = client.post('/nfc/login/read', data=form, follow_redirects=False)
    assert response.status_code == 200, response.text
    assert SECURE_COOKIE_NAME not in response.cookies
    assert 'value="0007"' not in response.text
    assert setup[2].nsec not in response.text
    return fields(response)


def attempt(active, pin='0007', **kwargs):
    form = read_form(active, **kwargs)
    form['pin'] = pin
    return active[0][0].post('/nfc/login', data=form, follow_redirects=False), form


def test_success_creates_standard_session_without_record_unlock(active):
    response, form = attempt(active)
    assert response.status_code == 303
    assert response.headers['location'] == '/wallet'
    assert response.headers['cache-control'] == 'no-store'
    cookie = response.cookies[SECURE_COOKIE_NAME]
    credentials = SessionCipher(active[0][1]).decode(cookie)
    assert credentials.nsec == active[0][2].nsec
    assert credentials.bootstrap_relay == active[0][2].bootstrap_relay
    assert credentials.record_protection_key is None
    assert 'Secure' in response.headers['set-cookie']
    assert 'HttpOnly' in response.headers['set-cookie']
    assert 'SameSite=strict' in response.headers['set-cookie']
    assert active[0][0].post('/nfc/login', data=form, follow_redirects=False).status_code == 400


@pytest.mark.parametrize('pin', ['', '7', '0008', '☃', '0007 '])
def test_invalid_pin_never_issues_session_and_consumes_challenge(active, pin):
    response, form = attempt(active, pin)
    assert response.status_code == 400
    assert SECURE_COOKIE_NAME not in response.cookies
    assert policy(active[0]).login_failures == 1
    form['pin'] = '0007'
    assert active[0][0].post('/nfc/login', data=form).status_code == 400
    assert policy(active[0]).login_failures == 1


def test_lockout_persists_across_browsers_and_new_db_connections(active):
    for _ in range(5):
        assert attempt(active, '9999')[0].status_code == 400
    assert policy(active[0]).login_failures == 5
    other = TestClient(active[0][0].app, base_url='https://safebox.example')
    form = read_form(active, client=other)
    form['pin'] = '0007'
    response = other.post('/nfc/login', data=form, follow_redirects=False)
    assert response.status_code == 400
    assert SECURE_COOKIE_NAME not in response.cookies
    assert policy(active[0]).login_failures == 5


def test_replacement_resets_lockout_but_old_card_stays_invalid(active):
    setup, old = active
    engine = setup[0].app.state.database_engine
    cards.compare_update(engine, cards.owner(setup[2]), policy(setup).revision, login_failures=5)
    payload, generation, expires = cards.encode_card(setup[1], setup[2], '0007', 90)
    cards.compare_update(engine, cards.owner(setup[2]), policy(setup).revision,
        pending_generation=generation, pending_digest=hashlib.sha256(payload).hexdigest(),
        pending_deadline=cards.now()+300)
    encoded = base64.b64encode(payload).decode()
    response = setup[0].post('/nfc/activate', data={
        'csrf_token':CsrfProtector(setup[1]).issue(), 'card_payload':encoded,
        'revision':str(policy(setup).revision), 'confirmed':'yes'}, follow_redirects=False)
    assert response.status_code == 303
    assert policy(setup).login_failures == 0
    assert attempt(active)[0].status_code == 400
    assert attempt((setup, encoded))[0].status_code == 303


def test_revocation_after_read_and_pending_cards_rejected(active):
    setup, _ = active
    form = read_form(active)
    cards.compare_update(setup[0].app.state.database_engine, cards.owner(setup[2]),
                         policy(setup).revision, active_generation=None)
    form['pin'] = '0007'
    response = setup[0].post('/nfc/login', data=form, follow_redirects=False)
    assert response.status_code == 400
    assert SECURE_COOKIE_NAME not in response.cookies


def test_expired_card_rejected(active, monkeypatch):
    clock = cards.now()
    monkeypatch.setattr(cards, 'now', lambda: clock + 91*86400)
    response, _ = attempt(active)
    assert response.status_code == 400
    assert SECURE_COOKIE_NAME not in response.cookies


def test_challenge_expiry_csrf_and_browser_binding(active, monkeypatch):
    form = read_form(active)
    form['pin'] = '0007'
    client = active[0][0]
    other = TestClient(client.app, base_url='https://safebox.example')
    assert other.post('/nfc/login', data=form).status_code == 400
    assert client.post('/nfc/login', data={**form, 'csrf_token':'bad'}).status_code == 403
    clock = login.now()
    monkeypatch.setattr(login, 'now', lambda: clock + 301)
    assert client.post('/nfc/login', data=form).status_code == 400


def test_payload_bound_to_read_challenge(active):
    form = read_form(active)
    form['pin'] = '0007'
    form['card_payload'] = base64.b64encode(b'different').decode()
    response = active[0][0].post('/nfc/login', data=form, follow_redirects=False)
    assert response.status_code == 400
    assert SECURE_COOKIE_NAME not in response.cookies


def test_tampered_card_and_wrong_issuer_rejected(active):
    from dataclasses import replace
    setup, encoded = active
    payload = base64.b64decode(encoded)
    altered = payload[:-1] + bytes([payload[-1] ^ 1])
    assert attempt(active, payload=base64.b64encode(altered).decode())[0].status_code == 400
    foreign, _, _ = cards.encode_card(replace(setup[1], nfc_issuer_origin='https://other.example'),
                                     setup[2], '0007', 90)
    assert attempt(active, payload=base64.b64encode(foreign).decode())[0].status_code == 400
    assert policy(setup).login_failures == 0


def test_parallel_submission_of_same_challenge_issues_at_most_one_session(active):
    setup, _ = active
    form = read_form(active)
    form['pin'] = '0007'
    cookie = setup[0].cookies[login.BROWSER_COOKIE]
    def submit(_):
        client = TestClient(setup[0].app, base_url='https://safebox.example')
        client.cookies.set(login.BROWSER_COOKIE, cookie)
        return client.post('/nfc/login', data=form, follow_redirects=False)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(submit, range(2)))
    assert sorted(r.status_code for r in responses) == [303, 400]
    assert sum(SECURE_COOKIE_NAME in r.cookies for r in responses) == 1


@pytest.mark.parametrize('payload', [b'test marker', b'\xa0', b'\x00'])
def test_malformed_card_has_generic_failure(active, payload):
    response, _ = attempt(active, payload=base64.b64encode(payload).decode())
    assert response.status_code == 400
    assert login.FAILURE in response.text
    assert SECURE_COOKIE_NAME not in response.cookies


def test_source_rate_limit_and_bounded_form(active):
    client = active[0][0]
    assert client.post('/nfc/login', content='x'*4097,
        headers={'content-type':'application/x-www-form-urlencoded'}).status_code == 413
    for _ in range(login.SOURCE_LIMIT - 1):
        assert client.get('/nfc/login').status_code == 200
    assert client.get('/nfc/login').status_code == 429


def test_database_failure_cannot_issue_session(active, monkeypatch):
    form = read_form(active)
    form['pin'] = '0007'
    def fail(*args):
        raise OperationalError('test', {}, Exception('unavailable'))
    monkeypatch.setattr(login, 'authorize', fail)
    response = active[0][0].post('/nfc/login', data=form)
    assert response.status_code == 503
    assert SECURE_COOKIE_NAME not in response.cookies


def test_parallel_wrong_attempts_never_exceed_budget(active):
    setup, encoded = active
    metadata = cards.decode_card(setup[1], base64.b64decode(encoded), cards.owner(setup[2]))
    engine = setup[0].app.state.database_engine
    with ThreadPoolExecutor(max_workers=4) as pool:
        assert not any(pool.map(lambda _: login.authorize(engine, metadata, False), range(12)))
    assert 1 <= policy(setup).login_failures <= 5
    # Exhaust any remaining budget. Correct PIN cannot bypass the lock.
    for _ in range(5):
        login.authorize(engine, metadata, False)
    assert not login.authorize(engine, metadata, True)

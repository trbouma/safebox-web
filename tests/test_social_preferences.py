from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select
from stroma import Keys

from app.main import create_app
from app.models import ClaimedHandle
from app.dependencies import get_acorn
from app.security import SessionCipher, SessionCredentials, SECURE_COOKIE_NAME, CsrfProtector
from app.social_identity import validate_social_npub
from test_app import database_settings, TEST_NSEC

SOCIAL = Keys(priv_k='02'*32)
WALLET = Keys(priv_k=TEST_NSEC)


@pytest.mark.parametrize('value', ['nsec1secret', 'a'*64, 'npub1invalid', 'npub1'+'q'*58, '<script>'])
def test_invalid_public_key(value):
    with pytest.raises(ValueError):
        validate_social_npub(value)


def test_social_setting_does_not_change_wallet_delivery(tmp_path):
    settings = replace(database_settings(tmp_path), nip05_external_relays=('wss://relay.example.com',))
    app = create_app(settings)
    credentials = SessionCredentials(nsec=TEST_NSEC, bootstrap_relay='wss://relay.example.com')
    with TestClient(app, base_url='https://safebox.example') as client:
        with Session(app.state.database_engine) as db:
            db.add(ClaimedHandle(claimed_handle='alice', npub=WALLET.public_key_bech32(), home_relay='ws://private-grove:8080'))
            db.commit()
        assert client.get('/preferences').status_code == 401
        client.cookies.set(SECURE_COOKIE_NAME, SessionCipher(settings).encode(credentials), domain='safebox.example',path='/')
        before = client.get('/.well-known/safebox.json?name=alice').json()
        data = {'currency':'USD','language':'en','social_npub':SOCIAL.public_key_bech32(), 'csrf_token':CsrfProtector(settings).issue()}
        assert client.post('/preferences',data={**data,'csrf_token':'bad'}).status_code == 403
        assert client.post('/preferences',data=data, follow_redirects=False).status_code == 303
        assert SOCIAL.public_key_bech32() in client.get('/preferences').text
        assert client.get('/.well-known/nostr.json?name=alice').json() == {'names':{'alice':SOCIAL.public_key_hex()}}
        assert client.get('/.well-known/safebox.json?name=alice').json() == before
        with Session(app.state.database_engine) as db:
            row = db.exec(select(ClaimedHandle)).one()
            assert row.npub == WALLET.public_key_bech32() and row.home_relay == 'ws://private-grove:8080'
        # Local wallet messages must not call social identity lookup.
        acorn = SimpleNamespace(pubkey_bech32=WALLET.public_key_bech32(), get_private_messages=AsyncMock(), secure_dm=AsyncMock())
        app.dependency_overrides[get_acorn] = lambda: acorn
        assert client.post('/messages',data={'csrf_token':data['csrf_token'],'recipient':'alice@safebox.example','message':'hello'},follow_redirects=False).status_code == 303
        assert acorn.secure_dm.await_args.args[0] == WALLET.public_key_bech32()
        assert acorn.secure_dm.await_args.kwargs['dm_relays'] == ['ws://private-grove:8080']
        app.dependency_overrides.clear()
        response = client.post('/preferences',data={**data,'social_npub':'nsec1secret'})
        assert response.status_code == 400 and 'nsec1secret' not in response.text
        # Older preference forms omit this field and must preserve it.
        assert client.post('/preferences',data={k:v for k,v in data.items() if k!='social_npub'},follow_redirects=False).status_code == 303
        client.cookies.clear()
        assert client.get('/.well-known/nostr.json?name=alice').json()['names']['alice'] == SOCIAL.public_key_hex()
        other = SessionCredentials(nsec=Keys(priv_k='03'*32).private_key_bech32(),bootstrap_relay='wss://relay.example.com')
        client.cookies.set(SECURE_COOKIE_NAME,SessionCipher(settings).encode(other),domain='safebox.example',path='/')
        assert SOCIAL.public_key_bech32() not in client.get('/preferences').text
        assert client.post('/preferences',data=data).status_code == 400
        client.cookies.clear()
        client.cookies.set(SECURE_COOKIE_NAME,SessionCipher(settings).encode(credentials),domain='safebox.example',path='/')
        assert client.post('/preferences',data={**data,'social_npub':''},follow_redirects=False).status_code == 303
        assert client.get('/.well-known/nostr.json?name=alice').json()['names']['alice'] == WALLET.public_key_hex()
        assert client.get('/.well-known/safebox.json?name=alice').json() == before


def test_migration_preserves_existing_wallet_binding(tmp_path):
    from alembic import command
    from alembic.config import Config
    from app.database import PROJECT_ROOT
    from sqlalchemy import create_engine, text
    config = Config(str(PROJECT_ROOT/'alembic.ini'))
    config.set_main_option('script_location', str(PROJECT_ROOT/'alembic'))
    url = 'sqlite:///'+str(tmp_path/'migration.db')
    config.set_main_option('sqlalchemy.url', url)
    command.upgrade(config, '20261016_0015')
    engine = create_engine(url)
    with engine.begin() as db:
        db.execute(text('INSERT INTO claimed_handle(claimed_handle,npub,home_relay) VALUES(:name,:key,:relay)'),
                   {'name':'alice','key':WALLET.public_key_bech32(),'relay':'ws://grove:8080'})
    command.upgrade(config, 'head')
    with engine.begin() as db:
        row = db.execute(text('SELECT npub,home_relay,social_npub FROM claimed_handle')).one()
        assert tuple(row) == (WALLET.public_key_bech32(), 'ws://grove:8080', None)
        db.execute(text('UPDATE claimed_handle SET social_npub=:key'), {'key':SOCIAL.public_key_bech32()})
    with pytest.raises(RuntimeError, match='Clear social identities'):
        command.downgrade(config,'20261016_0015')
    with engine.begin() as db:
        db.execute(text('UPDATE claimed_handle SET social_npub=NULL'))
    command.downgrade(config,'20261016_0015')
    engine.dispose()

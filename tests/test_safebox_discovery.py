import builtins
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session
from stroma import Keys
from acorn.safebox_discovery import parse_safebox_document, CASH, CLEAR
from app.main import create_app
from app.models import ClaimedHandle
from test_app import database_settings


def setup_directory(tmp_path, **changes):
    settings = replace(database_settings(tmp_path), nip05_external_relays=('wss://relay.example.com',), **changes)
    return create_app(settings)


def register(app):
    keys = Keys(priv_k='01'*32)
    with Session(app.state.database_engine) as session:
        session.add(ClaimedHandle(claimed_handle='alice', npub=keys.public_key_bech32(), home_relay='ws://private-grove:8080'))
        session.commit()
    return keys.public_key_hex()


def test_public_directory_keeps_nip05_and_hides_private_routes(tmp_path):
    app = setup_directory(tmp_path, clear_receive_enabled=True, clear_units=('CAD',),
                          clear_external_mints=('https://mint.example',))
    with TestClient(app, base_url='https://safebox.example') as client:
        pubkey = register(app)
        before = client.get('/.well-known/nostr.json?name=alice').json()
        response = client.get('/.well-known/safebox.json?name=Alice')
        assert response.status_code == 200
        result = parse_safebox_document(response.json(), 'alice@safebox.example')
        assert result.pubkey == pubkey and result.supports(CASH, 'sat')
        assert result.supports(CLEAR, 'CAD') and not result.supports(CLEAR, 'USD')
        assert result.supports('safebox-wallet-message')
        assert 'private-grove' not in response.text and 'mints' not in response.text
        assert client.get('/.well-known/nostr.json?name=alice').json() == before
        assert response.headers['access-control-allow-origin'] == '*'
        assert response.headers['cache-control'] == 'no-store'
        assert response.headers['x-content-type-options'] == 'nosniff'


@pytest.mark.parametrize('query', ['', '?name=alice&name=bob', '?name=', '?name=a%40b', '?name='+'x'*65])
def test_invalid_names(tmp_path, query):
    with TestClient(setup_directory(tmp_path), base_url='https://safebox.example') as client:
        response = client.get('/.well-known/safebox.json'+query)
        assert response.status_code == 400 and response.json() == {'error':'invalid_name'}


def test_missing_or_unadvertised_recipient(tmp_path):
    app = create_app(database_settings(tmp_path))
    with TestClient(app, base_url='https://safebox.example') as client:
        assert client.get('/.well-known/safebox.json?name=missing').status_code == 404
        register(app)
        # A private home relay is never a substitute for configured public routing.
        assert client.get('/.well-known/safebox.json?name=alice').status_code == 404
        assert client.get('/.well-known/nostr.json?name=alice').status_code == 200


def test_old_dependency_does_not_break_nip05(tmp_path, monkeypatch):
    app = setup_directory(tmp_path)
    with TestClient(app, base_url='https://safebox.example') as client:
        register(app)
        real_import = builtins.__import__
        def old_import(name, *args, **kwargs):
            if name == 'acorn.safebox_discovery':
                raise ModuleNotFoundError(name)
            return real_import(name, *args, **kwargs)
        monkeypatch.setattr(builtins, '__import__', old_import)
        assert client.get('/.well-known/safebox.json?name=alice').status_code == 503
        assert client.get('/.well-known/nostr.json?name=alice').status_code == 200

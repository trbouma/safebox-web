import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient
from app import lightning_invoice as ln
from app.main import create_app
from app.outgoing_payment import claim_outgoing_payment_job, run_outgoing_payment_job, get_outgoing_payment_job
from test_app import database_settings


def mocks(monkeypatch, **changes):
    metadata = {'tag':'payRequest','minSendable':1000,'maxSendable':100000,
                'callback':'https://provider.example/callback?token=x&safebox=true&comment=old',
                'safebox':True, 'nonce':'legacy-nonce', **changes}
    fetch = AsyncMock(side_effect=[metadata, {'pr':'invoice'}])
    monkeypatch.setattr(ln, 'public_json', fetch)
    monkeypatch.setattr(ln.bolt11,'decode', lambda invoice:SimpleNamespace(currency='bc',amount_msat=21000,date=int(time.time()),expiry=300))
    return fetch


@pytest.mark.parametrize('comments', [0,100])
def test_invoice_ignores_safebox_flag_and_obeys_comments(monkeypatch, comments):
    fetch=mocks(monkeypatch, commentAllowed=comments)
    assert asyncio.run(ln.resolve_lightning_invoice('alice@example.com',21,'hello')) == 'invoice'
    assert fetch.await_count == 2
    params=fetch.await_args.args[1]
    assert params == {'token':'x','amount':'21000', **({'comment':'hello'} if comments else {})}
    assert all('safebox.json' not in call.args[0] and 'nostr.json' not in call.args[0] for call in fetch.await_args_list)


@pytest.mark.parametrize('change', [{'currency':'tb'}, {'amount_msat':22000}, {'date':0,'expiry':1}])
def test_wrong_invoice_never_accepted(monkeypatch, change):
    mocks(monkeypatch)
    decoded={'currency':'bc','amount_msat':21000,'date':int(time.time()),'expiry':300,**change}
    monkeypatch.setattr(ln.bolt11,'decode',lambda invoice:SimpleNamespace(**decoded))
    with pytest.raises(ValueError,match='No payment was submitted'):
        asyncio.run(ln.resolve_lightning_invoice('alice@example.com',21))


def test_address_job_only_calls_invoice_api(tmp_path, monkeypatch):
    mocks(monkeypatch)
    app=create_app(database_settings(tmp_path))
    wallet=SimpleNamespace(pay_multi=AsyncMock(side_effect=AssertionError('Never call legacy address payer')),
        pay_multi_invoice=AsyncMock(return_value=('Paid',0,'hash','preimage',None)))
    with TestClient(app) as client:
        engine=app.state.database_engine
        claimed,token,_=claim_outgoing_payment_job(engine,'npub-test',payment_kind='address',recipient='alice@example.com',amount=21)
        assert claimed
        asyncio.run(run_outgoing_payment_job(engine=engine,acorn=wallet,npub='npub-test',owner_token=token,
                    payment_kind='address',recipient='alice@example.com',amount=21,comment='hello'))
        wallet.pay_multi.assert_not_awaited()
        wallet.pay_multi_invoice.assert_awaited_once()
        assert wallet.pay_multi_invoice.await_args.kwargs['lninvoice'] == 'invoice'
        assert get_outgoing_payment_job(engine,'npub-test')['status'] == 'COMPLETE'


def test_invoice_fetch_rejects_private_dns(monkeypatch):
    async def run():
        monkeypatch.setattr(asyncio.get_running_loop(),'getaddrinfo',AsyncMock(return_value=[(2,1,6,'',('127.0.0.1',443))]))
        with pytest.raises(ValueError):
            await ln.public_json('https://provider.example/callback')
    asyncio.run(run())


def test_invoice_fetch_pins_dns_and_refuses_redirect(monkeypatch):
    original=httpx.AsyncClient
    seen=[]
    def handler(request):
        seen.append(request)
        assert request.url.host == '8.8.8.8'
        assert request.headers['host'] == 'provider.example'
        assert request.extensions['sni_hostname'] == 'provider.example'
        return httpx.Response(302,headers={'location':'http://127.0.0.1/secret'})
    async def run():
        monkeypatch.setattr(asyncio.get_running_loop(),'getaddrinfo',AsyncMock(return_value=[(2,1,6,'',('8.8.8.8',443))]))
        monkeypatch.setattr(ln.httpx,'AsyncClient',lambda **kwargs:original(**kwargs,transport=httpx.MockTransport(handler)))
        with pytest.raises(httpx.HTTPStatusError):
            await ln.public_json('https://provider.example/callback')
    asyncio.run(run())
    assert len(seen)==1

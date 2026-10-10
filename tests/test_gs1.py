import asyncio
import base64
import hashlib

import httpx
import pytest
from stroma import Event

from app.gs1 import parse_product_link, product_associations, retrieve_product_artifact
from app.openetr import build_openetr_history, unavailable_openetr_history

GTIN = "09520123456788"
BASE = f"https://printed.invalid/01/{GTIN}"
DATA = b"Product information\x00\xff"
DIGEST = hashlib.sha256(DATA).hexdigest()


@pytest.mark.parametrize("reference", [DIGEST, base64.urlsafe_b64encode(bytes.fromhex(DIGEST)).decode().rstrip("=")])
@pytest.mark.parametrize("parameter", ["d", "digest"])
def test_product_link(reference, parameter):
    product = parse_product_link(BASE + f"/10/LOT%2B01/21/000123?{parameter}={reference}")
    assert (product.gtin, product.lot, product.serial, product.digest) == (GTIN, "LOT+01", "000123", DIGEST)


def test_optional_fields_and_non_product():
    assert parse_product_link(BASE).digest is None
    assert parse_product_link(BASE + "/21/ABC").lot is None
    assert parse_product_link(BASE.replace("https:", "http:")).gtin == GTIN
    assert parse_product_link("https://example.com/help") is None
    assert parse_product_link("lnbc1234") is None


@pytest.mark.parametrize("link", [
    BASE + "?d=" + DIGEST + "&digest=" + DIGEST,
    BASE + "?d=" + DIGEST + "&d=" + DIGEST,
    BASE + "?d=", BASE + "?d=" + DIGEST.upper(), BASE + "?d=" + "B" * 43,
    BASE + "?expiry=123", BASE + "?d=" + DIGEST + "&other=1",
    BASE + "/10/", BASE + "/10/one/10/two", BASE + "/21/one/10/two",
    BASE + "/22/123", BASE + "/10/a%2Fb", BASE + "/10/%2e%2e",
    BASE + "/10/%FF", BASE + "/10/%GG", BASE + "/10/a%20b",
    BASE + "/21/" + "x" * 21, BASE + "#fragment",
    BASE.replace(GTIN, GTIN[:-1] + "9"), BASE.replace(GTIN, GTIN[1:]),
    BASE.replace("printed.invalid", "user:pass@printed.invalid"),
    BASE.replace("printed.invalid", "printed.invalid:bad"),
])
def test_bad_product_links(link):
    with pytest.raises(ValueError):
        parse_product_link(link)


def anchor(*tags):
    event = Event(kind=1415, created_at=100, content="Product", tags=[
        ["o", DIGEST], ["action", "issue"], *tags])
    event.sign("01" * 32)
    return event


def test_signed_product_associations():
    matching = anchor(["gs1_gtin", GTIN], ["gs1_lot", "one"])
    mismatch = anchor(["gs1_gtin", GTIN], ["gs1_lot", "two"])
    repeated = anchor(["gs1_gtin", GTIN], ["gs1_gtin", GTIN], ["gs1_lot", "one"])
    extra = anchor(["gs1_gtin", GTIN], ["gs1_lot", "one"], ["gs1_serial", "extra"])
    unsigned = anchor(["gs1_gtin", GTIN], ["gs1_lot", "one"])
    unsigned._sig = "00" * 64
    history = build_openetr_history(DIGEST, [matching, mismatch, repeated, extra, unsigned], ("wss://relay.invalid",))
    results = product_associations(parse_product_link(BASE + "/10/one?d=" + DIGEST), history)
    assert len(results) == 4
    assert [r["event_id"] for r in results if r["matches"]] == [matching.id]
    assert product_associations(parse_product_link(BASE), unavailable_openetr_history(DIGEST, ())) == []
    forged = dict(matching.data(), tags=[["o", DIGEST], ["action", "issue"],
                                       ["gs1_gtin", GTIN], ["gs1_lot", "forged"]])
    history["verification"]["evidence"].append(forged)
    results = product_associations(parse_product_link(BASE + "/10/one?d=" + DIGEST), history)
    assert [r["event_id"] for r in results if r["matches"]] == [matching.id]


class Stream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield DATA[:5]
        yield DATA[5:]


def mock_storage(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr("app.gs1.httpx.AsyncClient", lambda **kwargs: original(
        transport=httpx.MockTransport(handler), **kwargs))


def test_storage_exact_bytes_and_fallback(monkeypatch):
    requests = []
    def handle(request):
        requests.append(str(request.url))
        assert request.headers["accept-encoding"] == "identity"
        assert "authorization" not in request.headers
        if request.url.host == "first.invalid":
            return httpx.Response(404)
        return httpx.Response(200, stream=Stream())
    mock_storage(monkeypatch, handle)
    result = asyncio.run(retrieve_product_artifact(DIGEST, ("https://first.invalid", "https://second.invalid"), timeout=1, max_bytes=100))
    assert result["status"] == "verified"
    assert result["data"] == DATA
    assert requests == [f"https://first.invalid/{DIGEST}", f"https://second.invalid/{DIGEST}"]


@pytest.mark.parametrize("status,headers,digest,max_bytes,expected", [
    (302, {"location": "http://127.0.0.1/private"}, DIGEST, 100, "unavailable"),
    (404, {}, DIGEST, 100, "not_found"),
    (200, {}, "00" * 32, 100, "digest_mismatch"),
    (200, {}, DIGEST, 3, "too_large"),
    (200, {"content-length": "999"}, DIGEST, 100, "too_large"),
    (200, {"content-encoding": "gzip"}, DIGEST, 100, "unsupported_encoding"),
])
def test_untrusted_storage_rejected(monkeypatch, status, headers, digest, max_bytes, expected):
    requests = []
    def handle(request):
        requests.append(request)
        return httpx.Response(status, headers=headers, stream=Stream())
    mock_storage(monkeypatch, handle)
    result = asyncio.run(retrieve_product_artifact(digest, ("https://storage.invalid",), timeout=1, max_bytes=max_bytes))
    assert result["data"] is None
    assert result["attempts"][0]["status"] == expected
    assert len(requests) == 1


def test_timeout_and_invalid_source(monkeypatch):
    async def handle(request):
        await asyncio.sleep(1)
    mock_storage(monkeypatch, handle)
    result = asyncio.run(retrieve_product_artifact(DIGEST, ("file:///tmp", "https://storage.invalid"), timeout=.01, max_bytes=100))
    assert [a["status"] for a in result["attempts"]] == ["unavailable", "unavailable"]

import asyncio

import pytest
from stroma import Event

from app.gs1 import parse_product_link, product_associations
from app.gs1_discovery import acquire_product, association_fields, discover_product, evaluate_candidate, validate_expiry
from app.openetr import build_openetr_history

GTIN = "00057606029096"
RAW = "01000576060290961727013110693773"
DIGEST = "ab" * 32


def signed(*tags, time=100, key="01" * 32):
    event = Event(kind=1415, created_at=time, content="Certificate", tags=[
        ["action", "issue"], ["o", DIGEST], ["g", GTIN], *tags])
    event.sign(key)
    return event


@pytest.mark.parametrize("raw", [RAW, "(01)00057606029096(17)270131(10)693773",
                                     "176?", "]d2" + RAW])
def test_worked_scan(raw):
    if raw == "176?":
        with pytest.raises(ValueError): acquire_product(raw, "gs1")
        return
    product = acquire_product(raw, "gs1")
    assert (product.gtin, product.lot, product.expiry, product.serial, product.digest) == (GTIN, "693773", "270131", None, None)


def test_field_order_and_separators():
    product = acquire_product("10693773\x1d1727013101" + GTIN + "21000a", "gs1")
    assert product.lot == "693773" and product.expiry == "270131" and product.serial == "000a"
    with pytest.raises(ValueError): acquire_product(RAW)
    with pytest.raises(ValueError): acquire_product("106937731727013101" + GTIN, "gs1")
    with pytest.raises(ValueError): acquire_product("(01)" + GTIN + "(10) abc", "gs1")
    assert acquire_product("\x1d" + RAW, "gs1") == acquire_product(RAW, "gs1")
    with pytest.raises(ValueError): acquire_product("(ignored)(01)" + GTIN, "gs1")


@pytest.mark.parametrize("raw", ["01" + GTIN + "01" + GTIN, "(01)" + GTIN + "(17)270131(17)270131",
                                 "01" + GTIN + "11270101", "(01)" + GTIN + "(99)abc", RAW + "\x1d"])
def test_unsupported_duplicate_and_malformed(raw):
    with pytest.raises(ValueError): acquire_product(raw, "gs1")


def test_gtin_and_upce():
    assert acquire_product("96385074", "gtin").gtin == "00000096385074"
    assert acquire_product("042100005264", symbology="upc_a").gtin == "00042100005264"
    assert acquire_product("04252614", symbology="upc_e").gtin == "00042100005264"
    with pytest.raises(ValueError): acquire_product("04252614")
    with pytest.raises(ValueError): acquire_product("04252615", symbology="upc_e")
    with pytest.raises(ValueError): acquire_product("96385074", symbology="ean_13")


@pytest.mark.parametrize("value", ["271331", "270229", "270431", "270000", "20270131", "abc123"])
def test_invalid_dates(value):
    with pytest.raises(ValueError): validate_expiry(value)


def test_expiry_digital_link_and_exact_zero_day():
    product = parse_product_link(f"https://untrusted.invalid/01/{GTIN}?17=270100&d={DIGEST}")
    assert product.expiry == "270100" and product.digest == DIGEST
    validate_expiry("000229")  # Century remains unresolved, not silently 2000.
    with pytest.raises(ValueError): parse_product_link(f"https://untrusted.invalid/01/{GTIN}?17=270131&17=270131")
    candidate = evaluate_candidate(product, signed(["gs1_expiry", "270131"]))
    assert candidate["status"] == "Mismatch"


@pytest.mark.parametrize("tags,status", [
    ([["gs1_lot", "693773"], ["gs1_expiry", "270131"]], "Exact field match"),
    ([["gs1_lot", "693774"], ["gs1_expiry", "270131"]], "Mismatch"),
    ([["gs1_lot", "693773"], ["gs1_expiry", "270228"]], "Mismatch"),
    ([["gs1_lot", "693773"]], "Incomplete"),
    ([], "General product record — incomplete"),
    ([["gs1_lot", "693773"], ["gs1_expiry", "270131"], ["gs1_serial", "001"]], "More-specific candidate"),
])
def test_local_matching(tags, status):
    assert evaluate_candidate(acquire_product(RAW, "gs1"), signed(*tags))["status"] == status


@pytest.mark.parametrize("tags", [
    [["g", GTIN]], [["gs1_gtin", "09520123456788"]], [["gs1_lot", "a"], ["gs1_lot", "a"]],
    [["gs1_expiry", "270231"]], [["gs1_serial", ""]],
])
def test_invalid_associations(tags):
    with pytest.raises(ValueError): evaluate_candidate(acquire_product(RAW, "gs1"), signed(*tags))


def test_signature_wrong_gtin_and_aliases():
    event = signed()
    event._sig = "00" * 64
    with pytest.raises(ValueError): evaluate_candidate(acquire_product(RAW, "gs1"), event)
    with pytest.raises(ValueError): evaluate_candidate(acquire_product("09520123456788"), signed())
    assert association_fields([["gs1_gtin", GTIN]])["gtin"] == GTIN
    with pytest.raises(ValueError): association_fields([["gs1_gtin", GTIN]], indexed=True)
    event = signed(["gs1_gtin", GTIN])
    product = parse_product_link(f"https://ignored.invalid/01/{GTIN}?d={DIGEST}")
    assert product_associations(product, build_openetr_history(DIGEST, [event], ()))[0]["matches"]
    other = parse_product_link(f"https://ignored.invalid/01/{GTIN}?d={'cd' * 32}")
    assert evaluate_candidate(other, event)["status"] == "Mismatch"


def test_per_relay_discovery_deduplicates_and_reports_saturation(monkeypatch):
    first, second = signed(time=200), signed(time=100, key="02" * 32)
    requests = []
    class Pool:
        def __init__(self, relays, **kwargs): self.relay = relays[0]
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def query(self, query, **kwargs):
            requests.append((self.relay, query))
            if self.relay == "wss://failed": raise TimeoutError
            return [first, second] if "until" not in query else [second, second]
    monkeypatch.setattr("app.gs1_discovery.ClientPool", Pool)
    result = asyncio.run(discover_product(acquire_product(GTIN), ("wss://one", "wss://two", "wss://failed"), timeout=.1, limit=2))
    assert len(result["candidates"]) == 2
    assert all(c["status"] == "Exact field match" for c in result["candidates"])
    assert "saturated" in result["coverage"][0]["status"]
    assert "timed out" in result["coverage"][2]["status"]
    assert result["may_be_incomplete"]
    assert all(q["#g"] == [GTIN] and q["kinds"] == [1415] and "authors" not in q and "#gs1_lot" not in q for _, q in requests)
    assert requests[1][1].get("until") == 100


def test_pagination_overlap_reaches_older_events(monkeypatch):
    events = [signed(time=t) for t in (300, 200, 100)]
    class Pool:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def query(self, query, **kwargs):
            return [e for e in events if e.data()["created_at"] <= query.get("until", 999)][:2]
    monkeypatch.setattr("app.gs1_discovery.ClientPool", Pool)
    result = asyncio.run(discover_product(acquire_product(GTIN), ("wss://one",), timeout=.1, limit=2))
    assert len(result["candidates"]) == 3
    assert result["coverage"][0]["pages"] == 3
    assert "exhausted" in result["coverage"][0]["status"]


def test_discovery_keeps_valid_copy_and_bounds_pages(monkeypatch):
    good = signed()
    bad = signed()
    bad._sig = "00" * 64
    class Pool:
        def __init__(self, *args, **kwargs): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def query(self, query, **kwargs): return [good, bad]
    monkeypatch.setattr("app.gs1_discovery.ClientPool", Pool)
    result = asyncio.run(discover_product(acquire_product(GTIN), ("wss://one",), timeout=.1, limit=2, pages=1))
    assert len(result["candidates"]) == 1 and len(result["rejected"]) == 1
    assert result["coverage"][0]["status"] == "page limit reached"

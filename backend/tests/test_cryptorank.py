"""cryptorank access layer tests — mocked HTTP, no real network, no real key."""
import asyncio

import httpx
import pytest

from backend import cryptorank


def _list_body():
    return {
        "data": [
            {
                "id": 1,
                "slug": "bitcoin",
                "symbol": "BTC",
                "name": "Bitcoin",
                "rank": 1,
                "imageUrl": "https://images.cryptorank.io/coins/150x150.bitcoin.png",
                "price": "67000.5",
                "priceChangePercent24h": 2.34,
                "marketCap": "1310000000000",
                "volume24h": "32000000000",
                "circulatingSupply": "19700000",
            },
            {
                "id": 1027,
                "slug": "ethereum",
                "symbol": "ETH",
                "name": "Ethereum",
                "rank": 2,
                "imageUrl": None,
                "price": "3150.4",
                "priceChangePercent24h": None,
                "marketCap": "379000000000",
                "volume24h": "14000000000",
                "circulatingSupply": "120400000",
            },
        ],
        "meta": {"page": 1, "pageSize": 25, "hasNextPage": True},
        "status": {"timestamp": 1780000000000, "usedCredits": 1},
    }


def _router(payload_fn):
    class Router:
        def __init__(self):
            self.requests = []

        def __call__(self, request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            return payload_fn(request)

    return Router()


def run(coro):
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _clear_cache():
    cryptorank._cr_cache.clear()
    cryptorank._credits_used = 0
    yield


def test_missing_key(monkeypatch):
    monkeypatch.delenv("CRYPTORANK_API_KEY", raising=False)
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    out = run(cryptorank.list_coins(client, limit=5))
    assert out["ok"] is False
    assert "CRYPTORANK_API_KEY" in out["reason"]


def test_list_coins_parses_and_sends_header(monkeypatch):
    monkeypatch.setenv("CRYPTORANK_API_KEY", "test-key-123")
    router = _router(lambda req: httpx.Response(200, json=_list_body()))
    client = httpx.AsyncClient(transport=httpx.MockTransport(router))
    out = run(cryptorank.list_coins(client, limit=10))
    assert out["ok"] is True
    assert out["source"] == "cryptorank"
    btc, eth = out["data"]
    assert btc["symbol"] == "BTC" and btc["rank"] == 1
    assert btc["marketCap"] == pytest.approx(1.31e12)
    assert btc["volume24h"] == pytest.approx(3.2e10)
    assert eth["priceChangePercent24h"] is None
    req = router.requests[0]
    assert req.headers["X-Api-Key"] == "test-key-123"
    assert dict(req.url.params) == {"page": "1", "sortBy": "volume24h", "sortOrder": "desc"}
    assert cryptorank.stats()["creditsUsed"] == 1


def test_list_coins_caches(monkeypatch):
    monkeypatch.setenv("CRYPTORANK_API_KEY", "k")
    router = _router(lambda req: httpx.Response(200, json=_list_body()))
    client = httpx.AsyncClient(transport=httpx.MockTransport(router))
    run(cryptorank.list_coins(client))
    second = run(cryptorank.list_coins(client))
    assert len(router.requests) == 1
    assert second.get("cached") is True
    assert cryptorank.stats()["creditsUsed"] == 1


def test_error_code_surfaced(monkeypatch):
    monkeypatch.setenv("CRYPTORANK_API_KEY", "bad-key")
    body = {"error": {"code": "PLAN_LIMIT_EXCEEDED", "message": "plan limit exceeded", "status": 403}}
    router = _router(lambda req: httpx.Response(403, json=body))
    client = httpx.AsyncClient(transport=httpx.MockTransport(router))
    out = run(cryptorank.list_coins(client, limit=5))
    assert out["ok"] is False
    assert out["status"] == 403
    assert out["code"] == "PLAN_LIMIT_EXCEEDED"


def test_symbol_map_and_block(monkeypatch):
    monkeypatch.setenv("CRYPTORANK_API_KEY", "k")
    router = _router(lambda req: httpx.Response(200, json=_list_body()))
    client = httpx.AsyncClient(transport=httpx.MockTransport(router))
    cr_map = run(cryptorank.symbol_map(client))
    assert set(cr_map) == {"BTC", "ETH"}
    block = cryptorank.block_for("BTCUSDT", cr_map)
    assert block["rank"] == 1 and block["name"] == "Bitcoin"
    assert cryptorank.block_for("DOGEUSDT", cr_map) is None
    assert cryptorank.bare_symbol(" ethusdt ") == "ETH"


def test_coin_profile_normalize(monkeypatch):
    monkeypatch.setenv("CRYPTORANK_API_KEY", "k")
    body = {
        "data": {
            "id": 1, "slug": "bitcoin", "symbol": "BTC", "name": "Bitcoin", "type": "coin",
            "rank": 1, "price": "67000.5", "marketCap": "1310000000000",
            "volume24h": "32000000000", "circulatingSupply": "19700000", "totalSupply": "21000000",
            "imageUrl": "x",
        },
        "status": {"timestamp": 1780000000000, "usedCredits": 1},
    }

    def payload(req):
        assert req.url.path == "/v3/currencies/1"
        return httpx.Response(200, json=body)

    client = httpx.AsyncClient(transport=httpx.MockTransport(payload))
    out = run(cryptorank.coin_profile(client, 1))
    assert out["ok"] is True
    assert out["data"]["totalSupply"] == pytest.approx(2.1e7)
    assert out["data"]["symbol"] == "BTC"


def test_screen_attach_survives_cryptorank_outage(monkeypatch):
    """CR down/invalid key must not break the enrichment step."""
    monkeypatch.setenv("CRYPTORANK_API_KEY", "k")
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500, json={})))
    rows = [{"symbol": "BTCUSDT"}, {"symbol": "ETHUSDT"}]

    async def _main():
        from backend.server import _attach_cryptorank

        await _attach_cryptorank(client, rows)

    run(_main())
    assert [r["cryptorank"] for r in rows] == [None, None]
    assert [r["sources"]["cryptorank"] for r in rows] == [False, False]


def test_screen_attach_enriches(monkeypatch):
    monkeypatch.setenv("CRYPTORANK_API_KEY", "k")
    router = _router(lambda req: httpx.Response(200, json=_list_body()))
    client = httpx.AsyncClient(transport=httpx.MockTransport(router))
    rows = [{"symbol": "BTCUSDT"}, {"symbol": "ETHUSDT"}, {"symbol": "DOGEUSDT"}]

    async def _main():
        from backend.server import _attach_cryptorank

        await _attach_cryptorank(client, rows)

    run(_main())
    assert rows[0]["cryptorank"]["rank"] == 1
    assert rows[1]["cryptorank"]["name"] == "Ethereum"
    assert rows[2]["cryptorank"] is None
    assert all(r["sources"]["cryptorank"] is True for r in rows)

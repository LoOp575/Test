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


# ---------------------------------------------------------------------------
# Plan diagnostics & unlock-overhang gate
# ---------------------------------------------------------------------------

PRO_STATUS = {
    "plan": "Pro",
    "rateLimitPerMinute": 60,
    "credits": {"monthlyLimit": 1000000, "monthlyUsed": 4200, "monthlyRemaining": 995800,
                "dailyLimit": 10000, "dailyUsed": 55, "dailyRemaining": 9945},
    "currentPeriodStart": "2026-10-25T00:00:00.000Z",
    "currentPeriodEnd": "2026-11-25T00:00:00.000Z",
    "endpoints": ["/v3/currencies/list", "/v3/currencies/map", "/v3/currencies/{id}",
                  "/v3/currencies/upcoming-token-unlocks", "/v3/currencies/{id}/vesting/vc-pressure"],
}

SANDBOX_STATUS = {
    "plan": "Sandbox",
    "rateLimitPerMinute": 10,
    "credits": {"monthlyLimit": 10000, "monthlyUsed": 42, "monthlyRemaining": 9958},
    "currentPeriodStart": "2026-10-25T00:00:00.000Z",
    "currentPeriodEnd": "2026-11-25T00:00:00.000Z",
    "endpoints": ["/v3/currencies/list", "/v3/currencies/map", "/v3/currencies/{id}", "/v3/status"],
}

UNLOCK_FEED = {
    "data": [
        {"currencyId": 7777, "date": "2026-11-20", "unlockValue": "50000000",
         "percentOfMcap": 4.1, "tokens": "12000000", "id": 9001},
        {"currencyId": 1, "date": "2026-11-25", "unlockValue": "30000000",
         "percentOfMcap": 1.8, "tokens": "1000000", "id": 9002},
    ],
    "meta": {"page": 1, "pageSize": 25, "hasNextPage": False},
    "status": {"timestamp": 1780000000000, "usedCredits": 1},
}


def _rime_list_body():
    body = _list_body()
    body["data"].append({
        "id": 7777, "slug": "rime", "symbol": "RIME", "name": "Rimuru", "rank": 850,
        "imageUrl": None, "price": "0.42", "priceChangePercent24h": -3.1,
        "marketCap": "1220000000", "volume24h": "85000000", "circulatingSupply": "2900000000",
    })
    return body


def _router_with(status_body, list_body=None, unlock_feed=None):
    class Router:
        def __init__(self):
            self.requests = []

        def __call__(self, request: httpx.Request) -> httpx.Response:
            self.requests.append(request)
            path = request.url.path
            if path == "/v3/status":
                return httpx.Response(200, json=status_body)
            if path == "/v3/currencies/list":
                return httpx.Response(200, json=list_body or _rime_list_body())
            if path == "/v3/currencies/upcoming-token-unlocks":
                if unlock_feed is None:
                    return httpx.Response(403, json={"error": {"code": "ENDPOINT_NOT_AVAILABLE", "message": "nope", "status": 403}})
                return httpx.Response(200, json=unlock_feed)
            return httpx.Response(404, json={})

    return Router()


def test_plan_status_parse_and_cache(monkeypatch):
    monkeypatch.setenv("CRYPTORANK_API_KEY", "k")
    router = _router_with(PRO_STATUS)
    client = httpx.AsyncClient(transport=httpx.MockTransport(router))
    out = run(cryptorank.plan_status(client))
    assert out["ok"] is True
    assert out["plan"] == "Pro"
    assert out["rateLimitPerMinute"] == 60
    assert out["credits"]["monthlyRemaining"] == 995800
    again = run(cryptorank.plan_status(client))
    assert again.get("cached") is True
    assert sum(1 for r in router.requests if r.url.path == "/v3/status") == 1


def test_plan_allows(monkeypatch):
    monkeypatch.setenv("CRYPTORANK_API_KEY", "k")
    client = httpx.AsyncClient(transport=httpx.MockTransport(_router_with(PRO_STATUS)))
    assert run(cryptorank.plan_allows(client, "upcoming-token-unlocks")) is True

    cryptorank._cr_cache.clear()
    sandbox = httpx.AsyncClient(transport=httpx.MockTransport(_router_with(SANDBOX_STATUS)))
    assert run(cryptorank.plan_allows(sandbox, "upcoming-token-unlocks")) is False


def test_upcoming_unlocks_maps_to_symbols(monkeypatch):
    monkeypatch.setenv("CRYPTORANK_API_KEY", "k")
    router = _router_with(PRO_STATUS, unlock_feed=UNLOCK_FEED)
    client = httpx.AsyncClient(transport=httpx.MockTransport(router))
    m = run(cryptorank.unlocks_by_symbol(client))
    assert set(m) == {"BTC", "RIME"}
    assert m["RIME"]["percentOfMcap"] == pytest.approx(4.1)
    assert m["BTC"]["unlockValueUsd"] == pytest.approx(3.0e7)


def test_upcoming_unlocks_denied_on_sandbox(monkeypatch):
    monkeypatch.setenv("CRYPTORANK_API_KEY", "k")
    router = _router_with(SANDBOX_STATUS)  # not in endpoints list
    client = httpx.AsyncClient(transport=httpx.MockTransport(router))
    m = run(cryptorank.unlocks_by_symbol(client))
    assert m == {}


def test_unlock_gate_downgrades_long():
    from backend.screening import apply_unlock_gate

    def mk():
        return {"ok": True, "verdict": "LONG", "filters": {}, "reasons": ["x"],
                "verdictLabel": "Long candidate", "verdictTone": "long"}

    r = mk()
    assert apply_unlock_gate(r, {"percentOfMcap": 2.0, "date": "2026-11-20"}) is True
    assert r["verdict"] == "WATCH" and r["filters"]["unlockOverhang"] is True
    assert r["verdictLabel"] == "Watchlist"

    r = mk()
    assert apply_unlock_gate(r, {"percentOfMcap": 3.5, "date": "2026-11-20"}) is True
    assert r["verdict"] == "AVOID"

    r = mk()
    assert apply_unlock_gate(r, {"percentOfMcap": 0.4, "date": "2026-11-20"}) is False
    assert r["verdict"] == "LONG" and r["filters"]["unlockOverhang"] is False

    r = mk()
    assert apply_unlock_gate(r, None) is False and r["verdict"] == "LONG"
    r = {"ok": True, "verdict": "WATCH", "filters": {}, "reasons": []}
    assert apply_unlock_gate(r, {"percentOfMcap": 9.9}) is False  # watch untouched
    assert r["verdict"] == "WATCH"


def test_screen_attach_applies_gate(monkeypatch):
    monkeypatch.setenv("CRYPTORANK_API_KEY", "k")
    router = _router_with(PRO_STATUS, unlock_feed=UNLOCK_FEED)
    client = httpx.AsyncClient(transport=httpx.MockTransport(router))
    rows = [
        {"symbol": "RIMEUSDT", "ok": True, "verdict": "LONG", "filters": {}, "reasons": [],
         "verdictLabel": "Long candidate", "verdictTone": "long"},
        {"symbol": "ETHUSDT", "ok": True, "verdict": "LONG", "filters": {}, "reasons": [],
         "verdictLabel": "Long candidate", "verdictTone": "long"},
    ]

    async def _main():
        from backend.server import _attach_cryptorank

        return await _attach_cryptorank(client, rows)

    changed = run(_main())
    assert changed == 1
    assert rows[0]["verdict"] == "AVOID"  # 4.1% >= 3.0 avoid gate
    assert rows[0]["unlock"]["percentOfMcap"] == pytest.approx(4.1)
    assert rows[0]["unlock"]["coinId"] == 7777
    assert rows[1]["verdict"] == "LONG"   # 1.8%? no wait - BTC only in feed, ETH untouched
    assert rows[1]["unlock"] is None
    assert rows[0]["sources"]["unlock"] is True

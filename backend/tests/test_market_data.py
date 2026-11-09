"""market_data fallback-chain tests — no real network.

Uses httpx.MockTransport so the exact endpoint priority and parser behaviour
are pinned down: vision -> binance.us -> binance.com/... -> mexc -> futures -> coingecko,
and the seed-fallback when everything is unreachable (e.g. geo-blocked IP).
"""
import asyncio

import httpx
import pytest

from backend import market_data

BTC = "BTCUSDT"


def _ticker_rows():
    row = {
        "symbol": BTC,
        "priceChangePercent": "1.25",
        "lastPrice": "67000.12",
        "highPrice": "68000.00",
        "lowPrice": "66000.00",
        "volume": "12345.6",
        "quoteVolume": "820000000.5",
    }
    eth = {**row, "symbol": "ETHUSDT", "quoteVolume": "410000000.0"}
    usdc = {**row, "symbol": "USDCUSDT", "quoteVolume": "99999999.0"}
    return [usdc, eth, row]


def _klines(n=120):
    rows, base = [], 1_700_000_000_000
    for i in range(n):
        t = base + i * 86_400_000
        rows.append([
            t, "100.0", "101.5", "99.0", "100.8", "1234.5", t + 86_399_999,
            "124700.0", "987", "600.0", "60480.0", "0",
        ])
    return rows


def _coingecko_rows():
    return [
        {
            "symbol": "btc",
            "current_price": 67000.0,
            "total_volume": 8.2e8,
            "high_24h": 68000.0,
            "low_24h": 66000.0,
            "price_change_percentage_24h": 1.2,
        }
    ]


class _Router:
    """Maps host -> ('json', payload) | ('status', code) | 'boom' for ConnectError."""

    def __init__(self, routes):
        self.routes = routes
        self.seen = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request.url.host)
        route = self.routes.get(request.url.host, "boom")
        if route == "boom":
            raise httpx.ConnectError("boom", request=request)
        kind, payload = route
        if kind == "json":
            return httpx.Response(200, json=payload)
        return httpx.Response(kind if isinstance(kind, int) else 500, json=payload)


def _client(router):
    return httpx.AsyncClient(transport=httpx.MockTransport(router))


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------

def test_binance_us_ticker_fallback():
    router = _Router({
        "data-api.binance.vision": "boom",
        "api.binance.us": ("json", _ticker_rows()),
    })
    client = _client(router)
    out = run(market_data.fetch_universe(client, limit=10))
    assert out["live"] is True
    assert out["source"] == "binance-us"
    symbols = [r["symbol"] for r in out["data"]]
    assert "BTCUSDT" in symbols and "ETHUSDT" in symbols
    assert "USDCUSDT" not in symbols  # stable-filtered
    assert router.seen[0] == "data-api.binance.vision"  # tried in order


def test_binance_us_klines_fallback():
    router = _Router({
        "data-api.binance.vision": "boom",
        "api.binance.us": ("json", _klines(120)),
    })
    client = _client(router)
    out = run(market_data.fetch_klines(client, BTC, 120))
    assert out["live"] is True
    assert out["source"] == "binance-us"
    assert len(out["candles"]) == 120
    assert out["candles"][0]["takerBuyBase"] == 600.0
    assert router.seen[:2] == ["data-api.binance.vision", "api.binance.us"]


def test_vision_wins_when_reachable():
    router = _Router({
        "data-api.binance.vision": ("json", _ticker_rows()),
        "api.binance.us": ("json", []),
    })
    client = _client(router)
    out = run(market_data.fetch_universe(client, limit=10))
    assert out["source"] == "binance-vision"


def test_coingecko_universe_last_resort():
    router = _Router({
        "api.coingecko.com": ("json", _coingecko_rows()),
    })
    client = _client(router)
    out = run(market_data.fetch_universe(client, limit=10))
    assert out["live"] is True
    assert out["source"] == "coingecko"
    assert out["data"][0]["symbol"] == "BTCUSDT"


def test_seed_fallback_when_everything_fails():
    router = _Router({})  # every host -> ConnectError
    client = _client(router)
    uni = run(market_data.fetch_universe(client, limit=5))
    assert uni["live"] is False
    assert uni["source"] == "seed-fallback"
    kl = run(market_data.fetch_klines(client, BTC, 120))
    assert kl["live"] is False
    assert kl["source"] == "seed-fallback"
    assert kl["warning"]


def test_short_kline_history_is_rejected():
    router = _Router({
        "data-api.binance.vision": ("json", _klines(30)),  # < 61 candles -> reject
        "api.binance.us": ("json", _klines(120)),
    })
    client = _client(router)
    out = run(market_data.fetch_klines(client, BTC, 120))
    assert out["source"] == "binance-us"


def test_normalize_drops_non_usdt_and_bad_rows():
    rows = market_data._normalize_exchange_rows([
        {"symbol": "BTCEUR", "lastPrice": "1", "quoteVolume": "10"},   # not USDT
        {"symbol": "XRPLUSDT", "lastPrice": "0", "quoteVolume": "10"},  # bad price
        {"symbol": "SOLUSDT", "lastPrice": "150", "quoteVolume": "5000000",
         "highPrice": "160", "lowPrice": "140", "priceChangePercent": "-2.5"},
    ])
    assert [r["symbol"] for r in rows] == ["SOLUSDT"]
    assert rows[0]["priceChangePercent"] == -2.5


def test_min_quote_volume_floor_filters_before_slice():
    rows = _ticker_rows() + [
        {**_ticker_rows()[0], "symbol": "DUSTUSDT", "quoteVolume": "1200000"},   # below $5m floor
        {**_ticker_rows()[0], "symbol": "MIDCAPUSDT", "quoteVolume": "5000000"}, # exactly at floor
    ]
    router = _Router({
        "data-api.binance.vision": ("json", rows),
    })
    client = _client(router)
    out = run(market_data.fetch_universe(client, limit=10, min_quote_volume=5_000_000))
    syms = [r["symbol"] for r in out["data"]]
    assert "DUSTUSDT" not in syms
    assert "MIDCAPUSDT" in syms
    assert out["universeTotal"] == len(syms) >= 3
    assert out["minQuoteVolume"] == 5_000_000


def test_min_quote_volume_zero_keeps_everything():
    router = _Router({
        "data-api.binance.vision": ("json", _ticker_rows()),
    })
    client = _client(router)
    out = run(market_data.fetch_universe(client, limit=10, min_quote_volume=0))
    assert "BTCUSDT" in [r["symbol"] for r in out["data"]]

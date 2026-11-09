"""
Meridian — market data access layer.

Live sources (in order): Binance public-data mirror (data-api.binance.vision, market data only) -> Binance.US (US-legal, same API shape) -> Binance spot -> MEXC spot -> Binance futures -> CoinGecko.
Set BINANCE_US_FIRST=1 on US-region deploys (e.g. Vercel iad1) to try Binance.US first.
OHLCV klines: Binance public-data mirror -> Binance.US -> Binance -> MEXC -> Binance futures. If every live source fails
(or SCREEN_SOURCE=seed is set), a deterministic synthetic seed universe is used
so the app stays fully usable offline. Seed output is always labelled
`source: "seed-fallback"` so callers can surface it honestly.
"""

from __future__ import annotations

import asyncio
import math
import os
import time
from typing import Any, Optional

import httpx

def _binance_us_first() -> bool:
    # Vercel functions run in iad1 (US); Binance geo-blocks many US datacenter
    # IPs, while api.binance.us is the US-legal endpoint. Setting BINANCE_US_FIRST=1
    # tries binance-us before the vision mirror so the chain usually succeeds on
    # the first hop. Default keeps the original vision -> us -> ... order.
    return os.getenv("BINANCE_US_FIRST", "").lower() in {"1", "true", "yes", "on"}

VISION_ENDPOINT = "https://data-api.binance.vision/api/v3/ticker/24hr"
BINANCE_US_ENDPOINT = "https://api.binance.us/api/v3/ticker/24hr"
BINANCE_SPOT_ENDPOINTS = [
    "https://api.binance.com/api/v3/ticker/24hr",
    "https://api1.binance.com/api/v3/ticker/24hr",
    "https://api2.binance.com/api/v3/ticker/24hr",
    "https://api3.binance.com/api/v3/ticker/24hr",
    "https://api4.binance.com/api/v3/ticker/24hr",
]
# kept for backwards compatibility; order is the fallback order
SPOT_ENDPOINTS = [VISION_ENDPOINT, BINANCE_US_ENDPOINT, *BINANCE_SPOT_ENDPOINTS]


def _universe_chain():
    chain = [
        ("binance-vision", [VISION_ENDPOINT], _normalize_exchange_rows),
        ("binance-us", [BINANCE_US_ENDPOINT], _normalize_exchange_rows),
        ("binance-spot", BINANCE_SPOT_ENDPOINTS, _normalize_exchange_rows),
        ("mexc-spot", MEXC_ENDPOINTS, _normalize_exchange_rows),
        ("binance-futures", FUTURES_ENDPOINTS, _normalize_exchange_rows),
    ]
    if _binance_us_first():
        chain = [chain[1], chain[0], *chain[2:]]
    return chain
FUTURES_ENDPOINTS = ["https://fapi.binance.com/fapi/v1/ticker/24hr"]
MEXC_ENDPOINTS = ["https://api.mexc.com/api/v3/ticker/24hr"]
COINGECKO_ENDPOINT = (
    "https://api.coingecko.com/api/v3/coins/markets"
    "?vs_currency=usd&order=volume_desc&per_page=100&page=1"
    "&sparkline=false&price_change_percentage=24h"
)

KLINE_HOSTS = [
    ("binance-vision", "https://data-api.binance.vision/api/v3/klines", {}),
    ("binance-us", "https://api.binance.us/api/v3/klines", {}),
    ("binance-spot", "https://api.binance.com/api/v3/klines", {}),
    ("mexc-spot", "https://api.mexc.com/api/v3/klines", {}),
    ("binance-futures", "https://fapi.binance.com/fapi/v1/klines", {}),
]


def _kline_hosts():
    if _binance_us_first():
        return [KLINE_HOSTS[1], KLINE_HOSTS[0], *KLINE_HOSTS[2:]]
    return KLINE_HOSTS

_UA = {"accept": "application/json", "user-agent": "meridian-crypto-screen/1.0"}

STABLE_OR_FIAT = {"USDT", "USDC", "DAI", "BUSD", "TUSD", "FDUSD", "USDE", "USD1", "EUR", "GBP"}


def _to_float(v: Any, fallback: float = 0.0) -> float:
    try:
        n = float(v)
        if math.isfinite(n):
            return n
    except (TypeError, ValueError):
        pass
    return fallback


def _normalize_exchange_rows(raw: list[dict]) -> list[dict]:
    out = []
    for item in raw:
        sym = str(item.get("symbol") or "").upper()
        if not sym.endswith("USDT"):
            continue
        base = sym[:-4]
        if base in STABLE_OR_FIAT:
            continue
        last = _to_float(item.get("lastPrice") or item.get("price"))
        high = _to_float(item.get("highPrice") or item.get("high"), last)
        low = _to_float(item.get("lowPrice") or item.get("low"), last)
        qv = _to_float(item.get("quoteVolume") or item.get("amount") or item.get("volumeQuote"))
        if last <= 0 or qv <= 0:
            continue
        rate = _to_float(item.get("priceChangeRate"))
        change = _to_float(item.get("priceChangePercent"), rate * 100 if abs(rate) <= 1 else rate)
        vol24 = (high - low) / last if last > 0 else 0.0
        out.append(
            {
                "symbol": sym,
                "lastPrice": last,
                "priceChangePercent": change,
                "volume": _to_float(item.get("volume")),
                "quoteVolume": qv,
                "highPrice": high,
                "lowPrice": low,
                "volatility24h": vol24,
            }
        )
    out.sort(key=lambda x: x["quoteVolume"], reverse=True)
    return out


def _normalize_coingecko_rows(raw: list[dict]) -> list[dict]:
    out = []
    for item in raw:
        sym = str(item.get("symbol") or "").upper()
        if not sym or sym in STABLE_OR_FIAT:
            continue
        last = _to_float(item.get("current_price"))
        qv = _to_float(item.get("total_volume"))
        if last <= 0 or qv <= 0:
            continue
        high = _to_float(item.get("high_24h"), last)
        low = _to_float(item.get("low_24h"), last)
        vol24 = (high - low) / last if last > 0 else 0.0
        out.append(
            {
                "symbol": f"{sym}USDT",
                "lastPrice": last,
                "priceChangePercent": _to_float(item.get("price_change_percentage_24h")),
                "volume": 0,
                "quoteVolume": qv,
                "highPrice": high,
                "lowPrice": low,
                "volatility24h": vol24,
            }
        )
    out.sort(key=lambda x: x["quoteVolume"], reverse=True)
    return out


async def _fetch_json(client: httpx.AsyncClient, url: str, timeout: float = 6.0) -> Any:
    r = await client.get(url, timeout=timeout, headers=_UA)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code} for {url}")
    return r.json()


DEFAULT_MIN_QUOTE_VOLUME = float(os.getenv("SCREEN_MIN_QUOTE_VOLUME", "5000000"))


async def fetch_universe(
    client: httpx.AsyncClient,
    limit: int = 40,
    min_quote_volume: float = 0.0,
) -> dict[str, Any]:
    """Fetch the 24h ticker universe with the documented fallback chain.

    `min_quote_volume` drops thin books (USDT already enforced, stables/fiat
    excluded) BEFORE the top-`limit` slice — so "limit" bounds how many
    qualifying rows are returned, never hides which coins qualified.
    The response reports `universeTotal` (qualifying count before slicing)
    and the floor applied, so callers can show real coverage.
    """
    debug: list[str] = []
    for source, urls, normalizer in _universe_chain():
        for url in urls:
            try:
                raw = await _fetch_json(client, url)
                rows = normalizer(raw if isinstance(raw, list) else [])
                if min_quote_volume > 0:
                    rows = [r for r in rows if r["quoteVolume"] >= min_quote_volume]
                if rows:
                    return {
                        "source": source,
                        "live": True,
                        "data": rows[:limit],
                        "universeTotal": len(rows),
                        "minQuoteVolume": min_quote_volume,
                        "debug": debug,
                    }
                debug.append(f"{source}: empty after normalize")
            except Exception as exc:  # noqa: BLE001 - fallback chain must not raise
                debug.append(f"{source}: {exc}")
    try:
        raw = await _fetch_json(client, COINGECKO_ENDPOINT)
        rows = _normalize_coingecko_rows(raw if isinstance(raw, list) else [])
        if min_quote_volume > 0:
            rows = [r for r in rows if r["quoteVolume"] >= min_quote_volume]
        if rows:
            return {
                "source": "coingecko",
                "live": True,
                "data": rows[:limit],
                "universeTotal": len(rows),
                "minQuoteVolume": min_quote_volume,
                "debug": debug,
            }
        debug.append("coingecko: empty after normalize")
    except Exception as exc:  # noqa: BLE001
        debug.append(f"coingecko: {exc}")

    from backend import sample_data

    rows = sample_data.seed_universe(limit)
    if min_quote_volume > 0:
        rows = [r for r in rows if r["quoteVolume"] >= min_quote_volume]
    return {
        "source": "seed-fallback",
        "live": False,
        "data": rows,
        "universeTotal": len(rows),
        "minQuoteVolume": min_quote_volume,
        "debug": debug,
        "warning": "Live market APIs unreachable from this environment — showing deterministic demo data.",
    }


def _parse_klines(raw: Any) -> list[dict]:
    out = []
    if not isinstance(raw, list):
        return out
    for k in raw:
        try:
            out.append(
                {
                    "date": int(k[0]),
                    "open": _to_float(k[1]),
                    "high": _to_float(k[2]),
                    "low": _to_float(k[3]),
                    "close": _to_float(k[4]),
                    "volume": _to_float(k[5]),
                    "quoteVolume": _to_float(k[7]),
                    "trades": _to_float(k[8]),
                    "takerBuyBase": _to_float(k[9]),
                    "takerBuyQuote": _to_float(k[10]),
                }
            )
        except (TypeError, IndexError, ValueError):
            continue
    return out


async def fetch_klines(client: httpx.AsyncClient, symbol: str, limit: int = 120) -> dict[str, Any]:
    """Fetch daily candles with fallback hosts. Falls back to synthetic seed data."""
    debug: list[str] = []
    for source, base, _params in _kline_hosts():
        try:
            url = f"{base}?symbol={symbol}&interval=1d&limit={limit}"
            raw = await _fetch_json(client, url)
            candles = _parse_klines(raw)
            if len(candles) >= 61:
                return {"source": source, "live": True, "candles": candles, "debug": debug}
            debug.append(f"{source}: only {len(candles)} candles")
        except Exception as exc:  # noqa: BLE001
            debug.append(f"{source}: {exc}")

    from backend import sample_data

    return {
        "source": "seed-fallback",
        "live": False,
        "candles": sample_data.seed_klines(symbol, limit),
        "debug": debug,
        "warning": "Live kline APIs unreachable — showing deterministic demo candles.",
    }


# ---------------------------------------------------------------------------
# Tiny in-memory TTL cache (per-process; fine for serverless warm instances)
# ---------------------------------------------------------------------------

class TTLCache:
    def __init__(self, ttl_seconds: float = 60.0):
        self.ttl = ttl_seconds
        self._store: dict[str, tuple[float, Any]] = {}

    def get(self, key: str) -> Optional[Any]:
        item = self._store.get(key)
        if not item:
            return None
        ts, value = item
        if time.monotonic() - ts > self.ttl:
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any) -> None:
        self._store[key] = (time.monotonic(), value)


screen_cache = TTLCache(ttl_seconds=float(os.getenv("SCREEN_CACHE_SECONDS", "60")))

# Concurrency bound for fanning out kline requests across the universe
KLINE_CONCURRENCY = int(os.getenv("KLINE_CONCURRENCY", "12"))


async def gather_klines(client: httpx.AsyncClient, symbols: list[str], limit: int = 120):
    sem = asyncio.Semaphore(KLINE_CONCURRENCY)

    async def one(sym: str):
        async with sem:
            return sym, await fetch_klines(client, sym, limit)

    return dict(await asyncio.gather(*(one(s) for s in symbols)))


def seed_label(source: str) -> str:
    return "Live" if source != "seed-fallback" else "Demo seed"


def deterministic_seed(text: str) -> int:
    return int(hashlib.sha256(text.encode()).hexdigest()[:16], 16)

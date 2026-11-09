"""
Meridian — CryptoRank market/fundamentals access layer (optional, additive).

CryptoRank is used as a *quality/context layer* on top of the exchange-derived
screening model: rank, market cap, circulating supply, corroborating 24h change,
logos. The screening formula itself is unchanged — everything here is additive
information (`row["cryptorank"]`) that the model/UI may consume.

Auth
----
Every request authenticates with the `X-Api-Key` header read from the
`CRYPTORANK_API_KEY` environment variable (generate one at
https://cryptorank.io/public-api/dashboard). The key must never be committed
or exposed client-side; store it in `.env` locally or as an encrypted env var
on Vercel.

Endpoints used (all available from the Sandbox plan, 1 credit each)
--------------------------------------------------------------------
  GET /v3/currencies/list    paginated market table (rank-sortable)
  GET /v3/currencies/{id}    single coin profile
  GET /v3/currencies/map     lightweight id reference

Historical OHLCV (`/v3/currencies/{id}/ohlcv`) is an Advanced-plan endpoint and
is intentionally *not* used; OHLCV/taker-buy/participation stay on the exchange
chain in `market_data.py`.

All calls are cached in-process for `CR_CACHE_SECONDS` (default 300s) because
credits are quota-limited. Every public function degrades gracefully: a missing
key or any failure returns `{"ok": False, "reason": ...}` and callers attach
`None` instead of raising — the screening pipeline never breaks because of this.
"""

from __future__ import annotations

import asyncio
import math
import os
import time
from typing import Any, Optional

import httpx

CR_BASE = "https://api.cryptorank.io/v3"
CR_UA = {"accept": "application/json", "user-agent": "meridian-crypto-screen/1.0"}
CR_TIMEOUT = 8.0
CR_CACHE_SECONDS = float(os.getenv("CR_CACHE_SECONDS", "300"))

CR_LIST_SORT = "volume24h"
CR_LIST_LIMIT = 100  # top of CR market table by volume; matches our universe selection

# Unlock-gate thresholds (percent of coin market cap within 30 days).
UNLOCK_WATCH_PCT = float(os.getenv("CR_UNLOCK_WATCH_PCT", "1.0"))
UNLOCK_AVOID_PCT = float(os.getenv("CR_UNLOCK_AVOID_PCT", "3.0"))


def _to_float(v: Any, fallback: Optional[float] = None) -> Optional[float]:
    try:
        n = float(v)
        if math.isfinite(n):
            return n
    except (TypeError, ValueError):
        pass
    return fallback


def cr_configured() -> bool:
    return bool(os.getenv("CRYPTORANK_API_KEY", "").strip())


def _api_key() -> str:
    return os.getenv("CRYPTORANK_API_KEY", "").strip()


def _headers() -> dict[str, str]:
    return {**CR_UA, "X-Api-Key": _api_key()}


# ---------------------------------------------------------------------------
# Tiny in-memory TTL cache (per-process; fine for serverless warm instances)
# ---------------------------------------------------------------------------

_cr_cache: dict[str, tuple[float, Any]] = {}
_credits_used = 0


def _cache_get(key: str) -> Optional[Any]:
    item = _cr_cache.get(key)
    if not item:
        return None
    ts, value = item
    if time.monotonic() - ts > CR_CACHE_SECONDS:
        _cr_cache.pop(key, None)
        return None
    return value


def _cache_set(key: str, value: Any) -> None:
    _cr_cache[key] = (time.monotonic(), value)


def stats() -> dict[str, Any]:
    return {
        "configured": cr_configured(),
        "creditsUsed": _credits_used,
        "cacheSize": len(_cr_cache),
        "cacheTtlSeconds": CR_CACHE_SECONDS,
    }


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

async def _get(client: httpx.AsyncClient, path: str, params: Optional[dict] = None) -> dict[str, Any]:
    global _credits_used
    if not cr_configured():
        return {"ok": False, "reason": "CRYPTORANK_API_KEY not set"}
    try:
        r = await client.get(
            f"{CR_BASE}{path}",
            params=params or {},
            headers=_headers(),
            timeout=CR_TIMEOUT,
        )
    except Exception as exc:  # noqa: BLE001 - caller must not crash on CR failure
        return {"ok": False, "reason": f"network error: {exc}"}

    try:
        body = r.json()
    except Exception:  # noqa: BLE001
        body = None

    used = (body or {}).get("status", {}).get("usedCredits") if isinstance(body, dict) else None
    if isinstance(used, (int, float)):
        _credits_used += int(used)

    if r.status_code != 200:
        err = (body or {}).get("error") if isinstance(body, dict) else None
        code = (err or {}).get("code") if isinstance(err, dict) else None
        msg = (err or {}).get("message") if isinstance(err, dict) else r.text[:200]
        return {"ok": False, "status": r.status_code, "code": code, "reason": msg or f"HTTP {r.status_code}"}

    return {"ok": True, "body": body or {}}


# ---------------------------------------------------------------------------
# Normalisers
# ---------------------------------------------------------------------------

def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool) or v is None:
        return None
    return _to_float(v)


def _normalize_list_item(item: dict) -> dict[str, Any]:
    return {
        "id": item.get("id"),
        "slug": item.get("slug"),
        "symbol": str(item.get("symbol") or "").upper(),
        "name": item.get("name"),
        "rank": item.get("rank"),
        "price": _num(item.get("price")),
        "priceChangePercent24h": _num(item.get("priceChangePercent24h")),
        "marketCap": _num(item.get("marketCap")),
        "volume24h": _num(item.get("volume24h")),
        "circulatingSupply": _num(item.get("circulatingSupply")),
        "imageUrl": item.get("imageUrl"),
    }


# ---------------------------------------------------------------------------
# Public API used by the server
# ---------------------------------------------------------------------------

async def list_coins(client: httpx.AsyncClient, limit: int = CR_LIST_LIMIT) -> dict[str, Any]:
    """Top of the CryptoRank market table (by volume), normalized. 1 credit."""
    cache_key = f"cr:list:{limit}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return {**cached, "cached": True}

    out = await _get(
        client,
        "/currencies/list",
        params={
            "page": 1,
            "sortBy": CR_LIST_SORT,
            "sortOrder": "desc",
        },
    )
    if not out.get("ok"):
        return out

    body = out["body"]
    raw_items = body.get("data") if isinstance(body, dict) else None
    items = [_normalize_list_item(i) for i in (raw_items or []) if isinstance(i, dict)]
    items = [i for i in items if i["symbol"]]
    result = {
        "ok": True,
        "source": "cryptorank",
        "data": items[:limit],
        "meta": body.get("meta"),
    }
    _cache_set(cache_key, result)
    return result


async def coin_profile(client: httpx.AsyncClient, coin_id: int) -> dict[str, Any]:
    """Single coin profile (identity + price snapshot). 1 credit."""
    cache_key = f"cr:coin:{coin_id}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return {**cached, "cached": True}

    out = await _get(client, f"/currencies/{coin_id}")
    if not out.get("ok"):
        return out

    data = (out["body"] or {}).get("data") or {}
    if not isinstance(data, dict):
        return {"ok": False, "reason": "unexpected profile shape"}
    result = {
        "ok": True,
        "source": "cryptorank",
        "data": {
            "id": data.get("id"),
            "slug": data.get("slug"),
            "symbol": str(data.get("symbol") or "").upper(),
            "name": data.get("name"),
            "type": data.get("type"),
            "rank": data.get("rank"),
            "price": _num(data.get("price")),
            "marketCap": _num(data.get("marketCap")),
            "volume24h": _num(data.get("volume24h")),
            "circulatingSupply": _num(data.get("circulatingSupply")),
            "totalSupply": _num(data.get("totalSupply")),
            "imageUrl": data.get("imageUrl"),
        },
    }
    _cache_set(cache_key, result)
    return result


async def currency_map(client: httpx.AsyncClient) -> dict[str, Any]:
    """Lightweight id reference. 1 credit. Cached hard (id mapping is stable)."""
    cache_key = "cr:map"
    cached = _cache_get(cache_key)
    if cached is not None:
        return {**cached, "cached": True}

    out = await _get(client, "/currencies/map")
    if not out.get("ok"):
        return out
    data = (out["body"] or {}).get("data") or []
    result = {"ok": True, "data": data if isinstance(data, list) else []}
    _cache_set(cache_key, result)
    return result


async def symbol_map(client: httpx.AsyncClient) -> dict[str, dict[str, Any]]:
    """Build {BARE_SYMBOL -> normalized list item}. Empty dict on any failure."""
    res = await list_coins(client)
    if not res.get("ok"):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for item in res["data"]:
        sym = item["symbol"]
        if not sym or sym in out:
            continue  # first (highest-volume) wins on duplicate tickers
        out[sym] = item
    return out


# ---------------------------------------------------------------------------
# Plan diagnostics & unlock overhang layer (Pro-gated, self-configuring)
# ---------------------------------------------------------------------------

async def plan_status(client: httpx.AsyncClient) -> dict[str, Any]:
    """Free/Sandbox: current plan, RPM, credit usage, allowed endpoints. Cached 10 min."""
    cache_key = "cr:status"
    cached = _cache_get(cache_key)
    if cached is not None:
        return {**cached, "cached": True}

    out = await _get(client, "/status")
    if not out.get("ok"):
        return out
    body = out["body"]
    result = {
        "ok": True,
        "plan": body.get("plan"),
        "rateLimitPerMinute": body.get("rateLimitPerMinute"),
        "credits": body.get("credits") or {},
        "currentPeriodStart": body.get("currentPeriodStart"),
        "currentPeriodEnd": body.get("currentPeriodEnd"),
        "endpoints": body.get("endpoints") or [],
    }
    _cache_set(cache_key, result)
    return result


async def plan_allows(client: httpx.AsyncClient, path_substring: str) -> Optional[bool]:
    """True/False if /v3/status lists the path; None when diagnostics are unavailable."""
    ps = await plan_status(client)
    if not ps.get("ok"):
        return None
    eps = ps.get("endpoints") or []
    if not isinstance(eps, list) or not eps:
        return None
    needle = path_substring.lower()
    return any(needle in str(e).lower() for e in eps)


def _num_or_none(v: Any) -> Optional[float]:
    return _to_float(v)


def _normalize_unlock_item(item: dict) -> Optional[dict[str, Any]]:
    currency = item.get("currency") if isinstance(item.get("currency"), dict) else {}
    coin_id = (
        item.get("currencyId")
        or item.get("coinId")
        or currency.get("id")
        or item.get("id")
    )
    date = item.get("date") or item.get("unlockDate") or item.get("time")
    usd = _num_or_none(item.get("unlockValue") or item.get("valueUsd") or item.get("amountUsd"))
    pct = _num_or_none(item.get("percentOfMcap") or item.get("percent") or item.get("percentOfSupply"))
    tokens = _num_or_none(item.get("tokens") or item.get("tokenAmount") or item.get("amount"))
    symbol = str(currency.get("symbol") or item.get("symbol") or "").upper()
    if coin_id is None and not symbol:
        return None
    return {
        "coinId": coin_id,
        "symbol": symbol,
        "date": date,
        "unlockValueUsd": usd,
        "percentOfMcap": pct,
        "tokens": tokens,
    }


async def upcoming_unlocks(
    client: httpx.AsyncClient,
    days_ahead: int = 30,
    min_percent_mcap: float = 0.0,
    min_value: Optional[float] = None,
    max_pages: int = 4,
) -> dict[str, Any]:
    """BulK market-wide upcoming unlocks (Pro plan). Cached 6h. ~1 credit/page."""
    cache_key = f"cr:unlocks:{days_ahead}:{min_percent_mcap}:{min_value}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return {**cached, "cached": True}

    items: list[dict[str, Any]] = []
    debug: list[str] = []
    pages_fetched = 0
    for page in range(1, max_pages + 1):
        params: dict[str, Any] = {
            "page": page,
            "daysAhead": days_ahead,
            "sortBy": "percentOfMcap",
            "sortOrder": "desc",
        }
        if min_percent_mcap:
            params["minPercentOfMcap"] = min_percent_mcap
        if min_value:
            params["minValue"] = min_value
        out = await _get(client, "/currencies/upcoming-token-unlocks", params=params)
        if not out.get("ok"):
            if page == 1:
                return out
            debug.append(f"page {page}: {out.get('reason')}")
            break
        body = out["body"]
        pages_fetched += 1
        rows = body.get("data") if isinstance(body, dict) else []
        for raw in rows or []:
            if isinstance(raw, dict):
                item = _normalize_unlock_item(raw)
                if item:
                    items.append(item)
        meta = body.get("meta") if isinstance(body, dict) else {}
        if not (meta or {}).get("hasNextPage"):
            break

    result = {"ok": True, "source": "cryptorank", "data": items, "pages": pages_fetched, "debug": debug}
    _cache_set(cache_key, result)
    return result


async def unlocks_by_symbol(client: httpx.AsyncClient) -> dict[str, dict[str, Any]]:
    """{BARE_SYMBOL -> normalized unlock} for our universe. Empty dict when plan denies."""
    allowed = await plan_allows(client, "upcoming-token-unlocks")
    if allowed is False:
        return {}
    res = await upcoming_unlocks(client, days_ahead=30, min_percent_mcap=0.5)
    if not res.get("ok"):
        return {}

    # Map coin id -> symbol via the cached market list (Symbol is authoritative there).
    res2 = await list_coins(client)
    id2symbol = {i["id"]: i["symbol"] for i in res2.get("data", []) if i.get("id") is not None}

    out: dict[str, dict[str, Any]] = {}
    for item in res["data"]:
        sym = item.get("symbol") or id2symbol.get(item.get("coinId")) or ""
        sym = bare_symbol(sym)
        if not sym:
            continue
        # keep the largest unlock event per symbol within the window
        prev = out.get(sym)
        if prev is None or (item.get("percentOfMcap") or 0) > (prev.get("percentOfMcap") or 0):
            out[sym] = {**item, "symbol": sym}
    return out


def bare_symbol(symbol: Optional[str]) -> str:
    """'BTCUSDT' -> 'BTC' (and upper-cased)."""
    s = (symbol or "").upper().strip()
    if s.endswith("USDT"):
        return s[:-4]
    return s


def unlock_block(symbol: Optional[str], unlock_map: dict[str, dict[str, Any]]) -> Optional[dict[str, Any]]:
    return unlock_map.get(bare_symbol(symbol))
    """'BTCUSDT' -> 'BTC' (and upper-cased)."""
    s = (symbol or "").upper().strip()
    if s.endswith("USDT"):
        return s[:-4]
    return s


def block_for(symbol: Optional[str], cr_map: dict[str, dict[str, Any]]) -> Optional[dict[str, Any]]:
    """Slim, UI/model-ready block for a screen row; None when unavailable."""
    item = cr_map.get(bare_symbol(symbol))
    if not item:
        return None
    return {
        "rank": item.get("rank"),
        "id": item.get("id"),
        "slug": item.get("slug"),
        "name": item.get("name"),
        "marketCap": item.get("marketCap"),
        "circulatingSupply": item.get("circulatingSupply"),
        "volume24h": item.get("volume24h"),
        "priceChangePercent24h": item.get("priceChangePercent24h"),
        "imageUrl": item.get("imageUrl"),
    }

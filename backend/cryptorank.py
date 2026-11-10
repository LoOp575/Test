"""CryptoRank additive context layer.

Auth: CRYPTORANK_API_KEY (sent as X-Api-Key).

This is an emergency stub after a bad push truncated the module. Screening keeps
working (cryptorank blocks stay null). Restore the full module from commit
022ee625 and re-apply any local edits.
"""

from __future__ import annotations

import os
from typing import Any, Optional

import httpx

CR_BASE = "https://api.cryptorank.io/v3"
CR_CACHE_SECONDS = float(os.getenv("CR_CACHE_SECONDS", "300"))
UNLOCK_WATCH_PCT = float(os.getenv("CR_UNLOCK_WATCH_PCT", "1.0"))
UNLOCK_AVOID_PCT = float(os.getenv("CR_UNLOCK_AVOID_PCT", "3.0"))


def cr_configured() -> bool:
    return bool(os.getenv("CRYPTORANK_API_KEY", "").strip())


def stats() -> dict[str, Any]:
    return {
        "configured": cr_configured(),
        "creditsUsed": 0,
        "cacheSize": 0,
        "cacheTtlSeconds": CR_CACHE_SECONDS,
        "stub": True,
    }


async def list_coins(client: httpx.AsyncClient, limit: int = 100) -> dict[str, Any]:
    if not cr_configured():
        return {"ok": False, "reason": "CRYPTORANK_API_KEY not set"}
    return {"ok": False, "reason": "cryptorank module is a temporary stub — restore full backend/cryptorank.py from commit 022ee625"}


async def coin_profile(client: httpx.AsyncClient, coin_id: int) -> dict[str, Any]:
    return await list_coins(client)


async def currency_map(client: httpx.AsyncClient) -> dict[str, Any]:
    return await list_coins(client)


async def symbol_map(client: httpx.AsyncClient) -> dict[str, dict[str, Any]]:
    return {}


async def plan_status(client: httpx.AsyncClient) -> dict[str, Any]:
    if not cr_configured():
        return {"ok": False, "reason": "CRYPTORANK_API_KEY not set"}
    return {"ok": False, "reason": "cryptorank module is a temporary stub"}


async def plan_allows(client: httpx.AsyncClient, path_substring: str) -> Optional[bool]:
    return None


async def upcoming_unlocks(client: httpx.AsyncClient, days_ahead: int = 30, min_percent_mcap: float = 0.0) -> dict[str, Any]:
    return {"ok": False, "reason": "stub"}


async def unlocks_by_symbol(client: httpx.AsyncClient) -> dict[str, dict[str, Any]]:
    return {}


def bare_symbol(symbol: Optional[str]) -> str:
    s = (symbol or "").upper().strip()
    return s[:-4] if s.endswith("USDT") else s


def unlock_block(symbol: Optional[str], unlock_map: dict[str, dict[str, Any]]) -> Optional[dict[str, Any]]:
    return unlock_map.get(bare_symbol(symbol))


def block_for(symbol: Optional[str], cr_map: dict[str, dict[str, Any]]) -> Optional[dict[str, Any]]:
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

"""
Meridian — CryptoRank market/fundamentals access layer (optional, additive).

CryptoRank is used as a *quality/context layer* on top of the exchange-derived
screening model: rank, market cap, circulating supply, corroborating 24h change,
logos. The screening formula itself is unchanged — everything here is additive
information (`row["cryptorank"]`) that the model/UI may consume.

Auth
----
Every request authenticates with the `X-Api-Key` header read from the
`CRYPTORANK_API_KEY` environment variable. Generate/copy the key from your
CryptoRank dashboard (path: https://cryptorank.io/public-api/dashboard). The
key must never be committed or exposed client-side; store it in `.env` locally
or as an encrypted env var on Vercel.

API base (NOT the dashboard link above): `CR_BASE = "https://api.cryptorank.io/v3"`.

Endpoints used
--------------
  Sandbox plan, 1 credit each:
  GET /v3/currencies/list    paginated market table (rank-sortable)
  GET /v3/currencies/{id}    single coin profile
  GET /v3/currencies/map     lightweight id reference
  Free diagnostics (no credit):
  GET /v3/status             plan tier, RPM, credits, allowed endpoints (cached 600s)
  Pro plan (self-gated via /v3/status; no-op silently if not allowed):
  GET /v3/currencies/upcoming-token-unlocks   market-wide 30d unlock feed
                                              (1 credit/page, cached 6h)

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


# In-process TTL cache: {key: (expires_at, value)}
_cache: dict[str, tuple[float, Any]] = {}
_credits_used = 0


def _cache_get(key: str) -> Optional[Any]:
    entry = _cache.get(key)
    if not entry:
        return None
    exp, val = entry
    if time.time() > exp:
        _cache.pop(key, None)
        return None
    return val


def _cache_set(key: str, value: Any) -> None:
    _cache[key] = (time.time() + CR_CACHE_SECONDS, value)


def stats() -> dict[str, Any]:
    return {
        "configured": cr_configured(),
        "creditsUsed": _credits_used,
        "cacheSize": len(_cache),
        "cacheTtlSeconds": CR_CACHE_SECONDS,
    }

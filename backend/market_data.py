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
import hashlib
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


class SymbolNotListed(Exception):
    """Host menjawab secara eksplisit bahwa simbol ini tidak terdaftar.

    Binance: HTTP 400 dengan ``code: -1121`` / ``msg: "Invalid symbol."``.
    MEXC: HTTP 400 dengan ``code: 700002`` / ``msg: "invalid symbol"``.
    Ini BUKAN kegagalan jaringan — jawabannya otoritatif, jadi bisa dipakai
    untuk membedakan "koin tidak ada" dari "server tidak bisa menghubungi bursa".
    """


# Kode error yang berarti "simbol tidak ada", bukan "coba lagi nanti".
_NOT_LISTED_CODES = {-1121, 700002}


def _looks_not_listed(status: int, body: Any) -> bool:
    if status not in (400, 404):
        return False
    if not isinstance(body, dict):
        return False
    msg = str(body.get("msg") or body.get("message") or "").lower()
    return body.get("code") in _NOT_LISTED_CODES or "invalid symbol" in msg or "symbol not" in msg


async def _fetch_json(client: httpx.AsyncClient, url: str, timeout: float = 6.0) -> Any:
    r = await client.get(url, timeout=timeout, headers=_UA)
    if r.status_code != 200:
        body: Any = None
        if r.status_code in (400, 404):
            try:
                body = r.json()
            except Exception:  # noqa: BLE001 - body tidak wajib JSON
                body = None
        if _looks_not_listed(r.status_code, body):
            raise SymbolNotListed(f"HTTP {r.status_code} {body}")
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


# Minimum daily history for the adapted model (must match screening.MIN_HISTORY).
# Symbols younger than this cannot be scored; they must NOT be replaced by
# synthetic candles, because that would show invented data for a real coin.
MIN_KLINE_BARS = 61


async def fetch_klines(client: httpx.AsyncClient, symbol: str, limit: int = 120) -> dict[str, Any]:
    """Fetch daily candles with fallback hosts.

    Three distinct outcomes (never silently mixed):
    - ``live``            : a real host answered with enough history.
    - ``live`` + short    : a real host answered but the symbol is too young;
                            candles are the REAL ones and ``warning`` says so.
    - ``seed-fallback``   : every host failed (network/geo-block), so synthetic
                            candles are used and clearly labelled.
    """
    debug: list[str] = []
    best_live: Optional[tuple[str, list[dict]]] = None

    for source, base, _params in _kline_hosts():
        try:
            url = f"{base}?symbol={symbol}&interval=1d&limit={limit}"
            raw = await _fetch_json(client, url)
            candles = _parse_klines(raw)
            if len(candles) >= MIN_KLINE_BARS:
                return {"source": source, "live": True, "candles": candles, "debug": debug}
            if candles:
                # Real data exists, just not enough for the model. Keep the longest
                # real series instead of replacing it with synthetic candles.
                debug.append(f"{source}: {len(candles)} candles (riwayat pendek, data asli)")
                if best_live is None or len(candles) > len(best_live[1]):
                    best_live = (source, candles)
            else:
                debug.append(f"{source}: symbol not listed (0 candles)")
        except SymbolNotListed:
            debug.append(f"{source}: symbol not listed (HTTP 400 invalid symbol)")
        except Exception as exc:  # noqa: BLE001
            debug.append(f"{source}: {exc}")

    if best_live is not None:
        source, candles = best_live
        return {
            "source": source,
            "live": True,
            "candles": candles,
            "debug": debug,
            "shortHistory": True,
            "bars": len(candles),
            "warning": (
                f"Riwayat {symbol} baru {len(candles)} candle harian (butuh "
                f"{MIN_KLINE_BARS}). Data ini ASLI dari {source}, bukan simulasi — "
                f"tapi belum cukup untuk dinilai model."
            ),
        }

    from backend import sample_data

    # Hanya sampai sini bila SEMUA host gagal (atau tidak mengenal simbolnya).
    debug.append(f"{symbol}: no host returned real candles -> seed")
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

# Symbols where every host answered "I do not list this" (empty body) rather
# than failing. Used to tell "coin does not exist" apart from "cannot reach".
_SYMBOL_NOT_LISTED = "SYMBOL_NOT_LISTED"


def _is_not_listed(debug: list[str]) -> bool:
    """True bila SEMUA host menjawab tanpa candle (simbol memang tidak ada).

    Kegagalan jaringan/geo-block tidak dihitung: itu bukan bukti simbol tidak ada.
    """
    if not debug:
        return False
    return all("symbol not listed" in d for d in debug)


async def fetch_symbol_klines(client: httpx.AsyncClient, symbol: str, limit: int = 120) -> dict[str, Any]:
    """Klines for ONE symbol on the Binance chain, so the answer is unambiguous.

    `fetch_klines` also tries MEXC/futures/CoinGecko and then a synthetic seed,
    which would let a misspelled ticker look like a real coin. Here:
    - real history (any length) -> ``live`` true, ``shortHistory`` if < MIN_KLINE_BARS
    - Binance answers "invalid symbol" on every Binance host -> ``exists`` false
      (HTTP 404 upstream). Hanya jawaban otoritatif itu yang dianggap bukti;
      timeout/geo-block tidak pernah disimpulkan sebagai "koin tidak ada".
    - hosts unreachable        -> ``unavailable`` true (HTTP 503 upstream)
    Never returns synthetic candles.
    """
    debug: list[str] = []
    best: Optional[tuple[str, list[dict]]] = None
    for source, base, _params in [h for h in _kline_hosts() if h[0].startswith("binance")]:
        try:
            url = f"{base}?symbol={symbol}&interval=1d&limit={limit}"
            raw = await _fetch_json(client, url)
            candles = _parse_klines(raw)
            if len(candles) >= MIN_KLINE_BARS:
                return {"source": source, "live": True, "exists": True, "candles": candles, "debug": debug}
            if candles:
                debug.append(f"{source}: {len(candles)} candles (riwayat pendek, data asli)")
                if best is None or len(candles) > len(best[1]):
                    best = (source, candles)
            else:
                debug.append(f"{source}: symbol not listed (0 candles)")
        except SymbolNotListed:
            debug.append(f"{source}: symbol not listed (HTTP 400 invalid symbol)")
        except Exception as exc:  # noqa: BLE001
            debug.append(f"{source}: {exc}")

    if best is not None:
        source, candles = best
        return {
            "source": source,
            "live": True,
            "exists": True,
            "candles": candles,
            "debug": debug,
            "shortHistory": True,
            "bars": len(candles),
            "warning": (
                f"Riwayat {symbol} baru {len(candles)} candle harian (butuh "
                f"{MIN_KLINE_BARS}). Data ini ASLI dari {source}, bukan simulasi — "
                f"tapi belum cukup untuk dinilai model."
            ),
        }

    if _is_not_listed(debug):
        return {"source": None, "live": False, "exists": False, "candles": [], "debug": debug}

    return {"source": None, "live": False, "exists": None, "unavailable": True, "candles": [], "debug": debug}


async def search_binance_symbols(client: httpx.AsyncClient, query: str, max_quote_volume: float = 0.0) -> dict[str, Any]:
    """Cari pair USDT di seluruh daftar bursa, bukan hanya yang sudah dimuat.

    Memakai endpoint ticker yang sama dengan universe (satu request), lalu
    menyaring secara lokal. Hasil diurutkan: exact match dulu, lalu prefix,
    lalu yang memuat query; di dalam grup diurut volume 24h.
    """
    import re as _re

    raw = await _fetch_json(client, VISION_ENDPOINT)
    rows = _normalize_exchange_rows(raw if isinstance(raw, list) else [])
    q = _re.sub(r"[^A-Z0-9]", "", (query or "").upper())
    if not q:
        return {"query": query, "matches": [], "universeSize": len(rows)}

    def rank(row: dict) -> Optional[tuple[int, float]]:
        base = row["symbol"][:-4]
        full = row["symbol"]
        if base == q or full == q:
            return (0, -row["quoteVolume"])
        if base.startswith(q) or full.startswith(q):
            return (1, -row["quoteVolume"])
        if q in base:
            return (2, -row["quoteVolume"])
        return None

    scored = [(rank(r), r) for r in rows]
    hits = sorted((h for h in scored if h[0] is not None), key=lambda h: h[0])
    matches = [
        {
            "symbol": r["symbol"],
            "base": r["symbol"][:-4],
            "lastPrice": r["lastPrice"],
            "priceChangePercent": r["priceChangePercent"],
            "quoteVolume": r["quoteVolume"],
            "match": ("exact", "prefix", "contains")[h[0]],
        }
        for h, r in hits[:20]
    ]
    return {"query": query, "matches": matches, "universeSize": len(rows), "source": "binance-vision"}


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

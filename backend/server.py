"""
LQ-Short Hunter — FastAPI Backend
Probabilistic Liquidity Short Engine for BTC and crypto market analysis.

Hardened for Vercel:
- fallback market sources
- safe Monte Carlo defaults
- market fuel enrichment from recent candles when available
- no hard crash on external API issues
"""

import math
import os
from contextlib import asynccontextmanager
from typing import Any, Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from backend.monte_carlo import (
    build_simulation_params_from_market,
    run_monte_carlo_simulation,
    normalize_simulation_input,
)
from backend.agent import generate_agent_analysis

load_dotenv()

SPOT_ENDPOINTS = [
    "https://api.binance.com/api/v3/ticker/24hr",
    "https://api1.binance.com/api/v3/ticker/24hr",
    "https://api2.binance.com/api/v3/ticker/24hr",
    "https://api3.binance.com/api/v3/ticker/24hr",
    "https://api4.binance.com/api/v3/ticker/24hr",
]
FUTURES_ENDPOINTS = ["https://fapi.binance.com/fapi/v1/ticker/24hr"]
MEXC_ENDPOINTS = ["https://api.mexc.com/api/v3/ticker/24hr"]
COINGECKO_ENDPOINT = (
    "https://api.coingecko.com/api/v3/coins/markets"
    "?vs_currency=usd&order=volume_desc&per_page=100&page=1"
    "&sparkline=false&price_change_percentage=24h"
)
CRYPTORANK_TRENDING_ENDPOINTS = [
    os.getenv("CRYPTORANK_TRENDING_URL", "").strip(),
    "https://api.cryptorank.io/v2/currencies/trending",
    "https://api.cryptorank.io/v1/currencies/trending",
    "https://api.cryptorank.io/v2/currencies?limit=100",
    "https://api.cryptorank.io/v1/currencies?limit=100",
]

SEED_MARKETS: list[dict[str, Any]] = [
    {"symbol": "BTCUSDT", "lastPrice": 105000, "priceChangePercent": 2.4, "quoteVolume": 5_000_000_000, "highPrice": 108000, "lowPrice": 101000},
    {"symbol": "ETHUSDT", "lastPrice": 3800, "priceChangePercent": 3.8, "quoteVolume": 2_600_000_000, "highPrice": 3920, "lowPrice": 3600},
    {"symbol": "SOLUSDT", "lastPrice": 172, "priceChangePercent": 8.7, "quoteVolume": 1_300_000_000, "highPrice": 181, "lowPrice": 154},
    {"symbol": "BNBUSDT", "lastPrice": 690, "priceChangePercent": 1.9, "quoteVolume": 850_000_000, "highPrice": 705, "lowPrice": 665},
    {"symbol": "XRPUSDT", "lastPrice": 2.25, "priceChangePercent": 6.2, "quoteVolume": 720_000_000, "highPrice": 2.38, "lowPrice": 2.07},
    {"symbol": "DOGEUSDT", "lastPrice": 0.19, "priceChangePercent": 11.5, "quoteVolume": 650_000_000, "highPrice": 0.205, "lowPrice": 0.165},
    {"symbol": "PEPEUSDT", "lastPrice": 0.000012, "priceChangePercent": 18.4, "quoteVolume": 610_000_000, "highPrice": 0.0000135, "lowPrice": 0.0000097},
    {"symbol": "ADAUSDT", "lastPrice": 0.72, "priceChangePercent": 4.8, "quoteVolume": 420_000_000, "highPrice": 0.76, "lowPrice": 0.68},
    {"symbol": "AVAXUSDT", "lastPrice": 38.5, "priceChangePercent": 7.2, "quoteVolume": 390_000_000, "highPrice": 40.8, "lowPrice": 35.4},
    {"symbol": "LINKUSDT", "lastPrice": 18.4, "priceChangePercent": 5.1, "quoteVolume": 310_000_000, "highPrice": 19.1, "lowPrice": 17.2},
    {"symbol": "WIFUSDT", "lastPrice": 2.85, "priceChangePercent": 14.2, "quoteVolume": 280_000_000, "highPrice": 3.05, "lowPrice": 2.49},
    {"symbol": "TONUSDT", "lastPrice": 5.42, "priceChangePercent": 4.5, "quoteVolume": 240_000_000, "highPrice": 5.71, "lowPrice": 5.12},
]


def _to_float(v: Any, fallback: float = 0.0) -> float:
    try:
        n = float(v)
        if math.isfinite(n):
            return n
    except (TypeError, ValueError):
        pass
    return fallback


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _deep_get(obj: Any, path: list[str], fallback: Any = None) -> Any:
    cur = obj
    for key in path:
        if isinstance(cur, dict) and key in cur:
            cur = cur[key]
        else:
            return fallback
    return cur


def _normalize_exchange_rows(raw: list[dict]) -> list[dict]:
    out = []
    for item in raw:
        sym = item.get("symbol") or ""
        if not sym.endswith("USDT"):
            continue
        last = _to_float(item.get("lastPrice") or item.get("price"))
        high = _to_float(item.get("highPrice") or item.get("high"), last)
        low = _to_float(item.get("lowPrice") or item.get("low"), last)
        qv = _to_float(item.get("quoteVolume") or item.get("amount") or item.get("volumeQuote"))
        if last <= 0 or qv < 0:
            continue
        rate = _to_float(item.get("priceChangeRate"))
        change = _to_float(item.get("priceChangePercent"), rate * 100 if abs(rate) <= 1 else rate)
        vol24 = (high - low) / last if last > 0 else 0
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
        sym = (item.get("symbol") or "").upper()
        if not sym or sym in ("USDT", "USDC", "DAI", "BUSD", "TUSD", "FDUSD", "USDE"):
            continue
        last = _to_float(item.get("current_price"))
        if last <= 0:
            continue
        high = _to_float(item.get("high_24h"), last)
        low = _to_float(item.get("low_24h"), last)
        qv = _to_float(item.get("total_volume"))
        vol24 = (high - low) / last if last > 0 else 0
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


def _extract_list(payload: Any) -> list[dict]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    candidates = [
        payload.get("data"), payload.get("result"), payload.get("items"), payload.get("currencies"), payload.get("coins"),
        _deep_get(payload, ["data", "data"]), _deep_get(payload, ["data", "items"]), _deep_get(payload, ["data", "currencies"]),
        _deep_get(payload, ["result", "data"]), _deep_get(payload, ["result", "items"]),
    ]
    for c in candidates:
        if isinstance(c, list):
            return [x for x in c if isinstance(x, dict)]
    return []


def _normalize_cryptorank_rows(raw_payload: Any) -> list[dict]:
    items = _extract_list(raw_payload)
    out = []
    for item in items:
        sym = item.get("symbol") or _deep_get(item, ["currency", "symbol"]) or _deep_get(item, ["coin", "symbol"]) or ""
        sym = str(sym).upper().strip()
        if not sym or sym in ("USDT", "USDC", "DAI", "BUSD", "TUSD", "FDUSD", "USDE"):
            continue
        display_symbol = sym if sym.endswith("USDT") else f"{sym}USDT"
        usd = item.get("values") if isinstance(item.get("values"), dict) else {}
        usd = usd.get("USD", usd) if isinstance(usd, dict) else {}
        last = _to_float(item.get("price") or item.get("priceUSD") or item.get("usdPrice") or usd.get("price") or usd.get("value"))
        if last <= 0:
            continue
        change = _to_float(
            item.get("priceChangePercent") or item.get("priceChange24h") or item.get("percentChange24h")
            or item.get("change24h") or usd.get("percentChange24h") or usd.get("priceChange24h")
        )
        qv = _to_float(item.get("volume24h") or item.get("volume") or item.get("totalVolume") or usd.get("volume24h") or usd.get("volume"))
        market_cap = _to_float(item.get("marketCap") or usd.get("marketCap"))
        if qv <= 0 and market_cap > 0:
            qv = market_cap
        volatility_guess = min(abs(change) / 100.0, 1.5)
        high = last * (1 + volatility_guess / 2)
        low = last * max(0.000001, 1 - volatility_guess / 2)
        trend = _to_float(item.get("trendingScore") or item.get("trendScore") or item.get("rankDelta") or item.get("rank") or item.get("marketCapRank"))
        if trend > 1:
            trend = 1 / max(1, trend)
        out.append({
            "symbol": display_symbol,
            "lastPrice": last,
            "priceChangePercent": change,
            "volume": 0,
            "quoteVolume": qv,
            "marketCap": market_cap,
            "highPrice": high,
            "lowPrice": low,
            "volatility24h": volatility_guess,
            "trendingScore": trend,
        })
    out.sort(key=lambda x: (x.get("trendingScore", 0), x.get("quoteVolume", 0)), reverse=True)
    return out


def _normalize_seed_rows(seed: list[dict]) -> list[dict]:
    return _normalize_exchange_rows([{**s, "volume": s.get("volume", 0)} for s in seed])


def _cryptorank_headers() -> dict[str, str]:
    key = os.getenv("CRYPTORANK_API_KEY") or os.getenv("CRYPTORANKAPIKEY") or ""
    headers = {"accept": "application/json", "user-agent": "LQ-Short-Hunter/2.4"}
    if key:
        headers["X-Api-Key"] = key
        headers["Authorization"] = f"Bearer {key}"
    return headers


async def _try_fetch_array(client: httpx.AsyncClient, urls: list[str]) -> tuple[Optional[list], list[str]]:
    errors: list[str] = []
    for url in urls:
        if not url:
            continue
        try:
            r = await client.get(url, timeout=7.0, headers={"accept": "application/json", "user-agent": "LQ-Short-Hunter/2.4"})
            if r.status_code != 200:
                errors.append(f"{url} -> HTTP {r.status_code}")
                continue
            j = r.json()
            if isinstance(j, list):
                return j, errors
            errors.append(f"{url} -> not array")
        except Exception as e:
            errors.append(f"{url} -> {type(e).__name__}: {e}")
    return None, errors


async def _try_fetch_json(client: httpx.AsyncClient, urls: list[str], headers: Optional[dict] = None) -> tuple[Optional[Any], list[str]]:
    errors: list[str] = []
    for url in urls:
        if not url:
            continue
        try:
            r = await client.get(url, timeout=8.0, headers=headers or {"accept": "application/json"})
            if r.status_code != 200:
                errors.append(f"{url} -> HTTP {r.status_code}")
                continue
            return r.json(), errors
        except Exception as e:
            errors.append(f"{url} -> {type(e).__name__}: {e}")
    return None, errors


def _parse_kline_row(row: Any) -> Optional[dict[str, float]]:
    if not isinstance(row, list) or len(row) < 6:
        return None
    o = _to_float(row[1])
    h = _to_float(row[2])
    l = _to_float(row[3])
    c = _to_float(row[4])
    base_v = _to_float(row[5])
    qv = _to_float(row[7] if len(row) > 7 else 0)
    if qv <= 0 and c > 0:
        qv = base_v * c
    if h <= 0 or l <= 0 or c <= 0:
        return None
    rng = max(h - l, 1e-12)
    return {
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "quoteVolume": qv,
        "upperWickRatio": _clamp((h - max(o, c)) / rng),
        "closeStrength": _clamp((c - l) / rng),
    }


def _build_fuel_metrics_from_klines(rows: list[Any]) -> Optional[dict[str, Any]]:
    candles = [x for x in (_parse_kline_row(r) for r in rows) if x]
    if len(candles) < 6:
        return None
    recent = candles[-3:]
    prev = candles[-6:-3]
    recent_qv = sum(c["quoteVolume"] for c in recent)
    prev_qv = sum(c["quoteVolume"] for c in prev)
    trend_pct = ((recent_qv - prev_qv) / prev_qv * 100) if prev_qv > 0 else 0.0
    avg_close_strength = sum(c["closeStrength"] for c in recent) / len(recent)
    avg_upper_wick = sum(c["upperWickRatio"] for c in recent) / len(recent)
    rejection_score = _clamp(0.65 * avg_upper_wick + 0.35 * (1 - avg_close_strength))

    if trend_pct <= -25:
        volume_status = "declining"
    elif trend_pct >= 25:
        volume_status = "rising"
    else:
        volume_status = "flat"

    if volume_status == "declining" and rejection_score >= 0.45:
        fuel_signal = "volume_down_rejection_short_validating"
    elif volume_status == "declining" and avg_close_strength >= 0.55:
        fuel_signal = "thin_weak_pump_watch"
    elif volume_status == "rising" and avg_close_strength <= 0.45:
        fuel_signal = "distribution_possible"
    elif volume_status == "rising" and avg_close_strength >= 0.55:
        fuel_signal = "fuel_still_strong"
    else:
        fuel_signal = "neutral_watch"

    return {
        "volumeTrendPercent": round(trend_pct, 2),
        "volumeTrendStatus": volume_status,
        "recentQuoteVolume3h": round(recent_qv, 2),
        "previousQuoteVolume3h": round(prev_qv, 2),
        "closeStrength3h": round(avg_close_strength, 4),
        "upperWickRatio3h": round(avg_upper_wick, 4),
        "rejectionScore": round(rejection_score, 4),
        "fuelSignal": fuel_signal,
    }


async def _enrich_market_with_fuel_metrics(client: httpx.AsyncClient, market: dict) -> dict:
    sym = str(market.get("symbol") or "").upper().strip()
    if not sym:
        return market
    urls = [
        f"https://api.mexc.com/api/v3/klines?symbol={sym}&interval=1h&limit=12",
        f"https://api.binance.com/api/v3/klines?symbol={sym}&interval=1h&limit=12",
        f"https://api1.binance.com/api/v3/klines?symbol={sym}&interval=1h&limit=12",
        f"https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval=1h&limit=12",
    ]
    raw, errs = await _try_fetch_array(client, urls)
    if raw is None:
        return {**market, "fuelMetricsSource": "unavailable", "fuelMetricsDebug": errs[:3]}
    metrics = _build_fuel_metrics_from_klines(raw)
    if not metrics:
        return {**market, "fuelMetricsSource": "insufficient_klines"}
    return {**market, **metrics, "fuelMetricsSource": "recent_1h_klines"}


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _app.state.http = httpx.AsyncClient()
    try:
        yield
    finally:
        await _app.state.http.aclose()


app = FastAPI(title="LQ-Short Hunter API", version="2.4.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class SimulateBody(BaseModel):
    currentPrice: float
    takeProfit: Optional[float] = None
    stopLoss: Optional[float] = None
    mu: Optional[float] = 0
    lambda_: Optional[float] = Field(0.5, alias="lambda")
    annualVolatility: Optional[float] = 0.65
    daysForecast: Optional[float] = 7
    simulations: Optional[int] = 15000
    steps: Optional[int] = 32
    spotFlow: Optional[float] = 0
    oiFlow: Optional[float] = 0
    shortLiqAbove: Optional[float] = 0
    longLiqBelow: Optional[float] = 0
    exhaustionScore: Optional[float] = 0
    autoLevels: Optional[dict] = None

    class Config:
        populate_by_name = True


class AgentBody(BaseModel):
    market: dict
    autoLevels: Optional[dict] = None
    results: Optional[dict] = None
    provider: Optional[str] = None
    aiProvider: Optional[str] = None
    mode: Optional[str] = None
    agentMode: Optional[str] = None


@app.get("/api/health")
async def health():
    return {"ok": True, "service": "lq-short-hunter", "version": "2.4.0"}


@app.get("/api/markets")
async def markets():
    client: httpx.AsyncClient = app.state.http
    debug: list[str] = []

    raw, errs = await _try_fetch_array(client, SPOT_ENDPOINTS)
    if raw is not None:
        rows = _normalize_exchange_rows(raw)
        if rows:
            return {"ok": True, "source": "binance-spot", "marketType": "binance-spot", "count": len(rows), "data": rows}
        debug.append("binance-spot empty after normalize")
    debug.extend(errs)

    raw, errs = await _try_fetch_array(client, MEXC_ENDPOINTS)
    if raw is not None:
        rows = _normalize_exchange_rows(raw)
        if rows:
            return {"ok": True, "source": "mexc-spot", "marketType": "mexc-spot", "count": len(rows), "data": rows, "warning": "Using MEXC Spot fallback because Binance Spot failed.", "debug": debug[:8]}
        debug.append("mexc-spot empty after normalize")
    debug.extend(errs)

    raw, errs = await _try_fetch_array(client, FUTURES_ENDPOINTS)
    if raw is not None:
        rows = _normalize_exchange_rows(raw)
        if rows:
            return {"ok": True, "source": "binance-futures", "marketType": "binance-futures", "count": len(rows), "data": rows, "warning": "Using Binance Futures fallback because Binance Spot and MEXC failed.", "debug": debug[:8]}
        debug.append("binance-futures empty after normalize")
    debug.extend(errs)

    raw, errs = await _try_fetch_array(client, [COINGECKO_ENDPOINT])
    if raw is not None:
        rows = _normalize_coingecko_rows(raw)
        if rows:
            return {"ok": True, "source": "coingecko", "marketType": "coingecko-fallback", "count": len(rows), "data": rows, "warning": "Using CoinGecko fallback because Binance and MEXC endpoints failed.", "debug": debug[:8]}
        debug.append("coingecko empty after normalize")
    debug.extend(errs)

    rows = _normalize_seed_rows(SEED_MARKETS)
    return {"ok": True, "source": "local-seed", "marketType": "local-seed-fallback", "count": len(rows), "data": rows, "warning": "External market APIs failed. Showing local seed data.", "debug": debug[:12]}


@app.get("/api/trending")
async def trending():
    client: httpx.AsyncClient = app.state.http
    debug: list[str] = []
    urls = [u for u in CRYPTORANK_TRENDING_ENDPOINTS if u]
    raw, errs = await _try_fetch_json(client, urls, headers=_cryptorank_headers())
    if raw is not None:
        rows = _normalize_cryptorank_rows(raw)
        if rows:
            return {"ok": True, "source": "cryptorank", "marketType": "cryptorank-trending", "count": len(rows), "data": rows[:100]}
        debug.append("cryptorank empty after normalize")
    debug.extend(errs)

    fallback = await markets()
    rows = fallback.get("data", [])
    rows = sorted(
        rows,
        key=lambda r: abs(r.get("priceChangePercent", 0)) * 0.45
        + r.get("volatility24h", 0) * 100 * 0.35
        + math.log10(max(r.get("quoteVolume", 0), 1)) * 0.20,
        reverse=True,
    )
    return {"ok": True, "source": fallback.get("source"), "marketType": "trending-fallback", "count": len(rows), "data": rows[:100], "warning": "CryptoRank trending failed. Using internal trending fallback.", "debug": debug[:8]}


@app.get("/api/markets/{symbol}")
async def market_by_symbol(symbol: str):
    sym = symbol.upper().strip()
    data = await markets()
    rows = data.get("data", [])
    found = next((r for r in rows if r["symbol"] == sym), None)
    if not found:
        trend_data = await trending()
        found = next((r for r in trend_data.get("data", []) if r["symbol"] == sym), None)
    if not found:
        raise HTTPException(status_code=404, detail=f"{sym} not found in market list.")
    return {"ok": True, "market": found, "source": data.get("source")}


@app.post("/api/simulate")
async def simulate(body: SimulateBody):
    payload = body.model_dump(by_alias=True)
    try:
        cp = payload.get("currentPrice")
        tp = payload.get("takeProfit")
        sl = payload.get("stopLoss")
        raw_errors: list[str] = []
        try:
            cp_f = float(cp) if cp is not None else None
        except (TypeError, ValueError):
            cp_f = None
        try:
            tp_f = float(tp) if tp is not None else None
        except (TypeError, ValueError):
            tp_f = None
        try:
            sl_f = float(sl) if sl is not None else None
        except (TypeError, ValueError):
            sl_f = None

        if cp_f is None or cp_f <= 0:
            raw_errors.append("currentPrice must be greater than 0")
        if tp_f is not None and cp_f is not None and tp_f >= cp_f:
            raw_errors.append("For a short setup, takeProfit should be below currentPrice")
        if sl_f is not None and cp_f is not None and sl_f <= cp_f:
            raw_errors.append("For a short setup, stopLoss should be above currentPrice")
        if raw_errors:
            raise HTTPException(status_code=400, detail={"ok": False, "errors": raw_errors})

        normalized = normalize_simulation_input(payload)
        result = run_monte_carlo_simulation(normalized)
        if not result["ok"]:
            raise HTTPException(status_code=400, detail={"ok": False, "errors": result.get("errors", [])})
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Simulation failed: {e}")


@app.post("/api/analyze/{symbol}")
async def analyze_symbol(symbol: str):
    sym = symbol.upper().strip()
    m = await market_by_symbol(sym)
    client: httpx.AsyncClient = app.state.http
    market = await _enrich_market_with_fuel_metrics(client, dict(m["market"]))
    params = build_simulation_params_from_market(market)
    sim = run_monte_carlo_simulation(normalize_simulation_input(params))
    if not sim["ok"]:
        raise HTTPException(status_code=400, detail={"ok": False, "errors": sim.get("errors", [])})
    return {"ok": True, "market": market, "autoLevels": params["autoLevels"], "results": sim}


@app.post("/api/agent-analysis")
async def agent_analysis(body: AgentBody):
    try:
        out = await generate_agent_analysis(body.model_dump())
        return {"ok": True, **out}
    except Exception as e:
        return {"ok": True, "source": "rule-based", "model": "local-fallback", "analysis": f"Agent error, fallback aktif: {e}"}

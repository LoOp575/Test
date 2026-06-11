"""
LQ-Short Hunter — FastAPI Backend
Probabilistic Liquidity Short Engine for BTC and crypto market analysis.

Ports the original Next.js logic to Python with upgrades:
  - Vectorized Monte Carlo via numpy (10–50x faster)
  - Refined volatility estimation (Parkinson + close-to-close blend)
  - Calibrated scoring (smarter status thresholds)
  - Multi-source market data (Binance Spot → Futures → CoinGecko → Seed)
  - AI Agent: AIXCHIA → Emergent LLM (Claude) → rule-based fallback chain
"""

import math
import os
from contextlib import asynccontextmanager
from typing import Any, Optional

import httpx
import numpy as np
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from monte_carlo import (
    build_simulation_params_from_market,
    run_monte_carlo_simulation,
    normalize_simulation_input,
)
from agent import generate_agent_analysis

load_dotenv()


# ============================================================
# Market data sources
# ============================================================

SPOT_ENDPOINTS = [
    "https://api.binance.com/api/v3/ticker/24hr",
    "https://api1.binance.com/api/v3/ticker/24hr",
    "https://api2.binance.com/api/v3/ticker/24hr",
    "https://api3.binance.com/api/v3/ticker/24hr",
    "https://api4.binance.com/api/v3/ticker/24hr",
]
FUTURES_ENDPOINTS = ["https://fapi.binance.com/fapi/v1/ticker/24hr"]
COINGECKO_ENDPOINT = (
    "https://api.coingecko.com/api/v3/coins/markets"
    "?vs_currency=usd&order=volume_desc&per_page=100&page=1"
    "&sparkline=false&price_change_percentage=24h"
)

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


def _normalize_binance_rows(raw: list[dict]) -> list[dict]:
    out = []
    for item in raw:
        sym = item.get("symbol") or ""
        if not sym.endswith("USDT"):
            continue
        last = _to_float(item.get("lastPrice"))
        high = _to_float(item.get("highPrice"), last)
        low = _to_float(item.get("lowPrice"), last)
        qv = _to_float(item.get("quoteVolume"))
        if last <= 0 or qv < 0:
            continue
        vol24 = (high - low) / last if last > 0 else 0
        out.append({
            "symbol": sym,
            "lastPrice": last,
            "priceChangePercent": _to_float(item.get("priceChangePercent")),
            "volume": _to_float(item.get("volume")),
            "quoteVolume": qv,
            "highPrice": high,
            "lowPrice": low,
            "volatility24h": vol24,
        })
    out.sort(key=lambda x: x["quoteVolume"], reverse=True)
    return out


def _normalize_coingecko_rows(raw: list[dict]) -> list[dict]:
    out = []
    for item in raw:
        sym = (item.get("symbol") or "").upper()
        if not sym or sym in ("USDT", "USDC", "DAI", "BUSD", "TUSD", "FDUSD", "USDE"):
            continue  # skip stables vs themselves
        last = _to_float(item.get("current_price"))
        if last <= 0:
            continue
        high = _to_float(item.get("high_24h"), last)
        low = _to_float(item.get("low_24h"), last)
        qv = _to_float(item.get("total_volume"))
        vol24 = (high - low) / last if last > 0 else 0
        out.append({
            "symbol": f"{sym}USDT",
            "lastPrice": last,
            "priceChangePercent": _to_float(item.get("price_change_percentage_24h")),
            "volume": 0,
            "quoteVolume": qv,
            "highPrice": high,
            "lowPrice": low,
            "volatility24h": vol24,
        })
    out.sort(key=lambda x: x["quoteVolume"], reverse=True)
    return out


def _normalize_seed_rows(seed: list[dict]) -> list[dict]:
    return _normalize_binance_rows([{**s, "volume": s.get("volume", 0)} for s in seed])


async def _try_fetch_array(client: httpx.AsyncClient, urls: list[str]) -> tuple[Optional[list], list[str]]:
    errors: list[str] = []
    for url in urls:
        try:
            r = await client.get(url, timeout=7.0, headers={"accept": "application/json", "user-agent": "LQ-Short-Hunter/2.0"})
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


# ============================================================
# App & Models
# ============================================================

@asynccontextmanager
async def lifespan(_app: FastAPI):
    _app.state.http = httpx.AsyncClient()
    try:
        yield
    finally:
        await _app.state.http.aclose()


app = FastAPI(title="LQ-Short Hunter API", version="2.0.0", lifespan=lifespan)
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
    simulations: Optional[int] = 50000
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


# ============================================================
# Routes
# ============================================================

@app.get("/api/health")
async def health():
    return {"ok": True, "service": "lq-short-hunter", "version": "2.0.0"}


@app.get("/api/markets")
async def markets():
    client: httpx.AsyncClient = app.state.http
    debug: list[str] = []

    # 1. Binance Spot
    raw, errs = await _try_fetch_array(client, SPOT_ENDPOINTS)
    if raw is not None:
        rows = _normalize_binance_rows(raw)
        if rows:
            return {"ok": True, "source": "binance-spot", "marketType": "binance-spot",
                    "count": len(rows), "data": rows}
        debug.append("binance-spot empty after normalize")
    debug.extend(errs)

    # 2. Binance Futures
    raw, errs = await _try_fetch_array(client, FUTURES_ENDPOINTS)
    if raw is not None:
        rows = _normalize_binance_rows(raw)
        if rows:
            return {"ok": True, "source": "binance-futures", "marketType": "binance-futures",
                    "count": len(rows), "data": rows,
                    "warning": "Using Binance Futures fallback because Binance Spot failed."}
        debug.append("binance-futures empty after normalize")
    debug.extend(errs)

    # 3. CoinGecko
    raw, errs = await _try_fetch_array(client, [COINGECKO_ENDPOINT])
    if raw is not None:
        rows = _normalize_coingecko_rows(raw)
        if rows:
            return {"ok": True, "source": "coingecko", "marketType": "coingecko-fallback",
                    "count": len(rows), "data": rows,
                    "warning": "Using CoinGecko fallback because Binance endpoints failed.",
                    "debug": debug[:8]}
        debug.append("coingecko empty after normalize")
    debug.extend(errs)

    # 4. Seed
    rows = _normalize_seed_rows(SEED_MARKETS)
    return {"ok": True, "source": "local-seed", "marketType": "local-seed-fallback",
            "count": len(rows), "data": rows,
            "warning": "External market APIs failed. Showing local seed data.",
            "debug": debug[:12]}


@app.get("/api/markets/{symbol}")
async def market_by_symbol(symbol: str):
    sym = symbol.upper().strip()
    data = await markets()
    rows = data.get("data", [])
    found = next((r for r in rows if r["symbol"] == sym), None)
    if not found:
        raise HTTPException(status_code=404, detail=f"{sym} not found in market list.")
    return {"ok": True, "market": found, "source": data.get("source")}


@app.post("/api/simulate")
async def simulate(body: SimulateBody):
    payload = body.model_dump(by_alias=True)
    try:
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
    """One-shot endpoint: fetch market, build params, simulate, return everything."""
    sym = symbol.upper().strip()
    m = await market_by_symbol(sym)
    market = m["market"]
    params = build_simulation_params_from_market(market)
    sim = run_monte_carlo_simulation(normalize_simulation_input(params))
    if not sim["ok"]:
        raise HTTPException(status_code=400, detail={"ok": False, "errors": sim.get("errors", [])})
    return {
        "ok": True,
        "market": market,
        "autoLevels": params["autoLevels"],
        "results": sim,
    }


@app.post("/api/agent-analysis")
async def agent_analysis(body: AgentBody):
    try:
        out = await generate_agent_analysis(body.model_dump())
        return {"ok": True, **out}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Agent analysis failed: {e}")

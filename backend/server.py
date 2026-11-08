"""
Meridian — Crypto Quality Screener API
======================================
Adapts the deferred-revenue quality / filing-instability models from the
reference slides to public crypto market data (Binance / MEXC / CoinGecko).

Routes:
  GET  /api/health            service status
  GET  /api/screen            ranked screener (query: limit, source)
  GET  /api/screen/{symbol}   single-asset deep dive (query: source)
  GET  /api/markets           raw 24h universe snapshot (fallback chain)

Static note: Vercel serves frontend/build and forwards /api/* here. Locally,
uvicorn serves the same app; if frontend/build exists it is mounted at /.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend import market_data
from backend.market_data import screen_cache
from backend.screening import VERDICT_META, compute_features, enrich_with_ticker

load_dotenv()

FRONTEND_BUILD = Path(__file__).resolve().parent.parent / "frontend" / "build"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    _app.state.http = httpx.AsyncClient()
    try:
        yield
    finally:
        await _app.state.http.aclose()


app = FastAPI(title="Meridian — Crypto Quality Screener", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _preferred_source() -> Optional[str]:
    src = os.getenv("SCREEN_SOURCE", "auto").strip().lower()
    if src in {"live", "seed"}:
        return src
    return None


async def _universe_rows(client: httpx.AsyncClient, limit: int, source_pref: Optional[str]) -> dict[str, Any]:
    if source_pref == "seed":
        from backend import sample_data

        return {
            "source": "seed-fallback",
            "live": False,
            "data": sample_data.seed_universe(limit),
            "debug": ["SCREEN_SOURCE=seed"],
        }
    uni = await market_data.fetch_universe(client, limit=limit)
    if source_pref == "live" and not uni.get("live"):
        # Caller explicitly demanded live data and none is reachable
        uni = {
            **uni,
            "warning": "SCREEN_SOURCE=live but all live sources failed. Serving seed data instead.",
        }
    return uni


async def _screen_symbol(
    client: httpx.AsyncClient,
    symbol: str,
    ticker: Optional[dict[str, Any]],
    kline_payload: dict[str, Any],
) -> dict[str, Any]:
    candles = kline_payload["candles"]
    result = compute_features(candles)
    result = enrich_with_ticker(result, ticker)
    sym_clean = symbol.upper().strip()
    result["symbol"] = sym_clean
    kline_live = bool(kline_payload.get("live"))
    result["sources"] = {
        "market": kline_payload["source"],
        "klines": kline_payload["source"],
        "live": kline_live,
    }
    if kline_payload.get("warning"):
        result["warning"] = kline_payload["warning"]
    if result.get("ok"):
        meta = VERDICT_META.get(result["verdict"], VERDICT_META["NEUTRAL"])
        result["verdictLabel"] = meta["label"]
        result["verdictTone"] = meta["tone"]
    return result


def _rank_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows.sort(
        key=lambda r: (
            VERDICT_META.get(r.get("verdict", "NEUTRAL"), {}).get("rank", 9),
            -(r.get("scores", {}).get("signal") or 0.0),
        )
    )
    return rows


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/api/health")
async def health():
    return {"ok": True, "service": "meridian-crypto-screen", "version": "1.0.0"}


@app.get("/api/markets")
async def markets(limit: int = Query(40, ge=1, le=100)):
    client: httpx.AsyncClient = app.state.http
    return await _universe_rows(client, limit, _preferred_source())


@app.get("/api/screen")
async def screen(
    limit: int = Query(18, ge=4, le=40),
    source: str = Query("auto", pattern="^(auto|live|seed)$"),
):
    pref = source if source != "auto" else _preferred_source()
    cache_key = f"screen:{limit}:{pref or 'auto'}"
    cached = screen_cache.get(cache_key)
    if cached is not None:
        return {**cached, "cached": True}

    client: httpx.AsyncClient = app.state.http
    uni = await _universe_rows(client, limit, pref)
    rows = uni["data"]
    symbols = [r["symbol"] for r in rows]
    tickers = {r["symbol"]: r for r in rows}
    klines = await market_data.gather_klines(client, symbols, 120)

    out_rows = []
    for sym in symbols:
        payload = klines.get(sym)
        if not payload:
            continue
        out_rows.append(await _screen_symbol(client, sym, tickers.get(sym), payload))

    ok_rows = [r for r in out_rows if r.get("ok")]
    _rank_rows(out_rows)

    counts = {v: 0 for v in VERDICT_META}
    for r in out_rows:
        counts[r.get("verdict", "NEUTRAL")] = counts.get(r.get("verdict", "NEUTRAL"), 0) + 1

    signal_scores = [r["scores"]["signal"] for r in ok_rows]
    summary = {
        "universe": len(out_rows),
        "longCandidates": counts.get("LONG", 0),
        "watchlist": counts.get("WATCH", 0),
        "avoid": counts.get("AVOID", 0),
        "noSignal": counts.get("NO_SIGNAL", 0),
        "avgSignal": round(sum(signal_scores) / len(signal_scores), 1) if signal_scores else 0.0,
    }

    live = bool(uni.get("live")) and all(k.get("live") for k in klines.values())
    result = {
        "ok": True,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "source": uni.get("source"),
        "live": live,
        "warning": uni.get("warning"),
        "summary": summary,
        "data": out_rows,
        "count": len(out_rows),
    }
    screen_cache.set(cache_key, result)
    return result


@app.get("/api/screen/{symbol}")
async def screen_symbol(symbol: str, source: str = Query("auto", pattern="^(auto|live|seed)$")):
    sym = symbol.upper().strip()
    if not sym.isalnum() or len(sym) > 20:
        raise HTTPException(status_code=400, detail="Invalid symbol.")

    pref = source if source != "auto" else _preferred_source()
    client: httpx.AsyncClient = app.state.http
    uni = await _universe_rows(client, 40, pref)
    ticker = next((r for r in uni["data"] if r["symbol"] == sym), None)
    kline_payload = await market_data.fetch_klines(client, sym, 120)
    if pref == "live" and not kline_payload.get("live"):
        kline_payload = {
            **kline_payload,
            "warning": "SCREEN_SOURCE=live requested but live klines unavailable — showing seed candles.",
        }
    result = await _screen_symbol(client, sym, ticker, kline_payload)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("reason", f"No data for {sym}."))

    candles = kline_payload["candles"]
    series = [
        {
            "t": c["date"],
            "close": c["close"],
            "baseVolume": c["volume"],
            "quoteVolume": c["quoteVolume"],
            "takerBuyRatio": round(
                (c["takerBuyBase"] / c["volume"]) if c["volume"] > 0 else 0.5, 4
            ),
        }
        for c in candles[-90:]
    ]
    result["series"] = series
    result["marketSource"] = uni.get("source")
    return {"ok": True, **result}


# ---------------------------------------------------------------------------
# Static frontend (local preview / non-Vercel serving). Registered last so
# /api/* always wins.
# ---------------------------------------------------------------------------

if FRONTEND_BUILD.is_dir():
    assets_dir = FRONTEND_BUILD / "static"
    if assets_dir.is_dir():
        app.mount("/static", StaticFiles(directory=str(assets_dir)), name="static")

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa_fallback(full_path: str):
        candidate = (FRONTEND_BUILD / full_path).resolve()
        if full_path and candidate.is_file() and str(candidate).startswith(str(FRONTEND_BUILD.resolve())):
            return FileResponse(str(candidate))
        return FileResponse(str(FRONTEND_BUILD / "index.html"))

"""
Meridian — Crypto Quality Screener API
======================================
Adapts the deferred-revenue quality / filing-instability models from the
reference slides to public crypto market data (Binance / MEXC / CoinGecko).

Routes:
  GET  /api/health                    service status
  GET  /api/screen                    ranked screener (query: limit, source)
  GET  /api/screen/{symbol}           single-asset deep dive (query: source)
  GET  /api/markets                   raw 24h universe snapshot (fallback chain)
  GET  /api/cryptorank/coins          CryptoRank market table (query: limit) — optional, additive
  GET  /api/cryptorank/coin/{id}      CryptoRank coin profile — optional, additive
  GET  /api/cryptorank/stats          CryptoRank usage/credits/cache stats
  GET  /api/cryptorank/plan           Plan tier/RPM/credits/allowed endpoints (free /v3/status)
  GET  /api/cryptorank/unlocks        Upcoming token unlocks, market-wide (Pro tier; 6h cache)
  POST /api/calibrate/run              walk-forward p^acc calibration vs heuristic (live Binance klines)
  POST /api/calibrate/sweep            auto-sweep label variants on one fetch (ranked candidates)
  GET  /api/calibrate/formulas         current heuristic formula metadata (single source of truth)
  GET  /api/calibrate/history          last 25 calibration runs recorded on this instance

Static note: Vercel serves frontend/build and forwards /api/* here. Locally,
uvicorn serves the same app; if frontend/build exists it is mounted at /.
"""

from __future__ import annotations

import os
from collections import deque
from contextlib import asynccontextmanager
from uuid import uuid4
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend import calibration_lab, cryptorank, market_data
from backend.market_data import screen_cache
from backend.screening import VERDICT_META, compute_features, enrich_with_ticker, apply_unlock_gate

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


# In-memory ring buffer of recent calibration runs (per server instance; empty
# after every redeploy — the frontend also persists runs to localStorage).
_CAL_HISTORY: deque[dict[str, Any]] = deque(maxlen=25)


class CalibrateRequest(BaseModel):
    """Request body for /api/calibrate/run and /api/calibrate/sweep."""

    lookbackDays: int = Field(730, ge=150, le=1000, description="Trading days of history to fetch (<= 1000)")
    stepDays: int = Field(7, ge=3, le=28, description="Anchor stride in days")
    label: str = Field("pos", pattern="^(pos|strong)$", description="pos: fwd 30d organic growth > 0 · strong: >= +15%")


def _history_entry(rep: dict[str, Any], req: CalibrateRequest) -> dict[str, Any]:
    v = rep.get("validation", {})
    h = rep.get("currentHeuristic", {})
    return {
        "id": uuid4().hex[:10],
        "ts": rep.get("generatedAt"),
        "label": rep.get("params", {}).get("label"),
        "lookbackDays": rep.get("params", {}).get("lookbackDays"),
        "stepDays": rep.get("params", {}).get("stepDays"),
        "samples": rep.get("samples"),
        "baseRateValidation": rep.get("baseRateValidation"),
        "l2": rep.get("l2"),
        "fitted": {"brier": v.get("brier"), "auc": v.get("auc"), "logloss": v.get("logloss")},
        "heuristic": {"brier": h.get("validationBrier"), "auc": h.get("validationAuc")},
        "verdict": (rep.get("verdict") or {}).get("status"),
    }


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


async def _attach_cryptorank(client: httpx.AsyncClient, rows: list[dict[str, Any]]) -> int:
    """Attach `cryptorank` + `unlock` context blocks in-place, then apply the unlock gate.

    Never raises. Unlock feed is Pro-gated and self-configuring via /v3/status;
    when unavailable rows get `unlock: None` and the gate is a no-op.
    Returns the number of verdicts downgraded by the unlock gate.
    """
    cr_map = await cryptorank.symbol_map(client)

    unlock_allowed = await cryptorank.plan_allows(client, "upcoming-token-unlocks")
    unlock_map: dict[str, dict[str, Any]] = {}
    if unlock_allowed is not False:
        try:
            unlock_map = await cryptorank.unlocks_by_symbol(client)
        except Exception:  # noqa: BLE001 - enrichment must never break screening
            unlock_map = {}

    changed = 0
    for r in rows:
        r["cryptorank"] = cryptorank.block_for(r.get("symbol"), cr_map)
        r["unlock"] = cryptorank.unlock_block(r.get("symbol"), unlock_map)
        r.setdefault("sources", {})["cryptorank"] = bool(cr_map)
        r["sources"]["unlock"] = unlock_allowed is not False
        if apply_unlock_gate(r, r.get("unlock"), cryptorank.UNLOCK_WATCH_PCT, cryptorank.UNLOCK_AVOID_PCT):
            changed += 1
    return changed


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
    return {
        "ok": True,
        "service": "meridian-crypto-screen",
        "version": "1.0.0",
        "cryptorank": cryptorank.stats(),
    }


@app.get("/api/markets")
async def markets(limit: int = Query(40, ge=1, le=100)):
    client: httpx.AsyncClient = app.state.http
    return await _universe_rows(client, limit, _preferred_source())


# ---------------------------------------------------------------------------
# CryptoRank (optional quality/context layer — additive, never fatal)
# ---------------------------------------------------------------------------

@app.get("/api/cryptorank/coins")
async def cryptorank_coins(limit: int = Query(50, ge=1, le=100)):
    client: httpx.AsyncClient = app.state.http
    result = await cryptorank.list_coins(client, limit=limit)
    if not result.get("ok"):
        raise HTTPException(status_code=503, detail=result)
    return {**result, "creditsUsed": cryptorank.stats()["creditsUsed"]}


@app.get("/api/cryptorank/coin/{coin_id}")
async def cryptorank_coin(coin_id: int):
    client: httpx.AsyncClient = app.state.http
    result = await cryptorank.coin_profile(client, coin_id)
    if not result.get("ok"):
        raise HTTPException(status_code=503, detail=result)
    return {**result, "creditsUsed": cryptorank.stats()["creditsUsed"]}


@app.get("/api/cryptorank/stats")
async def cryptorank_stats():
    return cryptorank.stats()


@app.get("/api/cryptorank/plan")
async def cryptorank_plan():
    """Free (/v3/status): plan tier, rate limit, credits used, endpoints allowed."""
    client: httpx.AsyncClient = app.state.http
    return await cryptorank.plan_status(client)


@app.get("/api/cryptorank/unlocks")
async def cryptorank_unlocks(days_ahead: int = Query(30, ge=1, le=365)):
    """Market-wide upcoming token unlocks (Pro tier). 1 credit per page; cached 6h."""
    client: httpx.AsyncClient = app.state.http
    result = await cryptorank.upcoming_unlocks(client, days_ahead=days_ahead, min_percent_mcap=0.5)
    if not result.get("ok"):
        raise HTTPException(status_code=503, detail=result)
    return result


@app.post("/api/calibrate/run")
async def calibrate_run(req: CalibrateRequest):
    """Walk-forward calibration of p^acc on live Binance daily klines.

    Returns the fitted L2-logistic coefficients + Brier/AUC/log-loss vs the
    production heuristic, a reliability table and an explicit lulus/kurang
    verdict. Never mutates production constants.
    """
    client: httpx.AsyncClient = app.state.http
    rep = await calibration_lab.run_calibration(
        client,
        lookback_days=req.lookbackDays,
        step_days=req.stepDays,
        label=req.label,
    )
    if rep.get("ok"):
        _CAL_HISTORY.append(_history_entry(rep, req))
    return rep


@app.post("/api/calibrate/sweep")
async def calibrate_sweep(req: CalibrateRequest):
    """Fetch once, fit both label variants, rank by validation log-loss.

    Answers "kalau kurang, kalibrasi ulang untuk menemukan rumus yang sesuai":
    returns every candidate with its verdict; nothing is auto-applied.
    """
    client: httpx.AsyncClient = app.state.http
    rep = await calibration_lab.sweep_calibration(
        client,
        lookback_days=req.lookbackDays,
        step_days=req.stepDays,
    )
    if rep.get("ok") and rep.get("bestLabel"):
        best = next((c for c in rep["candidates"] if c.get("label") == rep["bestLabel"]), None)
        if best:
            synthetic = {
                "generatedAt": rep["generatedAt"],
                "params": {**rep["params"], "label": best["label"]},
                "samples": best.get("samples"),
                "baseRateValidation": None,
                "l2": best.get("l2"),
                "validation": best.get("validation", {}),
                "currentHeuristic": best.get("currentHeuristic", {}),
                "verdict": best.get("verdict", {}),
            }
            _CAL_HISTORY.append(_history_entry(synthetic, req))
    return rep


@app.get("/api/calibrate/formulas")
async def calibrate_formulas():
    """Current p^acc heuristic formula metadata (weights are the live ones)."""
    return calibration_lab.formula_meta()


@app.get("/api/calibrate/history")
async def calibrate_history(limit: int = Query(25, ge=1, le=25)):
    """Recent calibration runs recorded on this instance (newest first).

    Empty after redeploy by design; the frontend also persists runs locally.
    """
    return {
        "ok": True,
        "count": min(limit, len(_CAL_HISTORY)),
        "volatile": True,
        "note": "In-memory per instance; hilang saat redeploy. Frontend menyimpan riwayat di browser (localStorage).",
        "runs": list(_CAL_HISTORY)[::-1][:limit],
    }


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

    gate_changed = await _attach_cryptorank(client, out_rows)
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
        "debug": uni.get("debug"),
        "cryptorank": {
            "configured": cryptorank.cr_configured(),
            "creditsUsed": cryptorank.stats()["creditsUsed"],
            "attached": sum(1 for r in out_rows if r.get("cryptorank")),
            "unlockGateApplied": gate_changed,
        },
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
    await _attach_cryptorank(client, [result])
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

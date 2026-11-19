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

from backend import calibration_lab, cryptorank, fokker_planck, fokker_planck_v2, market_data
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


async def _universe_rows(
    client: httpx.AsyncClient,
    limit: int,
    source_pref: Optional[str],
    min_quote_volume: float = 0.0,
) -> dict[str, Any]:
    if source_pref == "seed":
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
            "debug": ["SCREEN_SOURCE=seed"],
        }
    uni = await market_data.fetch_universe(client, limit=limit, min_quote_volume=min_quote_volume)
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
    limit: int = Query(18, ge=4, le=150),
    source: str = Query("auto", pattern="^(auto|live|seed)$"),
    minQuote: float = Query(None, ge=0.0, description="Override 24h quote-volume floor (USD)"),
):
    pref = source if source != "auto" else _preferred_source()
    floor = minQuote if minQuote is not None else market_data.DEFAULT_MIN_QUOTE_VOLUME
    cache_key = f"screen:{limit}:{pref or 'auto'}:{floor:.0f}"
    cached = screen_cache.get(cache_key)
    if cached is not None:
        return {**cached, "cached": True}

    client: httpx.AsyncClient = app.state.http
    uni = await _universe_rows(client, limit, pref, floor)
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

    klines_total = len(klines)
    klines_live = sum(1 for k in klines.values() if k.get("live"))
    live = bool(uni.get("live")) and klines_live == klines_total
    # Surface WHY live failed: merge universe-chain reasons with per-symbol kline
    # reasons (deduped, capped) so the UI can show them instead of hiding them.
    combined_debug = list(uni.get("debug") or [])
    seen_reasons = set(combined_debug)
    for kpayload in klines.values():
        for reason in (kpayload.get("debug") or [])[:3]:
            if reason not in seen_reasons:
                combined_debug.append(reason)
                seen_reasons.add(reason)
        if len(combined_debug) >= 15:
            break
    result = {
        "ok": True,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "source": uni.get("source"),
        "live": live,
        "liveCoverage": {
            "universeLive": bool(uni.get("live")),
            "klinesLive": klines_live,
            "klinesTotal": klines_total,
        },
        "criteria": {
            "minQuoteVolume": floor,
            "universeTotal": uni.get("universeTotal"),
            "returned": len(out_rows),
            "filters": ["USDT pair", "bukan stablecoin/fiat", f"volume 24h ≥ ${floor:,.0f}", "≥61 candle harian"],
        },
        "warning": uni.get("warning"),
        "summary": summary,
        "data": out_rows,
        "count": len(out_rows),
        "debug": combined_debug,
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
# Fokker–Planck distribution & microstructure lab (isolated, additive)
# ---------------------------------------------------------------------------

class FpSimulateRequest(BaseModel):
    """Request body for POST /api/fokker-planck/simulate."""

    mode: str = Field("orderbook", pattern="^(orderbook|direct)$")
    # Order-book & spoofing mode
    bidDepth: float = Field(100.0, ge=0.0, le=1e12)
    askDepth: float = Field(100.0, ge=0.0, le=1e12)
    takerBuyVol: float = Field(40.0, ge=0.0, le=1e12)
    takerSellVol: float = Field(40.0, ge=0.0, le=1e12)
    canceledBidVol: float = Field(10.0, ge=0.0, le=1e12)
    canceledAskVol: float = Field(10.0, ge=0.0, le=1e12)
    sigmaPerSec: float = Field(0.0007, ge=1e-5, le=0.05)
    horizonSec: float = Field(30.0, ge=1.0, le=600.0)
    tauSpoofSec: float = Field(6.0, ge=1.0, le=120.0)
    midPrice: Optional[float] = Field(None, gt=0.0, le=1e9)
    # Shared zone threshold
    stableBand: float = Field(0.004, ge=0.0005, le=0.50)
    # Direct PDE sandbox mode
    mu: float = Field(-0.02, ge=-2.0, le=2.0)
    diffusion: float = Field(0.0025, ge=1e-7, le=2.0)
    kappa: float = Field(0.05, ge=0.0, le=5.0)
    tailEta: float = Field(0.20, ge=0.0, le=1.5)
    downsideAsym: float = Field(0.25, ge=0.0, le=1.5)
    horizon: float = Field(1.0, ge=0.05, le=120.0)
    naiveP0: float = Field(0.80, ge=0.01, le=0.99)


class FpBacktestRequest(BaseModel):
    """Request body for POST /api/fokker-planck/backtest."""

    horizonDays: int = Field(7, ge=1, le=30)
    stableBand: float = Field(0.05, ge=0.01, le=0.25)
    stepDays: int = Field(7, ge=3, le=21)
    lookbackDays: int = Field(240, ge=100, le=500)
    limit: int = Field(16, ge=4, le=40)
    source: str = Field("auto", pattern="^(auto|live|seed)$")


async def _fp_klines_map(
    client: httpx.AsyncClient,
    symbols: list[str],
    limit: int,
    pref: Optional[str],
) -> dict[str, dict[str, Any]]:
    if pref == "seed":
        from backend import sample_data

        return {
            sym: {
                "source": "seed-fallback",
                "live": False,
                "candles": sample_data.seed_klines(sym, limit),
            }
            for sym in symbols
        }
    return await market_data.gather_klines(client, symbols, limit)


@app.get("/api/fokker-planck/spec")
async def fp_spec():
    """Governing PDE metadata, zone definitions, and microstructure presets."""
    return {"ok": True, **fokker_planck.formula_spec()}


@app.get("/api/fokker-planck/universe")
async def fp_universe(
    limit: int = Query(18, ge=4, le=60),
    horizonDays: float = Query(7.0, ge=1.0, le=60.0),
    stableBand: float = Query(0.05, ge=0.01, le=0.30),
    source: str = Query("auto", pattern="^(auto|live|seed)$"),
    minQuote: float = Query(None, ge=0.0),
):
    """Run the 1D Fokker–Planck distribution engine across the existing market universe."""
    pref = source if source != "auto" else _preferred_source()
    floor = minQuote if minQuote is not None else market_data.DEFAULT_MIN_QUOTE_VOLUME
    cache_key = f"fp_uni:{limit}:{horizonDays:.2f}:{stableBand:.4f}:{pref or 'auto'}:{floor:.0f}"
    cached = fokker_planck.fp_cache.get(cache_key)
    if cached is not None:
        return {**cached, "cached": True}

    client: httpx.AsyncClient = app.state.http
    uni = await _universe_rows(client, limit, pref, floor)
    rows = uni["data"]
    symbols = [r["symbol"] for r in rows]
    tickers = {r["symbol"]: r for r in rows}
    klines = await _fp_klines_map(client, symbols, 120, pref)

    cr_map: dict[str, Any] = {}
    try:
        cr_map = await cryptorank.symbol_map(client)
    except Exception:  # noqa: BLE001 - optional enrichment never blocks
        cr_map = {}

    out_rows: list[dict[str, Any]] = []
    for sym in symbols:
        payload = klines.get(sym)
        if not payload:
            continue
        candles = payload.get("candles") or []
        fp_res = fokker_planck.analyze_candles_fp(
            candles,
            horizon_days=horizonDays,
            stable_band=stableBand,
            include_curve=False,
        )
        if not fp_res.get("ok"):
            continue
        screen_res = compute_features(candles)
        verdict = screen_res.get("verdict", "NEUTRAL") if screen_res.get("ok") else "INSUFFICIENT_DATA"
        v_meta = VERDICT_META.get(verdict, VERDICT_META["NEUTRAL"])
        tk = tickers.get(sym) or {}
        out_rows.append(
            {
                "symbol": sym,
                "baseAsset": sym[:-4] if sym.endswith("USDT") else sym,
                "price": tk.get("lastPrice") or fp_res["price"],
                "priceChange24h": tk.get("priceChangePercent"),
                "quoteVolume24h": tk.get("quoteVolume"),
                "regime": fp_res["regime"],
                "regimeLabel": fp_res["regimeLabel"],
                "regimeTone": fp_res["regimeTone"],
                "regimeRank": fp_res["regimeRank"],
                "metrics": fp_res["metrics"],
                "parameters": fp_res["parameters"],
                "priceBands": fp_res["priceBands"],
                "screening": {
                    "verdict": verdict,
                    "verdictLabel": v_meta["label"],
                    "verdictTone": v_meta["tone"],
                    "signal": round(screen_res.get("scores", {}).get("signal", 0.0), 1)
                    if screen_res.get("ok")
                    else None,
                },
                "cryptorank": cryptorank.block_for(sym, cr_map) if cr_map else None,
                "klineSource": payload.get("source"),
                "live": bool(payload.get("live")),
            }
        )

    out_rows.sort(
        key=lambda r: (
            r["regimeRank"],
            -r["metrics"]["pStable"],
            r["metrics"]["pTailCrash"],
        )
    )

    n_ok = len(out_rows)
    summary = {
        "universe": n_ok,
        "avgPStable": round(sum(r["metrics"]["pStable"] for r in out_rows) / n_ok, 4) if n_ok else 0.0,
        "avgPDownside": round(sum(r["metrics"]["pDownside"] for r in out_rows) / n_ok, 4) if n_ok else 0.0,
        "avgPUpside": round(sum(r["metrics"]["pUpside"] for r in out_rows) / n_ok, 4) if n_ok else 0.0,
        "stableAccumCount": sum(
            1 for r in out_rows if r["regime"] in {"ACCUMULATION_STABLE", "BULLISH_EXPANSION"}
        ),
        "calmCount": sum(1 for r in out_rows if r["regime"] == "SIDEWAYS_CALM"),
        "tailRiskCount": sum(
            1 for r in out_rows if r["regime"] in {"DOWNSIDE_SKEW", "TAIL_CRASH_RISK"}
        ),
        "dispersedCount": sum(1 for r in out_rows if r["regime"] == "HIGH_VOL_DISPERSED"),
    }

    klines_live = sum(1 for k in klines.values() if k.get("live"))
    live_all = bool(uni.get("live")) and klines_live == len(klines) and len(klines) > 0
    result = {
        "ok": True,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "source": uni.get("source"),
        "live": live_all,
        "warning": uni.get("warning"),
        "params": {
            "horizonDays": horizonDays,
            "stableBand": stableBand,
            "minQuoteVolume": floor,
        },
        "summary": summary,
        "count": n_ok,
        "data": out_rows,
    }
    fokker_planck.fp_cache.set(cache_key, result)
    return result


@app.get("/api/fokker-planck/asset/{symbol}")
async def fp_asset(
    symbol: str,
    horizonDays: float = Query(7.0, ge=1.0, le=60.0),
    stableBand: float = Query(0.05, ge=0.01, le=0.30),
    microHorizonSec: float = Query(30.0, ge=5.0, le=300.0),
    microBand: float = Query(0.004, ge=0.0005, le=0.05),
    source: str = Query("auto", pattern="^(auto|live|seed)$"),
):
    """Single-asset Fokker–Planck PDF curve, horizon timeline, and order-book spoof snapshot."""
    sym = symbol.upper().strip()
    if not sym.isalnum() or len(sym) > 20:
        raise HTTPException(status_code=400, detail="Simbol tidak valid.")
    if not sym.endswith("USDT") and len(sym) <= 10:
        sym = f"{sym}USDT"

    pref = source if source != "auto" else _preferred_source()
    client: httpx.AsyncClient = app.state.http
    if pref == "seed":
        from backend import sample_data

        kline_payload = {
            "source": "seed-fallback",
            "live": False,
            "candles": sample_data.seed_klines(sym, 120),
            "warning": "Mode data demo (seed) aktif.",
        }
    else:
        kline_payload = await market_data.fetch_klines(client, sym, 120)

    candles = kline_payload.get("candles") or []
    fp_res = fokker_planck.analyze_candles_fp(
        candles,
        horizon_days=horizonDays,
        stable_band=stableBand,
        include_curve=True,
    )
    if not fp_res.get("ok"):
        raise HTTPException(status_code=404, detail=fp_res.get("reason", f"Data {sym} tidak cukup."))

    micro = await fokker_planck.fetch_microstructure_snapshot(
        client,
        sym,
        fallback_candles=candles,
        horizon_sec=microHorizonSec,
        stable_band=microBand,
        prefer_seed=(pref == "seed"),
    )

    screen_res = compute_features(candles)
    screening_block: Optional[dict[str, Any]] = None
    if screen_res.get("ok"):
        v = screen_res["verdict"]
        v_meta = VERDICT_META.get(v, VERDICT_META["NEUTRAL"])
        screening_block = {
            "verdict": v,
            "verdictLabel": v_meta["label"],
            "verdictTone": v_meta["tone"],
            "scores": screen_res.get("scores"),
            "probabilities": screen_res.get("probabilities"),
        }

    cr_block = None
    try:
        cr_map = await cryptorank.symbol_map(client)
        if cr_map:
            cr_block = cryptorank.block_for(sym, cr_map)
    except Exception:  # noqa: BLE001
        cr_block = None

    price_series = [
        {
            "t": c["date"],
            "close": c["close"],
            "high": c["high"],
            "low": c["low"],
            "takerBuyShare": round((c["takerBuyBase"] / c["volume"]) if c["volume"] > 0 else 0.5, 4),
        }
        for c in candles[-60:]
    ]

    return {
        "ok": True,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "symbol": sym,
        "baseAsset": sym[:-4] if sym.endswith("USDT") else sym,
        "source": kline_payload.get("source"),
        "live": bool(kline_payload.get("live")),
        "warning": kline_payload.get("warning"),
        "candleAnalysis": fp_res,
        "microstructure": micro,
        "screening": screening_block,
        "cryptorank": cr_block,
        "priceSeries": price_series,
    }


@app.post("/api/fokker-planck/simulate")
async def fp_simulate(req: FpSimulateRequest):
    """Interactive simulation for order-book spoofing scenarios or direct (μ, D, t) inputs."""
    if req.mode == "direct":
        return fokker_planck.simulate_direct_pde(
            mu=req.mu,
            diffusion=req.diffusion,
            kappa=req.kappa,
            tail_eta=req.tailEta,
            downside_asym=req.downsideAsym,
            horizon=req.horizon,
            stable_band=req.stableBand,
            naive_p0=req.naiveP0,
        )
    return fokker_planck.analyze_orderbook_fp(
        bid_depth=req.bidDepth,
        ask_depth=req.askDepth,
        taker_buy_vol=req.takerBuyVol,
        taker_sell_vol=req.takerSellVol,
        canceled_bid_vol=req.canceledBidVol,
        canceled_ask_vol=req.canceledAskVol,
        sigma_per_sec=req.sigmaPerSec,
        horizon_sec=req.horizonSec,
        stable_band=req.stableBand,
        tau_spoof_sec=req.tauSpoofSec,
        mid_price=req.midPrice,
    )


@app.post("/api/fokker-planck/backtest")
async def fp_backtest(req: FpBacktestRequest):
    """Chronological walk-forward validation of Fokker–Planck zone probabilities."""
    pref = req.source if req.source != "auto" else _preferred_source()
    client: httpx.AsyncClient = app.state.http
    uni = await _universe_rows(client, req.limit, pref, market_data.DEFAULT_MIN_QUOTE_VOLUME)
    symbols = [r["symbol"] for r in uni["data"]]
    klines = await _fp_klines_map(client, symbols, req.lookbackDays, pref)
    candles_by_sym = {s: p["candles"] for s, p in klines.items() if p and p.get("candles")}
    live_count = sum(1 for p in klines.values() if p and p.get("live"))
    is_live = bool(uni.get("live")) and live_count == len(klines) and len(klines) > 0
    return fokker_planck.backtest_candles_fp(
        candles_by_sym,
        horizon_days=req.horizonDays,
        stable_band=req.stableBand,
        step_days=req.stepDays,
        source_label=str(uni.get("source") or "unknown"),
        live=is_live,
    )


# ---------------------------------------------------------------------------
# Fokker–Planck Model v2 & Side-by-Side v1 vs v2 Comparison (strictly isolated;
# v1 module, routes, caches, and UI above remain 100% untouched)
# ---------------------------------------------------------------------------

class FpV2CompareSimRequest(BaseModel):
    """Request body for POST /api/fokker-planck-v2/compare/simulate."""

    bidDepth: float = Field(100.0, ge=0.0, le=1e12)
    askDepth: float = Field(100.0, ge=0.0, le=1e12)
    farBidDepth: float = Field(0.0, ge=0.0, le=1e12)
    farAskDepth: float = Field(0.0, ge=0.0, le=1e12)
    wallDistancePct: float = Field(0.008, ge=0.003, le=0.05)
    decayLambda: Optional[float] = Field(None, ge=100.0, le=2000.0)
    takerBuyVol: float = Field(40.0, ge=0.0, le=1e12)
    takerSellVol: float = Field(40.0, ge=0.0, le=1e12)
    canceledBidVol: float = Field(10.0, ge=0.0, le=1e12)
    canceledAskVol: float = Field(10.0, ge=0.0, le=1e12)
    rawBidForV1: Optional[float] = Field(None, ge=0.0, le=1e12)
    rawAskForV1: Optional[float] = Field(None, ge=0.0, le=1e12)
    rawCancelBidForV1: Optional[float] = Field(None, ge=0.0, le=1e12)
    rawCancelAskForV1: Optional[float] = Field(None, ge=0.0, le=1e12)
    spreadBps: float = Field(8.0, ge=0.1, le=500.0)
    priceChangeBps: Optional[float] = Field(None, ge=-5000.0, le=5000.0)
    tradeWindowSec: float = Field(15.0, ge=1.0, le=300.0)
    bestBid: Optional[float] = Field(None, gt=0.0, le=1e9)
    bestAsk: Optional[float] = Field(None, gt=0.0, le=1e9)
    bestBidQty: Optional[float] = Field(None, ge=0.0, le=1e12)
    bestAskQty: Optional[float] = Field(None, ge=0.0, le=1e12)
    sigmaPerSec: float = Field(0.0007, ge=1e-5, le=0.05)
    horizonSec: float = Field(30.0, ge=1.0, le=600.0)
    tauSpoofSec: float = Field(6.0, ge=1.0, le=120.0)
    stableBand: float = Field(0.004, ge=0.0005, le=0.50)
    midPrice: Optional[float] = Field(None, gt=0.0, le=1e9)


@app.get("/api/fokker-planck-v2/spec")
async def fp_v2_spec():
    """Model v2 formula translation diff table and v1-vs-v2 scenario presets."""
    return {"ok": True, **fokker_planck_v2.formula_spec_v2()}


@app.get("/api/fokker-planck-v2/compare/universe")
async def fp_v2_compare_universe(
    limit: int = Query(18, ge=4, le=60),
    horizonDays: float = Query(7.0, ge=1.0, le=60.0),
    stableBand: float = Query(0.05, ge=0.01, le=0.30),
    source: str = Query("auto", pattern="^(auto|live|seed)$"),
    minQuote: float = Query(None, ge=0.0),
):
    """Side-by-side universe scan comparing untouched Model v1 vs 3-Pillar Model v2."""
    pref = source if source != "auto" else _preferred_source()
    floor = minQuote if minQuote is not None else market_data.DEFAULT_MIN_QUOTE_VOLUME
    cache_key = f"fp_v2_cmp_uni:{limit}:{horizonDays:.2f}:{stableBand:.4f}:{pref or 'auto'}:{floor:.0f}"
    cached = fokker_planck_v2.fp_v2_cache.get(cache_key)
    if cached is not None:
        return {**cached, "cached": True}

    client: httpx.AsyncClient = app.state.http
    uni = await _universe_rows(client, limit, pref, floor)
    rows = uni["data"]
    symbols = [r["symbol"] for r in rows]
    tickers = {r["symbol"]: r for r in rows}
    klines = await _fp_klines_map(client, symbols, 120, pref)

    out_rows: list[dict[str, Any]] = []
    for sym in symbols:
        payload = klines.get(sym)
        if not payload:
            continue
        candles = payload.get("candles") or []
        cmp_res = fokker_planck_v2.compare_candles_v1_v2(
            candles,
            horizon_days=horizonDays,
            stable_band=stableBand,
            include_curve=False,
        )
        if not cmp_res.get("ok"):
            continue
        r1, r2, cmp_m = cmp_res["v1"], cmp_res["v2"], cmp_res["comparison"]
        tk = tickers.get(sym) or {}
        out_rows.append(
            {
                "symbol": sym,
                "baseAsset": sym[:-4] if sym.endswith("USDT") else sym,
                "price": tk.get("lastPrice") or r2["price"],
                "priceChange24h": tk.get("priceChangePercent"),
                "quoteVolume24h": tk.get("quoteVolume"),
                "v1": {
                    "regime": r1["regime"],
                    "regimeLabel": r1["regimeLabel"],
                    "regimeTone": r1["regimeTone"],
                    "metrics": r1["metrics"],
                    "parameters": r1["parameters"],
                },
                "v2": {
                    "regime": r2["regime"],
                    "regimeLabel": r2["regimeLabel"],
                    "regimeTone": r2["regimeTone"],
                    "metrics": r2["metrics"],
                    "parameters": r2["parameters"],
                },
                "comparison": cmp_m,
            }
        )

    out_rows.sort(
        key=lambda r: (
            0 if r["comparison"]["sameRegime"] else 1,
            -r["comparison"]["pdfOverlap"],
            -r["v2"]["metrics"]["pStable"],
        )
    )

    n_ok = len(out_rows)
    same_reg = sum(1 for r in out_rows if r["comparison"]["sameRegime"])
    strong_cnt = sum(1 for r in out_rows if r["comparison"]["status"] == "SEPAKAT_KUAT")
    dir_cnt = sum(1 for r in out_rows if r["comparison"]["sameDirection"])
    div_cnt = sum(
        1 for r in out_rows if r["comparison"]["status"] in {"DIVERGENSI", "FILTER_V2_AKTIF"}
    )
    summary = {
        "universe": n_ok,
        "sameRegimeCount": same_reg,
        "strongConsensusCount": strong_cnt,
        "sameDirectionCount": dir_cnt,
        "divergenceCount": div_cnt,
        "agreementRate": round(same_reg / n_ok, 4) if n_ok else 0.0,
        "avgOverlap": round(
            sum(r["comparison"]["pdfOverlap"] for r in out_rows) / n_ok, 4
        )
        if n_ok
        else 0.0,
        "avgPStableV1": round(
            sum(r["v1"]["metrics"]["pStable"] for r in out_rows) / n_ok, 4
        )
        if n_ok
        else 0.0,
        "avgPStableV2": round(
            sum(r["v2"]["metrics"]["pStable"] for r in out_rows) / n_ok, 4
        )
        if n_ok
        else 0.0,
    }

    klines_live = sum(1 for k in klines.values() if k.get("live"))
    live_all = bool(uni.get("live")) and klines_live == len(klines) and len(klines) > 0
    result = {
        "ok": True,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "source": uni.get("source"),
        "live": live_all,
        "warning": uni.get("warning"),
        "params": {
            "horizonDays": horizonDays,
            "stableBand": stableBand,
            "minQuoteVolume": floor,
        },
        "summary": summary,
        "count": n_ok,
        "data": out_rows,
    }
    fokker_planck_v2.fp_v2_cache.set(cache_key, result)
    return result


@app.get("/api/fokker-planck-v2/compare/asset/{symbol}")
async def fp_v2_compare_asset(
    symbol: str,
    horizonDays: float = Query(7.0, ge=1.0, le=60.0),
    stableBand: float = Query(0.05, ge=0.01, le=0.30),
    microHorizonSec: float = Query(30.0, ge=5.0, le=300.0),
    microBand: float = Query(0.004, ge=0.0005, le=0.05),
    source: str = Query("auto", pattern="^(auto|live|seed)$"),
):
    """Side-by-side single-asset comparison: candle PDF overlay + order-book v1 vs v2."""
    sym = symbol.upper().strip()
    if not sym.isalnum() or len(sym) > 20:
        raise HTTPException(status_code=400, detail="Simbol tidak valid.")
    if not sym.endswith("USDT") and len(sym) <= 10:
        sym = f"{sym}USDT"

    pref = source if source != "auto" else _preferred_source()
    client: httpx.AsyncClient = app.state.http
    if pref == "seed":
        from backend import sample_data

        kline_payload = {
            "source": "seed-fallback",
            "live": False,
            "candles": sample_data.seed_klines(sym, 120),
            "warning": "Mode data demo (seed) aktif.",
        }
    else:
        kline_payload = await market_data.fetch_klines(client, sym, 120)

    candles = kline_payload.get("candles") or []
    candle_cmp = fokker_planck_v2.compare_candles_v1_v2(
        candles,
        horizon_days=horizonDays,
        stable_band=stableBand,
        include_curve=True,
    )
    if not candle_cmp.get("ok"):
        raise HTTPException(status_code=404, detail=candle_cmp.get("reason", f"Data {sym} tidak cukup."))

    micro_cmp = await fokker_planck_v2.compare_microstructure_snapshot_v1_v2(
        client,
        sym,
        fallback_candles=candles,
        horizon_sec=microHorizonSec,
        stable_band=microBand,
        prefer_seed=(pref == "seed"),
    )

    return {
        "ok": True,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "symbol": sym,
        "baseAsset": sym[:-4] if sym.endswith("USDT") else sym,
        "source": kline_payload.get("source"),
        "live": bool(kline_payload.get("live")),
        "warning": kline_payload.get("warning"),
        "candleCompare": candle_cmp,
        "microCompare": micro_cmp,
    }


@app.post("/api/fokker-planck-v2/compare/simulate")
async def fp_v2_compare_simulate(req: FpV2CompareSimRequest):
    """Side-by-side interactive order-book & anti-spoofing simulation (v1 vs v2)."""
    return fokker_planck_v2.compare_orderbook_v1_v2(
        bid_depth=req.bidDepth,
        ask_depth=req.askDepth,
        far_bid_depth=req.farBidDepth,
        far_ask_depth=req.farAskDepth,
        wall_distance_pct=req.wallDistancePct,
        decay_lambda=req.decayLambda,
        taker_buy_vol=req.takerBuyVol,
        taker_sell_vol=req.takerSellVol,
        canceled_bid_vol=req.canceledBidVol,
        canceled_ask_vol=req.canceledAskVol,
        raw_bid_for_v1=req.rawBidForV1,
        raw_ask_for_v1=req.rawAskForV1,
        raw_cancel_bid_for_v1=req.rawCancelBidForV1,
        raw_cancel_ask_for_v1=req.rawCancelAskForV1,
        spread_bps=req.spreadBps,
        price_change_bps=req.priceChangeBps,
        trade_window_sec=req.tradeWindowSec,
        best_bid=req.bestBid,
        best_ask=req.bestAsk,
        best_bid_qty=req.bestBidQty,
        best_ask_qty=req.bestAskQty,
        sigma_per_sec=req.sigmaPerSec,
        horizon_sec=req.horizonSec,
        tau_spoof_sec=req.tauSpoofSec,
        stable_band=req.stableBand,
        mid_price=req.midPrice,
    )


@app.post("/api/fokker-planck-v2/compare/backtest")
async def fp_v2_compare_backtest(req: FpBacktestRequest):
    """Chronological walk-forward accuracy showdown: untouched v1 vs v2 vs v1∩v2 ensemble."""
    pref = req.source if req.source != "auto" else _preferred_source()
    client: httpx.AsyncClient = app.state.http
    uni = await _universe_rows(client, req.limit, pref, market_data.DEFAULT_MIN_QUOTE_VOLUME)
    symbols = [r["symbol"] for r in uni["data"]]
    klines = await _fp_klines_map(client, symbols, req.lookbackDays, pref)
    candles_by_sym = {s: p["candles"] for s, p in klines.items() if p and p.get("candles")}
    live_count = sum(1 for p in klines.values() if p and p.get("live"))
    is_live = bool(uni.get("live")) and live_count == len(klines) and len(klines) > 0
    return fokker_planck_v2.compare_backtest_v1_v2(
        candles_by_sym,
        horizon_days=req.horizonDays,
        stable_band=req.stableBand,
        step_days=req.stepDays,
        source_label=str(uni.get("source") or "unknown"),
        live=is_live,
    )


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

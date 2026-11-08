# Meridian — Crypto Quality Screener · PRD

## What it is
Web app implementing an adaptation of the deferred-revenue quality / filing-instability
quantitative models for crypto assets. Replaces the former "LQ-Short Hunter"
(Monte Carlo / pump-exhaustion short engine) — that project was retired, not extended.

## Why
Find accumulation candidates where unit-level demand compounds faster than price,
and filter out hype/distress using a red-flag instability layer — with transparent,
documented math and no black boxes.

## Users
Crypto traders / quant researchers comfortable with factor-style screening.

## Delivered (v1.0.0)
- `/api/screen` ranked screen + summary; `/api/screen/{symbol}` deep dive; 60s TTL cache; concurrency-capped kline fan-out
- Multi-source data fallback (Binance → MEXC → Futures → CoinGecko) + labelled deterministic demo data
- Pure-computation model in `backend/screening.py` with 10 unit tests
- Elegant light design (paper/pine/antique-gold; Fraunces + Inter + IBM Plex Mono)
- Pages: Screener (sortable/filterable/searchable table), Token detail (charts, evidence, metrics), Methodology (full formula documentation)
- Single-port preview: FastAPI serves `frontend/build` + SPA fallback

## Honest gaps / backlog
- P1: backtest harness + threshold calibration against historical Binance data
- P1: real p(accel)/p(adverse) classifiers trained on labelled outcomes
- P2: derivatives data (funding, OI), unlock/vesting schedules, exchange-specific liquidity depth
- P2: intraday (1h/4h) confirmation replacing daily VWAP for timing
- P2: export screen (CSV/PNG), watchlist persistence (localStorage)

## Non-goals
Trade execution, portfolio tracking, price prediction claims. Not investment advice.

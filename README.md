# Meridian — Crypto Quality Screener

An adapted **deferred-revenue quality model** for crypto assets. Meridian separates
**organic unit demand** (base-asset volume) from price-driven volume, scores retention,
buy-side conversion and flow stability, flags filing-style instability — and ranks liquid
USDT pairs for accumulation opportunities.

Derived from the quantitative slides (two models): a *Deferred-Revenue Quality* model
(long candidates) and a *Filing-Instability* model (red flags). See
`docs/SCREENING_MODEL.md` for the full formula mapping, and the in-app **Methodology**
page for the annotated version.

> Research tool, not financial advice. Probabilities are heuristic and uncalibrated.

## Stack

- **Frontend**: React 18 + TypeScript-free JSX, Tailwind v3, Recharts, Fraunces/Inter/IBM Plex Mono
- **Backend**: FastAPI (Python 3.10+), httpx with multi-source fallbacks, stdlib math only
- **Data**: Binance public-data mirror (`data-api.binance.vision`) → Binance.US → Binance spot → MEXC spot → Binance futures → CoinGecko (daily OHLCV + taker-buy volume); deterministic seed fallback offline

### CryptoRank (optional additive context layer)

Set `CRYPTORANK_API_KEY` (dashboard: https://cryptorank.io/public-api/dashboard) to attach a `cryptorank` block (rank, market cap, circulating supply, 24h corroboration) to every `/api/screen` row and to enable:

- `GET /api/cryptorank/coins?limit=50` — CryptoRank market table (1 credit)
- `GET /api/cryptorank/coin/{id}` — single coin profile (1 credit)
- `GET /api/cryptorank/stats` — key/cache/credit usage
- `GET /api/cryptorank/plan` — plan tier, RPM, credits, allowed endpoints (FREE `/v3/status`)
- `GET /api/cryptorank/unlocks` — market-wide upcoming token unlocks (Pro tier)
- `POST /api/calibrate/run` — walk-forward p^acc calibration on live Binance klines (never the seed universe; never auto-applies)
- `POST /api/calibrate/sweep` — fetch once, fit both label variants, rank by validation log-loss
- `GET /api/calibrate/formulas` — current p^acc heuristic metadata (weights = the live ones)
- `GET /api/calibrate/history` — last 25 runs recorded on the instance (ephemeral; the frontend also keeps browser-local history)
- `/api/health` — `cryptorank.configured`, `creditsUsed`, cache stats

**Unlock-overhang gate** (Pro plan): LONG verdicts are vetoed to WATCH (>=1% of market cap unlocking within 30d) or AVOID (>=3%) — an additive supply-shock red flag (`CR_UNLOCK_WATCH_PCT` / `CR_UNLOCK_AVOID_PCT`, default 1.0/3.0). No-op when the plan lacks the Pro unlock feed.

**Calibrating the heuristic probability** — two paths, both keep production unchanged until a reviewed run justifies it:

1. In-app: the **Kalibrasi** tab (`/calibration`) — runs the walk-forward fit on demand from the browser (live Binance klines, capped at 1000 candles/symbol), records results to browser-local history plus an ephemeral server ring buffer, shows fitted-vs-heuristic Brier/AUC/log-loss with an explicit **lulus/kurang** verdict, and the "Cari rumus terbaik" button sweeps both label variants and reports the honest winner (or says nothing passes). Nothing is applied automatically — there is deliberately no "apply" button.
2. CLI: `python3 scripts/calibrate.py` — same walk-forward Logistic fit with metrics vs the current hand-tuned sigmoid.

See docs/SCREENING_MODEL.md.

The screening formula is **unchanged**; CryptoRank data is additive context only. Endpoints used are the Sandbox-tier v3 endpoints (`/v3/currencies/list`, `/v3/currencies/{id}`, `/v3/currencies/map`), 1 credit per call, in-process cached for `CR_CACHE_SECONDS` (default 300s). If the key is missing or CryptoRank fails, the screener keeps working and rows simply carry `cryptorank: null`. Key stays server-side (`X-Api-Key` header) — never commit it. See `.env.example`.
- **Deploy**: Vercel (`api/index.py` serverless function + static frontend)

## Run locally

```bash
pip install -r requirements.txt
cd frontend && npm install && npm run build && cd ..
SCREEN_SOURCE=auto uvicorn backend.server:app --port 8000
```

Open <http://localhost:8000> — the API also serves the built SPA (single port).

Dev mode (hot reload):

```bash
uvicorn backend.server:app --reload --port 8000     # terminal 1
cd frontend && npm start                              # terminal 2 (proxy -> :8000)
```

### Live data vs demo data

| `SCREEN_SOURCE` | Behaviour |
|---|---|
| `auto` (default) | Live APIs, deterministic demo candles only if every source fails |
| `live` | Live APIs only (falls back to demo with a warning banner if unreachable) |
| `seed` | Demo data always (useful offline / CI) |

The UI shows a **Demo data** banner whenever synthetic candles are in play.

## API

| Route | Description |
|---|---|
| `GET /api/health` | Service status |
| `GET /api/screen?limit=18&source=auto` | Ranked screener + summary stats (60s in-process cache) |
| `GET /api/screen/{symbol}?source=auto` | Single asset: full breakdown, 90d series, verdict reasons |
| `GET /api/markets?limit=40` | Raw 24h universe snapshot |

## Model in one line

`Q↑DR = sat(G↑org)₊ · S↑flow · (1 + Q↑ret)₊ · (1 + C↑cash)₊` → quality score;
`S↑DR` = weighted geometric mean of (organic growth, acceleration, demand lead, gap) →
signal score; `LONG` requires signal ≥ 55, p(accel) ≥ 60%, organic growth ≤ 90%, price > VWAP-20 —
and is vetoed by low-quality-growth, billing-distortion and red-flag filters.

## Tests

```bash
python3 -m pytest backend/tests -q
```

## Project structure

```txt
.
├── api/index.py            # Vercel serverless entrypoint (exposes FastAPI `app`)
├── backend/
│   ├── server.py           # FastAPI routes + SPA static serving
│   ├── screening.py        # Adapted model — pure computation, no I/O (unit tested)
│   ├── market_data.py      # Source fallbacks, klines, TTL cache, concurrency
│   ├── sample_data.py      # Deterministic synthetic candles (labelled fallback)
│   └── tests/test_screening.py
├── frontend/               # React SPA (build/ served by FastAPI or Vercel)
├── docs/SCREENING_MODEL.md # Formula mapping & limitations
└── vercel.json
```

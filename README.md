# LQ-Short Hunter

Probabilistic Liquidity Short Engine for BTC and crypto market analysis.

This project uses Monte Carlo simulation with Geometric Brownian Motion and liquidity pressure scoring to estimate short-entry probability, downside probability, take-profit probability, stop-loss probability, and expected value.

## Core Idea

Most traders only look at candles and momentum. LQ-Short Hunter focuses on:

- liquidity pressure
- spot flow
- open interest flow
- liquidation magnet
- Monte Carlo probability
- short entry risk/reward
- expected value
- Binance USDT 24hr screener

This tool does not predict the future. It estimates possible outcomes based on assumptions and simulation.

## Install

```bash
npm install
cd frontend && npm install
```

## Run Development Servers

```bash
uvicorn backend.server:app --reload --port 8000
cd frontend && REACT_APP_BACKEND_URL=http://localhost:8000 npm start
```

Open:

```txt
http://localhost:3000
```

## Build

```bash
npm run build
cd frontend && npm run build
```

## Deploy to Vercel

Import the repository at its root and leave the Vercel framework preset as
`Other`. The root build command builds the React dashboard from `frontend/`.
The `api/index.py` entrypoint exposes FastAPI as a Vercel Python function,
`/api/*` requests are forwarded to it, and browser routes such as
`/analyze/BTCUSDT` are sent back to the React application. Static frontend
assets are served before the SPA fallback so JavaScript and CSS are never
rewritten to `index.html`.

No backend URL environment variable is needed on Vercel because the frontend
uses same-origin `/api/*` requests. Optional AI providers can be enabled with
`AIXCHIA_API_KEY` or `EMERGENT_LLM_KEY`; without them, the built-in rule-based
analysis remains available.

## Project Structure

```txt
.
├── package.json
├── next.config.js
├── styles/
│   └── globals.css
├── lib/
│   ├── utils.js
│   └── monteCarlo.js
├── pages/
│   ├── _app.js
│   ├── index.jsx
│   └── api/
│       ├── simulate.js
│       └── binance24hr.js
└── components/
    ├── InputPanel.jsx
    ├── ResultPanel.jsx
    ├── ScoreGauge.jsx
    ├── StatusBadge.jsx
    ├── MetricCard.jsx
    ├── DistributionChart.jsx
    └── ScreenerPanel.jsx
```

## Formula

Liquidity Pressure:

```txt
L = 0.4 * SpotFlow + 0.3 * OIFlow + 0.3 * LiquidationMagnet
```

Liquidation Magnet:

```txt
LiquidationMagnet = (ShortLiqAbove - LongLiqBelow) / (ShortLiqAbove + LongLiqBelow)
```

Adjusted Drift:

```txt
muAdjusted = mu + lambda * L
```

Monte Carlo GBM:

```txt
ST = S0 * exp((muAdjusted - 0.5 * sigma^2) * T + sigma * sqrt(T) * Z)
```

Expected Value:

```txt
EV = ProbabilityTP * Gain - ProbabilitySL * Loss
```

## Binance Screener

The dashboard includes a Binance public 24hr ticker screener using:

```txt
https://api4.binance.com/api/v3/ticker/24hr
```

The app fetches this through a local backend route:

```txt
/api/binance24hr
```

Clicking a USDT pair sends its latest price and estimated annualized volatility into the Monte Carlo engine.

## Disclaimer

This project is for educational and research purposes only. It is not financial advice, trading advice, or an investment recommendation.

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
```

## Run Development Server

```bash
npm run dev
```

Open:

```txt
http://localhost:3000
```

## Build

```bash
npm run build
```

## Start Production

```bash
npm run start
```

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

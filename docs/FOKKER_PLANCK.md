# Fokker–Planck Crypto Distribution & Microstructure Lab

Source of truth: `backend/fokker_planck.py` · UI route: `/fokker-planck` (`frontend/src/pages/FokkerPlanckPage.jsx`).

## Isolation guarantee

This module is **strictly additive**. It does not modify `backend/screening.py`, `backend/market_data.py`,
`backend/cryptorank.py`, `backend/calibration_lab.py`, or `backend/crypto_intelligence_v2.py`, and does not
alter any existing score, threshold, or verdict. All numerics use Python's standard library (`math`,
`statistics`) with zero extra pip dependencies for Vercel deployment.

## Governing equation & numerical scheme

State variable: log-return $x = \ln(S_t / S_0)$. Probability density $P(x,t)$ evolves via the 1D Itô
Fokker–Planck (Kolmogorov forward) equation:

$$\frac{\partial P(x,t)}{\partial t} = -\frac{\partial}{\partial x}\big[\mu(x,t) P(x,t)\big] + \frac{1}{2}\frac{\partial^2}{\partial x^2}\big[D(x,t) P(x,t)\big]$$

Rewritten in conservative flux form with zero-flux boundaries $J(-L, t) = J(+L, t) = 0$:

$$\frac{\partial P}{\partial t} = -\frac{\partial J}{\partial x}, \qquad J(x,t) = \Big[\mu(x,t) - \frac{1}{2}\frac{\partial D}{\partial x}\Big] P(x,t) - \frac{1}{2} D(x,t)\frac{\partial P}{\partial x}$$

Each interface flux is discretised with Chang–Cooper exponential fitting via the Bernoulli function
$B(w) = w / (e^w - 1)$ and advanced implicitly with an $O(N)$ Thomas tridiagonal solver. For any $\Delta t > 0$
and $D(x,t) > 0$, the scheme guarantees $P_i \ge 0$, exact mass conservation $\sum_i P_i \Delta x = 1$, and exact
stationary equilibrium for both Ornstein–Uhlenbeck and fat-tailed (Student-$t$) state-dependent diffusion.

## Modes & API routes

| Route | Purpose |
|---|---|
| `GET /api/fokker-planck/spec` | Formula metadata, zone definitions, and microstructure scenario presets |
| `GET /api/fokker-planck/universe` | Scans the existing `market_data` universe (`horizonDays`, `stableBand`, `source`) with optional CryptoRank metadata and side-by-side Meridian verdict |
| `GET /api/fokker-planck/asset/{symbol}` | Full 5-zone PDF curve, horizon timeline, parameter breakdown, and order-book/trade snapshot |
| `POST /api/fokker-planck/simulate` | Interactive simulator for order-book spoofing (`mode="orderbook"`) and direct $(\mu, D, \kappa, \eta, t)$ exploration (`mode="direct"`) |
| `POST /api/fokker-planck/backtest` | Chronological walk-forward validation (Brier score, skill score, 4-zone empirical coverage, 5-bin reliability) |

## Data sources reused

- **OHLCV universe & klines**: `backend/market_data.py` chain (`data-api.binance.vision` → `api.binance.us` → `api.binance.com` → `api.mexc.com` → `fapi.binance.com` → CoinGecko → labelled deterministic seed fallback).
- **Order-book & trade snapshots**: top-20 `/depth` + recent `/trades` from the same exchange host chain (plus consecutive-snapshot passive cancellation estimation), falling back to a labelled kline-derived proxy when unreachable.
- **Browser live stream**: optional `wss://stream.binance.com:9443/stream` (`@depth20@1000ms` + `@aggTrade` in `frontend/src/lib/liveOrderbook.js`, plus `@miniTicker` via existing `useLiveTickers`).
- **CryptoRank context**: optional `cryptorank.symbol_map` rank/market-cap attachment when `CRYPTORANK_API_KEY` is configured.

## Limitations

- The PDE solver is numerically exact up to discretisation, but mapping crypto features into $\mu(x,t)$ and $D(x,t)$ is heuristic — always inspect the walk-forward reliability table.
- Discontinuous jumps (protocol exploits, abrupt delistings, extreme liquidation cascades) can exceed continuous diffusion assumptions even with quadratic fat-tail diffusion.
- Research tool, not financial advice.

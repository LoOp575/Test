# LQ-Short Hunter — Product Requirements Document

## Original Problem Statement
> "Cek repo ini perbaiki dan percantik ,, perbaiki logika logika nya upgrade lebih baik"
> Repo: https://github.com/LoOp575/Test

## Project Summary
**LQ-Short Hunter v2** — Probabilistic crypto short engine combining pump-exhaustion detection, Monte Carlo (GBM) simulation and AI-narrative analysis for educational/research use. Re-platformed from Next.js to React + FastAPI to align with the deployment environment and unlock native LLM integration.

## Architecture
- **Frontend**: React 18 (CRA) + Tailwind v3 + Recharts + lucide-react + react-router-dom + react-markdown
- **Backend**: FastAPI (Python 3.11), numpy (vectorized Monte Carlo), httpx (multi-source market data), emergentintegrations
- **AI Cascade**: AIXCHIA API → Emergent LLM (Claude Sonnet 4.6) → rule-based fallback
- **Data Sources**: Binance Spot → Binance Futures → CoinGecko → local seed (multi-tier fallback)

## User Persona
Crypto trader / quant researcher who wants an automatic "is this pump exhausted?" reading without configuring TP/SL manually.

## Implemented Features (Jan 2026)
- **Dashboard / Screener**
  - Multi-tab token table (Short Candidates / Trending / Gainers / Losers / Volume / Volatility)
  - Search by symbol
  - Hot-score bar per row
  - Source + row-count indicator + warning banners
  - Premium Bloomberg-style dark theme
- **Token Analysis Page**
  - Auto market summary
  - Pump-exhaustion card with 5 sub-meters + auto TP/SL + RR
  - Score gauge (0–100) with animated arc + status badge with pulse dot
  - 10-metric quant grid (probabilities, EV, μ-adjusted, liquidity pressure, etc.)
  - AI Agent panel rendering markdown (tables, lists, code, blockquotes)
  - 60-bucket Monte Carlo distribution histogram with TP/SL/Current/Mean reference lines
- **Backend Logic Upgrades** vs. original JS
  - Vectorized numpy Monte Carlo (10–50× faster vs. for-loop JS)
  - Parkinson-style annualized volatility estimate
  - Calibrated scoring v3 with directional-edge term and tuned thresholds
  - Wick-rejection ratio added to exhaustion score
  - Robust clamping for micro-cap meme coins / scientific-notation prices
  - Four-tier market data fallback so the UI never goes empty
  - AI fallback chain (AIXCHIA → Emergent LLM → rules) — always produces output

## Prioritized Backlog
- **P0** — Premium UI polish: ✅ done
- **P0** — Logic upgrades (vol estimation, scoring, fallback): ✅ done
- **P0** — AI agent with Emergent LLM fallback: ✅ done
- **P1** — Symbol-not-found gentler UX (suggest closest match)
- **P1** — Persist last-viewed tokens / favorites (localStorage)
- **P1** — Liquidation-data integration (Coinglass / Hyperliquid public)
- **P2** — Multi-token "watch board" with auto-refresh
- **P2** — Share / export analysis as PNG
- **P2** — Param overrides (advanced mode)

## Integrations
- **Emergent LLM Key** (Claude Sonnet 4.6) — set in `/app/backend/.env`
- **AIXCHIA** (optional) — env keys: `AIXCHIA_API_KEY`, `AIXCHIA_API_URL`, `AIXCHIA_MODEL`

## Files of Reference
- `/app/backend/server.py` — FastAPI routes (`/api/health`, `/api/markets`, `/api/analyze/{sym}`, `/api/agent-analysis`)
- `/app/backend/monte_carlo.py` — Vectorized MC + pump exhaustion + auto TP/SL
- `/app/backend/agent.py` — AI cascade orchestrator
- `/app/frontend/src/pages/AnalyzePage.jsx` — one-shot analysis flow
- `/app/frontend/src/components/ScreenerPanel.jsx` — token screener
- `/app/frontend/src/components/ScoreGauge.jsx` — SVG circular gauge
- `/app/frontend/src/components/DistributionChart.jsx` — Recharts histogram
- `/app/frontend/src/components/AgentAnalysisPanel.jsx` — markdown renderer

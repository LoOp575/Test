# Screening model — formula mapping & notes

Source of truth for the math: `backend/screening.py` (tunable constants at the top of the file).
Annotated, user-facing version: the in-app **Methodology** page (`frontend/src/pages/MethodologyPage.jsx`).

## Why adaptation was needed

The reference slides implement two models for US equities:

1. **Deferred-revenue quality** — deferred revenue (cash collected, revenue not yet recognised) as a
   leading indicator of future revenue, cleaned of M&A/FX/billing distortions, quality-weighted by
   retention and cash conversion, turned into a long signal with VWAP confirmation.
2. **Filing instability** — amendment frequency (10-Q/A, 10-K/A, 8-K/A), material correction size,
   repeated topics and internal-control weakness → an instability score used to exclude or short names.

Crypto has neither audited deferred revenue nor amended filings. Every input was re-mapped to the
closest observable analogue; unavailable inputs are scored as `null`, never invented.

## Model 1 — demand quality

Notation: `t` = day; windows are 30 days (`cur` = last 30, `prev` = the 30 before, `prior` = the 30 before that).
`sat(x) = x/(1+|x|)`.

| Formula | Equity (slides) | Crypto (this repo) |
|---|---|---|
| `G^rev` | Revenue growth | Price return over 30d |
| `G^DR_reported` | Reported DR growth | Quote-volume (USD) growth |
| `G^org` | Organic DR growth (strip M&A, FX, billing) | Unit (base-asset) volume growth |
| `distortion` | G^DR − G^org | Reported minus organic = price/mix residual |
| `A^DR` | DR acceleration | Organic growth now vs prior window |
| `D^lead` | G^org − G^rev | Same (identical form) |
| `S^flow` (churn stability) | exp(−λ\|Δchurn\|) | exp(−3.0 · max(0, participation growthₚᵣₑᵥ − participation growth_now)) — penalises *steepening decay* of trade count only |
| `Q^ret` | α·NRR + β·GRR − γ·Churn | `0.5·(NRR*−1) + 0.8·GRR* − 1.1·Churn*`; NRR* = 1+G^org clamped 0–2; GRR* = share of buy-dominant days (taker ratio ≥ 0.5); Churn* = max(0, −participation growth) |
| `C^cash` | OCF / Revenue | Σ taker-buy base volume / Σ volume (30d) |
| `Q^DR` | `(G^org)₊ · S · (1+Q)₊ · (1+C)₊` | Same skeleton; `G^org` saturated via `sat()`; mapped to 0–100 via `100·x/(x+0.45)` |
| `R̂^DR` | DR · Ĉ^DR | `R̂ = (1 + G^org) × C^cash` (convertible demand multiple, display metric) |
| `GAP^DR` | `(R̂ − Revenue)/Revenue` | `GAP = D^lead / (1 + G^rev)` — under-recognition vs price |
| `S^DR` | Multiplicative score | Weighted geometric mean `sat(G^org)^0.4 · sat(A)^0.2 · sat(D)^0.2 · sat(GAP)^0.2` → 0–100 via `100·x/(x+0.12)` |
| `p^acc` | Classifier on x^DR | Hand-tuned sigmoid over bounded features (not calibrated) |

## Model 2 — instability flags

| Formula | Equity (slides) | Crypto (this repo) |
|---|---|---|
| `A` / event(t) | 1[amendment filed] | `1[\|r\| ≥ max(2.5σ₉₀d, 5.5%) OR \|OLS-detrended log-QV residual\| ≥ 2.5σ_resid]` |
| `N^amend`, `F^amend` | Rolling count / window | Abnormal-move days / 30 |
| `M^corr` → `Q^corr` | Σ weighted Δ line items | Mean \|r\| of event days ÷ mean \|r\| of all days, saturated |
| `R^topic` | Σ ωₖ·1[topic repeats] | Share of events within ±2d of another event |
| `I^IC` | 1[Material Weakness] + λ·1[Ineffective Controls] | **Unavailable** — scored as `null` |
| `S^file` | F · Q⁺ · (1+γR) · (1+ηI) | `(n_events/10) · (0.35 + 0.65·Q^corr) · (1 + 1.2·R^topic)` → 0–100 |
| `p^bad` | Pr(restatement \| x^rep) | `σ((S^file − 52)/12 + 0.5·R^topic)` — uncalibrated proxy |

## Verdict rules (override order)

1. **Low-quality growth → AVOID** — `G^rev > 5%` AND `participation growth < −10%` AND `C^cash < 0.47`
2. **Billing distortion → NO SIGNAL** — `G^reported > 5%` AND `G^org ≤ 0`
3. **Red-flag exclusion → AVOID** — `S^file ≥ 55` OR `p^bad ≥ 0.65`
4. **LONG** — `S^DR ≥ 55` AND `p^acc ≥ 0.60` AND `0 < G^org ≤ 0.90` AND `price > VWAP-20`
5. **WATCH** — `S^DR ≥ 38` AND `p^acc ≥ 0.50`
6. **Otherwise NEUTRAL**

Exit triggers (existing positions): organic growth < 0 · flow stability < 0.40 · participation exodus > 25%.

## Known limitations

- No backtest ships with the repo; thresholds are judgement defaults, not fitted parameters.
- p(accel) / p(adverse) are sigmoid blends for ranking, not calibrated probabilities.
- Spot OHLCV ignores derivatives, unlocks, treasury flows, exchange fragmentation and listing quality.
- Taker-buy aggressor flow ≠ intent (bots, hedges, spoofing-adjacent behaviour).
- Cross-window comparisons assume ≥ 61 daily candles; 90+ recommended.


## CryptoRank fundamentals layer (additive, optional)

CryptoRank does **not** feed the demand-quality / instability math above (it has no daily OHLCV/taker-buy on the Sandbox plan; OHLCV is Advanced-tier and intentionally unused). Instead, every screened row may carry a `cryptorank` block — `{rank, marketCap, circulatingSupply, volume24h, priceChangePercent24h, imageUrl}` — built from `/v3/currencies/list` (1 credit, cached 300s). Consumers may use it as a universe-quality filter (e.g. exclude very low caps), for display, or as sizing input — the LONG/WATCH math in this document is unchanged. If the key is absent or the call fails, rows carry `cryptorank: null` and nothing else changes.

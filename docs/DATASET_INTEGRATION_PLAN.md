# LQ-Short Hunter Dataset Integration Plan

## Objective
Improve Monte Carlo short-entry analysis by adding sentiment and market exhaustion features.

## Phase 1 (Recommended)

### Dataset A
Instrumetriq/crypto-market-sentiment-observations

Purpose:
- Social sentiment
- Crypto market activity
- Time-series sentiment context

Output Feature:
- SentimentExtreme
- CrowdEuphoria

Weight:
10%

---

### Dataset B
smilegeng/bitcoin_price_timeseries

Purpose:
- OHLCV validation
- Pump/Dump historical study
- Monte Carlo calibration

Output Feature:
- PumpVelocity
- VolumeDivergence
- VolatilityHeat

Weight:
15%

---

### Dataset C
zeroshot/twitter-financial-news-sentiment

Purpose:
- Financial news sentiment
- Market narrative detection

Output Feature:
- NewsSentiment
- NarrativeStrength

Weight:
10%

---

## New Exhaustion Engine

ExhaustionScore =
0.30 * FundingHeat +
0.25 * OIAcceleration +
0.20 * LiquidationImbalance +
0.15 * VolumeDivergence +
0.10 * SentimentExtreme

Range:
0-100

Interpretation:
0-30 = Healthy Trend
30-60 = Watch Zone
60-80 = Overheated
80-100 = Exhaustion Risk

---

## Monte Carlo Integration

muAdjusted = mu + lambda * LiquidityPressure - gamma * ExhaustionScore

Effect:
High ExhaustionScore reduces bullish drift and increases reversal probability.

---

## Future Phase 2

- Funding Rate Dataset
- Open Interest Dataset
- Liquidation Heatmap Dataset
- Exchange Flow Dataset

These datasets should have higher priority than adding more NLP datasets.

---

## Expected Output

Liquidity Pressure
Exhaustion Score
Pump Failure Probability
Short Entry Probability
Take Profit Probability
Expected Value

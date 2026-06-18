# Sentiment Exhaustion Engine

## Purpose
This layer lets LQ-Short Hunter accept future Hugging Face or external sentiment dataset features without changing the core Monte Carlo formula every time.

It is designed for pump exhaustion detection, not price prediction.

---

## Accepted Feature Inputs

The backend Monte Carlo engine now supports these optional normalized inputs:

| Field | Meaning | Range |
|---|---|---|
| `sentimentExtreme` | How extreme bullish/euphoric sentiment is | `0..1` or `0..100` |
| `crowdEuphoria` | Crowd/social hype intensity | `0..1` or `0..100` |
| `newsSentiment` | Positive financial/news sentiment intensity | `0..1` or `0..100` |
| `narrativeStrength` | Strength of bullish market narrative | `0..1` or `0..100` |
| `socialActivityScore` | Social activity / attention spike | `0..1` or `0..100` |

The engine normalizes these values safely.

---

## Sentiment Exhaustion Formula

```txt
SentimentExhaustionScore =
0.35 * sentimentExtreme +
0.25 * crowdEuphoria +
0.18 * newsSentiment +
0.12 * narrativeStrength +
0.10 * socialActivityScore
```

Interpretation:

```txt
0.00 - 0.42 = SENTIMENT_NORMAL
0.42 - 0.62 = SENTIMENT_WATCH
0.62 - 0.80 = EUPHORIA_HIGH
0.80 - 1.00 = EUPHORIA_EXTREME
```

---

## Integration with Pump Exhaustion

The existing pump exhaustion formula now includes the sentiment layer:

```txt
ExhaustionScore =
0.22 * PumpStrength +
0.16 * HighPressure +
0.16 * VolatilityStrength +
0.13 * RejectionScore +
0.16 * FuelDecayScore +
0.07 * VolumeStrength +
0.10 * SentimentExhaustionScore
```

This keeps candle/volume behavior as the main driver while adding sentiment as confirmation.

---

## Integration with Monte Carlo Drift

The Monte Carlo drift now receives an additional negative bias when sentiment is euphoric:

```txt
ExhaustionReversalBias =
-0.04 * ExhaustionScore
-0.035 * FuelDecayScore
-0.025 * SentimentExhaustionScore
```

Then:

```txt
muAdjusted = mu + lambda * LiquidityPressure + ExhaustionReversalBias
```

Meaning:
- Strong liquidity can still push price upward.
- But extreme sentiment/euphoria reduces bullish drift.
- This increases the probability of pump failure/reversal in the simulation.

---

## Recommended Hugging Face Dataset Mapping

| Dataset | Engine Field |
|---|---|
| `Instrumetriq/crypto-market-sentiment-observations` | `sentimentExtreme`, `crowdEuphoria`, `socialActivityScore` |
| `zeroshot/twitter-financial-news-sentiment` | `newsSentiment`, `narrativeStrength` |
| `TimKoornstra/financial-tweets-sentiment` | `crowdEuphoria`, `sentimentExtreme` |
| `cogneolabs/Cogneo-Crypto-Sentiment` | `sentimentExtreme`, `newsSentiment` |

---

## Example Input

```json
{
  "currentPrice": 100,
  "takeProfit": 94,
  "stopLoss": 104,
  "spotFlow": 0.2,
  "oiFlow": 0.1,
  "sentimentExtreme": 0.9,
  "crowdEuphoria": 0.8,
  "newsSentiment": 0.7,
  "narrativeStrength": 0.75,
  "socialActivityScore": 0.85
}
```

---

## Design Rule
Sentiment must not become the main signal. It is only a confirmation layer for detecting overheated pumps.

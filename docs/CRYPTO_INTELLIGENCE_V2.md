# Crypto Intelligence v2: formula specification

## Status and scope

This is an isolated, additive research implementation in
`backend/crypto_intelligence_v2.py`. It is not imported by
`backend/screening.py`, is not connected to API routes, and does not change
production scores or LONG/WATCH/AVOID decisions.

The engine measures exchange-traded activity. It does not directly measure
wallet holders, on-chain adoption, project fundamentals, whale identities, or
future returns.

## Definitions

All comparison windows contain 30 observed daily candles. If timestamps have
gaps, the result includes a warning. The windows must not be described as
complete calendar months in that case.

For each window:
- `B`: summed base-asset volume.
- `Q`: summed quote-asset volume.
- `T`: summed trade count.
- `TB`: summed taker-buy base volume.

### 1. Base and quote activity growth

- `baseVolumeGrowth30 = B_current / B_previous - 1`
- `quoteVolumeGrowth30 = Q_current / Q_previous - 1`

These are exchange-volume changes, not organic user or adoption growth.
If the previous window has zero volume, growth is unavailable (`null`), not
silently set to zero.

### 2. Effective traded-price change

- `effectivePrice = Q / B`
- `effectivePriceGrowth30 = effectivePrice_current / effectivePrice_previous - 1`
- Equivalently, when both growth rates are defined:
  `(1 + quoteVolumeGrowth30) / (1 + baseVolumeGrowth30) - 1`.

This is a volume-weighted effective traded-price proxy. It is not the same as
a candle close-to-close return and can be affected by venue and pair mix.

### 3. Aggressive buy-flow proxy

- `buyShare30 = TB_current / B_current`
- `buyImbalance30 = 2 * buyShare30 - 1`

The imbalance is centered at zero: negative indicates lower taker-buy share,
zero is balanced, and positive indicates higher taker-buy share. It is not
proof of net investor accumulation. Exchange aggressor data can include
arbitrage, hedging, bots, and venue-specific effects.

### 4. Buy-pressure persistence

- `persistence = count(days where daily taker-buy share > 0.5) / count(days with positive base volume)`.

Days with zero base volume are excluded from the denominator. If no valid days
exist, persistence is unavailable.

### 5. Activity acceleration

- `previousGrowth = B_previous / B_prior - 1`
- `acceleration = baseVolumeGrowth30 - previousGrowth`

This requires three complete 30-observation windows (90 candles). For 61-89
candles, current and previous window growth are available but acceleration is
`null`; the model does not invent a zero prior growth.

### 6. Volatility and abnormal-return count

Descriptive volatility is the population standard deviation of the latest
up-to-90 observed daily returns. It is withheld when calendar gaps exist,
because a multi-day return must not be silently treated as a one-day return.

The abnormal-return count uses a separate baseline:
- Estimate standard deviation from the 90 returns immediately BEFORE the
  target 30-return period.
- Threshold = `max(2.5 * baseline_stddev, 5.5%)`.
- Count target-period returns whose absolute value exceeds that threshold.

At least 120 daily returns (normally 121 candles) are required for this
baseline-and-target comparison. This is a descriptive risk feature, not a
calibrated manipulation probability.

### 7. Robust volume-shock proxy

When at least 120 candles exist, each of the latest 30 quote-volume observations
is compared with the preceding up-to-90 observations, excluding the day being
tested. The threshold uses median and median absolute deviation:

- `robust_sigma = 1.4826 * MAD`
- `threshold = median + 3.5 * robust_sigma`

A day is counted only when its quote volume exceeds both the threshold and the
baseline median. This is a screening feature, not proof of manipulation.

## Data validation

The module rejects:
- fewer than 61 candles;
- missing, non-finite, or invalid numeric fields;
- non-positive OHLC prices;
- negative activity;
- positive base volume with missing/zero quote volume;
- taker-buy base volume greater than total base volume;
- non-chronological or duplicate candle timestamps;
- inconsistent high/low bounds.

Calendar gaps are surfaced as warnings. Volatility-based abnormal-return
features are withheld when gaps make daily returns ambiguous. Growth windows
remain observation-based and are explicitly labelled as such.

## Output contract

The module returns raw features and data-quality metadata. It intentionally
does not calculate a composite quality score, a probability, or a trading
verdict. Those require separate target definitions and out-of-sample
calibration.

## Integration gate

Before production integration:
1. Run the new unit tests in the repository environment.
2. Compare v1 and v2 on the same real, source-labelled candle data.
3. Confirm spot and futures inputs are never mixed without explicit source
   metadata and separate model treatment.
4. Add chronological walk-forward evaluation with purging/embargo for
   overlapping forward labels.
5. Define separate labels for future price return, BTC-relative return,
   2x outcome, and downside risk.
6. Evaluate calibration, precision/recall, drawdown, fees, slippage, and
   survivorship/delisting bias.
7. Only then consider a reviewed integration into production.

No existing production formula is replaced by this research module.

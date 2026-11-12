# Crypto Intelligence v2: formula specification

## Status and scope

This is an isolated, additive research implementation in
`backend/crypto_intelligence_v2.py`. It is intentionally not imported by
`backend/screening.py`, not connected to API routes, and does not change
production scores or LONG/WATCH/AVOID decisions.

The engine measures exchange-traded activity. It does not directly measure
wallet holders, on-chain adoption, project fundamentals, whale identities, or
future returns.

## Definitions

All windows are 30 observed daily candles. If candle timestamps have gaps, the
result includes a warning; the windows are not silently described as complete
calendar months.

For each window:

- (B): summed base-asset volume.
- (Q): summed quote-asset volume.
- (T): summed trade count.
- (TB): summed taker-buy base volume.

### 1. Base and quote activity growth

[
G_B = rac{B_{cur}}{B_{prev}} - 1
]

[
G_Q = rac{Q_{cur}}{Q_{prev}} - 1
]

These are exchange volume changes, not organic user or adoption growth.
If the previous window has zero volume, growth is unavailable (`null`), not
silently set to zero.

### 2. Effective traded-price change

[
P_{eff} = rac{Q}{B}
]

[
G_{P,eff} =
rac{P_{eff,cur}}{P_{eff,prev}} - 1
=
rac{1+G_Q}{1+G_B}-1
]

This is a volume-weighted effective traded price proxy. It is not the same as
a candle close-to-close return and can be affected by venue and pair mix.

### 3. Aggressive buy-flow proxy

[
BuyShare = rac{TB_{cur}}{B_{cur}}
]

[
BuyImbalance = 2 	imes BuyShare - 1
]

The imbalance is centered at zero: negative indicates lower taker-buy share,
zero is balanced, and positive indicates higher taker-buy share. It is not
proof of net investor accumulation; exchange aggressor data can include
arbitrage, hedging, bots, and venue-specific effects.

### 4. Buy-pressure persistence

[
Persistence =
rac{#{d: BuyShare_d > 0.5}}
{#{d: B_d > 0}}
]

Days with zero base volume are excluded from the denominator. If no valid days
exist, persistence is unavailable.

### 5. Activity acceleration

[
A_B = G_{B,cur} - G_{B,prev}
]

This requires three complete 30-observation windows (90 candles). For 61-89
candles, current and previous window growth are available but acceleration is
`null`; the model does not invent a zero prior growth.

### 6. Volatility and abnormal-return count

The module computes population standard deviation over the latest available
up-to-90 daily returns, requiring at least 30 returns:

[
sigma_{90} = StdDev(r_{t-k+1},...,r_t)
]

[
Threshold = max(2.5sigma_{90}, 5.5%)
]

The count is the number of absolute daily returns in the latest 30 observations
that exceed the threshold. It is a descriptive risk feature, not a calibrated
manipulation probability.

### 7. Robust volume-shock proxy

When at least 120 candles exist, each of the latest 30 quote-volume observations
is compared with the preceding up-to-90 observations, excluding the day being
tested. The threshold uses median and median absolute deviation:

[
RobustSigma = 1.4826 	imes MAD
]

[
Threshold = Median + 3.5 	imes RobustSigma
]

A day is counted only when its quote volume exceeds both the threshold and the
baseline median. This is a screening feature, not proof of manipulation.

## Data validation

The module rejects:
- fewer than 61 candles;
- missing, non-finite, or invalid numeric fields;
- non-positive prices;
- negative activity;
- taker-buy base volume greater than total base volume;
- non-chronological or duplicate candle timestamps;
- inconsistent high/low bounds.

Calendar gaps are surfaced as warnings. The calculations use observed-candle
windows, so a gap must not be mistaken for a fully observed 30-day interval.

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

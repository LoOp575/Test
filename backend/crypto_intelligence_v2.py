"""Read-only v2 crypto market-activity features.

This module deliberately does not emit LONG/SHORT decisions or calibrated
probabilities. It measures exchange-traded activity proxies only. It does not
claim to measure users, holders, on-chain adoption, or whale accumulation.
"""
from __future__ import annotations

from math import isfinite
from statistics import median, pstdev
from typing import Any

WINDOW = 30
MIN_CANDLES = 61
FULL_HISTORY_CANDLES = 90
EPS = 1e-12
DAY_MS = 86_400_000


class DataQualityError(ValueError):
    """Raised when candle data cannot be safely interpreted."""


def _number(candle: dict[str, Any], key: str) -> float:
    try:
        value = float(candle[key])
    except (KeyError, TypeError, ValueError) as exc:
        raise DataQualityError(f"missing_or_invalid_{key}") from exc
    if not isfinite(value):
        raise DataQualityError(f"non_finite_{key}")
    return value


def _validate(candles: list[dict[str, Any]]) -> tuple[list[dict[str, float]], list[str]]:
    if not isinstance(candles, list) or len(candles) < MIN_CANDLES:
        raise DataQualityError(f"need_at_least_{MIN_CANDLES}_daily_candles")
    clean: list[dict[str, float]] = []
    warnings: list[str] = []
    previous_date: float | None = None
    for index, candle in enumerate(candles):
        if not isinstance(candle, dict):
            raise DataQualityError(f"invalid_candle_{index}")
        row = {
            "date": _number(candle, "date"),
            "open": _number(candle, "open"),
            "high": _number(candle, "high"),
            "low": _number(candle, "low"),
            "close": _number(candle, "close"),
            "volume": _number(candle, "volume"),
            "quoteVolume": _number(candle, "quoteVolume"),
            "trades": _number(candle, "trades"),
            "takerBuyBase": _number(candle, "takerBuyBase"),
        }
        if min(row["open"], row["high"], row["low"], row["close"]) <= 0:
            raise DataQualityError(f"non_positive_price_{index}")
        if row["volume"] < 0 or row["quoteVolume"] < 0 or row["trades"] < 0:
            raise DataQualityError(f"negative_activity_{index}")
        if row["takerBuyBase"] < 0 or row["takerBuyBase"] > row["volume"] + EPS:
            raise DataQualityError(f"invalid_taker_buy_volume_{index}")
        if row["volume"] > EPS and row["quoteVolume"] <= EPS:
            raise DataQualityError(f"missing_quote_volume_{index}")
        if row["high"] < max(row["open"], row["close"], row["low"]):
            raise DataQualityError(f"invalid_high_{index}")
        if row["low"] > min(row["open"], row["close"]):
            raise DataQualityError(f"invalid_low_{index}")
        if previous_date is not None:
            delta = row["date"] - previous_date
            if delta <= 0:
                raise DataQualityError("candles_not_strictly_chronological")
            if delta > 1.5 * DAY_MS:
                warnings.append("calendar_gaps_present_windows_are_observation_based")
        previous_date = row["date"]
        clean.append(row)
    return clean, sorted(set(warnings))


def _sum(rows: list[dict[str, float]], key: str) -> float:
    return sum(row[key] for row in rows)


def _growth(current: float, previous: float) -> float | None:
    if previous <= EPS:
        return None
    return current / previous - 1.0


def _safe_ratio(numerator: float, denominator: float) -> float | None:
    if denominator <= EPS:
        return None
    return numerator / denominator


def _returns(closes: list[float]) -> list[float]:
    return [closes[i] / closes[i - 1] - 1.0 for i in range(1, len(closes))]


def _median_mad(values: list[float]) -> tuple[float, float]:
    center = median(values)
    mad = median([abs(value - center) for value in values])
    return center, mad


def compute_v2_features(candles: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute transparent activity, buy-pressure, and risk features.

    Windows are 30 observed candles, not guaranteed calendar days when gaps
    exist. 61-89 candles return partial metrics; acceleration requires 90.
    """
    rows, warnings = _validate(candles)
    n = len(rows)
    cur = rows[-WINDOW:]
    prev = rows[-2 * WINDOW:-WINDOW]
    prior = rows[-3 * WINDOW:-2 * WINDOW] if n >= FULL_HISTORY_CANDLES else None

    cur_base = _sum(cur, "volume")
    prev_base = _sum(prev, "volume")
    cur_quote = _sum(cur, "quoteVolume")
    prev_quote = _sum(prev, "quoteVolume")
    base_growth = _growth(cur_base, prev_base)
    quote_growth = _growth(cur_quote, prev_quote)

    cur_effective_price = _safe_ratio(cur_quote, cur_base)
    prev_effective_price = _safe_ratio(prev_quote, prev_base)
    effective_price_growth = (
        cur_effective_price / prev_effective_price - 1.0
        if cur_effective_price is not None and prev_effective_price is not None
        and prev_effective_price > EPS else None
    )

    buy_share = _safe_ratio(_sum(cur, "takerBuyBase"), cur_base)
    buy_imbalance = 2.0 * buy_share - 1.0 if buy_share is not None else None
    daily_buy_shares = [
        _safe_ratio(row["takerBuyBase"], row["volume"]) for row in cur
        if row["volume"] > EPS
    ]
    persistence = (
        sum(1 for share in daily_buy_shares if share is not None and share > 0.5)
        / len(daily_buy_shares)
        if daily_buy_shares else None
    )

    cur_trades = _sum(cur, "trades")
    prev_trades = _sum(prev, "trades")
    trade_growth = _growth(cur_trades, prev_trades)

    acceleration = None
    prior_base_growth = None
    if prior is not None:
        prior_base = _sum(prior, "volume")
        prior_base_growth = _growth(prev_base, prior_base)
        if base_growth is not None and prior_base_growth is not None:
            acceleration = base_growth - prior_base_growth

    closes = [row["close"] for row in rows]
    daily_returns = _returns(closes)
    has_calendar_gaps = bool(warnings)

    # Descriptive volatility over the latest up-to-90 observed returns.
    # It is withheld when candle gaps make daily returns ambiguous.
    volatility_returns = daily_returns[-90:]
    volatility_90 = (
        pstdev(volatility_returns)
        if len(volatility_returns) >= 30 and not has_calendar_gaps else None
    )

    # For abnormal-return counting, estimate the threshold from the 90 returns
    # BEFORE the target 30-return period. This avoids using the events being
    # classified to inflate their own volatility threshold.
    abnormal_return_days = None
    if len(daily_returns) >= 120 and not has_calendar_gaps:
        baseline_returns = daily_returns[-120:-30]
        baseline_volatility = pstdev(baseline_returns)
        threshold = max(2.5 * baseline_volatility, 0.055)
        abnormal_return_days = sum(
            1 for value in daily_returns[-WINDOW:] if abs(value) >= threshold
        )

    # Each recent volume observation is compared with up to 90 prior
    # observations, excluding the observation being tested.
    volume_shock_days = None
    if n >= 120:
        shocks = 0
        for idx in range(n - WINDOW, n):
            baseline = [r["quoteVolume"] for r in rows[max(0, idx - 90):idx]]
            if len(baseline) < 30:
                continue
            center, mad = _median_mad(baseline)
            robust_sigma = 1.4826 * mad
            threshold = center + 3.5 * robust_sigma
            if rows[idx]["quoteVolume"] > threshold and rows[idx]["quoteVolume"] > center:
                shocks += 1
        volume_shock_days = shocks

    return {
        "ok": True,
        "model": "crypto-intelligence-v2-activity",
        "dataQuality": {
            "candles": n,
            "history": "full" if prior is not None else "partial",
            "windowBasis": "30_observations",
            "warnings": warnings,
            "probabilitiesCalibrated": False,
        },
        "features": {
            "baseVolumeGrowth30": base_growth,
            "quoteVolumeGrowth30": quote_growth,
            "effectivePriceGrowth30": effective_price_growth,
            "effectivePrice30": cur_effective_price,
            "buyShare30": buy_share,
            "buyImbalance30": buy_imbalance,
            "buyPressurePersistence30": persistence,
            "tradeCountGrowth30": trade_growth,
            "baseVolumeGrowthPrevious30": prior_base_growth,
            "baseVolumeAcceleration": acceleration,
            "volatility90": volatility_90,
            "abnormalReturnDays30": abnormal_return_days,
            "volumeShockDays30": volume_shock_days,
        },
        "interpretation": {
            "activityGrowth": (
                "positive" if base_growth is not None and base_growth > 0
                else "negative_or_unavailable"
            ),
            "buyPressure": (
                "buyer_aggressive_flow" if buy_imbalance is not None and buy_imbalance > 0
                else "seller_or_balanced_flow"
            ),
            "accumulationClaim": False,
            "note": (
                "Exchange taker-buy flow is an activity proxy. It does not prove "
                "whale accumulation, new holders, on-chain adoption, or future returns."
            ),
        },
    }

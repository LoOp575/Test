"""Runtime patches for richer market fuel metrics on Vercel."""

from __future__ import annotations

import math
from typing import Any

import httpx

import backend.server as server
import backend.monte_carlo as mc
import backend.agent as agent


def _to_float(value: Any, fallback: float = 0.0) -> float:
    try:
        number = float(value)
        if math.isfinite(number):
            return number
    except Exception:
        pass
    return fallback


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


def _mean(values: list[float]) -> float:
    return sum(values) / len(values) if values else 0.0


def _std(values: list[float]) -> float:
    if not values:
        return 0.0
    mu = _mean(values)
    return math.sqrt(sum((v - mu) ** 2 for v in values) / max(len(values), 1))


def _symbol_variants(symbol: str) -> tuple[str, str]:
    sym = symbol.upper().strip()
    base = sym[:-4] if sym.endswith("USDT") else sym
    return f"{base}USDT", f"{base}-USDT"


def _base_symbol(symbol: str) -> str:
    sym = symbol.upper().strip()
    return sym[:-4] if sym.endswith("USDT") else sym


def _safe_sigmoid(value: float) -> float:
    try:
        if value >= 0:
            z = math.exp(-value)
            return 1 / (1 + z)
        z = math.exp(value)
        return z / (1 + z)
    except Exception:
        return 0.5


def _quality_profile(*, has_kline: bool, has_taker: bool, has_market_cap: bool, has_math: bool = False) -> dict[str, Any]:
    volume_quality = "REAL" if has_kline else "ESTIMATED"
    buyer_quality = "REAL" if has_taker else ("PROXY" if has_kline else "ESTIMATED")
    market_cap_quality = "REAL" if has_market_cap else "MISSING"

    score = 0
    score += 30 if has_kline else 8
    score += 25 if has_taker else (12 if has_kline else 6)
    score += 20 if has_market_cap else 0
    score += 15 if has_math else (8 if has_kline else 0)
    score += 0
    score += 0

    if score >= 85:
        overall = "REAL"
    elif score >= 55:
        overall = "PARTIAL"
    elif score >= 25:
        overall = "ESTIMATED"
    else:
        overall = "LOW_CONFIDENCE"

    return {
        "overall": overall,
        "confidencePercent": round(score, 2),
        "volumeTrend3h": volume_quality,
        "buyerPressure3h": buyer_quality,
        "marketCap": market_cap_quality,
        "mathScoring": "REAL" if has_math else ("PARTIAL" if has_kline else "ESTIMATED"),
        "orderBookDepth": "MISSING",
        "derivatives": "MISSING",
    }


def _with_quality(market: dict, *, has_kline: bool, has_taker: bool, has_market_cap: bool, has_math: bool = False) -> dict:
    quality = _quality_profile(has_kline=has_kline, has_taker=has_taker, has_market_cap=has_market_cap, has_math=has_math)
    return {
        **market,
        "dataQuality": quality,
        "dataConfidencePercent": quality["confidencePercent"],
        "dataQualityOverall": quality["overall"],
    }


def _parse_kline(row: Any) -> dict[str, float] | None:
    if not isinstance(row, list) or len(row) < 6:
        return None
    ts = _to_float(row[0])
    open_price = _to_float(row[1])
    high = _to_float(row[2])
    low = _to_float(row[3])
    close = _to_float(row[4])
    base_volume = _to_float(row[5])
    quote_volume = _to_float(row[7] if len(row) > 7 else 0)
    taker_buy_quote_volume = _to_float(row[10] if len(row) > 10 else 0)
    if quote_volume <= 0 and close > 0:
        quote_volume = base_volume * close
    if high <= 0 or low <= 0 or close <= 0:
        return None
    candle_range = max(high - low, 1e-12)
    return {
        "timestamp": ts,
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "quoteVolume": quote_volume,
        "takerBuyQuoteVolume": max(0.0, min(taker_buy_quote_volume, quote_volume)) if quote_volume > 0 else 0.0,
        "upperWickRatio": _clamp((high - max(open_price, close)) / candle_range),
        "closeStrength": _clamp((close - low) / candle_range),
    }


def _extract_kline_rows(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if isinstance(data, list):
        return data
    result = payload.get("result")
    if isinstance(result, dict) and isinstance(result.get("list"), list):
        return result["list"]
    if isinstance(result, list):
        return result
    return []


def _historical_stats_from_closes(closes: list[float], interval: str = "1h") -> dict[str, Any]:
    returns: list[float] = []
    for prev, cur in zip(closes, closes[1:]):
        if prev > 0 and cur > 0:
            returns.append(math.log(cur / prev))
    if len(returns) < 2:
        return {"historicalReturns": returns, "historicalReturnInterval": interval}
    mean_return = _mean(returns)
    sigma = _std(returns)
    scale = math.sqrt(24.0) if interval == "1h" else 1.0
    mean_scale = 24.0 if interval == "1h" else 1.0
    return {
        "historicalReturns": [round(r, 8) for r in returns[-24:]],
        "historicalReturnInterval": interval,
        "historicalVolDaily": round(sigma * scale, 8),
        "historicalMeanDaily": round(mean_return * mean_scale, 8),
    }


def _normal_pdf(x: float, mu: float, sigma: float) -> float:
    sigma = max(abs(sigma), 1e-12)
    return (1.0 / (sigma * math.sqrt(2.0 * math.pi))) * math.exp(-((x - mu) ** 2) / (2.0 * sigma ** 2))


def _entropy_from_states(states: list[str]) -> float:
    if not states:
        return 0.0
    counts: dict[str, int] = {}
    for state in states:
        counts[state] = counts.get(state, 0) + 1
    total = len(states)
    raw = 0.0
    for count in counts.values():
        p = count / total
        raw -= p * math.log(max(p, 1e-12))
    max_entropy = math.log(max(len(counts), 1))
    return _clamp(raw / max_entropy) if max_entropy > 0 else 0.0


def _classify_step(ret: float, volume_velocity: float, close_strength: float, rejection: float) -> str:
    if ret > 0.015 and volume_velocity >= 0 and close_strength >= 0.55 and rejection < 0.35:
        return "FUEL_STRONG"
    if ret > 0 and close_strength >= 0.50:
        return "PUMPING"
    if ret > 0 and (volume_velocity < 0 or rejection >= 0.35):
        return "FUEL_WEAKENING"
    if ret <= 0 and rejection >= 0.45:
        return "EXHAUSTION"
    if ret < -0.012:
        return "REVERSAL"
    return "NEUTRAL"


def _markov_next_probabilities(states: list[str]) -> dict[str, Any]:
    labels = ["FUEL_STRONG", "PUMPING", "FUEL_WEAKENING", "EXHAUSTION", "REVERSAL", "NEUTRAL"]
    if not states:
        return {"currentState": "UNKNOWN", "next": {label: 0.0 for label in labels}}

    current = states[-1]
    counts = {label: 1 for label in labels}
    for a, b in zip(states, states[1:]):
        if a == current and b in counts:
            counts[b] += 1
    total = sum(counts.values())
    return {
        "currentState": current,
        "next": {label: round(count / total, 4) for label, count in counts.items()},
    }


def _fourier_dominant_cycle(values: list[float]) -> dict[str, float]:
    n = len(values)
    if n < 8:
        return {"dominantCyclePeriod": 0.0, "dominantAmplitude": 0.0, "cycleExhaustionScore": 0.0}
    mu = _mean(values)
    centered = [v - mu for v in values]
    best_k = 0
    best_amp = 0.0
    for k in range(1, max(2, n // 2)):
        real = 0.0
        imag = 0.0
        for idx, val in enumerate(centered):
            angle = -2.0 * math.pi * k * idx / n
            real += val * math.cos(angle)
            imag += val * math.sin(angle)
        amp = math.sqrt(real * real + imag * imag) / n
        if amp > best_amp:
            best_amp = amp
            best_k = k
    period = (n / best_k) if best_k else 0.0
    recent_mean = _mean(values[-3:])
    prev_mean = _mean(values[-6:-3]) if n >= 6 else _mean(values[:-3])
    cycle_exhaustion = _clamp((prev_mean - recent_mean) / (abs(best_amp) + 1e-12)) if best_amp > 0 else 0.0
    return {
        "dominantCyclePeriod": round(period, 4),
        "dominantAmplitude": round(best_amp, 8),
        "cycleExhaustionScore": round(cycle_exhaustion, 4),
    }


def _bayesian_exhaustion_probability(signals: dict[str, float]) -> float:
    prior = 0.32
    odds = prior / (1.0 - prior)
    likelihoods = [
        2.2 if signals.get("negativeAcceleration", 0) > 0.55 else 0.85,
        1.8 if signals.get("negativeVolumeAcceleration", 0) > 0.50 else 0.95,
        1.7 if signals.get("zExtreme", 0) > 0.55 else 0.90,
        2.0 if signals.get("rejection", 0) > 0.45 else 0.82,
        1.6 if signals.get("buyerWeakness", 0) > 0.50 else 0.95,
    ]
    for likelihood in likelihoods:
        odds *= likelihood
    return _clamp(odds / (1.0 + odds))


def _math_features_from_candles(market: dict, candles: list[dict[str, float]], source: str) -> dict[str, Any]:
    if len(candles) < 6:
        return {}
    closes = [c["close"] for c in candles if c.get("close", 0) > 0]
    qvols = [max(c.get("quoteVolume", 0), 0) for c in candles]
    close_strengths = [c.get("closeStrength", 0.5) for c in candles]
    rejections = [_clamp(0.65 * c.get("upperWickRatio", 0.0) + 0.35 * (1.0 - c.get("closeStrength", 0.5))) for c in candles]
    if len(closes) < 6:
        return {}

    derivatives = [(cur - prev) for prev, cur in zip(closes, closes[1:])]
    velocity_now = derivatives[-1] if derivatives else 0.0
    velocity_prev = derivatives[-2] if len(derivatives) >= 2 else 0.0
    acceleration_now = velocity_now - velocity_prev

    log_returns = [math.log(cur / prev) for prev, cur in zip(closes, closes[1:]) if prev > 0 and cur > 0]
    log_return_now = log_returns[-1] if log_returns else 0.0
    log_return_prev = log_returns[-2] if len(log_returns) >= 2 else 0.0
    log_acceleration = log_return_now - log_return_prev
    mu = _mean(log_returns)
    sigma = _std(log_returns)
    z_score = (log_return_now - mu) / sigma if sigma > 1e-12 else 0.0
    pdf = _normal_pdf(log_return_now, mu, sigma if sigma > 1e-12 else 1e-6)
    pdf_at_mean = _normal_pdf(mu, mu, sigma if sigma > 1e-12 else 1e-6)
    tail_score = _clamp(1.0 - (pdf / max(pdf_at_mean, 1e-12)))

    volume_velocities = []
    for prev, cur in zip(qvols, qvols[1:]):
        volume_velocities.append((cur - prev) / prev if prev > 0 else 0.0)
    volume_velocity_now = volume_velocities[-1] if volume_velocities else 0.0
    volume_velocity_prev = volume_velocities[-2] if len(volume_velocities) >= 2 else 0.0
    volume_acceleration = volume_velocity_now - volume_velocity_prev

    states = []
    for idx, ret in enumerate(log_returns):
        q_idx = min(idx + 1, len(qvols) - 1)
        vvel = volume_velocities[idx] if idx < len(volume_velocities) else 0.0
        close_strength = close_strengths[q_idx] if q_idx < len(close_strengths) else 0.5
        rejection = rejections[q_idx] if q_idx < len(rejections) else 0.0
        states.append(_classify_step(ret, vvel, close_strength, rejection))

    entropy = _entropy_from_states(states)
    markov = _markov_next_probabilities(states)
    fourier = _fourier_dominant_cycle(log_returns[-24:])
    recent_buyer_values = [
        _clamp(c.get("takerBuyQuoteVolume", 0.0) / c.get("quoteVolume", 1.0))
        for c in candles[-3:]
        if c.get("quoteVolume", 0.0) > 0 and c.get("takerBuyQuoteVolume", 0.0) > 0
    ]
    recent_buyer = _mean(recent_buyer_values)
    if recent_buyer <= 0:
        recent_buyer = _mean(close_strengths[-3:])

    negative_acceleration_score = _clamp(-log_acceleration / max(sigma, 1e-6))
    negative_volume_acceleration_score = _clamp(-volume_acceleration)
    z_extreme_score = _clamp(abs(z_score) / 3.0)
    rejection_score = _mean(rejections[-3:])
    buyer_weakness = _clamp(1.0 - recent_buyer)
    bayes = _bayesian_exhaustion_probability({
        "negativeAcceleration": negative_acceleration_score,
        "negativeVolumeAcceleration": negative_volume_acceleration_score,
        "zExtreme": z_extreme_score,
        "rejection": rejection_score,
        "buyerWeakness": buyer_weakness,
    })

    drift = mu - 0.5 * sigma * sigma
    denom = max(sigma, 1e-6)
    brownian_prob_up = _safe_sigmoid((drift + log_return_now) / denom)
    brownian_prob_down = _clamp(1.0 - brownian_prob_up)

    vec = [
        negative_acceleration_score,
        negative_volume_acceleration_score,
        z_extreme_score,
        tail_score,
        entropy,
        rejection_score,
        buyer_weakness,
        bayes,
        markov["next"].get("EXHAUSTION", 0.0) + markov["next"].get("REVERSAL", 0.0),
        brownian_prob_down,
    ]
    proto = [0.85, 0.70, 0.70, 0.65, 0.55, 0.75, 0.65, 0.75, 0.70, 0.65]
    dot = sum(a * b for a, b in zip(vec, proto))
    norm_a = math.sqrt(sum(a * a for a in vec))
    norm_b = math.sqrt(sum(b * b for b in proto))
    exhaustion_similarity = _clamp(dot / max(norm_a * norm_b, 1e-12))

    neural_z = (
        1.20 * negative_acceleration_score
        + 0.95 * negative_volume_acceleration_score
        + 0.85 * z_extreme_score
        + 0.70 * tail_score
        + 0.65 * entropy
        + 1.00 * rejection_score
        + 0.75 * buyer_weakness
        + 1.05 * bayes
        + 0.90 * exhaustion_similarity
        + 0.85 * brownian_prob_down
        - 0.80 * _clamp(log_return_now / max(sigma, 1e-6))
        - 1.85
    )
    neural_score = _safe_sigmoid(neural_z)

    return {
        "mathFeatures": {
            "source": source,
            "derivative": round(velocity_now, 12),
            "secondDerivative": round(acceleration_now, 12),
            "priceIntegral": round(sum(closes), 12),
            "logReturn": round(log_return_now, 8),
            "logAcceleration": round(log_acceleration, 8),
            "meanLogReturn": round(mu, 8),
            "volatility": round(sigma, 8),
            "zScore": round(z_score, 4),
            "normalPdf": round(pdf, 8),
            "normalTailScore": round(tail_score, 4),
            "volumeVelocity": round(volume_velocity_now, 6),
            "volumeAcceleration": round(volume_acceleration, 6),
            "entropy": round(entropy, 4),
            "fourier": fourier,
            "bayesianExhaustionProbability": round(bayes, 4),
            "markov": markov,
            "brownian": {
                "probUp": round(brownian_prob_up, 4),
                "probDown": round(brownian_prob_down, 4),
                "drift": round(drift, 8),
            },
            "kelly": {
                "rawFraction": 0.0,
                "cappedFraction": 0.0,
                "note": "computed_after_monte_carlo_payload",
            },
            "cosineSimilarity": {
                "exhaustionPattern": round(exhaustion_similarity, 4),
            },
            "matrixWeight": {
                "z": round(neural_z, 6),
                "score": round(neural_score, 4),
            },
        }
    }


def _estimated_math_features(market: dict, close_strength: float, rejection: float) -> dict[str, Any]:
    change = _to_float(market.get("priceChangePercent")) / 100.0
    vol = _to_float(market.get("volatility24h"))
    velocity = change
    acceleration = 0.0
    z = change / max(vol, 1e-6) if vol > 0 else 0.0
    buyer_pressure = close_strength
    buyer_weakness = _clamp(1.0 - buyer_pressure)
    bayes = _bayesian_exhaustion_probability({
        "negativeAcceleration": 0.0,
        "negativeVolumeAcceleration": 0.0,
        "zExtreme": _clamp(abs(z) / 3),
        "rejection": rejection,
        "buyerWeakness": buyer_weakness,
    })
    prob_down = _clamp(0.5 + 0.25 * bayes - 0.25 * _clamp(change / 0.30))
    return {
        "mathFeatures": {
            "source": "estimated_from_24h_range",
            "derivative": round(velocity, 12),
            "secondDerivative": round(acceleration, 12),
            "priceIntegral": _to_float(market.get("lastPrice")),
            "logReturn": round(math.log(1 + change) if change > -0.99 else 0.0, 8),
            "logAcceleration": 0.0,
            "meanLogReturn": 0.0,
            "volatility": round(vol, 8),
            "zScore": round(z, 4),
            "normalPdf": 0.0,
            "normalTailScore": _clamp(abs(z) / 3),
            "volumeVelocity": 0.0,
            "volumeAcceleration": 0.0,
            "entropy": 0.0,
            "fourier": {"dominantCyclePeriod": 0.0, "dominantAmplitude": 0.0, "cycleExhaustionScore": 0.0},
            "bayesianExhaustionProbability": round(bayes, 4),
            "markov": {
                "currentState": "ESTIMATED",
                "next": {
                    "FUEL_STRONG": round(_clamp(change), 4),
                    "PUMPING": round(_clamp(change), 4),
                    "FUEL_WEAKENING": round(_clamp(bayes), 4),
                    "EXHAUSTION": round(_clamp(bayes * 0.65), 4),
                    "REVERSAL": round(prob_down, 4),
                    "NEUTRAL": 0.25,
                },
            },
            "brownian": {"probUp": round(1.0 - prob_down, 4), "probDown": round(prob_down, 4), "drift": 0.0},
            "kelly": {"rawFraction": 0.0, "cappedFraction": 0.0, "note": "estimated_only"},
            "cosineSimilarity": {"exhaustionPattern": round(_clamp(0.5 * bayes + 0.5 * rejection), 4)},
            "matrixWeight": {"z": 0.0, "score": round(bayes, 4)},
        }
    }


def _build_fuel_metrics(rows: list[Any], source: str) -> dict[str, Any] | None:
    candles = [parsed for parsed in (_parse_kline(row) for row in rows) if parsed]
    if len(candles) < 6:
        return None
    if all(c.get("timestamp", 0) > 0 for c in candles):
        candles.sort(key=lambda c: c["timestamp"])

    recent = candles[-3:]
    previous = candles[-6:-3]
    recent_qv = sum(c["quoteVolume"] for c in recent)
    previous_qv = sum(c["quoteVolume"] for c in previous)
    recent_taker_buy_qv = sum(c["takerBuyQuoteVolume"] for c in recent)
    has_taker = recent_qv > 0 and recent_taker_buy_qv > 0
    buyer_pressure = _clamp(recent_taker_buy_qv / recent_qv) if has_taker else 0.0
    seller_pressure = _clamp(1.0 - buyer_pressure) if has_taker else 0.0

    trend_pct = ((recent_qv - previous_qv) / previous_qv * 100) if previous_qv > 0 else 0.0
    close_strength = sum(c["closeStrength"] for c in recent) / len(recent)
    upper_wick = sum(c["upperWickRatio"] for c in recent) / len(recent)
    rejection = _clamp(0.65 * upper_wick + 0.35 * (1.0 - close_strength))

    if not has_taker:
        buyer_pressure = close_strength
        seller_pressure = _clamp(1.0 - close_strength)

    if trend_pct <= -25:
        volume_status = "declining"
    elif trend_pct >= 25:
        volume_status = "rising"
    else:
        volume_status = "flat"

    if volume_status == "declining" and rejection >= 0.45:
        fuel_signal = "volume_down_rejection_short_validating"
    elif volume_status == "declining" and close_strength >= 0.55:
        fuel_signal = "thin_weak_pump_watch"
    elif volume_status == "rising" and close_strength <= 0.45:
        fuel_signal = "distribution_possible"
    elif volume_status == "rising" and close_strength >= 0.55:
        fuel_signal = "fuel_still_strong"
    else:
        fuel_signal = "neutral_watch"

    closes = [c["close"] for c in candles]
    return {
        "volumeTrendPercent": round(trend_pct, 2),
        "volumeTrendStatus": volume_status,
        "recentQuoteVolume3h": round(recent_qv, 2),
        "previousQuoteVolume3h": round(previous_qv, 2),
        "takerBuyQuoteVolume3h": round(recent_taker_buy_qv, 2) if has_taker else 0.0,
        "takerSellQuoteVolume3h": round(max(recent_qv - recent_taker_buy_qv, 0), 2) if has_taker else 0.0,
        "buyerPressure3h": round(buyer_pressure, 4),
        "sellerPressure3h": round(seller_pressure, 4),
        "buyerPressureSource": "taker_buy_quote_volume" if has_taker else "close_strength_proxy",
        "closeStrength3h": round(close_strength, 4),
        "upperWickRatio3h": round(upper_wick, 4),
        "rejectionScore": round(rejection, 4),
        "fuelSignal": fuel_signal,
        "fuelMetricsSource": source,
        "hasRealTakerBuyVolume": has_taker,
        **_historical_stats_from_closes(closes, "1h"),
        **_math_features_from_candles({}, candles, source),
    }


def _estimate_fuel_metrics(market: dict, reason: str) -> dict[str, Any]:
    price = max(0.0, _to_float(market.get("lastPrice") or market.get("currentPrice")))
    high = max(price, _to_float(market.get("highPrice"), price))
    low = min(price, _to_float(market.get("lowPrice"), price)) if price > 0 else _to_float(market.get("lowPrice"), 0.0)
    quote_volume = max(0.0, _to_float(market.get("quoteVolume")))
    candle_range = max(high - low, 1e-12)
    close_strength = _clamp((price - low) / candle_range) if price > 0 else 0.5
    upper_wick = _clamp((high - price) / candle_range) if price > 0 else 0.0
    rejection = _clamp(0.65 * upper_wick + 0.35 * (1.0 - close_strength))
    qv_3h = quote_volume * (3.0 / 24.0)
    vol24 = _to_float(market.get("volatility24h"))
    if vol24 <= 0 and price > 0:
        vol24 = candle_range / price
    estimated = {
        **market,
        "volumeTrendPercent": 0.0,
        "volumeTrendStatus": "estimated",
        "recentQuoteVolume3h": round(qv_3h, 2),
        "previousQuoteVolume3h": round(qv_3h, 2),
        "takerBuyQuoteVolume3h": 0.0,
        "takerSellQuoteVolume3h": 0.0,
        "buyerPressure3h": round(close_strength, 4),
        "sellerPressure3h": round(1.0 - close_strength, 4),
        "buyerPressureSource": "estimated_close_strength_proxy",
        "closeStrength3h": round(close_strength, 4),
        "upperWickRatio3h": round(upper_wick, 4),
        "rejectionScore": round(rejection, 4),
        "fuelSignal": "estimated_from_24h_range",
        "fuelMetricsSource": "estimated_from_24h_range",
        "fuelMetricsDebug": reason,
        "historicalReturns": [],
        "historicalReturnInterval": "estimated_24h",
        "historicalVolDaily": round(max(vol24, 0.0), 8),
        "historicalMeanDaily": round(_to_float(market.get("priceChangePercent")) / 100.0, 8),
        **_estimated_math_features(market, close_strength, rejection),
    }
    return _with_quality(estimated, has_kline=False, has_taker=False, has_market_cap=_to_float(estimated.get("marketCap")) > 0, has_math=False)


async def _fetch_market_cap(client: httpx.AsyncClient, symbol: str) -> dict[str, Any] | None:
    base = _base_symbol(symbol).lower()
    if not base:
        return None
    headers = {"accept": "application/json", "user-agent": "LQ-Short-Hunter/2.5"}
    try:
        search_response = await client.get(
            "https://api.coingecko.com/api/v3/search",
            params={"query": base},
            timeout=8.0,
            headers=headers,
        )
        if search_response.status_code != 200:
            return None
        coins = (search_response.json() or {}).get("coins") or []
        coin_id = None
        for coin in coins:
            if str(coin.get("symbol") or "").lower() == base:
                coin_id = coin.get("id")
                break
        if not coin_id and coins:
            coin_id = coins[0].get("id")
        if not coin_id:
            return None

        market_response = await client.get(
            "https://api.coingecko.com/api/v3/coins/markets",
            params={"vs_currency": "usd", "ids": coin_id, "sparkline": "false"},
            timeout=8.0,
            headers=headers,
        )
        if market_response.status_code != 200:
            return None
        rows = market_response.json()
        if not isinstance(rows, list) or not rows:
            return None
        item = rows[0]
        market_cap = _to_float(item.get("market_cap"))
        if market_cap <= 0:
            return None
        return {
            "marketCap": market_cap,
            "marketCapSource": "coingecko",
            "coinGeckoId": coin_id,
            "circulatingSupply": _to_float(item.get("circulating_supply")),
            "fullyDilutedValuation": _to_float(item.get("fully_diluted_valuation")),
            "coinGeckoTotalVolume": _to_float(item.get("total_volume")),
        }
    except Exception:
        return None


async def _add_market_cap_and_quality(client: httpx.AsyncClient, market: dict, *, has_kline: bool, has_taker: bool, has_math: bool = False) -> dict:
    current_cap = _to_float(market.get("marketCap") or market.get("market_cap"))
    enriched = dict(market)
    if current_cap <= 0:
        cap = await _fetch_market_cap(client, str(market.get("symbol") or ""))
        if cap:
            enriched.update(cap)
            current_cap = _to_float(enriched.get("marketCap"))
    return _with_quality(enriched, has_kline=has_kline, has_taker=has_taker, has_market_cap=current_cap > 0, has_math=has_math)


async def enrich_market_with_fuel_metrics(client: httpx.AsyncClient, market: dict) -> dict:
    sym = str(market.get("symbol") or "").upper().strip()
    if not sym:
        return market
    usdt_symbol, hyphen_symbol = _symbol_variants(sym)
    urls = [
        ("binance_spot_1h_klines", f"https://api.binance.com/api/v3/klines?symbol={usdt_symbol}&interval=1h&limit=24"),
        ("binance_spot_alt_1h_klines", f"https://api1.binance.com/api/v3/klines?symbol={usdt_symbol}&interval=1h&limit=24"),
        ("binance_futures_1h_klines", f"https://fapi.binance.com/fapi/v1/klines?symbol={usdt_symbol}&interval=1h&limit=24"),
        ("mexc_1h_klines", f"https://api.mexc.com/api/v3/klines?symbol={usdt_symbol}&interval=1h&limit=24"),
        ("okx_1h_klines", f"https://www.okx.com/api/v5/market/candles?instId={hyphen_symbol}&bar=1H&limit=24"),
        ("bybit_spot_1h_klines", f"https://api.bybit.com/v5/market/kline?category=spot&symbol={usdt_symbol}&interval=60&limit=24"),
        ("bybit_linear_1h_klines", f"https://api.bybit.com/v5/market/kline?category=linear&symbol={usdt_symbol}&interval=60&limit=24"),
    ]
    errors: list[str] = []
    for source, url in urls:
        try:
            response = await client.get(url, timeout=9.0, headers={"accept": "application/json", "user-agent": "LQ-Short-Hunter/2.5"})
            if response.status_code != 200:
                errors.append(f"{source}: HTTP {response.status_code}")
                continue
            metrics = _build_fuel_metrics(_extract_kline_rows(response.json()), source)
            if metrics:
                has_taker = bool(metrics.get("hasRealTakerBuyVolume"))
                return await _add_market_cap_and_quality(client, {**market, **metrics}, has_kline=True, has_taker=has_taker, has_math=True)
            errors.append(f"{source}: insufficient klines")
        except Exception as exc:
            errors.append(f"{source}: {type(exc).__name__}")
    estimated = _estimate_fuel_metrics(market, "; ".join(errors[:5]) or "klines unavailable")
    return await _add_market_cap_and_quality(client, estimated, has_kline=False, has_taker=False, has_math=False)


_original_build_simulation_params = mc.build_simulation_params_from_market


def build_simulation_params_from_market(market: dict | None = None) -> dict:
    market = market or {}
    params = _original_build_simulation_params(market)
    parkinson_annual = _to_float(params.get("annualVolatility"), 0.65)
    hist_vol_daily = _to_float(market.get("historicalVolDaily"), 0.0)
    hist_mean_daily = _to_float(market.get("historicalMeanDaily"), 0.0)

    if hist_vol_daily <= 0:
        values = [_to_float(r) for r in (market.get("historicalReturns") or []) if isinstance(r, (int, float, str))]
        if len(values) >= 2:
            mean_return = _mean(values)
            variance = sum((r - mean_return) ** 2 for r in values) / max(1, len(values) - 1)
            interval = str(market.get("historicalReturnInterval") or "daily")
            scale = math.sqrt(24.0) if interval == "1h" else 1.0
            hist_vol_daily = math.sqrt(max(variance, 0.0)) * scale
            hist_mean_daily = mean_return * (24.0 if interval == "1h" else 1.0)

    if hist_vol_daily > 0:
        hist_annual = _clamp(hist_vol_daily * math.sqrt(365.0), 0.10, 5.0)
        params["annualVolatility"] = _clamp(0.60 * hist_annual + 0.40 * parkinson_annual, 0.10, 5.0)
        params["historicalVolDaily"] = hist_vol_daily
        params["historicalMeanDaily"] = hist_mean_daily
        params["volatilitySource"] = "hybrid_60pct_historical_returns_40pct_parkinson_range"
    else:
        params["volatilitySource"] = "parkinson_24h_range_only"
    return params


def _score_math_decision(payload: dict, raw_market: dict) -> dict[str, Any]:
    market = payload.get("market") or {}
    mc_payload = payload.get("monteCarlo") or {}
    bull = payload.get("bullishCounterScenario") or {}
    vf = bull.get("volumeFuel") or {}
    quality = payload.get("dataQuality") or market.get("dataQuality") or {}
    math_features = raw_market.get("mathFeatures") or market.get("mathFeatures") or {}

    if not isinstance(math_features, dict):
        math_features = {}

    vol = abs(_to_float(math_features.get("volatility")))
    log_return = _to_float(math_features.get("logReturn"))
    log_acceleration = _to_float(math_features.get("logAcceleration"))
    z_score = _to_float(math_features.get("zScore"))
    tail_score = _clamp(_to_float(math_features.get("normalTailScore")))
    volume_velocity = _to_float(math_features.get("volumeVelocity"))
    volume_acceleration = _to_float(math_features.get("volumeAcceleration"))
    entropy = _clamp(_to_float(math_features.get("entropy")))
    bayes = _clamp(_to_float(math_features.get("bayesianExhaustionProbability")))
    rejection = _clamp(_to_float(vf.get("rejectionScore") or market.get("rejectionScore")))
    buyer_pressure = _clamp(_to_float(vf.get("buyerPressure3h") or market.get("buyerPressure3h")))
    seller_pressure = _clamp(_to_float(vf.get("sellerPressure3h") or market.get("sellerPressure3h")))
    data_conf = _clamp(_to_float(quality.get("confidencePercent") or market.get("dataConfidencePercent")) / 100.0)

    markov = math_features.get("markov") if isinstance(math_features.get("markov"), dict) else {}
    next_probs = markov.get("next") if isinstance(markov.get("next"), dict) else {}
    markov_pump = _clamp(_to_float(next_probs.get("FUEL_STRONG")) + _to_float(next_probs.get("PUMPING")))
    markov_exhaust = _clamp(_to_float(next_probs.get("FUEL_WEAKENING")) + _to_float(next_probs.get("EXHAUSTION")) + _to_float(next_probs.get("REVERSAL")))
    brownian = math_features.get("brownian") if isinstance(math_features.get("brownian"), dict) else {}
    brownian_down = _clamp(_to_float(brownian.get("probDown")))
    brownian_up = _clamp(_to_float(brownian.get("probUp")))
    cosine = math_features.get("cosineSimilarity") if isinstance(math_features.get("cosineSimilarity"), dict) else {}
    pattern_similarity = _clamp(_to_float(cosine.get("exhaustionPattern")))
    matrix_weight = math_features.get("matrixWeight") if isinstance(math_features.get("matrixWeight"), dict) else {}
    neural_score = _clamp(_to_float(matrix_weight.get("score")))

    prob_tp = _clamp(_to_float(mc_payload.get("probabilityTP")) / 100.0)
    prob_sl = _clamp(_to_float(mc_payload.get("probabilitySL")) / 100.0)
    rr = max(_to_float(mc_payload.get("riskReward")), 0.0)
    ev_pct = _to_float(mc_payload.get("expectedValuePct")) / 100.0
    q = 1.0 - prob_tp
    b = max(rr, 1e-9)
    kelly_raw = ((b * prob_tp) - q) / b if b > 0 else 0.0
    kelly_capped = _clamp(kelly_raw, 0.0, 0.05)

    negative_acceleration = _clamp(-log_acceleration / max(vol, 1e-6))
    positive_acceleration = _clamp(log_acceleration / max(vol, 1e-6))
    positive_velocity = _clamp(log_return / max(vol, 1e-6))
    negative_velocity = _clamp(-log_return / max(vol, 1e-6))
    z_extreme = _clamp(abs(z_score) / 3.0)
    volume_decay = _clamp(-volume_velocity)
    volume_decel = _clamp(-volume_acceleration)
    low_data_penalty = _clamp(1.0 - data_conf)

    pump_continuation = _clamp(
        0.22 * positive_velocity
        + 0.18 * positive_acceleration
        + 0.14 * _clamp(volume_velocity)
        + 0.16 * buyer_pressure
        + 0.10 * _clamp(1.0 - rejection)
        + 0.10 * brownian_up
        + 0.10 * markov_pump
    )

    pump_exhaustion = _clamp(
        0.20 * negative_acceleration
        + 0.16 * volume_decay
        + 0.13 * volume_decel
        + 0.12 * z_extreme
        + 0.10 * tail_score
        + 0.10 * entropy
        + 0.14 * bayes
        + 0.10 * markov_exhaust
        + 0.10 * rejection
        + 0.05 * seller_pressure
    )

    short_ready = _clamp(
        0.14 * negative_acceleration
        + 0.10 * z_extreme
        + 0.08 * tail_score
        + 0.08 * entropy
        + 0.13 * bayes
        + 0.10 * markov_exhaust
        + 0.10 * brownian_down
        + 0.09 * pattern_similarity
        + 0.08 * volume_decay
        + 0.07 * rejection
        + 0.08 * neural_score
        + 0.05 * _clamp(ev_pct * 10.0)
        - 0.14 * pump_continuation
        - 0.10 * prob_sl
        - 0.08 * low_data_penalty
    )

    already_dropped = negative_velocity > 0.55 and short_ready > 0.40 and pump_exhaustion > 0.50

    if data_conf < 0.35:
        final_state = "DATA_KURANG_TUNGGU"
        label = "Data kurang — tunggu validasi"
    elif already_dropped:
        final_state = "PUMP_SUDAH_LEWAT"
        label = "Pump sudah lewat / telat short"
    elif short_ready >= 0.70 and pump_continuation < 0.45:
        final_state = "SIAP_SHORT"
        label = "Siap short setelah konfirmasi struktur"
    elif pump_exhaustion >= 0.60 and short_ready >= 0.45:
        final_state = "EXHAUSTION_WATCH"
        label = "Pump exhaustion watch"
    elif pump_continuation >= 0.62 and short_ready < 0.55:
        final_state = "MASIH_PUMP"
        label = "Masih pump / fuel aktif"
    elif entropy >= 0.75 and short_ready < 0.60:
        final_state = "MARKET_RANDOM"
        label = "Market random — jangan entry agresif"
    else:
        final_state = "WAIT_CONFIRMATION"
        label = "Tunggu konfirmasi"

    math_features["kelly"] = {
        "rawFraction": round(kelly_raw, 4),
        "cappedFraction": round(kelly_capped, 4),
        "note": "theoretical_only_not_position_advice",
    }

    return {
        "finalState": final_state,
        "label": label,
        "shortEntryReadinessScore": round(short_ready * 100, 2),
        "pumpContinuationScore": round(pump_continuation * 100, 2),
        "pumpExhaustionScore": round(pump_exhaustion * 100, 2),
        "dataConfidenceScore": round(data_conf * 100, 2),
        "kellyFractionCapped": round(kelly_capped * 100, 2),
        "scoreDrivers": {
            "negativeAcceleration": round(negative_acceleration, 4),
            "positiveVelocity": round(positive_velocity, 4),
            "volumeDecay": round(volume_decay, 4),
            "volumeDeceleration": round(volume_decel, 4),
            "zExtreme": round(z_extreme, 4),
            "tailRisk": round(tail_score, 4),
            "entropy": round(entropy, 4),
            "bayesianExhaustion": round(bayes, 4),
            "markovPump": round(markov_pump, 4),
            "markovExhaustion": round(markov_exhaust, 4),
            "brownianDown": round(brownian_down, 4),
            "patternSimilarity": round(pattern_similarity, 4),
            "pumpContinuationPenalty": round(pump_continuation, 4),
            "probSLPenalty": round(prob_sl, 4),
        },
    }


def _patch_agent_payloads() -> None:
    if not hasattr(agent, "_market_fuel_original_compact_payload"):
        agent._market_fuel_original_compact_payload = agent._compact_payload
    if not hasattr(agent, "_market_fuel_original_rule_based_analysis"):
        agent._market_fuel_original_rule_based_analysis = agent._rule_based_analysis
    if not hasattr(agent, "_market_fuel_original_agent_prompts"):
        agent._market_fuel_original_agent_prompts = agent._agent_prompts

    original_compact = agent._market_fuel_original_compact_payload
    original_rule = agent._market_fuel_original_rule_based_analysis
    original_prompts = agent._market_fuel_original_agent_prompts

    def compact_payload_with_math_decision(body: dict) -> dict:
        payload = original_compact(body)
        raw_market = body.get("market") or {}
        market = payload.setdefault("market", {})
        raw_math = raw_market.get("mathFeatures") or {}
        additions = {
            "buyerPressure3h": agent._safe_num(raw_market.get("buyerPressure3h"), 4),
            "sellerPressure3h": agent._safe_num(raw_market.get("sellerPressure3h"), 4),
            "buyerPressureSource": raw_market.get("buyerPressureSource") or "unknown",
            "takerBuyQuoteVolume3h": agent._safe_num(raw_market.get("takerBuyQuoteVolume3h"), 2),
            "takerSellQuoteVolume3h": agent._safe_num(raw_market.get("takerSellQuoteVolume3h"), 2),
            "dataConfidencePercent": agent._safe_num(raw_market.get("dataConfidencePercent"), 2),
            "dataQualityOverall": raw_market.get("dataQualityOverall") or "unknown",
            "marketCapSource": raw_market.get("marketCapSource") or ("payload" if agent._safe_num(raw_market.get("marketCap"), 2) > 0 else "missing"),
            "circulatingSupply": agent._safe_num(raw_market.get("circulatingSupply"), 2),
        }
        market.update(additions)
        data_quality = raw_market.get("dataQuality") or {}
        if data_quality:
            payload["dataQuality"] = data_quality
            market["dataQuality"] = data_quality
        if raw_math:
            payload["mathSignals"] = raw_math
            market["mathFeatures"] = raw_math

        bull = payload.setdefault("bullishCounterScenario", {})
        bull["dataQuality"] = data_quality or {
            "overall": additions["dataQualityOverall"],
            "confidencePercent": additions["dataConfidencePercent"],
        }
        vf = bull.setdefault("volumeFuel", {})
        vf.update({
            "buyerPressure3h": additions["buyerPressure3h"],
            "sellerPressure3h": additions["sellerPressure3h"],
            "buyerPressureSource": additions["buyerPressureSource"],
            "takerBuyQuoteVolume3h": additions["takerBuyQuoteVolume3h"],
            "takerSellQuoteVolume3h": additions["takerSellQuoteVolume3h"],
            "dataConfidencePercent": additions["dataConfidencePercent"],
            "dataQualityOverall": additions["dataQualityOverall"],
        })

        payload["mathDecision"] = _score_math_decision(payload, raw_market)
        if payload.get("mathSignals"):
            payload["mathSignals"]["kelly"] = {
                "rawFraction": payload["mathSignals"].get("kelly", {}).get("rawFraction", 0),
                "cappedFraction": payload["mathDecision"].get("kellyFractionCapped", 0) / 100.0,
                "note": "theoretical_only_not_position_advice",
            }
        return payload

    def _fmt_money(value: Any) -> str:
        try:
            return agent._compact_money(float(value or 0))
        except Exception:
            return "$0"

    def rule_based_with_math_decision(payload: dict) -> str:
        text = original_rule(payload)
        market = payload.get("market") or {}
        bull = payload.get("bullishCounterScenario") or {}
        vf = bull.get("volumeFuel") or {}
        dq = payload.get("dataQuality") or bull.get("dataQuality") or market.get("dataQuality") or {}
        confidence = vf.get("dataConfidencePercent") or market.get("dataConfidencePercent") or dq.get("confidencePercent") or 0
        overall = vf.get("dataQualityOverall") or market.get("dataQualityOverall") or dq.get("overall") or "unknown"
        math_decision = payload.get("mathDecision") or {}
        signals = payload.get("mathSignals") or {}
        drivers = math_decision.get("scoreDrivers") or {}
        lines = [
            "",
            "## Math Scoring Decision",
            f"- Final state: **{math_decision.get('label', math_decision.get('finalState', 'unknown'))}**",
            f"- Short readiness: {math_decision.get('shortEntryReadinessScore', 0)}/100",
            f"- Pump continuation: {math_decision.get('pumpContinuationScore', 0)}/100",
            f"- Pump exhaustion: {math_decision.get('pumpExhaustionScore', 0)}/100",
            f"- Velocity/log return: {signals.get('logReturn', 0)} | Acceleration: {signals.get('logAcceleration', 0)}",
            f"- Z-score: {signals.get('zScore', 0)} | Entropy: {signals.get('entropy', 0)} | Bayesian exhaustion: {signals.get('bayesianExhaustionProbability', 0)}",
            f"- Brownian down: {(signals.get('brownian') or {}).get('probDown', 0)} | Markov state: {(signals.get('markov') or {}).get('currentState', 'unknown')}",
            f"- Pattern similarity: {(signals.get('cosineSimilarity') or {}).get('exhaustionPattern', 0)} | Kelly capped: {math_decision.get('kellyFractionCapped', 0)}%",
            f"- Main drivers: negAccel={drivers.get('negativeAcceleration', 0)}, volDecay={drivers.get('volumeDecay', 0)}, rejection={vf.get('rejectionScore', market.get('rejectionScore', 0))}, pumpPenalty={drivers.get('pumpContinuationPenalty', 0)}",
            "",
            "## Data Quality / Buyer Pressure",
            f"- Data confidence: {confidence}% ({overall})",
            f"- Volume trend 3h quality: {dq.get('volumeTrend3h', 'unknown')}",
            f"- Buyer pressure 3h: {vf.get('buyerPressure3h', market.get('buyerPressure3h', 0))} ({vf.get('buyerPressureSource', market.get('buyerPressureSource', 'unknown'))})",
            f"- Taker buy/sell 3h: {_fmt_money(vf.get('takerBuyQuoteVolume3h', 0))} / {_fmt_money(vf.get('takerSellQuoteVolume3h', 0))}",
            f"- Market cap quality: {dq.get('marketCap', 'unknown')} | Math scoring: {dq.get('mathScoring', 'unknown')} | Order book: {dq.get('orderBookDepth', 'MISSING')} | Derivatives: {dq.get('derivatives', 'MISSING')}",
        ]
        return text + "\n" + "\n".join(lines)

    def prompts_with_math_decision(payload: dict) -> tuple[str, str]:
        system_prompt, user_prompt = original_prompts(payload)
        if (payload.get("analysisMode") or "").lower() == "market_fuel":
            system_prompt += (
                " Gunakan mathDecision sebagai keputusan utama. Jangan hitung ulang dari nol. "
                "Jelaskan kenapa finalState menjadi SIAP_SHORT, MASIH_PUMP, EXHAUSTION_WATCH, "
                "WAIT_CONFIRMATION, PUMP_SUDAH_LEWAT, MARKET_RANDOM, atau DATA_KURANG_TUNGGU. "
                "Bahas score: shortEntryReadiness, pumpContinuation, pumpExhaustion, dataConfidence, "
                "serta sinyal derivative/acceleration, z-score, entropy, Bayesian, Markov, Brownian, Kelly, dan cosine similarity."
            )
            user_prompt = (
                "Analisis payload berikut sebagai MARKET FUEL CHECKER. "
                "PENTING: mathDecision adalah output final scoring engine; tugasmu menjelaskan, bukan mengganti keputusan.\n"
                "Gunakan format markdown ini:\n"
                "## Market Fuel Status\n"
                "## Math Scoring Decision\n"
                "## Volume & Buyer Pressure\n"
                "## Required Fuel for Next Pump\n"
                "## Exhaustion / Rejection Signal\n"
                "## Monte Carlo vs Fuel\n"
                "## Data Quality\n"
                "## Final Read\n\n"
                f"Payload:\n{__import__('json').dumps(payload, indent=2)}"
            )
        return system_prompt, user_prompt

    agent._compact_payload = compact_payload_with_math_decision
    agent._rule_based_analysis = rule_based_with_math_decision
    agent._agent_prompts = prompts_with_math_decision


def apply_runtime_patches() -> None:
    server._enrich_market_with_fuel_metrics = enrich_market_with_fuel_metrics
    mc.build_simulation_params_from_market = build_simulation_params_from_market
    server.build_simulation_params_from_market = build_simulation_params_from_market
    _patch_agent_payloads()

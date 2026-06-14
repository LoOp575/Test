"""
Monte Carlo Simulation + Pump Exhaustion + Auto Levels.

Upgrade:
- Vectorized GBM simulation with numpy
- Hybrid volatility blend
- Path-based TP/SL hit simulation
- Robust clamping to prevent NaN/Inf
- Safer defaults for Vercel runtime
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np


def _to_float(v: Any, fallback: float = 0.0) -> float:
    try:
        n = float(v)
        if math.isfinite(n):
            return n
    except (TypeError, ValueError):
        pass
    return fallback


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def calc_pump_exhaustion(market: dict | None = None) -> dict:
    market = market or {}
    price = max(0.0, _to_float(market.get("lastPrice", market.get("currentPrice", 0))))
    high = max(price, _to_float(market.get("highPrice"), price))
    low = max(0.0, min(price, _to_float(market.get("lowPrice"), price)))
    change_pct = _to_float(market.get("priceChangePercent")) / 100.0
    quote_volume = max(0.0, _to_float(market.get("quoteVolume")))

    rng = max(high - low, 0.0)
    range_pct = (rng / price) if price > 0 else 0.0
    position_in_range = _clamp((price - low) / rng, 0.0, 1.0) if rng > 0 else 0.5

    pump_strength = _clamp(max(change_pct, 0.0) / 0.25, 0.0, 1.0)
    volatility_strength = _clamp(range_pct / 0.18, 0.0, 1.0)
    high_pressure = _clamp(position_in_range, 0.0, 1.0)
    volume_strength = _clamp(math.log10(quote_volume + 1.0) / 10.0, 0.0, 1.0)

    wick_ratio = 0.0
    if rng > 0 and change_pct > 0:
        wick_ratio = _clamp(1.0 - position_in_range, 0.0, 1.0)

    exhaustion_score = _clamp(
        0.30 * pump_strength
        + 0.22 * high_pressure
        + 0.22 * volatility_strength
        + 0.13 * volume_strength
        + 0.13 * wick_ratio,
        0.0,
        1.0,
    )

    phase = "NORMAL"
    if exhaustion_score >= 0.75:
        phase = "PUMP_EXHAUSTED"
    elif exhaustion_score >= 0.58:
        phase = "PUMP_TIRED"
    elif exhaustion_score >= 0.42:
        phase = "PUMP_WATCH"

    return {
        "price": price,
        "high": high,
        "low": low,
        "range": rng,
        "rangePct": range_pct,
        "changePct": change_pct,
        "positionInRange": position_in_range,
        "pumpStrength": pump_strength,
        "volatilityStrength": volatility_strength,
        "highPressure": high_pressure,
        "volumeStrength": volume_strength,
        "wickRatio": wick_ratio,
        "exhaustionScore": exhaustion_score,
        "phase": phase,
    }


def build_auto_short_levels(market: dict | None = None) -> dict:
    x = calc_pump_exhaustion(market)
    price = x["price"]
    if price <= 0:
        return {"ok": False, "reason": "Invalid market price", "takeProfit": 0, "stopLoss": 0, "exhaustion": x}

    pullback_pct = _clamp(
        0.015 + 0.42 * x["rangePct"] + 0.06 * x["exhaustionScore"],
        0.015,
        0.18,
    )
    stop_buffer_pct = _clamp(
        0.010 + 0.22 * x["rangePct"] + 0.035 * (1 - x["exhaustionScore"]),
        0.010,
        0.12,
    )

    if x["range"] > 0:
        range_target = price - x["range"] * (0.28 + 0.22 * x["exhaustionScore"])
    else:
        range_target = price * (1 - pullback_pct)
    percent_target = price * (1 - pullback_pct)

    take_profit = min(range_target, percent_target)
    take_profit = max(take_profit, price * 0.55)
    take_profit = min(take_profit, price * 0.985)

    high_stop = x["high"] * 1.003 if x["high"] > price else price * (1 + stop_buffer_pct)
    pct_stop = price * (1 + stop_buffer_pct)
    stop_loss = max(high_stop, pct_stop)
    stop_loss = min(stop_loss, price * 1.25)
    stop_loss = max(stop_loss, price * 1.01)

    gain = price - take_profit
    loss = stop_loss - price
    rr = gain / loss if loss > 0 else 0.0

    return {
        "ok": True,
        "takeProfit": take_profit,
        "stopLoss": stop_loss,
        "pullbackPct": pullback_pct,
        "stopBufferPct": stop_buffer_pct,
        "gain": gain,
        "loss": loss,
        "riskReward": rr,
        "exhaustion": x,
    }


def build_simulation_params_from_market(market: dict | None = None) -> dict:
    levels = build_auto_short_levels(market)
    x = levels["exhaustion"]
    price = x["price"]

    rng_pct = max(x["rangePct"], 0.005)
    parkinson_daily = rng_pct / (2.0 * math.sqrt(math.log(2.0)))
    annual_vol = _clamp(parkinson_daily * math.sqrt(365.0), 0.10, 5.0)

    if x["changePct"] > 0:
        mu = -0.02 - 0.06 * x["exhaustionScore"]
    else:
        mu = 0.0

    return {
        "currentPrice": price,
        "mu": mu,
        "annualVolatility": annual_vol,
        "daysForecast": 7,
        "simulations": 15000,
        "steps": 32,
        "spotFlow": _clamp(0.1 + 0.55 * x["pumpStrength"] - 0.40 * x["exhaustionScore"], -1, 1),
        "oiFlow": 0.0,
        "shortLiqAbove": 0.0,
        "longLiqBelow": 0.0,
        "takeProfit": levels["takeProfit"],
        "stopLoss": levels["stopLoss"],
        "lambda": 0.5,
        "exhaustionScore": x["exhaustionScore"],
        "autoLevels": levels,
    }


def _repair_short_levels(price: float, raw_tp: Any, raw_sl: Any) -> tuple[float, float, float]:
    p = max(1e-12, _to_float(price, 65000))
    tp = _to_float(raw_tp, p * 0.96)
    sl = _to_float(raw_sl, p * 1.03)
    if not math.isfinite(tp) or tp <= 0 or tp >= p:
        tp = p * 0.96
    if not math.isfinite(sl) or sl <= p:
        sl = p * 1.03
    tp = max(p * 1e-6, min(tp, p * 0.999))
    sl = max(sl, p * 1.001)
    return p, tp, sl


def normalize_simulation_input(raw: dict | None = None) -> dict:
    raw = raw or {}
    price, tp, sl = _repair_short_levels(raw.get("currentPrice"), raw.get("takeProfit"), raw.get("stopLoss"))
    sims = int(round(_clamp(_to_float(raw.get("simulations"), 15000), 1000, 30000)))
    ann_vol = _clamp(_to_float(raw.get("annualVolatility"), 0.65), 0.01, 5.0)
    days = _clamp(_to_float(raw.get("daysForecast"), 7), 1, 365)
    steps = int(round(_clamp(_to_float(raw.get("steps"), 32), 8, 96)))

    auto_levels = raw.get("autoLevels") or {}
    exh_inner = auto_levels.get("exhaustion") if isinstance(auto_levels, dict) else None
    raw_exh = raw.get("exhaustionScore")
    if raw_exh is None and isinstance(exh_inner, dict):
        raw_exh = exh_inner.get("exhaustionScore", 0)

    return {
        "currentPrice": price,
        "takeProfit": tp,
        "stopLoss": sl,
        "mu": _clamp(_to_float(raw.get("mu"), 0), -5, 5),
        "lambda": _clamp(_to_float(raw.get("lambda"), 0.5), 0, 2),
        "annualVolatility": ann_vol,
        "daysForecast": days,
        "simulations": sims,
        "steps": steps,
        "exhaustionScore": _clamp(_to_float(raw_exh, 0), 0, 1),
        "spotFlow": _clamp(_to_float(raw.get("spotFlow"), 0), -1, 1),
        "oiFlow": _clamp(_to_float(raw.get("oiFlow"), 0), -1, 1),
        "shortLiqAbove": max(0.0, _to_float(raw.get("shortLiqAbove"), 0)),
        "longLiqBelow": max(0.0, _to_float(raw.get("longLiqBelow"), 0)),
        "autoLevels": auto_levels if isinstance(auto_levels, dict) else None,
    }


def _build_histogram(arr: np.ndarray, bucket_count: int) -> list[dict]:
    if len(arr) == 0:
        return []
    mn = float(arr.min())
    mx = float(arr.max())
    if mn == mx:
        return [{"lower": mn, "upper": mx, "mid": mn, "count": int(len(arr)), "density": 1.0}]
    counts, edges = np.histogram(arr, bins=bucket_count, range=(mn, mx))
    total = int(len(arr))
    out = []
    for i, c in enumerate(counts):
        lo = float(edges[i])
        hi = float(edges[i + 1])
        out.append(
            {
                "lower": lo,
                "upper": hi,
                "mid": float((lo + hi) / 2.0),
                "count": int(c),
                "density": float(c) / total if total else 0.0,
            }
        )
    return out


def run_monte_carlo_simulation(p: dict) -> dict:
    errors: list[str] = []
    if p["currentPrice"] <= 0:
        errors.append("currentPrice must be greater than 0")
    if p["takeProfit"] >= p["currentPrice"]:
        errors.append("For a short setup, takeProfit should be below currentPrice")
    if p["stopLoss"] <= p["currentPrice"]:
        errors.append("For a short setup, stopLoss should be above currentPrice")
    if p["annualVolatility"] <= 0:
        errors.append("annualVolatility must be greater than 0")
    if p["daysForecast"] <= 0:
        errors.append("daysForecast must be greater than 0")
    if p["simulations"] <= 0:
        errors.append("simulations must be greater than 0")
    if errors:
        return {"ok": False, "errors": errors, "repairedInput": p}

    short_above = max(0.0, p["shortLiqAbove"])
    long_below = max(0.0, p["longLiqBelow"])
    s = short_above + long_below
    liq_magnet = (short_above - long_below) / s if s > 0 else 0.0
    liq_pressure = 0.4 * p["spotFlow"] + 0.3 * p["oiFlow"] + 0.3 * liq_magnet
    mu_adj = p["mu"] + p["lambda"] * liq_pressure + 0.08 * p["exhaustionScore"]

    sigma = _clamp(p["annualVolatility"], 0.01, 5.0)
    T = p["daysForecast"] / 365.0
    n = int(p["simulations"])
    steps = int(p.get("steps", 32))
    dt = T / steps

    rng = np.random.default_rng()

    log_prices = np.full((n,), math.log(p["currentPrice"]), dtype=np.float64)
    hit_tp = np.zeros(n, dtype=bool)
    hit_sl = np.zeros(n, dtype=bool)
    tp = p["takeProfit"]
    sl = p["stopLoss"]
    ln_tp = math.log(tp)
    ln_sl = math.log(sl)

    drift = (mu_adj - 0.5 * sigma * sigma) * dt
    vol_term = sigma * math.sqrt(dt)

    for _ in range(steps):
        z = rng.standard_normal(n)
        log_prices = log_prices + drift + vol_term * z
        prices = np.exp(log_prices)
        hit_tp |= prices <= tp
        hit_sl |= prices >= sl
        if np.all(hit_tp | hit_sl):
            break

    ST = np.exp(log_prices)
    current = p["currentPrice"]

    prob_tp = float(np.mean(hit_tp))
    prob_sl = float(np.mean(hit_sl))
    prob_down = float(np.mean(ST < current))

    gain = current - tp
    loss = sl - current
    rr = (gain / loss) if loss > 0 else 0.0
    expected_value = prob_tp * gain - prob_sl * loss
    expected_value_pct = expected_value / current if current > 0 else 0.0

    exhaustion_component = p["exhaustionScore"]
    rr_score = _clamp(rr / 3.0, 0.0, 1.0)
    ev_score = _clamp(expected_value_pct / 0.08, -1.0, 1.0)
    directional_edge = _clamp(prob_down - 0.5, 0.0, 0.5) * 2.0

    score_raw = (
        0.22 * exhaustion_component
        + 0.18 * prob_down
        + 0.18 * directional_edge
        + 0.18 * prob_tp
        + 0.12 * rr_score
        + 0.12 * ev_score
        - 0.20 * prob_sl
    )
    score = _clamp(score_raw, 0.0, 1.0)

    status = "NO_SHORT"
    if prob_sl > 0.45:
        status = "DANGER_STOP_RISK"
    elif score >= 0.75 and expected_value > 0 and prob_tp > prob_sl:
        status = "STRONG_SHORT_SETUP"
    elif score >= 0.62 and expected_value > 0:
        status = "SHORT_VALID"
    elif score >= 0.48:
        status = "SHORT_WATCH"
    elif score >= 0.32:
        status = "WEAK_WATCH"

    sorted_arr = np.sort(ST)
    median = float(np.median(sorted_arr))
    mean_p = float(np.mean(sorted_arr))
    worst5 = float(np.quantile(sorted_arr, 0.05))
    best5 = float(np.quantile(sorted_arr, 0.95))

    buckets = _build_histogram(sorted_arr, 60)

    return {
        "ok": True,
        "input": {
            "currentPrice": current,
            "takeProfit": tp,
            "stopLoss": sl,
            "mu": p["mu"],
            "annualVolatility": sigma,
            "daysForecast": p["daysForecast"],
            "simulations": n,
            "steps": steps,
            "exhaustionScore": exhaustion_component,
            "spotFlow": p["spotFlow"],
            "oiFlow": p["oiFlow"],
            "shortLiqAbove": short_above,
            "longLiqBelow": long_below,
            "lambda": p["lambda"],
        },
        "liquidity": {
            "liquidityPressure": liq_pressure,
            "liquidationMagnet": liq_magnet,
            "muAdjusted": mu_adj,
        },
        "probabilities": {
            "probDown": prob_down,
            "probTP": prob_tp,
            "probSL": prob_sl,
        },
        "trade": {
            "expectedValue": expected_value,
            "expectedValuePct": expected_value_pct,
            "gain": gain,
            "loss": loss,
            "riskReward": rr,
        },
        "score": {
            "shortEntryScore": score,
            "scoreRaw": score_raw,
            "scorePercent": round(score * 100),
            "status": status,
            "components": {
                "exhaustionComponent": exhaustion_component,
                "rrScore": rr_score,
                "evScore": ev_score,
                "probDown": prob_down,
                "probTP": prob_tp,
                "probSL": prob_sl,
                "directionalEdge": directional_edge,
            },
        },
        "stats": {"median": median, "mean": mean_p, "worst5": worst5, "best5": best5},
        "chart": {"buckets": buckets},
  }

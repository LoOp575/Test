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


def _symbol_variants(symbol: str) -> tuple[str, str]:
    sym = symbol.upper().strip()
    base = sym[:-4] if sym.endswith("USDT") else sym
    return f"{base}USDT", f"{base}-USDT"


def _base_symbol(symbol: str) -> str:
    sym = symbol.upper().strip()
    return sym[:-4] if sym.endswith("USDT") else sym


def _quality_profile(*, has_kline: bool, has_taker: bool, has_market_cap: bool) -> dict[str, Any]:
    volume_quality = "REAL" if has_kline else "ESTIMATED"
    buyer_quality = "REAL" if has_taker else ("PROXY" if has_kline else "ESTIMATED")
    market_cap_quality = "REAL" if has_market_cap else "MISSING"

    score = 0
    score += 30 if has_kline else 8
    score += 25 if has_taker else (12 if has_kline else 6)
    score += 20 if has_market_cap else 0
    # Reserved for future modules.
    score += 0  # order book depth
    score += 0  # derivatives pressure

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
        "orderBookDepth": "MISSING",
        "derivatives": "MISSING",
    }


def _with_quality(market: dict, *, has_kline: bool, has_taker: bool, has_market_cap: bool) -> dict:
    quality = _quality_profile(has_kline=has_kline, has_taker=has_taker, has_market_cap=has_market_cap)
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
    mean_return = sum(returns) / len(returns)
    variance = sum((r - mean_return) ** 2 for r in returns) / max(1, len(returns) - 1)
    sigma = math.sqrt(max(variance, 0.0))
    scale = math.sqrt(24.0) if interval == "1h" else 1.0
    mean_scale = 24.0 if interval == "1h" else 1.0
    return {
        "historicalReturns": [round(r, 8) for r in returns[-24:]],
        "historicalReturnInterval": interval,
        "historicalVolDaily": round(sigma * scale, 8),
        "historicalMeanDaily": round(mean_return * mean_scale, 8),
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
        # Keep a buyer pressure number for the AI, but mark it as proxy in dataQuality.
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
    }
    return _with_quality(estimated, has_kline=False, has_taker=False, has_market_cap=_to_float(estimated.get("marketCap")) > 0)


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


async def _add_market_cap_and_quality(client: httpx.AsyncClient, market: dict, *, has_kline: bool, has_taker: bool) -> dict:
    current_cap = _to_float(market.get("marketCap") or market.get("market_cap"))
    enriched = dict(market)
    if current_cap <= 0:
        cap = await _fetch_market_cap(client, str(market.get("symbol") or ""))
        if cap:
            enriched.update(cap)
            current_cap = _to_float(enriched.get("marketCap"))
    return _with_quality(enriched, has_kline=has_kline, has_taker=has_taker, has_market_cap=current_cap > 0)


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
                return await _add_market_cap_and_quality(client, {**market, **metrics}, has_kline=True, has_taker=has_taker)
            errors.append(f"{source}: insufficient klines")
        except Exception as exc:
            errors.append(f"{source}: {type(exc).__name__}")
    estimated = _estimate_fuel_metrics(market, "; ".join(errors[:5]) or "klines unavailable")
    return await _add_market_cap_and_quality(client, estimated, has_kline=False, has_taker=False)


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
            mean_return = sum(values) / len(values)
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


def _patch_agent_payloads() -> None:
    if not hasattr(agent, "_market_fuel_original_compact_payload"):
        agent._market_fuel_original_compact_payload = agent._compact_payload
    if not hasattr(agent, "_market_fuel_original_rule_based_analysis"):
        agent._market_fuel_original_rule_based_analysis = agent._rule_based_analysis

    original_compact = agent._market_fuel_original_compact_payload
    original_rule = agent._market_fuel_original_rule_based_analysis

    def compact_payload_with_data_quality(body: dict) -> dict:
        payload = original_compact(body)
        raw_market = body.get("market") or {}
        market = payload.setdefault("market", {})
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
        return payload

    def rule_based_with_data_quality(payload: dict) -> str:
        text = original_rule(payload)
        market = payload.get("market") or {}
        bull = payload.get("bullishCounterScenario") or {}
        vf = bull.get("volumeFuel") or {}
        dq = payload.get("dataQuality") or bull.get("dataQuality") or market.get("dataQuality") or {}
        confidence = vf.get("dataConfidencePercent") or market.get("dataConfidencePercent") or dq.get("confidencePercent") or 0
        overall = vf.get("dataQualityOverall") or market.get("dataQualityOverall") or dq.get("overall") or "unknown"
        lines = [
            "",
            "## Data Quality / Buyer Pressure",
            f"- Data confidence: {confidence}% ({overall})",
            f"- Volume trend 3h quality: {dq.get('volumeTrend3h', 'unknown')}",
            f"- Buyer pressure 3h: {vf.get('buyerPressure3h', market.get('buyerPressure3h', 0))} ({vf.get('buyerPressureSource', market.get('buyerPressureSource', 'unknown'))})",
            f"- Taker buy/sell 3h: {_fmt_money(vf.get('takerBuyQuoteVolume3h', 0))} / {_fmt_money(vf.get('takerSellQuoteVolume3h', 0))}",
            f"- Market cap quality: {dq.get('marketCap', 'unknown')} | Order book: {dq.get('orderBookDepth', 'MISSING')} | Derivatives: {dq.get('derivatives', 'MISSING')}",
        ]
        return text + "\n" + "\n".join(lines)

    def _fmt_money(value: Any) -> str:
        try:
            return agent._compact_money(float(value or 0))
        except Exception:
            return "$0"

    agent._compact_payload = compact_payload_with_data_quality
    agent._rule_based_analysis = rule_based_with_data_quality


def apply_runtime_patches() -> None:
    server._enrich_market_with_fuel_metrics = enrich_market_with_fuel_metrics
    mc.build_simulation_params_from_market = build_simulation_params_from_market
    server.build_simulation_params_from_market = build_simulation_params_from_market
    _patch_agent_payloads()

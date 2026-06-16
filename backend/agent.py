"""
AI Market Fuel Checker.

Monte Carlo tetap menjadi mesin angka. Modul ini membaca payload,
mengecek apakah pump masih punya bahan bakar, lalu memberi warning naratif.
"""

from __future__ import annotations

import json
import math
import os
from typing import Any

import httpx

try:
    from emergentintegrations.llm.chat import LlmChat, UserMessage  # type: ignore
    _EMG_AVAILABLE = True
except Exception:
    _EMG_AVAILABLE = False


def _safe_num(v: Any, decimals: int = 4) -> float:
    try:
        n = float(v)
        if not math.isfinite(n):
            return 0.0
        return round(n, decimals)
    except Exception:
        return 0.0


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, v))


def _compact_money(v: float) -> str:
    try:
        n = float(v)
    except Exception:
        return "$0"
    if abs(n) >= 1_000_000_000:
        return f"${n / 1_000_000_000:.2f}B"
    if abs(n) >= 1_000_000:
        return f"${n / 1_000_000:.2f}M"
    if abs(n) >= 1_000:
        return f"${n / 1_000:.2f}K"
    return f"${n:.2f}"


def _volume_status_from_pct(pct: float) -> str:
    if pct <= -25:
        return "declining"
    if pct >= 25:
        return "rising"
    if pct != 0:
        return "flat"
    return "unknown"


def _build_bullish_scenario(market: dict, results: dict) -> dict:
    price = _safe_num(market.get("lastPrice"), 12)
    quote_volume = _safe_num(market.get("quoteVolume"), 2)
    market_cap = _safe_num(
        market.get("marketCap")
        or market.get("market_cap")
        or market.get("fdv")
        or market.get("fullyDilutedValuation"),
        2,
    )
    change24 = _safe_num(market.get("priceChangePercent"), 4)
    vol24 = _safe_num(market.get("volatility24h"), 6)
    prob = (results or {}).get("probabilities") or {}
    score = (results or {}).get("score") or {}

    volume_trend_pct = _safe_num(market.get("volumeTrendPercent"), 2)
    volume_status = market.get("volumeTrendStatus") or _volume_status_from_pct(volume_trend_pct)
    close_strength = _safe_num(market.get("closeStrength3h"), 4)
    rejection_score = _safe_num(market.get("rejectionScore"), 4)
    upper_wick = _safe_num(market.get("upperWickRatio3h"), 4)
    fuel_signal = market.get("fuelSignal") or "unknown"

    target_2x = price * 2 if price > 0 else 0
    target_150 = price * 1.5 if price > 0 else 0
    target_120 = price * 1.2 if price > 0 else 0

    if market_cap > 0:
        current_cap_proxy = market_cap
        cap_note = "market_cap_available"
    else:
        current_cap_proxy = quote_volume * 2.5 if quote_volume > 0 else 0
        cap_note = "market_cap_estimated_from_volume_proxy"

    needed_cap_120 = current_cap_proxy * 1.2
    needed_cap_150 = current_cap_proxy * 1.5
    needed_cap_2x = current_cap_proxy * 2
    cap_gap_120 = max(0, needed_cap_120 - current_cap_proxy)
    cap_gap_150 = max(0, needed_cap_150 - current_cap_proxy)
    cap_gap_2x = max(0, needed_cap_2x - current_cap_proxy)

    vol_to_mcap_ratio = quote_volume / market_cap if market_cap > 0 and quote_volume > 0 else 0
    req_vol_120 = cap_gap_120 * 0.35
    req_vol_150 = cap_gap_150 * 0.35
    req_vol_2x = cap_gap_2x * 0.35
    volume_gap_ratio = req_vol_2x / quote_volume if quote_volume > 0 else 0

    volume_decay_score = 0.0
    if volume_status == "declining":
        volume_decay_score += _clamp(abs(volume_trend_pct) / 45) * 0.45
    if rejection_score > 0:
        volume_decay_score += _clamp(rejection_score / 0.65) * 0.35
    if close_strength > 0:
        volume_decay_score += _clamp((0.50 - close_strength) / 0.50) * 0.20
    volume_decay_score = _clamp(volume_decay_score)

    fuel_strength_score = 0.0
    if volume_status == "rising":
        fuel_strength_score += _clamp(volume_trend_pct / 60) * 0.45
    if close_strength > 0:
        fuel_strength_score += _clamp(close_strength / 0.75) * 0.35
    if change24 > 0:
        fuel_strength_score += _clamp(change24 / 30) * 0.20
    fuel_strength_score = _clamp(fuel_strength_score)

    continuation_risk_score = 0.0
    continuation_risk_score += min(max(change24, 0) / 30, 1) * 0.20
    continuation_risk_score += min(vol24 / 0.40, 1) * 0.18
    continuation_risk_score += min(vol_to_mcap_ratio, 1) * 0.12
    continuation_risk_score += min(max((prob.get("probSL") or 0), 0), 1) * 0.20
    continuation_risk_score += fuel_strength_score * 0.25
    continuation_risk_score -= volume_decay_score * 0.25
    continuation_risk_score += (1 if (score.get("status") == "DANGER_STOP_RISK") else 0) * 0.10
    continuation_risk_score = _clamp(continuation_risk_score)

    if continuation_risk_score >= 0.70:
        risk_label = "high"
    elif continuation_risk_score >= 0.45:
        risk_label = "medium"
    elif continuation_risk_score >= 0.25:
        risk_label = "low-medium"
    else:
        risk_label = "low"

    if volume_decay_score >= 0.68:
        fuel_label = "FUEL EXHAUSTED"
    elif volume_decay_score >= 0.45:
        fuel_label = "FUEL WEAKENING"
    elif continuation_risk_score >= 0.65 or fuel_strength_score >= 0.60:
        fuel_label = "FUEL STRONG"
    elif continuation_risk_score >= 0.40:
        fuel_label = "FUEL MEDIUM"
    else:
        fuel_label = "FUEL WEAKENING"

    edge = max(0, (prob.get("probTP") or 0) - (prob.get("probSL") or 0))
    short_validation_score = _clamp(
        0.35 * volume_decay_score
        + 0.25 * _clamp((results.get("input", {}).get("exhaustionScore") or 0))
        + 0.20 * max(0, (prob.get("probDown") or 0) - 0.5) * 2
        + 0.20 * _clamp(edge / 0.30)
    )

    if short_validation_score >= 0.65 and continuation_risk_score < 0.50:
        short_read = "short_valid_after_rejection"
    elif volume_decay_score >= 0.45:
        short_read = "short_watch_fuel_weakening"
    elif continuation_risk_score >= 0.60:
        short_read = "do_not_short_aggressively_fuel_alive"
    else:
        short_read = "neutral_watch"

    return {
        "fuelLabel": fuel_label,
        "shortRead": short_read,
        "shortValidationScore": _safe_num(short_validation_score * 100, 2),
        "volumeFuel": {
            "volumeTrendPercent": volume_trend_pct,
            "volumeTrendStatus": volume_status,
            "recentQuoteVolume3h": _safe_num(market.get("recentQuoteVolume3h"), 2),
            "previousQuoteVolume3h": _safe_num(market.get("previousQuoteVolume3h"), 2),
            "closeStrength3h": close_strength,
            "upperWickRatio3h": upper_wick,
            "rejectionScore": rejection_score,
            "volumeDecayScore": _safe_num(volume_decay_score * 100, 2),
            "fuelStrengthScore": _safe_num(fuel_strength_score * 100, 2),
            "fuelSignal": fuel_signal,
            "source": market.get("fuelMetricsSource") or "unknown",
        },
        "bullishTargets": {
            "plus20pctPrice": _safe_num(target_120, 12),
            "plus50pctPrice": _safe_num(target_150, 12),
            "doublePrice": _safe_num(target_2x, 12),
        },
        "marketCapScenario": {
            "mode": cap_note,
            "currentMarketCapOrProxy": _safe_num(current_cap_proxy, 2),
            "neededForPlus20pct": _safe_num(needed_cap_120, 2),
            "neededForPlus50pct": _safe_num(needed_cap_150, 2),
            "neededForDouble": _safe_num(needed_cap_2x, 2),
            "additionalCapNeededForDouble": _safe_num(cap_gap_2x, 2),
        },
        "volumeScenario": {
            "quoteVolume24h": quote_volume,
            "volumeToMarketCapRatio": _safe_num(vol_to_mcap_ratio, 4),
            "roughRequiredVolumeForPlus20pct": _safe_num(req_vol_120, 2),
            "roughRequiredVolumeForPlus50pct": _safe_num(req_vol_150, 2),
            "roughRequiredVolumeForDouble": _safe_num(req_vol_2x, 2),
            "requiredVolumeVsCurrent24h": _safe_num(volume_gap_ratio, 4),
        },
        "narrativeRisk": {
            "continuationRiskScore": _safe_num(continuation_risk_score * 100, 2),
            "continuationRiskLabel": risk_label,
            "interpretation": "Cek apakah pump masih punya bahan bakar: volume trend, rejection, close strength, SL risk, dan syarat volume untuk target berikutnya.",
        },
    }


def _compact_payload(body: dict) -> dict:
    market = body.get("market") or {}
    auto = body.get("autoLevels") or {}
    exh = auto.get("exhaustion") or {}
    results = body.get("results") or {}
    prob = results.get("probabilities") or {}
    trade = results.get("trade") or {}
    score = results.get("score") or {}
    stats = results.get("stats") or {}
    liq = results.get("liquidity") or {}
    scenario = _build_bullish_scenario(market, results)
    mode = (body.get("mode") or body.get("agentMode") or "market_fuel").strip().lower()

    return {
        "analysisMode": mode,
        "token": market.get("symbol"),
        "market": {
            "price": _safe_num(market.get("lastPrice"), 10),
            "change24hPercent": _safe_num(market.get("priceChangePercent"), 4),
            "quoteVolume": _safe_num(market.get("quoteVolume"), 2),
            "marketCap": _safe_num(market.get("marketCap") or market.get("market_cap"), 2),
            "high24h": _safe_num(market.get("highPrice"), 10),
            "low24h": _safe_num(market.get("lowPrice"), 10),
            "volatility24h": _safe_num(market.get("volatility24h"), 6),
            "volumeTrendPercent": _safe_num(market.get("volumeTrendPercent"), 2),
            "volumeTrendStatus": market.get("volumeTrendStatus") or "unknown",
            "closeStrength3h": _safe_num(market.get("closeStrength3h"), 4),
            "upperWickRatio3h": _safe_num(market.get("upperWickRatio3h"), 4),
            "rejectionScore": _safe_num(market.get("rejectionScore"), 4),
            "fuelSignal": market.get("fuelSignal") or "unknown",
            "fuelMetricsSource": market.get("fuelMetricsSource") or "unknown",
        },
        "pumpExhaustion": {
            "phase": exh.get("phase"),
            "score": _safe_num((exh.get("exhaustionScore") or 0) * 100, 2),
            "pumpStrength": _safe_num((exh.get("pumpStrength") or 0) * 100, 2),
            "highPressure": _safe_num((exh.get("highPressure") or 0) * 100, 2),
            "volatilityStrength": _safe_num((exh.get("volatilityStrength") or 0) * 100, 2),
            "positionInRange": _safe_num((exh.get("positionInRange") or 0) * 100, 2),
            "wickRatio": _safe_num((exh.get("wickRatio") or 0) * 100, 2),
        },
        "autoLevels": {
            "takeProfit": _safe_num(auto.get("takeProfit"), 10),
            "stopLoss": _safe_num(auto.get("stopLoss"), 10),
            "riskReward": _safe_num(auto.get("riskReward"), 4),
            "pullbackPct": _safe_num((auto.get("pullbackPct") or 0) * 100, 2),
            "stopBufferPct": _safe_num((auto.get("stopBufferPct") or 0) * 100, 2),
        },
        "monteCarlo": {
            "score": score.get("scorePercent"),
            "status": score.get("status"),
            "probabilityDown": _safe_num((prob.get("probDown") or 0) * 100, 2),
            "probabilityTP": _safe_num((prob.get("probTP") or 0) * 100, 2),
            "probabilitySL": _safe_num((prob.get("probSL") or 0) * 100, 2),
            "expectedValue": _safe_num(trade.get("expectedValue"), 10),
            "expectedValuePct": _safe_num((trade.get("expectedValuePct") or 0) * 100, 4),
            "riskReward": _safe_num(trade.get("riskReward"), 4),
            "muAdjusted": _safe_num(liq.get("muAdjusted"), 6),
            "liquidityPressure": _safe_num(liq.get("liquidityPressure"), 6),
            "meanPrice": _safe_num(stats.get("mean"), 10),
            "medianPrice": _safe_num(stats.get("median"), 10),
            "worst5": _safe_num(stats.get("worst5"), 10),
            "best5": _safe_num(stats.get("best5"), 10),
        },
        "bullishCounterScenario": scenario,
    }


def _fuel_volume_interpretation(vol: dict) -> str:
    required_vs_current = vol.get("requiredVolumeVsCurrent24h", 0)
    if required_vs_current >= 2:
        return "berat: butuh dorongan volume jauh di atas kondisi 24h sekarang."
    if required_vs_current >= 1:
        return "sedang: butuh volume sekitar setara/lebih besar dari 24h saat ini."
    if required_vs_current > 0:
        return "ringan-menengah: volume 24h saat ini relatif masih cukup mendukung continuation."
    return "belum bisa dihitung akurat karena data market cap/volume terbatas."


def _short_read_sentence(code: str) -> str:
    return {
        "short_valid_after_rejection": "Fuel melemah dan short mulai valid setelah ada rejection/lower-high.",
        "short_watch_fuel_weakening": "Fuel mulai melemah, tapi tetap tunggu konfirmasi agar tidak kena final spike.",
        "do_not_short_aggressively_fuel_alive": "Fuel masih hidup. Short agresif berbahaya karena risiko stop hunt/continuation masih besar.",
        "neutral_watch": "Masih watchlist. Sinyal belum cukup bersih.",
    }.get(code, "Masih watchlist. Sinyal belum cukup bersih.")


def _rule_based_analysis(payload: dict) -> str:
    mc = payload["monteCarlo"]
    ex = payload["pumpExhaustion"]
    market = payload["market"]
    token = payload["token"]
    bull = payload.get("bullishCounterScenario") or {}
    targets = bull.get("bullishTargets") or {}
    cap = bull.get("marketCapScenario") or {}
    vol = bull.get("volumeScenario") or {}
    narrative = bull.get("narrativeRisk") or {}
    volume_fuel = bull.get("volumeFuel") or {}

    fuel_label = bull.get("fuelLabel") or "FUEL UNKNOWN"
    short_read = bull.get("shortRead") or "neutral_watch"
    cont_label = narrative.get("continuationRiskLabel", "unknown")
    cont_score = narrative.get("continuationRiskScore", 0)
    volume_interpretation = _fuel_volume_interpretation(vol)

    volume_status = volume_fuel.get("volumeTrendStatus", "unknown")
    volume_pct = volume_fuel.get("volumeTrendPercent", 0)
    rejection = volume_fuel.get("rejectionScore", 0)
    decay = volume_fuel.get("volumeDecayScore", 0)
    strength = volume_fuel.get("fuelStrengthScore", 0)
    source = volume_fuel.get("source", "unknown")

    if volume_status == "declining" and rejection >= 0.45:
        volume_read = "Volume 3 jam terakhir menurun dan rejection cukup jelas. Ini mendukung logika pump kehilangan tenaga."
    elif volume_status == "declining":
        volume_read = "Volume 3 jam terakhir menurun. Fuel melemah, tapi tetap butuh rejection/struktur lower-high."
    elif volume_status == "rising" and strength >= 55:
        volume_read = "Volume dan close strength masih mendukung buyer. Short agresif berisiko."
    elif volume_status == "rising" and rejection >= 0.45:
        volume_read = "Volume naik tetapi harga gagal close kuat. Ini bisa menandakan distribusi."
    else:
        volume_read = "Volume trend belum memberi konfirmasi kuat. Tetap gunakan wick, close strength, dan Monte Carlo sebagai filter."

    return "\n".join([
        f"## Market Fuel Status — {token}",
        f"**{fuel_label}** | Short validation **{bull.get('shortValidationScore', 0)}/100** | Continuation risk **{cont_label}** ({cont_score}/100).",
        "",
        "## Volume & Buyer Pressure",
        f"- Source fuel data: {source}",
        f"- Volume trend 3h: **{volume_status}** ({volume_pct}%)",
        f"- Rejection score: {rejection} | Volume decay: {decay}/100 | Fuel strength: {strength}/100",
        f"- Close strength 3h: {volume_fuel.get('closeStrength3h', 0)} | Upper wick 3h: {volume_fuel.get('upperWickRatio3h', 0)}",
        f"- Bacaan: {volume_read}",
        "",
        "## Required Fuel for Next Pump",
        f"- Harga sekarang: ${market['price']} ({market['change24hPercent']}% 24h)",
        f"- Target +20%: ${targets.get('plus20pctPrice', 0)} membutuhkan rough volume: {_compact_money(vol.get('roughRequiredVolumeForPlus20pct', 0))}",
        f"- Target +50%: ${targets.get('plus50pctPrice', 0)} membutuhkan rough volume: {_compact_money(vol.get('roughRequiredVolumeForPlus50pct', 0))}",
        f"- Target 2x: ${targets.get('doublePrice', 0)} membutuhkan rough volume: {_compact_money(vol.get('roughRequiredVolumeForDouble', 0))} — {volume_interpretation}",
        f"- Market cap/proxy sekarang: {_compact_money(cap.get('currentMarketCapOrProxy', 0))}",
        "",
        "## Exhaustion / Rejection Signal",
        f"- Exhaustion: {ex.get('score', 0)}/100 | Pump strength: {ex.get('pumpStrength', 0)}/100",
        f"- Posisi range 24h: {ex.get('positionInRange', 0)}% | Wick ratio base: {ex.get('wickRatio', 0)}%",
        "",
        "## Monte Carlo vs Fuel",
        f"- Short score: {mc.get('score')}/100 ({mc.get('status')})",
        f"- Probability Down: {mc.get('probabilityDown')}% | TP: {mc.get('probabilityTP')}% | SL: {mc.get('probabilitySL')}%",
        f"- Risk/Reward: {mc.get('riskReward')}x | EV: {mc.get('expectedValuePct')}%",
        "",
        "## Final Read",
        _short_read_sentence(short_read),
        "",
        "_Disclaimer: ini pembacaan fuel probabilistik untuk edukasi, bukan instruksi entry pasti._",
    ])


def _agent_prompts(payload: dict) -> tuple[str, str]:
    mode = payload.get("analysisMode") or "market_fuel"
    if mode == "market_fuel":
        system_prompt = (
            "Kamu adalah AI Market Fuel Checker untuk dashboard edukatif crypto short-hunter. "
            "Tugas utama: cek apakah token yang sudah pump masih punya bahan bakar untuk lanjut naik, "
            "atau fuel-nya mulai habis sehingga sinyal short lebih masuk akal. "
            "WAJIB bahas volume trend 3h, rejection score, close strength, required volume untuk +20%/+50%/2x, "
            "pump exhaustion, probability SL, dan continuation risk. "
            "Kalau volume menurun saat harga masih tinggi dan rejection muncul, jelaskan bahwa short makin valid setelah konfirmasi. "
            "Kalau volume naik dan close strength masih kuat, jelaskan risiko stop hunt/continuation. "
            "JANGAN beri financial advice atau instruksi entry pasti. Bahasa Indonesia jelas dan praktis."
        )
        user_prompt = (
            "Analisis payload berikut sebagai MARKET FUEL CHECKER. Gunakan format markdown ini:\n"
            "## Market Fuel Status\n"
            "## Volume & Buyer Pressure\n"
            "## Required Fuel for Next Pump\n"
            "## Exhaustion / Rejection Signal\n"
            "## Monte Carlo vs Fuel\n"
            "## Final Read\n\n"
            f"Payload:\n{json.dumps(payload, indent=2)}"
        )
        return system_prompt, user_prompt

    system_prompt = "Kamu adalah quantitative market analyst untuk dashboard edukatif crypto. Jangan beri financial advice."
    user_prompt = f"Analisis token berikut dalam format markdown:\n{json.dumps(payload, indent=2)}"
    return system_prompt, user_prompt


async def _openai_compatible_chat(*, endpoint: str, api_key: str, model: str, payload: dict, source: str, max_tokens: int = 1200) -> dict | None:
    system_prompt, user_prompt = _agent_prompts(payload)
    try:
        async with httpx.AsyncClient(timeout=25.0) as c:
            r = await c.post(
                endpoint,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={
                    "model": model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": 0.25,
                    "max_tokens": max_tokens,
                },
            )
        if r.status_code != 200:
            return {f"_{source}_error": f"HTTP {r.status_code}: {r.text[:160]}"}
        j = r.json()
        content = (
            (j.get("choices") or [{}])[0].get("message", {}).get("content")
            or (j.get("choices") or [{}])[0].get("text")
            or j.get("message")
            or j.get("content")
        )
        if not content:
            return {f"_{source}_error": "empty content"}
        return {"source": source, "model": model, "analysis": content}
    except Exception as e:
        return {f"_{source}_error": f"{type(e).__name__}: {e}"}


async def _try_aixchia(payload: dict) -> dict | None:
    api_key = os.getenv("AIXCHIA_API_KEY") or os.getenv("AIXCHIAAPIKEY")
    if not api_key:
        return None
    api_url = (os.getenv("AIXCHIA_API_URL") or os.getenv("AIXCHIAAPIURL") or "https://www.aichixia.xyz/api/v1").rstrip("/")
    model = os.getenv("AIXCHIA_MODEL") or os.getenv("AIXCHIAMODEL") or "gpt-5-mini"
    return await _openai_compatible_chat(endpoint=f"{api_url}/chat/completions", api_key=api_key, model=model, payload=payload, source="aixchia")


async def _try_0g_minimax(payload: dict) -> dict | None:
    api_key = os.getenv("OG_API_KEY") or os.getenv("OGAI_API_KEY") or os.getenv("ZERO_G_API_KEY") or os.getenv("ZEROG_API_KEY") or os.getenv("0G_API_KEY")
    if not api_key:
        return None
    endpoint = (os.getenv("OG_API_URL") or os.getenv("OGAI_API_URL") or "https://router-api.0g.ai/v1/chat/completions").rstrip("/")
    model = os.getenv("OG_MODEL") or os.getenv("OGAI_MODEL") or os.getenv("MINIMAX_MODEL") or "minimax"
    return await _openai_compatible_chat(endpoint=endpoint, api_key=api_key, model=model, payload=payload, source="0g-minimax")


async def _try_emergent(payload: dict, session_id: str) -> dict | None:
    if not _EMG_AVAILABLE:
        return None
    api_key = os.getenv("EMERGENT_LLM_KEY")
    if not api_key:
        return None
    system_msg, user_text = _agent_prompts(payload)
    try:
        chat = LlmChat(api_key=api_key, session_id=session_id, system_message=system_msg).with_model("anthropic", "claude-sonnet-4-6")
        resp = await chat.send_message(UserMessage(text=user_text))
        if not resp:
            return None
        return {"source": "emergent-llm", "model": "claude-sonnet-4-6", "analysis": str(resp)}
    except Exception as e:
        return {"_emergent_error": f"{type(e).__name__}: {e}"}


async def generate_agent_analysis(body: dict) -> dict:
    payload = _compact_payload(body)
    if not payload.get("token"):
        return {"source": "error", "analysis": "Missing token payload.", "payload": payload}

    provider = (body.get("provider") or body.get("aiProvider") or "0g-minimax").strip().lower()
    session_id = f"analysis-{payload['token']}"
    warnings: list[str] = []

    async def use_aixchia():
        out = await _try_aixchia(payload)
        if out and out.get("analysis"):
            return out
        if out and out.get("_aixchia_error"):
            warnings.append(f"AIXCHIA: {out['_aixchia_error']}")
        return None

    async def use_0g():
        out = await _try_0g_minimax(payload)
        if out and out.get("analysis"):
            return out
        if out and out.get("_0g-minimax_error"):
            warnings.append(f"0G MiniMax: {out['_0g-minimax_error']}")
        return None

    async def use_emergent():
        out = await _try_emergent(payload, session_id)
        if out and out.get("analysis"):
            return out
        if out and out.get("_emergent_error"):
            warnings.append(f"Emergent: {out['_emergent_error']}")
        return None

    if provider in ("0g", "0g-minimax", "og", "minimax"):
        result = await use_0g()
        if result:
            return {**result, "mode": payload.get("analysisMode"), "payload": payload}
        warnings.append("Selected provider 0G MiniMax unavailable; fallback Market Fuel rule-based aktif.")
    elif provider in ("aixchia", "gpt", "default"):
        result = await use_aixchia()
        if result:
            return {**result, "mode": payload.get("analysisMode"), "payload": payload}
        warnings.append("Selected provider AIXCHIA unavailable; fallback Market Fuel rule-based aktif.")
    elif provider in ("emergent", "claude", "emergent-llm"):
        result = await use_emergent()
        if result:
            return {**result, "mode": payload.get("analysisMode"), "payload": payload}
        warnings.append("Selected provider Emergent unavailable; fallback Market Fuel rule-based aktif.")
    else:
        for runner in (use_0g, use_aixchia, use_emergent):
            result = await runner()
            if result:
                return {**result, "mode": payload.get("analysisMode"), "payload": payload}

    return {
        "source": "rule-based",
        "model": "local-fallback",
        "mode": payload.get("analysisMode"),
        "analysis": _rule_based_analysis(payload),
        "payload": payload,
        "warning": " | ".join(warnings) if warnings else "AI key tidak tersedia. Menggunakan Market Fuel rule-based.",
    }

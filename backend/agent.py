"""
AI Market Fuel Checker.

Monte Carlo tetap menjadi mesin angka. Modul ini hanya membaca payload,
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

    target_2x = price * 2 if price > 0 else 0
    target_150 = price * 1.5 if price > 0 else 0
    target_120 = price * 1.2 if price > 0 else 0

    if market_cap > 0:
        current_cap_proxy = market_cap
        needed_cap_2x = market_cap * 2
        needed_cap_150 = market_cap * 1.5
        cap_gap_2x = needed_cap_2x - market_cap
        cap_note = "market_cap_available"
    else:
        current_cap_proxy = quote_volume * 2.5 if quote_volume > 0 else 0
        needed_cap_2x = current_cap_proxy * 2
        needed_cap_150 = current_cap_proxy * 1.5
        cap_gap_2x = needed_cap_2x - current_cap_proxy
        cap_note = "market_cap_estimated_from_volume_proxy"

    vol_to_mcap_ratio = quote_volume / market_cap if market_cap > 0 and quote_volume > 0 else 0
    required_volume_for_2x = max(0, cap_gap_2x * 0.35)
    volume_gap_ratio = required_volume_for_2x / quote_volume if quote_volume > 0 else 0

    continuation_risk_score = 0.0
    continuation_risk_score += min(max(change24, 0) / 30, 1) * 0.25
    continuation_risk_score += min(vol24 / 0.40, 1) * 0.25
    continuation_risk_score += min(vol_to_mcap_ratio, 1) * 0.20
    continuation_risk_score += min(max((prob.get("probSL") or 0), 0), 1) * 0.20
    continuation_risk_score += (1 if (score.get("status") == "DANGER_STOP_RISK") else 0) * 0.10
    continuation_risk_score = max(0.0, min(1.0, continuation_risk_score))

    if continuation_risk_score >= 0.70:
        risk_label = "high"
        fuel_label = "FUEL STRONG / DANGEROUS CONTINUATION"
    elif continuation_risk_score >= 0.45:
        risk_label = "medium"
        fuel_label = "FUEL MEDIUM"
    elif continuation_risk_score >= 0.25:
        risk_label = "low-medium"
        fuel_label = "FUEL WEAKENING"
    else:
        risk_label = "low"
        fuel_label = "FUEL EXHAUSTED"

    return {
        "fuelLabel": fuel_label,
        "bullishTargets": {
            "plus20pctPrice": _safe_num(target_120, 12),
            "plus50pctPrice": _safe_num(target_150, 12),
            "doublePrice": _safe_num(target_2x, 12),
        },
        "marketCapScenario": {
            "mode": cap_note,
            "currentMarketCapOrProxy": _safe_num(current_cap_proxy, 2),
            "neededForPlus50pct": _safe_num(needed_cap_150, 2),
            "neededForDouble": _safe_num(needed_cap_2x, 2),
            "additionalCapNeededForDouble": _safe_num(cap_gap_2x, 2),
        },
        "volumeScenario": {
            "quoteVolume24h": quote_volume,
            "volumeToMarketCapRatio": _safe_num(vol_to_mcap_ratio, 4),
            "roughRequiredVolumeForDouble": _safe_num(required_volume_for_2x, 2),
            "requiredVolumeVsCurrent24h": _safe_num(volume_gap_ratio, 4),
        },
        "narrativeRisk": {
            "continuationRiskScore": _safe_num(continuation_risk_score * 100, 2),
            "continuationRiskLabel": risk_label,
            "interpretation": "Cek apakah pump masih punya bahan bakar: volume, volatility, posisi dekat high, SL risk, dan narasi continuation.",
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

    fuel_label = bull.get("fuelLabel") or "FUEL UNKNOWN"
    cont_label = narrative.get("continuationRiskLabel", "unknown")
    cont_score = narrative.get("continuationRiskScore", 0)
    volume_interpretation = _fuel_volume_interpretation(vol)

    if cont_label in ("high", "medium") and (mc.get("probabilitySL") or 0) >= 45:
        short_read = "Short belum bersih. Fuel/continuation risk masih bisa menyapu stop sebelum turun."
    elif cont_label in ("low", "low-medium") and (ex.get("score") or 0) >= 50:
        short_read = "Fuel mulai melemah. Jika muncul rejection/lower-high, short setup lebih masuk akal."
    else:
        short_read = "Masih watchlist. Perlu konfirmasi price action sebelum percaya sinyal."

    return "\n".join([
        f"## Market Fuel Status — {token}",
        f"**{fuel_label}** dengan continuation risk **{cont_label}** ({cont_score}/100).",
        "",
        "## Fuel Data",
        f"- Harga sekarang: ${market['price']} ({market['change24hPercent']}% 24h)",
        f"- Volume 24h: {_compact_money(market.get('quoteVolume', 0))}",
        f"- Volatility 24h: {market.get('volatility24h', 0)}",
        f"- Exhaustion: {ex.get('score', 0)}/100 | Pump strength: {ex.get('pumpStrength', 0)}/100",
        f"- Posisi range 24h: {ex.get('positionInRange', 0)}% | Wick ratio: {ex.get('wickRatio', 0)}%",
        "",
        "## Bullish Fuel Target",
        f"- Target +20%: ${targets.get('plus20pctPrice', 0)}",
        f"- Target +50%: ${targets.get('plus50pctPrice', 0)}",
        f"- Target 2x: ${targets.get('doublePrice', 0)}",
        f"- Market cap/proxy sekarang: {_compact_money(cap.get('currentMarketCapOrProxy', 0))}",
        f"- Estimasi cap/proxy untuk 2x: {_compact_money(cap.get('neededForDouble', 0))}",
        f"- Rough required volume 2x: {_compact_money(vol.get('roughRequiredVolumeForDouble', 0))} — {volume_interpretation}",
        "",
        "## Monte Carlo vs Fuel",
        f"- Short score: {mc.get('score')}/100 ({mc.get('status')})",
        f"- Probability Down: {mc.get('probabilityDown')}% | TP: {mc.get('probabilityTP')}% | SL: {mc.get('probabilitySL')}%",
        f"- Risk/Reward: {mc.get('riskReward')}x | EV: {mc.get('expectedValuePct')}%",
        "",
        "## Final Read",
        short_read,
        "",
        "_Disclaimer: ini pembacaan fuel probabilistik untuk edukasi, bukan instruksi entry pasti._",
    ])


def _agent_prompts(payload: dict) -> tuple[str, str]:
    mode = payload.get("analysisMode") or "market_fuel"
    if mode == "market_fuel":
        system_prompt = (
            "Kamu adalah AI Market Fuel Checker untuk dashboard edukatif crypto short-hunter. "
            "Tugasmu BUKAN sekadar menjelaskan Monte Carlo. Tugas utama: cek apakah token yang sudah pump masih punya bahan bakar untuk lanjut naik, "
            "atau fuel-nya mulai habis sehingga sinyal short lebih masuk akal. "
            "Baca volume 24h, market cap/proxy, target +20%/+50%/2x, volatility, pump exhaustion, probability SL, dan continuation risk. "
            "Monte Carlo adalah mesin angka; Market Fuel Checker adalah validasi narasi lawan. "
            "JANGAN beri financial advice atau instruksi entry pasti. Bahasa Indonesia jelas, tajam, dan praktis."
        )
        user_prompt = (
            "Analisis payload berikut sebagai MARKET FUEL CHECKER. Gunakan format markdown ini persis:\n"
            "## Market Fuel Status\n"
            "Berikan label: FUEL STRONG / FUEL MEDIUM / FUEL WEAKENING / FUEL EXHAUSTED.\n"
            "## Fuel Data\n"
            "Bahas volume, volatility, posisi range, wick, exhaustion.\n"
            "## Bullish Fuel Requirement\n"
            "Jelaskan syarat agar harga bisa lanjut +20%, +50%, atau 2x.\n"
            "## Monte Carlo vs Fuel\n"
            "Bandingkan short score dengan risiko continuation/SL.\n"
            "## Final Read\n"
            "Tegaskan apakah short bersih, watch, atau bahaya stop hunt.\n\n"
            f"Payload:\n{json.dumps(payload, indent=2)}"
        )
        return system_prompt, user_prompt

    system_prompt = (
        "Kamu adalah quantitative market analyst untuk dashboard edukatif crypto. "
        "JANGAN beri financial advice / instruksi entry pasti. Bahasa Indonesia profesional."
    )
    user_prompt = f"Analisis token berikut dalam format markdown:\n{json.dumps(payload, indent=2)}"
    return system_prompt, user_prompt


async def _openai_compatible_chat(
    *, endpoint: str, api_key: str, model: str, payload: dict, source: str, max_tokens: int = 1100
) -> dict | None:
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
    api_key = (
        os.getenv("OG_API_KEY")
        or os.getenv("OGAI_API_KEY")
        or os.getenv("ZERO_G_API_KEY")
        or os.getenv("ZEROG_API_KEY")
        or os.getenv("0G_API_KEY")
    )
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

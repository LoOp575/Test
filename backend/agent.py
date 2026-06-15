"""
AI Agent Analysis with cascading fallback:
1. AIXCHIA API
2. Emergent LLM (Claude Sonnet)
3. Rule-based fallback
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
        needed_cap_2x = market_cap * 2
        needed_cap_150 = market_cap * 1.5
        cap_gap_2x = needed_cap_2x - market_cap
        cap_note = "market_cap_available"
    else:
        # Kalau market cap belum ada, gunakan 24h volume sebagai proxy kasar.
        # Ini bukan valuasi final, hanya bahan penjelasan agent.
        estimated_cap = quote_volume * 2.5 if quote_volume > 0 else 0
        needed_cap_2x = estimated_cap * 2
        needed_cap_150 = estimated_cap * 1.5
        cap_gap_2x = needed_cap_2x - estimated_cap
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
    elif continuation_risk_score >= 0.45:
        risk_label = "medium"
    else:
        risk_label = "low"

    return {
        "bullishTargets": {
            "plus20pctPrice": _safe_num(target_120, 12),
            "plus50pctPrice": _safe_num(target_150, 12),
            "doublePrice": _safe_num(target_2x, 12),
        },
        "marketCapScenario": {
            "mode": cap_note,
            "currentMarketCapOrProxy": _safe_num(market_cap if market_cap > 0 else quote_volume * 2.5, 2),
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
            "interpretation": "Cek apakah pump masih punya bahan bakar: volume, narasi, listing/news, dan buyer continuation.",
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

    return {
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


def _rule_based_analysis(payload: dict) -> str:
    mc = payload["monteCarlo"]
    ex = payload["pumpExhaustion"]
    auto = payload["autoLevels"]
    market = payload["market"]
    token = payload["token"]
    bull = payload.get("bullishCounterScenario") or {}
    targets = bull.get("bullishTargets") or {}
    cap = bull.get("marketCapScenario") or {}
    vol = bull.get("volumeScenario") or {}
    narrative = bull.get("narrativeRisk") or {}

    positives, negatives = [], []

    if ex["score"] >= 70:
        positives.append(f"Pump exhaustion tinggi ({ex['score']}/100, phase: {ex['phase']}).")
    elif ex["score"] >= 50:
        positives.append(f"Pump exhaustion moderat ({ex['score']}/100).")
    else:
        negatives.append(f"Pump exhaustion belum ekstrem ({ex['score']}/100).")

    if mc["probabilityDown"] >= 60:
        positives.append(f"Probability Down kuat ({mc['probabilityDown']}%).")
    elif mc["probabilityDown"] >= 50:
        positives.append(f"Probability Down marginal ({mc['probabilityDown']}%).")
    else:
        negatives.append(f"Probability Down rendah ({mc['probabilityDown']}%).")

    if mc["probabilityTP"] >= 50:
        positives.append(f"Probability TP cukup baik ({mc['probabilityTP']}%).")
    else:
        negatives.append(f"Probability TP rendah ({mc['probabilityTP']}%).")

    if mc["probabilitySL"] <= 25:
        positives.append(f"Probability SL terkontrol ({mc['probabilitySL']}%).")
    elif mc["probabilitySL"] <= 40:
        negatives.append(f"Probability SL menengah ({mc['probabilitySL']}%).")
    else:
        negatives.append(f"Probability SL tinggi ({mc['probabilitySL']}%) — risiko stop hunt nyata.")

    if mc["riskReward"] >= 1.5:
        positives.append(f"Risk/Reward menarik ({mc['riskReward']}x).")
    elif mc["riskReward"] >= 1.0:
        positives.append(f"Risk/Reward seimbang ({mc['riskReward']}x).")
    else:
        negatives.append(f"Risk/Reward belum ideal ({mc['riskReward']}x).")

    if mc["expectedValue"] > 0:
        positives.append(f"Expected Value positif (+{mc['expectedValuePct']}% per trade).")
    else:
        negatives.append(f"Expected Value negatif ({mc['expectedValuePct']}% per trade).")

    cont_label = narrative.get("continuationRiskLabel", "unknown")
    cont_score = narrative.get("continuationRiskScore", 0)
    if cont_label == "high":
        negatives.append(f"Narrative continuation risk tinggi ({cont_score}/100): pump masih bisa lanjut kalau volume/narasi kuat.")
    elif cont_label == "medium":
        negatives.append(f"Narrative continuation risk menengah ({cont_score}/100): perlu konfirmasi rejection.")
    else:
        positives.append(f"Narrative continuation risk rendah ({cont_score}/100): skenario lanjut pump belum kuat.")

    verdict_map = {
        "STRONG_SHORT_SETUP": "Setup short cukup matang secara kuantitatif. Tetap validasi narasi bullish sebelum agresif.",
        "SHORT_VALID": "Setup short valid secara probabilistik. Jika skenario bullish butuh volume/cap terlalu berat, sinyal short makin masuk akal.",
        "SHORT_WATCH": "Belum cukup matang — masuk watchlist, tunggu wick rejection / lower-high.",
        "WEAK_WATCH": "Edge masih tipis. Hindari force entry, tunggu setup yang lebih jelas.",
        "NO_SHORT": "Belum ada edge yang signifikan. Pasar belum memberi sinyal.",
        "DANGER_STOP_RISK": "BAHAYA — kemungkinan stop loss tersapu tinggi. Skip atau tunggu reset.",
    }
    verdict = verdict_map.get(mc["status"], "Status tidak dikenal.")

    positives_lines = [f"- {p}" for p in positives] if positives else ["- Belum ada faktor pendukung yang kuat."]
    negatives_lines = [f"- {n}" for n in negatives] if negatives else ["- Risiko utama tetap volatility dan invalidasi struktur."]

    required_vs_current = vol.get("requiredVolumeVsCurrent24h", 0)
    if required_vs_current >= 2:
        volume_interpretation = "berat: butuh dorongan volume jauh di atas kondisi 24h sekarang."
    elif required_vs_current >= 1:
        volume_interpretation = "sedang: butuh volume sekitar setara/lebih besar dari 24h saat ini."
    elif required_vs_current > 0:
        volume_interpretation = "ringan-menengah: volume 24h saat ini relatif cukup mendukung continuation."
    else:
        volume_interpretation = "belum bisa dihitung akurat karena data market cap/volume terbatas."

    return "\n".join([
        f"## Ringkasan {token}",
        f"Status sistem: **{mc['status']}** dengan score **{mc['score']}/100**.",
        f"Harga saat ini: ${market['price']} ({market['change24hPercent']}% 24h).",
        "",
        "## Kondisi Pasar",
        f"- Phase: **{ex['phase']}** (exhaustion {ex['score']}/100)",
        f"- Posisi dalam range 24h: {ex['positionInRange']}%",
        f"- Wick rejection ratio: {ex['wickRatio']}%",
        "",
        "## Auto Level System",
        f"- Take Profit: ${auto['takeProfit']} (pullback {auto['pullbackPct']}%)",
        f"- Stop Loss: ${auto['stopLoss']} (buffer {auto['stopBufferPct']}%)",
        f"- Risk/Reward: {auto['riskReward']}x",
        "",
        "## Probabilitas Monte Carlo",
        f"- Down: {mc['probabilityDown']}%  |  TP: {mc['probabilityTP']}%  |  SL: {mc['probabilitySL']}%",
        f"- EV: {mc['expectedValuePct']}% per trade",
        "",
        "## Bullish Counter Scenario",
        f"- Jika harga lanjut +20%: target sekitar ${targets.get('plus20pctPrice', 0)}",
        f"- Jika harga lanjut +50%: target sekitar ${targets.get('plus50pctPrice', 0)}",
        f"- Jika harga 2x: target sekitar ${targets.get('doublePrice', 0)}",
        f"- Market cap/proxy sekarang: {_compact_money(cap.get('currentMarketCapOrProxy', 0))}",
        f"- Estimasi cap/proxy untuk 2x: {_compact_money(cap.get('neededForDouble', 0))}",
        f"- Rough required volume untuk 2x: {_compact_money(vol.get('roughRequiredVolumeForDouble', 0))} ({volume_interpretation})",
        f"- Continuation risk: **{cont_label}** ({cont_score}/100)",
        "",
        "## Faktor Pendukung Short",
        *positives_lines,
        "",
        "## Faktor Risiko / Narasi Lawan",
        *negatives_lines,
        "",
        "## Kesimpulan",
        verdict,
        "",
        "_Disclaimer: angka di atas adalah hasil simulasi probabilistik dan skenario edukatif, bukan sinyal pasti._",
    ])


async def _try_aixchia(payload: dict) -> dict | None:
    api_key = os.getenv("AIXCHIA_API_KEY") or os.getenv("AIXCHIAAPIKEY")
    if not api_key:
        return None

    api_url = (os.getenv("AIXCHIA_API_URL") or os.getenv("AIXCHIAAPIURL") or "https://www.aichixia.xyz/api/v1").rstrip("/")
    model = os.getenv("AIXCHIA_MODEL") or os.getenv("AIXCHIAMODEL") or "gpt-5-mini"
    endpoint = f"{api_url}/chat/completions"

    system_prompt = (
        "Kamu adalah quantitative market analyst untuk dashboard edukatif crypto. "
        "Baca payload kuantitatif: pump exhaustion, auto TP/SL, Monte Carlo, dan Bullish Counter Scenario. "
        "Monte Carlo adalah mesin angka; Bullish Counter Scenario adalah validasi narasi lawan. "
        "Jelaskan apakah sinyal short didukung atau perlu diperingatkan karena risiko continuation. "
        "JANGAN beri financial advice / instruksi entry pasti. "
        "Bahasa Indonesia profesional. Format markdown rapi dengan section headers (##)."
    )
    user_prompt = (
        "Analisis token berikut dalam format markdown:\n"
        "## Ringkasan Kondisi\n## Bacaan Score & Phase\n"
        "## Peluang TP vs SL\n## Bullish Counter Scenario\n"
        "## Syarat Agar Harga Lanjut Naik\n## Risiko Utama\n"
        "## Konfirmasi yang Perlu Ditunggu\n## Kesimpulan Watch / No Trade\n\n"
        f"Payload:\n{json.dumps(payload, indent=2)}"
    )

    try:
        async with httpx.AsyncClient(timeout=20.0) as c:
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
                    "max_tokens": 1100,
                },
            )
        if r.status_code != 200:
            return {"_aixchia_error": f"HTTP {r.status_code}"}
        j = r.json()
        content = (
            (j.get("choices") or [{}])[0].get("message", {}).get("content")
            or (j.get("choices") or [{}])[0].get("text")
            or j.get("message")
            or j.get("content")
        )
        if not content:
            return {"_aixchia_error": "empty content"}
        return {"source": "aixchia", "model": model, "analysis": content}
    except Exception as e:
        return {"_aixchia_error": f"{type(e).__name__}: {e}"}


async def _try_emergent(payload: dict, session_id: str) -> dict | None:
    if not _EMG_AVAILABLE:
        return None
    api_key = os.getenv("EMERGENT_LLM_KEY")
    if not api_key:
        return None

    system_msg = (
        "Kamu adalah quantitative market analyst untuk dashboard edukatif crypto LQ-Short Hunter. "
        "Tugasmu membaca payload kuantitatif (pump exhaustion, auto TP/SL, Monte Carlo) dan "
        "Bullish Counter Scenario untuk menilai risiko continuation. JANGAN beri financial advice atau instruksi entry pasti. "
        "Gunakan bahasa Indonesia profesional dan format markdown rapi dengan section headers. Maksimal 500 kata."
    )
    user_text = (
        "Analisis token berikut dalam format markdown dengan sections:\n"
        "## Ringkasan Kondisi\n## Bacaan Score & Phase\n"
        "## Peluang TP vs SL\n## Bullish Counter Scenario\n"
        "## Risiko Utama\n## Kesimpulan Watch / No Trade\n\n"
        f"Payload:\n{json.dumps(payload, indent=2)}"
    )

    try:
        chat = LlmChat(
            api_key=api_key,
            session_id=session_id,
            system_message=system_msg,
        ).with_model("anthropic", "claude-sonnet-4-6")
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

    session_id = f"analysis-{payload['token']}"

    warnings: list[str] = []

    aix = await _try_aixchia(payload)
    if aix and aix.get("analysis"):
        return {**aix, "payload": payload}
    if aix and aix.get("_aixchia_error"):
        warnings.append(f"AIXCHIA: {aix['_aixchia_error']}")

    emg = await _try_emergent(payload, session_id)
    if emg and emg.get("analysis"):
        out = {**emg, "payload": payload}
        if warnings:
            out["warning"] = " | ".join(warnings)
        return out
    if emg and emg.get("_emergent_error"):
        warnings.append(f"Emergent: {emg['_emergent_error']}")

    return {
        "source": "rule-based",
        "model": "local-fallback",
        "analysis": _rule_based_analysis(payload),
        "payload": payload,
        "warning": (" | ".join(warnings) if warnings else "AIXCHIA key tidak di-set. Menggunakan fallback rule-based."),
    }

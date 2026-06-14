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

    return {
        "token": market.get("symbol"),
        "market": {
            "price": _safe_num(market.get("lastPrice"), 10),
            "change24hPercent": _safe_num(market.get("priceChangePercent"), 4),
            "quoteVolume": _safe_num(market.get("quoteVolume"), 2),
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
    }


def _rule_based_analysis(payload: dict) -> str:
    mc = payload["monteCarlo"]
    ex = payload["pumpExhaustion"]
    auto = payload["autoLevels"]
    market = payload["market"]
    token = payload["token"]

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

    verdict_map = {
        "STRONG_SHORT_SETUP": "Setup short cukup matang. Tetap tunggu konfirmasi penolakan di level resistance.",
        "SHORT_VALID": "Setup short valid secara probabilistik. Manage size dan SL ketat.",
        "SHORT_WATCH": "Belum cukup matang — masuk watchlist, tunggu wick rejection / lower-high.",
        "WEAK_WATCH": "Edge masih tipis. Hindari force entry, tunggu setup yang lebih jelas.",
        "NO_SHORT": "Belum ada edge yang signifikan. Pasar belum memberi sinyal.",
        "DANGER_STOP_RISK": "BAHAYA — kemungkinan stop loss tersapu tinggi. Skip atau tunggu reset.",
    }
    verdict = verdict_map.get(mc["status"], "Status tidak dikenal.")

    positives_lines = [f"- {p}" for p in positives] if positives else ["- Belum ada faktor pendukung yang kuat."]
    negatives_lines = [f"- {n}" for n in negatives] if negatives else ["- Risiko utama tetap volatility dan invalidasi struktur."]

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
        "## Faktor Pendukung",
        *positives_lines,
        "",
        "## Faktor Risiko",
        *negatives_lines,
        "",
        "## Kesimpulan",
        verdict,
        "",
        "_Disclaimer: angka di atas adalah hasil simulasi probabilistik, bukan sinyal pasti._",
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
        "Baca payload kuantitatif (pump exhaustion, auto TP/SL, Monte Carlo). "
        "JANGAN beri financial advice / instruksi entry pasti. "
        "Bahasa Indonesia profesional. Format markdown rapi dengan section headers (##)."
    )
    user_prompt = (
        "Analisis token berikut dalam format markdown:\n"
        "## Ringkasan Kondisi\n## Bacaan Score & Phase\n"
        "## Peluang TP vs SL\n## Risiko Utama\n"
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
                    "max_tokens": 900,
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
        "menjelaskan kondisinya. JANGAN beri financial advice atau instruksi entry pasti. "
        "Gunakan bahasa Indonesia profesional dan format markdown rapi dengan section headers "
        "berformat '## Judul Section'. Maksimal 400 kata."
    )
    user_text = (
        "Analisis token berikut dalam format markdown dengan sections:\n"
        "## Ringkasan Kondisi\n## Bacaan Score & Phase\n"
        "## Peluang TP vs SL\n## Risiko Utama\n"
        "## Konfirmasi yang Perlu Ditunggu\n## Kesimpulan Watch / No Trade\n\n"
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

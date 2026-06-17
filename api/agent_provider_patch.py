"""Patch AI agent provider fallback for Vercel runtime.

Goal: if the selected provider fails, try the other configured providers before
falling back to local rule-based analysis.
"""

from __future__ import annotations

from typing import Awaitable, Callable

import backend.agent as agent
import backend.server as server


async def _run_provider(label: str, runner: Callable[[], Awaitable[dict | None]], warnings: list[str]) -> dict | None:
    try:
        out = await runner()
    except Exception as exc:  # keep API alive even when provider SDK breaks
        warnings.append(f"{label}: {type(exc).__name__}: {exc}")
        return None

    if out and out.get("analysis"):
        return out
    if isinstance(out, dict):
        for key, value in out.items():
            if key.startswith("_") and key.endswith("_error"):
                warnings.append(f"{label}: {value}")
                break
    else:
        warnings.append(f"{label}: unavailable")
    return None


def apply_agent_provider_fallback() -> None:
    """Patch both backend.agent and backend.server imported route reference."""
    if not hasattr(agent, "_provider_fallback_original_generate"):
        agent._provider_fallback_original_generate = agent.generate_agent_analysis

    async def generate_agent_analysis_with_provider_fallback(body: dict) -> dict:
        payload = agent._compact_payload(body)
        if not payload.get("token"):
            return {"source": "error", "analysis": "Missing token payload.", "payload": payload}

        provider = (body.get("provider") or body.get("aiProvider") or "auto").strip().lower()
        session_id = f"analysis-{payload['token']}"
        warnings: list[str] = []

        async def use_0g() -> dict | None:
            return await _run_provider("0G MiniMax/NVIDIA", lambda: agent._try_0g_minimax(payload), warnings)

        async def use_aixchia() -> dict | None:
            return await _run_provider("AIXCHIA", lambda: agent._try_aixchia(payload), warnings)

        async def use_emergent() -> dict | None:
            return await _run_provider("Emergent", lambda: agent._try_emergent(payload, session_id), warnings)

        if provider in ("0g", "0g-minimax", "og", "minimax", "nvidia", "stepfun"):
            order = (use_0g, use_aixchia, use_emergent)
        elif provider in ("aixchia", "gpt", "default"):
            order = (use_aixchia, use_0g, use_emergent)
        elif provider in ("emergent", "claude", "emergent-llm"):
            order = (use_emergent, use_0g, use_aixchia)
        else:
            order = (use_0g, use_aixchia, use_emergent)

        for runner in order:
            result = await runner()
            if result:
                return {
                    **result,
                    "mode": payload.get("analysisMode"),
                    "payload": payload,
                    "warning": " | ".join(warnings) if warnings else None,
                }

        return {
            "source": "rule-based",
            "model": "local-fallback",
            "mode": payload.get("analysisMode"),
            "analysis": agent._rule_based_analysis(payload),
            "payload": payload,
            "warning": "AI provider gagal semua: " + (" | ".join(warnings) if warnings else "tidak ada provider/key aktif."),
        }

    agent.generate_agent_analysis = generate_agent_analysis_with_provider_fallback
    # backend.server imported the function by value at import time, so patch that too.
    server.generate_agent_analysis = generate_agent_analysis_with_provider_fallback

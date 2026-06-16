"""Vercel Python function entrypoint for the FastAPI application."""

from __future__ import annotations

import os

import httpx

from backend.server import app


def _mask_key(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}...{value[-4:]}"


def _resolve_ai_config() -> dict:
    api_key = (
        os.getenv("NVIDIA_API_KEY")
        or os.getenv("OG_API_KEY")
        or os.getenv("OGAI_API_KEY")
        or os.getenv("ZERO_G_API_KEY")
        or os.getenv("ZEROG_API_KEY")
        or os.getenv("0G_API_KEY")
    )
    endpoint = (
        os.getenv("NVIDIA_API_URL")
        or os.getenv("OG_API_URL")
        or os.getenv("OGAI_API_URL")
        or "https://router-api.0g.ai/v1/chat/completions"
    ).strip()
    model = (
        os.getenv("NVIDIA_MODEL")
        or os.getenv("STEPFUN_MODEL")
        or os.getenv("OG_MODEL")
        or os.getenv("OGAI_MODEL")
        or os.getenv("MINIMAX_MODEL")
        or "minimax"
    ).strip()

    normalized_endpoint = endpoint.rstrip("/")
    if normalized_endpoint.endswith("/v1"):
        normalized_endpoint = f"{normalized_endpoint}/chat/completions"

    provider_hint = "custom-openai-compatible"
    if "nvidia" in normalized_endpoint.lower():
        provider_hint = "nvidia-stepfun"
    elif "0g.ai" in normalized_endpoint.lower():
        provider_hint = "0g"

    return {
        "providerHint": provider_hint,
        "apiKeyDetected": bool(api_key),
        "apiKeyPreview": _mask_key(api_key),
        "endpointRaw": endpoint,
        "endpointEffective": normalized_endpoint,
        "model": model,
        "baseUrlWarning": endpoint.rstrip("/").endswith("/v1"),
        "timeoutSeconds": 45,
        "api_key": api_key,
    }


@app.get("/api/ai-check")
async def ai_check():
    """Small browser-friendly AI provider diagnostic. Never returns the full API key."""
    cfg = _resolve_ai_config()
    api_key = cfg.pop("api_key")
    if not api_key:
        return {
            "ok": False,
            **cfg,
            "status": "missing_api_key",
            "message": "NVIDIA_API_KEY atau OG_API_KEY belum terdeteksi di Vercel Production env.",
        }

    payload = {
        "model": cfg["model"],
        "messages": [{"role": "user", "content": "Reply with exactly: AI_OK"}],
        "temperature": 0.0,
        "max_tokens": 16,
    }
    try:
        async with httpx.AsyncClient(timeout=45.0) as client:
            response = await client.post(
                cfg["endpointEffective"],
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=payload,
            )
        preview = response.text[:300]
        return {
            "ok": response.status_code == 200,
            **cfg,
            "httpStatus": response.status_code,
            "status": "ok" if response.status_code == 200 else "provider_http_error",
            "responsePreview": preview,
        }
    except Exception as e:
        return {
            "ok": False,
            **cfg,
            "status": "provider_exception",
            "errorType": type(e).__name__,
            "error": str(e),
        }


__all__ = ["app"]

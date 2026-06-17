"""Vercel Python function entrypoint for the FastAPI application."""

from __future__ import annotations

import os

import httpx

import backend.server as _server
from backend.server import app
from backend import agent as _agent
from api.runtime_patch import apply_runtime_patches

apply_runtime_patches()


def _mask_key(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}...{value[-4:]}"


def _timeout_seconds(default: float = 50.0) -> float:
    try:
        value = float(os.getenv("AI_TIMEOUT_SECONDS") or default)
    except Exception:
        value = default
    return max(10.0, min(value, 55.0))


def _effective_endpoint(endpoint: str) -> str:
    normalized_endpoint = endpoint.strip().rstrip("/")
    if normalized_endpoint.endswith("/v1"):
        return f"{normalized_endpoint}/chat/completions"
    return normalized_endpoint


def _provider_label(endpoint: str, model: str) -> str:
    low = f"{endpoint} {model}".lower()
    if "nvidia" in low and "step" in low:
        return "nvidia-stepfun"
    if "nvidia" in low:
        return "nvidia-openai-compatible"
    if "0g.ai" in low:
        return "0g-minimax"
    return "openai-compatible"


async def _patched_openai_compatible_chat(*, endpoint: str, api_key: str, model: str, payload: dict, source: str, max_tokens: int = 900) -> dict | None:
    """Longer-timeout OpenAI-compatible call for Vercel runtime."""
    system_prompt, user_prompt = _agent._agent_prompts(payload)
    timeout = _timeout_seconds()
    endpoint = _effective_endpoint(endpoint)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
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
        if response.status_code != 200:
            return {f"_{source}_error": f"HTTP {response.status_code}: {response.text[:220]}"}
        data = response.json()
        content = (
            (data.get("choices") or [{}])[0].get("message", {}).get("content")
            or (data.get("choices") or [{}])[0].get("text")
            or data.get("message")
            or data.get("content")
        )
        if not content:
            return {f"_{source}_error": "empty content"}
        return {"source": source, "model": model, "analysis": content}
    except Exception as exc:
        return {f"_{source}_error": f"{type(exc).__name__}: {exc}"}


async def _patched_try_openai_router(payload: dict) -> dict | None:
    api_key = (
        os.getenv("NVIDIA_API_KEY")
        or os.getenv("OG_API_KEY")
        or os.getenv("OGAI_API_KEY")
        or os.getenv("ZERO_G_API_KEY")
        or os.getenv("ZEROG_API_KEY")
        or os.getenv("0G_API_KEY")
    )
    if not api_key:
        return None
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
    source = _provider_label(endpoint, model)
    return await _patched_openai_compatible_chat(endpoint=endpoint, api_key=api_key, model=model, payload=payload, source=source)


# Patch the imported backend agent without touching its full file.
_agent._openai_compatible_chat = _patched_openai_compatible_chat
_agent._try_0g_minimax = _patched_try_openai_router


BINANCE_SPOT_MARKET_ENDPOINTS = [
    "https://api.binance.com/api/v3/ticker/24hr",
    "https://api-gcp.binance.com/api/v3/ticker/24hr",
    "https://api1.binance.com/api/v3/ticker/24hr",
    "https://api2.binance.com/api/v3/ticker/24hr",
    "https://api3.binance.com/api/v3/ticker/24hr",
    "https://api4.binance.com/api/v3/ticker/24hr",
    "https://data-api.binance.vision/api/v3/ticker/24hr",
]
BINANCE_FUTURES_MARKET_ENDPOINTS = [
    "https://fapi.binance.com/fapi/v1/ticker/24hr",
    "https://fapi1.binance.com/fapi/v1/ticker/24hr",
    "https://fapi2.binance.com/fapi/v1/ticker/24hr",
]


async def _try_fetch_binance_array(client: httpx.AsyncClient, urls: list[str]) -> tuple[list | None, list[str]]:
    errors: list[str] = []
    headers = {
        "accept": "application/json,text/plain,*/*",
        "user-agent": "Mozilla/5.0 LQ-Short-Hunter/2.5",
        "cache-control": "no-cache",
    }
    for url in urls:
        try:
            response = await client.get(url, timeout=12.0, headers=headers)
            if response.status_code != 200:
                errors.append(f"{url} -> HTTP {response.status_code}: {response.text[:90]}")
                continue
            data = response.json()
            if isinstance(data, list) and data:
                return data, errors
            errors.append(f"{url} -> empty/not array")
        except Exception as exc:
            errors.append(f"{url} -> {type(exc).__name__}: {exc}")
    return None, errors


async def _fetch_binance_rows() -> tuple[list[dict], str, str, list[str]]:
    """Fetch dashboard rows from Binance first: spot first, then futures."""
    client = getattr(app.state, "http", None)
    created_client = False
    if client is None:
        client = httpx.AsyncClient()
        created_client = True
    debug: list[str] = []
    try:
        raw, errs = await _try_fetch_binance_array(client, BINANCE_SPOT_MARKET_ENDPOINTS)
        debug.extend(errs)
        if raw is not None:
            rows = _server._normalize_exchange_rows(raw)
            if rows:
                return rows, "binance-spot", "binance-spot", debug[:10]
            debug.append("binance-spot empty after normalize")

        raw, errs = await _try_fetch_binance_array(client, BINANCE_FUTURES_MARKET_ENDPOINTS)
        debug.extend(errs)
        if raw is not None:
            rows = _server._normalize_exchange_rows(raw)
            if rows:
                return rows, "binance-futures", "binance-futures", debug[:10]
            debug.append("binance-futures empty after normalize")

        return [], "binance-unavailable", "binance-primary", debug[:14]
    finally:
        if created_client:
            await client.aclose()


@app.get("/api/binance-markets")
async def binance_markets():
    """Dashboard market list: Binance first, then automatic fallback to other market APIs."""
    rows, source, market_type, debug = await _fetch_binance_rows()
    if rows:
        warning = None
        if source == "binance-futures":
            warning = "Binance Spot gagal, otomatis memakai Binance Futures fallback."
        return {
            "ok": True,
            "source": source,
            "marketType": market_type,
            "count": len(rows),
            "data": rows,
            "warning": warning,
            "debug": debug,
        }

    fallback = await _server.markets()
    fallback_debug = list(fallback.get("debug") or [])
    fallback_source = fallback.get("source") or "fallback"
    fallback_data = fallback.get("data") or []
    return {
        "ok": True,
        "source": fallback_source,
        "marketType": fallback.get("marketType") or "auto-fallback",
        "count": len(fallback_data),
        "data": fallback_data,
        "warning": (
            "Binance API gagal dari server ini, jadi dashboard otomatis memakai "
            f"{fallback_source} fallback."
        ),
        "debug": ["binance failed; fallback enabled", *debug[:8], *fallback_debug[:6]],
    }


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

    effective = _effective_endpoint(endpoint)
    provider_hint = _provider_label(effective, model)

    return {
        "providerHint": provider_hint,
        "apiKeyDetected": bool(api_key),
        "apiKeyPreview": _mask_key(api_key),
        "endpointRaw": endpoint,
        "endpointEffective": effective,
        "model": model,
        "baseUrlWarning": endpoint.rstrip("/").endswith("/v1"),
        "timeoutSeconds": _timeout_seconds(45.0),
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
        async with httpx.AsyncClient(timeout=cfg["timeoutSeconds"]) as client:
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

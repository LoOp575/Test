"""Offline tests for the server-side Calibration Lab."""

from __future__ import annotations

import json

import httpx
import pytest

from backend import calibration_lab
from backend.server import app


def _mk_candles(n: int, seed: int = 7) -> list[dict]:
    import random

    rng = random.Random(seed)
    rows = []
    base_ms = 1_600_000_000_000
    price = 100.0
    vol = 10_000.0
    drift = 0.0008 + 0.0006 * rng.random()
    for i in range(n):
        shock = rng.gauss(0.0, 0.028)
        price = max(1.0, price * (1.0 + drift + shock))
        vol = max(100.0, vol * (1.0 + rng.gauss(0.0, 0.09)))
        rows.append(
            {
                "date": base_ms + i * 86_400_000,
                "open": price * 0.99,
                "high": price * 1.01,
                "low": price * 0.98,
                "close": price,
                "volume": vol,
                "quoteVolume": vol * price,
                "trades": vol / 3.0,
                "takerBuyBase": vol * 0.55,
                "takerBuyQuote": vol * 0.55 * price,
            }
        )
    return rows


def _klines_raw(candles: list[dict]) -> list[list]:
    out = []
    for c in candles:
        t = c["date"]
        out.append(
            [
                t,
                f"{c['open']:.8f}",
                f"{c['high']:.8f}",
                f"{c['low']:.8f}",
                f"{c['close']:.8f}",
                f"{c['volume']:.8f}",
                t + 86_399_999,
                f"{c['quoteVolume']:.8f}",
                int(c["trades"]),
                f"{c['takerBuyBase']:.8f}",
                f"{c['takerBuyQuote']:.8f}",
                "0",
            ]
        )
    return out


def _transport(candles_by_symbol: dict[str, list[dict]], fail_all: bool = False):
    class Router:
        def __call__(self, request: httpx.Request) -> httpx.Response:
            if fail_all:
                raise httpx.ConnectError("host unreachable", request=request)
            from urllib.parse import parse_qs, urlparse

            qs = parse_qs(urlparse(str(request.url)).query)
            sym = (qs.get("symbol") or [""])[0].upper()
            candles = candles_by_symbol.get(sym)
            if candles is None:
                return httpx.Response(404, json={"code": -1121, "msg": "Invalid symbol."})
            return httpx.Response(200, json=_klines_raw(candles))

    return Router()


def _client(candles_by_symbol=None, fail_all=False):
    return httpx.AsyncClient(transport=httpx.MockTransport(_transport(candles_by_symbol or {}, fail_all)))


def test_formula_meta_matches_production_weights():
    from backend.screening import P_ACC_WEIGHTS, sigmoid

    meta = calibration_lab.formula_meta()
    assert meta["weights"] == P_ACC_WEIGHTS
    assert meta["status"]
    assert len(meta["features"]) == 6

    # The lab reports the same constants the sigmoid uses: verify by checking
    # that a z computed from the weights produces sigmoid(z) == p_acc.
    from backend.screening import compute_features
    from scripts.calibrate import feature_vector

    vec = feature_vector(compute_features(_mk_candles(150, seed=3)))
    assert vec is not None
    g_pos, a_pos, d_pos, gap_pos, c_cash, s_flow = vec
    analysis = P_ACC_WEIGHTS
    z = (
        analysis["intercept"]
        + analysis["g_pos"] * g_pos
        + analysis["a_pos"] * a_pos
        + analysis["d_pos"] * d_pos
        + analysis["gap_pos"] * gap_pos
        + analysis["c_cash"] * (c_cash - 0.5)
        + analysis["s_flow"] * (s_flow - 0.7)
    )
    p = sigmoid(z)
    # p^acc surfaces under output["pAcceleration"] (rounded to 4dp)
    assert p == pytest.approx(compute_features(_mk_candles(150, seed=3))["scores"]["pAcceleration"], abs=1e-4)


@pytest.mark.anyio
async def test_run_calibration_offline_happy_path():
    client = _client(
        {
            "AAAUSDT": _mk_candles(400, seed=11),
            "BBBUSDT": _mk_candles(400, seed=22),
            "CCCUSDT": _mk_candles(400, seed=33),
        }
    )
    rep = await calibration_lab.run_calibration(client, lookback_days=400, step_days=28, symbols=["AAAUSDT", "BBBUSDT", "CCCUSDT"], min_samples=10)
    assert rep["ok"] is True
    assert rep["universe"] == ["AAAUSDT", "BBBUSDT", "CCCUSDT"]
    assert rep["samples"] >= 10
    assert rep["validation"]["brier"] <= 1.0
    assert 0.0 <= rep["validation"]["auc"] <= 1.0
    assert rep["verdict"]["status"] in {"lulus", "kurang"}
    assert set(rep["verdict"]["criteria"]) == {"brierImproves", "aucNotWorse", "precisionAtPoint6BeatsBaseRate"}
    assert "suggestedBlock" in rep and "z = " in rep["suggestedBlock"]
    assert rep["anchorFirst"] and rep["anchorLast"]


@pytest.mark.anyio
async def test_run_calibration_never_fabricates_on_failure():
    client = _client(fail_all=True)
    rep = await calibration_lab.run_calibration(client, lookback_days=300, step_days=7, symbols=["AAAUSDT", "BBBUSDT"])
    assert rep["ok"] is False
    assert rep["skipped"]
    assert "universe" not in rep  # no fabricated candles leaked into the fit


@pytest.mark.anyio
async def test_run_calibration_partial_coverage_reports_skips():
    client = _client({"AAAUSDT": _mk_candles(400, seed=5)})
    rep = await calibration_lab.run_calibration(
        client, lookback_days=400, step_days=28, symbols=["AAAUSDT", "UNKNOWNUSDT"], min_samples=5
    )
    assert rep["ok"] is True
    assert rep["universe"] == ["AAAUSDT"]
    assert rep["skipped"] and rep["skipped"][0]["symbol"] == "UNKNOWNUSDT"


def test_verdict_logic():
    base = {"samples": 500, "validation": {"brier": 0.18, "auc": 0.62, "p_ge_0.6": {"count": 20, "precision": 0.7}},
            "currentHeuristic": {"validationBrier": 0.3, "validationAuc": 0.55}, "baseRateValidation": 0.4}
    out = calibration_lab._verdict(base)
    assert out["status"] == "lulus"

    bad = {**base, "validation": {"brier": 0.35, "auc": 0.5, "p_ge_0.6": {"count": 2, "precision": 0.1}}}
    out = calibration_lab._verdict(bad)
    assert out["status"] == "kurang"
    assert len(out["failed"]) >= 1


def test_history_buffer_shape():
    import asyncio

    async def _go():
        client = _client({"AAAUSDT": _mk_candles(400, seed=11), "BBBUSDT": _mk_candles(400, seed=22)})
        rep = await calibration_lab.run_calibration(client, lookback_days=400, step_days=28, symbols=["AAAUSDT", "BBBUSDT"], min_samples=5)
        assert rep["ok"] is True
        return rep

    rep = asyncio.run(_go())
    assert "perSymbolCandles" in rep

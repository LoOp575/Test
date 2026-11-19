"""Unit & API integration tests for the isolated Fokker–Planck module.

Covers:
  1. Analytical benchmarks: drifted Brownian motion, Ornstein–Uhlenbeck, and
     state-dependent fat-tailed (Student-t) stationary density.
  2. Invariants: total mass == 1.0, positivity >= 0, volatility monotonically
     reduces central peak stability (fixing the naive scalar bug), strong buy
     drift stays <= 1.0.
  3. Microstructure regimes: calm market, spoof sell/buy walls, genuine panic
     dump, genuine buy breakout, high-volatility whipsaw, and mock live
     Binance depth+trades ingestion + cancellation diffing.
  4. OHLCV candle analyzer & chronological walk-forward calibration audit.
  5. FastAPI /api/fokker-planck/* endpoints + regression check on existing routes.
"""

from __future__ import annotations

import asyncio
import math

import httpx
import pytest

from backend import fokker_planck as fp
from backend.sample_data import seed_klines
from backend.server import app


def _norm_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


# ---------------------------------------------------------------------------
# 1. Core PDE solver — analytical benchmarks & mathematical invariants
# ---------------------------------------------------------------------------

def test_brownian_drift_matches_exact_gaussian():
    mu0, d0, horizon, band, s0 = -0.04, 0.008, 1.0, 0.05, 0.006
    sol = fp.solve_fokker_planck(
        lambda _x, _t: mu0,
        lambda _x, _t: d0,
        x_min=-0.65,
        x_max=0.65,
        stable_band=band,
        horizon=horizon,
        n_grid=301,
        n_steps=80,
        sigma0=s0,
    )
    f = sol.final
    exact_mean = mu0 * horizon
    exact_std = math.sqrt(s0 * s0 + d0 * horizon)
    exact_stab = _norm_cdf((band - exact_mean) / exact_std) - _norm_cdf((-band - exact_mean) / exact_std)
    exact_down = _norm_cdf((-band - exact_mean) / exact_std)
    exact_up = 1.0 - _norm_cdf((band - exact_mean) / exact_std)

    assert abs(f.total_mass - 1.0) < 1e-9
    assert abs(f.p_stable + f.p_downside + f.p_upside - 1.0) < 1e-9
    assert abs(f.mean_log_return - exact_mean) < 5e-4
    assert abs(f.std_log_return - exact_std) < 1e-3
    assert abs(f.p_stable - exact_stab) < 3e-3
    assert abs(f.p_downside - exact_down) < 3e-3
    assert abs(f.p_upside - exact_up) < 3e-3
    assert all(val >= 0.0 for val in sol.pdf)


def test_ornstein_uhlenbeck_converges_to_exact_stationary_gaussian():
    kappa, d0 = 1.5, 0.02
    sol = fp.solve_fokker_planck(
        lambda x, _t: -kappa * x,
        lambda _x, _t: d0,
        x_min=-0.65,
        x_max=0.65,
        stable_band=0.05,
        horizon=8.0,
        n_grid=241,
        n_steps=80,
    )
    exact_std = math.sqrt(d0 / (2.0 * kappa))
    assert abs(sol.final.total_mass - 1.0) < 1e-9
    assert abs(sol.final.std_log_return - exact_std) < 5e-4
    assert abs(sol.final.kurtosis - 3.0) < 0.03


def test_state_dependent_diffusion_reproduces_student_t_fat_tails():
    # μ(x) = -κ x, D(x) = D0 (1 + c x²) -> P_eq(x) ∝ (1 + c x²)^(-(ν+1)/2)
    nu = 5.0
    d0 = 0.01
    c = 80.0
    kappa = 0.5 * (nu - 1.0) * c * d0
    sol = fp.solve_fokker_planck(
        lambda x, _t: -kappa * x,
        lambda x, _t: d0 * (1.0 + c * x * x),
        x_min=-1.5,
        x_max=1.5,
        stable_band=0.05,
        horizon=15.0,
        n_grid=401,
        n_steps=100,
    )
    raw = [(1.0 + c * x * x) ** (-(nu + 1.0) / 2.0) for x in sol.grid]
    norm = sum(raw) * sol.dx
    exact_pdf = [v / norm for v in raw]
    l1_err = sum(abs(a - b) for a, b in zip(sol.pdf, exact_pdf)) * sol.dx
    exact_std = math.sqrt(1.0 / (c * (nu - 2.0)))

    assert abs(sol.final.total_mass - 1.0) < 1e-9
    assert l1_err < 1e-3
    assert abs(sol.final.std_log_return - exact_std) < 1e-3
    assert sol.final.kurtosis > 6.0  # genuine fat tails (> 3.0 Gaussian)


def test_higher_volatility_decreases_stability_and_strong_drift_stays_bounded():
    low_vol = fp.simulate_direct_pde(mu=0.0, diffusion=0.001, kappa=0.0, tail_eta=0.0, downside_asym=0.0)
    high_vol = fp.simulate_direct_pde(mu=0.0, diffusion=0.05, kappa=0.0, tail_eta=0.0, downside_asym=0.0)
    assert high_vol["metrics"]["pStable"] < low_vol["metrics"]["pStable"]

    strong_buy = fp.simulate_direct_pde(mu=0.35, diffusion=0.10, horizon=1.0, stable_band=0.05, naive_p0=0.80)
    m = strong_buy["metrics"]
    assert 0.0 <= m["pStable"] <= 1.0
    assert 0.0 <= m["pUpside"] <= 1.0
    assert abs(m["pStable"] + m["pDownside"] + m["pUpside"] - 1.0) < 1e-5
    # Meanwhile the naive scalar formula bursts past 1.0 and is flagged
    assert strong_buy["naiveComparison"]["naiveResult"] > 1.0
    assert strong_buy["naiveComparison"]["validProbability"] is False


# ---------------------------------------------------------------------------
# 2. Microstructure & Spoofing analyzer + live snapshot ingestion
# ---------------------------------------------------------------------------

def test_microstructure_distinguishes_spoof_from_real_dump_and_whipsaw():
    calm = fp.analyze_orderbook_fp(
        bid_depth=120, ask_depth=120, taker_buy_vol=45, taker_sell_vol=45,
        canceled_bid_vol=10, canceled_ask_vol=10, sigma_per_sec=0.0005,
    )
    spoof_sell = fp.analyze_orderbook_fp(
        bid_depth=45, ask_depth=260, taker_buy_vol=38, taker_sell_vol=42,
        canceled_bid_vol=8, canceled_ask_vol=340, sigma_per_sec=0.0007,
    )
    spoof_buy = fp.analyze_orderbook_fp(
        bid_depth=260, ask_depth=45, taker_buy_vol=41, taker_sell_vol=39,
        canceled_bid_vol=340, canceled_ask_vol=8, sigma_per_sec=0.0007,
    )
    real_dump = fp.analyze_orderbook_fp(
        bid_depth=45, ask_depth=220, taker_buy_vol=18, taker_sell_vol=95,
        canceled_bid_vol=15, canceled_ask_vol=25, sigma_per_sec=0.0011,
    )
    whipsaw = fp.analyze_orderbook_fp(
        bid_depth=100, ask_depth=100, taker_buy_vol=85, taker_sell_vol=85,
        canceled_bid_vol=45, canceled_ask_vol=45, sigma_per_sec=0.0020,
    )

    assert calm["regime"] == "STABLE_BALANCED"
    assert spoof_sell["regime"] == "SPOOF_SELL_WALL"
    assert spoof_buy["regime"] == "SPOOF_BUY_WALL"
    assert real_dump["regime"] == "REAL_SELL_PRESSURE"
    assert whipsaw["regime"] == "HIGH_VOL_WHIPSAW"

    # Spoof wall causes noticeably less downside/crash mass than genuine aggressive selling
    assert spoof_sell["microstructure"]["spoofIndex"] > 0.30
    assert real_dump["microstructure"]["spoofIndex"] < 0.10
    assert real_dump["metrics"]["pDownside"] > spoof_sell["metrics"]["pDownside"] + 0.30
    assert real_dump["metrics"]["pTailCrash"] > spoof_sell["metrics"]["pTailCrash"]

    # Symmetry check on whipsaw
    assert abs(whipsaw["metrics"]["pDownside"] - whipsaw["metrics"]["pUpside"]) < 1e-4


def test_fetch_microstructure_snapshot_tracks_live_cancellations_and_fallback():
    fp._PREV_BOOK_CACHE.clear()
    call_idx = {"depth": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/depth"):
            call_idx["depth"] += 1
            if call_idx["depth"] == 1:
                # Poll 1: huge ask wall at 100.10 (500 units)
                return httpx.Response(
                    200,
                    json={
                        "bids": [["99.90", "60.0"], ["99.80", "40.0"]],
                        "asks": [["100.10", "500.0"], ["100.20", "50.0"]],
                    },
                )
            # Poll 2: ask wall pulled down to 200 (300 vanished) while only 15 taker-buy executed -> 285 canceled!
            return httpx.Response(
                200,
                json={
                    "bids": [["99.90", "55.0"], ["99.80", "40.0"]],
                    "asks": [["100.10", "200.0"], ["100.20", "50.0"]],
                },
            )
        if request.url.path.endswith("/trades"):
            trades = [
                {"price": str(100.0 + (i % 3 - 1) * 0.04), "qty": "1.5", "time": 1_700_000_000_000 + i * 500, "isBuyerMaker": (i % 2 == 0)}
                for i in range(20)
            ]
            return httpx.Response(200, json=trades)
        return httpx.Response(500)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            first = await fp.fetch_microstructure_snapshot(client, "TESTUSDT")
            second = await fp.fetch_microstructure_snapshot(client, "TESTUSDT")
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda req: (_ for _ in ()).throw(httpx.ConnectError("offline", request=req)))
        ) as offline_client:
            fb = await fp.fetch_microstructure_snapshot(offline_client, "BTCUSDT", fallback_candles=seed_klines("BTCUSDT", 90))
        return first, second, fb

    first, second, fb = asyncio.run(go())
    assert first["live"] is True
    assert first["inputs"]["cancelDiffAvailable"] is False
    assert second["live"] is True
    assert second["inputs"]["cancelDiffAvailable"] is True
    assert second["inputs"]["canceledAskVol"] > 250.0
    assert second["analysis"]["regime"] == "SPOOF_SELL_WALL"

    assert fb["live"] is False
    assert fb["source"] == "kline-proxy-fallback"
    assert fb["analysis"]["ok"] is True


# ---------------------------------------------------------------------------
# 3. Candle analyzer & chronological walk-forward backtest
# ---------------------------------------------------------------------------

def test_analyze_candles_fp_and_backtest_on_seed_universe():
    btc = fp.analyze_candles_fp(seed_klines("BTCUSDT", 120), horizon_days=7.0, stable_band=0.05, include_curve=True)
    wif = fp.analyze_candles_fp(seed_klines("WIFUSDT", 120), horizon_days=7.0, stable_band=0.05, include_curve=True)
    short = fp.analyze_candles_fp(seed_klines("BTCUSDT", 120)[:30])

    assert btc["ok"] is True
    assert wif["ok"] is True
    assert short["ok"] is False

    # BTC should be substantially more stable with far smaller tail-crash mass than spike-prone WIF
    assert btc["metrics"]["pStable"] > wif["metrics"]["pStable"]
    assert btc["metrics"]["pTailCrash"] < wif["metrics"]["pTailCrash"]
    assert len(btc["curve"]) >= 80
    assert len(btc["timeline"]) >= 4

    bt = fp.backtest_candles_fp(
        {s: seed_klines(s, 160) for s in ["BTCUSDT", "ETHUSDT", "DOGEUSDT", "WIFUSDT", "ADAUSDT"]},
        horizon_days=7,
        stable_band=0.05,
        step_days=7,
    )
    assert bt["ok"] is True
    assert bt["samples"] >= 40
    assert 0.0 <= bt["scores"]["brierStable"] <= 1.0
    assert len(bt["coverage"]) == 4
    assert len(bt["reliabilityBins"]) == 5


# ---------------------------------------------------------------------------
# 4. FastAPI route tests (additive routes + zero regression on existing API)
# ---------------------------------------------------------------------------

def test_api_endpoints_and_existing_routes_unbroken():
    async def run_requests():
        app.state.http = httpx.AsyncClient()
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
                r_health = await ac.get("/api/health")
                r_screen = await ac.get("/api/screen?limit=6&source=seed")
                r_spec = await ac.get("/api/fokker-planck/spec")
                r_uni = await ac.get("/api/fokker-planck/universe?limit=8&horizonDays=7&stableBand=0.05&source=seed")
                r_asset = await ac.get("/api/fokker-planck/asset/BTCUSDT?horizonDays=7&stableBand=0.05&source=seed")
                r_sim_ob = await ac.post(
                    "/api/fokker-planck/simulate",
                    json={
                        "mode": "orderbook",
                        "bidDepth": 45,
                        "askDepth": 260,
                        "takerBuyVol": 38,
                        "takerSellVol": 42,
                        "canceledBidVol": 8,
                        "canceledAskVol": 340,
                        "sigmaPerSec": 0.0007,
                        "horizonSec": 30,
                        "stableBand": 0.004,
                    },
                )
                r_sim_dir = await ac.post(
                    "/api/fokker-planck/simulate",
                    json={"mode": "direct", "mu": -0.35, "diffusion": 0.05, "horizon": 1.0, "stableBand": 0.05, "naiveP0": 0.80},
                )
                r_bt = await ac.post(
                    "/api/fokker-planck/backtest",
                    json={"horizonDays": 7, "stableBand": 0.05, "stepDays": 10, "lookbackDays": 160, "limit": 8, "source": "seed"},
                )
                return r_health, r_screen, r_spec, r_uni, r_asset, r_sim_ob, r_sim_dir, r_bt
        finally:
            await app.state.http.aclose()

    r_health, r_screen, r_spec, r_uni, r_asset, r_sim_ob, r_sim_dir, r_bt = asyncio.run(run_requests())

    assert r_health.status_code == 200 and r_health.json()["ok"] is True
    assert r_screen.status_code == 200 and r_screen.json()["count"] == 6
    assert r_spec.status_code == 200 and len(r_spec.json()["presets"]) == 6

    uni_body = r_uni.json()
    assert r_uni.status_code == 200 and uni_body["ok"] is True and uni_body["count"] == 8

    asset_body = r_asset.json()
    assert r_asset.status_code == 200
    assert asset_body["symbol"] == "BTCUSDT"
    assert len(asset_body["candleAnalysis"]["curve"]) >= 80
    assert asset_body["microstructure"]["analysis"]["ok"] is True

    assert r_sim_ob.status_code == 200 and r_sim_ob.json()["regime"] == "SPOOF_SELL_WALL"
    assert r_sim_dir.status_code == 200 and abs(r_sim_dir.json()["metrics"]["totalMass"] - 1.0) < 1e-5
    assert r_bt.status_code == 200 and r_bt.json()["ok"] is True

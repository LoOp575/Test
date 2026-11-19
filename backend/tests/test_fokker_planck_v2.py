"""Tests for isolated Fokker–Planck Model v2 & Side-by-Side v1 vs v2 Comparator.

Verifies:
  1. Model v1 remains 100% untouched (zero cache coupling, bit-identical outputs).
  2. All 3 v2 parameter-translation pillars (Taker Priority + Iceberg Guard,
     Spoof-Filtered Stoikov Micro-Price, Exponential Distance Decay e^(-λd)).
  3. Side-by-side similarity metrics (∫min(P_v1, P_v2)dx in [0,1], consensus on
     real moves, and FILTER_V2_AKTIF on distant spoof walls & iceberg absorption).
  4. Chronological walk-forward head-to-head audit (v1 vs v2 vs ensemble).
  5. All /api/fokker-planck-v2/* endpoints alongside original v1 & screener APIs.
"""

from __future__ import annotations

import asyncio
import math

import httpx

from backend import fokker_planck as fp_v1
from backend import fokker_planck_v2 as fp_v2
from backend.sample_data import seed_klines
from backend.server import app


def test_v1_untouched_and_side_by_side_consensus_vs_filters():
    # 1. Baseline calm market -> v1 and v2 strongly agree (overlap > 95%)
    calm = fp_v2.compare_orderbook_v1_v2(
        bid_depth=120, ask_depth=120, taker_buy_vol=45, taker_sell_vol=45,
        canceled_bid_vol=10, canceled_ask_vol=10, sigma_per_sec=0.0005,
    )
    direct_v1 = fp_v1.analyze_orderbook_fp(
        bid_depth=120, ask_depth=120, taker_buy_vol=45, taker_sell_vol=45,
        canceled_bid_vol=10, canceled_ask_vol=10, sigma_per_sec=0.0005,
    )
    # Embedded v1 result must be 100% identical to standalone Model v1
    assert calm["v1"] == direct_v1
    assert calm["comparison"]["status"] == "SEPAKAT_KUAT"
    assert calm["comparison"]["pdfOverlap"] > 0.95
    assert len(calm["overlayCurve"]) == 121

    # 2. Distant Ask Wall (0.8% away): v1 sees raw -0.67 OBI; v2 decays it to ~0.00
    far_wall = fp_v2.compare_orderbook_v1_v2(
        bid_depth=115, ask_depth=110, far_ask_depth=480, wall_distance_pct=0.008,
        taker_buy_vol=46, taker_sell_vol=44, sigma_per_sec=0.0006,
    )
    assert far_wall["v1"]["microstructure"]["obi"] < -0.65
    assert abs(far_wall["v2"]["microstructure"]["obi"]) < 0.05
    assert far_wall["v2"]["regime"] == "DISTANT_SPOOF_FILTERED"
    assert far_wall["comparison"]["status"] == "FILTER_V2_AKTIF"
    assert far_wall["v2"]["metrics"]["pDownside"] < far_wall["v1"]["metrics"]["pDownside"]

    # 3. Iceberg Buy Absorption: v1 flags REAL_SELL_PRESSURE; v2 catches stalled price
    iceberg = fp_v2.compare_orderbook_v1_v2(
        bid_depth=150, ask_depth=105, taker_buy_vol=16, taker_sell_vol=96,
        canceled_bid_vol=6, canceled_ask_vol=10, price_change_bps=0.0, sigma_per_sec=0.0008,
    )
    assert iceberg["v1"]["regime"] == "REAL_SELL_PRESSURE"
    assert iceberg["v2"]["regime"] == "ICEBERG_BUY_ABSORPTION"
    assert iceberg["comparison"]["status"] == "FILTER_V2_AKTIF"
    assert iceberg["v2"]["metrics"]["pStable"] > iceberg["v1"]["metrics"]["pStable"] + 0.30

    # 4. Genuine Panic Dump: BOTH v1 and v2 agree on REAL_SELL_PRESSURE
    real_dump = fp_v2.compare_orderbook_v1_v2(
        bid_depth=45, ask_depth=220, taker_buy_vol=18, taker_sell_vol=95,
        canceled_bid_vol=15, canceled_ask_vol=25, price_change_bps=-26.0, sigma_per_sec=0.0011,
    )
    assert real_dump["v1"]["regime"] == "REAL_SELL_PRESSURE"
    assert real_dump["v2"]["regime"] == "REAL_SELL_PRESSURE"
    assert real_dump["comparison"]["sameRegime"] is True
    assert real_dump["comparison"]["pdfOverlap"] > 0.85


def test_v2_micro_price_and_exponential_decay_math():
    mp = fp_v2.compute_micro_price(best_bid=100.0, best_ask=100.20, bid_qty=90.0, ask_qty=10.0)
    assert abs(mp["microPrice"] - 100.18) < 1e-9
    assert abs(mp["microImbalance"] - 0.90) < 1e-9

    mp_cancel = fp_v2.compute_micro_price(
        best_bid=100.0, best_ask=100.20, bid_qty=90.0, ask_qty=10.0, bid_cancel_rate=0.95
    )
    assert mp_cancel["microPrice"] < mp["microPrice"]

    lam_calm = fp_v2.adaptive_decay_lambda(0.0003, 30.0)
    lam_fast = fp_v2.adaptive_decay_lambda(0.0020, 30.0)
    assert lam_calm > lam_fast
    assert math.exp(-lam_fast * 0.010) < 0.012

    # Taker priority beats passive ask wall
    res = fp_v2.analyze_orderbook_fp_v2(
        bid_depth=60, ask_depth=300, taker_buy_vol=95, taker_sell_vol=18, sigma_per_sec=0.0009
    )
    assert res["microstructure"]["takerPriorityShare"] >= 0.80
    assert res["microstructure"]["muRealBpsPerSec"] > 0
    assert res["metrics"]["pUpside"] > res["metrics"]["pDownside"]


def test_live_snapshot_comparator_never_pollutes_v1_cache():
    fp_v1._PREV_BOOK_CACHE.clear()
    fp_v2._PREV_BOOK_CACHE_V2.clear()
    fp_v2._PREV_BOOK_RAW_CACHE_FOR_CMP.clear()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/depth"):
            assert request.url.params.get("limit") == "100"
            return httpx.Response(
                200,
                json={
                    "bids": [["99.98", "60"], ["99.95", "55"]],
                    "asks": [["100.02", "60"], ["100.05", "50"], ["100.85", "650"]],
                },
            )
        if request.url.path.endswith("/trades"):
            trades = [
                {"price": "100.00", "qty": "2.0", "time": 1_700_000_000_000 + i * 500, "isBuyerMaker": (i % 2 == 0)}
                for i in range(16)
            ]
            return httpx.Response(200, json=trades)
        return httpx.Response(500)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await fp_v2.compare_microstructure_snapshot_v1_v2(client, "CMPUSDT")

    snap = asyncio.run(go())
    assert snap["live"] is True
    assert len(fp_v1._PREV_BOOK_CACHE) == 0  # v1 cache completely untouched!
    assert snap["v1"]["microstructure"]["obi"] < -0.60
    assert abs(snap["v2"]["microstructure"]["obi"]) < 0.08
    assert snap["comparison"]["status"] == "FILTER_V2_AKTIF"


def test_v2_api_routes_and_walkforward_showdown():
    async def go():
        app.state.http = httpx.AsyncClient()
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
                r_v1_spec = await ac.get("/api/fokker-planck/spec")
                r_v2_spec = await ac.get("/api/fokker-planck-v2/spec")
                r_uni = await ac.get(
                    "/api/fokker-planck-v2/compare/universe?limit=8&horizonDays=7&stableBand=0.05&source=seed"
                )
                r_asset = await ac.get(
                    "/api/fokker-planck-v2/compare/asset/BTCUSDT?horizonDays=7&stableBand=0.05&source=seed"
                )
                r_bt = await ac.post(
                    "/api/fokker-planck-v2/compare/backtest",
                    json={
                        "horizonDays": 7,
                        "stableBand": 0.05,
                        "stepDays": 10,
                        "lookbackDays": 160,
                        "limit": 8,
                        "source": "seed",
                    },
                )
                preset_res = []
                for p in r_v2_spec.json()["presets"]:
                    clean = {k: v for k, v in p["params"].items() if v is not None}
                    resp = await ac.post("/api/fokker-planck-v2/compare/simulate", json=clean)
                    preset_res.append((p["id"], resp))
                return r_v1_spec, r_v2_spec, r_uni, r_asset, r_bt, preset_res
        finally:
            await app.state.http.aclose()

    r_v1_spec, r_v2_spec, r_uni, r_asset, r_bt, preset_res = asyncio.run(go())

    # v1 still has its exact original 6 presets untouched
    assert r_v1_spec.status_code == 200 and len(r_v1_spec.json()["presets"]) == 6
    assert r_v2_spec.status_code == 200 and len(r_v2_spec.json()["presets"]) == 8

    uni = r_uni.json()
    assert r_uni.status_code == 200 and uni["ok"] is True and uni["count"] == 8
    assert 0.0 <= uni["summary"]["avgOverlap"] <= 1.0

    asset = r_asset.json()
    assert r_asset.status_code == 200 and asset["symbol"] == "BTCUSDT"
    assert len(asset["candleCompare"]["overlayCurve"]) == 121
    assert asset["microCompare"]["ok"] is True

    bt = r_bt.json()
    assert r_bt.status_code == 200 and bt["ok"] is True
    assert 0.0 <= bt["v1"]["scores"]["brierStable"] <= 1.0
    assert 0.0 <= bt["v2"]["scores"]["brierStable"] <= 1.0
    assert 0.0 <= bt["headToHead"]["regimeAgreementRate"] <= 1.0
    assert len(bt["coverageComparison"]) == 4

    for pid, resp in preset_res:
        assert resp.status_code == 200, pid
        body = resp.json()
        for ver in ("v1", "v2"):
            m = body[ver]["metrics"]
            assert abs(m["pDownside"] + m["pStable"] + m["pUpside"] - 1.0) < 1e-5, (pid, ver)
        assert 0.0 <= body["comparison"]["pdfOverlap"] <= 1.0, pid


def _make_5m_series(
    n: int,
    *,
    ret_per_bar: float,
    buy_share: float,
    wick: float = 0.0020,
    vol: float = 100.0,
) -> list[dict]:
    px = 100.0
    out = []
    for i in range(n):
        alt = (1 if i % 2 == 0 else -1) * 0.0012
        op = px
        cl = op * math.exp(ret_per_bar + alt)
        hi = max(op, cl) * (1.0 + wick)
        lo = min(op, cl) * (1.0 - wick)
        v = vol * (1.15 if i >= n - 8 else 1.0)
        tr = 240.0 if i >= n - 8 else 180.0
        out.append(
            {
                "date": 1_700_000_000_000 + i * 300_000,
                "open": op,
                "high": hi,
                "low": lo,
                "close": cl,
                "volume": v,
                "quoteVolume": v * cl,
                "trades": tr,
                "takerBuyBase": v * buy_share,
            }
        )
        px = cl
    return out


def test_intraday_15_30m_forecast_direction_neutral_and_absorption():
    # 1. Persistent taker-buy CVD + uptrend -> CONDONG NAIK KUAT
    bull_bars = _make_5m_series(90, ret_per_bar=0.0008, buy_share=0.72)
    bull = fp_v2.forecast_intraday_fp(bull_bars, horizon_min=30.0)
    assert bull["ok"] is True
    assert bull["call"]["code"] == "NAIK_KUAT"
    assert bull["stats"]["pUp"] > 0.58
    assert abs(bull["stats"]["pUp"] + bull["stats"]["pDown"] - 1.0) < 1e-4
    assert bull["stats"]["pTouchTargetFirst"] > 0.58
    assert bull["consensus"]["bothActiveAgree"] is True
    q = bull["stats"]["priceQuantiles"]
    assert q["p10"] < q["p25"] < q["median"] < q["p75"] < q["p90"]

    # 2. Persistent taker-sell CVD + downtrend -> CONDONG TURUN KUAT
    bear_bars = _make_5m_series(90, ret_per_bar=-0.0008, buy_share=0.28)
    bear = fp_v2.forecast_intraday_fp(bear_bars, horizon_min=15.0)
    assert bear["ok"] is True
    assert bear["call"]["code"] == "TURUN_KUAT"
    assert bear["stats"]["pDown"] > 0.58
    assert bear["stats"]["pTouchStopFirst"] > 0.58
    assert bear["consensus"]["bothActiveAgree"] is True

    # 3. Pure balanced chop (50% taker buy, zero drift) -> NETRAL_TUNGGU (don't gamble!)
    flat_bars = _make_5m_series(90, ret_per_bar=0.0, buy_share=0.50)
    flat = fp_v2.forecast_intraday_fp(flat_bars, horizon_min=30.0)
    assert flat["call"]["code"] == "NETRAL_TUNGGU"
    assert abs(flat["stats"]["pUp"] - 0.50) < 0.03

    # 4. 15m Passive Buy Absorption: heavy market-sell volume in last 3 bars but price refuses to fall
    abs_bars = _make_5m_series(90, ret_per_bar=0.0, buy_share=0.50)
    anchor_px = abs_bars[-4]["close"]
    for j in (-3, -2, -1):
        abs_bars[j] = {
            "date": abs_bars[j]["date"],
            "open": anchor_px,
            "high": anchor_px * 1.0018,
            "low": anchor_px * 0.9982,
            "close": anchor_px * 1.0001,  # holds firm despite heavy selling!
            "volume": 220.0,
            "quoteVolume": 220.0 * anchor_px,
            "trades": 320.0,
            "takerBuyBase": 220.0 * 0.16,  # 84% taker sell!
        }
    absorbed = fp_v2.forecast_intraday_fp(abs_bars, horizon_min=30.0)
    assert absorbed["signals"]["absorptionSide"] == "BUY"
    assert absorbed["signals"]["absorptionIndex"] >= 0.40
    # v2 protects against panic-shorting into an iceberg floor (higher pUp than naive v1)
    assert absorbed["stats"]["pUp"] > absorbed["v1Baseline"]["pUp"]


def test_intraday_api_get_post_and_walkforward_backtest():
    async def go():
        app.state.http = httpx.AsyncClient()
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
                r_get = await ac.get(
                    "/api/fokker-planck-v2/intraday/BTCUSDT?horizonMin=30&source=seed"
                )
                r_post = await ac.post(
                    "/api/fokker-planck-v2/intraday/SOLUSDT",
                    json={
                        "horizonMin": 15,
                        "source": "seed",
                        "tfi": 0.45,
                        "obi": 0.22,
                        "spoofIndex": 0.05,
                        "absorptionIndex": 0.0,
                        "accumulatedMin": 6.5,
                    },
                )
                return r_get, r_post
        finally:
            await app.state.http.aclose()

    r_get, r_post = asyncio.run(go())
    assert r_get.status_code == 200
    b_get = r_get.json()
    assert b_get["ok"] is True and b_get["symbol"] == "BTCUSDT"
    assert [h["horizonMin"] for h in b_get["horizonMatrix"]] == [15, 30, 60]
    assert b_get["backtest"]["ok"] is True
    assert b_get["backtest"]["totalSamples"] >= 40
    assert len(b_get["backtest"]["recentSignals"]) >= 5

    assert r_post.status_code == 200
    b_post = r_post.json()
    assert b_post["ok"] is True and b_post["microSource"] == "browser-ws-accumulator"
    assert b_post["forecast"]["signals"]["orderbookLive"]["accumulatedMin"] == 6.5

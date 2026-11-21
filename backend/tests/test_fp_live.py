"""Tests for the isolated FP Live (5·15·30·60m) engine & API endpoints."""

from __future__ import annotations

import asyncio
import math

import httpx

from backend import fp_live
from backend.server import app


def _make_1m_and_5m(
    n_1m: int = 450,
    *,
    ret_per_1m: float = 0.0,
    buy_share: float = 0.50,
    mean_revert: bool = False,
):
    px = 95_000.0
    b1 = []
    for i in range(n_1m):
        cyc = (1 if i % 2 == 0 else -1) * 0.00045
        if mean_revert:
            dev = math.log(px / 95_000.0)
            lr = -0.18 * dev + cyc
        else:
            lr = ret_per_1m + cyc
        op = px
        cl = op * math.exp(lr)
        hi = max(op, cl) * 1.0006
        lo = min(op, cl) * 0.9994
        vol = 20.0 * (1.2 if i >= n_1m - 30 else 1.0)
        b1.append(
            {
                "date": 1_700_000_000_000 + i * 60_000,
                "open": op,
                "high": hi,
                "low": lo,
                "close": cl,
                "volume": vol,
                "quoteVolume": vol * cl,
                "trades": 300.0 * (vol / 20.0),
                "takerBuyBase": vol * buy_share,
            }
        )
        px = cl

    b5 = []
    for j in range(0, len(b1) - 4, 5):
        g = b1[j : j + 5]
        b5.append(
            {
                "date": g[0]["date"],
                "open": g[0]["open"],
                "high": max(x["high"] for x in g),
                "low": min(x["low"] for x in g),
                "close": g[-1]["close"],
                "volume": sum(x["volume"] for x in g),
                "quoteVolume": sum(x["quoteVolume"] for x in g),
                "trades": sum(x["trades"] for x in g),
                "takerBuyBase": sum(x["takerBuyBase"] for x in g),
            }
        )
    return b1, b5


def test_absorbing_barrier_pde_symmetry_and_conservation():
    # Zero drift & symmetric diffusion -> exact 50/50 split + mass conservation = 1.0
    sym = fp_live.solve_absorbing_first_passage(
        lambda _x, _t: 0.0,
        lambda _x, _t: 0.0008**2,
        band=0.004,
        horizon=30.0,
    )
    assert abs(sym["pHitTargetFirst"] - sym["pHitStopFirst"]) < 1e-4
    assert abs(sym["condTargetVsStop"] - 0.5) < 1e-4
    assert abs(sym["pHitTargetFirst"] + sym["pHitStopFirst"] + sym["pNeitherYet"] - 1.0) < 1e-3
    assert sym["pNeitherYet"] > 0.02

    # Positive drift -> higher target hit probability than stop hit
    pos = fp_live.solve_absorbing_first_passage(
        lambda _x, t: 0.00018 * math.exp(-t / 24.0),
        lambda _x, _t: 0.0008**2,
        band=0.004,
        horizon=30.0,
    )
    assert pos["pHitTargetFirst"] > pos["pHitStopFirst"] + 0.15
    assert abs(pos["pHitTargetFirst"] + pos["pHitStopFirst"] + pos["pNeitherYet"] - 1.0) < 1e-3


def test_orderbook_walls_top5_and_distant_spoof_filter():
    mid = 100_000.0
    bids = [(mid - 20 * i, 1.2) for i in range(1, 45)]
    asks = [(mid + 20 * i, 1.2) for i in range(1, 45)]
    # Add real near support wall (-0.08%) and distant fake ask spoof wall (+0.85%)
    bids.append((99_920.0, 22.0))
    asks.append((100_850.0, 65.0))

    ob = fp_live.analyze_orderbook_walls(bids, asks, decay_lambda=360.0, trades_tfi=0.25)
    assert ob is not None
    # Raw OBI is fooled bearish by fake ask wall, while weighted e^(-λd) OBI stays bullish!
    assert ob["rawObi"] < -0.10
    assert ob["weightedObi"] > 0.05
    assert len(ob["topSupports"]) == 5
    assert len(ob["topResistances"]) == 5
    assert ob["activeBidWall"]["price"] == 99_920.0
    assert len(ob["spoofWalls"]) >= 1
    assert ob["spoofWalls"][0]["side"] == "ASK"
    assert ob["spoofWalls"][0]["cutPct"] >= 85


def test_fp_live_bull_bear_chop_btc_guard_and_rounded_narrative():
    # 1. Persistent bullish 1m+5m flow
    b1_up, b5_up = _make_1m_and_5m(450, ret_per_1m=0.00018, buy_share=0.72)
    book_up = fp_live.seed_orderbook_walls("BTCUSDT", b1_up[-1]["close"])
    up = fp_live.forecast_fp_live(b5_up, symbol="BTCUSDT", horizon_min=30, candles_1m=b1_up, book=book_up)
    assert up["ok"] is True
    assert up["call"]["short"] == "NAIK"
    assert up["stats"]["pUp"] > 0.58
    assert up["narrative"]["headline"].startswith("Dibulatkan: CONDONG NAIK")
    assert up["narrative"]["bullCount"] >= 3
    assert "waitRange" in up["narrative"]["keyLevels"]
    assert len(up["components"]) == 5

    # 2. Persistent bearish flow
    b1_dn, b5_dn = _make_1m_and_5m(450, ret_per_1m=-0.00018, buy_share=0.28)
    dn = fp_live.forecast_fp_live(b5_dn, symbol="ETHUSDT", horizon_min=15, candles_1m=b1_dn)
    assert dn["call"]["short"] == "TURUN"
    assert dn["stats"]["pDown"] > 0.58
    assert dn["narrative"]["headline"].startswith("Dibulatkan: CONDONG TURUN")

    # 3. Mean-reverting choppy sideways -> VR <= 0.86 & honest NETRAL when centered
    b1_ch, b5_ch = _make_1m_and_5m(450, ret_per_1m=0.0, buy_share=0.52, mean_revert=True)
    ch = fp_live.forecast_fp_live(b5_ch, symbol="SOLUSDT", horizon_min=30, candles_1m=b1_ch)
    assert ch["regime"]["code"] == "BOLAK_BALIK"
    assert ch["call"]["short"] == "NETRAL"
    assert "NETRAL" in ch["narrative"]["headline"]

    # 4. BTC Leader Guard dampens altcoin bullish call when BTC is dumping heavily
    alt_free = fp_live.forecast_fp_live(b5_up, symbol="SOLUSDT", horizon_min=30, candles_1m=b1_up)
    alt_damped = fp_live.forecast_fp_live(
        b5_up,
        symbol="SOLUSDT",
        horizon_min=30,
        candles_1m=b1_up,
        btc_context={"cvd15m": -0.42, "cvd30m": -0.35, "ret30mPct": -0.85},
    )
    assert alt_damped["btcGuard"]["conflict"] is True
    assert alt_damped["stats"]["pUp"] < alt_free["stats"]["pUp"]


def test_fp_live_api_get_and_post():
    async def go():
        app.state.http = httpx.AsyncClient()
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
                r1 = await ac.get("/api/fp-live/BTCUSDT?horizonMin=30&source=seed")
                r2 = await ac.post(
                    "/api/fp-live/SOLUSDT",
                    json={
                        "horizonMin": 15,
                        "source": "seed",
                        "tfi": 0.44,
                        "obi": 0.25,
                        "spoofIndex": 0.08,
                        "absorptionIndex": 0.05,
                        "accumulatedMin": 6.0,
                    },
                )
                return r1, r2
        finally:
            await app.state.http.aclose()

    r1, r2 = asyncio.run(go())
    assert r1.status_code == 200
    d1 = r1.json()
    assert d1["ok"] is True and d1["symbol"] == "BTCUSDT"
    assert [m["horizonMin"] for m in d1["horizonMatrix"]] == [5, 15, 30, 60]
    assert len(d1["orderbook"]["topSupports"]) == 5
    assert len(d1["orderbook"]["topResistances"]) == 5
    assert d1["backtest"]["ok"] is True

    assert r2.status_code == 200
    d2 = r2.json()
    assert d2["ok"] is True and d2["wsActive"] is True
    assert d2["forecast"]["btcGuard"] is not None


def test_fp_live_real_binance_json_format_mock():
    """Verify exact live Binance JSON parsing for 5m klines, 1m klines, depth100, and trades500."""
    from backend import market_data

    def _raw_kline(ts_ms: int, px: float, vol: float, tb: float):
        return [
            ts_ms,
            f"{px:.2f}",
            f"{px*1.0012:.2f}",
            f"{px*0.9990:.2f}",
            f"{px*1.0003:.2f}",
            f"{vol:.4f}",
            ts_ms + 59_999,
            f"{vol*px:.2f}",
            420,
            f"{tb:.4f}",
            f"{tb*px:.2f}",
            "0",
        ]

    async def fake_fetch(_client, url: str, timeout: float = 5.0):
        if "interval=5m" in url:
            return [_raw_kline(1_700_000_000_000 + i * 300_000, 95_000 + i * 12, 80.0, 54.0) for i in range(180)]
        if "interval=1m" in url:
            return [_raw_kline(1_700_050_000_000 + i * 60_000, 96_800 + i * 4, 18.0, 12.6) for i in range(90)]
        if "/depth?" in url:
            mid = 97_160.0
            bids = [[f"{mid - 5*i:.2f}", f"{15.0 if i == 6 else 1.1:.4f}"] for i in range(1, 61)]
            asks = [[f"{mid + 5*i:.2f}", f"{18.0 if i == 8 else 1.0:.4f}"] for i in range(1, 61)]
            return {"lastUpdateId": 999, "bids": bids, "asks": asks}
        if "/trades?" in url:
            return [
                {"price": "97160.0", "qty": "0.25", "time": 1_700_055_000_000 + i * 400, "isBuyerMaker": (i % 3 == 0)}
                for i in range(150)
            ]
        raise RuntimeError(f"unexpected url {url}")

    orig = market_data._fetch_json
    market_data._fetch_json = fake_fetch
    try:
        async def go():
            async with httpx.AsyncClient() as c:
                return await fp_live.build_fp_live_bundle(c, "ETHUSDT", horizon_min=30, prefer_seed=False)

        res = asyncio.run(go())
    finally:
        market_data._fetch_json = orig

    assert res["ok"] is True
    assert res["live"] is True and res["live1m"] is True and res["liveBook"] is True
    assert res["orderbook"]["levelsCount"] == 120
    assert res["forecast"]["call"]["short"] == "NAIK"
    assert res["forecast"]["btcGuard"] is not None
    fp = res["forecast"]["firstPassage"]
    assert abs(fp["pHitTargetFirst"] + fp["pHitStopFirst"] + fp["pNeitherYet"] - 1.0) < 1.5e-3


def _money_levels(txt: str) -> list[float]:
    import re

    return [float(x.replace(",", "")) for x in re.findall(r"\$([0-9,]+\.[0-9]+)", txt)]


def test_wait_and_target_levels_never_cross_spot_price():
    """Guard penyajian: level tunggu selalu di bawah spot, target selalu di atas spot.

    Tidak mengubah rumus PDE/CVD — hanya memastikan level yang ditampilkan tidak
    menyilang harga saat ini ketika tembok order book menempel tepat di harga.
    """
    for drift, share in ((0.00016, 0.62), (-0.00022, 0.38), (0.0, 0.50)):
        b1, b5 = _make_1m_and_5m(450, ret_per_1m=drift, buy_share=share, mean_revert=(drift == 0.0))
        for h in (5, 15, 30, 60):
            fc = fp_live.forecast_fp_live(b5, symbol="BTCUSDT", horizon_min=h, candles_1m=b1)
            assert fc["ok"] is True
            nar = fc["narrative"]
            px = fc["price"]
            short = fc["call"]["short"]
            waits = _money_levels(nar["keyLevels"]["waitRange"])
            targets = _money_levels(nar["keyLevels"]["targetRange"])
            assert waits and targets
            if short == "NAIK":
                assert all(w < px for w in waits), (short, h, waits, px)
                assert all(t > px for t in targets), (short, h, targets, px)
            elif short == "TURUN":
                assert all(t > px for t in targets), (short, h, targets, px)
                assert all(w < px for w in waits), (short, h, waits, px)
            else:
                assert all(w < px for w in waits), (short, h, waits, px)
                assert all(t > px for t in targets), (short, h, targets, px)


def test_ws_midprice_guard_rejects_absurd_browser_price():
    """midPrice WS dari browser yang menyimpang jauh tidak boleh menggeser harga acuan."""
    from backend.server import app

    async def go():
        transport = httpx.ASGITransport(app=app)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
                sane = await c.post(
                    "/api/fp-live/BTCUSDT",
                    json={"horizonMin": 30, "tfi": 0.1, "obi": 0.0, "accumulatedMin": 5.0, "source": "seed"},
                )
                bogus = await c.post(
                    "/api/fp-live/BTCUSDT",
                    json={
                        "horizonMin": 30,
                        "tfi": 0.1,
                        "obi": 0.0,
                        "accumulatedMin": 5.0,
                        "source": "seed",
                        "midPrice": 1_000.0,
                        "microPrice": 1_000.0,
                    },
                )
                return sane, bogus

    sane, bogus = asyncio.run(go())
    assert sane.status_code == 200 and bogus.status_code == 200
    p_sane = sane.json()["forecast"]["price"]
    p_bogus = bogus.json()["forecast"]["price"]
    assert abs(p_bogus / p_sane - 1.0) < 0.25, (p_sane, p_bogus)
    assert "midPrice WS" in (bogus.json().get("warning") or "")


def test_source_notes_flag_synthetic_1m_feed():
    """Payload harus menyatakan jujur mana feed live dan mana sintetis/seed."""
    b1, b5 = _make_1m_and_5m(450)
    fc = fp_live.forecast_fp_live(b5, symbol="BTCUSDT", horizon_min=30, candles_1m=b1)
    assert fc["ok"] is True

    async def go():
        async with httpx.AsyncClient() as c:
            return await fp_live.build_fp_live_bundle(c, "ETHUSDT", horizon_min=30, prefer_seed=True)

    res = asyncio.run(go())
    assert res["ok"] is True
    notes = res["sourceNotes"]
    assert notes["k5m"] == "seed" and notes["book"] == "seed"
    assert notes["k1m"] in {"live", "sintetis-dari-5m"}
    assert res["orderbook"]["qtyUnit"] == "koin"

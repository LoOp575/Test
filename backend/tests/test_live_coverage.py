"""Regression: `klinesLive` menghitung simbol yang BERHASIL, bukan yang gagal.

Banner produksi pernah menulis "klines 18/18 simbol gagal" padahal 18/18 itu
justru simbol yang datanya asli dari bursa. Angka di API benar; yang dijaga di
sini adalah semantiknya supaya UI tidak bisa lagi salah membacanya.
"""

import asyncio
import httpx
import pytest

from backend.server import app

SYMBOLS = [f"C{i}USDT" for i in range(18)]
FULL = SYMBOLS[:14]      # riwayat penuh -> bisa dinilai model
SHORT = SYMBOLS[14:]     # koin baru, 59 candle -> data asli tapi belum cukup


def _klines(n):
    return [
        [1_700_000_000_000 + i * 86_400_000, "100", "110", "90", "105.0", "1000", "0",
         "105000", 500, "600", "0"]
        for i in range(n)
    ]


def _ticker():
    return [
        {"symbol": s, "lastPrice": "105.0", "priceChangePercent": "1.2", "highPrice": "110",
         "lowPrice": "95", "volume": "1000", "quoteVolume": "50000000"}
        for s in SYMBOLS
    ]


class _Exchange(httpx.AsyncBaseTransport):
    """Reproduksi persis log produksi: vision menjawab, US tidak punya simbolnya."""

    async def handle_async_request(self, request):
        host, path = request.url.host, request.url.path
        if "ticker" in path:
            return httpx.Response(200, json=_ticker())
        symbol = request.url.params.get("symbol")
        if "vision" in host:
            return httpx.Response(200, json=_klines(120 if symbol in FULL else 59))
        if "binance.us" in host:
            return httpx.Response(400, json={"code": -1121, "msg": "Invalid symbol."})
        if "api.binance.com" in host:
            return httpx.Response(200, json=_klines(40 if symbol in SHORT else 120))
        return httpx.Response(451, json={})


def _screen(payload_limit=18):
    async def go():
        async with app.router.lifespan_context(app):
            app.state.http = httpx.AsyncClient(transport=_Exchange())
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
                return (await client.get(f"/api/screen?limit={payload_limit}")).json()

    return asyncio.run(go())


@pytest.fixture(scope="module")
def payload():
    return _screen()


def test_klines_live_counts_successes_not_failures(payload):
    cov = payload["liveCoverage"]
    assert cov["klinesTotal"] == 18
    assert cov["klinesLive"] == 18          # semua simbol DIJAWAB bursa
    assert cov["klinesUsable"] == 14        # 14 cukup untuk model
    assert cov["klinesShort"] == 4          # 4 koin baru, data asli tapi pendek
    assert cov["universeLive"] is True


def test_partial_banner_inputs_are_consistent(payload):
    """`total - live` = simbol yang benar-benar jatuh ke seed (di sini: nol)."""
    cov = payload["liveCoverage"]
    failed = max(0, cov["klinesTotal"] - cov["klinesLive"])
    assert failed == 0
    assert not any(r["sources"]["klines"] == "seed-fallback" for r in payload["data"])


def test_live_flag_stays_false_when_some_coins_are_unscorable(payload):
    """Jujur: `live` hanya true bila semua simbol nyata DAN cukup."""
    assert payload["live"] is False


def test_short_history_rows_carry_bars_and_reason(payload):
    short_rows = [r for r in payload["data"] if r["verdict"] == "INSUFFICIENT_DATA"]
    assert len(short_rows) == 4
    for row in short_rows:
        assert row["bars"] == 59
        assert row["minBars"] == 61
        assert row["sources"]["live"] is True
        assert row["sources"]["klines"] == "binance-vision"


def test_scorable_rows_are_scored_normally(payload):
    """Rumus tidak berubah: koin dengan riwayat penuh tetap dinilai penuh."""
    ok_rows = [r for r in payload["data"] if r["ok"]]
    assert len(ok_rows) == 14
    for row in ok_rows:
        assert row["verdict"] != "INSUFFICIENT_DATA"
        assert "scores" in row and "signal" in row["scores"]

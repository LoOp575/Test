"""Pencarian & halaman koin: tidak boleh ada data demo, dan koin yang ada harus ketemu.

Tiga kasus yang dibedakan tegas:
- koin terdaftar + riwayat cukup  -> dinilai penuh
- koin terdaftar + riwayat pendek -> 200, ditandai ``insufficient`` (data ASLI)
- ticker tidak terdaftar          -> 404 ("tidak terdaftar"), BUKAN candle seed
- bursa tidak terjangkau          -> 503 ("belum bisa dipastikan"), juga bukan seed
"""

import asyncio
import httpx

from backend.market_data import screen_cache
from backend.server import app

FULL = [f"COIN{i}USDT" for i in range(6)]
SHORT = ["NEWAUSDT", "NEWBUSDT"]
LISTED = FULL + SHORT


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
        for s in LISTED
    ]


class _Exchange(httpx.AsyncBaseTransport):
    """Binance menjawab -1121 untuk ticker yang tidak ada (bukan array kosong)."""

    def __init__(self, down: bool = False):
        self.down = down

    async def handle_async_request(self, request):
        if self.down:
            raise httpx.ConnectError("down", request=request)
        path = request.url.path
        if "ticker" in path:
            return httpx.Response(200, json=_ticker())
        symbol = request.url.params.get("symbol")
        if symbol not in LISTED:
            return httpx.Response(400, json={"code": -1121, "msg": "Invalid symbol."})
        return httpx.Response(200, json=_klines(120 if symbol in FULL else 40))


def _call(path, down=False):
    screen_cache._store.clear()

    async def go():
        async with app.router.lifespan_context(app):
            app.state.http = httpx.AsyncClient(transport=_Exchange(down))
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
                response = await client.get(path)
                return response.status_code, response.json()

    return asyncio.run(go())


# ---------------------------------------------------------------- search


def test_search_finds_coin_outside_the_loaded_page():
    """Kotak pencarian dulu hanya menyaring 18 baris; sekarang menyapu bursa."""
    code, payload = _call("/api/search?q=COIN5")
    assert code == 200
    assert [m["symbol"] for m in payload["matches"]] == ["COIN5USDT"]
    assert payload["universeSize"] == len(LISTED)


def test_search_is_case_and_punctuation_insensitive():
    code, payload = _call("/api/search?q=newa")
    assert code == 200
    assert payload["matches"][0]["symbol"] == "NEWAUSDT"


def test_search_ranks_exact_match_first():
    code, payload = _call("/api/search?q=COIN")
    assert code == 200
    names = [m["symbol"] for m in payload["matches"]]
    assert len(names) == len(FULL)          # semua prefix COIN ditemukan
    assert names[0].startswith("COIN")


def test_search_reports_zero_matches_clearly():
    code, payload = _call("/api/search?q=ZZZZ")
    assert code == 200
    assert payload["matches"] == []
    assert payload["universeSize"] == len(LISTED)   # bukan "tidak bisa dicari"


def test_search_does_not_crash_when_exchange_is_down():
    code, payload = _call("/api/search?q=COIN", down=True)
    assert code == 200
    assert payload["matches"] == []
    assert "error" in payload


# ---------------------------------------------------------------- symbol page


def test_listed_coin_with_enough_history_is_scored():
    code, payload = _call("/api/screen/COIN3USDT")
    assert code == 200
    assert payload["verdict"] != "INSUFFICIENT_DATA"
    assert payload["sources"]["klines"] == "binance-vision"
    assert len(payload["series"]) == 90


def test_young_coin_returns_real_data_not_404_and_not_seed():
    code, payload = _call("/api/screen/NEWAUSDT")
    assert code == 200
    assert payload["insufficient"] is True
    assert payload["bars"] == 40
    assert payload["minBars"] == 61
    assert payload["sources"]["klines"] == "binance-vision"
    assert payload["sources"]["live"] is True
    assert len(payload["series"]) == 40
    assert payload["sources"]["klines"] != "seed-fallback"


def test_unknown_ticker_is_404_not_an_invented_analysis():
    """Dulu ticker ngawur dianalisis penuh dengan 90 candle sintetis."""
    code, payload = _call("/api/screen/NOTACOINUSDT")
    assert code == 404
    assert "tidak terdaftar" in payload["detail"]
    assert "series" not in payload


def test_unreachable_exchange_is_503_not_404():
    """Jaringan mati tidak boleh disimpulkan sebagai "koin tidak ada"."""
    code, payload = _call("/api/screen/COIN3USDT", down=True)
    assert code == 503
    assert "belum bisa dipastikan" in payload["detail"]


def test_no_path_ever_serves_seed_candles():
    """Satu-satunya endpoint koin tidak boleh menyentuh sample_data.seed_klines."""
    for symbol in ("COIN3USDT", "NEWAUSDT", "NOTACOINUSDT"):
        code, payload = _call(f"/api/screen/{symbol}")
        assert (payload.get("sources") or {}).get("klines") != "seed-fallback"

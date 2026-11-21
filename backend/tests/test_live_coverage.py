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


def _clear_screen_cache():
    """`screen_cache` bersifat global (TTL 60s); tes harus mulai dari nol."""
    from backend.market_data import screen_cache

    screen_cache._store.clear()


def _screen(payload_limit=18):
    _clear_screen_cache()

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


def test_live_flag_true_when_every_displayed_row_is_real_and_scorable(payload):
    """`live` menilai baris yang DITAMPILKAN, bukan kandidat mentah.

    Koin baru kini disaring keluar dari layar (lihat tes berikutnya), jadi
    seluruh 18 baris yang tampil benar-benar bisa dinilai -> `live` true.
    """
    assert payload["live"] is True
    assert all(r["ok"] for r in payload["data"])


def test_young_coins_are_filtered_out_of_the_display(payload):
    """Koin <61 hari tidak boleh memakan slot `Top 18` lalu jadi baris kosong."""
    assert [r for r in payload["data"] if r["verdict"] == "INSUFFICIENT_DATA"] == []
    assert payload["criteria"]["tooShort"] == 4
    assert payload["criteria"]["scorable"] == 14
    # Keempat koin muda tetap TERBACA sebagai data asli, cuma tidak ditampilkan.
    assert payload["liveCoverage"]["klinesShort"] == 4
    assert payload["liveCoverage"]["klinesLive"] == 18


def test_history_filter_is_claimed_only_for_pairs_actually_examined(payload):
    """Caption tidak boleh mengklaim '>=61 candle' untuk pool yang belum diperiksa."""
    crit = payload["criteria"]
    assert crit["examined"] == crit["universeTotal"]   # 18 kandidat <= batas probe
    assert "diperiksa" in crit["historyFilter"]
    assert all("candle harian" not in f for f in crit["filters"])


def test_scorable_rows_are_scored_normally(payload):
    """Rumus tidak berubah: koin dengan riwayat penuh tetap dinilai penuh."""
    ok_rows = [r for r in payload["data"] if r["ok"]]
    assert len(ok_rows) == 14
    for row in ok_rows:
        assert row["verdict"] != "INSUFFICIENT_DATA"
        assert "scores" in row and "signal" in row["scores"]


ALL_YOUNG_SYMBOLS = [f"Y{i}USDT" for i in range(18)]


class _AllYoungExchange(httpx.AsyncBaseTransport):
    """Semua pair baru berumur 59 hari: tidak ada yang bisa dinilai model."""

    async def handle_async_request(self, request):
        host, path = request.url.host, request.url.path
        if "ticker" in path:
            return httpx.Response(200, json=[
                {"symbol": s, "lastPrice": "105.0", "priceChangePercent": "1.2",
                 "highPrice": "110", "lowPrice": "95", "volume": "1000",
                 "quoteVolume": "50000000"}
                for s in ALL_YOUNG_SYMBOLS
            ])
        if "vision" in host:
            return httpx.Response(200, json=_klines(59))
        return httpx.Response(451, json={})


def test_all_young_universe_falls_back_to_showing_real_short_history():
    """Kalau tidak ada satu pun yang cukup, layar TIDAK kosong dan TIDAK menipu.

    Baris tetap ditampilkan dengan candle asli + bars/minBars, dan `live` false
    supaya UI memberi tahu bahwa belum ada yang bisa dinilai.
    """
    _clear_screen_cache()

    async def go():
        async with app.router.lifespan_context(app):
            app.state.http = httpx.AsyncClient(transport=_AllYoungExchange())
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
                return (await client.get("/api/screen?limit=18")).json()

    payload = asyncio.run(go())
    assert payload["live"] is False
    assert len(payload["data"]) == 18            # tidak kosong
    for row in payload["data"]:
        assert row["verdict"] == "INSUFFICIENT_DATA"
        assert row["bars"] == 59 and row["minBars"] == 61
        assert row["sources"]["live"] is True     # data ASLI, bukan seed
        assert row["sources"]["klines"] == "binance-vision"
    assert payload["criteria"]["scorable"] == 0
    assert payload["criteria"]["tooShort"] == 18


def test_young_coins_do_not_steal_slots_from_scorable_coins():
    """Inti perbaikan filter: koin baru tidak boleh menggeser koin yang bisa dinilai.

    Pool 60 pair diurut volume; 10 koin TERATAS adalah koin baru (<61 hari).
    Dulu `limit` memotong lebih dulu, jadi 10 slot terbuang dan hanya 8 koin
    yang bisa dinilai. Sekarang kandidat diprobe lebih banyak lalu disaring.
    """
    old_syms = [f"OLD{i}USDT" for i in range(50)]
    new_syms = [f"NEW{i}USDT" for i in range(10)]
    symbols = new_syms + old_syms                      # koin baru paling besar volume
    rank = {s: i for i, s in enumerate(symbols)}

    class Exchange(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            host, path = request.url.host, request.url.path
            if "ticker" in path:
                return httpx.Response(200, json=[
                    {"symbol": s,
                     "lastPrice": "105.0", "priceChangePercent": "1.2",
                     "highPrice": "110", "lowPrice": "95", "volume": "1000",
                     "quoteVolume": str(90_000_000 - rank[s] * 1_000_000)}
                    for s in symbols
                ])
            if "vision" in host:
                sym = request.url.params.get("symbol")
                return httpx.Response(200, json=_klines(120 if sym.startswith("OLD") else 40))
            return httpx.Response(451, json={})

    _clear_screen_cache()

    async def go():
        async with app.router.lifespan_context(app):
            app.state.http = httpx.AsyncClient(transport=Exchange())
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
                return (await client.get("/api/screen?limit=18")).json()

    payload = asyncio.run(go())
    crit, cov = payload["criteria"], payload["liveCoverage"]

    # 18 slot terisi penuh oleh koin yang BISA dinilai, bukan 8 + 10 kosong.
    assert len(payload["data"]) == 18
    assert all(r["ok"] for r in payload["data"])
    assert all(r["verdict"] != "INSUFFICIENT_DATA" for r in payload["data"])
    assert payload["live"] is True
    # Koin baru tetap diperiksa dan dilaporkan jujur, hanya tidak ditampilkan.
    assert cov["klinesShort"] == 10
    assert crit["tooShort"] == 10
    # 54 pair diprobe (limit 18 x 3), 10 di antaranya koin baru -> 44 scorable.
    assert crit["examined"] == 54
    assert crit["scorable"] == 44
    # Probe melebar melewati `limit` supaya penyaringan bisa terjadi.
    assert crit["examined"] > crit["returned"]
    # Rumus tidak berubah: baris yang dinilai punya skor penuh.
    for row in payload["data"]:
        assert "signal" in row["scores"]


def test_request_stays_bounded(monkeypatch):
    """Probe tidak boleh meledak: ada plafon, dan `limit` tetap dihormati."""
    from backend import server as srv

    assert srv._candidate_count(18) == 54
    # Selalu antara `limit` dan `limit x multiplier` (atau plafon, mana dulu).
    for limit in (4, 18, 40, 150):
        probes = srv._candidate_count(limit)
        assert probes >= limit
        assert probes <= max(limit, srv.KLINE_CANDIDATE_CAP)
    monkeypatch.setenv("KLINE_CANDIDATE_MULTIPLIER", "1")
    monkeypatch.setattr(srv, "KLINE_CANDIDATE_MULTIPLIER", 1)
    assert srv._candidate_count(18) == 18

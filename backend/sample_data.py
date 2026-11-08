"""
Deterministic synthetic market data.

Used ONLY as a last-resort fallback when no live exchange/aggregator endpoint is
reachable (e.g. offline sandboxes) or SCREEN_SOURCE=seed is set. Every payload
built here is labelled source="seed-fallback" and the UI shows a demo banner.

The generator is seeded from the symbol string so results are stable across
restarts and environments.

Regime design (so the demo shows a realistic verdict spread):
  quality   -> organic unit demand up sharply & ACCELERATING, buy-side strong (LONG/WATCH)
  watch     -> organic demand up moderately (WATCH)
  froth     -> price up, participation dying, buy-side weak (AVOID - low quality)
  distortion-> USD volume up on price, unit volume flat (NO SIGNAL - distortion)
  unstable  -> clustered abnormal-move days (AVOID - red flag)
  flat      -> no edge (NEUTRAL)
"""

from __future__ import annotations

import hashlib
import math
import random
import time
from typing import Any

DAY_MS = 86_400_000

_REGIMES: dict[str, dict[str, Any]] = {
    "BTCUSDT":  {"price0": 88000,  "drift": 0.0016, "vol": 0.021, "volTrend": 0.0028, "volAccel": 0.75, "taker": 0.545, "regime": "quality",    "baseVol": 18_000,  "spikes": []},
    "ETHUSDT":  {"price0": 2750,   "drift": 0.0018, "vol": 0.024, "volTrend": 0.0031, "volAccel": 0.80, "taker": 0.555, "regime": "quality",    "baseVol": 210_000, "spikes": []},
    "SOLUSDT":  {"price0": 128,    "drift": 0.0022, "vol": 0.030, "volTrend": 0.0035, "volAccel": 0.90, "taker": 0.565, "regime": "quality",    "baseVol": 2_400_000, "spikes": []},
    "LINKUSDT": {"price0": 11.2,   "drift": 0.0024, "vol": 0.028, "volTrend": 0.0037, "volAccel": 0.90, "taker": 0.575, "regime": "quality",    "baseVol": 6_800_000, "spikes": []},
    "INJUSDT":  {"price0": 9.6,    "drift": 0.0026, "vol": 0.046, "volTrend": 0.0039, "volAccel": 1.00, "taker": 0.558, "regime": "quality",    "baseVol": 3_400_000, "spikes": []},
    "AVAXUSDT": {"price0": 22.4,   "drift": 0.0010, "vol": 0.031, "volTrend": 0.0018, "volAccel": 0.35, "taker": 0.525, "regime": "watch",      "baseVol": 3_100_000, "spikes": []},
    "TONUSDT":  {"price0": 2.62,   "drift": 0.0008, "vol": 0.027, "volTrend": 0.0014, "volAccel": 0.30, "taker": 0.515, "regime": "watch",      "baseVol": 9_000_000, "spikes": []},
    "ARBUSDT":  {"price0": 0.34,   "drift": 0.0015, "vol": 0.034, "volTrend": 0.0022, "volAccel": 0.45, "taker": 0.540, "regime": "watch",      "baseVol": 72_000_000, "spikes": []},
    "DOGEUSDT": {"price0": 0.091,  "drift": 0.0042, "vol": 0.042, "volTrend": -0.0090, "volAccel": 0.0, "taker": 0.435, "regime": "froth",     "baseVol": 900_000_000, "spikes": []},
    "XRPUSDT":  {"price0": 0.52,   "drift": 0.0036, "vol": 0.036, "volTrend": -0.0095, "volAccel": 0.0, "taker": 0.425, "regime": "froth",     "baseVol": 700_000_000, "spikes": []},
    "BNBUSDT":  {"price0": 595,    "drift": 0.0028, "vol": 0.020, "volTrend": 0.0001,  "volAccel": 0.0, "taker": 0.500, "regime": "distortion", "baseVol": 950_000,  "spikes": []},
    "WIFUSDT":  {"price0": 1.35,   "drift": -0.0012, "vol": 0.052, "volTrend": 0.0021, "volAccel": 0.5, "taker": 0.460, "regime": "unstable",  "baseVol": 140_000_000, "spikes": [102, 103, 110]},
    "NEARUSDT": {"price0": 2.15,   "drift": 0.0002, "vol": 0.030, "volTrend": 0.0004,  "volAccel": 0.2, "taker": 0.495, "regime": "flat",      "baseVol": 22_000_000, "spikes": []},
    "ADAUSDT":  {"price0": 0.34,   "drift": -0.0004, "vol": 0.028, "volTrend": -0.0002, "volAccel": 0.1, "taker": 0.485, "regime": "flat",     "baseVol": 240_000_000, "spikes": []},
    "DOTUSDT":  {"price0": 3.35,   "drift": 0.0001, "vol": 0.027, "volTrend": 0.0002,  "volAccel": 0.2, "taker": 0.490, "regime": "flat",      "baseVol": 18_000_000, "spikes": []},
    "ATOMUSDT": {"price0": 4.10,   "drift": -0.0002, "vol": 0.026, "volTrend": -0.0004, "volAccel": 0.1, "taker": 0.480, "regime": "flat",     "baseVol": 12_000_000, "spikes": []},
}


def _rng(symbol: str) -> random.Random:
    digest = hashlib.sha256(f"meridian:{symbol}".encode()).hexdigest()
    return random.Random(int(digest[:16], 16))


def _regime(symbol: str) -> dict[str, Any]:
    if symbol in _REGIMES:
        return _REGIMES[symbol]
    rng = _rng(symbol + ":regime")
    return {
        "price0": 10 ** rng.uniform(-1.5, 2.5),
        "drift": rng.uniform(-0.001, 0.002),
        "vol": rng.uniform(0.022, 0.045),
        "volTrend": rng.uniform(-0.0006, 0.0022),
        "volAccel": rng.uniform(0.0, 0.6),
        "taker": rng.uniform(0.44, 0.57),
        "regime": "flat",
        "baseVol": 10 ** rng.uniform(6.0, 8.5),
        "spikes": [],
    }


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def seed_klines(symbol: str, limit: int = 120) -> list[dict]:
    symbol = symbol.upper().strip()
    p = _regime(symbol)
    rng = _rng(symbol + ":path")
    days = max(limit, 120)
    now = int(time.time() * 1000) // DAY_MS * DAY_MS

    price = p["price0"]
    base_vol = float(p["baseVol"])
    vol_accel = float(p.get("volAccel", 0.0))
    candles: list[dict] = []
    spikes = set(p.get("spikes") or [])

    for i in range(days):
        drift = p["drift"]
        shock = 0.0
        if i in spikes:
            shock = -0.16 if (i % 2 == 0) else 0.13   # clustered violent moves
        ret = drift + p["vol"] * rng.gauss(0, 1) + shock
        open_price = price
        close = max(price * math.exp(ret), price * 0.05)
        hi = max(open_price, close) * (1 + abs(rng.gauss(0, p["vol"] * 0.45)))
        lo = min(open_price, close) * (1 - abs(rng.gauss(0, p["vol"] * 0.45)))

        # Organic demand path: steady trend + quadratic acceleration in later days
        accel_term = vol_accel * (max(0, i - 60) ** 2) / 3600.0
        trend_mult = math.exp(p["volTrend"] * i + accel_term)
        vol_noise = math.exp(rng.gauss(0, 0.17))
        spike_vol = 3.2 if i in spikes else 1.0
        volume = base_vol * trend_mult * vol_noise * spike_vol

        taker = _clamp(p["taker"] + rng.gauss(0, 0.045) - (0.10 if i in spikes else 0.0), 0.30, 0.75)
        taker_base = volume * taker
        quote_volume = close * volume * (1.0 + rng.gauss(0, 0.02))
        taker_quote = taker_base * close * (1.0 + rng.gauss(0, 0.02))
        trades = volume * (1.0 + abs(rng.gauss(0, 0.18))) * 0.55

        candles.append(
            {
                "date": now - (days - 1 - i) * DAY_MS,
                "open": round(open_price, 8),
                "high": round(hi, 8),
                "low": round(lo, 8),
                "close": round(close, 8),
                "volume": round(volume, 6),
                "quoteVolume": round(quote_volume, 2),
                "trades": round(trades, 1),
                "takerBuyBase": round(taker_base, 6),
                "takerBuyQuote": round(taker_quote, 2),
            }
        )
        price = close

    return candles[-limit:]


def seed_universe(limit: int = 40) -> list[dict]:
    out = []
    for symbol in list(_REGIMES)[:limit]:
        candles = seed_klines(symbol, 3)
        last = candles[-1]
        prev = candles[-2] if len(candles) >= 2 else last
        r1 = last["close"] / prev["close"] - 1.0 if prev["close"] > 0 else 0.0
        out.append(
            {
                "symbol": symbol,
                "lastPrice": last["close"],
                "priceChangePercent": round(r1 * 100, 2),
                # Scale demo volumes to look like a top venue's 24h prints
                "quoteVolume": round(last["quoteVolume"] * 28, 2),
                "volume": last["volume"],
                "highPrice": last["high"],
                "lowPrice": last["low"],
                "volatility24h": (last["high"] - last["low"]) / last["close"] if last["close"] else 0.0,
            }
        )
    out.sort(key=lambda x: x["quoteVolume"], reverse=True)
    return out

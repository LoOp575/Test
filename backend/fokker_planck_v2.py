"""Fokker–Planck Model v2 (3-Pillar Anti-Spoofing Parameter Translator) + v1 vs v2 Comparator.

Architecture & strict isolation guarantee:
  - `backend/fokker_planck.py` (Model v1) is NEVER modified or mutated; v2 even keeps
    its own independent TTL and snapshot-cancellation caches.
  - Both v1 and v2 share the exact same implicit Chang–Cooper 1D PDE solver
    (`fokker_planck.solve_fokker_planck`) so every difference between v1 and v2 comes
    purely from the market-data -> (μ, D, x_eq) parameter translation layer:
      Pillar 1 — Real Trade-Flow Priority (Taker volume dominates μ >= 80%) +
                 Passive Iceberg Absorption detection when heavy flow fails to move price.
      Pillar 2 — Spoof-Filtered Stoikov Micro-Price P_micro = P_bid(1 - I) + P_ask·I,
                 weighting the thinner queue side and anchoring -κ(x - x_micro).
      Pillar 3 — Volatility-Adaptive Exponential Distance Decay w(d) = exp(-λ·d),
                 suppressing resting orders / stale distant price levels > 0.5%-1.0%
                 toward near-zero weight.
  - Provides apple-to-apple side-by-side comparison helpers (`compare_orderbook_v1_v2`,
    `compare_candles_v1_v2`, `compare_microstructure_snapshot_v1_v2`,
    `compare_backtest_v1_v2`) returning curve overlap ∫min(P_v1, P_v2)dx, Hellinger
    similarity, consensus classification, delta metrics, and merged overlay curves.
"""

from __future__ import annotations

import asyncio
import math
import time
from statistics import mean
from typing import Any, Optional

import httpx

from backend import fokker_planck as fp_v1
from backend import market_data

EPS = fp_v1.EPS
clamp = fp_v1.clamp
sat = fp_v1.sat
_finite_float = fp_v1._finite_float

# Independent caches so v2 NEVER touches v1 state
fp_v2_cache = market_data.TTLCache(ttl_seconds=120)
_PREV_BOOK_CACHE_V2: dict[str, dict[str, Any]] = {}
_PREV_BOOK_RAW_CACHE_FOR_CMP: dict[str, dict[str, Any]] = {}
_PREV_BOOK_TTL_SEC = 45.0
_PREV_BOOK_MAX = 120


MICRO_REGIME_META_V2: dict[str, dict[str, str]] = {
    "STABLE_BALANCED": {
        "label": "Stabil & Seimbang",
        "tone": "long",
        "summary": "Arus transaksi nyata (taker) dan likuiditas dekat mid-price seimbang; Micro-Price stabil di tengah spread.",
    },
    "DISTANT_SPOOF_FILTERED": {
        "label": "Tembok Jauh Dinetralkan",
        "tone": "long",
        "summary": "Tembok besar terdeteksi jauh (>0,5%) dari mid-price namun diredam hampir nol oleh e^(-λd); arus nyata tetap stabil.",
    },
    "SPOOF_SELL_WALL": {
        "label": "Indikasi Spoof Jual",
        "tone": "watch",
        "summary": "Tembok ask dekat disertai cancel tinggi tanpa eksekusi market-sell sepadan — tekanan turun transien.",
    },
    "SPOOF_BUY_WALL": {
        "label": "Indikasi Spoof Beli",
        "tone": "watch",
        "summary": "Tembok bid dekat disertai cancel tinggi tanpa eksekusi market-buy sepadan — dorongan naik rawan semu.",
    },
    "ICEBERG_BUY_ABSORPTION": {
        "label": "Absorpsi Beli (Iceberg)",
        "tone": "long",
        "summary": "Market-sell deras namun harga tertahan tidak turun — likuiditas beli pasif menyerap tekanan jual.",
    },
    "ICEBERG_SELL_ABSORPTION": {
        "label": "Absorpsi Jual (Iceberg)",
        "tone": "watch",
        "summary": "Market-buy deras namun harga tertahan tidak naik — distribusi pasif menyerap dorongan beli.",
    },
    "REAL_BUY_PRESSURE": {
        "label": "Tekanan Beli Riil",
        "tone": "long",
        "summary": "Transaksi nyata taker-buy mendominasi drift μ dan Micro-Price bergeser mendekati best ask.",
    },
    "REAL_SELL_PRESSURE": {
        "label": "Tekanan Jual Riil",
        "tone": "avoid",
        "summary": "Transaksi nyata taker-sell mendominasi drift μ dan Micro-Price tertekan mendekati best bid.",
    },
    "HIGH_VOL_WHIPSAW": {
        "label": "Whipsaw Volatilitas",
        "tone": "watch",
        "summary": "Difusi D(x,t) tinggi melebarkan kurva ke kedua ekor secara bersamaan (risiko sapuan dua arah).",
    },
}


CONSENSUS_META: dict[str, dict[str, str]] = {
    "SEPAKAT_KUAT": {
        "label": "Sepakat Kuat",
        "tone": "long",
    },
    "SEPAKAT_ARAH": {
        "label": "Sepakat Arah",
        "tone": "long",
    },
    "FILTER_V2_AKTIF": {
        "label": "Filter v2 Aktif",
        "tone": "watch",
    },
    "DIVERGENSI": {
        "label": "Divergensi v1/v2",
        "tone": "avoid",
    },
}


# ---------------------------------------------------------------------------
# 3-Pillar Building Blocks (Distance Decay + Spoof-Filtered Micro-Price)
# ---------------------------------------------------------------------------

def adaptive_decay_lambda(
    sigma_per_sec: float = 0.0007,
    horizon_sec: float = 30.0,
    override_lambda: Optional[float] = None,
) -> float:
    """Volatility-adaptive exponential decay rate λ for relative distance d = |p - p_mid| / p_mid.

    Default band λ ∈ [450, 800] attenuates resting orders beyond 0.5%-1.0% from mid-price
    to near-zero (e.g. at λ=550: w(0.5%)=6.4%, w(1.0%)=0.41%).
    """
    if override_lambda is not None:
        val = _finite_float(override_lambda, 0.0)
        if val > 0:
            return clamp(val, 100.0, 2000.0)
    sigma_h = max(1e-5, _finite_float(sigma_per_sec, 0.0007)) * math.sqrt(
        clamp(_finite_float(horizon_sec, 30.0), 1.0, 600.0)
    )
    char_dist = clamp(0.55 * sigma_h, 0.00125, 0.00222)
    return 1.0 / char_dist


def compute_micro_price(
    *,
    best_bid: float,
    best_ask: float,
    bid_qty: float,
    ask_qty: float,
    bid_cancel_rate: float = 0.0,
    ask_cancel_rate: float = 0.0,
) -> dict[str, float]:
    """Compute spoof-filtered Stoikov Micro-Price: P_micro = P_bid(1 - I) + P_ask·I."""
    p_bid = max(EPS, _finite_float(best_bid, 100.0))
    p_ask = max(p_bid, _finite_float(best_ask, p_bid))
    mid = 0.5 * (p_bid + p_ask)
    spread = max(0.0, p_ask - p_bid)
    spread_bps = (spread / mid) * 10_000.0 if mid > EPS else 0.0

    q_bid_raw = max(0.0, _finite_float(bid_qty, 0.0))
    q_ask_raw = max(0.0, _finite_float(ask_qty, 0.0))
    r_cb = clamp(_finite_float(bid_cancel_rate, 0.0), 0.0, 1.0)
    r_ca = clamp(_finite_float(ask_cancel_rate, 0.0), 0.0, 1.0)

    raw_sum = q_bid_raw + q_ask_raw
    imbalance_raw = (q_bid_raw / raw_sum) if raw_sum > EPS else 0.5

    q_bid_eff = q_bid_raw * (1.0 - 0.65 * r_cb)
    q_ask_eff = q_ask_raw * (1.0 - 0.65 * r_ca)
    eff_sum = q_bid_eff + q_ask_eff
    imbalance_eff = (q_bid_eff / eff_sum) if eff_sum > EPS else 0.5

    micro_px = p_bid * (1.0 - imbalance_eff) + p_ask * imbalance_eff
    micro_raw_px = p_bid * (1.0 - imbalance_raw) + p_ask * imbalance_raw
    micro_log_shift = math.log(max(micro_px, EPS) / max(mid, EPS))

    return {
        "bestBid": p_bid,
        "bestAsk": p_ask,
        "midPrice": mid,
        "microPrice": micro_px,
        "rawMicroPrice": micro_raw_px,
        "spreadBps": spread_bps,
        "microImbalance": imbalance_eff,
        "rawMicroImbalance": imbalance_raw,
        "microShiftLog": micro_log_shift,
        "microShiftBps": micro_log_shift * 10_000.0,
    }


def compute_weighted_book(
    bids: Any,
    asks: Any,
    *,
    sigma_per_sec: float = 0.0007,
    horizon_sec: float = 30.0,
    decay_lambda: Optional[float] = None,
    far_threshold: float = 0.005,
    max_levels: int = 100,
) -> dict[str, Any]:
    """Parse L2 bids/asks with exponential distance decay w(d) = exp(-λ·d)."""

    def _clean(raw_side: Any, reverse: bool) -> list[tuple[float, float]]:
        parsed: list[tuple[float, float]] = []
        if not isinstance(raw_side, list):
            return parsed
        for entry in raw_side[:max_levels]:
            if not isinstance(entry, (list, tuple)) or len(entry) < 2:
                continue
            px = _finite_float(entry[0], 0.0)
            qty = max(0.0, _finite_float(entry[1], 0.0))
            if px > 0 and qty >= 0:
                parsed.append((px, qty))
        parsed.sort(key=lambda pair: pair[0], reverse=reverse)
        return parsed

    bid_levels = _clean(bids, reverse=True)
    ask_levels = _clean(asks, reverse=False)
    if not bid_levels or not ask_levels:
        return {"ok": False}

    best_bid, best_bid_qty = bid_levels[0]
    best_ask, best_ask_qty = ask_levels[0]
    if best_bid <= 0 or best_ask <= 0 or best_ask < best_bid:
        return {"ok": False}

    mid = 0.5 * (best_bid + best_ask)
    lam = adaptive_decay_lambda(sigma_per_sec, horizon_sec, decay_lambda)

    def _accum(
        levels: list[tuple[float, float]]
    ) -> tuple[float, float, float, float, dict[str, float], dict[str, float]]:
        raw_total = 0.0
        weighted_total = 0.0
        near_total = 0.0
        far_total = 0.0
        w_map: dict[str, float] = {}
        raw_top20_map: dict[str, float] = {}
        for idx, (px, qty) in enumerate(levels):
            dist = abs(px - mid) / mid
            weight = math.exp(-min(60.0, lam * dist))
            w_qty = qty * weight
            raw_total += qty
            weighted_total += w_qty
            if dist >= far_threshold:
                far_total += qty
            else:
                near_total += qty
            key = f"{px:.8f}"
            w_map[key] = w_qty
            if idx < 20:
                raw_top20_map[key] = qty
        return raw_total, weighted_total, near_total, far_total, w_map, raw_top20_map

    raw_b, w_b, near_b, far_b, map_b, top20_b = _accum(bid_levels)
    raw_a, w_a, near_a, far_a, map_a, top20_a = _accum(ask_levels)
    max_span = max(abs(bid_levels[-1][0] - mid) / mid, abs(ask_levels[-1][0] - mid) / mid)

    return {
        "ok": True,
        "bestBid": best_bid,
        "bestAsk": best_ask,
        "bestBidQty": best_bid_qty,
        "bestAskQty": best_ask_qty,
        "midPrice": mid,
        "spreadBps": ((best_ask - best_bid) / mid) * 10_000.0,
        "decayLambda": lam,
        "weightAt05Pct": math.exp(-lam * 0.005),
        "weightAt10Pct": math.exp(-lam * 0.010),
        "rawBidDepth": raw_b,
        "rawAskDepth": raw_a,
        "top20BidDepth": sum(top20_b.values()),
        "top20AskDepth": sum(top20_a.values()),
        "weightedBidDepth": w_b,
        "weightedAskDepth": w_a,
        "nearBidDepth": near_b,
        "nearAskDepth": near_a,
        "farBidDepth": far_b,
        "farAskDepth": far_a,
        "weightedBidMap": map_b,
        "weightedAskMap": map_a,
        "rawTop20BidMap": top20_b,
        "rawTop20AskMap": top20_a,
        "levelsUsed": len(bid_levels) + len(ask_levels),
        "maxDepthSpanPct": max_span * 100.0,
    }


# ---------------------------------------------------------------------------
# Mode 1 (v2) — OHLCV Candle Analyzer with 3-Pillar Parameter Translation
# ---------------------------------------------------------------------------

def analyze_candles_fp_v2(
    candles: list[dict[str, Any]],
    *,
    horizon_days: float = 7.0,
    stable_band: float = 0.05,
    window: int = fp_v1.DEFAULT_WINDOW,
    include_curve: bool = False,
    n_grid: int = 201,
) -> dict[str, Any]:
    """Model v2 OHLCV translator using the exact same Chang–Cooper solver as v1.

    Translation upgrades over v1:
      1. Taker-Flow Priority + Candle Absorption Guard: weights recent executed
         taker-buy imbalance exponentially and discounts drift when heavy volume
         + one-sided flow fails to displace close relative to intraday range.
      2. Candle Micro-Price + Distance-Decayed Equilibrium Anchor: weights each
         bar's typical price by w(d_i) = exp(-λ_c * |p_i - p_now|/p_now) so stale
         far-away price zones lose pull, blended with latest bar micro-price.
      3. Exponentially-decayed Parkinson + EWMA diffusion so volatility adapts
         cleanly across regime transitions without over-lagging old spikes.
    """
    rows = fp_v1._sanitize_candles(candles)
    min_needed = max(fp_v1.MIN_CANDLES, 2 * window + 1)
    if len(rows) < min_needed:
        return {
            "ok": False,
            "modelVersion": "v2",
            "reason": f"Histori kurang: {len(rows)} candle valid (minimal {min_needed}).",
        }

    horizon_days = clamp(_finite_float(horizon_days, 7.0), 0.5, 60.0)
    stable_band = clamp(_finite_float(stable_band, 0.05), 0.005, 0.35)

    closes = [c["close"] for c in rows]
    price_now = closes[-1]
    log_rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    hist = log_rets[-min(len(log_rets), 3 * window) :]

    # 1. Diffusion D0: EWMA return variance + exponentially-decayed Parkinson range variance
    ewma_var = hist[0] ** 2
    for r in hist[1:]:
        ewma_var = 0.93 * ewma_var + 0.07 * (r * r)

    park_k = 1.0 / (4.0 * math.log(2.0))
    cur = rows[-window:]
    prev = rows[-2 * window : -window]
    p_num = 0.0
    p_den = 0.0
    for age, c in enumerate(reversed(cur)):
        w_t = math.exp(-0.08 * age)  # half-life ~ 8.7 days
        pv = park_k * (math.log(max(c["high"], EPS) / max(c["low"], EPS)) ** 2)
        p_num += w_t * pv
        p_den += w_t
    park_var = p_num / max(p_den, EPS)
    d0 = max(0.65 * ewma_var + 0.35 * park_var, 0.85 * ewma_var, 1e-8)
    sigma_d = math.sqrt(d0)

    # 2. Fat-tail kurtosis & downside volatility asymmetry
    mu_h = mean(hist)
    var_h = max(mean((r - mu_h) ** 2 for r in hist), 1e-10)
    std_h = math.sqrt(var_h)
    sample_kurt = mean(((r - mu_h) / std_h) ** 4 for r in hist)
    excess_kurt = max(0.0, sample_kurt - 3.0)
    tail_eta = clamp(0.075 * excess_kurt, 0.05, 0.55)

    neg_sq = [r * r for r in hist if r < 0]
    pos_sq = [r * r for r in hist if r > 0]
    down_asym = (
        clamp((mean(neg_sq) / max(mean(pos_sq), EPS)) - 1.0, 0.0, 0.65)
        if (neg_sq and pos_sq)
        else 0.0
    )

    # 3. Pillar 1 + Pillar 3: Exponentially distance- & time-decayed real taker flow
    #    plus passive absorption detection when heavy taker flow fails to move price.
    lam_price = clamp(1.0 / max(1.8 * sigma_d * math.sqrt(horizon_days), 0.025), 5.0, 40.0)
    w_tb = 0.0
    w_vol = 0.0
    abs_num = 0.0
    abs_den = 0.0
    for age, c in enumerate(reversed(cur)):
        vol = c["volume"]
        if vol <= EPS:
            continue
        typ = (c["high"] + c["low"] + c["close"]) / 3.0
        d_px = abs(typ - price_now) / max(price_now, EPS)
        w = math.exp(-0.07 * age) * math.exp(-min(30.0, 0.5 * lam_price * d_px))
        w_tb += w * c["takerBuyBase"]
        w_vol += w * vol

        bar_tfi = 2.0 * (c["takerBuyBase"] / vol) - 1.0
        bar_ret = math.log(max(c["close"], EPS) / max(c["open"], EPS))
        bar_rng = max(math.log(max(c["high"], EPS) / max(c["low"], EPS)), sigma_d * 0.5, EPS)
        if abs(bar_tfi) > 0.08:
            flow_dir = 1.0 if bar_tfi > 0 else -1.0
            eff = clamp((flow_dir * bar_ret) / max(abs(bar_tfi) * bar_rng, EPS), 0.0, 1.0)
            abs_num += w * abs(bar_tfi) * (1.0 - eff)
            abs_den += w * abs(bar_tfi)

    has_taker = w_vol > EPS and any(c["takerBuyBase"] > 0 for c in cur)
    buy_share = (w_tb / w_vol) if has_taker else 0.5
    tfi_weighted = 2.0 * buy_share - 1.0
    absorption_score = clamp((abs_num / abs_den) if abs_den > EPS else 0.0, 0.0, 1.0)
    # Only penalize when net flow is genuinely one-sided yet stalled
    eff_absorption = absorption_score * clamp((abs(tfi_weighted) - 0.04) / 0.16, 0.0, 1.0)

    cur_tr = sum(c["trades"] for c in cur)
    prev_tr = sum(c["trades"] for c in prev)
    cur_vol = sum(c["volume"] for c in cur)
    prev_vol = sum(c["volume"] for c in prev)
    cur_qv = sum(c["quoteVolume"] for c in cur)
    prev_qv = sum(c["quoteVolume"] for c in prev)

    part_growth = (cur_tr - prev_tr) / prev_tr if prev_tr > EPS else 0.0
    unit_growth = (cur_vol - prev_vol) / prev_vol if prev_vol > EPS else 0.0
    qv_growth = (cur_qv - prev_qv) / prev_qv if prev_qv > EPS else 0.0
    part_weight = clamp(1.0 + 0.55 * sat(part_growth), 0.40, 1.25)
    distortion_flag = qv_growth > 0.05 and unit_growth <= 0.0
    distortion_discount = 0.55 if distortion_flag else 1.0

    # Taker flow carries ~92% of drift capacity (vs passive volume growth 8%)
    raw_signal = (
        0.95 * tfi_weighted * (1.0 - 0.55 * eff_absorption) * part_weight
        + 0.08 * sat(unit_growth)
    ) * distortion_discount
    mu_flow = clamp(raw_signal, -0.16, 0.16) * sigma_d

    # 4. Pillar 2 + Pillar 3: Distance-decayed VWAP + Intraday Micro-Price anchor
    vwap_seg = rows[-min(20, len(rows)) :]
    dec_num = 0.0
    dec_den = 0.0
    for age, c in enumerate(reversed(vwap_seg)):
        typ = (c["high"] + c["low"] + c["close"]) / 3.0
        d_px = abs(typ - price_now) / max(price_now, EPS)
        w = max(c["volume"], EPS) * math.exp(-min(40.0, lam_price * d_px)) * math.exp(-0.05 * age)
        dec_num += typ * w
        dec_den += w
    decayed_vwap = (dec_num / dec_den) if dec_den > EPS else price_now

    # Range Micro-Price on recent 3 bars: thinner overhead wick => imbalance toward high
    rec3 = rows[-3:]
    hi3 = max(c["high"] for c in rec3)
    lo3 = min(c["low"] for c in rec3)
    tb3 = sum(c["takerBuyBase"] for c in rec3)
    vol3 = sum(c["volume"] for c in rec3)
    imb_flow3 = (tb3 / vol3) if vol3 > EPS else 0.5
    imb_pos3 = clamp((price_now - lo3) / max(hi3 - lo3, EPS), 0.0, 1.0)
    micro_imb = clamp(0.60 * imb_flow3 + 0.40 * imb_pos3, 0.05, 0.95)
    candle_micro_px = lo3 * (1.0 - micro_imb) + hi3 * micro_imb

    anchor_px = 0.70 * decayed_vwap + 0.30 * candle_micro_px
    x_micro_anchor = clamp(math.log(max(anchor_px, EPS) / price_now), -0.15, 0.15)
    kappa = 0.035 + 0.025 * eff_absorption

    scale_x = max(sigma_d * math.sqrt(horizon_days), stable_band)
    drift_span = abs(mu_flow) * horizon_days + abs(x_micro_anchor)
    domain = max(6.5 * scale_x + drift_span, 4.2 * stable_band)
    n_steps = max(24, min(64, int(round(horizon_days * 4))))

    sol = fp_v1.solve_fokker_planck(
        lambda x, _t: mu_flow - kappa * (x - x_micro_anchor),
        lambda x, _t: d0
        * (1.0 + down_asym * max(0.0, -x / scale_x) + tail_eta * min((x / scale_x) ** 2, 16.0)),
        x_min=-domain,
        x_max=domain,
        stable_band=stable_band,
        horizon=horizon_days,
        n_grid=n_grid,
        n_steps=n_steps,
        snapshot_count=6,
    )

    f = sol.final
    regime = fp_v1._classify_candle_regime(f, mu_flow)
    meta = fp_v1.CANDLE_REGIME_META[regime]
    band_pct = stable_band * 100.0

    notes = [
        f"[v2] Horizon {horizon_days:g}h (±{band_pct:g}%): P(stabil)={f.p_stable*100:.1f}%, "
        f"P(down)={f.p_downside*100:.1f}%, P(up)={f.p_upside*100:.1f}%.",
        f"[v2] Prioritas Taker terbobot eksponensial: buy-share {buy_share*100:.1f}% (TFI {tfi_weighted:+.3f}), "
        f"skor absorpsi pasif {eff_absorption*100:.0f}% → μ={mu_flow*100:+.2f}%/hari.",
        f"[v2] Jangkar Micro-Price + VWAP teredam e^(-λd) (λ_c={lam_price:.1f}): "
        f"${anchor_px:,.4g} ({x_micro_anchor*100:+.2f}% dari harga kini), σ={sigma_d*100:.2f}%/hari.",
    ]

    payload: dict[str, Any] = {
        "ok": True,
        "modelVersion": "v2",
        "price": price_now,
        "vwap20": decayed_vwap,
        "microAnchorPrice": anchor_px,
        "candlesUsed": len(rows),
        "horizonDays": horizon_days,
        "stableBand": stable_band,
        "regime": regime,
        "regimeLabel": meta["label"],
        "regimeTone": meta["tone"],
        "regimeRank": meta["rank"],
        "regimeSummary": meta["summary"],
        "metrics": f.to_dict(),
        "parameters": {
            "muFlowDaily": round(mu_flow, 6),
            "muFlowDailyPct": round(mu_flow * 100.0, 4),
            "d0Daily": round(d0, 8),
            "sigmaDaily": round(sigma_d, 6),
            "sigmaDailyPct": round(sigma_d * 100.0, 4),
            "ewmaVolPct": round(math.sqrt(ewma_var) * 100.0, 4),
            "parkinsonVolPct": round(math.sqrt(park_var) * 100.0, 4),
            "kappaMeanRev": round(kappa, 4),
            "vwapAnchorLog": round(x_micro_anchor, 6),
            "vwapAnchorPct": round(x_micro_anchor * 100.0, 3),
            "decayLambdaCandle": round(lam_price, 2),
            "microImbalance3d": round(micro_imb, 4),
            "absorptionScore": round(eff_absorption, 4),
            "tailEta": round(tail_eta, 4),
            "downsideAsym": round(down_asym, 4),
            "sampleKurtosis": round(sample_kurt, 3),
            "buyShare30d": round(buy_share, 4),
            "buyImbalance30d": round(tfi_weighted, 4),
            "unitGrowth30d": round(unit_growth, 4),
            "participationGrowth30d": round(part_growth, 4),
            "participationWeight": round(part_weight, 4),
            "distortionDiscounted": distortion_flag,
        },
        "priceBands": {
            "crashPrice": round(price_now * math.exp(-2.0 * stable_band), 8),
            "lowerStable": round(price_now * math.exp(-stable_band), 8),
            "currentPrice": round(price_now, 8),
            "microAnchorPrice": round(anchor_px, 8),
            "expectedPrice": round(price_now * math.exp(f.mean_log_return), 8),
            "upperStable": round(price_now * math.exp(stable_band), 8),
            "pumpPrice": round(price_now * math.exp(2.0 * stable_band), 8),
        },
        "notes": notes,
    }
    if include_curve:
        payload["curve"] = fp_v1.export_chart_curve(sol)
        payload["timeline"] = [m.to_dict() for m in sol.timeline]
    return payload


# ---------------------------------------------------------------------------
# Mode 2 (v2) — Order-Book & Spoofing Analyzer (3-Pillar Anti-Spoofing)
# ---------------------------------------------------------------------------

def analyze_orderbook_fp_v2(
    *,
    bid_depth: float,
    ask_depth: float,
    taker_buy_vol: float,
    taker_sell_vol: float,
    canceled_bid_vol: float = 0.0,
    canceled_ask_vol: float = 0.0,
    far_bid_depth: float = 0.0,
    far_ask_depth: float = 0.0,
    wall_distance_pct: float = 0.008,
    decay_lambda: Optional[float] = None,
    spread_bps: float = 8.0,
    price_change_bps: Optional[float] = None,
    trade_window_sec: float = 15.0,
    best_bid: Optional[float] = None,
    best_ask: Optional[float] = None,
    best_bid_qty: Optional[float] = None,
    best_ask_qty: Optional[float] = None,
    sigma_per_sec: float = 0.0007,
    horizon_sec: float = 30.0,
    stable_band: float = 0.004,
    tau_spoof_sec: float = 6.0,
    mid_price: Optional[float] = None,
) -> dict[str, Any]:
    """Evolve short-horizon Fokker–Planck PDF using Model v2 3-pillar translation."""
    near_bid = max(0.0, _finite_float(bid_depth, 0.0))
    near_ask = max(0.0, _finite_float(ask_depth, 0.0))
    far_bid = max(0.0, _finite_float(far_bid_depth, 0.0))
    far_ask = max(0.0, _finite_float(far_ask_depth, 0.0))
    tb = max(0.0, _finite_float(taker_buy_vol, 0.0))
    ts = max(0.0, _finite_float(taker_sell_vol, 0.0))
    cb = max(0.0, _finite_float(canceled_bid_vol, 0.0))
    ca = max(0.0, _finite_float(canceled_ask_vol, 0.0))

    sigma_s = clamp(_finite_float(sigma_per_sec, 0.0007), 1e-5, 0.05)
    horizon_s = clamp(_finite_float(horizon_sec, 30.0), 1.0, 600.0)
    band = clamp(_finite_float(stable_band, 0.004), 0.0005, 0.15)
    tau = clamp(_finite_float(tau_spoof_sec, 6.0), 1.0, 120.0)
    wall_dist = clamp(_finite_float(wall_distance_pct, 0.008), 0.003, 0.05)

    # Pillar 3: Exponential Distance Decay w(d) = exp(-λ·d)
    lam = adaptive_decay_lambda(sigma_s, horizon_s, decay_lambda)
    w_05pct = math.exp(-lam * 0.005)
    w_10pct = math.exp(-lam * 0.010)
    w_far = math.exp(-lam * wall_dist)

    bid_d = near_bid + far_bid * w_far
    ask_d = near_ask + far_ask * w_far
    raw_bid_total = near_bid + far_bid
    raw_ask_total = near_ask + far_ask

    book_sum = bid_d + ask_d
    raw_book_sum = raw_bid_total + raw_ask_total
    obi = (bid_d - ask_d) / book_sum if book_sum > EPS else 0.0
    obi_raw = (raw_bid_total - raw_ask_total) / raw_book_sum if raw_book_sum > EPS else obi

    far_total = far_bid + far_ask
    far_wall_share = far_total / raw_book_sum if raw_book_sum > EPS else 0.0
    distant_wall_gap = obi - obi_raw
    distant_wall_detected = far_wall_share >= 0.35 and abs(distant_wall_gap) >= 0.25
    distant_wall_side = (
        "ASK" if (distant_wall_detected and far_ask > far_bid) else (
            "BID" if distant_wall_detected else "NONE"
        )
    )

    # Near-touch cancel spoof diagnostics
    trade_sum = tb + ts
    tfi = (tb - ts) / trade_sum if trade_sum > EPS else 0.0
    ask_cancel_rate = ca / (ask_d + ca) if (ask_d + ca) > EPS else 0.0
    bid_cancel_rate = cb / (bid_d + cb) if (bid_d + cb) > EPS else 0.0

    eff_bid = bid_d + cb
    eff_ask = ask_d + ca
    obi_eff = (eff_bid - eff_ask) / (eff_bid + eff_ask) if (eff_bid + eff_ask) > EPS else obi

    ask_spoof = clamp(max(0.0, tfi - min(obi, obi_eff)) * ask_cancel_rate, 0.0, 1.0)
    bid_spoof = clamp(max(0.0, max(obi, obi_eff) - tfi) * bid_cancel_rate, 0.0, 1.0)
    spoof_index = max(ask_spoof, bid_spoof)
    net_spoof = bid_spoof - ask_spoof

    # Pillar 2: Spoof-Filtered Stoikov Micro-Price
    ref_mid = mid_price if (mid_price is not None and _finite_float(mid_price, 0.0) > 0) else 100.0
    if best_bid is not None and best_ask is not None and best_bid > 0 and best_ask >= best_bid:
        p_bid_use = float(best_bid)
        p_ask_use = float(best_ask)
        ref_mid = 0.5 * (p_bid_use + p_ask_use)
    else:
        half_sp = 0.5 * clamp(_finite_float(spread_bps, 8.0), 0.1, 500.0) * 1e-4
        p_bid_use = ref_mid * (1.0 - half_sp)
        p_ask_use = ref_mid * (1.0 + half_sp)

    if (
        best_bid_qty is not None
        and best_ask_qty is not None
        and (best_bid_qty + best_ask_qty) > EPS
        and book_sum > EPS
    ):
        bbo_sum = max(EPS, float(best_bid_qty) + float(best_ask_qty))
        bbo_bid_share = clamp(float(best_bid_qty) / bbo_sum, 0.0, 1.0)
        dec_bid_share = clamp(bid_d / book_sum, 0.0, 1.0)
        blend_bid_share = 0.60 * bbo_bid_share + 0.40 * dec_bid_share
        mp_bid_q = blend_bid_share * book_sum
        mp_ask_q = (1.0 - blend_bid_share) * book_sum
    else:
        mp_bid_q = bid_d
        mp_ask_q = ask_d

    mp = compute_micro_price(
        best_bid=p_bid_use,
        best_ask=p_ask_use,
        bid_qty=mp_bid_q,
        ask_qty=mp_ask_q,
        bid_cancel_rate=bid_cancel_rate,
        ask_cancel_rate=ask_cancel_rate,
    )
    x_micro = clamp(mp["microShiftLog"] * (1.0 - 0.60 * spoof_index), -0.5 * band, 0.5 * band)

    # Pillar 1: Real Taker-Flow Priority + Passive Iceberg Absorption Guard
    absorption_index = 0.0
    absorption_side = "NONE"
    obs_change_bps: Optional[float] = None
    if price_change_bps is not None and math.isfinite(_finite_float(price_change_bps, math.nan)):
        obs_change_bps = clamp(float(price_change_bps), -5000.0, 5000.0)
        if abs(tfi) > 0.28 and trade_sum > EPS:
            dx_obs = obs_change_bps * 1e-4
            win_s = clamp(_finite_float(trade_window_sec, 15.0), 1.0, 300.0)
            expected_scale = max(0.80 * sigma_s * math.sqrt(win_s), mp["spreadBps"] * 1e-4, 0.0004)
            flow_dir = 1.0 if tfi > 0 else -1.0
            impact_efficiency = clamp(
                (flow_dir * dx_obs) / max(abs(tfi) * expected_scale, EPS), 0.0, 1.0
            )
            flow_strength = clamp((abs(tfi) - 0.28) / 0.48, 0.0, 1.0)
            book_support = 1.0 if (flow_dir * obi) <= 0.15 else 0.65
            absorption_index = clamp(flow_strength * (1.0 - impact_efficiency) * book_support, 0.0, 1.0)
            if absorption_index >= 0.35:
                absorption_side = "BUY" if tfi < 0 else "SELL"

    alpha_tfi = 0.36 * (1.0 - 0.70 * absorption_index)
    genuine_book_weight = 1.0 - 0.75 * spoof_index
    beta_obi = 0.09 * genuine_book_weight
    taker_priority_share = alpha_tfi / max(alpha_tfi + beta_obi, EPS)

    mu_real = (alpha_tfi * tfi + beta_obi * obi) * sigma_s
    mu_spoof_init = 0.50 * net_spoof * sigma_s
    kappa = (0.018 + 0.075 * spoof_index + 0.065 * absorption_index) * (
        1.0 - 0.45 * abs(tfi) * (1.0 - absorption_index)
    )

    d0 = sigma_s * sigma_s
    down_asym = 0.45 * max(0.0, -tfi) * (1.0 - 0.60 * absorption_index)
    tail_eta = 0.20 + 0.35 * spoof_index

    spread = max(sigma_s * math.sqrt(horizon_s), band)
    drift_reach = (abs(mu_real) + abs(mu_spoof_init)) * horizon_s + abs(x_micro)
    domain = max(6.5 * spread + drift_reach, 5.0 * band)
    n_steps = max(24, min(60, int(round(horizon_s))))

    sol = fp_v1.solve_fokker_planck(
        lambda x, t: mu_real + mu_spoof_init * math.exp(-t / tau) - kappa * (x - x_micro),
        lambda x, _t: d0
        * (1.0 + down_asym * max(0.0, -x / band) + tail_eta * min((x / band) ** 2, 25.0)),
        x_min=-domain,
        x_max=domain,
        stable_band=band,
        horizon=horizon_s,
        n_grid=201,
        n_steps=n_steps,
        snapshot_count=6,
    )

    f = sol.final
    if absorption_index >= 0.42:
        regime = "ICEBERG_BUY_ABSORPTION" if absorption_side == "BUY" else "ICEBERG_SELL_ABSORPTION"
    elif spoof_index >= 0.28 and abs(tfi) < 0.28:
        regime = "SPOOF_SELL_WALL" if net_spoof < 0 else "SPOOF_BUY_WALL"
    elif f.p_downside >= 0.42 and tfi <= -0.22:
        regime = "REAL_SELL_PRESSURE"
    elif f.p_upside >= 0.42 and tfi >= 0.22:
        regime = "REAL_BUY_PRESSURE"
    elif f.p_stable < 0.50:
        regime = "HIGH_VOL_WHIPSAW"
    elif distant_wall_detected and abs(tfi) < 0.25:
        regime = "DISTANT_SPOOF_FILTERED"
    else:
        regime = "STABLE_BALANCED"

    meta = MICRO_REGIME_META_V2[regime]
    notes: list[str] = [
        f"Prioritas Taker: TFI {tfi:+.2f} memegang {taker_priority_share*100:.0f}% bobot drift μ vs OBI efektif {obi:+.2f}.",
        f"Stoikov Micro-Price: bobot I={mp['microImbalance']*100:.1f}% ({mp['microShiftBps']:+.2f} bps vs mid, spread {mp['spreadBps']:.1f} bps).",
        f"Exponential Decay (λ={lam:.0f}): bobot jarak 0,5% = {w_05pct*100:.1f}%, jarak 1,0% = {w_10pct*100:.2f}%.",
    ]
    if distant_wall_detected:
        notes.append(
            f"Tembok {distant_wall_side} jauh ({far_wall_share*100:.0f}% buku di ~{wall_dist*100:.2f}%) diredam: OBI mentah {obi_raw:+.2f} → efektif {obi:+.2f}."
        )
    if regime in {"ICEBERG_BUY_ABSORPTION", "ICEBERG_SELL_ABSORPTION"}:
        notes.append(
            f"Absorpsi Iceberg {absorption_side} ({absorption_index*100:.0f}%): TFI {tfi:+.2f} tapi Δp hanya {obs_change_bps:+.1f} bps."
        )

    has_real_price = (mid_price is not None and _finite_float(mid_price, 0.0) > 0) or (
        best_bid is not None and best_ask is not None
    )
    out: dict[str, Any] = {
        "ok": True,
        "modelVersion": "v2",
        "mode": "orderbook",
        "horizonSec": horizon_s,
        "stableBand": band,
        "regime": regime,
        "regimeLabel": meta["label"],
        "regimeTone": meta["tone"],
        "regimeSummary": meta["summary"],
        "metrics": f.to_dict(),
        "microstructure": {
            "tfi": round(tfi, 4),
            "takerPriorityShare": round(taker_priority_share, 4),
            "absorptionIndex": round(absorption_index, 4),
            "absorptionSide": absorption_side,
            "priceChangeBps": round(obs_change_bps, 2) if obs_change_bps is not None else None,
            "bestBid": round(mp["bestBid"], 8),
            "bestAsk": round(mp["bestAsk"], 8),
            "midPrice": round(mp["midPrice"], 8),
            "microPrice": round(mp["microPrice"], 8),
            "rawMicroPrice": round(mp["rawMicroPrice"], 8),
            "spreadBps": round(mp["spreadBps"], 3),
            "microImbalance": round(mp["microImbalance"], 4),
            "rawMicroImbalance": round(mp["rawMicroImbalance"], 4),
            "microShiftBps": round(mp["microShiftBps"], 3),
            "anchorShiftBps": round(x_micro * 10_000.0, 3),
            "obi": round(obi, 4),
            "obiRaw": round(obi_raw, 4),
            "weightedBidDepth": round(bid_d, 4),
            "weightedAskDepth": round(ask_d, 4),
            "farBidDepth": round(far_bid, 4),
            "farAskDepth": round(far_ask, 4),
            "farWallShare": round(far_wall_share, 4),
            "distantWallGap": round(distant_wall_gap, 4),
            "distantWallDetected": distant_wall_detected,
            "distantWallSide": distant_wall_side,
            "decayLambda": round(lam, 1),
            "weightAt05Pct": round(w_05pct, 4),
            "weightAt10Pct": round(w_10pct, 5),
            "weightAtWallDist": round(w_far, 5),
            "wallDistancePct": round(wall_dist * 100.0, 2),
            "bidCancelRate": round(bid_cancel_rate, 4),
            "askCancelRate": round(ask_cancel_rate, 4),
            "askSpoof": round(ask_spoof, 4),
            "bidSpoof": round(bid_spoof, 4),
            "spoofIndex": round(spoof_index, 4),
            "netSpoof": round(net_spoof, 4),
            "muRealBpsPerSec": round(mu_real * 10_000.0, 4),
            "muSpoofInitBpsPerSec": round(mu_spoof_init * 10_000.0, 4),
            "sigmaBpsPerSec": round(sigma_s * 10_000.0, 3),
            "d0": round(d0, 10),
            "kappa": round(kappa, 5),
            "tauSpoofSec": tau,
            "tailEta": round(tail_eta, 4),
            "downsideAsym": round(down_asym, 4),
        },
        "curve": fp_v1.export_chart_curve(sol),
        "timeline": [m.to_dict() for m in sol.timeline],
        "notes": notes,
    }
    if has_real_price and ref_mid > 0:
        out["midPrice"] = round(ref_mid, 8)
        out["microPrice"] = round(mp["microPrice"], 8)
        out["priceBands"] = {
            "crashPrice": round(ref_mid * math.exp(-2.0 * band), 8),
            "lowerStable": round(ref_mid * math.exp(-band), 8),
            "currentPrice": round(ref_mid, 8),
            "microPrice": round(mp["microPrice"], 8),
            "upperStable": round(ref_mid * math.exp(band), 8),
            "pumpPrice": round(ref_mid * math.exp(2.0 * band), 8),
        }
    return out


# ---------------------------------------------------------------------------
# Quantitative Similarity & Consensus Engine (v1 vs v2 Side-by-Side)
# ---------------------------------------------------------------------------

def _interp_density(xs: list[float], ys: list[float], x: float) -> float:
    """Linear interpolation of exported PDF with zero outside [xs[0], xs[-1]]."""
    if not xs or x <= xs[0] or x >= xs[-1]:
        return 0.0
    lo, hi = 0, len(xs) - 1
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if xs[mid] <= x:
            lo = mid
        else:
            hi = mid
    span = xs[hi] - xs[lo]
    if span <= EPS:
        return ys[lo]
    t = (x - xs[lo]) / span
    return max(0.0, ys[lo] * (1.0 - t) + ys[hi] * t)


def build_overlay_and_similarity(
    curve_v1: Optional[list[dict[str, Any]]],
    curve_v2: Optional[list[dict[str, Any]]],
    m1: dict[str, Any],
    m2: dict[str, Any],
    *,
    include_overlay: bool = True,
    n_points: int = 121,
) -> tuple[dict[str, float], list[dict[str, Any]]]:
    """Compute exact PDF overlap ∫min(P1, P2)dx, Bhattacharyya affinity, and overlay curve."""
    # 3-zone L1 similarity: 1 - 0.5 * sum |P_v1(z) - P_v2(z)|
    tv_zones = 0.5 * (
        abs(float(m1["pDownside"]) - float(m2["pDownside"]))
        + abs(float(m1["pStable"]) - float(m2["pStable"]))
        + abs(float(m1["pUpside"]) - float(m2["pUpside"]))
    )
    zone_similarity = clamp(1.0 - tv_zones, 0.0, 1.0)

    overlay: list[dict[str, Any]] = []
    if curve_v1 and curve_v2 and len(curve_v1) >= 5 and len(curve_v2) >= 5:
        x1 = [float(pt["x"]) for pt in curve_v1]
        y1 = [max(0.0, float(pt["density"])) for pt in curve_v1]
        x2 = [float(pt["x"]) for pt in curve_v2]
        y2 = [max(0.0, float(pt["density"])) for pt in curve_v2]
        lo = min(x1[0], x2[0])
        hi = max(x1[-1], x2[-1])
        n_int = 241
        dx = (hi - lo) / (n_int - 1)
        overlap_sum = 0.0
        bhatt_sum = 0.0
        for i in range(n_int):
            x = lo + i * dx
            d1 = _interp_density(x1, y1, x)
            d2 = _interp_density(x2, y2, x)
            w = 0.5 if (i == 0 or i == n_int - 1) else 1.0
            overlap_sum += w * min(d1, d2)
            bhatt_sum += w * math.sqrt(d1 * d2)
        pdf_overlap = clamp(overlap_sum * dx, 0.0, 1.0)
        bhattacharyya = clamp(bhatt_sum * dx, 0.0, 1.0)
        hellinger_sim = clamp(1.0 - math.sqrt(max(0.0, 1.0 - bhattacharyya)), 0.0, 1.0)

        if include_overlay:
            dx_o = (hi - lo) / (n_points - 1)
            for i in range(n_points):
                x = lo + i * dx_o
                p1 = _interp_density(x1, y1, x) * 0.01  # probability per 1% log-return
                p2 = _interp_density(x2, y2, x) * 0.01
                overlay.append(
                    {
                        "x": round(x, 6),
                        "xPct": round(x * 100.0, 4),
                        "xBps": round(x * 10_000.0, 2),
                        "v1": round(p1, 6),
                        "v2": round(p2, 6),
                        "common": round(min(p1, p2), 6),
                    }
                )
    else:
        # Fast analytical Gaussian overlap approximation when curves are omitted (universe scan)
        mu1, s1 = float(m1["meanLogReturn"]), max(float(m1["stdLogReturn"]), 1e-6)
        mu2, s2 = float(m2["meanLogReturn"]), max(float(m2["stdLogReturn"]), 1e-6)
        bc_term = 0.25 * math.log(0.25 * ((s1 * s1) / (s2 * s2) + (s2 * s2) / (s1 * s1) + 2.0)) + (
            ((mu1 - mu2) ** 2) / (4.0 * (s1 * s1 + s2 * s2))
        )
        bhattacharyya = clamp(math.exp(-min(50.0, bc_term)), 0.0, 1.0)
        hellinger_sim = clamp(1.0 - math.sqrt(max(0.0, 1.0 - bhattacharyya)), 0.0, 1.0)
        pdf_overlap = clamp(0.65 * zone_similarity + 0.35 * bhattacharyya, 0.0, 1.0)

    metrics = {
        "pdfOverlap": round(pdf_overlap, 4),
        "zoneSimilarity": round(zone_similarity, 4),
        "bhattacharyya": round(bhattacharyya, 4),
        "hellingerSimilarity": round(hellinger_sim, 4),
        "deltaPStable": round(float(m2["pStable"]) - float(m1["pStable"]), 4),
        "deltaPDownside": round(float(m2["pDownside"]) - float(m1["pDownside"]), 4),
        "deltaPUpside": round(float(m2["pUpside"]) - float(m1["pUpside"]), 4),
        "deltaPTailCrash": round(float(m2["pTailCrash"]) - float(m1["pTailCrash"]), 4),
        "deltaMeanBps": round(
            (float(m2["meanLogReturn"]) - float(m1["meanLogReturn"])) * 10_000.0, 2
        ),
    }
    return metrics, overlay


def _regime_direction(regime: str) -> str:
    if regime in {"ACCUMULATION_STABLE", "BULLISH_EXPANSION", "REAL_BUY_PRESSURE"}:
        return "BULL"
    if regime in {"DOWNSIDE_SKEW", "TAIL_CRASH_RISK", "REAL_SELL_PRESSURE"}:
        return "BEAR"
    if regime in {"HIGH_VOL_DISPERSED", "HIGH_VOL_WHIPSAW"}:
        return "VOL"
    if regime in {
        "SPOOF_SELL_WALL",
        "SPOOF_BUY_WALL",
        "DISTANT_SPOOF_FILTERED",
        "ICEBERG_BUY_ABSORPTION",
        "ICEBERG_SELL_ABSORPTION",
    }:
        return "FILTER"
    return "CALM"


def _classify_consensus(
    r1: str,
    r2: str,
    sim: dict[str, float],
    *,
    v2_filter_reason: Optional[str] = None,
) -> dict[str, Any]:
    same_regime = r1 == r2
    same_dir = _regime_direction(r1) == _regime_direction(r2)
    overlap = sim["pdfOverlap"]
    zone_sim = sim["zoneSimilarity"]

    if v2_filter_reason and (not same_regime or overlap < 0.88):
        status = "FILTER_V2_AKTIF"
        reason = v2_filter_reason
    elif same_regime and overlap >= 0.85 and zone_sim >= 0.88:
        status = "SEPAKAT_KUAT"
        reason = (
            f"v1 dan v2 menghasilkan rezim yang sama dengan irisan distribusi {overlap*100:.1f}% — "
            "sinyal terkonfirmasi lintas kedua model."
        )
    elif same_dir and zone_sim >= 0.80:
        status = "SEPAKAT_ARAH"
        reason = (
            f"Kedua model searah (kesamaan zona {zone_sim*100:.1f}%, irisan kurva {overlap*100:.1f}%), "
            f"dengan selisih P(stabil) {sim['deltaPStable']*100:+.1f} pp."
        )
    else:
        status = "DIVERGENSI"
        reason = (
            f"v1 ({r1}) dan v2 ({r2}) berbeda bacaan (irisan kurva {overlap*100:.1f}%, "
            f"ΔP(stabil) {sim['deltaPStable']*100:+.1f} pp, ΔP(down) {sim['deltaPDownside']*100:+.1f} pp)."
        )

    meta = CONSENSUS_META[status]
    return {
        "status": status,
        "label": meta["label"],
        "tone": meta["tone"],
        "sameRegime": same_regime,
        "sameDirection": same_dir,
        "reason": reason,
        **sim,
    }


def compare_candles_v1_v2(
    candles: list[dict[str, Any]],
    *,
    horizon_days: float = 7.0,
    stable_band: float = 0.05,
    include_curve: bool = False,
) -> dict[str, Any]:
    """Run untouched Model v1 and new Model v2 on the exact same candle series."""
    v1 = fp_v1.analyze_candles_fp(
        candles,
        horizon_days=horizon_days,
        stable_band=stable_band,
        include_curve=include_curve,
    )
    v2 = analyze_candles_fp_v2(
        candles,
        horizon_days=horizon_days,
        stable_band=stable_band,
        include_curve=include_curve,
    )
    if not v1.get("ok") or not v2.get("ok"):
        return {
            "ok": False,
            "reason": v1.get("reason") or v2.get("reason") or "Data candle tidak mencukupi.",
        }

    sim, overlay = build_overlay_and_similarity(
        v1.get("curve"),
        v2.get("curve"),
        v1["metrics"],
        v2["metrics"],
        include_overlay=include_curve,
    )
    abs_score = float(v2["parameters"].get("absorptionScore", 0.0))
    filter_reason = None
    if abs_score >= 0.45 and v1["regime"] != v2["regime"]:
        filter_reason = (
            f"v2 mendeteksi absorpsi volume pasif ({abs_score*100:.0f}%) sehingga menahan drift v1."
        )
    consensus = _classify_consensus(
        v1["regime"], v2["regime"], sim, v2_filter_reason=filter_reason
    )
    out: dict[str, Any] = {
        "ok": True,
        "v1": v1,
        "v2": v2,
        "comparison": consensus,
    }
    if include_curve:
        out["overlayCurve"] = overlay
    return out


def compare_orderbook_v1_v2(
    *,
    bid_depth: float,
    ask_depth: float,
    taker_buy_vol: float,
    taker_sell_vol: float,
    canceled_bid_vol: float = 0.0,
    canceled_ask_vol: float = 0.0,
    far_bid_depth: float = 0.0,
    far_ask_depth: float = 0.0,
    wall_distance_pct: float = 0.008,
    decay_lambda: Optional[float] = None,
    spread_bps: float = 8.0,
    price_change_bps: Optional[float] = None,
    trade_window_sec: float = 15.0,
    best_bid: Optional[float] = None,
    best_ask: Optional[float] = None,
    best_bid_qty: Optional[float] = None,
    best_ask_qty: Optional[float] = None,
    raw_bid_for_v1: Optional[float] = None,
    raw_ask_for_v1: Optional[float] = None,
    raw_cancel_bid_for_v1: Optional[float] = None,
    raw_cancel_ask_for_v1: Optional[float] = None,
    sigma_per_sec: float = 0.0007,
    horizon_sec: float = 30.0,
    stable_band: float = 0.004,
    tau_spoof_sec: float = 6.0,
    mid_price: Optional[float] = None,
) -> dict[str, Any]:
    """Run untouched Model v1 and 3-Pillar Model v2 on the EXACT SAME order-book scene.

    Model v1 sees unweighted resting depth (`bid_depth + far_bid_depth`, `ask_depth + far_ask_depth`)
    and ignores spread micro-price & realized trade price impact — faithfully reproducing v1.
    Model v2 applies Taker Priority + Iceberg Absorption, Stoikov Micro-Price, and exp(-λ·d) decay.
    """
    v1_bid = (
        float(raw_bid_for_v1)
        if raw_bid_for_v1 is not None
        else max(0.0, _finite_float(bid_depth, 0.0)) + max(0.0, _finite_float(far_bid_depth, 0.0))
    )
    v1_ask = (
        float(raw_ask_for_v1)
        if raw_ask_for_v1 is not None
        else max(0.0, _finite_float(ask_depth, 0.0)) + max(0.0, _finite_float(far_ask_depth, 0.0))
    )
    v1_cb = float(raw_cancel_bid_for_v1) if raw_cancel_bid_for_v1 is not None else canceled_bid_vol
    v1_ca = float(raw_cancel_ask_for_v1) if raw_cancel_ask_for_v1 is not None else canceled_ask_vol

    v1 = fp_v1.analyze_orderbook_fp(
        bid_depth=v1_bid,
        ask_depth=v1_ask,
        taker_buy_vol=taker_buy_vol,
        taker_sell_vol=taker_sell_vol,
        canceled_bid_vol=v1_cb,
        canceled_ask_vol=v1_ca,
        sigma_per_sec=sigma_per_sec,
        horizon_sec=horizon_sec,
        stable_band=stable_band,
        tau_spoof_sec=tau_spoof_sec,
        mid_price=mid_price,
    )
    v2 = analyze_orderbook_fp_v2(
        bid_depth=bid_depth,
        ask_depth=ask_depth,
        far_bid_depth=far_bid_depth,
        far_ask_depth=far_ask_depth,
        wall_distance_pct=wall_distance_pct,
        decay_lambda=decay_lambda,
        taker_buy_vol=taker_buy_vol,
        taker_sell_vol=taker_sell_vol,
        canceled_bid_vol=canceled_bid_vol,
        canceled_ask_vol=canceled_ask_vol,
        spread_bps=spread_bps,
        price_change_bps=price_change_bps,
        trade_window_sec=trade_window_sec,
        best_bid=best_bid,
        best_ask=best_ask,
        best_bid_qty=best_bid_qty,
        best_ask_qty=best_ask_qty,
        sigma_per_sec=sigma_per_sec,
        horizon_sec=horizon_sec,
        stable_band=stable_band,
        tau_spoof_sec=tau_spoof_sec,
        mid_price=mid_price,
    )

    sim, overlay = build_overlay_and_similarity(
        v1.get("curve"),
        v2.get("curve"),
        v1["metrics"],
        v2["metrics"],
        include_overlay=True,
    )

    ms1 = v1["microstructure"]
    ms2 = v2["microstructure"]
    filter_reason: Optional[str] = None
    if ms2["absorptionIndex"] >= 0.42:
        filter_reason = (
            f"Iceberg Guard v2 aktif ({ms2['absorptionSide']} {ms2['absorptionIndex']*100:.0f}%): "
            f"v1 membaca TFI {ms1['tfi']:+.2f} sebagai '{v1['regimeLabel']}' (P_dump {v1['metrics']['pDownside']*100:.1f}%), "
            f"sedangkan v2 menahan drift karena harga tak bergerak (P_stabil {v2['metrics']['pStable']*100:.1f}%)."
        )
    elif ms2["distantWallDetected"]:
        filter_reason = (
            f"Exponential Decay v2 aktif (λ={ms2['decayLambda']:.0f}): v1 ikut menghitung tembok {ms2['distantWallSide']} jauh "
            f"(OBI v1 {ms1['obi']:+.2f}), sedangkan v2 meredamnya ke hampir nol (OBI v2 {ms2['obi']:+.2f})."
        )
    elif (ms1["muRealBpsPerSec"] * ms2["muRealBpsPerSec"] < 0) and abs(ms2["tfi"]) > 0.20:
        filter_reason = (
            f"Prioritas Taker v2 membalik arah drift: v1 tertahan buku order pasif "
            f"(μ_v1 {ms1['muRealBpsPerSec']:+.2f} bps/s), sedangkan v2 mengikuti transaksi nyata TFI "
            f"(μ_v2 {ms2['muRealBpsPerSec']:+.2f} bps/s)."
        )

    consensus = _classify_consensus(
        v1["regime"], v2["regime"], sim, v2_filter_reason=filter_reason
    )
    return {
        "ok": True,
        "mode": "orderbook_compare",
        "horizonSec": v2["horizonSec"],
        "stableBand": v2["stableBand"],
        "midPrice": v2.get("midPrice") or v1.get("midPrice"),
        "v1": v1,
        "v2": v2,
        "comparison": consensus,
        "overlayCurve": overlay,
    }


# ---------------------------------------------------------------------------
# Live Order-Book + Trades Snapshot Feeding BOTH v1 & v2 Identically
# ---------------------------------------------------------------------------

def _parse_recent_trades_v2(raw: Any) -> tuple[float, float, float, float, Optional[float]]:
    if not isinstance(raw, list) or not raw:
        return 0.0, 0.0, 0.0006, 0.0, None

    taker_buy = 0.0
    taker_sell = 0.0
    prices_ts: list[tuple[float, float]] = []

    for item in raw:
        if not isinstance(item, dict):
            continue
        price = _finite_float(item.get("price") or item.get("p"), 0.0)
        qty = max(0.0, _finite_float(item.get("qty") or item.get("q"), 0.0))
        ts_ms = _finite_float(item.get("time") or item.get("T"), 0.0)
        is_buyer_maker = bool(
            item.get("isBuyerMaker") if "isBuyerMaker" in item else item.get("m")
        )
        if price <= 0 or qty <= 0:
            continue
        if is_buyer_maker:
            taker_sell += qty
        else:
            taker_buy += qty
        if ts_ms > 0:
            prices_ts.append((ts_ms / 1000.0, price))

    if len(prices_ts) < 3:
        return taker_buy, taker_sell, 0.0006, 0.0, None

    prices_ts.sort(key=lambda pair: pair[0])
    span = max(0.25, prices_ts[-1][0] - prices_ts[0][0])
    price_change_bps = math.log(prices_ts[-1][1] / prices_ts[0][1]) * 10_000.0

    sec_closes: dict[int, float] = {}
    for t_sec, px in prices_ts:
        sec_closes[int(t_sec)] = px
    ordered = sorted(sec_closes.items())
    if len(ordered) >= 3:
        var_terms = [
            (math.log(ordered[i][1] / ordered[i - 1][1]) ** 2)
            / max(1.0, float(ordered[i][0] - ordered[i - 1][0]))
            for i in range(1, len(ordered))
        ]
        sigma_s = math.sqrt(max(mean(var_terms), 1e-10))
    else:
        sum_sq = sum(
            math.log(prices_ts[i][1] / prices_ts[i - 1][1]) ** 2
            for i in range(1, len(prices_ts))
        )
        sigma_s = math.sqrt(max(sum_sq / max(span, 1.0), 1e-10))

    return taker_buy, taker_sell, clamp(sigma_s, 3e-5, 0.015), span, price_change_bps


def _diff_cache(
    cache: dict[str, dict[str, Any]],
    symbol: str,
    bid_map: dict[str, float],
    ask_map: dict[str, float],
    taker_buy: float,
    taker_sell: float,
) -> tuple[float, float, bool]:
    now = time.monotonic()
    prev = cache.get(symbol)
    if len(cache) >= _PREV_BOOK_MAX:
        oldest = min(cache, key=lambda k: cache[k]["ts"])
        cache.pop(oldest, None)
    cache[symbol] = {"ts": now, "bids": bid_map, "asks": ask_map}
    if not prev or (now - prev["ts"]) > _PREV_BOOK_TTL_SEC:
        return 0.0, 0.0, False
    bid_van = sum(max(0.0, oq - bid_map.get(px, 0.0)) for px, oq in prev["bids"].items())
    ask_van = sum(max(0.0, oq - ask_map.get(px, 0.0)) for px, oq in prev["asks"].items())
    return max(0.0, bid_van - taker_sell), max(0.0, ask_van - taker_buy), True


async def compare_microstructure_snapshot_v1_v2(
    client: httpx.AsyncClient,
    symbol: str,
    fallback_candles: Optional[list[dict[str, Any]]] = None,
    *,
    horizon_sec: float = 30.0,
    stable_band: float = 0.004,
    prefer_seed: bool = False,
) -> dict[str, Any]:
    """Fetch one live market snapshot and evaluate BOTH v1 (top-20 raw) and v2 (top-100 3-pillar)."""
    sym = symbol.upper().strip()
    debug: list[str] = []

    if not prefer_seed:
        for source, base in fp_v1._microstructure_hosts():
            try:
                depth_url = f"{base}/depth?symbol={sym}&limit=100"
                trades_url = f"{base}/trades?symbol={sym}&limit=200"
                depth_raw, trades_raw = await asyncio.gather(
                    market_data._fetch_json(client, depth_url, timeout=4.5),
                    market_data._fetch_json(client, trades_url, timeout=4.5),
                )
                tb, ts, sigma_s, span_s, px_chg_bps = _parse_recent_trades_v2(trades_raw)
                book = compute_weighted_book(
                    depth_raw.get("bids"),
                    depth_raw.get("asks"),
                    sigma_per_sec=sigma_s,
                    horizon_sec=horizon_sec,
                    max_levels=100,
                )
                if book.get("ok") and book["weightedBidDepth"] > 0 and book["weightedAskDepth"] > 0:
                    cb_v2, ca_v2, has_diff = _diff_cache(
                        _PREV_BOOK_CACHE_V2, sym, book["weightedBidMap"], book["weightedAskMap"], tb, ts
                    )
                    cb_v1, ca_v1, _ = _diff_cache(
                        _PREV_BOOK_RAW_CACHE_FOR_CMP,
                        sym,
                        book["rawTop20BidMap"],
                        book["rawTop20AskMap"],
                        tb,
                        ts,
                    )
                    lam = book["decayLambda"]
                    wall_dist = 0.008
                    w_far = math.exp(-lam * wall_dist)
                    near_w_bid = max(0.0, book["weightedBidDepth"] - book["farBidDepth"] * w_far)
                    near_w_ask = max(0.0, book["weightedAskDepth"] - book["farAskDepth"] * w_far)

                    cmp_res = compare_orderbook_v1_v2(
                        bid_depth=near_w_bid,
                        ask_depth=near_w_ask,
                        far_bid_depth=book["farBidDepth"],
                        far_ask_depth=book["farAskDepth"],
                        wall_distance_pct=wall_dist,
                        decay_lambda=lam,
                        taker_buy_vol=tb,
                        taker_sell_vol=ts,
                        canceled_bid_vol=cb_v2,
                        canceled_ask_vol=ca_v2,
                        raw_bid_for_v1=book["top20BidDepth"],
                        raw_ask_for_v1=book["top20AskDepth"],
                        raw_cancel_bid_for_v1=cb_v1,
                        raw_cancel_ask_for_v1=ca_v1,
                        spread_bps=book["spreadBps"],
                        price_change_bps=px_chg_bps,
                        trade_window_sec=max(1.0, span_s),
                        best_bid=book["bestBid"],
                        best_ask=book["bestAsk"],
                        best_bid_qty=book["bestBidQty"],
                        best_ask_qty=book["bestAskQty"],
                        sigma_per_sec=sigma_s,
                        horizon_sec=horizon_sec,
                        stable_band=stable_band,
                        mid_price=book["midPrice"],
                    )
                    return {
                        "live": True,
                        "source": source,
                        "symbol": sym,
                        "inputs": {
                            "bidDepth": round(near_w_bid, 6),
                            "askDepth": round(near_w_ask, 6),
                            "farBidDepth": round(book["farBidDepth"], 6),
                            "farAskDepth": round(book["farAskDepth"], 6),
                            "rawBidForV1": round(book["top20BidDepth"], 6),
                            "rawAskForV1": round(book["top20AskDepth"], 6),
                            "takerBuyVol": round(tb, 6),
                            "takerSellVol": round(ts, 6),
                            "canceledBidVol": round(cb_v2, 6),
                            "canceledAskVol": round(ca_v2, 6),
                            "rawCancelBidForV1": round(cb_v1, 6),
                            "rawCancelAskForV1": round(ca_v1, 6),
                            "spreadBps": round(book["spreadBps"], 3),
                            "priceChangeBps": round(px_chg_bps, 2) if px_chg_bps is not None else None,
                            "tradeWindowSec": round(max(1.0, span_s), 1),
                            "bestBid": round(book["bestBid"], 8),
                            "bestAsk": round(book["bestAsk"], 8),
                            "bestBidQty": round(book["bestBidQty"], 6),
                            "bestAskQty": round(book["bestAskQty"], 6),
                            "sigmaPerSec": round(sigma_s, 6),
                            "midPrice": round(book["midPrice"], 8),
                            "microPrice": cmp_res["v2"]["microstructure"]["microPrice"],
                            "decayLambda": round(lam, 1),
                            "levelsUsed": book["levelsUsed"],
                            "maxDepthSpanPct": round(book["maxDepthSpanPct"], 3),
                            "cancelDiffAvailable": has_diff,
                        },
                        **cmp_res,
                    }
                debug.append(f"{source}: empty depth/trades")
            except Exception as exc:  # noqa: BLE001
                debug.append(f"{source}: {exc}")

    # Graceful fallback from OHLCV candle proxy (same inputs to both v1 & v2)
    rows = fp_v1._sanitize_candles(fallback_candles or [])
    if rows:
        last = rows[-1]
        mid = last["close"]
        vol_slice = max(last["volume"] / 2880.0, 1.0)
        buy_ratio = clamp(last["takerBuyBase"] / max(last["volume"], EPS), 0.15, 0.85)
        tb = vol_slice * buy_ratio
        ts = vol_slice * (1.0 - buy_ratio)
        bid_d = vol_slice * 2.4 * (0.7 + 0.6 * buy_ratio)
        ask_d = vol_slice * 2.4 * (0.7 + 0.6 * (1.0 - buy_ratio))
        hl_sigma_d = math.sqrt(
            (1.0 / (4.0 * math.log(2.0)))
            * (math.log(max(last["high"], EPS) / max(last["low"], EPS)) ** 2)
        )
        sigma_s = clamp((max(hl_sigma_d, 0.015) / math.sqrt(86_400.0)) * 3.5, 0.00025, 0.004)
        sp_bps = clamp(hl_sigma_d * 220.0, 2.5, 25.0)
    else:
        mid, bid_d, ask_d, tb, ts, sigma_s, sp_bps = 100.0, 100.0, 100.0, 40.0, 40.0, 0.0006, 8.0

    cmp_res = compare_orderbook_v1_v2(
        bid_depth=bid_d,
        ask_depth=ask_d,
        taker_buy_vol=tb,
        taker_sell_vol=ts,
        spread_bps=sp_bps,
        sigma_per_sec=sigma_s,
        horizon_sec=horizon_sec,
        stable_band=stable_band,
        mid_price=mid,
    )
    return {
        "live": False,
        "source": "kline-proxy-fallback",
        "symbol": sym,
        "warning": "Endpoint order-book live tidak terjangkau dari server — menampilkan proxy candle identik untuk v1 & v2. Gunakan preset, slider, atau Live WS.",
        "debug": debug[:6],
        "inputs": {
            "bidDepth": round(bid_d, 4),
            "askDepth": round(ask_d, 4),
            "farBidDepth": 0.0,
            "farAskDepth": 0.0,
            "takerBuyVol": round(tb, 4),
            "takerSellVol": round(ts, 4),
            "canceledBidVol": 0.0,
            "canceledAskVol": 0.0,
            "spreadBps": round(sp_bps, 2),
            "priceChangeBps": None,
            "sigmaPerSec": round(sigma_s, 6),
            "midPrice": round(mid, 8),
            "microPrice": cmp_res["v2"]["microstructure"]["microPrice"],
            "decayLambda": cmp_res["v2"]["microstructure"]["decayLambda"],
            "cancelDiffAvailable": False,
        },
        **cmp_res,
    }


# ---------------------------------------------------------------------------
# Mode 3 — Chronological Walk-Forward Accuracy Showdown (v1 vs v2, no lookahead)
# ---------------------------------------------------------------------------

def _summarize_model_samples(
    samples: list[dict[str, Any]],
    key_prefix: str,
    stable_band: float,
) -> dict[str, Any]:
    base_s = mean(s["yStable"] for s in samples)
    base_d = mean(s["yDownside"] for s in samples)
    base_u = mean(s["yUpside"] for s in samples)
    base_c = mean(s["yTailCrash"] for s in samples)

    pred_s = mean(s[f"{key_prefix}_pStable"] for s in samples)
    pred_d = mean(s[f"{key_prefix}_pDownside"] for s in samples)
    pred_u = mean(s[f"{key_prefix}_pUpside"] for s in samples)
    pred_c = mean(s[f"{key_prefix}_pTailCrash"] for s in samples)

    brier_s = mean(s[f"{key_prefix}_brierStable"] for s in samples)
    brier_m = mean(s[f"{key_prefix}_brierMulti"] for s in samples)
    log_loss = mean(s[f"{key_prefix}_logLoss"] for s in samples)

    base_brier_s = mean((base_s - s["yStable"]) ** 2 for s in samples)
    base_brier_m = mean(
        ((base_d - s["yDownside"]) ** 2 + (base_s - s["yStable"]) ** 2 + (base_u - s["yUpside"]) ** 2)
        / 3.0
        for s in samples
    )
    skill = 1.0 - (brier_s / base_brier_s) if base_brier_s > EPS else 0.0
    cal_gap = abs(pred_s - base_s)

    bins: list[dict[str, Any]] = []
    for idx in range(5):
        lo, hi = idx * 0.2, (idx + 1) * 0.2
        bucket = [
            s
            for s in samples
            if (lo <= s[f"{key_prefix}_pStable"] < hi)
            or (idx == 4 and s[f"{key_prefix}_pStable"] <= 1.0 and s[f"{key_prefix}_pStable"] >= lo)
        ]
        bins.append(
            {
                "bin": f"{int(lo*100)}–{int(hi*100)}%",
                "count": len(bucket),
                "predicted": round(mean(x[f"{key_prefix}_pStable"] for x in bucket), 4) if bucket else None,
                "actual": round(mean(x["yStable"] for x in bucket), 4) if bucket else None,
            }
        )

    band_pct = stable_band * 100.0
    return {
        "scores": {
            "brierStable": round(brier_s, 4),
            "baselineBrierStable": round(base_brier_s, 4),
            "brierSkillScore": round(skill, 4),
            "brierMultiZone": round(brier_m, 4),
            "baselineBrierMultiZone": round(base_brier_m, 4),
            "logLoss3Zone": round(log_loss, 4),
            "calibrationGapStable": round(cal_gap, 4),
        },
        "coverage": [
            {
                "zone": f"Stabil (±{band_pct:g}%)",
                "predicted": round(pred_s, 4),
                "actual": round(base_s, 4),
                "gap": round(pred_s - base_s, 4),
            },
            {
                "zone": f"Downside (<-{band_pct:g}%)",
                "predicted": round(pred_d, 4),
                "actual": round(base_d, 4),
                "gap": round(pred_d - base_d, 4),
            },
            {
                "zone": f"Upside (>+{band_pct:g}%)",
                "predicted": round(pred_u, 4),
                "actual": round(base_u, 4),
                "gap": round(pred_u - base_u, 4),
            },
            {
                "zone": f"Ekor Crash (<-{2*band_pct:g}%)",
                "predicted": round(pred_c, 4),
                "actual": round(base_c, 4),
                "gap": round(pred_c - base_c, 4),
            },
        ],
        "reliabilityBins": bins,
    }


def compare_backtest_v1_v2(
    universe_candles: dict[str, list[dict[str, Any]]],
    *,
    horizon_days: int = 7,
    stable_band: float = 0.05,
    step_days: int = 7,
    window: int = fp_v1.DEFAULT_WINDOW,
    source_label: str = "unknown",
    live: bool = False,
) -> dict[str, Any]:
    """Chronological walk-forward showdown evaluating v1, v2, and v1∩v2 consensus on identical anchors."""
    h = int(clamp(horizon_days, 1, 30))
    step = int(clamp(step_days, 2, 30))
    band = clamp(_finite_float(stable_band, 0.05), 0.01, 0.30)
    min_hist = max(fp_v1.MIN_CANDLES, 2 * window + 10)

    samples: list[dict[str, Any]] = []
    per_symbol: list[dict[str, Any]] = []
    skipped: list[str] = []

    for sym, raw_candles in universe_candles.items():
        rows = fp_v1._sanitize_candles(raw_candles)
        max_anchor = len(rows) - h
        if max_anchor <= min_hist:
            skipped.append(sym)
            continue

        sym_rows: list[dict[str, Any]] = []
        for anchor in range(min_hist, max_anchor + 1, step):
            past = rows[:anchor]
            c_now = past[-1]["close"]
            c_fut = rows[anchor - 1 + h]["close"]
            realized_lr = math.log(c_fut / c_now)

            r1 = fp_v1.analyze_candles_fp(
                past, horizon_days=float(h), stable_band=band, window=window, include_curve=False, n_grid=161
            )
            r2 = analyze_candles_fp_v2(
                past, horizon_days=float(h), stable_band=band, window=window, include_curve=False, n_grid=161
            )
            if not (r1.get("ok") and r2.get("ok")):
                continue

            m1, m2 = r1["metrics"], r2["metrics"]
            y_s = 1.0 if abs(realized_lr) <= band else 0.0
            y_d = 1.0 if realized_lr < -band else 0.0
            y_u = 1.0 if realized_lr > band else 0.0
            y_c = 1.0 if realized_lr < -2.0 * band else 0.0

            def _pack(m: dict[str, Any]) -> tuple[float, float, float, float, float, float, float]:
                ps, pd, pu, pc = float(m["pStable"]), float(m["pDownside"]), float(m["pUpside"]), float(m["pTailCrash"])
                bs = (ps - y_s) ** 2
                bm = ((pd - y_d) ** 2 + (ps - y_s) ** 2 + (pu - y_u) ** 2) / 3.0
                p_true = ps if y_s else (pd if y_d else pu)
                ll = -math.log(clamp(p_true, 1e-4, 1.0))
                return ps, pd, pu, pc, bs, bm, ll

            ps1, pd1, pu1, pc1, bs1, bm1, ll1 = _pack(m1)
            ps2, pd2, pu2, pc2, bs2, bm2, ll2 = _pack(m2)
            # 50/50 ensemble of v1 + v2 ("kesamaan gabungan")
            ens_m = {
                "pStable": 0.5 * (ps1 + ps2),
                "pDownside": 0.5 * (pd1 + pd2),
                "pUpside": 0.5 * (pu1 + pu2),
                "pTailCrash": 0.5 * (pc1 + pc2),
            }
            pse, pde, pue, pce, bse, bme, lle = _pack(ens_m)

            row = {
                "symbol": sym,
                "sameRegime": r1["regime"] == r2["regime"],
                "yStable": y_s,
                "yDownside": y_d,
                "yUpside": y_u,
                "yTailCrash": y_c,
                "v1_pStable": ps1,
                "v1_pDownside": pd1,
                "v1_pUpside": pu1,
                "v1_pTailCrash": pc1,
                "v1_brierStable": bs1,
                "v1_brierMulti": bm1,
                "v1_logLoss": ll1,
                "v2_pStable": ps2,
                "v2_pDownside": pd2,
                "v2_pUpside": pu2,
                "v2_pTailCrash": pc2,
                "v2_brierStable": bs2,
                "v2_brierMulti": bm2,
                "v2_logLoss": ll2,
                "ens_pStable": pse,
                "ens_pDownside": pde,
                "ens_pUpside": pue,
                "ens_pTailCrash": pce,
                "ens_brierStable": bse,
                "ens_brierMulti": bme,
                "ens_logLoss": lle,
            }
            sym_rows.append(row)
            samples.append(row)

        if sym_rows:
            b1 = mean(r["v1_brierStable"] for r in sym_rows)
            b2 = mean(r["v2_brierStable"] for r in sym_rows)
            agree = mean(1.0 if r["sameRegime"] else 0.0 for r in sym_rows)
            diff = b2 - b1
            winner = "v2" if diff < -0.0005 else ("v1" if diff > 0.0005 else "Seri")
            per_symbol.append(
                {
                    "symbol": sym,
                    "samples": len(sym_rows),
                    "actualStable": round(mean(r["yStable"] for r in sym_rows), 4),
                    "v1PredStable": round(mean(r["v1_pStable"] for r in sym_rows), 4),
                    "v2PredStable": round(mean(r["v2_pStable"] for r in sym_rows), 4),
                    "v1Brier": round(b1, 4),
                    "v2Brier": round(b2, 4),
                    "deltaBrier": round(diff, 4),
                    "agreementRate": round(agree, 4),
                    "winner": winner,
                }
            )

    if not samples:
        return {
            "ok": False,
            "reason": "Sampel walk-forward tidak cukup untuk parameter yang dipilih.",
            "skipped": skipped,
        }

    s_v1 = _summarize_model_samples(samples, "v1", band)
    s_v2 = _summarize_model_samples(samples, "v2", band)
    s_ens = _summarize_model_samples(samples, "ens", band)

    agreed = [r for r in samples if r["sameRegime"]]
    disagreed = [r for r in samples if not r["sameRegime"]]
    agreement_rate = len(agreed) / len(samples)
    mean_abs_p_diff = mean(abs(r["v1_pStable"] - r["v2_pStable"]) for r in samples)

    b1 = s_v1["scores"]["brierStable"]
    b2 = s_v2["scores"]["brierStable"]
    be = s_ens["scores"]["brierStable"]
    v2_wins = sum(1 for p in per_symbol if p["winner"] == "v2")
    v1_wins = sum(1 for p in per_symbol if p["winner"] == "v1")
    ties = sum(1 for p in per_symbol if p["winner"] == "Seri")

    if abs(b2 - b1) <= 0.001:
        overall_winner = "SERI"
        headline = "Akurasi v1 & v2 Sangat Dekat (Konsisten)"
    elif b2 < b1:
        overall_winner = "V2"
        headline = f"Model v2 Lebih Presisi (Brier lebih rendah {(b1 - b2):.4f})"
    else:
        overall_winner = "V1"
        headline = f"Model v1 Lebih Presisi (Brier lebih rendah {(b2 - b1):.4f})"

    per_symbol.sort(key=lambda r: r["deltaBrier"])
    return {
        "ok": True,
        "live": live,
        "source": source_label,
        "samples": len(samples),
        "params": {
            "horizonDays": h,
            "stableBand": band,
            "stepDays": step,
            "windowDays": window,
            "symbolsEvaluated": len(per_symbol),
        },
        "v1": s_v1,
        "v2": s_v2,
        "ensemble": s_ens,
        "headToHead": {
            "overallWinner": overall_winner,
            "headline": headline,
            "v2SymbolWins": v2_wins,
            "v1SymbolWins": v1_wins,
            "ties": ties,
            "regimeAgreementRate": round(agreement_rate, 4),
            "meanAbsPStableDiff": round(mean_abs_p_diff, 4),
            "ensembleBrierStable": be,
            "agreedSamples": len(agreed),
            "agreedBrierEns": round(mean(r["ens_brierStable"] for r in agreed), 4) if agreed else None,
            "disagreedSamples": len(disagreed),
            "disagreedBrierV1": round(mean(r["v1_brierStable"] for r in disagreed), 4) if disagreed else None,
            "disagreedBrierV2": round(mean(r["v2_brierStable"] for r in disagreed), 4) if disagreed else None,
        },
        "coverageComparison": [
            {
                "zone": s_v1["coverage"][i]["zone"],
                "actual": s_v1["coverage"][i]["actual"],
                "v1": s_v1["coverage"][i]["predicted"],
                "v2": s_v2["coverage"][i]["predicted"],
                "ensemble": s_ens["coverage"][i]["predicted"],
            }
            for i in range(len(s_v1["coverage"]))
        ],
        "perSymbol": per_symbol,
        "skipped": skipped,
    }


def formula_spec_v2() -> dict[str, Any]:
    """Specification of v1 vs v2 translation differences and shared simulation presets."""
    return {
        "name": "Fokker–Planck Model v2 (3-Pillar Translator) & Side-by-Side Comparator vs v1",
        "version": "2.0-side-by-side",
        "sharedSolver": fp_v1.formula_spec()["pde"],
        "translationDiff": [
            {
                "aspect": "Mesin Solver PDE Inti",
                "v1": "Chang–Cooper 1D implisit, batas fluks nol, massa ∫P dx = 1",
                "v2": "Identik 100% (memanggil solver yang sama agar perbandingan adil)",
            },
            {
                "aspect": "Bobot Drift μ (Taker vs Limit)",
                "v1": "0.30·TFI + 0.15·OBI (67% trade / 33% buku order), tanpa filter absorpsi",
                "v2": "0.36·TFI_eff + 0.09·OBI_λ (≥80% transaksi nyata s/d >93%) + Iceberg Absorption Guard",
            },
            {
                "aspect": "Harga Wajar & Jangkar Ekuilibrium",
                "v1": "Mid-price murni (P_bid + P_ask)/2, gaya balik −κ·x (candle: VWAP-20 rata)",
                "v2": "Stoikov Micro-Price P_bid(1−I) + P_ask·I terfilter cancel, −κ·(x − x_micro)",
            },
            {
                "aspect": "Pembobotan Jarak Order Book",
                "v1": "Bobot seragam (w = 1) di semua level top-20",
                "v2": "Exponential Distance Decay w(d) = e^(−λ·d), λ ∈ [450, 800] (tembok >0,5%–1% ≈ 0, depth 100)",
            },
        ],
        "presets": [
            {
                "id": "calm",
                "label": "Pasar Tenang",
                "subtitle": "Uji kesamaan baseline (v1 ≈ v2)",
                "params": {
                    "bidDepth": 120,
                    "askDepth": 120,
                    "farBidDepth": 0,
                    "farAskDepth": 0,
                    "wallDistancePct": 0.008,
                    "takerBuyVol": 45,
                    "takerSellVol": 45,
                    "canceledBidVol": 10,
                    "canceledAskVol": 10,
                    "spreadBps": 8,
                    "priceChangeBps": None,
                    "sigmaPerSec": 0.0005,
                    "horizonSec": 30,
                    "stableBand": 0.004,
                },
            },
            {
                "id": "distant_wall",
                "label": "Tembok Jauh 0,8%",
                "subtitle": "v1 tertekan vs v2 redam e^(−λd)",
                "params": {
                    "bidDepth": 115,
                    "askDepth": 110,
                    "farBidDepth": 0,
                    "farAskDepth": 480,
                    "wallDistancePct": 0.008,
                    "takerBuyVol": 46,
                    "takerSellVol": 44,
                    "canceledBidVol": 8,
                    "canceledAskVol": 12,
                    "spreadBps": 8,
                    "priceChangeBps": None,
                    "sigmaPerSec": 0.0006,
                    "horizonSec": 30,
                    "stableBand": 0.004,
                },
            },
            {
                "id": "iceberg_buy",
                "label": "Absorpsi Iceberg Beli",
                "subtitle": "v1 baca dump vs v2 tangkap absorpsi",
                "params": {
                    "bidDepth": 150,
                    "askDepth": 105,
                    "farBidDepth": 0,
                    "farAskDepth": 0,
                    "wallDistancePct": 0.008,
                    "takerBuyVol": 16,
                    "takerSellVol": 96,
                    "canceledBidVol": 6,
                    "canceledAskVol": 10,
                    "spreadBps": 8,
                    "priceChangeBps": 0.0,
                    "sigmaPerSec": 0.0008,
                    "horizonSec": 30,
                    "stableBand": 0.004,
                },
            },
            {
                "id": "taker_vs_wall",
                "label": "Taker Buy vs Tembok Ask",
                "subtitle": "Transaksi nyata lawan limit jual tebal",
                "params": {
                    "bidDepth": 55,
                    "askDepth": 320,
                    "farBidDepth": 0,
                    "farAskDepth": 0,
                    "wallDistancePct": 0.008,
                    "takerBuyVol": 92,
                    "takerSellVol": 24,
                    "canceledBidVol": 6,
                    "canceledAskVol": 12,
                    "spreadBps": 12,
                    "priceChangeBps": 18.0,
                    "sigmaPerSec": 0.0009,
                    "horizonSec": 30,
                    "stableBand": 0.004,
                },
            },
            {
                "id": "spoof_sell",
                "label": "Spoofing Jual (Dekat)",
                "subtitle": "v1 & v2 sama-sama deteksi spoof",
                "params": {
                    "bidDepth": 45,
                    "askDepth": 260,
                    "farBidDepth": 0,
                    "farAskDepth": 0,
                    "wallDistancePct": 0.008,
                    "takerBuyVol": 38,
                    "takerSellVol": 42,
                    "canceledBidVol": 8,
                    "canceledAskVol": 340,
                    "spreadBps": 10,
                    "priceChangeBps": None,
                    "sigmaPerSec": 0.0007,
                    "horizonSec": 30,
                    "stableBand": 0.004,
                },
            },
            {
                "id": "real_dump",
                "label": "Panic Dump Riil",
                "subtitle": "Konfirmasi SEPAKAT KUAT dump",
                "params": {
                    "bidDepth": 45,
                    "askDepth": 220,
                    "farBidDepth": 0,
                    "farAskDepth": 0,
                    "wallDistancePct": 0.008,
                    "takerBuyVol": 18,
                    "takerSellVol": 95,
                    "canceledBidVol": 15,
                    "canceledAskVol": 25,
                    "spreadBps": 14,
                    "priceChangeBps": -26.0,
                    "sigmaPerSec": 0.0011,
                    "horizonSec": 30,
                    "stableBand": 0.004,
                },
            },
            {
                "id": "real_pump",
                "label": "Breakout Beli Riil",
                "subtitle": "Konfirmasi SEPAKAT KUAT naik",
                "params": {
                    "bidDepth": 230,
                    "askDepth": 55,
                    "farBidDepth": 0,
                    "farAskDepth": 0,
                    "wallDistancePct": 0.008,
                    "takerBuyVol": 95,
                    "takerSellVol": 22,
                    "canceledBidVol": 20,
                    "canceledAskVol": 12,
                    "spreadBps": 12,
                    "priceChangeBps": 24.0,
                    "sigmaPerSec": 0.0009,
                    "horizonSec": 30,
                    "stableBand": 0.004,
                },
            },
            {
                "id": "whipsaw",
                "label": "Whipsaw / Makro",
                "subtitle": "Konfirmasi SEPAKAT KUAT 2 arah",
                "params": {
                    "bidDepth": 100,
                    "askDepth": 100,
                    "farBidDepth": 0,
                    "farAskDepth": 0,
                    "wallDistancePct": 0.008,
                    "takerBuyVol": 85,
                    "takerSellVol": 85,
                    "canceledBidVol": 45,
                    "canceledAskVol": 45,
                    "spreadBps": 22,
                    "priceChangeBps": None,
                    "sigmaPerSec": 0.0020,
                    "horizonSec": 30,
                    "stableBand": 0.004,
                },
            },
        ],
    }

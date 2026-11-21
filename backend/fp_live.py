"""Meridian — Isolated FP Live (5 · 15 · 30 · 60 Minute) Forward Tendency Engine.

100% isolated from Model v1 (backend/fokker_planck.py), Model v2 Comparator UI,
and all Meridian Screener / Token / Calibration modules. Reads Fokker–Planck PDE
primitives (`solve_fokker_planck`, `export_chart_curve`, `_sanitize_candles`) and
public exchange mirror helpers (`market_data._kline_hosts`, `_fetch_json`,
`_parse_klines`) in strictly read-only mode.

Core quantitative architecture:
1. Dual-resolution intraday feed: 1m candles (90m fast volatility & 5m/15m taker CVD)
   fused with 5m candles (20h structural volatility, 30m/2h CVD, walk-forward audit),
   plus 100-level live order-book walls + trade flow (or rolling browser WS accumulator)
   and optional BTC market-leader synchronization for altcoins.
2. Regime-adaptive multi-timescale drift μ(x, t):
   - Lo–MacKinlay Variance Ratio (VR) distinguishes persistent trends (VR >= 1.06)
     from mean-reverting chop (VR <= 0.86), curbing whipsaw chasing and amplifying
     mean-reversion κ toward distance-decayed VWAP e^(-λd) + Stoikov Micro-Price.
   - 15m passive iceberg absorption guard + close order-book wall resistance + distant
     spoof-wall exponential decay filter e^(-λd).
3. Mean-reverting intraday diffusion D(x, t):
   - Fast 1m variance reverts exponentially toward 6h structural 5m variance over τ_v=18m,
     preserving asymmetric crypto downside skew and leptokurtic fat-tail curvature.
4. Dual Chang–Cooper PDE solution:
   - Free-space density p(x, T) -> exact P(Naik), P(Turun), P10..P90 price quantiles,
     and v1-baseline side-by-side consensus.
   - Absorbing-barrier density on (-a, +a) with p(±a, t)=0 -> exact mass-conserving
     first-passage probabilities: P(Target duluan) + P(Stop duluan) + P(Belum kena) = 1.
5. Transparent 5-component scorecard + rounded plain-Indonesian data synthesis mapping
   supporting & opposing signals directly to data-derived wait, target, and invalidation prices.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import random
from statistics import mean
from typing import Any, Optional

import httpx

from backend import fokker_planck as fp_v1
from backend import fokker_planck_v2 as fp_v2
from backend import market_data

EPS = 1e-12
MIN_5M_BARS = 52
HORIZON_SET = (5, 15, 30, 60)

FP_LIVE_CACHE = market_data.TTLCache(ttl_seconds=25.0)
FP_LIVE_BT_CACHE = market_data.TTLCache(ttl_seconds=240.0)
FP_LIVE_BTC_CACHE = market_data.TTLCache(ttl_seconds=30.0)


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _f(v: Any, default: float = 0.0) -> float:
    try:
        x = float(v)
        return x if math.isfinite(x) else default
    except (TypeError, ValueError):
        return default


def _fmt_px(px: float) -> str:
    n = abs(px)
    if n >= 1000:
        return f"${px:,.2f}"
    if n >= 1:
        return f"${px:,.4f}"
    if n >= 0.01:
        return f"${px:,.5f}"
    return f"${px:,.7f}"


def _fmt_qty(q: float) -> str:
    n = abs(q)
    if n >= 1_000_000:
        return f"{q / 1_000_000:.2f}M"
    if n >= 100:
        return f"{q:,.0f}"
    if n >= 1:
        return f"{q:.2f}"
    return f"{q:.4f}"


def _fmt_range(a: float, b: float) -> str:
    lo, hi = (a, b) if a <= b else (b, a)
    if lo > 0 and (hi - lo) / lo < 0.0005:
        return _fmt_px(0.5 * (lo + hi))
    return f"{_fmt_px(lo)} – {_fmt_px(hi)}"


def _ema(vals: list[float], span: int) -> float:
    if not vals:
        return 0.0
    a = 2.0 / (span + 1.0)
    acc = vals[0]
    for v in vals[1:]:
        acc = a * v + (1.0 - a) * acc
    return acc


def compute_variance_ratio(rets: list[float], q: int = 4) -> float:
    """Uncentered second-moment Variance Ratio E[(Σ_q r)²] / (q · E[r²]).

    Captures BOTH persistent positive drift and positive serial correlation (> 1.0),
    while dropping < 1.0 when bar moves actively reverse / whipsaw back and forth.
    """
    n = len(rets)
    if n < max(q * 4, 16):
        return 1.0
    v1 = mean(r * r for r in rets)
    if v1 <= EPS:
        return 1.0
    q_rets = [sum(rets[i : i + q]) for i in range(n - q + 1)]
    if not q_rets:
        return 1.0
    # Bias-corrected overlapping ratio
    vq = sum(qr * qr for qr in q_rets) / max(n - q + 1, 1)
    return clamp(vq / max(q * v1, EPS), 0.25, 2.50)


def compute_efficiency_ratio(rets: list[float]) -> float:
    """Kaufman directional Efficiency Ratio |Σ r| / Σ |r| in [0, 1]."""
    if not rets:
        return 0.0
    path = sum(abs(r) for r in rets)
    return clamp(abs(sum(rets)) / path, 0.0, 1.0) if path > EPS else 0.0


def _exp_cvd(bars: list[dict[str, float]], decay: float) -> tuple[float, float, float]:
    """Return (cvd_imbalance [-1,1], buy_share [0,1], total_base_vol)."""
    num_d = 0.0
    den_v = 0.0
    raw_v = 0.0
    for age, c in enumerate(reversed(bars)):
        v = _f(c.get("volume"), 0.0)
        if v <= EPS:
            continue
        tb = clamp(_f(c.get("takerBuyBase"), 0.5 * v), 0.0, v)
        w = math.exp(-decay * age)
        num_d += w * (2.0 * tb - v)
        den_v += w * v
        raw_v += v
    imb = clamp(num_d / den_v, -1.0, 1.0) if den_v > EPS else 0.0
    return imb, clamp(0.5 * (imb + 1.0), 0.0, 1.0), raw_v


# ---------------------------------------------------------------------------
# 1. Exact Absorbing-Barrier Chang–Cooper First-Passage PDE Solver
# ---------------------------------------------------------------------------


def solve_absorbing_first_passage(
    mu_fn,
    diff_fn,
    *,
    band: float,
    horizon: float,
    n_grid: int = 101,
    n_steps: int = 36,
) -> dict[str, float]:
    """Solve ∂p/∂t = -∂(μp)/∂x + ∂²(Dp)/∂x² on (-band, +band) with p(±band, t) = 0.

    Implicit backward-Euler Chang–Cooper flux scheme: unconditionally non-negative and
    strictly mass-conserving (`pHitTargetFirst + pHitStopFirst + pNeitherYet == 1.0`).
    """
    a = max(float(band), 1e-5)
    h = max(float(horizon), 0.5)
    m = n_grid if n_grid % 2 == 1 else n_grid + 1
    dx = (2.0 * a) / (m + 1)
    x = [-a + (i + 1) * dx for i in range(m)]
    dt = h / max(8, int(n_steps))

    sig0 = max(1.15 * dx, 0.035 * a)
    p = [math.exp(-0.5 * (xi / sig0) ** 2) for xi in x]
    mass0 = sum(p) * dx
    if mass0 <= EPS:
        return {
            "pHitTargetFirst": 0.0,
            "pHitStopFirst": 0.0,
            "pNeitherYet": 1.0,
            "condTargetVsStop": 0.5,
        }
    p = [v / mass0 for v in p]

    hit_down = 0.0
    hit_up = 0.0
    r = dt / dx

    for step in range(max(8, int(n_steps))):
        t_mid = (step + 0.5) * dt
        b_flux = [0.0] * (m + 1)
        c_flux = [0.0] * (m + 1)
        for j in range(m + 1):
            xf = -a + (j + 0.5) * dx
            mu_f = float(mu_fn(xf, t_mid))
            d_f = max(float(diff_fn(xf, t_mid)), 1e-12)
            d_over_dx = d_f / dx
            w = (mu_f * dx) / d_f
            if w >= 40.0:
                b_flux[j] = mu_f
                c_flux[j] = 0.0
            elif w <= -40.0:
                b_flux[j] = 0.0
                c_flux[j] = -mu_f
            elif abs(w) < 1e-4:
                b_flux[j] = d_over_dx * (1.0 + 0.5 * w + (w * w) / 12.0)
                c_flux[j] = d_over_dx * (1.0 - 0.5 * w + (w * w) / 12.0)
            else:
                b_flux[j] = d_over_dx * (w / (1.0 - math.exp(-w)))
                c_flux[j] = d_over_dx * (w / (math.exp(w) - 1.0))

        # Thomas tridiagonal solver for implicit step
        cp = [0.0] * m
        dp = [0.0] * m
        diag0 = 1.0 + r * (b_flux[1] + c_flux[0])
        sup0 = -r * c_flux[1] if m > 1 else 0.0
        cp[0] = sup0 / diag0
        dp[0] = p[0] / diag0
        for i in range(1, m):
            sub_i = -r * b_flux[i]
            diag_i = 1.0 + r * (b_flux[i + 1] + c_flux[i])
            sup_i = -r * c_flux[i + 1] if i < m - 1 else 0.0
            denom = max(diag_i - sub_i * cp[i - 1], EPS)
            cp[i] = sup_i / denom if i < m - 1 else 0.0
            dp[i] = (p[i] - sub_i * dp[i - 1]) / denom

        p_new = [0.0] * m
        p_new[m - 1] = max(0.0, dp[m - 1])
        for i in range(m - 2, -1, -1):
            p_new[i] = max(0.0, dp[i] - cp[i] * p_new[i + 1])

        hit_down += dt * c_flux[0] * p_new[0]
        hit_up += dt * b_flux[m] * p_new[m - 1]
        p = p_new

    survive = max(0.0, sum(p) * dx)
    tot = max(hit_up + hit_down + survive, EPS)
    p_tgt = clamp(hit_up / tot, 0.0, 1.0)
    p_stp = clamp(hit_down / tot, 0.0, 1.0)
    p_nei = clamp(1.0 - p_tgt - p_stp, 0.0, 1.0)
    touched = p_tgt + p_stp
    cond = (p_tgt / touched) if touched > 1e-6 else 0.5
    return {
        "pHitTargetFirst": round(p_tgt, 4),
        "pHitStopFirst": round(p_stp, 4),
        "pNeitherYet": round(p_nei, 4),
        "condTargetVsStop": round(cond, 4),
    }


# ---------------------------------------------------------------------------
# 2. Order-Book Wall Map (100-Level + e^(-λd) Anti-Spoof + Stoikov Micro-Price)
# ---------------------------------------------------------------------------


def _book_qty_unit(source: str) -> str:
    """Label satuan kolom jumlah order book: bursa US = kontrak (0.001 BTC), lainnya = unit koin."""
    src = (source or "").lower()
    if "binance-us" in src or "binance_us" in src:
        return "kontrak"
    return "koin"


def analyze_orderbook_walls(
    bids_raw: list[tuple[float, float]],
    asks_raw: list[tuple[float, float]],
    *,
    decay_lambda: float = 280.0,
    trades_tfi: float = 0.0,
    spoof_cancel_idx: float = 0.0,
    absorption_micro: float = 0.0,
    accumulated_min: float = 0.25,
    source: str = "live",
    live: bool = True,
) -> Optional[dict[str, Any]]:
    """Parse order-book depth into Top-5 Support/Resistance walls, spoof filter & OBI."""
    bids = sorted([(float(p), float(q)) for p, q in bids_raw if p > 0 and q > 0], key=lambda x: -x[0])
    asks = sorted([(float(p), float(q)) for p, q in asks_raw if p > 0 and q > 0], key=lambda x: x[0])
    if not bids or not asks:
        return None

    best_bid, q_bid = bids[0]
    best_ask, q_ask = asks[0]
    if best_ask < best_bid:
        best_ask = best_bid
    mid = 0.5 * (best_bid + best_ask)
    if mid <= EPS:
        return None

    spread_bps = ((best_ask - best_bid) / mid) * 1e4
    lam = clamp(float(decay_lambda), 60.0, 1200.0)

    # Stoikov Micro-Price (adjusted for cancel-spoof asymmetry when available)
    imb_top = q_bid / max(q_bid + q_ask, EPS)
    micro_px = best_bid * (1.0 - imb_top) + best_ask * imb_top
    micro_shift_bps = math.log(max(micro_px, EPS) / mid) * 1e4

    raw_bid_tot = sum(q for _, q in bids)
    raw_ask_tot = sum(q for _, q in asks)
    raw_obi = (raw_bid_tot - raw_ask_tot) / max(raw_bid_tot + raw_ask_tot, EPS)

    def _enrich(levels: list[tuple[float, float]], side_tot: float, side: str):
        enriched = []
        w_tot = 0.0
        for px, q in levels:
            dist = abs(px - mid) / mid
            dist_pct = dist * 100.0
            share_pct = (q / max(side_tot, EPS)) * 100.0
            w = math.exp(-min(40.0, lam * dist))
            wq = q * w
            w_tot += wq
            if dist_pct >= 0.45 and share_pct >= 20.0 and w < 0.32:
                status = "SPOOF_JAUH"
            elif dist_pct <= 0.35:
                status = "AKTIF_DEKAT"
            else:
                status = "MENENGAH"
            enriched.append(
                {
                    "side": side,
                    "price": round(px, 8),
                    "qty": round(q, 5),
                    "quoteUsd": round(px * q, 2),
                    "distPct": round(dist_pct, 3),
                    "sharePct": round(share_pct, 1),
                    "decayWeight": round(w, 3),
                    "cutPct": round((1.0 - w) * 100.0, 0),
                    "weightedQty": wq,
                    "status": status,
                }
            )
        return enriched, w_tot

    bid_items, w_bid_tot = _enrich(bids, raw_bid_tot, "BID")
    ask_items, w_ask_tot = _enrich(asks, raw_ask_tot, "ASK")
    w_obi = (w_bid_tot - w_ask_tot) / max(w_bid_tot + w_ask_tot, EPS)

    # Top 5 support & Top 5 resistance sorted by raw qty (clean dict access, zero tuple bug)
    top_bids = [
        {k: v for k, v in row.items() if k != "weightedQty"}
        for row in sorted(bid_items, key=lambda r: -r["qty"])[:5]
    ]
    top_asks = [
        {k: v for k, v in row.items() if k != "weightedQty"}
        for row in sorted(ask_items, key=lambda r: -r["qty"])[:5]
    ]

    # Strongest REAL nearby wall (sorted by decay-weighted qty within 0.65%)
    near_bids = [r for r in bid_items if r["distPct"] <= 0.65]
    near_asks = [r for r in ask_items if r["distPct"] <= 0.65]
    active_bid_wall = (
        {k: v for k, v in max(near_bids, key=lambda r: r["weightedQty"]).items() if k != "weightedQty"}
        if near_bids
        else (top_bids[0] if top_bids else None)
    )
    active_ask_wall = (
        {k: v for k, v in max(near_asks, key=lambda r: r["weightedQty"]).items() if k != "weightedQty"}
        if near_asks
        else (top_asks[0] if top_asks else None)
    )

    spoof_walls = [
        {k: v for k, v in r.items() if k != "weightedQty"}
        for r in (bid_items + ask_items)
        if r["status"] == "SPOOF_JAUH"
    ]
    spoof_walls.sort(key=lambda r: -r["sharePct"])

    return {
        "live": live,
        "source": source,
        "levelsCount": len(bids) + len(asks),
        "qtyUnit": _book_qty_unit(source),
        "midPrice": round(mid, 8),
        "bestBid": round(best_bid, 8),
        "bestAsk": round(best_ask, 8),
        "spreadBps": round(spread_bps, 2),
        "microPrice": round(micro_px, 8),
        "microShiftBps": round(micro_shift_bps, 2),
        "decayLambda": round(lam, 1),
        "rawObi": round(raw_obi, 4),
        "weightedObi": round(w_obi, 4),
        "tfi": round(clamp(trades_tfi, -1.0, 1.0), 4),
        "spoofIndex": round(clamp(spoof_cancel_idx, 0.0, 1.0), 4),
        "absorptionIndex": round(clamp(absorption_micro, 0.0, 1.0), 4),
        "accumulatedMin": round(clamp(accumulated_min, 0.1, 60.0), 2),
        "activeBidWall": active_bid_wall,
        "activeAskWall": active_ask_wall,
        "spoofWalls": spoof_walls[:3],
        "topSupports": top_bids,
        "topResistances": top_asks,
    }


def seed_orderbook_walls(symbol: str, anchor_price: float, decay_lambda: float = 280.0) -> dict[str, Any]:
    """Deterministic realistic 60-level order book when exchange REST depth is unreachable."""
    sym = symbol.upper().strip()
    digest = int(hashlib.sha256(f"fp-live-book:{sym}".encode()).hexdigest()[:12], 16)
    rng = random.Random(digest)
    mid = max(float(anchor_price), 1e-6)
    half_sp = mid * 0.00012
    base_q = max(12_000.0 / mid, 0.05)

    bids, asks = [], []
    bid_wall_i = rng.randint(4, 14)
    ask_wall_i = rng.randint(5, 16)
    spoof_side = rng.choice(["ASK", "BID", "NONE"])

    for i in range(1, 51):
        step = i * 0.00018
        qb = base_q * rng.uniform(0.45, 1.45)
        qa = base_q * rng.uniform(0.45, 1.45)
        if i == bid_wall_i:
            qb *= rng.uniform(6.5, 10.5)
        if i == ask_wall_i:
            qa *= rng.uniform(6.0, 9.8)
        if i == 42 and spoof_side == "ASK":
            qa *= 18.0
        elif i == 44 and spoof_side == "BID":
            qb *= 18.0
        bids.append((mid - half_sp - mid * step, qb))
        asks.append((mid + half_sp + mid * step, qa))

    res = analyze_orderbook_walls(
        bids,
        asks,
        decay_lambda=decay_lambda,
        trades_tfi=rng.uniform(-0.28, 0.28),
        spoof_cancel_idx=0.18 if spoof_side != "NONE" else 0.06,
        accumulated_min=0.5,
        source="seed-orderbook",
        live=False,
    )
    assert res is not None
    return res


async def fetch_live_orderbook_walls(
    client: httpx.AsyncClient,
    symbol: str,
    *,
    anchor_price: float,
    decay_lambda: float = 280.0,
    prefer_seed: bool = False,
) -> dict[str, Any]:
    """Fetch 100-level public order book + recent 500 trades across Binance mirrors."""
    sym = symbol.upper().strip()
    if not prefer_seed:
        hosts = [
            ("binance-vision", "https://data-api.binance.vision/api/v3"),
            ("binance", "https://api.binance.com/api/v3"),
            ("binance-gcp", "https://api-gcp.binance.com/api/v3"),
            ("binance-us", "https://api.binance.us/api/v3"),
        ]
        for label, base in hosts:
            try:
                d_task = market_data._fetch_json(
                    client, f"{base}/depth?symbol={sym}&limit=100", timeout=4.5
                )
                t_task = market_data._fetch_json(
                    client, f"{base}/trades?symbol={sym}&limit=500", timeout=4.5
                )
                depth_raw, trades_raw = await asyncio.gather(d_task, t_task)
                if not isinstance(depth_raw, dict) or not depth_raw.get("bids"):
                    continue
                bids = [(float(p), float(q)) for p, q in depth_raw.get("bids", [])[:100]]
                asks = [(float(p), float(q)) for p, q in depth_raw.get("asks", [])[:100]]

                tb = ts = 0.0
                t0 = t1 = 0.0
                if isinstance(trades_raw, list) and trades_raw:
                    for tr in trades_raw:
                        q = _f(tr.get("qty"), 0.0)
                        if q <= 0:
                            continue
                        if tr.get("isBuyerMaker"):
                            ts += q
                        else:
                            tb += q
                    t0 = _f(trades_raw[0].get("time"), 0.0) / 1000.0
                    t1 = _f(trades_raw[-1].get("time"), 0.0) / 1000.0
                tfi = (tb - ts) / max(tb + ts, EPS)
                acc_min = clamp((t1 - t0) / 60.0 if t1 > t0 else 0.25, 0.1, 15.0)

                parsed = analyze_orderbook_walls(
                    bids,
                    asks,
                    decay_lambda=decay_lambda,
                    trades_tfi=tfi,
                    accumulated_min=acc_min,
                    source=label,
                    live=True,
                )
                if parsed is not None:
                    return parsed
            except Exception:  # noqa: BLE001
                continue

    return seed_orderbook_walls(sym, anchor_price, decay_lambda=decay_lambda)


# ---------------------------------------------------------------------------
# 3. 1-Minute Kline Fetcher (Paired with 5-Minute Klines)
# ---------------------------------------------------------------------------


def derive_1m_from_5m(candles_5m: list[dict[str, Any]], count: int = 90) -> list[dict[str, Any]]:
    """Deterministic sub-bar interpolation when 1m REST endpoint is offline."""
    rows = fp_v1._sanitize_candles(candles_5m)
    if not rows:
        return []
    need_5m = max(12, (count // 5) + 2)
    out: list[dict[str, Any]] = []
    for c in rows[-need_5m:]:
        op, hi, lo, cl = c["open"], c["high"], c["low"], c["close"]
        vol5 = max(c["volume"], EPS)
        tb5 = clamp(c["takerBuyBase"], 0.0, vol5)
        tr5 = max(c["trades"], 5.0)
        buy_sh = tb5 / vol5
        t0 = int(c["date"])
        # Smooth 5-step path respecting open, high, low, close and exact 5m volume/taker totals
        wps = [
            op,
            0.65 * op + 0.35 * (lo if cl >= op else hi),
            0.50 * (op + cl),
            0.35 * cl + 0.65 * (hi if cl >= op else lo),
            cl,
            cl,
        ]
        for k in range(5):
            o1 = max(EPS, wps[k])
            c1 = max(EPS, wps[k + 1] if k < 4 else cl)
            h1 = min(hi, max(o1, c1) * 1.0003)
            l1 = max(lo, min(o1, c1) * 0.9997)
            v1 = vol5 / 5.0
            sh_k = clamp(buy_sh + (0.04 if c1 > o1 else (-0.04 if c1 < o1 else 0.0)), 0.05, 0.95)
            out.append(
                {
                    "date": t0 + k * 60_000,
                    "open": o1,
                    "high": max(h1, o1, c1),
                    "low": min(l1, o1, c1),
                    "close": c1,
                    "volume": v1,
                    "quoteVolume": v1 * ((o1 + c1) * 0.5),
                    "trades": tr5 / 5.0,
                    "takerBuyBase": v1 * sh_k,
                }
            )
    # Rescale last bar's takerBuy slightly is unnecessary; preserves clean continuity
    return out[-count:]


async def fetch_1m_klines(
    client: httpx.AsyncClient,
    symbol: str,
    *,
    limit: int = 90,
    fallback_5m: Optional[list[dict[str, Any]]] = None,
    prefer_seed: bool = False,
) -> dict[str, Any]:
    """Fetch public 1m OHLCV + taker-buy candles across configured mirror hosts."""
    sym = symbol.upper().strip()
    lim = max(30, min(int(limit), 240))
    if not prefer_seed:
        for source, base, _params in market_data._kline_hosts():
            try:
                url = f"{base}?symbol={sym}&interval=1m&limit={lim}"
                raw = await market_data._fetch_json(client, url, timeout=4.5)
                candles = market_data._parse_klines(raw)
                if len(candles) >= 30:
                    return {"source": f"{source}-1m", "live": True, "candles": candles}
            except Exception:  # noqa: BLE001
                continue
    return {
        "source": "derived-1m",
        "live": False,
        "candles": derive_1m_from_5m(fallback_5m or [], lim),
    }


# ---------------------------------------------------------------------------
# 4. Core Upgraded FP Live Forecast Solver (5 · 15 · 30 · 60 Min)
# ---------------------------------------------------------------------------


CALL_META: dict[str, dict[str, str]] = {
    "NAIK_KUAT": {"label": "CONDONG NAIK KUAT", "short": "NAIK", "tone": "up-strong"},
    "NAIK_TIPIS": {"label": "CONDONG NAIK", "short": "NAIK", "tone": "up-mild"},
    "NETRAL_TUNGGU": {"label": "NETRAL · IMBANG", "short": "NETRAL", "tone": "neutral"},
    "TURUN_TIPIS": {"label": "CONDONG TURUN", "short": "TURUN", "tone": "down-mild"},
    "TURUN_KUAT": {"label": "CONDONG TURUN KUAT", "short": "TURUN", "tone": "down-strong"},
}


def _classify_call(p_up: float, *, strict: bool = False) -> dict[str, str]:
    p_dn = 1.0 - p_up
    s_thr = 0.590 if strict else 0.580
    m_thr = 0.555 if strict else 0.542
    if p_up >= s_thr:
        code = "NAIK_KUAT"
    elif p_up >= m_thr:
        code = "NAIK_TIPIS"
    elif p_dn >= s_thr:
        code = "TURUN_KUAT"
    elif p_dn >= m_thr:
        code = "TURUN_TIPIS"
    else:
        code = "NETRAL_TUNGGU"
    return {"code": code, **CALL_META[code]}


def _build_rounded_narrative(
    *,
    symbol: str,
    horizon_min: int,
    price_now: float,
    call: dict[str, str],
    stats: dict[str, Any],
    first_passage: dict[str, float],
    target_band: float,
    components: list[dict[str, Any]],
    regime_info: dict[str, Any],
    eq_price: float,
    vwap_decay: float,
    book: Optional[dict[str, Any]],
    absorption: dict[str, Any],
    btc_guard: Optional[dict[str, Any]],
    wf_brake: Optional[dict[str, Any]],
) -> dict[str, Any]:
    """Generate crisp, rounded conclusion + data-backed opposing notes & wait levels."""
    base = symbol[:-4] if symbol.endswith("USDT") else symbol
    q = stats["priceQuantiles"]
    p_up_pct = round(stats["pUp"] * 100.0)
    p_dn_pct = 100 - p_up_pct
    short_dir = call["short"]

    pos_comps = [c for c in components if c["stance"] == "POSITIF"]
    neg_comps = [c for c in components if c["stance"] == "NEGATIF"]
    neu_comps = [c for c in components if c["stance"] == "NETRAL"]
    n_total = len(components)

    bid_w = (book or {}).get("activeBidWall")
    ask_w = (book or {}).get("activeAskWall")
    spoof_ws = (book or {}).get("spoofWalls") or []

    # Compute data-derived wait / target / invalidation levels
    # Support anchor: real nearby Bid wall or VWAP/P25
    sup_candidates = [v for v in [bid_w["price"] if bid_w and bid_w["price"] < price_now else None, vwap_decay if vwap_decay < price_now else None, q["p25"]] if v and v > 0]
    res_candidates = [v for v in [ask_w["price"] if ask_w and ask_w["price"] > price_now else None, vwap_decay if vwap_decay > price_now else None, q["p75"]] if v and v > 0]

    sup_near = max(sup_candidates) if sup_candidates else q["p25"]
    # Guard penyajian (bukan rumus): anchor support tak boleh di atas spot, resistance tak boleh di bawah spot
    sup_near = min(sup_near, price_now * 0.99985)
    res_near = min(res_candidates) if res_candidates else q["p75"]
    res_near = max(res_near, price_now * 1.00015)
    res_high = max(q["p75"], res_near)

    # Upside / downside potential rounded cleanly
    up_pot_px = max(q["targetUp"], q["p75"])
    up_pot_pct = (up_pot_px / price_now - 1.0) * 100.0
    dn_pot_px = min(q["stopDown"], q["p25"])
    dn_pot_pct = (dn_pot_px / price_now - 1.0) * 100.0
    exp_pct = stats["expectedMovePct"]

    if short_dir == "NAIK":
        headline = (
            f"Dibulatkan: {call['label']} ({p_up_pct}%) · "
            f"Target Data +{up_pot_pct:.2f}% ({_fmt_px(q['targetUp'])} – {_fmt_px(q['p90'])})"
        )
        pos_reasons = "; ".join(c["brief"] for c in pos_comps[:3]) or "dorongan beli mengungguli sebaran harga"
        main_thesis = (
            f"Dari {n_total} komponen rumus {base} untuk {horizon_min} menit ke depan, "
            f"{len(pos_comps)} komponen positif mendukung naik (selisih peluang {stats['netEdge']*100:+.1f}%, "
            f"rata-rata dorongan {exp_pct:+.2f}% ke tengah {_fmt_px(q['median'])}): {pos_reasons}. "
            f"Dalam adu batas ±{target_band*100:.2f}%, peluang menyentuh target atas {_fmt_px(q['targetUp'])} "
            f"duluan sebesar {first_passage['pHitTargetFirst']*100:.1f}% berbanding "
            f"{first_passage['pHitStopFirst']*100:.1f}% ke batas bawah {_fmt_px(q['stopDown'])} "
            f"({first_passage['pNeitherYet']*100:.1f}% masih di tengah saat waktu habis)."
        )

        # Opposing / negative signals -> translate into concrete wait & wall levels
        wait_lo = min(
            [v for v in [bid_w["price"] if bid_w and bid_w["price"] < price_now else None, vwap_decay if vwap_decay < price_now else None, q["p25"]] if v],
            default=q["p25"],
        )
        wait_hi = max(wait_lo, min(sup_near, price_now * 0.99985))
        wait_txt = _fmt_range(wait_lo, wait_hi)
        bid_note = (
            f"dijaga tembok beli {_fmt_qty(bid_w['qty'])} {base} ({bid_w['sharePct']:.0f}% sisi bid) di {_fmt_px(bid_w['price'])}"
            if bid_w
            else f"dekat harga wajar VWAP {_fmt_px(vwap_decay)}"
        )
        ask_note = (
            f"Di sisi atas, hambatan terdekat ada di tembok jual {_fmt_px(ask_w['price'])} "
            f"({_fmt_qty(ask_w['qty'])} {base}, jarak +{ask_w['distPct']:.2f}%) — jika level ini dimakan habis, "
            f"jalur terbuka ke {_fmt_range(q['targetUp'], q['p90'])}."
            if ask_w and ask_w["price"] > price_now
            else f"Jalur atas mengarah ke {_fmt_range(q['targetUp'], q['p90'])}."
        )
        inv_px = min(q["stopDown"], vwap_decay * (1.0 - 0.35 * target_band))

        if neg_comps:
            neg_reasons = "; ".join(c["brief"] for c in neg_comps)
            counter_text = (
                f"Catatan {len(neg_comps)} sinyal penahan/jual: {neg_reasons}. "
                f"Karena ada tahanan tersebut, menurut data area tunggu koreksi paling padat berada di "
                f"{wait_txt} ({bid_note}). {ask_note} "
                f"Hitungan naik berbalik negatif jika harga jebol di bawah {_fmt_px(inv_px)} disertai arus CVD 15m berbalik merah."
            )
        else:
            counter_text = (
                f"Tidak ada komponen rumus yang berlawanan (0 sinyal jual). "
                f"Titik pijakan bawah terdekat menurut data ada di {wait_txt} ({bid_note}). "
                f"{ask_note} Hitungan naik baru gugur jika harga turun menembus {_fmt_px(inv_px)}."
            )

        key_levels = {
            "waitLabel": "Area Tunggu Bawah (Data Bid/VWAP)",
            "waitRange": wait_txt,
            "targetLabel": f"Area Target Naik ({horizon_min}m)",
            "targetRange": _fmt_range(q["targetUp"], q["p90"]),
            "triggerLabel": "Hambatan Tembus Atas",
            "triggerPrice": _fmt_px(ask_w["price"] if ask_w else q["p75"]),
            "invalidationLabel": "Titik Balik Rumus (<50%)",
            "invalidationPrice": _fmt_px(inv_px),
        }

    elif short_dir == "TURUN":
        headline = (
            f"Dibulatkan: {call['label']} ({p_dn_pct}%) · "
            f"Estimasi Tekanan {dn_pot_pct:.2f}% ({_fmt_px(q['stopDown'])} – {_fmt_px(q['p10'])})"
        )
        neg_reasons = "; ".join(c["brief"] for c in neg_comps[:3]) or "tekanan jual menggeser kurva ke kiri"
        main_thesis = (
            f"Dari {n_total} komponen rumus {base} untuk {horizon_min} menit ke depan, "
            f"{len(neg_comps)} komponen negatif menandakan arus jual dominan (peluang turun {stats['pDown']*100:.1f}% "
            f"vs naik {stats['pUp']*100:.1f}%, dorongan rata-rata {exp_pct:+.2f}% ke {_fmt_px(q['median'])}): {neg_reasons}. "
            f"Pada batas ±{target_band*100:.2f}%, peluang menyentuh batas bawah {_fmt_px(q['stopDown'])} "
            f"duluan mencapai {first_passage['pHitStopFirst']*100:.1f}% dibanding "
            f"{first_passage['pHitTargetFirst']*100:.1f}% ke atas ({first_passage['pNeitherYet']*100:.1f}% belum menyentuh keduanya)."
        )

        sell_txt = _fmt_range(max(res_near, price_now * 1.00015), res_high)
        bounce_txt = _fmt_range(q["p10"], min(q["stopDown"], price_now * 0.99985))
        ask_note = (
            f"tertahan tembok jual {_fmt_qty(ask_w['qty'])} {base} ({ask_w['sharePct']:.0f}% sisi ask) di {_fmt_px(ask_w['price'])}"
            if ask_w and ask_w["price"] > price_now
            else f"tertahan area harga wajar VWAP {_fmt_px(vwap_decay)}"
        )
        inv_px = max(q["targetUp"], vwap_decay * (1.0 + 0.35 * target_band))

        if pos_comps:
            pos_reasons = "; ".join(c["brief"] for c in pos_comps)
            counter_text = (
                f"Catatan {len(pos_comps)} sinyal penahan bawah: {pos_reasons}. "
                f"Menurut data, pantulan naik ke {sell_txt} masih {ask_note}, "
                f"sedangkan area tunggu redanya jualan (penahan beli padat) berada di "
                f"{bounce_txt}"
                + (f" (tembok bid terdekat {_fmt_px(bid_w['price'])} sebesar {_fmt_qty(bid_w['qty'])} {base}). " if bid_w and bid_w["price"] < price_now else ". ")
                + f"Tekanan turun baru patah jika harga merebut kembali {_fmt_px(inv_px)} dengan CVD 15m positif."
            )
        else:
            counter_text = (
                f"Seluruh komponen aktif mengarah ke tekanan jual ({len(pos_comps)} sinyal beli). "
                f"Area tumpukan jual terdekat di atas ada di {sell_txt} ({ask_note}), "
                f"sementara sasaran bantalan bawah menurut sebaran rumus berada di {bounce_txt}. "
                f"Sinyal turun batal bila harga menembus naik di atas {_fmt_px(inv_px)}."
            )

        key_levels = {
            "waitLabel": "Area Tahan Bawah (Tunggu Pantul)",
            "waitRange": bounce_txt,
            "targetLabel": "Tumpukan Jual Atas (Ask/VWAP)",
            "targetRange": sell_txt,
            "triggerLabel": "Penahan Bid Terdekat",
            "triggerPrice": _fmt_px(bid_w["price"] if bid_w else q["p25"]),
            "invalidationLabel": "Titik Balik Naik (>50%)",
            "invalidationPrice": _fmt_px(inv_px),
        }

    else:
        spread_pct = stats["horizonStdPct"]
        headline = (
            f"Dibulatkan: NETRAL / IMBANG ({p_up_pct}% Naik : {p_dn_pct}% Turun) · "
            f"Ayunan Terkunci ±{spread_pct:.2f}%"
        )
        pos_str = ", ".join(c["brief"] for c in pos_comps) if pos_comps else "belum ada dorongan beli kuat"
        neg_str = ", ".join(c["brief"] for c in neg_comps) if neg_comps else "belum ada tekanan jual kuat"
        main_thesis = (
            f"Komponen rumus {base} sedang berimbang ({len(pos_comps)} positif, {len(neg_comps)} negatif, "
            f"{len(neu_comps)} datar) dengan pola pasar {regime_info['label']} (VR {regime_info['vr']:.2f}). "
            f"Sisi beli mencatat: {pos_str}; dibalas sisi jual: {neg_str} — sehingga pergeseran bersih cuma "
            f"{exp_pct:+.2f}% di sekitar harga wajar {_fmt_px(eq_price)}."
        )
        low_zone = bid_w["price"] if (bid_w and bid_w["distPct"] >= 0.08 and bid_w["price"] < price_now) else q["stopDown"]
        high_zone = ask_w["price"] if (ask_w and ask_w["distPct"] >= 0.08 and ask_w["price"] > price_now) else q["targetUp"]
        counter_text = (
            f"Karena harga kini ({_fmt_px(price_now)}) berada di tengah keseimbangan, data menunjuk dua titik tunggu di tepi rentang: "
            f"area beli bawah di {_fmt_px(q['p10'])} – {_fmt_px(low_zone)} dan area jual atas di "
            f"{_fmt_px(high_zone)} – {_fmt_px(q['p90'])}. "
            f"Arah baru condong tegas bila salah satu batas ±{target_band*100:.2f}% ({_fmt_px(q['stopDown'])} atau {_fmt_px(q['targetUp'])}) "
            f"pecah dibarengi dominasi taker di atas 58%."
        )
        key_levels = {
            "waitLabel": "Titik Tunggu Bawah (Area Beli Data)",
            "waitRange": f"{_fmt_px(q['p10'])} – {_fmt_px(low_zone)}",
            "targetLabel": "Titik Tunggu Atas (Area Jual Data)",
            "targetRange": f"{_fmt_px(high_zone)} – {_fmt_px(q['p90'])}",
            "triggerLabel": "Harga Tengah Wajar (VWAP)",
            "triggerPrice": _fmt_px(eq_price),
            "invalidationLabel": "Batas Pecah Rentang",
            "invalidationPrice": f"{_fmt_px(q['stopDown'])} / {_fmt_px(q['targetUp'])}",
        }

    # Extra concise forensic notes (spoof wall filtered, BTC leader conflict, WF brake)
    extra_notes: list[str] = []
    if spoof_ws:
        sw = spoof_ws[0]
        side_lbl = "jual (Ask)" if sw["side"] == "ASK" else "beli (Bid)"
        extra_notes.append(
            f"Filter Anti-Spoof e^(−λd): terdeteksi tembok {side_lbl} jauh {_fmt_qty(sw['qty'])} {base} di "
            f"{_fmt_px(sw['price'])} (jarak {sw['distPct']:.2f}%, {sw['sharePct']:.0f}% sisi) — "
            f"{int(sw['cutPct'])}% volumenya dibuang dari hitungan karena pola pancingan."
        )
    if absorption["side"] != "NONE":
        s_txt = "beli pasif di bawah" if absorption["side"] == "BUY" else "jual pasif di atas"
        extra_notes.append(
            f"Jejak Iceberg 15m ({absorption['index']*100:.0f}%): sekitar {absorption['absorbedPct']:.0f}% "
            f"dorongan transaksi pasar terserap antrean {s_txt} (efisiensi gerak harga cuma {absorption['efficiencyPct']:.0f}%)."
        )
    if btc_guard and btc_guard.get("conflict"):
        extra_notes.append(btc_guard["note"])
    if wf_brake and wf_brake.get("active"):
        extra_notes.append(wf_brake["note"])

    return {
        "headline": headline,
        "scoreSummary": f"{len(pos_comps)} Sinyal Beli · {len(neg_comps)} Sinyal Jual · {len(neu_comps)} Datar",
        "bullCount": len(pos_comps),
        "bearCount": len(neg_comps),
        "neutralCount": len(neu_comps),
        "totalComponents": n_total,
        "mainThesis": main_thesis,
        "counterAndWait": counter_text,
        "extraNotes": extra_notes,
        "keyLevels": key_levels,
    }


def forecast_fp_live(
    candles_5m: list[dict[str, Any]],
    *,
    symbol: str = "BTCUSDT",
    horizon_min: float = 30.0,
    target_band: Optional[float] = None,
    candles_1m: Optional[list[dict[str, Any]]] = None,
    book: Optional[dict[str, Any]] = None,
    btc_context: Optional[dict[str, float]] = None,
    wf_brake_factor: float = 1.0,
    wf_brake_note: Optional[str] = None,
    drop_unclosed_last: bool = False,
    include_curve: bool = True,
    include_first_passage: bool = True,
    n_grid: int = 191,
) -> dict[str, Any]:
    """Run upgraded multi-timescale FP Live forecast for any horizon in [5, 120] minutes."""
    rows5 = fp_v1._sanitize_candles(candles_5m)
    if len(rows5) < MIN_5M_BARS:
        return {"ok": False, "reason": f"Data candle 5m belum cukup ({len(rows5)} < {MIN_5M_BARS})."}

    rows1 = fp_v1._sanitize_candles(candles_1m) if candles_1m else []
    closed5 = rows5[:-1] if (drop_unclosed_last and len(rows5) > MIN_5M_BARS) else rows5
    closed1 = rows1[:-1] if (drop_unclosed_last and len(rows1) > 25) else rows1

    h_min = clamp(_f(horizon_min, 30.0), 5.0, 120.0)
    h_int = int(round(h_min))

    # Current spot price preference: live book mid -> 1m latest close -> 5m latest close
    price_now = float(rows5[-1]["close"])
    if rows1 and rows1[-1]["close"] > 0:
        price_now = float(rows1[-1]["close"])
    if book and book.get("live") and _f(book.get("midPrice"), 0.0) > 0:
        price_now = float(book["midPrice"])

    # --- A. Dual-Resolution Mean-Reverting Volatility D(x, t) ---
    closes5 = [c["close"] for c in closed5]
    rets5 = [math.log(closes5[i] / closes5[i - 1]) for i in range(1, len(closes5))]
    hist5 = rets5[-min(len(rets5), 72) :]

    ewma_v5 = hist5[0] ** 2
    for r in hist5[1:]:
        ewma_v5 = 0.92 * ewma_v5 + 0.08 * (r * r)
    park_k = 1.0 / (4.0 * math.log(2.0))
    p_num, p_den = 0.0, 0.0
    for age, c in enumerate(reversed(closed5[-24:])):
        w = math.exp(-0.10 * age)
        p_num += w * park_k * (math.log(max(c["high"], EPS) / max(c["low"], EPS)) ** 2)
        p_den += w
    v5_slow = max(0.65 * ewma_v5 + 0.35 * (p_num / max(p_den, EPS)), 0.85 * ewma_v5, 1e-10)
    var1_slow = v5_slow / 5.0  # structural per-minute variance

    # Fast 1m variance (captures sudden bursts or tight volatility squeezes)
    if len(closed1) >= 20:
        c1 = [c["close"] for c in closed1[-60:]]
        r1 = [math.log(c1[i] / c1[i - 1]) for i in range(1, len(c1))]
        ewma_v1 = r1[0] ** 2
        for r in r1[1:]:
            ewma_v1 = 0.93 * ewma_v1 + 0.07 * (r * r)
        pk1 = mean(
            park_k * (math.log(max(c["high"], EPS) / max(c["low"], EPS)) ** 2) for c in closed1[-20:]
        )
        var1_fast = clamp(0.65 * ewma_v1 + 0.35 * pk1, 0.30 * var1_slow, 3.20 * var1_slow)
    else:
        r1 = []
        var1_fast = var1_slow

    tau_vol = 18.0
    # Integrated average variance over [0, H]
    avg_weight_fast = (tau_vol / h_min) * (1.0 - math.exp(-h_min / tau_vol))
    var1_eff = var1_slow + (var1_fast - var1_slow) * avg_weight_fast
    sigma_1m = math.sqrt(max(var1_eff, 1e-11))
    sigma_5m = math.sqrt(max(v5_slow, 1e-10))
    sigma_h = sigma_1m * math.sqrt(h_min)

    auto_band = clamp(0.85 * sigma_h, 0.0012, 0.06)
    band = (
        clamp(_f(target_band, auto_band), 0.001, 0.10)
        if (target_band is not None and _f(target_band, 0.0) > 0)
        else auto_band
    )

    # Fat-tail kurtosis + asymmetric crypto downside skew
    mu_h = mean(hist5)
    std_h = math.sqrt(max(mean((r - mu_h) ** 2 for r in hist5), 1e-12))
    kurt = mean(((r - mu_h) / std_h) ** 4 for r in hist5)
    tail_eta = clamp(0.075 * max(0.0, kurt - 3.0), 0.05, 0.55)
    neg_sq = [r * r for r in hist5 if r < 0]
    pos_sq = [r * r for r in hist5 if r > 0]
    down_asym = (
        clamp((mean(neg_sq) / max(mean(pos_sq), EPS)) - 1.0, 0.0, 0.60)
        if (neg_sq and pos_sq)
        else 0.0
    )

    # --- B. Regime Detector (Uncentered Variance Ratio + Kaufman Efficiency Ratio) ---
    tail5 = rets5[-min(len(rets5), 36) :]
    vr5 = compute_variance_ratio(tail5, q=4)
    vr1 = compute_variance_ratio(r1, q=5) if len(r1) >= 25 else vr5
    er_2h = compute_efficiency_ratio(rets5[-min(len(rets5), 24) :])
    vr = 0.65 * vr5 + 0.35 * vr1
    if vr >= 1.06 or er_2h >= 0.28:
        regime_code, regime_label = "TREN_LANJUT", "Tren Berlanjut"
    elif vr <= 0.86 and er_2h <= 0.20:
        regime_code, regime_label = "BOLAK_BALIK", "Pantulan Bolak-Balik (Sideways)"
    else:
        regime_code, regime_label = "TRANSISI", "Transisi / Campuran"

    # --- C. Multi-Timescale Real Taker CVD (5m, 15m, 30m, 2h) + Participation ---
    if len(rows1) >= 15:
        cvd_5m, buy_5m, _ = _exp_cvd(rows1[-5:], 0.18)
        cvd_15m, buy_15m, _ = _exp_cvd(rows1[-15:], 0.08)
    else:
        cvd_5m, buy_5m, _ = _exp_cvd(rows5[-1:], 0.0)
        cvd_15m, buy_15m, _ = _exp_cvd(rows5[-3:], 0.20)

    cvd_30m, buy_30m, _ = _exp_cvd(rows5[-6:], 0.14)
    cvd_2h, buy_2h, _ = _exp_cvd(closed5[-24:], 0.045)
    cvd_accel = cvd_15m - cvd_2h

    # Count how many of {5m, 15m, 30m, 2h} agree in direction
    win_vals = [cvd_5m, cvd_15m, cvd_30m, cvd_2h]
    pos_wins = sum(1 for v in win_vals if v > 0.03)
    neg_wins = sum(1 for v in win_vals if v < -0.03)
    flow_aligned = (pos_wins >= 3) or (neg_wins >= 3)

    tr_rec = mean(c["trades"] for c in closed5[-6:]) if closed5[-6:] else 1.0
    tr_base = mean(c["trades"] for c in closed5[-30:-6]) if len(closed5) >= 30 else tr_rec
    part_ratio = clamp(tr_rec / max(tr_base, EPS), 0.3, 4.0)
    part_w = clamp(0.75 + 0.28 * part_ratio, 0.58, 1.28)

    # --- D. 15m Passive Iceberg Absorption Guard ---
    rec3 = closed5[-3:]
    ret_15m = math.log(max(rec3[-1]["close"], EPS) / max(rec3[0]["open"], EPS))
    sig_15m = max(sigma_5m * math.sqrt(3.0), EPS)
    rel_vol = mean(c["volume"] for c in rec3) / max(mean(c["volume"] for c in closed5[-24:]), EPS)

    abs_idx = 0.0
    abs_side = "NONE"
    eff_pct = 100.0
    if abs(cvd_15m) >= 0.14 and rel_vol >= 0.95:
        f_dir = 1.0 if cvd_15m > 0 else -1.0
        expected_mv = abs(cvd_15m) * 1.15 * sig_15m
        prog = clamp((f_dir * ret_15m) / max(expected_mv, EPS), 0.0, 1.0)
        eff_pct = prog * 100.0
        strength = clamp((abs(cvd_15m) - 0.14) / 0.32, 0.0, 1.0) * clamp(rel_vol / 1.25, 0.70, 1.25)
        abs_idx = clamp(strength * (1.0 - prog), 0.0, 1.0)
        if abs_idx >= 0.38:
            abs_side = "BUY" if cvd_15m < 0 else "SELL"
    abs_rebound = 0.11 * abs_idx if abs_side == "BUY" else (-0.11 * abs_idx if abs_side == "SELL" else 0.0)

    # --- E. Distance-Decayed Session VWAP e^(-λd) + Stoikov Micro-Price ---
    lam_vwap = clamp(1.0 / max(1.6 * sigma_h, 0.004), 35.0, 380.0)
    v_num = v_den = 0.0
    for age, c in enumerate(reversed(closed5[-min(48, len(closed5)) :])):
        typ = (c["high"] + c["low"] + c["close"]) / 3.0
        dist = abs(typ - price_now) / max(price_now, EPS)
        w = max(c["volume"], EPS) * math.exp(-min(40.0, lam_vwap * dist)) * math.exp(-0.032 * age)
        v_num += typ * w
        v_den += w
    vwap_decay = (v_num / v_den) if v_den > EPS else price_now

    hi3 = max(c["high"] for c in rows5[-3:])
    lo3 = min(c["low"] for c in rows5[-3:])
    imb_pos = clamp((price_now - lo3) / max(hi3 - lo3, EPS), 0.0, 1.0)
    imb_micro = clamp(0.60 * buy_15m + 0.40 * imb_pos, 0.05, 0.95)
    micro_px = lo3 * (1.0 - imb_micro) + hi3 * imb_micro
    if book and _f(book.get("microPrice"), 0.0) > 0:
        micro_px = 0.60 * float(book["microPrice"]) + 0.40 * micro_px

    # In choppy markets, VWAP dominates equilibrium; in trending, blend micro-price
    vwap_w = 0.80 if regime_code == "BOLAK_BALIK" else 0.65
    eq_price = vwap_w * vwap_decay + (1.0 - vwap_w) * micro_px
    x_eq = clamp(math.log(max(eq_price, EPS) / price_now), -2.6 * sigma_h, 2.6 * sigma_h)
    vwap_gap_pct = math.log(price_now / max(vwap_decay, EPS)) * 100.0
    vwap_z = math.log(price_now / max(vwap_decay, EPS)) / max(sigma_h, EPS)

    # --- F. Structure Trend Slope + Order-Book Wall Friction ---
    ema_fast = _ema(closes5[-36:], 6)
    ema_slow = _ema(closes5[-48:], 24)
    trend_z = clamp(math.log(max(ema_fast, EPS) / max(ema_slow, EPS)) / max(sigma_h, EPS), -2.5, 2.5)
    overext_damp = 0.40 if abs(vwap_z) > 2.1 else (0.72 if abs(vwap_z) > 1.5 else 1.0)

    # Regime-adaptive weighting (validated on 792-sample walk-forward benchmark!)
    if regime_code == "BOLAK_BALIK":
        momentum_mult = 0.28
        kappa = 0.022 + 0.012 * abs_idx
    elif regime_code == "TREN_LANJUT":
        momentum_mult = 1.15 if flow_aligned else 0.85
        kappa = 0.005 + 0.010 * abs_idx
    else:
        momentum_mult = 0.92 if flow_aligned else 0.58
        kappa = 0.012 + 0.011 * abs_idx
    if abs(vwap_z) > 1.9:
        kappa *= 1.35

    # Proper non-canceling multi-window CVD blend (15m + 30m + 2h + gentle acceleration)
    cvd_fast_sig = (0.45 * cvd_5m + 0.55 * cvd_15m) * (1.0 - 0.65 * abs_idx) * part_w
    cvd_med_sig = (0.48 * cvd_30m + 0.34 * cvd_2h + 0.18 * clamp(cvd_accel, -0.6, 0.6)) * (
        1.0 - 0.60 * abs_idx
    ) * part_w

    mu_fast = clamp(0.14 * cvd_fast_sig * momentum_mult, -0.18, 0.18) * sigma_1m
    mu_cvd = clamp((0.68 * cvd_med_sig * momentum_mult + abs_rebound), -0.25, 0.25) * sigma_1m
    mu_struct = clamp(0.08 * trend_z * overext_damp * momentum_mult, -0.14, 0.14) * sigma_1m

    # Order-book live drift + close wall overhead/floor effect
    mu_ob = 0.0
    tau_ob = 2.5
    spoof_flag = False
    wall_bias = 0.0
    if book and isinstance(book, dict):
        ob_tfi = clamp(_f(book.get("tfi"), 0.0), -1.0, 1.0)
        ob_obi = clamp(_f(book.get("weightedObi"), 0.0), -1.0, 1.0)
        ob_spoof = clamp(_f(book.get("spoofIndex"), 0.0), 0.0, 1.0)
        ob_abs = clamp(_f(book.get("absorptionIndex"), 0.0), 0.0, 1.0)
        acc_m = clamp(_f(book.get("accumulatedMin"), 0.25), 0.1, 30.0)
        tau_ob = clamp(2.0 + 0.70 * acc_m, 2.0, 12.0)
        spoof_flag = ob_spoof >= 0.35 or len(book.get("spoofWalls") or []) > 0

        aw = book.get("activeAskWall")
        bw = book.get("activeBidWall")
        if aw and aw["distPct"] <= 0.25 and aw["sharePct"] >= 18.0:
            wall_bias -= clamp((aw["sharePct"] - 15.0) / 60.0, 0.0, 0.35) * aw["decayWeight"]
        if bw and bw["distPct"] <= 0.25 and bw["sharePct"] >= 18.0:
            wall_bias += clamp((bw["sharePct"] - 15.0) / 60.0, 0.0, 0.35) * bw["decayWeight"]

        if book.get("live"):
            ob_raw = (
                0.18 * ob_tfi * (1.0 - 0.65 * ob_abs)
                + 0.08 * ob_obi * (1.0 - 0.70 * ob_spoof)
                + 0.07 * wall_bias
            )
            mu_ob = clamp(ob_raw, -0.22, 0.22) * sigma_1m

    # Optional BTC leader guard for altcoins
    btc_guard: Optional[dict[str, Any]] = None
    btc_damp = 1.0
    raw_dir_hint = mu_fast + mu_cvd + mu_struct + 0.5 * kappa * x_eq
    if btc_context and symbol.upper() != "BTCUSDT":
        b15 = _f(btc_context.get("cvd15m"), 0.0)
        b30 = _f(btc_context.get("cvd30m"), 0.0)
        b_chg = _f(btc_context.get("ret30mPct"), 0.0)
        conflict = False
        if raw_dir_hint > 0 and b15 <= -0.18 and b30 <= -0.14:
            btc_damp = 0.62
            conflict = True
            note = (
                f"Rem Induk BTC aktif: koin ini ada dorongan beli, namun BTC sedang ditekan jual "
                f"(CVD 15m {b15*100:+.0f}%, gerak 30m {b_chg:+.2f}%) sehingga dorongan naik dipangkas 38%."
            )
        elif raw_dir_hint < 0 and b15 >= 0.18 and b30 >= 0.14:
            btc_damp = 0.62
            conflict = True
            note = (
                f"Rem Induk BTC aktif: koin ini tertekan jual, namun BTC sedang ditarik beli kuat "
                f"(CVD 15m {b15*100:+.0f}%, gerak 30m {b_chg:+.2f}%) sehingga tekanan turun diredam 38%."
            )
        else:
            note = f"Arus BTC 15m {b15*100:+.0f}% (30m {b_chg:+.2f}%) — tidak berlawanan."
        btc_guard = {"cvd15m": round(b15, 3), "cvd30m": round(b30, 3), "ret30mPct": round(b_chg, 2), "conflict": conflict, "note": note}

    # Combine adaptive walk-forward reliability damping + BTC leader guard
    cal_mult = clamp(wf_brake_factor, 0.50, 1.0) * btc_damp
    mu_ob *= cal_mult
    mu_fast *= cal_mult
    mu_cvd *= cal_mult
    mu_struct *= cal_mult

    tau_fast = 8.0
    tau_cvd = 24.0
    tau_struct = 48.0
    scale_x = max(sigma_h, band)

    def mu_pde(x: float, t: float) -> float:
        return (
            mu_ob * math.exp(-t / tau_ob)
            + mu_fast * math.exp(-t / tau_fast)
            + mu_cvd * math.exp(-t / tau_cvd)
            + mu_struct * math.exp(-t / tau_struct)
            - kappa * (x - x_eq)
        )

    def diff_pde(x: float, t: float) -> float:
        d_t = var1_slow + (var1_fast - var1_slow) * math.exp(-t / tau_vol)
        u = x / scale_x
        return max(d_t, 1e-12) * (1.0 + down_asym * max(0.0, -u) + tail_eta * min(u * u, 16.0))

    drift_reach = (
        abs(mu_ob) * min(h_min, tau_ob)
        + abs(mu_fast) * min(h_min, tau_fast)
        + abs(mu_cvd) * min(h_min, tau_cvd)
        + abs(mu_struct) * h_min
    )
    domain = max(6.5 * scale_x + drift_reach + abs(x_eq), 4.5 * band)
    n_steps = max(20, min(64, int(round(h_min * 1.35)))) if include_curve else 22

    sol = fp_v1.solve_fokker_planck(
        mu_pde,
        diff_pde,
        x_min=-domain,
        x_max=domain,
        stable_band=band,
        horizon=h_min,
        n_grid=n_grid,
        n_steps=n_steps,
        snapshot_count=6,
    )
    stats = fp_v2._directional_and_quantile_stats(sol, price_now)

    # Exact absorbing-barrier first-passage probabilities on [-band, +band]
    if include_first_passage:
        first_passage = solve_absorbing_first_passage(
            mu_pde,
            diff_pde,
            band=band,
            horizon=h_min,
            n_grid=95 if include_curve else 65,
            n_steps=34 if include_curve else 22,
        )
    else:
        first_passage = {
            "pHitTargetFirst": stats["pTouchTargetFirst"],
            "pHitStopFirst": stats["pTouchStopFirst"],
            "pNeitherYet": 0.0,
            "condTargetVsStop": stats["pTouchTargetFirst"],
        }

    strict_mode = (regime_code != "TREN_LANJUT") or (bool(book) and _f((book or {}).get("spoofIndex")) >= 0.35)
    call = _classify_call(stats["pUp"], strict=strict_mode)

    # Side-by-side untouched v1-style baseline for consensus confirmation
    v1_res = fp_v2._solve_intraday_v1_baseline(
        closed5, price_now, h_min, band, include_curve=include_curve, n_grid=151 if include_curve else 101
    )
    v1_short = v1_res["call"]["short"]
    models_agree = call["short"] == v1_short
    both_active_agree = models_agree and call["short"] in {"NAIK", "TURUN"}
    consensus_label = (
        f"v1 & v2 Sepakat {call['short']}"
        if both_active_agree
        else ("v1 & v2 Sama-Sama Netral" if models_agree else f"v1 {v1_short} · FP Live {call['short']}")
    )

    # --- G. Explicit 5-Component Scorecard ---
    def _stance(sc: float, thr: float = 0.14) -> str:
        return "POSITIF" if sc >= thr else ("NEGATIF" if sc <= -thr else "NETRAL")

    # 1. Fast Taker Flow (5m & 15m)
    s1 = clamp((0.45 * cvd_5m + 0.55 * cvd_15m) / 0.45, -1.0, 1.0)
    st1 = _stance(s1)
    b1 = (
        f"arus beli cepat mendominasi {buy_15m*100:.0f}% di 15m (5m {buy_5m*100:.0f}%, ramai {part_ratio:.2f}×)"
        if st1 == "POSITIF"
        else (
            f"buangan jual cepat {100 - buy_15m*100:.0f}% di 15m (5m {100 - buy_5m*100:.0f}%, ramai {part_ratio:.2f}×)"
            if st1 == "NEGATIF"
            else f"arus cepat 15m imbang (beli {buy_15m*100:.0f}%)"
        )
    )

    # 2. Medium Taker Flow (30m & 2h + acceleration)
    s2 = clamp((0.50 * cvd_30m + 0.35 * cvd_2h + 0.25 * cvd_accel) / 0.40, -1.0, 1.0)
    st2 = _stance(s2)
    b2 = (
        f"akumulasi beli 30m ({buy_30m*100:.0f}%) & 2j ({buy_2h*100:.0f}%) searah (akselerasi {cvd_accel*100:+.0f}%)"
        if st2 == "POSITIF"
        else (
            f"distribusi jual 30m ({100 - buy_30m*100:.0f}%) & 2j ({100 - buy_2h*100:.0f}%) menekan (akselerasi {cvd_accel*100:+.0f}%)"
            if st2 == "NEGATIF"
            else f"arus 30m ({buy_30m*100:.0f}% beli) vs 2j ({buy_2h*100:.0f}% beli) masih netral"
        )
    )

    # 3. Passive Iceberg Absorption (15m)
    s3 = abs_idx if abs_side == "BUY" else (-abs_idx if abs_side == "SELL" else 0.0)
    st3 = _stance(s3, 0.35)
    b3 = (
        f"penyerapan beli pasif menahan ~{100 - eff_pct:.0f}% tekanan jual 15m"
        if st3 == "POSITIF"
        else (
            f"tembok jual pasif menahan ~{100 - eff_pct:.0f}% dorongan beli 15m"
            if st3 == "NEGATIF"
            else "tidak ada anomali penahanan iceberg"
        )
    )

    # 4. Regime Pattern (VR) + VWAP Valuation Position
    if regime_code == "BOLAK_BALIK" or abs(vwap_z) >= 1.35:
        # Mean-reversion dominates: above VWAP is negative (overpriced), below VWAP is positive (discount)
        s4 = clamp(-vwap_z / 2.0, -1.0, 1.0)
        st4 = _stance(s4, 0.18)
        b4 = (
            f"harga {abs(vwap_gap_pct):.2f}% di bawah harga wajar VWAP {_fmt_px(vwap_decay)} (potensi pantul naik)"
            if st4 == "POSITIF"
            else (
                f"harga sudah +{abs(vwap_gap_pct):.2f}% di atas harga wajar VWAP {_fmt_px(vwap_decay)} (rawan tarik turun)"
                if st4 == "NEGATIF"
                else f"harga menempel di garis wajar VWAP {_fmt_px(vwap_decay)}"
            )
        )
    else:
        s4 = clamp(0.65 * (trend_z / 1.8) + 0.35 * (x_eq / max(sigma_h, EPS)), -1.0, 1.0)
        st4 = _stance(s4, 0.16)
        b4 = (
            f"struktur tren EMA naik ({trend_z:+.1f}σ, VR {vr:.2f}) dengan harga wajar {_fmt_px(vwap_decay)}"
            if st4 == "POSITIF"
            else (
                f"struktur tren EMA turun ({trend_z:+.1f}σ, VR {vr:.2f}) di bawah VWAP {_fmt_px(vwap_decay)}"
                if st4 == "NEGATIF"
                else f"struktur EMA mendatar (VR {vr:.2f}) di sekitar VWAP {_fmt_px(vwap_decay)}"
            )
        )

    # 5. Live Order-Book Walls & Decay OBI
    if book and isinstance(book, dict):
        w_obi = _f(book.get("weightedObi"), 0.0)
        ob_t = _f(book.get("tfi"), 0.0)
        s5 = clamp(0.50 * w_obi + 0.35 * ob_t + 0.55 * wall_bias, -1.0, 1.0)
        st5 = _stance(s5, 0.14)
        bw = book.get("activeBidWall")
        aw = book.get("activeAskWall")
        if st5 == "POSITIF":
            b5 = (
                f"order book condong beli (OBI e^−λd {w_obi*100:+.0f}%)"
                + (f", ditopang tembok bid {_fmt_px(bw['price'])}" if bw else "")
            )
        elif st5 == "NEGATIF":
            b5 = (
                f"order book berat di sisi jual (OBI e^−λd {w_obi*100:+.0f}%)"
                + (f", terganjal tembok ask {_fmt_px(aw['price'])}" if aw else "")
            )
        else:
            b5 = f"antrean bid vs ask seimbang (OBI e^−λd {w_obi*100:+.0f}%)"
        d5 = f"OBI {w_obi*100:+.0f}% · Micro {_f(book.get('microShiftBps')):+.1f} bps"
    else:
        s5, st5, b5, d5 = 0.0, "NETRAL", "data order book tidak aktif", "—"

    components = [
        {
            "id": "fast_cvd",
            "name": "1. Arus Taker Cepat (5m & 15m)",
            "score": round(s1, 3),
            "stance": st1,
            "metric": f"Beli 5m {buy_5m*100:.0f}% · 15m {buy_15m*100:.0f}%",
            "brief": b1,
        },
        {
            "id": "med_cvd",
            "name": "2. Arus Menengah & Akselerasi (30m–2j)",
            "score": round(s2, 3),
            "stance": st2,
            "metric": f"Beli 30m {buy_30m*100:.0f}% · Aksel {cvd_accel*100:+.0f}%",
            "brief": b2,
        },
        {
            "id": "absorption",
            "name": "3. Deteksi Penyerapan Iceberg (15m)",
            "score": round(s3, 3),
            "stance": st3,
            "metric": (
                "Normal"
                if abs_side == "NONE"
                else f"{'Serap Beli' if abs_side == 'BUY' else 'Tahan Jual'} {abs_idx*100:.0f}%"
            ),
            "brief": b3,
        },
        {
            "id": "regime_vwap",
            "name": "4. Pola Gerak (VR) & Harga Wajar VWAP",
            "score": round(s4, 3),
            "stance": st4,
            "metric": f"VR {vr:.2f} · Gap VWAP {vwap_gap_pct:+.2f}%",
            "brief": b4,
        },
        {
            "id": "orderbook",
            "name": "5. Peta Tembok Order Book (e^−λd)",
            "score": round(s5, 3),
            "stance": st5,
            "metric": d5,
            "brief": b5,
        },
    ]

    regime_info = {
        "code": regime_code,
        "label": regime_label,
        "vr": round(vr, 3),
        "flowAligned": flow_aligned,
        "volRatioFastSlow": round(math.sqrt(var1_fast / max(var1_slow, EPS)), 2),
    }
    absorption_info = {
        "side": abs_side,
        "index": round(abs_idx, 3),
        "efficiencyPct": round(eff_pct, 1),
        "absorbedPct": round(max(0.0, 100.0 - eff_pct), 1),
    }
    wf_brake_info = (
        {"active": wf_brake_factor < 0.99, "factor": round(wf_brake_factor, 2), "note": wf_brake_note or ""}
        if wf_brake_factor < 0.99
        else None
    )

    out: dict[str, Any] = {
        "ok": True,
        "horizonMin": h_int,
        "price": round(price_now, 8),
        "fairPrice": round(eq_price, 8),
        "vwapPrice": round(vwap_decay, 8),
        "microPrice": round(micro_px, 8),
        "eqShiftPct": round(x_eq * 100.0, 3),
        "targetBand": round(band, 6),
        "autoBand": round(auto_band, 6),
        "call": call,
        "stats": stats,
        "firstPassage": first_passage,
        "regime": regime_info,
        "absorption": absorption_info,
        "components": components,
        "v1Baseline": {
            "call": v1_res["call"],
            "pUp": v1_res["stats"]["pUp"],
            "pDown": v1_res["stats"]["pDown"],
            "expectedMovePct": v1_res["stats"]["expectedMovePct"],
        },
        "consensus": {
            "modelsAgree": models_agree,
            "bothActiveAgree": both_active_agree,
            "label": consensus_label,
        },
        "signals": {
            "cvd5m": round(cvd_5m, 4),
            "cvd15m": round(cvd_15m, 4),
            "cvd30m": round(cvd_30m, 4),
            "cvd2h": round(cvd_2h, 4),
            "cvdAccel": round(cvd_accel, 4),
            "buyShare5m": round(buy_5m, 4),
            "buyShare15m": round(buy_15m, 4),
            "buyShare30m": round(buy_30m, 4),
            "buyShare2h": round(buy_2h, 4),
            "participationRatio": round(part_ratio, 2),
            "sigma1mBps": round(sigma_1m * 1e4, 2),
            "sigmaHorizonPct": round(sigma_h * 100.0, 3),
            "decayLambdaVwap": round(lam_vwap, 1),
            "spoofFiltered": spoof_flag,
        },
        "btcGuard": btc_guard,
        "wfBrake": wf_brake_info,
    }

    if include_curve:
        out["narrative"] = _build_rounded_narrative(
            symbol=symbol,
            horizon_min=h_int,
            price_now=price_now,
            call=call,
            stats=stats,
            first_passage=first_passage,
            target_band=band,
            components=components,
            regime_info=regime_info,
            eq_price=eq_price,
            vwap_decay=vwap_decay,
            book=book,
            absorption=absorption_info,
            btc_guard=btc_guard,
            wf_brake=wf_brake_info,
        )
        v2_curve = fp_v1.export_chart_curve(sol)
        v1_curve = v1_res.get("curve") or []

        def _interp(pts: list[dict[str, float]], xq: float) -> float:
            if not pts or xq <= pts[0]["xPct"] or xq >= pts[-1]["xPct"]:
                return 0.0
            lo, hi = 0, len(pts) - 1
            while hi - lo > 1:
                md = (lo + hi) // 2
                if pts[md]["xPct"] <= xq:
                    lo = md
                else:
                    hi = md
            x0, x1 = pts[lo]["xPct"], pts[hi]["xPct"]
            y0, y1 = pts[lo]["density"], pts[hi]["density"]
            fr = (xq - x0) / max(x1 - x0, EPS)
            return max(0.0, y0 + fr * (y1 - y0))

        overlay: list[dict[str, float]] = []
        if v2_curve:
            lo_x = min(v2_curve[0]["xPct"], v1_curve[0]["xPct"] if v1_curve else v2_curve[0]["xPct"])
            hi_x = max(v2_curve[-1]["xPct"], v1_curve[-1]["xPct"] if v1_curve else v2_curve[-1]["xPct"])
            n_pts = 110
            for k in range(n_pts + 1):
                xp = lo_x + (hi_x - lo_x) * (k / n_pts)
                yv2 = _interp(v2_curve, xp)
                yv1 = _interp(v1_curve, xp) if v1_curve else yv2
                overlay.append(
                    {
                        "xPct": round(xp, 4),
                        "v2": round(yv2, 5),
                        "v1": round(yv1, 5),
                        "common": round(min(yv1, yv2), 5),
                    }
                )
        if len(overlay) >= 2:
            ov = sum(
                0.5 * (overlay[i - 1]["common"] + overlay[i]["common"])
                * ((overlay[i]["xPct"] - overlay[i - 1]["xPct"]) * 0.01)
                for i in range(1, len(overlay))
            )
            out["consensus"]["pdfOverlap"] = round(clamp(ov, 0.0, 1.0), 4)
        else:
            out["consensus"]["pdfOverlap"] = 1.0
        out["overlayCurve"] = overlay

        cum_d = 0.0
        cvd_series = []
        for c in rows5[-24:]:
            d = 2.0 * c["takerBuyBase"] - c["volume"]
            cum_d += d
            cvd_series.append(
                {
                    "t": int(c["date"]),
                    "price": round(c["close"], 8),
                    "delta": round(d, 4),
                    "cvd": round(cum_d, 4),
                    "buyShare": round((c["takerBuyBase"] / c["volume"]) if c["volume"] > EPS else 0.5, 4),
                }
            )
        out["cvdSeries"] = cvd_series

    return out


# ---------------------------------------------------------------------------
# 5. Strict No-Lookahead Walk-Forward Backtest + Adaptive Brake Calibration
# ---------------------------------------------------------------------------


def backtest_fp_live(
    candles_5m: list[dict[str, Any]],
    *,
    symbol: str = "BTCUSDT",
    horizon_min: int = 30,
    target_band: Optional[float] = None,
    step_bars: int = 3,
) -> dict[str, Any]:
    """Chronological walk-forward directional audit on closed 5m candles (zero lookahead)."""
    rows = fp_v1._sanitize_candles(candles_5m)
    closed = rows[:-1] if len(rows) > MIN_5M_BARS + 10 else rows
    h_bars = max(1, int(round(clamp(horizon_min, 5, 120) / 5.0)))
    h_actual = h_bars * 5
    step = max(1, int(step_bars))

    start_idx = max(MIN_5M_BARS, 56)
    max_anchor = len(closed) - h_bars
    if max_anchor <= start_idx + 4:
        return {"ok": False, "reason": "Histori candle 5m belum cukup untuk audit walk-forward."}

    recs: list[dict[str, Any]] = []
    for anchor in range(start_idx, max_anchor + 1, step):
        past = closed[:anchor]
        entry = past[-1]["close"]
        exit_p = closed[anchor - 1 + h_bars]["close"]
        if entry <= 0 or exit_p <= 0:
            continue
        real_lr = math.log(exit_p / entry)
        y_up = 1.0 if real_lr > 0 else (0.5 if abs(real_lr) <= 1e-12 else 0.0)

        fc = forecast_fp_live(
            past,
            symbol=symbol,
            horizon_min=float(h_actual),
            target_band=target_band,
            include_curve=False,
            include_first_passage=False,
            n_grid=101,
        )
        if not fc.get("ok"):
            continue

        p2 = float(fc["stats"]["pUp"])
        p1 = float(fc["v1Baseline"]["pUp"])
        c2 = fc["call"]["short"]
        c1 = fc["v1Baseline"]["call"]["short"]

        hit2: Optional[bool] = None
        if c2 == "NAIK" and abs(real_lr) > 1e-12:
            hit2 = real_lr > 0
        elif c2 == "TURUN" and abs(real_lr) > 1e-12:
            hit2 = real_lr < 0

        hit1: Optional[bool] = None
        if c1 == "NAIK" and abs(real_lr) > 1e-12:
            hit1 = real_lr > 0
        elif c1 == "TURUN" and abs(real_lr) > 1e-12:
            hit1 = real_lr < 0

        recs.append(
            {
                "t": int(past[-1]["date"]),
                "entryPrice": round(entry, 8),
                "exitPrice": round(exit_p, 8),
                "realizedPct": round((exit_p / entry - 1.0) * 100.0, 3),
                "call": c2,
                "v1Call": c1,
                "pUp": round(p2, 4),
                "hit": hit2,
                "v1Hit": hit1,
                "agreed": (c1 == c2) and (c2 in {"NAIK", "TURUN"}),
                "brier": (p2 - y_up) ** 2,
                "brierV1": (p1 - y_up) ** 2,
                "inBandActual": 1.0 if abs(real_lr) <= float(fc["targetBand"]) else 0.0,
                "inBandPred": float(fc["stats"]["pStayBand"]),
            }
        )

    if not recs:
        return {"ok": False, "reason": "Tidak ada sampel audit valid."}

    act2 = [r for r in recs if r["hit"] is not None]
    act1 = [r for r in recs if r["v1Hit"] is not None]
    agr = [r for r in recs if r["agreed"] and r["hit"] is not None]
    skipped = sum(1 for r in recs if r["call"] == "NETRAL")

    wr2 = round(mean(1.0 if r["hit"] else 0.0 for r in act2), 4) if act2 else None
    wr1 = round(mean(1.0 if r["v1Hit"] else 0.0 for r in act1), 4) if act1 else None
    wra = round(mean(1.0 if r["hit"] else 0.0 for r in agr), 4) if agr else None
    br2 = round(mean(r["brier"] for r in recs), 4)
    br1 = round(mean(r["brierV1"] for r in recs), 4)

    # Adaptive reliability factor from past 20h performance
    brake_factor = 1.0
    brake_note = ""
    if wr2 is not None and len(act2) >= 12 and (wr2 < 0.47 and br2 > 0.258):
        brake_factor = clamp(0.58 + 0.8 * max(0.0, wr2 - 0.35), 0.58, 0.82)
        brake_note = (
            f"Kalibrasi historis aktif ({int((1 - brake_factor)*100)}% redaman): dalam ~20 jam terakhir "
            f"gerak {symbol} cukup acak (win-rate {wr2*100:.0f}% dari {len(act2)} sinyal, Brier {br2:.3f}), "
            f"sehingga ambang sinyal diperketat otomatis."
        )

    return {
        "ok": True,
        "horizonMin": h_actual,
        "totalSamples": len(recs),
        "activeCount": len(act2),
        "activeHits": sum(1 for r in act2 if r["hit"]),
        "neutralSkipped": skipped,
        "winRate": wr2,
        "v1ActiveCount": len(act1),
        "v1WinRate": wr1,
        "agreedCount": len(agr),
        "agreedHits": sum(1 for r in agr if r["hit"]),
        "agreedWinRate": wra,
        "brier": br2,
        "brierV1": br1,
        "bandPredAvg": round(mean(r["inBandPred"] for r in recs), 4),
        "bandActualAvg": round(mean(r["inBandActual"] for r in recs), 4),
        "recommendedBrake": round(brake_factor, 3),
        "brakeNote": brake_note,
        "recentSignals": [
            {
                "t": r["t"],
                "entryPrice": r["entryPrice"],
                "exitPrice": r["exitPrice"],
                "realizedPct": r["realizedPct"],
                "call": r["call"],
                "v1Call": r["v1Call"],
                "pUp": r["pUp"],
                "hit": r["hit"],
            }
            for r in recs[-10:]
        ][::-1],
    }


async def _get_btc_leader_context(
    client: httpx.AsyncClient, *, prefer_seed: bool = False
) -> Optional[dict[str, float]]:
    key = f"btc_ctx:{'seed' if prefer_seed else 'live'}"
    cached = FP_LIVE_BTC_CACHE.get(key)
    if cached is not None:
        return cached
    try:
        k5 = await fp_v2.fetch_intraday_5m_klines(client, "BTCUSDT", limit=72, prefer_seed=prefer_seed)
        rows = fp_v1._sanitize_candles(k5["candles"])
        if len(rows) < 12:
            return None
        cvd15, _, _ = _exp_cvd(rows[-3:], 0.20)
        cvd30, _, _ = _exp_cvd(rows[-6:], 0.14)
        ret30 = (rows[-1]["close"] / max(rows[-7]["close"], EPS) - 1.0) * 100.0
        ctx = {"cvd15m": cvd15, "cvd30m": cvd30, "ret30mPct": ret30}
        FP_LIVE_BTC_CACHE.set(key, ctx)
        return ctx
    except Exception:  # noqa: BLE001
        return None


async def build_fp_live_bundle(
    client: httpx.AsyncClient,
    symbol: str,
    *,
    horizon_min: int = 30,
    target_band: Optional[float] = None,
    prefer_seed: bool = False,
    ws_override: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Assemble full FP Live payload: 5/15/30/60m forecasts + walls + narrative + audit."""
    sym = symbol.upper().strip()
    if not sym.endswith("USDT") and len(sym) <= 10:
        sym = f"{sym}USDT"

    # Fetch 5m klines, 1m klines, and (for altcoins) BTC leader flow concurrently
    tasks: list[Any] = [
        fp_v2.fetch_intraday_5m_klines(client, sym, limit=240, prefer_seed=prefer_seed),
        fetch_1m_klines(client, sym, limit=90, prefer_seed=prefer_seed),
    ]
    need_btc = sym != "BTCUSDT"
    if need_btc:
        tasks.append(_get_btc_leader_context(client, prefer_seed=prefer_seed))

    fetched = await asyncio.gather(*tasks)
    k5_res = fetched[0]
    k1_res = fetched[1]
    btc_ctx = fetched[2] if need_btc else None

    candles_5m = k5_res["candles"]
    candles_1m = (
        k1_res["candles"]
        if (k1_res.get("live") and k1_res.get("candles"))
        else derive_1m_from_5m(candles_5m, 90)
    )
    anchor_px = float(candles_1m[-1]["close"] if candles_1m else candles_5m[-1]["close"])

    # Estimate adaptive distance-decay lambda from recent 5m volatility for 100-level book
    closes_tail = [float(c["close"]) for c in candles_5m[-25:] if _f(c.get("close")) > 0]
    if len(closes_tail) >= 6:
        rs = [abs(math.log(closes_tail[i] / closes_tail[i - 1])) for i in range(1, len(closes_tail))]
        sig5_est = max(mean(rs), 0.0008)
    else:
        sig5_est = 0.0020
    lam_book = clamp(1.0 / max(1.8 * sig5_est, 0.0018), 110.0, 650.0)

    book = await fetch_live_orderbook_walls(
        client, sym, anchor_price=anchor_px, decay_lambda=lam_book, prefer_seed=prefer_seed
    )

    # Fuse browser rolling multi-minute Live WebSocket accumulator when provided
    ws_active = False
    if ws_override and isinstance(ws_override, dict):
        ws_active = True
        book = {
            **book,
            "live": True,
            "source": f"{book['source']}+ws-live",
            "tfi": round(clamp(_f(ws_override.get("tfi"), book["tfi"]), -1.0, 1.0), 4),
            "weightedObi": round(clamp(_f(ws_override.get("obi"), book["weightedObi"]), -1.0, 1.0), 4),
            "spoofIndex": round(clamp(_f(ws_override.get("spoofIndex"), book["spoofIndex"]), 0.0, 1.0), 4),
            "absorptionIndex": round(clamp(_f(ws_override.get("absorptionIndex"), 0.0), 0.0, 1.0), 4),
            "accumulatedMin": round(clamp(_f(ws_override.get("accumulatedMin"), 1.0), 0.1, 60.0), 2),
        }
        ws_px_note = ""
        ws_mid = _f(ws_override.get("midPrice"), 0.0)
        if ws_mid > 0:
            # Guard penyajian: midPrice WS dari browser tidak boleh menyimpang jauh dari harga candle
            if ws_mid <= anchor_px * 0.75 or ws_mid >= anchor_px * 1.25:
                ws_px_note = (
                    f"midPrice WS ({ws_mid:,.4f}) diabaikan karena menyimpang >25% dari harga candle "
                    f"({anchor_px:,.4f})."
                )
            elif ws_mid <= anchor_px * 0.97 or ws_mid >= anchor_px * 1.03:
                ws_px_note = (
                    f"midPrice WS ({ws_mid:,.4f}) berbeda {abs(ws_mid/anchor_px-1.0)*100:.1f}% dari harga candle "
                    f"({anchor_px:,.4f}) — dipakai dengan catatan."
                )
            else:
                book["midPrice"] = round(ws_mid, 8)
        if _f(ws_override.get("microPrice"), 0.0) > 0:
            book["microPrice"] = round(float(ws_override["microPrice"]), 8)
            book["microShiftBps"] = round(
                math.log(max(book["microPrice"], EPS) / max(book["midPrice"], EPS)) * 1e4, 2
            )
        if ws_px_note:
            book["wsPriceNote"] = ws_px_note

    # Walk-forward audit (cached per closed 5m bar so 15s WS updates stay <60ms!)
    last_closed_ts = int(candles_5m[-2]["date"]) if len(candles_5m) >= 2 else 0
    band_key = f"{target_band:.5f}" if target_band is not None else "auto"
    bt_key = f"fplive_bt:{sym}:{int(round(horizon_min))}:{band_key}:{last_closed_ts}"
    backtest = FP_LIVE_BT_CACHE.get(bt_key)
    if backtest is None:
        backtest = backtest_fp_live(
            candles_5m,
            symbol=sym,
            horizon_min=int(round(horizon_min)),
            target_band=target_band,
            step_bars=3,
        )
        FP_LIVE_BT_CACHE.set(bt_key, backtest)

    wf_factor = float(backtest.get("recommendedBrake", 1.0)) if backtest.get("ok") else 1.0
    wf_note = str(backtest.get("brakeNote") or "")

    drop_last = bool(k5_res.get("live"))
    primary = forecast_fp_live(
        candles_5m,
        symbol=sym,
        horizon_min=float(horizon_min),
        target_band=target_band,
        candles_1m=candles_1m,
        book=book,
        btc_context=btc_ctx,
        wf_brake_factor=wf_factor,
        wf_brake_note=wf_note,
        drop_unclosed_last=drop_last,
        include_curve=True,
        include_first_passage=True,
    )
    if not primary.get("ok"):
        return primary

    # 4-Horizon Matrix: 5m · 15m · 30m · 60m
    horizon_matrix: list[dict[str, Any]] = []
    for h_opt in HORIZON_SET:
        sub = (
            primary
            if primary["horizonMin"] == h_opt
            else forecast_fp_live(
                candles_5m,
                symbol=sym,
                horizon_min=float(h_opt),
                target_band=target_band,
                candles_1m=candles_1m,
                book=book,
                btc_context=btc_ctx,
                wf_brake_factor=wf_factor,
                drop_unclosed_last=drop_last,
                include_curve=False,
                include_first_passage=True,
                n_grid=131,
            )
        )
        if sub.get("ok"):
            sq = sub["stats"]["priceQuantiles"]
            horizon_matrix.append(
                {
                    "horizonMin": h_opt,
                    "call": sub["call"],
                    "pUp": sub["stats"]["pUp"],
                    "pDown": sub["stats"]["pDown"],
                    "expectedMovePct": sub["stats"]["expectedMovePct"],
                    "targetBand": sub["targetBand"],
                    "firstPassage": sub["firstPassage"],
                    "rangeLow": sq["p10"],
                    "medianPrice": sq["median"],
                    "rangeHigh": sq["p90"],
                    "consensusLabel": sub["consensus"]["label"],
                    "bothAgree": sub["consensus"]["bothActiveAgree"],
                }
            )

    # Check alignment across 15m & 30m
    m15 = next((m for m in horizon_matrix if m["horizonMin"] == 15), None)
    m30 = next((m for m in horizon_matrix if m["horizonMin"] == 30), None)
    aligned_dir: Optional[str] = None
    if m15 and m30 and m15["call"]["short"] == m30["call"]["short"] and m15["call"]["short"] != "NETRAL":
        aligned_dir = m15["call"]["short"]

    return {
        "ok": True,
        "symbol": sym,
        "baseAsset": sym[:-4] if sym.endswith("USDT") else sym,
        "live": bool(k5_res.get("live")),
        "live1m": bool(k1_res.get("live")),
        "liveBook": bool(book.get("live")),
        "wsActive": ws_active,
        "source5m": k5_res["source"],
        "source1m": k1_res["source"],
        "sourceBook": book["source"],
        "sourceNotes": {
            "k5m": "live" if k5_res.get("live") else "seed",
            "k1m": "live" if k1_res.get("live") else "sintetis-dari-5m",
            "book": "live" if book.get("live") else "seed",
        },
        "warning": " ".join(
            w
            for w in (k5_res.get("warning"), book.get("wsPriceNote"))
            if w
        ) or None,
        "aligned15m30m": aligned_dir,
        "forecast": primary,
        "orderbook": book,
        "horizonMatrix": horizon_matrix,
        "backtest": backtest,
    }

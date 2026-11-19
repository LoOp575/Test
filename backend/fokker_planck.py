"""
Meridian — 1D Itô Fokker–Planck Crypto Distribution & Microstructure Engine.

Isolated, additive module. Never imported by `backend/screening.py` and never
mutates production quality/signal/verdict scores. Uses pure Python stdlib math
so Vercel serverless bundle size and cold-start stay unchanged.

Governing PDE (Kolmogorov forward / Itô Fokker–Planck equation):

    ∂P(x,t)/∂t = -∂/∂x [ μ(x,t) P(x,t) ] + (1/2) ∂²/∂x² [ D(x,t) P(x,t) ]

Rewritten in probability-flux conservation form:

    ∂P/∂t = -∂J/∂x,    J(x,t) = μ_eff(x,t) P - (1/2) D(x,t) ∂P/∂x
    where μ_eff(x,t) = μ(x,t) - (1/2) ∂D(x,t)/∂x

Discretised on a uniform 1D log-return grid x = ln(S_t / S_0) with an implicit
Chang–Cooper (exponentially fitted Bernoulli-flux) finite-volume scheme, O(N)
Thomas tridiagonal solver, and zero-flux boundary conditions J(-L,t) = J(+L,t) = 0.

Numerical guarantees for any Δt > 0, any finite μ(x,t), and any D(x,t) > 0:
  1. Unconditional positivity: P_i(t) >= 0 (strictly diagonally dominant M-matrix).
  2. Exact probability conservation: Σ_i P_i(t) Δx = 1 (telescoping fluxes).
  3. Exact stationary equilibrium for Ornstein–Uhlenbeck and power-law fat-tailed
     (Student-t / Tsallis) diffusions D(x) = D_0 (1 + η (x/s)²).

Honesty note: while the PDE solver is mathematically exact up to grid/step
discretisation, mapping exchange OHLCV or order-book features into μ(x,t) and
D(x,t) is a heuristic model that should be evaluated out-of-sample (see
`backtest_candles_fp`). Research tool, not financial advice.
"""

from __future__ import annotations

import asyncio
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import mean
from typing import Any, Callable, Optional

import httpx

from backend import market_data

EPS = 1e-12
MIN_CANDLES = 61
DEFAULT_WINDOW = 30

# Regime metadata for consistent UI tone & Indonesian labels
CANDLE_REGIME_META: dict[str, dict[str, Any]] = {
    "ACCUMULATION_STABLE": {
        "label": "Akumulasi Stabil",
        "tone": "long",
        "rank": 1,
        "summary": "Distribusi terpusat di zona stabil dengan condong positif dan ekor kiri tipis.",
    },
    "BULLISH_EXPANSION": {
        "label": "Ekspansi Naik",
        "tone": "long",
        "rank": 2,
        "summary": "Massa probabilitas bergeser ke zona upside namun dispersi volatilitas cukup lebar.",
    },
    "SIDEWAYS_CALM": {
        "label": "Sideways Tenang",
        "tone": "neutral",
        "rank": 3,
        "summary": "Probabilitas harga bertahan di zona stabil dominan dengan drift mendekati netral.",
    },
    "HIGH_VOL_DISPERSED": {
        "label": "Dispersi Tinggi",
        "tone": "watch",
        "rank": 4,
        "summary": "Difusi volatilitas melebar ke dua arah; probabilitas bertahan di zona stabil rendah.",
    },
    "DOWNSIDE_SKEW": {
        "label": "Condong Turun",
        "tone": "avoid",
        "rank": 5,
        "summary": "Tekanan jual menggeser kurva ke zona downside dengan asimetri negatif.",
    },
    "TAIL_CRASH_RISK": {
        "label": "Risiko Ekor Crash",
        "tone": "avoid",
        "rank": 6,
        "summary": "Massa probabilitas di ekor kiri ekstrem (<-2x band) tinggi akibat difusi fat-tail & tekanan jual.",
    },
}

MICRO_REGIME_META: dict[str, dict[str, str]] = {
    "STABLE_BALANCED": {
        "label": "Stabil & Seimbang",
        "tone": "long",
        "summary": "Arus transaksi nyata (taker) dan likuiditas dekat mid-price seimbang; Micro-Price stabil di tengah spread.",
    },
    "DISTANT_SPOOF_FILTERED": {
        "label": "Tembok Jauh Dinetralkan",
        "tone": "long",
        "summary": "Tembok besar terdeteksi jauh (>0,5%) dari mid-price namun diredam hampir nol oleh exponential decay; arus nyata tetap stabil.",
    },
    "SPOOF_SELL_WALL": {
        "label": "Indikasi Spoof Jual",
        "tone": "watch",
        "summary": "Tembok ask tebal disertai cancel tinggi tanpa eksekusi market-sell sepadan — tekanan turun bersifat transien.",
    },
    "SPOOF_BUY_WALL": {
        "label": "Indikasi Spoof Beli",
        "tone": "watch",
        "summary": "Tembok bid tebal disertai cancel tinggi tanpa eksekusi market-buy sepadan — dorongan naik rawan semu.",
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

# Short-lived in-memory snapshot buffer to estimate passive cancellations between consecutive REST polls
_PREV_BOOK_CACHE: dict[str, dict[str, Any]] = {}
_PREV_BOOK_TTL_SEC = 90.0
_PREV_BOOK_MAX = 120

fp_cache = market_data.TTLCache(ttl_seconds=45.0)


# ---------------------------------------------------------------------------
# Numeric primitives (pure stdlib)
# ---------------------------------------------------------------------------

def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def sat(x: float) -> float:
    return x / (1.0 + abs(x))


def _finite_float(value: Any, fallback: float = 0.0) -> float:
    try:
        v = float(value)
        if math.isfinite(v):
            return v
    except (TypeError, ValueError):
        pass
    return fallback


def bernoulli(w: float) -> float:
    """Bernoulli function B(w) = w / (exp(w) - 1), C²-smooth and overflow-safe for all w in R."""
    if abs(w) < 1e-4:
        w2 = w * w
        return 1.0 - 0.5 * w + w2 / 12.0 - (w2 * w2) / 720.0
    if w > 50.0:
        return w * math.exp(-w)
    if w < -50.0:
        return -w
    return w / math.expm1(w)


def solve_tridiagonal(
    lower: list[float],
    diag: list[float],
    upper: list[float],
    rhs: list[float],
) -> list[float]:
    """O(N) Thomas algorithm for strictly diagonally dominant tridiagonal system."""
    n = len(diag)
    c_star = [0.0] * n
    d_star = [0.0] * n

    inv0 = 1.0 / diag[0]
    c_star[0] = upper[0] * inv0
    d_star[0] = rhs[0] * inv0

    for i in range(1, n):
        denom = diag[i] - lower[i] * c_star[i - 1]
        inv = 1.0 / (denom if abs(denom) > EPS else EPS)
        c_star[i] = upper[i] * inv if i < n - 1 else 0.0
        d_star[i] = (rhs[i] - lower[i] * d_star[i - 1]) * inv

    out = [0.0] * n
    out[n - 1] = max(0.0, d_star[n - 1])
    for i in range(n - 2, -1, -1):
        out[i] = max(0.0, d_star[i] - c_star[i] * out[i + 1])
    return out


# ---------------------------------------------------------------------------
# Core 1D Itô Fokker–Planck PDE solver
# ---------------------------------------------------------------------------

@dataclass
class DistributionMetrics:
    t: float
    p_stable: float
    p_downside: float
    p_upside: float
    p_tail_crash: float
    p_tail_pump: float
    p_adaptive_1sigma: float
    mean_log_return: float
    expected_return_pct: float
    std_log_return: float
    skewness: float
    kurtosis: float
    shannon_entropy: float
    total_mass: float
    boundary_mass: float

    def to_dict(self) -> dict[str, float]:
        return {
            "t": round(self.t, 4),
            "pStable": round(self.p_stable, 6),
            "pDownside": round(self.p_downside, 6),
            "pUpside": round(self.p_upside, 6),
            "pTailCrash": round(self.p_tail_crash, 6),
            "pTailPump": round(self.p_tail_pump, 6),
            "pAdaptive1Sigma": round(self.p_adaptive_1sigma, 6),
            "meanLogReturn": round(self.mean_log_return, 6),
            "expectedReturnPct": round(self.expected_return_pct, 4),
            "stdLogReturn": round(self.std_log_return, 6),
            "skewness": round(self.skewness, 4),
            "kurtosis": round(self.kurtosis, 4),
            "shannonEntropy": round(self.shannon_entropy, 4),
            "totalMass": round(self.total_mass, 8),
            "boundaryMass": round(self.boundary_mass, 6),
        }


@dataclass
class FokkerPlanckSolution:
    final: DistributionMetrics
    timeline: list[DistributionMetrics]
    grid: list[float]
    pdf: list[float]
    dx: float
    stable_band: float
    horizon: float


def _integrate_interval(grid: list[float], p: list[float], dx: float, lo: float, hi: float) -> float:
    total = 0.0
    half = 0.5 * dx
    for x, dens in zip(grid, p):
        overlap = min(x + half, hi) - max(x - half, lo)
        if overlap > 0.0:
            total += dens * overlap
    return total


def _summarize_pdf(
    grid: list[float],
    p: list[float],
    dx: float,
    stable_band: float,
    t: float,
) -> DistributionMetrics:
    total_mass = sum(p) * dx
    p_stable = _integrate_interval(grid, p, dx, -stable_band, stable_band)
    p_down = _integrate_interval(grid, p, dx, -math.inf, -stable_band)
    p_up = _integrate_interval(grid, p, dx, stable_band, math.inf)
    p_crash = _integrate_interval(grid, p, dx, -math.inf, -2.0 * stable_band)
    p_pump = _integrate_interval(grid, p, dx, 2.0 * stable_band, math.inf)

    mean_x = sum(x * d for x, d in zip(grid, p)) * dx
    exp_ret = sum(math.expm1(clamp(x, -4.0, 4.0)) * d for x, d in zip(grid, p)) * dx
    var_x = max(0.0, sum(((x - mean_x) ** 2) * d for x, d in zip(grid, p)) * dx)
    std_x = math.sqrt(var_x)

    p_adapt = _integrate_interval(grid, p, dx, -std_x, std_x) if std_x > EPS else 1.0

    if std_x > 1e-9:
        skew = sum((((x - mean_x) / std_x) ** 3) * d for x, d in zip(grid, p)) * dx
        kurt = sum((((x - mean_x) / std_x) ** 4) * d for x, d in zip(grid, p)) * dx
    else:
        skew, kurt = 0.0, 3.0

    entropy = -sum(d * math.log(d) for d in p if d > 1e-30) * dx
    edge_count = max(1, len(p) // 50)
    boundary_mass = (sum(p[:edge_count]) + sum(p[-edge_count:])) * dx

    return DistributionMetrics(
        t=t,
        p_stable=clamp(p_stable, 0.0, 1.0),
        p_downside=clamp(p_down, 0.0, 1.0),
        p_upside=clamp(p_up, 0.0, 1.0),
        p_tail_crash=clamp(p_crash, 0.0, 1.0),
        p_tail_pump=clamp(p_pump, 0.0, 1.0),
        p_adaptive_1sigma=clamp(p_adapt, 0.0, 1.0),
        mean_log_return=mean_x,
        expected_return_pct=exp_ret * 100.0,
        std_log_return=std_x,
        skewness=skew,
        kurtosis=kurt,
        shannon_entropy=entropy,
        total_mass=total_mass,
        boundary_mass=clamp(boundary_mass, 0.0, 1.0),
    )


def _interp_pdf(grid: list[float], pdf: list[float], dx: float, x: float) -> float:
    if x <= grid[0]:
        return pdf[0]
    if x >= grid[-1]:
        return pdf[-1]
    pos = (x - grid[0]) / dx
    idx = int(pos)
    if idx >= len(grid) - 1:
        return pdf[-1]
    frac = pos - idx
    return pdf[idx] * (1.0 - frac) + pdf[idx + 1] * frac


def export_chart_curve(sol: FokkerPlanckSolution, n_points: int = 85) -> list[dict[str, Any]]:
    """Build zone-segmented curve points for gap-free frontend area plotting."""
    a = sol.stable_band
    f = sol.final
    span = max(3.2 * a, 3.4 * f.std_log_return + abs(f.mean_log_return))
    view_lim = min(abs(sol.grid[-1]), max(span, 2.4 * a))

    base_xs = [-view_lim + (2.0 * view_lim * i) / max(n_points - 1, 1) for i in range(n_points)]
    anchors = [-2.0 * a, -a, 0.0, a, 2.0 * a]
    merged: list[float] = sorted(
        [x for x in base_xs if all(abs(x - k) > 1e-6 * view_lim for k in anchors)]
        + [k for k in anchors if -view_lim <= k <= view_lim]
    )

    tol = 1e-9 + 1e-6 * a
    points: list[dict[str, Any]] = []
    for x in merged:
        dens = max(0.0, _interp_pdf(sol.grid, sol.pdf, sol.dx, x))
        # Density per 1% move makes the vertical scale readable across both daily & second horizons
        prob_per_pct = dens * 0.01
        val = round(prob_per_pct, 6)
        points.append(
            {
                "x": round(x, 6),
                "xPct": round(x * 100.0, 4),
                "xBps": round(x * 10_000.0, 2),
                "density": round(dens, 6),
                "probPerPct": val,
                "crash": val if x <= -2.0 * a + tol else None,
                "downside": val if (-2.0 * a - tol <= x <= -a + tol) else None,
                "stable": val if (-a - tol <= x <= a + tol) else None,
                "upside": val if (a - tol <= x <= 2.0 * a + tol) else None,
                "pump": val if x >= 2.0 * a - tol else None,
            }
        )
    return points


def solve_fokker_planck(
    mu_fn: Callable[[float, float], float],
    d_fn: Callable[[float, float], float],
    *,
    x_min: float,
    x_max: float,
    stable_band: float,
    horizon: float,
    n_grid: int = 201,
    n_steps: int = 32,
    x0: float = 0.0,
    sigma0: Optional[float] = None,
    snapshot_count: int = 6,
) -> FokkerPlanckSolution:
    """Solve 1D Itô Fokker–Planck PDE via implicit Chang–Cooper finite volume."""
    if n_grid < 21 or n_grid % 2 == 0:
        raise ValueError("n_grid must be an odd integer >= 21")
    if not (math.isfinite(x_min) and math.isfinite(x_max) and x_max > x_min):
        raise ValueError("x_max must be strictly greater than x_min")
    if not (math.isfinite(horizon) and horizon > 0):
        raise ValueError("horizon must be positive")
    if not (math.isfinite(stable_band) and stable_band > 0):
        raise ValueError("stable_band must be positive")
    if n_steps < 1:
        raise ValueError("n_steps must be >= 1")

    dx = (x_max - x_min) / (n_grid - 1)
    dt = horizon / n_steps
    grid = [x_min + i * dx for i in range(n_grid)]

    s0 = sigma0 if (sigma0 is not None and sigma0 > 0) else 1.5 * dx
    inv_2v0 = 1.0 / (2.0 * s0 * s0)
    x0_clamped = clamp(x0, x_min + 2.0 * dx, x_max - 2.0 * dx)
    p = [math.exp(-min(700.0, ((x - x0_clamped) ** 2) * inv_2v0)) for x in grid]
    mass0 = sum(p) * dx
    if mass0 <= EPS:
        mid = min(range(n_grid), key=lambda idx: abs(grid[idx] - x0_clamped))
        p = [0.0] * n_grid
        p[mid] = 1.0 / dx
    else:
        p = [v / mass0 for v in p]

    # Pick evenly spaced snapshot step indices (always include final step)
    snap_steps: set[int] = {n_steps}
    if snapshot_count > 1:
        for k in range(1, snapshot_count + 1):
            idx = max(1, min(n_steps, round(k * n_steps / snapshot_count)))
            snap_steps.add(idx)

    n_faces = n_grid - 1
    alpha = [0.0] * n_faces
    beta = [0.0] * n_faces
    lower = [0.0] * n_grid
    diag = [0.0] * n_grid
    upper = [0.0] * n_grid
    r = dt / dx
    timeline: list[DistributionMetrics] = []

    for step in range(1, n_steps + 1):
        t = step * dt
        d_nodes = [max(_finite_float(d_fn(x, t), EPS), EPS) for x in grid]

        for i in range(n_faces):
            x_half = grid[i] + 0.5 * dx
            mu_half = _finite_float(mu_fn(x_half, t), 0.0)
            d_half = max(0.5 * (d_nodes[i] + d_nodes[i + 1]), EPS)
            dd_dx = (d_nodes[i + 1] - d_nodes[i]) / dx
            # Itô -> Fickian conversion: μ_eff = μ - 0.5 * ∂D/∂x
            mu_eff = mu_half - 0.5 * dd_dx
            peclet = clamp((2.0 * mu_eff * dx) / d_half, -500.0, 500.0)
            scale = d_half / (2.0 * dx)
            alpha[i] = scale * bernoulli(-peclet)
            beta[i] = scale * bernoulli(peclet)

        # Zero-flux boundary condition J_{-1/2} = J_{N-1/2} = 0
        for i in range(n_grid):
            out_r = alpha[i] if i < n_faces else 0.0
            out_l = beta[i - 1] if i > 0 else 0.0
            diag[i] = 1.0 + r * (out_r + out_l)
            lower[i] = -r * alpha[i - 1] if i > 0 else 0.0
            upper[i] = -r * beta[i] if i < n_faces else 0.0

        p = solve_tridiagonal(lower, diag, upper, p)
        if step in snap_steps:
            timeline.append(_summarize_pdf(grid, p, dx, stable_band, t))

    return FokkerPlanckSolution(
        final=timeline[-1],
        timeline=timeline,
        grid=grid,
        pdf=p,
        dx=dx,
        stable_band=stable_band,
        horizon=horizon,
    )


# ---------------------------------------------------------------------------
# Mode 1 — Daily/OHLCV Candle Screener & Asset Distribution Analyzer
# ---------------------------------------------------------------------------

def _sanitize_candles(candles: list[dict[str, Any]]) -> list[dict[str, float]]:
    clean: list[dict[str, float]] = []
    for c in candles or []:
        if not isinstance(c, dict):
            continue
        close = _finite_float(c.get("close"), 0.0)
        open_p = _finite_float(c.get("open"), close)
        high = max(_finite_float(c.get("high"), close), open_p, close)
        low_raw = _finite_float(c.get("low"), close)
        low = min(low_raw if low_raw > 0 else close, open_p, close)
        if close <= 0 or low <= 0 or high <= 0:
            continue
        vol = max(0.0, _finite_float(c.get("volume"), 0.0))
        qv = max(0.0, _finite_float(c.get("quoteVolume"), 0.0))
        trades = max(0.0, _finite_float(c.get("trades"), 0.0))
        tb = clamp(_finite_float(c.get("takerBuyBase"), 0.5 * vol), 0.0, vol)
        clean.append(
            {
                "date": _finite_float(c.get("date"), 0.0),
                "open": open_p,
                "high": high,
                "low": low,
                "close": close,
                "volume": vol,
                "quoteVolume": qv,
                "trades": trades,
                "takerBuyBase": tb,
            }
        )
    return clean


def _classify_candle_regime(m: DistributionMetrics, mu_flow: float) -> str:
    if m.p_tail_crash >= 0.22:
        return "TAIL_CRASH_RISK"
    if m.p_downside >= 0.40 or (m.p_downside > m.p_upside + 0.12 and mu_flow < 0):
        return "DOWNSIDE_SKEW"
    if m.p_stable >= 0.52 and m.p_upside > m.p_downside * 1.30 and mu_flow > 0:
        return "ACCUMULATION_STABLE"
    if m.p_upside >= 0.36 and m.p_upside > m.p_downside * 1.35:
        return "BULLISH_EXPANSION"
    if m.p_stable >= 0.50:
        return "SIDEWAYS_CALM"
    return "HIGH_VOL_DISPERSED"


def analyze_candles_fp(
    candles: list[dict[str, Any]],
    *,
    horizon_days: float = 7.0,
    stable_band: float = 0.05,
    window: int = DEFAULT_WINDOW,
    include_curve: bool = False,
    n_grid: int = 201,
) -> dict[str, Any]:
    """Estimate crypto drift/diffusion parameters from OHLCV candles and evolve P(x,t)."""
    rows = _sanitize_candles(candles)
    if len(rows) < max(MIN_CANDLES, 2 * window + 1):
        return {
            "ok": False,
            "reason": f"Histori kurang: {len(rows)} candle valid (minimal {max(MIN_CANDLES, 2 * window + 1)}).",
        }

    horizon_days = clamp(_finite_float(horizon_days, 7.0), 0.5, 60.0)
    stable_band = clamp(_finite_float(stable_band, 0.05), 0.005, 0.35)

    closes = [c["close"] for c in rows]
    price_now = closes[-1]
    log_rets = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    hist = log_rets[-min(len(log_rets), 3 * window) :]

    # 1. Baseline diffusion D0: EWMA log-return variance + Parkinson intraday wick variance.
    #    Parkinson captures intra-candle wicks without letting tight ranges shrink realized vol.
    ewma_var = hist[0] ** 2
    for r in hist[1:]:
        ewma_var = 0.94 * ewma_var + 0.06 * (r * r)

    park_k = 1.0 / (4.0 * math.log(2.0))
    park_var = mean(
        park_k * (math.log(max(c["high"], EPS) / max(c["low"], EPS)) ** 2)
        for c in rows[-window:]
    )
    d0 = max(ewma_var, 0.60 * ewma_var + 0.40 * park_var, 1e-8)
    sigma_d = math.sqrt(d0)

    # 2. Fat-tail coefficient (excess kurtosis) & downside volatility asymmetry (leverage effect)
    mu_h = mean(hist)
    var_h = max(mean((r - mu_h) ** 2 for r in hist), 1e-10)
    std_h = math.sqrt(var_h)
    sample_kurt = mean(((r - mu_h) / std_h) ** 4 for r in hist)
    excess_kurt = max(0.0, sample_kurt - 3.0)
    tail_eta = clamp(0.08 * excess_kurt, 0.05, 0.55)

    neg_sq = [r * r for r in hist if r < 0]
    pos_sq = [r * r for r in hist if r > 0]
    down_asym = (
        clamp((mean(neg_sq) / max(mean(pos_sq), EPS)) - 1.0, 0.0, 0.65)
        if (neg_sq and pos_sq)
        else 0.0
    )

    # 3. Flow-quality-adjusted drift μ(x,t)
    cur = rows[-window:]
    prev = rows[-2 * window : -window]
    cur_vol = sum(c["volume"] for c in cur)
    prev_vol = sum(c["volume"] for c in prev)
    cur_qv = sum(c["quoteVolume"] for c in cur)
    prev_qv = sum(c["quoteVolume"] for c in prev)
    cur_tb = sum(c["takerBuyBase"] for c in cur)
    cur_tr = sum(c["trades"] for c in cur)
    prev_tr = sum(c["trades"] for c in prev)

    has_taker = cur_vol > EPS and any(c["takerBuyBase"] > 0 for c in cur)
    buy_share = (cur_tb / cur_vol) if has_taker else 0.5
    buy_imbalance = 2.0 * buy_share - 1.0
    unit_growth = (cur_vol - prev_vol) / prev_vol if prev_vol > EPS else 0.0
    qv_growth = (cur_qv - prev_qv) / prev_qv if prev_qv > EPS else 0.0
    part_growth = (cur_tr - prev_tr) / prev_tr if prev_tr > EPS else 0.0

    # Discount drift when trade count decays (froth) or quote growth is purely price-driven
    part_weight = clamp(1.0 + 0.60 * sat(part_growth), 0.35, 1.25)
    distortion_flag = qv_growth > 0.05 and unit_growth <= 0.0
    distortion_discount = 0.50 if distortion_flag else 1.0

    raw_signal = (0.85 * buy_imbalance * part_weight + 0.12 * sat(unit_growth)) * distortion_discount
    # Conservatively cap daily drift at +-0.15 * sigma_d to avoid overconfident probabilities
    mu_flow = clamp(raw_signal, -0.15, 0.15) * sigma_d

    # 4. Ornstein–Uhlenbeck anchor toward 20-day VWAP
    vwap_seg = rows[-min(20, len(rows)) :]
    vwap_den = sum(c["volume"] for c in vwap_seg)
    if vwap_den > EPS:
        vwap20 = (
            sum(((c["high"] + c["low"] + c["close"]) / 3.0) * c["volume"] for c in vwap_seg)
            / vwap_den
        )
    else:
        vwap20 = mean(c["close"] for c in vwap_seg)
    x_vwap = clamp(math.log(max(vwap20, EPS) / price_now), -0.20, 0.20)
    kappa = 0.03

    scale_x = max(sigma_d * math.sqrt(horizon_days), stable_band)
    drift_span = abs(mu_flow) * horizon_days + abs(x_vwap)
    domain = max(6.5 * scale_x + drift_span, 4.2 * stable_band)
    n_steps = max(24, min(64, int(round(horizon_days * 4))))

    sol = solve_fokker_planck(
        lambda x, _t: mu_flow - kappa * (x - x_vwap),
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
    regime = _classify_candle_regime(f, mu_flow)
    meta = CANDLE_REGIME_META[regime]

    band_pct = stable_band * 100.0
    notes: list[str] = [
        f"Dalam horizon {horizon_days:g} hari, probabilitas harga bertahan di zona ±{band_pct:g}% adalah {f.p_stable*100:.1f}% "
        f"(downside {f.p_downside*100:.1f}% · upside {f.p_upside*100:.1f}%).",
        f"Difusi harian σ={sigma_d*100:.2f}% (EWMA {math.sqrt(ewma_var)*100:.2f}%, Parkinson wick {math.sqrt(park_var)*100:.2f}%) "
        f"dengan parameter ekor tebal η={tail_eta:.2f} (kurtosis hasil {f.kurtosis:.2f}).",
        f"Drift aliran μ={mu_flow*100:+.2f}%/hari dari taker-buy share {buy_share*100:.1f}% "
        f"(bobot partisipasi {part_weight:.2f}x) dan jangkar VWAP-20 {x_vwap*100:+.1f}%.",
    ]
    if distortion_flag:
        notes.append(
            f"Volume USD naik {qv_growth:+.1%} sementara volume unit {unit_growth:+.1%} — sinyal drift didiskon 50% (filter distorsi harga)."
        )
    if down_asym >= 0.20:
        notes.append(
            f"Terdeteksi asimetri volatilitas turun (λ_down={down_asym:.2f}): pergerakan turun historis lebih tajam daripada naik, mempertebal ekor kiri."
        )
    if f.p_tail_crash >= 0.15:
        notes.append(
            f"Peringatan tail-risk: peluang penurunan ekstrem di bawah -{2*band_pct:g}% mencapai {f.p_tail_crash*100:.1f}%."
        )

    payload: dict[str, Any] = {
        "ok": True,
        "price": price_now,
        "vwap20": vwap20,
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
            "kappaMeanRev": kappa,
            "vwapAnchorLog": round(x_vwap, 6),
            "vwapAnchorPct": round(x_vwap * 100.0, 3),
            "tailEta": round(tail_eta, 4),
            "downsideAsym": round(down_asym, 4),
            "sampleKurtosis": round(sample_kurt, 3),
            "buyShare30d": round(buy_share, 4),
            "buyImbalance30d": round(buy_imbalance, 4),
            "unitGrowth30d": round(unit_growth, 4),
            "participationGrowth30d": round(part_growth, 4),
            "participationWeight": round(part_weight, 4),
            "distortionDiscounted": distortion_flag,
        },
        "priceBands": {
            "crashPrice": round(price_now * math.exp(-2.0 * stable_band), 8),
            "lowerStable": round(price_now * math.exp(-stable_band), 8),
            "currentPrice": round(price_now, 8),
            "upperStable": round(price_now * math.exp(stable_band), 8),
            "pumpPrice": round(price_now * math.exp(2.0 * stable_band), 8),
            "expectedPrice": round(price_now * (1.0 + f.expected_return_pct / 100.0), 8),
        },
        "notes": notes,
    }
    if include_curve:
        payload["curve"] = export_chart_curve(sol)
        payload["timeline"] = [m.to_dict() for m in sol.timeline]
    return payload


# ---------------------------------------------------------------------------
# Mode 2 — Order-Book & Spoofing Microstructure Analyzer + Direct PDE Lab
#
# Implements the 3-pillar anti-spoofing microstructure architecture:
#   Pillar 1: Real Trade-Flow Priority (Taker Volume dominates μ) + Iceberg
#             Absorption guard (heavy taker flow with stalled price impact).
#   Pillar 2: Spoof-Filtered Stoikov Micro-Price
#             P_micro = (P_ask·Q_bid + P_bid·Q_ask) / (Q_bid + Q_ask),
#             giving higher weight to the thinner side and anchoring x_eq.
#   Pillar 3: Volatility-Adaptive Exponential Distance Decay w(d) = exp(-λ·d)
#             reducing resting walls >0.5%–1.0% from mid-price to near zero.
# ---------------------------------------------------------------------------

def adaptive_decay_lambda(
    sigma_per_sec: float = 0.0007,
    horizon_sec: float = 30.0,
    override_lambda: Optional[float] = None,
) -> float:
    """Volatility-adaptive exponential decay rate λ for relative distance d = |p - p_mid| / p_mid.

    Default band λ ∈ [450, 800] guarantees weights beyond 0.5%–1.0% from mid-price
    decay to near-zero (e.g. at λ=550: w(0.5%) = 6.4%, w(1.0%) = 0.41%), while
    tightening automatically for calm majors and relaxing slightly for fast pairs.
    """
    if override_lambda is not None:
        val = _finite_float(override_lambda, 0.0)
        if val > 0:
            return clamp(val, 100.0, 2000.0)
    sigma_h = max(1e-5, _finite_float(sigma_per_sec, 0.0007)) * math.sqrt(
        clamp(_finite_float(horizon_sec, 30.0), 1.0, 600.0)
    )
    char_dist = clamp(0.55 * sigma_h, 0.00125, 0.00222)  # λ between ~450 and 800
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
    """Compute spoof-filtered Stoikov Micro-Price.

    Weights best_ask by effective bid depth and best_bid by effective ask depth:
        P_micro = (P_ask * Q_bid_eff + P_bid * Q_ask_eff) / (Q_bid_eff + Q_ask_eff)
    When the ask queue is thin and bid queue is thick, I_micro -> 1 and P_micro
    shifts toward P_ask (the thinner, easier-to-clear side). Cancel-heavy sides
    are discounted so flickering BBO spoof orders cannot hijack fair value.
    """
    p_bid = max(EPS, _finite_float(best_bid, 100.0))
    p_ask = max(p_bid, _finite_float(best_ask, p_bid))
    mid = 0.5 * (p_bid + p_ask)
    spread = max(0.0, p_ask - p_bid)
    spread_bps = (spread / mid) * 10_000.0 if mid > EPS else 0.0

    q_bid_raw = max(0.0, _finite_float(bid_qty, 0.0))
    q_ask_raw = max(0.0, _finite_float(ask_qty, 0.0))
    r_cb = clamp(_finite_float(bid_cancel_rate, 0.0), 0.0, 1.0)
    r_ca = clamp(_finite_float(ask_cancel_rate, 0.0), 0.0, 1.0)

    # Raw Stoikov imbalance vs cancel-penalised effective imbalance
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
        "microImbalance": imbalance_eff,      # 0 = pinned to bid, 0.5 = mid, 1 = pinned to ask
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
    far_threshold: float = 0.005,  # 0.5% from mid-price
    max_levels: int = 100,
) -> dict[str, Any]:
    """Parse L2 bids/asks with exponential distance decay w(d) = exp(-λ·d)."""
    def _clean_levels(raw_side: Any, reverse: bool) -> list[tuple[float, float]]:
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

    bid_levels = _clean_levels(bids, reverse=True)
    ask_levels = _clean_levels(asks, reverse=False)
    if not bid_levels or not ask_levels:
        return {"ok": False}

    best_bid, best_bid_qty = bid_levels[0]
    best_ask, best_ask_qty = ask_levels[0]
    if best_bid <= 0 or best_ask <= 0 or best_ask < best_bid:
        return {"ok": False}

    mid = 0.5 * (best_bid + best_ask)
    lam = adaptive_decay_lambda(sigma_per_sec, horizon_sec, decay_lambda)

    def _accumulate(levels: list[tuple[float, float]]) -> tuple[float, float, float, float, dict[str, float]]:
        raw_total = 0.0
        weighted_total = 0.0
        near_total = 0.0
        far_total = 0.0
        weighted_map: dict[str, float] = {}
        for px, qty in levels:
            dist = abs(px - mid) / mid
            weight = math.exp(-min(60.0, lam * dist))
            w_qty = qty * weight
            raw_total += qty
            weighted_total += w_qty
            if dist >= far_threshold:
                far_total += qty
            else:
                near_total += qty
            weighted_map[f"{px:.8f}"] = w_qty
        return raw_total, weighted_total, near_total, far_total, weighted_map

    raw_b, w_b, near_b, far_b, map_b = _accumulate(bid_levels)
    raw_a, w_a, near_a, far_a, map_a = _accumulate(ask_levels)

    max_bid_dist = abs(bid_levels[-1][0] - mid) / mid
    max_ask_dist = abs(ask_levels[-1][0] - mid) / mid

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
        "weightedBidDepth": w_b,
        "weightedAskDepth": w_a,
        "nearBidDepth": near_b,
        "nearAskDepth": near_a,
        "farBidDepth": far_b,
        "farAskDepth": far_a,
        "weightedBidMap": map_b,
        "weightedAskMap": map_a,
        "levelsUsed": len(bid_levels) + len(ask_levels),
        "maxDepthSpanPct": max(max_bid_dist, max_ask_dist) * 100.0,
    }


def analyze_orderbook_fp(
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
    """Evolve short-horizon Fokker–Planck PDF with 3-pillar anti-spoof microstructure.

    `bid_depth` / `ask_depth` represent near-touch (or already decay-weighted) resting
    liquidity; `far_bid_depth` / `far_ask_depth` represent distant resting walls placed
    around `wall_distance_pct` (default 0.80% from mid-price), which are attenuated via
    exponential decay w(d) = exp(-λ·d).
    """
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

    # --- Pillar 3: Exponential Distance Decay w(d) = exp(-λ·d) --------------
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
    distant_wall_gap = obi - obi_raw  # positive => scary distant ask wall filtered out
    distant_wall_detected = far_wall_share >= 0.35 and abs(distant_wall_gap) >= 0.25
    distant_wall_side = (
        "ASK" if (distant_wall_detected and far_ask > far_bid) else (
            "BID" if distant_wall_detected else "NONE"
        )
    )

    # --- Spoof cancellation diagnostics (near-touch book) -------------------
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
    net_spoof = bid_spoof - ask_spoof  # < 0 fake ask/sell wall, > 0 fake bid/buy wall

    # --- Pillar 2: Spoof-Filtered Stoikov Micro-Price -----------------------
    ref_mid = mid_price if (mid_price is not None and _finite_float(mid_price, 0.0) > 0) else 100.0
    if best_bid is not None and best_ask is not None and best_bid > 0 and best_ask >= best_bid:
        p_bid_use = float(best_bid)
        p_ask_use = float(best_ask)
        ref_mid = 0.5 * (p_bid_use + p_ask_use)
    else:
        half_sp = 0.5 * clamp(_finite_float(spread_bps, 8.0), 0.1, 500.0) * 1e-4
        p_bid_use = ref_mid * (1.0 - half_sp)
        p_ask_use = ref_mid * (1.0 + half_sp)

    # Blend level-1 BBO size (60%) with decay-weighted near-touch depth (40%) when BBO qty is known,
    # preventing tiny dust quotes at BBO from distorting fair value while keeping front-queue focus.
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
    # Fair-value equilibrium anchor x_micro = ln(P_micro / P_mid), damped under active spoofing
    x_micro = clamp(mp["microShiftLog"] * (1.0 - 0.60 * spoof_index), -0.5 * band, 0.5 * band)

    # --- Pillar 1: Real Taker-Flow Priority + Iceberg Absorption Guard ------
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
            impact_efficiency = clamp((flow_dir * dx_obs) / max(abs(tfi) * expected_scale, EPS), 0.0, 1.0)
            flow_strength = clamp((abs(tfi) - 0.28) / 0.48, 0.0, 1.0)
            # Confirm passive side hasn't collapsed
            book_support = 1.0 if (flow_dir * obi) <= 0.15 else 0.65
            absorption_index = clamp(flow_strength * (1.0 - impact_efficiency) * book_support, 0.0, 1.0)
            if absorption_index >= 0.35:
                # Heavy selling stalled => passive buy iceberg; heavy buying stalled => passive sell iceberg
                absorption_side = "BUY" if tfi < 0 else "SELL"

    # Real executed market orders receive >=80% baseline drift authority (rising >93% under spoofing),
    # while resting limit-order imbalance plays only a supporting role after exponential decay.
    alpha_tfi = 0.36 * (1.0 - 0.70 * absorption_index)
    genuine_book_weight = 1.0 - 0.75 * spoof_index
    beta_obi = 0.09 * genuine_book_weight
    weight_denom = max(alpha_tfi + beta_obi, EPS)
    taker_priority_share = alpha_tfi / weight_denom

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

    sol = solve_fokker_planck(
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

    meta = MICRO_REGIME_META[regime]
    micro_dir = (
        "mendekati Best Ask"
        if mp["microImbalance"] > 0.58
        else ("mendekati Best Bid" if mp["microImbalance"] < 0.42 else "seimbang di sekitar Mid-Price")
    )
    notes: list[str] = [
        f"Prioritas Transaksi Nyata: TFI (market order) {tfi:+.2f} memegang {taker_priority_share*100:.0f}% bobot drift μ, "
        f"sedangkan OBI terbobot jarak {obi:+.2f} (Indeks Spoof {spoof_index*100:.0f}%).",
        f"Micro-Price: bobot sisi beli I={mp['microImbalance']*100:.1f}% ({micro_dir}, pergeseran {mp['microShiftBps']:+.2f} bps dari mid-price, "
        f"spread {mp['spreadBps']:.1f} bps).",
        f"Exponential Decay (λ={lam:.0f}): antrean jarak 0,5% diberi bobot {w_05pct*100:.1f}% dan jarak 1,0% tinggal {w_10pct*100:.2f}%. "
        f"Hasil t={horizon_s:g}s: P(stabil)={f.p_stable*100:.1f}%, P(dump)={f.p_downside*100:.1f}%, P(pump)={f.p_upside*100:.1f}%.",
    ]
    if distant_wall_detected:
        wall_lbl = "jual (ask)" if distant_wall_side == "ASK" else "beli (bid)"
        notes.append(
            f"Filter Tembok Jauh aktif: {far_wall_share*100:.0f}% volume berada jauh (~{wall_dist*100:.2f}% dari mid) sehingga OBI mentah "
            f"tampak ekstrem ({obi_raw:+.2f}), namun setelah redaman e^(-λd) (bobot {w_far*100:.1f}%) OBI efektif hanya {obi:+.2f} — tembok {wall_lbl} jauh diabaikan."
        )
    if regime in {"ICEBERG_BUY_ABSORPTION", "ICEBERG_SELL_ABSORPTION"}:
        side_txt = "beli (bid iceberg)" if absorption_side == "BUY" else "jual (ask iceberg)"
        notes.append(
            f"Terdeteksi absorpsi pasif {side_txt} (skor {absorption_index*100:.0f}%): arus market order deras (TFI {tfi:+.2f}) "
            f"tetapi perubahan harga aktual hanya {obs_change_bps:+.1f} bps — sinyal drift agresor didiskon agar tidak tertipu."
        )
    elif regime in {"SPOOF_SELL_WALL", "SPOOF_BUY_WALL"}:
        side = "jual (ask)" if regime == "SPOOF_SELL_WALL" else "beli (bid)"
        notes.append(
            f"Divergensi spoofing: tembok {side} tebal + cancel tinggi ({max(bid_cancel_rate, ask_cancel_rate)*100:.0f}%) tanpa eksekusi taker sepadan. "
            f"Drift semu meluruh (τ={tau:g}s) dan Micro-Price dilindungi filter cancel."
        )
    elif regime == "REAL_SELL_PRESSURE":
        notes.append(
            "Market-sell nyata mendominasi transaksi — drift μ negatif bersifat persisten dan mendorong kurva P(x,T) ke zona dump."
        )
    elif regime == "REAL_BUY_PRESSURE":
        notes.append(
            "Market-buy nyata mendominasi transaksi — drift μ positif bersifat persisten dan mendorong kurva P(x,T) ke zona pump."
        )
    elif regime == "HIGH_VOL_WHIPSAW":
        notes.append(
            "Difusi volatilitas mendominasi drift: puncak P(x,t) merata dan massa probabilitas menyebar ke kedua sisi sekaligus."
        )

    has_real_price = (mid_price is not None and _finite_float(mid_price, 0.0) > 0) or (
        best_bid is not None and best_ask is not None
    )

    out: dict[str, Any] = {
        "ok": True,
        "mode": "orderbook",
        "horizonSec": horizon_s,
        "stableBand": band,
        "regime": regime,
        "regimeLabel": meta["label"],
        "regimeTone": meta["tone"],
        "regimeSummary": meta["summary"],
        "metrics": f.to_dict(),
        "microstructure": {
            # Pillar 1: Taker flow priority & iceberg absorption
            "tfi": round(tfi, 4),
            "takerPriorityShare": round(taker_priority_share, 4),
            "absorptionIndex": round(absorption_index, 4),
            "absorptionSide": absorption_side,
            "priceChangeBps": round(obs_change_bps, 2) if obs_change_bps is not None else None,
            # Pillar 2: Spoof-filtered Stoikov Micro-Price
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
            # Pillar 3: Exponential distance decay & distant wall guard
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
            # Spoof & PDE parameters
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
        "curve": export_chart_curve(sol),
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


def simulate_direct_pde(
    *,
    mu: float = -0.02,
    diffusion: float = 0.0025,
    kappa: float = 0.05,
    tail_eta: float = 0.20,
    downside_asym: float = 0.25,
    horizon: float = 1.0,
    stable_band: float = 0.05,
    naive_p0: float = 0.80,
) -> dict[str, Any]:
    """Direct mathematical Fokker–Planck sandbox on user-chosen (μ, D, κ, η, t)."""
    mu_v = clamp(_finite_float(mu, -0.02), -2.0, 2.0)
    d_v = clamp(_finite_float(diffusion, 0.0025), 1e-7, 2.0)
    kappa_v = clamp(_finite_float(kappa, 0.05), 0.0, 5.0)
    eta_v = clamp(_finite_float(tail_eta, 0.20), 0.0, 1.5)
    asym_v = clamp(_finite_float(downside_asym, 0.25), 0.0, 1.5)
    t_end = clamp(_finite_float(horizon, 1.0), 0.05, 120.0)
    band = clamp(_finite_float(stable_band, 0.05), 0.001, 1.0)
    p0 = clamp(_finite_float(naive_p0, 0.80), 0.01, 0.99)

    sigma_h = math.sqrt(d_v * t_end)
    scale_x = max(sigma_h, band)
    drift_shift = abs(mu_v) * (
        (1.0 - math.exp(-kappa_v * t_end)) / kappa_v if kappa_v > 1e-4 else t_end
    )
    domain = max(6.5 * scale_x + 1.5 * drift_shift, 4.5 * band)

    sol = solve_fokker_planck(
        lambda x, _t: mu_v - kappa_v * x,
        lambda x, _t: d_v
        * (1.0 + asym_v * max(0.0, -x / scale_x) + eta_v * min((x / scale_x) ** 2, 20.0)),
        x_min=-domain,
        x_max=domain,
        stable_band=band,
        horizon=t_end,
        n_grid=221,
        n_steps=40,
        snapshot_count=6,
    )
    f = sol.final

    # Compute the naive scalar expression side-by-side solely to illustrate why full PDE matters
    naive_p = p0 + (mu_v * p0 + 0.5 * d_v * p0) * t_end
    naive_issues: list[str] = []
    if naive_p > 1.0:
        naive_issues.append(f"melanggar batas probabilitas (> 100%: {naive_p*100:.1f}%)")
    if naive_p < 0.0:
        naive_issues.append(f"bernilai negatif ({naive_p*100:.1f}%)")
    if d_v > 0:
        naive_issues.append(
            f"suku +0.5·D·P menambah +{0.5*d_v*p0*t_end*100:.2f}% (keliru: volatilitas seharusnya melebarkan kurva & menurunkan puncak)"
        )

    notes = [
        f"Solusi PDE Fokker–Planck penuh dengan μ(x)={mu_v:+.4f} - {kappa_v:.3f}·x dan D₀={d_v:.5f} (σ={math.sqrt(d_v):.4f}): "
        f"total massa ∫P(x,t)dx = {f.total_mass:.6f} (terkonservasi tepat 100%).",
        f"Luas kurva pada t={t_end:g}: zona stabil [-{band*100:g}%, +{band*100:g}%] = {f.p_stable*100:.1f}%, "
        f"ekor kiri (dump) = {f.p_downside*100:.1f}%, ekor kanan (pump) = {f.p_upside*100:.1f}%.",
    ]

    return {
        "ok": True,
        "mode": "direct",
        "horizon": t_end,
        "stableBand": band,
        "metrics": f.to_dict(),
        "parameters": {
            "mu": mu_v,
            "diffusion": d_v,
            "sigma": round(math.sqrt(d_v), 6),
            "kappa": kappa_v,
            "tailEta": eta_v,
            "downsideAsym": asym_v,
        },
        "naiveComparison": {
            "p0": p0,
            "naiveResult": round(naive_p, 6),
            "validProbability": 0.0 <= naive_p <= 1.0,
            "issues": naive_issues,
        },
        "curve": export_chart_curve(sol),
        "timeline": [m.to_dict() for m in sol.timeline],
        "notes": notes,
    }


# ---------------------------------------------------------------------------
# Exchange Order-Book + Trades Snapshot (reusing existing market_data hosts)
# ---------------------------------------------------------------------------

def _microstructure_hosts() -> list[tuple[str, str]]:
    hosts = [
        ("binance-vision", "https://data-api.binance.vision/api/v3"),
        ("binance-us", "https://api.binance.us/api/v3"),
        ("binance-spot", "https://api.binance.com/api/v3"),
        ("mexc-spot", "https://api.mexc.com/api/v3"),
        ("binance-futures", "https://fapi.binance.com/fapi/v1"),
    ]
    if market_data._binance_us_first():
        return [hosts[1], hosts[0], *hosts[2:]]
    return hosts


def _parse_recent_trades(raw: Any) -> tuple[float, float, float, float, Optional[float]]:
    """Return (taker_buy_vol, taker_sell_vol, sigma_per_sec, span_sec, price_change_bps)."""
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
        is_buyer_maker = bool(item.get("isBuyerMaker") if "isBuyerMaker" in item else item.get("m"))
        if price <= 0 or qty <= 0:
            continue
        # isBuyerMaker=True => passive buyer was hit by a market SELL order
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

    # Aggregate into 1-second buckets to avoid bid-ask bounce inflation
    sec_closes: dict[int, float] = {}
    for t_sec, px in prices_ts:
        sec_closes[int(t_sec)] = px
    ordered = sorted(sec_closes.items())
    if len(ordered) >= 3:
        var_per_sec_terms: list[float] = []
        for i in range(1, len(ordered)):
            dt_s = max(1.0, float(ordered[i][0] - ordered[i - 1][0]))
            lr = math.log(ordered[i][1] / ordered[i - 1][1])
            var_per_sec_terms.append((lr * lr) / dt_s)
        sigma_s = math.sqrt(max(mean(var_per_sec_terms), 1e-10))
    else:
        sum_sq = sum(
            math.log(prices_ts[i][1] / prices_ts[i - 1][1]) ** 2
            for i in range(1, len(prices_ts))
        )
        sigma_s = math.sqrt(max(sum_sq / max(span, 1.0), 1e-10))

    return taker_buy, taker_sell, clamp(sigma_s, 3e-5, 0.015), span, price_change_bps


def _estimate_cancellations(
    symbol: str,
    bid_map: dict[str, float],
    ask_map: dict[str, float],
    taker_buy: float,
    taker_sell: float,
) -> tuple[float, float, bool]:
    """Estimate unexecuted order disappearance between consecutive decay-weighted snapshots."""
    now = time.monotonic()
    prev = _PREV_BOOK_CACHE.get(symbol)

    if len(_PREV_BOOK_CACHE) >= _PREV_BOOK_MAX:
        oldest_key = min(_PREV_BOOK_CACHE, key=lambda k: _PREV_BOOK_CACHE[k]["ts"])
        _PREV_BOOK_CACHE.pop(oldest_key, None)

    _PREV_BOOK_CACHE[symbol] = {"ts": now, "bids": bid_map, "asks": ask_map}

    if not prev or (now - prev["ts"]) > _PREV_BOOK_TTL_SEC:
        return 0.0, 0.0, False

    bid_vanished = sum(
        max(0.0, old_q - bid_map.get(px, 0.0)) for px, old_q in prev["bids"].items()
    )
    ask_vanished = sum(
        max(0.0, old_q - ask_map.get(px, 0.0)) for px, old_q in prev["asks"].items()
    )
    # Subtract executed aggressor volume on that side; remainder is passive cancellation/pull
    canceled_bid = max(0.0, bid_vanished - taker_sell)
    canceled_ask = max(0.0, ask_vanished - taker_buy)
    return canceled_bid, canceled_ask, True


async def fetch_microstructure_snapshot(
    client: httpx.AsyncClient,
    symbol: str,
    fallback_candles: Optional[list[dict[str, Any]]] = None,
    *,
    horizon_sec: float = 30.0,
    stable_band: float = 0.004,
    prefer_seed: bool = False,
) -> dict[str, Any]:
    """Fetch top-100 order book + recent trades with exponential decay & Micro-Price."""
    sym = symbol.upper().strip()
    debug: list[str] = []

    if not prefer_seed:
        for source, base in _microstructure_hosts():
            try:
                # limit=100 spans far enough from mid-price (often 0.3%–1%+) so exponential
                # distance decay can catch & suppress distant intimidation walls.
                depth_url = f"{base}/depth?symbol={sym}&limit=100"
                trades_url = f"{base}/trades?symbol={sym}&limit=200"
                depth_raw, trades_raw = await asyncio.gather(
                    market_data._fetch_json(client, depth_url, timeout=4.5),
                    market_data._fetch_json(client, trades_url, timeout=4.5),
                )
                tb, ts, sigma_s, span_s, px_chg_bps = _parse_recent_trades(trades_raw)
                book = compute_weighted_book(
                    depth_raw.get("bids"),
                    depth_raw.get("asks"),
                    sigma_per_sec=sigma_s,
                    horizon_sec=horizon_sec,
                    max_levels=100,
                )
                if book.get("ok") and book["weightedBidDepth"] > 0 and book["weightedAskDepth"] > 0:
                    c_bid, c_ask, has_diff = _estimate_cancellations(
                        sym, book["weightedBidMap"], book["weightedAskMap"], tb, ts
                    )
                    # Pass near-touch weighted depth and far (>0.5%) raw walls separately
                    # so analyze_orderbook_fp reports both raw vs decay-filtered OBI accurately.
                    lam = book["decayLambda"]
                    wall_dist = 0.008
                    w_far = math.exp(-lam * wall_dist)
                    near_w_bid = max(0.0, book["weightedBidDepth"] - book["farBidDepth"] * w_far)
                    near_w_ask = max(0.0, book["weightedAskDepth"] - book["farAskDepth"] * w_far)

                    analysis = analyze_orderbook_fp(
                        bid_depth=near_w_bid,
                        ask_depth=near_w_ask,
                        far_bid_depth=book["farBidDepth"],
                        far_ask_depth=book["farAskDepth"],
                        wall_distance_pct=wall_dist,
                        decay_lambda=lam,
                        taker_buy_vol=tb,
                        taker_sell_vol=ts,
                        canceled_bid_vol=c_bid,
                        canceled_ask_vol=c_ask,
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
                    # Override raw OBI with exact level-by-level raw depth ratio
                    raw_sum = book["rawBidDepth"] + book["rawAskDepth"]
                    if raw_sum > EPS:
                        exact_obi_raw = (book["rawBidDepth"] - book["rawAskDepth"]) / raw_sum
                        analysis["microstructure"]["obiRaw"] = round(exact_obi_raw, 4)
                        analysis["microstructure"]["distantWallGap"] = round(
                            analysis["microstructure"]["obi"] - exact_obi_raw, 4
                        )

                    inputs = {
                        "bidDepth": round(near_w_bid, 6),
                        "askDepth": round(near_w_ask, 6),
                        "farBidDepth": round(book["farBidDepth"], 6),
                        "farAskDepth": round(book["farAskDepth"], 6),
                        "rawBidDepth": round(book["rawBidDepth"], 6),
                        "rawAskDepth": round(book["rawAskDepth"], 6),
                        "takerBuyVol": round(tb, 6),
                        "takerSellVol": round(ts, 6),
                        "canceledBidVol": round(c_bid, 6),
                        "canceledAskVol": round(c_ask, 6),
                        "sigmaPerSec": round(sigma_s, 6),
                        "spreadBps": round(book["spreadBps"], 3),
                        "priceChangeBps": round(px_chg_bps, 2) if px_chg_bps is not None else None,
                        "bestBid": round(book["bestBid"], 8),
                        "bestAsk": round(book["bestAsk"], 8),
                        "midPrice": round(book["midPrice"], 8),
                        "microPrice": analysis["microstructure"]["microPrice"],
                        "decayLambda": round(lam, 1),
                        "levelsUsed": book["levelsUsed"],
                        "maxDepthSpanPct": round(book["maxDepthSpanPct"], 3),
                        "tradeWindowSec": round(span_s, 1),
                        "cancelDiffAvailable": has_diff,
                    }
                    if not has_diff:
                        analysis["notes"].append(
                            "Snapshot awal: klik 'Ambil Snapshot API' sekali lagi atau aktifkan 'Pantau Live WS' untuk menghitung cancel order antar-detik."
                        )
                    return {
                        "live": True,
                        "source": source,
                        "symbol": sym,
                        "inputs": inputs,
                        "analysis": analysis,
                    }
                debug.append(f"{source}: empty depth/trades")
            except Exception as exc:  # noqa: BLE001
                debug.append(f"{source}: {exc}")

    # Graceful fallback: derive realistic flow & volatility proxy from latest OHLCV candles
    rows = _sanitize_candles(fallback_candles or [])
    if rows:
        last = rows[-1]
        mid = last["close"]
        vol_slice = max(last["volume"] / 2880.0, 1.0)  # ~30-second slice of daily volume
        buy_ratio = clamp(last["takerBuyBase"] / max(last["volume"], EPS), 0.15, 0.85)
        tb = vol_slice * buy_ratio
        ts = vol_slice * (1.0 - buy_ratio)
        bid_depth = vol_slice * 2.4 * (0.7 + 0.6 * buy_ratio)
        ask_depth = vol_slice * 2.4 * (0.7 + 0.6 * (1.0 - buy_ratio))
        hl_sigma_d = math.sqrt(
            (1.0 / (4.0 * math.log(2.0)))
            * (math.log(max(last["high"], EPS) / max(last["low"], EPS)) ** 2)
        )
        sigma_s = clamp((max(hl_sigma_d, 0.015) / math.sqrt(86_400.0)) * 3.5, 0.00025, 0.004)
        est_spread_bps = clamp(hl_sigma_d * 220.0, 2.5, 25.0)
    else:
        mid = 100.0
        bid_depth, ask_depth, tb, ts, sigma_s, est_spread_bps = 100.0, 100.0, 40.0, 40.0, 0.0006, 8.0

    analysis = analyze_orderbook_fp(
        bid_depth=bid_depth,
        ask_depth=ask_depth,
        far_bid_depth=0.0,
        far_ask_depth=0.0,
        taker_buy_vol=tb,
        taker_sell_vol=ts,
        canceled_bid_vol=0.0,
        canceled_ask_vol=0.0,
        spread_bps=est_spread_bps,
        sigma_per_sec=sigma_s,
        horizon_sec=horizon_sec,
        stable_band=stable_band,
        mid_price=mid,
    )
    inputs = {
        "bidDepth": round(bid_depth, 4),
        "askDepth": round(ask_depth, 4),
        "farBidDepth": 0.0,
        "farAskDepth": 0.0,
        "takerBuyVol": round(tb, 4),
        "takerSellVol": round(ts, 4),
        "canceledBidVol": 0.0,
        "canceledAskVol": 0.0,
        "sigmaPerSec": round(sigma_s, 6),
        "spreadBps": round(est_spread_bps, 2),
        "priceChangeBps": None,
        "midPrice": round(mid, 8),
        "microPrice": analysis["microstructure"]["microPrice"],
        "decayLambda": analysis["microstructure"]["decayLambda"],
        "tradeWindowSec": None,
        "cancelDiffAvailable": False,
    }
    return {
        "live": False,
        "source": "kline-proxy-fallback",
        "symbol": sym,
        "warning": "Endpoint order-book live tidak terjangkau dari server ini — menampilkan estimasi awal dari candle OHLCV. Gunakan preset skenario, slider simulator, atau tombol Live WS dari browser.",
        "debug": debug[:6],
        "inputs": inputs,
        "analysis": analysis,
    }


# ---------------------------------------------------------------------------
# Mode 3 — Chronological Walk-Forward Calibration Audit (no lookahead)
# ---------------------------------------------------------------------------

def backtest_candles_fp(
    universe_candles: dict[str, list[dict[str, Any]]],
    *,
    horizon_days: int = 7,
    stable_band: float = 0.05,
    step_days: int = 7,
    window: int = DEFAULT_WINDOW,
    source_label: str = "unknown",
    live: bool = False,
) -> dict[str, Any]:
    """Strict walk-forward evaluation of Fokker–Planck zone probabilities (no lookahead)."""
    h = int(clamp(horizon_days, 1, 30))
    step = int(clamp(step_days, 2, 30))
    band = clamp(_finite_float(stable_band, 0.05), 0.01, 0.30)
    min_hist = max(MIN_CANDLES, 2 * window + 10)

    samples: list[dict[str, Any]] = []
    per_symbol: list[dict[str, Any]] = []
    skipped: list[str] = []

    for sym, raw_candles in universe_candles.items():
        rows = _sanitize_candles(raw_candles)
        max_anchor = len(rows) - h
        if max_anchor <= min_hist:
            skipped.append(sym)
            continue

        sym_brier: list[float] = []
        sym_hits = 0
        sym_pred_stab: list[float] = []
        for anchor in range(min_hist, max_anchor + 1, step):
            past = rows[:anchor]
            c_now = past[-1]["close"]
            c_fut = rows[anchor - 1 + h]["close"]
            realized_log_ret = math.log(c_fut / c_now)

            res = analyze_candles_fp(
                past,
                horizon_days=float(h),
                stable_band=band,
                window=window,
                include_curve=False,
                n_grid=161,
            )
            if not res.get("ok"):
                continue

            m = res["metrics"]
            p_s = float(m["pStable"])
            p_d = float(m["pDownside"])
            p_u = float(m["pUpside"])
            p_c = float(m["pTailCrash"])

            y_s = 1.0 if abs(realized_log_ret) <= band else 0.0
            y_d = 1.0 if realized_log_ret < -band else 0.0
            y_u = 1.0 if realized_log_ret > band else 0.0
            y_c = 1.0 if realized_log_ret < -2.0 * band else 0.0

            brier_s = (p_s - y_s) ** 2
            brier_multi = ((p_d - y_d) ** 2 + (p_s - y_s) ** 2 + (p_u - y_u) ** 2) / 3.0
            sym_brier.append(brier_s)
            sym_pred_stab.append(p_s)
            sym_hits += int(y_s)

            samples.append(
                {
                    "symbol": sym,
                    "pStable": p_s,
                    "pDownside": p_d,
                    "pUpside": p_u,
                    "pTailCrash": p_c,
                    "yStable": y_s,
                    "yDownside": y_d,
                    "yUpside": y_u,
                    "yTailCrash": y_c,
                    "brierStable": brier_s,
                    "brierMulti": brier_multi,
                }
            )

        if sym_brier:
            per_symbol.append(
                {
                    "symbol": sym,
                    "samples": len(sym_brier),
                    "predStable": round(mean(sym_pred_stab), 4),
                    "actualStable": round(sym_hits / len(sym_brier), 4),
                    "brierStable": round(mean(sym_brier), 4),
                }
            )

    if not samples:
        return {
            "ok": False,
            "reason": "Sampel walk-forward tidak cukup untuk horizon dan jendela candle yang dipilih.",
            "skipped": skipped,
        }

    base_s = mean(s["yStable"] for s in samples)
    base_d = mean(s["yDownside"] for s in samples)
    base_u = mean(s["yUpside"] for s in samples)
    base_c = mean(s["yTailCrash"] for s in samples)

    pred_s = mean(s["pStable"] for s in samples)
    pred_d = mean(s["pDownside"] for s in samples)
    pred_u = mean(s["pUpside"] for s in samples)
    pred_c = mean(s["pTailCrash"] for s in samples)

    model_brier_s = mean(s["brierStable"] for s in samples)
    clim_brier_s = mean((base_s - s["yStable"]) ** 2 for s in samples)
    skill_score = 1.0 - (model_brier_s / clim_brier_s) if clim_brier_s > EPS else 0.0

    model_brier_multi = mean(s["brierMulti"] for s in samples)
    clim_brier_multi = mean(
        ((base_d - s["yDownside"]) ** 2 + (base_s - s["yStable"]) ** 2 + (base_u - s["yUpside"]) ** 2) / 3.0
        for s in samples
    )

    # 5-bin reliability curve for P(stable)
    bins: list[dict[str, Any]] = []
    for b_idx in range(5):
        lo = b_idx * 0.2
        hi = (b_idx + 1) * 0.2
        bucket = [
            s for s in samples
            if (lo <= s["pStable"] < hi) or (b_idx == 4 and s["pStable"] == 1.0)
        ]
        bins.append(
            {
                "bin": f"{int(lo*100)}–{int(hi*100)}%",
                "count": len(bucket),
                "predicted": round(mean(x["pStable"] for x in bucket), 4) if bucket else None,
                "actual": round(mean(x["yStable"] for x in bucket), 4) if bucket else None,
            }
        )

    cal_gap = abs(pred_s - base_s)
    beats_baseline = model_brier_s < clim_brier_s and model_brier_multi <= clim_brier_multi
    status = "TERKALIBRASI_BAIK" if (beats_baseline and cal_gap <= 0.08) else "PERLU_KALIBRASI_LANJUT"

    per_symbol.sort(key=lambda row: row["brierStable"])

    return {
        "ok": True,
        "generatedAt": datetime.now(timezone.utc).isoformat(),
        "source": source_label,
        "live": live,
        "params": {
            "horizonDays": h,
            "stableBand": band,
            "stepDays": step,
            "symbolsEvaluated": len(per_symbol),
            "symbolsSkipped": skipped,
        },
        "samples": len(samples),
        "scores": {
            "brierStable": round(model_brier_s, 4),
            "baselineBrierStable": round(clim_brier_s, 4),
            "brierSkillScore": round(skill_score, 4),
            "brierMultiZone": round(model_brier_multi, 4),
            "baselineBrierMultiZone": round(clim_brier_multi, 4),
            "calibrationGapStable": round(cal_gap, 4),
        },
        "coverage": [
            {
                "zone": f"Stabil (±{band*100:g}%)",
                "key": "stable",
                "predicted": round(pred_s, 4),
                "actual": round(base_s, 4),
                "gap": round(pred_s - base_s, 4),
            },
            {
                "zone": f"Downside (<-{band*100:g}%)",
                "key": "downside",
                "predicted": round(pred_d, 4),
                "actual": round(base_d, 4),
                "gap": round(pred_d - base_d, 4),
            },
            {
                "zone": f"Upside (>+{band*100:g}%)",
                "key": "upside",
                "predicted": round(pred_u, 4),
                "actual": round(base_u, 4),
                "gap": round(pred_u - base_u, 4),
            },
            {
                "zone": f"Tail Crash (<-{2*band*100:g}%)",
                "key": "tailCrash",
                "predicted": round(pred_c, 4),
                "actual": round(base_c, 4),
                "gap": round(pred_c - base_c, 4),
            },
        ],
        "reliabilityBins": bins,
        "perSymbol": per_symbol,
        "verdict": {
            "status": status,
            "label": "Terkalibrasi Baik" if status == "TERKALIBRASI_BAIK" else "Perlu Kalibrasi Lanjut",
            "reason": (
                f"Brier score model ({model_brier_s:.4f}) lebih rendah dari baseline naif ({clim_brier_s:.4f}, "
                f"skill {skill_score*100:+.1f}%) dengan selisih rata-rata P(stabil) {cal_gap*100:.1f} poin persentase."
                if status == "TERKALIBRASI_BAIK"
                else f"Brier score model {model_brier_s:.4f} vs baseline {clim_brier_s:.4f}; selisih rata-rata P(stabil) "
                f"{cal_gap*100:.1f} poin persentase — sesuaikan skala difusi/drift pada rezim pasar ini."
            ),
        },
    }


def formula_spec() -> dict[str, Any]:
    """Self-documenting metadata for the Fokker–Planck equation & 3-pillar anti-spoof model."""
    return {
        "name": "1D Itô Fokker–Planck (Kolmogorov Forward Equation) + 3-Pillar Anti-Spoof Microstructure",
        "version": "2.0",
        "pde": "∂P(x,t)/∂t = -∂/∂x [ μ(x,t) P(x,t) ] + (1/2) ∂²/∂x² [ D(x,t) P(x,t) ]",
        "fluxForm": "∂P/∂t = -∂J/∂x,   J = [μ - (1/2)∂D/∂x] P - (1/2) D ∂P/∂x,   J(-L,t) = J(+L,t) = 0",
        "scheme": "Implicit Chang–Cooper finite-volume (Bernoulli flux B(w) = w/(e^w - 1)) + Thomas O(N) tridiagonal",
        "stateVariable": "x = ln(S_t / S_0)  (log-return terhadap harga saat ini)",
        "microstructurePillars": {
            "takerFlowPriority": "μ(x,t) = (α·TFI_eff + β·OBI_λ)·σ + μ_spoof·e^{-t/τ} - κ·(x - x_micro),  α/(α+β) ≥ 80% + Iceberg Absorption Guard",
            "microPrice": "P_micro = P_bid·(1 - I) + P_ask·I,   I = Q_bid^eff / (Q_bid^eff + Q_ask^eff),   x_micro = ln(P_micro / P_mid)",
            "exponentialDecay": "w(d) = exp(-λ·d),   d = |p_k - P_mid| / P_mid,   λ ∈ [450, 800] (adaptif volatilitas; w(0.5%) ≈ 2–10%, w(1.0%) < 1%)",
        },
        "zones": {
            "pStable": "∫_{-a}^{+a} P(x,T) dx",
            "pDownside": "∫_{-∞}^{-a} P(x,T) dx",
            "pUpside": "∫_{+a}^{+∞} P(x,T) dx",
            "pTailCrash": "∫_{-∞}^{-2a} P(x,T) dx",
            "conservation": "P_downside + P_stable + P_upside = 1 (100%)",
        },
        "presets": [
            {
                "id": "calm",
                "label": "Pasar Tenang",
                "subtitle": "TFI, Micro-Price & buku seimbang",
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
                "subtitle": "Tembok ask raksasa diredam e^(-λd)",
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
                "id": "spoof_sell",
                "label": "Spoofing Jual (Dekat)",
                "subtitle": "Tembok ask dekat + cancel tinggi",
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
                "id": "spoof_buy",
                "label": "Spoofing Beli (Dekat)",
                "subtitle": "Tembok bid dekat + cancel tinggi",
                "params": {
                    "bidDepth": 260,
                    "askDepth": 45,
                    "farBidDepth": 0,
                    "farAskDepth": 0,
                    "wallDistancePct": 0.008,
                    "takerBuyVol": 41,
                    "takerSellVol": 39,
                    "canceledBidVol": 340,
                    "canceledAskVol": 8,
                    "spreadBps": 10,
                    "priceChangeBps": None,
                    "sigmaPerSec": 0.0007,
                    "horizonSec": 30,
                    "stableBand": 0.004,
                },
            },
            {
                "id": "iceberg_buy",
                "label": "Absorpsi Iceberg Beli",
                "subtitle": "Market sell deras tapi harga tertahan",
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
                "id": "real_dump",
                "label": "Panic Dump Riil",
                "subtitle": "Market sell nyata menghantam bid",
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
                "subtitle": "Market buy nyata + ask tipis",
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
                "label": "Whipsaw / Rilis Makro",
                "subtitle": "Volatilitas meledak 2 arah",
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

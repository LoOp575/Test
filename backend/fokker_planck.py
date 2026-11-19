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
        "summary": "Kedalaman bid/ask dan aliran eksekusi seimbang; kurva P(x,t) tetap rapat di sekitar mid-price.",
    },
    "SPOOF_SELL_WALL": {
        "label": "Indikasi Spoof Jual",
        "tone": "watch",
        "summary": "Tembok ask tebal disertai cancel tinggi tanpa eksekusi jual sepadan — tekanan turun bersifat transien.",
    },
    "SPOOF_BUY_WALL": {
        "label": "Indikasi Spoof Beli",
        "tone": "watch",
        "summary": "Tembok bid tebal disertai cancel tinggi tanpa eksekusi beli sepadan — dorongan naik rawan semu.",
    },
    "REAL_BUY_PRESSURE": {
        "label": "Tekanan Beli Riil",
        "tone": "long",
        "summary": "Agresor taker-buy dan kedalaman bid searah menggeser distribusi secara persisten ke zona pump.",
    },
    "REAL_SELL_PRESSURE": {
        "label": "Tekanan Jual Riil",
        "tone": "avoid",
        "summary": "Agresor taker-sell menghantam bid secara konsisten; massa distribusi bergeser kuat ke zona dump.",
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
# ---------------------------------------------------------------------------

def analyze_orderbook_fp(
    *,
    bid_depth: float,
    ask_depth: float,
    taker_buy_vol: float,
    taker_sell_vol: float,
    canceled_bid_vol: float = 0.0,
    canceled_ask_vol: float = 0.0,
    sigma_per_sec: float = 0.0007,
    horizon_sec: float = 30.0,
    stable_band: float = 0.004,
    tau_spoof_sec: float = 6.0,
    mid_price: Optional[float] = None,
) -> dict[str, Any]:
    """Evolve short-horizon Fokker–Planck distribution from order-book & aggressor flow."""
    bid_d = max(0.0, _finite_float(bid_depth, 0.0))
    ask_d = max(0.0, _finite_float(ask_depth, 0.0))
    tb = max(0.0, _finite_float(taker_buy_vol, 0.0))
    ts = max(0.0, _finite_float(taker_sell_vol, 0.0))
    cb = max(0.0, _finite_float(canceled_bid_vol, 0.0))
    ca = max(0.0, _finite_float(canceled_ask_vol, 0.0))

    sigma_s = clamp(_finite_float(sigma_per_sec, 0.0007), 1e-5, 0.05)
    horizon_s = clamp(_finite_float(horizon_sec, 30.0), 1.0, 600.0)
    band = clamp(_finite_float(stable_band, 0.004), 0.0005, 0.15)
    tau = clamp(_finite_float(tau_spoof_sec, 6.0), 1.0, 120.0)

    book_sum = bid_d + ask_d
    trade_sum = tb + ts
    obi = (bid_d - ask_d) / book_sum if book_sum > EPS else 0.0
    tfi = (tb - ts) / trade_sum if trade_sum > EPS else 0.0

    ask_cancel_rate = ca / (ask_d + ca) if (ask_d + ca) > EPS else 0.0
    bid_cancel_rate = cb / (bid_d + cb) if (bid_d + cb) > EPS else 0.0

    # Effective book imbalance includes volume that just flashed & canceled in the window,
    # catching both persistent flickering walls and large walls pulled completely before fill.
    eff_bid = bid_d + cb
    eff_ask = ask_d + ca
    obi_eff = (eff_bid - eff_ask) / (eff_bid + eff_ask) if (eff_bid + eff_ask) > EPS else obi

    # Same-side spoof score: passive wall opposing/exceeding real execution + high cancel churn
    ask_spoof = clamp(max(0.0, tfi - min(obi, obi_eff)) * ask_cancel_rate, 0.0, 1.0)
    bid_spoof = clamp(max(0.0, max(obi, obi_eff) - tfi) * bid_cancel_rate, 0.0, 1.0)
    spoof_index = max(ask_spoof, bid_spoof)
    net_spoof = bid_spoof - ask_spoof  # < 0 fake ask/sell wall, > 0 fake bid/buy wall

    genuine_book_weight = 1.0 - 0.70 * spoof_index
    mu_real = (0.30 * tfi + 0.15 * obi * genuine_book_weight) * sigma_s
    mu_spoof_init = 0.55 * net_spoof * sigma_s
    kappa = (0.015 + 0.075 * spoof_index) * (1.0 - 0.5 * abs(tfi))

    d0 = sigma_s * sigma_s
    down_asym = 0.45 * max(0.0, -tfi)
    tail_eta = 0.20 + 0.35 * spoof_index

    spread = max(sigma_s * math.sqrt(horizon_s), band)
    drift_reach = (abs(mu_real) + abs(mu_spoof_init)) * horizon_s
    domain = max(6.5 * spread + drift_reach, 5.0 * band)
    n_steps = max(24, min(60, int(round(horizon_s))))

    sol = solve_fokker_planck(
        lambda x, t: mu_real + mu_spoof_init * math.exp(-t / tau) - kappa * x,
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
    if spoof_index >= 0.28 and abs(tfi) < 0.28:
        regime = "SPOOF_SELL_WALL" if net_spoof < 0 else "SPOOF_BUY_WALL"
    elif f.p_downside >= 0.42 and tfi <= -0.22:
        regime = "REAL_SELL_PRESSURE"
    elif f.p_upside >= 0.42 and tfi >= 0.22:
        regime = "REAL_BUY_PRESSURE"
    elif f.p_stable < 0.50:
        regime = "HIGH_VOL_WHIPSAW"
    else:
        regime = "STABLE_BALANCED"

    meta = MICRO_REGIME_META[regime]
    notes: list[str] = [
        f"Order Book Imbalance (OBI) {obi:+.2f} vs Trade Flow Imbalance (TFI) {tfi:+.2f} "
        f"→ Indeks Spoofing {spoof_index*100:.0f}% (cancel bid {bid_cancel_rate*100:.0f}%, ask {ask_cancel_rate*100:.0f}%).",
        f"Pada t={horizon_s:g} detik (zona stabil ±{band*100:.2f}%): P(stabil)={f.p_stable*100:.1f}%, "
        f"P(dump)={f.p_downside*100:.1f}%, P(pump)={f.p_upside*100:.1f}%, P(crash ekor)={f.p_tail_crash*100:.1f}%.",
    ]
    if regime in {"SPOOF_SELL_WALL", "SPOOF_BUY_WALL"}:
        side = "jual (ask)" if regime == "SPOOF_SELL_WALL" else "beli (bid)"
        notes.append(
            f"Divergensi terdeteksi: tembok {side} mendominasi buku order dan banyak di-cancel tanpa eksekusi sepadan. "
            f"Komponen drift spoofing meluruh dengan τ={tau:g} dtk dan ditahan gaya balik κ={kappa:.3f}."
        )
    elif regime == "REAL_SELL_PRESSURE":
        notes.append(
            "Penjualan agresor (market sell) mengonfirmasi tekanan ask — drift negatif bersifat persisten dan memperbesar ekor dump."
        )
    elif regime == "REAL_BUY_PRESSURE":
        notes.append(
            "Pembelian agresor (market buy) mengonfirmasi dukungan bid — drift positif menggeser kurva secara konsisten ke atas."
        )
    elif regime == "HIGH_VOL_WHIPSAW":
        notes.append(
            "Difusi volatilitas mendominasi drift: puncak P(x,t) merata dan massa probabilitas menyebar ke kedua sisi sekaligus."
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
            "obi": round(obi, 4),
            "tfi": round(tfi, 4),
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
    if mid_price and mid_price > 0:
        out["midPrice"] = mid_price
        out["priceBands"] = {
            "crashPrice": round(mid_price * math.exp(-2.0 * band), 8),
            "lowerStable": round(mid_price * math.exp(-band), 8),
            "currentPrice": round(mid_price, 8),
            "upperStable": round(mid_price * math.exp(band), 8),
            "pumpPrice": round(mid_price * math.exp(2.0 * band), 8),
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


def _parse_book_side(levels: Any) -> tuple[float, float, dict[str, float]]:
    """Return (total_base_qty, best_price, price->qty map)."""
    total = 0.0
    best = 0.0
    level_map: dict[str, float] = {}
    if not isinstance(levels, list):
        return total, best, level_map
    for idx, entry in enumerate(levels[:20]):
        if not isinstance(entry, (list, tuple)) or len(entry) < 2:
            continue
        price = _finite_float(entry[0], 0.0)
        qty = max(0.0, _finite_float(entry[1], 0.0))
        if price <= 0:
            continue
        if idx == 0:
            best = price
        total += qty
        level_map[f"{price:.8f}"] = qty
    return total, best, level_map


def _parse_recent_trades(raw: Any) -> tuple[float, float, float, float]:
    """Return (taker_buy_vol, taker_sell_vol, sigma_per_sec, span_sec)."""
    if not isinstance(raw, list) or not raw:
        return 0.0, 0.0, 0.0006, 0.0

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
        return taker_buy, taker_sell, 0.0006, 0.0

    prices_ts.sort(key=lambda pair: pair[0])
    span = max(0.25, prices_ts[-1][0] - prices_ts[0][0])
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

    return taker_buy, taker_sell, clamp(sigma_s, 3e-5, 0.015), span


def _estimate_cancellations(
    symbol: str,
    bid_map: dict[str, float],
    ask_map: dict[str, float],
    taker_buy: float,
    taker_sell: float,
) -> tuple[float, float, bool]:
    """Estimate unexecuted order disappearance between consecutive snapshots."""
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
    """Fetch top-20 order book + recent trades from existing exchange hosts, with safe fallback."""
    sym = symbol.upper().strip()
    debug: list[str] = []

    if not prefer_seed:
        for source, base in _microstructure_hosts():
            try:
                depth_url = f"{base}/depth?symbol={sym}&limit=20"
                trades_url = f"{base}/trades?symbol={sym}&limit=200"
                depth_raw, trades_raw = await asyncio.gather(
                    market_data._fetch_json(client, depth_url, timeout=4.5),
                    market_data._fetch_json(client, trades_url, timeout=4.5),
                )
                bid_depth, best_bid, bid_map = _parse_book_side(depth_raw.get("bids"))
                ask_depth, best_ask, ask_map = _parse_book_side(depth_raw.get("asks"))
                tb, ts, sigma_s, span_s = _parse_recent_trades(trades_raw)
                if bid_depth > 0 and ask_depth > 0 and best_bid > 0 and best_ask > 0:
                    mid = 0.5 * (best_bid + best_ask)
                    spread_bps = ((best_ask - best_bid) / mid) * 10_000.0 if mid > 0 else 0.0
                    c_bid, c_ask, has_diff = _estimate_cancellations(sym, bid_map, ask_map, tb, ts)
                    inputs = {
                        "bidDepth": round(bid_depth, 6),
                        "askDepth": round(ask_depth, 6),
                        "takerBuyVol": round(tb, 6),
                        "takerSellVol": round(ts, 6),
                        "canceledBidVol": round(c_bid, 6),
                        "canceledAskVol": round(c_ask, 6),
                        "sigmaPerSec": round(sigma_s, 6),
                        "spreadBps": round(spread_bps, 3),
                        "tradeWindowSec": round(span_s, 1),
                        "cancelDiffAvailable": has_diff,
                    }
                    analysis = analyze_orderbook_fp(
                        bid_depth=bid_depth,
                        ask_depth=ask_depth,
                        taker_buy_vol=tb,
                        taker_sell_vol=ts,
                        canceled_bid_vol=c_bid,
                        canceled_ask_vol=c_ask,
                        sigma_per_sec=sigma_s,
                        horizon_sec=horizon_sec,
                        stable_band=stable_band,
                        mid_price=mid,
                    )
                    if not has_diff:
                        analysis["notes"].append(
                            "Snapshot awal: klik 'Ambil Snapshot' sekali lagi atau aktifkan 'Pantau Live WS' untuk menghitung volume cancel order antar-detik."
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
        # Approximate resting depth proportional to flow balance
        bid_depth = vol_slice * 2.4 * (0.7 + 0.6 * buy_ratio)
        ask_depth = vol_slice * 2.4 * (0.7 + 0.6 * (1.0 - buy_ratio))
        hl_sigma_d = math.sqrt(
            (1.0 / (4.0 * math.log(2.0))) * (math.log(max(last["high"], EPS) / max(last["low"], EPS)) ** 2)
        )
        # Convert daily volatility to per-second scale (sqrt(86400) ≈ 293.9), slightly boosted for intraday clustering
        sigma_s = clamp((max(hl_sigma_d, 0.015) / math.sqrt(86_400.0)) * 3.5, 0.00025, 0.004)
    else:
        mid = 100.0
        bid_depth, ask_depth, tb, ts, sigma_s = 100.0, 100.0, 40.0, 40.0, 0.0006

    inputs = {
        "bidDepth": round(bid_depth, 4),
        "askDepth": round(ask_depth, 4),
        "takerBuyVol": round(tb, 4),
        "takerSellVol": round(ts, 4),
        "canceledBidVol": 0.0,
        "canceledAskVol": 0.0,
        "sigmaPerSec": round(sigma_s, 6),
        "spreadBps": None,
        "tradeWindowSec": None,
        "cancelDiffAvailable": False,
    }
    analysis = analyze_orderbook_fp(
        bid_depth=bid_depth,
        ask_depth=ask_depth,
        taker_buy_vol=tb,
        taker_sell_vol=ts,
        canceled_bid_vol=0.0,
        canceled_ask_vol=0.0,
        sigma_per_sec=sigma_s,
        horizon_sec=horizon_sec,
        stable_band=stable_band,
        mid_price=mid,
    )
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
    """Self-documenting metadata for the Fokker–Planck equation & scheme."""
    return {
        "name": "1D Itô Fokker–Planck (Kolmogorov Forward Equation)",
        "pde": "∂P(x,t)/∂t = -∂/∂x [ μ(x,t) P(x,t) ] + (1/2) ∂²/∂x² [ D(x,t) P(x,t) ]",
        "fluxForm": "∂P/∂t = -∂J/∂x,   J = [μ - (1/2)∂D/∂x] P - (1/2) D ∂P/∂x,   J(-L,t) = J(+L,t) = 0",
        "scheme": "Implicit Chang–Cooper finite-volume (Bernoulli flux B(w) = w/(e^w - 1)) + Thomas O(N) tridiagonal",
        "stateVariable": "x = ln(S_t / S_0)  (log-return terhadap harga saat ini)",
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
                "subtitle": "Bid/ask & flow seimbang",
                "params": {
                    "bidDepth": 120,
                    "askDepth": 120,
                    "takerBuyVol": 45,
                    "takerSellVol": 45,
                    "canceledBidVol": 10,
                    "canceledAskVol": 10,
                    "sigmaPerSec": 0.0005,
                    "horizonSec": 30,
                    "stableBand": 0.004,
                },
            },
            {
                "id": "spoof_sell",
                "label": "Spoofing Jual",
                "subtitle": "Tembok ask palsu + cancel tinggi",
                "params": {
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
            },
            {
                "id": "spoof_buy",
                "label": "Spoofing Beli",
                "subtitle": "Tembok bid palsu + cancel tinggi",
                "params": {
                    "bidDepth": 260,
                    "askDepth": 45,
                    "takerBuyVol": 41,
                    "takerSellVol": 39,
                    "canceledBidVol": 340,
                    "canceledAskVol": 8,
                    "sigmaPerSec": 0.0007,
                    "horizonSec": 30,
                    "stableBand": 0.004,
                },
            },
            {
                "id": "real_dump",
                "label": "Panic Dump Riil",
                "subtitle": "Market sell menghantam bid",
                "params": {
                    "bidDepth": 45,
                    "askDepth": 220,
                    "takerBuyVol": 18,
                    "takerSellVol": 95,
                    "canceledBidVol": 15,
                    "canceledAskVol": 25,
                    "sigmaPerSec": 0.0011,
                    "horizonSec": 30,
                    "stableBand": 0.004,
                },
            },
            {
                "id": "real_pump",
                "label": "Breakout Beli Riil",
                "subtitle": "Market buy menyapu ask",
                "params": {
                    "bidDepth": 230,
                    "askDepth": 55,
                    "takerBuyVol": 95,
                    "takerSellVol": 22,
                    "canceledBidVol": 20,
                    "canceledAskVol": 12,
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
                    "takerBuyVol": 85,
                    "takerSellVol": 85,
                    "canceledBidVol": 45,
                    "canceledAskVol": 45,
                    "sigmaPerSec": 0.0020,
                    "horizonSec": 30,
                    "stableBand": 0.004,
                },
            },
        ],
    }

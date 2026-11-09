"""
Meridian — Crypto Quality Screener
Adapted "deferred-revenue quality" model for crypto assets.

The formulas in this module are a documented adaptation of the two models from
the reference slides (see docs/SCREENING_MODEL.md):

  Model 1 — Deferred-Revenue Quality  ->  Demand-Quality model (organic,
             verified on-chain demand growth vs. price, demand retention,
             buy-side conversion, leading-gap to price).
  Model 2 — Filing Instability        ->  Instability / Red-Flag model
             (abnormal-move event frequency, materiality of those moves,
             clustering of abnormal moves). NOTE: internal-control data has
             no public crypto equivalent and is explicitly not scored.

Everything here is pure computation. No I/O happens in this module so it can be
unit tested deterministically.

Candle dict shape (daily):
  {date, open, high, low, close, volume, quoteVolume, trades, takerBuyBase, takerBuyQuote}
"""

from __future__ import annotations

from math import exp, log
from statistics import mean, pstdev
from typing import Any, Optional

# ---------------------------------------------------------------------------
# Tunable constants (documented in docs/SCREENING_MODEL.md)
# ---------------------------------------------------------------------------

LAMBDA_FLOW = 3.0        # S_flow = exp(-LAMBDA_FLOW * steepening of engagement decline)
#                          (only declines in participation growth are penalised; acceleration is not "instability")
ALPHA_NRR = 0.5          # weight: demand NRR-analog (net demand retention)
BETA_GRR = 0.8           # weight: GRR-analog (share of buy-side dominant days)
GAMMA_CHURN = 1.1        # penalty: participation exodus
SAT_K_QDR = 0.45         # saturation scale for Q_DR raw -> 0..100
SAT_K_SDR = 0.12         # saturation scale for signal raw -> 0..100
SAT_K_FILE = 0.35        # saturation scale for instability raw -> 0..100
G_MAX = 0.90             # blow-off cap: 30d organic growth above this => no long
LONG_SDR = 55.0          # minimum signal score for a LONG candidate
LONG_PACC = 0.60         # minimum model probability of revenue/demand acceleration
WATCH_SDR = 38.0         # minimum signal score for WATCH
WATCH_PACC = 0.50        # minimum probability for WATCH
FILE_AVOID = 55.0        # instability score at/above which a name is excluded from longs
PBAD_AVOID = 0.65        # adverse-event probability at/above which we flag avoid
VWAP_WINDOW = 20         # daily VWAP window (typical-price weighted by volume)
WINDOW = 30              # comparison window in days (cur vs prev vs prior)

EPS = 1e-9


# ---------------------------------------------------------------------------
# Small numeric helpers
# ---------------------------------------------------------------------------

def sat(x: float) -> float:
    """Saturating map R -> (-1, 1). sat(0)=0, |sat|<1. Used to bound raw growth into score units."""
    return x / (1.0 + abs(x))


def pos_sat(x: float) -> float:
    """Positive part of sat: result in [0, 1)."""
    return max(0.0, sat(x))


def score100(raw: float, k: float) -> float:
    """Map a non-negative raw score into 0..100 with diminishing returns: 100*raw/(raw+k)."""
    raw = max(0.0, raw)
    return 100.0 * raw / (raw + k)


def sigmoid(z: float) -> float:
    z = max(-30.0, min(30.0, z))
    return 1.0 / (1.0 + exp(-z))


def safe_div(a: float, b: float, default: float = 0.0) -> float:
    if b is None or abs(b) < EPS:
        return default
    return a / b


def clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


# ---------------------------------------------------------------------------
# Window aggregation
# ---------------------------------------------------------------------------

def _window_sums(candles: list[dict], a: int, b: int) -> dict[str, float]:
    """Sum volumes over candles[a:b]."""
    seg = candles[a:b]
    return {
        "quoteVolume": sum(c["quoteVolume"] for c in seg),
        "volume": sum(c["volume"] for c in seg),
        "trades": sum(c["trades"] for c in seg),
        "takerBuyBase": sum(c["takerBuyBase"] for c in seg),
        "takerBuyQuote": sum(c["takerBuyQuote"] for c in seg),
        "days": len(seg),
    }


def _growth(now: float, before: float) -> float:
    """Growth rate now vs before, guarded against zero/negative bases."""
    if before <= EPS:
        return 0.0
    return (now - before) / before


def _daily_returns(closes: list[float]) -> list[float]:
    out = []
    for i in range(1, len(closes)):
        prev = closes[i - 1]
        if prev > EPS:
            out.append(closes[i] / prev - 1.0)
    return out


def _vwap(candles: list[dict], window: int = VWAP_WINDOW) -> float:
    seg = candles[-window:]
    num, den = 0.0, 0.0
    for c in seg:
        typical = (c["high"] + c["low"] + c["close"]) / 3.0
        num += typical * c["volume"]
        den += c["volume"]
    return safe_div(num, den, seg[-1]["close"] if seg else 0.0)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def compute_features(candles: list[dict[str, Any]]) -> dict[str, Any]:
    """Compute the full adapted model for one asset.

    Returns a dict with raw features, 0-100 scores, probabilities, the verdict
    and human-readable reasons. `ok` is False when history is insufficient.
    """
    n = len(candles)
    if n < 61:
        return {
            "ok": False,
            "reason": f"Need at least 61 daily candles, got {n}.",
            "verdict": "INSUFFICIENT_DATA",
        }

    closes = [c["close"] for c in candles]
    price = closes[-1]

    # Window boundaries (cur = latest WINDOW days, prev = the WINDOW before, prior = before that)
    cur = _window_sums(candles, n - WINDOW, n)
    prev = _window_sums(candles, n - 2 * WINDOW, n - WINDOW)
    has_prior = n >= 3 * WINDOW
    prior = _window_sums(candles, n - 3 * WINDOW, n - 2 * WINDOW) if has_prior else None

    # --- Model 1: demand quality -------------------------------------------
    # Price growth over the comparison window (uses the close just before cur).
    close_prev_edge = closes[n - WINDOW - 1]
    close_prior_edge = closes[n - 2 * WINDOW - 1] if n - 2 * WINDOW - 1 >= 0 else closes[0]

    g_rev = _growth(closes[-1], close_prev_edge)                       # revenue growth analog
    g_rev_prev = _growth(close_prev_edge, close_prior_edge)            # previous-window price growth
    g_qv = _growth(cur["quoteVolume"], prev["quoteVolume"])            # reported (USD) demand growth ~ G^DR
    g_org = _growth(cur["volume"], prev["volume"])                     # organic (unit) demand growth ~ G^org
    g_org_prev = _growth(prev["volume"], prior["volume"]) if prior else None  # for acceleration

    # Distortion removed = total USD demand growth minus unit (organic) growth.
    distortion = g_qv - g_org  # dominated by price appreciation + mix

    a_dr = (g_org - g_org_prev) if g_org_prev is not None else 0.0     # acceleration A^DR analog
    d_lead = g_org - g_rev                                             # lead D^lead (identical form to slides)

    # Buy-side (cash-conversion) quality: taker buy share of volume
    c_cash = safe_div(cur["takerBuyBase"], cur["volume"], 0.5)

    # Participation / engagement (trades) — churn-stability analog
    p_growth = _growth(cur["trades"], prev["trades"])
    p_growth_prev = _growth(prev["trades"], prior["trades"]) if prior else 0.0
    # Penalise only a *steepening* participation decline (each window worse than the
    # last). Improving or stable engagement must not be punished.
    s_flow = exp(-LAMBDA_FLOW * max(0.0, p_growth_prev - p_growth))    # S^churn analog
    churn = max(0.0, -p_growth)                                        # participation exodus

    # Retention quality — NRR / GRR analogs
    nrr = clamp(1.0 + g_org, 0.0, 2.0)                                 # net demand retention >1 => expansion
    grr_days = sum(
        1 for c in candles[-WINDOW:]
        if safe_div(c["takerBuyBase"], c["volume"], 0.5) >= 0.5
    )
    grr = grr_days / WINDOW                                            # share of buy-side dominant days
    q_ret = ALPHA_NRR * (nrr - 1.0) + BETA_GRR * grr - GAMMA_CHURN * churn

    # Quality score Q^DR analog (saturating G^org, as in the slides, then mapped 0-100)
    g_pos = pos_sat(g_org)
    q_dr_raw = g_pos * s_flow * max(1.0 + q_ret, 0.0) * (1.0 + c_cash)
    q_dr_100 = score100(q_dr_raw, SAT_K_QDR)

    # Expected recognition & gap — R̂^DR and GAP^DR analogs
    # R̂ (display metric): convertible organic demand multiple (organic growth x buy-side conversion).
    r_hat = (1.0 + g_org) * c_cash
    # GAP^DR analog used for scoring: the organic-demand lead, relative to price.
    # Positive => organic demand is running ahead of price appreciation (under-recognised).
    gap = safe_div(d_lead, max(1.0 + g_rev, 0.05), 0.0)
    gap_sat = sat(gap)

    # Signal score S^DR analog — same multiplicative logic as the slides, but
    # factors are blended with a weighted geometric mean (0.4/0.2/0.2/0.2) so the
    # score stays on a usable scale across assets while still punishing any
    # missing factor (a zero factor zeroes the score).
    a_pos, d_pos, gap_pos = pos_sat(a_dr), pos_sat(d_lead), max(0.0, gap_sat)
    if g_pos > 0.0 and a_pos > 0.0 and d_pos > 0.0 and gap_pos > 0.0:
        s_raw = g_pos**0.4 * a_pos**0.2 * d_pos**0.2 * gap_pos**0.2
    else:
        s_raw = 0.0
    s_dr_100 = score100(s_raw, SAT_K_SDR)

    # Heuristic probability of demand acceleration p^acc (documented as uncalibrated)
    z = (
        -0.75
        + 1.70 * g_pos
        + 1.30 * a_pos
        + 1.50 * d_pos
        + 1.20 * max(0.0, gap_sat)
        + 1.20 * (c_cash - 0.5)
        + 0.90 * (s_flow - 0.7)
    )
    p_acc = sigmoid(z)

    # --- Model 2: instability / red-flag score ------------------------------
    rets = _daily_returns(closes)
    ret_std = pstdev(rets) if len(rets) > 1 else 0.0
    ret_thr = max(2.5 * ret_std, 0.06)  # an "abnormal move" day

    # Volume shocks are detected on OLS-detrended log(quote volume) residuals so
    # that legitimately trending volumes are not flagged as abnormal.
    log_qv = [log(max(c["quoteVolume"], 1e-9)) for c in candles]
    m = float(n)
    sx = n * (n - 1) / 2.0
    sxx = (n - 1) * n * (2 * n - 1) / 6.0
    sy = sum(log_qv)
    sxy = sum(i * v for i, v in enumerate(log_qv))
    denom = m * sxx - sx * sx
    if abs(denom) > EPS:
        slope = (m * sxy - sx * sy) / denom
        intercept = (sy - slope * sx) / m
        resid = [v - (slope * i + intercept) for i, v in enumerate(log_qv)]
    else:
        resid = [0.0] * n
    resid_std = pstdev(resid) if len(resid) > 1 else 0.0
    qv_resid_thr = (2.5 * resid_std) if resid_std > 1e-6 else float("inf")

    event_days: list[int] = []
    for i in range(max(1, n - WINDOW), n):
        r = abs(closes[i] / closes[i - 1] - 1.0) if closes[i - 1] > EPS else 0.0
        vol_shock = abs(resid[i]) >= qv_resid_thr
        if r >= ret_thr or vol_shock:
            event_days.append(i)

    n_events = len(event_days)
    f_amend = n_events / WINDOW                                        # amendments-frequency analog
    if n_events:
        event_mag = mean(abs(closes[i] / closes[i - 1] - 1.0) for i in event_days if closes[i - 1] > EPS)
        q_corr_raw = safe_div(event_mag, mean(abs(r) for r in rets) + EPS, 1.0)
        q_corr = sat(q_corr_raw)                                       # normalized magnitude (0..1)
    else:
        q_corr = 0.0

    # Repeated-topic analog: share of events occurring within 2 days of another event
    if n_events >= 2:
        clustered = sum(
            1 for i in event_days
            if any(0 < abs(i - j) <= 2 for j in event_days if j != i)
        )
        r_topic = clustered / n_events
    else:
        r_topic = 0.0

    i_ic = 0.0  # internal-control data: not publicly observable for crypto -> explicitly unscored

    # Event frequency is normalised against ~10 abnormal days/month (an asset
    # with that many shocks is clearly unstable), matching the amendments-per-
    # period logic with a crypto-calibrated denominator.
    s_file_raw = (n_events / 10.0) * (0.35 + 0.65 * q_corr) * (1.0 + 1.2 * r_topic) * (1.0 + 0.0 * i_ic)
    s_file_100 = score100(s_file_raw, SAT_K_FILE)
    p_bad = sigmoid((s_file_100 - 52.0) / 12.0 + 0.5 * r_topic)

    # --- Confirmation & context ---------------------------------------------
    vwap_20 = _vwap(candles, VWAP_WINDOW)
    close_vs_vwap = safe_div(price - vwap_20, vwap_20, 0.0)
    avg_qv_30 = mean(c["quoteVolume"] for c in candles[-WINDOW:])
    rvol = safe_div(candles[-1]["quoteVolume"], avg_qv_30, 1.0) if avg_qv_30 else 1.0

    # --- Verdict & filters (order = override priority) -----------------------
    billing_distortion = g_qv > 0.05 and g_org <= 0.0
    low_quality_growth = g_rev > 0.05 and p_growth < -0.10 and c_cash < 0.47
    red_flag_exclude = s_file_100 >= FILE_AVOID or p_bad >= PBAD_AVOID

    reasons: list[str] = []
    if billing_distortion:
        reasons.append(
            f"Reported USD volume grew {g_qv:+.1%} but unit (organic) volume is flat/negative "
            f"({g_org:+.1%}) — demand is price/mix, not organic adoption (billing-distortion filter)."
        )
    if low_quality_growth:
        reasons.append(
            f"Price up {g_rev:+.1%} with participation down {p_growth:+.1%} and buy-side share {c_cash:.2f} — low-quality growth, reject."
        )
    if red_flag_exclude:
        reasons.append(
            f"{n_events} abnormal-move event(s) in 30d, instability score {s_file_100:.0f}/100 — excluded from the long universe (red-flag rule)."
        )
    if g_org > 0:
        reasons.append(f"Organic demand growth {g_org:+.1%} (vs reported USD {g_qv:+.1%}, price {g_rev:+.1%}); {distortion:+.1%} of reported growth was price/mix.")
    else:
        reasons.append(f"Organic demand growth is negative ({g_org:+.1%}).")
    if g_org_prev is not None:
        direction = "accelerating" if a_dr > 0 else "decelerating"
        reasons.append(f"Demand is {direction}: {g_org_prev:+.1%} prior window → {g_org:+.1%} this window (A={a_dr:+.1%}).")

    # Override order: low-quality growth is an active negative signal (reject);
    # distortion is absence of organic demand; red-flag is a hard exclusion.
    if low_quality_growth:
        verdict = "AVOID"
    elif billing_distortion:
        verdict = "NO_SIGNAL"
    elif red_flag_exclude:
        verdict = "AVOID"
    elif (
        s_dr_100 >= LONG_SDR
        and p_acc >= LONG_PACC
        and 0.0 < g_org <= G_MAX
        and price > vwap_20
        and not billing_distortion
    ):
        verdict = "LONG"
        reasons.append(
            f"Signal {s_dr_100:.0f}/100, p(accel)={p_acc:.2f}, price above VWAP-20 ({close_vs_vwap:+.1%}), growth below blow-off cap — long criteria met."
        )
    elif s_dr_100 >= WATCH_SDR and p_acc >= WATCH_PACC:
        verdict = "WATCH"
        reasons.append("Demand-quality construct is positive but at least one long criterion is not met — watchlist, do not enter yet.")
    else:
        verdict = "NEUTRAL"
        reasons.append("No edge: signal strength or probability below thresholds.")

    if verdict == "LONG" and price <= vwap_20:
        # Defensive: should be unreachable given the condition above.
        verdict = "WATCH"

    # Exit guidance for existing holders (mirrors the slides' exit rule)
    exit_reasons = []
    if g_org < 0:
        exit_reasons.append("organic demand growth turned negative")
    if s_flow < 0.4:
        exit_reasons.append("engagement trend unstable (flow-stability < 0.40)")
    if churn > 0.25:
        exit_reasons.append(f"participation exodus ({churn:+.1%} trades decay)")

    return {
        "ok": True,
        "verdict": verdict,
        "price": price,
        "features": {
            "gRev": g_rev,
            "gRevPrev": g_rev_prev,
            "gQuoteVolReported": g_qv,
            "gOrganic": g_org,
            "gOrganicPrev": g_org_prev,
            "distortionRemoved": distortion,
            "acceleration": a_dr,
            "leadVsPrice": d_lead,
            "cashConversion": c_cash,
            "participationGrowth": p_growth,
            "flowStability": s_flow,
            "churn": churn,
            "nrrAnalog": nrr,
            "grrAnalog": grr,
            "retentionQuality": q_ret,
            "expectedRecognition": r_hat,
            "gap": gap,
            "vwap20": vwap_20,
            "closeVsVwap20": close_vs_vwap,
            "rvol": rvol,
        },
        "scores": {
            "demandQuality": round(q_dr_100, 1),
            "signal": round(s_dr_100, 1),
            "instability": round(s_file_100, 1),
            "pAcceleration": round(p_acc, 4),
            "pAdverse": round(p_bad, 4),
        },
        "risk": {
            "abnormalEvents30d": n_events,
            "eventFrequency": f_amend,
            "correctionMagnitude": q_corr,
            "clusteredEvents": r_topic,
            "internalControl": None,  # not publicly observable for crypto
        },
        "filters": {
            "billingDistortion": billing_distortion,
            "lowQualityGrowth": low_quality_growth,
            "redFlagExcluded": red_flag_exclude,
            "belowVwap": price <= vwap_20,
            "aboveBlowoffCap": g_org > G_MAX,
        },
        "reasons": reasons,
        "exitTriggers": exit_reasons,
    }


def enrich_with_ticker(result: dict[str, Any], ticker: Optional[dict[str, Any]]) -> dict[str, Any]:
    """Attach market snapshot fields (24h change, USD volume, high/low) when available."""
    if ticker:
        result["market"] = {
            "lastPrice": ticker.get("lastPrice"),
            "priceChangePercent": ticker.get("priceChangePercent"),
            "quoteVolume": ticker.get("quoteVolume"),
            "highPrice": ticker.get("highPrice"),
            "lowPrice": ticker.get("lowPrice"),
        }
    return result


def apply_unlock_gate(
    result: dict[str, Any],
    unlock: Optional[dict[str, Any]],
    watch_pct: float = 1.0,
    avoid_pct: float = 3.0,
) -> bool:
    """Downgrade LONG when a material token-unlock is due within 30 days.

    Mirrors the red-flag filter: unlock overhang is a *supply shock* signal,
    not a demand signal — it can only veto a long, never promote anything.
    Purely additive; an absent/unavailable unlock feed is a no-op.

    Thresholds are percent of coin market cap (`percentOfMcap`) within 30 days:
      >= avoid_pct (default 3%) -> AVOID (hard exclusion, like instability)
      >= watch_pct (default 1%) -> WATCH (await transfer of the unlock window)
    Returns True when the verdict was changed.
    """
    if not unlock or not result.get("ok"):
        return False
    pct = unlock.get("percentOfMcap")
    try:
        pct_f = float(pct) if pct is not None else None
    except (TypeError, ValueError):
        pct_f = None
    if pct_f is None or result.get("verdict") != "LONG":
        result.setdefault("filters", {})["unlockOverhang"] = pct_f is not None and pct_f >= watch_pct
        return False

    result.setdefault("filters", {})["unlockOverhang"] = pct_f >= watch_pct

    target = None
    if pct_f >= avoid_pct:
        target = "AVOID"
    elif pct_f >= watch_pct:
        target = "WATCH"
    if target is None:
        return False

    result["verdict"] = target
    meta = VERDICT_META[target]
    result["verdictLabel"] = meta["label"]
    result["verdictTone"] = meta["tone"]
    when = unlock.get("date") or "within 30d"
    result.setdefault("reasons", []).append(
        f"Supply overhang: unlock of ~{pct_f:.1f}% of market cap due {when} (>= {watch_pct if target == 'WATCH' else avoid_pct}% gate) — long vetoed (unlock-overhang rule)."
    )
    return True


VERDICT_META = {
    "LONG": {"label": "Long candidate", "tone": "long", "rank": 0},
    "WATCH": {"label": "Watchlist", "tone": "watch", "rank": 1},
    "NEUTRAL": {"label": "Neutral", "tone": "neutral", "rank": 2},
    "NO_SIGNAL": {"label": "No signal — distortion", "tone": "flat", "rank": 3},
    "AVOID": {"label": "Avoid", "tone": "avoid", "rank": 4},
    "INSUFFICIENT_DATA": {"label": "Insufficient data", "tone": "flat", "rank": 5},
}

#!/usr/bin/env python3
"""
Meridian — p^(accel) calibration study.

Fits a small ridge-regularised logistic regression for the demand-acceleration
probability on *real* daily candles, replacing the hand-tuned sigmoid in
backend/screening.py with measured coefficients + thresholds.

Label: y = 1 when forward 30d ORGANIC (base-asset volume) growth > 0,
y2 = 1 when forward organic growth >= +15% (sensitivity check).

Features (identical to the production sigmoid inputs — no drift):
  g_pos = pos_sat(gOrganic), a_pos = pos_sat(acceleration),
  d_pos = pos_sat(leadVsPrice), gap_pos = max(0, sat(gap)),
  c_cash = cashConversion, s_flow = flowStability

Method: walk-forward anchors every `--step-days` days (using ONLY candles whose
timestamp <= anchor — no lookahead), standardised features, time-ordered
train/validation split (last 25% of anchors = validation), L2 grid-search on
validation log-loss. Reports base rate, Brier, AUC (rank-based), log-loss,
reliability deciles, precision@prediction>=0.6 vs base rate, and the CURRENT
heuristic's Brier/AUC for comparison. Nothing is auto-applied to production;
the script writes calibration_report.json which you review, then paste the
`suggested` block into backend/screening.py (or hand it to the agent).

Data sources (public, no key): data-api.binance.vision, falling back to
api.binance.us. Run on a machine with internet access to those hosts:

    python3 scripts/calibrate.py --lookback-days 1095 --out calibration_report.json

Needs the repo root on sys.path (script handles that) and backend deps
(requirements.txt) installed. Pure stdlib + httpx-free fetch via urllib.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.screening import compute_features, pos_sat, sat  # noqa: E402

WINDOW = 30
MIN_HISTORY = 120
VISION = "https://data-api.binance.vision/api/v3/klines"
BINANCE_US = "https://api.binance.us/api/v3/klines"

DEFAULT_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT", "AVAXUSDT",
    "DOGEUSDT", "DOTUSDT", "LINKUSDT", "MATICUSDT", "LTCUSDT", "UNIUSDT",
    "ATOMUSDT", "ETCUSDT", "XLMUSDT", "FILUSDT", "HBARUSDT", "ICPUSDT", "NEARUSDT",
    "APTUSDT", "ARBUSDT", "OPUSDT", "INJUSDT", "SUIUSDT", "SEIUSDT", "TIAUSDT",
    "JUPUSDT", "PEPEUSDT", "WIFUSDT", "BONKUSDT", "FLOKIUSDT", "TRXUSDT", "TONUSDT",
    "MKRUSDT", "AAVEUSDT", "LDOUSDT", "RUNEUSDT", "GALAUSDT", "SANDUSDT",
]


# ---------------------------------------------------------------------------
# Fetching (urllib; sync; retry once)
# ---------------------------------------------------------------------------

def _http_get_json(url: str, timeout: float = 20.0):
    for attempt in (1, 2):
        try:
            req = urllib.request.Request(url, headers={"user-agent": "meridian-calibrate/1.0", "accept": "application/json"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode())
        except Exception as exc:  # noqa: BLE001
            if attempt == 2:
                raise RuntimeError(f"GET {url} failed: {exc}") from exc
            time.sleep(1.5)
    return None


def fetch_daily_klines(symbol: str, start_ms: int, end_ms: int) -> list[dict]:
    rows: list[dict] = []
    cursor = start_ms
    while cursor < end_ms:
        for base in (VISION, BINANCE_US):
            url = f"{base}?symbol={symbol}&interval=1d&limit=1000&startTime={cursor}&endTime={end_ms}"
            try:
                raw = _http_get_json(url)
                break
            except RuntimeError:
                raw = None
        if not raw:
            break
        batch = []
        for k in raw:
            try:
                batch.append(
                    {
                        "date": int(k[0]),
                        "open": float(k[1]), "high": float(k[2]), "low": float(k[3]), "close": float(k[4]),
                        "volume": float(k[5]), "quoteVolume": float(k[7]), "trades": float(k[8]),
                        "takerBuyBase": float(k[9]), "takerBuyQuote": float(k[10]),
                    }
                )
            except (TypeError, IndexError, ValueError):
                continue
        if not batch:
            break
        rows.extend(batch)
        last_close_time = batch[-1]["date"] + 86_400_000
        if last_close_time <= cursor:
            break
        cursor = last_close_time
        if len(batch) < 1000:
            break
        time.sleep(0.25)
    # de-duplicate and sort
    seen = {}
    for r in rows:
        seen[r["date"]] = r
    return [seen[k] for k in sorted(seen)]


# ---------------------------------------------------------------------------
# Labels & features (mirrors backend/screening.py semantics; no lookahead)
# ---------------------------------------------------------------------------

def forward_organic_growth(candles: list[dict], anchor_idx: int, window: int = WINDOW) -> float | None:
    """Organic (base-asset) volume growth over the FORWARD window vs the trailing one."""
    if anchor_idx + 1 + window > len(candles):
        return None
    cur_end = anchor_idx + window  # last forward candle index (inclusive-ish slices below)
    fwd = sum(c["volume"] for c in candles[anchor_idx + 1 : cur_end + 1])
    prev_lo = max(0, anchor_idx - window + 1)
    prev = sum(c["volume"] for c in candles[prev_lo : anchor_idx + 1])
    if prev <= 1e-9:
        return None
    return (fwd - prev) / prev


def feature_vector(result: dict) -> list[float] | None:
    if not result.get("ok"):
        return None
    f = result["features"]
    g_pos = pos_sat(f["gOrganic"])
    a_pos = pos_sat(f["acceleration"])
    d_pos = pos_sat(f["leadVsPrice"])
    gap_pos = max(0.0, sat(f["gap"]))
    return [g_pos, a_pos, d_pos, gap_pos, f["cashConversion"], f["flowStability"]]


def current_heuristic_p(vec: list[float]) -> float:
    g_pos, a_pos, d_pos, gap_pos, c_cash, s_flow = vec
    z = (
        -0.75
        + 1.70 * g_pos
        + 1.30 * a_pos
        + 1.50 * d_pos
        + 1.20 * gap_pos
        + 1.20 * (c_cash - 0.5)
        + 0.90 * (s_flow - 0.7)
    )
    z = max(-30.0, min(30.0, z))
    return 1.0 / (1.0 + math.exp(-z))


# ---------------------------------------------------------------------------
# Logistic regression (Newton-Raphson, ridge on non-intercept weights)
# ---------------------------------------------------------------------------

def _solve(A: list[list[float]], b: list[float]) -> list[float]:
    n = len(A)
    M = [row[:] + [b[i]] for i, row in enumerate(A)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(M[r][col]))
        if abs(M[piv][col]) < 1e-12:
            raise ValueError("singular matrix")
        M[col], M[piv] = M[piv], M[col]
        inv = 1.0 / M[col][col]
        for r in range(n):
            if r != col and abs(M[r][col]) > 0.0:
                factor = M[r][col] * inv
                for c in range(n + 1):
                    M[r][c] -= factor * M[col][c]
        for c in range(n + 1):
            M[col][c] *= inv
    return [M[i][n] for i in range(n)]


def fit_logistic(
    X: list[list[float]],
    y: list[int],
    l2: float = 1.0,
    max_iter: int = 200,
    tol: float = 1e-8,
) -> list[float]:
    n, p = len(X), len(X[0])
    w = [0.0] * p
    for _ in range(max_iter):
        z = [sum(w[j] * x[j] for j in range(p)) for x in X]
        a = [1.0 / (1.0 + math.exp(-max(-30.0, min(30.0, v)))) for v in z]
        grad = [0.0] * p
        H = [[0.0] * p for _ in range(p)]
        for i in range(n):
            r = a[i] - y[i]
            for j in range(p):
                grad[j] += r * X[i][j]
                pw = a[i] * (1.0 - a[i])
                H[j][j] += pw * X[i][j] * X[i][j]
                for k in range(j + 1, p):
                    H[j][k] += pw * X[i][j] * X[i][k]
        for j in range(1, p):  # intercept unpenalised
            grad[j] += l2 * w[j]
        for j in range(1, p):
            H[j][j] += l2
        for j in range(p):
            for k in range(j + 1, p):
                H[k][j] = H[j][k]
        step = _solve(H, grad)
        new_w = [w[j] - step[j] for j in range(p)]
        delta = max(abs(new_w[j] - w[j]) for j in range(p))
        w = new_w
        if delta < tol:
            break
    return w


def predict_proba(X: list[list[float]], w: list[float]) -> list[float]:
    out = []
    for x in X:
        z = sum(w[j] * x[j] for j in range(len(w)))
        z = max(-30.0, min(30.0, z))
        out.append(1.0 / (1.0 + math.exp(-z)))
    return out


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def brier(p: list[float], y: list[int]) -> float:
    return sum((pi - yi) ** 2 for pi, yi in zip(p, y)) / max(1, len(y))


def logloss(p: list[float], y: list[int]) -> float:
    eps = 1e-9
    p = [min(1 - eps, max(eps, pi)) for pi in p]
    return -sum(yi * math.log(pi) + (1 - yi) * math.log(1 - pi) for pi, yi in zip(p, y)) / max(1, len(y))


def auc(scores: list[float], y: list[int]) -> float:
    n_pos = sum(y)
    n_neg = len(y) - n_pos
    if n_pos == 0 or n_neg == 0:
        return float("nan")
    order = sorted(range(len(scores)), key=lambda i: scores[i])
    ranks = [0.0] * len(scores)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and scores[order[j + 1]] == scores[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    sum_pos = sum(ranks[i] for i in range(len(y)) if y[i] == 1)
    return (sum_pos - n_pos * (n_pos + 1) / 2.0) / (n_pos * n_neg)


def reliability(p: list[float], y: list[int], bins: int = 10) -> list[dict]:
    out = []
    for b in range(bins):
        lo, hi = b / bins, (b + 1) / bins
        sel = [i for i, pi in enumerate(p) if (lo <= pi < hi or (b == bins - 1 and pi == 1.0))]
        if not sel:
            out.append({"bin": b, "count": 0, "predMean": None, "obsFreq": None})
            continue
        out.append(
            {
                "bin": b,
                "count": len(sel),
                "predMean": round(sum(p[i] for i in sel) / len(sel), 4),
                "obsFreq": round(sum(y[i] for i in sel) / len(sel), 4),
            }
        )
    return out


# ---------------------------------------------------------------------------
# Dataset assembly & run
# ---------------------------------------------------------------------------

def collect_dataset(klines_by_symbol: dict[str, list[dict]], step_days: int, window: int = WINDOW):
    rows = []
    for symbol, candles in klines_by_symbol.items():
        if len(candles) < MIN_HISTORY + window:
            continue
        for anchor in range(MIN_HISTORY - 1, len(candles) - window - 1, step_days):
            fwd = forward_organic_growth(candles, anchor, window)
            if fwd is None:
                continue
            result = compute_features(candles[max(0, anchor - 119) : anchor + 1])
            vec = feature_vector(result)
            if vec is None:
                continue
            closes = [c["close"] for c in candles[max(0, anchor - 119) : anchor + 1]]
            rows.append(
                {
                    "symbol": symbol,
                    "anchorMs": candles[anchor]["date"],
                    "x": vec,
                    "y": 1 if fwd > 0.0 else 0,
                    "y_strong": 1 if fwd >= 0.15 else 0,
                    "fwdOrganic": fwd,
                    "signal": result["scores"]["signal"],
                    "price": closes[-1],
                    "heuristicP": current_heuristic_p(vec),
                    "verdictLong": result["verdict"] == "LONG",
                }
            )
    rows.sort(key=lambda r: r["anchorMs"])
    return rows


def standardize(X):
    p = len(X[0])
    mean = [sum(x[j] for x in X) / len(X) for j in range(p)]
    std = []
    for j in range(p):
        var = sum((x[j] - mean[j]) ** 2 for x in X) / len(X)
        std.append(math.sqrt(var) if var > 1e-12 else 1.0)
    return [[(x[j] - mean[j]) / std[j] for j in range(p)] for x in X], mean, std


def augment_intercept(X):
    return [[1.0] + row for row in X]


def time_split(n: int, train_frac: float = 0.75) -> int:
    return max(1, min(n - 1, int(n * train_frac)))


def run(
    klines_by_symbol: dict[str, list[dict]],
    step_days: int = 7,
    val_frac: float = 0.25,
    min_samples: int = 50,
    label: str = "pos",
) -> dict:
    rows = collect_dataset(klines_by_symbol, step_days)
    if label == "strong":
        for r in rows:
            r["y"] = r["y_strong"]
    if len(rows) < min_samples:
        raise RuntimeError(f"not enough labelled samples ({len(rows)}); need >= {min_samples}")

    X = [r["x"] for r in rows]
    y = [r["y"] for r in rows]
    Xs, mean, std = standardize(X)
    Xa = augment_intercept(Xs)

    split = time_split(len(rows), 1 - val_frac)
    Xtr, ytr = Xa[:split], y[:split]
    Xva, yva = Xa[split:], y[split:]

    best_l2, best_ll, best_w = None, float("inf"), None
    for l2 in (0.01, 0.1, 1.0, 10.0):
        w = fit_logistic(Xtr, ytr, l2=l2)
        ll = logloss(predict_proba(Xva, w), yva)
        if ll < best_ll:
            best_l2, best_ll, best_w = l2, ll, w
    w = best_w
    p_va = predict_proba(Xva, w)
    p_tr = predict_proba(Xtr, w)

    base_rate = sum(yva) / len(yva)
    heur_va = [r["heuristicP"] for r in rows[split:]]
    heur_tr = [r["heuristicP"] for r in rows[:split]]

    def precision_at(ps, ys, thr):
        sel = [i for i, p in enumerate(ps) if p >= thr]
        if not sel:
            return {"count": 0, "precision": None, "recall": None}
        tp = sum(ys[i] for i in sel)
        return {
            "count": len(sel),
            "precision": round(tp / len(sel), 4),
            "recall": round(tp / max(1, sum(ys)), 4),
        }

    report = {
        "samples": len(rows),
        "label": label,
        "perSymbolSamples": dict(sorted(__import__("collections").Counter(r["symbol"] for r in rows).items())),
        "anchorFirstMs": min(r["anchorMs"] for r in rows),
        "anchorLastMs": max(r["anchorMs"] for r in rows),
        "trainSamples": split,
        "validationSamples": len(rows) - split,
        "symbols": sorted(klines_by_symbol),
        "baseRateValidation": round(base_rate, 4),
        "l2": best_l2,
        "featureStd": {k: round(v, 6) for k, v in zip(["g_pos", "a_pos", "d_pos", "gap_pos", "c_cash", "s_flow"], std)},
        "featureMean": {k: round(v, 6) for k, v in zip(["g_pos", "a_pos", "d_pos", "gap_pos", "c_cash", "s_flow"], mean)},
        "coefficients": {k: round(v, 6) for k, v in zip(["intercept", "g_pos", "a_pos", "d_pos", "gap_pos", "c_cash", "s_flow"], w)},
        "validation": {
            "brier": round(brier(p_va, yva), 4),
            "auc": round(auc(p_va, yva), 4),
            "logloss": round(logloss(p_va, yva), 4),
            "reliability": reliability(p_va, yva),
            "p_ge_0.6": precision_at(p_va, yva, 0.6),
        },
        "train": {
            "brier": round(brier(p_tr, ytr), 4),
            "auc": round(auc(p_tr, ytr), 4),
        },
        "currentHeuristic": {
            "validationBrier": round(brier(heur_va, yva), 4),
            "validationAuc": round(auc(heur_va, yva), 4),
            "predictions_ge_0.6": precision_at(heur_va, yva, 0.6),
        },
        "note": (
            "Coefficients are on standardised features. To port into backend/screening.py, "
            "either standardise with featureStd/featureMean before the sigmoid, or refit on raw "
            "inputs. Do NOT hard-paste without reviewing the reliability table and AUC/Brier vs "
            "currentHeuristic."
        ),
    }
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="Meridian p^(accel) calibration study")
    ap.add_argument("--symbols", default=",".join(DEFAULT_SYMBOLS))
    ap.add_argument("--lookback-days", type=int, default=1095)
    ap.add_argument("--step-days", type=int, default=7)
    ap.add_argument("--out", default="calibration_report.json")
    args = ap.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    end_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    start_ms = int((datetime.now(timezone.utc) - timedelta(days=args.lookback_days)).timestamp() * 1000)

    klines_by_symbol = {}
    for i, sym in enumerate(symbols):
        try:
            candles = fetch_daily_klines(sym, start_ms, end_ms)
        except RuntimeError as exc:
            print(f"[skip] {sym}: {exc}", file=sys.stderr)
            continue
        klines_by_symbol[sym] = candles
        print(f"[{i + 1}/{len(symbols)}] {sym}: {len(candles)} daily candles")

    report = run(klines_by_symbol, step_days=args.step_days)
    out_path = Path(args.out)
    out_path.write_text(json.dumps(report, indent=2))

    v, h = report["validation"], report["currentHeuristic"]
    print("\n=== Calibration report ===")
    print(f"samples={report['samples']} train={report['trainSamples']} val={report['validationSamples']}")
    print(f"base rate (val) = {report['baseRateValidation']:.4f}")
    print(f"CALIBRATED : brier={v['brier']} auc={v['auc']} logloss={v['logloss']} p>=0.6: {v['p_ge_0.6']}")
    print(f"HEURISTIC  : brier={h['validationBrier']} auc={h['validationAuc']} p>=0.6: {h['predictions_ge_0.6']}")
    print(f"wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
